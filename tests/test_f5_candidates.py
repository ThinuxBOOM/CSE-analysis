"""
Stage F5 — F4 -> F5 candidate construction (worker/financial_candidates.py), invariants I-1..I-14
at the pure level (the database-level guards are in test_f5_postgres.py).

Two kinds of input: REAL F4 output (extract_from_words over the synthetic word
layers of the F4 tests - no Poppler needed), and hand-built F4 models for edge
cases F4 fixtures do not reach.
"""
import copy
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from worker import financial_candidates as f5, financial_concepts as fc, statement_extraction as se
from worker.financial_values import parse_value
from test_statement_extraction import CTC_PERIODS, EXTRACTOR, L, R, classification, doc, page, pl_rows, sp  # noqa: E402

SHA = "ab" * 32
PATH = "cmt/upload_report_file/369_1762944976777.pdf"
FILING = {"cse_filing_id": 1, "path": PATH, "uploaded_at": "2025-11-12T10:56:16.777000+00:00",
          "uploaded_at_raw": "12 Nov 2025 04:26:16 PM", "authorized_at": "2025-11-12T10:59:24.124000+00:00",
          "authorized_at_raw": "12 Nov 2025 04:29:24 PM", "first_seen_at": "2026-09-24T01:00:00+00:00",
          "file_text": "Interim Financial Statements for the Quarter ended 30th September 2025", "manual_date_raw": 1759170600000}
RETRIEVAL = {"last_modified": "Wed, 12 Nov 2025 10:56:16 GMT", "retrieved_at": "2026-09-27T05:00:00+00:00"}


def cls_(periods=CTC_PERIODS, **kw):
    c = classification(periods, period_end=kw.pop("period_end", "2025-12-31"), period_status=kw.pop("period_status", "document_only"))
    c.update({"text_extractor": "pdftotext 24.02.0 (poppler) -layout", "evidence": kw.pop("evidence", []),
              "fiscal_year_end": None, "fiscal_year_end_basis": "none", "fiscal_year_end_status": "undetermined"})
    c.update(kw)
    return c


def real(rows=None, cls=None):
    cls = cls or cls_()
    ext = se.extract_from_words(doc(page(rows or pl_rows())), cls, filing_id=1, sha256=SHA)
    return ext, cls


# --- hand-built F4 models ----------------------------------------------------------------------------

def col(index, kind="duration", end="2026-03-31", months=3, *, role="current", basis="f3.statement_period", scope=None,
        audit="unknown", header="", column_kind="period", status="resolved"):
    label = None if kind != "duration" else (f"{months}M" if months else "unspecified")
    start = None
    return se.ColumnModel(index, column_kind, status, 100.0 * index, 100.0 * index + 60, 100.0 * index + 60, header,
                          header.lower(), period_kind=kind, start_date=start, end_date=end, duration_months=months,
                          duration_label=label, period_basis="f3.header_parser" if kind else None, role=role,
                          role_basis=basis if role != "unknown" else None, scope=scope, audit_status=audit)


def row(index, label, section=None):
    return se.RowModel(index, 1, label, se._norm_label(label), 10.0 * index, 10.0 * index + 8, 1, False, section, None,
                       "values", "ok")


def cell(kind, st_index, r, c, raw, *, status="extracted", scale=1000, currency="LKR", reasons=()):
    pv = parse_value(raw)
    return se.ExtractedCell(1, SHA, 1, kind, st_index, c.scope, r.index, r.label_raw, r.label_normalized,
                            r.section_label_raw, c.index, c.column_kind, c.header_raw, None, c.role, c.audit_status, raw,
                            pv.parsed, pv.representation_class, scale, "header_zone", currency,
                            {"page": 1, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0, "page_width": 595, "page_height": 842},
                            EXTRACTOR, "f4.1", status, list(reasons), "agree", "high" if status == "extracted" else "low", [])


