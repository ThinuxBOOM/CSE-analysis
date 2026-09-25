"""
Stage F3 — report type & period classification.

The fixtures are SYNTHETIC page texts in `pdftotext -layout` form, rebuilt
from the header layouts observed in the 19 real F3-discovery filings (cover
wording, column headers, scope labels, dates, audit labels). They contain
placeholder amounts only so the tests can prove that no amount ever reaches
the output. No real document text is stored in the repository.

The classifier is pure; the F2 integration tests use a fake CDN fetcher and a
real temporary file (and the real pdftotext when it is installed).
"""
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import classify_filing_documents as cli
from worker import document_retrieval as dr
from worker import document_text as dt
from worker import report_classification as rc
from worker import report_classification_store as store

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


# --- fixture helpers -----------------------------------------------------------------------

def L(*parts):
    """A layout line: (column, text) pairs placed at absolute character columns."""
    s = ""
    for col, text in parts:
        s = s + " " * (col - len(s)) if len(s) < col else s + "  "
        s += text
    return s


def page(*lines):
    return "\n".join(lines) + "\n"


def doc(*pages):
    return dt.from_pages(list(pages))


def ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=rc.CSE_LOCAL_TZ).timestamp() * 1000)


def meta(title, manual=None, uploaded=None, buckets=("quarterly",)):
    return {"file_text": title, "manual_date_raw": manual, "uploaded_at": uploaded,
            "authorized_at": uploaded, "source_buckets": list(buckets)}


NOTES = "These interim financial statements have been prepared in accordance with LKAS 34 Interim Financial Reporting."
DATA = L((0, "Revenue"), (38, "1,234,567"), (50, "1,111,222"), (68, "(987,654)"), (82, "876,543"))


def periods(res, statement=None, **kw):
    out = [p for p in res.statement_periods if statement is None or p["statement_kind"] == statement]
    return [p for p in out if all(p.get(k) == v for k, v in kw.items())]


def evidence(res, rule_id):
    return [e for e in res.evidence if e["rule_id"] == rule_id]


def classify(d, m=None, fid=1):
    return rc.classify(d, m, cse_filing_id=fid, sha256="a" * 64)


# --- real-case fixtures (layouts as observed in discovery) ---------------------------------

def acl_march_fye_q1():
    """ACL (52620): March year-end, 3 months to 30 June, Group|Company, cover says UNAUDITED."""
    return doc(
        page("INTERIM FINANCIAL STATEMENTS", "FOR THE THREE MONTHS ENDED 30TH JUNE 2026", "UNAUDITED",
             "ACL CABLES PLC (PQ 102)"),
        page("STATEMENT OF PROFIT OR LOSS",
             L((42, "Group"), (72, "Company")),
             L((34, "For the period ended 30 June")),
             L((38, "2026"), (50, "2025"), (68, "2026"), (82, "2025")),
             DATA),
        page("STATEMENT OF FINANCIAL POSITION",
             L((42, "Group"), (72, "Company")),
             L((0, "As at"), (36, "30-Jun-26"), (48, "31-Mar-26"), (66, "30-Jun-26"), (80, "31-Mar-26")),
             L((49, "Audited"), (81, "Audited")),
             DATA),
        page("STATEMENT OF CASH FLOWS",
             L((42, "Group"), (72, "Company")),
             L((34, "For the period ended 30 June")),
             L((38, "2026"), (50, "2025"), (68, "2026"), (82, "2025")),
             DATA),
        page("NOTES TO THE FINANCIAL STATEMENTS", NOTES,
             "They should be read with the annual financial statements for the year ended 31 March 2026."))


ACL_META = meta("Interim Financial Statements for the Quarter ended 30th June 2026", ms(2026, 6, 30), "2026-08-14T10:00:00+05:30")


def seyb_december_fye_q2():
    """SEYB (52157): December year-end bank, 6M + quarter, Bank|Group, 'Growth %' separators."""
    return doc(
        page("Seylan Bank PLC", "Interim Financial Statements", "For the 06 Months Ended 30th June 2026"),
        page(L((0, "Income Statement"), (60, "Bank")),
             L((30, "For the Six Months Ended"), (80, "For the Quarter Ended")),
             L((34, "30th June"), (84, "30th June")),
             L((30, "2026"), (42, "2025"), (54, "Growth"), (80, "2026"), (92, "2025"), (104, "Growth")),
             L((54, "%"), (104, "%")),
             L((0, "Interest Income"), (28, "1,000,000"), (40, "900,000"), (54, "11.1"), (78, "500,000"), (90, "450,000"))),
        page("Statement of Financial Position",
             L((35, "Bank"), (75, "Group")),
             L((30, "As at"), (45, "As at"), (70, "As at"), (85, "As at")),
             L((30, "30.06.2026"), (45, "31.12.2025"), (70, "30.06.2026"), (85, "31.12.2025")),
             L((45, "(Audited)"), (85, "(Audited)")),
             L((0, "Cash and Cash Equivalents"), (30, "12,345"), (45, "11,111"), (70, "13,000"), (85, "12,000"))),
        page(L((0, "Statement of Cash Flows"), (40, "Bank"), (70, "Group")),
             L((30, "For the Six Months ended 30th June")),
             L((32, "2026"), (46, "2025"), (72, "2026"), (86, "2025")),
             L((0, "Profit before tax"), (30, "5,000"), (44, "4,000"), (70, "6,000"), (84, "5,500"))),
        page("Explanatory notes", NOTES))


SEYB_META = meta("Interim Financial Statements for the Quarter ended 30th June 2026", ms(2026, 6, 30), "2026-07-30T16:00:00+05:30")


