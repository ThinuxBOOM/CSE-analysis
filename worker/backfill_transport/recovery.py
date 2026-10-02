"""
Expiry of dead CSE slices, spool first (design sections 14.2 and 15.3).

HB-1's store.expire_dead_leases closes every outcome-less attempt 'unrecorded' in the same transaction that expires the
lease, which would make a response already in the spool unrecoverable (an outcome is written once). The transport
therefore expires dead leases itself, against the same tables and guards, in this order, ONE transaction per lease:

  1. the lease is expired (only while this session holds P2's lock exclusively, and only if it is still active);
  2. for each of its attempts without an outcome, the lease's journal is consulted:
       - a complete, verified spool record -> its outcome, recovered_from_spool, with the archived body (L5) and, for a
         block, its L9 block row: NO new request;
       - otherwise -> 'unrecorded' (the request may have reached CSE; it still counts towards the day's budget);
  3. its in-flight items become 'abandoned' (the HB-1 transition 'expire'); their attempt and claim counts stay.

Rerunning it changes nothing: a lease already expired is skipped, and an outcome is never written twice. A spool record
that is present but does not verify stops the expiry (the transaction rolls back): it needs an operator, never a guess.
"""
from ..financial_backfill import states as hb1_states, store as hb1_store
from . import journal, ledger


class RecoveryError(Exception):
    pass


def expire_dead(conn, by_wakeup_id, spool_root):
    """Inside a slice holding P2's lock: expire every other active lease, recovering its spooled attempts first."""
    if not hb1_store.holds_cse_lock(conn):
        raise RecoveryError("dead slices are expired only by a slice that holds P2's global lock exclusively")
    rows = ledger._q(conn, "select id from backfill_leases where state = 'active' and not (holder_pid = "
                           "pg_backend_pid() and holder_started_at = hb_session_started()) order by id")
    out = []
    for (lease_id,) in rows:
        result = hb1_store._tx(conn, lambda cur, lease_id=lease_id: _expire_one(cur, lease_id, by_wakeup_id,
                                                                                spool_root))
        if result is not None:
            out.append(result)
    return out


def _expire_one(cur, lease_id, by_wakeup_id, spool_root):
    cur.execute("update backfill_leases set state = 'expired', released_at = now(), expired_by_wakeup = %s, "
                "result = 'expired' where id = %s and state = 'active' returning id", (by_wakeup_id, lease_id))
    if cur.fetchone() is None:
        return None
    cur.execute("select a.id from backfill_request_attempts a where a.lease_id = %s and not exists (select 1 from "
                "backfill_request_outcomes o where o.attempt_id = a.id) order by a.id", (lease_id,))
    open_attempts = [r[0] for r in cur.fetchall()]
    spooled = journal.Journal(spool_root, lease_id).spooled()
    recovered, unrecorded, blocks = [], [], []
    for attempt_id in open_attempts:
        key = spooled.get(int(attempt_id))
        if key is None:
            ledger.insert_outcome_in(cur, attempt_id, {"outcome": "unrecorded", "outcome_class": "unrecorded",
                                                       "error": hb1_store.OPEN_LEASE_ERROR})
            unrecorded.append(attempt_id)
            continue
        try:
            rec, body = journal.load_record(spool_root, key, attempt_id)
        except journal.RecordCorrupt as exc:
            raise RecoveryError(f"lease {lease_id}: {exc}") from None
        o = {k: rec.get(k) for k in ledger.OUTCOME_FIELDS if k not in ("spool_record_key", "recovered_from_spool")}
        o.update(spool_record_key=key, recovered_from_spool=True,
                 details=dict(rec.get("details") or {}, recovered_by_wakeup=by_wakeup_id))
        block_id = ledger.insert_outcome_in(cur, attempt_id, o, body, rec.get("block_reason"), by_wakeup_id)
        recovered.append(attempt_id)
        if block_id is not None:
            blocks.append(block_id)
    cur.execute("select s.item_id from backfill_item_state s where s.lease_id = %s and s.state = any(%s) "
                "order by s.item_id", (lease_id, list(hb1_states.IN_FLIGHT)))
    items = [str(r[0]) for r in cur.fetchall()]
    for item_id in items:
        hb1_store.append_event_in(cur, item_id, "abandoned", "expire", lease_id=lease_id, wakeup_id=by_wakeup_id,
                                  reason="its CSE slice ended without recording an outcome")
    return {"lease_id": lease_id, "recovered_from_spool": recovered, "unrecorded": unrecorded, "blocks": blocks,
            "items_abandoned": items}
