"""
Idempotent, atomic inserts (docs/F6.4_DESIGN.md sections 11.2, 18). Every function runs inside the caller's
transaction; nothing here commits. The caller rolls back on any exception, so a partial parent is never committed
(and the database's deferred checks would refuse one anyway).

Conflicts:
- a validation run's content key already stored -> the stored hashes must equal the recomputed ones: 'already_present'
  (the caller rolls back); different hashes, or the same natural key with another content key -> NondeterminismError.
  A natural-key violation raised by a CONCURRENT insert of the same run (ON CONFLICT arbitrates the content key only)
  is resolved the same way: the committed row decides;
- the insert-if-absent registries (T4 facts, T8 configurations) conflict as a matter of course: the stored row is
  kept, checked against the recomputed one, and the job continues.
"""
import psycopg2
import psycopg2.errors
import psycopg2.extras


class NondeterminismError(RuntimeError):
    """The same key or natural key with different content: never overwritten, never forked silently."""


T1_COLS = ("validation_run_key", "f5_run_id", "cse_filing_id", "document_sha256", "classification_id",
           "issuer_link_id", "publication_source_field", "publication_uploaded_at", "publication_date",
           "validation_version", "input_policy_version", "op1_version", "admission_version", "identity_version",
           "input_hash", "output_hash", "output_json", "candidates_total", "admitted_numeric", "admitted_nil",
           "not_admitted", "so_count", "op1_count", "store_version", "code_revision", "job_id", "started_at",
           "finished_at")
T2_COLS = ("candidate_validation_key", "validation_run_key", "candidate_ordinal", "candidate_id", "statement_index",
           "row_index", "column_index", "value_ordinal", "concept_key", "eligibility", "ineligible_reasons",
           "normalization_reasons", "admitted", "value_kind", "nil_form", "admission_reasons", "lifted_reasons",
           "operations_route", "op1_key", "ef_key", "input_hash", "output_hash", "output_json")
T3_COLS = ("validation_run_key", "op1_key", "op1_ordinal", "op1_json", "op1_version", "statement_root",
           "statement_kind", "period_kind", "period_end", "duration_months", "reported_scope", "role", "concept_key",
           "outcome", "reasons", "currency", "value_type", "computed", "total", "tolerance", "difference",
           "validated_candidate_ids")
T4_COLS = ("ef_key", "identity_version", "issuer_id", "concept_key", "period_kind", "period_end", "duration_months",
           "scope", "operations", "maturity", "currency", "first_validation_run_key")
IDENTITY_COLS = T4_COLS[1:11]
REPORTED_COLS = ("reported_raw_value", "reported_parsed_value", "reported_representation_class",
                 "reported_printed_decimals", "reported_sign_as_printed", "reported_scale", "reported_scale_basis",
                 "reported_currency", "reported_value_type")
T5_COLS = ("so_key", "ef_key", "validation_run_key", "f5_run_id", "cse_filing_id", "document_sha256",
           "observation_status", "value_kind", "nil_forms", "member_count", "representative_candidate_validation_key",
           *REPORTED_COLS, "normalized_value", "half_unit", "precision", "interval_low", "interval_high", "roles",
           "annotations", "output_hash", "so_json")
T6_COLS = ("so_key", "candidate_validation_key", "candidate_id", "member_ordinal", "member_json", "value_kind",
           "normalized_value", "half_unit", "currency", "value_type", "role", "period_derivation", "operations_route",
           "maturity_basis", "audit_label_reported", "restated")
CMP_VALUES = ("outcome", "reason", "a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance", "abs_difference",
              "sign_only")
T7_COLS = ("so_key", "comparison_ordinal", "a_candidate_validation_key", "a_member_ordinal",
           "b_candidate_validation_key", "b_member_ordinal", "comparison_json", *CMP_VALUES)
T8_COLS = ("configuration_id", "reconciliation_version", "validation_version", "input_policy_version", "op1_version",
           "admission_version", "identity_version", "configuration_json")
T12_COLS = ("batch_id", "job_id", "configuration_id", "issuer_id", "sequence", "previous_batch_id",
            "partition_input_hash", "output_hash", "output_json", "results_count", "records_appended")
T13_COLS = ("ef_key", "configuration_id", "sequence", "previous_record_id", "batch_id", "reconciliation_version",
            "input_hash", "output_hash", "state", "value_kind", "interval_low", "interval_high",
            "representative_so_key", "representative_candidate_validation_key", *REPORTED_COLS,
            "representative_normalized_value", "representative_half_unit", "document_count", "so_count",
            "comparison_count", "annotations", "reasons", "result_json")
T14_COLS = ("record_id", "observation_ordinal", "so_key", "observation_json", "document_sha256", "so_output_hash",
            "role_in_outcome")
T15_COLS = ("record_id", "comparison_ordinal", "a_so_key", "a_observation_ordinal", "a_candidate_validation_key",
            "a_member_ordinal", "b_so_key", "b_observation_ordinal", "b_candidate_validation_key", "b_member_ordinal",
            "comparison_json", *CMP_VALUES)
T16_COLS = ("batch_id", "result_ordinal", "ef_key", "record_id", "appended")


def _insert(cur, table, cols, rows, suffix=""):
    if not rows:
        return []
    values = [tuple(r[c] for c in cols) for r in rows]
    return psycopg2.extras.execute_values(cur, f"insert into {table} ({', '.join(cols)}) values %s {suffix}", values,
                                          page_size=500, fetch=bool(suffix and "returning" in suffix))


