"""
Database rows -> F6.3 inputs (docs/F6.4_DESIGN.md section 17.3; the rendering rules are part of f6.store.1).

L1  the F5 result dict is rebuilt from the 0008 rows in exactly the JSON-form shape that F6.1's
    contexts_from_f5_result and F6.3's candidate_attributes read (statement / row / column indices resolved through
    the foreign keys);
L2  every timestamptz of the F5 raw timestamp snapshot is rendered as value.astimezone(UTC).isoformat(), exactly as
    F5's own timestamp_snapshot (_ts) renders it; dates stay date objects; the session uses P1's fixed settings;
L3  numeric -> Decimal (never float), integers -> int, arrays -> lists;
L4  a uuid is rendered as its canonical lower-case text;
L5  F5RunRef always carries recorded_at, classification_id and content_sha256 from the run row;
L6  IssuerLinkDecision comes from its filing_issuer_links row, DocumentContext from the classification row (with its
    id), and the publication input is report_filings.uploaded_at (the stored value on reproduction);
L7  candidate ids are always present.

Changing any rule here would change F6.3 input hashes for unchanged inputs; the natural-key constraint then refuses
loudly (section 17.1), so a rule change is a design decision, never a silent code change.
"""
from datetime import datetime, timezone

from ..financial_truth import inputs
from ..ops import dbhash

TIMESTAMP_FIELDS = ("uploaded_at", "uploaded_at_raw", "authorized_at", "authorized_at_raw", "path_epoch_ms",
                    "path_epoch_at", "cdn_last_modified", "cdn_last_modified_raw", "f1_first_seen_at",
                    "document_retrieved_at")
RUN_FIELDS = ("cse_filing_id", "document_sha256", "word_extractor", "f4_extractor_version", "classifier_version",
              "text_extractor", "builder_version", "mapper_version", "vocabulary_version")


class LoaderError(ValueError):
    """A referenced row is missing: the job refuses rather than guesses."""


def session(cur):
    """P1's fixed output settings (timezone UTC, ISO dates, ...), as the restore check uses."""
    dbhash.apply_session_settings(cur)


