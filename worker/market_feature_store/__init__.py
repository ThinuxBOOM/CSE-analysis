"""Phase 3 store (MF-7): loaders and writers around the pure package worker/market_features/ (design §13.2).

    store            load (one REPEATABLE READ, READ ONLY transaction) and write a snapshot
    snapshot         build one snapshot from admitted evidence (pure)
    history_bars     imported rows as feature bars (pure)
    tradeiq_import   the owner-run TradeIQ import (C-0035, C-0055)
    configuration    the content-addressed configuration a result names
    jobs             load, build and write under a stored configuration
    targets_store    target builds: labels of stored reference snapshots
    calendar_sync    the append-only copy of trading_calendar's closures
    selection        the designated configuration at an instant; KNOWN_RECORDED
    verify, explain  re-prove what is stored; explain one row down to its evidence
    preflight        the worker role's security preflight
    cli              python -m worker.market_feature_store <command>

Nothing here contacts CSE or any network: MF reads only what is already in PostgreSQL (and, for the owner's import,
local files).
"""
EXIT_OK, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 4, 5
