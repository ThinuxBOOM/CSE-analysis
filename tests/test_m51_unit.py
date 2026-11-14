"""
Phase 5, step M5-1 (docs/PHASE5_ML_DESIGN.md revision 5, §6, §9.3, §12): the evaluation library, scoring.1 and the
gate test. Pure and offline; every metric against a hand-computed case. MLK-8 and MLK-22.
"""
import ast
import inspect
import math
import random

import pytest

from worker.evaluation_metrics import bootstrap as bs
from worker.evaluation_metrics import metrics as mt
from worker.evaluation_metrics import scoring as sc

P = lambda f, l, r: {"fall": f, "flat": l, "rise": r}  # noqa: E731


def test_d1_direction_metrics_by_hand():
    actual = ["rise", "rise", "fall", "flat"]
    pred = ["rise", "fall", "fall", "rise"]
    m = mt.confusion_matrix(actual, pred)
    assert m["rise"] == {"fall": 1, "flat": 0, "rise": 1} and m["flat"]["rise"] == 1 and m["fall"]["fall"] == 1
    assert mt.directional_accuracy(actual, pred) == 0.5
    assert mt.balanced_accuracy(actual, pred) == pytest.approx((0.5 + 1.0 + 0.0) / 3)
    assert mt.predicted_class(P(0.4, 0.2, 0.4)) == "rise" and mt.predicted_class(P(0.5, 0.5, 0)) == "flat"
    probs = [P(0.1, 0.2, 0.7), P(0.0, 0.0, 1.0)]
    # (0.01 + 0.04 + 0.09) = 0.14 for the first; (1 + 0 + 0) + ... second realised 'fall' with p 0: 1 + 0 + 1 = 2
    assert mt.brier(["rise", "fall"], probs) == pytest.approx((0.14 + 2.0) / 2)
    ll, floored = mt.log_loss(["rise", "fall"], probs)
    assert floored == 1 and ll == pytest.approx((-math.log(0.7) - math.log(1e-15)) / 2)
    with pytest.raises(ValueError):
        mt.brier(["rise"], [P(0.5, 0.5, 0.5)])


def test_d2_reliability_and_ece():
    actual = ["rise", "rise", "fall", "fall"]
    probs = [P(0.2, 0.0, 0.8), P(0.2, 0.0, 0.8), P(0.8, 0.0, 0.2), P(0.8, 0.0, 0.2)]
    rel = mt.reliability(actual, probs, bins=10)
    assert rel["rise"] == [(0.2, 0.3, 2, 0.2, 0.0), (0.8, 0.9, 2, 0.8, 1.0)]
    # rise: (2·0.2 + 2·0.2)/4 = 0.2; fall the same; flat: p 0, observed 0 → 0. Mean over classes: 0.4/3
    assert mt.expected_calibration_error(actual, probs) == pytest.approx(0.4 / 3)


def test_d3_point_errors_price_volatility_and_intervals():
    e = mt.errors([0.0, 0.0, 0.0, 0.0], [0.1, -0.2, 0.3, -0.4])
    assert e["mae"] == pytest.approx(0.25) and e["rmse"] == pytest.approx(math.sqrt(0.075))
    assert e["median_abs"] == pytest.approx(0.25) and e["mean_signed"] == pytest.approx(-0.05)
    assert e["quantiles"][0.5] == pytest.approx(-0.05) and e["quantiles"][0.05] == pytest.approx(-0.37)
    pe = mt.price_error([100.0], [0.10], [0.05])                    # 105 against 110: (1.05 - 1.10)
    assert pe["mean_signed"] == pytest.approx(-0.05)
    assert mt.volatility_error([0.0, 0.02], [0.01, 0.01])["mse"] == pytest.approx(0.0001)
    with pytest.raises(ValueError):
        mt.volatility_error([0.0], [-0.01])
    cov, width = mt.interval_coverage([0.0, 0.5, -1.0], [-0.1, 0.0, -0.5], [0.1, 0.4, 0.5])
    assert cov == pytest.approx(1 / 3) and width == pytest.approx((0.2 + 0.4 + 1.0) / 3)
    assert mt.by_date(["b", "a", "b"], [1.0, 2.0, 3.0]) == {"a": 2.0, "b": 2.0}
    assert mt.quantile([3, 1, 2], 0.5) == 2 and mt.quantile([1, 2], 0.25) == 1.25


def row(state, **kw):
    if state in (sc.RESOLVED, sc.RESOLVED_CARRIED, sc.UNEXPLAINED):
        kw.setdefault("ret", 0.01)
        kw.setdefault("direction", "rise")
    return sc.LabelRow(("S", "2024-01-02", 5), state, **kw)


