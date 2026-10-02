"""
Per-attempt outcome classification (design sections 16.2 and 16.3). Pure: no database, no network.

JSON (www.cse.lk, getFinancialAnnouncement / financials): P2's classification ORDER and vocabulary
(worker/market_capture/http.py classify), reproduced, because P2's own shape check accepts only P2's three market
endpoints. The shape check here is F1's own acceptance (report_discovery.extract_feed_items / extract_listing_buckets)
on the exact response bytes, so an attempt is 'ok' exactly when F1 would ingest it.

Documents (cdn.cse.lk, one F2 fetch() call): API-style blocks except that a CDN 403 is F2's 'forbidden or missing' for
one document, never a block (section 16.3); 404 is 'not found'. Both are terminal for the attempt and count towards the
circuit breaker.

Outcome classes are HB-1's (migration 0016 chk_bfro_class): ok | retryable | terminal | block | unrecorded.
"""
import json
from dataclasses import dataclass
from typing import Any, Optional

from .. import report_discovery as f1
from ..market_capture import config as p2config

API_HOST = "www.cse.lk"
CDN_HOST = "cdn.cse.lk"
JSON_ENDPOINTS = {f1.FEED_ENDPOINT: ("fromDate", "toDate"), f1.LISTING_ENDPOINT: ("symbol",)}
JSON_METHOD = "POST"                                  # Stage E / F1 send both as form data
JSON_ACCEPT = "application/json"

API_BLOCK_STATUSES = (401, 403, 407, 451)             # section 16.3: block, stop every stage
CDN_BLOCK_STATUSES = (401, 407, 451)                  # a CDN 403 is NOT a block (F2: forbidden or missing)
RATE_LIMITED = 429
# P2's RETRYABLE set (worker/market_capture/http.py), reproduced; a unit test compares the two.
RETRYABLE = ("server_error", "network_error", "timeout", "empty_response", "invalid_json", "malformed_response",
             "rate_limited")
TERMINAL = ("too_large", "http_error", "unexpected_redirect", "forbidden_or_missing", "not_found", "redirect",
            "spool_failed", "stream_not_closed")


def json_url(endpoint):
    if endpoint not in JSON_ENDPOINTS:
        raise ValueError(f"{endpoint!r} is not a Phase 2 JSON endpoint")
    return f"{p2config.BASE_URL}/{endpoint}"


@dataclass
class TransportResponse:
    """The fields F1's extract_feed_items / extract_listing_buckets read, with Stage E's CSEResponse names and meaning
    (a unit test compares the field set), built from the exact bytes of one governed attempt."""
    endpoint: str
    request_method: str
    request_params: dict
    status_code: Optional[int]
    ok: bool
    body: Any = None
    raw_text: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


def parse_body(body):
    """(parse_status, parsed) for exact bytes: None | 'empty' | 'not_json' | 'json_ok' (P2's rule)."""
    if body is None:
        return None, None
    if not body.strip():
        return "empty", None
    try:
        return "json_ok", json.loads(body)
    except ValueError:
        return "not_json", None


def transport_response(endpoint, params, ex, parse_status=None, parsed=None):
    """What Stage E's client would have returned for the same exchange: ok is requests' Response.ok (status < 400),
    body is the parsed JSON, raw_text the first 2,000 characters of an unparseable body."""
    ok = ex.status is not None and ex.status < 400
    r = TransportResponse(endpoint=endpoint, request_method=JSON_METHOD, request_params=dict(params),
                          status_code=ex.status, ok=ok, elapsed_ms=ex.elapsed_ms)
    if ex.status is None:
        r.ok, r.error = False, ex.error
        return r
    if parse_status == "json_ok":
        r.body = parsed
    elif ex.body is not None:
        r.raw_text = ex.body.decode("utf-8", "replace")[:2000]
        r.error = "Response was not valid JSON"
    elif ex.error:
        r.error = ex.error
    return r


def f1_accepts(endpoint, parsed):
    """F1's own acceptance of a 2xx JSON body: no failure category from its extractor."""
    probe = TransportResponse(endpoint=endpoint, request_method=JSON_METHOD, request_params={}, status_code=200,
                              ok=True, body=parsed)
    if endpoint == f1.FEED_ENDPOINT:
        return f1.extract_feed_items(probe)[1] is None
    if endpoint == f1.LISTING_ENDPOINT:
        return f1.extract_listing_buckets(probe)[2] is None
    return False


def classify_json(endpoint, ex):
    """(outcome, parse_status, parsed) of one JSON attempt, in P2's order: transport errors, block statuses, 429, 5xx,
    3xx, other non-2xx, empty, not JSON, F1 shape, ok."""
    parse_status, parsed = parse_body(ex.body)
    if ex.error_kind == "timeout":
        return "timeout", None, None
    if ex.error_kind == "too_large":
        return "too_large", None, None
    if ex.error_kind == "network":
        return "network_error", None, None
    s = ex.status
    if s in API_BLOCK_STATUSES:
        return "blocked", parse_status, parsed
    if s == RATE_LIMITED:
        return "rate_limited", parse_status, parsed
    if s is not None and 500 <= s <= 599:
        return "server_error", parse_status, parsed
    if s is not None and 300 <= s <= 399:
        return "unexpected_redirect", parse_status, parsed
    if s is None or not 200 <= s <= 299:
        return "http_error", parse_status, parsed
    if parse_status == "empty":
        return "empty_response", parse_status, None
    if parse_status == "not_json":
        return "invalid_json", parse_status, None
    if not f1_accepts(endpoint, parsed):
        return "malformed_response", parse_status, parsed
    return "ok", parse_status, parsed


def classify_cdn(status, error_kind=None, stream_error=False):
    """Outcome of one F2 fetch() call: error_kind is 'timeout' | 'network' when fetch itself failed; stream_error when
    reading the body failed after the headers arrived."""
    if error_kind == "timeout":
        return "timeout"
    if error_kind == "network":
        return "network_error"
    if status in CDN_BLOCK_STATUSES:
        return "blocked"
    if status == 403:
        return "forbidden_or_missing"
    if status == 404:
        return "not_found"
    if status == RATE_LIMITED:
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    if 300 <= status <= 399:
        return "redirect"                     # F2 follows it (cdn.cse.lk only) with a new, separately governed fetch
    if not 200 <= status <= 299:
        return "http_error"
    if stream_error:
        return "network_error"
    return "ok"


def outcome_class(outcome, *, block=False):
    """HB-1's outcome class. `block` is the caller's decision for a 429 (beyond the Retry-After bound, on the last
    attempt, or ending a circuit-breaker run), which P2 also treats as a block."""
    if block or outcome == "blocked":
        return "block"
    if outcome == "ok":
        return "ok"
    if outcome in RETRYABLE:
        return "retryable"
    if outcome in TERMINAL:
        return "terminal"
    raise ValueError(f"unknown outcome {outcome!r}")
