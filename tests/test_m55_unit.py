"""
Phase 5 M5-5 (docs/PHASE5_ML_DESIGN.md §8, §13): calibration, intervals and contributions on synthetic data with
known truth. Vector scaling repairs an overconfident model on held-out rows; conformal intervals reach the nominal
coverage in every liquidity class (where one pooled quantile would not); the floor and the flags; MLK-7: a planted
signal of a stated size passes the gate test of §9.3 in at least a stated share of seeds, and the contributions name
the planted feature. Linux only (the ML libraries).
"""
import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("sklearn")

from worker.evaluation_metrics import bootstrap, metrics       # noqa: E402
from worker.ml_dataset import dataset as ds                    # noqa: E402
from worker.ml_models import baselines, calibration as cal, data, linear   # noqa: E402
from worker.ml_models.artifact import Refused                  # noqa: E402
from worker.ml_protocol.folds import FitScope                  # noqa: E402

H = 5
CLS = ("rise", "flat", "fall")


def row(i, feats, liq="mid", per_day=10):
    session = date(2024, 1, 1) + timedelta(days=i // per_day)
    return ds.Row(f"S{i % per_day:02d}.N0000", session, datetime.combine(session, datetime.min.time(), timezone.utc),
                  i, feats, {"liquidity_class": liq}, ())


def dataset(rows, labels):
    return ds.Dataset(None, "v", tuple(rows), labels, "point_in_time", "complete", "all", {})


def label(direction, ret, vol="0.01"):
    return ds.Label("resolved", {"direction": direction, "ret": str(ret), "volatility": vol,
                                 "volatility_state": "resolved"}, None, "0" * 64)


def scope(name, rows):
    return FitScope(name, frozenset(r.key for r in rows))


# ------------------------------------------------------------------------------------------------ §8.1


def test_c1_vector_scaling_repairs_an_overconfident_model():
    rnd = random.Random(5)
    rows, labels, raw = [], {}, []
    for i in range(6000):
        lg = np.array([rnd.gauss(0, 1), 0.0, rnd.gauss(0, 1)])
        p_true = np.exp(lg) / np.exp(lg).sum()
        k = int(np.searchsorted(np.cumsum(p_true), rnd.random()))
        k = min(k, 2)
        over = np.exp(2.5 * lg + np.array([0.4, 0, -0.4]))          # overconfident and biased towards rise
        raw.append(tuple((over / over.sum()).tolist()))
        r = row(i, {}, liq=("low", "mid", "high")[i % 3])
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label(CLS[k], 0.01 if k == 0 else -0.01 if k == 2 else 0)
    d = dataset(rows, labels)
    calib, held = rows[:3000], rows[3000:]
    art = cal.fit_vector_scaling(scope("calibrate", calib), calib, raw[:3000], d, H)
    b = art.load("vector_scaling")
    assert abs(sum(b["offset"])) < 1e-12 and b["converged"]
    fixed = cal.apply_scaling(art, raw[3000:])
    before = cal.reliability_report(d, held, raw[3000:], H)
    after = cal.reliability_report(d, held, fixed, H)
    assert after["overall"][0] < before["overall"][0] / 2
    assert set(after) == {"overall", "low", "mid", "high"}
    assert all(after[g][0] < before[g][0] for g in after)
    actual = [labels[(r.symbol, r.session, H)].values["direction"] for r in held]
    ll = lambda ps: metrics.log_loss(actual, [dict(zip(metrics.CLASSES, (p[CLS.index(c)] for c in metrics.CLASSES)))
                                              for p in ps])[0]                       # noqa: E731
    assert ll(fixed) < ll(raw[3000:])
    assert cal.reliability_ok(after, 0.05) and not cal.reliability_ok(before, 0.05)
    with pytest.raises(Refused, match="wrong_slice"):
        cal.fit_vector_scaling(scope("fit", calib), calib, raw[:3000], d, H)
    assert cal.apply_scaling(art, [None]) == [None]


# ------------------------------------------------------------------------------------------------ §8.2


def heteroscedastic(n=9000, seed=7):
    """Returns with noise proportional to vol_21 x sqrt(h), times a factor per liquidity class (so one pooled
    quantile cannot cover every class); a few rows without vol_21."""
    rnd = random.Random(seed)
    mult = {"low": 2.5, "mid": 1.0, "high": 0.5}
    rows, labels, pred = [], {}, []
    for i in range(n):
        liq = ("low", "mid", "high")[i % 3]
        vol = 0.005 + 0.02 * rnd.random()
        feats = {"mf.vol_21": (None, "missing", "no_trades") if i % 50 == 0 else (D(str(round(vol, 8))), "fresh",
                                                                                    None)}
        r = row(i, feats, liq=liq, per_day=30)
        mu = rnd.gauss(0, 0.01)
        y = mu + rnd.gauss(0, 1) * vol * math.sqrt(H) * mult[liq]
        rows.append(r)
        pred.append(mu)
        labels[(r.symbol, r.session, H)] = label("rise" if y > 0 else "fall", round(y, 10))
    return dataset(rows, labels), rows, pred


def test_i1_class_conditional_conformal_reaches_the_level_in_every_class():
    d, rows, pred = heteroscedastic()
    fit, conf, held = rows[:3000], rows[3000:6000], rows[6000:]
    art = cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, pred[3000:6000], d, H, 0.9)
    b = art.load("conformal")
    positive = sorted(float(r.features["mf.vol_21"][0]) * math.sqrt(H) for r in fit
                      if r.features["mf.vol_21"][0] is not None)
    assert b["floor"] == pytest.approx(float(np.quantile(positive, 0.05)))
    assert b["quantile"]["low"] > b["quantile"]["mid"] > b["quantile"]["high"]
    iv, flags = cal.intervals(art, held, pred[6000:])
    rep = cal.coverage_report(d, held, iv, H)
    for g in ("overall", "low", "mid", "high"):
        assert abs(rep[g]["coverage"] - 0.9) < 0.03, (g, rep[g])
    assert rep["overall"]["dates"] == 100
    nulls = [i for i, r in enumerate(held) if r.features["mf.vol_21"][0] is None]
    assert nulls and all("vol_21_null" in flags[i] for i in nulls)
    lo, hi = iv[nulls[0]]
    assert (hi - lo) / 2 == pytest.approx(b["quantile"][held[nulls[0]].meta["liquidity_class"]] * b["floor"])
    pooled = sorted(abs(y - p) for y, p in zip(data.targets(d, [r.key for r in conf], H).ret, pred[3000:6000]))
    # one pooled quantile on raw residuals undercovers the low class: why the quantile is class-conditional
    q = pooled[math.ceil(len(pooled) * 0.9) - 1]
    pooled_iv = [(p - q, p + q) for p in pred[6000:]]
    assert cal.coverage_report(d, held, pooled_iv, H)["low"]["coverage"] < 0.85


