"""OpenAPI documentation for the REST API (served at /docs and /redoc, machine-readable at /openapi.json).

Everything user-facing about the API's documentation lives here, apart from the routes themselves:
  * the overview and per-resource descriptions,
  * every search parameter (what it matches, allowed values),
  * a worked example for every resource type,
  * typed Bundle models so each endpoint advertises exactly what it returns.

tests/test_openapi.py keeps this in sync with the code: every search parameter in search.py must be documented here,
every example must validate against its model, and every resource type must have a tag."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from .. import codesystems as cs
from ..resources import REGISTRY
from .search import INCLUDES, params_for

SERVICE_TAG = "Service"
# Order shown in /docs: the service, then people and their roles, then reference data, then elections, then provenance.
TAG_ORDER = [SERVICE_TAG, "Person", "OfficeTerm", "PartyAffiliation", "Organization", "Location",
             "Election", "Contest", "Candidacy", "ContestResult", "Provenance"]
README = "https://github.com/charlestirrell/polstats"

# ---------------------------------------------------------------------------------------------------------------
# Models used only to document responses
# ---------------------------------------------------------------------------------------------------------------


class OperationIssue(BaseModel):
    severity: str = Field(description="`error` for every failure this API returns.")
    code: str = Field(description="Machine-readable kind: `invalid`, `not-found`, `forbidden` or `conflict`.")
    diagnostics: str = Field(description="Human-readable explanation, e.g. which search parameter was not recognised.")


class OperationOutcome(BaseModel):
    """Every error is an OperationOutcome document."""
    resourceType: Literal["OperationOutcome"] = "OperationOutcome"
    issue: list[OperationIssue]


class BundleLink(BaseModel):
    relation: str = Field(description="`self`, or `next` when more results remain.")
    url: str = Field(description="Absolute URL. Follow `next` to page through results.")


class SearchInfo(BaseModel):
    mode: Literal["match", "include"] = Field(description="`match` for a search result, `include` for a resource pulled in by `_include`.")


class CapabilityResource(BaseModel):
    type: str
    interaction: list[dict[str, str]] = Field(description="Operations supported: `read`, `vread-history`, `search-type`, and `create`/`update` only when writes are enabled.")
    searchParam: list[dict[str, str]] = Field(description="Search parameter names this resource accepts.")
    searchInclude: list[str] = Field(description="Values accepted by `_include` for this resource.")


class CapabilityRest(BaseModel):
    mode: Literal["server"] = "server"
    resource: list[CapabilityResource]


class CapabilityStatement(BaseModel):
    """What this server supports."""
    resourceType: Literal["CapabilityStatement"] = "CapabilityStatement"
    software: dict[str, str]
    rest: list[CapabilityRest]


@lru_cache(maxsize=None)
def search_bundle_model(rtype: str) -> type[BaseModel]:
    cls = REGISTRY[rtype]
    entry = create_model(f"{rtype}SearchEntry", fullUrl=(str, Field(description="`Type/id` of the resource.")),
                         resource=(cls, Field(description=f"The {rtype}. Entries with `search.mode = include` may be other resource types.")),
                         search=(SearchInfo, ...))
    return create_model(f"{rtype}SearchBundle", resourceType=(Literal["Bundle"], "Bundle"), type=(Literal["searchset"], "searchset"),
                        total=(int, Field(description="Number of matches across all pages, not just this page.")),
                        link=(list[BundleLink], Field(default_factory=list)), entry=(list[entry], Field(default_factory=list)))


@lru_cache(maxsize=None)
def history_bundle_model(rtype: str) -> type[BaseModel]:
    cls = REGISTRY[rtype]
    entry = create_model(f"{rtype}HistoryEntry", fullUrl=(str, Field(description="`Type/id/_history/version`.")),
                         resource=(cls, Field(description="That version of the resource.")))
    return create_model(f"{rtype}HistoryBundle", resourceType=(Literal["Bundle"], "Bundle"), type=(Literal["history"], "history"),
                        total=(int, Field(description="Number of versions.")), entry=(list[entry], Field(default_factory=list)))


# ---------------------------------------------------------------------------------------------------------------
# Search parameters
# ---------------------------------------------------------------------------------------------------------------
Spec = tuple[str, dict[str, Any] | None]   # (description, schema override)


def _enum(codes) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(codes) if not isinstance(codes, list) else codes}


CHAMBERS = _enum(["senate", "house"])
OFFICES = _enum(list(cs.CODES[cs.OFFICE]))
PARTIES = _enum(list(cs.CODES[cs.PARTY]))
ELECTION_KINDS = _enum(list(cs.CODES[cs.ELECTION_TYPE]))
OUTCOMES = _enum(list(cs.CODES[cs.CONTEST_OUTCOME]))
DATE = {"type": "string", "format": "date"}
INTEGER = {"type": "integer"}


def _ref(target: str) -> Spec:
    return (f"A `{target}`, given as its id or as `{target}/id`.", None)


STATE = ("A US state, territory or DC by USPS code, case-insensitive (e.g. `TX`, `DC`, `PR`).", None)
DISTRICT = ("A House district by label, such as `TX-12`. At-large districts are `VT-AL`. Numbers are redrawn after each census, "
            "so the same label can mean different territory in different years.", None)

COMMON_DOCS: dict[str, Spec] = {
    "_id": ("Match a single resource by its UUID.", {"type": "string", "format": "uuid"}),
    "identifier": ("Match by identifier. Give `system|value` (e.g. `https://bioguide.congress.gov|X000000`) or just `value` "
                   "to match that value in any system.", None),
}

PARAM_DOCS: dict[str, dict[str, Spec]] = {
    "Person": {
        "name": ("Case-insensitive substring of the person's display name or family name.", None),
        "family": ("Case-insensitive substring of the family name.", None),
        "gender": ("Exact match: `female` or `male`.", _enum(["female", "male"])),
        "birthdate": ("Date of birth, `YYYY-MM-DD`, optionally prefixed with a comparison: `eq` (default), `ge`, `le`, `gt` or `lt`, "
                      "e.g. `ge1950-01-01`.", None),
    },
    "OfficeTerm": {
        "person": _ref("Person"),
        "state": STATE,
        "district": DISTRICT,
        "chamber": ("The chamber.", CHAMBERS),
        "office": ("The office held.", OFFICES),
        "class": ("Senate class, 1 to 3. Senate terms only.", {"type": "integer", "minimum": 1, "maximum": 3}),
        "active-on": ("Only terms whose period contains this date (`YYYY-MM-DD`). This is how to ask who held a seat on a given day.", DATE),
        "party": ("Only terms whose holder belonged to this party on the `active-on` date (today if `active-on` is not given).", PARTIES),
    },
    "PartyAffiliation": {
        "person": _ref("Person"),
        "party": ("Party code.", PARTIES),
        "active-on": ("Only affiliations whose period contains this date (`YYYY-MM-DD`).", DATE),
    },
    "Organization": {
        "name": ("Case-insensitive substring of the name.", None),
        "type": ("Kind of organization.", _enum(list(cs.CODES[cs.ORG_TYPE]))),
        "partof": _ref("Organization"),
    },
    "Location": {
        "name": ("Case-insensitive substring of the name, e.g. `TX-1` or `Texas`.", None),
        "type": ("Level in the hierarchy.", _enum(list(cs.CODES[cs.LOCATION_TYPE]))),
        "partof": _ref("Location"),
    },
    "Election": {
        "year": ("Election year, e.g. `2024`.", INTEGER),
        "kind": ("Kind of election.", ELECTION_KINDS),
    },
    "Contest": {
        "election": _ref("Election"),
        "year": ("Election year of the contest's election, e.g. `2024`.", INTEGER),
        "office": ("The office contested.", OFFICES),
        "state": STATE,
        "district": DISTRICT,
        "special": ("`true` for special elections, `false` for regular ones.", _enum(["true", "false"])),
    },
    "Candidacy": {
        "contest": _ref("Contest"),
        "person": ("A `Person`, given as an id or `Person/id`. Only candidacies linked to a person match (today: winners who are current members).", None),
        "name": ("Case-insensitive substring of the candidate's name as printed by the source (upper case).", None),
        "party": ("Party code of the candidacy.", PARTIES),
    },
    "ContestResult": {
        "contest": _ref("Contest"),
        "candidacy": _ref("Candidacy"),
        "year": ("Election year, e.g. `2024`.", INTEGER),
        "office": ("The office contested.", OFFICES),
        "state": STATE,
        "party": ("Candidate's party code.", PARTIES),
        "outcome": ("Computed outcome. Outcomes are derived from vote counts, not official.", OUTCOMES),
    },
    "Provenance": {
        "target": ("Provenance records that cover this resource, as `Type/id` (e.g. `Person/0b1f6c2e-...`).", None),
        "source": ("Exact source name, e.g. `unitedstates/congress-legislators` or `MIT Election Data and Science Lab`.", None),
    },
}

RESERVED_DOCS = {
    "_count": ("Page size: 0 to 500, default 50. `0` returns just the `total`.", {"type": "integer", "minimum": 0, "maximum": 500, "default": 50}),
    "_offset": ("Number of results to skip, for paging. Prefer following the `next` link in the response.", {"type": "integer", "minimum": 0, "default": 0}),
}


def _qparam(name: str, desc: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    return {"name": name, "in": "query", "required": False, "description": desc, "schema": schema or {"type": "string"}}


def search_parameters(rtype: str) -> list[dict[str, Any]]:
    """OpenAPI parameter objects for GET /{rtype}: the type's own parameters, the common ones, then paging and includes."""
    params = [_qparam(n, d, s) for n, (d, s) in PARAM_DOCS[rtype].items()]
    params += [_qparam(n, d, s) for n, (d, s) in COMMON_DOCS.items()]
    params += [_qparam(n, d, s) for n, (d, s) in RESERVED_DOCS.items()]
    includes = [f"{rtype}:{f}" for f in INCLUDES.get(rtype, {})]
    if includes:
        params.append({"name": "_include", "in": "query", "required": False, "style": "form", "explode": True,
                       "description": "Also return the resource each named reference points to, as extra Bundle entries with "
                                      "`search.mode = include`. Repeat the parameter to include several.",
                       "schema": {"type": "array", "items": {"type": "string", "enum": includes}}})
    return params


