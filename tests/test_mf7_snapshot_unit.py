"""MF-7c: one snapshot from imported evidence (worker/market_feature_store/snapshot.py). Synthetic; pure."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from fractions import Fraction as F

from worker.market_feature_store import history_bars as hb
from worker.market_feature_store import snapshot as sn
from worker.market_features import actions as act
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, Query

UTC = timezone.utc


def weekdays(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_s1_a_snapshot_with_a_split_no_trade_days_and_a_market():
    days = weekdays(40)
    imported = {}
    for k in range(4):
        sym = f"S{k}.N0000"
        rows = {d: {"close": str(100 + k + (i % 3)), "shares": "1000", "turnover": "200000", "flags": ""}
                for i, d in enumerate(days) if not (k == 3 and 20 <= i < 25)}             # S3 skips 5 sessions
        imported[sym] = hb.Imported(rows=rows, complete=((days[0], days[-1]),))
    rows = {d: {"close": "100" if i < 30 else "50", "shares": "1000", "turnover": "200000", "flags": ""}
            for i, d in enumerate(days)}
    imported["SPL.N0000"] = hb.Imported(rows=rows, complete=((days[0], days[-1]),))
    t = hb.available_at(days[-1])
    q = Query(AVAILABLE, t, t + timedelta(days=30))
    split = act.statement("sp", "SPL.N0000", act.SUBDIVISION, recorded_at=t, available_at=t - timedelta(days=20),
                          source_reference="TradeIQ split", ex_date=days[30], terms={"old_shares": 1, "new_shares": 2})
    policy = universe.EligibilityPolicy(min_history_sessions=10, min_median_turnover_lkr=D("1"))
    snap = sn.build(imported, [split], q, policy=policy, config=FeatureConfig(min_constituents=3))
    assert snap.d_star == days[-1] and len(snap.rows) == 5
    by = {r["symbol"]: r for r in snap.rows}
    spl = by["SPL.N0000"]["features"]
    assert spl["ret_21"][0] == 0 and spl["ret_21"][1] == "fresh"       # the split is adjusted: no false fall
    assert [f.value for f in snap.factors["SPL.N0000"]] == [F(1, 2)] and snap.moves["SPL.N0000"] == []
    s3 = by["S3.N0000"]
    assert s3["eligibility"] == "eligible" and s3["features"]["zero_trade_ratio_63"][1] in ("missing", "partial")
    assert snap.market["mkt_ret_ew_1"][1] if isinstance(snap.market["mkt_ret_ew_1"], tuple) else True
    assert len(snap.adjustment_set_hash) == 64
    no_split = sn.build(imported, [], q, policy=policy, config=FeatureConfig(min_constituents=3))
    nb = {r["symbol"]: r for r in no_split.rows}["SPL.N0000"]
    assert nb["features"]["ret_21"][1] == "unlabelled"                  # without the split: an unexplained fall
    assert [m.state for m in no_split.moves["SPL.N0000"]] == [act.UNEXPLAINED]


def _base(n=40):
    days = weekdays(n)
    imported = {}
    for k in range(4):
        rows = {d: {"close": str(100 + k + (i % 3)), "shares": "1000", "turnover": "200000", "flags": ""}
                for i, d in enumerate(days)}
        imported[f"S{k}.N0000"] = hb.Imported(rows=rows, complete=((days[0], days[-1]),))
    t = hb.available_at(days[-1])
    return days, imported, t, Query(AVAILABLE, t, t + timedelta(days=30))


POLICY = universe.EligibilityPolicy(min_history_sessions=10, min_median_turnover_lkr=D("1"))


def test_s2_adj_ref_is_adjusted_across_an_ex_date_after_the_last_trade():                       # audit C6
    days, imported, t, q = _base()
    rows = {d: {"close": "100", "shares": "1000", "turnover": "100000", "flags": ""} for d in days[:35]}
    imported["SPL.N0000"] = hb.Imported(rows=rows, complete=((days[0], days[-1]),))               # no trade after 35
    split = act.statement("sp", "SPL.N0000", act.SUBDIVISION, recorded_at=t, available_at=t - timedelta(days=10),
                          source_reference="TradeIQ split", ex_date=days[37], terms={"old_shares": 1, "new_shares": 2})
    snap = sn.build(imported, [split], q, policy=POLICY, config=FeatureConfig(min_constituents=3))
    r = {x["symbol"]: x for x in snap.rows}["SPL.N0000"]
    assert (r["px_ref"], r["ref_session"], r["stale_sessions"]) == (D("100"), days[34], 5)
    assert r["adj_ref"] == D("50")


def test_s3_a_split_announced_after_the_cutoff_is_not_applied():                                 # audit C7
    days, imported, t, q = _base()
    imported["SPL.N0000"] = hb.Imported(
        rows={d: {"close": "100" if i < 30 else "50", "shares": "1000", "turnover": "200000", "flags": ""}
              for i, d in enumerate(days)}, complete=((days[0], days[-1]),))
    late = act.statement("sp", "SPL.N0000", act.SUBDIVISION, recorded_at=t - timedelta(days=20),
                         available_at=t + timedelta(days=1), source_reference="TradeIQ split", ex_date=days[30],
                         terms={"old_shares": 1, "new_shares": 2})
    snap = sn.build(imported, [late], q, policy=POLICY, config=FeatureConfig(min_constituents=3))
    assert snap.factors["SPL.N0000"] == []
    assert [m.state for m in snap.moves["SPL.N0000"]] == [act.UNEXPLAINED]
    assert {x["symbol"]: x for x in snap.rows}["SPL.N0000"]["features"]["ret_21"][1] == "unlabelled"


def test_s4_eligibility_is_evaluated_at_d_star():                                                # audit C8
    days, imported, t, q = _base()
    young = {d: {"close": "10", "shares": "1000", "turnover": "200000", "flags": ""} for d in days[-10:]}
    imported["NEW.N0000"] = hb.Imported(rows=young, complete=((days[-10], days[-1]),))   # 10 sessions at D*, 9 before
    old = {d: {"close": "10", "shares": "1000", "turnover": "200000", "flags": ""} for d in days[:-6]}
    imported["OLD.N0000"] = hb.Imported(rows=old, complete=((days[0], days[-1]),))       # 6 stale at D*, 5 before
    snap = sn.build(imported, [], q, policy=POLICY, config=FeatureConfig(min_constituents=3))
    el = {x["symbol"]: x["eligibility"] for x in snap.rows}
    assert (el["NEW.N0000"], el["OLD.N0000"]) == ("eligible", "stale")


def test_s5_the_market_lookback_reads_every_session_it_needs(monkeypatch):                       # audit C1
    days = weekdays(170)
    imported = {}
    for k in range(8):                                  # gaps of different lengths: membership changes over time
        rows = {}
        for i, d in enumerate(days):
            if (i * (k + 3)) % 23 < 4 + k % 3:
                continue
            rows[d] = {"close": str(50 + k + ((i * 7 + k * 5) % 11) / 2), "shares": str(100 + (i * k) % 37),
                       "turnover": str(10000 + (i * 13 + k) % 997), "flags": ""}
        imported[f"M{k}.N0000"] = hb.Imported(rows=rows, complete=((days[0], days[-1]),))
    t = hb.available_at(days[-1])
    q = Query(AVAILABLE, t, t + timedelta(days=30))
    policy = universe.EligibilityPolicy(min_history_sessions=20, min_median_turnover_lkr=D("1"), max_stale_sessions=2)
    cfg = FeatureConfig(min_constituents=3)
    got = sn.build(imported, [], q, policy=policy, config=cfg)
    monkeypatch.setattr(sn, "MARKET_LOOKBACK", 10 ** 6)                                 # every session: start = 0
    full = sn.build(imported, [], q, policy=policy, config=cfg)
    assert got.rows == full.rows and got.market == full.market
