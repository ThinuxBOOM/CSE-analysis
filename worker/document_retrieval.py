"""
Stage F2: temporary document retrieval and lifecycle.

CSE documents are TEMPORARY extraction inputs, never an archive. Given a
report_filings row, this module resolves the CDN URL, streams the document
into a unique temporary directory, validates it, hashes it, hands it to a
consumer (F3/F4 later), and always deletes it — verifying the deletion.
Nothing here writes to a database, object storage, or the repository; the
only durable output is a compact metadata record: CSE source metadata (source
path, resolved URL, HTTP metadata, hashes) but no document bytes and no
temporary/local filesystem path — that path exists only in TempDocument.path,
during the consumer call.

Behaviour below is grounded in live Stage F0/F2.0 observations of cdn.cse.lk
(Amazon S3 behind a CDN), not assumptions:
- Paths starting "cmt/" work directly. Legacy "upload_report_file/..." paths
  return 403 (S3 AccessDenied) directly and 200 with "cmt/" prefixed — seen for
  every legacy path tried (2013-2019), but only tried AFTER the direct URL fails.
- Missing objects also return 403 AccessDenied, so 403 = "forbidden or missing".
- 851 real paths contain spaces/parentheses and 13 contain '&': paths must be
  percent-encoded (quote(path, safe='/')); none contain % # ? + or backslash.
- A default (gzip) GET has no Content-Length and a weak ETag. With
  "Accept-Encoding: identity" the CDN sends exact bytes, a Content-Length and
  a strong ETag equal to the MD5 of the body — two independent integrity checks.
- A "path2" ending in "." returns HTTP 200 with a 0-byte body; a ".jpg" path
  returns 200 image/jpeg. HTTP 200 alone therefore proves nothing.
- http:// answers 301 to the same https URL; https requests were not redirected.
"""
import hashlib
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

from . import config

CDN_BASE = "https://cdn.cse.lk/"
CDN_HOST = "cdn.cse.lk"
LEGACY_PREFIX = "upload_report_file/"
CMT_PREFIX = "cmt/"
FALLBACK_ON_STATUS = (403, 404)          # only these justify trying the legacy cmt/ variant
MAX_REDIRECTS = 2
DEFAULT_MAX_BYTES = 200 * 1024 * 1024    # F0: largest sampled annual report ~20 MB
DEFAULT_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 64 * 1024
PDF_HEADER_WINDOW = 1024                 # PDF spec: header may follow leading junk within 1 KB
PDF_EOF_WINDOW = 2048
STRONG_MD5_ETAG = re.compile(r'^"([0-9a-f]{32})"$')
ZIP_MAGIC = b"PK\x03\x04"


class InvalidPath(ValueError):
    pass


class TemporaryFileCleanupError(RuntimeError):
    """Deletion of a temporary document could not be confirmed. Never swallowed."""


# --- URL resolution (pure) ---------------------------------------------------------

@dataclass(frozen=True)
class UrlCandidate:
    strategy: str      # 'direct' | 'legacy_cmt_prefix'
    url: str


def resolve_candidates(path) -> list:
    """Ordered URL candidates for a CSE document path. Deterministic; raises
    InvalidPath for anything that cannot be a CDN object key."""
    if path is None:
        raise InvalidPath("path is NULL")
    if not isinstance(path, str):
        raise InvalidPath(f"path is {type(path).__name__}, not text")
    if not path.strip():
        raise InvalidPath("path is empty")
    if path != path.strip():
        raise InvalidPath("path has leading/trailing whitespace")
    if path.startswith("/") or "://" in path or "\\" in path:
        raise InvalidPath("path is not a relative CDN key")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise InvalidPath("path contains control characters")
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise InvalidPath("path has empty or relative segments")
    if path.endswith("."):
        raise InvalidPath("path ends in '.' (observed: such CDN keys return 200 with an empty body)")

    candidates = [UrlCandidate("direct", CDN_BASE + quote(path, safe="/"))]
    if path.startswith(LEGACY_PREFIX):
        candidates.append(UrlCandidate("legacy_cmt_prefix", CDN_BASE + quote(CMT_PREFIX + path, safe="/")))
    return candidates


# --- network boundary ---------------------------------------------------------------

@dataclass
class FetchResponse:
    status: int
    headers: dict               # lower-cased keys
    chunks: object              # iterator of bytes
    close: Callable = lambda: None