def pabc_q3_bank():
    """PABC (49086): December year-end bank, 9M + quarter with 'Change' columns, kerned years
    ('2 025'); cash flow headed 'Current Period / Previous Period' with 'From ... To' dates."""
    return doc(
        page("INTERIM FINANCIAL STATEMENTS", "FOR THE NINE MONTHS ENDED 30TH SEPTEMBER 2025", "COMPANY REGISTRATION NO : PQ 48"),
        page(L((0, "Income Statement")),
             L((110, "In Rupee Thousands")),
             L((60, "For the Nine Months ended"), (88, "Change"), (100, "For the Quarter ended"), (126, "Change")),
             L((65, "30th September"), (91, "%"), (105, "30th September"), (129, "%")),
             L((0, "Interest Income"), (60, "2 025"), (74, "2 024"), (89, "(2)"), (100, "2 025"), (114, "2 024"), (128, "4")),
             L((0, "Interest Expenses"), (58, "1,234,567"), (72, "1,111,111"), (98, "400,000"), (112, "380,000"))),
        page(L((0, "Statement of Financial Position")),
             L((110, "As at 31/12/2024"), (128, "Change")),
             L((114, "(Audited)"), (131, "%")),
             L((92, "As at 30/09/2025")),
             L((0, "Cash and cash equivalents"), (92, "12,345,678"), (110, "11,000,000"))),
        page(L((0, "Statement of Cash Flows")),
             L((70, "Current Period"), (88, "Previous Period")),
             L((70, "From 01/01/2025 To"), (90, "From 01/01/2024 To")),
             L((70, "30/09/2025"), (90, "30/09/2024")),
             L((0, "Profit before tax"), (70, "9,999,999"), (90, "8,888,888"))),
        page(NOTES))


def ctc_q4_twelve_months():
    """CTC (50553): cover says '3 months ended 31 December 2025' but statements carry
    3M and 12M; 12M comparative audited; single entity (no scope labels)."""
    return doc(
        page("Ceylon Tobacco Company PLC", "Interim Financial Statements - 3 months ended 31 December 2025"),
        page("STATEMENT OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME",
             L((40, "03 months ended 31 December"), (80, "12 months ended 31 December")),
             L((40, "Un-audited"), (55, "Un-audited"), (80, "Un-audited"), (95, "Audited")),
             L((42, "2025"), (57, "2024"), (82, "2025"), (97, "2024")),
             L((0, "Revenue"), (38, "40,000,000"), (53, "38,000,000"), (78, "150,000,000"), (93, "140,000,000"))),
        page("STATEMENT OF FINANCIAL POSITION",
             L((40, "31-Dec"), (55, "31-Dec")),
             L((40, "2025"), (55, "2024")),
             L((38, "Un-audited"), (55, "Audited")),
             L((0, "Assets"), (38, "9,000,000"), (53, "8,500,000"))),
        page("STATEMENT OF CASH FLOWS",
             L((50, "12 months ended 31 December")),
             L((50, "Un-audited"), (66, "Audited")),
             L((52, "2025"), (67, "2024")),
             L((0, "Cash generated from operations"), (48, "20,000,000"), (63, "19,000,000"))),
        page(NOTES, "The same accounting policies as in the audited financial statements for the year ended "
                    "31st December 2024 have been applied."))


def crl_audited_fs():
    """CRL (48576): audited financial statements, 'FINANCIAL STATEMENTS 31 MARCH 2025' cover,
    auditor's report, many scanned pages."""
    return doc(
        page("SOFTLOGIC FINANCE PLC", "FINANCIAL STATEMENTS", "31 MARCH 2025"),
        page("INDEPENDENT AUDITOR'S REPORT", "TO THE SHAREHOLDERS OF SOFTLOGIC FINANCE PLC",
             "Report on the audit of the financial statements. We have audited the financial statements."),
        page("INCOME STATEMENT", L((50, "Year ended 31 March")), L((52, "2025"), (66, "2024")),
             L((0, "Interest income"), (48, "5,000,000"), (62, "4,000,000"))),
        page("STATEMENT OF FINANCIAL POSITION", L((0, "As at 31 March"), (52, "2025"), (66, "2024")),
             L((0, "Cash and bank balances"), (48, "1,000,000"), (62, "900,000"))),
        page("STATEMENT OF CASH FLOWS", L((50, "Year ended 31 March")), L((52, "2025"), (66, "2024")),
             L((0, "Profit before tax"), (48, "300,000"), (62, "250,000"))),
        "", "  ", "")


CRL_META = meta("Audited Financial Statements for the year ended 31st March 2025", ms(2025, 3, 31),
                "2025-08-11T12:00:00+05:30", buckets=("annual",))


def tess_annual_report_with_quarters():
    """TESS (53096): annual report whose first page is the back cover, main statements for
    the year, plus a quarterly analysis page (three months ended ...)."""
    return doc(
        page("+94 11 2269859", "info@tess.lk", "TESS AGRO PLC", "2025/2026", "ANNUAL", "REPORT"),
        page("NOTICE OF MEETING", "To receive and consider the Statements of Accounts for the year ended "
                                  "31st March 2026 and the Report of the Auditors thereon."),
        page("CHAIRMAN'S MESSAGE", "Dear shareholders,", "CORPORATE GOVERNANCE is central to our business."),
        page("INDEPENDENT AUDITOR'S REPORT", "Opinion. We have audited the financial statements of the company."),
        page("STATEMENT OF PROFIT OR LOSS", L((48, "For the year ended 31 March")), L((50, "2026"), (64, "2025")),
             L((0, "Revenue"), (46, "450,000,000"), (60, "400,000,000"))),
        page("STATEMENT OF FINANCIAL POSITION", L((0, "As at 31 March"), (50, "2026"), (64, "2025")),
             L((64, "Restated")),
             L((0, "Property, plant and equipment"), (46, "973,000,000"), (60, "934,000,000"))),
        page("STATEMENT OF PROFIT OR LOSS - QUARTERLY ANALYSIS",
             L((40, "three months ended")),
             L((30, "30th June"), (45, "30th Sept"), (60, "31st Dec"), (75, "31st March")),
             L((30, "2025"), (45, "2025"), (60, "2025"), (75, "2026")),
             L((0, "Revenue"), (28, "100,000,000"), (43, "110,000,000"), (58, "120,000,000"), (73, "120,000,000"))))


TESS_META = meta("Annual Report as at 31st March 2026", ms(2026, 3, 31), "2026-08-30T10:00:00+05:30", buckets=("annual",))


