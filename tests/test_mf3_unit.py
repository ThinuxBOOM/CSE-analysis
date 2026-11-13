"""
Phase 3, step MF-3 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, section 7): corporate-action evidence, factors,
adjustment by source session, the unexplained-move detector. Pure and offline; every expected number is worked by
hand in the test. Each leakage test also asserts the converse.
"""
from datetime import date, datetime, timezone
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from worker.market_features import actions as act
from worker.market_features.modes import AVAILABLE, KNOWN, Query
from worker.market_features.reference import Reference

UTC = timezone.utc
S = [date(2026, 3, d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]          # ten final sessions
SYM = "ABC.N0000"


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


Q = Query(KNOWN, at(S[-1], 23))


def st(sid, kind, ex=None, terms=None, rec=None, ann=None, supersedes=None, symbol=SYM):
    rec = rec or at(S[0], 1)
    return act.statement(sid, symbol, kind, recorded_at=rec, available_at=ann or rec, source_reference="notice " + sid,
                         ex_date=ex, terms=terms, supersedes=supersedes)


def refs(prices):
    """prices: {session index: close}. Builds the reference series by carrying the last close with its session."""
    out, px, src = {}, None, None
    for i, s in enumerate(S):
        if i in prices:
            px, src = D(str(prices[i])), s
        out[s] = Reference(s, px, src, None if px is None else S.index(s) - S.index(src),
                           "known" if px is not None else "no_price_yet")
    return out


def build(statements, prices, query=Q, cutoff=S[-1]):
    return act.build_factors(SYM, statements, query, sessions=[s for s in S if s <= cutoff],
                             references=refs(prices), cutoff_session=cutoff)


def test_a1_statements_are_validated_and_supersession_is_by_name_only():
    for bad in (lambda: st("1", "split", S[3]),
                lambda: st("1", act.SUBDIVISION, None, {"old_shares": 1, "new_shares": 2}),
                lambda: st("1", act.SUBDIVISION, S[3], {"old_shares": 0, "new_shares": 2}),
                lambda: st("1", act.BONUS, S[3], {"held": 4, "new": True}),
                lambda: st("1", act.RIGHTS, S[3], {"held": 4, "new": 1, "subscription_price": 40.0}),
                lambda: st("1", act.WITHDRAWN),
                lambda: st("1", act.WITHDRAWN, supersedes="1"),
                lambda: st("1", act.NO_ACTION_CONFIRMED, terms={"session_from": S[3], "session_to": S[3]}),
                lambda: st("1", act.NO_ACTION_CONFIRMED, terms={"session_from": S[2], "session_to": S[3]},
                           rec=at(S[4]), ann=at(S[3]))):
        with pytest.raises(ValueError):
            bad()
    a = st("a", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 2})
    b = st("b", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 5}, rec=at(S[1]))      # later, names nothing
    c = st("c", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 5}, rec=at(S[1]), supersedes="a")
    assert [s.statement_id for s in act.applying([a, c], Q)[0]] == ["c"]
    assert sorted(s.statement_id for s in act.applying([a, b], Q)[0]) == ["a", "b"]          # arrival order: nothing
    w = st("w", act.WITHDRAWN, rec=at(S[2]), supersedes="c")
    assert act.applying([a, c, w], Q)[0] == []                    # the action is cancelled; "a" stays superseded
    other = st("x", act.WITHDRAWN, rec=at(S[2]), supersedes="a", symbol="XYZ.N0000")
    live, misdirected = act.applying([a, other], Q)
    assert [s.statement_id for s in live] == ["a"] and misdirected == [other]


