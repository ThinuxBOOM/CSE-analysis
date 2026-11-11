# Phase 5: ML and the Route 1 combination (design)

**Status: DESIGN, revision 1 (2026-11-11). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact, no training on real data and no change to `main`.

**Governing records:** Master Architecture (MA) §2, §4, §11 to §14, §21 to §23, §25, §27, §28, §32 to §37, §55
(Phase 5), §56 to §62, §68; `docs/PHASE3_MARKET_FEATURES_DESIGN.md` (features, universe, targets);
`docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md` (financial features, engines, reference baselines). Where this document
and those differ, they win.

**Short name:** ML. Steps are ML-1 to ML-8 (§15). Decisions are P5-D*n* ([delegated]) and P5-R*n* (reserved) (§14).

---

## 1. Conclusion

```
MF snapshots + F7 features + DE outputs ─▶ ML-1 dataset builder ─▶ versioned dataset (features | labels, kept apart)
                                                │
                                   ML-2 chronological protocol: purged walk-forward, declared final test period
                                                │
              ML-3 model adapters ─▶ ML-4 calibration and uncertainty ─▶ ML-5 registry (candidate → … → production)
                                                │
                       ML-6 Route 1 combination (versioned; component outputs kept) ─▶ ML-7 Route 1 predictions
```

1. **The hard part of this phase is the protocol, not the algorithm.** MA §12.2 mandates no algorithm. What it
   mandates is chronological validation, isolation of the final test period, a registry, and no leakage. The design
   therefore fixes the dataset, split, registry and gate machinery precisely, and treats models as replaceable
   adapters behind one interface.
2. **Overlapping labels are the main leakage path, and the split closes it.** A row at session D with a 21-session
   horizon has a label that is not known until 21 sessions later. A training set that ends at C may contain only rows
   whose label had **resolved** by C (§5.2). This is purging; it is enforced by the split code and tested.
3. **The final test period is declared before any training and every look at it is recorded** (MA §36). The declaration
   and the access ledger are append-only database rows, not a convention.
4. **Nothing here can be validated yet.** There is no market history and no fundamental panel (Phase 3 §14, Phase 4
   §15). On synthetic data the pipeline can be proven correct: it must find a planted signal, report chance on noise,
   and be unmoved by planted leaks. It cannot be proven useful. No model leaves `candidate` on synthetic data, and the
   registry enforces that (§9.3).
5. **ML dependencies stay out of the frozen build.** They live in a separate requirements file and separate packages;
   the capture and backfill workers import none of them.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Where met |
|---|---|---|
| Model families unconstrained; every model has ID, version, training dataset, feature schema, training cutoff, validation period, hyperparameters, artifact hash, metrics, deployment status | §12.2, §28, §61 | §7, §9 |
| Registry statuses `candidate`, `validated`, `production`, `retired`, `rejected`; test period recorded | §28 | §9 |
| The Route 1 combination is versioned and keeps component outputs; engines are baselines, not fallbacks | §13 | §10 |
| Reasoning from feature contributions; explainability | §14 | §8.3 |
| The prediction contract: probability of rise and fall, direction, expected return, price where meaningful, volatility, uncertainty | §4 | §6, §8, §11 |
| Confidence statistically grounded | §21 | §8 |
| Refuse to forecast: model outside validated domain, missing critical features | §22 | §11.2 |
| Chronological walk-forward; future observations never in earlier windows | §32 | §5 |
| Historical universe, survivorship, selection bias | §33 to §35 | §4.3 |
| Final test period isolated; no repeated optimisation against it | §36 | §5.3 |
| Metrics by horizon, security, liquidity class, model version, regime, period | §25, §37 | §12 |
| A production model is never silently modified; research never silently becomes production | §27, §58, §59 | §9 |
| Leakage tests | §11, §57 | §13 |

---

## 3. Boundary

**ML reads:** `mf_snapshot_rows` and `mf_target_rows` (Phase 3), `f7_feature_rows` and `de_outputs` (Phase 4), and its
own tables. It reads no raw observation, no economic fact and no F8 result directly: the layers below are its only
inputs, and their row hashes are its provenance.

