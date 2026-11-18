"""
M5-5 (docs/PHASE5_ML_DESIGN.md §8): calibration, predictive intervals, and their reports.

§8.1  `linear` and `gbt`: vector scaling fitted on the CALIBRATE slice: p' = softmax(a_k z_k + b_k), z the model's
      log-probabilities, one scale a_k and one offset b_k per class, the offsets constrained to sum to zero.
      `calibrated_engine`: its map is the calibration; the calibrate slice is where its reliability is measured
      (`reliability_ok`). Reliability tables overall and per liquidity class (`reliability_report`).
§8.2  Split-conformal intervals on the CONFORMAL slice, scale-normalised and class-conditional: a row's scale is
      max(vol_21 x sqrt(h), floor), the floor the 5th percentile of the positive scales of the FIT slice; a row
      without vol_21 takes the floor and is flagged; the quantile of |y - prediction| / scale is taken separately
      within each liquidity class. Coverage is reported as §8.2 claims it: the share of rows inside, averaged over
      the period's dates, per liquidity class (`coverage_report`).
§8.3  Contributions stay those of the raw score (the adapters'); the calibrated value is stored beside it.
Every fitted part is admitted through a FitScope of its own slice (MLK-3) and stored as a canonical artifact.
"""
import math
from decimal import Decimal

import numpy as np

from worker.evaluation_metrics import metrics

from . import CLASSES
from .artifact import Artifact, Refused, one_thread
from .data import direction_of, targets

SCALING, CONFORMAL = "vector_scaling", "conformal"
FLOOR_QUANTILE = 0.05
LIQUIDITY = ("low", "mid", "high", "unknown")


def _scoped(scope, rows, slice_name):
    if scope.slice_name != slice_name:
        raise Refused("wrong_slice", f"this is fitted on the {slice_name} slice, not {scope.slice_name!r}")
    return scope.admit(rows)


# ------------------------------------------------------------------------------------------------ §8.1


def _calibrated(z, a, b):
    t = z * a + b
    t -= t.max(axis=1, keepdims=True)
    e = np.exp(t)
    return e / e.sum(axis=1, keepdims=True)


def fit_vector_scaling(scope, rows, probs, dataset, horizon):
    """rows: the calibrate slice's rows (admitted through `scope`, which must be that slice); probs: the model's
    (p_rise, p_flat, p_fall) on them. Rows without a trained direction label are not used."""
    from scipy.optimize import minimize
    rows = _scoped(scope, rows, "calibrate")
    y = targets(dataset, [r.key for r in rows], horizon).direction
    keep = [i for i, p in enumerate(probs) if p is not None and y[i] >= 0]
    if len({int(y[i]) for i in keep}) < 3:
        raise ValueError("vector scaling needs all three classes in the calibrate slice")
    z = np.log(np.clip(np.array([probs[i] for i in keep], dtype=float), 1e-15, 1.0))
    yy = np.array([int(y[i]) for i in keep])

    def unpack(x):
        return x[:3], np.array([x[3], x[4], -x[3] - x[4]])        # offsets sum to zero: the map is identified

    def nll(x):
        a, b = unpack(x)
        p = _calibrated(z, a, b)
        return -float(np.mean(np.log(np.clip(p[np.arange(len(yy)), yy], 1e-300, 1.0))))

    with one_thread():
        res = minimize(nll, np.array([1.0, 1.0, 1.0, 0.0, 0.0]), method="L-BFGS-B")
    a, b = unpack(res.x)
    return Artifact.make(SCALING, {"slice": "calibrate", "horizon": horizon, "scale": a.tolist(),
                                   "offset": b.tolist(), "rows_used": len(keep), "converged": bool(res.success),
                                   "nll": float(res.fun)})


def apply_scaling(artifact, probs):
    """The calibrated probabilities beside the raw ones (§8.3); None stays None."""
    b = artifact.load(SCALING)
    a, off = np.array(b["scale"]), np.array(b["offset"])
    out = []
    for p in probs:
        if p is None:
            out.append(None)
            continue
        z = np.log(np.clip(np.array([p], dtype=float), 1e-15, 1.0))
        out.append(tuple(_calibrated(z, a, off)[0].tolist()))
    return out


def _as_metric(p):
    return dict(zip(CLASSES, (float(x) for x in p)))


