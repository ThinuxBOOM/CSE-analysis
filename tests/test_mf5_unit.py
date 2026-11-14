"""
Phase 3, step MF-5 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, section 10): MF's arithmetic, the carried
adjusted series, feature set v1 and its quality states, market context. Pure and offline; expected values are worked
in the test with exact or Decimal arithmetic independent of the code under test.
"""
import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from decimal import localcontext
from fractions import Fraction as F

import pytest

from worker.market_features import actions as act
from worker.market_features import features as ft
from worker.market_features import market as mk
from worker.market_features import numeric as num
from worker.market_features import series as sr
from worker.market_features.bars import FeatureBar
from worker.market_features.calendar import SessionSequence
from worker.market_features.modes import KNOWN, Query
from worker.market_features.reference import reference_series
from worker.market_features.universe import EligibilityPolicy

UTC = timezone.utc
SYM = "ABC.N0000"
Q12 = D("0.000000000001")


def days(n):
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(close=None, *, traded=True, volume="1000", turnover="500000", trades="10", high=None, low=None, open_=None,
        quality="fresh", unknown=False, cap=None, symbol=SYM):
    if close is None and not unknown:
        traded, volume, turnover, trades = False, None, None, None
    px = None if close is None else D(str(close))
    return FeatureBar(symbol=symbol, row=1, traded=None if (unknown and close is None and traded is None) else traded,
                      px_close=px, price_unknown=unknown,
                      share_volume=None if volume is None else D(volume), trade_count=None if trades is None else D(trades),
                      turnover=None if turnover is None else D(turnover),
                      open_price=None if open_ is None else D(str(open_)), high=None if high is None else D(str(high)),
                      low=None if low is None else D(str(low)), quality=quality,
                      market_cap_reported=None if cap is None else D(str(cap)))


def build(bars, *, gaps=None, factors=(), moves=(), symbol=SYM):
    """bars: one FeatureBar (or None) per session. gaps: {index: n} = n gap dates between session index and index+1."""
    sessions = days(len(bars))
    gaps_after = tuple(tuple(date(2000, 1, 1 + k) for k in range((gaps or {}).get(i, 0))) for i in range(len(bars) - 1))
    seq = SessionSequence(tuple(sessions), gaps_after)
    by = dict(zip(sessions, bars))
    refs = {r.session: r for r in reference_series(list(zip(sessions, bars)))}
    return sr.build_series(symbol, seq, by, refs, list(factors), moves), refs


def feats(bars, **kw):
    return ft.Calculator(build(bars, **kw)[0]).all()


def dln(x):
    with localcontext(num.CTX):
        return D(x).ln()


def test_n1_the_arithmetic_is_decimal_in_one_context_and_takes_no_float():
    assert num.CTX.prec == 28 and num.QUANTUM == Q12
    assert num.to_decimal(F(1, 3)) == D("0.3333333333333333333333333333")
    assert num.q(D("0.1234567890125")) == D("0.123456789012") and num.q(D("0.1234567890135")) == D("0.123456789014")
    assert num.simple_return(D("110"), D("100")) == D("0.1") and num.log_return(D("100"), D("100")) == 0
    for bad in (lambda: num.to_decimal(0.5), lambda: num.to_decimal(True), lambda: num.ln(D(0)),
                lambda: num.simple_return(D(1), D(0))):
        with pytest.raises((TypeError, ValueError)):
            bad()
    src = "".join(inspect.getsource(m) for m in (num, sr, ft, mk))
    assert "float(" not in src and "import math" not in src


def test_s1_the_carried_series_zero_on_a_no_trade_session_and_the_whole_move_on_the_next_trade():
    s, _ = build([bar(100), bar(), bar(), bar(121), bar(121)])
    assert [p.c for p in s.points] == [100, 100, 100, 121, 121]
    assert [p.r for p in s.points] == [None, 0, 0, dln("1.21"), 0]
    assert [p.carried for p in s.points] == [False, True, True, False, False]
    assert [p.stale_sessions for p in s.points] == [0, 1, 2, 0, 0]
    assert build([None, bar(), bar(50)])[0].points[1].state == sr.NO_PRICE_YET


