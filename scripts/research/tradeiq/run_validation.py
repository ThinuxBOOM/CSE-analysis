"""
Reproducible validation of the TradeIQ daily share prices (research; owner decision C-0035, G-1 extension E1).

    python3 -I scripts/research/tradeiq/run_validation.py <archive root> <work dir> <summary.json>

<archive root>  a checkout of https://github.com/TradeIQ-CSE/cse-dataset (its historical_data/ directory)
<work dir>      OUTSIDE the repository: receives the normalised rows (CSE data; never committed)
<summary.json>  counts only, with file hashes: safe to commit (no prices, symbols-with-dates or other data values)

Needs xlrd 2.0.1 and openpyxl 3.1.5 (research-only). Sends no request anywhere.
"""
import bisect
import collections
import csv
import hashlib
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import daily_parse as dp          # noqa: E402
import validate_daily as vd       # noqa: E402

DAILY_DIRS = ("30Daily Shares Price List - 1991-2000/32Daily Shares Price List - 1991-2000",
              "31Daily Shares Price List -2001-2010", "stock_data/32Daily Shares Price List -2011-2020",
              "stock_data/33Daily Shares Price List -2021-2025")
FIELDS = ["symbol", "company", "cls", "date", "close", "high", "low", "open", "trades", "shares", "turnover",
          "layout", "file", "sheet", "row", "block", "flags"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_all(root, out_csv):
    files, footers = [], {}
    rej_path = os.path.join(os.path.dirname(out_csv), "rejected.csv")
    with open(out_csv, "w", newline="") as fo, open(rej_path, "w", newline="") as fr:
        w = csv.writer(fo)
        w.writerow(FIELDS)
        wr = csv.writer(fr)
        wr.writerow(["file", "sheet", "row", "reason", "symbol", "date"])
        for d in DAILY_DIRS:
            for name in sorted(os.listdir(os.path.join(root, d))):
                path = os.path.join(root, d, name)
                entry = {"file": name, "sha256": sha256(path), "bytes": os.path.getsize(path), "sheets": []}
                for sheet, rows in dp.read_sheets(path):
                    lay = dp.detect_layout(rows) if rows else "empty"
                    recs, bad = dp.parse_sheet(rows, lay) if rows else ([], [])
                    for r in recs:
                        w.writerow([r.symbol, r.company_code, r.security_class or "", r.trade_date.isoformat(),
                                    r.close, r.high, r.low, r.open, r.trades, r.shares, r.turnover, r.layout, name,
                                    sheet, r.source_row, r.source_block, "|".join(r.flags)])
                    for b in bad:                # the quarantine of the import: symbol and date where readable
                        sym = day = ""
                        if lay == "L4" and len(b.cells) > 4:
                            try:
                                sym = dp.symbol(b.cells[0], b.cells[1] or None, b.cells[2]) if b.cells[1] else ""
                            except ValueError:
                                sym = ""
                            d0 = dp.to_date(b.cells[4])
                            day = d0.isoformat() if d0 else ""
                        elif lay == "L1" and len(b.cells) > 1:     # e.g. [34740, 'HAYL', '194..75'] (F14)
                            try:
                                d0 = dp.to_date(b.cells[0])
                            except (ValueError, OverflowError):
                                d0 = None
                            code = b.cells[1].strip().upper()
                            if d0 and re.fullmatch(r"[A-Z0-9&]+", code):
                                sym, day = code, d0.isoformat()
                        wr.writerow([name, sheet, b.source_row, b.reason, sym, day])
                    stated = None
                    for b in bad:          # SQL*Plus footer of the 1990s exports: "<n> rows selected."
                        m = re.match(r"^(\d+)\s*rows\s*selec", " ".join(b.cells))
                        if m:
                            stated = int(m[1])
                    reasons = collections.Counter(b.reason for b in bad
                                                  if not re.match(r"^(\d+\s*rows|SQL>)", " ".join(b.cells)))
                    entry["sheets"].append({"sheet": sheet, "rows": len(rows), "layout": lay, "records": len(recs),
                                            "rejected": dict(reasons), "stated_rows": stated,
                                            "flags": dict(collections.Counter(f for r in recs for f in r.flags)),
                                            "first": min((r.trade_date for r in recs), default=None),
                                            "last": max((r.trade_date for r in recs), default=None)})
                files.append(entry)
    return files


def _dmy(v):
    d = dp.to_date(v)
    if d:
        return d
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", dp.text(v))
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def _norm(s):
    s = dp.text(s).upper().replace(" ", "")
    m = re.fullmatch(r"([A-Z0-9&]+)[.-]([A-Z])-?(\d{0,4})", s)
    if m:
        return f"{m[1]}.{m[2]}{int(m[3] or 0):04d}"
    return f"{s}.N0000" if re.fullmatch(r"[A-Z0-9&]+", s) else None


def read_splits(splits_xls):
    """[(symbol, old, new, effective date, announcement date or None, statement id)] from the split workbook."""
    splits = []
    for sheet, rows in dp.read_sheets(splits_xls):
        h = next((i for i, r in enumerate(rows) if any("OLD PROPORTION" in dp.text(c).upper() for c in r)), None)
        if h is None:
            continue
        cols = [dp.text(c).upper() for c in rows[h]]
        col = lambda *n: next((i for i, c in enumerate(cols) if any(x in c for x in n)), None)  # noqa: E731
        cs = col("SECURITY") if col("SECURITY") is not None else col("COMPANY ID")
        co, cn, ce, ca = col("OLD PROPORTION"), col("NEW PROPORTION"), col("EFFECTIVE"), col("ANNOUNCE")
        for i, r in enumerate(rows[h + 1:], start=h + 1):
            r = list(r) + [None] * 10
            try:
                old, new = dp.number(r[co]), dp.number(r[cn])
            except ValueError:
                continue
            sym, eff = _norm(r[cs]), _dmy(r[ce])
            if sym and eff and old and new:
                splits.append((sym, old, new, eff, dp.to_date(r[ca]) if ca is not None else None,
                               f"tiq:split:{sheet}:{i}"))
    return splits


def split_test(splits_xls, recs):
    """Does the close drop by the split ratio across each listed split's effective date? (raw versus adjusted)"""
    splits = [(sym, old, new, eff, ann is not None) for sym, old, new, eff, ann, _sid in read_splits(splits_xls)]
    series = collections.defaultdict(list)
    for r in recs:
        if r["close"] is not None:
            series[r["symbol"]].append((r["d"], r["close"]))
    for v in series.values():
        v.sort()
    out = collections.Counter()
    for sym, old, new, eff, has_announcement in splits:
        out["with_announcement_date" if has_announcement else "without_announcement_date"] += 1
        s = series.get(sym)
        if not s:
            out["no_price_series_for_symbol"] += 1
            continue
        ds = [d for d, _ in s]
        k = bisect.bisect_left(ds, eff)
        if k == 0 or k == len(ds):
            out["no_price_on_both_sides"] += 1
            continue
        ratio = s[k][1] / s[k - 1][1]
        f = old / new
        if abs(ratio / f - 1) < Decimal("0.25"):
            out["drop_matches_ratio_raw"] += 1
        elif abs(ratio - 1) < Decimal("0.25"):
            out["no_drop"] += 1
        else:
            out["other_move"] += 1
    out["splits"] = len(splits)
    return dict(out)


def duplicates(recs):
    g = collections.defaultdict(list)
    for r in recs:
        g[(r["symbol"], r["d"])].append(r)
    res = collections.Counter()
    for v in g.values():
        if len(v) < 2:
            continue
        same_source = len({(x["file"], x["sheet"]) for x in v}) == 1
        agree = len({x["close"] for x in v}) == 1
        res[("within_one_sheet_" if same_source else "across_files_") + ("agree" if agree else "disagree")] += 1
    return dict(res)


def main(root, work, out_json):
    hd = os.path.join(root, "historical_data")
    os.makedirs(work, exist_ok=True)
    out_csv = os.path.join(work, "daily.csv")
    files = parse_all(hd, out_csv)
    recs = vd.load(out_csv)
    idx = os.path.join(hd, "stock_data", "07Market Indices - Daily.xls")
    sessions = vd.calendar_from_index(next(iter(dp.read_sheets(idx)))[1], dp.to_date)
    res = vd.checks(recs, sessions)
    for k in ("invariant_examples",):
        res.pop(k)
    res["duplicates"] = duplicates(recs)
    res["dates_not_index_sessions"].pop("examples", None)
    res["split_test"] = split_test(os.path.join(hd, "stock_data", "05Sub Division (Share Splits).xls"), recs)
    res["records"] = len(recs)
    res["index_calendar"] = {"sessions": len(sessions), "first": min(sessions), "last": max(sessions),
                             "sha256": sha256(idx)}
    res["files"] = files
    json.dump(res, open(out_json, "w"), indent=1, default=str, sort_keys=True)
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
