"""
R-F4's commands (docs/R_F4_DESIGN.md sections 5 and 10). Read-only; they write nothing and contact nothing but
PostgreSQL (DATABASE_URL).

    python -m worker.financial_f4_store preflight [--role cse_worker|cse_reader]
    python -m worker.financial_f4_store verify [--no-rebuild] [--limit N]

Each prints a JSON report and exits 0 when it is clean, 1 otherwise.
"""
import argparse
import json
import sys

from .. import db
from . import preflight, verify


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m worker.financial_f4_store")
    sub = p.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="migration 0018's pinned hash, tables, privileges and triggers")
    pre.add_argument("--role", choices=sorted(preflight.PRIVILEGES), default="cse_worker")
    ver = sub.add_parser("verify", help="re-prove every saved extraction and F5 rebuild (read-only)")
    ver.add_argument("--no-rebuild", action="store_true", help="check the extractions and their presence only")
    ver.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    conn = db.get_connection()
    try:
        if args.command == "preflight":
            problems = preflight.problems(conn, role=args.role)
            report = {"command": "preflight", "problems": problems, "clean": not problems}
        else:
            report = dict(verify.verify(conn, rebuild=not args.no_rebuild, limit=args.limit), command="verify")
    finally:
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
