"""
Phase 3, step MF-6 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, section 11): the labels. Pure and offline;
expected values are worked in the test.
"""
import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from worker.market_features import actions as act
from worker.market_features import numeric as num
from worker.market_features import series as sr
from worker.market_features import targets as tg
from worker.market_features.bars import FeatureBar
from worker.market_features.calendar import SessionSequence
from worker.market_features.reference import reference_series

SYM = "ABC.N0000"
UTC = timezone.utc


def days(n):
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(close=None, *, unknown=False, quality="fresh"):
    traded = close is not None or unknown
    return FeatureBar(symbol=SYM, row=1 if close is not None else (2 if unknown else 3), traded=traded,
                      px_close=None if close is None else D(str(close)), price_unknown=unknown,
                      share_volume=D(100) if traded else D(0), trade_count=None, turnover=None, open_price=None,
                      high=None, low=None, quality=quality)


def build(bars, *, gaps=None, factors=(), moves=()):
    sessions = days(len(bars))
    gaps_after = tuple(tuple(date(2000, 1, 1 + k) for k in range((gaps or {}).get(i, 0))) for i in range(len(bars) - 1))
    seq = SessionSequence(tuple(sessions), gaps_after)
    refs = {r.session: r for r in reference_series(list(zip(sessions, bars)))}
    return sr.build_series(SYM, seq, dict(zip(sessions, bars)), refs, list(factors), moves), refs, sessions


def row(refs, session):
    r = refs[session]
    return tg.ReferenceRow(SYM, session, r.px_ref, r.ref_session, "row-1")


def at(i):
    return datetime(2024, 3, 1, tzinfo=UTC) + timedelta(hours=i)


def label(bars, d_index=0, h=1, **kw):
    build_kw = {k: kw.pop(k) for k in ("gaps", "factors", "moves") if k in kw}
    s, refs, sessions = build(bars, **build_kw)
    inst = kw.pop("instants", {x: at(i) for i, x in enumerate(sessions)})
    return tg.label(row(refs, sessions[d_index]), h, s, refs, session_instants=inst, **kw), sessions


def test_l1_resolved_return_direction_and_abs_ret_1():
    lab, s = label([bar(100), bar(110)])
    assert (lab.state, lab.ret, lab.direction, lab.target_session) == ("resolved", D("0.1"), "rise", s[1])
    assert (lab.volatility_name, lab.volatility) == ("abs_ret_1", num.q(num.ln(D("1.1"))))
    assert lab.resolved_at == at(1) and not lab.reference_revised and not lab.provisional
    assert label([bar(100), bar(100)])[0].direction == "flat"
    assert label([bar(100), bar(90)])[0].direction == "fall"


def test_l2_pending_until_the_target_session_exists():
    lab, _ = label([bar(100), bar(101)], h=5)
    assert (lab.state, lab.ret, lab.resolved_at) == ("pending", None, None)
    with pytest.raises(ValueError):
        label([bar(100), bar(101)], h=2)


def test_l3_resolved_carried_needs_a_price_at_f_and_records_staleness():                      # Minor 16
    lab, _ = label([bar(100), bar(105), bar(), bar(), bar(), bar()], h=5)
    assert (lab.state, lab.ret, lab.stale_sessions) == ("resolved_carried", D("0.05"), 4)
    lab, _ = label([bar(100), bar(105), bar(unknown=True), bar(), bar(), bar()], h=5)
    assert (lab.state, lab.ret) == ("unresolvable_missing_price", None)


def test_l4_a_split_inside_the_horizon_is_divided_out_even_with_no_trade_on_the_ex_date():    # ML-19
    s = days(7)
    split = [act.Factor(SYM, act.SUBDIVISION, s[2], 1, 2, act.APPLIED, (), ("sp",))]
    lab, _ = label([bar(100), bar(100), bar(), bar(), bar(), bar(51)], h=5, factors=split,
                   statement_instants={"sp": at(50)})
    assert (lab.state, lab.ret) == ("resolved", D("0.02"))                 # 51 / (1/2) / 100 - 1
    assert lab.factor_statement_ids == ("sp",) and lab.resolved_at == at(50)    # the late statement's instant
    carried, _ = label([bar(100), bar(), bar(), bar(), bar(), bar()], h=5, factors=split)
    assert (carried.state, carried.ret) == ("resolved_carried", D("0"))     # no trade since: cum price with itself
    assert carried.factor_statement_ids == ()


