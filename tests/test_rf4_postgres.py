"""
R-F4 (docs/R_F4_DESIGN.md) against REAL PostgreSQL 17: migration 0018 through P1's runner, the writer, the reader, the
F3 read-back, the F5 rebuild, verify and the preflight, as the restricted roles.

Stage 1 (design section 8.2): F5's `_persist` is not wired to R-F4 yet (a later, separately reviewed stage). These
tests therefore save each document in `_persist`'s accepted order by hand: F3's store, the classification id, the
issuer link, R-F4's store, then F5's store, in one transaction. RT-14 through F5's `run()` and HB-4's slice, and their
rollback/retry paths, belong to that later stage.

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
Synthetic word layers only: NOT real-document validation.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_postgres.py
"""
import contextlib
import copy
import decimal
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rf4_support as R  # noqa: E402
from worker import financial_candidates as f5  # noqa: E402
from worker.financial_candidates_store import PostgresCandidateStore, classification_id  # noqa: E402
from worker.financial_f4_store import codec, preflight, reader, store, verify  # noqa: E402
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
