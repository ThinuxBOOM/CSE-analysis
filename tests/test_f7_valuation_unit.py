"""
Phase 4 F7-3 (part 2): EPS restatement and the valuation features (worker/financial_features/valuation.py; design
§6.2, §6.3). Hand-computed values for FL-14, FL-16, FL-17, FL-18, FL-21 and FL-22, and the market-value rules. Pure.
"""
from datetime import date
from decimal import Decimal as D

from f7_factories import fact, result
from worker.financial_features import panel as pn
from worker.financial_features import valuation as va

E = date(2024, 3, 31)
A = "2024-05-15T04:00:00+00:00"                      # the EPS document's availability: 2024-05-15 Colombo
DSTAR = date(2024, 6, 28)


def eps(v="10", end=E, available=(A,), **kw):
    return fact("eps_basic", end, 12, value=v, available=available, **kw)


def P(*facts):
    return pn.panel(result([fact("profit_for_period", E, 12)] + list(facts)), "live")


def row(adj="100", px="100", ref=date(2024, 6, 27), stale=0, factors=(), moves=(), mcap="1000"):
    return va.SecurityRow("ABC.N0000", "sec", DSTAR, D(px) if px else None, D(adj) if adj else None, ref, stale,
                          D(mcap) if mcap else None, tuple(factors), tuple(moves))


def split(ex, num=1, den=2, status="applied", flags=()):
    return va.Factor("subdivision", ex, num, den, status, tuple(flags))


def test_v1_no_action_plain_yield():
    v = va.eps_features(P(eps()), row())
    assert v["eps_latest_fy"].value == D("10") and v["earnings_yield_eps"].value == D("0.1")
    assert v["pe_eps"].value == D("10")


def test_v2_fl14_split_after_availability_is_restated():
    v = va.eps_features(P(eps()), row(adj="50", factors=[split(date(2024, 6, 3))]))
    assert v["eps_latest_fy"].value == D("5") and v["earnings_yield_eps"].value == D("0.1")      # hand-computed
    # the ex-date between the period end and the availability: untrusted
    v = va.eps_features(P(eps()), row(factors=[split(date(2024, 4, 30))]))
    assert (v["eps_latest_fy"].state, v["eps_latest_fy"].reason) == (pn.UNTRUSTED, "eps_basis_ambiguous")


def test_v3_fl16_split_with_no_trade_since():
    # the ex-date between the last trade and D*: adj_ref is already on D*'s basis (50), px_ref still 100
    before = va.eps_features(P(eps()), row(adj="50", px="100", ref=date(2024, 6, 20), factors=[split(date(2024, 6, 25))]))
    after = va.eps_features(P(eps()), row(adj="50", px="50", ref=date(2024, 6, 27), factors=[split(date(2024, 6, 25))]))
    assert before["earnings_yield_eps"].value == after["earnings_yield_eps"].value == D("0.1")


def test_v4_fl17_rights_applied_cash_dividend_not():
    rights = va.Factor("rights", date(2024, 6, 3), 9, 10, "applied")
    cash = va.Factor("cash_dividend", date(2024, 6, 4), 98, 100, "no_price_factor")
    v = va.eps_features(P(eps()), row(adj="90", factors=[rights, cash]))
    assert v["eps_latest_fy"].value == D("9")


def test_v5_fl18_straddling_documents_and_unknown_availability():
    two = eps(available=("2024-05-15T04:00:00+00:00", "2024-07-01T04:00:00+00:00"))
    v = va.eps_features(P(two), row(factors=[split(date(2024, 6, 3))]))
    assert v["eps_latest_fy"].reason == "eps_basis_ambiguous"
    v = va.eps_features(P(eps(available=(None,))), row())
    assert v["eps_latest_fy"].reason == "eps_basis_ambiguous"
    v = va.eps_features(P(eps(avail_flags=("availability_sources_disagree",))), row())
    assert v["eps_latest_fy"].reason == "eps_basis_ambiguous"


def test_v6_fl21_unentered_actions():
    after_a = [va.Move(date(2024, 6, 2), date(2024, 6, 3), "open")]
    v = va.eps_features(P(eps()), row(moves=after_a))
    assert (v["eps_latest_fy"].state, v["eps_latest_fy"].reason) == (pn.UNTRUSTED, "unexplained_move_since_document")
    before_a = [va.Move(date(2024, 4, 29), date(2024, 4, 30), "open")]
    assert va.eps_features(P(eps()), row(moves=before_a))["eps_latest_fy"].reason == "eps_basis_ambiguous"
    cleared = [va.Move(date(2024, 6, 2), date(2024, 6, 3), "confirmed_no_action")]
    assert va.eps_features(P(eps()), row(moves=cleared))["eps_latest_fy"].value == D("10")      # (c)
    # (d) no trade since before the document: untrusted whether or not the action is entered
    v = va.eps_features(P(eps()), row(ref=date(2024, 5, 10)))
    assert v["earnings_yield_eps"].reason == "no_close_since_document"
    v = va.eps_features(P(eps()), row(ref=date(2024, 5, 10), factors=[split(date(2024, 4, 30))]))
    assert v["earnings_yield_eps"].state == pn.UNTRUSTED


def test_v7_fl22_latest_fy_rules():
    v = va.eps_features(P(eps(None, state="conflicting"), eps("8", date(2023, 3, 31))), row())
    assert v["eps_latest_fy"].state == pn.CONFLICTING               # never the older year's value
    old = pn.panel(result([fact("profit_for_period", date(2024, 9, 30), 6), eps("8", date(2023, 3, 31))]), "live")
    assert va.eps_features(old, row())["eps_latest_fy"].reason == "fy_eps_too_old"
    interim = pn.panel(result([fact("profit_for_period", date(2024, 9, 30), 6), eps()]), "live")
    v = va.eps_features(interim, row(ref=date(2024, 6, 27)))
    assert "earlier_period_than_anchor" in v["eps_latest_fy"].flags


def test_v8_price_states_staleness_and_pe():
    assert va.eps_features(P(eps()), row(adj=None, px=None))["earnings_yield_eps"].reason == "price_unknown"
    assert va.eps_features(P(eps()), row(adj=None, px="100"))["earnings_yield_eps"].reason == "factor_unresolved"
    assert va.eps_features(P(eps()), row(stale=3))["earnings_yield_eps"].state == "stale"
    assert va.eps_features(P(eps()), row(stale=9))["earnings_yield_eps"].state == pn.MISSING
    v = va.eps_features(P(eps("-4")), row())
    assert v["earnings_yield_eps"].value == D("-0.04") and v["pe_eps"].reason == "denominator_not_positive"
    v = va.eps_features(P(eps()), row(adj="50", factors=[split(date(2024, 6, 3), flags=("cum_price_stale",))]))
    assert v["eps_latest_fy"].state == "partial" and v["earnings_yield_eps"].state == "partial"
    v = va.eps_features(P(eps()), row(factors=[split(date(2024, 6, 3), status="unresolved")]))
    assert v["eps_latest_fy"].reason == "factor_unresolved"


def test_v9_market_value_is_untrusted_while_provisional():
    p = P(fact("revenue", E, 12, value="500"))
    lines = [row(mcap="1000"), row(mcap="200")]
    mv = va.issuer_market_value(lines)
    assert (mv.state, mv.value, mv.reason) == (pn.UNTRUSTED, D("1200"), "input_unverified")
    out = va.mcap_features(p, lines, None, None)
    assert out["sales_to_market"].state == pn.UNTRUSTED and out["sales_to_market"].reason == "input_unverified"
    assert va.issuer_market_value([row(mcap=None), row()]).reason == "incomplete_lines"