def documented_param_names(rtype: str) -> set[str]:
    return set(PARAM_DOCS[rtype]) | set(COMMON_DOCS)


# ---------------------------------------------------------------------------------------------------------------
# Resource descriptions and examples (all names, ids and numbers in examples are fictional)
# ---------------------------------------------------------------------------------------------------------------
RESOURCE_DOCS: dict[str, dict[str, Any]] = {
    "Person": {
        "plural": "people", "one": "person",
        "about": "A human being, with a stable identity across chambers, parties and decades. Roles and party membership hang off a "
                 "Person as `OfficeTerm` and `PartyAffiliation` resources. `identifier` carries cross-reference IDs (Bioguide, FEC, "
                 "ICPSR, GovTrack, OpenSecrets, Wikidata, ...) so other datasets can be joined.",
        "queries": ["/Person?name=cantwell", "/Person?identifier=https://bioguide.congress.gov|C000127",
                    "/Person?gender=female&birthdate=ge1970-01-01"],
    },
    "OfficeTerm": {
        "plural": "office terms", "one": "office term",
        "about": "A person holding a Senate or House seat for a period of time. This is the resource to ask \"who held this seat on "
                 "this date\" with `active-on`. `period.end` is the scheduled end of the term; an appointee's term can end on the "
                 "special-election date even though they keep serving until a successor is seated.",
        "queries": ["/OfficeTerm?state=TX&chamber=senate&active-on=2026-11-10&_include=OfficeTerm:person",
                    "/OfficeTerm?state=TX&chamber=house&party=R&active-on=2026-11-10",
                    "/OfficeTerm?district=TX-12&active-on=2020-01-15"],
    },
    "PartyAffiliation": {
        "plural": "party affiliations", "one": "party affiliation",
        "about": "A person's party over a period. It is a separate resource (not a field on the term) because people change party "
                 "mid-term, and one term can span several affiliations.",
        "queries": ["/PartyAffiliation?person=Person/<id>", "/PartyAffiliation?party=I&active-on=2026-11-10&_include=PartyAffiliation:person"],
    },
    "Organization": {
        "plural": "organizations", "one": "organization",
        "about": "An organization. Today these are the political parties that terms and candidates refer to; committees and caucuses "
                 "are planned. A party's `identifier` with system `urn:polstats:party` holds its code (`D`, `R`, `I`, ...).",
        "queries": ["/Organization?type=party", "/Organization?name=republican"],
    },
    "Location": {
        "plural": "locations", "one": "location",
        "about": "A place in the hierarchy nation > state > congressional district. States carry their USPS code as an identifier "
                 "(system `https://www.usps.com/state`); districts carry their label such as `TX-12` or `VT-AL`. Districts from every "
                 "decade are present, so a label does not pin down territory.",
        "queries": ["/Location?type=state", "/Location?name=TX-1", "/Location?partof=Location/<state id>&type=district"],
    },
    "Election": {
        "plural": "elections", "one": "election",
        "about": "A cycle and kind of election, such as the 2024 general or a 2021 runoff. The source data has no election dates, so "
                 "`date` is set only for regular November general elections.",
        "queries": ["/Election?year=2024", "/Election?kind=runoff"],
    },
    "Contest": {
        "plural": "contests", "one": "contest",
        "about": "One race: an office in a state (and a district, for the House) within an election. `uncontested` races carry no "
                 "vote counts because the sources store only a placeholder.",
        "queries": ["/Contest?year=2024&office=senator&state=TX", "/Contest?district=TX-12", "/Contest?special=true&year=2020"],
    },
    "Candidacy": {
        "plural": "candidacies", "one": "candidacy",
        "about": "A candidate in a contest. Catch-all rows from the sources (write-ins, blank and void votes, \"exhausted ballots\") are "
                 "kept as candidacies but never win. `person` is set only where the link to a `Person` is unambiguous.",
        "queries": ["/Candidacy?contest=Contest/<id>", "/Candidacy?name=cruz&party=R&_include=Candidacy:contest"],
    },
    "ContestResult": {
        "plural": "contest results", "one": "contest result",
        "about": "The votes one candidacy received. **`outcome` is computed by polstats** from the vote counts (plurality; runoff pairs "
                 "marked `advanced-to-runoff`); the sources have no winner column, so it is not an official or certified result. "
                 "`year`, `office`, `state` and `partyCode` are copied from the contest so results can be searched directly.",
        "queries": ["/ContestResult?year=2024&state=TX&office=senator&outcome=won&_include=ContestResult:candidacy",
                    "/ContestResult?party=D&outcome=won&year=2018&office=representative&_count=0"],
    },
    "Provenance": {
        "plural": "provenance records", "one": "provenance record",
        "about": "Where a set of records came from: the source, its license, and when it was fetched. Loaders write one per person "
                 "and one per contest, so any record can be traced back to its source.",
        "queries": ["/Provenance?source=unitedstates/congress-legislators&_count=1", "/Provenance?target=Person/<id>"],
    },
}

