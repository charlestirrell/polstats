import psycopg
import pytest

from polstats import codesystems as cs
from polstats.datatypes import HumanName, Identifier
from polstats.resources import Person
from polstats.store import Store, dsn

pytestmark = pytest.mark.db


@pytest.fixture
def store():
    try:
        conn = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    s = Store(conn)
    s.init_schema()
    yield s
    conn.close()


def test_versioning_and_identifier_search(store):
    p = store.create(Person(
        name=[HumanName(family="Test", given=["Pat"])],
        identifier=[Identifier(system=cs.ID_BIOGUIDE, value="T000TEST")],
    ))
    assert p.meta.versionId == "1"
    p.gender = "other"
    store.update(p)
    assert store.read("Person", p.id).meta.versionId == "2"
    assert [r.meta.versionId for r in store.history("Person", p.id)] == ["2", "1"]
    assert any(r.id == p.id for r in store.find_by_identifier("Person", cs.ID_BIOGUIDE, "T000TEST"))
