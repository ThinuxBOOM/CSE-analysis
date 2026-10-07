"""
Phase 2 HB-5: F6 orchestration and audit (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md sections 10.3, 11, 14, 15.3, 18,
19, 22, 25 and 27; the producer contract of docs/F8_DESIGN.md section 3.4). A library only: no command, entry point or
timer (HB-6 owns those).

HB-5 makes no CSE request, takes no lock of its own and opens no CSE slice. F6 rows are written only by F6.4's own
jobs, in F6.4's own transaction units and under F6.4's own lock; ledger rows only by HB-1's store; everything else is
read. It decides no value, period, issuer, winner or availability time.

    validation     validate-pending batches: F6.4's pending_runs (M4) and validate, one validate:<F5 run> item each
    configuration  the backfill configuration: F6.4's configuration_from_present_runs over ONE version tuple (the
                   armed one), registered; the owner designates it through F6.4's own owner path, never HB-5
    readiness      which issuers may be reconciled: every in-window filing of the issuer has a final document item
                   for its current path version (superseded or out-of-window items never hold one back)
    reconcile      the per-issuer reconcile items, then the final full pass, through F6.4's reconcile (--no-validate),
                   only while F6.4 has no pending validation (F6.4's reconcile is global before it is per issuer)
    promotion      document items from F6 evidence: persisted -> validated -> reconciled, and needs_validation (M4)
    snapshot       one read-only REPEATABLE READ snapshot, and the window W
    coverage       the coverage audit hb.coverage.1: the funnel (one unit per filing), its dimensions, the candidate
                   and fact levels, the expectation checks and review lists, the per-filing table and its digest
    anomalies      the detectors hb.anomaly.1: the real-data catalogue P-1 .. P-34 and the Phase 2 findings
    audit          audit:<n>: one snapshot -> coverage + anomalies -> the L11 snapshot and its L10 records
    reports        the reports of design section 25, built read-only and never written inside the repository
    preflight      HB-5's own checks, in addition to HB-1's and F6.4's

Re-running finished work is an explicit operator re-queue (HB-1's rule, never automatic): a validate item whose F5 run
needs re-validation after late evidence (M4), or a second reconcile pass of an issuer, waits for one.

Deployment prerequisites (owner-run; none is an implementation prerequisite):
  - P1's PostgreSQL 17 deployment with migrations 0001-0017 and its roles (cse_worker, cse_reader, cse_migrator);
  - the owner's designation of the registered configuration (F6.4's owner path), once the pilot's first F5 runs exist;
  - the documents themselves (HB-4, behind HB-P1, HB-X2 and the owner's arming, which also records W);
  - a report directory outside the repository on the server; optionally a private copy of the F0 captures (E1);
  - P1's backups and the owner's checkpoints (section 21).
"""
TOOL_VERSION = "hb.f6.1"
RULE_VERSION = "hb.f6.1"
COVERAGE_RULE = "hb.coverage.1"
ANOMALY_RULE = "hb.anomaly.1"

# Bounds of one call (design section 10.3: "in bounded batches"). The runner (HB-6) calls again at its next wake-up.
VALIDATE_BATCH = 50
RECONCILE_BATCH = 5
