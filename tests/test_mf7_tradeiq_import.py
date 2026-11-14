"""
MF-7b: the TradeIQ import (worker/market_feature_store/tradeiq_import.py). A synthetic work directory in the research
outputs' shapes; `plan` offline, and `write` into a throwaway cluster with migration 0019 (skipped unless
P1_PG_BINDIR is set). No archive data, no network.
"""
import csv
import json
import os
from datetime import date

import pytest

from worker.market_feature_store import tradeiq_import as ti

FIELDS = ["symbol", "company", "cls", "date", "close", "high", "low", "open", "trades", "shares", "turnover", "layout",
          "file", "sheet", "row", "block", "flags"]


def workdir(tmp_path):
    rows = [
        ["ABAN", "ABAN", "", "1995-01-02", "70", "", "", "", "", "", "", "L1", "1995 data.xls", "1995", "3", "", ""],
        ["ZZZ", "ZZZ", "", "1995-01-02", "5", "", "", "", "", "", "", "L1", "1995 data.xls", "1995", "4", "", ""],
        ["ABAN.N0000", "ABAN", "N", "2001-03-01", "25.5", "", "", "", "", "100", "", "L2", "2001 data.xls", "C", "4",
         "main", ""],
        ["AAF.N0000", "AAF", "N", "2015-01-02", "1.9", "2", "1.8", "", "48", "1657683", "3149594.7", "L3",
         "2015 Data.xls", "Sheet1", "6", "", ""],
        ["AAF.N0000", "AAF", "N", "2015-01-05", "2.0", "2", "1.9", "", "61", "1701013", "3400025.9", "L3",
         "2015 Data.xls", "Sheet1", "7", "", ""],
        ["BIL.N0000", "BIL", "N", "2015-01-05", "1.4", "", "", "", "", "", "", "L3", "2015 Data.xls", "Sheet1", "8", "", ""],
        ["BIL.N0000", "BIL", "N", "2015-01-05", "0.1", "", "", "", "", "", "", "L3", "2015 Data.xls", "Sheet1", "40", "", ""],
    ]
    with open(tmp_path / "daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    with open(tmp_path / "rejected.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "sheet", "row", "reason", "symbol", "date"])
        w.writerow(["2015 Data.xls", "Sheet1", "9", "row_wider_than_header", "CTC.N0000", "2015-01-05"])
        w.writerow(["1995 data.xls", "1995", "99", "unreadable_row", "", ""])
    files = [{"file": n, "sha256": c * 64} for n, c in (("1995 data.xls", "a"), ("2001 data.xls", "b"),
                                                         ("2015 Data.xls", "c"))]
    json.dump({"files": files}, open(tmp_path / "summary.json", "w"))
    with open(tmp_path / "recon.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["statement_id", "symbol", "kind", "ex_date", "reading", "held", "new", "subscription_price",
                    "expected_factor", "observed_factor", "cum_session", "available_on", "method", "source_row",
                    "source_sha256"])
        w.writerow(["tiq:rights:x:2015:7", "AAF.N0000", "rights", "2015-01-05", "new:held", "2", "1", "1.5", "17/19",
                    "20/19", "2015-01-02", "2014-12-20", "reconstructed_from_prices.v1", "04 [2015] row 7", "d" * 64])
    return tmp_path


SESSIONS = [date(2015, 1, 2), date(2015, 1, 5)]
SPLITS = [("AAF.N0000", 1, 2, date(2015, 1, 5), None, "tiq:split:s:2015:4"), ("AAF.N0000", 1, 1.5, date(2015, 1, 5),
                                                                                None, "tiq:split:s:2015:5")]


def test_t1_plan_maps_quarantines_and_declares(tmp_path):
    w = workdir(tmp_path)
    p = ti.plan(w, w / "summary.json", SPLITS, w / "recon.csv", SESSIONS)
    b95, b15 = p.batches["1995 data.xls"], p.batches["2015 Data.xls"]
    assert [x["symbol"] for x in b95.bars] == ["ABAN.N0000"] and p.counts["bars_left_out_unmapped_code"] == 1
    assert (b95.survivorship, p.batches["2001 data.xls"].survivorship, b15.survivorship) == ("unknown", "complete",
                                                                                            "complete")
    assert b15.quarantine == {("BIL.N0000", "2015-01-05"): "workbook_disagrees_with_itself",
                              ("CTC.N0000", "2015-01-05"): "row_wider_than_header"}
    assert {x["symbol"] for x in b15.bars} == {"AAF.N0000"}          # BIL's two disagreeing rows are out
    assert b15.complete == {"AAF.N0000": ["2015-01-02", "2015-01-05"]}
    assert b15.calendar == (date(2015, 1, 1), date(2015, 12, 31))     # its dates match the index sessions
    assert b95.calendar is None                                       # the index has no 1995 sessions here
    kinds = [(a["kind"], a["statement_id"]) for a in p.actions]
    assert kinds == [("subdivision", "tiq:split:s:2015:4"), ("rights", "tiq:rights:x:2015:7")]
    assert p.counts["splits_left_out_ratio"] == 1                     # 1:1.5 is not a whole-share ratio
    rights = p.actions[1]
    assert json.loads(rights["terms_json"])["subscription_price"] == "1.5"
    assert rights["available_at"].date() == date(2014, 12, 20)         # the announcement date


BINDIR = os.environ.get("P1_PG_BINDIR")


@pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
def test_t2_write_through_the_owner_path_in_one_transaction(tmp_path):
    import f64_support as fs
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path / "pg")
    try:
        c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
        mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(os.path.join(fs.REPO, "supabase", "migrations_pending")),
                  log=lambda m: None)
        c.close()
        db = fs.fresh_db(ec)
        w = workdir(tmp_path)
        p = ti.plan(w, w / "summary.json", SPLITS, w / "recon.csv", SESSIONS)
        conn = fs.conn(ec, db, user="cse_migrator")
        ti.write(conn, p)
        check = fs.conn(ec, db, user="postgres")
        with check.cursor() as cur:
            cur.execute("select count(*) from mf_history_imports")
            assert cur.fetchone()[0] == 3
            cur.execute("select count(*) from mf_history_bars")
            assert cur.fetchone()[0] == 4
            cur.execute("select count(*) from mf_history_quarantine")
            assert cur.fetchone()[0] == 2
            cur.execute("select kind, ex_date from mf_corporate_action_observations order by statement_id")
            assert cur.fetchall() == [("rights", date(2015, 1, 5)), ("subdivision", date(2015, 1, 5))]
            cur.execute("select price_basis, no_trade_convention, values_as_of from mf_history_imports limit 1")
            assert cur.fetchone() == ("raw", "row_absent", date(2026, 10, 19))
        w2 = fs.conn(ec, db, user="cse_worker")                       # the worker cannot replay it
        with pytest.raises(Exception):
            ti.write(w2, p)
    finally:
        ec.stop()
