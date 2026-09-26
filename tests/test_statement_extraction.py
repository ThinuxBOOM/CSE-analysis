"""
Stage F4 — transient statement/column/row/cell extraction (worker/statement_extraction.py).

Offline: pages are built from synthetic word geometry modelled on the real F4
benchmark cases (SLTL, CTC, RWSL, BLUE, DIMO, LOLC, COMB, UCAR, CRL, LLUB, ASPH,
TILE, ACL). No PDF, no Poppler, no network, no database.
"""
import ast
import builtins
import dataclasses
import os
import socket
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import statement_extraction as se
from worker.pdf_words import DocumentWords, PageImage, PageWords, Word

CW, H, GAP = 4.0, 8.0, 1.6          # char width, text height, word space (0.2 x height)
EXTRACTOR = "poppler-pdftotext 24.02.0 -bbox-layout"


def cell_words(text, y, x0=None, x1=None, h=H, cw=CW):
    """Words of one printed cell, left-aligned at x0 or right-aligned at x1."""
    parts = text.split(" ")
    width = sum(len(p) * cw for p in parts) + GAP * (len(parts) - 1)
    x = x0 if x0 is not None else x1 - width
    out = []
    for p in parts:
        out.append(Word(p, round(x, 2), y, round(x + len(p) * cw, 2), y + h))
        x += len(p) * cw + GAP
    return out


def L(text, x0):
    return ("L", text, x0)


def R(text, x1):
    return ("R", text, x1)


def page(rows, number=1, width=595.0, height=842.0):
    words = []
    for y, cells in rows:
        for align, text, x in cells:
            words += cell_words(text, y, x0=x if align == "L" else None, x1=x if align == "R" else None)
    return PageWords(number, width, height, words)


def doc(*pages, images=()):
    return DocumentWords(list(pages), EXTRACTOR, images=list(images))


def sp(kind, end, months, label, role, audit="unknown", pkind="duration"):
    return {"statement_kind": kind, "period_kind": pkind, "end_date": end, "duration_months": months,
            "duration_label": label, "role": role, "audit_status": audit, "restated": False}


def classification(periods=(), period_end=None, period_start=None, fid=1, doc_type="interim_financial_statements"):
    return {"cse_filing_id": fid, "document_sha256": "ab" * 32, "classifier_version": "f3.1", "document_type": doc_type,
            "period_end": period_end, "period_start": period_start, "statement_periods": list(periods)}


def find(ext, label, end=None, scope="any", months="any"):
    """The single cell whose row label starts with `label` in the column ending `end`."""
    out = [c for c in ext.cells if c.row_label_normalized.startswith(label.lower())
           and (end is None or (c.column_period and c.column_period["end_date"] == end))
           and (scope == "any" or c.scope == scope)
           and (months == "any" or (c.column_period and c.column_period["duration_months"] == months))]
    assert len(out) == 1, [(c.row_label_raw, c.column_period, c.scope, c.raw_value) for c in out]
    return out[0]


# --- a baseline statement (CTC-like: company only, millions, 3M + 12M) --------------------------

VALS_X = (300, 370, 460, 530)


def pl_rows(scale_text="(all amounts in Sri Lanka Rupees millions)", extra=()):
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 40)]),
        (52, [L(scale_text, 40)]) if scale_text else None,
        (70, [L("03 months ended", 262), L("12 months ended", 422)]),
        (82, [R("31.12.2025", 300), R("31.12.2024", 370), R("31.12.2025", 460), R("31.12.2024", 530)]),
        (94, [R("Unaudited", 300), R("Unaudited", 370), R("Unaudited", 460), R("Audited", 530)]),
        (110, [L("Revenue", 40), R("62,085", 300), R("58,001", 370), R("66,563", 460), R("62,529", 530)]),
        (122, [L("Cost of sales", 40), R("(40,000)", 303), R("(38,000)", 373), R("(41,000)", 463), R("(40,500)", 533)]),
        (134, [L("Gross profit", 40), R("22,085", 300), R("20,001", 370), R("25,563", 460), R("22,029", 530)]),
        (146, [L("Other operating income", 40), R("(40)", 303), R("-", 368), R("12", 460), R("7", 530)]),
        (158, [L("Profit before income tax", 40), R("14,616", 300), R("13,000", 370), R("29,000", 460), R("25,000", 530)]),
        (170, [L("Income tax expense", 40), R("(4,000)", 303), R("(3,000)", 373), R("(5,000)", 463), R("(4,000)", 533)]),
        (182, [L("Profit for the period", 40), R("10,616", 300), R("10,000", 370), R("24,000", 460), R("21,000", 530)]),
        (194, [L("Earnings per share (Rs.)", 40), R("55.54", 300), R("50.12", 370), R("155.54", 460), R("140.10", 530)]),
    ]
    return [r for r in rows if r] + list(extra)


CTC_PERIODS = [sp("profit_or_loss", "2025-12-31", 3, "3M", "current"), sp("profit_or_loss", "2024-12-31", 3, "3M", "comparative"),
               sp("profit_or_loss", "2025-12-31", 12, "12M", "current", "unaudited"),
               sp("profit_or_loss", "2024-12-31", 12, "12M", "comparative", "audited")]


def extract(pages, periods=CTC_PERIODS, **kw):
    cls = classification(periods, period_end=kw.pop("period_end", "2025-12-31"), period_start=kw.pop("period_start", None))
    return se.extract_from_words(doc(*pages, images=kw.pop("images", ())), cls, **kw)


def test_baseline_statement_rows_columns_periods_roles():
    ext = extract([page(pl_rows())])
    assert ext.document_status == "extracted"
    [st] = ext.statements
    assert st.statement_kind == "profit_or_loss" and st.scale == 1_000_000 and st.scale_status == "resolved"
    periods = [(c.end_date, c.duration_months, c.role, c.audit_status) for c in st.columns]
    assert periods == [("2025-12-31", 3, "current", "unaudited"), ("2024-12-31", 3, "comparative", "unaudited"),
                       ("2025-12-31", 12, "current", "unaudited"), ("2024-12-31", 12, "comparative", "audited")]
    c = find(ext, "revenue", "2025-12-31", months=12)
    assert (c.raw_value, c.parsed_value, c.scale, c.status, c.current_comparative) == ("66,563", Decimal("66563"), 1_000_000,
                                                                                      "extracted", "current")
    assert c.column_period == {"period_kind": "duration", "start_date": "2025-01-01", "end_date": "2025-12-31",
                               "duration_months": 12, "duration_label": "12M", "basis": "f3.header_parser"}


