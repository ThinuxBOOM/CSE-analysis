"""
Phase 2 historical financial backfill (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md). Step HB-1: the PostgreSQL ledger.

    states     the work-item state machine; the Python mirror of migration 0016's backfill_item_transitions
    keys       natural work-item keys, Colombo days and request-budget accounting (pure)
    records    F2 retrieval records as ledger rows: metadata only, temporary paths redacted (pure)
    store      the worker's ledger operations (migration 0016, records L2-L11)
    owner      owner decisions through the owner path: arming (L1), hold resolutions (L7), block acknowledgements (L9)
    preflight  the worker role's checks before any ledger write: migration lineage, frozen pins and versions, the
               PostgreSQL role and schema model, the ledger's rule data, and that this package has no network code

Nothing here contacts CSE or any network. There is no transport, discovery, retrieval, issuer acquisition, F6
orchestration, audit or runner: those are steps HB-2 to HB-6. PostgreSQL is the authority. Every ledger write is
append-only, except the guarded heartbeat, release and expiry of wake-ups and leases.
"""
TOOL_VERSION = "hb.ledger.1"

LEDGER_MIGRATION = "0016_historical_backfill_ledger.sql"
LEDGER_MIGRATION_SHA256 = "07727e13dd9061d32976ae7e204659b331c0087c5ee16e1bf2554706931e1701"

WORKER_ROLE = "cse_worker"
OWNER_ROLE = "cse_owner"
# P1's owner-delegation login: only root reaches its OS user (sudo); it acts as cse_owner through SET LOCAL ROLE.
OWNER_PATH_ROLE = "cse_migrator"

# The side-effect-free helpers of migration 0016 that its guards call in the worker's own session: the worker holds
# EXECUTE on exactly these and on no trigger function (the preflight checks both).
HELPER_FUNCTIONS = ("hb_cse_lock_key()", "hb_holds_cse_lock(integer)", "hb_session_started()",
                    "hb_session_alive(integer, timestamp with time zone)")
TRIGGER_FUNCTIONS = ("hb_rule_data_guard()", "hb_owner_decision_guard()", "hb_wakeup_guard()", "hb_lease_guard()",
                     "hb_attempt_guard()", "hb_outcome_guard()", "hb_retrieval_guard()", "hb_hold_guard()",
                     "hb_block_guard()", "hb_anomaly_guard()", "hb_item_event_guard()")