def stmt(index, kind, cols, rows, values, *, currency="LKR", scope=None):
    """values: {(row_index, col_index): raw}"""
    by_r, by_c = {r.index: r for r in rows}, {c.index: c for c in cols}
    cells = []
    for (ri, ci), raw in values.items():
        if isinstance(raw, tuple):
            for v in raw:
                cells.append(cell(kind, index, by_r[ri], by_c[ci], v, status="unresolved", currency=currency,
                                  reasons=["multiple_values_in_column"]))
        else:
            cells.append(cell(kind, index, by_r[ri], by_c[ci], raw, currency=currency))
    return se.StatementExtraction(index, kind, 1, [1], None, scope, "STATEMENT", 1, None, "extracted", [], 1000, "resolved",
                                  "header_zone", [{"zone": "header", "page": 1, "text": "Rs.'000", "magnitude": 1000,
                                                   "strength": "strong"}], currency, cols, rows, cells)


def ext_of(*statements):
    return se.DocumentExtraction(1, SHA, EXTRACTOR, "f4.1", "f3.1", "interim_financial_statements", "extracted", [], 1,
                                 [{"page": 1, "status": "text_native"}], list(statements))


def cands(result, **match):
    return [c for c in result["candidates"] if all(c[k] == v for k, v in match.items())]


def columns_by_index(result, statement_index=0):
    return {c["column_index"]: c for c in result["columns"] if c["statement_index"] == statement_index}


# --- I-1 period_kind / period_class --------------------------------------------------------------------

def test_i1_candidate_period_kind_equals_concept_period_kind_on_real_f4_output():
    ext, cls = real()
    res = f5.build(ext, cls)
    mapped = [c for c in res["candidates"] if c["concept_key"]]
    assert mapped
    for c in mapped:
        assert c["period_kind"] == fc.BY_KEY[c["concept_key"]].period_kind
        assert (c["period_class"] is None) == (c["period_kind"] == "instant")


def test_i1_instant_and_duration_concepts():
    fp = stmt(0, "financial_position", [col(0, "instant", "2026-03-31", None), col(1, "instant", "2025-03-31", None, role="comparative")],
              [row(0, "Total assets")], {(0, 0): "5,000", (0, 1): "4,000"})
    pl = stmt(1, "profit_or_loss", [col(0, "duration", "2026-03-31", 3), col(1, "duration", "2026-03-31", 12)],
              [row(0, "Revenue")], {(0, 0): "100", (0, 1): "400"})
    res = f5.build(ext_of(fp, pl), cls_())
    ta = cands(res, concept_key="total_assets")
    assert {(c["period_kind"], c["period_class"]) for c in ta} == {("instant", None)}
    rev = cands(res, concept_key="revenue")
    assert sorted(c["period_class"] for c in rev) == ["12m", "3m"] and {c["period_kind"] for c in rev} == {"duration"}
    cols = columns_by_index(res, 0)
    assert cols[0]["period_kind"] == "instant" and cols[0]["period_class"] is None


def test_i1_duration_concept_in_an_instant_column_is_not_a_candidate():
    cf = stmt(0, "cash_flows", [col(0, "instant", "2026-03-31", None)], [row(0, "Net cash from operating activities")],
              {(0, 0): "1,000"})
    res = f5.build(ext_of(cf), cls_())
    assert res["candidates"] == [] and res["run"]["counts"]["skipped_cells_period_kind_mismatch"] == 1


def test_i1_cash_at_end_of_period_is_an_instant_at_the_duration_column_end():
    cf = stmt(0, "cash_flows", [col(0, "duration", "2026-03-31", 12)],
              [row(0, "Net cash from operating activities"), row(1, "Cash and cash equivalents at the end of the year")],
              {(0, 0): "1,000", (1, 0): "2,500"})
    res = f5.build(ext_of(cf), cls_())
    [end] = cands(res, concept_key="cash_at_end_of_period")
    assert (end["period_kind"], end["period_class"], end["period_derivation"]) == ("instant", None, "duration_column_end")
    [ops] = cands(res, concept_key="net_cash_from_operating_activities")
    assert (ops["period_kind"], ops["period_class"], ops["period_derivation"]) == ("duration", "12m", "column")


