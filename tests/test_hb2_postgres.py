"""
Phase 2 HB-2 (the governed transport) against REAL PostgreSQL 17 with migration 0016's real guards: the slice
lifecycle under P2's exclusive lock, intent before send, outcomes written once with their spooled bodies, blocks and
the owner's acknowledgement, P2 blocks, crash recovery from the spool and 'unrecorded', session death, one active
lease, P3 coexistence (busy lock, quiet window, combined-ceiling reservation), Colombo-day budgets, the stopped stage
(A5), the item-claim maximum (A2), disarm between requests, and an end-to-end F2 retrieval against a scripted CDN.

Every CSE response comes from a scripted transport or session: nothing contacts the network.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb2_postgres.py
"""
import os
import shutil
import socket
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb2_fakes as F  # noqa: E402
from worker import document_retrieval as f2, report_discovery as f1  # noqa: E402
from worker.backfill_transport import gates, journal, ledger, preflight  # noqa: E402
from worker.backfill_transport.errors import Blocked, CircuitOpen, DurabilityStop, Refused, SliceBusy, SliceRefused  # noqa: E402
from worker.backfill_transport.slice import Runtime, open_slice  # noqa: E402
from worker.financial_backfill import keys, owner, store  # noqa: E402
from worker.market_capture import config as p2config, runs as p2runs  # noqa: E402
from worker.ops import spool  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
UTC = timezone.utc
COLOMBO = p2config.COLOMBO
LISTING = (f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"})
LISTING_OK = b'{"reqFinancial": [], "infoAnnualData": []}'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """TCP connections fail the test; PostgreSQL is reached over its Unix socket only."""
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
    base = tempfile.mkdtemp(prefix="hb2", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


class Env:
    def __init__(self, cluster, tmp_path):
        self.cluster, self.db, self._open = cluster, S.fresh_db(cluster), []
        self.spool = tmp_path / "spool"
        self.spool.mkdir()
        self.tmp = tmp_path / "tmp"
        self.tmp.mkdir()
        self._owner = None

    def conn(self, user="cse_worker"):
        c = S.conn(self.cluster, self.db, user, False)
        self._open.append(c)
        return c

    def owner_conn(self):
        if self._owner is None:
            self._owner = self.conn("cse_migrator")
        return self._owner

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
    d = dict(armed=True, note="owner arming for the HB-2 tests", armed_stages=("HB-S2", "HB-S4"),
             window_first_date=date(2021, 4, 1), window_last_date=date(2026, 9, 30), daily_request_budget=600,
             combined_daily_ceiling=800, slice_max_json_requests=30, slice_max_documents=10, slice_max_seconds=600,
             attempts_per_json_request=3, attempts_per_document=2, item_max_attempts=3, user_agent=F.USER_AGENT,
             host=F.HOST, version_tuple=gates.running_version_tuple(), expected_requests={"listings": 1},
             stop_conditions=("any block",), g1_reference="G-1 (test)")
    d.update(over)
    return owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision(**d), "tester")


def disarm(env):
    return owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision.disarm("owner disarm in a test"),
                                        "tester")


class PolicyRuntime(Runtime):
    max_failures = None

    def policy(self):
        p = super().policy()
        if self.max_failures is not None:
            import dataclasses
            return dataclasses.replace(p, max_consecutive_failures=self.max_failures)
        return p


