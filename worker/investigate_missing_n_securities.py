"""
One-off investigation — NOT part of the production pipeline. Zero database
involvement (same safety property as --dry-run and validate_universe_coverage).
Read-only: it only calls cse.lk and writes a local JSON report file.

Question under investigation: the 2026-09-21 universe-coverage run
(validate_universe_coverage.py) found 19 `.N` securities present in
allSecurityCode and answering companyInfoSummery, but with no row in
tradeSummary. This script re-checks exactly those 19 tickers against fresh
responses from the SAME three existing endpoints and preserves what each one
says, verbatim.

What this script deliberately does NOT do:
- It does not interpret any value. tradeSummary's "status" field (observed
  value 0 in a real sample) has no established meaning; it is recorded, never
  decoded. Nothing here asserts suspended / inactive / delisted / newly-listed.
- It does not turn the 2026-09-21 ticker list (or any ticker suffix) into a
  filter. The list below is the historical observation being re-checked, not
  an eligibility rule.
- It does not modify mapping, reconciliation, or ingestion.

Presence is tri-state per endpoint: True / False / None. None means "could not
be determined" (the endpoint call failed or its body was unusable) — it is
never collapsed into False, so an unusable response can't masquerade as
evidence of absence.

Status-like fields are found by FIELD NAME only (case-insensitive substring
match on STATUS_LIKE_NAME_SUBSTRINGS), at any nesting depth, and recorded with
their raw values. "trad" is deliberately NOT a substring: against a real
payload it matched lastTradedPrice, tdyTradeVolume, hiTrade, etc.

Also recorded (zero extra requests — both come from the single shared
tradeSummary / allSecurityCode responses already fetched):
- today's full allSecurityCode-vs-tradeSummary set difference, so the 19 can
  be seen in the context of today's whole missing set;
- a verbatim tally of tradeSummary "sharevolume" values that are 0 / null /
  absent, which bears on whether tradeSummary carries non-traded securities.
  Counts only — no conclusion is drawn here.

Usage:
    python -m worker.investigate_missing_n_securities
    python -m worker.investigate_missing_n_securities --request-delay-seconds 0.3
    python -m worker.investigate_missing_n_securities --report-file some_other_name.json

The default report file name refers to the 2026-09-21 ticker set being
investigated (not the run date — the run date is inside the report), and must
match the upload path in
.github/workflows/missing-n-securities-investigation-2026-09-24.yml.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import cse_client, mapping

# Exactly the 19 `.N` tickers classified missing_from_tradesummary in the
# real 2026-09-21 universe-coverage report. Historical observation, NOT a
# production filter.
HISTORICAL_MISSING_N_TICKERS = [
    "AMCL.N0000", "ALHP.N0000", "ACAP.N0000", "BRR.N0000", "BLI.N0000",
    "BLUE.N0000", "BBH.N0000", "CARG.N0000", "CARS.N0000", "CHOU.N0000",
    "CFI.N0000", "SOY.N0000", "ELPL.N0000", "HELA.N0000", "KDL.N0000",
    "SHAW.N0000", "CSF.N0000", "SLND.N0000", "LHL.N0000",
]
HISTORICAL_SOURCE_DATE = "2026-09-21"

STATUS_LIKE_NAME_SUBSTRINGS = ("status", "active", "list", "suspend", "delist", "board", "flag")

# reqSymbolInfo fields copied into a short per-ticker summary for readability.
# Verbatim values only; the full companyInfoSummery body is preserved too.
COMPANY_INFO_ACTIVITY_FIELDS = [
    "symbol", "name", "lastTradedPrice", "closingPrice", "previousClose",
    "tdyShareVolume", "tdyTradeVolume", "tdyTurnover", "issueDate", "instrumentsDate",
]

DEFAULT_REPORT_FILE = f"missing_n_securities_investigation_{HISTORICAL_SOURCE_DATE}.json"


def _top_level_rows(body: Any) -> list:
    """Same list-locating rule as cse_client's extract_* helpers (a bare list,
    or the first list value of a top-level dict), so row counts here describe
    exactly the rows those helpers search."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list):
                return v
    return []


def _security_code_entry_symbol(entry: Any) -> Optional[str]:
    """Mirrors extract_symbol_list_from_all_security_codes' key choice."""
    if isinstance(entry, dict):
        return entry.get("symbol") or entry.get("Symbol") or entry.get("securityCode")
    if isinstance(entry, str):
        return entry
    return None


