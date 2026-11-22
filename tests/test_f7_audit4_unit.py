"""
Phase 4 F7-1 to F7-3: tests for the findings of the fourth F7 audit. B-1 (a held EPS fact made an eps_ttm_approx
path applicable in KNOWN) through F8's own selection, with both of the auditor's reproductions, AVAILABLE and the
converse; M-1 (the move window's bounds); M-3 (the AVAILABLE guard); the internal not_shown marker. Pure.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from f7_factories import World, at, fact, publish, result
from f63_factories import Doc
from worker.financial_asof import AVAILABLE, KNOWN, Refused, selection
from worker.financial_features import features as ft
from worker.financial_features import panel as pn
from worker.financial_features import valuation as va

IT = "interim_financial_statements"
T, H = at("2024-06-30T00:00"), at("2025-01-01T00:00")
ROW = va.SecurityRow("ABC.N0000", "sec", date(2024, 6, 28), D("100"), D("100"), date(2024, 6, 27), 0, D("1000"))


def doc(filing, n, values, *, end, months=12, doc_type="annual_report"):
    d = Doc(filing, doc=n, doc_type=doc_type)
    st = d.statement("profit_or_loss")
    ci = d.column(st, end=end, months=months, scope="group")
    for c, raw in values.items():
        d.value(st, d.row(st, c.replace("_", " ").title()), ci, raw, c, **({"scale": 1} if c == "eps_basic" else {}))
    return d


def base():
    w = World()
    w.configure(at("2020-01-01"))
    for f, n, vals, end, months, dt, pub in (
            (9001, 1, {"profit_for_period": "1,200", "eps_basic": "12.00"}, "2024-03-31", 12, "annual_report",
             "2024-05-15"),
            (9002, 2, {"eps_basic": "3.00"}, "2023-06-30", 3, IT, "2023-08-10"),
            (9003, 3, {"eps_basic": "3.30"}, "2023-06-30", 3, IT, "2023-08-20"),          # conflicting
            (9004, 4, {"eps_basic": "3.00"}, "2023-09-30", 3, IT, "2023-11-10"),
            (9005, 5, {"eps_basic": "3.00"}, "2023-12-31", 3, IT, "2024-02-10")):
        publish(w, doc(f, n, vals, end=end, months=months, doc_type=dt), published=at(pub + "T09:00"),
                recorded=at(pub + "T10:00"))
    return w


def plant(w, published):
    publish(w, doc(9006, 6, {"eps_basic": "3.00"}, end="2024-03-31", months=3, doc_type=IT),
            published=published, recorded=at("2024-06-20T10:00"))
    return w


def run(w, mode, cut, h=None, row=ROW):
    p = pn.panel(selection.evaluate(w.evidence(), w.query(mode, cutoff=cut, horizon=h)),
                 "live" if mode == KNOWN else "point_in_time")
    f = va.eps_features(p, row)["eps_ttm_approx"]
    return f.state, f.value, f.reason


def test_b1_a_held_eps_fact_never_makes_a_trailing_eps_path_applicable():
    assert run(base(), KNOWN, T) == run(plant(base(), at("2024-08-10T09:00")), KNOWN, T)
    assert run(base(), AVAILABLE, T, H) == run(plant(base(), at("2024-08-10T09:00")), AVAILABLE, T, H)
    public = run(plant(base(), at("2024-06-21T09:00")), KNOWN, T)                        # converse: once public
    assert public != run(base(), KNOWN, T)


def test_b1_a_fresh_trailing_eps_is_not_lost():
    def world():
        w = World()
        w.configure(at("2020-01-01"))
        for f, vals, end, months, pub, dt in (
                (9101, {"profit_for_period": "600", "eps_basic": "6.00"}, "2024-06-30", 6, "2024-08-10", IT),
                (9102, {"eps_basic": "3.00"}, "2024-06-30", 3, "2024-08-10", IT),
                (9103, {"eps_basic": "3.00"}, "2024-03-31", 3, "2024-05-10", IT),
                (9104, {"eps_basic": "12.00"}, "2023-12-31", 12, "2024-03-20", "annual_report"),
                (9105, {"eps_basic": "6.00"}, "2023-06-30", 6, "2023-08-10", IT),
                (9106, {"eps_basic": "3.00"}, "2023-09-30", 3, "2023-11-10", IT),
                (9107, {"eps_basic": "3.30"}, "2023-09-30", 3, "2023-11-20", IT)):
            publish(w, doc(f, f, vals, end=end, months=months, doc_type=dt), published=at(pub + "T09:00"),
                    recorded=at(pub + "T10:00"))
        return w
    t2 = at("2024-09-01T00:00")
    r2 = va.SecurityRow("ABC.N0000", "sec", date(2024, 8, 30), D("100"), D("100"), date(2024, 8, 29), 0, D("1000"))
    held = world()
    publish(held, doc(9108, 9108, {"eps_basic": "3.00"}, end="2023-12-31", months=3, doc_type=IT),
            published=at("2024-10-01T09:00"), recorded=at("2024-08-25T10:00"))
    clean = run(world(), KNOWN, t2, row=r2)
    assert clean[0] == pn.FRESH and run(held, KNOWN, t2, row=r2) == clean


A = "2024-05-15T04:00:00+00:00"
E = date(2024, 3, 31)


def eps_panel():
    return pn.panel(result([fact("profit_for_period", E, 12), fact("eps_basic", E, 12, value="10",
                                                                   available=(A,))]), "live")


def test_m1_the_move_window_bounds():
    r = lambda moves: va.eps_features(eps_panel(), va.SecurityRow(  # noqa: E731
        "ABC.N0000", "sec", date(2024, 6, 28), D("100"), D("100"), date(2024, 6, 27), 0, D("1000"), (),
        tuple(moves)))["eps_latest_fy"]
    before_end = va.Move(date(2024, 3, 27), date(2024, 3, 28), "open")        # session_to before the period end
    assert r([before_end]).state == pn.FRESH
    at_dstar = va.Move(date(2024, 6, 27), date(2024, 6, 28), "open")          # session_to = D*
    assert r([at_dstar]).reason == "unexplained_move_since_document"
    after_dstar = va.Move(date(2024, 6, 28), date(2024, 7, 1), "open")        # after D*: not this snapshot's
    assert r([after_dstar]).state == pn.FRESH


def test_m3_the_available_guard_needs_h():
    avail = result([fact()], mode=AVAILABLE, horizon="2026-01-01T00:00:00+00:00")
    assert pn.panel(avail, "point_in_time").state == pn.OK
    from dataclasses import replace
    from worker.financial_asof.result import seal
    no_h = seal(replace(avail, knowledge_horizon=None, result_hash=""))
    with pytest.raises(Refused):
        pn.panel(no_h, "point_in_time")


def test_n1_the_internal_marker_never_reaches_a_feature():
    p = pn.panel(result([fact("profit_for_period", E, 12), fact("total_equity", E, None, value="10"),
                         fact("interest_bearing_borrowings", E, None, value="5", maturity="current", state="none")]),
                 "live")
    assert all("not_shown" not in v.flags for v in ft.compute(p).values())
