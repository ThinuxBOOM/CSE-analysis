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


def reference_series(bars_by_session):
    """bars_by_session: [(session, FeatureBar or None)] for ONE security over consecutive FINAL sessions, in order.
    None means the security was not a member on that session (no bar): a price already carried is carried on.
    Returns one Reference per session."""
    out, px, src, since, unknown = [], None, None, 0, False
    for session, bar in bars_by_session:
        if bar is not None and bar.px_close is not None:
            px, src, since, unknown = bar.px_close, session, 0, False
        elif bar is not None and bar.price_unknown:
            unknown = True
        elif px is not None:
            since += 1
        if unknown:
            out.append(Reference(session, None, None, None, "price_unknown"))
        elif px is None:
            out.append(Reference(session, None, None, None, "no_price_yet"))
        else:
            out.append(Reference(session, px, src, since, "known"))
    return out
