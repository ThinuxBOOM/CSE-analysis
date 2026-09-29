"""
P3 scheduler against REAL PostgreSQL 17 (a throwaway initdb cluster; roles from the P1 bootstrap; migrations
0001-0014 through the P1 runner into a template, and a FRESH database cloned from it for every test, so scheduler
state never leaks between tests) with P2's code unchanged and a FAKE CSE transport built from the real fixtures that
serves the latest session as of a FAKE clock: no network, no CSE, no real sleeping.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p3_scheduler_postgres.py

Covers: schedule creation, duplicate wake-ups / timers / dates, successful-date idempotency, missed dates, weekends,
declared and undeclared holidays, multi-day downtime and deterministic catch-up, retryable failures, partial,
blocked and abandoned captures, stale leases and heartbeats, two schedulers, scheduler + manual capture, restart
during work, the role boundaries (arming is owner-only), P2 invocation with an explicit Colombo date (never UTC),
G-1 controls and the daily budget, backup independence, and the frozen D-2 defect preserved (canonicalisation
failure recorded, never promoted, archive intact).
"""
import json
import os
import socket
import sys
import threading
import uuid
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from p2_fakes import ABSENT, TRADED, FakeClock, R  # noqa: E402
from p3_fakes import FRI, MON, NEXT_MON, SAT, SUN, THU, TUE, WED, SessionCSE, colombo, set_wall, weekdays  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
EMAIL = "p3-tests@example.org"
HOST = "cse-prod-test"
PLAN_CALLS = 9                  # allSecurityCode + tradeSummary + 2 absent fallbacks + 5 cross-checks (the fixtures)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    from worker.ops.ephemeral_pg import EphemeralCluster
    base = tmp_path_factory.mktemp("p3_cluster")
    ec = EphemeralCluster(BINDIR, base_dir=str(base), port=free_port(), durable=True, superuser="postgres").start()
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse_tpl", "-f", BOOTSTRAP)
    c = ec.connect(dbname="cse_tpl", user="cse_migrator")
    mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()
    yield ec
    ec.cleanup()


@pytest.fixture
def dbname(cluster):
    """A fresh, fully migrated database for this test (a copy of the template, with P1's database-level grants)."""
    name = f"p3_{uuid.uuid4().hex[:12]}"
    su = cluster.connect(dbname="postgres", user="postgres")
    su.autocommit = True
    with su.cursor() as cur:
        cur.execute(f"create database {name} template cse_tpl owner cse_owner")
        cur.execute(f"revoke all on database {name} from public")
        cur.execute(f"grant connect on database {name} to cse_migrator, cse_worker, cse_reader, cse_backup")
    su.close()
    return name


def conn(cluster, db, user="cse_worker", autocommit=False):
    c = cluster.connect(dbname=db, user=user)
    c.autocommit = autocommit
    return c


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
    c.commit()
    return rows


class Env:
    """One scheduler process: its three worker connections, a fake clock, a session-aware fake CSE, and a P3 Runtime
    whose clock-sync probe says 'synchronised' and whose host is HOST."""

    def __init__(self, cluster, db, root, *, sessions=None, script=None, universe=None, host=HOST, email=EMAIL,
                 clock=None):
        from worker.market_capture import config as cfgmod
        from worker.scheduler import wakeup
        self.cluster, self.db, self.root = cluster, db, root
        env = {"CSE_DB_NAME": db, "CSE_DB_HOST": cluster.sockdir, "CSE_DB_PORT": str(cluster.port),
               "CSE_DB_USER": "cse_worker", "CSE_BACKUP_ROOT": str(root), "CSE_PG_BINDIR": BINDIR,
               "CSE_CAPTURE_CONTACT_EMAIL": email}
        self.env = env
        os.makedirs(os.path.join(str(root), "spool"), exist_ok=True)
        os.makedirs(os.path.join(str(root), "state"), exist_ok=True)
        self.cfg = cfgmod.load(env)
        self.clock = clock or FakeClock()
        self.cse = SessionCSE(self.clock, sessions=sessions, script=script, universe=universe)
        self.logs = []
        self.synced = True
        self.marker = os.path.join(str(root), "state", "capture-finished")
        self.rt = wakeup.Runtime(transport=self.cse, clock=self.clock.monotonic, wall=self.clock.wall,
                                 sleep=self.clock.sleep, log=self.logs.append, clock_synchronized=lambda: self.synced,
                                 host=lambda: host, pid=lambda: 4242, boot_id=lambda: "boot-test",
                                 marker_path=self.marker, heartbeat_every_seconds=0)
        self.ctl, self.work, self.hb = (conn(cluster, db) for _ in range(3))

    def at(self, when):
        set_wall(self.clock, when)
        return self

    def wake(self, **kw):
        from worker.scheduler import wakeup
        return wakeup.wake(self.ctl, self.work, self.hb, self.cfg, self.rt, **kw)

    def arm(self, start, **values):
        from worker.scheduler import schedule as sched, store
        owner = conn(self.cluster, self.db, "cse_migrator")
        s = sched.ScheduleSettings(armed=True, start_date=start, user_agent=self.cfg.user_agent, host=HOST,
                                   expected_requests="55-65", stop_conditions=sched.STOP_CONDITIONS,
                                   note="test: owner arming decision", **values).validate()
        sid = store.record_settings_as_owner(owner, s, "tester")
        owner.close()
        return sid

    def item(self, day):
        from worker.scheduler import store
        return store.item_for_date(self.hb, day)

    def state(self, day):
        r = q(self.hb, "select state, action, run_id from market_schedule_item_state where trading_date = %s", (day,))
        return r[0] if r else None

    def events(self, day):
        from worker.scheduler import store
        return store.item_events(self.hb, self.item(day)["id"])

    def runs(self, day):
        return q(self.hb, "select r.id::text, s.state, r.trading_date_basis from market_capture_runs r join "
                          "market_capture_run_state s on s.run_id = r.id where r.trading_date = %s order by "
                          "r.created_at", (day,))

    def close(self):
        for c in (self.ctl, self.work, self.hb):
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
def env(cluster, dbname, tmp_path):
    made = []

    def make(**kw):
        e = Env(cluster, dbname, tmp_path / f"e{len(made)}" / "backup", **kw)
        made.append(e)
        return e
    yield make
    for e in made:
        e.close()


# ------------------------------------------------------------------------------------------------ arming / creation

def test_disarmed_scheduler_contacts_nobody_and_owns_no_dates(env):
    e = env()
    for when in (colombo(MON, 10), colombo(MON, 16), colombo(TUE, 9)):
        code, rep = e.at(when).wake()
        assert (code, rep["result"]) == (0, "disarmed")
    assert e.cse.calls == []
    assert q(e.hb, "select count(*) from market_schedule_items") == [(0,)]
    assert q(e.hb, "select count(*) from market_capture_runs") == [(0,)]
    assert q(e.hb, "select state, result, trigger_kind from market_schedule_wakeups order by id") == \
        [("released", "disarmed", "timer")] * 3


