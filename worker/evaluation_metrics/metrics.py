"""MA §25's metrics (Phase 5 §12.1). Inputs are plain sequences; a row with a missing value is never passed in (the
caller applies the scoring policy first). Classes are the strings 'rise', 'flat', 'fall'."""
import math
from typing import Sequence

CLASSES = ("fall", "flat", "rise")
LOG_LOSS_FLOOR = 1e-15            # a probability of 0 for the realised class is scored as this, and counted


def _need(xs, n=1):
    if len(xs) < n:
        raise ValueError(f"needs at least {n} value(s)")


def _same(*seqs):
    if len({len(s) for s in seqs}) != 1:
        raise ValueError("one prediction per label: the inputs have different lengths")


def _classes(xs):
    if any(x not in CLASSES for x in xs):
        raise ValueError("a class is one of rise, flat, fall")


def mean(xs):
    _need(xs)
    return math.fsum(xs) / len(xs)


def quantile(xs, q):
    """Linear interpolation between order statistics (type 7, the common default)."""
    _need(xs)
    if not 0 <= q <= 1:
        raise ValueError("q in [0, 1]")
    v = sorted(xs)
    pos = q * (len(v) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def median(xs):
    return quantile(xs, 0.5)


# ---------------------------------------------------------------------------------------------------- direction

def predicted_class(probs):
    """The most probable class; ties broken in the fixed order rise, flat, fall (documented, deterministic)."""
    order = ("rise", "flat", "fall")
    return max(order, key=lambda c: (probs[c], -order.index(c)))


def confusion_matrix(actual: Sequence[str], predicted: Sequence[str]):
    _same(actual, predicted)
    _classes(actual), _classes(predicted)
    m = {a: {p: 0 for p in CLASSES} for a in CLASSES}
    for a, p in zip(actual, predicted):
        m[a][p] += 1
    return m


def directional_accuracy(actual, predicted):
    _need(actual)
    _same(actual, predicted)
    _classes(actual), _classes(predicted)
    return sum(1 for a, p in zip(actual, predicted) if a == p) / len(actual)


def balanced_accuracy(actual, predicted):
    """Mean recall over the classes present among the labels."""
    m = confusion_matrix(actual, predicted)
    recalls = [m[c][c] / sum(m[c].values()) for c in CLASSES if sum(m[c].values())]
    _need(recalls)
    return math.fsum(recalls) / len(recalls)


def _check_probs(p):
    if set(p) != set(CLASSES) or any(v < 0 for v in p.values()) or abs(math.fsum(p.values()) - 1) > 1e-9:
        raise ValueError("three probabilities, non-negative, summing to 1")


def brier(actual, probs):
    """Multi-class Brier score: mean over rows of the sum over classes of (p - indicator)^2. 0 is perfect, 2 worst."""
    _need(actual)
    _same(actual, probs)
    _classes(actual)
    total = []
    for a, p in zip(actual, probs):
        _check_probs(p)
        total.append(math.fsum((p[c] - (1.0 if c == a else 0.0)) ** 2 for c in CLASSES))
    return math.fsum(total) / len(total)


def log_loss(actual, probs):
    """(mean of -ln p(realised class), the number of rows floored at LOG_LOSS_FLOOR)."""
    _need(actual)
    _same(actual, probs)
    _classes(actual)
    terms, floored = [], 0
    for a, p in zip(actual, probs):
        _check_probs(p)
        q = p[a]
        if q < LOG_LOSS_FLOOR:
            q, floored = LOG_LOSS_FLOOR, floored + 1
        terms.append(-math.log(q))
    return math.fsum(terms) / len(terms), floored


def reliability(actual, probs, bins=10):
    """One-vs-rest reliability for every class: per equal-width bin of the predicted probability, (count, mean
    predicted, observed frequency). Returns {class: [(lo, hi, n, mean_p, freq) for non-empty bins]}."""
    _need(actual)
    _same(actual, probs)
    _classes(actual)
    out = {}
    for c in CLASSES:
        groups = [[] for _ in range(bins)]
        for a, p in zip(actual, probs):
            k = min(int(p[c] * bins), bins - 1)
            groups[k].append((p[c], 1.0 if a == c else 0.0))
        out[c] = [(k / bins, (k + 1) / bins, len(g), math.fsum(x for x, _ in g) / len(g),
                   math.fsum(y for _, y in g) / len(g)) for k, g in enumerate(groups) if g]
    return out


def expected_calibration_error(actual, probs, bins=10):
    """Mean over classes of the count-weighted mean |mean predicted - observed| over that class's bins."""
    rel = reliability(actual, probs, bins)
    n = len(actual)
    return math.fsum(math.fsum(cnt * abs(mp - fr) for _, _, cnt, mp, fr in rows) / n for rows in rel.values()) / 3


# ---------------------------------------------------------------------------------------------- return, volatility

def errors(actual, predicted):
    """Point-forecast errors (predicted - actual): MAE, RMSE, median absolute error, mean signed error, and the
    5th, 25th, 50th, 75th and 95th percentiles of the signed error."""
    if len(actual) != len(predicted):
        raise ValueError("one prediction per label")
    e = [p - a for a, p in zip(actual, predicted)]
    _need(e)
    return {"mae": mean([abs(x) for x in e]), "rmse": math.sqrt(mean([x * x for x in e])),
            "median_abs": median([abs(x) for x in e]), "mean_signed": mean(e),
            "quantiles": {q: quantile(e, q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}}


def mse(actual, predicted):
    _same(actual, predicted)
    return mean([(p - a) ** 2 for a, p in zip(actual, predicted)])


def price_error(reference_close, actual_return, predicted_return):
    """Predicted price reference × (1 + predicted return) against the realised reference × (1 + return), as a relative
    error per row (§6, derived fields). Returns the errors() of the relative price errors. The reference close
    cancels, so this equals the return error; it is kept so that reports name the price error MA §25 asks for, and
    the rupee error is reference × this."""
    actual = [1.0 + r for r in actual_return]
    pred = [1.0 + r for r in predicted_return]
    if any(x <= 0 for x in reference_close):
        raise ValueError("a reference close is positive")
    return errors(actual, pred)          # the reference close cancels in the relative error


def volatility_error(actual, predicted):
    """The volatility output's primary loss is MSE of the mean in level units (P5-D17); a negative prediction is a
    defect upstream, refused here."""
    if any(p < 0 for p in predicted):
        raise ValueError("a predicted volatility is never negative")
    out = errors(actual, predicted)
    out["mse"] = mse(actual, predicted)
    return out


def interval_coverage(actual, lower, upper):
    """(share of rows inside [lower, upper], mean width). §8.2's estimand averages per date: see by_date()."""
    _need(actual)
    _same(actual, lower, upper)
    if any(lo > hi for lo, hi in zip(lower, upper)):
        raise ValueError("lower <= upper")
    inside = [1.0 if lo <= a <= hi else 0.0 for a, lo, hi in zip(actual, lower, upper)]
    return mean(inside), mean([hi - lo for lo, hi in zip(lower, upper)])


def by_date(dates, values):
    """The date-level series: {date: mean of the values on that date}, in date order (§9.3, §12.3)."""
    groups = {}
    for d, v in zip(dates, values):
        groups.setdefault(d, []).append(v)
    return {d: math.fsum(v) / len(v) for d, v in sorted(groups.items())}
