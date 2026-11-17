"""
Phase 5 M5-4 (docs/PHASE5_ML_DESIGN.md §7, §13): adapters and baselines on synthetic datasets with a planted signal.
MLK-3 (fit scope), MLK-9 (label shuffle: skill at chance), MLK-11 (determinism: identical artifact hashes and
predictions), MLK-16 (a changed byte refused), engine scores never an ML input, contributions that add up, every
baseline against a hand-computed case. Linux only (the ML libraries are pinned for the Linux image).
"""
import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")

from worker.ml_dataset import dataset as ds                    # noqa: E402
from worker.ml_models import baselines, data, gbt, linear       # noqa: E402
from worker.ml_models.artifact import Artifact, Refused, q9     # noqa: E402
from worker.ml_protocol.folds import FitScope                   # noqa: E402
from worker.ml_protocol.folds import Refused as ScopeRefused    # noqa: E402

H = 5


def synthetic(n=600, seed=1, shuffle=False, signal=1.0, date_shock=0.0):
    """Rows with a planted signal in mf.x: the return is signal * x + noise (+ a common shock per date); a decoy de.*
    score equal to the label. shuffle: False, "across" (all rows) or "within" (within each date)."""
    rnd = random.Random(seed)
    shocks = {}
    rows, labels = [], {}
    for i in range(n):
        sym, session = f"S{i % 40:02d}.N0000", date(2024, 1, 1) + timedelta(days=i // 40)
        x, z = rnd.gauss(0, 1), rnd.gauss(0, 1)
        shock = shocks.setdefault(session, rnd.gauss(0, 1))
        ret = 0.01 * (signal * x + 0.7 * rnd.gauss(0, 1) + date_shock * shock)
        feats = {"mf.x": (D(str(round(x, 6))), "fresh", None), "mf.z": (D(str(round(z, 6))), "fresh", None),
                 "mf.gap": (None, "missing", "no_trades") if i % 7 == 0 else (D(str(round(rnd.random(), 6))),
                                                                              "fresh", None),
                 "f7.eligibility": ("eligible" if i % 3 else "stale", "fresh", None),
                 "de.fund_growth": (D(str(round(ret * 100, 6))), "scored", None)}
        rows.append(ds.Row(sym, session, datetime.combine(session, datetime.min.time(), timezone.utc), i, feats,
                           {}, ()))
        direction = "rise" if ret > 0.004 else "fall" if ret < -0.004 else "flat"
        labels[(sym, session, H)] = {"direction": direction, "ret": str(round(ret, 8)),
                                     "volatility": str(round(abs(ret) * 1.5, 8)), "volatility_state": "resolved"}
    if shuffle == "within":
        by = {}
        for k in labels:
            by.setdefault(k[1], []).append(k)
        out = {}
        for keys in by.values():
            vals = [labels[k] for k in keys]
            rnd.shuffle(vals)
            out.update(zip(keys, vals))
        labels = out
    elif shuffle:
        vals = list(labels.values())
        rnd.shuffle(vals)
        labels = dict(zip(labels, vals))
    lab = {k: ds.Label("resolved", v, None, f"{hash(k) & 0xffff:064x}") for k, v in labels.items()}
    return ds.Dataset(None, "v", tuple(rows), lab, "point_in_time", "complete", "all", {})


def split(d, frac=0.7, name="fit", h=H):
    k = int(len(d.rows) * frac)
    fit_rows, test_rows = d.rows[:k], d.rows[k:]
    scope = FitScope(name, frozenset(r.key for r in fit_rows))
    return data.train_set(d, scope, fit_rows, h), test_rows


def logloss(preds, d, rows):
    out = []
    for p, r in zip(preds.probs, rows):
        lab = d.labels[(r.symbol, r.session, H)].values["direction"]
        out.append(-math.log(max(p[("rise", "flat", "fall").index(lab)], 1e-15)))
    return sum(out) / len(out)


@pytest.mark.parametrize("adapter", [linear, gbt])
def test_mlk11_deterministic_and_mlk16_integrity(adapter):
    d = synthetic()
    tr, test = split(d)
    a1, a2 = adapter.fit(tr, seed=7), adapter.fit(tr, seed=7)
    assert a1.sha256 == a2.sha256 and a1.text == a2.text
    X, keys = data.matrix(test, adapter.schema_of(a1))
    p1, p2 = adapter.predict(a1, X, keys), adapter.predict(a2, X, keys)
    assert [tuple(map(q9, p)) for p in p1.probs] == [tuple(map(q9, p)) for p in p2.probs]
    assert list(map(q9, p1.ret)) == list(map(q9, p2.ret))
    bad = Artifact(a1.adapter, a1.text.replace("0", "1", 1), a1.sha256)
    with pytest.raises(Refused, match="artifact_integrity"):
        adapter.predict(bad, X, keys)
    with pytest.raises(Refused, match="artifact_adapter"):
        (gbt if adapter is linear else linear).predict(a1, X, keys)
    desc = adapter.describe(a1)
    assert desc["environment"]["threads"] == 1 and desc["environment"]["seed"] == 7


@pytest.mark.parametrize("adapter", [linear, gbt])
def test_planted_signal_beats_the_prior_and_mlk9_shuffle_does_not(adapter):
    for shuffle in (False, True):
        d = synthetic(n=2000, shuffle=shuffle and "across")
        tr, test = split(d)
        art = adapter.fit(tr, seed=3)
        X, keys = data.matrix(test, adapter.schema_of(art))
        model = logloss(adapter.predict(art, X, keys), d, test)
        prior = logloss(baselines.predict(baselines.fit(split(d, name="training")[0]), test, H)["base_prior"], d,
                        test)
        if shuffle:
            assert model > prior - 0.03                     # no skill beyond chance on shuffled labels
        else:
            assert model < prior - 0.1                      # the planted signal is found


def test_engine_scores_are_never_ml_inputs_and_mlk3_fit_scope():
    d = synthetic(n=200)
    tr, _ = split(d)
    assert not any(c.startswith("de.") for c in tr.schema.columns())
    assert "f7.eligibility=eligible" in tr.schema.columns() and "mf.gap" in tr.schema.numeric
    with pytest.raises(ScopeRefused, match="fit_scope"):
        data.train_set(d, FitScope("fit", frozenset(r.key for r in d.rows[:10])), d.rows[:20], H)


def test_contributions_add_up():
    d = synthetic()
    tr, test = split(d)
    art = gbt.fit(tr, seed=1)
    X, keys = data.matrix(test, gbt.schema_of(art))
    p = gbt.predict(art, X, keys)
    for i in range(5):
        assert abs(sum(p.contributions[i]["return"].values()) - p.ret[i]) < 1e-9
    la = linear.fit(tr, seed=1)
    lp = linear.predict(la, *data.matrix(test, linear.schema_of(la)))
    b = la.load("linear")
    for i in range(5):
        assert abs(sum(lp.contributions[i]["return"].values()) - lp.ret[i]) < 1e-9       # base (intercept) included
    assert all(v >= 0 for v in p.volatility + lp.volatility)
    assert any(c.startswith("missing:") for c in b["columns"])            # mf.gap has nulls: an indicator


def test_baselines_by_hand():
    d = synthetic(n=200)
    tr, _ = split(d, 0.5, name="training")
    art = baselines.fit(tr, {"interval_level": 0.8})
    b = art.load("ref")
    y = tr.y
    assert b["freqs"] == [float(np.mean(y.direction == k)) for k in range(3)]
    assert abs(b["mean"] - float(np.mean(y.ret))) < 1e-15
    resid = np.sort(np.abs(y.ret - b["mean"]))
    assert b["q"] == float(resid[math.ceil(101 * 0.8) - 1])
    row = lambda **f: ds.Row("A", date(2024, 1, 1), None, 0,                       # noqa: E731
                             {f"mf.{k}": (None if v is None else D(str(v)), "fresh", None) for k, v in f.items()}, {}, ())
    rows = [row(hist_up_share_5=0.5, hist_flat_share_5=0.0, hist_down_share_5=0.5, hist_mean_ret_5=0.001,
                hist_n_5=10, mom_252_21=0, vol_21=0.02),
            row(mom_252_21=-0.1, vol_21=None)]
    d1 = ds.Dataset(None, "v", d.rows, {(k[0], k[1], 1): v for k, v in d.labels.items()}, "point_in_time",
                    "complete", "all", {})
    h1 = baselines.predict(baselines.fit(split(d1, 0.5, name="training", h=1)[0]), rows, 1)
    assert h1["base_vol_trailing"].volatility[0] == pytest.approx(0.02 * math.sqrt(2 / math.pi), abs=1e-15)
    with pytest.raises(ValueError, match="another horizon"):
        baselines.predict(art, rows, 1)
    out = baselines.predict(art, rows, 5)
    # (share x n + 1) / (n + 3) with n = 10: (6, 1, 6) / 13; a rise-fall tie has no class (Phase 4's rule)
    assert out["base_rate_252"].probs[0] == pytest.approx((6 / 13, 1 / 13, 6 / 13), abs=1e-15)
    assert out["base_rate_252"].direction[0] is None and out["base_rate_252"].ret[0] == pytest.approx(0.001)
    assert out["base_rate_252"].probs[1] is None and out["base_rate_252"].ret[1] is None
    assert out["base_momentum_sign"].direction == [None, "fall"]               # zero momentum: silent
    assert out["base_no_change"].ret == [0.0, 0.0] and out["base_no_change"].direction == ["flat", "flat"]
    assert out["base_vol_trailing"].volatility == [0.02, None]
    lo, hi = out["base_interval"].interval[0]
    assert hi - lo == pytest.approx(2 * b["q"]) and (lo + hi) / 2 == pytest.approx(b["mean"], abs=1e-15)


# ------------------------------------------------------------------------------------------------ first M5-4 audit


@pytest.mark.parametrize("adapter", [linear, gbt])
@pytest.mark.parametrize("seed", [11, 12, 13])
def test_mlk9_shuffles_within_and_across_dates_leave_no_skill(adapter, seed):
    """MLK-9 (§13): labels shuffled within each date and across dates, on data with a common shock per date, give
    skill at chance against base_prior."""
    for how in ("within", "across"):
        d = synthetic(n=2000, seed=seed, shuffle=how, date_shock=1.0)
        tr, test = split(d)
        art = adapter.fit(tr, seed=seed)
        model = logloss(adapter.predict(art, *data.matrix(test, adapter.schema_of(art))), d, test)
        prior = logloss(baselines.predict(baselines.fit(split(d, name="training")[0]), test, H)["base_prior"], d,
                        test)
        assert model > prior - 0.03, (how, model, prior)


def test_m1_each_adapter_fits_only_its_own_slice():
    d = synthetic(n=300)
    fit_tr, _ = split(d, name="fit")
    all_tr, _ = split(d, name="training")
    for a in (linear, gbt):
        with pytest.raises(Refused, match="wrong_slice"):
            a.fit(all_tr)
        assert a.describe(a.fit(fit_tr))  # fits
        assert a.fit(fit_tr).load(a.ADAPTER)["slice"] == "fit"
    with pytest.raises(Refused, match="wrong_slice"):
        baselines.fit(fit_tr)
    loose = data.Train(H, fit_tr.schema, fit_tr.X, fit_tr.y, fit_tr.rows, "fit")       # not made by train_set
    for f in (linear.fit, gbt.fit):
        with pytest.raises(Refused, match="train_not_scoped"):
            f(loose)


def test_m2_targets_train_only_trained_states_and_need_the_volatility_state():
    d = synthetic(n=60)
    keys = [r.key for r in d.rows]
    labels = dict(d.labels)
    k0, k1 = (keys[0][0], keys[0][1], H), (keys[1][0], keys[1][1], H)
    labels[k0] = ds.Label("terminated", labels[k0].values, None, "a" * 64)
    labels[k1] = ds.Label("resolved", {**labels[k1].values, "volatility_state": "unresolvable_quality"}, None,
                          "b" * 64)
    d2 = ds.Dataset(None, "v", d.rows, labels, "point_in_time", "complete", "all", {})
    t = data.targets(d2, keys, H)
    assert t.direction[0] == -1 and np.isnan(t.ret[0]) and np.isnan(t.volatility[0])
    assert t.direction[1] >= 0 and np.isnan(t.volatility[1]) and not np.isnan(t.volatility[2])
    labels[k0] = ds.Label("resolved", {k: v for k, v in labels[k1].values.items() if k != "volatility_state"},
                          None, "c" * 64)
    with pytest.raises(ValueError, match="volatility_state"):
        data.targets(ds.Dataset(None, "v", d.rows, labels, "point_in_time", "complete", "all", {}), keys, H)


def test_m2_the_schema_and_every_fitted_part_come_from_the_admitted_rows():
    d = synthetic(n=300)
    rows = list(d.rows)
    later = rows[200]
    feats = {**later.features, "f7.eligibility": ("insufficient_history", "fresh", None), "mf.only_later": (D(1),
                                                                                                    "fresh", None)}
    rows[200] = ds.Row(later.symbol, later.session, later.t, later.snapshot_id, feats, {}, ())
    d2 = ds.Dataset(None, "v", tuple(rows), d.labels, "point_in_time", "complete", "all", {})
    tr, _ = split(d2, frac=0.5)
    cols = tr.schema.columns()
    assert "mf.only_later" not in cols and "f7.eligibility=insufficient_history" not in cols
    b = linear.fit(tr).load("linear")
    X = tr.X
    med = np.nanmedian(X, axis=0)
    assert b["median"] == pytest.approx(np.where(np.isnan(med), 0.0, med).tolist())       # median, not mean
    Xi = np.where(np.isnan(X), med, X)
    Xi = np.hstack([Xi, np.isnan(X)[:, b["indicators"]].astype(float)])
    assert b["mean"] == pytest.approx(Xi.mean(axis=0).tolist())                           # standardised on the slice
    assert b["scale"] == pytest.approx(np.where(Xi.std(axis=0) > 0, Xi.std(axis=0), 1.0).tolist())


def test_m2_direction_contributions_are_the_predicted_class_and_floors_hold():
    d = synthetic()
    tr, test = split(d)
    la = linear.fit(tr, hyperparameters={"volatility_floor": 0.015})
    X, keys = data.matrix(test, linear.schema_of(la))
    lp = linear.predict(la, X, keys)
    b = la.load("linear")
    from worker.ml_models.linear import _design
    Z = _design(X, b)
    logits = Z @ np.array(b["direction"]["coef"]).T + np.array(b["direction"]["intercept"])
    for i in range(20):
        k = int(np.argmax(logits[i]))
        assert sum(lp.contributions[i]["direction"].values()) == pytest.approx(logits[i, k], abs=1e-9)
    assert min(lp.volatility) >= 0.015 and lp.volatility.count(0.015) > 0                 # linear's floor binds
    ga = gbt.fit(tr)
    gp = gbt.predict(ga, *data.matrix(test, gbt.schema_of(ga)))
    import lightgbm as lgb
    raw = lgb.Booster(model_str=ga.load("gbt")["direction"]).predict(data.matrix(test, gbt.schema_of(ga))[0],
                                                                         raw_score=True)
    for i in range(20):
        k = int(np.argmax(raw[i]))
        assert sum(gp.contributions[i]["direction"].values()) == pytest.approx(raw[i, k], abs=1e-9)
    for out in ("direction", "return", "volatility"):
        txt = ga.load("gbt")[out]
        for p in ("[bagging_fraction: 1]", "[feature_fraction: 1]", "[num_threads: 1]", "[deterministic: 1]"):
            assert p in txt, (out, p)


def test_m2_artifacts_refuse_nan_and_a_wrong_matrix():
    from worker.ml_models.artifact import dumps
    with pytest.raises(ValueError):
        dumps({"x": float("nan")})
    d = synthetic(n=300)
    tr, test = split(d)
    for a in (linear, gbt):
        art = a.fit(tr)
        X, keys = data.matrix(test, a.schema_of(art))
        with pytest.raises(ValueError, match="columns"):
            a.predict(art, X[:, 1:], keys)
    assert data.direction_of((0.4, 0.2, 0.4)) is None and data.direction_of((0.3, 0.4, 0.3)) == "flat"
    assert data.direction_of((0.4, 0.4, 0.2)) == "flat" and data.direction_of((0.5, 0.2, 0.3)) == "rise"
