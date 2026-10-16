"""
Migration 0018's objects as R-F4 relies on them, checked against the catalogue in ONE query (docs/R_F4_DESIGN.md
sections 4.4 and 5). The store runs it inside its savepoint before every save, so a missing table, a missing privilege
or a missing, disabled or altered trigger refuses the save (SchemaUnavailable) before anything is written. Nothing is
cached: a schema change made while a process runs is seen by its next save.

A trigger is checked as 0018 creates it, not only by name: the function it runs, by identity (schema public, no
arguments: a same-named function in another schema is another function) and by definition (language, the caller's
rights, and the body exactly as its migration writes it); its timing and events (tgtype); and that it has no WHEN
condition, no arguments, no column list, and is neither a constraint trigger nor one with transition tables.

The preflight runs the same check for a named role, then the checks only it makes (privileges that must be absent, the
guard function's rights, the constraints' definitions, the migration ledger).
"""
from . import codec

GUARD_FUNCTION = "financial_f4_extraction_guard"
REJECT_FUNCTION = "f5_reject_mutation"
FUNCTION_SCHEMA = "public"

# Each trigger function as its migration defines it: (migration, language, SHA-256 of the body exactly as written
# between its dollar quotes, LF line ends). tests/test_rf4_unit.py derives both hashes from the migration files.
FUNCTIONS = {
    GUARD_FUNCTION: ("0018", "plpgsql", "8d0e0e3367b01b4c230d1ef5b41898352ab8b27d17fb362852b9018386b3a76b"),
    REJECT_FUNCTION: ("0007", "plpgsql", "cc8e9c18d81d7bbb7d3886226d60adfe81aa9003499778d498c7f07b75e99ac8"),
}

# pg_trigger.tgtype bits: 1 row, 2 before, 4 insert, 8 delete, 16 update, 32 truncate.
BEFORE_INSERT_ROW = 1 | 2 | 4
BEFORE_UPDATE_DELETE_ROW = 1 | 2 | 8 | 16
BEFORE_TRUNCATE_STATEMENT = 2 | 32

# table -> {trigger: (function, tgtype)}, exactly as migration 0018 creates them.
TRIGGERS = {
    "financial_f4_extractions": {"trg_ff4e_guard": (GUARD_FUNCTION, BEFORE_INSERT_ROW),
                                 "trg_ff4e_append_only": (REJECT_FUNCTION, BEFORE_UPDATE_DELETE_ROW),
                                 "trg_ff4e_no_truncate": (REJECT_FUNCTION, BEFORE_TRUNCATE_STATEMENT)},
    "financial_f4_statements": {"trg_ff4s_append_only": (REJECT_FUNCTION, BEFORE_UPDATE_DELETE_ROW),
                                "trg_ff4s_no_truncate": (REJECT_FUNCTION, BEFORE_TRUNCATE_STATEMENT)},
    "financial_f4_columns": {"trg_ff4k_append_only": (REJECT_FUNCTION, BEFORE_UPDATE_DELETE_ROW),
                             "trg_ff4k_no_truncate": (REJECT_FUNCTION, BEFORE_TRUNCATE_STATEMENT)},
    "financial_f4_rows": {"trg_ff4r_append_only": (REJECT_FUNCTION, BEFORE_UPDATE_DELETE_ROW),
                          "trg_ff4r_no_truncate": (REJECT_FUNCTION, BEFORE_TRUNCATE_STATEMENT)},
    "financial_f4_cells": {"trg_ff4c_append_only": (REJECT_FUNCTION, BEFORE_UPDATE_DELETE_ROW),
                           "trg_ff4c_no_truncate": (REJECT_FUNCTION, BEFORE_TRUNCATE_STATEMENT)},
}

# What the store needs on every table: INSERT to write, SELECT for the conflict check and the read-back.
STORE_PRIVILEGES = ("SELECT", "INSERT")

