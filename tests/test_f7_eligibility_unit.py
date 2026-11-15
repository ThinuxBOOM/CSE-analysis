"""Phase 4 F7-4: financial eligibility (worker/financial_features/eligibility.py; design §11). Each result. Pure."""
from datetime import date

from f7_factories import fact, result
from worker.financial_features import eligibility as el
from worker.financial_features import features as ft
from worker.financial_features import panel as pn

E, E1 = date(2024, 3, 31), date(2023, 3, 31)
OK = [fact("profit_for_period", E, 12, value="120"), fact("profit_for_period", E1, 12, value="100"),
      fact("revenue", E, 12, value="1200"), fact("revenue", E1, 12, value="1000"),
      fact("total_equity", E, None, value="800"), fact("total_assets", E, None, value="2000")]


def judge(facts, cutoff="2024-06-30T00:00:00+00:00"):
    p = pn.panel(result(facts, cutoff=cutoff), "live")
    return el.eligibility(p, ft.compute(p))


def test_e1_eligible_and_measured():
    e = judge(OK)
    assert (e.result, e.fiscal_years, e.anchor_age_days) == (el.ELIGIBLE, 2, 91)
    assert "profit_g_ttm" in e.growth_features and "roe_end" in e.return_features


def test_e2_each_failure():
    assert judge([fact("revenue", E, 12)]).result == el.NO_PANEL
    assert judge([fact(scope="unlabelled"), fact(end=E1, scope="group")]).result == el.SCOPE_UNAVAILABLE
    assert judge([fact(currency="USD"), fact(currency="EUR")]).result == el.CURRENCY_AMBIGUOUS
    assert judge([fact(nil=True)]).result == el.ANCHOR_UNUSABLE
    assert judge([fact(state="conflicting", value=None)]).result == el.CRITICAL_CONFLICT
    bad_equity = [f for f in OK if f.identity.concept_key != "total_equity"] + [
        fact("total_equity", E, None, state="conflicting", value=None)]
    assert judge(bad_equity).result == el.CRITICAL_CONFLICT
    one_year = [f for f in OK if f.identity.period_end != E1]
    e = judge(one_year)
    assert e.result == el.INSUFFICIENT_HISTORY and el.INSUFFICIENT_FEATURES in e.failed
    assert judge(OK, cutoff="2025-01-31T00:00:00+00:00").result == el.STALE                    # 306 days
    assert judge(OK, cutoff="2024-12-26T00:00:00+00:00").result == el.ELIGIBLE                 # 270 days
    no_returns = [f for f in OK if f.identity.concept_key not in ("total_equity", "total_assets")]
    assert judge(no_returns).result == el.INSUFFICIENT_FEATURES
