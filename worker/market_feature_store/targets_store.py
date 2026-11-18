"""
Target builds (Phase 3 §11; MF-7): the labels of stored reference snapshots, as one target build sees everything after
each reference session, stored in mf_target_builds and mf_target_rows.

A target build is a query (mode, T, H) under a configuration. It loads and builds through `jobs.build`, exactly as a
snapshot does, and labels every row of each reference snapshot at each horizon (1, 5, 10, 21) with `targets.label`.
The label reads P_D and s_D from the STORED reference row (never recomputed), and the build's own view after D.

Admission instants (for `resolved_at`, conservative by construction): an imported row enters at the end of its own
Colombo day in AVAILABLE, and at the later of its batch's recorded_at and that instant in KNOWN (`store.row_instant`).
A batch enters with its first admitted row; its quarantines, ranges and calendar row never take an earlier instant
than that (re-audit N-B1).
  - session finality: the latest instant of any admitted row or quarantine dated after the previous session up to
    this one (a date that stopped being a session binds the next one);
  - the security's own bar: its row's instant; a quarantine's instant; a no-trade bar (a `security_complete` range,
    clipped at the last admitted row) rests on the security's NEXT admitted row, so it takes that row's instant;
  - statements: their admission instant; every withdrawn or superseding statement of the security, and the one it
    names, go into `extra_instants`;
  - calendar evidence, all of it (audit 8): a declared closure's admission instant; an import range's, at the latest
    date of the range the build admits.
"""
import bisect
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal

from worker.market_features import VERSION, actions, targets
from worker.market_features.canonical import canonical_json, sha256_hex
from worker.market_features.modes import AVAILABLE, KNOWN

from . import configuration as configuration_
from . import history_bars as hb
from . import jobs, store


def _json_value(v):
    if isinstance(v, (Decimal, date, datetime)):
        return v.isoformat() if isinstance(v, (date, datetime)) else str(v)
    if isinstance(v, tuple):
        return [_json_value(x) for x in v]
    return v


def values_json(lab):
    skip = {"symbol", "horizon", "state", "resolved_at", "factor_statement_ids", "reference_session"}
    return canonical_json({k: _json_value(v) for k, v in sorted(asdict(lab).items()) if k not in skip})


def row_hash(reference_snapshot_id, symbol, horizon, state, values, resolved_at, adjustment_set):
    return sha256_hex(canonical_json([reference_snapshot_id, symbol, horizon, state, values,
                                      None if resolved_at is None else resolved_at.astimezone(timezone.utc).isoformat(),
                                      adjustment_set]))


def build_hash(header, rows, written=None):
    """SHA-256 binding a target build's header, its recorded_at and created_by, and its rows (reference snapshot,
    symbol, horizon, row_hash)."""
    mode, cutoff, horizon, cid, version, code = header
    recorded, by = written or (None, None)
    utc = lambda t: None if t is None else t.astimezone(timezone.utc).isoformat()  # noqa: E731
    return sha256_hex(canonical_json({"header": [mode, utc(cutoff), utc(horizon), cid, version, code],
                                      "written": [utc(recorded), by],
                                      "rows": sorted([a, b, c, d] for a, b, c, d in rows)}))


def _finality(imported, sessions):
    """{session: the latest instant of any admitted evidence dated in (previous session, session]}: every row of every
    security (zero-price rows included) and every quarantine. A session is final because of some of it, and a date
    between two sessions is not a session because of the rest (a quarantine, a disagreeing correction, a zero-price
    row that removed it: re-audit 2, B-1(a)), so the next session carries that date's instants: a label that reads
    across the date covers them."""
    by_date = {}
    for imp in imported.values():
        for d, r in imp.rows.items():
            if r.get("instant") is not None:
                by_date[d] = max(by_date.get(d, r["instant"]), r["instant"])
        for d, i in imp.quarantine_instants.items():
            if i is not None:
                by_date[d] = max(by_date.get(d, i), i)
    out, dates = {}, sorted(by_date)
    k = 0
    for i, s in enumerate(sessions):
        prev = sessions[i - 1] if i else None
        vals = []
        while k < len(dates) and dates[k] <= s:
            if prev is None or dates[k] > prev:
                vals.append(by_date[dates[k]])
            k += 1
        if vals:
            out[s] = max(vals)
    return out