def runtime(env, clock, *, script=(), routes=None, wall=None, preflight_fn=None, on_send=None):
    transport = F.FakeTransport(clock, script, on_send=on_send)
    session = F.FakeSession(clock, routes or {})
    rt = PolicyRuntime(wall=wall or (lambda: datetime.now(UTC)), clock=clock.clock, sleep=clock.sleep,
                     hostname=lambda: F.HOST, clock_synchronized=lambda: True,
                     env={"CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT, "CSE_BACKUP_ROOT": str(env.spool.parent)},
                     spool_root=str(env.spool), preflight=preflight_fn, json_transport=transport,
                     session_factory=lambda: session)
    rt.transport_double, rt.session_double = transport, session
    return rt


def ok(body=LISTING_OK):
    return dict(status=200, body=body, headers={"Content-Type": "application/json"})


def listing_item(c, symbol="COMB.N0000"):
    return store.ensure_item(c, keys.listing(symbol))[0]["id"]


# ------------------------------------------------------------------------------------------------ preflight

def test_p1_the_preflight_passes_on_the_frozen_baseline_and_refuses_other_roles(env):
    w = env.conn()
    assert preflight.problems(w, spool_root=str(env.spool)) == []
    assert preflight.database_problems(w) == []
    assert [p for p in preflight.problems(env.conn("cse_backup"), spool_root=str(env.spool))
            if p.startswith("connected as")]
    assert preflight.problems(w, spool_root=str(env.spool / "missing"))


def test_p2_no_arming_no_request(env):
    w = env.conn()
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[ok()])
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=rt)
    assert ei.value.codes == ["disarmed"]
    arm(env)
    disarm(env)
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=rt)
    assert ei.value.codes == ["disarmed"] and rt.transport_double.calls == []
    assert q(w, "select count(*) from backfill_leases") == [(0,)]
    rt_bad = runtime(env, clock, script=[ok()])
    rt_bad.hostname = lambda: "elsewhere"
    arm(env)
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=rt_bad)
    assert ei.value.codes == ["host"]


# ------------------------------------------------------------------------------------------------ requests

def test_r1_intent_before_send_outcome_once_with_its_spooled_body(env):
    w, watch = env.conn(), env.conn()
    arm(env)
    item = listing_item(w)
    clock = F.FakeClock()
    seen = []

    def on_send(n):
        seen.append(q(watch, "select a.attempt_no, o.attempt_id is null from backfill_request_attempts a left join "
                             "backfill_request_outcomes o on o.attempt_id = a.id order by a.id"))
    rt = runtime(env, clock, script=[ok()], on_send=on_send)
    with open_slice(w, stage="HB-S2", kind="json", runtime=rt) as sl:
        sl.claim(item)
        res = sl.json_request(item, *LISTING)
        lease = sl.lease_id
        jentries = [e["event"] for e in sl.journal.entries()]
    assert seen == [[(1, True)]]                                   # committed intent, no outcome, before the send
    assert clock.sleeps == [1.5]                                   # the release guard: a full interval before release
    assert res.ok and jentries == ["intent", "spooled"]
    row = q(w, "select o.outcome, o.outcome_class, o.http_status, o.body_sha256, o.spool_body_key, o.spool_record_key, "
               "o.parse_status, o.recovered_from_spool, b.body_bytes, decode(b.body_base64, 'base64') from "
               "backfill_request_outcomes o join backfill_response_bodies b using (body_sha256)")[0]
    assert row[:3] == ("ok", "ok", 200) and row[6:9] == ("json_ok", False, len(LISTING_OK))
    assert bytes(row[9]) == LISTING_OK and spool.read(str(env.spool), row[4]) == LISTING_OK
    assert journal.load_record(str(env.spool), row[5], res.attempts[0].attempt_id)[1] == LISTING_OK
    with pytest.raises(Exception):                                 # an outcome is written once (primary key)
        ledger.record_outcome(w, res.attempts[0].attempt_id, {"outcome": "timeout", "outcome_class": "retryable"})
    assert q(w, "select state, result, details ->> 'stage' from backfill_leases where id = %s", (lease,)) == \
        [("released", "completed", "HB-S2")]
    a = q(w, "select request_class, request_host, endpoint, http_method, url, user_agent, request_params, "
             "request_headers from backfill_request_attempts")[0]
    assert a == ("json", "www.cse.lk", "financials", "POST", "https://www.cse.lk/api/financials", F.USER_AGENT,
                 {"symbol": "COMB.N0000"}, {"user-agent": F.USER_AGENT, "accept": "application/json"})
    assert p2runs.acquire_global_lock(env.conn())                  # released


