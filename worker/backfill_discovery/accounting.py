"""
Retry accounting (D-HB3-1, G10). Read-only.

    C  claims since the item's last re-queue: HB-2's own count (ledger.claims_since_requeue, owner decision A2)
    A  actual HTTP attempts since that re-queue: attempt intents under those claims' leases, counted whatever their
       outcome ('unrecorded' and recovered attempts included), except one proven NOT sent (HB-2 records a journal
       failure as outcome 'spool_failed' with details.sent = false before any request)

With attempts_per_json_request = 1, every claim makes at most one attempt, so A <= C. A claim can be used without an
attempt (a refusal or a crash between claim and intent); A never exceeds the armed maximum because C cannot.
"""
from ..backfill_transport import ledger as transport_ledger


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


_SINCE_REQUEUE = ("e.seq > coalesce((select max(r.seq) from backfill_item_events r where r.item_id = e.item_id "
                  "and r.action = 'requeue'), 0)")


def claims(conn, item_id):
    """C: HB-2's own count of claims since the last re-queue."""
    return int(transport_ledger.claims_since_requeue(conn, item_id))


def claim_events(conn, item_id):
    """[(seq, lease_id, occurred_at)] of the item's claims since its last re-queue, oldest first."""
    return _q(conn, f"select e.seq, e.lease_id, e.occurred_at from backfill_item_events e where e.item_id = %s and "
                    f"e.action = 'claim' and {_SINCE_REQUEUE} order by e.seq", (item_id,))


def http_attempts(conn, item_id):
    """A: actual HTTP attempts since the last re-queue."""
    return int(_q(conn, f"""
        select count(*) from backfill_request_attempts a
         left join backfill_request_outcomes o on o.attempt_id = a.id
         where a.item_id = %s
           and a.lease_id in (select e.lease_id from backfill_item_events e where e.item_id = a.item_id
                               and e.action = 'claim' and {_SINCE_REQUEUE})
           and not (o.outcome is not distinct from 'spool_failed'
                    and coalesce(o.details ->> 'sent', '') = 'false')""", (item_id,), fetch="one")[0])


def open_attempts(conn, item_id):
    """Attempt ids of the item without an outcome (only a live or a not-yet-expired slice can leave these)."""
    return [r[0] for r in _q(conn, "select a.id from backfill_request_attempts a where a.item_id = %s and not exists "
                                   "(select 1 from backfill_request_outcomes o where o.attempt_id = a.id) order by a.id",
                             (item_id,))]


def in_flight_items(conn, lease_id):
    """G2: items whose CURRENT state is in flight under this lease."""
    return [str(r[0]) for r in _q(conn, "select item_id from backfill_item_state where lease_id = %s and "
                                        "state in ('requesting', 'processing') order by item_id", (lease_id,))]


def counts(conn, item_id):
    return {"claims": claims(conn, item_id), "http_attempts": http_attempts(conn, item_id)}
