"""
F7-3 (part 2): EPS restated onto the price's share basis, and the valuation features (Phase 4 design §6.2 Earnings,
§6.3). Pure: it reads the panel and, per security, the MF snapshot inputs F7 is allowed (§3): `adj_ref`,
`ref_session`, `stale_sessions`, `market_cap_reported`, the factor list and the move flags.

f7.eps_basis.1:  eps_restated = eps x prod f_k over the share-basis actions k with date(A) < e_k <= D*;
                 price = adj_ref (already on D*'s basis). A is the latest availability instant among the fact's
                 visible observations; date(A) its Colombo date.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from . import panel as pn
from .dates import months_before
from .features import FeatureValue, ratio
from .periods import Derived, ttm

EPS_BASIS_POLICY = "f7.eps_basis.1"
COLOMBO = timezone(timedelta(hours=5, minutes=30))
SHARE_BASIS_KINDS = ("subdivision", "consolidation", "bonus", "scrip_dividend", "rights")
PARTIAL, STALE = "partial", "stale"
MCAP_FEATURES = ("issuer_market_value", "earnings_yield_mcap", "book_to_market", "sales_to_market", "cfo_yield",
                 "fcf_yield", "dividend_yield_cash")


@dataclass(frozen=True)
class Factor:
    """A row of mf_snapshot_factors."""
    kind: str
    ex_date: date
    numerator: Optional[int]
    denominator: Optional[int]
    status: str                          # applied | out_of_the_money | no_price_factor | unresolved
    flags: tuple = ()


@dataclass(frozen=True)
class Move:
    """A row of mf_snapshot_move_flags."""
    session_from: date
    session_to: date
    status: str                          # open | confirmed_no_action | not_evaluable


@dataclass(frozen=True)
class SecurityRow:
    """The MF snapshot row of one equity line, with its factors and move flags."""
    symbol: str
    company_id: Optional[str]
    d_star: date
    px_ref: Optional[Decimal]
    adj_ref: Optional[Decimal]
    ref_session: Optional[date]
    stale_sessions: Optional[int]
    market_cap_reported: Optional[Decimal]
    factors: tuple = ()
    moves: tuple = ()


def _colombo_date(text, precision):
    if text is None:
        return None
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if precision == "day":
        # F8 stores a day-precision availability as the end of that Colombo day (the next 00:00 Colombo); it counts
        # as that day (§6.3; re-audit B-4)
        t = t - timedelta(microseconds=1)
    return t.astimezone(COLOMBO).date()


def availability_date(u):
    """(date(A), dates of every visible observation) or (None, ...) if any has no availability instant."""
    dates = [_colombo_date(a, p) for a, p in u.availability]
    if not dates or any(d is None for d in dates):
        return None, dates
    return max(dates), dates


def restate(u, row):
    """f7.eps_basis.1 for one EPS fact (a panel Usable) on one security's row: a Derived."""
    if u is None:
        return Derived(pn.MISSING, reason="absent")
    if u.state != pn.FRESH or u.nil or u.value is None:
        return Derived(u.state if u.state != pn.FRESH else pn.MISSING, reason=u.reason or "printed_nil",
                       inputs=(u.ef_key,))
    a, dates = availability_date(u)
    if a is None or any(f in u.recorded for f in pn.RECORDED_AVAILABILITY_FLAGS):
        return Derived(pn.UNTRUSTED, reason="eps_basis_ambiguous", inputs=(u.ef_key,))
    if row.ref_session is not None and row.ref_session < a:
        # no close since the document: untrusted for this reason whether or not an action is entered (FL-21 (d))
        return Derived(pn.UNTRUSTED, reason="no_close_since_document", inputs=(u.ef_key,))
    share = [f for f in row.factors if f.kind in SHARE_BASIS_KINDS]
    # an ex-date between the earliest and latest availability of the fact's observations (FL-18), or between the
    # period end and date(A) (the printed figure may or may not reflect it): never guessed
    lo = min(dates)
    if any(lo < f.ex_date <= a for f in share if lo < a) or any(u.period_end < f.ex_date <= a for f in share):
        return Derived(pn.UNTRUSTED, reason="eps_basis_ambiguous", inputs=(u.ef_key,))
    # an action not yet entered: an open move firing after the period end and at or before D* (FL-21)
    for m in row.moves:
        if m.status == "open" and u.period_end < m.session_to <= row.d_star:
            why = "eps_basis_ambiguous" if m.session_to <= a else "unexplained_move_since_document"
            return Derived(pn.UNTRUSTED, reason=why, inputs=(u.ef_key,))
    product, state, flags = Fraction(1), pn.FRESH, []
    for f in sorted(share, key=lambda x: x.ex_date):
        if not (a < f.ex_date <= row.d_star):
            continue
        if f.status == "unresolved":
            return Derived(pn.UNTRUSTED, reason="factor_unresolved", inputs=(u.ef_key,))
        if f.status != "applied" or f.numerator is None:
            continue
        product *= Fraction(f.numerator, f.denominator)
        if "cum_price_stale" in f.flags:
            state = PARTIAL
            flags.append("cum_price_stale")
    k = Decimal(product.numerator) / Decimal(product.denominator)
    value = u.value * k
    lo, hi = (u.low * k if u.low is not None else value), (u.high * k if u.high is not None else value)
    return Derived(state, value, lo, hi, rule=EPS_BASIS_POLICY, inputs=(u.ef_key,), reason="cum_price_stale"
                   if state == PARTIAL else None, flags=tuple(flags) + ("equal_earnings_per_line_assumed",))


