"""
Migration 0018's objects as R-F4 relies on them, checked against the catalogue in ONE query (docs/R_F4_DESIGN.md
sections 4.4 and 5). The store runs it inside its savepoint before every save, so a missing table, a missing privilege
or a missing, disabled or altered trigger refuses the save (SchemaUnavailable) before anything is written. Nothing is
cached: a schema change made while a process runs is seen by its next save.

A trigger is checked as 0018 creates it, not only by name: the function it runs, by identity (schema public, no
arguments: a same-named function in another schema is another function) and by definition (language, the caller's
rights, the body exactly as its migration writes it, and its configuration); its timing and events (tgtype); and that it
has no WHEN condition, no arguments, no column list, and is neither a constraint trigger nor one with transition tables.
Each table's enabled triggers must be exactly the ones 0018 creates: any other is a problem, since a BEFORE trigger
firing after the guard (they fire in name order) could change a row the guard has already accepted.

Of the function's catalogue entry, exactly what changes what the function does when the trigger fires is compared:
- identity (schema, name, arguments): which function runs;
- language and body (prosrc): what it executes;
- security definer (prosecdef): with whose rights it reads report_document_classifications;
- configuration (proconfig): the settings it runs under. Migrations 0007 and 0018 set none. A function-level
  search_path in particular would change which report_document_classifications the guard's unqualified name resolves
  to, with the body unchanged.
Not compared, because they do not change what the function does when a trigger fires: volatility, strictness, cost,
rows, parallel safety, leakproofness, its owner (it runs with the caller's rights) and its comment. Its EXECUTE grants
are the preflight's (the guard function: no EXECUTE for either role).

Before all of that the store checks the saving session's name resolution (search_path_problems, below): the guard
and the store use unqualified names, so the session must not be able to resolve them outside public and pg_catalog.

The preflight runs the same check for a named role, then the checks only it makes (privileges that must be absent, the
guard function's rights, the constraints' definitions, the migration ledger).
"""
from . import codec

GUARD_FUNCTION = "financial_f4_extraction_guard"
REJECT_FUNCTION = "f5_reject_mutation"
FUNCTION_SCHEMA = "public"

