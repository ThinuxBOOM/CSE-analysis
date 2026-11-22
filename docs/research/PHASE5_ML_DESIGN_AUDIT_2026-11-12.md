# Phase 5 ML design review — audit findings

**Date:** 2026-11-12  
**Status:** Review findings recorded; not an acceptance or implementation authorization  
**Target branch:** `research/tradeiq-phase3-validation-2026-11-12`  
**Design reviewed:** `integration/cloud:docs/PHASE5_ML_DESIGN.md` (revision 3; blob `c9cdef0b562a4431e0283bded706437dfd0a2639`)  
**Upstream design references checked:** Phase 3 revision 5 (blob `a03336459f29d9c521c0280a6f1a6d25a1e37221`); Phase 4 revision 6 (blob `531b9447aa915a6e050e320313661737040644b2`); Master Architecture on `integration/cloud` (blob `bf95fa8aedf5ed9529109f09f23f8ca1bd9de9d4`).

## 1. Purpose and limits

This report records six issues identified while reviewing the Phase 5 revision 3 design against its stated upstream design dependencies and the Master Architecture.

This is a repository review record, **not** a fresh-context formal audit, a delegated acceptance, an owner decision, or evidence that any proposed correction has been implemented or tested. The Phase 5 design remains design-only and unaccepted. This report does not authorize implementation, real-data training, CSE contact, deployment, or changes to `main`.

The report is intentionally added to the current TradeIQ research branch as a separate artifact. It does not modify the Phase 5 design or the Phase 3/4 design documents on `integration/cloud`.

## 2. Findings summary

| ID | Severity / disposition | Finding |
|---|---|---|
| P5-A01 | Major | Phase 5 scoring policy omits Phase 3's `unresolvable_missing_price` label state. |
| P5-A02 | Major | The `calibrated_engine` adapter contradicts the v1 adapter input rule that says ML adapters consume MF/F7 features, not engine scores. |
| P5-A03 | Major | Phase 5's governing references point to stale Phase 3 and Phase 4 revisions. |
| P5-A04 | Major | Phase 5's inherited leakage-test coverage references obsolete Phase 3/4 test ranges and omits newly added upstream tests. |
| P5-A05 | Blocker to design acceptance | Route 1 can emit a combination without any deterministic-engine component, conflicting with the Master Architecture's Route 1 definition. This requires an owner decision. |
| P5-A06 | Major review item; not yet established as a defect | Statistical assumptions for the stationary bootstrap and split-conformal intervals need explicit validation against time-series and cross-sectional dependence. |

---

## 3. Detailed findings

### P5-A01 — Missing Phase 3 label state in the Phase 5 scoring policy

**Severity:** Major  
**Evidence:**
- Phase 5 revision 3, §6, defines the scoring policy and enumerates the label states handled by training, headline metrics, and supplementary reporting.
- The current Phase 3 revision 5, §11, defines `unresolvable_missing_price`. It applies when the reference or target price is undefined, or when a required within-horizon return for the volatility label is undefined. No label value is stored for this state.
- Phase 5 §6 does not assign this state a scoring disposition.

**Why it matters:** The Phase 5 policy is intended to be the shared, versioned scoring policy used by Phases 5, 9, and 10. An upstream label state without an explicit disposition leaves the training population, headline-metric population, and exclusion accounting underspecified. It also risks inconsistent behavior between the model phase and later evaluation/backtesting phases.

**Required resolution:**
1. Add `unresolvable_missing_price` explicitly to the scoring-policy table.
2. State whether it is excluded from training and headline metrics (the expected default given that no label value exists).
3. Require counts by horizon, period, and relevant strata so exclusions remain visible.
4. State whether a sensitivity analysis is meaningful. Do not invent a synthetic return or class for a label with no defined price; if no defensible sensitivity exists, say so explicitly.
5. Add a test proving the state cannot silently enter training or headline metrics and that its count is reported.

**Acceptance evidence:** Updated scoring-policy text, a versioned policy change, and tests covering all three outputs and all four horizons.

### P5-A02 — `calibrated_engine` adapter contradicts the v1 feature-input rule

**Severity:** Major  
**Evidence:**
- Phase 5 §7.2 defines `calibrated_engine` as mapping a Phase 4 engine score `s` to direction/return outputs.
- Immediately below the adapter table, Phase 5 states: “v1 ML adapters take MF and F7 features, not engine scores” (P5-D3).
- The `calibrated_engine` definition therefore consumes precisely the engine score that the stated v1 adapter input rule excludes.

