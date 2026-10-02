"""
The governed JSON requester (design section 16.2): one logical request of one claimed discovery item, with P2's retry
discipline reproduced (P2's request loop in worker/market_capture/http.py; parity-tested), recorded in HB-1's ledger.

Each attempt, in this order:
    the slice admits it (same arming in force, budgets, slice bounds) -> the HB-1 intent row is committed ->
    the journal intent line (fsync) -> the throttle -> the request (P2's RequestsTransport) -> classification ->
    the exact body and the outcome record into the spool -> the outcome row (+ the L5 body, + the L9 block) committed
    in one transaction -> the block / circuit-breaker / retry decision

Decisions, in P2's order: an OK resets the breaker; a block status stops at once (L9); a 429 waits for Retry-After
within the bound, and becomes a block beyond it or on the last attempt; N consecutive non-OK attempts of any kind stop
the slice (a block when they end in rate limiting); only then is a retryable outcome retried, after P2's backoff.

The result carries a TransportResponse that HB-3 hands to F1's own helpers; nothing here parses a filing.
"""
from dataclasses import dataclass, field
from typing import Optional

from ..market_capture import http as p2http
from . import classify, gates, journal, ledger
from .errors import Blocked, CircuitOpen, DurabilityStop, Refused


@dataclass
class AttemptResult:
    attempt_id: int
    attempt_no: int
    outcome: str
    outcome_class: str
    http_status: Optional[int]
    body_sha256: Optional[str]
    spool_record_key: Optional[str]


@dataclass
class JsonResult:
    endpoint: str
    params: dict
    attempts: list = field(default_factory=list)
    ok: bool = False
    response: Optional[classify.TransportResponse] = None
    block_id: Optional[int] = None

    @property
    def outcome(self):
        return self.attempts[-1].outcome if self.attempts else None


def _check_request(endpoint, params, arming):
    want = classify.JSON_ENDPOINTS.get(endpoint)
    if want is None:
        return [("request", f"{endpoint!r} is not a Phase 2 JSON endpoint")]
    if sorted(params) != sorted(want) or not all(isinstance(params[k], str) and params[k] for k in want):
        return [("request", f"{endpoint} takes exactly the form fields {list(want)}")]
    return gates.window_refusals(endpoint, params, arming)


def headers(user_agent):
    """Stage E's request headers, with the armed User-Agent (lower-cased names, as recorded)."""
    return {"User-Agent": user_agent, "Accept": classify.JSON_ACCEPT}


def _outcome(ex, outcome, block_reason, parse_status):
    return {"outcome": outcome, "outcome_class": classify.outcome_class(outcome, block=block_reason is not None),
            "requested_at": ex.requested_at, "observed_at": ex.observed_at, "elapsed_ms": ex.elapsed_ms,
            "http_status": ex.status, "response_headers": ex.response_headers,
            "removed_response_headers": list(ex.removed_response_headers or []),
            "response_bytes": len(ex.body) if ex.body is not None else None, "body_sha256": None,
            "spool_body_key": None, "spool_record_key": None, "parse_status": None, "error": ex.error,
            "block_reason": block_reason, "details": {}}


