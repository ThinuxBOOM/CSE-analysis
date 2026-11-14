"""
Run the strict ex-date reconstruction (ca_reconstruct.py; C-0055) over the TradeIQ archive.

    python3 -I scripts/research/tradeiq/run_ca_reconstruct.py <archive root> <work dir> <summary.json>

<work dir> must already hold daily.csv from run_validation.py. Writes <work dir>/reconstructed_actions.csv (evidence
candidates with their source rows; CSE data, never committed) and <summary.json> (counts only). Sends no request.
"""
import collections
import csv
import json
import os
import re
import sys
from datetime import date, timedelta
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ca_reconstruct as ca      # noqa: E402
import daily_parse as dp         # noqa: E402
import run_validation as rv      # noqa: E402


def closes_by_symbol(daily_csv):
    """Unique traded closes per (symbol, date); a (symbol, date) with disagreeing closes is left out."""
    seen = collections.defaultdict(set)
    for r in csv.DictReader(open(daily_csv, newline="")):
        if r["close"]:
            seen[(r["symbol"], r["date"])].add(Decimal(r["close"]))
    out = collections.defaultdict(list)
    for (sym, d), vals in seen.items():
        if len(vals) == 1:
            out[sym].append((date.fromisoformat(d), vals.pop()))
    for v in out.values():
        v.sort()
    return out


def names_by_symbol(root):
    names = collections.defaultdict(set)
    for d in rv.DAILY_DIRS[1:]:
        for n in sorted(os.listdir(os.path.join(root, d))):
            for sheet, rows in dp.read_sheets(os.path.join(root, d, n)):
                lay = dp.detect_layout(rows) if rows else ""
                cur = None
                for r in rows:
                    if lay == "L4" and len(r) > 4 and dp.text(r[1]) in ("N", "X"):
                        try:
                            names[dp.symbol(dp.text(r[0]), dp.text(r[1]), r[2])].add(dp.text(r[3]).upper())
                        except ValueError:
                            pass
                    elif lay == "L3":
                        j = "".join(dp.text(c) for c in r)
                        m = dp._HEADER_L3.search(j)
                        if m:
                            cur = dp.symbol(m[1], m[2], m[3])
                        elif cur and j.upper().startswith("SHORT NAME"):
                            names[cur].add(j[10:].strip(" :").upper())
    return names


def events(path, kind):
    """(sheet, row, company, ratio cell, allotment, announcement, subscription price) per readable data row."""
    out = []
    for sheet, rows in dp.read_sheets(path):
        h = next((i for i, r in enumerate(rows[:12]) if any(_is_ratio_header(dp.text(c)) for c in r)), None)
        if h is None:
            continue
        cols = [dp.text(c).upper() for c in rows[h]]
        ci = lambda *k: next((i for i, c in enumerate(cols) if any(x in c for x in k)), None)   # noqa: E731
        cc = ci("COMPANY", "NAME")
        cr = next((i for i, c in enumerate(cols) if _is_ratio_header(c) and "OLD" not in c and "NEW" not in c), None)
        c_old = next((i for i, c in enumerate(cols) if "OLD PROPORTION" in c), None)
        c_new = next((i for i, c in enumerate(cols) if "NEW PROPORTION" in c), None)
        cd = ci("ALLOTMENT")
        ca_ = ci("ANNOUNCE")
        cp = ci("ISSUE PRICE", "CONSIDERATION") if kind == "rights" else None
        for i, r in enumerate(rows[h + 1:], start=h + 1):
            r = list(r) + [None] * 14
            comp = dp.text(r[cc]) if cc is not None else ""
            if _summary(comp):
                break                                    # the sheet's summary tables follow its events
            if not comp or not comp[0].isalpha() or comp.upper() in ("VOTING", "COMPANY"):
                continue
            if cr is not None:
                ratio_cell = r[cr]
            elif c_old is not None and c_new is not None:
                ratio_cell = f"{dp.text(r[c_old])}:{dp.text(r[c_new])}"     # both readings are tried anyway
            else:
                ratio_cell = None
            price = None
            if cp is not None:
                try:
                    price = dp.number(r[cp])
                except ValueError:
                    price = None
            out.append((sheet.strip(), i, comp, ratio_cell,
                        _date(r[cd]) if cd is not None else None, _date(r[ca_]) if ca_ is not None else None, price))
    return out