def _ts(value):
    """L2: F5's _ts rule, exactly."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise LoaderError("a naive timestamp reached the loader")
        return value.astimezone(timezone.utc).isoformat()
    return value


def _rows(cur, sql, args):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def f5_result(cur, run_id):
    """(result dict, run row) of one persisted F5 run (L1-L3, L7)."""
    runs = _rows(cur, "select * from financial_extraction_runs where id = %s", (run_id,))
    if not runs:
        raise LoaderError(f"F5 run {run_id} does not exist")
    run = runs[0]
    statements = _rows(cur, "select statement_index, statement_kind, first_page, pages, continuation_of, heading_raw, "
                            "status, reasons, reported_scope, scale, scale_status, scale_basis, scale_evidence, "
                            "currency from financial_statement_extracts where run_id = %s order by statement_index",
                       (run_id,))
    columns = _rows(cur, "select x.statement_index, c.column_index, c.header_raw, c.column_status, c.period_kind, "
                         "c.period_class, c.start_date, c.end_date, c.duration_months, c.duration_label, "
                         "c.period_evidence_source, c.fiscal_label, c.fiscal_label_rule_id, c.role, c.role_basis, "
                         "c.role_trust, c.role_rule_id, c.reported_scope, c.reported_scope_basis, c.canonical_scope, "
                         "c.canonical_scope_basis, c.scope_rule_id, c.audit_label_reported, c.audit_evidence_source, "
                         "c.audit_trust, c.audit_rule_id, c.restated, c.reasons "
                         "from financial_statement_columns c join financial_statement_extracts x on x.id = c.statement_id "
                         "where x.run_id = %s order by x.statement_index, c.column_index", (run_id,))
    rows = _rows(cur, "select x.statement_index, r.row_index, r.page, r.label_raw, r.section_label_raw, r.note_ref_raw, "
                      "r.wrapped, r.line_count, r.operations, r.operations_basis, r.reasons "
                      "from financial_statement_rows r join financial_statement_extracts x on x.id = r.statement_id "
                      "where x.run_id = %s order by x.statement_index, r.row_index", (run_id,))
    candidates = _rows(cur, "select fc.id, x.statement_index, r.row_index, c.column_index, fc.value_ordinal, "
                            "fc.concept_key, fc.mapping_status, fc.mapping_rule_ids, fc.ambiguous_concepts, "
                            "fc.period_kind, fc.period_class, fc.period_derivation, fc.value_type, fc.attribution, "
                            "fc.raw_value, fc.parsed_value, fc.representation_class, fc.printed_decimals, "
                            "fc.sign_as_printed, fc.reported_scale, fc.scale_basis, fc.reported_currency, fc.f4_status, "
                            "fc.f4_reasons, fc.f4_confidence, fc.f4_quality_flags, fc.cross_check, fc.page, "
                            "fc.candidate_status "
                            "from financial_fact_candidates fc "
                            "join financial_statement_rows r on r.id = fc.row_id "
                            "join financial_statement_columns c on c.id = fc.column_id "
                            "join financial_statement_extracts x on x.id = r.statement_id "
                            "where fc.run_id = %s order by fc.id", (run_id,))
    for c in candidates:
        if c["id"] is None:
            raise LoaderError("a candidate without an id cannot be persisted (L7)")
    result = {"run": dict({k: run[k] for k in RUN_FIELDS},
                          timestamps={k: _ts(run[k]) for k in TIMESTAMP_FIELDS}),
              "statements": statements, "columns": columns, "rows": rows, "candidates": candidates}
    return result, run


def f5_run_ref(cur, run_id, result=None, run=None):
    """L5: the F5RunRef of a persisted run (recorded_at, classification_id and content_sha256 always carried)."""
    if result is None or run is None:
        result, run = f5_result(cur, run_id)
    return inputs.f5_run_ref(result, f5_run_id=str(run["id"]), recorded_at=run["recorded_at"],
                             classification_id=str(run["classification_id"]), content_sha256=run["content_sha256"])


def all_run_refs(cur, run_ids=None):
    """F5RunRefs of every persisted F5 run (or of `run_ids`), without loading candidates (selection needs only the run
    keys, versions, recorded_at and the timestamp snapshot)."""
    if run_ids is None:
        runs = _rows(cur, "select * from financial_extraction_runs order by id", ())
    else:
        runs = _rows(cur, "select * from financial_extraction_runs where id = any(%s::uuid[]) order by id",
                     ([str(r) for r in run_ids],))
    out = []
    for run in runs:
        result = {"run": dict({k: run[k] for k in RUN_FIELDS}, timestamps={k: _ts(run[k]) for k in TIMESTAMP_FIELDS})}
        out.append(inputs.f5_run_ref(result, f5_run_id=str(run["id"]), recorded_at=run["recorded_at"],
                                     classification_id=str(run["classification_id"]),
                                     content_sha256=run["content_sha256"]))
    return out


def issuer_link(cur, link_id):
    """L6: IssuerLinkDecision from its filing_issuer_links row (None when link_id is None)."""
    if link_id is None:
        return None
    rows = _rows(cur, "select id, cse_filing_id, status, basis, issuer_id, decided_at from filing_issuer_links "
                      "where id = %s", (link_id,))
    if not rows:
        raise LoaderError(f"issuer decision {link_id} does not exist")
    r = rows[0]
    return inputs.IssuerLinkDecision(r["cse_filing_id"], r["status"], r["basis"],
                                     str(r["issuer_id"]) if r["issuer_id"] is not None else None,
                                     link_id=r["id"], decided_at=r["decided_at"])


def current_issuer_link_id(cur, cse_filing_id):
    """D-1 / M4: the latest filing_issuer_links decision of the filing (highest id), or None."""
    cur.execute("select max(id) from filing_issuer_links where cse_filing_id = %s", (cse_filing_id,))
    return cur.fetchone()[0]


def current_uploaded_at(cur, cse_filing_id):
    """D-7 / M4: report_filings.uploaded_at as currently committed (report_filings is mutable)."""
    cur.execute("select uploaded_at from report_filings where cse_filing_id = %s", (cse_filing_id,))
    row = cur.fetchone()
    if row is None:
        raise LoaderError(f"filing {cse_filing_id} does not exist")
    return row[0]


def document(cur, classification_id):
    """L6: DocumentContext from the classification row, with its id."""
    rows = _rows(cur, "select * from report_document_classifications where id = %s", (classification_id,))
    if not rows:
        raise LoaderError(f"classification {classification_id} does not exist")
    return inputs.DocumentContext.from_classification(rows[0], classification_id=str(rows[0]["id"]))
