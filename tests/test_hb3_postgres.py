"""
Phase 2 HB-3 (discovery and issuer evidence) against REAL PostgreSQL 17 with migration 0016's real guards and HB-2's
frozen governed transport. Every CSE response comes from a scripted transport: nothing contacts the network.

The HB-P1 runtime gate is exercised both ways:
  - absent: no derived P2 security master in the database -> every HB-3 entry point that could make a discovery
    request (the discovery slice, the listing plan, the issuer import) refuses;
  - present: the security master is produced by P2's OWN frozen capture code (capture.start -> derive_run ->
    ensure_companies) driven by P2's scripted fake CSE in a throwaway database. That is test evidence only: it does NOT
    satisfy production HB-P1, which needs the first governed P2 capture on the deployed server. Nothing here inserts
    into `companies` directly.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb3_postgres.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb2_fakes as F  # noqa: E402
import p2_fakes as P  # noqa: E402
from worker import report_discovery as f1  # noqa: E402
from worker.backfill_discovery import accounting, discovery, f1_cycle, identity  # noqa: E402
from worker.backfill_discovery.discovery import DiscoverySlice, create_plan_items  # noqa: E402
from worker.backfill_discovery.errors import DiscoveryRefused, SecurityMasterUnavailable  # noqa: E402
from worker.backfill_transport import gates, ledger as tledger, requester  # noqa: E402
from worker.backfill_transport.errors import Refused  # noqa: E402
from worker.backfill_transport.slice import Runtime, open_slice  # noqa: E402
from worker.financial_backfill import owner, store  # noqa: E402
from worker.market_capture import capture, config as p2config, http as p2http, runs as p2runs  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
UTC = timezone.utc
P2_START = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)          # Friday 15:30 Colombo: a scripted post-close capture
P2_TD = date(2026, 10, 2)
P2_SHIFT = 28                                                 # the real 2026-09-04 rows, as if traded on 2026-10-02
SWEEP_START = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)         # Saturday 06:30 Colombo (calendar: closed)
IN_W_MS = 1740800000000                                       # 2025-03-01 Colombo: inside W
COMB_SEC, LOLC_SEC = 369, 378


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        if self.family != socket.AF_UNIX:
            raise AssertionError(f"a network connection was attempted: {address!r}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a network connection was attempted")))


@pytest.fixture(scope="module")
def cluster():
    base = tempfile.mkdtemp(prefix="hb3", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


class Env:
    def __init__(self, cluster, tmp_path):
        self.cluster, self.db, self._open = cluster, S.fresh_db(cluster), []
        self.root = tmp_path / "backup"
        self.spool = self.root / "spool"
        self.spool.mkdir(parents=True)
        self._owner = None

    def conn(self, user="cse_worker"):
        c = S.conn(self.cluster, self.db, user, False)
        self._open.append(c)
        return c

    def owner_conn(self):
        if self._owner is None:
            self._owner = self.conn("cse_migrator")
        return self._owner

    def p2(self, start, policy, td, **fake):
        cfg = p2config.load({"CSE_BACKUP_ROOT": str(self.root), "CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT},
                            require_contact=True)
        clock = P.FakeClock(start)
        cse = P.FakeCSE(clock, **fake)
        rt = capture.Runtime(transport=cse, clock=clock.monotonic, wall=clock.wall, sleep=clock.sleep,
                             log=lambda m: None)
        state, rep = capture.start(self.conn(), self.conn(), cfg, trading_date=td, policy=policy, rt=rt)
        return state, rep

    def capture_master(self, start=P2_START, td=P2_TD):
        """The security master, through P2's own frozen capture and derivation (scripted transport)."""
        state, rep = self.p2(start, p2config.daily_policy("post_close"), td, shift_days=P2_SHIFT)
        assert state == "succeeded"
        return rep["run_id"]

    def sweep(self, start=SWEEP_START, limit=None, **fake):
        """A P2 metadata sweep on a non-trading Colombo day (the calendar records it closed)."""
        day = start.astimezone(p2config.COLOMBO).date()
        q(self.conn(), "insert into trading_calendar (trade_date, market_status, established_by) values (%s, "
                       "'closed', 'live_capture') on conflict do nothing", (day,))
        state, rep = self.p2(start, p2config.sweep_policy(sweep_limit=limit), day, **fake)
        assert state == "succeeded"
        return rep["run_id"]

    def close(self):
        for c in self._open:
            try:
                c.close()
            except Exception:
                pass


@pytest.fixture
def env(cluster, tmp_path):
    e = Env(cluster, tmp_path)
    yield e
    e.close()


def q(c, sql, args=None):
    try:
        with c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if cur.description else None
        c.commit()
    except Exception:
        c.rollback()
        raise
    return rows


def arm(env, **over):
    d = dict(armed=True, note="owner arming for the HB-3 tests", armed_stages=("HB-S1", "HB-S2"),
             window_first_date=date(2021, 4, 1), window_last_date=date(2026, 9, 30), daily_request_budget=600,
             combined_daily_ceiling=800, slice_max_json_requests=30, slice_max_documents=10, slice_max_seconds=600,
             attempts_per_json_request=1, attempts_per_document=2, item_max_attempts=3, user_agent=F.USER_AGENT,
             host=F.HOST, version_tuple=gates.running_version_tuple(), expected_requests={"feed": 66, "listings": 7},
             stop_conditions=("any block",), g1_reference="G-1 (test)")
    d.update(over)
    return owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision(**d), "tester")


def disarm(env):
    return owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision.disarm("owner disarm in a test"),
                                        "tester")


class RoutedTransport:
    """A scripted HB-2 JSON transport: `route(url, params)` gives the next response dict (status, body, headers,
    error_kind) or raises. Records every call; never opens a socket."""

    def __init__(self, clock, route, on_send=None, duration=0.2):
        self.clock, self.route, self.on_send, self.duration, self.calls = clock, route, on_send, duration, []

    def send(self, method, url, params, headers, timeout, clock=None, wall=None):
        if self.on_send:
            self.on_send(len(self.calls), params)
        step = self.route(url, dict(params))
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})
        ex = p2http.Exchange(method=method, url=url, params=dict(params), request_headers=dict(headers),
                             requested_at=self.clock.wall())
        self.clock.t += self.duration
        ex.elapsed_ms = int(self.duration * 1000)
        if step.get("error_kind"):
            ex.error_kind, ex.error = step["error_kind"], f"Fake{step['error_kind'].title()}: scripted"
            return ex
        ex.status = step["status"]
        ex.response_headers, ex.removed_response_headers = p2http.sanitize_headers(step.get("headers") or {})
        ex.body = step.get("body")
        ex.observed_at = self.clock.wall()
        return ex


class ScriptedRuntime(Runtime):
    max_failures = None

    def policy(self):
        p = super().policy()
        if self.max_failures is not None:
            import dataclasses
            return dataclasses.replace(p, max_consecutive_failures=self.max_failures)
        return p


def runtime(env, clock, route=None, *, script=None, on_send=None, max_failures=None):
    if route is None:
        steps = list(script or [])

        def route(url, params):
            if not steps:
                raise AssertionError("the transport was called more often than scripted")
            return steps.pop(0)
    transport = RoutedTransport(clock, route, on_send=on_send)
    rt = ScriptedRuntime(wall=clock.wall, clock=clock.clock, sleep=clock.sleep, hostname=lambda: F.HOST,
                     clock_synchronized=lambda: True,
                     env={"CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT, "CSE_BACKUP_ROOT": str(env.root)},
                     spool_root=str(env.spool), json_transport=transport)
    rt.max_failures = max_failures
    rt.transport_double = transport
    return rt


def ok(body):
    return dict(status=200, body=body if isinstance(body, bytes) else json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})


