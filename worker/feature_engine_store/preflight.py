"""
Security preflight of the worker role for F7 / DE (Phase 4 §10.1), run before every command that writes: P2's role
checks, then migration 0020 as actual privileges: SELECT + INSERT on the worker's tables, SELECT only on the
designations, no UPDATE / DELETE / TRUNCATE anywhere, no EXECUTE on a guard function, every trigger present and
enabled. [] means the role model holds; any problem refuses the command before anything is written.
"""
from ..market_capture import runs as p2runs

WRITE_TABLES = ("f7_configurations", "de_configurations", "f7_panels", "f7_feature_rows", "de_runs", "de_outputs")
READ_ONLY = ("f7_designations", "de_designations")
NEVER = ("UPDATE", "DELETE", "TRUNCATE")
GUARDS = {"f7_configurations": ["trg_f7c_guard"], "de_configurations": ["trg_dec_guard"],
          "f7_designations": ["trg_f7d_owner"], "de_designations": ["trg_ded_owner"], "f7_panels": ["trg_f7p_stamp"],
          "f7_feature_rows": ["trg_f7r_guard"], "de_runs": ["trg_der_stamp", "trg_der_guard"]}
GUARD_FUNCTIONS = ("f7_configuration_guard()", "f7_owner_only_guard()", "f7_stamp_guard()", "f7_row_guard()",
                   "de_run_guard()")


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
                out.append(f"relation {rel} missing (migration 0020 not applied?)")
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
        out.append(f"cannot verify the F7/DE tables: {type(exc).__name__}: {exc}".strip())
    return out
