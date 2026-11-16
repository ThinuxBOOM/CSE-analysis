# Phase 4: financial features and deterministic engines (implementation record)

Built on `integration/cloud` against `docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md` (revision 6, accepted, C-0027).
Each step is accepted when a fresh-context audit reports no Blocker (C-0037). Nothing here contacts a network.

## F7-1: the fundamental panel as of T

**Status: implemented and tested; see the audit sections below for its state.**

| File | Content |
|---|---|
| `worker/financial_features/panel.py` | §4.2's usability table per F8 fact; the guards per pipeline (`live`: `require_live`; `point_in_time`: `require_point_in_time`, and `AVAILABLE` must carry H and the F8 configuration id); the newest period (facts F8 shows only, so a held but not yet public document never moves it: FL-19); `f7.scope.1`, `f7.currency.1`, the anchor's duration n, the panel states (`ok`, `anchor_absent`, `scope_unavailable`, `currency_ambiguous`, `critical_conflict`, `anchor_unusable`) and flags (`scope_changed`, `has_discontinued`, `unlabelled_scope`); `f7.sign.2`; `f7.security_link.1` |
| `worker/financial_features/dates.py` | §5.1's E−k on F6.1's `_add_months` (moved from `periods.py`) |
| `tests/f7_factories.py` | Built F8 FactViews and sealed results; multi-concept documents through F6.3 and F8's own World |
| `tests/test_f7_panel_unit.py` | Every usability row, scope, currency, anchor and flag rule; FL-8, FL-11, FL-15; FL-1 to FL-5 and FL-19 through F8's selection on synthetic worlds, each with its converse |

Choices for the audit:
- A panel with no anchor fact shown by F8 has the state `anchor_absent` (the design names no state for it).
- `scope_changed` compares the scopes of the anchor facts F8 shows at E−12 (C-0073: a `none` fact never shapes the
  panel).
- `has_discontinued` is set by any `discontinued` fact of the chosen scope at the anchor period.
- Facts in other scopes or currencies are not in the panel's table at all, so no formula can mix them.

## F7-2: period arithmetic

**Status: implemented and tested; see the audit sections below for its state.**

| File | Content |
|---|---|
| `worker/financial_features/dates.py` | §5.1: E−k on F6.1's `_add_months`; exactness only for month-ends and days up to the 27th (C-0073) |
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

**Status: implemented and tested; see the audit sections below for its state.**

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

**Status: implemented and tested; see the audit sections below for its state.**

| File | Content |
|---|---|
| `worker/financial_features/eligibility.py` | §11's criteria under `financial_eligibility_policy.0` (provisional: 2 fiscal years, 270 days); every failed criterion kept, the result by §11's order; the measured years, age and the non-null growth and return features |
| `tests/test_f7_eligibility_unit.py` | Each result, the 270-day boundary, a conflicting `total_equity` |

Choices for the audit: a panel in `anchor_absent` is `no_panel`; "fiscal years with a usable 12-month anchor fact"
counts distinct period ends of numeric 12-month `profit_for_period` facts in the chosen scope and currency; T's date
is its UTC date.

## DE-1 to DE-3: the engine contract, engine set v1 and the baselines

**Status: accepted (C-0076).**