def test_printed_signs_dashes_and_short_cells_kept_exactly():
    ext = extract([page(pl_rows())])
    c = find(ext, "other operating income", "2025-12-31", months=3)
    assert (c.raw_value, c.parsed_value, c.representation_class) == ("(40)", Decimal("-40"), "parenthesised_negative")
    d = find(ext, "other operating income", "2024-12-31", months=3)          # CTC '(40)' / '-' cells
    assert (d.raw_value, d.parsed_value, d.representation_class, d.status) == ("-", None, "dash_nil", "extracted")
    cost = find(ext, "cost of sales", "2025-12-31", months=12)
    assert cost.parsed_value == Decimal("-41000")                          # sign as printed, never from the label


def test_per_share_row_with_printed_unit_is_full_rupees_in_a_millions_statement():
    ext = extract([page(pl_rows())])
    c = find(ext, "earnings per share", "2025-12-31", months=12)
    assert (c.raw_value, c.scale, c.scale_basis, c.status) == ("155.54", 1, "row_label_unit", "extracted")


def test_per_share_row_without_unit_in_thousands_statement_is_unresolved():
    rows = [r if r[0] != 194 else (194, [L("Basic earnings per share", 40), R("2.10", 300), R("1.90", 370), R("4.20", 460),
                                         R("3.80", 530)]) for r in pl_rows("Rs.'000")]
    c = find(extract([page(rows)]), "basic earnings per share", "2025-12-31", months=12)
    assert c.status == "unresolved" and c.scale is None and "per_share_unit_not_stated" in c.reasons


def test_every_extracted_cell_has_complete_provenance():
    ext = extract([page(pl_rows())])
    required = ("filing_id", "document_sha256", "page", "statement", "row_label_raw", "column_header_raw", "raw_value",
                "scale", "coordinates", "extractor", "extractor_version")
    extracted = [c for c in ext.cells if c.status == "extracted"]
    assert len(extracted) == 32
    for c in extracted:
        for f in required:
            assert getattr(c, f) not in (None, "", {}), (f, c)
        assert c.column_period and c.column_period["end_date"]
        assert set(c.coordinates) == {"page", "x0", "y0", "x1", "y1", "page_width", "page_height"}
        assert c.extractor == EXTRACTOR and c.extractor_version == se.F4_EXTRACTOR_VERSION
        assert "31.12" in c.column_header_raw


def test_cell_model_has_no_concept_or_fact_fields():
    names = {f.name for f in dataclasses.fields(se.ExtractedCell)}
    assert not {n for n in names if "concept" in n or "fact" in n or n.endswith("_id") and n != "filing_id"}


# --- geometry ----------------------------------------------------------------------------------

def test_right_edge_alignment_beats_centre_alignment():
    # wide columns with LEFT-aligned headers: a short right-aligned value's CENTRE is nearer the NEXT column's
    # header, its RIGHT edge is nearest its own column (F4 discovery: centre rule 3 wrong, right-edge rule 1 wrong)
    rows = [
        (40, [L("STATEMENT OF FINANCIAL POSITION", 40)]),
        (52, [L("Rs.'000", 40)]),
        (70, [L("As at", 40), L("31.03.2026", 250), L("31.03.2025", 340)]),
        (90, [L("Total assets", 40), R("12,345,678,901,234", 330), R("11,111,111,111,111", 420)]),
        (102, [L("Total equity", 40), R("5", 330), R("(7)", 423)]),
    ]
    periods = [sp("financial_position", "2026-03-31", None, None, "current", pkind="instant"),
               sp("financial_position", "2025-03-31", None, None, "comparative", pkind="instant")]
    ext = extract([page(rows)], periods, period_end="2026-03-31")
    small = find(ext, "total equity", "2026-03-31")
    assert small.raw_value == "5"
    centre = (small.coordinates["x0"] + small.coordinates["x1"]) / 2
    headers = [c for c in ext.statements[0].columns]
    assert abs(centre - (headers[1].header_x0 + headers[1].header_x1) / 2) < abs(centre - (headers[0].header_x0 + headers[0].header_x1) / 2)
    assert find(ext, "total equity", "2025-03-31").raw_value == "(7)"
    assert find(ext, "total assets", "2026-03-31").raw_value == "12,345,678,901,234"


def test_values_left_of_labels_dimo_lolc():
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 150)]),
        (52, [L("Rs.'000", 40)]),
        (64, [L("Year ended", 30), L("9 months ended", 290), L("9 months ended", 360)]),
        (76, [R("31-03-2024", 70), R("31-12-2024", 335), R("31-12-2023", 400)]),
        (95, [R("43,644,295", 70), L("Revenue", 80), R("36,002,833", 335), R("35,568,091", 400)]),
        (107, [R("(31,790,151)", 73), L("Cost of sales", 80), R("(27,421,452)", 338), R("(26,172,686)", 403)]),
    ]
    periods = [sp("profit_or_loss", "2024-12-31", 9, "9M", "current"), sp("profit_or_loss", "2023-12-31", 9, "9M", "comparative")]
    ext = extract([page(rows)], periods, period_end="2024-12-31")
    left = find(ext, "revenue", "2024-03-31")
    assert left.raw_value == "43,644,295" and left.column_period["duration_months"] == 12 and "value_left_of_label" in left.quality_flags
    assert left.current_comparative == "unknown"          # prior FY column: neither current nor its comparative
    assert find(ext, "revenue", "2024-12-31").raw_value == "36,002,833"
    assert find(ext, "cost of sales", "2023-12-31").raw_value == "(26,172,686)"


