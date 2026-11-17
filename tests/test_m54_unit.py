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


def synthetic(n=600, seed=1, shuffle=False, signal=1.0):
    """Rows with a planted signal in mf.x: the return is signal * x + noise; a decoy de.* score equal to the label."""
    rnd = random.Random(seed)
    rows, labels = [], {}
    for i in range(n):
        sym, session = f"S{i % 40:02d}.N0000", date(2024, 1, 1) + timedelta(days=i // 40)
        x, z = rnd.gauss(0, 1), rnd.gauss(0, 1)
        ret = 0.01 * (signal * x + 0.7 * rnd.gauss(0, 1))
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
    if shuffle:
        vals = list(labels.values())
        rnd.shuffle(vals)
        labels = dict(zip(labels, vals))
    lab = {k: ds.Label("resolved", v, None, f"{hash(k) & 0xffff:064x}") for k, v in labels.items()}
    return ds.Dataset(None, "v", tuple(rows), lab, "point_in_time", "complete", "all", {})


def split(d, frac=0.7):
    k = int(len(d.rows) * frac)
    fit_rows, test_rows = d.rows[:k], d.rows[k:]
    scope = FitScope("fit", frozenset(r.key for r in fit_rows))
    return data.train_set(d, scope, fit_rows, H), test_rows


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
        d = synthetic(n=2000, shuffle=shuffle)
        tr, test = split(d)
        art = adapter.fit(tr, seed=3)
        X, keys = data.matrix(test, adapter.schema_of(art))
        model = logloss(adapter.predict(art, X, keys), d, test)
        prior = logloss(baselines.predict(baselines.fit(tr), test, H)["base_prior"], d, test)
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
        assert abs(sum(lp.contributions[i]["return"].values()) + b["return"]["intercept"] - lp.ret[i]) < 1e-9
    assert all(v >= 0 for v in p.volatility + lp.volatility)
    assert any(c.startswith("missing:") for c in b["columns"])            # mf.gap has nulls: an indicator


def test_baselines_by_hand():
    d = synthetic(n=200)
    tr, _ = split(d, 0.5)
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
    h1 = baselines.predict(baselines.fit(data.Train(1, tr.schema, tr.X, tr.y, tr.rows)), rows, 1)
    assert h1["base_vol_trailing"].volatility[0] == pytest.approx(0.02 * math.sqrt(2 / math.pi), abs=1e-15)
    with pytest.raises(ValueError, match="another horizon"):
        baselines.predict(art, rows, 1)
    out = baselines.predict(art, rows, 5)
    # (share x n + 1) / (n + 3) with n = 10: (6, 1, 6) / 13
    assert out["base_rate_252"].probs[0] == pytest.approx((6 / 13, 1 / 13, 6 / 13), abs=1e-15)
    assert out["base_rate_252"].direction[0] == "rise" and out["base_rate_252"].ret[0] == pytest.approx(0.001)
    assert out["base_rate_252"].probs[1] is None and out["base_rate_252"].ret[1] is None
    assert out["base_momentum_sign"].direction == [None, "fall"]               # zero momentum: silent
    assert out["base_no_change"].ret == [0.0, 0.0] and out["base_no_change"].direction == ["flat", "flat"]
    assert out["base_vol_trailing"].volatility == [0.02, None]
    lo, hi = out["base_interval"].interval[0]
    assert hi - lo == pytest.approx(2 * b["q"])


def test_mlk14_dependency_boundary():
    import ast
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ml = ("numpy", "scipy", "sklearn", "lightgbm", "joblib", "threadpoolctl")
    guarded = ["market_capture", "backfill_transport", "backfill_discovery", "backfill_documents", "backfill_f6",
               "backfill_ops", "financial_backfill", "financial_truth", "financial_truth_store", "financial_f4_store"]
    def imports(path):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                yield from (a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                yield node.module
    for pkg in guarded:
        for root, _d, files in os.walk(os.path.join(repo, "worker", pkg)):
            for f in files:
                if f.endswith(".py"):
                    bad = [m for m in imports(os.path.join(root, f)) if m.split(".")[0] in ml]
                    assert not bad, (pkg, f, bad)
    for pkg in ("ml_models", "ml_dataset", "ml_protocol", "evaluation_metrics"):
        for f in os.listdir(os.path.join(repo, "worker", pkg)):
            if f.endswith(".py"):
                bad = [m for m in imports(os.path.join(repo, "worker", pkg, f))
                       if any(x in m for x in ("cse_client", "backfill_transport", "requests", "urllib", "socket"))]
                assert not bad, (pkg, f, bad)