def request(sl, item_id, endpoint, params):
    """Make one logical request for a claimed item. Returns a JsonResult (ok or not); raises Blocked, CircuitOpen,
    Refused or DurabilityStop when the slice must stop."""
    refusals = _check_request(endpoint, params, sl.arming)
    if refusals:
        raise Refused(refusals)
    params = dict(params)
    url = classify.json_url(endpoint)
    result = JsonResult(endpoint=endpoint, params=params)
    allowed = sl.json_attempts_left(item_id)
    if allowed <= 0:
        raise Refused([("json_attempts", f"item {item_id} used its JSON attempts in this slice")])
    policy, transport = sl.policy, sl.rt.transport(sl.policy)
    for i in range(allowed):
        sl.admit(need=1, new_work=(i == 0))
        hdrs = headers(sl.user_agent)
        attempt_id, attempt_no = ledger.record_intent(
            sl.conn, item_id, sl.lease_id, sl.wakeup_id, request_class="json", request_host=classify.API_HOST,
            endpoint=endpoint, url=url, user_agent=sl.user_agent, params=params,
            headers={k.lower(): v for k, v in hdrs.items()})
        sl.note_json_attempt(item_id)
        attempt = {"attempt_id": attempt_id, "attempt_no": attempt_no, "item_id": item_id, "lease_id": sl.lease_id,
                   "endpoint": endpoint, "url": url, "params": params, "user_agent": sl.user_agent}
        try:
            journal.intent(sl.journal, attempt_id, attempt_no, item_id)
        except journal.SpoolUnavailable as exc:
            ledger.record_outcome(sl.conn, attempt_id, {"outcome": "spool_failed", "outcome_class": "terminal",
                                                        "error": f"not sent: {exc}", "details": {"sent": False}})
            sl.stop = DurabilityStop(str(exc))
            raise sl.stop
        sl.throttle.before()
        try:
            ex = transport.send("POST", url, dict(params), hdrs,
                                (policy.connect_timeout_seconds, policy.read_timeout_seconds),
                                clock=sl.rt.clock, wall=sl.rt.wall)
        finally:
            sl.throttle.after()
        outcome, parse_status, parsed = classify.classify_json(endpoint, ex)
        tripped = sl.note_outcome(outcome == "ok")
        last = i == allowed - 1
        block_reason, delay, stop_circuit = None, None, False
        if outcome == "blocked":
            block_reason = f"CSE refused {endpoint} with HTTP {ex.status}: Phase 2 stopped (G-1)"
        elif outcome == "rate_limited":
            wanted = p2http.retry_after_seconds(ex.response_headers, sl.rt.wall())
            if wanted is not None and wanted > policy.retry_after_max_seconds:
                block_reason = f"CSE rate-limited {endpoint} and asked for {wanted:.0f} s; stopping (G-1)"
            elif last:
                block_reason = f"CSE kept rate-limiting {endpoint}; Phase 2 stopped (G-1)"
            else:
                delay = wanted
        if block_reason is None and outcome != "ok" and tripped:
            if outcome == "rate_limited":
                block_reason = (f"{sl.consecutive_failures} consecutive failed requests ending in rate limiting at "
                                f"{endpoint}; Phase 2 stopped (G-1)")
            else:
                stop_circuit = True
        o = _outcome(ex, outcome, block_reason, parse_status)
        body = None
        spool_error = None
        try:
            if ex.body is not None and outcome != "too_large":
                o["body_sha256"], o["spool_body_key"] = journal.spool_body(sl.journal.root, ex.body)
                o["parse_status"], body = parse_status, ex.body
            o["spool_record_key"] = journal.spool_record(sl.journal, journal.build_record(attempt, o))
        except journal.SpoolUnavailable as exc:
            spool_error = str(exc)
            o.update(body_sha256=None, spool_body_key=None, spool_record_key=None, parse_status=None,
                     details={"spool_failed": spool_error[:300]})
            if o["outcome_class"] == "ok":
                o.update(outcome="spool_failed", outcome_class="terminal")
            body = None
        try:
            block_id = ledger.record_outcome(sl.conn, attempt_id, o, body, block_reason, sl.wakeup_id)
        except Exception as exc:  # noqa: BLE001 — the spool copy stands; the next slice recovers it
            sl.stop = DurabilityStop(f"{endpoint} attempt {attempt_no} is spooled but its outcome could not be "
                                     f"committed: {type(exc).__name__}: {exc}", result.attempts)
            raise sl.stop
        sl.heartbeat()
        result.attempts.append(AttemptResult(attempt_id, attempt_no, o["outcome"], o["outcome_class"], ex.status,
                                             o["body_sha256"], o["spool_record_key"]))
        result.response = classify.transport_response(endpoint, params, ex, parse_status, parsed)
        if block_id is not None:
            result.block_id = block_id
            sl.stop = Blocked(block_reason, block_id, result.attempts)
            raise sl.stop
        if spool_error is not None:
            sl.stop = DurabilityStop(spool_error, result.attempts)
            raise sl.stop
        if o["outcome"] == "ok":
            result.ok = True
            return result
        if stop_circuit:
            sl.stop = CircuitOpen(f"{sl.consecutive_failures} consecutive failed requests; the slice stops",
                                  result.attempts)
            raise sl.stop
        if outcome not in classify.RETRYABLE or last:
            return result
        wait = policy.backoff(i + 1) if delay is None else max(delay, policy.min_interval_seconds)
        sl.rt.sleep(wait)
    return result