# Each trigger function as its migration defines it: (migration, language, SHA-256 of the body exactly as written
# between its dollar quotes, LF line ends, its configuration: pg_proc.proconfig, the settings of its SET clauses).
# tests/test_rf4_unit.py derives the hashes and the (absent) SET clauses from the migration files.
FUNCTIONS = {
    GUARD_FUNCTION: ("0018", "plpgsql", "8d0e0e3367b01b4c230d1ef5b41898352ab8b27d17fb362852b9018386b3a76b", ()),
    REJECT_FUNCTION: ("0007", "plpgsql", "cc8e9c18d81d7bbb7d3886226d60adfe81aa9003499778d498c7f07b75e99ac8", ()),
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
# ('O' origin and 'A' always; 'D' disabled and 'R' replica-only do not), each with its function's identity,
# definition and configuration and its own properties. A function body is hashed with CRLF line ends read as LF.
_QUERY = """
select t.name,
       c.oid is not null,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'SELECT') end,
       case when c.oid is null then null else has_table_privilege(coalesce(%(role)s, current_user)::name, c.oid, 'INSERT') end,
       coalesce((select json_agg(json_build_object(
                          'name', g.tgname, 'schema', n.nspname, 'function', p.proname,
                          'arguments', pg_get_function_identity_arguments(p.oid), 'language', l.lanname,
                          'security_definer', p.prosecdef, 'config', p.proconfig,
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
        migration, language, body, config = FUNCTIONS[function]
        if (got["language"], got["security_definer"], got["body_sha256"]) != (language, False, body):
            out.append(f"{at} runs {FUNCTION_SCHEMA}.{function}() defined other than in migration {migration} "
                       f"(language {got['language']}, security definer {got['security_definer']}, body sha256 "
                       f"{got['body_sha256']}; expected {language}, False, {body})")
        if tuple(got["config"] or ()) != config:
            out.append(f"{at} runs {FUNCTION_SCHEMA}.{function}() configured {list(got['config'] or [])} "
                       f"(proconfig); migration {migration} creates it with {list(config) or 'no configuration'}")
    if got["when"] is not None:
        out.append(f"{at} has a WHEN condition ({got['when']}); migration 0018 creates it without one")
    if got["nargs"]:
        out.append(f"{at} passes {got['nargs']} argument(s); migration 0018 passes none")
    if got["columns"]:
        out.append(f"{at} fires only for column(s) {got['columns']} (attnum); migration 0018 names no columns")
    if got["constraint_trigger"] or got["transition_tables"]:
        out.append(f"{at} is a constraint trigger or has transition tables; migration 0018 creates neither")
    return out


# ================================================================================ name resolution (search_path)
#
# The guard (0018) reads report_document_classifications, and R-F4's store and reader read and write their tables, by
# unqualified names; the guard's comparisons use unqualified operators (=). With no function-level configuration
# (Finding 4), every one of those names resolves through the SAVING SESSION's effective search path, whatever set it:
# a role, role-in-database, database or all-roles setting, the server, the client's connection options, or a SET. So
# the function's body and configuration can match 0018 exactly while its lookup reads another table.
#
# PostgreSQL's lookup: relations and types are searched first in the session's temporary schema (unless pg_temp is
# listed later), then in pg_catalog (unless it is listed later), then in the listed schemas in order; "$user" stands
# for a schema named after the current role; a listed schema that does not exist, or that the role may not use, is
# skipped. Functions and operators are never looked up in the temporary schema.
#
# The invariant, checked before any R-F4 insert (and, for the sessions a role will get, by the preflight): before
# public, a session searches only
# - pg_catalog: the system schema (nothing can be added to it without superuser and allow_system_table_mods); listed
#   after public, public's operators would come before the built-in ones, so that is refused;
# - its own temporary schema, which only that session can fill: at save time it must hold no relation and no type
#   (between this check and the insert the session runs nothing else);
# - "$user", PostgreSQL's default, only while no schema named after the role exists and the role may not create one
#   (CREATE on the database); otherwise any session of the role could make one appear before public.
# Any other schema before public is refused whether or not it exists or holds a table now: one created, or given a
# table, later would be searched first. A path without public is refused. Checking only what
# report_document_classifications resolves to now would miss all of these.
#
# Remaining, and outside what a check can close: a superuser or the owner of the schemas can change any of this between
# the check and the insert (as they can change a trigger); the check sees it at the next save.
SAFE_BEFORE_PUBLIC = ("pg_catalog", "pg_temp")
USER_SCHEMA = "$user"
# PostgreSQL's whitespace in a list of names (scanner_isspace), and no other character. Python's str.isspace() is wider
# (a no-break space, U+001C to U+001F, U+0085, ...); PostgreSQL keeps those inside a name, so splitting on them would
# read a raw 'public<no-break space>, public' (a configuration file, a client's options) as public, public while
# PostgreSQL searches a schema named 'public<no-break space>' first (audit evidence, 2026-10-17).
PG_SPACE = " \t\n\r\v\f"


def parse_search_path(text):
    """The schema names of a search_path value, split as PostgreSQL splits it (SplitIdentifierString): comma-separated,
    PostgreSQL's whitespace (PG_SPACE, nothing else) around a name ignored; a double-quoted name keeps its case and ""
    inside it is a quote; an unquoted name is lower-cased (ASCII). [] for an empty value; None for one PostgreSQL would
    refuse (an unterminated quote, an empty unquoted name, a missing comma), and for an empty quoted name (""), which
    PostgreSQL accepts though no schema can have that name: refused all the same (fail-closed). Names are not truncated
    to 63 bytes as PostgreSQL truncates them; a truncated name is never public, pg_catalog, pg_temp or "$user", so the
    judgement is the same."""
    out, i, n = [], 0, len(text)
    if not text.strip(PG_SPACE):
        return out
    while True:
        while i < n and text[i] in PG_SPACE:
            i += 1
        if i < n and text[i] == '"':
            parts, j = [], i + 1
            while True:
                k = text.find('"', j)
                if k < 0:
                    return None
                parts.append(text[j:k])
                if text[k + 1:k + 2] == '"':
                    parts.append('"')
                    j = k + 2
                    continue
                i = k + 1
                break
            name = "".join(parts)
        else:
            j = i
            while i < n and text[i] != "," and text[i] not in PG_SPACE:
                i += 1
            name = "".join(c.lower() if "A" <= c <= "Z" else c for c in text[j:i])
        if not name:
            return None
        out.append(name)
        while i < n and text[i] in PG_SPACE:
            i += 1
        if i == n:
            return out
        if text[i] != ",":
            return None
        i += 1


def _shown(name):
    """A schema name as a report shows it: bare when it is a plain lower-case identifier, else its Python repr, so that
    a character PostgreSQL keeps inside the name (a no-break space, a control character) can be seen."""
    first, rest = "abcdefghijklmnopqrstuvwxyz_", "abcdefghijklmnopqrstuvwxyz0123456789_$"
    return name if name and name[0] in first and all(c in rest for c in name) else repr(name)


def path_problems(elements, role, role_schema_exists, role_may_create_schemas):
    """How a search path (its parsed elements, for `role`) breaks the invariant above; [] when it holds."""
    if "public" not in elements:
        return ["public is not in it, so an unqualified name resolves elsewhere or not at all"]
    i = elements.index("public")
    out = []
    for name in elements[:i]:
        if name in SAFE_BEFORE_PUBLIC:
            continue
        if name == USER_SCHEMA:
            if role_schema_exists:
                out.append(f'"$user" before public names an existing schema {role}')
            elif role_may_create_schemas:
                out.append(f'"$user" before public, and {role} may create a schema named {role} (CREATE on the '
                           "database)")
            continue
        out.append(f"schema {_shown(name)} is searched before public, whether or not it exists or holds a table now")
    if "pg_catalog" in elements[i + 1:] and "pg_catalog" not in elements[:i]:
        out.append("pg_catalog comes after public, so public's operators and functions come before the built-in ones")
    return out


# The saving session's own resolution, every name qualified with pg_catalog (the operators too), so that the check
# cannot itself be redirected by the path it inspects. current_schemas() creates the session's temporary schema when
# pg_temp is listed ahead of every existing schema and that schema does not exist yet, which fails for a role without
# TEMPORARY (no cse_* role has it in P1): so it is not called while the setting mentions pg_temp (any case, even inside
# another name) and the session has no temporary schema. Nothing is then searched in a temporary schema, and the parsed
# setting is judged alone (audit evidence, 2026-10-17).
_SESSION_QUERY = """
select pg_catalog.current_setting('search_path'),
       case when pg_catalog.strpos(pg_catalog.lower(pg_catalog.current_setting('search_path')), 'pg_temp')
                   OPERATOR(pg_catalog.=) 0
              or exists (select 1 from pg_catalog.pg_namespace n
                          where n.oid OPERATOR(pg_catalog.=) pg_catalog.pg_my_temp_schema())
            then pg_catalog.current_schemas(true)::pg_catalog.text[] end,
       (select n.nspname::pg_catalog.text from pg_catalog.pg_namespace n
         where n.oid OPERATOR(pg_catalog.=) pg_catalog.pg_my_temp_schema()),
       current_user::pg_catalog.text,
       exists (select 1 from pg_catalog.pg_namespace n where n.nspname OPERATOR(pg_catalog.=) current_user),
       pg_catalog.has_database_privilege(current_user, pg_catalog.current_database(), 'CREATE'),
       (select pg_catalog.count(*) from pg_catalog.pg_class c
         where c.relnamespace OPERATOR(pg_catalog.=) pg_catalog.pg_my_temp_schema())
         OPERATOR(pg_catalog.+)
       (select pg_catalog.count(*) from pg_catalog.pg_type t
         where t.typnamespace OPERATOR(pg_catalog.=) pg_catalog.pg_my_temp_schema())
"""


def search_path_problems(cur):
    """[] when the current session's name resolution meets the invariant above. One catalogue query, run by the store
    before anything else in its savepoint: until it passes, no other unqualified name is trusted."""
    cur.execute(_SESSION_QUERY)
    setting, schemas, temp, role, role_schema, may_create, temp_objects = cur.fetchone()
    elements = parse_search_path(setting)
    if elements is None:
        out = [f"search_path {setting!r} cannot be parsed"]
    else:
        out = [f"search_path {setting!r}: {p}" for p in path_problems(elements, role, role_schema, may_create)]
    if schemas is None:                           # not read (the query): the parsed setting is judged alone
        pass
    elif "public" in schemas:                     # what this session does search before public, implicit schemas too
        extra = [s for s in schemas[:schemas.index("public")] if s not in ("pg_catalog", temp)]
        if extra:
            out.append(f"this session searches {', '.join(_shown(s) for s in extra)} before public")
    elif elements and "public" in elements:
        out.append("this session does not search public (it may not use it)")
    if temp_objects:
        out.append(f"this session's temporary schema {temp} holds {temp_objects} relation(s) or type(s); it is "
                   "searched before public and pg_catalog for every unqualified relation and type name")
    return out


def problems(cur, role=None):
    """[] when every table exists, `role` (None: the current role) has SELECT and INSERT on it, every trigger 0018
    creates is present, enabled and exactly as 0018 creates it, and no other trigger is enabled on it (the module
    docstring). One catalogue query."""
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
        for trigger in sorted(set(live) - set(TRIGGERS[name])):
            got = live[trigger]
            out.append(f"{name}: trigger {trigger} (runs {got['schema']}.{got['function']}, tgtype {got['tgtype']}) is "
                       "enabled and is not one migration 0018 creates")
    return out
