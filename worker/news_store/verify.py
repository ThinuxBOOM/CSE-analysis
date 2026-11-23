"""
`verify` (NS-7): every stored snapshot's rows reproduce their row hashes and the snapshot hash, its configuration
reproduces its id, and a settled snapshot (its governing time T or H plus a margin before it was written) is rebuilt
from the stored inputs with the stored session sequence and must be identical. Every stored result's alias-set hash
is the hash of its recorded alias context.
"""
import json
from datetime import date, timedelta

from worker.market_features.modes import Query
from worker.news_sentiment import aliases as al
from worker.news_sentiment import snapshot as sn

from . import store

MARGIN = timedelta(minutes=15)


def _rows(conn, sql, args=()):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.rollback()


def snapshot_report(conn, snapshot_id, margin=MARGIN):
    (mode, cutoff, horizon, d_star, sessions_json, cid, active, procs, imported, counts, stored_hash,
     recorded) = _rows(conn, "select mode, cutoff, knowledge_horizon, d_star, sessions_json, configuration_id, "
                             "active_sources_json, processor_versions_json, imported_inputs, counts_json, "
                             "snapshot_hash, recorded_at from ns_snapshots where snapshot_id = %s", (snapshot_id,))[0]
    problems = []
    cfg_json = _rows(conn, "select configuration_json from ns_configurations where configuration_id = %s", (cid,))[0][0]
    cfg = store.config_from_json(cfg_json)
    if cfg.configuration_id() != cid:
        problems.append("the stored configuration does not reproduce its id")
    rows = _rows(conn, "select subject, state, features_json, stories_json, row_hash from ns_snapshot_rows where "
                       "snapshot_id = %s order by subject", (snapshot_id,))
    snap = sn.Snapshot(mode, cutoff, horizon, d_star, cid, json.loads(active), json.loads(procs), imported,
                       json.loads(counts), [])
    for subject, state, fj, sj, h in rows:
        r = sn.Row(subject, state, {k: tuple(v) for k, v in json.loads(fj).items()}, json.loads(sj))
        snap.rows.append(r)
        if sn.row_hash(snap, r) != h:
            problems.append(f"{subject}: row_hash does not match the stored row")
    for r, (_s, _st, _f, _j, h) in zip(snap.rows, rows):
        r.row_hash = h
    sessions = [date.fromisoformat(x) for x in json.loads(sessions_json)]
    if store.snapshot_hash(snap, sessions) != stored_hash:
        problems.append("snapshot_hash does not match the stored rows, counts and sessions")
    governing = cutoff if mode == "KNOWN" else horizon
    replay = governing is not None and governing + margin <= recorded
    if replay and not problems:
        q = Query(mode, cutoff, horizon)
        issuers = [r.subject for r in snap.rows if r.subject != "market"]
        loaded = store.load(conn, q)
        again = sn.build(q, sessions, store.articles_at(loaded, q, cfg), loaded["sources"], loaded["runs"], cfg,
                         issuers)
        if store.snapshot_hash(again, sessions) != stored_hash:
            problems.append("replay differs (evidence changed after a settled time)")
    return {"snapshot_id": snapshot_id, "mode": mode, "replayed": replay, "problems": problems}


def results_report(conn):
    problems = []
    for rid, ids_json, h, rj in _rows(conn, "select result_id, alias_ids_json, alias_set_hash, result_json from "
                                            "ns_article_results"):
        ids = json.loads(ids_json)
        if al.alias_set_hash(ids) != h:
            problems.append(f"result {rid}: alias_set_hash is not the hash of its alias context")
        if sorted(json.loads(rj).get("context_alias_ids", [])) != sorted(ids):
            problems.append(f"result {rid}: the result's own alias context differs from its alias_ids_json")
    return problems


def verify(conn, margin=MARGIN):
    snaps = [snapshot_report(conn, sid, margin) for (sid,) in _rows(conn, "select snapshot_id from ns_snapshots "
                                                                           "order by snapshot_id")]
    res = results_report(conn)
    return {"ok": not res and all(not s["problems"] for s in snaps), "snapshots": snaps, "results": res}