def test_wrapped_label_value_on_continuation_line_sltl_acl():
    rows = pl_rows(extra=[
        (210, [L("Share of profit of equity accounted investees, net", 40)]),
        (219, [L("of tax", 40), R("1,111", 300), R("2,222", 370), R("3,333", 460), R("4,444", 530)]),
        (240, [L("Foreign currency translation differences on", 40)]),
        (249, [L("foreign operations", 40), R("(3)", 303), R("-", 368), R("5", 460), R("6", 530)]),
    ])
    ext = extract([page(rows)])
    c = find(ext, "share of profit of equity accounted investees, net of tax", "2025-12-31", months=3)
    assert c.raw_value == "1,111" and "wrapped_label" in c.quality_flags
    f = find(ext, "foreign currency translation differences on foreign operations", "2025-12-31", months=3)
    assert f.raw_value == "(3)"
    assert not [x for x in ext.cells if x.row_label_raw.startswith("of tax") or x.row_label_raw.startswith("foreign operations")]


def test_value_row_between_the_lines_of_a_wrapped_label_sltl_p3():
    rows = pl_rows(extra=[
        (210, [L("Equity instruments designated", 40)]),
        (215, [R("8", 300), R("6", 370)]),
        (219, [L("at fair value through OCI", 40), R("-", 458), R("-", 528)]),
    ])
    ext = extract([page(rows)])
    cells = [c for c in ext.cells if c.row_label_raw == "Equity instruments designated at fair value through OCI"]
    assert sorted(c.raw_value for c in cells) == ["-", "-", "6", "8"]


def test_bullet_item_is_not_joined_to_a_section_heading():
    rows = pl_rows(extra=[
        (210, [L("Earnings per share", 40)]),
        (222, [L("- Basic (Rs.)", 44), R("1.96", 300), R("1.26", 370), R("3.66", 460), R("2.37", 530)]),
        (234, [L("- Diluted (Rs.)", 44), R("1.95", 300), R("1.25", 370), R("3.65", 460), R("2.36", 530)]),
    ])
    ext = extract([page(rows)])
    basic = find(ext, "basic (rs.)", "2025-12-31", months=12)            # a bullet dash is not part of the matching form
    assert basic.raw_value == "3.66" and basic.section_label_raw == "Earnings per share" and basic.scale == 1
    assert find(ext, "diluted (rs.)", "2025-12-31", months=12).section_label_raw == "Earnings per share"


def test_repeated_labels_stay_separate_rows():
    rows = pl_rows(extra=[
        (210, [L("Attributable to:", 40)]),
        (222, [L("Profit for the period", 40), R("9,000", 300), R("8,000", 370), R("20,000", 460), R("18,000", 530)]),
    ])
    ext = extract([page(rows)])
    reps = [c for c in ext.cells if c.row_label_raw == "Profit for the period" and c.column_period["duration_months"] == 3
            and c.column_period["end_date"] == "2025-12-31"]
    assert sorted(c.raw_value for c in reps) == ["10,616", "9,000"]
    assert len({c.row_index for c in reps}) == 2
    assert [c.section_label_raw for c in sorted(reps, key=lambda c: c.row_index)] == [None, "Attributable to:"]


def test_multiline_stacked_header_and_note_column():
    rows = [
        (40, [L("STATEMENT OF COMPREHENSIVE INCOME", 40)]),
        (60, [L("For the year ended", 230), L("For the quarter ended", 380)]),
        (72, [R("31.03.26", 260), R("31.03.25", 320), R("31.03.26", 410), R("31.03.25", 470)]),
        (84, [R("(unaudited)", 260), R("(audited)", 320), R("(unaudited)", 410), R("(audited)", 470)]),
        (96, [L("Notes", 180), R("Rs'000", 260), R("Rs'000", 320), R("Rs'000", 410), R("Rs'000", 470)]),
        (110, [L("Revenue", 40), R("4", 200), R("53,012", 260), R("44,727", 320), R("12,377", 410), R("12,195", 470)]),
        (122, [L("Revaluation gain", 40), R("14.3", 200), R("122,000", 260), R("47,182", 320), R("122,000", 410), R("47,182", 470)]),
    ]
    periods = [sp("comprehensive_income", "2026-03-31", 12, "12M", "current"), sp("comprehensive_income", "2025-03-31", 12, "12M", "comparative"),
               sp("comprehensive_income", "2026-03-31", 3, "quarter", "current"), sp("comprehensive_income", "2025-03-31", 3, "quarter", "comparative")]
    ext = extract([page(rows)], periods, period_end="2026-03-31")
    [st] = ext.statements
    assert [c.column_kind for c in st.columns] == ["note_reference", "period", "period", "period", "period"]
    rev = find(ext, "revenue", "2026-03-31", months=3)
    assert rev.raw_value == "12,377" and rev.audit_status == "unaudited"
    assert find(ext, "revenue", "2025-03-31", months=12).audit_status == "audited"
    row = [r for r in st.rows if r.label_raw == "Revaluation gain"][0]
    assert row.note_ref_raw == "14.3"                                  # KHC decimal note ref: provenance, not a value
    assert not [c for c in ext.cells if c.raw_value in ("4", "14.3")]


def test_variance_columns_are_non_period():
    rows = [
        (40, [L("INCOME STATEMENT", 40)]),
        (52, [L("In Rupee Thousands", 400)]),
        (64, [L("For the nine months ended", 250), L("Change", 400), L("For the quarter ended", 460), L("Change", 600)]),
        (76, [L("30th September", 260), R("%", 424), L("30th September", 470), R("%", 624)]),
        (88, [R("2025", 316), R("2024", 380), R("2025", 491), R("2024", 555)]),
        (100, [L("Interest Income", 40), R("22,648,199", 316), R("23,095,995", 380), R("(2)", 427), R("7,640,318", 491),
               R("7,361,896", 555), R(">100", 624)]),
        (112, [L("Interest Expense", 40), R("(13,123,702)", 319), R("(14,301,107)", 383), R("(8)", 427), R("(4,382,277)", 494),
               R("(4,419,810)", 558), R("(1)", 627)]),
    ]
    periods = [sp("profit_or_loss", "2025-09-30", 9, "9M", "current"), sp("profit_or_loss", "2024-09-30", 9, "9M", "comparative"),
               sp("profit_or_loss", "2025-09-30", 3, "quarter", "current"), sp("profit_or_loss", "2024-09-30", 3, "quarter", "comparative")]
    ext = extract([page(rows, width=700)], periods, period_end="2025-09-30")
    kinds = [c.column_kind for c in ext.statements[0].columns]
    assert kinds == ["period", "period", "variance", "period", "period", "variance"]
    var = [c for c in ext.cells if c.column_kind == "variance"]
    assert {c.status for c in var} == {"non_period"} and {c.raw_value for c in var} == {"(2)", ">100", "(8)", "(1)"}
    assert find(ext, "interest income", "2025-09-30", months=3).raw_value == "7,640,318"


