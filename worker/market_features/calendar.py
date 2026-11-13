"""
The session calendar as of T (design section 6; step MF-2).

CSE's closures follow no computable rule, so sessions and closures come from evidence. The one use of the weekday is
P3's own: Monday to Friday are candidate days. A Saturday or Sunday needs no closure evidence; if session evidence
exists for one, it is a session like any other.

A candidate date at or before D* is one of:

    final      a final captured session (sessions.py), or a date with a traded bar in an admitted import
    non_final  a session that is proven to have happened, with no final capture: a known gap, nothing is taken from it
    closure    a declared closure, an import that is calendar-complete over the date and has no row for it, or P3's
               no-session evidence (a not_applicable event that names a run)
    unknown    none of the above (a missed capture leaves one): never counted as a session, never assumed closed

Session evidence beats a closure. The SESSION SEQUENCE is the ordered list of final sessions up to D*; all windows and
horizons count positions in it. A GAP is an unknown date or a non-final session between two consecutive final
sessions: the return across it covers more than one trading day (flag spans_gap) and makes a window partial, or
missing beyond the tolerance.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from .modes import Query

COLOMBO = timezone(timedelta(hours=5, minutes=30))          # fixed offset; no daylight saving (P3 section 5)
DECLARED_CLOSED, IMPORT_CALENDAR_COMPLETE = "declared_closed", "import_calendar_complete"
FINAL, NON_FINAL, CLOSURE, UNKNOWN, NOT_CANDIDATE = "final", "non_final", "closure", "unknown", "not_candidate"


def end_of_colombo_day(day: date) -> datetime:
    """A date-only source instant counts from the end of that Colombo day (F8's rule; design section 4.1)."""
    return datetime.combine(day, time(23, 59, 59), COLOMBO)


@dataclass(frozen=True)
class CalendarObservation:
    """A row of mf_calendar_observations. declared_closed: one date, with the notice date as available_at.
    import_calendar_complete: a date range over which an import batch holds every session; batch_session_dates are
    the dates the batch has rows for."""
    kind: str
    date_from: date
    date_to: date
    recorded_at: datetime
    available_at: Optional[datetime]            # None for import_calendar_complete: each date is available on itself
    source_reference: str
    batch_session_dates: frozenset = frozenset()

    def __post_init__(self):
        if self.kind not in (DECLARED_CLOSED, IMPORT_CALENDAR_COMPLETE) or self.date_to < self.date_from:
            raise ValueError("a calendar observation needs a known kind and an ordered date range")
        if self.kind == DECLARED_CLOSED and (self.date_from != self.date_to or self.available_at is None):
            raise ValueError("declared_closed is for one date and carries the notice instant")
        if not self.source_reference:
            raise ValueError("a calendar observation keeps its source reference")


@dataclass(frozen=True)
class NoSessionEvidence:
    """P3's not_applicable item event that NAMES A RUN: snapshots taken on the date showed an earlier session. The
    run-less not_applicable event (an operator's declared closure) is not this evidence and is never loaded as it."""
    trading_date: date
    run_id: str
    recorded_at: datetime

    def __post_init__(self):
        if not self.run_id:
            raise ValueError("no-session evidence names the run that produced it")


def is_candidate(day: date) -> bool:
    return day.weekday() < 5


def closure_evidence(day: date, query: Query, calendar_observations, no_session_evidence):
    """The admitted closure evidence for a date, as a list of short descriptions (empty: none)."""
    found = []
    for o in calendar_observations:
        if not (o.date_from <= day <= o.date_to):
            continue
        if o.kind == DECLARED_CLOSED and query.admits(o.recorded_at, o.available_at):
            found.append(f"declared_closed:{o.source_reference}")
        elif o.kind == IMPORT_CALENDAR_COMPLETE and is_candidate(day) and day not in o.batch_session_dates \
                and query.admits(o.recorded_at, end_of_colombo_day(day)):
            found.append(f"import_calendar_complete:{o.source_reference}")
    for e in no_session_evidence:
        if e.trading_date == day and query.admits(e.recorded_at, e.recorded_at):
            found.append(f"no_session:{e.run_id}")
    return found


@dataclass(frozen=True)
class DayStatus:
    day: date
    kind: str
    closure_evidence: tuple = ()
    contradiction: bool = False             # session evidence AND closure evidence: it is a session, and flagged


def classify_day(day: date, *, final_dates, non_final_dates, query: Query, calendar_observations=(),
                 no_session_evidence=()) -> DayStatus:
    """final_dates / non_final_dates: the sets the caller derived from run evidence and admitted imports under the
    same query (sessions.py; an imported date with a traded bar is final)."""
    closures = tuple(closure_evidence(day, query, calendar_observations, no_session_evidence))
    if day in final_dates:
        return DayStatus(day, FINAL, closures, bool(closures))
    if day in non_final_dates:
        return DayStatus(day, NON_FINAL, closures, bool(closures))
    if closures:
        return DayStatus(day, CLOSURE, closures)
    return DayStatus(day, UNKNOWN if is_candidate(day) else NOT_CANDIDATE)


@dataclass(frozen=True)
class SessionSequence:
    """The final sessions up to D*, in order, and the gap dates between consecutive ones."""
    sessions: tuple                          # dates, ascending; the last is D*
    gaps_after: tuple                        # gaps_after[i]: the gap dates strictly between sessions[i] and [i+1]
    contradictions: tuple = ()

    @property
    def cutoff(self):
        return self.sessions[-1] if self.sessions else None

    def index(self, day: date) -> int:
        return self.sessions.index(day)

    def gaps_between(self, start_index: int, end_index: int) -> tuple:
        """Every gap date strictly inside the stretch from sessions[start_index] to sessions[end_index]."""
        if not 0 <= start_index <= end_index < len(self.sessions):
            raise IndexError("the stretch must lie inside the sequence")
        return tuple(d for i in range(start_index, end_index) for d in self.gaps_after[i])

    def spans_gap(self, index: int) -> bool:
        """Does the return from sessions[index - 1] to sessions[index] cover more than one trading day?"""
        return index >= 1 and bool(self.gaps_after[index - 1])


def session_sequence(*, final_dates, non_final_dates, query: Query, calendar_observations=(),
                     no_session_evidence=()) -> SessionSequence:
    finals = sorted(final_dates)
    gaps, contradictions = [], []
    kw = dict(final_dates=set(final_dates), non_final_dates=set(non_final_dates), query=query,
              calendar_observations=calendar_observations, no_session_evidence=no_session_evidence)
    for d in finals:
        if classify_day(d, **kw).contradiction:
            contradictions.append(d)
    for a, b in zip(finals, finals[1:]):
        between = []
        day = a + timedelta(days=1)
        while day < b:
            status = classify_day(day, **kw)
            if status.kind in (NON_FINAL, UNKNOWN):
                between.append(day)
            if status.contradiction:
                contradictions.append(day)
            day += timedelta(days=1)
        gaps.append(tuple(between))
    return SessionSequence(tuple(finals), tuple(gaps), tuple(sorted(set(contradictions))))


def gap_tolerance(window_sessions: int) -> int:
    """P3-D5: 0 for windows of 5 sessions or fewer; 1 for 6 to 63; 2% of the window rounded down beyond that."""
    if window_sessions < 1:
        raise ValueError("a window has at least one session")
    if window_sessions <= 5:
        return 0
    if window_sessions <= 63:
        return 1
    return window_sessions * 2 // 100


def window_gap_state(sequence: SessionSequence, end_index: int, window_sessions: int):
    """(state, gap dates) for the stretch of `window_sessions` returns ending at sessions[end_index], that is, from
    sessions[end_index - window_sessions] to sessions[end_index]. state: ok | partial | missing | too_short."""
    start = end_index - window_sessions
    if start < 0:
        return "too_short", ()
    gaps = sequence.gaps_between(start, end_index)
    if not gaps:
        return "ok", ()
    return ("partial" if len(gaps) <= gap_tolerance(window_sessions) else "missing"), gaps
