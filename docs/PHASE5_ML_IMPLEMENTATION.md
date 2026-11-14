# Phase 5: ML and the Route 1 combination (implementation record)

**Design:** `docs/PHASE5_ML_DESIGN.md`, revision 5, accepted [delegated] for implementation on `integration/cloud`
(C-0044). **Branch:** `integration/cloud` only. Nothing here has run on real data; no model exists.

## M5-1: the evaluation library, `scoring.1`, the gate test

**Status: ACCEPTED on the branch (C-0049: audit reported no Blocker; Majors fixed).**

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

---

## M5-3: folds and slices

**Status: ACCEPTED on the branch (C-0049: audit reported no Blocker; Majors fixed).**

| File | Content | Design |
|---|---|---|
| `worker/ml_protocol/folds.py` | `Calendar` (session arithmetic), `Fold` (training, validation, `resolved_late`, unused with reasons), `Slices` (fit, select, calibrate, conformal, with purge and embargo at each boundary), `walk_forward` (expanding folds), `FitScope` (MLK-3) | §5.2, §5.3 (revision 5) |
| `tests/test_m53_unit.py` | 5 tests: MLK-1 (in a session-index era, a datetime era and across two eras; expanding folds never validate a row twice), refusals of malformed folds and slices, MLK-2 (a date memoriser: chance under the protocol in all 40 histories, skill without the purge), MLK-3 | §13 |

Choices made inside the design's words, for the audit:
- The inner slices use the outer split's purge and embargo literally: a select, calibrate or conformal row has
  T at least `embargo` sessions after the slice's lower boundary and at most g sessions before its upper one, and
  R_h at or before the upper one. Rows between are unused.
- A row with no R_h (pending) is never a training or validation row and is counted as `pending`.
- `resolved_late` rows exist only when a label resolves after V_end although its T is g sessions before it (a late
  corporate action or confirmation): the purge already covers the grace period.

Mutation check (6 mutants: no purge, late rows fitted, training by T, slices without purge or R bound, no embargo,
an open fit scope): all killed. Three of my first test expectations were wrong (no late row without a late
confirmation; a memoriser that used non-overlapping labels; reused keys) and were corrected before commit.


---

## Audit of M5-1 and M5-3: fresh-context agent

Audited at `53d15b2`. Verdicts: **both ACCEPT** (no Blocker). The bootstrap's recentring and p-value, the geometric
blocks, the circular wrap and Politis–White were verified numerically; the oversize of MLK-8 comes from the method at
small n (a non-studentised test with about 30 blocks, and the stationary bootstrap's down-weighting of an MA overlap),
not from the code: 6.45% for iid data at n = 300, 5.3% at n = 2 000.

| Finding | Severity | Disposition |
|---|---|---|
| The design's floors on L and B were not enforced inside `gate_test` | Major | **Fixed.** Refused below max(h, 10) and max(9 999, ⌈20/level⌉); only `simulation=True` lifts them, for size studies |
| Test gaps (p without +1, geometric mean, last-block cut, family at α/m, an insufficient baseline in a family, Politis–White) | Major | **Fixed.** `test_b3` (exact p, family level, an untestable baseline fails the family, the fast resampler against a direct step-by-step one on autocorrelated data), `test_b4` (Politis–White against AR(1) theory) |
| MLK-8 at 200 seeds and counting `insufficient_evidence` as non-rejection | Major | **Fixed.** 400 seeds; the rate among tested seeds. Measured: 30 of 368 tested (8.2%), 32 untestable; inside the acceptance region, and oversized as stated above |
| Slice T-side purge and embargo never asserted | Major | **Fixed.** `check_mlk1` asserts them; an embargo of 3 case |
| R_h ≤ T rows; negative grace or embargo; length and class checks in metrics; fewer than 10 dates; pending row in a window; R = C_train; inclusive coverage; balanced accuracy over present classes | Minor | **Fixed** and tested |
| `price_error` equals the return error | Minor | **Stated** in its docstring |
| `era_absence` mixes two states; the datetime session convention | Minor | **Carried** |