def test_a2_split_bonus_and_no_price_factor_actions():
    fs = build([st("s", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 2}),
                st("c", act.CONSOLIDATION, S[5], {"old_shares": 10, "new_shares": 1}),
                st("b", act.BONUS, S[6], {"held": 4, "new": 1}),
                st("d", act.CASH_DIVIDEND, S[7]),
                st("m", act.SUSPENSION, S[8])], {0: 100})
    assert [(f.kind, f.value, f.status) for f in fs] == [
        (act.SUBDIVISION, F(1, 2), act.APPLIED), (act.CONSOLIDATION, F(10), act.APPLIED),
        (act.BONUS, F(4, 5), act.APPLIED), (act.CASH_DIVIDEND, F(1), act.NO_PRICE_FACTOR),
        (act.SUSPENSION, F(1), act.NO_PRICE_FACTOR)]
    assert act.adj(D("100"), S[0], S[9], fs) == 400               # 100 * 1/2 * 10 * 4/5
    assert act.adj(D("100"), S[3], S[4], fs) == 100               # printed ON the ex-date: already ex
    assert act.adj(D("100"), S[2], S[3], fs) == 50
    assert act.adj(None, S[0], S[9], fs) is None
    with pytest.raises(ValueError):
        act.adj(D("1"), S[4], S[3], fs)


def test_a3_rights_terp_and_every_edge_case():
    rights = st("r", act.RIGHTS, S[4], {"held": 4, "new": 1, "subscription_price": D("40")})
    (f,) = build([rights], {3: 50})                               # traded on the last cum session at 50
    assert (f.value, f.status, f.flags) == (F(24, 25), act.APPLIED, ())       # TERP (4*50+40)/5 = 48; 48/50
    (f,) = build([rights], {1: 50})                               # last trade two sessions before the cum session
    assert (f.value, f.flags) == (F(24, 25), ("cum_price_stale",))
    (f,) = build([st("r", act.RIGHTS, S[4], {"held": 4, "new": 1, "subscription_price": D("50")})], {3: 50})
    assert (f.value, f.status) == (F(1), act.OUT_OF_THE_MONEY)    # P_sub = P_cum
    (f,) = build([st("r", act.RIGHTS, S[4], {"held": 4, "new": 1, "subscription_price": D("60")})], {3: 50})
    assert (f.value, f.status) == (F(1), act.OUT_OF_THE_MONEY)
    (f,) = build([rights], {5: 50})                               # no price before the ex-date
    assert (f.status, f.value, f.flags) == (act.UNRESOLVED, None, ("no_cum_price",))
    (f,) = build([st("r", act.RIGHTS, S[0], {"held": 4, "new": 1, "subscription_price": D("40")})], {0: 50})
    assert (f.status, f.flags) == (act.UNRESOLVED, ("no_cum_session",))
    unknown = refs({1: 50})
    unknown[S[3]] = Reference(S[3], None, None, None, "price_unknown")        # a conflicting bar leaves this (5.3)
    (f,) = act.build_factors(SYM, [rights], Q, sessions=S, references=unknown, cutoff_session=S[-1])
    assert (f.status, f.flags) == (act.UNRESOLVED, ("cum_price_unknown",))    # never estimated (Minor 11)
    assert act.adj(D("50"), S[1], S[9], [f]) is None              # an unresolved factor in (u, b]: untrusted
    assert act.adj(D("50"), S[4], S[9], [f]) == 50                # outside it: untouched


def test_a4_the_designs_worked_example_a_split_then_rights_with_no_trade_between():                 # ML-19, N1
    split = st("s", act.SUBDIVISION, S[2], {"old_shares": 1, "new_shares": 2})
    rights = st("r", act.RIGHTS, S[5], {"held": 4, "new": 1, "subscription_price": D("40")})
    fs = build([rights, split], {1: 100})                         # last trade 100 before e1, none since
    assert [(f.kind, f.value) for f in fs] == [(act.SUBDIVISION, F(1, 2)), (act.RIGHTS, F(24, 25))]
    assert fs[1].value != F(22, 25)                               # the raw 100 would have given 0.88
    assert fs[1].flags == ("cum_price_stale",)
    assert act.adj(D("100"), S[1], S[9], fs) == 48                # 100 * 0.5 * 0.96
    # an unresolved earlier factor makes the rights factor unresolved too
    dup = st("s2", act.SUBDIVISION, S[2], {"old_shares": 1, "new_shares": 3})
    fs = build([rights, split, dup], {1: 100})
    assert [f.status for f in fs] == [act.UNRESOLVED, act.UNRESOLVED, act.UNRESOLVED]
    assert fs[0].flags == ("conflicting_statements",) and fs[2].flags == ("earlier_factor_unresolved",)
    # same ex-date: the fixed order, sub-division first, then rights on the basis it leaves
    same = build([st("r", act.RIGHTS, S[5], {"held": 4, "new": 1, "subscription_price": D("40")}),
                  st("s", act.SUBDIVISION, S[5], {"old_shares": 1, "new_shares": 2})], {4: 100})
    assert [(f.kind, f.value) for f in same] == [(act.SUBDIVISION, F(1, 2)), (act.RIGHTS, F(24, 25))]


