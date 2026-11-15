"""
Phase 4 F7-1: the fundamental panel as of T (worker/financial_features/panel.py; design §4). Pure rules on built
FactViews; leakage tests FL-1 to FL-5 and FL-19 through F8's own selection on synthetic worlds; FL-8 (guards), FL-11
(the link) and FL-15 (sign). No database, no network.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from f7_factories import World, at, fact, fin_doc, not_yet, publish, result
from worker.financial_asof import AVAILABLE, CURRENT, KNOWN, Refused, selection
from worker.financial_features import panel as pn
from worker.financial_features.periods import months_before

UTC = timezone.utc
E = date(2024, 3, 31)


# ------------------------------------------------------------------------------------------------ §4.2 usability

def test_u1_usability_table():
    assert pn.usability(fact(value="100")).number
    assert pn.usability(fact(state="corroborated", value="5")).value == D("5")
    assert pn.usability(fact(state="conflicting", value=None)).state == pn.CONFLICTING
    u = pn.usability(fact(nil=True))
    assert (u.state, u.nil, u.number) == (pn.FRESH, True, False)                    # a printed dash: never zero
    assert pn.usability(not_yet()).reason == "not_yet_available"
    assert pn.usability(fact(state="none", value=None)).reason == "absent"
    assert pn.usability(fact(annotations=("representative_ambiguous",))).reason == "representative_ambiguous"
    for f in ("ambiguous_supersession", "metadata_version_tie"):
        u = pn.usability(fact(flags=(f,)))
        assert (u.state, u.reason) == (pn.UNTRUSTED, f)
    u = pn.usability(fact(annotations=("restated_comparative_present", "internal_conflict")))
    assert u.number and u.recorded == ("internal_conflict", "restated_comparative_present")


def test_u2_sign_rule_and_fl15():
    assert pn.signed("cost_of_sales", D("-50")) == (D("50"), "magnitude")
    assert pn.signed("finance_costs", D("50")) == (D("50"), "magnitude")
    assert pn.signed("impairment_charges", D("-30")) == (D("-30"), "as_printed")     # FL-15: a reversal stays one
    assert pn.signed("income_tax_expense", D("-4")) == (D("-4"), "as_printed")
    assert pn.signed("revenue", None) == (None, None)


def test_u3_dates():
    assert months_before(date(2024, 3, 31), 12) == date(2023, 3, 31)
    assert months_before(date(2024, 6, 30), 6) == date(2023, 12, 31)
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)
    assert months_before(date(2024, 12, 25), 3) == date(2024, 9, 25)


# ------------------------------------------------------------------------------------------------ §4.3, §4.4

def test_p1_scope_order_and_unlabelled():
    p = pn.panel(result([fact(scope="company"), fact(scope="group"), fact(scope="bank")]), "live")
    assert (p.state, p.scope, p.anchor_period, p.anchor_months) == (pn.OK, "group", E, 12)
    p = pn.panel(result([fact(scope="company"), fact(scope="bank")]), "live")
    assert p.scope == "bank"
    p = pn.panel(result([fact(scope="unlabelled")]), "live")
    assert (p.state, p.scope, p.flags) == (pn.OK, "unlabelled", ("unlabelled_scope",))
    older_labelled = fact(end=date(2023, 3, 31), scope="group")
    p = pn.panel(result([fact(scope="unlabelled"), older_labelled]), "live")
    assert p.state == pn.SCOPE_UNAVAILABLE                         # newest only unlabelled, older labelled facts


def test_p2_currency():
    assert pn.panel(result([fact(currency="USD"), fact()]), "live").currency == "LKR"
    assert pn.panel(result([fact(currency="USD")]), "live").currency == "USD"
    p = pn.panel(result([fact(currency="USD"), fact(currency="EUR")]), "live")
    assert p.state == pn.CURRENCY_AMBIGUOUS
    p = pn.panel(result([fact(), fact("revenue", currency="USD", value="9")]), "live")
    assert p.fact("revenue", E, 12) is None                         # other-currency facts ignored for the panel


def test_p3_anchor_duration_and_states():
    h = date(2024, 6, 30)
    p = pn.panel(result([fact(end=h, months=3), fact(end=h, months=6)]), "live")
    assert (p.anchor_period, p.anchor_months) == (h, 6)              # a half-year interim prints 3 and 6 months
    p = pn.panel(result([fact(state="conflicting", value=None), fact(end=date(2023, 3, 31))]), "live")
    assert (p.state, p.anchor_period) == (pn.CRITICAL_CONFLICT, E)  # never stepped over to the older period
    assert pn.panel(result([fact(flags=("ambiguous_supersession",))]), "live").state == pn.ANCHOR_UNUSABLE
    assert pn.panel(result([fact(nil=True)]), "live").state == pn.ANCHOR_UNUSABLE
    assert pn.panel(result([fact("revenue")]), "live").state == pn.ANCHOR_ABSENT


def test_p4_scope_changed_and_discontinued():
    prior_company = fact(end=date(2023, 3, 31), scope="company")
    p = pn.panel(result([fact(), prior_company]), "live")
    assert "scope_changed" in p.flags
    p = pn.panel(result([fact(), fact(end=date(2023, 3, 31)), prior_company]), "live")
    assert "scope_changed" not in p.flags
    p = pn.panel(result([fact(), fact(operations="discontinued", value="3")]), "live")
    assert "has_discontinued" in p.flags


def test_p5_fl19_a_held_document_not_yet_public_does_not_move_the_anchor():
    p = pn.panel(result([fact(), not_yet(end=date(2024, 6, 30))]), "live")
    assert (p.newest_period, p.anchor_period) == (E, E)


# ------------------------------------------------------------------------------------------------ FL-8, FL-11

def test_g1_fl8_guards():
    cur = result([fact()], mode=CURRENT, cutoff=None, horizon="2026-01-01T00:00:00+00:00")
    for pipe in ("live", "point_in_time"):
        with pytest.raises(Refused):
            pn.panel(cur, pipe)
    avail = result([fact()], mode=AVAILABLE, horizon="2026-01-01T00:00:00+00:00")
    with pytest.raises(Refused):
        pn.panel(avail, "live")                                       # AVAILABLE never feeds the live pipeline
    assert pn.panel(avail, "point_in_time").state == pn.OK
    forged = result([fact()], mode=CURRENT, cutoff=None, horizon="2026-01-01T00:00:00+00:00",
                    label="known")                                    # a relabelled CURRENT envelope
    with pytest.raises(Refused):
        pn.panel(forged, "point_in_time")


def test_l1_fl11_the_link_at_t():
    t = datetime(2024, 6, 30, tzinfo=UTC)
    ds = [pn.Decision(1, "sec", "iss-a", "evidenced", t - timedelta(days=10)),
          pn.Decision(2, "sec", None, "conflict", t + timedelta(days=1)),
          pn.Decision(3, "sec", "iss-b", "evidenced", t + timedelta(days=2))]
    assert pn.security_link(ds, "sec", KNOWN, t) == pn.Link(pn.LINKED, "iss-a", 1)
    assert pn.security_link(ds, "sec", KNOWN, t + timedelta(days=1)).state == pn.LINK_CONFLICT
    assert pn.security_link(ds, "sec", AVAILABLE, t, t + timedelta(days=5)) == pn.Link(pn.LINKED, "iss-b", 3, True)
    assert pn.security_link(ds, "other", KNOWN, t).state == pn.LINK_ABSENT
    assert pn.security_link(ds, None, KNOWN, t).state == pn.LINK_ABSENT        # imported-only security
    with pytest.raises(Refused):
        pn.security_link(ds, "sec", AVAILABLE, t)
    with pytest.raises(Refused):
        pn.security_link(ds, "sec", CURRENT, t)


# ------------------------------------------------------------------------------------------------ real F8 worlds

def _world():
    w = World()
    w.configure(at("2020-01-01"))
    return w


def _panel(w, mode, cutoff, horizon=None):
    r = selection.evaluate(w.evidence(), w.query(mode, cutoff=cutoff, horizon=horizon))
    return pn.panel(r, "live" if mode == KNOWN else "point_in_time")


def _key(p):
    return (p.state, p.scope, p.currency, p.newest_period, p.anchor_period, p.anchor_months, p.flags,
            {k: (u.state, u.value, u.reason) for k, u in p.facts.items()})


def test_w1_fl1_fl4_a_later_filing_changes_nothing_at_t():
    w = _world()
    publish(w, fin_doc(5001, 1, {"profit_for_period": "1,000", "revenue": "9,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))
    t = at("2023-06-30T00:00")
    before = _panel(w, KNOWN, t)
    assert (before.state, before.anchor_period) == (pn.OK, date(2023, 3, 31))
    publish(w, fin_doc(5002, 2, {"profit_for_period": "1,200"}, end="2024-03-31"),
            published=at("2024-05-15T09:00"), recorded=at("2024-05-15T10:00"))
    assert _key(_panel(w, KNOWN, t)) == _key(before)                               # FL-1, FL-4
    assert _panel(w, KNOWN, at("2024-06-30T00:00")).anchor_period == date(2024, 3, 31)   # converse


def test_w2_fl2_fl5_a_restatement_or_conflict_after_t():
    w = _world()
    publish(w, fin_doc(5101, 1, {"profit_for_period": "1,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))
    t = at("2023-06-30T00:00")
    before = _panel(w, KNOWN, t)
    publish(w, fin_doc(5102, 2, {"profit_for_period": "1,500"}, end="2023-03-31"),
            published=at("2023-08-01T09:00"), recorded=at("2023-08-01T10:00"))
    after = _panel(w, KNOWN, t)
    assert _key(after) == _key(before)                                             # FL-2, FL-5
    later = _panel(w, KNOWN, at("2023-09-01T00:00"))
    assert later.state == pn.CRITICAL_CONFLICT                                     # converse: two values conflict


def test_w3_fl3_published_before_t_but_known_after_t():
    w = _world()
    publish(w, fin_doc(5201, 1, {"profit_for_period": "1,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2026-01-12T10:00"))     # backfilled in 2026
    t = at("2023-06-30T00:00")
    assert _panel(w, KNOWN, t).state == pn.ANCHOR_ABSENT                           # KNOWN at T: not yet known
    h = at("2026-02-01T00:00")
    assert _panel(w, AVAILABLE, t, h).anchor_period == date(2023, 3, 31)           # AVAILABLE: it was public


def test_w4_fl19_held_but_not_public():
    w = _world()
    publish(w, fin_doc(5301, 1, {"profit_for_period": "1,000"}, end="2023-03-31"),
            published=at("2023-05-15T09:00"), recorded=at("2023-05-15T10:00"))
    t = at("2023-06-30T00:00")
    before = _panel(w, KNOWN, t)
    publish(w, fin_doc(5302, 2, {"profit_for_period": "1,100"}, end="2023-06-30", months=3),
            published=at("2023-08-10T09:00"), recorded=at("2023-06-29T10:00"))     # held at T, public later
    after = _panel(w, KNOWN, t)
    assert (after.newest_period, after.anchor_period) == (before.newest_period, before.anchor_period)