_META = {"versionId": "1", "lastUpdated": "2026-11-10T21:04:11Z"}
_PERSON = "0b1f6c2e-5d4a-4c1e-9a77-3f2d8e6b1a40"
_PARTY = "5c2d9e71-8a3b-4f60-b1d2-7e4a9c0f3b15"
_STATE = "9e4b7a12-3c58-4d0f-8a61-5b2f0d7c9e33"
_DISTRICT = "2f8c6d90-7b14-4e3a-9c05-1a6d4b8e7f22"
_ELECTION = "c7a1e3f4-6b28-4d95-8e10-9a3b5c7d2e61"
_CONTEST = "e1d4b8a6-2f73-4c09-b5e8-0d6a9c3f7b14"
_CANDIDACY = "4a9c0e7b-1d52-4f86-a3b9-8c2e6d0f5a77"


def _concept(system: str, code: str, display: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}]}


EXAMPLES: dict[str, dict[str, Any]] = {
    "Person": {"resourceType": "Person", "id": _PERSON, "meta": _META,
               "identifier": [{"system": cs.ID_BIOGUIDE, "value": "X000000"}, {"system": cs.ID_FEC, "value": "H4TX12000"}],
               "name": [{"use": "official", "family": "Example", "given": ["Alex", "Q."], "text": "Alex Q. Example"}],
               "gender": "female", "birthDate": "1970-01-15"},
    "OfficeTerm": {"resourceType": "OfficeTerm", "id": "8d3f6a21-9b4c-4e05-a7d2-1c5e0b9f3a68", "meta": _META,
                   "identifier": [{"system": "urn:polstats:office-term", "value": "X000000:rep:2025-01-03"}],
                   "person": {"reference": f"Person/{_PERSON}"},
                   "office": _concept(cs.OFFICE, "representative", "Representative"), "chamber": _concept(cs.CHAMBER, "house", "House of Representatives"),
                   "state": {"reference": f"Location/{_STATE}"}, "district": {"reference": f"Location/{_DISTRICT}"},
                   "period": {"start": "2025-01-03", "end": "2027-01-03"}},
    "PartyAffiliation": {"resourceType": "PartyAffiliation", "id": "b2e7c4d9-5a31-4f68-9e0b-3d8a1c6f4e52", "meta": _META,
                         "identifier": [{"system": "urn:polstats:party-affiliation", "value": "X000000:D:2025-01-03"}],
                         "person": {"reference": f"Person/{_PERSON}"}, "party": {"reference": f"Organization/{_PARTY}", "display": "Democratic"},
                         "period": {"start": "2025-01-03", "end": "2027-01-03"}},
    "Organization": {"resourceType": "Organization", "id": _PARTY, "meta": _META,
                     "identifier": [{"system": cs.PARTY, "value": "D"}],
                     "type": _concept(cs.ORG_TYPE, "party", "Party"), "name": "Democratic"},
    "Location": {"resourceType": "Location", "id": _DISTRICT, "meta": _META,
                 "identifier": [{"system": "urn:polstats:district", "value": "TX-12"}],
                 "type": _concept(cs.LOCATION_TYPE, "district", "Congressional district"), "name": "TX-12",
                 "partOf": {"reference": f"Location/{_STATE}", "display": "Texas"}},
    "Election": {"resourceType": "Election", "id": _ELECTION, "meta": _META,
                 "identifier": [{"system": "urn:polstats:election", "value": "2024:general"}],
                 "name": "2024 general election", "year": 2024, "kind": _concept(cs.ELECTION_TYPE, "general", "General"), "date": "2024-11-05"},
    "Contest": {"resourceType": "Contest", "id": _CONTEST, "meta": _META,
                "identifier": [{"system": "urn:polstats:contest", "value": "2024:general:TX:representative:TX-12"}],
                "election": {"reference": f"Election/{_ELECTION}"}, "office": _concept(cs.OFFICE, "representative", "Representative"),
                "state": {"reference": f"Location/{_STATE}"}, "district": {"reference": f"Location/{_DISTRICT}"},
                "special": False, "uncontested": False, "totalVotes": 339718, "reporting": "official"},
    "Candidacy": {"resourceType": "Candidacy", "id": _CANDIDACY, "meta": _META,
                  "identifier": [{"system": "urn:polstats:candidacy", "value": "2024:general:TX:representative:TX-12:ALEX Q EXAMPLE:n"}],
                  "contest": {"reference": f"Contest/{_CONTEST}"}, "name": "ALEX Q. EXAMPLE",
                  "person": {"reference": f"Person/{_PERSON}"}, "party": {"reference": f"Organization/{_PARTY}"},
                  "partyLabel": "DEMOCRAT", "writeIn": False},
    "ContestResult": {"resourceType": "ContestResult", "id": "6f0b3d85-7c19-4a2e-8d64-2e9a1c5b7f03", "meta": _META,
                      "identifier": [{"system": "urn:polstats:contest-result", "value": "2024:general:TX:representative:TX-12:ALEX Q EXAMPLE:n"}],
                      "contest": {"reference": f"Contest/{_CONTEST}"}, "candidacy": {"reference": f"Candidacy/{_CANDIDACY}"},
                      "year": 2024, "office": _concept(cs.OFFICE, "representative", "Representative"), "state": {"reference": f"Location/{_STATE}"},
                      "partyCode": "D", "votes": 215564, "voteShare": 0.634552,
                      "outcome": _concept(cs.CONTEST_OUTCOME, "won", "Won"), "outcomeDerived": True},
    "Provenance": {"resourceType": "Provenance", "id": "d9a5e2c7-3b60-4f18-a4d1-7e8c0b2f6a95", "meta": _META,
                   "identifier": [{"system": "urn:polstats:provenance", "value": "unitedstates/congress-legislators:X000000"}],
                   "target": [{"reference": f"Person/{_PERSON}"}], "source": "unitedstates/congress-legislators",
                   "sourceUrl": "https://github.com/unitedstates/congress-legislators", "sourceVersion": "9f2b4c1a7d03",
                   "recorded": "2026-11-10", "license": "CC0-1.0"},
}


