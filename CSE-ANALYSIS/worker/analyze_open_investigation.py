"""
Analyzes cse_open_investigation_log.jsonl (produced by investigate_open_price.py)
to help determine whether tradeSummary.open behaves like a genuine session-
opening/first-trade price or something else.

Usage:
    python -m worker.analyze_open_investigation

This script only reads the local log file — no network, no database.
"""
import json
import os
from collections import defaultdict

from .investigate_open_price import LOG_FILE


def load_records():
    if not os.path.exists(LOG_FILE):
        print(f"No log file found at {LOG_FILE} — run investigate_open_price.py first, "
              f"ideally several times across a trading day.")
        return []
    records = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main():
    records = load_records()
    if not records:
        return

    by_symbol = defaultdict(list)
    for r in records:
        by_symbol[r["symbol"]].append(r)

    print(f"Loaded {len(records)} observations across {len(by_symbol)} symbols.\n")

    for symbol, obs_list in by_symbol.items():
        obs_list.sort(key=lambda r: r["captured_at"])
        print(f"=== {symbol} — {len(obs_list)} observations ===")
        print(f"{'captured_at':<30} {'open':>10} {'last':>10} {'closing':>10} "
              f"{'prevClose':>10} {'high':>10} {'low':>10} {'status':>7}")
        for r in obs_list:
            print(f"{r['captured_at']:<30} {str(r['tradeSummary_open']):>10} "
                  f"{str(r['last_traded_price']):>10} {str(r['closing_price']):>10} "
                  f"{str(r['previous_close']):>10} {str(r['high']):>10} {str(r['low']):>10} "
                  f"{str(r['status']):>7}")

        opens = [r["tradeSummary_open"] for r in obs_list if r["tradeSummary_open"] is not None]
        distinct_opens = set(opens)
        print(f"\n  Distinct 'open' values observed: {distinct_opens}")
        if len(distinct_opens) == 1:
            print(f"  -> 'open' was CONSTANT across all {len(obs_list)} observations for this symbol. "
                  f"Consistent with a fixed session-opening reference price (or a static value that "
                  f"simply hasn't been re-observed across a wide enough time range yet).")
        elif len(distinct_opens) > 1:
            print(f"  -> 'open' CHANGED across observations. This would be surprising for a genuine "
                  f"session-open price (which should be set once and hold) — worth checking whether "
                  f"the observations span more than one trading day, or whether 'open' tracks "
                  f"something else entirely (e.g. a rolling/current-ish value).")

        # Compare first observed 'last' of the day against 'open', as a rough
        # proxy for "does open match the first trade" — imperfect, since we
        # don't have true first-trade timestamps, but informative directionally.
        first_last = obs_list[0]["last_traded_price"]
        if opens and first_last is not None:
            first_open = opens[0]
            if first_open == first_last:
                print(f"  -> First-captured 'last' ({first_last}) MATCHES 'open' ({first_open}) — "
                      f"consistent with, but not proof of, open being the first trade of the day.")
            else:
                print(f"  -> First-captured 'last' ({first_last}) DIFFERS from 'open' ({first_open}) — "
                      f"either your first capture wasn't early enough to catch the literal first trade, "
                      f"or 'open' isn't simply 'whatever the first observed price was'.")
        print()

    # Cross-endpoint agreement summary across ALL observations
    print("=== Cross-endpoint agreement summary (all observations, all symbols) ===")
    turnover_diffs = []
    for r in records:
        a, b = r.get("tdy_turnover_companyinfo"), r.get("turnover_tradesummary")
        if a is not None and b is not None:
            turnover_diffs.append(abs(a - b))
    if turnover_diffs:
        print(f"  turnover (companyInfoSummery.tdyTurnover vs tradeSummary.turnover): "
              f"{len(turnover_diffs)} comparable observations, "
              f"exact matches: {sum(1 for d in turnover_diffs if d == 0)}, "
              f"max difference: {max(turnover_diffs):.2f}, "
              f"mean difference: {sum(turnover_diffs) / len(turnover_diffs):.2f}")
    else:
        print("  No comparable turnover observations yet.")


if __name__ == "__main__":
    main()
