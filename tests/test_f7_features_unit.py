"""
Phase 4 F7-3 (part 1): the fundamental features (worker/financial_features/features.py; design §6.1, §6.2). Every
formula against a hand-computed vector, with negative-equity and loss-making cases for the ratios, the industry
group, denominators, scope and discontinued rules. Pure.
"""
from datetime import date
from decimal import Decimal as D

from f7_factories import fact, result
from worker.financial_features import features as ft
from worker.financial_features import panel as pn

E, E1 = date(2024, 3, 31), date(2023, 3, 31)


def dur(c, v, end=E):
    return fact(c, end, 12, value=v)


def ins(c, v, end=E, **kw):
    return fact(c, end, None, value=v, **kw)


BASE = [dur("profit_for_period", "120"), dur("profit_for_period", "100", E1), dur("revenue", "1200"),
        dur("revenue", "1000", E1), dur("gross_profit", "480"), dur("operating_profit", "200"),
        dur("finance_costs", "-40"), dur("cost_of_sales", "720"), dur("purchase_of_ppe", "-50"),
        dur("dividends_paid", "-30"), dur("profit_attributable_to_owners", "110"),
        dur("profit_attributable_to_nci", "10"),
        ins("total_assets", "2000"), ins("total_assets", "1800", E1), ins("total_equity", "800"),
        ins("total_equity", "700", E1), ins("total_liabilities", "1200"), ins("cash_and_cash_equivalents", "100"),
        ins("trade_and_other_receivables", "150"), ins("inventories", "90"),
        ins("equity_attributable_to_owners", "750"),
        ins("interest_bearing_borrowings", "300", maturity="current"),
        ins("interest_bearing_borrowings", "200", maturity="non_current"),
        dur("net_cash_from_operating_activities", "180"), dur("net_cash_from_investing_activities", "-60"),
        dur("net_cash_from_financing_activities", "-70"), ins("cash_at_end_of_period", "100"),
        ins("cash_at_end_of_period", "50", E1)]


def compute(facts):
    return ft.compute(pn.panel(result(facts), "live"))


def test_f1_hand_computed_vector_general():
    v = compute(BASE)
    expect = {"rev_g_cum": D("0.2"), "rev_g_ttm": D("0.2"), "profit_g_ttm": D("0.2"), "assets_g": D("1800") and
              D("2000") / D("1800") - 1, "equity_g": D("800") / D("700") - 1,
              "gross_margin": D("0.4"), "operating_margin": D("200") / D("1200"), "net_margin": D("0.1"),
              "roe_end": D("0.15"), "roa_end": D("0.06"), "roe_avg": D("120") / D("750"),
              "roa_avg": D("120") / D("1900"), "roe_owners_end": D("110") / D("750"),
              "liabilities_to_equity": D("1.5"), "equity_to_assets": D("0.4"), "borrowings_to_equity": D("0.625"),
              "interest_cover": D("5"), "cash_to_assets": D("0.05"), "net_debt": D("400"),
              "cfo_to_profit": D("1.5"), "fcf_ttm": D("130"), "fcf_margin": D("130") / D("1200"),
              "receivables_days": D("150") / D("1200") * 365, "inventory_days": D("90") / D("720") * 365,
              "payout_ratio_cash": D("0.25"), "accounting_identity_gap": D("0"),
              "nci_share_of_profit": D("10") / D("120")}
    for k, x in expect.items():
        assert (v[k].state, v[k].value) == (pn.FRESH, x), (k, v[k])
    assert "dividends_may_include_nci" in v["payout_ratio_cash"].flags
    assert v["cash_reconciles"].value == 1 and "information_only" in v["cash_reconciles"].flags
    assert v["net_margin_bank"].reason == "not_applicable_industry"       # a general issuer