def test_group_scope_labels_printed_over_change_columns_comb():
    rows = [
        (40, [L("STATEMENT OF FINANCIAL POSITION", 200)]),
        (53, [R("Group", 514), R("Bank", 696)]),
        (66, [L("As at", 96), R("30.09.2025", 417), R("31.12.2024", 475), R("Change", 514), R("30.09.2025", 594),
              R("31.12.2024", 656), R("Change", 696)]),
        (79, [R("(Audited)", 475), R("(Audited)", 656)]),
        (91, [R("Rs.'000", 417), R("Rs.'000", 475), R("%", 514), R("Rs.'000", 594), R("Rs.'000", 656), R("%", 696)]),
        (114, [L("Cash and cash equivalents", 96), R("105,835,626", 414), R("89,615,459", 473), R("18.10", 512),
               R("102,490,978", 592), R("86,848,291", 654), R("18.01", 694)]),
        (126, [L("Total Assets", 96), R("3,232,686,149", 414), R("2,875,992,867", 473), R("12.40", 512),
               R("3,125,382,696", 592), R("2,789,780,288", 654), R("12.03", 694)]),
    ]
    periods = [sp("financial_position", "2025-09-30", None, None, "current", pkind="instant"),
               sp("financial_position", "2024-12-31", None, None, "comparative", "audited", pkind="instant")]
    ext = extract([page(rows, width=792, height=612)], periods, period_end="2025-09-30", period_start="2025-01-01")
    scopes = [(c.column_kind, c.scope) for c in ext.statements[0].columns]
    assert scopes == [("period", "group"), ("period", "group"), ("variance", None), ("period", "bank"), ("period", "bank"),
                      ("variance", None)]
    assert find(ext, "total assets", "2025-09-30", scope="bank").raw_value == "3,125,382,696"
    assert find(ext, "total assets", "2025-09-30", scope="group").raw_value == "3,232,686,149"


def test_continuation_heading_links_pages_comb_p9_p10():
    p9 = page([(40, [L("STATEMENT OF FINANCIAL POSITION", 200)]), (52, [L("Rs.'000", 40)]),
               (66, [L("As at", 96), R("30.09.2025", 417), R("31.12.2024", 475)]),
               (80, [L("Total Assets", 96), R("3,232,686,149", 414), R("2,875,992,867", 473)]),
               (92, [L("Total Liabilities", 96), R("2,910,245,671", 414), R("2,590,173,445", 473)])], number=9)
    p10 = page([(40, [L("STATEMENT OF FINANCIAL POSITION (Contd...)", 200)]), (52, [L("Rs.'000", 40)]),
                (66, [L("As at", 96), R("30.09.2025", 417), R("31.12.2024", 475)]),
                (80, [L("Total Equity", 96), R("322,440,478", 414), R("285,819,422", 473)])], number=10)
    periods = [sp("financial_position", "2025-09-30", None, None, "current", pkind="instant"),
               sp("financial_position", "2024-12-31", None, None, "comparative", pkind="instant")]
    ext = extract([p9, p10], periods, period_end="2025-09-30")
    a, b = ext.statements
    assert a.pages == [9, 10] and b.continuation_of == 0 and b.first_page == 10
    sig = [s for s in a.signals if s["relation"] == "assets_equals_liabilities_plus_equity" and s["end_date"] == "2025-09-30"]
    assert sig and sig[0]["result"] == "holds:sum_as_printed"          # signal across the continuation page


def test_headingless_continuation_page_inherits_columns():
    p1 = page([(40, [L("STATEMENT OF CASH FLOWS", 40)]), (52, [L("Rs.'000", 40)]),
               (58, [L("For the year ended", 40)]),
               (66, [R("31.03.2026", 300), R("31.03.2025", 380)]),
               (80, [L("Profit before tax", 40), R("1,000", 300), R("900", 380)]),
               (92, [L("Depreciation", 40), R("200", 300), R("180", 380)])], number=5)
    p2 = page([(40, [L("Interest paid", 40), R("(50)", 303), R("(40)", 383)]),
               (52, [L("Tax paid", 40), R("(70)", 303), R("(60)", 383)]),
               (64, [L("Net cash from operating activities", 40), R("1,080", 300), R("980", 380)])], number=6)
    periods = [sp("cash_flows", "2026-03-31", 12, "12M", "current"), sp("cash_flows", "2025-03-31", 12, "12M", "comparative")]
    ext = extract([p1, p2], periods, period_end="2026-03-31")
    a, b = ext.statements
    assert b.continuation_of == 0 and b.first_page == 6 and a.pages == [5, 6] and b.scale == 1000
    c = find(ext, "tax paid", "2026-03-31")
    assert c.raw_value == "(70)" and "inherited_columns" in c.quality_flags and c.confidence != "high"


def test_notes_page_is_not_taken_as_a_continuation():
    p1 = page([(40, [L("STATEMENT OF CASH FLOWS", 40)]), (52, [L("Rs.'000", 40)]),
               (66, [R("31.03.2026", 300), R("31.03.2025", 380)]),
               (80, [L("Profit before tax", 40), R("1,000", 300), R("900", 380)])], number=5)
    p2 = page([(30, [L("NOTES TO THE FINANCIAL STATEMENTS", 40)]),
               (40, [L("Interest paid", 40), R("(50)", 303), R("(40)", 383)]),
               (52, [L("Tax paid", 40), R("(70)", 303), R("(60)", 383)]),
               (64, [L("Other", 40), R("1,080", 300), R("980", 380)])], number=6)
    ext = extract([p1, p2], [sp("cash_flows", "2026-03-31", 12, "12M", "current")], period_end="2026-03-31")
    assert len(ext.statements) == 1 and ext.statements[0].pages == [5]


def test_numbers_inside_narrative_are_not_values():
    rows = pl_rows(extra=[(260, [L("I certify that the statements comply with the Companies Act No. 7 of 2007 and give a true", 40)])])
    ext = extract([page(rows)])
    assert not [c for c in ext.cells if c.row_label_raw.startswith("I certify") or c.raw_value == "2007"]


