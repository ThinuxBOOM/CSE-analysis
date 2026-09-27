"""
Stage F5 CLI — F2 temporary document -> F3 -> F4 -> F5, in memory (worker/extract_financial_candidates.py).

The REAL F2 lifecycle runs against a scripted fake CDN, so deletion is proven.
Candidates are built inside the consumer call (while the document exists); the
timestamp snapshot is completed from F2's record afterwards; nothing is handed to
a store unless the consumer succeeded AND the deletion was verified.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from worker import document_text, extract_financial_candidates as cli, financial_candidates as f5, statement_extraction as se
from test_document_retrieval import PDF, URL, FakeFetcher, FakeResp, TempRoot  # noqa: E402
from test_statement_extraction import doc, page, pl_rows  # noqa: E402

PRIMARY = "cmt/upload_report_file/771_1790000000001.pdf"
LAST_MODIFIED = "Mon, 21 Sep 2026 14:13:20 GMT"


def fakes():
    words = doc(page(pl_rows()))
    lines = se.build_lines(words.pages[0])
    text = document_text.from_pages(["\n".join(l.rendered for l in lines)], extractor="pdftotext 24.02.0 (poppler) -layout")
    return (lambda path: text), (lambda path: words)


def filing(fid=50553):
    return {"cse_filing_id": fid, "path": PRIMARY, "path2": None, "file_text": "Interim Financial Statements",
            "manual_date_raw": None, "uploaded_at": "2026-09-21T14:13:20+00:00", "uploaded_at_raw": "21 Sep 2026 07:43:20 PM",
            "authorized_at": None, "authorized_at_raw": None, "first_seen_at": "2026-09-22T00:00:00+00:00",
            "source_buckets": ["quarterly"], "source_symbol": "CTC", "listing_symbols": ["CTC.N0000"]}


class RecordingStores(dict):
    """Stands in for the Postgres stores: records what would be persisted, in order."""

    def __init__(self):
        super().__init__()
        self.saved = []
        this = self

        class Cls:
            def save(self, c, n):
                this.saved.append(("f3", c["cse_filing_id"]))
                return "inserted"

        class Iss:
            def link_filing(self, fid):
                this.saved.append(("link", fid))
                return None

        class Cand:
            def save(self, result, cid, link):
                this.saved.append(("f5", result["run"]["cse_filing_id"], result["run"]["timestamps"]["cdn_last_modified_raw"]))
                return "inserted", "run-1"

        class Conn:
            def commit(self):
                this.saved.append(("commit",))

        self.update(conn=Conn(), classification=Cls(), issuer=Iss(), candidates=Cand())


def test_candidates_are_built_in_memory_and_the_document_is_deleted(monkeypatch):
    text, words = fakes()
    import worker.financial_candidates_store as st
    monkeypatch.setattr(st, "classification_id", lambda conn, c: "cid-1")
    got, stores = {}, RecordingStores()
    with TempRoot() as root:
        fetcher = FakeFetcher({URL(PRIMARY): FakeResp(200, PDF, {"last-modified": LAST_MODIFIED})})
        out = cli.run([filing()], temp_root=root, request_delay_seconds=0, fetcher=fetcher, extract_text=text,
                      extract_words=words, require_poppler=False, stores=stores,
                      on_result=lambda fid, res, cls: got.setdefault(fid, res))
        assert os.listdir(root) == []
    assert out["ok"] and out["leftover_temp_entries"] == []
    [rec] = out["records"]
    assert rec["retrieval"]["cleanup_status"] == "deleted" and rec["retrieval"]["consumer_status"] == "succeeded"
    res = got[50553]
    assert res["run"]["counts"]["candidates"] == 28 and res["run"]["timestamps"]["cdn_last_modified_raw"] == LAST_MODIFIED
    assert res["run"]["timestamps"]["path_epoch_ms"] == 1790000000001
    assert stores.saved == [("f3", 50553), ("link", 50553), ("f5", 50553, LAST_MODIFIED), ("commit",)]
    assert rec["stored"]["f5"] == "inserted"
    report = json.dumps(out, default=str)
    assert "62,085" not in report and "66563" not in report and "Revenue" not in report      # counts only


@pytest.mark.parametrize("where", ["f3", "f4", "f5"])
def test_failure_anywhere_in_the_consumer_still_deletes_and_persists_nothing(monkeypatch, where):
    text, words = fakes()

    def boom(*a, **k):
        raise RuntimeError(f"{where} failed")
    if where == "f3":
        monkeypatch.setattr(cli.rc, "classify", boom)
    elif where == "f4":
        monkeypatch.setattr(cli.se, "extract_document", boom)
    else:
        monkeypatch.setattr(cli.f5, "build", boom)
    stores = RecordingStores()
    with TempRoot() as root:
        out = cli.run([filing()], temp_root=root, request_delay_seconds=0,
                      fetcher=FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)}), extract_text=text, extract_words=words,
                      require_poppler=False, stores=stores)
        assert os.listdir(root) == []
    [rec] = out["records"]
    assert rec["retrieval"]["outcome"] == "consumer_failed" and rec["retrieval"]["cleanup_status"] == "deleted"
    assert rec["f5"] is None and rec["stored"] is None
    assert stores.saved == [("commit",)]


def test_nothing_is_persisted_when_deletion_is_not_verified(monkeypatch):
    text, words = fakes()
    real_process = cli.dr.process_filing

    def failed_cleanup(*a, **k):
        rec = real_process(*a, **k)
        rec.cleanup_status, rec.outcome = "failed", "cleanup_failed"
        return rec
    monkeypatch.setattr(cli.dr, "process_filing", failed_cleanup)
    stores = RecordingStores()
    with TempRoot() as root:
        out = cli.run([filing()], temp_root=root, request_delay_seconds=0,
                      fetcher=FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)}), extract_text=text, extract_words=words,
                      require_poppler=False, stores=stores)
    assert out["records"][0]["stored"] is None and stores.saved == [("commit",)]


def test_governance_cap():
    with pytest.raises(ValueError, match="at most 20"):
        cli.run([filing(fid=i) for i in range(21)], require_poppler=False)


def test_dry_run_output_is_deterministic():
    text, words = fakes()
    hashes = []
    for _ in range(2):
        with TempRoot() as root:
            out = cli.run([filing()], temp_root=root, request_delay_seconds=0,
                          fetcher=FakeFetcher({URL(PRIMARY): FakeResp(200, PDF)}), extract_text=text, extract_words=words,
                          require_poppler=False)
        hashes.append(out["records"][0]["f5"]["content_sha256"])
    assert hashes[0] == hashes[1]


def test_loader_keeps_the_raw_timestamp_fields(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps([filing()]), encoding="utf-8")
    args = type("A", (), {"filings_json": str(p), "f1_state": None, "ids": ""})
    [f] = cli.load_filings(args)
    assert f["uploaded_at_raw"] == "21 Sep 2026 07:43:20 PM" and f["first_seen_at"] and f["listing_symbols"] == ["CTC.N0000"]
    assert f5.timestamp_snapshot(f)["uploaded_at_raw"] == "21 Sep 2026 07:43:20 PM"