@pytest.mark.parametrize("months,expected", [(3, "3m"), (6, "6m"), (9, "9m"), (12, "12m"), (4, "other_4m"),
                                             (15, "other_15m"), (None, "unspecified")])
def test_period_class_derivation(months, expected):
    assert f5.period_class("duration", months) == expected
    assert f5.period_class("instant", months) is None
    assert f5.period_class(None, months) is None


def test_unspecified_duration_candidate_is_unresolved():
    pl = stmt(0, "profit_or_loss", [col(0, "duration", "2026-03-31", None)], [row(0, "Revenue")], {(0, 0): "100"})
    [c] = f5.build(ext_of(pl), cls_())["candidates"]
    assert (c["period_class"], c["candidate_status"]) == ("unspecified", "unresolved")


def test_undated_period_column_gives_no_candidate():
    pl = stmt(0, "profit_or_loss", [col(0, None, None, None, role="unknown")], [row(0, "Revenue")], {(0, 0): "100"})
    res = f5.build(ext_of(pl), cls_())
    assert res["candidates"] == [] and res["run"]["counts"]["skipped_cells_period_unresolved"] == 1


def test_period_class_never_comes_from_title_manual_date_or_f3_document_period():
    ext, cls = real()
    base = f5.build(ext, cls, filing=FILING)
    meta = dict(FILING, file_text="Interim Financial Statements for the Nine Months ended 31st December 2025",
                manual_date_raw=-19800000)
    other = dict(cls, period_end="2024-06-30", duration_months=6, duration_label="6M", period_kind="duration")
    again = f5.build(ext, other, filing=meta)
    assert [c["period_class"] for c in base["candidates"]] == [c["period_class"] for c in again["candidates"]]
    assert {c["period_class"] for c in base["candidates"]} == {"3m", "12m"}


# --- I-7 fiscal labels ------------------------------------------------------------------------------------

def _fiscal(status="document_only", basis="documented", fye_status="document_only", fye="03-31"):
    c = cls_(period_status=status)
    c.update({"fiscal_year_end_basis": basis, "fiscal_year_end_status": fye_status,
              "fiscal_year_end": fye if basis in ("documented", "hostile") else None,
              "fiscal_year_end_inferred": fye if basis == "inferred_only" else None})
    return c


@pytest.mark.parametrize("end,months,label", [("2025-06-30", 3, "Q1"), ("2025-09-30", 3, "Q2"), ("2025-12-31", 3, "Q3"),
                                              ("2026-03-31", 3, "Q4"), ("2025-09-30", 6, "H1"), ("2026-03-31", 6, "H2"),
                                              ("2025-12-31", 9, "9M"), ("2026-03-31", 12, "FY"), ("2025-12-31", 12, None),
                                              ("2025-11-30", 3, None), ("2025-12-31", 6, None)])
def test_fiscal_label_from_documented_fiscal_year_end(end, months, label):
    assert f5.fiscal_label(_fiscal(), "duration", end, months) == label


@pytest.mark.parametrize("kw", [dict(status="metadata_only"), dict(status="conflicting"), dict(status="undetermined"),
                                dict(basis="inferred_only"), dict(basis="conflicting"), dict(basis="none"),
                                dict(fye_status="metadata_only"), dict(fye_status="conflicting")])
@pytest.mark.parametrize("hostile", [False, True])
def test_untrusted_f3_periods_or_undocumented_fye_never_give_fiscal_labels(kw, hostile):
    c = _fiscal(**kw)
    if hostile:                                     # an MM-DD present despite a non-documented basis is still ignored
        c["fiscal_year_end"] = "03-31"
    assert f5.fiscal_label(c, "duration", "2025-12-31", 3) is None
    pl = stmt(0, "profit_or_loss", [col(0, "duration", "2025-12-31", 3)], [row(0, "Revenue")], {(0, 0): "100"})
    assert {x["fiscal_label"] for x in f5.build(ext_of(pl), c)["columns"]} == {None}