def test_footer_page_number_is_excluded():
    rows = pl_rows(extra=[(815, [L("-2-", 293)]), (815.5, [])])
    ext = extract([page(rows)])
    assert not [c for c in ext.cells if c.raw_value in ("-2-", "2")]


def test_superscript_ordinal_joins_its_date_rwsl():
    base = cell_words("31", 125.7, x0=255.3, h=8.4)
    sup = [Word("st", 255.3 + 8, 122.8, 255.3 + 11.6, 128.4)]
    from worker.statement_extraction import _attach_superscripts
    out = _attach_superscripts(base + sup)
    assert [w.text for w in out] == ["31st"]


def test_asph_letter_spaced_value_extracted_with_merge_flag():
    rows = pl_rows()
    pg = page(rows)
    # replace the 3M current revenue '62,085' with letter-spaced fragments '6 2 ,08 5'
    target = [w for w in pg.words if w.text == "62,085"][0]
    pg.words.remove(target)
    x = target.x0
    for t in ("6", "2", ",08", "5"):
        pg.words.append(Word(t, x, target.y0, x + 4 * len(t), target.y1))
        x += 4 * len(t) + 0.9
    ext = extract([pg])
    c = find(ext, "revenue", "2025-12-31", months=3)
    assert c.raw_value == "62,085" and "merged_fragments" in c.quality_flags and c.confidence == "medium"


# --- periods ---------------------------------------------------------------------------------------

def test_month_range_headers_sltl_give_literal_periods_only():
    rows = [
        (40, [L("Interim Condensed Consolidated Statement of Profit or Loss and Other Comprehensive Income", 60)]),
        (52, [L("(All amounts in LKR Millions )", 60)]),
        (62, [L("Group", 242), L("Company", 327), L("Group", 427), L("Company", 522)]),
        (72, [L("Apr-Jun", 239), L("Apr-Jun", 330), L("Jan - Jun", 422), L("Jan - Jun", 523)]),
        (82, [R("2026", 240), R("2025", 283), R("2026", 330), R("2025", 374), R("2026", 423), R("2025", 471), R("2026", 522),
              R("2025", 571)]),
        (100, [L("Revenue", 60), R("30,517", 249), R("27,316", 292), R("19,594", 340), R("17,682", 383), R("61,314", 434),
               R("55,167", 484), R("39,309", 533), R("35,513", 586)]),
    ]
    ext = extract([page(rows)], [], period_end="2026-06-30", period_start="2026-01-01")
    cols = ext.statements[0].columns
    got = [(c.start_date, c.end_date, c.duration_months, c.period_basis, c.role, c.scope) for c in cols]
    assert got[0] == ("2026-04-01", "2026-06-30", 3, "f4.month_range_header", "current", "group")
    assert got[1] == ("2025-04-01", "2025-06-30", 3, "f4.month_range_header", "comparative", "group")
    assert got[4] == ("2026-01-01", "2026-06-30", 6, "f4.month_range_header", "current", "group")
    assert got[7] == ("2025-01-01", "2025-06-30", 6, "f4.month_range_header", "comparative", "company")
    assert all(c.duration_label in ("3M", "6M") for c in cols)           # a literal range, never 'Q2'
    assert find(ext, "revenue", "2026-06-30", scope="group", months=3).scale == 1_000_000


def test_parse_month_range_is_literal_and_strict():
    from datetime import date
    assert se.parse_month_range("Apr-Jun 2026") == (date(2026, 4, 1), date(2026, 6, 30), 3)
    assert se.parse_month_range("Jan - Jun", 2026) == (date(2026, 1, 1), date(2026, 6, 30), 6)
    assert se.parse_month_range("Oct - Mar", 2026) == (date(2025, 10, 1), date(2026, 3, 31), 6)
    assert se.parse_month_range("Apr-Jun 2025", 2026) is None           # phrase year disagrees with the column year
    assert se.parse_month_range("Apr-Jun") is None                     # no year at all: unresolved
    assert se.parse_month_range("Quarter ended") is None


def test_undated_year_column_is_unresolved_ucar():
    rows = [
        (40, [L("STATEMENT OF COMPREHENSIVE INCOME", 40)]),
        (60, [L("Quarter", 277), L("Year", 522)]),
        (70, [L("For The Period Ended 25Th June,", 40), L("Ended", 281), L("Ended", 513)]),
        (82, [R("2026", 312), R("2025", 544)]),
        (94, [R("Rs.'000", 312), R("Rs.'000", 544)]),
        (106, [L("Revenue", 40), R("364,764", 310), R("1,591,597", 542)]),
    ]
    ext = extract([page(rows)], [], period_end="2026-06-25")
    year = find(ext, "revenue", None, months="any") if False else [c for c in ext.cells if c.raw_value == "1,591,597"][0]
    assert year.status == "unresolved" and year.column_period is None


def test_unspecified_duration_is_unresolved_not_inferred_acl():
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 40)]),
        (52, [L("(all amounts in Sri Lanka Rupees thousands)", 40)]),
        (64, [L("For the period ended 30 June", 250)]),
        (76, [R("2026", 300), R("2025", 370)]),
        (90, [L("Revenue from contracts with customers", 40), R("11,486,953", 300), R("10,000,000", 370)]),
    ]
    periods = [sp("profit_or_loss", "2026-06-30", None, "unspecified", "current")]
    ext = extract([page(rows)], periods, period_end="2026-06-30")
    c = find(ext, "revenue from contracts", "2026-06-30")
    assert c.column_period["duration_months"] is None and c.status == "unresolved" and "duration_unspecified" in c.reasons


