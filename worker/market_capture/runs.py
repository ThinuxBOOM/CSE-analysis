"""
Capture-run ledger (migration 0012): immutable run rows, the append-only state history, the global capture lock,
abandoned-run detection, the G-1 block gate and the capture role's security preflight.

States: pending, running, succeeded, partial, failed, missed, blocked, abandoned (transitions enforced by a trigger;
see 0012). The latest event is the current state. Nothing is ever updated or deleted.
"""
import getpass
import os
import socket
from typing import Any

P2_TABLES = ("market_capture_runs", "market_capture_run_events", "market_capture_block_acknowledgements",
             "market_response_bodies", "market_source_responses", "market_capture_security_results")
# One P2 process at a time may contact CSE (G-1: no parallel requests, even across processes). Session-level, so it
# is released automatically when the process dies - which is how a dead 'running' run is recognised as abandoned.
GLOBAL_LOCK_KEY = 4_346_836_117_002_312          # the migration runner uses ...311
TERMINAL = ("succeeded", "partial", "failed", "missed", "blocked", "abandoned")
RESUMABLE = ("partial", "failed", "abandoned", "blocked")


class RunRefused(Exception):
    """A capture/resume/sweep may not start (nothing was requested from CSE)."""


def os_user():
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USER") or os.environ.get("USERNAME")


def _q(conn, sql, args=(), fetch="all") -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = (cur.fetchall() if fetch == "all" else cur.fetchone()) if cur.description else None
    conn.commit()
    return rows


def acquire_global_lock(conn):
    return _q(conn, "select pg_try_advisory_lock(%s)", (GLOBAL_LOCK_KEY,), fetch="one")[0]


def release_global_lock(conn):
    _q(conn, "select pg_advisory_unlock(%s)", (GLOBAL_LOCK_KEY,), fetch="one")