def test_initial_schedule_creation_then_on_time_capture_through_p2(env):
    e = env()
    e.arm(MON)
    code, rep = e.at(colombo(MON, 10)).wake()                      # before the due time: the item exists, waits
    assert code == 0 and rep["discovered"] == [{"trading_date": str(MON), "origin": "scheduled"}]
    assert e.cse.calls == [] and e.state(MON)[0] == "pending"
    item = e.item(MON)
    assert item["due_at"] == colombo(MON, 15, 15) and item["window_closes_at"] == colombo(MON, 23, 59, 59)
    assert item["origin"] == "scheduled" and item["schedule"]["max_attempts"] == 4
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert code == 0 and rep["result"] == "captured", json.dumps(rep, default=str)[:3000]
    keys = e.cse.keys()
    assert keys[:2] == ["allSecurityCode", "tradeSummary"] and len(keys) == PLAN_CALLS    # the P2 plan, unchanged
    assert sorted(keys[2:4]) == [f"companyInfoSummery:{s}" for s in sorted(ABSENT)]
    (run_id, run_state, basis), = e.runs(MON)
    assert (run_state, basis) == ("succeeded", "scheduler")
    assert q(e.hb, "select trading_date, capture_mode, run_kind from market_capture_runs where id = %s",
             (run_id,)) == [(MON, "post_close", "market_capture")]
    assert e.state(MON) == ("succeeded", "start", run_id)
    ev = e.events(MON)
    assert [x["state"] for x in ev] == ["pending", "running", "succeeded"]
    assert ev[-1]["details"]["completeness"]["C"]["status"] == "complete"
    assert q(e.hb, "select market_status, established_by from trading_calendar where trade_date = %s", (MON,)) == \
        [("open", "live_capture")]
    with open(e.marker, encoding="utf-8") as f:
        assert json.load(f)["run_id"] == run_id                   # backups became eligible (asynchronously)
    assert q(e.hb, "select count(*) from raw_market_observations where request_attempt_id = %s", (run_id,)) == [(7,)]


def test_duplicate_wakeups_and_timer_firings_never_duplicate_work(env):
    e = env()
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    calls = len(e.cse.calls)
    for when in (colombo(MON, 15, 20), colombo(MON, 15, 35), colombo(MON, 16), colombo(MON, 23)):
        code, rep = e.at(when).wake()
        assert code == 0 and rep["result"] == "idle"
    assert len(e.cse.calls) == calls
    assert len(e.runs(MON)) == 1 and q(e.hb, "select count(*) from market_schedule_items") == [(1,)]
    assert q(e.hb, "select count(*) from market_schedule_item_events") == [(3,)]


def test_duplicate_logical_trading_date_creation_is_impossible(env, cluster, dbname):
    import psycopg2
    from worker.scheduler import schedule as sched, store, wakeup
    e = env()
    e.arm(MON)
    snap = sched.DISARMED.schedule_snapshot()
    barrier, out = threading.Barrier(4), []

    def create():
        c = conn(cluster, dbname)
        barrier.wait()
        out.append(store.create_item(c, trading_date=TUE, due_at=sched.due_at(TUE, snap),
                                     window_closes_at=sched.window_closes_at(TUE, snap), discovered_at=colombo(TUE, 9),
                                     origin="scheduled", reason=None, settings_id=None, schedule=snap, wakeup_id=None,
                                     scheduler_time=colombo(TUE, 9))[1])
        c.close()
    threads = [threading.Thread(target=create) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out) == [False, False, False, True]              # exactly one creation, three found it
    assert q(e.hb, "select count(*) from market_schedule_items where trading_date = %s", (TUE,)) == [(1,)]
    assert [x["state"] for x in e.events(TUE)] == ["pending"]
    e.at(colombo(TUE, 9, 30))
    again = wakeup.add_item(e.ctl, e.hb, e.cfg, TUE, "operator re-adds an existing date", e.rt)
    assert again["created"] is False and again["item"]["id"] == e.item(TUE)["id"]
    with pytest.raises(psycopg2.errors.UniqueViolation):
        q(e.hb, "insert into market_schedule_items (work_kind, trading_date, capture_mode, due_at, window_closes_at, "
                "discovered_at, origin, schedule) values ('daily_post_close', %s, 'post_close', now(), "
                "now() + interval '1 hour', now(), 'scheduled', '{}')", (TUE,))


def test_missed_date_is_recorded_without_contacting_cse(env):
    e = env()
    e.arm(MON)
    code, rep = e.at(colombo(TUE, 8)).wake()                        # the server was off all Monday
    assert code == 2                                                # a missed date needs the operator's attention
    assert e.cse.calls == []
    assert rep["discovered"] == [{"trading_date": str(MON), "origin": "catch_up"},
                                 {"trading_date": str(TUE), "origin": "scheduled"}]
    st, action, run_id = e.state(MON)
    assert (st, action) == ("missed", "record_missed")
    assert q(e.hb, "select trading_date_basis, user_agent, policy ->> 'name' from market_capture_runs where id = %s",
             (run_id,)) == [("scheduler", None, "missed_record")]
    assert q(e.hb, "select count(*) from market_source_responses") == [(0,)]
    assert e.state(TUE)[0] == "pending"
    assert q(e.hb, "select count(*) from trading_calendar") == [(0,)]           # a miss proves nothing about the market


def test_weekends_are_never_trading_dates(env):
    e = env()
    e.arm(FRI)
    e.at(colombo(FRI, 15, 30)).wake()
    assert e.state(FRI)[0] == "succeeded"
    calls = len(e.cse.calls)
    for when in (colombo(SAT, 16), colombo(SUN, 16), colombo(NEXT_MON, 10)):
        code, _ = e.at(when).wake()
        assert code == 0
    assert len(e.cse.calls) == calls                                # nothing requested over the weekend
    assert [r[0] for r in q(e.hb, "select trading_date from market_schedule_items order by trading_date")] == \
        [FRI, NEXT_MON]


def test_declared_holiday_is_not_applicable_without_any_request(env):
    from worker.scheduler import wakeup
    e = env()
    e.arm(WED)
    out = wakeup.declare_closed(e.hb, WED, "CSE trading holiday notice 2026 (Poya day), circular ref TEST-1")
    assert out["recorded"] is True
    code, rep = e.at(colombo(WED, 16)).wake()
    assert code == 0 and e.cse.calls == []
    assert e.state(WED)[:2] == ("not_applicable", "finalize")
    assert "CSE trading holiday notice" in e.events(WED)[-1]["reason"]
    assert q(e.hb, "select market_status, established_by from trading_calendar where trade_date = %s", (WED,)) == \
        [("closed", "cse_notice")]


