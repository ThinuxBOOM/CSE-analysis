"""
One coverage audit (design sections 18, 19 and 22). The work item audit:<n> (HB-1), then ONE read-only snapshot for
the coverage and the anomaly detectors, then the immutable records:

    L11  the coverage snapshot: rule hb.coverage.1, the digest of the per-filing table, the stage counts, and what the
         design stores with it (dimensions, levels, review lists, expectations)
    L10  one record per detector (rule hb.anomaly.1), each naming that snapshot
    L3   the audit item's succeeded event, naming the snapshot (failed, with the reason, when the audit could not end)

The per-filing table itself is returned for export outside the repository (reports.write_json); PostgreSQL keeps its
digest. The database identifies an L11 row by (rule, digest) and an L10 row by its content, so re-auditing an unchanged
database adds the new audit item and nothing else.
"""
from ..financial_backfill import keys, store
from . import ANOMALY_RULE, COVERAGE_RULE, RULE_VERSION, anomalies, coverage, preflight as checks
from .errors import F6Refused
from .snapshot import read_only, window_of

NEW_ITEM_TRIES = 5
MAX_REASON = 400


def _next_number(conn):
    with read_only(conn) as cur:
        cur.execute("select coalesce(max(sequence_no), 0) + 1 from backfill_work_items where item_kind = 'audit'")
        return cur.fetchone()[0]


def new_item(conn, details, wakeup_id=None):
    """A new audit item at the next free number: always a newly created one, never another process's."""
    for _ in range(NEW_ITEM_TRIES):
        item, created = store.ensure_item(conn, keys.audit(_next_number(conn)), details=details, wakeup_id=wakeup_id)
        if created:
            return item
    raise F6Refused([("audit", "no free audit number: another process keeps taking them")])


def l10_row(record):
    """The L10 columns of one detector record: its subjects and examples are its subject ids (labels and positions,
    never values); its counts, the count first."""
    return {"detector_id": record["detector_id"], "detector_version": record["detector_version"],
            "anomaly_class": record["anomaly_class"], "status": record["status"],
            "subject_ids": {"subjects": record["subjects"], "examples": record["examples"]},
            "counts": dict({"count": record["count"]}, **record["counts"])}


def snapshot_details(cov):
    return {"rule": cov["rule"], "anomaly_rule": ANOMALY_RULE, "window": cov["window"],
            "designated_configuration": cov["designated_configuration"], "versions": cov["versions"],
            "dimensions": cov["dimensions"], "levels": cov["levels"], "review_lists": cov["review_lists"],
            "expectations": cov["expectations"]}


def run(conn, *, window=None, reader=None, baseline=None, code_revision=None, wakeup_id=None, preflight=None):
    """One audit. `conn` is the worker (it records); `reader`, when given, is the session that reads (cse_reader).
    Returns the coverage (with its per-filing table), the detector records and the ids recorded."""
    checks.require(conn, preflight)
    w = window_of(store.arming_in_force(conn), window)
    item = new_item(conn, {"rule": RULE_VERSION, "window": [d.isoformat() for d in w]}, wakeup_id)
    try:
        src = reader or conn
        with read_only(src) as cur:
            cov = coverage.read(src, cur, w, baseline=baseline)
            records = anomalies.detect(cur, w, cov)
        snapshot_id, created = store.record_snapshot(conn, rule_version=COVERAGE_RULE, snapshot_digest=cov["digest"],
                                                     stage_counts=cov["funnel"], details=snapshot_details(cov),
                                                     code_revision=code_revision, wakeup_id=wakeup_id)
        anomaly_ids = [store.record_anomaly(conn, snapshot_id=snapshot_id, wakeup_id=wakeup_id, **l10_row(r))[0]
                       for r in records]
    except Exception as exc:
        store.append_event(conn, item["id"], "failed", "record", wakeup_id=wakeup_id,
                           reason=f"{type(exc).__name__}: {exc}"[:MAX_REASON], details={"rule": RULE_VERSION})
        raise
    store.append_event(conn, item["id"], "succeeded", "record", snapshot_id=snapshot_id, wakeup_id=wakeup_id,
                       details={"rule": RULE_VERSION, "digest": cov["digest"], "filings": cov["funnel"]["filings"],
                                "complete": cov["funnel"]["complete"], "anomaly_records": len(anomaly_ids),
                                "snapshot_created": created})
    return {"item": item, "snapshot_id": snapshot_id, "snapshot_created": created, "digest": cov["digest"],
            "coverage": cov, "anomalies": records, "anomaly_ids": anomaly_ids}