def test_r2_a_block_stops_everything_until_the_owner_acknowledges_it(env):
    w = env.conn()
    arm(env)
    item = listing_item(w)
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[dict(status=403, body=b"{}")])
    with pytest.raises(Blocked) as ei:
        with open_slice(w, stage="HB-S2", kind="json", runtime=rt) as sl:
            sl.claim(item)
            try:
                sl.json_request(item, *LISTING)
            except Blocked as exc:
                store.append_event(w, item, "blocked", "record", lease_id=sl.lease_id, block_id=exc.block_id)
                raise
    bid = ei.value.block_id
    assert q(w, "select b.id, o.outcome_class, o.http_status from backfill_blocks b join backfill_request_outcomes o "
                "using (attempt_id)") == [(bid, "block", 403)]
    assert q(w, "select result from backfill_leases") == [("blocked",)]
    rt2 = runtime(env, clock, script=[ok()])
    with pytest.raises(SliceRefused) as e2:
        open_slice(w, stage="HB-S2", kind="json", runtime=rt2)
    assert "blocked" in e2.value.codes and rt2.transport_double.calls == []
    owner.acknowledge_block_as_owner(env.owner_conn(), bid, "owner reviewed the CSE block", operator="tester")
    store.append_event(w, item, "pending", "resume")
    with open_slice(w, stage="HB-S2", kind="json", runtime=rt2) as sl:
        sl.claim(item)
        assert sl.json_request(item, *LISTING).ok


def test_r3_an_unacknowledged_p2_block_refuses_phase2(env):
    w = env.conn()
    arm(env)
    run = p2runs.create_run(w, run_kind="market_capture", trading_date=date(2026, 9, 30), capture_mode="post_close",
                            policy={}, user_agent=F.USER_AGENT, tool_version="test", code_revision=None)
    p2runs.append_event(w, run, "running")
    p2runs.append_event(w, run, "blocked", "HTTP 403")
    clock = F.FakeClock()
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert ei.value.codes == ["blocked"]
    p2runs.acknowledge_block_as_owner(env.owner_conn(), run, "owner reviewed the P2 block")
    open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock)).close()


def test_r4_disarm_between_requests_prevents_the_next(env):
    w = env.conn()
    arm(env)
    a, b = listing_item(w, "COMB.N0000"), listing_item(w, "LOLC.N0000")
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[ok(), ok()])
    with open_slice(w, stage="HB-S2", kind="json", runtime=rt) as sl:
        sl.claim(a)
        sl.json_request(a, *LISTING)
        disarm(env)
        with pytest.raises(Refused) as ei:
            sl.claim(b)
        assert ei.value.codes == ["disarmed"]
    assert len(rt.transport_double.calls) == 1
    assert q(w, "select result from backfill_leases") == [("refused",)]


# ------------------------------------------------------------------------------------------------ crash recovery

