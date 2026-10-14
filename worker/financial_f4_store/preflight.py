"""
R-F4's preflight (docs/R_F4_DESIGN.md section 5). [] means every check passes; anything else is a problem to fix
before R-F4 writes or is relied on. Nothing frozen calls it: it runs in staging after the migrations, in the
production smoke tests and at every checkpoint (design section 10). The store's own in-band check
(store.schema_problems) is narrower and runs on every save.

    file      the checkout's migration 0018 is byte-identical (LF-normalised, as P1's runner hashes it) to its pin
    database  the five tables; the role's privileges (cse_worker: SELECT + INSERT; cse_reader: SELECT; never UPDATE,
              DELETE or TRUNCATE); the guard and the append-only triggers, enabled; the guard function with the
              caller's rights and no EXECUTE for the role; and, where the role may read ops.schema_migrations, 0018's
              ledger row at the pinned hash (cse_worker has no access to the ops schema, so for it this is skipped)
"""
import os

from ..ops import migrate as mig
from . import codec

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = "0018_f4_structure_persistence.sql"
MIGRATION_SHA256 = "39855c0dd3c9e9d6d6726dad78399d55af81aba1c43182099599a3f407991fe4"
GUARD_FUNCTION = "financial_f4_extraction_guard"
TRIGGERS = {
    "financial_f4_extractions": ("trg_ff4e_guard", "trg_ff4e_append_only", "trg_ff4e_no_truncate"),
    "financial_f4_statements": ("trg_ff4s_append_only", "trg_ff4s_no_truncate"),
    "financial_f4_columns": ("trg_ff4k_append_only", "trg_ff4k_no_truncate"),
    "financial_f4_rows": ("trg_ff4r_append_only", "trg_ff4r_no_truncate"),
    "financial_f4_cells": ("trg_ff4c_append_only", "trg_ff4c_no_truncate"),
}
PRIVILEGES = {"cse_worker": (("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")),
              "cse_reader": (("SELECT",), ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))}


def file_problems(repo=REPO):
    path = os.path.join(repo, "supabase", "migrations", MIGRATION)
    if not os.path.exists(path):
        return [f"migration {MIGRATION} is missing from the checkout"]
    if mig.file_sha256(path) != MIGRATION_SHA256:
        return [f"migration {MIGRATION} differs from its pinned hash"]
    return []


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def database_problems(conn, role="cse_worker"):
    """`role`: the privileges expected of the connection's current role (cse_worker or cse_reader). Read-only; run it
    on a connection with no open transaction (it rolls its own back)."""
    must, must_not = PRIVILEGES[role]
    out = []
    try:
        with conn.cursor() as cur:
            for t in codec.TABLES:
                if not _one(cur, "select to_regclass(%s) is not null", (f"public.{t}",))[0]:
                    out.append(f"{t} is missing (migration 0018 not applied?)")
                    continue
                for p in must:
                    if not _one(cur, "select has_table_privilege(current_user, %s, %s)", (f"public.{t}", p))[0]:
                        out.append(f"the current role lacks {p} on {t}")
                for p in must_not:
                    if _one(cur, "select has_table_privilege(current_user, %s, %s)", (f"public.{t}", p))[0]:
                        out.append(f"the current role has {p} on {t}: the table is append-only")
                cur.execute("select tgname from pg_trigger where tgrelid = %s::regclass and not tgisinternal "
                            "and tgenabled = 'O'", (f"public.{t}",))
                enabled = {r[0] for r in cur.fetchall()}
                for name in TRIGGERS[t]:
                    if name not in enabled:
                        out.append(f"trigger {name} on {t} is missing or disabled")
            got = _one(cur, "select p.prosecdef, has_function_privilege(current_user, p.oid, 'EXECUTE') from pg_proc p "
                            "join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and "
                            "p.proname = %s", (GUARD_FUNCTION,))
            if got is None:
                out.append(f"function {GUARD_FUNCTION} is missing")
            else:
                if got[0]:
                    out.append(f"function {GUARD_FUNCTION} is SECURITY DEFINER")
                if got[1]:
                    out.append(f"the current role has EXECUTE on the trigger function {GUARD_FUNCTION}")
            if _one(cur, "select case when not exists (select 1 from pg_namespace where nspname = 'ops') then false "
                         "when not has_schema_privilege(current_user, 'ops', 'USAGE') then false "
                         "when to_regclass('ops.schema_migrations') is null then false "
                         "else has_table_privilege(current_user, 'ops.schema_migrations', 'SELECT') end")[0]:
                row = _one(cur, "select sha256 from ops.schema_migrations where filename = %s", (MIGRATION,))
                if row is None:
                    out.append(f"{MIGRATION} is not in the migration ledger")
                elif row[0] != MIGRATION_SHA256:
                    out.append(f"{MIGRATION} was applied at another hash than its pin")
        conn.rollback()
    except Exception as exc:  # noqa: BLE001 - a preflight reports, it does not crash the caller
        conn.rollback()
        out.append(f"cannot check migration 0018's objects: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, role="cse_worker", repo=REPO):
    return file_problems(repo) + database_problems(conn, role)
