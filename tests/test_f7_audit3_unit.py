"""
Phase 4 F7-1 to F7-4 and DE-1 to DE-3: tests for the findings of the third F7 audit. B-1 (a held, not yet public
document made a TTM path applicable) through F8's own selection with its converse; FL-21 (d)'s reason; B-6's
interval scaling; §7.2's universes (FL-7, FL-13); each engine of set v1 on a known-rank universe (DE-2); and the
rules that survived mutation in F7-3, F7-4 and DE. Pure.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from f7_factories import World, at, fact, fin_doc, publish, result
from worker.engines import baselines as bl
from worker.engines import registry as rg
from worker.engines import scoring as sc
from worker.financial_asof import AVAILABLE, KNOWN, selection
from worker.financial_features import eligibility as el
from worker.financial_features import features as ft
from worker.financial_features import panel as pn
from worker.financial_features import periods as pr
from worker.financial_features import valuation as va

E, E1 = date(2024, 3, 31), date(2023, 3, 31)
IT = "interim_financial_statements"
A = "2024-05-15T04:00:00+00:00"
DSTAR = date(2024, 6, 28)
V = sc.Value


def _base():
    w = World()
    w.configure(at("2020-01-01"))
    publish(w, fin_doc(9001, 1, {"profit_for_period": "1,200", "revenue": "12,000"}, end="2024-03-31"),
            published=at("2024-05-15T09:00"), recorded=at("2024-05-15T10:00"))
    publish(w, fin_doc(9000, 9, {"profit_for_period": "1,000", "revenue": "10,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))
    publish(w, fin_doc(9002, 2, {"revenue": "3,000"}, end="2023-06-30", months=3, doc_type=IT),
            published=at("2023-08-10T09:00"), recorded=at("2023-08-10T10:00"))
    publish(w, fin_doc(9003, 3, {"revenue": "3,300"}, end="2023-06-30", months=3, doc_type=IT),     # conflicting
            published=at("2023-08-20T09:00"), recorded=at("2023-08-20T10:00"))
    publish(w, fin_doc(9004, 4, {"revenue": "3,000"}, end="2023-09-30", months=3, doc_type=IT),
            published=at("2023-11-10T09:00"), recorded=at("2023-11-10T10:00"))
    publish(w, fin_doc(9005, 5, {"revenue": "3,000"}, end="2023-12-31", months=3, doc_type=IT),
            published=at("2024-02-10T09:00"), recorded=at("2024-02-10T10:00"))
    return w


def _key(w, mode, cut, h=None):
    p = pn.panel(selection.evaluate(w.evidence(), w.query(mode, cutoff=cut, horizon=h)),
                 "live" if mode == KNOWN else "point_in_time")
    t, f = pr.ttm(p, "revenue", E), ft.compute(p)
    return (t.state, t.value, f["rev_g_ttm"].state, f["net_margin"].state), p


def test_b1_a_held_document_never_makes_a_ttm_path_applicable():
    t, h = at("2024-06-30T00:00"), at("2025-01-01T00:00")
    clean, held = _base(), _base()
    publish(held, fin_doc(9006, 6, {"revenue": "3,000"}, end="2024-03-31", months=3, doc_type=IT),
            published=at("2024-08-10T09:00"), recorded=at("2024-06-20T10:00"))           # held at T
    assert _key(clean, KNOWN, t)[0] == _key(held, KNOWN, t)[0] == (pn.FRESH, D("12000000"), pn.FRESH, pn.FRESH)
    assert _key(clean, AVAILABLE, t, h)[0] == _key(held, AVAILABLE, t, h)[0]
    later = _key(held, KNOWN, at("2024-09-01T00:00"))[1]                                    # converse
    assert pr.ttm(later, "revenue", E).state == pn.CONFLICTING


def P(*facts):
    ends = [f.identity.period_end for f in facts] or [E]
    return pn.panel(result([fact("profit_for_period", max(ends), 12)] + list(facts)), "live")


def row(adj="100", px="100", ref=date(2024, 6, 27), stale=0, factors=(), moves=(), mcap="1000"):
    return va.SecurityRow("ABC.N0000", "sec", DSTAR, D(px) if px else None, D(adj) if adj else None, ref, stale,
                          D(mcap) if mcap else None, tuple(factors), tuple(moves))


def eps(v="10", end=E, available=(A,), **kw):
    return fact("eps_basic", end, 12, value=v, available=available, **kw)


def split(ex, flags=()):
    return va.Factor("subdivision", ex, 1, 2, "applied", tuple(flags))


def test_v1_fl21d_reason_whether_or_not_entered():
    for factors in ((), (split(date(2024, 4, 30)),)):
        v = va.eps_features(P(eps()), row(ref=date(2024, 5, 10), factors=factors))
        assert (v["eps_latest_fy"].state, v["eps_latest_fy"].reason) == (pn.UNTRUSTED, "no_close_since_document")


def test_v2_b6_intervals_are_scaled():
    q = [replace(fact("eps_basic", d, 3, value=v, available=(A,)), interval_low=D(v) - D("0.05"),
                 interval_high=D(v) + D("0.05"))
         for d, v in ((date(2024, 3, 31), "2.5"), (date(2023, 12, 31), "2.5"), (date(2023, 9, 30), "2.5"),
                      (date(2023, 6, 30), "2.5"))]
    d = va.eps_ttm_approx(P(*q), row(adj="50", factors=[split(date(2024, 6, 3))]))
    assert (d.value, d.low, d.high) == (D("5"), D("4.9"), D("5.1"))


def test_v3_ex_dates_at_the_window_ends():
    on_a = va.eps_features(P(eps()), row(factors=[split(date(2024, 5, 15))]))     # e = date(A): ambiguous
    assert on_a["eps_latest_fy"].reason == "eps_basis_ambiguous"
    on_d = va.eps_features(P(eps()), row(adj="50", factors=[split(DSTAR)]))        # e = D*: applied
    assert on_d["eps_latest_fy"].value == D("5")
    held = eps("12", date(2024, 3, 31), state="none")                              # V10: a held, newer FY EPS
    v = va.eps_features(pn.panel(result([fact("profit_for_period", E, 12), held, eps("8", E1)]), "live"), row())
    assert v["eps_latest_fy"].value == D("8")
    assert va.eps_features(P(eps()), row(stale=5))["earnings_yield_eps"].state == "stale"      # at the limit
    assert va.eps_features(P(eps()), row(stale=6))["earnings_yield_eps"].state == pn.MISSING


def test_f1_survivors_in_features():
    v = ft.compute(P(fact("total_equity", E, None, value="10"), fact("total_assets", E, None, value="-5")))
    assert v["equity_to_assets"].reason == "denominator_not_positive"
    bank = [fact("profit_for_period", E, 12, value="50"), fact("profit_for_period", E1, 12, value="40"),
            fact("gross_income", E, 12, value="500"), fact("gross_income", E1, 12, value="400"),
            fact("profit_for_period", E, 12, operations="discontinued", value="2")]
    v = ft.compute(pn.panel(result(bank), "live"))
    assert v["net_margin_bank"].state == "partial" and v["net_margin_chg"].state == "partial"


OK = [fact("profit_for_period", E, 12, value="120"), fact("profit_for_period", E1, 12, value="100"),
      fact("revenue", E, 12, value="1200"), fact("revenue", E1, 12, value="1000"),
      fact("total_equity", E, None, value="800"), fact("total_assets", E, None, value="2000")]


def judge(facts, cutoff="2024-06-30T00:00:00+00:00", features=None):
    p = pn.panel(result(facts, cutoff=cutoff), "live")
    return el.eligibility(p, features if features is not None else ft.compute(p))


def test_e1_eligibility_survivors():
    bad_assets = [f for f in OK if f.identity.concept_key != "total_assets"] + [
        fact("total_assets", E, None, state="conflicting", value=None)]
    assert judge(bad_assets).result == el.CRITICAL_CONFLICT
    unusable_year = [f for f in OK if not (f.identity.concept_key == "profit_for_period"
                                           and f.identity.period_end == E1)] + [
        fact("profit_for_period", E1, 12, flags=("ambiguous_supersession",))]
    assert judge(unusable_year).result == el.INSUFFICIENT_HISTORY            # usable years only
    six_month = [f for f in OK if not (f.identity.concept_key == "profit_for_period"
                                       and f.identity.period_end == E1)] + [
        fact("profit_for_period", E1, 6, value="50")]
    assert judge(six_month).result == el.INSUFFICIENT_HISTORY                 # 12-month facts only
    disc = [f for f in OK if not (f.identity.concept_key == "profit_for_period"
                                  and f.identity.period_end == E1)] + [
        fact("profit_for_period", E1, 12, operations="discontinued", value="5")]
    assert judge(disc).result == el.INSUFFICIENT_HISTORY                      # total operations only
    p = pn.panel(result(OK), "live")
    f = ft.compute(p)
    f = dict(f, profit_g_ttm=replace(f["profit_g_ttm"], state="stale"), roe_end=replace(f["roe_end"],
                                                                                       state="partial"))
    f = {k: (v if k in ("profit_g_ttm", "roe_end") else replace(v, state="missing", value=None))
         for k, v in f.items()}
    assert el.eligibility(p, f).result == el.ELIGIBLE                         # partial and stale count
    one_year_old = [f for f in OK if f.identity.period_end != E1]
    e = judge(one_year_old, cutoff="2025-06-30T00:00:00+00:00")
    assert e.failed[:2] == (el.INSUFFICIENT_HISTORY, el.STALE) and e.result == el.INSUFFICIENT_HISTORY


def test_u1_universes_fl7_fl13():
    issuers = {"I1": {"eligible": True, "group": "general", "lines": [("A.N0000", True), ("A.X0000", False)]},
               "I2": {"eligible": False, "group": "general", "lines": [("B.N0000", True)]},
               "I3": {"eligible": True, "group": "general", "lines": [("C.N0000", False)]},
               "I4": {"eligible": True, "group": "bank_finance", "lines": [("D.N0000", True)]}}
    members, na = sc.fundamental_universe(rg.BY_ID["fund_growth"], issuers)
    assert members == ["I1"] and na == ["I4"]
    assert sc.valuation_eps_universe(issuers) == {"bank_finance": ["D.N0000"], "general": ["A.N0000"]}
    assert sc.market_universe({"A.N0000": True, "A.X0000": False}) == ["A.N0000"]
    # FL-13: another issuer's later filing (it becomes eligible) does not change this issuer's membership at T
    later = dict(issuers, I2=dict(issuers["I2"], eligible=True))
    assert sc.fundamental_universe(rg.BY_ID["fund_growth"], issuers)[0] == ["I1"]
    assert sc.fundamental_universe(rg.BY_ID["fund_growth"], later)[0] == ["I1", "I2"]     # converse


def test_d1_every_engine_on_a_known_rank_universe():
    for e in rg.ENGINES:
        req = next(c for c in e.components if c.required)
        members = {f"M{i:02d}": {c.feature_id: V(D(i), "fresh") for c in e.components} for i in range(12)}
        out = sc.score(e, members, group="all")
        assert out["M11"].state == sc.SCORED, e.engine_id
        top, bottom = out["M11"].score, out["M00"].score
        expected = sum(c.orientation * c.weight for c in e.components) / sum(c.weight for c in e.components)
        assert (top > 0) == (expected > 0) and (bottom < 0) == (expected > 0), e.engine_id
        only = {f"M{i:02d}": {req.feature_id: V(D(i), "fresh")} for i in range(12)}
        state = sc.score(e, only, group="all")["M05"].state
        assert state == (sc.SCORED if e.min_components == 1 else sc.INSUFFICIENT), e.engine_id
    signs = {e.engine_id: tuple(c.orientation for c in e.components) for e in rg.ENGINES}
    assert signs["fund_leverage"] == (1, -1) and signs["fund_cash_flow"] == (1, 1)
    assert signs["mkt_trend"] == (1, 1) and signs["mkt_reversal"] == (-1, -1)
    assert signs["fund_profitability_bank"] == (1, 1, 1, 1) and rg.BY_ID["mkt_liquidity"].min_components == 2


def test_d2_scoring_survivors():
    e = rg.Engine("t", "1", "fundamental", "issuer", "general", (rg.C("x", 1, True), rg.C("y", 1)), 1)
    m = {f"M{i:02d}": {"x": V(D(i), "fresh"), "y": V(D(i), "fresh")} for i in range(12)}
    m["M00"] = {"y": V(D(0), "fresh")}                                   # min 1 met by y, required x missing
    assert sc.score(e, m, group="general")["M00"].state == sc.INSUFFICIENT
    a = sc.score(e, m, group="general")["M05"].inputs_hash
    b = sc.score(e, dict(m, M99={"x": V(D(9), "fresh")}), group="general")["M05"].inputs_hash
    assert a != b                                                         # the universe's digest is an input
    nc = bl.base_no_change(5)
    assert {k for k, v in nc.items() if v["reason"] is None} == {"expected_return", "direction"}
