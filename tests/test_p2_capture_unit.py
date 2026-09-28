"""
P2 market capture unit tests: no PostgreSQL, no CSE, no real sleeping (fake clock). The only network use is a local
127.0.0.1 HTTP server that exercises the real requests-based transport. Database behaviour is covered by
test_p2_capture_postgres.py.
"""
import base64
import gzip
import hashlib
import http.server
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from p2_fakes import ABSENT, TRADED, FakeClock, FakeCSE, R, dumps, real_ts_rows, universe_body  # noqa: E402
from worker import cse_client  # noqa: E402
from worker.market_capture import archive, capture, cli, completeness, config as cfgmod, derive  # noqa: E402
from worker.market_capture import http as p2http  # noqa: E402
from worker.ops import migrate as mig, spool  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(REPO, "worker", "market_capture")
EMAIL = "p2-tests@example.org"


# ------------------------------------------------------------------------------------------------ config / G-1 bounds

def test_min_interval_can_never_go_below_one_and_a_half_seconds():
    assert cfgmod.MIN_INTERVAL_FLOOR_SECONDS == 1.5
    assert cfgmod.RequestPolicy().min_interval_seconds == 1.5
    assert cfgmod.RequestPolicy(min_interval_seconds=2.0).min_interval_seconds == 2.0
    for bad in (0.0, 1.0, 1.49):
        with pytest.raises(cfgmod.ConfigError, match="G-1 floor"):
            cfgmod.RequestPolicy(min_interval_seconds=bad)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load({"CSE_CAPTURE_MIN_INTERVAL_SECONDS": "1.0"})


def test_retry_and_budget_knobs_are_bounded():
    for kw in ({"backoff_max_seconds": 601.0}, {"retry_after_max_seconds": 901.0}, {"max_consecutive_failures": 0},
               {"attempts": {"universe": 6, "trade_summary": 3, "absent_fallback": 2, "cross_check": 2,
                             "metadata_sweep": 2}}):
        with pytest.raises(cfgmod.ConfigError):
            cfgmod.RequestPolicy(**kw)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.daily_policy("post_close", max_requests=501)
    p = cfgmod.RequestPolicy()
    assert [p.backoff(i) for i in (1, 2, 3, 4, 5, 6)] == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0]


def test_default_policies_are_the_p05_minimum_request_plan():
    pc, po, sw = cfgmod.daily_policy("post_close"), cfgmod.daily_policy("post_open"), cfgmod.sweep_policy()
    assert (pc.absent_fallback, pc.cross_check_size, pc.derive, pc.max_requests) == (True, 10, True, 150)
    assert (po.absent_fallback, po.cross_check_size, po.derive) == (False, 0, True)
    assert (sw.run_kind, sw.sweep_all, sw.derive, sw.fetch_trade_summary) == ("metadata_sweep", True, False, False)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.daily_policy("post_lunch")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.CapturePolicy(**{**cfgmod.sweep_policy().as_json(), "derive": True,
                                "request": cfgmod.RequestPolicy()})


def test_user_agent_requires_a_contact_email():
    ua = cfgmod.user_agent(EMAIL)
    assert EMAIL in ua and "personal non-commercial" in ua and ua.startswith("cse-analysis-capture/")
    for bad in ("", None, "not-an-email", "a@b", "x y@example.org", "evil@example.org) (x"):
        with pytest.raises(cfgmod.ConfigError, match="contact e-mail"):
            cfgmod.user_agent(bad)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load({}, require_contact=True)
    assert cfgmod.load({"CSE_CAPTURE_CONTACT_EMAIL": EMAIL}, require_contact=True).user_agent == ua


def test_endpoint_semantics_are_exactly_stage_e_cse_client():
    """Read from the frozen cse_client SOURCE: older frozen tests replace its public functions at module level and
    never restore them, so calling them would depend on test order."""
    import ast
    tree = ast.parse(open(os.path.join(REPO, "worker", "cse_client.py"), encoding="utf-8").read())
    stage_e = {}
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in ("get_all_security_codes", "get_trade_summary_all",
                                                           "get_company_info_summary"):
            ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return)).value
            assert isinstance(ret, ast.Call) and isinstance(ret.func, ast.Name)
            data = next((k.value for k in ret.keywords if k.arg == "data"), None)
            stage_e[fn.name] = ({"_get": "GET", "_post": "POST"}[ret.func.id], ret.args[0].value,
                                ast.unparse(data) if data is not None else None)
    assert stage_e == {"get_all_security_codes": ("GET", "allSecurityCode", None),
                       "get_trade_summary_all": ("POST", "tradeSummary", "{}"),
                       "get_company_info_summary": ("POST", "companyInfoSummery", "{'symbol': symbol}")}
    u, t, c = cfgmod.universe_spec(), cfgmod.trade_summary_spec(), cfgmod.company_info_spec("COMB.N0000", "cross_check")
    assert [(s.method, s.endpoint, s.params) for s in (u, t, c)] == [
        ("GET", "allSecurityCode", {}), ("POST", "tradeSummary", {}), ("POST", "companyInfoSummery",
                                                                      {"symbol": "COMB.N0000"})]
    assert all(s.url == f"{cse_client.BASE_URL}/{s.endpoint}" for s in (u, t, c))
    assert cfgmod.BASE_URL == "https://www.cse.lk/api"


