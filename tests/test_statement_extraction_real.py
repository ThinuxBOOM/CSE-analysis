"""
Stage F4 — acceptance on the 20-document F4 benchmark (19 primary PDFs + COMB's
.xlsx companion) against the 114-value gold set read by eye in F4 discovery.

Network test: runs only with F4_REAL_DOCUMENTS=1 (otherwise SKIPPED), and needs
the pinned Poppler (Linux: poppler-utils 24.02.0 or 25.03.0). One F2 batch at
1 request/s, exactly the 20-document governance cap: each document is
downloaded to a temporary directory, classified (F3), extracted (F4) in memory
and deleted. The repository keeps only F1 listing metadata and the expected
values (tests/fixtures/filings/f4_gold_values.json); no document, text or cell
table is stored anywhere.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import f4_gold  # noqa: E402
from worker import extract_filing_statements as cli, pdf_words  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("F4_REAL_DOCUMENTS") != "1",
                                reason="network test: set F4_REAL_DOCUMENTS=1 (downloads 20 CSE documents temporarily)")

GOLD = f4_gold.load()
SYM = {f["cse_filing_id"]: f["source_symbol"] for f in GOLD["filings"]}
ID = {v: k for k, v in SYM.items()}

# Every non-exact gold item, and why. Anything else must be exact.
EXPECTED_NON_EXACT = {
    **{i: ("unresolved", "duration_unspecified") for i in range(33, 39)},    # ACL: 'For the period ended 30 June'
    70: ("unresolved", "per_share_unit_not_stated"),                       # LOLC EPS in a Rs'000 statement, no unit
    79: ("unresolved", "per_share_unit_not_stated"),                       # DIAL '- basic', no unit
    93: ("unresolved", "column_period_unresolved"),                        # UCAR 'Year Ended 2025': no date printed
    100: ("conflicting", "statement_scale_conflicting"),                   # BLUE: Rs.'000 header vs rupees footnote
    101: ("conflicting", "statement_scale_conflicting"),
    102: ("unresolved", "header_duration_conflict"),                       # BLUE: F3 dates a 'Quarter' column 6M
    **{i: ("unreadable", "ocr_untrusted") for i in (103, 104, 105)},      # CRL: embedded OCR layer
    **{i: ("unreadable", "unreadable") for i in (113, 114)},               # LLUB: image-only scan
}


@pytest.fixture(scope="module")
def run():
    assert pdf_words.extractor_available(), "F4_REAL_DOCUMENTS=1 needs the pinned Poppler (see pdf_words)"
    exts = {}
    out = cli.run(GOLD["filings"], with_companion=True, request_delay_seconds=1.0,
                  on_extraction=lambda fid, ext: exts.__setitem__(fid, ext))
    return out, exts


def test_lifecycle_within_cap_and_all_deleted(run):
    out, exts = run
    assert out["documents_requested"] == 20 and out["ok"]
    assert out["outcome_counts"] == {"succeeded": 19} and out["companion_outcomes"] == ["succeeded"]
    assert out["leftover_temp_entries"] == [] and out["cleanup_failures"] == []
    assert all(r["retrieval"]["cleanup_status"] == "deleted" for r in out["records"])
    assert len(exts) == 19
    assert out["extractor"].split()[1] in pdf_words.SUPPORTED_POPPLER_VERSIONS


def test_gold_set_breakdown(run):
    _, exts = run
    items, counts = f4_gold.score_all(exts)
    assert counts == {"exact": 97, "wrong_row": 0, "wrong_column": 0, "parse_mismatch": 0, "wrong_scale": 0,
                      "unresolved": 10, "conflicting": 2, "unreadable": 5}
    for it in items:
        if it["id"] in EXPECTED_NON_EXACT:
            cat, reason = EXPECTED_NON_EXACT[it["id"]]
            assert it["category"] == cat and reason in it["detail"], it
        else:
            assert it["category"] == "exact", it
    agree, bad = f4_gold.role_agreement(exts)
    assert agree == 95 and bad == []


def test_trust_boundaries(run):
    _, exts = run
    llub, crl, blue = exts[ID["LLUB"]], exts[ID["CRL"]], exts[ID["BLUE"]]
    assert llub.document_status == "unreadable" and llub.cells == []
    assert crl.document_status == "ocr_untrusted" and crl.cells == []
    assert {s.status for s in crl.statements} == {"ocr_untrusted"}
    assert sum(t["status"] == "ocr_layer_suspected" for t in crl.page_trust) == 26
    sofp = [s for s in blue.statements if s.statement_kind == "financial_position"][0]
    assert sofp.status != "ocr_untrusted"                 # soft-masked signature overlay, not a scan


def test_scale_traps(run):
    _, exts = run
    st = lambda sym, page: [s for s in exts[ID[sym]].statements if s.first_page == page][0]
    rwsl = st("RWSL", 3)
    assert (rwsl.scale, rwsl.scale_basis) == (1, "currency_only_header")
    # the same page's footnote 'amounting to Rs. 243 million' never becomes scale evidence
    assert not any(e["magnitude"] == 1_000_000 for e in rwsl.scale_evidence)
    assert all(c.scale in (1, None) for c in exts[ID["RWSL"]].cells if c.page == 3)
    blue = st("BLUE", 3)
    assert blue.scale_status == "conflicting" and {e["zone"] for e in blue.scale_evidence} == {"header", "footer"}
    assert st("SLTL", 2).scale == 1_000_000 and st("CTC", 3).scale == 1_000_000
    assert st("PABC", 6).scale == 1000 and st("KHC", 84).scale == 1


def test_month_range_columns_sltl(run):
    _, exts = run
    cols = [s for s in exts[ID["SLTL"]].statements if s.first_page == 2][0].columns
    assert [(c.end_date, c.duration_months, c.role, c.scope) for c in cols] == [
        ("2026-06-30", 3, "current", "group"), ("2025-06-30", 3, "comparative", "group"),
        ("2026-06-30", 3, "current", "company"), ("2025-06-30", 3, "comparative", "company"),
        ("2026-06-30", 6, "current", "group"), ("2025-06-30", 6, "comparative", "group"),
        ("2026-06-30", 6, "current", "company"), ("2025-06-30", 6, "comparative", "company")]
    assert {c.period_basis for c in cols} == {"f4.month_range_header"}


def test_asph_letter_spaced_values(run):
    _, exts = run
    c = [c for c in exts[ID["ASPH"]].cells if c.page == 3 and c.raw_value == "12,377"]
    assert len(c) == 1 and "merged_fragments" in c[0].quality_flags and c[0].status == "extracted"


def test_comb_continuation_and_companion(run):
    out, exts = run
    comb = exts[ID["COMB"]]
    sofp = [s for s in comb.statements if s.statement_kind == "financial_position"][0]
    assert sofp.pages == [9, 10]
    assert any(g["relation"] == "assets_equals_liabilities_plus_equity" and g["result"].startswith("holds") for g in sofp.signals)
    chk = comb.companion_check
    assert chk["authoritative"] is False and chk["not_found"] == 0 and chk["matched"] >= 500
    assert chk["hidden_text_cells"] >= 1 and any(h["text"] == "(Audited)" for h in chk["hidden_text_examples"])


def test_every_usable_cell_has_provenance(run):
    _, exts = run
    n = 0
    for ext in exts.values():
        for c in ext.cells:
            if c.status != "extracted":
                continue
            n += 1
            assert c.filing_id and len(c.document_sha256) == 64 and c.page and c.statement and c.row_label_raw
            assert c.column_header_raw and c.column_period and c.column_period["end_date"] and c.raw_value
            assert c.scale is not None or c.representation_class in ("dash_nil", "percentage", "comparison_bound") \
                or c.scale_basis == "row_label_percent"
            assert set(c.coordinates) >= {"x0", "y0", "x1", "y1"} and c.extractor.startswith("poppler-pdftotext")
    assert n > 5000


def test_signals_and_crosscheck_are_consistent(run):
    _, exts = run
    results = Counter(g["result"].split(":")[0] for ext in exts.values() for s in ext.statements for g in s.signals)
    assert results["holds"] >= 100 and results["differs"] == 0
    xc = Counter(c.cross_check for ext in exts.values() for c in ext.cells)
    assert xc["agree"] > 10000
    disagree = {(SYM[fid], c.page) for fid, ext in exts.items() for c in ext.cells if c.cross_check == "disagree"}
    # only rows that Poppler -layout itself split around a stray dash (TILE p3, RWSL p6) or a signature line (SEYB p8)
    assert disagree <= {("TILE", 3), ("RWSL", 6), ("SEYB", 8)}
