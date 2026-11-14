"""
Phase 3, step MF-4 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, sections 8 and 9): identity, the universe of
a session, market-data eligibility. Pure and offline.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from worker.market_features import universe as uni
from worker.market_features.bars import FeatureBar
from worker.market_features.canonical import security_id
from worker.market_features.modes import AVAILABLE, KNOWN, Query
from worker.market_features.reference import Reference

UTC = timezone.utc
SYM = "ABC.N0000"
D0 = date(2026, 1, 5)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def bar(traded=True, turnover="500000", quality="fresh", flags=()):
    return FeatureBar(symbol=SYM, row=1 if traded else 3, traded=traded, px_close=D("10") if traded else None,
                      price_unknown=False, share_volume=None, trade_count=None,
                      turnover=None if turnover is None else D(turnover), open_price=None, high=None, low=None,
                      quality=quality, flags=flags)


def ref(stale=0, px="10"):
    return Reference(D0, None if px is None else D(px), None if px is None else D0, None if px is None else stale,
                     "known" if px is not None else "price_unknown")


def test_u1_the_class_table_only_voting_and_non_voting_shares_are_equity_lines():
    assert uni.security_class("ABC.N0000") == "ordinary_voting" and uni.is_equity_line("ABC.N0000")
    assert uni.security_class("ABC.X0000") == "ordinary_non_voting" and uni.is_equity_line("ABC.X0000")
    for s, c in (("ABC.R0000", "rights"), ("ABC.W0012", "warrant"), ("ABC.P0000", "preference"),
                 ("ABC.D0001", "debenture"), ("ABC.U0000", "class_U"), ("ABC.B0000", "class_B")):
        assert uni.security_class(s) == c and not uni.is_equity_line(s)
    for s in ("ABC.Z0000", "ABC", "abc.N0000", "ABC.N000", "", None, "ABC.N0000 "):
        assert uni.security_class(s) == uni.UNRECOGNISED and not uni.is_equity_line(s)       # in the universe, n/a


def test_u2_the_universe_is_the_union_and_the_active_flag_never_filters():
    u = uni.universe({"ABC.N0000": True, "DEF.N0000": False, "GHI.R0000": None}, ["ABC.N0000", "JKL.X0000"],
                     {"ABC.N0000": 7})
    assert sorted(u) == ["ABC.N0000", "DEF.N0000", "GHI.R0000", "JKL.X0000"]
    assert u["DEF.N0000"].active is False and u["DEF.N0000"].equity_line          # inactive: still a member
    assert (u["JKL.X0000"].in_all_security_code, u["JKL.X0000"].in_trade_summary, u["JKL.X0000"].active) == (False, True, None)
    assert not u["GHI.R0000"].equity_line and u["ABC.N0000"].company_id == 7 and u["DEF.N0000"].company_id is None
    # the key is computed from the symbol: a security P2 could not match has a key like any other
    assert security_id("JKL.X0000") == security_id("JKL.X0000") != security_id("JKL.N0000")


def test_u3_survivorship_a_dataset_carries_the_worst_label_of_its_eras():
    assert uni.worst_survivorship(["complete", "complete"]) == "complete"
    assert uni.worst_survivorship(["complete", "survivors_only"]) == "survivors_only"
    assert uni.worst_survivorship(["survivors_only", "unknown", "complete"]) == "unknown"
    for bad in ([], ["complete", "partial"]):
        with pytest.raises(ValueError):
            uni.worst_survivorship(bad)


def test_u4_a_link_joins_two_series_only_from_its_admission_and_a_reuse_splits_one():
    link = uni.IdentityObservation(uni.LINK, "NEW.N0000", date(2026, 6, 1), at(date(2026, 6, 10)),
                                   at(date(2026, 5, 20)), "CSE notice", predecessor="OLD.N0000")
    Seg = uni.Segment
    joined = [Seg("OLD.N0000", None, date(2026, 6, 1)), Seg("NEW.N0000", date(2026, 6, 1), None)]
    alone = [Seg("NEW.N0000", None, None)]
    assert uni.series_segments("NEW.N0000", [], Query(KNOWN, at(date(2026, 7, 1)))) == alone      # nothing automatic
    assert uni.series_segments("NEW.N0000", [link], Query(KNOWN, at(date(2026, 6, 9)))) == alone  # recorded later
    assert uni.series_segments("NEW.N0000", [link], Query(KNOWN, at(date(2026, 6, 10)))) == joined
    avail = Query(AVAILABLE, at(date(2026, 5, 20)), at(date(2026, 7, 1)))
    assert uni.series_segments("NEW.N0000", [link], avail) == joined                              # by announcement
    assert uni.series_segments("NEW.N0000", [link],
                               Query(AVAILABLE, at(date(2026, 5, 19)), at(date(2026, 7, 1)))) == alone
    assert uni.series_segments("OLD.N0000", [link], Query(KNOWN, at(date(2026, 7, 1)))) == [Seg("OLD.N0000", None, None)]
    reuse = uni.IdentityObservation(uni.SYMBOL_REUSED, "OLD.N0000", date(2024, 1, 1), at(date(2026, 1, 1)),
                                    at(date(2024, 1, 1)), "CSE notice")
    q = Query(KNOWN, at(date(2026, 7, 1)))
    assert uni.series_segments("NEW.N0000", [link, reuse], q) == [Seg("OLD.N0000", date(2024, 1, 1), date(2026, 6, 1)),
                                                                  joined[1]]
    other = uni.IdentityObservation(uni.LINK, "NEW.N0000", date(2026, 6, 1), at(date(2026, 6, 10)),
                                    at(date(2026, 5, 20)), "x", predecessor="ELSE.N0000")
    with pytest.raises(ValueError):
        uni.series_segments("NEW.N0000", [link, other], q)
    back = uni.IdentityObservation(uni.LINK, "OLD.N0000", date(2025, 1, 1), at(date(2026, 6, 10)),
                                   at(date(2026, 5, 20)), "x", predecessor="NEW.N0000")
    with pytest.raises(ValueError):
        uni.series_segments("NEW.N0000", [link, back], q)
    for bad in (lambda: uni.IdentityObservation("rename", "A.N0000", D0, at(D0), at(D0), "x"),
                lambda: uni.IdentityObservation(uni.LINK, "A.N0000", D0, at(D0), at(D0), "x"),
                lambda: uni.IdentityObservation(uni.LINK, "A.N0000", D0, at(D0), at(D0), "x", predecessor="A.N0000"),
                lambda: uni.IdentityObservation(uni.SYMBOL_REUSED, "A.N0000", D0, at(D0), at(D0), "x", predecessor="B.N0000"),
                lambda: uni.IdentityObservation(uni.SYMBOL_REUSED, "A.N0000", D0, at(D0), at(D0), "")):
        with pytest.raises(ValueError):
            bad()


def test_e1_eligible_and_each_single_failure_with_its_measured_values():
    good = [bar()] * 252
    e = uni.eligibility(SYM, good, ref())
    assert (e.result, e.failed, e.history_sessions, e.traded_ratio, e.median_turnover, e.stale_sessions,
            e.quality_blocks) == (uni.ELIGIBLE, (), 252, F(1), D("500000"), 0, 0)
    assert uni.eligibility("ABC.R0000", good, ref()).result == uni.NOT_APPLICABLE
    assert uni.eligibility(SYM, [None] * 300, ref()).result == uni.NO_DATA
    assert uni.eligibility(SYM, [], None).result == uni.NO_DATA
    e = uni.eligibility(SYM, good[:251], ref())
    assert (e.result, e.history_sessions) == (uni.INSUFFICIENT_HISTORY, 251)
    assert uni.eligibility(SYM, [None] * 50 + good, ref()).result == uni.ELIGIBLE     # sessions without a bar: not history
    # activity: 31 traded of the last 63 is below one half; 32 is not
    e = uni.eligibility(SYM, [bar()] * 189 + [bar(False, None)] * 32 + [bar()] * 31, ref())
    assert (e.result, e.traded_ratio) == (uni.LOW_ACTIVITY, F(31, 63))
    assert uni.eligibility(SYM, [bar()] * 189 + [bar(False, None)] * 31 + [bar()] * 32, ref()).result == uni.ELIGIBLE
    # liquidity: the median turnover of the traded sessions of the window
    e = uni.eligibility(SYM, [bar()] * 189 + [bar(turnover="99999.99")] * 63, ref())
    assert (e.result, e.median_turnover) == (uni.LOW_LIQUIDITY, D("99999.99"))       # revision 6: its own result
    assert uni.eligibility(SYM, [bar()] * 189 + [bar(turnover="100000")] * 63, ref()).result == uni.ELIGIBLE
    assert uni.eligibility(SYM, [bar()] * 189 + [bar(turnover=None)] * 63, ref()).result == uni.LOW_LIQUIDITY
    # freshness
    assert uni.eligibility(SYM, good, ref(stale=5)).result == uni.ELIGIBLE
    e = uni.eligibility(SYM, good, ref(stale=6))
    assert (e.result, e.stale_sessions) == (uni.STALE, 6)
    # quality: a flagged bar inside the block window blocks; an older one does not
    e = uni.eligibility(SYM, [bar()] * 231 + [bar(quality="conflicting")] + [bar()] * 20, ref())
    assert (e.result, e.quality_blocks) == (uni.BLOCKED_QUALITY, 1)
    assert uni.eligibility(SYM, [bar()] * 230 + [bar(quality="conflicting")] + [bar()] * 21, ref()).result == uni.ELIGIBLE
    assert uni.eligibility(SYM, good[:-1] + [bar(flags=("untrusted",))], ref()).result == uni.BLOCKED_QUALITY


def test_e2_precedence_all_failures_are_listed_and_freshness_is_not_evaluated_while_the_price_is_unknown():
    e = uni.eligibility(SYM, [bar(False, None)] * 100 + [bar(quality="conflicting")], ref(px=None))
    assert e.result == uni.PRICE_UNKNOWN
    assert e.failed == (uni.PRICE_UNKNOWN, uni.INSUFFICIENT_HISTORY, uni.BLOCKED_QUALITY, uni.LOW_ACTIVITY)
    assert e.stale_sessions is None and uni.STALE not in e.failed                              # Minor 3
    assert uni.eligibility(SYM, [bar()] * 252, None).result == uni.PRICE_UNKNOWN
    e = uni.eligibility(SYM, [bar(False, None)] * 252, ref(stale=300))
    assert (e.result, e.failed) == (uni.LOW_ACTIVITY, (uni.LOW_ACTIVITY, uni.LOW_LIQUIDITY, uni.STALE))


def test_e3_a_bar_whose_trading_is_unknown_is_left_out_of_the_activity_ratio():                 # Minor 5
    unknown = FeatureBar(symbol=SYM, row=5, traded=None, px_close=None, price_unknown=True, share_volume=None,
                         trade_count=None, turnover=None, open_price=None, high=None, low=None, quality="missing")
    e = uni.eligibility(SYM, [bar()] * 189 + [unknown] * 3 + [bar(False, None)] * 30 + [bar()] * 30, ref())
    assert (e.traded_ratio, e.activity_sessions_known, e.result) == (F(30, 60), 60, uni.ELIGIBLE)
    e = uni.eligibility(SYM, [bar()] * 189 + [unknown] * 63, ref())
    assert (e.traded_ratio, e.result) == (None, uni.LOW_ACTIVITY)


def test_e4_the_policy_is_provisional_and_its_values_are_the_designs():
    p = uni.EligibilityPolicy()
    assert (p.status, p.min_history_sessions, p.activity_window, p.min_traded_ratio, p.min_median_turnover_lkr,
            p.max_stale_sessions, p.quality_block_window) == ("provisional", 252, 63, F(1, 2), D("100000"), 5, 21)
    lenient = uni.EligibilityPolicy(min_history_sessions=10)
    assert uni.eligibility(SYM, [bar()] * 10, ref(), lenient).result == uni.ELIGIBLE


# ------------------------------------------------------------------- added after audit 2 of MF-4 and MF-5 (C-0043)

def test_x1_an_untrusted_session_blocks_through_the_real_series():                                         # M3
    from worker.market_features import actions as act
    from worker.market_features import series as sr
    from worker.market_features.calendar import SessionSequence
    from worker.market_features.reference import reference_series
    sessions = [D0 + timedelta(days=i) for i in range(400) if (D0 + timedelta(days=i)).weekday() < 5][:260]
    from decimal import Decimal as Dd
    priced = [FeatureBar(symbol=SYM, row=1, traded=True, px_close=Dd("10"), price_unknown=False, share_volume=Dd(1),
                         trade_count=None, turnover=Dd("500000"), open_price=None, high=None, low=None, quality="fresh")
              for _ in sessions]
    refs = {r.session: r for r in reference_series(list(zip(sessions, priced)))}
    seq = SessionSequence(tuple(sessions), tuple(() for _ in sessions[1:]))
    unresolved = [act.Factor(SYM, act.RIGHTS, sessions[-3], None, None, act.UNRESOLVED)]
    s = sr.build_series(SYM, seq, dict(zip(sessions, priced)), refs, unresolved)
    assert uni.eligibility(SYM, priced, refs[sessions[-1]]).result == uni.ELIGIBLE
    e = uni.eligibility(SYM, priced, refs[sessions[-1]], uni.EligibilityPolicy(), s.points)
    assert (e.result, e.quality_blocks) == (uni.BLOCKED_QUALITY, 18)    # the sessions before the ex-date


def test_x2_link_filters_and_the_median_of_an_even_count():                                              # M4
    q = Query(KNOWN, at(date(2026, 12, 1)))
    Seg = uni.Segment
    link = uni.IdentityObservation(uni.LINK, "NEW.N0000", date(2026, 6, 1), at(date(2026, 6, 2)),
                                   at(date(2026, 5, 20)), "n", predecessor="OLD.N0000")
    reuse = uni.IdentityObservation(uni.SYMBOL_REUSED, "NEW.N0000", date(2026, 8, 1), at(date(2026, 8, 2)),
                                    at(date(2026, 8, 1)), "n")
    assert uni.series_segments("NEW.N0000", [link, reuse], q) == [Seg("NEW.N0000", date(2026, 8, 1), None)]
    later = uni.IdentityObservation(uni.LINK, "OLD.N0000", date(2026, 7, 1), at(date(2026, 7, 2)),
                                    at(date(2026, 6, 20)), "n", predecessor="OLDER.N0000")
    assert uni.series_segments("NEW.N0000", [link, later], q) == [Seg("OLD.N0000", None, date(2026, 6, 1)),
                                                                  Seg("NEW.N0000", date(2026, 6, 1), None)]
    window = [bar(turnover="90000"), bar(turnover="120000")] * 31 + [bar(turnover="100000")]
    assert uni.eligibility(SYM, [bar()] * 189 + window, ref()).median_turnover == D("100000")      # odd: the middle
    even = [bar(False, None)] + [bar(turnover="90000"), bar(turnover="120000")] * 31          # 62 traded sessions
    e = uni.eligibility(SYM, [bar()] * 189 + even, ref())
    assert (e.activity_sessions_known, e.median_turnover) == (63, D("105000"))      # the mean of the two middle
