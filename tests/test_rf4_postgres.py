"""
R-F4 (docs/R_F4_DESIGN.md) against REAL PostgreSQL 17: migration 0018 through P1's runner, the writer, the reader, the
F3 read-back, the F5 rebuild, verify and the preflight, as the restricted roles.

Stage 1 (design section 8.2): F5's `_persist` is not wired to R-F4 yet (a later, separately reviewed stage). These
tests therefore save each document in `_persist`'s accepted order by hand: F3's store, the classification id, the
issuer link, R-F4's store, then F5's store, in one transaction. RT-14 through F5's `run()` and HB-4's slice, and their
rollback/retry paths, belong to that later stage.

    p1   0018 applied at its pinned hash; the preflight passes as cse_worker and as cse_reader
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
Synthetic word layers only: NOT real-document validation.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_postgres.py
"""
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
    assert preflight.problems(db.conn()) == []
    assert preflight.problems(db.reader(), role="cse_reader") == []
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
        problems = preflight.database_problems(w)
        assert problems and all("missing" in p for p in problems)
        assert mig.apply(m, mig.discover(S.MIGDIR), log=lambda _: None)["applied"] == [preflight.MIGRATION]
        assert preflight.problems(w) == [] and preflight.problems(d.reader(), role="cse_reader") == []
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
