"""
Market-context features (design sections 10.2 and 10.3): the self-computed, equal-weighted market return.

m_u   the equal-weighted mean of r_u over the equity lines that were market-data eligible AS OF SESSION u-1, with that
      eligibility recomputed from the snapshot's own evidence. Membership uses nothing from session u or later. It
      needs at least min_constituents members WITH A RETURN; otherwise it is missing. A member without a return on u
      is left out; the denominator is the members with a value. m_u is a mean of log returns: mkt_ret_ew_n compounds
      it, the return of the cross-sectional geometric mean. m_u inherits spans_gap and spans_unknown from its members.

Eligibility does not depend on any market-context feature, so this is not circular.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from . import numeric
from .calendar import gap_tolerance
from .features import (FRESH, MISSING, PARTIAL, UNLABELLED, Calculator, FeatureConfig, Notes, Value, finish)
from .numeric import CTX
from .universe import ELIGIBLE, EligibilityPolicy, eligibility

MARKET_FEATURE_IDS = ("mkt_ret_ew_1", "mkt_ret_ew_5", "mkt_ret_ew_21", "mkt_ret_ew_63", "mkt_breadth_1", "rel_ret_21",
                      "rel_ret_63", "beta_126", "xs_rank_ret_21")


@dataclass(frozen=True)
class MarketPoint:
    m: Optional[Decimal]                 # unquantized
    members: tuple                       # the symbols whose return entered m (empty when missing)
    eligible: int                        # equity lines eligible as of the session before
    spans_gap: bool = False
    spans_unknown: bool = False


def eligibility_by_session(series_by_symbol, references_by_symbol, policy: EligibilityPolicy = EligibilityPolicy()):
    """{symbol: [eligible at each session of the sequence]}, each computed from the bars up to that session only."""
    out = {}
    for symbol, series in series_by_symbol.items():
        refs = references_by_symbol[symbol]
        sessions = series.sequence.sessions
        out[symbol] = [eligibility(symbol, series.bars[:i + 1], refs.get(sessions[i]), policy).result == ELIGIBLE
                       for i in range(len(sessions))]
    return out


def market_points(series_by_symbol, eligible, min_constituents):
    """One MarketPoint per session. Session 0 has no earlier session to fix membership on: missing."""
    symbols = sorted(series_by_symbol)
    length = len(next(iter(series_by_symbol.values()))) if symbols else 0
    points = [MarketPoint(None, (), 0)]
    for u in range(1, length):
        pool = [s for s in symbols if eligible[s][u - 1]]
        with_return = [s for s in pool if series_by_symbol[s].points[u].r is not None]
        if len(with_return) < min_constituents:
            points.append(MarketPoint(None, (), len(pool)))
            continue
        ps = [series_by_symbol[s].points[u] for s in with_return]
        points.append(MarketPoint(numeric.mean([p.r for p in ps]), tuple(with_return), len(pool),
                                  any(p.spans_gap for p in ps), any(p.spans_unknown for p in ps)))
    return points[:length]


def _market_sum(points, start, end, notes: Notes):
    """Sum of m_u over (start, end]; every m_u is required."""
    if start < 0:
        notes.add(MISSING, "insufficient_history")
        return None
    window = points[start + 1:end + 1]
    if any(p.m is None for p in window):
        notes.add(MISSING, "market_return_missing")
        return None
    if any(p.spans_gap for p in window):
        notes.add(PARTIAL, "spans_gap")
    if any(p.spans_unknown for p in window):
        notes.add(PARTIAL, "spans_unknown")
    return numeric.add(*[p.m for p in window])


def market_features(points, sequence):
    """The features that are the same for every security of the snapshot."""
    t = len(points) - 1
    out = {}
    for n in (1, 5, 21, 63):
        notes = Notes()
        total = _market_sum(points, t - n, t, notes)
        if total is not None and len(sequence.gaps_between(t - n, t)) > gap_tolerance(n):
            notes.add(MISSING, "gaps_beyond_tolerance")
        value = None if notes.is_null() or total is None else CTX.subtract(numeric.exp(total), Decimal(1))
        out[f"mkt_ret_ew_{n}"] = finish(f"mkt_ret_ew_{n}", value, notes, None, n)
    return out


def breadth(points, series_by_symbol):
    t = len(points) - 1
    p = points[t]
    notes = Notes()
    if p.m is None:
        return finish("mkt_breadth_1", None, notes.add(MISSING, "below_min_constituents"))
    up = sum(1 for s in p.members if series_by_symbol[s].points[t].r > 0)
    if p.spans_gap:
        notes.add(PARTIAL, "spans_gap")
    if p.spans_unknown:
        notes.add(PARTIAL, "spans_unknown")
    return finish("mkt_breadth_1", CTX.divide(Decimal(up), Decimal(len(p.members))), notes, None, len(p.members))


def security_market_features(symbol, series, points, ret_21_by_symbol, config: FeatureConfig = FeatureConfig()):
    """rel_ret_21, rel_ret_63, beta_126 and xs_rank_ret_21 for one security.
    ret_21_by_symbol: {symbol: the Value of ret_21} for the securities of the snapshot."""
    calc = Calculator(series, config)
    t = calc.t
    out = {}
    for n in (21, 63):
        own = calc._price_return("x", t, n)
        notes = Notes()
        notes.items = list(own.reasons)
        total = _market_sum(points, t - n, t, notes)
        value = None
        if not notes.is_null() and total is not None:
            value = CTX.subtract(numeric.log_return(series.points[t].c, series.points[t - n].c), total)
        out[f"rel_ret_{n}"] = finish(f"rel_ret_{n}", value, notes, own.traded_sessions)
    out["beta_126"] = _beta(calc, series, points, t)
    out["xs_rank_ret_21"] = _rank(symbol, points[t], ret_21_by_symbol, config)
    return out


def _beta(calc, series, points, t, n=126, minimum=80):
    notes = calc._stretch(t - n, t, n)
    pairs = []
    for u in range(max(t - n + 1, 1), t + 1):
        p, m = series.points[u], points[u]
        if p.r is not None and not p.spans_unknown and m.m is not None:       # a spans_unknown return is left out
            pairs.append((p.r, m.m))
    if len(pairs) < minimum:
        notes.add(MISSING, "below_min_observations")
    elif len(pairs) < n:
        notes.add(PARTIAL, "fewer_inputs_than_window")
    value = None
    if not notes.is_null():
        mr, mm = numeric.mean([r for r, _ in pairs]), numeric.mean([m for _, m in pairs])
        cov = numeric.add(*[CTX.multiply(CTX.subtract(r, mr), CTX.subtract(m, mm)) for r, m in pairs])
        var = numeric.add(*[CTX.multiply(CTX.subtract(m, mm), CTX.subtract(m, mm)) for _, m in pairs])
        if var == 0:
            notes.add(MISSING, "zero_base")
        else:
            value = CTX.divide(cov, var)
    return finish("beta_126", value, notes, None, len(pairs))


def _rank(symbol, point, ret_21_by_symbol, config):
    """Percentile rank of ret_21 among the members of m_t with a value: (average rank - 1/2) / N, rank 1 the lowest,
    ties taking the average rank."""
    notes = Notes()
    if point.m is None:
        return finish("xs_rank_ret_21", None, notes.add(MISSING, "below_min_constituents"))
    values = {s: ret_21_by_symbol[s].value for s in point.members
              if s in ret_21_by_symbol and ret_21_by_symbol[s].value is not None}
    if len(values) < config.min_constituents:
        return finish("xs_rank_ret_21", None, notes.add(MISSING, "below_min_constituents"), None, len(values))
    if symbol not in values:
        return finish("xs_rank_ret_21", None, notes.add(MISSING, "not_a_member_with_a_value"), None, len(values))
    own = values[symbol]
    below = sum(1 for v in values.values() if v < own)
    equal = sum(1 for v in values.values() if v == own)
    average_rank = CTX.add(Decimal(below), CTX.divide(Decimal(equal + 1), Decimal(2)))
    own_state = ret_21_by_symbol[symbol].state
    if own_state != FRESH:
        notes.add(PARTIAL if own_state not in (UNLABELLED,) else UNLABELLED, "ret_21_" + own_state)
    return finish("xs_rank_ret_21", CTX.divide(CTX.subtract(average_rank, Decimal("0.5")), Decimal(len(values))),
                  notes, None, len(values))
