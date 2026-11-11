"""Code systems (vocabularies). Extend by adding codes, no migration needed."""
from __future__ import annotations

from .datatypes import CodeableConcept, Coding

PARTY = "urn:polstats:party"
CHAMBER = "urn:polstats:chamber"
OFFICE = "urn:polstats:office"
ORG_TYPE = "urn:polstats:organization-type"
LOCATION_TYPE = "urn:polstats:location-type"
TERM_END_REASON = "urn:polstats:term-end-reason"
ELECTION_TYPE = "urn:polstats:election-type"
VOTE_POSITION = "urn:polstats:vote-position"
CONTEST_OUTCOME = "urn:polstats:contest-outcome"

ID_MEDSL = "https://doi.org/10.7910/DVN"  # MIT Election Data + Science Lab datasets on Harvard Dataverse

# Identifier systems (cross-walk IDs)
ID_BIOGUIDE = "https://bioguide.congress.gov"
ID_FEC = "https://www.fec.gov/candidate-id"
ID_ICPSR = "https://voteview.com/icpsr"
ID_GOVTRACK = "https://www.govtrack.us/person"
ID_OPENSECRETS = "https://www.opensecrets.org/cid"
ID_WIKIDATA = "https://www.wikidata.org/entity"
ID_FIPS = "https://www.census.gov/fips"
ID_USPS = "https://www.usps.com/state"

CODES: dict[str, dict[str, str]] = {
    PARTY: {"D": "Democratic", "R": "Republican", "I": "Independent", "L": "Libertarian", "G": "Green",
            "other": "Other"},
    CONTEST_OUTCOME: {"won": "Won", "lost": "Lost", "advanced-to-runoff": "Advanced to runoff"},
    CHAMBER: {"house": "House of Representatives", "senate": "Senate"},
    OFFICE: {"representative": "Representative", "senator": "Senator",
             "delegate": "Delegate", "resident-commissioner": "Resident Commissioner"},
    ORG_TYPE: {"party": "Party", "chamber": "Chamber", "committee": "Committee",
               "subcommittee": "Subcommittee", "caucus": "Caucus"},
    LOCATION_TYPE: {"nation": "Nation", "state": "State", "district": "Congressional district"},
    TERM_END_REASON: {"term-expired": "Term expired", "resigned": "Resigned", "died": "Died",
                      "lost-election": "Lost election", "expelled": "Expelled", "appointed-other": "Appointed elsewhere"},
    ELECTION_TYPE: {"general": "General", "primary": "Primary", "special": "Special", "runoff": "Runoff"},
    VOTE_POSITION: {"yea": "Yea", "nay": "Nay", "present": "Present", "not-voting": "Not voting"},
}


def concept(system: str, code: str) -> CodeableConcept:
    """Build a CodeableConcept, validating the code against its system."""
    if system in CODES and code not in CODES[system]:
        raise ValueError(f"unknown code {code!r} for {system}")
    return CodeableConcept(coding=[Coding(system=system, code=code, display=CODES.get(system, {}).get(code))])
