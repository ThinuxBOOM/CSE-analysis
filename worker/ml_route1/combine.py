"""
§10: combinations. A combination is an ordered list of component configurations, each past gates 1 to 8, and a rule.
A component is a gated research model or a calibrated engine (Phase 4, through its calibration); a Route 2 output is
never a component, and an ungated configuration is refused (M5-7 audit M4). Whether any component is an engine is
derived here (`no_engine_component`), never stated by the caller.

combine_mean_v1:
  direction    the mean of the components' calibrated probabilities, then the combination's own vector scaling on the
               logarithm of that mean, its offsets summing to zero (a direction-only baseline never enters a
               probability mean: a component without probabilities is refused for this output)
  return,      the mean of the point estimates in level units
  volatility
  interval     §8.2's interval, the combination's own (M5-7 audit M1): the scale max(vol_21 x sqrt(h), floor), the
               floor the 5th percentile of the positive scales of the rows the combiner is fitted on, and the
               split-conformal quantile of |y - mean| / scale taken separately within each liquidity class

Its fitted part is trained on OUT-OF-FOLD component predictions only (MLK-10): fold k's combiner reads the
components' validation predictions of folds before k, each made by that fold's own model, and only those whose label
resolved at or before fold k's cutoff (purged against its own cutoff). Fold 1 has none. An in-sample prediction (made
by a model whose training rows include the row), a prediction of fold k or later, or one resolving after the cutoff is
refused, never silently dropped: the caller must not hand them over. The FINAL combiner (`fit_final`) reads every
fold's validation predictions resolved by the last V_end and is applied to the final fits' predictions.

The fitted part is an `Artifact` (hashed, MLK-16) bound to its horizon and to its fold (M5-7 audit M2): fold k's
combiner is applied only to fold k's component predictions, the final combiner only to the final fits'.
A combination needs all of its components: there is no "combine whatever is present".
"""
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from worker.ml_models.artifact import Artifact

from . import COMBINE_MEAN

# §8.2's constants, as worker.ml_models.calibration states them (kept here so this module needs no numpy; a test
# asserts they are equal)
FLOOR_QUANTILE = 0.05
LIQUIDITY = ("low", "mid", "high", "unknown")

CLASSES = ("rise", "flat", "fall")
KINDS = ("model", "calibrated_engine")          # what may be a component; "route2" and anything else are refused
FINAL = "final"


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Component:
    """A component configuration: its kind and whether it passed gates 1 to 8 (from the registry)."""
    configuration_id: str
    kind: str
    gated: bool


def check_components(components):
    """§10: every component is a gated model or a calibrated engine; none twice. Returns `no_engine_component`."""
    if not components:
        raise Refused("components", "a combination has at least one component")
    ids = [c.configuration_id for c in components]
    if len(set(ids)) != len(ids):
        raise Refused("components", "a component appears twice")
    for c in components:
        if c.kind not in KINDS:
            raise Refused("component_kind", f"{c.configuration_id}: a {c.kind} is never a component (§10)")
        if not c.gated:
            raise Refused("component_not_gated", f"{c.configuration_id} has not passed gates 1 to 8")
    return not any(c.kind == "calibrated_engine" for c in components)


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


@dataclass(frozen=True)
class RowMeta:
    """What §8.2's scale needs for one row: vol_21 (None: the floor, flagged) and the liquidity class."""
    vol_21: Optional[float]
    liquidity_class: str = "unknown"


def _check_probs(p):
    if p is None:
        return
    if len(p) != 3 or any(not math.isfinite(x) or x < 0 for x in p) or abs(math.fsum(p) - 1) > 1e-9:
        raise Refused("invalid_probabilities", f"{p} is not a probability vector over three classes")


def check_out_of_fold(preds, fold_no, cutoff):
    """MLK-10: the predictions a fold-`fold_no` combiner may be fitted on (fold_no n + 1: the final combiner)."""
    for p in preds:
        if p.in_sample:
            raise Refused("in_sample", f"{p.configuration_id} {p.key}: an in-sample prediction")
        if p.fold_no is None or p.fold_no >= fold_no:
            raise Refused("not_out_of_fold", f"{p.configuration_id} {p.key}: fold {p.fold_no} is not before {fold_no}")
        if p.resolved_at is None or p.resolved_at > cutoff:
            raise Refused("resolves_after_cutoff", f"{p.configuration_id} {p.key}: its label resolved after "
                                                   f"the combiner's cutoff")
    return True


def _rows(preds, ids):
    by = {}
    for p in preds:
        got = by.setdefault(p.key, {})
        if p.configuration_id in got:
            raise Refused("duplicate", f"{p.configuration_id} {p.key}: two predictions for one row")
        got[p.configuration_id] = p
    out = {}
    for key, got in sorted(by.items()):
        if set(got) != set(ids):
            continue                             # a combination needs all of its components
        out[key] = [got[c] for c in ids]
    return out


def mean_outputs(parts, output):
    """The combination's raw output for one row from its components' predictions, in component order."""
    if output == "direction":
        if any(p.probs is None for p in parts):
            raise Refused("no_probabilities", "a direction-only component never enters a probability mean")
        for p in parts:
            _check_probs(p.probs)
        return tuple(math.fsum(p.probs[i] for p in parts) / len(parts) for i in range(3))
    attr = "ret" if output == "return" else "vol"
    vals = [getattr(p, attr) for p in parts]
    if any(v is None or not math.isfinite(v) for v in vals):
        raise Refused("missing_component_output", f"a component has no finite {output}")
    return math.fsum(vals) / len(vals)


