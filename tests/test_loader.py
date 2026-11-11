from datetime import date

import psycopg
import pytest

from polstats import codesystems as cs
from polstats.loaders.congress_legislators import Loader, coalesce, office_code, party_code
from polstats.store import Store, dsn

RECORD = {
    "id": {"bioguide": "Z000LOAD", "fec": ["S0XX00001", "H0XX00002"], "icpsr": 99999, "lis": "S999"},
    "name": {"first": "Zed", "middle": "Q", "last": "Loader", "official_full": "Zed Q. Loader"},
    "bio": {"birthday": "1970-02-03", "gender": "F"},
    "terms": [
        {"type": "rep", "start": "2013-01-03", "end": "2015-01-03", "state": "TX", "district": 12, "party": "Democrat"},
        {"type": "rep", "start": "2015-01-03", "end": "2017-01-03", "state": "TX", "district": 12, "party": "Democrat"},
        {"type": "sen", "start": "2017-01-03", "end": "2023-01-03", "state": "TX", "class": 2, "party": "Republican"},
        {"type": "sen", "start": "2023-01-03", "end": "2029-01-03", "state": "TX", "class": 2,
         "party": "Republican", "caucus": "Republican"},
    ],
}


def test_helpers():
    assert party_code("Democrat") == "D"
    assert party_code("Some New Party") == "some-new-party"
    assert office_code("rep", "PR") == "resident-commissioner"
    assert office_code("rep", "DC") == "delegate"
    assert office_code("rep", "TX") == "representative"
    merged = coalesce([("D", "Democrat", date(2013, 1, 3), date(2015, 1, 3)),
                       ("D", "Democrat", date(2015, 1, 3), date(2017, 1, 3)),
                       ("R", "Republican", date(2017, 1, 3), date(2023, 1, 3))])
    assert [(m[0], m[2], m[3]) for m in merged] == [
        ("D", date(2013, 1, 3), date(2017, 1, 3)), ("R", date(2017, 1, 3), date(2023, 1, 3))]


@pytest.mark.db
def test_load_is_idempotent():
    try:
        conn = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running")
    store = Store(conn)
    store.init_schema()

    Loader(store, "testver", "2026-01-01").load_record(RECORD)
    person = store.find_by_identifier("Person", cs.ID_BIOGUIDE, "Z000LOAD")[0]
    assert len(person.identifier) == 5  # bioguide, 2x fec, icpsr, lis

    ref = f"Person/{person.id}"
    terms = conn.execute("SELECT count(*) FROM office_term WHERE resource->'person'->>'reference' = %s", (ref,)).fetchone()[0]
    affs = conn.execute("SELECT count(*) FROM party_affiliation WHERE resource->'person'->>'reference' = %s", (ref,)).fetchone()[0]
    assert (terms, affs) == (4, 2)  # D span coalesced, R span coalesced

    Loader(store, "testver", "2026-01-01").load_record(RECORD)  # second run: no new versions
    assert store.read("Person", person.id).meta.versionId == "1"
    assert conn.execute("SELECT max(version_id) FROM office_term WHERE resource->'person'->>'reference' = %s", (ref,)).fetchone()[0] == 1
    conn.close()