**ML never** contacts a network, reads a mutable table, trains inside a production prediction call, or modifies a
frozen component.

**Not owned:** features and targets (Phases 3 and 4), Route 2 (Phase 7), the user-facing forecast record that joins
both routes (Phase 8), live outcome evaluation (Phase 9), the backtesting laboratory's replay loop (Phase 10),
drift monitoring and retraining policy (Phase 11). Phase 5 provides the pieces those phases call: the metrics
library, the protocol, the registry and the predictor.

---

## 4. ML-1: the dataset

### 4.1 A row

One row per (security, reference session D, cutoff T). T is the information cutoff at which every input was computed;
D is its D\* (Phase 3 §4.3).

- **Features:** the MF feature vector, the F7 feature vector of the security's issuer, both eligibility results, and
  optionally DE outputs (§7.2). Each comes with its quality state, and each source row's hash is kept.
- **Labels:** MF's target rows for (security, D, h), h ∈ {1, 5, 10, 21}, with their states and their **resolution
  instant**: the instant at which the target session's bar became visible under the dataset's mode.
- **Row metadata:** the mode label (`point_in_time` or `reconstructed`), the survivorship label of the era (Phase 3
  §8), liquidity class (terciles of `turnover_avg_21` in the same-T universe), and the regime tags of §12.2.

Features and labels are stored in separate files of the dataset, and the training code receives labels only through
the split object of §5. No code path joins them without a split.

### 4.2 The manifest and the version

A dataset is defined by a manifest: the MF, F7 and DE configuration ids, the date range, the cutoff rule (for example
"each session's close-of-capture instant"), the mode per era, the row filter (which eligibility results are admitted)
and the label definition version. The **dataset version** is the SHA-256 of the canonical manifest plus the ordered
list of source row hashes. Two builds with the same version are byte-identical; a model records the version it was
trained on (MA §12.2, "training dataset").

Data is written as columnar files under a content-addressed directory outside the repository and outside Git. The
database holds the manifest, the version and the file hashes (§9.1), never the matrix.

### 4.3 Honest labelling of what the data is

- A dataset over a `reconstructed` era is a reconstruction, and every metric computed on it says so.
- A dataset over a `survivors_only` or `unknown` survivorship era carries that label into every report built on it,
  and such a dataset alone cannot support the promotion gate (§9.3). The historical universe is the as-of universe of
  each T (MA §35); the builder never filters by today's list.
- Rows are never dropped silently. A row excluded by the filter is counted by reason in the manifest's build report.

### 4.4 Missing values

Features arrive with nulls and quality states; Phases 3 and 4 never impute. In Phase 5:

- the admissible quality states per feature are part of the model's feature schema (default: `fresh`, `stale`,
  `partial`; never `conflicting` or `untrusted`), and a value outside them is treated as null;
- nulls are passed to models that handle them natively, with an explicit missing indicator per feature; for models
  that do not, an imputation fitted **on the training fold only** is part of the model artifact;
- a prediction row with a null in a feature the schema marks `critical` is not predicted (`missing_critical_features`).

---

## 5. ML-2: the chronological protocol

### 5.1 Time order

Every row has a **decision instant** T and each of its labels a **resolution instant** R_h > T. All splitting is by
these instants, never by row index or by date alone.

### 5.2 Purged, embargoed walk-forward

A fold is defined by three instants C_train < V_start ≤ V_end:

- **Training rows:** T ≤ C_train **and** R_h ≤ C_train for the horizon being trained. A row whose label was not yet
  known at C_train is purged, whatever its T.
- **Validation rows:** V_start ≤ T ≤ V_end. Their labels resolve later, by construction.
- **Embargo:** V_start ≥ C_train plus one session. With purging by resolution instant no further gap is needed for
  label overlap; the embargo covers any feature whose window is anchored on a session boundary.
- Folds expand: each next fold's C_train is the previous V_end, purged again.

