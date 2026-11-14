"""
The TradeIQ import (Phase 3 MF-7b; design revision 6 §5.4 and §14; owner decisions C-0035 and C-0055).

An owner-run, file-based step: it reads the validated research outputs (never the network, never CSE) and writes, in
one transaction through the owner path (cse_migrator -> SET ROLE cse_owner):
  mf_history_imports            one batch per source workbook, with its declarations and provenance
  mf_history_bars               the parsed rows, verbatim; 1990s rows keep their classless code (F4)
  mf_identity_observations      a link from each 1990s code to CODE.N0000 where the code later has N alone (F4)
  mf_history_quarantine         (security, date) pairs set aside: a security a workbook prints as two series (F10, the
                                whole security for that batch), and rejected rows whose code and date are readable (F6,
                                F14)
  mf_history_security_complete  per batch and security, first to last row, from 2001 only: the 1991-2000 exports lost
                                rows against their own stated counts (F3), so an absent row there is unknown (§5.4)
  mf_calendar_observations      import_calendar_complete per year from 1992 whose dates equal the index workbook's
  mf_corporate_action_observations   the splits whose raw prices drop by the ratio (F1; the others are left out with
                                their reason) and the reconstructed bonus and rights issues (C-0055)
Rows dated before 1991 are not admitted (TRADEIQ_VALIDATION §4 rule 1).

`plan()` is pure apart from reading its input files; `write()` is the only database code.
"""
import bisect
import collections
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

COLOMBO = timezone(timedelta(hours=5, minutes=30))
ORIGIN = "TradeIQ-CSE/cse-dataset@7903c390c1b94e050e62440e32b6a5b2d0c399e7"
TERMS = "G-1 section 9 (extension E1); decision log C-0035, C-0055"
VALUES_AS_OF = date(2026, 10, 19)                     # the archive commit's date
FIRST_CALENDAR_YEAR = 1992                            # 1991 is partial (TRADEIQ_VALIDATION F2)
FIRST_DATE = "1991-01-01"                             # earlier rows are not admitted (§4 rule 1)
LAST_UNKNOWN_SURVIVORSHIP_YEAR = 2000                 # 1991-2000: survivorship unknown, exports short (F3)
MAPPING_BEFORE = "2003-01-01"                         # the F4 mapping reads 2001-2002 only
MAPPING_AVAILABLE_ON = date(2002, 12, 31)             # ... so it is known from the end of 2002
SPLIT_TOLERANCE = Decimal("0.25")                     # the F1 split test's tolerance


def end_of_day(d):
    return datetime.combine(d, time(23, 59, 59), COLOMBO)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass
class Batch:
    source_label: str
    source_sha256: str
    survivorship: str
    calendar: tuple = None                            # (date_from, date_to) or None
    bars: list = field(default_factory=list)          # dicts
    quarantine: dict = field(default_factory=dict)    # (symbol, date) -> reason
    complete: dict = field(default_factory=dict)      # symbol -> [first, last]
    calendar_years: list = field(default_factory=list)   # [(date_from, date_to)], consecutive years merged


@dataclass
class Plan:
    batches: dict = field(default_factory=dict)       # source label -> Batch
    actions: list = field(default_factory=list)       # dicts for mf_corporate_action_observations
    links: list = field(default_factory=list)         # dicts for mf_identity_observations
    left_out: list = field(default_factory=list)      # (statement id, reason) for splits not written
    counts: collections.Counter = field(default_factory=collections.Counter)


def _l1_mapping(rows):
    """1990s code -> CODE.N0000 where the code appears in 2001-2002 with ordinary voting shares only."""
    classes = collections.defaultdict(set)
    for r in rows:
        if r["layout"] in ("L2", "L3") and r["date"] < MAPPING_BEFORE and r["cls"]:
            classes[r["company"]].add(r["cls"])
    return {c: f"{c}.N0000" for c, k in classes.items() if {"N", "X"} & k == {"N"}}


def _batch_year(label):
    return int(label.split(" ")[0].split("-")[0])


def _ranges(years):
    out = []
    for y in years:
        if out and out[-1][1] == y - 1:
            out[-1][1] = y
        else:
            out.append([y, y])
    return [(date(lo, 1, 1), date(hi, 12, 31)) for lo, hi in out]


def split_confirmed(series, old, new, eff):
    """The F1 test: does the raw close drop by the ratio across the effective date? None if it does, else why not."""
    if not series:
        return "no_price_series_for_symbol"
    ds = [d for d, _ in series]
    k = bisect.bisect_left(ds, eff)
    if k == 0 or k == len(ds):
        return "no_price_on_both_sides"
    ratio = series[k][1] / series[k - 1][1]
    if abs(ratio / (Decimal(old) / Decimal(new)) - 1) < SPLIT_TOLERANCE:
        return None
    return "no_drop" if abs(ratio - 1) < SPLIT_TOLERANCE else "other_move"