def filing(fid, path, uploaded_ms=IN_W_MS, text="Annual Report"):
    return {"id": fid, "path": path, "manualDate": None, "uploadedDate": uploaded_ms, "fileText": text, "path2": None,
            "authorizedDate": None}


def listing_body(sec_ids=(), annual=()):
    return {"reqFinancial": [{"secId": s, "elmId": "1", "data": "x"} for s in sec_ids], "infoAnnualData": list(annual),
            "infoQuarterlyData": [], "infoOtherData": [], "infoWebLink": []}


FEED_EMPTY = {"reqFinancialAnnouncemnets": []}


def ready(env, clock=None):
    """Security master (P2's own capture), arming (D-HB3-1) and the plan."""
    env.capture_master()
    arm(env)
    clock = clock or F.FakeClock()
    out = create_plan_items(env.conn(), wall=clock.wall())
    return clock, out


def item(w, key):
    got = store.item_by_key(w, key)
    assert got is not None, key
    return got


def state(w, it):
    return store.current_state(w, it["id"])["state"]


def last_event(w, it):
    return store.events(w, it["id"])[-1]


def f1_runs(w, symbol):
    return q(w, "select id, status, started_at, request_params, http_status from report_discovery_runs where "
                "source_endpoint = 'financials' and request_params ->> 'symbol' = %s order by started_at", (symbol,))


def lease_row(w, lease_id):
    return q(w, "select state, result from backfill_leases where id = %s", (lease_id,))[0]


def wait_lock_free(c, timeout=10.0):
    end = time.monotonic() + timeout
    while not p2runs.acquire_global_lock(c):
        assert time.monotonic() < end, "the dead session's lock was not released"
        time.sleep(0.05)
    p2runs.release_global_lock(c)


def one_slice(env, clock, it, *, script=(), route=None, conn=None, **kw):
    """Open a discovery slice, try one item, close it; returns (slice, error or None)."""
    rt = runtime(env, clock, route, script=list(script), **kw)
    ds = DiscoverySlice(conn or env.conn(), runtime=rt)
    err = None
    try:
        with ds:
            ds.discover_one(it)
    except BaseException as exc:  # noqa: BLE001 — the test inspects it
        err = exc
    return ds, err, rt


# ================================================================================================ HB-P1 runtime gate

def test_p1_absent_every_entry_point_refuses_and_nothing_is_sent(env):
    """HB-P1 unsatisfied: implementation and offline tests are allowed, live discovery is not. No lock, wake-up, lease,
    item or request comes out of any HB-3 entry point."""
    w = env.conn()
    arm(env)
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[])
    with pytest.raises(SecurityMasterUnavailable) as ei:
        DiscoverySlice(w, runtime=rt).__enter__()
    assert ei.value.codes == ["hb_p1"]
    with pytest.raises(SecurityMasterUnavailable):
        create_plan_items(w, wall=clock.wall())
    with pytest.raises(SecurityMasterUnavailable):
        identity.identity_pass(w, wall=clock.wall())
    assert rt.transport_double.calls == []
    assert q(w, "select (select count(*) from backfill_wakeups), (select count(*) from backfill_leases), "
                "(select count(*) from backfill_work_items), (select count(*) from companies)") == [(0, 0, 0, 0)]


def test_p1_a_stale_or_future_security_master_refuses(env):
    env.capture_master()
    arm(env)
    w = env.conn()
    late = F.FakeClock(F.BASE_WALL + timedelta(days=8))
    with pytest.raises(SecurityMasterUnavailable) as ei:
        DiscoverySlice(w, runtime=runtime(env, late, script=[])).__enter__()
    assert ei.value.codes == ["hb_p1_stale"]
    early = F.FakeClock(P2_START - timedelta(days=1))
    with pytest.raises(SecurityMasterUnavailable) as ei:
        create_plan_items(w, wall=early.wall())
    assert ei.value.codes == ["hb_p1_clock"]
    arm(env, stop_conditions=({"security_master_max_age_days": 30}, "any block"))   # may only tighten, never loosen
    with pytest.raises(SecurityMasterUnavailable):
        create_plan_items(w, wall=late.wall())
    assert q(w, "select count(*) from backfill_leases") == [(0,)]


def test_p1_a_sweep_alone_is_not_a_security_master(env):
    """A metadata sweep archives allSecurityCode but derives nothing: it never satisfies HB-P1."""
    env.sweep()
    arm(env)
    with pytest.raises(SecurityMasterUnavailable) as ei:
        create_plan_items(env.conn(), wall=F.FakeClock().wall())
    assert ei.value.codes == ["hb_p1"]


def test_p1_fixture_evidence_allows_the_offline_logic_only(env):
    """With security-master evidence produced by P2's own capture code against a scripted CSE (test evidence, NOT
    production HB-P1), the plan is the 66 Colombo months of W and exactly the derived securities."""
    clock, out = ready(env)
    assert (out["feed_window"], out["listing"], out["existing"]) == (66, 7, 0)
    w = env.conn()
    assert sorted(r["query_symbol"] for r in discovery.item_rows(w, ("listing",))) == sorted(P.TRADED + P.ABSENT)
    months = [r["window_month"] for r in discovery.item_rows(w, ("feed_window",))]
    assert min(months) == date(2021, 4, 1) and max(months) == date(2026, 9, 1) and len(set(months)) == 66
    again = create_plan_items(w, wall=clock.wall())
    assert (again["feed_window"], again["listing"], again["existing"]) == (0, 0, 73)
    ds, err, rt = one_slice(env, clock, item(w, "listing:COMB.N0000"), script=[ok(listing_body([COMB_SEC]))])
    assert err is None and state(w, item(w, "listing:COMB.N0000")) == "succeeded"


def test_p1_hb3_never_plans_a_symbol_outside_the_security_master(env):
    clock, _ = ready(env)
    w = env.conn()
    stray, _ = store.ensure_item(w, {"item_kind": "listing", "natural_key": "listing:DELIST.N0000",
                                     "query_symbol": "DELIST.N0000"})
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(w, runtime=rt) as ds:
        assert stray["id"] not in {r["id"] for r in ds.eligible()}
        with pytest.raises(DiscoveryRefused) as ei:
            ds.discover_one(stray)
        assert ei.value.codes == ["plan"]
    assert rt.transport_double.calls == [] and state(w, stray) == "pending"


def test_p1_an_underived_capture_is_not_a_security_master(env):
    """A market capture whose tradeSummary does not show its trading date archives allSecurityCode but P2 derives
    nothing from it, so ensure_companies never ran: HB-P1 is not satisfied."""
    state, _ = env.p2(P2_START, p2config.daily_policy("post_close"), P2_TD)        # the 2026-09-04 session rows
    assert state != "succeeded"
    arm(env)
    w = env.conn()
    assert q(w, "select count(*) from market_source_responses where request_key = 'allSecurityCode' and "
                "outcome = 'ok'") == [(1,)]
    with pytest.raises(SecurityMasterUnavailable) as ei:
        create_plan_items(w, wall=F.FakeClock().wall())
    assert ei.value.codes == ["hb_p1"]