def test_s2_an_unknown_price_is_bridged_and_flagged_and_a_gap_is_flagged_never_split():
    s, _ = build([bar(100), bar(unknown=True, traded=True), bar(), bar(110), bar(110)], gaps={3: 1})
    assert [p.state for p in s.points] == ["known", "price_unknown", "price_unknown", "known", "known"]
    p = s.points[3]
    assert (p.r, p.spans_unknown, p.base_index, p.spans_gap) == (dln("1.1"), True, 0, False)
    assert (s.points[4].r, s.points[4].spans_gap, s.points[4].spans_unknown) == (0, True, False)
    assert s.points[1].r is None and s.points[2].r is None


def test_s3_a_split_with_no_trade_on_the_ex_date_gives_a_flat_series_and_an_unresolved_factor_is_untrusted():   # ML-19
    d = days(6)
    split = [act.Factor(SYM, act.SUBDIVISION, d[2], 1, 2, act.APPLIED)]
    s, _ = build([bar(100), bar(100), bar(), bar(), bar(51), bar(51)], factors=split)
    assert [p.c for p in s.points] == [50, 50, 50, 50, 51, 51]         # carried over the ex-date: the cum print, adjusted
    assert [p.r for p in s.points][1:4] == [0, 0, 0]
    f = ft.Calculator(s).all()
    assert f["ret_5"].value == D("0.02") and f["ret_1"].value == 0
    carried, _ = build([bar(100), bar(100), bar(), bar()], factors=split)
    v = ft.Calculator(carried).all()["ret_1"]
    assert (v.value, v.state) == (0, "stale") and ("stale", "ex_date_since_last_trade") in v.reasons
    bad = [act.Factor(SYM, act.RIGHTS, d[2], None, None, act.UNRESOLVED)]
    s, _ = build([bar(100), bar(100), bar(60), bar(60), bar(61), bar(61)], factors=bad)
    assert [p.state for p in s.points] == ["untrusted", "untrusted", "known", "known", "known", "known"]
    assert s.points[2].r is None                                        # no return out of an untrusted price
    f = ft.Calculator(s).all()
    assert (f["ret_5"].value, f["ret_5"].state) == (None, "untrusted")
    assert f["ret_1"].state == "fresh" and f["ret_1"].value == 0


def test_f1_returns_and_every_state_of_section_10_4():
    base = [bar(100)] * 5 + [bar(110)]
    v = feats(base)["ret_5"]
    assert (v.value, v.state, v.reasons, v.traded_sessions) == (D("0.1"), "fresh", (), 5)
    assert feats(base)["ret_10"].reasons == (("missing", "insufficient_history"),)
    v = feats([bar(100)] * 4 + [bar(110), bar()])["ret_5"]
    assert (v.value, v.state) == (D("0.1"), "stale")                    # a carried end, within the limit
    v = feats([bar(100), bar(110)] + [bar()] * 6)["ret_1"]
    assert (v.value, v.state) == (None, "missing")                      # the end is 6 sessions stale, the start 5
    assert v.reasons == (("missing", "staleness_limit"), ("stale", "carried_price"))
    v = feats(base[:5] + [bar(unknown=True, traded=True)])["ret_5"]
    assert (v.value, v.state) == (None, "missing") and ("missing", "price_unknown") in v.reasons
    assert feats(base, gaps={4: 1})["ret_5"].reasons == (("missing", "gaps_beyond_tolerance"),)
    long = [bar(100)] * 6 + [bar(110)]
    v = feats(long, gaps={0: 1})["ret_5"]
    assert v.state == "fresh"                                           # the gap lies before the window
    ten = [bar(100)] * 10 + [bar(110)]
    v = feats(ten, gaps={4: 1})["ret_10"]
    assert (v.value, v.state, v.reasons) == (D("0.1"), "partial", (("partial", "spans_gap"),))
    assert feats(ten, gaps={4: 2})["ret_10"].state == "missing"
    v = feats(base[:3] + [bar(100, quality="conflicting")] + base[4:])["ret_5"]
    assert (v.value, v.state) == (None, "conflicting")
    d = days(6)
    move = [act.Move(SYM, d[2], d[3], act.UNEXPLAINED, F(1, 2))]
    v = feats(base, moves=move)["ret_5"]
    assert (v.value, v.state) == (D("0.1"), "unlabelled")               # present, flagged
    assert feats(base, moves=[act.Move(SYM, d[2], d[3], act.CONFIRMED, F(1, 2), "k")])["ret_5"].state == "fresh"
    # precedence: conflicting before unlabelled, and both are listed
    v = feats(base[:3] + [bar(100, quality="conflicting")] + base[4:], moves=move)["ret_5"]
    assert v.state == "conflicting" and [s for s, _ in v.reasons] == ["conflicting", "unlabelled"]
    v = feats([bar(100), bar(unknown=True, traded=True), bar(105), bar(105), bar(105), bar(110)])["ret_5"]
    assert (v.value, v.state, v.reasons) == (D("0.1"), "partial", (("partial", "spans_unknown"),))      # Minor 6