def test_shared_date_over_quarter_and_nine_months_pair_tile():
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 18)]),
        (50, [R("Rs.'000", 250)]),
        (60, [L("31.12.2018", 237), L("31.12.2017", 339)]),               # each date centred over its pair
        (70, [L("Quarter", 224), L("Nine Months", 262), L("Quarter", 326), L("Nine Months", 364)]),
        (84, [L("Sales (net of tax)", 18), R("2,368,151", 250), R("5,368,159", 301), R("1,986,215", 352), R("4,515,356", 403)]),
    ]
    periods = [sp("profit_or_loss", "2018-12-31", 9, "9M", "current")]
    ext = extract([page(rows, width=792, height=612)], periods, period_end="2018-12-31")
    got = sorted((c.column_period["end_date"], c.column_period["duration_months"], c.raw_value, c.status) for c in ext.cells)
    assert got == [("2017-12-31", 3, "1,986,215", "extracted"), ("2017-12-31", 9, "4,515,356", "extracted"),
                   ("2018-12-31", 3, "2,368,151", "extracted"), ("2018-12-31", 9, "5,368,159", "extracted")]


def test_header_duration_conflicting_with_anchor_is_ambiguous():
    # a date anchor that F3 dates as 6M sits over a column whose own header cell says 'Quarter'
    from worker.statement_extraction import ColumnModel, _check_header_durations
    m = ColumnModel(0, "period", "resolved", 300, 340, 340, "", "", period_kind="duration", end_date="2023-09-30",
                    duration_months=6, duration_label="6M")
    hl = se.Line(1, 0, [], [se.Phrase([], "Quarter", 305, 335, 50, 58)], 50, 58, 8)
    _check_header_durations("profit_or_loss", [m], [], [hl])
    assert m.status == "ambiguous" and m.reasons == ["header_duration_conflict:3M_above_vs_6M_anchor"]


# --- scale ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("wording,scale", [
    ("(all amounts in Sri Lanka Rupees thousands)", 1000), ("Rs.'000", 1000), ("Rs. '000", 1000), ("Rs’000", 1000),
    ("Rs.‘000", 1000), ("LKR' 000s", 1000), ("In Rupee Thousands", 1000), ("(Amounts in LKR Thousands )", 1000),
    ("(All amounts in LKR Millions )", 1_000_000), ("Rs. Mn", 1_000_000), ("LKR Mn", 1_000_000), ("Rs. million", 1_000_000),
    ("Rs.", 1), ("LKR", 1),
])
def test_statement_scale_wordings(wording, scale):
    ext = extract([page(pl_rows(wording))])
    assert (ext.statements[0].scale, ext.statements[0].scale_status) == (scale, "resolved")


def test_bare_rs_header_cell_is_full_rupees_rwsl():
    rows = pl_rows(None)
    rows.insert(1, (58, [L("Rs.", 67)]))
    st = extract([page(rows)]).statements[0]
    assert (st.scale, st.scale_basis) == (1, "currency_only_header")


def test_footnote_amount_never_sets_scale_rwsl_trap():
    rows = pl_rows(None, extra=[(260, [L("Administration expenses include flood loss amounting to Rs. 243 million. Further", 40)])])
    rows.insert(1, (58, [L("Rs.", 67)]))
    st = extract([page(rows)]).statements[0]
    assert st.scale == 1 and st.scale_status == "resolved"
    assert any(e["strength"] == "ignored" and "243" in e["text"] for e in st.scale_evidence)
    assert find(extract([page(rows)]), "revenue", "2025-12-31", months=12).scale == 1


def test_competing_scale_evidence_is_conflicting_blue():
    rows = pl_rows("Rs. '000", extra=[(260, [L("All values in are Sri Lankan Rupees", 40)])])
    ext = extract([page(rows)])
    st = ext.statements[0]
    assert (st.scale, st.scale_status) == (None, "conflicting")
    assert {e["zone"] for e in st.scale_evidence} == {"header", "footer"}
    cells = [c for c in ext.cells if c.column_kind == "period"]
    assert cells and {c.status for c in cells} == {"conflicting"} and {c.scale for c in cells} == {None}


def test_narrative_on_another_statement_page_does_not_leak_seyb():
    narrative = page([(40, [L("The Group recorded a profit before tax of LKR 3,218 Mn for the period", 40)])], number=1)
    ext = extract([narrative, page(pl_rows("Rs.'000"), number=2)])
    assert [s.scale for s in ext.statements] == [1000]


def test_missing_scale_is_unresolved():
    ext = extract([page(pl_rows(None))])
    st = ext.statements[0]
    assert (st.scale, st.scale_status) == (None, "unresolved")
    assert find(ext, "revenue", "2025-12-31", months=12).status == "unresolved"


def test_scale_evidence_unit():
    assert se.scale_evidence("amounting to Rs. 243 million. Further")[0] == "ignored"
    assert se.scale_evidence("LKR 3,218 Mn")[0] == "ignored"
    assert se.scale_evidence("Group") is None and se.scale_evidence("2026") is None


# --- trust ------------------------------------------------------------------------------------------

def test_image_only_document_is_unreadable_llub():
    scans = [PageWords(n, 595.44, 842.4, []) for n in range(1, 4)]
    imgs = [PageImage(n, 1654, 2340, 200, 200, "jbig2", 1) for n in range(1, 4)]
    ext = se.extract_from_words(doc(*scans, images=imgs), classification())
    assert ext.document_status == "unreadable" and ext.statements == [] and ext.cells == []
    assert {t["status"] for t in ext.page_trust} == {"no_text_layer"}
    assert ext.page_trust[0]["reasons"] == ["image_only_page"]


def test_embedded_ocr_layer_gives_no_values_crl():
    pg = page(pl_rows("Rs."), width=612, height=792)
    scan = PageImage(1, 776, 1099, 100, 100, "jpeg", 8)                  # CRL: 100 ppi grey JPEG under the OCR text
    ext = extract([pg], images=[scan])
    assert ext.document_status == "ocr_untrusted" and ext.cells == []
    st = ext.statements[0]
    assert st.status == "ocr_untrusted" and "text_layer_over_full_page_raster" in st.reasons


def test_ocr_separator_anomalies_flag_a_page_even_without_an_image():
    rows = pl_rows("Rs.", extra=[(260, [L("Loans", 40), R("7.982.249.471", 300), R("801.814,765", 370), R("1.133.204.770", 460),
                                         R("6.123.456.789", 530)])])
    ext = extract([page(rows)])
    assert ext.statements[0].status == "ocr_untrusted" and "numeric_separator_anomalies" in ext.statements[0].reasons


