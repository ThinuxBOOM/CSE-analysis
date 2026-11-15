"""
Security preflight of the MF worker role (Phase 3 §13.1), run before every MF command that writes. It reuses P2's role
checks (not a superuser, not the owner, not a member of cse_owner / cse_migrator / cse_backup / pg_read_all_data) and
then checks migration 0019 as actual PostgreSQL privileges: SELECT + INSERT on the worker's tables, SELECT only on the
owner's, no UPDATE / DELETE / TRUNCATE anywhere, no EXECUTE on a guard function, every 0019 trigger present and
enabled. [] means the role model holds; any problem refuses the command before anything is written.
"""
from ..market_capture import runs as p2runs
from .calendar_sync import TABLE as CALENDAR

WRITE_TABLES = ("mf_configurations", "mf_calendar_observations", "mf_snapshots", "mf_snapshot_factors",
                "mf_snapshot_move_flags", "mf_snapshot_rows", "mf_target_builds", "mf_target_rows")
READ_ONLY = ("mf_designations", "mf_identity_observations", "mf_corporate_action_observations", "mf_history_imports",
             "mf_history_bars", "mf_history_security_complete", "mf_history_quarantine")
NEVER = ("UPDATE", "DELETE", "TRUNCATE")
GUARDS = {"mf_configurations": "trg_mfc_guard", "mf_designations": "trg_mfd_owner",
          "mf_identity_observations": "trg_mfi_owner", "mf_corporate_action_observations": "trg_mfa_owner",
          "mf_history_imports": "trg_mfh_owner", "mf_history_bars": "trg_mfb_owner",
          "mf_history_security_complete": "trg_mfs_owner", "mf_history_quarantine": "trg_mfq_owner",
          "mf_calendar_observations": "trg_mfk_guard"}
GUARD_FUNCTIONS = ("mf_configuration_guard()", "mf_owner_only_guard()", "mf_calendar_guard()")


def triggers(table):
    names = [f"trg_{table}_append_only", f"trg_{table}_no_truncate"]
    return ([GUARDS[table]] if table in GUARDS else []) + names


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
                out.append(f"relation {rel} missing (migration 0019 not applied?)")
                continue
            must = ("SELECT", "INSERT") if rel in WRITE_TABLES else ("SELECT",)
            must_not = NEVER if rel in WRITE_TABLES else ("INSERT",) + NEVER
            for priv in must:
                if not _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    out.append(f"{expected_role} lacks {priv} on {rel}")
            for priv in must_not:
                if _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    what = "owner evidence" if rel in READ_ONLY and priv == "INSERT" else "MF storage is append-only"
                    out.append(f"{expected_role} has {priv} on {rel}: {what}")
            for name in triggers(rel):
                enabled = _one(conn, "select coalesce(bool_and(tgenabled = 'O'), false) from pg_trigger where "
                                     "tgrelid = %s::regclass and tgname = %s and not tgisinternal",
                               (f"public.{rel}", name))[0]
                if not enabled:
                    out.append(f"trigger {name} on {rel} missing or disabled")
        for fn in GUARD_FUNCTIONS:
            if _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} has EXECUTE on the guard function {fn}")
        if not _one(conn, "select has_table_privilege(current_user, %s, 'SELECT')", (f"public.{CALENDAR}",))[0]:
            out.append(f"{expected_role} lacks SELECT on {CALENDAR} (calendar-sync)")
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the MF tables: {type(exc).__name__}: {exc}".strip())
    return out
