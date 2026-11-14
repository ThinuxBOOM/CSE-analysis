# Phase 5: ML and the Route 1 combination (design)

**Status: DESIGN, revision 4 (2026-11-14). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact, no training on real data and no change to `main`.

*Revision 4 replaces revision 3 (commit `36322ae`). It applies the six findings of the review recorded on the
research branch (`research/tradeiq-phase3-validation-2026-11-12`, P5-A01 to P5-A06; §19) and three owner decisions:
Route 1 may be ML-only (C-0036), validation on `reconstructed` data is waived (C-0039), and historical prices come from
the TradeIQ archive (C-0035). The main changes: the label state `unresolvable_missing_price` is scored (§6); the
upstream designs are pinned at Phase 3 revision 5 and Phase 4 revision 6, with a compatibility statement (§18.1);
`calibrated_engine` is an engine calibration, not an ML adapter (§7.2); the inherited leakage tests are brought up to
date (MLK-20); the uncertainty claims are narrowed to what is shown, and a simulation test of them at realistic sizes
is added (§8.2, §12.3, MLK-23).*

*Revision 3 replaced revision 2 (commit `5c67269`). The second audit found no Blocker and confirmed that the split
rules of §5.2 and §5.3 are leak-free as written, then failed the design on 22 Major findings in what surrounds the
splits (§19). The main changes: test periods of different protocols cannot overlap, and the count of things tried is
kept across protocols (§5.4); the gated unit is a **configuration**, and the tested unit is its final fit, with the
order of events fixed (§5.6); the gate's statistical test is specified (§9.3); the volatility output, the interval
scale and the baselines are defined (§6 to §8); a designation list is evaluated as it will be used (§10); and the
sufficiency arithmetic is redone in usable dates, which lengthens the honest timescale considerably (§5.5).*

**Governing records:** Master Architecture (MA) §2, §4, §11 to §14, §21 to §23, §25, §27, §28, §32 to §37, §43.1, §55
(Phase 5), §56 to §62, §68; `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, **revision 5**, accepted at `6a034ea` ("MF":
features, universe, labels); `docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md`, **revision 6**, accepted at `62f77fc`
(financial features "F7", engines and baselines "DE"). What this design takes from each is listed in §18.1.
Where this document and those differ, they win.

**Identifiers.** Steps are M5-1 to M5-8 (§15). Tests are MLK-*n* (§13). Decisions are P5-D*n* ([delegated]) and
P5-R*n* (reserved) (§14). Phase 3's leakage tests are "MF's ML-1 to ML-24" here.

---

## 1. Conclusion

```
MF snapshot rows + F7 feature rows + DE outputs ─▶ dataset builder ─▶ versioned dataset (features | labels, apart)
                                              │
                        chronological protocol: bounded, purged walk-forward; one declared, disjoint test period
                                              │
     configurations ─▶ walk-forward evidence ─▶ gates ─▶ nomination ─▶ final fit ─▶ one test access ─▶ promotion
                                              │
              Route 1 designation lists of combinations (gated as they are used) ─▶ Route 1 predictions