def test_undeclared_holiday_is_proven_by_session_evidence_and_never_written_closed(env):
    e = env(sessions=[d for d in weekdays() if d != WED])          # CSE did not trade on Wednesday
    e.arm(WED)
    e.at(colombo(WED, 15, 20)).wake()                               # snapshot shows Tuesday's session
    assert e.cse.keys() == ["allSecurityCode", "tradeSummary"]      # nothing more requested, nothing derived
    assert e.state(WED)[0] == "failed"
    e.at(colombo(WED, 15, 30)).wake()                               # still backing off (20 min)
    assert len(e.cse.calls) == 2
    code, rep = e.at(colombo(WED, 15, 45)).wake()                   # a fresh snapshot to confirm
    assert len(e.cse.calls) == 4
    assert e.state(WED)[:2] == ("not_applicable", "finalize")
    assert "no trading session" in e.events(WED)[-1]["reason"]
    assert [r[1] for r in e.runs(WED)] == ["failed", "failed"]      # two NEW runs (a resume could not re-snapshot)
    assert q(e.hb, "select count(*) from raw_market_observations o join market_capture_runs r on "
                   "r.id = o.request_attempt_id where r.trading_date = %s", (WED,)) == [(0,)]
    assert q(e.hb, "select count(*) from trading_calendar where trade_date = %s", (WED,)) == [(0,)]   # stays unknown


def test_multiple_days_of_downtime_catch_up_keeps_every_date_and_makes_one_capture(env):
    e = env()
    e.arm(MON)
    code, rep = e.at(colombo(THU, 16)).wake()                       # off Monday to Wednesday; back Thursday 16:00
    assert code == 2 and rep["result"] == "captured"
    assert len(e.cse.calls) == PLAN_CALLS                           # ONE capture: Thursday's, inside its window
    states = q(e.hb, "select trading_date, state, origin from market_schedule_item_state order by trading_date")
    assert states == [(MON, "missed", "catch_up"), (TUE, "missed", "catch_up"), (WED, "missed", "catch_up"),
                      (THU, "succeeded", "catch_up")]
    # each date keeps its own identity: a missed record per date, the capture labelled Thursday
    assert [r[0] for r in q(e.hb, "select trading_date from market_capture_runs order by created_at")] == \
        [MON, TUE, WED, THU]
    order = [str(a["trading_date"]) for a in rep["actions"]]
    assert order == sorted(order)                                   # oldest first, deterministically
    run = e.state(THU)[2]
    assert q(e.hb, "select observation_date from raw_market_observations where request_attempt_id = %s limit 1",
             (run,)) == [(THU,)]


def test_trading_date_is_never_the_utc_calendar_date(env):
    e = env()
    e.arm(MON)
    # 20:00 UTC on Monday is 01:30 on TUESDAY in Colombo: Monday's window (23:59:59 Colombo) has already closed
    from datetime import datetime, timezone
    code, rep = e.at(datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)).wake()
    assert rep["colombo_date"] == str(TUE)
    assert e.cse.calls == []                                        # the UTC date would still have said "Monday"
    assert e.state(MON)[0] == "missed" and e.state(TUE)[0] == "pending"
    assert e.item(TUE)["due_at"] == colombo(TUE, 15, 15)


# ------------------------------------------------------------------------------------------------ failures and retries

def test_retryable_failure_backs_off_then_resumes_the_same_run(env):
    e = env(script={"tradeSummary": [R(error_kind="network")] * 3})
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    assert e.cse.keys() == ["allSecurityCode"] + ["tradeSummary"] * 3        # P2's bounded retries
    (run1, st1, _), = e.runs(MON)
    assert st1 == "failed" and e.state(MON)[0] == "failed"
    e.at(colombo(MON, 15, 30)).wake()                                         # back-off: 20 min after attempt 1
    assert len(e.cse.calls) == 4
    code, rep = e.at(colombo(MON, 15, 41)).wake()
    assert code == 0 and e.state(MON)[0] == "succeeded"
    assert [r[0] for r in e.runs(MON)] == [run1]                              # the SAME run: idempotent derivation
    assert e.cse.keys()[4] == "tradeSummary"
    assert "allSecurityCode" not in e.cse.keys()[4:]                          # archived OK once: never re-requested
    assert q(e.hb, "select max(attempt_no) from market_source_responses where run_id = %s and "
                   "request_key = 'tradeSummary'", (run1,)) == [(4,)]
    assert [x["action"] for x in e.events(MON) if x["state"] == "running"] == ["start", "resume"]


def test_partial_capture_is_resumed_inside_the_window(env):
    e = env(script={"companyInfoSummery:ABSA.N0000": [R(500, b""), R(500, b"")]})
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    assert e.state(MON)[0] == "partial"
    before = len(e.cse.calls)
    e.at(colombo(MON, 15, 41)).wake()
    assert e.cse.keys()[before:] == ["companyInfoSummery:ABSA.N0000"]        # only what the run still lacks
    assert e.state(MON)[0] == "succeeded" and len(e.runs(MON)) == 1


def test_partial_capture_is_closed_when_its_window_closes_never_missed(env):
    e = env(script={"companyInfoSummery:ABSA.N0000": [R(500, b"")] * 20})
    e.arm(MON, max_attempts=2)
    e.at(colombo(MON, 15, 20)).wake()
    e.at(colombo(MON, 15, 41)).wake()
    calls = len(e.cse.calls)
    code, rep = e.at(colombo(MON, 18)).wake()                                 # attempts used up: waits, attention
    assert code == 2 and len(e.cse.calls) == calls
    code, rep = e.at(colombo(TUE, 0, 5)).wake()                               # window closed at 23:59:59
    assert len(e.cse.calls) == calls
    assert e.state(MON)[:2] == ("partial", "finalize")                        # captured (A) but incomplete (C): closed
    from worker.scheduler import store
    assert str(MON) not in [str(i["trading_date"]) for i in store.open_items(e.hb)]
    e.at(colombo(TUE, 0, 20)).wake()
    assert [x["action"] for x in e.events(MON)].count("finalize") == 1        # closed once, not re-alerted forever


