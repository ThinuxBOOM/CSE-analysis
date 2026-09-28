"""
HTTP layer for P2: transport (exact bytes, no proxies, no cookies, no redirects), the G-1 throttle, outcome
classification and the bounded retry/backoff loop. Everything time-related is injectable (clock, wall clock, sleep),
so tests never contact CSE and never sleep for real.

G-1 controls enforced here:
  - ONE request at a time: a non-reentrant lock around every request (and, across processes, the global capture
    advisory lock held by the orchestrator);
  - at least min_interval (>= 1.5 s) from the END of one request to the START of the next, including across process
    boundaries (seeded from the archive's last request);
  - bounded exponential backoff on server errors, network failures, timeouts and unusable bodies;
  - HTTP 429: Retry-After honoured within a bound, a longer one ends the run (never retried early);
  - HTTP 401/403/407/451: the run STOPS at once (no retry, no alternative path);
  - requests.Session with trust_env=False (environment proxies ignored), no proxies, a cookie policy that stores none
    (so none is ever sent), redirects not followed (recorded instead);
  - the configured identifiable User-Agent on every request.
"""
import email.utils
import http.cookiejar
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .. import cse_client

BLOCK_STATUSES = (401, 403, 407, 451)
RETRYABLE = ("server_error", "network_error", "timeout", "empty_response", "invalid_json", "malformed_response",
             "rate_limited")
# Response headers never archived (their NAMES are recorded, so a cookie challenge etc. remains visible as a fact).
SENSITIVE_HEADERS = {"set-cookie", "set-cookie2", "cookie", "authorization", "proxy-authorization",
                     "proxy-authenticate", "www-authenticate", "x-api-key", "x-amz-security-token"}


def utcnow():
    return datetime.now(timezone.utc)


def sanitize_headers(headers):
    """(kept {name: value}, removed [names]) - names lower-cased; values of sensitive headers are never kept."""
    kept, removed = {}, []
    for name, value in (headers or {}).items():
        lname = str(name).lower()
        if lname in SENSITIVE_HEADERS:
            if lname not in removed:
                removed.append(lname)
            continue
        kept[lname] = str(value) if lname not in kept else f"{kept[lname]}, {value}"
    return kept, sorted(removed)


@dataclass
class Exchange:
    """One HTTP attempt as it happened. body is the exact response bytes (after content-coding is removed), None when
    no body was received."""
    method: str
    url: str
    params: dict
    request_headers: dict
    requested_at: datetime
    observed_at: Optional[datetime] = None
    elapsed_ms: Optional[int] = None
    status: Optional[int] = None
    response_headers: Optional[dict] = None
    removed_response_headers: list = field(default_factory=list)
    body: Optional[bytes] = None
    error_kind: Optional[str] = None          # timeout | network | too_large | None
    error: Optional[str] = None


class RequestsTransport:
    """The only real network path. One Session for the process; see the module docstring for the guarantees."""

    def __init__(self, max_body_bytes):
        import requests
        self._requests = requests
        self.max_body_bytes = max_body_bytes
        s = requests.Session()
        s.trust_env = False                   # ignore HTTP(S)_PROXY / ALL_PROXY / NO_PROXY / .netrc entirely
        s.proxies.clear()
        s.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))   # store (hence send) none
        self.session = s

    def send(self, method, url, params, headers, timeout, clock=time.monotonic, wall=utcnow):
        req = self._requests.Request(method, url, headers=headers,
                                     data=params if method == "POST" else None,
                                     params=params if method == "GET" and params else None)
        prep = self.session.prepare_request(req)
        sent_headers, _ = sanitize_headers(dict(prep.headers))
        ex = Exchange(method=method, url=prep.url or url, params=dict(params), request_headers=sent_headers,
                      requested_at=wall())
        t0 = clock()
        try:
            resp = self.session.send(prep, timeout=timeout, allow_redirects=False, stream=True, proxies={})
        except self._requests.exceptions.Timeout as exc:
            ex.error_kind, ex.error = "timeout", f"{type(exc).__name__}: {exc}"
            ex.elapsed_ms = int((clock() - t0) * 1000)
            return ex
        except self._requests.exceptions.RequestException as exc:
            ex.error_kind, ex.error = "network", f"{type(exc).__name__}: {exc}"
            ex.elapsed_ms = int((clock() - t0) * 1000)
            return ex
        try:
            ex.status = resp.status_code
            ex.response_headers, ex.removed_response_headers = sanitize_headers(dict(resp.headers))
            chunks, size = [], 0
            try:
                for chunk in resp.iter_content(65536):          # content-coding (gzip/deflate) removed here
                    size += len(chunk)
                    if size > self.max_body_bytes:
                        ex.error_kind, ex.error = "too_large", f"response body exceeds {self.max_body_bytes} bytes"
                        break
                    chunks.append(chunk)
            except self._requests.exceptions.RequestException as exc:
                ex.error_kind, ex.error = "network", f"body read failed: {type(exc).__name__}: {exc}"
            if ex.error_kind is None:
                ex.body = b"".join(chunks)
        finally:
            resp.close()
        ex.observed_at = wall()
        ex.elapsed_ms = int((clock() - t0) * 1000)
        return ex


