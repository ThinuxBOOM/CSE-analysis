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
    return FitScope(name, frozenset(r.key for r in rows), H)


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
    rep = cal.coverage_report(d, held, iv, H, 0.9, 0.03)
    for g in ("overall", "low", "mid", "high"):
        assert rep[g]["within_tolerance"] and abs(rep[g]["coverage"] - 0.9) < 0.03, (g, rep[g])
    assert rep["overall"]["dates"] == 100
    nulls = [i for i, r in enumerate(held) if r.features["mf.vol_21"][0] is None]
    assert nulls and all("vol_21_null" in flags[i] for i in nulls)
    lo, hi = iv[nulls[0]]
    assert (hi - lo) / 2 == pytest.approx(b["quantile"][held[nulls[0]].meta["liquidity_class"]] * b["floor"])
    pooled = sorted(abs(y - p) for y, p in zip(data.targets(d, [r.key for r in conf], H).ret, pred[3000:6000]))
    # one pooled quantile on raw residuals undercovers the low class: why the quantile is class-conditional
    q = pooled[math.ceil(len(pooled) * 0.9) - 1]
    pooled_iv = [(p - q, p + q) for p in pred[6000:]]
    assert cal.coverage_report(d, held, pooled_iv, H, 0.9, 0.03)["low"]["coverage"] < 0.85


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


def gate(seed, size, adapter):
    d, rows = planted(seed, size)
    fit, test = rows[:3000], rows[3000:]
    art = adapter.fit(data.train_set(d, scope("fit", fit), fit, H), seed=seed)
    X, keys = data.matrix(test, adapter.schema_of(art))
    p = adapter.predict(art, X, keys)
    prior = baselines.predict(baselines.fit(data.train_set(d, scope("training", fit), fit, H)), test, H)["base_prior"]
    actual = [d.labels[(r.symbol, r.session, H)].values["direction"] for r in test]
    loss = lambda ps: [-math.log(max(q[CLS.index(a)], 1e-15)) for q, a in zip(ps, actual)]   # noqa: E731
    diff = metrics.by_date([r.session for r in test], [m - b for m, b in zip(loss(p.probs), loss(prior.probs))])
    res = bootstrap.gate_test(list(diff.values()), delta=0.0, h=H, level=0.05, seed=seed)
    mean_abs = {}
    for c in p.contributions:
        for f, v in c["direction"].items():
            if f not in ("intercept", "bias"):
                mean_abs[f] = mean_abs.get(f, 0.0) + abs(v)
    return bool(res.rejects), max(mean_abs, key=mean_abs.get)


def test_mlk7_a_planted_signal_passes_the_gate_test_in_most_seeds_and_is_named():
    """Stated size 0.15 (standard deviations of the latent per unit of mf.x), near where power is not a certainty;
    stated share 4 of 5 seeds at level 0.05, delta 0, B = 9 999 and the design's block length floor; contributions
    name mf.x for both ML adapters. Control: with no signal (size 0) the test rejects in at most 1 of 5 seeds."""
    from worker.ml_models import gbt
    res = [gate(seed, 0.15, linear) for seed in range(5)]
    assert sum(r for r, _ in res) >= 4 and all(f == "mf.x" for _, f in res)
    assert all(gate(seed, 0.15, gbt)[1] == "mf.x" for seed in range(2))
    assert sum(gate(seed, 0.0, linear)[0] for seed in range(5)) <= 1


def test_i3_coverage_is_averaged_over_dates_not_rows():
    """§8.2's estimand: one date with 1 row inside and another with 3 rows outside is 0.5, not 0.25."""
    rows, labels = [], {}
    for i, (day, y) in enumerate([(0, 0.0), (1, 5.0), (1, 5.0), (1, 5.0)]):
        session = date(2024, 1, 1) + timedelta(days=day)
        r = ds.Row(f"S{i}.N0000", session, None, i, {}, {"liquidity_class": "mid"}, ())
        rows.append(r)
        labels[(r.symbol, session, H)] = label("flat", y)
    rep = cal.coverage_report(dataset(rows, labels), rows, [(-1.0, 1.0)] * 4, H, 0.9, 0.05)
    o = rep["overall"]
    assert (o["coverage"], o["dates"], o["rows"], o["no_interval"]) == (0.5, 2, 4, 0) and rep["mid"]["coverage"] == 0.5
    assert o["coarse"] and o["windows"] == 2 / H and not o["within_tolerance"]


