"""
F7-3 (part 1): the feature registry and the fundamental features of set v1 (Phase 4 design §6.1, §6.2, §6.4). Pure.

Each feature has one definition and no fallback; it returns a FeatureValue with its state, reason, rules and the
ef_keys it used. The EPS and valuation features (§6.3) are in valuation.py.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from . import panel as pn
from .dates import months_before
from .periods import (Derived, avg_instant, duration, growth_cum, growth_instant, growth_ttm, instant, total, ttm,
                      cashflow_confirmed)

REGISTRY_VERSION = "f7.features.1"
INDUSTRY_POLICY = "f7.industry.1"
GENERAL, BANK = "general", "bank_finance"
DAYS = Decimal(365)


@dataclass(frozen=True)
class FeatureValue:
    feature_id: str
    state: str
    value: Optional[Decimal] = None
    reason: Optional[str] = None
    rules: tuple = ()
    inputs: tuple = ()                   # ef_keys
    flags: tuple = ()


@dataclass(frozen=True)
class Spec:
    feature_id: str
    family: str
    industries: str                      # all | general | bank_finance
    positive_denominator: bool
    compute: object                      # (panel, anchor) -> Derived or FeatureValue


def industry(p):
    """f7.industry.1: bank_finance if the panel has, at the anchor period in the chosen scope, a fact (in any state) of
    a concept whose industries is bank_finance; otherwise general."""
    from worker import financial_concepts as fc
    for (concept, end, _m, _o, _mat) in p.facts:
        if end == p.anchor_period and fc.BY_KEY.get(concept) is not None and fc.BY_KEY[concept].industries == BANK:
            return BANK
    return GENERAL


def _fv(fid, d, *more_flags):
    if isinstance(d, FeatureValue):
        return d
    rules = tuple(x for x in (d.rule,) if x) + tuple(p.rule for p in d.paths if p.rule and p.rule != d.rule)
    return FeatureValue(fid, d.state, d.value if d.number else None, d.reason, rules, d.inputs,
                        tuple(dict.fromkeys(d.flags + more_flags)))


def ratio(num, den, positive):
    """num / den. A null input gives its state; a zero or nil denominator `missing`, reason denominator_zero; a
    non-positive one, where the registry requires a positive denominator, `missing`, reason denominator_not_positive."""
    for x in (num, den):
        if not x.number:
            return Derived(x.state, reason=x.reason, inputs=num.inputs + den.inputs)
    if den.value == 0:
        return Derived(pn.MISSING, reason="denominator_zero", inputs=num.inputs + den.inputs)
    if positive and den.value < 0:
        return Derived(pn.MISSING, reason="denominator_not_positive", inputs=num.inputs + den.inputs)
    return Derived(pn.FRESH, num.value / den.value, rule="ratio", inputs=num.inputs + den.inputs,
                   flags=num.flags + den.flags)


def _scale(d, k):
    return d if not d.number else Derived(d.state, d.value * k, rule=d.rule, inputs=d.inputs, flags=d.flags)


def _borrowings(p, e):
    return total("borrowings", [instant(p, "interest_bearing_borrowings", e, "current"),
                                instant(p, "interest_bearing_borrowings", e, "non_current")], [1, 1])


def _cfo_ttm(p, e):
    """TTM operating cash flow, each of its period facts confirmed by f7.cashflow_sign.1: the TTM paths read
    confirmed totals only."""
    conf = {}

    class Confirmed:
        """The panel as seen through the cash-flow sign identity: an unconfirmed total is missing."""
        def __getattr__(self, name):
            return getattr(p, name)

        def fact(self, concept, end, months=None, operations=pn.ANCHOR_OPERATIONS, maturity=pn.NOT_APPLICABLE):
            u = p.fact(concept, end, months, operations, maturity)
            if concept.startswith("net_cash_from_") and u is not None and u.number:
                key = (end, months)
                if key not in conf:
                    conf[key] = cashflow_confirmed(p, end, months)
                c = conf[key][concept]
                if not c.number:
                    return pn.Usable(u.ef_key, pn.MISSING, reason=c.reason)
            return u
    return ttm(Confirmed(), "net_cash_from_operating_activities", e)


def _margin_at(p, e, bank):
    den = ttm(p, "gross_income" if bank else "revenue", e)
    return ratio(ttm(p, "profit_for_period", e), den, True)


def _net_margin_chg(p, e):
    bank = industry(p) == BANK
    now, before = _margin_at(p, e, bank), _margin_at(p, months_before(e, 12), bank)
    return total("net_margin_chg", [now, before], [1, -1])


def _fcf(p, e):
    return total("fcf_ttm", [_cfo_ttm(p, e), ttm(p, "purchase_of_ppe", e)], [1, -1])


def _payout(p, e):
    d = ratio(ttm(p, "dividends_paid", e), ttm(p, "profit_for_period", e), True)
    return d if not d.number else Derived(d.state, d.value, rule=d.rule, inputs=d.inputs,
                                          flags=d.flags + (("dividends_may_include_nci",) if p.scope == "group"
                                                           else ()))


def _identity_gap(p, e):
    a = instant(p, "total_assets", e)
    gap = total("identity_gap", [a, instant(p, "total_liabilities", e), instant(p, "total_equity", e)], [1, -1, -1])
    return ratio(gap, a, True)


def _cash_reconciles(p, e):
    x, y = instant(p, "cash_at_end_of_period", e), instant(p, "cash_and_cash_equivalents", e)
    if not (x.number and y.number):
        return Derived(x.state if not x.number else y.state, reason=x.reason or y.reason, inputs=x.inputs + y.inputs)
    agree = (x.low or x.value) <= (y.high or y.value) and (y.low or y.value) <= (x.high or x.value)
    return Derived(pn.FRESH, Decimal(1 if agree else 0), rule="cash_reconciles", inputs=x.inputs + y.inputs,
                   flags=("information_only",))


def _n(p):
    return p.anchor_months


SPECS = [
    Spec("rev_g_cum", "growth", GENERAL, False, lambda p, e: growth_cum(p, "revenue", e, _n(p))),
    Spec("rev_g_ttm", "growth", GENERAL, False, lambda p, e: growth_ttm(p, "revenue", e)),
    Spec("gross_income_g_cum", "growth", BANK, False, lambda p, e: growth_cum(p, "gross_income", e, _n(p))),
    Spec("gross_income_g_ttm", "growth", BANK, False, lambda p, e: growth_ttm(p, "gross_income", e)),
    Spec("nii_g_cum", "growth", BANK, False, lambda p, e: growth_cum(p, "net_interest_income", e, _n(p))),
    Spec("profit_g_cum", "growth", "all", False, lambda p, e: growth_cum(p, "profit_for_period", e, _n(p))),
    Spec("profit_g_ttm", "growth", "all", False, lambda p, e: growth_ttm(p, "profit_for_period", e)),
    Spec("profit_owners_g_ttm", "growth", "all", False,
         lambda p, e: growth_ttm(p, "profit_attributable_to_owners", e)),
    Spec("assets_g", "growth", "all", False, lambda p, e: growth_instant(p, "total_assets", e)),
    Spec("equity_g", "growth", "all", False, lambda p, e: growth_instant(p, "total_equity", e)),
    Spec("loans_g", "growth", BANK, False, lambda p, e: growth_instant(p, "loans_and_advances_to_customers", e)),
    Spec("deposits_g", "growth", BANK, False, lambda p, e: growth_instant(p, "customer_deposits", e)),
    Spec("gross_margin", "margins", GENERAL, True,
         lambda p, e: ratio(ttm(p, "gross_profit", e), ttm(p, "revenue", e), True)),
    Spec("operating_margin", "margins", GENERAL, True,
         lambda p, e: ratio(ttm(p, "operating_profit", e), ttm(p, "revenue", e), True)),
    Spec("net_margin", "margins", GENERAL, True, lambda p, e: _margin_at(p, e, False)),
    Spec("net_margin_bank", "margins", BANK, True, lambda p, e: _margin_at(p, e, True)),
    Spec("net_margin_chg", "margins", "all", False, _net_margin_chg),
    Spec("nim_proxy", "margins", BANK, True,
         lambda p, e: ratio(ttm(p, "net_interest_income", e), avg_instant(p, "total_assets", e), True)),
    Spec("roe_end", "returns", "all", True,
         lambda p, e: ratio(ttm(p, "profit_for_period", e), instant(p, "total_equity", e), True)),
    Spec("roa_end", "returns", "all", True,
         lambda p, e: ratio(ttm(p, "profit_for_period", e), instant(p, "total_assets", e), True)),
    Spec("roe_avg", "returns", "all", True,
         lambda p, e: ratio(ttm(p, "profit_for_period", e), avg_instant(p, "total_equity", e), True)),
    Spec("roa_avg", "returns", "all", True,
         lambda p, e: ratio(ttm(p, "profit_for_period", e), avg_instant(p, "total_assets", e), True)),
    Spec("roe_owners_end", "returns", "all", True,
         lambda p, e: ratio(ttm(p, "profit_attributable_to_owners", e),
                            instant(p, "equity_attributable_to_owners", e), True)),
    Spec("liabilities_to_equity", "leverage", "all", True,
         lambda p, e: ratio(instant(p, "total_liabilities", e), instant(p, "total_equity", e), True)),
    Spec("equity_to_assets", "leverage", "all", True,
         lambda p, e: ratio(instant(p, "total_equity", e), instant(p, "total_assets", e), True)),
    Spec("borrowings_to_equity", "leverage", GENERAL, True,
         lambda p, e: ratio(_borrowings(p, e), instant(p, "total_equity", e), True)),
    Spec("interest_cover", "leverage", GENERAL, True,
         lambda p, e: ratio(ttm(p, "operating_profit", e), ttm(p, "finance_costs", e), True)),
    Spec("loans_to_deposits", "leverage", BANK, True,
         lambda p, e: ratio(instant(p, "loans_and_advances_to_customers", e), instant(p, "customer_deposits", e),
                            True)),
    Spec("cash_to_assets", "liquidity", "all", True,
         lambda p, e: ratio(instant(p, "cash_and_cash_equivalents", e), instant(p, "total_assets", e), True)),
    Spec("net_debt", "liquidity", GENERAL, False,
         lambda p, e: total("net_debt", [_borrowings(p, e), instant(p, "cash_and_cash_equivalents", e)], [1, -1])),
    Spec("cfo_to_profit", "cash_flow", GENERAL, True,
         lambda p, e: ratio(_cfo_ttm(p, e), ttm(p, "profit_for_period", e), True)),
    Spec("fcf_ttm", "cash_flow", GENERAL, False, _fcf),
    Spec("fcf_margin", "cash_flow", GENERAL, True, lambda p, e: ratio(_fcf(p, e), ttm(p, "revenue", e), True)),
    Spec("receivables_days", "working_capital", GENERAL, True,
         lambda p, e: _scale(ratio(instant(p, "trade_and_other_receivables", e), ttm(p, "revenue", e), True), DAYS)),
    Spec("inventory_days", "working_capital", GENERAL, True,
         lambda p, e: _scale(ratio(instant(p, "inventories", e), ttm(p, "cost_of_sales", e), True), DAYS)),
    Spec("payout_ratio_cash", "dividends", "all", True, _payout),
    Spec("accounting_identity_gap", "data_checks", "all", False, _identity_gap),
    Spec("cash_reconciles", "data_checks", "all", False, _cash_reconciles),
    Spec("nci_share_of_profit", "data_checks", "all", True,
         lambda p, e: ratio(ttm(p, "profit_attributable_to_nci", e), ttm(p, "profit_for_period", e), True)),
]
BY_ID = {s.feature_id: s for s in SPECS}
PANEL_REASONS = {pn.ANCHOR_ABSENT: "absent", pn.SCOPE_UNAVAILABLE: "scope_unavailable",
                 pn.CURRENCY_AMBIGUOUS: "currency_ambiguous", pn.CRITICAL_CONFLICT: "critical_conflict",
                 pn.ANCHOR_UNUSABLE: "anchor_unusable"}
YOY = ("rev_g_cum", "rev_g_ttm", "gross_income_g_cum", "gross_income_g_ttm", "nii_g_cum", "profit_g_cum",
       "profit_g_ttm", "profit_owners_g_ttm", "assets_g", "equity_g", "loans_g", "deposits_g", "net_margin_chg")
# §4.3: features that divide a total-operations profit by revenue (usually continuing-only) are partial when the
# anchor period also has discontinued facts
DIVIDED_BY_REVENUE = ("net_margin", "net_margin_bank", "net_margin_chg")


def compute(p):
    """Every fundamental feature of set v1 at the panel's anchor: {feature_id: FeatureValue}."""
    group = industry(p) if p.state == pn.OK else None
    out = {}
    for s in SPECS:
        if p.state != pn.OK:
            st = pn.CONFLICTING if p.state == pn.CRITICAL_CONFLICT else pn.MISSING
            out[s.feature_id] = FeatureValue(s.feature_id, st, reason=PANEL_REASONS.get(p.state, p.state))
            continue
        if s.industries not in ("all", group):
            out[s.feature_id] = FeatureValue(s.feature_id, pn.MISSING, reason="not_applicable_industry")
            continue
        if s.feature_id in YOY and "scope_changed" in p.flags:
            out[s.feature_id] = FeatureValue(s.feature_id, pn.MISSING, reason="scope_changed")
            continue
        v = _fv(s.feature_id, s.compute(p, p.anchor_period))
        if s.feature_id in DIVIDED_BY_REVENUE and "has_discontinued" in p.flags and v.state == pn.FRESH:
            v = FeatureValue(v.feature_id, "partial", v.value, "has_discontinued", v.rules, v.inputs, v.flags)
        out[s.feature_id] = v
    return out