def test_blocked_capture_gates_everything_until_the_owner_acknowledges(env, cluster, dbname):
    import psycopg2
    from worker.market_capture import capture
    e = env(script={"tradeSummary": [R(403, b"denied")]})
    e.arm(MON)
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert code == 3 and e.state(MON)[0] == "blocked"
    assert e.cse.keys() == ["allSecurityCode", "tradeSummary"]                # stopped at once, no retry
    for when in (colombo(MON, 15, 45), colombo(MON, 17)):
        code, rep = e.at(when).wake()
        assert code == 3 and len(e.cse.calls) == 2                            # every later capture is gated
    (run, _, _), = e.runs(MON)
    with pytest.raises(Exception):                                            # the scheduler's role cannot acknowledge
        capture.acknowledge_block(e.hb, run, "the scheduler acknowledging its own block")
    e.hb.rollback()
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(e.hb, "insert into market_capture_block_acknowledgements (run_id, note) values (%s, 'worker ack attempt')",
          (run,))
    e.hb.rollback()
    owner = conn(cluster, dbname, "cse_migrator")                             # the owner path (P2, unchanged)
    capture.acknowledge_block(owner, run, "owner reviewed the CSE block; resume allowed", operator="owner-test")
    owner.close()
    code, rep = e.at(colombo(MON, 17, 15)).wake()
    assert code == 0 and e.state(MON)[0] == "succeeded"
    assert [r[0] for r in e.runs(MON)] == [run]                               # blocked -> running: the same run


class Crash(BaseException):
    """A process death (power loss / SIGKILL): nothing below it runs, not even the scheduler's error handling."""


def test_abandoned_capture_after_a_crash_is_resumed_under_the_same_run(env, monkeypatch):
    """The scheduler process dies mid-capture: no terminal state, the lease still active, the item still 'running'.
    After a restart, the next wake-up expires the dead lease, P2 marks the run abandoned, and the item is resumed under
    the SAME run id - nothing archived is requested again, nothing is deleted."""
    from worker.scheduler import store
    e = env()
    real_send = e.cse.send

    def crashing_send(method, url, params, headers, timeout, clock=None, wall=None):
        if params.get("symbol") == "ABSB.N0000":
            raise Crash("power loss")
        return real_send(method, url, params, headers, timeout, clock=clock, wall=wall)
    e.cse.send = crashing_send

    def dead(*a, **k):
        raise Crash("the process is gone")
    monkeypatch.setattr(store, "finish_wakeup", dead)
    e.arm(MON)
    with pytest.raises(Crash):
        e.at(colombo(MON, 15, 20)).wake()
    monkeypatch.undo()
    e.close()                                                                 # its connections die with it
    look = conn(e.cluster, e.db)
    assert q(look, "select request_key from market_source_responses order by sequence_no") == \
        [("allSecurityCode",), ("tradeSummary",), ("companyInfoSummery:ABSA.N0000",)]
    assert q(look, "select state from market_schedule_wakeups") == [("active",)]
    look.close()
    e2 = env(clock=e.clock)                                                   # the restarted scheduler
    code, rep = e2.at(colombo(MON, 15, 50)).wake()
    assert len(rep["expired_leases"]) == 1
    assert q(e2.hb, "select state, expired_by from market_schedule_wakeups order by id")[0] == \
        ("expired", rep["wakeup_id"])
    (run, st, _), = e2.runs(MON)
    assert st == "succeeded" and e2.state(MON)[0] == "succeeded"
    assert not {"allSecurityCode", "tradeSummary", "companyInfoSummery:ABSA.N0000"} & set(e2.cse.keys())
    assert [x["state"] for x in e2.events(MON)] == ["pending", "running", "abandoned", "running", "succeeded"]


def test_stale_lease_is_reported_and_never_taken_over(env, cluster, dbname):
    from worker.market_capture import runs as p2runs
    e = env()
    e.arm(MON)
    holder = conn(cluster, dbname)                                            # a live process holding the lock ...
    assert p2runs.acquire_global_lock(holder)
    q(holder, "insert into market_schedule_wakeups (state, trigger_kind, scheduler_time, heartbeat_at, started_at, "
              "tool_version, pid, host) values ('active', 'timer', %s, now() - interval '2 hours', "
              "now() - interval '2 hours', 'test', 99, 'somewhere')",
      (colombo(MON, 13, 20),))                                               # ... whose heartbeat stopped 2 h ago
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["result"]) == (2, "stale_lease")
    assert e.cse.calls == [] and q(e.hb, "select count(*) from market_schedule_items") == [(0,)]
    assert q(e.hb, "select count(*) from market_schedule_wakeups where state = 'active'") == [(1,)]   # untouched
    holder.close()                                                            # it dies: the lock is freed with it
    code, rep = e.at(colombo(MON, 15, 25)).wake()
    assert rep["result"] == "captured" and len(rep["expired_leases"]) == 1
    assert q(e.hb, "select count(*) from market_schedule_wakeups where state = 'active'") == [(0,)]


def test_fresh_lease_means_busy_and_the_other_wakeup_does_nothing(env, cluster, dbname):
    from worker.market_capture import runs as p2runs
    e = env()
    e.arm(MON)
    holder = conn(cluster, dbname)
    assert p2runs.acquire_global_lock(holder)
    q(holder, "insert into market_schedule_wakeups (state, trigger_kind, scheduler_time, tool_version) "
              "values ('active', 'timer', %s, 'test')", (colombo(MON, 15, 19),))
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["result"]) == (0, "busy") and e.cse.calls == []
    assert q(e.hb, "select result from market_schedule_wakeups where state = 'skipped'") == [("busy",)]
    holder.close()


def test_heartbeat_is_refreshed_while_a_capture_runs(env, cluster, dbname):
    e = env()
    e.arm(MON)
    seen = []
    watcher = conn(cluster, dbname)
    real_send = e.cse.send

    def watching_send(*a, **k):
        seen.append(q(watcher, "select state, heartbeat_at from market_schedule_wakeups order by id desc limit 1")[0])
        return real_send(*a, **k)
    e.cse.send = watching_send
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    watcher.close()
    assert rep["result"] == "captured" and rep["heartbeats"] >= PLAN_CALLS
    assert {s for s, _ in seen} == {"active"}                                 # held for the whole capture
    beats = [h for _, h in seen]
    assert beats == sorted(beats) and beats[-1] > beats[0]                    # refreshed while it ran
    assert q(e.hb, "select state, result from market_schedule_wakeups") == [("released", "captured")]


# ------------------------------------------------------------------------------------------------ concurrency

