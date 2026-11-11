# Phase 5: ML and the Route 1 combination (design)

**Status: DESIGN, revision 2 (2026-11-11). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact, no training on real data and no change to `main`.

*Revision 2 replaces revision 1 (commit `301811b`), which its audit failed with three Blockers and fifteen Major
findings (§19). The Blockers were all about the final test period: many candidates could each be tested once and the
best kept; validation labels could resolve inside the test period; and the validation folds were used both to choose
hyperparameters and as gate evidence. Revision 2 makes the test period one access per protocol with a criterion
declared in advance (§5.4), bounds every row's label inside its own window (§5.2), and moves all selection inside the
training rows (§5.3).*

**Governing records:** Master Architecture (MA) §2, §4, §11 to §14, §21 to §23, §25, §27, §28, §32 to §37, §43.1, §55
(Phase 5), §56 to §62, §68; `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, revision 2 ("MF": features, universe, labels);
`docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md`, revision 2 (financial features "F7", engines and baselines "DE").
Where this document and those differ, they win.

**Identifiers.** Implementation steps are M5-1 to M5-8 (§15). Tests are MLK-*n* (§13). Decisions are P5-D*n*
([delegated]) and P5-R*n* (reserved) (§14). Phase 3's leakage tests are called "MF's ML-1 to ML-20" here to keep
them apart.

---

## 1. Conclusion

```
MF snapshot rows + F7 feature rows + DE outputs ─▶ dataset builder ─▶ versioned dataset (features | labels, apart)
                                              │
                        chronological protocol: bounded, purged walk-forward; one declared final test period
                                              │
          model adapters ─▶ calibration and intervals ─▶ registry (candidate → validated → production)
                                              │
                 Route 1 combinations (versioned, gated; component outputs kept) ─▶ Route 1 predictions
