# Phase 9: outcome evaluation (design)

**Status: DESIGN, revision 1 (2026-11-20). Not audited. Not implemented.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no
provider call and no change to `main`.

**Governing records:** Master Architecture (MA) §4, §24 to §26, §37, §55 (Phase 9), §56, §60, §62, §68; the Phase 3
design (§7 to §9: targets, `targets.1`, `resolved_at`, label states); the Phase 5 design (§6: the scoring population
and `scoring.1`; §12: metrics, strata, the date-level bootstrap; §9.3: the gate test); the Phase 7 design (§7.4
`as_stated`, §9 contamination); the Phase 8 design (`fc_forecasts`). Where this document and those differ, they win.

**Short name:** OE. Steps OE-1 to OE-5 (§10). Decisions P9-D*n* and P9-R*n* (§9).

---

## 1. Conclusion

```
fc_forecasts (T, h) ── wait until the label resolves ──▶ MF target build (the actual, targets.1)
        ─▶ ev_outcomes: per forecast and per route, the actual and every error (immutable)
        ─▶ ev_reports: per period, metrics by stratum with date-level bootstrap intervals; route comparison
```

1. **The actual comes from one place.** Realised direction, return and volatility are MF's labels (`targets.1`),
   computed point-in-time with corporate actions and the session calendar (Phase 3 §7 to §9). Phase 9 never computes
   its own return from prices, so a forecast and its training labels mean the same event.
2. **Evaluated when the label is resolved, recorded as it was known.** An outcome row binds the label row (build id,
   row hash, `resolved_at`) it used. A later label build that changes the label (an action entered late, Phase 3
   ML-21) produces a new outcome row that `revises` the first; the first stays (MA §24).
3. **Each route is evaluated on its own** (MA §26), with the same metric library as Phase 5 (MA §25 via
   `worker/evaluation_metrics`), and Route 2 per provider configuration and specification version. Neither route
   replaces the other because it did better on some date.
4. **Comparisons carry their uncertainty.** Route comparisons and period summaries use Phase 5's estimand (the mean
   over dates of the date-level loss) with the date-level stationary bootstrap; fewer than 20 blocks is reported as
   unreliable (Phase 5 §12.3). Nothing is ranked on a handful of rows.
