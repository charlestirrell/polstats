"""Loader for unitedstates/congress-legislators (legislators-current/historical .json).

Usage:  python -I -m polstats.loaders.congress_legislators raw/congress-legislators/legislators-current.json

Idempotent: every resource is upserted by a natural-key identifier, and unchanged
resources do not get a new version.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

from .. import codesystems as cs
from ..datatypes import Extension, HumanName, Identifier, Period, Reference
from ..resources import (Location, OfficeTerm, Organization, PartyAffiliation, Person,
                         Provenance, Resource)
from ..store import Store, dsn

SOURCE = "unitedstates/congress-legislators"
SOURCE_URL = "https://github.com/unitedstates/congress-legislators"
LICENSE = "CC0-1.0 (as reported by the project; verify)"

SYS_NATION = "urn:polstats:nation"
SYS_DISTRICT = "urn:polstats:district"
SYS_TERM = "urn:polstats:office-term"
SYS_AFFIL = "urn:polstats:party-affiliation"
SYS_PROV = "urn:polstats:provenance"
SYS_ID_OTHER = "urn:congress-legislators:id:"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
    # historical
    "DK": "Dakota Territory", "OL": "Orleans Territory", "PI": "Philippine Islands",
}

PARTY_CODES = {"Democrat": "D", "Republican": "R", "Independent": "I",
               "Libertarian": "L", "Green": "G"}
GENDERS = {"F": "female", "M": "male"}
SYS_ID = {
    "bioguide": cs.ID_BIOGUIDE, "fec": cs.ID_FEC, "icpsr": cs.ID_ICPSR,
    "govtrack": cs.ID_GOVTRACK, "opensecrets": cs.ID_OPENSECRETS, "wikidata": cs.ID_WIKIDATA,
}


def party_code(name: str) -> str:
    return PARTY_CODES.get(name) or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def office_code(term_type: str, state: str) -> str:
    if term_type == "sen":
        return "senator"
    if state == "PR":
        return "resident-commissioner"
    if state in {"DC", "GU", "VI", "AS", "MP"}:
        return "delegate"
    return "representative"


def d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def coalesce(spans: list[tuple[str, str, date | None, date | None]]):
    """Merge adjacent/overlapping spans with the same party code."""
    out: list[list] = []
    for code, name, start, end in sorted(spans, key=lambda s: s[2] or date.min):
        if out and out[-1][0] == code and start and out[-1][3] and start <= out[-1][3] + timedelta(days=1):
            out[-1][3] = max(out[-1][3], end) if end else None
        else:
            out.append([code, name, start, end])
    return out


class Loader:
    def __init__(self, store: Store, source_version: str, recorded: str):
        self.store, self.source_version, self.recorded = store, source_version, recorded
        self._refs: dict[tuple[str, str], Reference] = {}
        self.counts: dict[str, int] = {}

    def _put(self, res: Resource, system: str, value: str) -> Reference:
        res.identifier = [i for i in res.identifier if not (i.system == system and i.value == value)]
        res.identifier.append(Identifier(system=system, value=value))
        saved = self.store.upsert(res, system, value)
        self.counts[res.resourceType] = self.counts.get(res.resourceType, 0) + 1
        return Reference(reference=f"{saved.resourceType}/{saved.id}",
                         display=getattr(saved, "name", None) if isinstance(getattr(saved, "name", None), str) else None)

    def _cached(self, key: tuple[str, str], make) -> Reference:
        if key not in self._refs:
            self._refs[key] = make()
        return self._refs[key]

    # --- reference data -------------------------------------------------
    def nation(self) -> Reference:
        return self._cached(("nation", "US"), lambda: self._put(
            Location(type=cs.concept(cs.LOCATION_TYPE, "nation"), name="United States"), SYS_NATION, "US"))

    def state(self, code: str) -> Reference:
        return self._cached(("state", code), lambda: self._put(
            Location(type=cs.concept(cs.LOCATION_TYPE, "state"), name=STATES.get(code, code),
                     partOf=self.nation()), cs.ID_USPS, code))

    def district(self, state: str, number: int) -> Reference:
        label = f"{state}-{'AL' if number == 0 else number}"
        return self._cached(("district", label), lambda: self._put(
            Location(type=cs.concept(cs.LOCATION_TYPE, "district"), name=label,
                     partOf=self.state(state)), SYS_DISTRICT, label))

    def party(self, name: str) -> Reference:
        code = party_code(name)
        display = cs.CODES[cs.PARTY].get(code, name)
        return self._cached(("party", code), lambda: self._put(
            Organization(type=cs.concept(cs.ORG_TYPE, "party"), name=display), cs.PARTY, code))

    # --- one legislator -------------------------------------------------
    def load_record(self, rec: dict) -> None:
        ids, nm, bio = rec["id"], rec["name"], rec.get("bio", {})
        bioguide = ids["bioguide"]

        identifiers = []
        for key, val in ids.items():
            for v in (val if isinstance(val, list) else [val]):
                identifiers.append(Identifier(system=SYS_ID.get(key, SYS_ID_OTHER + key), value=str(v)))

        person = Person(
            name=[HumanName(use="official", family=nm.get("last"),
                            given=[x for x in (nm.get("first"), nm.get("middle")) if x],
                            suffix=[nm["suffix"]] if nm.get("suffix") else [],
                            text=nm.get("official_full"))]
            + ([HumanName(use="nickname", given=[nm["nickname"]])] if nm.get("nickname") else []),
            gender=GENDERS.get(bio.get("gender")),
            birthDate=d(bio.get("birthday")),
            identifier=identifiers,
        )
        pref = self._put(person, cs.ID_BIOGUIDE, bioguide)
        targets = [pref]

        spans = []
        for t in rec["terms"]:
            start, end, state = d(t["start"]), d(t["end"]), t["state"]
            is_sen = t["type"] == "sen"
            ext = [Extension(url=f"urn:polstats:{k}", value=t[k]) for k in ("caucus", "state_rank") if k in t]
            term = OfficeTerm(
                person=pref,
                office=cs.concept(cs.OFFICE, office_code(t["type"], state)),
                chamber=cs.concept(cs.CHAMBER, "senate" if is_sen else "house"),
                state=self.state(state),
                district=None if is_sen else self.district(state, t.get("district", 0)),
                senateClass=t.get("class") if is_sen else None,
                period=Period(start=start, end=end),
                extension=ext,
            )
            targets.append(self._put(term, SYS_TERM, f"{bioguide}:{t['type']}:{t['start']}"))

            if t.get("party_affiliations"):
                for pa in t["party_affiliations"]:
                    spans.append((party_code(pa["party"]), pa["party"], d(pa.get("start")) or start, d(pa.get("end")) or end))
            elif t.get("party"):
                spans.append((party_code(t["party"]), t["party"], start, end))

        for code, name, start, end in coalesce(spans):
            aff = PartyAffiliation(person=pref, party=self.party(name), period=Period(start=start, end=end))
            targets.append(self._put(aff, SYS_AFFIL, f"{bioguide}:{code}:{start}"))

        prov = Provenance(target=targets, source=SOURCE, sourceUrl=SOURCE_URL,
                          sourceVersion=self.source_version, recorded=self.recorded, license=LICENSE)
        self._put(prov, SYS_PROV, f"{SOURCE}:{bioguide}")


def load_file(path: Path, conn: psycopg.Connection) -> dict[str, int]:
    raw = path.read_bytes()
    records = json.loads(raw)
    store = Store(conn)
    store.init_schema()
    recorded = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    loader = Loader(store, hashlib.sha256(raw).hexdigest()[:12], recorded)
    for rec in records:
        loader.load_record(rec)
    return loader.counts


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with psycopg.connect(dsn()) as conn:
        counts = load_file(Path(argv[1]), conn)
    print("upserted:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
