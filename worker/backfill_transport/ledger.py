"""
The transport's PostgreSQL reads and writes, as the capture worker (cse_worker), through HB-1's frozen tables and
guards only (migration 0016). Nothing here changes a grant, a guard or a frozen table; the HB-1 store is reused where
its functions fit, and the few statements it lacks (an outcome together with its block in one transaction, a lease
with its stage, the spool-aware expiry in recovery.py) are written against the same tables, which the database guards
exactly as they guard the store's own writes.

Reads of P2 and P3 are read-only and mirror their frozen modules (worker/market_capture/runs.py,
worker/scheduler/store.py); unit tests compare the two.
"""
import json
from typing import Any

from ..financial_backfill import records as hb1_records, store as hb1_store
from ..market_capture import runs as p2runs
from ..scheduler import schedule as p3schedule
from . import gates

# worker/scheduler/store.py FINAL_STATES, mirrored (parity-tested): a final P3 item needs no capture today.
P3_FINAL_STATES = ("succeeded", "missed", "not_applicable")


def _q(conn, sql, args=(), fetch="all") -> Any:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = (cur.fetchall() if fetch == "all" else cur.fetchone()) if cur.description else None
        conn.commit()
    except BaseException:
        hb1_store._rollback(conn)
        raise
    return rows


def _json(value):
    return hb1_store._json(value)


# ------------------------------------------------------------------------------------------------ reads for the gates

def arming_in_force(conn):
    return hb1_store.arming_in_force(conn)


def phase2_blocks(conn):
    return tuple(b["block_id"] for b in hb1_store.unacknowledged_blocks(conn))


def unblocked_block_attempts(conn):
    """Attempts whose outcome is a block but that have no L9 block row. The transport writes both in one transaction,
    so this is empty unless something outside it wrote such an outcome; it is still treated as a block."""
    return tuple(r[0] for r in _q(conn, "select o.attempt_id from backfill_request_outcomes o where o.outcome_class = "
                                        "'block' and not exists (select 1 from backfill_blocks b where "
                                        "b.attempt_id = o.attempt_id) order by 1"))


def p2_blocks(conn):
    return tuple(b["run_id"] for b in p2runs.unacknowledged_blocks(conn))


def completed_lease_results(conn, stage, since):
    """Results of the stage's completed leases acquired since `since` (the arming in force), newest first."""
    rows = _q(conn, "select result from backfill_leases where state <> 'active' and details ->> 'stage' = %s and "
                    "acquired_at >= %s order by id desc limit 10", (stage, since))
    return [r[0] for r in rows]


def budget_view(conn, day, arming, p3):
    return gates.BudgetView(phase2_requests=hb1_store.requests_on_colombo_day(conn, day),
                            p2_requests=hb1_store.p2_requests_on_colombo_day(conn, day),
                            daily_request_budget=int(arming.get("daily_request_budget") or 0),
                            combined_daily_ceiling=arming.get("combined_daily_ceiling"),
                            p3_reserve=gates.p3_reserve(p3, day))


def p3_view(conn, day):
    """P3's latest settings row (the one in force), the calendar row and today's item, read-only."""
    s = _q(conn, "select armed, earliest_start_local, window_close_local, daily_request_budget from "
                 "market_schedule_settings order by id desc limit 1", fetch="one")
    cal = _q(conn, "select market_status from trading_calendar where trade_date = %s", (day,), fetch="one")
    item = _q(conn, "select state from market_schedule_item_state where work_kind = %s and trading_date = %s",
              (p3schedule.WORK_KIND, day), fetch="one")
    if s is None:
        return gates.P3View(closed_today=bool(cal and cal[0] == "closed"))
    return gates.P3View(armed=bool(s[0]), earliest_start_local=s[1], window_close_local=s[2],
                        daily_request_budget=int(s[3] or 0), closed_today=bool(cal and cal[0] == "closed"),
                        item_state=item[0] if item else None, item_final=bool(item and item[0] in P3_FINAL_STATES))


def last_runner_time(conn):
    return hb1_store.last_runner_time(conn)


def claims_since_requeue(conn, item_id):
    """Owner decision A2: L3 'claim' events of the item after its latest 'requeue' event."""
    return _q(conn, "select count(*) from backfill_item_events e where e.item_id = %s and e.action = 'claim' and "
                    "e.seq > coalesce((select max(r.seq) from backfill_item_events r where r.item_id = %s and "
                    "r.action = 'requeue'), 0)", (item_id, item_id), fetch="one")[0]


