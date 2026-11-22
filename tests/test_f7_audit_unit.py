"""
Phase 4 F7-1 and F7-2: tests for the findings of their first audit. B1 (a held but not yet public document shaped
the panel in KNOWN) through F8's own selection, one case per channel, each with its converse; the 27th rule (M2); a
conflicting input in one TTM path (M3); the state order in growth (M4); interval arithmetic (M5); the AVAILABLE
construction of the leakage tests and converses (M6); the rules that survived mutation (M7). Pure.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from f7_factories import World, at, fact, fin_doc, publish, result
from worker.financial_asof import AVAILABLE, KNOWN, selection
from worker.financial_features import panel as pn
from worker.financial_features import periods as pr
from worker.financial_features.dates import exact_rule_ok, months_before

T = at("2023-06-30T00:00")
H = at("2026-02-01T00:00")


def _world():
    w = World()
    w.configure(at("2020-01-01"))
    return w


def _panel(w, mode=KNOWN, cutoff=T, horizon=None):
    r = selection.evaluate(w.evidence(), w.query(mode, cutoff=cutoff, horizon=horizon))
    return pn.panel(r, "live" if mode == KNOWN else "point_in_time")


def _public(w, filing, n, values, **kw):
    publish(w, fin_doc(filing, n, values, **kw), published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))


def _held(w, filing, n, values, **kw):
    """Recorded before T (held), published after T (not public at T)."""
    publish(w, fin_doc(filing, n, values, **kw), published=at("2023-08-10T09:00"), recorded=at("2023-06-29T10:00"))


def _shape(p):
    return (p.state, p.scope, p.currency, p.newest_period, p.anchor_months, p.flags)


def _case(public, held, later=None):
    """The panel at T is unchanged by the held document; the converse: once public it changes."""
    w = _world()
    for i, (values, kw) in enumerate(public):
        _public(w, 6000 + i, i + 1, values, **kw)
    before = _shape(_panel(w))
    _held(w, 6100, 50, held[0], **held[1])
    assert _shape(_panel(w)) == before, "a held, not yet public document changed the panel at T"
    after = _shape(_panel(w, cutoff=at("2023-09-01T00:00")))
    assert after != before, "converse: once public, the document is seen"
    return before, after


def test_b1_channels_scope_currency_duration_unlabelled_scope_changed():
    _case([({"profit_for_period": "100"}, {"end": "2023-03-31", "scope": "company"})],
          ({"profit_for_period": "110"}, {"end": "2023-03-31", "scope": "group"}))                  # A: scope
    _case([({"profit_for_period": "100"}, {"end": "2023-03-31", "currency": "USD"})],
          ({"profit_for_period": "110"}, {"end": "2023-03-31", "currency": "LKR"}))                 # B: currency
    _case([({"profit_for_period": "30"}, {"end": "2023-06-30", "months": 3, "doc_type": "interim_financial_statements"})],
          ({"profit_for_period": "60"}, {"end": "2023-06-30", "months": 6, "doc_type": "interim_financial_statements"}))
    _case([({"profit_for_period": "100"}, {"end": "2023-03-31", "scope": "unstated"})],
          ({"revenue": "900"}, {"end": "2023-03-31", "scope": "group"}))                            # D: unlabelled
    _case([({"profit_for_period": "100"}, {"end": "2023-03-31", "scope": "group"})],
          ({"profit_for_period": "90"}, {"end": "2022-03-31", "scope": "company"}))                 # E: scope_changed


def test_m6_available_construction_and_converses():
    """§12: two evidence sets differing only by the plant, both recorded before one fixed H later than the plant."""
    def base():
        w = _world()
        _public(w, 6200, 1, {"profit_for_period": "1,000", "revenue": "9,000"}, end="2023-03-31")
        return w
    clean, planted = base(), base()
    publish(planted, fin_doc(6201, 2, {"profit_for_period": "1,200"}, end="2024-03-31"),
            published=at("2024-05-15T09:00"), recorded=at("2024-05-15T10:00"))           # FL-1, FL-4: after T
    k = lambda p: (_shape(p), {kk: (u.state, u.value) for kk, u in p.facts.items()})  # noqa: E731
    assert k(_panel(clean, AVAILABLE, T, H)) == k(_panel(planted, AVAILABLE, T, H))
    assert _panel(planted, AVAILABLE, at("2024-06-30T00:00"), H).anchor_period == date(2024, 3, 31)
    # FL-2: a restatement (the comparative restated in a later filing) available after T
    rest = base()
    publish(rest, fin_doc(6202, 3, {"profit_for_period": "1,050"}, end="2023-03-31"),
            published=at("2023-09-01T09:00"), recorded=at("2023-09-01T10:00"))
    assert k(_panel(clean, AVAILABLE, T, H)) == k(_panel(rest, AVAILABLE, T, H))
    assert _panel(rest, AVAILABLE, at("2023-10-01T00:00"), H).state != _panel(clean, AVAILABLE, T, H).state or \
        k(_panel(rest, AVAILABLE, at("2023-10-01T00:00"), H)) != k(_panel(clean, AVAILABLE, T, H))
    # FL-3 before and after: published before T, known after T
    w = _world()
    t_before = _panel(w, KNOWN, T)
    publish(w, fin_doc(6203, 4, {"profit_for_period": "1,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2026-01-12T10:00"))
    assert _shape(_panel(w, KNOWN, T)) == _shape(t_before)
    assert _panel(w, AVAILABLE, T, H).anchor_period == date(2023, 3, 31)
    # FL-19 converse: the held document, once public, moves the newest period
    w = base()
    _held(w, 6204, 5, {"profit_for_period": "300"}, end="2023-06-30", months=3,
          doc_type="interim_financial_statements")
    assert _panel(w).newest_period == date(2023, 3, 31)
    assert _panel(w, cutoff=at("2023-09-01T00:00")).newest_period == date(2023, 6, 30)


def test_m2_the_27th_rule():
    assert exact_rule_ok(date(2001, 5, 27)) and not exact_rule_ok(date(2001, 5, 28))
    assert exact_rule_ok(date(2001, 2, 28)) and exact_rule_ok(date(2024, 2, 29))      # month-ends
    bad = []
    d = date(2000, 1, 1)
    while d < date(2033, 1, 1):
        if exact_rule_ok(d):
            for n in (3, 6, 9):
                if months_before(months_before(d, n), 12) != months_before(months_before(d, 12), n):
                    bad.append((d, n))
            if months_before(months_before(months_before(d, 3), 3), 3) != months_before(d, 9):
                bad.append((d, "chain"))
        d += timedelta(days=1)
    assert bad == []


def _P(*facts):
    ends = [f.identity.period_end for f in facts]
    return pn.panel(result(list(facts) + [fact("profit_for_period", max(ends), 12)]), "live")


def _rev(end, months, v, **kw):
    return fact("revenue", end, months, value=v, **kw)


def test_m3_a_conflicting_input_in_one_path_makes_the_ttm_conflicting():
    p = _P(_rev(date(2024, 9, 30), 12, None, state="conflicting"), _rev(date(2024, 9, 30), 6, "700"),
           _rev(date(2024, 3, 31), 12, "1200"), _rev(date(2023, 9, 30), 6, "600"))
    assert pr.ttm(p, "revenue", date(2024, 9, 30)).state == pn.CONFLICTING


def test_m4_state_order_in_growth():
    p = _P(_rev(date(2023, 3, 31), 12, None, state="conflicting"))
    g = pr.growth_ttm(p, "revenue", date(2024, 3, 31))                     # current missing, base conflicting
    assert g.state == pn.CONFLICTING


def test_m5_intervals():
    def iv(c, end, m, v, lo, hi, **kw):
        f = fact(c, end, m, value=v, **kw)
        from dataclasses import replace
        return replace(f, interval_low=D(lo), interval_high=D(hi))
    p = _P(iv("revenue", date(2024, 9, 30), 6, "700", "699", "701"), iv("revenue", date(2024, 3, 31), 12, "1200",
                                                                       "1199", "1201"),
           iv("revenue", date(2023, 9, 30), 6, "600", "590", "610"))
    t = pr.ttm(p, "revenue", date(2024, 9, 30))
    assert (t.value, t.low, t.high) == (D("1300"), D("1288"), D("1312"))            # subtraction swaps the bounds
    p = _P(iv("cost_of_sales", date(2024, 3, 31), 12, "-500", "-501", "-499"))
    t = pr.ttm(p, "cost_of_sales", date(2024, 3, 31))
    assert (t.value, t.low, t.high) == (D("500"), D("499"), D("501"))               # the magnitude's bounds
    p = _P(iv("cost_of_sales", date(2024, 3, 31), 12, "0.5", "-0.1", "0.9"))
    t = pr.ttm(p, "cost_of_sales", date(2024, 3, 31))
    assert (t.low, t.high) == (D("0"), D("0.9"))                                    # straddling zero
    e = date(2024, 3, 31)
    cash = [iv("net_cash_from_operating_activities", e, 12, "500", "499", "501"),
            fact("net_cash_from_investing_activities", e, 12, value="-200"),
            fact("net_cash_from_financing_activities", e, 12, value="-100"),
            fact("cash_at_end_of_period", months_before(e, 12), None, value="1000"),
            fact("cash_at_end_of_period", e, None, value="1201")]
    assert all(x.number for x in pr.cashflow_confirmed(_P(*cash), e, 12).values())  # within the interval


def test_m7_rules_that_survived_mutation():
    for c in ("purchase_of_ppe", "dividends_paid"):
        p = _P(fact(c, date(2024, 3, 31), 12, value="-40"))
        assert pr.ttm(p, c, date(2024, 3, 31)).value == D("40"), c
    usd6 = fact("profit_for_period", date(2024, 3, 31), 6, currency="USD")
    p = pn.panel(result([fact("profit_for_period", date(2024, 3, 31), 3), usd6]), "live")
    assert p.anchor_months == 3                                    # n from the panel currency's facts only
    p = pn.panel(result([fact(), fact(operations="discontinued", scope="company", value="3")]), "live")
    assert "has_discontinued" not in p.flags                       # another scope's discontinued fact
    p = pn.panel(result([fact(), fact("revenue", scope="company", value="9")]), "live")
    assert p.fact("revenue", date(2024, 3, 31), 12) is None        # another scope's facts are not in the table
    p = _P(_rev(date(2024, 3, 31), 12, "1200"), _rev(date(2023, 3, 31), 12, "0"))
    assert pr.growth_ttm(p, "revenue", date(2024, 3, 31)).reason == "base_not_positive"
    e = date(2024, 9, 30)
    cash6 = [fact("net_cash_from_operating_activities", e, 6, value="50"),
             fact("net_cash_from_investing_activities", e, 6, value="-20"),
             fact("net_cash_from_financing_activities", e, 6, value="-10"),
             fact("cash_at_end_of_period", months_before(e, 6), None, value="100"),
             fact("cash_at_end_of_period", e, None, value="120")]
    assert all(x.number for x in pr.cashflow_confirmed(_P(*cash6), e, 6).values())   # opening at E-6
    p = _P(_rev(date(2024, 3, 31), 12, None, nil=True))
    assert pr.ttm(p, "revenue", date(2024, 3, 31)).reason == "printed_nil"
    p = _P(_rev(date(2024, 9, 30), 6, "700"), _rev(date(2023, 9, 30), 6, "560"))
    assert pr.growth_cum(p, "revenue", date(2024, 9, 30), 6).value == D("0.25")       # base at E-12 for n = 6
    t = datetime(2024, 6, 30, tzinfo=timezone.utc)
    ds = [pn.Decision(5, "sec", "iss-a", "evidenced", t - timedelta(days=10)),
          pn.Decision(9, "sec", "iss-b", "evidenced", t - timedelta(days=20))]         # higher id, earlier time
    assert pn.security_link(ds, "sec", KNOWN, t).issuer_id == "iss-b"