class RequestsFetcher:
    """The only network code in F2. No redirects are followed automatically,
    identity transfer encoding is requested, and no cookies/auth are sent."""

    def __init__(self, timeout=DEFAULT_TIMEOUT_SECONDS):
        import requests
        self._requests = requests
        self.timeout = timeout

    def fetch(self, url: str) -> FetchResponse:
        headers = {"User-Agent": config.get_user_agent(), "Accept-Encoding": "identity", "Accept": "*/*"}
        r = self._requests.get(url, headers=headers, stream=True, allow_redirects=False, timeout=self.timeout)
        return FetchResponse(status=r.status_code, headers={k.lower(): v for k, v in r.headers.items()},
                             chunks=r.iter_content(chunk_size=CHUNK_SIZE), close=r.close)


# --- validation (pure over file head/tail) --------------------------------------------

def detect_kind(head: bytes) -> str:
    h = head.lstrip()[:64].lower()
    if b"%PDF-" in head[:PDF_HEADER_WINDOW]:
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    if h.startswith(b"<?xml"):
        return "xml"
    if h.startswith(b"<!doctype html") or h.startswith(b"<html") or b"<html" in h:
        return "html"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG"):
        return "png"
    return "unknown" if head else "empty"


def validate_content(expected_kind: str, head: bytes, tail: bytes, byte_length: int,
                     content_length_header, md5_hex: str, etag) -> dict:
    """Returns {'ok', 'category', 'warnings', 'detected_kind', 'pdf_version', 'etag_check'}."""
    warnings = []
    kind = detect_kind(head)
    result = {"ok": False, "category": None, "warnings": warnings, "detected_kind": kind,
              "pdf_version": None, "etag_check": "not_comparable"}

    if content_length_header is not None:
        try:
            declared = int(content_length_header)
        except ValueError:
            declared = None
            warnings.append(f"unparseable Content-Length {content_length_header!r}")
        if declared is not None and declared != byte_length:
            result["category"] = "truncated" if byte_length < declared else "length_mismatch"
            return result
    else:
        warnings.append("no Content-Length: truncation can only be judged structurally")

    m = STRONG_MD5_ETAG.match(etag or "")
    if m:
        result["etag_check"] = "match" if m.group(1) == md5_hex else "mismatch"
        if result["etag_check"] == "mismatch":
            result["category"] = "etag_mismatch"
            return result

    if byte_length == 0:
        result["category"] = "empty_body"
        return result

    if expected_kind == "pdf":
        pos = head.find(b"%PDF-")
        if pos < 0 or pos >= PDF_HEADER_WINDOW:
            result["category"] = "not_pdf"
            return result
        if pos > 0:
            warnings.append(f"%PDF- header at offset {pos}, not 0")
        v = re.match(rb"%PDF-(\d\.\d)", head[pos:pos + 8])
        result["pdf_version"] = v.group(1).decode() if v else None
        if b"%%EOF" not in tail:
            result["category"] = "truncated_or_malformed"
            return result
    elif expected_kind == "zip":
        if kind != "zip":
            result["category"] = "not_expected_type"
            return result
    else:
        raise ValueError(f"unknown expected kind {expected_kind!r}")

    result["ok"] = True
    result["category"] = "valid"
    return result


# --- temporary storage ------------------------------------------------------------------

def _repo_root():
    return os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def validate_temp_root(temp_root: Optional[str]) -> str:
    """Resolves the directory under which per-document temp dirs are made.
    Must exist, be writable, lie OUTSIDE the repository, and lie inside the
    system temp directory (tempfile.gettempdir(), which honours TMPDIR/TEMP) or
    the CI runner's temp directory (RUNNER_TEMP). /tmp is never assumed."""
    root = os.path.realpath(temp_root or tempfile.gettempdir())
    allowed = [os.path.realpath(tempfile.gettempdir())]
    if os.environ.get("RUNNER_TEMP"):
        allowed.append(os.path.realpath(os.environ["RUNNER_TEMP"]))
    repo = _repo_root()

    def inside(child, parent):
        try:
            return os.path.commonpath([child, parent]) == parent
        except ValueError:          # different drives on Windows
            return False

    if not os.path.isdir(root):
        raise ValueError(f"temp root does not exist or is not a directory: {root}")
    if inside(root, repo) or inside(repo, root):
        raise ValueError("temp root must be outside (and must not contain) the repository")
    if not any(inside(root, a) for a in allowed):
        raise ValueError(f"temp root must be inside the system temp directory ({', '.join(allowed)})")
    if not os.access(root, os.W_OK):
        raise ValueError(f"temp root is not writable: {root}")
    return root


