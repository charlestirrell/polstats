"""Loader for MIT Election Data + Science Lab (MEDSL) U.S. Senate / House returns (.tab files).

Usage:  python -P -m polstats.loaders.medsl raw/medsl-senate/1976-2024-senate-state.tab

Source: https://doi.org/10.7910/DVN/PEJ5QU (Senate), https://doi.org/10.7910/DVN/IG0UN2 (House); CC0 1.0.
(Harvard Dataverse requires a download "guestbook" form for the House file; the Senate file does not.)

What it does
  * One Election per (year, kind); one Contest per race; one Candidacy + ContestResult per candidate.
  * Candidates listed on several party lines (fusion tickets) are summed into one candidacy.
  * The source has NO winner column, so `outcome` is DERIVED (ContestResult.outcomeDerived = true):
      - general: plurality leader wins; if a runoff exists (or the pre-runoff 'pre' stage shows no majority)
        the top two are 'advanced-to-runoff'; runoff: leader wins; primaries get no outcome.
  * Winners are linked to a Person only when state + term-start window + family name all agree.
  * Idempotent (upserts by natural key; unchanged resources get no new version).
Sanity check reported: candidate votes must sum to the contest's `totalvotes`.
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

from .. import codesystems as cs
from ..datatypes import Identifier, Reference
from ..resources import (Candidacy, Contest, ContestResult, Election, Organization, Provenance, Resource)
from ..store import Store, dsn
from .congress_legislators import Loader as GeoLoader
from .congress_legislators import office_code

SOURCE = "MIT Election Data and Science Lab"
SOURCE_URL = "https://electionlab.mit.edu/data"
LICENSE = "CC0-1.0"
SYS_ELECTION = "urn:polstats:election"
SYS_CONTEST = "urn:polstats:contest"
SYS_CANDIDACY = "urn:polstats:candidacy"
SYS_RESULT = "urn:polstats:contest-result"
SYS_PROV = "urn:polstats:provenance"

# Catch-all rows that are not people: write-ins, scattering, blanks, void/spoiled/under/over votes, ranked-choice
# "exhausted ballots", "other", and party names used in place of a candidate name.
BUCKET_RX = re.compile(r"^(WRITE ?IN|OTHERS?|ALL OTHERS|SCATTER(ING)?|BLANKS?|VOID|SPOILED|UNDER ?VOTES?|OVER ?VOTES?|"
                       r"NONE OF THE ABOVE|NONE OF THESE|MISCELLANEOUS|EXHAUSTED( BALLOTS?)?|UNNAMED)( |$)")
BUCKET_EXACT = {"INDEPENDENT", "WORKING FAMILIES", "MODERATE", "NO NAME"}


# ---------- pure helpers (unit-tested) ----------
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()


# Rows that are ballots but not votes for anyone; some states' stated totals exclude them.
NONVOTE_RX = re.compile(r"^(BLANKS?|VOID|SPOILED|UNDER ?VOTES?|OVER ?VOTES?)( |$)")


def is_nonvote_name(name: str) -> bool:
    return bool(NONVOTE_RX.match(norm(name)))


def is_bucket_name(name: str) -> bool:
    n = norm(name)
    return not n or n in BUCKET_EXACT or bool(BUCKET_RX.match(n))


def party_code(detailed: str, simplified: str = "") -> str:
    d = (detailed or "").upper()
    if "DEMOCRAT" in d or d == "DFL" or "DEMOCRATIC-FARMER" in d:
        return "D"
    if "REPUBLICAN" in d:
        return "R"
    if d.startswith("INDEPENDENT"):
        return "I"
    if "LIBERTARIAN" in d:
        return "L"
    if d == "GREEN" or d.startswith("GREEN "):
        return "G"
    s = (simplified or "").upper()
    return {"DEMOCRAT": "D", "REPUBLICAN": "R", "LIBERTARIAN": "L"}.get(s, "other")


def stage_norm(stage: str) -> str:
    s = (stage or "").strip().lower()
    if "runoff" in s:
        return "runoff"
    if s in ("pri", "primary"):
        return "pri"
    return "gen"  # gen, pre (pre-runoff general)


def effective_stage(stage: str, has_pre_round: bool) -> str:
    """In Georgia's data the first round is stage 'pre' and the runoff that follows is labelled 'gen'."""
    return "runoff" if has_pre_round and stage.strip().lower() == "gen" else stage