def plan(work_dir, summary_json, splits, reconstructed_csv, index_sessions):
    """work_dir: run_validation's output (daily.csv, rejected.csv); summary_json: its counts summary with each file's
    SHA-256; splits: [(symbol, old, new, effective date, announcement date or None, statement id)];
    reconstructed_csv: run_ca_reconstruct's output; index_sessions: the index workbook's session dates."""
    p = Plan()
    shas = {f["file"]: f["sha256"] for f in json.load(open(summary_json))["files"]}
    rows = list(csv.DictReader(open(os.path.join(work_dir, "daily.csv"), newline="")))
    p.counts["rows_before_1991_not_admitted"] = sum(1 for r in rows if r["date"] < FIRST_DATE)
    rows = [r for r in rows if r["date"] >= FIRST_DATE]
    l1 = _l1_mapping(rows)
    sessions_by_year = collections.defaultdict(set)
    for d in index_sessions:
        sessions_by_year[d.year].add(d.isoformat())
    # F10: a security one workbook prints with two different closes on a day is two series under one name; which rows
    # belong to it is not known, so the whole security is set aside for that batch
    seen = collections.defaultdict(set)
    for r in rows:
        seen[(r["file"], r["symbol"], r["date"])].add(r["close"])
    two_series = {(f, sym) for (f, sym, _d), closes in seen.items() if len(closes) > 1}
    p.counts["securities_printed_as_two_series"] = len(two_series)
    for r in rows:
        f = r["file"]
        if f not in p.batches:
            p.batches[f] = Batch(f, shas[f], "unknown" if _batch_year(f) <= LAST_UNKNOWN_SURVIVORSHIP_YEAR
                                 else "complete")
        b = p.batches[f]
        sym = r["symbol"]                                  # verbatim: a 1990s code keeps its classless form (F4)
        if (f, sym) in two_series:
            b.quarantine[(sym, r["date"])] = "workbook_prints_two_series"
            continue
        flags = r["flags"]
        b.bars.append({"symbol": sym, "trade_date": r["date"], "close": r["close"] or None, "high": r["high"] or None,
                       "low": r["low"] or None, "open_price": r["open"] or None, "trades": r["trades"] or None,
                       "shares": r["shares"] or None, "turnover": r["turnover"] or None, "flags": flags,
                       "source_sheet": r["sheet"], "source_row": int(r["row"])})
        if _batch_year(f) > LAST_UNKNOWN_SURVIVORSHIP_YEAR:
            span = b.complete.setdefault(sym, [r["date"], r["date"]])
            span[0], span[1] = min(span[0], r["date"]), max(span[1], r["date"])
        p.counts["bars"] += 1
    p.counts["quarantined_two_series"] = sum(1 for b in p.batches.values() for v in b.quarantine.values()
                                             if v == "workbook_prints_two_series")
    for r in csv.DictReader(open(os.path.join(work_dir, "rejected.csv"), newline="")):
        if r["symbol"] and r["date"] and r["date"] >= FIRST_DATE and r["file"] in p.batches:
            p.batches[r["file"]].quarantine.setdefault((r["symbol"], r["date"]), r["reason"])
            p.counts["quarantined_rejected_row"] += 1
        else:
            p.counts["rejected_rows_without_code_or_date"] += 1
    for b in p.batches.values():                           # calendar completeness per year from 1992
        dates = collections.defaultdict(set)
        for x in b.bars:
            dates[int(x["trade_date"][:4])].add(x["trade_date"])
        years = [y for y in sorted(dates) if y >= FIRST_CALENDAR_YEAR and sessions_by_year.get(y)
                 and dates[y] == sessions_by_year[y]]
        b.calendar_years = _ranges(years)
        b.calendar = b.calendar_years[0] if len(b.calendar_years) == 1 else None
    first_n = {}
    for r in rows:                                         # F4 links: from the first CODE.N0000 row
        if r["cls"] == "N" and (r["symbol"] not in first_n or r["date"] < first_n[r["symbol"]]):
            first_n[r["symbol"]] = r["date"]
    linked = sorted({r["symbol"] for r in rows if not r["cls"] and r["symbol"] in l1})
    for code in linked:
        n = l1[code]
        p.links.append({"symbol": n, "predecessor": code, "effective_date": date.fromisoformat(first_n[n]),
                        "available_at": end_of_day(MAPPING_AVAILABLE_ON),
                        "source_reference": f"TradeIQ F4: {code} appears in 2001-2002 as {n} only"})
    p.counts["codes_without_link"] = len({r["symbol"] for r in rows if not r["cls"]}) - len(linked)
    series = collections.defaultdict(list)                 # stored closes, for the F1 split check
    for b in p.batches.values():
        for x in b.bars:
            if x["close"] is not None and Decimal(x["close"]) > 0:
                series[x["symbol"]].append((date.fromisoformat(x["trade_date"]), Decimal(x["close"])))
    for v in series.values():
        v.sort()
    for sym, old, new, eff, ann, sid in splits:
        if old != int(old) or new != int(new) or old == new:
            p.left_out.append((sid, "ratio_not_whole_shares"))
            p.counts["splits_left_out_ratio"] += 1
            continue
        why = split_confirmed(series.get(sym), int(old), int(new), eff)
        if why:
            p.left_out.append((sid, why))
            p.counts["splits_left_out_" + why] += 1
            continue
        kind = "subdivision" if new > old else "consolidation"
        p.actions.append({"statement_id": sid, "symbol": sym, "kind": kind, "ex_date": eff,
                          "terms_json": canonical({"old_shares": int(old), "new_shares": int(new),
                                                   "source": "tradeiq_split_workbook"}),
                          "available_at": end_of_day(ann or eff), "source_reference": f"TradeIQ split {sid}"})
    for r in csv.DictReader(open(reconstructed_csv, newline="")):
        terms = {"held": int(r["held"]), "new": int(r["new"]), "method": r["method"],
                 "expected_factor": r["expected_factor"], "observed_factor": r["observed_factor"],
                 "cum_session": r["cum_session"], "source_sha256": r["source_sha256"]}
        if r["kind"] == "rights":
            terms["subscription_price"] = r["subscription_price"]
        kind = "bonus" if r["kind"] == "bonus" else "rights"
        p.actions.append({"statement_id": r["statement_id"], "symbol": r["symbol"], "kind": kind,
                          "ex_date": date.fromisoformat(r["ex_date"]), "terms_json": canonical(terms),
                          "available_at": end_of_day(date.fromisoformat(r["available_on"])),
                          "source_reference": r["source_row"]})
    p.counts["actions"] = len(p.actions)
    p.counts["links"] = len(p.links)
    return p


