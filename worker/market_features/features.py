"""
Feature set v1 for one security (design sections 10.3 and 10.4; step MF-5). Market-context features are in market.py.

Every value carries a state. `missing`, `conflicting` and `untrusted` values are null, with the reason beside them;
`unlabelled`, `partial`, `stale` and `fresh` values are present. Where several states apply the first in the order
untrusted, conflicting, missing, unlabelled, partial, stale, fresh is recorded and the others are listed. A null is
never replaced by zero or by a cross-sectional fill.

This module is the FEATURE path: it must not import the target module (design section 11). The return primitive it
shares with the labels lives in numeric.py.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from . import actions, numeric
from .bars import CONFLICTING
from .calendar import gap_tolerance
from .numeric import CTX
from .series import KNOWN, NO_PRICE_YET, PRICE_UNKNOWN, UNTRUSTED, Series

REGISTRY_VERSION = "features.1"
FRESH, STALE, PARTIAL, UNLABELLED, MISSING, CONFLICT, UNTRUST = ("fresh", "stale", "partial", "unlabelled", "missing",
                                                                 "conflicting", "untrusted")
PRECEDENCE = (UNTRUST, CONFLICT, MISSING, UNLABELLED, PARTIAL, STALE, FRESH)
NULL_STATES = (UNTRUST, CONFLICT, MISSING)
HISTORY_HORIZONS = (1, 5, 10, 21)


@dataclass(frozen=True)
class FeatureConfig:
    max_stale_sessions: int = 5
    min_constituents: int = 30


@dataclass(frozen=True)
class Value:
    feature_id: str
    value: Optional[Decimal]             # quantized to 12 places; None in a null state
    state: str
    reasons: tuple = ()                  # (state, reason) pairs, every one that applies
    traded_sessions: Optional[int] = None
    inputs: Optional[int] = None


class Notes:
    """The states that apply to one value, each with its reasons."""

    def __init__(self):
        self.items = []

    def add(self, state, reason):
        if (state, reason) not in self.items:
            self.items.append((state, reason))
        return self

    def merge(self, other):
        for item in other.items:
            self.add(*item)
        return self

    def state(self):
        states = {s for s, _ in self.items}
        return next((s for s in PRECEDENCE if s in states), FRESH)

    def is_null(self):
        return self.state() in NULL_STATES

    def reasons(self):
        return tuple(sorted(self.items, key=lambda x: (PRECEDENCE.index(x[0]), x[1])))


def finish(feature_id, value, notes: Notes, traded=None, inputs=None, integer=False) -> Value:
    if value is None and not notes.is_null():
        notes.add(MISSING, "no_value")
    if notes.is_null():
        value = None
    elif not integer:
        value = numeric.q(value)
    return Value(feature_id, value, notes.state(), notes.reasons(), traded, inputs)


class Calculator:
    """The features of one security at t = D* (the last session of its series)."""

    def __init__(self, series: Series, config: FeatureConfig = FeatureConfig()):
        self.s, self.cfg = series, config
        self.t = len(series) - 1
        self.sessions = series.sequence.sessions
        if self.t < 0:
            raise ValueError("a snapshot has at least one final session")

    # ------------------------------------------------------------------------------------------------- helpers
    def _stretch(self, start, end, n, prices=True) -> Notes:
        """The states a window owes to the stretch of sessions from start to end: gaps and unexplained moves between
        them, untrusted adjustment and bridged unknown prices in (start, end], and a `conflicting` bar anywhere in
        [start, end] (audit 2, B1). With `prices` (a formula that uses c or r), also a price carried from a
        `conflicting` bar, and the base of a return bridged from before `start` (audit 3, B1'); without it (volume,
        Parkinson, activity) a carried price is not an input and is not checked (audit 3, MJ-1). n is the window's
        nominal length for the tolerance."""
        notes = Notes()
        if start < 0:
            return notes.add(MISSING, "insufficient_history")
        gaps = self.s.sequence.gaps_between(start, end)
        first = self.s.points[start + 1] if start + 1 <= end else None
        if prices and first is not None and first.spans_unknown and first.base_index is not None \
                and first.base_index < start:
            gaps = self.s.sequence.gaps_between(first.base_index, start) + gaps   # the bridged return's stretch
        if gaps:
            notes.add(*((PARTIAL, "spans_gap") if len(gaps) <= gap_tolerance(n) else (MISSING, "gaps_beyond_tolerance")))
        for i in range(start, end + 1):
            bar, p = self.s.bars[i], self.s.points[i]
            if (bar is not None and bar.quality == CONFLICTING) or (prices and p.source_conflicting):
                notes.add(CONFLICT, "conflicting_bar")
            if i == start:
                continue
            if prices and p.spans_unknown and p.base_index is not None and p.base_index < start:
                base_bar, base = self.s.bars[p.base_index], self.s.points[p.base_index]
                if (base_bar is not None and base_bar.quality == CONFLICTING) or base.source_conflicting:
                    notes.add(CONFLICT, "conflicting_bar")
            if p.state == UNTRUSTED:
                notes.add(UNTRUST, "unresolved_factor")
            if p.spans_unknown or p.state == PRICE_UNKNOWN:
                notes.add(PARTIAL, "spans_unknown")
        if actions.unlabelled(self.s.moves, self.sessions[start], self.sessions[end]):
            notes.add(UNLABELLED, "unexplained_move")
        return notes

    def _end(self, i) -> Notes:
        """The states a value owes to using the price of session i as an end of its window."""
        notes = Notes()
        if i < 0:
            return notes.add(MISSING, "insufficient_history")
        p = self.s.points[i]
        if p.source_conflicting:
            notes.add(CONFLICT, "conflicting_bar")
        if p.state == UNTRUSTED:
            notes.add(UNTRUST, "unresolved_factor")
        elif p.state == PRICE_UNKNOWN:
            notes.add(MISSING, "price_unknown")
        elif p.state == NO_PRICE_YET:
            notes.add(MISSING, "no_price")
        elif p.carried:
            if p.stale_sessions > self.cfg.max_stale_sessions:
                notes.add(MISSING, "staleness_limit")
            else:
                notes.add(STALE, "carried_price")
            if actions.ex_date_since_last_trade(self.sessions[i - p.stale_sessions], self.sessions[i], self.s.factors):
                notes.add(STALE, "ex_date_since_last_trade")
        return notes

    def _traded(self, first, last):
        return sum(1 for i in range(max(first, 0), last + 1) if self.s.bars[i] is not None and self.s.bars[i].traded)

    def _c(self, i):
        return self.s.points[i].c if i >= 0 else None

    def _price_return(self, fid, end, n):
        start = end - n
        notes = self._stretch(start, end, n).merge(self._end(start)).merge(self._end(end))
        value = None if notes.is_null() else numeric.simple_return(self._c(end), self._c(start))
        return finish(fid, value, notes, self._traded(start + 1, end))

    def _volume(self, i):
        """(adjusted share volume or None, note) of session i: 0 on a no-trade session."""
        bar = self.s.bars[i]
        if bar is None or bar.traded is None:
            return None, None
        if bar.traded is False:
            return Decimal(0), None
        if bar.share_volume is None:
            return None, (UNLABELLED, "null_volume")
        v, reason = actions.adj_volume(bar.share_volume, self.sessions[i], self.sessions[self.t], self.s.factors)
        if v is None:
            return None, (PARTIAL, reason) if reason == "share_count_changed" else (UNTRUST, "unresolved_factor")
        return numeric.to_decimal(v), None

    def _field(self, i, name):
        bar = self.s.bars[i]
        if bar is None or bar.traded is None:
            return None, None
        if bar.traded is False:
            return Decimal(0), None
        v = getattr(bar, name)
        return (v, None) if v is not None else (None, (UNLABELLED, "null_" + name))

    def _session_mean(self, fid, n, minimum, getter, last=None):
        last = self.t if last is None else last
        first = last - n + 1
        if first < 0:
            return finish(fid, None, Notes().add(MISSING, "insufficient_history")), None
        notes = self._stretch(first, last, n, prices=False)
        values = []
        for i in range(first, last + 1):
            v, note = getter(i)
            if note:
                notes.add(*note)
            if v is not None:
                values.append(v)
        notes.items = [x for x in notes.items if x != (PARTIAL, "spans_unknown")]     # no price is used here
        if len(values) < minimum:
            notes.add(MISSING, "below_min_observations")
        elif len(values) < n:
            notes.add(PARTIAL, "fewer_inputs_than_window")
        value = None if notes.is_null() else numeric.mean(values)
        return finish(fid, value, notes, self._traded(first, last), len(values)), value

    # ------------------------------------------------------------------------------------------------ features
    def returns(self):
        return [self._price_return(f"ret_{n}", self.t, n) for n in (1, 5, 10, 21, 63, 126, 252)]

    def momentum(self):
        out = []
        for n in (252, 126):
            end, start = self.t - 21, self.t - n
            notes = self._stretch(start, end, n - 21).merge(self._end(start)).merge(self._end(end)) if end >= 0 \
                else Notes().add(MISSING, "insufficient_history")
            value = None if notes.is_null() else numeric.simple_return(self._c(end), self._c(start))
            out.append(finish(f"mom_{n}_21", value, notes, self._traded(start + 1, end)))
        return out

    def volatility(self):
        out = []
        for n, minimum in ((21, 15), (63, 40)):
            start = self.t - n
            notes = self._stretch(start, self.t, n)
            rs = [self.s.points[i].r for i in range(max(start + 1, 0), self.t + 1) if self.s.points[i].r is not None]
            if len(rs) < minimum:
                notes.add(MISSING, "below_min_observations")
            elif len(rs) < n:
                notes.add(PARTIAL, "fewer_inputs_than_window")
            value = None
            if not notes.is_null():
                m = numeric.mean(rs)
                ss = numeric.add(*[CTX.multiply(CTX.subtract(r, m), CTX.subtract(r, m)) for r in rs])
                value = numeric.sqrt(CTX.divide(ss, Decimal(len(rs) - 1)))
            out.append(finish(f"vol_{n}", value, notes, self._traded(start + 1, self.t), len(rs)))
        out.append(self._parkinson())
        return out

    def _parkinson(self, n=21, minimum=10):
        first = self.t - n + 1
        if first < 0:
            return finish("vol_parkinson_21", None, Notes().add(MISSING, "insufficient_history"))
        notes = self._stretch(first, self.t, n, prices=False)
        notes.items = [x for x in notes.items if x[1] not in ("spans_unknown", "unresolved_factor")]   # a ratio
        squares = []
        for i in range(first, self.t + 1):
            bar = self.s.bars[i]
            if bar is not None and bar.traded and bar.high is not None and bar.low is not None and bar.low > 0 \
                    and bar.high >= bar.low:
                x = numeric.ln(CTX.divide(bar.high, bar.low))
                squares.append(CTX.multiply(x, x))
        if len(squares) < minimum:
            notes.add(MISSING, "below_min_observations")
        value = None
        if not notes.is_null():
            value = numeric.sqrt(CTX.divide(numeric.mean(squares), CTX.multiply(Decimal(4), numeric.ln(Decimal(2)))))
        return finish("vol_parkinson_21", value, notes, self._traded(first, self.t), len(squares))

    def drawdown(self, n=252, minimum=126):
        first = self.t - n + 1
        notes = self._stretch(first, self.t, n) if first >= 0 else Notes().add(MISSING, "insufficient_history")
        notes.merge(self._end(self.t))
        known = [(i, self.s.points[i].c) for i in range(max(first, 0), self.t + 1) if self.s.points[i].state == KNOWN]
        if len(known) < minimum:
            notes.add(MISSING, "below_min_observations")
        elif len(known) < n:
            notes.add(PARTIAL, "fewer_inputs_than_window")
        dd = days = None
        if not notes.is_null():
            peak = max(c for _, c in known)
            at = max(i for i, c in known if c == peak)                     # the latest, if tied
            dd, days = numeric.simple_return(self._c(self.t), peak), Decimal(self.t - at)
        traded = self._traded(max(first, 0), self.t)
        return [finish("dd_252", dd, notes, traded, len(known)),
                finish("dd_days_252", days, Notes().merge(notes), traded, len(known), integer=True)]

    def volume(self):
        shares, _ = self._session_mean("vol_shares_avg_21", 21, 15, self._volume)
        turnover, _ = self._session_mean("turnover_avg_21", 21, 15, lambda i: self._field(i, "turnover"))
        trades, _ = self._session_mean("trades_avg_21", 21, 15, lambda i: self._field(i, "trade_count"))
        base, base_value = self._session_mean("rel_volume_21", 21, 15, self._volume, last=self.t - 1)
        notes = Notes()
        notes.items = list(base.reasons)
        today, note = self._volume(self.t)
        if note:
            notes.add(*note)
        if self.t >= 1:                          # the step from the base to t: a conflicting bar at t (m1, B1)
            step = self._stretch(self.t - 1, self.t, 21, prices=False)
            notes.merge(step)
            # gaps are counted once over t-21..t against the 21-session tolerance (audit 3, MJ-2)
            notes.items = [x for x in notes.items if x not in ((PARTIAL, "spans_unknown"), (PARTIAL, "spans_gap"),
                                                                (MISSING, "gaps_beyond_tolerance"))]
            if self.t >= 21:
                gaps = self.s.sequence.gaps_between(self.t - 21, self.t)
                if gaps:
                    notes.add(*((PARTIAL, "spans_gap") if len(gaps) <= gap_tolerance(21)
                                else (MISSING, "gaps_beyond_tolerance")))
        if today is None and not note:
            notes.add(MISSING, "no_bar")
        if base_value is not None and base_value == 0:
            notes.add(MISSING, "zero_base")
        value = None if notes.is_null() or today is None or base_value is None else CTX.divide(today, base_value)
        return [shares, turnover, trades, finish("rel_volume_21", value, notes, base.traded_sessions, base.inputs)]

    def liquidity(self):
        n, minimum, start = 21, 10, self.t - 21
        notes = self._stretch(start, self.t, n)
        terms = []
        for i in range(max(start + 1, 0), self.t + 1):
            bar, p = self.s.bars[i], self.s.points[i]
            if bar is not None and bar.traded and bar.turnover is not None and bar.turnover > 0 and p.r is not None:
                terms.append(CTX.divide(abs(p.r), bar.turnover))
        if len(terms) < minimum:
            notes.add(MISSING, "below_min_observations")
        amihud = finish("amihud_21", None if notes.is_null() else numeric.mean(terms), notes,
                        self._traded(start + 1, self.t), len(terms))
        first = self.t - 62
        zn = Notes().add(MISSING, "insufficient_history") if first < 0 else self._stretch(first, self.t, 63, prices=False)
        zn.items = [x for x in zn.items if x[1] not in ("spans_unknown", "unresolved_factor", "unexplained_move")]
        known = [b for b in self.s.bars[max(first, 0):self.t + 1] if b is not None and b.traded is not None]
        if len(known) < 40:
            zn.add(MISSING, "below_min_observations")
        elif len(known) < 63:
            zn.add(PARTIAL, "fewer_inputs_than_window")
        ratio = None if zn.is_null() else CTX.divide(Decimal(sum(1 for b in known if b.traded is False)),
                                                    Decimal(len(known)))
        p = self.s.points[self.t]
        sn = Notes()
        if p.stale_sessions is None:
            sn.add(MISSING, "price_unknown" if p.state == PRICE_UNKNOWN else "no_price")
        stale = finish("stale_sessions", None if p.stale_sessions is None else Decimal(p.stale_sessions), sn,
                       integer=True)
        return [amihud, finish("zero_trade_ratio_63", ratio, zn, self._traded(max(first, 0), self.t), len(known)), stale]

    def return_history(self, window=252, minimum=126):
        """Over the h-session returns whose END session lies in the window ending at t. A return is left out when it
        spans a gap or when one of its ends is at an unknown price. Every return used ends at or before D*."""
        out = []
        first = max(self.t - window + 1, 0)
        for h in HISTORY_HORIZONS:
            notes, values = Notes(), []
            for u in range(first, self.t + 1):
                a = u - h
                if a < 0:
                    continue
                pa, pu = self.s.points[a], self.s.points[u]
                if any((b is not None and b.quality == CONFLICTING) for b in (self.s.bars[a], self.s.bars[u])) \
                        or pa.source_conflicting or pu.source_conflicting:
                    notes.add(CONFLICT, "conflicting_bar")                 # audit 2, B1
                if UNTRUSTED in (pa.state, pu.state):
                    notes.add(UNTRUST, "unresolved_factor")
                    continue
                if pa.state != KNOWN or pu.state != KNOWN or self.s.sequence.gaps_between(a, u):
                    continue
                values.append(numeric.simple_return(pu.c, pa.c))
            if actions.unlabelled(self.s.moves, self.sessions[max(first - h, 0)], self.sessions[self.t]):
                notes.add(UNLABELLED, "unexplained_move")
            n = len(values)
            count = Value(f"hist_n_{h}", Decimal(n), FRESH if n == window else PARTIAL,
                          () if n == window else ((PARTIAL, "fewer_inputs_than_window"),), None, n)
            if n < minimum:
                notes.add(MISSING, "below_min_observations")
            elif n < window:
                notes.add(PARTIAL, "fewer_inputs_than_window")
            null = notes.is_null()
            share = lambda k: None if null else CTX.divide(Decimal(k), Decimal(n))
            out += [finish(f"hist_up_share_{h}", share(sum(1 for v in values if v > 0)), Notes().merge(notes), None, n),
                    finish(f"hist_flat_share_{h}", share(sum(1 for v in values if v == 0)), Notes().merge(notes), None, n),
                    finish(f"hist_down_share_{h}", share(sum(1 for v in values if v < 0)), Notes().merge(notes), None, n),
                    finish(f"hist_mean_ret_{h}", None if null else numeric.mean(values), Notes().merge(notes), None, n),
                    count]
        return out

    def size_and_range(self):
        bar = self.s.bars[self.t]
        out = []

        def simple(fid, value, reason):
            notes = Notes()
            if value is None:
                notes.add(MISSING, reason)
            elif bar.quality == CONFLICTING:
                notes.add(CONFLICT, "conflicting_bar")
            return finish(fid, value, notes)

        cap = None if bar is None else bar.market_cap_reported
        out.append(simple("market_cap_reported", cap, "no_bar" if bar is None else "not_reported"))
        traded = bar is not None and bar.traded is True and bar.px_close is not None and bar.px_close > 0
        ltp = None
        if traded:                               # §10.3: market cap over CSE's last traded price (audit 2, M1)
            try:
                ltp = numeric.to_decimal(bar.raw.get("last_traded_price")) if bar.raw.get("last_traded_price") \
                    not in (None, "") else None
            except (TypeError, ValueError, ArithmeticError):
                ltp = None
        ltp = ltp if ltp is not None and ltp > 0 else None
        out.append(simple("shares_implied", CTX.divide(cap, ltp) if traded and cap is not None and ltp else None,
                          "not_traded" if not traded else ("not_reported" if cap is None else "no_last_traded_price")))
        rng = traded and bar.high is not None and bar.low is not None
        out.append(simple("range_1", CTX.divide(CTX.subtract(bar.high, bar.low), bar.px_close) if rng else None,
                          "not_traded" if not traded else "no_high_low"))
        notes = self._end(self.t - 1)
        notes.items = [x for x in notes.items if x[0] != STALE]            # the previous price is a carried one by design
        if self.t >= 1 and self.s.sequence.spans_gap(self.t):
            notes.add(MISSING, "gaps_beyond_tolerance")                    # a 1-session window tolerates no gap
        if bar is None or bar.traded is not True or bar.open_price is None or bar.open_price <= 0:
            notes.add(MISSING, "no_open_price")
        elif bar.quality == CONFLICTING:
            notes.add(CONFLICT, "conflicting_bar")
        if self.t >= 1 and actions.unlabelled(self.s.moves, self.sessions[self.t - 1], self.sessions[self.t]):
            notes.add(UNLABELLED, "unexplained_move")
        value = None if notes.is_null() else numeric.simple_return(bar.open_price, self._c(self.t - 1))
        out.append(finish("gap_1", value, notes))
        return out

    def all(self):
        """Every per-security feature of the registry, by feature id."""
        values = (self.returns() + self.volatility() + self.momentum() + self.drawdown() + self.volume()
                  + self.liquidity() + self.return_history() + self.size_and_range())
        return {v.feature_id: v for v in values}


SECURITY_FEATURE_IDS = tuple(
    [f"ret_{n}" for n in (1, 5, 10, 21, 63, 126, 252)] + ["vol_21", "vol_63", "vol_parkinson_21", "mom_252_21",
    "mom_126_21", "dd_252", "dd_days_252", "vol_shares_avg_21", "turnover_avg_21", "trades_avg_21", "rel_volume_21",
    "amihud_21", "zero_trade_ratio_63", "stale_sessions"]
    + [f"hist_{k}_{h}" for h in HISTORY_HORIZONS for k in ("up_share", "flat_share", "down_share", "mean_ret", "n")]
    + ["market_cap_reported", "shares_implied", "range_1", "gap_1"])
