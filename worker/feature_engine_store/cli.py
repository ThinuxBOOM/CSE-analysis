"""
F7 / DE command line (Phase 4 §10). On the server run it through ops/bin/cse-engines: as the cse-worker OS user
(peer-authenticated as cse_worker), except `designate`, which runs as the owner-delegation login cse-migrator acting
as cse_owner. A JSON report goes to stdout. No command contacts CSE or any network.

    preflight                                    the worker role's security preflight
    register-configuration                       insert the F7 and DE configurations if absent (provisional)
    designate --kind f7|de --configuration ID --note TEXT
                                                 OWNER decision: the canonical configuration
    run --snapshot ID [--f8-configuration ID]    panels, features and one engine run for a stored MF snapshot
    verify                                       re-prove every stored hash (read-only)
    status                                       counts and the latest runs

There is deliberately no delete / reset / repair command: the storage is append-only.

Exit status: 0 ok, 4 PostgreSQL unavailable, 5 refused or failed.
"""
import argparse
import json
import os
import sys

from ..financial_asof import Refused
from ..ops import settings as ops_settings
from . import codec, jobs, loader, preflight, verify

EXIT_OK, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 4, 5
OWNER_PATH_ROLE = "cse_migrator"
WRITING = ("register-configuration", "run")


class OwnerPathRequired(RuntimeError):
    pass


def parser():
    p = argparse.ArgumentParser(prog="python -m worker.feature_engine_store")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("register-configuration")
    d = sub.add_parser("designate")
    d.add_argument("--kind", choices=("f7", "de"), required=True)
    d.add_argument("--configuration", required=True)
    d.add_argument("--note", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshot", type=int, required=True)
    r.add_argument("--f8-configuration", dest="f8_configuration")
    sub.add_parser("verify")
    sub.add_parser("status")
    return p


def _print(obj):
    print(json.dumps(obj, sort_keys=True, default=str))


def _owner_path(conn):
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"an owner step: connected as {who!r}; run `sudo bash ops/bin/cse-engines ...` "
                                f"(runs as {OWNER_PATH_ROLE})")


def designate(conn, kind, configuration_id, note, operator=None):
    _owner_path(conn)
    text = note if not operator else f"{note} (operator {operator})"
    with conn.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute(f"insert into {kind}_designations (purpose, configuration_id, note) values ('canonical', %s, %s) "
                    "returning id", (configuration_id, text))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def register(conn):
    f7, de = codec.f7_configuration(), codec.de_configuration()
    with conn.cursor() as cur:
        for t, (cid, js, st) in (("f7_configurations", f7), ("de_configurations", de)):
            cur.execute(f"insert into {t} (configuration_id, configuration_json, status) values (%s, %s, %s) "
                        "on conflict (configuration_id) do nothing", (cid, js, st))
    conn.commit()
    return {"f7_configuration_id": f7[0], "de_configuration_id": de[0]}


def status(conn):
    out = {}
    with conn.cursor() as cur:
        for t in ("f7_configurations", "de_configurations", "f7_designations", "de_designations", "f7_panels",
                  "f7_feature_rows", "de_runs", "de_outputs"):
            cur.execute(f"select count(*) from {t}")
            out[t] = cur.fetchone()[0]
        cur.execute("select run_id, mode, cutoff, snapshot_id, recorded_at from de_runs order by run_id desc limit 5")
        out["latest_runs"] = [dict(zip(("run_id", "mode", "cutoff", "snapshot_id", "recorded_at"), r))
                              for r in cur.fetchall()]
    conn.rollback()
    return out


def run(args, connect):
    import psycopg2
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    try:
        if args.command == "designate":
            _print({"state": "designated", "kind": args.kind, "configuration_id": args.configuration,
                    "id": designate(conn, args.kind, args.configuration, args.note, os.environ.get("CSE_OPERATOR"))})
            return EXIT_OK
        if args.command in WRITING or args.command == "preflight":
            found = preflight.problems(conn)
            if args.command == "preflight":
                _print({"problems": found})
                return EXIT_OK if not found else EXIT_REFUSED
            if found:
                _print({"state": "refused", "reason": "preflight", "problems": found})
                return EXIT_REFUSED
        if args.command == "register-configuration":
            _print({"state": "registered", **register(conn)})
            return EXIT_OK
        if args.command == "run":
            out = jobs.run(conn, args.snapshot, f8_configuration_id=args.f8_configuration)
            _print({"state": "stored", "snapshot_id": args.snapshot, "panels": len(out["panels"]),
                    "feature_rows": out["rows"], "run_id": out["run_id"], "run_hash": out["run_hash"]})
            return EXIT_OK
        if args.command == "verify":
            found = verify.report(conn)
            _print({"ok": not found, "problems": found})
            return EXIT_OK if not found else EXIT_REFUSED
        if args.command == "status":
            _print(status(conn))
            return EXIT_OK
    except (OwnerPathRequired, Refused, loader.SnapshotRefused, LookupError, ValueError) as exc:
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