def parse_body(body):
    """(parse_status, parsed) for exact bytes: 'empty' | 'not_json' | 'json_ok'."""
    if body is None:
        return None, None
    if not body.strip():
        return "empty", None
    try:
        return "json_ok", json.loads(body)
    except ValueError:
        return "not_json", None


def shape_ok(endpoint, parsed):
    """Structural sanity per endpoint, using Stage E's own tolerant helpers where they exist."""
    if endpoint == "allSecurityCode":
        return bool(cse_client.extract_symbol_list_from_all_security_codes(cse_client.CSEResponse(
            endpoint=endpoint, request_method="GET", request_params={}, status_code=200, ok=True, body=parsed)))
    if endpoint == "tradeSummary":
        return isinstance(parsed, list) or (isinstance(parsed, dict) and any(isinstance(v, list)
                                                                             for v in parsed.values()))
    if endpoint == "companyInfoSummery":
        return isinstance(parsed, dict)
    return False


def classify(endpoint, ex):
    """(outcome, parse_status, parsed). outcome is the archive's classification of one attempt."""
    parse_status, parsed = parse_body(ex.body)
    if ex.error_kind == "timeout":
        return "timeout", None, None
    if ex.error_kind == "too_large":
        return "too_large", None, None
    if ex.error_kind == "network":
        return "network_error", None, None
    s = ex.status
    if s in BLOCK_STATUSES:
        return "blocked", parse_status, parsed
    if s == 429:
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
    if not shape_ok(endpoint, parsed):
        return "malformed_response", parse_status, parsed
    return "ok", parse_status, parsed


def retry_after_seconds(headers, now):
    """Seconds requested by a Retry-After header (delta-seconds or HTTP-date), or None."""
    value = (headers or {}).get("retry-after")
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


class Throttle:
    """At least min_interval from the end of one request to the start of the next (deterministic, injectable)."""

    def __init__(self, min_interval, clock=time.monotonic, sleep=time.sleep):
        self.min_interval, self.clock, self.sleep = min_interval, clock, sleep
        self._last_end = None
        self.waits = []

    def seed(self, seconds_since_last_request):
        """Cross-process spacing: the archive's most recent attempt ended this long ago."""
        if seconds_since_last_request is not None and seconds_since_last_request < self.min_interval:
            self._last_end = self.clock() - max(0.0, seconds_since_last_request)

    def before(self):
        if self._last_end is not None:
            remaining = self._last_end + self.min_interval - self.clock()
            if remaining > 0:
                self.waits.append(remaining)
                self.sleep(remaining)

    def after(self):
        self._last_end = self.clock()


class StopRun(Exception):
    """The run must stop making CSE requests."""
    state = "failed"

    def __init__(self, message, attempt=None):
        super().__init__(message)
        self.attempt = attempt


class Blocked(StopRun):
    state = "blocked"


class RateLimited(Blocked):
    pass


class CircuitOpen(StopRun):
    pass


class BudgetExhausted(StopRun):
    state = "partial"


