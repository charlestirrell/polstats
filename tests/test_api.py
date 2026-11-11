import psycopg
import pytest
from fastapi.testclient import TestClient

from polstats import codesystems as cs
from polstats.api.app import create_app
from polstats.loaders.congress_legislators import Loader
from polstats.store import Store, dsn

from test_loader import RECORD

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client():
    try:
        conn = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running")
    store = Store(conn)
    store.init_schema()
    Loader(store, "apitest", "2026-01-01").load_record(RECORD)
    conn.close()
    return TestClient(create_app())


@pytest.fixture(scope="module")
def pid(client):
    b = client.get("/Person", params={"identifier": f"{cs.ID_BIOGUIDE}|Z000LOAD"}).json()
    assert b["total"] == 1
    return b["entry"][0]["resource"]["id"]


def test_read_and_etag(client, pid):
    r = client.get(f"/Person/{pid}")
    assert r.status_code == 200
    assert r.json()["resourceType"] == "Person"
    assert r.headers["etag"].startswith('W/"')


def test_not_found_is_operation_outcome(client):
    for url in ("/Person/00000000-0000-0000-0000-000000000000", "/Person/not-a-uuid", "/Bogus/1"):
        r = client.get(url)
        assert r.status_code == 404 and r.json()["resourceType"] == "OperationOutcome"


def test_person_search(client):
    assert client.get("/Person", params={"name": "loader"}).json()["total"] >= 1
    assert client.get("/Person", params={"family": "Loader", "gender": "female"}).json()["total"] >= 1
    assert client.get("/Person", params={"birthdate": "ge1970-02-03", "family": "Loader"}).json()["total"] >= 1
    assert client.get("/Person", params={"birthdate": "lt1970-02-03", "family": "Loader"}).json()["total"] == 0


def test_office_term_search_by_state_chamber_date(client, pid):
    base = {"person": f"Person/{pid}"}
    assert client.get("/OfficeTerm", params=base).json()["total"] == 4
    assert client.get("/OfficeTerm", params={**base, "chamber": "senate"}).json()["total"] == 2
    assert client.get("/OfficeTerm", params={**base, "state": "tx", "active-on": "2020-01-01"}).json()["total"] == 1
    assert client.get("/OfficeTerm", params={**base, "state": "CA"}).json()["total"] == 0
    assert client.get("/OfficeTerm", params={**base, "district": "TX-12"}).json()["total"] == 2
    assert client.get("/OfficeTerm", params={**base, "class": "2"}).json()["total"] == 2


def test_party_filter_respects_date(client, pid):
    base = {"person": f"Person/{pid}"}
    assert client.get("/OfficeTerm", params={**base, "party": "R", "active-on": "2020-01-01"}).json()["total"] == 1
    assert client.get("/OfficeTerm", params={**base, "party": "D", "active-on": "2020-01-01"}).json()["total"] == 0
    assert client.get("/OfficeTerm", params={**base, "party": "D", "active-on": "2014-01-01"}).json()["total"] == 1


def test_include_and_paging(client, pid):
    r = client.get("/OfficeTerm", params={"person": pid, "_count": 1, "_include": "OfficeTerm:person"}).json()
    modes = [e["search"]["mode"] for e in r["entry"]]
    assert r["total"] == 4 and modes.count("match") == 1 and modes.count("include") == 1
    nxt = [l for l in r["link"] if l["relation"] == "next"]
    assert nxt
    page2 = client.get(nxt[0]["url"]).json()
    assert page2["entry"][0]["resource"]["id"] != r["entry"][0]["resource"]["id"]


def test_bad_requests(client):
    for params in ({"bogus": "1"}, {"birthdate": "yesterday"}, {"_count": "-1"}, {"_id": "xyz"},
                   {"_include": "Person:nope"}):
        r = client.get("/Person", params=params)
        assert r.status_code == 400 and r.json()["resourceType"] == "OperationOutcome", params


