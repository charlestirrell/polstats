"""Reusable datatypes (Identifier, Reference, Period, CodeableConcept, ...), modelled on HL7 FHIR conventions.

The field descriptions below appear in the API documentation (/docs, /redoc), so they are written for API users."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coding(Element):
    """One coded value from a vocabulary (a `system` URN such as `urn:polstats:party`)."""
    system: str | None = Field(None, description="Vocabulary the code belongs to, e.g. `urn:polstats:chamber`.")
    code: str | None = Field(None, description="The code itself, e.g. `senate`, `D`, `general`. Search parameters match on this.")
    display: str | None = Field(None, description="Human-readable label for the code, e.g. `Senate`.")


class CodeableConcept(Element):
    """A coded value (one or more `Coding`s) with optional free text."""
    coding: list[Coding] = Field(default_factory=list, description="The code(s) for this concept; normally exactly one.")
    text: str | None = Field(None, description="Free-text form of the concept.")


class Period(Element):
    """A span of dates. Everything time-bound in polstats (terms, party membership) carries one."""
    start: date | None = Field(None, description="First day, inclusive (`YYYY-MM-DD`). Absent means unknown or unbounded.")
    end: date | None = Field(None, description="Last day, inclusive (`YYYY-MM-DD`). Absent means open-ended.")

    def contains(self, d: date) -> bool:
        return (self.start is None or self.start <= d) and (self.end is None or d <= self.end)


class Identifier(Element):
    """An identifier for the thing in some external or internal system. Used for cross-reference IDs and natural keys."""
    system: str = Field(description="Namespace of the identifier, e.g. `https://bioguide.congress.gov`.")
    value: str = Field(description="The identifier within that namespace, e.g. a Bioguide ID.")


class Reference(Element):
    """A link to another resource."""
    reference: str = Field(description="Relative reference in the form `Type/id`, e.g. `Person/0b1f6c2e-5d4a-4c1e-9a77-3f2d8e6b1a40`.")
    display: str | None = Field(None, description="Optional human-readable name of the target; informational only.")


class HumanName(Element):
    """A person's name."""
    use: str | None = Field(None, description="Kind of name: `official`, `nickname`, `maiden`, ...")
    family: str | None = Field(None, description="Family (last) name.")
    given: list[str] = Field(default_factory=list, description="Given names in order: first, then middle.")
    prefix: list[str] = Field(default_factory=list, description="Name prefixes, e.g. `Dr.`.")
    suffix: list[str] = Field(default_factory=list, description="Name suffixes, e.g. `Jr.`.")
    text: str | None = Field(None, description="The full name as it should be displayed.")


class Extension(Element):
    """An escape hatch for data that has no field yet. `url` names it; `value` can be any JSON value."""
    url: str = Field(description="Names the extension, e.g. `urn:polstats:caucus`.")
    value: Any = Field(None, description="The value (any JSON type).")


class Meta(Element):
    """Server-managed bookkeeping. Ignored on create; the server assigns it."""
    versionId: str | None = Field(None, description="Version number of this resource, starting at `1` and incrementing on every update.")
    lastUpdated: datetime | None = Field(None, description="When this version was written (UTC).")
