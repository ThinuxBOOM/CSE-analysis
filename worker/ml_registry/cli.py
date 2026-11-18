"""
ML registry command line (Phase 5 §9, M5-6). On the server run it through ops/bin/cse-ml: as the cse-worker OS user
(peer-authenticated as cse_worker), except the owner acts `waive`, `flag` and `promote`, which run as the
owner-delegation login cse-migrator acting as cse_owner. A JSON report goes to stdout. No command contacts CSE or
any network. Training, validation and the test access are done by the research code through `store`; this command
line is for inspection and for the owner's decisions.

    preflight                                    the worker role's security preflight
    status                                       counts, every model's status, unspent test periods, accesses
    verify                                       re-prove every artifact's, configuration's and declaration's hash
    alerts                                       production models whose source version was later rejected
    transition --model ID --to rejected|retired --reason TEXT
                                                 a research decision; validated and production have their own paths
    waive --kind survivorship|mode|reconstructed_evidence --covers JSON --note TEXT
                                                 OWNER decision: a waiver (gates 7 and 11)
    flag --from TS --to TS --note TEXT           OWNER decision: an incident flag over an interval
    promote --model ID --purpose TEXT --list ID[,ID...] --evidence FILE --note TEXT --code-version TEXT
                                                 OWNER decision: gates 9 to 12 and, on a pass, the production copy and
                                                 its designation. The registry supplies the access, its result, the
                                                 labels and the access count; FILE states only the backtest and the
                                                 point-in-time share (store.promotion_evidence)

There is deliberately no delete / reset / repair command: the registry is append-only.

Exit status: 0 ok, 4 PostgreSQL unavailable, 5 refused or failed.
"""
import argparse
import base64
import hashlib
import json
import os
import sys

from ..ops import settings as ops_settings
from . import preflight, store

EXIT_OK, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 4, 5
OWNER_PATH_ROLE = "cse_migrator"
OWNER = ("waive", "flag", "promote")
WRITING = ("transition",)
TABLES = ("ml_configurations", "ml_datasets", "ml_protocols", "ml_test_periods", "ml_runs", "ml_run_events",
          "ml_trials", "ml_artifacts", "ml_fold_models", "ml_models", "ml_model_metrics", "ml_gate_reports",
          "ml_test_accesses", "ml_model_events", "ml_waivers", "ml_incident_flags", "ml_designations",
          "r1_predictions")


class OwnerPathRequired(RuntimeError):
    pass


def parser():
    p = argparse.ArgumentParser(prog="python -m worker.ml_registry")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("preflight", "status", "verify", "alerts"):
        sub.add_parser(name)
    t = sub.add_parser("transition")
    t.add_argument("--model", type=int, required=True)
    t.add_argument("--to", choices=("rejected", "retired"), required=True)
    t.add_argument("--reason", required=True)
    w = sub.add_parser("waive")
    w.add_argument("--kind", choices=("survivorship", "mode", "reconstructed_evidence"), required=True)
    w.add_argument("--covers", required=True)
    w.add_argument("--note", required=True)
    f = sub.add_parser("flag")
    f.add_argument("--from", dest="start", required=True)
    f.add_argument("--to", dest="end", required=True)
    f.add_argument("--note", required=True)
    m = sub.add_parser("promote")
    m.add_argument("--model", type=int, required=True)
    m.add_argument("--purpose", required=True)
    m.add_argument("--list", dest="list_ids", required=True)
    m.add_argument("--evidence", required=True)
    m.add_argument("--note", required=True)
    m.add_argument("--code-version", dest="code_version", required=True)
    return p


def _print(obj):
    print(json.dumps(obj, sort_keys=True, default=str))


def _owner_path(conn):
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"an owner step: connected as {who!r}; run `sudo bash ops/bin/cse-ml ...` "
                                f"(runs as {OWNER_PATH_ROLE})")


def _noted(note, operator):
    return note if not operator else f"{note} (operator {operator})"