def test_p1_a_corrupted_archived_universe_refuses(env):
    """Defence in depth: P2's own CHECK already makes this impossible; in this throwaway database the test removes
    that CHECK to show HB-3 re-verifies the bytes and fails closed."""
    env.capture_master()
    arm(env)
    su = env.conn("postgres")
    q(su, "alter table market_response_bodies drop constraint chk_mrb_exact")
    q(su, "alter table market_response_bodies disable trigger user")
    # the same securities re-serialised: only the SHA-256 re-verification can tell the bytes changed
    sha, b64 = q(su, "select b.body_sha256, b.body_base64 from market_response_bodies b join market_source_responses r "
                     "on r.body_sha256 = b.body_sha256 where r.request_key = 'allSecurityCode'")[0]
    import base64 as b64mod
    altered = json.dumps(json.loads(b64mod.b64decode(b64)), indent=2).encode()
    q(su, "update market_response_bodies set body_base64 = %s where body_sha256 = %s",
      (b64mod.b64encode(altered).decode(), sha))
    q(su, "alter table market_response_bodies enable trigger user")
    with pytest.raises(SecurityMasterUnavailable) as ei:
        create_plan_items(env.conn(), wall=F.FakeClock().wall())
    assert "hb_p1_body" in ei.value.codes and "SHA-256" in str(ei.value)


# ================================================================================================ D-HB3-1

def test_d1_an_arming_with_hidden_retries_is_refused(env):
    env.capture_master()
    w = env.conn()
    for over in ({"attempts_per_json_request": 3}, {"item_max_attempts": 4}):
        arm(env, **over)
        with pytest.raises(DiscoveryRefused) as ei:
            DiscoverySlice(w, runtime=runtime(env, F.FakeClock(), script=[])).__enter__()
        assert "d_hb3_1" in ei.value.codes
        with pytest.raises(DiscoveryRefused):
            create_plan_items(w, wall=F.FakeClock().wall())
    assert q(w, "select count(*) from backfill_leases") == [(0,)]


def test_d1_one_f1_run_per_http_attempt_and_a_retry_is_a_new_claim(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    ds, err, rt1 = one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    assert err is None and state(w, it) == "retry_wait" and len(rt1.transport_double.calls) == 1
    ev = last_event(w, it)
    assert ev["f1_run_id"] is not None and ev["attempt_id"] is not None and ev["reason"] == "server_error"
    rt = runtime(env, clock, script=[])                       # in the same slice the item is never tried twice
    ds2, err, rt2 = one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    assert err is None and state(w, it) == "succeeded"
    attempts = store.attempts(w, it["id"])
    runs = f1_runs(w, "COMB.N0000")
    assert [a["outcome"] for a in attempts] == ["server_error", "ok"]
    assert [(r[1], r[4]) for r in runs] == [("failed", 503), ("succeeded", 200)]
    claims = [(e["lease_id"], e["occurred_at"]) for e in store.events(w, it["id"]) if e["action"] == "claim"]
    assert len(claims) == 2 and {a["lease_id"] for a in attempts} == {c[0] for c in claims}
    for (lease, at), a, r in zip(claims, attempts, runs):       # one claim -> one F1 run -> one HTTP attempt
        assert a["lease_id"] == lease and r[2] == at and r[3] == a["request_params"] == {"symbol": "COMB.N0000"}
    assert len(rt1.transport_double.calls) + len(rt2.transport_double.calls) == len(attempts) == len(runs) == 2
    assert accounting.counts(w, it["id"]) == {"claims": 2, "http_attempts": 2}


def test_d1_no_second_attempt_inside_one_slice(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    rt = runtime(env, clock, script=[dict(status=503, body=b"busy")])
    with DiscoverySlice(w, runtime=rt) as ds:
        ds.discover_one(it)
        assert it["id"] not in {r["id"] for r in ds.eligible()}
        with pytest.raises(DiscoveryRefused) as ei:
            ds.discover_one(it)
        assert ei.value.codes == ["one_claim_per_slice"]
    assert len(rt.transport_double.calls) == 1 and len(store.attempts(w, it["id"])) == 1


def test_d1_the_maximum_is_three_actual_attempts_then_failed_g10a(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    calls = 0
    for n in range(3):
        ds, err, rt = one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
        assert err is None
        calls += len(rt.transport_double.calls)
    assert state(w, it) == "failed" and calls == 3
    ev = last_event(w, it)
    runs = f1_runs(w, "COMB.N0000")
    assert ev["f1_run_id"] == runs[-1][0] and runs[-1][1] == "failed"
    assert accounting.counts(w, it["id"]) == {"claims": 3, "http_attempts": 3}
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(w, runtime=rt) as ds:
        assert it["id"] not in {r["id"] for r in ds.eligible()}
    assert rt.transport_double.calls == []


# ================================================================================================ G2

def test_g2_exception_before_the_f1_run_keeps_the_lease_and_the_connection_is_discarded(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    a = env.conn()
    monkeypatch.setattr(f1_cycle, "begin", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("boom before F1")))
    ds, err, rt = one_slice(env, clock, it, conn=a)
    assert isinstance(err, RuntimeError) and ds.lease_kept and a.closed
    assert lease_row(w, ds.sl.lease_id) == ("active", None) and state(w, it) == "requesting"
    assert q(w, "select result from backfill_wakeups where id = %s", (ds.sl.wakeup_id,)) == [("error",)]
    with pytest.raises(Exception):                            # never reused
        DiscoverySlice(a, runtime=runtime(env, clock)).__enter__()
    monkeypatch.undo()
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])) as ds2:
        promoted = dict(ds2.reconciled["promoted"])
    assert promoted == {it["id"]: "pending"} and lease_row(w, ds.sl.lease_id)[0] == "expired"
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 0} and f1_runs(w, "COMB.N0000") == []


def test_g2_exception_after_the_f1_run_before_the_intent(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    monkeypatch.setattr(requester, "request", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("boom pre-intent")))
    ds, err, rt = one_slice(env, clock, it)
    assert isinstance(err, RuntimeError) and ds.lease_kept and rt.transport_double.calls == []
    monkeypatch.undo()
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])):
        pass
    assert state(w, it) == "pending" and [r[1] for r in f1_runs(w, "COMB.N0000")] == ["running"]
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 0}