def example_body(rtype: str) -> dict[str, Any]:
    """The example as it would be POSTed: the server assigns `id` and `meta`."""
    return {k: v for k, v in EXAMPLES[rtype].items() if k not in ("id", "meta")}


# ---------------------------------------------------------------------------------------------------------------
# Overview, tags and per-operation documentation
# ---------------------------------------------------------------------------------------------------------------
API_SUMMARY = "Open data on US national politics: members of Congress, the terms they serve, and Senate and House election results."

API_DESCRIPTION = f"""
polstats serves who sits in Congress, from which state and district, in which party and when, and how seats were won in
elections since 1976. Everything is a **resource**: a JSON document with a `resourceType`, an `id` and version metadata.
Resources point at each other with `Type/id` references. The explorer at [`/ui/`](/ui/) is a client of this API.

### Resources

| Resource | What it is |
|---|---|
| **Person** | A human being, with cross-reference IDs |
| **OfficeTerm** | A person holding a Senate or House seat for a period |
| **PartyAffiliation** | A person's party over a period |
| **Organization** | A political party (committees planned) |
| **Location** | Nation > state > congressional district |
| **Election** | A cycle and kind of election (2024 general, a runoff, ...) |
| **Contest** | One race within an election |
| **Candidacy** | A candidate in a contest |
| **ContestResult** | Votes for one candidacy, with a *computed* outcome |
| **Provenance** | Where a set of records came from |

Each resource has the same five operations: search (`GET /Type`), read (`GET /Type/{{id}}`), history
(`GET /Type/{{id}}/_history`), create (`POST /Type`) and update (`PUT /Type/{{id}}`).

### Searching
`GET /Type?param=value` returns a **Bundle** (`type: searchset`) of matches. Several parameters combine with AND; parameters
are typed and **unknown ones are rejected with `400`** rather than silently ignored. Each resource's parameters are listed on
its operations below. Every resource also accepts `_id` and `identifier`.

* **Paging:** `_count` (0 to 500, default 50) and `_offset`. The Bundle's `total` is the number of matches across all pages, and
  a `next` link is included while more remain. `_count=0` is a cheap way to just get the count.
* **`_include=Type:field`:** also returns the resource a reference points to, as extra entries marked `search.mode = include`.
  See each resource for the values it supports.
* **Dates:** `active-on` finds records whose period contains a date. `birthdate` accepts the prefixes `ge`, `le`, `gt`, `lt`.

### Versions
Every write creates a new version. `meta.versionId` counts up from `1`, reads return a weak `ETag` (`W/"<version>"`), and
`GET /Type/{{id}}/_history` returns every version, newest first. `PUT` honours `If-Match` and answers `412` on a stale version.

### Read-only by default
There is **no authentication**. `POST` and `PUT` answer `403` unless the server was started with `POLSTATS_ALLOW_WRITES=1`, so
enable writes only on a machine you control. Reading and searching always work.

### Errors
Every error is an `OperationOutcome`: `400` for an invalid request (bad parameter, bad body), `403` when writes are disabled,
`404` for an unknown type or id, `412` for a version conflict.

### About the data
Current members come from [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) and election
results from the [MIT Election Data + Science Lab](https://electionlab.mit.edu/data) (both CC0). **Election outcomes are computed from
vote counts and are not official or certified results.** Only people serving today are loaded, and nothing after 2024 is included
yet. See the [project README]({README}#readme) for sources, licenses and known limitations.
""".strip()