def test_f2_momentum_skips_the_last_21_sessions():
    bars = [bar(100)] * 105 + [bar(120)] * 21 + [bar(240)] * 1
    f = feats(bars)                                                     # t = 126; c_{t-21} = 120, c_{t-126} = 100
    assert (f["mom_126_21"].value, f["mom_126_21"].state) == (D("0.2"), "fresh")
    assert f["ret_126"].value == D("1.4") and f["mom_252_21"].state == "missing"


def test_f3_volatility_by_hand_and_parkinson():
    prices = [D(100)]
    for i in range(21):
        prices.append(prices[-1] * (D("1.1") if i % 2 == 0 else 1 / D("1.1")))
    f = feats([bar(p, high=p * 2, low=p) for p in prices])
    a = dln("1.1")
    with localcontext(num.CTX):
        rs = [num.log_return(prices[i + 1], prices[i]) for i in range(21)]
        m = sum(rs) / 21
        expected = (sum((r - m) ** 2 for r in rs) / 20).sqrt()
        park = ((dln("2") ** 2) / (4 * dln("2"))).sqrt()
    assert abs(rs[0] - a) < D("1e-25")
    assert (f["vol_21"].value, f["vol_21"].state, f["vol_21"].inputs) == (expected.quantize(Q12), "fresh", 21)
    assert f["vol_parkinson_21"].value == park.quantize(Q12) and f["vol_parkinson_21"].inputs == 21
    assert f["vol_63"].state == "missing"
    short = feats([bar(100)] * 22)
    assert short["vol_21"].value == 0 and short["vol_parkinson_21"].state == "missing"      # no high or low
    thin = feats([bar(100)] + [bar(unknown=True, traded=True)] * 8 + [bar(100)] * 13)
    assert thin["vol_21"].reasons[0] == ("missing", "below_min_observations")               # 13 returns < 15


def test_f4_drawdown_and_the_latest_maximum_when_tied():
    bars = [bar(100)] * 250 + [bar(200), bar(200), bar(150)]
    f = feats(bars)
    assert (f["dd_252"].value, f["dd_days_252"].value, f["dd_252"].state) == (D("-0.25"), 1, "fresh")
    assert f["dd_days_252"].value == D(1) and isinstance(f["dd_days_252"].value, D)
    assert feats([bar(100)] * 200)["dd_252"].state == "missing"
    part = feats([None] * 100 + [bar(100)] * 151 + [bar(80)])
    assert (part["dd_252"].value, part["dd_252"].state) == (D("-0.2"), "partial")


def test_f5_volume_a_no_trade_session_counts_as_zero_and_share_count_actions_adjust():
    bars = [bar(100, volume="1000", turnover="100", trades="4")] * 11 + [bar()] * 10 + [bar(100, volume="3000",
                                                                                         turnover="300", trades="6")]
    f = feats(bars)                                                     # window: 10 traded at 1000, 10 at 0, 1 at 3000
    assert f["vol_shares_avg_21"].value == num.q(num.CTX.divide(D(13000), D(21)))
    assert f["turnover_avg_21"].value == num.q(num.CTX.divide(D(1300), D(21)))
    assert f["trades_avg_21"].value == num.q(num.CTX.divide(D(46), D(21)))
    assert (f["vol_shares_avg_21"].state, f["vol_shares_avg_21"].traded_sessions, f["vol_shares_avg_21"].inputs) == ("fresh", 11, 21)
    assert f["rel_volume_21"].value == num.q(num.CTX.divide(D(3000), num.CTX.divide(D(11000), D(21))))
    d = days(22)
    split = [act.Factor(SYM, act.SUBDIVISION, d[21], 1, 2, act.APPLIED)]
    g = feats(bars, factors=split)                                      # earlier volumes double; today's is already ex
    assert g["vol_shares_avg_21"].value == num.q(num.CTX.divide(D(23000), D(21)))
    assert g["turnover_avg_21"].value == f["turnover_avg_21"].value     # money is never adjusted
    rights = [act.Factor(SYM, act.RIGHTS, d[5], 24, 25, act.APPLIED)]
    h = feats(bars, factors=rights)["vol_shares_avg_21"]                # the four traded sessions before it are out
    assert h.state == "partial" and ("partial", "share_count_changed") in h.reasons and h.inputs == 17
    assert h.value == num.q(num.CTX.divide(D(9000), D(17)))
    late = feats(bars, factors=[act.Factor(SYM, act.RIGHTS, d[21], 24, 25, act.APPLIED)])["vol_shares_avg_21"]
    assert late.state == "missing" and late.inputs == 11               # too few volumes on today's share basis
    z = feats([bar(100)] + [bar()] * 21 + [bar(100)])["rel_volume_21"]
    assert (z.value, z.state) == (None, "missing") and ("missing", "zero_base") in z.reasons
    nv = feats([bar(100)] * 21 + [bar(100, volume=None)])["vol_shares_avg_21"]
    assert nv.state == "unlabelled" and nv.inputs == 20 and nv.value == D(1000)


