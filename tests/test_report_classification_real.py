"""
Stage F3 — acceptance on the 19 REAL filings studied in F3 discovery.

Network test: runs only with F3_REAL_DOCUMENTS=1 (otherwise SKIPPED). It uses
the real F2 lifecycle (one batch of 19 <= the 20-filing cap): each document is
downloaded to a temporary directory, classified, and deleted. The repository
stores only the F1 listing metadata (tests/fixtures/filings/
f3_real_cases_metadata.json) and the EXPECTED semantics below, which were
written from what each document itself states (F3 discovery evidence), not
copied from classifier output. No document text is stored anywhere.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("F3_REAL_DOCUMENTS") != "1",
                                reason="network test: set F3_REAL_DOCUMENTS=1 (downloads 19 CSE documents temporarily)")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "filings", "f3_real_cases_metadata.json")
META = {f["cse_filing_id"]: f for f in json.load(open(FIX, encoding="utf-8"))["filings"]}

# (statement_kind, period_kind, end_date, duration_label, role[, audit_status]) that MUST be present
EXPECT = {
    52157: dict(why="SEYB: December-FYE bank, cover 'For the 06 Months Ended 30th June 2026'; Six Months + Quarter columns",
                document_type="interim_financial_statements", period_kind="duration", period_end="2026-06-30",
                duration_months=6, period_start="2026-01-01", fiscal_year_end="12-31", fiscal_period="Q2",
                include=[("profit_or_loss", "duration", "2026-06-30", "6M", "current"),
                         ("profit_or_loss", "duration", "2025-06-30", "6M", "comparative"),
                         ("profit_or_loss", "duration", "2026-06-30", "quarter", "current"),
                         ("profit_or_loss", "duration", "2025-06-30", "quarter", "comparative"),
                         ("financial_position", "instant", "2026-06-30", None, "current"),
                         ("financial_position", "instant", "2025-12-31", None, "comparative", "audited")],
                scopes={"group", "bank"}),
    52620: dict(why="ACL: March FYE, cover 'THREE MONTHS ENDED 30TH JUNE 2026 UNAUDITED'; notes 'year ended 31 March 2026'",
                document_type="interim_financial_statements", period_kind="duration", period_end="2026-06-30",
                duration_months=3, period_start="2026-04-01", fiscal_year_end="03-31", fiscal_period="Q1",
                include=[("financial_position", "instant", "2026-06-30", None, "current", "unaudited"),
                         ("financial_position", "instant", "2026-03-31", None, "comparative"),
                         ("profit_or_loss", "duration", "2026-06-30", "unspecified", "current")],   # header: 'For the period ended 30 June'
                scopes={"group", "company"}),
    51060: dict(why="CTC Q1: December FYE, '3 months ended 31 March 2026', single entity",
                document_type="interim_financial_statements", period_end="2026-03-31", duration_months=3,
                fiscal_year_end="12-31", fiscal_period="Q1",
                include=[("comprehensive_income", "duration", "2026-03-31", "3M", "current", "unaudited"),
                         ("comprehensive_income", "duration", "2025-03-31", "3M", "comparative"),
                         ("financial_position", "instant", "2025-12-31", None, "comparative", "audited")],
                scopes=set()),
    50553: dict(why="CTC Q4: cover says '3 months ended 31 December 2025' but statements carry 03 and 12 months",
                document_type="interim_financial_statements", period_end="2025-12-31", duration_months=12,
                period_start="2025-01-01", fiscal_year_end="12-31", fiscal_period="Q4",
                include=[("comprehensive_income", "duration", "2025-12-31", "3M", "current"),
                         ("comprehensive_income", "duration", "2025-12-31", "12M", "current", "unaudited"),
                         ("comprehensive_income", "duration", "2024-12-31", "12M", "comparative", "audited"),
                         ("cash_flows", "duration", "2025-12-31", "12M", "current")],
                rules=["dp.statements_cumulative_period"]),
    51712: dict(why="RWSL: 'TWELVE MONTHS ENDED 31 MARCH 2026' interim; Quarter + Twelve Months columns",
                document_type="interim_financial_statements", period_end="2026-03-31", duration_months=12,
                fiscal_year_end="03-31", fiscal_period="Q4",
                include=[("profit_or_loss", "duration", "2026-03-31", "quarter", "current"),
                         ("profit_or_loss", "duration", "2026-03-31", "12M", "current", "unaudited"),
                         ("profit_or_loss", "duration", "2025-03-31", "12M", "comparative", "audited")]),
    48292: dict(why="BLUE: 'For The Six Months Ended 30th September 2024', filed 8.5 months late",
                document_type="interim_financial_statements", period_end="2024-09-30", duration_months=6,
                fiscal_year_end="03-31", fiscal_period="Q2",
                include=[("financial_position", "instant", "2024-09-30", None, "current"),
                         ("financial_position", "instant", "2024-03-31", None, "comparative")]),
    49086: dict(why="PABC: December-FYE bank, 'NINE MONTHS ENDED 30TH SEPTEMBER 2025'; Nine Months + Quarter + Change columns",
                document_type="interim_financial_statements", period_end="2025-09-30", duration_months=9,
                period_start="2025-01-01", fiscal_year_end="12-31", fiscal_period="Q3",
                include=[("profit_or_loss", "duration", "2025-09-30", "9M", "current"),
                         ("profit_or_loss", "duration", "2024-09-30", "9M", "comparative"),
                         ("profit_or_loss", "duration", "2025-09-30", "quarter", "current"),
                         ("profit_or_loss", "duration", "2024-09-30", "quarter", "comparative"),
                         ("financial_position", "instant", "2024-12-31", None, "comparative", "audited"),
                         ("cash_flows", "duration", "2025-09-30", "unspecified", "current"),
                         ("cash_flows", "duration", "2024-09-30", "unspecified", "comparative")]),
    47478: dict(why="DIMO: 9 months to 31 Dec 2024, March FYE; statements also show audited 'Year ended 31-03-2024'",
                document_type="interim_financial_statements", period_end="2024-12-31", duration_months=9,
                fiscal_year_end="03-31", fiscal_period="Q3",
                include=[("comprehensive_income", "duration", "2024-12-31", "9M", "current", "unaudited"),
                         ("comprehensive_income", "duration", "2023-12-31", "9M", "comparative"),
                         ("comprehensive_income", "duration", "2024-03-31", "12M", "unknown", "audited")]),   # prior FY: no current 12M to correspond to
    49117: dict(why="LLUB: fully scanned (no text layer)", unreadable=True),
    48576: dict(why="CRL: audited financial statements, 26/80 pages have text", document_type="audited_financial_statements",
                period_end="2025-03-31", duration_months=12, fiscal_year_end="03-31", fiscal_period="FY",
                reasons={"partial_text_layer"},
                include=[("profit_or_loss", "duration", "2025-03-31", "12M", "current"),
                         ("financial_position", "instant", "2024-03-31", None, "comparative")]),
    53096: dict(why="TESS: '2025/2026 ANNUAL REPORT' with a quarterly analysis inside", document_type="annual_report",
                period_end="2026-03-31", duration_months=12, fiscal_year_end="03-31", fiscal_period="FY",
                include=[("profit_or_loss", "duration", "2026-03-31", "12M", "current"),
                         ("profit_or_loss", "duration", "2025-03-31", "12M", "comparative"),
                         ("profit_or_loss", "duration", "2025-06-30", "3M", "unknown"),
                         ("profit_or_loss", "duration", "2025-09-30", "3M", "unknown"),
                         ("profit_or_loss", "duration", "2025-12-31", "3M", "unknown")]),
    53067: dict(why="KHC: CSE title 'Amended Annual Report'; the document's letter says 'ERRATA NOTICE'",
                document_type="errata_or_reissue", document_type_status="conflicting", underlying_type="annual_report",
                period_end="2026-03-31", duration_months=12, fiscal_period="FY", conflicts={"title_revision"}),
    53129: dict(why="UCAR: errata letter + reissued interim; periods end on the 25th (52/53-week style)",
                document_type="errata_or_reissue", document_type_status="confirmed",
                underlying_type="interim_financial_statements", period_end="2026-06-25", duration_months=6,
                period_start=None, fiscal_period=None,
                include=[("cash_flows", "duration", "2026-06-25", "6M", "current"),
                         ("cash_flows", "duration", "2025-06-25", "6M", "comparative")]),
    52860: dict(why="ASPH: errata known only from the CSE title; document is a full Q4 interim",
                document_type="errata_or_reissue", document_type_status="metadata_only",
                underlying_type="interim_financial_statements", period_end="2026-03-31", duration_months=12,
                fiscal_year_end="03-31", fiscal_period="Q4",
                include=[("comprehensive_income", "duration", "2026-03-31", "12M", "current", "unaudited"),
                         ("comprehensive_income", "duration", "2025-03-31", "12M", "comparative", "audited")]),
    45857: dict(why="HPL: title '3st March 2024' (malformed), manualDate = upload date; 12 + 03 months to 31.03.2024",
                document_type="interim_financial_statements", period_end="2024-03-31", duration_months=12,
                period_status="document_only", fiscal_year_end="03-31", fiscal_period="Q4",
                rules=["meta.title.date_unparseable", "meta.manual_date.equals_upload_date"],
                include=[("profit_or_loss", "duration", "2024-03-31", "12M", "current", "unaudited"),
                         ("profit_or_loss", "duration", "2023-03-31", "12M", "comparative", "audited")]),
    32216: dict(why="TILE (2019): 'Provisional Financial Statements For the Nine months ended 31st December 2018'; manualDate 1970",
                document_type="interim_financial_statements", period_end="2018-12-31", duration_months=9,
                fiscal_year_end="03-31", fiscal_period="Q3", rules=["meta.manual_date.epoch_placeholder"],
                include=[("comprehensive_income", "duration", "2018-12-31", "9M", "current", "provisional")]),
    49729: dict(why="JFP: full-year consolidated statements in the 'other' bucket, fully scanned", unreadable=True),
    39390: dict(why="DIAL: 'Q3 Press Release' — results announcement, no primary statements",
                document_type="press_release", period_end="2021-09-30", duration_months=9, fiscal_period=None,
                no_statement_periods=True),
    49384: dict(why="COMB: December-FYE bank, nine months + quarter, Group and Bank statements",
                document_type="interim_financial_statements", period_end="2025-09-30", duration_months=9,
                fiscal_year_end="12-31", fiscal_period="Q3",
                include=[("profit_or_loss", "duration", "2025-09-30", "9M", "current"),
                         ("profit_or_loss", "duration", "2024-09-30", "9M", "comparative"),
                         ("profit_or_loss", "duration", "2025-09-30", "quarter", "current"),
                         ("profit_or_loss", "duration", "2024-09-30", "quarter", "comparative"),
                         ("financial_position", "instant", "2024-12-31", None, "comparative", "audited")],
                scopes={"group", "bank"}),
}


@pytest.fixture(scope="module")
def run():
    from worker import classify_filing_documents as cli
    filings = [META[i] for i in EXPECT]
    assert len(filings) <= 20
    out = cli.run(filings, request_delay_seconds=1.0)
    if os.environ.get("F3_REAL_OUTPUT"):          # optional: keep the (value-free) classification report
        with open(os.environ["F3_REAL_OUTPUT"], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=str)
    return out, {r["retrieval"]["cse_filing_id"]: r for r in out["records"]}


def test_lifecycle_left_nothing_behind(run):
    out, _ = run
    assert out["ok"] and out["leftover_temp_entries"] == [] and out["outcome_counts"] == {"succeeded": len(EXPECT)}
    assert all(r["retrieval"]["cleanup_status"] == "deleted" for r in out["records"])


@pytest.mark.parametrize("fid", sorted(EXPECT))
def test_real_case_semantics(run, fid):
    _, by_id = run
    exp, c = EXPECT[fid], by_id[fid]["classification"]
    assert c is not None, f"{fid}: no classification ({by_id[fid]['retrieval']['consumer_error']})"
    if exp.get("unreadable"):
        assert (c["classification_status"], c["document_type"], c["underlying_type"]) == ("unreadable",) * 3 \
            or (c["classification_status"], c["document_type"]) == ("unreadable", "unreadable")
        assert c["period_end"] is None and c["duration_months"] is None and c["fiscal_period"] is None
        assert c["statement_periods"] == [] and c["text_page_count"] == 0
        return
    for key in ("document_type", "document_type_status", "underlying_type", "period_kind", "period_end",
                "duration_months", "period_start", "period_status", "fiscal_year_end", "fiscal_period"):
        if key in exp:
            assert c[key] == exp[key], f"{fid} {key}: expected {exp[key]!r}, got {c[key]!r} — {exp['why']}"
    got = {(p["statement_kind"], p["period_kind"], p["end_date"], p["duration_label"], p["role"], p["audit_status"])
           for p in c["statement_periods"]}
    for want in exp.get("include", []):
        assert any(g[:len(want)] == want for g in got), f"{fid}: missing statement period {want} — {exp['why']}"
    if "scopes" in exp:
        assert {s for p in c["statement_periods"] for s in p["scopes"]} == exp["scopes"]
    for rule in exp.get("rules", []):
        assert any(e["rule_id"] == rule for e in c["evidence"]), f"{fid}: no evidence for {rule}"
    if "conflicts" in exp:
        assert exp["conflicts"] <= set(c["metadata_conflicts"])
    if "reasons" in exp:
        assert exp["reasons"] <= set(c["status_reasons"])
    if exp.get("no_statement_periods"):
        assert c["statement_periods"] == []


def test_ucar_nonstandard_period_gets_no_fabricated_quarter(run):
    c = run[1][53129]["classification"]
    assert c["fiscal_period"] is None and c["fiscal_period_reason"] in (
        "non_standard_period_end", "fiscal_year_end_conflicting")
    assert all(p["start_date"] is None for p in c["statement_periods"])


def test_tess_older_quarters_are_not_comparatives(run):
    c = run[1][53096]["classification"]
    older = [p for p in c["statement_periods"] if p["duration_label"] == "3M" and p["end_date"] < "2026-03-31"]
    assert older and all(p["role"] == "unknown" for p in older)


def test_tile_1970_manual_date_never_becomes_a_period(run):
    c = run[1][32216]["classification"]
    assert "1970" not in (c["period_end"] or "") and all("1970" not in p["end_date"] for p in c["statement_periods"])


def test_unreadable_cases_ignore_the_cse_title(run):
    for fid in (49117, 49729):
        c = run[1][fid]["classification"]
        assert c["document_type_status"] == "undetermined" and c["period_status"] == "undetermined"
        assert all(e["outcome"] in ("ignored", "note", "supports") for e in c["evidence"])
        assert any(e["rule_id"] == "tl.no_text_layer" for e in c["evidence"])


def test_real_outputs_are_compact_and_value_free(run):
    out, _ = run
    blob = json.dumps([r["classification"] for r in out["records"]])
    assert not re.search(r"\d{1,3},\d{3}", blob), "amount-like token in output"
    assert "cse_f2_" not in blob and "AppData" not in blob and "/tmp/" not in blob
    for r in out["records"]:
        for e in r["classification"]["evidence"]:
            assert e["snippet"] is None or len(e["snippet"]) <= 160
            assert re.fullmatch(r"(tl|dt|dp|rv|st|sp|role|audit|fye|fp|meta)\.[a-z0-9_.]+", e["rule_id"])
            assert e["source"] in ("document", "metadata") and (e["source"] == "metadata" or e["source_field"] is None)
