"""
Owner decisions (design sections 7.7, 16.3 and 16.6): arming (record L1), hold resolutions (L7) and block
acknowledgements (L9).

Each is accepted ONLY from P1's owner-delegation login (cse_migrator, which only root can reach), acting as cse_owner
through SET LOCAL ROLE for exactly one INSERT in one transaction: the same owner path as P2's acknowledge-block, P3's
arm and F6.4's designate. The worker cannot record any of them, three ways: it has no INSERT privilege on these
tables, it is not a member of cse_owner, and each table's guard trigger refuses every service-role session.

Recording a decision is not making one: the values come from the owner. Nothing here chooses a window, a budget or a
resolution, and an arming decision authorises nothing by itself (G-1 is an accepted risk, not CSE authorization).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..market_capture import runs as p2runs
from . import OWNER_PATH_ROLE, OWNER_ROLE
from .store import ARMING_COLUMNS, ARMING_IN_FORCE_SQL, _json, _rollback

STAGES = ("HB-S0", "HB-S1", "HB-S2", "HB-S3", "HB-S4", "HB-S5", "HB-S6")      # design section 14.1
HOLD_RESOLUTIONS = ("acquire_evidence", "record_as_is", "keep_held")          # design section 7.7 step 4
NOTE_MIN = 10
ATTEMPT_BOUNDS = (1, 5)                     # P2's bounds for attempts per request (design section 16.4)
BUDGET_FIELDS = ("daily_request_budget", "slice_max_json_requests", "slice_max_documents", "slice_max_seconds",
                 "attempts_per_json_request", "attempts_per_document", "item_max_attempts")


class OwnerPathRequired(Exception):
    """An owner decision was attempted from a login other than the owner path."""


class InvalidDecision(ValueError):
    pass


@dataclass(frozen=True)
class ArmingDecision:
    """One owner arming decision (design section 16.6). validate() mirrors migration 0016's CHECK constraints on
    backfill_arming_decisions, so a malformed decision is refused before the owner path is opened."""
    armed: bool
    note: str
    armed_stages: tuple = ()
    window_first_date: Optional[date] = None
    window_last_date: Optional[date] = None
    daily_request_budget: Optional[int] = None
    combined_daily_ceiling: Optional[int] = None
    slice_max_json_requests: Optional[int] = None
    slice_max_documents: Optional[int] = None
    slice_max_seconds: Optional[int] = None
    attempts_per_json_request: Optional[int] = None
    attempts_per_document: Optional[int] = None
    item_max_attempts: Optional[int] = None
    user_agent: Optional[str] = None
    host: Optional[str] = None
    version_tuple: dict = field(default_factory=dict)
    expected_requests: dict = field(default_factory=dict)
    stop_conditions: tuple = ()
    g1_reference: Optional[str] = None

    @classmethod
    def disarm(cls, note):
        return cls(armed=False, note=note)

    def validate(self):
        if not set(self.armed_stages) <= set(STAGES):
            raise InvalidDecision(f"unknown stages {sorted(set(self.armed_stages) - set(STAGES))}")
        if self.armed != bool(self.armed_stages):
            raise InvalidDecision("an armed decision names at least one stage; a disarm names none")
        if len((self.note or "").strip()) < NOTE_MIN:
            raise InvalidDecision(f"the release note needs at least {NOTE_MIN} characters")
        if self.window_first_date and self.window_last_date and self.window_last_date < self.window_first_date:
            raise InvalidDecision("the window ends before it starts")
        for name in BUDGET_FIELDS + ("combined_daily_ceiling",):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
                raise InvalidDecision(f"{name} must be a positive integer, not {v!r}")
        for name in ("attempts_per_json_request", "attempts_per_document"):
            v = getattr(self, name)
            if v is not None and not ATTEMPT_BOUNDS[0] <= v <= ATTEMPT_BOUNDS[1]:
                raise InvalidDecision(f"{name}={v} is outside P2's bounds {ATTEMPT_BOUNDS}")
        if not isinstance(self.version_tuple, dict) or not isinstance(self.expected_requests, dict):
            raise InvalidDecision("version_tuple and expected_requests are objects")
        if self.armed:
            missing = [n for n in ("window_first_date", "window_last_date") + BUDGET_FIELDS if getattr(self, n) is None]
            missing += [n for n in ("user_agent", "host", "g1_reference") if not (getattr(self, n) or "").strip()]
            missing += [n for n in ("version_tuple", "expected_requests", "stop_conditions") if not getattr(self, n)]
            if missing:
                raise InvalidDecision(f"an armed decision records every release-gate fact; missing {missing}")
        return self


def _as_owner(conn, what, write):
    """One INSERT as cse_owner, only from the owner-delegation login, in one transaction."""
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"{what} is an owner decision (G-1): connected as {who!r}; it is recorded only "
                                f"through the owner path ({OWNER_PATH_ROLE} -> SET LOCAL ROLE {OWNER_ROLE})")
    try:
        with conn.cursor() as cur:
            cur.execute(f"set local role {OWNER_ROLE}")
            out = write(cur)
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return out


def _recorder(operator):
    return f"{p2runs.os_user()} (operator: {operator})" if operator else p2runs.os_user()


def record_arming_as_owner(conn, decision, os_user):
    """Insert one arming decision (arm, re-arm with new values, or disarm); the latest row is in force."""
    d = decision.validate()

    def write(cur):
        cur.execute("insert into backfill_arming_decisions (armed, armed_stages, window_first_date, window_last_date, "
                    "daily_request_budget, combined_daily_ceiling, slice_max_json_requests, slice_max_documents, "
                    "slice_max_seconds, attempts_per_json_request, attempts_per_document, item_max_attempts, "
                    "user_agent, host, version_tuple, expected_requests, stop_conditions, g1_reference, note, "
                    "os_user) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "returning id",
                    (d.armed, list(d.armed_stages), d.window_first_date, d.window_last_date, d.daily_request_budget,
                     d.combined_daily_ceiling, d.slice_max_json_requests, d.slice_max_documents, d.slice_max_seconds,
                     d.attempts_per_json_request, d.attempts_per_document, d.item_max_attempts, d.user_agent, d.host,
                     _json(d.version_tuple), _json(d.expected_requests), _json(list(d.stop_conditions)),
                     d.g1_reference, d.note, os_user))
        return cur.fetchone()[0]
    return _as_owner(conn, "arming the backfill", write)


def arming_in_force_as_owner(conn):
    """The owner-delegation login (NOINHERIT) reads nothing on its own: read as cse_owner inside a read-only
    transaction that is rolled back (P1's migration-status pattern)."""
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(f"set local role {OWNER_ROLE}")
            cur.execute(ARMING_IN_FORCE_SQL)
            row = cur.fetchone()
    finally:
        _rollback(conn)
    return None if row is None else dict(zip(ARMING_COLUMNS, row))


def acknowledge_block_as_owner(conn, block_id, note, operator=None):
    """The owner's review of a CSE block; blocked items may then resume."""
    if len((note or "").strip()) < NOTE_MIN:
        raise InvalidDecision(f"the acknowledgement note needs at least {NOTE_MIN} characters")

    def write(cur):
        cur.execute("insert into backfill_block_acknowledgements (block_id, note, os_user) values (%s, %s, %s) "
                    "returning id", (block_id, note, _recorder(operator)))
        return cur.fetchone()[0]
    return _as_owner(conn, "acknowledging a CSE block", write)


def resolve_hold_as_owner(conn, hold_id, resolution, note, operator=None):
    """The owner's resolution of a held observation (design section 7.7 step 4); the latest per hold is in force."""
    if resolution not in HOLD_RESOLUTIONS:
        raise InvalidDecision(f"resolution must be one of {HOLD_RESOLUTIONS}")
    if len((note or "").strip()) < NOTE_MIN:
        raise InvalidDecision(f"the resolution note needs at least {NOTE_MIN} characters")

    def write(cur):
        cur.execute("insert into backfill_hold_resolutions (hold_id, resolution, note, os_user) values (%s, %s, %s, %s) "
                    "returning id", (hold_id, resolution, note, _recorder(operator)))
        return cur.fetchone()[0]
    return _as_owner(conn, "resolving a hold", write)