def write(conn, p):
    """One transaction through the owner path. The caller connects as cse_migrator."""
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        cur.execute("set local role cse_owner")
        for label, b in sorted(p.batches.items()):
            cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                        "price_basis, survivorship, no_trade_convention, calendar_complete_from, calendar_complete_to, "
                        "values_as_of) values (%s,%s,%s,%s,'raw',%s,'row_absent',%s,%s,%s) returning import_id",
                        (label, b.source_sha256, ORIGIN, TERMS, b.survivorship,
                         b.calendar[0] if b.calendar else None, b.calendar[1] if b.calendar else None, VALUES_AS_OF))
            imp = cur.fetchone()[0]
            execute_values(cur, "insert into mf_history_bars (import_id, symbol, trade_date, close, high, low, "
                           "open_price, trades, shares, turnover, flags, source_sheet, source_row) values %s",
                           [(imp, x["symbol"], x["trade_date"], x["close"], x["high"], x["low"], x["open_price"],
                             x["trades"], x["shares"], x["turnover"], x["flags"], x["source_sheet"], x["source_row"])
                            for x in b.bars], page_size=5000)
            for (sym, d), why in sorted(b.quarantine.items()):
                cur.execute("insert into mf_history_quarantine (import_id, symbol, trade_date, reason) values "
                            "(%s,%s,%s,%s) on conflict do nothing", (imp, sym, d, why))
            for sym, (lo, hi) in sorted(b.complete.items()):
                cur.execute("insert into mf_history_security_complete (import_id, symbol, date_from, date_to) values "
                            "(%s,%s,%s,%s)", (imp, sym, lo, hi))
            for lo, hi in b.calendar_years:
                cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, "
                            "import_id) values ('import_calendar_complete',%s,%s,%s,%s)",
                            (lo, hi, f"TradeIQ {label}: {lo.year}-{hi.year} dates equal the index workbook's", imp))
        for k in p.links:
            cur.execute("insert into mf_identity_observations (kind, symbol, predecessor, effective_date, available_at, "
                        "source_reference) values ('link',%s,%s,%s,%s,%s)",
                        (k["symbol"], k["predecessor"], k["effective_date"], k["available_at"], k["source_reference"]))
        for a in p.actions:
            cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                        "available_at, source_reference) values (%s,%s,%s,%s,%s,%s,%s)",
                        (a["statement_id"], a["symbol"], a["kind"], a["ex_date"], a["terms_json"], a["available_at"],
                         a["source_reference"]))
    conn.commit()
