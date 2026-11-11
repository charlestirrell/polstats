"""Postgres JSONB document store with per-resource versioning and history."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from .datatypes import Meta
from .resources import REGISTRY, Resource

DEFAULT_DSN = "postgresql://polstats:polstats_dev@localhost:5432/polstats"


def dsn() -> str:
    return os.environ.get("POLSTATS_DSN", DEFAULT_DSN)


def schema_sql() -> str:
    parts = []
    for cls in REGISTRY.values():
        t = cls.table
        parts.append(f"""
CREATE TABLE IF NOT EXISTS {t} (
    id           uuid PRIMARY KEY,
    version_id   int NOT NULL,
    last_updated timestamptz NOT NULL,
    resource     jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS {t}_history (
    id           uuid NOT NULL,
    version_id   int NOT NULL,
    last_updated timestamptz NOT NULL,
    resource     jsonb NOT NULL,
    PRIMARY KEY (id, version_id)
);
CREATE INDEX IF NOT EXISTS {t}_identifier_gin ON {t}
    USING gin ((resource -> 'identifier') jsonb_path_ops);""")
    return "\n".join(parts)


class Store:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def init_schema(self) -> None:
        self.conn.execute(schema_sql())
        self.conn.commit()

    @staticmethod
    def _doc(resource: Resource, rid: str, version: int, now: datetime) -> dict:
        resource.id = rid
        resource.meta = Meta(versionId=str(version), lastUpdated=now)
        return resource.model_dump(mode="json", exclude_none=True)

    def create(self, resource: Resource) -> Resource:
        return self._write(resource, str(uuid.uuid4()), 1)

    def update(self, resource: Resource) -> Resource:
        if not resource.id:
            raise ValueError("update requires resource.id")
        row = self.conn.execute(
            f"SELECT version_id FROM {resource.table} WHERE id = %s FOR UPDATE", (resource.id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"{resource.resourceType}/{resource.id}")
        return self._write(resource, resource.id, row[0] + 1)

    def _write(self, resource: Resource, rid: str, version: int) -> Resource:
        now = datetime.now(timezone.utc)
        doc = self._doc(resource, rid, version, now)
        t = resource.table
        self.conn.execute(
            f"""INSERT INTO {t} (id, version_id, last_updated, resource)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET version_id = EXCLUDED.version_id,
                    last_updated = EXCLUDED.last_updated, resource = EXCLUDED.resource""",
            (rid, version, now, Jsonb(doc)),
        )
        self.conn.execute(
            f"INSERT INTO {t}_history (id, version_id, last_updated, resource) VALUES (%s, %s, %s, %s)",
            (rid, version, now, Jsonb(doc)),
        )
        self.conn.commit()
        return resource

    def read(self, resource_type: str, rid: str) -> Resource | None:
        cls = REGISTRY[resource_type]
        row = self.conn.execute(f"SELECT resource FROM {cls.table} WHERE id = %s", (rid,)).fetchone()
        return cls.model_validate(row[0]) if row else None

    def history(self, resource_type: str, rid: str) -> list[Resource]:
        cls = REGISTRY[resource_type]
        rows = self.conn.execute(
            f"SELECT resource FROM {cls.table}_history WHERE id = %s ORDER BY version_id DESC", (rid,)
        ).fetchall()
        return [cls.model_validate(r[0]) for r in rows]

    def upsert(self, resource: Resource, system: str, value: str) -> Resource:
        """Create or update by natural key; no new version if content is unchanged."""
        if not any(i.system == system and i.value == value for i in resource.identifier):
            raise ValueError("resource must carry the natural-key identifier")
        found = self.find_by_identifier(resource.resourceType, system, value)
        if not found:
            return self.create(resource)
        existing = found[0]
        skip = {"id", "meta"}
        new = resource.model_dump(mode="json", exclude_none=True, exclude=skip)
        if existing.model_dump(mode="json", exclude_none=True, exclude=skip) == new:
            return existing
        resource.id = existing.id
        return self.update(resource)

    def find_by_identifier(self, resource_type: str, system: str, value: str) -> list[Resource]:
        cls = REGISTRY[resource_type]
        rows = self.conn.execute(
            f"SELECT resource FROM {cls.table} WHERE resource -> 'identifier' @> %s",
            (Jsonb([{"system": system, "value": value}]),),
        ).fetchall()
        return [cls.model_validate(r[0]) for r in rows]