def test_create_update_history_and_if_match(client):
    body = {"resourceType": "Organization", "name": "API Test Caucus",
            "type": cs.concept(cs.ORG_TYPE, "caucus").model_dump(exclude_none=True)}
    c = client.post("/Organization", json=body)
    assert c.status_code == 201
    doc = c.json()
    oid = doc["id"]

    doc["name"] = "API Test Caucus 2"
    stale = client.put(f"/Organization/{oid}", json=doc, headers={"If-Match": 'W/"99"'})
    assert stale.status_code == 412
    ok = client.put(f"/Organization/{oid}", json=doc, headers={"If-Match": c.headers["etag"]})
    assert ok.status_code == 200 and ok.json()["meta"]["versionId"] == "2"

    h = client.get(f"/Organization/{oid}/_history").json()
    assert [e["resource"]["meta"]["versionId"] for e in h["entry"]] == ["2", "1"]

    assert client.put("/Organization/00000000-0000-0000-0000-000000000000",
                      json={**doc, "id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_create_validates(client):
    assert client.post("/Organization", json={"resourceType": "Organization"}).status_code == 400  # name required
    assert client.post("/Organization", json={"resourceType": "Person", "name": "x"}).status_code == 400
    assert client.post("/Organization", json={"resourceType": "Organization", "name": "x", "bogus": 1}).status_code == 400


def test_election_resources_search(client, tmp_path):
    from polstats.loaders.medsl import load_file
    from test_medsl import FIXTURE, tsv
    f = tmp_path / "senate.tab"
    tsv(f, FIXTURE)
    conn = psycopg.connect(dsn())
    load_file(f, conn)
    conn.close()

    assert client.get("/Election", params={"year": 2022, "kind": "general"}).json()["total"] == 1
    assert client.get("/Election", params={"year": "x"}).status_code == 400

    c = client.get("/Contest", params={"year": 2022, "state": "tx", "office": "senator"}).json()
    assert c["total"] == 1 and c["entry"][0]["resource"]["totalVotes"] == 1005
    assert client.get("/Contest", params={"year": 2020, "special": "false", "state": "GA"}).json()["total"] == 1
    assert client.get("/Contest", params={"special": "maybe"}).status_code == 400

    won = client.get("/ContestResult", params={"year": 2022, "state": "TX", "outcome": "won", "office": "senator",
                                               "_include": "ContestResult:candidacy"}).json()
    match = [e for e in won["entry"] if e["search"]["mode"] == "match"]
    inc = [e for e in won["entry"] if e["search"]["mode"] == "include"]
    assert len(match) == 1 and match[0]["resource"]["votes"] == 620 and inc[0]["resource"]["name"] == "ZED LOADER"
    assert client.get("/ContestResult", params={"year": 2022, "state": "TX", "party": "D", "office": "senator"}).json()["total"] == 1

    zed = inc[0]["resource"]["person"]["reference"]
    cand = client.get("/Candidacy", params={"person": zed, "_include": "Candidacy:contest"}).json()
    assert any(e["resource"]["resourceType"] == "Contest" for e in cand["entry"])
    assert client.get("/Candidacy", params={"name": "jane roe", "party": "D"}).json()["total"] >= 1

    prov = client.get("/Provenance", params={"source": "MIT Election Data and Science Lab", "_count": 1}).json()
    assert prov["total"] >= 1 and prov["entry"][0]["resource"]["license"] == "CC0-1.0"
    assert client.get("/Provenance", params={"source": "no such source", "_count": 1}).json()["total"] == 0


def test_ui_is_served_and_does_not_shadow_resources(client):
    r = client.get("/ui/")
    assert r.status_code == 200 and "Elections Explorer" in r.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/filters.js").status_code == 200 and client.get("/ui/style.css").status_code == 200
    for path in ("/ui/", "/ui/app.js", "/ui/filters.js", "/ui/style.css"):   # never serve a half-stale UI after an upgrade
        assert client.get(path).headers["cache-control"] == "no-cache", path
    assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
    assert client.get("/Person?_count=0").json()["resourceType"] == "Bundle"


def test_api_is_read_only_unless_writes_are_enabled(client, monkeypatch):
    monkeypatch.delenv("POLSTATS_ALLOW_WRITES", raising=False)
    body = {"resourceType": "Organization", "name": "Should Not Exist"}
    for r in (client.post("/Organization", json=body),
              client.put("/Organization/00000000-0000-0000-0000-000000000000", json=body)):
        assert r.status_code == 403 and r.json()["resourceType"] == "OperationOutcome"
        assert "POLSTATS_ALLOW_WRITES" in r.json()["issue"][0]["diagnostics"]
    assert client.get("/Organization", params={"name": "Should Not Exist"}).json()["total"] == 0
    assert client.get("/Person?_count=0").status_code == 200  # reads are unaffected
    interactions = [i["code"] for i in client.get("/metadata").json()["rest"][0]["resource"][0]["interaction"]]
    assert "create" not in interactions and "update" not in interactions and "read" in interactions
    for falsy in ("0", "false", "", "no"):
        monkeypatch.setenv("POLSTATS_ALLOW_WRITES", falsy)
        assert client.post("/Organization", json=body).status_code == 403, falsy
    monkeypatch.setenv("POLSTATS_ALLOW_WRITES", "true")
    assert client.post("/Organization", json={"resourceType": "Organization", "name": "Writes On"}).status_code == 201


def test_metadata(client):
    m = client.get("/metadata").json()
    types = {r["type"]: r for r in m["rest"][0]["resource"]}
    assert "party" in [p["name"] for p in types["OfficeTerm"]["searchParam"]]
