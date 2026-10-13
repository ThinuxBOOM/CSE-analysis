"""
The single runner (design section 20; the HB-5 freeze observation "HB-6 should run F6 work from one runner").

F6 work, the audit and the stage bookkeeping never take P2's lock ...312 (section 16.5, F6.4 section 15.7), and no new
advisory-lock key is introduced (HB-B8). The runner's claim is therefore its own HB-1 wake-up row (L8: holder,
heartbeat, outcome), whose recording session HB-1's guard identifies and whose liveness hb_session_alive reports:

    one SERIALIZABLE transaction:
        read every ACTIVE runner wake-up (tool_version hb.ops.1) whose recording session is alive
        none  -> HB-1's start_wakeup records this runner's active wake-up, in the same transaction
        some  -> nothing recorded in it; a finished 'busy' wake-up is recorded instead, naming the holder

Two runners racing through that transaction cannot both commit: each read the predicate the other's insert changes, so
PostgreSQL's serializable isolation aborts one (SQLSTATE 40001), which then records its 'busy' wake-up. A live holder
is never taken over. A runner that died (its session gone) leaves an active row that HB-1's expire_dead_wakeups
closes; HB-1's guard re-checks that the session is dead. HB-2's slices record their own wake-ups (another tool
version): a slice runs only inside the runner that opened it, so it is never another runner.
"""
from ..financial_backfill import store
from . import TOOL_VERSION

HOLDER_COLUMNS = ("id", "host", "pid", "os_user", "trigger_kind", "started_at", "heartbeat_at")
LIVE_RUNNERS_SQL = (f"select {', '.join(HOLDER_COLUMNS)} from backfill_wakeups where state = 'active' and "
                    "tool_version = %s and hb_session_alive(backend_pid, backend_started_at) order by id")
SERIALIZATION_FAILURE = "40001"


def live_runners(conn):
    """Every active runner wake-up whose recording session is alive (read-only)."""
    try:
        with conn.cursor() as cur:
            cur.execute(LIVE_RUNNERS_SQL, (TOOL_VERSION,))
            rows = cur.fetchall()
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return [dict(zip(HOLDER_COLUMNS, r)) for r in rows]


def _busy(conn, ident, trigger, runner_time, details):
    store.record_skipped_wakeup(conn, trigger=trigger, runner_time=runner_time, result="busy", details=details, **ident)


def acquire(conn, *, trigger, runner_time, ident):
    """(wakeup_id, None) when this process is now THE runner; (None, holder) when another runner holds the claim.
    `conn` is the runner's control connection: its session holds the claim until it finishes or dies."""
    import psycopg2
    conn.rollback()
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level serializable")
            cur.execute(LIVE_RUNNERS_SQL, (TOOL_VERSION,))
            live = [dict(zip(HOLDER_COLUMNS, r)) for r in cur.fetchall()]
        if live:
            conn.rollback()
            holder = live[0]
            _busy(conn, ident, trigger, runner_time,
                  {"reason": "another runner holds the claim", "holder_wakeup": holder["id"],
                   "holder_host": holder["host"], "holder_pid": holder["pid"]})
            return None, holder
        # HB-1's own insert, in the SAME serializable transaction (store._q executes it, then commits)
        return store.start_wakeup(conn, trigger=trigger, runner_time=runner_time, **ident), None
    except psycopg2.Error as exc:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        if getattr(exc, "pgcode", None) != SERIALIZATION_FAILURE:
            raise
        _busy(conn, ident, trigger, runner_time, {"reason": "another runner claimed at the same moment "
                                                            "(serialization failure)"})
        return None, {"concurrent": True}
