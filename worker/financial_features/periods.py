"""
F7-2: period arithmetic (Phase 4 design §5.2). Pure. Each derived value records its rule, the ef_keys used and their
states; f7.sign.2 is applied per fact before any arithmetic; a rule applies only when facts exist with exactly the
required period ends (dates.py, §5.1).
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .dates import exact_rule_ok, months_before  # noqa: F401  (re-exported)
from .panel import CONFLICTING, FRESH, MISSING, SHOWN, UNTRUSTED, signed

# ------------------------------------------------------------------------------------------------ F7-2 rules (§5.2)


CASHFLOW_SIGN_POLICY = "f7.cashflow_sign.1"
NULL_ORDER = (UNTRUSTED, CONFLICTING, MISSING)          # Phase 3 §10.4: the first null state that applies wins


@dataclass(frozen=True)
class Derived:
    """A derived value: its state, value and interval, the rule, the ef_keys it used, the reason when null, the other
    paths (for TTM), and flags (sign rule uses, corroborated_paths)."""
    state: str
    value: Optional[Decimal] = None
    low: Optional[Decimal] = None
    high: Optional[Decimal] = None
    rule: Optional[str] = None
    inputs: tuple = ()
    reason: Optional[str] = None
    paths: tuple = ()
    flags: tuple = ()

    @property
    def number(self):
        return self.state == FRESH and self.value is not None


def _get(panel, concept, end, months, maturity="not_applicable"):
    """(value, low, high, ef_key, flags) for one fact, after f7.sign.2 per fact; or a null Derived."""
    u = panel.fact(concept, end, months, maturity=maturity)
    if u is None:
        return Derived(MISSING, reason="no_exact_period_match")
    if u.f8_state is not None and u.f8_state not in SHOWN:
        # a fact F8 does not show (in KNOWN, a document held but not yet public) is as absent as one never filed:
        # it makes no path applicable (third audit B-1; C-0073)
        return Derived(MISSING, reason=u.reason or "absent", inputs=(u.ef_key,), flags=("not_shown",))
    if u.state != FRESH:
        return Derived(u.state, reason=u.reason, inputs=(u.ef_key,))
    if u.nil:
        return Derived(MISSING, reason="printed_nil", inputs=(u.ef_key,))
    v, rule = signed(concept, u.value)
    lo, hi = u.low, u.high
    if rule == "magnitude" and (lo is None) != (hi is None):
        lo = hi = None                        # one bound only: no interval, the value stands for both
    if rule == "magnitude" and lo is not None and hi is not None:
        # the magnitude of an interval: [0, max] if it straddles zero, else the reflected bounds (audit minor)
        lo, hi = (Decimal(0), max(-lo, hi)) if lo < 0 < hi else tuple(sorted((abs(lo), abs(hi))))
    printed = "negative" if u.value < 0 else "positive" if u.value > 0 else "zero"
    return (v, lo if lo is not None else v, hi if hi is not None else v, u.ef_key,
            (f"sign_rule:magnitude:{concept}:printed_{printed}",) if rule == "magnitude" else ())


def _combine(rule, parts, signs):
    """sum(sign * part); null if any part is null (the first null state in NULL_ORDER wins)."""
    nulls = [p for p in parts if isinstance(p, Derived)]
    if nulls:
        state = next(s for s in NULL_ORDER if any(p.state == s for p in nulls))
        first = next(p for p in nulls if p.state == state)
        return Derived(state, rule=rule, reason=first.reason, inputs=tuple(i for p in nulls for i in p.inputs))
    v = sum((s * p[0] for s, p in zip(signs, parts)), Decimal(0))
    lo = sum(((p[1] if s > 0 else p[2]) * s for s, p in zip(signs, parts)), Decimal(0))
    hi = sum(((p[2] if s > 0 else p[1]) * s for s, p in zip(signs, parts)), Decimal(0))
    return Derived(FRESH, v, lo, hi, rule, tuple(p[3] for p in parts), flags=tuple(f for p in parts for f in p[4]))


def _absent(parts):
    """A path does not apply when one of its facts does not exist (re-audit B-1: decided per part, not from the
    combined state, which a conflicting part would make `conflicting`)."""
    return any(isinstance(x, Derived) and x.state == MISSING
               and (x.reason == "no_exact_period_match" or "not_shown" in x.flags) for x in parts)


TTM_RULES = ("F7P-1", "F7P-2", "F7P-3")


def ttm(panel, concept, end, rules=TTM_RULES):
    """TTM(E) by every rule of `rules` that applies (F7P-1, F7P-2 for n in 3, 6, 9, F7P-3). Paths agree when their
    intervals overlap: the lowest-numbered rule's value is used, marked corroborated_paths (P4-D8). Otherwise
    conflicting, with every path kept. An applicable path (all its facts exist) with a conflicting or untrusted input
    makes the TTM take that state: F7 never picks a side (§4.2, §5.2; C-0073)."""
    if not exact_rule_ok(end):
        return Derived(MISSING, reason="no_exact_period_match")
    specs = []
    if "F7P-1" in rules:
        specs.append(("F7P-1", [_get(panel, concept, end, 12)], [1]))
    if "F7P-2" in rules:
        for n in (3, 6, 9):
            e_n, e_12 = months_before(end, n), months_before(end, 12)
            specs.append((f"F7P-2:{n}", [_get(panel, concept, end, n), _get(panel, concept, e_n, 12),
                                         _get(panel, concept, e_12, n)], [1, 1, -1]))
    if "F7P-3" in rules:
        specs.append(("F7P-3", [_get(panel, concept, months_before(end, k), 3) for k in (0, 3, 6, 9)],
                      [1, 1, 1, 1]))
    paths, applicable = [], []
    for rule, parts, signs in specs:
        d = _combine(rule, parts, signs)
        paths.append(d)
        if not _absent(parts):
            applicable.append(d)
    blocked = [p for p in applicable if p.state in (UNTRUSTED, CONFLICTING)]
    if blocked:
        state = next(s for s in NULL_ORDER if any(p.state == s for p in blocked))
        first = next(p for p in blocked if p.state == state)
        return Derived(state, reason=first.reason, inputs=tuple(i for p in blocked for i in p.inputs),
                       paths=tuple(paths))
    ok = [p for p in applicable if p.number]
    if not ok:
        nulls = [p for p in applicable if not p.number]
        if nulls:
            state = next(s for s in NULL_ORDER if any(p.state == s for p in nulls))
            first = next(p for p in nulls if p.state == state)
            return Derived(state, reason=first.reason, paths=tuple(paths))
        return Derived(MISSING, reason="no_exact_period_match", paths=tuple(paths))
    lo, hi = max(p.low for p in ok), min(p.high for p in ok)
    best = ok[0]
    if len(ok) > 1 and lo > hi:
        return Derived(CONFLICTING, reason="paths_disagree", paths=tuple(paths))
    flags = best.flags + (("corroborated_paths",) if len(ok) > 1 else ())
    return Derived(FRESH, best.value, best.low, best.high, best.rule, best.inputs, paths=tuple(ok), flags=flags)


def avg_instant(panel, concept, end):
    """F7P-5: (I(E) + I(E-12)) / 2."""
    if not exact_rule_ok(end):
        return Derived(MISSING, reason="no_exact_period_match")
    d = _combine("F7P-5", [_get(panel, concept, end, None), _get(panel, concept, months_before(end, 12), None)],
                 [1, 1])
    if not d.number:
        return d
    return Derived(FRESH, d.value / 2, d.low / 2, d.high / 2, d.rule, d.inputs, flags=d.flags)


def _growth(rule, cur, base):
    nulls = [x for x in (cur, base) if not x.number]
    if nulls:                                       # Phase 3 §10.4's order across both inputs (audit M4)
        state = next(s for s in NULL_ORDER if any(x.state == s for x in nulls))
        first = next(x for x in nulls if x.state == state)
        return Derived(state, rule=rule, reason=first.reason, inputs=cur.inputs + base.inputs)
    if base.value <= 0:
        return Derived(MISSING, rule=rule, reason="base_not_positive", inputs=cur.inputs + base.inputs)
    return Derived(FRESH, cur.value / base.value - 1, rule=rule, inputs=cur.inputs + base.inputs,
                   flags=cur.flags + base.flags)


def growth_instant(panel, concept, end):
    """F7P-8: I(E) / I(E-12) - 1, only when I(E-12) > 0."""
    def one(e):
        x = _get(panel, concept, e, None)
        return x if isinstance(x, Derived) else Derived(FRESH, x[0], x[1], x[2], "fact", (x[3],), flags=x[4])
    return _growth("F7P-8", one(end), one(months_before(end, 12)))


def growth_ttm(panel, concept, end):
    """F7P-6: TTM(E) / TTM(E-12) - 1, only when TTM(E-12) > 0."""
    return _growth("F7P-6", ttm(panel, concept, end), ttm(panel, concept, months_before(end, 12)))


def growth_cum(panel, concept, end, months):
    """F7P-7: D_n(E) / D_n(E-12) - 1 for the anchor's own n."""
    def one(e):
        x = _get(panel, concept, e, months)
        return x if isinstance(x, Derived) else Derived(FRESH, x[0], x[1], x[2], "fact", (x[3],), flags=x[4])
    return _growth("F7P-7", one(end), one(months_before(end, 12)))


