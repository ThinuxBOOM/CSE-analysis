"""
MF-7b: the TradeIQ import (worker/market_feature_store/tradeiq_import.py). A synthetic work directory in the research
outputs' shapes; `plan` offline, and `write` into a throwaway cluster with migration 0019 (skipped unless
P1_PG_BINDIR is set). No archive data, no network.
"""
import csv
import json
import os
from datetime import date
from decimal import Decimal

import pytest

from worker.market_feature_store import tradeiq_import as ti

FIELDS = ["symbol", "company", "cls", "date", "close", "high", "low", "open", "trades", "shares", "turnover", "layout",
          "file", "sheet", "row", "block", "flags"]
FILES = ("1991-1993 data.xls", "1995 data.xls", "2000 data.xls", "2001 data.xls", "2003 Data.xls", "2015 Data.xls")


def l1(code, d, close, f, row):
    return [code, code, "", d, close, "", "", "", "", "", "", "L1", f, f[:4], str(row), "", ""]


def cls_row(company, k, d, close, layout, f, row, shares=""):
    return [f"{company}.{k}0000", company, k, d, close, "", "", "", "", shares, "", layout, f, "Sheet1", str(row), "", ""]


ROWS = [
    l1("SAMP", "1990-12-31", "20", "1991-1993 data.xls", 1),          # before 1991: not admitted
    l1("SAMP", "1992-01-02", "23", "1991-1993 data.xls", 2),
    l1("ABAN", "1995-01-02", "70", "1995 data.xls", 3),
    l1("ZZZ", "1995-01-02", "5", "1995 data.xls", 4),                  # never seen with a class: no link
    l1("XCO", "1995-01-02", "9", "1995 data.xls", 5),                  # later N and X: no link
    l1("LATE", "1995-01-02", "3", "1995 data.xls", 6),                 # N only from 2003: no link
    l1("ABAN", "2000-06-01", "30", "2000 data.xls", 7),
    cls_row("ABAN", "N", "2001-03-01", "25.5", "L2", "2001 data.xls", 4, "100"),
    cls_row("XCO", "N", "2001-03-01", "8", "L2", "2001 data.xls", 5),
    cls_row("XCO", "X", "2001-03-01", "6", "L2", "2001 data.xls", 6),
    cls_row("LATE", "N", "2003-01-02", "4", "L3", "2003 Data.xls", 7),
    cls_row("LATE", "N", "2003-01-04", "4", "L3", "2003 Data.xls", 8),  # a date the index lacks
    cls_row("AAF", "N", "2015-01-02", "1.9", "L3", "2015 Data.xls", 6, "1657683"),
    cls_row("AAF", "N", "2015-01-05", "0.95", "L3", "2015 Data.xls", 7, "1701013"),
    cls_row("AAF", "N", "2016-01-04", "0.475", "L3", "2015 Data.xls", 8),
    cls_row("AAF", "N", "2016-01-05", "0.475", "L3", "2015 Data.xls", 9),
    cls_row("BIL", "N", "2015-01-02", "1.4", "L3", "2015 Data.xls", 10),
    cls_row("BIL", "N", "2015-01-05", "1.4", "L3", "2015 Data.xls", 11),
    cls_row("BIL", "N", "2015-01-05", "0.1", "L3", "2015 Data.xls", 40),   # a second series under BIL's name (F10)
]
REJECTED = [
    ["2015 Data.xls", "Sheet1", "12", "row_wider_than_header", "CTC.N0000", "2015-01-05"],
    ["1995 data.xls", "1995", "99", "unreadable_row", "HAYL", "1995-02-10"],   # F14: a 1990s row with its code read
    ["1995 data.xls", "1995", "100", "unreadable_row", "", ""],
    ["1991-1993 data.xls", "1990", "101", "unreadable_row", "SAMP", "1990-12-28"],
]
SESSIONS = [date(1992, 1, 2), date(1995, 1, 2), date(2001, 3, 1), date(2003, 1, 2), date(2015, 1, 2),
            date(2015, 1, 5), date(2016, 1, 4), date(2016, 1, 5), date(2016, 1, 6)]
SPLITS = [
    ("AAF.N0000", 1, 2, date(2015, 1, 5), date(2014, 12, 30), "tiq:split:2015:4"),   # drops by the ratio
    ("AAF.N0000", 1, 1.5, date(2015, 1, 5), None, "tiq:split:2015:5"),               # not whole shares
    ("BIL.N0000", 1, 2, date(2015, 1, 5), None, "tiq:split:2015:6"),                 # its rows are all quarantined
    ("AAF.N0000", 1, 4, date(2015, 1, 5), None, "tiq:split:2015:7"),                 # moved, not by 1:4
    ("AAF.N0000", 1, 2, date(2016, 1, 5), None, "tiq:split:2016:1"),                 # no drop
    ("ABAN.N0000", 1, 2, date(2001, 3, 1), None, "tiq:split:2001:1"),                # no price before it
    ("AAF.N0000", 2, 4, date(2016, 1, 4), None, "tiq:split:2016:2"),                 # drops; no announcement
]