def test_l5_unexplained_move_stores_a_provisional_return_and_a_confirmation_resolves_it():   # ML-21
    s = days(3)
    fired = [act.Move(SYM, s[0], s[1], act.UNEXPLAINED, F(-1, 2))]
    lab, _ = label([bar(100), bar(50)] + [bar(50)] * 4, h=5, moves=fired)
    assert (lab.state, lab.ret, lab.provisional) == ("unexplained_move_in_horizon", D("-0.5"), True)
    confirmed = [act.Move(SYM, s[0], s[1], act.CONFIRMED, F(-1, 2), "nac")]
    lab2, _ = label([bar(100), bar(50)] + [bar(50)] * 4, h=5, moves=confirmed, statement_instants={"nac": at(90)})
    assert (lab2.state, lab2.ret, lab2.provisional) == ("resolved", D("-0.5"), False)
    assert lab2.resolved_at == at(90) > lab.resolved_at                     # a later build, a later resolved_at
    key = (SYM, lab.reference_session, 5)
    assert tg.disagreements({key: lab}, {key: lab2}) == [(key, lab.state, lab2.state)]
    assert tg.disagreements({key: lab}, {key: lab}) == []


def test_l6_quality_missing_price_and_unknown_sessions():
    lab, _ = label([bar(100), bar(101, quality="conflicting")] + [bar(102)] * 4, h=5)
    assert (lab.state, lab.ret) == ("unresolvable_quality", None)
    s = days(3)
    bad = [act.Factor(SYM, act.RIGHTS, s[1], None, None, act.UNRESOLVED, (), ("r",))]
    assert label([bar(100), bar(101)] + [bar(102)] * 4, h=5, factors=bad)[0].state == "unresolvable_quality"
    lab, _ = label([bar(100), bar(101)] + [bar(102)] * 4, h=5, gaps={1: 1})
    assert (lab.state, lab.ret) == ("unknown_session_in_horizon", None)
    lab, _ = label([bar(unknown=True), bar(101)], h=1)                       # Minor 13: resolved at once
    assert (lab.state, lab.resolved_at) == ("unresolvable_missing_price", at(0))


def test_l7_precedence_when_several_states_apply():
    s = days(4)
    fired = [act.Move(SYM, s[0], s[1], act.UNEXPLAINED, F(1))]
    lab, _ = label([bar(100), bar(200, quality="conflicting")] + [bar(200)] * 4, h=5, moves=fired)
    assert lab.state == "unresolvable_quality"                               # quality before unexplained move
    lab, _ = label([bar(100)] + [bar(200)] * 5, h=5, gaps={0: 1}, moves=fired)
    assert lab.state == "unknown_session_in_horizon"                         # before quality and moves
    assert tg.STATES.index("pending") < tg.STATES.index("terminated") < tg.STATES.index("unknown_session_in_horizon")


def test_l8_terminated_by_an_identity_event_or_after_the_grace_period():
    s = days(30)
    merger = [act.Factor(SYM, act.MERGER, s[2], 1, 1, act.NO_PRICE_FACTOR, (), ("m",))]
    lab, _ = label([bar(100), bar(101)] + [bar(102)] * 4, h=5, factors=merger)
    assert (lab.state, lab.ret, lab.left_session, lab.last_px_ref) == ("terminated", None, s[2], D("101"))
    policy = tg.TargetPolicy(grace_sessions=3)
    lab, _ = label([bar(100), None, None, None], h=1, policy=policy)
    assert (lab.state, lab.ret) == ("pending", None)                         # it left; the grace period runs
    lab, _ = label([bar(100), None, None, None, None], h=1, policy=policy)
    assert (lab.state, lab.left_session, lab.last_px_ref) == ("terminated", s[1], D("100"))
    back = [bar(100), None, bar(103), bar(103), bar(103), bar(103)]
    assert label(back, h=5, policy=policy)[0].state == "resolved"            # it came back in time


def test_l9_volatility_has_its_own_state_and_reference_revision_is_flagged():                # Minor 1
    prices = [100, 110, 100, 110, 100, 110]
    lab, _ = label([bar(p) for p in prices], h=5)
    with __import__("decimal").localcontext(num.CTX):
        a = D("1.1").ln()
        rs = [a, -a, a, -a, a]
        m = sum(rs) / 5
        expected = (sum((r - m) ** 2 for r in rs) / 4).sqrt()
    assert (lab.state, lab.volatility_name, lab.volatility) == ("resolved", "realised_vol_5", expected.quantize(num.QUANTUM))
    gap_vol, _ = label([bar(100), bar(110), bar(unknown=True), bar(105), bar(100), bar(110)], h=5)
    assert (gap_vol.state, gap_vol.ret, gap_vol.volatility, gap_vol.volatility_state) == (
        "resolved", D("0.1"), None, "unresolvable_missing_price")             # the return is kept
    s, refs, sessions = build([bar(100), bar(110)])
    revised = tg.ReferenceRow(SYM, sessions[0], D("99"), sessions[0])
    lab = tg.label(revised, 1, s, refs, session_instants={})
    assert lab.reference_revised and lab.ret == num.q(D(110) / D(99) - 1)   # the row's close, not the build's


def test_l10_the_feature_path_never_imports_the_target_module():
    from worker.market_features import features, market, series
    for module in (features, market, series):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(node, "module", None) or ""] + [a.name for a in node.names]
                assert not any("targets" in x for x in names), module.__name__
