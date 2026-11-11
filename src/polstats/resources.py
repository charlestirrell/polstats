"""Phase-1 resources. Every resource has resourceType, id, meta."""
from __future__ import annotations

from datetime import date
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from .datatypes import (CodeableConcept, Element, Extension, HumanName, Identifier,
                        Meta, Period, Reference)


_Date = date  # alias: a field named `date` would otherwise shadow the type in its own annotation


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    id: str | None = None
    meta: Meta | None = None
    identifier: list[Identifier] = []
    extension: list[Extension] = []

    table: ClassVar[str]


class Education(Element):
    institution: str
    degree: str | None = None
    period: Period | None = None


class Occupation(Element):
    text: str
    period: Period | None = None


class MilitaryService(Element):
    branch: str | None = None
    period: Period | None = None


class Person(Resource):
    resourceType: Literal["Person"] = "Person"
    table: ClassVar[str] = "person"
    name: list[HumanName] = []
    gender: str | None = None
    birthDate: date | None = None
    birthPlace: str | None = None
    education: list[Education] = []
    occupation: list[Occupation] = []
    military: list[MilitaryService] = []


class Organization(Resource):
    resourceType: Literal["Organization"] = "Organization"
    table: ClassVar[str] = "organization"
    type: CodeableConcept | None = None  # party | chamber | committee ...
    name: str
    partOf: Reference | None = None  # subcommittee -> committee


class Location(Resource):
    resourceType: Literal["Location"] = "Location"
    table: ClassVar[str] = "location"
    type: CodeableConcept  # nation | state | district
    name: str
    partOf: Reference | None = None  # district -> state -> nation


class OfficeTerm(Resource):
    """A person holding a seat for a period (analogue of PractitionerRole)."""
    resourceType: Literal["OfficeTerm"] = "OfficeTerm"
    table: ClassVar[str] = "office_term"
    person: Reference
    office: CodeableConcept  # representative | senator ...
    chamber: CodeableConcept
    state: Reference  # Location
    district: Reference | None = None  # Location, House only
    senateClass: int | None = None  # 1-3, Senate only
    period: Period
    endReason: CodeableConcept | None = None
    electedIn: Reference | None = None  # Contest (reserved; not populated yet)


class PartyAffiliation(Resource):
    resourceType: Literal["PartyAffiliation"] = "PartyAffiliation"
    table: ClassVar[str] = "party_affiliation"
    person: Reference
    party: Reference  # Organization(type=party)
    period: Period


class Provenance(Resource):
    resourceType: Literal["Provenance"] = "Provenance"
    table: ClassVar[str] = "provenance"
    target: list[Reference]
    source: str  # e.g. "unitedstates/congress-legislators"
    sourceUrl: str | None = None
    sourceVersion: str | None = None
    recorded: str | None = None  # ISO datetime
    license: str | None = None


class Election(Resource):
    """A cycle + kind of election (e.g. 2024 general). Source data carries no dates, so `date` is only
    set where it is derivable (regular November general elections)."""
    resourceType: Literal["Election"] = "Election"
    table: ClassVar[str] = "election"
    name: str
    year: int
    kind: CodeableConcept  # general | primary | special | runoff
    date: _Date | None = None


class Contest(Resource):
    """One race: an office in a state (and district for the House) within an Election."""
    resourceType: Literal["Contest"] = "Contest"
    table: ClassVar[str] = "contest"
    election: Reference
    office: CodeableConcept  # senator | representative
    state: Reference  # Location
    district: Reference | None = None  # Location, House only
    special: bool = False
    uncontested: bool = False  # sources store placeholder votes (1 or -1) for these; we keep none
    totalVotes: int | None = None  # as stated by the source; None when unknown or uncontested
    reporting: str = "official"  # official | unofficial


class Candidacy(Resource):
    """A candidate in a Contest. `person` is only set when we can tie them to a Person record."""
    resourceType: Literal["Candidacy"] = "Candidacy"
    table: ClassVar[str] = "candidacy"
    contest: Reference
    name: str
    person: Reference | None = None
    party: Reference | None = None  # Organization(type=party)
    partyLabel: str | None = None  # label as printed in the source (e.g. DEMOCRATIC-FARMER-LABOR)
    writeIn: bool = False


class ContestResult(Resource):
    """Votes for one Candidacy. Search-friendly fields (year, office, state, partyCode) are
    denormalised from the Contest. `outcome` is DERIVED by the loader (the source has no winner column)."""
    resourceType: Literal["ContestResult"] = "ContestResult"
    table: ClassVar[str] = "contest_result"
    contest: Reference
    candidacy: Reference
    year: int
    office: CodeableConcept
    state: Reference
    partyCode: str | None = None
    votes: int | None = None  # None for uncontested races (sources only hold a placeholder)
    voteShare: float | None = None  # votes / contest totalVotes, 0..1
    outcome: CodeableConcept | None = None
    outcomeDerived: bool = True


REGISTRY: dict[str, type[Resource]] = {
    cls.model_fields["resourceType"].default: cls
    for cls in (Person, Organization, Location, OfficeTerm, PartyAffiliation, Provenance,
                Election, Contest, Candidacy, ContestResult)
}
