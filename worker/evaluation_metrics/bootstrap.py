"""
The gate test (Phase 5 §9.3, revision 5): a one-sided test that a model M beats a baseline B by more than delta, on
the series of per-date loss differences d_t = loss_M(t) - loss_B(t), by a recentred stationary bootstrap.

    H0: mean(d) >= -delta        against        H1: mean(d) < -delta
    replicate means of the resampled series are recentred to the null boundary -delta;
    p = (1 + #{replicate mean <= observed mean}) / (B + 1)

The estimand is the mean over dates of the date-level loss (§12.3). With fewer than MIN_BLOCKS independent blocks the
test is not run: the result is `insufficient_evidence`.
"""
import math
import random
from dataclasses import dataclass
from typing import Optional

MIN_BLOCKS = 20
DEFAULT_REPLICATES = 9999


def replicates_for(m, alpha):
    """B = max(9 999, ceil(20 m / alpha)): enough resolution for the Bonferroni level alpha/m."""
    if m < 1 or not 0 < alpha < 1:
        raise ValueError("m >= 1 and alpha in (0, 1)")
    return max(DEFAULT_REPLICATES, math.ceil(20 * m / alpha))


def _autocov(x, k):
    n, mu = len(x), math.fsum(x) / len(x)
    return math.fsum((x[i] - mu) * (x[i + k] - mu) for i in range(n - k)) / n


def politis_white(x):
    """Automatic expected block length for the stationary bootstrap (Politis and White 2004, with the correction of
    Patton, Politis and White 2009), using the flat-top lag window. Returns a float >= 1."""
    n = len(x)
    if n < 10:
        raise ValueError("too short to estimate a block length")
    g0 = _autocov(x, 0)
    if g0 == 0:
        return 1.0
    kn = max(5, math.ceil(math.log10(n)))
    mmax = math.ceil(math.sqrt(n)) + kn
    c = 2 * math.sqrt(math.log10(n) / n)
    rho = [_autocov(x, k) / g0 for k in range(0, min(mmax + kn, n - 1) + 1)]
    mhat = None
    for m in range(1, len(rho) - kn):
        if all(abs(rho[m + j]) < c for j in range(1, kn + 1)):
            mhat = m
            break
    if mhat is None:
        mhat = mmax
    big_m = min(2 * mhat, mmax)

    def lam(t):
        t = abs(t)
        return 1.0 if t <= 0.5 else (2 * (1 - t) if t <= 1 else 0.0)
    gam = [_autocov(x, k) for k in range(0, big_m + 1)]
    g = math.fsum(2 * lam(k / big_m) * k * gam[k] for k in range(1, big_m + 1))
    spec0 = gam[0] + math.fsum(2 * lam(k / big_m) * gam[k] for k in range(1, big_m + 1))
    d_sb = 2 * spec0 ** 2
    if d_sb == 0:
        return 1.0
    return max(1.0, (2 * g * g / d_sb) ** (1 / 3) * n ** (1 / 3))


def block_length(d, h):
    """L = max(h, 10, the data-driven estimate), rounded up."""
    return max(h, 10, math.ceil(politis_white(d)))


def stationary_means(x, L, replicates, rng):
    """Means of `replicates` stationary-bootstrap resamples of x (circular), with geometric block lengths of mean L.
    Each block's sum comes from prefix sums, so a replicate costs O(number of blocks)."""
    n = len(x)
    pre = [0.0]
    for v in x + x:
        pre.append(pre[-1] + v)
    p = 1.0 / L
    out = []
    for _ in range(replicates):
        total, taken = 0.0, 0
        while taken < n:
            start = rng.randrange(n)
            # geometric length with mean L: 1 + floor(ln U / ln(1 - p)); exact inverse-CDF draw
            length = 1 if p >= 1 else 1 + int(math.log(1.0 - rng.random()) / math.log(1.0 - p))
            length = min(length, n - taken)
            total += pre[start + length] - pre[start]
            taken += length
        out.append(total / n)
    return out


@dataclass(frozen=True)
class TestResult:
    state: str                    # tested | insufficient_evidence
    observed_mean: Optional[float]
    p_value: Optional[float]
    block_length: int
    blocks: float
    replicates: int
    rejects: Optional[bool] = None


def gate_test(d, *, delta, h, level, replicates=None, seed=0, block=None, simulation=False):
    """The one-sided test of H0: mean(d) >= -delta at `level` (alpha / m for a family of m).
    The design's floors are enforced: L >= max(h, 10) and B >= max(9 999, ceil(20 / level)). `simulation=True` lifts
    them, for size studies only (MLK-8); a gate never passes it."""
    d = [float(v) for v in d]
    if delta < 0 or not 0 < level < 1:
        raise ValueError("delta >= 0 and level in (0, 1)")
    floor_b = max(DEFAULT_REPLICATES, math.ceil(20 / level))
    replicates = replicates if replicates is not None else floor_b
    if not simulation and (replicates < floor_b or (block is not None and block < max(h, 10))):
        raise ValueError("the block length or the number of replicates is below the design's floor")
    if len(d) < 10:
        return TestResult("insufficient_evidence", None, None, 0, 0.0, 0)
    L = block if block is not None else block_length(d, h)
    blocks = len(d) / L
    if blocks < MIN_BLOCKS:
        return TestResult("insufficient_evidence", None, None, L, blocks, 0)
    obs = math.fsum(d) / len(d)
    centred = [v - obs - delta for v in d]                       # the series under the null boundary, mean -delta
    means = stationary_means(centred, L, replicates, random.Random(seed))
    p = (1 + sum(1 for m in means if m <= obs)) / (replicates + 1)
    return TestResult("tested", obs, p, L, blocks, replicates, p <= level)


def family_test(d_by_baseline, *, delta, h, alpha, m, replicates=None, seed=0, simulation=False):
    """§9.3: M must reject against every baseline (intersection-union, no correction across baselines), each at the
    Bonferroni level alpha / m. Returns (passes, {baseline: TestResult})."""
    level = alpha / m
    replicates = replicates or replicates_for(m, alpha)
    results = {b: gate_test(d, delta=delta, h=h, level=level, replicates=replicates, seed=seed,
                            simulation=simulation)
               for b, d in sorted(d_by_baseline.items())}
    passes = bool(results) and all(r.state == "tested" and r.rejects for r in results.values())
    return passes, results