def test_f6_liquidity_amihud_zero_trade_ratio_and_stale_sessions():
    bars = [bar(100, turnover="1000")] + [bar(110 if i % 2 == 0 else 100, turnover="1000") for i in range(21)]
    f = feats(bars)
    assert f["amihud_21"].value == num.q(num.CTX.divide(dln("1.1"), D(1000))) and f["amihud_21"].inputs == 21
    f = feats([bar(100)] * 43 + [bar()] * 20)
    assert (f["zero_trade_ratio_63"].value, f["zero_trade_ratio_63"].state) == (num.q(num.CTX.divide(D(20), D(63))), "fresh")
    assert (f["stale_sessions"].value, f["stale_sessions"].state) == (20, "fresh")
    assert f["amihud_21"].state == "missing"                            # one traded session in the window
    u = feats([bar(100), bar(unknown=True, traded=True)])["stale_sessions"]
    assert (u.value, u.reasons) == (None, (("missing", "price_unknown"),))


def test_f7_return_history_counts_only_returns_that_end_in_the_window_and_always_reports_n():
    bars = [bar(100)] + [bar(100 + (i % 3)) for i in range(1, 253)]     # 100, 101, 102, 100, 101, ...
    f = feats(bars)
    assert f["hist_n_1"].value == 252 and f["hist_n_1"].state == "fresh"
    assert f["hist_up_share_1"].value == num.q(num.CTX.divide(D(168), D(252)))
    assert f["hist_down_share_1"].value == num.q(num.CTX.divide(D(84), D(252))) and f["hist_flat_share_1"].value == 0
    assert f["hist_n_21"].value == 232 and f["hist_n_21"].state == "partial"     # ends 21..252 have a start
    assert f["hist_flat_share_21"].value == 1 and f["hist_mean_ret_21"].value == 0
    short = feats(bars[:100])
    assert (short["hist_n_1"].value, short["hist_up_share_1"].value, short["hist_up_share_1"].state) == (99, None, "missing")
    g = feats(bars, gaps={200: 1})
    assert g["hist_n_1"].value == 251 and g["hist_n_5"].value == 248 - 5         # every 5-return spanning the gap is out
    unk = bars[:150] + [bar(unknown=True, traded=True)] + bars[151:]
    assert feats(unk)["hist_n_1"].value == 250 and feats(unk)["hist_n_5"].value == 246   # only ENDS at the unknown (Minor 9)


def test_f8_size_range_and_the_opening_gap():
    import dataclasses
    t_bar = dataclasses.replace(bar(110, high=112, low=108, open_=105, cap=1100000), raw={"last_traded_price": D("112")})
    f = feats([bar(100), t_bar])
    assert f["market_cap_reported"].value == 1100000                  # §10.3: over the last traded price, not the close
    assert f["shares_implied"].value == num.q(num.CTX.divide(D(1100000), D(112)))
    no_ltp = feats([bar(100), bar(110, high=112, low=108, open_=105, cap=1100000)])["shares_implied"]
    assert no_ltp.reasons == (("missing", "no_last_traded_price"),)
    assert f["range_1"].value == num.q(num.CTX.divide(D(4), D(110))) and f["gap_1"].value == D("0.05")
    f = feats([bar(100), bar()])
    assert [f[k].reasons[0][1] for k in ("market_cap_reported", "shares_implied", "range_1", "gap_1")] == [
        "not_reported", "not_traded", "not_traded", "no_open_price"]
    d = days(3)
    split = [act.Factor(SYM, act.SUBDIVISION, d[2], 1, 2, act.APPLIED)]
    g = feats([bar(100), bar(), bar(52, open_=51)], factors=split)["gap_1"]
    assert g.value == D("0.02")                                         # c_{t-1} on t's basis: 50, a carried cum print
    assert set(ft.Calculator(build([bar(100)])[0]).all()) == set(ft.SECURITY_FEATURE_IDS)
    assert all(v.value is None or v.value == v.value.quantize(Q12) for v in f.values())


