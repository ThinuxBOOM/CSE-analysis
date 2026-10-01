"""
Real-data validation runner (docs/REAL_DATA_VALIDATION_DESIGN.md): the whole pipeline on a throwaway PostgreSQL 17
cluster.
- The full JSON report is written OUTSIDE the repository: it holds CSE-derived labels and values (G-1).
- tests/test_rdv_postgres.py runs the same functions and asserts the same numbers.

    CSE_F6_CORPUS_DIR=... CSE_F0_CAPTURE_DIR=... P1_PG_BINDIR=... python3 tests/rdv_report.py --out /tmp/rdv.json

This needs Linux (the P1 ephemeral cluster). Run it without a network (`docker run --network none`): it contacts
no one.
"""
import argparse
import json
import os
import platform
import sys
import tempfile
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rdv_evidence as E  # noqa: E402
import rdv_measure as M  # noqa: E402
from worker.financial_truth_store import preflight, verify  # noqa: E402


def _count(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        n = cur.fetchone()[0]
    conn.rollback()
    return n


def first_difference(a, b):
    """The first section of two projections that differs, with a few differing items (for diagnosis)."""
    for section in sorted(set(a) | set(b)):
        x, y = a.get(section), b.get(section)
        if x == y:
            continue
        if isinstance(x, list) and isinstance(y, list):
            diff = [[i, p, q] for i, (p, q) in enumerate(zip(x, y)) if p != q][:3]
            return {"section": section, "lengths": [len(x), len(y)], "items": diff}
        return {"section": section}
    return None


def run(bindir, base_dir, bundle, log=print):
    timings, t = {}, time.time()

    def lap(name):
        nonlocal t
        timings[name] = round(time.time() - t, 1)
        log(f"[rdv] {name}: {timings[name]} s")
        t = time.time()

    cluster = S.start_cluster(bindir, base_dir)
    try:
        lap("cluster + migrations")
        first = E.build_database(cluster, bundle)
        lap("replay + F6.4 validate / configure / designate / reconcile")
        db = first["database"]
        reader = E.reader_conn(cluster, db)
        worker = S.conn(cluster, db, "cse_worker")
        import psycopg2
        report = {
            "rdv_version": E.RDV_VERSION, "manifest_sha256": E.manifest_digest(),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "psycopg2": psycopg2.__version__},
            "replay": first["replay"],
            "f6_4": {"validate": dict(Counter(v["state"] for v in first["validate"])),
                     "validate_failures": [v for v in first["validate"] if v["state"] != "succeeded"],
                     "configuration": list(first["configuration"]), "designation_id": first["designation"],
                     "reconcile": {k: first["reconcile"].get(k) for k in ("state", "partitions", "written",
                                                                          "unchanged", "failed",
                                                                          "failed_validations")}},
        }
        report["coverage"] = M.measure(reader)
        lap("coverage")
        report["verify"] = verify.verify(reader, sample=report["coverage"]["validation"]["validation_runs"])
        lap("verify (every validation run reproduced)")
        report["recompute"] = M.recompute(reader)
        lap("recompute (D4)")
        report["persistence"] = {
            "element_checks": {name: _count(worker, sql) for name, sql in S.ELEMENT_CHECKS.items()},
            "numeric_text_mismatches": S.numeric_text_mismatches(worker),
            "preflight_problems": preflight.problems(worker)}
        lap("persistence checks")
        report["differential"] = M.differential(reader)
        lap("issuer-evidence differential")
        report["provenance"] = M.provenance(reader)
        lap("provenance")
        report["anomalies"] = M.anomalies(reader)
        lap("anomalies")
        proj1 = M.projection(reader)
        repeat = E.repeat_jobs(cluster, db)
        refused = E.nondeterminism_refused(cluster, db, 49384)
        proj1b = M.projection(reader)
        lap("D1 / D2 / D6")
        reader.close()
        worker.close()
        second = E.build_database(cluster, bundle)
        lap("second replay")
        r2 = E.reader_conn(cluster, second["database"])
        proj2 = M.projection(r2)
        r2.close()
        lap("projections")
        report["determinism"] = {
            "repeat": repeat, "nondeterminism": refused,
            "unchanged_after_repeats": proj1 == proj1b,
            "second_load": {"databases": [db, second["database"]],
                            "projection_digests": [proj1["digest"], proj2["digest"]],
                            "identical": proj1 == proj2,
                            "first_difference": first_difference(proj1["projection"], proj2["projection"])}}
        report["timings_seconds"] = timings
        return report
    finally:
        cluster.cleanup()


def summary(r):
    c = r["coverage"]
    v, rec = c["validation"], c["reconciliation"]
    lines = [
        f"manifest {r['manifest_sha256']}",
        f"F1 filings {c['f1']['filings']}; with F3/F5 evidence {c['f1']['filings_with_f3_f5_evidence']}",
        f"issuer decisions {c['issuer_evidence']['filing_decisions']}",
        f"candidates {v['candidates']} admission {v['admission']}",
        f"refused only for issuer evidence {v['refused_only_for_issuer_evidence']['candidates']}",
        f"SOs {c['source_observations']['count']} {c['source_observations']['by_status']}",
        f"facts {c['facts']['count']}; current {rec['current_facts']} {rec['states']}",
        f"verify ok={r['verify']['ok']} reproduced={r['verify']['counts'].get('reproduced')}",
        f"recompute ok={r['recompute']['ok']} {r['recompute']['counts']}",
        f"differential unexplained={len(r['differential']['unexplained'])} {r['differential']['explained']}",
        f"provenance complete={r['provenance']['complete']} facts={r['provenance']['facts_traced']} "
        f"candidates={r['provenance']['candidates_traced']}",
        f"determinism repeat={r['determinism']['repeat']['validate']} rows_unchanged="
        f"{r['determinism']['repeat']['rows_unchanged']} "
        f"D6 refused={bool(r['determinism']['nondeterminism']['refused'])}"
        f" second load identical={r['determinism']['second_load']['identical']}",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="report path OUTSIDE the repository")
    ap.add_argument("--base-dir", default=None, help="short directory for the throwaway cluster (socket path limit)")
    args = ap.parse_args(argv)
    out = os.path.realpath(args.out)
    if out.startswith(E.REPO + os.sep):
        raise SystemExit("the report holds CSE-derived data: write it outside the repository (G-1)")
    bundle, bindir = E.locate(), os.environ.get("P1_PG_BINDIR")
    if bundle is None or not bindir:
        raise SystemExit(f"set {E.CORPUS_ENV}, {E.CAPTURE_ENV} and P1_PG_BINDIR")
    report = run(bindir, args.base_dir or tempfile.mkdtemp(prefix="rdv"), bundle)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    print(summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
