"""
The reference price and its source session (design section 5.3).

px_close(s, D)    a close CSE printed for a session on which s traded.
px_ref(s, D)      the latest px_close(s, u) with u <= D, WITH ref_session(s, D) = that u. A carried price always
                  travels with the session it was printed on: adjustment (MF-3) is by that session, never by the date
                  of the bar that carries it.
price unknown     when the security traded, or may have traded, and MF has no close, px_ref is undefined on that
                  session and stays undefined on the following sessions until the security next prints a close.
                  Carrying the earlier close would assert a zero return over a day it traded.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Reference:
    session: date
    px_ref: Optional[Decimal]
    ref_session: Optional[date]
    stale_sessions: Optional[int]        # final sessions in (ref_session, session]; None while undefined
    state: str                           # known | price_unknown | no_price_yet
    source_quality: Optional[str] = None # the quality of the bar that printed px_ref (conflicting: MFB-6, MFB-8)
    bar_quality: Optional[str] = None    # the quality of THIS session's bar, decided with the history; None: no bar
    bar_quality_reason: Optional[str] = None


def reference_series(bars_by_session):
    """bars_by_session: [(session, FeatureBar or None)] for ONE security over consecutive FINAL sessions, in order.
    None means the security was not a member on that session (no bar): a price already carried is carried on.
    Returns one Reference per session."""
    out, px, src, since, unknown, src_quality = [], None, None, 0, False, None
    for session, bar in bars_by_session:
        no_trade = bar is not None and bar.px_close is None and not bar.price_unknown
        if bar is not None and bar.px_close is not None:
            px, src, since, unknown, src_quality = bar.px_close, session, 0, False, bar.quality
        elif bar is not None and bar.price_unknown:
            unknown = True
        elif px is not None:
            since += 1                   # a no-trade bar, or no bar at all: neither ends an unknown-price stretch
        state = "price_unknown" if unknown else ("no_price_yet" if px is None else "known")
        quality, reason = (None, None) if bar is None else (bar.quality, None)
        if no_trade:                     # section 5.2: stale once a price is carried, missing before or while unknown
            quality, reason = ("stale", None) if state == "known" else ("missing", state)
        if state == "known":
            out.append(Reference(session, px, src, since, state, src_quality, quality, reason))
        else:
            out.append(Reference(session, None, None, None, state, None, quality, reason))
    return out