def test_c1_a_spooled_response_is_recovered_without_another_request(env, monkeypatch):
    a_conn, b_conn = env.conn(), env.conn()
    arm(env)
    item = listing_item(a_conn)
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[ok()])
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=rt)
    sl.claim(item)
    real = ledger.record_outcome
    monkeypatch.setattr(ledger, "record_outcome", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    with pytest.raises(DurabilityStop):
        sl.json_request(item, *LISTING)
    monkeypatch.setattr(ledger, "record_outcome", real)
    sl.close(sl.stop)                                             # the lease is left active for recovery
    lease = sl.lease_id
    assert q(b_conn, "select state from backfill_leases where id = %s", (lease,)) == [("active",)]
    rt2 = runtime(env, clock)                                     # no script: any request would fail the test
    with open_slice(b_conn, stage="HB-S2", kind="json", runtime=rt2) as sl2:
        rec = sl2.recovered["leases"]
    assert [(r["lease_id"], len(r["recovered_from_spool"]), r["unrecorded"]) for r in rec] == [(lease, 1, [])]
    row = q(b_conn, "select o.outcome, o.recovered_from_spool, o.body_sha256 is not null, o.spool_record_key is not "
                    "null from backfill_request_outcomes o")
    assert row == [("ok", True, True, True)]
    assert store.current_state(b_conn, item)["state"] == "abandoned" and rt2.transport_double.calls == []
    assert q(b_conn, "select state, expired_by_wakeup is not null from backfill_leases where id = %s", (lease,)) == \
        [("expired", True)]


def test_c2_a_slice_that_dies_mid_request_is_closed_unrecorded_after_session_death(env):
    a_conn, b_conn = env.conn(), env.conn()
    arm(env)
    item = listing_item(a_conn)
    clock = F.FakeClock()
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    sl.claim(item)
    ledger.record_intent(a_conn, item, sl.lease_id, sl.wakeup_id, request_class="json", request_host="www.cse.lk",
                         endpoint="financials", url="https://www.cse.lk/api/financials", user_agent=F.USER_AGENT,
                         params={"symbol": "COMB.N0000"}, headers={})
    pid, started = q(a_conn, "select backend_pid, backend_started_at from backfill_wakeups where id = %s",
                     (sl.wakeup_id,))[0]
    a_conn.close()                                               # the process dies mid-request
    end = time.monotonic() + 15
    while q(b_conn, "select hb_session_alive(%s, %s)", (pid, started))[0][0]:
        assert time.monotonic() < end
        time.sleep(0.05)
    clock2 = F.FakeClock()
    rt2 = runtime(env, clock2, script=[ok()])
    with open_slice(b_conn, stage="HB-S2", kind="json", runtime=rt2) as sl2:
        assert [r["unrecorded"] for r in sl2.recovered["leases"]] == [[1]]
        assert sl2.recovered["wakeups"]
        store.append_event(b_conn, item, "pending", "promote", reason="no F1 run finished")
        sl2.claim(item)
        sl2.json_request(item, *LISTING)
        first_waits = list(clock2.sleeps)
    # a dead predecessor forces a full interval from when its death was recorded, before the FIRST request (the
    # slice's own start-up time counts towards it; the exact rule is unit-tested)
    assert first_waits and first_waits[0] > 0.5
    assert q(b_conn, "select attempt_no, o.outcome from backfill_request_attempts a join backfill_request_outcomes o "
                     "on o.attempt_id = a.id order by attempt_no") == [(1, "unrecorded"), (2, "ok")]
    assert ledger.claims_since_requeue(b_conn, item) == 2       # A2: the item's claims, not its HTTP attempts


def test_c3_recovery_is_idempotent_and_refuses_a_corrupt_spool_record(env, monkeypatch):
    a_conn, b_conn = env.conn(), env.conn()
    arm(env)
    item = listing_item(a_conn)
    clock = F.FakeClock()
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock, script=[ok()]))
    sl.claim(item)
    real = ledger.record_outcome
    monkeypatch.setattr(ledger, "record_outcome", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    with pytest.raises(DurabilityStop):
        sl.json_request(item, *LISTING)
    monkeypatch.setattr(ledger, "record_outcome", real)
    sl.close(sl.stop)
    key = sl.journal.spooled()[1]
    path = os.path.join(str(env.spool), *key.split("/"))
    os.chmod(path, 0o640)
    with open(path, "ab") as f:
        f.write(b" ")
    from worker.backfill_transport.recovery import RecoveryError
    with pytest.raises(RecoveryError, match="does not match"):
        open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert q(b_conn, "select state from backfill_leases where id = %s", (sl.lease_id,)) == [("active",)]
    assert q(b_conn, "select count(*) from backfill_request_outcomes") == [(0,)]          # nothing was guessed
    assert p2runs.acquire_global_lock(env.conn())                                       # and the lock was released


def test_c4_a_spooled_block_is_recovered_as_a_block(env, monkeypatch):
    a_conn, b_conn = env.conn(), env.conn()
    arm(env)
    item = listing_item(a_conn)
    clock = F.FakeClock()
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock,
                                                                        script=[dict(status=403, body=b"{}")]))
    sl.claim(item)
    real = ledger.record_outcome
    monkeypatch.setattr(ledger, "record_outcome", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    with pytest.raises(DurabilityStop):
        sl.json_request(item, *LISTING)
    monkeypatch.setattr(ledger, "record_outcome", real)
    sl.close(sl.stop)
    with pytest.raises(SliceRefused) as ei:                       # recovered, then refused under the lock
        open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert ei.value.codes == ["blocked"]
    assert q(b_conn, "select o.outcome_class, o.recovered_from_spool, o.http_status, b.reason like 'CSE refused%%' "
                     "from backfill_blocks b join backfill_request_outcomes o using (attempt_id)") == \
        [("block", True, 403, True)]
    assert store.current_state(b_conn, item)["state"] == "abandoned"


# ------------------------------------------------------------------------------------------------ locks and P3

def test_l1_one_slice_at_a_time_p3_and_shared_holders_are_never_taken_over(env):
    a_conn, b_conn, p3_conn = env.conn(), env.conn(), env.conn()
    arm(env)
    clock = F.FakeClock()
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    with pytest.raises(SliceBusy):
        open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert q(b_conn, "select state, result from backfill_wakeups where state = 'skipped'") == [("skipped", "busy")]
    assert q(b_conn, "select count(*) from backfill_leases where state = 'active'") == [(1,)]
    sl.close()
    assert p2runs.acquire_global_lock(p3_conn)                  # P3 (or P2) holds the lock: the slice waits its turn
    with pytest.raises(SliceBusy):
        open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    p2runs.release_global_lock(p3_conn)
    assert q(p3_conn, "select pg_try_advisory_lock_shared(%s)", (p2runs.GLOBAL_LOCK_KEY,)) == [(True,)]
    with pytest.raises(SliceBusy):                              # a shared holder blocks the exclusive acquire
        open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    q(p3_conn, "select pg_advisory_unlock_shared(%s)", (p2runs.GLOBAL_LOCK_KEY,))
    open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock)).close()