# ------------------------------------------------------------------------------------------------ first M5-5 audit


def test_a1_untrained_labels_never_reach_a_fitted_value():
    rnd = random.Random(3)
    rows, labels, raw = [], {}, []
    for i in range(1200):
        lg = np.array([rnd.gauss(0, 1), 0.0, rnd.gauss(0, 1)])
        p = np.exp(1.5 * lg) / np.exp(1.5 * lg).sum()
        k = min(int(np.searchsorted(np.cumsum(np.exp(lg) / np.exp(lg).sum()), rnd.random())), 2)
        r = row(i, {"mf.vol_21": (D("0.01"), "fresh", None)}, liq=("low", "mid", "high")[i % 3])
        rows.append(r)
        raw.append(tuple(p.tolist()))
        labels[(r.symbol, r.session, H)] = label(CLS[k], 0.01 * (k - 1) * -1)
    pend = dict(labels)
    for r in rows[::4]:
        pend[(r.symbol, r.session, H)] = ds.Label("pending", {}, None, "0" * 64)
    a = cal.fit_vector_scaling(scope("calibrate", rows), rows, raw, dataset(rows, pend), H).load("vector_scaling")
    keep = [i for i in range(1200) if i % 4]
    b = cal.fit_vector_scaling(scope("calibrate", [rows[i] for i in keep]), [rows[i] for i in keep],
                               [raw[i] for i in keep], dataset(rows, labels), H).load("vector_scaling")
    assert a["rows_used"] == 900 and np.allclose(a["scale"], b["scale"]) and np.allclose(a["offset"], b["offset"])
    rep = cal.reliability_report(dataset(rows, pend), rows, raw, H)
    assert rep["overall"][2] == 900
    pred = [0.0] * 1200
    q1 = cal.fit_intervals(scope("fit", rows), rows, scope("conformal", rows), rows, pred, dataset(rows, pend), H)
    q2 = cal.fit_intervals(scope("fit", rows), rows, scope("conformal", [rows[i] for i in keep]),
                           [rows[i] for i in keep], [0.0] * 900, dataset(rows, labels), H)
    assert q1.load("conformal")["quantile"] == q2.load("conformal")["quantile"]


def test_a2_scale_floor_and_classes_by_interval_width():
    rnd = random.Random(9)
    rows, labels = [], {}
    for i in range(3000):
        liq = ("low", "mid", "high", "unknown")[i % 4]
        vol = 0.0 if i % 10 == 0 else 0.002 + 0.02 * rnd.random()            # zero vols: not in the floor's base
        r = row(i, {"mf.vol_21": (D(str(round(vol, 8))), "fresh", None)}, liq=liq, per_day=30)
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label("rise", round(rnd.gauss(0, 1) * max(vol, 0.003) * (3 if liq ==
                                                                                                      "unknown" else 1), 10))
    d = dataset(rows, labels)
    fit, conf = rows[:1500], rows[1500:]
    art = cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, [0.0] * 1500, d, H)
    b = art.load("conformal")
    positive = [float(r.features["mf.vol_21"][0]) * math.sqrt(H) for r in fit if float(r.features["mf.vol_21"][0]) > 0]
    assert b["floor"] == pytest.approx(float(np.quantile(positive, 0.05)))
    assert b["quantile"]["unknown"] > 2 * b["quantile"]["mid"]                # its own quantile, not pooled
    probe = [row(10 ** 6 + k, {"mf.vol_21": (D(v), "fresh", None)}, liq="mid") for k, v in
             enumerate(("0.02", "0.0000001"))]
    (lo1, hi1), (lo2, hi2) = cal.intervals(art, probe, [0.0, 0.0])[0]
    assert (hi1 - lo1) / 2 == pytest.approx(b["quantile"]["mid"] * 0.02 * math.sqrt(H))      # q x vol x sqrt(h)
    assert (hi2 - lo2) / 2 == pytest.approx(b["quantile"]["mid"] * b["floor"])               # the floor binds


