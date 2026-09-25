"""
Stage F3 persistence (migration 0005): classification + statement periods +
compact evidence. Row building is pure (classification_rows) and unit-tested;
PostgresClassificationStore is imported only for real database runs.

Idempotency: the parent row is unique on (cse_filing_id, document_sha256,
classifier_version, text_extractor). Saving an identical classification again
inserts nothing (ON CONFLICT DO NOTHING, children skipped). Each save runs in
its own SAVEPOINT so one bad filing cannot poison a batch.
"""
CLASSIFICATION_COLUMNS = (
    "cse_filing_id", "document_sha256", "document_bytes", "classifier_version", "text_extractor",
    "classification_status", "status_reasons", "page_count", "text_page_count", "document_type",
    "document_type_status", "underlying_type", "underlying_type_status", "period_kind", "period_start",
    "period_start_basis", "period_end", "duration_months", "duration_label", "period_status", "fiscal_year_end",
    "fiscal_year_end_status", "fiscal_period", "fiscal_period_status", "fiscal_period_reason", "metadata_conflicts",
)
PERIOD_COLUMNS = ("statement_kind", "first_page", "scopes", "period_kind", "start_date", "end_date",
                  "duration_months", "duration_label", "role", "audit_status", "restated", "evidence_ordinals")
EVIDENCE_COLUMNS = ("ordinal", "decision", "source", "source_field", "rule_id", "evidence_kind", "page",
                    "snippet", "outcome")


def classification_rows(result: dict, document_bytes=None):
    """(classification_row, [period_rows], [evidence_rows]) from Classification.to_dict().
    Pure; the column names are exactly migration 0005's."""
    if not result.get("document_sha256") or result.get("cse_filing_id") is None:
        raise ValueError("classification lacks cse_filing_id or document_sha256")
    row = {c: result.get(c) for c in CLASSIFICATION_COLUMNS if c != "document_bytes"}
    row["document_bytes"] = document_bytes
    periods = []
    for sp in result.get("statement_periods", []):
        p = {c: sp.get(c) for c in PERIOD_COLUMNS if c != "evidence_ordinals"}
        p["evidence_ordinals"] = list(sp.get("evidence") or [])
        periods.append(p)
    evidence = [{c: e.get(c) for c in EVIDENCE_COLUMNS} for e in result.get("evidence", [])]
    return row, periods, evidence


class PostgresClassificationStore:
    def __init__(self, conn):
        self.conn = conn

    def save(self, result: dict, document_bytes=None) -> str:
        """Returns 'inserted' or 'already_present'. Raises on database errors
        after rolling back to this filing's savepoint."""
        row, periods, evidence = classification_rows(result, document_bytes)
        with self.conn.cursor() as cur:
            cur.execute("savepoint f3_save")
            try:
                cols = ", ".join(CLASSIFICATION_COLUMNS)
                cur.execute(
                    f"insert into report_document_classifications ({cols}) values "
                    f"({', '.join('%(' + c + ')s' for c in CLASSIFICATION_COLUMNS)}) "
                    "on conflict (cse_filing_id, document_sha256, classifier_version, text_extractor) do nothing "
                    "returning id", row)
                got = cur.fetchone()
                if got is None:
                    cur.execute("release savepoint f3_save")
                    return "already_present"
                cid = got[0]
                for p in periods:
                    cur.execute(
                        f"insert into report_statement_periods (classification_id, {', '.join(PERIOD_COLUMNS)}) values "
                        f"(%(cid)s, {', '.join('%(' + c + ')s' for c in PERIOD_COLUMNS)})", {**p, "cid": cid})
                for e in evidence:
                    cur.execute(
                        f"insert into report_classification_evidence (classification_id, {', '.join(EVIDENCE_COLUMNS)}) "
                        f"values (%(cid)s, {', '.join('%(' + c + ')s' for c in EVIDENCE_COLUMNS)})", {**e, "cid": cid})
                cur.execute("release savepoint f3_save")
                return "inserted"
            except Exception:
                cur.execute("rollback to savepoint f3_save")
                raise

    def commit(self):
        self.conn.commit()