def ucar_errata_25th():
    """UCAR (53129): errata cover letter + reissued interim; periods end on the 25th
    (52/53-week style calendar)."""
    return doc(
        page("Colombo Stock Exchange", "Dear Madam.",
             "UNION CHEMICALS LANKA PLC ERRATA - CORRECTION OF THE PUBLIC HOLDING PERCENTAGE INDICATED IN THE INTERIM"),
        page("Interim Financial Statements for the Period Ended 25th June 2026", "UNION CHEMICALS LANKA PLC"),
        page("STATEMENT OF COMPREHENSIVE INCOME",
             L((40, "Quarter"), (52, "Quarter"), (64, "Six Months"), (78, "Six Months")),
             L((40, "Ended"), (52, "Ended"), (64, "Ended"), (78, "Ended")),
             L((0, "For The Period Ended 25Th June,")),
             L((41, "2026"), (53, "2025"), (66, "2026"), (80, "2025")),
             L((0, "Revenue"), (38, "1,000,000"), (50, "900,000"), (62, "2,000,000"), (76, "1,800,000"))),
        page("STATEMENT OF FINANCIAL POSITION",
             L((40, "As at"), (55, "As at")),
             L((40, "2026"), (55, "2025")),
             L((0, "As at 25th June,")),
             L((0, "Assets"), (38, "5,000,000"), (53, "4,500,000"))),
        page(NOTES, "Accounting policies are consistent with the Annual Report for the year ended 25th December, 2025."))


def asph_errata_metadata_only():
    """ASPH (52860): a complete reissued Q4 interim; the document never says 'errata' —
    only the CSE title does."""
    return doc(
        page("Since 1964", "YEARS"),
        page("INDUSTRIAL ASPHALTS (CEYLON) PLC", "INTERIM FINANCIAL STATEMENTS", "TABLE OF CONTENTS",
             "Statement of Comprehensive Income 3", "Statement of Financial Position 4", "Statement of Changes in Equity 5"),
        page("STATEMENT OF COMPREHENSIVE INCOME",
             L((48, "For the year ended"), (86, "For the quarter ended")),
             L((58, "31.03.26"), (72, "31.03.25"), (84, "Change"), (97, "31.03.26"), (111, "31.03.25"), (123, "Change")),
             L((57, "(unaudited)"), (72, "(audited)"), (96, "(unaudited)"), (111, "(audited)")),
             L((0, "Revenue"), (56, "800,000"), (70, "700,000"), (95, "200,000"), (109, "180,000"))),
        page("STATEMENT OF FINANCIAL POSITION",
             L((0, "As at"), (58, "31.03.26"), (72, "31.03.25")),
             L((57, "(unaudited)"), (72, "(audited)")),
             L((0, "Assets"), (56, "900,000"), (70, "850,000"))),
        page(NOTES, "Comparative figures are drawn from the audited financial statements for the year ended 31 March 2025."))


def dial_press_release():
    """DIAL (39390): a results press release — no primary statements."""
    return doc(
        page("Dialog Continues Consistent Performance with a Stable Q3", "11th November 2021. Colombo.",
             "Dialog Axiata PLC announced, Thursday 11th November 2021, its consolidated financial results for the "
             "nine months ended 30th September 2021. Revenue grew 5% QoQ."),
        page("Dialog Group continued to exhibit a healthy and low geared",
             "balance sheet as the Net Debt to EBITDA ratio declined to 0.26 times as at 30th September 2021."))


def tile_provisional_9m():
    """TILE (32216, 2019): 'Provisional Financial Statements', nine months, legacy manualDate 1970."""
    return doc(
        page("LANKA TILES PLC", "Provisional Financial Statements", "For the Nine months ended 31st December 2018"),
        page("STATEMENT OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME",
             L((40, "Group"), (80, "Company")),
             L((32, "Nine Months"), (47, "Nine Months"), (72, "Nine Months"), (87, "Nine Months")),
             L((32, "31.12.2018"), (47, "31.12.2017"), (72, "31.12.2018"), (87, "31.12.2017")),
             L((0, "Revenue"), (30, "8,000,000"), (45, "7,000,000"), (70, "6,000,000"), (85, "5,000,000"))),
        page(NOTES, "They have been prepared using the policies of the financial statements for the year ended "
                    "31st March 2018 and are in compliance with LKAS 34."))


TILE_META = meta("Interim Financial Statements for the Quarter Ended 31st December 2018", rc.EPOCH_PLACEHOLDER_MS,
                 "2019-02-14T10:00:00+05:30")


def hpl_malformed_title():
    """HPL (45857): title '3st March 2024' (malformed), manualDate = upload date; Q4 interim."""
    return doc(
        page("HATTON PLANTATIONS PLC - INTERIM FINANCIAL STATEMENT 1"),
        page("INCOME STATEMENT - GROUP",
             L((40, "Unaudited"), (55, "Audited"), (75, "Unaudited"), (90, "Unaudited")),
             L((40, "12 months"), (55, "12 months"), (75, "03 months"), (90, "03 months")),
             L((40, "ended"), (55, "ended"), (75, "ended"), (90, "ended")),
             L((40, "31.03.2024"), (55, "31.03.2023"), (75, "31.03.2024"), (90, "31.03.2023")),
             L((0, "Revenue"), (38, "5,000,000"), (53, "4,000,000"), (73, "1,200,000"), (88, "1,100,000"))),
        page(NOTES))


HPL_META = meta("Interim Financial Statements as of 3st March 2024", ms(2024, 5, 31), "2024-05-31T15:00:00+05:30")


def interim_3m_no_fye():
    """A 3-month interim with no fiscal-year evidence at all."""
    return doc(
        page("XYZ PLC", "INTERIM FINANCIAL STATEMENTS", "FOR THE THREE MONTHS ENDED 30TH JUNE 2026"),
        page("STATEMENT OF PROFIT OR LOSS",
             L((40, "3 months ended 30 June")),
             L((40, "2026"), (55, "2025")),
             L((0, "Revenue"), (38, "1,000"), (53, "900"))),
        page(NOTES))


