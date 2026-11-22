"""
`calibrated_engine` (§7.2; P5-D3): a calibration of ONE Phase 4 engine, not an ML adapter. On the fit-slice rows where
the engine used its full component set: a multinomial logistic map on (s, s^2) for direction and a linear map on s
for return, s the engine's score. It has nothing to select, so its select slice is unused. Its provenance names the
engine and the version of the engine's outputs it was fitted on (from the data; refused if mixed or not the current
registry's); its contributions are the engine's own components: each component's share of the score, rescaled so the
shares add up to s (stored shares are quantised), times the map's slope at that score, so base plus contributions
equal the raw logit or return to floating-point precision. Its only hyperparameter is `engine_id`: the map has
nothing to select (first M5-4 part 2 audit, m3). Rows where the engine has no score or did not use its full set get no prediction (None).
It produces no volatility.
"""
from decimal import Decimal

import numpy as np

from worker.engines import registry

from .artifact import Artifact, Predictions, environment, one_thread
from .data import direction_of, require

ADAPTER = "calibrated_engine"
FIXED = {"C": 1e6, "max_iter": 2000}               # an unregularised map in effect: nothing is selected (§7.2)


def _score(row, engine_id):
    v = row.features.get(f"de.{engine_id}")
    full = row.features.get(f"de.{engine_id}.full")
    if v is None or v[0] is None or v[1] != "scored" or full is None or full[0] is not True:
        return None
    return float(Decimal(str(v[0])))


def fit(train, hyperparameters, seed=0):
    """hyperparameters must name `engine_id`."""
    with one_thread():
        return _fit(require(train, "fit"), hyperparameters, seed)


def _fit(train, hyperparameters, seed):
    from sklearn.linear_model import LinearRegression, LogisticRegression
    extra = set(hyperparameters or {}) - {"engine_id"}
    if extra:
        raise ValueError(f"calibrated_engine has nothing to select: {sorted(extra)} refused")
    hp = {**FIXED, "engine_id": (hyperparameters or {}).get("engine_id")}
    engine = registry.BY_ID.get(hp.get("engine_id"))
    if engine is None:
        raise ValueError("calibrated_engine calibrates one registered Phase 4 engine: name it as engine_id")
    s = np.array([np.nan if _score(r, engine.engine_id) is None else _score(r, engine.engine_id) for r in train.rows])
    use = ~np.isnan(s)
    versions = {r.features.get(f"de.{engine.engine_id}.version", (None,))[0] for r, u in zip(train.rows, use) if u}
    if versions != {engine.version}:
        raise ValueError(f"calibrated_engine fits one engine version, the registry's ({engine.version}); the rows "
                         f"carry {sorted(map(str, versions))}")
    d, y = train.y.direction, train.y.ret
    dm = use & (d >= 0)
    if len(set(d[dm].tolist())) < 3:
        raise ValueError("calibrated_engine needs all three direction classes among full-set rows of the fit slice")
    clf = LogisticRegression(C=hp["C"], max_iter=hp["max_iter"], random_state=seed)
    clf.fit(np.column_stack([s[dm], s[dm] ** 2]), d[dm])
    order = [list(clf.classes_).index(k) for k in range(3)]
    rm = use & ~np.isnan(y)
    if rm.sum() < 2:
        raise ValueError("calibrated_engine needs full-set fit-slice rows with a return")
    reg = LinearRegression().fit(s[rm].reshape(-1, 1), y[rm])
    body = {"engine_id": engine.engine_id, "engine_version": engine.version, "registry": registry.REGISTRY_VERSION,
            "slice": train.slice_name, "horizon": train.horizon, "rows_used": int(use.sum()),
            "direction": {"coef": clf.coef_[order].tolist(), "intercept": clf.intercept_[order].tolist()},
            "return": {"slope": float(reg.coef_[0]), "intercept": float(reg.intercept_)},
            "hyperparameters": hp, "environment": environment(seed)}
    return Artifact.make(ADAPTER, body)


def describe(artifact):
    b = artifact.load(ADAPTER)
    return {k: b[k] for k in ("engine_id", "engine_version", "registry", "slice", "horizon", "rows_used",
                              "hyperparameters", "environment")}


def predict(artifact, rows, horizon=None):
    b = artifact.load(ADAPTER)
    if horizon is not None and b["horizon"] != horizon:
        raise ValueError(f"the artifact was fitted for h={b['horizon']}, not h={horizon}")
    eid = b["engine_id"]
    W, w0 = np.array(b["direction"]["coef"]), np.array(b["direction"]["intercept"])
    slope, icept = b["return"]["slope"], b["return"]["intercept"]
    out = dict(probs=[], direction=[], ret=[], contributions=[])
    for r in rows:
        s = _score(r, eid)
        if s is None:
            for k in out:
                out[k].append(None)
            continue
        logits = w0 + W[:, 0] * s + W[:, 1] * s * s
        p = np.exp(logits - logits.max())
        p /= p.sum()
        k = int(np.argmax(p))
        parts = {f: float(Decimal(str(x))) for f, x in r.features[f"de.{eid}.parts"][0].items()}
        total = sum(parts.values())
        if total != 0:                       # stored shares are quantised: rescale so they add up to s
            parts = {f: x * s / total for f, x in parts.items()}
        # decompositions: b s + c s^2 = (b + c s) * sum(parts); slope * s = slope * sum(parts)
        dslope = W[k, 0] + W[k, 1] * s
        out["probs"].append(tuple(p.tolist()))
        out["direction"].append(direction_of(tuple(p.tolist())))
        out["ret"].append(icept + slope * s)
        out["contributions"].append({"direction": {**{f: dslope * x for f, x in parts.items()},
                                                   "intercept": float(w0[k])},
                                     "return": {**{f: slope * x for f, x in parts.items()}, "intercept": icept}})
    n = len(rows)
    return Predictions([r.key for r in rows], out["probs"], out["direction"], out["ret"], [None] * n, [None] * n,
                       out["contributions"])
