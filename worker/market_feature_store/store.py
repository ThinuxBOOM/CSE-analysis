"""
The database side of a snapshot (Phase 3 §13.1, §13.2; MF-7c second part).

load()   reads the evidence admitted at (T, mode), in one REPEATABLE READ, READ ONLY transaction:
           imported rows (a row is admitted by its batch's recorded_at and the end of its own Colombo day, §4.1),
           quarantine and security_complete of admitted batches, the calendar observations and the corporate-action
           statements (returned whole: `actions.applying` admits them by the query inside the build)
write()  stores a snapshot through the worker: mf_snapshots, mf_snapshot_rows, mf_snapshot_factors,
           mf_snapshot_move_flags; canonical JSON and SHA-256 hashes; one transaction.
Captured (P2) evidence is read by `captured.load` (MF-8) in its own read-only transaction; `jobs.build_all` combines
the two. The two reads are separate snapshots of the database: for a settled T (§4.1) they see the same evidence, so
a replay is exact; a live snapshot at an unsettled T is proved from its stored rows, as before (MF-8 audit F6).
A row's `row_hash` does not bind `company_id`; the snapshot's `snapshot_hash` does (MF-8 audit F8: DE-4 reads the
row together with its snapshot, whose hash covers every row's company_id).
"""
import collections
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

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


def row_instant(query, recorded_at, d):
    """The instant an imported row (or evidence dated d) enters the query: the end of its Colombo day in AVAILABLE;
    in KNOWN the later of its batch's recorded_at and the end of its day, since a row dated after T's day is never
    admitted (audit of MF-7c third part, re-audit N-B1)."""
    end = hb.available_at(d)
    return end if query.mode == AVAILABLE else max(recorded_at, end)


@dataclass(frozen=True)
class AdmittedCalendarObservation(calendar.CalendarObservation):
    """A calendar observation with the instant the loader admitted it: for an import range, its batch's admission
    instant (the batch enters with its first admitted row). Labels' resolved_at reads it."""
    admitted_at: Optional[datetime] = None


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
        batch_instant = {}
        admitted_imports = []
        for imp, recorded, cal_from, cal_to, sha in imports:
            cur.execute("select symbol, trade_date, close, high, low, open_price, trades, shares, turnover, flags, id "
                        "from mf_history_bars where import_id = %s order by symbol, trade_date, id", (imp,))
            n, instants = 0, []
            for sym, d, close, high, low, opn, trades, shares, turnover, flags, bar_id in cur.fetchall():
                if not _row_admitted(query, recorded, d):
                    continue
                n += 1
                instants.append(row_instant(query, recorded, d))
                batch_dates[imp].add(d)
                candidates[(sym, d)].append({"close": close, "high": high, "low": low, "open_price": opn,
                                             "trades": trades, "shares": shares, "turnover": turnover,
                                             "flags": flags, "ids": (bar_id,),
                                             "instant": instants[-1]})
            if not n:
                continue
            # the batch, and with it its quarantines, ranges and calendar row, enters with its first admitted row
            batch_instant[imp] = min(instants)
            admitted_imports.append((imp, sha, n))
            cur.execute("select symbol, trade_date from mf_history_quarantine where import_id = %s", (imp,))
            for sym, d in cur.fetchall():
                if query.cutoff is not None and hb.available_at(d) > query.cutoff:
                    continue                  # dated after T's day: not admitted, as rows are not (audit 3, B-3)
                quarantined[sym].add(d)
                inst = max(row_instant(query, recorded, d), batch_instant[imp])
                q_instants[(sym, d)] = max(q_instants.get((sym, d), inst), inst)
            cur.execute("select symbol, date_from, date_to from mf_history_security_complete where import_id = %s",
                        (imp,))
            for sym, lo, hi in cur.fetchall():
                complete[sym].append((lo, hi))
                complete_recorded[sym].append((lo, hi, batch_instant[imp]))
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
            cal_obs.append(AdmittedCalendarObservation(
                kind, lo, hi, recorded, avail, ref, frozenset(batch_dates[imp]) if imp else frozenset(),
                admitted_at=max(batch_instant[imp], recorded if query.mode != AVAILABLE else batch_instant[imp])
                if imp else None))
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
    # a security whose every row was quarantined or disagreed keeps an entry without rows: its quarantine
    # instants still bind the labels that read across the session it removed (audit 3, B-1). A snapshot skips it.
    symbols = set(rows) | {sym for sym, ds in quarantined.items() if ds}
    imported = {sym: hb.Imported(rows=dict(rows.get(sym, {})), complete=tuple(sorted(complete.get(sym, ()))),
                                 quarantined=frozenset(quarantined.get(sym, ())),
                                 quarantine_instants={d: i for (s_, d), i in q_instants.items() if s_ == sym},
                                 complete_recorded=tuple(sorted(complete_recorded.get(sym, ()))))
                for sym in sorted(symbols)}
    admitted_statements = sorted(s.statement_id for s in statements if query.admits(s.recorded_at, s.available_at))
    evidence = {"imports": admitted_imports, "statements": admitted_statements, "calendar": cal_ids}
    return imported, statements, cal_obs, evidence


