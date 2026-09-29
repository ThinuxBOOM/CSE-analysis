"""
Scheduling rules, pure (no database, no clock of their own): the owner-approved settings and their bounds, Colombo
trading-date arithmetic, candidate trading days, capture windows and the retry schedule.

Trading dates are COLOMBO dates. Asia/Colombo is a fixed UTC+05:30 (no daylight saving since 2006), exactly as P2 uses
it (worker.market_capture.config.COLOMBO). The machine's UTC calendar date is never a trading date: at 20:00 UTC on a
Monday it is already Tuesday in Colombo.

Candidate trading days are Monday-Friday. That is the whole rule: CSE's holidays (Poya days and other closures) follow
no computable rule, so none is invented here. A closure is known only from an explicit trading_calendar 'closed' row
(an operator declaration citing CSE's own notice), or proven by P2's session evidence (a snapshot taken on that date
showing an earlier session). trading_calendar stays the empirical three-state table 0001 defined.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from ..market_capture.config import COLOMBO

WORK_KIND = "daily_post_close"
CAPTURE_MODE = "post_close"
OPERATOR_HARD_MAX_ATTEMPTS = 8           # operator retries included; never more capture actions per item
MIN_START_REQUESTS = 10                  # a new run needs at least this much of the daily budget left
P2_RUN_REQUEST_CAP = 150                 # P2's daily_post_close max_requests

# The G-1 stop conditions the owner acknowledges when arming (recorded verbatim on the settings row).
STOP_CONDITIONS = (
    "CSE blocks access (HTTP 401/403/407/451) or keeps rate-limiting: P2 stops the run and ALL capture stays gated "
    "until the owner records acknowledge-block (G-1 controls 5-6)",
    "CSE objects, requests cessation or deletion, or its published terms change materially: the owner disarms the "
    "scheduler and reviews G-1 (G-1 section 5, control 10)",
    "5 consecutive failed requests (P2 circuit breaker), P2's per-run request cap, or the daily request budget: no "
    "further requests that day",
    "the configured User-Agent or host differs from the approved one, the clock is not synchronised, or the security "
    "preflight fails: no capture",
    "the project becomes commercial or public, or raw data would be redistributed: stop and review G-1 first "
    "(control 12)",
)

DEFAULTS = {
    "earliest_start_local": time(15, 15),    # P0.5 roadmap proposal B-7: 45 min after CSE's 14:30 close
    "window_close_local": time(23, 59, 59),  # P0.5: the window closes at the end of the trading date (M7 deferred)
    "retry_base_minutes": 20,
    "retry_max_minutes": 120,
    "max_attempts": 4,
    "no_session_confirmations": 2,
    "daily_request_budget": 150,             # = P2's per-run cap: the normal envelope is about 55-65 a day
    "max_catch_up_days": 14,
    "stale_lease_minutes": 30,
}

BOUNDS = {                                   # identical to migration 0014's CHECK constraints
    "retry_base_minutes": (5, 240),
    "max_attempts": (1, 8),
    "no_session_confirmations": (1, 3),
    "daily_request_budget": (10, 500),
    "max_catch_up_days": (1, 60),
    "stale_lease_minutes": (5, 240),
}
EARLIEST_START_FLOOR = time(14, 30)          # CSE's published close; a post_close capture is never due before it


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduleSettings:
    """One owner decision (a market_schedule_settings row). The latest row is in force; none means disarmed."""
    armed: bool = False
    id: Optional[int] = None
    work_kind: str = WORK_KIND
    start_date: Optional[date] = None
    earliest_start_local: time = DEFAULTS["earliest_start_local"]
    window_close_local: time = DEFAULTS["window_close_local"]
    retry_base_minutes: int = DEFAULTS["retry_base_minutes"]
    retry_max_minutes: int = DEFAULTS["retry_max_minutes"]
    max_attempts: int = DEFAULTS["max_attempts"]
    no_session_confirmations: int = DEFAULTS["no_session_confirmations"]
    daily_request_budget: int = DEFAULTS["daily_request_budget"]
    max_catch_up_days: int = DEFAULTS["max_catch_up_days"]
    stale_lease_minutes: int = DEFAULTS["stale_lease_minutes"]
    max_capture_actions_per_wakeup: int = 1
    user_agent: Optional[str] = None
    host: Optional[str] = None
    expected_requests: Optional[str] = None
    stop_conditions: tuple = field(default_factory=tuple)
    note: str = "no owner decision recorded: disarmed"
    approved_by: Optional[str] = None
    created_at: Optional[datetime] = None

    def validate(self):
        for name, (lo, hi) in BOUNDS.items():
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise ScheduleError(f"{name}={v!r} is outside [{lo}, {hi}]")
        if not self.retry_base_minutes <= self.retry_max_minutes <= 480:
            raise ScheduleError("retry_max_minutes must be between retry_base_minutes and 480")
        if self.max_capture_actions_per_wakeup != 1:
            raise ScheduleError("exactly one capture action per wake-up (G-1: catch-up never bursts)")
        if self.work_kind != WORK_KIND:
            raise ScheduleError(f"work kind must be {WORK_KIND}")
        if self.earliest_start_local < EARLIEST_START_FLOOR:
            raise ScheduleError(f"earliest start {self.earliest_start_local} is before CSE's 14:30 close")
        if not self.window_close_local > self.earliest_start_local:
            raise ScheduleError("the capture window must close after it opens, on the same date")
        if len((self.note or "").strip()) < 10:
            raise ScheduleError("a note of at least 10 characters is required (the owner decision / reason)")
        if self.armed:
            missing = [n for n in ("start_date", "user_agent", "host", "expected_requests") if not getattr(self, n)]
            if missing or not self.stop_conditions:
                raise ScheduleError(f"arming needs {missing + ([] if self.stop_conditions else ['stop_conditions'])}")
        return self

    def schedule_snapshot(self):
        """The values an item is created under (kept on the item, so later changes never rewrite its times)."""
        return {"settings_id": self.id, "earliest_start_local": self.earliest_start_local.isoformat(),
                "window_close_local": self.window_close_local.isoformat(),
                "retry_base_minutes": self.retry_base_minutes, "retry_max_minutes": self.retry_max_minutes,
                "max_attempts": self.max_attempts, "no_session_confirmations": self.no_session_confirmations}

    def public(self):
        d = asdict(self)
        for k in ("earliest_start_local", "window_close_local"):
            d[k] = d[k].isoformat()
        d["start_date"] = self.start_date.isoformat() if self.start_date else None
        d["stop_conditions"] = list(self.stop_conditions)
        return d


DISARMED = ScheduleSettings()


# ------------------------------------------------------------------------------------------------ Colombo time

def colombo_date(moment):
    """The Colombo calendar date of an aware datetime (never the UTC date)."""
    if moment.tzinfo is None:
        raise ScheduleError("naive datetimes are ambiguous; the scheduler only uses aware UTC times")
    return moment.astimezone(COLOMBO).date()


def colombo_moment(day, local):
    """day + local Colombo time, as an aware UTC datetime."""
    return datetime.combine(day, local, tzinfo=COLOMBO).astimezone(timezone.utc)


def colombo_day_bounds(day):
    """[start, end) of a Colombo calendar day in UTC (for counting the day's CSE requests)."""
    return colombo_moment(day, time(0, 0)), colombo_moment(day + timedelta(days=1), time(0, 0))


def due_at(day, schedule):
    return colombo_moment(day, _t(schedule["earliest_start_local"]))


def window_closes_at(day, schedule):
    return colombo_moment(day, _t(schedule["window_close_local"]))


def _t(value):
    return value if isinstance(value, time) else time.fromisoformat(value)


def is_candidate_trading_day(day):
    """Monday-Friday. Holidays are NOT computed (see the module docstring)."""
    return day.weekday() < 5


def candidate_dates(first, last):
    """Weekdays in [first, last], ascending."""
    out, d = [], first
    while d <= last:
        if is_candidate_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def parse_date(text):
    """Strict YYYY-MM-DD (the same rule as P2's --trading-date)."""
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ScheduleError(f"date must be YYYY-MM-DD, got {text!r}") from None
    if d.isoformat() != text:
        raise ScheduleError(f"date must be YYYY-MM-DD, got {text!r}")
    return d


def parse_local_time(text):
    try:
        return time.fromisoformat(text)
    except (TypeError, ValueError):
        raise ScheduleError(f"time must be HH:MM or HH:MM:SS, got {text!r}") from None


# ------------------------------------------------------------------------------------------------ retries

def retry_delay_minutes(schedule, capture_actions):
    """Minutes to wait after the n-th capture action (1-based) before the next: base * 2^(n-1), capped."""
    n = max(1, capture_actions)
    return min(schedule["retry_base_minutes"] * (2 ** (n - 1)), schedule["retry_max_minutes"])


def next_attempt_at(schedule, capture_actions, last_outcome_at):
    return last_outcome_at + timedelta(minutes=retry_delay_minutes(schedule, capture_actions))
