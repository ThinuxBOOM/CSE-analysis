# Phase 4: financial features and deterministic engines (design)

**Status: DESIGN, revision 1 (2026-11-11). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact and no change to `main`.

**Governing records:** Master Architecture (MA) §2, §6, §9 to §14, §22, §39, §41, §42, §55 (Phase 4), §56 to §59, §68;
`docs/F8_DESIGN.md` (§7, §8, §14: the as-of contract and the consumers' rules); `docs/F6.2_DESIGN.md` (the economic
fact and its states); `docs/PHASE3_MARKET_FEATURES_DESIGN.md` (market bars, universe, market features, targets). Where
this document and those differ, they win.

**Short names.** F7 is the financial feature layer (the name `docs/F8_DESIGN.md` §14 already uses). DE is the
deterministic engine layer. Steps are F7-1 to F7-4 and DE-1 to DE-4 (§14). Decisions are P4-D*n* ([delegated]) and
P4-R*n* (reserved) (§13).

---

## 1. Conclusion

```
F6.4 economic facts ──F8 as_of──▶ F7 fundamental panel ──▶ F7 financial features ──┐
                                                                                    ├─▶ DE engines ─▶ signals ─▶ baselines
P2/P3 archive ──MF (Phase 3)──▶ market bars, market features ──────────────────────┘
```

1. **F7 reads financial truth only through F8**, in `KNOWN` for live work and `AVAILABLE` for backfilled eras, and
   stores each `result_hash` (the contract F8 set for it). It never reads F5 candidates, F6.4 tables or
   `report_filings` directly.
2. **F7 derives; it never repairs.** Trailing-twelve-month values, single-quarter values, averages and ratios are
   features with stated formulas. A `conflicting` fact is never resolved by F7, an `unlabelled` scope is never guessed,
   currencies are never converted, and a missing value is never zero (MA §9).
3. **An engine is a pure, versioned function from features to a structured signal** with its evidence. It contains no
   fitted parameter. Engines are "first-class baselines, not fallbacks" (MA §13): their outputs are kept beside ML's,
   never merged away.
4. **A signal is not a forecast.** Turning a signal into MA §4's prediction contract (probability of rise, expected
   return, range) needs a calibration fitted on history. That is a fitted object, so it belongs to Phase 5's training
   discipline and model registry. Phase 4 ships the signals and three **parameter-free reference baselines** that need
   no fitting (§9), so that Phase 5 and Phase 9 always have something honest to compare against.
5. **It is buildable now and unvalidatable now.** The only economic facts ever produced are 321 facts for one issuer,
   from a replay whose evidence is since lost (MA §52). Every threshold here is labelled `provisional`, and real-data
   questions are listed in §15.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Where met |
