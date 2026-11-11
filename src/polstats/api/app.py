"""REST API with FHIR-inspired conventions (Bundles, search parameters, _include, _history).

    GET  /metadata
    GET  /{Type}?param=value&_count=&_offset=&_include=Type:field     -> Bundle (searchset)
    GET  /{Type}/{id}                                                  -> resource (+ETag)
    GET  /{Type}/{id}/_history                                         -> Bundle (history)
    POST /{Type}                                                       -> 201 + Location   (writes)
    PUT  /{Type}/{id}      (honours If-Match: W/"<version>")                                   (writes)

The API is READ-ONLY by default. POST and PUT answer 403 unless POLSTATS_ALLOW_WRITES=1 is set, because there is no
authentication: enable writes only on a machine you control, never on a network-facing deployment.

Local-development service: no authentication. Bind to localhost only.
Run:  PYTHONPATH=src uv run uvicorn polstats.api.app:app --port 8000
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ..resources import REGISTRY
from ..store import Store, dsn
from .search import INCLUDES, RESERVED, BadRequest, build_where, params_for

DEFAULT_COUNT, MAX_COUNT = 50, 500
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def outcome(status: int, severity: str, code: str, text: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        {"resourceType": "OperationOutcome",
         "issue": [{"severity": severity, "code": code, "diagnostics": text}]},
        status_code=status, headers=headers)


class WritesDisabled(Exception):
    pass


def writes_enabled() -> bool:
    """Read at request time (not import time) so it can be toggled per process or per test."""
    return os.environ.get("POLSTATS_ALLOW_WRITES", "").strip().lower() in ("1", "true", "yes", "on")


def require_writes() -> None:
    if not writes_enabled():
        raise WritesDisabled("this server is read-only; start it with POLSTATS_ALLOW_WRITES=1 to enable POST and PUT "
                             "(there is no authentication, so only do this on a machine you control)")


class RevalidatedStatic(StaticFiles):
    """Static files the browser must revalidate on every load (ETag makes that a cheap 304), so an upgraded UI is
    never served half-stale (new app.js running against an old, cached filters.js)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def get_conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(dsn()) as conn:
        yield conn


def _cls(rtype: str):
    cls = REGISTRY.get(rtype)
    if cls is None:
        raise LookupError(f"unknown resource type {rtype!r}; known: {sorted(REGISTRY)}")
    return cls


def _valid_id(rid: str) -> bool:
    try:
        uuid.UUID(rid)
        return True
    except ValueError:
        return False


def _etag(doc: dict) -> str:
    return f'W/"{doc["meta"]["versionId"]}"'


