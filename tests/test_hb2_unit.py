"""
Phase 2 HB-2 (the governed transport, worker/backfill_transport/) without a database: classification and retry
parity with P2, spacing and seeding, the release guard, every gate (arming, User-Agent, host, version tuple, blocks,
stopped stage, budgets, the P3 quiet window, the clock), the item-claim maximum (A2), the document reservation (A9),
the governed F2 fetcher end to end against a scripted CDN, and the package's static boundaries.

No test contacts any network: every socket connection attempt fails the test.
"""
import dataclasses
import json
import os
import re
import shutil
import socket
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import hb2_fakes as F  # noqa: E402
from worker import document_retrieval as f2, report_discovery as f1  # noqa: E402
from worker.backfill_transport import (DOCUMENT_WORST_CASE_REQUESTS, STAGE_KINDS, classify, fetcher, gates,  # noqa: E402
                                       journal, ledger, preflight, throttle)
from worker.backfill_transport.errors import (Blocked, CircuitOpen, DurabilityStop, GovernanceRefusal,  # noqa: E402
                                              Refused)
from worker.backfill_transport.slice import Runtime, Slice  # noqa: E402
from worker.market_capture import config as p2config, http as p2http  # noqa: E402
from worker.ops import spool  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGE = os.path.join(REPO, "worker", "backfill_transport")
UTC = timezone.utc
COLOMBO = p2config.COLOMBO
FEED_OK = json.dumps({"reqFinancialAnnouncemnets": []}).encode()
LISTING_OK = json.dumps({"reqFinancial": [], "infoAnnualData": [], "infoQuarterlyData": []}).encode()
FEED = (f1.FEED_ENDPOINT, {"fromDate": "2021-04-01", "toDate": "2021-04-30"})
LISTING = (f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to open a network connection fails the test (HB-2 makes no live request)."""
    def refuse(*a, **k):
        raise AssertionError("a network connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def make_slice(tmp_path, monkeypatch, *, arming_row=None, script=(), routes=None, stage="HB-S2", p2_requests=0,
               p3=None, on_send=None):
    clock = F.FakeClock()
    fl = F.FakeLedger(arming_row or F.arming(), p2_requests=p2_requests, p3=p3)
    fl.install(monkeypatch, ledger)
    root = tmp_path / "spool"
    root.mkdir(parents=True, exist_ok=True)
    transport = F.FakeTransport(clock, script, on_send=on_send)
    session = F.FakeSession(clock, routes or {})
    rt = Runtime(wall=clock.wall, clock=clock.clock, sleep=clock.sleep, hostname=lambda: F.HOST,
                 clock_synchronized=lambda: True,
                 env={"CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT, "CSE_BACKUP_ROOT": str(tmp_path)},
                 spool_root=str(root), preflight=lambda c: [], json_transport=transport,
                 session_factory=lambda: session)
    sl = Slice(None, rt, stage, STAGE_KINDS[stage], fl.arming, F.USER_AGENT, rt.policy(), 7, 11, {"stage": stage})
    return sl, fl, clock, transport, session


def ok(body=LISTING_OK, **kw):
    return dict(status=200, body=body, headers={"Content-Type": "application/json"}, **kw)


# ------------------------------------------------------------------------------------------------ classification

def ex(status=None, body=None, error_kind=None, headers=None):
    e = p2http.Exchange(method="POST", url="https://www.cse.lk/api/financials", params={}, request_headers={},
                        requested_at=F.BASE_WALL)
    e.status, e.body, e.error_kind, e.response_headers = status, body, error_kind, headers
    return e


def test_c1_json_classification_follows_p2s_order_and_vocabulary():
    assert classify.RETRYABLE == p2http.RETRYABLE
    assert classify.API_BLOCK_STATUSES == p2http.BLOCK_STATUSES
    cases = [
        (ex(error_kind="timeout"), "timeout"), (ex(error_kind="too_large"), "too_large"),
        (ex(error_kind="network"), "network_error"), (ex(403, b"{}"), "blocked"), (ex(401), "blocked"),
        (ex(407), "blocked"), (ex(451), "blocked"), (ex(429, b"{}"), "rate_limited"), (ex(503, b"x"), "server_error"),
        (ex(302, b""), "unexpected_redirect"), (ex(404, b"{}"), "http_error"), (ex(200, b"  "), "empty_response"),
        (ex(200, b"<html>"), "invalid_json"), (ex(200, b'{"x": []}'), "malformed_response"),
        (ex(200, LISTING_OK), "ok")]
    for e, want in cases:
        assert classify.classify_json(f1.LISTING_ENDPOINT, e)[0] == want, (e.status, e.error_kind, want)
        # every non-shape branch is P2's own classification of the same exchange
        if want not in ("ok", "malformed_response"):
            assert p2http.classify("tradeSummary", e)[0] == want
    assert classify.classify_json(f1.FEED_ENDPOINT, ex(200, FEED_OK))[0] == "ok"
    assert classify.classify_json(f1.FEED_ENDPOINT, ex(200, LISTING_OK))[0] == "malformed_response"
    assert classify.classify_json(f1.LISTING_ENDPOINT, ex(200, FEED_OK))[0] == "malformed_response"


def test_c2_json_shape_is_f1s_own_acceptance_on_the_exact_bytes():
    for endpoint, body in ((f1.FEED_ENDPOINT, FEED_OK), (f1.FEED_ENDPOINT, b'{"reqFinancialAnnouncemnets": {}}'),
                           (f1.LISTING_ENDPOINT, LISTING_OK), (f1.LISTING_ENDPOINT, b'{"infoAnnualData": "x"}'),
                           (f1.LISTING_ENDPOINT, b"[]")):
        r = classify.transport_response(endpoint, {}, ex(200, body), *classify.parse_body(body))
        extract = f1.extract_feed_items if endpoint == f1.FEED_ENDPOINT else f1.extract_listing_buckets
        f1_ok = extract(r)[-2] is None
        assert (classify.classify_json(endpoint, ex(200, body))[0] == "ok") == f1_ok, (endpoint, body)


def test_c3_the_transport_response_has_stage_e_client_fields():
    from worker import cse_client
    assert [f.name for f in dataclasses.fields(classify.TransportResponse)] == \
        [f.name for f in dataclasses.fields(cse_client.CSEResponse)]
    r = classify.transport_response("financials", {"symbol": "X"}, ex(500, b"<html>err"), "not_json", None)
    assert (r.ok, r.status_code, r.body, r.raw_text, r.error) == (False, 500, None, "<html>err",
                                                                  "Response was not valid JSON")
    r = classify.transport_response("financials", {}, ex(error_kind="timeout"))
    assert r.ok is False and r.status_code is None


def test_c4_cdn_classification_never_blocks_a_missing_document():
    assert classify.classify_cdn(403) == "forbidden_or_missing" and classify.outcome_class("forbidden_or_missing") \
        == "terminal"
    assert classify.classify_cdn(404) == "not_found" and classify.outcome_class("not_found") == "terminal"
    for s in (401, 407, 451):
        assert classify.classify_cdn(s) == "blocked" and classify.outcome_class("blocked") == "block"
    assert classify.classify_cdn(429) == "rate_limited" and classify.outcome_class("rate_limited") == "retryable"
    assert classify.outcome_class("rate_limited", block=True) == "block"
    assert classify.classify_cdn(302) == "redirect" and classify.classify_cdn(500) == "server_error"
    assert classify.classify_cdn(200) == "ok" and classify.classify_cdn(200, stream_error=True) == "network_error"
    assert classify.classify_cdn(None, error_kind="timeout") == "timeout"
    assert classify.classify_cdn(None, error_kind="network") == "network_error"
    assert classify.classify_cdn(418) == "http_error"


# ------------------------------------------------------------------------------------------------ retry parity with P2

class _P2Archiver:
    def __init__(self):
        self.n = 0

    def next_attempt_no(self, key):
        return self.n + 1

    def intent(self, spec, attempt_no):
        self.n += 1
        return self.n

    def archive(self, seq, spec, attempt_no, ex_, outcome, parse_status):
        return outcome


SCRIPTS = {
    "two 5xx then ok": [dict(status=503, body=b"x"), dict(status=503, body=b"x"), "OK"],
    "429 within Retry-After then ok": [dict(status=429, body=b"{}", headers={"Retry-After": "7"}), "OK"],
    "429 beyond Retry-After": [dict(status=429, body=b"{}", headers={"Retry-After": "999"})],
    "429 on every attempt": [dict(status=429, body=b"{}")] * 3,
    "a block": [dict(status=403, body=b"{}")],
    "three timeouts": [dict(error_kind="timeout")] * 3,
    "not retryable": [dict(status=404, body=b"{}")],
    "too large": [dict(error_kind="too_large")],
}


def _p2_run(script, ok_body):
    clock = F.FakeClock()
    steps = [dict(status=200, body=ok_body) if s == "OK" else s for s in script]
    t = F.FakeTransport(clock, steps)
    policy = p2config.RequestPolicy()
    r = p2http.Requester(t, policy, F.USER_AGENT, _P2Archiver(), clock=clock.clock, wall=clock.wall,
                         sleep=clock.sleep)
    spec = p2config.RequestSpec("tradeSummary", "trade_summary", "tradeSummary", "POST", {})
    try:
        res = r.fetch(spec, attempts=3)
        return [a for a in res.attempts], "ok" if res.ok else "returned", clock.sleeps
    except p2http.StopRun as exc:
        return [], type(exc).__name__, clock.sleeps


def test_r1_retry_block_and_backoff_decisions_match_p2(tmp_path, monkeypatch):
    names = {"Blocked": "Blocked", "RateLimited": "Blocked", "CircuitOpen": "CircuitOpen"}
    for label, script in SCRIPTS.items():
        p2_attempts, p2_end, p2_sleeps = _p2_run(script, json.dumps([{"symbol": "X"}]).encode())
        steps = [ok() if s == "OK" else s for s in script]
        sl, fl, clock, t, _ = make_slice(tmp_path / label.replace(" ", "_"), monkeypatch, script=steps)
        try:
            res = sl.json_request("item-1", *LISTING)
            end = "ok" if res.ok else "returned"
            outcomes = [a.outcome for a in res.attempts]
        except (Blocked, CircuitOpen) as exc:
            end, outcomes = names[type(exc).__name__.replace("Blocked", "Blocked")], None
        if p2_end in names:
            assert end == names[p2_end], label
        else:
            assert end == p2_end and outcomes == p2_attempts, (label, outcomes, p2_attempts)
        assert clock.sleeps == p2_sleeps, (label, clock.sleeps, p2_sleeps)
        assert len(fl.intents) == len(t.calls) == len(fl.outcomes), label        # every request: intent + outcome


def test_r2_the_circuit_breaker_counts_any_non_ok_attempt_across_items(tmp_path, monkeypatch):
    script = [dict(status=404, body=b"{}")] * 4 + [dict(status=503, body=b"x")]
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=script)
    for i in range(4):
        assert not sl.json_request(f"item-{i}", *LISTING).ok          # terminal, not retried
    with pytest.raises(CircuitOpen):
        sl.json_request("item-9", *LISTING)
    assert sl.stop is not None and len(t.calls) == 5
    with pytest.raises(Refused, match="stopped"):                       # nothing more from this slice
        sl.json_request("item-10", *LISTING)
    # an OK resets the counter
    sl2, _, _, _, _ = make_slice(tmp_path / "b", monkeypatch, script=[dict(status=404, body=b"{}")] * 4 + [ok()] +
                                 [dict(status=404, body=b"{}")] * 4)
    for i in range(9):
        sl2.json_request(f"i{i}", *LISTING)
    assert sl2.stop is None


def test_r3_a_block_is_recorded_with_its_outcome_and_stops_the_slice(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[dict(status=451, body=b"{}")])
    with pytest.raises(Blocked) as ei:
        sl.json_request("item-1", *LISTING)
    o = fl.outcomes[1]
    assert o["outcome_class"] == "block" and o["block_reason"] and ei.value.block_id == 1 and fl.blocks == [1]
    assert sl.stop is ei.value and ei.value.lease_result == "blocked"


def test_r4_the_ok_outcome_carries_its_spooled_body(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok(FEED_OK)])
    res = sl.json_request("item-1", *FEED)
    o = fl.outcomes[1]
    assert res.ok and o["body"] == FEED_OK and o["parse_status"] == "json_ok" and o["outcome_class"] == "ok"
    root = sl.journal.root
    assert spool.read(root, o["spool_body_key"]) == FEED_OK
    rec, body = journal.load_record(root, o["spool_record_key"], 1)
    assert body == FEED_OK and rec["outcome"] == "ok" and rec["item_id"] == "item-1"
    events = [e["event"] for e in sl.journal.entries()]
    assert events == ["intent", "spooled"]
    assert res.response.ok and res.response.body == {"reqFinancialAnnouncemnets": []}
    assert t.calls[0]["headers"] == {"User-Agent": F.USER_AGENT, "Accept": "application/json"}
    assert fl.intents[0]["headers"] == {"user-agent": F.USER_AGENT, "accept": "application/json"}
    assert t.calls[0]["method"] == "POST" and t.calls[0]["params"] == FEED[1]
    assert t.calls[0]["url"] == "https://www.cse.lk/api/getFinancialAnnouncement"


def test_r5_the_intent_and_journal_precede_the_request(tmp_path, monkeypatch):
    seen = []
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()],
                                     on_send=lambda n: seen.append((len(fl.intents), len(fl.outcomes),
                                                                    [e["event"] for e in sl.journal.entries()])))
    sl.json_request("item-1", *LISTING)
    assert seen == [(1, 0, ["intent"])]


def test_r6_spool_or_database_failures_stop_the_slice(tmp_path, monkeypatch):
    # the journal cannot be written: the intent is closed 'spool_failed' and NOTHING is sent
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()])
    real_intent = journal.intent
    monkeypatch.setattr(journal, "intent", lambda *a: (_ for _ in ()).throw(journal.SpoolUnavailable("disk full")))
    with pytest.raises(DurabilityStop):
        sl.json_request("item-1", *LISTING)
    assert t.calls == [] and fl.outcomes[1]["outcome"] == "spool_failed"
    monkeypatch.setattr(journal, "intent", real_intent)
    # the outcome cannot be committed: the slice stops, the spool copy stands for recovery
    sl, fl, clock, t, _ = make_slice(tmp_path / "b", monkeypatch, script=[ok()])
    fl.fail_outcome = True
    with pytest.raises(DurabilityStop):
        sl.json_request("item-1", *LISTING)
    assert list(sl.journal.spooled()) == [1] and sl.stop.lease_result == "error"


def test_r7_requests_are_validated_before_any_intent(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()])
    for endpoint, params in (("tradeSummary", {}), (f1.LISTING_ENDPOINT, {"symbol": "X", "extra": "y"}),
                             (f1.FEED_ENDPOINT, {"fromDate": "2019-01-01", "toDate": "2019-01-31"}),
                             (f1.FEED_ENDPOINT, {"fromDate": "2026-09-01", "toDate": "2026-10-31"})):
        with pytest.raises(Refused):
            sl.json_request("item-1", endpoint, params)
    assert fl.intents == [] and t.calls == []


# ------------------------------------------------------------------------------------------------ spacing and seeding

def test_t1_requests_are_at_least_the_minimum_interval_apart(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()] * 5)
    for i in range(5):
        sl.json_request(f"item-{i}", *LISTING)
    gaps = [t.calls[i + 1]["start"] - t.calls[i]["end"] for i in range(4)]
    assert all(g >= 1.5 - 1e-9 for g in gaps), gaps
    assert sl.policy.min_interval_seconds == 1.5


def test_t2_seeding_a_dead_predecessor_and_the_release_guard():
    clock = F.FakeClock()
    th = throttle.SliceThrottle(1.5, clock=clock.clock, sleep=clock.sleep)
    th.seed(0.0)                                   # a dead predecessor: its request counts as ending now
    th.before()
    assert clock.sleeps == [1.5]
    th.after()
    th.release_guard()                             # the lock is released only a full interval after the last request
    assert clock.sleeps == [1.5, 1.5]
    clock2 = F.FakeClock()
    th2 = throttle.SliceThrottle(1.5, clock=clock2.clock, sleep=clock2.sleep)
    th2.seed(10.0)                                 # long ago: no wait
    th2.before()
    assert clock2.sleeps == []
    th2.hold(7.0)                                  # a Retry-After within its bound holds the next request
    th2.before()
    assert clock2.sleeps == [7.0]


def test_t3_seed_seconds_takes_the_later_of_both_archives(monkeypatch):
    from worker.market_capture import runs as p2runs
    monkeypatch.setattr(p2runs, "seconds_since_last_request", lambda conn, now: 40.0)
    monkeypatch.setattr(ledger, "seconds_since_last_ledger_request", lambda conn: 0.3)
    assert throttle.seed_seconds(None, F.BASE_WALL) == 0.3
    monkeypatch.setattr(ledger, "seconds_since_last_ledger_request", lambda conn: None)
    assert throttle.seed_seconds(None, F.BASE_WALL) == 40.0
    monkeypatch.setattr(p2runs, "seconds_since_last_request", lambda conn, now: None)
    assert throttle.seed_seconds(None, F.BASE_WALL) is None


# ------------------------------------------------------------------------------------------------ budgets and bounds

def test_b1_daily_and_combined_budgets_with_the_p3_reservation():
    b = gates.BudgetView(phase2_requests=599, p2_requests=0, daily_request_budget=600, combined_daily_ceiling=800)
    assert b.covers(1) and not b.covers(2)
    b = gates.BudgetView(phase2_requests=100, p2_requests=600, daily_request_budget=600, combined_daily_ceiling=800)
    assert b.combined_remaining == 100 and b.covers(100) and not b.covers(101)
    b = gates.BudgetView(phase2_requests=100, p2_requests=500, daily_request_budget=600, combined_daily_ceiling=800,
                         p3_reserve=150)
    assert b.combined_remaining == 50
    assert gates.BudgetView(0, 10_000, 600, None).covers(600)              # no ceiling armed: daily budget only
    friday = date(2026, 10, 2)
    p3 = gates.P3View(armed=True, earliest_start_local=dtime(15, 15), window_close_local=dtime(23, 59, 59),
                      daily_request_budget=150)
    assert gates.p3_reserve(p3, friday) == 150
    assert gates.p3_reserve(p3, date(2026, 10, 3)) == 0                    # Saturday
    assert gates.p3_reserve(dataclasses.replace(p3, closed_today=True), friday) == 0
    assert gates.p3_reserve(dataclasses.replace(p3, item_final=True, item_state="succeeded"), friday) == 0
    assert gates.p3_reserve(dataclasses.replace(p3, armed=False), friday) == 0


def test_b2_slice_request_and_time_bounds(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()] * 3,
                                     arming_row=F.arming(slice_max_json_requests=2))
    sl.json_request("a", *LISTING)
    sl.json_request("b", *LISTING)
    with pytest.raises(Refused, match="slice_requests"):
        sl.json_request("c", *LISTING)
    # A8: past the slice bound no new work starts, but a retry of the request in progress still may
    sl, fl, clock, t, _ = make_slice(tmp_path / "b", monkeypatch, script=[dict(status=503, body=b"x"), ok()],
                                     arming_row=F.arming(slice_max_seconds=5))
    clock.t += 4.0
    res = sl.json_request("a", *LISTING)                                    # backoff of 5 s crosses the bound
    assert res.ok and len(t.calls) == 2 and sl.elapsed() > 5
    with pytest.raises(Refused, match="slice_time"):
        sl.json_request("b", *LISTING)


def test_b3_the_daily_budget_is_rechecked_before_every_request(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[dict(status=503, body=b"x")] * 3,
                                     arming_row=F.arming(daily_request_budget=2))
    res_or_exc = None
    with pytest.raises(Refused, match="budget"):
        res_or_exc = sl.json_request("a", *LISTING)
    assert res_or_exc is None and len(t.calls) == 2


def test_b4_item_claims_not_http_attempts_bound_an_item(tmp_path, monkeypatch):
    """A2: item_max_attempts counts CLAIMS since the last re-queue; HTTP retries are bounded separately, per slice."""
    assert gates.claim_refusals(2, 3) == [] and gates.claim_refusals(3, 3) and gates.claim_refusals(4, 3)
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[dict(status=503, body=b"x")] * 3 + [ok()])
    fl.claims["item-1"] = 3
    monkeypatch.setattr(sl, "lease_id", 11)
    from worker.financial_backfill import store as hb1_store
    claimed = []
    monkeypatch.setattr(hb1_store, "claim", lambda *a, **k: claimed.append(a))
    with pytest.raises(Refused, match="item_max"):
        sl.claim("item-1")
    fl.claims["item-1"] = 2
    sl.claim("item-1")
    assert len(claimed) == 1
    res = sl.json_request("item-1", *LISTING)                  # three HTTP attempts within ONE claim
    assert not res.ok and len(res.attempts) == 3
    with pytest.raises(Refused, match="json_attempts"):        # the per-slice attempt limit is spent for the item
        sl.json_request("item-1", *LISTING)


def test_b5_a_document_starts_only_if_budgets_cover_six_requests(tmp_path, monkeypatch):
    assert DOCUMENT_WORST_CASE_REQUESTS == 2 * (f2.MAX_REDIRECTS + 1)
    sl, fl, clock, _, _ = make_slice(tmp_path, monkeypatch, stage="HB-S4",
                                     arming_row=F.arming(daily_request_budget=5))
    with pytest.raises(Refused, match="budget"):
        sl.document_fetcher("doc-1")
    sl, fl, clock, _, _ = make_slice(tmp_path / "b", monkeypatch, stage="HB-S4",
                                     arming_row=F.arming(daily_request_budget=6, slice_max_documents=1))
    sl.document_fetcher("doc-1")
    with pytest.raises(Refused, match="slice_documents"):
        sl.document_fetcher("doc-2")
    sl, fl, clock, _, _ = make_slice(tmp_path / "c", monkeypatch, stage="HB-S4")
    first, second = sl.document_fetcher("doc-1"), sl.document_fetcher("doc-1")
    assert (first.pass_no, first.last_pass, second.pass_no, second.last_pass) == (1, False, 2, True)
    with pytest.raises(Refused, match="document_attempts"):          # attempts_per_document = 2 F2 passes
        sl.document_fetcher("doc-1")
    with pytest.raises(Refused, match="kind"):
        make_slice(tmp_path / "d", monkeypatch)[0].document_fetcher("doc-1")


# ------------------------------------------------------------------------------------------------ gates

def _start(**over):
    base = dict(now=F.BASE_WALL, stage="HB-S2", kind="json", arming=F.arming(), contact_problem=None,
                runtime_user_agent=F.USER_AGENT, hostname=F.HOST, running_versions=gates.running_version_tuple(),
                preflight_problems=(), phase2_blocks=(), p2_blocks=(), stage_stopped=False,
                budget=gates.BudgetView(0, 0, 600, 800), p3=gates.P3View(), clock_synchronized=True,
                last_runner_time=None)
    base.update(over)
    return gates.StartSnapshot(**base)


def codes(refusals):
    return sorted({c for c, _ in refusals})


def test_g1_the_arming_matrix():
    assert gates.start_refusals(_start()) == []
    assert codes(gates.start_refusals(_start(arming=None))) == ["disarmed"]
    assert codes(gates.start_refusals(_start(arming=F.arming(armed=False, armed_stages=[])))) == ["disarmed"]
    assert codes(gates.start_refusals(_start(arming=F.arming(armed_stages=["HB-S4"])))) == ["stage"]
    assert codes(gates.start_refusals(_start(kind="document"))) == ["stage"]          # HB-S2 is a JSON stage
    assert codes(gates.start_refusals(_start(runtime_user_agent=None, contact_problem="no contact"))) == ["contact"]
    other = p2config.user_agent("someone.else@example.org")
    assert codes(gates.start_refusals(_start(runtime_user_agent=other))) == ["user_agent"]
    assert codes(gates.start_refusals(_start(hostname="another-host"))) == ["host"]
    changed = dict(gates.running_version_tuple(), **{"worker.financial_concepts.MAPPER_VERSION": "f5.map.2"})
    assert codes(gates.start_refusals(_start(running_versions=changed))) == ["versions"]
    assert codes(gates.start_refusals(_start(preflight_problems=("x",)))) == ["preflight"]
    assert codes(gates.start_refusals(_start(phase2_blocks=(1,)))) == ["blocked"]
    assert codes(gates.start_refusals(_start(unblocked_block_attempts=(9,)))) == ["blocked"]
    assert codes(gates.start_refusals(_start(p2_blocks=("r",)))) == ["blocked"]
    assert codes(gates.start_refusals(_start(stage_stopped=True))) == ["stage_stopped"]
    assert codes(gates.start_refusals(_start(budget=gates.BudgetView(600, 0, 600, 800)))) == ["budget"]
    assert codes(gates.start_refusals(_start(kind="document", stage="HB-S4",
                                             budget=gates.BudgetView(595, 0, 600, 800)))) == ["budget"]


def test_g2_the_owners_user_agent_is_p2s_exact_value():
    ua, problem = gates.runtime_user_agent(F.CONTACT)
    assert problem is None and ua == p2config.user_agent(F.CONTACT)
    assert ua.startswith("cse-analysis-capture/p2.capture.1 (personal non-commercial research; contact: ")
    assert gates.runtime_user_agent(None)[0] is None and gates.runtime_user_agent("not-an-email")[1]


def test_g3_the_clock_rules_are_p3s():
    from worker.scheduler import wakeup as p3wakeup
    from worker.backfill_transport import CLOCK_BACKWARDS_TOLERANCE_SECONDS
    assert timedelta(seconds=CLOCK_BACKWARDS_TOLERANCE_SECONDS) == p3wakeup.CLOCK_BACKWARDS_TOLERANCE
    assert codes(gates.start_refusals(_start(clock_synchronized=None))) == ["clock"]
    assert codes(gates.start_refusals(_start(clock_synchronized=False))) == ["clock"]
    assert gates.start_refusals(_start(last_runner_time=F.BASE_WALL + timedelta(minutes=4))) == []
    assert codes(gates.start_refusals(_start(last_runner_time=F.BASE_WALL + timedelta(minutes=6)))) == ["clock"]


def test_g4_timedatectl_parity_with_p3(monkeypatch):
    import subprocess
    from worker.backfill_transport import slice as tslice
    from worker.scheduler import wakeup as p3wakeup
    for out in ("yes\n", "no\n", "", "maybe"):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out))
        assert tslice.timedatectl_synchronized() == p3wakeup.timedatectl_synchronized()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no timedatectl")))
    assert tslice.timedatectl_synchronized() is None is p3wakeup.timedatectl_synchronized()


def colombo(day, hh, mm, ss=0):
    return datetime.combine(day, dtime(hh, mm, ss), tzinfo=COLOMBO).astimezone(UTC)


def test_q1_the_quiet_window(monkeypatch):
    """A6: P3 armed, a weekday not declared closed, now >= due - (slice_max + 60 s), now < window close, today's item
    absent or not final."""
    fri = date(2026, 10, 2)
    p3 = gates.P3View(armed=True, earliest_start_local=dtime(15, 15), window_close_local=dtime(23, 59, 59),
                      daily_request_budget=150)
    q = gates.quiet_window
    opens = colombo(fri, 15, 15) - timedelta(seconds=660)
    assert not q(opens - timedelta(seconds=1), p3, 600) and q(opens, p3, 600)
    assert q(colombo(fri, 20, 0), p3, 600) and q(colombo(fri, 23, 59, 58), p3, 600)
    assert not q(colombo(fri, 23, 59, 59), p3, 600)                       # the window has closed
    assert not q(colombo(date(2026, 10, 3), 16, 0), p3, 600)              # Saturday
    assert not q(colombo(date(2026, 10, 4), 16, 0), p3, 600)              # Sunday
    assert not q(colombo(fri, 16, 0), dataclasses.replace(p3, closed_today=True), 600)
    assert not q(colombo(fri, 16, 0), dataclasses.replace(p3, armed=False), 600)
    assert q(colombo(fri, 16, 0), dataclasses.replace(p3, item_state="partial"), 600)       # not final
    assert not q(colombo(fri, 16, 0), dataclasses.replace(p3, item_state="succeeded", item_final=True), 600)
    assert q(colombo(fri, 15, 5), p3, 600) and not q(colombo(fri, 15, 5), p3, 300)       # the armed slice bound
    assert codes(gates.start_refusals(_start(now=colombo(fri, 16, 0), p3=p3))) == ["quiet_window"]


def test_q2_p3_final_states_and_settings_rule_mirror_p3():
    from worker.scheduler import store as p3store
    assert ledger.P3_FINAL_STATES == p3store.FINAL_STATES
    assert "order by id desc limit 1" in p3store.CURRENT_SETTINGS_SQL


def test_s1_stage_stopped_needs_three_consecutive_circuit_open_slices():
    assert gates.stage_stopped(["circuit_open"] * 3, 3)
    assert not gates.stage_stopped(["circuit_open", "circuit_open"], 3)
    assert not gates.stage_stopped(["circuit_open", "completed", "circuit_open", "circuit_open"], 3)
    assert gates.stage_stopped(["circuit_open"] * 3 + ["completed"], 3)


def test_s2_disarm_or_a_new_arming_between_requests_stops_the_next_one(tmp_path, monkeypatch):
    sl, fl, clock, t, _ = make_slice(tmp_path, monkeypatch, script=[ok()] * 2)
    sl.json_request("a", *LISTING)
    fl.arming = F.arming(id=2, armed=False, armed_stages=[])                # the owner disarmed
    with pytest.raises(Refused, match="disarmed"):
        sl.json_request("b", *LISTING)
    assert sl.stop.codes == ["disarmed"] and sl.stop.lease_result == "refused"
    with pytest.raises(Refused, match="stopped"):                          # the slice is over
        sl.json_request("c", *LISTING)
    assert len(t.calls) == 1
    sl, fl, clock, t, _ = make_slice(tmp_path / "b", monkeypatch, script=[ok()] * 2)
    sl.json_request("a", *LISTING)
    fl.arming = F.arming(id=3)                                             # re-armed: this slice stops anyway
    with pytest.raises(Refused, match="arming_changed"):
        sl.json_request("b", *LISTING)
    assert len(t.calls) == 1


# ------------------------------------------------------------------------------------------------ the governed fetcher

PATH = "upload_report_file/771_1653995188923.pdf"
DIRECT = f2.CDN_BASE + PATH
CMT = f2.CDN_BASE + "cmt/" + PATH
PDF = b"%PDF-1.7\n" + b"0" * 200_000 + b"\n%%EOF\n"


def pdf_headers(body=PDF):
    import hashlib
    return {"Content-Type": "application/pdf", "Content-Length": str(len(body)),
            "ETag": '"' + hashlib.md5(body).hexdigest() + '"'}


def _filing():
    return {"cse_filing_id": 771, "path": PATH, "path2": None}


def test_f1_the_fetcher_sends_f2s_headers_through_a_hardened_session(tmp_path, monkeypatch):
    captured = {}

    class _Req:
        @staticmethod
        def get(url, headers=None, **kw):
            captured.update(headers)
            raise RuntimeError("stop")
    rf = f2.RequestsFetcher()
    rf._requests = _Req
    with pytest.raises(RuntimeError):
        rf.fetch(DIRECT)
    ours = fetcher.f2_headers(F.USER_AGENT)
    assert {k: v for k, v in ours.items() if k != "User-Agent"} == \
        {k: v for k, v in captured.items() if k != "User-Agent"}
    assert set(ours) == set(captured) and ours["User-Agent"] == F.USER_AGENT
    s = p2http.RequestsTransport(1024).session                     # the default session: P2's hardening
    assert s.trust_env is False and not s.proxies


def test_f2_every_fetch_is_governed_and_recorded_and_the_document_is_deleted(tmp_path, monkeypatch):
    routes = {DIRECT: (403, {"Content-Type": "application/xml"}, b"<Error><Code>AccessDenied</Code></Error>"),
              CMT: (200, pdf_headers(), PDF)}
    sl, fl, clock, _, session = make_slice(tmp_path, monkeypatch, stage="HB-S4", routes=routes)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(temp_root))
    seen = {}

    def consumer(doc):
        seen["exists"] = os.path.exists(doc.path)
    fe = sl.document_fetcher("doc-1")
    rec = f2.process_filing(_filing(), consumer, fetcher=fe, temp_root=str(temp_root))
    assert rec.outcome == "succeeded" and rec.cleanup_status == "deleted" and seen["exists"]
    assert rec.strategy == "legacy_cmt_prefix" and len(rec.attempts) == 2
    assert os.listdir(temp_root) == []                                   # F2 deleted it; nothing else was written
    assert [c["url"] for c in session.calls] == [DIRECT, CMT]
    assert len(fl.intents) == 2 and fe.attempt_ids == [1, 2]             # one intent per fetch() call
    assert [fl.outcomes[i]["outcome"] for i in (1, 2)] == ["forbidden_or_missing", "ok"]
    assert [fl.outcomes[i]["outcome_class"] for i in (1, 2)] == ["terminal", "ok"]
    assert fl.outcomes[2]["response_bytes"] == len(PDF) and fl.outcomes[2]["body"] is None
    assert fl.outcomes[2]["details"]["stream_complete"] is True
    assert all(c["allow_redirects"] is False and c["stream"] is True and c["proxies"] == {} for c in session.calls)
    assert session.calls[1]["start"] - session.calls[0]["start"] >= 1.5 - 1e-9   # paced like any CSE request
    assert fl.intents[0]["request_class"] == "document" and fl.intents[0]["endpoint"] == "cdn"
    assert not os.listdir(sl.journal.root)                               # documents never enter the spool


def test_f3_redirects_are_separate_governed_requests_and_never_leave_the_cdn(tmp_path, monkeypatch):
    moved = f2.CDN_BASE + "cmt/moved.pdf"
    routes = {f2.CDN_BASE + "cmt/a.pdf": (301, {"Location": moved}, b""), moved: (200, pdf_headers(), PDF)}
    sl, fl, clock, _, session = make_slice(tmp_path, monkeypatch, stage="HB-S4", routes=routes)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    fe = sl.document_fetcher("doc-1")
    rec = f2.process_filing({"cse_filing_id": 1, "path": "cmt/a.pdf"}, None, fetcher=fe,
                            temp_root=str(tmp_path / "tmp"))
    assert rec.outcome == "succeeded" and len(fl.intents) == 2
    assert [fl.outcomes[i]["outcome"] for i in (1, 2)] == ["redirect", "ok"]
    with pytest.raises(GovernanceRefusal):
        fe.fetch("https://evil.example/x.pdf")
    assert fe.refused and len(fl.intents) == 2


def test_f4_governance_refusal_inside_a_document_is_exposed(tmp_path, monkeypatch):
    routes = {DIRECT: (403, {}, b""), CMT: (200, pdf_headers(), PDF)}
    sl, fl, clock, _, session = make_slice(tmp_path, monkeypatch, stage="HB-S4", routes=routes)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    fe = sl.document_fetcher("doc-1")
    fl.after_intent = lambda n: setattr(fl, "arming", F.arming(id=9, armed=False, armed_stages=[]))
    rec = f2.process_filing(_filing(), None, fetcher=fe, temp_root=str(tmp_path / "tmp"))
    assert rec.outcome == "download_failed" and rec.failure_category == "network_error"      # F2's own view
    assert fe.refused and fe.refused[0][0] == "disarmed"                                       # the real reason
    assert len(fl.intents) == 1 and [c["url"] for c in session.calls] == [DIRECT]             # no fallback sent


def test_f5_cdn_blocks_rate_limits_failures_and_the_breaker(tmp_path, monkeypatch):
    routes = {DIRECT: (451, {}, b"")}
    sl, fl, clock, _, _ = make_slice(tmp_path, monkeypatch, stage="HB-S4", routes=routes)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    f2.process_filing(_filing(), None, fetcher=sl.document_fetcher("d"), temp_root=str(tmp_path / "tmp"))
    assert fl.outcomes[1]["outcome_class"] == "block" and fl.blocks == [1] and isinstance(sl.stop, Blocked)
    # 429 within Retry-After on a first pass: retryable and the next request waits; on the last pass: a block
    routes = {DIRECT: (429, {"Retry-After": "9"}, b""), CMT: (200, pdf_headers(), PDF)}
    sl, fl, clock, _, _ = make_slice(tmp_path / "b", monkeypatch, stage="HB-S4", routes=routes)
    fe = sl.document_fetcher("d")
    f2.process_filing(_filing(), None, fetcher=fe, temp_root=str(tmp_path / "tmp"))
    assert fl.outcomes[1]["outcome_class"] == "retryable" and sl.stop is None
    rec = f2.process_filing(_filing(), None, fetcher=sl.document_fetcher("d"), temp_root=str(tmp_path / "tmp"))
    assert sum(clock.sleeps) >= 9.0 and fl.outcomes[2]["outcome_class"] == "block" and isinstance(sl.stop, Blocked)
    assert rec.outcome == "download_failed"
    # a timeout is recorded at once and F2 still sees a timeout; five non-OK fetches open the circuit
    import requests
    routes = {f2.CDN_BASE + f"cmt/{i}.pdf": (404, {}, b"") for i in range(4)}
    routes[f2.CDN_BASE + "cmt/t.pdf"] = requests.exceptions.ConnectTimeout("scripted")
    sl, fl, clock, _, _ = make_slice(tmp_path / "c", monkeypatch, stage="HB-S4", routes=routes,
                                     arming_row=F.arming(slice_max_documents=10))
    for i in range(4):
        r = f2.process_filing({"cse_filing_id": i + 1, "path": f"cmt/{i}.pdf"}, None,
                              fetcher=sl.document_fetcher(f"d{i}"), temp_root=str(tmp_path / "tmp"))
        assert r.failure_category == "not_found"
    r = f2.process_filing({"cse_filing_id": 9, "path": "cmt/t.pdf"}, None, fetcher=sl.document_fetcher("d9"),
                          temp_root=str(tmp_path / "tmp"))
    assert r.failure_category == "timeout" and fl.outcomes[5]["outcome"] == "timeout"
    assert isinstance(sl.stop, CircuitOpen) and sl.stop.lease_result == "circuit_open"


def test_f6_a_broken_stream_is_recorded_and_f2_still_cleans_up(tmp_path, monkeypatch):
    routes = {f2.CDN_BASE + "cmt/b.pdf": (200, pdf_headers(), PDF, 65536)}
    sl, fl, clock, _, _ = make_slice(tmp_path, monkeypatch, stage="HB-S4", routes=routes)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    rec = f2.process_filing({"cse_filing_id": 2, "path": "cmt/b.pdf"}, None, fetcher=sl.document_fetcher("d"),
                            temp_root=str(tmp_path / "tmp"))
    assert rec.outcome == "download_failed" and rec.cleanup_status == "deleted"
    o = fl.outcomes[1]
    assert o["outcome"] == "network_error" and o["details"]["stream_complete"] is False and o["error"]
    assert os.listdir(tmp_path / "tmp") == []


# ------------------------------------------------------------------------------------------------ static boundaries

def test_x1_the_package_boundary(tmp_path):
    files = sorted(f for f in os.listdir(PACKAGE) if f.endswith(".py"))
    assert files == ["__init__.py", "classify.py", "errors.py", "fetcher.py", "gates.py", "journal.py", "ledger.py",
                     "preflight.py", "recovery.py", "requester.py", "slice.py", "throttle.py"]
    assert preflight.static_problems() == [] and preflight.pin_problems() == [] and preflight.compat_problems() == []
    srcs = {f: open(os.path.join(PACKAGE, f), encoding="utf-8").read() for f in files}
    for f, src in srcs.items():
        assert "rdv_" not in src, f
        assert not re.findall(r"4_?346_?836_?117_?002_?31\d", src), f        # P2's key by name only
        assert "security definer" not in src.lower() and "row level security" not in src.lower(), f
        assert not re.search(r"\b(create|alter|drop)\s+(table|role|policy|function|trigger)\b|\bgrant\s",
                             src, re.I), f
        if f != "fetcher.py":
            assert "import requests" not in src and "urllib" not in src, f
    writes = set(re.findall(r"(?:insert into|update)\s+(\w+)", "\n".join(srcs.values())))
    assert writes and all(t.startswith("backfill_") for t in writes), writes   # frozen tables keep their writers
    assert "backfill_arming_decisions" not in writes and "backfill_block_acknowledgements" not in writes
    assert "backfill_hold_resolutions" not in writes                            # owner decisions stay owner-only
    assert not os.path.exists(os.path.join(PACKAGE, "__main__.py"))
    assert not os.path.exists(os.path.join(REPO, "ops", "backfill"))          # HB-6, not HB-2
    hb1 = os.path.join(REPO, "worker", "financial_backfill")
    assert sorted(f for f in os.listdir(hb1) if f.endswith(".py")) == [
        "__init__.py", "keys.py", "owner.py", "preflight.py", "records.py", "states.py", "store.py"]


def test_x2_static_checks_catch_planted_violations(tmp_path):
    for name, src, want in (
            ("a.py", "import requests\n", "network module"),
            ("b.py", "from .. import cse_client\n", "worker.cse_client"),
            ("c.py", "from ..market_capture import http as p2http\nx = p2http.classify\n", "P2's classify"),
            ("d.py", "from ..scheduler import wakeup\n", "worker.scheduler.wakeup"),
            ("e.py", "KEY = 4_346_836_117_002_314\n", "advisory-lock literal"),
            ("f.py", "if __name__ == '__main__':\n    pass\n", "entry point"),
            ("g.py", "from ..financial_backfill import owner\n", "worker.financial_backfill.owner"),
            ("h.py", "x = 'pg_try_advisory_lock'\n", "pg_try_advisory"),
            ("__main__.py", "", "__main__ entry point")):
        d = tmp_path / name.replace(".py", "")
        d.mkdir()
        (d / name).write_text(src, encoding="utf-8")
        found = preflight.static_problems(str(d))
        assert [p for p in found if want in p], (name, found)


def test_x3_pins_detect_a_changed_p3_module(tmp_path):
    copy = tmp_path / "repo"
    for rel in preflight.PINNED_FILES:
        dst = copy / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(REPO, rel), dst)
    assert preflight.pin_problems(str(copy)) == []
    with open(copy / "worker" / "scheduler" / "schedule.py", "a", encoding="utf-8") as f:
        f.write("\n")
    assert preflight.pin_problems(str(copy)) == ["frozen file worker/scheduler/schedule.py changed (HB-B9: the "
                                                 "transport reads exactly the frozen code)"]


def test_x4_spool_paths_are_checked(tmp_path):
    assert preflight.spool_problems(str(tmp_path)) == []
    assert preflight.spool_problems(str(tmp_path / "missing"))
    assert preflight.spool_problems(None)


def test_k1_a_slice_closes_completed_only_when_every_attempt_has_an_outcome(tmp_path, monkeypatch):
    """B-HB2-1: an attempt without an outcome (or an unverifiable lease) keeps the lease ACTIVE for recovery; nothing
    is invented, and a slice whose attempts all have outcomes still closes 'completed'."""
    from worker.backfill_transport import slice as tslice
    from worker.financial_backfill import store as hb1_store
    calls = []
    monkeypatch.setattr(hb1_store, "release_lease", lambda conn, lease, result, details: calls.append(("lease", result)))
    monkeypatch.setattr(hb1_store, "finish_wakeup", lambda conn, w, result, details, error=None:
                        calls.append(("wakeup", result, error)))
    monkeypatch.setattr(tslice, "_release_lock", lambda conn: calls.append(("lock",)))

    def run(open_attempts):
        calls.clear()
        sl, *_ = make_slice(tmp_path / str(len(os.listdir(tmp_path))), monkeypatch)
        monkeypatch.setattr(hb1_store, "open_attempts", open_attempts)
        sl.close()
        return list(calls), sl
    out, _ = run(lambda conn, lease: [])
    assert out == [("lease", "completed"), ("wakeup", "completed", None), ("lock",)]
    out, _ = run(lambda conn, lease: [42])
    assert [c[:2] for c in out] == [("wakeup", "error"), ("lock",)] and "[42]" in out[0][2]      # lease kept
    out, _ = run(lambda conn, lease: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert [c[:2] for c in out] == [("wakeup", "error"), ("lock",)] and "not verifiable" in out[0][2]
    # a stop with its own result: still never released while an attempt lacks an outcome
    calls.clear()
    sl, *_ = make_slice(tmp_path / "stop", monkeypatch)
    monkeypatch.setattr(hb1_store, "open_attempts", lambda conn, lease: [7])
    sl.stop = Refused([("budget", "spent")])
    sl.close()
    assert [c[:2] for c in calls] == [("wakeup", "error"), ("lock",)]