def _remove_tree(path, rmtree=shutil.rmtree):
    """Deletes a document's temp dir and CONFIRMS it is gone."""
    try:
        rmtree(path)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — re-raised as a cleanup error below
        raise TemporaryFileCleanupError(f"could not delete temporary document dir: {type(exc).__name__}: {exc}") from exc
    if os.path.exists(path):
        raise TemporaryFileCleanupError("temporary document dir still exists after deletion")


# --- records -----------------------------------------------------------------------------

@dataclass
class Attempt:
    strategy: str
    url: str
    status: Optional[int] = None
    error: Optional[str] = None
    s3_error_code: Optional[str] = None
    redirects: list = field(default_factory=list)


@dataclass
class RetrievalRecord:
    """Compact, storable metadata. Contains CSE source metadata (source_path,
    final_url) but deliberately NO document bytes and NO temporary/local path."""
    cse_filing_id: int
    role: str                              # 'primary' (path) | 'companion' (path2)
    source_path: Optional[str]
    outcome: str = "pending"               # see OUTCOMES
    failure_category: Optional[str] = None
    strategy: Optional[str] = None
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    content_length_header: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    byte_length: Optional[int] = None
    sha256: Optional[str] = None
    md5: Optional[str] = None
    validation: Optional[dict] = None
    attempts: list = field(default_factory=list)
    retrieved_at: Optional[str] = None
    consumer_status: str = "not_run"       # not_run | succeeded | failed
    consumer_error: Optional[str] = None
    cleanup_status: str = "not_needed"     # not_needed | deleted | failed
    cleanup_error: Optional[str] = None

    def to_dict(self):
        return asdict(self)


OUTCOMES = ("succeeded", "no_document", "invalid_path", "download_failed", "validation_failed",
            "hash_failed", "consumer_failed", "internal_error", "cleanup_failed")


@dataclass
class TempDocument:
    """What a consumer (F3/F4) receives. `path` is valid ONLY during the consumer call."""
    cse_filing_id: int
    role: str
    path: str
    sha256: str
    byte_length: int
    content_type: Optional[str]
    final_url: str
    strategy: str


# --- retrieval into a temp dir -------------------------------------------------------

def _s3_code(first_bytes: bytes):
    m = re.search(rb"<Code>([A-Za-z]+)</Code>", first_bytes or b"")
    return m.group(1).decode() if m else None


def _open_following_redirects(fetcher, url, attempt):
    """Follows at most MAX_REDIRECTS, and only to https://cdn.cse.lk/."""
    for _ in range(MAX_REDIRECTS + 1):
        resp = fetcher.fetch(url)
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            resp.close()
            parts = urlsplit(location)
            if parts.scheme != "https" or parts.hostname != CDN_HOST:
                attempt.status = resp.status
                attempt.error = f"redirect to disallowed location {location[:120]!r}"
                return None, "unexpected_redirect"
            attempt.redirects.append({"status": resp.status, "to": location})
            url = location
            continue
        return resp, None
    attempt.error = "too many redirects"
    return None, "too_many_redirects"


def _download(fetcher, candidate, dest_file, max_bytes, hash_factories):
    """Streams one candidate into dest_file. Returns (attempt, response_meta, error_category, hashes, n)."""
    attempt = Attempt(strategy=candidate.strategy, url=candidate.url)
    try:
        resp, redirect_error = _open_following_redirects(fetcher, candidate.url, attempt)
    except Exception as exc:  # noqa: BLE001 — network failures become a category
        attempt.error = f"{type(exc).__name__}: {exc}"
        return attempt, None, ("timeout" if "timeout" in type(exc).__name__.lower() else "network_error"), None, 0
    if redirect_error:
        return attempt, None, redirect_error, None, 0

    attempt.status = resp.status
    meta = {"status": resp.status, "headers": resp.headers,
            "final_url": attempt.redirects[-1]["to"] if attempt.redirects else candidate.url}
    if resp.status != 200:
        try:
            first = next(iter(resp.chunks), b"")
        except Exception:  # noqa: BLE001
            first = b""
        attempt.s3_error_code = _s3_code(first[:2048])
        resp.close()
        if resp.status == 403:
            return attempt, meta, "forbidden_or_missing", None, 0
        if resp.status == 404:
            return attempt, meta, "not_found", None, 0
        if resp.status >= 500:
            return attempt, meta, "server_error", None, 0
        return attempt, meta, "unexpected_status", None, 0

    hashes = {name: f() for name, f in hash_factories.items()}
    n = 0
    try:
        with open(dest_file, "xb") as out:          # exclusive create: never overwrites
            for chunk in resp.chunks:
                if not chunk:
                    continue
                n += len(chunk)
                if n > max_bytes:
                    attempt.error = f"exceeded max_bytes={max_bytes}"
                    return attempt, meta, "too_large", None, n
                out.write(chunk)
                try:
                    for h in hashes.values():
                        h.update(chunk)
                except Exception as exc:  # noqa: BLE001
                    raise _HashError(f"{type(exc).__name__}: {exc}") from exc
    except _HashError as exc:
        attempt.error = str(exc)
        return attempt, meta, "hash_failed", None, n
    except Exception as exc:  # noqa: BLE001 — interrupted stream / disk error
        attempt.error = f"{type(exc).__name__}: {exc}"
        return attempt, meta, "download_interrupted", None, n
    finally:
        resp.close()
    return attempt, meta, None, hashes, n


