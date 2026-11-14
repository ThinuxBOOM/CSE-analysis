"""
Imported history as feature bars (Phase 3 design revision 6, §5.4; MF-7c). Pure.

For a `row_absent` batch (the TradeIQ declaration):
  a row with a close                   traded, the close is px_close (row 1)
  a row without a close                traded, the price is unknown (row 2; TradeIQ's `no_price_printed`)
  a row flagged `zero_price`           a printed zero is not a price; whether it traded is unknown (row 5)
  no row, the pair quarantined         missing: unknown whether it traded, the price is unknown
  no row, inside the security's `security_complete` range on a session of the batch's calendar
                                       a no-trade bar (row 3)
  no row otherwise                     not a member that day (None)
An imported date is a final session when an admitted raw-basis batch has a traded bar for it (§14).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from worker.market_features.bars import FeatureBar

COLOMBO = timezone(timedelta(hours=5, minutes=30))


def available_at(d: date) -> datetime:
    """A date-only source instant counts from the end of that Colombo day (§4.1)."""
    return datetime.combine(d, time(23, 59, 59), COLOMBO)


def _dec(v) -> Optional[Decimal]:
    return None if v in (None, "") else Decimal(str(v))


def _bar(symbol, row, traded, px, unknown, quality, flags=(), shares=None, trades=None, turnover=None, high=None,
         low=None, open_price=None):
    return FeatureBar(symbol=symbol, row=row, traded=traded, px_close=px, price_unknown=unknown, share_volume=shares,
                      trade_count=trades, turnover=turnover, open_price=open_price, high=high, low=low,
                      quality=quality, flags=tuple(flags))


def from_row(symbol, r) -> FeatureBar:
    flags = tuple(f for f in str(r.get("flags") or "").split("|") if f)
    if "zero_price" in flags:
        return _bar(symbol, 5, None, None, True, "missing", flags)
    close = _dec(r.get("close"))
    common = dict(shares=_dec(r.get("shares")), trades=_dec(r.get("trades")), turnover=_dec(r.get("turnover")),
                  high=_dec(r.get("high")), low=_dec(r.get("low")), open_price=_dec(r.get("open_price")))
    if close is None or close <= 0:
        return _bar(symbol, 2, True, None, True, "partial", flags + ("close_unpublished",), **common)
    return _bar(symbol, 1, True, close, False, "fresh", flags, **common)


@dataclass(frozen=True)
class Imported:
    """One security's imported evidence: rows by date, its complete ranges, its quarantined dates."""
    rows: dict                  # date -> row dict (one row per date; a date with several disagreeing rows is quarantined)
    complete: tuple = ()        # ((date_from, date_to), ...)
    quarantined: frozenset = frozenset()


def bar_on(symbol, imp: Imported, session: date) -> Optional[FeatureBar]:
    if session in imp.rows:
        return from_row(symbol, imp.rows[session])
    if session in imp.quarantined:
        return _bar(symbol, 5, None, None, True, "missing", ("quarantined",))
    if any(lo <= session <= hi for lo, hi in imp.complete):
        return _bar(symbol, 3, False, None, False, "stale", ("no_row_security_complete",), shares=Decimal(0),
                    trades=Decimal(0), turnover=Decimal(0))
    return None


def final_sessions(imported_by_symbol) -> list:
    """Dates with at least one traded bar in an admitted raw-basis batch."""
    out = set()
    for imp in imported_by_symbol.values():
        for d, r in imp.rows.items():
            if "zero_price" not in str(r.get("flags") or ""):
                out.add(d)
    return sorted(out)
