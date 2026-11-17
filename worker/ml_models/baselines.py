"""
The `ref` adapter: the baselines of §7.3. Each fills only what it has a method for; elsewhere it is silent (None),
and a comparison uses only the rows where both sides produced a value. Inputs are the dataset's MF features, which
are already null outside the admissible states (an `unlabelled` value is absent, so an unentered bonus or rights
issue cannot feed a baseline a false fall). `base_prior` and `base_interval` are fitted on all of the fold's training
rows and stored as a canonical artifact; the others have no parameters.
"""
import math
from decimal import Decimal

import numpy as np

from . import CLASSES
from .artifact import Artifact, Predictions

ADAPTER = "ref"
NAMES = ("base_no_change", "base_rate_252", "base_momentum_sign", "base_prior", "base_vol_trailing",
         "base_interval")
SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)


def _f(row, name):
    v = row.features.get(f"mf.{name}")
    if v is None or v[0] is None:
        return None
    return float(Decimal(str(v[0])))


def fit(train, hyperparameters=None, seed=0):
    """base_prior's class frequencies and mean return, and base_interval's conformal quantile of |y - mean|, on the
    fold's training rows (train.rows are all of them, admitted by the caller's FitScope)."""
    level = float((hyperparameters or {}).get("interval_level", 0.9))
    d = train.y.direction[train.y.direction >= 0]
    r = train.y.ret[~np.isnan(train.y.ret)]
    if len(d) == 0 or len(r) == 0:
        raise ValueError("base_prior needs training rows with a direction and a return")
    freqs = [float(np.sum(d == k)) / len(d) for k in range(3)]
    mean = float(np.mean(r))
    resid = np.sort(np.abs(r - mean))
    n = len(resid)
    k = min(n, math.ceil((n + 1) * level))                   # the split-conformal order statistic
    body = {"horizon": train.horizon, "freqs": freqs, "mean": mean, "q": float(resid[k - 1]), "level": level,
            "n": n}
    return Artifact.make(ADAPTER, body)


def describe(artifact):
    return artifact.load(ADAPTER)


def predict(artifact, rows, horizon):
    """{baseline name: Predictions} for the rows."""
    b = artifact.load(ADAPTER)
    if b["horizon"] != horizon:
        raise ValueError("the artifact was fitted for another horizon")
    keys = [r.key for r in rows]
    n = len(rows)
    out = {}

    def preds(**cols):
        base = dict(probs=[None] * n, direction=[None] * n, ret=[None] * n, volatility=[None] * n,
                    interval=[None] * n, contributions=[None] * n)
        base.update(cols)
        return Predictions(keys, **base)

    out["base_no_change"] = preds(direction=["flat"] * n, ret=[0.0] * n)
    probs, rets = [], []
    for r in rows:
        up, flat, down = (_f(r, f"hist_{x}_share_{horizon}") for x in ("up", "flat", "down"))
        m, cnt = _f(r, f"hist_mean_ret_{horizon}"), _f(r, f"hist_n_{horizon}")
        if None in (up, flat, down) or cnt is None:
            probs.append(None)
        else:                                                # (share x n + 1) / (n + 3): no class at probability 0
            p = [(s * cnt + 1) / (cnt + 3) for s in (up, flat, down)]
            t = sum(p)
            probs.append(tuple(x / t for x in p))
        rets.append(m)
    out["base_rate_252"] = preds(probs=probs, direction=[None if p is None else CLASSES[int(np.argmax(p))]
                                                         for p in probs], ret=rets)
    mom = []
    for r in rows:
        v = _f(r, "mom_252_21")
        mom.append(None if v is None or v == 0 else ("rise" if v > 0 else "fall"))
    out["base_momentum_sign"] = preds(direction=mom)
    pr = tuple(b["freqs"])
    out["base_prior"] = preds(probs=[pr] * n, direction=[CLASSES[int(np.argmax(pr))]] * n, ret=[b["mean"]] * n)
    vols = []
    for r in rows:
        v = _f(r, "vol_21")
        vols.append(None if v is None else (v * SQRT_2_OVER_PI if horizon == 1 else v))
    out["base_vol_trailing"] = preds(volatility=vols)
    out["base_interval"] = preds(ret=[b["mean"]] * n, interval=[(b["mean"] - b["q"], b["mean"] + b["q"])] * n)
    return out
