"""
R-F4's reader (docs/R_F4_DESIGN.md section 5): the saved F4 extraction, the F3 classification read back from
migration 0005, and F5 rebuilt from them, from the database only (no PDF). Read-only. A problem is a typed
EvidenceRefused, never repaired, never partially used, never answered with a download.

F5 is rebuilt by its own frozen builder, unchanged, under the declared reconstruction context (RF4-M3): Python's
default decimal context, the one the production pipeline extracts under. F5 outputs the stored parsed_value as it is;
its own consistency check re-parses raw_value, and when that check refuses (an extraction made under another context,
on a negative longer than the precision) the rebuild is `f5_rebuild_refused`, never a different result.
"""
import decimal

from .. import financial_candidates as f5
from .. import statement_extraction as se
from ..report_classification_store import CLASSIFICATION_COLUMNS, EVIDENCE_COLUMNS, PERIOD_COLUMNS
from . import STORE_VERSION, codec
from .errors import EvidenceRefused

SUPPORTED = {(STORE_VERSION, se.F4_EXTRACTOR_VERSION)}        # (store_version, f4_extractor_version) this code reads
RECONSTRUCTION_CONTEXT = decimal.Context(
    prec=28, rounding=decimal.ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0, flags=[],
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow])
RUN_KEY = ("cse_filing_id", "document_sha256", "word_extractor", "f4_extractor_version", "classification_id")


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ================================================================================================ the extraction

def extraction_row(conn, extraction_id):
    with conn.cursor() as cur:
        got = _rows(cur, "select * from financial_f4_extractions where id = %s", (str(extraction_id),))
    if not got:
        raise EvidenceRefused("missing", f"no F4 extraction {extraction_id}")
    return got[0]


def extraction_id_for(conn, key):
    """The extraction of an F5 run's key {cse_filing_id, document_sha256, word_extractor, f4_extractor_version,
    classification_id}, or None."""
    with conn.cursor() as cur:
        cur.execute("select id from financial_f4_extractions where cse_filing_id = %s and document_sha256 = %s and "
                    "word_extractor = %s and f4_extractor_version = %s and classification_id = %s",
                    tuple(str(key[k]) if k == "classification_id" else key[k] for k in RUN_KEY))
        got = cur.fetchone()
    return got and str(got[0])


def load_extraction(conn, extraction_id):
    """The DocumentExtraction, exactly as F4 produced it: the version, the child counts and the content hash are
    checked (version_incompatible, incomplete, corrupt)."""
    e = extraction_row(conn, extraction_id)
    if (e["store_version"], e["f4_extractor_version"]) not in SUPPORTED:
        raise EvidenceRefused("version_incompatible",
                              f"extraction {extraction_id}: store {e['store_version']} / F4 {e['f4_extractor_version']}"
                              f" is not readable by this code ({sorted(SUPPORTED)})")
    eid = str(extraction_id)
    with conn.cursor() as cur:
        statements = _rows(cur, "select * from financial_f4_statements where extraction_id = %s "
                                "order by statement_index", (eid,))
        children = {}
        for table, name in (("financial_f4_columns", "columns"), ("financial_f4_rows", "rows"),
                            ("financial_f4_cells", "cells")):
            children[name] = _rows(cur, f"select s.statement_index, k.* from {table} k join financial_f4_statements s "
                                        "on s.id = k.statement_id where s.extraction_id = %s "
                                        "order by s.statement_index, k.seq", (eid,))
    counts = {"statement_count": len(statements), "column_count": len(children["columns"]),
              "row_count": len(children["rows"]), "cell_count": len(children["cells"])}
    if any(counts[k] != e[k] for k in counts):
        raise EvidenceRefused("incomplete", f"extraction {eid}: child rows {counts} differ from its counts "
                                            f"{ {k: e[k] for k in counts} }")
    ext = codec.decode_extraction({"extraction": e, "statements": statements, **children})
    if codec.f4_content_sha256(ext) != e["f4_content_sha256"]:
        raise EvidenceRefused("corrupt", f"extraction {eid}: the content does not hash to f4_content_sha256")
    return ext


# ================================================================================================ F3 read-back

def load_classification(conn, classification_id):
    """The F3 classification from migration 0005's three tables, in codec.f3_projection's shape (dates as ISO
    strings): every F3 field F5's builder reads. 0005 has no frozen loader; this one reads it and changes nothing."""
    cid = str(classification_id)
    with conn.cursor() as cur:
        got = _rows(cur, f"select {', '.join(CLASSIFICATION_COLUMNS)} from report_document_classifications "
                         "where id = %s", (cid,))
        if not got:
            raise EvidenceRefused("missing", f"no F3 classification {cid}")
        out = {k: _iso(v) for k, v in got[0].items() if k != "document_bytes"}
        cols = [c for c in PERIOD_COLUMNS if c != "evidence_ordinals"]
        periods = _rows(cur, f"select {', '.join(cols)}, evidence_ordinals from report_statement_periods "
                             "where classification_id = %s order by id", (cid,))
        out["statement_periods"] = [{**{c: _iso(p[c]) for c in cols}, "evidence": list(p["evidence_ordinals"])}
                                    for p in periods]
        out["evidence"] = _rows(cur, f"select {', '.join(EVIDENCE_COLUMNS)} from report_classification_evidence "
                                     "where classification_id = %s order by ordinal", (cid,))
    for k in ("status_reasons", "metadata_conflicts"):
        if out.get(k) is not None:
            out[k] = list(out[k])
    for p in out["statement_periods"]:
        p["scopes"] = list(p["scopes"])
    return out


# ================================================================================================ F5 rebuild

def run_key(conn, run_id):
    with conn.cursor() as cur:
        got = _rows(cur, f"select {', '.join(RUN_KEY)}, content_sha256 from financial_extraction_runs where id = %s",
                    (str(run_id),))
    if not got:
        raise EvidenceRefused("missing", f"no F5 run {run_id}")
    return got[0]


def evidence_for_run(conn, run_id):
    """(extraction id, the F4 extraction, the F3 classification, the run row) of an F5 run, every check applied."""
    run = run_key(conn, run_id)
    eid = extraction_id_for(conn, run)
    if eid is None:
        raise EvidenceRefused("missing", f"F5 run {run_id} has no saved F4 extraction")
    ext = load_extraction(conn, eid)
    cls = load_classification(conn, run["classification_id"])
    if codec.f3_input_sha256(cls) != extraction_row(conn, eid)["f3_input_sha256"]:
        raise EvidenceRefused("corrupt", f"extraction {eid}: the F3 classification read back differs from the input "
                                         f"F5 consumed (f3_input_sha256)")
    return eid, ext, cls, run


def rebuild_f5(conn, run_id):
    """(the F5 result rebuilt from saved F3 and F4 evidence, the run's stored content_sha256). F5's own builder,
    under RECONSTRUCTION_CONTEXT; its refusal is f5_rebuild_refused."""
    _, ext, cls, run = evidence_for_run(conn, run_id)
    try:
        with decimal.localcontext(RECONSTRUCTION_CONTEXT):
            result = f5.build(ext, cls)
    except f5.CandidateBuildError as exc:
        raise EvidenceRefused("f5_rebuild_refused", f"F5 run {run_id}: {exc}") from None
    return result, run["content_sha256"]
