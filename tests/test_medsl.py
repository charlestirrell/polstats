from datetime import date

import psycopg
import pytest

from polstats import codesystems as cs
from polstats.loaders.congress_legislators import Loader as CongressLoader
from polstats.loaders.medsl import (SYS_CONTEST, Cand, derive_outcomes, district_label, district_number,
                                    effective_stage, kind_of, load_file, norm, party_code,
                                    regular_election_day, stage_norm)
from polstats.store import Store, dsn

from test_loader import RECORD


def test_helpers():
    assert norm("Wyche Fowler, Jr.") == "WYCHE FOWLER JR"
    assert norm("José  Núñez") == "JOSE NUNEZ"
    assert party_code("DEMOCRATIC-FARMER-LABOR") == "D" and party_code("DFL") == "D"
    assert party_code("REPUBLICAN") == "R" and party_code("INDEPENDENT") == "I"
    assert party_code("WORKING FAMILIES", "OTHER") == "other" and party_code("", "DEMOCRAT") == "D"
    assert [stage_norm(s) for s in ("gen", "GEN", "pre", "runoff", "GEN RUNOFF", "pri")] == \
        ["gen", "gen", "gen", "runoff", "runoff", "pri"]
    assert kind_of("GEN", False) == "general" and kind_of("gen", True) == "special"
    assert kind_of("runoff", True) == "runoff" and kind_of("pri", False) == "primary"
    assert regular_election_day(2024) == date(2024, 11, 5)
    assert regular_election_day(2022) == date(2022, 11, 8)
    assert regular_election_day(2020) == date(2020, 11, 3)
    assert regular_election_day(2021) is None
    assert district_number("statewide") is None and district_number("000") == 0 and district_number("012") == 12
    assert district_label("VT", "0") == "VT-AL" and district_label("TX", "012") == "TX-12"
    assert district_label("TX", "statewide") is None
    # Georgia-style: 'gen' that follows a 'pre' round is the runoff
    assert effective_stage("gen", True) == "runoff" and effective_stage("gen", False) == "gen"
    assert effective_stage("pre", True) == "pre" and effective_stage("GEN", True) == "runoff"


def _cands(*triples):
    return [Cand(n, False, v, [(p, v, "")]) for n, v, p in triples]


def test_outcome_rules():
    c = _cands(("A", 520, "DEMOCRAT"), ("B", 470, "REPUBLICAN"), ("OTHER", 10, "OTHER"))
    derive_outcomes("GEN", c, 1000, None)
    assert [x.outcome for x in c] == ["won", "lost", "lost"]

    c = _cands(("A", 490, "R"), ("B", 480, "D"), ("C", 30, "L"))  # runoff follows
    derive_outcomes("GEN", c, 1000, {"A", "B"})
    assert [x.outcome for x in c] == ["advanced-to-runoff", "advanced-to-runoff", "lost"]

    c = _cands(("A", 450, "D"), ("B", 440, "R"), ("C", 110, "L"))  # pre-runoff, runoff not in data
    derive_outcomes("pre", c, 1000, None)
    assert [x.outcome for x in c] == ["advanced-to-runoff", "advanced-to-runoff", "lost"]

    c = _cands(("A", 505, "R"), ("B", 515, "D"))
    derive_outcomes("runoff", c, 1020, None)
    assert [x.outcome for x in c] == ["lost", "won"]

    c = _cands(("A", 500, "D"))
    derive_outcomes("pri", c, 500, None)
    assert c[0].outcome is None  # primaries get no derived outcome


HEADER = ["year", "state", "state_po", "state_fips", "state_cen", "state_ic", "office", "district", "stage",
          "special", "candidate", "party_detailed", "writein", "mode", "candidatevotes", "totalvotes",
          "unofficial", "version", "party_simplified"]