**Why it matters:** The interface and boundary of an adapter are not unambiguous. It is unclear whether `calibrated_engine` is an ML adapter, a calibration wrapper around a deterministic engine, or an explicit exception to P5-D3. That affects feature schemas, artifact identity, contribution/explanation semantics, data access boundaries, and test coverage.

**Required resolution:** Choose and document one coherent interpretation:
- **Option A:** Classify `calibrated_engine` as a distinct engine-calibration component, not an MF/F7-input ML adapter, and define its interface, registry kind, lifecycle, calibration data, and contribution semantics separately; or
- **Option B:** Explicitly amend P5-D3 to identify `calibrated_engine` as a narrowly scoped exception, with the permitted engine-score input and its provenance/versioning rules.

Do not leave the current blanket rule and adapter table in conflict.

**Acceptance evidence:** One consistent adapter taxonomy and input contract, reflected in the registry, dataset boundary, tests, and explanatory text.

### P5-A03 — Stale upstream design revision references

**Severity:** Major  
**Evidence:**
- Phase 5 revision 3's governing-records paragraph identifies Phase 3 revision 3 and Phase 4 revision 3 as the controlling upstream designs.
- The current Phase 3 design on `integration/cloud` is revision 5.
- The current Phase 4 design on `integration/cloud` is revision 6.
- The later revisions add or change material upstream behavior, including Phase 3's missing-price label state and Phase 4's FL-21/FL-22 cases.

**Why it matters:** Phase 5's dataset, labels, financial features, engine inputs, and inherited tests depend on the upstream contracts. Stale revision references make it unclear which exact upstream definitions Phase 5 consumes and can conceal changes that invalidate assumptions made by the older design revisions.

**Required resolution:**
1. Reconcile every Phase 3 and Phase 4 reference in Phase 5, not merely the revision numbers in the introductory paragraph.
2. Check the actual referenced contracts: tables/columns, label states, target-build semantics, feature schemas, engine outputs, quality states, cutoffs, and test obligations.
3. Pin the intended upstream revisions or immutable commit identifiers in the design.
4. Record any Phase 3/4 changes that Phase 5 intentionally does not consume, with a reason.

**Acceptance evidence:** A complete dependency-reference reconciliation, with no operative stale references and a documented compatibility statement.

### P5-A04 — Inherited leakage-test matrix is out of date

**Severity:** Major  
**Evidence:**
- Phase 5 §13's MLK-20 describes inherited coverage as Phase 3 ML-1 through ML-23 and Phase 4 FL-1 through FL-20.
- Current Phase 3 revision 5 includes ML-24, covering missing-price / unknown-price continuity protection associated with the newer price-unknown and label-state rules.
- Current Phase 4 revision 6 includes FL-21 and FL-22:
  - **FL-21:** corporate actions not yet entered at the prediction cutoff, including the no-trade-since-document case and the resulting untrusted EPS valuation.
  - **FL-22:** conflicting or too-old fiscal-year EPS handling.

**Why it matters:** Phase 5's inherited leakage test claims do not cover the complete current upstream test matrices. In particular, a Phase 5 dataset or prediction path could appear to preserve older leakage guarantees while failing to demonstrate that newer missing-price continuity and point-in-time corporate-action/fiscal-EPS protections survive dataset construction.

**Required resolution:**
1. Update the inherited test references to include Phase 3 ML-24 and Phase 4 FL-21/FL-22.
2. Define the Phase 5-level fixture or integration test that carries each upstream condition through dataset construction and prediction.
3. Verify that a Phase 5 dataset at cutoff T and predictions derived from it remain unchanged by later-arriving information, while preserving the upstream refusal/untrusted states.
4. Reconcile any further changes discovered during the full upstream-reference audit under P5-A03.

**Acceptance evidence:** Updated MLK-20 (or replacement test mapping), explicit coverage for ML-24/FL-21/FL-22, and executable integration tests.

### P5-A05 — Route 1 can lack a deterministic-engine component

**Severity:** Blocker to design acceptance; owner decision required  
**Evidence:**
- The Master Architecture defines Route 1 as deterministic engines plus ML.
- Phase 5 §2 acknowledges that its combination mechanism does not guarantee an engine-derived component. A combination may contain only components that passed their gates and may contain no engine-derived component; the resulting prediction is flagged `no_engine_component`.
- Phase 5 §10 allows that condition to flow through the combination/designation mechanism.

