"""
Reconstruction of bonus-issue and rights-issue ex-dates from the TradeIQ archive's own daily prices, by a strict rule
(owner decision C-0055, P3-R2). Research step, run OUTSIDE MF: it produces corporate-action evidence rows marked
`reconstructed`; MF reads them like any other evidence and never infers an action itself.

The rule (`find_ex_date`):
  1. An event is considered only if its company name matches exactly one ordinary voting security, and its ratio is
     readable ("1 for 2", "2:1", "01 for 01", or Excel's conversion of "h:mm" into a fraction of a day).
  2. Both readings of the ratio are tried (new:held and held:new).
  3. Inside the window, a session w is a match for a reading when the security traded on w and on its previous trade
     u, and close(w) / close(u) lies within TOLERANCE (relative) of the reading's expected factor:
        bonus   f = held / (held + new)
        rights  f = TERP / P_cum, TERP = (held * P_cum + new * P_sub) / (held + new), P_cum = close(u)
     A reading whose expected factor is above MAX_FACTOR (a fall under 10%) is not considered: it cannot be told apart
     from an ordinary price move.
  4. Exactly one (session, expected factor) must match. Zero or several: the event stays unentered.
The ex-date is the matching session w. For adjustment by source session this is exact even when the true ex-date
was an earlier day without trades: no price was printed between u and w.
"""
import bisect
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Optional

METHOD = "reconstructed_from_prices.v1"
TOLERANCE = Fraction(7, 100)
MAX_FACTOR = Fraction(9, 10)
WINDOWS = {"bonus": (45, 7), "rights": (60, 21)}      # days before and after the allotment date

STOP = {"PLC", "LTD", "LIMITED", "THE", "CO", "COMPANY", "OF", "AND", "SRI", "LANKA", "CEYLON"}


def tokens(name):
    return [t for t in re.sub(r"[^A-Z0-9 ]", " ", str(name).upper()).split() if t not in STOP]


def match_symbol(company, names_by_symbol):
    """The one ordinary voting symbol whose short name agrees with the company's first token and the first three
    letters of its second; None when none or several do (`ambiguous`)."""
    t = tokens(company)
    if not t:
        return None, "unreadable_name"
    found = set()
    for sym, names in names_by_symbol.items():
        if not sym.endswith(".N0000"):
            continue
        for n in names:
            nt = tokens(n)
            if nt and nt[0] == t[0] and (len(t) < 2 or len(nt) < 2 or nt[1][:3] == t[1][:3]):
                found.add(sym)
                break
    if len(found) == 1:
        return found.pop(), None
    return None, ("ambiguous_name" if found else "unmatched_name")


def decode_ratio(cell):
    """(a, b) of a ratio cell, or None. Excel turns a typed "1:4" into 1h04 = 64/1440 of a day."""
    if isinstance(cell, float) and 0 < cell < 1:
        minutes = cell * 1440
        if abs(minutes - round(minutes)) < 1e-6:
            m = round(minutes)
            a, b = divmod(m, 60)
            return (a, b) if a > 0 and b > 0 else None
        return None
    s = str(cell).strip().lower()
    m = re.fullmatch(r"0*(\d+)\s*(?:for|:)\s*0*(\d+)", s)
    if m and int(m[1]) > 0 and int(m[2]) > 0:
        return int(m[1]), int(m[2])
    return None


@dataclass(frozen=True)
class Match:
    ex_date: date
    reading: str                         # "new:held" or "held:new"
    expected: Fraction
    observed: Fraction
    cum_session: date


def _readings(ratio):
    a, b = ratio
    return (("new:held", a, b), ("held:new", b, a))       # (label, new, held)


def find_ex_date(kind, ratio, closes, window, subscription_price=None):
    """closes: [(session, close)] of one security, ascending, traded sessions only. window: (lo, hi) dates.
    Returns (Match or None, reason)."""
    if kind not in WINDOWS:
        raise ValueError("bonus or rights")
    if kind == "rights" and (subscription_price is None or subscription_price <= 0):
        return None, "no_subscription_price"
    lo, hi = window
    matches, considered, in_window = [], False, False
    for (u, p), (w, q) in zip(closes, closes[1:]):
        if not lo <= w <= hi or p <= 0:
            continue
        in_window = True
        p, q = Fraction(p), Fraction(q)
        for label, new, held in _readings(ratio):
            if kind == "bonus":
                f = Fraction(held, held + new)
            else:
                sub = Fraction(subscription_price)
                if sub >= p:
                    continue                                     # out of the money: no adjustment, nothing to find
                f = (held * p + new * sub) / (held + new) / p
            if f > MAX_FACTOR:
                continue
            considered = True
            obs = q / p
            if abs(obs / f - 1) <= TOLERANCE:
                matches.append(Match(w, label, f, obs, u))
    distinct = {(m.ex_date, m.expected): m for m in matches}    # a symmetric ratio (1:1) gives one factor twice
    if len(distinct) == 1:
        return next(iter(distinct.values())), "reconstructed"
    if not distinct:
        # every reading's fall was under 10% (or the rights were out of the money): skipped by design, not a failure
        return None, ("below_threshold" if in_window and not considered else "no_match")
    return None, "ambiguous_match"


def window_for(kind, allotment, announcement=None):
    before, after = WINDOWS[kind]
    lo = allotment - timedelta(days=before)
    if announcement is not None and announcement > lo:
        lo = announcement
    return lo, allotment + timedelta(days=after)


def validate_on_splits(splits, closes_by_symbol, before=30, after=30):
    """Hide each split's effective date and let the rule find it inside +- 30 days, with the split's own ratio read
    as a bonus-like factor old/new. Returns a dict of counts: correct (the first traded session on or after the
    effective date), wrong, no_match, ambiguous_match, no_series."""
    out = {"correct": 0, "wrong": 0, "no_match": 0, "ambiguous_match": 0, "no_series": 0}
    for sym, old, new, eff in splits:
        closes = closes_by_symbol.get(sym)
        if not closes:
            out["no_series"] += 1
            continue
        f = Fraction(old) / Fraction(new)
        lo, hi = eff - timedelta(days=before), eff + timedelta(days=after)
        found = [w for (u, p), (w, q) in zip(closes, closes[1:])
                 if lo <= w <= hi and p > 0 and f <= MAX_FACTOR and abs(Fraction(q) / Fraction(p) / f - 1) <= TOLERANCE]
        if len(found) != 1:
            out["no_match" if not found else "ambiguous_match"] += 1
            continue
        dates = [d for d, _ in closes]
        first_on_or_after = dates[bisect.bisect_left(dates, eff)] if bisect.bisect_left(dates, eff) < len(dates) else None
        out["correct" if found[0] == first_on_or_after else "wrong"] += 1
    return out