Everything fitted is fitted inside the fold on its training rows only: imputation, scaling, feature selection,
hyperparameter choice, calibration maps (which use a later slice of the training rows, never validation rows), and
conformal residuals. The fold object hands out data in a way that makes the wrong order a type error: validation
labels are released only to the metrics function, after predictions are frozen and hashed.

Cross-sectional transformations within one T (ranks, same-date standardisation) use only that T's universe and are
not fitted objects.

### 5.3 The final test period

- An **experiment protocol** row declares, before any training: the dataset manifest rule, the fold schedule, the
  horizons, the metrics, the primary metric, the model families allowed, the hyperparameter search budget, and the
  **final test period** [P_start, P_end] with P_start after the last V_end.
- No training, tuning, feature selection or early stopping may read a row with T in the test period, or a label that
  resolves inside or after it beyond the purge rule.
- A test evaluation is possible only through one command, which first writes a **test access** row (who, when, which
  model version, which protocol) and then computes. The ledger is append-only. The number of accesses per protocol is
  shown on every report that cites a test metric, and the promotion gate takes a limit on it (default: one access per
  registered candidate version; P5-D5).
- A model changed after a test access is a new version and needs a new protocol with a later test period for any new
  claim. The old result stands as recorded.

### 5.4 Data sufficiency

A protocol is `sufficient` only if it meets stated minimums: sessions of history, folds, validation rows per fold per
horizon, and distinct securities per fold. The values are provisional (P5-D6: at least 756 sessions, 4 folds, 2 000
validation rows per fold, 50 securities). A protocol below them can run and is labelled `exploratory`; its models
cannot pass the gate.

---

## 6. Prediction targets

Per horizon h, three model outputs cover MA §4's contract:

| Output | Target | Form |
|---|---|---|
| Direction probabilities | MF's `direction` ∈ {`rise`, `flat`, `fall`} | Three probabilities summing to 1. Probability of rise and of fall are MA §4's two numbers; `flat` is explicit because illiquid securities often do not move (P5-D2) |
| Expected return | MF's return | A point estimate and a predictive interval (§8.2) |
| Expected volatility | MF's realised volatility over the horizon (h ≥ 5) | A point estimate; for h = 1, the conditional scale from the return model's interval |

- **Predicted direction** is the most probable class. **Predicted price** is `reference_close × (1 + expected return)`
  with the interval mapped the same way, given "where meaningful": it is withheld when the reference close is a
  carried-forward close older than the policy's staleness limit.
- Only label rows in state `resolved` train or score by default. `unresolvable_no_trade` rows are scored in a separate
  stratum, because a zero return from no trading is real for the holder; the others are excluded and counted.

---

## 7. ML-3: model adapters

### 7.1 The interface

```
fit(train: FoldData, hyperparameters, seed) -> artifact
predict(artifact, features) -> predictions with per-row contributions
describe(artifact) -> feature schema, training envelope (§11.2), hyperparameters, library versions
```

An adapter is deterministic given data, hyperparameters, seed and environment: fixed seeds, a single thread, and the
library versions recorded with the artifact. The artifact is serialised to bytes and its SHA-256 is its identity (MA
§28).

Floating point is unavoidable here. Reproducibility is therefore defined as: the same artifact bytes give the same
predictions in the recorded environment, and retraining in the recorded environment reproduces the artifact hash. A
different environment is a different, recorded environment tuple, not a silent variation.

### 7.2 Adapters in v1

| Adapter | Use | Contributions |
|---|---|---|
| `ref` | Wraps Phase 4's parameter-free baselines so they flow through the same protocol and reports | — |
| `calibrated_engine` | One Phase 4 engine's score mapped to the prediction contract by a monotone calibration (§8.1) | The engine's own components |
| `linear` | Regularised multinomial logistic regression (direction); ridge regression (return, volatility) | Coefficient × standardised value |
| `gbt` | Gradient-boosted trees for each output | Exact tree SHAP values from the library's own contribution output |

- v1 ML adapters take MF and F7 features, **not** engine scores (P5-D3). Engines and ML then remain separable
  components for the combination (§10) and for the deterministic-versus-ML comparison MA §31 requires.
