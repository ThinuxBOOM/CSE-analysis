"""
The governed F2 fetcher (design section 16.2): F2's own `fetch(url) -> FetchResponse` boundary
(worker/document_retrieval.py), unchanged on F2's side. F2 keeps its URL resolution, legacy cmt/ fallback, redirect
policy (at most two, to cdn.cse.lk only), validation, hashing, temporary directory and verified deletion. This module
is the only one in the transport that imports a network library.

Every fetch() call - a first request, a legacy fallback or a redirect F2 follows - is one CSE request, separately:
admitted by the slice (same arming in force, budgets) -> HB-1 intent committed -> paced -> sent (P2's hardened
session: no environment proxies, no cookies, no automatic redirects; F2's exact headers; the armed User-Agent) ->
streamed to F2 chunk by chunk (never buffered, never written by this module) -> its outcome committed when F2 closes
the response, or at once if the request itself failed. The document's attempt ids are collected for its L6 record.

A refused request raises GovernanceRefusal inside F2, which F2 records as a network failure; `refused` tells the
document worker (HB-4) the real reason. A block or an open circuit is recorded and stops the slice: the next fetch()
is refused. Documents never enter the spool or L5.
"""
from datetime import datetime, timezone

from .. import document_retrieval as f2
from ..market_capture import http as p2http
from . import classify, ledger
from .errors import Blocked, CircuitOpen, DurabilityStop, GovernanceRefusal, Refused

CDN_BASE = f2.CDN_BASE
TIMEOUT_SECONDS = f2.DEFAULT_TIMEOUT_SECONDS
CHUNK_SIZE = f2.CHUNK_SIZE


def f2_headers(user_agent):
    """F2's own fetcher headers exactly, with the armed User-Agent."""
    return {"User-Agent": user_agent, "Accept-Encoding": "identity", "Accept": "*/*"}


def _wall(sl):
    return sl.rt.wall() if sl is not None else datetime.now(timezone.utc)


class _Stream:
    """F2 reads the body through this: chunks pass straight through; only their count and size are kept."""

    def __init__(self, inner):
        self.inner, self.bytes, self.complete, self.error = inner, 0, False, None

    def __iter__(self):
        try:
            for chunk in self.inner:
                self.bytes += len(chunk)
                yield chunk
            self.complete = True
        except Exception as exc:  # noqa: BLE001 — recorded, then F2 sees the same exception
            self.error = f"{type(exc).__name__}: {exc}"
            raise


