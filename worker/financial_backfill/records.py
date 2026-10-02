"""
F2 retrieval records as ledger rows (design HB-R8, record L6). Pure.

A row keeps what F2 itself keeps on purpose: CSE source metadata (the CDN object key, the final URL), HTTP metadata,
the hashes and the lifecycle statuses. It never keeps document bytes or text, and never a temporary or local path.
Any token naming one of F2's temporary directories (cse_f2_<filing>_...) or a given temporary root is replaced before
anything is stored, errors are truncated, and the database refuses a row that still contains such a token.
"""
import re
from datetime import datetime

# Exactly the fields of worker/document_retrieval.py RetrievalRecord (frozen F2): anything else is not F2's record.
F2_FIELDS = ("cse_filing_id", "role", "source_path", "outcome", "failure_category", "strategy", "final_url",
             "http_status", "content_type", "content_length_header", "etag", "last_modified", "byte_length", "sha256",
             "md5", "validation", "attempts", "retrieved_at", "consumer_status", "consumer_error", "cleanup_status",
             "cleanup_error")
MAX_ERROR = 500                                           # = migration 0016 chk_bfrr_errors / chk_bfro_error
REDACTED = "<temporary>"
TEMPORARY_MARKER = "cse_f2_"                              # F2's per-document directory prefix
_TEMPORARY_TOKEN = re.compile(r"""[^\s'"(),;]*cse_f2_[^\s'"(),;]*""")
_ERROR_CLASS = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]{0,99}):")


def redact_temporary(text, temp_roots=()):
    """`text` without any temporary path, truncated to MAX_ERROR characters (None stays None)."""
    if text is None:
        return None
    s = str(text)
    for root in sorted({r for r in temp_roots if r}, key=len, reverse=True):
        s = s.replace(root, REDACTED)
    return _TEMPORARY_TOKEN.sub(REDACTED, s)[:MAX_ERROR]


def _redact_tree(value, temp_roots):
    if isinstance(value, str):
        return redact_temporary(value, temp_roots)
    if isinstance(value, list):
        return [_redact_tree(v, temp_roots) for v in value]
    if isinstance(value, dict):
        return {k: _redact_tree(v, temp_roots) for k, v in value.items()}
    return value


def error_class(error):
    """The exception class F2 put first in a consumer error ('TextExtractionError: ...'), the design's stage 4/5 split
    (section 18.2)."""
    if error is None:
        return None
    m = _ERROR_CLASS.match(error)
    if m is None:
        raise ValueError("not an F2 consumer error (F2 records '<ExceptionClass>: <message>')")
    return m.group(1)


def retrieval_row(record, *, temp_roots=()):
    """The 0016 backfill_retrieval_records columns for one F2 RetrievalRecord.to_dict()."""
    if set(record) != set(F2_FIELDS):
        raise ValueError(f"not an F2 RetrievalRecord: fields {sorted(set(record) ^ set(F2_FIELDS))} differ")
    if record["role"] != "primary":
        raise ValueError("Phase 2 retrieves primary documents only (HB-R2)")
    if record["source_path"] is None:
        raise ValueError("a document item without a path is excluded (no_document), never retrieved")
    retrieved = record["retrieved_at"]
    return {
        "cse_filing_id": int(record["cse_filing_id"]),
        "role": record["role"],
        "cdn_object_key": record["source_path"],
        "outcome": record["outcome"],
        "failure_category": redact_temporary(record["failure_category"], temp_roots),
        "strategy": record["strategy"],
        "final_url": record["final_url"],
        "http_status": record["http_status"],
        "content_type": record["content_type"],
        "content_length_header": record["content_length_header"],
        "etag": record["etag"],
        "last_modified": record["last_modified"],
        "byte_length": record["byte_length"],
        "document_sha256": record["sha256"],
        "document_md5": record["md5"],
        "validation": _redact_tree(record["validation"], temp_roots),
        "attempts": _redact_tree(record["attempts"] or [], temp_roots),
        "retrieved_at": datetime.fromisoformat(retrieved) if isinstance(retrieved, str) else retrieved,
        "consumer_status": record["consumer_status"],
        "consumer_error": redact_temporary(record["consumer_error"], temp_roots),
        "consumer_error_class": error_class(record["consumer_error"]),
        "cleanup_status": record["cleanup_status"],
        "cleanup_error": redact_temporary(record["cleanup_error"], temp_roots),
    }