- Neural models "where justified" (MA §12.2) are not in v1: with at most a few hundred securities and daily data,
  nothing yet justifies them. The interface admits them later.
- Libraries are chosen and pinned at ML-3 in `requirements-ml.txt` (numerical arrays, a linear-model library, a
  boosted-tree library with native contribution output, a columnar file format). The capture, backfill and financial
  truth packages import none of them, and a static test enforces that (§13, MLK-12).

### 7.3 Hyperparameters

Chosen inside the walk-forward on validation folds only, within the protocol's declared budget, with every trial
recorded (parameters, fold metrics, seed). The count of trials is reported beside the chosen model's metrics, because
the more configurations are tried, the less the best validation score means (MA §36).

---

## 8. ML-4: calibration, uncertainty, explanation

### 8.1 Calibration

Raw model scores are mapped to probabilities by a calibration fitted on a chronologically later slice of the training
rows of each fold (isotonic or logistic for binary reductions; a multinomial logistic map for the three classes). The
calibration map is part of the artifact. Reliability (predicted against observed frequency, by bin) is always
reported with a probability metric, and a model whose validation reliability fails the protocol's tolerance cannot
pass the gate.

### 8.2 Predictive intervals

Return intervals come from split-conformal residuals computed chronologically: residuals on the calibration slice of
each fold give the quantiles added to the point estimate. Coverage on validation folds is reported against the nominal
level. Under serial dependence the coverage guarantee is approximate, and the report says so; the measured coverage is
what the gate checks, not the theory.

This is the "statistically grounded" confidence MA §21 asks for: a stated method, a nominal level, and a measured
coverage.

### 8.3 Contributions

Every prediction row stores the contribution of each feature to each output, in the model's own units, plus the base
value, so that base plus contributions reproduces the score. These are what Route 1's reasoning cites (MA §14). They
describe the model, not causes in the world, and Phase 8's text must not say otherwise.

---

## 9. ML-5: registry and gates

### 9.1 Migration `0021_ml_registry.sql` (additive; append-only, the pattern of 0015 and 0017)

| Table | Contents |
|---|---|
| `ml_datasets` | Manifest (canonical JSON, SHA-256), dataset version, file hashes, build report, labels (`point_in_time` / `reconstructed`, survivorship), `synthetic` flag |
| `ml_protocols` | The experiment protocol of §5.3, declared before training; sufficiency result |
| `ml_runs` | One training or evaluation run: protocol, dataset version, adapter, hyperparameters, seed, fold, environment tuple, code version tuple |
| `ml_trials` | Every hyperparameter trial of a search |
| `ml_models` | Model id and version; kind (`ref`, `calibrated_engine`, `linear`, `gbt`, `combiner`); artifact SHA-256 and size; feature schema; training cutoff; validation period; test period; training envelope; `environment` (`research`, `backtest`, `production`) |
| `ml_model_metrics` | Metrics per model, fold, horizon, stratum; reliability tables; interval coverage |
| `ml_test_accesses` | §5.3's ledger |
| `ml_model_events` | Status transitions with reason and actor; the latest event is the status |
| `ml_designations` | Owner-only: which model version is production for which purpose from when |
| `r1_combinations` | §10: a combination's definition and version |
| `r1_predictions` | §11: Route 1's immutable prediction rows |

Artifacts are files in a content-addressed store on the backup disk's data area, never in PostgreSQL; the registry
holds their hash and refuses to load an artifact whose bytes do not match. Backing that store up is P1's dump
boundary extended by configuration, decided at ML-5 with the runbook.

### 9.2 Statuses and transitions

```
candidate ──▶ validated ──▶ production ──▶ retired
     └──────────▶ rejected ◀──────┘
```

A trigger enforces legal transitions and that an event is never changed. A model's artifact, schema and metrics are
immutable: changing anything is a new version (MA §27, §59).

### 9.3 The gates

**`candidate` → `validated`** (automatic check, recorded with its evidence):