def scanned():
    return dt.from_pages(["", " ", "\x0c", "  12  "])


ALL_FIXTURES = {
    "acl": (acl_march_fye_q1, ACL_META), "seyb": (seyb_december_fye_q2, SEYB_META),
    "pabc": (pabc_q3_bank, meta("Interim Financial Statements for the nine months ended 30th September 2025", ms(2025, 9, 30))),
    "ctc": (ctc_q4_twelve_months, meta("Interim Financial Statements for the Quarter ended 31st December 2025", ms(2025, 12, 31))),
    "crl": (crl_audited_fs, CRL_META), "tess": (tess_annual_report_with_quarters, TESS_META),
    "ucar": (ucar_errata_25th, meta("Errata to the Interim Financial Statements for the Quarter ended 25th June 2026", ms(2026, 6, 25))),
    "asph": (asph_errata_metadata_only, meta("Errata to the Interim Financial Statements for the Quarter ended 31st March 2026", ms(2026, 3, 31))),
    "dial": (dial_press_release, meta("Q3 Press Release", ms(2021, 11, 12), "2021-11-12T09:00:00+05:30", buckets=("other",))),
    "tile": (tile_provisional_9m, TILE_META), "hpl": (hpl_malformed_title, HPL_META),
    "nofye": (interim_3m_no_fye, meta("Interim Financial Statements for the Quarter ended 30th June 2026")),
    "scanned": (scanned, meta("Financial Statements as of 30.09.2025", ms(2025, 9, 30))),
}


# --- report type -----------------------------------------------------------------------------

def test_march_year_end_q1():
    r = classify(acl_march_fye_q1(), ACL_META)
    assert r.classification_status == "classified"
    assert (r.document_type, r.document_type_status) == ("interim_financial_statements", "confirmed")
    assert (r.period_kind, r.period_start, r.period_end, r.duration_months) == ("duration", "2026-04-01", "2026-06-30", 3)
    assert r.period_status == "confirmed"
    assert (r.fiscal_year_end, r.fiscal_period) == ("03-31", "Q1")
    assert evidence(r, "fye.explicit_year_ended_phrase")[0]["page"] == 5


def test_december_year_end_q2_same_title_different_quarter():
    """Identical CSE title to ACL's ('Quarter ended 30th June 2026') -> Q2, not Q1."""
    r = classify(seyb_december_fye_q2(), SEYB_META)
    assert SEYB_META["file_text"] == ACL_META["file_text"]
    assert (r.duration_months, r.period_start, r.period_end) == (6, "2026-01-01", "2026-06-30")
    assert (r.fiscal_year_end, r.fiscal_period) == ("12-31", "Q2")
    assert evidence(r, "fye.cumulative_period_start")
    pl = periods(r, "profit_or_loss")
    assert {(p["end_date"], p["duration_label"], p["role"]) for p in pl} == {
        ("2026-06-30", "6M", "current"), ("2025-06-30", "6M", "comparative"),
        ("2026-06-30", "quarter", "current"), ("2025-06-30", "quarter", "comparative")}


def test_q3_nine_months_with_change_columns_and_kerned_years():
    r = classify(*[f() if callable(f) else f for f in ALL_FIXTURES["pabc"]])
    assert (r.duration_months, r.period_end, r.fiscal_year_end, r.fiscal_period) == (9, "2025-09-30", "12-31", "Q3")
    pl = periods(r, "profit_or_loss")
    assert {(p["end_date"], p["duration_label"], p["role"]) for p in pl} == {
        ("2025-09-30", "9M", "current"), ("2024-09-30", "9M", "comparative"),
        ("2025-09-30", "quarter", "current"), ("2024-09-30", "quarter", "comparative")}
    cf = periods(r, "cash_flows")
    assert {(p["end_date"], p["role"]) for p in cf} == {("2025-09-30", "current"), ("2024-09-30", "comparative")}
    assert all(p["duration_label"] == "unspecified" for p in cf)       # 'From ... To' spans both columns: not guessed


def test_q4_interim_with_twelve_months_unaudited_vs_audited_comparative():
    r = classify(*[f() if callable(f) else f for f in ALL_FIXTURES["ctc"]])
    assert r.document_type == "interim_financial_statements"
    assert (r.duration_months, r.period_start, r.period_end) == (12, "2025-01-01", "2025-12-31")
    assert evidence(r, "dp.statements_cumulative_period")               # cover said 3 months; statements add 12M
    assert (r.fiscal_year_end, r.fiscal_period) == ("12-31", "Q4")
    ci = periods(r, "comprehensive_income")
    assert {(p["end_date"], p["duration_label"], p["role"], p["audit_status"]) for p in ci} == {
        ("2025-12-31", "3M", "current", "unaudited"), ("2024-12-31", "3M", "comparative", "unaudited"),
        ("2025-12-31", "12M", "current", "unaudited"), ("2024-12-31", "12M", "comparative", "audited")}


def test_audited_financial_statements_annual():
    r = classify(crl_audited_fs(), CRL_META)
    assert (r.document_type, r.document_type_status) == ("audited_financial_statements", "confirmed")
    assert (r.duration_months, r.period_end, r.fiscal_period) == (12, "2025-03-31", "FY")
    assert "partial_text_layer" in r.status_reasons and r.classification_status == "classified"
    assert r.no_text_pages == [6, 7, 8]
    assert periods(r, "profit_or_loss", end_date="2025-03-31")[0]["audit_status"] == "audited"
    assert evidence(r, "dt.annual_statements_with_auditor_report")


def test_annual_report_with_quarterly_tables_stays_annual():
    r = classify(tess_annual_report_with_quarters(), TESS_META)
    assert (r.document_type, r.document_type_status) == ("annual_report", "confirmed")
    assert (r.duration_months, r.period_end, r.fiscal_period) == (12, "2026-03-31", "FY")
    assert r.fiscal_period not in ("Q1", "Q2", "Q3", "Q4")
    quarterly = [p for p in r.statement_periods if p["duration_label"] == "3M"]
    assert {p["end_date"] for p in quarterly} == {"2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"}
    assert periods(r, "profit_or_loss", end_date="2026-03-31", duration_label="12M")[0]["role"] == "current"


