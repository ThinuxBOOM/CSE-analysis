"""
F6.4 command line (docs/F6.4_DESIGN.md section 17.6). On the server run it through ops/bin/cse-financial: as the
cse-worker OS user (peer-authenticated as cse_worker) for everything except `designate`, the OWNER decision, which runs
as the owner-delegation login cse-migrator (acting as cse_owner for one INSERT; root via sudo only, as P2's
acknowledge-block and P3's arm). A JSON report goes to stdout.

    validate --f5-run UUID | --pending               validate F5 runs lacking their canonical validation run (M4)
    register-configuration (--all-present | --f3 V|T ... --f4 W|F ... --f5 B|M|V ...)
                                                     insert a reconciliation configuration if absent
    designate --configuration ID --note TEXT         OWNER decision: the canonical configuration consumers read
    reconcile (--configuration ID | --designated) [--no-validate] [--issuer UUID]
    cleanup                                          mark started jobs without a final event `abandoned`
    status                                           jobs, pending validations, designation, current batches
    verify [--sample N]                              re-prove everything stored (read-only)
    preflight                                        the worker role's security preflight

There is deliberately no delete / reset / repair command: F6.4 storage is append-only.

Exit status: 0 ok / already present / busy, 4 PostgreSQL unavailable, 5 refused or failed (preflight, input error,
nondeterminism, inputs changed, decomposition mismatch, version or configuration mismatch, --no-validate with
missing validations).
"""
import argparse
import json
import os
import sys

from ..financial_truth import reconciliation
from ..ops import settings as ops_settings
from . import EXIT_DATABASE_UNAVAILABLE, EXIT_OK, EXIT_REFUSED, jobs, preflight, verify

WRITING = ("validate", "register-configuration", "reconcile", "cleanup")


def parser():
    ap = argparse.ArgumentParser(prog="python -m worker.financial_truth_store", description="F6.4 financial truth store")
    sub = ap.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate")
    g = v.add_mutually_exclusive_group(required=True)
    g.add_argument("--f5-run")
    g.add_argument("--pending", action="store_true")
    v.add_argument("--nowait", action="store_true")
    rc = sub.add_parser("register-configuration")
    rc.add_argument("--all-present", action="store_true")
    rc.add_argument("--f3", action="append", default=[], help="classifier_version|text_extractor")
    rc.add_argument("--f4", action="append", default=[], help="word_extractor|f4_extractor_version")
    rc.add_argument("--f5", action="append", default=[], help="builder_version|mapper_version|vocabulary_version")
    d = sub.add_parser("designate")
    d.add_argument("--configuration", required=True)
    d.add_argument("--note", required=True)
    r = sub.add_parser("reconcile")
    rg = r.add_mutually_exclusive_group(required=True)
    rg.add_argument("--configuration")
    rg.add_argument("--designated", action="store_true")
    r.add_argument("--no-validate", action="store_true")
    r.add_argument("--issuer")
    sub.add_parser("cleanup")
    sub.add_parser("status")
    vf = sub.add_parser("verify")
    vf.add_argument("--sample", type=int, default=3)
    sub.add_parser("preflight")
    return ap


def _tuples(values, width, what):
    out = []
    for v in values:
        parts = [None if p == "" else p for p in v.split("|")]
        if len(parts) != width:
            raise SystemExit(f"--{what} needs {width} '|'-separated parts: {v!r}")
        out.append(tuple(parts))
    return tuple(out)


def _print(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def status(conn):
    out = {}
    with conn.cursor() as cur:
        cur.execute("select state, count(*) from financial_f6_job_state group by state order by state")
        out["jobs_by_state"] = dict(cur.fetchall())
        out["pending_validations"] = len(jobs.pending_runs(cur))
        out["designated"] = jobs.designated_configuration(cur)
        cur.execute("select configuration_id, count(distinct issuer_id), count(*) from financial_reconciliation_current "
                    "group by configuration_id order by configuration_id")
        out["current"] = [{"configuration_id": c, "issuers": i, "facts": n} for c, i, n in cur.fetchall()]
    conn.rollback()
    return out


def run(args, settings, connect):
    import psycopg2
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    try:
        if args.command == "designate":
            new_id = jobs.designate(conn, args.configuration, args.note, os.environ.get("CSE_OPERATOR"))
            _print({"state": "designated", "id": new_id, "configuration_id": args.configuration})
            return EXIT_OK
        if args.command in WRITING or args.command == "preflight":
            found = preflight.problems(conn)
            if args.command == "preflight":
                _print({"problems": found})
                return EXIT_OK if not found else EXIT_REFUSED
            if found:
                _print({"state": "refused", "reason": "preflight", "problems": found})
                return EXIT_REFUSED
        rev = jobs.code_revision()
        if args.command == "validate":
            if args.pending:
                with conn.cursor() as cur:
                    todo = jobs.pending_runs(cur)
                conn.rollback()
                reports = [jobs.validate(conn, r, wait=not args.nowait, code_revision=rev) for r in todo]
            else:
                reports = [jobs.validate(conn, args.f5_run, wait=not args.nowait, code_revision=rev)]
            _print(reports)
            bad = [r for r in reports if r["state"] in ("failed",)]
            return EXIT_REFUSED if bad else EXIT_OK
        if args.command == "register-configuration":
            if args.all_present:
                with conn.cursor() as cur:
                    cfg = jobs.configuration_from_present_runs(cur)
                conn.rollback()
            else:
                cfg = reconciliation.ReconciliationConfiguration(accepted_f3=_tuples(args.f3, 2, "f3"),
                                                                 accepted_f4=_tuples(args.f4, 2, "f4"),
                                                                 accepted_f5=_tuples(args.f5, 3, "f5"))
            state, cid = jobs.register_configuration(conn, cfg)
            _print({"state": state, "configuration_id": cid})
            return EXIT_OK
        if args.command == "reconcile":
            cid = args.configuration
            if args.designated:
                with conn.cursor() as cur:
                    cid = jobs.designated_configuration(cur)
                conn.rollback()
                if cid is None:
                    _print({"state": "refused", "reason": "no designated configuration"})
                    return EXIT_REFUSED
            rep = jobs.reconcile(conn, cid, no_validate=args.no_validate, code_revision=rev, only_issuer=args.issuer)
            _print(rep)
            return EXIT_OK if rep["state"] in ("succeeded",) or rep.get("reason") == "busy" else EXIT_REFUSED
        if args.command == "cleanup":
            rep = jobs.cleanup(conn, code_revision=rev)
            _print(rep)
            return EXIT_OK
        if args.command == "status":
            _print(status(conn))
            return EXIT_OK
        if args.command == "verify":
            rep = verify.verify(conn, sample=args.sample)
            _print(rep)
            return EXIT_OK if rep["ok"] else EXIT_REFUSED
    except (jobs.JobRefused, jobs.OwnerPathRequired, reconciliation.ConfigurationError) as exc:
        _print({"state": "refused", "reason": str(exc)})
        return EXIT_REFUSED
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    finally:
        conn.close()
    return EXIT_REFUSED


def main(argv=None):
    args = parser().parse_args(argv)
    settings = ops_settings.load()
    return run(args, settings, lambda: ops_settings.connect(settings))


if __name__ == "__main__":
    sys.exit(main())