def test_two_schedulers_never_capture_twice_or_in_parallel(env):
    a, b = env(), env()
    a.arm(MON)
    shared = {"in_flight": 0, "max": 0, "calls": 0}
    lock = threading.Lock()
    for e in (a, b):
        real = e.cse.send

        def counted(*args, _real=real, **kw):
            with lock:
                shared["in_flight"] += 1
                shared["calls"] += 1
                shared["max"] = max(shared["max"], shared["in_flight"])
            try:
                return _real(*args, **kw)
            finally:
                with lock:
                    shared["in_flight"] -= 1
        e.cse.send = counted
        e.at(colombo(MON, 15, 20))
    barrier, results = threading.Barrier(2), {}

    def go(name, e):
        barrier.wait()
        results[name] = e.wake()
    threads = [threading.Thread(target=go, args=(n, x)) for n, x in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outcomes = sorted(r[1]["result"] for r in results.values())
    assert outcomes in (["busy", "captured"], ["captured", "idle"])           # the loser saw the lock, or the work done
    assert shared["calls"] == PLAN_CALLS and shared["max"] == 1               # one capture, one request at a time
    assert len(a.runs(MON)) == 1 and q(a.hb, "select count(*) from market_schedule_items") == [(1,)]


def test_manual_capture_holding_the_lock_makes_the_scheduler_wait(env, cluster, dbname):
    from worker.market_capture import runs as p2runs
    e = env()
    e.arm(MON)
    manual = conn(cluster, dbname)                                            # e.g. `cse-capture capture` running now
    assert p2runs.acquire_global_lock(manual)
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["result"]) == (0, "busy") and "manual capture" in rep["holder"]
    assert e.cse.calls == []
    manual.close()


def test_a_manual_capture_satisfies_the_item_and_is_never_repeated(env):
    from worker.market_capture import capture, config as cfgmod
    e = env()
    e.arm(MON)
    e.at(colombo(MON, 15, 16))
    p2rt = capture.Runtime(transport=e.cse, clock=e.clock.monotonic, wall=e.clock.wall, sleep=e.clock.sleep,
                           log=lambda m: None)
    state, rep = capture.start(e.ctl, e.work, e.cfg, trading_date=MON, policy=cfgmod.daily_policy("post_close"),
                               rt=p2rt)                                       # the operator's manual P2 capture
    assert state == "succeeded"
    calls = len(e.cse.calls)
    code, out = e.at(colombo(MON, 15, 20)).wake()
    assert code == 0 and len(e.cse.calls) == calls                            # adopted, not captured again
    assert e.state(MON) == ("succeeded", "observe", rep["run_id"])
    assert e.events(MON)[-1]["details"]["adopted"] is True
    assert [r[2] for r in e.runs(MON)] == ["operator"]


# ------------------------------------------------------------------------------------------------ security

SETTINGS_INSERT = ("insert into market_schedule_settings (armed, earliest_start_local, window_close_local, "
                   "retry_base_minutes, retry_max_minutes, max_attempts, no_session_confirmations, "
                   "daily_request_budget, max_catch_up_days, stale_lease_minutes, note) values (false, '15:15', "
                   "'23:59:59', 20, 120, 4, 2, 150, 14, 30, 'a service role trying to decide')")


def test_arming_is_owner_only_and_history_is_append_only(env, cluster, dbname):
    import psycopg2
    from worker.scheduler import schedule as sched, store
    e = env()
    w = e.hb
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):                # 1. no INSERT privilege
        q(w, SETTINGS_INSERT)
    w.rollback()
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):                # 2. cannot become the owner
        q(w, "set role cse_owner")
    w.rollback()
    armed = sched.ScheduleSettings(armed=True, start_date=MON, user_agent=e.cfg.user_agent, host=HOST,
                                   expected_requests="55-65", stop_conditions=sched.STOP_CONDITIONS,
                                   note="the worker arming itself")
    with pytest.raises(store.OwnerPathRequired):                              # 3. its code path refuses
        store.record_settings_as_owner(w, armed, "worker")
    su = conn(cluster, dbname, "postgres", autocommit=True)
    q(su, "grant insert on market_schedule_settings to cse_worker")           # 4. a mistaken grant ...
    try:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="owner decision"):
            q(w, SETTINGS_INSERT)                                             # ... is still refused by the trigger
        w.rollback()
    finally:
        q(su, "revoke insert on market_schedule_settings from cse_worker")
    for role in ("cse_backup",):
        b = conn(cluster, dbname, role)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(b, SETTINGS_INSERT)
        b.rollback()
        assert q(b, "select count(*) from market_schedule_items") == [(0,)]   # backup reads everything, writes none
        b.close()
    assert store.current_settings(w).armed is False
    e.arm(MON)                                                                # the owner path works
    assert store.current_settings(w).armed is True
    assert q(w, "select approved_by from market_schedule_settings") == [("cse_migrator",)]
    e.at(colombo(MON, 15, 20)).wake()
    for stmt in ("update market_schedule_items set origin = 'operator'", "delete from market_schedule_items",
                 "update market_schedule_item_events set state = 'succeeded'", "delete from market_schedule_item_events",
                 "delete from market_schedule_wakeups", "truncate market_schedule_wakeups",
                 "update market_capture_runs set trading_date = trading_date", "delete from trading_calendar",
                 "update trading_calendar set market_status = 'closed'"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(w, stmt)
        w.rollback()
    with pytest.raises(psycopg2.Error, match="finished|immutable"):          # finished lease rows are immutable
        q(w, "update market_schedule_wakeups set result = 'rewritten'")
    w.rollback()
    owner = conn(cluster, dbname, "cse_migrator")
    for stmt in ("update market_schedule_items set origin = 'operator'", "delete from market_schedule_item_events",
                 "update market_schedule_settings set armed = false"):
        q(owner, "set role cse_owner")
        with pytest.raises(psycopg2.Error) as ei:                             # append-only stops even the owner
            q(owner, stmt)
        assert ei.value.pgcode == "23001", stmt
        owner.rollback()
    owner.close()
    su.close()


def test_item_history_cannot_contradict_p2_evidence(env):
    """The item-event guard checks every event against P2's own run state and archive: the scheduler's history can
    never claim a success, a miss or a non-trading day that P2's evidence contradicts."""
    import psycopg2
    from worker.market_capture import runs as p2runs
    from worker.scheduler import store
    e = env(script={"companyInfoSummery:ABSA.N0000": [R(500, b""), R(500, b"")]})
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    assert e.state(MON)[0] == "partial"                                       # open, with the date's session archived
    item, (run, _, _) = e.item(MON), e.runs(MON)[0]
    missed_run = p2runs.create_run(e.ctl, run_kind="market_capture", trading_date=MON, capture_mode="post_close",
                                   policy={"name": "missed_record"}, user_agent=None, tool_version="t",
                                   code_revision=None)
    p2runs.append_event(e.ctl, missed_run, "missed", "a (wrong) manual missed record")
    tue_run = p2runs.create_run(e.ctl, run_kind="market_capture", trading_date=TUE, capture_mode="post_close",
                                policy={"name": "x"}, user_agent=None, tool_version="t", code_revision=None)
    bad = [("succeeded", run, "own state is succeeded"),                     # claims more than the run proves
           ("succeeded", None, "must name a capture run"),                    # a success without any run
           ("running", tue_run, "is not a post_close capture run for"),      # another date's run
           ("missed", missed_run, "cannot be missed"),                        # the date's session IS captured
           ("not_applicable", None, "cannot be not_applicable")]              # observations of the date exist
    for state, run_id, msg in bad:
        with pytest.raises(psycopg2.Error, match=msg):
            store.append_item_event(e.hb, item["id"], state, "observe", run_id=run_id, scheduler_time=colombo(MON, 16))
        e.hb.rollback()
    assert e.state(MON)[0] == "partial"                                       # nothing was recorded


def test_scheduler_refuses_when_its_role_is_over_privileged(env, cluster, dbname):
    e = env()
    e.arm(MON)
    su = conn(cluster, dbname, "postgres", autocommit=True)
    for grant, revoke in (("grant insert on market_schedule_settings to cse_worker",
                           "revoke insert on market_schedule_settings from cse_worker"),
                          ("grant delete on market_schedule_item_events to cse_worker",
                           "revoke delete on market_schedule_item_events from cse_worker"),
                          ("grant insert on market_capture_block_acknowledgements to cse_worker",
                           "revoke insert on market_capture_block_acknowledgements from cse_worker")):
        q(su, grant)
        try:
            code, rep = e.at(colombo(MON, 15, 20)).wake()
            assert code == 5 and rep["refused"] == "security_preflight", rep
            assert e.cse.calls == []
        finally:
            q(su, revoke)
    q(su, "alter table market_schedule_item_events disable trigger trg_msie_guard")
    try:
        code, rep = e.at(colombo(MON, 15, 20)).wake()
        assert code == 5 and any("trg_msie_guard" in p for p in rep["problems"])
    finally:
        q(su, "alter table market_schedule_item_events enable trigger trg_msie_guard")
    su.close()
    code, rep = e.at(colombo(MON, 15, 21)).wake()
    assert rep["result"] == "captured"


def test_p1_verifier_and_role_model_hold_with_0014(cluster, dbname):
    from worker.ops import verify_server as vs
    rep = vs.Report()
    c = conn(cluster, dbname, "postgres")
    with c.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=MIGDIR)
    assert not [i for i in rep.items if i["status"] != "PASS"], rep.items
    assert q(c, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = "
                "'public' and p.proname like 'market_schedule%%' and (p.prosecdef or p.proowner <> "
                "'cse_owner'::regrole)") == [(0,)]                            # no SECURITY DEFINER, owned by the owner
    assert q(c, "select rolname from pg_roles where rolname like 'cse%%' and (rolsuper or rolcreaterole or "
                "rolcreatedb)") == []
    assert q(c, "select pg_has_role('cse_worker', 'cse_owner', 'MEMBER'), pg_has_role('cse_worker', 'cse_migrator', "
                "'MEMBER')") == [(False, False)]
    assert q(c, "select has_table_privilege('cse_reader', 'market_schedule_items', 'SELECT'), "
                "has_table_privilege('cse_reader', 'market_schedule_items', 'INSERT')") == [(True, False)]
    c.close()


