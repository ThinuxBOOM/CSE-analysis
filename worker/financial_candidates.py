"""
Stage F5: financial fact CANDIDATES from one document's F4 extraction (pure, in memory).

    F1 metadata -> F2 temporary document -> F3 classification -> F4 cells
                -> F5 concept mapping + candidates (THIS MODULE) -> persisted (Design B) -> F6 validation

build() runs inside the F2 consumer call while the temporary document exists; it
reads only F4's in-memory DocumentExtraction and F3's classification. Its result
holds exactly what Design B persists:

    run         one per (filing, SHA-256, F4 build, F3 classification, mapper + vocabulary versions)
    statements  only statements that have candidates
    columns     the period columns of those statements
    rows        only rows whose label mapped (or mapped ambiguously) to a v1 concept
    candidates  one per (mapped row, period column, concept) cell

Never persisted: the PDF, its text, unmapped rows and their cells, non-period
columns, column/row geometry beyond each candidate's own bounding box.

Contracts (F5.0 decision record, invariants I-1..I-14):
- Periods: `period_kind` is instant | duration; `period_class` (3m 6m 9m 12m
  other_Nm unspecified) exists only for durations and comes ONLY from the column's
  own duration - never from the CSE title or manualDate. A candidate's period_kind
  equals its concept's (I-1). `fiscal_label` is set only when F3 documented the
  fiscal year-end AND F3's period is document-evidenced (I-7).
- Values: raw text, F4's parsed Decimal, representation, printed decimals, the
  sign exactly as printed, F4's reported scale and the statement's currency are
  copied. Nothing is negated, a dash stays a nil (no 0), no currency is defaulted
  or converted, and no scaled amount is produced (I-2, I-3).
- Roles: trusted only under a confirmed/document_only F3 period (I-4).
- Audit: `audit_trust = 'trusted'` means ONLY "the label passed the F5 v1
  provenance rule" (known label + confirmed/document_only F3 period). It is not an
  independently proven audit status of the column. `audit_evidence_source`
  records where the label came from; a label that F3's cover-page / auditor-report
  rule could have produced is ALWAYS recorded as 'f3_cover_page_inference', even
  if a header word is also present (never upgraded to column-level evidence) (I-5).
- Scope: canonical 'separate' only beside a group column of the same statement;
  unstated stays unresolved; `bank` is never equated with separate (I-6).
- Ambiguity is explicit: an ambiguous label gives concept NULL + the candidate
  list, never a pick (I-8). Identical input + versions give identical output (I-14).
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

from . import financial_concepts as fc
from . import report_classification as rc
from . import statement_extraction as se
from .financial_values import parse_value

F5_BUILDER_VERSION = "f5.1"
AUDIT_RULE_ID = "f5.audit.v1"
ROLE_TRUST_RULE_ID = "f5.role.v1"
SCOPE_RULE_ID = "f5.scope.v1"
FISCAL_LABEL_RULE_ID = "f5.fiscal_label.v1"

TRUSTED_PERIOD_STATUSES = se.DOCUMENT_PERIOD_STATUSES          # ('confirmed', 'document_only')
DOCUMENT_ROLE_BASES = ("f3.statement_period", "f4.explicit_header_word", "f4.mirrored_f3_role_rule")
PERIOD_CLASSES = ("3m", "6m", "9m", "12m", "unspecified")      # plus other_<N>m
AUDIT_SOURCES = ("column_header_word", "f3_cover_page_inference", "f3_statement_period", "none")
COVER_AUDIT_RULES = {"audit.auditor_report_document": ("audited",),
                     "audit.document_cover_statement": ("unaudited", "provisional")}
CANDIDATE_STATUSES = ("proposed", "ambiguous", "conflicting", "unresolved")
TEXT_LIMIT = 160
PATH_RE = re.compile(r"(?:^|/)(?P<sec>\d+)_(?P<epoch>\d{10,13})\.[A-Za-z0-9]+$")


class CandidateBuildError(ValueError):
    pass


# --- period ---------------------------------------------------------------------------------------

def period_class(period_kind, duration_months):
    """Duration classification from the column's OWN duration. None for instants."""
    if period_kind != "duration":
        return None
    if not duration_months:
        return "unspecified"
    if duration_months in (3, 6, 9, 12):
        return f"{duration_months}m"
    return f"other_{int(duration_months)}m"