|---|---|---|
| Fundamental engine families: growth, EPS, margins, ROE/ROA, leverage, liquidity, cash flow, working capital, dividends, valuation, balance-sheet strength | §12.1, §39 | §6, §8 |
| Market/statistical engine families: momentum, trend, volatility, volume, turnover, drawdown, mean reversion, correlations, market-relative; sector-relative | §12.1 | §8 (sector-relative deferred with Phase 3's P3-D6) |
| Each engine produces structured, versioned outputs | §12.1 | §7 |
| Component outputs retained, not only the final number; engines are first-class baselines | §13 | §7, §9, §10 |
| Reasoning grounded in the actual inputs | §14 | §7.2 |
| No silent sign, scale or currency transformation; nil is never zero; conflicts preserved; Group/Company/Bank never silently merged | §9 | §4, §5 |
| Financial eligibility by measurable criteria, versioned, empirically tested | §6, §22 | §11 |
| Point-in-time and leakage tests | §11, §57 | §4.1, §12 |
| Feature source, formula, units, availability rule, version, quality state | §39 | §6.1 |
| Provenance forecast → feature → economic fact → source observation → filing → CSE source | §42 | §10 |

---

## 3. Boundary

**F7 reads:** `worker.financial_asof.api` (`as_of`, `timeline`, `explain`) and nothing else of the financial truth
layer; `issuer_securities` (0007) to join an issuer to its securities; `financial_concepts` (0008) for a concept's
family, period kind, value type, natural sign and industries.

**DE reads:** F7's features and MF's snapshots (Phase 3). Nothing else.

**Neither** contacts a network, writes outside its own tables, modifies a frozen component, reads a mutable table, or
calls F8 in `CURRENT` mode on a point-in-time path (F8's `require_point_in_time` guard is applied to every result).

**Not owned:** the financial truth itself (F6, F8), market features and targets (Phase 3), fitted calibrations and
the Route 1 combination (Phase 5), reasoning text for the user (Phase 8).

---

## 4. F7-1: the fundamental panel as of T

### 4.1 The query

For an issuer at information cutoff T (and horizon H in `AVAILABLE`), F7 calls `as_of` once for all facts and keeps:
for each fact its identity (`concept_key`, `period_kind`, `period_end`, `duration_months`, `scope`, `operations`,
`maturity`, `currency`), its state, its representative normalized value and half-unit, its availability, and the
result's `result_hash`, `f8_configuration_id` and rule versions.

- The mode is chosen per pipeline, exactly as `docs/F8_DESIGN.md` §14 lays down: live `KNOWN`; backfilled eras
  `AVAILABLE`, labelled `reconstructed`. F7 passes the label through to every feature and never upgrades it.
- A dataset over many dates uses `timeline`, which is point-in-time only.
- The financial-data cutoff of a forecast (MA §11) is this T. It may differ from the market cutoff's D\* (Phase 3
  §4.3); both are recorded, and the forecast's information cutoff is the later instant with each layer queried at it.

### 4.2 Which facts are usable

| Fact state (F6.3 / F8) | F7 treatment |
|---|---|
| `single_source`, `corroborated` | Usable. The representative normalized value is used; the state is carried into the feature |
| `conflicting` | **Not usable.** Every feature that needs it is `conflicting` with a null value. F7 never picks a side (MA §9; `docs/F8_DESIGN.md` §14) |
| `none` (nothing visible at T) | The fact is absent: features needing it are `missing` |
| Value kind not numeric (for example a printed nil) | Kept as nil. A nil is not zero. A feature whose formula needs a number there is `missing`, with reason `printed_nil` |
| Availability flag `availability_unknown` | Usable in `KNOWN` (the system held it); excluded in `AVAILABLE`, where availability is the whole question. F8 already applies this; F7 does not second-guess it |

### 4.3 Scope (Group, Company, Bank)

Facts of different scopes are different facts and are never mixed in one formula.

- The **scope policy** `f7.scope.1` picks one scope per issuer per T: `group` if the issuer has a usable group fact of
  the anchor concept (`profit_for_period`, else `total_assets`) for its latest period; otherwise `bank`; otherwise
  `company`.
- The chosen scope is recorded on the panel and on every feature. A change of scope between two dates of an issuer's
  timeline is flagged `scope_changed`, and no growth feature is computed across it.
- `unlabelled` scope is never chosen. An issuer with only `unlabelled` facts has no panel (`blocked: scope_unlabelled`).
- `operations`: `total_or_unstated` is used. `continuing` and `discontinued` facts are kept in the panel for Phase 8's
  reasoning but enter no v1 formula.

### 4.4 Currency

All facts in one formula must share one currency. A feature that would combine two is `missing` with reason
`currency_mismatch`. Valuation features (§6.3) also need the fact currency to equal the price currency (LKR); an issuer
reporting in another currency gets no valuation feature in v1. No FX conversion exists in this phase (MA §9).

### 4.5 Sign

F6 keeps every value with its printed sign. `financial_concepts.natural_sign` documents what the concept is, and F5
never changes a sign. Formulas need a consistent orientation, so F7 applies exactly one explicit, versioned rule and
records each use:

- **`f7.sign.1`.** For a concept whose `natural_sign` is `expense` or `outflow`, the formula uses the magnitude of the
  printed value, and the feature records `sign_rule: magnitude` with the printed sign beside it. For `income`,
  `balance` and `as_printed` concepts the printed sign is used unchanged.
- This is a stated transformation inside a derived feature. The fact is untouched, and the rule is visible in the
  feature's provenance, so it is not the silent transformation MA §9 forbids.
- An `expense` concept printed with the opposite sign to its issuer's other periods is flagged
  `sign_convention_unstable`; features using it are `unlabelled`.

---

## 5. F7-2: period arithmetic

Interim statements report cumulative durations (3, 6, 9 or 12 months) and sometimes single quarters. F7 derives
comparable period values by rules with stable ids. Each derived value records the rule, the `ef_key`s it used and
their states.

With D_n(E) the duration fact of n months ending on E, and E−k the date k months before E (month arithmetic on the
period end, month-end preserved):

| Rule | Derives | From |
|---|---|---|
| `F7P-1` TTM, direct | TTM(E) | D_12(E) |
| `F7P-2` TTM, by cumulative periods | TTM(E) | D_n(E) + D_12(E−n) − D_n(E−12), for n ∈ {3, 6, 9}, where D_12(E−n) is the 12-month fact ending exactly n months earlier |
| `F7P-3` TTM, by quarters | TTM(E) | D_3(E) + D_3(E−3) + D_3(E−6) + D_3(E−9) |
| `F7P-4` single quarter | Q(E) | D_3(E), or D_n(E) − D_{n−3}(E−3) for n ∈ {6, 9, 12} |
| `F7P-5` average balance | AVG(E) | (I(E) + I(E−12)) / 2 for an instant fact I |
| `F7P-6` year-on-year growth | G(E) | X(E) / X(E−12) − 1, only when X(E−12) > 0; otherwise null with reason `base_not_positive` |

- **More than one path.** When F7P-1, F7P-2 and F7P-3 can each produce TTM(E), all are computed. If they agree within
  the sum of their inputs' half-units, the value is taken from the lowest-numbered rule and marked `corroborated_paths`.
  If they disagree, the value is `conflicting` and every path is kept. F7 does not pick (the project's rule for facts,
  applied to derivations).
- **F7P-2 is valid only when the n-month period starts at the fiscal year's start**, which is exactly what "a 12-month
  fact ends n months before E" establishes. No fiscal-year-end is assumed or read from metadata.
- **Per-share values are not summed across periods.** A TTM of `eps_basic` by F7P-2 or F7P-3 adds amounts divided by
  different share counts. It is produced as `eps_ttm_approx` with that name and the flag `share_count_may_differ`, and
  only F7P-1 gives `eps_fy`.
- Half-units propagate: a sum's uncertainty is the sum of its inputs' half-units, recorded as `abs_uncertainty`. v1
  does not propagate uncertainty through ratios (P4-D5); the inputs' half-units are recorded instead.

**The latest period.** For each concept, E\* is the latest `period_end` with a usable fact at T. Features are computed
at the issuer's **anchor period**: the latest E for which the anchor concept is usable. Concepts whose own E\* is
earlier than the anchor are `stale` for that feature, never silently mixed with a newer period.

---

## 6. F7-3: financial features

### 6.1 The registry

As Phase 3 §10.1, with two more fields: `industries` (`all`, `general`, `bank_finance`: which issuers the formula
applies to, from `financial_concepts.industries`) and `period_rules` (which of F7P-1 to F7P-6 it may use). Arithmetic
is the same fixed-context `Decimal` (Phase 3, P3-D7). Every value carries: scope, currency, anchor period, the
`ef_key`s and rules used, the F8 `result_hash`, the mode label, the quality state, and two ages: days from the anchor
period's end to T, and days from the newest contributing availability instant to T.

### 6.2 Feature set v1 (from the 36 active concepts of vocabulary v1)

| Family | Feature | Definition | Industries |
|---|---|---|---|
| Growth | `rev_g_yoy` | G of TTM `revenue` | general |
| | `gross_income_g_yoy` | G of TTM `gross_income` | bank_finance |
| | `nii_g_yoy` | G of TTM `net_interest_income` | bank_finance |
| | `profit_g_yoy` | G of TTM `profit_attributable_to_owners` (else `profit_for_period`, named in the value) | all |
| | `assets_g_yoy`, `equity_g_yoy` | G of the instant | all |
| | `loans_g_yoy`, `deposits_g_yoy` | G of `loans_and_advances_to_customers`, `customer_deposits` | bank_finance |
| Earnings | `eps_fy`, `eps_ttm_approx` | §5 | all |
| Margins | `gross_margin` | TTM `gross_profit` / TTM `revenue` | general |
| | `operating_margin` | TTM `operating_profit` / TTM `revenue` | general |
| | `net_margin` | TTM `profit_for_period` / TTM `revenue` (general) or / TTM `gross_income` (bank_finance) | all |
| | `nim_proxy` | TTM `net_interest_income` / AVG `total_assets` | bank_finance |
| | `cost_of_risk_proxy` | TTM `impairment_charges` / AVG `loans_and_advances_to_customers` | bank_finance |
| Returns | `roe_avg` | TTM `profit_attributable_to_owners` / AVG `equity_attributable_to_owners` | all |
| | `roe_end` | The same over the period-end balance (used when the prior-year balance is absent; a different id, never a silent substitute) | all |
| | `roa_avg`, `roa_end` | TTM `profit_for_period` / AVG or end `total_assets` | all |
| Leverage | `liabilities_to_equity` | `total_liabilities` / `total_equity` | all |
| | `borrowings_to_equity` | (`interest_bearing_borrowings` current + non-current) / `total_equity` | general |
| | `interest_cover` | TTM `operating_profit` / TTM `finance_costs` | general |
| | `loans_to_deposits` | `loans_and_advances_to_customers` / `customer_deposits` | bank_finance |
| | `equity_to_assets` | `total_equity` / `total_assets` | all |
| Liquidity | `cash_to_assets` | `cash_and_cash_equivalents` / `total_assets` | all |
| | `net_debt` | borrowings − `cash_and_cash_equivalents` | general |
| Cash flow | `cfo_to_profit` | TTM `net_cash_from_operating_activities` / TTM `profit_for_period` | general |
| | `fcf_ttm` | TTM `net_cash_from_operating_activities` − TTM `purchase_of_ppe` | general |
| | `fcf_margin` | `fcf_ttm` / TTM `revenue` | general |
| | `cash_reconciles` | Whether `cash_at_end_of_period` equals `cash_and_cash_equivalents` within half-units (a consistency flag, not a ratio) | all |
| Working capital | `receivables_days` | `trade_and_other_receivables` / TTM `revenue` × 365 | general |
| | `inventory_days` | `inventories` / TTM `cost_of_sales` × 365 | general |
| Dividends | `payout_ratio_cash` | TTM `dividends_paid` / TTM `profit_attributable_to_owners` | all |
| Balance-sheet strength | `nci_share_of_profit` | TTM `profit_attributable_to_nci` / TTM `profit_for_period` | all |
| | `accounting_identity_gap` | (`total_assets` − `total_liabilities` − `total_equity`) / `total_assets`: a quality feature | all |

- The vocabulary has no current assets, current liabilities, or share count. The current ratio, quick ratio and book
  value per share are therefore **not in v1**. They need a vocabulary extension, which is F5 change control (§15).
- `borrowings` requires both maturity facts (`current`, `non_current`) at the same period; one alone gives `partial`.
- A denominator that is zero, negative where the ratio is only meaningful for positive values (equity, profit in
  `payout_ratio_cash`, revenue), or nil gives a null value with a named reason. No clipping, no sign flip.

### 6.3 Valuation features (the join with market data)

Valuation needs a price and a per-share or whole-company basis.

| Feature | Definition | Needs |
|---|---|---|
| `earnings_yield_eps` | `eps_ttm_approx` (or `eps_fy`) / `px_ref` | EPS and price only. Per security |
| `pe_eps` | `px_ref` / EPS, null when EPS ≤ 0 (the yield above stays defined) | As above |
| `market_cap_reported` | CSE's own `marketCap` for the security on D\* | Evidence below |
| `earnings_yield_mcap`, `book_to_market`, `sales_to_market`, `cfo_yield`, `fcf_yield`, `dividend_yield_cash` | TTM profit to owners, owners' equity, revenue, CFO, FCF, dividends paid, each / issuer market value | Issuer market value |

- **Timing.** Price and market value are as of the market cutoff (T, D\*); fundamentals as of the financial cutoff. A
  valuation feature is visible at T only if both sides are.
- **Issuer market value** is the sum of `market_cap_reported` over all of the issuer's securities
  (`issuer_securities`) in the universe on D\*. If any of them lacks a value on D\*, the feature is `partial` and null.
- **Evidence status of `market_cap_reported`: unverified.** CSE's `tradeSummary` rows carry a `marketCap` field (the
  real fixtures under `tests/fixtures/` show it), and the frozen mapper reads it for cross-checks only; it has no
  column. It survives verbatim in `raw_market_observations.raw_payload`, which is append-only, so it can be read
  point-in-time. What is **not** established: whether it is per security or per issuer, whether it is present for
  non-traded securities (P2 requests `companyInfoSummery` only for those), and how it behaves across share-count
  actions. Until that is checked on real captures (§15), every market-value feature carries `input_unverified` and
  eligibility policy v0 does not depend on any of them (P4-D4).
