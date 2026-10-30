"""
R-F4 (docs/R_F4_DESIGN.md) against REAL PostgreSQL 17: migration 0018 through P1's runner, the writer, the reader, the
F3 read-back, the F5 rebuild, verify and the preflight, as the restricted roles.

p1-p17 (Stage 1) save each document in `_persist`'s accepted order by hand: F3's store, the classification id, the
issuer link, R-F4's store, then F5's store, in one transaction. p18-p24 (Stage 2, design section 8.2 step 3) go through
the real callers, with F5's `_persist` wired to R-F4: F5's own `run()` with exactly main()'s four stores, and HB-4's
document slice, both unchanged and both reaching R-F4 through `_persist`'s fallback.

    p1   0018 applied at its pinned hash; the preflight's file and object checks pass for both roles, and its ledger
         check is 'verified' as cse_reader (0009 lets it read ops) and the migration role, 'unverifiable' as cse_worker
    p2   least privilege; append-only for every role, the superuser included; the classification guard
    p3   every synthetic layout: saved, read back type-exactly (RT-15), the F3 read-back exact, F5 rebuilt to the same
         content_sha256 as the original in memory and as stored (RT-14 at the store level); unmapped cells kept
    p4   Decimals through the real chain under three extraction contexts (RT-3), and f5_rebuild_refused
    p5   ints, floats, -0.0, subnormals and extremes, bit-exact (RT-4); non-finite refused; the negative controls
    p6   refusals write nothing and leave the caller's transaction usable
    p7   idempotency and conflicting evidence
    p8   a failure inside the store, or a failed read-back, rolls back its savepoint; the caller decides the rest
    p9   a database without 0018: refused in-band before any insert; the forward upgrade 0017 -> 0018
    p10  concurrent writers of the same key: one set of rows, no deadlock
    p11  verify: clean, then missing, incomplete, corrupt, version_incompatible and f5_mismatch, read-only as the reader
    p12  in-band schema integrity: a missing, disabled, replica-only or re-pointed trigger, or a missing privilege,
         refuses the save before any insert (the transaction's insert counter stays 0); the check is not cached
    p13  the preflight's three checks kept apart: the ledger verified as cse_reader, the migration role and the
         superuser, unverifiable as cse_worker, failed on a tampered or absent entry; object failures reported,
         a forbidden INSERT or UPDATE granted on single columns included; the command's exit codes (0 / 1 / 2)
    p14  integers: each column's SQL-range boundaries are stored and read back exactly; one past them (line_count
         2**40) is a typed refusal before any insert, and the caller's transaction stays usable
    p15  verify: an oversized number in saved evidence (as text, or inside jsonb) is `corrupt` with its reason, and
         the other extractions and runs are still verified
    p16  long integers (up to 4300 digits) saved by a writer with no int-string limit read back exactly through the
         real reader and its jsonb decoding, verify included, in a process at the lowest limit Python allows (640)
    p17  a rerun of a saved key is 'already_present' only when the saved extraction reads back intact: after a row is
         appended or a cell tampered with, the same save is refused (incomplete, corrupt), writes nothing and leaves the
         caller's transaction usable; once the evidence is restored it is 'already_present' again
    p18  RT-14 and RT-15 through F5's run() for every synthetic layout: each saved F4 extraction reads back type-exactly
         equal to F4's in-memory output, and F5 rebuilt from the saved evidence hashes to the in-memory result and the
         stored one; verify is clean
    p19  a rerun of run() is 'already_present' for F3, F4 and F5 (the documents are downloaded again; nothing is
         written twice); a run saved before R-F4 is 'missing' to verify and gets F4 only from a new download
    p20  RT-3 through run() wrapped in three extraction contexts: signed zeros and exponent forms stored exactly, RT-14
         holds, and the precision-5 extraction is f5_rebuild_refused under the declared context
    p21  a failure at filing k (a scripted F4 failure, or RT-4's NaN refused by R-F4) makes run() raise and leaves the
         whole batch uncommitted, filing 1 included; a rerun saves every filing once
    p22  RT-14 and RT-15 through HB-4's slice; the F4 extraction, the F5 run and the 'persisted' event are written by
         one transaction per filing
    p23  HB-4: a failure inside the F4 save, inside the F5 save after it, in the ledger event, or RT-4's NaN rolls the
         filing back (no F3, F4 or F5 rows) and is recorded (retry_wait or failed); the retry downloads once more and
         saves once
    p24  two concurrent run()s of one filing, each on its own connection: the second waits on the first's uncommitted
         rows, then finds F3, F4 and F5 already present; one set of rows, no deadlock (design section 6)
    p25  audit remediation (Finding 1): an F3 row persisted earlier under the same F3 key, with content other than the
         classification F5 consumed, refuses the F4 save (ConflictingEvidence) before any insert, on the 'inserted'
         and 'already_present' paths; run() then leaves its whole batch uncommitted, HB-4 rolls the filing back and
         records it; verify still reports historical evidence saved without the check
    p26  audit remediation (Finding 2): a trigger running a same-named function in another schema, a WHEN condition,
         a replaced function body, a column list or an argument is refused by the store before any insert and
         reported by the preflight
    p27  audit remediation (Finding 3): the live constraints equal the preflight's definitions; a same-name
         replacement of another type, a weakened check, a changed key, a foreign key into another schema or with
         another action, a NOT VALID constraint and an extra constraint are each reported, the three checks apart
    p28  audit remediation (Finding 4): a search_path set on the guard function, its body unchanged, lets a shadow
         table vouch for another filing's classification; the store refuses before any insert, the preflight reports it
    p29  audit remediation (Finding 5): an extra enabled BEFORE INSERT trigger named after the guard rewrites a row
         the guard accepted; it, and one on another table, are refused by the store before any insert and reported
         by the preflight; a disabled one is not counted as enabled
    p30  audit remediation (search_path): a role-level search_path with a shadow schema ahead of public, under which
         Findings 2-5's checks pass and the guard accepts another filing's classification, is refused by the store
         before any insert and reported by the preflight; a safe role-in-database setting outranks it and passes
    p31  a database-level search_path ahead of public is refused and reported whether its schema holds a shadow
         table, is empty or does not exist; a safe one passes
    p32  a SET in the saving session is refused in-band (the preflight cannot see it); "$user" with an existing
         cse_worker schema, or with CREATE on the database, is refused and reported; public missing or pg_catalog
         after public is refused; the default passes
    p33  a temporary report_document_classifications, which the guard reads first, or any temporary relation, is
         refused in-band; an empty temporary schema passes
    p34  search_path evidence: ALTER ROLE ALL SET and ALTER ROLE ... IN DATABASE ... SET, unsafe, compared with the
         session PostgreSQL gives cse_worker: refused in-band and reported at the level PostgreSQL applies; a safe
         higher level passes; cse_reader (NOLOGIN, entered by SET ROLE) gets none of its own settings
    p35  a server-level search_path (ALTER SYSTEM, reload) is refused, also in a session opened before the reload, and
         reported; client options and a database setting outrank it; a preflight connection without the server's value
         reports undetermined, never clean
    p36  a raw server value 'public<no-break space>, public' names a schema PostgreSQL searches before public (the
         defect this work found and fixed: the parser read it as public, public); refused and reported
    p37  parse_search_path agrees with PostgreSQL's own split, value by value (one documented fail-closed divergence)
    p38  a real operator public.= ahead of pg_catalog subverts the guard; refused and reported; public missing too
    p39  pg_temp listed first, no temporary schema, no TEMPORARY: judged without creating one (the second defect
         this work found and fixed: the check failed untyped); a safe path saves, an unsafe one is refused
    p40  audit finding RF4-A1: a reader session with an unsafe search path, where empty shadow tables would hide a
         corrupt extraction from verify and make F3 evidence look missing: verify refuses (not clean, nothing
         read) and every public reader entry point raises SchemaUnavailable before an unqualified query
    p41  a login entering cse_reader by SET ROLE keeps its own unsafe path, or a temporary shadow table; both
         refused, while the preflight (cse_reader's configuration) stays clean
    p42  the safe path: verify clean, the reader reads; verify's transaction is REPEATABLE READ and READ ONLY,
         and a write inside it is refused even for cse_worker
    p43  verify reads one snapshot while another transaction commits a filing or appends a row; the next
         verify sees both
    p44  verify's snapshot metadata: every statement verify runs (the check, the enumerations, every F4, F3 and
         F5 read) has the reported pg_snapshot, transaction start and settings; the snapshot does not see a
         transaction committed mid-run (pg_visible_in_snapshot), the next verify's does
    p45  an autocommit session whose defaults make every statement REPEATABLE READ and READ ONLY gives each
         statement its own snapshot (PostgreSQL's behaviour, shown); verify refuses it before sending anything
    p46  the session is terminated mid-run: verify raises the OperationalError, not the rollback's
         InterfaceError, which is noted on it; nothing is reported clean
    p47  HB-4's slice and F5's run() persist one filing at once (each order): the second waits on the first's
         uncommitted rows, then finds F3, F4 and F5 already present; one set of rows, no deadlock; HB-4's item
         persisted under one claim and one download, its lease released; while HB-4 holds the filing a second
         HB-4 slice cannot start (SliceBusy) and requests nothing
    p48  HB-4 on a database without 0018: R-F4's refusal rolls the whole filing back (no F3 or F5 row, issuer
         links unchanged) and HB-4 records retry_wait or failed through its own path; with 0018 applied, the retry
         saves once
    p49  a limited verify (--limit N) whose first N extractions and runs are intact while unchecked evidence is
         corrupt: partial, never clean, and the command exits 1; a full scan finds the corruption; a limit covering
         every row is still partial; 0 and negative limits are refused (ValueError, usage error exit 2)
    p50  the preflight's columns: 0018's live columns equal its map; a dropped NOT NULL on a critical column, a
         changed type, default, identity, collation, a renamed or an extra column are each reported (the constraint
         check alone sees none of them), the three checks apart; everything restored
    p51  the preflight's table properties: row-level security enabled or forced, a policy, UNLOGGED, a child table
         and an inheritance parent, on 0018's tables and on F5's run table, are each refused, the three checks
         apart; clean before and after each; nothing left behind
    p52  row-level security can hide evidence from verify: a policy on F5's run table hiding a bad run, or no
         policy on 0018's extractions and the run table, and verify (as the restricted cse_reader) reports clean;
         the preflight refuses both, so verify runs only after it is clean; verify itself is unchanged
Synthetic word layers only: NOT real-document validation.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_postgres.py
"""
import contextlib
import copy
import decimal
import json
import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb4_support as H  # noqa: E402
import rf4_support as R  # noqa: E402
from test_document_retrieval import FakeFetcher, FakeResp, TempRoot  # noqa: E402
from worker import document_text  # noqa: E402
from worker import extract_financial_candidates as f5cli  # noqa: E402
from worker import financial_candidates as f5  # noqa: E402
from worker import statement_extraction as se  # noqa: E402
from worker.backfill_documents import worker as hb4  # noqa: E402
from worker.financial_backfill import store as ledger  # noqa: E402
from worker.financial_candidates_store import PostgresCandidateStore, classification_id  # noqa: E402
from worker.financial_f4_store import codec, preflight, reader, schema, store, verify  # noqa: E402
from worker.financial_f4_store.errors import (ConflictingEvidence, EvidenceRefused, SchemaUnavailable,  # noqa: E402
                                              UnsupportedValue)
