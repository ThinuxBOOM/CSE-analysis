"""
Phase 6, news ingestion (docs/PHASE6_NEWS_SENTIMENT_DESIGN.md §4): the governed transport, the fetch ledger, the
source governance records and the adapter interface. The transport is the only code of the phase that can open a
network connection, and only through `http.py` (a static test enforces it); everything else is pure or works on
records. No real news host is configured anywhere: sources exist only with an owner-approved, hash-pinned governance
record (P6-R1), and CSE's hosts are refused unconditionally.
"""
VERSION = "ns_ingest.1"