- The EPS-based features do not have that dependency, which is why they are separate ids.

### 6.4 Quality state

Phase 3 §10.3's states and precedence, with the financial causes: `conflicting` (a conflicting fact or disagreeing
derivation paths), `unlabelled` (unstable sign convention), `untrusted` (`input_unverified` market value, where the
policy says so), `missing`, `partial`, `stale` (the anchor period older than the policy's limit, or a concept older
than the anchor), `fresh`.

---

## 7. DE-1: the engine contract

### 7.1 Definition

An engine is a registered pure function:

```
engine_id, version
family            fundamental: growth | profitability | leverage | cash_flow | valuation | quality
                  market: momentum | trend | volatility | liquidity | mean_reversion | market_relative
inputs            feature ids with their minimum quality states
universe_rule     which securities it scores (industries, eligibility)
output            EngineOutput (below)
```

- No fitted parameter, no data-dependent constant, no clock, no randomness. Constants are part of the version.
- Cross-sectional engines (ranks, z-scores) score against the **eligible universe of the same T** (Phase 3 §8, §9).
  They are computed for the whole universe in one pass, and the universe's digest is part of the output's inputs.

### 7.2 EngineOutput

| Field | Meaning |
|---|---|
| `state` | `scored`, `insufficient_inputs`, `not_applicable` (wrong industry), `blocked_quality` |
| `score` | A number in [−1, 1], positive meaning "more favourable on this engine's own definition". Null unless `scored` |
| `components` | Each contributing feature: its id and version, value, quality state, its transformed value (rank or z-score), its weight, its contribution to the score |
| `universe` | The size and digest of the universe scored against |
| `inputs_hash` | Hash of every input feature row hash |
| `evidence` | The ids needed to walk back: MF snapshot row, F7 feature row, F8 `result_hash` |
| `labels` | The mode labels of the inputs (`point_in_time`, `reconstructed`), the worst of them governing |