CASHFLOW_TOTALS = ("net_cash_from_operating_activities", "net_cash_from_investing_activities",
                   "net_cash_from_financing_activities")


def cashflow_confirmed(panel, end, months):
    """f7.cashflow_sign.1: the three totals for (E, n) are used only when operating + investing + financing equals the
    change in cash_at_end_of_period from E-n to E, within the facts' intervals. Returns {concept: Derived}; an
    unconfirmed total is missing with reason sign_unconfirmed."""
    if not exact_rule_ok(end):
        return {c: Derived(MISSING, rule=CASHFLOW_SIGN_POLICY, reason="no_exact_period_match")
                for c in CASHFLOW_TOTALS}
    parts = {c: _get(panel, c, end, months) for c in CASHFLOW_TOTALS}
    opening = _get(panel, "cash_at_end_of_period", months_before(end, months), None)
    closing = _get(panel, "cash_at_end_of_period", end, None)
    total = _combine("cashflow_sum", [parts[c] for c in CASHFLOW_TOTALS], [1, 1, 1])
    change = _combine("cash_change", [closing, opening], [1, -1])
    confirmed = total.number and change.number and total.low <= change.high and change.low <= total.high
    out = {}
    for c, p in parts.items():
        if isinstance(p, Derived):
            out[c] = p
        elif confirmed:
            out[c] = Derived(FRESH, p[0], p[1], p[2], CASHFLOW_SIGN_POLICY, total.inputs + change.inputs,
                             flags=("sign_confirmed",))
        else:
            out[c] = Derived(MISSING, rule=CASHFLOW_SIGN_POLICY, reason="sign_unconfirmed", inputs=(p[3],))
    return out


def instant(panel, concept, end, maturity="not_applicable"):
    """One instant fact as a Derived (f7.sign.2 applied)."""
    x = _get(panel, concept, end, None, maturity)
    return x if isinstance(x, Derived) else Derived(FRESH, x[0], x[1], x[2], "fact", (x[3],), flags=x[4])


def duration(panel, concept, end, months):
    """One duration fact as a Derived (f7.sign.2 applied)."""
    x = _get(panel, concept, end, months)
    return x if isinstance(x, Derived) else Derived(FRESH, x[0], x[1], x[2], "fact", (x[3],), flags=x[4])


def total(rule, parts, signs):
    """sum(sign * part) over Derived values (null if any is null; the first null state of NULL_ORDER wins)."""
    raw = [p if not p.number else (p.value, p.low if p.low is not None else p.value,
                                   p.high if p.high is not None else p.value, None, p.flags) for p in parts]
    d = _combine(rule, raw, signs)
    return Derived(d.state, d.value, d.low, d.high, rule, tuple(i for p in parts for i in p.inputs), d.reason,
                   flags=tuple(f for p in parts for f in p.flags if f != "not_shown"))   # internal marker only

