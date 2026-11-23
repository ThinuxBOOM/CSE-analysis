"""
§4.1: the governed news transport, on P2's pattern (worker/market_capture/http.py), for one source at a time.

Every request, in this order:
  1. the source is not stopped (an earlier block, a long Retry-After, the circuit breaker, an objection);
  2. the host rules of `hosts.check`, BEFORE any socket: CSE refused unconditionally, a CSE host in the URL refused,
     fetch-on-behalf services refused unless the record names them, the allow-list; a refusal is recorded as a
     refused attempt;
  3. robots.txt of the host, read once a Colombo day through this same transport, and followed (record: `obey`);
  4. the daily budget (attempts actually sent, per Colombo day);
  5. the spacing: at least `min_interval_seconds` from the end of the previous request of the source to the start of
     this one, also across processes (seeded from the ledger);
  6. the attempt is recorded BEFORE it is sent; its outcome is a later event.
Responses: 2xx is ok (bodies above the record's byte limit are failed, never truncated); 3xx is recorded and NEVER
followed automatically: its target is requested only if it passes step 2 as an allowed host of the same source, as a
new, separately recorded attempt (at most `max_redirects`); 401, 403, 407 and 451 stop the source at once; 429 waits a
Retry-After within the record's bound, a longer one stops the source; 5xx, timeouts and network failures back off
exponentially up to `max_retries`; 404 and 410 are returned (NS-2 decides about withdrawals); other 4xx are returned.
`max_consecutive_failures` non-ok outcomes in a row stop the source.

Clocks and sleeping are injected; tests use `ScriptedSender` and never touch a network.
"""
import email.utils
import hashlib
import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from . import hosts

COLOMBO = timezone(timedelta(hours=5, minutes=30))
BLOCK_STATUSES = (401, 403, 407, 451)


@dataclass(frozen=True)
class Response:
    status: Optional[int]
    headers: dict = field(default_factory=dict)
    body: Optional[bytes] = None
    error_kind: Optional[str] = None             # timeout | network | too_large


@dataclass
class Result:
    outcome: str                                 # ok | refused | http_<n> | failed | stopped | budget_exhausted |
                                                 # robots_disallowed | redirect_refused | redirect_limit
    code: Optional[str] = None
    response: Optional[Response] = None
    url: Optional[str] = None                    # the URL of the final response
    attempts: list = field(default_factory=list)


class ScriptedSender:
    """A fake: {url: [Response, ...]} consumed in order; any other URL is a test error (nothing is ever sent)."""

    def __init__(self, script):
        self.script = {u: list(rs) for u, rs in script.items()}
        self.sent = []

    def send(self, url, headers, timeout, max_body_bytes):
        self.sent.append((url, dict(headers)))
        if url not in self.script or not self.script[url]:
            raise AssertionError(f"the fake sender has no scripted response for {url}")
        return self.script[url].pop(0)


class MemoryLedger:
    """The fetch ledger in memory (the store writes ns_fetch_attempts and ns_fetch_events, NS-7)."""

    def __init__(self):
        self.attempts, self.events = [], []
        self._ids = itertools.count(1)

    def open_attempt(self, source_id, purpose, url, host, at, state, redirect_of=None, retry_of=None):
        aid = next(self._ids)
        self.attempts.append({"id": aid, "source_id": source_id, "purpose": purpose, "url": url, "host": host,
                              "at": at, "state": state, "redirect_of": redirect_of, "retry_of": retry_of})
        return aid

    def event(self, attempt_id, kind, at, **detail):
        self.events.append({"attempt_id": attempt_id, "kind": kind, "at": at, **detail})

    def sent_on(self, source_id, day):
        return sum(1 for a in self.attempts if a["source_id"] == source_id and a["state"] == "sent"
                   and a["at"].astimezone(COLOMBO).date() == day)

    def last_end(self, source_id, now=None):
        """The end of the source's previous request; an attempt sent with no outcome yet (in flight, perhaps in
        another process) counts as ending `now` (NS-1 audit)."""
        done = {e["attempt_id"] for e in self.events if e["kind"] in ("completed", "failed")}
        if now is not None and any(a["source_id"] == source_id and a["state"] == "sent" and a["id"] not in done
                                   for a in self.attempts):
            return now
        ends = [e["at"] for e in self.events if e["kind"] in ("completed", "failed")
                and self._source_of(e["attempt_id"]) == source_id]
        return max(ends) if ends else None

    def record_stop(self, source_id, reason, at):
        """A stopped source stays stopped across processes until the owner clears it (NS-1 audit, G-1's rule)."""
        self.stops = getattr(self, "stops", [])
        self.stops.append({"source_id": source_id, "kind": "stopped", "reason": reason, "at": at})

    def owner_clear_stop(self, source_id, note, at):
        """The owner's act (the store requires the owner path)."""
        if not note or not note.strip():
            raise ValueError("clearing a stop states why")
        self.stops = getattr(self, "stops", [])
        self.stops.append({"source_id": source_id, "kind": "cleared", "reason": note, "at": at})

    def stopped(self, source_id):
        """The reason the source is stopped, or None: the latest stop row decides."""
        rows = [r for r in getattr(self, "stops", []) if r["source_id"] == source_id]
        return rows[-1]["reason"] if rows and rows[-1]["kind"] == "stopped" else None

    def _source_of(self, aid):
        return next(a["source_id"] for a in self.attempts if a["id"] == aid)


