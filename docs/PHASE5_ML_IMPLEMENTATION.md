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

## M5-2: the dataset builder, manifest, version and strata (part 1: the pure builder)

**Status: ACCEPTED on the branch (C-0090: re-audit reported no Blocker and no Major).**

| File | Content | Design |
|---|---|---|
| `worker/ml_dataset/dataset.py` | `Manifest` (configurations, range, eras with mode and H, the pinned target build, cutoff rule, row filter, admissible states, withheld test periods, regime policy); `build()`: one snapshot per session by the cutoff rule, market-eligible lines only, MF, F7 and DE features with nulls that keep state and reason, labels apart from features from one target build, labels of withheld test periods not built, the worst label class, survivorship and coverage, a report that counts every exclusion; `version()` | §4.1 to §4.4, §5.4 |
| `worker/ml_dataset/strata.py` | Liquidity terciles of `turnover_avg_21` in the same-T eligible universe (ties share a class); the trend tag from `mkt_ret_ew_21` against fixed bands; the volatility tag from the cross-sectional median of `vol_21` within its own earlier sessions; model-age strata; `RegimePolicy` (`regime.0`, provisional) | §4.1, §12.2, §5.6 |
| `tests/test_m52_unit.py` | 14 tests: determinism and order-freedom; the version bound to every source and label hash; labels apart; the cutoff rule; exclusions counted; withheld labels; MLK-17; MLK-18; MLK-20 (nulls with their reasons, label states passed through, a label resolved by T refused, rows at T unchanged by later plants); the MLK-24 refusals of a dataset (two H values, `basis_not_raw`) and the era, mode and configuration refusals | §13 |

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
`tests/test_m52_loader_postgres.py` (3 tests, on DE-4's PostgreSQL world plus two target builds): the dataset carries
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

## M5-4: adapters and baselines (part 1: `ref`, `linear`, `gbt`)

**Status: built (part 1); audit pending.** Part 2, `calibrated_engine`, follows: it fits only on rows where the
engine used its full component set, which the dataset does not carry yet (a small addition to M5-2's DE features).

| File | Content | Design |
|---|---|---|
| `.github/ci/requirements-ml.txt` (and `requirements-ml.txt`, which includes it) | Pins: numpy 2.5.3, scipy 1.16.3, scikit-learn 1.7.2, LightGBM 4.6.0, joblib 1.5.2, threadpoolctl 3.6.0 | §7.2 |
| `.github/ci/linux.Dockerfile` | Installs them (binary wheels; `libgomp1` for LightGBM) at build time, since tests run without network; the build fails unless they import. `docs/CI_TEST_STRATEGY.md` updated | §15 |
| `worker/ml_models/data.py` | The schema (MF and F7 features only, never `de.*`), the matrix (nulls as NaN, categories one-hot), targets of the trained states only (`scoring.1`), `train_set` through `FitScope` (MLK-3) | §4.4, §6, §7.2 |
| `worker/ml_models/artifact.py` | Canonical JSON artifacts, SHA-256 identity, integrity and adapter checks (MLK-16), the environment tuple, 9-decimal quantisation | §7.1 |
| `worker/ml_models/baselines.py` | The `ref` adapter: all six baselines of §7.3, silent where they have no method | §7.3 |
| `worker/ml_models/linear.py` | Multinomial logistic regression and ridge; median imputation with missing indicators and standardisation fitted on the fit slice; prediction from the artifact text alone; contributions coefficient x standardised value | §7.2 |
| `worker/ml_models/gbt.py` | LightGBM: one thread, deterministic, no sampling, fixed seed; LightGBM's text model in the artifact; exact tree contributions | §7.2 |
| `tests/test_m54_unit.py` | 8 tests: MLK-11 and MLK-16 for both adapters; a planted signal beats `base_prior` and shuffled labels give no skill (MLK-9); no engine score in the schema; MLK-3; contributions add up; every baseline by hand; MLK-14 | §13 |

Choices made inside the design's words, for the audit: the volatility floor is a configuration constant (default 0);
direction contributions are reported for the predicted class; `base_interval` uses the split-conformal order
statistic ceil((n + 1) x level) at level 0.9 by default; `linear` and `gbt` refuse a fit slice without all three
direction classes. Mutation check: 11 mutants (engine scores admitted, no fit scope, integrity unchecked, no
smoothing, zero momentum not silent, the h = 1 factor, the conformal index, unscaled contributions, no missing
indicators, a nondeterministic sampler, no volatility floor): all killed. **Not verified here:** the CI image build
with the new libraries (Docker is not available in the cloud container); the next CI run is its first build.

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
