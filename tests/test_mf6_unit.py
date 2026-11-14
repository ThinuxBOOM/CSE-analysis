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


# ------------------------------------------------------------------- added after audit 3 (C-0047)

def test_l11_every_absence_in_the_horizon_is_examined():                                             # B-T1
    policy = tg.TargetPolicy(grace_sessions=3)
    gone = [bar(100), None, bar(103), None, None, None, None, None, None, None]
    lab, s = label(gone, h=5, policy=policy)
    assert (lab.state, lab.ret, lab.left_session) == ("terminated", None, s[3])
    running = [bar(100), None, bar(103), None, None, None]
    assert label(running, h=5, policy=policy)[0].state == "pending"


def test_l12_resolved_at_covers_a_price_carried_from_before_d():                                    # B-T2
    s, refs, sessions = build([bar(100), bar(), bar()])
    row_ = tg.ReferenceRow(SYM, sessions[1], refs[sessions[1]].px_ref, refs[sessions[1]].ref_session)
    inst = {sessions[0]: at(100), sessions[1]: at(1), sessions[2]: at(2)}
    lab = tg.label(row_, 1, s, refs, session_instants=inst)
    assert (lab.state, lab.resolved_at) == ("resolved_carried", at(100))


def test_l13_quality_factor_windows_and_the_reference_bar():                                        # MJ-T1, MJ-T2
    s = days(10)
    after_f = [act.Factor(SYM, act.RIGHTS, s[8], None, None, act.UNRESOLVED)]
    lab, _ = label([bar(100)] * 5 + [bar(105)] * 5, h=5, factors=after_f)
    assert (lab.state, lab.ret) == ("resolved", D("0.05"))           # an unresolved factor after F is not in it
    sb, refs, sessions = build([bar(100), bar(110)])
    conflicting_d = tg.ReferenceRow(SYM, sessions[0], D("100"), sessions[0], source_quality="conflicting")
    assert tg.label(conflicting_d, 1, sb, refs, session_instants={}).state == "unresolvable_quality"
    sb, refs, sessions = build([bar(100), bar(110, quality="conflicting")])
    assert tg.label(row(refs, sessions[0]), 1, sb, refs, session_instants={}).state == "unresolvable_quality"


def test_l14_factor_boundaries_precedence_and_small_cases():                                       # MJ-T3
    s = days(7)
    on_sd = [act.Factor(SYM, act.SUBDIVISION, s[0], 1, 2, act.APPLIED)]
    lab, _ = label([bar(50)] * 6, h=5, factors=on_sd)
    assert lab.ret == 0                                              # an ex-date on s_D is already in P_D
    on_sf = [act.Factor(SYM, act.SUBDIVISION, s[5], 1, 2, act.APPLIED)]
    lab, _ = label([bar(100)] * 5 + [bar(50)], h=5, factors=on_sf)
    assert (lab.state, lab.ret) == ("resolved", D("0"))              # an ex-date on s_F, traded at F, is divided out
    policy = tg.TargetPolicy(grace_sessions=3)
    back_at_end = [bar(100), None, None, None, bar(101), bar(101)]
    assert label(back_at_end, h=5, policy=policy)[0].state == "resolved"     # back on the grace window's last session
    gapped_and_gone = [bar(100), None, None, None, None, None, None]
    assert label(gapped_and_gone, h=5, gaps={0: 1}, policy=policy)[0].state == "terminated"
    conflicting_and_missing = [bar(100), bar(101, quality="conflicting")] + [bar(102)] * 3 + [bar(unknown=True)]
    assert label(conflicting_and_missing, h=5)[0].state == "unresolvable_missing_price"
    one_stale = [bar(100)] * 5 + [bar()]
    lab, _ = label(one_stale, h=5)
    assert (lab.state, lab.stale_sessions) == ("resolved_carried", 1)
    fall, _ = label([bar(100), bar(90)], h=1)
    assert fall.volatility == num.q(abs(num.ln(D("0.9"))))         # abs_ret_1 of a fall is positive
    sb, refs, sessions = build([bar(100), bar(), bar(110)])
    moved = tg.ReferenceRow(SYM, sessions[1], D("100"), sessions[1])    # same price, other source session
    assert tg.label(moved, 1, sb, refs, session_instants={}).reference_revised
    lab, _ = label([bar(unknown=True), bar(101)], h=5)
    assert lab.state == "unresolvable_missing_price"                 # Minor 13 before pending
    missing_f, _ = label([bar(100)] * 5 + [bar(unknown=True)], h=5)
    assert missing_f.volatility_state == "unresolvable_missing_price" and missing_f.volatility is None