def load_identity(conn, query):
    """Every identity observation (§8.1), and the ids of those the query admits (for the input digest). Admission
    itself is `universe.series_segments`'s, by (recorded_at, available_at): a link takes effect from its recorded_at in
    KNOWN and from its announcement in AVAILABLE."""
    from worker.market_features import universe
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        cur.execute("select id, kind, symbol, predecessor, effective_date, available_at, source_reference, recorded_at "
                    "from mf_identity_observations order by id")
        rows = cur.fetchall()
    conn.rollback()
    obs, admitted = [], []
    for i, kind, sym, pred, eff, avail, ref, recorded in rows:
        obs.append(universe.IdentityObservation(kind, sym, eff, recorded, avail, ref, pred))
        if query.admits(recorded, avail):
            admitted.append(i)
    return tuple(obs), admitted


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
    from .snapshot import segment_symbol
    segs = getattr(snap, "segments", {}).get(symbol)

    def owner(s):                                 # §8.1: the symbol whose segment holds the session
        return segment_symbol(segs, s) if segs else symbol

    def rows_of(sym):
        return imported[sym].rows if sym in imported else {}
    dstar_owner = owner(snap.d_star)
    dstar = list(rows_of(dstar_owner).get(snap.d_star, {}).get("ids", ())) if dstar_owner else []
    window = sorted(i for s in snap.sessions if owner(s) and s in rows_of(owner(s))
                    for i in rows_of(owner(s))[s].get("ids", ()))
    cap = getattr(snap, "captured", None)
    if cap is not None:
        # MF-8: the raw observation ids reconcile merged into the captured bars, after the imported bar ids (ints
        # and uuid texts are never sorted together, so an import-only snapshot keeps its exact provenance)
        b = cap.bar_on(dstar_owner, snap.d_star) if dstar_owner else None
        dstar += sorted(str(i) for i in (b.contributing_observation_ids if b is not None else ()))
        window += sorted(str(i) for s in snap.sessions if owner(s) and cap.bar_on(owner(s), s) is not None
                         for i in cap.bar_on(owner(s), s).contributing_observation_ids)
    return dstar, sha256_hex(canonical_json(window)), window


def _utc(t):
    return None if t is None else t.astimezone(timezone.utc).isoformat()


def _num(v):
    return None if v is None else str(int(v)) if v == int(v) else str(v)


SURVIVORSHIP_ORDER = ("complete", "survivors_only", "unknown")
FEATURE_WINDOW_DAYS = 400              # the longest feature window, in calendar days (survivorship's reach)


def market_payload(market):
    """The snapshot's market-wide features as canonical JSON (C-0116), in the rows' feature format."""
    return canonical_json({k: {"value": _value(v.value), "state": v.state, "reasons": [list(x) for x in v.reasons]}
                           for k, v in sorted(market.items())})


def snapshot_survivorship(cur, query, d_star, imported_inputs):
    """C-0116 (replacing C-0089's departure): the survivorship of the imported history a snapshot read. A snapshot that
    read no imported batch is `complete` (captured data is the whole universe). Otherwise the worst survivorship of
    the raw `row_absent` batches admitted by the governing time (T in KNOWN, H in AVAILABLE) with a bar in the
    longest feature window before D*; none in reach is `unknown`. Never prices: headers and bar dates only."""
    if not imported_inputs:
        return "complete"
    governing = query.horizon if query.mode == AVAILABLE else query.cutoff
    cur.execute("select distinct i.survivorship from mf_history_imports i where i.price_basis = 'raw' and "
                "i.no_trade_convention = 'row_absent' and i.recorded_at <= %s and exists (select 1 from "
                "mf_history_bars b where b.import_id = i.import_id and b.trade_date between %s and %s)",
                (governing, d_star - timedelta(days=FEATURE_WINDOW_DAYS), d_star))
    found = [r[0] for r in cur.fetchall()]
    return max(found, key=SURVIVORSHIP_ORDER.index) if found else "unknown"