# One row per table: whether it exists, the role's privileges on it, and its triggers that fire in normal operation
# ('O' origin and 'A' always; 'D' disabled and 'R' replica-only do not), each with its function's identity and
# definition and its own properties. A function body is hashed with CRLF line ends read as LF.
_QUERY = """
select t.name,
       c.oid is not null,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'SELECT') end,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'INSERT') end,
       coalesce((select json_agg(json_build_object(
                          'name', g.tgname, 'schema', n.nspname, 'function', p.proname,
                          'arguments', pg_get_function_identity_arguments(p.oid), 'language', l.lanname,
                          'security_definer', p.prosecdef,
                          'body_sha256',
                          encode(sha256(convert_to(replace(p.prosrc, E'\\r\\n', E'\\n'), 'UTF8')), 'hex'),
                          'tgtype', g.tgtype, 'when', pg_get_expr(g.tgqual, g.tgrelid), 'nargs', g.tgnargs,
                          'columns', g.tgattr::text, 'constraint_trigger', g.tgconstraint <> 0,
                          'transition_tables', g.tgoldtable is not null or g.tgnewtable is not null)
                        order by g.tgname)
                   from pg_trigger g join pg_proc p on p.oid = g.tgfoid
                   join pg_namespace n on n.oid = p.pronamespace join pg_language l on l.oid = p.prolang
                  where g.tgrelid = c.oid and not g.tgisinternal and g.tgenabled in ('O', 'A')), '[]'::json)
  from unnest(%(tables)s::text[]) with ordinality as t(name, n)
  left join pg_class c on c.oid = to_regclass('public.' || t.name)
 order by t.n
"""


def _trigger_problems(table, trigger, want, got):
    """How the live trigger `got` (a row of _QUERY's array) differs from 0018's `want` (function, tgtype)."""
    at = f"{table}: trigger {trigger}"
    function, tgtype = want
    if (got["function"], got["tgtype"]) != (function, tgtype):
        return [f"{at} runs {got['function']} (tgtype {got['tgtype']}), expected {function} (tgtype {tgtype})"]
    out = []
    if (got["schema"], got["arguments"]) != (FUNCTION_SCHEMA, ""):
        out.append(f"{at} runs {got['schema']}.{function}({got['arguments']}), expected {FUNCTION_SCHEMA}.{function}()")
    else:
        migration, language, body = FUNCTIONS[function]
        if (got["language"], got["security_definer"], got["body_sha256"]) != (language, False, body):
            out.append(f"{at} runs {FUNCTION_SCHEMA}.{function}() defined other than in migration {migration} "
                       f"(language {got['language']}, security definer {got['security_definer']}, body sha256 "
                       f"{got['body_sha256']}; expected {language}, False, {body})")
    if got["when"] is not None:
        out.append(f"{at} has a WHEN condition ({got['when']}); migration 0018 creates it without one")
    if got["nargs"]:
        out.append(f"{at} passes {got['nargs']} argument(s); migration 0018 passes none")
    if got["columns"]:
        out.append(f"{at} fires only for column(s) {got['columns']} (attnum); migration 0018 names no columns")
    if got["constraint_trigger"] or got["transition_tables"]:
        out.append(f"{at} is a constraint trigger or has transition tables; migration 0018 creates neither")
    return out


def problems(cur, role=None):
    """[] when every table exists, `role` (None: the current role) has SELECT and INSERT on it, and every trigger
    0018 creates is present, enabled and exactly as 0018 creates it (the module docstring). One catalogue query."""
    cur.execute(_QUERY, {"role": role, "tables": list(codec.TABLES)})
    who = role or "the current role"
    out = []
    for name, exists, can_select, can_insert, triggers in cur.fetchall():
        if not exists:
            out.append(f"{name} is missing (migration 0018 not applied?)")
            continue
        for priv, ok in zip(STORE_PRIVILEGES, (can_select, can_insert)):
            if not ok:
                out.append(f"{name}: {who} lacks {priv}")
        live = {t["name"]: t for t in triggers}
        for trigger, want in TRIGGERS[name].items():
            got = live.get(trigger)
            if got is None:
                out.append(f"{name}: trigger {trigger} is missing or disabled")
            else:
                out += _trigger_problems(name, trigger, want, got)
    return out