def tsv(path, rows):
    lines = ["\t".join(HEADER)]
    for r in rows:
        d = dict(state_fips="1", state_cen="1", state_ic="1", office="US SENATE", district="statewide",
                 special="False", writein="False", mode="TOTAL", unofficial="False", version="1",
                 party_simplified="OTHER", state="X")
        d.update(r)
        lines.append("\t".join(f'"{d[h]}"' if h in ("state", "candidate", "party_detailed", "office") else str(d[h]) for h in HEADER))
    path.write_text("\n".join(lines) + "\n")


FIXTURE = [
    # TX 2022: fusion candidate (two lines), write-in bucket, linked winner
    dict(year=2022, state_po="TX", stage="GEN", candidate="ZED LOADER", party_detailed="REPUBLICAN", candidatevotes="600.0", totalvotes="1005.0"),
    dict(year=2022, state_po="TX", stage="GEN", candidate="ZED LOADER", party_detailed="CONSERVATIVE", candidatevotes="20.0", totalvotes="1005.0"),
    dict(year=2022, state_po="TX", stage="GEN", candidate="JANE ROE", party_detailed="DEMOCRAT", candidatevotes="380.0", totalvotes="1005.0"),
    dict(year=2022, state_po="TX", stage="GEN", candidate="", party_detailed="", writein="True", candidatevotes="5.0", totalvotes="1005.0"),
    # GA 2020 general + 2021 runoff
    dict(year=2020, state_po="GA", stage="gen", candidate="DAVID PERDUE", party_detailed="REPUBLICAN", candidatevotes="490.0", totalvotes="1000.0"),
    dict(year=2020, state_po="GA", stage="gen", candidate="JON OSSOFF", party_detailed="DEMOCRAT", candidatevotes="480.0", totalvotes="1000.0"),
    dict(year=2020, state_po="GA", stage="gen", candidate="SHANE HAZEL", party_detailed="LIBERTARIAN", candidatevotes="30.0", totalvotes="1000.0"),
    dict(year=2021, state_po="GA", stage="runoff", candidate="DAVID PERDUE", party_detailed="REPUBLICAN", candidatevotes="505.0", totalvotes="1020.0"),
    dict(year=2021, state_po="GA", stage="runoff", candidate="JON OSSOFF", party_detailed="DEMOCRAT", candidatevotes="515.0", totalvotes="1020.0"),
    # GA 1992 'pre' with no runoff rows
    dict(year=1992, state_po="GA", stage="pre", candidate="WYCHE FOWLER, JR.", party_detailed="DEMOCRAT", candidatevotes="450.0", totalvotes="1000.0"),
    dict(year=1992, state_po="GA", stage="pre", candidate="PAUL COVERDELL", party_detailed="REPUBLICAN", candidatevotes="440.0", totalvotes="1000.0"),
    dict(year=1992, state_po="GA", stage="pre", candidate="JIM HUDSON", party_detailed="LIBERTARIAN", candidatevotes="110.0", totalvotes="1000.0"),
    # GA 2008-style: 'pre' = first round, 'gen' = the runoff that follows it
    dict(year=2008, state_po="GA", stage="pre", candidate="SAXBY CHAMBLISS", party_detailed="REPUBLICAN", candidatevotes="498.0", totalvotes="1000.0"),
    dict(year=2008, state_po="GA", stage="pre", candidate="JIM MARTIN", party_detailed="DEMOCRAT", candidatevotes="440.0", totalvotes="1000.0"),
    dict(year=2008, state_po="GA", stage="pre", candidate="ALLEN BUCKLEY", party_detailed="LIBERTARIAN", candidatevotes="62.0", totalvotes="1000.0"),
    dict(year=2008, state_po="GA", stage="gen", candidate="SAXBY CHAMBLISS", party_detailed="REPUBLICAN", candidatevotes="600.0", totalvotes="1000.0"),
    dict(year=2008, state_po="GA", stage="gen", candidate="JIM MARTIN", party_detailed="DEMOCRAT", candidatevotes="400.0", totalvotes="1000.0"),
    # a contest whose votes do not add up (must be counted, not fatal)
    dict(year=2018, state_po="NE", stage="gen", candidate="SOMEONE", party_detailed="DEMOCRAT", candidatevotes="10.0", totalvotes="99.0"),
]


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running")
    yield c
    c.close()