def test_instants_have_no_fiscal_label_and_non_month_end_fye_gives_none():
    assert f5.fiscal_label(_fiscal(), "instant", "2025-12-31", None) is None
    assert f5.fiscal_label(_fiscal(fye="03-15"), "duration", "2025-12-31", 3) is None


# --- I-2 / I-3 values, sign, scale, currency ----------------------------------------------------------------

def test_i2_values_are_exactly_f4s_printed_parse_on_real_output():
    ext, cls = real()
    res = f5.build(ext, cls)
    by = {(c.statement_index, c.row_index, c.column_index): c for c in ext.cells}
    for c in res["candidates"]:
        src = by[(c["statement_index"], c["row_index"], c["column_index"])]
        assert (c["raw_value"], c["parsed_value"], c["representation_class"], c["reported_scale"], c["reported_currency"]) \
            == (src.raw_value, src.parsed_value, src.representation_class, src.scale, src.currency)
    [cos] = cands(res, concept_key="cost_of_sales", column_index=0)
    assert (cos["raw_value"], cos["parsed_value"], cos["sign_as_printed"]) == ("(40,000)", Decimal("-40000"), "negative")
    assert cos["reported_scale"] == 1_000_000            # carried, never applied


def test_i2_no_sign_flip_no_dash_to_zero_no_scaled_amount():
    pl = stmt(0, "profit_or_loss", [col(0), col(1, role="comparative", end="2025-03-31")],
              [row(0, "Cost of sales"), row(1, "Finance costs"), row(2, "Income tax expense")],
              {(0, 0): "40,000", (0, 1): "(38,000)", (1, 0): "-", (1, 1): "—", (2, 0): "(0)", (2, 1): "-1,234"})
    res = f5.build(ext_of(pl), cls_())
    got = {(c["concept_key"], c["column_index"]): (c["raw_value"], c["parsed_value"], c["representation_class"],
                                                   c["sign_as_printed"]) for c in res["candidates"]}
    assert got[("cost_of_sales", 0)] == ("40,000", Decimal("40000"), "numeric", "positive")      # expense printed positive stays positive
    assert got[("cost_of_sales", 1)] == ("(38,000)", Decimal("-38000"), "parenthesised_negative", "negative")
    assert got[("finance_costs", 0)] == ("-", None, "dash_nil", "nil")                            # never 0
    assert got[("finance_costs", 1)] == ("—", None, "dash_nil", "nil")
    assert got[("income_tax_expense", 0)][2:] == ("negative_zero", "negative_zero")
    assert got[("income_tax_expense", 1)] == ("-1,234", Decimal("-1234"), "minus_negative", "negative")
    for c in res["candidates"]:
        assert not any(("scaled" in k or "normalis" in k or "normaliz" in k or k == "amount") for k in c)
        assert c["reported_scale"] == 1000


def test_i2_a_tampered_parse_is_refused():
    pl = stmt(0, "profit_or_loss", [col(0)], [row(0, "Revenue")], {(0, 0): "100"})
    pl.cells[0].parsed_value = Decimal("100000")        # e.g. somebody applied the scale
    with pytest.raises(f5.CandidateBuildError):
        f5.build(ext_of(pl), cls_())


@pytest.mark.parametrize("currency", [None, "LKR", "USD"])
def test_i3_currency_is_carried_never_defaulted_or_converted(currency):
    pl = stmt(0, "profit_or_loss", [col(0)], [row(0, "Revenue")], {(0, 0): "100"}, currency=currency)
    res = f5.build(ext_of(pl), cls_())
    assert [c["reported_currency"] for c in res["candidates"]] == [currency]
    assert res["statements"][0]["currency"] == currency