`components` is what MA §13 and §14 ask for: Route 1's reasoning can say which signals moved the score and cite the
actual numbers. A score is never stored without its components.

### 7.3 Scoring rules common to all engines

- **Transformation.** Each input is turned into a cross-sectional percentile rank in the engine's universe, mapped to
  [−1, 1], oriented so that the favourable direction is positive. Ranks are robust to the heavy tails of ratios and
  need no fitted scale.
- **Missing inputs.** An engine states the minimum set of inputs it needs. Below it: `insufficient_inputs`, no score.
  Above it, the score is the weighted mean over the inputs present, and the missing ones are listed. No imputation.
- **Quality.** `conflicting` and `untrusted` inputs are never used. An engine whose required input is in one of those
  states is `blocked_quality`.
- **Weights** are equal within an engine in v1 (P4-D2). Unequal weights would be a judgement that needs evidence.

---

## 8. DE-2: engine set v1

| Engine | Inputs (favourable direction) | Universe |
|---|---|---|
| `fund_growth` | `rev_g_yoy` or `gross_income_g_yoy` (+), `profit_g_yoy` (+), `assets_g_yoy` (+) | all, by industry input |
| `fund_profitability` | `roe_avg` or `roe_end` (+), `roa_avg` or `roa_end` (+), `net_margin` (+), `operating_margin` (+, general) | all |
| `fund_leverage` | `liabilities_to_equity` (−), `interest_cover` (+), `borrowings_to_equity` (−) | general |
| `fund_bank_strength` | `equity_to_assets` (+), `loans_to_deposits` (−), `cost_of_risk_proxy` (−), `nim_proxy` (+) | bank_finance |
| `fund_cash_flow` | `cfo_to_profit` (+), `fcf_margin` (+) | general |
| `fund_valuation_eps` | `earnings_yield_eps` (+) | all |
| `fund_valuation_mcap` | `book_to_market` (+), `earnings_yield_mcap` (+), `cfo_yield` (+), `dividend_yield_cash` (+) | all; `blocked_quality` while `market_cap_reported` is unverified |
| `fund_quality` | `accounting_identity_gap` (closer to 0 is better), `cash_reconciles`, count of `conflicting` facts in the panel (−) | all |
| `mkt_momentum` | `mom_252_21` (+), `mom_126_21` (+) | market-data eligible |
| `mkt_trend` | `ret_63` (+), `dd_252` (closer to 0 is better) | market-data eligible |
| `mkt_reversal` | `ret_5` (−), `ret_1` (−) | market-data eligible |
| `mkt_volatility` | `vol_63` (−), `vol_parkinson_21` (−) | market-data eligible |
| `mkt_liquidity` | `turnover_avg_21` (+), `amihud_21` (−), `zero_trade_ratio_63` (−) | market-data eligible |
| `mkt_relative` | `rel_ret_21` (+), `rel_ret_63` (+), `beta_126` (reported, weight 0) | market-data eligible |

