"""
Document items (design sections 9 HB-R3/HB-R4, 14.2 and 15.3): created for the filings of the armed window W once the
document gate is open, excluded with their reason, and promoted from F5 evidence. No CSE request, no lease needed.

Creation, one item per (filing, path version) - HB-1's natural key `document:<cse_filing_id>:<SHA-256 of the path>`:
    upload date in W (Colombo, HB-W1), path F2 accepts   -> discovered
    upload date in W, path NULL                          -> excluded: no_document
    upload date in W, path F2's resolve_candidates refuses -> excluded: invalid_path
    upload date NULL                                     -> excluded: window_undetermined
    upload date outside W                                -> no item (HB-W5: recorded by F1, never retrieved; audited)

Promotion (any wake-up; design section 15.3: "promotions to terminal states may run at any wake-up"):
    discovered, the filing now outside W / undated       -> excluded: out_of_window / window_undetermined
    discovered, F1's current path is still this one      -> persisted (evidence wins, evidence.py), else pending
    pending / retry_wait with evidence                   -> persisted
    an item whose path is no longer F1's current path    -> left as it is: F5's composition retrieves only the current
                                                            path (load_filings_from_db), so it is never claimed; a
                                                            class-4 anomaly records it (design 6.3: a re-upload is a
                                                            new document item; both documents are kept)
    a pending item whose filing left W                   -> left as it is (HB-1 has no pending -> excluded), never
                                                            claimed; a class-3 anomaly records it (HB-W4)
"""
from .. import document_retrieval as f2
from ..backfill_discovery import plan as hb3_plan
from ..financial_backfill import keys, store
from . import RULE_VERSION, evidence
from .errors import DocumentRefused

ANOMALY_VERSION = "hb.anomaly.1"
PATH_SUPERSEDED = "document_path_superseded"
WINDOW_CHANGED = "window_membership_changed"


def _q(conn, sql, args=(), fetch="all"):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if fetch == "all" else cur.fetchone()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return rows


def in_window(uploaded_at, window):
    start, end = hb3_plan.window_bounds(*window)
    return uploaded_at is not None and start <= uploaded_at < end


def first_state(filing):
    """(state, reason) of a new item for a filing dated in W or undated (design section 14.2)."""
    if filing["uploaded_at"] is None:
        return "excluded", "window_undetermined"
    if filing["path"] is None:
        return "excluded", "no_document"
    try:
        f2.resolve_candidates(filing["path"])
    except f2.InvalidPath:
        return "excluded", "invalid_path"
    return "discovered", None


def candidate_filings(conn, window):
    """F1 filings whose upload date lies in W, or is unknown, by cse_filing_id."""
    start, end = hb3_plan.window_bounds(*window)
    rows = _q(conn, "select cse_filing_id, path, uploaded_at from report_filings where (uploaded_at >= %s and "
                    "uploaded_at < %s) or uploaded_at is null order by cse_filing_id", (start, end))
    return [{"cse_filing_id": int(r[0]), "path": r[1], "uploaded_at": r[2]} for r in rows]


ITEM_SQL = """
    select i.id, i.natural_key, i.cse_filing_id, i.path_sha256, s.state, f.path, f.uploaded_at, s.seq
      from backfill_work_items i
      join backfill_item_state s on s.item_id = i.id
      join report_filings f on f.cse_filing_id = i.cse_filing_id
     where i.item_kind = 'document'"""


def document_items(conn, states=None):
    """Document items with their current state and their filing's CURRENT F1 path and upload date."""
    sql, args = ITEM_SQL, ()
    if states is not None:
        sql, args = sql + " and s.state = any(%s)", (list(states),)
    rows = _q(conn, sql + " order by f.uploaded_at nulls last, i.cse_filing_id, i.natural_key", args)
    return [{"id": str(r[0]), "natural_key": r[1], "cse_filing_id": int(r[2]), "path_sha256": r[3], "state": r[4],
             "path": r[5], "uploaded_at": r[6], "seq": r[7]} for r in rows]


