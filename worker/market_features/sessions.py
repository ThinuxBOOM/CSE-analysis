"""
Session finality and the market cutoff D* (design section 4.3).

P2 writes, into each terminal event of a capture run, its session evidence, universe result and completeness, and
names the derivation pass. The store loads those events as TerminalEvent rows; this module decides, for a date and a
query, whether the date is a FINAL session (and which event governs it), a NON-FINAL session (it happened, but MF
takes nothing from it), or neither. D* is the latest final session admitted by the query.

A capture that never becomes final leaves a non-final session. Nothing is taken from it: there is no "degraded"
session (revision 4 of the design withdrew that).
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from .modes import Query

MARKET_CAPTURE, POST_CLOSE = "market_capture", "post_close"
TERMINAL_STATES_WITH_EVIDENCE = ("succeeded", "partial")
FINAL, NON_FINAL, NO_EVIDENCE = "final", "non_final", "no_evidence"


@dataclass(frozen=True)
class TerminalEvent:
    """One terminal event of a P2 run, as MF needs it. recorded_at is the event's occurred_at; available_at is the
    latest observed_at of the run's ok responses (design section 4.1)."""
    run_id: str
    trading_date: date
    run_kind: str                      # market_capture | metadata_sweep
    capture_mode: str                  # post_close | post_open | metadata_sweep
    state: str                         # succeeded | partial | failed | ...
    recorded_at: datetime
    available_at: datetime
    pass_no: Optional[int]             # the derivation pass the event names; None if it names none
    session_matches_trading_date: Optional[bool]      # P2's E1
    all_closing_prices_published: Optional[bool]      # P2's E2
    universe_known: bool                               # P2's dimension B
    expected: int                                      # P2's dimension C
    produced: int

    def coverage(self):
        return Decimal(self.produced) / Decimal(self.expected) if self.expected > 0 else Decimal(0)


def _admitted(events, query: Query):
    return [e for e in events if query.admits(e.recorded_at, e.available_at)]


def is_final_evidence(event: TerminalEvent, min_coverage: Decimal):
    """Does this terminal event make its date a final session (design section 4.3, first row)?"""
    return (event.run_kind == MARKET_CAPTURE and event.capture_mode == POST_CLOSE
            and event.state in TERMINAL_STATES_WITH_EVIDENCE
            and event.pass_no is not None
            and event.session_matches_trading_date is True
            and event.all_closing_prices_published is True
            and event.universe_known is True
            and event.coverage() >= min_coverage)


def governing_event(events, trading_date: date, query: Query, min_coverage: Decimal):
    """The governing terminal event of a date under a query: the latest admitted event, by recorded_at then run id,
    that meets the finality conditions. None if the date is not a final session."""
    final = [e for e in _admitted(events, query)
             if e.trading_date == trading_date and is_final_evidence(e, min_coverage)]
    return max(final, key=lambda e: (e.recorded_at, e.run_id)) if final else None


def classify(events, trading_date: date, query: Query, min_coverage: Decimal, *, has_admitted_observation=False):
    """final | non_final | no_evidence for a captured date. A session is PROVEN by E1 on any admitted event for the
    date, or by any admitted derived observation for it (migration 0014's own guard treats that as proof)."""
    if governing_event(events, trading_date, query, min_coverage) is not None:
        return FINAL
    proven = has_admitted_observation or any(
        e.trading_date == trading_date and e.session_matches_trading_date is True for e in _admitted(events, query))
    return NON_FINAL if proven else NO_EVIDENCE


def final_sessions(events, query: Query, min_coverage: Decimal):
    """Every final captured session admitted by the query, in date order, each with its governing event."""
    dates = sorted({e.trading_date for e in _admitted(events, query)})
    out = []
    for d in dates:
        g = governing_event(events, d, query, min_coverage)
        if g is not None:
            out.append((d, g))
    return out


def market_cutoff(events, query: Query, min_coverage: Decimal):
    """D*: the latest final session admitted by the query, or None. It is an output, never an input the caller
    asserts, and it is never a non-final session."""
    sessions = final_sessions(events, query, min_coverage)
    return sessions[-1][0] if sessions else None