- Bank and general issuers are ranked within their own industry group for the fundamental engines, because their
  statements are not comparable (a bank's leverage is not a manufacturer's). The group comes from which industry
  concepts the issuer's panel actually contains at T (`bank_finance` if it has usable `net_interest_income` or
  `customer_deposits`), never from a mutable sector label.
- "Favourable" is each engine's own definition and a hypothesis. Whether any of these directions predicts CSE returns
  is exactly what the backtesting laboratory must test. Nothing here asserts that it does.
- Correlation engines and sector-relative engines (MA §12.1) are not in v1: the first needs a design for a
  point-in-time correlation universe that is not just `beta_126`, the second needs a sector source (Phase 3, P3-D6).

---

## 9. DE-3: reference baselines

Phase 5 and Phase 9 need baselines that cannot have been tuned. Each emits MA §4's prediction contract per horizon,
with no fitted parameter:

| Baseline | Probability of rise | Expected return | Note |
|---|---|---|---|
| `base_no_change` | 0.5 | 0 | The random-walk reference |
| `base_rate_252` | The share of the security's own h-session returns that were positive over the 252 sessions ending at D\*, each one fully resolved by D\* | Their mean | Point-in-time by construction: only returns whose end is at or before D\* |
| `base_momentum_sign` | 1 if `mom_252_21` > 0, 0 if < 0, 0.5 otherwise, and declared `uncalibrated` | Null | A directional reference only; its probability is not a calibrated probability and is scored only on direction |

