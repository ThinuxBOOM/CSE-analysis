"""
The database side of a snapshot (Phase 3 §13.1, §13.2; MF-7c second part).

load()   reads the evidence admitted at (T, mode), in one REPEATABLE READ, READ ONLY transaction:
           imported rows (a row is admitted by its batch's recorded_at and the end of its own Colombo day, §4.1),
           quarantine and security_complete of admitted batches, the calendar observations and the corporate-action
           statements (returned whole: `actions.applying` admits them by the query inside the build)
write()  stores a snapshot through the worker: mf_snapshots, mf_snapshot_rows, mf_snapshot_factors,
           mf_snapshot_move_flags; canonical JSON and SHA-256 hashes; one transaction.
Captured (P2) evidence is not read yet: no capture era exists in this build (MF-7c third part).
"""
import collections
import json
from datetime import date
from decimal import Decimal

from worker.market_features import actions, calendar
from worker.market_features.canonical import canonical_json, sha256_hex

from . import history_bars as hb

MOVE_STATUS = {actions.UNEXPLAINED: "open", actions.CONFIRMED: "confirmed_no_action",
               actions.NOT_EVALUABLE: "not_evaluable"}


def _statement(r):
    statement_id, symbol, kind, ex_date, terms_json, supersedes, available_at, source_reference, recorded_at = r
    terms = json.loads(terms_json)
    if "subscription_price" in terms:
        terms["subscription_price"] = Decimal(str(terms["subscription_price"]))
    for k in ("session_from", "session_to"):
        if k in terms:
            terms[k] = date.fromisoformat(terms[k])
    return actions.statement(statement_id, symbol, kind, recorded_at=recorded_at, available_at=available_at,
                             source_reference=source_reference, ex_date=ex_date, terms=terms, supersedes=supersedes)


FIELDS = ("close", "high", "low", "open_price", "trades", "shares", "turnover")


def merge(candidates):
    """One row from the rows several batches (or one batch twice) hold for a (security, date): field by field, a
    value present in one row fills another's gap; two different values in any field return None (the pair is
    quarantined). A row without a volume never erases one with a volume (audit M-B)."""
    out = {}
    for f in FIELDS:
        vals = {r[f] for r in candidates if r[f] is not None}
        if len(vals) > 1:
            return None
        out[f] = next(iter(vals), None)
    out["flags"] = "|".join(sorted({x for r in candidates for x in r["flags"].split("|") if x}))
    return out


def _row_admitted(query, recorded, d):
    """A row is admitted by its batch's recorded_at and the end of its own Colombo day (§4.1). In KNOWN too a row
    dated after T's day is never admitted: it cannot be a final session at T (audit M-D)."""
    if not query.admits(recorded, hb.available_at(d)):
        return False
    return query.cutoff is None or hb.available_at(d) <= query.cutoff


def load(conn, query):
    """(imported_by_symbol, statements, calendar_observations, evidence) for the query. `evidence` lists what was
    admitted, for the snapshot's input digest: evidence recorded later never changes it."""
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        cur.execute("select import_id, recorded_at, calendar_complete_from, calendar_complete_to, source_sha256 "
                    "from mf_history_imports where price_basis = 'raw' and no_trade_convention = 'row_absent' "
                    "order by import_id")
        imports = cur.fetchall()
        candidates = collections.defaultdict(list)
        quarantined = collections.defaultdict(set)
        complete = collections.defaultdict(list)
        batch_dates = collections.defaultdict(set)
        admitted_imports = []
        for imp, recorded, cal_from, cal_to, sha in imports:
            cur.execute("select symbol, trade_date, close, high, low, open_price, trades, shares, turnover, flags "
                        "from mf_history_bars where import_id = %s order by symbol, trade_date, id", (imp,))
            n = 0
            for sym, d, close, high, low, opn, trades, shares, turnover, flags in cur.fetchall():
                if not _row_admitted(query, recorded, d):
                    continue
                n += 1
                batch_dates[imp].add(d)
                candidates[(sym, d)].append({"close": close, "high": high, "low": low, "open_price": opn,
                                             "trades": trades, "shares": shares, "turnover": turnover,
                                             "flags": flags})
            if not n:
                continue
            admitted_imports.append((imp, sha, n))
            cur.execute("select symbol, trade_date from mf_history_quarantine where import_id = %s", (imp,))
            for sym, d in cur.fetchall():
                quarantined[sym].add(d)
            cur.execute("select symbol, date_from, date_to from mf_history_security_complete where import_id = %s",
                        (imp,))
            for sym, lo, hi in cur.fetchall():
                complete[sym].append((lo, hi))
        cur.execute("select id, kind, date_from, date_to, available_at, source_reference, import_id, recorded_at "
                    "from mf_calendar_observations order by id")
        cal_obs, cal_ids = [], []
        for oid, kind, lo, hi, avail, ref, imp, recorded in cur.fetchall():
            if imp is not None and imp not in batch_dates:
                continue                  # its batch is not admitted: its range must not close that batch's gaps
            if imp is None and not query.admits(recorded, avail):
                continue
            cal_ids.append(oid)
            cal_obs.append(calendar.CalendarObservation(kind, lo, hi, recorded, avail, ref,
                                                        frozenset(batch_dates[imp]) if imp else frozenset()))
        cur.execute("select statement_id, symbol, kind, ex_date, terms_json, supersedes, available_at, "
                    "source_reference, recorded_at from mf_corporate_action_observations order by statement_id")
        statements = [_statement(r) for r in cur.fetchall()]
    conn.rollback()
    rows = collections.defaultdict(dict)
    for (sym, d), cs in candidates.items():
        m = merge(cs)
        if m is None:                     # two rows disagree on a value: quarantined
            quarantined[sym].add(d)
        elif d not in quarantined[sym]:   # a quarantine of any admitted batch wins over a row (C-0063)
            rows[sym][d] = m
    imported = {sym: hb.Imported(rows=dict(r), complete=tuple(sorted(complete.get(sym, ()))),
                                 quarantined=frozenset(quarantined.get(sym, ())))
                for sym, r in rows.items()}
    admitted_statements = sorted(s.statement_id for s in statements if query.admits(s.recorded_at, s.available_at))
    evidence = {"imports": admitted_imports, "statements": admitted_statements, "calendar": cal_ids}
    return imported, statements, cal_obs, evidence


