"""Run DB tests against a separate database so fixtures never pollute dev data."""
import os

import psycopg
from psycopg import sql

from polstats.store import DEFAULT_DSN

TEST_DB = "polstats_test"


def _setup() -> None:
    base = os.environ.get("POLSTATS_DSN", DEFAULT_DSN)
    try:
        with psycopg.connect(base, autocommit=True, connect_timeout=3) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    except psycopg.OperationalError:
        return  # DB tests will skip themselves
    info = psycopg.conninfo.conninfo_to_dict(base)
    info["dbname"] = TEST_DB
    os.environ["POLSTATS_DSN"] = psycopg.conninfo.make_conninfo(**info)


_setup()