def test_a5_no_trade_on_the_ex_date_gives_no_false_return():                                         # ML-19
    fs = build([st("s", act.SUBDIVISION, S[4], {"old_shares": 1, "new_shares": 2})], {3: 100, 6: 51})
    r = refs({3: 100, 6: 51})
    series = [act.adj(r[s].px_ref, r[s].ref_session, S[9], fs) for s in S[3:8]]
    assert series == [50, 50, 50, 51, 51]                         # carried over the ex-date: still the cum print
    by_bar_date = [F(r[s].px_ref) * (F(1, 2) if s < S[4] else 1) for s in S[3:6]]
    assert by_bar_date == [50, 100, 100]                          # revision 1's error: a 100% false return
    assert act.ex_date_since_last_trade(r[S[5]].ref_session, S[5], fs)
    assert not act.ex_date_since_last_trade(r[S[6]].ref_session, S[6], fs)
    assert not act.ex_date_since_last_trade(S[3], S[5], build([st("d", act.CASH_DIVIDEND, S[4])], {3: 100}))


def test_a6_an_action_recorded_after_the_cutoff_or_with_a_later_ex_date_changes_nothing():          # ML-4, ML-5
    split = dict(kind=act.SUBDIVISION, ex=S[3], terms={"old_shares": 1, "new_shares": 2})
    q = Query(KNOWN, at(S[5], 9))
    late = st("s", rec=at(S[5], 10), ann=at(S[1]), **split)       # ex-date before T, recorded after T
    assert build([late], {0: 100}, q, S[4]) == []
    assert act.adj(D("100"), S[0], S[4], build([late], {0: 100}, q, S[4])) == 100
    on_time = st("s", rec=at(S[5], 9), ann=at(S[1]), **split)
    assert [f.value for f in build([on_time], {0: 100}, q, S[4])] == [F(1, 2)]
    # AVAILABLE: by the announcement and H
    avail = Query(AVAILABLE, at(S[5], 9), at(S[9]))
    assert [f.value for f in build([late], {0: 100}, avail, S[4])] == [F(1, 2)]
    unannounced = st("s", rec=at(S[5], 10), ann=at(S[5], 10), **split)
    assert build([unannounced], {0: 100}, avail, S[4]) == []
    # ML-5: announced before T, ex-date after D*
    future = st("s", act.SUBDIVISION, S[7], {"old_shares": 1, "new_shares": 2})
    assert build([future], {0: 100}, q, S[4]) == []
    assert [f.value for f in build([future], {0: 100}, Q, S[7])] == [F(1, 2)]
    # a withdrawal recorded after T does not withdraw at T
    w = st("w", act.WITHDRAWN, rec=at(S[5], 10), supersedes="s")
    assert [f.value for f in build([on_time, w], {0: 100}, q, S[4])] == [F(1, 2)]
    assert build([on_time, w], {0: 100}, Q) == []


def test_a7_volume_is_adjusted_for_share_count_actions_only():
    split = build([st("s", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 2})], {0: 100})
    assert act.adj_volume(D("1000"), S[2], S[9], split) == (2000, None)
    assert act.adj_volume(D("1000"), S[3], S[9], split) == (1000, None)
    assert act.adj_volume(None, S[2], S[9], split) == (None, None)
    rights = build([st("r", act.RIGHTS, S[4], {"held": 4, "new": 1, "subscription_price": D("40")})], {3: 50})
    assert act.adj_volume(D("1000"), S[2], S[9], rights) == (None, "share_count_changed")
    assert act.adj_volume(D("1000"), S[4], S[9], rights) == (1000, None)
    cash = build([st("d", act.CASH_DIVIDEND, S[4])], {3: 50})
    assert act.adj_volume(D("1000"), S[2], S[9], cash) == (1000, None)


