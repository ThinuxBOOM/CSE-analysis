"""
M5-2 part 2: read the stored rows of Phases 3 and 4 into the builder's inputs (read-only, one repeatable-read
transaction). ML reads only `mf_snapshots` and their rows, factors aside, `mf_target_rows`, `f7_feature_rows` and
`de_outputs` (§3), plus the import headers for survivorship. No raw observation, economic fact or F8 result.

Per snapshot in the manifest's range and mode:
  - the DE run of that snapshot under the manifest's F7 and DE configurations (the latest, if it was run twice); its
    outputs give each line's engine scores and its industry group, and its evidence names the F7 rows the run read,
    so the line's F7 features are exactly those rows (the issuer's row and the line's own);
  - survivorship: `complete` for a snapshot that read no imported history; otherwise the worst survivorship of the
    import batches MF admits (raw, `row_absent`) recorded by the snapshot's governing time (T in KNOWN, H in
    AVAILABLE) that hold a bar within the 400 days before the session, the reach of its longest feature window.
    This reads the import headers and bar dates, never a price: a recorded departure from §3 (C-0089) until MF
    stores the survivorship it admitted on the snapshot itself;
  - the header of the manifest's one target build, and its labels for the chosen snapshots.
A snapshot with no DE run under the manifest's configurations is loaded without F7 and DE inputs and marked, so the
builder records the loss (first M5-2 audit, M4).
"""
import json
from datetime import timedelta, timezone
from decimal import Decimal

from . import dataset as ds

FEATURE_WINDOW_DAYS = 400
GROUP_ENGINES = ("fund_leverage", "fund_bank_strength", "fund_growth", "fund_growth_bank")


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _triples(obj, reason_index):
    """{fid: {value, state, reason or reasons}} -> {fid: (Decimal or None, state, reason)}."""
    out = {}
    for fid, x in obj.items():
        v = x.get("value")
        if "reasons" in x:
            rs = x.get("reasons") or []
            reason = rs[0][reason_index] if rs and len(rs[0]) > reason_index else None
        else:
            reason = x.get("reason")
        out[fid] = (None if v is None else Decimal(str(v)), x.get("state"), reason)
    return out


def _survivorship(cur, snap):
    if not snap["imported_inputs"]:
        return "complete"
    governing = snap["knowledge_horizon"] if snap["mode"] == "AVAILABLE" else snap["cutoff"]
    rows = _rows(cur, "select distinct i.survivorship from mf_history_imports i where i.price_basis = 'raw' and "
                      "i.no_trade_convention = 'row_absent' and i.recorded_at <= %s and exists (select 1 from mf_history_bars b where b.import_id = i.import_id "
                      "and b.trade_date between %s and %s)",
                 (governing, snap["d_star"] - timedelta(days=FEATURE_WINDOW_DAYS), snap["d_star"]))
    found = [r["survivorship"] for r in rows]
    return max(found, key=ds.SURVIVORSHIP_ORDER.index) if found else "unknown"


def _detail(engine_id, js):
    """(full, parts) of one scored output: whether every component of the engine's registry entry was present, and
    each present component's share of the score, contribution / total weight of the present components (DE-1 §7.3),
    so that the shares add up to the score."""
    from worker.engines import registry
    engine = registry.BY_ID.get(engine_id)
    present = set(js.get("components_present") or [])
    comps = [c for c in js.get("components") or [] if c.get("feature_id") in present and "contribution" in c]
    weight = sum(Decimal(str(c["weight"])) for c in comps)
    parts = {c["feature_id"]: Decimal(c["contribution"]) / weight for c in comps} if weight else {}
    full = engine is not None and present == {c.feature_id for c in engine.components if c.weight > 0}
    return full, parts


def _run(cur, sid, manifest):
    runs = _rows(cur, "select run_id from de_runs where snapshot_id = %s and f7_configuration_id = %s and "
                      "de_configuration_id = %s order by run_id desc limit 1",
                 (sid, manifest.f7_configuration_id, manifest.de_configuration_id))
    return runs[0]["run_id"] if runs else None


