"""
Stage F4 CLI — F2 temporary document -> F3 -> F4, in memory (worker/extract_filing_statements.py).

The network is a scripted fake fetcher (F2 test double), the word layer is the
synthetic statement of test_statement_extraction, and the F3 text is its
rendering; the REAL F2 temp lifecycle runs, so deletion is proven. The run
report must hold counts and structure only - never extracted values.
"""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from worker import document_text, extract_filing_statements as cli, pdf_words, statement_extraction as se
from test_document_retrieval import PDF, URL, FakeFetcher, FakeResp, TempRoot  # noqa: E402
from test_statement_extraction import doc, page, pl_rows  # noqa: E402
from test_xlsx_companion import make_xlsx  # noqa: E402

PRIMARY = "cmt/upload_report_file/771_1790000000001.pdf"
COMPANION = "cmt/upload_report_file/771_1790000000002.xlsx"


def fakes():
    words = doc(page(pl_rows()))
    lines = se.build_lines(words.pages[0])
    text = document_text.from_pages(["\n".join(l.rendered for l in lines)], extractor="pdftotext 24.02.0 (poppler) -layout")
    return (lambda path: text), (lambda path: words)


def filing(fid=50553, path2=None):
    return {"cse_filing_id": fid, "path": PRIMARY, "path2": path2, "file_text": "Interim Financial Statements",
            "manual_date_raw": None, "uploaded_at": "2026-02-10T10:00:00+00:00", "authorized_at": None,
            "source_buckets": ["quarterly"], "source_symbol": "CTC"}


def xlsx_bytes():
    with tempfile.TemporaryDirectory() as d:
        return open(make_xlsx(os.path.join(d, "c.xlsx")), "rb").read()


def test_run_extracts_in_memory_reports_counts_only_and_deletes():
    text, words = fakes()
    got = {}
    with TempRoot() as root:
        fetcher = FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)})
        out = cli.run([filing()], temp_root=root, request_delay_seconds=0, fetcher=fetcher, extract_text=text,
                      extract_words=words, on_extraction=lambda fid, ext: got.setdefault(fid, ext), require_poppler=False)
        assert os.listdir(root) == []
    assert out["ok"] and out["leftover_temp_entries"] == [] and out["outcome_counts"] == {"succeeded": 1}
    [rec] = out["records"]
    assert rec["retrieval"]["cleanup_status"] == "deleted" and rec["retrieval"]["consumer_status"] == "succeeded"
    assert rec["f4"]["document_status"] == "extracted" and rec["f4"]["cells_by_status"]["extracted"] == 32
    assert rec["structure"][0]["columns"][2]["end_date"] == "2025-12-31"
    assert rec["temp_bytes_peak"] == len(PDF)
    report = json.dumps(out, default=str)
    assert "62,085" not in report and "66563" not in report and "Revenue" not in report      # no values, no labels
    ext = got[50553]                                          # the in-memory hand-off still has the cells
    assert any(c.raw_value == "62,085" for c in ext.cells)
    assert {c.cross_check for c in ext.cells} >= {"agree"}    # F3's Poppler text used as the cross-check


def test_companion_is_retrieved_nested_compared_and_deleted():
    text, words = fakes()
    with TempRoot() as root:
        fetcher = FakeFetcher({URL(PRIMARY): FakeResp(200, PDF),
                               URL(COMPANION): FakeResp(200, xlsx_bytes(), {"content-type": "application/octet-stream"})})
        out = cli.run([filing(path2=COMPANION)], with_companion=True, temp_root=root, request_delay_seconds=0,
                      fetcher=fetcher, extract_text=text, extract_words=words, require_poppler=False)
        assert os.listdir(root) == []
    assert out["ok"] and out["companion_outcomes"] == ["succeeded"] and out["documents_requested"] == 2
    [rec] = out["records"]
    assert rec["companion"]["cleanup_status"] == "deleted"
    chk = rec["f4"]["companion_check"]
    assert chk["authoritative"] is False and chk["matched"] >= 4 and "mismatches" not in chk
    assert fetcher.calls == [URL(PRIMARY), URL(COMPANION)]


def test_governance_cap_counts_companions():
    many = [filing(fid=i) for i in range(1, 21)]
    with pytest.raises(ValueError, match="at most 20"):
        cli.run(many + [filing(fid=99)], require_poppler=False)
    # 20 primaries are allowed alone, but not with a companion on top: 21 documents
    with pytest.raises(ValueError, match="21 documents"):
        cli.run(many[:19] + [filing(fid=98, path2=COMPANION)], with_companion=True, require_poppler=False)


def test_missing_poppler_fails_before_any_download(monkeypatch):
    pdf_words._identity_cache.clear()
    monkeypatch.setattr(pdf_words, "_banner", lambda b: ("/usr/bin/pdftotext", "pdftotext version 4.06\nGlyph & Cog, LLC"))
    fetcher = FakeFetcher({})
    with pytest.raises(pdf_words.ExtractorUnavailable):
        cli.run([filing()], fetcher=fetcher)
    assert fetcher.calls == []
    pdf_words._identity_cache.clear()


def test_extraction_failure_is_recorded_and_document_still_deleted():
    text, _ = fakes()

    def broken(path):
        raise pdf_words.WordExtractionError("pdftotext exit 1: damaged")
    with TempRoot() as root:
        out = cli.run([filing()], temp_root=root, request_delay_seconds=0, fetcher=FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)}),
                      extract_text=text, extract_words=broken, require_poppler=False)
        assert os.listdir(root) == []
    [rec] = out["records"]
    assert rec["retrieval"]["outcome"] == "consumer_failed" and rec["retrieval"]["cleanup_status"] == "deleted"
    assert rec["f4"] is None


def test_trailing_dot_path2_is_not_a_companion():
    # F2: 'cmt/upload_report_file/663_1750068649347.' returns an empty 200 - a placeholder, never requested
    assert not cli.has_companion({"path2": "cmt/upload_report_file/663_1750068649347."})
    assert cli.has_companion({"path2": COMPANION})
    assert not cli.has_companion({"path2": None})
    text, words = fakes()
    with TempRoot() as root:
        fetcher = FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)})
        out = cli.run([filing(path2="cmt/upload_report_file/663_1750068649347.")], with_companion=True, temp_root=root,
                      request_delay_seconds=0, fetcher=fetcher, extract_text=text, extract_words=words, require_poppler=False)
    assert out["documents_requested"] == 1 and out["companion_outcomes"] == [] and fetcher.calls == [URL(PRIMARY)]