def test_errata_is_reissue_with_statements_and_non_standard_period():
    r = classify(*[f() if callable(f) else f for f in ALL_FIXTURES["ucar"]])
    assert (r.document_type, r.document_type_status) == ("errata_or_reissue", "confirmed")
    assert r.underlying_type == "interim_financial_statements"          # errata != non-financial
    assert (r.period_end, r.duration_months, r.period_start) == ("2026-06-25", 6, None)  # not month-end: no derived start
    assert r.fiscal_period is None and r.fiscal_year_end == "12-25"
    assert r.fiscal_period_reason == "non_standard_period_end"
    assert {p["end_date"] for p in r.statement_periods} >= {"2026-06-25", "2025-06-25"}
    assert all(p["start_date"] is None for p in r.statement_periods)


def test_errata_known_only_from_metadata_is_marked_metadata_only():
    r = classify(*[f() if callable(f) else f for f in ALL_FIXTURES["asph"]])
    assert (r.document_type, r.document_type_status) == ("errata_or_reissue", "metadata_only")
    assert (r.underlying_type, r.underlying_type_status) == ("interim_financial_statements", "confirmed")
    assert (r.duration_months, r.fiscal_period) == (12, "Q4")
    ci = periods(r, "comprehensive_income")
    assert {(p["end_date"], p["duration_label"], p["audit_status"]) for p in ci} == {
        ("2026-03-31", "12M", "unaudited"), ("2025-03-31", "12M", "audited"),
        ("2026-03-31", "quarter", "unaudited"), ("2025-03-31", "quarter", "audited")}


def test_amendment_with_cover_letter_is_amended_annual_report():
    d = tess_annual_report_with_quarters()
    d.pages[0] = page("07 September 2026", "Chief Regulatory Officer", "Colombo Stock Exchange", "Dear Madam,",
                      "Please find attached the amended Annual Report 2025/26 incorporating the adjustment.")
    d.text_pages = [i + 1 for i, p in enumerate(d.pages) if dt.page_has_text(p)]
    r = classify(d, meta("Amended Annual Report as at 31st March 2026", ms(2026, 3, 31), buckets=("annual",)))
    assert (r.document_type, r.document_type_status) == ("amendment", "confirmed")
    assert r.underlying_type == "annual_report"                          # not 'a cover letter'
    assert r.fiscal_period == "FY"


def test_document_wins_when_it_calls_an_amended_filing_an_errata():
    """KHC (53067): the CSE title says 'Amended', the document's letter says 'ERRATA NOTICE'."""
    d = tess_annual_report_with_quarters()
    d.pages[0] = page("Dear Madam,", "THE KANDY HOTELS CO. (1938) PLC - ERRATA NOTICE",
                      "We refer to the Annual Report 2025/26, which was uploaded to the CSE website.")
    d.text_pages = [i + 1 for i, p in enumerate(d.pages) if dt.page_has_text(p)]
    r = classify(d, meta("Amended Annual Report as at 31st March 2026", ms(2026, 3, 31), buckets=("annual",)))
    assert (r.document_type, r.document_type_status) == ("errata_or_reissue", "conflicting")
    assert "title_revision" in r.metadata_conflicts
    assert [e["outcome"] for e in evidence(r, "meta.title.revision_word")] == ["conflicts"]


def test_press_release_is_not_financial_statements_despite_q3_title():
    r = classify(*[f() if callable(f) else f for f in ALL_FIXTURES["dial"]])
    assert (r.document_type, r.document_type_status) == ("press_release", "confirmed")
    assert r.statements == [] and r.statement_periods == []              # prose 'balance sheet ...' is not a heading
    assert r.fiscal_period is None and r.fiscal_period_reason == "not_an_interim_document"
    assert (r.period_end, r.duration_months) == ("2021-09-30", 9)


def test_provisional_statements_and_epoch_manual_date():
    r = classify(tile_provisional_9m(), TILE_META)
    assert r.document_type == "interim_financial_statements"
    assert r.period_end == "2018-12-31" and "1970" not in json.dumps(r.to_dict()).replace("1970-01-01)", "")
    assert [e["outcome"] for e in evidence(r, "meta.manual_date.epoch_placeholder")] == ["ignored"]
    assert r.period_status == "confirmed"                                # by the title end date, not manualDate
    cur = periods(r, "comprehensive_income", role="current")
    assert cur and all(p["audit_status"] == "provisional" for p in cur)
    assert (r.fiscal_year_end, r.fiscal_period) == ("03-31", "Q3")
    assert set(periods(r, "comprehensive_income")[0]["scopes"]) == {"group", "company"}


def test_malformed_title_and_upload_date_manual_date_are_ignored():
    r = classify(hpl_malformed_title(), HPL_META)
    assert evidence(r, "meta.title.date_unparseable") and evidence(r, "meta.manual_date.equals_upload_date")
    assert r.period_end == "2024-03-31" and r.period_status == "document_only"
    assert r.metadata_conflicts == []
    assert (r.duration_months, r.fiscal_period) == (12, "Q4")
    pl = periods(r, "profit_or_loss")
    assert {(p["end_date"], p["duration_label"], p["audit_status"], p["role"]) for p in pl} == {
        ("2024-03-31", "12M", "unaudited", "current"), ("2023-03-31", "12M", "audited", "comparative"),
        ("2024-03-31", "3M", "unaudited", "current"), ("2023-03-31", "3M", "unaudited", "comparative")}
    assert all(p["scopes"] == ["group"] for p in pl)


def test_scanned_document_is_unreadable_and_never_classified_from_title():
    r = classify(scanned(), ALL_FIXTURES["scanned"][1])
    assert (r.classification_status, r.document_type, r.underlying_type) == ("unreadable", "unreadable", "unreadable")
    assert r.status_reasons == ["no_text_layer"]
    assert r.period_end is None and r.fiscal_period is None and r.statement_periods == []
    assert r.period_status == "undetermined" and r.document_type_status == "undetermined"
    assert all(e["outcome"] == "ignored" for e in evidence(r, "meta.not_used_document_unreadable"))
    assert evidence(r, "tl.no_text_layer")[0]["source"] == "document"