def header(headers, name):
    """A response header, looked up without regard to case."""
    low = name.lower()
    return next((v for k, v in (headers or {}).items() if str(k).lower() == low), None)


def _retry_after(value, now):
    if value is None:
        return None
    value = str(value).strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


class GovernedTransport:
    def __init__(self, record, sender, ledger, *, clock, sleep, max_redirects=3, timeout_seconds=30):
        self.record, self.sender, self.ledger = record, sender, ledger
        self.clock, self.sleep = clock, sleep
        self.max_redirects, self.timeout = max_redirects, timeout_seconds
        self.consecutive_failures = 0
        self._robots = {}                         # (scheme://host:port, Colombo day) -> RobotFileParser or "deny"
        self._lock = threading.Lock()             # one request at a time (the store adds a per-source advisory lock)

    # ------------------------------------------------------------------------------------------- the public call
    @property
    def stopped(self) -> Optional[str]:
        """Read from the ledger every time: a stop made by any process holds until the owner clears it."""
        return self.ledger.stopped(self.record.source_id)

    def stop(self, reason):
        """Stop the source (an objection, a cessation request, or one of the rules above), recorded."""
        if not self.stopped:
            self.ledger.record_stop(self.record.source_id, reason, self.clock())

    def get(self, url, purpose, *, follow_redirects=True):
        if purpose == "robots":
            raise ValueError("the robots purpose is the transport's own")
        result = Result("failed")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("one request at a time: the transport is busy")
        try:
            self._get(url, purpose, follow_redirects, result, hops=0, redirect_of=None)
        finally:
            self._lock.release()
        return result

    # ------------------------------------------------------------------------------------------- the steps
    def _refuse(self, url, purpose, code, result, redirect_of=None, outcome="refused"):
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        aid = self.ledger.open_attempt(self.record.source_id, purpose, url, host, self.clock(), "refused",
                                       redirect_of=redirect_of)
        self.ledger.event(aid, "refused", self.clock(), code=code)
        result.attempts.append(aid)
        result.outcome, result.code, result.url = outcome, code, url
        return result

    def _get(self, url, purpose, follow, result, hops, redirect_of):
        if self.stopped:
            return self._refuse(url, purpose, f"source_stopped:{self.stopped}", result, redirect_of, "stopped")
        try:
            host = hosts.check(url, self.record.allowed_hosts, self.record.fetch_on_behalf_hosts)
        except hosts.HostRefused as e:
            return self._refuse(url, purpose, e.code, result, redirect_of,
                                "redirect_refused" if redirect_of else "refused")
        if purpose != "robots" and not self._robots_allow(url, result):
            if self.stopped:
                return self._refuse(url, purpose, f"source_stopped:{self.stopped}", result, redirect_of, "stopped")
            return self._refuse(url, purpose, "robots_disallowed", result, redirect_of, "robots_disallowed")
        disc = self.record.discipline
        retry_of = None
        for k in itertools.count():
            day = self.clock().astimezone(COLOMBO).date()
            if self.ledger.sent_on(self.record.source_id, day) >= disc.daily_budget:
                return self._refuse(url, purpose, "daily_budget", result, redirect_of, "budget_exhausted")
            last = self.ledger.last_end(self.record.source_id, self.clock())
            if last is not None:
                wait = (last + timedelta(seconds=disc.min_interval_seconds) - self.clock()).total_seconds()
                if wait > 0:
                    self.sleep(wait)
            aid = self.ledger.open_attempt(self.record.source_id, purpose, url, host, self.clock(), "sent",
                                           redirect_of=redirect_of, retry_of=retry_of)
            result.attempts.append(aid)
            headers = {"User-Agent": self.record.user_agent}
            if self.record.contact:
                headers["From"] = str(self.record.contact)
            try:
                resp = self.sender.send(url, headers, self.timeout, disc.max_body_bytes)
            except Exception:
                self.ledger.event(aid, "failed", self.clock(), error_kind="exception")   # never left open
                raise
            if resp.body is not None and len(resp.body) > disc.max_body_bytes:
                resp = Response(resp.status, resp.headers, None, "too_large")
            now = self.clock()
            if resp.error_kind:
                self.ledger.event(aid, "failed", now, error_kind=resp.error_kind)
            else:
                self.ledger.event(aid, "completed", now, status=resp.status,
                                  size=None if resp.body is None else len(resp.body),
                                  body_sha256=None if resp.body is None else hashlib.sha256(resp.body).hexdigest())
            result.response, result.url = resp, url
            status = resp.status
            # --- classify
            if resp.error_kind == "too_large":
                return self._fail(result, "failed", "too_large")
            if resp.error_kind in ("timeout", "network") or (status is not None and status >= 500):
                if k < disc.max_retries and self._count_failure():
                    self.sleep(min(disc.backoff_base_seconds * 2 ** k, disc.backoff_max_seconds))
                    retry_of = aid
                    continue
                return self._fail(result, "failed", resp.error_kind or f"http_{status}")
            if status in BLOCK_STATUSES:
                self.stop(f"http_{status}")
                return self._fail(result, "stopped", f"http_{status}")
            if status == 429:
                wait = _retry_after(header(resp.headers, "Retry-After"), now)
                if wait is None or wait > disc.retry_after_max_seconds:
                    self.stop("rate_limited")
                    return self._fail(result, "stopped", "rate_limited")
                if k < disc.max_retries and self._count_failure():
                    self.sleep(wait)
                    retry_of = aid
                    continue
                return self._fail(result, "failed", "rate_limited")
            if status is not None and 300 <= status < 400:
                location = header(resp.headers, "Location")
                target = urljoin(url, location) if location else None
                self.ledger.event(aid, "redirect", self.clock(), location=target, followed=bool(follow and target))
                if not (follow and target):
                    result.outcome, result.code = f"http_{status}", "redirect_not_followed"
                    return result
                if hops >= self.max_redirects:
                    return self._refuse(target, purpose, "redirect_limit", result, aid, "redirect_limit")
                return self._get(target, purpose, follow, result, hops + 1, aid)
            if status is not None and 200 <= status < 300:
                self.consecutive_failures = 0
                result.outcome, result.code = "ok", None
                return result
            if not (purpose == "robots" and status in (404, 410)):     # no robots.txt is an answer, not a fault
                self._count_failure()
            result.outcome, result.code = f"http_{status}", None
            return result

    def _count_failure(self):
        """Counts a non-ok outcome; False (and the source stopped) when the circuit breaker opens."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.record.discipline.max_consecutive_failures:
            self.stop("circuit_breaker")
            return False
        return True

    def _fail(self, result, outcome, code):
        if outcome != "stopped" and not self.stopped:
            self._count_failure()
        result.outcome, result.code = ("stopped" if self.stopped else outcome), code
        return result

    def _robots_allow(self, url, result):
        day = self.clock().astimezone(COLOMBO).date()
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{(parts.hostname or '').rstrip('.')}:{parts.port or ''}"
        key = (origin, day)
        if key not in self._robots:
            robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
            r = Result("failed")
            self._get(robots_url, "robots", False, r, hops=0, redirect_of=None)    # never redirected
            result.attempts.extend(r.attempts)
            if r.outcome == "ok":
                p = RobotFileParser()
                p.parse((r.response.body or b"").decode("utf-8", "replace").splitlines())
                self._robots[key] = p
            elif r.outcome in ("http_404", "http_410"):
                p = RobotFileParser()
                p.parse([])                                       # no robots.txt: nothing disallowed
                self._robots[key] = p
            else:
                self._robots[key] = "deny"                        # unreadable robots.txt: conservative
        p = self._robots[key]
        return p != "deny" and p.can_fetch(self.record.user_agent, url)


def fake_clock(start):
    """A clock and a sleep that advances it: (clock, sleep, slept list)."""
    state = {"now": start}
    slept = []

    def clock():
        return state["now"]

    def sleep(seconds):
        slept.append(seconds)
        state["now"] = state["now"] + timedelta(seconds=seconds)
    return clock, sleep, slept


def utcnow():
    return datetime.now(timezone.utc)
