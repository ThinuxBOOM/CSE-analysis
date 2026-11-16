# Phase 4: financial features and deterministic engines (implementation record)

Built on `integration/cloud` against `docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md` (revision 6, accepted, C-0027).
Each step is accepted when a fresh-context audit reports no Blocker (C-0037). Nothing here contacts a network.

## F7-1: the fundamental panel as of T

**Status: implemented and tested (2026-11-15); audit pending.**

| File | Content |
|---|---|
| `worker/financial_features/panel.py` | §4.2's usability table per F8 fact; the guards per pipeline (`live`: `require_live`; `point_in_time`: `require_point_in_time`, and `AVAILABLE` must carry H and the F8 configuration id); the newest period (facts F8 shows only, so a held but not yet public document never moves it: FL-19); `f7.scope.1`, `f7.currency.1`, the anchor's duration n, the panel states (`ok`, `anchor_absent`, `scope_unavailable`, `currency_ambiguous`, `critical_conflict`, `anchor_unusable`) and flags (`scope_changed`, `has_discontinued`, `unlabelled_scope`); `f7.sign.2`; `f7.security_link.1` |
| `worker/financial_features/periods.py` | §5.1's E−k on F6.1's `_add_months` |
| `tests/f7_factories.py` | Built F8 FactViews and sealed results; multi-concept documents through F6.3 and F8's own World |
| `tests/test_f7_panel_unit.py` | Every usability row, scope, currency, anchor and flag rule; FL-8, FL-11, FL-15; FL-1 to FL-5 and FL-19 through F8's selection on synthetic worlds, each with its converse |

Choices for the audit:
- A panel with no anchor fact shown by F8 has the state `anchor_absent` (the design names no state for it).
- `scope_changed` compares the scopes of anchor facts at E−12 in any state, as §4.3 reads "has an anchor fact".
- `has_discontinued` is set by any `discontinued` fact of the chosen scope at the anchor period.
- Facts in other scopes or currencies are not in the panel's table at all, so no formula can mix them.

## F7-2: period arithmetic

**Status: implemented and tested (2026-11-15); audit pending.**

| File | Content |
|---|---|
| `worker/financial_features/dates.py` | §5.1: E−k on F6.1's `_add_months`; exactness only for month-ends and days up to the 28th |
| `worker/financial_features/periods.py` | F7P-1, F7P-2 (n = 3, 6, 9), F7P-3, F7P-5 to F7P-8; `f7.sign.2` per fact before arithmetic; intervals added through sums; disagreeing TTM paths `conflicting` with every path kept, agreeing ones `corroborated_paths`; `f7.cashflow_sign.1` |
| `tests/test_f7_periods_unit.py` | Hand-computed cases: annual, half-year with a March year-end, nine-month with a December year-end, quarter-only, the 25th, a non-month-end after the 28th, disagreeing paths, a conflicting input, per-fact magnitude, growth and averages, `base_not_positive`, the cash-flow identity and FL-20 |

Choices for the audit:
- A missing input fact gives `missing`, reason `no_exact_period_match`; a printed nil gives `printed_nil`.
- Paths "agree" when all their intervals have a common point (the intersection of every agreeing path's interval).
- The cash-flow identity is checked for (E, n) against `cash_at_end_of_period` at E and E−n; a missing opening cash
  leaves the totals unconfirmed.

## F7-3 (part 1): the feature registry and the fundamental features

**Status: implemented and tested (2026-11-15); audit pending. Part 2 (EPS restatement and the valuation join, §6.3)
follows.**

| File | Content |
|---|---|
| `worker/financial_features/features.py` | The registry (`f7.features.1`): 39 fundamental features of set v1 with family, industries and the positive-denominator rule; `f7.industry.1`; ratio rules (`denominator_zero`, `denominator_not_positive`); the year-on-year features null under `scope_changed`; net margins `partial` under `has_discontinued`; cash-flow features through `f7.cashflow_sign.1` |
| `worker/financial_features/periods.py` | `instant`, `duration` and `total` helpers; a maturity argument for the borrowings |
| `worker/financial_features/panel.py` | The panel's fact key now includes the maturity (current and non-current borrowings are separate facts) |
| `tests/test_f7_features_unit.py` | A hand-computed vector for a general issuer and for a bank; negative equity and losses; zero denominators; both maturities required; scope change, discontinued operations, panel states; an unconfirmed cash-flow total |

Choices for the audit:
- A feature outside the issuer's industry group is `missing`, reason `not_applicable_industry` (a code not in §6.4's
  list).
- On a panel that is not `ok`, every feature takes the panel's condition: `conflicting` for `critical_conflict`,
  otherwise `missing` with the panel state as reason.
- `cash_reconciles` is 1 or 0, flagged `information_only`.
- `net_margin_chg` uses the bank margin for a `bank_finance` issuer.

## F7-3 (part 2): EPS restatement and the valuation features

**Status: implemented and tested (2026-11-15); audit pending.**

| File | Content |
|---|---|
| `worker/financial_features/valuation.py` | `f7.eps_basis.1` (factors of share-basis kinds with date(A) < e ≤ D\*; untrusted for an ex-date between the period end and date(A), for straddling observations, for an unknown availability or an availability flag, for an open move firing after the period end, for no close since the document, for an unresolved factor; `partial` for `cum_price_stale`); `eps_latest_fy` (no stepping back; `fy_eps_too_old`; `earlier_period_than_anchor`); `eps_ttm_approx` (each component restated by its own A; F7P-2 or F7P-3 only; `sum_of_per_share`); `earnings_yield_eps`, `pe_eps` on `adj_ref` with price states and staleness; `issuer_market_value` and the market-value features, `untrusted` with `input_unverified` while `market_cap_reported` is provisional |
| `worker/financial_features/panel.py` | `Usable` keeps F8's state, the period end and each visible observation's availability |
| `tests/test_f7_valuation_unit.py` | FL-14, FL-16, FL-17, FL-18, FL-21 (a) to (d), FL-22, price states, staleness, negative EPS, `cum_price_stale`, unresolved factors, the market value |

