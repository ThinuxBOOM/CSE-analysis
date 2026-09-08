"""
Investigation tool for the tradeSummary "open" field discovery — NOT part of
the production pipeline. Makes real CSE calls, appends structured records to
a local JSON-lines log file, and touches NO database at all (same zero-DB
safety property as --dry-run).

Purpose: determine whether tradeSummary.open is the genuine session-opening/
first-traded price, or some other CSE reference value, by observing it
across multiple securities and multiple points in the trading day.

Usage — run this several times across one trading day (before open, shortly
after open, mid-session, near close, after close), and ideally across a few
days:

    python -m worker.investigate_open_price

Each run appends one record per symbol to cse_open_investigation_log.jsonl
in the current directory. Nothing is ever overwritten — this file is itself
an append-only log, matching the project's broader philosophy.

After collecting several runs across a day, run:

    python -m worker.analyze_open_investigation

to see a per-symbol timeline and a cross-endpoint agreement summary.
"""
import json
from datetime import datetime, timezone

from . import cse_client, mapping

# A handful of liquid, well-known securities across different sectors —
# chosen for likely high trade frequency (more chances to observe "open"
# behavior against real intraday movement), not because we're confident in
# any particular field mapping for them individually.
DEFAULT_SYMBOLS = ["COMB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000"]

LOG_FILE = "cse_open_investigation_log.jsonl"

# The exact fields requested for this investigation, pulled from wherever
# they actually live in the two real responses (per the fixes already made).
FIELDS_OF_INTEREST = [
    "tradeSummary_open",       # tradeSummary's "open" — the field under investigation
    "last_traded_price",       # companyInfoSummery lastTradedPrice / tradeSummary price (cross-checked)
    "closing_price",           # closingPrice, present in both endpoints
    "previous_close",          # previousClose, present in both endpoints
    "high", "low",              # tradeSummary high/low
    "tdy_share_volume",           # tradeSummary "sharevolume" AND companyInfoSummery "tdyShareVolume"
    "tdy_turnover_companyinfo",     # companyInfoSummery tdyTurnover
    "turnover_tradesummary",          # tradeSummary turnover
    "status",                          # tradeSummary "status" — unconfirmed meaning, logged for pattern-spotting
]


def capture_one_symbol(symbol: str) -> dict:
    ci_response = cse_client.get_company_info_summary(symbol)
    ts_response = cse_client.get_trade_summary_all()
    ts_row = cse_client.extract_symbol_row_from_trade_summary(ts_response, symbol)

    ci_mapped = mapping.map_company_info_summary(ci_response.body)

    record = {
        "symbol": symbol,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "companyInfoSummery_ok": ci_response.ok,
        "tradeSummary_ok": ts_response.ok,
        "tradeSummary_open": (ts_row or {}).get("open"),
        "last_traded_price": ci_mapped.fields.get("last_traded_price"),
        "closing_price": ci_mapped.fields.get("closing_price"),
        "previous_close": (ts_row or {}).get("previousClose"),
        "high": (ts_row or {}).get("high"),
        "low": (ts_row or {}).get("low"),
        "tdy_share_volume": ci_mapped.fields.get("turnover") and None,  # placeholder, see below
        "tdy_turnover_companyinfo": ci_mapped.fields.get("turnover"),
        "turnover_tradesummary": (ts_row or {}).get("turnover"),
        "status": (ts_row or {}).get("status"),
        # Full verbatim bodies too, in case something else turns out to matter later
        "_raw_companyInfoSummery": ci_response.body,
        "_raw_tradeSummary_row": ts_row,
    }
    # tdy_share_volume: prefer tradeSummary's confirmed 'sharevolume', fall back
    # to reqSymbolInfo's tdyShareVolume if the unwrap succeeded and it's there.
    if ts_row and "sharevolume" in ts_row:
        record["tdy_share_volume"] = ts_row["sharevolume"]
    elif isinstance(ci_response.body, dict):
        nested = ci_response.body.get(ci_mapped.notes.get("unwrapped_into_key") or "", {})
        record["tdy_share_volume"] = nested.get("tdyShareVolume") if isinstance(nested, dict) else None

    return record


def main():
    print(f"Investigating tradeSummary.open across {len(DEFAULT_SYMBOLS)} symbols...")
    print(f"Appending to {LOG_FILE} — this file is never overwritten, only appended to.\n")

    records = []
    for symbol in DEFAULT_SYMBOLS:
        print(f"  Capturing {symbol}...")
        record = capture_one_symbol(symbol)
        records.append(record)
        print(f"    open={record['tradeSummary_open']}  last={record['last_traded_price']}  "
              f"closing={record['closing_price']}  previousClose={record['previous_close']}  "
              f"status={record['status']}")

    with open(LOG_FILE, "a") as f:
        for record in records:
            f.write(json.dumps(record, default=str) + "\n")

    print(f"\nAppended {len(records)} records to {LOG_FILE}. "
          f"Run this again at different points in the trading day, then run "
          f"analyze_open_investigation.py to see the pattern.")


if __name__ == "__main__":
    main()