Every engine's signal can also be turned into a forecast by a calibration map. That map is fitted chronologically and
registered as a model in Phase 5 (`kind: calibrated_engine`), so a "deterministic baseline forecast" in Route 1 is
always the pair (engine version, calibration model version). An engine without a registered calibration contributes a
signal and its components, and no probability.

---

## 10. Storage and provenance

### 10.1 Migration `0020_financial_features_and_engines.sql` (additive; append-only, the pattern of 0015 and 0017)

| Table | Contents |
|---|---|
| `f7_configurations` | Canonical JSON and SHA-256: feature registry version, `f7.scope`, `f7.sign`, period rules, `financial_eligibility_policy` and its status, tolerances |
| `f7_panels` | One per (issuer, T, mode, configuration): the F8 `result_hash`, `f8_configuration_id`, chosen scope, anchor period, counts by fact state, label |
| `f7_feature_rows` | One per (panel, security or issuer): the feature vector as canonical JSON with per-value quality, rules and `ef_key`s; `row_hash` |
| `de_configurations` | Engine registry version and constants; canonical JSON and SHA-256 |
| `de_runs` | One per (T, mode, MF snapshot, F7 configuration, DE configuration): universe digest, counts, label |
| `de_outputs` | One per (run, security, engine): the EngineOutput as canonical JSON; `output_hash` |
| `f7_designations`, `de_designations` | Owner-only: the configuration in force per purpose (the F8 pattern) |

Roles and guards as Phase 3 §13.1. No new role and no `SECURITY DEFINER`.