1. The dataset is not `synthetic`.
2. The protocol is `sufficient` (§5.4) and was declared before the model's first run.
3. The leakage suite of §13 passed on the code version that trained it.
4. Walk-forward metrics exist for every fold, with reliability and coverage inside the protocol's tolerances.
5. On the protocol's primary metric the model is no worse than `base_no_change` and `base_rate_252` in a stated
   majority of folds. A model that cannot beat the parameter-free baselines is not `validated`, whatever else it does.
6. The MF and F7 eligibility policies it was trained under are not `provisional` (Phase 3 §9, Phase 4 §11).
7. The dataset's survivorship label is `complete`, or the limitation is recorded in a waiver signed through the owner
   path.

**`validated` → `production`** (the owner's decision, through the owner path, never automatic):

8. A backtest by the laboratory (Phase 10) exists for this version and is valid by MA §62.
9. A final test evaluation exists, within the access limit.
10. The owner's designation row names the version and the purpose.

There is no path from `research` to `production` that skips these: a `production` prediction call loads only a
version named by a designation in force at the call's cutoff (MA §58).

---

## 10. ML-6: the Route 1 combination

- A **combination** is a registered object: an ordered list of component model versions (calibrated engines, ML
  models, reference baselines) and a rule that maps their per-horizon outputs to one output.
- **`combine_equal_v1`**, the reference combination, has no fitted parameter: the mean of the components' class
  probabilities, the mean of their expected returns, and the widest of their intervals. It exists so that a fitted
  combiner has something untuned to beat.
- A **fitted combiner** (`kind: combiner`) is a model like any other: trained on out-of-fold component predictions
  under the same purged protocol, registered, gated and versioned. Stacking on in-sample predictions is the classic
  leak here and is closed by construction: the combiner's training rows are validation-fold predictions only.
- Every combined prediction stores **each component's full output** and the combination version (MA §13). A component
  that could not predict (an engine `blocked_quality`, a model outside its domain) is recorded as absent with its
  reason, and the rule states what it does then: v1 combines the components present if at least the configured minimum
  are, and otherwise Route 1 is `blocked`.
- Route 1 is never replaced by Route 2, and no combination has Route 2 as a component (MA §26, §59). Comparing the
  routes is Phase 8's agreement layer.

---

## 11. ML-7: Route 1 predictions

### 11.1 The record (`r1_predictions`, append-only)

Per (security, D, T, horizon, combination version): the state; the three direction probabilities, predicted direction,
expected return and interval with its nominal level, predicted price and interval where meaningful, expected
volatility; every component's output and contributions; the MF, F7 and DE row hashes used; the dataset-builder
manifest rule; the model artifact hashes; the mode labels; the code version tuple. This is the Route 1 block of MA
§23, and Phase 8's forecast record points at it.

A prediction is never overwritten. A re-run at the same key with identical inputs must reproduce the row's hash; with
different inputs it is a new row with a new `created_at`, and the earlier one stays.

### 11.2 Refusing to predict

States follow MA §22: `generated`, `blocked`, `insufficient_data`, `invalid_input`, `not_applicable`.

| Cause | State |
|---|---|
| Not market-data or financially eligible | `insufficient_data`, with the eligibility reason |
| A `critical` feature null or in an inadmissible quality state | `insufficient_data` (`missing_critical_features`) |
| **Outside the validated domain:** any `critical` feature outside the model's training envelope (the stored per-feature range between the 0.5th and 99.5th training percentiles, widened by the policy's margin) | `blocked` (`outside_validated_domain`) |
| A critical data-quality incident flag at T (set through the owner path) | `blocked` |
| No designated production model for the purpose | `not_applicable` |
| Inputs fail their own hash checks | `invalid_input` |

"No prediction is better than fabricated precision" (MA §22): none of these falls back to a guess.

---

## 12. Metrics

### 12.1 One library

`worker/evaluation_metrics/` is pure and shared by Phase 5's validation, Phase 9's outcome evaluation and Phase 10's
laboratory, so that a research number and a live number mean the same thing. It implements MA §25: directional
accuracy, the confusion matrix, balanced accuracy; Brier score (multi-class), log loss, reliability tables and
expected calibration error; MAE, RMSE, median absolute error, signed error and error quantiles for returns; absolute
and percentage error for price; error and realised-against-predicted for volatility; interval coverage and width.

