"""
P2 market capture: a persistent, auditable CSE market-data capture layer wrapped around the FROZEN Stage E code.

    python -m worker.market_capture capture --trading-date 2026-09-28 --mode post_close

This package owns: the explicit trading date, run identity and state, request sequencing, the G-1 request discipline
(one request at a time, >= 1.5 s apart, bounded backoff, stop on blocks, identifiable User-Agent, no proxies, no
cookies), the raw-response archive (P1 filesystem spool FIRST, then PostgreSQL), completeness accounting, resume,
reprocessing and spool recovery.

Frozen Stage E keeps owning everything it owned: field mapping (worker/mapping.py), the raw_market_observations
structure and writes (worker/db.py), reconciliation (worker/reconciliation.py) and validation (worker/validation.py).
They are called unchanged. Stage E's own request path (cse_client / capture_*.py) is NOT used for capture: it keeps
only parsed JSON (not the exact bytes) and requests companyInfoSummery for every security (about 330 requests a day
instead of the P0.5 plan's about 55-65).

G-1: CSE data use is an owner-accepted risk, NOT CSE authorization (docs/governance/G-1_CSE_DATA_USE.md). Nothing here
may redistribute raw CSE responses, and nothing here deletes anything (purge is a separate, owner-only, designed-but-not-
implemented procedure: docs/ops/P2_MARKET_CAPTURE.md).
"""
TOOL_VERSION = "p2.capture.1"