### 10.2 Packages

`worker/financial_features/` (pure: panel, periods, features), `worker/engines/` (pure: registry, scoring, baselines),
`worker/feature_engine_store/` (loader, writer, preflight, `verify`). `verify` recomputes stored rows from F8 and MF
and compares hashes.

### 10.3 The provenance chain

`de_outputs.evidence` → `f7_feature_rows.row_hash` → `ef_key`s and the F8 `result_hash` → F8 `explain` → source
observations, F5 run, classification, filing, listing observations (F6.4's chain); and → `mf_snapshot_rows.row_hash`
→ raw observation ids → run → archived response (Phase 3). That is MA §42's chain, in stored hashes.

### 10.4 Frozen contracts affected

None modified. As Phase 3 §13.3, the migration-enumerating guards need change control at implementation. F8's static
test fixes F8's own package file list, not its callers, so reading F8 from a new package does not touch it; this is to
be confirmed by running the suite at F7-1.

---

## 11. Financial eligibility

The second step of MA §6. A pure function of the panel and a versioned policy, returning a reason and the measured
values, never a bare boolean.

| Criterion (policy v0, `provisional`) | Provisional value |
|---|---|
| A panel exists with a chosen scope | required |
| Fiscal years with a usable anchor fact (12-month, F7P-1) | ≥ 2 |
| Days from the anchor period's end to T | ≤ 270 |
| No `conflicting` fact among the critical concepts at the anchor period (`profit_for_period`, `total_assets`, `total_equity`) | required |
| At least one growth and one return feature not `missing` | required |

Results: `eligible`, `no_panel`, `insufficient_history`, `stale`, `critical_conflict`, `insufficient_features`. They
map onto MA §22's blockers ("insufficient financial history", "stale data", "unresolved critical financial conflict",
"missing critical features"). As in Phase 3, the values are placeholders, labelled `provisional`, and no production
candidate may be trained on them.

---

## 12. Leakage tests

Each builds synthetic economic facts through the F6.4 and F8 test factories (`tests/f64_support.py`,
`tests/f8_factories.py`), computes at T, plants later information, and requires unchanged bytes.

| ID | Planted after T | Must not change |
|---|---|---|
| FL-1 | A filing whose availability is after T | Any F7 feature at T |
| FL-2 | A restatement (a superseding document) available after T | The pre-restatement value at T |
| FL-3 | A document published before T but first known to the system after T | `KNOWN` at T (it changes `AVAILABLE` at T, and the test asserts that too) |
| FL-4 | A later period's fact | The anchor period and every TTM at T |
| FL-5 | A new conflicting observation recorded after T | The fact's state at T |
| FL-6 | A later price | Any valuation feature at T |
| FL-7 | A later security added to the universe | Every cross-sectional rank and engine score at T |
| FL-8 | A `CURRENT` F8 result offered to F7 | Refused by the guard |
| FL-9 | Any target value | No feature or engine output depends on it; `worker/financial_features` and `worker/engines` cannot import Phase 3's target module |
| FL-10 | A later F8 or F7 designation | Results pinned to the configuration in force at T |

As in Phase 3, each test also asserts that the same information planted at or before T **does** change the result.

---

## 13. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0008)

| ID | Decision | Reason |
|---|---|---|
| P4-D1 | Engines emit signals; forecasts need a registered calibration (Phase 5). Phase 4 ships three parameter-free baselines | A probability without a fitted, validated mapping would be invented precision (MA §21, §22) |
| P4-D2 | Equal weights inside an engine; percentile-rank transformation | No evidence exists to justify anything else; ranks need no fitted scale |
| P4-D3 | Sign rule `f7.sign.1`: magnitude for `expense` and `outflow` concepts, recorded per use | Formulas need one orientation; the rule is explicit and versioned, and the fact is untouched |
| P4-D4 | Market-value features are built but flagged `input_unverified`; nothing in policy v0 depends on them | `marketCap`'s semantics are unverified |
| P4-D5 | Half-unit uncertainty is summed through additions and recorded, not propagated through ratios, in v1 | Keeps v1 simple; the inputs' half-units stay available for a later version |
| P4-D6 | Industry group from the concepts present in the panel, not from `companies.sector` | The sector column is mutable and has no history |
| P4-D7 | Scope policy `f7.scope.1` (group, else bank, else company; never `unlabelled`) | Consolidated figures are the usual analytical basis; the choice is recorded per value |
| P4-D8 | Multiple TTM derivation paths are all computed; disagreement is `conflicting` | The project's no-arbitrary-winner rule, applied to derivations |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P4-R1 | Whether to extend the F5 concept vocabulary (current assets and liabilities, share count, dividends per share): F5 change control, re-audit, re-freeze, re-staging | Current ratio, book value per share, dividends per share |
| P4-R2 | Whether to fix the open F3/F5 findings (P-18, P-23, P-1) before the pilot | It decides how many issuers have usable panels |
| P4-R3 | The merge into `main` and the re-staging that follows | Production use |

