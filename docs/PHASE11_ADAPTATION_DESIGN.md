# Phase 11: adaptation (design)

**Status: DESIGN, revision 3 (2026-11-22). Revision 1 NOT ACCEPTED (one Blocker); revision 2 ACCEPTED [delegated]
(C-0115): its re-audit closed the Blocker and found no new one, and four new Majors and three Minors, answered in
revision 3 (§11). Not implemented.** Written by the cloud session on the branch `integration/cloud`
(`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no provider call, no
promotion and no change to `main`.

**Governing records:** Master Architecture (MA) §27, §28, §36, §55 (Phase 11), §58, §59, §61, §68; the Phase 5 design
(§5.3 to §5.6: slices, test periods, the access rule, roll-forward versions; §8: calibration; §9: the registry, gates
and promotion; §10: combinations and designation lists; §11.2: the domain and model age), the Phase 7 design (§7.4:
R2 calibration), the Phase 9 design (outcomes, populations, §8's test-period rule) and the Phase 10 design (valid
backtests, gate 10's schedule). Where this document and those differ, they win.

**Short name:** AD. Steps AD-1 to AD-5 (§9). Decisions P11-D*n* and P11-R*n* (§8).

---

## 1. Conclusion

```
ev_outcomes (Phase 9) + inputs at T ─▶ monitors (drift, calibration, performance, age) ─▶ ad_reports, ad_alerts
        an alert, or the schedule ─▶ a candidate DESIGNATION LIST (every component rolled forward + its combiner)
        ─▶ Phase 5's gates (by reference where the procedure is unchanged) ─▶ shadow designation (owner-made):
           predicts on live dates, never shown ─▶ one comparison, on a fixed window, outside every unspent test period
        ─▶ the owner promotes, or not; the incumbent is retired only by the owner
```

1. **Adaptation is controlled improvement** (MA §27). A bad day changes nothing. Monitors raise alerts; alerts
   propose candidates; candidates go through Phase 5's gates and a shadow period; only the owner designates,
   promotes or retires.
2. **A production model is never modified** (MA §27, §58). A refit is a new registered version (`rollforward_of`);
   the incumbent keeps serving until the owner designates the successor.
3. **One rule for test periods** (Phase 5 §5.4, Phase 9 §8, Phase 10 §6): no training slice, monitor, shadow
   comparison or any other join of predictions to labels reads a label inside a declared, unspent test interval. Such
   a join is an access, and the only access is the protocol's own command.
4. **The unit is what serves.** Route 1 serves designation lists of combinations, gated as a unit, position by
   position (Phase 5 §10). A candidate is therefore a whole designation list, and it is shadowed and compared as one.
5. **The shadow period is the prospective comparison history cannot give**, and it is counted: one comparison per
   shadow on a window fixed in advance, and every shadow is counted in the promotion note (MA §36).

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §27 | Outcomes → evaluation history → monitoring → candidate retraining/recalibration → chronological validation → compare to incumbent → version → controlled deployment; never silently modified | §1 to §5 |
| §28 | Registry entries and statuses | Phase 5's registry, used unchanged |
| §36 | No repeated optimisation against the same test evidence | §1 items 3 and 5, §5 |
| §55 | Drift, retraining, recalibration, candidate validation, promotion/retirement | §3 to §5 |
| §58, §59 | Research/production separation; nothing changed casually | §1 item 2, §5 |

---

## 3. Monitors (AD-1, AD-2), policy `monitor.1`

Each monitor runs on a schedule (weekly by default, P11-D3), over a window of recent dates, and writes a report row;
a breach writes an alert. Every monitor reads only resolved outcomes (Phase 9) and only outside unspent test
intervals (§1 item 3).

| Monitor | Measures | Breach (provisional) |
|---|---|---|
| Feature drift | Per schema feature: the population stability index of its values at recent T against a **reference histogram** (decile bin edges and proportions of the fit slice) stored with each model at registration (AD-3 adds it to the domain record; today's domain stores only the envelope). For a model fitted on a `reconstructed` era, the expected PSI between that era and the first live weeks is recorded first as the baseline | PSI above the recorded baseline by 0.25 on any critical feature, or by 0.1 on a third of the schema |
| Domain pressure | The share of rows refused as `outside_validated_domain` or clipped, against validation's share | Above twice validation's share over 20 sessions |
| Calibration | Route 1's ECE and interval coverage per class, **averaged over dates** (Phase 5 §8.2) with a date-level block-bootstrap interval, against the validated values | The validated coverage outside the bootstrap interval; ECE above validated ECE + 0.05 |
| Performance | The date-level loss difference against `base_prior` on resolved outcomes, with its bootstrap interval. `base_prior`'s live predictions come from the baseline fitted with the model (Phase 5 §7.3) and are stored with each Route 1 record's components | The interval lies entirely above zero (worse than the baseline) |
| Age | Sessions since the model's C_train against `max_model_age_sessions` | A **notice** when the remaining age is less than the lead time L = shadow minimum + h + grace g + a build margin of 21 sessions (about 63 + 21 + 21 + 21 = 126 at h = 21), **plus the sessions of every declared unspent interval ahead** (a shadow cannot run inside one, and no roll-forward row's window may reach into one), so a successor can finish its shadow before the incumbent is blocked (`model_stale`, Phase 5 §11.2) |
| Route 2 | Its `as_stated` calibration and failure rate per configuration | Failure rate above the configuration's validated rate + 10 points |

**Test periods and adaptation (N4).** Test intervals are global. While one runs, Route 1's outcomes are sealed
(Phase 9 §8), so the performance and calibration monitors see nothing (`status` reports `monitor blind: sealed`
with the interval), no shadow can run, and no roll-forward can use rows whose windows reach into it. A long
prospective interval (at h = 21, a sufficient one is at least about 420 usable dates) can therefore outlast the serving
list's age limit. So **when the owner declares a protocol**, its declaration reports, for every serving list, whether
a successor can be shadowed and promoted before that list's age limit given the interval; if not, the declaration
says so, and the owner either shortens or moves the interval, or records the expected `model_stale` outage as
accepted (P11-R5).

Monitors report through `status` and Phase 12's health page. The alert channel is one open decision shared with P1
and LP-R3 (P11-R1).

---

## 4. Candidates (AD-3)

| Kind | When | What it is | Validation |
|---|---|---|---|
| Roll-forward list | Scheduled (P11-R2) or on an age, drift or performance alert | The incumbent designation list with **every component** fitted again with a later C_train by the same procedure, and **each combination's combiner** fitted again on the components' out-of-fold predictions (Phase 5 §10) | Phase 5 §5.6 per component: gates 2 and 4 to 9 by reference to the configuration's report and test; gates 1 and 3 itself; MLK-11; its own domain; the list's positions validated as Phase 5 §10 does |
| Recalibration | On a calibration alert | **Not a roll-forward.** Refitting calibration on later slices is a different procedure from the one the walk-forward measured (the calibration belongs to its fit, Phase 5 §5.3). It is a **configuration variant** with its own evidence: its own protocol, or a Phase 5 revision that defines and measures the procedure. Until then, a calibration alert proposes a roll-forward list instead | Its own protocol |
| New configuration | Never automatic | A research change | A new protocol under Phase 5 (a new test period, a new access) |

- **Every candidate builder obeys Phase 5 §5.4, by label window**: no training, calibration or conformal row whose T
  **or whose label window (T, R_h] extended by the grace g** meets another protocol's declared, unspent interval. The
  registry's `check_rows_outside_unspent` tests T only; the builders apply the window check themselves (and the
  registry function gains it in the same Phase 5 amendment). Tested with a row at P_start − 1 (ADL-6). A roll-forward
  whose C_train would need such rows uses the latest C_train whose windows end before the interval.
- A candidate's slices follow Phase 5 §5.3 with C_train at the candidate's cutoff; the embargo and grace apply. Model
  age is counted from each component's own C_train.
- Candidates are registered as research models; the registry's trial count includes them (MA §36).

---

## 5. Shadow period, comparison and promotion (AD-4)

- **The shadow assignment is the shadow's designation.** The owner records an `ad_shadow_assignments` row: the
  candidate list, the incumbent list it shadows, the comparison window [start, end] fixed in advance, the purpose,
  and (N7) the comparison's provisional criterion. It is **not** an `ml_designations` row: Route 1's shadow run takes
  its list from the assignment, and its records go only to `ad_shadow_predictions`, keyed by the assignment id in
  place of a designation id. Shadow predictions are never shown as forecasts and never enter Phase 8. Nothing in
  `ml_designations` changes, so `designation_in_force` never sees a shadow.
- **A registry check this needs** (a Phase 5 registry amendment, carried before `main`, branch review): today the
  check that a designation lists only production models of its output and horizon runs only in a transaction with a
  `production` event, so the owner could insert a designation of research models with no check. 0021 gains a
  deferred constraint trigger on `ml_designations` that applies the same check in **every** transaction (ADL-9).
- **Window and test periods.** The comparison window may not intersect any declared, unspent test interval,
  extended by the horizon and grace (Phase 10 §6's rule): the assignment is refused otherwise. The check is repeated
  when the comparison runs: if an interval declared after the assignment now meets the window, the comparison is
  recorded `void` (not spent, nothing read) and the owner may make a new assignment after the interval (N5). The
  window starts at or after the candidate's latest C_train + embargo. In particular, a
  **new configuration** is never shadowed during its own prospective test period; it is shadowed only after its
  protocol's access, and its test-period evidence comes only through the access command (Phase 5 §5.4).
- **Comparison, once.** When the window's labels have resolved, the comparison runs **once** per assignment: both
  lists on the same rows, per position (Phase 5 §10, MLK-21), the date-level loss difference with its bootstrap
  interval, calibration and coverage, refusal rates, through Phase 9's outcome machinery with population `shadow`
  (Phase 9 §7: subject kind `ad_shadow_prediction`, added to the subject check by 0027). A window shorter than the
  shadow minimum (P11-R3) is refused at assignment; no comparison is run before the window ends.
- **Counting.** Every shadow assignment for an incumbent is counted, overlapping shadows on the same dates are
  allowed only when all of them are reported together, and the promotion note cites the count (as it cites the
  lifetime test accesses, Phase 5 §9.3 gate 12).
- **Gate 10 for a roll-forward.** A roll-forward has no V_end of its own. Its backtest (Phase 10) is the
  configuration's walk-forward backtest (Phase 10's gate-10 schedule), plus a post-C_train backtest over the shadow
  window, labelled `rollforward_shadow`. Both are cited. This adds a schedule to Phase 10 §4.2 (done there) and a
  reading of Phase 5's gate 10 (the Phase 5 amendment below) (N6).
- **The criterion** (N7). The assignment states a provisional criterion fixed in advance: the candidate list's
  date-level loss difference against the incumbent's has an upper bootstrap bound below +δ (δ = 0 by default: not
  worse), coverage within tolerance per class, and refusal rates not above the incumbent's by more than 5 points.
  The comparison reports whether it is met. The owner decides, and a promotion against an unmet criterion states why.
- **Promotion needs a registry change** (N2), part of the same Phase 5 amendment, because `promote` as built cannot
  promote a list:
  - **gate 9 by reference**: for a roll-forward, gate 9 is met when the version it is `rollforward_of` (followed back
    to the tested final fit) passed its protocol's access as a nominee (Phase 5 §5.6 says gate 9 is inherited; the
    built `promotion_evidence` looks for this exact model id among the nominees and would refuse every roll-forward);
  - **a list-promotion owner command** that promotes every new version of the list and writes the one designation of
    the whole list in a single owner transaction, so no mixed old/new list is ever in force;
  - **what is promoted**: the combinations the list names are promoted and designated; their components are promoted
    (production versions, so the production record names production artifacts) but never designated on their own.
  The comparison and the shadow count are cited in the note.
- **Retirement.** The promotion's designation replaces the incumbent list; the incumbent's models are then
  undesignated but still `production`, which `status` reports until the owner retires them (Phase 5 §9.2), optionally
  in the same owner transaction.
- **Never automatic.** No code path promotes, designates or retires; the owner path is the only one (the database
  already refuses anything else, Phase 5 §9).
- **Route 2 calibration.** A validated `r2_calibrations` map is put into use only by an owner designation (as
  P7-R2b), never automatically.

---

## 6. Storage (AD-5)

**Migration `0027_adaptation.sql`** (additive; append-only; waits in `migrations_pending/`):

| Table | Contents |
|---|---|
| `ad_policies` | `monitor.1` and its successors, content-addressed |
| `ad_reports` | Per monitor run: model or list, window, measurements, policy, `report_hash` |
| `ad_alerts` | A breach: monitor, measurement, threshold, report id; acknowledgement is a later owner row, not an update |
| `ad_shadow_assignments` | Owner-only: candidate and incumbent lists, purpose, the fixed window, the shadow designation; refused when the window meets an unspent test interval |
| `ad_shadow_predictions` | As `r1_predictions`, with the assignment id |
| `ad_comparisons` | One per assignment (unique): metrics per position, intervals, block counts, the outcome ids read, the count of shadows for the incumbent, `report_hash` |

Stamped, append-only and hash-guarded as 0021. 0027 extends Phase 9's `ev_outcomes` subject check with the
`ad_shadow_prediction` kind (no ALTER of the table). The tables hold CSE-derived values and fall under G-1 §6's
owner-controlled purge.

---

## 7. Point-in-time and downtime

- Monitors and comparisons read only rows recorded at their own instant, only resolved outcomes, and never a label
  inside an unspent test interval; nothing they write is read by a model or a feature.
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
| P11-D4 | Recalibration is a configuration variant with its own evidence, not a roll-forward | The walk-forward never measured it |
| P11-D5 | A shadow period precedes every promotion of a candidate that would replace an incumbent; the shadow window never meets an unspent test interval; one comparison per shadow, counted | The only prospective comparison available, without spending a test period or repeating a test |
| P11-D6 | The candidate is a whole designation list | What serves is a list (Phase 5 §10) |
| P11-D7 | The age notice comes one lead time L, including unspent intervals ahead, before the limit | So the incumbent is not blocked mid-shadow, except by an outage the owner accepted at declaration |
| P11-D8 | The shadow's designation is its assignment, never an `ml_designations` row | Shadows can never serve |
| P11-D9 | The registry amendments of §4 and §5 (designation check in every transaction, label-window check, gate 9 by reference, list promotion) are made before Phase 11 is built | `promote` as built cannot promote a list |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P11-R1 | The alert channel (shared with P1's open decision and LP-R3) | Unattended operation |
| P11-R2 | A scheduled roll-forward cadence, or alerts only; the default proposal is derived from L (at most `max_model_age_sessions` − L between roll-forwards) | The first roll-forward |
| P11-R3 | The shadow minimum (default 63 sessions and 20 blocks at the horizon's block length) | The first assignment |
| P11-R4 | Every shadow assignment, promotion and retirement | Each one |
| P11-R5 | Accepting a `model_stale` outage caused by a declared test interval | The declaration |

---

## 9. Implementation sequence and tests

| Step | What | Exit |
|---|---|---|
| AD-1 | Pure monitors | Hand-computed PSI, date-averaged coverage intervals, thresholds |
| AD-2 | Monitor job, reports and alerts | ADL-1, ADL-2 |
| AD-3 | Candidate list builders; the reference histogram in the domain record | A candidate list is new versions; the incumbent's rows unchanged; ADL-6 |
| AD-4 | Shadow designation and the single comparison | ADL-3, ADL-7, ADL-8 |
| AD-5 | Migration 0027, store, runbook | PostgreSQL 17; no non-owner path to promotion (ADL-4) |

| ID | Planted | Must hold |
|---|---|---|
| ADL-1 | A label not yet resolved at the monitor's instant | Not read |
| ADL-2 | A date inside an unspent test period | Excluded from every monitor |
| ADL-3 | A shadow prediction | Never in `fc_forecasts` |
| ADL-4 | A worker attempt to designate, assign a shadow or retire | Refused by the database |
| ADL-5 | A drift breach | An alert only; no model changes |
| ADL-6 | A roll-forward whose slices would reach into another protocol's unspent interval | Refused (MLK-5) |
| ADL-7 | A shadow window meeting a prospective test period | Assignment refused |
| ADL-8 | A second comparison of one assignment, or one before the window ends | Refused |
| ADL-9 | An owner designation listing research models under the live purpose, with no production event | Refused at commit |
| ADL-10 | A roll-forward that is not a nominee; a list of three combinations | Gate 9 by reference passes; one transaction, one designation |
| ADL-11 | An interval declared after an assignment, meeting its window | The comparison `void`, unspent |

---

## 10. The fifteen questions (MA §68)

1. Layer: adaptation (monitoring, candidates, shadow). 2. Sources: outcomes, inputs, the registry. 3. At its instant:
resolved outcomes outside unspent test intervals, and rows recorded then. 4. Provenance: report and comparison hashes,
policy versions, the shadow count. 5. Frozen: none. 6. Migration: 0027. 7. Leakage: §1 item 3, §7, ADL tests.
8. Tests: §9. 9. Reproduction: reports from named rows. 10. Routes: Route 1 lists adapted; Route 2 monitored, its
calibration designated by the owner in R2's own registry. 11. Evaluation: Phase 9's, population `shadow`.
12. Downtime: late runs labelled; no backfilled shadow. 13. Failures: alerts and reports. 14. Versions: every
candidate a version. 15. Reasoning: comparisons show their intervals.

---

## 11. Audit record

### Audit 1 (revision 1; fresh-context agent, 2026-11-22): NOT ACCEPTED

One Blocker and seven Majors, all answered in revision 2:

| ID | Finding | Revision 2 |
|---|---|---|
| P11-B1 (Blocker) | A shadow comparison could join predictions to labels inside an unspent test period: an unrecorded access | §1 item 3; windows refused when they meet an unspent interval; a new configuration shadowed only after its access (§5); ADL-7 |
| P11-M1 | Roll-forwards could train inside another protocol's unspent interval | Every builder obeys MLK-5 (§4); ADL-6 |
| P11-M2 | Recalibration inherited evidence it does not have | A configuration variant with its own evidence (§4, P11-D4) |
| P11-M3 | A candidate was a single model; Route 1 serves lists | Candidate lists, shadow designation, per-position comparison (§4, §5) |
| P11-M4 | Phase 9 could not store shadow outcomes | Population `shadow`, subject kind added by 0027 (§5, §6; Phase 9 §7) |
| P11-M5 | Optional stopping and uncounted shadows | One comparison on a fixed window; every shadow counted (§5) |
| P11-M6 | The age notice came too late | Lead time L (§3, P11-D7, P11-R2) |
| P11-M7 | Gate 10 undefined for roll-forwards | The configuration's backtest plus a post-C_train shadow backtest (§5) |

Minors fixed: a reference histogram stored with each model and an era baseline for PSI; date-averaged coverage with
a bootstrap; where `base_prior`'s live predictions come from; the retirement wording; Route 2 calibration by owner
designation.

### Audit 2 (revision 2; fresh-context agent, 2026-11-22): ACCEPT

The Blocker and the seven Majors are closed (M1 and M3 partly, completed here). Four new Majors, answered in revision 3:

| ID | Finding | Revision 3 |
|---|---|---|
| N1 | A "shadow designation" had no home; the registry admits an owner designation of research models | The assignment is the shadow's designation; a designation check in every transaction (§5, ADL-9) |
| N2 | `promote` as built cannot promote a roll-forward list | Gate 9 by reference, a list-promotion command, what is promoted (§5, ADL-10) |
| N3 | The unspent-interval check tested T, not the label window | Window plus grace (§4, ADL-6) |
| N4 | A long test interval can freeze adaptation until the list goes stale | The interaction stated; L counts intervals; the declaration reports it; P11-R5 (§3) |

Minors fixed: a void comparison when a later interval meets the window (N5); the `rollforward_shadow` schedule added
to Phase 10 and the window after C_train + embargo (N6); a provisional criterion fixed at assignment (N7).