class _HashError(RuntimeError):
    pass


def _default_hash_factories():
    return {"sha256": hashlib.sha256, "md5": hashlib.md5}


def _read_head_tail(path, head_n=PDF_HEADER_WINDOW, tail_n=PDF_EOF_WINDOW):
    with open(path, "rb") as f:
        head = f.read(head_n)
        size = os.fstat(f.fileno()).st_size
        f.seek(max(0, size - tail_n))
        tail = f.read(tail_n)
    return head, tail, size


def retrieve_into(temp_dir: str, filing: dict, role: str, fetcher, *, max_bytes=DEFAULT_MAX_BYTES,
                  hash_factories=None, now_fn=None):
    """Resolves, downloads, validates and hashes one document into temp_dir.
    Returns (RetrievalRecord, file_path_or_None). Never deletes; the caller's
    lifecycle owns cleanup of temp_dir."""
    hash_factories = hash_factories or _default_hash_factories()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    key = "path" if role == "primary" else "path2"
    expected = "pdf" if role == "primary" else "zip"
    source_path = filing.get(key)
    rec = RetrievalRecord(cse_filing_id=int(filing["cse_filing_id"]), role=role, source_path=source_path)

    if role == "companion" and (source_path is None or source_path == ""):
        rec.outcome, rec.failure_category = "no_document", "no_companion_path"
        return rec, None
    try:
        candidates = resolve_candidates(source_path)
    except InvalidPath as exc:
        rec.outcome = "no_document" if source_path is None else "invalid_path"
        rec.failure_category = "null_path" if source_path is None else "invalid_path"
        rec.attempts.append(asdict(Attempt(strategy="none", url="", error=str(exc))))
        return rec, None

    dest = os.path.join(temp_dir, "document.pdf" if expected == "pdf" else "document.bin")
    for i, cand in enumerate(candidates):
        attempt, meta, error, hashes, n = _download(fetcher, cand, dest, max_bytes, hash_factories)
        rec.attempts.append(asdict(attempt))
        if error is None:
            break
        if os.path.exists(dest):
            os.remove(dest)       # partial file from a failed attempt; the dir is still cleaned later
        can_fall_back = (attempt.status in FALLBACK_ON_STATUS and i + 1 < len(candidates))
        if not can_fall_back:
            rec.outcome = "hash_failed" if error == "hash_failed" else "download_failed"
            rec.failure_category = error
            rec.http_status = attempt.status
            return rec, None

    rec.retrieved_at = now_fn().isoformat()
    headers = meta["headers"]
    rec.strategy, rec.final_url, rec.http_status = attempt.strategy, meta["final_url"], meta["status"]
    rec.content_type = headers.get("content-type")
    rec.content_length_header = headers.get("content-length")
    rec.etag, rec.last_modified = headers.get("etag"), headers.get("last-modified")
    try:
        rec.sha256, rec.md5 = hashes["sha256"].hexdigest(), hashes["md5"].hexdigest()
        head, tail, on_disk = _read_head_tail(dest)
    except Exception as exc:  # noqa: BLE001
        rec.outcome, rec.failure_category = "hash_failed", f"{type(exc).__name__}: {exc}"
        return rec, None
    if on_disk != n:
        rec.outcome, rec.failure_category = "hash_failed", f"on-disk size {on_disk} != streamed {n}"
        return rec, None
    rec.byte_length = n
    rec.validation = validate_content(expected, head, tail, n, rec.content_length_header, rec.md5, rec.etag)
    if rec.content_type and expected == "pdf" and rec.content_type.split(";")[0].strip() not in (
            "application/pdf", "application/octet-stream"):
        rec.validation["warnings"].append(f"unexpected Content-Type {rec.content_type!r}")
    if not rec.validation["ok"]:
        rec.outcome, rec.failure_category = "validation_failed", rec.validation["category"]
        return rec, None
    return rec, dest