# --- I-4 roles --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,trusted", [("confirmed", True), ("document_only", True), ("metadata_only", False),
                                            ("conflicting", False), ("undetermined", False), (None, False)])
@pytest.mark.parametrize("basis", ["f3.statement_period", "f4.explicit_header_word", "f4.mirrored_f3_role_rule"])
def test_i4_role_trust_only_under_document_evidenced_f3_period(status, trusted, basis):
    assert f5.role_trust({"period_status": status}, "current", basis) == ("trusted" if trusted else "untrusted")
    assert f5.role_trust({"period_status": status}, "unknown", None) == "untrusted"
    assert f5.role_trust({"period_status": "document_only"}, "current", None) == "untrusted"


def test_i4_role_trust_on_real_f4_output():
    ext, cls = real(cls=cls_(period_status="document_only"))
    assert {c["role_trust"] for c in f5.build(ext, cls)["columns"]} == {"trusted"}
    ext, cls = real(cls=cls_(period_status="metadata_only"))
    cols = f5.build(ext, cls)["columns"]
    assert {(c["role"], c["role_trust"]) for c in cols} == {("unknown", "untrusted")}


# --- I-5 audit -------------------------------------------------------------------------------------------

COVER_UNAUDITED = [{"decision": "audit_status", "rule_id": "audit.document_cover_statement", "source": "document"}]
AUDITOR_REPORT = [{"decision": "audit_status", "rule_id": "audit.auditor_report_document", "source": "document"}]


def _audit(label, header, *, status="document_only", evidence=(), sp_role="current", sp_audit=None):
    c = col(0, "duration", "2026-03-31", 3, audit=label, header=header)
    periods = [sp("profit_or_loss", "2026-03-31", 3, "3M", sp_role, sp_audit or label)]
    return f5.audit_fields(cls_(periods, period_status=status, evidence=list(evidence)), "profit_or_loss", c)


def test_i5_header_word_label_trusted_under_document_evidenced_period():
    assert _audit("unaudited", "31.03.2026 Unaudited") == ("unaudited", "column_header_word", "trusted", "f5.audit.v1")


@pytest.mark.parametrize("status", ["metadata_only", "conflicting", "undetermined"])
def test_i5_label_preserved_but_untrusted_under_untrusted_period(status):
    assert _audit("unaudited", "31.03.2026 Unaudited", status=status)[:3] == ("unaudited", "column_header_word", "untrusted")
    assert _audit("audited", "", status=status, evidence=AUDITOR_REPORT)[:3] == ("audited", "f3_cover_page_inference", "untrusted")


def test_i5_unknown_label_has_no_source_and_is_never_trusted():
    assert _audit("unknown", "Unaudited") == ("unknown", "none", "untrusted", "f5.audit.v1")


def test_i5_cover_page_inference_stays_distinguishable():
    assert _audit("unaudited", "31.03.2026", evidence=COVER_UNAUDITED)[:3] == ("unaudited", "f3_cover_page_inference", "trusted")
    assert _audit("audited", "31.03.2026", evidence=AUDITOR_REPORT)[:2] == ("audited", "f3_cover_page_inference")
    assert _audit("provisional", "", evidence=COVER_UNAUDITED)[1] == "f3_cover_page_inference"


def test_i5_cover_inference_is_never_upgraded_by_a_header_word():
    # the header ALSO prints 'Unaudited' (e.g. a neighbour's wide header leaking in): the weaker source is kept
    assert _audit("unaudited", "Unaudited 31.03.2026", evidence=COVER_UNAUDITED)[1] == "f3_cover_page_inference"


def test_i5_cover_rule_only_explains_current_periods_with_the_same_label():
    # comparative column: F3's cover rule never applies there -> the header word is the source
    assert _audit("audited", "Audited", evidence=COVER_UNAUDITED + AUDITOR_REPORT, sp_role="comparative")[1] == "column_header_word"
    # cover says unaudited, the column says audited: not explainable by the cover
    assert _audit("audited", "Audited", evidence=COVER_UNAUDITED)[1] == "column_header_word"


