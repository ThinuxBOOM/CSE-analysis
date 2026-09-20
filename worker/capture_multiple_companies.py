"""
Stage E: controlled multi-company capture — a VALIDATION stage, not the full
production scheduler. Proves the single-company vertical slice (mapping,
reconciliation, db, validation — all reused unchanged, not duplicated) scales
safely to 10-20 companies in one run, with proper failure isolation,
provenance, idempotency, and a machine-readable capture report.

Two modes, same as capture_single_company.py:

  --dry-run   Real CSE calls, ZERO database connections. Symbol universe is
              still discovered/specified for real; no company_id lookup or
              writes happen.

  (default)   Full path: real CSE calls + real DB writes, reusing
              capture_single_company.fetch_and_map, db.py, reconciliation.py,
              and validation.py exactly as-is.

Symbol universe — two ways, NEVER invented:

  --symbols COMB.N0000,JKH.N0000,...   Explicit list you specify.
  --discover-count N                     Calls the real CSE allSecurityCode
                                            endpoint and takes the first N
                                            symbols from what CSE actually
                                            returns. If discovery fails or
                                            returns nothing, this refuses to
                                            proceed rather than falling back
                                            to a hardcoded list.

Efficiency: tradeSummary is fetched EXACTLY ONCE per run, regardless of how
many symbols are captured — every company's row is extracted from that one
shared response (see capture_single_company.fetch_and_map's
trade_summary_response parameter).

Usage:
    # Safe first check — no DB, explicit symbols:
    python -m worker.capture_multiple_companies --symbols COMB.N0000,JKH.N0000,LOLC.N0000 \\
        --window post_close --dry-run

    # Real capture, discovering 15 symbols from the live CSE universe:
    python -m worker.capture_multiple_companies --discover-count 15 --window post_close

    # Resuming a crashed batch (same attempt, no duplicates):
    python -m worker.capture_multiple_companies --symbols ... --window post_close \\
        --resume-attempt-id <uuid-from-the-crashed-run>
"""
import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from . import cse_client, capture_single_company, reconciliation, validation

DEFAULT_TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}


@dataclass
class SymbolResult:
    symbol: str
    status: str = "pending"   # 'success' | 'company_not_found' | 'http_failure' |
                                 # 'mapping_failure' | 'database_failure'
    reason: Optional[str] = None
    duration_ms: int = 0
    raw_observation_id: Optional[str] = None
    reconciliation_status: Optional[str] = None
    validation_status: Optional[str] = None
    open_price: Optional[float] = None
    post_open_price: Optional[float] = None
    closing_price: Optional[float] = None
    last_traded_price: Optional[float] = None
    share_volume: Optional[int] = None
    turnover: Optional[float] = None
    trade_count: Optional[int] = None


def _process_symbol(conn, symbol, window, request_attempt_id, observation_date,
                     trade_summary_response, tolerances, dry_run):
    """
    Processes exactly one symbol. Every exit point sets a definitive status —
    no path silently returns "success" without having actually reconciled
    something, and no path silently skips without recording why.
    """
    result = SymbolResult(symbol=symbol)

    company_id = None
    if not dry_run:
        from . import db
        company_id = db.get_company_id_by_ticker(conn, symbol)
        if not company_id:
            result.status = "company_not_found"
            result.reason = f"No company row found for ticker '{symbol}' — not inventing one."
            return result

    try:
        raw_fields, raw_payload = capture_single_company.fetch_and_map(
            symbol, window, trade_summary_response=trade_summary_response, verbose=False,
        )
    except capture_single_company.CSEFetchError as exc:
        result.status = "http_failure"
        result.reason = str(exc)
        return result

    observed_at = datetime.now(timezone.utc)

    if dry_run:
        synthetic_obs = {
            "id": "DRY-RUN-NOT-A-REAL-ID", "capture_window": window, "source": "CSE_API",
            "observed_at": observed_at.isoformat(),
            **{k: v for k, v in raw_fields.items() if k != "cross_source_comparison"},
        }
        canonical = reconciliation.reconcile([synthetic_obs], tolerances)
        v_status, v_notes = validation.validate(canonical, previous_close=None, tolerances=tolerances)
        canonical["validation_status"] = v_status
    else:
        from . import db
        try:
            raw_id = db.insert_raw_observation(
                conn,
                request_attempt_id=request_attempt_id, ingestion_job_id=None,
                company_id=company_id, observation_date=observation_date,
                capture_window=window, source="CSE_API", observed_at=observed_at,
                fields=raw_fields, raw_payload=raw_payload,
            )
            result.raw_observation_id = raw_id
            raw_obs = db.get_raw_observations_for_date(conn, company_id=company_id, observation_date=observation_date)
            canonical = reconciliation.reconcile(raw_obs, tolerances)
            previous_close = db.get_previous_close(conn, company_id=company_id, before_date=observation_date)
            v_status, v_notes = validation.validate(canonical, previous_close, tolerances)
            canonical["validation_status"] = v_status
            canonical["validation_notes"] = v_notes
            db.upsert_daily_market_data(conn, company_id=company_id, trade_date=observation_date, canonical=canonical)
        except Exception as exc:  # noqa: BLE001 — deliberately broad: ANY db-layer failure for
                                     # this one symbol must not propagate and kill the batch
            # CRITICAL: roll back before returning. Without this, a failed
            # command leaves the shared connection's transaction in an
            # aborted state, and Postgres then refuses every subsequent
            # command on that connection — silently failing every OTHER
            # symbol in the batch too. Found via real multi-company testing
            # against a live database, not assumed safe.
            try:
                conn.rollback()
            except Exception:
                pass  # connection itself may be unusable; the caller's next
                        # iteration will surface that clearly if so
            result.status = "database_failure"
            result.reason = f"{type(exc).__name__}: {exc}"
            return result

    result.status = "success"
    result.reconciliation_status = canonical.get("reconciliation_status")
    result.validation_status = canonical.get("validation_status")
    result.open_price = canonical.get("open_price")
    result.post_open_price = canonical.get("post_open_price")
    result.closing_price = canonical.get("closing_price")
    result.last_traded_price = canonical.get("last_traded_price")
    result.share_volume = canonical.get("share_volume")
    result.turnover = canonical.get("turnover")
    result.trade_count = canonical.get("trade_count")
    return result