# ------------------------------------------------------------------------------------------------ classification

@pytest.mark.parametrize("status,body,error,expected", [
    (200, dumps({"reqTradeSummery": []}), None, "ok"),
    (200, b"   \n", None, "empty_response"),
    (200, b"<html>", None, "invalid_json"),
    (200, dumps({"x": 1}), None, "malformed_response"),
    (403, b"denied", None, "blocked"), (401, b"", None, "blocked"), (407, b"", None, "blocked"),
    (451, b"", None, "blocked"), (429, b"", None, "rate_limited"), (500, b"", None, "server_error"),
    (503, b"", None, "server_error"), (404, b"", None, "http_error"), (302, b"", None, "unexpected_redirect"),
    (None, None, "timeout", "timeout"), (None, None, "network", "network_error"), (200, None, "too_large", "too_large"),
])
def test_classification(status, body, error, expected):
    ex = p2http.Exchange(method="POST", url="u", params={}, request_headers={}, requested_at=datetime.now(timezone.utc),
                         status=status, body=body, error_kind=error)
    assert p2http.classify("tradeSummary", ex)[0] == expected


def test_universe_shape_uses_stage_e_helper():
    ex = lambda b: p2http.Exchange(method="GET", url="u", params={}, request_headers={}, requested_at=None, status=200,
                                   body=b)
    assert p2http.classify("allSecurityCode", ex(dumps(universe_body())))[0] == "ok"
    assert p2http.classify("allSecurityCode", ex(dumps([])))[0] == "malformed_response"      # "discovery failed"
    assert p2http.classify("companyInfoSummery", ex(dumps([1])))[0] == "malformed_response"


def test_sensitive_headers_are_dropped_but_named():
    kept, removed = p2http.sanitize_headers({"Content-Type": "application/json", "Set-Cookie": "sess=1",
                                             "WWW-Authenticate": "Basic", "X-Api-Key": "k", "Date": "d"})
    assert kept == {"content-type": "application/json", "date": "d"}
    assert removed == ["set-cookie", "www-authenticate", "x-api-key"]


def test_retry_after_parsing():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    assert p2http.retry_after_seconds({"retry-after": "30"}, now) == 30.0
    assert p2http.retry_after_seconds({"retry-after": "Mon, 28 Sep 2026 12:01:00 GMT"}, now) == 60.0
    assert p2http.retry_after_seconds({"retry-after": "soon"}, now) is None
    assert p2http.retry_after_seconds({}, now) is None


# ------------------------------------------------------------------------------------------------ requester / throttle

class FakeArchiver:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.n = {}

    def next_attempt_no(self, key):
        return self.n.get(key, 0) + 1

    def intent(self, spec, attempt_no):
        self.events.append(("intent", spec.request_key, attempt_no))
        self.n[spec.request_key] = attempt_no
        return len(self.events)

    def archive(self, seq, spec, attempt_no, ex, outcome, parse_status):
        self.events.append(("archive", spec.request_key, attempt_no, outcome))
        body_sha = hashlib.sha256(ex.body).hexdigest() if ex.body is not None else None
        return SimpleNamespace(request_key=spec.request_key, attempt_no=attempt_no, outcome=outcome,
                               http_status=ex.status, body_sha256=body_sha)


class RecordingCSE(FakeCSE):
    def __init__(self, clock, events, **kw):
        super().__init__(clock, **kw)
        self.events = events

    def send(self, *a, **kw):
        self.events.append(("send",))
        return super().send(*a, **kw)


def requester(script=None, policy=None, max_requests=None, **kw):
    clock = FakeClock()
    events = []
    cse = RecordingCSE(clock, events, script=script, **kw)
    req = p2http.Requester(cse, policy or cfgmod.RequestPolicy(), cfgmod.user_agent(EMAIL), FakeArchiver(events),
                           clock=clock.monotonic, wall=clock.wall, sleep=clock.sleep, max_requests=max_requests)
    return req, cse, clock, events


def test_requests_are_sequential_spaced_and_identified():
    req, cse, clock, events = requester()
    for spec in (cfgmod.universe_spec(), cfgmod.trade_summary_spec(), cfgmod.company_info_spec("COMB.N0000",
                                                                                             "cross_check")):
        assert req.fetch(spec).ok is not None
    starts = [c["started"] for c in cse.calls]
    ends = [c["ended"] for c in cse.calls]
    assert all(s - e >= 1.5 - 1e-9 for e, s in zip(ends, starts[1:]))
    assert cse.max_in_flight == 1
    assert all(c["headers"] == {"User-Agent": cfgmod.user_agent(EMAIL), "Accept": "application/json"}
               for c in cse.calls)
    assert [e[0] for e in events] == ["intent", "send", "archive"] * 3          # durable intent BEFORE each request


def test_no_parallel_requests_even_from_threads():
    req, cse, clock, _ = requester()
    specs = [cfgmod.company_info_spec(s, "cross_check") for s in TRADED]
    threads = [threading.Thread(target=req.fetch, args=(s,)) for s in specs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cse.calls) == 5 and cse.max_in_flight == 1