def find_status_like_fields(obj: Any, path: str = "") -> dict:
    """Returns {dotted.path: raw value} for every dict key, at any depth, whose
    name contains a STATUS_LIKE_NAME_SUBSTRINGS substring. Values are never
    interpreted or normalised."""
    found = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_path = f"{path}.{key}" if path else str(key)
            if any(s in str(key).lower() for s in STATUS_LIKE_NAME_SUBSTRINGS):
                found[key_path] = value
            found.update(find_status_like_fields(value, key_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.update(find_status_like_fields(item, f"{path}[{i}]"))
    return found


def _batch_health(response, rows: list) -> dict:
    """A batch endpoint is 'usable' only if the call succeeded, its body parsed
    as JSON, AND at least one row was located. Anything else means presence of
    individual tickers cannot be determined from it."""
    if not response.ok:
        reason = f"call failed: status={response.status_code} error={response.error}"
    elif response.body is None:
        reason = f"body was not parseable JSON: status={response.status_code} error={response.error}"
    elif not rows:
        reason = "body parsed but no row list could be located"
    else:
        reason = None
    return {
        "usable": reason is None,
        "unusable_reason": reason,
        "status_code": response.status_code,
        "ok": response.ok,
        "error": response.error,
        "elapsed_ms": response.elapsed_ms,
        "row_count": len(rows),
        "raw_text_if_not_json": response.raw_text,
    }


def _sharevolume_tally(ts_rows: list) -> dict:
    tally = {"rows": len(ts_rows), "sharevolume_zero": 0, "sharevolume_null": 0,
             "sharevolume_key_absent": 0, "sharevolume_other": 0, "symbols_with_zero_or_null": []}
    for row in ts_rows:
        if not isinstance(row, dict):
            continue
        if "sharevolume" not in row:
            tally["sharevolume_key_absent"] += 1
            continue
        v = row["sharevolume"]
        if v is None:
            tally["sharevolume_null"] += 1
            tally["symbols_with_zero_or_null"].append(row.get("symbol"))
        elif v == 0:
            tally["sharevolume_zero"] += 1
            tally["symbols_with_zero_or_null"].append(row.get("symbol"))
        else:
            tally["sharevolume_other"] += 1
    return tally


def _investigate_one(ticker, universe_rows, universe_usable, ts_rows, ts_usable) -> dict:
    record = {"ticker": ticker}

    # --- allSecurityCode (shared response) ---
    entries = [e for e in universe_rows if _security_code_entry_symbol(e) == ticker]
    record["present_in_allSecurityCode"] = (len(entries) > 0) if universe_usable else None
    record["allSecurityCode_match_count"] = len(entries) if universe_usable else None
    record["allSecurityCode_entries_raw"] = entries
    record["allSecurityCode_status_like_fields"] = find_status_like_fields(entries)

    # --- tradeSummary (shared response, fetched once for all tickers) ---
    ts_matches = [r for r in ts_rows if isinstance(r, dict) and r.get("symbol") == ticker]
    record["present_in_tradeSummary"] = (len(ts_matches) > 0) if ts_usable else None
    record["tradeSummary_match_count"] = len(ts_matches) if ts_usable else None
    record["tradeSummary_rows_raw"] = ts_matches
    record["tradeSummary_status_like_fields"] = find_status_like_fields(ts_matches)

    # --- companyInfoSummery (per-ticker call) ---
    ci = cse_client.get_company_info_summary(ticker)
    record["companyInfoSummery_http"] = {
        "status_code": ci.status_code, "ok": ci.ok, "error": ci.error, "elapsed_ms": ci.elapsed_ms,
    }
    record["companyInfoSummery_body_raw"] = ci.body
    record["companyInfoSummery_raw_text_if_not_json"] = ci.raw_text

    if not ci.ok or ci.body is None:
        record["present_in_companyInfoSummery"] = None
        record["companyInfoSummery_data_block_key"] = None
        record["companyInfoSummery_activity_fields_verbatim"] = None
    else:
        ci_mapped = mapping.map_company_info_summary(ci.body)
        block_key = ci_mapped.notes.get("unwrapped_into_key")
        block = ci.body.get(block_key) if (block_key and isinstance(ci.body, dict)) else None
        record["present_in_companyInfoSummery"] = isinstance(block, dict)
        record["companyInfoSummery_data_block_key"] = block_key
        record["companyInfoSummery_unwrap_scores"] = ci_mapped.notes.get("unwrap_scores")
        record["companyInfoSummery_activity_fields_verbatim"] = (
            {k: block[k] for k in COMPANY_INFO_ACTIVITY_FIELDS if k in block} if isinstance(block, dict) else None
        )
    record["companyInfoSummery_status_like_fields"] = find_status_like_fields(ci.body)
    return record


def investigate(tickers=None, request_delay_seconds: float = 0.2, verbose: bool = True) -> dict:
    """Core, importable, testable function — no argparse, no file I/O."""
    tickers = list(tickers or HISTORICAL_MISSING_N_TICKERS)
    started_at = datetime.now(timezone.utc)

    if verbose:
        print("Fetching allSecurityCode (fresh)...")
    universe_response = cse_client.get_all_security_codes()
    universe_rows = _top_level_rows(universe_response.body)
    universe_health = _batch_health(universe_response, universe_rows)
    if verbose:
        print(f"  status={universe_response.status_code} ok={universe_response.ok} rows={len(universe_rows)}")

    if verbose:
        print(f"Fetching tradeSummary ONCE for all {len(tickers)} tickers...")
    ts_response = cse_client.get_trade_summary_all()
    ts_rows = _top_level_rows(ts_response.body)
    ts_health = _batch_health(ts_response, ts_rows)
    if verbose:
        print(f"  status={ts_response.status_code} ok={ts_response.ok} rows={len(ts_rows)}")

    for name, health in (("allSecurityCode", universe_health), ("tradeSummary", ts_health)):
        if not health["usable"] and verbose:
            print(f"  *** {name} UNUSABLE ({health['unusable_reason']}) — presence in it is "
                  f"recorded as null (unknown), NOT as absent.")

    results = []
    for i, ticker in enumerate(tickers):
        if i > 0 and request_delay_seconds > 0:
            time.sleep(request_delay_seconds)
        try:
            record = _investigate_one(ticker, universe_rows, universe_health["usable"],
                                      ts_rows, ts_health["usable"])
        except Exception as exc:  # noqa: BLE001 — one ticker's surprise must not lose the others'
            record = {"ticker": ticker, "investigation_error": f"{type(exc).__name__}: {exc}"}
        results.append(record)
        if verbose:
            print(f"  {ticker:12s} allSecurityCode={record.get('present_in_allSecurityCode')} "
                  f"tradeSummary={record.get('present_in_tradeSummary')} "
                  f"companyInfoSummery={record.get('present_in_companyInfoSummery')} "
                  f"status-like(ts)={record.get('tradeSummary_status_like_fields')}")

    universe_symbols = {s for s in (_security_code_entry_symbol(e) for e in universe_rows) if s}
    ts_symbols = {r.get("symbol") for r in ts_rows if isinstance(r, dict) and r.get("symbol")}
    both_usable = universe_health["usable"] and ts_health["usable"]
    in_universe_not_ts = sorted(universe_symbols - ts_symbols) if both_usable else None
    in_ts_not_universe = sorted(ts_symbols - universe_symbols) if both_usable else None

    def _tickers_where(key, value):
        return [r["ticker"] for r in results if r.get(key) is value]

    finished_at = datetime.now(timezone.utc)
    return {
        "purpose": ("Re-check the .N securities missing from tradeSummary on "
                    f"{HISTORICAL_SOURCE_DATE}. Raw evidence only; no value is interpreted."),
        "historical_source_date": HISTORICAL_SOURCE_DATE,
        "run_date_utc": started_at.date().isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total_duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "tickers_investigated": tickers,
        "status_like_name_substrings": list(STATUS_LIKE_NAME_SUBSTRINGS),
        "endpoint_health": {"allSecurityCode": universe_health, "tradeSummary": ts_health},
        "tradesummary_fetched_once": True,
        "summary": {
            "present_in_tradeSummary_today": _tickers_where("present_in_tradeSummary", True),
            "absent_from_tradeSummary_today": _tickers_where("present_in_tradeSummary", False),
            "tradeSummary_presence_unknown": _tickers_where("present_in_tradeSummary", None),
            "absent_from_allSecurityCode_today": _tickers_where("present_in_allSecurityCode", False),
            "companyInfoSummery_presence_unknown": _tickers_where("present_in_companyInfoSummery", None),
            "companyInfoSummery_no_data_block": _tickers_where("present_in_companyInfoSummery", False),
            "investigation_errors": [r["ticker"] for r in results if "investigation_error" in r],
        },
        "todays_universe_comparison": {
            "allSecurityCode_symbol_count": len(universe_symbols),
            "tradeSummary_symbol_count": len(ts_symbols),
            "in_allSecurityCode_not_in_tradeSummary": in_universe_not_ts,
            "in_tradeSummary_not_in_allSecurityCode": in_ts_not_universe,
            "note": None if both_usable else "Not computed: an endpoint was unusable (see endpoint_health).",
        },
        "tradeSummary_sharevolume_tally": _sharevolume_tally(ts_rows) if ts_health["usable"] else None,
        "per_ticker": results,
        "raw_allSecurityCode_body": universe_response.body,
        "raw_tradeSummary_body": ts_response.body,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-delay-seconds", type=float, default=0.2,
                        help="Politeness delay between companyInfoSummery calls")
    parser.add_argument("--report-file", default=DEFAULT_REPORT_FILE)
    args = parser.parse_args()

    report = investigate(request_delay_seconds=args.request_delay_seconds, verbose=True)

    with open(args.report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    s = report["summary"]
    print(f"\n{'=' * 70}")
    print(f"Missing .N securities investigation complete ({len(report['tickers_investigated'])} tickers).")
    for name, health in report["endpoint_health"].items():
        print(f"  {name}: usable={health['usable']} rows={health['row_count']}"
              + (f"  ({health['unusable_reason']})" if not health["usable"] else ""))
    print(f"Present in tradeSummary today: {s['present_in_tradeSummary_today']}")
    print(f"Absent from tradeSummary today: {s['absent_from_tradeSummary_today']}")
    print(f"tradeSummary presence unknown: {s['tradeSummary_presence_unknown']}")
    print(f"Absent from allSecurityCode today: {s['absent_from_allSecurityCode_today']}")
    print(f"companyInfoSummery unknown / no data block: "
          f"{s['companyInfoSummery_presence_unknown']} / {s['companyInfoSummery_no_data_block']}")
    if s["investigation_errors"]:
        print(f"*** Per-ticker investigation errors: {s['investigation_errors']}")
    print(f"Report written to {args.report_file}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