def _scale_probs(mean_probs, scale, offset):
    z = [math.log(max(x, 1e-15)) * a + b for x, a, b in zip(mean_probs, scale, offset)]
    top = max(z)
    e = [math.exp(v - top) for v in z]
    s = math.fsum(e)
    return tuple(v / s for v in e)


def _row_scale(meta, horizon, floor):
    if meta.vol_21 is None or not math.isfinite(meta.vol_21):
        return floor, True
    return max(meta.vol_21 * math.sqrt(horizon), floor), False


def _quantile_floor(scales):
    v = sorted(s for s in scales if s > 0)
    if not v:
        raise Refused("no_scale", "no row with a positive vol_21 to set the scale floor")
    pos = (len(v) - 1) * FLOOR_QUANTILE
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _fit(components, output, preds, labels, fold_no, cutoff, horizon, meta, level, label):
    no_engine = check_components(components)
    ids = [c.configuration_id for c in components]
    check_out_of_fold(preds, fold_no, cutoff)
    rows = {k: v for k, v in _rows(preds, ids).items() if k in labels}
    if not rows:
        raise Refused("no_rows", "no out-of-fold row with every component and a label")
    body = {"rule": COMBINE_MEAN, "components": ids, "kinds": [c.kind for c in components],
            "no_engine_component": no_engine, "output": output, "horizon": horizon, "fold": label,
            "cutoff": cutoff.isoformat(), "rows": len(rows)}
    if output == "direction":
        from scipy.optimize import minimize
        means = [mean_outputs(v, output) for v in rows.values()]
        ys = [labels[k] for k in rows]
        if set(ys) != {0, 1, 2}:
            raise Refused("classes", "vector scaling needs all three classes")

        def nll(x):
            a, b = x[:3], (x[3], x[4], -x[3] - x[4])
            return -math.fsum(math.log(max(_scale_probs(m, a, b)[y], 1e-300)) for m, y in zip(means, ys)) / len(ys)
        res = minimize(nll, [1.0, 1.0, 1.0, 0.0, 0.0], method="L-BFGS-B")
        if not res.success:
            raise Refused("not_converged", f"the combination's vector scaling did not converge: {res.message}")
        body.update(scale=[float(v) for v in res.x[:3]],
                    offset=[float(res.x[3]), float(res.x[4]), float(-res.x[3] - res.x[4])])
    else:
        if meta is None or any(k not in meta for k in rows):
            raise Refused("row_meta", "§8.2's interval needs every row's vol_21 and liquidity class")
        floor = _quantile_floor([meta[k].vol_21 * math.sqrt(horizon) for k in rows
                                 if meta[k].vol_21 is not None and math.isfinite(meta[k].vol_21)])
        by = {}
        for k, v in rows.items():
            s, _null = _row_scale(meta[k], horizon, floor)
            by.setdefault(meta[k].liquidity_class, []).append(abs(labels[k] - mean_outputs(v, output)) / s)
        q = {}
        for cls in LIQUIDITY:
            r = sorted(by.get(cls, []))
            n = len(r)
            j = math.ceil((n + 1) * level)
            q[cls] = None if n == 0 or j > n else float(r[j - 1])
        body.update(level=level, floor=floor, quantile=q, counts={c: len(by.get(c, [])) for c in LIQUIDITY})
    return Artifact.make(COMBINE_MEAN, body)


def fit(components, output, preds, labels, fold_no, cutoff, *, horizon, meta=None, level=0.9):
    """Fold `fold_no`'s combiner. components: [Component]; preds: [ComponentPrediction] (checked out of fold);
    labels: {key: class index (for direction) or realised value}; meta: {key: RowMeta} for return and volatility.
    Returns the fitted part as an Artifact bound to the horizon and the fold."""
    if fold_no < 2:
        raise Refused("fold_1", "fold 1 has no earlier folds: no combiner")
    return _fit(components, output, preds, labels, fold_no, cutoff, horizon, meta, level, fold_no)


def fit_final(components, output, preds, labels, n_folds, last_v_end, *, horizon, meta=None, level=0.9):
    """§10: the final combiner, on every fold's out-of-fold predictions resolved by the last V_end; applied to the
    final fits' predictions."""
    return _fit(components, output, preds, labels, n_folds + 1, last_v_end, horizon, meta, level, FINAL)


def apply(artifact, parts, *, horizon, fold, meta=None):
    """The combination's prediction for one row from all its components' predictions (in the artifact's order).
    fold: the fold whose component predictions these are (FINAL for the final fits); it must be the combiner's."""
    body = artifact.load(COMBINE_MEAN)
    if body["horizon"] != horizon:
        raise Refused("horizon", f"a combiner for h = {body['horizon']} applied at h = {horizon}")
    if body["fold"] != fold or any(p.fold_no != (None if fold == FINAL else fold) for p in parts):
        raise Refused("fold", f"the fold-{body['fold']} combiner applied to predictions of another fold")
    if [p.configuration_id for p in parts] != body["components"]:
        raise Refused("components", "a combination needs all of its components, in its order")
    m = mean_outputs(parts, body["output"])
    if body["output"] == "direction":
        return {"probs": _scale_probs(m, body["scale"], body["offset"]), "raw_mean": m, "flags": []}
    if meta is None:
        raise Refused("row_meta", "§8.2's interval needs the row's vol_21 and liquidity class")
    s, null = _row_scale(meta, horizon, body["floor"])
    q = body["quantile"].get(meta.liquidity_class)
    flags = (["vol_21_null"] if null else []) + (["insufficient_rows"] if q is None else [])
    return {"value": m, "interval": None if q is None else (m - q * s, m + q * s), "level": body["level"],
            "flags": flags}
