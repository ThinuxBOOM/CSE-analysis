"""
Stage E.5: universe-coverage validation — NOT production ingestion. Zero
database involvement whatsoever, same safety property as --dry-run.

Purpose: discover the CURRENT REAL CSE security universe from the live
allSecurityCode endpoint (never a hardcoded fallback list), run every
discovered security through the existing mapping/reconciliation/validation
path, and report exactly what happened for each one — with explicit,
mutually-distinguishable categories, not a single pass/fail flag.

This never writes to any database. It only calls cse.lk and writes a local
JSON report file.

Usage:
    python -m worker.validate_universe_coverage
    python -m worker.validate_universe_coverage --max-count 50   # cap for a
                                                                     partial run
    python -m worker.validate_universe_coverage --request-delay-seconds 0.3
    python -m worker.validate_universe_coverage --report-file universe_report.json
"""
import argparse
import json
import time
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from . import cse_client, mapping, reconciliation, validation

DEFAULT_TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}

# Every possible outcome, explicit and mutually exclusive — chosen by
# priority when more than one condition could technically apply, so every
# security gets exactly ONE category, never zero, never silently uncounted.
CATEGORY_PRIORITY = [
    "duplicate_symbol",
    "tradesummary_api_failure",
    "companyinfo_http_failure",
    "unexpected_response_schema",
    "missing_from_tradesummary",
    "mapping_failure",
    "successful",
]


@dataclass
class SecurityResult:
    symbol: str
    category: str
    reason: Optional[str] = None
    duration_ms: int = 0
    open_price: Optional[float] = None
    closing_price: Optional[float] = None
    last_traded_price: Optional[float] = None
    share_volume: Optional[int] = None
    turnover: Optional[float] = None
    trade_count: Optional[int] = None
    unexpected_fields_seen: Optional[dict] = None


def _process_one_security(symbol: str, trade_summary_response, tolerances) -> SecurityResult:
    result = SecurityResult(symbol=symbol, category="pending")

    ci_response = cse_client.get_company_info_summary(symbol)
    ts_row = cse_client.extract_symbol_row_from_trade_summary(trade_summary_response, symbol)

    if not ci_response.ok:
        result.category = "companyinfo_http_failure"
        result.reason = f"status={ci_response.status_code} error={ci_response.error}"
        return result

    try:
        ci_mapped = mapping.map_company_info_summary(ci_response.body)
    except Exception as exc:  # noqa: BLE001 — a hard crash during mapping is its own category,
                                 # distinct from a recognized-but-unexpected schema
        result.category = "mapping_failure"
        result.reason = f"Unexpected {type(exc).__name__} in map_company_info_summary: {exc}"
        return result

    if ci_mapped.notes.get("error") and not ci_mapped.notes.get("unwrapped_into_key"):
        # map_company_info_summary couldn't identify ANY data-bearing block —
        # a genuinely different response shape than anything seen before,
        # not just an HTTP-level failure.
        result.category = "unexpected_response_schema"
        result.reason = ci_mapped.notes.get("error")
        return result

    if ts_row is None:
        # The company responded fine, but simply isn't present in today's
        # tradeSummary batch — plausibly suspended/untraded/newly-listed.
        # This is a data-completeness fact, not necessarily an error.
        result.category = "missing_from_tradesummary"
        result.reason = "companyInfoSummery succeeded but no row found for this symbol in tradeSummary"
        return result

    try:
        ts_mapped = mapping.map_trade_summary_row(ts_row)
        if ts_mapped.notes.get("unexpected_fields"):
            result.unexpected_fields_seen = ts_mapped.notes["unexpected_fields"]

        raw_fields = mapping.build_raw_observation(
            company_info_result=ci_mapped, trade_summary_result=ts_mapped, capture_window="post_close",
        )
        synthetic_obs = {
            "id": "UNIVERSE-VALIDATION-NOT-A-REAL-ID", "capture_window": "post_close", "source": "CSE_API",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in raw_fields.items() if k != "cross_source_comparison"},
        }
        canonical = reconciliation.reconcile([synthetic_obs], tolerances)
        v_status, v_notes = validation.validate(canonical, previous_close=None, tolerances=tolerances)
    except Exception as exc:  # noqa: BLE001
        result.category = "mapping_failure"
        result.reason = f"Unexpected {type(exc).__name__} downstream of initial mapping: {exc}"
        return result

    result.category = "successful"
    result.open_price = canonical.get("open_price")
    result.closing_price = canonical.get("closing_price")
    result.last_traded_price = canonical.get("last_traded_price")
    result.share_volume = canonical.get("share_volume")
    result.turnover = canonical.get("turnover")
    result.trade_count = canonical.get("trade_count")
    return result


