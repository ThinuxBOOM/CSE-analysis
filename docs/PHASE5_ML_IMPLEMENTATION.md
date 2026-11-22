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
| `tests/test_m51_unit.py` | 11 tests (8 at this step; 3 added by its audit), every metric against a hand-computed case; MLK-8; MLK-22 | §13 |

Choices made inside the design's words, for the audit:
- Floats with `math.fsum` and fixed orders, not Decimal: these are statistics, not stored prices.
- The predicted class breaks ties in the order rise, flat, fall.
- ECE is the mean over the three classes of the one-vs-rest, count-weighted calibration gap, 10 equal-width bins.
- The data-driven block length is Politis and White (2004) with the Patton, Politis and White (2009) correction.
- **MLK-8 in CI uses B = 199** so that it runs in seconds. At this step it used 200 seeds; the audit raised the
  single test to the design's 400 seeds (the family check stays at 200). The acceptance region is the binomial 0.1%
  to 99.9% range for the number of tested seeds.

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

## M5-2: the dataset builder, manifest, version and strata (part 1: the pure builder)

**Status: ACCEPTED on the branch (C-0090: re-audit reported no Blocker and no Major).**

| File | Content | Design |
|---|---|---|
| `worker/ml_dataset/dataset.py` | `Manifest` (configurations, range, eras with mode and H, the pinned target build, cutoff rule, row filter, admissible states, withheld test periods, regime policy); `build()`: one snapshot per session by the cutoff rule, market-eligible lines only, MF, F7 and DE features with nulls that keep state and reason, labels apart from features from one target build, labels of withheld test periods not built, the worst label class, survivorship and coverage, a report that counts every exclusion; `version()` | §4.1 to §4.4, §5.4 |
| `worker/ml_dataset/strata.py` | Liquidity terciles of `turnover_avg_21` in the same-T eligible universe (ties share a class); the trend tag from `mkt_ret_ew_21` against fixed bands; the volatility tag from the cross-sectional median of `vol_21` within its own earlier sessions; model-age strata; `RegimePolicy` (`regime.0`, provisional) | §4.1, §12.2, §5.6 |
| `tests/test_m52_unit.py` | 26 tests (14 at part 1; the rest added by later parts and audits): determinism and order-freedom; the version bound to every source and label hash; labels apart; the cutoff rule; exclusions counted; withheld labels; MLK-17; MLK-18; MLK-20 (nulls with their reasons, label states passed through, a label resolved by T refused, rows at T unchanged by later plants); the MLK-24 refusals of a dataset (two H values, `basis_not_raw`) and the era, mode and configuration refusals | §13 |

Choices made inside the design's words, for the audit:
- The cutoff rule's instant is a time of day in Colombo (default 23:59:59); among the snapshots of a session at or
  before it, the latest T wins, ties by snapshot id.
- A label whose `resolved_at` is not after its row's T is refused, not used: it would be a leak.
- Features are named by family (`mf.`, `f7.`, `de.`); F7's financial eligibility is a categorical feature;
  a line with no issuer link at T has `f7.eligibility` null with reason `issuer_link_absent` and no other F7 value.
- The volatility tag's trailing distribution is the medians of the dataset's own earlier sessions (at most 1 260),
  `undefined` below 252.
- **Gap (C-0087):** an MF snapshot does not store its market aggregates (`mkt_ret_ew_21`), so the trend tag is
  `undefined` with reason `market_aggregate_not_stored` until it does. The builder already takes them when given.
- A dataset without rows carries no label class, survivorship or coverage (None), never a claim of `complete`.