from worker.issuer_store import PostgresIssuerStore  # noqa: E402
from worker.ops import migrate as mig  # noqa: E402
from worker.report_classification_store import PostgresClassificationStore  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """TCP connections fail the test; PostgreSQL is reached over its Unix socket only."""
    real = socket.socket.connect

    def guarded(self, address):
        if self.family != socket.AF_UNIX:
            raise AssertionError(f"a network connection was attempted: {address!r}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="module")
def cluster():
    base = tempfile.mkdtemp(prefix="rf4", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


class Db:
    def __init__(self, cluster, name=None):
        self.cluster, self.name, self._open = cluster, name or S.fresh_db(cluster), []

    def conn(self, user="cse_worker"):
        c = S.conn(self.cluster, self.name, user, False)
        self._open.append(c)
        return c

    def su(self):
        c = self.cluster.connect(dbname=self.name, user="postgres")
        c.autocommit = True
        self._open.append(c)
        return c

    def reader(self):
        """A connection whose current role is cse_reader (a NOLOGIN role, entered through SET ROLE)."""
        c = self.cluster.connect(dbname=self.name, user="postgres")
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("set role cse_reader")
        c.autocommit = False
        self._open.append(c)
        return c

    def close(self):
        for c in self._open:
            try:
                c.close()
            except Exception:
                pass


@pytest.fixture
def db(cluster):
    d = Db(cluster)
    yield d
    d.close()


@pytest.fixture(scope="module")
def layouts():
    return R.harvest()


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None


def f4_counts(c, fid=None):
    where = "where x.cse_filing_id = %s" if fid is not None else ""
    args = (fid,) if fid is not None else ()
    out = {"extractions": q(c, f"select count(*) from financial_f4_extractions x {where}", args)[0][0]}
    for t in ("statements", "columns", "rows", "cells"):
        join = "" if t == "statements" else " join financial_f4_statements s on s.id = k.statement_id"
        sid = "k.extraction_id" if t == "statements" else "s.extraction_id"
        out[t] = q(c, f"select count(*) from financial_f4_{t} k{join} join financial_f4_extractions x on x.id = {sid} "
                      f"{where}", args)[0][0]
    return out


def seed_filing(w, fid):
    q(w, "insert into report_filings (cse_filing_id, path, file_text, source_buckets, uploaded_at, uploaded_at_raw, "
         "first_seen_at, last_seen_at) values (%s, %s, 'Interim Financial Statements', '{quarterly}', %s, %s, now(), "
         "now())", (fid, R.path_of(fid), R.FILING_TS["uploaded_at"], R.FILING_TS["uploaded_at_raw"]))
    w.commit()


def save_in_persist_order(w, cls, ext, res, *, commit=True):
    """F3 store -> classification id -> issuer link -> R-F4 store -> F5 store, in one transaction (the accepted order,
    design section 3; F5's `_persist` is wired in a later stage)."""
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    link = PostgresIssuerStore(w).link_filing(cls["cse_filing_id"])
    f4 = store.PostgresF4Store(w).save(ext, cid, cls)
    status, run_id = PostgresCandidateStore(w).save(res, cid, link)
    if commit:
        w.commit()
    return SimpleNamespace(cid=cid, f4=f4, f5=status, run_id=run_id)


def persist(w, words, fid, context=None):
    cls, ext, res = R.chain(words, fid, context)
    seed_filing(w, fid)
    s = save_in_persist_order(w, cls, ext, res)
    s.cls, s.ext, s.res, s.fid = cls, ext, res, fid
    s.eid = reader.extraction_id_for(w, {"cse_filing_id": fid, "document_sha256": ext.document_sha256,
                                         "word_extractor": ext.extractor, "f4_extractor_version": ext.extractor_version,
                                         "classification_id": s.cid})
    return s


def a_layout_with_cells(layouts):
    for name, words, _, _ in layouts:
        if R.cells(R.chain(words, 1)[1]):
            return words
    raise AssertionError("no layout with cells")


# ================================================================================================ p1 / p2

def test_p1_0018_is_applied_at_its_pin_and_the_preflight_passes_for_both_roles(db):
    su = db.su()
    assert q(su, "select sha256 from ops.schema_migrations where filename = %s", (preflight.MIGRATION,)) == \
        [(preflight.MIGRATION_SHA256,)]
    names = [r[0] for r in q(su, "select filename from ops.schema_migrations order by version")]
    assert names[-2:] == ["0017_f8_asof_configuration.sql", preflight.MIGRATION]
    r = preflight.report(db.conn())
    assert r["file"] == [] and r["objects"] == [] and r["ledger"]["status"] == "unverifiable" and not r["clean"]
    r = preflight.report(db.reader())
    assert r["clean"] and r["ledger"] == {"status": "verified", "problems": [], "read_as": "cse_reader"}
    m = db.cluster.connect(dbname=db.name, user="cse_migrator")
    try:
        r = preflight.report(m)
        assert r["clean"] and r["ledger"] == {"status": "verified", "problems": [], "read_as": "cse_owner"}
    finally:
        m.close()
    types = dict(q(su, "select table_name || '.' || column_name, data_type from information_schema.columns where "
                       "table_name like 'financial\\_f4\\_%%'"))
    assert types["financial_f4_cells.parsed_value_text"] == "text" and types["financial_f4_columns.x0"] == "text"
    assert types["financial_f4_columns.header_raw"] == "text" and types["financial_f4_statements.heading_raw"] == "text"
    assert types["financial_f4_cells.coordinates"] == "jsonb" and types["financial_f4_statements.pages"] == "ARRAY"
    assert not [k for k, v in types.items() if v in ("character varying", "character", "numeric", "double precision",
                                                     "real", "bytea")]


def test_p2_least_privilege_append_only_for_every_role_and_the_classification_guard(db, layouts):
    import psycopg2
    w = db.conn()
    s = persist(w, a_layout_with_cells(layouts), 920001)
    su = db.su()
    grants = q(su, "select grantee, table_name, privilege_type from information_schema.role_table_grants where "
                   "table_name like 'financial\\_f4\\_%%' and grantee <> 'cse_owner'")
    by = {}
    for grantee, table, priv in grants:
        by.setdefault(grantee, {}).setdefault(table, set()).add(priv)
    assert set(by) == {"cse_worker", "cse_reader"}
    assert all(p == {"SELECT", "INSERT"} for p in by["cse_worker"].values()) and len(by["cse_worker"]) == 5
    assert all(p == {"SELECT"} for p in by["cse_reader"].values()) and len(by["cse_reader"]) == 5
    for sql in ("update financial_f4_cells set raw_value = 'x'", "delete from financial_f4_rows",
                "truncate financial_f4_extractions cascade"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(w, sql)
        w.rollback()
        with pytest.raises(psycopg2.Error, match="append-only|not allowed|rejected"):
            q(su, sql)
    rd = db.reader()
    assert q(rd, "select count(*) from financial_f4_extractions")[0][0] == 1
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(rd, "insert into financial_f4_extractions (cse_filing_id) values (1)")
    rd.rollback()
    other = persist(w, a_layout_with_cells(layouts), 920002)                 # a classification of another filing
    row = q(su, "select * from financial_f4_extractions where id = %s", (s.eid,))
    assert row
    with pytest.raises(psycopg2.errors.CheckViolation, match="is not of filing"):
        q(w, "insert into financial_f4_extractions (cse_filing_id, document_sha256, word_extractor, f4_extractor_version,"
             " classification_id, store_version, document_status, status_reasons, page_count, page_trust, "
             "statement_count, column_count, row_count, cell_count, f4_content_sha256, f3_input_sha256) values (%s, %s,"
             " 'w', 'f4.9', %s, 'f4.store.1', 'x', '[]', 0, '[]', 0, 0, 0, 0, %s, %s)",
          (s.fid, s.ext.document_sha256, other.cid, "a" * 64, "b" * 64))
    w.rollback()


# ================================================================================================ p3

def test_p3_every_synthetic_layout_round_trips_and_f5_rebuilds_to_the_same_hash(db, layouts):
    w = db.conn()
    totals = {"cells": 0, "candidates": 0, "layouts": 0}
    for i, (name, words, _, _) in enumerate(layouts):
        s = persist(w, words, 930000 + i)
        assert (s.f4, s.f5) == ("inserted", "inserted"), name
        counts = f4_counts(w, s.fid)
        assert counts == {"extractions": 1, "statements": len(s.ext.statements),
                          "columns": sum(len(x.columns) for x in s.ext.statements),
                          "rows": sum(len(x.rows) for x in s.ext.statements), "cells": len(R.cells(s.ext))}, name
        back = reader.load_extraction(w, s.eid)
        assert R.diff(s.ext, back) is None, (name, R.diff(s.ext, back))                       # RT-15
        assert R.diff(codec.f3_projection(s.cls), reader.load_classification(w, s.cid)) is None, name
        rebuilt, stored = reader.rebuild_f5(w, s.run_id)
        assert f5.content_sha256(rebuilt) == stored == f5.content_sha256(s.res), name          # RT-14 (store level)
        totals["cells"] += counts["cells"]
        totals["candidates"] += len(s.res["candidates"])
        totals["layouts"] += 1
        w.commit()
    assert totals["layouts"] == len(layouts) >= 45
    assert totals["cells"] > totals["candidates"] > 0                                          # unmapped cells kept
    report = verify.verify(w)
    assert report["clean"], report["problems"][:3]
    assert report["extractions"] == report["runs"] == report["rebuilt"] == len(layouts)


# ================================================================================================ p4

def _texts(c, eid):
    return {r[0]: r[1] for r in q(c, "select k.raw_value, k.parsed_value_text from financial_f4_cells k join "
                                     "financial_f4_statements s on s.id = k.statement_id where s.extraction_id = %s",
                                  (eid,))}


def test_p4_decimals_through_the_real_chain_under_three_extraction_contexts(db):
    w = db.conn()
    default = persist(w, R.rt3_layout(), 940001, decimal.Context())
    floor = persist(w, R.rt3_layout(), 940002, decimal.Context(rounding=decimal.ROUND_FLOOR))
    prec5 = persist(w, R.rt3_layout(), 940003, decimal.Context(prec=5))
    t = _texts(w, default.eid)
    assert (t["(0)"], t["(0.00)"], t["-0.000"], t["(0.0)"]) == ("0", "0.00", "0.000", "0.0")
    assert t["(1234567890123456789012345678901)"] == "-1.234567890123456789012345679E+30"
    t = _texts(w, floor.eid)
    assert (t["(0)"], t["(0.00)"], t["-0.000"], t["(0.0)"]) == ("-0", "-0.00", "-0.000", "-0.0")
    assert _texts(w, prec5.eid)["(123,456)"] == "-1.2346E+5"
    signed = {str(c["parsed_value"]) for c in floor.res["candidates"] if c["raw_value"] in ("(0)", "(0.00)")}
    assert signed == {"-0", "-0.00"}                                           # the signed zeros reach F5's output
    for s in (default, floor, prec5):
        back = reader.load_extraction(w, s.eid)
        assert R.diff(s.ext, back) is None                                     # Decimal.as_tuple() of every cell
    for s in (default, floor):                                                 # RT-14 under the declared context
        rebuilt, stored = reader.rebuild_f5(w, s.run_id)
        assert f5.content_sha256(rebuilt) == stored == f5.content_sha256(s.res)
    with pytest.raises(EvidenceRefused) as e:
        reader.rebuild_f5(w, prec5.run_id)
    assert e.value.kind == "f5_rebuild_refused"
    w.commit()
    report = verify.verify(w)
    assert [(p["kind"], p.get("run_id")) for p in report["problems"]] == [("f5_rebuild_refused", str(prec5.run_id))]
    # negative control: numeric would have lost the sign of zero and the exponent form
    assert [str(v) for v in q(w, "select %s::numeric, %s::numeric", (decimal.Decimal("-0.00"),
                                                                    decimal.Decimal("-1.2E+30")))[0]] == \
        ["0.00", "-1200000000000000000000000000000"]
    w.rollback()


# ================================================================================================ p5

def _candidate_and_other_cell(layouts):
    """(words, statement position, a cell F5 makes a candidate of, a cell it does not): F5's frozen 0008 stores a
    candidate's bbox as real[] (single precision), so extreme doubles go only on a cell F5 does not persist."""
    for _, words, _, _ in layouts:
        _, ext, res = R.chain(words, 1)
        keys = {(c["statement_index"], c["row_index"], c["column_index"]) for c in res["candidates"]}
        for st in ext.statements:
            cand = [i for i, c in enumerate(st.cells) if (st.index, c.row_index, c.column_index) in keys]
            other = [i for i, c in enumerate(st.cells) if (st.index, c.row_index, c.column_index) not in keys]
            if cand and other:
                return words, st.index, cand[0], other[0]
    raise AssertionError("no layout with both kinds of cells")


def test_p5_ints_floats_and_extremes_survive_bit_exact_and_non_finite_is_refused(db, layouts):
    w = db.conn()
    words, si, ci, oi = _candidate_and_other_cell(layouts)
    cls, ext, _ = R.chain(words, 950001)
    st = ext.statements[si]
    st.cells[ci].coordinates.update({"x0": -0.0, "page_width": 595, "page_height": 842.0})       # F5's bbox: -0.0
    st.cells[oi].coordinates.update({"x0": -0.0, "y0": 5e-324, "x1": 1.7976931348623157e308, "y1": 2 ** 70})
    st.columns[0].x0, st.columns[0].right_edge, st.columns[0].header_x0 = -0.0, 1e16, 5
    st.rows[0].y0, st.rows[0].y1 = -0.0, 1e-05
    if st.heading_bbox:
        st.heading_bbox["x0"] = -0.0
    res = f5.build(ext, cls)
    f5.attach_timestamps(res, {**R.meta_of(950001), **R.FILING_TS}, R.RETRIEVAL)
    seed_filing(w, 950001)
    s = save_in_persist_order(w, cls, ext, res)
    eid = reader.extraction_id_for(w, {"cse_filing_id": 950001, "document_sha256": ext.document_sha256,
                                       "word_extractor": ext.extractor, "f4_extractor_version": ext.extractor_version,
                                       "classification_id": s.cid})
    back = reader.load_extraction(w, eid)
    assert R.diff(ext, back) is None
    bst = back.statements[si]
    got = bst.cells[oi].coordinates
    assert struct.pack(">d", got["x0"]) == struct.pack(">d", -0.0) and type(got["y1"]) is int
    assert struct.pack(">d", got["y0"]) == struct.pack(">d", 5e-324) and got["x1"] == 1.7976931348623157e308
    assert type(bst.cells[ci].coordinates["page_width"]) is int
    assert type(bst.columns[0].header_x0) is int and struct.pack(">d", bst.rows[0].y0) == struct.pack(">d", -0.0)
    rebuilt, stored = reader.rebuild_f5(w, s.run_id)
    assert f5.content_sha256(rebuilt) == stored == f5.content_sha256(res)
    for bad in (float("nan"), float("inf"), float("-inf")):
        _, ext2, _ = R.chain(a_layout_with_cells(layouts), 950002)
        ext2.statements[si].cells[oi].coordinates["x0"] = bad
        with pytest.raises(UnsupportedValue, match="non-finite"):
            store.PostgresF4Store(w).save(ext2, s.cid, cls)
    assert f4_counts(w, 950002)["extractions"] == 0
    # negative controls: float8 turns an int into a float; plain jsonb loses -0.0 and turns 1e16 into an int
    assert type(q(w, "select %s::float8", (595,))[0][0]) is float
    j = q(w, "select %s::jsonb", (json.dumps([-0.0, 1e16]),))[0][0]
    assert struct.pack(">d", float(j[0])) != struct.pack(">d", -0.0) and type(j[1]) is int
    w.rollback()


# ================================================================================================ p6 / p7

def test_p6_a_refused_value_writes_nothing_and_leaves_the_transaction_usable(db, layouts):
    w = db.conn()
    words = a_layout_with_cells(layouts)
    cls, ext, res = R.chain(words, 960001)
    seed_filing(w, 960001)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    breakers = {
        "nul header": lambda e: R.set_column_header(e, *next((i, c.column_index) for i, s in enumerate(e.statements)
                                                            for c in s.cells), "a\x00b"),
        "surrogate label": lambda e: R.set_row_label(e, *next((i, c.row_index) for i, s in enumerate(e.statements)
                                                             for c in s.cells), "a\ud800b"),
        "reserved key": lambda e: e.page_trust.append({"$f": "1.0"}),
        "tuple": lambda e: setattr(e, "status_reasons", tuple(e.status_reasons)),
        "nan": lambda e: R.cells(e)[0].coordinates.__setitem__("y1", float("nan")),
        "wrong document": lambda e: setattr(e, "document_sha256", "f" * 64),
    }
    for name, breaker in breakers.items():
        bad = copy.deepcopy(ext)
        breaker(bad)
        with pytest.raises(UnsupportedValue):
            store.PostgresF4Store(w).save(bad, cid, cls)
        assert f4_counts(w)["extractions"] == 0, name
        assert q(w, "select count(*) from report_document_classifications where id = %s", (cid,)) == [(1,)], name
    assert store.PostgresF4Store(w).save(ext, cid, cls) == "inserted"         # the same transaction goes on
    w.rollback()
    assert f4_counts(w)["extractions"] == 0 and q(w, "select count(*) from report_document_classifications") == [(0,)]


def test_p7_a_rerun_is_already_present_and_different_content_is_refused(db, layouts):
    w = db.conn()
    s = persist(w, a_layout_with_cells(layouts), 970001)
    before = f4_counts(w)
    assert store.PostgresF4Store(w).save(copy.deepcopy(s.ext), s.cid, s.cls) == "already_present"
    changed = copy.deepcopy(s.ext)
    R.cells(changed)[0].raw_value += " "
    with pytest.raises(ConflictingEvidence):
        store.PostgresF4Store(w).save(changed, s.cid, s.cls)
    w.commit()
    assert f4_counts(w) == before


# ================================================================================================ p8

def test_p8_a_failure_inside_the_store_rolls_back_its_savepoint_and_the_caller_decides(db, layouts, monkeypatch):
    import psycopg2
    import psycopg2.extras
    w = db.conn()
    cls, ext, _ = R.chain(a_layout_with_cells(layouts), 980001)
    seed_filing(w, 980001)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    real = psycopg2.extras.execute_values

    def null_in_cells(cur, sql, argslist, **kw):                    # a NOT NULL violation after the columns and rows
        if "financial_f4_cells" in sql:
            argslist = [list(a) for a in argslist]
            argslist[-1][codec.CELL_FIELDS.index("raw_value") + 1] = None
        return real(cur, sql, argslist, **kw)
    monkeypatch.setattr(store.psycopg2.extras, "execute_values", null_in_cells)
    with pytest.raises(psycopg2.errors.NotNullViolation):
        store.PostgresF4Store(w).save(ext, cid, cls)
    assert f4_counts(w)["extractions"] == 0 and q(w, "select 1") == [(1,)]    # savepoint gone, transaction usable
    monkeypatch.setattr(store.psycopg2.extras, "execute_values", real)
    real_load = reader.load_extraction

    def altered(conn, eid):
        back = real_load(conn, eid)
        R.cells(back)[0].confidence = "altered"
        return back
    monkeypatch.setattr(reader, "load_extraction", altered)
    with pytest.raises(EvidenceRefused) as e:
        store.PostgresF4Store(w).save(ext, cid, cls)
    assert e.value.kind == "corrupt" and f4_counts(w)["extractions"] == 0
    monkeypatch.setattr(reader, "load_extraction", real_load)
    assert q(w, "select count(*) from report_document_classifications where id = %s", (cid,)) == [(1,)]
    w.rollback()                                                               # the caller's decision
    assert q(w, "select count(*) from report_document_classifications") == [(0,)]


# ================================================================================================ p9

def test_p9_without_0018_the_store_refuses_before_any_insert_and_0017_upgrades_forward(cluster, layouts):
    name = f"rf4_{uuid.uuid4().hex[:10]}"
    cluster.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", f"dbname={name}", "-f", S.BOOTSTRAP)
    m = cluster.connect(dbname=name, user="cse_migrator")
    out = mig.apply(m, mig.discover(S.MIGDIR), target=17, log=lambda _: None)
    assert out["applied"][-1] == "0017_f8_asof_configuration.sql"
    d = Db(cluster, name)
    try:
        w = d.conn()
        cls, ext, res = R.chain(a_layout_with_cells(layouts), 990001)
        seed_filing(w, 990001)
        PostgresClassificationStore(w).save(cls, 1000)
        cid = classification_id(w, cls)
        with pytest.raises(SchemaUnavailable, match="0018"):
            store.PostgresF4Store(w).save(ext, cid, cls)
        w.rollback()
        problems = preflight.object_problems(w)
        assert problems and all("missing" in p for p in problems)
        assert preflight.ledger_check(m) == {"status": "failed", "read_as": "cse_owner",
                                             "problems": [f"{preflight.MIGRATION} is not in the migration ledger"]}
        assert preflight.ledger_check(w)["status"] == "unverifiable"
        assert mig.apply(m, mig.discover(S.MIGDIR), log=lambda _: None)["applied"] == [preflight.MIGRATION]
        assert preflight.object_problems(w) == [] and preflight.object_problems(d.reader()) == []
        assert preflight.report(m)["clean"]
        s = persist(w, a_layout_with_cells(layouts), 990002)
        assert s.f4 == "inserted"
    finally:
        m.close()
        d.close()


def test_p9_an_edited_0018_in_the_checkout_is_a_preflight_problem(tmp_path):
    copy_dir = tmp_path / "supabase" / "migrations"
    copy_dir.mkdir(parents=True)
    src = os.path.join(S.MIGDIR, preflight.MIGRATION)
    text = open(src, encoding="utf-8").read()
    (copy_dir / preflight.MIGRATION).write_text(text + "\n-- edited\n", encoding="utf-8")
    assert preflight.file_problems(str(tmp_path)) == [f"migration {preflight.MIGRATION} differs from its pinned hash"]
    os.remove(copy_dir / preflight.MIGRATION)
    assert preflight.file_problems(str(tmp_path)) == [f"migration {preflight.MIGRATION} is missing from the checkout"]


# ================================================================================================ p10

def test_p10_concurrent_writers_of_one_key_leave_one_set_of_rows(db, layouts):
    a, b = db.conn(), db.conn()
    cls, ext, _ = R.chain(a_layout_with_cells(layouts), 995001)
    seed_filing(a, 995001)
    PostgresClassificationStore(a).save(cls, 1000)
    a.commit()
    cid = classification_id(a, cls)
    assert store.PostgresF4Store(a).save(ext, cid, cls) == "inserted"       # a holds the key, uncommitted
    got = {}

    def second():
        try:
            got["b"] = store.PostgresF4Store(b).save(copy.deepcopy(ext), cid, cls)
            b.commit()
        except Exception as exc:  # noqa: BLE001 - reported below
            got["b"] = exc
    t = threading.Thread(target=second)
    t.start()
    t.join(1.0)
    assert t.is_alive()                                                       # b waits on a's uncommitted key
    a.commit()
    t.join(30)
    assert not t.is_alive() and got["b"] == "already_present"
    assert f4_counts(a)["extractions"] == 1
    assert f4_counts(a)["cells"] == len(R.cells(ext))


# ================================================================================================ p11

def test_p11_verify_reports_every_kind_of_bad_evidence_read_only_as_the_reader(db, layouts):
    w = db.conn()
    words = a_layout_with_cells(layouts)
    good = persist(w, words, 996001)
    tampered = {k: persist(w, words, 996100 + i) for i, k in enumerate(("cell", "row_gone", "version", "f3", "f5"))}
    cls, ext, res = R.chain(words, 996200)                                      # an F5 run saved before R-F4
    seed_filing(w, 996200)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    _, pre_run = PostgresCandidateStore(w).save(res, cid, PostgresIssuerStore(w).link_filing(996200))
    w.commit()
    assert verify.verify(db.reader())["problems"] == [{"kind": "missing", "run_id": str(pre_run),
                                                       "detail": f"F5 run {pre_run} has no saved F4 extraction"}]
    su = db.su()
    q(su, "set session_replication_role = replica")                              # superuser only: triggers off
    q(su, "update financial_f4_cells k set raw_value = raw_value || '!' from financial_f4_statements s where "
          "s.id = k.statement_id and s.extraction_id = %s and k.seq = 0", (tampered["cell"].eid,))
    q(su, "delete from financial_f4_cells k using financial_f4_statements s where s.id = k.statement_id and "
          "s.extraction_id = %s and k.seq = (select max(seq) from financial_f4_cells where statement_id = s.id)",
      (tampered["row_gone"].eid,))
    q(su, "update financial_f4_extractions set store_version = 'f4.store.99' where id = %s", (tampered["version"].eid,))
    q(su, "update report_statement_periods set role = 'unknown' where classification_id = %s and role <> 'unknown'",
      (tampered["f3"].cid,))
    q(su, "update financial_extraction_runs set content_sha256 = %s where id = %s", ("0" * 64, tampered["f5"].run_id))
    q(su, "set session_replication_role = origin")
    report = verify.verify(db.reader())
    kinds = {}
    for p in report["problems"]:
        kinds.setdefault(p["kind"], set()).add(p.get("extraction_id") or p.get("run_id"))
    assert str(tampered["cell"].eid) in kinds["corrupt"]
    assert str(tampered["row_gone"].eid) in kinds["incomplete"]
    assert str(tampered["version"].eid) in kinds["version_incompatible"]
    assert str(tampered["f3"].eid) in kinds["corrupt"]                          # the F3 input hash
    assert str(tampered["f5"].run_id) in kinds["f5_mismatch"]
    assert str(pre_run) in kinds["missing"]
    assert not any(str(good.eid) in v or str(good.run_id) in v for v in kinds.values())
    assert set(kinds) == {"corrupt", "incomplete", "version_incompatible", "f5_mismatch", "missing"}
    with pytest.raises(ValueError, match="no open transaction"):
        rd = db.reader()
        q(rd, "select 1")
        verify.verify(rd)


# ================================================================================================ p12

def f4_inserts(c):
    """Rows inserted into R-F4's tables by this backend, rolled-back savepoints included: pg_stat_xact_user_tables
    counts an insert when it happens, not when it commits. It may also hold earlier transactions' counts that are not
    flushed yet (PostgreSQL 15+), so a test compares it before and after, never with 0."""
    return q(c, "select coalesce(sum(n_tup_ins), 0) from pg_stat_xact_user_tables where relname like "
                "'financial\\_f4\\_%%'")[0][0]


def trigger_def(su, table, trigger):
    return q(su, "select pg_get_triggerdef(oid) from pg_trigger where tgrelid = %s::regclass and tgname = %s",
             (f"public.{table}", trigger))[0][0]


def as_owner(su, sql):
    q(su, f"set role cse_owner; {sql}; reset role")


def restore_after_rollback(w, su, restore_sql):
    """Undo a schema change made for a test. The worker's transaction is rolled back first: after a failed assertion it
    may still hold locks on the table, and the restoring ALTER TABLE would wait on them for ever. The restore runs even
    if that rollback fails (a broken connection releases its locks anyway)."""
    try:
        w.rollback()
    finally:
        as_owner(su, restore_sql)


def refused_before_any_insert(w, su, words, fid, match):
    """Save F3 as `_persist` would, then R-F4: SchemaUnavailable, no row inserted into R-F4's tables even inside the
    rolled-back savepoint, and the caller's transaction still usable (its F3 row is there until the caller rolls
    back)."""
    cls, ext, _ = R.chain(words, fid)
    seed_filing(w, fid)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    before = f4_inserts(w)
    with pytest.raises(SchemaUnavailable, match=match):
        store.PostgresF4Store(w).save(ext, cid, cls)
    assert f4_inserts(w) == before
    assert q(w, "select count(*) from report_document_classifications where id = %s", (cid,)) == [(1,)]
    w.rollback()
    assert f4_counts(su, fid)["extractions"] == 0                  # counted as the superuser: SELECT may be revoked


def test_p12_a_broken_trigger_or_privilege_refuses_the_save_before_any_insert(db, layouts):
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    # the positive control: a working save does register its inserts in this counter
    cls, ext, _ = R.chain(words, 960000)
    seed_filing(w, 960000)
    PostgresClassificationStore(w).save(cls, 1000)
    before = f4_inserts(w)
    assert store.PostgresF4Store(w).save(ext, classification_id(w, cls), cls) == "inserted"
    assert f4_inserts(w) > before
    w.rollback()

    guard = trigger_def(su, "financial_f4_extractions", "trg_ff4e_guard")
    append_only = trigger_def(su, "financial_f4_statements", "trg_ff4s_append_only")
    # (break, restore, the store's message, the preflight's message for cse_worker)
    cases = [
        ("alter table financial_f4_cells disable trigger trg_ff4c_append_only",
         "alter table financial_f4_cells enable trigger trg_ff4c_append_only",
         "financial_f4_cells: trigger trg_ff4c_append_only is missing or disabled",
         "financial_f4_cells: trigger trg_ff4c_append_only is missing or disabled"),
        ("alter table financial_f4_rows enable replica trigger trg_ff4r_no_truncate",     # fires only in replica mode
         "alter table financial_f4_rows enable trigger trg_ff4r_no_truncate",
         "financial_f4_rows: trigger trg_ff4r_no_truncate is missing or disabled",
         "financial_f4_rows: trigger trg_ff4r_no_truncate is missing or disabled"),
        ("drop trigger trg_ff4e_guard on financial_f4_extractions", guard,
         "financial_f4_extractions: trigger trg_ff4e_guard is missing or disabled",
         "financial_f4_extractions: trigger trg_ff4e_guard is missing or disabled"),
        ("drop trigger trg_ff4s_append_only on financial_f4_statements; create trigger trg_ff4s_append_only before "
         "update on financial_f4_statements for each row execute function f5_reject_mutation()",  # DELETE let through
         f"drop trigger trg_ff4s_append_only on financial_f4_statements; {append_only}",
         r"trg_ff4s_append_only runs f5_reject_mutation \(tgtype 19\), expected f5_reject_mutation \(tgtype 27\)",
         "financial_f4_statements: trigger trg_ff4s_append_only runs f5_reject_mutation (tgtype 19), expected "
         "f5_reject_mutation (tgtype 27)"),
        ("revoke insert on financial_f4_rows from cse_worker", "grant insert on financial_f4_rows to cse_worker",
         "financial_f4_rows: the current role lacks INSERT", "cse_worker lacks INSERT on financial_f4_rows"),
        ("revoke select on financial_f4_cells from cse_worker", "grant select on financial_f4_cells to cse_worker",
         "financial_f4_cells: the current role lacks SELECT", "cse_worker lacks SELECT on financial_f4_cells"),
    ]
    for n, (break_sql, restore_sql, match, reported) in enumerate(cases):
        as_owner(su, break_sql)
        try:
            refused_before_any_insert(w, su, words, 960001 + n, match)
            assert reported in preflight.object_problems(w)
        finally:
            restore_after_rollback(w, su, restore_sql)
    assert preflight.object_problems(su) == []                        # every change was undone, exactly
    assert q(su, "select count(*) from financial_f4_extractions") == [(0,)]


def test_p12_the_check_runs_on_every_save_and_never_caches_a_success(db, layouts):
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    one = store.PostgresF4Store(w)                                    # one store, one connection, the whole test
    cls, ext, _ = R.chain(words, 961001)
    seed_filing(w, 961001)
    PostgresClassificationStore(w).save(cls, 1000)
    assert one.save(ext, classification_id(w, cls), cls) == "inserted"
    w.commit()
    cls2, ext2, _ = R.chain(words, 961002)
    seed_filing(w, 961002)
    as_owner(su, "alter table financial_f4_extractions disable trigger trg_ff4e_guard")
    try:
        PostgresClassificationStore(w).save(cls2, 1000)
        with pytest.raises(SchemaUnavailable, match="trg_ff4e_guard is missing or disabled"):
            one.save(ext2, classification_id(w, cls2), cls2)
        w.rollback()
    finally:
        restore_after_rollback(w, su, "alter table financial_f4_extractions enable trigger trg_ff4e_guard")
    PostgresClassificationStore(w).save(cls2, 1000)
    assert one.save(ext2, classification_id(w, cls2), cls2) == "inserted"
    w.commit()
    assert f4_counts(w, 961002)["extractions"] == 1


# ================================================================================================ p13

def migrator(db):
    """A connection as P1's migration role (NOINHERIT; may SET ROLE cse_owner), as `--migration-role` connects."""
    c = db.cluster.connect(dbname=db.name, user="cse_migrator")
    db._open.append(c)
    return c


def test_p13_the_ledger_is_verified_only_by_a_role_that_can_read_it(db):
    w, rd, su, m = db.conn(), db.reader(), db.su(), migrator(db)
    assert preflight.ledger_check(su) == {"status": "verified", "problems": [], "read_as": "postgres"}
    assert preflight.ledger_check(rd) == {"status": "verified", "problems": [], "read_as": "cse_reader"}
    assert preflight.ledger_check(m) == {"status": "verified", "problems": [], "read_as": "cse_owner"}
    assert q(m, "select current_user") == [("cse_migrator",)]        # the owner role ended with the transaction
    m.rollback()
    got = preflight.ledger_check(w)
    assert got["status"] == "unverifiable" and got["read_as"] is None
    assert got["problems"][0].startswith("role cse_worker cannot read ops.schema_migrations")
    assert "NOT verified" in got["problems"][0] and "--migration-role" in got["problems"][0]
    assert q(w, "select has_schema_privilege(current_user, 'ops', 'USAGE')") == [(False,)]       # least privilege
    w.rollback()
    r = preflight.report(w)
    assert r["file"] == [] and r["objects"] == [] and not r["clean"]
    # one run as the migration role checks the file, both roles' objects and the ledger
    assert preflight.report(m) == {"file": [], "objects": [], "roles": ["cse_worker", "cse_reader"], "clean": True,
                                   "ledger": {"status": "verified", "problems": [], "read_as": "cse_owner"}}


def test_p13_a_tampered_or_misnumbered_ledger_entry_fails(db):
    su, m = db.su(), migrator(db)
    pinned = q(su, "select sha256 from ops.schema_migrations where version = 18")[0][0]
    assert pinned == preflight.MIGRATION_SHA256
    q(su, "set session_replication_role = replica")                  # the ledger is append-only by trigger
    try:
        q(su, "update ops.schema_migrations set sha256 = %s where version = 18", ("e" * 64,))
        got = preflight.ledger_check(m)
        assert got["status"] == "failed" and "e" * 64 in got["problems"][0]
        assert not preflight.report(m)["clean"]
        q(su, "update ops.schema_migrations set version = 1018, sha256 = %s where version = 18", (pinned,))
        got = preflight.ledger_check(m)
        assert got["status"] == "failed" and "1018" in got["problems"][0]   # found by its filename, wrong version
    finally:
        q(su, "update ops.schema_migrations set version = 18, sha256 = %s where filename = %s",
          (pinned, preflight.MIGRATION))
        q(su, "set session_replication_role = origin")
    assert preflight.ledger_check(m)["status"] == "verified"


def test_p13_object_problems_are_reported_for_the_named_roles(db):
    su, m = db.su(), migrator(db)
    decimal_def = q(su, "select pg_get_constraintdef(oid) from pg_constraint where conname = 'chk_ff4c_decimal'")[0][0]
    cases = [
        ("grant update on financial_f4_cells to cse_worker", "revoke update on financial_f4_cells from cse_worker",
         "cse_worker has UPDATE on financial_f4_cells: the table is append-only"),
        ("grant insert on financial_f4_rows to cse_reader", "revoke insert on financial_f4_rows from cse_reader",
         "cse_reader has INSERT on financial_f4_rows: the table is append-only"),
        ("revoke select on financial_f4_statements from cse_reader",
         "grant select on financial_f4_statements to cse_reader", "cse_reader lacks SELECT on financial_f4_statements"),
        ("grant execute on function financial_f4_extraction_guard() to cse_worker",
         "revoke execute on function financial_f4_extraction_guard() from cse_worker",
         "cse_worker has EXECUTE on the trigger function financial_f4_extraction_guard"),
        ("alter table financial_f4_cells drop constraint chk_ff4c_decimal",
         f"alter table financial_f4_cells add constraint chk_ff4c_decimal {decimal_def}",
         "financial_f4_cells: constraint chk_ff4c_decimal is missing"),
        ("alter table financial_f4_columns disable trigger trg_ff4k_no_truncate",
         "alter table financial_f4_columns enable trigger trg_ff4k_no_truncate",
         "financial_f4_columns: trigger trg_ff4k_no_truncate is missing or disabled"),
    ]
    for break_sql, restore_sql, want in cases:
        as_owner(su, break_sql)
        try:
            got = preflight.report(m)
            assert want in got["objects"] and not got["clean"], (want, got["objects"])
            assert got["file"] == [] and got["ledger"]["status"] == "verified"     # the three checks stay apart
        finally:
            as_owner(su, restore_sql)
    assert preflight.report(m)["clean"]


def acls(su):
    """Every table- and column-level ACL on R-F4's tables, to compare before and after a test that changes grants."""
    return q(su, "select c.relname, '', coalesce(c.relacl::text, '') from pg_class c where c.relkind = 'r' and "
                 "c.relname like 'financial\\_f4\\_%' union all select c.relname, a.attname, a.attacl::text from "
                 "pg_attribute a join pg_class c on c.oid = a.attrelid where c.relname like 'financial\\_f4\\_%' and "
                 "a.attacl is not null order by 1, 2")


def test_p13_a_forbidden_privilege_granted_on_single_columns_is_reported(db):
    su, m = db.su(), migrator(db)
    before = acls(su)
    assert preflight.report(m)["clean"]
    # (what is granted, to whom, the table and privilege, what the preflight must report)
    cases = [
        ("insert (raw_value) on financial_f4_cells", "cse_reader", ("financial_f4_cells", "INSERT"),
         "cse_reader has INSERT on column(s) raw_value of financial_f4_cells: the table is append-only"),
        ("insert (label_raw, kind) on financial_f4_rows", "cse_reader", ("financial_f4_rows", "INSERT"),
         "cse_reader has INSERT on column(s) label_raw, kind of financial_f4_rows: the table is append-only"),
        ("update (status) on financial_f4_rows", "cse_worker", ("financial_f4_rows", "UPDATE"),
         "cse_worker has UPDATE on column(s) status of financial_f4_rows: the table is append-only"),
        ("update (raw_value) on financial_f4_cells", "cse_reader", ("financial_f4_cells", "UPDATE"),
         "cse_reader has UPDATE on column(s) raw_value of financial_f4_cells: the table is append-only"),
    ]
    try:
        for what, role, (table, priv), want in cases:
            as_owner(su, f"grant {what} to {role}")
            try:
                assert q(su, "select has_table_privilege(%s, %s, %s), has_any_column_privilege(%s, %s, %s)",
                         (role, table, priv, role, table, priv)) == [(False, True)]       # column-level only
                got = preflight.report(m)
                assert want in got["objects"] and not got["clean"], (want, got["objects"])
                assert got["file"] == [] and got["ledger"]["status"] == "verified"     # the three checks stay apart
            finally:
                as_owner(su, f"revoke {what} from {role}")
            assert acls(su) == before
    finally:
        for what, role, _, _ in cases:                    # again, idempotently, in case a step above failed
            as_owner(su, f"revoke {what} from {role}")
    assert acls(su) == before and preflight.report(m)["clean"]


def test_p13_the_command_exits_0_clean_1_problem_2_unverifiable(db, monkeypatch, capsys):
    from worker import db as worker_db
    from worker.financial_f4_store import __main__ as cli
    from worker.ops import settings as ops_settings
    monkeypatch.setattr(worker_db, "get_connection", lambda: db.conn())
    assert cli.main(["preflight"]) == 2                               # nothing wrong found, the ledger NOT verified
    out = json.loads(capsys.readouterr().out)
    assert out["ledger"]["status"] == "unverifiable" and out["clean"] is False
    monkeypatch.setattr(ops_settings, "load", lambda: SimpleNamespace(owner_role="cse_owner", db_user=None))
    monkeypatch.setattr(ops_settings, "connect", lambda s, user=None: db.cluster.connect(dbname=db.name, user=user))
    assert cli.main(["preflight", "--migration-role"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["clean"] and out["ledger"]["read_as"] == "cse_owner"
    su = db.su()
    as_owner(su, "alter table financial_f4_cells disable trigger trg_ff4c_append_only")
    try:
        assert cli.main(["preflight", "--migration-role"]) == 1
        assert cli.main(["preflight"]) == 1                           # a found problem outranks 'unverifiable'
    finally:
        as_owner(su, "alter table financial_f4_cells enable trigger trg_ff4c_append_only")
    capsys.readouterr()


# ================================================================================================ p14

def first_statement_with_cells(ext):
    for i, st in enumerate(ext.statements):
        if st.cells and st.rows and st.columns:
            return i
    raise AssertionError("no statement with cells")


def test_p14_integer_boundaries_are_stored_exactly_and_one_past_is_refused_before_any_insert(db, layouts):
    w = db.conn()
    cls, ext, _ = R.chain(a_layout_with_cells(layouts), 975001)
    seed_filing(w, 975001)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    si = first_statement_with_cells(ext)
    bad = copy.deepcopy(ext)
    bad.statements[si].rows[0].line_count = 2 ** 40                  # the audit's case
    before = f4_inserts(w)
    with pytest.raises(UnsupportedValue, match="line_count: 1099511627776 is outside the integer column range"):
        store.PostgresF4Store(w).save(bad, cid, cls)
    assert f4_inserts(w) == before and q(w, "select 1") == [(1,)]   # nothing written; the transaction is usable
    assert q(w, "select count(*) from report_document_classifications where id = %s", (cid,)) == [(1,)]
    edge = copy.deepcopy(ext)                                         # every boundary, none of them copied to a cell
    st = edge.statements[si]
    edge.page_count = 2 ** 31 - 1
    st.heading_page, st.scale, st.first_page = -2 ** 31, 2 ** 63 - 1, 2 ** 31 - 1
    st.rows[0].line_count = 2 ** 31 - 1
    st.cells[0].scale = -2 ** 63
    assert store.PostgresF4Store(w).save(edge, cid, cls) == "inserted"
    w.commit()
    eid = reader.extraction_id_for(w, {"cse_filing_id": 975001, "document_sha256": edge.document_sha256,
                                       "word_extractor": edge.extractor, "f4_extractor_version": edge.extractor_version,
                                       "classification_id": cid})
    assert R.diff(edge, reader.load_extraction(w, eid)) is None
    w.rollback()


# ================================================================================================ p15

def test_p15_an_oversized_number_in_saved_evidence_is_corrupt_and_the_others_still_verify(db, layouts):
    w = db.conn()
    text_bad, json_bad, good = (persist(w, a_layout_with_cells(layouts), 976001 + i) for i in range(3))
    su = db.su()
    q(su, "set session_replication_role = replica")                  # superuser only: the append-only triggers off
    try:
        q(su, "update financial_f4_rows r set y0 = %s from financial_f4_statements s where s.id = r.statement_id "
              "and s.extraction_id = %s and r.seq = 0", ("1" * 5000, text_bad.eid))           # a number column
        q(su, "update financial_f4_cells k set coordinates = jsonb_set(k.coordinates, '{page}', "
              "('1' || repeat('0', 5000))::jsonb) from financial_f4_statements s where s.id = k.statement_id and "
              "s.extraction_id = %s and k.seq = 0", (json_bad.eid,))                         # inside jsonb
    finally:
        q(su, "set session_replication_role = origin")
    report = verify.verify(db.reader())                               # completes: nothing escapes as a ValueError
    by = {}
    for p in report["problems"]:
        by.setdefault(p.get("extraction_id") or p.get("run_id"), []).append(p)
    for s, why in ((text_bad, "exceeds 4300 digits"), (json_bad, "its saved evidence cannot be decoded: ValueError")):
        assert [p["kind"] for p in by[str(s.eid)]] == ["corrupt"] and why in by[str(s.eid)][0]["detail"], by
        assert [p["kind"] for p in by[str(s.run_id)]] == ["corrupt"]                       # its F5 run cannot rebuild
    assert str(good.eid) not in by and str(good.run_id) not in by                          # the others still verified
    assert report["extractions"] == report["runs"] == 3 and report["rebuilt"] == 1 and not report["clean"]


# ================================================================================================ p16

@contextlib.contextmanager
def int_digit_limit(n):
    """sys.set_int_max_str_digits(n) for the block (0 = unlimited), restored on exit whatever happens inside."""
    old = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(n)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(old)


def test_p16_long_integers_saved_without_a_limit_read_back_under_the_lowest_limit(db, layouts):
    started_with = sys.get_int_max_str_digits()
    w = db.conn()
    cls, ext, _ = R.chain(a_layout_with_cells(layouts), 977001)
    seed_filing(w, 977001)
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    si = first_statement_with_cells(ext)
    st = ext.statements[si]
    st.columns[0].x0 = 10 ** 999 + 7                                  # a number column (text), 1000 digits
    st.rows[0].y1 = -(10 ** 4300 - 1)                                 # the longest the contract allows
    st.reasons = st.reasons + [10 ** 2000 + 3, {"k": [-(10 ** 4300 - 1)]}]       # inside jsonb
    with int_digit_limit(0):                                          # a permissive writer
        assert store.PostgresF4Store(w).save(ext, cid, cls) == "inserted"
        w.commit()
    key = {"cse_filing_id": 977001, "document_sha256": ext.document_sha256, "word_extractor": ext.extractor,
           "f4_extractor_version": ext.extractor_version, "classification_id": cid}
    try:
        with int_digit_limit(640):                                    # a reader at the lowest limit Python allows
            eid = reader.extraction_id_for(w, key)
            back = reader.load_extraction(w, eid)                     # the real reader: text columns and jsonb
            assert R.diff(ext, back) is None
            w.rollback()
            report = verify.verify(db.reader())
            assert report["clean"] and report["extractions"] == 1, report["problems"]
            assert store.PostgresF4Store(w).save(copy.deepcopy(ext), cid, cls) == "already_present"
            w.rollback()
    finally:
        assert sys.get_int_max_str_digits() == started_with                  # restored to what the test found


# ================================================================================================ p17

def test_p17_a_rerun_is_already_present_only_when_the_saved_extraction_reads_back_intact(db, layouts):
    w, su = db.conn(), db.su()
    s = persist(w, a_layout_with_cells(layouts), 978001)
    rerun = store.PostgresF4Store(w)
    assert rerun.save(copy.deepcopy(s.ext), s.cid, s.cls) == "already_present"         # intact: as before
    w.rollback()
    intact = f4_counts(su)
    sid = q(su, "select id from financial_f4_statements where extraction_id = %s order by statement_index limit 1",
            (s.eid,))[0][0]

    def refused(kind):
        """The same save as the original, now refused with `kind`: nothing written, the caller's transaction usable."""
        q(w, "select count(*) from report_document_classifications")    # the caller's own work in this transaction
        before = f4_inserts(w)
        with pytest.raises(EvidenceRefused) as e:
            rerun.save(copy.deepcopy(s.ext), s.cid, s.cls)
        assert e.value.kind == kind, e.value
        assert f4_inserts(w) == before and q(w, "select 1") == [(1,)]
        w.rollback()

    # a row appended after the save, by the worker role itself (INSERT is all append-only allows)
    q(w, "insert into financial_f4_rows (statement_id, seq, row_index, page, label_raw, label_normalized, y0, y1, "
         "line_count, wrapped, kind, status, reasons) values (%s, 9999, 9999, 1, 'x', 'x', '1', '2', 1, false, "
         "'values', 'ok', '[]')", (sid,))
    w.commit()
    try:
        refused("incomplete")
    finally:
        q(su, "set session_replication_role = replica")              # the triggers off: undo the append
        try:
            q(su, "delete from financial_f4_rows where statement_id = %s and seq = 9999", (sid,))
        finally:
            q(su, "set session_replication_role = origin")
    assert f4_counts(su) == intact
    assert rerun.save(copy.deepcopy(s.ext), s.cid, s.cls) == "already_present"
    w.rollback()

    # a cell tampered with after the save
    cell = q(su, "select k.id, k.raw_value from financial_f4_cells k join financial_f4_statements t on t.id = "
                 "k.statement_id where t.extraction_id = %s order by t.statement_index, k.seq limit 1", (s.eid,))[0]
    q(su, "set session_replication_role = replica")
    try:
        q(su, "update financial_f4_cells set raw_value = raw_value || '!' where id = %s", (cell[0],))
        refused("corrupt")
    finally:
        try:
            q(su, "update financial_f4_cells set raw_value = %s where id = %s", (cell[1], cell[0]))
        finally:
            q(su, "set session_replication_role = origin")
    assert f4_counts(su) == intact
    assert rerun.save(copy.deepcopy(s.ext), s.cid, s.cls) == "already_present"
    w.rollback()
    assert verify.verify(db.reader())["clean"]


# ================================================================================================ Stage 2: the callers

def text_of(words):
    """F3's layout text of a synthetic word layer, as rf4_support.chain makes it."""
    return document_text.from_pages(["\n".join(l.rendered for l in se.build_lines(p)) for p in words.pages],
                                    extractor=R.LAYOUT_EXTRACTOR)


class ByDocument:
    """F3's text and F4's word layer of each temporary document, chosen by the filing its PDF names (the HB-4
    harness's distinct PDF per filing, `% filing <id>`): one synthetic layout per filing through the real F2 lifecycle,
    nothing about the callers changed."""

    def __init__(self, layers):
        self.layers = layers

    def fid(self, path):
        with open(path, "rb") as f:
            return int(re.search(rb"% filing (\d+)", f.read()).group(1))

    def words(self, path):
        return self.layers[self.fid(path)]

    def text(self, path):
        return text_of(self.words(path))


class Originals:
    """F4's and F5's outputs as the consumer makes them, captured as made (F4's deep-copied at once, so a later change
    to the object it hands on would show): the originals of RT-14 and RT-15. `mutate(ext)`, if given, changes F4's
    output after the copy (RT-4: a value R-F4 must refuse)."""

    def __init__(self, monkeypatch, mutate=None):
        self.f4, self.f5 = {}, {}
        real_extract, real_build = f5cli.se.extract_document, f5cli.f5.build

        def extract(*a, **k):
            ext = real_extract(*a, **k)
            self.f4.setdefault(ext.filing_id, copy.deepcopy(ext))
            if mutate:
                mutate(ext)
            return ext

        def build(ext, cls):
            res = real_build(ext, cls)
            self.f5.setdefault(ext.filing_id, copy.deepcopy(res))
            return res
        monkeypatch.setattr(f5cli.se, "extract_document", extract)
        monkeypatch.setattr(f5cli.f5, "build", build)


def main_stores(w):
    """Exactly the stores F5's main() builds (four keys): R-F4 is reached through _persist's fallback."""
    return {"conn": w, "classification": PostgresClassificationStore(w), "issuer": PostgresIssuerStore(w),
            "candidates": PostgresCandidateStore(w)}


def run_f5(w, fids, layers, fetcher=None):
    """F5's own run() over `fids` (from report_filings): the real F2 lifecycle against a scripted CDN serving the
    harness's distinct PDF per filing, F3/F4 over each filing's layout, persisted and committed by run()."""
    by = ByDocument(layers)
    fetcher = fetcher or FakeFetcher({H.url_of(R.path_of(f)): _ok(f) for f in fids})
    with TempRoot() as root:
        out = f5cli.run(f5cli.load_filings_from_db(w, fids), temp_root=root, request_delay_seconds=0, fetcher=fetcher,
                        extract_text=by.text, extract_words=by.words, require_poppler=False, stores=main_stores(w))
        assert os.listdir(root) == []
    return out, fetcher


def _ok(fid):
    status, headers, body = H.ok_doc(fid)
    return FakeResp(status, body, headers)


def rows_of(c, fids):
    """(F3 classifications, F4 extractions, F5 runs) of these filings."""
    return tuple(q(c, f"select count(*) from {t} where cse_filing_id = any(%s)", (list(fids),))[0][0]
                 for t in ("report_document_classifications", "financial_f4_extractions", "financial_extraction_runs"))


def assert_rt14_rt15(c, fid, originals, run_id):
    """RT-15: the saved F4 output reads back type-exactly equal to F4's in-memory original. RT-14: F5 rebuilt from the
    saved F3 and F4 evidence under the declared context hashes to both the original in-memory result and the stored."""
    key = q(c, "select cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id from "
               "financial_extraction_runs where id = %s", (run_id,))[0]
    eid = reader.extraction_id_for(c, dict(zip(reader.RUN_KEY, key)))
    assert eid is not None, fid
    d = R.diff(originals.f4[fid], reader.load_extraction(c, eid))
    assert d is None, (fid, d)
    rebuilt, stored = reader.rebuild_f5(c, run_id)
    assert f5.content_sha256(rebuilt) == stored == f5.content_sha256(originals.f5[fid]), fid


def test_p18_rt14_and_rt15_through_f5_run_for_every_synthetic_layout(db, layouts, monkeypatch):
    w = db.conn()
    fids = [981000 + i for i in range(len(layouts))]
    layers = {fid: words for fid, (_, words, _, _) in zip(fids, layouts)}
    for fid in fids:
        seed_filing(w, fid)
    originals = Originals(monkeypatch)
    stored = {}
    for i in range(0, len(fids), f5cli.MAX_FILINGS_PER_RUN):
        out, _ = run_f5(w, fids[i:i + f5cli.MAX_FILINGS_PER_RUN], layers)
        assert out["ok"]
        for r in out["records"]:
            assert (r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) == ("inserted",) * 3, r["retrieval"]
            stored[r["retrieval"]["cse_filing_id"]] = r["stored"]
    assert sorted(stored) == fids and len(fids) >= 45
    for fid in fids:
        assert_rt14_rt15(w, fid, originals, stored[fid]["run_id"])
    w.rollback()
    report = verify.verify(db.reader())
    assert report["clean"] and report["extractions"] == report["runs"] == report["rebuilt"] == len(fids)


def test_p19_a_rerun_is_already_present_for_all_three_and_a_pre_r_f4_run_gets_f4_only_by_downloading_again(
        db, layouts, monkeypatch):
    w = db.conn()
    words = a_layout_with_cells(layouts)
    fids = [982001, 982002]
    for fid in fids:
        seed_filing(w, fid)
    layers = dict.fromkeys(fids, words)
    out, fetcher = run_f5(w, fids, layers)
    assert all(r["stored"]["f4"] == "inserted" for r in out["records"])
    before = (rows_of(w, fids), f4_counts(w))
    out, fetcher2 = run_f5(w, fids, layers)                          # the same batch again: downloaded again
    assert [(r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) for r in out["records"]] == \
        [("already_present",) * 3] * 2 and len(fetcher2.calls) == 2
    assert (rows_of(w, fids), f4_counts(w)) == before                # nothing written twice
    # a run saved before R-F4 (F3 + link + F5, as the pre-R-F4 _persist did): verify finds it missing; a governed
    # re-download through run() then saves F4, and F3 and F5 are already present
    pre = 982003
    seed_filing(w, pre)
    with TempRoot() as root:
        made = {}
        f5cli.run(f5cli.load_filings_from_db(w, [pre]), temp_root=root, request_delay_seconds=0,
                  fetcher=FakeFetcher({H.url_of(R.path_of(pre)): _ok(pre)}), extract_text=ByDocument({pre: words}).text,
                  extract_words=ByDocument({pre: words}).words, require_poppler=False,
                  on_result=lambda fid, res, cls: made.setdefault(fid, (res, cls)))
    res, cls = made[pre]
    PostgresClassificationStore(w).save(cls, 1000)
    cid = classification_id(w, cls)
    PostgresCandidateStore(w).save(res, cid, PostgresIssuerStore(w).link_filing(pre))
    w.commit()
    assert rows_of(w, [pre]) == (1, 0, 1)
    assert [p["kind"] for p in verify.verify(db.reader())["problems"]] == ["missing"]
    out, fetcher3 = run_f5(w, [pre], {pre: words})
    [r] = out["records"]
    assert (r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) == ("already_present", "inserted",
                                                                        "already_present") and len(fetcher3.calls) == 1
    assert rows_of(w, [pre]) == (1, 1, 1) and verify.verify(db.reader())["clean"]


def test_p20_rt3_decimals_through_f5_run_under_three_extraction_contexts(db, monkeypatch):
    """Design section 9, RT-3 with run() wrapped in a test-only context: default (unsigned zeros), ROUND_FLOOR (signed
    zeros) and precision 5 (an exponent form F5's consistency check later refuses under the declared context)."""
    w = db.conn()
    words = R.rt3_layout()
    contexts = {983001: decimal.Context(), 983002: decimal.Context(rounding=decimal.ROUND_FLOOR),
                983003: decimal.Context(prec=5)}
    originals = Originals(monkeypatch)
    stored = {}
    for fid, ctx in contexts.items():
        seed_filing(w, fid)
        with decimal.localcontext(ctx):
            out, _ = run_f5(w, [fid], {fid: words})
        [r] = out["records"]
        assert r["stored"]["f4"] == "inserted", r
        stored[fid] = r["stored"]
    signed = [c.parsed_value for c in R.cells(originals.f4[983002]) if c.parsed_value is not None]
    assert any(v.is_zero() and v.is_signed() for v in signed)               # the floor context's signed zeros
    for fid in (983001, 983002):
        assert_rt14_rt15(w, fid, originals, stored[fid]["run_id"])
    key = q(w, "select cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id from "
               "financial_extraction_runs where id = %s", (stored[983003]["run_id"],))[0]
    eid = reader.extraction_id_for(w, dict(zip(reader.RUN_KEY, key)))
    assert R.diff(originals.f4[983003], reader.load_extraction(w, eid)) is None             # stored exactly
    with pytest.raises(EvidenceRefused) as e:
        reader.rebuild_f5(w, stored[983003]["run_id"])
    assert e.value.kind == "f5_rebuild_refused"


def _nan_coordinate(ext):
    for st in ext.statements:
        for cell in st.cells:
            cell.coordinates["x0"] = float("nan")
            return


@pytest.mark.parametrize("fault", ["scripted_f4_failure", "nan_in_f4_output"])
def test_p21_a_failure_at_filing_k_leaves_runs_batch_uncommitted_and_a_rerun_saves_every_filing_once(
        db, layouts, monkeypatch, fault):
    w = db.conn()
    words = a_layout_with_cells(layouts)
    fids = [984001, 984002, 984003]
    for fid in fids:
        seed_filing(w, fid)
    layers = dict.fromkeys(fids, words)
    real_save = store.PostgresF4Store.save
    calls = []
    with monkeypatch.context() as m:
        if fault == "scripted_f4_failure":
            def save(self, ext, cid, cls):
                calls.append(ext.filing_id)
                if len(calls) == 2:                                         # filing k = 2
                    raise ValueError("scripted: the F4 save fails at filing k")
                return real_save(self, ext, cid, cls)
            m.setattr(store.PostgresF4Store, "save", save)
            expected = ValueError
        else:
            Originals(m, mutate=lambda ext: ext.filing_id == fids[1] and _nan_coordinate(ext))
            expected = UnsupportedValue
        with pytest.raises(expected):
            run_f5(w, fids, layers)
    w.rollback()                                                            # what main()'s finally does (close)
    assert rows_of(w, fids) == (0, 0, 0) and f4_counts(w)["extractions"] == 0    # filing 1 included: one batch
    out, _ = run_f5(w, fids, layers)
    assert [(r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) for r in out["records"]] == \
        [("inserted",) * 3] * 3
    assert rows_of(w, fids) == (3, 3, 3) and verify.verify(db.reader())["clean"]


# ------------------------------------------------------------------------------------------------ HB-4's slice

@pytest.fixture
def env(cluster, tmp_path, monkeypatch):
    e = H.Env(cluster, tmp_path, monkeypatch)
    yield e
    e.close()


def hb4_slice(env, cdn, by, **settings):
    """One HB-4 document slice, run and closed; (slice, error or None)."""
    ds = env.slice(cdn, extract_text=by.text, extract_words=by.words, **settings)
    err = None
    try:
        with ds:
            ds.run()
    except BaseException as exc:  # noqa: BLE001 - the test inspects it
        err = exc
    return ds, err


def open_hb4(env, fids, **arming):
    H.ready(env, [H.feed_item(fid, H.path_of(fid)) for fid in fids], **arming)
    return env.conn()


def test_p22_rt14_and_rt15_through_hb4s_slice_with_f4_in_the_filings_one_transaction(env, layouts, monkeypatch):
    fids = [985001, 985002, 985003, 985004]
    picked = [l for l in layouts if R.cells(R.chain(l[1], 1)[1])][:4]
    layers = {fid: words for fid, (_, words, _, _) in zip(fids, picked)}
    w = open_hb4(env, fids)
    su = env.conn("postgres")                                   # test-only: the top-level transaction of each write
    # No trigger goes on R-F4's tables: the in-band check refuses any it does not expect (audit Finding 5). The F5
    # run's trigger also records the F4 extraction of the same key: visible to it and inserted by a transaction still
    # in progress, so by this one (another transaction's uncommitted rows are invisible). Its xmin is a savepoint's
    # subtransaction, which txid_status reports as 'in progress' while the top-level transaction runs.
    H.q(su, "create table rf4_tx (relname text, txid bigint, status text)")     # H.q commits: the slice must see it
    H.q(su, "create function rf4_tx_log() returns trigger language plpgsql as $$ begin "
            "insert into rf4_tx values (tg_table_name, txid_current(), null); "
            "if tg_table_name = 'financial_extraction_runs' then "
            "insert into rf4_tx select 'financial_f4_extractions', txid_current(), "
            "txid_status((txid_current() >> 32 << 32) + x.xmin::text::bigint) from financial_f4_extractions x "
            "where x.cse_filing_id = new.cse_filing_id and x.document_sha256 = new.document_sha256 and "
            "x.word_extractor = new.word_extractor and x.f4_extractor_version = new.f4_extractor_version and "
            "x.classification_id = new.classification_id; "
            "end if; return new; end $$")
    H.q(su, "grant insert on rf4_tx to cse_worker")
    H.q(su, "grant execute on function rf4_tx_log() to cse_worker")
    for t, cond in (("financial_extraction_runs", ""), ("backfill_item_events", "when (new.state = 'persisted') ")):
        H.q(su, f"create trigger rf4_tx_{t} after insert on {t} for each row {cond}execute function rf4_tx_log()")
    originals = Originals(monkeypatch)
    cdn = H.ScriptedCDN(env.clock, {H.url_of(H.path_of(f)): H.ok_doc(f) for f in fids})
    ds, err = hb4_slice(env, cdn, ByDocument(layers))
    assert err is None and len(cdn.calls) == len(fids)
    for fid in fids:
        it = H.item_of(w, fid)
        assert H.state(w, it) == "persisted", fid
        ev = ledger.events(w, it["id"])[-1]
        assert ev["details"]["f3"] == "inserted" and ev["details"]["f5"] == "inserted"
        assert_rt14_rt15(w, fid, originals, ev["f5_run_id"])
    by_tx, f4_status = {}, []
    for rel, tx, status in H.q(su, "select relname, txid, status from rf4_tx"):
        by_tx.setdefault(tx, []).append(rel)
        if rel == "financial_f4_extractions":
            f4_status.append(status)
    assert sorted(sorted(v) for v in by_tx.values()) == [sorted(["backfill_item_events", "financial_extraction_runs",
                                                                 "financial_f4_extractions"])] * len(fids)
    assert f4_status == ["in progress"] * len(fids)
    w.rollback()
    report = verify.verify(_reader_of(env))
    assert report["clean"] and report["extractions"] == report["runs"] == report["rebuilt"] == len(fids)


def _reader_of(env):
    c = env.conn("postgres")
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("set role cse_reader")
    c.autocommit = False
    return c


@pytest.mark.parametrize("fault, mx, final", [("f4_save", 3, "retry_wait"), ("f4_save", 1, "failed"),
                                              ("f5_save_after_f4", 3, "retry_wait"), ("ledger_event", 3, "retry_wait"),
                                              ("nan_in_f4_output", 1, "failed")])
def test_p23_hb4_rolls_the_filing_back_records_it_and_a_retry_saves_once(env, layouts, monkeypatch, fault, mx, final):
    fid = 986001
    words = a_layout_with_cells(layouts)
    w = open_hb4(env, [fid], item_max_attempts=mx)
    by = ByDocument({fid: words})
    cdn = H.ScriptedCDN(env.clock, {H.url_of(H.path_of(fid)): H.ok_doc(fid)})
    with monkeypatch.context() as m:
        if fault == "f4_save":
            def boom(self, ext, cid, cls):
                raise ValueError("scripted: a database failure inside the F4 save")
            m.setattr(store.PostgresF4Store, "save", boom)
        elif fault == "f5_save_after_f4":
            def boom(self, result, cid, link=None):
                raise ValueError("scripted: a database failure inside the F5 save, after the F4 save")
            m.setattr(PostgresCandidateStore, "save", boom)
        elif fault == "ledger_event":
            real = hb4.append_event_in

            def boom(cur, item_id, st, action, **kw):
                if st == "persisted":
                    raise RuntimeError("scripted: the ledger event cannot be written")
                return real(cur, item_id, st, action, **kw)
            m.setattr(hb4, "append_event_in", boom)
        else:
            Originals(m, mutate=_nan_coordinate)
        ds, err = hb4_slice(env, cdn, by)
    it = H.item_of(w, fid)
    ev = ledger.events(w, it["id"])[-1]
    assert err is None and (ev["state"], ev["action"]) == (final, "record"), ev
    if fault != "ledger_event":
        assert "rolled back" in ev["reason"], ev["reason"]
    if fault == "nan_in_f4_output":
        assert "UnsupportedValue" in ev["reason"] or "unsupported_value" in ev["reason"], ev["reason"]
    assert rows_of(w, [fid]) == (0, 0, 0) and f4_counts(w)["extractions"] == 0      # the whole filing rolled back
    if final == "failed":
        return
    ds, err = hb4_slice(env, cdn, by)                                # the retry: one more download, saved once
    assert err is None and H.state(w, it) == "persisted" and len(cdn.calls) == 2
    assert rows_of(w, [fid]) == (1, 1, 1) and f4_counts(w)["extractions"] == 1
    w.rollback()
    assert verify.verify(_reader_of(env))["clean"]


# ------------------------------------------------------------------------------------------------ concurrent callers

def test_p24_two_concurrent_runs_of_one_filing_leave_one_set_of_rows_and_no_deadlock(db, layouts, monkeypatch):
    """Design section 6 (concurrent writers) through F5's run(), each run on its own connection with main()'s four
    stores: run A holds its uncommitted F3, F4 and F5 rows while run B saves the same filing. B waits on A's rows (a
    lock wait in pg_stat_activity); once A commits, B finds all three already present. No deadlock, one set of rows."""
    fid = 987001
    words = a_layout_with_cells(layouts)
    wa, wb = db.conn(), db.conn()
    seed_filing(wa, fid)
    held, release = threading.Event(), threading.Event()
    real_f5_save = PostgresCandidateStore.save

    def f5_save(self, result, cid, link=None):
        out = real_f5_save(self, result, cid, link)
        if self.conn is wa:                                   # run A: F3, F4 and F5 saved, not yet committed
            held.set()
            assert release.wait(60)
        return out
    monkeypatch.setattr(PostgresCandidateStore, "save", f5_save)
    got = {}

    def run(name, w):
        try:
            got[name] = run_f5(w, [fid], {fid: words})[0]
        except BaseException as exc:  # noqa: BLE001 - reported below
            got[name] = exc
    a = threading.Thread(target=run, args=("a", wa))
    b = threading.Thread(target=run, args=("b", wb))
    su, pid = db.su(), wb.get_backend_pid()
    try:
        a.start()
        assert held.wait(60), got
        b.start()
        for _ in range(400):                                  # B waits on A's uncommitted rows
            if q(su, "select wait_event_type from pg_stat_activity where pid = %s", (pid,))[0][0] == "Lock":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"run B never waited on run A's rows: {got}")
        assert b.is_alive() and rows_of(su, [fid]) == (0, 0, 0)       # nothing of A's is committed yet
    finally:
        release.set()
        a.join(60)
        if b.ident is not None:
            b.join(60)
    assert not a.is_alive() and not b.is_alive()
    assert not any(isinstance(r, BaseException) for r in got.values()), repr(got)     # in full: pytest cuts a dict
    stored = {k: [(r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) for r in v["records"]]
              for k, v in got.items()}
    assert stored =={"a": [("inserted",) * 3], "b": [("already_present",) * 3]}
    assert rows_of(su, [fid]) == (1, 1, 1) and f4_counts(su)["extractions"] == 1
    report = verify.verify(db.reader())
    assert report["clean"] and report["extractions"] == report["runs"] == report["rebuilt"] == 1


# ========================================================================================== audit remediation

def differing(classification):
    """A classification of the same F3 key (filing, document, classifier version, text extractor) with other content:
    what F3's store keeps when it saved an earlier classification of the same document."""
    earlier = copy.deepcopy(classification)
    earlier["status_reasons"] = list(earlier.get("status_reasons") or []) + ["rf4 test: an earlier classification"]
    return earlier


def consumed_classification(w, fid, words, url):
    """The F3 classification F5's consumer makes for this filing (a dry run: nothing is written)."""
    made = {}
    by = ByDocument({fid: words})
    with TempRoot() as root:
        f5cli.run(f5cli.load_filings_from_db(w, [fid]), temp_root=root, request_delay_seconds=0,
                  fetcher=FakeFetcher({url: _ok(fid)}), extract_text=by.text, extract_words=by.words,
                  require_poppler=False, on_result=lambda f, res, cls: made.setdefault(f, cls))
    return made[fid]


def test_p25_a_persisted_f3_other_than_the_one_f5_consumed_refuses_the_save_and_verify_reports_history(
        db, layouts, monkeypatch):
    w, su = db.conn(), db.su()
    fid = 990001
    cls, ext, res = R.chain(a_layout_with_cells(layouts), fid)
    seed_filing(w, fid)
    earlier = differing(cls)
    assert PostgresClassificationStore(w).save(earlier, 1000) == "inserted"
    w.commit()
    assert PostgresClassificationStore(w).save(cls, 1000) == "already_present"     # F3's store keeps the earlier row
    cid = classification_id(w, cls)
    persisted = codec.f3_input_sha256(reader.load_classification(w, cid))
    assert persisted == codec.f3_input_sha256(earlier) != codec.f3_input_sha256(cls)
    before = f4_inserts(w)
    with pytest.raises(ConflictingEvidence, match="is persisted with content other than the classification F5 "
                                                  "consumed") as e:
        store.PostgresF4Store(w).save(ext, cid, cls)
    assert e.value.kind == "conflicting" and persisted in e.value.detail
    assert f4_inserts(w) == before                                              # refused before any insert
    assert q(w, "select count(*) from report_document_classifications where id = %s", (cid,)) == [(1,)]   # usable
    w.rollback()
    # History: an extraction saved by a writer without this check (simulated by reading back the classification F5
    # consumed) names F3 evidence F5 did not consume. verify reports it; saving it again is refused, not
    # 'already_present'.
    with monkeypatch.context() as m:
        m.setattr(store.reader, "load_classification", lambda conn, c: cls)
        assert store.PostgresF4Store(w).save(ext, cid, cls) == "inserted"
    _, run_id = PostgresCandidateStore(w).save(res, cid, PostgresIssuerStore(w).link_filing(fid))
    w.commit()
    eid = reader.extraction_id_for(w, {"cse_filing_id": fid, "document_sha256": ext.document_sha256,
                                       "word_extractor": ext.extractor, "f4_extractor_version": ext.extractor_version,
                                       "classification_id": cid})
    report = verify.verify(db.reader())
    assert not report["clean"]
    assert {(p["kind"], p.get("extraction_id") or p.get("run_id")) for p in report["problems"]} == \
        {("corrupt", str(eid)), ("corrupt", str(run_id))}
    assert all("f3_input_sha256" in p["detail"] for p in report["problems"])
    before = f4_inserts(w)
    with pytest.raises(ConflictingEvidence, match="is persisted with content other than"):
        store.PostgresF4Store(w).save(copy.deepcopy(ext), cid, cls)               # the 'already_present' path
    assert f4_inserts(w) == before
    w.rollback()
    assert f4_counts(su, fid)["extractions"] == 1


def test_p25_run_with_a_differing_persisted_f3_raises_and_leaves_its_whole_batch_uncommitted(db, layouts):
    w = db.conn()
    words = a_layout_with_cells(layouts)
    fids = [990101, 990102]
    for fid in fids:
        seed_filing(w, fid)
    cls = consumed_classification(w, fids[1], words, H.url_of(R.path_of(fids[1])))
    PostgresClassificationStore(w).save(differing(cls), 1000)
    w.commit()
    with pytest.raises(ConflictingEvidence, match="is persisted with content other than"):
        run_f5(w, fids, dict.fromkeys(fids, words))
    w.rollback()                                                            # what main()'s finally does (close)
    assert rows_of(w, [fids[0]]) == (0, 0, 0)                               # filing 1 is uncommitted too
    assert rows_of(w, [fids[1]]) == (1, 0, 0)                               # only the earlier F3 row
    assert f4_counts(w)["extractions"] == 0 and verify.verify(db.reader())["clean"]


def test_p25_hb4_with_a_differing_persisted_f3_rolls_the_filing_back_and_records_it(env, layouts):
    fid = 990201
    words = a_layout_with_cells(layouts)
    w = open_hb4(env, [fid], item_max_attempts=3)
    cls = consumed_classification(w, fid, words, H.url_of(H.path_of(fid)))
    PostgresClassificationStore(w).save(differing(cls), 1000)
    w.commit()
    cdn = H.ScriptedCDN(env.clock, {H.url_of(H.path_of(fid)): H.ok_doc(fid)})
    ds, err = hb4_slice(env, cdn, ByDocument({fid: words}))
    it = H.item_of(w, fid)
    ev = ledger.events(w, it["id"])[-1]
    assert err is None and (ev["state"], ev["action"]) == ("retry_wait", "record"), ev
    assert "rolled back" in ev["reason"] and "ConflictingEvidence" in ev["reason"], ev["reason"]
    assert rows_of(w, [fid]) == (1, 0, 0) and f4_counts(w)["extractions"] == 0     # only the earlier F3 row
    assert not any(e["state"] == "persisted" for e in ledger.events(w, it["id"]))
    w.rollback()


def test_p26_a_trigger_altered_beyond_its_name_is_refused_before_any_insert_and_reported(db, layouts):
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    guard = trigger_def(su, "financial_f4_extractions", "trg_ff4e_guard")
    cells = trigger_def(su, "financial_f4_cells", "trg_ff4c_append_only")
    statements = trigger_def(su, "financial_f4_statements", "trg_ff4s_append_only")
    rows = trigger_def(su, "financial_f4_rows", "trg_ff4r_append_only")
    guard_function = q(su, "select pg_get_functiondef('public.financial_f4_extraction_guard()'::regprocedure)")[0][0]
    status = q(su, "select attnum from pg_attribute where attrelid = 'public.financial_f4_statements'::regclass and "
                   "attname = 'status'")[0][0]
    noop = "returns trigger language plpgsql as $$ begin return coalesce(new, old); end $$"
    # (break, restore, the store's message (a regex), the preflight's message for cse_worker)
    cases = [
        # a same-named function in another schema: a no-op that lets UPDATE and DELETE through
        (f"create schema rf4_other; create function rf4_other.f5_reject_mutation() {noop}; "
         "drop trigger trg_ff4c_append_only on financial_f4_cells; create trigger trg_ff4c_append_only before update "
         "or delete on financial_f4_cells for each row execute function rf4_other.f5_reject_mutation()",
         f"drop trigger trg_ff4c_append_only on financial_f4_cells; {cells}; drop schema rf4_other cascade",
         r"trg_ff4c_append_only runs rf4_other\.f5_reject_mutation\(\), expected public\.f5_reject_mutation\(\)",
         "financial_f4_cells: trigger trg_ff4c_append_only runs rf4_other.f5_reject_mutation(), expected "
         "public.f5_reject_mutation()"),
        # a WHEN condition that never lets the guard fire
        ("drop trigger trg_ff4e_guard on financial_f4_extractions; create trigger trg_ff4e_guard before insert on "
         "financial_f4_extractions for each row when (false) execute function financial_f4_extraction_guard()",
         f"drop trigger trg_ff4e_guard on financial_f4_extractions; {guard}",
         r"trg_ff4e_guard has a WHEN condition \(false\)",
         "financial_f4_extractions: trigger trg_ff4e_guard has a WHEN condition (false); migration 0018 creates it "
         "without one"),
        # the guard function's body replaced in place (same name, schema and oid)
        (f"create or replace function public.financial_f4_extraction_guard() {noop}", guard_function,
         r"trg_ff4e_guard runs public\.financial_f4_extraction_guard\(\) defined other than in migration 0018",
         None),
        # an update trigger narrowed to one column: an UPDATE of any other column gets through
        ("drop trigger trg_ff4s_append_only on financial_f4_statements; create trigger trg_ff4s_append_only before "
         "update of status or delete on financial_f4_statements for each row execute function f5_reject_mutation()",
         f"drop trigger trg_ff4s_append_only on financial_f4_statements; {statements}",
         rf"trg_ff4s_append_only fires only for column\(s\) {status} \(attnum\)",
         f"financial_f4_statements: trigger trg_ff4s_append_only fires only for column(s) {status} (attnum); "
         "migration 0018 names no columns"),
        # an argument the migration does not pass
        ("drop trigger trg_ff4r_append_only on financial_f4_rows; create trigger trg_ff4r_append_only before update "
         "or delete on financial_f4_rows for each row execute function f5_reject_mutation('x')",
         f"drop trigger trg_ff4r_append_only on financial_f4_rows; {rows}",
         r"trg_ff4r_append_only passes 1 argument\(s\)",
         "financial_f4_rows: trigger trg_ff4r_append_only passes 1 argument(s); migration 0018 passes none"),
    ]
    for n, (break_sql, restore_sql, match, reported) in enumerate(cases):
        q(su, break_sql)                                              # as the superuser: a schema, functions, triggers
        try:
            refused_before_any_insert(w, su, words, 962001 + n, match)
            problems = preflight.object_problems(w)
            assert any(re.search(match, p) for p in problems), (match, problems)
            if reported is not None:
                assert reported in problems, (reported, problems)
        finally:
            try:
                w.rollback()
            finally:
                q(su, restore_sql)
    assert preflight.object_problems(su) == []                        # every change was undone, exactly
    assert q(su, "select count(*) from financial_f4_extractions") == [(0,)]
    assert q(su, "select count(*) from pg_namespace where nspname = 'rf4_other'") == [(0,)]


def live_constraints(su):
    return {t: {n: (k, d) for n, k, d in q(su, "select conname, contype, pg_get_constraintdef(oid) from pg_constraint "
                                               "where conrelid = %s::regclass and contype <> 'n'", (f"public.{t}",))}
            for t in codec.TABLES}


def test_p27_the_preflight_verifies_each_constraint_by_type_and_definition(db):
    su, m = db.su(), migrator(db)
    q(su, "set search_path = public")
    assert live_constraints(su) == preflight.CONSTRAINT_DEFINITIONS          # 0018 as P1's runner applies it
    assert preflight.report(m)["clean"]
    d = preflight.CONSTRAINT_DEFINITIONS
    put = lambda t, n: f"alter table {t} drop constraint {n}; alter table {t} add constraint {n} {d[t][n][1]}"
    cases = [  # (break, restore, what the preflight must report)
        ("alter table financial_f4_cells drop constraint uq_financial_f4_cell_seq; alter table financial_f4_cells "
         "add constraint uq_financial_f4_cell_seq check (seq >= 0)",                 # same name, another type
         put("financial_f4_cells", "uq_financial_f4_cell_seq"),
         "financial_f4_cells: constraint uq_financial_f4_cell_seq is a check constraint (CHECK ((seq >= 0))), "
         f"expected a unique constraint ({d['financial_f4_cells']['uq_financial_f4_cell_seq'][1]})"),
        ("alter table financial_f4_cells drop constraint chk_ff4c_decimal; alter table financial_f4_cells add "
         "constraint chk_ff4c_decimal check (parsed_value_text is null or length(parsed_value_text) > 0)",  # weakened
         put("financial_f4_cells", "chk_ff4c_decimal"),
         "financial_f4_cells: constraint chk_ff4c_decimal is CHECK (((parsed_value_text IS NULL) OR "
         f"(length(parsed_value_text) > 0))), expected {d['financial_f4_cells']['chk_ff4c_decimal'][1]}"),
        ("alter table financial_f4_extractions drop constraint uq_financial_f4_extraction; alter table "
         "financial_f4_extractions add constraint uq_financial_f4_extraction unique (cse_filing_id, document_sha256, "
         "word_extractor, f4_extractor_version, classification_id, store_version)",   # a key widened (weaker)
         put("financial_f4_extractions", "uq_financial_f4_extraction"),
         "financial_f4_extractions: constraint uq_financial_f4_extraction is UNIQUE (cse_filing_id, document_sha256, "
         "word_extractor, f4_extractor_version, classification_id, store_version), expected "
         f"{d['financial_f4_extractions']['uq_financial_f4_extraction'][1]}"),
        ("alter table financial_f4_cells drop constraint fk_ff4c_row; alter table financial_f4_cells add constraint "
         "fk_ff4c_row foreign key (statement_id) references financial_f4_statements(id)",   # another relationship
         put("financial_f4_cells", "fk_ff4c_row"),
         "financial_f4_cells: constraint fk_ff4c_row is FOREIGN KEY (statement_id) REFERENCES "
         f"financial_f4_statements(id), expected {d['financial_f4_cells']['fk_ff4c_row'][1]}"),
        ("alter table financial_f4_statements drop constraint financial_f4_statements_extraction_id_fkey; alter "
         "table financial_f4_statements add constraint financial_f4_statements_extraction_id_fkey foreign key "
         "(extraction_id) references financial_f4_extractions(id) on delete cascade",       # another action
         put("financial_f4_statements", "financial_f4_statements_extraction_id_fkey"),
         "financial_f4_statements: constraint financial_f4_statements_extraction_id_fkey is FOREIGN KEY "
         "(extraction_id) REFERENCES financial_f4_extractions(id) ON DELETE CASCADE, expected "
         f"{d['financial_f4_statements']['financial_f4_statements_extraction_id_fkey'][1]}"),
        ("create schema rf4_other; create table rf4_other.report_filings (cse_filing_id bigint primary key); alter "
         "table financial_f4_extractions drop constraint financial_f4_extractions_cse_filing_id_fkey; alter table "
         "financial_f4_extractions add constraint financial_f4_extractions_cse_filing_id_fkey foreign key "
         "(cse_filing_id) references rf4_other.report_filings(cse_filing_id)",          # a same-named table elsewhere
         put("financial_f4_extractions", "financial_f4_extractions_cse_filing_id_fkey")
         + "; drop schema rf4_other cascade",
         "financial_f4_extractions: constraint financial_f4_extractions_cse_filing_id_fkey is FOREIGN KEY "
         "(cse_filing_id) REFERENCES rf4_other.report_filings(cse_filing_id), expected "
         f"{d['financial_f4_extractions']['financial_f4_extractions_cse_filing_id_fkey'][1]}"),
        ("alter table financial_f4_rows drop constraint chk_ff4r_seq; alter table financial_f4_rows add constraint "
         "chk_ff4r_seq check (seq >= 0) not valid",                                      # not validated
         put("financial_f4_rows", "chk_ff4r_seq"),
         "financial_f4_rows: constraint chk_ff4r_seq is not validated"),
        ("alter table financial_f4_rows add constraint chk_rf4_extra check (seq < 1000000)",   # not 0018's
         "alter table financial_f4_rows drop constraint chk_rf4_extra",
         "financial_f4_rows: constraint chk_rf4_extra (CHECK ((seq < 1000000))) is not one migration 0018 creates"),
    ]
    for break_sql, restore_sql, want in cases:
        q(su, break_sql)                                                  # as the superuser
        try:
            got = preflight.report(m)
            assert want in got["objects"] and not got["clean"], (want, got["objects"])
            assert got["file"] == [] and got["ledger"]["status"] == "verified"     # the three checks stay apart
            assert len(got["objects"]) == 1, got["objects"]                          # nothing else is reported
        finally:
            q(su, restore_sql)
    assert live_constraints(su) == preflight.CONSTRAINT_DEFINITIONS and preflight.report(m)["clean"]
    assert q(su, "select count(*) from pg_namespace where nspname = 'rf4_other'") == [(0,)]


def guard_catalogue(su):
    """(proconfig, body sha256) of the guard function, as the catalogue holds it."""
    return q(su, "select p.proconfig, encode(sha256(convert_to(replace(p.prosrc, E'\\r\\n', E'\\n'), 'UTF8')), "
                 "'hex') from pg_proc p where p.oid = 'public.financial_f4_extraction_guard()'::regprocedure")[0]


def insert_extraction_row(w, fid, document_sha256, classification_id, word_extractor="rf4 test"):
    """One extraction row written directly (no store, so no in-band check): what the triggers alone let through."""
    q(w, "insert into financial_f4_extractions (cse_filing_id, document_sha256, word_extractor, f4_extractor_version, "
         "classification_id, store_version, document_status, status_reasons, page_count, page_trust, statement_count, "
         "column_count, row_count, cell_count, f4_content_sha256, f3_input_sha256) values (%s, %s, %s, 'f4.9', %s, "
         "'f4.store.1', 'x', '[]', 0, '[]', 0, 0, 0, 0, %s, %s) returning classification_id",
      (fid, document_sha256, word_extractor, classification_id, "a" * 64, "b" * 64))
    return str(q(w, "select classification_id from financial_f4_extractions where cse_filing_id = %s and "
                    "word_extractor = %s", (fid, word_extractor))[0][0])


def test_p28_a_search_path_set_on_the_guard_function_is_refused_by_the_store_and_the_preflight(db, layouts):
    """Finding 4: the guard reads report_document_classifications by an unqualified name. A function-level search_path
    changes which table that is, with the body unchanged: here a shadow table vouches for a classification of another
    filing. The store refuses before any insert and the preflight reports it; then everything is restored."""
    import psycopg2
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    s = persist(w, words, 963001)
    other = persist(w, words, 963002)                       # a classification of another filing
    before = guard_catalogue(su)
    assert before[0] is None                                # migration 0018 sets no configuration
    with pytest.raises(psycopg2.errors.CheckViolation, match="is not of filing"):
        insert_extraction_row(w, s.fid, s.ext.document_sha256, other.cid)     # the guard, as 0018 creates it
    w.rollback()
    q(su, "create schema rf4_other; create table rf4_other.report_document_classifications (id uuid primary key, "
          "cse_filing_id bigint, document_sha256 text); grant usage on schema rf4_other to cse_worker; grant select on "
          "rf4_other.report_document_classifications to cse_worker")
    q(su, "insert into rf4_other.report_document_classifications values (%s, %s, %s)",
      (other.cid, s.fid, s.ext.document_sha256))
    try:
        q(su, "alter function public.financial_f4_extraction_guard() set search_path = rf4_other, public")
        after = guard_catalogue(su)
        assert after == (["search_path=rf4_other, public"], before[1])       # the body is unchanged
        # why it matters: the guard now accepts an extraction naming another filing's classification
        assert insert_extraction_row(w, s.fid, s.ext.document_sha256, other.cid) == str(other.cid)
        w.rollback()                                                           # nothing of it is kept
        want = ("financial_f4_extractions: trigger trg_ff4e_guard runs public.financial_f4_extraction_guard() "
                "configured ['search_path=rf4_other, public'] (proconfig); migration 0018 creates it with no "
                "configuration")
        refused_before_any_insert(w, su, words, 963003, re.escape(want))
        assert want in preflight.object_problems(w)
        got = preflight.report(migrator(db))
        assert want in got["objects"] and not got["clean"] and got["file"] == [] and \
            got["ledger"]["status"] == "verified"
    finally:
        try:
            w.rollback()
        finally:
            q(su, "alter function public.financial_f4_extraction_guard() reset search_path; "
                  "drop schema rf4_other cascade")
    assert guard_catalogue(su) == before
    assert preflight.object_problems(su) == [] and preflight.report(migrator(db))["clean"]
    assert q(su, "select count(*) from pg_namespace where nspname = 'rf4_other'") == [(0,)]
    assert f4_counts(su)["extractions"] == 2                                   # s and other only


def test_p29_an_enabled_trigger_0018_does_not_create_is_refused_by_the_store_and_reported_by_the_preflight(
        db, layouts):
    """Finding 5: BEFORE triggers fire in name order, so a BEFORE INSERT trigger named after trg_ff4e_guard runs after
    the guard has accepted the row and can change it: here it renames the classification to another filing's. Such a
    trigger is refused by the store before any insert and reported by the preflight; one on another table too. A
    disabled extra trigger does not fire in normal operation and, like a disabled expected trigger, is not counted as
    enabled. Everything is restored."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    s = persist(w, words, 964001)
    other = persist(w, words, 964002)
    q(su, "create function public.rf4_rewrite_classification() returns trigger language plpgsql as $$ begin "
          f"new.classification_id := '{other.cid}'; return new; end $$")
    after_guard = ("financial_f4_extractions: trigger trg_ff4e_zz_after_guard (runs public.rf4_rewrite_classification, "
                   "tgtype 7) is enabled and is not one migration 0018 creates")
    on_cells = ("financial_f4_cells: trigger trg_ff4c_extra (runs public.f5_reject_mutation, tgtype 7) is enabled and "
                "is not one migration 0018 creates")
    try:
        q(su, "create trigger trg_ff4e_zz_after_guard before insert on financial_f4_extractions for each row execute "
              "function public.rf4_rewrite_classification()")
        assert q(su, "select tgname from pg_trigger where tgrelid = 'public.financial_f4_extractions'::regclass and "
                     "tgname like 'trg\\_ff4e\\_%%' and tgtype = 7 order by tgname") == \
            [("trg_ff4e_guard",), ("trg_ff4e_zz_after_guard",)]               # fires after the guard
        # why it matters: the guard accepts s's own classification, then the row is stored naming another filing's
        assert insert_extraction_row(w, s.fid, s.ext.document_sha256, s.cid) == str(other.cid)
        w.rollback()
        refused_before_any_insert(w, su, words, 964003, re.escape(after_guard))
        assert after_guard in preflight.object_problems(w)
        got = preflight.report(m)
        assert got["objects"] == [after_guard] and not got["clean"] and got["ledger"]["status"] == "verified"
        # a disabled extra trigger does not fire in normal operation: not counted as enabled, as before
        q(su, "alter table financial_f4_extractions disable trigger trg_ff4e_zz_after_guard")
        assert preflight.object_problems(w) == []
        q(su, "drop trigger trg_ff4e_zz_after_guard on financial_f4_extractions")
        # another table, another timing, even an expected function: still not 0018's
        q(su, "create trigger trg_ff4c_extra before insert on financial_f4_cells for each row execute function "
              "f5_reject_mutation()")
        refused_before_any_insert(w, su, words, 964004, re.escape(on_cells))
        assert on_cells in preflight.object_problems(w)
    finally:
        try:
            w.rollback()
        finally:
            q(su, "drop trigger if exists trg_ff4e_zz_after_guard on financial_f4_extractions; drop trigger if exists "
                  "trg_ff4c_extra on financial_f4_cells; drop function public.rf4_rewrite_classification()")
    assert preflight.object_problems(su) == [] and preflight.report(m)["clean"]
    assert q(su, "select count(*) from pg_proc where proname = 'rf4_rewrite_classification'") == [(0,)]
    assert f4_counts(su)["extractions"] == 2                                   # s and other only


# ---------------------------------------------------------------------------------------- name resolution (search_path)

def shadow_schema(su, s=None, other=None):
    """rf4_shadow, a schema cse_worker and cse_reader may use; with s and other, a report_document_classifications in
    it that says other's classification is of s's filing and document."""
    q(su, "create schema rf4_shadow; grant usage on schema rf4_shadow to cse_worker, cse_reader")
    if s is not None:
        q(su, "create table rf4_shadow.report_document_classifications (id uuid primary key, cse_filing_id bigint, "
              "document_sha256 text); grant select on rf4_shadow.report_document_classifications to cse_worker")
        q(su, "insert into rf4_shadow.report_document_classifications values (%s, %s, %s)",
          (other.cid, s.fid, s.ext.document_sha256))


def f3_saved(w, words, fid):
    """The filing and its F3 classification, saved and committed by a session that resolves names safely (F3's own
    store, frozen, uses unqualified names too); (cls, ext, cid)."""
    cls, ext, _ = R.chain(words, fid)
    seed_filing(w, fid)
    PostgresClassificationStore(w).save(cls, 1000)
    w.commit()
    return cls, ext, classification_id(w, cls)


def f4_refused(a, w, words, fid, match):
    """R-F4's save in session a: SchemaUnavailable before any insert; a's transaction still usable."""
    cls, ext, cid = f3_saved(w, words, fid)
    before = f4_inserts(a)
    with pytest.raises(SchemaUnavailable, match=match):
        store.PostgresF4Store(a).save(ext, cid, cls)
    assert f4_inserts(a) == before and q(a, "select 1") == [(1,)]
    a.rollback()


def f4_saved(a, w, words, fid):
    cls, ext, cid = f3_saved(w, words, fid)
    assert store.PostgresF4Store(a).save(ext, cid, cls) == "inserted"
    a.commit()


def search_path_settings(su):
    """Every catalogued search_path setting (role, database), to show a test leaves none behind."""
    return q(su, "select coalesce(r.rolname, '*'), coalesce(d.datname, '*'), cfg from pg_db_role_setting s "
                 "cross join lateral unnest(s.setconfig) as cfg left join pg_roles r on r.oid = s.setrole "
                 "left join pg_database d on d.oid = s.setdatabase where cfg like 'search_path=%%'")


def assert_restored(db, su, m):
    assert search_path_settings(su) == []
    assert q(su, "select nspname from pg_namespace where nspname like 'rf4\\_%%' or nspname = 'cse_worker'") == []
    assert q(su, "select has_database_privilege('cse_worker', current_database(), 'CREATE'), "
                 "has_database_privilege('cse_worker', current_database(), 'TEMPORARY')") == [(False, False)]
    assert preflight.report(m)["clean"]


def test_p30_a_role_level_search_path_ahead_of_public_is_refused_by_the_store_and_reported_by_the_preflight(
        db, layouts):
    """ALTER ROLE cse_worker SET search_path = rf4_shadow, public: the guard's body, configuration and triggers are
    still 0018's (Findings 2-5's checks pass), yet a shadow report_document_classifications makes it accept another
    filing's classification. The store refuses before any insert; the preflight reports the role's setting. A
    role-in-database setting outranks it: a safe one there passes both checks."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    s = persist(w, words, 965001)
    other = persist(w, words, 965002)
    assert_restored(db, su, m)
    try:
        shadow_schema(su, s, other)
        q(su, "alter role cse_worker set search_path = rf4_shadow, public")
        a = db.conn()                                                    # a new session gets the role's setting
        assert q(a, "show search_path") == [("rf4_shadow, public",)]
        assert schema.problems(a.cursor()) == []                         # Findings 2-5's checks see nothing wrong
        a.rollback()
        # why it matters: the guard reads rf4_shadow's table and accepts a classification of another filing
        assert insert_extraction_row(a, s.fid, s.ext.document_sha256, other.cid) == str(other.cid)
        a.rollback()
        f4_refused(a, w, words, 965003, re.escape("search_path 'rf4_shadow, public': schema rf4_shadow is searched "
                                                  "before public"))
        got = preflight.report(m)
        want = ("cse_worker: the search_path its sessions get (its role setting (ALTER ROLE ... SET): "
                "'rf4_shadow, public'): schema rf4_shadow is searched before public, whether or not it exists or "
                "holds a table now")
        assert got["objects"] == [want] and not got["clean"]             # cse_reader's sessions are not affected
        assert got["file"] == [] and got["ledger"]["status"] == "verified"
        # a role-in-database setting outranks the role setting: a safe one passes both checks
        q(su, f"alter role cse_worker in database {db.name} set search_path = pg_catalog, public")
        b = db.conn()
        assert q(b, "show search_path") == [("pg_catalog, public",)]
        f4_saved(b, w, words, 965004)
        assert preflight.report(m)["clean"]
    finally:
        q(su, f"alter role cse_worker reset search_path; alter role cse_worker in database {db.name} reset "
              "search_path; drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)


def test_p31_a_database_level_search_path_is_refused_even_before_its_schema_holds_a_table_or_exists(db, layouts):
    """ALTER DATABASE … SET search_path: an unsafe effective setting for cse_worker (and cse_reader) whether the
    schema ahead of public holds a shadow table, is empty, or does not exist yet; a safe one passes."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    assert_restored(db, su, m)
    level = "this database's setting (ALTER DATABASE ... SET)"
    try:
        shadow_schema(su)                                                # exists, may be used, holds no table
        for n, path in enumerate(("rf4_shadow, public", "rf4_not_yet, public")):     # the second does not exist
            q(su, f"alter database {db.name} set search_path = {path}")
            a = db.conn()
            assert q(a, "show search_path") == [(path,)]
            schema_name = path.split(",")[0]
            f4_refused(a, w, words, 966001 + n, re.escape(f"search_path '{path}': schema {schema_name} is searched "
                                                          "before public, whether or not it exists or holds a table "
                                                          "now"))
            got = preflight.report(m)
            assert got["objects"] == [f"{role}: the search_path its sessions get ({level}: '{path}'): schema "
                                      f"{schema_name} is searched before public, whether or not it exists or holds a "
                                      "table now" for role in ("cse_worker", "cse_reader")], got["objects"]
            assert got["file"] == [] and got["ledger"]["status"] == "verified" and not got["clean"]
        q(su, f"alter database {db.name} set search_path = pg_catalog, public")      # safe
        f4_saved(db.conn(), w, words, 966003)
        assert preflight.report(m)["clean"]
    finally:
        q(su, f"alter database {db.name} reset search_path; drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)


def test_p32_a_session_setting_and_the_default_user_schema_are_checked_where_they_take_effect(db, layouts):
    """A SET in the saving session (as a client's connection options would be) is seen by the store's in-band check,
    which runs in that session; the preflight, which reads the catalogue, cannot see it. The default "$user" is safe
    only while no schema named cse_worker exists and cse_worker may not create one. public missing, and pg_catalog
    after public (public's operators before the built-in =), are refused."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    assert_restored(db, su, m)
    try:
        shadow_schema(su)
        for n, (path, match) in enumerate((
                ("rf4_shadow, public", "schema rf4_shadow is searched before public"),
                ("public, pg_catalog", "pg_catalog comes after public"),
                ("rf4_shadow", "public is not in it"))):
            a = db.conn()
            q(a, f"set search_path = {path}")
            f4_refused(a, w, words, 967001 + n, re.escape(match))
            assert preflight.report(m)["clean"]                          # not in the catalogue: the session's own
        q(su, "create schema cse_worker; grant usage on schema cse_worker to cse_worker")    # "$user" now names one
        a = db.conn()
        assert q(a, "show search_path") == [('"$user", public',)]
        f4_refused(a, w, words, 967004, re.escape("this session searches cse_worker before public"))
        want = ("cse_worker: the search_path its sessions get (the server's default: '\"$user\", public'): \"$user\" "
                "before public names an existing schema cse_worker")
        assert preflight.report(m)["objects"] == [want]
        q(su, "drop schema cse_worker")
        q(su, f"grant create on database {db.name} to cse_worker")      # it could make one itself
        f4_refused(db.conn(), w, words, 967005, re.escape("cse_worker may create a schema named cse_worker"))
        assert preflight.report(m)["objects"] == [
            "cse_worker: the search_path its sessions get (the server's default: '\"$user\", public'): \"$user\" "
            "before public, and cse_worker may create a schema named cse_worker (CREATE on the database)"]
        q(su, f"revoke create on database {db.name} from cse_worker")
        f4_saved(db.conn(), w, words, 967006)                           # the default, safe again
    finally:
        q(su, f"revoke create on database {db.name} from cse_worker; drop schema if exists cse_worker; "
              "drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)


def test_p33_a_temporary_table_searched_before_public_is_refused(db, layouts):
    """The session's temporary schema is searched before public and pg_catalog for relations: a temporary
    report_document_classifications makes the guard accept another filing's classification. Any relation in the
    saving session's temporary schema is refused (cse_worker has no TEMPORARY in P1; granted here for the test); an
    empty temporary schema passes."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    s = persist(w, words, 968001)
    other = persist(w, words, 968002)
    assert_restored(db, su, m)
    try:
        q(su, f"grant temporary on database {db.name} to cse_worker")
        t = db.conn()
        q(t, "create temporary table report_document_classifications (id uuid primary key, cse_filing_id bigint, "
             "document_sha256 text)")
        q(t, "insert into report_document_classifications values (%s, %s, %s)",
          (other.cid, s.fid, s.ext.document_sha256))
        t.commit()                                                       # the temporary table lasts the session
        assert insert_extraction_row(t, s.fid, s.ext.document_sha256, other.cid) == str(other.cid)
        t.rollback()                                                     # why it matters; nothing kept
        f4_refused(t, w, words, 968003, r"this session's temporary schema pg_temp_\d+ holds \d+ relation")
        assert preflight.report(m)["clean"]                              # a session's own objects: in-band only
        q(t, "drop table report_document_classifications; create temporary table rf4_scratch (x integer)")
        t.commit()
        f4_refused(t, w, words, 968004, r"temporary schema pg_temp_\d+ holds")       # any temporary relation
        q(t, "drop table rf4_scratch")
        t.commit()
        assert q(t, "select pg_my_temp_schema() <> 0") == [(True,)]     # its temporary schema exists, now empty
        t.rollback()
        f4_saved(t, w, words, 968005)
    finally:
        q(su, f"revoke temporary on database {db.name} from cse_worker")
    assert_restored(db, su, m)
    assert f4_counts(su)["extractions"] == 3                            # s, other and 968005 only


# --------------------------------------------------------------------------- search_path evidence (2026-10-17): p34-p38

class _Levels:
    """preflight.SEARCH_PATH_LEVELS, looked up when a test runs: a tree without it fails those tests, not collection
    (the negative control against the code before the search-path check)."""

    def __getitem__(self, n):
        return preflight.SEARCH_PATH_LEVELS[n]


LEVEL = _Levels()
AHEAD = "schema rf4_shadow is searched before public, whether or not it exists or holds a table now"
UNKNOWN = ("{}: the search_path its sessions get cannot be determined from this connection: no role or database "
           "setting applies, and this connection's own search_path is not the server's")


def configured(role, source, path, problem):
    """The preflight's report of the search path `role`'s sessions get."""
    return f"{role}: the search_path its sessions get ({source}: {path!r}): {problem}"


def session_path(c):
    """(search_path, source) of session c, as PostgreSQL itself reports them (pg_settings)."""
    got = q(c, "select setting, source from pg_settings where name = 'search_path'")[0]
    c.rollback()
    return got


def searched(c):
    """The schemas session c searches, in order (current_schemas(true)); read as JSON, so any name comes back
    exactly."""
    return q(c, "select array_to_json(current_schemas(true))")[0][0]


def reload_until(db, su, want, timeout=30):
    """pg_reload_conf(), then wait until a NEW cse_worker session gets `want` (search_path, source): the server has
    read its configuration again."""
    q(su, "select pg_reload_conf()")
    deadline = time.monotonic() + timeout
    while True:
        c = S.conn(db.cluster, db.name, "cse_worker", False)
        try:
            got = q(c, "select setting, source from pg_settings where name = 'search_path'")[0]
        finally:
            c.close()
        if got == want:
            return
        assert time.monotonic() < deadline, (got, want)
        time.sleep(0.2)


def until(c, want, timeout=30):
    """Wait until the OPEN session c has `want` (search_path, source)."""
    deadline = time.monotonic() + timeout
    while (got := session_path(c)) != want:
        assert time.monotonic() < deadline, (got, want)
        time.sleep(0.2)


def connect_with_options(db, user, options):
    """A session given a search_path by the client's connection options (libpq `options`, as PGOPTIONS)."""
    import psycopg2
    c = psycopg2.connect(**db.cluster.conn_kwargs(db.name, user), options=options)
    db._open.append(c)
    return c


def test_p34_role_in_database_and_all_roles_settings_are_refused_and_reported_at_postgresqls_precedence(db, layouts):
    """ALTER ROLE ALL SET and ALTER ROLE ... IN DATABASE ... SET, each unsafe, each compared with the session PostgreSQL
    actually gives cse_worker (pg_settings' value and source): the store refuses before any insert and the preflight
    reports the level PostgreSQL applies; a safe setting at a higher level passes both. cse_reader is NOLOGIN, entered
    by SET ROLE, which applies none of its settings: the preflight reports its own setting all the same (it cannot see
    the settings of the logins that SET ROLE cse_reader)."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    assert_restored(db, su, m)
    try:
        shadow_schema(su)
        q(su, "alter role all set search_path = rf4_shadow, public")
        a = db.conn()
        assert session_path(a) == ("rf4_shadow, public", "global")                     # the lowest catalogued level
        f4_refused(a, w, words, 969001, re.escape(f"search_path 'rf4_shadow, public': {AHEAD}"))
        got = preflight.report(m)
        assert got["objects"] == [configured(r, LEVEL[4], "rf4_shadow, public", AHEAD)
                                  for r in ("cse_worker", "cse_reader")], got["objects"]
        assert got["file"] == [] and got["ledger"]["status"] == "verified" and not got["clean"]
        # a safe setting of this database outranks the setting for all roles
        q(su, f"alter database {db.name} set search_path = pg_catalog, public")
        b = db.conn()
        assert session_path(b) == ("pg_catalog, public", "database")
        f4_saved(b, w, words, 969002)
        assert preflight.report(m)["clean"]
        # the role's setting in this database outranks its role setting and the database's
        q(su, "alter role cse_worker set search_path = pg_catalog, public")
        q(su, f"alter role cse_worker in database {db.name} set search_path = rf4_shadow, public")
        c = db.conn()
        assert session_path(c) == ("rf4_shadow, public", "database user")
        f4_refused(c, w, words, 969003, re.escape(f"search_path 'rf4_shadow, public': {AHEAD}"))
        got = preflight.report(m)
        assert got["objects"] == [configured("cse_worker", LEVEL[1], "rf4_shadow, public", AHEAD)], got["objects"]
        assert got["file"] == [] and got["ledger"]["status"] == "verified"           # cse_reader: the database's, safe
        # without it, cse_worker's role setting applies
        q(su, f"alter role cse_worker in database {db.name} reset search_path")
        d = db.conn()
        assert session_path(d) == ("pg_catalog, public", "user")
        f4_saved(d, w, words, 969004)
        assert preflight.report(m)["clean"]
        # cse_reader's own setting reaches no session: SET ROLE applies none
        q(su, "alter role cse_reader set search_path = rf4_shadow, public")
        r = db.reader()
        assert q(r, "select current_user::text, session_user::text") == [("cse_reader", "postgres")]
        assert session_path(r) == ("pg_catalog, public", "database")                   # the login's, not cse_reader's
        assert preflight.report(m)["objects"] == [configured("cse_reader", LEVEL[2], "rf4_shadow, public", AHEAD)]
    finally:
        q(su, "alter role all reset search_path; alter role cse_worker reset search_path; alter role cse_reader reset "
              f"search_path; alter role cse_worker in database {db.name} reset search_path; alter database {db.name} "
              "reset search_path; drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)
    assert session_path(db.conn()) == ('"$user", public', "default")


def test_p35_a_server_level_search_path_is_refused_reported_and_can_leave_the_preflight_undetermined(db, layouts):
    """The server's own configuration (ALTER SYSTEM, then a reload) in the disposable test cluster: refused by the
    store, also in a session opened before the reload (a reload changes an open session's path between its commands,
    which is why the store checks at every save); reported by the preflight from a connection whose value is the
    server's. A client's connection options outrank it (judged in-band: a safe value saves, an unsafe one is refused),
    and so does a database setting. A preflight connection whose own value is not the server's (connection options; a
    login with a role setting) reports the roles without a catalogued setting as undetermined, never clean. Everything
    is restored and the restoration checked."""
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    q(w, "set search_path = public")                      # the session saving F3 for the test keeps its own path
    w.commit()
    assert_restored(db, su, migrator(db))
    path, server = "rf4_shadow, public", "the server's configuration file"
    try:
        shadow_schema(su)
        before = db.conn()                                                   # opened before the server's value changes
        assert session_path(before) == ('"$user", public', "default")
        q(su, "alter system set search_path = rf4_shadow, public")
        reload_until(db, su, (path, "configuration file"))
        f4_refused(db.conn(), w, words, 970001, re.escape(f"search_path 'rf4_shadow, public': {AHEAD}"))
        until(before, (path, "configuration file"))                         # changed under the open session
        f4_refused(before, w, words, 970002, re.escape(f"search_path 'rf4_shadow, public': {AHEAD}"))
        m = migrator(db)
        assert session_path(m) == (path, "configuration file")               # its own value is the server's
        got = preflight.report(m)
        assert got["objects"] == [configured(r, server, path, AHEAD) for r in ("cse_worker", "cse_reader")]
        assert got["file"] == [] and got["ledger"]["status"] == "verified" and not got["clean"]
        # a client's connection options outrank the server's value; only the in-band check sees them
        c = connect_with_options(db, "cse_worker", "-c search_path=pg_catalog,public")
        assert session_path(c) == ("pg_catalog,public", "client")
        f4_saved(c, w, words, 970003)
        u = connect_with_options(db, "cse_worker", "-c search_path=rf4_shadow,public")     # an unsafe one: refused
        assert session_path(u) == ("rf4_shadow,public", "client")
        f4_refused(u, w, words, 970005, re.escape(f"search_path 'rf4_shadow,public': {AHEAD}"))
        # undetermined: the preflight's own connection does not have the server's value
        got = preflight.report(connect_with_options(db, "cse_migrator", "-c search_path=public"))
        assert got["objects"] == [UNKNOWN.format(r) for r in ("cse_worker", "cse_reader")], got["objects"]
        assert got["ledger"]["status"] == "verified" and not got["clean"]
        q(su, "alter role cse_worker set search_path = pg_catalog, public")
        k = db.conn()                                                         # a login with a role setting of its own
        assert session_path(k) == ("pg_catalog, public", "user")
        assert preflight.object_problems(k) == [UNKNOWN.format("cse_reader")]  # cse_worker's: catalogued, safe
        q(su, "alter role cse_worker reset search_path")
        # a safe setting of this database outranks the server's
        q(su, f"alter database {db.name} set search_path = pg_catalog, public")
        d = db.conn()
        assert session_path(d) == ("pg_catalog, public", "database")
        f4_saved(d, w, words, 970004)
        assert preflight.report(migrator(db))["clean"]
    finally:
        try:
            q(su, "alter system reset search_path")
        finally:
            q(su, f"alter role cse_worker reset search_path; alter database {db.name} reset search_path; "
                  "drop schema if exists rf4_shadow cascade")
            reload_until(db, su, ('"$user", public', "default"))
    assert q(su, "select count(*) from pg_file_settings where name = 'search_path'") == [(0,)]
    assert_restored(db, su, migrator(db))


def test_p36_a_raw_server_value_with_a_no_break_space_names_a_schema_searched_before_public(db, layouts):
    """Found by this evidence work (2026-10-17). PostgreSQL keeps a no-break space inside a name; the parser treated it
    as whitespace (Python's isspace()). A raw value in the server's postgresql.conf, as an administrator might write
    it, 'public<no-break space>, public', makes PostgreSQL search a schema named 'public<no-break space>' before
    public; the parser read it as public, public, so while that schema did not exist the store saved and the preflight
    reported nothing. Now the store refuses before any insert and the preflight reports it. Once the schema exists with
    a shadow table, PostgreSQL searches it first and the guard accepts another filing's classification. The file is
    restored byte for byte."""
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    s = persist(w, words, 972001)
    other = persist(w, words, 972002)
    q(w, "set search_path = public")
    w.commit()
    assert_restored(db, su, migrator(db))
    odd = "public\N{NO-BREAK SPACE}"
    value = f"{odd}, public"
    ahead = f"schema {odd!r} is searched before public, whether or not it exists or holds a table now"
    conf = os.path.join(db.cluster.data, "postgresql.conf")
    with open(conf, "rb") as f:
        original = f.read()
    try:
        with open(conf, "ab") as f:
            f.write(f"\nsearch_path = '{value}'\n".encode("utf-8"))
        reload_until(db, su, (value, "configuration file"))
        a = db.conn()
        assert searched(a) == ["pg_catalog", "public"]                                 # not created yet
        a.rollback()
        f4_refused(a, w, words, 972003, re.escape(f"search_path {value!r}: {ahead}"))
        got = preflight.report(migrator(db))
        assert got["objects"] == [configured(r, "the server's configuration file", value, ahead)
                                  for r in ("cse_worker", "cse_reader")], got["objects"]
        assert got["file"] == [] and got["ledger"]["status"] == "verified" and not got["clean"]
        # why it matters: once that schema exists, PostgreSQL searches it first
        q(su, f'create schema "{odd}"; grant usage on schema "{odd}" to cse_worker; create table '
              f'"{odd}".report_document_classifications (id uuid primary key, cse_filing_id bigint, document_sha256 '
              f'text); grant select on "{odd}".report_document_classifications to cse_worker')
        q(su, f'insert into "{odd}".report_document_classifications values (%s, %s, %s)',
          (other.cid, s.fid, s.ext.document_sha256))
        b = db.conn()
        assert searched(b) == ["pg_catalog", odd, "public"]
        b.rollback()
        assert insert_extraction_row(b, s.fid, s.ext.document_sha256, other.cid) == str(other.cid)
        b.rollback()
        f4_refused(b, w, words, 972004, re.escape(f"search_path {value!r}: {ahead}; this session searches {odd!r} "
                                                  "before public"))
    finally:
        try:
            with open(conf, "wb") as f:
                f.write(original)
        finally:
            q(su, f'drop schema if exists "{odd}" cascade')
            reload_until(db, su, ('"$user", public', "default"))
    with open(conf, "rb") as f:
        assert f.read() == original
    assert q(su, "select count(*) from pg_file_settings where name = 'search_path'") == [(0,)]
    assert q(su, "select count(*) from pg_namespace where nspname like 'public_%%'") == [(0,)]
    assert_restored(db, su, migrator(db))
    assert f4_counts(su)["extractions"] == 2                                   # s and other only


def test_p37_a_search_path_value_is_split_as_postgresql_splits_it(db):
    """parse_search_path against PostgreSQL itself. For each value, every schema the parser names is created and usable;
    the schemas the session then searches (current_schemas) must be exactly the parsed ones, in order. Values PostgreSQL
    refuses are None. The one known divergence is fail-closed: an empty quoted name, accepted by PostgreSQL (no schema
    can have it), is refused by the parser. pg_temp listed first is p39's: current_schemas cannot report it for
    cse_worker."""
    import psycopg2
    from psycopg2 import sql
    su = db.su()
    values = ["public\N{NO-BREAK SPACE}, public", "\N{NO-BREAK SPACE}public, public", "public\x1c,public",
              "public\x1f,public", "public\x85,public", "public\N{EM SPACE},public", "a\v,public", "b\f,public",
              "b\r,public", "\tpublic\n", '"$user", public', "Public", '"Rf4 Q", public', "public , pg_temp",
              '"a""b", public', "pg_catalog, public", "public, pg_catalog"]
    special = ("$user", "pg_temp", "pg_catalog", "public")
    created = sorted({n for v in values for n in schema.parse_search_path(v) if n not in special})
    try:
        for n in created:
            q(su, sql.SQL("create schema {0}; grant usage on schema {0} to cse_worker").format(sql.Identifier(n)))
        a = db.conn()
        for v in values:
            names = schema.parse_search_path(v)
            want = [n for n in names if n not in ("$user", "pg_temp")]   # no cse_worker schema, no temporary schema
            want = want if "pg_catalog" in want else ["pg_catalog"] + want
            q(a, "select set_config('search_path', %s, false)", (v,))
            assert searched(a) == want, (v, names)
            a.rollback()
        for v in ("public,", ",public", '"x', "a b", "a,,b"):
            assert schema.parse_search_path(v) is None
            with pytest.raises(psycopg2.errors.InvalidParameterValue):
                q(a, "select set_config('search_path', %s, false)", (v,))
            a.rollback()
        assert schema.parse_search_path('"", public') is None                  # fail-closed, though PostgreSQL ...
        q(a, "select set_config('search_path', %s, false)", ('"", public',))
        assert searched(a) == ["pg_catalog", "public"]                                  # ... accepts it
        a.rollback()
    finally:
        for n in created:
            q(su, sql.SQL("drop schema if exists {}").format(sql.Identifier(n)))
    assert q(su, "select count(*) from pg_namespace where nspname = any(%s)", (created,)) == [(0,)]


def test_p38_a_real_operator_in_public_ahead_of_pg_catalog_subverts_the_guard_and_is_refused_and_reported(db, layouts):
    """An operator public.=(bigint, bigint) that is always true. Under the default path pg_catalog is searched first:
    the built-in = is used, the guard holds and the store saves. With cse_worker's search_path 'public, pg_catalog'
    PostgreSQL picks public's =, and the guard's c.cse_filing_id = new.cse_filing_id accepts a classification of
    another filing; the store refuses before any insert and the preflight reports pg_catalog after public. A path
    without public is refused and reported too. Everything is restored."""
    import psycopg2
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    s = persist(w, words, 973001)
    other = persist(w, words, 973002)
    assert_restored(db, su, m)
    try:
        q(su, "create function public.rf4_any_bigints_equal(bigint, bigint) returns boolean language sql immutable as "
              "'select true'; create operator public.= (leftarg = bigint, rightarg = bigint, function = "
              "public.rf4_any_bigints_equal)")
        d = db.conn()
        assert q(d, "select 1::bigint = 2::bigint") == [(False,)] and searched(d) == ["pg_catalog", "public"]
        d.rollback()
        with pytest.raises(psycopg2.errors.CheckViolation, match="is not of filing"):
            insert_extraction_row(d, s.fid, other.ext.document_sha256, other.cid)      # the built-in =
        d.rollback()
        f4_saved(d, w, words, 973003)                                    # present, but not searched first: safe
        q(su, "alter role cse_worker set search_path = public, pg_catalog")
        a = db.conn()
        assert q(a, "select 1::bigint = 2::bigint") == [(True,)] and searched(a) == ["public", "pg_catalog"]
        a.rollback()
        # why it matters: the guard now compares the filings with public's =
        assert insert_extraction_row(a, s.fid, other.ext.document_sha256, other.cid) == str(other.cid)
        a.rollback()
        f4_refused(a, w, words, 973004, re.escape("search_path 'public, pg_catalog': pg_catalog comes after public"))
        got = preflight.report(m)
        assert got["objects"] == [configured("cse_worker", LEVEL[2], "public, pg_catalog", "pg_catalog comes after "
                                             "public, so public's operators and functions come before the built-in "
                                             "ones")], got["objects"]
        assert got["file"] == [] and got["ledger"]["status"] == "verified" and not got["clean"]
        # a path without public
        shadow_schema(su)
        q(su, "alter role cse_worker set search_path = rf4_shadow")
        b = db.conn()
        assert searched(b) == ["pg_catalog", "rf4_shadow"]
        assert q(b, "select to_regclass('financial_f4_extractions')") == [(None,)]  # R-F4's tables are not found
        b.rollback()
        f4_refused(b, w, words, 973005, re.escape("search_path 'rf4_shadow': public is not in it"))
        assert preflight.report(m)["objects"] == [configured("cse_worker", LEVEL[2], "rf4_shadow", "public is not in "
                                                             "it, so an unqualified name resolves elsewhere or not at "
                                                             "all")]
    finally:
        q(su, "alter role cse_worker reset search_path; drop operator if exists public.= (bigint, bigint); drop "
              "function if exists public.rf4_any_bigints_equal(bigint, bigint); drop schema if exists rf4_shadow "
              "cascade")
    assert_restored(db, su, m)
    assert q(su, "select count(*) from pg_operator o join pg_namespace n on n.oid = o.oprnamespace where "
                 "n.nspname = 'public' and o.oprname = '='") == [(0,)]
    assert f4_counts(su)["extractions"] == 3                                   # s, other and 973003 only


def test_p39_a_path_listing_pg_temp_first_is_judged_without_creating_a_temporary_schema(db, layouts):
    """Found by this evidence work (2026-10-17). With pg_temp listed ahead of every existing schema and no temporary
    schema yet, PostgreSQL's current_schemas() creates one, which fails for cse_worker (no TEMPORARY in P1). The store's
    check called it, so 'pg_temp, public', which the invariant allows, was refused with an untyped
    InsufficientPrivilege, and 'pg_temp, rf4_shadow, public' was refused untyped too. Now the first saves without a
    temporary schema being created, and the second is SchemaUnavailable before any insert."""
    import psycopg2
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    assert_restored(db, su, m)
    a = db.conn()
    q(a, "set search_path = pg_temp, public")
    a.commit()
    with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="temporary tables"):
        searched(a)                                                      # PostgreSQL's own behaviour
    a.rollback()
    f4_saved(a, w, words, 974001)
    assert q(a, "select pg_my_temp_schema()::integer") == [(0,)]          # none was created
    a.rollback()
    try:
        shadow_schema(su)
        b = db.conn()
        q(b, "set search_path = pg_temp, rf4_shadow, public")
        b.commit()
        f4_refused(b, w, words, 974002, re.escape(f"search_path 'pg_temp, rf4_shadow, public': {AHEAD}"))
        assert q(b, "select pg_my_temp_schema()::integer") == [(0,)]
        b.rollback()
    finally:
        q(su, "drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)


# ------------------------------------------------------------- audit finding RF4-A1 (2026-10-20): the reader and verify

READER_SHADOWED = ("financial_f4_extractions", "financial_extraction_runs", "report_document_classifications")
ENUMERATION = "from financial_f4_extractions order by id"                         # verify's first unqualified query


def reader_shadows(su, tables=READER_SHADOWED, select=True):
    """rf4_shadow with an empty copy (LIKE public's) of each of `tables`, usable by cse_reader, readable unless
    select=False."""
    shadow_schema(su)
    for t in tables:
        q(su, f"create table rf4_shadow.{t} (like public.{t})")
        if select:
            q(su, f"grant select on rf4_shadow.{t} to cse_reader")


def reader_with_path(db, path):
    """A session that enters cse_reader by SET ROLE (as db.reader()) and then sets its own search path."""
    r = db.reader()
    q(r, f"set search_path = {path}")
    r.commit()
    return r


def hook_cursor(marker, hook):
    """A cursor class (a real psycopg2 cursor, so the reader's per-cursor JSON decoding works) that calls hook(conn)
    once, right after the first statement containing `marker` has run."""
    import psycopg2.extensions

    class HookCursor(psycopg2.extensions.cursor):
        pending = [hook]

        def execute(self, sql, args=None):
            out = super().execute(sql, args)
            if self.pending and marker in str(sql):
                self.pending.pop()(self.connection)
            return out
    return HookCursor


def tamper_a_cell(su, eid):
    q(su, "set session_replication_role = replica")                              # superuser only: triggers off
    try:
        q(su, "update financial_f4_cells k set raw_value = raw_value || '!' from financial_f4_statements s where "
              "s.id = k.statement_id and s.extraction_id = %s and k.seq = 0", (eid,))
    finally:
        q(su, "set session_replication_role = origin")


def kinds_of(report):
    return {(p["kind"], p.get("extraction_id") or p.get("run_id")) for p in report["problems"]}


def test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader(db, layouts):
    """RF4-A1. A reader session whose own search path puts rf4_shadow ahead of public: PostgreSQL then answers verify's
    and the reader's unqualified names from empty shadow copies, which would hide a corrupt extraction (verify clean,
    nothing found) and make saved F3 evidence look missing. verify refuses (not clean, nothing read) and every public
    reader entry point raises SchemaUnavailable, before any unqualified query; without SELECT on the shadows (so that
    reading one would fail) the refusal still comes first. Restored afterwards, the real problem is found again."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    good = persist(w, words, 990001)
    bad = persist(w, words, 990002)
    tamper_a_cell(su, bad.eid)
    assert ("corrupt", str(bad.eid)) in kinds_of(verify.verify(db.reader()))   # what a safe session finds
    assert_restored(db, su, m)
    q(su, "set lock_timeout = '10s'")                   # a lock wait below fails the test, never hangs it
    r = None
    try:
        reader_shadows(su)
        r = reader_with_path(db, "rf4_shadow, public")
        # PostgreSQL's own resolution in that session: the shadows answer the unqualified names
        assert q(r, "select count(*) from financial_f4_extractions") == [(0,)]
        assert q(r, "select count(*) from public.financial_f4_extractions") == [(2,)]
        r.rollback()
        report = verify.verify(r)
        assert not report["clean"] and (report["extractions"], report["runs"]) == (0, 0), report
        assert [p["kind"] for p in report["problems"]] == ["name_resolution"], report["problems"]
        assert "schema rf4_shadow is searched before public" in report["problems"][0]["detail"]
        key = reader.run_key(db.reader(), good.run_id)                          # read in a safe session
        for call in (lambda: reader.extraction_row(r, good.eid), lambda: reader.extraction_id_for(r, key),
                     lambda: reader.load_extraction(r, good.eid), lambda: reader.load_classification(r, good.cid),
                     lambda: reader.run_key(r, good.run_id), lambda: reader.evidence_for_run(r, good.run_id),
                     lambda: reader.rebuild_f5(r, good.run_id)):
            with pytest.raises(SchemaUnavailable, match="rf4_shadow"):
                call()
            r.rollback()
        # without SELECT on the shadows a read of one would fail: the refusal comes before any
        q(su, "revoke select on all tables in schema rf4_shadow from cse_reader")
        report = verify.verify(reader_with_path(db, "rf4_shadow, public"))
        assert [p["kind"] for p in report["problems"]] == ["name_resolution"] and not report["clean"]
    finally:
        rollback_quietly(r)               # a read that was not refused leaves its locks on the shadows: release them
        q(su, "drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)
    assert ("corrupt", str(bad.eid)) in kinds_of(verify.verify(db.reader()))


def test_p41_a_session_entering_cse_reader_by_set_role_is_judged_by_its_own_path_and_temporary_schema(db, layouts):
    """RF4-A1. SET ROLE cse_reader applies none of cse_reader's settings: a login with an unsafe search path of its own
    keeps it after SET ROLE, and the preflight, which checks cse_reader's configuration, cannot see it. A session whose
    temporary schema holds a shadow table read by cse_reader is the same. verify and the reader judge the session
    itself and refuse both. Everything is restored."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    good = persist(w, words, 991001)
    assert_restored(db, su, m)
    q(su, "set lock_timeout = '10s'")                   # a lock wait below fails the test, never hangs it
    c = t = None
    try:
        reader_shadows(su)
        q(su, "create role rf4_reader_login login; grant cse_reader to rf4_reader_login; "
              f"grant connect on database {db.name} to rf4_reader_login; "
              "alter role rf4_reader_login set search_path = rf4_shadow, public")
        c = db.cluster.connect(dbname=db.name, user="rf4_reader_login")
        db._open.append(c)
        c.autocommit = True
        q(c, "set role cse_reader")
        c.autocommit = False
        assert q(c, "select current_user::text, session_user::text, current_setting('search_path')") == \
            [("cse_reader", "rf4_reader_login", "rf4_shadow, public")]           # the login's path, kept
        assert q(c, "select count(*) from financial_f4_extractions") == [(0,)]   # the shadow answers
        c.rollback()
        assert preflight.report(m)["clean"]                   # cse_reader's own configuration is safe
        report = verify.verify(c)
        assert [p["kind"] for p in report["problems"]] == ["name_resolution"] and not report["clean"]
        with pytest.raises(SchemaUnavailable, match="rf4_shadow"):
            reader.load_extraction(c, good.eid)
        c.rollback()
        c.close()
        # a temporary shadow made by the login before it enters cse_reader
        t = db.cluster.connect(dbname=db.name, user="postgres")
        db._open.append(t)
        t.autocommit = True
        q(t, "create temporary table financial_f4_extractions (like public.financial_f4_extractions); "
             "grant select on financial_f4_extractions to cse_reader; set role cse_reader")
        t.autocommit = False
        assert q(t, "select count(*) from financial_f4_extractions") == [(0,)]   # the temporary table answers
        t.rollback()
        report = verify.verify(t)
        assert [p["kind"] for p in report["problems"]] == ["name_resolution"] and not report["clean"]
        assert "temporary schema" in report["problems"][0]["detail"]
        t.close()
    finally:
        for session in (c, t):            # a read that was not refused leaves its locks on the shadows: release them
            rollback_quietly(session)
        q(su, f"revoke connect on database {db.name} from rf4_reader_login; drop role if exists rf4_reader_login; "
              "drop schema if exists rf4_shadow cascade")
    assert_restored(db, su, m)
    assert q(su, "select count(*) from pg_roles where rolname = 'rf4_reader_login'") == [(0,)]
    assert verify.verify(db.reader())["clean"]


def test_p42_a_safe_path_verifies_in_one_read_only_repeatable_read_transaction(db, layouts):
    """The safe path works: verify is clean and the reader reads, as cse_reader. verify's transaction is REPEATABLE
    READ and READ ONLY, set before its first query: inside it, PostgreSQL refuses a write even from cse_worker, which
    is allowed to insert. verify writes nothing."""
    import psycopg2
    w = db.conn()
    good = persist(w, a_layout_with_cells(layouts), 992001)
    r = db.reader()
    report = verify.verify(r)
    assert report["clean"] and (report["extractions"], report["runs"], report["rebuilt"]) == (1, 1, 1), report
    assert report["snapshot"]["isolation"] == "repeatable read" and report["snapshot"]["read_only"] is True
    assert codec.f4_content_sha256(reader.load_extraction(r, good.eid)) == codec.f4_content_sha256(good.ext)
    r.rollback()
    seen = {}

    def probe(conn):
        with conn.cursor() as c:
            c.execute("select current_setting('transaction_isolation'), current_setting('transaction_read_only')")
            seen["transaction"] = c.fetchone()
            c.execute("savepoint rf4_probe")
            try:
                c.execute("insert into financial_f4_rows default values")
            except psycopg2.errors.ReadOnlySqlTransaction as exc:
                seen["write"] = type(exc).__name__
            c.execute("rollback to savepoint rf4_probe")
    wk = db.conn()                                                               # cse_worker: INSERT is granted
    wk.cursor_factory = hook_cursor(ENUMERATION, probe)
    before = f4_counts(db.su())
    report = verify.verify(wk)
    assert seen == {"transaction": ("repeatable read", "on"), "write": "ReadOnlySqlTransaction"}, seen
    assert report["clean"] and f4_counts(db.su()) == before


def test_p43_verify_reads_one_snapshot_while_another_transaction_commits(db, layouts):
    """While verify runs, another transaction commits a new filing (F3, F4 and F5) after verify has listed the
    extractions, and in a second run appends a row to an extraction verify will read. verify reports the database as
    it was at its snapshot: 1 extraction and 1 run, not 1 and 2; the appended row is not read. Neither change is taken
    as clean evidence of the new state: the next verify, at a later snapshot, sees the new filing and reports the
    appended row as incomplete (the extraction, and the run whose rebuild reads it)."""
    words = a_layout_with_cells(layouts)
    w = db.conn()
    first = persist(w, words, 993001)

    def commit_a_filing(_conn):
        persist(db.conn(), words, 993002)
    r = db.reader()
    r.cursor_factory = hook_cursor(ENUMERATION, commit_a_filing)
    report = verify.verify(r)
    assert (report["extractions"], report["runs"], report["clean"]) == (1, 1, True), report
    later = verify.verify(db.reader())
    assert (later["extractions"], later["runs"], later["clean"]) == (2, 2, True), later
    assert report["snapshot"]["transaction_started_at"] < later["snapshot"]["transaction_started_at"]
    assert report["snapshot"]["pg_snapshot"] != later["snapshot"]["pg_snapshot"]       # the commit came between

    sid = q(w, "select id from financial_f4_statements where extraction_id = %s order by statement_index limit 1",
            (first.eid,))[0][0]
    w.commit()

    def append_a_row(_conn):
        a = db.conn()
        q(a, "insert into financial_f4_rows (statement_id, seq, row_index, page, label_raw, label_normalized, y0, y1, "
             "line_count, wrapped, kind, status, reasons) values (%s, 9999, 9999, 1, 'x', 'x', '1', '2', 1, false, "
             "'values', 'ok', '[]')", (sid,))
        a.commit()
    r = db.reader()
    r.cursor_factory = hook_cursor(ENUMERATION, append_a_row)
    during = verify.verify(r)
    assert during["clean"] and (during["extractions"], during["runs"]) == (2, 2), during
    after = verify.verify(db.reader())
    assert kinds_of(after) == {("incomplete", str(first.eid)), ("incomplete", str(first.run_id))}, after["problems"]


# ----------------------------------------------- verify's snapshot metadata and failure paths (2026-10-20, after RF4-A1)

TABLES_READ = ("financial_f4_extractions", "financial_f4_statements", "financial_f4_columns", "financial_f4_rows",
               "financial_f4_cells", "report_document_classifications", "report_statement_periods",
               "report_classification_evidence", "financial_extraction_runs")
STATE = ("select pg_catalog.pg_current_snapshot()::pg_catalog.text, pg_catalog.transaction_timestamp(), "
         "pg_catalog.current_setting('transaction_isolation'), pg_catalog.current_setting('transaction_read_only')")


def recording_cursor(seen, *, state=False, marker=None, hook=None):
    """A cursor class (a real psycopg2 cursor) that appends every statement it runs to `seen`. With state=True each
    entry is (statement, snapshot, transaction start, isolation, read only), read right after the statement by a
    separate plain cursor of the same connection, so in the same transaction (not after SET TRANSACTION, which must
    precede every query). hook(conn) runs once, after the first statement containing `marker`."""
    import psycopg2.extensions

    class Recording(psycopg2.extensions.cursor):
        pending = [hook] if hook else []

        def execute(self, sql, args=None):
            out = super().execute(sql, args)
            if not state:
                seen.append(str(sql))
            elif not str(sql).startswith("set transaction"):
                side = psycopg2.extensions.cursor(self.connection)
                try:
                    side.execute(STATE)
                    seen.append((str(sql), *side.fetchone()))
                finally:
                    side.close()
            if self.pending and marker in str(sql):
                self.pending.pop()(self.connection)
            return out
    return Recording


def persist_noting_xid(w, words, fid):
    """persist()'s rows (F3, F4, F5) saved in one transaction; that transaction's id (xid8, as text)."""
    cls, ext, res = R.chain(words, fid, None)
    seed_filing(w, fid)
    xid = q(w, "select pg_current_xact_id()::text")[0][0]
    save_in_persist_order(w, cls, ext, res)
    return xid


def test_p44_every_statement_of_verify_runs_under_the_snapshot_its_report_names(db, layouts):
    """The report's pg_snapshot is the snapshot of every statement verify runs: recorded after each one (the check,
    the snapshot query, both enumerations, every reader check and every F4, F3 and F5 read), on the same connection,
    the snapshot, the transaction's start and its settings equal the report's, every time. It identifies what verify
    saw: a filing committed by another transaction after the enumeration is not visible in it
    (pg_visible_in_snapshot), the earlier filing is, and the next verify's snapshot sees both. transaction_started_at
    is the transaction's start, the same for every statement; the snapshot is named by its identity, not a time."""
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    first = persist_noting_xid(w, words, 994001)
    mid = {}

    def commit_a_filing(_conn):
        mid["xid"] = persist_noting_xid(db.conn(), words, 994002)
    seen = []
    r = db.reader()
    r.cursor_factory = recording_cursor(seen, state=True, marker=ENUMERATION, hook=commit_a_filing)
    report = verify.verify(r)
    assert (report["extractions"], report["runs"], report["rebuilt"], report["clean"]) == (1, 1, 1, True), report
    snap = report["snapshot"]
    assert set(snap) == {"isolation", "read_only", "pg_snapshot", "transaction_started_at"}, snap
    assert {(s, t.isoformat(), i, ro) for _, s, t, i, ro in seen} == {
        (snap["pg_snapshot"], snap["transaction_started_at"], "repeatable read", "on")}, seen
    statements = [s for s, *_ in seen]
    assert statements[:2] == [schema._SESSION_QUERY, verify._SNAPSHOT] and "xid" in mid
    assert {t for t in TABLES_READ if any(t in s for s in statements)} == set(TABLES_READ)
    assert statements.count(schema._SESSION_QUERY) >= 3                     # verify's check and the reader's
    assert q(su, "select pg_visible_in_snapshot(%s::xid8, %s::pg_snapshot), "
                 "pg_visible_in_snapshot(%s::xid8, %s::pg_snapshot)",
             (first, snap["pg_snapshot"], mid["xid"], snap["pg_snapshot"])) == [(True, False)]
    later = verify.verify(db.reader())
    assert (later["extractions"], later["runs"], later["clean"]) == (2, 2, True), later
    assert q(su, "select pg_visible_in_snapshot(%s::xid8, %s::pg_snapshot)",
             (mid["xid"], later["snapshot"]["pg_snapshot"])) == [(True,)]
    assert snap["transaction_started_at"] < later["snapshot"]["transaction_started_at"]


def test_p45_an_autocommit_session_is_refused_before_verify_sends_a_statement(db, layouts):
    """psycopg2's set_session(REPEATABLE READ, read only, autocommit) sets the session's defaults, and PostgreSQL then
    runs every statement as its own transaction: SET TRANSACTION only warns, current_setting reports 'repeatable read'
    and 'on', and a commit between two statements is seen by the second, under a new snapshot. verify's isolation check
    could not tell; verify refuses the connection before it sends any statement (the previous code ran, its reads at
    several snapshots, a filing committed mid-run counted as a run but not as an extraction)."""
    words = a_layout_with_cells(layouts)
    persist(db.conn(), words, 995001)
    r = db.reader()
    r.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=True)
    before = q(r, "select current_setting('transaction_isolation'), current_setting('transaction_read_only'), "
                  "pg_current_snapshot()::text")[0]
    xid = persist_noting_xid(db.conn(), words, 995002)
    after = q(r, "select pg_current_snapshot()::text, pg_visible_in_snapshot(%s::xid8, pg_current_snapshot())",
              (xid,))[0]
    assert before[:2] == ("repeatable read", "on") and after[0] != before[2] and after[1] is True, (before, after)
    q(r, "set transaction isolation level repeatable read, read only")
    assert r.notices[-1].startswith("WARNING:  SET TRANSACTION can only be used in transaction blocks")
    seen = []
    r.cursor_factory = recording_cursor(seen, marker=ENUMERATION,
                                        hook=lambda _conn: persist(db.conn(), words, 995003))
    try:
        report = verify.verify(r)
    except ValueError as exc:
        assert "autocommit" in str(exc)
    else:
        pytest.fail(f"verify ran on an autocommit connection: {report}")
    assert seen == [] and f4_counts(db.su())["extractions"] == 2


def test_p46_a_session_ended_mid_run_raises_its_own_error_not_the_rollbacks(db, layouts):
    """The server ends verify's session mid-run (pg_terminate_backend; an administrator or a restart does the same):
    the next statement raises OperationalError and psycopg2 marks the connection closed, so the rollback that follows
    raises InterfaceError ('connection already closed'). verify raises the OperationalError, the failure that
    happened, with the rollback's error noted on it; it returns no report, so nothing is called clean. A new
    session's verify is clean."""
    import psycopg2
    persist(db.conn(), a_layout_with_cells(layouts), 996001)
    su = db.su()

    def terminate(conn):
        assert q(su, "select pg_terminate_backend(%s, 10000)", (conn.get_backend_pid(),)) == [(True,)]
    r = db.reader()
    r.cursor_factory = hook_cursor(ENUMERATION, terminate)
    with pytest.raises(psycopg2.Error) as got:
        verify.verify(r)
    assert isinstance(got.value, psycopg2.OperationalError), repr(got.value)
    assert r.closed and getattr(got.value, "__notes__", None) == [
        "verify's rollback also failed: InterfaceError: connection already closed"], got.value
    assert verify.verify(db.reader())["clean"]


# ------------------------------------------- HB-4 integration gaps (docs/R_F4_IMPLEMENTATION.md section 6.2, design 6)

def lock_wait(su, pid, alive):
    """True once session `pid` waits on a lock (a row another transaction holds, as pg_stat_activity shows it); False
    if alive() turns false or 30 seconds pass first. `su` is an autocommit superuser session."""
    for _ in range(600):
        if q(su, "select wait_event_type from pg_stat_activity where pid = %s", (pid,))[0][0] == "Lock":
            return True
        if not alive():
            return False
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("first", ["hb4", "run"])
def test_p47_hb4s_slice_and_f5s_run_persisting_one_filing_at_once(env, layouts, monkeypatch, first):
    """HB-4's slice and F5's run() persist the same filing at once, each on its own connection. `first` holds its
    filing transaction open after its F3, F4 and F5 saves; the other then waits on those uncommitted rows (a lock wait
    in pg_stat_activity) and, once the first commits, finds F3, F4 and F5 already present (identical evidence: HB-4's
    d2). One set of rows, no deadlock, nothing committed while the first held; HB-4's item is persisted under one claim
    and one download and its lease released as completed; verify is clean. While HB-4 holds its filing, a second HB-4
    slice cannot start: P2's global lock is held for the whole slice, so it is SliceBusy, recorded as a busy wakeup,
    with no request and no claim."""
    from worker.backfill_discovery import accounting
    from worker.backfill_transport.errors import SliceBusy
    fid = 988001
    words = a_layout_with_cells(layouts)
    w = open_hb4(env, [fid])
    by = ByDocument({fid: words})
    url = H.url_of(H.path_of(fid))
    cdn = H.ScriptedCDN(env.clock, {url: H.ok_doc(fid)})
    hb4_conn, run_conn, su = env.conn(), env.conn(), env.conn("postgres")
    su.autocommit = True
    holder, waiter = (hb4_conn, run_conn) if first == "hb4" else (run_conn, hb4_conn)
    held, release = threading.Event(), threading.Event()
    got = {}

    def run_f5_now():
        try:
            got["run"] = run_f5(run_conn, [fid], {fid: words}, fetcher=FakeFetcher({url: _ok(fid)}))[0]
        except BaseException as exc:  # noqa: BLE001 - reported below
            got["run"] = exc

    def watch_the_waiter(alive):
        got["waited"] = lock_wait(su, waiter.get_backend_pid(), alive)
        got["committed_meanwhile"] = rows_of(su, [fid])
    runner = threading.Thread(target=run_f5_now)
    watcher = threading.Thread(target=lambda: (watch_the_waiter(lambda: True), release.set()))

    def while_hb4_holds():                                  # main thread, inside HB-4's open filing transaction
        cdn2 = H.ScriptedCDN(env.clock, {url: H.ok_doc(fid)})
        with pytest.raises(SliceBusy):
            env.slice(cdn2, install_sigterm=False, extract_text=by.text, extract_words=by.words).__enter__()
        got["second_slice_requests"] = len(cdn2.calls)
        runner.start()
        watch_the_waiter(runner.is_alive)
    real_f5_save = PostgresCandidateStore.save

    def f5_save(self, result, cid, link=None):
        out = real_f5_save(self, result, cid, link)
        if self.conn is holder and not held.is_set():       # the first: F3, F4 and F5 saved, not yet committed
            held.set()
            if first == "hb4":
                while_hb4_holds()
            else:
                assert release.wait(60)
        return out
    monkeypatch.setattr(PostgresCandidateStore, "save", f5_save)
    ds = env.slice(cdn, conn=hb4_conn, extract_text=by.text, extract_words=by.words)
    err = None
    try:
        if first == "run":
            runner.start()
            assert held.wait(60), got
            watcher.start()
        with ds:
            ds.run()
    except BaseException as exc:  # noqa: BLE001 - asserted below
        err = exc
    finally:
        release.set()
        for t in (runner, watcher):
            if t.ident is not None:
                t.join(60)
    assert not runner.is_alive() and not watcher.is_alive()
    assert err is None and not isinstance(got["run"], BaseException), (err, got)
    assert got["waited"] is True and got["committed_meanwhile"] == (0, 0, 0), got
    it = H.item_of(w, fid)
    ev = ledger.events(w, it["id"])[-1]
    run_stored = [(r["stored"]["f3"], r["stored"]["f4"], r["stored"]["f5"]) for r in got["run"]["records"]]
    if first == "hb4":
        assert run_stored == [("already_present",) * 3] and (ev["details"]["f3"], ev["details"]["f5"]) == \
            ("inserted", "inserted"), (run_stored, ev)
        assert got["second_slice_requests"] == 0
        assert q(su, "select count(*) from backfill_wakeups where state = 'skipped' and result = 'busy'") == [(1,)]
    else:
        assert run_stored == [("inserted",) * 3] and (ev["details"]["f3"], ev["details"]["f5"]) == \
            ("already_present", "already_present"), (run_stored, ev)
    assert (ev["state"], ev["action"]) == ("persisted", "record") and H.state(w, it) == "persisted"
    assert accounting.claims(w, it["id"]) == 1 and len(cdn.calls) == 1
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "completed")
    assert rows_of(su, [fid]) == (1, 1, 1) and f4_counts(su)["extractions"] == 1
    assert q(su, "select id::text from financial_extraction_runs where cse_filing_id = %s", (fid,)) == \
        [(str(ev["f5_run_id"]),)]
    report = verify.verify(_reader_of(env))
    assert report["clean"] and report["extractions"] == report["runs"] == report["rebuilt"] == 1, report


def env_without_0018(cluster, tmp_path, monkeypatch):
    """HB-4's test environment on a database migrated through P1's runner to 0017 only (as p9 builds it)."""
    name = f"rf4_{uuid.uuid4().hex[:10]}"
    cluster.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", f"dbname={name}", "-f", S.BOOTSTRAP)
    m = cluster.connect(dbname=name, user="cse_migrator")
    try:
        out = mig.apply(m, mig.discover(S.MIGDIR), target=17, log=lambda _: None)
        assert out["applied"][-1] == "0017_f8_asof_configuration.sql"
    finally:
        m.close()
    with monkeypatch.context() as m:
        m.setattr(S, "fresh_db", lambda _cluster: name)
        return H.Env(cluster, tmp_path, monkeypatch)


@pytest.mark.parametrize("mx, final", [(3, "retry_wait"), (1, "failed")])
def test_p48_hb4_on_a_database_without_0018_rolls_the_filing_back_and_records_it(
        cluster, tmp_path, monkeypatch, layouts, mx, final):
    """No frozen preflight checks 0018 (design section 7), so HB-4 runs on a database without it. Inside the filing's
    one transaction F3's save and the issuer link succeed, then R-F4's in-band check refuses (SchemaUnavailable, naming
    0018): the whole filing rolls back (no F3 or F5 row; the filing's issuer links exactly as HB-3's identity pass left
    them in the setup; R-F4's tables do not exist) and HB-4 records it through its own path, retry_wait or failed, with
    the reason; one claim, one download, the lease released. With 0018 applied (the operational prerequisite), the
    retry downloads once more and saves F3, F4 and F5 once."""
    from worker.backfill_discovery import accounting
    e = env_without_0018(cluster, tmp_path, monkeypatch)
    try:
        fid = 989001
        w = open_hb4(e, [fid], item_max_attempts=mx)
        assert q(w, "select to_regclass('public.financial_f4_extractions') is null") == [(True,)]
        links = "select id, status, basis from filing_issuer_links where cse_filing_id = %s order by id"
        linked = q(w, links, (fid,))                                  # HB-3's identity pass linked the filing
        w.rollback()
        by = ByDocument({fid: a_layout_with_cells(layouts)})
        cdn = H.ScriptedCDN(e.clock, {H.url_of(H.path_of(fid)): H.ok_doc(fid)})
        ds, err = hb4_slice(e, cdn, by)
        it = H.item_of(w, fid)
        ev = ledger.events(w, it["id"])[-1]
        assert err is None and (ev["state"], ev["action"]) == (final, "record"), ev
        assert "rolled back" in ev["reason"] and "SchemaUnavailable" in ev["reason"] and "0018" in ev["reason"], ev
        assert (H.f3_rows(w, fid), H.f5_rows(w, fid)) == (0, 0)
        assert q(w, links, (fid,)) == linked                          # the filing's links as the setup left them
        assert accounting.claims(w, it["id"]) == 1 and len(cdn.calls) == 1
        assert H.lease_row(w, ds.sl.lease_id) == ("released", "completed")
        w.rollback()
        if final == "failed":
            return
        assert mig.apply(e.owner_conn(), mig.discover(S.MIGDIR), log=lambda _: None)["applied"] == \
            [preflight.MIGRATION]
        ds, err = hb4_slice(e, cdn, by)                                  # the retry: one more download, saved once
        assert err is None and H.state(w, it) == "persisted" and len(cdn.calls) == 2
        assert rows_of(w, [fid]) == (1, 1, 1) and f4_counts(w)["extractions"] == 1
        w.rollback()
        assert verify.verify(_reader_of(e))["clean"]
    finally:
        e.close()


# --------------------------------------------------------------- verify's scope; the preflight's columns (2026-10-20)

def test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1(db, layouts, monkeypatch, capsys):
    """verify(limit=N) checks the first N extractions and the first N F5 runs, by id. Here they are intact while a
    filing outside them has a corrupt cell: the partial scan finds nothing, yet is not clean (scope: complete false)
    and the command exits 1, never 0. A full scan finds the corruption. A limit covering every row is still partial.
    A limit of 0 or below is refused before anything is sent (ValueError), and by the command as a usage error (2)."""
    import psycopg2.extensions
    from worker import db as worker_db
    from worker.financial_f4_store import __main__ as cli
    words = a_layout_with_cells(layouts)
    w, su = db.conn(), db.su()
    saved = [persist(w, words, 997101 + i) for i in range(3)]
    monkeypatch.setattr(worker_db, "get_connection", db.reader)
    assert cli.main(["verify"]) == 0                                      # a clean full scan: the only exit 0
    out = json.loads(capsys.readouterr().out)
    assert out["clean"] and out["scope"] == {"complete": True, "limit": None} and out["extractions"] == 3, out
    every = verify.verify(db.reader(), limit=3)                           # covers every row: still partial
    assert (every["extractions"], every["runs"], every["problems"], every["clean"]) == (3, 3, [], False), every
    assert every["scope"]["complete"] is False and every["scope"]["limit"] == 3
    first_eid = q(su, "select id::text from financial_f4_extractions order by id limit 1")[0][0]
    first_run = q(su, "select id::text from financial_extraction_runs order by id limit 1")[0][0]
    bad = next(s for s in saved if str(s.eid) != first_eid and str(s.run_id) != first_run)
    tamper_a_cell(su, bad.eid)                                            # outside what limit=1 reads
    partial = verify.verify(db.reader(), limit=1)
    assert (partial["extractions"], partial["runs"], partial["problems"]) == (1, 1, []), partial
    assert partial["clean"] is False and (partial["scope"]["complete"], partial["scope"]["limit"]) == (False, 1)
    assert cli.main(["verify", "--limit", "1"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["problems"] == [] and out["clean"] is False and out["scope"]["complete"] is False, out
    full = verify.verify(db.reader())
    assert ("corrupt", str(bad.eid)) in kinds_of(full) and not full["clean"] and full["scope"]["complete"], full
    assert cli.main(["verify"]) == 1
    capsys.readouterr()
    for limit in (0, -1):
        r = db.reader()
        with pytest.raises(ValueError, match="limit must be None"):
            verify.verify(r, limit=limit)
        assert r.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE     # nothing was sent
        with pytest.raises(SystemExit) as e:
            cli.main(["verify", f"--limit={limit}"])
        assert e.value.code == 2
    capsys.readouterr()


def live_columns(su):
    """Each table's live columns {name: (type, NOT NULL, default)}, read with the preflight's own query; none is an
    identity or generated column or has another collation."""
    out = {}
    for t in codec.TABLES:
        rows = q(su, preflight._COLUMNS_QUERY, (f"public.{t}",))
        assert [r for r in rows if (r[4], r[5], r[6]) != ("", "", None)] == [], (t, rows)
        out[t] = {n: (typ, not_null, default) for n, typ, not_null, default, _, _, _ in rows}
    return out


def test_p50_the_preflight_reports_column_drift_that_no_constraint_shows(db):
    """On PostgreSQL 17 NOT NULL is a column property, not a pg_constraint row, so a dropped NOT NULL on a critical
    column (the extraction's content seal, a cell's statement) leaves every constraint as 0018 creates it, as do a
    changed type, default, identity or collation and a renamed or an extra column. The preflight reports each,
    exactly, in its object check (the file and the ledger still pass: the three checks stay apart), and is not clean.
    Each change is undone in `finally`; at the end every column and constraint is 0018's and the preflight is clean."""
    su, m = db.su(), migrator(db)
    q(su, "set search_path = public")
    assert live_columns(su) == preflight.COLUMN_DEFINITIONS                   # 0018 as P1's runner applies it
    assert preflight.report(m)["clean"]
    e, s, k, r, c = (f"financial_f4_{x}" for x in ("extractions", "statements", "columns", "rows", "cells"))
    cases = [  # (break, restore, exactly what the preflight must report)
        (f"alter table {e} alter column f4_content_sha256 drop not null",
         f"alter table {e} alter column f4_content_sha256 set not null",
         [f"{e}: column f4_content_sha256 is nullable, expected NOT NULL"]),
        (f"alter table {c} alter column statement_id drop not null",
         f"alter table {c} alter column statement_id set not null",
         [f"{c}: column statement_id is nullable, expected NOT NULL"]),
        (f"alter table {k} alter column header_raw type varchar(160)",          # F6.2's former 160-character clip
         f"alter table {k} alter column header_raw type text",
         [f"{k}: column header_raw is character varying(160), expected text"]),
        (f"alter table {c} alter column scale type integer",
         f"alter table {c} alter column scale type bigint",
         [f"{c}: column scale is integer, expected bigint"]),
        (f"alter table {e} alter column recorded_at drop default",
         f"alter table {e} alter column recorded_at set default now()",
         [f"{e}: column recorded_at has default none, expected now()"]),
        (f"alter table {s} alter column statement_index add generated by default as identity",
         f"alter table {s} alter column statement_index drop identity",
         [f"{s}: column statement_index is an identity column (GENERATED BY DEFAULT AS IDENTITY); migration 0018 "
          "creates none"]),
        (f'alter table {e} alter column word_extractor type text collate "C"',
         f"alter table {e} alter column word_extractor type text",              # the type's default collation again
         [f"{e}: column word_extractor has collation C, not its type's default"]),
        (f"alter table {r} rename column note_ref_raw to note_ref",
         f"alter table {r} rename column note_ref to note_ref_raw",
         [f"{r}: column note_ref_raw is missing", f"{r}: column note_ref (text) is not one migration 0018 creates"]),
        (f"alter table {r} add column rf4_extra text",
         f"alter table {r} drop column rf4_extra",
         [f"{r}: column rf4_extra (text) is not one migration 0018 creates"]),
    ]
    for break_sql, restore_sql, want in cases:
        q(su, break_sql)                                                  # as the superuser
        try:
            assert live_constraints(su) == preflight.CONSTRAINT_DEFINITIONS, break_sql   # no constraint shows it
            got = preflight.report(m)
            assert got["objects"] == want and not got["clean"], (break_sql, got["objects"])
            assert got["file"] == [] and got["ledger"]["status"] == "verified"     # the three checks stay apart
        finally:
            q(su, restore_sql)
    assert live_columns(su) == preflight.COLUMN_DEFINITIONS
    assert live_constraints(su) == preflight.CONSTRAINT_DEFINITIONS and preflight.report(m)["clean"]


# -------------------------------------------------------- table properties; row-level security and verify (2026-10-20)

CHECKED_TABLES = codec.TABLES + preflight.READ_TABLES
ORDINARY_TABLE = ("r", "p", False, False, False, [], [], [])   # kind, persistence, RLS, forced, partition, parents, ...


def rls_enabled(t):
    return (f"{t} has row-level security enabled: a role it applies to sees only the rows its policies allow (none "
            "without a policy), so verify and the reader could miss evidence; expected disabled")


def rollback_quietly(c):
    """End `c`'s transaction, if it has one, so its locks cannot block a restoring ALTER TABLE."""
    if c is not None and not c.closed:
        try:
            c.rollback()
        except Exception:  # noqa: BLE001 - a broken connection holds no locks
            pass


def no_table_drift(su):
    """Every checked table is ordinary and permanent, without row-level security, policy, partitioning or
    inheritance (read with the preflight's own query); no policy anywhere in the database and no rf4_ relation."""
    assert {t: q(su, preflight._TABLE_QUERY, (f"public.{t}",))[0] for t in CHECKED_TABLES} == \
        dict.fromkeys(CHECKED_TABLES, ORDINARY_TABLE)
    assert q(su, "select count(*) from pg_policy") == [(0,)]
    assert q(su, "select relname from pg_class where relname like 'rf4\\_%%'") == []


def test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift(db):
    """Migrations 0005, 0008 and 0018 create ordinary, permanent tables with no row-level security, policy,
    partitioning or inheritance. Enabling or forcing row-level security, a policy (even with row-level security off),
    UNLOGGED, a child table (a read of the parent includes the child's rows) and an inheritance parent, on 0018's
    tables or on F5's run table that verify reads, are each refused by the preflight, exactly, in its object check;
    the file and the ledger still pass (the three checks apart). The database is clean before and after each change;
    each is undone in `finally`, and no policy, table or altered persistence is left behind. Attaching a table as a
    partition is not exercised here (u26 covers its report)."""
    su, m = db.su(), migrator(db)
    q(su, "set search_path = public; set lock_timeout = '10s'")    # a lock wait fails the test, never hangs it
    no_table_drift(su)
    assert preflight.report(m)["clean"]
    c, s, r, runs = "financial_f4_cells", "financial_f4_statements", "financial_f4_rows", "financial_extraction_runs"
    cases = [  # (break, restore, exactly what the preflight must report)
        (f"alter table {c} enable row level security", f"alter table {c} disable row level security",
         [rls_enabled(c)]),
        (f"alter table {s} force row level security", f"alter table {s} no force row level security",
         [f"{s} forces row-level security on its owner; expected not"]),
        (f"create policy rf4_policy on {r} for select using (true)", f"drop policy rf4_policy on {r}",
         [f"{r} has row-level security policy(ies) rf4_policy; expected none"]),
        (f"alter table {runs} enable row level security", f"alter table {runs} disable row level security",
         [rls_enabled(runs)]),                                            # F5's table (0008), read by verify
        (f"alter table {c} set unlogged", f"alter table {c} set logged",      # nothing references the cells
         [f"{c} is UNLOGGED (emptied after a crash, never replicated), expected permanent"]),
        (f"create table rf4_child () inherits ({c})", "drop table rf4_child",
         [f"{c} has child table(s) rf4_child, whose rows a read of {c} includes; expected none"]),
        (f"create table rf4_parent (); alter table {c} inherit rf4_parent",
         f"alter table {c} no inherit rf4_parent; drop table rf4_parent",
         [f"{c} inherits from rf4_parent; expected neither"]),
    ]
    for break_sql, restore_sql, want in cases:
        q(su, break_sql)                                                  # as the superuser
        try:
            got = preflight.report(m)
            assert got["objects"] == want and not got["clean"], (break_sql, got["objects"])
            assert got["file"] == [] and got["ledger"]["status"] == "verified"     # the three checks stay apart
        finally:
            q(su, restore_sql)
        assert preflight.report(m)["clean"], restore_sql                  # clean again after each
    no_table_drift(su)


def test_p52_row_level_security_can_hide_evidence_from_verify_and_the_preflight_refuses_it(db, layouts):
    """verify reads what its role can see. cse_reader (NOBYPASSRLS, entered by SET ROLE as the reader is) is genuinely
    restricted by row-level security: a policy hides rows from it, and without a policy it sees none, while the
    superuser sees them all. Two configurations make verify report clean while evidence it never read is bad:
    (1) a policy on F5's run table (0008, not one of 0018's) hides the one run whose stored content_sha256 is wrong:
    verify checks 3 extractions and 2 runs and is clean; (2) row-level security without a policy on 0018's
    extractions and on the run table: verify sees nothing and is clean, with a corrupt extraction stored. verify is
    unchanged (not a repair; it cannot see what its role cannot). The preflight reads the catalogue, not the rows, and
    refuses both: verify is to be run only after it is clean. Restored in `finally`; clean before and after."""
    words = a_layout_with_cells(layouts)
    w, su, m = db.conn(), db.su(), migrator(db)
    q(su, "set lock_timeout = '10s'")                   # a lock wait below fails the test, never hangs it
    a, b, _ = (persist(w, words, 998001 + i) for i in range(3))
    w.rollback()            # persist's read-back left a transaction open, locking the tables the ALTERs below change
    ext, runs = "financial_f4_extractions", "financial_extraction_runs"
    r = None
    assert preflight.report(m)["clean"] and verify.verify(db.reader())["clean"]
    q(su, "set session_replication_role = replica")                      # superuser only: triggers off
    try:
        q(su, f"update {runs} set content_sha256 = %s where id = %s", ("0" * 64, b.run_id))
    finally:
        q(su, "set session_replication_role = origin")
    assert kinds_of(verify.verify(db.reader())) == {("f5_mismatch", str(b.run_id))}      # all visible: found
    try:   # (1) a policy hides b's run from cse_reader
        q(su, f"alter table {runs} enable row level security; create policy rf4_hide on {runs} for select to "
              f"cse_reader using (cse_filing_id <> {int(b.fid)})")
        r = db.reader()
        assert q(r, f"select count(*) from {runs}") == [(2,)] and q(su, f"select count(*) from {runs}") == [(3,)]
        r.rollback()
        report = verify.verify(r)
        assert (report["extractions"], report["runs"], report["problems"], report["clean"]) == (3, 2, [], True), \
            report                                                       # clean, with b's bad run unchecked
        got = preflight.report(m)
        assert got["objects"] == [rls_enabled(runs), f"{runs} has row-level security policy(ies) rf4_hide; "
                                  "expected none"], got["objects"]
        assert not got["clean"] and got["file"] == [] and got["ledger"]["status"] == "verified"
    finally:
        rollback_quietly(r)                                             # its locks would block the restore
        q(su, f"drop policy if exists rf4_hide on {runs}; alter table {runs} disable row level security")
    assert preflight.report(m)["clean"]
    tamper_a_cell(su, a.eid)
    assert ("corrupt", str(a.eid)) in kinds_of(verify.verify(db.reader()))                  # all visible: found
    try:   # (2) row-level security without a policy: cse_reader sees no extraction and no run
        q(su, f"alter table {ext} enable row level security; alter table {runs} enable row level security")
        r = db.reader()
        assert q(r, f"select count(*) from {ext}") == [(0,)] and q(su, f"select count(*) from {ext}") == [(3,)]
        r.rollback()
        report = verify.verify(r)
        assert (report["extractions"], report["runs"], report["problems"], report["clean"]) == (0, 0, [], True), \
            report                                                       # clean, having read nothing
        got = preflight.report(m)
        assert got["objects"] == [rls_enabled(ext), rls_enabled(runs)] and not got["clean"], got["objects"]
    finally:
        rollback_quietly(r)
        q(su, f"alter table {ext} disable row level security; alter table {runs} disable row level security")
    assert preflight.report(m)["clean"]
    q(su, "set search_path = public")
    no_table_drift(su)
    full = kinds_of(verify.verify(db.reader()))                         # what verify missed, seen again
    assert ("corrupt", str(a.eid)) in full and ("f5_mismatch", str(b.run_id)) in full
