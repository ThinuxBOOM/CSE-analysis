"""
The TradeIQ import (Phase 3 MF-7b; design revision 6 §5.4 and §14; owner decisions C-0035 and C-0055).

An owner-run, file-based step: it reads the validated research outputs (never the network, never CSE) and writes, in
one transaction through the owner path (cse_migrator -> SET ROLE cse_owner):
  mf_history_imports            one batch per source workbook, with its declarations and provenance
  mf_history_bars               the parsed rows, verbatim; 1990s codes without a class only where they map to one
                                ordinary voting line (F4), others left out and counted
  mf_history_quarantine         (security, date) pairs set aside: rows a workbook printed inconsistently (F6, F10)
  mf_history_security_complete  per batch and security, first to last row: no-trade bars for absent rows (§5.4)
  mf_calendar_observations      import_calendar_complete per batch from 1992, whose dates match the index workbook
  mf_corporate_action_observations   the splits (TradeIQ's own dates) and the reconstructed bonus and rights issues

`plan()` is pure apart from reading its input files; `write()` is the only database code.
"""
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


@dataclass
class Plan:
    batches: dict = field(default_factory=dict)       # source label -> Batch
    actions: list = field(default_factory=list)       # dicts for mf_corporate_action_observations
    counts: collections.Counter = field(default_factory=collections.Counter)


def _l1_mapping(rows):
    """1990s code -> CODE.N0000 where the code appears in 2001-2002 with ordinary voting shares only."""
    classes = collections.defaultdict(set)
    for r in rows:
        if r["layout"] in ("L2", "L3") and r["date"] < "2003-01-01" and r["cls"]:
            classes[r["company"]].add(r["cls"])
    return {c: f"{c}.N0000" for c, k in classes.items() if {"N", "X"} & k == {"N"}}


def plan(work_dir, summary_json, splits, reconstructed_csv, index_sessions):
    """work_dir: run_validation's output (daily.csv, rejected.csv); summary_json: its counts summary with each file's
    SHA-256; splits: [(symbol, old, new, effective date, announcement date or None, statement id)];
    reconstructed_csv: run_ca_reconstruct's output; index_sessions: the index workbook's session dates."""
    p = Plan()
    shas = {f["file"]: f["sha256"] for f in json.load(open(summary_json))["files"]}
    rows = list(csv.DictReader(open(os.path.join(work_dir, "daily.csv"), newline="")))
    l1 = _l1_mapping(rows)
    sessions_by_year = collections.defaultdict(set)
    for d in index_sessions:
        sessions_by_year[d.year].add(d)
    # quarantine: rows of one (file, symbol, date) that disagree (F10), and readable rejected rows (F6)
    seen = collections.defaultdict(set)
    for r in rows:
        seen[(r["file"], r["symbol"], r["date"])].add(r["close"])
    for (f, sym, d), closes in seen.items():
        if len(closes) > 1:
            p.batches.setdefault(f, None)
            p.counts["quarantined_disagreeing"] += 1
    for r in rows:
        f = r["file"]
        if p.batches.get(f) is None:
            year = int(f.split(" ")[0].split("-")[0])
            p.batches[f] = Batch(f, shas[f], "unknown" if year <= 2000 else "complete")
        b = p.batches[f]
        sym = r["symbol"]
        if not r["cls"]:                                   # a 1990s code without a class
            if sym not in l1:
                p.counts["bars_left_out_unmapped_code"] += 1
                continue
            sym = l1[sym]
        if len(seen[(f, r["symbol"], r["date"])]) > 1:
            b.quarantine[(sym, r["date"])] = "workbook_disagrees_with_itself"
            continue
        b.bars.append({"symbol": sym, "trade_date": r["date"], "close": r["close"] or None, "high": r["high"] or None,
                       "low": r["low"] or None, "open_price": r["open"] or None, "trades": r["trades"] or None,
                       "shares": r["shares"] or None, "turnover": r["turnover"] or None, "flags": r["flags"],
                       "source_sheet": r["sheet"], "source_row": int(r["row"])})
        span = b.complete.setdefault(sym, [r["date"], r["date"]])
        span[0], span[1] = min(span[0], r["date"]), max(span[1], r["date"])
        p.counts["bars"] += 1
    for r in csv.DictReader(open(os.path.join(work_dir, "rejected.csv"), newline="")):
        if r["symbol"] and r["date"] and r["file"] in p.batches:
            p.batches[r["file"]].quarantine[(r["symbol"], r["date"])] = r["reason"]
            p.counts["quarantined_rejected_row"] += 1
    for b in p.batches.values():                           # calendar completeness from 1992, years the index covers
        years = sorted({int(x["trade_date"][:4]) for x in b.bars})
        years = [y for y in years if y >= FIRST_CALENDAR_YEAR and sessions_by_year.get(y)]
        if years and {d.isoformat() for y in years for d in sessions_by_year[y]} <= {x["trade_date"] for x in b.bars}:
            b.calendar = (date(years[0], 1, 1), date(years[-1], 12, 31))
    for sym, old, new, eff, ann, sid in splits:
        if old != int(old) or new != int(new) or old == new:
            p.counts["splits_left_out_ratio"] += 1
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
            if b.calendar:
                cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, "
                            "import_id) values ('import_calendar_complete',%s,%s,%s,%s)",
                            (b.calendar[0], b.calendar[1], f"TradeIQ {label} matches the index workbook", imp))
        for a in p.actions:
            cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                        "available_at, source_reference) values (%s,%s,%s,%s,%s,%s,%s)",
                        (a["statement_id"], a["symbol"], a["kind"], a["ex_date"], a["terms_json"], a["available_at"],
                         a["source_reference"]))
    conn.commit()