def test_g2_exception_after_the_intent_where_hb2_alone_would_release(env, monkeypatch):
    """The outcome is committed, so HB-2's own check (attempts without an outcome) finds nothing and would release
    the lease with the item still 'requesting'. HB-3's in-flight guard keeps it; the next slice finishes THE SAME F1
    run from the recorded response, with no new request."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    real = f1_cycle.finish_from_ledger
    monkeypatch.setattr(f1_cycle, "finish_from_ledger",
                        lambda *x, **k: (_ for _ in ()).throw(RuntimeError("boom after the outcome")))
    ds, err, rt = one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    assert isinstance(err, RuntimeError) and ds.lease_kept
    assert store.open_attempts(w, ds.sl.lease_id) == [] and lease_row(w, ds.sl.lease_id)[0] == "active"
    monkeypatch.setattr(f1_cycle, "finish_from_ledger", real)
    rt2 = runtime(env, clock, script=[])
    with DiscoverySlice(env.conn(), runtime=rt2):
        pass
    assert state(w, it) == "succeeded" and rt2.transport_double.calls == []
    assert [r[1] for r in f1_runs(w, "COMB.N0000")] == ["succeeded"] and len(rt.transport_double.calls) == 1


def test_g2_blocked_records_the_item_then_releases(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    rt = runtime(env, clock, script=[dict(status=403, body=b"{}")])
    with DiscoverySlice(w, runtime=rt) as ds:
        ds.run(max_items=None)                               # the first eligible item is the 2021-04 feed month
    blocked = [r for r in discovery.item_rows(w) if r["state"] == "blocked"]
    assert len(blocked) == 1
    ev = last_event(w, blocked[0])
    assert ev["block_id"] is not None and ev["f1_run_id"] is not None
    assert q(w, "select status, http_status from report_discovery_runs where id = %s", (ev["f1_run_id"],)) == \
        [("failed", 403)]
    assert lease_row(w, ds.sl.lease_id) == ("released", "blocked") and not ds.lease_kept and not w.closed
    assert len(rt.transport_double.calls) == 1
    with pytest.raises(Exception) as ei:                     # every stage stops until the owner acknowledges it
        DiscoverySlice(w, runtime=runtime(env, clock, script=[])).__enter__()
    assert "blocked" in str(ei.value)
    assert state(w, it) == "pending"


def test_g2_circuit_open_records_the_item_then_releases(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    rt = runtime(env, clock, script=[dict(status=503, body=b"busy")], max_failures=1)
    ds = DiscoverySlice(w, runtime=rt)
    with ds:
        with pytest.raises(Exception) as ei:
            ds.discover_one(it)
        assert type(ei.value).__name__ == "CircuitOpen"
    assert state(w, it) == "retry_wait" and lease_row(w, ds.sl.lease_id) == ("released", "circuit_open")


def test_g2_refused_after_the_claim_before_the_f1_run(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    real = accounting.claims

    def claims_then_disarm(conn, item_id):
        disarm(env)
        return real(conn, item_id)
    rt = runtime(env, clock, script=[])
    ds = DiscoverySlice(w, runtime=rt)
    with ds:
        monkeypatch.setattr(accounting, "claims", claims_then_disarm)      # after the slice opened
        with pytest.raises(Refused):
            ds.discover_one(it)
    ev = last_event(w, it)
    assert state(w, it) == "retry_wait" and "refused before any request" in ev["reason"] and ev["f1_run_id"] is None
    assert lease_row(w, ds.sl.lease_id) == ("released", "refused") and rt.transport_double.calls == []
    assert f1_runs(w, "COMB.N0000") == []


def test_g10_a_refusal_on_the_last_claim_is_final_at_once(env, monkeypatch):
    """G10 case a on the refusal path: a claim refused before any request that was the item's last allowed claim
    makes it failed at once, with the reason (never retry_wait left for a later slice)."""
    clock, _ = ready(env)
    arm(env, item_max_attempts=1)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    real = accounting.claims

    def claims_then_disarm(conn, item_id):
        disarm(env)
        return real(conn, item_id)
    rt = runtime(env, clock, script=[])
    ds = DiscoverySlice(w, runtime=rt)
    with ds:
        monkeypatch.setattr(accounting, "claims", claims_then_disarm)      # after the slice opened
        with pytest.raises(Refused):
            ds.discover_one(it)
    ev = last_event(w, it)
    assert (ev["state"], ev["action"], ev["f1_run_id"]) == ("failed", "record", None)
    assert "refused before any request" in ev["reason"] and ev["details"]["claims"] == 1
    assert rt.transport_double.calls == [] and f1_runs(w, "COMB.N0000") == []


def test_g2_refused_after_the_f1_run_never_names_the_running_run(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    real = f1_cycle.begin

    def begin_then_disarm(*a, **k):
        run = real(*a, **k)
        disarm(env)
        return run
    monkeypatch.setattr(f1_cycle, "begin", begin_then_disarm)
    rt = runtime(env, clock, script=[])
    ds = DiscoverySlice(w, runtime=rt)
    with ds:
        with pytest.raises(Refused):
            ds.discover_one(it)
    ev = last_event(w, it)
    runs = f1_runs(w, "COMB.N0000")
    assert state(w, it) == "retry_wait" and ev["f1_run_id"] is None
    assert [r[1] for r in runs] == ["running"] and ev["details"]["f1_run_without_response"] == str(runs[0][0])
    assert rt.transport_double.calls == [] and accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 0}


def test_g2_normal_close_releases_and_keeps_the_connection(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    a = env.conn()
    ds, err, rt = one_slice(env, clock, it, conn=a, script=[ok(listing_body([COMB_SEC]))])
    assert err is None and state(w, it) == "succeeded" and not ds.lease_kept and not a.closed
    assert lease_row(w, ds.sl.lease_id) == ("released", "completed")


def test_g2_the_guard_itself_against_the_raw_hb2_hazard(env):
    """Without HB-3's guard, HB-2's Slice.close() releases a lease on a TransportStop while a claimed item is still
    'requesting', and that item can never leave it. With the guard, the lease is kept and the item recovered."""
    clock, _ = ready(env)
    w = env.conn()
    raw_item, guarded_item = item(w, "listing:HNB.N0000"), item(w, "listing:JKH.N0000")
    raw = open_slice(env.conn(), stage="HB-S2", kind="json", runtime=runtime(env, clock, script=[]))
    raw.claim(raw_item["id"])
    raw.close(Refused([("test", "a stop before the item event")]))          # what HB-3 must never do
    assert lease_row(w, raw.lease_id) == ("released", "refused") and state(w, raw_item) == "requesting"
    with pytest.raises(Exception):
        store.append_event(w, raw_item["id"], "retry_wait", "record", lease_id=raw.lease_id, reason="too late")
    a = env.conn()
    ds = DiscoverySlice(a, runtime=runtime(env, clock, script=[]))
    ds.__enter__()
    ds.sl.claim(guarded_item["id"])
    ds.close(Refused([("test", "a stop before the item event")]))
    assert ds.lease_kept and a.closed and lease_row(w, ds.sl.lease_id) == ("active", None)
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])):
        pass
    assert state(w, guarded_item) == "pending"


def test_g2_session_death_mid_request_is_recovered_and_counted(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    a = env.conn()

    def die(n, params):
        raise KeyboardInterrupt("the process is killed while the request is in flight")
    ds = DiscoverySlice(a, runtime=runtime(env, clock, script=[], on_send=die))
    ds.__enter__()
    with pytest.raises(KeyboardInterrupt):
        ds.discover_one(it)
    a.close()                                                # no close: the session dies
    b = env.conn()
    wait_lock_free(b)
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(b, runtime=rt) as ds2:
        rec = ds2.sl.recovered["leases"]
    assert [(r["lease_id"], len(r["unrecorded"])) for r in rec] == [(ds.sl.lease_id, 1)]
    assert state(w, it) == "pending" and [r[1] for r in f1_runs(w, "COMB.N0000")] == ["running"]
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 1} and rt.transport_double.calls == []


# ================================================================================================ G10

def _crash_before_f1(env, clock, it, monkeypatch, times):
    monkeypatch.setattr(f1_cycle, "begin", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    for _ in range(times):
        ds, err, _rt = one_slice(env, clock, it)
        assert isinstance(err, RuntimeError) and ds.lease_kept
    monkeypatch.undo()


def test_g10_final_claim_with_no_f1_run_is_terminalised_without_a_request_and_idempotently(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    _crash_before_f1(env, clock, it, monkeypatch, 3)
    rt = runtime(env, clock, script=[])
    ds = DiscoverySlice(env.conn(), runtime=rt)
    with ds:
        assert dict(ds.reconciled["terminalised"]) == {it["id"]: "failed"}
        n_events = len(store.events(w, it["id"]))
        assert discovery.terminalise(ds, discovery.item_rows(w, ("listing",))[2]) is None    # idempotent: final
        assert len(store.events(w, it["id"])) == n_events
    evs = store.events(w, it["id"])
    assert [(e["state"], e["action"]) for e in evs[-2:]] == [("requesting", "claim"), ("failed", "record")]
    assert evs[-1]["f1_run_id"] is None and "G10" in evs[-1]["reason"]
    assert evs[-1]["details"]["claims"] == 3 and evs[-1]["details"]["http_attempts"] == 0
    assert rt.transport_double.calls == [] and store.attempts(w, it["id"]) == []


def test_g10_retry_wait_at_the_maximum_names_its_failed_f1_run(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    for _ in range(2):
        one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    assert state(w, it) == "retry_wait"
    arm(env, item_max_attempts=2)                            # the owner lowers the maximum: G10 case b
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(env.conn(), runtime=rt):
        pass
    ev = last_event(w, it)
    runs = f1_runs(w, "COMB.N0000")
    assert (ev["state"], ev["action"]) == ("failed", "record") and ev["f1_run_id"] == runs[-1][0]
    assert runs[-1][1] == "failed" and rt.transport_double.calls == []


def test_g10_final_claim_after_an_unrecorded_attempt_names_no_running_run(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    for _ in range(2):
        one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    a = env.conn()
    ds = DiscoverySlice(a, runtime=runtime(env, clock, script=[], on_send=lambda n, p: (_ for _ in ()).throw(
        KeyboardInterrupt("killed"))))
    ds.__enter__()
    with pytest.raises(KeyboardInterrupt):
        ds.discover_one(it)
    a.close()
    b = env.conn()
    wait_lock_free(b)
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(b, runtime=rt):
        pass
    ev = last_event(w, it)
    runs = f1_runs(w, "COMB.N0000")
    assert ev["state"] == "failed" and ev["f1_run_id"] is None and [r[1] for r in runs] == ["failed", "failed",
                                                                                          "running"]
    assert ev["details"]["http_attempts"] == 3 and {"run": str(runs[-1][0]), "status": "running"} in \
        ev["details"]["f1_runs"]
    assert [a_["outcome"] for a_ in store.attempts(w, it["id"])] == ["server_error", "server_error", "unrecorded"]
    assert rt.transport_double.calls == []


def test_g10_a_recoverable_spool_response_wins_on_the_final_claim(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    for _ in range(2):
        one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    monkeypatch.setattr(tledger, "record_outcome", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    ds, err, rt = one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    assert type(err).__name__ == "DurabilityStop" or ds.lease_kept
    assert ds.lease_kept
    monkeypatch.undo()
    rt2 = runtime(env, clock, script=[])
    with DiscoverySlice(env.conn(), runtime=rt2) as ds2:
        rec = ds2.sl.recovered["leases"]
    assert len(rec[0]["recovered_from_spool"]) == 1
    assert state(w, it) == "succeeded" and rt2.transport_double.calls == []
    assert [r[1] for r in f1_runs(w, "COMB.N0000")] == ["failed", "failed", "succeeded"]


def test_g10_evidence_wins_over_recovery_and_terminalisation(env, monkeypatch):
    """An F1 run of the same request that succeeded (here: before an operator re-queue) wins over both a crashed
    claim's promotion and G10's terminalisation; HB-1's own guard refuses 'failed' against it."""
    clock, _ = ready(env)
    w = env.conn()
    it, it2 = item(w, "listing:COMB.N0000"), item(w, "listing:HNB.N0000")
    one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    one_slice(env, clock, it2, script=[ok(listing_body([373]))])
    good, good2 = f1_runs(w, "COMB.N0000")[0][0], f1_runs(w, "HNB.N0000")[0][0]
    good_att, good_att2 = store.attempts(w, it["id"])[0]["id"], store.attempts(w, it2["id"])[0]["id"]
    for x in (it, it2):
        store.append_event(w, x["id"], "pending", "requeue", reason="operator re-queue in an HB-3 test")
    _crash_before_f1(env, clock, it, monkeypatch, 1)        # a crashed claim: recovery promotes to the old success
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])):
        pass
    ev = last_event(w, it)
    assert (ev["state"], ev["action"], ev["f1_run_id"]) == ("succeeded", "promote", good)
    for _ in range(2):                                        # two failed attempts after the re-queue
        one_slice(env, clock, it2, script=[dict(status=503, body=b"busy")])
    assert state(w, it2) == "retry_wait"
    with pytest.raises(Exception):
        store.append_event(w, it2["id"], "failed", "record", reason="no failed F1 run named")
    arm(env, item_max_attempts=2)                            # G10 case b on an item with success evidence
    rt = runtime(env, clock, script=[])
    with DiscoverySlice(env.conn(), runtime=rt):
        pass
    evs = store.events(w, it2["id"])
    assert [(e["state"], e["action"]) for e in evs[-2:]] == [("requesting", "claim"), ("succeeded", "record")]
    assert evs[-1]["f1_run_id"] == good2 and "evidence wins" in evs[-1]["reason"] and rt.transport_double.calls == []
    # IE-4 reads the response behind each winning run (a claim before the re-queue), though neither event names it
    found, without = identity.listing_responses(w)
    assert sorted((sym, att) for _i, sym, att, _r in found) == [("COMB.N0000", good_att), ("HNB.N0000", good_att2)]
    assert without == []


