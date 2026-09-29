"""
Database access for the scheduler (migration 0014 tables, P2's run ledger and archive read-only, trading_calendar).

Everything runs as the capture worker (cse_worker) except record_settings_as_owner, which is the owner path (the
same one as P2's acknowledge-block): P1's owner-delegation login cse_migrator, acting as cse_owner through SET LOCAL
ROLE for exactly one INSERT. Nothing here updates or deletes evidence; the only UPDATEs are to the scheduler's own
lease rows (heartbeat, release, expiry), which the table's guard trigger restricts.
"""
import json
from typing import Any

from ..market_capture import derive
from . import schedule as sched

OWNER_PATH_ROLE = "cse_migrator"
FINAL_STATES = ("succeeded", "missed", "not_applicable")
SETTINGS_COLUMNS = ("id", "armed", "work_kind", "start_date", "earliest_start_local", "window_close_local",
                    "retry_base_minutes", "retry_max_minutes", "max_attempts", "no_session_confirmations",
                    "daily_request_budget", "max_catch_up_days", "stale_lease_minutes",
                    "max_capture_actions_per_wakeup", "user_agent", "host", "expected_requests", "stop_conditions",
                    "note", "approved_by", "created_at")


class OwnerPathRequired(Exception):
    """An owner decision was attempted from a login other than the owner path."""


def _q(conn, sql, args=(), fetch="all") -> Any:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = (cur.fetchall() if fetch == "all" else cur.fetchone()) if cur.description else None
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return rows


def _rollback(conn):
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — the connection itself may be gone
        pass


def _json(value):
    from psycopg2.extras import Json
    return Json(value, dumps=lambda v: json.dumps(v, default=str, sort_keys=True))


# ------------------------------------------------------------------------------------------------ settings

CURRENT_SETTINGS_SQL = f"select {', '.join(SETTINGS_COLUMNS)} from market_schedule_settings order by id desc limit 1"


def _settings(row):
    if row is None:
        return sched.DISARMED
    d = dict(zip(SETTINGS_COLUMNS, row))
    d["stop_conditions"] = tuple(d["stop_conditions"] or ())
    return sched.ScheduleSettings(**d)


def current_settings(conn):
    return _settings(_q(conn, CURRENT_SETTINGS_SQL, fetch="one"))


def current_settings_as_owner(conn):
    """The owner-delegation login (NOINHERIT) can read nothing on its own: read as cse_owner inside a transaction that
    is rolled back (the same pattern as P1's migration runner status)."""
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute("set local role cse_owner")
            cur.execute(CURRENT_SETTINGS_SQL)
            row = cur.fetchone()
    finally:
        _rollback(conn)
    return _settings(row)


def settings_history(conn, limit=10):
    rows = _q(conn, "select id, armed, start_date, host, user_agent, daily_request_budget, note, approved_by, "
                    "created_at from market_schedule_settings order by id desc limit %s", (limit,))
    keys = ("id", "armed", "start_date", "host", "user_agent", "daily_request_budget", "note", "approved_by",
            "created_at")
    return [dict(zip(keys, r)) for r in rows]


def record_settings_as_owner(conn, settings, os_user):
    """Owner decision (arm / re-arm / disarm): accepted ONLY from the owner-delegation login (cse_migrator, reachable
    only by root via `sudo ops/bin/cse-scheduler arm|disarm`), acting as cse_owner for exactly this one INSERT. The
    worker has no INSERT privilege on the table, is not a member of cse_owner, and the table's guard trigger refuses
    any service-role session - the same three barriers as P2's block acknowledgement."""
    settings.validate()
    who = _q(conn, "select session_user", fetch="one")[0]
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"scheduler settings are an owner decision (G-1): connected as {who!r}; run it with "
                                f"`sudo bash ops/bin/cse-scheduler arm|disarm ...`, which runs as {OWNER_PATH_ROLE}")
    s = settings
    try:
        with conn.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute(
                "insert into market_schedule_settings (armed, work_kind, start_date, earliest_start_local, "
                "window_close_local, retry_base_minutes, retry_max_minutes, max_attempts, no_session_confirmations, "
                "daily_request_budget, max_catch_up_days, stale_lease_minutes, max_capture_actions_per_wakeup, "
                "user_agent, host, expected_requests, stop_conditions, note, os_user) values (%s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (s.armed, s.work_kind, s.start_date, s.earliest_start_local, s.window_close_local,
                 s.retry_base_minutes, s.retry_max_minutes, s.max_attempts, s.no_session_confirmations,
                 s.daily_request_budget, s.max_catch_up_days, s.stale_lease_minutes, s.max_capture_actions_per_wakeup,
                 s.user_agent, s.host, s.expected_requests, _json(list(s.stop_conditions)), s.note, os_user))
            new_id = cur.fetchone()[0]
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return new_id