def create_run(conn, *, run_kind, trading_date, capture_mode, policy, user_agent, tool_version, code_revision,
               basis="operator"):
    from psycopg2.extras import Json
    try:
        with conn.cursor() as cur:
            cur.execute("insert into market_capture_runs (run_kind, trading_date, trading_date_basis, capture_mode, "
                        "policy, user_agent, tool_version, code_revision, host, os_user) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                        (run_kind, trading_date, basis, capture_mode, Json(policy), user_agent, tool_version,
                         code_revision, socket.gethostname(), os_user()))
            run_id = str(cur.fetchone()[0])
            cur.execute("insert into market_capture_run_events (run_id, seq, state, reason) values (%s, 1, 'pending', "
                        "'created')", (run_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return run_id


def append_event(conn, run_id, state, reason=None, details=None):
    from psycopg2.extras import Json
    try:
        with conn.cursor() as cur:
            cur.execute("insert into market_capture_run_events (run_id, seq, state, reason, details) "
                        "select %s, coalesce(max(seq), 0) + 1, %s, %s, %s from market_capture_run_events "
                        "where run_id = %s returning seq",
                        (run_id, state, reason, Json(details or {}, dumps=_dumps), run_id))
            seq = cur.fetchone()[0]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return seq


def _dumps(value):
    import json
    return json.dumps(value, default=str, sort_keys=True)


def get_run(conn, run_id):
    row = _q(conn, "select id, run_kind, trading_date, capture_mode, policy, user_agent, tool_version, created_at, "
                   "trading_date_basis from market_capture_runs where id = %s", (run_id,), fetch="one")
    if row is None:
        return None
    keys = ("id", "run_kind", "trading_date", "capture_mode", "policy", "user_agent", "tool_version", "created_at",
            "trading_date_basis")
    run = dict(zip(keys, row))
    run["id"] = str(run["id"])
    return run


def current_state(conn, run_id):
    row = _q(conn, "select state, seq, reason, details from market_capture_run_events where run_id = %s "
                   "order by seq desc limit 1", (run_id,), fetch="one")
    return {"state": row[0], "seq": row[1], "reason": row[2], "details": row[3]} if row else None


def history(conn, run_id):
    rows = _q(conn, "select seq, state, occurred_at, reason, details, recorded_by from market_capture_run_events "
                    "where run_id = %s order by seq", (run_id,))
    return [{"seq": r[0], "state": r[1], "occurred_at": r[2], "reason": r[3], "details": r[4], "recorded_by": r[5]}
            for r in rows]


def mark_abandoned(conn, log=lambda m: None):
    """Call ONLY while holding the global capture lock: then no P2 process is alive, so every run still 'running'
    ended without a terminal state (crash, kill, power loss). Records 'abandoned'; the run can be resumed."""
    rows = _q(conn, "select run_id from market_capture_run_state where state = 'running' order by created_at")
    out = []
    for (rid,) in rows:
        append_event(conn, str(rid), "abandoned", "process ended without recording a terminal state",
                     {"detected_by": os_user(), "host": socket.gethostname()})
        log(f"run {rid}: marked abandoned (it was still 'running' with no live capture process)")
        out.append(str(rid))
    return out


def unacknowledged_blocks(conn):
    rows = _q(conn, "select s.run_id, s.trading_date, s.capture_mode, s.reason from market_capture_run_state s "
                    "where s.state = 'blocked' and not exists (select 1 from market_capture_block_acknowledgements a "
                    "where a.run_id = s.run_id) order by s.created_at")
    return [{"run_id": str(r[0]), "trading_date": str(r[1]), "capture_mode": r[2], "reason": r[3]} for r in rows]


def acknowledge_block(conn, run_id, note):
    try:
        with conn.cursor() as cur:
            cur.execute("insert into market_capture_block_acknowledgements (run_id, note, os_user) values (%s, %s, %s) "
                        "returning id", (run_id, note, os_user()))
            ack = str(cur.fetchone()[0])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return ack


def seconds_since_last_request(conn, now):
    """How long ago the archive's most recent attempt ended (cross-process request spacing), or None."""
    row = _q(conn, "select max(coalesce(observed_at, requested_at)) from market_source_responses", fetch="one")
    if not row or row[0] is None:
        return None
    return max(0.0, (now - row[0]).total_seconds())


def security_preflight(conn, expected_role):
    """Problems (list of str) with the capture role; [] means the P1 role model holds for P2. Checked before every
    CSE-contacting command: the worker must not be a superuser, an owner, the migration role or the backup role,
    must not be able to rewrite archived evidence, and the append-only triggers must be in place."""
    import psycopg2
    problems = []
    who = _q(conn, "select current_user, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls "
                   "from pg_roles r where r.rolname = current_user", fetch="one")
    user = who[0]
    if any(who[1:]):
        problems.append(f"{user} has superuser/createrole/createdb/replication/bypassrls")
    if user != expected_role:
        return problems + [f"connected as {user!r}; P2 capture must run as {expected_role!r}"]
    try:
        return problems + _role_checks(conn, user)
    except psycopg2.Error as exc:
        conn.rollback()
        return problems + [f"cannot verify {user}'s privileges: {type(exc).__name__}: {exc}".strip()]


def _role_checks(conn, user):
    problems = []
    for role in ("cse_owner", "cse_migrator", "cse_backup", "pg_read_all_data"):
        if _q(conn, "select case when exists (select 1 from pg_roles where rolname = %s) "
                    "then pg_has_role(current_user, %s, 'MEMBER') else false end", (role, role), fetch="one")[0]:
            problems.append(f"{user} is a member of {role}")
    owned = _q(conn, "select string_agg(tablename, ', ') from pg_tables where schemaname = 'public' "
                     "and tableowner = current_user", fetch="one")[0]
    if owned:
        problems.append(f"{user} owns tables: {owned}")
    for t in P2_TABLES + ("raw_market_observations",):
        if _q(conn, "select to_regclass(%s)", (f"public.{t}",), fetch="one")[0] is None:
            problems.append(f"table {t} missing (migration 0012 not applied?)")
            continue
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            if _q(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{t}", priv), fetch="one")[0]:
                problems.append(f"{user} has {priv} on {t}")
        if not _q(conn, "select has_table_privilege(current_user, %s, 'INSERT') and "
                        "has_table_privilege(current_user, %s, 'SELECT')", (f"public.{t}", f"public.{t}"),
                  fetch="one")[0]:
            problems.append(f"{user} lacks SELECT/INSERT on {t}")
        trig = _q(conn, "select coalesce(bool_or(tgenabled = 'O' and (tgtype & 1) = 1 and (tgtype & 24) <> 0), false), "
                        "coalesce(bool_or(tgenabled = 'O' and (tgtype & 32) <> 0), false) from pg_trigger "
                        "where tgrelid = %s::regclass and not tgisinternal", (f"public.{t}",), fetch="one")
        if not (trig[0] and trig[1]):
            problems.append(f"append-only triggers missing or disabled on {t}")
    for t, privs in (("daily_market_data", ("INSERT", "UPDATE")), ("companies", ("INSERT", "UPDATE", "SELECT"))):
        for priv in privs:
            if not _q(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{t}", priv), fetch="one")[0]:
                problems.append(f"{user} lacks {priv} on {t} (needed by the frozen Stage E writes)")
    return problems
