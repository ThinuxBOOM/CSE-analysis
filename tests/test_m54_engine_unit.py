"""
Phase 5 M5-4 part 2 (docs/PHASE5_ML_DESIGN.md §7.2): `calibrated_engine`, the calibration of one Phase 4 engine. It
fits only on fit-slice rows where the engine used its full component set; it is silent elsewhere; its provenance names
the engine and its version; its contributions are the engine's own components and add up exactly; it is deterministic
and refuses a wrong slice or an unknown engine. Linux only (the ML libraries).
"""
import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from worker.engines import registry                              # noqa: E402
from worker.ml_dataset import dataset as ds                      # noqa: E402
from worker.ml_models import baselines, calibrated_engine as ce, data   # noqa: E402
from worker.ml_models.artifact import Refused, q9                # noqa: E402
from worker.ml_protocol.folds import FitScope                    # noqa: E402

H, E = 5, "fund_growth"


def world(n=1200, seed=2, partial_noise=False):
    """The engine's score s drives the label; rows where the engine missed a component (full False) carry labels
    that contradict s, so a calibration that used them would be pulled off."""
    rnd = random.Random(seed)
    rows, labels = [], {}
    for i in range(n):
        sym, session = f"S{i % 40:02d}.N0000", date(2024, 1, 1) + timedelta(days=i // 40)
        s = max(-0.99, min(0.99, rnd.gauss(0, 0.45)))
        full = i % 4 != 0
        a = round(s * 0.6, 12)
        feats = {"mf.x": (D(str(round(rnd.gauss(0, 1), 6))), "fresh", None),
                 f"de.{E}": (D(str(round(s, 12))), "scored", None),
                 f"de.{E}.full": (full, "fresh", None),
                 f"de.{E}.parts": ({"rev_g_cum": D(str(a)), "profit_g_cum": D(str(round(s - a, 12)))}, "fresh", None),
                 f"de.{E}.version": (registry.BY_ID[E].version, "fresh", None)}
        rows.append(ds.Row(sym, session, datetime.combine(session, datetime.min.time(), timezone.utc), i, feats, {},
                           ()))
        sign = 1 if full else -1
        ret = 0.01 * (sign * 2.0 * s + 0.6 * rnd.gauss(0, 1))
        direction = "rise" if ret > 0.003 else "fall" if ret < -0.003 else "flat"
        labels[(sym, session, H)] = ds.Label("resolved", {"direction": direction, "ret": str(round(ret, 8)),
                                                          "volatility": "0.01", "volatility_state": "resolved"},
                                             None, f"{i:064x}")
    return ds.Dataset(None, "v", tuple(rows), labels, "point_in_time", "complete", "all", {})


def split(d, frac=0.7, name="fit"):
    k = int(len(d.rows) * frac)
    scope = FitScope(name, frozenset(r.key for r in d.rows[:k]), H)
    return data.train_set(d, scope, d.rows[:k], H), d.rows[k:]


def test_e1_fits_only_full_set_rows_and_finds_the_engine_signal():
    d = world()
    tr, test = split(d)
    art = ce.fit(tr, {"engine_id": E}, seed=1)
    b = art.load("calibrated_engine")
    assert b["return"]["slope"] > 0.015                                     # the sign of the full-set rows only
    # first part 2 audit M2: the direction map too is the fit on full-set rows only, exactly
    from sklearn.linear_model import LogisticRegression
    full_rows = [i for i, r in enumerate(tr.rows) if r.features[f"de.{E}.full"][0] and tr.y.direction[i] >= 0]
    s = np.array([float(tr.rows[i].features[f"de.{E}"][0]) for i in full_rows])
    ref = LogisticRegression(C=1e6, max_iter=2000, random_state=1).fit(np.column_stack([s, s * s]),
                                                                       tr.y.direction[full_rows])
    order = [list(ref.classes_).index(k) for k in range(3)]
    assert np.allclose(b["direction"]["coef"], ref.coef_[order], atol=1e-6)
    assert b["direction"]["coef"][0][0] > 1.0                               # rise rises with s on full-set rows
    assert b["rows_used"] == sum(1 for r in tr.rows if r.features[f"de.{E}.full"][0])
    full = [r for r in test if r.features[f"de.{E}.full"][0]]
    p = ce.predict(art, full)
    prior = baselines.predict(baselines.fit(split(d, name="training")[0]), full, H)["base_prior"]
    def ll(pr):
        return sum(-math.log(max(q[("rise", "flat", "fall").index(d.labels[(r.symbol, r.session, H)].values[
            "direction"])], 1e-15)) for q, r in zip(pr.probs, full)) / len(full)
    assert ll(p) < ll(prior) - 0.1


def test_e2_silent_where_the_engine_did_not_use_every_component_or_has_no_score():
    d = world(n=400)
    tr, test = split(d)
    art = ce.fit(tr, {"engine_id": E})
    partial = [r for r in test if not r.features[f"de.{E}.full"][0]][:3]
    r0 = test[1]
    noscore = ds.Row(r0.symbol, r0.session, r0.t, 0, {**r0.features, f"de.{E}": (None, "insufficient_inputs",
                                                                               "insufficient_inputs")}, {}, ())
    p = ce.predict(art, partial + [noscore])
    assert p.probs == [None] * 4 and p.ret == [None] * 4 and p.contributions == [None] * 4
    assert p.volatility == [None] * 4                                     # it produces no volatility


def test_e3_contributions_are_the_engines_components_and_add_up():
    d = world(n=600)
    tr, test = split(d)
    art = ce.fit(tr, {"engine_id": E})
    b = art.load("calibrated_engine")
    rows = [r for r in test if r.features[f"de.{E}.full"][0]][:10]
    p = ce.predict(art, rows)
    W, w0 = np.array(b["direction"]["coef"]), np.array(b["direction"]["intercept"])
    for i, r in enumerate(rows):
        s = float(r.features[f"de.{E}"][0])
        c = p.contributions[i]
        assert set(c["return"]) == {"rev_g_cum", "profit_g_cum", "intercept"}
        assert sum(c["return"].values()) == pytest.approx(p.ret[i], abs=1e-15)
        logits = w0 + W[:, 0] * s + W[:, 1] * s * s
        assert sum(c["direction"].values()) == pytest.approx(logits[int(np.argmax(logits))], abs=1e-9)


def test_e4_provenance_determinism_and_refusals():
    d = world(n=400)
    tr, test = split(d)
    a1, a2 = ce.fit(tr, {"engine_id": E}, seed=3), ce.fit(tr, {"engine_id": E}, seed=3)
    assert a1.sha256 == a2.sha256
    desc = ce.describe(a1)
    assert (desc["engine_id"], desc["engine_version"], desc["registry"]) == (E, registry.BY_ID[E].version,
                                                                             registry.REGISTRY_VERSION)
    assert desc["slice"] == "fit" and desc["environment"]["threads"] == 1
    rows = test[:20]
    assert [q9(x) for x in ce.predict(a1, rows).ret if x is not None] == \
        [q9(x) for x in ce.predict(a2, rows).ret if x is not None]
    with pytest.raises(ValueError, match="engine_id"):
        ce.fit(tr, {"engine_id": "no_such_engine"})
    with pytest.raises(ValueError, match="nothing to select"):
        ce.fit(tr, {"engine_id": E, "C": 0.01})
    rows = list(d.rows)
    r0 = rows[1]
    rows[1] = ds.Row(r0.symbol, r0.session, r0.t, r0.snapshot_id, {**r0.features, f"de.{E}.version": ("0", "fresh",
                                                                                                    None)}, {}, ())
    mixed = ds.Dataset(None, "v", tuple(rows), d.labels, "point_in_time", "complete", "all", {})
    with pytest.raises(ValueError, match="one engine version"):
        ce.fit(split(mixed)[0], {"engine_id": E})
    with pytest.raises(Refused, match="wrong_slice"):
        ce.fit(split(d, name="training")[0], {"engine_id": E})
    with pytest.raises(Refused, match="artifact_adapter"):
        ce.predict(baselines.fit(split(d, name="training")[0]), rows)