def eps_latest_fy_fact(p):
    """§6.2: the 12-month eps_basic at the latest period end at or before the anchor that has such a fact in state
    single_source, corroborated or conflicting (never stepping back past an unusable one)."""
    ends = sorted({k[1] for k, u in p.facts.items() if k[0] == "eps_basic" and k[2] == 12 and k[1] <= p.anchor_period
                   and k[3] == pn.ANCHOR_OPERATIONS and k[4] == pn.NOT_APPLICABLE and u.f8_state in pn.SHOWN})
    if not ends:
        return None, None
    end = ends[-1]
    return end, p.fact("eps_basic", end, 12)


def _price(row):
    if row.adj_ref is not None:
        return Derived(pn.FRESH, row.adj_ref, rule="adj_ref")
    if row.px_ref is None:
        return Derived(pn.MISSING, reason="price_unknown")
    return Derived(pn.UNTRUSTED, reason="factor_unresolved")


def _staleness(d, row, max_stale):
    # every state that carries a value is checked against the limit, partial included (re-audit B-5)
    if d.value is None or d.state not in (pn.FRESH, PARTIAL, STALE) or not row.stale_sessions:
        return d
    if row.stale_sessions > max_stale:
        return Derived(pn.MISSING, reason="stale_price", inputs=d.inputs)
    return Derived(STALE if d.state == pn.FRESH else d.state, d.value, rule=d.rule, inputs=d.inputs,
                   reason=d.reason, flags=d.flags)


def _fv(fid, d, *flags):
    return FeatureValue(fid, d.state, d.value if d.state in (pn.FRESH, PARTIAL, STALE) else None, d.reason,
                        tuple(x for x in (d.rule,) if x), d.inputs, tuple(dict.fromkeys(d.flags + flags)))


EPS_FEATURES = ("eps_latest_fy", "eps_ttm_approx", "earnings_yield_eps", "pe_eps")


def _guard(p, ids):
    """Valuation needs an ok panel (re-audit M-d) in LKR, the price currency (§4.4; re-audit B-3)."""
    from .features import PANEL_REASONS
    if p.state != pn.OK:
        st = pn.CONFLICTING if p.state == pn.CRITICAL_CONFLICT else pn.MISSING
        return {f: FeatureValue(f, st, reason=PANEL_REASONS.get(p.state, p.state)) for f in ids}
    if p.currency != "LKR":
        return {f: FeatureValue(f, pn.MISSING, reason="currency_mismatch") for f in ids}
    return None


def eps_features(p, row, max_stale=5):
    """eps_latest_fy, eps_ttm_approx, earnings_yield_eps and pe_eps for one security (§6.2, §6.3)."""
    blocked = _guard(p, EPS_FEATURES)
    if blocked:
        return blocked
    out = {}
    end, u = eps_latest_fy_fact(p)
    if end is None:
        eps = Derived(pn.MISSING, reason="absent")
    elif end < months_before(p.anchor_period, 12):
        eps = Derived(pn.MISSING, reason="fy_eps_too_old")
    else:
        eps = restate(u, row)
        if end < p.anchor_period and eps.state in (pn.FRESH, PARTIAL):
            eps = Derived(eps.state, eps.value, rule=eps.rule, inputs=eps.inputs, reason=eps.reason,
                          flags=eps.flags + ("earlier_period_than_anchor",))
    out["eps_latest_fy"] = _fv("eps_latest_fy", eps)
    out["eps_ttm_approx"] = _fv("eps_ttm_approx", eps_ttm_approx(p, row), "sum_of_per_share")
    price = _price(row)
    ey = ratio(_as_fresh(eps), price, True) if eps.state == PARTIAL else ratio(eps, price, True)
    if eps.state == PARTIAL:
        ey = _partial(ey, "cum_price_stale")
    out["earnings_yield_eps"] = _fv("earnings_yield_eps", _staleness(ey, row, max_stale))
    eps_ok = eps.number or (eps.state == PARTIAL and eps.value is not None)
    if not eps_ok or not price.number:
        pe = ratio(eps if not eps_ok else _as_fresh(eps), price, True)      # the state order across both
    elif eps.value == 0:
        pe = Derived(pn.MISSING, reason="denominator_zero", inputs=eps.inputs)
    elif eps.value < 0:
        pe = Derived(pn.MISSING, reason="denominator_not_positive", inputs=eps.inputs)
    else:
        pe = ratio(price, _as_fresh(eps), True)
        if eps.state == PARTIAL:
            pe = _partial(pe, "cum_price_stale")
    out["pe_eps"] = _fv("pe_eps", _staleness(pe, row, max_stale))
    return out