def test_i2_too_few_rows_give_no_interval_and_slices_are_enforced():
    d, rows, pred = heteroscedastic(n=600)
    fit, conf = rows[:300], rows[300:315]                    # 5 conformal rows per class: below the level's need
    art = cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, pred[300:315], d, H, 0.9)
    iv, flags = cal.intervals(art, rows[400:403], pred[400:403])
    assert iv == [None] * 3 and all("insufficient_rows" in f for f in flags)
    with pytest.raises(Refused, match="wrong_slice"):
        cal.fit_intervals(scope("training", fit), fit, scope("conformal", conf), conf, pred[300:315], d, H)
    with pytest.raises(Refused, match="wrong_slice"):
        cal.fit_intervals(scope("fit", fit), fit, scope("fit", conf), conf, pred[300:315], d, H)


# ------------------------------------------------------------------------------------------------ MLK-7


def planted(seed, size):
    """Direction depends on mf.x with a stated effect size; mf.noise1, mf.noise2 are irrelevant."""
    rnd = random.Random(seed)
    rows, labels = [], {}
    for i in range(6000):
        x = rnd.gauss(0, 1)
        feats = {"mf.x": (D(str(round(x, 6))), "fresh", None),
                 "mf.noise1": (D(str(round(rnd.gauss(0, 1), 6))), "fresh", None),
                 "mf.noise2": (D(str(round(rnd.gauss(0, 1), 6))), "fresh", None)}
        r = row(i, feats, per_day=10)
        lat = size * x + rnd.gauss(0, 1)
        k = 0 if lat > 0.5 else 2 if lat < -0.5 else 1
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label(CLS[k], {0: 0.01, 1: 0.0, 2: -0.01}[k])
    return dataset(rows, labels), rows


def test_mlk7_a_planted_signal_passes_the_gate_test_in_most_seeds_and_is_named():
    """Stated size 0.5 (standard deviations of the latent per unit of mf.x); stated share 4 of 5 seeds at level 0.05
    with delta 0, B = 9 999 and the design's block length floor."""
    passed, named = 0, 0
    for seed in range(5):
        d, rows = planted(seed, 0.5)
        fit, test = rows[:3000], rows[3000:]
        tr = data.train_set(d, scope("fit", fit), fit, H)
        art = linear.fit(tr, seed=seed)
        X, keys = data.matrix(test, linear.schema_of(art))
        p = linear.predict(art, X, keys)
        prior = baselines.predict(baselines.fit(data.train_set(d, scope("training", fit), fit, H)), test,
                                  H)["base_prior"]
        actual = [d.labels[(r.symbol, r.session, H)].values["direction"] for r in test]
        loss = lambda ps: [-math.log(max(q[CLS.index(a)], 1e-15)) for q, a in zip(ps, actual)]   # noqa: E731
        diff = metrics.by_date([r.session for r in test], [m - b for m, b in zip(loss(p.probs), loss(prior.probs))])
        res = bootstrap.gate_test(list(diff.values()), delta=0.0, h=H, level=0.05, seed=seed)
        passed += bool(res.rejects)
        mean_abs = {}
        for c in p.contributions:
            for f, v in c["direction"].items():
                if f != "intercept":
                    mean_abs[f] = mean_abs.get(f, 0.0) + abs(v)
        named += max(mean_abs, key=mean_abs.get) == "mf.x"
    assert passed >= 4 and named == 5


def test_i3_coverage_is_averaged_over_dates_not_rows():
    """§8.2's estimand: one date with 1 row inside and another with 3 rows outside is 0.5, not 0.25."""
    rows, labels = [], {}
    for i, (day, y) in enumerate([(0, 0.0), (1, 5.0), (1, 5.0), (1, 5.0)]):
        session = date(2024, 1, 1) + timedelta(days=day)
        r = ds.Row(f"S{i}.N0000", session, None, i, {}, {"liquidity_class": "mid"}, ())
        rows.append(r)
        labels[(r.symbol, session, H)] = label("flat", y)
    rep = cal.coverage_report(dataset(rows, labels), rows, [(-1.0, 1.0)] * 4, H)
    assert rep["overall"] == {"coverage": 0.5, "dates": 2, "rows": 4} and rep["mid"]["coverage"] == 0.5