def test_g10_terminalisation_is_atomic(env, monkeypatch):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    _crash_before_f1(env, clock, it, monkeypatch, 3)
    real = discovery.append_event_in
    seen = []

    def second_fails(cur, item_id, st, action, **kw):
        seen.append((st, action))
        if len(seen) == 2:
            raise RuntimeError("crash inside the terminal transaction")
        return real(cur, item_id, st, action, **kw)
    monkeypatch.setattr(discovery, "append_event_in", second_fails)
    ds = DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[]))
    with pytest.raises(RuntimeError):
        ds.__enter__()                                        # recovery promotes; the terminal transaction crashes
    monkeypatch.undo()
    evs = store.events(w, it["id"])
    assert seen == [("requesting", "claim"), ("failed", "record")]
    assert (evs[-1]["state"], evs[-1]["action"]) == ("pending", "promote")       # the terminal claim rolled back
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])):
        pass
    evs = store.events(w, it["id"])
    assert [(e["state"], e["action"]) for e in evs[-3:]] == [("pending", "promote"), ("requesting", "claim"),
                                                              ("failed", "record")]


def test_g10_never_while_an_attempt_has_no_outcome(env):
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    arm(env, item_max_attempts=1)
    rt = runtime(env, clock, script=[])
    ds = DiscoverySlice(env.conn(), runtime=rt)
    ds.__enter__()
    try:
        ds.sl.claim(it["id"])
        tledger.record_intent(ds.conn, it["id"], ds.sl.lease_id, ds.sl.wakeup_id, request_class="json",
                              request_host="www.cse.lk", endpoint="financials", url="https://www.cse.lk/api/financials",
                              user_agent=F.USER_AGENT, params={"symbol": "COMB.N0000"}, headers={})
        store.append_event(ds.conn, it["id"], "retry_wait", "record", lease_id=ds.sl.lease_id, reason="test setup")
        assert accounting.open_attempts(w, it["id"]) and accounting.claims(w, it["id"]) == 1
        assert discovery.terminalise(ds, item(w, "listing:COMB.N0000")) is None
        assert state(w, it) == "retry_wait"
    finally:
        ds.close()
    assert ds.lease_kept                                      # an attempt without an outcome: HB-2 keeps the lease