def test_l15_a_conflicting_close_carried_from_before_d_and_the_builds_own_view_of_p_d():
    sb, refs, sessions = build([bar(100, quality="conflicting"), bar(), bar()])
    lab = tg.label(row(refs, sessions[1]), 1, sb, refs, session_instants={})
    assert lab.state == "unresolvable_quality"                       # P_F carried from that bar (future source)
    sb, refs, sessions = build([bar(100), bar(), bar(110)])
    stored = tg.ReferenceRow(SYM, sessions[1], D("100"), sessions[0])
    inst = {sessions[0]: at(100), sessions[1]: at(1), sessions[2]: at(2)}
    lab = tg.label(stored, 1, sb, refs, session_instants=inst)
    assert (lab.state, lab.resolved_at) == ("resolved", at(100))    # the build's own P_D evidence is an input


# ------------------------------------------------------------------- added after audit 4 (C-0048)

def test_l16_resolved_at_covers_the_deciding_unresolved_factor_and_the_builds_conflicting_d():      # B-T3, B-T4
    s = days(7)
    bad = [act.Factor(SYM, act.RIGHTS, s[4], None, None, act.UNRESOLVED, (), ("r",))]
    lab, _ = label([bar(100), bar(100), bar(105), bar(), bar(), bar(), bar()], d_index=1, h=5, factors=bad,
                   statement_instants={"r": at(500)})
    assert (lab.state, lab.resolved_at) == ("unresolvable_quality", at(500))
    sb, refs, sessions = build([bar(100, quality="conflicting"), bar(110), bar(100), bar(110), bar(100), bar(110)])
    fresh_row = tg.ReferenceRow(SYM, sessions[0], D("100"), sessions[0], source_quality="fresh")
    lab = tg.label(fresh_row, 5, sb, refs, session_instants={})
    assert (lab.state, lab.volatility) == ("unresolvable_quality", None)   # the build's own D bar conflicts


def test_l17_an_absence_running_at_d_and_pending_before_an_identity_event():
    policy = tg.TargetPolicy(grace_sessions=3)
    lab, s = label([bar(100)] + [None] * 9, d_index=1, h=5, policy=policy)
    assert (lab.state, lab.left_session, lab.last_px_ref) == ("terminated", s[1], D("100"))
    ss = days(5)
    merger = [act.Factor(SYM, act.MERGER, ss[3], 1, 1, act.NO_PRICE_FACTOR, (), ("m",))]
    lab, _ = label([bar(100), None, None, None, None], h=1, factors=merger,
                   policy=tg.TargetPolicy(grace_sessions=21))
    assert lab.state == "pending"                                    # the earlier absence's grace period runs first


# ------------------------------------------------------------------- added after audit 5 (C-0050)

def test_l18_resolved_at_reaches_every_session_and_statement_that_decided_the_label():   # B-T5, B-T6, B-T7, M0
    policy = tg.TargetPolicy(grace_sessions=3)
    lab, _ = label([bar(100), None, None, bar(101)], h=1, policy=policy)
    assert (lab.state, lab.resolved_at) == ("resolved_carried", at(3))  # the return after F decided it
    sb, refs, sessions = build([bar(100), bar(unknown=True), bar(), bar()])
    stored = tg.ReferenceRow(SYM, sessions[2], D("100"), sessions[0])
    inst = {sessions[0]: at(0), sessions[1]: at(500), sessions[2]: at(2), sessions[3]: at(3)}
    lab = tg.label(stored, 1, sb, refs, session_instants=inst)
    assert (lab.state, lab.resolved_at) == ("unresolvable_missing_price", at(500))   # the unknown bar before D
    s = days(10)
    after_f = [act.Factor(SYM, act.RIGHTS, s[8], None, None, act.UNRESOLVED, (), ("r9",))]
    prices = [100, 110, 100, 110, 100, 110, 110, 110, 110, 110]
    lab, _ = label([bar(p) for p in prices], h=5, factors=after_f, statement_instants={"r9": at(900)})
    plain, _ = label([bar(p) for p in prices], h=5)
    assert lab.volatility == plain.volatility is not None              # a factor after F changes nothing (F basis)
    assert lab.resolved_at == at(900)                                  # and its statement is still covered
    gone, _ = label([bar(100)] + [None] * 10, h=10, policy=policy)
    assert (gone.state, gone.resolved_at) == ("terminated", at(10))    # waits for F's finality (M0)


