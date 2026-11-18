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
MIN_GROUP_ROWS = 50            # a reliability group smaller than this is reported, never judged (audit M1)
MIN_WINDOWS = 30               # fewer independent label windows than this: the coverage estimate is coarse (§8.2)


def _same_length(rows, values, what):
    if len(values) != len(rows):
        raise ValueError(f"{len(values)} {what} for {len(rows)} rows")


def _check_level(level):
    if not 0 < level < 1:
        raise ValueError("the interval level is in (0, 1)")


def _check_horizon(body, horizon):
    if horizon is not None and body["horizon"] != horizon:
        raise ValueError(f"the artifact was fitted for h={body['horizon']}, not h={horizon}")


def _check_probs(probs):
    for p in probs:
        if p is not None and (len(p) != 3 or any(not math.isfinite(x) or x < 0 for x in p)
                              or abs(math.fsum(p) - 1) > 1e-9):
            raise ValueError("a probability vector is three finite non-negative numbers summing to 1")


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
    _same_length(rows, probs, "probability vectors")
    _check_probs(probs)
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
    if not res.success:
        raise ValueError(f"vector scaling did not converge: {res.message}")
    a, b = unpack(res.x)
    flags = ["scale_negative"] if (a < 0).any() else []          # the map reverses the model's ranking: say so
    return Artifact.make(SCALING, {"slice": "calibrate", "horizon": horizon, "scale": a.tolist(),
                                   "offset": b.tolist(), "rows_used": len(keep), "converged": True,
                                   "nll": float(res.fun), "flags": flags})


def apply_scaling(artifact, probs, horizon=None):
    """The calibrated probabilities beside the raw ones (§8.3); None stays None."""
    b = artifact.load(SCALING)
    _check_horizon(b, horizon)
    _check_probs(probs)
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


def reliability_report(dataset, rows, probs, horizon, bins=10, scope=None):
    """{"overall": (ECE, table, n), "<liquidity class>": (ECE, table, n)} over rows with a probability and a trained
    direction label. With `scope`, the rows are admitted through it (the calibrate slice, for calibrated_engine)."""
    if scope is not None:
        rows = scope.admit(rows)
    _same_length(rows, probs, "probability vectors")
    _check_probs(probs)
    y = targets(dataset, [r.key for r in rows], horizon).direction
    groups = {}
    for r, p, k in zip(rows, probs, y):
        if p is None or k < 0:
            continue
        for g in ("overall", r.meta.get("liquidity_class", "unknown")):
            groups.setdefault(g, ([], []))
            groups[g][0].append(CLASSES[int(k)])
            groups[g][1].append(_as_metric(p))
    return {g: (metrics.expected_calibration_error(a, p, bins), metrics.reliability(a, p, bins), len(a))
            for g, (a, p) in sorted(groups.items())}


def reliability_ok(report, tolerance, min_rows=MIN_GROUP_ROWS):
    """§8.1 for calibrated_engine: within tolerance overall and in every group with at least `min_rows` rows, or the
    configuration is not taken to validation. No evidence is not a pass (audit M1): an empty report, or an overall
    group below `min_rows`, fails."""
    overall = report.get("overall")
    if overall is None or overall[2] < min_rows:
        return False
    return all(ece <= tolerance for ece, _t, n in report.values() if n >= min_rows)


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
    _check_level(level)
    fit_rows = _scoped(fit_scope, fit_rows, "fit")
    conformal_rows = _scoped(conformal_scope, conformal_rows, "conformal")
    _same_length(conformal_rows, predicted, "predictions")
    if any(p is not None and not math.isfinite(p) for p in predicted):
        raise ValueError("a prediction is not a finite number")
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


def intervals(artifact, rows, predicted, horizon=None):
    """[(low, high) or None, ...] and flags: `vol_21_null` (the floor used), `insufficient_rows` (the class had too
    few conformal rows for the level: no interval is given), `prediction_not_finite`."""
    b = artifact.load(CONFORMAL)
    _check_horizon(b, horizon)
    _same_length(rows, predicted, "predictions")
    out, flags = [], []
    for r, pred in zip(rows, predicted):
        f = []
        s, null = _scale(r, b["horizon"], b["floor"])
        if null:
            f.append("vol_21_null")
        q = b["quantile"].get(r.meta.get("liquidity_class", "unknown"))
        if pred is not None and not math.isfinite(pred):
            out.append(None)
            f.append("prediction_not_finite")
        elif pred is None or q is None:
            out.append(None)
            if q is None:
                f.append("insufficient_rows")
        else:
            out.append((pred - q * s, pred + q * s))
        flags.append(tuple(f))
    return out, flags


def coverage_report(dataset, rows, ivals, horizon, level, tolerance):
    """§8.2's claim and report: per liquidity class (and overall), the share of rows inside the interval, averaged
    over the period's dates, against the nominal level and tolerance. Every class with a labelled row appears, even if
    none had an interval (audit M2): `no_interval` counts them, and a class with labelled rows but no interval is
    reported with coverage None and `within_tolerance` False. `windows` is the number of independent label windows
    (dates / h); below MIN_WINDOWS the estimate is marked `coarse`."""
    _check_level(level)
    _same_length(rows, ivals, "intervals")
    y = targets(dataset, [r.key for r in rows], horizon).ret
    groups = {}
    for r, iv, yy in zip(rows, ivals, y):
        if np.isnan(yy):
            continue
        for g in ("overall", r.meta.get("liquidity_class", "unknown")):
            dates, vals, missing = groups.setdefault(g, ([], [], [0]))
            if iv is None:
                missing[0] += 1
                continue
            dates.append(r.session)
            vals.append(1.0 if iv[0] <= yy <= iv[1] else 0.0)
    out = {}
    for g, (dates, vals, missing) in sorted(groups.items()):
        per_date = metrics.by_date(dates, vals) if vals else {}
        cov = math.fsum(per_date.values()) / len(per_date) if per_date else None
        windows = len(per_date) / horizon
        out[g] = {"coverage": cov, "level": level, "tolerance": tolerance, "dates": len(per_date),
                  "rows": len(vals), "no_interval": missing[0], "windows": windows,
                  "coarse": windows < MIN_WINDOWS,
                  "within_tolerance": cov is not None and missing[0] == 0 and abs(cov - level) <= tolerance}
    return out


def calibrated_directions(probs):
    return [direction_of(p) for p in probs]