def test_g10_never_below_the_maximum(env):
    """terminalise itself refuses an item below its claim maximum (not only its caller's filter): nothing recorded."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    assert state(w, it) == "retry_wait" and accounting.claims(w, it["id"]) == 1
    n = len(store.events(w, it["id"]))
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])) as ds:
        assert discovery.terminalise(ds, item(w, "listing:COMB.N0000")) is None
    assert len(store.events(w, it["id"])) == n and state(w, it) == "retry_wait"


def test_g2_an_unverifiable_in_flight_check_keeps_the_lease(env, monkeypatch):
    clock, _ = ready(env)
    a = env.conn()
    ds = DiscoverySlice(a, runtime=runtime(env, clock, script=[]))
    ds.__enter__()
    monkeypatch.setattr(accounting, "in_flight_items", lambda *x: (_ for _ in ()).throw(RuntimeError("db flaky")))
    ds.close()
    monkeypatch.undo()
    assert ds.lease_kept and a.closed and lease_row(env.conn(), ds.sl.lease_id)[0] == "active"


def test_g10_evidence_wins_when_the_last_live_attempt_fails(env):
    """G10 case a: the claim that reaches the maximum fails live, but an F1 run of the same request succeeded before
    an operator re-queue: the item ends succeeded with that run, never failed."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    good = f1_runs(w, "COMB.N0000")[0][0]
    store.append_event(w, it["id"], "pending", "requeue", reason="operator re-queue in an HB-3 test")
    for _ in range(3):
        ds, err, _rt = one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
        assert err is None
    ev = last_event(w, it)
    assert (ev["state"], ev["action"], ev["f1_run_id"]) == ("succeeded", "record", good)
    assert "evidence wins" in ev["reason"] and accounting.counts(w, it["id"]) == {"claims": 3, "http_attempts": 3}


def test_g10_evidence_wins_names_the_response_behind_the_winning_run_for_ie4(env):
    """Evidence wins on a live failure: the event names the attempt behind the winning F1 run (never the failed
    attempt), so IE-4 reads that run's archived listing and records its secIds instead of reporting the listing as
    having no Phase 2 response."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    good_run, good_attempt = f1_runs(w, "COMB.N0000")[0][0], store.attempts(w, it["id"])[0]["id"]
    store.append_event(w, it["id"], "pending", "requeue", reason="operator re-queue in an HB-3 test")
    for _ in range(3):
        one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    ev = last_event(w, it)
    assert (ev["state"], ev["f1_run_id"], ev["attempt_id"]) == ("succeeded", good_run, good_attempt)
    assert ev["details"]["last_attempt"] == store.attempts(w, it["id"])[-1]["id"] != good_attempt
    found, without = identity.listing_responses(w)
    assert [(sym, att) for _i, sym, att, _r in found] == [("COMB.N0000", good_attempt)] and without == []
    batch, without = identity.ie4_batch(w)
    assert [(o["symbol"], o["cse_sec_id"]) for o, _ in batch] == [("COMB.N0000", COMB_SEC)] and without == []


def test_g10_evidence_wins_matches_the_request_as_hb1s_guard_does(env):
    """Evidence wins on the request's fields, exactly as HB-1's guard for 'failed' matches them (here: symbol), not on
    the whole parameter object: a succeeded F1 run of the same listing with another parameter shape still wins."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    from worker.report_filings_store import PostgresFilingStore
    fs = PostgresFilingStore(w)
    at = clock.wall() - timedelta(days=30)
    other = fs.begin_run("financials", {"symbol": "COMB.N0000", "page": 1}, at)
    fs.finish_run(other, {"status": "succeeded", "failure_category": None, "http_status": 200, "rows_returned": 0,
                          "filings_new": 0, "observations_new": 0, "metadata_changes": 0, "rows_rejected": 0,
                          "item_failures": 0, "details": {}}, at)
    fs.commit()
    for _ in range(3):
        one_slice(env, clock, it, script=[dict(status=503, body=b"busy")])
    ev = last_event(w, it)
    assert (ev["state"], ev["f1_run_id"], ev["attempt_id"]) == ("succeeded", other, None)
    assert identity.listing_responses(w) == ([], ["COMB.N0000"])     # no Phase 2 response behind it: reported


def test_d1_a_call_behind_more_than_one_attempt_is_refused_and_g2_keeps_the_lease(env, monkeypatch):
    """Defence in depth for D-HB3-1: should the governed call ever report more than one HTTP attempt, HB-3 refuses
    to record an outcome for the claim, and G2 leaves the lease active with the item in flight."""
    from worker.backfill_transport.slice import Slice
    real = Slice.json_request

    def doubled(self, item_id, endpoint, params):
        result = real(self, item_id, endpoint, params)
        result.attempts = list(result.attempts) * 2
        return result
    monkeypatch.setattr(Slice, "json_request", doubled)
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    ds, err, _rt = one_slice(env, clock, it, script=[ok(listing_body([COMB_SEC]))])
    assert isinstance(err, RuntimeError) and "D-HB3-1" in str(err)
    assert state(w, it) == "requesting" and ds.lease_kept and lease_row(w, ds.sl.lease_id)[0] == "active"


def test_d1_one_attempt_turns_any_429_into_a_block(env):
    """D-HB3-1 under HB-2's frozen rule: a 429 that exhausts the attempts is a block. With one attempt per governed
    call the first 429 is the last attempt, so it blocks Phase 2 (owner acknowledgement), never a hidden retry."""
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    ds, err, rt = one_slice(env, clock, it, script=[dict(status=429, body=b"slow down", headers={"Retry-After": "5"})])
    assert type(err).__name__ == "Blocked" and len(rt.transport_double.calls) == 1
    assert state(w, it) == "blocked" and not ds.lease_kept
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 1}


def test_hold_an_owner_hold_is_never_released_by_a_later_import(env):
    """Resolution is owner-only (design section 7.7 step 4): a held observation stays held, unresolved or keep_held,
    even when later evidence would let the simulation record it; only the owner's acquire_evidence releases it."""
    clock, _ = ready(env)
    env.sweep(limit=5)
    w = env.conn()
    identity.identity_pass(w, wall=clock.wall())
    bodies = {s: listing_body() for s in P.TRADED + P.ABSENT}
    bodies["ABSB.N0000"] = listing_body([9901])               # ABSB lists ABSA's secId, with no identity evidence
    _close_discovery(env, clock, bodies)
    identity.closure_pass(w, wall=clock.wall())
    assert q(w, "select symbol from issuer_identifier_observations where cse_sec_id = 9901") == [("ABSB.N0000",)]
    env.sweep(start=SWEEP_START + timedelta(days=7))          # every security: ABSA's identity (9901, ISIN, name)
    late = identity.late_pass(w, wall=clock.wall())
    held = [r[0] for r in q(w, "select id from backfill_holds where symbol = 'ABSA.N0000' order by id")]
    assert len(held) == 2 and set(held) <= set(late["held"])  # absence-only dispute against ABSB's sighting
    agreeing = P.absent_ci_body("ABSA.N0000", 1)              # ABSB now carries ABSA's identity: no dispute at all
    agreeing["reqSymbolInfo"]["symbol"] = "ABSB.N0000"
    env.sweep(start=SWEEP_START + timedelta(days=14), ci=dict(P.real_ci_bodies(), **{"ABSB.N0000": agreeing}))
    late = identity.late_pass(w, wall=clock.wall())           # unresolved: still held, whatever the simulation says
    assert set(held) <= set(late["held"])
    assert q(w, "select count(*) from issuer_identifier_observations where symbol = 'ABSA.N0000'") == [(0,)]
    for h in held:
        owner.resolve_hold_as_owner(env.owner_conn(), h, "keep_held", "owner keeps ABSA held for review")
    late = identity.late_pass(w, wall=clock.wall())           # keep_held: still held
    assert set(held) <= set(late["held"]) and sorted(late["resolutions"]["still_held"]) == held
    assert q(w, "select count(*) from issuer_identifier_observations where symbol = 'ABSA.N0000'") == [(0,)]
    for h in held:
        owner.resolve_hold_as_owner(env.owner_conn(), h, "acquire_evidence", "owner: comparable evidence acquired")
    late = identity.late_pass(w, wall=clock.wall())
    assert late["resolutions"]["recorded"] == 2 and late["resolutions"]["still_held"] == []
    assert not set(held) & set(late["held"])                  # released: no longer reported as held
    assert q(w, "select count(*) from issuer_identifier_observations where symbol = 'ABSA.N0000'") == [(2,)]