### 12.2 Strata

Every metric is reported by horizon, security, liquidity class, model version, time period and market regime (MA §25,
§37). Sector is omitted until a point-in-time sector source exists (Phase 3, P3-D6).

Regimes are tags computed point-in-time from MF's own market aggregates at each T: market trend (the sign and size of
`mkt_ret_ew_21` against fixed bands) and market volatility (the cross-sectional median of `vol_21` against its own
trailing distribution up to T). The bands are a versioned, provisional policy. A regime tag is a feature of the date,
known at T; it is never assigned with hindsight.

### 12.3 Statistical care

- With overlapping horizons, daily rows are not independent. Reports give block-bootstrap intervals (blocks at least
  as long as the horizon) for headline metrics, not naive standard errors.
- Per-security metrics over few rows are shown with their counts and are not ranked.

---

## 13. Tests

Synthetic archives and facts are generated through the lower layers' own factories, so the whole chain runs.

| ID | Test | Passes when |
|---|---|---|
| MLK-1 | **Purge.** A feature is planted equal to the row's own future label | With the protocol's split, validation skill is at chance. With purging switched off in the test harness, skill appears. Both are asserted, so the test proves the purge is what blocks the leak |
| MLK-2 | **Future rows.** Rows with T after C_train are planted in the training source | The fold never yields them |
| MLK-3 | **Fit scope.** A scaler or imputer is given validation rows | The fold object refuses |
| MLK-4 | **Test isolation.** Training code requests a row in the test period | Refused; a test evaluation without an access row is refused |
| MLK-5 | **Planted signal.** Synthetic returns depend on a known feature | The pipeline's validation skill exceeds the baselines, and the contributions name that feature |
| MLK-6 | **Pure noise.** Labels independent of every feature | No model passes gate 5; reported skill is within the bootstrap interval of chance |
| MLK-7 | **Label shuffle.** Labels permuted within date | Skill collapses to chance |
| MLK-8 | **Stacking.** A combiner offered in-sample component predictions | Refused: only out-of-fold predictions are admissible |
| MLK-9 | **Determinism.** Train twice in the recorded environment | Identical artifact hash and predictions |
| MLK-10 | **Registry immutability.** UPDATE, DELETE, TRUNCATE and illegal transitions for every role | All refused |
| MLK-11 | **Gate.** A synthetic dataset, an undeclared protocol, a `provisional` policy, a failed reliability check | Each alone blocks `validated` |
| MLK-12 | **Dependency boundary.** Capture, backfill and financial-truth packages | Import no ML library; ML packages import no CSE transport |
| MLK-13 | **Domain.** A prediction row outside the training envelope | `blocked: outside_validated_domain` |
| MLK-14 | **Artifact integrity.** One byte of an artifact changed | The loader refuses |
| MLK-15 | **Inherited leakage.** The Phase 3 and Phase 4 leakage plants (ML-1 to ML-13, FL-1 to FL-10) applied below a built dataset | The dataset at T, and predictions from it, are unchanged |

---

## 14. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0009)

| ID | Decision | Reason |
|---|---|---|
| P5-D1 | Models are adapters behind one interface; v1 has `ref`, `calibrated_engine`, `linear`, `gbt` | MA §12.2 mandates no algorithm; a linear model and boosted trees are the standard, inspectable pair for tabular data of this size |
| P5-D2 | Direction is three classes (`rise`, `flat`, `fall`) | Illiquid securities often have a zero return; folding `flat` into either side would distort both probabilities |
| P5-D3 | v1 ML models take features, not engine scores | Keeps engines and ML separable for MA §13 and §31 |
| P5-D4 | ML dependencies in `requirements-ml.txt` and separate packages | The staged build's dependency set is unchanged (MA §43.1) |
| P5-D5 | One final-test access per registered candidate version | MA §36; the ledger makes the count visible |
| P5-D6 | Sufficiency minimums: 756 sessions, 4 folds, 2 000 validation rows per fold, 50 securities; provisional | A stated floor is better than none; to be revised with data |
| P5-D7 | No model may become `validated` on synthetic data or under a `provisional` eligibility policy | Honest status; the software can be frozen, the models cannot be trusted yet |
| P5-D8 | The metrics library is shared with Phases 9 and 10 | One definition of every number |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P5-R1 | Any promotion to `production` (the designation) | Live Route 1 predictions |
| P5-R2 | The final test period of each real protocol | The first real training |
| P5-R3 | Acceptance of the ML dependency set into a deployable build, with the re-staging MA §43.1 requires ("the ML and data-processing dependencies of later phases") | Deployment |
| P5-R4 | A waiver for a dataset whose survivorship is not `complete` | Gate 7, if history comes from a survivors-only source (Phase 3, P3-R1) |