def seconds_since_last_ledger_request(conn):
    """How long ago the ledger's latest CSE request ended, conservatively: an attempt without an outcome counts as
    ending now, an 'unrecorded' one as ending when its slice was found dead. None when the ledger has no attempt."""
    row = _q(conn, "select extract(epoch from now() - max(case when o.attempt_id is null then now() "
                   "when o.outcome_class = 'unrecorded' then o.recorded_at "
                   "else coalesce(o.observed_at, o.requested_at, a.intended_at) end)) "
                   "from backfill_request_attempts a left join backfill_request_outcomes o on o.attempt_id = a.id",
             fetch="one")
    return None if row is None or row[0] is None else max(0.0, float(row[0]))


# ------------------------------------------------------------------------------------------------ slice rows

def open_lease(conn, wakeup_id, details):
    """The slice's lease (HB-1 guard: only the session holding P2's lock exclusively, one active lease system-wide),
    with its stage recorded for owner decision A5."""
    return _q(conn, "insert into backfill_leases (wakeup_id, state, details) values (%s, 'active', %s) returning id",
              (wakeup_id, _json(details)), fetch="one")[0]


# ------------------------------------------------------------------------------------------------ attempts

def record_intent(conn, item_id, lease_id, wakeup_id, *, request_class, request_host, endpoint, url, user_agent,
                  params, headers):
    """(attempt_id, attempt_no), committed BEFORE the request (HB-1 store)."""
    return hb1_store.record_intent(conn, item_id, lease_id, wakeup_id, request_class=request_class,
                                   request_host=request_host, endpoint=endpoint,
                                   http_method="POST" if request_class == "json" else "GET", url=url,
                                   user_agent=user_agent, params=params, headers=headers)


OUTCOME_FIELDS = ("outcome", "outcome_class", "requested_at", "observed_at", "elapsed_ms", "http_status",
                  "response_headers", "removed_response_headers", "response_bytes", "body_sha256", "parse_status",
                  "error", "spool_body_key", "spool_record_key", "recovered_from_spool", "details")


def insert_outcome_in(cur, attempt_id, o, body=None, block_reason=None, wakeup_id=None):
    """The outcome of one attempt, its JSON body (L5, content-addressed) and, for a block, its L9 block row, inside the
    caller's transaction. Returns the block id or None."""
    if body is not None:
        sha = hb1_store.archive_body_in(cur, body)
        if sha != o["body_sha256"]:
            raise ValueError("the archived body is not the spooled one")
    cur.execute("insert into backfill_request_outcomes (attempt_id, outcome, outcome_class, requested_at, observed_at, "
                "elapsed_ms, http_status, response_headers, removed_response_headers, response_bytes, body_sha256, "
                "parse_status, error, spool_body_key, spool_record_key, recovered_from_spool, details) values (%s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (attempt_id, o["outcome"], o["outcome_class"], o.get("requested_at"), o.get("observed_at"),
                 o.get("elapsed_ms"), o.get("http_status"),
                 None if o.get("response_headers") is None else _json(o["response_headers"]),
                 list(o.get("removed_response_headers") or []), o.get("response_bytes"), o.get("body_sha256"),
                 o.get("parse_status"), hb1_records.redact_temporary(o.get("error")), o.get("spool_body_key"),
                 o.get("spool_record_key"), bool(o.get("recovered_from_spool")), _json(o.get("details") or {})))
    if block_reason is None:
        return None
    cur.execute("insert into backfill_blocks (attempt_id, reason, details, wakeup_id) values (%s, %s, %s, %s) "
                "returning id", (attempt_id, block_reason, _json({"outcome": o["outcome"],
                                                                 "http_status": o.get("http_status")}), wakeup_id))
    return cur.fetchone()[0]


def record_outcome(conn, attempt_id, o, body=None, block_reason=None, wakeup_id=None):
    return hb1_store._tx(conn, lambda cur: insert_outcome_in(cur, attempt_id, o, body, block_reason, wakeup_id))


def heartbeat(conn, lease_id, wakeup_id):
    hb1_store.heartbeat_lease(conn, lease_id)
    if wakeup_id is not None:
        hb1_store.heartbeat_wakeup(conn, wakeup_id)


def dumps(value):
    return json.dumps(value, default=str, sort_keys=True)