def _is_ratio_header(text):
    """The ratio column: 'Ratio', 'Proportion' or CSE's spelling 'Propotion', as a word (not 'Consideration')."""
    return bool(re.search(r"\b(RATIO|PROPOTION|PROPORTION)\b", str(text).upper()))


def _summary(company):
    c = str(company).strip().upper()
    return c.startswith(("TOTAL", "ISSUES (NO", "VALUE (RS", "SECTOR", "DATA N.A")) or "BY SECTOR" in c


def _date(v):
    d = dp.to_date(v)
    if d:
        return d
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", dp.text(v))
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def main(root, work, out_json):
    hd = os.path.join(root, "historical_data")
    closes = closes_by_symbol(os.path.join(work, "daily.csv"))
    names = names_by_symbol(hd)
    counts = collections.Counter()
    by_year = collections.defaultdict(collections.Counter)
    rows_out = []
    for fname, kind in (("03Capitalization of Reserves (Bonus Issues).xls", "bonus"), ("04Rights Issues.xls", "rights")):
        path = os.path.join(hd, "stock_data", fname)
        sha = rv.sha256(path)
        for sheet, i, comp, rcell, allot, announce, price in events(path, kind):
            counts[f"{kind}_rows"] += 1
            ratio = ca.decode_ratio(rcell)
            sym, why = ca.match_symbol(comp, names)
            if ratio is None:
                why = "unreadable_ratio"
            elif sym is not None and allot is None:
                why = "no_allotment_date"
            m = None
            if why is None:
                m, why = ca.find_ex_date(kind, ratio, closes.get(sym, []), ca.window_for(kind, allot, announce), price)
            counts[f"{kind}_{why}"] += 1
            by_year[sheet][f"{kind}_{why}"] += 1
            if m is not None:
                rows_out.append([f"tiq:{kind}:{sha[:12]}:{sheet}:{i}", sym, kind, m.ex_date.isoformat(), m.reading,
                                 str(m.expected), str(m.observed), m.cum_session.isoformat(),
                                 (announce or m.ex_date).isoformat(), ca.METHOD, f"{fname} [{sheet}] row {i}", sha])
    with open(os.path.join(work, "reconstructed_actions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["statement_id", "symbol", "kind", "ex_date", "reading", "expected_factor", "observed_factor",
                    "cum_session", "available_on", "method", "source_row", "source_sha256"])
        w.writerows(rows_out)
    splits = []
    for sheet, rows in dp.read_sheets(os.path.join(hd, "stock_data", "05Sub Division (Share Splits).xls")):
        h = next((i for i, r in enumerate(rows) if any("OLD PROPORTION" in dp.text(c).upper() for c in r)), None)
        if h is None:
            continue
        cols = [dp.text(c).upper() for c in rows[h]]
        ci = lambda *k: next((i for i, c in enumerate(cols) if any(x in c for x in k)), None)   # noqa: E731
        cs = ci("SECURITY") if ci("SECURITY") is not None else ci("COMPANY ID")
        for r in rows[h + 1:]:
            r = list(r) + [None] * 10
            try:
                old, new = dp.number(r[ci("OLD PROPORTION")]), dp.number(r[ci("NEW PROPORTION")])
            except ValueError:
                continue
            s = dp.text(r[cs]).upper().replace(" ", "")
            mm = re.fullmatch(r"([A-Z0-9&]+)[.-]([A-Z])-?(\d{0,4})", s)
            sym = f"{mm[1]}.{mm[2]}{int(mm[3] or 0):04d}" if mm else (f"{s}.N0000" if re.fullmatch(r"[A-Z0-9&]+", s) else None)
            eff = _date(r[ci("EFFECTIVE")])
            if sym and eff and old and new:
                splits.append((sym, old, new, eff))
    summary = {"method": ca.METHOD, "tolerance": str(ca.TOLERANCE), "max_factor": str(ca.MAX_FACTOR),
               "windows_days": ca.WINDOWS, "counts": dict(sorted(counts.items())),
               "by_sheet": {k: dict(v) for k, v in sorted(by_year.items())},
               "validation_on_splits": ca.validate_on_splits(splits, closes), "splits": len(splits)}
    json.dump(summary, open(out_json, "w"), indent=1, sort_keys=True)
    return summary


if __name__ == "__main__":
    s = main(*sys.argv[1:4])
    print(json.dumps({"counts": s["counts"], "validation_on_splits": s["validation_on_splits"]}, indent=1))