---

## 15. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| ML-1 | `worker/evaluation_metrics/` | Every metric against hand-computed cases |
| ML-2 | Dataset builder, manifest, version | Determinism; MLK-15 |
| ML-3 | Fold objects, purge and embargo, protocol and test-access logic (pure parts) | MLK-1 to MLK-4 |
| ML-4 | Adapters `ref`, `calibrated_engine`, `linear`, `gbt`; dependency pinning | MLK-5 to MLK-7, MLK-9, MLK-12 |
| ML-5 | Calibration, conformal intervals, contributions | Reliability and coverage on synthetic data with known truth |
| ML-6 | Migration 0021, registry store, artifact store, gates, wrapper, runbook | PostgreSQL 17 in CI; MLK-10, MLK-11, MLK-14 |
| ML-7 | Combination and `r1_predictions`; refusal states | MLK-8, MLK-13 |
| ML-8 | An end-to-end synthetic walk-forward, as a rehearsal recorded like R-F4's runbook rehearsal | The full chain from a synthetic archive to gated models, reproducible |

Phase 5 needs Phase 3 complete and Phase 4's F7-1 to DE-3. ML-1 has no dependency and can start at once.

---

## 16. What stays open until real data exists

- Whether anything beats the parameter-free baselines on CSE data. It may not, especially at the one-day horizon and
  for thinly traded securities, and the design treats that as a legitimate result: Route 1 then reports the baselines
  with honest uncertainty or refuses.
- The sufficiency minimums, regime bands, tolerances and the envelope margin.
- How much history exists at all, and of what kind (Phase 3, P3-R1). With forward capture only, the first
  `sufficient` protocol is about three years after the first production capture.
- Whether the `reconstructed` and `point_in_time` eras behave alike; if not, models trained on one may not transfer.

## 17. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new layer above MF, F7 and DE: datasets, models, registry, Route 1 predictions |
| 2 | Source of truth | The stored, hashed rows of the layers below; ML's own append-only registry |
| 3 | Information at its timestamp | Each row's T; each label's resolution instant; folds defined on both |
| 4 | Provenance retained | Source row hashes, dataset version, artifact hashes, combination version, per prediction |
| 5 | Frozen components | None modified |
| 6 | Migration | Yes: 0021, additive |
| 7 | Leakage prevention | Purged walk-forward, fit-inside-fold, the declared test period and access ledger, out-of-fold stacking, MLK-1 to MLK-15 |
| 8 | Testing | §13; synthetic chains through the lower layers' factories; PostgreSQL 17 in CI |
| 9 | Reproduction | Dataset version, seed, environment tuple, artifact hash; retraining reproduces the hash in the recorded environment |
| 10 | Route 1 and Route 2 | This is Route 1. Route 2 is never a component, and never replaces it |
| 11 | Later evaluation | The shared metrics library; Phase 9 scores `r1_predictions` against MF's targets |
| 12 | After downtime | No prediction is back-filled as if made on time: a row's T is when its inputs were cut, and a late run is labelled by its real T |
| 13 | Failure representation | Prediction states and reasons (§11.2); components recorded as absent with reasons |
| 14 | Versions | Dataset, protocol, model, combination, environment and code versions on every row |
| 15 | Grounded reasoning | Stored per-feature contributions and component outputs (§8.3, §10) |

## 18. Audit record

*(Filled in by the audit under C-0005.)*