def test_f2_negative_equity_and_losses():
    neg = [f for f in BASE if f.identity.concept_key != "total_equity"] + [ins("total_equity", "-50"),
                                                                            ins("total_equity", "-40", E1)]
    v = compute(neg)
    for k in ("roe_end", "roe_avg", "liabilities_to_equity", "borrowings_to_equity"):
        assert (v[k].state, v[k].reason) == (pn.MISSING, "denominator_not_positive"), k
    assert v["equity_to_assets"].value == D("-0.025")               # negative: ranks last, as it should
    loss = [f for f in BASE if f.identity.concept_key != "profit_for_period"] + [dur("profit_for_period", "-60"),
                                                                                dur("profit_for_period", "100", E1)]
    v = compute(loss)
    assert v["net_margin"].value == D("-0.05") and v["roe_end"].value == D("-0.075")
    assert v["cfo_to_profit"].reason == "denominator_not_positive"
    assert v["payout_ratio_cash"].reason == "denominator_not_positive"
    assert v["profit_g_ttm"].value == D("-1.6")
    base_loss = [f for f in BASE if f.identity.concept_key != "profit_for_period"] + [
        dur("profit_for_period", "120"), dur("profit_for_period", "-10", E1)]
    assert compute(base_loss)["profit_g_ttm"].reason == "base_not_positive"


def test_f3_zero_denominator_and_missing_inputs():
    z = [f for f in BASE if f.identity.concept_key != "finance_costs"] + [dur("finance_costs", "0")]
    assert compute(z)["interest_cover"].reason == "denominator_zero"
    one = [f for f in BASE if not (f.identity.concept_key == "interest_bearing_borrowings"
                                   and f.identity.maturity == "non_current")]
    assert compute(one)["borrowings_to_equity"].state == pn.MISSING         # both maturities required


def test_f4_bank_industry_group():
    bank = [dur("profit_for_period", "50"), dur("profit_for_period", "40", E1), dur("gross_income", "500"),
            dur("gross_income", "400", E1), dur("net_interest_income", "200"), ins("total_assets", "4000"),
            ins("total_assets", "3600", E1), ins("total_equity", "400"), ins("loans_and_advances_to_customers", "2500"),
            ins("loans_and_advances_to_customers", "2000", E1), ins("customer_deposits", "3000")]
    p = pn.panel(result(bank), "live")
    assert ft.industry(p) == ft.BANK
    v = ft.compute(p)
    assert v["net_margin_bank"].value == D("0.1") and v["gross_income_g_cum"].value == D("0.25")
    assert v["nim_proxy"].value == D("200") / D("3800") and v["loans_g"].value == D("0.25")
    assert v["loans_to_deposits"].value == D("2500") / D("3000")
    assert v["rev_g_cum"].reason == "not_applicable_industry"


def test_f5_scope_changed_and_discontinued_and_panel_states():
    changed = BASE + [fact("profit_for_period", E1, 12, scope="company", value="1")]
    changed = [f for f in changed if not (f.identity.scope == "group" and f.identity.period_end == E1
                                          and f.identity.concept_key == "profit_for_period")]
    v = compute(changed)
    assert v["profit_g_ttm"].reason == "scope_changed" and v["roe_end"].state == pn.FRESH
    v = compute(BASE + [fact("profit_for_period", E, 12, operations="discontinued", value="5")])
    assert (v["net_margin"].state, v["net_margin"].reason) == ("partial", "has_discontinued")
    assert v["gross_margin"].state == pn.FRESH
    v = compute([fact(state="conflicting", value=None)])
    assert {x.state for x in v.values()} == {pn.CONFLICTING}


def test_f6_unconfirmed_cash_flow_nulls_its_features():
    bad = [f for f in BASE if f.identity.concept_key != "net_cash_from_investing_activities"] + [
        dur("net_cash_from_investing_activities", "60")]                  # an outflow printed positive (FL-20)
    v = compute(bad)
    for k in ("cfo_to_profit", "fcf_ttm", "fcf_margin"):
        assert v[k].state == pn.MISSING and v[k].reason == "sign_unconfirmed", k
