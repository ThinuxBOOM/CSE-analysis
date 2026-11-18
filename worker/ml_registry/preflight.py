"""
Security preflight of the worker role for the ML registry (Phase 5 §9, migration 0021), run before every command
that writes: P2's role checks, then 0021 as actual privileges: SELECT + INSERT on the research tables, SELECT only on
the owner tables (waivers, incident flags, designations), no UPDATE / DELETE / TRUNCATE anywhere, no EXECUTE on a
guard function, every trigger present and enabled. [] means the role model holds.
"""
from ..market_capture import runs as p2runs

WRITE_TABLES = ("ml_configurations", "ml_datasets", "ml_protocols", "ml_test_periods", "ml_runs", "ml_run_events",
                "ml_trials", "ml_artifacts", "ml_fold_models", "ml_models", "ml_model_metrics", "ml_gate_reports",
                "ml_test_accesses", "ml_model_events", "r1_predictions")
READ_ONLY = ("ml_waivers", "ml_incident_flags", "ml_designations")
NEVER = ("UPDATE", "DELETE", "TRUNCATE")
GUARDS = {"ml_configurations": ["trg_mlc_guard"], "ml_protocols": ["trg_mlp_guard"],
          "ml_test_periods": ["trg_mlt_guard"], "ml_runs": ["trg_mlr_guard"], "ml_test_accesses": ["trg_mlx_guard"],
          "ml_models": ["trg_mlm_guard"], "ml_model_events": ["trg_mle_guard", "trg_mle_designation"],
          "ml_waivers": ["trg_mlw_owner"], "ml_incident_flags": ["trg_mli_owner"],
          "ml_designations": ["trg_mlz_owner"]}
GUARD_FUNCTIONS = ("ml_configuration_guard()", "ml_protocol_guard()", "ml_test_period_guard()", "ml_run_guard()",
                   "ml_test_access_guard()", "ml_model_guard()", "ml_model_event_guard()",
                   "ml_production_designation_check()", "ml_owner_only_guard()")


def triggers(table):
    return GUARDS.get(table, []) + [f"trg_{table}_append_only", f"trg_{table}_no_truncate"]


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
                out.append(f"relation {rel} missing (migration 0021 not applied?)")
                continue
            must = ("SELECT", "INSERT") if rel in WRITE_TABLES else ("SELECT",)
            must_not = NEVER if rel in WRITE_TABLES else ("INSERT",) + NEVER
            for priv in must:
                if not _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    out.append(f"{expected_role} lacks {priv} on {rel}")
            for priv in must_not:
                if _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    out.append(f"{expected_role} has {priv} on {rel}")
            for name in triggers(rel):
                if not _one(conn, "select coalesce(bool_and(tgenabled = 'O'), false) from pg_trigger where "
                                  "tgrelid = %s::regclass and tgname = %s and not tgisinternal",
                            (f"public.{rel}", name))[0]:
                    out.append(f"trigger {name} on {rel} missing or disabled")
        for fn in GUARD_FUNCTIONS:
            if _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} has EXECUTE on the guard function {fn}")
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the ML registry tables: {type(exc).__name__}: {exc}".strip())
    return out