def results_for(conn, ckey):
    rows = conn.execute(
        """SELECT cr.resource, ca.resource FROM contest_result cr
           JOIN candidacy ca ON 'Candidacy/' || ca.id = cr.resource -> 'candidacy' ->> 'reference'
           WHERE cr.resource -> 'contest' ->> 'reference' =
                 (SELECT 'Contest/' || id FROM contest WHERE resource -> 'identifier' @> %s::jsonb)""",
        ('[{"system": "%s", "value": "%s"}]' % (SYS_CONTEST, ckey),)).fetchall()
    return {ca["name"]: (cr, ca) for cr, ca in rows}


@pytest.mark.db
def test_load_senate_fixture(conn, tmp_path):
    store = Store(conn)
    store.init_schema()
    CongressLoader(store, "t", "2026-01-01").load_record(RECORD)  # Person "Zed Q. Loader", senator TX from 2023-01-03
    f = tmp_path / "senate.tab"
    tsv(f, FIXTURE)

    counts, metrics = load_file(f, conn)
    assert metrics["vote_sum_mismatch"] == 1  # the deliberately broken NE contest

    r = results_for(conn, "2022:general:TX:senator:statewide")
    zed_res, zed_cand = r["ZED LOADER"]
    assert zed_res["votes"] == 620 and zed_res["outcome"]["coding"][0]["code"] == "won"  # two lines summed
    assert zed_res["partyCode"] == "R" and zed_cand["partyLabel"] == "REPUBLICAN"          # top line wins
    assert zed_cand["person"]["reference"].startswith("Person/")                           # linked to the Person
    assert r["JANE ROE"][0]["outcome"]["coding"][0]["code"] == "lost" and "person" not in r["JANE ROE"][1]
    assert "(write-in, aggregated)" in r and r["(write-in, aggregated)"][1]["writeIn"] is True
    assert abs(zed_res["voteShare"] - 620 / 1005) < 1e-6
    assert zed_res["outcomeDerived"] is True

    ga = results_for(conn, "2020:general:GA:senator:statewide")
    assert {n: v[0]["outcome"]["coding"][0]["code"] for n, v in ga.items()} == {
        "DAVID PERDUE": "advanced-to-runoff", "JON OSSOFF": "advanced-to-runoff", "SHANE HAZEL": "lost"}
    ro = results_for(conn, "2021:runoff:GA:senator:statewide")
    assert {n: v[0]["outcome"]["coding"][0]["code"] for n, v in ro.items()} == {"DAVID PERDUE": "lost", "JON OSSOFF": "won"}
    pre = results_for(conn, "1992:general:GA:senator:statewide")
    assert sorted(n for n, v in pre.items() if v[0]["outcome"]["coding"][0]["code"] == "advanced-to-runoff") == \
        ["PAUL COVERDELL", "WYCHE FOWLER, JR."]

    ga08 = results_for(conn, "2008:general:GA:senator:statewide")
    assert {n: v[0]["outcome"]["coding"][0]["code"] for n, v in ga08.items()} == {
        "SAXBY CHAMBLISS": "advanced-to-runoff", "JIM MARTIN": "advanced-to-runoff", "ALLEN BUCKLEY": "lost"}
    ga08r = results_for(conn, "2008:runoff:GA:senator:statewide")  # separate contest: no key collision
    assert {n: v[0]["outcome"]["coding"][0]["code"] for n, v in ga08r.items()} == {"SAXBY CHAMBLISS": "won", "JIM MARTIN": "lost"}
    assert ga08r["SAXBY CHAMBLISS"][0]["votes"] == 600 and ga08["SAXBY CHAMBLISS"][0]["votes"] == 498

    # Elections: derived Election Day only for a regular general
    e = store.find_by_identifier("Election", "urn:polstats:election", "2022:general")[0]
    assert str(e.date) == "2022-11-08"
    assert store.find_by_identifier("Election", "urn:polstats:election", "2021:runoff")[0].date is None

    # idempotent: a second load creates no new versions
    load_file(f, conn)
    assert conn.execute("SELECT max(version_id) FROM contest_result").fetchone()[0] == 1
    assert conn.execute("SELECT max(version_id) FROM candidacy").fetchone()[0] == 1


