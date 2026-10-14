"""
Migration 0018's objects as R-F4 relies on them, checked against the catalogue in ONE query (docs/R_F4_DESIGN.md
sections 4.4 and 5). The store runs it inside its savepoint before every save, so a missing table, a missing privilege
or a missing, disabled or altered trigger refuses the save (SchemaUnavailable) before anything is written. Nothing is
cached: a schema change made while a process runs is seen by its next save.

The preflight runs the same check for a named role, then the checks only it makes (privileges that must be absent, the
guard function's rights, constraints, the migration ledger).
"""
from . import codec

GUARD_FUNCTION = "financial_f4_extraction_guard"
REJECT_FUNCTION = "f5_reject_mutation"

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
# ('O' origin and 'A' always; 'D' disabled and 'R' replica-only do not), each as name, function and tgtype.
_QUERY = """
select t.name,
       c.oid is not null,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'SELECT') end,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'INSERT') end,
       coalesce((select array_agg(array[g.tgname::text, p.proname::text, g.tgtype::text] order by g.tgname)
                   from pg_trigger g join pg_proc p on p.oid = g.tgfoid
                  where g.tgrelid = c.oid and not g.tgisinternal and g.tgenabled in ('O', 'A')), '{}')
  from unnest(%(tables)s::text[]) with ordinality as t(name, n)
  left join pg_class c on c.oid = to_regclass('public.' || t.name)
 order by t.n
"""


def problems(cur, role=None):
    """[] when every table exists, `role` (None: the current role) has SELECT and INSERT on it, and every trigger
    0018 creates is present, enabled and runs the expected function at the expected timing. One catalogue query."""
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
        live = {t: (f, int(k)) for t, f, k in triggers}
        for trigger, want in TRIGGERS[name].items():
            got = live.get(trigger)
            if got is None:
                out.append(f"{name}: trigger {trigger} is missing or disabled")
            elif got != want:
                out.append(f"{name}: trigger {trigger} runs {got[0]} (tgtype {got[1]}), expected {want[0]} "
                           f"(tgtype {want[1]})")
    return out
