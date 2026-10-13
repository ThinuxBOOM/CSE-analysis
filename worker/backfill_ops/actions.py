"""
Operator actions (design section 20's commands; section 14.2: "re-queueing is an explicit operator action with a
reason, never automatic").

    requeue    a FINAL item back to pending, with a reason (HB-1's guarded 'requeue': at least 10 characters). Used
               after an investigated failure, after late evidence (M4: the validate and reconcile items of the runs
               F6.4 lists as pending again) or after a cleanup failure
    resume     a BLOCKED item back to pending, once the owner has acknowledged its block (HB-1's guarded 'resume')
    late_pass  the late-evidence link pass (section 7.4 step 5; HB-3's late_pass): newly qualifying sweeps, the owner's
               hold resolutions, resolve_securities and a full re-link of the current plan's window. No document is
               downloaded again; F6.4 re-validation (M4) then follows through re-queues of the affected items

Each validates with HB-1's own transition table first; the database guard (migration 0016) re-checks every rule.
"""
from ..backfill_discovery import identity as hb3_identity
from ..financial_backfill import states, store
from . import NOTE_MIN
from .errors import OpsRefused


def _item(conn, natural_key):
    item = store.item_by_key(conn, natural_key)
    if item is None:
        raise OpsRefused([("item", f"no work item {natural_key!r}")])
    cur = store.current_state(conn, item["id"]) or {}
    return item, cur.get("state")


def _transition(conn, natural_key, to_state, action, reason):
    reason = (reason or "").strip()
    if len(reason) < NOTE_MIN:
        raise OpsRefused([("reason", f"a reason of at least {NOTE_MIN} characters is required")])
    item, state = _item(conn, natural_key)
    try:
        states.check(item["item_kind"], state, to_state, action, reason)
    except states.IllegalTransition as exc:
        raise OpsRefused([(action, str(exc))]) from None
    seq = store.append_event(conn, item["id"], to_state, action, reason=reason,
                             details={"by": "hb.ops.1", "previous_state": state})
    return {"item": item["natural_key"], "kind": item["item_kind"], "from": state, "to": to_state, "seq": seq}


def requeue(conn, natural_key, reason):
    """A final item (failed, succeeded, retrieval_failed, consumer_failed, cleanup_failed, excluded, ...) -> pending."""
    item, state = _item(conn, natural_key)
    if state not in states.FINAL.get(item["item_kind"], ()):
        raise OpsRefused([("requeue", f"{natural_key} is {state!r}, not final: only a final item is re-queued")])
    return _transition(conn, natural_key, "pending", "requeue", reason)


def resume(conn, natural_key, reason):
    """A blocked item -> pending; the database refuses unless the owner acknowledged its block."""
    _, state = _item(conn, natural_key)
    if state != "blocked":
        raise OpsRefused([("resume", f"{natural_key} is {state!r}, not blocked")])
    return _transition(conn, natural_key, "pending", "resume", reason)


def late_pass(conn, wall, *, wakeup_id=None, preflight=None):
    """HB-3's late-evidence pass (refuses while a CSE slice is active, or before the current plan's IE-4 pass)."""
    res = hb3_identity.late_pass(conn, wall=wall, wakeup_id=wakeup_id, preflight=preflight)
    return {k: res.get(k) for k in ("item", "plan_fingerprint", "ie4_pass", "sweeps", "imported_new", "held",
                                    "resolutions")}
