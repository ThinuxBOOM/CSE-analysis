"""
Phase 4 F7-2: period arithmetic (worker/financial_features/periods.py; design §5). Hand-computed cases for 12-month,
half-year (6), nine-month (9) and quarter-only (3) reporters, March and December year-ends, a non-month-end period
end, disagreeing paths, the per-fact magnitude rule and the cash-flow sign identity (FL-20). Pure.
"""
from datetime import date
from decimal import Decimal as D

from f7_factories import fact, result
from worker.financial_features import panel as pn
from worker.financial_features import periods as pr


def P(*facts):
    """A panel over the given facts plus a profit anchor at the latest period end among them."""
    ends = [f.identity.period_end for f in facts]
    anchor = fact("profit_for_period", max(ends), 12)
    return pn.panel(result(list(facts) + [anchor]), "live")


def rev(end, months, v, **kw):
    return fact("revenue", end, months, value=v, **kw)


def test_t1_ttm_from_an_annual_figure():
    p = P(rev(date(2024, 3, 31), 12, "1200"))
    t = pr.ttm(p, "revenue", date(2024, 3, 31))
    assert (t.state, t.value, t.rule) == (pn.FRESH, D("1200"), "F7P-1")


def test_t2_half_year_reporter_march_year_end():
    # TTM(Sep-2024) = H1(Sep-2024) + FY(Mar-2024) - H1(Sep-2023) = 700 + 1200 - 600 = 1300
    p = P(rev(date(2024, 9, 30), 6, "700"), rev(date(2024, 3, 31), 12, "1200"), rev(date(2023, 9, 30), 6, "600"))
    t = pr.ttm(p, "revenue", date(2024, 9, 30))
    assert (t.value, t.rule, len(t.inputs)) == (D("1300"), "F7P-2:6", 3)


def test_t3_nine_month_reporter_december_year_end():
    # TTM(Sep-2024) = 9M(Sep-2024) + FY(Dec-2023) - 9M(Sep-2023) = 950 + 1200 - 900 = 1250
    p = P(rev(date(2024, 9, 30), 9, "950"), rev(date(2023, 12, 31), 12, "1200"), rev(date(2023, 9, 30), 9, "900"))
    assert pr.ttm(p, "revenue", date(2024, 9, 30)).value == D("1250")


def test_t4_quarter_only_reporter_and_corroborated_paths():
    q = [rev(date(2024, 6, 30), 3, "310"), rev(date(2024, 3, 31), 3, "300"), rev(date(2023, 12, 31), 3, "290"),
         rev(date(2023, 9, 30), 3, "280")]
    t = pr.ttm(P(*q), "revenue", date(2024, 6, 30))
    assert (t.value, t.rule) == (D("1180"), "F7P-3")
    # with an annual figure at Mar-2024 and the quarter a year earlier, F7P-2:3 also applies: 310 + 1150 - 280 = 1180
    q2 = q + [rev(date(2024, 3, 31), 12, "1150"), rev(date(2023, 6, 30), 3, "280")]
    t = pr.ttm(P(*q2), "revenue", date(2024, 6, 30))
    assert (t.value, t.rule, "corroborated_paths" in t.flags) == (D("1180"), "F7P-2:3", True)


def test_t5_paths_that_disagree_are_conflicting():
    q = [rev(date(2024, 6, 30), 3, "310"), rev(date(2024, 3, 31), 3, "300"), rev(date(2023, 12, 31), 3, "290"),
         rev(date(2023, 9, 30), 3, "280"), rev(date(2024, 3, 31), 12, "1500"), rev(date(2023, 6, 30), 3, "280")]
    t = pr.ttm(P(*q), "revenue", date(2024, 6, 30))
    assert (t.state, t.reason, sum(p.number for p in t.paths)) == (pn.CONFLICTING, "paths_disagree", 2)