```

1. **The hard part of this phase is the protocol, not the algorithm.** MA §12.2 mandates no algorithm. It mandates
   chronological validation, an isolated final test period, a registry and no leakage.
2. **Three time rules close label leakage** (§5.2, §5.3), and the audit confirmed them: a training row's label had
   resolved by the training cutoff; a validation or test row's label resolves inside its own window; everything
   fitted or chosen for a fold uses training rows only, in ordered, purged slices.
3. **What is tried is counted, across protocols.** Choosing among configurations by their validation results is
   selection. The design does not pretend otherwise: the number of hypotheses is fixed at declaration, the test
   corrects for it, and sessions already used as someone's validation or test evidence are tracked (§5.4, §9.3).
4. **The software can stop sanctioned misuse, not a person who looks.** §5.4 states that limit.
5. **Nothing here can be validated yet.** No model leaves `candidate` on synthetic data.
6. **The honest timescale is long.** By the provisional minimums of §5.5, the first protocol that could gate a
   one-day-horizon output needs roughly 1 600 sessions and a one-month-horizon output roughly 2 600: about seven and
   eleven years of trading days. Forward capture alone cannot support Phase 5 on any useful timescale. The source of
   market history (Phase 3, P3-R1) is the decision this phase depends on, and the minimums themselves are the owner's
   to set for a real protocol (P5-R2).
7. **ML dependencies stay out of the frozen build**, and artifacts live in PostgreSQL so P1's backups cover them.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Status and where |
|---|---|---|
| Every model has ID, version, training dataset, feature schema, training cutoff, validation period, test period, hyperparameters, artifact hash, metrics, creation timestamp, deployment status | §12.2, §28, §61 | Met: §9.1 |
| Statuses `candidate`, `validated`, `production`, `retired`, `rejected` | §28 | Met: §9.2 |
| A candidate passes its gates and receives a new immutable version; research never silently becomes production | §58, §27, §59 | Met: §9.2 |
| Route 1 is deterministic engines plus ML; the combination is versioned and keeps component outputs | §12, §13 | Met in mechanism: §10. A combination contains whichever components passed their gates and may contain no engine-derived one: **the owner decided this is allowed (C-0036, 2026-11-14).** Such a prediction is flagged `no_engine_component`, shown with that flag wherever it is displayed, and the security's full engine outputs are stored with every prediction regardless (§11.1). `calibrated_engine` (§7.2) is the engine-derived component |
| Reasoning from feature contributions | §14 | Met: §8.3 |
| The prediction contract for every horizon, including volatility | §4 | Met: §6 |
| Confidence statistically grounded | §21 | Met: §8 |
| Refuse to forecast: outside validated domain, missing critical features | §22 | Met: §11.2 |
| Chronological walk-forward | §32, §11 | Met: §5 |
| Historical universe, survivorship, selection bias | §33 to §35 | Met, with labels carried: §4.3 |
| Final test period isolated; no repeated optimisation against it | §36 | Met on the sanctioned path, with the stated limit: §5.4 |
| Metrics by horizon, security, **sector**, liquidity class, model version, regime, period | §25, §37 | Met except **sector** (no point-in-time source; Phase 3, P3-R5) and, of §37's regimes, "market stress" and "sector-specific events", which are not built. Recorded deviations (P5-R4) |
| Compare a candidate with the incumbent before deployment | §27 | Not in this phase: Phase 11 owns it. Promotion here has no incumbent comparison |
| Leakage tests | §11, §57 | Met: §13 |

---

## 3. Boundary

**ML reads:** `mf_snapshot_rows`, `mf_target_rows` (Phase 3), `f7_feature_rows`, `de_outputs` (Phase 4), and its own
tables. No raw observation, economic fact or F8 result directly.

**ML never** contacts a network, reads a mutable table, trains inside a prediction call, or modifies a frozen
component.

**Not owned:** features and labels (Phases 3, 4), Route 2 (Phase 7), the user-facing forecast (Phase 8), live outcome
evaluation (Phase 9), the laboratory's replay loop (Phase 10), drift, retraining policy and the incumbent comparison
(Phase 11).

---

## 4. The dataset

### 4.1 A row

One row per (security, reference session D), where the cutoff rule of the manifest picks **one** snapshot per session
(the last one computed at or before the rule's instant on that day), so no session appears twice. T is that
snapshot's cutoff.

- **Rows are equity lines that are market-data eligible at T.** Others are counted and left out.
- **Features:** the MF feature vector; the F7 feature vector of the issuer linked at T; both eligibility results; DE
  outputs. Each with its quality state and source row hash.
- **Labels:** MF's label rows for (reference snapshot row, h), h ∈ {1, 5, 10, 21}, from **one pinned target build**:
  state, values, `resolved_at`, row hash. A dataset never mixes builds.
- **Row metadata:** label class (`point_in_time`, `reconstructed`); survivorship; `fundamental_coverage`; liquidity
  class (terciles of `turnover_avg_21` in the same-T universe); regime tags (§12.2); industry group.

**The two clocks of a row.** T and each label's resolution instant R_h are on the **admission clock of the row's
era**: `recorded_at` in a `KNOWN` era; `available_at` in an `AVAILABLE` era, with H pinned in the manifest. (Taking
`recorded_at` for imported history would make every imported label "resolve" at the import instant.) R_h is the
label's `resolved_at`, which Phase 3 §11 defines over every input including corporate actions; for a label
whose inputs span two eras, it is the latest of their admission instants, each on its own era's clock.

Features and labels are separate files, and training code receives labels only through the split object of §5.
**Labels for rows inside a declared test period are not built into any dataset** until the access command runs.

### 4.2 The manifest and the version

A manifest names: the MF, F7 and DE configuration ids; the date range; the cutoff rule; the mode per era with H; the
MF target-build id; the row filter; the label definition version. The **dataset version** is the SHA-256 of the
canonical manifest plus the ordered source row hashes: a hash over logical content.

Datasets are rebuildable cache files outside the repository and Git, checked against their version. The database
holds the manifest and the version.

### 4.3 Honest labelling

- A `reconstructed` dataset is a reconstruction; every metric on it says so.
- A captured era is `complete` for its final sessions; an imported era carries its batch's label; a dataset carries
  the worst of its eras'.
- **Nothing is dropped silently.** The build report counts, by reason and by label state: rows excluded by the
  filter; rows whose labels fall outside every window of the protocol (§5.2); and the rows that straddle a slice
  boundary and are used by no slice (§5.3).

### 4.4 Missing values

Phases 3 and 4 never impute. The admissible quality states per feature are part of a configuration's feature schema
(default `fresh`, `stale`, `partial`); a value outside them is null. Nulls go to models that handle them natively,
with a missing indicator; otherwise an imputation fitted on the fit slice is part of the artifact. A null in a
feature the schema marks `critical` means no prediction.

---

## 5. The chronological protocol

### 5.1 Time order

Every row has T and, per horizon, R_h > T (§4.1). All splitting is on these instants, per horizon.

### 5.2 The outer walk-forward: bounded and purged

A fold has C_train < V_start ≤ V_end. Let g = h + the label grace period (Phase 3's provisional 21 sessions), the
longest any label of horizon h can take to reach a terminal state.

| Rows | Condition |
|---|---|
| Training | R_h ≤ C_train |
| Validation | V_start ≤ T ≤ V_end − g sessions, **and** R_h ≤ V_end |
| Embargo | V_start is at least `embargo_sessions` after C_train (default 1), for the uncertainty of assumed availability times in `reconstructed` eras |

- **Why the validation rows stop g sessions before V_end.** Labels do not all resolve at the same speed. A label of
  a security that leaves the market resolves only after the grace period; one touched by a late corporate action
  resolves later still. If rows ran to V_end, exactly those labels would fall out of every fold's tail, and the
  counts of delistings would be understated by about a third in a 63-session fold. Stopping g sessions early gives
  every label of every row time to reach its state, so the scored population is the same in kind across states.
- Folds expand: the next fold's C_train is this fold's V_end. Rows with V_end − g < T ≤ V_end are in no fold's
  validation set; they become training rows of a later fold. They are counted (§4.3). No row is validated twice and
  no validation label reaches past its window.
- The purge is per horizon, which is coherent with one configuration per (output, horizon); tables that put horizons
  side by side state each one's date set.

### 5.3 Inside the training rows: ordered slices

Everything fitted or chosen for a fold uses its training rows only, cut by S1 < S2 < S3 < C_train:

| Slice | Rows | Used for |
|---|---|---|
| Fit | R_h ≤ S1 | Parameters, imputation, scaling, clipping bounds, the envelope |
| Select | T > S1 and R_h ≤ S2 | Hyperparameter choice and early stopping (both are selection; neither score is ever reported as evidence) |
| Calibrate | T > S2 and R_h ≤ S3 | The calibration map, the volatility retransformation |
| Conformal | T > S3 and R_h ≤ C_train | Interval residuals |

- Each boundary has the outer split's purge and embargo, so no label window straddles one. The rows that do straddle
  are unused and counted.
- **A fold's model is fitted on the fit slice and not refitted afterwards**, because the calibration map and
  residuals would then belong to a different model. **The cost:** the parameters stop at S1. At the one-month
  horizon the three later slices span well over a year, so a model is that old on the day it is first used.
  §5.6 says how its age is evaluated and bounded.
- Baselines need no later slices: `base_prior` and `base_interval` are fitted on **all** training rows of the fold,
  so the reference is not handicapped.

**The validation fold fits and chooses nothing.** Its metrics are evidence about a configuration. Comparing
configurations by that evidence, and nominating one, is selection all the same; §9.3 corrects for it.

### 5.4 The final test period

**Declaration.** A protocol row, written before any training, fixes: the dataset manifest rule; every configuration
and policy id it uses; the fold schedule and slice sizes; **the family** (the list of configurations and combinations
it may take to validation, per (output, horizon): its size m is the multiplicity §9.3 corrects for, and nothing
outside the list can be gated under this protocol); one primary (output, horizon); the nomination rule; the test
criterion; and the test period [P_start, P_end].

**Rules the software enforces.**

- **Test periods are pairwise disjoint.** A declared interval may not overlap any interval ever declared, spent or
  not. One interval, one protocol, one access. (Revision 2 allowed many protocols to declare the same interval, which
  made "one access per protocol" best-of-many again.)
- P_start is at least `embargo_sessions` after the last V_end. Test rows have P_start ≤ T ≤ P_end − g and
  R_h ≤ P_end.
- **No protocol**, `exploratory` ones included, takes training or validation rows from another protocol's declared,
  unspent interval.
- **The count of what was tried follows the sessions, not the protocol.** For each (output, horizon), the registry
  counts every configuration of every protocol that has validation metrics on sessions overlapping this protocol's
  validation sessions. If that count exceeds the declared family's, the larger number is the multiplicity. A
  researcher cannot explore widely under one protocol and then declare a fresh one with a single winner.
- **Policies are evidence too.** An eligibility policy, tolerance, effect size or domain margin used by a protocol
  must record an evidence cutoff before the protocol's **first V_start**; otherwise choosing it is counted as
  configurations tried.
- **One access.** The access command checks that the interval is declared by this protocol and unspent; that the
  nominees' final fits exist and their test-period predictions are frozen and hashed; then writes the access row,
  builds the test labels for the first time, computes the metrics and evaluates the criterion. The interval is then
  spent.
- **The test criterion has a minimum form:** the test of §9.3 against every baseline, on the test rows, at the
  protocol's level, with the same refusal logic, **corrected across the protocol's nominees** (Bonferroni over their
  number). A weaker criterion cannot be declared.
- Anything that joins predictions to labels in the test period is an access. A laboratory backtest of a candidate
  ends at the last V_end; one that covers the test period runs only after the access and is labelled post-access.
- A test period is `prospective` if P_start is after the declaration's database time, otherwise `retrospective`; and
  `previously_scored` if any earlier dataset in the registry built labels inside it. Both labels are carried into
  every report and must be cited in the promotion (§9.3).
- Every promotion reports the lifetime number of test accesses in the registry. With imported history, consecutive
  retrospective intervals can be declared without end; the count makes that visible.

**The limit, stated plainly.** These rules bind the sanctioned code path and are recorded append-only. Prices and
labels for a test period also exist in the database, where a reader can query them, and the worker role that writes
gate reports is the same role the triggers constrain. No software rule stops a person from looking and letting that
shape a design.

### 5.5 Data sufficiency

A protocol is `sufficient` for horizon h only if it meets these minimums (provisional, P5-D6). The unit is **dates
with usable rows**; a window's span is longer by the purge at its edges.

| Quantity | Minimum usable dates | Span |
|---|---|---|
| The first fold's fit slice | 504 | 504 |
| Each of select, calibrate, conformal | max(42, 6·h) | + h |
| Each validation fold | max(63, 8·h) | + h + 21 (the grace period) |
| Folds | 5 (a combination needs one more fold than its components, and every Route 1 output is a combination) | |
| The test period | max(126, 8·h) | + h + 21 |
| Distinct securities with rows, per fold and in the test period | 50 | |

A protocol below them can run, labelled `exploratory`; nothing under it can pass a gate.

**What this implies.** Rows with complete market-context features start about 380 sessions after capture begins
(Phase 3 §9). Then:

| | h = 1 | h = 21 |
|---|---|---|
| Warm-up before complete rows | 380 | 380 |
| Fit slice | 504 | 504 |
| Three inner slices | 3 × 43 = 129 | 3 × 147 = 441 |
| Five validation folds | 5 × 85 = 425 | 5 × 210 = 1 050 |
| Test period | 148 | 210 |
| **Total sessions** | **about 1 590** | **about 2 590** |

At roughly 240 sessions a year (an assumption about CSE's calendar) that is about seven and eleven years. Even then
the one-month folds hold eight non-overlapping label windows each, which is thin evidence. The levers are the owner's
(P5-R2): fewer folds, a shorter first fit, or accepting `exploratory` status for the long horizons. None changes the
conclusion that forward capture alone cannot support this phase.

### 5.6 What is gated, what is tested, and in what order

- A **configuration** is the gated unit: an adapter, a feature schema, a hyperparameter search space, the slice rule
  and the seed rule, for one (output, horizon). It is a procedure for producing a model from training rows.
- **Fold models** are what the procedure produces in each fold. Their artifacts' hashes and their frozen validation
  predictions' hashes are recorded.
- The **final fit** is the same procedure run once more with C_train = the last V_end and its own inner slices: it
  searches hyperparameters again exactly as each fold did. It is the artifact that is tested and can be promoted.

**The order is fixed:** declaration → walk-forward of every configuration in the family → gates 1 to 8 on each
(evidence about the **configuration**) → nomination by the declared rule, among configurations that passed → the
final fit of each nominee → its test-period predictions frozen and hashed → the one access (evidence about the
**artifact**) → promotion.

**Model age.** A model is evaluated at the ages its validation and test rows have: from zero to about the fold
length after its C_train. It can only be promoted after its test period has resolved, so on its first production day
it is already older than most of what was evaluated.

- Every metric is also reported by **age stratum** (sessions since C_train).
- The domain policy's `max_model_age_sessions` may not exceed the largest age with evaluated rows in the walk-forward
  and the test. A protocol whose fold and test lengths do not cover the intended service age cannot support it.
- **Roll-forward refits.** A tested configuration may be fitted again later, with a later C_train and the same
  procedure, as a version marked `rollforward_of`. Its evidence is the configuration's walk-forward and test, which
  measured the procedure, not this artifact; the registry says so on the version, and the owner's promotion of a
  roll-forward cites it. Without this, every production model would be stale by construction.

---

## 6. Prediction targets and the scoring population

Per horizon h there are three outputs:

| Output | Label (Phase 3 §11) | What is predicted | Primary loss |
|---|---|---|---|
| `direction` | `rise`, `flat`, `fall` | Three probabilities summing to 1 | Multi-class log loss |
| `return` | The return | Its mean, with a predictive interval (§8.2) | Mean squared error for the point; coverage and width for the interval |
| `volatility` | Realised volatility for h ≥ 5; `abs_ret_1` for h = 1 | Its **mean**, in level units, never negative | Mean squared error |

The volatility label is exactly zero whenever the security did not trade through the window. That is a real outcome
and part of the mean. Models may fit a transformed target, but the registered output is the mean in level units: a
model that fits the logarithm applies a retransformation factor fitted on the calibrate slice, and its floor is a
constant of the configuration. Intervals are produced for `return` only.

**The scoring policy** (`scoring.1`, versioned, in the metrics library, used by Phases 5, 9 and 10; Phase 3 defines
the label states and this policy decides how they are scored):

| Label state | Training | Headline metrics | Also reported |
|---|---|---|---|
| `resolved`, `resolved_carried` | Yes | Yes | `resolved` alone, as a secondary stratum |
| `terminated` | No in v1: it has no agreed return | No | Its count; and a **pessimistic sensitivity** in which each such label counts as a total loss (return −1, class `fall`) |
| `unexplained_move_in_horizon` | No | No | Its count; and a sensitivity that includes it at its raw return. Leaving these out removes the largest moves that have no entered action, which flatters return error, volatility error and interval coverage |
| `unresolvable_missing_price` | No | No | Its count, by horizon, period and liquidity class. **No sensitivity:** the label has no value, and inventing one (a return, a class) would be fabrication. Counted separately from the next row so that a rise in missing prices is visible |
| `unresolvable_quality`, `unknown_session_in_horizon`, `pending` | No | No | Counts |

- Including `resolved_carried` is deliberate: whether a security will trade on the target session is unknown at T,
  so excluding those rows would condition on the future. Their return is zero at the one-session horizon and whatever
  the last trade gave at longer ones.
- **CSE's carried close.** From 2015 CSE's official close stays at the previous close on a day too little trades,
  although the security traded (`docs/research/TRADEIQ_VALIDATION.md`, F11). The labels use CSE's close as Phase 3
  defines it, so such a day contributes a zero return and lowers the volatility label. This is CSE's definition, not
  an error; the share of target sessions whose close equals the previous close is reported per stratum.
- Excluding `terminated` and `unexplained_move_in_horizon` from the headline is the same kind of conditioning, done
  for want of an agreed value. The two sensitivities bound what it hides, and every report shows them.

**Derived fields.** Predicted direction is the most probable class. Predicted price is
`reference_close × (1 + expected return)`, on the share basis of the reference close's own session, shown with its
`stale_sessions`. If the direction's most probable class and the sign of the expected return disagree, both are kept
and the row is flagged.

---

## 7. Adapters and baselines

### 7.1 The interface

```
fit(fold_slices, hyperparameters, seed) -> artifact
predict(artifact, features) -> predictions with per-row contributions
describe(artifact) -> feature schema, clipping bounds, envelope, hyperparameters, environment tuple
```

- Serialisation is canonical per adapter (a text form defined by this project, never a language-level object
  pickle); the artifact's SHA-256 is its identity. The environment tuple records library versions, the CPU feature
  set, the thread count (1) and the seed.
- **Claimed:** the same artifact bytes in the recorded environment give the same predictions after quantisation to 9
  decimal places; retraining in the same environment on the same machine reproduces the artifact hash (MLK-11).
  **Not claimed:** identical bytes across hosts. That is measured at staging.

### 7.2 Adapters in v1

| Adapter | Outputs | How | Contributions |
|---|---|---|---|
| `ref` | Those each baseline fills | §7.3 | — |
| `calibrated_engine` | `direction`, `return` | One Phase 4 engine's score s, on rows where the engine used its full component set: a multinomial logistic map on (s, s²); a linear map on s. Fitted on the fit slice; it has nothing to select, so its select slice is unused | The engine's components |
| `linear` | All three | Regularised multinomial logistic regression; ridge regression on return and on volatility | Coefficient × standardised value |
| `gbt` | All three | Gradient-boosted trees | The library's exact tree contribution values |

- **Two kinds of component (P5-D3, revision 4).** `linear` and `gbt` are the ML adapters: they take MF and F7
  features and never an engine score. `calibrated_engine` is not an ML adapter: it is a **calibration of one Phase 4
  engine**, the one component whose input is an engine score. It is registered with kind `calibrated_engine`, its
  provenance names the engine and the engine's version, its contributions are the engine's own components, and in
  §10 it is the engine-derived component. A combination that contains no `calibrated_engine` is flagged
  `no_engine_component`.
- For `linear` and `gbt` the "class scores" given to calibration are the model's log-probabilities.
- Libraries are pinned at M5-4 in `requirements-ml.txt`; the capture, backfill and financial-truth packages import
  none of them (MLK-14).
- No sample weighting, class weighting or time decay in v1 (§16).

### 7.3 Baselines

| Baseline | Scored on | Definition |
|---|---|---|
| `base_no_change` | `return` (0); `direction` as the class `flat`, on class metrics only | Phase 4 |
| `base_rate_252` | `direction` probabilities; `return` | Phase 4's three historical shares, smoothed as (share × n + 1) / (n + 3) with n the number of returns behind them (MF's `hist_n_h`), so that no class has probability 0; and the historical mean |
| `base_momentum_sign` | `direction` as a class only | Phase 4 |
| `base_prior` | `direction` probabilities; `return` | The class frequencies and the mean return of **all** the fold's training rows |
| `base_vol_trailing` | `volatility` | `vol_21` for h ≥ 5; `vol_21` × √(2/π) for h = 1 (the mean absolute value of a normal return, a stated approximation) |
| `base_interval` | The `return` interval | Conformal quantiles around `base_prior`'s mean, on all training rows |

- A comparison between a model and a baseline uses exactly the rows on which **both** produced a value and neither
  was refused. A baseline that is silent on a row (momentum of zero, a `missing` history feature) is not compared
  there, and its coverage is reported. `base_prior` is never silent, so every model row is compared with something.

---

## 8. Calibration, uncertainty, explanation

### 8.1 Calibration

- `linear` and `gbt`: **vector scaling** on the calibrate slice, a multinomial logistic layer with a scale and an
  offset per class, the offsets constrained to sum to zero so the map is identified.
- `calibrated_engine`: its map is the calibration; the calibrate slice is where its reliability is measured, and a
  configuration whose reliability there is outside tolerance is not taken to validation.
- Reliability tables accompany every probability metric, overall and per liquidity class.

### 8.2 Predictive intervals

Split-conformal on the conformal slice, **scale-normalised and class-conditional**:

- A row's scale is `max(vol_21 × √h, floor)`, where the floor is a low quantile (the 5th percentile) of the positive
  scales in the fit slice. `vol_21` is zero or tiny for a security that has not traded or keeps printing one price;
  without a floor the ratio is undefined or enormous and sets everyone's interval. Where `vol_21` is null the floor
  is used and the row is flagged.
- Residuals are divided by the scale, and the quantile is taken **separately within each liquidity class**: one
  pooled quantile gives only overall coverage, and the gate asks for coverage in each class.
- Coverage on validation folds is reported against the nominal level, overall and per class. The tolerance reflects
  how few independent label windows a slice holds; with a handful, the coverage estimate is coarse and the report
  says so.
- **What is claimed (revision 4).** Split-conformal guarantees rest on exchangeability, which serially dependent and
  cross-sectionally correlated returns do not have. The claim is therefore only this: **the share of rows of a period
  whose realised return falls inside the interval, averaged over that period's dates, per liquidity class**, measured
  on validation folds and on the test period. No conditional coverage (for one security, one regime or one day) is
  claimed. MLK-23 measures the claimed coverage under dependence at realistic sizes; if it misses the nominal level
  there, the interval is reported with its measured coverage, not the nominal one.

### 8.3 Contributions

Every prediction stores each feature's contribution and the base value such that base plus contributions reproduces
the model's **raw score before calibration**. The calibrated value is stored beside it, not decomposed. For a
combination, the components' outputs are the decomposition. Contributions describe the model, not causes.

---

## 9. Registry and gates

### 9.1 Migration `0021_ml_registry.sql` (additive; append-only, the pattern of 0015 and 0017)

| Table | Contents |
|---|---|
| `ml_configurations` | Model configurations (§5.6) and the versioned ML policies (sufficiency, tolerances, effect size, the domain policy, regime bands, `scoring`), as canonical JSON with SHA-256, status and evidence cutoff |
| `ml_datasets` | Manifest, version, build report, labels, `synthetic` flag |
| `ml_protocols` | §5.4's declaration, including the family; sufficiency; `prospective`, `retrospective`, `previously_scored` |
| `ml_test_periods` | Every declared interval and its state, with an exclusion constraint that forbids overlap |
| `ml_runs`, `ml_run_events` | A run and its state history |
| `ml_trials` | Every hyperparameter trial |
| `ml_fold_models` | Per (configuration, fold): slice boundaries, artifact hash, the hash of its frozen validation predictions |
| `ml_models` | A registered artifact: kind (`ref`, `calibrated_engine`, `linear`, `gbt`, `combination`); configuration; output and horizon; training dataset version; training cutoff; validation and test periods; environment tuple; artifact SHA-256 and size; validated domain; `environment` (`research`, `production`); `promoted_from`; `rollforward_of`; creation timestamp |
| `ml_artifacts` | Artifact bytes as base64 `text`, content-addressed, with a CHECK that the decoded bytes match the SHA-256 and are at most 32 MiB (P2's representation for response bodies) |
| `ml_model_metrics` | Append-only metric rows per model or configuration, fold or test, stratum (including age and refusal rates) |
| `ml_gate_reports` | One per gate evaluation: each check, its evidence hashes, the multiplicity used, pass or fail |
| `ml_test_accesses` | §5.4's ledger |
| `ml_waivers` | Owner-entered waivers (survivorship, mode), each naming what it covers |
| `ml_model_events` | Status events with reason and actor; unique (model, sequence) |
| `ml_incident_flags` | Owner-entered data-quality incidents with their range and their own `recorded_at` |
| `ml_designations` | Owner-only: the ordered list of combinations in force per purpose and (output, horizon), from when |
| `r1_predictions` | §11.1 |

- Fold predictions themselves are rebuildable cache files; their hashes in `ml_fold_models` and a determinism test
  (MLK-11) are what make them evidence.
- Artifacts are in PostgreSQL so that P1's whole-database dumps cover them with no change to frozen P1 (P5-D9).
- **Migration text.** The guard over all migrations scans the raw text, comments included, for the binary column
  type and for column names like a blob or a file, document, storage or local path. Hashes are `text`, the bytes
  column is `artifact_base64`, no path is stored, and the migration's comments avoid those words.
- Roles as Phase 3 §13.1. No new role, no `SECURITY DEFINER`.

### 9.2 Statuses, versions and transitions

- **First event.** A `research` row's first event is `candidate`. A `production` row's first event is `production`,
  and it can be written only through the owner path, with `promoted_from` naming a `validated` version, in the same
  transaction as the designation that names it.
- **Later transitions:** `candidate → validated`, `candidate → rejected`, `validated → rejected`,
  `validated → retired`, `production → retired`. A production model is never modified and leaves service only by
  `retired` (MA §27).
- **Promotion creates a new version** with the same artifact hash (MA §58). A validated version is promoted at most
  once per purpose.
- **If a validated version is later rejected, its production copy is not changed automatically.** `status` reports
  the inconsistency as an alert, and retiring the production version is the owner's act. A silent automatic
  retirement would be a production change nobody decided.
- "Immutable" means rows are never changed. Test metrics, later gate reports and events are new rows.

### 9.3 The gates

**The statistical test** used by gates 5 and 9, for a model or configuration M against a baseline B on a set of
scored dates:

- For each date, the loss of each is the equal-weighted mean of the primary loss (§6) over the rows of that date on
  which both produced a value and neither was refused. d_t is M's minus B's.
- Hypothesis: H0, "M is not better than B by at least δ" (the mean of d ≥ −δ), against "M is better by more than δ".
  δ is the protocol's declared minimum effect size, per output.
- p-value by a **stationary bootstrap** of the d_t series, recentred to the null, with expected block length
  L = max(h, 10, a data-driven estimate) dates and B = 9 999 replicates: p = (1 + the number of replicates at least
  as extreme) / (B + 1). Pooling the validation dates of expanding folds tests the average performance of the
  procedure; the gaps at fold boundaries are at least h, which makes the concatenation tolerable.
- **Across baselines** M must reject H0 for every one. That is an intersection-union test and needs no correction.
- **Across the family** the level is α / m (Bonferroni), with m the declared family size for that (output, horizon)
  or the larger registry count of §5.4. Bonferroni, not a step-down method: a later configuration then cannot change
  an earlier one's result.
- **A warning the design owes its reader:** with α/m small and only a few dozen independent blocks, this test has
  little power. At the minimums of §5.5 a one-month-horizon configuration may be unable to pass however good it is.
  The remedy is a small declared family and more data, not a looser test.

**Who enforces what.**

| A database trigger | Application code, with a required `ml_gate_reports` row |
|---|---|
| Legal first events and transitions; event immutability | Gates 2 to 8, and gates 9 to 12 in the owner-path promotion command |
| `validated` only with a passing gate report for that model | |
| The dataset is not `synthetic` (gate 1) | |
| The protocol's declaration precedes the first run, by the database clock | |
| Test intervals do not overlap | |
| A `production` first event only through the owner path, with `promoted_from` validated, a passing promotion gate report and a designation in the same transaction | |

**`candidate → validated`** (evidence about a configuration):

1. The dataset is not `synthetic`.
2. The protocol is `sufficient` for the horizon and was declared before the first run; the configuration is in its
   declared family.
3. The leakage suite of §13 passed on the code version.
4. Walk-forward metrics exist for every fold; reliability and interval coverage are within tolerance overall and per
   liquidity class; refusal rates are reported overall and by regime.
5. The test above is passed against every baseline that fills the output.
6. The eligibility policies and every ML policy used are not `provisional`.
7. The dataset's survivorship is `complete`, or an `ml_waivers` row covers it and the label carries
   `survivorship_waived`.
8. The validated domain (§11.2) is recorded, including the ages evaluated.

**Promotion to `production`** (evidence about the final-fit artifact; the owner's act):

9. The protocol's one access has happened, and this nominee passed the test criterion.
10. A laboratory backtest (Phase 10) exists for it, valid by MA §62, ending at the last V_end or labelled
    post-access.
11. Validation holds `point_in_time` rows in at least the protocol's declared share, or an `ml_waivers` row accepts
    `reconstructed`-only evidence.
12. The designation cites every label the model carries: `survivorship_waived`, `retrospective`,
    `previously_scored`, `rollforward_of`, `no_engine_component`, and the lifetime count of test accesses. A
    designation that omits one is refused.

---

## 10. Route 1 combinations and designation lists

A **combination** is a registered configuration of kind `combination` for one (output, horizon): an ordered list of
component configurations and a rule.

- **Components** must each have passed gates 1 to 8. They need not be nominated or tested alone: the combination's
  own test covers them as they are used.
- **`combine_mean_v1`** (reference): for `direction`, the mean of the components' calibrated probabilities, then its
  own vector scaling on the logarithm of that mean; for `return` and `volatility`, the mean of the point estimates in
  level units; for the interval, its own conformal quantile. A direction-only baseline never enters a probability
  mean. It has a small fitted part and is gated like any configuration.
- **A fitted combiner** is trained on out-of-fold component predictions only: fold k's combiner on the components'
  validation predictions from folds before k, purged against its own cutoff. Fold 1 has none, hence five folds.
- **An approximation, stated.** Fold k's combiner is fitted on predictions from the components' earlier fold models
  and applied to their fold-k models, which are different artifacts of the same configurations. §5.3 forbids that
  for a base model's own calibration. Here it is unavoidable, and its effect is exactly what the combination's
  fold-k reliability and coverage measure, so the gate sees it.
- **The final fit of a combination** is its combiner fitted on all out-of-fold predictions up to the last V_end,
  applied to the components' final fits.
- **A combination needs all of its components.** There is no "combine whatever is present".

**Designation lists.** Coverage comes from an ordered list of combinations per (output, horizon), for example a full
one and a market-only one. A row is predicted by the first whose components are all available.

- **A list is gated as a unit, as it will be used.** A market-only combination validated on every row it can score
  is not thereby validated on the rows it will actually get, which are the securities with no fundamental panel. So
  the routing rule is applied in validation and in the test, and metrics, reliability and coverage are computed
  **per position, on the rows routed to it**.
- A protocol's family and its nominees are therefore lists; the correction of §5.4 counts each position.
- A prediction made by a combination with no engine-derived component is flagged `no_engine_component` (§2).
- Route 2 is never a component, and Route 1 is never replaced by it (MA §26, §59).

---

## 11. Route 1 predictions

### 11.1 The record (`r1_predictions`, append-only)

Per (security, D, T, horizon, designation id): the state; for each output, the combination version that produced it
and its position in the list; the three probabilities and predicted direction; expected return and its interval with
the nominal level; predicted price; expected volatility; flags; every component's output and contributions; **the
security's full set of Phase 4 engine outputs at T, whether or not any was a component**; the MF, F7 and DE row
hashes; the artifact hashes; the label class; the code version tuple; `row_hash`.

A prediction is never overwritten. A re-run with identical inputs reproduces the row's hash in the recorded
environment; with different inputs it is a new row.

### 11.2 Refusing to predict, and the validated domain

A model's **validated domain** is recorded with it: the label classes of its validation rows; the eligibility policy
version; the versions of the features **in its schema**; its horizon; the industry groups present; the per-feature
envelope; its training cutoff and the ages evaluated.

| Cause | State |
|---|---|
| Not market-data eligible; or no combination in the list has all its components | `insufficient_data`, with the reason |
| A `critical` feature null or inadmissible | `insufficient_data` (`missing_critical_features`) |
| A `critical`, non-rank feature outside the envelope (the 0.1st to 99.9th percentile of the fit slice, widened by the policy's margin) | `blocked` (`outside_validated_domain`). A market-wide feature blocks every security that day, which is intended |
| The eligibility policy version, or the version of a feature in the model's schema, differs from the domain's | `blocked`. An unrelated feature added to the registry does not block |
| The row's label class or industry group is not in the domain, and no waiver covers it | `blocked` |
| Older than `max_model_age_sessions` | `blocked` (`model_stale`) |
| An incident flag covers T **and was recorded at or before T** | `blocked`. A flag entered later does not remove a day from validation with hindsight; such days are a reported stratum |
| No designation for the purpose | `not_applicable` |
| Inputs fail their hash checks | `invalid_input` |

- Non-critical features are clipped to their fit-slice bounds.
- The same refusal logic runs in validation and in the test; refusal rates are reported overall and by regime, so a
  model that only predicts on quiet days is seen to do so.
- The envelope says nothing about rank features and detects no joint shift; the age and version checks cover those.
  Drift monitoring proper is Phase 11.

---

## 12. Metrics

### 12.1 One library

`worker/evaluation_metrics/` is pure and shared by Phases 5, 9 and 10, with the scoring policy of §6 and the test of
§9.3. It implements MA §25: directional accuracy, confusion matrix, balanced accuracy; multi-class Brier score, log
loss, reliability, expected calibration error; MAE, RMSE, median absolute error, signed error, error quantiles;
price error; volatility error; interval coverage and width.

### 12.2 Strata

By horizon, security, liquidity class, model version, time period, model age and market regime. Sector and two of
MA §37's regimes are the recorded deviations of §2.

Regime tags are strata only, not model inputs. They are computed point-in-time from MF's aggregates under a pinned,
provisional policy: market trend from `mkt_ret_ew_21` against fixed bands, and market volatility from the
cross-sectional median of `vol_21` against its own trailing distribution up to T, `undefined` until that
distribution has its minimum length.

### 12.3 Statistical care

- Headline metrics carry intervals from the date-level stationary bootstrap of §9.3: whole dates, all securities
  together. The number of blocks is reported; with few, the interval is reported as unreliable.
- Blocks address label overlap, and with the data-driven length some of the clustering in volatility; the reports do
  not claim more.
- **The estimand (revision 4)** of every headline interval and of the gate test is the **mean over dates of the
  date-level loss** (the equal-weighted mean over the date's rows) for the period. Resampling whole dates keeps the
  dependence between securities on one date; blocks of dates keep serial dependence up to the block length. Nothing is
  claimed for a single security, a stratum smaller than the reporting minimum, or a different period.
- The number of independent blocks (period length over block length) is reported with every interval; below 20 the
  interval is marked unreliable, and the gate test of §9.3 is not run (the gate is failed as `insufficient_evidence`).
- Per-security metrics over few rows are shown with their counts and are not ranked.

---

## 13. Tests

Synthetic archives and facts are generated through the lower layers' own factories.

| ID | Test | Passes when |
|---|---|---|
| MLK-1 | **Split structure.** | For every fold, slice and horizon: the R-side and T-side bounds hold; slices and validation sets are pairwise disjoint; no row has two roles; the unused rows are counted. Run in a `KNOWN` era, an `AVAILABLE` era and across the two |
| MLK-2 | **Purge, behavioural.** Labels share a per-date shock across overlapping windows; a model that memorises by date | Chance with the protocol's split; skill with purging switched off in the harness |
| MLK-3 | **Fit scope.** A scaler, imputer, calibration map, early-stopping loop or conformal quantile offered rows outside its slice | Refused |
| MLK-4 | **Test isolation.** A training request for a test row; a dataset build asking for test labels; a second access; a nominee outside the declared family; an access before predictions are frozen | Each refused; one ledger row |
| MLK-5 | **Across protocols.** Rows taken from another protocol's unspent interval; an overlapping test interval declared; a fresh protocol over sessions where many configurations already have validation metrics | Refused; refused; the larger multiplicity is used |
| MLK-6 | **Declaration time.** A protocol inserted after a run of its model, or with a supplied earlier timestamp; a policy whose evidence cutoff is after the first V_start | The trigger refuses; the policy is counted as tried |
| MLK-7 | **Planted signal** of a stated size | The test of §9.3 is passed in at least a stated share of seeds, and the contributions name the planted feature |
| MLK-8 | **Size of the test.** (a) Two predictors of equal expected loss, with cross-sectional date shocks, overlapping-label autocorrelation and volatility clustering; (b) labels independent of every feature | (a) the single-test rejection rate over 400 seeds lies in the binomial acceptance region of the nominal level; (b) the family-wise rate does not exceed it |
| MLK-9 | **Label shuffle** within date and across dates | Skill at chance |
| MLK-10 | **Stacking.** In-sample component predictions; out-of-fold predictions resolving after the combiner's cutoff | Refused |
| MLK-11 | **Determinism.** Train twice in one environment on one machine | Identical artifact hashes and fold-prediction hashes |
| MLK-12 | **Registry.** UPDATE, DELETE, TRUNCATE; every illegal first event and transition; `validated` without a gate report; `production` outside the owner path, without a designation, or twice from one version | All refused |
| MLK-13 | **Each gate alone.** One case per gate 1 to 12, including: the effect-size branch and the multiplicity branch of gate 5; a missing or post-access-unlabelled backtest (gate 10); the mode share (gate 11); each label a designation must cite (gate 12) | Each one alone blocks |
| MLK-14 | **Dependency boundary.** | Capture, backfill and financial-truth packages import no ML library; ML packages import no CSE transport |
| MLK-15 | **Domain.** Out of envelope; stale; a changed schema feature version; an unrelated new registry feature; a label class outside the domain; an incident flag recorded after T | The stated outcome for each, in validation as in prediction |
| MLK-16 | **Artifact integrity.** One byte changed | Refused |
| MLK-17 | **Strata.** Later data planted after T | Liquidity class, regime tags and age strata at T unchanged |
| MLK-18 | **Labels carried.** `reconstructed`, `survivors_only` and mixed eras; two target builds offered to one dataset | The worst label is carried; the second build is refused |
| MLK-19 | **Designation.** One recorded after a prediction's cutoff | The prediction uses the one in force at its cutoff |
| MLK-20 | **Inherited leakage.** MF's ML-1 to ML-24 and Phase 4's FL-1 to FL-22 plants below a built dataset, including an unknown price inside a label's horizon (ML-24), a corporate action not yet entered at the cutoff with no close since the document (FL-21) and a conflicting or too old fiscal-year EPS (FL-22) | The dataset at T and predictions from it are unchanged, and each upstream refusal or `untrusted` state reaches the dataset as a null with its reason, never as a value |
| MLK-21 | **Routing.** A list of two combinations on rows with and without fundamentals | Metrics are per position on the rows routed to it; a position's gate uses only those rows |
| MLK-22 | **Scoring policy.** Each label state, including `unresolvable_missing_price` | Trained, scored, counted and given its sensitivity exactly as §6's table says; `unresolvable_missing_price` never enters training or a headline metric, and its count is reported for every horizon |
| MLK-23 | **Coverage and size under dependence.** Synthetic panels at the protocol's actual minimum sizes, varying serial dependence, a common date factor, volatility clustering, a regime change, unbalanced security coverage and liquidity-class composition | The conformal interval's measured coverage (the §8.2 estimand) and the gate test's type-I error are reported for each case; a case outside the acceptance region marks that configuration of the method as unreliable in every report that uses it |

---

## 14. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0009, C-0012 and C-0021)

| ID | Decision | Reason |
|---|---|---|
| P5-D1 | Models are adapters behind one interface; v1 has `ref`, `calibrated_engine`, `linear`, `gbt` | MA §12.2 mandates no algorithm |
| P5-D2 | Direction is three classes | Thin securities often do not move |
| P5-D3 | v1 ML models take features, not engine scores; `calibrated_engine` is an engine calibration, the one engine-score component (revision 4) | Keeps engines and ML separable (MA §13, §31) |
| P5-D4 | ML dependencies in `requirements-ml.txt` and separate packages | The staged build's dependency set is unchanged |
| P5-D5 | One test access per protocol, on a test period disjoint from every other, for declared nominees, against a criterion of a minimum form | MA §36 |
| P5-D6 | Sufficiency minimums in usable dates (§5.5), provisional | A floor in independent information, not in rows |
| P5-D7 | Nothing is `validated` on synthetic data or under a `provisional` policy | Honest status |
| P5-D8 | One metrics library, one scoring policy and one test for Phases 5, 9 and 10 | One definition of every number |
| P5-D9 | Artifacts in PostgreSQL as checked base64 text | Covered by P1's backups without changing frozen P1 |
| P5-D10 | All fitting and choosing inside the training rows; no refit after calibration | Validation stays evidence |
| P5-D11 | `scoring.1`: train and score on `resolved` and `resolved_carried`; `terminated` and `unexplained_move_in_horizon` out of the headline, each with a sensitivity | No conditioning on the future where a value exists; honest bounds where none does |
| P5-D12 | Promotion creates a new immutable version | MA §58 |
| P5-D13 | A combination needs all its components; designation lists give coverage and are gated per position on the rows routed there | Every predictor that runs has been validated as it runs |
| P5-D14 | The gate test: a recentred stationary bootstrap of per-date loss differences, one-sided with a minimum effect size, Bonferroni over a family fixed at declaration | A defined test whose result cannot change later |
| P5-D15 | The gated unit is a configuration; the tested unit is its final fit; roll-forward refits are a marked version class | Says what the evidence is about, and keeps a production model from being stale by construction |
| P5-D16 | Validation and test rows stop a grace period before their window's end | Slow-resolving labels are not selectively lost |
| P5-D17 | Volatility is predicted as a mean in level units under squared error | The label is zero-inflated; a mean under squared error is defined at zero |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P5-R1 | Any promotion to `production` and its designation, with the labels it must cite | Live Route 1 predictions |
| P5-R2 | For each real protocol: the family, the test period, the nomination rule, the criterion, the level and effect sizes, and the sufficiency minimums | The first real training |
| P5-R3 | Acceptance of the ML dependency set into a deployable build, with re-staging | Deployment |
| P5-R4 | The deviations from MA §12 to §13, §25 and §37 recorded in §2. **Route 1 without an engine component: decided, allowed (C-0036).** Sector and the two regimes: open | Acceptance of this design |
| P5-R5 | Any waiver: survivorship that is not `complete`; validation on `reconstructed` data only. **Validation on `reconstructed` data: waived (C-0039).** Survivorship: open; until decided, no protocol uses an era whose survivorship is not `complete` | Gates 7 and 11 |

---

## 15. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| M5-1 | `worker/evaluation_metrics/`, `scoring.1`, the bootstrap test | Every metric against hand-computed cases; MLK-8, MLK-22 |
| M5-2 | Dataset builder, manifest, version, strata | Determinism; MLK-17, MLK-18, MLK-20 |
| M5-3 | Fold and slice objects (pure) | MLK-1 to MLK-3 |
| M5-4 | Adapters and baselines; canonical serialisation; pinning | MLK-9, MLK-11, MLK-14 |
| M5-5 | Calibration, intervals, contributions | Reliability and coverage per class on synthetic data with known truth; MLK-7 |
| M5-6 | Migration 0021, registry store, triggers, protocol, test-period and access logic, gates, wrapper, runbook | PostgreSQL 17 in CI; MLK-4 to MLK-6, MLK-12, MLK-13, MLK-16 |
| M5-7 | Combinations, designation lists, `r1_predictions`, domain and refusal logic | MLK-10, MLK-15, MLK-19, MLK-21 |
| M5-8 | An end-to-end synthetic walk-forward rehearsal, recorded | The whole chain, reproducible |

Phase 5 needs Phase 3 complete and Phase 4 through DE-4. M5-1 depends on nothing and can start at once.

**Frozen contracts affected.** None modified. Migration 0021 meets the range-stating guards Phase 3 §13.3 names. CI
needs a new Linux group that installs `requirements-ml.txt`, a workflow change recorded when made. Builds of the
pinned libraries for the Windows CI jobs are unverified, so ML tests are planned for Linux only.

---

## 16. What stays open until real data exists

- Whether anything beats the baselines on CSE data. It may not, and that is a legitimate result.
- Whether the gate's test can be passed at all at the long horizons with obtainable data (§9.3).
- Every provisional number.
- How much usable history the TradeIQ archive gives once imported (P3-R1, decided: C-0035). Its first validation
  (`docs/research/TRADEIQ_VALIDATION.md`) finds daily prices for 1992 to 2025, in the `reconstructed` era; corporate
  actions other than splits are not yet adjustable (P3-R2), so their windows and labels are excluded by §6.
- Whether `reconstructed` and `point_in_time` eras behave alike.
- Whether weighting, class balancing or time decay help. Fundamental features change a few times a year per issuer,
  so daily rows repeat them.
- How large the two sensitivities of §6 are.
- Whether artifacts reproduce byte for byte across hosts.

## 17. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new layer above MF, F7 and DE: datasets, configurations, models, registry, Route 1 predictions |
| 2 | Source of truth | The stored, hashed rows of the layers below; ML's own append-only registry |
| 3 | Information at its timestamp | Each row's T and each label's R_h on the era's admission clock; every window bounded on both |
| 4 | Provenance retained | Source row hashes, dataset version, fold and artifact hashes, designation and combination versions |
| 5 | Frozen components | None modified |
| 6 | Migration | Yes: 0021, additive |
| 7 | Leakage prevention | Bounded, purged splits; fitting inside training rows; disjoint test periods with one access; multiplicity counted across protocols; out-of-fold stacking; MLK-1 to MLK-22 |
| 8 | Testing | §13; PostgreSQL 17 in CI |
| 9 | Reproduction | Dataset version over logical content, seed, environment tuple, canonical artifacts |
| 10 | Route 1 and Route 2 | This is Route 1. Route 2 is never a component and never replaces it |
| 11 | Later evaluation | The shared library, policy and test; Phase 9 scores `r1_predictions` against MF's labels |
| 12 | After downtime | No prediction is back-filled as if made on time |
| 13 | Failure representation | Prediction states and reasons; label states scored by a stated policy with sensitivities |
| 14 | Versions | Dataset, protocol, policy, configuration, model, designation, environment and code versions on every row |
| 15 | Grounded reasoning | Stored contributions to the raw score, component outputs, and the full engine outputs |

## 18. How the phases' wording lines up

### 18.1 What this design takes from Phase 3 revision 5 and Phase 4 revision 6

| Upstream | What Phase 5 uses | Changes since the revisions cited by Phase 5 revision 3, and how they are taken |
|---|---|---|
| Phase 3 §5.3 | `px_ref`, `ref_session`, `stale_sessions` per snapshot row | Revision 5's `price_unknown`: the reference price is null with a reason; a row with no reference price has a null predicted price and is refused (§11.2, missing critical feature) |
| Phase 3 §10 | Feature values with their quality states | No change of contract; nulls stay nulls (§4.4) |
| Phase 3 §11 | Label states, `resolved_at`, `abs_ret_1` | Revision 5 added `unresolvable_missing_price`: scored by §6. Revision 4 removed the degraded session: no effect here |
| Phase 3 §12 | ML-1 to ML-24 | ML-24 added: MLK-20 |
| Phase 4 F7 | Financial feature rows with their guards | Revisions 4 to 6 added `eps_basis_ambiguous`, `unexplained_move_since_document` and `no_close_since_document`: each reaches a dataset as a null with its reason (MLK-20) |
| Phase 4 DE | Engine scores and their component sets | `calibrated_engine` uses a score only where the engine used its full component set; a guarded input makes the engine's output absent for that row, and the calibration is not applied there |
| Phase 4 tests | FL-1 to FL-22 | FL-21 and FL-22 added: MLK-20 |

Phase 5 consumes nothing else from those revisions. Any later revision of Phase 3 or 4 is checked against this table
before Phase 5's next acceptance.

- Phases 3 and 4 say no model trained under a `provisional` eligibility policy can pass; gate 6 enforces it.
- Phase 3 defines the label states; the scoring policy here decides how each is scored, for Phases 5, 9 and 10.
- Phase 3 exposes `hist_n_h` for the baseline smoothing of §7.3.

## 19. Audit record

**Review on the research branch (2026-11-12, recorded in `docs/research/PHASE5_ML_DESIGN_AUDIT_2026-11-12.md` on
`research/tradeiq-phase3-validation-2026-11-12`; not a fresh-context audit under C-0005).** Dispositions in revision 4:

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| P5-A01 | Major | `unresolvable_missing_price` had no scoring disposition | **Fixed.** §6 row; MLK-22 |
| P5-A02 | Major | `calibrated_engine` contradicted "ML adapters take features, not engine scores" | **Fixed** by the review's option A: an engine calibration, not an ML adapter (§7.2, P5-D3) |
| P5-A03 | Major | Upstream revisions cited were stale | **Fixed.** Pinned at Phase 3 revision 5 and Phase 4 revision 6 with commits; compatibility table §18.1 |
| P5-A04 | Major | Inherited leakage tests out of date | **Fixed.** MLK-20 covers ML-24, FL-21, FL-22 and the survival of upstream refusals |
| P5-A05 | Blocker (owner decision) | Route 1 could contain no engine component | **Decided by the owner: allowed** (C-0036); §2, P5-R4 |
| P5-A06 | Major review item | Bootstrap and conformal assumptions under dependence | **Fixed by narrowing and testing:** the estimands are stated (§8.2, §12.3), the evidence minimum is set, and MLK-23 measures coverage and type-I error under dependence at realistic sizes |


**Revision 1 (commit `301811b`): audited 2026-11-11. FAIL**, three Blockers, fifteen Major. Its 40 findings and
their dispositions are in revision 2 (`git show 5c67269:docs/PHASE5_ML_DESIGN.md`, §19).

**Revision 2 (commit `5c67269`): audited 2026-11-12 (cloud, fresh-context agent; C-0005). Verdict: FAIL**, no
Blocker, 22 Major, 15 Minor, 6 Notes. The auditor confirmed that the inequalities of §5.2 and §5.3 are leak-free as
written, that the purge per horizon is coherent, that the unit checks against Phase 3 hold, and that artifacts as
base64 text are covered by P1's dumps. Of revision 1's 40 findings it judged 20 resolved and 20 partly resolved.
Dispositions in revision 3:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| N1 | Major | Protocols could declare overlapping test intervals: best-of-many again | **Fixed.** Disjoint intervals by constraint (§5.4, §9.1); MLK-5 |
| N2 | Major | Validation sessions and policies reusable across protocols with the count reset | **Fixed.** Multiplicity follows the sessions; policy evidence cutoffs; `previously_scored` (§5.4); MLK-5, MLK-6 |
| N3 | Major | Test-stage multiplicity and the criterion undefined | **Fixed.** A minimum criterion, corrected across nominees; the lifetime access count (§5.4, gate 12) |
| N4 | Major | "The model" that is gated was not defined | **Fixed.** Configuration, fold models, final fit, and the fixed order (§5.6, P5-D15) |
| N5 | Major | Model age in production outside any evaluated range | **Fixed.** Age strata, the bound on `max_model_age_sessions`, roll-forward refits (§5.6) |
| N6 | Major | Gate 5's test not specified | **Fixed.** §9.3, including its low power stated |
| N7 | Major | The rate test did not test size | **Fixed.** MLK-8 (a) and (b) |
| N8 | Major | Phase 3's new label state unassigned | **Fixed.** `scoring.1` (§6); MLK-22 |
| N9 | Major | Slow-resolving labels selectively lost at window tails | **Fixed.** Rows stop a grace period early (§5.2, P5-D16) |
| N10 | Major | The clock of R_h in `reconstructed` eras | **Fixed.** §4.1; MLK-1 |
| N11 | Major | Sufficiency incoherent; totals a lower bound | **Fixed.** Usable dates, five folds, the recomputed totals (§5.5, §1) |
| N12 | Major | Volatility output under-specified and zero-inflated | **Fixed.** A mean in level units under squared error (§6, P5-D17) |
| N13 | Major | The interval scale could be zero | **Fixed.** A floor and class-conditional quantiles (§8.2) |
| N14 | Major | Combinations inconsistent with one nominee and the registry | **Fixed.** Lists as nominees, component status, the stated approximation, the final combiner fit (§10) |
| N15 | Major | The fallback population is not the validated one | **Fixed.** Lists gated per position on routed rows (§10); MLK-21 |
| N16 | Major | The key of `r1_predictions` | **Fixed.** §11.1 |
| N17 | Major | No storage for fold artifacts and predictions | **Fixed.** `ml_fold_models`; predictions as hashed caches (§9.1) |
| N18 | Major | The validated domain partly decorative, partly too strict | **Fixed.** §11.2; MLK-15 |
| N19 | Major | The production row's life cycle | **Fixed.** §9.2, the enforcement table, `ml_waivers` |
| N20 | Major | "Route 1 combination: met" not guaranteed | **Admitted as a deviation** with the flag and the stored engine outputs (§2, §11.1, P5-R4) |
| N21 | Major | Baselines differed from Phase 4 and handicapped the reference | **Fixed.** §7.3; `hist_n_h` added to Phase 3 |
| N22 | Major | "Each gate alone" not delivered | **Fixed.** MLK-13 |
| N23 to N37 | Minor | Stale test ids; a reused reserved id; Phase 3's wording on scoring; the carried-label wording; inner-slice embargo and the engine map; the embargo before the test; identifiability of vector scaling; MLK-1's coverage; step exits; the migration guard and the artifact cap; MA §37's regimes; one row per session; the worker-role limit; refusal rates by regime; unused straddling rows | **Fixed**, respectively in §7.1 and §7.2; P5-R5; §18 and Phase 3; §6; §5.3 and §7.2; §5.4; §8.1; MLK-1; §15; §9.1; §2; §4.1; §5.4; gate 4 and §11.2; §4.3 |
| N38 to N43 | Note | P1 coverage plausible; selection and early stopping on one slice acceptable; pooling over folds; purge per horizon; Phase 3 revision 3 matches; no incumbent comparison | Recorded (§5.3, §9.3, §5.2, §4.1, §2) |

Revision 3 is to be audited again before any implementation.