def workdir(tmp_path, rows=ROWS):
    with open(tmp_path / "daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    with open(tmp_path / "rejected.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "sheet", "row", "reason", "symbol", "date"])
        w.writerows(REJECTED)
    json.dump({"files": [{"file": n, "sha256": "abcdef"[i] * 64} for i, n in enumerate(FILES)]},
              open(tmp_path / "summary.json", "w"))
    with open(tmp_path / "recon.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["statement_id", "symbol", "kind", "ex_date", "reading", "held", "new", "subscription_price",
                    "expected_factor", "observed_factor", "cum_session", "available_on", "method", "source_row",
                    "source_sha256"])
        w.writerow(["tiq:rights:x:2015:7", "AAF.N0000", "rights", "2015-01-05", "new:held", "2", "1", "1.5", "17/19",
                    "20/19", "2015-01-02", "2014-12-20", "reconstructed_from_prices.v1", "04 [2015] row 7", "d" * 64])
    return tmp_path


def make_plan(tmp_path, rows=ROWS):
    w = workdir(tmp_path, rows)
    return ti.plan(w, w / "summary.json", SPLITS, w / "recon.csv", SESSIONS)


def test_t1_rows_are_verbatim_and_1990s_codes_are_linked_not_renamed(tmp_path):
    p = make_plan(tmp_path)
    b95 = p.batches["1995 data.xls"]
    assert [x["symbol"] for x in b95.bars] == ["ABAN", "ZZZ", "XCO", "LATE"]          # classless codes kept (F4)
    assert [(k["predecessor"], k["symbol"], k["effective_date"]) for k in p.links] == [
        ("ABAN", "ABAN.N0000", date(2001, 3, 1))]       # XCO also has X; LATE's N appears only from 2003; ZZZ never
    assert p.links[0]["available_at"] == ti.end_of_day(date(2002, 12, 31))
    assert p.counts["codes_without_link"] == 4                                      # SAMP, ZZZ, XCO, LATE


def test_t2_rows_before_1991_are_not_admitted(tmp_path):
    p = make_plan(tmp_path)
    b = p.batches["1991-1993 data.xls"]
    assert [x["trade_date"] for x in b.bars] == ["1992-01-02"] and p.counts["rows_before_1991_not_admitted"] == 1
    assert ("SAMP", "1990-12-28") not in b.quarantine


def test_t3_no_security_complete_through_2000_and_survivorship_boundary(tmp_path):
    p = make_plan(tmp_path)
    surv = {k: (b.survivorship, bool(b.complete)) for k, b in p.batches.items()}
    assert surv == {"1991-1993 data.xls": ("unknown", False), "1995 data.xls": ("unknown", False),
                    "2000 data.xls": ("unknown", False), "2001 data.xls": ("complete", True),
                    "2003 Data.xls": ("complete", True), "2015 Data.xls": ("complete", True)}
    assert p.batches["2015 Data.xls"].complete == {"AAF.N0000": ["2015-01-02", "2016-01-05"]}


def test_t4_a_security_printed_as_two_series_is_quarantined_for_the_whole_batch(tmp_path):
    p = make_plan(tmp_path)
    b15 = p.batches["2015 Data.xls"]
    assert {x["symbol"] for x in b15.bars} == {"AAF.N0000"}
    assert b15.quarantine == {("BIL.N0000", "2015-01-02"): "workbook_prints_two_series",   # its single-row day too
                              ("BIL.N0000", "2015-01-05"): "workbook_prints_two_series",
                              ("CTC.N0000", "2015-01-05"): "row_wider_than_header"}
    assert p.counts["securities_printed_as_two_series"] == 1 and p.counts["quarantined_two_series"] == 2


def test_t5_rejected_1990s_rows_with_a_readable_code_are_quarantined(tmp_path):
    p = make_plan(tmp_path)
    assert p.batches["1995 data.xls"].quarantine == {("HAYL", "1995-02-10"): "unreadable_row"}
    assert p.counts["rejected_rows_without_code_or_date"] == 2      # the empty one, and the one dated 1990


def test_t6_calendar_completeness_per_year_equal_to_the_index(tmp_path):
    p = make_plan(tmp_path)
    cal = {k: b.calendar_years for k, b in p.batches.items()}
    assert cal["1991-1993 data.xls"] == [(date(1992, 1, 1), date(1992, 12, 31))]
    assert cal["1995 data.xls"] == [(date(1995, 1, 1), date(1995, 12, 31))]
    assert cal["2000 data.xls"] == []                                # the index has no 2000 sessions here
    assert cal["2001 data.xls"] == [(date(2001, 1, 1), date(2001, 12, 31))]
    assert cal["2003 Data.xls"] == []                                # 2003-01-04 is not an index session
    assert cal["2015 Data.xls"] == [(date(2015, 1, 1), date(2015, 12, 31))]   # 2016-01-06 is missing