# --- lifecycle ------------------------------------------------------------------------

@contextmanager
def temporary_workspace(cse_filing_id: int, temp_root: Optional[str] = None, rmtree=shutil.rmtree):
    """A unique, private directory for ONE document; deleted and verified on
    exit. Raises TemporaryFileCleanupError if deletion cannot be confirmed —
    even if the body raised (the body's exception is chained, not lost)."""
    root = validate_temp_root(temp_root)
    path = tempfile.mkdtemp(prefix=f"cse_f2_{int(cse_filing_id)}_", dir=root)
    body_error = None
    try:
        yield path
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        try:
            _remove_tree(path, rmtree=rmtree)
        except TemporaryFileCleanupError as cleanup_exc:
            if body_error is not None:
                raise cleanup_exc from body_error
            raise


def process_filing(filing: dict, consumer: Optional[Callable] = None, *, role="primary", fetcher=None,
                   temp_root=None, max_bytes=DEFAULT_MAX_BYTES, hash_factories=None, rmtree=shutil.rmtree,
                   now_fn=None) -> RetrievalRecord:
    """Full lifecycle for one filing: resolve -> download -> validate -> hash ->
    consumer(TempDocument) -> delete (verified). Always returns a record; a
    cleanup failure overrides every other outcome ('cleanup_failed')."""
    fetcher = fetcher or RequestsFetcher()
    rec = RetrievalRecord(cse_filing_id=int(filing["cse_filing_id"]), role=role,
                          source_path=filing.get("path" if role == "primary" else "path2"))
    try:
        root = validate_temp_root(temp_root)
    except ValueError as exc:             # nothing was created
        rec.outcome, rec.failure_category = "download_failed", f"temp_root: {exc}"
        return rec
    try:
        with temporary_workspace(rec.cse_filing_id, root, rmtree=rmtree) as tmp:
            try:
                rec, doc_path = retrieve_into(tmp, filing, role, fetcher, max_bytes=max_bytes,
                                              hash_factories=hash_factories, now_fn=now_fn)
            except Exception as exc:  # noqa: BLE001 — a bug must not escape the lifecycle
                rec.outcome, rec.failure_category = "internal_error", f"{type(exc).__name__}: {str(exc)[:200]}"
                doc_path = None
            rec.cleanup_status = "pending"
            if doc_path is not None:
                rec.outcome = "succeeded"
                if consumer is not None:
                    doc = TempDocument(rec.cse_filing_id, role, doc_path, rec.sha256, rec.byte_length,
                                       rec.content_type, rec.final_url, rec.strategy)
                    try:
                        consumer(doc)
                        rec.consumer_status = "succeeded"
                    except Exception as exc:  # noqa: BLE001 — recorded, cleanup still runs
                        rec.consumer_status = "failed"
                        rec.consumer_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                        rec.outcome = "consumer_failed"
        rec.cleanup_status = "deleted"
    except TemporaryFileCleanupError as exc:
        rec.cleanup_status, rec.cleanup_error = "failed", str(exc)
        rec.outcome = "cleanup_failed"
    return rec


def process_batch(filings, consumer=None, *, role="primary", fetcher=None, temp_root=None,
                  request_delay_seconds=1.0, sleep=time.sleep, **kw) -> dict:
    """Processes filings one by one (failure-isolated), then proves no temporary
    files of this batch remain under the temp root."""
    fetcher = fetcher or RequestsFetcher()
    root = validate_temp_root(temp_root)
    before = set(os.listdir(root))
    records = []
    for i, filing in enumerate(filings):
        if i and request_delay_seconds:
            sleep(request_delay_seconds)
        records.append(process_filing(filing, consumer, role=role, fetcher=fetcher, temp_root=root, **kw))
    leftovers = sorted(n for n in set(os.listdir(root)) - before if n.startswith("cse_f2_"))
    counts = {}
    for r in records:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    return {"records": [r.to_dict() for r in records], "outcome_counts": counts,
            "leftover_temp_entries": leftovers,
            "cleanup_failures": [r.cse_filing_id for r in records if r.cleanup_status == "failed"],
            "ok": not leftovers and all(r.cleanup_status != "failed" for r in records)}
