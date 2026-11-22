"""
MLK-23 (design §9.3, §12.3, §13; revision 5): the gate test's size at a protocol's OWN dependence, shown before gates 5
and 9 can pass (`size_shown`).

The dependence is estimated on TRAINING rows only: the lag-1 autocorrelation of the date-level series (serial
dependence, including what a common date factor and overlapping horizons leave in it) and of the squares of its AR(1)
residuals (volatility clustering). Null series of the protocol's length are simulated with those parameters and mean zero (an AR(1) with
GARCH-like variance), the gate test is run on each at the protocol's level and block length, and the share of
rejections among the tested series is compared with the binomial acceptance region (0.1 % to 99.9 %) around the
nominal level. Outside it, the configuration of the method is unreliable, and gate 5 fails as `size_not_shown`.

Simulation lifts the test's floors on B (replicates) for speed only (`simulation=True` in `gate_test`); the level and
the block length are the protocol's. The result says how many series were tested and the region used.
"""
import math
import random
from dataclasses import dataclass

from . import bootstrap


def _acf1(xs):
    n = len(xs)
    if n < 3:
        return 0.0
    m = math.fsum(xs) / n
    num = math.fsum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, n))
    den = math.fsum((x - m) ** 2 for x in xs)
    return 0.0 if den == 0 else num / den


@dataclass(frozen=True)
class Dependence:
    phi: float                   # lag-1 autocorrelation of the series, clipped to (-0.95, 0.95)
    arch: float                  # lag-1 autocorrelation of the squared, demeaned series, clipped to [0, 0.9]


def estimate(training_series):
    """The dependence of a date-level series computed on training rows only. `arch` is measured on the AR(1)
    residuals' squares: the series' own squares already carry phi squared (M5-8 audit Minor 4)."""
    xs = list(training_series)
    m = math.fsum(xs) / len(xs) if xs else 0.0
    phi = max(-0.95, min(0.95, _acf1(xs)))
    resid = [(xs[i] - m) - phi * (xs[i - 1] - m) for i in range(1, len(xs))]
    return Dependence(phi, max(0.0, min(0.9, _acf1([e * e for e in resid]))))


def simulate(dep, n, rng):
    """A null series of length n with mean zero and the given dependence."""
    out, x, var = [], 0.0, 1.0
    for _ in range(n + 50):                       # 50 burn-in steps
        e = rng.gauss(0, 1) * math.sqrt(var)
        x = dep.phi * x + e
        var = (1 - dep.arch) + dep.arch * e * e
        out.append(x)
    return out[50:]


def _binom_cdf(k, n, p):
    return math.fsum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def acceptance_region(n, p, lo=0.001, hi=0.999):
    """The smallest and largest rejection counts whose binomial tail probabilities stay within (lo, hi)."""
    a = next(k for k in range(n + 1) if _binom_cdf(k, n, p) >= lo)
    b = next(k for k in range(n + 1) if _binom_cdf(k, n, p) >= hi)
    return a, b


@dataclass(frozen=True)
class SizeResult:
    dependence: Dependence
    n_dates: int
    level: float
    block: int
    seeds: int
    tested: int
    rejected: int
    region: tuple

    @property
    def rate(self):
        return self.rejected / self.tested if self.tested else None

    @property
    def shown(self):
        """Gate 5's `size_shown`: enough series were testable and the rejections lie inside the region."""
        return self.tested >= self.seeds // 2 and self.region[0] <= self.rejected <= self.region[1]


def size_study(training_series, n_dates, *, h, level, seeds=200, replicates=199, block=None, seed=0):
    dep = estimate(training_series)
    rng = random.Random(seed)
    tested = rejected = 0
    used_block = block
    for s in range(seeds):
        r = bootstrap.gate_test(simulate(dep, n_dates, rng), delta=0.0, h=h, level=level, replicates=replicates,
                                seed=seed + s, block=block, simulation=True)
        if r.state == "tested":
            tested += 1
            rejected += bool(r.rejects)
            used_block = r.block_length
    region = acceptance_region(tested, level) if tested else (0, 0)
    return SizeResult(dep, n_dates, level, used_block or 0, seeds, tested, rejected, region)
