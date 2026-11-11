"""Phase-1 resources. Every resource has resourceType, id, meta.

The docstrings and field descriptions below are published in the API documentation (/docs, /redoc)."""
from __future__ import annotations

from datetime import date
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .datatypes import (CodeableConcept, Element, Extension, HumanName, Identifier,
                        Meta, Period, Reference)


_Date = date  # alias: a field named `date` would otherwise shadow the type in its own annotation

_ID = "Server-assigned UUID. Leave it out when creating; it is required (and must match the URL) when updating."
_META = "Version bookkeeping, assigned by the server."
_IDENT = ("Cross-reference and natural-key identifiers (`system` + `value`). Search with "
          "`identifier=system|value`.")
_EXT = "Extra data that has no field of its own yet."


class Resource(BaseModel):
    """Fields shared by every resource."""
    model_config = ConfigDict(extra="forbid")
    resourceType: str = Field(description="The resource's type, e.g. `Person`. Must match the endpoint on create and update.")
    id: str | None = Field(None, description=_ID)
    meta: Meta | None = Field(None, description=_META)
    identifier: list[Identifier] = Field(default_factory=list, description=_IDENT)
    extension: list[Extension] = Field(default_factory=list, description=_EXT)

    table: ClassVar[str]


class Education(Element):
    """An education entry (modelled but not yet populated by any loader)."""
    institution: str = Field(description="School or university.")
    degree: str | None = Field(None, description="Degree earned.")
    period: Period | None = Field(None, description="When they attended.")


class Occupation(Element):
    """An occupation held before or alongside office (modelled but not yet populated)."""
    text: str = Field(description="What the occupation was.")
    period: Period | None = Field(None, description="When they held it.")


class MilitaryService(Element):
    """Military service (modelled but not yet populated)."""
    branch: str | None = Field(None, description="Branch of service.")
    period: Period | None = Field(None, description="When they served.")


class Person(Resource):
    """A human being: stable identity across chambers, parties and decades. Roles and party membership hang off
    a Person as `OfficeTerm` and `PartyAffiliation` resources."""
    resourceType: Literal["Person"] = "Person"
    table: ClassVar[str] = "person"
    name: list[HumanName] = Field(default_factory=list, description="Names; the first is the official name.")
    gender: str | None = Field(None, description="`female` or `male`, as recorded by the source.")
    birthDate: date | None = Field(None, description="Date of birth (`YYYY-MM-DD`).")
    birthPlace: str | None = Field(None, description="Place of birth, free text.")
    education: list[Education] = Field(default_factory=list, description="Education history (not populated yet).")
    occupation: list[Occupation] = Field(default_factory=list, description="Occupations (not populated yet).")
    military: list[MilitaryService] = Field(default_factory=list, description="Military service (not populated yet).")


class Organization(Resource):
    """An organization. Today these are political parties; committees and caucuses are planned."""
    resourceType: Literal["Organization"] = "Organization"
    table: ClassVar[str] = "organization"
    type: CodeableConcept | None = Field(None, description="Kind of organization (`urn:polstats:organization-type`): `party`, `chamber`, `committee`, `subcommittee`, `caucus`.")
    name: str = Field(description="Display name, e.g. `Democratic`.")
    partOf: Reference | None = Field(None, description="Parent organization, e.g. a subcommittee's committee.")


class Location(Resource):
    """A place in the hierarchy nation > state > congressional district."""
    resourceType: Literal["Location"] = "Location"
    table: ClassVar[str] = "location"
    type: CodeableConcept = Field(description="`nation`, `state` or `district` (`urn:polstats:location-type`).")
    name: str = Field(description="Display name. For a district this is its label, e.g. `TX-12` or `VT-AL` (at-large).")
    partOf: Reference | None = Field(None, description="The containing location: district > state > nation.")


class OfficeTerm(Resource):
    """A person holding a seat for a period: a Senate or House term. Use `active-on` to ask who held a seat on a date."""
    resourceType: Literal["OfficeTerm"] = "OfficeTerm"
    table: ClassVar[str] = "office_term"
    person: Reference = Field(description="The `Person` who holds the seat.")
    office: CodeableConcept = Field(description="`senator`, `representative`, `delegate` or `resident-commissioner` (`urn:polstats:office`).")
    chamber: CodeableConcept = Field(description="`senate` or `house` (`urn:polstats:chamber`).")
    state: Reference = Field(description="The state (`Location`).")
    district: Reference | None = Field(None, description="The House district (`Location`); absent for the Senate.")
    senateClass: int | None = Field(None, description="Senate class 1, 2 or 3; absent for the House.")
    period: Period = Field(description="When the term runs. `end` is the *scheduled* end; see the data notes on hold-overs.")
    endReason: CodeableConcept | None = Field(None, description="Why the term ended early, if known (not populated yet).")
    electedIn: Reference | None = Field(None, description="The `Contest` that put them in the seat (reserved; not populated yet).")