@dataclass
class FetchResult:
    spec: object
    attempts: list                    # archive results of every attempt made by this fetch, in order
    ok: Optional[object] = None       # the successful attempt's archive result, if any
    parsed: object = None             # parsed JSON of the successful attempt


class Requester:
    """Makes one logical request with bounded retries. Each attempt runs, under one lock:
         archiver.next_attempt_no -> archiver.intent (durable, BEFORE the request) -> throttle -> send ->
         classify -> archiver.archive (spool, then PostgreSQL)
    so every attempt - failures included - is archived before any retry decision, and an archive failure (raised by
    the archiver) stops the run before another request is made."""

    def __init__(self, transport, policy, user_agent, archiver, throttle=None, clock=time.monotonic, wall=utcnow,
                 sleep=time.sleep, max_requests=None, log=lambda m: None):
        self.transport, self.policy, self.user_agent, self.archiver = transport, policy, user_agent, archiver
        self.clock, self.wall, self.sleep, self.log = clock, wall, sleep, log
        self.throttle = throttle or Throttle(policy.min_interval_seconds, clock=clock, sleep=sleep)
        self.max_requests = max_requests
        self.requests_made = 0
        self.consecutive_failures = 0
        self._lock = threading.Lock()                    # one request at a time, even if misused from threads

    def headers(self):
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def _attempt(self, spec):
        with self._lock:
            if self.max_requests is not None and self.requests_made >= self.max_requests:
                raise BudgetExhausted(f"request budget of {self.max_requests} exhausted")
            attempt_no = self.archiver.next_attempt_no(spec.request_key)
            seq = self.archiver.intent(spec, attempt_no)
            self.throttle.before()
            try:
                ex = self.transport.send(spec.method, spec.url, dict(spec.params), self.headers(),
                                         (self.policy.connect_timeout_seconds, self.policy.read_timeout_seconds),
                                         clock=self.clock, wall=self.wall)
            finally:
                self.requests_made += 1
                self.throttle.after()
            outcome, parse_status, parsed = classify(spec.endpoint, ex)
            archived = self.archiver.archive(seq, spec, attempt_no, ex, outcome, parse_status)
            return attempt_no, ex, outcome, parsed, archived

    def fetch(self, spec, attempts=None):
        allowed = attempts or self.policy.attempts[spec.purpose]
        result = FetchResult(spec=spec, attempts=[])
        for i in range(allowed):
            attempt_no, ex, outcome, parsed, archived = self._attempt(spec)
            result.attempts.append(archived)
            self.log(f"  {spec.request_key} attempt {attempt_no}: {outcome}"
                     + (f" (HTTP {ex.status})" if ex.status is not None else ""))
            if outcome == "ok":
                self.consecutive_failures = 0
                result.ok, result.parsed = archived, parsed
                return result
            self.consecutive_failures += 1
            if outcome == "blocked":
                raise Blocked(f"CSE refused {spec.request_key} with HTTP {ex.status}: capture stopped (G-1)", archived)
            last = i == allowed - 1
            delay = None
            if outcome == "rate_limited":
                wanted = retry_after_seconds(ex.response_headers, self.wall())
                if wanted is not None and wanted > self.policy.retry_after_max_seconds:
                    raise RateLimited(f"CSE rate-limited {spec.request_key} and asked for {wanted:.0f} s; stopping "
                                      f"rather than retrying early (G-1)", archived)
                if last:
                    raise RateLimited(f"CSE kept rate-limiting {spec.request_key}; capture stopped (G-1)", archived)
                delay = wanted
            elif outcome not in RETRYABLE:
                return result                              # http_error, redirect, too_large: not retried
            if self.consecutive_failures >= self.policy.max_consecutive_failures:
                raise CircuitOpen(f"{self.consecutive_failures} consecutive failed requests; stopping the run",
                                  archived)
            if last:
                return result
            wait = self.policy.backoff(i + 1) if delay is None else max(delay, self.policy.min_interval_seconds)
            self.log(f"  backing off {wait:.1f} s before retrying {spec.request_key}")
            self.sleep(wait)
        return result