def test_i5_label_from_f3_statement_period_without_header_word_or_cover():
    assert _audit("audited", "31.03.2026")[:2] == ("audited", "f3_statement_period")


def test_i5_trusted_means_only_the_v1_provenance_rule():
    # the trust decision reads nothing but the label and F3's period status - not the source
    for src_kw in (dict(evidence=COVER_UNAUDITED), dict()):
        for header in ("Unaudited", ""):
            label, source, trust, rule = _audit("unaudited", header, **src_kw)
            assert trust == "trusted" and rule == "f5.audit.v1"
            assert _audit("unaudited", header, status="conflicting", **src_kw)[2] == "untrusted"


# --- I-6 scope ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reported,has_group,expected", [
    ("group", False, "consolidated"), ("group", True, "consolidated"), ("company", True, "separate"),
    ("bank", True, "separate"), ("company", False, "unresolved"), ("bank", False, "unresolved"),
    (None, True, "unresolved"), (None, False, "unresolved")])
def test_i6_canonical_scope_rule(reported, has_group, expected):
    assert f5.canonical_scope(reported, has_group)[0] == expected


def test_i6_bank_alone_is_not_separate_and_unstated_is_not_defaulted():
    bank = stmt(0, "financial_position", [col(0, "instant", "2026-03-31", None, scope="bank")], [row(0, "Total assets")],
                {(0, 0): "1"})
    both = stmt(1, "financial_position", [col(0, "instant", "2026-03-31", None, scope="group"),
                                          col(1, "instant", "2026-03-31", None, scope="bank")],
                [row(0, "Total assets")], {(0, 0): "2", (0, 1): "1"})
    none = stmt(2, "financial_position", [col(0, "instant", "2026-03-31", None)], [row(0, "Total assets")], {(0, 0): "1"})
    res = f5.build(ext_of(bank, both, none), cls_())
    got = {(c["statement_index"], c["column_index"]): (c["reported_scope"], c["canonical_scope"]) for c in res["columns"]}
    assert got == {(0, 0): ("bank", "unresolved"), (1, 0): ("group", "consolidated"), (1, 1): ("bank", "separate"),
                   (2, 0): ("unstated", "unresolved")}


# --- I-8 ambiguity ------------------------------------------------------------------------------------------

def test_i8_ambiguous_label_gives_no_concept_and_an_explicit_list():
    ext, cls = real()
    res = f5.build(ext, cls)
    eps = [c for c in res["candidates"] if c["mapping_status"] == "ambiguous"]
    assert len(eps) == 4
    for c in eps:
        assert c["concept_key"] is None and c["ambiguous_concepts"] == ["eps_basic", "eps_diluted"]
        assert c["candidate_status"] == "ambiguous" and c["value_type"] is None
    assert res["run"]["counts"]["ambiguous_rows"] == 1


def test_i8_bank_document_never_maps_generic_revenue():
    pl = stmt(0, "profit_or_loss", [col(0)], [row(0, "Interest income"), row(1, "Net interest income"), row(2, "Revenue"),
                                              row(3, "Gross income")],
              {(0, 0): "10", (1, 0): "5", (2, 0): "15", (3, 0): "20"})
    res = f5.build(ext_of(pl), cls_())
    assert res["run"]["template"] == "bank_finance"
    assert {c["concept_key"] for c in res["candidates"]} == {"interest_income", "net_interest_income", "gross_income"}
    assert [r["label_raw"] for r in res["rows"]] == ["Interest income", "Net interest income", "Gross income"]


# --- I-12 nothing unmapped --------------------------------------------------------------------------------------

