"""DE-4 `verify`: recompute every stored hash from the stored columns (read-only). Reports, never repairs:
  - each configuration's id is the SHA-256 of its JSON;
  - each panel's panel_hash; each feature row's row_hash (bound to its panel's hash);
  - each output's output_hash (from output_json) and that its columns agree with its JSON;
  - each run's run_hash (bound to every output's hash) and that the run's outputs are all present;
  - every F7 row an output cites (evidence f7_row_hash) exists, belongs to the run's MF snapshot, is the row of the
    output's subject (the issuer for an issuer engine, the line for fund_valuation_eps) and comes from the panel of
    the cited F8 result; and every feature row of a snapshot that has runs is cited by one of them. Since every output's hash
    is bound into run_hash, deleting, replacing or re-hashing a feature row or a panel is noticed (first DE-4
    audit B-1)."""
import json

from . import codec


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def report(conn):
    problems = []
    with conn.cursor() as cur:
        for t in ("f7_configurations", "de_configurations"):
            for r in _rows(cur, f"select configuration_id, configuration_json from {t}"):
                if codec.sha(r["configuration_json"]) != r["configuration_id"]:
                    problems.append(f"{t} {r['configuration_id']}: id is not the hash of its JSON")
        cols = ", ".join(codec.PANEL_COLUMNS)
        hashes, f8_of = {}, {}
        for r in _rows(cur, f"select panel_id, {cols}, panel_hash from f7_panels order by panel_id"):
            f8_of[r["panel_id"]] = r["f8_result_hash"]
            for k in ("newest_period", "anchor_period"):
                r[k] = None if r[k] is None else r[k].isoformat()
            hashes[r["panel_id"]] = r["panel_hash"]
            if codec.panel_hash(r) != r["panel_hash"]:
                problems.append(f"panel {r['panel_id']}: panel_hash does not match its columns")
        by_hash = {}
        for r in _rows(cur, f"select id, panel_id, {', '.join(codec.ROW_COLUMNS)}, row_hash from f7_feature_rows "
                            "order by id"):
            if codec.row_hash(hashes[r["panel_id"]], r) != r["row_hash"]:
                problems.append(f"feature row {r['id']}: row_hash does not match its columns and panel")
            by_hash.setdefault(r["row_hash"], []).append(r)
        outs = {}
        for r in _rows(cur, "select * from de_outputs order by run_id, engine_id, member"):
            outs.setdefault(r["run_id"], []).append(r)
            if codec.sha(r["output_json"]) != r["output_hash"]:
                problems.append(f"output {r['run_id']}/{r['engine_id']}/{r['member']}: output_hash mismatch")
            js = json.loads(r["output_json"])
            if any(js.get(k) != r[k] for k in ("engine_id", "engine_version", "member", "state", "issuer_id")):
                problems.append(f"output {r['run_id']}/{r['engine_id']}/{r['member']}: columns differ from JSON")
        cited = {}
        for r in _rows(cur, f"select run_id, {', '.join(codec.RUN_COLUMNS)}, run_hash from de_runs order by run_id"):
            if codec.run_hash(r, outs.get(r["run_id"], [])) != r["run_hash"]:
                problems.append(f"run {r['run_id']}: run_hash does not match its columns and outputs")
            found, c = _citations(r, outs.get(r["run_id"], []), by_hash, f8_of)
            problems += found
            cited.setdefault(r["snapshot_id"], set()).update(c)
        for h, xs in by_hash.items():                 # a stored feature row no run of its snapshot cites
            for x in xs:
                if x["snapshot_id"] in cited and h not in cited[x["snapshot_id"]]:
                    problems.append(f"feature row {x['id']}: cited by no run of snapshot {x['snapshot_id']}")
    conn.rollback()
    return problems


def _citations(run, outs, by_hash, f8_of):
    """B-1: the F7 rows a run's outputs cite exist and are the right rows. Returns (problems, cited hashes)."""
    problems, cited = [], set()
    for o in outs:
        ev = json.loads(o["output_json"]).get("evidence") or {}
        h = ev.get("f7_row_hash")
        if h is None:
            continue
        where = f"output {o['run_id']}/{o['engine_id']}/{o['member']}"
        found = [x for x in by_hash.get(h, []) if x["snapshot_id"] == run["snapshot_id"]]
        if len(found) != 1:
            problems.append(f"{where}: cites F7 row {h[:12]}, which is {'missing' if not found else 'not unique'}")
            continue
        x = found[0]
        cited.add(h)
        subject = o["member"] if o["engine_id"] == "fund_valuation_eps" else o["issuer_id"]
        if x["subject"] != subject:
            problems.append(f"{where}: cites the F7 row of {x['subject']}, not of {subject}")
        if ev.get("f8_result_hash") is not None and f8_of.get(x["panel_id"]) != ev["f8_result_hash"]:
            problems.append(f"{where}: the cited F7 row is not from the panel of the cited F8 result")
    return problems, cited
