"""Synthetic staging data (docs/ops/STAGING.md: ST-08, ST-20, ST-23). Runs in the staging host container as the OS user
cse-worker (peer authentication to cse_worker, P1's map), and writes ONLY through the frozen stores, in F5's accepted
persist order: F3 store -> classification id -> issuer link -> R-F4 store -> F5 store, one transaction per filing (the
order tests/test_rf4_postgres.py uses). The word layers are the frozen F4 tests' synthetic layouts, harvested in the
runner (inner/harvest_layouts.py). Nothing here is CSE data, and nothing contacts anything but PostgreSQL's socket.

    seed_synthetic.py --layouts FILE --mode full|filings-f3 --start-fid N --count N

`filings-f3` writes the F1 filing rows and F3 classifications only: on a database migrated to 0017, this build's F5 save
is refused without 0018 (docs/ops/PHASE2_BACKFILL.md section 12), so the pre-upgrade seed (ST-08) stops at F3.
Prints one JSON line of measurements.
"""
import argparse
import json
import os
import pickle
import sys
import time

APP = "/opt/cse/app"
sys.path[:0] = [APP, os.path.join(APP, "tests")]

import psycopg2  # noqa: E402

import rf4_support as R  # noqa: E402  (synthetic chain helpers; imports worker modules only)
from worker.financial_candidates_store import PostgresCandidateStore, classification_id  # noqa: E402
from worker.financial_f4_store import store as f4store  # noqa: E402
from worker.issuer_store import PostgresIssuerStore  # noqa: E402
from worker.report_classification_store import PostgresClassificationStore  # noqa: E402


def filing_row(cur, fid):
    cur.execute("insert into report_filings (cse_filing_id, path, file_text, source_buckets, uploaded_at, "
                "uploaded_at_raw, first_seen_at, last_seen_at) values (%s, %s, 'Interim Financial Statements', "
                "'{quarterly}', %s, %s, now(), now())",
                (fid, R.path_of(fid), R.FILING_TS["uploaded_at"], R.FILING_TS["uploaded_at_raw"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layouts", required=True)
    ap.add_argument("--mode", choices=("full", "filings-f3"), required=True)
    ap.add_argument("--start-fid", type=int, required=True)
    ap.add_argument("--count", type=int, required=True)
    a = ap.parse_args()
    with open(a.layouts, "rb") as f:
        data = pickle.load(f)
    by_name = dict(data["layers"])
    pool = [by_name[n] for n in data["with_cells"]]
    conn = psycopg2.connect(dbname=os.environ.get("CSE_DB_NAME", "cse"), host="/var/run/postgresql", user="cse_worker")
    conn.autocommit = False
    timings, f4_saved, f5_saved = [], 0, 0
    for i in range(a.count):
        fid = a.start_fid + i
        cls, ext, res = R.chain(pool[i % len(pool)], fid)
        t0 = time.monotonic()
        with conn.cursor() as cur:
            filing_row(cur, fid)
        PostgresClassificationStore(conn).save(cls, 1000)
        if a.mode == "full":
            cid = classification_id(conn, cls)
            link = PostgresIssuerStore(conn).link_filing(cls["cse_filing_id"])
            f4store.PostgresF4Store(conn).save(ext, cid, cls)
            PostgresCandidateStore(conn).save(res, cid, link)
            f4_saved += 1
            f5_saved += 1
        conn.commit()
        timings.append(time.monotonic() - t0)
    conn.close()
    timings.sort()
    print(json.dumps({"mode": a.mode, "filings": a.count, "start_fid": a.start_fid, "f4_extractions": f4_saved,
                      "f5_runs": f5_saved, "layouts_in_pool": len(pool), "seconds_total": round(sum(timings), 3),
                      "ms_per_filing_median": round(1000 * timings[len(timings) // 2], 2),
                      "ms_per_filing_max": round(1000 * timings[-1], 2)}))


if __name__ == "__main__":
    main()
