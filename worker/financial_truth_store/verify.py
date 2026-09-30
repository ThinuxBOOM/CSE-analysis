"""
`verify` (docs/F6.4_DESIGN.md sections 6.6, 17.6, 19): re-proves everything stored, read-only, in Python.

- every envelope hashes to its stored hash (E1-E6);
- E3 / E4 / E5 decode into F6.3 objects and re-encode byte-exactly;
- the section 11.6 invariant (EDI-1 to EDI-5) holds for every stored decomposition (the Python mirror of the
  database checks, re-run on the rows as read back; after a pg_restore the insert-time guards did not run);
- seals (child counts) and chains (sequence / previous) are intact;
- a sample of validation runs is reproduced from its referenced F5 inputs (with the STORED issuer decision and
  publication instant) and must give identical keys and hashes.

A mismatch is reported, never repaired. The reader role can run it (it needs no EXECUTE grant).
"""
import json
import random

from . import codec
from .jobs import compute_validation


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _by(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def verify(conn, sample=3, seed=0):
    problems, counts = [], {}
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read")
        cur.execute("set transaction read only")
        t1s = _rows(cur, "select * from financial_validation_runs order by validation_run_key")
        t2 = _by(_rows(cur, "select * from financial_candidate_validations"), "validation_run_key")
        t3 = _by(_rows(cur, "select * from financial_op1_records"), "validation_run_key")
        t4 = {f["ef_key"]: f for f in _rows(cur, "select * from financial_economic_facts")}
        t5 = _rows(cur, "select * from financial_source_observations")
        t6 = _by(_rows(cur, "select * from financial_so_members"), "so_key")
        t7 = _by(_rows(cur, "select * from financial_so_comparisons"), "so_key")
        t8 = _rows(cur, "select * from financial_reconciliation_configurations")
        t12 = _rows(cur, "select * from financial_reconciliation_batches")
        t13 = _rows(cur, "select * from financial_reconciliation_records")
        t14 = _by(_rows(cur, "select * from financial_reconciliation_inputs"), "record_id")
        t15 = _by(_rows(cur, "select * from financial_reconciliation_comparisons"), "record_id")
        t16 = _by(_rows(cur, "select * from financial_reconciliation_batch_results"), "batch_id")
        reported = {}                   # F5 run -> {candidate id: ReportedValue JSON of the F5 row}
        for c in _rows(cur, "select id, run_id, raw_value, parsed_value, representation_class, printed_decimals, "
                            "sign_as_printed, reported_scale, scale_basis, reported_currency, value_type from "
                            "financial_fact_candidates where run_id in (select f5_run_id from financial_validation_runs)"):
            reported.setdefault(str(c["run_id"]), {})[c["id"]] = codec.candidate_reported_json(c)
        link_issuer = {r["id"]: r["issuer_id"] for r in _rows(
            cur, "select id, issuer_id from filing_issuer_links where id in (select issuer_link_id from "
                 "financial_validation_runs)")}
        so_by_run = _by(t5, "validation_run_key")
        counts.update(validation_runs=len(t1s), source_observations=len(t5), facts=len(t4), configurations=len(t8),
                      batches=len(t12), records=len(t13))
        # envelopes: hashes
        for what, rows, env, h in (("E1", t1s, "output_json", "output_hash"),
                                   ("E2", [r for rs in t2.values() for r in rs], "output_json", "output_hash"),
                                   ("E3", t5, "so_json", "output_hash"), ("E4", t13, "result_json", "output_hash"),
                                   ("E5", t8, "configuration_json", "configuration_id"),
                                   ("E6", t12, "output_json", "output_hash")):
            for r in rows:
                if codec.sha256_hex(r[env]) != r[h]:
                    problems.append(f"{what} hash mismatch: {r[h]}")
        # envelopes: decode / re-encode
        for s in t5:
            try:
                codec.decode_source_observation(s["so_json"], s["output_hash"])
            except Exception as exc:
                problems.append(f"E3 {s['so_key']}: {exc}")
        for r in t13:
            try:
                codec.decode_result(r["result_json"], r["output_hash"])
            except Exception as exc:
                problems.append(f"E4 {r['record_id']}: {exc}")
        for c in t8:
            try:
                codec.decode_configuration(c["configuration_json"])
            except Exception as exc:
                problems.append(f"E5 {c['configuration_id']}: {exc}")
            problems.extend(f"configuration {c['configuration_id']}: {p}" for p in codec.check_configuration(c))
        # section 11.6 for every validation run (with its SOs) and every record and batch
        for vr in t1s:
            k = vr["validation_run_key"]
            sos = so_by_run.get(k, [])
            d = {"T1": vr, "T2": t2.get(k, []), "T3": t3.get(k, []), "T5": sos,
                 "T4": [t4[s["ef_key"]] for s in sos if s["ef_key"] in t4],
                 "T6": [m for s in sos for m in t6.get(s["so_key"], [])],
                 "T7": [c for s in sos for c in t7.get(s["so_key"], [])]}
            problems.extend(f"validation run {k}: {p}" for p in codec.check_validation(
                d, reported.get(str(vr["f5_run_id"]), {}), link_issuer.get(vr["issuer_link_id"])))
        so_rows = {s["so_key"]: s for s in t5}
        cfg_version = {c["configuration_id"]: c["reconciliation_version"] for c in t8}
        for r in t13:
            ins = t14.get(r["record_id"], [])
            sos = {i["so_key"]: {"row": so_rows[i["so_key"]],
                                 "members": {m["candidate_validation_key"]: m for m in t6.get(i["so_key"], [])}}
                   for i in ins if i["so_key"] in so_rows}
            problems.extend(f"record {r['record_id']}: {p}"
                            for p in codec.check_record(r, ins, t15.get(r["record_id"], []), t4.get(r["ef_key"]), sos,
                                                        cfg_version.get(r["configuration_id"])))
        hashes = {r["record_id"]: r["output_hash"] for r in t13}
        for b in t12:
            problems.extend(f"batch {b['batch_id']}: {p}"
                            for p in codec.check_batch(b, t16.get(b["batch_id"], []), hashes))
            if sum(1 for r in t13 if r["batch_id"] == b["batch_id"]) != b["records_appended"]:
                problems.append(f"batch {b['batch_id']}: seal (records appended)")
        # chains
        for key, rows, seq, prev, rid in (
                ("record", _by(t13, "ef_key"), "sequence", "previous_record_id", "record_id"),
                ("batch", _by(t12, "issuer_id"), "sequence", "previous_batch_id", "batch_id")):
            for group in rows.values():
                for chain in _by(group, "configuration_id").values():
                    chain.sort(key=lambda x: x[seq])
                    for i, x in enumerate(chain):
                        if x[seq] != i + 1 or x[prev] != (chain[i - 1][rid] if i else None):
                            problems.append(f"{key} chain broken at {x[rid]}")
        # reproduction of a sample, with the stored issuer decision and publication instant
        rng = random.Random(seed)
        picked = rng.sample(t1s, min(sample, len(t1s))) if sample else []
        reproduced = 0
        for vr in picked:
            got, sos, *_ = compute_validation(cur, vr["f5_run_id"], link_id=vr["issuer_link_id"],
                                              uploaded_at=vr["publication_uploaded_at"])
            if (got.key, got.input_hash, got.output_hash) != (vr["validation_run_key"], vr["input_hash"],
                                                              vr["output_hash"]):
                problems.append(f"reproduction of {vr['validation_run_key']} differs")
                continue
            stored_c = {r["candidate_validation_key"]: (r["input_hash"], r["output_hash"])
                        for r in t2.get(vr["validation_run_key"], [])}
            if stored_c != {r.key: (r.input_hash, r.output_hash) for r in got.candidates}:
                problems.append(f"reproduction of {vr['validation_run_key']}: candidate hashes differ")
            stored_s = {s["so_key"]: s["output_hash"] for s in so_by_run.get(vr["validation_run_key"], [])}
            if stored_s != {o.so_key: o.output_hash for o in sos}:
                problems.append(f"reproduction of {vr['validation_run_key']}: SO hashes differ")
            reproduced += 1
        counts["reproduced"] = reproduced
    conn.rollback()
    return {"ok": not problems, "problems": problems, "counts": counts}


def dumps(report):
    return json.dumps(report, indent=2, sort_keys=True, default=str)