| File | Content |
|---|---|
| `worker/engines/registry.py` | The engine contract (§7.1) and engine set v1 (§8): fifteen engines, equal weights, required components, minima; `beta_126` reported with weight 0 |
| `worker/engines/scoring.py` | §7.3: n_c over admissible non-null values; `N_min` 10 (provisional); p = (below + ½ equal)/n_c; s_c = orientation × (2p − 1); the weighted mean; `insufficient_inputs`, `blocked_quality` (a required component conflicting or untrusted), unlabelled absent; the EngineOutput (§7.4) with every component, the universe's digest and the inputs hash; fan-out of an issuer output to its linked lines |
| `worker/engines/baselines.py` | §9: `base_no_change`, `base_rate_252` (MF's `hist_*` features; the tie rule), `base_momentum_sign`; every other field null, `not_provided_by_baseline` |
| `tests/test_de_engines_unit.py` | Hand-computed percentiles with ties and orientation; small n; required, minimum, blocking and unlabelled components; weight 0; `fund_valuation_mcap` blocked while untrusted; fan-out with an issuer counted once; determinism; FL-7 and FL-13 by the universe passed; the baselines; FL-9 (no target-module or network import in `worker/engines` or `worker/financial_features`) |

Choices for the audit: a weight-0 component is reported but counts toward neither the mean nor `min_components`;
the selection of each universe (eligibility, links, groups) is the caller's (the store step, DE-4), and scoring ranks
exactly the members it is given.

## Second F7 audit: F7-1/F7-2 re-audit and F7-3 first audit (fresh-context agent)

At `ab685f7`. Guard log empty. 95 mutants, 67 killed. **F7-1 and F7-2: REJECT** (one Blocker; every first-audit
finding confirmed fixed). **F7-3: REJECT** (five Blockers).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | A TTM path that does not apply (a fact absent) blocked the TTM when another of its inputs was conflicting | **Fixed:** applicability decided per part. `test_b1` |
| B-2 | Blocker | Look-ahead: a held, not yet public document changed the industry group (`KNOWN`) | **Fixed:** shown facts only. `test_b2` through F8, with its converse |
| B-3 | Blocker | Valuation never checked that the panel currency is LKR | **Fixed:** `currency_mismatch` for every valuation and market-value feature. `test_b3` |
| B-4 | Blocker | A day-precision availability (stored as the next 00:00 Colombo) was read one day late | **Fixed:** that day. `test_b4` |
| B-5 | Blocker | A `partial` value escaped the stale-price limit | **Fixed:** every value-carrying state is checked. `test_b5` |
| B-6 | Blocker | `eps_ttm_approx` used F7P-1 (a conflicting or different annual EPS blocked it) and dropped intervals | **Fixed:** F7P-2 and F7P-3 only; intervals scaled by the factor product; a `cum_price_stale` component makes it `partial`. `test_b6` |
| M-a | Major | Ratios and `pe_eps` took the first null input's state, not Phase 3's order | **Fixed.** `test_ma`; `pe_eps` with EPS 0 is `denominator_zero` |
| M-b | Major | `cash_reconciles` treated an interval end of 0 as absent | **Fixed.** `test_mb` |
| M-c | Major | `eps_latest_fy` chose a year among every operations value | **Fixed:** total operations only. `test_mc` |
| M-d | Major | Valuation had no panel-state guard | **Fixed:** a non-ok panel nulls every valuation feature as `compute` does. `test_md` |
| M-e | Major | Values lacked §6.1's provenance and ages | **Fixed:** `features.annotate` adds scope, currency, anchor, F8 hash, label class, both ages and `unlabelled_scope`. `test_me` |
| M-f | Major | §14's exit cases incomplete | **Tested:** loss and negative cases for the remaining ratios, `net_margin_chg` by hand, `eps_ttm_approx`, the market-value features, FL-6, FL-12, FL-17 under a price-adjusting policy |
| M-g | Major | Rules surviving mutation | **Tested:** `has_discontinued` from shown facts, the industry at the anchor only, untrusted inputs blocking, the reason boundary at date(A), mixed availability, the flags |
| M-h | Major | Record mismatches | **Fixed** above |
| M-i | Major | `Spec.positive_denominator` unused; flags inconsistent with §6.2 | **Fixed:** every ratio reads its flag from the registry; `accounting_identity_gap` and `cash_to_assets` follow §6.2's list (no positive requirement) |
| — | Minors | One-bound magnitude intervals; a duration without months; the reason codes and flags not in §6.4; the straddle check redundant | Fixed where code; the rest **carried** |

Each Blocker fix and the main Majors were reverted as mutants: all ten are killed.

## Third F7 audit, and first audit of F7-4 and DE-1 to DE-3 (fresh-context agent)

At `f051ce0`. Guard log empty. 97 mutants, 71 killed. **F7-1 to F7-3: REJECT** (one Blocker). **F7-4: ACCEPT** and
**DE-1 to DE-3: ACCEPT** (no Blocker; C-0037), with Majors, all now fixed or tested.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | In `KNOWN`, a held, not yet public document (F8 `none`) made a TTM path applicable, so a conflicting input in that path blocked a clean TTM (every TTM feature; `eps_ttm_approx` was **not** covered by this fix, see the fourth audit) | **Fixed:** a fact F8 does not show is as absent as one never filed for applicability (`not_shown`). `tests/test_f7_audit3_unit.py::test_b1` through F8 in both modes, with its converse; the reverted fix fails it |
| 1 | Major | FL-21 (d) gave `eps_basis_ambiguous` with an entered action | **Fixed:** `no_close_since_document` is checked first. `test_v1` |
| 2 | Major | B-6's interval scaling untested | **Tested.** `test_v2` |
| 3 | Major | §7.2's universes not built | **Built:** `fundamental_universe` (financially eligible, a market-eligible linked line, the group; `not_applicable` otherwise), `valuation_eps_universe`, `market_universe`. `test_u1` (FL-13 with its converse) |
| 4 | Major | DE-2's exit not met | **Tested:** every engine of set v1 on a known-rank universe, orientations and minima. `test_d1` |
| — | Majors | Untested F7-3 rules (the `equity_to_assets` flag, bank margins `partial`, ex-dates at date(A) and at D\*, the SHOWN filter for `eps_latest_fy`, the stale boundary), F7-4 criteria (conflicting `total_assets`, usable, 12-month and total-operations years, partial and stale features, the order of results), DE (required versus minimum, the universe digest in `inputs_hash`, baseline fields) | **Tested:** `test_v3`, `test_f1`, `test_e1`, `test_d2` |
| — | Minors | `net_margin_bank` passed `pos` as `bank` (correct by accident); `cash_reconciles` ignored the state order; `annotate`'s day-precision age; `base_rate_252` accepted unlabelled values | **Fixed** |
| — | Minors | Edge cases (an out-of-the-money rights factor in the ambiguous window, `not_evaluable` moves, `stale_sessions` null), UTC dates for ages, `avg_instant`'s date limit, reason codes outside §6.4, the feature version on engine components, MA §4's extra baseline fields | **Carried** to DE-4 or recorded |

## Fourth F7 audit (fresh-context agent)

At `a8e52f1`. Guard log empty. A seeded differential search through F8 over 2 200 worlds (4 400 comparisons in
`KNOWN` and `AVAILABLE`, 14 kinds of plant) and 83 mutants (70 killed). **F7-1 to F7-3: REJECT** (one Blocker).
`AVAILABLE` showed no difference anywhere.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | `eps_ttm_approx`'s restating wrapper dropped F8's state, so a held EPS fact made a path applicable in `KNOWN` | **Fixed:** a fact F8 does not show stays not shown through the wrapper. `tests/test_f7_audit4_unit.py` (both reproductions through F8, `AVAILABLE`, the converse); the reverted fix fails both. The auditor's 1 600 further worlds on the fixed code showed no state or value difference |
| M-1 | Major | FL-21's move window untested at its bounds | **Tested.** `test_m1` |
| M-2 | Major | The record said the third audit's fix covered `eps_ttm_approx` | **Corrected** above |
| M-3 | Major | The `AVAILABLE` guard (H) untested | **Tested.** `test_m3` |
| — | Minors | The internal `not_shown` marker reached feature flags; `earlier_period_than_anchor` was a flag only | **Fixed:** the marker stays internal (`test_n1`); the reason and the period end are recorded |
| — | Minors | A held fact's existence shows in reasons and provenance in `KNOWN` (`not_yet_available`, as §4.2 requires; states and values never change); `Panel.counts` includes `none` facts, as F8's own hash does | **Recorded** as consistent with F8 and §4.2 |

