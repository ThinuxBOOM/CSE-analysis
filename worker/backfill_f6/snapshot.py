"""
One read-only snapshot (design section 18.1: "one REPEATABLE READ, read-only snapshot, as the real-data validation's
snapshot"), and the window W the audit and the readiness rule judge filings by.

W is configuration, not semantics (HB-W5): it is the window of the owner's arming decision in force. A disarm records
no window, so after one W must be given explicitly; it is never invented or taken from an older decision.
"""
import contextlib
from datetime import date

from ..backfill_discovery import plan as hb3_plan
from ..backfill_documents import planning
from ..financial_truth_store import loader
from .errors import F6Refused


@contextlib.contextmanager
def read_only(conn):
    """One read-only REPEATABLE READ transaction with P1's fixed session settings (F6.4's loader.session), rolled
    back on exit. Run it as the worker, or as cse_reader."""
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read")
        cur.execute("set transaction read only")
        loader.session(cur)
        try:
            yield cur
        finally:
            conn.rollback()


def window_of(arming, window=None):
    """(first, last) Colombo upload dates of W, inclusive: `window` when given, else the arming in force's."""
    if window is None and arming:
        window = (arming.get("window_first_date"), arming.get("window_last_date"))
    if window is None or not all(isinstance(d, date) for d in window) or window[1] < window[0]:
        raise F6Refused([("window", "no window W: the arming in force records none (a disarm) and none was given")])
    return tuple(window)


def bounds(window):
    """[start, end) of W as aware datetimes (HB-W1), by HB-3's own rule (the one HB-4's in_window uses)."""
    return hb3_plan.window_bounds(*window)


def in_window(uploaded_at, window):
    return planning.in_window(uploaded_at, window)


def rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()[0]
