"""
`linear` (§7.2): regularised multinomial logistic regression for direction; ridge regression for return and for
volatility (floored at a configured constant, never negative). Imputation (the fit slice's median), a missing
indicator for every numeric column with a null in the fit slice, and standardisation are fitted on the fit slice and
stored in the artifact. Prediction is computed from the artifact text alone (numpy), never from a library object.
Contributions: coefficient x standardised value, for the predicted class (direction) and for each regression.
"""
import math

import numpy as np

from . import CLASSES
from .artifact import Artifact, Predictions, environment
from .data import Schema

ADAPTER = "linear"
DEFAULTS = {"C": 1.0, "alpha": 1.0, "max_iter": 2000, "volatility_floor": 0.0}


def _design(X, body):
    """Imputed, indicator-extended, standardised features; column names."""
    med = np.array(body["median"])
    miss = np.isnan(X)
    Xi = np.where(miss, med, X)
    ind = body["indicators"]
    if ind:
        Xi = np.hstack([Xi, miss[:, ind].astype(float)])
    return (Xi - np.array(body["mean"])) / np.array(body["scale"])


def fit(train, hyperparameters=None, seed=0):
    from sklearn.linear_model import LogisticRegression, Ridge
    hp = {**DEFAULTS, **(hyperparameters or {})}
    X = train.X
    if X.shape[0] == 0:
        raise ValueError("linear needs fit-slice rows")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # an all-null column: its median is 0
        med = np.nanmedian(X, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    ind = [int(j) for j in np.where(np.isnan(X).any(axis=0))[0]]
    body = {"median": med.tolist(), "indicators": ind}
    Xi = np.where(np.isnan(X), med, X)
    if ind:
        Xi = np.hstack([Xi, np.isnan(X)[:, ind].astype(float)])
    mean, scale = Xi.mean(axis=0), Xi.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    body.update(mean=mean.tolist(), scale=scale.tolist())
    Z = (Xi - mean) / scale
    d = train.y.direction
    ok = d >= 0
    if len(set(d[ok].tolist())) < 3:
        raise ValueError("linear needs all three direction classes in the fit slice")
    clf = LogisticRegression(C=hp["C"], max_iter=hp["max_iter"], random_state=seed)
    clf.fit(Z[ok], d[ok])
    order = [list(clf.classes_).index(k) for k in range(3)]
    body["direction"] = {"coef": clf.coef_[order].tolist(), "intercept": clf.intercept_[order].tolist()}
    for out, y in (("return", train.y.ret), ("volatility", train.y.volatility)):
        m = ~np.isnan(y)
        if m.sum() < 2:
            raise ValueError(f"linear needs fit-slice rows with a {out} label")
        reg = Ridge(alpha=hp["alpha"], random_state=seed).fit(Z[m], y[m])
        body[out] = {"coef": reg.coef_.tolist(), "intercept": float(reg.intercept_)}
    names = list(train.schema.columns()) + [f"missing:{train.schema.columns()[j]}" for j in ind]
    body.update(horizon=train.horizon, schema=train.schema.as_json(), columns=names, hyperparameters=hp,
                environment=environment(seed))
    return Artifact.make(ADAPTER, body)


def describe(artifact):
    b = artifact.load(ADAPTER)
    return {k: b[k] for k in ("horizon", "schema", "columns", "hyperparameters", "environment")}


def schema_of(artifact):
    return Schema.from_json(artifact.load(ADAPTER)["schema"])


def predict(artifact, X, keys):
    b = artifact.load(ADAPTER)
    Z = _design(X, b)
    W, w0 = np.array(b["direction"]["coef"]), np.array(b["direction"]["intercept"])
    logits = Z @ W.T + w0
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    ret = Z @ np.array(b["return"]["coef"]) + b["return"]["intercept"]
    vol = np.maximum(Z @ np.array(b["volatility"]["coef"]) + b["volatility"]["intercept"],
                     b["hyperparameters"]["volatility_floor"])
    cols = b["columns"]
    contrib = []
    for i in range(len(keys)):
        k = int(np.argmax(p[i]))
        contrib.append({"direction": dict(zip(cols, (W[k] * Z[i]).tolist())),
                        "return": dict(zip(cols, (np.array(b["return"]["coef"]) * Z[i]).tolist())),
                        "volatility": dict(zip(cols, (np.array(b["volatility"]["coef"]) * Z[i]).tolist()))})
    return Predictions(list(keys), [tuple(r) for r in p.tolist()], [CLASSES[int(np.argmax(r))] for r in p],
                       ret.tolist(), vol.tolist(), [None] * len(keys), contrib)
