"""
R-F4's commands (docs/R_F4_DESIGN.md sections 5 and 10). Read-only; they write nothing and contact nothing but
PostgreSQL.

    python -m worker.financial_f4_store preflight [--role cse_worker|cse_reader ...] [--migration-role]
    python -m worker.financial_f4_store verify [--no-rebuild] [--limit N]

Each prints a JSON report. verify exits 0 when clean, 1 otherwise. preflight exits 0 only when the checkout's 0018
matches its pin, the database's objects pass AND the ledger was read and records 0018 at the pin; 1 when any check found
a problem; 2 when nothing was found wrong but the ledger could not be read by this connection (NOT verified).

preflight connects with DATABASE_URL, or with --migration-role as P1's runner does (CSE_DB_* settings, user CSE_DB_USER
or cse_migrator), which reads the ledger as the owner role: the owner-level check.
"""
import argparse
import json
import sys

from .. import db
from ..ops import settings as ops_settings
from . import preflight, verify


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m worker.financial_f4_store")
    sub = p.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="0018: the checkout file against its pin, the database objects, the ledger")
    pre.add_argument("--role", choices=sorted(preflight.PRIVILEGES), action="append",
                     help="a role whose privileges to check (repeatable; default: both)")
    pre.add_argument("--migration-role", action="store_true",
                     help="connect as P1's runner does and read the ledger as the owner role")
    ver = sub.add_parser("verify", help="re-prove every saved extraction and F5 rebuild (read-only)")
    ver.add_argument("--no-rebuild", action="store_true", help="check the extractions and their presence only")
    ver.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    owner_role = preflight.OWNER_ROLE
    if args.command == "preflight" and args.migration_role:
        s = ops_settings.load()
        owner_role = s.owner_role or owner_role
        conn = ops_settings.connect(s, user=s.db_user or "cse_migrator")
    else:
        conn = db.get_connection()
    try:
        if args.command == "preflight":
            report = dict(preflight.report(conn, roles=tuple(args.role or preflight.PRIVILEGES),
                                           owner_role=owner_role), command="preflight")
            code = 0 if report["clean"] else 1 if (report["file"] or report["objects"]
                                                     or report["ledger"]["status"] == "failed") else 2
        else:
            report = dict(verify.verify(conn, rebuild=not args.no_rebuild, limit=args.limit), command="verify")
            code = 0 if report["clean"] else 1
    finally:
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
