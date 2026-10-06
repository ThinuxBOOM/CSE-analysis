"""
F2 retrieval records -> what happens to the document item (design sections 9 HB-R6, 14.2, 16.3 and 24). Pure.

F2 keeps its own outcome and failure category (worker/document_retrieval.py); this module only reads them:

    cleanup     F2 could not verify the deletion                       -> cleanup_failed: STOP the stage (17)
    leftover    F2's leftover check found a temporary entry             -> failed, STOP the stage (10.1)
    persist     consumer succeeded and deletion verified                -> one transaction: _persist + 'persisted'
    consumer    F3/F4/F5 raised inside the consumer                     -> consumer_failed (terminal for the versions)
    terminal    forbidden_or_missing (403), not_found (404), too_large, not_pdf, a disallowed redirect, another 4xx,
                F2's internal error, a path F2 does not accept           -> retrieval_failed
    retryable   5xx, timeouts, network errors, interrupted streams, a 429, a truncated, mismatched or empty body, a
                local hashing failure                                   -> another F2 pass, retry_wait, or, at the
                                                                           item maximum, retrieval_failed
    local       the temporary root itself failed inside F2              -> retry_wait, and the slice stops

A block (CDN 401/407/451, or a 429 beyond its bounds) is HB-2's to detect and record (L9); the worker reads it from the
slice, never from F2's category.
"""
CLEANUP, LEFTOVER, PERSIST, CONSUMER, TERMINAL, RETRYABLE, LOCAL = (
    "cleanup", "leftover", "persist", "consumer", "terminal", "retryable", "local")

# The F2 outcomes HB-1's guard accepts for 'retrieval_failed' (migration 0016 hb_item_event_guard).
RETRIEVAL_FAILED_OUTCOMES = ("no_document", "invalid_path", "download_failed", "validation_failed", "hash_failed",
                             "internal_error")

# F2 failure categories (worker/document_retrieval.py), by what retrying can change.
TERMINAL_CATEGORIES = frozenset({
    "forbidden_or_missing", "not_found", "too_large", "unexpected_redirect", "too_many_redirects", "not_pdf",
    "null_path", "invalid_path", "not_expected_type"})
RETRYABLE_CATEGORIES = frozenset({
    "server_error", "network_error", "timeout", "download_interrupted", "truncated", "length_mismatch",
    "etag_mismatch", "truncated_or_malformed", "empty_body"})
RATE_LIMITED_STATUS = 429


def retrieved(rec):
    """Did F2 hand the document to the consumer (validated and hashed)?"""
    return rec.get("consumer_status") in ("succeeded", "failed")


def disposition(rec, leftovers):
    """One of the dispositions above, for F2's record of one pass and its batch leftover list."""
    if rec["cleanup_status"] == "failed" or rec["outcome"] == "cleanup_failed":
        return CLEANUP
    if leftovers:
        return LEFTOVER
    outcome, category = rec["outcome"], rec.get("failure_category") or ""
    if outcome == "succeeded":
        return PERSIST if rec["consumer_status"] == "succeeded" and rec["cleanup_status"] == "deleted" else CLEANUP
    if outcome == "consumer_failed":
        return CONSUMER
    if outcome in ("no_document", "invalid_path", "internal_error"):
        return TERMINAL
    if outcome == "hash_failed":
        return RETRYABLE
    if outcome == "validation_failed":
        return TERMINAL if category in TERMINAL_CATEGORIES else RETRYABLE
    if outcome == "download_failed":
        if category.startswith("temp_root:"):
            return LOCAL
        if category in RETRYABLE_CATEGORIES:
            return RETRYABLE
        if category == "unexpected_status":
            return RETRYABLE if rec.get("http_status") == RATE_LIMITED_STATUS else TERMINAL
        return TERMINAL
    return TERMINAL


def live_state(kind, claims, item_max):
    """The item's state after a pass whose disposition is `kind` (retrieval failures only): retry_wait below the item
    maximum, retrieval_failed at it (design section 14.2: 'retry_wait ... attempts < max'). A local failure is never
    the document's: it waits (and the start-of-slice terminalisation decides at the maximum)."""
    if kind == TERMINAL:
        return "retrieval_failed"
    if kind == RETRYABLE:
        return "retry_wait" if claims < item_max else "retrieval_failed"
    if kind == LOCAL:
        return "retry_wait"
    raise ValueError(f"{kind!r} is not a retrieval failure")