# ------------------------------------------------------------------------------------------------ wake-ups (lease)

def start_wakeup(conn, *, trigger, scheduler_time, settings_id, host, pid, boot_id, os_user, tool_version,
                 code_revision):
    return _q(conn, "insert into market_schedule_wakeups (state, trigger_kind, scheduler_time, host, pid, boot_id, "
                    "os_user, tool_version, code_revision, settings_id) values ('active', %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s) returning id",
              (trigger, scheduler_time, host, pid, boot_id, os_user, tool_version, code_revision, settings_id),
              fetch="one")[0]


def heartbeat(conn, wake_id):
    return _q(conn, "update market_schedule_wakeups set heartbeat_at = greatest(now(), heartbeat_at) "
                    "where id = %s and state = 'active' returning heartbeat_at", (wake_id,), fetch="one")


def finish_wakeup(conn, wake_id, result, details, error=None):
    _q(conn, "update market_schedule_wakeups set state = 'released', finished_at = now(), "
             "heartbeat_at = greatest(now(), heartbeat_at), result = %s, details = %s, error = %s "
             "where id = %s and state = 'active'", (result, _json(details), error, wake_id))


def expire_dead_leases(conn, by_id):
    """Close every OTHER active lease. Call ONLY while holding the global capture lock: its holders are then known to
    be dead (the lock is released with their connection), so nothing alive is ever expired."""
    rows = _q(conn, "update market_schedule_wakeups set state = 'expired', finished_at = now(), expired_by = %s, "
                    "result = 'expired', error = 'the process ended without releasing its lease (the capture lock was "
                    "free when wake-up ' || %s || ' started)' where state = 'active' and id <> %s "
                    "returning id, heartbeat_at, pid, host", (by_id, str(by_id), by_id))
    return [{"id": r[0], "heartbeat_at": r[1], "pid": r[2], "host": r[3]} for r in rows]


def record_skipped(conn, *, trigger, scheduler_time, result, details, host, pid, boot_id, os_user, tool_version,
                   code_revision, settings_id):
    return _q(conn, "insert into market_schedule_wakeups (state, trigger_kind, scheduler_time, finished_at, host, "
                    "pid, boot_id, os_user, tool_version, code_revision, settings_id, result, details) values "
                    "('skipped', %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
              (trigger, scheduler_time, host, pid, boot_id, os_user, tool_version, code_revision, settings_id,
               result, _json(details)), fetch="one")[0]


def active_lease(conn):
    row = _q(conn, "select id, started_at, heartbeat_at, extract(epoch from (now() - heartbeat_at)), host, pid, "
                   "trigger_kind from market_schedule_wakeups where state = 'active' order by id desc limit 1",
             fetch="one")
    if row is None:
        return None
    return {"id": row[0], "started_at": row[1], "heartbeat_at": row[2], "heartbeat_age_seconds": float(row[3]),
            "host": row[4], "pid": row[5], "trigger": row[6]}


def last_scheduler_time(conn):
    return _q(conn, "select max(scheduler_time) from market_schedule_wakeups where state <> 'skipped'",
              fetch="one")[0]


def recent_wakeups(conn, limit=5):
    rows = _q(conn, "select id, state, trigger_kind, scheduler_time, started_at, heartbeat_at, finished_at, result, "
                    "error, host, pid from market_schedule_wakeups order by id desc limit %s", (limit,))
    keys = ("id", "state", "trigger", "scheduler_time", "started_at", "heartbeat_at", "finished_at", "result", "error",
            "host", "pid")
    return [dict(zip(keys, r)) for r in rows]


# ------------------------------------------------------------------------------------------------ work items

ITEM_COLUMNS = ("id", "work_kind", "trading_date", "capture_mode", "due_at", "window_closes_at", "discovered_at",
                "origin", "reason", "settings_id", "schedule", "created_at")


