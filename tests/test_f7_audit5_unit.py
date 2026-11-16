"""
Fifth F7 audit (C-0082). B-1: a public fact F8 shows only as a metadata_version_tie (F8 state none, Usable
untrusted) was treated as not shown, so it became missing and stopped blocking its TTM path; design §4.2 makes it
untrusted and §5.2 makes an untrusted input block the TTM. Majors: tests for rules no earlier test protected (the
u.number guard and the (end, months) key of the cash-flow confirmation, the anchor bound of eps_latest_fy, and
_Restated keeping `partial`). Pure, through real F8 where the auditor reproduced it.
"""
from datetime import date

import f7_factories as ff
from f7_factories import AVAILABLE, KNOWN, World, at, fact, fin_doc, not_yet, publish, result
from worker.financial_asof import selection
from worker.financial_features import features as ft
from worker.financial_features import panel as pn
from worker.financial_features import periods as pr
from worker.financial_features import valuation as va

E = date(2024, 3, 31)


def _world(tie):
    w = World()
    w.configure(at("2020-01-01"))
    publish(w, fin_doc(9001, 1, {"profit_for_period": "100", "total_equity": "500"}, end="2024-03-31", months=12),
            published=at("2024-05-20T09:00"), recorded=at("2024-05-20T10:00"))
    publish(w, fin_doc(9003, 3, {"profit_for_period": "30"}, end="2024-03-31", months=3,
                       doc_type="interim_financial_statements"),
            published=at("2024-05-10T09:00"), recorded=at("2024-05-10T10:00"))
    publish(w, fin_doc(9004, 4, {"profit_for_period": "95"}, end="2023-12-31", months=12,
                       doc_type="interim_financial_statements"),
            published=at("2024-02-10T09:00"), recorded=at("2024-02-10T10:00"))
    d2 = fin_doc(9002, 2, {"profit_for_period": "999"}, end="2023-03-31", months=3,
                 doc_type="interim_financial_statements")
    obs = at("2023-05-10T08:00")
    w.listing(9002, obs, uploaded=at("2023-05-09T09:00"), text="Interim A")
    if tie:                                  # two listings of one filing that disagree only in metadata: a tie
        w.listing(9002, obs, uploaded=at("2023-05-09T09:01"), text="Interim B")
    dec = w.decision(9002, at("2023-05-10T09:00"))
    w.process(d2, at("2023-05-10T10:00"), decision=dec, uploaded_at=at("2023-05-09T09:00"))
    return w


def test_b1_a_tie_only_fact_is_untrusted_and_blocks_its_ttm_path_in_both_modes():
    for mode, h in ((KNOWN, None), (AVAILABLE, at("2024-09-01"))):
        w = _world(True)
        p = pn.panel(selection.evaluate(w.evidence(), w.query(mode, cutoff=at("2024-07-01"), horizon=h)),
                     "live" if mode == KNOWN else "point_in_time")
        assert p.fact("profit_for_period", date(2023, 3, 31), 3).state == pn.UNTRUSTED
        assert pr.duration(p, "profit_for_period", date(2023, 3, 31), 3).state == pn.UNTRUSTED
        t = pr.ttm(p, "profit_for_period", E)
        assert t.state == pn.UNTRUSTED, (mode, t.state, t.value, t.rule)


def test_b1_the_same_filing_untied_conflicts():
    w = _world(False)
    p = pn.panel(selection.evaluate(w.evidence(), w.query(KNOWN, cutoff=at("2024-07-01"))), "live")
    assert pr.ttm(p, "profit_for_period", E).state == pn.CONFLICTING        # 30 + 95 - 999 against 100


BASE = [fact(), fact("revenue", value="1000"), fact("total_equity", months=None, value="500")]


def test_f24_a_conflicting_or_held_cash_flow_never_becomes_a_confirmed_input():
    p = pn.panel(result(BASE + [fact("net_cash_from_operating_activities", state="conflicting", value=None)]), "live")
    assert ft.compute(p)["cfo_to_profit"].state == pn.CONFLICTING
    p = pn.panel(result(BASE + [fact("net_cash_from_operating_activities", E, 3, state="conflicting", value=None),
                                not_yet("net_cash_from_operating_activities", date(2023, 12, 31), 12),
                                fact("net_cash_from_operating_activities", date(2023, 3, 31), 3, value="5")]),
                 "live")
    d = ft._cfo_ttm(p, E)
    assert (d.state, d.reason) == (pn.MISSING, "no_exact_period_match")     # the held fact makes no path applicable


def test_f26_a_quarter_is_confirmed_by_its_own_identity_not_the_years():
    cash = {date(2023, 3, 31): "100", date(2023, 6, 30): "110", date(2023, 9, 30): "120", date(2023, 12, 31): "130",
            E: "200"}
    facts = BASE + [fact("cash_at_end_of_period", d, months=None, value=v) for d, v in cash.items()]
    for c, v in (("operating", "60"), ("investing", "20"), ("financing", "20")):    # the year: 100 = 200 - 100
        facts.append(fact(f"net_cash_from_{c}_activities", E, 12, value=v))
    for end, op in ((date(2023, 6, 30), "10"), (date(2023, 9, 30), "10"), (date(2023, 12, 31), "10"), (E, "40")):
        facts += [fact("net_cash_from_operating_activities", end, 3, value=op),       # the quarter to E: 40 != 70
                  fact("net_cash_from_investing_activities", end, 3, value="0"),
                  fact("net_cash_from_financing_activities", end, 3, value="0")]
    p = pn.panel(result(facts), "live")
    d = ft._cfo_ttm(p, E)
    assert d.state == pn.FRESH and d.value == 60 and d.rule == "F7P-1"


def test_v28_eps_latest_fy_is_never_after_the_anchor():
    p = pn.panel(result(BASE + [fact("eps_basic", E, 12, value="2"), fact("eps_basic", date(2024, 6, 30), 12,
                                                                          value="9")]), "live")
    assert va.eps_latest_fy_fact(p)[0] == E


def test_v30_a_partial_component_keeps_the_restated_panel_partial(monkeypatch):
    p = pn.panel(result(BASE + [fact("eps_basic", E, 3, value="1"), fact("eps_basic", date(2023, 12, 31), 3,
                                                                         value="1")]), "live")
    states = iter([va.PARTIAL, pn.FRESH])
    monkeypatch.setattr(va, "restate", lambda u, row: pr.Derived(next(states), u.value))
    r = va._Restated(p, None)
    r.fact("eps_basic", E, 3)
    r.fact("eps_basic", date(2023, 12, 31), 3)
    assert r.partial