def session_instants(symbol, imported, sessions, finality, query):
    imp = imported[symbol]
    dates = sorted(imp.rows)
    q_dates = sorted(imp.quarantine_instants)
    out = {}
    for s in sessions:
        own = None
        if s in imp.quarantined:
            own = imp.quarantine_instants.get(s)
        elif s in imp.rows:
            own = imp.rows[s].get("instant")
        else:
            k = bisect.bisect_right(dates, s)
            ranges = [rec for lo, hi, rec in imp.complete_recorded if lo <= s <= hi]
            in_range = any(lo <= s <= hi for lo, hi in imp.complete)
            if k < len(dates) and in_range:
                # the row that closes the clipped range AND every batch whose range makes the bar (audit B1), each
                # at its batch's admission instant (re-audit N-B1), and never before the end of the day itself
                own = max([imp.rows[dates[k]].get("instant"), hb.available_at(s)] + ranges)
            elif in_range:
                # no bar because the range is clipped at the last admitted row: a quarantine of a later row is what
                # clipped it (re-audit 2, B-1(b))
                later = [imp.quarantine_instants[d] for d in q_dates[bisect.bisect_right(q_dates, s):]
                         if imp.quarantine_instants[d] is not None]
                own = max(later) if later else None
        vals = [x for x in (finality.get(s), own) if x is not None]
        if vals:
            out[s] = max(vals)
    return out


def calendar_instant(query, cal_obs, d_star):
    vals = []
    for o in cal_obs:
        if o.kind == "import_calendar_complete":
            last = min(o.date_to, d_star)
            if last >= o.date_from:
                vals.append(store.row_instant(query, o.recorded_at, last))
                if getattr(o, "admitted_at", None) is not None:
                    vals.append(o.admitted_at)                   # its batch's admission (re-audit N-B1)
        elif query.admits(o.recorded_at, o.available_at):
            vals.append(store.admission_instant(query, o.recorded_at, o.available_at))
    return max(vals) if vals else None


def statement_instants(query, statements):
    return {s.statement_id: store.admission_instant(query, s.recorded_at, s.available_at)
            for s in statements if query.admits(s.recorded_at, s.available_at)}


def superseded_instants(symbol, statements, stmt_instants):
    """The withdrawn or superseding statements of the security and every statement they name, whatever its security
    (a misdirected supersession naming another security's statement stops applying once that statement is admitted:
    audit 3, B-2; audits 4 and 6)."""
    named = {s.supersedes for s in statements if s.symbol == symbol and s.supersedes}
    return [stmt_instants[s.statement_id] for s in statements if s.statement_id in stmt_instants
            and ((s.symbol == symbol and (s.supersedes or s.kind == actions.WITHDRAWN)) or s.statement_id in named)]


def admissible_reference(query, mode, cutoff, horizon, recorded_at):
    """The instant the build admits a stored reference snapshot, or why it may not (audit B2). The reference's own
    information set must lie inside the build's: in KNOWN, a KNOWN snapshot written by T; in AVAILABLE, a KNOWN or
    AVAILABLE snapshot with T_ref <= T, written by H, whose own horizon (if any) is not after H."""
    if query.mode == KNOWN:
        if mode != KNOWN or recorded_at > query.cutoff:
            return None, "reference snapshot is not in a KNOWN build's information set (mode, or written after T)"
        return recorded_at, None
    if mode not in (KNOWN, AVAILABLE) or cutoff > query.cutoff or recorded_at > query.horizon or (
            horizon is not None and horizon > query.horizon):
        return None, "reference snapshot is not in this AVAILABLE build's information set (T, H or recorded_at)"
    return store.admission_instant(query, recorded_at, cutoff), None


def reference_rows(cur, snapshot_id):
    cur.execute("select d_star from mf_snapshots where snapshot_id = %s", (snapshot_id,))
    head = cur.fetchone()
    if head is None:
        raise LookupError(f"no snapshot {snapshot_id}")
    cur.execute("select symbol, px_ref, ref_session, ref_source_quality from mf_snapshot_rows where snapshot_id = %s "
                "order by symbol", (snapshot_id,))
    return [targets.ReferenceRow(sym, head[0], px, rs, f"{snapshot_id}:{sym}", rsq) for sym, px, rs, rsq in
            cur.fetchall()]