def test_acc_an_attempt_proven_not_sent_is_not_an_http_attempt(env, monkeypatch):
    """HB-2 records a journal failure before sending as outcome spool_failed with sent = false: the claim is used,
    no HTTP attempt is counted, the F1 run stays running and the lease is kept for recovery."""
    from worker.backfill_transport import journal
    clock, _ = ready(env)
    w = env.conn()
    it = item(w, "listing:COMB.N0000")
    monkeypatch.setattr(journal, "intent", lambda *a, **k: (_ for _ in ()).throw(journal.SpoolUnavailable("disk")))
    ds, err, rt = one_slice(env, clock, it, script=[])
    monkeypatch.undo()
    assert type(err).__name__ == "DurabilityStop" and ds.lease_kept and rt.transport_double.calls == []
    assert [a["outcome"] for a in store.attempts(w, it["id"])] == ["spool_failed"]
    with DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[])):
        pass
    assert state(w, it) == "pending" and [r[1] for r in f1_runs(w, "COMB.N0000")] == ["running"]
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 0}


# ================================================================================================ F1 equivalence

def _f1_rows(c):
    runs = q(c, "select source_endpoint, request_params, status, failure_category, http_status, rows_returned, "
                "filings_new, observations_new, metadata_changes, rows_rejected, item_failures, details from "
                "report_discovery_runs order by started_at")
    cols = ", ".join(c_ for c_ in f1.NORMALIZED_COLUMNS if c_ != "company_id")
    filings = q(c, f"select cse_filing_id, {cols} from report_filings order by cse_filing_id")
    obs = q(c, "select cse_filing_id, source_endpoint, source_bucket, query_symbol, metadata_hash, raw_item from "
               "report_filing_observations order by cse_filing_id, source_endpoint, source_bucket, metadata_hash")
    return runs, filings, obs


def test_f1_equivalence_with_f1s_own_discover_functions(env, cluster, tmp_path, monkeypatch):
    """HB-3's discovery step persists exactly what F1's discover_feed_window / discover_company_listing persist for
    the same response (the request replaced only)."""
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures", "filings")
    feed = open(os.path.join(fixtures, "real_feed_response_sample.json"), "rb").read()
    listing = open(os.path.join(fixtures, "real_financials_COMB_N0000_trimmed.json"), "rb").read()
    clock, _ = ready(env)
    w = env.conn()
    month = item(w, "feed_window:2025-03")

    def route(url, params):
        return ok(feed if url.endswith("getFinancialAnnouncement") else listing)
    for it in (month, item(w, "listing:COMB.N0000")):
        ds, err, _rt = one_slice(env, clock, it, route=route)
        assert err is None and state(w, it) == "succeeded"
    other = Env(cluster, tmp_path / "f1")
    try:
        other.capture_master()                                # the same security master (F1's company linkage)
        from worker import cse_client
        from worker.report_filings_store import PostgresFilingStore
        c = other.conn()
        resp = {"getFinancialAnnouncement": feed, "financials": listing}
        monkeypatch.setattr(cse_client, "get_financial_announcements", lambda a, b: cse_client.CSEResponse(
            "getFinancialAnnouncement", "POST", {"fromDate": a, "toDate": b}, 200, True, json.loads(resp[
                "getFinancialAnnouncement"])))
        monkeypatch.setattr(cse_client, "get_company_financials", lambda s: cse_client.CSEResponse(
            "financials", "POST", {"symbol": s}, 200, True, json.loads(resp["financials"])))
        f1.discover_feed_window(PostgresFilingStore(c), "2025-03-01", "2025-03-31")
        f1.discover_company_listing(PostgresFilingStore(c), "COMB.N0000")
        assert _f1_rows(w) == _f1_rows(c)
    finally:
        other.close()
    obs_at = q(w, "select distinct o.observed_at = x.observed_at from report_filing_observations o join "
                  "report_discovery_runs r on r.id = o.discovery_run_id join backfill_request_attempts a on "
                  "a.request_params = r.request_params join backfill_request_outcomes x on x.attempt_id = a.id")
    assert obs_at == [(True,)]                                # F1's ingestion time is the response's observed_at


# ================================================================================================ issuer evidence

def _close_discovery(env, clock, bodies, feed_months=None):
    """Run slices until every discovery item is final, answering from `bodies` (listing symbol -> body) and
    `feed_months` ('YYYY-MM' -> body; empty months otherwise)."""
    feed_months = feed_months or {}

    def route(url, params):
        if url.endswith("financials"):
            return ok(bodies[params["symbol"]])
        return ok(feed_months.get(params["fromDate"][:7], FEED_EMPTY))
    w = env.conn()
    for _ in range(10):
        if discovery.closed(w):
            break
        with DiscoverySlice(env.conn(), runtime=runtime(env, clock, route)) as ds:
            ds.run()
    assert discovery.closed(w)


def test_ie2_path_b_equals_the_frozen_path_a(env, cluster, tmp_path):
    """The in-process import of a sweep records exactly what export-company-info + link_issuers record."""
    clock, _ = ready(env)
    env.sweep()
    w = env.conn()
    out = identity.identity_pass(w, wall=clock.wall())
    assert out["held"] == [] and out["observations"] > 0
    other = Env(cluster, tmp_path / "pa")
    try:
        other.capture_master()
        run_id = other.sweep()
        c = other.conn()
        path = str(tmp_path / "company_info.json")
        capture.export_company_info(c, run_id, path)
        from worker import link_issuers
        from worker.issuer_store import PostgresIssuerStore
        link_issuers.run(PostgresIssuerStore(c), c, json_files=[path])
        sql = ("select o.source_endpoint, o.source_field, o.query_symbol, o.symbol, o.cse_security_id, o.cse_sec_id, "
               "o.isin, o.name, o.active, o.payload_sha256, o.observed_at, r.request_key from "
               "issuer_identifier_observations o join market_source_responses r on o.source_ref = "
               "'market_source_responses:' || r.id::text order by 4, 2, 6")
        decisions = ("select c.ticker, s.link_status, s.observed_sec_ids, s.reasons, s.rule_version, s.evidence_sha256 "
                     "from issuer_securities s join companies c on c.id = s.company_id order by 1")
        assert q(w, sql) == q(c, sql) and len(q(w, sql)) > 0
        assert q(w, decisions) == q(c, decisions)
        assert q(w, "select count(*) from issuer_identifier_observations") == \
            q(c, "select count(*) from issuer_identifier_observations")
    finally:
        other.close()


