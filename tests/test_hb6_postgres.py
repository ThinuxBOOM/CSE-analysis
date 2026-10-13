"""
Phase 2 HB-6 (operations and pilot) against REAL PostgreSQL 17, with migration 0016's real guards and the frozen HB-1 to
HB-5 and F6.4: HB-6's runner and command line drive everything; every CSE response comes from a scripted transport or a
scripted cdn.cse.lk (tests/hb6_support.py): nothing contacts the network.

    orchestration  o1 the pilot from identity to reconciliation, in stage order, one slice per wake-up, the daily
                   audit and its export
    single runner  s1 a second runner records busy and touches nothing; a dead runner's claim is expired
                   s2 two runners racing through the claim cannot both hold it (no lock key: serializable)
    resume         r1 an interrupted wake-up is recorded stopped; the next resumes; repeated wake-ups are idempotent
    failures       f1 a failed validation needs attention, defers every reconcile, and an operator re-queue recovers it
    late evidence  m1 M4 through the late pass, promotion and explicit re-queues, with nothing downloaded again
    stop signal    t1 SIGTERM unwinds the wake-up, which is recorded stopped; the previous handler is restored
    gates          g1 disarmed: F6 bookkeeping only; g2 a block stops every stage until acknowledged and resumed;
                   g3 no HB-P1; g4 the clock; g5 HB-S3 without a sample, a wrong User-Agent; g6 the real preflight;
                   g7 the database disk refuses below its floor, the dump destination only alerts (B-HB6-2)
    reports        a1 the pilot report and the reports, outside the repository, once; c1 HB-S4 in checkpoint blocks
    boundaries     b1 F6.4 units never wrapped, no lock or lease during F6 work; b2 the runner writes only its wake-ups
    command line   c2 status, plan, requeue, run and the owner's arming through cse-migrator

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb6_postgres.py
"""
import json
import os
import platform
import shutil
import signal
import socket
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb2_fakes as F  # noqa: E402
import hb4_support as H  # noqa: E402
import hb5_support as X  # noqa: E402
from hb4_support import q  # noqa: E402
from hb5_support import Filing  # noqa: E402
from hb6_support import (World, active_runner, attention, counts, pilot_arming, requeue,  # noqa: E402
                         route_for, runner_wakeups, states_of)
from worker.backfill_discovery import identity  # noqa: E402
from worker.backfill_f6 import audit as hb5_audit, preflight as hb5_preflight, validation as hb5_validation  # noqa
from worker.backfill_f6.errors import F6Refused  # noqa: E402
from worker.backfill_ops import (EXIT_ATTENTION, EXIT_BLOCKED, EXIT_OK, EXIT_REFUSED, actions, claim, cli,  # noqa
                                 decisions, pilot, preflight, reports, runner as ops_runner)