def test_soft_masked_overlay_is_not_ocr_blue_p4():
    overlay = PageImage(1, 1870, 2420, 245, 245, "image", 8, soft_masked=True)   # 81% coverage, transparent
    ext = extract([page(pl_rows(), width=612, height=792)], images=[overlay])
    assert ext.page_trust[0]["status"] == "text_native" and ext.document_status == "extracted"


def test_clean_text_native_page():
    logo = PageImage(1, 200, 100, 150, 150, "jpeg", 8)
    ext = extract([page(pl_rows())], images=[logo])
    assert ext.page_trust == [{"page": 1, "status": "text_native", "reasons": [], "image_coverage": ext.page_trust[0]["image_coverage"]}]
    assert ext.page_trust[0]["image_coverage"] < 0.1


# --- cross-check ----------------------------------------------------------------------------------

def layout_of(rows_text):
    return ["\n".join(rows_text)]


def test_layout_crosscheck_agreement_and_disagreement():
    pg = page(pl_rows())
    good = ["STATEMENT OF PROFIT OR LOSS",
            "Revenue                      62,085     58,001     66,563     62,529",
            "Cost of sales               (40,000)   (38,000)   (41,000)   (40,500)"]
    ext = extract([pg], layout_pages=layout_of(good), layout_extractor="pdftotext 24.02.0 (poppler) -layout")
    assert find(ext, "revenue", "2025-12-31", months=12).cross_check == "agree"
    assert find(ext, "revenue", "2025-12-31", months=12).confidence == "high"
    assert find(ext, "gross profit", "2025-12-31", months=12).cross_check == "not_checked"
    drift = ["Revenue                      62,085     58,001",
             "Cost of sales                66,563     62,529  (40,000)   (38,000)   (41,000)   (40,500)"]
    ext = extract([pg], layout_pages=layout_of(drift), layout_extractor="pdftotext 24.02.0 (poppler) -layout")
    c = find(ext, "revenue", "2025-12-31", months=12)
    assert c.cross_check == "disagree" and c.status == "conflicting" and "layout_crosscheck_disagree" in c.reasons
    assert c.raw_value == "66,563"                                    # the coordinate value is kept, not replaced


def test_layout_crosscheck_label_must_be_whole_cell():
    pg = page(pl_rows())
    text = ["Net Revenue                  1          2          3          4",
            "Revenue                      62,085     58,001     66,563     62,529"]
    ext = extract([pg], layout_pages=layout_of(text), layout_extractor="pdftotext 24.02.0 (poppler) -layout")
    assert find(ext, "revenue", "2025-12-31", months=12).cross_check == "agree"


def test_xpdf_layout_text_is_never_used_as_crosscheck():
    pg = page(pl_rows())
    ext = extract([pg], layout_pages=layout_of(["Revenue 1 2 3 4"]), layout_extractor="pdftotext 4.06 (xpdf) -layout")
    assert "layout_crosscheck_unavailable:extractor_mismatch" in ext.status_reasons
    assert {c.cross_check for c in ext.cells} == {"not_available"}
    ext = extract([pg], layout_pages=layout_of(["Revenue 1 2 3 4"]), layout_extractor="pdftotext 25.03.0 (poppler) -layout")
    assert {c.cross_check for c in ext.cells} == {"not_available"}     # different Poppler release than the words


# --- signals ----------------------------------------------------------------------------------------

def test_signals_are_literal_and_non_authoritative():
    ext = extract([page(pl_rows())])
    rel = {(s["relation"], s["end_date"], s["duration_months"]): s for s in ext.statements[0].signals}
    gp = rel[("revenue_and_cost_of_sales_to_gross_profit", "2025-12-31", 3)]
    assert gp["result"] == "holds:sum_as_printed" and gp["terms"] == {"gross_profit": "22,085", "revenue": "62,085",
                                                                      "cost_of_sales": "(40,000)"}
    pbt = rel[("profit_before_tax_and_tax_to_profit", "2025-12-31", 3)]
    assert pbt["result"] == "holds:sum_as_printed"
    # 'Turnover' is not 'Revenue': no signal without the literal label
    rows = [r if r[0] != 110 else (110, [L("Turnover", 40)] + r[1][1:]) for r in pl_rows()]
    ext = extract([page(rows)])
    assert not [s for s in ext.statements[0].signals if s["relation"].startswith("revenue")]


def test_signal_reports_difference_when_relation_fails():
    rows = [r if r[0] != 134 else (134, [L("Gross profit", 40), R("22,000", 300), R("20,001", 370), R("25,563", 460),
                                         R("22,029", 530)]) for r in pl_rows()]
    ext = extract([page(rows)])
    s = [s for s in ext.statements[0].signals if s["relation"].startswith("revenue") and s["duration_months"] == 3
         and s["end_date"] == "2025-12-31"][0]
    assert s["result"] == "differs" and s["differences"]["sum_as_printed"] == "-85"


def test_expenses_printed_positive_are_kept_positive():
    # COMB 'Less : Interest expense 118,045,677' printed positive: F4 keeps it positive (sign normalisation is F5/F6)
    rows = pl_rows(extra=[(210, [L("Less : Interest expense", 40), R("118,045,677", 300), R("1", 370), R("2", 460), R("3", 530)])])
    c = find(extract([page(rows)]), "less : interest expense", "2025-12-31", months=3)
    assert c.parsed_value == Decimal("118045677") and c.representation_class == "numeric"


# --- safety -----------------------------------------------------------------------------------------

F4_MODULES = ("worker/statement_extraction.py", "worker/financial_values.py", "worker/pdf_words.py", "worker/xlsx_companion.py")


@pytest.mark.parametrize("path", F4_MODULES)
def test_f4_modules_have_no_db_network_or_storage_imports(path):
    src = open(os.path.join(os.path.dirname(__file__), "..", path), encoding="utf-8").read()
    tree = ast.parse(src)
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add((n.module or "").split(".")[0])
            names |= {a.name for a in n.names}
    assert not names & {"psycopg2", "db", "requests", "urllib", "socket", "http", "boto3", "supabase", "sqlite3",
                        "report_classification_store", "document_retrieval", "shutil.copy", "tempfile", "pickle"}


def test_extraction_is_pure_no_files_no_network(monkeypatch):
    def no_open(*a, **k):
        raise AssertionError(f"F4 extraction opened a file: {a}")

    def no_socket(*a, **k):
        raise AssertionError("F4 extraction used the network")

    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr(socket, "socket", no_socket)
    ext = extract([page(pl_rows())])
    assert ext.cells