def reliability_report(dataset, rows, probs, horizon, bins=10):
    """{"overall": (ECE, table), "<liquidity class>": (ECE, table)} over rows with a probability and a trained
    direction label."""
    y = targets(dataset, [r.key for r in rows], horizon).direction
    groups = {}
    for r, p, k in zip(rows, probs, y):
        if p is None or k < 0:
            continue
        for g in ("overall", r.meta.get("liquidity_class", "unknown")):
            groups.setdefault(g, ([], []))
            groups[g][0].append(CLASSES[int(k)])
            groups[g][1].append(_as_metric(p))
    return {g: (metrics.expected_calibration_error(a, p, bins), metrics.reliability(a, p, bins))
            for g, (a, p) in sorted(groups.items())}


def reliability_ok(report, tolerance):
    """§8.1 for calibrated_engine: every group's ECE within tolerance, or the configuration is not taken to
    validation."""
    return all(ece <= tolerance for ece, _t in report.values())


# ------------------------------------------------------------------------------------------------ §8.2


def _vol(row):
    v = row.features.get("mf.vol_21")
    if v is None or v[0] is None:
        return None
    return float(Decimal(str(v[0])))


def _scale(row, horizon, floor):
    v = _vol(row)
    if v is None:
        return floor, True
    return max(v * math.sqrt(horizon), floor), False


def fit_intervals(fit_scope, fit_rows, conformal_scope, conformal_rows, predicted, dataset, horizon, level=0.9):
    """The floor from the fit slice's positive scales; the per-class quantile of |y - prediction| / scale on the
    conformal slice. predicted: the model's return on each conformal row (None: not used)."""
    fit_rows = _scoped(fit_scope, fit_rows, "fit")
    conformal_rows = _scoped(conformal_scope, conformal_rows, "conformal")
    positive = sorted(v * math.sqrt(horizon) for v in (_vol(r) for r in fit_rows) if v is not None and v > 0)
    if not positive:
        raise ValueError("the fit slice has no positive vol_21 to set the scale floor")
    floor = float(np.quantile(np.array(positive), FLOOR_QUANTILE))
    y = targets(dataset, [r.key for r in conformal_rows], horizon).ret
    by = {}
    for r, pred, yy in zip(conformal_rows, predicted, y):
        if pred is None or np.isnan(yy):
            continue
        s, _flag = _scale(r, horizon, floor)
        by.setdefault(r.meta.get("liquidity_class", "unknown"), []).append(abs(yy - pred) / s)
    q = {}
    for cls in LIQUIDITY:
        res = sorted(by.get(cls, []))
        n = len(res)
        k = math.ceil((n + 1) * level)
        q[cls] = None if k > n else float(res[k - 1])    # too few rows for the level: no finite interval
    return Artifact.make(CONFORMAL, {"slice": "conformal", "horizon": horizon, "level": level, "floor": floor,
                                     "quantile": q, "counts": {c: len(by.get(c, [])) for c in LIQUIDITY}})


def intervals(artifact, rows, predicted):
    """[(low, high) or None, ...] and flags: `vol_21_null` (the floor used), `insufficient_rows` (the class had too
    few conformal rows for the level: no interval is given)."""
    b = artifact.load(CONFORMAL)
    out, flags = [], []
    for r, pred in zip(rows, predicted):
        f = []
        s, null = _scale(r, b["horizon"], b["floor"])
        if null:
            f.append("vol_21_null")
        q = b["quantile"].get(r.meta.get("liquidity_class", "unknown"))
        if pred is None or q is None:
            out.append(None)
            if q is None:
                f.append("insufficient_rows")
        else:
            out.append((pred - q * s, pred + q * s))
        flags.append(tuple(f))
    return out, flags


def coverage_report(dataset, rows, ivals, horizon):
    """§8.2's claim: per liquidity class (and overall), the share of rows inside the interval, averaged over the
    period's dates; with the number of dates behind it."""
    y = targets(dataset, [r.key for r in rows], horizon).ret
    groups = {}
    for r, iv, yy in zip(rows, ivals, y):
        if iv is None or np.isnan(yy):
            continue
        inside = 1.0 if iv[0] <= yy <= iv[1] else 0.0
        for g in ("overall", r.meta.get("liquidity_class", "unknown")):
            groups.setdefault(g, ([], []))
            groups[g][0].append(r.session)
            groups[g][1].append(inside)
    out = {}
    for g, (dates, vals) in sorted(groups.items()):
        per_date = metrics.by_date(dates, vals)
        out[g] = {"coverage": math.fsum(per_date.values()) / len(per_date), "dates": len(per_date),
                  "rows": len(vals)}
    return out


def calibrated_directions(probs):
    return [direction_of(p) for p in probs]
