"""
Build the TradeIQ import plan (worker/market_feature_store/tradeiq_import.py) from a local archive checkout and the
validation outputs, and print counts only. Offline: it reads files, never the network, and writes nothing to a database.

    python3 run_import_plan.py <archive root> <work dir> <validation summary json> <reconstructed actions csv>
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
import daily_parse as dp          # noqa: E402
import validate_daily as vd       # noqa: E402
from run_validation import read_splits  # noqa: E402
from worker.market_feature_store import tradeiq_import as ti  # noqa: E402


def build(root, work, summary_json, reconstructed_csv):
    hd = os.path.join(root, "historical_data", "stock_data")
    sessions = vd.calendar_from_index(next(iter(dp.read_sheets(os.path.join(hd, "07Market Indices - Daily.xls"))))[1],
                                      dp.to_date)
    splits = read_splits(os.path.join(hd, "05Sub Division (Share Splits).xls"))
    return ti.plan(work, summary_json, splits, reconstructed_csv, sessions)


def counts(p):
    return {"counts": dict(sorted(p.counts.items())), "batches": len(p.batches),
            "calendar_complete_years": sorted({y for b in p.batches.values() for lo, hi in b.calendar_years
                                               for y in range(lo.year, hi.year + 1)}),
            "batches_with_security_complete": sum(1 for b in p.batches.values() if b.complete),
            "splits_left_out": dict(collections.Counter(why for _sid, why in p.left_out))}


if __name__ == "__main__":
    print(json.dumps(counts(build(*sys.argv[1:5])), indent=1, default=str))