5. **Live, late and simulated never mix.** Live forecasts, late assemblies (Phase 8 §8), and simulated forecasts
   (Phase 10) are evaluated in separate populations; Route 2 results that are not `clean` are never pooled with clean
   ones (Phase 7 §9).

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §24 | Wait for the target date, obtain the actual, evaluate each route; a separate immutable record | §4 |
| §25 | Direction, probability, return, price, volatility metrics; strata by horizon, security, sector, liquidity, model version, regime, period | §5 |
| §26 | Independent route evaluation; no automatic replacement | §6 |
| §37 | Regime segmentation | §5 (Phase 5 §12.2's tags) |
| §55 | Automatic target-date evaluation; errors; calibration; route comparisons; historical performance | §4 to §7 |

---

## 3. Boundary

- **Reads:** `fc_forecasts` and the rows they bind (Phase 8), MF target builds and rows (Phase 3), MF regime tags,
  Phase 5's validation reports (for the comparison with validated performance).
- **Writes:** `ev_*` only. Phase 11 reads them for monitoring; Phase 12 for display.
- **Does not:** change a forecast, a model or a label; decide any promotion.

---

## 4. Outcomes (OE-1, OE-2)

- **When.** A daily job after the target build for the latest session: for each forecast whose horizon has elapsed,
  it looks for the label of (symbol, D, h) in the designated evaluation build (KNOWN, `targets.1`, the latest build
  recorded at the job's instant).
- **States** follow the label: `resolved` (evaluated); `unresolvable_*` and `terminated` (recorded with the label's
  state, scored by `scoring.1` exactly as Phase 5 scores them, e.g. a delisting is not silently dropped);
  `unexplained_move_in_horizon` (recorded as `provisional`, never as resolved); not yet resolved (no row; retried
  daily).
- **What is recorded per forecast and route:** the actual class, return and volatility; the derived actual price; for
  direction: hit, the class confusion cell, Brier and log loss; for return: signed and absolute error, inside the
  interval or not; for price: absolute and percentage error; for volatility: error and ratio; the label row's build
  id, row hash and `resolved_at`; the metric code version.
- **Revision.** Each run compares the label it would use with the one bound to the latest outcome row of that
  forecast; a different row hash makes a new outcome row with `revises` set and the reason (`label_changed`). Reports
  use the latest outcome per forecast and show how many were revised.

---

## 5. Metrics and strata (OE-3)

From `worker/evaluation_metrics` (one library for Phases 5, 9 and 10): directional and balanced accuracy, confusion
matrix; multi-class Brier, log loss, reliability and ECE; MAE, RMSE, median absolute error, signed error, error
quantiles; price errors; volatility error; interval coverage and width per liquidity class (Phase 5 §8.2).

Strata (Phase 5 §12.2): horizon, security, liquidity class, model version (Route 1 designation and model ids; Route 2
configuration and specification), time period, model age, market regime, industry group (sector), agreement category
(Phase 8 §5). Per-security results over few rows are shown with their counts and are not ranked. A stratum below the
reporting minimum (P9-D3) is shown as counts only.

**Calibration monitoring.** Reliability tables and ECE per period; Route 1's interval coverage per class against its
nominal level and its validated coverage; Route 2's `as_stated` probabilities scored as stated (Phase 7 §7.4).

---

## 6. Route comparison (OE-4)

- Per (horizon, period), on the forecasts where both routes predicted: the date-level loss difference (log loss for
  direction, absolute error for return) with its bootstrap interval and the number of blocks.
- Reported per Route 2 pack policy: comparisons under the default pack (Route 2 saw Route 1) are labelled as such;
  only `independent`-pack results support a claim that the routes carry different information (Phase 7 §10).
- The agreement categories are evaluated too: the error of each route when the routes `agree` versus `diverge`.
- These reports inform the owner. They change no designation by themselves (MA §26, §27).

---

## 7. Reports and storage (OE-5)

**Migration `0025_outcome_evaluation.sql`** (additive; append-only; waits in `migrations_pending/`):

| Table | Contents |
|---|---|
| `ev_outcomes` | One row per (forecast, route, evaluation): the `population` (`live`, `late`, `simulated`) and exactly one of `fc_forecast_id` or `bt_forecast_id` (Phase 10, P10-D5); the label binding (build id, row hash, `resolved_at`), the state, the actual values, every per-row error of §4, `revises`, the metric code version, `row_hash` |
| `ev_reports` | A period report: its population (live, late, simulated run id), period, strata, metrics with intervals and block counts, the outcome ids it read (or their hash), the code version, `report_hash` |

Stamped by the database, append-only, hash-guarded as 0021. A report is reproducible from the outcome rows it names.

---

## 8. Point-in-time

- An outcome's instant is its `recorded_at`; the label it binds must have been recorded at or before it. Evaluation
  needs data after the forecast by definition: what it must not do is let an outcome reach a forecast or a model.
  Nothing upstream reads `ev_*`, except Phase 11's monitoring (drift and alerts, which change nothing by themselves)
  and Phase 5's protocol when the owner declares a new protocol whose test period lies after these outcomes.
- **Test periods.** A live period that an ML protocol has declared as its test period (`ml_test_periods`) is not
  reported per model before that protocol's single access: Phase 9's reports for models of that protocol exclude it
  and say so (MLK-4/MLK-5 at the reporting layer). Route-level reports that do not name the protocol's models are
  not affected.

---

## 9. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| P9-D1 | The actual is MF's label, never a separate computation | One definition of the event for training, forecasting and evaluation |
| P9-D2 | A changed label makes a new outcome row; nothing is overwritten | MA §24 |
| P9-D3 | Reporting minimum: 30 dates and 20 blocks for an interval; below it counts only | Phase 5 §12.3 |
| P9-D4 | Live, late and simulated populations never pooled | Different information sets |
| P9-D5 | Unspent test periods are hidden from per-model reports of their protocol | MLK-4 |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P9-R1 | Which reports are shown on the dashboard by default (Phase 12) | Phase 12 |
| P9-R2 | Whether evaluation of a Route 2 configuration continues after it is withdrawn from service | Its withdrawal |

---

## 10. Implementation sequence

| Step | What | Exit |
|---|---|---|
| OE-1 | Pure outcome computation from a forecast and a label | Every label state; hand-computed errors |
| OE-2 | The daily job, revision rule | A late action produces a revising row; the first is unchanged |
| OE-3 | Metrics and strata (reusing the library) | Same numbers as Phase 5 on the same rows |
| OE-4 | Route comparison | Bootstrap intervals; pack-policy labelling |
| OE-5 | Migration 0025, store, `verify`, runbook | PostgreSQL 17: append-only, hash guards, OEL tests |

---

## 11. Tests

| ID | Planted | Must hold |
|---|---|---|
| OEL-1 | A label build recorded after the outcome | The outcome unchanged; the next run revises with `label_changed` |
| OEL-2 | `unexplained_move_in_horizon` | `provisional`, never `resolved` |
| OEL-3 | A delisting in the horizon | Scored per `scoring.1`, not dropped |
| OEL-4 | Simulated and live forecasts of the same key | Separate populations; no report pools them |
| OEL-5 | A Route 2 `contaminated` simulated result | Excluded from every clean report |
| OEL-6 | A declared, unspent test period | Absent from the protocol's models' reports, with the reason |
| OEL-7 | A forecast whose label never resolves | No outcome row; counted as pending |

---

## 12. The fifteen questions (MA §68)

1. Layer: evaluation. 2. Sources: forecasts, MF labels. 3. At its instant: label rows recorded before it.
4. Provenance: label binding, outcome and report hashes. 5. Frozen: none. 6. Migration: 0025. 7. Leakage: nothing
upstream reads outcomes; test periods hidden until accessed. 8. Tests: §11. 9. Reproduction: reports from named
outcome rows. 10. Routes: each separately, compared with intervals. 11. Evaluation: this is it. 12. Downtime: the
daily job catches up; late assemblies kept apart. 13. Failures: label states recorded as such. 14. Versions: metric
code, policies. 15. Reasoning: reports show their counts and intervals.

---

## 13. Audit record

None yet.