def create_app() -> FastAPI:
    app = FastAPI(title="polstats", version="0.1.0", description="Open data API for US national politics: members of Congress, terms, and election results")

    # Static data explorer. Mounted before the /{rtype} routes so "/ui" is never read as a resource type.
    app.mount("/ui", RevalidatedStatic(directory=WEB_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    @app.exception_handler(BadRequest)
    async def _bad(_: Request, exc: BadRequest):
        return outcome(400, "error", "invalid", str(exc))

    @app.exception_handler(WritesDisabled)
    async def _forbidden(_: Request, exc: WritesDisabled):
        return outcome(403, "error", "forbidden", str(exc))

    @app.exception_handler(LookupError)
    async def _missing(_: Request, exc: LookupError):
        return outcome(404, "error", "not-found", str(exc.args[0]) if exc.args else "not found")

    @app.get("/metadata")
    def metadata():
        return {
            "resourceType": "CapabilityStatement",
            "software": {"name": "polstats", "version": "0.1.0"},
            "rest": [{"mode": "server", "resource": [
                {"type": t,
                 "interaction": [{"code": c} for c in ("read", "vread-history", "search-type",
                                                       *(("create", "update") if writes_enabled() else ()))],
                 "searchParam": [{"name": n} for n in sorted(params_for(t))],
                 "searchInclude": [f"{t}:{f}" for f in INCLUDES.get(t, {})]}
                for t in sorted(REGISTRY)]}],
        }

    @app.get("/{rtype}")
    def search(rtype: str, request: Request, conn: psycopg.Connection = Depends(get_conn)):
        cls = _cls(rtype)
        query = [(k, v) for k, v in request.query_params.multi_items()]
        where, args = build_where(rtype, query)

        def _int(name: str, default: int, lo: int, hi: int) -> int:
            raw = request.query_params.get(name)
            if raw is None:
                return default
            try:
                n = int(raw)
            except ValueError:
                raise BadRequest(f"{name} must be an integer")
            if not lo <= n <= hi:
                raise BadRequest(f"{name} must be between {lo} and {hi}")
            return n

        count = _int("_count", DEFAULT_COUNT, 0, MAX_COUNT)
        offset = _int("_offset", 0, 0, 10**9)
        total = conn.execute(f"SELECT count(*) FROM {cls.table} r WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT r.resource FROM {cls.table} r WHERE {where} ORDER BY r.last_updated, r.id LIMIT %s OFFSET %s",
            [*args, count, offset]).fetchall()
        docs = [r[0] for r in rows]

        entries = [{"fullUrl": f"{rtype}/{d['id']}", "resource": d, "search": {"mode": "match"}} for d in docs]

        # _include=Type:field (repeatable)
        wanted: dict[str, set[str]] = {}
        for spec in request.query_params.getlist("_include"):
            src, _, field = spec.partition(":")
            if src != rtype or field not in INCLUDES.get(rtype, {}):
                raise BadRequest(f"unsupported _include {spec!r}; supported: "
                                 f"{[f'{rtype}:{f}' for f in INCLUDES.get(rtype, {})]}")
            target_type = INCLUDES[rtype][field]
            for d in docs:
                ref = (d.get(field) or {}).get("reference", "")
                if ref.startswith(target_type + "/") and _valid_id(ref.split("/", 1)[1]):
                    wanted.setdefault(target_type, set()).add(ref.split("/", 1)[1])
        for ttype, ids in wanted.items():
            inc = conn.execute(f"SELECT resource FROM {REGISTRY[ttype].table} WHERE id = ANY(%s::uuid[])",
                               [sorted(ids)]).fetchall()
            entries += [{"fullUrl": f"{ttype}/{r[0]['id']}", "resource": r[0], "search": {"mode": "include"}}
                        for r in inc]

        links = [{"relation": "self", "url": str(request.url)}]
        if offset + count < total:
            links.append({"relation": "next",
                          "url": str(request.url.include_query_params(_offset=offset + count, _count=count))})
        return {"resourceType": "Bundle", "type": "searchset", "total": total, "link": links, "entry": entries}

    @app.get("/{rtype}/{rid}")
    def read(rtype: str, rid: str, conn: psycopg.Connection = Depends(get_conn)):
        cls = _cls(rtype)
        row = conn.execute(f"SELECT resource FROM {cls.table} WHERE id = %s::uuid", [rid]).fetchone() \
            if _valid_id(rid) else None
        if row is None:
            raise LookupError(f"{rtype}/{rid} not found")
        doc = row[0]
        return JSONResponse(doc, headers={"ETag": _etag(doc), "Last-Modified": doc["meta"]["lastUpdated"]})

    @app.get("/{rtype}/{rid}/_history")
    def history(rtype: str, rid: str, conn: psycopg.Connection = Depends(get_conn)):
        cls = _cls(rtype)
        rows = conn.execute(
            f"SELECT resource FROM {cls.table}_history WHERE id = %s::uuid ORDER BY version_id DESC", [rid]
        ).fetchall() if _valid_id(rid) else []
        if not rows:
            raise LookupError(f"{rtype}/{rid} not found")
        return {"resourceType": "Bundle", "type": "history", "total": len(rows),
                "entry": [{"fullUrl": f"{rtype}/{rid}/_history/{r[0]['meta']['versionId']}", "resource": r[0]}
                          for r in rows]}

    def _parse(cls, rtype: str, body: dict):
        if not isinstance(body, dict) or body.get("resourceType") != rtype:
            raise BadRequest(f"body must be a JSON object with resourceType {rtype!r}")
        try:
            return cls.model_validate(body)
        except ValidationError as e:
            raise BadRequest("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))

    @app.post("/{rtype}", status_code=201)
    async def create(rtype: str, request: Request, _w: None = Depends(require_writes),
                     conn: psycopg.Connection = Depends(get_conn)):
        cls = _cls(rtype)
        try:
            body = await request.json()
        except ValueError:
            raise BadRequest("body is not valid JSON")
        res = _parse(cls, rtype, {k: v for k, v in body.items() if k not in ("id", "meta")} if isinstance(body, dict) else body)
        saved = Store(conn).create(res)
        doc = saved.model_dump(mode="json", exclude_none=True)
        return JSONResponse(doc, status_code=201,
                            headers={"Location": f"/{rtype}/{saved.id}/_history/1", "ETag": _etag(doc)})

    @app.put("/{rtype}/{rid}")
    async def update(rtype: str, rid: str, request: Request, _w: None = Depends(require_writes),
                     conn: psycopg.Connection = Depends(get_conn)):
        cls = _cls(rtype)
        try:
            body = await request.json()
        except ValueError:
            raise BadRequest("body is not valid JSON")
        res = _parse(cls, rtype, body)
        if res.id != rid:
            raise BadRequest("body id must match URL id")
        row = conn.execute(f"SELECT resource FROM {cls.table} WHERE id = %s::uuid", [rid]).fetchone() \
            if _valid_id(rid) else None
        if row is None:
            raise LookupError(f"{rtype}/{rid} not found (PUT does not create)")
        if_match = request.headers.get("If-Match")
        if if_match and if_match != _etag(row[0]):
            return outcome(412, "error", "conflict", f"version mismatch; current is {_etag(row[0])}")
        saved = Store(conn).update(res)
        doc = saved.model_dump(mode="json", exclude_none=True)
        return JSONResponse(doc, headers={"ETag": _etag(doc)})

    return app


app = create_app()