def test_l5_a_shared_hold_is_never_a_slice_and_recovery_needs_the_lock(env, monkeypatch):
    w = env.conn()
    arm(env)
    clock = F.FakeClock()
    from worker.backfill_transport import recovery, slice as tslice
    monkeypatch.setattr(tslice.p2runs, "acquire_global_lock", lambda c: q(
        c, "select pg_try_advisory_lock_shared(%s)", (p2runs.GLOBAL_LOCK_KEY,))[0][0])
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert ei.value.codes == ["lock"] and q(w, "select count(*) from backfill_leases") == [(0,)]
    q(w, "select pg_advisory_unlock_all()")
    with pytest.raises(recovery.RecoveryError):                  # expiry only under P2's exclusive lock
        recovery.expire_dead(env.conn(), 1, str(env.spool))


def test_l6_open_and_dead_attempts_count_as_ending_when_last_seen(env):
    """Throttle seeding is conservative: an attempt without an outcome counts as ending NOW, and one closed
    'unrecorded' as ending when its slice was found dead - never at its (older) intent time."""
    a_conn, b_conn, watch = env.conn(), env.conn(), env.conn()
    arm(env)
    item = listing_item(a_conn)
    clock = F.FakeClock()
    sl = open_slice(a_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    sl.claim(item)
    ledger.record_intent(a_conn, item, sl.lease_id, sl.wakeup_id, request_class="json", request_host="www.cse.lk",
                         endpoint="financials", url="https://www.cse.lk/api/financials", user_agent=F.USER_AGENT,
                         params={"symbol": "COMB.N0000"}, headers={})
    time.sleep(2.2)
    assert ledger.seconds_since_last_ledger_request(watch) < 1.0          # in flight: it ends now
    pid, started = q(a_conn, "select backend_pid, backend_started_at from backfill_wakeups where id = %s",
                     (sl.wakeup_id,))[0]
    a_conn.close()
    end = time.monotonic() + 15
    while q(watch, "select hb_session_alive(%s, %s)", (pid, started))[0][0]:
        assert time.monotonic() < end
        time.sleep(0.05)
    sl2 = open_slice(b_conn, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert ledger.seconds_since_last_ledger_request(watch) < 1.0          # found dead now, not 2 s ago
    sl2.close()


def _arm_p3(env, budget=150):
    from worker.scheduler import schedule as sched, store as p3store
    s = sched.ScheduleSettings(armed=True, start_date=date(2026, 9, 1), user_agent=F.USER_AGENT, host=F.HOST,
                               expected_requests="about 60", stop_conditions=sched.STOP_CONDITIONS,
                               daily_request_budget=budget, note="owner arms P3 for a test")
    p3store.record_settings_as_owner(env.owner_conn(), s, "tester")


def test_l2_the_quiet_window_and_the_p3_reservation_from_p3s_own_state(env):
    w = env.conn()
    arm(env)
    _arm_p3(env, budget=150)
    fri = date(2026, 10, 2)
    at = datetime.combine(fri, dtime(15, 10), tzinfo=COLOMBO).astimezone(UTC)
    p3 = ledger.p3_view(w, fri)
    assert (p3.armed, p3.earliest_start_local, p3.daily_request_budget, p3.item_state) == \
        (True, dtime(15, 15), 150, None)
    assert gates.quiet_window(at, p3, 600)
    clock = F.FakeClock()
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock, wall=lambda: at))
    assert ei.value.codes == ["quiet_window"]
    b = ledger.budget_view(w, fri, store.arming_in_force(w), p3)
    assert b.p3_reserve == 150 and b.combined_remaining == 800 - 150
    from worker.scheduler import store as p3store
    p3store.declare_closed(w, fri, "CSE notice (test)")           # a closed day: no P3 capture, no quiet window
    p3 = ledger.p3_view(w, fri)
    assert p3.closed_today and not gates.quiet_window(at, p3, 600) and gates.p3_reserve(p3, fri) == 0
    open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock, wall=lambda: at)).close()