def test_metadata_conflict_is_kept_and_document_wins():
    m = meta("Interim Financial Statements for the Quarter ended 30th June 2025", ms(2025, 6, 30), buckets=("annual",))
    r = classify(pabc_q3_bank(), m)
    assert r.period_end == "2025-09-30" and r.period_status == "conflicting"
    assert {"title_end_date", "manual_date", "bucket_type"} <= set(r.metadata_conflicts)
    assert r.fiscal_period == "Q3"
    assert {e["outcome"] for e in r.evidence if e["rule_id"].endswith(".compare")} == {"conflicts"}


def test_period_end_after_upload_is_flagged():
    m = dict(ACL_META, uploaded_at="2026-06-01T10:00:00+05:30")
    r = classify(acl_march_fye_q1(), m)
    assert "period_end_after_upload" in r.metadata_conflicts


def test_quarter_ended_title_alone_never_yields_a_quarter():
    r = classify(interim_3m_no_fye(), ALL_FIXTURES["nofye"][1])
    assert r.duration_months == 3 and r.period_end == "2026-06-30"
    assert r.fiscal_year_end is None and r.fiscal_year_end_status == "undetermined"
    assert r.fiscal_period is None and r.fiscal_period_reason == "fiscal_year_end_not_evidenced"
    assert evidence(r, "meta.title.quarter_is_not_duration")


def test_fiscal_year_end_conflict_blocks_quarter():
    d = acl_march_fye_q1()
    d.pages[4] = page(NOTES, "year ended 31 March 2026 policies apply.",
                      "A subsidiary's financial year ended 31 December 2025 has been consolidated.")
    r = classify(d, ACL_META)
    assert r.fiscal_year_end_status == "conflicting" and r.fiscal_period is None
    assert r.fiscal_period_reason == "fiscal_year_end_conflicting"


# --- statement / column periods ----------------------------------------------------------------

def test_point_in_time_balance_sheet_dates_are_instants():
    r = classify(acl_march_fye_q1(), ACL_META)
    fp = periods(r, "financial_position")
    assert fp and all(p["period_kind"] == "instant" and p["duration_months"] is None and p["start_date"] is None for p in fp)
    assert {(p["end_date"], p["role"], p["audit_status"]) for p in fp} == {
        ("2026-06-30", "current", "unaudited"), ("2026-03-31", "comparative", "audited")}
    assert evidence(r, "role.financial_position_prior_fiscal_year_end")


def test_balance_sheet_stays_point_in_time_under_a_duration_title():
    """Some balance-sheet pages repeat the report title ('For the Six Months ended ...');
    their columns are still as-at dates, never durations."""
    block = list(enumerate([
        "STATEMENT OF FINANCIAL POSITION",
        L((30, "For the Six Months ended 30th June 2026")),
        L((30, "As at"), (45, "As at")),
        L((30, "30.06.2026"), (45, "31.12.2025"))]))
    cols, _ = rc.parse_statement_header(block, "financial_position")
    assert [(c["kind"], c["months"], c["label"]) for c in cols] == [("instant", None, None)] * 2


def test_audit_labels_never_bleed_into_the_neighbouring_column():
    """Two-space layout gaps: '(unaudited)' ends right before the next column's date."""
    block = list(enumerate([
        "STATEMENT OF FINANCIAL POSITION",
        L((50, "31.03.26"), (62, "31.03.25")),
        L((49, "(unaudited)"), (62, "(audited)"))]))
    cols, _ = rc.parse_statement_header(block, "financial_position")
    assert [(c["end"].isoformat(), c["audit"]) for c in cols] == [("2026-03-31", "unaudited"), ("2025-03-31", "audited")]


def test_income_and_cash_flow_periods_are_durations():
    r = classify(seyb_december_fye_q2(), SEYB_META)
    for kind in ("profit_or_loss", "cash_flows"):
        ps = periods(r, kind)
        assert ps and all(p["period_kind"] == "duration" for p in ps)
    assert {(p["end_date"], p["duration_label"]) for p in periods(r, "cash_flows")} == {("2026-06-30", "6M"), ("2025-06-30", "6M")}


def test_document_period_and_statement_periods_are_separate():
    r = classify(ctc_q4_twelve_months(), ALL_FIXTURES["ctc"][1])
    assert r.duration_months == 12
    assert len({(p["end_date"], p["duration_label"], p["period_kind"]) for p in r.statement_periods}) >= 5


def test_group_company_pairs_keep_both_scopes():
    r = classify(acl_march_fye_q1(), ACL_META)
    fp = periods(r, "financial_position", end_date="2026-06-30")
    assert fp[0]["scopes"] == ["group", "company"]
    assert set(r.statements[1]["scopes"]) == {"group", "company"}


def test_bank_group_labels_are_partitioned_by_position():
    r = classify(seyb_december_fye_q2(), SEYB_META)
    fp = periods(r, "financial_position")
    assert {p["end_date"]: tuple(p["scopes"]) for p in fp} == {"2026-06-30": ("group", "bank"), "2025-12-31": ("group", "bank")}
    pl = periods(r, "profit_or_loss")
    assert all(p["scopes"] == ["bank"] for p in pl)                      # 'Bank' label beside the heading


def test_scope_is_unknown_when_not_stated():
    r = classify(ctc_q4_twelve_months(), ALL_FIXTURES["ctc"][1])
    assert all(p["scopes"] == [] for p in r.statement_periods)          # absence of 'Group' is not 'company'


def test_comparative_needs_structure_not_just_an_older_date():
    r = classify(tess_annual_report_with_quarters(), TESS_META)
    q = [p for p in r.statement_periods if p["duration_label"] == "3M" and p["end_date"] != "2026-03-31"]
    assert q and all(p["role"] == "unknown" for p in q)                  # older quarters are not 'comparative'