def test_summary_has_counts_not_values():
    ext = extract([page(pl_rows())])
    s = ext.summary()
    flat = repr(s)
    assert "62,085" not in flat and "Revenue" not in flat
    assert s["cells_by_status"]["extracted"] == 32 and s["document_status"] == "extracted"
    d = ext.to_dict()
    assert isinstance(d["statements"][0]["cells"][0]["parsed_value"], str)     # Decimal serialised exactly


def test_f3_modules_are_not_modified_by_f4():
    # F4 reads F3 read-only; the frozen F3 classifier version is unchanged
    from worker import report_classification as rc
    assert rc.CLASSIFIER_VERSION == "f3.1"


# --- regressions found while building F4 ----------------------------------------------------------

def test_small_number_statement_keeps_its_first_rows():
    # millions statement where no value has a thousands comma: F3's header block would run into the data
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 40)]),
        (52, [L("(All amounts in LKR Millions )", 40)]),
        (64, [L("For the year ended", 250)]),
        (76, [R("31.03.2026", 300), R("31.03.2025", 370)]),
        (90, [L("Revenue", 40), R("83", 300), R("71", 370)]),
        (102, [L("Cost of sales", 40), R("(40)", 303), R("(35)", 373)]),
        (114, [L("Gross profit", 40), R("43", 300), R("36", 370)]),
    ]
    periods = [sp("profit_or_loss", "2026-03-31", 12, "12M", "current"), sp("profit_or_loss", "2025-03-31", 12, "12M", "comparative")]
    ext = extract([page(rows)], periods, period_end="2026-03-31")
    assert [find(ext, lab, "2026-03-31").raw_value for lab in ("revenue", "cost of sales", "gross profit")] == ["83", "(40)", "43"]


def test_close_values_are_never_glued_into_the_label():
    words = cell_words("Interest Expense", 100, x0=40) + cell_words("(13,123,702)", 100, x1=319) + \
        cell_words("(14,301,107)", 100, x1=371)                     # only 4 pt apart (0.5 x height)
    [line] = se.build_lines(PageWords(1, 595, 842, words))
    assert [p.text for p in line.phrases] == ["Interest Expense", "(13,123,702)", "(14,301,107)"]


def test_label_holding_an_amount_is_flagged():
    rows = pl_rows(extra=[(210, [L("Dividend of 1,234 shares issued", 40), R("5", 300), R("6", 370), R("7", 460), R("8", 530)])])
    c = find(extract([page(rows)]), "dividend of 1,234", "2025-12-31", months=3)
    assert "label_contains_amount" in c.quality_flags and c.confidence == "medium"


def test_page_positions_not_numbers_drive_f3_headings():
    # a document whose PageWords are numbered 9 and 10 (e.g. an extract): F3 counts positions from 1
    p9 = page(pl_rows(), number=9)
    ext = extract([p9])
    assert ext.statements[0].first_page == 9 and ext.cells[0].page == 9


def test_right_edge_not_centre_when_two_anchors_are_in_reach():
    # short values right-aligned at 330 (centre 326) under left-aligned headers A [250,290] and B [335,375]:
    # both headers are within column A's reach (45 pt); B's CENTRE is nearer (29 vs 56) but A's RIGHT edge is
    # nearer (40 vs 45) - the right edge must win; B then belongs to the column at 420
    rows = [
        (40, [L("STATEMENT OF FINANCIAL POSITION", 40)]),
        (52, [L("Rs.'000", 40)]),
        (70, [L("As at", 40), L("31.03.2026", 250), L("31.03.2025", 335)]),
        (90, [L("Other reserves", 40), R("12", 330), R("(7)", 420)]),
        (102, [L("Retained earnings", 40), R("45", 330), R("88", 420)]),
    ]
    periods = [sp("financial_position", "2026-03-31", None, None, "current", pkind="instant"),
               sp("financial_position", "2025-03-31", None, None, "comparative", pkind="instant")]
    ext = extract([page(rows)], periods, period_end="2026-03-31")
    assert find(ext, "other reserves", "2026-03-31").raw_value == "12"
    assert find(ext, "retained earnings", "2025-03-31").raw_value == "88"


def test_scale_is_statement_local_on_a_shared_page():
    # two statements on one page with different stated scales: each keeps its own
    rows = [
        (40, [L("STATEMENT OF PROFIT OR LOSS", 40)]), (52, [L("Rs.'000", 40)]),
        (64, [L("For the year ended", 250)]), (76, [R("31.03.2026", 300), R("31.03.2025", 370)]),
        (90, [L("Revenue", 40), R("1,000", 300), R("900", 370)]),
        (300, [L("STATEMENT OF FINANCIAL POSITION", 40)]), (312, [L("(All amounts in LKR Millions )", 40)]),
        (324, [L("As at", 40), R("31.03.2026", 300), R("31.03.2025", 370)]),
        (340, [L("Total assets", 40), R("5,000", 300), R("4,000", 370)]),
    ]
    periods = [sp("profit_or_loss", "2026-03-31", 12, "12M", "current"), sp("profit_or_loss", "2025-03-31", 12, "12M", "comparative")]
    ext = extract([page(rows)], periods, period_end="2026-03-31")
    assert [(s.statement_kind, s.scale, s.scale_status) for s in ext.statements] == [
        ("profit_or_loss", 1000, "resolved"), ("financial_position", 1_000_000, "resolved")]


def test_outputs_use_the_declared_vocabularies():
    from worker.financial_values import REPRESENTATION_CLASSES
    rows = pl_rows(extra=[(210, [L("Growth", 40), R("5%", 300), R(">100", 370), R("#REF!", 460), R("(0.000)", 530)])])
    ext = extract([page(rows)])
    assert {c.status for c in ext.cells} <= set(se.CELL_STATUSES)
    assert {c.representation_class for c in ext.cells} <= set(REPRESENTATION_CLASSES)
    assert {c.column_kind for s in ext.statements for c in s.columns} <= set(se.COLUMN_KINDS)
    assert {s.status for s in ext.statements} <= set(se.STATEMENT_STATUSES)
