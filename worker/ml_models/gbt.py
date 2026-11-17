"""
`gbt` (§7.2): gradient-boosted trees (LightGBM): multiclass for direction, regression for return and for volatility
(floored at a configured constant). Nulls are handled natively. Deterministic: one thread, `deterministic`,
row-wise histograms, no bagging or feature sampling, a fixed seed. The artifact holds LightGBM's own text model of
each booster (never a pickle). Contributions are the library's exact tree contributions (SHAP values), the bias term
named `bias`.
"""
import numpy as np

from . import CLASSES
from .artifact import Artifact, Predictions, environment
from .data import Schema

ADAPTER = "gbt"
DEFAULTS = {"num_leaves": 15, "learning_rate": 0.05, "num_boost_round": 200, "min_data_in_leaf": 20,
            "lambda_l2": 1.0, "volatility_floor": 0.0}


def _params(hp, seed, objective, extra=None):
    p = {"objective": objective, "num_leaves": hp["num_leaves"], "learning_rate": hp["learning_rate"],
         "min_data_in_leaf": hp["min_data_in_leaf"], "lambda_l2": hp["lambda_l2"], "num_threads": 1,
         "deterministic": True, "force_row_wise": True, "seed": seed, "bagging_fraction": 1.0,
         "feature_fraction": 1.0, "verbose": -1}
    p.update(extra or {})
    return p


def fit(train, hyperparameters=None, seed=0):
    import lightgbm as lgb
    hp = {**DEFAULTS, **(hyperparameters or {})}
    X, y = train.X, train.y
    names = [f"c{j}" for j in range(X.shape[1])]
    body = {}
    ok = y.direction >= 0
    if len(set(y.direction[ok].tolist())) < 3:
        raise ValueError("gbt needs all three direction classes in the fit slice")
    for out, mask, target, obj, extra in (
            ("direction", ok, y.direction, "multiclass", {"num_class": 3}),
            ("return", ~np.isnan(y.ret), y.ret, "regression", None),
            ("volatility", ~np.isnan(y.volatility), y.volatility, "regression", None)):
        if mask.sum() < 2:
            raise ValueError(f"gbt needs fit-slice rows with a {out} label")
        ds = lgb.Dataset(X[mask], label=target[mask], feature_name=names, free_raw_data=True,
                         params={"verbose": -1, "num_threads": 1})
        booster = lgb.train(_params(hp, seed, obj, extra), ds, num_boost_round=hp["num_boost_round"])
        body[out] = booster.model_to_string()
    body.update(horizon=train.horizon, schema=train.schema.as_json(), columns=list(train.schema.columns()),
                hyperparameters=hp, environment=environment(seed))
    return Artifact.make(ADAPTER, body)


def describe(artifact):
    b = artifact.load(ADAPTER)
    return {k: b[k] for k in ("horizon", "schema", "columns", "hyperparameters", "environment")}


def schema_of(artifact):
    return Schema.from_json(artifact.load(ADAPTER)["schema"])


def predict(artifact, X, keys):
    import lightgbm as lgb
    b = artifact.load(ADAPTER)
    cols = b["columns"] + ["bias"]
    boost = {k: lgb.Booster(model_str=b[k], params={"num_threads": 1}) for k in ("direction", "return", "volatility")}
    p = boost["direction"].predict(X, num_threads=1)
    ret = boost["return"].predict(X, num_threads=1)
    vol = np.maximum(boost["volatility"].predict(X, num_threads=1), b["hyperparameters"]["volatility_floor"])
    cd = boost["direction"].predict(X, pred_contrib=True, num_threads=1)        # 3 blocks of (features + bias)
    cr = boost["return"].predict(X, pred_contrib=True, num_threads=1)
    cv = boost["volatility"].predict(X, pred_contrib=True, num_threads=1)
    width = len(cols)
    contrib = []
    for i in range(len(keys)):
        k = int(np.argmax(p[i]))
        contrib.append({"direction": dict(zip(cols, cd[i, k * width:(k + 1) * width].tolist())),
                        "return": dict(zip(cols, cr[i].tolist())), "volatility": dict(zip(cols, cv[i].tolist()))})
    return Predictions(list(keys), [tuple(r) for r in p.tolist()], [CLASSES[int(np.argmax(r))] for r in p],
                       ret.tolist(), vol.tolist(), [None] * len(keys), contrib)
