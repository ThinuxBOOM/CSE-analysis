"""
Target builds (Phase 3 §11; MF-7): the labels of stored reference snapshots, as one target build sees everything after
each reference session, stored in mf_target_builds and mf_target_rows.

A target build is a query (mode, T, H) under a configuration. It loads and builds through `jobs.build`, exactly as a
snapshot does, and labels every row of each reference snapshot at each horizon (1, 5, 10, 21) with `targets.label`.
The label reads P_D and s_D from the STORED reference row (never recomputed), and the build's own view after D.

Admission instants (for `resolved_at`, conservative by construction): an imported row enters at its batch's
recorded_at in KNOWN and CURRENT and at the end of its own Colombo day in AVAILABLE (`store.admission_instant`).
  - session finality: the latest instant of any admitted row on that date;
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


def _finality(imported):
    out = {}
    for imp in imported.values():
        for d, r in imp.rows.items():
            if r.get("instant") is not None:
                out[d] = max(out.get(d, r["instant"]), r["instant"])
    return out


def session_instants(symbol, imported, sessions, finality):
    imp = imported[symbol]
    dates = sorted(imp.rows)
    out = {}
    for s in sessions:
        own = None
        if s in imp.quarantined:
            own = imp.quarantine_instants.get(s)
        elif s in imp.rows:
            own = imp.rows[s].get("instant")
        else:
            k = bisect.bisect_right(dates, s)
            if k < len(dates) and any(lo <= s <= hi for lo, hi in imp.complete):
                own = imp.rows[dates[k]].get("instant")          # the row that closes the clipped range
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
                vals.append(store.admission_instant(query, o.recorded_at, hb.available_at(last)))
        elif query.admits(o.recorded_at, o.available_at):
            vals.append(store.admission_instant(query, o.recorded_at, o.available_at))
    return max(vals) if vals else None


def statement_instants(query, statements):
    return {s.statement_id: store.admission_instant(query, s.recorded_at, s.available_at)
            for s in statements if query.admits(s.recorded_at, s.available_at)}


def superseded_instants(symbol, statements, stmt_instants):
    """The withdrawn or superseding statements of the security and the statements they name (audits 4 and 6)."""
    named = {s.supersedes for s in statements if s.symbol == symbol and s.supersedes}
    return [stmt_instants[s.statement_id] for s in statements if s.symbol == symbol and s.statement_id in stmt_instants
            and (s.supersedes or s.kind == actions.WITHDRAWN or s.statement_id in named)]


def reference_rows(cur, snapshot_id):
    cur.execute("select d_star from mf_snapshots where snapshot_id = %s", (snapshot_id,))
    head = cur.fetchone()
    if head is None:
        raise LookupError(f"no snapshot {snapshot_id}")
    cur.execute("select symbol, px_ref, ref_session, ref_source_quality from mf_snapshot_rows where snapshot_id = %s "
                "order by symbol", (snapshot_id,))
    return [targets.ReferenceRow(sym, head[0], px, rs, f"{snapshot_id}:{sym}", rsq) for sym, px, rs, rsq in
            cur.fetchall()]


def labels(snap, imported, statements, cal_obs, query, refs_by_snapshot, policy):
    """[(reference snapshot id, Label)] and counts of rows not labelled."""
    sessions = snap.sequence.sessions
    finality = _finality(imported)
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
            inst = session_instants(row.symbol, imported, sessions, finality)
            extra = [x for x in [cal] + superseded_instants(row.symbol, statements, stmt) if x is not None]
            for h in targets.HORIZONS:
                out.append((sid, targets.label(row, h, snap.series[row.symbol], snap.references[row.symbol],
                                               session_instants=inst, statement_instants=stmt,
                                               extra_instants=tuple(extra), policy=policy)))
    return out, skipped


def build(conn, query, configuration_id, reference_snapshot_ids, code_version=VERSION):
    """Label the reference snapshots at this target build and store it. Returns a report."""
    with conn.cursor() as cur:
        cfg = configuration_.load(cur, configuration_id)
        refs = {sid: reference_rows(cur, sid) for sid in reference_snapshot_ids}
    conn.rollback()
    snap, imported, _ev, statements, cal_obs = jobs.build_all(conn, query, cfg, keep_series=True)
    labelled, skipped = labels(snap, imported, statements, cal_obs, query, refs, cfg.target_policy)
    with conn.cursor() as cur:
        cur.execute("insert into mf_target_builds (mode, cutoff, knowledge_horizon, configuration_id, "
                    "target_definition_version, code_version) values (%s,%s,%s,%s,%s,%s) returning build_id",
                    (query.mode, query.cutoff, query.horizon, configuration_id, targets.TARGET_DEFINITION_VERSION,
                     code_version))
        bid = cur.fetchone()[0]
        for sid, lab in labelled:
            vj = values_json(lab)
            adj = canonical_json(sorted(lab.factor_statement_ids))
            cur.execute("insert into mf_target_rows (build_id, reference_snapshot_id, symbol, horizon, state, "
                        "values_json, resolved_at, adjustment_set, row_hash) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (bid, sid, lab.symbol, lab.horizon, lab.state, vj, lab.resolved_at, adj,
                         row_hash(sid, lab.symbol, lab.horizon, lab.state, vj, lab.resolved_at, adj)))
    conn.commit()
    states = {}
    for _sid, lab in labelled:
        states[lab.state] = states.get(lab.state, 0) + 1
    return {"build_id": bid, "labels": len(labelled), "states": states, "not_labelled": skipped}