def test_t6_missing_and_non_month_end():
    assert pr.ttm(P(rev(date(2024, 9, 30), 6, "700")), "revenue", date(2024, 9, 30)).state == pn.MISSING
    end = date(2024, 9, 29)                                    # not a month-end, after the 28th
    assert pr.ttm(P(rev(end, 12, "1")), "revenue", end).reason == "no_exact_period_match"
    end = date(2024, 9, 25)
    p = P(rev(end, 6, "700"), rev(date(2024, 3, 25), 12, "1200"), rev(date(2023, 9, 25), 6, "600"))
    assert pr.ttm(p, "revenue", end).value == D("1300")          # the 25th maps to the 25th


def test_t7_a_conflicting_input_makes_its_path_conflicting():
    p = P(rev(date(2024, 9, 30), 6, None, state="conflicting"), rev(date(2024, 3, 31), 12, "1200"),
          rev(date(2023, 9, 30), 6, "600"))
    t = pr.ttm(p, "revenue", date(2024, 9, 30))
    assert t.state == pn.CONFLICTING


def test_t8_magnitude_per_fact_before_arithmetic():
    # cost_of_sales printed with different signs in different documents: magnitudes are taken per fact first
    def cos(end, m, v):
        return fact("cost_of_sales", end, m, value=v)
    p = P(cos(date(2024, 9, 30), 6, "-400"), cos(date(2024, 3, 31), 12, "800"), cos(date(2023, 9, 30), 6, "-350"))
    t = pr.ttm(p, "cost_of_sales", date(2024, 9, 30))
    assert t.value == D("850") and any(f.startswith("sign_rule:magnitude") for f in t.flags)


def test_g1_growth_and_averages():
    p = P(fact("total_equity", date(2024, 3, 31), None, value="120"), fact("total_equity", date(2023, 3, 31), None,
                                                                          value="100"))
    assert pr.avg_instant(p, "total_equity", date(2024, 3, 31)).value == D("110")
    assert pr.growth_instant(p, "total_equity", date(2024, 3, 31)).value == D("0.2")
    p = P(rev(date(2024, 3, 31), 12, "1200"), rev(date(2023, 3, 31), 12, "1000"))
    assert pr.growth_ttm(p, "revenue", date(2024, 3, 31)).value == D("0.2")
    assert pr.growth_cum(p, "revenue", date(2024, 3, 31), 12).value == D("0.2")
    p = P(rev(date(2024, 3, 31), 12, "1200"), rev(date(2023, 3, 31), 12, "-5"))
    assert pr.growth_ttm(p, "revenue", date(2024, 3, 31)).reason == "base_not_positive"


def _cash(end, months, op, inv, fin, opening, closing):
    return [fact("net_cash_from_operating_activities", end, months, value=op),
            fact("net_cash_from_investing_activities", end, months, value=inv),
            fact("net_cash_from_financing_activities", end, months, value=fin),
            fact("cash_at_end_of_period", pr.months_before(end, months), None, value=opening),
            fact("cash_at_end_of_period", end, None, value=closing)]


def test_c1_the_cash_flow_identity_confirms_or_nulls_fl20():
    e = date(2024, 3, 31)
    ok = pr.cashflow_confirmed(P(*_cash(e, 12, "500", "-200", "-100", "1000", "1200")), e, 12)
    assert all(d.number and "sign_confirmed" in d.flags for d in ok.values())
    # FL-20: an investing outflow printed without parentheses (+200 instead of -200) does not balance
    bad = pr.cashflow_confirmed(P(*_cash(e, 12, "500", "200", "-100", "1000", "1200")), e, 12)
    assert {(d.state, d.reason) for d in bad.values()} == {(pn.MISSING, "sign_unconfirmed")}
    # no opening cash at E-12 (not a mapped concept elsewhere): unconfirmable
    none = pr.cashflow_confirmed(P(*_cash(e, 12, "500", "-200", "-100", "1000", "1200")[:3],
                                   fact("cash_at_end_of_period", e, None, value="1200")), e, 12)
    assert {d.reason for d in none.values()} == {"sign_unconfirmed"}