def test_explicit_current_previous_words_set_roles():
    r = classify(pabc_q3_bank(), ALL_FIXTURES["pabc"][1])
    cf = periods(r, "cash_flows")
    assert {p["role"] for p in cf} == {"current", "comparative"}
    assert all(e["rule_id"] == "role.explicit_header_word" for e in r.evidence if e["decision"] == "statement_period"
               and e["page"] == 4)


# --- headings, dates, metadata, redaction ---------------------------------------------------

def test_heading_detection_rules():
    toc = page("TABLE OF CONTENTS", "Statement of Financial Position 4", "Statement of Comprehensive Income 5",
               "Statement of Cash Flows 7", "Notes 8")
    prose = page("The Bank's balance sheet as the Net Debt to EBITDA ratio declined further during the year and",
                 "Balance Sheet and Capital Restructuring The Board is monitoring the Company's position")
    wrapped = page("   STATEMENT OF CHANGES IN 19", "   EQUITY", "   For the year ended 31 March 2026")
    combo = page("STATEMENT OF PROFIT OR LOSS/COMPREHENSIVE INCOME", "For the six months ended 30 September 2024")
    group = page("INCOME STATEMENT - GROUP 1", "For the nine months ended")
    got = rc.find_statement_headings(dt.from_pages([toc, prose, wrapped + "filler text for the text layer threshold",
                                                    combo, group]))
    assert [(k, p, s) for k, p, *_, s in got] == [("changes_in_equity", 3, []), ("comprehensive_income", 4, []),
                                                  ("profit_or_loss", 5, ["group"])]


def test_date_tokens():
    toks = rc.find_date_tokens("30th June 2026  31-Mar-26  31.03.2024  September 30, 2025  3st March 2024  "
                               "25Th June  30th June 30th June  2 025")
    got = [(t.kind, t.year, t.month, t.day) for t in toks]
    assert got == [("full", 2026, 6, 30), ("full", 2026, 3, 31), ("full", 2024, 3, 31), ("full", 2025, 9, 30),
                   ("month_day", None, 6, 25), ("month_day", None, 6, 30), ("month_day", None, 6, 30), ("year", 2025, None, None)]
    assert rc.first_full_date("as of 3st March 2024") is None           # malformed ordinal is not the 3rd
    assert rc.first_full_date("31.02.2024") is None                     # not a calendar date


def test_metadata_interpretation():
    ev = rc._Ev()
    h = rc.interpret_metadata(meta("Interim Financial Statements for the Quarter ended 30th June 2026",
                                   rc.EPOCH_PLACEHOLDER_MS, "2026-08-01T10:00:00+05:30"), ev)
    assert h["title_end"].isoformat() == "2026-06-30" and h["title_duration"] is None
    assert h["manual_date"] is None and h["title_types"] == {"interim_financial_statements"}
    h = rc.interpret_metadata(meta("INTERIM FINANCIAL STATEMENTS FOR THE 09 MONTHS PERIOD ENDED 31ST DECEMBER 2024",
                                   ms(2024, 12, 31), "2025-02-14T10:00:00+05:30"), rc._Ev())
    assert h["title_duration"] == 9 and h["manual_date"].isoformat() == "2024-12-31"


def test_redaction_removes_amounts_but_keeps_dates():
    s = rc.redact(",509 Revenue 1,234,567 (987,654) 35.76 12% 123456 as at 31.03.2024 30-Jun-26 2026 Note 5")
    assert not re.search(r"\d,\d{3}|\d\.\d{2}(?!\.)|\d%|\d{5,}", s.replace("31.03.2024", ""))
    assert "31.03.2024" in s and "30-Jun-26" in s and "2026" in s
    assert len(rc.redact("x" * 500)) == rc.SNIPPET_MAX


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_no_financial_values_and_compact_evidence(name):
    f, m = ALL_FIXTURES[name]
    out = json.dumps(classify(f(), m).to_dict())
    assert not re.search(r"\d{1,3},\d{3}", out), "an amount leaked into the classification"
    for e in classify(f(), m).evidence:
        assert e["snippet"] is None or len(e["snippet"]) <= rc.SNIPPET_MAX


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_deterministic(name):
    f, m = ALL_FIXTURES[name]
    a = classify(f(), m).to_dict()
    b = classify(f(), dict(reversed(list((m or {}).items())))).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["classifier_version"] == rc.CLASSIFIER_VERSION


def test_every_rule_id_is_stable_and_namespaced():
    seen = set()
    for f, m in ALL_FIXTURES.values():
        seen |= {e["rule_id"] for e in classify(f(), m).evidence}
    assert all(re.fullmatch(r"(tl|dt|dp|rv|st|sp|role|audit|fye|fp|meta)\.[a-z0-9_.]+", r) for r in seen), seen


def test_enumerations_are_respected():
    for f, m in ALL_FIXTURES.values():
        r = classify(f(), m)
        assert r.document_type in rc.DOCUMENT_TYPES and r.underlying_type in rc.BASE_TYPES
        for s in (r.document_type_status, r.underlying_type_status, r.period_status, r.fiscal_year_end_status,
                  r.fiscal_period_status):
            assert s in rc.STATUSES
        for p in r.statement_periods:
            assert p["role"] in rc.ROLES and p["audit_status"] in rc.AUDIT_STATUSES
            assert set(p["scopes"]) <= set(rc.SCOPES) and p["statement_kind"] in rc.STATEMENT_KINDS


# --- text layer ----------------------------------------------------------------------------------

def test_text_layer_detection():
    assert not dt.page_has_text("   12   ") and not dt.page_has_text("")
    assert dt.page_has_text("INTERIM FINANCIAL STATEMENTS FOR THE SIX MONTHS ENDED")
    assert dt.split_pages("a\fb\f") == ["a", "b"]


def test_extractor_failure_is_an_error_not_an_empty_document(monkeypatch):
    class Out:
        returncode, stdout, stderr = 1, b"", b"Syntax Error: broken xref"
    monkeypatch.setattr(dt, "extractor_identity", lambda binary="pdftotext": "pdftotext test")
    with pytest.raises(dt.TextExtractionError):
        dt.extract_text("x.pdf", run=lambda *a, **k: Out())


