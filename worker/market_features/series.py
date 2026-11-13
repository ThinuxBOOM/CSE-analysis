"""
One security's carried, adjusted series on the basis of the snapshot's D* (design section 10.2).

c_u   adj_ref(s, u): the reference price of session u, adjusted from the session it was PRINTED on to D*. It exists
      on every final session from the security's first traded bar, except while the price is unknown (5.3) or its
      adjustment crosses an unresolved factor (untrusted).
r_u   ln(c_u / c_{u-1}): the session return on the carried series. 0 on a no-trade session; on the first traded
      session after no-trade sessions it holds the whole move since the last trade. Undefined when either price is
      unknown; the first return after such a stretch runs from the last known price and is flagged spans_unknown.
      A return across a calendar gap is computed and flagged spans_gap; it is never split into invented daily returns.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from . import actions, numeric
from .calendar import SessionSequence

KNOWN, PRICE_UNKNOWN, NO_PRICE_YET, UNTRUSTED = "known", "price_unknown", "no_price_yet", "untrusted"


@dataclass(frozen=True)
class Point:
    c: Optional[Decimal]                 # unquantized, in MF's context
    state: str                           # known | price_unknown | no_price_yet | untrusted
    carried: bool = False                # the price was printed on an earlier session
    stale_sessions: Optional[int] = None
    r: Optional[Decimal] = None          # unquantized
    spans_gap: bool = False
    spans_unknown: bool = False
    base_index: Optional[int] = None     # the index r runs from (index - 1 unless spans_unknown)


@dataclass(frozen=True)
class Series:
    symbol: str
    sequence: SessionSequence
    points: tuple                        # one Point per session of the sequence
    bars: tuple                          # the FeatureBar (or None) of each session
    factors: tuple
    moves: tuple

    def __len__(self):
        return len(self.points)


def build_series(symbol, sequence: SessionSequence, bars, references, factors, moves=()) -> Series:
    """bars / references: {session: FeatureBar or None} and {session: reference.Reference} for this security (for a
    linked series, already joined by universe.series_segments). factors: its factor list at (T, mode)."""
    cutoff = sequence.cutoff
    points, last_known = [], None
    for i, session in enumerate(sequence.sessions):
        ref = references.get(session)
        if ref is None or ref.state == NO_PRICE_YET or (ref.px_ref is None and ref.state != PRICE_UNKNOWN):
            points.append(Point(None, NO_PRICE_YET))
            continue
        if ref.state == PRICE_UNKNOWN:
            points.append(Point(None, PRICE_UNKNOWN))
            continue
        adjusted = actions.adj(ref.px_ref, ref.ref_session, cutoff, factors)
        if adjusted is None:
            points.append(Point(None, UNTRUSTED, ref.ref_session != session, ref.stale_sessions))
            continue
        c = numeric.to_decimal(adjusted)
        r = base = None
        gap = unknown = False
        if last_known is not None:
            base = last_known
            r = numeric.log_return(c, points[base].c)
            unknown = base != i - 1
            gap = bool(sequence.gaps_between(base, i))
        points.append(Point(c, KNOWN, ref.ref_session != session, ref.stale_sessions, r, gap, unknown, base))
        last_known = i
    # A known price directly after an untrusted or not-yet-priced stretch has no return from it: only an
    # unknown-price stretch is bridged. Drop the returns whose stretch holds anything but unknown prices.
    fixed = []
    for i, p in enumerate(points):
        if p.r is not None and p.spans_unknown and any(points[j].state != PRICE_UNKNOWN
                                                       for j in range(p.base_index + 1, i)):
            p = Point(p.c, p.state, p.carried, p.stale_sessions)
        fixed.append(p)
    return Series(symbol, sequence, tuple(fixed), tuple(bars.get(s) for s in sequence.sessions), tuple(factors),
                  tuple(moves))
