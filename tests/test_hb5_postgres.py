"""
Phase 2 HB-5 (F6 orchestration and audit) against REAL PostgreSQL 17, with migration 0016's real guards, F6.4's frozen
jobs and the frozen HB-1 to HB-4 upstream (tests/hb5_support.py, on tests/hb4_support.py). Every CSE response comes
from a scripted transport or a scripted cdn.cse.lk: nothing contacts the network.

    validation     one validate:<run> item per pending run naming its F6.4 job and validation run; bounded batches;
                   a busy F6.4 lock records nothing; a failed validation is final until an operator re-queue
    configuration  registered only over the one armed version tuple; the designation is the owner's
    reconcile      deferred while F6.4 has a pending validation; per issuer once its in-window filings are final
                   (a superseded path or a filing that left W never holds it back); the final full pass once; a busy
                   lock records nothing; a failed pass is final until an operator re-queue
    promotion      persisted -> validated -> reconciled from F6 evidence only; late evidence (M4) through
                   needs_validation and operator re-queues, with nothing retrieved again
    audit          the funnel places every synthetic filing at the first stage it fails, at every phase; the snapshot
                   digest, the L11 / L10 records, the reader, a failed audit; the Phase 2 detectors
    boundaries     no F6.4 unit is wrapped (F8 P-3); HB-5 writes no frozen table and no CSE ledger record; reports
                   only outside the repository; the preflight

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb5_postgres.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb2_fakes as F  # noqa: E402
import hb4_support as H  # noqa: E402
import hb5_support as X  # noqa: E402
from hb4_support import q  # noqa: E402
from hb5_support import Filing  # noqa: E402
from worker.backfill_discovery import identity  # noqa: E402
from worker.backfill_documents import evidence, gate, planning  # noqa: E402
from worker.backfill_f6 import anomalies, audit, configuration, preflight, promotion, reconcile, reports, \
    validation  # noqa: E402
from worker.backfill_f6.errors import F6Refused  # noqa: E402
from worker.financial_backfill import keys, store  # noqa: E402
from worker.financial_truth import inputs, reconciliation  # noqa: E402
from worker.financial_truth_store import codec, jobs  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
NP = X.no_preflight
W = (date(2025, 3, 1), date(2025, 4, 30))


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
    base = tempfile.mkdtemp(prefix="hb5", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def env(cluster, tmp_path, monkeypatch):
    e = H.Env(cluster, tmp_path, monkeypatch)
    yield e
    e.close()


def events(w, subject):
    item = store.item_by_key(w, subject["natural_key"])
    return [] if item is None else store.events(w, item["id"])


def comb_issuer(w):
    return q(w, "select issuer_id::text from issuers where cse_sec_id = %s", (X.COMB_SEC_ID,))[0][0]


def designate_all(env, w):
    """Validate everything pending, register the configuration, and the owner designates it."""
    validation.validate_pending(w, preflight=NP)
    reg = configuration.register(w, preflight=NP)
    X.designate(env, reg["configuration_id"])
    return reg["configuration_id"]


def job(w, job_id):
    return q(w, "select kind, coalesce(f5_run_id::text, scope), state from financial_f6_job_state where job_id = %s",
             (str(job_id),))[0]


# ================================================================================================ validation

def test_v1_each_pending_run_is_one_validate_item_naming_its_f64_job_and_validation_run(env):
    fs = [Filing(911001, "facts", listed=True), Filing(911002, "facts"), Filing(911003, "unmapped", listed=True)]
    X.world(env, fs)
    w = env.conn()
    runs = {f.fid: X.runs_of(w, f.fid)[0] for f in fs}
    assert sorted(validation.pending_runs(w)) == sorted(runs.values())
    rep = validation.validate_pending(w, preflight=NP)
    assert rep["attempted"] == 3 and rep["failed"] == [] and rep["refused"] is None
    assert sorted((s["f5_run_id"], s["job_state"]) for s in rep["succeeded"]) == sorted(
        (r, "succeeded") for r in runs.values())
    for run in runs.values():
        ev = events(w, keys.validate(run))
        assert [(e["state"], e["action"]) for e in ev] == [("pending", "create"), ("succeeded", "record")]
        assert job(w, ev[-1]["f6_job_id"]) == ("validate", run, "succeeded")
        assert q(w, "select validation_run_key, job_id::text from financial_validation_run_current where f5_run_id = "
                    "%s", (run,)) == [(ev[-1]["validation_run_key"], str(ev[-1]["f6_job_id"]))]
    assert validation.pending_runs(w) == []
    before = X.f6_jobs(w)
    again = validation.validate_pending(w, preflight=NP)
    assert again == {"pending": 0, "attempted": 0, "succeeded": [], "failed": [], "refused": None,
                     "awaiting_requeue": [], "recovered": []}
    assert X.f6_jobs(w) == before
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'validate'") == [(3,)]


def test_v2_a_batch_is_bounded_and_the_next_one_resumes_without_duplicates(env):
    X.world(env, [Filing(912001 + i, "facts", listed=True) for i in range(3)])
    w = env.conn()
    first = validation.validate_pending(w, limit=2, preflight=NP)
    assert (first["pending"], first["attempted"], len(validation.pending_runs(w))) == (3, 2, 1)
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'validate'") == [(2,)]
    second = validation.validate_pending(w, limit=2, preflight=NP)
    assert (second["pending"], second["attempted"], validation.pending_runs(w)) == (1, 1, [])
    assert q(w, "select count(*), count(distinct natural_key) from backfill_work_items where item_kind = "
                "'validate'") == [(3, 3)]
    assert q(w, "select count(*) from financial_f6_job_state where kind = 'validate'") == [(3,)]
    for bad in (0, -1, True, "2"):
        with pytest.raises(ValueError):
            validation.validate_pending(w, limit=bad, preflight=NP)


def test_v3_a_busy_f64_lock_records_nothing_and_stops_the_batch(env):
    X.world(env, [Filing(913001, "facts", listed=True), Filing(913002, "facts", listed=True)])
    w, other = env.conn(), env.conn()
    assert jobs.lock_exclusive(other)                    # F6.4's own lock, held exclusively elsewhere (a reconcile)
    rep = validation.validate_pending(w, preflight=NP)
    assert rep["attempted"] == 1 and rep["succeeded"] == [] and rep["refused"]["reason"] == "busy"
    assert [e["state"] for e in events(w, keys.validate(rep["refused"]["f5_run_id"]))] == ["pending"]
    assert q(w, "select state from financial_f6_job_state where kind = 'validate'") == [("refused",)]
    jobs.unlock(other, shared=False)
    rep = validation.validate_pending(w, preflight=NP)
    assert rep["attempted"] == 2 and len(rep["succeeded"]) == 2 and rep["refused"] is None


def test_v4_a_failed_validation_is_final_and_defers_every_reconcile_until_an_operator_requeue(env, monkeypatch):
    X.world(env, [Filing(914001, "facts", listed=True), Filing(914002, "facts", listed=True)])
    w = env.conn()
    bad = X.runs_of(w, 914002)[0]
    real = jobs.compute_validation

    def failing(cur, f5_run_id, **kw):
        if str(f5_run_id) == bad:
            raise inputs.InputError("scripted input error")
        return real(cur, f5_run_id, **kw)
    monkeypatch.setattr(jobs, "compute_validation", failing)
    rep = validation.validate_pending(w, preflight=NP)
    assert len(rep["succeeded"]) == 1 and [f["f5_run_id"] for f in rep["failed"]] == [bad]
    ev = events(w, keys.validate(bad))[-1]
    assert ev["state"] == "failed" and "F6.4 validate error" in ev["reason"] and "scripted input error" in ev["reason"]
    assert job(w, ev["f6_job_id"]) == ("validate", bad, "failed")
    again = validation.validate_pending(w, preflight=NP)                # never retried on its own
    assert again["attempted"] == 0 and again["awaiting_requeue"] == [{"f5_run_id": bad, "state": "failed"}]
    assert q(w, "select count(*) from financial_f6_jobs where kind = 'validate' and f5_run_id = %s", (bad,)) == [(1,)]
    reg = configuration.register(w, preflight=NP)
    X.designate(env, reg["configuration_id"])
    assert reconcile.reconcile_ready(w, preflight=NP) == {"state": "deferred", "reason": "validations_pending",
                                                         "pending": 1, "configuration_id": reg["configuration_id"]}
    assert q(w, "select count(*) from financial_f6_jobs where kind = 'reconcile'") == [(0,)]
    monkeypatch.setattr(jobs, "compute_validation", real)
    X.requeue(w, keys.validate(bad), "investigated: the input error is fixed; validate again")
    fixed = validation.validate_pending(w, preflight=NP)
    assert [s["f5_run_id"] for s in fixed["succeeded"]] == [bad]
    assert [r["state"] for r in reconcile.reconcile_ready(w, preflight=NP)["runs"]] == ["succeeded"]


def test_v5_a_job_whose_ledger_event_was_never_written_is_recovered_from_f64s_rows_with_no_new_job(env, monkeypatch):
    X.world(env, [Filing(931001, "facts", listed=True)])
    w = env.conn()
    run = X.runs_of(w, 931001)[0]
    real = store.append_event

    def dies(conn, item_id, state, action, **kw):            # the process ends after F6.4's job, before the event
        if (state, action) == ("succeeded", "record"):
            raise SystemExit("scripted: the process ended before the ledger event")
        return real(conn, item_id, state, action, **kw)
    monkeypatch.setattr(store, "append_event", dies)
    with pytest.raises(SystemExit):
        validation.validate_pending(w, preflight=NP)
    monkeypatch.setattr(store, "append_event", real)
    assert [e["state"] for e in events(w, keys.validate(run))] == ["pending"]
    assert validation.pending_runs(w) == []                      # F6.4 committed its validation run and its job
    before = X.f6_jobs(w)
    vrk, job_id = q(w, "select validation_run_key, job_id::text from financial_validation_run_current where "
                       "f5_run_id = %s", (run,))[0]
    rep = validation.validate_pending(w, preflight=NP)
    assert rep["attempted"] == 0 and rep["recovered"] == [{"f5_run_id": run, "job_id": job_id}]
    assert X.f6_jobs(w) == before                                # recorded from evidence: no new job
    ev = events(w, keys.validate(run))
    assert [(e["state"], e["action"]) for e in ev] == [("pending", "create"), ("succeeded", "record")]
    assert (ev[-1]["validation_run_key"], str(ev[-1]["f6_job_id"])) == (vrk, job_id)
    assert job(w, job_id) == ("validate", run, "succeeded")
    assert validation.validate_pending(w, preflight=NP)["recovered"] == []


# ================================================================================================ configuration

def test_c1_the_configuration_is_registered_over_exactly_the_armed_tuple_and_never_designated_by_hb5(env):
    fs = [Filing(915001, "facts", listed=True)]
    X.world(env, fs, process_documents=False)
    w = env.conn()
    with pytest.raises(F6Refused) as ei:
        configuration.register(w, preflight=NP)
    assert ei.value.codes == ["no_runs"]
    X.process(env, fs)
    reg = configuration.register(w, preflight=NP)
    assert (reg["state"], reg["designated"], reg["designated_is_this"]) == ("inserted", None, False)
    with w.cursor() as cur:
        cfg = jobs.load_configuration(cur, reg["configuration_id"])
    w.rollback()
    f3, f4, f5_ = configuration.backfill_tuple()
    assert (cfg.accepted_f3, cfg.accepted_f4, cfg.accepted_f5) == ((f3,), (f4,), (f5_,))
    again = configuration.register(w, preflight=NP)
    assert (again["state"], again["configuration_id"]) == ("already_present", reg["configuration_id"])
    with pytest.raises(jobs.OwnerPathRequired):                         # the worker cannot designate
        jobs.designate(w, reg["configuration_id"], "the worker tries to designate it")
    with pytest.raises(F6Refused) as ei:
        reconcile.reconcile_ready(w, preflight=NP)
    assert ei.value.codes == ["designation"]
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'reconcile'") == [(0,)]
    X.designate(env, reg["configuration_id"])
    assert configuration.register(w, preflight=NP)["designated_is_this"] is True


def test_c2_a_second_version_tuple_is_the_owners_decision_and_nothing_is_registered(env):
    X.world(env, [Filing(916001, "facts", listed=True)])
    X.persist_under_other_tuple(env, 916001, sha="cd" * 32, word="25.03.0", text_release="24.02.0")  # F4 differs
    X.persist_under_other_tuple(env, 916001, sha="ce" * 32, word="24.02.0", text_release="25.03.0")  # F3 differs
    w = env.conn()
    with pytest.raises(F6Refused) as ei:
        configuration.register(w, preflight=NP)
    assert ei.value.codes == ["version_tuples"] and "25.03.0" in str(ei.value)
    assert q(w, "select count(*) from financial_reconciliation_configurations") == [(0,)]
    # the funnel counts only F5 runs of the armed tuple. The F4-only difference leaves an ARMED F3 row without an
    # armed F5 run: a document up to stage 4 (section 18.2: "or an F3 row"); the F3 difference is not counted at all
    validation.validate_pending(w, preflight=NP)
    [row] = [r for r in audit.run(w, preflight=NP)["coverage"]["table"] if r["cse_filing_id"] == 916001]
    assert (row["documents"], row["reached"], row["stop"]) == (2, 8, [9, "no_designated_configuration"])
    assert row["versions"] == "|".join(evidence.armed_versions()[k] for k in evidence.VERSION_COLUMNS)


# ================================================================================================ reconcile

def test_r1_an_issuer_waits_while_one_of_its_in_window_filings_is_not_final(env):
    a, b = Filing(917001, "facts", listed=True), Filing(917002, "facts", listed=True, cdn=503, day=3)
    X.world(env, [a, b])
    w = env.conn()
    assert (X.state_of(w, a.fid), X.state_of(w, b.fid)) == ("persisted", "retry_wait")
    designate_all(env, w)
    comb = comb_issuer(w)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert rep["runs"] == [] and rep["waiting"] == [{"issuer_id": comb, "open_filings": 1}]
    assert rep["final_pass"] is None and rep["readiness"]["open_filings"] == 1
    assert q(w, "select count(*) from financial_f6_jobs where kind = 'reconcile'") == [(0,)]
    X.process(env, [Filing(917002, "facts", listed=True, day=3)])       # the next slice retrieves it
    assert X.state_of(w, b.fid) == "persisted"
    validation.validate_pending(w, preflight=NP)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert [r["state"] for r in rep["runs"]] == ["succeeded"] and rep["final_pass"]["state"] == "succeeded"


def test_r2_a_superseded_path_or_a_filing_that_left_w_never_holds_its_issuer_back(env):
    a = Filing(918001, "facts", listed=True)
    c = Filing(918002, "facts", day=3)                   # unlisted: COMB's by its path prefix alone
    e = Filing(918003, "facts", day=4)                   # unlisted
    g = Filing(918004, "facts", day=5, dated=False)      # undated at planning: excluded (window_undetermined)
    h = Filing(918005, "facts", day=6, dated=False, path=None)       # undated and without a path at planning
    X.world(env, [a, c, e, g, h], process_documents=False)
    w = env.conn()
    planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    old_c = X.item_of(w, c.fid)
    assert X.state_of(w, g.fid) == X.state_of(w, h.fid) == "excluded"
    # F1 sees C under a new path, E dated after W, G dated inside W (same path), H dated inside W with a path: a
    # re-requested feed month
    c2 = Filing(918002, "facts", day=3, path=H.path_of(918002, epoch=1740900007777))
    e2 = dict(e.feed(), uploadedDate="02 May 2025 10:00:00 AM")
    h2 = Filing(918005, "facts", day=6)
    X.requeue(w, keys.feed_window(2025, 3), "an F1 metadata change for the test (owner-run re-request)")
    X.rediscover(env, [a, c2, e2, Filing(918004, "facts", day=5), h2], listed=[a])
    X.process(env, [a, c2, h2])                          # H: a new item for its new path version (HB-4's own rule)
    assert store.current_state(w, old_c["id"])["state"] == "pending"     # superseded: never claimed, never final
    assert (X.state_of(w, a.fid), X.state_of(w, c.fid), X.state_of(w, g.fid), X.state_of(w, h.fid)) == (
        "persisted", "persisted", "excluded", "persisted")
    assert q(w, "select s.state from backfill_item_state s join backfill_work_items i on i.id = s.item_id where "
                "i.cse_filing_id = %s", (e.fid,)) == [("pending",)]          # left W: never claimed
    designate_all(env, w)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert rep["readiness"]["filings"] == 4 and rep["readiness"]["open_filings"] == 0     # A, C, G, H
    assert [r["state"] for r in rep["runs"]] == ["succeeded"] and rep["final_pass"]["state"] == "succeeded"
    a_ = audit.run(w, preflight=NP)
    found = {r["detector_id"]: r for r in a_["anomalies"]}
    assert found["multiple_documents_per_filing"]["subjects"] == {"filings": [c.fid, h.fid]}
    # only G: its excluded item is still the current path version; H's new item replaced its old one
    assert found["excluded_item_entered_window"]["subjects"] == {"filings": {"918004": "window_undetermined"}}
    catalogue = reports.build(w, W, a_)["anomalies"]
    assert {"document_path_superseded", "window_membership_changed"} <= set(catalogue["by_detector"])


def test_r3_reconcile_waits_for_every_validation_then_runs_per_issuer_then_once_in_full(env):
    X.world(env, [Filing(919001, "facts", listed=True), Filing(919002, "facts", sec=X.UNKNOWN_SEC_ID, day=3)])
    w = env.conn()
    reg = configuration.register(w, preflight=NP)
    X.designate(env, reg["configuration_id"])
    assert reconcile.reconcile_ready(w, preflight=NP) == {"state": "deferred", "reason": "validations_pending",
                                                         "pending": 2, "configuration_id": reg["configuration_id"]}
    assert X.f6_jobs(w, "reconcile") == []
    validation.validate_pending(w, preflight=NP)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    comb = comb_issuer(w)
    assert rep["partitions"] == 1 and [r["natural_key"] for r in rep["runs"]] == [f"reconcile:{comb}"]
    assert rep["final_pass"]["natural_key"] == "reconcile:all_issuers" and rep["final_pass"]["state"] == "succeeded"
    for subject, scope in ((keys.reconcile(comb), f"issuer:{comb}"), (keys.reconcile(None), "all_issuers")):
        ev = events(w, subject)
        assert [(e["state"], e["action"]) for e in ev] == [("pending", "create"), ("succeeded", "record")]
        assert job(w, ev[-1]["f6_job_id"]) == ("reconcile", scope, "succeeded")
    before = X.f6_jobs(w)
    again = reconcile.reconcile_ready(w, preflight=NP)
    assert again["runs"] == [] and again["finished"] == [{"issuer_id": comb, "state": "succeeded"}]
    assert again["final_pass"] == {"natural_key": "reconcile:all_issuers", "state": "succeeded"}
    assert X.f6_jobs(w) == before
    assert q(w, "select count(*) from financial_reconciliation_batches") == [(1,)]  # the full pass found COMB unchanged


def test_r4_a_busy_lock_records_nothing_and_a_failed_pass_is_final_until_requeued(env, monkeypatch):
    X.world(env, [Filing(920101, "facts", listed=True)])
    w, other = env.conn(), env.conn()
    designate_all(env, w)
    comb = comb_issuer(w)
    assert jobs.lock_exclusive(other)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert rep["refused"]["state"] == "refused" and rep["refused"]["reason"] == "busy" and rep["final_pass"] is None
    assert [e["state"] for e in events(w, keys.reconcile(comb))] == ["pending"]
    jobs.unlock(other, shared=False)
    real = jobs.plan_partition

    def broken(*a, **k):
        raise codec.CodecError("scripted decomposition failure")
    monkeypatch.setattr(jobs, "plan_partition", broken)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert [r["state"] for r in rep["runs"]] == ["failed"] and rep["final_pass"] is None
    ev = events(w, keys.reconcile(comb))[-1]
    assert ev["state"] == "failed" and comb in ev["reason"] and job(w, ev["f6_job_id"])[2] == "failed"
    monkeypatch.setattr(jobs, "plan_partition", real)
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert rep["runs"] == [] and rep["finished"] == [{"issuer_id": comb, "state": "failed"}]
    assert rep["final_pass"] is None                                     # it waits for every issuer's own pass
    X.requeue(w, keys.reconcile(comb), "investigated: the decomposition fault is fixed")
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert [r["state"] for r in rep["runs"]] == ["succeeded"] and rep["final_pass"]["state"] == "succeeded"


def test_r5_one_call_runs_at_most_its_bound_and_the_next_resumes(env):
    X.world(env, [Filing(930001, "facts", listed=True), Filing(930002, "facts", listed=True, symbol=X.JKH,
                                                               sec=X.SEC_IDS[X.JKH], day=3)])
    w = env.conn()
    designate_all(env, w)
    issuers = sorted(r[0] for r in q(w, "select issuer_id::text from issuers where cse_sec_id in (369, 508)"))
    assert len(issuers) == 2 and reconcile.partition_issuers(w, W) == issuers
    first = reconcile.reconcile_ready(w, limit=1, preflight=NP)
    assert [r["natural_key"] for r in first["runs"]] == [f"reconcile:{issuers[0]}"] and first["final_pass"] is None
    second = reconcile.reconcile_ready(w, limit=1, preflight=NP)
    assert [r["natural_key"] for r in second["runs"]] == [f"reconcile:{issuers[1]}"] and second["final_pass"] is None
    third = reconcile.reconcile_ready(w, limit=1, preflight=NP)
    assert third["runs"] == [] and third["final_pass"]["state"] == "succeeded"
    for bad in (0, -2, False, 1.5):
        with pytest.raises(ValueError):
            reconcile.reconcile_ready(w, limit=bad, preflight=NP)


# ================================================================================================ promotion

def test_p1_document_items_are_promoted_from_f6_evidence_only(env):
    a, b = Filing(921001, "facts", listed=True), Filing(921002, "facts", day=3)
    X.world(env, [a, b])
    w = env.conn()
    assert promotion.promote_documents(w, preflight=NP) == {}         # never validated: stays persisted
    validation.validate_pending(w, preflight=NP)
    assert promotion.promote_documents(w, preflight=NP) == {"validated": 2}
    for f in (a, b):
        ev = store.events(w, X.item_of(w, f.fid)["id"])[-1]
        run = X.runs_of(w, f.fid)[0]
        vr = q(w, "select validation_run_key, job_id::text from financial_validation_run_current where f5_run_id = %s",
               (run,))[0]
        assert (ev["state"], ev["action"], str(ev["f5_run_id"]), ev["validation_run_key"], str(ev["f6_job_id"])) == \
            ("validated", "promote", run, vr[0], vr[1])
    reg = configuration.register(w, preflight=NP)
    X.designate(env, reg["configuration_id"])
    assert promotion.promote_documents(w, preflight=NP) == {}         # designated, but nothing reconciled yet
    reconcile.reconcile_ready(w, preflight=NP)
    assert promotion.promote_documents(w, preflight=NP) == {"reconciled": 1}
    assert (X.state_of(w, a.fid), X.state_of(w, b.fid)) == ("reconciled", "validated")   # B: refused (issuer)
    n = q(w, "select count(*) from backfill_item_events")
    assert promotion.promote_documents(w, preflight=NP) == {} and q(w, "select count(*) from backfill_item_events") == n


def test_p2_late_evidence_revalidates_and_reconciles_through_operator_requeues_retrieving_nothing(env):
    a, b = Filing(922001, "facts", listed=True), Filing(922002, "facts", day=3)
    _, cdn = X.world(env, [a, b])
    w = env.conn()
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    promotion.promote_documents(w, preflight=NP)
    comb, run_b = comb_issuer(w), X.runs_of(w, b.fid)[0]
    assert (X.state_of(w, a.fid), X.state_of(w, b.fid)) == ("reconciled", "validated")
    counted = ("financial_extraction_runs", "report_document_classifications", "backfill_retrieval_records")
    before = X.table_rows(w, counted)
    cdn_calls = len(cdn.calls)
    doc_attempts = q(w, "select count(*) from backfill_request_attempts where request_class = 'document'")
    # late evidence (owner-approved): COMB's listing now names B; HB-3's late pass re-links (design 7.4 step 5)
    X.requeue(w, keys.listing(X.COMB), "late evidence: COMB's listing re-requested by the owner")
    X.rediscover(env, [a, b], listed=[a, b])
    identity.late_pass(w, wall=env.clock.wall())
    assert q(w, "select status, basis from filing_issuer_links where cse_filing_id = %s order by id desc limit 1",
             (b.fid,)) == [("evidenced", "both")]
    # the audit follows M4: B's validation run is no longer canonical, so B is not validated (it waits)
    assert placed(audit.run(w, preflight=NP))[b.fid] == (6, [7, "not_validated"])
    assert promotion.promote_documents(w, preflight=NP) == {"needs_validation": 1}
    v = validation.validate_pending(w, preflight=NP)
    assert v["attempted"] == 0 and v["awaiting_requeue"] == [{"f5_run_id": run_b, "state": "succeeded"}]
    assert reconcile.reconcile_ready(w, preflight=NP)["state"] == "deferred"
    X.requeue(w, keys.validate(run_b), "late evidence: B's issuer decision changed; validate it again (M4)")
    v = validation.validate_pending(w, preflight=NP)
    assert [s["f5_run_id"] for s in v["succeeded"]] == [run_b]
    assert promotion.promote_documents(w, preflight=NP) == {"validated": 1}
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert rep["runs"] == [] and rep["final_pass"]["state"] == "succeeded"     # finished: a new pass needs a requeue
    for subject in (keys.reconcile(comb), keys.reconcile(None)):
        X.requeue(w, subject, "late evidence: reconcile COMB again now that B is admissible")
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert [r["state"] for r in rep["runs"]] == ["succeeded"] and rep["final_pass"]["state"] == "succeeded"
    assert promotion.promote_documents(w, preflight=NP) == {"reconciled": 1}
    assert X.state_of(w, b.fid) == "reconciled" and placed(audit.run(w, preflight=NP))[b.fid] == (9, None)
    assert X.table_rows(w, counted) == before and len(cdn.calls) == cdn_calls      # nothing retrieved again
    assert q(w, "select count(*) from backfill_request_attempts where request_class = 'document'") == doc_attempts


# ================================================================================================ the audit

FUNNEL = [
    Filing(920001, path=None),                                              # 2 no_document
    Filing(920002, path="/abs/report.pdf"),                                 # 2 invalid_path
    Filing(920003, cdn=404, day=3),                                         # 3 not_found
    Filing(920004, "text_error", day=3),                                    # 4 consumer_failed:TextExtractionError
    Filing(920005, "no_text", day=4),                                       # 4 unreadable
    Filing(920006, "prose", day=4),                                         # 5 no_statements
    Filing(920007, "words_error", day=5),                                   # 5 consumer_failed:WordExtractionError
    Filing(920008, "unmapped", listed=True, day=5),                         # 6 zero_candidates
    Filing(920009, "late_periods", listed=True, day=6),                     # 7 f6.1:...
    Filing(920010, "no_currency", listed=True, day=6),                      # 8 rules:...
    Filing(920011, "facts", sec=X.UNKNOWN_SEC_ID, day=7),                   # 8 issuer_evidence: unresolved
    Filing(920012, "facts", day=7),                                         # 8 issuer_evidence: path prefix only
    Filing(920013, "facts", listed=True, day=8),                            # 9
    Filing(920014, cdn="skip", day=20, sec=X.UNKNOWN_SEC_ID),               # 3 not_attempted (no issuer: it holds
]                                                                           #   back the full pass, not COMB's)
EARLY = {920001: (1, [2, "no_document"]), 920002: (1, [2, "invalid_path"]), 920003: (2, [3, "not_found"]),
         920004: (3, [4, "consumer_failed:TextExtractionError"]), 920005: (3, [4, "unreadable"]),
         920006: (4, [5, "no_statements"]), 920007: (4, [5, "consumer_failed:WordExtractionError"]),
         920008: (5, [6, "zero_candidates"]), 920014: (2, [3, "not_attempted"])}
LATE = {920009: (6, [7, "f6.1:period_end_after_publication,role_untrusted"]),
        920010: (7, [8, "rules:currency_not_reported"]),
        920011: (7, [8, "issuer_evidence:unresolved/document_path_prefix"]),
        920012: (7, [8, "issuer_evidence:path_prefix_only"])}
VALIDATED = (920009, 920010, 920011, 920012, 920013)


def placed(a_):
    return {r["cse_filing_id"]: (r["reached"], r["stop"]) for r in a_["coverage"]["table"]}


def test_a1_the_funnel_places_every_filing_at_the_first_stage_it_fails_at_every_phase(env):
    X.world(env, FUNNEL)
    w = env.conn()
    with w.cursor() as cur:                                     # F1 evidence outside W and undated: not in the funnel
        S.ensure_filing(cur, 920098, "2025-05-01T00:00:00+05:30")
        S.ensure_filing(cur, 920099, None)
    w.commit()
    phase = audit.run(w, preflight=NP)
    assert placed(phase) == {**EARLY, **{f: (6, [7, "not_validated"]) for f in VALIDATED}}
    validation.validate_pending(w, preflight=NP)
    reg = configuration.register(w, preflight=NP)
    assert placed(audit.run(w, preflight=NP)) == {**EARLY, **LATE, 920013: (8, [9, "no_designated_configuration"])}
    X.designate(env, reg["configuration_id"])
    # another, NON-designated configuration reconciled first (F6.4's own jobs): stage 9 counts only the designated one
    f3, f4, f5_ = configuration.backfill_tuple()
    other = reconciliation.ReconciliationConfiguration(accepted_f3=(f3, ("f3.1", "a test-only text extractor")),
                                                       accepted_f4=(f4,), accepted_f5=(f5_,))
    jobs.register_configuration(w, other)
    assert jobs.reconcile(w, other.configuration_id)["state"] == "succeeded"
    assert q(w, "select count(*) from financial_reconciliation_current where configuration_id = %s",
             (other.configuration_id,))[0][0] > 0
    assert placed(audit.run(w, preflight=NP)) == {**EARLY, **LATE, 920013: (8, [9, "not_reconciled"])}
    rep = reconcile.reconcile_ready(w, preflight=NP)
    assert [r["state"] for r in rep["runs"]] == ["succeeded"]          # COMB: every filing of it in W is final
    assert rep["final_pass"] is None and rep["readiness"]["open_filings"] == 1     # 920014 is not: no full pass yet
    final = audit.run(w, preflight=NP)
    assert placed(final) == {**EARLY, **LATE, 920013: (9, None)}
    funnel = final["coverage"]["funnel"]
    assert (funnel["filings"], funnel["complete"]) == (14, 1)
    assert funnel["reached"] == {"1": 14, "2": 12, "3": 10, "4": 8, "5": 6, "6": 5, "7": 4, "8": 1, "9": 1}
    assert funnel["stops"] == {
        "2": {"invalid_path": 1, "no_document": 1}, "3": {"not_attempted": 1, "not_found": 1},
        "4": {"consumer_failed:TextExtractionError": 1, "unreadable": 1},
        "5": {"consumer_failed:WordExtractionError": 1, "no_statements": 1}, "6": {"zero_candidates": 1},
        "7": {"f6.1:period_end_after_publication,role_untrusted": 1},
        "8": {"issuer_evidence:path_prefix_only": 1, "issuer_evidence:unresolved/document_path_prefix": 1,
              "rules:currency_not_reported": 1}}
    dims = final["coverage"]["dimensions"]
    assert dims["source"] == {"both": {"filings": 4, "complete": 1, "stops": {"6": 1, "7": 1, "8": 1}},
                              "feed": {"filings": 10, "complete": 0, "stops": {"2": 2, "3": 2, "4": 2, "5": 2,
                                                                                "8": 2}}}
    assert dims["upload_month"] == {"2025-03": {"filings": 14, "complete": 1, "stops": {
        "2": 2, "3": 2, "4": 2, "5": 2, "6": 1, "7": 1, "8": 3}}}
    levels = final["coverage"]["levels"]
    assert levels["candidates"]["candidates"] == q(w, "select count(*) from financial_candidate_validations")[0][0]
    assert levels["facts"]["count"] == q(w, "select count(*) from financial_economic_facts")[0][0] > 0
    assert levels["reconciliation"]["current_facts"] == q(
        w, "select count(*) from financial_reconciliation_current where configuration_id = %s",
        (reg["configuration_id"],))[0][0] > 0
    # the snapshot: L11 + one L10 record per detector, the audit item naming it; the digest is a pure function
    snap = q(w, "select rule_version, snapshot_digest, stage_counts -> 'complete' from backfill_coverage_snapshots "
                "where id = %s", (final["snapshot_id"],))
    assert snap == [("hb.coverage.1", final["digest"], 1)]
    assert q(w, "select count(*) from backfill_anomalies where snapshot_id = %s", (final["snapshot_id"],)) == [
        (len(final["anomalies"]),)]
    ev = store.events(w, final["item"]["id"])
    assert [(e["state"], e["snapshot_id"]) for e in ev] == [("pending", None), ("succeeded", final["snapshot_id"])]
    rows = q(w, "select count(*) from backfill_anomalies")
    again = audit.run(w, preflight=NP)
    assert (again["digest"], again["snapshot_id"], again["snapshot_created"]) == (
        final["digest"], final["snapshot_id"], False)
    assert q(w, "select count(*) from backfill_anomalies") == rows               # identical records: nothing new
    assert [i for (i,) in q(w, "select natural_key from backfill_work_items where item_kind = 'audit' order by "
                               "sequence_no")] == [f"audit:{n}" for n in range(1, 6)]


def test_a2_the_phase2_detectors_on_the_synthetic_world(env):
    X.world(env, FUNNEL)
    w = env.conn()
    with w.cursor() as cur:
        S.ensure_filing(cur, 920099, None)
    w.commit()
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    got = {r["detector_id"]: r for r in audit.run(w, preflight=NP)["anomalies"]}
    assert got["retrieval_and_consumer_failures"]["counts"]["by_reason"] == {
        "2:invalid_path": 1, "2:no_document": 1, "3:not_found": 1, "4:consumer_failed:TextExtractionError": 1,
        "5:consumer_failed:WordExtractionError": 1}
    assert got["retrieval_and_consumer_failures"]["anomaly_class"] == 3
    for d, cls in (("window_undetermined", 3), ("upload_time_missing", 4)):
        assert (got[d]["subjects"], got[d]["anomaly_class"], got[d]["count"]) == ({"filings": [920099]}, cls, 1)
    assert got["listing_only"]["count"] == 0
    assert got["feed_only"]["subjects"]["filings"] == [f.fid for f in FUNNEL if not f.listed]
    assert set(got["P-31"]["subjects"]) == {"920006", "920008"} and got["P-31"]["anomaly_class"] == 2
    assert set(got["P-16"]["subjects"]) == {"920011"} and set(got["P-17"]["subjects"]) == {"920005", "920006",
                                                                                          "920012"}
    assert got["P-1"]["count"] == 12 and got["P-1"]["anomaly_class"] == 6
    for d in ("multiple_documents_per_filing", "excluded_item_entered_window", "securities_not_queryable",
              "issuer_evidence_held", "path_epoch_missing"):
        assert got[d]["count"] == 0, d
    assert got["listing_withdrawn"]["subjects"] == {"by_symbol": {}}
    assert {r["anomaly_class"] for r in got.values()} <= {1, 2, 3, 4, 5, 6}
    assert {d for d in got if d.startswith("P-")} == {f"P-{n}" for n in range(1, 35)}


def test_a3_the_audit_reads_as_cse_reader_and_records_as_the_worker(env):
    X.world(env, [Filing(923001, "facts", listed=True), Filing(923002, "prose", day=3)])
    w = env.conn()
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    as_worker = audit.run(w, preflight=NP)
    rd = X.reader(env)
    as_reader = audit.run(w, reader=rd, preflight=NP)
    assert as_reader["digest"] == as_worker["digest"] and as_reader["snapshot_id"] == as_worker["snapshot_id"]
    with pytest.raises(Exception) as ei:                                # the reader writes nothing
        with rd.cursor() as cur:
            cur.execute("insert into backfill_anomalies (detector_id, detector_version, anomaly_class, subject_ids, "
                        "status) values ('x', 'hb.anomaly.1', 3, '{}', 'open')")
    rd.rollback()
    assert "permission denied" in str(ei.value)


def test_a4_a_failed_audit_is_recorded_failed_with_its_reason_and_records_no_snapshot(env, monkeypatch):
    X.world(env, [Filing(924001, "facts", listed=True)])
    w = env.conn()

    def broken(cur, window, coverage):
        raise RuntimeError("scripted detector failure")
    monkeypatch.setattr(anomalies, "detect", broken)
    with pytest.raises(RuntimeError):
        audit.run(w, preflight=NP)
    item = store.item_by_key(w, keys.audit(1)["natural_key"])
    ev = store.events(w, item["id"])[-1]
    assert ev["state"] == "failed" and "RuntimeError: scripted detector failure" in ev["reason"]
    assert q(w, "select count(*) from backfill_coverage_snapshots") == [(0,)]


def test_a5_without_a_window_the_audit_refuses_after_a_disarm(env):
    X.world(env, [Filing(925001, "facts", listed=True)])
    w = env.conn()
    from worker.financial_backfill import owner
    owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision.disarm("the owner disarms the backfill"),
                                 "tester")
    with pytest.raises(F6Refused) as ei:
        audit.run(w, preflight=NP)
    assert ei.value.codes == ["window"]
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'audit'") == [(0,)]
    assert audit.run(w, window=W, preflight=NP)["coverage"]["window"] == ["2025-03-01", "2025-04-30"]


# ================================================================================================ boundaries

def test_b1_no_f64_unit_is_wrapped_each_ledger_event_commits_in_a_transaction_of_its_own(env):
    X.world(env, [Filing(926001, "facts", listed=True)])
    w = env.conn()
    logged = X.track_transactions(env, ("financial_validation_runs", "financial_reconciliation_batches",
                                        "backfill_item_events"))
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    rows = logged()
    txid = {}
    for rel, t in rows:
        txid.setdefault(rel, []).append(t)
    f6_tx = set(txid["financial_validation_runs"]) | set(txid["financial_reconciliation_batches"])
    assert f6_tx and not f6_tx & set(txid["backfill_item_events"])           # F8 P-3: F6.4's own units, unwrapped


def test_b2_hb5_writes_no_frozen_table_and_no_cse_ledger_record(env):
    _, cdn = X.world(env, [Filing(927001, "facts", listed=True), Filing(927002, "facts", day=3)])
    w = env.conn()
    frozen = X.table_rows(w, X.F_STAGE_TABLES)
    cse = X.table_rows(w, ("backfill_request_attempts", "backfill_request_outcomes", "backfill_retrieval_records",
                           "backfill_leases", "backfill_holds", "backfill_blocks", "market_source_responses"))
    kinds = q(w, "select item_kind, count(*) from backfill_work_items group by 1 order by 1")
    calls = len(cdn.calls)
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    promotion.promote_documents(w, preflight=NP)
    audit.run(w, preflight=NP)
    assert X.table_rows(w, X.F_STAGE_TABLES) == frozen and len(cdn.calls) == calls
    assert X.table_rows(w, ("backfill_request_attempts", "backfill_request_outcomes", "backfill_retrieval_records",
                            "backfill_leases", "backfill_holds", "backfill_blocks", "market_source_responses")) == cse
    after = dict(q(w, "select item_kind, count(*) from backfill_work_items group by 1 order by 1"))
    assert {k: after[k] - dict(kinds).get(k, 0) for k in after if after[k] != dict(kinds).get(k, 0)} == {
        "audit": 1, "reconcile": 2, "validate": 2}
    assert q(w, "select count(*) from financial_validation_runs where job_id is null") == [(0,)]


def test_b3_reports_go_outside_the_repository_only_and_never_repeat_the_user_agent(env, tmp_path):
    X.world(env, [Filing(928001, "facts", listed=True), Filing(928002, "prose", day=3)])
    w = env.conn()
    designate_all(env, w)
    reconcile.reconcile_ready(w, preflight=NP)
    built = reports.build(w, W, audit.run(w, preflight=NP))
    assert set(built) == {"universe", "issuer_evidence", "requests", "anomalies", "coverage"}
    text = json.dumps(built, default=str)
    assert F.CONTACT not in text and "cse-analysis-capture" not in text
    assert built["universe"]["filings"]["in_window"] == 2 and built["coverage"]["funnel"]["complete"] == 1
    path = reports.write_json(tmp_path / "hb5_report.json", built)
    assert json.load(open(path))["coverage"]["digest"] == built["coverage"]["digest"]
    with pytest.raises(FileExistsError):
        reports.write_json(tmp_path / "hb5_report.json", built)
    with pytest.raises(F6Refused) as ei:
        reports.write_json(os.path.join(REPO, "hb5_report.json"), built)
    assert ei.value.codes == ["report_path"] and not os.path.exists(os.path.join(REPO, "hb5_report.json"))


def test_b4_the_preflight_passes_as_the_worker_and_refuses_otherwise_before_anything_is_written(env):
    X.world(env, [Filing(929001, "facts", listed=True)])
    w = env.conn()
    assert preflight.problems(w) == []
    rd = X.reader(env)
    assert any("connected as" in p or "cse_worker" in p for p in preflight.problems(rd))
    before = q(w, "select count(*) from backfill_item_events")
    for call in (lambda: validation.validate_pending(w, preflight=lambda c: ["a scripted problem"]),
                 lambda: configuration.register(w, preflight=lambda c: ["a scripted problem"]),
                 lambda: reconcile.reconcile_ready(w, preflight=lambda c: ["a scripted problem"]),
                 lambda: promotion.promote_documents(w, preflight=lambda c: ["a scripted problem"]),
                 lambda: audit.run(w, preflight=lambda c: ["a scripted problem"])):
        with pytest.raises(F6Refused) as ei:
            call()
        assert ei.value.codes == ["preflight"]
    assert q(w, "select count(*) from backfill_item_events") == before
    assert X.f6_jobs(w) == []
    rep = validation.validate_pending(w)                               # the real preflight, as the worker
    assert rep["attempted"] == 1 and len(rep["succeeded"]) == 1
