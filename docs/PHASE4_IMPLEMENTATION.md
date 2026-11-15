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