**Part 2, the loader** (`worker/ml_dataset/loader.py`): one read-only, repeatable-read transaction over
`mf_snapshots` and `mf_snapshot_rows`, `de_runs` and `de_outputs`, `f7_feature_rows`, `mf_target_rows` (the pinned
build only) and, for survivorship, the import headers and bar dates. Choices: a snapshot's DE run is the latest under
the manifest's F7 and DE configurations; a line's F7 features are exactly the rows the run's outputs cite (the
issuer's and the line's own); its industry group comes from an issuer engine's universe when that engine applies;
survivorship is `complete` for a snapshot that read no imported history, else the worst of the raw import batches
admitted by its governing time that hold a bar in the 400 days before the session (the reach of the longest feature
window). A snapshot without a matching DE run loads without F7 and DE inputs and is counted.
`tests/test_m52_loader_postgres.py` (9 tests now; 3 at part 2, on DE-4's PostgreSQL world plus two target builds): the dataset carries
the run's F7 rows and 15 engine scores per line, the pinned build's labels only, label states as stored, and is the
same twice; a run under other configurations is not used; survivorship from an in-reach import, not an older one; a
snapshot past its session's cutoff rule is not used.

Mutation check (14 mutants, among them liquidity over ineligible lines, a version without labels, non-admissible
values kept, a second build, withheld labels built, a session's own median in its history, the cutoff rule off, the
earliest snapshot chosen, the best label carried, a trend tag from a later session): all killed; three survived the
first test set and were closed by `test_mlk17_strata_use_the_eligible_same_t_universe_and_only_earlier_sessions`.

---

## First M5-2 audit (fresh-context agent, at 38a203c)

One Blocker, six Majors, minors. All Blocker and Major findings are fixed or recorded:

- **B1** the pinned build's header was never read: an AVAILABLE build, another H or another label definition could
  label a dataset, and its labels' R_h would be on another era's clock. Now the loader reads `mf_target_builds` and
  `build()` requires the header: its id, `target_definition_version`, MF configuration, mode and H must answer the
  manifest, and every era must be on the build's own clock. Consequence (C-0089): **a dataset is single-mode** until
  MF's targets give labels each era's own clock; a mixed manifest is refused, not mislabelled.
- **M1** a session's snapshots of another mode or H made the build fail, by snapshot-id order: now set aside before
  the cutoff rule and counted (`snapshots_other_mode_or_h`). A CURRENT snapshot is still refused.
- **M2** the cutoff rule had no instant for the AVAILABLE clock: each era now carries its own instant (default the
  last microsecond of D, Colombo), inclusive; the boundary, an earlier instant and the tie-break are tested.
- **M3** labels of rows just before a withheld test period carried test-period prices: a label whose R_h reaches the
  period (or a pending one while a period starts after T) is not built, and counted (`labels_reaching_test_period`).
- **M4** a snapshot without a DE run had its lines labelled "no link": now `no_engine_run` on F7 eligibility and on
  every engine, counted in the report; a line in link conflict says `issuer_link_conflict`.
- **M5** survivorship reads the import headers and bar dates, outside §3's list: recorded as a departure (C-0089)
  until MF stores the survivorship it admitted; the rule now uses MF's own admission filter (`raw`, `row_absent`).
- **M6** tests now kill every mutant the audit listed (20 builder and loader mutants, each failing a test), the
  age-stratum test asserts real bins, and the loader is tested in AVAILABLE. MLK-20 through real stored rows: F7's
  `incomplete_lines` nulls reach the dataset with their reason. **Carried Major:** MLK-20 with every MF (ML-1 to
  ML-24) and Phase 4 (FL-1 to FL-22) plant run through to a built dataset, which needs the full stack end to end;
  planned with M5-8's rehearsal, before `main`.

Minors fixed: liquidity uses the manifest's admissible states; the version binds T, snapshot id, row metadata and
the build header; duplicate labels refused; a line's own F7 row must have been computed on the same MF row.
Carried: the latest DE run is not pinned in the manifest (a rebuild after a re-run changes the version, which
fails closed); the volatility tag's history is the dataset's own sessions.

---

## M5-2 re-audit (fresh-context agent, at 5f51794): accepted

No Blocker and no Major: every first-audit fix holds (an AVAILABLE dataset with a matching build was built end to
end), no leakage into features, metadata, strata or labels, and every loader join is keyed by snapshot, run or build.
50 mutants; the seven that changed results are now killed by new tests (the id tie-break in both orders, the
volatility median from admissible eligible values only, `unknown` survivorship when no batch is in reach, the build
header's mode and H read from the database, the link state read from DE's evidence). Minors fixed: the manifest's
shape is checked (ordered dates, eras inside the range and not overlapping, zone-free cutoff times, aware ordered
withheld periods); a snapshot that read imported history marks its unlinked lines `captured_only`; the dataset
version binds the builder's code version (`ml_dataset.2`). **Carried to M5-6:** withheld test periods are given by
the caller until the registry exists; M5-6 must read the declared, unspent periods itself (§4.1, MLK-4). Also carried
(C-0089): MLK-20 with every upstream plant, at M5-8.

---

## M5-3: folds and slices

**Status: ACCEPTED on the branch (C-0049: audit reported no Blocker; Majors fixed).**

| File | Content | Design |
|---|---|---|
| `worker/ml_protocol/folds.py` | `Calendar` (session arithmetic), `Fold` (training, validation, `resolved_late`, unused with reasons), `Slices` (fit, select, calibrate, conformal, with purge and embargo at each boundary), `walk_forward` (expanding folds), `FitScope` (MLK-3) | §5.2, §5.3 (revision 5) |
| `tests/test_m53_unit.py` | 6 tests now (5 at this step): MLK-1 (in a session-index era, a datetime era and across two eras; expanding folds never validate a row twice), refusals of malformed folds and slices, MLK-2 (a date memoriser: chance under the protocol in all 40 histories, skill without the purge), MLK-3 | §13 |

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

---

## M5-4: adapters and baselines (part 1: `ref`, `linear`, `gbt`)

**Status: part 1 ACCEPTED on the branch (C-0092: first audit reported no Blocker; its Majors fixed).** Part 2,
`calibrated_engine`, is below (accepted, C-0095).

| File | Content | Design |
|---|---|---|
| `.github/ci/requirements-ml.txt` (and `requirements-ml.txt`, which includes it) | Pins: numpy 2.5.3, scipy 1.16.3, scikit-learn 1.7.2, LightGBM 4.6.0, joblib 1.5.2, threadpoolctl 3.6.0 | §7.2 |
| `.github/ci/linux.Dockerfile` | Installs them (binary wheels; `libgomp1` for LightGBM) at build time, since tests run without network; the build fails unless they import. `docs/CI_TEST_STRATEGY.md` updated | §15 |
| `worker/ml_models/data.py` | The schema (MF and F7 features only, never `de.*`), the matrix (nulls as NaN, categories one-hot), targets of the trained states only (`scoring.1`), `train_set` through `FitScope` (MLK-3) | §4.4, §6, §7.2 |
| `worker/ml_models/artifact.py` | Canonical JSON artifacts, SHA-256 identity, integrity and adapter checks (MLK-16), the environment tuple, 9-decimal quantisation | §7.1 |
| `worker/ml_models/baselines.py` | The `ref` adapter: all six baselines of §7.3, silent where they have no method | §7.3 |
| `worker/ml_models/linear.py` | Multinomial logistic regression and ridge; median imputation with missing indicators and standardisation fitted on the fit slice; prediction from the artifact text alone; contributions coefficient x standardised value | §7.2 |
| `worker/ml_models/gbt.py` | LightGBM: one thread, deterministic, no sampling, fixed seed; LightGBM's text model in the artifact; exact tree contributions | §7.2 |
| `tests/test_m54_unit.py` | 12 tests now (8 at part 1): MLK-11 and MLK-16 for both adapters; a planted signal beats `base_prior` and shuffled labels give no skill (MLK-9); no engine score in the schema; MLK-3; contributions add up; every baseline by hand; MLK-14 | §13 |

Choices made inside the design's words, for the audit: the volatility floor is a configuration constant (default 0);
direction contributions are reported for the predicted class; `base_interval` uses the split-conformal order
statistic ceil((n + 1) x level) at level 0.9 by default; `linear` and `gbt` refuse a fit slice without all three
direction classes. Mutation check: 11 mutants (engine scores admitted, no fit scope, integrity unchecked, no
smoothing, zero momentum not silent, the h = 1 factor, the conformal index, unscaled contributions, no missing
indicators, a nondeterministic sampler, no volatility floor): all killed. **Correction (first audit):** that set was
too narrow, and the claim overstated the coverage: 16 further mutants survived it (see below). **Not verified here:** the CI image build
with the new libraries (Docker is not available in the cloud container); the next CI run is its first build.

---

## First M5-4 audit (fresh-context agent, at 8c37e2e): part 1 accepted

No Blocker: the adapters match §6, §7.2 and §7.3 in every result checked; contributions add up; artifacts are
canonical and checked; hashes were identical at 1 and 8 threads; the real import graph meets MLK-14. Three Majors,
all fixed:

- **M1** the interface did not say or enforce which slice each part is fitted on. Now a `Train` records its slice and
  only `train_set` (through a `FitScope`) can make one an adapter accepts; `linear` and `gbt` refuse anything but the
  `fit` slice, the `ref` adapter anything but the fold's `training` rows (§7.3: `base_prior` uses all of them); each
  artifact records its slice.
- **M2** 16 important rules were untested (training only on `resolved` and `resolved_carried`; the volatility
  state; the schema from the admitted rows only; median imputation and standardisation; direction contributions of
  the predicted class in both adapters; LightGBM's sampling, thread and determinism parameters; `linear`'s
  volatility floor; the interval's centre; `allow_nan`; the scope and slice checks; the tie rule). Each now has a
  test that fails when it is reverted.
- **M3** MLK-9 now shuffles within each date and across dates, on data with a common shock per date, over three
  seeds, for both adapters.

Minors fixed: a label without `volatility_state` is refused; BLAS and OpenMP pools are limited to one thread in fit
and predict (`threadpoolctl`), and the environment tuple records the BLAS; `linear` contributions carry the
intercept, so base plus contributions equal the raw score; both adapters check the matrix's column count; the
classes follow Phase 4's tie rule (a rise-fall tie is no class); LightGBM's seeded bin-construction sample is stated;
MLK-14 is a separate static test (`tests/test_m54_static.py`) that runs without the libraries, on Windows too, over
a wider list. **Carried:** `describe` has no clipping bounds or validated envelope yet (M5-7 defines the envelope);
the expected-hash check of an artifact against the registry is M5-6's (MLK-16); hash-pinned wheels; recording the
ML library versions in CI's environment report.

---

## M5-4 part 2: `calibrated_engine`

**Status: ACCEPTED on the branch (C-0095: audit reported no Blocker; its Majors fixed). With part 1, M5-4 is
complete.**

| File | Content | Design |
|---|---|---|
| `worker/ml_dataset/` (M5-2 addition; `ml_dataset.3`) | For each scored DE output: `de.<engine>.full` (the engine used every component its registry entry has) and `de.<engine>.parts` (each present component's share, contribution / total weight, adding up to the score), read from the stored output, whose hash the dataset already binds; nothing for an unscored output; never in the null counts or an ML schema | §7.2 |
| `worker/ml_models/calibrated_engine.py` | One engine's calibration: a multinomial logistic map on (s, s²) and a linear map on s, fitted on the fit slice's full-set rows only (nothing to select: an effectively unregularised map); silent where the engine has no score or missed a component; no volatility; provenance names the engine, its version and the registry; contributions are the engine's components, share x the map's slope at s, plus the intercept, adding up to the raw logit or return exactly | §7.2, P5-D3 |
| `tests/test_m54_engine_unit.py` | 4 tests: fits only full-set rows (partial rows carry contradicting labels) and beats `base_prior`; silent on partial or unscored rows; contributions are the engine's components and add up; provenance, determinism, refusals (unknown engine, wrong slice, another adapter's artifact) | §13 |

Also: a dataset unit test of the new fields and a PostgreSQL check that `full` and the shares match the stored
outputs (both cases occur in the test world). Mutation check: 7 mutants (partial rows fitted, contributions without
the slope or unscaled, no s² term, detail for an unscored output, `full` always true, unnormalised shares): all
killed. **Correction (audit):** the "partial rows fitted" mutant covered only the shared path; fitting the direction
map alone on partial rows survived (M2, below).

**Audit (fresh-context agent, at d3294be): no Blocker.** Fixed: **M1** `full` and the engine version came from the
running registry, not from the stored output, so a changed registry could turn partial-set rows full without the
dataset version changing. Now `full`, the shares and the engine version all come from the stored output alone (whose
hash the dataset binds; `ml_dataset.4`): `full` when every positively weighted component it lists is present; the
adapter refuses rows of mixed versions or of a version other than the registry's, and records the data's.
**M2** the direction map is now checked against an explicit fit on full-set rows only. Minors: contributions rescale
the quantised shares to add up to s (to floating-point precision; the claim "exactly" is withdrawn); weight-0
components never count toward a full set, with a `_detail` unit test (weight 0, `n_below_min`, an own-value absence)
and the PostgreSQL check aligned; the only hyperparameter is `engine_id`. Each fix fails a test when reverted.

---

## M5-5: calibration, intervals, contributions

**Status: ACCEPTED on the branch (C-0096: audit reported no Blocker; its Majors fixed).**

| File | Content | Design |
|---|---|---|
| `worker/ml_models/calibration.py` | Vector scaling on the calibrate slice (a scale and an offset per class on the log-probabilities, offsets summing to zero; L-BFGS from the identity); the calibrated probabilities beside the raw ones; reliability tables and ECE overall and per liquidity class; `reliability_ok` for `calibrated_engine` (its map is the calibration). Split-conformal intervals: scale max(vol_21 x sqrt(h), floor), the floor the 5th percentile of the fit slice's positive scales, a null `vol_21` taking the floor and flagged; the quantile of the scaled residuals per liquidity class on the conformal slice; a class with too few rows for the level gets no interval and a flag. The coverage report as §8.2 claims it: rows inside, averaged over dates, per class. Every fitted part admitted through a FitScope of its slice | §8.1, §8.2 |
| `tests/test_m55_unit.py` | 12 tests now (5 at this step) on synthetic data with known truth: vector scaling halves the ECE of an overconfident, biased model on held-out rows, in every class, and lowers log loss; class-conditional intervals within 3 points of 90% in every class where one pooled quantile undercovers the wide class; the floor, the null flag, too few rows, slice refusals; coverage averaged over dates, not rows; **MLK-7**: a planted signal of stated size (0.5 at this step; 0.15 after the audit, below) passes the gate test (level 0.05, delta 0, B = 9 999) in at least 4 of 5 seeds, and the contributions name the planted feature in all 5 | §13 |

Contributions (§8.3): the adapters already store base plus contributions equal to the raw score (M5-4); the
calibrated value is stored beside it and not decomposed. Mutation check: 10 mutants (unconstrained offsets, a pooled
quantile, unscaled residuals, the floor's percentile, the null flag, the conformal index, a finite interval from too
few rows, coverage by rows, no slice check, a scale-free map): all killed; one survived the first test set (coverage
by rows) and was closed by `test_i3`.

**Audit (fresh-context agent, at 9c31ead): no Blocker; slices and §8.1, §8.2 as specified.** The record's mutant claim
covered a different set: 10 further mutants survived (M3). Fixed:
- **M1** `reliability_ok` passed on no evidence: now an empty report, or an overall group below 50 rows, fails;
  groups below 50 rows are reported, never judged; `reliability_report` can admit its rows through the calibrate
  slice's scope and reports each group's count.
- **M2** the coverage report dropped rows and classes without an interval and lacked §8.2's statements: every class
  with labelled rows now appears, with `no_interval`, the nominal level and tolerance, the number of independent label
  windows (dates / h), a `coarse` mark below 30, and `within_tolerance` (false when any row had no interval).
- **M3** tests now pin each of the audit's surviving rules (untrained labels in the calibrate and conformal slices,
  the NaN-label filter, the scale v x sqrt(h), the floor for positive and for zero volatility, per-class scales,
  every large group judged, an `unknown` class of its own, inclusive bounds, the empty report, a class without
  intervals): each fails its test when reverted.
- Minors fixed: lengths of predictions and probabilities checked against the rows; non-finite predictions refused
  (fit) or flagged (predict); the level validated; the horizon checked when applying an artifact; probability
  vectors validated; a non-converged scaling refused, a negative scale flagged (`scale_negative`); **MLK-7** now at
  size 0.15 (power below certainty) with a no-signal control that rejects in at most 1 of 5 seeds, and `gbt` names
  the planted feature too. **Carried to M5-7:** storing the calibrated value beside the raw one in `r1_predictions`,
  with the class the contributions explain (the raw class, which calibration can change).

---

## M5-6: the registry (parts 1 to 3: migration 0021, the rules, the store)

**Status: parts 1 to 4 built; the audit of parts 1 to 3 is running.** Part 1 the database, part 2 the rules in
application code, part 3 the store, part 4 the command line, wrapper and runbook (below).

`supabase/migrations_pending/0021_ml_registry.sql` (after 0019 and 0020, outside the chain until the owner's merge
decision): the eighteen tables of §9.1, every one append-only for every role; artifacts as base64 text checked
against their SHA-256 and a 32 MiB cap. What the database enforces (§9.3's left column, §5.4): legal first events
and transitions in sequence; `validated` only with a passing validation gate report for the model and a dataset that
is not synthetic (gate 1); a protocol declared before its first run, by the database clock; test periods pairwise
disjoint (an exclusion constraint), their timing (prospective or retrospective) set by the database clock, one access
each, only by the protocol that declared it; a production model only through the owner path, promoted from a
validated version with the same artifact, at most once, its first event `production` with a passing promotion report
for the source and a designation naming it in the same transaction (checked at commit); waivers, incident flags and
designations owner-only.

**Part 2, the rules in application code** (`worker/ml_registry/`): `sufficiency.py` (§5.5's minimums, every unmet
one named; revision 5's test period of at least max(126, 8h, 20L) dates); `gates.py` (gates 2 to 8 and 9 to 12 into
a report with each gate's result, reason and evidence, the labels the model carries, and the multiplicity used).
`tests/test_m56_gates_unit.py` (4): every sufficiency minimum alone; **MLK-13**, each gate alone blocking, both
branches of gate 5 (a baseline not beaten; `size_not_shown`), the engine baselines of a `no_engine_component`
combination, and each label a designation must cite. **A design inconsistency, for the owner:** §5.5's worked table
(test period 148 dates at h = 1, 210 at h = 21) predates revision 5's 20-block rule, which needs at least 200 and 420;
the code follows the rule, so the table's example schedules are `exploratory`.

**Part 3, the store** (`worker/ml_registry/store.py`): registrations (content-addressed configurations, datasets,
protocols with their sufficiency and their test periods timed by the database clock, `previously_scored` when
retrospective or when a registered dataset reaches into the period), runs, events, trials, fold models with their
artifacts, metrics, models with their first event; `validate` (gates 2 to 8 and the event in one transaction, the
database re-checking gate 1 and the report); `transition`; the one `access`; the owner's `promote` (the production
copy, its designation with every label and the lifetime access count, and its first event in one owner-path
transaction); `alerts` (a production copy of a later-rejected version, reported, never retired automatically). Test
periods: `withheld_for` and `check_manifest` close **M5-2's carry**: a dataset for training or validation that does
not withhold every unspent test period it touches is refused (MLK-4); `check_rows_outside_unspent` (MLK-5);
`multiplicity` follows the sessions (MLK-5). `tests/test_m56_store_postgres.py` (6), each rule failing a test when
reverted (8 mutants).

Tests: `tests/test_m56_migration_postgres.py` (8: the chain and append-only; configuration, protocol and artifact
identities, MLK-16 in the database; disjoint, clock-timed, once-accessed test periods; the protocol before its runs;
the lifecycle, gate 1 and the gate report; the owner path and its designation at commit; a validated source and its
promotion report; owner-only decisions), `tests/test_m56_migration_static.py` (2).

**Part 4, the command line** (`worker/ml_registry/cli.py`, `preflight.py`, `ops/bin/cse-ml`,
`docs/ops/P5_ML_REGISTRY.md`): `preflight`, `status`, `verify` (every artifact, configuration and declaration hash
re-proved in Python), `alerts` and `transition` as the worker; the owner acts `waive`, `flag` and `promote` on the
owner path. **`store.promotion_evidence`** reads gates 9 to 12's evidence from the registry: whether the protocol's
one access named this model and whether it passed, the protocol's declared point-in-time share, every label the model
carries (its passing validation report's, the access period's `retrospective` and `previously_scored`,
`rollforward_of`) and the lifetime access count. The owner's evidence file may state only what the registry cannot
know yet (the Phase 10 backtest and the validation's point-in-time share); any other key is refused. Tests:
`tests/test_m56_cli_postgres.py` (6), `tests/test_m56_ops_static.py` (4). Mutation check: 7 mutants on the evidence
(an access of another nominee, a pass without being named, the period's labels, the file claiming registry facts,
the declared share, the validation labels, the owner path): all killed; 4 survived the first test set and were
closed by `test_c6`.

## First M5-6 audit (fresh-context agent, at 6678ca5): parts 1 to 3 accepted

No Blocker (C-0037): append-only everywhere, the lifecycle, gate 1, the run clock, disjoint and clock-timed test
periods with one access, the owner-only production row, MLK-16 and §5.5's sufficiency verified (43 mutants, 31
killed). Ten Majors, all fixed; each fix fails a test when reverted (`tests/test_m56_audit_postgres.py` and the
updated store, migration, CLI and gate tests):
- **M1** a test period could be added to a protocol after its runs: now only in the protocol's own transaction (0021).
- **M2** every ledger time and writer was caller-settable: `ml_stamp_guard()` on all eighteen tables (MLK-6's
  "supplied earlier timestamp").
- **M3** a test period inside the protocol's own validation folds was accepted: the declaration states
  `embargo_sessions` and P_start must lie after the last V_end plus that many days (a necessary condition; the exact
  session count is the protocol builder's, M5-8).
- **M4** an access accepted a candidate, an out-of-family nominee and a non-hex hash: all refused (MLK-4).
- **M5** a failure after looking left the period unspent: the access row is committed first; a failure is recorded
  as the result and the period stays spent.
- **M6** the worker could retire a production model: owner only (0021), with an owner `retire` command.
- **M7** the worker could mark a policy validated: a validated configuration is the owner's (0021; `validate-policy`);
  gate 6 reads each declared policy's status from the registry; a policy whose evidence cutoff is not before the
  first V_start counts toward the multiplicity (MLK-6's second half).
- **M8** gate 5 read only `state` and `rejects`: a result now carries its level and whether it was a simulation, and
  counts only at alpha/m for the registry's m, with B and L at the design's floors.
- **M9** `validate` took registry facts from the caller: `registry_facts` supplies sufficiency (the primary horizon's;
  any other is `exploratory`, minor 14), family membership, folds, level, multiplicity and policy statuses. Promotion
  evidence comes from the registry (part 4).
- **M10** `check_manifest` had no caller: `register_dataset` and `start_run` both apply it (a period declared after a
  dataset was registered refuses its next run).
Minors fixed: 11 (a production copy equals its source; the designation matches its output and horizon), 12 (gate 4
needs at least one declared fold), 14 (above), 15 (the result is a metrics row; the access row stays append-only).
Recorded, not changed: 13 (a version is promoted at most once ever, stricter than §9.2's once per purpose); 16 (fold
`grace` = 21 equals the target policy's today; binding it to the pinned build is M5-8's); 17 (design §5.5's worked
table predates revision 5's 20-block rule; for the owner's next design revision). The twelve surviving mutants of
the audit each now have a test. Mutation check of the fixes: 22 mutants (each Major's rule, gate 5's three
conditions, minors 11, 12 and 14): all killed; 2 survived the first test set (an out-of-family nominee, which
`validate` cannot produce, now tested by a hand-validated model; gate 4 with no folds) and were closed. **Status:
M5-6 is ready for its re-audit.**

**Re-audit (fresh-context agent, at 9622e3b): ACCEPTED (C-0101).** No Blocker; each of the ten fixes holds (each
reverted fix fails a test). Seven new Majors, to be fixed before `main` (task "M5-6 re-audit Majors"): **A** a negative
`embargo_sessions` bypasses the embargo check; **B** an access result can be rewritten afterwards through
`record_metrics`; **C** `ml_model_events.actor` is not stamped; **D** gate 7 still takes survivorship and the waiver
from the caller; **E** gate 6 passes when a declaration lists no policies; **F** a designation list can name any model;
**G** `reconstructed_evidence` is not derived from `evidence_label_class`. Eleven Minors and eleven surviving mutants
are listed in the re-audit report and carried with the task.

**Re-audit Majors fixed (C-0104):** **A** `embargo_sessions` must be a whole number of at least 1 (a bool is refused);
**B** `record_metrics` refuses test-period metrics and a unique index keeps one result per access, so a result can
never be rewritten; `promotion_evidence` reads that one row; **C** `actor` joins the stamped writer columns; **D**
gate 7 reads the dataset's survivorship and an owner waiver of kind survivorship naming the dataset version
(`covers_json["datasets"]`); **E** a declaration must list its policies; **F** a designation may list, besides the new
copy, only production models of its output and horizon whose latest status is production (in `promote` and, at
commit, in the database); **G** `reconstructed_evidence` is derived from `evidence_label_class` (reconstructed or
mixed), and a reconstructed-only model may not state a point-in-time share. Minors fixed: promotion reports are
owner-only; `promote` derives its evidence itself (the owner's statement is all it takes); alpha in (0, 0.05]; the
family's shape validated; a result can never overwrite its access id; a unique index on `promoted_from`; a
retrospective period is `previously_scored` (database check); `validate-policy` refuses a changed cutoff; `retire`
is for production models; a malformed declaration never blocks `multiplicity`. Not changed: the designation's
`recorded_at = now()` check (near-equivalent, as the re-audit says). Tests: `tests/test_m56_reaudit_postgres.py` (9)
and updates; every test declaration now lists an owner-validated policy. Mutation check: 18 mutants (each Major,
both sides of F, the minors): all killed; one survived the first test set (the stored access id) and was closed. The
re-audit's surviving mutants each have a test (stamps of `created_by` and `approved_by`, the replicates floor at
large m, alpha from the registry, the policy cutoff boundary, `rollforward_of`, overlap in the multiplicity).

---

## M5-7: combinations, designation lists, the validated domain, `r1_predictions`

**Status: built; audit pending.**

| File | Content | Design |
|---|---|---|
| `worker/ml_route1/combine.py` | `combine_mean_v1`: the mean of the components' calibrated probabilities with its own vector scaling on the log of the mean (a direction-only component never enters a probability mean); the mean of point estimates; its own split-conformal quantile. Fitted only on out-of-fold component predictions of earlier folds, made by their own fold models, resolved by the combiner's cutoff (MLK-10: in-sample, same or later fold, final-fit or late-resolving predictions are refused, never dropped); fold 1 has none. A combination needs all its components, in its order | §10 |
| `worker/ml_route1/routing.py` | The designation in force at a cutoff (MLK-19); a row goes to the first position whose components are all available; metrics per position on the rows routed to it only (MLK-21) | §10 |
| `worker/ml_route1/domain.py` | The validated domain (label classes, eligibility policy and schema feature versions, horizon, industry groups, the envelope from the fit slice's 0.1st to 99.9th percentile widened by a margin, critical and rank features, maximum age, the C-0039 waiver) and §11.2's refusal table in a fixed order: `invalid_input`, `not_applicable`, `insufficient_data` (not eligible; no routed combination; a critical feature null or inadmissible), `blocked` (outside the envelope; a changed policy or schema feature version, an unrelated new feature never blocks; label class or industry group outside the domain without the waiver; stale; an incident flag covering T recorded at or before T); non-critical features clipped | §11.2 |
| `worker/ml_route1/record.py` | The §11.1 record (every part required: outputs, components, the full set of engine outputs, source and artifact hashes, the row's and the evidence's label class, the code version; each direction component keeps its raw and calibrated probabilities and the class its contributions explain, **the M5-5 carry**), its canonical form and row hash; the worker's append-only write to `r1_predictions`; the designation in force read from `ml_designations` | §11.1 |

Tests: `tests/test_m57_unit.py` (9: MLK-10, MLK-15 (every row of the table, the hindsight rule, rank features, the
waiver, clipping, the age limit), MLK-19, MLK-21, the combiner's direction scaling and conformal index, the record and
its hash), `tests/test_m57_postgres.py` (2: a written prediction is append-only and its hash reproduces from the stored
record; MLK-19 against `ml_designations`). Mutation check: 20 mutants, all killed (2 after the first test set).
**Not here, by design:** running the refusal logic inside validation and the test (it is called by the protocol
driver, M5-8), and the per-feature critical list and margin, which a configuration states.


## M5-7 audit (fresh-context agent, at `d70ff9d`): accepted

No Blocker (C-0037), so M5-7 is accepted. The audit found six Majors, all fixed (C-0110):

| ID | Finding | Fix |
|---|---|---|
| M1 | The combined interval was one pooled quantile of the raw \|y − mean\|, not §8.2's; with two classes of different volatility, coverage was 1.000 against 0.805 at a nominal 0.9 | §8.2's rule in the combiner: scale = max(vol_21·√h, floor), with the floor at the 5th percentile of the fitted rows' positive scales, and one quantile per liquidity class (`RowMeta`). A test checks that each class covers about 0.9 |
| M2 | The fitted combiner was a plain dict, not bound to a horizon or fold | An `Artifact` (hashed) carrying horizon, fold and cutoff; `apply` refuses another horizon or another fold's predictions; `fit_final` is the final combiner |
| M3 | The domain failed open: a critical feature with no envelope, or an empty group list, admitted everything | `Domain` refuses construction without an envelope for every critical non-rank feature; an empty group list admits nothing; `decide` takes the horizon and raises on another |
| M4 | Nothing checked a component's kind or gates, and `no_engine_component` was never derived | `Component(kind, gated)` and `check_components`: Route 2, baselines and ungated configurations are refused; the record requires each component's kind and derives `no_engine_component` |
| M5 | No validation path ran the refusals; a later-recorded incident gave no flag | `incident_recorded_later` flag; `refusal_rates` by regime; M5-8's rehearsal runs `decide` on every validation row and reports the rates by regime |
| M6 | `r1_predictions` accepted a row whose hash or columns contradicted its record | `r1_prediction_guard` in 0021: row_hash = SHA-256 of record_json, the columns are the record's key and state, and the designation's horizon is the row's |

**Minors fixed:**
- Versions are checked before the envelope.
- Incident ranges are half-open, as `covers` is.
- The evidence flag follows the actual class: `reconstructed_evidence` for reconstructed or mixed evidence, `<class>_evidence` for any other non-point-in-time class.
- Canonical form: -0.0 = 0.0 and Decimal scale are normalised, non-text keys are refused, and an unknown type is refused.
- Record validation by state:
  - a predicted row needs outputs, components, engines, artifact hashes and the mf, f7 and de source keys;
  - any refusal needs a reason.
- The combiner:
  - refuses non-convergence, invalid probabilities and duplicate predictions;
  - has `fit_final`;
  - records the domain's `training_cutoff`.

**Minors open:**
- `decide` takes the model's age from the caller rather than computing it from a calendar.
- Turning a designation's model ids into `Position`s is left to the production pipeline.

**Mutation survivors (9), each now killed by a named test:**
- the logarithm in the scaling: a hand-computed softmax;
- the sum-to-zero offsets;
- the three-class check;
- the waiver applying to classes other than point_in_time;
- clipping of critical features;
- the routed position's value;
- hash rounding;
- `sort_keys`;
- a predicted row with no outputs.

Tests now:
- `test_m57_unit.py`: 18;
- `test_m57_postgres.py`: 3 (p3: every tampered column and the hash refused; a designation for another horizon refused).

---

## M5-8: the end-to-end synthetic walk-forward rehearsal

Design §15's last step. **Exit:** the whole chain, reproducible.

| Module | What it does |
|---|---|
| `worker/evaluation_metrics/size.py` | MLK-23. The dependence of the date-level series is estimated on training rows only: the lag-1 autocorrelation of the series and of its squares. Null series of the protocol's length are simulated (AR(1) with ARCH-like variance) and the gate test is run on each at the protocol's level and block length. The rejection count is compared with the binomial acceptance region (0.1 % to 99.9 %). `shown` is gate 5's `size_shown` |
| `worker/ml_rehearsal/rehearsal.py` | One synthetic world: a planted signal in `mf.x`, an irrelevant `mf.z`, a persistent common date factor with calm and stress regimes, and three liquidity classes with their volatilities. It runs dataset → 5 expanding walk-forward folds and inner slices → `linear` on each fold's fit slice → vector scaling and §8.2 conformal intervals → the domain from the fit slice and §11.2's refusals on every validation row, with two incident flags (one recorded before the days it covers, one long after) → per-fold metrics, coverage and reliability → the gate test of the pooled date-level loss differences against `base_prior` → the size study → `report_hash`. `record` writes it to the registry as a synthetic dataset and validates it with the rehearsal's own evidence |

**Recorded result** (default world, seed 1, 200 size seeds):
- The gate test rejects: p = 0.0001 over 323 dates (two incident-blocked days have no scored row), block 10, B 9 999.
- The size study is shown: 14 rejections out of 200 at the training rows' dependence (φ ≈ 0.20), inside [2, 21]. *(Superseded by the audit fixes below.)*
- Fold coverage is about 0.9 per class. `report_hash` `ac22048d…`.
- Refusals in validation: `outside_validated_domain` 8, `incident` 48, `incident_recorded_later` 72. By regime, the stress days are all predicted, and the incidents fall on calm days.
- In the registry the protocol is `exploratory` (24 securities < 50, a 106-date fit slice < 504, no test dates), so validation writes a failing report. A `validated` event written directly is refused by the database's gate 1 (synthetic).

**Tests:**
- `tests/test_m58_unit.py` (5):
  - the same report and hash twice, another seed a different hash;
  - each fold refitted, with coverage near nominal;
  - MLK-7 through the protocol: three worlds with no signal are not rejected, the world with a signal is;
  - MLK-15 counts and rates by regime;
  - the size study: the binomial region's edges, φ estimated, an iid series shown, a method that ignores strong dependence (blocks of 1) not shown, a series too short to test not shown.
- `tests/test_m58_postgres.py` (1): the rehearsal recorded (dataset synthetic; five fold models with their artifacts reloaded by hash; metrics; run events; a failing report), gate 1 at the database, and the caller's evidence unable to override the registry's facts.

**The carried MLK-20 Major** is closed at the dataset by `test_l9` in `tests/test_m52_loader_postgres.py`. Every kind of upstream row the loader reads, recorded after the dataset was built, changes no row, label, report or version:
- an import batch with bars in the feature window (ML-9);
- an unentered candidate action (the `candidate_event` source);
- a later target build (ML-18);
- a later issuer link decision (ML-10, FL-11);
- a snapshot beyond the manifest's range (ML-1, ML-6).

The plants ML-1 to ML-24 and FL-1 to FL-22 remain shown at their own stored rows by MF's and F7/DE's suites (`test_mf1_unit` to `test_mf8_*`, `test_f7_*`, `test_de4_*`). The dataset is a function of those rows and of the build's own instants. A run of every plant from raw captured data through MF, F7 and DE to a dataset needs the full stack with captured data, which is the owner's staging run (ST-33).

**Not here:**
- a combination inside the rehearsal (the world has one model kind; the combiner is tested in M5-7);
- the test-period access (the rehearsal declares a test period but never opens it).

## M5-8 audit (fresh-context agent, at `8c1f5a6`): accepted

No Blocker (C-0037), so M5-8 is accepted (C-0115). The audit also confirmed that all six M5-7 Majors are closed.

| ID | Finding | Fix |
|---|---|---|
| Major 1 | `record()` claimed passes the rehearsal never showed: the leakage suite, reliability and coverage meaning only "not None", a stub domain, ages (0, 70) | Only what was shown is claimed. Gate 3's evidence is False, because the rehearsal runs no leakage suite. Reliability uses ECE ≤ 0.05 and coverage uses §8.2's `within_tolerance`. The real `Domain` is stored with the model, and the real ages are recorded. The PostgreSQL test checks the stored gates |
| Major 2 | The dependence came from training returns, not from the series the gate tests | Estimated on the date-level loss differences against `base_prior`, over fold 1's select, calibrate and conformal slices (out of sample for its fit, all before any validation date; raw probabilities). The test asserts the dates |
| Major 3 | Only one point of MLK-23 was built | `rehearsal.mlk23_grid`: seven cases. Each reports the measured §8.2 coverage per fold, the size study at the case's own dependence, the empirical type-I count over independent null worlds, and `unreliable` |

**Minors fixed:**
- `arch` is estimated on the AR(1) residuals;
- tests kill each surviving mutant that was checked: the region's lower edge, the level, volatility clustering in the simulation and the estimate, the hash binding the report, only predicted rows scored, the ages, the signal rejected across seeds, and the §8.2 floor;
- the recorded size studies use 200 seeds;
- 323 dates, not 325;
- validation predicts from the clipped features;
- `Domain` requires a policy and every schema feature's version.

**Open:** `Component.gated` is stated by the caller, not read from the registry. Destination: the production pipeline,
which builds components from `ml_model_events`.

**Recorded result after the fixes** (default world, seed 1, 200 size seeds):
- `report_hash` `e7e9ac75…`.
- The gate test rejects: p = 0.0001, 323 dates.
- The dependence of the gate's own series is φ = 0.035 and arch = 0.011, estimated over 135 dates (2022-06-07 to 2022-12-26).
- The size study is shown: 12 of 200 rejections, region [2, 21].
- Evaluated ages: 1 to 65 sessions.
- Fold 2's ECE is 0.080, outside the tolerance, so gate 4 fails. Gate 2 also fails, because the protocol is `exploratory`.

**The MLK-23 grid** (no signal; 200 size seeds; 20 independent null worlds per case; `grid_hash` `34b77722…`):

| Case | Coverage within tolerance in every fold | Size study (rejected of 200, region [2, 21]) | Empirical type-I (of 20, region [0, 5]) | Unreliable |
|---|---|---|---|---|
| base | yes | 14, shown | 0 | no |
| serial dependence (persistence 0.8) | **no** (per-class coverage down to 0.83) | **not testable**: block 27 leaves fewer than 20 blocks in 323 dates | 1 | **yes** |
| date factor 1.5 | **no** (fold 3 overall 0.81) | 14, shown | 0 | **yes** |
| volatility clustering 0.6 | yes | 11, shown | 0 | no |
| regime change (volatility doubles at mid-sample) | yes | 14, shown | 0 | no |
| unbalanced coverage (odd securities on 60 % of days) | **no** (a class outside tolerance) | 13, shown | 0 | **yes** |
| liquidity composition (70 % low) | yes | 13, shown | 0 | no |

How to read it:
- **A strong common date factor, or strong serial dependence, breaks the per-row conformal interval's coverage.** That
  is what MLK-23 exists to show. So is a short protocol under strong dependence, which cannot be tested at all.
- **The empirical type-I counts are near 0** because a model fitted to noise is worse than `base_prior`. The null
  worlds' mean loss difference is therefore above 0, so they sit inside H0, not at its boundary. The size study, run
  at the boundary (mean 0), is the type-I measurement that matters, and it is shown in every testable case.
- **The regime change does not move the direction classes**, which depend on the latent variable and not on the
  return's scale. Its effect is on coverage, which stays within tolerance because the scale uses `vol_21`.
- **These worlds are the rehearsal's size**, below §5.5's minimums. A protocol's own minimum sizes are run by passing a
  larger `World` through `cases`. **Before gate 5 is passed on real data**, the grid is run at the real protocol's
  sizes and its result cited (destination: the first real protocol).

## Registry amendments before `main` (C-0116; Phase 11 re-audit N1 to N3)

- **A designation is checked in every transaction.** `ml_designation_list_check`, a deferred constraint trigger on
  `ml_designations` in 0021, runs at commit. Every listed model, and every component named in the labels'
  `components`, must be a production model of the designation's output and horizon, with latest status
  `production`. Before this, the check ran only alongside a production event, so an owner designation of research
  models went unchecked. An empty list designates nothing. Tests that designated placeholder ids now use `[]`.
- **The unspent-interval check by label window.** `check_rows_outside_unspent` accepts `(T, window_end)` and a
  `grace`. A row whose label window meets another protocol's unspent interval is refused.
- **Gate 9 by reference.** `promotion_evidence` follows a roll-forward's `rollforward_of` chain to the version that
  passed its protocol's access, and adds the label `gate9_by_reference:<id>`.
- **`promote_list` and `cse-ml promote-list`.** These promote every version of a list, and its components, in one
  owner transaction with one designation; if any version fails, only the reports are written. The production
  designation check accepts a component named in the designation's labels.
- The preflight now also checks `trg_mlz_list` and the M5-7 guard `trg_r1p_guard`, and their functions are revoked
  from `public`.

Tests: `tests/test_registry_amendments_postgres.py` (4), `tests/test_m56_cli_postgres.py::test_c10`, and the static
tests updated.
---

## Branch-wide review (2026-11-18), Phase 5 findings

Recorded in full in `docs/governance/BRANCH_REVIEW_2026-11-18.md`. Fixed here: **M-1** a FitScope is now bound to
one horizon (`FitScope.of(..., horizon=h)`; `for_horizon` refuses another), used by `train_set`, the calibration and
the reliability report; **m-2** `predict` of `linear`, `gbt` and `calibrated_engine` refuses an artifact fitted for
another horizon. Not fixed, with their destinations: **M-2** `scoring.1`'s `candidate_event` and `era_row_absent`
had no source in the dataset: **fixed** (below); **M-3** CI results recorded in the review; m-1 two tie rules,
m-3 calibration and conformal artifacts not bound to their model, m-5 withheld labels reported as `pending` (M5-7);
m-4 the fold `grace` default (M5-6 audit); m-6 the `ml_models` docstring and version (M5-7); m-7 MLK-7 through the
protocol (M5-8); MLK-23 and MLK-24 have no step in design §15: MLK-23 goes to M5-8 (gates 5 and 9 need it), MLK-24
is covered by M5-2's refusals.

**M-2 fixed: a source for `scoring.1`'s flags** (`ml_dataset.5`). Each dataset label now carries `flags`, from
evidence the loader reads as of the pinned target build's own recording time (`dataset.ScoringEvidence`, bound into
the version with each label's flags; `report["labels_by_flag"]`):
- `candidate_event`: an `unexplained_move_in_horizon` label whose window (s_D, s_F] meets the window of a known but
  unentered bonus or rights issue of the security. Source: a new owner-only, stamped table in pending migration 0019,
  `mf_candidate_actions`, filled by the TradeIQ import from the reconstruction run's new `unentered_actions.csv` (the
  events with a matched symbol and an allotment date that were not reconstructed, with the reconstruction's own search
  window). Events whose company matched no symbol, or without an allotment date, cannot be placed and are not
  candidates: their moves stay `unexplained_genuine`, the conservative side (they keep the sensitivity).
- `era_row_absent`: a `terminated` (left the universe) or `unresolvable_missing_price` label whose absent session lies
  in an admitted raw `row_absent` import batch where `security_complete` does not cover the security and is not after
  its last covered range (after it, the absence is evidence of leaving).
`ml_models.data.label_rows` turns a dataset's labels into `scoring.1` rows with the flags. Tests: three in
`test_m52_unit.py` (each rule's edges; the flags in the dataset, its report, its version and scoring), `t12` and
`t13` in `test_mf7_tradeiq_import.py` (the plan; owner-only and stamped writes; the loader's as-of read).
**Not run here:** the reconstruction script against the archive (it is not in the container); the owner reruns it
before the import.