@pytest.mark.db
def test_load_house_style_columns(conn, tmp_path):
    """House files use `party` (no party_detailed/party_simplified) and numeric districts."""
    f = tmp_path / "house.tab"
    header = ["year", "state", "state_po", "office", "district", "stage", "special", "candidate", "party",
              "writein", "mode", "candidatevotes", "totalvotes", "unofficial", "version"]
    rows = [
        ["2022", '"VERMONT"', "VT", '"US HOUSE"', "000", "gen", "FALSE", '"A. CANDIDATE"', '"DEMOCRAT"', "FALSE", "total", "700", "1000", "FALSE", "1"],
        ["2022", '"VERMONT"', "VT", '"US HOUSE"', "000", "gen", "FALSE", '"B. CANDIDATE"', '"REPUBLICAN"', "FALSE", "total", "300", "1000", "FALSE", "1"],
        ["2022", '"TEXAS"', "TX", '"US HOUSE"', "012", "gen", "FALSE", '"C. CANDIDATE"', '"REPUBLICAN"', "FALSE", "total", "600", "1000", "FALSE", "1"],
        ["2022", '"TEXAS"', "TX", '"US HOUSE"', "012", "gen", "FALSE", '"D. CANDIDATE"', '"DEMOCRAT"', "FALSE", "total", "400", "1000", "FALSE", "1"],
    ]
    f.write_text("\t".join(header) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n")
    _, metrics = load_file(f, conn)
    assert metrics.get("vote_sum_mismatch", 0) == 0
    vt = results_for(conn, "2022:general:VT:representative:VT-AL")
    assert vt["A. CANDIDATE"][0]["outcome"]["coding"][0]["code"] == "won" and vt["B. CANDIDATE"][0]["partyCode"] == "R"
    tx = results_for(conn, "2022:general:TX:representative:TX-12")
    assert tx["C. CANDIDATE"][0]["outcome"]["coding"][0]["code"] == "won"


@pytest.mark.db
def test_comma_separated_original_loads_same_as_tab(conn, tmp_path):
    """Dataverse's original uploads are CSV; the converted .tab exports are TSV. Both must load identically."""
    rows = [dict(year=2026, state_po="WY", stage="gen", candidate="A ONE", party_detailed="REPUBLICAN",
                 candidatevotes="70.0", totalvotes="100.0"),
            dict(year=2026, state_po="WY", stage="gen", candidate="B TWO", party_detailed="DEMOCRAT",
                 candidatevotes="30.0", totalvotes="100.0")]
    t = tmp_path / "s.tab"
    tsv(t, rows)
    c = tmp_path / "s.csv"
    c.write_text(t.read_text().replace("\t", ","))
    load_file(t, conn)
    before = conn.execute("SELECT max(version_id) FROM contest_result").fetchone()[0]
    counts, metrics = load_file(c, conn)  # same data, other delimiter -> same resources, no new versions
    assert metrics.get("vote_sum_mismatch", 0) == 0 and counts["Contest"] == 1
    assert conn.execute("SELECT max(version_id) FROM contest_result").fetchone()[0] == before


@pytest.mark.db
def test_duplicate_contest_key_fails_loudly(conn, tmp_path):
    """'gen' and 'GEN' for the same race would collapse to one key; the loader must refuse, not overwrite."""
    f = tmp_path / "dup.tab"
    base = dict(year=2030, state_po="AK", candidatevotes="10.0", totalvotes="10.0")
    tsv(f, [dict(base, stage="gen", candidate="A ONE", party_detailed="DEMOCRAT"),
            dict(base, stage="GEN", candidate="B TWO", party_detailed="REPUBLICAN")])
    with pytest.raises(ValueError, match="duplicate contest key"):
        load_file(f, conn)
