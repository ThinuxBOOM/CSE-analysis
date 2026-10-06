"""
Phase 2 HB-4: the document worker (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md sections 9, 10.1, 10.2, 14.2, 15, 17 and
27). A library only: no command, entry point or timer (HB-6 owns those).

HB-4 is orchestration around the frozen producers. For one filing at a time it makes exactly the calls F5's run()
composes (design HB-R1, HB-B1, D6), minus the CLI cap and plus the ledger:

    F5 load_filings_from_db -> F2 process_batch([filing], F5 make_consumer(...), fetcher=<HB-2 governed fetcher>,
    temp_root=<dedicated root>) -> F5 attach_timestamps -> one transaction: F5 _persist + the ledger event 'persisted'

    gate        the document gate (HB-U5, HB-R3): the current discovery plan (HB-3's Plan.of over the arming in force and
                the verified security master, HB-P1) is closed and its IE-4 pass succeeded
    tools       the tool pin (section 10.2): exactly Poppler 24.02.0, checked before any download
    temproot    the dedicated temporary root: its validation, the free-space precheck (HB-R7) and the orphan sweep (17)
    signals     SIGTERM -> SystemExit while a slice runs, so that F2's own cleanup unwinds (section 17)
    evidence    F5 runs as evidence (section 15.3): the persisted run of a document item under the armed versions
    outcomes    F2 retrieval records -> item states (sections 14.2, 16.3, 24; HB-R6). Pure
    planning    document items for the filings of the armed window W (HB-R3, HB-R4): created, excluded, promoted
    worker      the document slice: G2 around HB-2's slice, the orphan sweep, reconciliation from evidence, terminal
                decisions at the claim maximum, and one filing at a time
    preflight   HB-4's own checks (frozen pins, static boundaries, compat, database reads), in addition to HB-1's,
                HB-2's and HB-3's

Nothing here contacts CSE or any network: every document request goes through HB-2's governed fetcher, inside a CSE
slice that holds P2's global lock, under the owner's arming decision. HB-4 adds no migration, grant, role, row-level
security, SECURITY DEFINER, lock key, command, entry point or timer, and changes no frozen file.

Deployment prerequisites (owner-run, after the software freeze; none of them is an implementation prerequisite):
  - HB-P1, HB-X2 and the owner's arming, as for HB-2 and HB-3 (runtime gates, unchanged);
  - a dedicated temporary root (CSE_BACKFILL_TEMP_ROOT) inside the system temp directory, ideally on tmpfs, owned by
    the worker and writable by no one else, with at least twice F2's 200 MB maximum free plus a margin (section 17);
  - Poppler 24.02.0 on the server (section 10.2);
  - P1's backups and the owner's backup checkpoints before bulk documents (section 21).
"""
TOOL_VERSION = "hb.documents.1"
RULE_VERSION = "hb.documents.1"

# Design section 14.1: the pilot (HB-S3) and the bulk documents (HB-S4) are HB-2's two document stages.
STAGES = ("HB-S3", "HB-S4")

# Design section 10.2: exactly one Poppler version for the whole backfill (F4 supports two; mixing them would create two
# F4 version tuples, so anything else is refused before any download).
POPPLER_VERSION = "24.02.0"

# Design section 17 / HB-R7: the dedicated root needs at least twice F2's maximum document size free, plus this margin.
FREE_SPACE_MARGIN_BYTES = 64 * 1024 * 1024

# The worker's dedicated temporary root (server configuration, never in the repository).
TEMP_ROOT_ENV = "CSE_BACKFILL_TEMP_ROOT"