---

## 14. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| F7-1 | Panel as of T from F8; usable-fact rules; scope, currency, sign rules | Unit tests on F8's factories; FL-1 to FL-5, FL-8 |
| F7-2 | Period arithmetic F7P-1 to F7P-6, including disagreeing paths | Hand-computed cases for 3, 6, 9, 12-month and quarter-only reporters, March and December year-ends |
| F7-3 | Feature registry and set v1; valuation join with MF | Every formula against a hand-computed vector; FL-6 |
| F7-4 | Financial eligibility | Unit tests of each result |
| DE-1 | Engine contract, scoring rules | Unit tests; determinism |
| DE-2 | Engine set v1 | Each engine on a synthetic universe with known ranks; FL-7 |
| DE-3 | Reference baselines | `base_rate_252` point-in-time test; FL-9 |
| DE-4 | Migration 0020, store, preflight, `verify`, wrapper, runbook | PostgreSQL 17 in CI; append-only for every role; mutation tests; FL-10 |

Phase 4 needs Phase 3's MF-1 to MF-6 for the market inputs and targets. F7-1 to F7-4 depend only on F8 and can
proceed in parallel with Phase 3.

---

## 15. What stays open until real data exists

- How many issuers have a usable panel at all. Real-data validation found facts for one issuer; the limit was issuer
  evidence, which the backfill (HB-3) addresses, and the open findings P-18, P-23 and P-1 (P4-R2).
- Whether `marketCap` is per security, present every day, and consistent with price × shares (§6.3).
- Whether issuers' sign conventions are stable enough for `f7.sign.1` (§4.5).
- How often derivation paths disagree (§5), and whether the tolerance is right.
- Every eligibility value (§11), and whether any engine's "favourable" direction has predictive value at all.

## 16. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | Two new read-only layers: F7 above F8; DE above F7 and MF |
| 2 | Source of truth | F8 results only, for financial data; MF snapshots only, for market data |
| 3 | Information at its timestamp | F8's mode and cutoffs for facts; MF's (T, D\*) for prices; both recorded |
| 4 | Provenance retained | Row hashes, `ef_key`s, F8 `result_hash`, MF row hashes (§10.3) |
| 5 | Frozen components | None modified; migration-enumerating guards at DE-4 |
| 6 | Migration | Yes: 0020, additive |
| 7 | Leakage prevention | F8's guards, pinned configurations, same-T universes, the target import ban, FL-1 to FL-10 |
| 8 | Testing | §12, §14; F6.4 and F8 factories; PostgreSQL 17 in CI |
| 9 | Reproduction | Deterministic Decimal arithmetic; configuration and input hashes; `verify` |
| 10 | Route 1 and Route 2 | Route 1 consumes signals, components and baselines. Route 2 receives the same features as structured input and never the engines' scores as instructions |
| 11 | Later evaluation | Baselines and calibrated engines are scored by Phase 9 against MF's targets, beside ML |
| 12 | After downtime | Nothing is scheduled in Phase 4. A late filing appears in `KNOWN` from when it was recorded, never earlier |
| 13 | Failure representation | Fact states carried through; feature quality states; engine states; eligibility reasons |
| 14 | Versions | Registries, rules, policies and constants in configuration hashes; code version tuple per run |
| 15 | Grounded reasoning | `components` lists each input value, its quality and its contribution (§7.2) |

## 17. Audit record

*(Filled in by the audit under C-0005.)*