```

1. **The hard part of this phase is the protocol, not the algorithm.** MA §12.2 mandates no algorithm. It mandates
   chronological validation, an isolated final test period, a registry and no leakage. The design fixes that
   machinery and treats models as replaceable adapters.
2. **Three time rules close the leaks the audit found.** A training row's label must have resolved by the training
   cutoff. A validation or test row's label must resolve inside its own window. Everything that is chosen or fitted
   is chosen and fitted on training rows only, in ordered, purged slices (§5).
3. **The final test period can be used once per protocol**, for one model nominated by a rule declared beforehand,
   against a pass criterion declared beforehand. The sanctioned code path enforces this and records every access. It
   cannot stop a person with database access from looking; the design says so instead of claiming otherwise (§5.4).
4. **Nothing here can be validated yet.** There is no market history and no fundamental panel. On synthetic data the
   tests show that specific planted faults are caught and that a planted signal is found; they do not show the
   pipeline is useful. No model leaves `candidate` on synthetic data (§9.3).
5. **The honest timescale is long.** With forward capture only, the first protocol that meets the sufficiency
   minimums for the one-month horizon needs roughly six years of sessions (§5.5). That makes the owner's decision on a
   source of market history (Phase 3, P3-R1) the single most important input to this phase.
6. **ML dependencies stay out of the frozen build**, and model artifacts live in PostgreSQL so that the existing
   backups cover them without a change to P1 (§9.1).

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Status and where |
|---|---|---|
| Every model has ID, version, training dataset, feature schema, training cutoff, validation period, test period, hyperparameters, artifact hash, metrics, creation timestamp, deployment status | §12.2, §28, §61 | Met: §9.1 |
| Statuses `candidate`, `validated`, `production`, `retired`, `rejected` | §28 | Met: §9.2 |
| A candidate passes its gates and receives a new immutable version; research never silently becomes production | §58, §27, §59 | Met: promotion creates a new version (§9.2) |
| The Route 1 combination is versioned and keeps component outputs; engines are baselines, not fallbacks | §13 | Met: §10 |
| Reasoning from feature contributions | §14 | Met: §8.3 |
| The prediction contract for every horizon, including volatility | §4 | Met: §6 |
| Confidence statistically grounded | §21 | Met: §8 |
| Refuse to forecast: outside validated domain, missing critical features | §22 | Met: §11.2 |
| Chronological walk-forward; no future observations in earlier windows | §32, §11 | Met: §5 |
| Historical universe, survivorship, selection bias | §33 to §35 | Met, with labels carried: §4.3 |
| Final test period isolated; no repeated optimisation against it | §36 | Met on the sanctioned path, with the stated limit: §5.4 |
| Metrics by horizon, security, **sector**, liquidity class, model version, regime, period | §25, §37 | Met except **sector**: no point-in-time sector source exists (Phase 3, P3-R5). A recorded deviation, for the owner |
| Leakage tests | §11, §57 | Met: §13 |

---

## 3. Boundary

**ML reads:** `mf_snapshot_rows`, `mf_target_rows` (Phase 3), `f7_feature_rows`, `de_outputs` (Phase 4), and its own
tables. It reads no raw observation, no economic fact and no F8 result directly.

**ML never** contacts a network, reads a mutable table, trains inside a prediction call, or modifies a frozen
component.

**Not owned:** features and labels (Phases 3, 4), Route 2 (Phase 7), the user-facing forecast record (Phase 8), live
outcome evaluation (Phase 9), the laboratory's replay loop (Phase 10), drift and retraining policy (Phase 11).
Phase 5 provides what those call: the metrics library with its scoring policy, the protocol, the registry and the
predictor.

---

## 4. The dataset

### 4.1 A row

One row per (security, reference session D, cutoff T), where D is the D\* of the snapshot computed at T.

- **Features:** the MF feature vector; the F7 feature vector of the security's issuer as linked at T; both
  eligibility results; DE outputs. Each with its quality state and its source row hash.
- **Labels:** MF's label rows for (reference snapshot row, h), h ∈ {1, 5, 10, 21}: state, values, `resolved_at` and
  row hash (Phase 3 §11, §13.1). **R_h**, the label's resolution instant, is that `resolved_at`.
- **Row metadata:** label class (`point_in_time` or `reconstructed`); the era's survivorship label; liquidity class
  (terciles of `turnover_avg_21` in the same-T universe); regime tags (§12.2); industry group.

Features and labels are separate files of a dataset, and the training code receives labels only through the split
object of §5. **Labels for rows inside a declared test period are not built into any dataset** until the access
command runs (§5.4).

### 4.2 The manifest and the version

A manifest names: the MF, F7 and DE configuration ids; the date range; the cutoff rule; the mode per era, with the
knowledge horizon **H** of every `AVAILABLE` era; the MF target-build id; the row filter; the label definition
version. The **dataset version** is the SHA-256 of the canonical manifest plus the ordered source row hashes, a hash
over **logical content**: it does not depend on how a columnar file happens to be written.

Datasets are cache files outside the repository and Git. They are not backed up, because they are rebuilt
deterministically from the database by their manifest and checked against their version. The database holds the
manifest and the version.

### 4.3 Honest labelling of what the data is

- A dataset over a `reconstructed` era is a reconstruction; every metric on it says so.
- **Survivorship.** A captured era is `complete` from the first capture on, because each session's universe is read
  from that day's archive. An imported era carries its batch's label. A dataset spanning several eras carries the
  worst of them (`survivors_only` and `unknown` are worse than `complete`).
- Rows are never dropped silently: every exclusion is counted by reason in the build report.

### 4.4 Missing values

Phases 3 and 4 never impute. In Phase 5 the admissible quality states per feature are part of the model's feature
schema (default `fresh`, `stale`, `partial`); a value outside them is null. Nulls go to models that handle them
natively, with a missing indicator; otherwise an imputation fitted on the fit rows is part of the artifact. A null in
a feature the schema marks `critical` means no prediction (`missing_critical_features`).

---

## 5. The chronological protocol

### 5.1 Time order

Every row has a decision instant T, and each of its labels a resolution instant R_h > T. All splitting is on these
instants, per horizon, never on row index or date alone.

### 5.2 The outer walk-forward: bounded and purged

A fold has C_train < V_start ≤ V_end. For the horizon being modelled:

| Rows | Condition |
|---|---|
| Training | R_h ≤ C_train (which implies T < C_train): the label was known at the training cutoff |
| Validation | V_start ≤ T **and** R_h ≤ V_end: the label resolves inside the fold's own window |
| Embargo | V_start is at least `embargo_sessions` after C_train (default 1). Its purpose is the uncertainty of the assumed availability time in `reconstructed` eras (Phase 3, P3-D4); for point-in-time rows purging alone is sufficient |

- Folds expand: the next fold's C_train is this fold's V_end. Rows with T ≤ V_end whose labels resolve after V_end
  belong to no fold's validation set; they become training rows of a later fold once resolved. Nothing is counted
  twice, and no validation label reaches past its window.
- Long feature look-back windows that overlap the training period are legitimate information, not leakage.
- Cross-sectional transformations within one T use only that T's universe and are not fitted objects.

### 5.3 Inside the training rows: ordered slices, no selection on validation

Everything chosen or fitted for a fold uses its training rows only, cut chronologically into four slices by
boundaries S1 < S2 < S3 < C_train:

| Slice | Rows | Used for |
|---|---|---|
| Fit | R_h ≤ S1 | Model parameters, imputation, scaling, feature clipping bounds, the training envelope |
| Select | T > S1 and R_h ≤ S2 | Hyperparameter choice and early stopping |
| Calibrate | T > S2 and R_h ≤ S3 | The calibration map |
| Conformal | T > S3 and R_h ≤ C_train | Interval residuals |

- Each slice is purged against the one before it by the same rule as the outer split, so no label window straddles a
  boundary.
- **The fold's model is the one fitted on the fit slice. It is not refitted on all training rows afterwards**, because
  the calibration map and the residuals would then belong to a different model. The cost is stated: the most recent
  part of the training period calibrates the model but does not train it.
- Each slice has a minimum size in the protocol (§5.5). A fold that cannot meet it is not run.
- **The validation fold is never used to choose anything.** Its metrics are evidence. What a researcher does after
  seeing validation metrics (trying another model family or feature set) is still selection, which is why the number
  of configurations taken to validation is recorded and corrected for in the gate (§9.3), and why the final check is
  the test period.

The fold object releases validation labels only to the metrics function, after the predictions are frozen and hashed.

### 5.4 The final test period

**Declaration.** An experiment protocol row, written before any training, fixes: the dataset manifest rule; every
configuration id it uses (MF, F7, DE and ML policies), each of which must record an evidence cutoff before P_start;
the fold schedule and slice sizes; the horizons and outputs; **one primary horizon, output and metric**; the model
families and search budget; the **nomination rule** by which one model per (output, horizon) is chosen from the
walk-forward; the **test pass criterion**; and the test period [P_start, P_end].

**Rules.**
- P_start is after the last V_end. Test rows have T ≥ P_start and R_h ≤ P_end.
- The model tested is the **final fit**: the model the nomination rule selects, fitted with C_train = the last V_end
  and its own inner slices. That same artifact is the one that can be promoted. A refit through the test period would
  be a new, untested version.
- **One access per protocol.** The access command writes a row to the test-access ledger (who, when, which protocol,
  which nominated models), builds the test-period labels for the first time, computes the metrics and evaluates the
  declared criterion. After it, the period is **spent**: no later selection under any protocol may be justified by it.
- Anything that joins predictions to labels inside the test period is an access. A laboratory backtest (Phase 10) of
  a candidate therefore ends at the last V_end; one that covers the test period runs only after the access and is
  labelled as post-access.
- **A global registry of test periods** holds every declared interval. No protocol, `exploratory` ones included, may
  take training or validation rows from an interval that another protocol has declared and not yet spent. Once spent,
  those sessions may become training data of a later protocol whose own test period is later still.
- A test period is `prospective` if P_start is after the declaration's database time, otherwise `retrospective`. With
  imported history the first test periods will be retrospective: the designer could have seen the data. The label is
  carried into every report and into the promotion decision.
- A model changed after the access is a new version and needs a new protocol with a later test period.

**The limit, stated plainly.** These rules are enforced in the sanctioned code path and recorded append-only. Labels
and prices for the test period also exist in the database, where a reader can query them. No software rule prevents
a person from looking and letting that shape a design. The ledger records sanctioned accesses; it cannot see others.

### 5.5 Data sufficiency

A protocol is `sufficient` for horizon h only if it meets these minimums (provisional, P5-D6), stated in sessions
because overlapping daily rows are not independent observations:

| Quantity | Minimum |
|---|---|
| Sessions in the first fold's fit slice | 504 |
| Folds | 4 |
| Sessions per validation fold | max(63, 6·h) |
| Sessions in each of the select, calibrate and conformal slices | max(42, 4·h) |
| Sessions in the test period | max(126, 6·h) |
| Distinct securities with rows, per fold and in the test period | 50 |

A protocol below them can run, labelled `exploratory`; its models cannot pass the gates.

**What this implies.** On top of the 252 sessions MF's provisional eligibility needs before any row exists: for
h = 21, about 252 + 504 + 3×84 + 4×126 + 126 ≈ 1 640 sessions; for h = 1 or 5, about 252 + 504 + 3×42 + 4×63 + 126 ≈
1 260. At roughly 240 sessions a year (an assumption about CSE's calendar), that is about seven and five years.
Every number here is provisional, but the order of magnitude is not: forward capture alone will not support a
`sufficient` protocol for years.

---

## 6. Prediction targets and the scoring population

A **model** is one output for one horizon. Per horizon h there are three outputs:

| Output | Label (Phase 3 §11) | Form |
|---|---|---|
| `direction` | `rise`, `flat`, `fall` | Three probabilities summing to 1 (P5-D2) |
| `return` | The return | A point estimate and a predictive interval (§8.2) |
| `volatility` | Realised volatility for h ≥ 5; `abs_ret_1` for h = 1 | A point estimate. Models fit the logarithm (with a floor) so the output is positive |

**The scoring policy** (`scoring.1`, versioned, in the metrics library, used by Phases 5, 9 and 10):

- **Main population:** label states `resolved` and `resolved_carried`. A security that does not trade on the target
  session has a real outcome for its holder (usually a zero return, class `flat`). Whether it will trade is not known
  at T, so excluding those rows would condition on the future and overstate the chance of a move for exactly the
  thinly traded securities. They are in training and in the headline metrics.
- **Secondary stratum:** `resolved` only, reported beside it.
- **`terminated`:** not trained on in v1 and reported as a separate count and stratum. A delisting has no agreed
  return, and leaving these rows out biases results favourably. Every report states the count.
- **Excluded and counted:** `unresolvable_quality`, `unknown_session_in_horizon`, `pending`.

**Derived fields.** Predicted direction is the most probable class. Predicted price is
`reference_close × (1 + expected return)`, on the share basis of the reference close's own session; it is shown with
the reference price's `stale_sessions`. If the direction model's most probable class and the sign of the expected
return disagree, both are kept and the row is flagged `direction_return_disagree`; nothing reconciles them silently.

---

## 7. Model adapters

### 7.1 The interface

```
fit(fold_slices, hyperparameters, seed) -> artifact
predict(artifact, features) -> predictions with per-row contributions
describe(artifact) -> feature schema, clipping bounds, training envelope, hyperparameters, environment tuple
```

- **Serialisation is canonical per adapter**: a text form defined by this project (a boosted-tree library's own text
  dump; coefficients as decimal strings in canonical JSON), never a language-level object pickle. The artifact's
  SHA-256 is its identity.
- **The environment tuple** records library versions, the CPU feature set, the thread count (fixed at 1) and the
  seed.
- **What is claimed.** Loading the same artifact bytes in the recorded environment gives the same predictions after
  quantisation (predictions are rounded to 9 decimal places before any hash). Retraining in the same environment on
  the same machine reproduces the artifact hash; that is tested (MLK-9). **Not claimed:** identical bytes across
  different hosts or CPU types. That is measured at staging, and a difference there is a recorded property of the
  environment, not hidden.

### 7.2 Adapters in v1

| Adapter | Outputs | How | Contributions |
|---|---|---|---|
| `ref` | Those each baseline fills | Passes Phase 4's baselines through (§7.3) | — |
| `calibrated_engine` | `direction`, `return` | One Phase 4 engine's score s: a multinomial logistic map on (s, s²) for the three classes, since `flat` need not be monotone in s; a linear map on s for the return | The engine's own components |
| `linear` | All three | Regularised multinomial logistic regression; ridge regression on return and on log-volatility | Coefficient × standardised value |
| `gbt` | All three | Gradient-boosted trees | The library's exact tree contribution values |

- v1 ML adapters take MF and F7 features, **not** engine scores (P5-D3), so engines and ML stay separable for the
  combination and for the deterministic-versus-ML comparison (MA §31).
- No neural model in v1: nothing yet justifies one at this data size.
- Libraries are chosen and pinned at M5-4 in `requirements-ml.txt`. The capture, backfill and financial-truth
  packages import none of them (MLK-12).
- No sample weighting, class weighting or time decay in v1. Fundamental features change only a few times a year per
  issuer, so daily rows repeat them; whether weighting helps is a data question (§16).

### 7.3 Baselines through the same protocol

| Baseline | Outputs it is scored on | Source |
|---|---|---|
| `base_no_change` | `return` (0) | Phase 4 |
| `base_rate_252` | `direction` (the three historical shares, with add-one smoothing so no class has probability 0), `return` (the historical mean) | Phase 4, from MF's return-history features |
| `base_momentum_sign` | Direction as a class only; never a probability metric | Phase 4 |
| `base_prior` | `direction`: the class frequencies of the fold's fit slice. `return`: its mean | Fitted in-fold by `ref`; the strongest trivial reference |
| `base_vol_trailing` | `volatility`: `vol_21` for h ≥ 5; `vol_21` × √(2/π) for h = 1 (the mean absolute value of a normal return, a stated approximation) | MF's feature |
| `base_interval` | The `return` interval: conformal quantiles around `base_prior`'s mean | Fitted in-fold |

Each baseline is compared only on the fields it fills. A model's gate comparison (§9.3) is against every baseline
that fills its output.

---

## 8. Calibration, uncertainty, explanation

### 8.1 Calibration

- `linear` and `gbt` produce three class scores. The calibration map is **vector scaling**: one multinomial logistic
  layer (a scale and an offset per class) fitted on the calibrate slice. It always yields probabilities that sum to 1.
- `calibrated_engine`'s map (§7.2) is itself the calibration, fitted on the fit slice and checked on the calibrate
  slice.
- Reliability tables are reported with every probability metric, overall and per liquidity class, and the gate
  checks them (§9.3).

### 8.2 Predictive intervals

Split-conformal on the conformal slice, **scale-normalised**: the residual of each row is divided by a row scale
(`vol_21` × √h; the slice's pooled scale where that is null), the quantile of those ratios is taken, and each
prediction's interval is its point estimate plus and minus that quantile times its own scale. Intervals are
therefore wider for volatile securities.

- Coverage on validation folds is reported against the nominal level, overall **and per liquidity class**, and the
  gate checks each.
- Under serial dependence the textbook guarantee is approximate, and the effective sample is the number of dates, not
  rows. The measured coverage is what counts.

### 8.3 Contributions

Every prediction stores each feature's contribution and the base value such that base plus contributions reproduces
the model's **raw score before calibration**. Calibration is not additive, so the calibrated probability is stored
beside it, not decomposed. For a combination, the components' outputs are the decomposition. Contributions describe
the model, not causes in the world.

---

## 9. Registry and gates

### 9.1 Migration `0021_ml_registry.sql` (additive; append-only, the pattern of 0015 and 0017)

| Table | Contents |
|---|---|
| `ml_configurations` | The versioned ML policies as canonical JSON with SHA-256: sufficiency minimums, tolerances, the domain policy, regime bands, `scoring` version, each with its status and evidence cutoff |
| `ml_datasets` | Manifest, dataset version, build report, label classes, survivorship, `synthetic` flag |
| `ml_protocols` | §5.4's declaration; sufficiency result; `prospective` or `retrospective` |
| `ml_test_periods` | The global registry of declared intervals and their state (`declared`, `spent`) |
| `ml_runs`, `ml_run_events` | A training or evaluation run and its state history (a run row is immutable; completion is an event) |
| `ml_trials` | Every hyperparameter trial |
| `ml_models` | Model id and version; kind (`ref`, `calibrated_engine`, `linear`, `gbt`, `combination`); output and horizon; training dataset version; feature schema; training cutoff; validation period; test period; hyperparameters; environment tuple; artifact SHA-256 and size; validated domain (§11.2); `environment` (`research`, `backtest`, `production`); `promoted_from`; creation timestamp. For a combination: its ordered component versions and rule |
| `ml_artifacts` | Artifact bytes as base64 `text`, content-addressed, with a CHECK that the decoded bytes match the SHA-256 and a size cap (P2's representation for response bodies, `docs/ops/P2_MARKET_CAPTURE.md` §5) |
| `ml_model_metrics` | Metrics per model, fold or test, stratum; reliability; coverage; refusal rates |
| `ml_gate_reports` | One per gate evaluation: each check, its evidence hashes, pass or fail |
| `ml_test_accesses` | §5.4's ledger |
| `ml_model_events` | Status transitions with reason and actor; a unique (model, sequence) so the latest event is unambiguous |
| `ml_incident_flags` | Owner-entered data-quality incidents with their time range (§11.2) |
| `ml_designations` | Owner-only: which model or combination versions are in force for which purpose, in what order, from when |
| `r1_predictions` | Route 1's immutable prediction rows (§11) |

- **Artifacts are in PostgreSQL** so that P1's existing dumps, off-site sync and restore check cover them with no
  change to frozen P1 (P5-D9). Revision 1 proposed a file store "added to P1's dump boundary by configuration"; the
  audit showed P1 has no such configuration. Boosted-tree and linear artifacts are small (megabytes).
- **Migration text.** The guard over all migrations (`tests/test_document_retrieval.py`) fails on a binary column
  type and on any column named like a blob or a file, document, storage or local path. Hashes are `text` (the 0017
  convention), the bytes column is `artifact_base64`, and no path is stored.
- Roles as Phase 3 §13.1. No new role, no `SECURITY DEFINER`.

### 9.2 Statuses, versions and transitions

Legal transitions, enforced by trigger: `candidate → validated`, `candidate → rejected`, `validated → rejected`,
`validated → retired`, `production → retired`.

**Promotion creates a new version** (MA §58). A `validated` model is never moved to `production`. The owner's
promotion writes a new `ml_models` row with `environment = production`, `promoted_from` naming the validated version,
and the same artifact hash; that row starts in `production`. A production model is never modified and is taken out
of service only by `retired` (MA §27). A model's artifact, schema and metrics are immutable.

### 9.3 The gates

**Who enforces what.**

| Enforced by a database trigger | Enforced by application code, with a required `ml_gate_reports` row |
|---|---|
| Legal transitions and event immutability | Sufficiency (gate 2) |
| `validated` only with a passing gate report for that model | The leakage suite's result for the code version (gate 3) |
| The model's dataset is not `synthetic` (gate 1) | Reliability and coverage tolerances (gate 4) |
| The protocol's declaration time precedes the model's first run, both by the database clock | The baseline comparison (gate 5) |
| A `production` row only through the owner path, with `promoted_from` a `validated` version and a designation | Policy status, survivorship, domain (gates 6 to 8) |

So a worker cannot write `validated` without a gate report, and the metric-based checks are as trustworthy as the
application code that writes that report, which the tests of §13 cover.

**`candidate → validated`:**

1. The dataset is not `synthetic`.
2. The protocol is `sufficient` for the model's horizon and was declared before the model's first run.
3. The leakage suite of §13 passed on the code version that trained it.
4. Walk-forward metrics exist for every fold; reliability and interval coverage are inside the protocol's tolerances
   overall and per liquidity class.
5. **Better than the baselines, by a test.** For the model's metric, take the per-date loss difference against each
   baseline that fills its output, pooled over the validation folds. The one-sided hypothesis "the model is no better"
   is tested with a date-block (stationary) bootstrap, blocks at least h sessions. The model passes only if it is
   rejected for every such baseline at the protocol's level **after a Holm correction** over all (output, horizon)
   models of the protocol and the number of configurations taken to validation, and the improvement exceeds the
   protocol's declared minimum effect size. A tie does not pass.
6. The MF and F7 eligibility policies and every ML policy it used are not `provisional`. (Phases 3 and 4 say the same:
   a model trained under a provisional policy cannot pass.)
7. The dataset's survivorship is `complete`; or an owner waiver exists, in which case the model's label carries
   `survivorship_waived`.
8. The model's validated domain (§11.2) is recorded.

**Promotion to `production`** (the owner's decision, through the owner path):

9. The protocol's single test access has happened and its declared criterion passed for this model.
10. A laboratory backtest (Phase 10) exists for this version, valid by MA §62, ending at the last V_end or labelled
    post-access.
11. **Mode.** The validation folds contain `point_in_time` rows in at least the protocol's declared share, or the
    owner's designation explicitly accepts a model validated on `reconstructed` data only.
12. The designation cites any `survivorship_waived` or `retrospective` label the model carries. A designation that
    does not cite them is refused.

A prediction call loads only versions named by a designation in force at its cutoff.

---

## 10. Route 1 combinations

A **combination** is a registered model of kind `combination`: an ordered list of component model versions for one
(output, horizon), and a rule. It is gated, versioned, promoted and designated like any model.

- **`combine_mean_v1`** (reference): for `direction`, the mean of the components' calibrated three-class
  probabilities; for `return` and `volatility`, the mean of their point estimates. Only components that provide the
  output in calibrated form enter (a direction-only baseline never enters a probability mean). The mean of calibrated
  probabilities is not itself calibrated, and averaging intervals has no coverage meaning. So even this combination
  has a small fitted part: its own vector-scaling map and its own conformal quantile, fitted on out-of-fold component
  predictions. It is the untuned reference a fitted combiner must beat.
- **A fitted combiner** is trained on out-of-fold component predictions only.
  - Fold k's combiner is fitted on the components' validation predictions from folds before k, purged against its
    own cutoff by the rule of §5.2, and evaluated on fold k. Fold 1 has none, so a combination has one evaluation fold
    fewer than its components. A protocol that gates a combination therefore needs at least 5 folds.
  - Because components no longer choose anything on validation folds (§5.3), their fold-k predictions are not
    contaminated by fold k's labels.
- **A combination needs all of its components.** If one cannot predict for a row, that combination does not predict
  for it. There is no "combine whatever is present": each subset would be a different, unvalidated predictor.
- **Ordered designations give coverage.** The owner designates an ordered list of combinations for a purpose, for
  example a full one and a market-only one. A row is predicted by the first whose components are all available, and
  the row records which. Each is gated separately. This is how a security with no fundamental panel, which is nearly
  every security at first, can still get a Route 1 output, labelled `market_only`. Whether a market-only output
  counts as a production-quality forecast (MA §60 asks for sufficient financial data) is Phase 8's rule.
- Every combined prediction stores each component's full output (MA §13).
- Route 2 is never a component, and Route 1 is never replaced by it (MA §26, §59).

---

## 11. Route 1 predictions

### 11.1 The record (`r1_predictions`, append-only)

Per (security, D, T, horizon, combination version): the state; the three direction probabilities and predicted
direction; expected return and its interval with the nominal level; predicted price; expected volatility; flags;
every component's output and contributions; the MF, F7 and DE row hashes; the artifact hashes; the label class; the
code version tuple; `row_hash`. This is the Route 1 block of MA §23.

A prediction is never overwritten. A re-run at the same key with identical inputs reproduces the row's hash in the
recorded environment; with different inputs it is a new row, and the earlier one stays.

### 11.2 Refusing to predict, and the validated domain

A model's **validated domain** is recorded with it: the label classes of its validation rows, the eligibility policy
and feature registry versions, its horizon, the industry groups present, the per-feature envelope, and its training
cutoff.

| Cause | State |
|---|---|
| Not market-data eligible; or not financially eligible and no market-only combination is designated | `insufficient_data`, with the reason |
| A `critical` feature null or in an inadmissible state | `insufficient_data` (`missing_critical_features`) |
| A `critical`, non-rank feature outside the envelope (the 0.1st to 99.9th percentile of the fit slice, widened by the domain policy's margin) | `blocked` (`outside_validated_domain`). When the feature is market-wide this blocks every security that day, which is intended: the market is somewhere the model has not seen |
| The feature registry or eligibility policy version differs from the domain's | `blocked` (`outside_validated_domain`) |
| More sessions since the training cutoff than the domain policy's `max_model_age_sessions` | `blocked` (`model_stale`) |
| An owner-entered incident flag covers T | `blocked` |
| No designated combination for the purpose | `not_applicable` |
| Inputs fail their hash checks | `invalid_input` |

- Non-critical features are clipped to their fit-slice bounds (part of the artifact), so a linear model cannot
  extrapolate without limit.
- **The same refusal logic runs in validation and in the test**, and refusal rates are reported with the metrics, so
  the validated numbers describe the population that will actually be predicted.
- Rank features are always inside [0, 1], so the envelope says nothing about them; the model-age and version checks
  are what cover drift there. The envelope detects no joint shift. Drift monitoring proper is Phase 11.

---

## 12. Metrics

### 12.1 One library

`worker/evaluation_metrics/` is pure and shared by Phases 5, 9 and 10, together with the scoring policy of §6, so a
research number and a live number mean the same thing. It implements MA §25: directional accuracy, confusion matrix,
balanced accuracy; multi-class Brier score, log loss, reliability tables, expected calibration error; MAE, RMSE,
median absolute error, signed error and error quantiles; absolute and percentage price error; volatility error and
realised against predicted; interval coverage and width.

### 12.2 Strata

By horizon, security, liquidity class, model version, time period and market regime. Sector is the recorded
deviation of §2.

**Regime tags** are strata only; they are not model inputs in v1. They are computed point-in-time from MF's
aggregates at each T under a pinned, versioned, provisional policy: market trend from `mkt_ret_ew_21` against fixed
bands, and market volatility from the cross-sectional median of `vol_21` against its own trailing distribution up to
T. Until that trailing distribution has the policy's minimum length the tag is `undefined`. Liquidity class and
regime tags have their own point-in-time test (MLK-17).

### 12.3 Statistical care

- Headline metrics carry intervals from a **date-block stationary bootstrap**: whole dates are resampled, all
  securities together, in blocks at least as long as the horizon. The number of blocks is reported; with few blocks
  the interval is reported as unreliable, not hidden.
- Blocks of length h address label overlap, not volatility clustering; the reports say so.
- Per-security metrics over few rows are shown with their counts and are not ranked.

---

## 13. Tests

Synthetic archives and facts are generated through the lower layers' own factories, so the whole chain runs.

| ID | Test | Passes when |
|---|---|---|
| MLK-1 | **Purge, structural.** | For every fold, slice and horizon, no yielded training row has R_h after its cutoff, and no validation or test row has R_h after its window's end |
| MLK-2 | **Purge, behavioural.** Synthetic labels share a per-date shock across overlapping label windows; a model able to memorise by date is trained | With the protocol's split, validation skill is at chance. With purging switched off in the harness, skill appears. Both are asserted |
| MLK-3 | **Fit scope.** A scaler, imputer, calibration map, early-stopping loop or conformal quantile is offered rows outside its slice | The fold object refuses each |
| MLK-4 | **Test isolation.** Training code requests a test-period row; a dataset build requests test-period labels; a second access is attempted; a second model is nominated | Each refused; exactly one ledger row exists |
| MLK-5 | **Cross-protocol.** A protocol takes training or validation rows from another protocol's unspent test interval | Refused by the global registry |
| MLK-6 | **Declaration time.** A protocol row inserted after a run of its model, or with a supplied earlier timestamp | The trigger refuses |
| MLK-7 | **Planted signal.** Synthetic returns depend on a known feature | Validation skill passes gate 5's test, and the contributions name that feature |
| MLK-8 | **Pure noise, as a rate.** Labels independent of every feature, over many seeds | The share of seeds in which any model passes gate 5 is at or below the nominal level within its binomial interval |
| MLK-9 | **Label shuffle.** Labels permuted within date and whole cross-sections reassigned across dates | Skill is at chance |
| MLK-10 | **Stacking.** A combiner is offered in-sample component predictions, or out-of-fold predictions whose labels resolve after its cutoff | Refused |
| MLK-11 | **Determinism.** Train twice in one environment on one machine | Identical artifact hash and quantised predictions |
| MLK-12 | **Registry.** UPDATE, DELETE, TRUNCATE and every illegal transition, for every role; `validated` without a gate report; `production` outside the owner path | All refused |
| MLK-13 | **Each gate alone.** A synthetic dataset; an insufficient protocol; a failed leakage suite; failed reliability; failed coverage in one liquidity class; a tie with a baseline; a `provisional` policy; survivorship without a waiver; a missing domain; a failed test criterion; a designation that omits a carried label | Each one alone blocks |
| MLK-14 | **Dependency boundary.** | Capture, backfill and financial-truth packages import no ML library; ML packages import no CSE transport |
| MLK-15 | **Domain.** A row outside the envelope; a stale model; a changed registry version | The stated `blocked` state; and the same rows are refused inside validation, with the refusal rate reported |
| MLK-16 | **Artifact integrity.** One byte of an artifact changed | The CHECK or the loader refuses |
| MLK-17 | **Strata.** Later data planted after T | Liquidity class and regime tags at T are unchanged |
| MLK-18 | **Labels carried.** Datasets over `reconstructed`, `survivors_only` and mixed eras | The dataset, the model and every report carry the worst label |
| MLK-19 | **Designation.** A designation recorded after a prediction's cutoff | The prediction uses the one in force at its cutoff |
| MLK-20 | **Inherited leakage.** MF's ML-1 to ML-20 and Phase 4's FL-1 to FL-15 plants applied below a built dataset | The dataset at T, and predictions from it, are unchanged |

---

## 14. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0009 and C-0012)

| ID | Decision | Reason |
|---|---|---|
| P5-D1 | Models are adapters behind one interface; v1 has `ref`, `calibrated_engine`, `linear`, `gbt` | MA §12.2 mandates no algorithm; these are the inspectable standard for tabular data of this size |
| P5-D2 | Direction is three classes | Thin securities often do not move; folding `flat` into a side distorts both probabilities |
| P5-D3 | v1 ML models take features, not engine scores | Keeps engines and ML separable (MA §13, §31) |
| P5-D4 | ML dependencies in `requirements-ml.txt` and separate packages | The staged build's dependency set is unchanged |
| P5-D5 | One test access per protocol, for the nominated model, against a pre-declared criterion | MA §36. Replaces revision 1's per-version limit, which allowed best-of-many |
| P5-D6 | Sufficiency minimums in sessions (§5.5), provisional | Overlapping rows are not independent; a floor in sessions measures information |
| P5-D7 | No model becomes `validated` on synthetic data or under a `provisional` policy | Honest status |
| P5-D8 | One metrics library and one scoring policy for Phases 5, 9 and 10 | One definition of every number |
| P5-D9 | Artifacts in PostgreSQL as checked base64 text | Covered by P1's backups without changing frozen P1 |
| P5-D10 | All selection inside the training rows; the fold's model is not refitted after calibration | Validation folds stay selection-free evidence |
| P5-D11 | Train and score on `resolved` plus `resolved_carried`; `terminated` reported, not trained | Excluding no-trade outcomes conditions on the future |
| P5-D12 | Promotion creates a new immutable version | MA §58 |
| P5-D13 | A combination needs all its components; coverage comes from an ordered list of designated combinations | Every predictor that runs has been validated as such |
| P5-D14 | Gate 5 is a corrected, one-sided, date-block bootstrap test with a minimum effect size | "Wins most folds" passes noise about a third of the time |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P5-R1 | Any promotion to `production` and its designation, including acceptance of `retrospective`, `reconstructed`-only or `survivorship_waived` evidence | Live Route 1 predictions |
| P5-R2 | The final test period, nomination rule and pass criterion of each real protocol | The first real training |
| P5-R3 | Acceptance of the ML dependency set into a deployable build, with the re-staging MA §43.1 requires | Deployment |
| P5-R4 | The deviation from MA §25: no sector strata until a sector source exists (with Phase 3's P3-R5) | Acceptance of this design |

---

## 15. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| M5-1 | `worker/evaluation_metrics/` and `scoring.1` | Every metric against hand-computed cases; the bootstrap on series with known dependence |
| M5-2 | Dataset builder, manifest, logical-content version | Determinism; MLK-18, MLK-20 |
| M5-3 | Fold and slice objects; protocol, test-period registry and access logic (pure parts) | MLK-1 to MLK-5 |
| M5-4 | Adapters and baselines; canonical serialisation; dependency pinning | MLK-7 to MLK-9, MLK-11, MLK-14 |
| M5-5 | Calibration, scale-normalised conformal intervals, contributions | Reliability and coverage per stratum on synthetic data with known truth |
| M5-6 | Migration 0021, registry store, triggers, gates, wrapper, runbook | PostgreSQL 17 in CI; MLK-6, MLK-12, MLK-13, MLK-16 |
| M5-7 | Combinations, ordered designations, `r1_predictions`, the domain and refusal logic | MLK-10, MLK-15, MLK-17, MLK-19 |
| M5-8 | An end-to-end synthetic walk-forward rehearsal, recorded | The whole chain from a synthetic archive to gated models, reproducible |

Phase 5 needs Phase 3 complete and Phase 4 through DE-4 (its tables are what ML reads). M5-1 depends on nothing and
can start at once.

**Frozen contracts affected.** None modified. Migration 0021 meets the same range-stating guards as 0019 and 0020
(Phase 3 §13.3); the full list is established by running the suite. CI needs a new Linux group that installs
`requirements-ml.txt`; that is a workflow change, recorded in the decision log when made. Whether the pinned
libraries have builds for the Windows CI jobs is unverified, so ML tests are planned for the Linux groups only.

---

## 16. What stays open until real data exists

- Whether anything beats the baselines on CSE data. It may not, and the design treats that as a legitimate result.
- Every provisional number: sufficiency, tolerances, effect size, the envelope margin, model age, regime bands.
- How much history exists and of what kind (Phase 3, P3-R1). §5.5 gives the consequence.
- Whether `reconstructed` and `point_in_time` eras behave alike.
- Whether weighting, class balancing or time decay help (§7.2).
- How large the favourable bias from `terminated` labels is (§6).
- Whether artifacts reproduce byte for byte between the workstation's containers and the server (§7.1).

## 17. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new layer above MF, F7 and DE: datasets, models, registry, Route 1 predictions |
| 2 | Source of truth | The stored, hashed rows of the layers below; ML's own append-only registry |
| 3 | Information at its timestamp | Each row's T; each label's R_h; every window bounded on both |
| 4 | Provenance retained | Source row hashes, dataset version, artifact hashes, combination version, per prediction |
| 5 | Frozen components | None modified (§15) |
| 6 | Migration | Yes: 0021, additive |
| 7 | Leakage prevention | Bounded, purged splits; selection inside training rows; one declared test access; out-of-fold stacking; MLK-1 to MLK-20 |
| 8 | Testing | §13; synthetic chains; PostgreSQL 17 in CI |
| 9 | Reproduction | Dataset version over logical content, seed, environment tuple, canonical artifact; same-machine retraining reproduces the hash |
| 10 | Route 1 and Route 2 | This is Route 1. Route 2 is never a component and never replaces it |
| 11 | Later evaluation | The shared metrics library and scoring policy; Phase 9 scores `r1_predictions` against MF's labels |
| 12 | After downtime | No prediction is back-filled as if made on time: a row's T is when its inputs were cut |
| 13 | Failure representation | Prediction states and reasons (§11.2); a combination that cannot run is recorded with the missing component |
| 14 | Versions | Dataset, protocol, policy, model, combination, environment and code versions on every row |
| 15 | Grounded reasoning | Stored contributions to the raw score, and component outputs (§8.3, §10) |

## 18. How the phases' wording lines up

Phase 3 §9 and Phase 4 §11 say no model trained under a `provisional` eligibility policy can pass; gate 6 is where
that is enforced. Training under one is allowed and labelled `exploratory`.

## 19. Audit record

**Revision 1 (commit `301811b`): audited 2026-11-11 (cloud, fresh-context agent; C-0005). Verdict: FAIL**, three
Blockers, fifteen Major, sixteen Minor, six Notes. The auditor confirmed that the label purge itself was correct, that
the cited conventions (append-only pattern, owner path, `requirements.txt`) exist, and that the statements about what
cannot be validated now were accurate. Dispositions in revision 2:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Blocker | The access limit allowed best-of-many on the test period; no pass criterion | **Fixed.** One access per protocol, nomination rule and criterion declared first, the period then spent (§5.4, gate 9) |
| 2 | Blocker | Validation labels of the last fold resolved inside the test period | **Fixed.** Validation and test rows are bounded by R_h (§5.2, §5.4); MLK-1 |
| 3 | Blocker | Validation folds used both for selection and as gate evidence; early stopping unassigned | **Fixed.** Four purged slices inside the training rows (§5.3); MLK-3 |
| 4 | Major | The purge test did not test purging | **Fixed.** MLK-1 (structural) and MLK-2 (behavioural, date-shock plant) |
| 5 | Major | Gate 5 was not a statistical test | **Fixed.** Gate 5 (§9.3); the unit of gating is one output for one horizon (§6); MLK-8 measures a rate |
| 6 | Major | Baselines binary; no volatility, interval or pooled baseline; no input path | **Fixed.** §7.3, with Phase 4 revision 2 |
| 7 | Major | Training only on `resolved` rows selected on the future | **Fixed.** `scoring.1` (§6, P5-D11) |
| 8 | Major | Calibration and conformal slices under-specified and leaking | **Fixed.** §5.3 (purged slices, no refit, sizes), §8.1 (vector scaling; the engine map), §8.2 (scale-normalised, per-class coverage) |
| 9 | Major | The one-session volatility output was a constant with no target | **Fixed.** `abs_ret_1` in Phase 3 revision 2; log-volatility models (§6) |
| 10 | Major | The combiner was not closed by construction | **Fixed.** §10: nested schedule, purged out-of-fold rows, gated and designated combinations, no partial component sets |
| 11 | Major | Isolation only on the sanctioned path, with routes around it | **Fixed where software can**, and the remaining limit stated (§5.4): labels not built before access, global registry, pinned configurations with evidence cutoffs, `prospective`/`retrospective`, test minimums, the final-fit rule, backtests ledgered |
| 12 | Major | Mode ignored by gates and domain | **Fixed.** Gate 11; the validated domain (§11.2) |
| 13 | Major | The envelope was weak and not applied in validation | **Fixed.** §11.2 |
| 14 | Major | The backup claim was wrong | **Fixed.** Artifacts in PostgreSQL (§9.1, P5-D9); datasets are rebuildable caches (§4.2) |
| 15 | Major | "The registry enforces" was unspecified; tables missing | **Fixed.** §9.3's enforcement table; `ml_configurations`, `ml_incident_flags`, `ml_run_events`, `ml_gate_reports` |
| 16 | Major | Promotion did not create a new version | **Fixed.** §9.2 (P5-D12) |
| 17 | Major | Reproducibility over-claimed | **Fixed.** §7.1, §4.2 |
| 18 | Major | Sufficiency minimums did not measure independent information | **Fixed.** §5.5, with the timescale stated |
| 19 | Minor | Embargo rationale vague | **Fixed.** §5.2 |
| 20 | Minor | Block bootstrap under-specified | **Fixed.** §12.3 |
| 21 | Minor | Regime tags | **Fixed.** §12.2; MLK-17 |
| 22 | Minor | Within-date shuffle keeps market-wide skill | **Fixed.** MLK-9 |
| 23 | Minor | Leak paths without tests; MLK-11 covered four gates | **Fixed.** MLK-3 to MLK-6, MLK-10, MLK-13, MLK-15, MLK-18, MLK-19 |
| 24 | Minor | Numbering conflicts | **Fixed.** Steps M5-*n*; headings no longer numbered as steps |
| 25 | Minor | Dependency statement wrong | **Fixed.** §15 |
| 26 | Minor | The resolution instant had no source | **Fixed.** Phase 3 revision 2 stores `resolved_at` and a row hash (§4.1) |
| 27 | Minor | Survivorship for captured and mixed eras | **Fixed.** §4.3 |
| 28 | Minor | Predicted price basis; direction against return | **Fixed.** §6 |
| 29 | Minor | Sector strata omitted without a recorded deviation; registry fields missing | **Fixed.** §2, P5-R4; §9.1 |
| 30 | Minor | Which quantity contributions reproduce | **Fixed.** §8.3 |
| 31 | Minor | The survivorship waiver reached production silently | **Fixed.** Gates 7 and 12 |
| 32 | Minor | Transitions ambiguous | **Fixed.** §9.2 |
| 33 | Minor | Over-claims | **Fixed.** §1 points 3 and 4; §10 |
| 34 | Minor | No frozen-contracts section | **Fixed.** §15 |
| 35 | Note | The migration text guard | Stated (§9.1) |
| 36 | Note | Wording across the three phases | §18 |
| 37 | Note | Nearly every security lacks a panel at first | Addressed by ordered designations and `market_only` (§10) |
| 38 | Note | Pseudo-replication, weights, imbalance | Recorded (§7.2, §16) |
| 39 | Note | "About three years" was a lower bound | Replaced by §5.5's estimate |
| 40 | Note | Redundant condition | Removed (§5.2) |

Revision 2 is to be audited again before any implementation.