def test_hold_rule_closure_pass_link_pass_and_owner_resolution(env):
    clock, _ = ready(env)
    env.sweep(limit=5)                                        # identity evidence for the five traded securities only
    w = env.conn()
    with pytest.raises(DiscoveryRefused) as ei:
        identity.closure_pass(w, wall=clock.wall())
    assert ei.value.codes == ["order"]
    identity.identity_pass(w, wall=clock.wall())
    with pytest.raises(DiscoveryRefused) as ei:
        identity.closure_pass(w, wall=clock.wall())
    assert ei.value.codes == ["closure"]
    bodies = {
        "COMB.N0000": listing_body([COMB_SEC], [filing(900001, f"cmt/upload_report_file/{COMB_SEC}_{IN_W_MS}.pdf")]),
        "ABSA.N0000": listing_body([COMB_SEC], [filing(900002, "cmt/upload_report_file/annual report.pdf")]),
        "ABSB.N0000": listing_body([77777], [filing(900003, "cmt/upload_report_file/report.pdf")]),
        "JKH.N0000": listing_body([LOLC_SEC]),
        "LOLC.N0000": listing_body([LOLC_SEC]),
        "HNB.N0000": listing_body([373]),
        "SAMP.N0000": listing_body([431]),
    }
    feed = {"2025-03": {"reqFinancialAnnouncemnets": [
        {"id": 900004, "path": f"cmt/upload_report_file/{COMB_SEC}_1740810000000.pdf", "manualDate": None,
         "uploadedDate": "01 Mar 2025 10:00:00 AM", "fileText": "Interim", "name": "COMMERCIAL BANK", "symbol": "COMB",
         "authorizedDate": None}]}}
    _close_discovery(env, clock, bodies, feed)
    out = identity.closure_pass(w, wall=clock.wall())
    holds = q(w, "select h.symbol, h.cse_sec_id, h.source_endpoint, a.item_id, h.dispute from backfill_holds h join "
                 "backfill_request_attempts a on a.id = h.attempt_id")
    # F5 compares identity only across observations carrying the disputed secId: a secId-only sighting has none, so
    # every batch observation of a secId whose NEW dispute is absence-only is held (369: ABSA vs COMB; 378: JKH's
    # listing sighting vs LOLC), never a silent drop; HNB / SAMP list their own secId and are recorded.
    assert sorted((h[0], h[1], h[2]) for h in holds) == [("ABSA.N0000", COMB_SEC, "financials"),
                                                         ("COMB.N0000", COMB_SEC, "financials"),
                                                         ("JKH.N0000", LOLC_SEC, "financials"),
                                                         ("LOLC.N0000", LOLC_SEC, "financials")]
    for h in holds:
        assert str(h[3]) == item(w, f"listing:{h[0]}")["id"]
        assert all(r.startswith("identity_evidence_insufficient:") for fs in h[4]["failures"].values() for r in fs)
    assert q(w, "select count(*) from issuer_identifier_observations where symbol = 'ABSA.N0000'") == [(0,)]
    assert q(w, "select symbol from issuer_identifier_observations where source_endpoint = 'financials' "
                "order by symbol") == [("ABSB.N0000",), ("HNB.N0000",), ("SAMP.N0000",)]
    decisions = dict(q(w, "select distinct on (c.ticker) c.ticker, s.link_status from issuer_securities s join "
                          "companies c on c.id = s.company_id order by c.ticker, s.id desc"))
    assert {t: decisions[t] for t in ("COMB.N0000", "ABSB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000",
                                      "SAMP.N0000")} == dict.fromkeys(("COMB.N0000", "ABSB.N0000", "JKH.N0000",
                                                                       "LOLC.N0000", "HNB.N0000", "SAMP.N0000"),
                                                                      "evidenced")
    assert "ABSA.N0000" not in decisions
    links = dict((fid, (st, basis)) for fid, st, basis in q(w, "select distinct on (cse_filing_id) cse_filing_id, "
                 "status, basis from filing_issuer_links order by cse_filing_id, id desc"))
    assert links[900001] == ("evidenced", "both") and links[900003] == ("evidenced", "listing_symbol_sec_id")
    assert links[900002][0] == "unresolved" and links[900004] == ("evidenced", "document_path_prefix")
    assert out["links"]["admissible"] == 2 and out["links"]["path_prefix_only"] == 1
    assert not identity.admissible({"status": "evidenced", "basis": "document_path_prefix"})
    assert identity.closure_pass(w, wall=clock.wall())["already"]
    hid = dict(q(w, "select symbol, id from backfill_holds"))
    owner.resolve_hold_as_owner(env.owner_conn(), hid["ABSA.N0000"], "record_as_is", "owner accepts the dispute")
    owner.resolve_hold_as_owner(env.owner_conn(), hid["COMB.N0000"], "keep_held", "owner keeps it held for review")
    owner.resolve_hold_as_owner(env.owner_conn(), hid["JKH.N0000"], "acquire_evidence", "owner wants JKH evidence first")
    late = identity.late_pass(w, wall=clock.wall())
    assert late["resolutions"]["recorded"] == 1
    assert sorted(late["resolutions"]["still_held"]) == sorted([hid["COMB.N0000"], hid["JKH.N0000"]])
    assert q(w, "select symbol from issuer_identifier_observations where source_endpoint = 'financials' "
                "order by symbol") == [("ABSA.N0000",), ("ABSB.N0000",), ("HNB.N0000",), ("SAMP.N0000",)]
    decisions = dict(q(w, "select distinct on (c.ticker) c.ticker, s.link_status from issuer_securities s join "
                          "companies c on c.id = s.company_id order by c.ticker, s.id desc"))
    assert decisions["COMB.N0000"] == "conflict"
    assert q(w, "select status from filing_issuer_links where cse_filing_id = 900001 order by id desc limit 1") == \
        [("conflict",)]
    assert q(w, "select count(*) from companies") == [(7,)]       # HB-3 never writes the security master


def test_ie2_only_a_qualifying_sweep_is_imported(env):
    """A sweep counts only after the security master, on a non-trading Colombo day or after that day's P3 capture
    (design section 7.4 step 2)."""
    clock, _ = ready(env)
    w = env.conn()
    state_, _ = env.p2(P2_START + timedelta(hours=1), p2config.sweep_policy(), P2_TD)   # a trading day, no P3 success
    assert state_ == "succeeded"
    with pytest.raises(DiscoveryRefused) as ei:
        identity.identity_pass(w, wall=clock.wall())
    assert ei.value.codes == ["ie2"]
    assert q(w, "select count(*) from issuer_identifier_observations") == [(0,)]
    env.sweep()
    out = identity.identity_pass(w, wall=clock.wall())
    assert len(out["sweeps"]) == 1 and len(out["not_qualifying"]) == 1


def test_ie2_a_sweep_before_the_security_master_does_not_qualify(env):
    env.sweep(start=P2_START - timedelta(days=1))           # a closed-day sweep, but before the derived capture
    env.capture_master()
    arm(env)
    with pytest.raises(DiscoveryRefused) as ei:
        identity.identity_pass(env.conn(), wall=F.FakeClock().wall())
    assert ei.value.codes == ["ie2"]


def test_link_passes_refuse_while_a_slice_is_active(env):
    clock, _ = ready(env)
    env.sweep()
    w = env.conn()
    ds = DiscoverySlice(env.conn(), runtime=runtime(env, clock, script=[]))
    ds.__enter__()
    try:
        with pytest.raises(DiscoveryRefused) as ei:
            identity.identity_pass(w, wall=clock.wall())
        assert ei.value.codes == ["lease"]
    finally:
        ds.close()


def test_hb3_preflight_passes_on_the_frozen_baseline(env):
    from worker.backfill_discovery import preflight
    assert preflight.problems(env.conn()) == []
