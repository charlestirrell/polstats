"""Reusable datatypes (Identifier, Reference, Period, CodeableConcept, ...), modelled on HL7 FHIR conventions."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coding(Element):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class CodeableConcept(Element):
    coding: list[Coding] = []
    text: str | None = None


class Period(Element):
    start: date | None = None
    end: date | None = None  # None = open-ended

    def contains(self, d: date) -> bool:
        return (self.start is None or self.start <= d) and (self.end is None or d <= self.end)


class Identifier(Element):
    system: str
    value: str


class Reference(Element):
    reference: str  # e.g. "Person/<uuid>"
    display: str | None = None


class HumanName(Element):
    use: str | None = None  # official | nickname | maiden ...
    family: str | None = None
    given: list[str] = []
    prefix: list[str] = []
    suffix: list[str] = []
    text: str | None = None


class Extension(Element):
    url: str
    value: Any = None


class Meta(Element):
    versionId: str | None = None
    lastUpdated: datetime | None = None
