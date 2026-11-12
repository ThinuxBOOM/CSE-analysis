# Phase 4: financial features and deterministic engines (design)

**Status: DESIGN, revision 4 (2026-11-12). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact and no change to `main`.

*Revision 2 replaces revision 1 (commit `95310b2`), which its audit failed with three Blockers and fifteen Major
findings (§17). The main changes: per-share values are restated onto the price's share basis before they meet a price
(§6.3); the sign rule no longer touches concepts whose printed sign carries meaning (§4.5); the link from a security
to its issuer is point-in-time (§4.6); a conflict at the newest period blocks instead of being stepped over (§4.3,
§5.4); cross-sectional scoring has a written formula, ranks issuers rather than securities, and uses one definition
per rank (§7, §8); the baselines no longer emit invented probabilities (§9).*

*Revision 3 (2026-11-12) answers the second audit, which failed revision 2 on one Blocker and seven Major findings
(§17). The Blocker: earnings per share was restated onto the snapshot's share basis but divided by a raw carried
price that could still be on the old basis. Valuation now uses Phase 3's `adj_ref` on both sides (§6.3). Other main
changes: a defined fiscal-year EPS for interim dates; EPS valuation at security level; the anchor's duration, the
newest period and `scope_changed` defined from one result; ratios that need a positive denominator say so; cash-flow
signs are confirmed by an identity or the value is not used.*

*Revision 4 (2026-11-12) answers the third audit, which found the revision 2 Blocker resolved by worked cases and two
Major findings left (§17): the rule that guards EPS against a corporate action not yet entered read an input Phase 3
did not store, and `eps_latest_fy` did not say which fact states count or how old it may be. Phase 3 now stores the
factor list and the unexplained-move firings by date; §6.2 and §6.3 are completed.*

**Governing records:** Master Architecture (MA) §2, §6, §9 to §14, §21, §22, §39, §41, §42, §55 (Phase 4), §56 to
§59, §68; `docs/F8_DESIGN.md` (§7, §8, §11, §12, §14); `docs/F6.2_DESIGN.md` (the economic fact, its states and
intervals); `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, revision 3 (market bars, universe, market features, targets;
"MF"). Where this document and those differ, they win.

**Short names.** F7 is the financial feature layer (`docs/F8_DESIGN.md` §14's name). DE is the deterministic engine
layer. Steps are F7-1 to F7-4 and DE-1 to DE-4 (§14). Decisions are P4-D*n* ([delegated]) and P4-R*n* (reserved)
(§13).

---

## 1. Conclusion

```
F6.4 economic facts ──F8 as_of──▶ F7 fundamental panel ──▶ F7 financial features ──┐
                                                                                    ├─▶ DE engines ─▶ signals
MF snapshot rows (Phase 3) ────────────────────────────────────────────────────────┤
                                                                                    └─▶ DE reference baselines