def test_f9_a_feature_at_t_uses_nothing_after_t():                                                    # ML-1 (features)
    bars = [bar(100 + (i * 7) % 13, high=120, low=90, open_=100) for i in range(300)]
    early = feats(bars[:280])
    later = [bar(999, high=2000, low=1, open_=5)] * 20
    assert feats(bars[:280]) == early
    truncated = ft.Calculator(build(bars[:280])[0]).all()
    assert truncated == early and feats(bars[:280] + later) != early   # the converse: at a later t the values differ
    s_full, _ = build(bars[:280] + later)
    assert s_full.points[:280] == build(bars[:280])[0].points          # with no action, earlier points are unchanged


LENIENT = EligibilityPolicy(min_history_sessions=3, activity_window=3, min_median_turnover_lkr=D("1"),
                            quality_block_window=3)
CFG = ft.FeatureConfig(min_constituents=3)


def market(paths):
    series, refs = {}, {}
    for sym, bars in paths.items():
        series[sym], refs[sym] = build(bars, symbol=sym)
    eligible = mk.eligibility_by_session(series, refs, LENIENT)
    return series, eligible, mk.market_points(series, eligible, CFG.min_constituents)


def test_k1_membership_is_fixed_on_the_session_before_and_needs_min_constituents():
    paths = {f"S{i}.N0000": [bar(100, symbol=f"S{i}.N0000")] * 3 + [bar(110, symbol=f"S{i}.N0000")] * 2 for i in range(3)}
    paths["LATE.N0000"] = [None] + [bar(100, symbol="LATE.N0000")] * 3 + [bar(200, symbol="LATE.N0000")]
    paths["RTS.R0000"] = [bar(1, symbol="RTS.R0000")] * 3 + [bar(9, symbol="RTS.R0000")] * 2
    series, eligible, pts = market(paths)
    assert eligible["S0.N0000"] == [False, False, True, True, True] and eligible["LATE.N0000"] == [False] * 3 + [True] * 2
    assert eligible["RTS.R0000"] == [False] * 5                         # not an equity line: never a member
    assert [p.m is None for p in pts] == [True, True, True, False, False]        # eligible on session 2: members from 3
    assert pts[3].members == ("S0.N0000", "S1.N0000", "S2.N0000") and abs(pts[3].m - dln("1.1")) < D("1e-26")
    assert pts[4].members == ("LATE.N0000", "S0.N0000", "S1.N0000", "S2.N0000")   # eligible as of session 3, not before
    with localcontext(num.CTX):
        assert abs(pts[4].m - dln("2") / 4) < D("1e-26")
    two = {k: v for k, v in paths.items() if k in ("S0.N0000", "S1.N0000")}
    assert all(p.m is None for p in market(two)[2])                     # below min_constituents: missing