def test_l19_contract_and_event_details():                                                         # M1 to M5
    lab, _ = label([bar(100), bar(110)], h=1, extra_instants=(at(700),))
    assert lab.resolved_at == at(700)                                  # a withdrawal's instant, via the contract
    s = days(6)
    on_f = [act.Factor(SYM, act.DELISTING, s[1], 1, 1, act.NO_PRICE_FACTOR, (), ("dl",))]
    lab, _ = label([bar(100), bar(101)], h=1, factors=on_f, statement_instants={"dl": at(800)})
    assert (lab.state, lab.left_session, lab.resolved_at) == ("terminated", s[1], at(800))
    two = [act.Factor(SYM, act.MERGER, s[4], 1, 1, act.NO_PRICE_FACTOR, (), ("m",)),
           act.Factor(SYM, act.SUSPENSION, s[2], 1, 1, act.NO_PRICE_FACTOR, (), ("su",))]
    lab, _ = label([bar(100)] * 6, h=5, factors=two)
    assert (lab.state, lab.left_session, lab.evidence[0]) == ("terminated", s[2], act.SUSPENSION)
    carried_conflict = [bar(100, quality="conflicting"), bar(), bar(105), bar(106), bar(107), bar(108), bar(109)]
    sb, refs, sessions = build(carried_conflict)
    fresh_row = tg.ReferenceRow(SYM, sessions[1], D("100"), sessions[0], source_quality="fresh")
    assert tg.label(fresh_row, 5, sb, refs, session_instants={}).state == "unresolvable_quality"   # M4
    no_trade_conflict_at_f = [bar(100), bar(101)] + [bar(102)] * 3 + [FeatureBar(
        symbol=SYM, row=3, traded=False, px_close=None, price_unknown=False, share_volume=D(0), trade_count=None,
        turnover=None, open_price=None, high=None, low=None, quality="conflicting")]
    assert label(no_trade_conflict_at_f, h=5)[0].state == "unresolvable_quality"                     # M5
    one, _ = label([bar(100), bar(90)], h=1)
    assert one.volatility_state == "resolved"


# ------------------------------------------------------------------- added after audit 6 (C-0052)

def test_l20_a_revised_row_reads_back_to_its_own_s_d():                                               # B-T8
    sb, refs, sessions = build([bar(100), bar(50), bar(100), bar(100), bar(100)])
    moves = [act.Move(SYM, sessions[0], sessions[1], act.UNEXPLAINED, F(-1, 2)),
             act.Move(SYM, sessions[1], sessions[2], act.UNEXPLAINED, F(1))]
    sb = sr.Series(sb.symbol, sb.sequence, sb.points, sb.bars, sb.factors, tuple(moves))
    stored = tg.ReferenceRow(SYM, sessions[3], D("100"), sessions[0])
    inst = {x: at(300) for x in sessions}
    inst[sessions[1]] = at(500)
    lab = tg.label(stored, 1, sb, refs, session_instants=inst)
    assert (lab.state, lab.resolved_at) == ("unexplained_move_in_horizon", at(500))
    assert lab.reference_revised


def test_l21_revised_row_details_unlabelled_window_and_volatility_quality():                         # M-b
    sb, refs, sessions = build([bar(100), bar(50), bar(50), bar(50)])
    move = [act.Move(SYM, sessions[0], sessions[1], act.UNEXPLAINED, F(-1, 2))]
    sb = sr.Series(sb.symbol, sb.sequence, sb.points, sb.bars, sb.factors, tuple(move))
    stored = tg.ReferenceRow(SYM, sessions[2], D("100"), sessions[0])        # s_D before the move, D after it
    lab = tg.label(stored, 1, sb, refs, session_instants={})
    assert lab.state == "unexplained_move_in_horizon"                # the window starts at the row's s_D
    s = days(7)
    bad = [act.Factor(SYM, act.RIGHTS, s[2], None, None, act.UNRESOLVED)]
    sb, refs, sessions = build([bar(100)] * 7, factors=bad)
    v, state = tg._volatility(sb, refs, 0, 5, 5)
    assert (v, state) == (None, "unresolvable_quality")              # an unresolved factor crossed, not a missing price
    div = [act.Factor(SYM, act.CASH_DIVIDEND, s[2], 1, 1, act.NO_PRICE_FACTOR, (), ("dv",))]
    lab, _ = label([bar(100)] * 6, h=5, factors=div)
    assert lab.factor_statement_ids == ()                            # a no-price factor is not in the adjustment set


