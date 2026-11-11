from datetime import date

import pytest

from polstats import codesystems as cs
from polstats.datatypes import HumanName, Identifier, Period, Reference
from polstats.resources import REGISTRY, OfficeTerm, Person


def test_period_contains():
    p = Period(start=date(2025, 1, 3), end=None)
    assert p.contains(date(2026, 11, 10))
    assert not p.contains(date(2024, 1, 1))


def test_concept_validates_code():
    assert cs.concept(cs.CHAMBER, "senate").coding[0].display == "Senate"
    with pytest.raises(ValueError):
        cs.concept(cs.CHAMBER, "bogus")


def test_office_term_roundtrip():
    t = OfficeTerm(
        person=Reference(reference="Person/1"),
        office=cs.concept(cs.OFFICE, "senator"),
        chamber=cs.concept(cs.CHAMBER, "senate"),
        state=Reference(reference="Location/tx"),
        senateClass=1,
        period=Period(start=date(2025, 1, 3)),
    )
    again = OfficeTerm.model_validate(t.model_dump(mode="json", exclude_none=True))
    assert again == t


def test_extra_fields_rejected():
    with pytest.raises(Exception):
        Person(name=[HumanName(family="X")], bogus=1)


def test_registry_complete():
    assert set(REGISTRY) == {"Person", "Organization", "Location", "OfficeTerm", "PartyAffiliation",
                             "Provenance", "Election", "Contest", "Candidacy", "ContestResult"}