def test_a8_the_detector_compares_after_the_admitted_factors_and_never_infers_an_action():
    closes = [(S[0], D("100")), (S[2], D("101")), (S[4], D("50.5")), (S[6], D("51"))]
    none = act.detect_moves(SYM, closes, [], [], Q, F(1, 4))
    assert [(m.session_from, m.session_to, m.state, m.change) for m in none] == [(S[2], S[4], act.UNEXPLAINED, F(-1, 2))]
    assert act.unlabelled(none, S[3], S[5]) and act.unlabelled(none, S[2], S[4]) and act.unlabelled(none, S[0], S[3])
    assert not act.unlabelled(none, S[4], S[9]) and not act.unlabelled(none, S[0], S[2])
    split = [st("s", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 2})]
    assert act.detect_moves(SYM, closes, build(split, {0: 100}), split, Q, F(1, 4)) == []          # explained
    # a factor-1 action between the closes does not hide an unentered split
    cash = [st("d", act.CASH_DIVIDEND, S[3])]
    assert [m.state for m in act.detect_moves(SYM, closes, build(cash, {0: 100}), cash, Q, F(1, 4))] == [act.UNEXPLAINED]
    # a confirmation is tied to its session pair, and the confirmed move stays stored (Minor 12)
    ok = st("k", act.NO_ACTION_CONFIRMED, terms={"session_from": S[2], "session_to": S[4]}, rec=at(S[6]))
    wrong = st("k2", act.NO_ACTION_CONFIRMED, terms={"session_from": S[0], "session_to": S[4]}, rec=at(S[6]))
    (m,) = act.detect_moves(SYM, closes, [], [ok], Q, F(1, 4))
    assert (m.state, m.statement_id, m.change) == (act.CONFIRMED, "k", F(-1, 2)) and not act.unlabelled([m], S[0], S[9])
    assert [m.state for m in act.detect_moves(SYM, closes, [], [wrong], Q, F(1, 4))] == [act.UNEXPLAINED]
    # ... and it is evidence like any other: recorded after T, it confirms nothing at T (ML-21's ground)
    assert [m.state for m in act.detect_moves(SYM, closes, [], [ok], Query(KNOWN, at(S[5])), F(1, 4))] == [act.UNEXPLAINED]
    # an unresolved factor between two closes: not evaluable, recorded as such
    r = [st("r", act.RIGHTS, S[3], {"held": 4, "new": 1, "subscription_price": D("40")})]
    fs = act.build_factors(SYM, r, Q, sessions=S, references={}, cutoff_session=S[-1])
    moves = act.detect_moves(SYM, closes, fs, r, Q, F(1, 4))
    assert [(m.session_to, m.state, m.change) for m in moves] == [(S[4], act.NOT_EVALUABLE, None)]
    assert act.detect_moves(SYM, closes, [], [], Q, F(3, 4)) == []                 # below the threshold
    with pytest.raises(ValueError):
        act.detect_moves(SYM, closes, [], [], Q, 0)
    with pytest.raises(ValueError):
        act.detect_moves(SYM, [(S[2], D("1")), (S[2], D("1"))], [], [], Q, F(1, 4))


def test_a9_the_adjustment_set_hash_covers_the_whole_factor_list_and_no_order():
    a = build([st("s", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 2}), st("d", act.CASH_DIVIDEND, S[5])],
              {0: 100})
    h = act.adjustment_set_hash({SYM: a})
    assert len(h) == 64 and h == act.adjustment_set_hash({SYM: list(reversed(a))})
    assert h != act.adjustment_set_hash({SYM: a[:1]})             # the factor-1 row is part of the set
    b = build([st("s", act.SUBDIVISION, S[3], {"old_shares": 1, "new_shares": 4}), st("d", act.CASH_DIVIDEND, S[5])],
              {0: 100})
    assert h != act.adjustment_set_hash({SYM: b})
    assert act.adjustment_set_hash({}) == act.adjustment_set_hash({SYM: []})


def test_a10_the_module_is_pure():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(act))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module.split(".")[0] if node.level == 0 else "<package>")
    assert mods <= {"dataclasses", "datetime", "decimal", "fractions", "typing", "<package>"}