def test_i12_only_mapped_rows_period_columns_and_candidate_statements_are_kept():
    rows = pl_rows(extra=[(206, [L("Other comprehensive income", 40), R("1", 300), R("2", 370), R("3", 460), R("4", 530)])])
    ext, cls = real(rows=rows)
    pos = stmt(1, "financial_position", [col(0, "instant", "2026-03-31", None)], [row(0, "Property, plant and equipment")],
               {(0, 0): "9"})
    ext.statements.append(pos)
    res = f5.build(ext, cls)
    assert [s["statement_index"] for s in res["statements"]] == [0]
    labels = {r["label_raw"] for r in res["rows"]}
    assert "Other operating income" not in labels and "Other comprehensive income" not in labels
    blob = f5.canonical_json(res)
    assert "Other operating income" not in blob and "Property, plant" not in blob
    assert all(len(c["header_raw"] or "") <= 160 for c in res["columns"])
    assert res["run"]["counts"]["statements_with_candidates"] == 1 and res["run"]["counts"]["statements"] == 2


def test_i12_non_period_columns_are_neither_persisted_nor_candidates():
    pl = stmt(0, "profit_or_loss", [col(0), col(1, column_kind="variance", kind=None, end=None, months=None, role="unknown")],
              [row(0, "Revenue")], {(0, 0): "100", (0, 1): "12%"})
    res = f5.build(ext_of(pl), cls_())
    assert [c["column_index"] for c in res["columns"]] == [0] and len(res["candidates"]) == 1
    assert res["run"]["counts"]["skipped_cells_non_period_column"] == 1


# --- I-10 timestamps -----------------------------------------------------------------------------------------

def test_i10_timestamp_snapshot_is_complete_and_names_no_availability():
    ext, cls = real()
    res = f5.build(ext, cls, filing=FILING, retrieval=RETRIEVAL)
    ts = res["run"]["timestamps"]
    assert ts == {"uploaded_at": "2025-11-12T10:56:16.777000+00:00", "uploaded_at_raw": "12 Nov 2025 04:26:16 PM",
                  "authorized_at": "2025-11-12T10:59:24.124000+00:00", "authorized_at_raw": "12 Nov 2025 04:29:24 PM",
                  "path_epoch_ms": 1762944976777, "path_epoch_at": "2025-11-12T10:56:16.777000+00:00",
                  "cdn_last_modified": "2025-11-12T10:56:16+00:00", "cdn_last_modified_raw": "Wed, 12 Nov 2025 10:56:16 GMT",
                  "f1_first_seen_at": "2026-09-24T01:00:00+00:00", "document_retrieved_at": "2026-09-27T05:00:00+00:00"}
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not [k for k in keys(res) if "availab" in k]       # no field is (or is named) an availability time


def test_i10_legacy_date_only_upload_and_missing_authorisation_are_kept_raw():
    legacy = {"path": "cmt/upload_report_file/771_1546332840000.pdf", "uploaded_at": "2019-01-01T00:00:00+05:30",
              "uploaded_at_raw": "01 Jan 2019", "authorized_at": None, "authorized_at_raw": None}
    ts = f5.timestamp_snapshot(legacy, {"last_modified": "Tue, 01 Jan 2019 08:54:00 GMT"})
    assert ts["uploaded_at"] == "2018-12-31T18:30:00+00:00" and ts["uploaded_at_raw"] == "01 Jan 2019"
    assert ts["authorized_at"] is None and ts["path_epoch_at"] == "2019-01-01T08:54:00+00:00"
    assert ts["cdn_last_modified"] == "2019-01-01T08:54:00+00:00"
    assert f5.timestamp_snapshot({"path": None})["path_epoch_ms"] is None


def test_i10_timestamps_can_be_attached_after_the_retrieval_and_are_outside_the_content_hash():
    ext, cls = real()
    res = f5.build(ext, cls)
    assert res["run"]["timestamps"] is None
    h = f5.content_sha256(res)
    f5.attach_timestamps(res, FILING, RETRIEVAL)
    assert res["run"]["timestamps"]["cdn_last_modified_raw"] == RETRIEVAL["last_modified"]
    assert f5.content_sha256(res) == h


# --- I-11 provenance chain ---------------------------------------------------------------------------------------

