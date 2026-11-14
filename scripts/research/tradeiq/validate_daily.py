"""
Content checks on the parsed TradeIQ daily share prices (research; C-0035). Input: the CSV written by
`run_validation.py` from `daily_parse.py`, and the daily index workbook (an independent trading calendar).
Output: a JSON summary of counts. It only reports; it repairs and drops nothing.
"""
import collections
import csv
import json
import sys
from datetime import date
from decimal import Decimal

D0 = Decimal(0)


def dec(s):
    return Decimal(s) if s not in ("", None) else None


def load(path):
    out = []
    for r in csv.DictReader(open(path, newline="")):
        r["d"] = date.fromisoformat(r["date"])
        for k in ("close", "high", "low", "open", "trades", "shares", "turnover"):
            r[k] = dec(r[k])
        out.append(r)
    return out


def file_year(name):
    """The nominal year span of a source file from its name: '1991-1993 data.xls' -> (1991, 1993)."""
    head = name.split(" ")[0]
    a, _, b = head.partition("-")
    return int(a), int(b or a)


def calendar_from_index(rows, read_date):
    """Dates on which the All Share Price Index has a value: an independent list of trading sessions."""
    out = set()
    for r in rows:
        if len(r) > 1 and read_date(r[0]) and str(r[1]).strip() not in ("", "None"):
            out.add(read_date(r[0]))
    return out


def checks(recs, sessions):
    res = {}
    by_year = collections.defaultdict(list)
    for r in recs:
        by_year[r["d"].year].append(r)
    res["per_year"] = {y: {"records": len(v), "symbols": len({r["symbol"] for r in v}),
                           "dates": len({r["d"] for r in v}),
                           "index_sessions": sum(1 for s in sessions if s.year == y)}
                       for y, v in sorted(by_year.items())}
    # dates outside the file's nominal years; weekends; dates that are not index sessions
    outside = collections.Counter()
    for r in recs:
        a, b = file_year(r["file"])
        if not a <= r["d"].year <= b:
            outside[(r["file"], r["sheet"])] += 1
    res["outside_file_years"] = {f"{f} [{s}]": n for (f, s), n in outside.items()}
    res["weekend_records"] = sum(1 for r in recs if r["d"].weekday() >= 5)
    first_index = min(sessions)
    not_session = collections.Counter(r["d"] for r in recs if r["d"] >= first_index and r["d"] not in sessions)
    res["dates_not_index_sessions"] = {"dates": len(not_session), "records": sum(not_session.values()),
                                       "examples": sorted((d.isoformat(), n) for d, n in not_session.items())[:15]}
    traded_dates = {r["d"] for r in recs}
    span = [s for s in sessions if date(1991, 1, 1) <= s <= max(traded_dates)]
    missing = [s for s in span if s not in traded_dates]
    res["index_sessions_without_any_price"] = {"count": len(missing), "examples": [d.isoformat() for d in missing[:20]],
                                               "by_year": dict(collections.Counter(d.year for d in missing))}
    # duplicates: one security, one date, more than one record
    groups = collections.defaultdict(list)
    for r in recs:
        groups[(r["symbol"], r["d"])].append(r)
    dup = {k: v for k, v in groups.items() if len(v) > 1}
    disagree = [k for k, v in dup.items() if len({x["close"] for x in v}) > 1]
    res["duplicates"] = {"keys": len(dup), "close_disagrees": len(disagree),
                         "by_files": dict(collections.Counter(" + ".join(sorted({f'{x["file"]}[{x["sheet"]}]' for x in v}))
                                                              for v in dup.values()).most_common(10)),
                         "disagree_examples": [(s, d.isoformat(), [str(x["close"]) for x in groups[(s, d)]])
                                               for s, d in disagree[:10]]}
    # value invariants
    inv = collections.Counter()
    ex = collections.defaultdict(list)

    def flag(name, r):
        inv[name] += 1
        if len(ex[name]) < 5:
            ex[name].append((r["symbol"], r["date"], str(r["close"]), str(r["high"]), str(r["low"]), str(r["open"]),
                             str(r["shares"]), str(r["turnover"]), r["file"]))
    for r in recs:
        c, h, l, o = r["close"], r["high"], r["low"], r["open"]
        if c is None:
            inv["no_price_printed"] += 1
            continue
        if c <= 0:
            flag("close_not_positive", r)
        if h is not None and l is not None:
            if h < l:
                flag("high_below_low", r)
            elif not l <= c <= h:
                flag("close_outside_high_low", r)
            if o is not None and o > 0 and not l <= o <= h:
                flag("open_outside_high_low", r)
        if r["shares"] is not None and r["shares"] == 0:
            flag("zero_shares_with_a_price", r)
        if r["shares"] and r["turnover"] is not None and h is not None and l is not None and l > 0:
            vwap = r["turnover"] / r["shares"]
            if not (l * Decimal("0.95") <= vwap <= h * Decimal("1.05")):
                flag("turnover_per_share_outside_range", r)
    res["invariants"] = dict(inv)
    res["invariant_examples"] = dict(ex)
    # day-to-day close moves per security (raw prices: a split shows as a move here)
    moves = collections.Counter()
    by_sym = collections.defaultdict(list)
    for r in recs:
        if r["close"] is not None and r["close"] > 0:
            by_sym[r["symbol"]].append((r["d"], r["close"]))
    for s, v in by_sym.items():
        v.sort()
        for (d0, p0), (d1, p1) in zip(v, v[1:]):
            if d0 == d1:
                continue
            ch = abs(p1 / p0 - 1)
            for t in ("0.25", "0.5", "1"):
                if ch > Decimal(t):
                    moves[t] += 1
    res["close_to_close_moves_over"] = dict(moves)
    # universe
    classes = collections.Counter(r["cls"] or "unknown" for r in recs)
    res["records_by_class"] = dict(classes)
    first_last = {s: (min(d for d, _ in v), max(d for d, _ in v)) for s, v in by_sym.items()}
    last = max(r["d"] for r in recs)
    res["securities"] = {"total": len(by_sym),
                         "last_seen_before_2025": sum(1 for a, b in first_last.values() if b.year < 2025),
                         "first_seen_after_1991": sum(1 for a, b in first_last.values() if a.year > 1991)}
    return res


def main(parsed_csv, index_xls, out_json):
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import daily_parse as dp
    recs = load(parsed_csv)
    rows = next(iter(dp.read_sheets(index_xls)))[1]
    sessions = calendar_from_index(rows, dp.to_date)
    res = checks(recs, sessions)
    res["index_calendar"] = {"sessions": len(sessions), "first": min(sessions).isoformat(), "last": max(sessions).isoformat()}
    res["records"] = len(recs)
    json.dump(res, open(out_json, "w"), indent=1, default=str)
    return res


if __name__ == "__main__":
    r = main(*sys.argv[1:4])
    print(json.dumps({k: v for k, v in r.items() if k not in ("per_year", "invariant_examples")}, indent=1, default=str))