def make_pdf(lines):
    """A minimal valid one-page PDF with a real text layer."""
    content = "BT /F1 11 Tf 40 780 Td 14 TL " + " ".join(f"({l}) '" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class _Resp:
    def __init__(self, body):
        self.body = body

    def to_fetch_response(self):
        h = {"content-type": "application/pdf", "content-length": str(len(self.body)),
             "etag": '"%s"' % hashlib.md5(self.body).hexdigest()}
        return dr.FetchResponse(200, h, iter([self.body]))


class _Fetcher:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def fetch(self, url):
        self.calls.append(url)
        return _Resp(self.routes[url]).to_fetch_response()


PDF_LINES = ["PQR PLC", "INTERIM FINANCIAL STATEMENTS", "FOR THE SIX MONTHS ENDED 30TH SEPTEMBER 2025",
             "Notes: read with the financial statements for the year ended 31 March 2025."]


def _filing(fid, path):
    return {"cse_filing_id": fid, "path": path, "path2": "", "file_text": "Interim Financial Statements for the "
            "Quarter ended 30th September 2025", "manual_date_raw": ms(2025, 9, 30), "uploaded_at": None,
            "authorized_at": None, "source_buckets": ["quarterly"], "source_symbol": "PQR"}


def test_f3_runs_inside_the_f2_lifecycle_and_the_document_is_deleted():
    body = make_pdf(PDF_LINES)
    path = "cmt/upload_report_file/1_2025.pdf"
    fetcher = _Fetcher({dr.CDN_BASE + path: body})
    seen = {}

    def fake_extract(p):
        seen["path"] = p
        assert os.path.isfile(p) and not os.path.realpath(p).startswith(REPO)
        return dt.from_pages([page(*PDF_LINES)], extractor="fake")

    out = cli.run([_filing(7, path)], fetcher=fetcher, extract=fake_extract, request_delay_seconds=0)
    rec = out["records"][0]
    assert rec["retrieval"]["outcome"] == "succeeded" and rec["retrieval"]["cleanup_status"] == "deleted"
    assert not os.path.exists(seen["path"]) and out["leftover_temp_entries"] == [] and out["ok"]
    c = rec["classification"]
    assert c["document_sha256"] == hashlib.sha256(body).hexdigest() and c["cse_filing_id"] == 7
    assert (c["document_type"], c["duration_months"], c["fiscal_period"]) == ("interim_financial_statements", 6, "Q2")
    assert "path" not in json.dumps(c)                                   # no temp path in the result


def test_extraction_failure_is_recorded_and_document_still_deleted():
    body = make_pdf(PDF_LINES)
    path = "cmt/upload_report_file/2_2025.pdf"

    def boom(p):
        raise dt.TextExtractionError("pdftotext exit 1")

    out = cli.run([_filing(8, path)], fetcher=_Fetcher({dr.CDN_BASE + path: body}), extract=boom, request_delay_seconds=0)
    rec = out["records"][0]
    assert rec["retrieval"]["outcome"] == "consumer_failed" and rec["classification"] is None
    assert rec["retrieval"]["cleanup_status"] == "deleted" and out["ok"]


def test_governance_cap_of_twenty():
    with pytest.raises(ValueError):
        cli.run([_filing(i, f"cmt/x/{i}.pdf") for i in range(21)], fetcher=_Fetcher({}), request_delay_seconds=0)


@pytest.mark.skipif(dt.extractor_available() is None, reason="pdftotext not installed")
def test_real_pdftotext_end_to_end():
    body = make_pdf(PDF_LINES)
    path = "cmt/upload_report_file/3_2025.pdf"
    out = cli.run([_filing(9, path)], fetcher=_Fetcher({dr.CDN_BASE + path: body}), request_delay_seconds=0)
    c = out["records"][0]["classification"]
    assert c["text_extractor"].startswith("pdftotext ") and c["text_page_count"] == 1
    assert (c["document_type"], c["period_end"], c["duration_months"]) == ("interim_financial_statements", "2025-09-30", 6)
    assert (c["fiscal_year_end"], c["fiscal_period"]) == ("03-31", "Q2")
    assert out["leftover_temp_entries"] == [] and out["records"][0]["retrieval"]["cleanup_status"] == "deleted"


# --- persistence shape -----------------------------------------------------------------------------

def _migration_columns(table):
    sql = open(os.path.join(REPO, "supabase", "migrations", "0005_report_classification.sql"), encoding="utf-8").read()
    body = re.search(rf"create table {table} \((.*?)\n\);", sql, re.S).group(1)
    return {m.group(1) for m in re.finditer(r"^\s{2}([a-z_0-9]+)\s+(?!check|key)", body, re.M)} - {"constraint", "primary"}


def test_rows_match_migration_columns():
    r = classify(acl_march_fye_q1(), ACL_META).to_dict()
    row, prows, erows = store.classification_rows(r, document_bytes=1234)
    assert set(row) <= _migration_columns("report_document_classifications")
    assert set(prows[0]) <= _migration_columns("report_statement_periods")
    assert set(erows[0]) <= _migration_columns("report_classification_evidence")
    assert set(store.CLASSIFICATION_COLUMNS) == set(row)
    assert all(len(e["snippet"] or "") <= 160 for e in erows)
    assert [e["ordinal"] for e in erows] == list(range(1, len(erows) + 1))
    assert all(o <= len(erows) for p in prows for o in p["evidence_ordinals"])


def test_rows_require_identity():
    with pytest.raises(ValueError):
        store.classification_rows({"cse_filing_id": 1, "document_sha256": None})


def test_migration_is_additive_and_stores_no_documents():
    sql = open(os.path.join(REPO, "supabase", "migrations", "0005_report_classification.sql"), encoding="utf-8").read().lower()
    code = "\n".join(l.split("--")[0] for l in sql.splitlines()).replace("on delete cascade", "")
    assert not re.search(r"\b(alter|drop|delete|update|truncate)\b", code)
    assert not re.search(r"\bbytea\b|storage\.|financial_facts|\bamount\b|\bvalue\b", code)
    assert "text_content" not in code and "page_text" not in code