def kind_of(stage: str, special: bool) -> str:
    st = stage_norm(stage)
    return "runoff" if st == "runoff" else "primary" if st == "pri" else "special" if special else "general"


def regular_election_day(year: int) -> date | None:
    """Tuesday after the first Monday in November (even years only)."""
    if year % 2:
        return None
    d = date(year, 11, 1)
    first_monday = d + timedelta(days=(0 - d.weekday()) % 7)
    return first_monday + timedelta(days=1)


def district_number(raw: str) -> int | None:
    """None for statewide contests, 0 for at-large, else the district number."""
    raw = (raw or "").strip().lower()
    if raw in ("", "statewide"):
        return None
    try:
        return int(float(raw))
    except ValueError:
        raise ValueError(f"unrecognised district {raw!r}")


def district_label(state: str, raw: str) -> str | None:
    n = district_number(raw)
    return None if n is None else f"{state}-{'AL' if n == 0 else n}"


@dataclass
class Cand:
    name: str
    write_in: bool
    votes: int = 0
    lines: list[tuple[str, int, str]] = field(default_factory=list)  # (party label, votes, simplified)
    outcome: str | None = None

    def _top(self) -> tuple[str, int, str]:
        """The candidate's primary party line: the best-supported major-party (D/R) line if any (fusion tickets
        list the same person under several parties), otherwise the line with the most votes."""
        if not self.lines:
            return ("", 0, "")
        major = [l for l in self.lines if party_code(l[0], l[2]) in ("D", "R")]
        return max(major or self.lines, key=lambda l: l[1])

    @property
    def label(self) -> str:
        label = self._top()[0]
        return "" if label.upper() == "NA" else label  # "NA" = no party recorded (catch-all rows)

    @property
    def party(self) -> str:
        return party_code(self._top()[0], self._top()[2])

    @property
    def is_bucket(self) -> bool:
        return is_bucket_name(self.name)


def derive_outcomes(stage: str, cands: list[Cand], total: int | None, runoff_names: set[str] | None) -> None:
    """Mutates cands[].outcome. runoff_names: normalised names appearing in a related runoff (or None)."""
    st = stage_norm(stage)
    if st == "pri":
        return
    ranked = sorted([c for c in cands if not c.is_bucket], key=lambda c: -c.votes)
    for c in cands:
        c.outcome = "lost"
    if not ranked:
        return
    if st == "runoff":
        ranked[0].outcome = "won"
        return
    no_majority = total and ranked[0].votes * 2 <= total
    if runoff_names:
        for c in ranked:
            if norm(c.name) in runoff_names:
                c.outcome = "advanced-to-runoff"
    elif stage.strip().lower() == "pre" and no_majority:
        for c in ranked[:2]:
            c.outcome = "advanced-to-runoff"
    else:
        ranked[0].outcome = "won"


def family_regex(family: str) -> re.Pattern | None:
    f = norm(family)
    return re.compile(rf"(?<![A-Z0-9]){re.escape(f)}(?![A-Z0-9])") if f else None