def _last_day(y, m):
    return se._month_end(y, m).day


def fiscal_label(classification, period_kind, end_date, duration_months):
    """Q1..Q4 | H1 | H2 | 9M | FY, or None. Only from a DOCUMENTED F3 fiscal year-end under a
    document-evidenced F3 period; the column's own end date and duration do the rest."""
    if period_kind != "duration" or not end_date or duration_months not in (3, 6, 9, 12):
        return None
    if classification.get("fiscal_year_end_basis") != "documented":
        return None
    if classification.get("fiscal_year_end_status") not in TRUSTED_PERIOD_STATUSES:
        return None
    if classification.get("period_status") not in TRUSTED_PERIOD_STATUSES:
        return None
    fye = classification.get("fiscal_year_end") or ""
    m = re.fullmatch(r"(\d{2})-(\d{2})", fye)
    if not m:
        return None
    fm, fd = int(m.group(1)), int(m.group(2))
    end = date.fromisoformat(end_date)
    if not (1 <= fm <= 12) or (fd != _last_day(2001, fm) and not (fm == 2 and fd == 29)):
        return None                                   # FYE not a month end: no quarter arithmetic
    if end.day != _last_day(end.year, end.month):
        return None
    k = (end.month - fm) % 12                          # months after the fiscal year-end month
    if duration_months == 12:
        return "FY" if k == 0 else None
    if duration_months == 3:
        return {3: "Q1", 6: "Q2", 9: "Q3", 0: "Q4"}.get(k)
    if duration_months == 6:
        return {6: "H1", 0: "H2"}.get(k)
    if duration_months == 9:
        return "9M" if k == 9 else None
    return None


# --- trust -----------------------------------------------------------------------------------------

def role_trust(classification, role, role_basis):
    """'trusted' only for a document-evidenced role basis under a confirmed/document_only F3 period."""
    if role in ("current", "comparative") and role_basis in DOCUMENT_ROLE_BASES \
            and classification.get("period_status") in TRUSTED_PERIOD_STATUSES:
        return "trusted"
    return "untrusted"


def _header_audit_word(header_raw):
    """The audit word printed in the column's header stack (F3's own precedence)."""
    t = header_raw or ""
    if rc.PROVISIONAL_RE.search(t):
        return "provisional"
    if rc.UNAUDITED_RE.search(t):
        return "unaudited"
    if rc.AUDITED_RE.search(t):
        return "audited"
    return None


def _cover_labels(classification):
    """Audit labels that F3's DOCUMENT-level cover / auditor-report rule applied in this classification."""
    out = set()
    for e in classification.get("evidence") or []:
        if e.get("decision") == "audit_status" and e.get("rule_id") in COVER_AUDIT_RULES:
            out.update(COVER_AUDIT_RULES[e["rule_id"]])
    return out


def audit_fields(classification, statement_kind, col):
    """(label, evidence_source, trust, rule_id) for one F4 period column.

    Source precedence is weakest-plausible-first so evidence is never upgraded:
      none                     label unknown
      f3_cover_page_inference  F3's cover/auditor-report rule could have produced this label for this
                               column (it applies only to F3 'current' periods whose own label was unknown)
      column_header_word       otherwise, the column's own header stack prints that word
      f3_statement_period      otherwise: F3's statement-period label for the same statement kind + period
    Trust (F5 v1 provenance rule): trusted iff the label is known AND F3's period status is
    confirmed/document_only. 'trusted' is NOT an independently proven audit status of the column."""
    label = col.audit_status or "unknown"
    if label == "unknown":
        return "unknown", "none", "untrusted", AUDIT_RULE_ID
    f3_role, f3_audit, _ = se._match_f3_role(classification, statement_kind, col)
    if label in _cover_labels(classification) and f3_role == "current" and f3_audit == label:
        source = "f3_cover_page_inference"
    elif _header_audit_word(col.header_raw) == label:
        source = "column_header_word"
    else:
        source = "f3_statement_period"
    trust = "trusted" if classification.get("period_status") in TRUSTED_PERIOD_STATUSES else "untrusted"
    return label, source, trust, AUDIT_RULE_ID


# --- scope -----------------------------------------------------------------------------------------

