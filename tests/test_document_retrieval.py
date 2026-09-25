"""
Stage F2 — temporary document retrieval and lifecycle.

Network is replaced by an injected fake fetcher (scripted per URL), so the
tests are deterministic; the REAL filesystem is used (under the system temp
directory) so cleanup is genuinely proven, not assumed. Scenarios mirror live
F2.0 observations of cdn.cse.lk (legacy 403 -> cmt/ 200, S3 AccessDenied XML,
0-byte 200 for trailing-dot keys, image/jpeg 200, gzip/identity behaviour).
"""
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import document_retrieval as dr

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PDF = b"%PDF-1.7\n" + b"1 0 obj << >> endobj\n" * 300 + b"trailer\n%%EOF\n"
S3_DENIED = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'
HTML = b"<!DOCTYPE html><html><body>Service temporarily unavailable</body></html>"


def md5(b):
    return hashlib.md5(b).hexdigest()


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, content_length=True, strong_etag=True,
                 fail_after_chunks=None, chunk=4096):
        self.status, self.body = status, body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        if status == 200:
            self.headers.setdefault("content-type", "application/pdf")
            if content_length:
                self.headers.setdefault("content-length", str(len(body)))
            if strong_etag:
                self.headers.setdefault("etag", f'"{md5(body)}"')
        self.fail_after_chunks, self.chunk = fail_after_chunks, chunk
        self.closed = False

    def to_fetch_response(self):
        def gen():
            for i in range(0, len(self.body), self.chunk):
                if self.fail_after_chunks is not None and i // self.chunk >= self.fail_after_chunks:
                    raise ConnectionError("connection reset mid-stream")
                yield self.body[i:i + self.chunk]
        def close():
            self.closed = True
        return dr.FetchResponse(self.status, dict(self.headers), gen(), close)


class FakeFetcher:
    def __init__(self, routes):
        self.routes = routes          # url -> FakeResp | Exception | list of those (sequence)
        self.calls = []
        self.lock = threading.Lock()

    def fetch(self, url):
        with self.lock:
            self.calls.append(url)
            r = self.routes.get(url)
            if isinstance(r, list):
                r = r.pop(0) if len(r) > 1 else r[0]
        if r is None:
            return FakeResp(403, S3_DENIED, {"content-type": "application/xml"}).to_fetch_response()
        if isinstance(r, Exception):
            raise r
        return r.to_fetch_response()


RECENT = "cmt/upload_report_file/369_1786618965674.pdf"
LEGACY = "upload_report_file/369_1384340825.pdf"
SPACES = "cmt/upload_report_file/747_1574072553206.14.2019) (1).pdf"
AMP = "cmt/upload_report_file/745_1607334269292. B. Creasy & Company PLC AR 2019-20.pdf"
URL = lambda p: dr.CDN_BASE + dr.quote(p, safe="/")