def test_t7_splits_are_written_only_where_raw_prices_drop_by_the_ratio(tmp_path):
    p = make_plan(tmp_path)
    assert sorted(p.left_out) == [("tiq:split:2001:1", "no_price_on_both_sides"),
                                  ("tiq:split:2015:5", "ratio_not_whole_shares"),
                                  ("tiq:split:2015:6", "no_price_series_for_symbol"),
                                  ("tiq:split:2015:7", "other_move"),
                                  ("tiq:split:2016:1", "no_drop")]
    by = {a["statement_id"]: a for a in p.actions}
    assert sorted(by) == ["tiq:rights:x:2015:7", "tiq:split:2015:4", "tiq:split:2016:2"]
    assert by["tiq:split:2015:4"]["kind"] == "subdivision"
    assert by["tiq:split:2015:4"]["available_at"] == ti.end_of_day(date(2014, 12, 30))   # the announcement
    assert by["tiq:split:2016:2"]["available_at"] == ti.end_of_day(date(2016, 1, 4))     # else the effective date
    rights = by["tiq:rights:x:2015:7"]
    assert json.loads(rights["terms_json"])["subscription_price"] == "1.5"
    assert rights["available_at"].date() == date(2014, 12, 20)


def test_t8_split_check_tolerance():
    s = [(date(2015, 1, 2), Decimal("10")), (date(2015, 1, 5), Decimal("5.2"))]
    assert ti.split_confirmed(s, 1, 2, date(2015, 1, 5)) is None
    assert ti.split_confirmed(s, 1, 2, date(2015, 1, 2)) == "no_price_on_both_sides"
    assert ti.split_confirmed(s, 1, 2, date(2015, 1, 6)) == "no_price_on_both_sides"
    assert ti.split_confirmed([], 1, 2, date(2015, 1, 5)) == "no_price_series_for_symbol"


BINDIR = os.environ.get("P1_PG_BINDIR")


def _cluster(tmp_path):
    import f64_support as fs
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path / "pg")
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(os.path.join(fs.REPO, "supabase", "migrations_pending")),
              log=lambda m: None)
    c.close()
    return fs, ec


def _counts(fs, ec, db):
    check = fs.conn(ec, db, user="postgres")
    out = {}
    with check.cursor() as cur:
        for t in ("mf_history_imports", "mf_history_bars", "mf_history_quarantine", "mf_history_security_complete",
                  "mf_calendar_observations", "mf_identity_observations", "mf_corporate_action_observations"):
            cur.execute(f"select count(*) from {t}")
            out[t] = cur.fetchone()[0]
    check.close()
    return out


@pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
def test_t9_write_through_the_owner_path(tmp_path):
    fs, ec = _cluster(tmp_path)
    try:
        db = fs.fresh_db(ec)
        p = make_plan(tmp_path)
        ti.write(fs.conn(ec, db, user="cse_migrator"), p)
        assert _counts(fs, ec, db) == {
            "mf_history_imports": 6, "mf_history_bars": 15, "mf_history_quarantine": 4,
            "mf_history_security_complete": 5, "mf_calendar_observations": 4, "mf_identity_observations": 1,
            "mf_corporate_action_observations": 3}
        check = fs.conn(ec, db, user="postgres")
        with check.cursor() as cur:
            cur.execute("select price_basis, no_trade_convention, values_as_of from mf_history_imports limit 1")
            assert cur.fetchone() == ("raw", "row_absent", date(2026, 10, 19))
            cur.execute("select source_label, calendar_complete_from from mf_history_imports order by source_label")
            assert dict(cur.fetchall())["2003 Data.xls"] is None
            cur.execute("select kind, symbol, predecessor from mf_identity_observations")
            assert cur.fetchall() == [("link", "ABAN.N0000", "ABAN")]
        with pytest.raises(Exception):
            ti.write(fs.conn(ec, db, user="cse_worker"), p)                # the worker cannot replay it
    finally:
        ec.stop()


@pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
def test_t10_write_is_one_transaction(tmp_path):
    fs, ec = _cluster(tmp_path)
    try:
        db = fs.fresh_db(ec)
        p = make_plan(tmp_path)
        p.actions.append(dict(p.actions[-1], statement_id="tiq:bad", kind="not_a_kind"))   # fails after every batch
        with pytest.raises(Exception):
            ti.write(fs.conn(ec, db, user="cse_migrator"), p)
        assert set(_counts(fs, ec, db).values()) == {0}
    finally:
        ec.stop()