def snapshot_hash(header, rows, factors, moves, written=None, market="{}", survivorship="unknown"):
    """SHA-256 binding a snapshot's header, its rows (by row_hash), factors and move flags (audit of MF-7c third
    part, M2): a live snapshot, which is not replayed, still proves every stored column. header: (mode, label, cutoff,
    horizon, d_star, configuration_id, code_version, input_digest, adjustment_set_hash, row_count, imported_inputs);
    market: the stored market_json; survivorship: the stored survivorship (C-0116);
    rows: [(symbol, row_hash)]; factors: [(symbol, kind, ex_date, numerator, denominator, status, flags,
    statement_ids_json)]; moves: [(symbol, session_from, session_to, close_from, close_to, status)]."""
    mode, label, cutoff, horizon, d_star, cid, code, digest, adj, n, imp = header
    recorded, by, pending = written or (None, None, "[]")
    return sha256_hex(canonical_json({
        "header": [mode, label, _utc(cutoff), _utc(horizon), _value(d_star), cid, code, digest, adj, n, bool(imp)],
        "written": [_utc(recorded), by, pending],          # recorded_at, created_by, pending_sessions (re-audit M-b)
        "market": market, "survivorship": survivorship,       # C-0116
        "rows": sorted([s, h, _value(c)] for s, h, c in rows),         # symbol, row_hash, company_id
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
                     row_hash(r, dj, window_digest), r.get("company_id")))
    factors = [(sym, f.kind, f.ex_date, f.numerator, f.denominator, f.status, "|".join(f.flags),
                canonical_json(sorted(f.statement_ids))) for sym, fs in sorted(snap.factors.items()) for f in fs]
    moves = []
    for sym, ms in sorted(snap.moves.items()):
        for m in ms:
            src = snap.closes[sym]                         # the two raw closes of the move (design §13.1; MF-8:
            moves.append((sym, m.session_from, m.session_to, src[m.session_from], src[m.session_to],   # either source)
                          MOVE_STATUS[m.state]))
    header = (query.mode, query.label, query.cutoff, query.horizon, snap.d_star, configuration_id, code_version,
              input_digest_, snap.adjustment_set_hash, len(snap.rows), bool(evidence["imports"]))
    with conn.cursor() as cur:
        cur.execute("select now(), session_user")             # the column defaults of this transaction's inserts
        recorded, by = cur.fetchone()
        pending = canonical_json([d.isoformat() for d in getattr(snap, "pending", ())])
        market = market_payload(snap.market)
        surv = snapshot_survivorship(cur, query, snap.d_star, bool(evidence["imports"])) if snap.d_star else "unknown"
        h = snapshot_hash(header, [(r[1], r[11], r[12]) for r in rows], factors, moves, (recorded, by, pending),
                          market, surv)
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, configuration_id, "
                    "code_version, input_digest, adjustment_set_hash, row_count, imported_inputs, snapshot_hash, "
                    "pending_sessions, market_json, survivorship) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "returning snapshot_id", header + (h, pending, market, surv))
        sid = cur.fetchone()[0]
        for r in rows:
            cur.execute("insert into mf_snapshot_rows (snapshot_id, security_id, symbol, eligibility, features_json, "
                        "px_ref, ref_session, stale_sessions, adj_ref, ref_source_quality, dstar_observation_ids, "
                        "window_digest, row_hash, company_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (sid,) + r)
        for f in factors:
            cur.execute("insert into mf_snapshot_factors (snapshot_id, symbol, kind, ex_date, numerator, "
                        "denominator, status, flags, statement_ids) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,) + f)
        for m in moves:
            cur.execute("insert into mf_snapshot_move_flags (snapshot_id, symbol, session_from, session_to, "
                        "close_from, close_to, status) values (%s,%s,%s,%s,%s,%s,%s)", (sid,) + m)
    conn.commit()
    return sid