def _stored_equal(cur, t1):
    """The committed validation run under t1's content key has t1's hashes (None stored: not equal)."""
    cur.execute("select input_hash, output_hash from financial_validation_runs where validation_run_key = %s",
                (t1["validation_run_key"],))
    return cur.fetchone() == (t1["input_hash"], t1["output_hash"])


def insert_validation(cur, rows):
    """T1 (content-keyed), T3, T4 (insert-if-absent), T2, T5, T6, T7 of one validation run, in foreign-key order.
    Returns 'inserted' or 'already_present'; raises NondeterminismError."""
    t1 = rows["T1"]
    cur.execute("savepoint f6_t1")
    try:
        got = _insert(cur, "financial_validation_runs", T1_COLS, [t1],
                      "on conflict (validation_run_key) do nothing returning validation_run_key")
    except psycopg2.errors.UniqueViolation as exc:
        # ON CONFLICT arbitrates the content key only. A concurrent job inserting the SAME validation run can win the
        # race after this insert passed its arbiter check; the natural key (uq_fvr_input_set) then fails once that job
        # commits. The committed row decides: the same content key is a concurrent duplicate, not nondeterminism.
        cur.execute("rollback to savepoint f6_t1")
        if not _stored_equal(cur, t1):
            raise NondeterminismError(f"the same input set (natural key) is already stored under another content key: "
                                      f"{exc.diag.constraint_name}") from None
        return "already_present"
    cur.execute("release savepoint f6_t1")
    if not got:
        if not _stored_equal(cur, t1):
            raise NondeterminismError(f"validation run {t1['validation_run_key']} is stored with other hashes")
        return "already_present"
    _insert(cur, "financial_op1_records", T3_COLS, rows["T3"])
    for f in rows["T4"]:
        inserted = _insert(cur, "financial_economic_facts", T4_COLS, [f],
                           "on conflict (ef_key) do nothing returning ef_key")
        if not inserted:
            cur.execute(f"select {', '.join(IDENTITY_COLS)} from financial_economic_facts where ef_key = %s",
                        (f["ef_key"],))
            stored = tuple(str(v) if c == "issuer_id" else v for c, v in zip(IDENTITY_COLS, cur.fetchone()))
            if stored != tuple(f[c] for c in IDENTITY_COLS):
                raise NondeterminismError(f"fact {f['ef_key']} is stored with another identity")
    _insert(cur, "financial_candidate_validations", T2_COLS, rows["T2"])
    _insert(cur, "financial_source_observations", T5_COLS, rows["T5"])
    _insert(cur, "financial_so_members", T6_COLS, rows["T6"])
    _insert(cur, "financial_so_comparisons", T7_COLS, rows["T7"])
    return "inserted"


def insert_configuration(cur, row):
    """T8, insert-if-absent. Returns 'inserted' or 'already_present' (the stored envelope must be identical)."""
    got = _insert(cur, "financial_reconciliation_configurations", T8_COLS, [row],
                  "on conflict (configuration_id) do nothing returning configuration_id")
    if got:
        return "inserted"
    cur.execute("select configuration_json from financial_reconciliation_configurations where configuration_id = %s",
                (row["configuration_id"],))
    if cur.fetchone()[0] != row["configuration_json"]:
        raise NondeterminismError(f"configuration {row['configuration_id']} is stored with another envelope")
    return "already_present"


def latest_batch(cur, configuration_id, issuer_id):
    cur.execute("select batch_id, sequence, partition_input_hash, results_count from financial_reconciliation_batches "
                "where configuration_id = %s and issuer_id = %s order by sequence desc limit 1",
                (configuration_id, issuer_id))
    return cur.fetchone()


def latest_record(cur, ef_key, configuration_id):
    cur.execute("select record_id, sequence, input_hash, output_hash from financial_reconciliation_records "
                "where ef_key = %s and configuration_id = %s order by sequence desc limit 1",
                (ef_key, configuration_id))
    return cur.fetchone()


def insert_batch(cur, row):
    """T12 with its chain columns from the latest batch of (configuration, issuer). Returns batch_id."""
    last = latest_batch(cur, row["configuration_id"], row["issuer_id"])
    row = dict(row, sequence=(last[1] + 1) if last else 1, previous_batch_id=last[0] if last else None)
    (bid,), = _insert(cur, "financial_reconciliation_batches", T12_COLS, [row], "returning batch_id")
    return bid


def insert_record(cur, t13, t14, t15, batch_id, last):
    """T13 appended after `last` (the latest record of its fact and configuration, or None), then T14 / T15."""
    row = dict(t13, batch_id=batch_id, sequence=(last[1] + 1) if last else 1,
               previous_record_id=last[0] if last else None)
    (rid,), = _insert(cur, "financial_reconciliation_records", T13_COLS, [row], "returning record_id")
    _insert(cur, "financial_reconciliation_inputs", T14_COLS, [dict(r, record_id=rid) for r in t14])
    _insert(cur, "financial_reconciliation_comparisons", T15_COLS, [dict(r, record_id=rid) for r in t15])
    return rid


def insert_results(cur, rows):
    _insert(cur, "financial_reconciliation_batch_results", T16_COLS, rows)