def test_k2_market_return_breadth_relative_return_beta_and_rank():
    n = 130
    def path(sym, mult):                                               # log return per session: mult * x_u
        px, out = D(100), []
        for u in range(n):
            if u:
                px = num.exp(num.CTX.multiply(D(mult), D("0.01") if u % 2 else D("-0.02"))) * px
            out.append(bar(px, symbol=sym))
        return out
    paths = {"A.N0000": path("A.N0000", 1), "B.N0000": path("B.N0000", 2), "C.N0000": path("C.N0000", 3)}
    series, eligible, pts = market(paths)
    t = n - 1
    assert abs(pts[t].m - D("0.02")) < D("1e-20")                       # mean of 0.01, 0.02, 0.03 on an odd session
    common = mk.market_features(pts, series["A.N0000"].sequence)
    assert common["mkt_ret_ew_1"].value == num.q(num.exp(pts[t].m) - 1)
    assert abs(common["mkt_ret_ew_5"].value - num.q(num.exp(D("0.02") * (3 * D("0.01") - 2 * D("0.02")) / D("0.01")) - 1)) <= Q12
    assert mk.market_features(pts[:3], SessionSequence(tuple(days(3)), ((), ())))["mkt_ret_ew_1"].state == "missing"
    assert mk.breadth(pts, series).value == 1 and mk.breadth(pts[:-1], {k: v for k, v in series.items()}).value == 0
    ret21 = {s: ft.Calculator(series[s], CFG).all()["ret_21"] for s in series}
    out = {s: mk.security_market_features(s, series[s], pts, ret21, CFG) for s in series}
    assert [abs(out[s]["beta_126"].value - b) <= D("1e-9") for s, b in (("A.N0000", D("0.5")), ("B.N0000", D(1)),
                                                                        ("C.N0000", D("1.5")))] == [True] * 3
    assert out["A.N0000"]["beta_126"].state == "fresh" and out["A.N0000"]["beta_126"].inputs == 126
    # rel_ret_21 = own log return - market sum: B is the market itself here
    assert abs(out["B.N0000"]["rel_ret_21"].value) <= Q12
    assert out["A.N0000"]["rel_ret_21"].value == -out["C.N0000"]["rel_ret_21"].value
    assert out["A.N0000"]["rel_ret_63"].state == "fresh"
    ranks = sorted(out[s]["xs_rank_ret_21"].value for s in series)
    assert ranks == [num.q(num.CTX.divide(D(k) - D("0.5"), D(3))) for k in (1, 2, 3)]
    tie = dict(ret21, **{"B.N0000": ret21["A.N0000"]})
    assert mk.security_market_features("A.N0000", series["A.N0000"], pts, tie, CFG)["xs_rank_ret_21"].value in (
        num.q(D("1.5") / 3 - D("0.5") / 3), num.q(num.CTX.divide(D(2), D(3))))
    few = mk.security_market_features("A.N0000", series["A.N0000"], pts, {"A.N0000": ret21["A.N0000"]}, CFG)
    assert few["xs_rank_ret_21"].reasons == (("missing", "below_min_constituents"),)


def test_g1_the_feature_path_does_not_import_the_target_module_and_is_pure():
    for module in (num, sr, ft, mk):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            assert not any("target" in x or "label" in x for x in names), (module.__name__, names)
    assert set(mk.MARKET_FEATURE_IDS).isdisjoint(ft.SECURITY_FEATURE_IDS)
    assert len(ft.SECURITY_FEATURE_IDS) + len(mk.MARKET_FEATURE_IDS) == 54


# ------------------------------------------------------------------- added after audit 2 of MF-4 and MF-5 (C-0043)

def test_a1_a_conflicting_input_at_the_first_session_of_a_window_or_carried_into_it_is_conflicting():      # B1
    assert feats([bar(100), bar(100, quality="conflicting"), bar(110)])["ret_1"].state == "conflicting"
    carried = feats([bar(90, quality="conflicting"), bar(), bar(), bar(95)])["ret_1"]
    assert (carried.value, carried.state) == (None, "conflicting")       # a close carried from a conflicting bar
    g = feats([bar(90, quality="conflicting"), bar(), bar(95, open_=96)])["gap_1"]
    assert (g.value, g.state) == (None, "conflicting")                 # gap_1's c(t-1) is that carried close
    long = [bar(100)] * 300
    long[300 - 1 - 63] = bar(100, quality="conflicting")
    assert feats(long[:-1] + [bar(110)])["ret_63"].state == "conflicting"
    dd = [bar(100)] * 260
    dd[260 - 252] = bar(300, quality="conflicting")
    assert feats(dd)["dd_252"].state == "conflicting"
    pk = [bar(100, high=101, low=99)] * 30
    pk[30 - 21] = bar(100, high=200, low=50, quality="conflicting")
    assert feats(pk)["vol_parkinson_21"].state == "conflicting"
    hist = [bar(100 + i % 3) for i in range(300)]
    hist[200] = bar(101, quality="conflicting")
    h = feats(hist)
    assert all(h[f"hist_{k}_1"].state == "conflicting" for k in ("up_share", "flat_share", "down_share", "mean_ret"))
    sv = [bar(100)] * 22
    sv[22 - 21] = bar(100, quality="conflicting")
    assert feats(sv)["vol_shares_avg_21"].state == "conflicting"     # the session window's first bar
    rv = [bar(100)] * 22 + [bar(100, volume="5000", quality="conflicting")]
    assert feats(rv)["rel_volume_21"].state == "conflicting"
    gap = feats([bar(100)] * 22 + [bar(100, volume="5000")], gaps={21: 1})["rel_volume_21"]
    assert (gap.value, gap.state) == (D(5), "partial")                 # a gap between t-1 and t (m1)


