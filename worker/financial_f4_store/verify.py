"""
R-F4's `verify` (docs/R_F4_DESIGN.md section 5), modelled on F6.4's: re-proves what is stored, read-only.

- every extraction: its version, child counts and content hash (it reads back exactly), and its F3 input hash;
- every F5 run: it has a saved F4 extraction ('missing' otherwise, e.g. a run saved before R-F4), and F5 rebuilt from
  the saved F3 and F4 evidence reproduces the run's stored content_sha256 ('f5_mismatch' otherwise;
  'f5_rebuild_refused' when F5's own consistency check refuses under the declared decimal context).

A problem is reported, never repaired. It runs in one READ ONLY transaction, so the reader role can run it, and every
change, even one a role with INSERT would be allowed, is refused by PostgreSQL itself.

One snapshot (audit finding RF4-A1, 2026-10-20). The transaction is REPEATABLE READ, set before its first query, so the
enumeration, the F4 read-backs, the F3 read-backs and the F5 rebuilds all see the database as it was at that query,
whatever another transaction commits meanwhile. A change committed during the run is not in this report; the next
verify sees it. The report records the snapshot (`snapshot`): its isolation, that it was read only, and its time.

Name resolution (audit finding RF4-A1). The enumeration names its tables without a schema, as the reader does. Before
it, the session's own name resolution is checked (schema.search_path_problems, the writer's check, unchanged); a
session that could resolve a name outside public and pg_catalog gets a refused report: not clean, nothing read
(`name_resolution`). The reader checks again before each of its own reads; a refusal there ends the run, also not clean.
"""
import psycopg2.extensions

from .. import financial_candidates as f5
from . import STORE_VERSION, codec, reader, schema
from .errors import EvidenceRefused, SchemaUnavailable

_SNAPSHOT = """select pg_catalog.current_setting('transaction_isolation'),
                      pg_catalog.current_setting('transaction_read_only'),
                      pg_catalog.transaction_timestamp()"""


def verify(conn, *, rebuild=True, limit=None):
    """The report {store_version, snapshot, extractions, runs, rebuilt, problems: [{kind, ...}], clean}. `conn` must
    have no open transaction; verify rolls its own read-only transaction back."""
    if conn.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        raise ValueError("verify needs a connection with no open transaction")
    report = {"store_version": STORE_VERSION, "snapshot": None, "extractions": 0, "runs": 0, "rebuilt": 0,
              "problems": []}
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read, read only")      # before any query
            unsafe = schema.search_path_problems(cur)          # pg_catalog-qualified: the snapshot's first query
            cur.execute(_SNAPSHOT)
            isolation, read_only, taken_at = cur.fetchone()
            report["snapshot"] = {"isolation": isolation, "read_only": read_only == "on",
                                  "taken_at": taken_at.isoformat()}
            if (isolation, read_only) != ("repeatable read", "on"):
                raise RuntimeError(f"verify's transaction is {isolation}, read_only={read_only}")
            if unsafe:
                report["problems"].append({"kind": "name_resolution", "detail": "; ".join(unsafe)})
                return _finish(report)
            cur.execute("select id, classification_id, f3_input_sha256 from financial_f4_extractions order by id"
                        + (" limit %s" % int(limit) if limit else ""))
            extractions = cur.fetchall()
            cur.execute("select id from financial_extraction_runs order by id" + (" limit %s" % int(limit) if limit
                                                                                  else ""))
            runs = [r[0] for r in cur.fetchall()]
        try:
            _check(conn, report, extractions, runs, rebuild)
        except SchemaUnavailable as exc:                   # the session's resolution changed during the run
            report["problems"].append({"kind": "name_resolution", "detail": exc.detail,
                                       "after": {"extractions": report["extractions"], "runs": report["runs"]}})
    finally:
        conn.rollback()
    return _finish(report)


def _finish(report):
    report["clean"] = not report["problems"]
    return report


def _check(conn, report, extractions, runs, rebuild):
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
