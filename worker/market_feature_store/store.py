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
from datetime import date, timezone
from decimal import Decimal

from worker.market_features import actions, calendar
from worker.market_features.modes import AVAILABLE, KNOWN
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
    out["ids"] = sorted(i for r in candidates for i in r.get("ids", ()))     # provenance: mf_history_bars ids
    out["instant"] = max((r["instant"] for r in candidates if r.get("instant") is not None), default=None)
    return out


def admission_instant(query, recorded_at, available_at):
    """The instant a piece of evidence enters the query's information set: recorded_at in KNOWN and CURRENT,
    available_at in AVAILABLE (whose recorded_at <= H holds for everything admitted). Labels' resolved_at reads it."""
    return available_at if query.mode == AVAILABLE and available_at is not None else recorded_at


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
        q_instants = {}
        complete = collections.defaultdict(list)
        complete_recorded = collections.defaultdict(list)
        batch_dates = collections.defaultdict(set)
        admitted_imports = []
        for imp, recorded, cal_from, cal_to, sha in imports:
            cur.execute("select symbol, trade_date, close, high, low, open_price, trades, shares, turnover, flags, id "
                        "from mf_history_bars where import_id = %s order by symbol, trade_date, id", (imp,))
            n = 0
            for sym, d, close, high, low, opn, trades, shares, turnover, flags, bar_id in cur.fetchall():
                if not _row_admitted(query, recorded, d):
                    continue
                n += 1
                batch_dates[imp].add(d)
                candidates[(sym, d)].append({"close": close, "high": high, "low": low, "open_price": opn,
                                             "trades": trades, "shares": shares, "turnover": turnover,
                                             "flags": flags, "ids": (bar_id,),
                                             "instant": admission_instant(query, recorded, hb.available_at(d))})
            if not n:
                continue
            admitted_imports.append((imp, sha, n))
            cur.execute("select symbol, trade_date from mf_history_quarantine where import_id = %s", (imp,))
            for sym, d in cur.fetchall():
                quarantined[sym].add(d)
                inst = admission_instant(query, recorded, hb.available_at(d))
                q_instants[(sym, d)] = max(q_instants.get((sym, d), inst), inst)
            cur.execute("select symbol, date_from, date_to from mf_history_security_complete where import_id = %s",
                        (imp,))
            for sym, lo, hi in cur.fetchall():
                complete[sym].append((lo, hi))
                complete_recorded[sym].append((lo, hi, recorded))
        cur.execute("select id, kind, date_from, date_to, available_at, source_reference, import_id, recorded_at "
                    "from mf_calendar_observations order by id")
        cal_obs, cal_ids = [], []
        for oid, kind, lo, hi, avail, ref, imp, recorded in cur.fetchall():
            if imp is not None and imp not in batch_dates:
                continue                  # its batch is not admitted: its range must not close that batch's gaps
            if imp is not None and recorded > (query.cutoff if query.mode == KNOWN else query.horizon):
                continue                  # a range declared after the query's knowledge (audit of MF-7c 3rd part)
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
            q_instants[(sym, d)] = max(c["instant"] for c in cs)
        elif d not in quarantined[sym]:   # a quarantine of any admitted batch wins over a row (C-0063)
            rows[sym][d] = m
    imported = {sym: hb.Imported(rows=dict(r), complete=tuple(sorted(complete.get(sym, ()))),
                                 quarantined=frozenset(quarantined.get(sym, ())),
                                 quarantine_instants={d: i for (s_, d), i in q_instants.items() if s_ == sym},
                                 complete_recorded=tuple(sorted(complete_recorded.get(sym, ()))))
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


def input_digest(query, evidence):
    return sha256_hex(canonical_json({"query": [query.mode, query.cutoff, query.horizon], "evidence": evidence}))


def row_body(symbol, eligibility, features_json, px_ref, ref_session, stale_sessions, adj_ref, ref_source_quality,
             dstar_observation_ids, window_digest):
    return {"symbol": symbol, "eligibility": eligibility, "features": features_json, "px_ref": _value(px_ref),
            "ref_session": _value(ref_session), "stale_sessions": stale_sessions, "adj_ref": _value(adj_ref),
            "ref_source_quality": ref_source_quality, "dstar_observation_ids": dstar_observation_ids,
            "window_digest": window_digest}


def row_hash(r, dstar_observation_ids, window_digest):
    """The stored row_hash of a snapshot row dict (as `snapshot.build` returns it) with its provenance columns."""
    return sha256_hex(canonical_json(row_body(r["symbol"], r["eligibility"], row_payload(r), r["px_ref"],
                                              r["ref_session"], r["stale_sessions"], r["adj_ref"],
                                              r["ref_source_quality"], dstar_observation_ids, window_digest)))


def provenance(snap, imported, symbol):
    """(D* bar ids, window digest) of a snapshot row (§13.1): the mf_history_bars ids merged into the security's D*
    bar, and a SHA-256 over the ids of every admitted row of the security at a session of the snapshot (every window
    reads within them). `explain` re-derives the list and checks it against the digest."""
    rows = imported[symbol].rows
    dstar = list(rows.get(snap.d_star, {}).get("ids", ()))
    window = sorted(i for s in snap.sessions if s in rows for i in rows[s].get("ids", ()))
    return dstar, sha256_hex(canonical_json(window)), window


def _utc(t):
    return None if t is None else t.astimezone(timezone.utc).isoformat()


def _num(v):
    return None if v is None else str(int(v)) if v == int(v) else str(v)


def snapshot_hash(header, rows, factors, moves):
    """SHA-256 binding a snapshot's header, its rows (by row_hash), factors and move flags (audit of MF-7c third
    part, M2): a live snapshot, which is not replayed, still proves every stored column. header: (mode, label, cutoff,
    horizon, d_star, configuration_id, code_version, input_digest, adjustment_set_hash, row_count, imported_inputs);
    rows: [(symbol, row_hash)]; factors: [(symbol, kind, ex_date, numerator, denominator, status, flags,
    statement_ids_json)]; moves: [(symbol, session_from, session_to, close_from, close_to, status)]."""
    mode, label, cutoff, horizon, d_star, cid, code, digest, adj, n, imp = header
    return sha256_hex(canonical_json({
        "header": [mode, label, _utc(cutoff), _utc(horizon), _value(d_star), cid, code, digest, adj, n, bool(imp)],
        "rows": sorted([s, h] for s, h in rows),
        "factors": sorted([s, k, _value(d), _num(a), _num(b), st, fl, ids] for s, k, d, a, b, st, fl, ids in factors),
        "moves": sorted([s, _value(f), _value(t), _value(cf), _value(ct), st] for s, f, t, cf, ct, st in moves)}))


def write(conn, snap, query, *, configuration_id, code_version, evidence, imported):
    """One transaction as the worker. Returns the snapshot id."""
    input_digest_ = input_digest(query, evidence)
    rows = []
    for r in snap.rows:
        dstar_ids, window_digest, _ids = provenance(snap, imported, r["symbol"])
        dj = canonical_json(dstar_ids)
        rows.append((r["security_id"], r["symbol"], r["eligibility"], row_payload(r), r["px_ref"], r["ref_session"],
                     r["stale_sessions"], r["adj_ref"], r["ref_source_quality"], dj, window_digest,
                     row_hash(r, dj, window_digest)))
    factors = [(sym, f.kind, f.ex_date, f.numerator, f.denominator, f.status, "|".join(f.flags),
                canonical_json(sorted(f.statement_ids))) for sym, fs in sorted(snap.factors.items()) for f in fs]
    moves = []
    for sym, ms in sorted(snap.moves.items()):
        for m in ms:
            src = imported[sym].rows                       # the two raw closes of the move (design §13.1)
            moves.append((sym, m.session_from, m.session_to, src[m.session_from]["close"], src[m.session_to]["close"],
                          MOVE_STATUS[m.state]))
    header = (query.mode, query.label, query.cutoff, query.horizon, snap.d_star, configuration_id, code_version,
              input_digest_, snap.adjustment_set_hash, len(snap.rows), bool(evidence["imports"]))
    h = snapshot_hash(header, [(r[1], r[11]) for r in rows], factors, moves)
    with conn.cursor() as cur:
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, configuration_id, "
                    "code_version, input_digest, adjustment_set_hash, row_count, imported_inputs, snapshot_hash) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning snapshot_id", header + (h,))
        sid = cur.fetchone()[0]
        for r in rows:
            cur.execute("insert into mf_snapshot_rows (snapshot_id, security_id, symbol, eligibility, features_json, "
                        "px_ref, ref_session, stale_sessions, adj_ref, ref_source_quality, dstar_observation_ids, "
                        "window_digest, row_hash) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,) + r)
        for f in factors:
            cur.execute("insert into mf_snapshot_factors (snapshot_id, symbol, kind, ex_date, numerator, "
                        "denominator, status, flags, statement_ids) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,) + f)
        for m in moves:
            cur.execute("insert into mf_snapshot_move_flags (snapshot_id, symbol, session_from, session_to, "
                        "close_from, close_to, status) values (%s,%s,%s,%s,%s,%s,%s)", (sid,) + m)
    conn.commit()
    return sid
