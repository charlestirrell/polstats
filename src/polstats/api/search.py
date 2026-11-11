"""Search parameter definitions.

Each parameter maps a query-string value to a SQL fragment over the outer table aliased `r`
(values are always bound parameters; table names come from the trusted REGISTRY).
A builder is `fn(value, ctx) -> (sql, params)` where ctx holds all query params of the request.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Callable

from psycopg.types.json import Jsonb

from .. import codesystems as cs
from ..loaders.congress_legislators import SYS_DISTRICT

RESERVED = {"_count", "_offset", "_include"}
Builder = Callable[[str, dict[str, str]], tuple[str, list]]


class BadRequest(Exception):
    pass


def _like(v: str) -> str:
    esc = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _date(v: str) -> date:
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise BadRequest(f"invalid date {v!r}, expected YYYY-MM-DD")


def _ref(v: str, rtype: str) -> str:
    return v if "/" in v else f"{rtype}/{v}"


# --- builders ----------------------------------------------------------------
def p_id(v, ctx):
    try:
        uuid.UUID(v)
    except ValueError:
        raise BadRequest(f"invalid _id {v!r}")
    return "r.id = %s::uuid", [v]


def p_identifier(v, ctx):
    if "|" in v:
        system, value = v.split("|", 1)
        probe = {"system": system, "value": value} if system else {"value": value}
    else:
        probe = {"value": v}
    return "r.resource -> 'identifier' @> %s", [Jsonb([probe])]


def text_eq(path: str) -> Builder:
    return lambda v, ctx: (f"r.resource ->> '{path}' = %s", [v])


def date_cmp(path: str) -> Builder:
    ops = {"eq": "=", "ge": ">=", "le": "<=", "gt": ">", "lt": "<"}

    def build(v, ctx):
        prefix = v[:2] if v[:2] in ops else "eq"
        raw = v[2:] if v[:2] in ops else v
        return f"(r.resource ->> '{path}')::date {ops[prefix]} %s", [_date(raw)]
    return build


def person_name(v, ctx):
    return ("EXISTS (SELECT 1 FROM jsonb_array_elements(r.resource -> 'name') n "
            "WHERE n ->> 'text' ILIKE %s OR n ->> 'family' ILIKE %s)", [_like(v), _like(v)])


def person_family(v, ctx):
    return ("EXISTS (SELECT 1 FROM jsonb_array_elements(r.resource -> 'name') n "
            "WHERE n ->> 'family' ILIKE %s)", [_like(v)])


def name_like(v, ctx):
    return "r.resource ->> 'name' ILIKE %s", [_like(v)]


def code_in(field: str) -> Builder:
    return lambda v, ctx: (f"r.resource -> '{field}' -> 'coding' @> %s", [Jsonb([{"code": v}])])


def ref_eq(field: str, rtype: str) -> Builder:
    return lambda v, ctx: (f"r.resource -> '{field}' ->> 'reference' = %s", [_ref(v, rtype)])


def location_by(field: str, system: str, upper: bool = True) -> Builder:
    def build(v, ctx):
        val = v.upper() if upper else v
        return (f"r.resource -> '{field}' ->> 'reference' IN "
                f"(SELECT 'Location/' || l.id FROM location l WHERE l.resource -> 'identifier' @> %s)",
                [Jsonb([{"system": system, "value": val}])])
    return build


def senate_class(v, ctx):
    try:
        return "(r.resource ->> 'senateClass')::int = %s", [int(v)]
    except ValueError:
        raise BadRequest(f"invalid class {v!r}")


def active_on_sql(alias: str, d: date) -> tuple[str, list]:
    return (f"(COALESCE(({alias}.resource -> 'period' ->> 'start')::date, '-infinity') <= %s "
            f"AND COALESCE(({alias}.resource -> 'period' ->> 'end')::date, 'infinity') >= %s)", [d, d])


def active_on(v, ctx):
    return active_on_sql("r", _date(v))


def term_party(v, ctx):
    """OfficeTerm?party=R : holder's party affiliation (valid on `active-on`, default: the term's own
    active date if given, else today)."""
    on = _date(ctx["active-on"]) if "active-on" in ctx else date.today()
    act, act_p = active_on_sql("a", on)
    return (f"EXISTS (SELECT 1 FROM party_affiliation a JOIN organization o "
            f"ON 'Organization/' || o.id = a.resource -> 'party' ->> 'reference' "
            f"WHERE a.resource -> 'person' ->> 'reference' = r.resource -> 'person' ->> 'reference' "
            f"AND o.resource -> 'identifier' @> %s AND {act})",
            [Jsonb([{"system": cs.PARTY, "value": v}]), *act_p])


def affil_party(v, ctx):
    return (f"r.resource -> 'party' ->> 'reference' IN "
            f"(SELECT 'Organization/' || o.id FROM organization o WHERE o.resource -> 'identifier' @> %s)",
            [Jsonb([{"system": cs.PARTY, "value": v}])])


def target(v, ctx):
    return "r.resource -> 'target' @> %s", [Jsonb([{"reference": v}])]


def int_eq(path: str) -> Builder:
    def build(v, ctx):
        try:
            return f"(r.resource ->> '{path}')::int = %s", [int(v)]
        except ValueError:
            raise BadRequest(f"invalid integer {v!r} for {path}")
    return build


def bool_eq(path: str) -> Builder:
    def build(v, ctx):
        if v.lower() not in ("true", "false"):
            raise BadRequest(f"{path} must be true or false")
        return f"COALESCE((r.resource ->> '{path}')::boolean, false) = %s", [v.lower() == "true"]
    return build


def contest_year(v, ctx):
    try:
        year = str(int(v))
    except ValueError:
        raise BadRequest(f"invalid year {v!r}")
    return ("r.resource -> 'election' ->> 'reference' IN "
            "(SELECT 'Election/' || e.id FROM election e WHERE e.resource ->> 'year' = %s)", [year])


def result_party(v, ctx):
    return "r.resource ->> 'partyCode' = %s", [v]


COMMON: dict[str, Builder] = {"_id": p_id, "identifier": p_identifier}

PARAMS: dict[str, dict[str, Builder]] = {
    "Person": {
        "name": person_name, "family": person_family,
        "gender": text_eq("gender"), "birthdate": date_cmp("birthDate"),
    },
    "Organization": {"name": name_like, "type": code_in("type"), "partof": ref_eq("partOf", "Organization")},
    "Location": {"name": name_like, "type": code_in("type"), "partof": ref_eq("partOf", "Location")},
    "OfficeTerm": {
        "person": ref_eq("person", "Person"),
        "state": location_by("state", cs.ID_USPS),
        "district": location_by("district", SYS_DISTRICT),
        "chamber": code_in("chamber"), "office": code_in("office"),
        "class": senate_class, "active-on": active_on, "party": term_party,
    },
    "PartyAffiliation": {
        "person": ref_eq("person", "Person"), "party": affil_party, "active-on": active_on,
    },
    "Provenance": {"target": target},
    "Election": {"year": int_eq("year"), "kind": code_in("kind")},
    "Contest": {
        "election": ref_eq("election", "Election"), "year": contest_year,
        "office": code_in("office"), "state": location_by("state", cs.ID_USPS),
        "district": location_by("district", SYS_DISTRICT), "special": bool_eq("special"),
    },
    "Candidacy": {
        "contest": ref_eq("contest", "Contest"), "person": ref_eq("person", "Person"),
        "name": name_like, "party": affil_party,
    },
    "ContestResult": {
        "contest": ref_eq("contest", "Contest"), "candidacy": ref_eq("candidacy", "Candidacy"),
        "year": int_eq("year"), "office": code_in("office"), "state": location_by("state", cs.ID_USPS),
        "party": result_party, "outcome": code_in("outcome"),
    },
}

# Reference fields available to _include=<Type>:<field>  ->  target resource type
INCLUDES: dict[str, dict[str, str]] = {
    "OfficeTerm": {"person": "Person", "state": "Location", "district": "Location"},
    "PartyAffiliation": {"person": "Person", "party": "Organization"},
    "Organization": {"partOf": "Organization"},
    "Location": {"partOf": "Location"},
    "Contest": {"election": "Election", "state": "Location", "district": "Location"},
    "Candidacy": {"contest": "Contest", "person": "Person", "party": "Organization"},
    "ContestResult": {"contest": "Contest", "candidacy": "Candidacy", "state": "Location"},
}


def params_for(rtype: str) -> dict[str, Builder]:
    return {**COMMON, **PARAMS.get(rtype, {})}


def build_where(rtype: str, query: list[tuple[str, str]]) -> tuple[str, list]:
    known = params_for(rtype)
    ctx = dict(query)
    clauses, args = [], []
    for name, value in query:
        if name in RESERVED:
            continue
        if name not in known:
            raise BadRequest(f"unknown search parameter {name!r} for {rtype}; "
                             f"supported: {sorted(known)}")
        sql, params = known[name](value, ctx)
        clauses.append(sql)
        args.extend(params)
    return (" AND ".join(clauses) or "TRUE"), args
