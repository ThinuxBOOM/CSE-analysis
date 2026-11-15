"""
`verify` (Phase 3 §13.2; ML-13): re-prove what MF stored. Read-only.

For every stored snapshot (or the latest N):
  - each row's `row_hash` and `security_id` recomputed from its stored columns, and the row count;
  - `adjustment_set_hash` recomputed from the stored factor list;
  - if its governing time (T in KNOWN, H in AVAILABLE) was settled when it was written, the snapshot is rebuilt from
    the evidence through `jobs.build` and compared: D*, input digest, adjustment set, rows, factors, move flags.
    "Settled" is F8's rule (§4.1), applied with a margin: the governing time lies at least `margin` before the
    snapshot's own recorded_at, so every writing transaction that began by then had ended (MF's writers are short).
    A live snapshot, written within the margin, is proved from its stored rows only. CURRENT is never replayed.
For every target build: each row's `row_hash` and the build's `build_hash`; settled labels on which two builds under
one configuration disagree are reported (§11), not counted as a failure.
Every snapshot's `snapshot_hash` binds its header, rows, factors and move flags, so a live snapshot is proved too.
The calendar: every `calendar-sync` copy whose `trading_calendar` row no longer says the same (drift).
"""
import json
from datetime import timedelta
from types import SimpleNamespace

from worker.market_features import actions, canonical
from worker.market_features.canonical import canonical_json, sha256_hex
from worker.market_features.modes import AVAILABLE, KNOWN, Query

from . import calendar_sync, configuration, jobs, store

MARGIN = timedelta(minutes=15)


def _int(v):
    return int(v) if v is not None and v == int(v) else v


def _stored(cur, sid):
    cur.execute("select symbol, security_id, eligibility, features_json, px_ref, ref_session, stale_sessions, adj_ref, "
                "ref_source_quality, dstar_observation_ids, window_digest, row_hash from mf_snapshot_rows where snapshot_id = %s order by symbol", (sid,))
    rows = cur.fetchall()
    cur.execute("select symbol, kind, ex_date, numerator, denominator, status, flags, statement_ids "
                "from mf_snapshot_factors where snapshot_id = %s order by symbol, ex_date, kind", (sid,))
    raw = cur.fetchall()
    factors = [SimpleNamespace(symbol=s, kind=k, ex_date=d, numerator=_int(n), denominator=_int(m), status=st,
                               flags=tuple(x for x in fl.split("|") if x), statement_ids=tuple(json.loads(ids)))
               for s, k, d, n, m, st, fl, ids in raw]
    cur.execute("select symbol, session_from, session_to, close_from, close_to, status from mf_snapshot_move_flags "
                "where snapshot_id = %s order by symbol, session_from, session_to", (sid,))
    moves = cur.fetchall()
    return rows, factors, moves, raw


def _factor_rows(factors):
    return sorted((f.symbol, f.kind, f.ex_date, _int(f.numerator), _int(f.denominator), f.status,
                   "|".join(sorted(f.flags) if not isinstance(f.flags, str) else f.flags.split("|")),
                   tuple(sorted(f.statement_ids))) for f in factors)


def snapshot_report(conn, header, margin=MARGIN):
    (sid, mode, cutoff, horizon, d_star, cid, digest, adj_hash, row_count, recorded, label, code, imp_inputs,
     stored_hash) = header
    problems = []
    with conn.cursor() as cur:
        rows, factors, moves, raw_factors = _stored(cur, sid)
        cur.execute("select configuration_json from mf_configurations where configuration_id = %s", (cid,))
        cfg_json = cur.fetchone()[0]
    conn.rollback()
    if store.snapshot_hash((mode, label, cutoff, horizon, d_star, cid, code, digest, adj_hash, row_count, imp_inputs),
                           [(r[0], r[11]) for r in rows], raw_factors, moves) != stored_hash:
        problems.append("snapshot_hash does not match the stored header, rows, factors and move flags")
    if len(rows) != row_count:
        problems.append(f"row_count {row_count} but {len(rows)} rows stored")
    for sym, sec, el, fj, px, rs, stale, adj, rsq, dj, wd, h in rows:
        if sec != canonical.security_id(sym):
            problems.append(f"{sym}: security_id is not the key of its symbol")
        if sha256_hex(canonical_json(store.row_body(sym, el, fj, px, rs, stale, adj, rsq, dj, wd))) != h:
            problems.append(f"{sym}: row_hash does not match the stored row")
    by = {}
    for f in factors:
        by.setdefault(f.symbol, []).append(f)
    if actions.adjustment_set_hash(by) != adj_hash:
        problems.append("adjustment_set_hash does not match the stored factors")
    governing = cutoff if mode == KNOWN else horizon if mode == AVAILABLE else None
    replay = governing is not None and governing + margin <= recorded
    out = {"snapshot_id": sid, "mode": mode, "replayed": replay}
    if replay and not problems:
        try:
            cfg = configuration.parse(cfg_json)
            q = Query(mode, cutoff, horizon)
            snap, imported, evidence = jobs.build(conn, q, cfg)
        except configuration.ConfigurationError as exc:
            problems.append(f"cannot rebuild: {exc}")
        else:
            if snap.d_star != d_star:
                problems.append(f"replay D* {snap.d_star} differs from stored {d_star}")
            if store.input_digest(q, evidence) != digest:
                problems.append("replay input digest differs (evidence changed after a settled time)")
            if snap.adjustment_set_hash != adj_hash:
                problems.append("replay adjustment set differs")
            stored = {r[0]: r[11] for r in rows}
            again = {}
            for r in snap.rows:
                ids, wd, _w = store.provenance(snap, imported, r["symbol"])
                again[r["symbol"]] = store.row_hash(r, canonical_json(ids), wd)
            for sym in sorted(set(stored) | set(again)):
                if stored.get(sym) != again.get(sym):
                    problems.append(f"{sym}: replay row differs")
            if _factor_rows([f for fs in snap.factors.values() for f in fs]) != _factor_rows(factors):
                problems.append("replay factor list differs")
            mv = sorted((sym, m.session_from, m.session_to, imported[sym].rows[m.session_from]["close"],
                         imported[sym].rows[m.session_to]["close"], store.MOVE_STATUS[m.state])
                        for sym, ms in snap.moves.items() for m in ms)
            if mv != sorted(moves):
                problems.append("replay move flags differ")
    out["problems"] = problems
    return out


