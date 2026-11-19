"""
§10: combinations. A combination is an ordered list of component configurations (each past gates 1 to 8) and a rule.

combine_mean_v1:
  direction    the mean of the components' calibrated probabilities, then the combination's own vector scaling on the
               logarithm of that mean (a direction-only baseline never enters a probability mean: a component without
               probabilities is refused for this output)
  return,      the mean of the point estimates in level units
  volatility
  interval     the combination's own split-conformal quantile of |y - mean| (scale-free here; per class, like §8.2)

Its fitted part is trained on OUT-OF-FOLD component predictions only (MLK-10): fold k's combiner reads the
components' validation predictions of folds before k, each made by that fold's own model, and only those whose label
resolved at or before fold k's cutoff (purged against its own cutoff). Fold 1 has none. An in-sample prediction (made
by a model whose training rows include the row), a prediction of fold k or later, or one resolving after the cutoff is
refused, never silently dropped: the caller must not hand them over.
A combination needs all of its components: there is no "combine whatever is present".
"""
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import COMBINE_MEAN

CLASSES = ("rise", "flat", "fall")


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ComponentPrediction:
    """One component's prediction for one row. fold_no: the fold whose model made it (None: a final fit);
    in_sample: the row was among that model's training rows; resolved_at: when the row's label resolved."""
    configuration_id: str
    key: tuple                                   # (symbol, session)
    fold_no: Optional[int]
    probs: Optional[tuple] = None                # calibrated (p_rise, p_flat, p_fall)
    ret: Optional[float] = None
    vol: Optional[float] = None
    in_sample: bool = False
    resolved_at: Optional[datetime] = None


def check_out_of_fold(preds, fold_no, cutoff):
    """MLK-10: the predictions a fold-`fold_no` combiner may be fitted on."""
    for p in preds:
        if p.in_sample:
            raise Refused("in_sample", f"{p.configuration_id} {p.key}: an in-sample prediction")
        if p.fold_no is None or p.fold_no >= fold_no:
            raise Refused("not_out_of_fold", f"{p.configuration_id} {p.key}: fold {p.fold_no} is not before {fold_no}")
        if p.resolved_at is None or p.resolved_at > cutoff:
            raise Refused("resolves_after_cutoff", f"{p.configuration_id} {p.key}: its label resolved after "
                                                   f"the combiner's cutoff")
    return True


def _rows(preds, components):
    by = {}
    for p in preds:
        by.setdefault(p.key, {})[p.configuration_id] = p
    out = {}
    for key, got in sorted(by.items()):
        if set(got) != set(components):
            continue                             # a combination needs all of its components
        out[key] = [got[c] for c in components]
    return out


def mean_outputs(parts, output):
    """The combination's raw output for one row from its components' predictions, in component order."""
    if output == "direction":
        if any(p.probs is None for p in parts):
            raise Refused("no_probabilities", "a direction-only component never enters a probability mean")
        m = [math.fsum(p.probs[i] for p in parts) / len(parts) for i in range(3)]
        return tuple(m)
    attr = "ret" if output == "return" else "vol"
    vals = [getattr(p, attr) for p in parts]
    if any(v is None for v in vals):
        raise Refused("missing_component_output", f"a component has no {output}")
    return math.fsum(vals) / len(vals)


def _scale(mean_probs, scale, offset):
    z = [math.log(max(x, 1e-15)) * a + b for x, a, b in zip(mean_probs, scale, offset)]
    top = max(z)
    e = [math.exp(v - top) for v in z]
    s = math.fsum(e)
    return tuple(v / s for v in e)


def fit(components, output, preds, labels, fold_no, cutoff, level=0.9):
    """Fold `fold_no`'s combiner. preds: [ComponentPrediction] (checked out of fold); labels: {key: class index (for
    direction) or realised value}. Returns the fitted part as a plain dict (the caller stores it as an artifact)."""
    if fold_no < 2:
        raise Refused("fold_1", "fold 1 has no earlier folds: no combiner")
    check_out_of_fold(preds, fold_no, cutoff)
    rows = {k: v for k, v in _rows(preds, components).items() if k in labels}
    if not rows:
        raise Refused("no_rows", "no out-of-fold row with every component and a label")
    body = {"rule": COMBINE_MEAN, "components": list(components), "output": output, "fold_no": fold_no,
            "rows": len(rows)}
    if output == "direction":
        from scipy.optimize import minimize
        means = [mean_outputs(v, output) for v in rows.values()]
        ys = [labels[k] for k in rows]
        if len(set(ys)) < 3:
            raise Refused("classes", "vector scaling needs all three classes")

        def nll(x):
            a, b = x[:3], (x[3], x[4], -x[3] - x[4])
            return -math.fsum(math.log(max(_scale(m, a, b)[y], 1e-300)) for m, y in zip(means, ys)) / len(ys)
        res = minimize(nll, [1.0, 1.0, 1.0, 0.0, 0.0], method="L-BFGS-B")
        a, b = list(res.x[:3]), [res.x[3], res.x[4], -res.x[3] - res.x[4]]
        body.update(scale=[float(v) for v in a], offset=[float(v) for v in b], converged=bool(res.success))
    else:
        resid = sorted(abs(labels[k] - mean_outputs(v, output)) for k, v in rows.items())
        n = len(resid)
        k = math.ceil((n + 1) * level)
        body.update(level=level, quantile=None if k > n else float(resid[k - 1]))
    return body


def apply(body, parts):
    """The combination's prediction for one row from all its components' predictions (in the body's order)."""
    if [p.configuration_id for p in parts] != body["components"]:
        raise Refused("components", "a combination needs all of its components, in its order")
    m = mean_outputs(parts, body["output"])
    if body["output"] == "direction":
        return {"probs": _scale(m, body["scale"], body["offset"]), "raw_mean": m}
    q = body.get("quantile")
    return {"value": m, "interval": None if q is None else (m - q, m + q), "level": body.get("level")}