# ------------------------------------------------------------------------------------------------ guards (G-1 and time)

def test_host_clock_and_user_agent_guards_refuse_before_any_request(env, cluster, dbname, tmp_path):
    e = env(host="some-other-machine")                                        # a restored copy on another machine
    e.arm(MON)
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["refused"]) == (5, "host") and e.cse.calls == []
    assert q(e.hb, "select count(*) from market_schedule_items") == [(0,)]
    ok = env()
    ok.synced = False                                                         # NTP not synchronised
    code, rep = ok.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["refused"]) == (5, "clock") and ok.cse.calls == []
    wrong_ua = env(email="someone-else@example.org")                          # not the User-Agent the owner approved
    code, rep = wrong_ua.at(colombo(MON, 15, 20)).wake()
    assert (code, rep["refused"]) == (5, "user_agent") and wrong_ua.cse.calls == []
    assert wrong_ua.state(MON)[0] == "pending"                                # bookkeeping done, capture refused
    ok.synced = True
    ok.at(colombo(MON, 15, 25)).wake()
    assert ok.state(MON)[0] == "succeeded"
    code, rep = ok.at(colombo(MON, 14)).wake()                                # the clock jumped back 85 minutes
    assert (code, rep["refused"]) == (5, "clock")


def test_catch_up_gap_beyond_the_horizon_needs_an_explicit_confirmation(env):
    e = env()
    e.arm(MON, max_catch_up_days=3)
    code, rep = e.at(colombo(NEXT_MON, 10)).wake()                            # off for a week (or the clock jumped)
    assert (code, rep["refused"]) == (5, "catch_up_gap")
    assert q(e.hb, "select count(*) from market_schedule_items") == [(0,)] and e.cse.calls == []
    code, rep = e.at(colombo(NEXT_MON, 10, 5)).wake(confirm_catch_up_through=NEXT_MON)
    assert code == 2 and e.cse.calls == []
    assert q(e.hb, "select trading_date, state from market_schedule_item_state order by trading_date") == \
        [(MON, "missed"), (TUE, "missed"), (WED, "missed"), (THU, "missed"), (FRI, "missed"), (NEXT_MON, "pending")]


def test_daily_request_budget_counts_every_run_and_defers_instead_of_bursting(env):
    e = env(script={"companyInfoSummery:ABSA.N0000": [R(500, b"")] * 20})
    e.arm(MON, daily_request_budget=10)
    e.at(colombo(MON, 15, 20)).wake()                                         # 1 + 1 + 2 (ABSA x2) + 1 + 5 = 10
    assert len(e.cse.calls) == 10 and e.state(MON)[0] == "partial"
    code, rep = e.at(colombo(MON, 15, 41)).wake()
    assert code == 2 and rep["result"] == "deferred" and "budget" in rep["reason"]
    assert len(e.cse.calls) == 10                                             # the envelope is never multiplied
    assert rep["budget"] == {"daily_request_budget": 10, "used_today": 10, "remaining": 0}


def test_g1_request_discipline_is_p2s_and_holds_inside_scheduled_captures(env):
    e = env()
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    calls = e.cse.calls
    assert e.cse.max_in_flight == 1
    for a, b in zip(calls, calls[1:]):
        assert b["started"] - a["ended"] >= 1.5 - 1e-9                        # >= 1.5 s end-to-start
    armed_ua = q(e.hb, "select user_agent from market_schedule_settings order by id desc limit 1")[0][0]
    assert all(c["headers"]["User-Agent"] == armed_ua for c in calls) and EMAIL in armed_ua
    assert all("cookie" not in {k.lower() for k in c["headers"]} for c in calls)
    assert q(e.hb, "select policy -> 'request' ->> 'min_interval_seconds', policy ->> 'max_requests' from "
                   "market_capture_runs") == [("1.5", "150")]