def labels(snap, imported, statements, cal_obs, query, refs_by_snapshot, policy, ref_instants=None):
    """[(reference snapshot id, Label)] and counts of rows not labelled. ref_instants: {snapshot id: the instant the
    build admits that stored reference snapshot}; the stored P_D, s_D and quality are inputs of every label."""
    ref_instants = ref_instants or {}
    sessions = snap.sequence.sessions
    finality = _finality(imported, sessions)
    stmt = statement_instants(query, statements)
    cal = calendar_instant(query, cal_obs, snap.d_star)
    out, skipped = [], {}
    for sid, rows in sorted(refs_by_snapshot.items()):
        for row in rows:
            why = ("security_not_in_build" if row.symbol not in snap.series else
                   "reference_session_not_final_at_build" if row.session not in sessions else None)
            if why:
                skipped[why] = skipped.get(why, 0) + 4
                continue
            inst = session_instants(row.symbol, imported, sessions, finality, query)
            # the security's presence in the build rests on its surviving rows: a security whose rows were all set
            # aside and that came back through a later row exists only from that row's admission (audit 4, E-1). A
            # row that survives at T survives at any earlier T' where it is admitted, so the earliest one suffices.
            surviving = [r.get("instant") for r in imported[row.symbol].rows.values() if r.get("instant") is not None]
            presence = min(surviving) if surviving else None
            extra = [x for x in [cal, ref_instants.get(sid), presence]
                     + superseded_instants(row.symbol, statements, stmt) if x is not None]
            for h in targets.HORIZONS:
                out.append((sid, targets.label(row, h, snap.series[row.symbol], snap.references[row.symbol],
                                               session_instants=inst, statement_instants=stmt,
                                               extra_instants=tuple(extra), policy=policy)))
    return out, skipped


def build(conn, query, configuration_id, reference_snapshot_ids, code_version=VERSION):
    """Label the reference snapshots at this target build and store it. Returns a report."""
    if query.mode not in (KNOWN, AVAILABLE):
        raise configuration_.ConfigurationError("a CURRENT target build is never stored")
    jobs.refuse_future(conn, query)
    with conn.cursor() as cur:
        cfg = configuration_.load(cur, configuration_id)
        refs, ref_instants = {}, {}
        for sid in reference_snapshot_ids:
            cur.execute("select mode, cutoff, knowledge_horizon, recorded_at from mf_snapshots where snapshot_id = %s",
                        (sid,))
            head = cur.fetchone()
            if head is None:
                raise LookupError(f"no snapshot {sid}")
            inst, why = admissible_reference(query, *head)
            if why:
                raise configuration_.ConfigurationError(f"snapshot {sid}: {why}")
            refs[sid], ref_instants[sid] = reference_rows(cur, sid), inst
    conn.rollback()
    snap, imported, _ev, statements, cal_obs = jobs.build_all(conn, query, cfg, keep_series=True)
    if snap.captured is not None and snap.captured.finals:
        # MF-8 part 2b: labels' resolved_at on captured sessions (the governing events' and observations' instants)
        # is not built yet; refusing is better than labelling captured windows with imported-only instants
        raise configuration_.ConfigurationError("target builds over captured sessions are not implemented yet "
                                                "(MF-8 part 2b)")
    labelled, skipped = labels(snap, imported, statements, cal_obs, query, refs, cfg.target_policy, ref_instants)
    rows = []
    for sid, lab in labelled:
        vj = values_json(lab)
        adj = canonical_json(sorted(lab.factor_statement_ids))
        rows.append((sid, lab.symbol, lab.horizon, lab.state, vj, lab.resolved_at, adj,
                     row_hash(sid, lab.symbol, lab.horizon, lab.state, vj, lab.resolved_at, adj)))
    header = (query.mode, query.cutoff, query.horizon, configuration_id, targets.TARGET_DEFINITION_VERSION,
              code_version)
    with conn.cursor() as cur:
        cur.execute("select now(), session_user")             # the column defaults of this transaction's inserts
        bh = build_hash(header, [(r[0], r[1], r[2], r[7]) for r in rows], cur.fetchone())
        cur.execute("insert into mf_target_builds (mode, cutoff, knowledge_horizon, configuration_id, "
                    "target_definition_version, code_version, build_hash) values (%s,%s,%s,%s,%s,%s,%s) "
                    "returning build_id", header + (bh,))
        bid = cur.fetchone()[0]
        for r in rows:
            cur.execute("insert into mf_target_rows (build_id, reference_snapshot_id, symbol, horizon, state, "
                        "values_json, resolved_at, adjustment_set, row_hash) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (bid,) + r)
    conn.commit()
    states = {}
    for _sid, lab in labelled:
        states[lab.state] = states.get(lab.state, 0) + 1
    return {"build_id": bid, "labels": len(labelled), "states": states, "not_labelled": skipped}