def _item(row):
    d = dict(zip(ITEM_COLUMNS, row))
    d["id"] = str(d["id"])
    return d


def create_item(conn, *, trading_date, due_at, window_closes_at, discovered_at, origin, reason, settings_id,
                schedule, wakeup_id, scheduler_time):
    """(item, created). One transaction: the item and its first event ('pending'). The unique (work kind, trading
    date) key makes a second creation - by a second timer firing, a second process or a restart - a no-op that
    returns the existing item."""
    try:
        with conn.cursor() as cur:
            cur.execute("insert into market_schedule_items (work_kind, trading_date, capture_mode, due_at, "
                        "window_closes_at, discovered_at, origin, reason, settings_id, schedule, wakeup_id) values "
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (work_kind, trading_date) do nothing "
                        f"returning {', '.join(ITEM_COLUMNS)}",
                        (sched.WORK_KIND, trading_date, sched.CAPTURE_MODE, due_at, window_closes_at, discovered_at,
                         origin, reason, settings_id, _json(schedule), wakeup_id))
            row = cur.fetchone()
            created = row is not None
            if created:
                cur.execute("insert into market_schedule_item_events (item_id, seq, state, action, scheduler_time, "
                            "reason, details, wakeup_id) values (%s, 1, 'pending', 'create', %s, %s, %s, %s)",
                            (row[0], scheduler_time, f"{origin}: work item created", _json({"origin": origin}),
                             wakeup_id))
            else:
                cur.execute(f"select {', '.join(ITEM_COLUMNS)} from market_schedule_items where work_kind = %s and "
                            f"trading_date = %s", (sched.WORK_KIND, trading_date))
                row = cur.fetchone()
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return _item(row), created


def append_item_event(conn, item_id, state, action, *, scheduler_time, run_id=None, reason=None, details=None,
                      wakeup_id=None):
    return _q(conn, "insert into market_schedule_item_events (item_id, seq, state, action, run_id, scheduler_time, "
                    "reason, details, wakeup_id) select %s, coalesce(max(seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s "
                    "from market_schedule_item_events where item_id = %s returning seq",
              (item_id, state, action, run_id, scheduler_time, reason, _json(details or {}), wakeup_id, item_id),
              fetch="one")[0]


def item_for_date(conn, trading_date):
    row = _q(conn, f"select {', '.join(ITEM_COLUMNS)} from market_schedule_items where work_kind = %s and "
                   f"trading_date = %s", (sched.WORK_KIND, trading_date), fetch="one")
    return _item(row) if row else None


def item_dates(conn, first, last):
    return {r[0] for r in _q(conn, "select trading_date from market_schedule_items where work_kind = %s and "
                                   "trading_date between %s and %s", (sched.WORK_KIND, first, last))}


def item_events(conn, item_id):
    rows = _q(conn, "select seq, state, action, run_id, scheduler_time, reason, details, wakeup_id, occurred_at "
                    "from market_schedule_item_events where item_id = %s order by seq", (item_id,))
    keys = ("seq", "state", "action", "run_id", "scheduler_time", "reason", "details", "wakeup_id", "occurred_at")
    out = []
    for r in rows:
        e = dict(zip(keys, r))
        e["run_id"] = str(e["run_id"]) if e["run_id"] else None
        out.append(e)
    return out


def open_items(conn):
    """Every item still open: its latest state is not final and it was not closed for capture ('finalize' after its
    window closed on a partial / blocked capture). Oldest trading date first - the deterministic processing order."""
    rows = _q(conn, f"select {', '.join('i.' + c for c in ITEM_COLUMNS)} from market_schedule_items i "
                    f"join market_schedule_item_state s on s.item_id = i.id where s.state not in %s "
                    f"and s.action <> 'finalize' order by i.trading_date, i.id", (FINAL_STATES,))
    return [_item(r) for r in rows]