SERVICE_DESCRIPTION = "Endpoints about the service itself."


def tag_description(rtype: str) -> str:
    d, cls = RESOURCE_DOCS[rtype], REGISTRY[rtype]
    rows = [f"| `{n}` | {desc} |" for n, (desc, _) in PARAM_DOCS[rtype].items()]
    rows += [f"| `{n}` | {desc.split('.')[0]}. |" for n, (desc, _) in COMMON_DOCS.items()]
    includes = INCLUDES.get(rtype, {})
    inc = ("\n\n**`_include` values:** " + ", ".join(f"`{rtype}:{f}` (a {t})" for f, t in includes.items()) + ".") if includes else ""
    return (f"{d['about']}\n\n**Search parameters**\n\n| Parameter | Matches |\n|---|---|\n" + "\n".join(rows) + inc
            + f"\n\nSee the **{cls.__name__}** schema at the bottom of the page for every field.")


def openapi_tags() -> list[dict[str, Any]]:
    tags = [{"name": SERVICE_TAG, "description": SERVICE_DESCRIPTION}]
    tags += [{"name": t, "description": tag_description(t)} for t in TAG_ORDER if t in REGISTRY]
    return tags


_HEADERS_READ = {
    "ETag": {"description": "Weak entity tag of the form `W/\"<version>\"`. Send it back as `If-Match` on a `PUT`.", "schema": {"type": "string"}},
    "Last-Modified": {"description": "When this version was written.", "schema": {"type": "string"}},
}
ID_DESCRIPTION = "The resource's id (a UUID), as returned in `id` or in a Bundle's `fullUrl`."


