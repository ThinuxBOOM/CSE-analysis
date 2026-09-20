"""
CSE HTTP client. Deliberately isolated: this module knows nothing about
Supabase, raw_market_observations, or the reconciliation schema. It just
makes requests and returns exactly what CSE returned, verbatim, with
diagnostic metadata about the request/response.

Testable independently of the database, per the Stage B constraint.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from . import config

BASE_URL = "https://www.cse.lk/api"


@dataclass
class CSEResponse:
    """
    Wraps a single CSE API call with everything needed for both mapping and
    full audit-trail storage in raw_payload. `body` is the parsed JSON,
    unmodified — no renaming, no flattening, no fabrication.
    """
    endpoint: str
    request_method: str
    request_params: dict
    status_code: Optional[int]
    ok: bool
    body: Any = None                 # parsed JSON, exactly as CSE returned it
    raw_text: Optional[str] = None   # populated only if JSON parsing failed
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


def _post(endpoint: str, data: Optional[dict] = None) -> CSEResponse:
    url = f"{BASE_URL}/{endpoint}"
    headers = {"User-Agent": config.get_user_agent(), "Accept": "application/json"}
    start = time.monotonic()
    try:
        resp = requests.post(url, data=data or {}, headers=headers, timeout=15)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = CSEResponse(
            endpoint=endpoint,
            request_method="POST",
            request_params=data or {},
            status_code=resp.status_code,
            ok=resp.ok,
            elapsed_ms=elapsed_ms,
        )
        try:
            result.body = resp.json()
        except ValueError:
            result.raw_text = resp.text[:2000]
            result.error = "Response was not valid JSON"
        return result
    except requests.exceptions.RequestException as exc:
        return CSEResponse(
            endpoint=endpoint,
            request_method="POST",
            request_params=data or {},
            status_code=None,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def _get(endpoint: str, params: Optional[dict] = None) -> CSEResponse:
    url = f"{BASE_URL}/{endpoint}"
    headers = {"User-Agent": config.get_user_agent(), "Accept": "application/json"}
    start = time.monotonic()
    try:
        resp = requests.get(url, params=params or {}, headers=headers, timeout=15)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = CSEResponse(
            endpoint=endpoint,
            request_method="GET",
            request_params=params or {},
            status_code=resp.status_code,
            ok=resp.ok,
            elapsed_ms=elapsed_ms,
        )
        try:
            result.body = resp.json()
        except ValueError:
            result.raw_text = resp.text[:2000]
            result.error = "Response was not valid JSON"
        return result
    except requests.exceptions.RequestException as exc:
        return CSEResponse(
            endpoint=endpoint,
            request_method="GET",
            request_params=params or {},
            status_code=None,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def get_company_info_summary(symbol: str) -> CSEResponse:
    """POST /api/companyInfoSummery — body: {symbol: "X.N0000"}"""
    return _post("companyInfoSummery", data={"symbol": symbol})


def get_trade_summary_all() -> CSEResponse:
    """
    POST /api/tradeSummary — no meaningful body, returns ALL securities.
    We filter to our target symbol AFTER receiving the response — this
    endpoint does not appear to support server-side filtering by symbol.
    """
    return _post("tradeSummary", data={})


def get_all_security_codes() -> CSEResponse:
    """
    GET /api/allSecurityCode — the CSE company/security universe. One of the
    few confirmed GET-only endpoints (per Phase 0 recon), unlike almost
    everything else in this API which is POST.

    Response shape is NOT yet confirmed against a real live call in this
    codebase — third-party recon describes it as a list of {id, symbol, ...}
    entries, but per this project's own discipline, that's treated as an
    expectation to verify, not a fact. See
    extract_symbol_list_from_all_security_codes() for the defensive parsing.
    """
    return _get("allSecurityCode")


def extract_symbol_list_from_all_security_codes(response: CSEResponse) -> list:
    """
    Defensively extracts a list of symbol strings from the allSecurityCode
    response, without assuming its exact shape. Returns an empty list (never
    None, never a fabricated placeholder) if nothing recognizable is found —
    callers must treat an empty list as "discovery failed", not "CSE has no
    securities".
    """
    body = response.body
    if body is None:
        return []

    candidates = []
    if isinstance(body, list):
        candidates = body
    elif isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list):
                candidates = v
                break

    symbols = []
    for entry in candidates:
        if isinstance(entry, dict):
            # Try the most likely key names without assuming which one is
            # correct — record what we actually find.
            sym = entry.get("symbol") or entry.get("Symbol") or entry.get("securityCode")
            if sym:
                symbols.append(sym)
        elif isinstance(entry, str):
            symbols.append(entry)
    return symbols


def extract_symbol_row_from_trade_summary(
    trade_summary_response: CSEResponse, symbol: str
) -> Optional[dict]:
    """
    tradeSummary returns an array (possibly wrapped in a key — unconfirmed
    until we see a real response). This function does NOT assume the shape;
    it searches defensively and returns None (not a fabricated empty dict)
    if it can't find the symbol, so the caller can distinguish "not found"
    from "found with nulls".
    """
    body = trade_summary_response.body
    if body is None:
        return None

    candidates = []
    if isinstance(body, list):
        candidates = body
    elif isinstance(body, dict):
        # Defensive: search any top-level list value for symbol-bearing rows,
        # since we don't yet know if this is wrapped like
        # {"reqTradeSummery": [...]} or similar.
        for v in body.values():
            if isinstance(v, list):
                candidates = v
                break

    for row in candidates:
        if isinstance(row, dict) and row.get("symbol") == symbol:
            return row
    return None