class GovernedFetcher:
    def __init__(self, sl, item_id, *, pass_no=1, last_pass=True):
        self.sl, self.item_id, self.pass_no, self.last_pass = sl, item_id, pass_no, last_pass
        self.session = sl.rt.session(sl.policy)
        self.attempt_ids = []
        self.outcomes = []
        self.refused = None

    # -------------------------------------------------------------------------------------------- F2's interface

    def fetch(self, url):
        sl = self.sl
        try:
            if not isinstance(url, str) or not url.startswith(CDN_BASE):
                raise Refused([("url", "the governed fetcher requests https://cdn.cse.lk/ only")])
            sl.admit(need=1)
        except Refused as exc:
            self.refused = exc.refusals
            raise GovernanceRefusal(str(exc)) from None
        hdrs = f2_headers(sl.user_agent)
        attempt_id, attempt_no = ledger.record_intent(
            sl.conn, self.item_id, sl.lease_id, sl.wakeup_id, request_class="document",
            request_host=classify.CDN_HOST, endpoint="cdn", url=url, user_agent=sl.user_agent, params={},
            headers={k.lower(): v for k, v in hdrs.items()})
        self.attempt_ids.append(attempt_id)
        sl.throttle.before()
        requested_at = _wall(sl)
        t0 = sl.rt.clock()
        try:
            r = self.session.get(url, headers=hdrs, stream=True, allow_redirects=False, timeout=TIMEOUT_SECONDS,
                                 proxies={})
        except Exception as exc:  # noqa: BLE001 — recorded now; F2 classifies the same exception as it always did
            sl.throttle.after()
            kind = "timeout" if _is_timeout(exc) else "network"
            self._record(attempt_id, classify.classify_cdn(None, error_kind=kind), status=None, headers=None,
                         nbytes=None, requested_at=requested_at, observed_at=None,
                         elapsed_ms=int((sl.rt.clock() - t0) * 1000), error=f"{type(exc).__name__}: {exc}",
                         complete=False)
            raise
        status = r.status_code
        raw_headers = dict(r.headers)
        stream = _Stream(r.iter_content(chunk_size=CHUNK_SIZE))
        state = {"closed": False}

        def close():
            if state["closed"]:
                return
            state["closed"] = True
            try:
                r.close()
            finally:
                sl.throttle.after()
                self._record(attempt_id, classify.classify_cdn(status, stream_error=stream.error is not None),
                             status=status, headers=raw_headers, nbytes=stream.bytes, requested_at=requested_at,
                             observed_at=_wall(sl), elapsed_ms=int((sl.rt.clock() - t0) * 1000), error=stream.error,
                             complete=stream.complete)

        return f2.FetchResponse(status=status, headers={k.lower(): v for k, v in raw_headers.items()},
                                chunks=iter(stream), close=close)

    # -------------------------------------------------------------------------------------------- recording

    def _record(self, attempt_id, outcome, *, status, headers, nbytes, requested_at, observed_at, elapsed_ms, error,
                complete):
        sl, policy = self.sl, self.sl.policy
        tripped = sl.note_outcome(outcome == "ok")
        block_reason = None
        if outcome == "blocked":
            block_reason = f"the CDN refused a document request with HTTP {status}: Phase 2 stopped (G-1)"
        elif outcome == "rate_limited":
            wanted = p2http.retry_after_seconds({k.lower(): v for k, v in (headers or {}).items()}, sl.rt.wall())
            if wanted is not None and wanted > policy.retry_after_max_seconds:
                block_reason = f"the CDN rate-limited a document and asked for {wanted:.0f} s; stopping (G-1)"
            elif self.last_pass:
                block_reason = "the CDN kept rate-limiting a document on its last pass; Phase 2 stopped (G-1)"
            else:
                sl.throttle.hold(max(wanted if wanted is not None else policy.backoff(1),
                                     policy.min_interval_seconds))
        if block_reason is None and outcome != "ok" and tripped and outcome == "rate_limited":
            block_reason = (f"{sl.consecutive_failures} consecutive failed requests ending in rate limiting at the "
                            f"CDN; Phase 2 stopped (G-1)")
        kept, removed = p2http.sanitize_headers(headers) if headers is not None else (None, [])
        o = {"outcome": outcome, "outcome_class": classify.outcome_class(outcome, block=block_reason is not None),
             "requested_at": requested_at, "observed_at": observed_at, "elapsed_ms": elapsed_ms,
             "http_status": status, "response_headers": kept, "removed_response_headers": removed,
             "response_bytes": nbytes, "error": error,
             "details": {"pass": self.pass_no, "stream_complete": bool(complete), "item_id": str(self.item_id)}}
        try:
            block_id = ledger.record_outcome(sl.conn, attempt_id, o, None, block_reason, sl.wakeup_id)
        except Exception as exc:  # noqa: BLE001 — never raised into F2's cleanup; the slice stops instead
            sl.stop = DurabilityStop(f"a document attempt's outcome could not be committed: {type(exc).__name__}: "
                                     f"{exc}")
            return
        self.outcomes.append((attempt_id, outcome, o["outcome_class"]))
        sl.heartbeat()
        if block_id is not None:
            sl.stop = Blocked(block_reason, block_id)
        elif outcome != "ok" and tripped:
            sl.stop = CircuitOpen(f"{sl.consecutive_failures} consecutive failed requests; the slice stops")


def _is_timeout(exc):
    import requests                                   # the only network import of the transport
    return isinstance(exc, requests.exceptions.Timeout)