def _de_and_f7(cur, run_id, sid, mf_hashes):
    """{symbol: (de dict, industry group, F7In or None, link state)} for one DE run. mf_hashes: {symbol: row_hash} of
    the snapshot; a line's own F7 row must have been computed on that MF row."""
    outs = _rows(cur, "select engine_id, member, issuer_id, state, output_json, output_hash from de_outputs "
                      "where run_id = %s", (run_id,))
    f7 = {r["row_hash"]: r for r in _rows(cur, "select r.subject, r.level, r.features_json, r.eligibility_json, "
                                               "r.row_hash, r.fundamental_coverage, r.mf_row_hash from f7_feature_rows r where "
                                               "r.snapshot_id = %s", (sid,))}
    by = {}
    for o in outs:
        js = json.loads(o["output_json"])
        d = by.setdefault(o["member"], {"de": {}, "group": None, "f7": set(), "issuer": o["issuer_id"],
                                        "link": None})
        score = js.get("score")
        d["de"][o["engine_id"]] = (None if score is None else Decimal(str(score)), o["state"], o["output_hash"])
        if score is not None:
            d.setdefault("detail", {})[o["engine_id"]] = _detail(o["engine_id"], js)
        group = (js.get("universe") or {}).get("group")
        if o["engine_id"] in GROUP_ENGINES and o["state"] != "not_applicable" and group:
            d["group"] = group
        excluded = (js.get("evidence") or {}).get("excluded")
        if o["issuer_id"] is None and excluded in ("issuer_link_absent", "issuer_link_conflict"):
            d["link"] = excluded
        h = (js.get("evidence") or {}).get("f7_row_hash")
        if h is not None:
            if h not in f7:
                raise ds.Refused("f7_row_missing", f"run {run_id} cites an F7 row that is not stored")
            d["f7"].add(h)
        if o["issuer_id"] is not None:
            d["issuer"] = o["issuer_id"]
    out = {}
    for sym, d in by.items():
        f7in = None
        if d["f7"]:
            feats, elig, cov = {}, None, "all"
            for h in sorted(d["f7"], key=lambda x: f7[x]["level"]):           # issuer first, the line's own after
                r = f7[h]
                if r["level"] == "security" and (r["subject"] != sym or r["mf_row_hash"] != mf_hashes.get(sym)):
                    raise ds.Refused("f7_row_mismatch", f"{sym}: its F7 row was not computed on this MF row")
                feats.update(_triples(json.loads(r["features_json"]), 1))
                if r["level"] == "issuer":
                    elig = json.loads(r["eligibility_json"]).get("result")
                if r["fundamental_coverage"] == "captured_only":
                    cov = "captured_only"
            f7in = ds.F7In(feats, elig, tuple(sorted(d["f7"])), cov)
        out[sym] = (d["de"], d["group"], f7in, d["link"], d.get("detail", {}))
    return out


def load(conn, manifest):
    """(snapshots [SnapshotIn], labels [LabelIn], target build TargetBuildIn) for a manifest. Read-only."""
    import psycopg2.extensions as ext
    if conn.autocommit or conn.info.transaction_status != ext.TRANSACTION_STATUS_IDLE:
        raise ds.Refused("connection", "the loader reads in its own read-only transaction")
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read, read only")
            snaps = _rows(cur, "select snapshot_id, mode, result_label, cutoff, knowledge_horizon, d_star, "
                               "configuration_id, imported_inputs from mf_snapshots where mode in ('KNOWN', "
                               "'AVAILABLE') and configuration_id = %s and d_star between %s and %s "
                               "order by snapshot_id",
                          (manifest.mf_configuration_id, manifest.date_from, manifest.date_to))
            out = []
            for s in snaps:
                rows = _rows(cur, "select symbol, eligibility, features_json, row_hash from mf_snapshot_rows "
                                  "where snapshot_id = %s order by symbol", (s["snapshot_id"],))
                run_id = _run(cur, s["snapshot_id"], manifest)
                extra = {} if run_id is None else _de_and_f7(cur, run_id, s["snapshot_id"],
                                                              {r["symbol"]: r["row_hash"] for r in rows})
                lines = {}
                for r in rows:
                    de, group, f7in, link, detail = extra.get(r["symbol"], ({}, None, None, None, {}))
                    lines[r["symbol"]] = ds.LineIn(r["eligibility"], _triples(json.loads(r["features_json"]), 1),
                                                   r["row_hash"], f7in, de, group, link, detail)
                out.append(ds.SnapshotIn(
                    s["snapshot_id"], s["d_star"], s["cutoff"].astimezone(timezone.utc), s["mode"],
                    None if s["knowledge_horizon"] is None else s["knowledge_horizon"].astimezone(timezone.utc),
                    s["configuration_id"], s["result_label"], _survivorship(cur, s), lines,
                    manifest.f7_configuration_id if run_id is not None else None,
                    manifest.de_configuration_id if run_id is not None else None, de_run=run_id is not None,
                    imported=bool(s["imported_inputs"])))
            ids = [s.snapshot_id for s in out]
            head = _rows(cur, "select build_id, mode, knowledge_horizon, configuration_id, target_definition_version "
                              "from mf_target_builds where build_id = %s", (manifest.target_build_id,))
            if not head:
                raise ds.Refused("target_build", f"no target build {manifest.target_build_id}")
            h = head[0]
            build = ds.TargetBuildIn(h["build_id"], h["mode"], None if h["knowledge_horizon"] is None else
                                     h["knowledge_horizon"].astimezone(timezone.utc), h["configuration_id"],
                                     h["target_definition_version"])
            labels = [ds.LabelIn(r["build_id"], r["reference_snapshot_id"], r["symbol"], r["horizon"], r["state"],
                                 json.loads(r["values_json"]), r["resolved_at"], r["row_hash"])
                      for r in _rows(cur, "select build_id, reference_snapshot_id, symbol, horizon, state, values_json, "
                                          "resolved_at, row_hash from mf_target_rows where build_id = %s and "
                                          "reference_snapshot_id = any(%s)", (manifest.target_build_id, ids))]
        return out, labels, build
    finally:
        conn.rollback()
