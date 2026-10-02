"""
Test doubles for the HB-2 governed transport (tests only): a fake clock, a scripted JSON transport, a scripted CDN
session and an in-memory stand-in for the transport's ledger calls. Nothing here opens a socket.
"""
from datetime import date, datetime, timedelta, timezone

from worker.backfill_transport import gates
from worker.market_capture import config as p2config, http as p2http

UTC = timezone.utc
CONTACT = "owner@example.org"
USER_AGENT = p2config.user_agent(CONTACT)
HOST = "backfill-test-host"
BASE_WALL = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)          # a Saturday in Colombo: P3 is never due


class FakeClock:
    def __init__(self, wall=BASE_WALL):
        self.t, self.base, self.sleeps = 1000.0, wall, []

    def clock(self):
        return self.t

    def wall(self):
        return self.base + timedelta(seconds=self.t - 1000.0)

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += max(0.0, s)


class FakeTransport:
    """Scripted P2-style transport: each script entry is a dict (status, body, headers, error_kind)."""

    def __init__(self, clock, script, duration=0.2, on_send=None):
        self.clock, self.script, self.duration, self.on_send = clock, list(script), duration, on_send
        self.calls = []

    def send(self, method, url, params, headers, timeout, clock=None, wall=None):
        if not self.script:
            raise AssertionError("the transport was called more often than scripted")
        if self.on_send:
            self.on_send(len(self.calls))
        step = self.script.pop(0)
        start = self.clock.clock()
        self.calls.append({"method": method, "url": url, "params": dict(params), "headers": dict(headers),
                           "start": start, "timeout": timeout})
        ex = p2http.Exchange(method=method, url=url, params=dict(params), request_headers=dict(headers),
                             requested_at=self.clock.wall())
        self.clock.t += self.duration
        if step.get("error_kind"):
            ex.error_kind, ex.error = step["error_kind"], f"Fake{step['error_kind'].title()}: scripted"
            ex.elapsed_ms = int(self.duration * 1000)
            return ex
        ex.status = step["status"]
        ex.response_headers, ex.removed_response_headers = p2http.sanitize_headers(step.get("headers") or {})
        ex.body = step.get("body")
        ex.observed_at = self.clock.wall()
        ex.elapsed_ms = int(self.duration * 1000)
        self.calls[-1]["end"] = self.clock.clock()
        return ex


class FakeResponse:
    def __init__(self, status, headers, body, fail_after=None):
        self.status_code, self.headers, self._body, self.fail_after = status, dict(headers), body, fail_after
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            if self.fail_after is not None and i >= self.fail_after:
                raise ConnectionError("scripted stream failure")
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """A scripted cdn.cse.lk: {url: (status, headers, body)} or a callable; exceptions are raised."""

    def __init__(self, clock, routes):
        self.clock, self.routes, self.calls = clock, routes, []

    def get(self, url, headers=None, stream=None, allow_redirects=None, timeout=None, proxies=None):
        self.calls.append({"url": url, "headers": dict(headers), "stream": stream, "allow_redirects": allow_redirects,
                           "timeout": timeout, "proxies": proxies, "start": self.clock.clock()})
        self.clock.t += 0.1
        route = self.routes[url]
        if isinstance(route, BaseException):
            raise route
        status, hdrs, body = route[:3]
        return FakeResponse(status, hdrs, body, *(route[3:4] or [None]))


def arming(**over):
    a = {"id": 1, "armed": True, "armed_stages": ["HB-S2", "HB-S3", "HB-S4"], "window_first_date": date(2021, 4, 1),
         "window_last_date": date(2026, 9, 30), "daily_request_budget": 600, "combined_daily_ceiling": 800,
         "slice_max_json_requests": 30, "slice_max_documents": 10, "slice_max_seconds": 600,
         "attempts_per_json_request": 3, "attempts_per_document": 2, "item_max_attempts": 3, "user_agent": USER_AGENT,
         "host": HOST, "version_tuple": gates.running_version_tuple(), "expected_requests": {"feed": 66},
         "stop_conditions": ["any block"], "g1_reference": "G-1 (test)", "note": "test arming decision",
         "os_user": "tester", "approved_by": "cse_migrator", "recorded_at": BASE_WALL - timedelta(days=1)}
    a.update(over)
    return a


class FakeLedger:
    """Replaces the transport's ledger calls in unit tests (installed with monkeypatch)."""

    def __init__(self, arming_row, *, p2_requests=0, p3=None):
        self.arming = arming_row
        self.p2_requests, self.p3 = p2_requests, p3 or gates.P3View()
        self.intents, self.outcomes, self.blocks, self.claims = [], {}, [], {}
        self.fail_outcome = False
        self.after_intent = None

    def install(self, monkeypatch, ledger):
        monkeypatch.setattr(ledger, "arming_in_force", lambda conn: self.arming)
        monkeypatch.setattr(ledger, "p3_view", lambda conn, day: self.p3)
        monkeypatch.setattr(ledger, "budget_view", self.budget_view)
        monkeypatch.setattr(ledger, "record_intent", self.record_intent)
        monkeypatch.setattr(ledger, "record_outcome", self.record_outcome)
        monkeypatch.setattr(ledger, "heartbeat", lambda conn, lease_id, wakeup_id: None)
        monkeypatch.setattr(ledger, "claims_since_requeue", lambda conn, item_id: self.claims.get(item_id, 0))

    def budget_view(self, conn, day, arming_row, p3):
        return gates.BudgetView(phase2_requests=len(self.intents), p2_requests=self.p2_requests,
                                daily_request_budget=int(arming_row["daily_request_budget"]),
                                combined_daily_ceiling=arming_row.get("combined_daily_ceiling"),
                                p3_reserve=gates.p3_reserve(p3, day))

    def record_intent(self, conn, item_id, lease_id, wakeup_id, **fields):
        n = sum(1 for i in self.intents if i["item_id"] == item_id) + 1
        self.intents.append(dict(fields, item_id=item_id, lease_id=lease_id, attempt_no=n))
        if self.after_intent:
            self.after_intent(len(self.intents))
        return len(self.intents), n

    def record_outcome(self, conn, attempt_id, o, body=None, block_reason=None, wakeup_id=None):
        if self.fail_outcome:
            raise RuntimeError("scripted database failure")
        assert attempt_id not in self.outcomes, "an outcome is written once"
        self.outcomes[attempt_id] = dict(o, body=body, block_reason=block_reason)
        if block_reason is not None:
            self.blocks.append(attempt_id)
            return len(self.blocks)
        return None
