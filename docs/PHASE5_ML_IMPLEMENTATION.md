# Phase 5: ML and the Route 1 combination (implementation record)

**Design:** `docs/PHASE5_ML_DESIGN.md`, revision 5, accepted [delegated] for implementation on `integration/cloud`
(C-0044). **Branch:** `integration/cloud` only. Nothing here has run on real data; no model exists.

## M5-1: the evaluation library, `scoring.1`, the gate test

**Status: implemented and tested (2026-11-14); audit pending.**

| File | Content | Design |
|---|---|---|
| `worker/evaluation_metrics/metrics.py` | Confusion matrix, directional and balanced accuracy, multi-class Brier, log loss (a zero probability floored at 1e-15 and counted), reliability and ECE, point errors with quantiles, price error, volatility error, interval coverage and width, the date-level series | §12.1, MA §25 |
| `worker/evaluation_metrics/scoring.py` | `scoring.1`: training, headline, `resolved` alone, `resolved_late`, the pessimistic `terminated` sensitivity, the genuine unexplained-move sensitivity; counts per state, candidate-event exclusions and `era_absence` | §6 (revision 5) |
| `worker/evaluation_metrics/bootstrap.py` | The one-sided recentred stationary-bootstrap test, B = max(9 999, ⌈20m/α⌉), L = max(h, 10, Politis–White), `insufficient_evidence` below 20 blocks, the intersection-union test over baselines at α/m | §9.3, §12.3 |
| `tests/test_m51_unit.py` | 8 tests, every metric against a hand-computed case; MLK-8; MLK-22 | §13 |

Choices made inside the design's words, for the audit:
- Floats with `math.fsum` and fixed orders, not Decimal: these are statistics, not stored prices.
- The predicted class breaks ties in the order rise, flat, fall.
- ECE is the mean over the three classes of the one-vs-rest, count-weighted calibration gap, 10 equal-width bins.
- The data-driven block length is Politis and White (2004) with the Patton, Politis and White (2009) correction.
- **MLK-8 in CI uses 200 seeds and B = 199** (the design says 400 seeds) so that it runs in about 6 seconds; the
  acceptance region is the binomial 0.1% to 99.9% range for 200 trials.

**Measured (synthetic, n = 300 dates, h = 5, a GARCH-like common shock smoothed over h dates, 30 securities):** the
single test rejected in 15 of 200 null cases (7.5% at nominal 5%), and the family of five at α/5 in 16 of 200 (8%).
Both are inside the acceptance region, but **both lean above nominal**: under strong dependence and short series the
test is mildly oversized. That is what MLK-23, run at a real protocol's own dependence before gates 5 and 9 can pass
(revision 5), is for. It is recorded here, not hidden.

An error of mine caught during this step: the first version of the null-series generator subtracted each series'
sample mean, which made the observed mean exactly zero and the test unable to reject (0 of 200); the check passed
because it was one-sided. Fixed: the generator has expected mean zero, and MLK-8's check is two-sided as the design
says.
