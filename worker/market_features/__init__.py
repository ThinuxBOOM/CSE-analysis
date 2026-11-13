"""
Phase 3, the market feature foundation (docs/PHASE3_MARKET_FEATURES_DESIGN.md, revision 5; "MF").

A PURE package: no database, no clock, no network, no file. It turns evidence the caller has already loaded (raw market
observations, P2's run evidence, calendar and corporate-action statements) into point-in-time results. The store that
loads that evidence and writes snapshots is a separate package and a later step (MF-7).

MF-1 (this step): modes and admission; session finality and the market cutoff D*; the daily bar through the frozen
worker.reconciliation.reconcile; the feature-bar decision table; the reference price with its source session.

Nothing here reads daily_market_data, trading_calendar, corporate_actions, symbol_history, system_config or companies:
MF's sources are the append-only raw layer and run ledger (design section 3). tests/test_mf1_unit.py checks the
package's imports.
"""
VERSION = "mf.1"