def _value(v):
    return None if v is None else str(v)


def row_payload(r):
    feats = {k: {"value": _value(v), "state": st, "reasons": [list(x) for x in reasons]}
             for k, (v, st, reasons) in sorted(r["features"].items())}
    return canonical_json(feats)


def write(conn, snap, query, *, configuration_id, code_version, evidence, imported):
    """One transaction as the worker. Returns the snapshot id."""
    input_digest = sha256_hex(canonical_json({"query": [query.mode, query.cutoff, query.horizon],
                                              "evidence": evidence}))
    with conn.cursor() as cur:
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, configuration_id, "
                    "code_version, input_digest, adjustment_set_hash, row_count, imported_inputs) values "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning snapshot_id",
                    (query.mode, query.label, query.cutoff, query.horizon, snap.d_star, configuration_id, code_version,
                     input_digest, snap.adjustment_set_hash, len(snap.rows), bool(evidence["imports"])))
        sid = cur.fetchone()[0]
        for r in snap.rows:
            fj = row_payload(r)
            body = {"symbol": r["symbol"], "eligibility": r["eligibility"], "features": fj,
                    "px_ref": _value(r["px_ref"]), "ref_session": _value(r["ref_session"]),
                    "stale_sessions": r["stale_sessions"], "adj_ref": _value(r["adj_ref"]),
                    "ref_source_quality": r["ref_source_quality"]}
            cur.execute("insert into mf_snapshot_rows (snapshot_id, security_id, symbol, eligibility, features_json, "
                        "px_ref, ref_session, stale_sessions, adj_ref, ref_source_quality, window_digest, row_hash) "
                        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (sid, r["security_id"], r["symbol"], r["eligibility"], fj, r["px_ref"], r["ref_session"],
                         r["stale_sessions"], r["adj_ref"], r["ref_source_quality"], input_digest,
                         sha256_hex(canonical_json(body))))
        for sym, fs in sorted(snap.factors.items()):
            for f in fs:
                cur.execute("insert into mf_snapshot_factors (snapshot_id, symbol, kind, ex_date, numerator, "
                            "denominator, status, flags, statement_ids) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (sid, sym, f.kind, f.ex_date, f.numerator, f.denominator, f.status, "|".join(f.flags),
                             canonical_json(sorted(f.statement_ids))))
        for sym, ms in sorted(snap.moves.items()):
            for m in ms:
                rows = imported[sym].rows                  # the two raw closes of the move (design §13.1)
                close_from, close_to = rows[m.session_from]["close"], rows[m.session_to]["close"]
                cur.execute("insert into mf_snapshot_move_flags (snapshot_id, symbol, session_from, session_to, "
                            "close_from, close_to, status) values (%s,%s,%s,%s,%s,%s,%s)",
                            (sid, sym, m.session_from, m.session_to, close_from, close_to, MOVE_STATUS[m.state]))
    conn.commit()
    return sid
