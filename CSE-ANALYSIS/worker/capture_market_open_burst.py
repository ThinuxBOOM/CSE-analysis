"""
Targeted investigation: captures tradeSummary repeatedly, in a tight loop,
around the market-open moment — to determine whether `open` equals the
earliest OBSERVED price, or whether it behaves as some other CSE mechanism
that could diverge from it.

IMPORTANT TERMINOLOGY: this script and its companion analysis script never
refer to "the first trade". Polling at discrete intervals can only ever
establish "the first non-null price observed at one of our poll times" —
there may have been trades between polls that we never saw. Every place
that matters is worded as "first OBSERVED price", not "first trade".

Design notes (per methodological review):
- tradeSummary is fetched ONCE per round; all tracked symbols are extracted
  from that single response. This avoids redundant per-symbol requests and
  avoids temporal skew between symbols within the same round (the old
  version called tradeSummary once per symbol, meaning symbol 5's read was
  measurably later than symbol 1's within the "same" round).
- companyInfoSummery is deliberately NOT called in this burst at all: every
  field this specific investigation needs (price, open, high, low,
  lastTradedTime, sharevolume, tradevolume, previousClose, status) already
  exists in the single tradeSummary response. Calling a second endpoint per
  symbol here would be unnecessary request volume with no investigative
  benefit for this specific question.
- Zero database involvement — same safety property as --dry-run.

Usage:
    python -m worker.capture_market_open_burst --duration-seconds 300 --interval-seconds 5

Start this ~1 minute before market open (09:29 IST) and let it run through
the opening minutes. Output appends to cse_open_burst_log.jsonl (never
overwritten).
"""
import argparse
import json
import time
from datetime import datetime, timezone

from . import cse_client

SYMBOLS = ["COMB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000"]
LOG_FILE = "cse_open_burst_log.jsonl"


def capture_one_round() -> list:
    """One tradeSummary call, all symbols extracted from it — see module docstring."""
    captured_at = datetime.now(timezone.utc).isoformat()
    ts_response = cse_client.get_trade_summary_all()

    records = []
    for symbol in SYMBOLS:
        row = cse_client.extract_symbol_row_from_trade_summary(ts_response, symbol)
        records.append({
            "symbol": symbol,
            "captured_at": captured_at,
            "tradeSummary_ok": ts_response.ok,
            "open": (row or {}).get("open"),
            # "price": the raw tradeSummary field, exactly as CSE names it.
            # This is the OBSERVED price at this poll time — never referred
            # to as "the first trade" anywhere downstream.
            "price": (row or {}).get("price"),
            "previous_close": (row or {}).get("previousClose"),
            "high": (row or {}).get("high"),
            "low": (row or {}).get("low"),
            "last_traded_time_epoch_ms": (row or {}).get("lastTradedTime"),
            "sharevolume": (row or {}).get("sharevolume"),
            "tradevolume": (row or {}).get("tradevolume"),
            "status": (row or {}).get("status"),
            "_raw_tradeSummary_row": row,
        })
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=int, default=5)
    args = parser.parse_args()

    print(f"Starting burst capture: ONE tradeSummary call per round (all {len(SYMBOLS)} symbols "
          f"extracted from that single response), every {args.interval_seconds}s for "
          f"{args.duration_seconds}s.")
    print(f"companyInfoSummery is NOT called in this burst — not needed for this question.")
    print(f"Appending to {LOG_FILE} (never overwritten).\n")
    print("Start this ~1 minute before market open and let it run through the opening minutes.\n")

    start = time.monotonic()
    round_num = 0
    with open(LOG_FILE, "a") as f:
        while time.monotonic() - start < args.duration_seconds:
            round_num += 1
            round_start = time.monotonic()
            records = capture_one_round()
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
            f.flush()

            print(f"--- Round {round_num} at {records[0]['captured_at']} ---")
            for record in records:
                lt_time = record["last_traded_time_epoch_ms"]
                lt_readable = (datetime.fromtimestamp(lt_time / 1000, tz=timezone.utc).isoformat()
                               if lt_time else None)
                print(f"  {record['symbol']}: open={record['open']}  price={record['price']}  "
                      f"lastTradedTime={lt_readable}")

            elapsed_this_round = time.monotonic() - round_start
            time.sleep(max(0.0, args.interval_seconds - elapsed_this_round))

    print(f"\nBurst capture complete — {round_num} rounds captured to {LOG_FILE} "
          f"({round_num} tradeSummary calls total, not {round_num * len(SYMBOLS)}).")


if __name__ == "__main__":
    main()