def test_l22_each_part_of_the_revised_row_rule_alone():                                             # B-T8
    # a move that starts before the stored row's s_D (it straddles s_D): only the move rule reaches its first bar
    sb, refs, sessions = build([bar(100), bar(unknown=True), bar(50), bar(50), bar(50)])
    move = [act.Move(SYM, sessions[0], sessions[2], act.UNEXPLAINED, F(-1, 2))]
    sb = sr.Series(sb.symbol, sb.sequence, sb.points, sb.bars, sb.factors, tuple(move))
    stored = tg.ReferenceRow(SYM, sessions[3], D("50"), sessions[1])
    inst = {x: at(300) for x in sessions}
    inst[sessions[0]] = at(500)
    lab = tg.label(stored, 1, sb, refs, session_instants=inst)
    assert (lab.state, lab.resolved_at) == ("unexplained_move_in_horizon", at(500))
    # an action inside the stretch the stored row reads: only the row's s_D rule reaches the bars behind its factor
    sb, refs, sessions = build([bar(100), bar(100), bar(48), bar(48), bar(48)],
                               factors=[act.Factor(SYM, act.RIGHTS, days(5)[2], 24, 25, act.APPLIED, (), ("rt",))])
    stored = tg.ReferenceRow(SYM, sessions[3], D("50"), sessions[0])
    inst = {x: at(300) for x in sessions}
    inst[sessions[1]] = at(500)
    lab = tg.label(stored, 1, sb, refs, session_instants=inst)
    assert lab.resolved_at == at(500)


# ------------------------------------------------------------------- added after audit 7 (C-0054)

def test_l23_an_unexplained_move_comes_before_resolved_carried():                                   # M-d
    s = days(6)
    move = [act.Move(SYM, s[0], s[1], act.UNEXPLAINED, F(-1, 2))]
    lab, _ = label([bar(100), bar(50), bar(), bar(), bar(), bar()], h=5, moves=move)
    assert lab.state == "unexplained_move_in_horizon"                # F is carried, but the move comes first (§11)


def _detected(bars, sessions):
    closes = [(x, b.px_close) for x, b in zip(sessions, bars) if b is not None and b.px_close is not None]
    return tuple(act.Move(SYM, u, w, act.UNEXPLAINED, F(q) / F(p) - 1)
                 for (u, p), (w, q) in zip(closes, closes[1:]) if abs(F(q) / F(p) - 1) > F(1, 4))


def test_l24_resolved_at_reaches_the_last_print_before_a_revised_rows_s_d():                       # B-T9
    for first in (100, 50):                                          # 50 makes the pair (s0, s2) fire
        bars = [bar(first), bar(unknown=True), bar(95), bar(95), bar(95)]
        sb, refs, sessions = build(bars)
        sb = sr.Series(sb.symbol, sb.sequence, sb.points, sb.bars, sb.factors, _detected(bars, sessions))
        stored = tg.ReferenceRow(SYM, sessions[3], D("95"), sessions[1])
        inst = {x: at(300) for x in sessions}
        inst[sessions[0]] = at(500)
        lab = tg.label(stored, 1, sb, refs, session_instants=inst)
        assert lab.resolved_at == at(500), (first, lab.state)          # bar s0 decided the state either way
    # the value: a rights cum price carried from a bar before the row's s_D
    s = days(5)
    for p0 in (100, 80):
        bars = [bar(p0), bar(), bar(96), bar(96), bar(96)]
        sessions = days(5)
        refs = {r.session: r for r in reference_series(list(zip(sessions, bars)))}

        class St:
            statement_id, ex_date = "rt", s[2]

            def term(self, k):
                return {"held": 1, "new": 1, "subscription_price": D(40)}[k]
        factors = [act._rights(SYM, St(), [], sessions, refs)]
        sb, refs, sessions = build(bars, factors=factors)
        stored = tg.ReferenceRow(SYM, sessions[3], D("100"), sessions[1])
        inst = {x: at(300) for x in sessions}
        inst[sessions[0]] = at(500)
        assert tg.label(stored, 1, sb, refs, session_instants=inst).resolved_at == at(500)
