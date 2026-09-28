"""
Restore check (P1): proves a local dump restores into a FRESH PostgreSQL cluster and reproduces the source exactly.

    python -m worker.ops.restore_check [--dump DUMP_DIR] [--keep]      # default: the newest dump

1. verify the dump directory (manifest sidecar, SHA-256 and size of every file, pg_restore --list);
2. initdb a throwaway cluster (worker/ops/ephemeral_pg.py: own data dir under <backup root>/restore-scratch, private
   socket, no TCP) - the production cluster is never written to and needs no CREATEDB privilege;
3. restore globals (roles) then the database with pg_restore --create --exit-on-error (owners and ACLs included);
   the throwaway cluster's bootstrap superuser gets the SOURCE's bootstrap superuser name (from the manifest), as
   PostgreSQL 16+ accepts "GRANT role ... GRANTED BY <bootstrap superuser>" only from that role;
4. recompute every table's row count + content digest, the trigger list and the migration ledger and compare them
   with the manifest (which was computed inside the dump's own snapshot);
5. check security invariants on the restored copy (roles, append-only triggers, worker privileges);
6. stop and delete the throwaway cluster; record the result in ops.backup_runs (restore_check) + status file.
"""
import argparse
import json
import os
import sys

from . import backup as ops_backup, dbhash, ledger as ops_ledger, settings as ops_settings
from .ephemeral_pg import EphemeralCluster, EphemeralError
from .redact import Redactor

TOOL_VERSION = "p1.restore_check.1"
ROLES = {"cse_owner": False, "cse_migrator": True, "cse_worker": True, "cse_reader": False, "cse_backup": True}
PROTECTED = ("raw_market_observations", "raw_index_observations", "report_filing_observations",
             "report_document_classifications", "report_statement_periods", "report_classification_evidence",
             "financial_fact_candidates", "issuer_identifier_observations")


def security_invariants(cur):
    problems = []
    cur.execute("select rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
                "from pg_roles where rolname = any(%s)", (list(ROLES),))
    roles = {r[0]: r[1:] for r in cur.fetchall()}
    for name, login in ROLES.items():
        if name not in roles:
            problems.append(f"role {name} missing after restore")
            continue
        can_login, *elevated = roles[name]
        if can_login != login:
            problems.append(f"role {name} login={can_login}, expected {login}")
        if any(elevated):
            problems.append(f"role {name} has elevated attributes")
    if "cse_worker" in roles:
        for t in PROTECTED:
            cur.execute("select to_regclass(%s)", (f"public.{t}",))
            if cur.fetchone()[0] is None:
                continue
            for priv in ("UPDATE", "DELETE", "TRUNCATE"):
                cur.execute("select has_table_privilege('cse_worker', %s, %s)", (f"public.{t}", priv))
                if cur.fetchone()[0]:
                    problems.append(f"cse_worker has {priv} on {t} after restore")
    cur.execute("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                "join pg_roles r on r.oid = c.relowner where n.nspname in ('public', 'ops') "
                "and c.relkind in ('r', 'p', 'v', 'S') and r.rolname <> 'cse_owner'")
    if cur.fetchone()[0]:
        problems.append("objects not owned by cse_owner after restore")
    return problems


def restore_and_compare(s, dump_dir, keep=False, log=print, cluster_factory=EphemeralCluster):
    problems = ops_backup.verify_dump(dump_dir, s.pg_bindir)
    if problems:
        return problems, {}
    manifest = ops_backup.load_manifest(dump_dir)
    paths = ops_settings.backup_paths(s)
    info = {}
    superuser = manifest.get("bootstrap_superuser") or "cse_restore_check"
    with cluster_factory(s.pg_bindir, base_dir=paths["restore_scratch"], port=s.restore_port, keep=keep,
                         superuser=superuser) as ec:
        g = ec.psql("postgres", "-f", os.path.join(dump_dir, ops_backup.GLOBALS_FILE), check=False)
        errors = [l for l in (g.stderr or "").splitlines() if "ERROR" in l and "already exists" not in l]
        if errors:
            problems.append("globals restore errors: " + " | ".join(errors[:5]))
        ec._run([ec.tool("pg_restore"), *ec.libpq_args(), "-d", "postgres", "--create", "--exit-on-error",
                 os.path.join(dump_dir, ops_backup.DUMP_FILE)], timeout=6 * 3600)
        conn = ec.connect(dbname=manifest["database"])
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                dbhash.apply_session_settings(cur)
                actual = dbhash.inventory(cur)
                problems += dbhash.compare(manifest["inventory"], actual)
                problems += security_invariants(cur)
                cur.execute("select current_setting('server_version')")
                info = {"restored_server_version": cur.fetchone()[0], "tables": len(actual["tables"]),
                        "rows": sum(t["rows"] for t in actual["tables"].values())}
        finally:
            conn.close()
    log(f"restore check of {dump_dir}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return problems, info


def run(s, led, redact, log, dump_dir=None, keep=False, cluster_factory=EphemeralCluster):
    rec_run = led.start("restore_check")
    dumps = ops_backup.list_dumps(s)
    target = dump_dir or (dumps[-1] if dumps else None)
    if target is None:
        return 1, led.finish(rec_run, "failed", error="no local dump to restore")
    key = os.path.relpath(target, s.backup_root).replace(os.sep, "/")
    try:
        problems, info = restore_and_compare(s, target, keep=keep, log=log, cluster_factory=cluster_factory)
    except (EphemeralError, OSError, ValueError, KeyError) as exc:
        problems, info = [f"{type(exc).__name__}: {exc}"], {}
    except Exception as exc:  # noqa: BLE001 — database errors from the throwaway cluster
        problems, info = [f"{type(exc).__name__}: {exc}"], {}
    if problems:
        return 1, led.finish(rec_run, "failed", artifact_key=key, covers=[],
                             details={"problems": problems[:50], **info}, error=problems[0])
    rec = led.finish(rec_run, "succeeded", artifact_key=key, covers=[key], details=info)
    problem = ops_ledger.ledger_problem(rec)
    if problem:
        log(problem)
    return ops_ledger.exit_code(rec), rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="Restore a local dump into a throwaway cluster and verify it")
    ap.add_argument("--dump", default=None, help="dump directory (default: newest)")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway cluster directory for inspection")
    args = ap.parse_args(argv)
    s = ops_settings.load()
    redact = Redactor()
    log = lambda m: print(redact(m), file=sys.stderr)
    led, conn = ops_backup.open_ledger(s, redact)
    led.tool_version = TOOL_VERSION
    try:
        if conn is not None:
            led.close_abandoned("restore_check", s.stale_run_hours)
        code, rec = run(s, led, redact, log, dump_dir=args.dump, keep=args.keep)
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(rec, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