def test_s1_scoring_policy_for_every_state():                                                         # MLK-22
    rows = [row(sc.RESOLVED), row(sc.RESOLVED_CARRIED), row(sc.TERMINATED), row(sc.UNEXPLAINED),
            row(sc.UNEXPLAINED, candidate_event=True), row(sc.QUALITY), row(sc.MISSING_PRICE),
            row(sc.UNKNOWN_SESSION), row(sc.PENDING), row(sc.TERMINATED, era_row_absent=True),
            row(sc.MISSING_PRICE, era_row_absent=True), row(sc.RESOLVED, resolved_late=True)]
    p = sc.population(rows)
    assert [r.state for r in p.training] == ["resolved", "resolved_carried"]          # never a late or other state
    assert [r.state for r in p.headline] == ["resolved", "resolved_carried", "resolved"]
    assert len(p.secondary_resolved_only) == 2 and len(p.resolved_late) == 1
    assert all(r.state not in (sc.MISSING_PRICE, sc.UNEXPLAINED, sc.TERMINATED) for r in p.training + p.headline)
    assert [(r.ret, r.direction) for r in p.pessimistic_terminated[3:]] == [(-1.0, "fall")]   # era absence left out
    assert [r.state for r in p.unexplained_raw[3:]] == [sc.UNEXPLAINED]                       # only the genuine one
    assert p.counts == {**{s: 0 for s in sc.STATES}, "resolved": 2, "resolved_carried": 1, "terminated": 2,
                        "unexplained_move_in_horizon": 2, "unresolvable_quality": 1, "unresolvable_missing_price": 2,
                        "unknown_session_in_horizon": 1, "pending": 1, "unexplained_candidate_event": 1,
                        "unexplained_genuine": 1, "era_absence": 2, "resolved_late": 1}
    with pytest.raises(ValueError):
        sc.population([row(sc.RESOLVED, resolved_late=True)], for_training_only=True)
    with pytest.raises(ValueError):
        sc.LabelRow(("S", "d", 1), "resolved")                     # a resolved label carries its return
    with pytest.raises(ValueError):
        sc.LabelRow(("S", "d", 1), "lost")


def test_b1_replicates_blocks_and_insufficient_evidence():
    assert bs.replicates_for(1, 0.05) == 9999 and bs.replicates_for(1000, 0.05) == 400000
    assert bs.gate_test([0.0] * 150, delta=0, h=1, level=0.05, block=10).state == "insufficient_evidence"   # 15 blocks
    r = bs.gate_test([0.0] * 200, delta=0, h=1, level=0.05, block=10, replicates=99)
    assert (r.state, r.blocks) == ("tested", 20.0)
    rng = random.Random(1)
    iid = [rng.gauss(0, 1) for _ in range(500)]
    ar = [0.0]
    for _ in range(499):
        ar.append(0.9 * ar[-1] + rng.gauss(0, 1))
    assert bs.politis_white(iid) < 3 < bs.politis_white(ar)
    assert bs.block_length(iid, 21) == 21 and bs.block_length(iid, 1) == 10


def test_b2_the_test_finds_a_real_difference_and_is_deterministic():
    rng = random.Random(7)
    better = [-0.05 + rng.gauss(0, 0.05) for _ in range(400)]       # M's loss lower by 0.05 per date
    a = bs.gate_test(better, delta=0.0, h=1, level=0.01, replicates=999, seed=3)
    b = bs.gate_test(better, delta=0.0, h=1, level=0.01, replicates=999, seed=3)
    assert a == b and a.rejects and a.p_value <= 0.002
    assert not bs.gate_test(better, delta=0.1, h=1, level=0.01, replicates=999, seed=3).rejects   # effect < delta
    worse = [-v for v in better]
    assert not bs.gate_test(worse, delta=0.0, h=1, level=0.05, replicates=999, seed=3).rejects
    passes, res = bs.family_test({"base_no_change": better, "base_rate_252": worse}, delta=0, h=1, alpha=0.05, m=1,
                                 replicates=999)
    assert not passes and res["base_no_change"].rejects and not res["base_rate_252"].rejects     # every baseline


def _binomial_lower(n, p, tail=0.001):
    """The largest k with P(X < k) <= tail."""
    c = 0.0
    for k in range(n + 1):
        c += math.comb(n, k) * p ** k * (1 - p) ** (n - k)
        if c > tail:
            return k
    return n


def _binomial_upper(n, p, tail=0.001):
    """The smallest k with P(X > k) <= tail for X ~ Binomial(n, p)."""
    c = 0.0
    for k in range(n + 1):
        c += math.comb(n, k) * p ** k * (1 - p) ** (n - k)
        if 1 - c <= tail:
            return k
    return n


def _null_series(rng, n=300, h=5, securities=30):
    """Date-level loss differences of two equally good predictors: a common date shock with GARCH-like volatility
    clustering, smoothed over h dates (overlapping labels), plus the mean of securities' idiosyncratic noise."""
    shocks, sigma2, prev = [], 1.0, 0.0
    for _ in range(n + h):
        sigma2 = 0.1 + 0.85 * sigma2 + 0.1 * prev * prev
        prev = rng.gauss(0, math.sqrt(sigma2))
        shocks.append(prev)
    out = []
    for t in range(n):
        common = math.fsum(shocks[t:t + h]) / h
        idio = math.fsum(rng.gauss(0, 1) for _ in range(securities)) / securities
        out.append(0.02 * (common + idio))
    return out                                                       # expected value 0: the null boundary


def test_mlk8_size_of_the_test_under_dependence():                                                 # MLK-8
    seeds, alpha = 200, 0.05
    single = sum(1 for s in range(seeds) if bs.gate_test(_null_series(random.Random(s)), delta=0, h=5, level=alpha,
                                                         replicates=199, seed=s).rejects)
    assert _binomial_lower(seeds, alpha) <= single <= _binomial_upper(seeds, alpha)     # (a) the nominal size
    m = 5
    family = 0
    for s in range(seeds):
        rng = random.Random(10_000 + s)
        if any(bs.gate_test(_null_series(rng), delta=0, h=5, level=alpha / m, replicates=199, seed=s).rejects
               for _ in range(m)):
            family += 1
    assert family <= _binomial_upper(seeds, alpha)                    # (b) family-wise rate within alpha


def test_p1_the_library_is_pure():
    from worker.evaluation_metrics import metrics, scoring, bootstrap
    for module in (metrics, scoring, bootstrap):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Import):
                assert {a.name.split(".")[0] for a in node.names} <= {"math", "random"}, module.__name__
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split(".")[0] in {"math", "random", "dataclasses", "typing"}, module.__name__
            elif isinstance(node, ast.Attribute):
                assert node.attr not in ("now", "today", "utcnow"), module.__name__