def test_403_stops_immediately_without_retry():
    req, cse, clock, events = requester(script={"tradeSummary": [R(403, b"no")]})
    with pytest.raises(p2http.Blocked) as ei:
        req.fetch(cfgmod.trade_summary_spec())
    assert len(cse.calls) == 1 and ei.value.state == "blocked" and ei.value.attempt.outcome == "blocked"
    assert events[-1] == ("archive", "tradeSummary", 1, "blocked")             # archived before stopping


def test_429_backoff_and_long_retry_after():
    req, cse, clock, _ = requester(script={"tradeSummary": [R(429, b"", {"Retry-After": "12"})]})
    assert req.fetch(cfgmod.trade_summary_spec()).ok is not None
    assert 12.0 in clock.slept and len(cse.calls) == 2
    req, cse, clock, _ = requester(script={"tradeSummary": [R(429, b"", {"Retry-After": "86400"})]})
    with pytest.raises(p2http.RateLimited):
        req.fetch(cfgmod.trade_summary_spec())
    assert len(cse.calls) == 1 and max(clock.slept, default=0) < 86400
    req, cse, clock, _ = requester(script={"tradeSummary": [R(429, b"")] * 3})
    with pytest.raises(p2http.RateLimited):                                     # persistent 429: stop, never loop
        req.fetch(cfgmod.trade_summary_spec())
    assert len(cse.calls) == 3


def test_server_error_backoff_is_exponential_and_bounded():
    req, cse, clock, _ = requester(script={"tradeSummary": [R(500, b"")] * 3})
    res = req.fetch(cfgmod.trade_summary_spec())
    assert res.ok is None and len(res.attempts) == 3 and len(cse.calls) == 3
    assert [s for s in clock.slept if s >= 5] == [5.0, 10.0]


def test_client_errors_and_redirects_are_not_retried():
    for r in (R(404, b""), R(302, b"", {"Location": "https://elsewhere.example/"}), R(400, b"bad")):
        req, cse, clock, _ = requester(script={"tradeSummary": [r]})
        assert req.fetch(cfgmod.trade_summary_spec()).ok is None and len(cse.calls) == 1


def test_circuit_breaker_stops_a_run_that_cannot_reach_cse():
    script = {f"companyInfoSummery:{s}": [R(error_kind="network")] * 2 for s in TRADED}
    req, cse, clock, _ = requester(script=script)
    with pytest.raises(p2http.CircuitOpen):
        for s in TRADED:
            req.fetch(cfgmod.company_info_spec(s, "cross_check"))
    assert len(cse.calls) == 5                                                  # max_consecutive_failures


def test_budget_is_checked_before_any_intent_or_request():
    req, cse, clock, events = requester(max_requests=2)
    req.fetch(cfgmod.universe_spec())
    req.fetch(cfgmod.trade_summary_spec())
    with pytest.raises(p2http.BudgetExhausted):
        req.fetch(cfgmod.company_info_spec("COMB.N0000", "cross_check"))
    assert len(cse.calls) == 2 and sum(1 for e in events if e[0] == "intent") == 2


def test_throttle_seeded_from_the_previous_process():
    clock = FakeClock()
    t = p2http.Throttle(1.5, clock=clock.monotonic, sleep=clock.sleep)
    t.seed(0.4)                                   # the archive's last request ended 0.4 s ago
    t.before()
    assert clock.slept == [pytest.approx(1.1)]
    t2 = p2http.Throttle(1.5, clock=clock.monotonic, sleep=clock.sleep)
    t2.seed(30.0)
    t2.before()
    assert clock.slept == [pytest.approx(1.1)]    # long ago: no wait


# ------------------------------------------------------------------------------------------------ real transport (local)

class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, status, body, headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = self.rfile.read(n)
        _Handler.seen.append({"path": self.path, "headers": dict(self.headers), "form": form})
        if self.path.endswith("/cookie"):
            return self._send(200, b'{"reqTradeSummery":[]}', [("Set-Cookie", "trk=1; Path=/")])
        if self.path.endswith("/gzip"):
            return self._send(200, gzip.compress(b'{"reqTradeSummery":[1]}'), [("Content-Encoding", "gzip")])
        if self.path.endswith("/redirect"):
            return self._send(302, b"moved", [("Location", "/api/cookie")])
        if self.path.endswith("/big"):
            return self._send(200, b"[" + b"0," * 5000 + b"0]")
        if self.path.endswith("/slow"):
            time.sleep(1.5)
            return self._send(200, b"{}")
        return self._send(200, b'\xef\xbb\xbf{"odd":"\xc3\xa9"}\r\n')


@pytest.fixture(scope="module")
def local_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api"
    srv.shutdown()


