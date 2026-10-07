"""
Validate-pending batches (design section 10.3: "jobs.validate(f5_run) or a pending_runs sweep, in bounded batches,
independent of CSE. F6.4 takes its own shared lock and contacts no one. The ledger records the F6 job ids").

The pending set is F6.4's own: jobs.pending_runs, every F5 run lacking its canonical validation run (M4) under the
implemented version set, whoever persisted it. Each run is one HB-1 work item, validate:<F5 run id>:

    no item, or the item pending      -> F6.4's jobs.validate, in F6.4's own transaction units and lock; then ONE
                                         ledger event in a transaction of its own (F8 P-3: no F6.4 unit is wrapped):
        succeeded / already_present   -> succeeded, naming the job and the validation run (the guard checks both)
        failed                        -> failed, naming the job and F6.4's redacted reason; never retried here
                                         (section 11.3: "never retries in a loop"); an operator re-queue retries it
        refused (F6.4's lock is busy) -> nothing recorded: the item stays pending and the batch stops
    the item already succeeded/failed -> not run: a final item leaves its state only by an explicit operator re-queue
                                         (HB-1's rule). A succeeded item whose run is pending again lost its canonical
                                         validation run to late evidence (M4): it awaits that re-queue

Reconciliation from evidence first (section 15.3: "the ledger re-reads F6 job states"): a PENDING item whose run is no
longer pending had its F6.4 job finish while its ledger event was never written (the process ended in between). F6.4
writes a validation run and its job's succeeded event in ONE transaction, so the run's canonical validation run names a
succeeded job: the item records it, and no new job runs.
"""
from ..financial_backfill import keys, states, store
from ..financial_truth_store import jobs, selection
from . import RULE_VERSION, VALIDATE_BATCH, preflight as checks
from .snapshot import read_only

KIND = "validate"
MAX_REASON = 400


def pending_runs(conn):
    """F6.4's pending set, in its own order (F5 run id)."""
    with read_only(conn) as cur:
        return jobs.pending_runs(cur)


def item_states(conn, kind=KIND):
    """{natural_key: current state} of every item of one kind."""
    with read_only(conn) as cur:
        cur.execute("select natural_key, state from backfill_item_state where item_kind = %s", (kind,))
        return dict(cur.fetchall())


def outcome_event(report):
    """(state, refs, reason) of the ledger event for one F6.4 validate report, or None (refused: nothing to record)."""
    state = report["state"]
    if state in ("succeeded", "already_present"):
        return "succeeded", {"f6_job_id": report["job_id"], "validation_run_key": report["validation_run_key"]}, None
    if state == "failed":
        why = f"F6.4 validate {report.get('reason') or 'failed'}: {report.get('error') or ''}".strip()
        return "failed", {"f6_job_id": report["job_id"]}, why[:MAX_REASON]
    if state == "refused":
        return None
    raise ValueError(f"F6.4 validate returned an unknown state {state!r}")


def validate_pending(conn, *, limit=VALIDATE_BATCH, wait=False, code_revision=None, wakeup_id=None, preflight=None):
    """One bounded batch: at most `limit` F6.4 validate jobs. Returns a report."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    checks.require(conn, preflight)
    todo = pending_runs(conn)
    known = item_states(conn)
    report = {"pending": len(todo), "attempted": 0, "succeeded": [], "failed": [], "refused": None,
              "awaiting_requeue": [], "recovered": recover(conn, todo, known, wakeup_id)}
    for run in todo:
        key = keys.validate(run)["natural_key"]
        state = known.get(key)
        if state is not None and state in states.FINAL[KIND]:
            report["awaiting_requeue"].append({"f5_run_id": run, "state": state})
            continue
        if report["attempted"] >= limit or report["refused"] is not None:
            continue
        item, _ = store.ensure_item(conn, keys.validate(run), details={"rule": RULE_VERSION}, wakeup_id=wakeup_id)
        report["attempted"] += 1
        rep = jobs.validate(conn, run, wait=wait, code_revision=code_revision)
        event = outcome_event(rep)
        if event is None:
            report["refused"] = {"f5_run_id": run, "job_id": rep["job_id"], "reason": rep.get("reason")}
            continue
        to_state, refs, reason = event
        details = {"rule": RULE_VERSION, "job_state": rep["state"]}
        details.update({k: rep[k] for k in ("candidates", "sos", "facts") if k in rep})
        store.append_event(conn, item["id"], to_state, "record", reason=reason, details=details, wakeup_id=wakeup_id,
                           **refs)
        report[to_state].append({"f5_run_id": run, "job_id": rep["job_id"], "job_state": rep["state"]})
    return report


def recover(conn, todo, known, wakeup_id=None):
    """Pending items whose run is no longer pending: recorded succeeded from F6.4's own rows (section 15.3)."""
    pending_now = {keys.validate(r)["natural_key"] for r in todo}
    out = []
    for key in sorted(k for k, s in known.items() if s == "pending" and k not in pending_now):
        run = key.split(":", 1)[1]
        with read_only(conn) as cur:
            vrk = selection.canonical_validation_run(cur, run)
            job = None
            if vrk is not None:
                cur.execute("select job_id::text from financial_validation_runs where validation_run_key = %s",
                            (vrk,))
                job = cur.fetchone()[0]
        if vrk is None or job is None:
            continue                                     # not F6.4's evidence: left as it is, never invented
        item = store.item_by_key(conn, key)
        store.append_event(conn, item["id"], "succeeded", "record", f6_job_id=job, validation_run_key=vrk,
                           details={"rule": RULE_VERSION, "recovered_from": "the F6.4 validation run"},
                           wakeup_id=wakeup_id)
        out.append({"f5_run_id": run, "job_id": job})
    return out
