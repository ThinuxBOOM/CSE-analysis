"""
`explain` (Phase 3 §13.1): one stored snapshot row, explained down to its evidence. It shows the stored values, the
security's factors and move flags, and re-derives from the append-only import tables the mf_history_bars ids behind
its D* bar and its windows, checking them against the stored `dstar_observation_ids` and `window_digest`. Read-only.
"""
import json

from worker.market_features.canonical import canonical_json
from worker.market_features.modes import Query

from . import configuration, jobs, store


def explain(conn, snapshot_id, symbol):
    with conn.cursor() as cur:
        cur.execute("select mode, cutoff, knowledge_horizon, configuration_id, d_star from mf_snapshots "
                    "where snapshot_id = %s", (snapshot_id,))
        head = cur.fetchone()
        if head is None:
            raise LookupError(f"no snapshot {snapshot_id}")
        cur.execute("select eligibility, features_json, px_ref, ref_session, stale_sessions, adj_ref, "
                    "dstar_observation_ids, window_digest from mf_snapshot_rows where snapshot_id = %s and symbol = %s",
                    (snapshot_id, symbol))
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"snapshot {snapshot_id} has no row for {symbol}")
        cur.execute("select kind, ex_date, numerator, denominator, status, statement_ids from mf_snapshot_factors "
                    "where snapshot_id = %s and symbol = %s order by ex_date", (snapshot_id, symbol))
        factors = [{"kind": k, "ex_date": d, "factor": f"{n}/{m}", "status": st, "statements": json.loads(ids)}
                   for k, d, n, m, st, ids in cur.fetchall()]
        cur.execute("select session_from, session_to, close_from, close_to, status from mf_snapshot_move_flags "
                    "where snapshot_id = %s and symbol = %s order by session_from", (snapshot_id, symbol))
        moves = [dict(zip(("session_from", "session_to", "close_from", "close_to", "status"), m))
                 for m in cur.fetchall()]
        cfg = configuration.load(cur, head[3])
    conn.rollback()
    mode, cutoff, horizon, _cid, d_star = head
    el, fj, px, rs, stale, adj, dj, wd = row
    snap, imported, _ev = jobs.build(conn, Query(mode, cutoff, horizon), cfg)
    ids, digest, window = store.provenance(snap, imported, symbol)          # MF-8: imported and captured ids
    return {"snapshot_id": snapshot_id, "symbol": symbol, "mode": mode, "d_star": d_star, "eligibility": el,
            "px_ref": px, "ref_session": rs, "stale_sessions": stale, "adj_ref": adj,
            "features": json.loads(fj), "factors": factors, "moves": moves,
            "dstar_observation_ids": json.loads(dj), "window_observation_ids": window,
            "provenance_ok": canonical_json(ids) == dj and digest == wd}
