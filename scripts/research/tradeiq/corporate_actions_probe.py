"""
Probe of the TradeIQ bonus-issue and rights-issue workbooks (research; C-0035). Counts only: how many rows have a
readable ratio, a company name that matches one ordinary-voting symbol in the daily prices, an allotment date, and a
one-day price fall of 10% or more within 40 days before to 20 days after that date. It loads nothing.

    python3 -I scripts/research/tradeiq/corporate_actions_probe.py <archive>/historical_data <work dir>/daily.csv
"""
import sys, re, csv, collections, bisect, os
from decimal import Decimal as D
from fractions import Fraction as F
from datetime import date, timedelta
sys.path.insert(0, "/home/user/CSE-analysis/scripts/research/tradeiq")
import daily_parse as dp
root = sys.argv[1]; daily = sys.argv[2]
# short names per symbol from the L4 era files and L3 "Short Name" lines
names = collections.defaultdict(set)
def toks(s): return [t for t in re.sub(r"[^A-Z0-9 ]", " ", s.upper()).split() if t not in ("PLC", "LTD", "LIMITED", "THE", "CO", "COMPANY", "OF", "AND", "SRI", "LANKA", "CEYLON")]
for d in ("stock_data/32Daily Shares Price List -2011-2020", "stock_data/33Daily Shares Price List -2021-2025", "31Daily Shares Price List -2001-2010"):
    for n in sorted(os.listdir(os.path.join(root, d))):
        for sheet, rows in dp.read_sheets(os.path.join(root, d, n)):
            lay = dp.detect_layout(rows) if rows else ""
            cur = None
            for r in rows:
                if lay == "L4" and len(r) > 4 and dp.text(r[1]) in ("N", "X"):
                    names[dp.symbol(dp.text(r[0]), dp.text(r[1]), r[2])].add(dp.text(r[3]).upper())
                elif lay == "L3":
                    j = "".join(dp.text(c) for c in r); m = dp._HEADER_L3.search(j)
                    if m: cur = dp.symbol(m[1], m[2], m[3])
                    elif cur and j.upper().startswith("SHORT NAME") and cur[-5:-4] in ("N", "X"): names[cur].add(j[10:].strip(" :").upper())
def match(company):
    t = toks(company)
    if not t: return None
    best = []
    for s, ns in names.items():
        if not s.endswith(".N0000"): continue
        for n in ns:
            nt = toks(n)
            if nt and nt[0] == t[0] and (len(t) < 2 or len(nt) < 2 or nt[1][:3] == t[1][:3]):
                best.append(s); break
    return best[0] if len(set(best)) == 1 else ("ambiguous" if best else None)
def ratio(v):
    """(new, held) from '1 for 2', '2:1', '01 for 01', or an Excel time fraction (h:mm typed as a ratio)."""
    if isinstance(v, float) and 0 < v < 1:
        mins = round(v * 1440)
        if abs(v * 1440 - mins) < 1e-6: return ("time", mins // 60, mins % 60)
        return None
    s = dp.text(v).lower()
    m = re.fullmatch(r"(\d+)\s*(?:for|:)\s*(\d+)", s)
    return ("text", int(m[1]), int(m[2])) if m else None
series = collections.defaultdict(list)
for r in csv.DictReader(open(daily)):
    if r["close"] and r["symbol"].endswith(".N0000"): series[r["symbol"]].append((date.fromisoformat(r["date"]), D(r["close"])))
for v in series.values(): v.sort()
def nearest_drop(sym, when, lo):
    """Largest one-day fall in (when-40d, when+20d); returns (days from 'when', fall)."""
    s = series.get(sym) or []
    ds = [d for d, _ in s]; a = bisect.bisect_left(ds, when - timedelta(days=40)); b = bisect.bisect_right(ds, when + timedelta(days=20))
    best = None
    for k in range(max(a, 1), b):
        f = s[k][1] / s[k - 1][1]
        if best is None or f < best[1]: best = ((s[k][0] - when).days, f)
    return best
out = collections.Counter(); offs = collections.Counter()
for fname, kind in (("03Capitalization of Reserves (Bonus Issues).xls", "bonus"), ("04Rights Issues.xls", "rights")):
    for sheet, rows in dp.read_sheets(os.path.join(root, "stock_data", fname)):
        h = next((i for i, r in enumerate(rows[:12]) if any(k in dp.text(c).upper() for c in r for k in ("RATIO", "PROPOTION", "PROPORTION"))), None)
        if h is None: continue
        cols = [dp.text(c).upper() for c in rows[h]]
        ci = lambda *k: next((i for i, c in enumerate(cols) if any(x in c for x in k)), None)
        cc, cr, cd = ci("COMPANY", "NAME"), ci("RATIO", "PROPOTION", "PROPORTION"), ci("ALLOTMENT", "P/ALLOTMENT")
        for r in rows[h + 1:]:
            r = list(r) + [None] * 14
            comp = dp.text(r[cc]) if cc is not None else ""
            if not comp or comp.upper() in ("VOTING", "") or not comp[0].isalpha(): continue
            out[f"{kind}_rows"] += 1
            rt = ratio(r[cr]) if cr is not None else None
            out[f"{kind}_ratio_{rt[0] if rt else 'unreadable'}"] += 1
            sym = match(comp)
            out[f"{kind}_name_{'matched' if sym and sym != 'ambiguous' else sym or 'unmatched'}"] += 1
            d = dp.to_date(r[cd]) if cd is not None else None
            if not d: out[f"{kind}_no_date"] += 1; continue
            if sym and sym != "ambiguous" and int(sheet.strip()[:4] or 0) >= 2002:
                nd = nearest_drop(sym, d, None)
                if nd and nd[1] < D("0.9"):
                    out[f"{kind}_drop_found"] += 1; offs[(kind, (nd[0] // 7) * 7)] += 1
                else: out[f"{kind}_no_clear_drop"] += 1
print(dict(sorted(out.items())))
print(sorted(offs.items()))