def _outcome(desc: str) -> dict[str, Any]:
    return {"model": OperationOutcome, "description": desc}


def route_docs(rtype: str) -> dict[str, dict[str, Any]]:
    """Keyword arguments for FastAPI's add_api_route, per operation, for one resource type."""
    d, cls, ex = RESOURCE_DOCS[rtype], REGISTRY[rtype], EXAMPLES[rtype]
    one, plural = d["one"], d["plural"]
    example_urls = "\n".join(f"* `GET {q}`" for q in d["queries"])
    json = lambda example: {"application/json": {"example": example}}   # noqa: E731
    return {
        "search": dict(
            tags=[rtype], operation_id=f"search{rtype}", summary=f"Search {plural}",
            description=(f"Returns a Bundle of the {plural} that match **all** the parameters given, oldest-updated first. With no "
                         f"parameters it pages through every {one}.\n\nExamples:\n\n{example_urls}\n\n"
                         "Unknown parameters and malformed values answer `400`."),
            response_description=f"A Bundle of matching {plural}.",
            responses={200: {"model": search_bundle_model(rtype), "description": f"A Bundle of matching {plural}.",
                             "content": json({"resourceType": "Bundle", "type": "searchset", "total": 1,
                                              "link": [{"relation": "self", "url": f"http://localhost:8000/{rtype}?_count=1"}],
                                              "entry": [{"fullUrl": f"{rtype}/{ex['id']}", "resource": ex, "search": {"mode": "match"}}]})},
                       400: _outcome("An unknown search parameter, or a value that does not fit it.")},
            openapi_extra={"parameters": search_parameters(rtype)}),
        "read": dict(
            tags=[rtype], operation_id=f"read{rtype}", summary=f"Read a {one}",
            description=f"Returns one {one} by id, with `ETag` and `Last-Modified` headers.",
            response_description=f"The {one}.",
            responses={200: {"model": cls, "description": f"The {one}.", "headers": _HEADERS_READ, "content": json(ex)},
                       404: _outcome(f"No {one} has that id.")}),
        "history": dict(
            tags=[rtype], operation_id=f"history{rtype}", summary=f"List the versions of a {one}",
            description=f"Every version of one {one}, newest first, as a Bundle (`type: history`). Each update creates a new version.",
            response_description="A Bundle with one entry per version.",
            responses={200: {"model": history_bundle_model(rtype), "description": "All versions, newest first."},
                       404: _outcome(f"No {one} has that id.")}),
        "create": dict(
            tags=[rtype], operation_id=f"create{rtype}", summary=f"Create a {one}", status_code=201,
            description=(f"Creates a {one}. The server assigns `id` and `meta`; any you send are ignored. The body is validated: "
                         "unknown fields and wrong types answer `400`.\n\n"
                         "**Disabled by default**: answers `403` unless the server runs with `POLSTATS_ALLOW_WRITES=1`."),
            response_description=f"The created {one}.",
            responses={201: {"model": cls, "description": f"The created {one}, as version 1.",
                             "headers": {"Location": {"description": "Where the new version can be read.", "schema": {"type": "string"}},
                                         "ETag": _HEADERS_READ["ETag"]}, "content": json(ex)},
                       400: _outcome("The body is not valid JSON, has the wrong `resourceType`, or fails validation."),
                       403: _outcome("Writes are disabled on this server.")},
            openapi_extra={"requestBody": {"required": True, "description": f"The {one} to create (`id` and `meta` are ignored).",
                                           "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{cls.__name__}"},
                                                                            "example": example_body(rtype)}}}}),
        "update": dict(
            tags=[rtype], operation_id=f"update{rtype}", summary=f"Update a {one}",
            description=(f"Replaces a {one} with the body and creates a new version. The body's `id` must match the URL, and `PUT` "
                         "does not create. Send the `ETag` you read as `If-Match` to avoid overwriting someone else's change.\n\n"
                         "**Disabled by default**: answers `403` unless the server runs with `POLSTATS_ALLOW_WRITES=1`."),
            response_description=f"The updated {one}.",
            responses={200: {"model": cls, "description": f"The updated {one}, with its new version.", "headers": {"ETag": _HEADERS_READ["ETag"]}},
                       400: _outcome("Invalid body, or the body's `id` does not match the URL."),
                       403: _outcome("Writes are disabled on this server."),
                       404: _outcome(f"No {one} has that id."),
                       412: _outcome("`If-Match` does not match the current version.")},
            openapi_extra={"parameters": [{"name": "If-Match", "in": "header", "required": False,
                                           "description": "The `ETag` you read, e.g. `W/\"2\"`. The update is refused (`412`) if the resource has changed since.",
                                           "schema": {"type": "string"}}],
                           "requestBody": {"required": True, "description": f"The full {one}, including its `id`.",
                                           "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{cls.__name__}"},
                                                                            "example": ex}}}}),
    }


METADATA_DOCS = dict(
    tags=[SERVICE_TAG], operation_id="getCapabilities", summary="Capability statement",
    description="Lists every resource type with the operations it supports (`create` and `update` appear only when writes are "
                "enabled), its search parameters and its `_include` values.",
    response_description="The capability statement.",
    responses={200: {"model": CapabilityStatement, "description": "What this server supports."}})
