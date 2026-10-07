"""
Reconciliation per issuer, then the final full pass (design sections 10.3, 14.1 HB-S5 and 22: "per-issuer
reconciliation after completion" is a determinism guard).

F6.4's reconcile is global before it is per issuer: its first step validates every selected F5 run that lacks its
canonical validation run, whatever its issuer; with --no-validate it refuses while any such run is missing (F6.4
section 9.4; design D8). So a pass starts only while F6.4 has NO pending validation (validation.py runs first), and it
runs in F6.4's own --no-validate mode: every validation is a validate:<run> item with its job recorded, and a
validation F6.4 could not make is never retried inside a reconcile. A failed validation therefore defers every pass
until an operator has dealt with it (section 24: "Investigate; rerun").

    the designated configuration   the owner's (F6.4 T9). None -> refused: nothing is reconciled before the owner
                                   designates (HB-S3)
    reconcile:<issuer>             once every in-window filing of the issuer is final (readiness.py), for each issuer
                                   F6.4 partitions in W (the issuers of the canonical validation runs' SOs)
    reconcile:all_issuers          the final full pass: every in-window filing final and every such issuer's own pass
                                   succeeded
    F6.4's report                  succeeded -> succeeded; failed -> failed (an operator re-queue reruns it); refused
                                   (its lock busy, or a validation pending again) -> nothing recorded; the call stops

A finished item is never re-run here: a second pass (late evidence, design section 7.4 step 5) is an operator re-queue.
"""
from ..financial_backfill import keys, states, store
from ..financial_truth import versions as f6_versions
from ..financial_truth_store import jobs
from . import RECONCILE_BATCH, RULE_VERSION, configuration, preflight as checks, readiness, validation
from .errors import F6Refused
from .snapshot import bounds, read_only, window_of

KIND = "reconcile"
MAX_REASON = 400

PARTITIONS_SQL = """
    select distinct e.issuer_id::text
      from financial_validation_run_current v
      join financial_extraction_runs r on r.id = v.f5_run_id
      join report_filings f on f.cse_filing_id = r.cse_filing_id
      join financial_source_observations s on s.validation_run_key = v.validation_run_key
      join financial_economic_facts e on e.ef_key = s.ef_key
     where f.uploaded_at >= %s and f.uploaded_at < %s
       and v.validation_version = %s and v.input_policy_version = %s and v.op1_version = %s
       and v.admission_version = %s and v.identity_version = %s
     order by 1"""


def partition_issuers(conn, window, vs=f6_versions.IMPLEMENTED):
    """The issuers F6.4 partitions among W's filings: those of the SOs of their canonical validation runs (F6.4's
    selection.run_issuer, over every such run at once)."""
    with read_only(conn) as cur:
        cur.execute(PARTITIONS_SQL, bounds(window) + (vs.validation_version, vs.input_policy_version, vs.op1_version,
                                                     vs.admission_version, vs.identity_version))
        return [r[0] for r in cur.fetchall()]


def outcome_event(report):
    """(state, reason) of the ledger event for one F6.4 reconcile report, or None (refused: nothing to record)."""
    state = report["state"]
    if state == "succeeded":
        return "succeeded", None
    if state == "failed":
        failed = report.get("failed") or []
        why = (f"F6.4 reconcile failed: partitions {[(p['issuer_id'], p['state']) for p in failed]}, "
               f"failed validations {report.get('failed_validations') or []}")
        return "failed", why[:MAX_REASON]
    if state == "refused":
        return None
    raise ValueError(f"F6.4 reconcile returned an unknown state {state!r}")


def _run(conn, subject, configuration_id, *, code_revision, wakeup_id):
    item, _ = store.ensure_item(conn, subject, details={"rule": RULE_VERSION}, wakeup_id=wakeup_id)
    try:
        rep = jobs.reconcile(conn, configuration_id, no_validate=True, code_revision=code_revision,
                             only_issuer=subject["issuer_id"])
    except jobs.JobRefused as exc:                     # before any F6 job: the configuration itself is refused
        raise F6Refused([("configuration", str(exc))]) from None
    event = outcome_event(rep)
    if event is not None:
        to_state, reason = event
        details = {"rule": RULE_VERSION, "job_state": rep["state"],
                   **{k: rep[k] for k in ("partitions", "written", "unchanged") if k in rep}}
        store.append_event(conn, item["id"], to_state, "record", reason=reason, details=details, wakeup_id=wakeup_id,
                           f6_job_id=rep["job_id"])
    return {"natural_key": subject["natural_key"], "job_id": rep["job_id"], "state": rep["state"],
            "reason": rep.get("reason")}


def reconcile_ready(conn, *, window=None, limit=RECONCILE_BATCH, code_revision=None, wakeup_id=None, preflight=None):
    """One bounded call: the per-issuer passes that are due, then the final pass when it is due. Returns a report."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    checks.require(conn, preflight)
    w = window_of(store.arming_in_force(conn), window)
    cid = configuration.designated(conn)
    if cid is None:
        raise F6Refused([("designation", "no configuration is designated canonical: the owner designates one "
                                         "through F6.4's owner path (HB-S3)")])
    pending = validation.pending_runs(conn)
    if pending:
        return {"state": "deferred", "reason": "validations_pending", "pending": len(pending), "configuration_id": cid}
    rd = readiness.of(conn, w)
    parts = partition_issuers(conn, w)
    report = {"state": "done", "configuration_id": cid, "readiness": rd.summary(), "partitions": len(parts),
              "runs": [], "waiting": [], "finished": [], "refused": None, "final_pass": None}
    for issuer in parts:
        subject = keys.reconcile(issuer)
        state = item_state(conn, subject)
        if state in states.FINAL[KIND]:
            report["finished"].append({"issuer_id": issuer, "state": state})
            continue
        if not rd.issuer_ready(issuer):
            report["waiting"].append({"issuer_id": issuer, "open_filings": len(rd.open_filings(issuer))})
            continue
        if len(report["runs"]) >= limit or report["refused"] is not None:
            continue
        out = _run(conn, subject, cid, code_revision=code_revision, wakeup_id=wakeup_id)
        report["runs"].append(out)
        if out["state"] == "refused":
            report["refused"] = out
    final = keys.reconcile(None)
    final_state = item_state(conn, final)
    if final_state in states.FINAL[KIND]:
        report["final_pass"] = {"natural_key": final["natural_key"], "state": final_state}
    elif report["refused"] is None and len(report["runs"]) < limit and rd.all_final and \
            all(item_state(conn, keys.reconcile(i)) == "succeeded" for i in parts):
        out = _run(conn, final, cid, code_revision=code_revision, wakeup_id=wakeup_id)
        report["final_pass"] = out
        if out["state"] == "refused":
            report["refused"] = out
    return report


def item_state(conn, subject):
    """The current state of the item of a subject, or None when it has no item yet."""
    item = store.item_by_key(conn, subject["natural_key"])
    return None if item is None else store.current_state(conn, item["id"])["state"]
