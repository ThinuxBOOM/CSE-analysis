# Phase 11: adaptation (design)

**Status: DESIGN, revision 1 (2026-11-20). Not audited. Not implemented.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no
provider call, no promotion and no change to `main`.

**Governing records:** Master Architecture (MA) §27, §28, §55 (Phase 11), §58, §59, §61, §68; the Phase 5 design
(§5.6: roll-forward versions; §8: calibration; §9: the registry, gates and promotion; §11.2: the domain and model age),
the Phase 7 design (§7.4: R2 calibration), the Phase 9 design (outcomes, reports) and the Phase 10 design (valid
backtests). Where this document and those differ, they win.

**Short name:** AD. Steps AD-1 to AD-5 (§9). Decisions P11-D*n* and P11-R*n* (§8).

---

## 1. Conclusion

```
ev_outcomes (Phase 9) + inputs at T ─▶ monitors (drift, calibration, performance, age) ─▶ ad_reports, ad_alerts
        an alert, or the schedule ─▶ a candidate: roll-forward refit, or recalibration (a new version)
        ─▶ Phase 5's gates (by reference where the procedure is unchanged) ─▶ shadow period (predicts, not shown)
        ─▶ comparison with the incumbent on the shadow period ─▶ the owner promotes, or not; the incumbent is retired
           only by the owner
```

1. **Adaptation is controlled improvement** (MA §27). A bad day changes nothing. Monitors raise alerts; alerts
   propose candidates; candidates go through Phase 5's gates and a shadow period; only the owner promotes or retires.
2. **A production model is never modified** (MA §27, §58). A refit or recalibration is a new registered version
   (`rollforward_of`, or a new calibration artifact under a new version); the incumbent keeps serving until the owner
   designates the successor.
3. **Monitors are measurements with stated thresholds**, computed on point-in-time inputs and resolved outcomes only;
   every threshold is a versioned, provisional policy (P11-D2).