def test_a3_per_class_scales_for_class_dependent_miscalibration():
    rnd = random.Random(4)
    rows, labels, raw = [], {}, []
    temp = np.array([3.0, 1.0, 0.4])                    # rise far too confident, fall far too timid
    for i in range(6000):
        lg = np.array([rnd.gauss(0, 1), rnd.gauss(0, 0.3), rnd.gauss(0, 1)])
        pt = np.exp(lg) / np.exp(lg).sum()
        k = min(int(np.searchsorted(np.cumsum(pt), rnd.random())), 2)
        z = np.exp(lg * temp)
        raw.append(tuple((z / z.sum()).tolist()))
        r = row(i, {})
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label(CLS[k], 0)
    a = cal.fit_vector_scaling(scope("calibrate", rows), rows, raw, dataset(rows, labels), H).load("vector_scaling")
    assert a["scale"][2] > 2 * a["scale"][0] > 0              # one shared scale could not do this


def test_a4_reliability_needs_evidence_and_every_large_group():
    assert not cal.reliability_ok({}, 0.05)
    assert not cal.reliability_ok({"overall": (0.0, {}, 10)}, 0.05)                       # too few rows
    ok = {"overall": (0.01, {}, 500), "low": (0.2, {}, 200), "unknown": (0.5, {}, 5)}
    assert not cal.reliability_ok(ok, 0.05)                                                # a large group fails
    assert cal.reliability_ok({"overall": (0.01, {}, 500), "unknown": (0.5, {}, 5)}, 0.05)  # a tiny one is not judged


def test_a5_reports_keep_every_class_and_bounds_are_inclusive():
    rows, labels = [], {}
    for i, (liq, y) in enumerate([("low", 0.0), ("mid", 1.0), ("mid", -1.0), ("high", 0.2)]):
        r = row(i, {}, liq=liq)
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label("flat", y)
    iv = [None, (-1.0, 1.0), (-1.0, 1.0), (0.0, 0.5)]                       # low: no interval
    rep = cal.coverage_report(dataset(rows, labels), rows, iv, H, 0.9, 0.05)
    assert rep["low"]["coverage"] is None and rep["low"]["no_interval"] == 1 and not rep["low"]["within_tolerance"]
    assert rep["mid"]["coverage"] == 1.0                                     # y on the bounds counts as inside
    assert rep["overall"]["no_interval"] == 1 and not rep["overall"]["within_tolerance"]


def test_a6_inputs_are_checked():
    d, rows, pred = heteroscedastic(n=900)
    fit, conf = rows[:300], rows[300:600]
    with pytest.raises(ValueError, match="for 300 rows"):
        cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, pred[300:400], d, H)
    with pytest.raises(ValueError, match="finite"):
        cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, [float("nan")] + pred[301:600], d, H)
    with pytest.raises(ValueError, match="level"):
        cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, pred[300:600], d, H, 0)
    art = cal.fit_intervals(scope("fit", fit), fit, scope("conformal", conf), conf, pred[300:600], d, H)
    with pytest.raises(ValueError, match="fitted for h=5"):
        cal.intervals(art, rows[600:610], pred[600:610], horizon=1)
    iv, flags = cal.intervals(art, rows[600:602], [float("nan"), 0.0])
    assert iv[0] is None and "prediction_not_finite" in flags[0]
    with pytest.raises(ValueError, match="three finite"):
        cal.fit_vector_scaling(scope("calibrate", conf), conf, [(0.5, 0.5, 0.5)] * 300, d, H)
    with pytest.raises(ValueError, match="for 300 rows"):
        cal.fit_vector_scaling(scope("calibrate", conf), conf, [(0.2, 0.3, 0.5)] * 30, d, H)


def test_a7_an_anti_informative_model_is_flagged():
    rnd = random.Random(8)
    rows, labels, raw = [], {}, []
    for i in range(3000):
        k = rnd.randrange(3)
        p = [0.1, 0.1, 0.1]
        p[2 - k] = 0.8                                        # confidently wrong: the reverse of the truth
        raw.append(tuple(p))
        r = row(i, {})
        rows.append(r)
        labels[(r.symbol, r.session, H)] = label(CLS[k], 0)
    a = cal.fit_vector_scaling(scope("calibrate", rows), rows, raw, dataset(rows, labels), H).load("vector_scaling")
    assert "scale_negative" in a["flags"]