# ---------- loader ----------
class MedslLoader(GeoLoader):
    def __init__(self, store: Store, source_version: str, recorded: str):
        super().__init__(store, source_version, recorded)
        self.metrics = defaultdict(int)
        self._index: dict | None = None
        self._seen: set[str] = set()
        self.problems: list[str] = []  # source inconsistencies worth a human look

    # reference data
    def party_ref(self, code: str) -> Reference:
        display = cs.CODES[cs.PARTY][code]
        return self._cached(("party", code), lambda: self._put(
            Organization(type=cs.concept(cs.ORG_TYPE, "party"), name=display), cs.PARTY, code))

    def election_ref(self, year: int, kind: str) -> Reference:
        return self._cached(("election", f"{year}:{kind}"), lambda: self._put(
            Election(name=f"{year} {kind} election", year=year, kind=cs.concept(cs.ELECTION_TYPE, kind),
                     date=regular_election_day(year) if kind == "general" else None),
            SYS_ELECTION, f"{year}:{kind}"))

    # person linking
    def term_index(self) -> dict[str, list]:
        """state code -> [(person ref, [family regexes], chamber, district label|None, start, end)]"""
        if self._index is None:
            c = self.store.conn
            usps = {r[0]: next((i["value"] for i in r[1] if i["system"] == cs.ID_USPS), None)
                    for r in c.execute("SELECT id::text, resource -> 'identifier' FROM location")}
            dist = {r[0]: r[1] for r in c.execute("SELECT id::text, resource ->> 'name' FROM location")}
            fam = {}
            for pid, names in c.execute("SELECT id::text, resource -> 'name' FROM person"):
                fam[pid] = [rx for n in names if (rx := family_regex(n.get("family", "")))]
            idx: dict[str, list] = defaultdict(list)
            for res in c.execute("SELECT resource FROM office_term"):
                t = res[0]
                pid = t["person"]["reference"].split("/")[1]
                st = usps.get(t["state"]["reference"].split("/")[1])
                if st and pid in fam:
                    d = dist.get(t["district"]["reference"].split("/")[1]) if t.get("district") else None
                    idx[st].append((t["person"]["reference"], fam[pid], t["chamber"]["coding"][0]["code"], d,
                                    t["period"].get("start"), t["period"].get("end")))
            self._index = idx
        return self._index

    def find_person(self, cand_name: str, state: str, office: str, district: str | None, year: int) -> str | None:
        lo, hi = date(year, 1, 1).isoformat(), date(year + 1, 3, 1).isoformat()
        chamber = "senate" if office == "senator" else "house"
        n = norm(cand_name)
        hits = {ref for ref, rxs, ch, d, start, _ in self.term_index().get(state, [])
                if ch == chamber and start and lo <= start <= hi and (district is None or d == district)
                and any(rx.search(n) for rx in rxs)}
        return hits.pop() if len(hits) == 1 else None

    # one contest
    def load_contest(self, key: tuple, rows: list[dict], office: str, runoff_names: set[str] | None,
                     stage: str | None = None) -> None:
        year, state, special, raw_stage, district_raw = key
        stage = raw_stage if stage is None else stage  # effective stage (see effective_stage)
        dnum = district_number(district_raw)
        dlabel = district_label(state, district_raw)
        kind = kind_of(stage, special)
        use = [r for r in rows if r["mode"] == "total"] or rows
        by: dict[tuple, Cand] = {}
        for r in use:
            name = r["candidate"].strip()
            wi = r["writein"] == "true"
            c = by.setdefault((norm(name), wi), Cand(name or ("(write-in, aggregated)" if wi else "(unnamed)"), wi))
            v = int(float(r["candidatevotes"]))
            c.votes += v
            c.lines.append(((r.get("party_detailed") or r.get("party") or "").strip(), v, r.get("party_simplified", "")))
        cands = list(by.values())
        named = [c for c in cands if not c.is_bucket]
        totals = [int(float(r["totalvotes"])) for r in use if r["totalvotes"]]
        total = max(totals) if totals and max(totals) > 0 else None  # sources use 0 / -1 for "unknown"
        # Uncontested: one named candidate and only a placeholder vote (1, or -1 in Florida) -> keep no votes.
        uncontested = len(named) == 1 and named[0].votes <= 1
        nonvotes = [c for c in cands if is_nonvote_name(c.name)]
        sum_all = sum(c.votes for c in cands)
        # Some states' totals exclude blank/void ballots: then they are not votes and get no vote share.
        total_excludes_nonvotes = bool(nonvotes) and total is not None and sum_all != total \
            and sum_all - sum(c.votes for c in nonvotes) == total
        if uncontested:
            total = None
        elif total is not None and sum_all != total and not total_excludes_nonvotes:
            self.metrics["vote_sum_mismatch"] += 1
            self.problems.append(f"{year} {state} {dlabel or 'statewide'} {kind}: candidates sum "
                                 f"{sum_all:,}, source total {total:,}")
        derive_outcomes(stage, cands, total, runoff_names)
        if uncontested:
            self.metrics["uncontested"] += 1

        ckey = f"{year}:{kind}{':special' if special and kind == 'runoff' else ''}:{state}:{office}:{dlabel or 'statewide'}"
        if ckey in self._seen:  # never silently overwrite: two source contests must not share a natural key
            raise ValueError(f"duplicate contest key {ckey!r} (source stages {raw_stage!r}); the key scheme needs refining")
        self._seen.add(ckey)
        contest = Contest(
            election=self.election_ref(year, kind), office=cs.concept(cs.OFFICE, office),
            state=self.state(state),
            district=None if dnum is None else self.district(state, dnum),
            special=special, uncontested=uncontested, totalVotes=total,
            reporting="unofficial" if any(r["unofficial"] == "true" for r in rows) else "official")
        cref = self._put(contest, SYS_CONTEST, ckey)
        targets = [cref]
        for c in cands:
            nkey = f"{ckey}:{norm(c.name) or 'unnamed'}:{'w' if c.write_in else 'n'}"
            code = c.party
            person = None
            if c.outcome == "won" and not c.is_bucket:
                person = self.find_person(c.name, state, office, dlabel, year)
                self.metrics["winners_linked" if person else "winners_unlinked"] += 1
            cand_res = Candidacy(contest=cref, name=c.name, party=self.party_ref(code), partyLabel=c.label or None,
                                 writeIn=c.write_in, person=Reference(reference=person) if person else None)
            candref = self._put(cand_res, SYS_CANDIDACY, nkey)
            res = ContestResult(
                contest=cref, candidacy=candref, year=year, office=cs.concept(cs.OFFICE, office),
                state=contest.state, partyCode=code, votes=None if uncontested else c.votes,
                voteShare=None if uncontested or not total or (total_excludes_nonvotes and c in nonvotes)
                else round(c.votes / total, 6),
                outcome=cs.concept(cs.CONTEST_OUTCOME, c.outcome) if c.outcome else None)
            targets += [candref, self._put(res, SYS_RESULT, nkey)]
        self._put(Provenance(target=targets, source=SOURCE, sourceUrl=SOURCE_URL,
                             sourceVersion=self.source_version, recorded=self.recorded, license=LICENSE),
                  SYS_PROV, f"{SOURCE}:{ckey}")


