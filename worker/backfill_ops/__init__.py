"""
Phase 2 HB-6: operations and pilot (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md sections 14.1, 16.5, 16.6, 17, 20, 21,
23.4, 25 and 27; Master Architecture section 43.1). The runner, its command line and the operator and owner actions,
composed over the frozen HB-1 .. HB-5. Run it through ops/bin/cse-backfill (python -m worker.backfill_ops): as the
cse-worker OS user (PostgreSQL cse_worker) for everything except the owner decisions, which run as cse-migrator (the
owner path, acting as cse_owner for one INSERT).

    runner      one wake-up (section 20): the single-runner claim, the preflight, the wake-up gates, the non-CSE work
                (stage bookkeeping through HB-3, F6 work through HB-5, the daily audit), at most one CSE slice (HB-3
                discovery or HB-4 documents, which hold P2's lock ...312 themselves), the wake-up recorded (L8)
    claim       the single runner: the runner's own HB-1 wake-up row, recorded only in a SERIALIZABLE transaction
                that finds no other live runner (no new lock key; a racing runner records a 'busy' wake-up)
    decisions   the owner decisions (arm, disarm, acknowledge-block, resolve-hold) through HB-1's owner path, validated
                first: the exact User-Agent, this host, the running version tuple, the pilot sample (HB-S3) and the
                backup checkpoint (HB-S4)
    pilot       the pilot's stratified sample (a proposal the owner reviews and records when arming HB-S3) and the
                pilot report (section 27's exit criteria)
    status      the operator status and the plan (section 20): armed state, stages, items, budget, blocks, holds,
                wake-ups, disk and the alerts
    actions     the operator actions: requeue and resume (HB-1's guarded transitions), the late-evidence pass (HB-3)
    reports     the reports of section 25 and the coverage export, written only outside the repository
    preflight   HB-6's own checks (the pins of HB-5's package, its static boundary), beside HB-1 .. HB-5's
    config      the server configuration, from the environment only (nothing machine-specific in Git)

Nothing here contacts CSE by itself: every request is made by HB-3's or HB-4's slice through HB-2's governed transport,
under the owner's arming decision. HB-6 adds no migration, schema object, grant, role, row-level security,
SECURITY DEFINER or lock key, and changes no frozen file.
"""
TOOL_VERSION = "hb.ops.1"
RULE_VERSION = "hb.ops.1"
PILOT_RULE = "hb.pilot.1"

# Exit status, as P3 and F6.4: 0 ok (or busy), 2 needs attention, 3 an unacknowledged block stops every stage,
# 4 PostgreSQL unavailable, 5 refused (preflight, clock, a gate, or a refused operator action).
EXIT_OK, EXIT_ATTENTION, EXIT_BLOCKED, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 2, 3, 4, 5

DOCUMENT_STAGES = ("HB-S3", "HB-S4")      # the pilot and the bulk documents (HB-4's stages)
PILOT_STAGE, BULK_STAGE = DOCUMENT_STAGES
PILOT_SIZE = 50                           # section 27: a pilot of about 50 documents
PILOT_MAX = 200                           # an HB-S3 arming may name at most this many pilot filings
CHECKPOINT_DOCUMENTS = 2000               # section 21: a backup checkpoint after every 2,000 documents (HB-S4 blocks)
NOTE_MIN = 10                             # a reason or note: at least 10 characters (HB-1's owner rule)

REPORT_DIR_ENV = "CSE_BACKFILL_REPORT_DIR"     # the report directory: outside the repository (G-1 controls 7-8)
DB_DISK_ENV = "CSE_BACKFILL_DB_DISK_PATH"      # optional: a path on the database's filesystem, for its free space
DB_DISK_MIN_FREE_BYTES = 20 * 1024 ** 3        # section 20: database disk under threshold -> alert
