"""
Analyzes cse_open_burst_log.jsonl (from capture_market_open_burst.py).

Strict rules for this script:
1. Never call any observed value "the first trade" — only ever "the first
   OBSERVED price" (polling at discrete intervals cannot establish that no
   trade happened between polls). This applies to every print statement,
   not just the summary.
2. Every line of output is either OBSERVED (a fact read directly from the
   data) or INFERENCE (a conclusion drawn from those facts), and never both
   blended into one unlabeled sentence.
3. Distinct lastTradedTime values are reported as exactly that — "N distinct
   timestamps observed" — never translated into "N trades occurred". Multiple
   trades could share a poll interval and be invisible to us; a single
   lastTradedTime change tells us at least one trade happened, not how many.
4. The final report is structured around exactly the seven questions (A–G)
   requested, answered explicitly and separately.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from .capture_market_open_burst import LOG_FILE


def load_records():
    if not os.path.exists(LOG_FILE):
        print(f"No log file found at {LOG_FILE} — run capture_market_open_burst.py first.")
        return []
    records = []
    with open(LOG_FILE) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def readable_time(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def detect_changes(obs_sorted: list) -> list:
    """
    Returns a list of change events between consecutive captures for one
    symbol: price changes, sharevolume increases, tradevolume increases,
    lastTradedTime changes, open changes. Purely observational — no
    trade-count inference.
    """
    events = []
    for prev, cur in zip(obs_sorted, obs_sorted[1:]):
        changes = {}
        if prev["price"] != cur["price"]:
            changes["price_changed"] = {"from": prev["price"], "to": cur["price"]}
        if (prev["sharevolume"] is not None and cur["sharevolume"] is not None
                and cur["sharevolume"] > prev["sharevolume"]):
            changes["sharevolume_increased"] = {"from": prev["sharevolume"], "to": cur["sharevolume"]}
        if (prev["tradevolume"] is not None and cur["tradevolume"] is not None
                and cur["tradevolume"] > prev["tradevolume"]):
            changes["tradevolume_increased"] = {"from": prev["tradevolume"], "to": cur["tradevolume"]}
        if prev["last_traded_time_epoch_ms"] != cur["last_traded_time_epoch_ms"]:
            changes["last_traded_time_changed"] = {
                "from": readable_time(prev["last_traded_time_epoch_ms"]),
                "to": readable_time(cur["last_traded_time_epoch_ms"]),
            }
        if prev["open"] != cur["open"]:
            changes["open_changed"] = {"from": prev["open"], "to": cur["open"]}
        if changes:
            events.append({"at": cur["captured_at"], "changes": changes})
    return events


def main():
    records = load_records()
    if not records:
        return

    by_symbol = defaultdict(list)
    for r in records:
        by_symbol[r["symbol"]].append(r)

    print(f"OBSERVED: {len(records)} total captures across {len(by_symbol)} symbols.\n")

    for symbol, obs in sorted(by_symbol.items()):
        obs.sort(key=lambda r: r["captured_at"])
        print(f"{'=' * 90}\n{symbol} — {len(obs)} captures\n{'=' * 90}")

        change_events = detect_changes(obs)
        print(f"\nOBSERVED: {len(change_events)} capture-to-capture change event(s) detected "
              f"(price/sharevolume/tradevolume/lastTradedTime/open):")
        for ev in change_events:
            print(f"  at {ev['at']}: {json.dumps(ev['changes'], default=str)}")

        first_null = next((r for r in obs if r["price"] is None), None)
        first_nonnull = next((r for r in obs if r["price"] is not None), None)

        print(f"\n--- Answering the seven questions for {symbol} ---")

        # A. Was open absent before trading?
        pre_trade_opens = {r["open"] for r in obs if r["price"] is None}
        print(f"A. OBSERVED: 'open' during pre-trade captures (price still null): {pre_trade_opens or 'n/a — no pre-trade captures in this burst'}")

        # B. When was the first non-null price observed?
        if first_nonnull:
            print(f"B. OBSERVED: first non-null 'price' observed at {first_nonnull['captured_at']}, "
                  f"price={first_nonnull['price']} (this is NOT necessarily the first trade of the "
                  f"session — only the first one caught by our poll interval).")
        else:
            print(f"B. OBSERVED: no non-null price appeared during this burst window.")
            print()
            continue

        # C. Did open equal that first OBSERVED price?
        if first_nonnull["open"] == first_nonnull["price"]:
            print(f"C. OBSERVED: 'open' ({first_nonnull['open']}) EQUALED the first OBSERVED price "
                  f"({first_nonnull['price']}) at that capture.")
        else:
            print(f"C. OBSERVED: 'open' ({first_nonnull['open']}) DIFFERED from the first OBSERVED "
                  f"price ({first_nonnull['price']}) at that capture.")

        # D. Did price/volume/trade timestamps subsequently change?
        subsequent = [r for r in obs if r["captured_at"] > first_nonnull["captured_at"]]
        price_changed = any(r["price"] != first_nonnull["price"] for r in subsequent)
        distinct_ltt_after = {r["last_traded_time_epoch_ms"] for r in subsequent
                               if r["last_traded_time_epoch_ms"] is not None}
        distinct_ltt_after |= {first_nonnull["last_traded_time_epoch_ms"]} if first_nonnull["last_traded_time_epoch_ms"] else set()
        print(f"D. OBSERVED: after the first non-null price, price subsequently changed: {price_changed}. "
              f"{len(distinct_ltt_after)} distinct lastTradedTime value(s) observed across the whole burst "
              f"for this symbol (this indicates at least one trade occurred per additional distinct "
              f"timestamp — NOT a count of how many trades occurred).")

        # E. Did open remain fixed while those values changed?
        distinct_opens = {r["open"] for r in obs if r["open"] is not None}
        print(f"E. OBSERVED: {len(distinct_opens)} distinct 'open' value(s) seen across the entire burst "
              f"for this symbol: {distinct_opens}. "
              f"{'Open remained fixed while price/lastTradedTime changed.' if len(distinct_opens) <= 1 and price_changed else ''}"
              f"{'Open itself changed during the burst.' if len(distinct_opens) > 1 else ''}")

        # F. Can the data establish open equals the actual first trade?
        print(f"F. INFERENCE: this data can establish that 'open' equaled the first price OBSERVED "
              f"by our poll interval{' and remained fixed as subsequent activity was observed' if len(distinct_opens) <= 1 else ''}. "
              f"It CANNOT establish that this was literally the session's first trade — a trade could "
              f"have occurred between market open and our first successful poll, or between any two "
              f"polls, that we never captured. The data supports 'open' behaving as a fixed "
              f"session-opening reference; it does not prove 'open' equals the literal first executed trade.")

        # G. Can the data establish an opening auction/call-auction mechanism?
        print(f"G. INFERENCE: this data CANNOT establish or rule out any specific CSE mechanism "
              f"(call auction, locked opening print, or otherwise). We only observe the external "
              f"behavior (a value that appears once trading begins and then does not change with "
              f"subsequent price movement) — the underlying mechanism producing that behavior is not "
              f"visible in this data and should not be assumed.")
        print()


if __name__ == "__main__":
    main()
