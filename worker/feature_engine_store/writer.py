"""DE-4: write one batch (F7 panels with their feature rows, and optionally one DE run with its outputs) in ONE
transaction as the worker login. Configurations are inserted if absent (content-addressed). The stamp (now(),
session_user) is read first in the same transaction, so every hash binds the recorded_at and created_by the
database stores. Any refusal (constraint or guard) rolls back everything: nothing partial is left."""
from dataclasses import dataclass, field

from . import codec


@dataclass
class PanelBatch:
    panel: object                        # a financial_features.panel.Panel
    rows: list = field(default_factory=list)   # dicts of codec.feature_row's arguments without panel_rec


@dataclass
class RunBatch:
    mode: str
    cutoff: str
    knowledge_horizon: object
    snapshot_id: int
    label_class: str
    outputs: list                        # [(EngineOutput, issuer_id or None)]
    fundamental_coverage: str = "all"


def _bind(o, row_hashes):
    """§7.4: an output's evidence names the F7 feature row it read, by the row_hash written in this batch."""
    subject = o.evidence.get("f7_subject")
    if subject is None:
        return o
    from dataclasses import replace
    ev = {k: v for k, v in o.evidence.items() if k != "f7_subject"}
    if subject not in row_hashes:
        raise ValueError(f"an output cites the feature row of {subject}, which this batch does not write")
    ev["f7_row_hash"] = row_hashes[subject]
    return replace(o, evidence=ev)


def _config(cur, table, cfg):
    cid, js, st = cfg
    cur.execute(f"insert into {table} (configuration_id, configuration_json, status) values (%s, %s, %s) "
                "on conflict (configuration_id) do nothing", (cid, js, st))


def _stamp(cur):
    cur.execute("select now(), session_user")
    return cur.fetchone()


def write(conn, *, f7_config, de_config=None, panels=(), run=None):
    """Returns {"panels": [(panel_id, panel_hash)], "rows": n, "run_id": id or None, "run_hash": h or None}."""
    if run is not None and de_config is None:
        raise ValueError("a DE run needs its DE configuration")
    try:
        with conn.cursor() as cur:
            stamp = _stamp(cur)
            _config(cur, "f7_configurations", f7_config)
            if de_config is not None:
                _config(cur, "de_configurations", de_config)
            out = {"panels": [], "rows": 0, "run_id": None, "run_hash": None}
            row_hashes = {}
            for b in panels:
                rec = codec.panel_record(b.panel, f7_config[0], stamp)
                cols = list(codec.PANEL_COLUMNS) + ["panel_hash"]
                cur.execute(f"insert into f7_panels ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                            "returning panel_id", [rec[c] for c in cols])
                pid = cur.fetchone()[0]
                out["panels"].append((pid, rec["panel_hash"]))
                for r in b.rows:
                    fr = codec.feature_row(rec, **r)
                    cols = ["panel_id"] + list(codec.ROW_COLUMNS) + ["row_hash"]
                    cur.execute(f"insert into f7_feature_rows ({', '.join(cols)}) values "
                                f"({', '.join(['%s'] * len(cols))})", [pid] + [fr[c] for c in cols[1:]])
                    out["rows"] += 1
                    if fr["subject"] in row_hashes:
                        raise ValueError(f"two feature rows for {fr['subject']} in one batch")
                    row_hashes[fr["subject"]] = fr["row_hash"]
            if run is not None:
                outs = [codec.output_record(_bind(o, row_hashes), iss) for o, iss in run.outputs]
                rr = codec.run_record(mode=run.mode, cutoff=run.cutoff, knowledge_horizon=run.knowledge_horizon,
                                      snapshot_id=run.snapshot_id, f7_configuration_id=f7_config[0],
                                      de_configuration_id=de_config[0], label_class=run.label_class, outputs=outs,
                                      stamp=stamp, fundamental_coverage=run.fundamental_coverage)
                cols = list(codec.RUN_COLUMNS) + ["run_hash"]
                cur.execute(f"insert into de_runs ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                            "returning run_id", [rr[c] for c in cols])
                rid = cur.fetchone()[0]
                for o in outs:
                    cur.execute("insert into de_outputs (run_id, engine_id, engine_version, member, issuer_id, state, "
                                "output_json, output_hash) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                                (rid, o["engine_id"], o["engine_version"], o["member"], o["issuer_id"], o["state"],
                                 o["output_json"], o["output_hash"]))
                out["run_id"], out["run_hash"] = rid, rr["run_hash"]
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
