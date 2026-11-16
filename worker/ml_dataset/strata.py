"""
Strata (Phase 5 §4.1, §12.2). Each is computed point-in-time: from the same-T universe, or from sessions at or before
T, never from a later one (MLK-17). Regime tags are strata only, never model inputs.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

LOW, MID, HIGH, UNKNOWN, UNDEFINED = "low", "mid", "high", "unknown", "undefined"
UP, FLAT, DOWN = "up", "flat", "down"
ADMISSIBLE = ("fresh", "stale", "partial")


@dataclass(frozen=True)
class RegimePolicy:
    """Provisional and pinned in the manifest (§12.2). trend: mkt_ret_ew_21 against fixed bands; volatility: the
    cross-sectional median of vol_21 placed in its own trailing distribution of earlier sessions, `undefined` until
    that distribution holds `min_history` sessions."""
    version: str = "regime.0"
    status: str = "provisional"
    trend_band: Decimal = Decimal("0.02")
    min_history: int = 252
    max_history: int = 1260


def _admissible(v):
    return v is not None and v[0] is not None and v[1] in ADMISSIBLE


def liquidity_classes(turnover):
    """Terciles of turnover_avg_21 in the same-T universe. turnover: {key: (value, state, reason)}. A member without an
    admissible value is `unknown`. Ties share a class: the class follows the average rank, so equal values never
    split across a boundary."""
    vals = {k: v[0] for k, v in turnover.items() if _admissible(v)}
    out = {k: UNKNOWN for k in turnover}
    n = len(vals)
    ordered = sorted(vals.values())
    for k, x in vals.items():
        below = sum(1 for y in ordered if y < x)
        equal = sum(1 for y in ordered if y == x)
        p = (Fraction(below) + Fraction(equal, 2)) / n          # mid-rank percentile, strictly inside (0, 1)
        out[k] = LOW if p < Fraction(1, 3) else MID if p < Fraction(2, 3) else HIGH
    return out


def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def trend_tag(mkt_ret_ew_21, policy=RegimePolicy()):
    """(tag, reason). mkt_ret_ew_21: (value, state, reason) or None when the snapshot does not carry it (C-0087)."""
    if mkt_ret_ew_21 is None:
        return UNDEFINED, "market_aggregate_not_stored"
    if not _admissible(mkt_ret_ew_21):
        return UNDEFINED, mkt_ret_ew_21[2] or mkt_ret_ew_21[1]
    v = mkt_ret_ew_21[0]
    return (UP if v > policy.trend_band else DOWN if v < -policy.trend_band else FLAT), None


def volatility_tags(medians, policy=RegimePolicy()):
    """medians: [(T, median vol_21 or None)] in time order, one per session. Returns {T: (tag, reason)}: the session's
    median placed in the distribution of the medians of the sessions strictly before it (at most max_history)."""
    out, history = {}, []
    for t, m in medians:
        if m is None:
            out[t] = (UNDEFINED, "no_cross_sectional_median")
        elif len(history) < policy.min_history:
            out[t] = (UNDEFINED, "trailing_history_short")
        else:
            window = history[-policy.max_history:]
            below = sum(1 for y in window if y < m)
            equal = sum(1 for y in window if y == m)
            p = (Fraction(below) + Fraction(equal, 2)) / len(window)
            out[t] = (LOW if p < Fraction(1, 3) else MID if p < Fraction(2, 3) else HIGH, None)
        if m is not None:
            history.append(m)
    return out


AGE_BOUNDS = (21, 63, 126, 252)


def age_stratum(sessions_since_c_train):
    """Model age (§5.6): sessions from C_train to the row's T, in bins [0, 21), [21, 63), [63, 126), [126, 252),
    [252, inf). Depends on T and C_train only."""
    if sessions_since_c_train is None or sessions_since_c_train < 0:
        raise ValueError("model age is sessions from C_train to T, never negative")
    lo = 0
    for b in AGE_BOUNDS:
        if sessions_since_c_train < b:
            return f"age_{lo}_{b}"
        lo = b
    return f"age_{lo}_plus"
