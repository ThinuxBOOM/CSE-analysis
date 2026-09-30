"""
Security preflight of the F6.4 worker role (docs/F6.4_DESIGN.md section 15.6), run before every job that writes.
Modelled on P3's preflight; it reuses P2's role checks (not a superuser, not the owner, not a member of cse_owner /
cse_migrator / cse_backup / pg_read_all_data) and then checks migration 0015 as ACTUAL PostgreSQL privileges:
exactly SELECT + INSERT on T1-T8 and T10-T16, SELECT only on T9 and the views, no UPDATE / DELETE / TRUNCATE
anywhere, EXECUTE on the section 11.6.3 helpers and on no trigger function, and every 0015 trigger present and
enabled. [] means the role model holds; any problem refuses the job (exit 5) before anything is written.
"""
from ..market_capture import runs as p2runs
from . import HELPER_FUNCTIONS

WRITE_TABLES = ("financial_validation_runs", "financial_candidate_validations", "financial_op1_records",
                "financial_economic_facts", "financial_source_observations", "financial_so_members",
                "financial_so_comparisons", "financial_reconciliation_configurations", "financial_f6_jobs",
                "financial_f6_job_events", "financial_reconciliation_batches", "financial_reconciliation_records",
                "financial_reconciliation_inputs", "financial_reconciliation_comparisons",
                "financial_reconciliation_batch_results")
READ_ONLY = ("financial_reconciliation_designations", "financial_f6_job_state", "financial_validation_run_current",
             "financial_reconciliation_designated", "financial_reconciliation_current", "financial_fact_state",
             "financial_fact_provenance")
NEVER = ("UPDATE", "DELETE", "TRUNCATE")

# table -> the migration 0015 triggers that must exist and be enabled
TRIGGERS = {
    "financial_reconciliation_configurations": ("trg_frc_envelope", "trg_frc_guard", "trg_frc_append_only",
                                                "trg_frc_no_truncate"),
    "financial_f6_jobs": ("trg_ffj_append_only", "trg_ffj_no_truncate"),
    "financial_f6_job_events": ("trg_ffje_guard", "trg_ffje_append_only", "trg_ffje_no_truncate"),
    "financial_reconciliation_designations": ("trg_frd_owner_only", "trg_frd_append_only", "trg_frd_no_truncate"),
    "financial_validation_runs": ("trg_fvr_envelope", "trg_fvr_guard", "trg_fvr_complete", "trg_fvr_append_only",
                                  "trg_fvr_no_truncate"),
    "financial_op1_records": ("trg_fop1_guard", "trg_fop1_seal", "trg_fop1_append_only", "trg_fop1_no_truncate"),
    "financial_economic_facts": ("trg_fef_guard", "trg_fef_complete", "trg_fef_append_only", "trg_fef_no_truncate"),
    "financial_candidate_validations": ("trg_fcv_envelope", "trg_fcv_guard", "trg_fcv_seal", "trg_fcv_append_only",
                                        "trg_fcv_no_truncate"),
    "financial_source_observations": ("trg_fso_envelope", "trg_fso_guard", "trg_fso_complete", "trg_fso_seal",
                                      "trg_fso_append_only", "trg_fso_no_truncate"),
    "financial_so_members": ("trg_fsm_guard", "trg_fsm_seal", "trg_fsm_append_only", "trg_fsm_no_truncate"),
    "financial_so_comparisons": ("trg_fsc_guard", "trg_fsc_seal", "trg_fsc_append_only", "trg_fsc_no_truncate"),
    "financial_reconciliation_batches": ("trg_frb_envelope", "trg_frb_guard", "trg_frb_complete",
                                         "trg_frb_append_only", "trg_frb_no_truncate"),
    "financial_reconciliation_records": ("trg_frr_envelope", "trg_frr_guard", "trg_frr_complete", "trg_frr_seal",
                                         "trg_frr_append_only", "trg_frr_no_truncate"),
    "financial_reconciliation_inputs": ("trg_fri_guard", "trg_fri_seal", "trg_fri_append_only", "trg_fri_no_truncate"),
    "financial_reconciliation_comparisons": ("trg_frcmp_guard", "trg_frcmp_seal", "trg_frcmp_append_only",
                                             "trg_frcmp_no_truncate"),
    "financial_reconciliation_batch_results": ("trg_frbr_guard", "trg_frbr_seal", "trg_frbr_append_only",
                                               "trg_frbr_no_truncate"),
}
TRIGGER_FUNCTIONS = (
    "f6_envelope_hash_guard()", "f6_validation_run_guard()", "f6_candidate_validation_guard()", "f6_op1_record_guard()",
    "f6_economic_fact_guard()", "f6_configuration_guard()", "f6_source_observation_guard()", "f6_so_member_guard()",
    "f6_so_comparison_guard()",
    "f6_batch_guard()", "f6_reconciliation_record_guard()", "f6_reconciliation_input_guard()",
    "f6_reconciliation_comparison_guard()", "f6_batch_result_guard()", "f6_designation_guard()",
    "f6_job_event_guard()", "f6_validation_run_complete()", "f6_source_observation_complete()",
    "f6_reconciliation_record_complete()", "f6_batch_complete()", "f6_economic_fact_complete()", "f6_child_seal()")


def _one(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
    conn.commit()
    return row


def problems(conn, expected_role="cse_worker"):
    import psycopg2
    out = list(p2runs.security_preflight(conn, expected_role))
    if any(p.startswith("connected as") for p in out):
        return out
    try:
        for rel in WRITE_TABLES + READ_ONLY:
            if _one(conn, "select to_regclass(%s)", (f"public.{rel}",))[0] is None:
                out.append(f"relation {rel} missing (migration 0015 not applied?)")
                continue
            must = ("SELECT", "INSERT") if rel in WRITE_TABLES else ("SELECT",)
            must_not = NEVER if rel in WRITE_TABLES else ("INSERT",) + NEVER
            for priv in must:
                if not _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    out.append(f"{expected_role} lacks {priv} on {rel}")
            for priv in must_not:
                if _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    what = ("designation is an owner decision" if rel == "financial_reconciliation_designations"
                            else "F6.4 storage is append-only")
                    out.append(f"{expected_role} has {priv} on {rel}: {what}")
        for fn in HELPER_FUNCTIONS:
            if not _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} lacks EXECUTE on {fn} (a section 11.6.3 helper its guards call)")
        for fn in TRIGGER_FUNCTIONS:
            if _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} has EXECUTE on the trigger function {fn}")
        for table, names in TRIGGERS.items():
            if _one(conn, "select to_regclass(%s)", (f"public.{table}",))[0] is None:
                continue
            for name in names:
                enabled = _one(conn, "select coalesce(bool_and(tgenabled = 'O'), false) from pg_trigger where "
                                     "tgrelid = %s::regclass and tgname = %s and not tgisinternal",
                               (f"public.{table}", name))[0]
                if not enabled:
                    out.append(f"trigger {name} on {table} missing or disabled")
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the F6.4 tables: {type(exc).__name__}: {exc}".strip())
    return out