def test_a2_window_edges_momentum_end_and_session_minimum():
    jump = [bar(100)] * 9 + [bar(200)] * 22                           # the only move is r at t-21
    v = feats(jump)["vol_21"]
    assert (v.value, v.inputs) == (0, 21)                              # outside the 21 returns ending at t
    mom = [bar(100)] * 107 + [bar(150)] * 20
    assert feats(mom)["mom_126_21"].value == 0                         # c(t-21) = c(t-126) = 100
    fifteen = [bar(100)] + [None] * 6 + [bar(100)] * 15
    assert feats(fifteen)["vol_shares_avg_21"].state == "partial"      # exactly the minimum: a value
    fourteen = [bar(100)] * 2 + [None] * 7 + [bar(100)] * 14
    assert feats(fourteen)["vol_shares_avg_21"].state == "missing"


def test_a3_history_untrusted_gap_1_on_ts_basis_and_its_gap_guard_and_unknown_trading():
    d = days(300)
    bad = [act.Factor(SYM, act.RIGHTS, d[250], None, None, act.UNRESOLVED)]
    h = feats([bar(100 + i % 3) for i in range(300)], factors=bad)
    assert h["hist_up_share_1"].state == "untrusted"
    d2 = days(3)
    split_at_t = [act.Factor(SYM, act.SUBDIVISION, d2[2], 1, 2, act.APPLIED)]
    g = feats([bar(100), bar(100), bar(52, open_=51)], factors=split_at_t)["gap_1"]
    assert g.value == D("0.02")                                        # t-1 traded at 100: 50 on t's basis
    assert feats([bar(100), bar(100), bar(101, open_=101)], gaps={1: 1})["gap_1"].state == "missing"
    unknown = FeatureBar(symbol=SYM, row=5, traded=None, px_close=None, price_unknown=True, share_volume=None,
                         trade_count=None, turnover=None, open_price=None, high=None, low=None, quality="missing")
    z = feats([bar(100)] * 50 + [unknown] * 13)["zero_trade_ratio_63"]
    assert (z.inputs, z.value) == (50, 0)                              # traded = unknown is in neither part
    both = feats([bar(100), bar(100, quality="conflicting"), bar(100), bar(100), bar(100), bar(unknown=True, traded=True)])["ret_5"]
    assert both.state == "conflicting" and ("missing", "price_unknown") in both.reasons


def test_a4_market_breadth_flat_is_not_up_the_gap_guard_and_beta_without_bridged_returns():
    def sym(i):
        return f"S{i}.N0000"
    flat = {sym(i): [bar(100, symbol=sym(i))] * 4 + [bar(110 if i < 2 else 100, symbol=sym(i))] for i in range(3)}
    series, eligible, pts = market(flat)
    assert mk.breadth(pts, series).value == num.q(num.CTX.divide(D(2), D(3)))
    assert mk.breadth([], {}).state == "missing"                       # an empty market (m2)
    seq = series[sym(0)].sequence
    gapped = SessionSequence(seq.sessions, seq.gaps_after[:-1] + ((date(2000, 1, 1),),))
    assert mk.market_features(pts, gapped)["mkt_ret_ew_1"].state == "missing"
    n = 130

    def path(s, mult, unknown_at=None):
        px, out = D(100), []
        for u in range(n):
            if u:
                px = num.exp(num.CTX.multiply(D(mult), D("0.01") if u % 2 else D("-0.02"))) * px
            out.append(bar(unknown=True, traded=True, symbol=s) if u == unknown_at else bar(px, symbol=s))
        return out
    paths = {sym(i): path(sym(i), i + 1, 60 if i == 0 else None) for i in range(4)}
    series, eligible, pts = market(paths)
    ret21 = {s: ft.Calculator(series[s], CFG).all()["ret_21"] for s in series}
    beta = mk.security_market_features(sym(0), series[sym(0)], pts, ret21, CFG)["beta_126"]
    assert beta.inputs == 124                                          # r(60) undefined, r(61) bridged: both out


# ------------------------------------------------------------------- added after audit 3 (C-0047)

