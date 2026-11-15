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