4. **The shadow period is the prospective test the history cannot give.** With history ending in 2025 and serving
   after it, a candidate is compared to the incumbent on the same live dates before it serves (MA §27 "compare to
   incumbent").

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §27 | Outcomes → evaluation history → monitoring → candidate retraining/recalibration → chronological validation → compare to incumbent → version → controlled deployment; never silently modified | §1 to §5 |
| §28 | Registry entries and statuses | Phase 5's registry, used unchanged |
| §55 | Drift, retraining, recalibration, candidate validation, promotion/retirement | §3 to §5 |
| §58, §59 | Research/production separation; nothing changed casually | §1 item 2, §5 |

---

## 3. Monitors (AD-1, AD-2), policy `monitor.1`

Each monitor runs on a schedule (weekly by default, P11-D3), over a window of recent dates, and writes a report row;
a breach writes an alert.

| Monitor | Measures | Breach (provisional) |
|---|---|---|
| Feature drift | Per schema feature: the population stability index of its values at recent T against the model's fit slice (the reference stored with the validated domain) | PSI ≥ 0.25 on any critical feature, or ≥ 0.1 on a third of the schema |
| Domain pressure | The share of rows refused as `outside_validated_domain` or clipped, against validation's share | Above twice validation's share over 20 sessions |
| Calibration | Route 1's ECE and interval coverage per class on resolved outcomes (Phase 9), against the validated values | Coverage outside the binomial acceptance region for its count; ECE above validated ECE + 0.05 |
| Performance | The date-level loss difference against `base_prior` on resolved outcomes, with its bootstrap interval | The interval lies entirely above zero (worse than the baseline) |
| Age | Sessions since the model's C_train against `max_model_age_sessions` | 80 % of the limit: an early notice; at the limit the domain blocks (Phase 5 §11.2) |
| Route 2 | Its `as_stated` calibration and failure rate per configuration | Failure rate above the configuration's validated rate + 10 points |

- Inputs are read at their own instants; outcomes only once resolved (Phase 9). A monitor never reads a label for a
  date inside an unspent test period of the model's protocol (Phase 9 §8).
- Monitors report through `status` and Phase 12's health page; an alert channel is P1's open decision (LP-R3).

---

## 4. Candidates (AD-3)

| Kind | When | What it is | Validation |
|---|---|---|---|
| Roll-forward | Scheduled (P11-R2) or on an age, drift or performance alert | The same configuration fitted with a later C_train, same procedure | Phase 5 §5.6: gates 2 and 4 to 9 by reference to the configuration's report and test; gates 1 and 3 itself; MLK-11; its own domain |
| Recalibration | On a calibration alert | The same fitted model with vector scaling and conformal quantiles refitted on a later calibrate and conformal slice | A new version with the same model artifact and new calibration artifacts; the same rules as a roll-forward |
| New configuration | Never automatic | A research change | A new protocol under Phase 5 (a new test period, a new access) |

- A candidate's slices follow Phase 5 §5.3 with C_train at the candidate's cutoff; the embargo and grace apply.
- Candidates are registered as research models; the registry's trial count includes them (MA §36).

---

## 5. Shadow period, comparison and promotion (AD-4)

- **Shadow.** A validated candidate predicts alongside the incumbent on live dates, through the same Route 1 path,
  into `ad_shadow_predictions` (same shape as `r1_predictions`). Shadow predictions are never shown as forecasts and
  never enter Phase 8.
- **Comparison.** After at least the shadow minimum (P11-R3: default 63 sessions and 20 blocks at the horizon's
  block length), Phase 9's machinery compares candidate and incumbent on the shadow rows: the date-level loss
  difference with its bootstrap interval, calibration and coverage, refusal rates. The result is an
  `ad_comparisons` row.
- **Promotion** is Phase 5's owner path (`promote`), with gates 10 to 12. The comparison is cited in the promotion
  note. Phase 10's valid backtest for the candidate is required as for any promotion (gate 10).
- **Retirement** of the incumbent is the owner's act (Phase 5 §9.2); the alert that a promoted successor is serving
  while the incumbent remains designated is reported by `status`.
- **Never automatic.** No code path promotes, designates or retires; the owner path is the only one (the database
  already refuses anything else, Phase 5 §9).

---

## 6. Storage (AD-5)

**Migration `0027_adaptation.sql`** (additive; append-only; waits in `migrations_pending/`):

| Table | Contents |
|---|---|
| `ad_policies` | `monitor.1` and its successors, content-addressed |
| `ad_reports` | Per monitor run: model, window, measurements, policy, `report_hash` |
| `ad_alerts` | A breach: monitor, measurement, threshold, report id; acknowledgement is a later owner row, not an update |
| `ad_shadow_assignments` | Owner-only: which candidate shadows which incumbent from when to when |
| `ad_shadow_predictions` | As `r1_predictions`, with the assignment id |
| `ad_comparisons` | The shadow comparison: metrics, intervals, block counts, the outcome ids read, `report_hash` |

Stamped, append-only and hash-guarded as 0021.

---

## 7. Point-in-time and downtime

- Monitors and comparisons read only rows recorded at their own instant, and outcomes only when resolved; nothing
  they write is read by a model or a feature.
- A missed monitor run is run late with its true instant; a missed shadow date is a missing shadow prediction, never
  back-filled from later data.

---

## 8. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| P11-D1 | Alerts propose, the owner decides; no automatic promotion, designation or retirement | MA §27, §59 |
| P11-D2 | Every threshold is a versioned provisional policy | Thresholds are not known before real data |
| P11-D3 | Weekly monitoring by default | Daily movements are mostly noise at these horizons |
| P11-D4 | Recalibration is a new version, not an edit | MA §58 |
| P11-D5 | A shadow period precedes every promotion of a candidate that would replace an incumbent | The only prospective comparison available |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P11-R1 | The alert channel (shared with P1's open decision) | Unattended operation |
| P11-R2 | A scheduled roll-forward cadence (default proposal: quarterly), or alerts only | The first roll-forward |
| P11-R3 | The shadow minimum | The first comparison |
| P11-R4 | Every promotion and retirement | Each one |

---

## 9. Implementation sequence and tests

| Step | What | Exit |
|---|---|---|
| AD-1 | Pure monitors | Hand-computed PSI, coverage regions, thresholds |
| AD-2 | Monitor job, reports and alerts | No read of unresolved or test-period labels (ADL-1, ADL-2) |
| AD-3 | Candidate builders (roll-forward, recalibration) | A candidate is a new version; the incumbent's rows unchanged |
| AD-4 | Shadow and comparison | Shadow predictions never reach Phase 8 (ADL-3) |
| AD-5 | Migration 0027, store, runbook | PostgreSQL 17; no non-owner path to promotion (ADL-4) |

| ID | Planted | Must hold |
|---|---|---|
| ADL-1 | A label not yet resolved at the monitor's instant | Not read |
| ADL-2 | A date inside an unspent test period | Excluded from the model's monitors |
| ADL-3 | A shadow prediction | Never in `fc_forecasts` |
| ADL-4 | A worker attempt to designate or retire | Refused by the database |
| ADL-5 | A drift breach | An alert only; no model changes |

---

## 10. The fifteen questions (MA §68)

1. Layer: adaptation (monitoring, candidates, shadow). 2. Sources: outcomes, inputs, the registry. 3. At its instant:
resolved outcomes and rows recorded then. 4. Provenance: report and comparison hashes, policy versions. 5. Frozen:
none. 6. Migration: 0027. 7. Leakage: §7, ADL tests. 8. Tests: §9. 9. Reproduction: reports from named rows.
10. Routes: Route 1 models adapted; Route 2 monitored, calibrated in R2's own registry. 11. Evaluation: Phase 9's.
12. Downtime: late runs labelled; no backfilled shadow. 13. Failures: alerts and reports. 14. Versions: every
candidate a version. 15. Reasoning: comparisons show their intervals.

---

## 11. Audit record

None yet.
