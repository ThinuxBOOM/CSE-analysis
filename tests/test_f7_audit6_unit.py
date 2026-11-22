"""Sixth F7 audit (C-0084): a public fact F8 shows only as a metadata tie (state none; untrusted, §4.2) counts
wherever F7 decides what exists: eps_latest_fy never steps back past it (§6.2), the panel never steps over it at
the newest period (§4.3), and the panel choices and the industry group see it. A held document still never counts.
Real F8 for the two Blockers; factory facts for the rest."""
from datetime import date
from decimal import Decimal

from f7_factories import AVAILABLE, KNOWN, World, at
from f63_factories import Doc
from worker.financial_asof import selection
from worker.financial_features import features as ft, panel as pn, valuation as va

IT, AR = "interim_financial_statements", "annual_report"


def doc(filing, end, months, values, dtype=AR):
    d = Doc(filing, doc=filing, doc_type=dtype)
    st = d.statement("profit_or_loss", currency="LKR")
    ci = d.column(st, end=end, months=months, scope="group")
    for c, raw in values.items():
        d.value(st, d.row(st, c.replace("_", " ").title()), ci, raw, c, **({"scale": 1} if c == "eps_basic" else {}))
    if "total_equity" not in values:
        sp = d.statement("financial_position", currency="LKR")
        cp = d.column(sp, kind="instant", end=end, scope="group")
        d.value(sp, d.row(sp, "Total Equity"), cp, "500", "total_equity")
    return d


def put(w, d, pub, tie=False):
    w.listing(d.filing, at(pub), uploaded=at(pub), text="A")
    if tie:
        from datetime import timedelta
        w.listing(d.filing, at(pub), uploaded=at(pub) + timedelta(minutes=1), text="B")
    from datetime import timedelta
    dec = w.decision(d.filing, at(pub) + timedelta(minutes=30))
    w.process(d, at(pub) + timedelta(hours=1), decision=dec, uploaded_at=at(pub))


def world(tie, with_interim):
    w = World(); w.configure(at("2020-01-01"))
    put(w, doc(9001, "2023-03-31", 12, {"profit_for_period": "80", "eps_basic": "0.80"}), "2023-05-20T09:00")
    if with_interim:   # the profit at 2024-03-31 comes from an untied interim; the tied annual report adds the FY EPS
        put(w, doc(9003, "2024-03-31", 12, {"profit_for_period": "100"}, IT), "2024-05-10T09:00")
        put(w, doc(9002, "2024-03-31", 12, {"eps_basic": "1.00"}), "2024-05-20T09:00", tie)
    else:
        put(w, doc(9002, "2024-03-31", 12, {"profit_for_period": "100", "eps_basic": "1.00"}), "2024-05-20T09:00", tie)
    return w


ROW = va.SecurityRow("ABC.N0000", "sec", date(2024, 6, 30), Decimal("10"), Decimal("10"), date(2024, 6, 30), 0,
                     None, (), ())


def panel(w, mode):
    h = None if mode == KNOWN else at("2024-09-01")
    return pn.panel(selection.evaluate(w.evidence(), w.query(mode, cutoff=at("2024-07-01"), horizon=h)),
                    "live" if mode == KNOWN else "point_in_time")


def test_eps_latest_fy_steps_back_past_a_tie_only_fy_eps():
    for mode in (KNOWN, AVAILABLE):
        p = panel(world(True, True), mode)
        assert p.state == pn.OK and p.anchor_period == date(2024, 3, 31)
        u = p.fact("eps_basic", date(2024, 3, 31), 12)
        assert u is not None and u.state == pn.UNTRUSTED and u.f8_state == "none"     # public, tie-only
        e = va.eps_features(p, ROW)
        print(mode, va.eps_latest_fy_fact(p)[0], e["eps_latest_fy"].state, e["eps_latest_fy"].value,
              e["eps_latest_fy"].flags, e["earnings_yield_eps"].state, e["earnings_yield_eps"].value)
        # design 6.2 + 4.2: the 2024-03-31 FY EPS exists and is untrusted; it never steps back to 2023
        assert e["eps_latest_fy"].state == pn.UNTRUSTED, (mode, e["eps_latest_fy"])


def test_panel_steps_over_a_tie_only_newest_anchor():
    for mode in (KNOWN, AVAILABLE):
        p = panel(world(True, False), mode)
        u = p.fact("profit_for_period", date(2024, 3, 31), 12)
        print(mode, p.state, p.anchor_period, u, ft.compute(p)["roe_end"].state if p.state == pn.OK else None)
        # design 4.3: an untrusted anchor at the newest period gives anchor_unusable, never an older period
        assert p.state == pn.ANCHOR_UNUSABLE, (mode, p.state, p.anchor_period)


# ------------------------------------------------------------------------------------------------ M-1 and the guard

from f7_factories import fact, not_yet, result                                  # noqa: E402
from worker.financial_asof.result import METADATA_TIE                           # noqa: E402

E = date(2024, 3, 31)


def tie(concept, end=E, months=12, **kw):
    return fact(concept, end, months, state="none", value=None, flags=(METADATA_TIE,), **kw)


def test_m1_a_tie_only_bank_concept_makes_the_issuer_bank_finance():
    base = [fact(), fact("profit_for_period", date(2023, 3, 31), 12)]
    assert ft.industry(pn.panel(result(base), "live")) == "general"
    assert ft.industry(pn.panel(result(base + [tie("net_interest_income")]), "live")) == "bank_finance"


def test_m1_a_tie_only_newest_anchor_blocks_and_shapes_the_scope():
    older = [fact("profit_for_period", date(2023, 3, 31), 12)]
    p = pn.panel(result(older + [tie("profit_for_period")]), "live")
    assert (p.state, p.newest_period) == (pn.ANCHOR_UNUSABLE, E)
    p = pn.panel(result([fact()] + [tie("profit_for_period", scope="company")]), "live")
    assert p.scope == "group"                                     # group still wins at the newest period
    p = pn.panel(result([fact(scope="company"), tie("profit_for_period", scope="group")]), "live")
    assert p.scope == "group" and p.state == pn.ANCHOR_UNUSABLE   # the tie is the group anchor: never stepped over


def test_a_held_document_still_never_counts():
    older = [fact("profit_for_period", date(2023, 3, 31), 12)]
    p = pn.panel(result(older + [not_yet("profit_for_period", E, 12)]), "live")
    assert (p.state, p.newest_period) == (pn.OK, date(2023, 3, 31))
    p = pn.panel(result(older + [not_yet("net_interest_income", date(2023, 3, 31), 12)]), "live")
    assert ft.industry(p) == "general"