from worker.backfill_ops.errors import OpsRefused  # noqa: E402
from worker.financial_backfill import keys, store  # noqa: E402
from worker.financial_truth import inputs  # noqa: E402
from worker.financial_truth_store import jobs  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
IDENT = {"host": "test-host", "pid": 1, "tool_version": "hb.ops.1"}


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
    base = tempfile.mkdtemp(prefix="hb6", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def env(cluster, tmp_path, monkeypatch):
    e = H.Env(cluster, tmp_path, monkeypatch)
    yield e
    e.close()


def pilot_world(env, tmp_path, filings, pilot_filings=None, **over):
    return World(env, filings, tmp_path, **pilot_arming(pilot_filings or filings, **over))


def to_designation(wd):
    """Wake until the pilot's runs are validated and the configuration waits for the owner; then the owner designates."""
    out = wd.until(lambda code, r: "designation" in attention(r))
    cid = out[-1][1]["f6"]["configuration"]["configuration_id"]
    X.designate(wd.env, cid)
    return out, cid


def status_of(w, env):
    from worker.backfill_ops import status
    return status.status(w, env.clock.wall())


def doc_attempts(w, fid=None):
    sql = ("select count(*) from backfill_request_attempts a join backfill_work_items i on i.id = a.item_id "
           "where a.request_class = 'document'")
    return q(w, sql + (" and i.cse_filing_id = %s" if fid else ""), (fid,) if fid else None)[0][0]


# ================================================================================================ orchestration

def test_o1_the_pilot_from_identity_to_reconciliation_in_stage_order_one_slice_per_wake_up(env, tmp_path):
    fs = [Filing(941001, "facts", listed=True), Filing(941002, "facts", listed=True, day=3),
          Filing(941003, "facts", sec=X.UNKNOWN_SEC_ID, day=4)]          # outside the sample, and without an issuer
    wd = pilot_world(env, tmp_path, fs, fs[:2])
    w = env.conn()
    code, r1 = wd.wake()                          # HB-S1 import, the plan, the day's audit, then ONE discovery slice
    assert r1["bookkeeping"]["identity_pass"]["item"] and not r1["bookkeeping"]["identity_pass"]["already"]
    assert r1["bookkeeping"]["plan_items"]["feed_window"] == 2 and r1["slice"]["kind"] == "discovery"
    assert r1["audit"]["due"] and r1["audit"]["snapshot_id"] and r1["result"] == "worked"
    code, r2 = wd.wake()                          # the plan is closed: its IE-4 pass, then the pilot's document slice
    assert r2["bookkeeping"]["discovery"]["closed"] and r2["bookkeeping"]["closure_pass"]["item"]
    assert (r2["slice"]["kind"], r2["slice"]["stage"]) == ("documents", "HB-S3")
    assert states_of(w, fs[:2]) == {941001: "persisted", 941002: "persisted"}
    assert X.runs_of(w, 941003) == [] and doc_attempts(w, 941003) == 0             # outside the owner's sample
    assert r2["audit"] == {"due": False, "reason": "today's audit is done"}
    code, r3 = wd.wake()                          # F6: validated; the configuration registered for the owner
    assert code == EXIT_ATTENTION and {"designation", "pilot_complete"} <= set(attention(r3))
    assert r3["f6"]["validation"]["succeeded"] == 2 and r3["f6"]["promotion"] == {"validated": 2}
    assert r3["slice"]["due"] is False and q(w, "select count(*) from financial_f6_jobs where kind = 'reconcile'") \
        == [(0,)]                                                                  # nothing before the designation
    X.designate(env, r3["f6"]["configuration"]["configuration_id"])
    code, r4 = wd.wake()                          # COMB's own pass; the final pass waits for 941003 (HB-5 readiness)
    assert [s for _, s in r4["f6"]["reconcile"]["runs"]] == ["succeeded"]
    assert not r4["f6"]["reconcile"]["final_pass_ran"] and r4["f6"]["promotion_after_reconcile"] == {"reconciled": 2}
    assert attention(r4) == ["pilot_complete"] and code == EXIT_ATTENTION
    snapshots = [r for r in runner_wakeups(w) if (r[4].get("audit") or {}).get("snapshot_id")]
    assert len(snapshots) == 1                                                     # once per Colombo day
    env.clock.sleep(86400)
    code, r5 = wd.wake()
    assert r5["audit"]["due"] and r5["audit"]["snapshot_id"] and r5["audit"]["export"].startswith(str(wd.reports))
    assert os.path.exists(r5["audit"]["export"])
    rows = runner_wakeups(w)
    assert [r[1] for r in rows] == ["released"] * 5 and all(r[2] == "manual" for r in rows)
    assert rows[0][4]["slice"]["kind"] == "discovery" and rows[1][4]["slice"]["stage"] == "HB-S3"


# ================================================================================================ the single runner

def test_s1_a_second_runner_records_busy_and_touches_nothing_and_a_dead_claim_is_expired(env, tmp_path):
    wd = World(env, [], tmp_path, master=False, sweep=False, arm=False)
    w = env.conn()
    holder, wid = active_runner(env)
    before = counts(w)
    code, rep = wd.wake()
    assert (code, rep["result"], rep["holder"]["id"]) == (EXIT_OK, "busy", wid)
    busy = [r for r in runner_wakeups(w) if r[3] == "busy"]
    assert len(busy) == 1 and busy[0][1] == "skipped" and busy[0][4]["holder_wakeup"] == wid
    assert counts(w) == before and q(w, "select count(*) from financial_f6_jobs") == [(0,)]
    holder.close()                                                                 # the holder's session ends
    end = time.monotonic() + 15
    while claim.live_runners(w):
        assert time.monotonic() < end, "the closed session is still alive"
        time.sleep(0.05)
    code, rep = wd.wake()
    assert code == EXIT_OK and rep["result"] == "idle" and wid in rep["expired_wakeups"]
    assert q(w, "select state, result from backfill_wakeups where id = %s", (wid,)) == [("expired", "expired")]


def test_s2_two_runners_racing_through_the_claim_cannot_both_hold_it(env, monkeypatch):
    a, b, w = env.conn(), env.conn(), env.conn()
    real, got = store.start_wakeup, {}

    def racing(conn, **kw):                       # A's whole claim between B's read and B's insert
        if conn is b and "a" not in got:
            got["a"] = claim.acquire(a, trigger="manual", runner_time=env.clock.wall(), ident=IDENT)
        return real(conn, **kw)
    monkeypatch.setattr(store, "start_wakeup", racing)
    wid_b, holder = claim.acquire(b, trigger="manual", runner_time=env.clock.wall(), ident=IDENT)
    wid_a, _ = got["a"]
    assert wid_a is not None and wid_b is None and holder == {"concurrent": True}
    assert [r["id"] for r in claim.live_runners(w)] == [wid_a]
    assert q(w, "select state, result from backfill_wakeups where tool_version = 'hb.ops.1' order by id") == [
        ("active", None), ("skipped", "busy")]


# ================================================================================================ resume

def test_r1_an_interrupted_wake_up_is_recorded_stopped_the_next_resumes_and_repetition_is_idempotent(env, tmp_path,
                                                                                                     monkeypatch):
    fs = [Filing(942001, "facts", listed=True), Filing(942002, "facts", listed=True, day=3)]
    wd = pilot_world(env, tmp_path, fs)
    w = env.conn()
    wd.wake()
    wd.wake()
    assert states_of(w, fs) == {942001: "persisted", 942002: "persisted"}
    real = hb5_validation.validate_pending

    def dies(*a, **k):
        raise SystemExit(143)                     # the process is stopped in the middle of the F6 work
    monkeypatch.setattr(hb5_validation, "validate_pending", dies)
    with pytest.raises(SystemExit):
        wd.wake()
    last = runner_wakeups(w)[-1]
    assert (last[1], last[3]) == ("released", "stopped")
    assert "SystemExit 143" in q(w, "select error from backfill_wakeups where id = %s", (last[0],))[0][0]
    monkeypatch.setattr(hb5_validation, "validate_pending", real)
    _, cid = to_designation(wd)
    wd.until(lambda code, r: r["f6"].get("promotion_after_reconcile") == {"reconciled": 2})
    snapshot = counts(w) | {"jobs": q(w, "select count(*) from financial_f6_jobs")[0][0],
                            "items": q(w, "select count(*) from backfill_work_items")[0][0],
                            "events": q(w, "select count(*) from backfill_item_events")[0][0]}
    for _ in range(3):
        code, rep = wd.wake()
        assert rep["result"] == "idle" and attention(rep) == ["pilot_complete"]
    assert counts(w) | {"jobs": q(w, "select count(*) from financial_f6_jobs")[0][0],
                        "items": q(w, "select count(*) from backfill_work_items")[0][0],
                        "events": q(w, "select count(*) from backfill_item_events")[0][0]} == snapshot


# ================================================================================================ failures and re-queues

def test_f1_a_failed_validation_needs_attention_defers_every_reconcile_and_an_operator_requeue_recovers_it(
        env, tmp_path, monkeypatch):
    fs = [Filing(943001, "facts", listed=True), Filing(943002, "facts", listed=True, day=3)]
    wd = pilot_world(env, tmp_path, fs)
    w = env.conn()
    wd.wake()
    wd.wake()
    bad = X.runs_of(w, 943002)[0]
    real = jobs.compute_validation

    def failing(cur, f5_run_id, **kw):
        if str(f5_run_id) == bad:
            raise inputs.InputError("scripted input error")
        return real(cur, f5_run_id, **kw)
    monkeypatch.setattr(jobs, "compute_validation", failing)
    code, rep = wd.wake()
    assert code == EXIT_ATTENTION and "validation_failed" in attention(rep)
    assert X.state_of(w, 943002) == "persisted" and store.current_state(
        w, store.item_by_key(w, keys.validate(bad)["natural_key"])["id"])["state"] == "failed"
    X.designate(env, rep["f6"]["configuration"]["configuration_id"])
    code, rep = wd.wake()                          # never retried in a loop; reconciliation waits (global first)
    assert rep["f6"]["validation"]["attempted"] == 0 and rep["f6"]["reconcile"]["state"] == "deferred"
    assert q(w, "select count(*) from financial_f6_jobs where kind = 'validate' and f5_run_id = %s", (bad,)) == [(1,)]
    for key, reason, code_ in ((keys.validate(bad)["natural_key"], "short", "reason"),
                               ("validate:00000000-0000-0000-0000-000000000000", "a reason long enough", "item"),
                               (keys.document(943001, X.H.path_of(943001))["natural_key"], "a reason long enough",
                                "requeue")):
        with pytest.raises(OpsRefused) as ei:
            requeue(w, key, reason)
        assert ei.value.codes == [code_]
    monkeypatch.setattr(jobs, "compute_validation", real)
    out = requeue(w, keys.validate(bad)["natural_key"], "investigated: the input error is fixed; validate again")
    assert (out["from"], out["to"]) == ("failed", "pending")
    code, rep = wd.wake()
    assert rep["f6"]["validation"]["succeeded"] == 1 and [s for _, s in rep["f6"]["reconcile"]["runs"]] == [
        "succeeded"]


# ================================================================================================ late evidence (M4)

def test_m1_late_evidence_through_the_late_pass_promotion_and_requeues_with_nothing_downloaded_again(env, tmp_path):
    a, b = Filing(944001, "facts", listed=True), Filing(944002, "facts", day=3)          # B: path prefix only
    wd = pilot_world(env, tmp_path, [a, b])
    w = env.conn()
    to_designation(wd)
    wd.wake()
    assert states_of(w, [a, b]) == {944001: "reconciled", 944002: "validated"}
    before, calls = counts(w), len(wd.cdn.calls)
    comb = q(w, "select issuer_id::text from issuers where cse_sec_id = %s", (X.COMB_SEC_ID,))[0][0]
    # owner-approved late evidence: COMB's listing now names B (the operator re-queues the listing; the runner's
    # discovery slice re-requests it: one JSON request, no document), then the late pass re-links (7.4 step 5)
    requeue(w, keys.listing(X.COMB)["natural_key"], "late evidence: re-request COMB's listing (owner-approved)")
    wd.route = route_for([a, b], listed=[a, b])
    code, rep = wd.wake()
    assert rep["slice"]["kind"] == "discovery"
    res = actions.late_pass(w, env.clock.wall())
    assert res["item"] and q(w, "select status, basis from filing_issuer_links where cse_filing_id = %s order by id "
                                "desc limit 1", (b.fid,)) == [("evidenced", "both")]
    code, rep = wd.wake()                          # M4: B lost its canonical validation run -> needs_validation
    assert rep["f6"]["promotion"] == {"needs_validation": 1} and "awaiting_requeue" in attention(rep)
    run_b = X.runs_of(w, b.fid)[0]
    requeue(w, keys.validate(run_b)["natural_key"], "late evidence: B's issuer decision changed (M4)")
    code, rep = wd.wake()
    assert rep["f6"]["validation"]["succeeded"] == 1 and rep["f6"]["promotion"] == {"validated": 1}
    for subject in (keys.reconcile(comb), keys.reconcile(None)):
        requeue(w, subject["natural_key"], "late evidence: reconcile COMB again now that B is admissible")
    code, rep = wd.wake()
    assert rep["f6"]["promotion_after_reconcile"] == {"reconciled": 1}
    assert X.state_of(w, b.fid) == "reconciled"
    after = counts(w)
    assert {k: after[k] for k in after if k != "backfill_request_attempts"} == \
        {k: before[k] for k in before if k != "backfill_request_attempts"}           # no F-stage row added
    assert len(wd.cdn.calls) == calls and doc_attempts(w) == 2                         # nothing downloaded again


# ================================================================================================ the stop signal

def test_t1_sigterm_unwinds_the_wake_up_which_is_recorded_stopped_and_the_handler_is_restored(env, tmp_path,
                                                                                            monkeypatch):
    wd = World(env, [], tmp_path, master=False, sweep=False, arm=False)
    w = env.conn()

    def signalled(*a, **k):
        os.kill(os.getpid(), signal.SIGTERM)      # delivered to this (the main) thread: HB-4's handler raises
        raise AssertionError("SIGTERM did not unwind the wake-up")
    monkeypatch.setattr(hb5_validation, "validate_pending", signalled)
    with H.sentinel_sigterm() as sentinel:
        with pytest.raises(SystemExit) as ei:
            wd.wake(install_sigterm=True)
        assert ei.value.code == 128 + int(signal.SIGTERM)
        assert signal.getsignal(signal.SIGTERM) is sentinel                         # restored
    last = runner_wakeups(w)[-1]
    assert (last[1], last[3]) == ("released", "stopped")


# ================================================================================================ the gates

def test_g1_disarmed_the_runner_does_f6_bookkeeping_only_and_contacts_nobody(env, tmp_path):
    wd = World(env, [], tmp_path, master=False, sweep=False, arm=False)
    w = env.conn()
    code, rep = wd.wake(runtime=wd.runtime(clock_synchronized=lambda: None))   # disarmed: the clock is not needed
    assert (code, rep["result"]) == (EXIT_OK, "idle") and rep["slice"]["reason"].startswith("disarmed")
    assert rep["audit"]["due"] is False and counts(w)["backfill_request_attempts"] == 0
    assert rep["arming"] == {"id": None, "armed": False, "armed_stages": []}


def test_g2_a_block_stops_every_stage_until_the_owner_acknowledges_it_and_the_item_is_resumed(env, tmp_path):
    wd = World(env, [Filing(945001, "facts", listed=True)], tmp_path, armed_stages=("HB-S1", "HB-S2"))
    w = env.conn()
    good = wd.route

    def blocking(url, params):
        if url.endswith("financials") and params["symbol"] == X.COMB:
            return dict(status=403, body=b"{}", headers={"Content-Type": "application/json"})
        return good(url, params)
    wd.route = blocking
    code, rep = wd.wake()
    assert code == EXIT_BLOCKED and rep["result"] == "blocked" and rep["blocks"]["phase2"]
    item = store.item_by_key(w, keys.listing(X.COMB)["natural_key"])
    assert store.current_state(w, item["id"])["state"] == "blocked"
    before = q(w, "select count(*) from backfill_wakeups")[0][0]
    code, rep = wd.wake()                          # every stage stays stopped: nothing but the wake-up row
    assert code == EXIT_BLOCKED and "bookkeeping" not in rep and "slice" not in rep
    assert q(w, "select count(*) from backfill_wakeups")[0][0] == before + 1
    code, st = status_of(w, env)
    assert code == EXIT_BLOCKED and "block" in [a[0] for a in st["alerts"]] and st["blocks"]["phase2"]
    with pytest.raises(Exception, match="not acknowledged"):     # HB-1's guard: only after the owner's acknowledgement
        actions.resume(w, item["natural_key"], "the owner reviewed the block")
    block_id = q(w, "select block_id from backfill_block_state order by block_id desc limit 1")[0][0]
    decisions.acknowledge_block(env.owner_conn(), block_id, "the owner reviewed the 403; G-1 review done", "tester")
    out = actions.resume(w, item["natural_key"], "block acknowledged by the owner: resume the listing")
    assert (out["from"], out["to"]) == ("blocked", "pending")
    events = q(w, "select count(*) from backfill_item_events where item_id = %s", (item["id"],))[0][0]
    with pytest.raises(OpsRefused) as refused:     # only a blocked item is resumed (HB-1's table backs HB-6's check)
        actions.resume(w, item["natural_key"], "a second resume of a pending item")
    assert refused.value.codes == ["resume"]
    assert q(w, "select count(*) from backfill_item_events where item_id = %s", (item["id"],))[0][0] == events
    wd.route = good
    code, rep = wd.wake()
    assert rep["slice"]["kind"] == "discovery" and store.current_state(w, item["id"])["state"] == "succeeded"


def test_g3_without_hb_p1_every_discovery_entry_point_refuses_and_nothing_is_requested(env, tmp_path):
    wd = World(env, [], tmp_path, master=False, armed_stages=("HB-S1", "HB-S2"))
    w = env.conn()
    code, rep = wd.wake()
    assert code == EXIT_REFUSED and rep["result"] == "refused"
    assert {r[0] for r in rep["refused"]} == {"identity_pass", "discovery"}
    assert counts(w)["backfill_request_attempts"] == 0 and q(w, "select count(*) from backfill_leases") == [(0,)]


def test_g4_an_armed_wake_up_refuses_on_an_unsynchronised_or_backwards_clock(env, tmp_path):
    wd = World(env, [], tmp_path, armed_stages=("HB-S1",))
    code, rep = wd.wake(runtime=wd.runtime(clock_synchronized=lambda: False))
    assert code == EXIT_REFUSED and rep["refused"][0][0] == "clock" and "bookkeeping" not in rep
    code, rep = wd.wake()
    assert code in (EXIT_OK, EXIT_ATTENTION)
    env.clock.t -= 3600                            # the clock goes back an hour
    code, rep = wd.wake()
    assert code == EXIT_REFUSED and "went backwards" in rep["refused"][0][1]


def test_g5_hb_s3_without_a_sample_and_a_wrong_user_agent_are_refused_before_any_request(env, tmp_path):
    fs = [Filing(946001, "facts", listed=True)]
    wd = World(env, fs, tmp_path, armed_stages=("HB-S1", "HB-S2", "HB-S3"),
               expected_requests={"feed": 2, "listings": 7, "documents": 1})
    w = env.conn()
    wd.until(lambda code, r: (r.get("bookkeeping") or {}).get("closure_pass"))
    code, rep = wd.wake()
    assert code == EXIT_REFUSED and rep["refused"][0][0] == "pilot_sample" and doc_attempts(w) == 0
    wd2 = World(env, fs, tmp_path, master=False, sweep=False, **pilot_arming(fs))
    bad = wd2.runtime()
    bad.env = dict(bad.env, CSE_CAPTURE_CONTACT_EMAIL="someone-else@example.org")
    code, rep = wd2.wake(runtime=bad)
    assert code == EXIT_REFUSED and rep["slice"]["result"] == "refused"
    assert "user_agent" in {c for c, _ in rep["slice"]["refused"]} and doc_attempts(w) == 0


def test_g6_the_real_preflight_passes_as_the_worker_and_a_changed_pin_refuses_before_anything_is_written(
        env, tmp_path, monkeypatch):
    wd = World(env, [], tmp_path, master=False, sweep=False, arm=False)
    w = env.conn()
    code, rep = wd.wake(preflight=None, stage_preflight=None)
    assert (code, rep["result"]) == (EXIT_OK, "idle")
    monkeypatch.setitem(preflight.PINNED_FILES, "worker/backfill_f6/validation.py", "0" * 64)
    tables = q(w, "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = "
                  "'public' and c.relkind = 'r' order by 1")
    before = {t: q(w, f"select count(*) from {t}")[0][0] for (t,) in tables}
    code, rep = wd.wake(preflight=None, stage_preflight=None)
    assert code == EXIT_REFUSED and any("validation.py changed" in p for _, p in rep["refused"])
    after = {t: q(w, f"select count(*) from {t}")[0][0] for (t,) in tables}
    assert {t for t in before if after[t] != before[t]} == {"backfill_wakeups"}
    monkeypatch.setitem(preflight.PINNED_FILES, "worker/backfill_f6/validation.py",
                        preflight.mig.file_sha256(os.path.join(REPO, "worker", "backfill_f6", "validation.py")))
    monkeypatch.setitem(hb5_preflight.PINNED_FILES, "worker/backfill_documents/worker.py", "0" * 64)
    code, rep = wd.wake(preflight=None, stage_preflight=None)                    # HB-5's pins of HB-4, in the composite
    assert code == EXIT_REFUSED and any("backfill_documents/worker.py changed" in p for _, p in rep["refused"])


def test_g7_a_low_or_unreadable_database_disk_refuses_before_any_work_and_the_dump_destination_only_alerts(env,
                                                                                                          tmp_path):
    from collections import namedtuple
    from worker.backfill_ops import DB_DISK_MIN_FREE_BYTES, status
    usage_of = namedtuple("usage", "total used free")
    db_path, dump_path = str(tmp_path / "pgdata"), str(tmp_path / "dumps")
    free = {"db": DB_DISK_MIN_FREE_BYTES - 1, "dump": 10 ** 15}

    def usage(path):
        return usage_of(0, 0, free["db"] if path == db_path else free["dump"])

    def broken(path):
        if path == db_path:
            raise OSError("no such device")
        return usage(path)
    wd = World(env, [Filing(951001, "facts", listed=True)], tmp_path, armed_stages=("HB-S1", "HB-S2"))
    w = env.conn()
    before = counts(w)
    code, rep = wd.wake(db_disk_path=db_path, dump_path=dump_path, disk_usage=usage)   # one byte under the floor
    assert code == EXIT_REFUSED and rep["refused"][0][0] == "db_disk" and "bookkeeping" not in rep
    assert counts(w) == before and rep["disk"]["database_disk"]["free_bytes"] == DB_DISK_MIN_FREE_BYTES - 1
    last = runner_wakeups(w)[-1]
    assert last[3] == "refused" and last[4]["disk"]["database_disk"]["free_bytes"] == DB_DISK_MIN_FREE_BYTES - 1
    code, rep = wd.wake(db_disk_path=db_path, dump_path=dump_path, disk_usage=broken)  # unreadable: fail closed
    assert code == EXIT_REFUSED and "cannot be checked" in rep["refused"][0][1] and counts(w) == before
    free.update(db=DB_DISK_MIN_FREE_BYTES, dump=1)          # at the floor: work runs; a full dump disk only alerts
    code, rep = wd.wake(db_disk_path=db_path, dump_path=dump_path, disk_usage=usage)
    assert code == EXIT_ATTENTION and "dump_disk" in attention(rep) and "bookkeeping" in rep
    assert rep["disk"]["dump_destination"]["free_bytes"] == 1 and rep["disk"]["database_bytes"] == rep["db_bytes"]
    assert runner_wakeups(w)[-1][4]["disk"]["dump_destination"]["free_bytes"] == 1      # recorded with the wake-up
    code, st = status.status(w, env.clock.wall(), db_disk_path=str(tmp_path / "missing"),
                             dump_path=str(tmp_path / "missing-too"))
    assert code == EXIT_ATTENTION and {"db_disk", "dump_disk"} <= {a[0] for a in st["alerts"]}


# ================================================================================================ reports and checkpoints

def test_a1_the_pilot_report_and_the_reports_are_written_outside_the_repository_once(env, tmp_path):
    fs = [Filing(947001, "facts", listed=True), Filing(947002, "unmapped", listed=True, day=3)]
    wd = pilot_world(env, tmp_path, fs)
    w = env.conn()
    wd.until(lambda code, r: "pilot_complete" in attention(r))
    arming = store.arming_in_force(w)
    rep = pilot.report(w, arming, [f.fid for f in fs], env.clock.wall())
    assert (rep["pilot_filings"], rep["document_items"], rep["unfinished"]) == (2, 2, [])
    assert rep["failure_rates"] == {"persisted": {"items": 1, "rate": 0.5}, "validated": {"items": 1, "rate": 0.5}} \
        or set(rep["failure_rates"]) <= {"persisted", "validated"}
    assert sum(r["n"] for r in rep["requests"] if r["request_class"] == "document") == doc_attempts(w)
    assert rep["time_per_document_seconds"]["documents"] == 2 and rep["funnel"]["filings"] == 2
    assert rep["e1_disappeared_since_f0"] == pilot.E1_NOT_MEASURED
    assert rep["database_growth"]["bytes_at_first_pilot_wakeup"] and rep["templates"]
    hints = rep["template_hint_vs_actual"]                  # the feed names COMMERCIAL BANK: both hinted bank_like
    assert list(hints) == ["bank_like"] and sum(hints["bank_like"].values()) == 2
    assert set(hints["bank_like"]) <= {t["template"] for t in rep["templates"]}         # F4's own template, per filing
    out = tmp_path / "out"
    out.mkdir()
    written = reports.write_all(w, str(out), (arming["window_first_date"], arming["window_last_date"]),
                                pilot_report=rep)
    assert set(written) == {"anomalies", "issuer_evidence", "requests", "universe", "pilot"}
    assert all(os.path.exists(p) and p.startswith(str(out)) for p in written.values())
    assert "user_agent" not in open(written["requests"], encoding="utf-8").read()
    with pytest.raises(F6Refused):
        reports.write_all(w, os.path.join(REPO, "tests"), (arming["window_first_date"], arming["window_last_date"]))
    exports = sorted(os.listdir(wd.reports))
    assert exports and all(e.startswith("coverage-") for e in exports)
    res = hb5_audit.run(w, preflight=X.no_preflight)
    first = reports.export_coverage(res, str(out))
    assert reports.export_coverage(res, str(out)) == first and os.path.exists(first)      # the same snapshot: once


def test_c1_hb_s4_runs_in_checkpoint_blocks_recorded_through_the_owner_path_and_stops_at_the_block(env, tmp_path):
    fs = [Filing(948001 + i, "facts", listed=True, day=2 + i) for i in range(3)]
    cp = decisions.Checkpoint("dump-7", "restore-8", True, "checkpoint before the first bulk block (test)")
    d = decisions.arming_decision(stages=("HB-S1", "HB-S2", "HB-S4"), window_first=H.W[0], window_last=H.W[1],
                                  user_agent=F.USER_AGENT, host=F.HOST, g1_reference="G-1 (test)",
                                  note="owner arms a bulk block of two documents", confirm_stop_conditions=True,
                                  expected_requests={"feed": 2, "listings": 7, "documents": 2}, checkpoint=cp,
                                  this_host=F.HOST)
    with pytest.raises(OpsRefused) as ei:                                  # the worker is not the owner path
        decisions.record_arming(env.conn(), d)
    assert ei.value.codes == ["owner_path"]
    wd = World(env, fs, tmp_path, arm=False)
    arming_id = decisions.record_arming(env.owner_conn(), d, "tester")
    w = env.conn()
    assert store.arming_in_force(w)["stop_conditions"][-1]["backup_checkpoint"]["dump_backup_run"] == "dump-7"
    out = wd.until(lambda code, r: (r.get("slice") or {}).get("stage") == "HB-S4")
    assert out[-1][1]["slice"]["max_items"] == 2
    assert ops_runner.documents_under_arming(w, arming_id) == 2 and doc_attempts(w, 948003) == 0
    code, rep = wd.wake()
    assert "checkpoint_due" in attention(rep) and rep["slice"] == {"due": False, "stage": "HB-S4", "documents": 2,
                                                                   "block": 2}
    assert doc_attempts(w, 948003) == 0


# ================================================================================================ boundaries

def test_b1_f64_units_are_never_wrapped_and_f6_work_never_holds_p2s_lock_or_a_lease(env, tmp_path, monkeypatch):
    fs = [Filing(949001, "facts", listed=True)]
    wd = pilot_world(env, tmp_path, fs)
    logged = X.track_transactions(env, ("financial_validation_runs", "financial_reconciliation_batches",
                                        "backfill_item_events"), prefix="hb6")
    seen = []
    real = hb5_validation.validate_pending

    def watched(conn, **kw):
        seen.append((store.holds_cse_lock(conn), store.active_lease(conn)))
        return real(conn, **kw)
    monkeypatch.setattr(hb5_validation, "validate_pending", watched)
    to_designation(wd)
    wd.wake()
    assert seen and all(lock is False and lease is None for lock, lease in seen)
    txid = {}
    for rel, t in logged():
        txid.setdefault(rel, set()).add(t)
    f6 = txid["financial_validation_runs"] | txid["financial_reconciliation_batches"]
    assert f6 and not f6 & txid["backfill_item_events"]                          # F8 P-3: F6.4's own units


def test_b2_a_disarmed_wake_up_writes_only_its_own_wake_up_row(env, tmp_path):
    wd = World(env, [], tmp_path, master=False, sweep=False, arm=False)
    w = env.conn()
    tables = [t for (t,) in q(w, "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                                 "where n.nspname = 'public' and c.relkind = 'r' order by 1")]
    before = {t: q(w, f"select count(*) from {t}")[0][0] for t in tables}
    wd.wake()
    after = {t: q(w, f"select count(*) from {t}")[0][0] for t in tables}
    assert {t: after[t] - before[t] for t in tables if after[t] != before[t]} == {"backfill_wakeups": 1}


# ================================================================================================ the command line

def cli_env(env, user):
    kw = env.conn_kwargs(user)
    return {"CSE_DB_HOST": kw["host"], "CSE_DB_PORT": str(kw["port"]), "CSE_DB_NAME": kw["dbname"],
            "CSE_DB_USER": user, "CSE_BACKUP_ROOT": str(env.backup), "CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT,
            "CSE_OPERATOR": "tester"}


def test_c2_the_command_line_status_plan_requeue_run_and_the_owners_arming(env, tmp_path, capsys):
    World(env, [], tmp_path, master=False, sweep=False, arm=False)
    rt = env.runtime()
    worker, owner_env = cli_env(env, "cse_worker"), cli_env(env, "cse_migrator")
    assert cli.main(["status"], env=worker, runtime=rt) in (EXIT_OK, EXIT_ATTENTION)
    status = json.loads(capsys.readouterr().out)
    assert status["armed_stages"] == [] and status["blocks"] is None and "user_agent" not in cli.opsstatus.PUBLIC_ARMING
    assert cli.main(["plan"], env=worker, runtime=rt) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["armed_stages"] == []
    assert cli.main(["requeue", "--item", "validate:nothing", "--reason", "a reason long enough"], env=worker,
                    runtime=rt) == EXIT_REFUSED
    capsys.readouterr()
    arm = ["arm", "--stages", "HB-S1", "--window-first", H.W[0].isoformat(), "--window-last", H.W[1].isoformat(),
           "--user-agent", F.USER_AGENT, "--host", platform.node(), "--g1-reference", "G-1 (test)",
           "--note", "the owner arms the identity import", "--confirm-stop-conditions", "--expected-listings", "7"]
    assert cli.main(arm, env=worker, runtime=rt) == EXIT_REFUSED                     # the worker is not the owner
    assert cli.main(arm, env=owner_env, runtime=rt) == EXIT_OK
    armed = json.loads(capsys.readouterr().out)
    w = env.conn()
    assert store.arming_in_force(w)["id"] == armed["arming_id"] and armed["armed_stages"] == ["HB-S1"]
    assert cli.main(["run"], env=worker, runtime=rt) in (EXIT_OK, EXIT_ATTENTION, EXIT_REFUSED)
    ran = json.loads(capsys.readouterr().out)
    assert ran["tool_version"] == "hb.ops.1" and ran["wakeup_id"]
    assert cli.main(["disarm", "--note", "the owner disarms after the test"], env=owner_env, runtime=rt) == EXIT_OK
    assert store.arming_in_force(w)["armed"] is False
