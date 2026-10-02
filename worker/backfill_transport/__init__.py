"""
Phase 2 historical backfill (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md). Step HB-2: the governed CSE transport.

Every Phase 2 CSE request goes through this package (design HB-B3, section 16). It is a library only: it has no
command, entry point or timer (those are step HB-6), and it makes no request unless every gate passes, including an
owner arming decision in force (HB-1 record L1) that names this host, this exact User-Agent and this frozen version
tuple.

    classify   per-attempt outcome classification (P2's order and vocabulary; F1's own shape acceptance for JSON)
    gates      the release gates: arming, User-Agent, host, version tuple, blocks, stopped stage, budgets, the P3 quiet
               window, the clock (pure decisions over snapshots read from PostgreSQL)
    ledger     the PostgreSQL reads and writes of the transport, through HB-1's frozen tables and guards
    journal    the transport's own spool journal (spool records and blobs are P1's, unchanged)
    throttle   >= the G-1 minimum spacing between ANY two CSE requests, seeded from P2's archive and the HB-1 ledger,
               and the release guard
    requester  the governed JSON requester (getFinancialAnnouncement, financials)
    fetcher    the governed F2 fetcher (cdn.cse.lk), the only module that imports a network library
    slice      one CSE slice: P2's exclusive lock, a wake-up, recovery, a lease, the per-request gates, release
    recovery   expiry of dead slices, recovering spooled responses before closing anything 'unrecorded'
    preflight  the transport's own checks, in addition to HB-1's

Two separate limits, never confused (owner decision A2):
  - HTTP attempts per logical request within ONE slice: arming `attempts_per_json_request` /
    `attempts_per_document` (P2's retry discipline);
  - ITEM CLAIMS since the item's last re-queue: arming `item_max_attempts` counts the slices that claimed the item
    (L3 'claim' events), NOT HTTP attempts. A claim beyond it is refused.

G-1: CSE data use is an owner-accepted risk, NOT CSE authorization (docs/governance/G-1_CSE_DATA_USE.md). A configured
contact e-mail or User-Agent is not permission to contact CSE.
"""
TOOL_VERSION = "hb.transport.1"
RULE_VERSION = "hb.transport.1"

# Design section 14.1: discovery requests (JSON) belong to HB-S2; documents to the pilot (HB-S3) and bulk (HB-S4).
STAGE_KINDS = {"HB-S2": "json", "HB-S3": "document", "HB-S4": "document"}

# One F2 document can make at most this many HTTP requests: two URL candidates (direct, legacy cmt/ fallback), each
# following at most F2's MAX_REDIRECTS = 2 redirects (owner decision A9: a document starts only if budgets cover it).
DOCUMENT_WORST_CASE_REQUESTS = 6

# Owner decision A6: the quiet window opens this long (plus the armed slice bound) before P3's due time.
QUIET_WINDOW_MARGIN_SECONDS = 60

# P3's backwards-clock tolerance (worker/scheduler/wakeup.py CLOCK_BACKWARDS_TOLERANCE), mirrored and parity-tested.
CLOCK_BACKWARDS_TOLERANCE_SECONDS = 300

# Owner decision A5: a stage is stopped when its last this-many completed leases (since the arming in force) ended
# 'circuit_open'; only a newer owner arming decision resumes it.
STOPPED_STAGE_SLICES = 3