def test_i11_every_candidate_traces_to_row_column_statement_and_run():
    ext, cls = real()
    res = f5.build(ext, cls, filing=FILING, retrieval=RETRIEVAL)
    stmts = {s["statement_index"] for s in res["statements"]}
    rows = {(r["statement_index"], r["row_index"]) for r in res["rows"]}
    cols = {(c["statement_index"], c["column_index"]) for c in res["columns"]}
    for c in res["candidates"]:
        assert c["statement_index"] in stmts
        assert (c["statement_index"], c["row_index"]) in rows and (c["statement_index"], c["column_index"]) in cols
        assert c["page"] == 1 and len(c["bbox"]) == 4 and c["mapping_rule_ids"]
    run = res["run"]
    assert run["document_sha256"] == SHA and run["cse_filing_id"] == 1
    for k in ("word_extractor", "f4_extractor_version", "classifier_version", "text_extractor", "builder_version",
              "mapper_version", "vocabulary_version", "template", "f3_period_status"):
        assert run[k], k


def test_i11_classification_of_another_document_is_refused():
    ext, cls = real()
    with pytest.raises(f5.CandidateBuildError):
        f5.build(ext, dict(cls, document_sha256="cd" * 32))


# --- candidate identity ---------------------------------------------------------------------------------------

def test_candidate_identity_is_source_based_and_multiple_values_get_ordinals():
    pl = stmt(0, "profit_or_loss", [col(0)], [row(0, "Revenue")], {(0, 0): ("100", "200")})
    res = f5.build(ext_of(pl), cls_())
    keys = [(c["statement_index"], c["row_index"], c["column_index"], c["value_ordinal"], c["concept_key"])
            for c in res["candidates"]]
    assert keys == [(0, 0, 0, 0, "revenue"), (0, 0, 0, 1, "revenue")]
    assert {c["candidate_status"] for c in res["candidates"]} == {"unresolved"}
    assert not any(k in c for c in res["candidates"] for k in ("ticker", "symbol", "issuer_id", "company_id"))


def test_candidate_status_follows_f4_status():
    pl = stmt(0, "profit_or_loss", [col(0)], [row(0, "Revenue")], {(0, 0): "100"})
    pl.cells[0].status = "conflicting"
    assert f5.build(ext_of(pl), cls_())["candidates"][0]["candidate_status"] == "conflicting"
    pl.cells[0].status = "unresolved"
    assert f5.build(ext_of(pl), cls_())["candidates"][0]["candidate_status"] == "unresolved"


# --- I-9 / I-14 determinism and versions ----------------------------------------------------------------------------

def test_i14_identical_input_and_versions_give_identical_output():
    ext, cls = real()
    a = f5.build(ext, cls, filing=FILING, retrieval=RETRIEVAL)
    b = f5.build(copy.deepcopy(ext), copy.deepcopy(cls), filing=dict(FILING), retrieval=dict(RETRIEVAL))
    ext2, cls2 = real()                      # a fresh F4 extraction of the same input
    c = f5.build(ext2, cls2, filing=FILING, retrieval=RETRIEVAL)
    assert f5.canonical_json(a) == f5.canonical_json(b) == f5.canonical_json(c)
    assert f5.content_sha256(a) == f5.content_sha256(c)


def test_i9_a_new_mapper_or_vocabulary_version_is_a_different_run(monkeypatch):
    ext, cls = real()
    a = f5.build(ext, cls)
    monkeypatch.setattr(fc, "MAPPER_VERSION", "f5.map.2")
    b = f5.build(ext, cls)
    assert b["run"]["mapper_version"] == "f5.map.2" and a["run"]["mapper_version"] == "f5.map.1"
    assert f5.content_sha256(a) != f5.content_sha256(b)
    monkeypatch.setattr(fc, "VOCABULARY_VERSION", "v2")
    assert f5.build(ext, cls)["run"]["vocabulary_version"] == "v2"