def test_l3_colombo_day_budgets_count_both_archives(env):
    w = env.conn()
    arm(env, daily_request_budget=2, combined_daily_ceiling=10)
    a, b, c = (listing_item(w, s) for s in ("A.N0000", "B.N0000", "C.N0000"))
    clock = F.FakeClock()
    rt = runtime(env, clock, script=[ok(), ok()])
    with open_slice(w, stage="HB-S2", kind="json", runtime=rt) as sl:
        for item, sym in ((a, "A.N0000"), (b, "B.N0000")):
            sl.claim(item)
            sl.json_request(item, f1.LISTING_ENDPOINT, {"symbol": sym})
        with pytest.raises(Refused) as ei:
            sl.claim(c)
        assert ei.value.codes == ["budget"]
    today = keys.colombo_date(datetime.now(UTC))
    assert ledger.budget_view(w, today, store.arming_in_force(w), gates.P3View()).phase2_requests == 2
    assert ledger.budget_view(w, today - timedelta(days=1), store.arming_in_force(w), gates.P3View()) \
        .phase2_requests == 0
    with pytest.raises(SliceRefused) as e2:
        open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert e2.value.codes == ["budget"]


def test_l4_seeding_reads_both_archives(env):
    w = env.conn()
    assert ledger.seconds_since_last_ledger_request(w) is None
    arm(env)
    item = listing_item(w)
    clock = F.FakeClock()
    with open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock, script=[ok()])) as sl:
        sl.claim(item)
        sl.json_request(item, *LISTING)
    since = ledger.seconds_since_last_ledger_request(w)
    assert since is not None and since < 60
    run = p2runs.create_run(w, run_kind="metadata_sweep", trading_date=date(2026, 9, 30),
                            capture_mode="metadata_sweep", policy={}, user_agent=F.USER_AGENT, tool_version="t",
                            code_revision=None)
    q(w, "insert into market_source_responses (run_id, request_key, request_purpose, sequence_no, attempt_no, "
         "trading_date, capture_mode, endpoint, http_method, url, user_agent, requested_at, outcome) values (%s, "
         "'allSecurityCode', 'metadata_sweep', 1, 1, '2026-09-30', 'metadata_sweep', 'allSecurityCode', 'GET', "
         "'https://www.cse.lk/api/allSecurityCode', 'ua', now(), 'network_error')", (run,))
    from worker.backfill_transport import throttle
    assert throttle.seed_seconds(w, datetime.now(UTC)) < 5


# ------------------------------------------------------------------------------------------------ A2 and A5

def test_a2_an_item_is_claimed_at_most_item_max_times_since_its_requeue(env):
    w = env.conn()
    arm(env, item_max_attempts=2)
    item = listing_item(w)
    clock = F.FakeClock()
    for _ in range(2):
        with open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock)) as sl:
            sl.claim(item)
            store.append_event(w, item, "retry_wait", "record", lease_id=sl.lease_id, reason="ReadTimeout")
    with open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock)) as sl:
        with pytest.raises(Refused) as ei:
            sl.claim(item)
        assert ei.value.codes == ["item_max"]
        store.append_event(w, item, "failed", "record", reason="item maximum of 2 claims reached")
    store.append_event(w, item, "pending", "requeue", reason="operator: retry once more after review")
    assert ledger.claims_since_requeue(w, item) == 0
    with open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock)) as sl:
        sl.claim(item)