def capture_multiple_companies(conn, symbols, window, request_attempt_id, observation_date,
                                tolerances=None, dry_run=False, verbose=True):
    """
    The core, testable, importable batch-capture function — no argparse, no
    sys.exit, so tests can call this directly with a real or mocked conn.
    Returns the machine-readable capture report as a dict.
    """
    tolerances = tolerances or DEFAULT_TOLERANCES
    started_at = datetime.now(timezone.utc)

    if verbose:
        print(f"Fetching tradeSummary ONCE for this batch of {len(symbols)} symbols...")
    trade_summary_response = cse_client.get_trade_summary_all()
    if verbose:
        print(f"  status={trade_summary_response.status_code} ok={trade_summary_response.ok}")

    results = []
    for symbol in symbols:
        t0 = time.monotonic()
        try:
            result = _process_symbol(
                conn, symbol, window, request_attempt_id, observation_date,
                trade_summary_response, tolerances, dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — a genuinely unexpected error (e.g. inside
                                     # mapping) must still be recorded, not crash the batch and
                                     # not be silently swallowed either
            result = SymbolResult(symbol=symbol, status="mapping_failure",
                                   reason=f"Unexpected {type(exc).__name__}: {exc}")
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        if verbose:
            detail = "" if result.status == "success" else f"  ({result.reason})"
            print(f"  [{result.status:16s}] {symbol:14s} {result.duration_ms:5d}ms{detail}")
        results.append(result)

    finished_at = datetime.now(timezone.utc)
    failed = [r for r in results if r.status != "success"]
    failure_categories = ("company_not_found", "http_failure", "mapping_failure", "database_failure")

    report = {
        "request_attempt_id": request_attempt_id,
        "capture_window": window,
        "observation_date": observation_date,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total_duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "requested_symbols": list(symbols),
        "successful_symbols": [r.symbol for r in results if r.status == "success"],
        "failed_symbols": [r.symbol for r in failed],
        "failures_by_category": {
            category: [r.symbol for r in failed if r.status == category]
            for category in failure_categories
            if any(r.status == category for r in failed)
        },
        "per_symbol": [asdict(r) for r in results],
    }
    return report


def resolve_symbol_universe(args) -> list:
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        print(f"Using explicit symbol list ({len(symbols)} symbols): {symbols}")
        return symbols

    if args.discover_count:
        print(f"Discovering CSE company universe via allSecurityCode (real API call, "
              f"not a hardcoded list)...")
        resp = cse_client.get_all_security_codes()
        print(f"  status={resp.status_code} ok={resp.ok} error={resp.error}")
        symbols = cse_client.extract_symbol_list_from_all_security_codes(resp)
        print(f"  Discovered {len(symbols)} symbols from the live response.")
        if not symbols:
            raise SystemExit(
                "Universe discovery returned zero symbols. Refusing to fall back to a "
                "hardcoded/invented list — check allSecurityCode's real response shape "
                "(it may differ from what extract_symbol_list_from_all_security_codes expects)."
            )
        selected = symbols[: args.discover_count]
        print(f"  Selected the first {len(selected)}: {selected}")
        return selected

    raise SystemExit("Must specify either --symbols or --discover-count.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None, help="Comma-separated explicit symbol list")
    parser.add_argument("--discover-count", type=int, default=None,
                         help="Discover N symbols from the live CSE universe via allSecurityCode")
    parser.add_argument("--window", required=True, choices=["post_open", "post_close"])
    parser.add_argument("--observation-date", default=None)
    parser.add_argument("--resume-attempt-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    symbols = resolve_symbol_universe(args)

    observation_date = args.observation_date or datetime.now(timezone.utc).date().isoformat()
    if args.resume_attempt_id:
        request_attempt_id = args.resume_attempt_id
        print(f"Resuming existing request_attempt_id: {request_attempt_id}")
    else:
        request_attempt_id = str(uuid.uuid4())
        print(f"New request_attempt_id generated: {request_attempt_id}")

    conn = None
    if not args.dry_run:
        from . import db
        conn = db.get_connection()

    report = capture_multiple_companies(
        conn, symbols, args.window, request_attempt_id, observation_date,
        dry_run=args.dry_run,
    )

    if conn:
        conn.close()

    report_file = args.report_file or f"capture_report_{observation_date}_{args.window}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{'=' * 70}")
    print(f"Capture complete: {len(report['successful_symbols'])}/{len(symbols)} succeeded")
    if report["failed_symbols"]:
        print(f"Failed: {report['failed_symbols']}")
        print(f"By category: {report['failures_by_category']}")
    print(f"Report written to {report_file}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