def path_is_current(item):
    """Is the item's path version F1's CURRENT path of the filing (the one load_filings_from_db hands to F2)?"""
    return item["path"] is not None and keys.path_version(item["path"]) == item["path_sha256"]


def filings_with_runs(conn, versions):
    """Filings with at least one F5 run under the armed versions (one read; evidence.py decides per item)."""
    cond = " and ".join(f"{c} = %({c})s" for c in evidence.VERSION_COLUMNS)
    return {int(r[0]) for r in _q(conn, f"select distinct cse_filing_id from financial_extraction_runs where {cond}",
                                  versions)}


def _anomaly(conn, detector, anomaly_class, item, wakeup_id, **extra):
    subject = {"item": item["id"], "natural_key": item["natural_key"], "cse_filing_id": item["cse_filing_id"],
               "state": item["state"], **extra}
    return store.record_anomaly(conn, detector_id=detector, detector_version=ANOMALY_VERSION,
                                anomaly_class=anomaly_class, subject_ids=subject, status="open",
                                wakeup_id=wakeup_id)[0]


def plan_documents(conn, gate, *, wakeup_id=None, holder_lease_id=None, versions=None):
    """Create the document items of the gate's window W and promote from evidence. Outside a CSE slice it refuses
    while a slice is active (a slice calls it with its own lease). Returns counts."""
    active = store.active_lease(conn)
    if active is not None and active["id"] != holder_lease_id:
        raise DocumentRefused([("lease", "a CSE slice is active: document planning runs inside that slice or "
                                         "between slices")])
    versions = versions or evidence.armed_versions()
    out = {"created": {}, "existing": 0, "promoted": {}, "superseded": 0, "window_changed": 0}
    known = {r[0] for r in _q(conn, "select natural_key from backfill_work_items where item_kind = 'document'")}
    details = dict(gate.basis(), rule=RULE_VERSION)
    for f in candidate_filings(conn, gate.window):
        subject = keys.document(f["cse_filing_id"], f["path"])
        if subject["natural_key"] in known:
            out["existing"] += 1
            continue
        if f["uploaded_at"] is not None and not in_window(f["uploaded_at"], gate.window):
            continue                                       # never created: outside W (the query is W or undated)
        state, reason = first_state(f)
        _, created = store.ensure_item(conn, subject, first_state=state, reason=reason, details=details,
                                       wakeup_id=wakeup_id)
        if created:
            out["created"][state] = out["created"].get(state, 0) + 1
        else:
            out["existing"] += 1
    runs = filings_with_runs(conn, versions)

    def promote(item, state, reason, **refs):
        store.append_event(conn, item["id"], state, "promote" if state != "excluded" else "record", reason=reason,
                           details={"rule": RULE_VERSION}, wakeup_id=wakeup_id, **refs)
        out["promoted"][state] = out["promoted"].get(state, 0) + 1

    for item in document_items(conn, ("discovered", "pending", "retry_wait")):
        current = path_is_current(item)
        if item["state"] == "discovered":
            if item["uploaded_at"] is None:
                promote(item, "excluded", "window_undetermined")
                continue
            if not in_window(item["uploaded_at"], gate.window):
                promote(item, "excluded", "out_of_window")
                continue
        if not current:
            _anomaly(conn, PATH_SUPERSEDED, 4, item, wakeup_id, reason="F1's current path of the filing is not this "
                                                                          "item's path version")
            out["superseded"] += 1
            continue
        if item["state"] != "discovered" and not in_window(item["uploaded_at"], gate.window):
            _anomaly(conn, WINDOW_CHANGED, 3, item, wakeup_id, reason="the filing's upload date left the armed window")
            out["window_changed"] += 1
            continue
        run = evidence.persisted_run(conn, item, versions) if item["cse_filing_id"] in runs else None
        if run is not None:
            promote(item, "persisted", "evidence: an F5 run of this document under the armed versions",
                    f5_run_id=run)
        elif item["state"] == "discovered":
            promote(item, "pending", "eligible: in W, discovery closed, no F5 run of this document")
    return out