**Why it matters:** The document describes a valid Route 1 prediction that does not satisfy the architecture's stated composition of Route 1. A warning flag records the deviation but does not itself make the output conform to the architecture. This is a product/architecture policy choice, not something an implementation team should silently decide.

**Required owner decision (not made by this report):**
- **Strict Route 1:** require at least one eligible, gated deterministic-engine component in every Route 1 combination; refuse or withhold the Route 1 prediction when none is available.
- **Explicit fallback policy:** permit ML-only output under a separately named and governed mode, clearly distinguished from canonical Route 1, with its own user-facing status, eligibility, designation, and acceptance criteria.

The owner must choose. The report does not select an option on the owner's behalf.

**Acceptance evidence:** An explicit owner decision recorded in the decision log and matching changes to the Master Architecture/Phase 5 contract, combination validation, refusal behavior, tests, and output labels.

### P5-A06 — Validate bootstrap and conformal assumptions under dependent financial data

**Severity:** Major review item; not yet established as a defect  
**Evidence:**
- Phase 5 §8.2 uses split-conformal residual quantiles, scale-normalized and class-conditional by liquidity class.
- Phase 5 §9.3 uses a stationary bootstrap of the date-level model-minus-baseline loss series, with expected block length based on `max(h, 10, data-driven estimate)`.
- Phase 5 §12.3 reports date-level bootstrap intervals and acknowledges that the number of blocks may be small.
- Phase 5 MLK-8 includes synthetic size tests with cross-sectional date shocks, overlapping-label autocorrelation, and volatility clustering, but the design still needs to establish that these tests validate the assumptions required by the actual reported uncertainty and interval claims.

**Why it matters:** Financial observations can be dependent both through time and across securities on the same date. A date-level aggregate can preserve common date shocks for a portfolio-level statistic, but that alone does not establish valid coverage for every security, liquidity class, regime, or conditional prediction interval. Likewise, ordinary split-conformal guarantees rely on exchangeability assumptions that are not automatically satisfied by non-stationary, serially dependent financial time series.

This finding is a request for validation, not a claim that the selected methods are necessarily invalid.

**Required resolution:**
1. State the exact estimands for which the stationary-bootstrap intervals and conformal intervals claim coverage.
2. Explain which dependence structures are handled by the date aggregation and block resampling, and which are not.
3. Extend simulation tests to vary temporal dependence, cross-sectional dependence, volatility clustering, regime changes, unbalanced security coverage, and liquidity-class composition.
4. Measure empirical coverage and type-I error at the actual minimum sample sizes and under the declared family-wise multiplicity.
5. If the intended coverage is not supported, narrow the claim or evaluate alternatives such as longer blocks, date-cluster methods, rolling/blocked calibration, or other time-series-aware procedures. Any alternative must be justified and tested rather than selected by convention.
6. Report effective independent block counts and mark results unreliable when the evidence is insufficient.

**Acceptance evidence:** A documented statistical rationale, simulation results at realistic sample sizes, and explicit limits on the claims made by the resulting metrics and intervals.

---

## 4. Recommended disposition order

1. **Resolve P5-A05 first:** it is an owner-level architecture decision and affects what a Route 1 prediction is allowed to mean.
2. **Resolve P5-A01 and P5-A02:** they affect core scoring and adapter contracts.
3. **Complete P5-A03 and P5-A04 together:** reconcile upstream revisions and then close the inherited-test mapping against the reconciled contracts.
4. **Resolve P5-A06 before treating uncertainty/coverage claims as accepted:** the design should demonstrate the statistical properties it claims, or narrow those claims.

These are review recommendations, not an implementation schedule or authorization.

## 5. Repository and scope record

At the time this report was prepared, the target branch comparison showed:

- Target: `research/tradeiq-phase3-validation-2026-11-12`
- Base: `main` at `33377421381f2f95a2ed1933ac78a7fa24ec1ad0`
- Comparison: 8 commits ahead, 0 behind
- Existing changed paths versus `main`: the two CI workflow files, the TradeIQ research report, and the TradeIQ archive audit script.

This report is a new, separate file on the target branch. No Phase 5, Phase 3, Phase 4, or Master Architecture source document was edited as part of recording these findings. No merge to `main`, implementation, CI execution, or acceptance is implied.
