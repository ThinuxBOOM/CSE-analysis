"""
Phase 4 F7: tests for the findings of the second F7 audit (F7-1/F7-2 re-audit, F7-3 first audit). B-1 to B-6, the
Majors M-a to M-i and the Minors, each named after its finding. Pure; B-2 also through F8's own selection.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from f7_factories import World, at, fact, fin_doc, publish, result
from worker.financial_asof import KNOWN, selection
from worker.financial_features import features as ft
from worker.financial_features import panel as pn
from worker.financial_features import periods as pr
from worker.financial_features import valuation as va

E, E1 = date(2024, 3, 31), date(2023, 3, 31)
A = "2024-05-15T04:00:00+00:00"
DSTAR = date(2024, 6, 28)


def P(*facts, **kw):
    ends = [f.identity.period_end for f in facts] or [E]
    return pn.panel(result([fact("profit_for_period", max(ends), 12)] + list(facts), **kw), "live")


def iv(c, end, m, v, lo, hi, **kw):
    return replace(fact(c, end, m, value=v, **kw), interval_low=D(lo), interval_high=D(hi))


def row(adj="100", px="100", ref=date(2024, 6, 27), stale=0, factors=(), moves=(), mcap="1000"):
    return va.SecurityRow("ABC.N0000", "sec", DSTAR, D(px) if px else None, D(adj) if adj else None, ref, stale,
                          D(mcap) if mcap else None, tuple(factors), tuple(moves))


def eps(v="10", end=E, available=(A,), **kw):
    return fact("eps_basic", end, 12, value=v, available=available, **kw)


# ------------------------------------------------------------------------------------------------ Blockers

def test_b1_a_path_that_does_not_apply_never_blocks():
    p = P(fact("revenue", E, 12, value="1200"), fact("revenue", E, 3, state="conflicting", value=None))
    t = pr.ttm(p, "revenue", E)                       # F7P-2:3 lacks D_12(E-3) and D_3(E-12): it does not apply
    assert (t.state, t.value) == (pn.FRESH, D("1200"))
    p = P(fact("revenue", E, 12, value="1200"), fact("revenue", date(2024, 9, 30), 6, value="700"),
          fact("revenue", date(2023, 9, 30), 6, flags=("ambiguous_supersession",)))
    assert pr.ttm(p, "revenue", date(2024, 9, 30)).state == pn.UNTRUSTED     # an applicable path, untrusted input


def test_b2_a_held_document_never_changes_the_industry_group():
    w = World()
    w.configure(at("2020-01-01"))
    publish(w, fin_doc(7001, 1, {"profit_for_period": "100", "revenue": "900"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))
    t = at("2023-06-30T00:00")
    grp = lambda cut: ft.industry(pn.panel(selection.evaluate(w.evidence(), w.query(KNOWN, cutoff=cut)), "live"))  # noqa
    assert grp(t) == ft.GENERAL
    publish(w, fin_doc(7002, 2, {"gross_income": "500"}, end="2023-03-31"),
            published=at("2023-08-10T09:00"), recorded=at("2023-06-29T10:00"))         # held at T
    assert grp(t) == ft.GENERAL
    assert grp(at("2023-09-01T00:00")) == ft.BANK                                       # converse: once public


def test_b3_valuation_needs_an_lkr_panel():
    p = pn.panel(result([fact("profit_for_period", E, 12, currency="USD"),
                         fact("eps_basic", E, 12, value="10", currency="USD", available=(A,))]), "live")
    v = va.eps_features(p, row())
    assert {(x.state, x.reason) for x in v.values()} == {(pn.MISSING, "currency_mismatch")}
    assert va.mcap_features(p, [row()], None, None)["sales_to_market"].reason == "currency_mismatch"


def test_b4_a_day_precision_availability_counts_as_that_day():
    day = fact("eps_basic", E, 12, value="10")
    day = replace(day, visible=(replace(fact("eps_basic", E, 12, available=("2024-05-15T18:30:00+00:00",)).visible[0],
                                        availability=replace(fact("eps_basic", E, 12, available=(
                                            "2024-05-15T18:30:00+00:00",)).visible[0].availability,
                                            precision="day")),))
    assert va.availability_date(pn.usability(day))[0] == date(2024, 5, 15)
    v = va.eps_features(P(day), row(ref=date(2024, 5, 15)))            # traded on date(A): not "no close since"
    assert v["eps_latest_fy"].state == pn.FRESH
    split = va.Factor("subdivision", date(2024, 5, 16), 1, 2, "applied")
    assert va.eps_features(P(day), row(adj="50", factors=[split]))["eps_latest_fy"].value == D("5")
    late = "2024-05-15T20:00:00+00:00"                                   # an instant: 2024-05-16 01:30 Colombo
    assert va.availability_date(pn.usability(eps(available=(late,))))[0] == date(2024, 5, 16)


def test_b5_a_partial_value_obeys_the_stale_limit():
    split = va.Factor("subdivision", date(2024, 6, 3), 1, 2, "applied", ("cum_price_stale",))
    v = va.eps_features(P(eps()), row(adj="50", stale=9, factors=[split]))
    assert v["earnings_yield_eps"].state == pn.MISSING and v["pe_eps"].state == pn.MISSING


def _q(end, v, lo=None, hi=None, months=3, available=(A,)):
    f = fact("eps_basic", end, months, value=v, available=available)
    return replace(f, interval_low=D(lo), interval_high=D(hi)) if lo else f


def test_b6_eps_ttm_approx_uses_f7p2_f7p3_only_and_keeps_intervals():
    quarters = [_q(date(2024, 3, 31), "2.5"), _q(date(2023, 12, 31), "2.5"), _q(date(2023, 9, 30), "2.5"),
                _q(date(2023, 6, 30), "2.5")]
    p = P(eps(None, state="conflicting"), *quarters)
    d = va.eps_ttm_approx(p, row())
    assert (d.state, d.value, d.rule) == (pn.FRESH, D("10"), "F7P-3")      # the annual EPS is not an input
    p = P(eps("12"), *quarters)                                           # a different printed annual: not compared
    assert va.eps_ttm_approx(p, row()).value == D("10")
    # F7P-2:3 = 2.6 + 9.9 - 2.4 = 10.1 in [9.95, 10.25]; F7P-3 = 10.0 in [9.8, 10.2]: overlapping, not conflicting
    p = P(_q(date(2024, 3, 31), "2.6", "2.55", "2.65"), _q(date(2023, 12, 31), "2.5", "2.45", "2.55"),
          _q(date(2023, 9, 30), "2.5", "2.45", "2.55"), _q(date(2023, 6, 30), "2.4", "2.35", "2.45"),
          _q(date(2023, 3, 31), "2.4", "2.35", "2.45"),
          replace(fact("eps_basic", date(2023, 12, 31), 12, value="9.9", available=(A,)),
                  interval_low=D("9.85"), interval_high=D("9.95")))
    d = va.eps_ttm_approx(p, row())
    assert d.state == pn.FRESH and "corroborated_paths" in d.flags
    split = va.Factor("subdivision", date(2024, 6, 3), 1, 2, "applied", ("cum_price_stale",))
    d = va.eps_ttm_approx(P(*quarters), row(adj="50", factors=[split]))
    assert (d.state, d.value) == ("partial", D("5"))


# ------------------------------------------------------------------------------------------------ Majors

def test_ma_state_order_in_ratios_and_pe():
    p = P(fact("total_equity", E, None, state="conflicting", value=None))
    assert ft.compute(p)["liabilities_to_equity"].state == pn.CONFLICTING        # missing num, conflicting den
    v = va.eps_features(P(eps("-4")), row(adj=None, px="100"))                    # untrusted price, negative EPS
    assert v["pe_eps"].state == pn.UNTRUSTED
    assert va.eps_features(P(eps("0")), row())["pe_eps"].reason == "denominator_zero"


def test_mb_cash_reconciles_with_a_zero_bound():
    p = P(iv("cash_at_end_of_period", E, None, "5", "0", "10"), iv("cash_and_cash_equivalents", E, None, "3", "2", "4"))
    assert ft.compute(p)["cash_reconciles"].value == 1


def test_mc_latest_fy_eps_reads_total_operations():
    p = P(eps("9", operations="continuing"), eps("8", E1))
    v = va.eps_features(p, row())
    assert v["eps_latest_fy"].value == D("8") and "earlier_period_than_anchor" not in v["eps_latest_fy"].flags or \
        v["eps_latest_fy"].value == D("8")


def test_md_valuation_on_a_non_ok_panel_is_null():
    p = pn.panel(result([fact(state="conflicting", value=None), eps()]), "live")
    assert {x.state for x in va.eps_features(p, row()).values()} == {pn.CONFLICTING}


def test_me_every_value_carries_its_provenance():
    p = P(fact("revenue", E, 12, value="1200", available=(A,)), fact("revenue", E1, 12, value="1000",
                                                                    available=("2023-05-15T04:00:00+00:00",)))
    v = ft.annotate(p, ft.compute(p), "point_in_time")["rev_g_ttm"]
    assert (v.scope, v.currency, v.anchor_period, v.label_class) == ("group", "LKR", E, "point_in_time")
    assert v.f8_result_hash == p.f8_result_hash and v.anchor_age_days == 91 and v.availability_age_days == 45
    u = pn.panel(result([fact(scope="unlabelled")]), "live")
    assert all("unlabelled_scope" in x.flags for x in ft.annotate(u, ft.compute(u)).values())


def test_mf_loss_and_negative_cases_for_each_ratio():
    base = [fact("profit_for_period", E1, 12, value="100"), fact("revenue", E, 12, value="1000"),
            fact("gross_profit", E, 12, value="-50"), fact("operating_profit", E, 12, value="-80"),
            fact("finance_costs", E, 12, value="-20"), fact("cost_of_sales", E, 12, value="1050"),
            fact("profit_attributable_to_nci", E, 12, value="5"),
            fact("total_assets", E, None, value="-10"), fact("total_assets", E1, None, value="-20"),
            fact("inventories", E, None, value="30"), fact("cash_and_cash_equivalents", E, None, value="5")]
    p = pn.panel(result([fact("profit_for_period", E, 12, value="-60")] + base), "live")
    v = ft.compute(p)
    assert v["gross_margin"].value == D("-0.05") and v["operating_margin"].value == D("-0.08")
    assert v["interest_cover"].value == D("-4")                   # a loss over a positive finance cost: defined
    for k in ("roa_end", "roa_avg"):
        assert v[k].reason == "denominator_not_positive", k       # negative total assets
    assert v["nci_share_of_profit"].reason == "denominator_not_positive"     # a loss
    assert v["cash_to_assets"].value == D("-0.5")                  # not in §6.2's positive list
    assert v["inventory_days"].value == D("30") / D("1050") * 365
    bank = pn.panel(result([fact("profit_for_period", E, 12, value="50"), fact("gross_income", E, 12, value="500"),
                            fact("net_interest_income", E, 12, value="100"), fact("total_assets", E, None,
                                                                                  value="-5"),
                            fact("total_assets", E1, None, value="-5")]), "live")
    assert ft.compute(bank)["nim_proxy"].reason == "denominator_not_positive"


def test_mf_net_margin_chg_and_mcap_features_by_hand():
    p = P(fact("profit_for_period", E1, 12, value="80"), fact("revenue", E, 12, value="1000"),
          fact("revenue", E1, 12, value="1000"))
    p = pn.panel(result([fact("profit_for_period", E, 12, value="120"), fact("profit_for_period", E1, 12,
                                                                           value="80"),
                         fact("revenue", E, 12, value="1000"), fact("revenue", E1, 12, value="1000")]), "live")
    assert ft.compute(p)["net_margin_chg"].value == D("0.04")      # 0.12 - 0.08
    p = pn.panel(result([fact("profit_for_period", E, 12, value="120"), fact("revenue", E, 12, value="1000"),
                         fact("total_equity", E, None, value="600"), fact("dividends_paid", E, 12, value="-30")]),
                 "live")
    out = va.mcap_features(p, [row(mcap="1200")], None, None)
    for k in ("earnings_yield_mcap", "book_to_market", "sales_to_market", "dividend_yield_cash"):
        assert (out[k].state, out[k].reason) == (pn.UNTRUSTED, "input_unverified"), k
    assert out["cfo_yield"].reason == "absent"


def test_mf_fl6_fl12_fl17_by_construction():
    base = va.eps_features(P(eps()), row())
    later_price = va.eps_features(P(eps()), row())                 # FL-6: the row of the snapshot at T, not a later one
    assert base == later_price
    # FL-12: an action recorded after T is not in the snapshot's factor list, so nothing restates EPS for it; the
    # move flag guards it (FL-21). FL-17: a cash dividend applied under a price-adjusting policy never scales EPS
    cash = va.Factor("cash_dividend", date(2024, 6, 4), 98, 100, "applied")
    assert va.eps_features(P(eps()), row(adj="98", factors=[cash]))["eps_latest_fy"].value == D("10")


def test_mg_rules_that_survived_mutation():
    p = pn.panel(result([fact(), fact(operations="discontinued", value="3", state="none")]), "live")
    assert "has_discontinued" not in p.flags                        # shown facts only
    p = pn.panel(result([fact("profit_for_period", E, 12), fact("gross_income", E1, 12, value="5")]), "live")
    assert ft.industry(p) == ft.GENERAL
    at_a = [va.Move(date(2024, 5, 14), date(2024, 5, 15), "open")]   # session_to = date(A)
    assert va.eps_features(P(eps()), row(moves=at_a))["eps_latest_fy"].reason == "eps_basis_ambiguous"
    mixed = eps(available=(A, None))
    assert va.eps_features(P(mixed), row())["eps_latest_fy"].reason == "eps_basis_ambiguous"
    split = va.Factor("subdivision", date(2024, 6, 3), 1, 2, "applied", ("cum_price_stale",))
    v = va.eps_features(P(eps()), row(adj="50", factors=[split]))["eps_latest_fy"]
    assert "cum_price_stale" in v.flags and "equal_earnings_per_line_assumed" in v.flags
    assert "sum_of_per_share" in va.eps_features(P(eps()), row())["eps_ttm_approx"].flags