def target_report(conn, build_id):
    from . import targets_store
    problems, labels = [], {}
    with conn.cursor() as cur:
        cur.execute("select mode, cutoff, knowledge_horizon, configuration_id, target_definition_version, code_version, "
                    "build_hash from mf_target_builds where build_id = %s", (build_id,))
        *header, stored = cur.fetchone()
        cur.execute("select reference_snapshot_id, symbol, horizon, state, values_json, resolved_at, adjustment_set, "
                    "row_hash from mf_target_rows where build_id = %s", (build_id,))
        rows = cur.fetchall()
    conn.rollback()
    for ref, sym, h, state, vj, resolved, adj, rh in rows:
        if targets_store.row_hash(ref, sym, h, state, vj, resolved, adj) != rh:
            problems.append(f"snapshot {ref} {sym} h={h}: row_hash does not match the stored row")
        if state != "pending":
            v = json.loads(vj)
            labels[(ref, sym, h)] = (state, v.get("ret"), v.get("volatility"), v.get("volatility_state"))
    if targets_store.build_hash(tuple(header), [(r[0], r[1], r[2], r[7]) for r in rows]) != stored:
        problems.append("build_hash does not match the stored header and rows")
    return {"build_id": build_id, "configuration_id": header[3], "problems": problems}, labels


def disagreements(reports_and_labels):
    """§11: two target builds under one configuration that disagree on a settled label (state, return or
    volatility) are reported, never resolved: a later build never replaces an earlier one. Pending labels are not
    compared. Reported for the owner; not a failure of either build."""
    out = []
    items = sorted(reports_and_labels, key=lambda x: x[0]["build_id"])
    for i, (ra, la) in enumerate(items):
        for rb, lb in items[i + 1:]:
            if ra["configuration_id"] != rb["configuration_id"]:
                continue
            for key in sorted(set(la) & set(lb)):
                if la[key] != lb[key]:
                    out.append({"builds": [ra["build_id"], rb["build_id"]], "reference_snapshot": key[0],
                                "symbol": key[1], "horizon": key[2], "states": [la[key][0], lb[key][0]]})
    return out


def verify(conn, sample=None, margin=MARGIN):
    with conn.cursor() as cur:
        cur.execute("select snapshot_id, mode, cutoff, knowledge_horizon, d_star, configuration_id, input_digest, "
                    "adjustment_set_hash, row_count, recorded_at, result_label, code_version, imported_inputs, "
                    "snapshot_hash from mf_snapshots order by snapshot_id desc"
                    + (" limit %s" % int(sample) if sample else ""))
        headers = cur.fetchall()
        cur.execute("select build_id from mf_target_builds order by build_id desc"
                    + (" limit %s" % int(sample) if sample else ""))
        builds = [r[0] for r in cur.fetchall()]
        drift = calendar_sync.drift(cur)
    conn.rollback()
    snaps = [snapshot_report(conn, h, margin) for h in headers]
    pairs = [target_report(conn, b) for b in builds]
    tgts = [r for r, _l in pairs]
    ok = not drift and all(not r["problems"] for r in snaps + tgts)
    return {"ok": ok, "snapshots": snaps, "target_builds": tgts, "calendar_drift": drift,
            "target_disagreements": disagreements(pairs)}