def _as_fresh(d):
    return Derived(pn.FRESH, d.value, rule=d.rule, inputs=d.inputs, flags=d.flags) if d.value is not None else d


def _partial(d, reason):
    return Derived(PARTIAL, d.value, rule=d.rule, inputs=d.inputs, reason=reason, flags=d.flags) if d.number else d


class _Restated:
    """The panel with every eps_basic fact restated by its own document's A (for eps_ttm_approx), intervals scaled by
    the same factor (re-audit B-6). A partial component (cum_price_stale) is used and makes the result partial."""

    def __init__(self, p, row):
        self.p, self.row, self.partial = p, row, False

    def fact(self, concept, end, months=None, operations=pn.ANCHOR_OPERATIONS, maturity=pn.NOT_APPLICABLE):
        u = self.p.fact(concept, end, months, operations, maturity)
        if concept != "eps_basic" or u is None:
            return u
        d = restate(u, self.row)
        if d.state in (pn.FRESH, PARTIAL) and d.value is not None:
            self.partial = self.partial or d.state == PARTIAL
            return pn.Usable(u.ef_key, pn.FRESH, d.value, d.low, d.high)
        return pn.Usable(u.ef_key, d.state, reason=d.reason)


def eps_ttm_approx(p, row):
    """F7P-2 or F7P-3 of restated eps_basic only (each component restated by its own A before adding); a printed
    annual EPS is eps_latest_fy and is never an input here (re-audit B-6)."""
    r = _Restated(p, row)
    d = ttm(r, "eps_basic", p.anchor_period, rules=("F7P-2", "F7P-3"))
    if d.number and r.partial:
        d = Derived(PARTIAL, d.value, d.low, d.high, d.rule, d.inputs, "cum_price_stale", d.paths, d.flags)
    return d


def issuer_market_value(rows):
    """Sum of market_cap_reported over the issuer's equity lines linked at T on D*: missing (incomplete_lines) if any
    line lacks it; always untrusted with input_unverified while market_cap_reported is provisional (P4-D4)."""
    if not rows:
        return Derived(pn.MISSING, reason="incomplete_lines")
    if any(r.market_cap_reported is None for r in rows):
        return Derived(pn.MISSING, reason="incomplete_lines")
    return Derived(pn.UNTRUSTED, sum((r.market_cap_reported for r in rows), Decimal(0)), rule="sum_lines",
                   reason="input_unverified")


def mcap_features(p, rows, cfo_ttm, fcf_ttm):
    """The market-value features (§6.3); untrusted (input_unverified) while market_cap_reported is provisional."""
    blocked = _guard(p, MCAP_FEATURES)
    if blocked:
        return blocked
    mv = issuer_market_value(rows)
    out = {"issuer_market_value": _fv("issuer_market_value", mv)}
    e = p.anchor_period
    nums = {"earnings_yield_mcap": ttm(p, "profit_for_period", e),
            "book_to_market": _instant(p, "total_equity", e), "sales_to_market": ttm(p, "revenue", e),
            "cfo_yield": cfo_ttm, "fcf_yield": fcf_ttm, "dividend_yield_cash": ttm(p, "dividends_paid", e)}
    for fid, num in nums.items():
        num = num if num is not None else Derived(pn.MISSING, reason="absent")
        if mv.value is None:
            out[fid] = FeatureValue(fid, mv.state, reason=mv.reason)
            continue
        d = ratio(num, Derived(pn.FRESH, mv.value), True)
        out[fid] = FeatureValue(fid, pn.UNTRUSTED if d.number else d.state, None,
                                "input_unverified" if d.number else d.reason, ("ratio",), d.inputs)
    return out


def _instant(p, concept, e):
    from .periods import instant
    return instant(p, concept, e)
