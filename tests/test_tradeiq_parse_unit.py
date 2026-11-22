"""
The TradeIQ daily-price parser (scripts/research/tradeiq/daily_parse.py; research, C-0035). Synthetic rows in the
four layouts found in the archive; no CSE data. Pure and offline: the spreadsheet libraries are not needed.
"""
import os
import sys
from datetime import date, datetime
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "research", "tradeiq"))
import daily_parse as dp  # noqa: E402


def test_t1_numbers_dates_and_symbols():
    assert dp.number(0.1) == D("0.1") and dp.number("1,987,698,910.00") == D("1987698910.00")
    assert dp.number("") is None and dp.number(None) is None
    for bad in ("194..75", "abc", True, "nan"):
        with pytest.raises(ValueError):
            dp.number(bad)
    assert dp.to_date(42006.0) == date(2015, 1, 2) and dp.to_date(33477.0) == date(1991, 8, 27)
    assert dp.to_date("02-JAN-20") == date(2020, 1, 2) and dp.to_date("2-Jan-23") == date(2023, 1, 2)
    assert dp.to_date("2025-01-02 00:00:00") == date(2025, 1, 2) and dp.to_date(datetime(2025, 1, 2)) == date(2025, 1, 2)
    assert dp.to_date(42006.5) is None and dp.to_date("SQL> SPOOL") is None and dp.to_date(12.0) is None
    assert dp.symbol("aaf", "N", "0.0") == "AAF.N0000" and dp.symbol("JKH", "W", "0022") == "JKH.W0022"
    assert dp.symbol("ABAN", None, None) == "ABAN"


def test_t2_layout_1_the_1990s_exports():
    rows = [["", "SECURITY_DA", "PRICE"], ["-----------", "----", "-----"], [33477.0, "SAMP", 23.0],
            [33478.0, "SAMP", 0.0], ["", "", ""], ["SECURITY_DA", "COMP", "CLOSE_PRICE"], [33479.0, "ABAN", "194..75"],
            ["2 rows", "selected.", ""], ["SQL> SPOOL", "OFF", ""]]
    assert dp.detect_layout(rows) == "L1"
    recs, bad = dp.parse_sheet(rows)
    assert [(r.symbol, r.trade_date, r.close, r.security_class, r.flags) for r in recs] == [
        ("SAMP", date(1991, 8, 27), D("23.0"), None, ()), ("SAMP", date(1991, 8, 28), None, None, ("zero_price",))]
    assert [b.source_row for b in bad] == [6, 7, 8]           # the typo and the footer are reported, not dropped


def test_t3_layout_2_two_tables_side_by_side():
    rows = [["Date", "Security ID", "Closing Price", "Share Volume"] + [""] * 6 + [36909.0, "HNB-D-0014", 87.0],
            [36971.0, "ABAN-N-0000", 25.5, 100.0] + [""] * 9]
    assert dp.detect_layout(rows) == "L2"
    recs, bad = dp.parse_sheet(rows)
    assert {(r.symbol, r.close, r.source_block) for r in recs} == {("HNB.D0014", D("87.0"), "right"),
                                                                   ("ABAN.N0000", D("25.5"), "main")}
    assert bad == []


def test_t4_layout_3_report_blocks_overflow_and_traded_without_a_price():
    rows = [["DAILY HIGH LOW"], ["Company Id", ":  AAIC", "Security", "Type :  N", "Sub T", "ype :  0000"],
            ["Day", "Date High", "High", "Date Low", "Low", "Closing", "Trades", "Shares", "Turnover", "Last", "Days"],
            [40394.0, 40394.0, 170.0, 40394.0, 165.0, 169.7, 38.0, "'11,692,517    1'", "',987,698,910.00'", 40394.0, 1.0],
            [40395.0, 40395.0, "", 40395.0, "", "", 1.0, 26715700.0, 394056575.0, 40395.0, 1.0],
            [40396.0, 40396.0, 12825.0, 40396.0, "'8,000.00  1'", 2825.0, 4.0, 4.0, 45325.0, 40396.0, 1.0]]
    assert dp.detect_layout(rows) == "L3"
    recs, bad = dp.parse_sheet(rows)
    assert bad == []
    a, b, c = recs
    assert (a.symbol, a.shares, a.turnover, a.flags) == ("AAIC.N0000", D("11692517"), D("1987698910.00"),
                                                         ("repaired_overflow",))
    assert (b.close, b.shares, b.flags) == (None, D("26715700.0"), ("no_price_printed",))
    assert (c.low, c.close, c.flags) == (D("8000.00"), D("12825"), ("repaired_overflow",))
    orphan = [["Day", "Date High"], [39815.0, 39815.0, 31.0, 39815.0, 30.0, 30.25, 4.0, 600.0, 18100.0, 39815.0, 1.0]]
    assert dp.parse_sheet(orphan, "L3")[1][0].reason == "row_before_any_company_line"


def test_t5_layout_4_columns_by_name_and_shifted_rows_quarantined():
    hdr = ["COMPANY ID", "MAIN TYPE", "SUB TYPE", "SHORT NAME", "TRADING DATE", "PRICE HIGH (Rs.)", "PRICE LOW (Rs.)",
           "CLOSE PRICE (Rs.)", "OPEN PRICE (Rs.)", "TRADE VOLUME (No.)", "SHARE VOLUME (No.)", "TURNOVER (Rs.)"]
    rows = [["DAILY HIGH, LOW"], hdr + ["", "", "", ""],
            ["AAF", "N", "0", "X", "3-Jan-23", "7.90", "7.50", "7.70", "7.90", "4", "10,261", "78,909.90", "", "", "", ""],
            ["BREW", "N", "0", "X", "23-Jan-23", "1.00", "50.00", "1.00", "50.00", "900", "1", "50.00", "3", "4", "4200", ""]]
    assert dp.detect_layout(rows) == "L4"
    recs, bad = dp.parse_sheet(rows)
    assert [(r.symbol, r.close, r.open, r.shares, r.turnover) for r in recs] == [
        ("AAF.N0000", D("7.70"), D("7.90"), D("10261"), D("78909.90"))]
    assert [b.reason for b in bad] == ["row_wider_than_header"]
    no_open = [h for h in hdr if not h.startswith("OPEN")]   # 2016: no OPEN PRICE column, so positions shift
    recs, bad = dp.parse_sheet([no_open, ["AAF", "N", 0.0, "X", 42373.0, 1.7, 1.6, 1.7, 12.0, 126036.0, 201761.1]], "L4")
    r = recs[0]
    assert (r.open, r.trades, r.shares, r.turnover) == (None, D("12.0"), D("126036.0"), D("201761.1"))


def test_t6_an_unknown_layout_is_reported():
    recs, bad = dp.parse_sheet([["nothing", "here"]])
    assert recs == [] and bad[0].reason == "unknown_layout"