def _clean(r: dict) -> dict:
    out = {k: (v or "").strip() for k, v in r.items() if k}
    out["special"] = out.get("special", "").lower() == "true"
    out["writein"] = out.get("writein", "").lower()
    out["unofficial"] = out.get("unofficial", "").lower()
    out["runoff"] = out.get("runoff", "").lower()
    out["mode"] = out.get("mode", "total").lower() or "total"
    out["state_po"] = out["state_po"].upper()
    return out


def load_file(path: Path, conn: psycopg.Connection) -> tuple[dict[str, int], dict[str, object]]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig").splitlines()
    # Dataverse's converted .tab exports are tab-separated; the original uploads (what polstats.fetch
    # downloads) may be comma- or tab-separated. Sniff from the header line.
    delimiter = "\t" if text and "\t" in text[0] else ","
    rows = [_clean(r) for r in csv.DictReader(text, delimiter=delimiter)]
    for r in rows:  # House files flag runoff elections in a separate column rather than in `stage`
        if r["runoff"] == "true":
            r["stage"] = "runoff"
    store = Store(conn)
    store.init_schema()
    recorded = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    loader = MedslLoader(store, hashlib.sha256(raw).hexdigest()[:12], recorded)

    contests: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        contests[(int(r["year"]), r["state_po"], r["special"], r["stage"], r.get("district", "statewide"))].append(r)

    # Georgia-style: a 'pre' stage is the first round, and the 'gen' stage that follows it is the runoff.
    pre_keys = {(y, s, sp, d) for (y, s, sp, st, d) in contests if st.strip().lower() == "pre"}
    eff = {k: effective_stage(k[3], (k[0], k[1], k[2], k[4]) in pre_keys) for k in contests}

    # runoffs by (state, special, district) -> [(year, names)] so generals can see who advanced
    runoffs: dict[tuple, list[tuple[int, set[str]]]] = defaultdict(list)
    for key, rs in contests.items():
        year, state, special, _, dist = key
        if stage_norm(eff[key]) == "runoff":
            runoffs[(state, special, dist)].append((year, {norm(r["candidate"]) for r in rs if r["candidate"]}))

    for key in sorted(contests, key=lambda k: (k[0], k[1], k[2], k[3])):
        year, state, special, stage, dist = key
        office = ("senator" if "SENATE" in contests[key][0].get("office", "").upper()
                  else office_code("rep", state))  # representative, or delegate for DC
        related = None
        if stage_norm(eff[key]) == "gen":
            names = set()
            for ry, rn in runoffs.get((state, special, dist), []):
                if ry in (year, year + 1):
                    names |= rn
            related = names or None
        loader.load_contest(key, contests[key], office, related, stage=eff[key])
    return loader.counts, {**loader.metrics, "mismatch_examples": loader.problems[:15]}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with psycopg.connect(dsn()) as conn:
        counts, metrics = load_file(Path(argv[1]), conn)
    print("upserted:", counts)
    examples = metrics.pop("mismatch_examples", [])
    print("metrics:", metrics)
    if examples:
        print("source inconsistencies (first 15; candidate votes vs stated total):")
        for e in examples:
            print("  -", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