def canonical_scope(reported, statement_has_group_column):
    """(canonical_scope, basis). consolidated <- group; separate <- company/bank ONLY beside a group
    column of the same statement; everything else unresolved (never defaulted)."""
    if reported == "group":
        return "consolidated", "reported_group"
    if reported in ("company", "bank"):
        if statement_has_group_column:
            return "separate", f"reported_{reported}_beside_group_column"
        return "unresolved", f"reported_{reported}_without_group_column"
    return "unresolved", "scope_not_stated"


# --- values ----------------------------------------------------------------------------------------

def sign_as_printed(representation_class, parsed):
    if representation_class == "dash_nil":
        return "nil"
    if parsed is None:
        return "not_a_number"
    if representation_class == "negative_zero":
        return "negative_zero"
    if parsed < 0:
        return "negative"
    if parsed > 0:
        return "positive"
    return "zero"


def _value_fields(cell):
    pv = parse_value(cell.raw_value)
    if pv.parsed != cell.parsed_value or pv.representation_class != cell.representation_class:
        raise CandidateBuildError(f"F4 value for {cell.raw_value!r} does not re-parse identically")
    return {"raw_value": cell.raw_value, "parsed_value": cell.parsed_value,
            "representation_class": cell.representation_class, "printed_decimals": pv.decimals,
            "sign_as_printed": sign_as_printed(cell.representation_class, cell.parsed_value),
            "reported_scale": cell.scale, "scale_basis": cell.scale_basis, "reported_currency": cell.currency}


# --- timestamps ------------------------------------------------------------------------------------

def _ts(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc).isoformat()


def path_epoch(path):
    """(epoch_ms, instant) from the document path's numeric suffix ('369_1762944976777.pdf'), else (None, None)."""
    m = PATH_RE.search(path or "")
    if not m:
        return None, None
    raw = int(m.group("epoch"))
    ms = raw if len(m.group("epoch")) == 13 else raw * 1000
    return ms, datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def path_sec_id(path):
    m = PATH_RE.search(path or "")
    return int(m.group("sec")) if m else None


def timestamp_snapshot(filing, retrieval=None):
    """Every raw availability-related timestamp as it stood at extraction time. No field here is,
    or may be treated as, 'available_at' (that policy is deferred and versioned, F6)."""
    retrieval = retrieval or {}
    lm_raw = retrieval.get("last_modified")
    try:
        lm = parsedate_to_datetime(lm_raw).astimezone(timezone.utc).isoformat() if lm_raw else None
    except (TypeError, ValueError):
        lm = None
    ms, at = path_epoch(filing.get("path"))
    return {"uploaded_at": _ts(filing.get("uploaded_at")), "uploaded_at_raw": filing.get("uploaded_at_raw"),
            "authorized_at": _ts(filing.get("authorized_at")), "authorized_at_raw": filing.get("authorized_at_raw"),
            "path_epoch_ms": ms, "path_epoch_at": at, "cdn_last_modified": lm, "cdn_last_modified_raw": lm_raw,
            "f1_first_seen_at": _ts(filing.get("first_seen_at")),
            "document_retrieved_at": _ts(retrieval.get("retrieved_at"))}


# --- build -----------------------------------------------------------------------------------------

def _clip(s):
    return None if s is None else s[:TEXT_LIMIT]


def _column_row(classification, st, col, has_group):
    kind = col.period_kind
    audit = audit_fields(classification, st.statement_kind, col)
    reported = col.scope or "unstated"
    canon, canon_basis = canonical_scope(col.scope, has_group)
    return {
        "statement_index": st.index, "column_index": col.index, "header_raw": _clip(col.header_raw),
        "column_status": col.status, "period_kind": kind,
        "period_class": period_class(kind, col.duration_months) if kind else None,
        "start_date": col.start_date, "end_date": col.end_date, "duration_months": col.duration_months,
        "duration_label": col.duration_label, "period_evidence_source": col.period_basis,
        "fiscal_label": fiscal_label(classification, kind, col.end_date, col.duration_months),
        "fiscal_label_rule_id": FISCAL_LABEL_RULE_ID,
        "role": col.role, "role_basis": col.role_basis,
        "role_trust": role_trust(classification, col.role, col.role_basis), "role_rule_id": ROLE_TRUST_RULE_ID,
        "reported_scope": reported, "reported_scope_basis": col.scope_basis,
        "canonical_scope": canon, "canonical_scope_basis": canon_basis, "scope_rule_id": SCOPE_RULE_ID,
        "audit_label_reported": audit[0], "audit_evidence_source": audit[1], "audit_trust": audit[2],
        "audit_rule_id": audit[3], "restated": bool(col.restated), "reasons": list(col.reasons),
    }