class PartyAffiliation(Resource):
    """A person's party over a period. Kept separate from terms because people change party mid-term."""
    resourceType: Literal["PartyAffiliation"] = "PartyAffiliation"
    table: ClassVar[str] = "party_affiliation"
    person: Reference = Field(description="The `Person`.")
    party: Reference = Field(description="The party (`Organization` with type `party`).")
    period: Period = Field(description="When the affiliation holds.")


class Provenance(Resource):
    """Where a set of records came from: source, version and license. Each loader writes one per person or contest."""
    resourceType: Literal["Provenance"] = "Provenance"
    table: ClassVar[str] = "provenance"
    target: list[Reference] = Field(description="The resources this provenance covers.")
    source: str = Field(description="Name of the source, e.g. `unitedstates/congress-legislators`.")
    sourceUrl: str | None = Field(None, description="Where the source can be found.")
    sourceVersion: str | None = Field(None, description="Version of the source file (a content hash).")
    recorded: str | None = Field(None, description="Date the source file was fetched (ISO 8601).")
    license: str | None = Field(None, description="License of the source data, e.g. `CC0-1.0`.")


class Election(Resource):
    """A cycle and kind of election, e.g. the 2024 general. The sources carry no dates, so `date` is only set where it
    can be derived (regular November general elections)."""
    resourceType: Literal["Election"] = "Election"
    table: ClassVar[str] = "election"
    name: str = Field(description="Display name, e.g. `2024 general election`.")
    year: int = Field(description="Election year.")
    kind: CodeableConcept = Field(description="`general`, `primary`, `special` or `runoff` (`urn:polstats:election-type`).")
    date: _Date | None = Field(None, description="Election Day, only for regular November general elections.")


class Contest(Resource):
    """One race: an office in a state (and a district for the House) within an `Election`."""
    resourceType: Literal["Contest"] = "Contest"
    table: ClassVar[str] = "contest"
    election: Reference = Field(description="The `Election` this race belongs to.")
    office: CodeableConcept = Field(description="`senator`, `representative` or `delegate`.")
    state: Reference = Field(description="The state (`Location`).")
    district: Reference | None = Field(None, description="The House district (`Location`); absent for the Senate.")
    special: bool = Field(False, description="True for a special election.")
    uncontested: bool = Field(False, description="True for an unopposed race. Sources store a placeholder vote for these, so no vote counts are kept.")
    totalVotes: int | None = Field(None, description="Total votes as stated by the source; absent when unknown or uncontested.")
    reporting: str = Field("official", description="`official` or `unofficial` (some recent results were still unofficial in the source).")


class Candidacy(Resource):
    """A candidate in a `Contest`. Catch-all rows (write-ins, blank votes) are kept as candidacies but never win."""
    resourceType: Literal["Candidacy"] = "Candidacy"
    table: ClassVar[str] = "candidacy"
    contest: Reference = Field(description="The `Contest` they ran in.")
    name: str = Field(description="Candidate name as the source prints it (upper case).")
    person: Reference | None = Field(None, description="The `Person`, set only when the link is unambiguous (today: winners who are current members).")
    party: Reference | None = Field(None, description="The party (`Organization`). For candidates on several party lines, their best-supported Democratic or Republican line if any.")
    partyLabel: str | None = Field(None, description="The party line as printed in the source, e.g. `DEMOCRATIC-FARMER-LABOR`.")
    writeIn: bool = Field(False, description="True for write-in candidates.")


class ContestResult(Resource):
    """Votes for one `Candidacy`. Search-friendly fields (`year`, `office`, `state`, `partyCode`) are copied from the
    contest. **`outcome` is computed by polstats** (the sources have no winner column), so it is not an official result."""
    resourceType: Literal["ContestResult"] = "ContestResult"
    table: ClassVar[str] = "contest_result"
    contest: Reference = Field(description="The `Contest`.")
    candidacy: Reference = Field(description="The `Candidacy` these votes are for.")
    year: int = Field(description="Election year (copied from the contest, for searching).")
    office: CodeableConcept = Field(description="Office (copied from the contest).")
    state: Reference = Field(description="State (copied from the contest).")
    partyCode: str | None = Field(None, description="The candidate's party code: `D`, `R`, `I`, `L`, `G` or `other`.")
    votes: int | None = Field(None, description="Votes received. Absent for uncontested races, where sources hold only a placeholder.")
    voteShare: float | None = Field(None, description="`votes` divided by the contest's total votes, from 0 to 1.")
    outcome: CodeableConcept | None = Field(None, description="Computed outcome: `won`, `lost` or `advanced-to-runoff` (`urn:polstats:contest-outcome`). Absent for primaries.")
    outcomeDerived: bool = Field(True, description="Always true: outcomes are computed from vote counts, not taken from the source.")


REGISTRY: dict[str, type[Resource]] = {
    cls.model_fields["resourceType"].default: cls
    for cls in (Person, Organization, Location, OfficeTerm, PartyAffiliation, Provenance,
                Election, Contest, Candidacy, ContestResult)
}