def validate_universe_coverage(symbols: list, request_delay_seconds: float = 0.2,
                                tolerances=None, verbose=True) -> dict:
    """
    Core, testable, importable function — no argparse, no sys.exit. Takes
    an already-discovered symbol list (discovery itself happens in main(),
    kept separate so this function is trivially testable with a synthetic
    list at any scale).
    """
    tolerances = tolerances or DEFAULT_TOLERANCES
    started_at = datetime.now(timezone.utc)

    # Duplicate detection BEFORE processing — record every duplicate, process
    # each symbol at most once (first occurrence), never silently drop or
    # silently double-process.
    counts = Counter(symbols)
    duplicates = {sym: count for sym, count in counts.items() if count > 1}
    seen = set()
    unique_symbols = []
    for sym in symbols:
        if sym not in seen:
            seen.add(sym)
            unique_symbols.append(sym)

    if verbose:
        print(f"Fetching tradeSummary ONCE for {len(unique_symbols)} unique securities "
              f"({len(symbols)} requested, {sum(duplicates.values()) - len(duplicates)} duplicate "
              f"occurrences found)...")
    trade_summary_response = cse_client.get_trade_summary_all()
    if verbose:
        print(f"  status={trade_summary_response.status_code} ok={trade_summary_response.ok}")

    tradesummary_batch_failed = not trade_summary_response.ok

    results = []
    for i, symbol in enumerate(unique_symbols):
        t0 = time.monotonic()
        if tradesummary_batch_failed:
            result = SecurityResult(
                symbol=symbol, category="tradesummary_api_failure",
                reason=f"Batch tradeSummary call failed: {trade_summary_response.error}",
            )
        else:
            try:
                result = _process_one_security(symbol, trade_summary_response, tolerances)
            except Exception as exc:  # noqa: BLE001 — absolute last-resort catch; must still
                                         # record something, never crash the whole run
                result = SecurityResult(symbol=symbol, category="mapping_failure",
                                         reason=f"Unhandled {type(exc).__name__}: {exc}")
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        results.append(result)
        if verbose and (i + 1) % 25 == 0:
            print(f"  ...{i + 1}/{len(unique_symbols)} processed")

        if request_delay_seconds > 0 and not tradesummary_batch_failed:
            time.sleep(request_delay_seconds)

    # Fold duplicates into the results list as their own category, AFTER
    # the unique pass — every requested symbol (including repeats) appears
    # somewhere in the final report.
    for sym, count in duplicates.items():
        for _ in range(count - 1):  # the first occurrence already has a real result above
            results.append(SecurityResult(symbol=sym, category="duplicate_symbol",
                                           reason=f"'{sym}' appeared {count} times in the discovered universe"))

    finished_at = datetime.now(timezone.utc)

    by_category = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r.symbol)

    all_unexpected_fields = {}
    for r in results:
        if r.unexpected_fields_seen:
            all_unexpected_fields[r.symbol] = r.unexpected_fields_seen

    report = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total_duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "requested_count": len(symbols),
        "unique_count": len(unique_symbols),
        "duplicate_symbols": duplicates,
        "tradesummary_fetched_once": True,
        "tradesummary_call_ok": trade_summary_response.ok,
        "counts_by_category": {cat: len(syms) for cat, syms in by_category.items()},
        "symbols_by_category": by_category,
        "unexpected_fields_by_symbol": all_unexpected_fields,
        "per_symbol": [asdict(r) for r in results],
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-count", type=int, default=None,
                         help="Cap the number of discovered securities processed (default: all)")
    parser.add_argument("--request-delay-seconds", type=float, default=0.2,
                         help="Politeness delay between companyInfoSummery calls")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    print("Discovering the REAL current CSE security universe via allSecurityCode "
          "(no hardcoded fallback)...")
    universe_response = cse_client.get_all_security_codes()
    print(f"  status={universe_response.status_code} ok={universe_response.ok} "
          f"error={universe_response.error}")
    symbols = cse_client.extract_symbol_list_from_all_security_codes(universe_response)
    print(f"  Discovered {len(symbols)} symbols.")

    if not symbols:
        raise SystemExit(
            "Universe discovery returned ZERO symbols. Refusing to proceed with a fabricated "
            "or hardcoded list. Raw response has been printed above (if any) — check whether "
            "allSecurityCode's real shape differs from what extract_symbol_list_from_all_security_codes "
            "expects, per this project's established discipline of not guessing."
        )

    target_symbols = symbols[: args.max_count] if args.max_count else symbols
    print(f"Processing {len(target_symbols)} of {len(symbols)} discovered securities "
          f"{'(capped by --max-count)' if args.max_count else '(the full discovered universe)'}.")

    report = validate_universe_coverage(
        target_symbols, request_delay_seconds=args.request_delay_seconds, verbose=True,
    )
    report["universe_endpoint_status_code"] = universe_response.status_code
    report["universe_endpoint_raw_count"] = len(symbols)

    report_file = args.report_file or f"universe_coverage_report_{datetime.now(timezone.utc).date().isoformat()}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{'=' * 70}")
    print(f"Universe coverage validation complete.")
    print(f"Discovered by CSE: {report['universe_endpoint_raw_count']}")
    print(f"Requested (after --max-count): {report['requested_count']}")
    print(f"Unique processed: {report['unique_count']}")
    print(f"By category: {json.dumps(report['counts_by_category'], indent=2)}")
    if report["unexpected_fields_by_symbol"]:
        print(f"Unexpected fields seen in {len(report['unexpected_fields_by_symbol'])} symbol(s) "
              f"— see report file for details.")
    print(f"Total duration: {report['total_duration_ms']}ms")
    print(f"Report written to {report_file}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