```

1. **F7 reads financial truth only through F8**, in `KNOWN` for live work and `AVAILABLE` for backfilled eras, and
   stores each `result_hash`. It never reads F5 candidates, F6.4 tables or `report_filings`.
2. **F7 derives; it never repairs.** A `conflicting` fact is never resolved, a scope is never guessed, currencies are
   never converted, a missing value is never zero, and a sign is never reinterpreted where the printed sign carries
   meaning (MA §9).
3. **An engine is a pure, versioned function from features to a structured signal** with its components. It has no
   fitted parameter. Engines are "first-class baselines, not fallbacks" (MA §13).
4. **A signal is not a forecast.** Turning a signal into MA §4's prediction contract needs a fitted calibration, which
   is a model and belongs to Phase 5's registry. Phase 4 ships the signals and three parameter-free **reference
   baselines**, each of which fills only the contract fields it can honestly fill (§9).
5. **Several of MA §12.1's engine families are not built in v1**, and §2 says which and why.
6. **It is buildable now and unvalidatable now.** The only economic facts ever produced are 321 facts for one issuer
   (MA §52). Every threshold is `provisional`; real-data questions are in §15.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Status and where |
|---|---|---|
| Each engine produces structured, versioned outputs; component outputs retained; engines are first-class baselines | §12.1, §13 | Met: §7, §9, §10 |
| Reasoning grounded in the actual inputs | §14 | Met: §7.2 |
| No silent sign, scale or currency transformation; nil is never zero; conflicts preserved; Group/Company/Bank never silently merged | §9 | Met: §4, §5 |
| Financial eligibility by measurable criteria, versioned, empirically tested | §6, §22 | Mechanism met; values `provisional`: §11 |
| Not restricted to large companies | §6 | Single-entity issuers with unlabelled statements are included (§4.3) |
| Point-in-time and leakage tests | §11, §57 | Met: §4, §12 |
| Feature source, formula, units, availability rule, version, quality state | §39 | Met: §6.1 |
| Confidence statistically grounded | §21 | Met by not emitting probabilities without a method: §9 |
| Research never silently becomes production | §58 | Met: configurations are designated through the owner path; nothing here is fitted (§10.1) |
| Provenance forecast → feature → economic fact → source observation → filing → CSE source | §42 | Met: §10.3 |

**The engine families of MA §12.1, one by one:**

| Family | v1 status |
|---|---|
| Revenue growth, earnings growth | Engines `fund_growth`, `fund_growth_bank` |
| Margins, ROE/ROA | Engines `fund_profitability`, `fund_profitability_bank`. Margin change is a feature (`net_margin_chg`) |
| Leverage, balance-sheet strength | Engines `fund_leverage` (general), `fund_bank_strength` (bank and finance). Interest cover and borrowings ratios are features only (§8 says why) |
| Cash flow | Engine `fund_cash_flow`, on values whose sign is confirmed (§4.5) |
| Valuation | Engine `fund_valuation_eps`; `fund_valuation_mcap` is built and `blocked_quality` until `marketCap` is verified |
| EPS | Features (`eps_latest_fy`, `eps_ttm_approx`); `eps_latest_fy` enters `fund_valuation_eps` |
| Liquidity (balance sheet) | Features only (`cash_to_assets`, `net_debt`). The vocabulary has no current assets or liabilities, so no current or quick ratio exists (P4-R1) |
| Working capital | Features only (`receivables_days`, `inventory_days`) |
| Dividends | Features only (`payout_ratio_cash`; `dividend_yield_cash` inside the blocked market-value engine) |
| Momentum, trend, volatility, volume/turnover, drawdown, mean reversion, market-relative | Engines `mkt_*` (§8) |
| Correlations | **Not built.** `beta_126` is reported as a component with weight 0 |
| Sector-relative | **Not built.** No point-in-time sector source exists (Phase 3 §8.3, P3-R5) |

---

## 3. Boundary

**F7 reads:**
- `worker.financial_asof`: `as_of`, `timeline`, `explain`, and the guards `require_point_in_time` and `require_live`.
  Nothing else of the financial truth layer. (Pure tests may call F8's `selection.evaluate` through F8's own test
  factories; production code does not.)
- `issuer_securities` (0007), under the as-of rule of §4.6.
- `financial_concepts` (0008): a concept's family, period kind, value type, natural sign and industries. An immutable
  seed.
- MF snapshots (Phase 3 §13.1), for the valuation join only. From the snapshot row: `adj_ref`, `ref_session`,
  `stale_sessions`, `market_cap_reported`, the symbol and the `company_id`. From `mf_snapshot_factors`: the factor
  list per security (action kind, ex-date, factor, status, flags). From `mf_snapshot_move_flags`: the unexplained
  moves by date. Phase 3 revision 3 stores both in that shape.

**DE reads:** F7's feature rows and MF's snapshot rows. Nothing else. In particular it never reads the raw layer or
MF's target tables.

**Neither** contacts a network, writes outside its own tables, modifies a frozen component, or reads a mutable table.

**Not owned:** the financial truth (F6, F8), market features and targets (Phase 3), fitted calibrations and the
Route 1 combination (Phase 5), user-facing reasoning text (Phase 8).

---

## 4. F7-1: the fundamental panel as of T

### 4.1 One cutoff, two layers

A computation has **one information cutoff T**. F8 is queried at T; the MF snapshot is the one computed at the same T,
whose D\* is derived (Phase 3 §4.3). There is no separate financial cutoff: MA §11's `financial_data_cutoff` and
`market_data_cutoff` are recorded as (T) and (T, D\*).

| Pipeline | F8 mode | Guard applied to every F8 result | Passed explicitly |
|---|---|---|---|
| Live | `KNOWN` (or `KNOWN_RECORDED`) | `require_live` | T |
| Backfilled era, dataset or backtest | `AVAILABLE` | `require_point_in_time` | T, **H**, and the **`f8_configuration_id`** |
| Research report | `CURRENT` | none; refused by both guards on any point-in-time path | H |

- In `AVAILABLE`, F8's unpinned configuration is the one designated at H and an omitted H is the database's `now()`,
  inside the hashed result. F7 therefore always passes H and the configuration id in that mode, and stores both, so
  the panel is reproducible.
- **Labels.** F7 records F8's label verbatim (`known`, `known_recorded`, `reconstructed`) and MF's
  (`point_in_time`, `reconstructed`). A row's governing class is `reconstructed` if either input is, otherwise
  `point_in_time`. `retrospective_current` never reaches a stored row.
- A dataset over many dates uses `timeline`.
- In `KNOWN_RECORDED` both layers return what they stored: the F7 panel stored for the purpose and the MF snapshot it
  recorded as its input, by id. "One cutoff" then means the stored pair, not a recomputation.

### 4.2 Which facts are usable

| What F8 returns for a fact | F7 treatment |
|---|---|
| State `single_source` or `corroborated`, with a numeric representative | Usable. The representative normalized value is used with the fact's interval [low, high] (F6.2 §7) as its uncertainty |
| F6's annotation `representative_ambiguous` on the fact's result, whatever its state (no single representative) | Not used as a number: `missing`, reason `representative_ambiguous`. v1 takes no midpoint |
| State `conflicting` | **Not usable.** Every feature needing it is `conflicting` and null. F7 never picks a side |
| State `none` with in-set exclusions `not_yet_available` | `missing`, reason `not_yet_available` (it exists but is not visible at T) |
| State `none`, nothing in the set | `missing`, reason `absent` |
| Value kind nil (a printed dash) | Kept as nil; never zero. A formula needing a number there is `missing`, reason `printed_nil` |
| Fact flag `ambiguous_supersession` or `metadata_version_tie` | Not used: `untrusted`, with the flag as reason. F8 could not order the versions, so F7 does not rely on the one shown |
| Observation flag `available_after_known` | Usable; the flag is recorded (it describes history, not doubt about the value) |
| Availability `availability_unknown` | As F8 applies it: kept in `KNOWN`, excluded in `AVAILABLE` |
| Availability flags `availability_sources_disagree`, `availability_evidence_changed`, `last_modified_after_upload` | Usable; recorded. They matter where F7 itself uses the availability instant, which is only the EPS basis (§6.3): there they make the value `untrusted` |
| F6 annotations `restated_comparative_present`, `internal_conflict`, `sign_only_difference` | Recorded as reasons beside the state F6 gave. F7 adds no rule: a `sign_only_difference` fact is already `conflicting` |
| Availability precision `day` | Counts from the end of that Colombo day, as F8 does |

### 4.3 Scope and the anchor period

Facts of different scopes are different facts and are never mixed in one formula.

- **Anchor concept:** `profit_for_period`, with `operations = total_or_unstated`.
- **Newest period:** the latest `period_end` at which the issuer has an anchor-concept fact in state
  `single_source`, `corroborated` or `conflicting`, in any scope and currency. A fact F8 reports as `none` does not
  count: in `KNOWN` that includes a document the system holds but that is not yet public, which must not move the
  anchor forward. It is defined before the scope, so the two are not circular.
- **Scope policy `f7.scope.1`:** among the scopes that have an anchor-concept fact (in any state) at the newest
  period, take the first of `group`, `bank`, `company`. `unlabelled` is taken only when it is the **only** scope the
  issuer has at T. Statements without Group and Company columns are typical of single-entity issuers, and excluding
  them would restrict the platform to larger groups (MA §6). An `unlabelled` panel is labelled so on every value and
  is never combined with a labelled scope. If the newest period has the anchor only in `unlabelled` scope while the
  issuer has older labelled facts, no scope qualifies and the panel's state is `scope_unavailable`.
- **The anchor's duration n** is the largest `duration_months` not above 12 among the anchor facts at the newest
  period in the chosen scope and currency. A half-year interim prints both a 3-month and a 6-month figure at one
  period end; n is 6.
- **The anchor period is the newest period in the chosen scope.** If the anchor fact there is `conflicting`,
  `untrusted` or not numeric, the panel's state is `critical_conflict` (or `anchor_unusable`): features are not
  computed from an older period instead. A conflict at the newest period is therefore visible, never stepped over.
- **`scope_changed`** is decided from the one result at T: the chosen scope has no anchor fact at the period twelve
  months before the anchor, while another scope has one. The panel then carries `scope_changed` and no year-on-year
  feature is computed. (Revision 2 asked F8 a second time at T minus 12 months. That is a different question, it
  fails in the first live year when nothing had been recorded a year earlier, and it tests the wrong thing: what
  matters is whether the comparative exists in the chosen scope as seen now.)
- `operations`: formulas use `total_or_unstated`. If the anchor period also has `discontinued` facts, the panel
  carries `has_discontinued`, and features that divide a total-operations profit by revenue (usually continuing-only)
  are `partial` with that reason.

### 4.4 Currency

**`f7.currency.1`:** the panel's currency is LKR if the issuer has the anchor fact in LKR at the newest period in the
chosen scope; otherwise the single other currency it has there; two or more non-LKR currencies without LKR give `blocked: currency_ambiguous`. Facts in any other
currency are ignored for the panel (an annual report's USD convenience translation, for example). Valuation features
additionally need the panel currency to be LKR, the price currency. No FX conversion exists in this phase.

### 4.5 Sign

F6 keeps every value with its printed sign, and F5 never changes one. `natural_sign` is documentation. F7 applies one
explicit, versioned rule and records each use:

- **`f7.sign.2`.** The **magnitude** of the printed value is used only for these concepts, whose F5 mapping rules
  match gross labels that cannot net to the opposite sign: `cost_of_sales`, `finance_costs`, `interest_expense`,
  `purchase_of_ppe`, `dividends_paid`. Each use records `sign_rule: magnitude` and the printed sign. The magnitude is
  taken **per fact, before any period arithmetic**, because F7P-2 adds and subtracts figures from documents that may
  print the same expense with different signs. (`interest_expense` is listed for completeness; no v1 feature uses
  it.)
- Every other concept is used **with its printed sign, unchanged**. That includes `impairment_charges` and
  `income_tax_expense`, whose mapped labels are of the form "charges/(reversals)": there the sign is the information,
  and taking a magnitude would turn a reversal into a charge.
- **Cash-flow totals need confirmation.** `net_cash_from_operating_activities` and the other two totals are
  `as_printed`, and F5's rule also matches labels such as "net cash used in operating activities". A statement that
  prints such a line without parentheses gives a positive number for an outflow, and F7 cannot see the label. So a
  cash-flow total is used only when F7 can confirm its sign from its own inputs: operating plus investing plus
  financing equals the change in `cash_at_end_of_period` over the same period, within the facts' intervals
  (`f7.cashflow_sign.1`). Confirmed values carry `sign_confirmed`. How often confirmation fails is a real-data
  question (§15).
- **What the identity needs, and where it will fail.** For a period of n months ending E it needs the opening cash,
  the instant `cash_at_end_of_period` at E−n. "Cash at the beginning of the period" is not a mapped concept, so that
  figure must come from another document: the comparative column for an annual period, the prior year-end report for
  an interim one, in the same scope and with an exactly matching date. A statement with an "effect of exchange rate
  changes on cash" line, which is not in the vocabulary either, fails the identity by that amount every period, so
  groups with foreign operations will mostly have no confirmed cash-flow totals. Two totals printed with flipped
  signs that happen to offset would still balance; the rule cannot see that case.
- **An unconfirmed total is null**, state `missing`, reason `sign_unconfirmed`, and so is every feature derived from
  it. A value of unknown sign is not handed to Phase 5 or Route 2.
- Consequence: v1 has **no feature that needs the orientation of `impairment_charges`**. Revision 1's
  `cost_of_risk_proxy` is removed. F6.1 already determines sign conventions from arithmetic relations, but F8 does not
  expose that result; using it is reserved (P4-R4).

### 4.6 The security-to-issuer link

`issuer_securities` is an append-only log of decisions; a security's current decision is its highest id, and a
decision can be `conflict` with no issuer. **`f7.security_link.1`:** for a security at T, take the highest-id decision
with `decided_at` ≤ T (in `AVAILABLE`: ≤ H, since a link is the system's own conclusion and has no publication time;
the row is labelled `reconstructed`). `evidenced` links the security to that issuer; `conflict`, or no decision, gives
`blocked: issuer_link_conflict` or `issuer_link_absent`. The decision id is part of the feature row's hash, so a later
decision cannot silently change which fundamentals a security had at T.

The security's `company_id` comes from the MF snapshot row (Phase 3 §8.1 keeps it beside the computed security key).
A security that exists only in imported history has none, so it is always `issuer_link_absent`: over a
`reconstructed` era, fundamental features and engines cover captured securities only. Rows of such an era carry
`fundamental_coverage: captured_only`, so that a dataset built on them says so.

---

## 5. F7-2: period arithmetic

Each derived value records its rule, the `ef_key`s used and their states.

### 5.1 Dates

E−k, "k months before period end E", is `_add_months(E + 1 day, −k) − 1 day`, with `_add_months` as F6.1 defines it
(`worker/financial_validation.py`): the day after the period end is shifted by whole months and stepped back one day.
A month-end maps to a month-end and the 25th to the 25th. A rule applies only when a fact exists with **exactly** the
required period end. Reporters whose year-end moves by a day or two (52/53-week years) will not match and get
`missing`, reason `no_exact_period_match`; how common that is on CSE is an open question (§15). The rule is exact for
month-ends and for days up to the 28th. For a period end on the 29th or 30th that is not a month-end, shifting by
six months twice does not always equal shifting by twelve, which F7P-2 needs; such period ends also get
`no_exact_period_match`.

### 5.2 Rules

With D_n(E) the duration fact of n months ending on E, and I(E) an instant fact:

| Rule | Derives | From |
|---|---|---|
| `F7P-1` | TTM(E) | D_12(E) |
| `F7P-2` | TTM(E) | D_n(E) + D_12(E−n) − D_n(E−12), for n ∈ {3, 6, 9} (the durations the schema allows) when all three facts exist |
| `F7P-3` | TTM(E) | D_3(E) + D_3(E−3) + D_3(E−6) + D_3(E−9), printed three-month facts only |
| `F7P-5` | AVG(E) | (I(E) + I(E−12)) / 2 |
| `F7P-8` | G_inst(E) | I(E) / I(E−12) − 1, only when I(E−12) > 0; otherwise null, reason `base_not_positive` |
| `F7P-6` | G_ttm(E) | TTM(E) / TTM(E−12) − 1, only when TTM(E−12) > 0; otherwise null, reason `base_not_positive` |
| `F7P-7` | G_cum(E) | D_n(E) / D_n(E−12) − 1 for the anchor's own n, same condition |

- **F7P-2 is an identity on intervals:** (E−n, E] plus (E−n−12, E−n] minus (E−n−12, E−12] is (E−12, E]. It holds for
  any facts with those ends, whatever the fiscal year. (Revision 1 wrongly tied it to the fiscal year's start.)
- F7P-4 (a single quarter) was removed: no v1 feature used it. The rule ids keep their numbers.
- **F7P-7 exists because F7P-6 is expensive.** TTM growth at an interim date needs filings from two earlier years.
  Growth of the cumulative period needs only the current filing and its comparative column.
- **More than one path.** When several rules can produce TTM(E), all are computed, each with the interval obtained by
  adding and subtracting its inputs' intervals. They agree if those intervals overlap; then the lowest-numbered rule's
  value is used, marked `corroborated_paths`. (F6 never moves a representative into its interval, so comparing
  representatives against interval widths could call two consistent paths conflicting.) Otherwise the value is `conflicting` and every path is kept (P4-D8).
- **Per-share values are not summed across periods without care.** §6.3.
- Uncertainty: a sum's interval is the sum of its inputs' intervals, recorded. It is not propagated through ratios in
  v1 (P4-D5); the inputs' intervals are recorded instead.

### 5.3 Concepts older than the anchor

Features are computed at the anchor period. A concept with no usable fact at the period the formula needs is
`missing` for that feature. It is never taken from another period.

### 5.4 What a conflict at the newest period does

Covered by §4.3: the panel is `critical_conflict`, financial eligibility fails with that reason (§11), and no feature
is silently computed from the period before.

---

## 6. F7-3: financial features

### 6.1 The registry

As Phase 3 §10.1, with: `industries` (`all`, `general`, `bank_finance`; `insurance` exists in the vocabulary but its
concepts are `reserved`, so no v1 feature applies to it) and `period_rules`. Arithmetic is the same fixed-context
`Decimal`. Every value carries: scope, currency, anchor period, the `ef_key`s and rules used, the F8 `result_hash`,
the label class, the quality state with its reason code, and two ages: days from the anchor period's end to T, and
days from the newest contributing availability instant to T.

### 6.2 Feature set v1

Each id has one definition. Where two bases are possible they are two ids, and no id falls back to another.

| Family | Feature | Definition | Industries |
|---|---|---|---|
| Growth | `rev_g_cum`, `rev_g_ttm` | F7P-7, F7P-6 of `revenue` | general |
| | `gross_income_g_cum`, `gross_income_g_ttm` | of `gross_income` | bank_finance |
| | `nii_g_cum` | F7P-7 of `net_interest_income` | bank_finance |
| | `profit_g_cum`, `profit_g_ttm` | of `profit_for_period` | all |
| | `profit_owners_g_ttm` | F7P-6 of `profit_attributable_to_owners` | all (null where the scope prints no owners' line) |
| | `assets_g`, `equity_g` | F7P-8 of `total_assets`, `total_equity`. An interim balance sheet's comparative is usually the last year-end, not the same date a year earlier, so at interim anchors these often need last year's filing as well | all |
| | `loans_g`, `deposits_g` | of `loans_and_advances_to_customers`, `customer_deposits` | bank_finance |
| Earnings | `eps_latest_fy` | The 12-month `eps_basic` at the **latest period end, at or before the anchor, that has such a fact in state `single_source`, `corroborated` or `conflicting`**, in the chosen scope and panel currency, restated (§6.3). If that fact is not usable (§4.2), the feature takes its state: it never steps back to an older year. The period end must be no earlier than twelve months before the anchor; otherwise `missing`, reason `fy_eps_too_old`. A stated exception to §5.3: at an interim anchor the last fiscal year's EPS is used, recorded with its period end and the reason `earlier_period_than_anchor` | all |
| | `eps_ttm_approx` | F7P-2 or F7P-3 of restated `eps_basic`; flagged `sum_of_per_share` | all |
| Margins | `gross_margin`, `operating_margin` | TTM `gross_profit`, `operating_profit` / TTM `revenue` | general |
| | `net_margin` | TTM `profit_for_period` / TTM `revenue` | general |
| | `net_margin_bank` | TTM `profit_for_period` / TTM `gross_income` | bank_finance |
| | `net_margin_chg` | `net_margin` (or `net_margin_bank`) at the anchor minus the same at the period twelve months before | all |
| | `nim_proxy` | TTM `net_interest_income` / AVG `total_assets` | bank_finance |
| Returns | `roe_end`, `roa_end` | TTM `profit_for_period` / `total_equity`, `total_assets` at E | all |
| | `roe_avg`, `roa_avg` | The same over AVG balances | all |
| | `roe_owners_end` | TTM `profit_attributable_to_owners` / `equity_attributable_to_owners` at E | all (null where not printed) |
| Leverage | `liabilities_to_equity` | `total_liabilities` / `total_equity` | all |
| | `equity_to_assets` | `total_equity` / `total_assets` | all |
| | `borrowings_to_equity` | (`interest_bearing_borrowings` current + non-current) / `total_equity`; both maturities required | general |
| | `interest_cover` | TTM `operating_profit` / TTM `finance_costs` (gross finance cost, by `f7.sign.2`) | general |
| | `loans_to_deposits` | `loans_and_advances_to_customers` / `customer_deposits` | bank_finance |
| Liquidity | `cash_to_assets` | `cash_and_cash_equivalents` / `total_assets` | all |
| | `net_debt` | borrowings − `cash_and_cash_equivalents`. An LKR amount, not comparable across issuers; no engine uses it | general |
| Cash flow | `cfo_to_profit` | TTM `net_cash_from_operating_activities` / TTM `profit_for_period` | general |
| | `fcf_ttm` | TTM `net_cash_from_operating_activities` − TTM `purchase_of_ppe` (magnitude). Capital expenditure here is property, plant and equipment only: intangibles and disposal proceeds are not in the vocabulary | general |
| | `fcf_margin` | `fcf_ttm` / TTM `revenue` | general |
| Working capital | `receivables_days` | `trade_and_other_receivables` / TTM `revenue` × 365 | general |
| | `inventory_days` | `inventories` / TTM `cost_of_sales` × 365 | general |
| Dividends | `payout_ratio_cash` | TTM `dividends_paid` / TTM `profit_for_period`. In a group cash flow, dividends paid can include those to non-controlling interests; the value is flagged so | all |
| Data checks | `accounting_identity_gap` | (`total_assets` − `total_liabilities` − `total_equity`) / `total_assets` | all |
| | `cash_reconciles` | Whether `cash_at_end_of_period` equals `cash_and_cash_equivalents` within their intervals. **Information only:** the cash-flow figure is often net of overdrafts and legitimately differs, so it is not treated as good or bad | all |
| | `nci_share_of_profit` | TTM `profit_attributable_to_nci` / TTM `profit_for_period` | all |

- The vocabulary has no current assets, current liabilities or share count, so the current ratio, quick ratio and book
  value per share are not in v1 (P4-R1).
- **Denominators.** A zero or nil denominator gives null with a named reason. Each ratio's registry entry also says
  whether it requires a **positive** denominator, and a non-positive one then gives null, reason
  `denominator_not_positive`. It is required for: every `roe_*` and `roa_*`, `liabilities_to_equity`,
  `borrowings_to_equity`, `equity_to_assets`, `loans_to_deposits`, `interest_cover`, every margin, `nim_proxy`,
  `cfo_to_profit`, `fcf_margin`, `payout_ratio_cash`, both days ratios, `nci_share_of_profit`, and every yield and
  price ratio of §6.3. Without it, an issuer with negative equity would rank as the least levered and a loss over
  negative equity as a high return. No clipping, no sign flip.
- A null from `denominator_not_positive` or from a zero or nil denominator (`denominator_zero`) has state `missing`.
  Consequence, stated: an issuer with negative equity has no `roe_end` and no `liabilities_to_equity`, so it gets no
  score from `fund_profitability`, and `fund_leverage` scores it on `equity_to_assets`, which is negative and ranks
  it last, as it should (§8).
- The `_cum` growth features compare periods of the anchor's own length n. Issuers at different points of their year
  have different n; the value records n, and §8 notes the limit on comparing them.

### 6.3 Valuation: bringing a per-share value and a price onto one basis

A printed EPS is on the share count of its own reporting. A price on D\* is on the share count of D\*. Between them
there may be a sub-division, bonus, scrip or rights issue. Dividing one by the other without restating is wrong by
the action's factor.

**Restating EPS (`f7.eps_basis.1`).** For an EPS fact from a document with availability instant A (F8's
`available_at` for the observation used), and a snapshot with cutoff session D\*:

```
eps_restated = eps × Π f_k    over the share-basis actions k of the security with  date(A) < e_k ≤ D*
price        = adj_ref        the reference price on the basis of D* (Phase 3 §7.3, stored with the snapshot row)
```

- **Both sides are on the basis of D\*.** `adj_ref` already carries the factor of any ex-date since the last trade.
  The raw `px_ref` does not: after a 2-for-1 split with no trade yet, it is still the old 100 while restated EPS has
  halved, and their ratio would be wrong by half until the next trade. That was revision 2's error.
- **Share-basis actions** are sub-divisions, consolidations, bonus and scrip issues, and rights issues (their bonus
  element, the same TERP factor). They are selected **by action kind**, not by MF's adjustment policy, so a later
  policy that adjusts prices for cash dividends cannot scale EPS.
- f_k are the price factors of Phase 3 §7.2 (fewer earnings per share after a split, exactly as the price falls). A
  factor flagged `cum_price_stale` makes the restated value `partial` with that reason.
- **A** is the latest availability instant among the fact's visible observations, and `date(A)` is its Colombo date
  (a day-precision availability counts as that day). If any visible observation has no availability instant, the
  value is `untrusted`.
- **An action not yet entered.** In `KNOWN`, a corporate action recorded after T is not in the factor list, so
  nothing restates EPS for it, while the price has already moved. The guard is MF's unexplained-move record: if
  `mf_snapshot_move_flags` holds a firing for the security with its later session in (date(A), D\*], the restated
  EPS is `untrusted`, reason `unexplained_move_since_document`. Without the guard, a 2-for-1 split not yet entered
  would double the earnings yield and put the line at the top of the valuation rank.
- A rights factor is computed per line from that line's own cum price, so one issuer's EPS is restated to slightly
  different values on its voting and non-voting lines. An ex-date after D\* is not in the factor list and is not
  considered: it has not affected the price yet either.

- **Why from A and not from the period end.** Accounting standards require a printed EPS to be restated already for a
  bonus or split that happens before the statements are issued. An action with period end < e ≤ date(A) may or may
  not be in the printed figure. F7 cannot tell, so in that case the value is `untrusted`, reason
  `eps_basis_ambiguous`. It is never guessed. The same applies when the fact has no availability instant, when its
  observations' availability instants straddle an ex-date, or when an availability flag of §4.2 is set.
- An `unresolved` factor in the interval, or an unexplained price move flagged by MF there, gives `untrusted`.
- `eps_ttm_approx` restates each component EPS by its own document's A before adding. It remains an approximation
  (weighted-average share counts differ between periods) and says so.
- **EPS valuation is per security.** Each equity line of the issuer gets the issuer's restated EPS over its own
  `adj_ref` and its own factor list. v1 assumes voting and non-voting shares earn the same per share; that is
  `provisional` and recorded on the value.

| Feature | Definition | Needs |
|---|---|---|
| `earnings_yield_eps` | `eps_latest_fy` (restated) / `adj_ref` | EPS, the price, the factor list |
| `pe_eps` | `adj_ref` / `eps_latest_fy`, null when EPS ≤ 0 (the yield stays defined) | As above |
| `issuer_market_value` | Sum of `market_cap_reported` over the issuer's equity lines linked at T (§4.6) on D\* | MF's field |
| `earnings_yield_mcap`, `book_to_market`, `sales_to_market`, `cfo_yield`, `fcf_yield`, `dividend_yield_cash` | TTM `profit_for_period`, `total_equity`, TTM `revenue`, CFO, FCF, dividends paid, each / `issuer_market_value` | As above |

- **Timing.** Both sides are as of the one cutoff T (§4.1). A valuation feature inherits the price's `stale_sessions`
  and is `stale` or `missing` by MF's limits.
- **`market_cap_reported` is `provisional`** (Phase 3 §5.1). In the five real fixture pairs it equals the security's
  last traded price times its `quantityIssued`, which suggests it is per security and price-at-capture based; that is
  five rows on one day. Until checked on real captures, every market-value feature carries reason
  `input_unverified`, engine `fund_valuation_mcap` is `blocked_quality`, and no eligibility criterion depends on any
  of them (P4-D4): their state is `untrusted` while the policy says so. If any linked equity line lacks the value on
  D\*, `issuer_market_value` is `missing`, reason `incomplete_lines`.

### 6.4 Quality state and reason codes

Phase 3 §10.4's states, order and rule (the state describes the value; `missing`, `conflicting` and `untrusted` are
null). Financial reason codes are separate from the states: `printed_nil`, `absent`, `not_yet_available`,
`representative_ambiguous`, `no_exact_period_match`, `base_not_positive`, `currency_mismatch`, `eps_basis_ambiguous`,
`input_unverified`, `has_discontinued`, `sum_of_per_share`, `scope_changed`, `denominator_not_positive`,
`denominator_zero`, `sign_unconfirmed`, `cum_price_stale`, `unexplained_move_since_document`, `factor_unresolved`,
`availability_flagged`, `fy_eps_too_old`, `earlier_period_than_anchor`, `equal_earnings_per_line_assumed`,
`dividends_may_include_nci`, `ambiguous_supersession`, `metadata_version_tie`, `incomplete_lines`, `corroborated_paths`, `issuer_link_conflict`,
`issuer_link_absent`, `currency_ambiguous`, `anchor_unusable`, `scope_unavailable`, `not_provided_by_baseline`.
`currency_mismatch` is the reason when a formula's facts are not all in the panel currency, or when a valuation
feature's panel currency is not LKR. `unlabelled` as a **state** keeps MF's
meaning; an unlabelled **scope** is a property of the panel (§4.3), not a quality state.

---

## 7. DE-1: the engine contract

### 7.1 Definition

```
engine_id, version
family            fundamental | market
level             issuer (fundamental engines except EPS valuation) | security (market engines, EPS valuation)
group             the ranking group: industry group for fundamental engines; all equity lines for market engines
components        ordered list of (feature id, orientation ±1, weight, required: yes/no)
min_components    how many components must be present for a score
admissible        the quality states a component value may have: fresh, stale, partial
```

No fitted parameter, no data-dependent constant, no clock, no randomness. Constants are part of the version.

### 7.2 The ranking universe

- **Market engines** rank the equity lines that are market-data eligible at T (Phase 3 §9).
- **Fundamental engines rank issuers, not securities.** The universe is the issuers that are financially eligible at
  T (§11), have at least one market-data-eligible equity line linked at T (§4.6), and belong to the engine's industry
  group. Each issuer counts once, however many lines it has listed. The issuer's output is then given to **every
  equity line linked to it at T**, market-eligible or not, with the issuer id recorded; market eligibility stays a
  separate fact about the line.
- **`fund_valuation_eps` is the exception:** its input is per security (§6.3), so it ranks the market-data-eligible
  equity lines whose issuer is financially eligible, within the issuer's industry group, with no fan-out.
- **Industry group (`f7.industry.1`):** `bank_finance` if the panel has, at the anchor period in the chosen scope, a
  fact (in any state) of a concept whose `industries` is `bank_finance`; otherwise `general`. This is the same
  information as F5's per-document template, since bank concepts are mapped only in bank-template documents. Insurers
  fall into `general`, where most features are null, because the insurance concepts are `reserved`; they will mostly
  score `insufficient_inputs`. That is a stated v1 limit.
- The universe is that of the **same T**, and its digest is part of every output's inputs.

### 7.3 The score

For component c of an engine, let V_c be the issuers (or securities) of the universe with an admissible, non-null
value of c, and n_c their number.

- If n_c < `N_min` (provisional default 10), the component is **absent for everyone**.
- Otherwise, for a member with value x: `p = (below + 0.5 × equal) / n_c`, where `below` is the number of members
  with a smaller value and `equal` the number with the same value, itself included. Then
  `s_c = orientation × (2p − 1)`, which lies strictly between −1 and 1. Ties share one value.
- A member's score is the weighted mean of s_c over the components it has, if it has every `required` component and
  at least `min_components`; otherwise its state is `insufficient_inputs` and it has no score.
- A component in state `conflicting` or `untrusted` is not used; if it was `required`, the state is
  `blocked_quality`. A component in state `unlabelled` is not admissible either and counts as absent, without
  blocking.

Scores of members with different component sets are not strictly comparable. The output therefore lists
`components_present`, and Phase 5 treats that list as part of the signal, not as noise.

### 7.4 EngineOutput

| Field | Meaning |
|---|---|
| `state` | `scored`, `insufficient_inputs`, `not_applicable` (wrong group or security class), `blocked_quality` |
| `score` | In (−1, 1); positive means more favourable **on this engine's own definition**. Null unless `scored` |
| `components` | For each component: feature id and version, value, quality state and reason, n_c, p, s_c, weight, contribution; or absent with its reason |
| `components_present` | The ids actually used |
| `universe` | Level, group, size and digest |
| `inputs_hash` | Hash of every input row hash |
| `evidence` | MF snapshot row, F7 feature row, F8 `result_hash`, the link decision id |
| `label_class` | `point_in_time` or `reconstructed` (§4.1) |

A score is never stored without its components (MA §13, §14).

---

## 8. DE-2: engine set v1

Weights are equal (P4-D2). "R" marks a required component.

| Engine | Level, group | Components (orientation) | Min |
|---|---|---|---|
| `fund_growth` | issuer, general | `rev_g_cum` (+) R, `profit_g_cum` (+), `assets_g` (+) | 2 |
| `fund_growth_bank` | issuer, bank_finance | `gross_income_g_cum` (+) R, `profit_g_cum` (+), `loans_g` (+) | 2 |
| `fund_profitability` | issuer, general | `roe_end` (+) R, `roa_end` (+), `net_margin` (+), `operating_margin` (+) | 2 |
| `fund_profitability_bank` | issuer, bank_finance | `roe_end` (+) R, `roa_end` (+), `nim_proxy` (+), `net_margin_bank` (+) | 2 |
| `fund_leverage` | issuer, general | `equity_to_assets` (+) R, `liabilities_to_equity` (−) | 1 |
| `fund_bank_strength` | issuer, bank_finance | `equity_to_assets` (+) R, `loans_to_deposits` (−) | 1 |
| `fund_cash_flow` | issuer, general | `cfo_to_profit` (+) R, `fcf_margin` (+) | 1 |
| `fund_valuation_eps` | security, by the issuer's group | `earnings_yield_eps` (+) R | 1 |
| `fund_valuation_mcap` | issuer, by group | `book_to_market` (+) R, `earnings_yield_mcap` (+), `cfo_yield` (+), `dividend_yield_cash` (+) | 2; `blocked_quality` while its inputs are `untrusted` (`input_unverified`) |
| `mkt_momentum` | security | `mom_252_21` (+) R, `mom_126_21` (+) | 1 |
| `mkt_trend` | security | `ret_63` (+) R, `dd_252` (+: closer to 0) | 1 |
| `mkt_reversal` | security | `ret_5` (−) R, `ret_1` (−) | 1 |
| `mkt_volatility` | security | `vol_63` (−) R, `vol_parkinson_21` (−) | 1 |
| `mkt_liquidity` | security | `turnover_avg_21` (+) R, `amihud_21` (−), `zero_trade_ratio_63` (−) | 2 |
| `mkt_relative` | security | `rel_ret_21` (+) R, `rel_ret_63` (+); `beta_126` reported with weight 0 | 1 |

- **One definition per rank.** No component is "A or B". Revision 1 mixed `roe_avg` with `roe_end`, and owners' profit
  with total profit, in one rank; v1 ranks the variant every issuer can have (period-end balances, total profit) and
  keeps the others as features.
- **Why interest cover and borrowings are not engine inputs.** An issuer with no borrowings prints no borrowings line
  and often a nil finance cost. Nil is not zero, so those features are null for exactly the least-levered issuers, and
  ranking on them would score the unlevered as "no data". `fund_leverage` uses ratios of balance-sheet totals, which
  every issuer has. The two features stay available to Phase 5 and to reasoning.
- **Growth's required components are the cumulative-period features**, which need one filing. `assets_g` and
  `loans_g` often need last year's filing too (§6.2) and are optional components for that reason. Issuers at different points of their fiscal
  year are compared on periods of different length; the limit is stated, and Phase 5 sees n.
- "Favourable" is each engine's own definition and a hypothesis. Whether any direction predicts CSE returns is what
  the backtesting laboratory must test.

---

## 9. DE-3: reference baselines

Parameter-free references for Phase 5 and Phase 9. Each fills only the fields of MA §4's contract that it has a
method for; every other field is null with reason `not_provided_by_baseline`. None invents a probability.

| Baseline | Fills | From |
|---|---|---|
| `base_no_change` | Expected return 0; direction `flat`; probabilities: none | Nothing |
| `base_rate_252` | Probability of rise, flat and fall; expected return; direction (the most frequent class; on a tie, `flat` if it is among the tied classes, otherwise none) | MF's return-history features `hist_up_share_h`, `hist_flat_share_h`, `hist_down_share_h`, `hist_mean_ret_h` (Phase 3 §10.3), which use only h-session returns that ended at or before D\*, on the series as adjusted at T. Null where that feature is `missing` |
| `base_momentum_sign` | Direction only: `rise` if `mom_252_21` > 0, `fall` if < 0, none if it is 0 or null | MF's feature |

- `base_rate_252`'s shares are observed frequencies with a stated window and method, which is what makes them
  admissible as probabilities (MA §21). They are computed in MF's feature path, so the engine package neither imports
  the target module nor re-implements the label.
- No baseline emits a volatility or a range. Phase 5's `ref` adapter passes them through as they are, and scores each
  only on the fields it fills.
- An engine's signal becomes a forecast only as the pair (engine version, calibration model version) registered in
  Phase 5 (`kind: calibrated_engine`).

---

## 10. Storage and provenance

### 10.1 Migration `0020_financial_features_and_engines.sql` (additive; append-only, the pattern of 0015 and 0017)

| Table | Contents |
|---|---|
| `f7_configurations` | Canonical JSON and SHA-256: feature registry version, `f7.scope`, `f7.currency`, `f7.sign`, `f7.security_link`, `f7.eps_basis`, `f7.industry`, `f7.cashflow_sign`, period rules, `financial_eligibility_policy` and its status, `N_min` |
| `f7_panels` | One per (issuer, T, mode, **H where the mode has one**, F7 configuration, `f8_configuration_id`): the F8 `result_hash`, chosen scope and currency, newest and anchor period, panel state, counts by fact state, label class |
| `f7_feature_rows` | One per (panel, MF snapshot, security or issuer): the feature vector as canonical JSON with per-value state, reason, rules and `ef_key`s; the link decision id; the MF snapshot row hash used for valuation; `row_hash` |
| `de_configurations` | Engine registry version and constants |
| `de_runs` | One per (T, mode, H where the mode has one, MF snapshot, F7 configuration, DE configuration): universe digests, counts, label class |
| `de_outputs` | One per (run, security, engine or baseline): the output as canonical JSON; `output_hash` |
| `f7_designations`, `de_designations` | Owner-only: the configuration in force per purpose (the F8 pattern) |

Roles and guards as Phase 3 §13.1, including the migration text constraints. No new role and no `SECURITY DEFINER`.
Nothing in Phase 4 is fitted, so there is no research-to-production path here beyond the owner's designation of a
configuration (MA §58).

### 10.2 Packages

`worker/financial_features/` (pure: panel, periods, features), `worker/engines/` (pure: registry, scoring,
baselines), `worker/feature_engine_store/` (loader, writer, preflight, `verify`).

### 10.3 The provenance chain

`de_outputs.evidence` → `f7_feature_rows.row_hash` → `ef_key`s and the F8 `result_hash` → F8 `explain` → source
observations, F5 run, classification, filing, listing observations; and → `mf_snapshot_rows.row_hash` → Phase 3's
chain to the archived response.

### 10.4 Frozen contracts affected

None modified. The range-stating guards Phase 3 §13.3 names need the same change control for 0020. F8's static test
fixes F8's own file list and its own reads, not its callers (confirmed by the audit), so a new package calling F8
does not touch it.

---

## 11. Financial eligibility

The second step of MA §6, at issuer level. A pure function of the panel and a versioned policy, returning a reason
and the measured values.

| Criterion (policy v0, `provisional`) | Provisional value |
|---|---|
| A panel exists, with a scope and a currency | required |
| The panel state is not `critical_conflict` or `anchor_unusable` (§4.3) | required |
| No `conflicting` fact at the anchor period among `total_assets` and `total_equity` | required |
| Fiscal years with a usable 12-month anchor fact | ≥ 2 |
| Days from the anchor period's end to T | ≤ 270 |
| At least one growth feature and one return feature with a **non-null** value | required |

Results: `eligible`, `no_panel`, `scope_unavailable`, `currency_ambiguous`, `anchor_unusable`, `critical_conflict`
(which also covers a conflicting `total_assets` or `total_equity` at the anchor), `insufficient_history`, `stale`,
`insufficient_features`. (Whether a given security is linked to the issuer is a fact about the security, decided at
the fan-out by §4.6, not an eligibility result of the issuer.) They map onto MA §22's blockers. The values are placeholders labelled `provisional`; real values are
chosen on data before any declared final test period, under a Phase 5 protocol (Phase 3 §9 states the same rule).

---

## 12. Leakage tests

**Construction.** In `KNOWN`: compute at T, plant, recompute at T, require identical bytes. In `AVAILABLE`: build
**two evidence sets** that differ only by the plant, both recorded before one fixed H that is later than the plant,
and compare the results at the same (T, H). (Comparing before and after the plant under a moving H would change the
hash for the wrong reason, and fixing H before the plant would hide the plant for the wrong reason.) Every test also
asserts the converse: the same information placed at or before T changes the result.

| ID | Planted | Must not change |
|---|---|---|
| FL-1 | A filing available after T | Any F7 feature at T |
| FL-2 | A restatement available after T | The pre-restatement value at T |
| FL-3 | A document published before T but first known after T | `KNOWN` at T (and, asserted, it does change `AVAILABLE`) |
| FL-4 | A later period's fact | The anchor period, scope, currency, industry group and every TTM at T |
| FL-5 | A conflicting observation recorded after T | The fact's state and the panel state at T |
| FL-6 | A later price; a later `marketCap`; a late-recorded observation for D\* | Any valuation feature at T |
| FL-7 | A security added to, or removed from, later universes | Every rank and score at T |
| FL-8 | A `CURRENT` result offered to F7; an `AVAILABLE` result offered to the live pipeline | Refused by `require_point_in_time` and `require_live` |
| FL-9 | Any label | No feature, engine or baseline depends on it; `worker/financial_features` and `worker/engines` cannot import MF's target module |
| FL-10 | A later designation in any of `f8_designations`, `mf_designations`, `f7_designations`, `de_designations` | `KNOWN`: results use the configurations in force at T. `AVAILABLE`: results use the configuration ids passed explicitly |
| FL-11 | A later `issuer_securities` decision, including a later `conflict` | The link, the fundamentals attached to the security and `issuer_market_value` at T |
| FL-12 | A corporate action recorded after T, with its ex-date before or after D\* | Restated EPS and every valuation feature at T |
| FL-13 | **Another** issuer's later filing | This issuer's ranks, n_c and industry group at T |
| FL-14 | A split between an EPS document's availability and D\*, known at T | The restated EPS equals the hand-computed value; with the ex-date between period end and availability the value is `untrusted` |
| FL-15 | An impairment reversal (a value printed with the opposite sign) | It is used with its printed sign; no feature takes its magnitude |
| FL-16 | A split with **no trade since**, the ex-date between the last trade and D\* | `earnings_yield_eps` equals its hand-computed value before and after the first post-split trade (the revision 2 Blocker) |
| FL-17 | A rights issue, and a cash dividend, between an EPS document and D\* | The rights factor is applied to EPS; the cash dividend is not, under any MF adjustment policy |
| FL-18 | An EPS fact corroborated by two documents with a split between their availability dates; an EPS fact with no availability instant | `untrusted`, reason `eps_basis_ambiguous` |
| FL-19 | A document the system holds but that is not yet public (`KNOWN`), for a period after the current anchor | The newest period and the anchor at T are unchanged |
| FL-20 | An outflow printed without parentheses under a "net cash used in" label | The cash-flow identity does not confirm it; the total and every feature derived from it are null |
| FL-21 | In `KNOWN`: a split with its ex-date after the EPS document, **not yet entered at T**, and one post-split trade | MF's move record fires; the restated EPS is `untrusted`, not a doubled yield. Entered by T, the yield equals its hand-computed value |
| FL-22 | The newest fiscal-year EPS `conflicting`, with a clean one a year older; and a newest fiscal-year EPS more than twelve months before the anchor | `eps_latest_fy` is `conflicting`, not the older value; and `missing`, reason `fy_eps_too_old` |

---

## 13. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0008, C-0011 and C-0018)

| ID | Decision | Reason |
|---|---|---|
| P4-D1 | Engines emit signals; forecasts need a registered calibration (Phase 5). Baselines fill only the contract fields they have a method for | A probability without a method is invented precision (MA §21, §22) |
| P4-D2 | Equal weights inside an engine; mid-rank percentile transformation (§7.3) | No evidence justifies anything else; ranks need no fitted scale |
| P4-D3 | Sign rule `f7.sign.2`: magnitude only for five concepts whose labels cannot net; every other concept keeps its printed sign | Revision 1's wider rule turned an impairment reversal into a charge |
| P4-D4 | Market-value features are built and carry `input_unverified`; nothing in policy v0 depends on them | `marketCap`'s semantics rest on five fixture rows |
| P4-D5 | Uncertainty is summed through additions and recorded, not propagated through ratios, in v1 | Simplicity; the inputs' intervals stay available |
| P4-D6 | Industry group from the concepts in the panel (`f7.industry.1`) | `companies.sector` is mutable and has no history |
| P4-D7 | Scope policy `f7.scope.1`: group, bank, company among the scopes present at the newest period; `unlabelled` only when it is the issuer's only scope | Consolidated figures are the usual basis; single-entity issuers must not be excluded (MA §6) |
| P4-D8 | All TTM derivation paths are computed; disagreement is `conflicting` | No arbitrary winner |
| P4-D9 | The anchor is the newest period; a conflict there blocks | A conflict must not be stepped over (MA §22) |
| P4-D10 | Fundamental engines rank issuers, then give the output to each equity line | An issuer with two listed lines must not count twice |
| P4-D11 | One definition per rank; no fallback between feature ids | A rank over mixed definitions is not a rank |
| P4-D12 | EPS is restated from its document's availability date by MF's factors; an ex-date between period end and availability makes it `untrusted` | The only honest reading of what a printed EPS already reflects |
| P4-D13 | `fund_leverage` uses balance-sheet totals only | Nil-is-not-zero would otherwise penalise unlevered issuers |
| P4-D14 | The link from a security to its issuer is taken as of T (`f7.security_link.1`) | Point-in-time correctness |
| P4-D15 | Currency rule `f7.currency.1` (LKR at the newest period in the chosen scope, else the single other currency) | One stated rule; no conversion |
| P4-D16 | Valuation divides restated EPS by `adj_ref`; EPS valuation is a security-level engine | Both sides on one share basis; an issuer's two lines have two prices |
| P4-D17 | `eps_latest_fy` may come from an earlier period than the anchor, as a stated exception | Otherwise EPS valuation is missing for most of every issuer's year |
| P4-D18 | A cash-flow total is used only when the cash identity confirms its sign (`f7.cashflow_sign.1`) | The printed sign of a "net cash used in" line is not reliable, and F7 cannot see the label |
| P4-D19 | Ratios declare whether they need a positive denominator | Negative equity must not rank as low leverage or high return |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P4-R1 | Extending the F5 concept vocabulary (current assets and liabilities, share count, dividends per share, insurance concepts): F5 change control, re-audit, re-freeze, re-staging | Current ratio, book value per share, insurers |
| P4-R2 | Whether to fix the open F3/F5 findings (P-18, P-23, P-1) before the pilot | How many issuers have usable panels |
| P4-R3 | The merge into `main` and the re-staging that follows | Production use |
| P4-R4 | Exposing F6.1's sign-convention result, or the matched label, through F8 (a change to frozen F8) | Any feature that needs the orientation of `impairment_charges` or tax; cash-flow totals that the identity cannot confirm |

---

## 14. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| F7-1 | Panel as of T from F8; guards per pipeline; usable-fact table; scope, anchor, currency, sign and link rules | Unit tests on new multi-concept, multi-period factories built over F8's (`tests/f8_factories.py` builds one value per document today); FL-1 to FL-5, FL-8, FL-11, FL-15, FL-19 |
| F7-2 | Period arithmetic (F7P-1 to F7P-3 and F7P-5 to F7P-8), dates, disagreeing paths, the cash-flow sign identity | Hand-computed cases for 3, 6, 9, 12-month and quarter-only reporters, March and December year-ends, a non-month-end period end |
| F7-3 | Feature registry and set v1; EPS restatement; the valuation join with MF | Every formula against a hand-computed vector, with a negative-equity and a loss-making case for each ratio; FL-6, FL-12, FL-14, FL-16 to FL-18, FL-20 to FL-22 |
| F7-4 | Financial eligibility | Unit tests of each result |
| DE-1 | Engine contract; universes; the score formula with ties, small n and absent components | Unit tests; determinism |
| DE-2 | Engine set v1 | Each engine on a synthetic universe with known ranks; an issuer with two lines counted once; FL-7, FL-13 |
| DE-3 | Reference baselines | Each fills exactly its fields; FL-9 |
| DE-4 | Migration 0020, store, preflight, `verify`, wrapper, runbook | PostgreSQL 17 in CI; append-only for every role; mutation tests; FL-10 |

F7-1, F7-2 and F7-4 depend only on F8. F7-3's valuation part and all of DE need Phase 3's MF-1 to MF-5.

---

## 15. What stays open until real data exists

- How many issuers have a usable panel. Real-data validation found facts for one issuer, with eight conflicting
  identities inside one document (MA §52).
- **How often prior-period facts conflict.** Each later filing's comparative column is a second observation of last
  year's figure; a restated comparative makes that fact `conflicting`. Growth and F7P-2 lean on exactly those facts.
- How often period ends fail to match exactly (§5.1), and how often derivation paths disagree.
- Whether `marketCap` is per security and consistent with price × shares over time (§6.3).
- How often an ex-date falls between a period end and its document's availability (§6.3).
- How often the cash-flow identity fails to confirm a sign (§4.5), and so how much of `fund_cash_flow` survives. Two
  structural causes are already known: an exchange-rate line, and an interim period whose prior year-end report is
  not in evidence.
- **Interim against annual.** An unaudited fourth-quarter interim and the audited annual report give two
  observations of the same year-end figures. If they differ, the anchor is `conflicting` and the issuer is blocked
  until its next quarter.
- Whether voting and non-voting shares earn the same per share (§6.3).
- `eps_ttm_approx` will often be `conflicting` between its paths, because weighted share counts differ by period.
- Fundamental ranks compare issuers whose anchor periods can be up to 270 days apart.
- Where assets equal liabilities plus equity, `liabilities_to_equity` and `equity_to_assets` rank issuers
  identically, so `fund_leverage` carries one piece of information, not two.
- Every eligibility value, `N_min`, and whether any engine's "favourable" direction has predictive value at all.

## 16. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | Two new read-only layers: F7 above F8; DE above F7 and MF |
| 2 | Source of truth | F8 results for financial data; MF snapshot rows for market data; `issuer_securities` decisions as of T |
| 3 | Information at its timestamp | One cutoff T for both layers; F8's mode with explicit H and configuration in `AVAILABLE`; D\* derived |
| 4 | Provenance retained | Row hashes, `ef_key`s, F8 `result_hash`, link decision id, MF row hashes (§10.3) |
| 5 | Frozen components | None modified; the range-stating guards at DE-4 |
| 6 | Migration | Yes: 0020, additive |
| 7 | Leakage prevention | F8's two guards, explicit H and configuration, the as-of link, same-T universes, the target import ban, FL-1 to FL-22 |
| 8 | Testing | §12, §14; PostgreSQL 17 in CI |
| 9 | Reproduction | Deterministic Decimal arithmetic; configuration and input hashes; `verify` |
| 10 | Route 1 and Route 2 | Route 1 consumes signals, components and baselines. Route 2 receives the same features as structured input, never the scores as instructions |
| 11 | Later evaluation | Baselines and calibrated engines are scored by Phase 9 against MF's labels, beside ML |
| 12 | After downtime | Nothing is scheduled in Phase 4. A late filing appears in `KNOWN` from when it was recorded, never earlier |
| 13 | Failure representation | Panel states, fact treatments, value states with reason codes, engine states, eligibility reasons |
| 14 | Versions | Registries, rules, policies and constants in configuration hashes; code version tuple per run |
| 15 | Grounded reasoning | `components` lists each input value, its state, its rank and its contribution (§7.4) |

## 17. Audit record

**Revision 1 (commit `95310b2`): audited 2026-11-11 (cloud, fresh-context agent; C-0005). Verdict: FAIL**, three
Blockers, fifteen Major, seven Minor, three Notes. The auditor verified the claims about F8's API, guards and states,
the fact identity, the 36-concept vocabulary and every concept key used, and the `marketCap` evidence; no test was
run. Dispositions in revision 2:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Blocker | EPS and price on different share bases | **Fixed.** `f7.eps_basis.1` (§6.3); FL-12, FL-14 |
| 2 | Blocker | The magnitude rule turned an impairment reversal into a charge | **Fixed.** `f7.sign.2` limited to five gross concepts; `cost_of_risk_proxy` removed; P4-R4; FL-15 |
| 3 | Blocker | The issuer-to-security join was not point-in-time | **Fixed.** `f7.security_link.1` (§4.6); the decision id in the row hash; FL-11 |
| 4 | Major | The read boundary could not supply `market_cap_reported`, `px_ref` or the base-rate inputs | **Fixed.** Phase 3 revision 2 registers and stores them; §3 names the read |
| 5 | Major | F8's live rule not enforced | **Fixed.** `require_live` on the live pipeline (§4.1, §3); FL-8 |
| 6 | Major | `AVAILABLE` semantics misdescribed; H missing from keys | **Fixed.** Explicit H and configuration id; H in both keys (§4.1, §10.1); FL-10 restated |
| 7 | Major | The cross-sectional score was undefined | **Fixed.** §7.2 to §7.4: formula, ties, `N_min`, universes, issuer-level ranking, required components; the boolean and count inputs are no longer ranked (`fund_quality` removed) |
| 8 | Major | "Or" inputs substituted one feature for another | **Fixed.** One definition per rank (§8, P4-D11) |
| 9 | Major | Excluding `unlabelled` scope removed single-entity issuers | **Fixed.** Allowed when it is the only scope (§4.3) |
| 10 | Major | The scope policy was circular and unstable | **Fixed.** Newest period defined first; scope from presence, not usability; `scope_changed` against T minus 12 months (§4.3) |
| 11 | Major | A conflict at the newest period was masked | **Fixed.** §4.3, §5.4, §11 (and "non-null" in the last criterion) |
| 12 | Major | F8 outputs not covered | **Fixed.** §4.2's table |
| 13 | Major | No currency selection rule | **Fixed.** `f7.currency.1` (§4.4) |
| 14 | Major | Absence treated as missing biased against unlevered issuers | **Fixed.** by removing those inputs from the engine (§8, P4-D13); the features remain |
| 15 | Major | Owners-only definitions with no fallback | **Fixed.** Total-basis ids for everyone; owners' ids separate (§6.2) |
| 16 | Major | Baselines contradicted P4-D1 and MA §21; one conflicted with FL-9 | **Fixed.** §9: fields filled per baseline; direction-only momentum; base rate from MF's feature path |
| 17 | Major | Leak paths without tests; `AVAILABLE` tests ill-constructed | **Fixed.** FL-11 to FL-15, FL-4 to FL-7 widened; the two-evidence-set construction (§12) |
| 18 | Major | The requirements table overstated | **Fixed.** §2 lists every family's status |
| 19 | Minor | F7P-2's stated condition wrong; date arithmetic unspecified | **Fixed.** §5.1, §5.2 |
| 20 | Minor | TTM growth needs three years | **Fixed.** F7P-7 and the `_cum` features; the conflict-rate question in §15 |
| 21 | Minor | Labels and modes did not match F8; T compared with D\* | **Fixed.** §4.1 |
| 22 | Minor | Industry group under-specified | **Fixed.** §7.2 |
| 23 | Minor | Individual feature caveats | **Fixed.** in §6.2's definitions and §6.3 (staleness inherited); `has_discontinued` (§4.3) |
| 24 | Minor | The representative can lie outside the interval | **Fixed.** Path agreement uses interval widths (§5.2) |
| 25 | Minor | `unlabelled` reused | **Fixed.** Reason codes (§6.4) |
| 26 | Note | Fixture evidence on `marketCap` and `quantityIssued` | Adopted, as `provisional` (§6.3; Phase 3 §5.1) |
| 27 | Note | Factories build one value per document | Recorded in §14 (F7-1) and §3 |
| 28 | Note | `mkt_ret_ew_63` missing in Phase 3 | Added in Phase 3 revision 2 |

**Revision 2 (commit `9e26947`): audited 2026-11-12 (cloud, fresh-context agent). Verdict: FAIL**, one Blocker,
seven Major, nine Minor, one Note. Of the 28 earlier findings it judged 21 resolved and 7 partly resolved. It
verified by hand the factor direction for EPS, the five magnitude concepts' mapping rules, the link rule against
migration 0007, the `AVAILABLE` semantics against F8's code, the score formula's range and ties, the date rule on
month-ends, leap days and the 25th, and the F7P-2 interval identity. It did not re-read every Master Architecture
section this round. Dispositions in revision 3:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| N1 | Blocker | Restated EPS was divided by a raw carried price on the old share basis | **Fixed.** Both sides on D\*'s basis through `adj_ref` (§6.3, P4-D16); Phase 3 revision 3 stores it; FL-16 |
| N2 | Major | `eps_fy` did not exist at interim anchors, so EPS valuation was mostly missing | **Fixed.** `eps_latest_fy` (§6.2, P4-D17) |
| N3 | Major | The EPS product was not limited to share-count actions; `cum_price_stale` not carried | **Fixed.** By action kind; the flag carried; the factor list's shape required (§6.3, §3); FL-17 |
| N4 | Major | The availability instant A undefined for several documents, for none, and at the boundary | **Fixed.** §6.3; FL-18 |
| N5 | Major | "Issuer (ordinary line)" undefined; an issuer's second line would get the first line's price | **Fixed.** A security-level engine (§7.2, §8, P4-D16) |
| N6 | Major | Cash-flow totals' printed sign is not label-safe | **Fixed.** `f7.cashflow_sign.1`: confirmed by the cash identity or not used (§4.5, P4-D18); FL-20; P4-R4 widened |
| N7 | Major | The anchor's duration, `none` facts, the unlabelled-only newest period, `scope_changed`, operations | **Fixed.** §4.3; FL-19 |
| N8 | Major | No list of ratios needing a positive denominator | **Fixed.** §6.2 (P4-D19); negative-equity and loss cases in F7-3's exit |
| N9 | Minor | Magnitude before or after period arithmetic | **Fixed.** Per fact, before (§4.5) |
| N10 | Minor | The mapping from MF's security key to `companies.id`; imported-only securities | **Fixed.** §4.6, with the coverage label; `link_blocked` removed from issuer eligibility (§11) |
| N11 | Minor | Path agreement could reject consistent paths | **Fixed.** Interval overlap (§5.2) |
| N12 | Minor | `unlabelled` inputs, `input_unverified`'s state, the fan-out target, a null called `partial` | **Fixed.** §7.2, §7.3, §6.3 |
| N13 | Minor | F8 and F6 outputs still not covered | **Fixed.** §4.2 |
| N14 | Minor | Keys and where the factor list lives | **Fixed.** §10.1, §3 |
| N15 | Minor | Currency rule's period and scope; no decision id | **Fixed.** §4.4, P4-D15 |
| N16 | Minor | A dead rule; date rule not associative on the 29th and 30th; instant growth without a base condition | **Fixed.** F7P-4 removed; §5.1; F7P-8 |
| N17 | Minor | Reason codes missing; "one filing" overstated; `KNOWN_RECORDED` | **Fixed.** §6.4, §6.2, §8, §4.1 |
| N18 | Note | Interim against annual; redundant leverage inputs; EPS paths; mixed anchor dates | Recorded (§15) |

**Revision 3 (commit `2a17542`): audited 2026-11-12 (cloud, fresh-context agent). Verdict: FAIL**, no Blocker, two
Major, fifteen Minor. It worked the EPS restatement by hand for a split after the last trade, a split before it, a
split inside the ambiguity window, two actions in sequence and a rights issue, and found the yield correct in each:
**the revision 2 Blocker is resolved.** Of N1 to N8 it judged five resolved and three partly resolved. Dispositions
in revision 4:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | Major | The unexplained-move guard, and the factor list's shape, read inputs Phase 3 did not store | **Fixed.** Phase 3 stores `mf_snapshot_factors` and `mf_snapshot_move_flags`; §3 and §6.3 read them; FL-21 |
| F2 | Major | `eps_latest_fy`: which fact states count, and how old it may be | **Fixed.** §6.2; FL-22 |
| N6 residue | — | The identity's opening balance, the exchange-rate line, and unconfirmed values still present | **Fixed.** §4.5: stated; unconfirmed totals are null |
| a to o | Minor | A defined twice; partial availability; the state of unconfirmed totals; denominators and negative equity; the non-eligible line; rights per line; reason codes; eligibility results; a stale rule id and duration range; a cross-reference | **Fixed** in §6.3, §4.5, §6.2, §8 (`equity_to_assets` is now the required component of `fund_leverage`), §6.4, §11, §5.2, §14. Left as stated limits: which fact the conflict test reads when a period has two durations (the n-month one, by §4.3's definition of n), and that an issuer's two lines both enter the one security-level valuation rank |

Revision 4 is to be audited again before any implementation.