def _owner_insert(conn, sql, args):
    _owner_path(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute(sql, args)
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise


def waive(conn, kind, covers, note, operator=None):
    obj = json.loads(covers)
    if not isinstance(obj, dict) or not obj:
        raise ValueError("--covers is a non-empty JSON object naming what the waiver covers")
    return _owner_insert(conn, "insert into ml_waivers (kind, covers_json, note) values (%s, %s, %s) "
                               "returning waiver_id", (kind, store.canonical(obj), _noted(note, operator)))


def flag(conn, start, end, note, operator=None):
    return _owner_insert(conn, "insert into ml_incident_flags (covers, note) values (tstzrange(%s, %s, '[]'), %s) "
                               "returning flag_id", (start, end, _noted(note, operator)))


def promote(conn, model_id, purpose, list_ids, evidence_path, note, code_version, operator=None):
    _owner_path(conn)
    with open(evidence_path, encoding="utf-8") as fh:
        external = json.load(fh)
    if not isinstance(external, dict):
        raise ValueError("the evidence file is a JSON object")
    ids = [int(x) for x in list_ids.split(",") if x.strip()]
    if model_id not in ids:
        raise ValueError("the designation list names the promoted model")
    with conn.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("select output, horizon from ml_models where model_id = %s", (model_id,))
        row = cur.fetchone()
    conn.rollback()
    if row is None:
        raise LookupError(f"no model {model_id}")
    evidence = store.promotion_evidence(conn, model_id, external)
    prod, report = store.promote(conn, model_id, evidence, purpose=purpose, output=row[0], horizon=row[1],
                                 list_ids=ids, note=_noted(note, operator), code_version=code_version)
    return prod, report


def status(conn):
    out = {}
    with conn.cursor() as cur:
        for t in TABLES:
            cur.execute(f"select count(*) from {t}")
            out[t] = cur.fetchone()[0]
        cur.execute("select m.model_id, m.kind, m.output, m.horizon, m.environment, (select status from "
                    "ml_model_events e where e.model_id = m.model_id order by seq desc limit 1) from ml_models m "
                    "order by m.model_id")
        out["models"] = [dict(zip(("model_id", "kind", "output", "horizon", "environment", "status"), r))
                         for r in cur.fetchall()]
    conn.rollback()
    out["unspent_test_periods"] = [list(map(str, p)) for p in store.unspent_periods(conn)]
    out["lifetime_test_accesses"] = store.lifetime_accesses(conn)
    return out


def verify(conn):
    """Every stored hash re-proved in Python, independently of the database's own checks."""
    found = []
    with conn.cursor() as cur:
        cur.execute("select artifact_sha256, artifact_base64, artifact_size from ml_artifacts")
        for h, b64, size in cur.fetchall():
            body = base64.b64decode(b64)
            if hashlib.sha256(body).hexdigest() != h or len(body) != size:
                found.append(f"artifact {h}: content does not match its hash or size")
        cur.execute("select configuration_id, configuration_json from ml_configurations")
        for cid, js in cur.fetchall():
            if store.sha(js) != cid:
                found.append(f"configuration {cid}: id is not the SHA-256 of its JSON")
        cur.execute("select protocol_id, declaration_json, declaration_hash from ml_protocols")
        for pid, js, h in cur.fetchall():
            if store.sha(js) != h:
                found.append(f"protocol {pid}: declaration hash does not match")
        cur.execute("select m.model_id from ml_models m left join ml_artifacts a on a.artifact_sha256 = "
                    "m.artifact_sha256 where a.artifact_sha256 is null")
        found += [f"model {r[0]}: artifact missing" for r in cur.fetchall()]
    conn.rollback()
    return found


def run(args, connect):
    import psycopg2
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    operator = os.environ.get("CSE_OPERATOR")
    try:
        if args.command == "waive":
            _print({"state": "waived", "id": waive(conn, args.kind, args.covers, args.note, operator)})
            return EXIT_OK
        if args.command == "flag":
            _print({"state": "flagged", "id": flag(conn, args.start, args.end, args.note, operator)})
            return EXIT_OK
        if args.command == "promote":
            prod, report = promote(conn, args.model, args.purpose, args.list_ids, args.evidence, args.note,
                                   args.code_version, operator)
            _print({"state": "promoted" if prod else "refused", "production_model_id": prod, "report": report})
            return EXIT_OK if prod else EXIT_REFUSED
        if args.command in WRITING or args.command == "preflight":
            found = preflight.problems(conn)
            if args.command == "preflight":
                _print({"problems": found})
                return EXIT_OK if not found else EXIT_REFUSED
            if found:
                _print({"state": "refused", "reason": "preflight", "problems": found})
                return EXIT_REFUSED
        if args.command == "transition":
            store.transition(conn, args.model, args.to, args.reason)
            _print({"state": args.to, "model_id": args.model})
            return EXIT_OK
        if args.command == "status":
            _print(status(conn))
            return EXIT_OK
        if args.command == "verify":
            found = verify(conn)
            _print({"ok": not found, "problems": found})
            return EXIT_OK if not found else EXIT_REFUSED
        if args.command == "alerts":
            found = store.alerts(conn)
            _print({"alerts": found})
            return EXIT_OK
    except (OwnerPathRequired, store.Refused, LookupError, ValueError, OSError) as exc:
        conn.rollback()
        _print({"state": "refused", "reason": str(exc)})
        return EXIT_REFUSED
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    except psycopg2.Error as exc:                      # a constraint or guard refused it: nothing was written
        conn.rollback()
        _print({"state": "refused", "reason": f"{type(exc).__name__}: {str(exc).strip()[:300]}"})
        return EXIT_REFUSED
    except Exception as exc:                           # noqa: BLE001  anything else: still a refusal, exit 5
        conn.rollback()
        _print({"state": "failed", "reason": f"{type(exc).__name__}: {str(exc).strip()[:300]}"})
        return EXIT_REFUSED
    finally:
        conn.close()
    return EXIT_REFUSED


def main(argv=None):
    args = parser().parse_args(argv)
    settings = ops_settings.load()
    return run(args, lambda: ops_settings.connect(settings))


if __name__ == "__main__":
    sys.exit(main())
