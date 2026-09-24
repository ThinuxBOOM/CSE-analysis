"""
Stage F1 CLI: discover CSE financial filings (metadata only — never downloads
a document, never derives a reporting period). See worker/report_discovery.py.

Usage:
    # Feed window by UPLOAD date, zero database (in-memory), JSON report:
    python -m worker.discover_financial_filings --store memory \\
        --from-date 2026-09-01 --to-date 2026-09-24

    # Same, persisting the in-memory state so a second run can prove idempotency:
    python -m worker.discover_financial_filings --store local-json --state-file f1_state.json \\
        --from-date 2026-09-01 --to-date 2026-09-24

    # Real database (DATABASE_URL = restricted cse_worker role; migration 0004 applied):
    python -m worker.discover_financial_filings --store postgres \\
        --from-date 2026-09-01 --to-date 2026-09-24

    # Per-company listing enrichment (full symbols, works for delisted securities):
    python -m worker.discover_financial_filings --store memory --symbols COMB.N0000,NEST.N0000
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import report_discovery


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, choices=["memory", "local-json", "postgres"])
    parser.add_argument("--state-file", default=None, help="required with --store local-json")
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--chunk-days", type=int, default=31, help="feed window size per request")
    parser.add_argument("--symbols", default="", help="comma-separated full symbols for /api/financials")
    parser.add_argument("--request-delay-seconds", type=float, default=1.0)
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if bool(args.from_date) != bool(args.to_date):
        parser.error("--from-date and --to-date must be given together")
    if not args.from_date and not symbols:
        parser.error("give a --from-date/--to-date window and/or --symbols")
    if args.store == "local-json" and not args.state_file:
        parser.error("--store local-json needs --state-file")

    conn = None
    if args.store == "postgres":
        from . import db, report_filings_store   # only here: memory runs never import the DB layer
        conn = db.get_connection()
        store = report_filings_store.PostgresFilingStore(conn)
    elif args.store == "local-json" and os.path.exists(args.state_file):
        store = report_discovery.InMemoryFilingStore.load(args.state_file)
    else:
        store = report_discovery.InMemoryFilingStore()

    try:
        report = report_discovery.discover(
            store, from_date=args.from_date, to_date=args.to_date, symbols=symbols,
            chunk_days=args.chunk_days, request_delay_seconds=args.request_delay_seconds)
    finally:
        if conn is not None:
            conn.close()

    if args.store == "local-json":
        store.save(args.state_file)
    report["store"] = args.store
    if args.store != "postgres":
        report["store_state"] = {"filings": len(store.filings), "observations": len(store.observations),
                                 "unresolved_company_filings": sum(1 for f in store.filings.values()
                                                                   if f["company_id"] is None)}
    report_file = args.report_file or (
        f"financial_filing_discovery_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    t = report["totals"]
    print(json.dumps({k: t[k] for k in ("requests", "rows_returned", "unique_filing_ids", "bucket_counts",
                                          "outcomes", "missing_path_filings", "duplicate_ids_within_a_response",
                                          "rejected_rows", "item_failures", "failed_requests",
                                          "runtime_seconds")}, indent=2, default=str))
    if report.get("store_state"):
        print("store_state:", report["store_state"])
    print(f"Report written to {report_file}")
    return 1 if t["failed_requests"] else 0


if __name__ == "__main__":
    sys.exit(main())