def items_by_state(conn, states=None, first=None, last=None):
    sql = ("select s.item_id, s.trading_date, s.state, s.action, s.run_id, s.reason, s.origin, s.due_at, "
           "s.window_closes_at, s.discovered_at, s.occurred_at from market_schedule_item_state s where true")
    args = []
    if states:
        sql += " and s.state = any(%s)"
        args.append(list(states))
    if first:
        sql += " and s.trading_date >= %s"
        args.append(first)
    if last:
        sql += " and s.trading_date <= %s"
        args.append(last)
    rows = _q(conn, sql + " order by s.trading_date", tuple(args))
    keys = ("item_id", "trading_date", "state", "action", "run_id", "reason", "origin", "due_at", "window_closes_at",
            "discovered_at", "since")
    return [dict(zip(keys, r)) for r in rows]


def state_counts(conn):
    return dict(_q(conn, "select state, count(*) from market_schedule_item_state group by state"))


# ------------------------------------------------------------------------------------------------ P2 evidence (read)

def runs_for_date(conn, trading_date, capture_mode=sched.CAPTURE_MODE):
    """Every P2 market-capture run of (date, mode) - scheduler and manual alike - with its current state."""
    rows = _q(conn, "select r.id, s.state, r.created_at, r.trading_date_basis, r.user_agent is null, "
                    "r.policy ->> 'name', s.reason from market_capture_runs r join market_capture_run_state s "
                    "on s.run_id = r.id where r.run_kind = 'market_capture' and r.trading_date = %s and "
                    "r.capture_mode = %s order by r.created_at, r.id", (trading_date, capture_mode))
    return [{"id": str(r[0]), "state": r[1], "created_at": r[2], "basis": r[3],
             "missed_record": bool(r[4]) and r[5] == "missed_record", "reason": r[6]} for r in rows]


def run_facts(conn, run, trading_date):
    """What the archive proves about one run: a successful tradeSummary archived (A), which session that snapshot
    shows (E1/E2, recomputed from the exact archived bytes), and whether observations were derived from it."""
    facts = dict(run, a_captured=False, session_date=None, e1=None, all_closing_prices_published=None,
                 derived=False)
    if run["missed_record"]:
        return facts
    ok = derive.ok_attempts(conn, run["id"])
    if "tradeSummary" in ok:
        facts["a_captured"] = True
        ev = derive.session_evidence(derive.body_of(conn, ok["tradeSummary"]["body_sha256"])[1], trading_date,
                                     sched.CAPTURE_MODE)
        facts["session_date"] = ev["latest_session_date_colombo"]
        facts["e1"] = ev["session_matches_trading_date"]
        facts["all_closing_prices_published"] = ev["all_closing_prices_published"]
    facts["derived"] = _q(conn, "select exists (select 1 from raw_market_observations where request_attempt_id = %s)",
                          (run["id"],), fetch="one")[0]
    return facts


def requests_on_colombo_day(conn, day):
    """CSE request attempts archived during that Colombo calendar day, by ANY run (scheduler or manual)."""
    start, end = sched.colombo_day_bounds(day)
    return _q(conn, "select count(*) from market_source_responses where requested_at >= %s and requested_at < %s",
              (start, end), fetch="one")[0]


# ------------------------------------------------------------------------------------------------ trading_calendar

def calendar_rows(conn, first, last):
    rows = _q(conn, "select trade_date, market_status, established_by, established_at, notes from trading_calendar "
                    "where trade_date between %s and %s order by trade_date", (first, last))
    return {r[0]: {"market_status": r[1], "established_by": r[2], "established_at": r[3], "notes": r[4]}
            for r in rows}


def declare_closed(conn, trading_date, reference):
    """A KNOWN non-trading day, from CSE's own published notice (the reference is required and kept). Never
    automatic. trading_calendar cannot be updated by the worker, so an existing row is reported, not changed."""
    row = _q(conn, "insert into trading_calendar (trade_date, market_status, established_by, notes) values "
                   "(%s, 'closed', 'cse_notice', %s) on conflict (trade_date) do nothing returning trade_date",
             (trading_date, reference), fetch="one")
    return row is not None


def record_session_open(conn, trading_date, notes):
    """(inserted, existing row or None): the date's own session was proven by live capture (P2 E1)."""
    row = _q(conn, "insert into trading_calendar (trade_date, market_status, established_by, notes) values "
                   "(%s, 'open', 'live_capture', %s) on conflict (trade_date) do nothing returning trade_date",
             (trading_date, notes), fetch="one")
    if row is not None:
        return True, None
    existing = calendar_rows(conn, trading_date, trading_date).get(trading_date)
    return False, existing
