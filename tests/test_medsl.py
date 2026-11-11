from datetime import date

import psycopg
import pytest

from polstats import codesystems as cs
from polstats.loaders.congress_legislators import Loader as CongressLoader
from polstats.loaders.medsl import (SYS_CONTEST, Cand, derive_outcomes, district_label, district_number,
                                    effective_stage, is_bucket_name, kind_of, load_file, norm, party_code,
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
    for name in ("WRITEIN", "BLANK VOTE/SCATTERING", "VOID", "UNDERVOTES", "OTHER", "SCATTERING", "",
                 "(write-in, aggregated)", "(unnamed)", "BLANK VOTE/VOID VOTE/SCATTERING", "OVERVOTES",
                 "EXHAUSTED BALLOT", "EXHAUSTED BALLOTS", "ALL OTHERS", "BLANKS", "MISCELLANEOUS", "SCATTER", "SPOILED",
                 "NONE OF THESE CANDIDATES", "NONE OF THESE, CANDIDATES", "INDEPENDENT", "WORKING FAMILIES", "MODERATE"):
        assert is_bucket_name(name), name
    for name in ("LUCY MCBATH", "WYCHE FOWLER, JR.", "OTHERO GORDON", "BLANKENSHIP", "VOIDE", "EXHAUSTEDSON",
                 "INDEPENDENT JONES", "MODERATE MARY", "SCATTERGOOD"):  # real names must not match
        assert not is_bucket_name(name), name
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
    def snapshot():
        return [conn.execute(f"SELECT count(*), COALESCE(sum(version_id), 0) FROM {t}").fetchone()
                for t in ("contest", "candidacy", "contest_result", "provenance")]
    before = snapshot()
    load_file(f, conn)
    assert snapshot() == before  # same rows, same versions: nothing was rewritten


@pytest.mark.db
def test_load_house_style_columns(conn, tmp_path):
    """House files: comma-separated, `party` column, a separate `runoff` column, catch-all rows with party NA,
    uncontested races stored as -1, and DC as an at-large (district 0) seat."""
    f = tmp_path / "house.csv"
    header = ["year", "state", "state_po", "office", "district", "stage", "runoff", "special", "candidate", "party",
              "writein", "mode", "candidatevotes", "totalvotes", "unofficial", "version", "fusion_ticket"]

    def row(year, st, dist, cand, party, votes, total, stage="GEN", runoff="NA", wi="FALSE", special="FALSE"):
        return [str(year), st, st, "US HOUSE", str(dist), stage, runoff, special, cand, party, wi, "TOTAL",
                str(votes), str(total), "FALSE", "1", "FALSE"]

    rows = [
        # VT at-large with a write-in catch-all row (party NA) that must never win
        row(2022, "VT", 0, "A. CANDIDATE", "DEMOCRAT", 690, 1000), row(2022, "VT", 0, "B. CANDIDATE", "REPUBLICAN", 300, 1000),
        row(2022, "VT", 0, "WRITEIN", "NA", 10, 1000, wi="TRUE"),
        row(2022, "TX", 12, "C. CANDIDATE", "REPUBLICAN", 600, 1000), row(2022, "TX", 12, "D. CANDIDATE", "DEMOCRAT", 400, 1000),
        # FL uncontested: placeholder -1 votes and -1 total
        row(2024, "FL", 20, "E. UNOPPOSED", "DEMOCRAT", -1, -1),
        # LA 2002 style: first round (runoff FALSE) then runoff (runoff TRUE), both stage GEN
        row(2002, "LA", 5, "F. FIRST", "REPUBLICAN", 450, 1000, runoff="FALSE"), row(2002, "LA", 5, "G. SECOND", "DEMOCRAT", 400, 1000, runoff="FALSE"),
        row(2002, "LA", 5, "H. THIRD", "REPUBLICAN", 150, 1000, runoff="FALSE"),
        row(2002, "LA", 5, "F. FIRST", "REPUBLICAN", 480, 1000, runoff="TRUE"), row(2002, "LA", 5, "G. SECOND", "DEMOCRAT", 520, 1000, runoff="TRUE"),
        # NY-style fusion: the Working Families line out-polls the Democratic line, but party must be D;
        # the stated total (900) excludes the BLANK row, so blank gets no vote share and is not a mismatch
        row(2022, "NY", 1, "K. FUSION", "DEMOCRAT", 200, 900), row(2022, "NY", 1, "K. FUSION", "WORKING FAMILIES", 300, 900),
        row(2022, "NY", 1, "L. REP", "REPUBLICAN", 400, 900), row(2022, "NY", 1, "BLANK", "NA", 50, 900),
        # DC delegate seat
        row(2022, "DC", 0, "I. DELEGATE", "DEMOCRAT", 800, 1000), row(2022, "DC", 0, "J. OTHER", "REPUBLICAN", 200, 1000),
    ]
    f.write_text(",".join(header) + "\n" + "\n".join(",".join(r) for r in rows) + "\n")
    _, metrics = load_file(f, conn)
    assert metrics.get("vote_sum_mismatch", 0) == 0 and metrics["uncontested"] == 1  # NY blank row is not a mismatch

    def code_of(res):
        return res["outcome"]["coding"][0]["code"]

    vt = results_for(conn, "2022:general:VT:representative:VT-AL")
    assert code_of(vt["A. CANDIDATE"][0]) == "won" and vt["B. CANDIDATE"][0]["partyCode"] == "R"
    assert code_of(vt["WRITEIN"][0]) == "lost" and "partyLabel" not in vt["WRITEIN"][1]  # "NA" is not a party
    assert vt["WRITEIN"][1]["writeIn"] is True
    assert code_of(results_for(conn, "2022:general:TX:representative:TX-12")["C. CANDIDATE"][0]) == "won"

    fl = results_for(conn, "2024:general:FL:representative:FL-20")
    unopposed, _ = fl["E. UNOPPOSED"]
    assert "votes" not in unopposed and "voteShare" not in unopposed and code_of(unopposed) == "won"
    contest = store_contest(conn, "2024:general:FL:representative:FL-20")
    assert contest["uncontested"] is True and "totalVotes" not in contest

    first = results_for(conn, "2002:general:LA:representative:LA-5")
    assert {n: code_of(v[0]) for n, v in first.items()} == {
        "F. FIRST": "advanced-to-runoff", "G. SECOND": "advanced-to-runoff", "H. THIRD": "lost"}
    runoff = results_for(conn, "2002:runoff:LA:representative:LA-5")
    assert {n: code_of(v[0]) for n, v in runoff.items()} == {"F. FIRST": "lost", "G. SECOND": "won"}

    ny = results_for(conn, "2022:general:NY:representative:NY-1")
    k, k_cand = ny["K. FUSION"]
    assert k["votes"] == 500 and k["partyCode"] == "D" and k_cand["partyLabel"] == "DEMOCRAT" and code_of(k) == "won"
    assert ny["BLANK"][0]["votes"] == 50 and "voteShare" not in ny["BLANK"][0]  # a ballot, not a vote
    assert abs(k["voteShare"] - 500 / 900) < 1e-6

    dc = store_contest(conn, "2022:general:DC:delegate:DC-AL")  # DC sends a delegate, not a voting representative
    assert dc["office"]["coding"][0]["code"] == "delegate"


def store_contest(conn, ckey):
    row = conn.execute("SELECT resource FROM contest WHERE resource -> 'identifier' @> %s::jsonb",
                       ('[{"system": "%s", "value": "%s"}]' % (SYS_CONTEST, ckey),)).fetchone()
    assert row, ckey
    return row[0]


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
