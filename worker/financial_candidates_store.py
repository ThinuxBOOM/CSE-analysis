"""
Stage F5 persistence (migration 0008, Design B): run -> statements -> period columns
-> mapped rows -> candidates, from the pure financial_candidates.build() result.

Idempotency: the run is unique on (filing, SHA-256, word extractor, F4 version,
F3 classification, builder/mapper/vocabulary versions). Saving the same result
again inserts nothing ('already_present'); a new mapper or vocabulary version
inserts a new run. Rows are never updated (the tables reject UPDATE/DELETE).
Each save runs in its own SAVEPOINT so one bad filing cannot poison a batch.
"""
import psycopg2.extras

from . import financial_candidates as f5

RUN_KEY_COLUMNS = ("cse_filing_id", "document_sha256", "word_extractor", "f4_extractor_version", "classifier_version",
                   "text_extractor", "builder_version", "mapper_version", "vocabulary_version", "template",
                   "template_basis", "document_status", "status_reasons", "withheld_pages", "f3_period_status")
TIMESTAMP_COLUMNS = ("uploaded_at", "uploaded_at_raw", "authorized_at", "authorized_at_raw", "path_epoch_ms",
                     "path_epoch_at", "cdn_last_modified", "cdn_last_modified_raw", "f1_first_seen_at",
                     "document_retrieved_at")
STATEMENT_COLUMNS = ("statement_index", "statement_kind", "first_page", "pages", "continuation_of", "heading_raw",
                     "status", "reasons", "reported_scope", "scale", "scale_status", "scale_basis", "scale_evidence",
                     "currency")
COLUMN_COLUMNS = ("column_index", "header_raw", "column_status", "period_kind", "period_class", "start_date", "end_date",
                  "duration_months", "duration_label", "period_evidence_source", "fiscal_label", "fiscal_label_rule_id",
                  "role", "role_basis", "role_trust", "role_rule_id", "reported_scope", "reported_scope_basis",
                  "canonical_scope", "canonical_scope_basis", "scope_rule_id", "audit_label_reported",
                  "audit_evidence_source", "audit_trust", "audit_rule_id", "restated", "reasons")
ROW_COLUMNS = ("row_index", "page", "label_raw", "section_label_raw", "note_ref_raw", "wrapped", "line_count",
               "operations", "operations_basis", "reasons")
CANDIDATE_COLUMNS = ("value_ordinal", "concept_key", "mapping_status", "mapping_rule_ids", "ambiguous_concepts",
                     "period_kind", "period_class", "period_derivation", "value_type", "attribution", "raw_value",
                     "parsed_value", "representation_class", "printed_decimals", "sign_as_printed", "reported_scale",
                     "scale_basis", "reported_currency", "f4_status", "f4_reasons", "f4_confidence", "f4_quality_flags",
                     "cross_check", "page", "bbox", "candidate_status")


def run_row(result, classification_id, issuer_link=None):
    """The financial_extraction_runs row (pure)."""
    run = result["run"]
    if run.get("timestamps") is None:
        raise ValueError("the timestamp snapshot is missing (attach_timestamps after the F2 retrieval)")
    row = {c: run.get(c) for c in RUN_KEY_COLUMNS}
    row.update({c: run["timestamps"].get(c) for c in TIMESTAMP_COLUMNS})
    row["classification_id"] = classification_id
    row["counts"] = psycopg2.extras.Json(run["counts"])
    row["content_sha256"] = f5.content_sha256(result)
    link = issuer_link or {}
    row["filing_issuer_link_id"] = link.get("id")
    row["issuer_id"] = link.get("issuer_id")
    row["issuer_link_status"] = link.get("status")
    return row


class PostgresCandidateStore:
    def __init__(self, conn):
        self.conn = conn

    def save(self, result, classification_id, issuer_link=None):
        """Returns ('inserted' | 'already_present', run_id)."""
        row = run_row(result, classification_id, issuer_link)
        cols = tuple(row)
        with self.conn.cursor() as cur:
            cur.execute("savepoint f5_save")
            try:
                cur.execute(f"insert into financial_extraction_runs ({', '.join(cols)}) values "
                            f"({', '.join('%(' + c + ')s' for c in cols)}) "
                            "on conflict on constraint uq_financial_extraction_run do nothing returning id", row)
                got = cur.fetchone()
                if got is None:
                    cur.execute("select id from financial_extraction_runs where cse_filing_id = %(cse_filing_id)s "
                                "and document_sha256 = %(document_sha256)s and word_extractor = %(word_extractor)s "
                                "and f4_extractor_version = %(f4_extractor_version)s and classification_id = %(classification_id)s "
                                "and builder_version = %(builder_version)s and mapper_version = %(mapper_version)s "
                                "and vocabulary_version = %(vocabulary_version)s", row)
                    run_id = cur.fetchone()[0]
                    cur.execute("release savepoint f5_save")
                    return "already_present", str(run_id)
                run_id = got[0]
                stmt_ids = {}
                for s in result["statements"]:
                    vals = {**s, "scale_evidence": psycopg2.extras.Json(s["scale_evidence"]), "run_id": run_id}
                    cur.execute(f"insert into financial_statement_extracts (run_id, {', '.join(STATEMENT_COLUMNS)}) values "
                                f"(%(run_id)s, {', '.join('%(' + c + ')s' for c in STATEMENT_COLUMNS)}) returning id", vals)
                    stmt_ids[s["statement_index"]] = cur.fetchone()[0]
                col_ids = self._bulk(cur, "financial_statement_columns", COLUMN_COLUMNS, result["columns"], stmt_ids,
                                     "column_index")
                row_ids = self._bulk(cur, "financial_statement_rows", ROW_COLUMNS, result["rows"], stmt_ids, "row_index")
                values = [(run_id, row_ids[(c["statement_index"], c["row_index"])],
                           col_ids[(c["statement_index"], c["column_index"])], *[c[k] for k in CANDIDATE_COLUMNS])
                          for c in result["candidates"]]
                if values:
                    psycopg2.extras.execute_values(
                        cur, f"insert into financial_fact_candidates (run_id, row_id, column_id, {', '.join(CANDIDATE_COLUMNS)}) "
                             "values %s", values, page_size=500)
                cur.execute("release savepoint f5_save")
                return "inserted", str(run_id)
            except Exception:
                cur.execute("rollback to savepoint f5_save")
                raise

    @staticmethod
    def _bulk(cur, table, columns, items, stmt_ids, index_col):
        values = [(stmt_ids[i["statement_index"]], *[i[k] for k in columns]) for i in items]
        if not values:
            return {}
        back = {v: k for k, v in stmt_ids.items()}
        got = psycopg2.extras.execute_values(
            cur, f"insert into {table} (statement_id, {', '.join(columns)}) values %s returning id, statement_id, {index_col}",
            values, page_size=500, fetch=True)
        return {(back[sid], idx): rid for rid, sid, idx in got}

    def commit(self):
        self.conn.commit()


def classification_id(conn, classification):
    """The id of the persisted F3 classification row for this exact classification (0005 determinism key)."""
    with conn.cursor() as cur:
        cur.execute("select id from report_document_classifications where cse_filing_id = %s and document_sha256 = %s "
                    "and classifier_version = %s and text_extractor = %s",
                    (classification["cse_filing_id"], classification["document_sha256"],
                     classification["classifier_version"], classification["text_extractor"]))
        got = cur.fetchone()
        return str(got[0]) if got else None
