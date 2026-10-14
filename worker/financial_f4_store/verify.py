"""
R-F4's `verify` (docs/R_F4_DESIGN.md section 5), modelled on F6.4's: re-proves what is stored, read-only.

- every extraction: its version, child counts and content hash (it reads back exactly), and its F3 input hash;
- every F5 run: it has a saved F4 extraction ('missing' otherwise, e.g. a run saved before R-F4), and F5 rebuilt from
  the saved F3 and F4 evidence reproduces the run's stored content_sha256 ('f5_mismatch' otherwise;
  'f5_rebuild_refused' when F5's own consistency check refuses under the declared decimal context).

A problem is reported, never repaired. It runs in a READ ONLY transaction, so the reader role can run it.
"""
import psycopg2.extensions

from .. import financial_candidates as f5
from . import STORE_VERSION, codec, reader
from .errors import EvidenceRefused


def verify(conn, *, rebuild=True, limit=None):
    """The report {store_version, extractions, runs, problems: [{kind, ...}], clean}. `conn` must have no open
    transaction; verify rolls its own read-only transaction back."""
    if conn.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        raise ValueError("verify needs a connection with no open transaction")
    report = {"store_version": STORE_VERSION, "extractions": 0, "runs": 0, "rebuilt": 0, "problems": []}
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute("select id, classification_id, f3_input_sha256 from financial_f4_extractions order by id"
                        + (" limit %s" % int(limit) if limit else ""))
            extractions = cur.fetchall()
            cur.execute("select id from financial_extraction_runs order by id" + (" limit %s" % int(limit) if limit
                                                                                  else ""))
            runs = [r[0] for r in cur.fetchall()]
        for eid, cid, f3_sha in extractions:
            report["extractions"] += 1
            try:
                reader.load_extraction(conn, eid)
                if codec.f3_input_sha256(reader.load_classification(conn, cid)) != f3_sha:
                    raise EvidenceRefused("corrupt", f"extraction {eid}: the F3 classification read back differs "
                                                     f"from the input F5 consumed (f3_input_sha256)")
            except EvidenceRefused as exc:
                report["problems"].append({"kind": exc.kind, "extraction_id": str(eid), "detail": exc.detail})
        for run_id in runs:
            report["runs"] += 1
            try:
                if rebuild:
                    result, stored = reader.rebuild_f5(conn, run_id)
                    report["rebuilt"] += 1
                    if f5.content_sha256(result) != stored:
                        raise EvidenceRefused("f5_mismatch", f"F5 run {run_id}: the rebuilt content_sha256 differs "
                                                             f"from the stored one")
                elif reader.extraction_id_for(conn, reader.run_key(conn, run_id)) is None:
                    raise EvidenceRefused("missing", f"F5 run {run_id} has no saved F4 extraction")
            except EvidenceRefused as exc:
                report["problems"].append({"kind": exc.kind, "run_id": str(run_id), "detail": exc.detail})
    finally:
        conn.rollback()
    report["clean"] = not report["problems"]
    return report