def test_a5_a_bridged_return_reaches_back_to_a_conflicting_base_and_non_price_features_ignore_carried_prices():
    bars = [bar(100 + i % 3) for i in range(31)]
    bars[8] = bar(150, quality="conflicting")
    bars[9] = bar(unknown=True, traded=True)                          # r(10) is bridged from session 8
    f = feats(bars)
    assert f["vol_21"].state == "conflicting" and f["amihud_21"].state == "conflicting"        # B1'
    bars[8] = bar(150)
    assert feats(bars)["vol_21"].state == "partial"                   # the same series without the conflict
    carried = [bar(100)] * 5 + [bar(100, quality="conflicting")] + [bar()] * 64
    f = feats(carried)                                                # MJ-1: no price is an input of these
    assert [f[k].state for k in ("vol_shares_avg_21", "turnover_avg_21", "trades_avg_21", "zero_trade_ratio_63")] == [
        "fresh"] * 4
    assert f["ret_1"].state == "conflicting"                          # but a return on that carried price is
    hist = [bar(100 + i % 3) for i in range(300)]
    hist[40] = bar(101, quality="conflicting")
    hist[41:48] = [bar()] * 7                                         # carried into first - h of hist_*_5
    assert feats(hist)["hist_up_share_5"].state == "conflicting"


def test_a6_rel_volume_counts_gaps_once_and_gap_1_checks_t_and_market_eligibility_uses_the_series():
    two = feats([bar(100)] * 22 + [bar(100, volume="5000")], gaps={10: 1, 21: 1})["rel_volume_21"]
    assert two.state == "missing" and ("missing", "gaps_beyond_tolerance") in two.reasons                    # MJ-2
    assert feats([bar(100), bar(100), bar(101, open_=101, quality="conflicting")])["gap_1"].state == "conflicting"
    d = days(12)
    sym = "U.N0000"
    bad = [act.Factor(sym, act.RIGHTS, d[10], None, None, act.UNRESOLVED)]
    s, refs = build([bar(10, symbol=sym)] * 12, factors=bad, symbol=sym)
    el = mk.eligibility_by_session({sym: s}, {sym: refs}, LENIENT)
    assert el[sym][5] is False                                        # untrusted sessions block (M3 through market)


def test_a7_a_window_built_on_a_price_carried_from_a_conflicting_bar_is_conflicting():
    f = feats([bar(100)] * 5 + [bar(100, quality="conflicting")] + [bar()] * 25)
    assert f["vol_21"].state == "conflicting" and f["ret_21"].state == "conflicting"


# ------------------------------------------------------------------- added after audit 4 (C-0048)

def test_a8_bridged_base_carried_conflict_gaps_parkinson_market_spans_and_rank_state():
    bars = [bar(100 + i % 3) for i in range(31)]
    bars[6] = bar(150, quality="conflicting")
    bars[7] = bar()                                                   # 150 carried into session 7
    bars[8] = bar(unknown=True, traded=True)
    bars[9] = bar(unknown=True, traded=True)                          # r(10) bridged from 7, whose close is carried
    assert feats(bars)["vol_21"].state == "conflicting"
    clean = [bar(100 + i % 3) for i in range(31)]
    clean[8] = bar(unknown=True, traded=True)
    clean[9] = bar(unknown=True, traded=True)
    v = feats(clean, gaps={7: 3})["vol_21"]                           # three gap dates before the window start
    assert v.state == "missing" and ("missing", "gaps_beyond_tolerance") in v.reasons
    pk = [bar(100, high=101, low=99)] * 30
    pk[2] = bar(100, quality="conflicting")
    pk[3:30] = [bar(100, high=101, low=99)] * 27
    pk[5] = bar()                                                     # a carried price; Parkinson uses no price
    assert feats(pk)["vol_parkinson_21"].state == "fresh"
    pts = [mk.MarketPoint(None, (), 0)] + [mk.MarketPoint(D("0"), ("A",), 3, False, True)] * 5
    seq = SessionSequence(tuple(days(6)), tuple(() for _ in range(5)))
    assert mk.market_features(pts, seq)["mkt_ret_ew_5"].state == "partial"   # spans_unknown carried into the sum
    point = mk.MarketPoint(D("0.01"), ("A.N0000", "B.N0000", "C.N0000"), 3)
    stale = ft.Value("ret_21", D("0.1"), "stale", (("stale", "carried_price"),))
    others = {s_: ft.Value("ret_21", D(str(i)), "fresh") for i, s_ in enumerate(("B.N0000", "C.N0000"))}
    r = mk._rank("A.N0000", point, {"A.N0000": stale, **others}, CFG)
    assert r.state == "stale"                                         # the own value's state is carried as it is
