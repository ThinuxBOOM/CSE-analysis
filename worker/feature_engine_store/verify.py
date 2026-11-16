"""DE-4 `verify`: recompute every stored hash from the stored columns (read-only). Reports, never repairs:
  - each configuration's id is the SHA-256 of its JSON;
  - each panel's panel_hash; each feature row's row_hash (bound to its panel's hash);
  - each output's output_hash (from output_json) and that its columns agree with its JSON;
  - each run's run_hash (bound to every output's hash) and that the run's outputs are all present."""
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
        hashes = {}
        for r in _rows(cur, f"select panel_id, {cols}, panel_hash from f7_panels order by panel_id"):
            for k in ("newest_period", "anchor_period"):
                r[k] = None if r[k] is None else r[k].isoformat()
            hashes[r["panel_id"]] = r["panel_hash"]
            if codec.panel_hash(r) != r["panel_hash"]:
                problems.append(f"panel {r['panel_id']}: panel_hash does not match its columns")
        for r in _rows(cur, f"select id, panel_id, {', '.join(codec.ROW_COLUMNS)}, row_hash from f7_feature_rows "
                            "order by id"):
            if codec.row_hash(hashes[r["panel_id"]], r) != r["row_hash"]:
                problems.append(f"feature row {r['id']}: row_hash does not match its columns and panel")
        outs = {}
        for r in _rows(cur, "select * from de_outputs order by run_id, engine_id, member"):
            outs.setdefault(r["run_id"], []).append(r)
            if codec.sha(r["output_json"]) != r["output_hash"]:
                problems.append(f"output {r['run_id']}/{r['engine_id']}/{r['member']}: output_hash mismatch")
            js = json.loads(r["output_json"])
            if any(js.get(k) != r[k] for k in ("engine_id", "engine_version", "member", "state", "issuer_id")):
                problems.append(f"output {r['run_id']}/{r['engine_id']}/{r['member']}: columns differ from JSON")
        for r in _rows(cur, f"select run_id, {', '.join(codec.RUN_COLUMNS)}, run_hash from de_runs order by run_id"):
            if codec.run_hash(r, outs.get(r["run_id"], [])) != r["run_hash"]:
                problems.append(f"run {r['run_id']}: run_hash does not match its columns and outputs")
    conn.rollback()
    return problems