class TempRoot:
    """A fresh directory inside the system temp dir for each test."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="f2test_")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


def _leftovers(root):
    return [n for n in os.listdir(root) if n.startswith("cse_f2_")]


def run(filing, routes, consumer=None, **kw):
    with TempRoot() as root:
        f = FakeFetcher(routes)
        rec = dr.process_filing(filing, consumer, fetcher=f, temp_root=root, **kw)
        return rec, f, _leftovers(root)


def filing(path, fid=101, path2=None):
    return {"cse_filing_id": fid, "path": path, "path2": path2}


# --- URL resolution ------------------------------------------------------------------

def test_url_resolution_direct_legacy_and_encoding():
    c = dr.resolve_candidates(RECENT)
    assert [x.strategy for x in c] == ["direct"] and c[0].url == "https://cdn.cse.lk/" + RECENT
    c = dr.resolve_candidates(LEGACY)
    assert [x.strategy for x in c] == ["direct", "legacy_cmt_prefix"]
    assert c[1].url == "https://cdn.cse.lk/cmt/" + LEGACY
    s = dr.resolve_candidates(SPACES)[0].url
    assert " " not in s and "%20" in s and "%28" in s and "%29" in s
    a = dr.resolve_candidates(AMP)[0].url
    assert "&" not in a and "%26" in a
    # an already-cmt/ path never becomes cmt/cmt/, and candidates are unique
    for p in (RECENT, LEGACY, SPACES):
        urls = [x.url for x in dr.resolve_candidates(p)]
        assert len(urls) == len(set(urls)) and all("cmt/cmt/" not in u for u in urls)
    print("PASS: direct, legacy cmt/ fallback candidate, percent-encoding, no duplicate/ambiguous candidates")


def test_url_resolution_rejects_bad_paths():
    bad = [None, "", "   ", " cmt/x.pdf", "/cmt/x.pdf", "https://evil.example/x.pdf", "cmt/../secret.pdf",
           "cmt//x.pdf", "cmt\\x.pdf", "cmt/x\n.pdf", "cmt/upload_report_file/369_1741169817422.", 123]
    for p in bad:
        try:
            dr.resolve_candidates(p)
        except dr.InvalidPath:
            continue
        raise AssertionError(f"accepted bad path {p!r}")
    print("PASS: NULL / empty / whitespace / absolute / traversal / backslash / control / trailing-dot paths rejected")


def test_null_empty_and_malformed_paths_in_lifecycle():
    rec, f, left = run(filing(None), {})
    assert rec.outcome == "no_document" and rec.failure_category == "null_path" and not f.calls and not left
    rec, f, left = run(filing(""), {})
    assert rec.outcome == "invalid_path" and not f.calls and not left
    rec, f, left = run(filing("/abs/x.pdf"), {})
    assert rec.outcome == "invalid_path" and not f.calls and not left
    # companion (path2): None / '' mean "no companion", trailing '.' is invalid (observed: 200 empty body)
    for p2, outcome in ((None, "no_document"), ("", "no_document"), ("cmt/upload_report_file/369_1741169817422.", "invalid_path")):
        rec, f, left = run(filing(RECENT, path2=p2), {}, role="companion")
        assert rec.outcome == outcome and not f.calls and not left, (p2, rec.outcome)
    print("PASS: null/empty/malformed path and path2 never reach the network and leave nothing behind")


# --- HTTP behaviour ------------------------------------------------------------------

def test_valid_pdf_succeeds_with_integrity_checks():
    rec, f, left = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF)})
    assert rec.outcome == "succeeded" and rec.strategy == "direct" and rec.byte_length == len(PDF)
    assert rec.sha256 == hashlib.sha256(PDF).hexdigest() and rec.validation["etag_check"] == "match"
    assert rec.validation["pdf_version"] == "1.7" and rec.cleanup_status == "deleted" and not left
    assert rec.validation["detected_kind"] == "pdf"
    assert dr.detect_kind(b"\n %PDF-1.4") == "pdf" and dr.detect_kind(dr.ZIP_MAGIC) == "zip"
    assert dr.detect_kind(S3_DENIED) == "xml" and dr.detect_kind(HTML) == "html" and dr.detect_kind(b"") == "empty"
    print("PASS: valid PDF -> succeeded, SHA-256 + length + strong-ETag(MD5) verified, cleaned up")


def test_legacy_path_falls_back_to_cmt_only_after_403_or_404():
    routes = {URL(LEGACY): FakeResp(403, S3_DENIED, {"content-type": "application/xml"}),
              dr.CDN_BASE + "cmt/" + LEGACY: FakeResp(200, PDF)}
    rec, f, _ = run(filing(LEGACY), routes)
    assert rec.outcome == "succeeded" and rec.strategy == "legacy_cmt_prefix" and len(rec.attempts) == 2
    assert rec.attempts[0]["status"] == 403 and rec.attempts[0]["s3_error_code"] == "AccessDenied"
    # a 5xx on the direct URL is transient: do NOT mask it with the fallback
    rec, f, _ = run(filing(LEGACY), {URL(LEGACY): FakeResp(500, b"oops", {"content-type": "text/plain"})})
    assert rec.outcome == "download_failed" and rec.failure_category == "server_error" and len(f.calls) == 1
    # both variants denied -> forbidden_or_missing (S3 also answers 403 for missing keys)
    rec, f, _ = run(filing(LEGACY), {})
    assert rec.failure_category == "forbidden_or_missing" and len(f.calls) == 2
    print("PASS: legacy cmt/ fallback only after 403/404; 5xx never masked; double-403 reported")


def test_http_failures_are_categorised():
    cases = [
        (FakeResp(404, b"", {"content-type": "application/xml"}), "download_failed", "not_found"),
        (FakeResp(403, S3_DENIED, {"content-type": "application/xml"}), "download_failed", "forbidden_or_missing"),
        (FakeResp(500, b"err", {"content-type": "text/plain"}), "download_failed", "server_error"),
        (FakeResp(200, HTML, {"content-type": "text/html"}), "validation_failed", "not_pdf"),
        (FakeResp(200, b"", {"content-type": "application/octet-stream"}), "validation_failed", "empty_body"),
        (FakeResp(200, b"\x00\x01garbage binary\xff" * 50), "validation_failed", "not_pdf"),
        (FakeResp(200, b"\xff\xd8\xff\xe0JFIF" + b"\x00" * 100, {"content-type": "image/jpeg"}), "validation_failed", "not_pdf"),
        (ConnectionError("dns failure"), "download_failed", "network_error"),
    ]
    for resp, outcome, category in cases:
        rec, f, left = run(filing(RECENT), {URL(RECENT): resp})
        assert (rec.outcome, rec.failure_category) == (outcome, category), (resp, rec.outcome, rec.failure_category)
        assert not left, "temporary files left behind"
        assert rec.consumer_status == "not_run" and rec.cleanup_status == "deleted"
        if rec.outcome == "download_failed":
            assert rec.sha256 is None and rec.byte_length is None
    rec, _, _ = run(filing(RECENT), {URL(RECENT): FakeResp(200, HTML, {"content-type": "text/html"})})
    assert rec.validation["detected_kind"] == "html"
    print("PASS: 404 / 403 / 500 / HTML-200 / empty-200 / binary-200 / jpeg-200 / network errors categorised")


def test_redirects_only_to_cdn_https():
    target = dr.CDN_BASE + "cmt/upload_report_file/moved.pdf"
    ok = {URL(RECENT): FakeResp(301, b"", {"location": target}), target: FakeResp(200, PDF)}
    rec, _, _ = run(filing(RECENT), ok)
    assert rec.outcome == "succeeded" and rec.final_url == target and rec.attempts[0]["redirects"][0]["status"] == 301
    evil = {URL(RECENT): FakeResp(302, b"", {"location": "https://evil.example/x.pdf"})}
    rec, f, _ = run(filing(RECENT), evil)
    assert rec.failure_category == "unexpected_redirect" and len(f.calls) == 1
    loop = {URL(RECENT): FakeResp(301, b"", {"location": URL(RECENT)})}
    rec, _, _ = run(filing(RECENT), loop)
    assert rec.failure_category == "too_many_redirects"
    print("PASS: redirects followed only to https://cdn.cse.lk (max 2); others rejected")


def test_truncation_and_interruption():
    short = PDF[:-500]
    # server declares the full length but sends less
    rec, _, left = run(filing(RECENT), {URL(RECENT): FakeResp(200, short, {"content-length": str(len(PDF))})})
    assert rec.failure_category == "truncated" and not left
    # no Content-Length (e.g. gzip transfer): structural check catches the missing %%EOF
    rec, _, _ = run(filing(RECENT), {URL(RECENT): FakeResp(200, short, content_length=False, strong_etag=False)})
    assert rec.failure_category == "truncated_or_malformed"
    assert any("no Content-Length" in w for w in rec.validation["warnings"])
    # connection drops mid-stream
    rec, _, left = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF, fail_after_chunks=1)})
    assert rec.failure_category == "download_interrupted" and not left
    # size cap
    rec, _, left = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF)}, max_bytes=1000)
    assert rec.failure_category == "too_large" and not left
    # bytes corrupted in transit relative to the strong ETag
    rec, _, _ = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF, {"etag": '"' + "0" * 32 + '"'})})
    assert rec.failure_category == "etag_mismatch"
    print("PASS: truncated (length), truncated (structure), interrupted stream, size cap, ETag mismatch")


# --- validation unit ------------------------------------------------------------------

def test_validation_rules():
    v = dr.validate_content("pdf", b"%PDF-1.4\n", b"%%EOF", 10, None, "x", None)
    assert v["ok"] and v["etag_check"] == "not_comparable"
    v = dr.validate_content("pdf", b"\n\n  %PDF-1.5", b"%%EOF\n", 20, "20", "x", 'W/"abc"')
    assert v["ok"] and any("offset" in w for w in v["warnings"])            # header within 1 KB
    v = dr.validate_content("pdf", b"x" * 1100 + b"%PDF-1.5", b"%%EOF", 1200, None, "x", None)
    assert not v["ok"] and v["category"] == "not_pdf"                          # header too late
    v = dr.validate_content("pdf", b"%PDF-1.7", b"no trailer", 50, None, "x", None)
    assert v["category"] == "truncated_or_malformed"
    v = dr.validate_content("pdf", b"GIF89a", b"", 6, None, "x", None)
    assert v["category"] == "not_pdf" and v["detected_kind"] == "unknown"
    v = dr.validate_content("zip", dr.ZIP_MAGIC + b"rest", b"", 8, None, "x", None)
    assert v["ok"]
    v = dr.validate_content("zip", b"%PDF-1.7", b"%%EOF", 8, None, "x", None)
    assert v["category"] == "not_expected_type"
    # no minimum size beyond "non-empty + structurally complete" was established by discovery
    v = dr.validate_content("pdf", b"%PDF-1.0 %%EOF", b"%PDF-1.0 %%EOF", 14, "14", "x", None)
    assert v["ok"]
    print("PASS: magic header (incl. 1 KB leniency), EOF marker, companion zip, no arbitrary minimum size")


# --- hashing ------------------------------------------------------------------------------

def test_hashing_deterministic_and_sensitive():
    a, _, _ = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF)})
    b, _, _ = run(filing(RECENT, fid=202), {URL(RECENT): FakeResp(200, PDF)})
    changed = PDF.replace(b"endobj", b"endobJ", 1)
    c, _, _ = run(filing(RECENT), {URL(RECENT): FakeResp(200, changed)})
    assert a.sha256 == b.sha256 == hashlib.sha256(PDF).hexdigest()
    assert c.sha256 != a.sha256 and c.sha256 == hashlib.sha256(changed).hexdigest()

    class Boom:
        def update(self, _):
            raise MemoryError("hash engine failure")
    rec, _, left = run(filing(RECENT), {URL(RECENT): FakeResp(200, PDF)},
                       hash_factories={"sha256": Boom, "md5": hashlib.md5})
    assert rec.outcome == "hash_failed" and not left
    print("PASS: same bytes -> same SHA-256, one changed byte -> different; hash failure categorised + cleaned")


# --- lifecycle -----------------------------------------------------------------------------

def test_lifecycle_cleans_up_on_every_path():
    seen = {}

    def consumer(doc):
        seen["path"], seen["exists"] = doc.path, os.path.exists(doc.path)
        assert open(doc.path, "rb").read() == PDF

    with TempRoot() as root:
        f = FakeFetcher({URL(RECENT): FakeResp(200, PDF)})
        rec = dr.process_filing(filing(RECENT), consumer, fetcher=f, temp_root=root)
        assert rec.outcome == "succeeded" and rec.consumer_status == "succeeded" and seen["exists"]
        assert not os.path.exists(seen["path"]) and not os.path.exists(os.path.dirname(seen["path"]))
        assert not _leftovers(root)                                                          # (1)

        for routes in ({URL(RECENT): FakeResp(200, HTML, {"content-type": "text/html"})},     # (2)
                       {URL(RECENT): FakeResp(404, b"")},                                      # (3)
                       {URL(RECENT): FakeResp(200, PDF, fail_after_chunks=2)}):
            dr.process_filing(filing(RECENT), consumer, fetcher=FakeFetcher(routes), temp_root=root)
            assert not _leftovers(root)

        def exploding(doc):                                                                   # (4)
            seen["boom_path"] = doc.path
            raise ValueError("extraction crashed")
        rec = dr.process_filing(filing(RECENT), exploding, fetcher=FakeFetcher({URL(RECENT): FakeResp(200, PDF)}), temp_root=root)
        assert rec.outcome == "consumer_failed" and "extraction crashed" in rec.consumer_error
        assert rec.cleanup_status == "deleted" and not os.path.exists(seen["boom_path"]) and not _leftovers(root)
    print("PASS: success, validation failure, download failure, interrupted stream and consumer crash all clean up")


def test_cleanup_failure_is_surfaced_never_swallowed():
    def failing_rmtree(path):
        raise PermissionError("file is locked")

    def noop_rmtree(path):
        return None          # "succeeds" but deletes nothing

    with TempRoot() as root:
        for bad in (failing_rmtree, noop_rmtree):
            rec = dr.process_filing(filing(RECENT), None, fetcher=FakeFetcher({URL(RECENT): FakeResp(200, PDF)}),
                                    temp_root=root, rmtree=bad)
            assert rec.outcome == "cleanup_failed" and rec.cleanup_status == "failed", bad
            assert rec.cleanup_error
        # a consumer failure + cleanup failure: cleanup_failed wins, consumer error still recorded
        rec = dr.process_filing(filing(RECENT), lambda d: 1 / 0, fetcher=FakeFetcher({URL(RECENT): FakeResp(200, PDF)}),
                                temp_root=root, rmtree=failing_rmtree)
        assert rec.outcome == "cleanup_failed" and rec.consumer_status == "failed"
        # the context manager raises, chaining the body's exception
        try:
            with dr.temporary_workspace(5, root, rmtree=failing_rmtree):
                raise KeyError("body failed")
        except dr.TemporaryFileCleanupError as exc:
            assert isinstance(exc.__cause__, KeyError)
        else:
            raise AssertionError("cleanup failure was swallowed")
        # a batch with a cleanup failure is not ok and names the filing
        batch = dr.process_batch([filing(RECENT, fid=7)], fetcher=FakeFetcher({URL(RECENT): FakeResp(200, PDF)}),
                                 temp_root=root, request_delay_seconds=0, rmtree=failing_rmtree)
        assert batch["ok"] is False and batch["cleanup_failures"] == [7]
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
    print("PASS: failed or ineffective deletion -> 'cleanup_failed' (records), TemporaryFileCleanupError (context), batch not ok")


def test_concurrent_filings_never_share_or_overwrite_files():
    bodies = {i: PDF + str(i).encode() * 10 + b"\n%%EOF\n" for i in range(8)}
    routes = {URL(f"cmt/upload_report_file/{i}.pdf"): FakeResp(200, bodies[i]) for i in range(8)}
    routes[URL(RECENT)] = FakeResp(200, PDF)
    seen, errors = {}, []
    lock = threading.Lock()

    def consumer(doc):
        data = open(doc.path, "rb").read()
        with lock:
            seen.setdefault(doc.cse_filing_id, []).append((doc.path, hashlib.sha256(data).hexdigest()))

    with TempRoot() as root:
        fetcher = FakeFetcher(routes)
        jobs = [filing(f"cmt/upload_report_file/{i}.pdf", fid=i) for i in range(8)] + [filing(RECENT, fid=99)] * 4

        def work(fl):
            try:
                rec = dr.process_filing(fl, consumer, fetcher=fetcher, temp_root=root)
                assert rec.outcome == "succeeded", rec.outcome
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=work, args=(j,)) for j in jobs]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors and not _leftovers(root)
    paths = [p for v in seen.values() for p, _ in v]
    assert len(paths) == len(set(paths)) == 12                       # every run had its own file
    for i in range(8):
        assert seen[i][0][1] == hashlib.sha256(bodies[i]).hexdigest()  # nobody read another filing's bytes
    assert len({h for _, h in seen[99]}) == 1 and len({p for p, _ in seen[99]}) == 4   # same filing x4: 4 dirs
    print("PASS: 12 concurrent retrievals (incl. the same filing x4) used 12 distinct temp files, no cross-talk")


def test_batch_leaves_no_temporary_files():
    routes = {URL(RECENT): FakeResp(200, PDF), URL(SPACES): FakeResp(200, PDF),
              URL(LEGACY): FakeResp(403, S3_DENIED), dr.CDN_BASE + "cmt/" + LEGACY: FakeResp(200, PDF),
              URL(AMP): FakeResp(200, HTML, {"content-type": "text/html"})}
    with TempRoot() as root:
        out = dr.process_batch([filing(RECENT, 1), filing(SPACES, 2), filing(LEGACY, 3), filing(AMP, 4), filing(None, 5)],
                               fetcher=FakeFetcher(routes), temp_root=root, request_delay_seconds=0)
        assert out["ok"] and out["leftover_temp_entries"] == [] and not _leftovers(root)
        assert out["outcome_counts"] == {"succeeded": 3, "validation_failed": 1, "no_document": 1}
    print("PASS: mixed batch -> no temporary files remain; outcomes counted")


def _inside(child, parent):
    try:
        return os.path.commonpath([os.path.realpath(child), os.path.realpath(parent)]) == os.path.realpath(parent)
    except ValueError:
        return False


def _rejected(root):
    try:
        dr.validate_temp_root(root)
    except ValueError:
        return True
    return False


def test_temp_root_must_be_temporary_and_outside_repo():
    for bad in (REPO, os.path.join(REPO, "tests"), os.path.join(tempfile.gettempdir(), "definitely_missing_f2_dir"),
                os.path.expanduser("~")):
        assert _rejected(bad), f"accepted non-temporary root {bad}"

    # The invariant, checked against THIS machine's actual layout: the default is
    # the system temp dir UNLESS that dir contains the repository (e.g. a checkout
    # under /tmp), in which case the default must be refused, not silently used.
    system_tmp = os.path.realpath(tempfile.gettempdir())
    if _inside(REPO, system_tmp):
        assert _rejected(None), "default temp root contains the repository but was accepted"
        with TempRoot() as sibling:                   # a separate dir under the temp dir is still fine
            assert dr.validate_temp_root(sibling) == os.path.realpath(sibling)
    else:
        assert dr.validate_temp_root(None) == system_tmp

    rec = dr.process_filing(filing(RECENT), None, fetcher=FakeFetcher({}), temp_root=REPO)
    assert rec.outcome == "download_failed" and rec.failure_category.startswith("temp_root")
    print("PASS: repo / missing / home-dir roots rejected; default follows the system temp dir only when it "
          "does not contain the repository")


def test_temp_root_invariant_under_both_layouts():
    """Layout-independent: simulate a repository INSIDE and OUTSIDE the system
    temp dir, so both branches are exercised on every machine."""
    saved_tempdir, saved_repo_root = tempfile.tempdir, dr._repo_root
    with TempRoot() as fake_tmp, TempRoot() as elsewhere:
        try:
            tempfile.tempdir = fake_tmp

            # Layout A: repository checked out under the temp dir (e.g. /tmp/repo)
            repo_a = os.path.join(fake_tmp, "repo")
            os.makedirs(os.path.join(repo_a, "tests"))
            sibling = os.path.join(fake_tmp, "work")
            os.makedirs(sibling)
            dr._repo_root = lambda: repo_a
            assert _rejected(None)                                     # default contains the repo
            assert _rejected(fake_tmp) and _rejected(repo_a) and _rejected(os.path.join(repo_a, "tests"))
            assert dr.validate_temp_root(sibling) == os.path.realpath(sibling)
            f = FakeFetcher({URL(RECENT): FakeResp(200, PDF)})
            rec = dr.process_filing(filing(RECENT), None, fetcher=f)   # no explicit root
            assert rec.outcome == "download_failed" and rec.failure_category.startswith("temp_root") and not f.calls
            rec = dr.process_filing(filing(RECENT), None, fetcher=f, temp_root=sibling)
            assert rec.outcome == "succeeded" and not _leftovers(sibling)

            # Layout B: repository outside the temp dir -> the temp dir itself is the default
            dr._repo_root = lambda: os.path.join(elsewhere, "repo")
            assert dr.validate_temp_root(None) == os.path.realpath(fake_tmp)
        finally:
            tempfile.tempdir, dr._repo_root = saved_tempdir, saved_repo_root
    print("PASS: temp-root safety invariant holds for a repo inside AND outside the system temp dir")


# --- zero-archive invariant ------------------------------------------------------------------

def test_zero_archive_invariant():
    captured = {}

    def consumer(doc):
        captured["path"] = doc.path

    with TempRoot() as root:
        out = dr.process_batch([filing(RECENT)], consumer, fetcher=FakeFetcher({URL(RECENT): FakeResp(200, PDF)}),
                               temp_root=root, request_delay_seconds=0)
    rec = out["records"][0]
    blob = json.dumps(out)                                          # must be plain metadata
    assert "%PDF" not in blob and captured["path"] not in blob and root not in blob
    assert not any(isinstance(v, (bytes, bytearray)) for v in rec.values())
    assert not any(k in rec for k in ("temp_path", "local_path", "file_path", "storage_path", "blob", "content"))
    src = open(os.path.join(REPO, "worker", "document_retrieval.py"), encoding="utf-8").read()
    for forbidden in ("import psycopg2", "from . import db", "report_filings_store", "supabase", "storage.from_",
                      "insert into", "boto3"):
        assert forbidden not in src.lower(), forbidden
    for mig in glob.glob(os.path.join(REPO, "supabase", "migrations", "*.sql")):
        sql = open(mig, encoding="utf-8").read().lower()
        assert "bytea" not in sql and not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql), mig
    assert rec["source_path"] == RECENT and rec["final_url"] == URL(RECENT)   # CSE metadata is kept on purpose
    print("PASS: records keep CSE source_path/final_url but no bytes and no temp/local path; "
          "F2 code has no DB/storage writes; no migration stores documents")


def test_cli_cap_and_end_to_end_with_fake_network():
    from worker import retrieve_filing_documents as cli
    with TempRoot() as root:
        many = os.path.join(root, "many.json")
        json.dump([filing(RECENT, fid=i) for i in range(cli.MAX_FILINGS_PER_RUN + 1)], open(many, "w"))
        try:
            cli.main(["--filings-json", many, "--report-file", os.path.join(root, "r.json")])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("cap not enforced")

        state = os.path.join(root, "f1_state.json")
        json.dump({"filings": [{"cse_filing_id": 11, "path": RECENT, "path2": None}]}, open(state, "w"))
        orig = dr.RequestsFetcher
        dr.RequestsFetcher = lambda: FakeFetcher({URL(RECENT): FakeResp(200, PDF)})
        try:
            code = cli.main(["--f1-state", state, "--ids", "11", "--temp-root", root,
                             "--request-delay-seconds", "0", "--report-file", os.path.join(root, "r.json")])
        finally:
            dr.RequestsFetcher = orig
        report = json.load(open(os.path.join(root, "r.json")))
        assert code == 0 and report["records"][0]["consumer_status"] == "succeeded" and not _leftovers(root)
    print("PASS: CLI enforces the 20-filing governance cap; end-to-end run verifies and deletes")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nAll Stage F2 document-retrieval tests passed.")