# ------------------------------------------------------------------------------------------------ backups, D-2

def test_backup_state_stays_independent_of_capture_state(env, cluster, dbname):
    from worker.market_capture import completeness
    e = env()
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()                                         # succeeded with no backup at all
    run = e.state(MON)[2]
    b = conn(cluster, dbname, "cse_backup")
    rid = q(b, "insert into ops.backup_runs (run_kind, status) values ('local_dump', 'running') returning id")[0][0]
    q(b, "update ops.backup_runs set status = 'failed', finished_at = now(), error = 'disk full' where id = %s", (rid,))
    assert completeness.protection(b, run)["level"] == "local_only"          # F: read-only, reported beside it
    b.close()
    e.at(colombo(MON, 16)).wake()
    assert e.state(MON)[0] == "succeeded" and e.runs(MON)[0][1] == "succeeded"   # a failed backup changed nothing
    with open(e.marker, encoding="utf-8") as f:
        assert json.load(f)["state"] == "succeeded"                          # the dump was only made eligible
    import inspect
    from worker.scheduler import wakeup
    src = inspect.getsource(wakeup)
    assert "backup_runs" not in src and "systemctl" not in src                # the scheduler never runs or reads backups


def test_frozen_d2_canonicalisation_failure_is_recorded_never_promoted_and_archive_intact(env):
    """A second post_close observation of the date that DISAGREES (here: an earlier manual capture's COMB row) makes the
    FROZEN Stage E upsert fail with D-2 (Decimal in the discrepancy JSON). The capture itself is complete and stays
    'succeeded' (A-C); canonicalisation is recorded as failed for that security (D); the archive and the raw
    observations are intact; the scheduler neither re-captures nor rewrites anything to make D look complete."""
    from datetime import datetime, timezone
    from worker import db as stage_e_db
    from worker.market_capture import capture
    e = env()
    e.arm(MON)
    cid = q(e.hb, "insert into companies (ticker, company_name) values ('COMB.N0000', 'COMB TEST NAME') "
                  "returning id")[0][0]
    stage_e_db.insert_raw_observation(e.work, request_attempt_id=str(uuid.uuid4()), ingestion_job_id=None,
                                      company_id=str(cid), observation_date=MON, capture_window="post_close",
                                      source="CSE_API", observed_at=datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc),
                                      fields={"closing_price": 1.25, "last_traded_price": 1.25},
                                      raw_payload={"test": "earlier disagreeing observation"})
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    st, _, run = e.state(MON)
    assert st == "succeeded"                                                  # the capture (A, B, C) is complete ...
    d = e.events(MON)[-1]["details"]["completeness"]["D"]
    assert d["status"] == "partial" and d["failed"] == 1                      # ... canonicalisation is NOT
    assert any("Decimal is not JSON serializable" in r for r in d["failed_reasons"])
    assert q(e.hb, "select canonical_status, raw_status from market_capture_security_results where run_id = %s "
                   "and symbol = 'COMB.N0000'", (run,)) == [("failed", "produced")]
    assert q(e.hb, "select count(*) from raw_market_observations where request_attempt_id = %s", (run,)) == [(7,)]
    assert capture.verify_archive(e.hb, e.cfg.spool_root, run)["problems"] == []   # archive: both copies intact
    calls = len(e.cse.calls)
    for when in (colombo(MON, 16), colombo(MON, 18), colombo(TUE, 1)):
        e.at(when).wake()
    assert len(e.cse.calls) == calls and len(e.runs(MON)) == 1                # never re-captured to "fix" D
    assert e.state(MON)[0] == "succeeded"
    assert q(e.hb, "select count(*) from daily_market_data d join companies c on c.id = d.company_id where "
                   "c.ticker = 'COMB.N0000' and d.trade_date = %s", (MON,)) == [(0,)]   # no canonical row faked


# ------------------------------------------------------------------------------------------------ operator commands

def test_operator_commands_are_safe_and_explicit(env, cluster, dbname, capsys):
    from worker.scheduler import cli, wakeup
    e = env(script={"tradeSummary": [R(error_kind="network")] * 3})
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    assert e.state(MON)[0] == "failed"

    def run(*argv):
        code = cli.main(list(argv), rt=e.rt, env=e.env)
        out = capsys.readouterr()
        return code, (json.loads(out.out) if out.out.strip() else None), out.err
    code, rep, _ = run("status")
    assert code == 0 and rep["settings"]["armed"] is True and rep["open"][0]["state"] == "failed"
    code, rows, _ = run("list", "--state", "failed")
    assert [str(r["trading_date"]) for r in rows] == [str(MON)]
    code, shown, _ = run("show", "--trading-date", str(MON))
    assert shown["item"]["trading_date"] == str(MON) and shown["runs"][0]["state"] == "failed"
    calls = len(e.cse.calls)
    e.at(colombo(MON, 15, 25))                                                # still inside the 20-min back-off
    code, rep, _ = run("retry", "--trading-date", str(MON), "--reason", "network fixed, retry now")
    assert code == 0 and e.state(MON)[0] == "succeeded" and len(e.cse.calls) > calls
    assert e.events(MON)[-2]["details"]["operator_reason"] == "network fixed, retry now"
    code, rep, err = run("retry", "--trading-date", str(TUE), "--reason", "no item for this date yet")
    assert code == 5 and "no work item" in err
    code, rep, _ = run("add", "--trading-date", str(SAT), "--reason", "CSE special Saturday session notice")
    assert code == 0 and rep["created"] is True and rep["item"]["origin"] == "operator"
    code, rep, _ = run("add", "--trading-date", str(SAT), "--reason", "CSE special Saturday session notice")
    assert rep["created"] is False                                            # never duplicated
    code, rep, err = run("declare-closed", "--trading-date", str(MON), "--reference", "a wrong holiday notice ref")
    assert code == 5 and "proved a session" in err                            # evidence beats a declaration
    calls = len(e.cse.calls)
    code, rep, _ = run("reprocess", "--trading-date", str(MON))
    assert code == 0 and rep["state"] == "succeeded" and len(e.cse.calls) == calls        # archive only
    code, rep, _ = run("verify")
    assert code == 0 and rep["problems"] == []
    code, rep, err = run("arm", "--start-date", str(TUE), "--user-agent", e.cfg.user_agent, "--host", HOST,
                         "--expected-requests", "55-65", "--note", "worker tries to arm", "--confirm-stop-conditions")
    assert code == 5 and "owner decision" in err                              # the worker login is refused
    names = cli.parser()._subparsers._group_actions[0].choices
    assert not [n for n in names if any(w in n for w in ("delete", "purge", "reset", "drop", "wipe", "truncate"))]
    assert wakeup.EXIT_BLOCKED == 3