Choices for the audit:
- A price older than MF's stale limit makes a valuation `missing`, reason `stale_price` (a code not in §6.4's list).
- The market-value features are null (`untrusted` is a null state) while P4-D4 holds; their computed value is not
  stored.
- `eps_ttm_approx` uses F7P-2 or F7P-3 only, never F7P-1 (a printed annual EPS is `eps_latest_fy`).

## Audit of F7-1 and F7-2: fresh-context agent

At `a1780c0`. Guard log empty. 56 mutants, 38 killed. **REJECT** (one Blocker).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B1 | Blocker | In `KNOWN`, a document held but not yet public (F8 state `none`) shaped the scope, currency, n, the unlabelled-only rule and `scope_changed` (five channels reproduced through F8) | **Fixed.** Only facts F8 shows (single_source, corroborated, conflicting) shape the panel; design §4.3 clarified (C-0073). `test_b1` (each channel through F8, with its converse); each reverted channel fails it |
| M1 | Major | The fact key dropped the maturity | **Fixed** before the audit's report (F7-3 part 1) |
| M2 | Major | §5.1's exactness was false for the 28th | **Fixed:** days up to the 27th or month-ends, design and code; `test_m2` checks every date of 2000-2032 |
| M3 | Major | TTM `fresh` with a conflicting input in one path | **Fixed:** an applicable path with a conflicting or untrusted input makes the TTM take that state; design §5.2 (C-0073). `test_m3` |
| M4 | Major | Growth ignored the state order across its inputs | **Fixed.** `test_m4` |
| M5 | Major | Interval rules untested | **Tested:** subtraction bounds, magnitude bounds, a straddling interval, the cash identity within an interval. `test_m5` |
| M6 | Major | Leakage tests short of §12's AVAILABLE construction and converses | **Tested:** two evidence sets under one fixed H for FL-1, FL-2, FL-4; FL-3 before and after; FL-19's converse. `test_m6` |
| M7 | Major | Rules surviving mutation | **Tested:** `purchase_of_ppe` and `dividends_paid` magnitudes, n by currency, `has_discontinued` by scope, the table by scope, a zero base, the cash identity at E−6, `printed_nil`, `growth_cum` with n = 6, the link's highest id. `test_m7` |
| — | Minors | Printed sign not recorded; a straddling magnitude interval; a conflicting TTM kept numeric paths only; the cash identity's inputs and exactness | **Fixed** (`sign_rule:magnitude:<concept>:printed_<sign>`; [0, max]; every path kept; all three totals' ef_keys; `no_exact_period_match`) |
| — | Minors | `paths_disagree` and `anchor_absent` not in §6.4's lists; FL-15 shown on `signed()` only; the AVAILABLE configuration check cannot fire on a real F8 result (it always carries the resolved id) | **Carried** to the store step, where "passed explicitly" is enforced by the stored panel row |

## F7-4: financial eligibility

**Status: implemented and tested (2026-11-15); audit pending.**

| File | Content |
|---|---|
| `worker/financial_features/eligibility.py` | §11's criteria under `financial_eligibility_policy.0` (provisional: 2 fiscal years, 270 days); every failed criterion kept, the result by §11's order; the measured years, age and the non-null growth and return features |
| `tests/test_f7_eligibility_unit.py` | Each result, the 270-day boundary, a conflicting `total_equity` |

Choices for the audit: a panel in `anchor_absent` is `no_panel`; "fiscal years with a usable 12-month anchor fact"
counts distinct period ends of numeric 12-month `profit_for_period` facts in the chosen scope and currency; T's date
is its UTC date.

## DE-1 to DE-3: the engine contract, engine set v1 and the baselines

**Status: implemented and tested (2026-11-16); audit pending.**

| File | Content |
|---|---|
| `worker/engines/registry.py` | The engine contract (§7.1) and engine set v1 (§8): fifteen engines, equal weights, required components, minima; `beta_126` reported with weight 0 |
| `worker/engines/scoring.py` | §7.3: n_c over admissible non-null values; `N_min` 10 (provisional); p = (below + ½ equal)/n_c; s_c = orientation × (2p − 1); the weighted mean; `insufficient_inputs`, `blocked_quality` (a required component conflicting or untrusted), unlabelled absent; the EngineOutput (§7.4) with every component, the universe's digest and the inputs hash; fan-out of an issuer output to its linked lines |
| `worker/engines/baselines.py` | §9: `base_no_change`, `base_rate_252` (MF's `hist_*` features; the tie rule), `base_momentum_sign`; every other field null, `not_provided_by_baseline` |
| `tests/test_de_engines_unit.py` | Hand-computed percentiles with ties and orientation; small n; required, minimum, blocking and unlabelled components; weight 0; `fund_valuation_mcap` blocked while untrusted; fan-out with an issuer counted once; determinism; FL-7 and FL-13 by the universe passed; the baselines; FL-9 (no target-module or network import in `worker/engines` or `worker/financial_features`) |

Choices for the audit: a weight-0 component is reported but counts toward neither the mean nor `min_components`;
the selection of each universe (eligibility, links, groups) is the caller's (the store step, DE-4), and scoring ranks
exactly the members it is given.