def _candidate_period(concept, col):
    """(period_kind, period_class, period_derivation) or None when the column cannot carry this concept."""
    if col.period_kind is None:
        return None
    if concept is None:
        return col.period_kind, period_class(col.period_kind, col.duration_months), "column"
    if concept.period_kind == col.period_kind:
        return col.period_kind, period_class(col.period_kind, col.duration_months), "column"
    if concept.period_kind == "instant" and concept.instant_from_duration_column_end and col.end_date:
        return "instant", None, "duration_column_end"
    return None


def _status(cell, mapping, pclass):
    if cell.status == "conflicting":
        return "conflicting"
    if cell.status != "extracted" or pclass == "unspecified":
        return "unresolved"
    if mapping.status == "ambiguous":
        return "ambiguous"
    return "proposed"


def build(ext, classification, *, filing=None, retrieval=None):
    """F5 candidate set for one document. ext: F4 DocumentExtraction (in memory); classification:
    F3 Classification or its to_dict() for the SAME document; filing: F1 report_filings-shaped dict
    (timestamp snapshot); retrieval: F2 RetrievalRecord dict (CDN Last-Modified, retrieved_at)."""
    cls = classification.to_dict() if hasattr(classification, "to_dict") else dict(classification or {})
    if ext.document_sha256 and cls.get("document_sha256") and ext.document_sha256 != cls["document_sha256"]:
        raise CandidateBuildError("F3 classification and F4 extraction are not of the same document")
    template, template_basis = fc.choose_template(ext.statements)
    statements, columns, rows, candidates = [], [], [], []
    counts = {"statements": len(ext.statements), "rows": 0, "value_rows": 0, "mapped_rows": 0, "ambiguous_rows": 0,
              "cells": 0, "period_columns": 0, "skipped_cells_period_unresolved": 0,
              "skipped_cells_period_kind_mismatch": 0, "skipped_cells_non_period_column": 0}
    for st in ext.statements:
        cols = {c.index: c for c in st.columns}
        counts["rows"] += len(st.rows)
        counts["cells"] += len(st.cells)
        mapped = {}
        for r in st.rows:
            if r.kind != "values":
                continue
            counts["value_rows"] += 1
            m = fc.map_label(st.statement_kind, template, r.label_normalized, r.section_label_raw)
            if m.status != "unmapped":
                mapped[r.index] = m
        st_cands = []
        ordinal = {}
        for cell in st.cells:
            m = mapped.get(cell.row_index)
            if m is None:
                continue
            col = cols[cell.column_index]
            if col.column_kind != "period":
                counts["skipped_cells_non_period_column"] += 1
                continue
            # F4 keeps every value of a 'multiple_values_in_column' cell (each unresolved): ordinal in reading order
            key = (cell.row_index, cell.column_index)
            ordinal[key] = ordinal.get(key, -1) + 1
            cell_ordinal = ordinal[key]
            concepts = [fc.BY_KEY[m.concept]] if m.status == "mapped" else [None]
            for concept in concepts:
                per = _candidate_period(concept, col)
                if per is None:
                    counts["skipped_cells_period_unresolved" if col.period_kind is None
                           else "skipped_cells_period_kind_mismatch"] += 1
                    continue
                st_cands.append((cell, cell_ordinal, m, concept, per))
        if not st_cands:
            continue
        period_cols = [c for c in st.columns if c.column_kind == "period"]
        has_group = any(c.scope == "group" for c in period_cols)
        used_rows = sorted({cell.row_index for cell, *_ in st_cands})
        statements.append({
            "statement_index": st.index, "statement_kind": st.statement_kind, "first_page": st.first_page,
            "pages": list(st.pages), "continuation_of": st.continuation_of, "heading_raw": _clip(st.heading_raw),
            "status": st.status, "reasons": list(st.reasons), "reported_scope": st.scope, "scale": st.scale,
            "scale_status": st.scale_status, "scale_basis": st.scale_basis,
            "scale_evidence": [{k: e.get(k) for k in ("zone", "page", "magnitude", "strength")} for e in st.scale_evidence],
            "currency": st.currency})
        for c in period_cols:
            columns.append(_column_row(cls, st, c, has_group))
        counts["period_columns"] += len(period_cols)
        rows_by = {r.index: r for r in st.rows}
        for ri in used_rows:
            r, m = rows_by[ri], mapped[ri]
            ops, ops_basis = fc.operations(r.label_raw, r.section_label_raw)
            rows.append({"statement_index": st.index, "row_index": ri, "page": r.page, "label_raw": r.label_raw,
                         "section_label_raw": r.section_label_raw, "note_ref_raw": r.note_ref_raw, "wrapped": r.wrapped,
                         "line_count": r.line_count, "operations": ops, "operations_basis": ops_basis,
                         "reasons": list(r.reasons)})
            counts["mapped_rows"] += 1
            counts["ambiguous_rows"] += m.status == "ambiguous"
        for cell, cell_ordinal, m, concept, (pkind, pclass, derivation) in st_cands:
            candidates.append({
                "statement_index": st.index, "row_index": cell.row_index, "column_index": cell.column_index,
                "value_ordinal": cell_ordinal,
                "concept_key": concept.key if concept else None, "mapping_status": m.status,
                "mapping_rule_ids": list(m.rule_ids), "ambiguous_concepts": list(m.candidates) if m.status == "ambiguous" else [],
                "period_kind": pkind, "period_class": pclass, "period_derivation": derivation,
                "value_type": concept.value_type if concept else None,
                "attribution": concept.attribution if concept else "not_applicable",
                **_value_fields(cell),
                "f4_status": cell.status, "f4_reasons": list(cell.reasons), "f4_confidence": cell.confidence,
                "f4_quality_flags": list(cell.quality_flags), "cross_check": cell.cross_check,
                "page": cell.coordinates["page"],
                "bbox": [cell.coordinates[k] for k in ("x0", "y0", "x1", "y1")],
                "candidate_status": _status(cell, m, pclass),
            })
    candidates.sort(key=lambda c: (c["statement_index"], c["row_index"], c["column_index"], c["value_ordinal"],
                                   c["concept_key"] or ""))
    counts["statements_with_candidates"] = len(statements)
    counts["candidates"] = len(candidates)
    run = {
        "cse_filing_id": ext.filing_id if ext.filing_id is not None else cls.get("cse_filing_id"),
        "document_sha256": ext.document_sha256 or cls.get("document_sha256"),
        "word_extractor": ext.extractor, "f4_extractor_version": ext.extractor_version,
        "classifier_version": cls.get("classifier_version"), "text_extractor": cls.get("text_extractor"),
        "builder_version": F5_BUILDER_VERSION, "mapper_version": fc.MAPPER_VERSION,
        "vocabulary_version": fc.VOCABULARY_VERSION, "template": template, "template_basis": template_basis,
        "document_status": ext.document_status, "status_reasons": list(ext.status_reasons),
        "withheld_pages": sorted(p["page"] for p in ext.page_trust if p.get("status") != "text_native"),
        "f3_period_status": cls.get("period_status"), "counts": counts,
        "timestamps": timestamp_snapshot(filing or {}, retrieval) if filing is not None else None,
    }
    return {"run": run, "statements": statements, "columns": columns, "rows": rows, "candidates": candidates}


def attach_timestamps(result, filing, retrieval=None):
    """Adds the timestamp snapshot once F2 has returned the retrieval record (after the consumer call)."""
    result["run"]["timestamps"] = timestamp_snapshot(filing or {}, retrieval)
    return result


def canonical_json(result):
    """Byte-stable serialisation (determinism checks, content hash)."""
    def enc(o):
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError(type(o))
    return json.dumps(result, sort_keys=True, separators=(",", ":"), default=enc)


def content_sha256(result):
    """Hash of everything except the timestamp snapshot: equal for identical input + versions."""
    body = {k: v for k, v in result.items() if k != "run"}
    body["run"] = {k: v for k, v in result["run"].items() if k != "timestamps"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()