def test_a5_three_circuit_open_slices_stop_the_stage_until_a_newer_arming(env):
    w = env.conn()
    arm(env)
    items = [listing_item(w, f"S{i}.N0000") for i in range(4)]
    clock = F.FakeClock()
    for i in range(3):
        rt = runtime(env, clock, script=[dict(status=503, body=b"x")])
        rt.max_failures = 1
        with pytest.raises(CircuitOpen):
            with open_slice(w, stage="HB-S2", kind="json", runtime=rt) as sl:
                sl.claim(items[i])
                sl.json_request(items[i], f1.LISTING_ENDPOINT, {"symbol": f"S{i}.N0000"})
    assert [r[0] for r in q(w, "select result from backfill_leases order by id")] == ["circuit_open"] * 3
    with pytest.raises(SliceRefused) as ei:
        open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock))
    assert ei.value.codes == ["stage_stopped"]
    open_slice(w, stage="HB-S4", kind="document", runtime=runtime(env, clock)).close()   # only that stage stops
    arm(env)                                                     # a newer owner arming decision resumes the stage
    open_slice(w, stage="HB-S2", kind="json", runtime=runtime(env, clock)).close()


# ------------------------------------------------------------------------------------------------ F2 end to end

PATH = "upload_report_file/771_1653995188923.pdf"
PDF = b"%PDF-1.7\n" + b"1" * 150_000 + b"\n%%EOF\n"


def test_d1_end_to_end_fake_cdn_retrieval_with_its_l6_record(env, monkeypatch):
    import hashlib
    w = env.conn()
    arm(env)
    with w.cursor() as cur:
        S.ensure_filing(cur, 771, datetime(2024, 5, 1, tzinfo=UTC))
    w.commit()
    item = store.ensure_item(w, keys.document(771, PATH))[0]["id"]
    store.append_event(w, item, "pending", "promote")
    routes = {f2.CDN_BASE + PATH: (403, {}, b"<Error><Code>AccessDenied</Code></Error>"),
              f2.CDN_BASE + "cmt/" + PATH: (200, {"Content-Type": "application/pdf",
                                                   "Content-Length": str(len(PDF)),
                                                   "ETag": '"' + hashlib.md5(PDF).hexdigest() + '"'}, PDF)}
    monkeypatch.setattr(f2.tempfile, "gettempdir", lambda: str(env.tmp))
    clock = F.FakeClock()
    seen = {}
    with open_slice(w, stage="HB-S4", kind="document", runtime=runtime(env, clock, routes=routes)) as sl:
        sl.claim(item)
        fe = sl.document_fetcher(item)
        rec = f2.process_filing({"cse_filing_id": 771, "path": PATH}, lambda d: seen.update(path=d.path),
                                fetcher=fe, temp_root=str(env.tmp))
        rid = store.record_retrieval(w, item, sl.lease_id, rec.to_dict(), attempt_ids=fe.attempt_ids,
                                     temp_roots=(str(env.tmp),))
        store.append_event(w, item, "processing", "record", lease_id=sl.lease_id, retrieval_id=rid)
    assert rec.outcome == "succeeded" and rec.cleanup_status == "deleted" and not os.path.exists(seen["path"])
    assert os.listdir(env.tmp) == []
    assert q(w, "select a.attempt_no, a.endpoint, a.request_class, o.outcome, o.outcome_class, o.http_status, "
                "o.body_sha256 is null from backfill_request_attempts a join backfill_request_outcomes o on "
                "o.attempt_id = a.id order by a.attempt_no") == [
        (1, "cdn", "document", "forbidden_or_missing", "terminal", 403, True), (2, "cdn", "document", "ok", "ok", 200,
                                                                               True)]
    assert q(w, "select attempt_ids, outcome, cleanup_status, document_sha256 from backfill_retrieval_records") == \
        [(fe.attempt_ids, "succeeded", "deleted", hashlib.sha256(PDF).hexdigest())]
    leaked = q(w, "select count(*) from (select row_to_json(r)::text t from backfill_retrieval_records r union all "
                  "select row_to_json(o)::text from backfill_request_outcomes o union all select "
                  "row_to_json(a)::text from backfill_request_attempts a union all select row_to_json(e)::text from "
                  "backfill_item_events e) x where t like %s or t like %s", ("%cse_f2_%", f"%{env.tmp}%"))
    assert leaked == [(0,)]                                         # no temporary path anywhere in the ledger
    assert q(w, "select count(*) from backfill_response_bodies") == [(0,)]       # documents never archived
    assert not os.path.exists(os.path.join(str(env.spool), "blobs"))            # nor spooled