def test_owner_arm_and_disarm_through_the_cli(env, capsys):
    from worker.scheduler import cli
    e = env()
    e.at(colombo(MON, 9))
    owner_env = dict(e.env, CSE_DB_USER="cse_migrator", CSE_OPERATOR="owner-at-keyboard")
    argv = ["arm", "--start-date", str(MON), "--user-agent", e.cfg.user_agent, "--host", HOST, "--expected-requests",
            "55-65", "--note", "release gate: first production capture approved", "--confirm-stop-conditions"]
    assert cli.main(argv[:-1], rt=e.rt, env=owner_env) == 5                   # stop conditions must be acknowledged
    capsys.readouterr()
    past = list(argv)
    past[2] = str(date(2026, 9, 4))
    assert cli.main(past, rt=e.rt, env=owner_env) == 5
    assert "past dates" in capsys.readouterr().err                            # never claims dates before arming
    bad_ua = list(argv)
    bad_ua[4] = e.cfg.user_agent.replace("personal", "PERSONAL")
    assert cli.main(bad_ua, rt=e.rt, env=owner_env) == 5
    capsys.readouterr()
    assert cli.main(argv, rt=e.rt, env=owner_env) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["armed"] is True and rep["settings"]["stop_conditions"] and rep["settings"]["host"] == HOST
    row = q(e.hb, "select armed, user_agent, host, expected_requests, approved_by, os_user, jsonb_array_length("
                  "stop_conditions), start_date from market_schedule_settings")
    assert row == [(True, e.cfg.user_agent, HOST, "55-65", "cse_migrator", row[0][5], 5, MON)]
    assert "owner-at-keyboard" in row[0][5]
    assert cli.main(["disarm", "--note", "CSE asked us to stop: disarm now"], rt=e.rt, env=owner_env) == 0
    capsys.readouterr()
    code, rep = e.at(colombo(MON, 15, 20)).wake()
    assert rep["result"] == "disarmed" and e.cse.calls == []                  # nothing contacted after disarming


def test_live_capture_contradicting_a_declared_closure_is_flagged(env):
    from worker.scheduler import wakeup
    e = env()
    e.arm(WED)
    wakeup.declare_closed(e.hb, WED, "CSE holiday notice (turned out to be wrong)")
    e.at(colombo(WED, 9))
    wakeup.add_item(e.ctl, e.hb, e.cfg, WED, "CSE announced the session will open after all", e.rt)
    code, rep = e.at(colombo(WED, 15, 20)).wake()
    assert e.state(WED)[0] == "succeeded" and len(e.cse.calls) == PLAN_CALLS
    assert code == 2 and any("but live capture proved a session" in a["reason"] for a in rep["attention"])
    assert q(e.hb, "select market_status from trading_calendar where trade_date = %s", (WED,)) == [("closed",)]


def test_run_that_died_before_its_window_closed_is_rederived_from_the_archive_only(env, monkeypatch):
    """The server dies mid-capture and is off until after the window closed. The date's session IS archived (A), so
    the item is not missed: the run is re-derived from the archive (P2 reprocess, no CSE request - CSE no longer serves
    that date's snapshot) and then closed as the partial capture it is."""
    from worker.scheduler import store
    e = env()
    real_send = e.cse.send

    def crashing_send(method, url, params, headers, timeout, clock=None, wall=None):
        if params.get("symbol") == "ABSB.N0000":
            raise Crash("power loss")
        return real_send(method, url, params, headers, timeout, clock=clock, wall=wall)
    e.cse.send = crashing_send

    def dead(*a, **k):
        raise Crash("the process is gone")
    monkeypatch.setattr(store, "finish_wakeup", dead)
    e.arm(MON)
    with pytest.raises(Crash):
        e.at(colombo(MON, 15, 20)).wake()
    monkeypatch.undo()
    e.close()
    e2 = env(clock=e.clock)
    code, rep = e2.at(colombo(TUE, 9)).wake()                                 # back the next morning
    assert e2.cse.calls == []                                                 # nothing re-requested, ever
    (run, st, _), = e2.runs(MON)
    assert st == "partial"                                                    # derived from what was archived
    assert [(x["state"], x["action"]) for x in e2.events(MON)][-2:] == [("abandoned", "observe"),
                                                                          ("partial", "reprocess")]
    assert q(e2.hb, "select count(*) from raw_market_observations where request_attempt_id = %s", (run,)) == [(6,)]
    code, rep = e2.at(colombo(TUE, 9, 15)).wake()
    assert e2.state(MON)[:2] == ("partial", "finalize") and e2.cse.calls == []
    assert q(e2.hb, "select market_status from trading_calendar where trade_date = %s", (MON,)) == [("open",)]


def test_retry_of_a_succeeded_date_never_captures_it_again(env, capsys):
    from worker.scheduler import cli
    e = env()
    e.arm(MON)
    e.at(colombo(MON, 15, 20)).wake()
    calls = len(e.cse.calls)
    code = cli.main(["retry", "--trading-date", str(MON), "--reason", "operator double-checks it"], rt=e.rt, env=e.env)
    rep = json.loads(capsys.readouterr().out)
    assert code == 0 and rep["result"] == "idle" and len(e.cse.calls) == calls
    assert len(e.runs(MON)) == 1 and e.state(MON)[0] == "succeeded"


def test_one_no_session_snapshot_then_the_window_closes_records_missed_once(env):
    """Regression: a snapshot taken on the date showing an EARLIER session (a holiday, or stale data) archives an OK
    tradeSummary that does not capture the date. If the window closes before the second confirmation, the date is
    recorded missed - exactly once, final, with no CSE request and no loop of new missed records."""
    e = env(sessions=[d for d in weekdays() if d != WED])
    e.arm(WED, max_attempts=1)                                                # one snapshot only, then no retry
    e.at(colombo(WED, 15, 20)).wake()
    assert e.cse.keys() == ["allSecurityCode", "tradeSummary"] and e.state(WED)[0] == "failed"
    code, rep = e.at(colombo(THU, 0, 5)).wake()                               # Wednesday's window has closed
    assert code == 2 and e.state(WED)[:2] == ("missed", "record_missed")
    assert "1 of 2 no-session confirmations" in e.events(WED)[-1]["reason"]
    for when in (colombo(THU, 0, 20), colombo(THU, 0, 35)):
        e.at(when).wake()
    assert len(e.cse.calls) == 2                                              # nothing more requested for Wednesday
    assert [r[1] for r in e.runs(WED)] == ["failed", "missed"]                # one missed record, never repeated
    assert q(e.hb, "select count(*) from market_source_responses r join market_capture_runs m on m.id = r.run_id "
                   "where m.trading_date = %s", (WED,)) == [(2,)]            # the evidence stays archived