def test_transport_ignores_proxy_environment_and_never_sends_cookies(local_server, monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")                          # nothing listens there
    monkeypatch.setenv("NO_PROXY", "")
    t = p2http.RequestsTransport(1 << 20)
    assert t.session.trust_env is False and not t.session.proxies
    hdr = {"User-Agent": cfgmod.user_agent(EMAIL), "Accept": "application/json"}
    _Handler.seen.clear()
    a = t.send("POST", f"{local_server}/cookie", {}, hdr, (2, 2))
    b = t.send("POST", f"{local_server}/cookie", {}, hdr, (2, 2))
    assert a.status == b.status == 200 and a.removed_response_headers == ["set-cookie"]
    assert all("Cookie" not in s["headers"] for s in _Handler.seen) and len(t.session.cookies) == 0
    assert _Handler.seen[0]["headers"]["User-Agent"] == cfgmod.user_agent(EMAIL)
    assert "cookie" not in a.request_headers and a.request_headers["user-agent"] == cfgmod.user_agent(EMAIL)


def test_transport_keeps_exact_bytes_decodes_gzip_and_does_not_follow_redirects(local_server):
    t = p2http.RequestsTransport(1 << 20)
    raw = t.send("POST", f"{local_server}/raw", {"symbol": "COMB.N0000"}, {}, (2, 2))
    assert raw.body == b'\xef\xbb\xbf{"odd":"\xc3\xa9"}\r\n'                   # BOM, UTF-8 and CRLF untouched
    assert _Handler.seen[-1]["form"] == b"symbol=COMB.N0000"                    # form-encoded like Stage E
    gz = t.send("POST", f"{local_server}/gzip", {}, {}, (2, 2))
    assert gz.body == b'{"reqTradeSummery":[1]}' and gz.response_headers["content-encoding"] == "gzip"
    red = t.send("POST", f"{local_server}/redirect", {}, {}, (2, 2))
    assert red.status == 302 and red.body == b"moved" and p2http.classify("tradeSummary", red)[0] == \
        "unexpected_redirect"


def test_transport_size_cap_and_timeout(local_server):
    t = p2http.RequestsTransport(4096)
    big = t.send("POST", f"{local_server}/big", {}, {}, (2, 2))
    assert big.error_kind == "too_large" and big.body is None
    slow = t.send("POST", f"{local_server}/slow", {}, {}, (2, 0.3))
    assert slow.error_kind == "timeout" and slow.body is None


# ------------------------------------------------------------------------------------------------ archive (spool first)

class MemoryStore:
    """In-memory stand-in for PgArchiveStore (the PostgreSQL side is tested against real PostgreSQL)."""

    def __init__(self, fail=None):
        self.rows, self.bodies, self.fail = [], {}, fail

    def max_sequence(self, run_id):
        return max((r["sequence_no"] for r, *_ in self.rows), default=None)

    def attempt_numbers(self, run_id):
        out = {}
        for r, *_ in self.rows:
            out[r["request_key"]] = max(out.get(r["request_key"], 0), r["attempt_no"])
        return out

    def sequence_numbers(self, run_id):
        return [r["sequence_no"] for r, *_ in self.rows]

    def run_exists(self, run_id):
        return True

    def insert_attempt(self, record, record_key, body_b64, recovered=False):
        if self.fail and self.fail(record, recovered):
            raise RuntimeError("database down")
        if body_b64 is not None:
            self.bodies.setdefault(record["body_sha256"], body_b64)
        self.rows.append((record, record_key, recovered))
        return f"row{len(self.rows)}"


def _archiver(tmp_path, store=None):
    run = {"id": "11111111-2222-3333-4444-555555555555", "trading_date": "2026-09-04", "capture_mode": "post_close",
           "user_agent": cfgmod.user_agent(EMAIL)}
    root = tmp_path / "spool"
    root.mkdir(exist_ok=True)
    return archive.Archiver(store or MemoryStore(), str(root), run), str(root)


def _exchange(body, status=200):
    now = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
    return p2http.Exchange(method="POST", url="https://www.cse.lk/api/tradeSummary", params={},
                           request_headers={"user-agent": cfgmod.user_agent(EMAIL), "accept": "application/json"},
                           requested_at=now, observed_at=now, elapsed_ms=5, status=status,
                           response_headers={"content-type": "application/json"}, body=body)


def test_spool_holds_the_exact_bytes_before_the_database_row(tmp_path):
    arc, root = _archiver(tmp_path)
    spec = cfgmod.trade_summary_spec()
    body = b'{"reqTradeSummery": []}\n'
    seq = arc.intent(spec, 1)
    a = arc.archive(seq, spec, 1, _exchange(body), "ok", "json_ok")
    rec, key, recovered = arc.store.rows[0]
    assert spool.read(root, rec["spool_body_key"]) == body and rec["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert base64.b64decode(arc.store.bodies[a.body_sha256]) == body and key == a.spool_record_key
    assert archive.load_spooled(root, key)[0] == rec and not recovered
    assert [e["event"] for e in arc.journal.entries()] == ["intent", "spooled"]


def test_failed_request_is_archived_without_a_body(tmp_path):
    arc, root = _archiver(tmp_path)
    spec = cfgmod.trade_summary_spec()
    ex = p2http.Exchange(method="POST", url="u", params={}, request_headers={}, requested_at=datetime.now(timezone.utc),
                         error_kind="timeout", error="read timed out")
    arc.archive(arc.intent(spec, 1), spec, 1, ex, "timeout", None)
    rec = arc.store.rows[0][0]
    assert (rec["outcome"], rec["body_sha256"], rec["spool_body_key"], rec["http_status"]) == ("timeout", None, None,
                                                                                               None)


def test_spool_failure_means_not_durably_captured(tmp_path, monkeypatch):
    arc, root = _archiver(tmp_path)
    spec = cfgmod.trade_summary_spec()
    seq = arc.intent(spec, 1)
    monkeypatch.setattr(spool, "write_blob", lambda *a: (_ for _ in ()).throw(OSError(28, "No space left")))
    with pytest.raises(archive.SpoolUnavailable, match="NOT durably captured"):
        arc.archive(seq, spec, 1, _exchange(b"{}"), "ok", "json_ok")
    rec = arc.store.rows[0][0]
    assert rec["outcome"] == "spool_failed" and rec["body_sha256"] is None and arc.store.bodies == {}


def test_no_request_without_a_writable_spool(tmp_path):
    arc, root = _archiver(tmp_path)
    arc.journal.path = str(tmp_path / "missing-dir" / "x" / "journal.jsonl")
    arc.journal.dir = os.path.dirname(arc.journal.path)
    open(tmp_path / "missing-dir", "w").close()                                # a FILE where a directory must go
    with pytest.raises(archive.SpoolUnavailable, match="no request was sent"):
        arc.intent(cfgmod.trade_summary_spec(), 1)


def test_database_failure_after_spool_is_recovered_idempotently(tmp_path):
    store = MemoryStore(fail=lambda rec, recovered: not recovered)
    arc, root = _archiver(tmp_path, store)
    spec = cfgmod.trade_summary_spec()
    body = dumps({"reqTradeSummery": real_ts_rows()})
    with pytest.raises(archive.ArchiveDatabaseUnavailable, match="recover"):
        arc.archive(arc.intent(spec, 1), spec, 1, _exchange(body), "ok", "json_ok")
    assert store.rows == []
    store.fail = None
    out = archive.recover(store, root)
    assert out["recovered"] == 1 and store.rows[0][2] is True
    rec = store.rows[0][0]
    assert rec["requested_at"] == "2026-09-04T09:00:00+00:00" and base64.b64decode(store.bodies[rec["body_sha256"]]) \
        == body
    assert archive.recover(store, root) == {**out, "recovered": 0, "already_present": 1}


def test_numbering_skips_numbers_used_by_a_crashed_attempt(tmp_path):
    arc, root = _archiver(tmp_path)
    spec = cfgmod.trade_summary_spec()
    assert arc.intent(spec, arc.next_attempt_no(spec.request_key)) == 1       # ... then the process died
    arc2 = archive.Archiver(arc.store, root, arc.run)
    assert arc2.next_attempt_no(spec.request_key) == 2 and arc2.intent(spec, 2) == 2


def test_identical_bytes_are_one_spool_entry(tmp_path):
    arc, root = _archiver(tmp_path)
    spec = cfgmod.universe_spec()
    body = dumps(universe_body())
    a1 = arc.archive(arc.intent(spec, 1), spec, 1, _exchange(body), "ok", "json_ok")
    a2 = arc.archive(arc.intent(spec, 2), spec, 2, _exchange(body), "ok", "json_ok")
    assert a1.body_sha256 == a2.body_sha256 and len(arc.store.bodies) == 1
    blobs = [f for _, _, fs in os.walk(os.path.join(root, "blobs")) for f in fs]
    assert len(blobs) == 1 and spool.verify(root)[0] == []


# ------------------------------------------------------------------------------------------------ derivation helpers

def test_session_evidence_from_the_real_fixture_rows():
    ts = {"reqTradeSummery": real_ts_rows()}
    ev = derive.session_evidence(ts, date(2026, 9, 4), "post_close")
    assert ev["session_matches_trading_date"] and ev["latest_session_date_colombo"] == "2026-09-04"
    assert ev["all_closing_prices_published"] and ev["warnings"] == []
    assert not derive.session_evidence(ts, date(2026, 9, 5), "post_close")["session_matches_trading_date"]
    empty = derive.session_evidence({"reqTradeSummery": []}, date(2026, 9, 4), "post_close")
    assert not empty["session_matches_trading_date"] and "no rows" in empty["warnings"][0]
    zero = [dict(r, closingPrice=0.0) for r in real_ts_rows()]
    ev0 = derive.session_evidence({"reqTradeSummery": zero}, date(2026, 9, 4), "post_close")
    assert ev0["rows_closing_price_zero"] == 5 and any("closingPrice 0.0" in w for w in ev0["warnings"])
    assert derive.session_evidence({"reqTradeSummery": zero}, date(2026, 9, 4), "post_open")["warnings"] == []


def test_plan_and_cross_check_sample_are_deterministic():
    universe = derive.universe_entries(universe_body(extra=["COMB.N0000"]))
    assert universe[1] == ["COMB.N0000"] and len(universe[0]) == 7              # duplicate recorded, kept once
    ts_by = derive.trade_summary_by_symbol({"reqTradeSummery": real_ts_rows() + [{"symbol": "TSONLY.N0000"}]})[0]
    p = derive.plan(universe[0], ts_by, date(2026, 9, 4), cfgmod.daily_policy("post_close", cross_check_size=3))
    assert p["absent_from_trade_summary"] == ABSENT and p["absent_fallback"] == ABSENT
    assert p["in_trade_summary_not_in_universe"] == ["TSONLY.N0000"]
    assert len(p["cross_check"]) == 3 and set(p["cross_check"]) <= set(TRADED)
    assert p == derive.plan(universe[0], ts_by, date(2026, 9, 4), cfgmod.daily_policy("post_close", cross_check_size=3))
    other = derive.cross_check_sample(TRADED, date(2026, 9, 7), 3)
    samples = {tuple(derive.cross_check_sample(TRADED, date(2026, 9, d), 3)) for d in range(1, 15)}
    assert len(samples) > 1 and other == derive.cross_check_sample(TRADED, date(2026, 9, 7), 3)   # rotates daily
    lim = derive.plan(universe[0], ts_by, date(2026, 9, 4),
                      cfgmod.daily_policy("post_close", absent_fallback_limit=1))
    assert lim["absent_fallback"] == ABSENT[:1] and lim["absent_fallback_skipped_by_limit"] == ABSENT[1:]
    po = derive.plan(universe[0], ts_by, date(2026, 9, 4), cfgmod.daily_policy("post_open"))
    assert po["absent_fallback"] == [] and po["cross_check"] == []


def test_raw_payload_keeps_stage_e_keys_and_only_real_company_info_bodies():
    from worker import mapping
    ts_row = real_ts_rows()[0]
    run = {"id": "r1", "trading_date": date(2026, 9, 4), "capture_mode": "post_close"}
    ts_att = {"id": "a1", "body_sha256": "a" * 64, "http_status": 200}
    ci_none, ts_m = mapping.map_company_info_summary(None), mapping.map_trade_summary_row(ts_row)
    fields = mapping.build_raw_observation(company_info_result=ci_none, trade_summary_result=ts_m,
                                           capture_window="post_close")
    p = derive.build_raw_payload(run=run, ts_row=ts_row, ts_attempt=ts_att, ci_body=None, ci_attempt=None,
                                 ci_purpose=None, ci_mapped=ci_none, ts_mapped=ts_m, raw_fields=fields,
                                 observed_at_source="tradeSummary")
    assert "companyInfoSummery" not in p                                        # F5 link_issuers safety
    assert set(p) >= {"tradeSummary_matched_row", "tradeSummary_call_status_code", "mapping_notes",
                      "cross_source_comparison", "p2"}
    body = {"reqSymbolInfo": {"symbol": "COMB.N0000"}}
    p2 = derive.build_raw_payload(run=run, ts_row=ts_row, ts_attempt=ts_att, ci_body=body,
                                  ci_attempt={"id": "a2", "body_sha256": "b" * 64, "http_status": 200},
                                  ci_purpose="cross_check", ci_mapped=mapping.map_company_info_summary(body),
                                  ts_mapped=ts_m, raw_fields=fields, observed_at_source="tradeSummary")
    assert p2["companyInfoSummery"] == {"status_code": 200, "body": body, "error": None}
    json.dumps(p2)


def test_state_decision_depends_on_a_b_c_and_evidence_only():
    ok_ev = {"session_matches_trading_date": True}
    s = {"run_kind": "market_capture", "A": {"status": "captured"}, "B": {"status": "known"},
         "C": {"status": "complete"}, "session_evidence": ok_ev, "D": {"status": "partial"}}
    assert completeness.decide_state(s) == "succeeded"                      # D (canonicalisation) never matters
    assert completeness.decide_state({**s, "C": {"status": "partial"}}) == "partial"
    assert completeness.decide_state({**s, "B": {"status": "unknown"}}) == "partial"
    assert completeness.decide_state({**s, "A": {"status": "not_captured"}}) == "failed"
    assert completeness.decide_state({**s, "session_evidence": {"session_matches_trading_date": False}}) == "failed"
    assert completeness.decide_state(s, stopped="stopped") == "partial"      # a stopped run is never 'succeeded'
    assert completeness.decide_state(s, stopped="blocked") == "blocked"


# ------------------------------------------------------------------------------------------------ CLI / trading date

def test_trading_date_is_required_and_strict(capsys):
    for argv in (["capture", "--mode", "post_close"], ["sweep"], ["record-missed", "--mode", "post_close",
                                                                   "--reason", "x"]):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(argv)
    for bad in ("2026-9-4", "04/09/2026", "2026-09-31", "today"):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(["capture", "--trading-date", bad, "--mode", "post_close"])
    a = cli.parser().parse_args(["capture", "--trading-date", "2026-09-04", "--mode", "post_close"])
    assert a.trading_date == date(2026, 9, 4)


def test_capture_refused_without_contact_email_before_any_connection(capsys):
    assert cli.main(["capture", "--trading-date", "2026-09-04", "--mode", "post_close"], env={}) == \
        capture.EXIT_REFUSED
    assert "contact e-mail" in capsys.readouterr().err


def test_unreachable_database_is_exit_4_before_any_request(capsys):
    env = {"CSE_DB_HOST": "/nonexistent-socket-dir", "CSE_DB_PORT": "1", "CSE_CAPTURE_CONTACT_EMAIL": EMAIL}
    rc = cli.main(["capture", "--trading-date", "2026-09-04", "--mode", "post_close"], env=env)
    assert rc == capture.EXIT_DATABASE_UNAVAILABLE
    assert "PostgreSQL unavailable" in capsys.readouterr().err


def test_no_purge_or_delete_command_exists():
    choices = cli.parser()._subparsers._group_actions[0].choices
    assert not any(re.search(r"purge|delete|drop|truncate|wipe", c) for c in choices)


def test_colombo_date_guard_uses_a_fixed_offset():
    wall = lambda: datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc)          # 00:30 on the 29th in Colombo
    assert capture.colombo_today(wall) == date(2026, 9, 29)


# ------------------------------------------------------------------------------------------------ static G-1 / freeze

def _package_source():
    out = {}
    for n in sorted(os.listdir(PKG)):
        if n.endswith(".py"):
            with open(os.path.join(PKG, n), encoding="utf-8") as f:
                out[n] = f.read()
    return out


def test_g1_static_guards_in_the_package():
    src = _package_source()
    allsrc = "\n".join(src.values())
    for forbidden in ("ThreadPoolExecutor", "ProcessPoolExecutor", "asyncio", "multiprocessing", "random.",
                      "socks", "rotate", "X-Forwarded-For", "http://", "cdn.cse.lk"):
        assert forbidden not in allsrc, forbidden
    assert "s.trust_env = False" in src["http.py"] and "s.proxies.clear()" in src["http.py"]
    assert "allowed_domains=[]" in src["http.py"] and "allow_redirects=False" in src["http.py"]
    assert re.findall(r"proxies=\{\}", src["http.py"]) == ["proxies={}"]
    low = allsrc.lower()
    assert not re.search(r"\bdelete\s+from\b|\btruncate\s+(table\s+)?[a-z_]|\bupdate\s+market_", low)


def test_migration_0012_is_additive_append_only_and_stores_no_documents():
    path = os.path.join(REPO, "supabase", "migrations", "0012_market_capture_archive.sql")
    sql = open(path, encoding="utf-8").read().lower()
    code = "\n".join(l.split("--")[0] for l in sql.splitlines())
    assert "bytea" not in sql and "cse.lk" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert not re.search(r"\b(alter|drop|delete|truncate)\b", code.replace("before update or delete", "").replace(
        "before truncate", ""))
    tables = re.findall(r"create table (\w+)", code)
    assert tables == ["market_capture_runs", "market_capture_run_events", "market_capture_block_acknowledgements",
                      "market_response_bodies", "market_source_responses", "market_capture_security_results"]
    for t in tables:
        assert re.search(rf"before update or delete on {t}\s+for each row execute function f5_reject_mutation", code), t
        assert re.search(rf"before truncate on {t}\s+for each statement execute function f5_reject_mutation", code), t
    grants = re.findall(r"grant ([a-z, ]+?) on ([a-z_, \n]+?)\s+to (\w+);", code)
    worker = [g for g in grants if g[2] == "cse_worker"]
    assert {g[0] for g in worker} == {"select, insert", "select"}
    assert not any("update" in g[0] or "delete" in g[0] or "truncate" in g[0] for g in grants)
    assert "from public" in code


FROZEN_STAGE_E = {   # SHA-256 (LF-normalised) at the P2 start (HEAD 79b7a5c): P2 wraps these, never edits them
    "cse_client.py": "3186d39955556a29bc47fdc43971e3be50885ee692cd9df211efabbadc8ec33c",
    "mapping.py": "e6691aff9d6be4769888b513b9da583afdb6d208762c4518f1078808235191cf",
    "reconciliation.py": "65b4d36cd22ece42ae5ed19da6de1aec157537616835b9708c34c5d364717326",
    "validation.py": "38b1d509ca83a46d44d650254d2a30efb22736941a47249e284ab08d3ede8e3d",
    "db.py": "62b0f7e6a87e3b1eb274b75d97f3afa4a0c03aaebd643742001f53ddf693beda",
    "capture_single_company.py": "485094ff00c020cb691fdcf18d13f75754d181b456ee7f12e38186a8d42b67a4",
    "capture_multiple_companies.py": "744686623c551d40029a85602ff2f84208edaa1f37840f87362d4e1f552955cd",
    "config.py": "34b2221284a5deb02caecf6c9426f23c9c74fc1a05041f4d2a0419de4aedaffa",
}


def test_frozen_stage_e_modules_unchanged():
    for name, sha in FROZEN_STAGE_E.items():
        assert mig.file_sha256(os.path.join(REPO, "worker", name)) == sha, f"frozen Stage E module {name} changed"


# ------------------------------------------------------------------------------------------------ review fixes

def test_circuit_breaker_counts_non_retryable_http_errors():
    """Five consecutive non-OK attempts of ANY classification stop the run - including the ones never retried."""
    script = {"companyInfoSummery:S1.N0000": [R(500, b""), R(500, b"")],        # retryable: 2 failures
              "companyInfoSummery:S2.N0000": [R(404, b"")],                     # never retried: 3
              "companyInfoSummery:S3.N0000": [R(302, b"", {"Location": "/x"})], # redirect, never retried: 4
              "companyInfoSummery:S4.N0000": [R(400, b"")],                     # never retried: 5 -> stop
              "companyInfoSummery:S5.N0000": [R(200, b"{}")]}
    req, cse, clock, _ = requester(script=script)
    with pytest.raises(p2http.CircuitOpen):
        for s in ("S1.N0000", "S2.N0000", "S3.N0000", "S4.N0000", "S5.N0000"):
            req.fetch(cfgmod.company_info_spec(s, "cross_check"))
    assert cse.keys() == ["companyInfoSummery:S1.N0000"] * 2 + ["companyInfoSummery:S2.N0000",
                                                              "companyInfoSummery:S3.N0000",
                                                              "companyInfoSummery:S4.N0000"]
    only_4xx = {f"companyInfoSummery:X{i}.N0000": [R(404, b"")] for i in range(6)}
    req, cse, clock, _ = requester(script=only_4xx)
    with pytest.raises(p2http.CircuitOpen):
        for i in range(6):
            req.fetch(cfgmod.company_info_spec(f"X{i}.N0000", "cross_check"))
    assert len(cse.calls) == 5


def test_circuit_breaker_resets_on_success():
    script = {f"companyInfoSummery:X{i}.N0000": [R(404, b"")] for i in range(9) if i != 4}
    req, cse, clock, _ = requester(script=script)          # X4 answers OK (default body)
    for i in range(9):
        req.fetch(cfgmod.company_info_spec(f"X{i}.N0000", "cross_check"))
    assert len(cse.calls) == 9 and req.consecutive_failures == 4


def test_block_and_rate_limit_keep_their_meaning_when_they_are_the_fifth_failure():
    base = {f"companyInfoSummery:X{i}.N0000": [R(404, b"")] for i in range(4)}
    req, cse, clock, _ = requester(script={**base, "companyInfoSummery:B.N0000": [R(403, b"")]})
    with pytest.raises(p2http.Blocked) as ei:
        for s in [f"X{i}.N0000" for i in range(4)] + ["B.N0000"]:
            req.fetch(cfgmod.company_info_spec(s, "cross_check"))
    assert type(ei.value) is p2http.Blocked
    req, cse, clock, _ = requester(script={**base, "companyInfoSummery:L.N0000": [R(429, b"")]})
    with pytest.raises(p2http.RateLimited):
        for s in [f"X{i}.N0000" for i in range(4)] + ["L.N0000"]:
            req.fetch(cfgmod.company_info_spec(s, "cross_check"))
    assert len(cse.calls) == 5 and ei.value.state == "blocked"


class _OneRowConn:
    """Minimal stand-in: every query returns the given row (enough for the owner-path role check)."""

    def __init__(self, row):
        self.row, self.executed = row, []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self.executed.append(sql)
        self.description = ("x",)

    def fetchone(self):
        return self.row

    def commit(self):
        pass

    def rollback(self):
        pass


def test_owner_acknowledgement_refuses_every_login_but_the_owner_path():
    from worker.market_capture import runs
    for login in ("cse_worker", "cse_backup", "cse_owner", "postgres"):
        c = _OneRowConn((login,))
        with pytest.raises(runs.RunRefused, match="owner action"):
            runs.acknowledge_block_as_owner(c, "r", "a reviewed block note")
        assert not any("insert" in sql.lower() or "set local role" in sql.lower() for sql in c.executed)
    assert runs.OWNER_PATH_ROLE == "cse_migrator"


def test_migration_0013_makes_acknowledgements_owner_only():
    path = os.path.join(REPO, "supabase", "migrations", "0013_market_capture_owner_acknowledgement.sql")
    sql = open(path, encoding="utf-8").read().lower()
    code = "\n".join(l.split("--")[0] for l in sql.splitlines())
    assert "bytea" not in sql and "cse.lk" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert re.search(r"revoke insert on market_capture_block_acknowledgements from cse_worker;", code)
    assert not re.search(r"\bgrant\b", code) and not re.search(r"\b(alter|drop|delete|truncate|update)\b", code)
    assert "security definer" not in code and "create role" not in code
    guard = code[code.index("create or replace function market_capture_block_ack_guard"):]
    assert "session_user in ('cse_worker', 'cse_backup', 'cse_reader')" in guard
    assert "insufficient_privilege" in guard and "<> 'blocked'" in guard       # the blocked-only rule is kept


def test_committed_migration_0012_is_never_edited():
    """0012 is committed (8fd83ca) and may be applied somewhere: later changes go in new migrations (0013+)."""
    assert mig.file_sha256(os.path.join(REPO, "supabase", "migrations", "0012_market_capture_archive.sql")) == \
        "164c3c8e19019f822f2e18aca251d6da4e2883bf03f1346232f9446ee1ada135"


def test_wrapper_runs_acknowledgement_as_the_owner_path_only():
    text = open(os.path.join(REPO, "ops", "bin", "cse-capture"), encoding="utf-8").read()
    assert 'user=cse-worker; role=cse_worker' in text                       # default: the capture worker
    assert re.search(r"acknowledge-block\)\s+user=cse-migrator; role=cse_migrator", text)
    assert re.search(r"protection\)\s+shift; set -- status \"\$@\"; user=cse-backup; role=cse_backup", text)
    assert '[[ $EUID -eq 0 ]]' in text and "runuser -u \"$user\"" in text   # reached only through sudo
    assert "cse_owner" not in text.replace("acting as cse_owner", "")       # never logs in as the owner
