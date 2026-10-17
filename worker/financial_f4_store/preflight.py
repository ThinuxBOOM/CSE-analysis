"""
R-F4's preflight (docs/R_F4_DESIGN.md sections 5 and 10). Nothing frozen calls it: it runs in staging after the
migrations, in the production smoke tests and at every checkpoint. It answers three separate questions, and `report`
keeps them apart:

    file     the checkout's migration 0018 is byte-identical (LF-normalised, as P1's runner hashes it) to the code's pin
    objects  the database's 0018 objects, as they are now: the five tables; every constraint 0018 creates, by name,
             with its type and its definition (its key columns, a foreign key's referenced table and columns, a check's
             expression), validated, and no other constraint; for each of cse_worker and cse_reader the privileges it
             must have (cse_worker SELECT + INSERT, cse_reader SELECT) and must not have (UPDATE, DELETE, TRUNCATE;
             INSERT for the reader), a forbidden INSERT or UPDATE found whether granted on the table or on a single
             column; the guard and append-only triggers, present, enabled and exactly as 0018 creates them (schema.py:
             the function by schema, signature, definition and configuration, the timing, no WHEN condition,
             arguments or column list), and no other trigger enabled on the tables; the guard function with the
             caller's rights and no EXECUTE for either role
    ledger   ops.schema_migrations records 0018 at the pinned hash: 'verified', 'failed' (a problem was found), or
             'unverifiable' (this connection cannot read the ledger)

Who can read the ledger (no grant is added for this): cse_reader, which 0009 gives USAGE on schema ops and SELECT on
its tables (0009_local_security_boundary.sql:60,117), through a login that inherits its privileges or whose session has
set role cse_reader (a member that does neither gets 'unverifiable'); the superuser; and the migration role
cse_migrator, which reads it, as P1's runner does (worker/ops/migrate.py, status), as the owner role inside a read-only
transaction that is rolled back. cse_worker cannot, by design (least privilege): for it the ledger is 'unverifiable'
and `report` is never clean.
The owner-level check:
    CSE_DB_USER=cse_migrator python -m worker.financial_f4_store preflight --migration-role
The object checks name the roles explicitly, so one run verifies both roles' privileges whoever runs it; the migration
role (NOINHERIT, no USAGE on schema public) reads the catalogue for them as the owner role, read-only.
"""
import os

from ..ops import migrate as mig
from . import codec, schema

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = "0018_f4_structure_persistence.sql"
MIGRATION_VERSION = 18
MIGRATION_SHA256 = "39855c0dd3c9e9d6d6726dad78399d55af81aba1c43182099599a3f407991fe4"
OWNER_ROLE = "cse_owner"
GUARD_FUNCTION = schema.GUARD_FUNCTION
TRIGGERS = {t: tuple(names) for t, names in schema.TRIGGERS.items()}
PRIVILEGES = {"cse_worker": (("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")),
              "cse_reader": (("SELECT",), ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))}
# The privileges PostgreSQL also grants per column (DELETE and TRUNCATE exist only for a whole table): a forbidden one
# is found whether it was granted on the table or on any of its columns.
COLUMN_PRIVILEGES = frozenset({"SELECT", "INSERT", "UPDATE", "REFERENCES"})
# Named in migration 0018 (tests/test_rf4_unit.py ties these names and the foreign-key counts to its text).
CONSTRAINTS = {
    "financial_f4_extractions": ("chk_ff4e_hex", "chk_ff4e_store_version", "chk_ff4e_counts",
                                 "uq_financial_f4_extraction"),
    "financial_f4_statements": ("chk_ff4s_index", "uq_financial_f4_statement"),
    "financial_f4_columns": ("chk_ff4k_seq", "chk_ff4k_numbers", "chk_ff4k_dates", "uq_financial_f4_column",
                             "uq_financial_f4_column_seq"),
    "financial_f4_rows": ("chk_ff4r_seq", "chk_ff4r_numbers", "uq_financial_f4_row", "uq_financial_f4_row_seq"),
    "financial_f4_cells": ("chk_ff4c_seq", "chk_ff4c_decimal", "uq_financial_f4_cell", "uq_financial_f4_cell_seq",
                           "fk_ff4c_row", "fk_ff4c_column"),
}
FOREIGN_KEYS = {"financial_f4_extractions": 2, "financial_f4_statements": 1, "financial_f4_columns": 1,
                "financial_f4_rows": 1, "financial_f4_cells": 3}
# Every constraint 0018 creates, by name: (pg_constraint.contype, its definition as PostgreSQL 17's
# pg_get_constraintdef renders it under search_path public). The names above, plus each table's primary key and the
# foreign keys of its `references` columns under PostgreSQL's default names. A table must have exactly these: a
# same-name replacement of another type or definition, a weakened or NOT VALID one, or an extra one is a problem.
# tests/test_rf4_postgres.py checks this map against a database migrated by P1's runner.
CONSTRAINT_DEFINITIONS = {
    "financial_f4_extractions": {
        "chk_ff4e_counts": ("c",
            'CHECK (((page_count >= 0) AND (statement_count >= 0) AND (column_count >= 0) AND (row_count >= 0) AND '
            '(cell_count >= 0)))'),
        "chk_ff4e_hex": ("c",
            "CHECK (((document_sha256 ~ '^[0-9a-f]{64}$'::text) AND (f4_content_sha256 ~ '^[0-9a-f]{64}$'::text) AND "
            "(f3_input_sha256 ~ '^[0-9a-f]{64}$'::text)))"),
        "chk_ff4e_store_version": ("c", "CHECK ((store_version ~ '^f4\\.store\\.[0-9]+$'::text))"),
        "financial_f4_extractions_classification_id_fkey": ("f",
            'FOREIGN KEY (classification_id) REFERENCES report_document_classifications(id)'),
        "financial_f4_extractions_cse_filing_id_fkey": ("f",
            'FOREIGN KEY (cse_filing_id) REFERENCES report_filings(cse_filing_id)'),
        "financial_f4_extractions_pkey": ("p", 'PRIMARY KEY (id)'),
        "uq_financial_f4_extraction": ("u",
            'UNIQUE (cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id)'),
    },
    "financial_f4_statements": {
        "chk_ff4s_index": ("c", 'CHECK ((statement_index >= 0))'),
        "financial_f4_statements_extraction_id_fkey": ("f",
            'FOREIGN KEY (extraction_id) REFERENCES financial_f4_extractions(id)'),
        "financial_f4_statements_pkey": ("p", 'PRIMARY KEY (id)'),
        "uq_financial_f4_statement": ("u", 'UNIQUE (extraction_id, statement_index)'),
    },
    "financial_f4_columns": {
        "chk_ff4k_dates": ("c",
            "CHECK ((((start_date IS NULL) OR (start_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text)) AND ((end_date IS "
            "NULL) OR (end_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text))))"),
        "chk_ff4k_numbers": ("c",
            "CHECK (((x0 ~ '^-?[0-9]'::text) AND (x1 ~ '^-?[0-9]'::text) AND (right_edge ~ '^-?[0-9]'::text) AND "
            "((header_x0 IS NULL) OR (header_x0 ~ '^-?[0-9]'::text)) AND ((header_x1 IS NULL) OR (header_x1 ~ "
            "'^-?[0-9]'::text))))"),
        "chk_ff4k_seq": ("c", 'CHECK ((seq >= 0))'),
        "financial_f4_columns_pkey": ("p", 'PRIMARY KEY (id)'),
        "financial_f4_columns_statement_id_fkey": ("f",
            'FOREIGN KEY (statement_id) REFERENCES financial_f4_statements(id)'),
        "uq_financial_f4_column": ("u", 'UNIQUE (statement_id, column_index)'),
        "uq_financial_f4_column_seq": ("u", 'UNIQUE (statement_id, seq)'),
    },
    "financial_f4_rows": {
        "chk_ff4r_numbers": ("c", "CHECK (((y0 ~ '^-?[0-9]'::text) AND (y1 ~ '^-?[0-9]'::text)))"),
        "chk_ff4r_seq": ("c", 'CHECK ((seq >= 0))'),
        "financial_f4_rows_pkey": ("p", 'PRIMARY KEY (id)'),
        "financial_f4_rows_statement_id_fkey": ("f",
            'FOREIGN KEY (statement_id) REFERENCES financial_f4_statements(id)'),
        "uq_financial_f4_row": ("u", 'UNIQUE (statement_id, row_index)'),
        "uq_financial_f4_row_seq": ("u", 'UNIQUE (statement_id, seq)'),
    },
    "financial_f4_cells": {
        "chk_ff4c_decimal": ("c",
            'CHECK (((parsed_value_text IS NULL) OR (parsed_value_text ~ '
            "'^-?[0-9]+(\\.[0-9]+)?(E[+-][0-9]+)?$'::text)))"),
        "chk_ff4c_seq": ("c", 'CHECK (((seq >= 0) AND (value_ordinal >= 0)))'),
        "financial_f4_cells_pkey": ("p", 'PRIMARY KEY (id)'),
        "financial_f4_cells_statement_id_fkey": ("f",
            'FOREIGN KEY (statement_id) REFERENCES financial_f4_statements(id)'),
        "fk_ff4c_column": ("f",
            'FOREIGN KEY (statement_id, column_index) REFERENCES financial_f4_columns(statement_id, column_index)'),
        "fk_ff4c_row": ("f",
            'FOREIGN KEY (statement_id, row_index) REFERENCES financial_f4_rows(statement_id, row_index)'),
        "uq_financial_f4_cell": ("u", 'UNIQUE (statement_id, row_index, column_index, value_ordinal)'),
        "uq_financial_f4_cell_seq": ("u", 'UNIQUE (statement_id, seq)'),
    },
}
CONSTRAINT_KINDS = {"c": "check", "f": "foreign key", "p": "primary key", "u": "unique", "x": "exclusion",
                    "t": "constraint trigger"}


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


def _constraint_problems(table, live):
    """How `live` ({name: (contype, validated, definition)}) differs from CONSTRAINT_DEFINITIONS[table]."""
    out = []
    for name, (kind, definition) in CONSTRAINT_DEFINITIONS[table].items():
        got = live.get(name)
        if got is None:
            out.append(f"{table}: constraint {name} is missing")
            continue
        got_kind, validated, got_definition = got
        if got_kind != kind:
            out.append(f"{table}: constraint {name} is a {CONSTRAINT_KINDS.get(got_kind, got_kind)} constraint "
                       f"({got_definition}), expected a {CONSTRAINT_KINDS[kind]} constraint ({definition})")
            continue
        if got_definition.removesuffix(" NOT VALID") != definition:
            out.append(f"{table}: constraint {name} is {got_definition}, expected {definition}")
        if not validated:
            out.append(f"{table}: constraint {name} is not validated")
    for name in sorted(set(live) - set(CONSTRAINT_DEFINITIONS[table])):
        out.append(f"{table}: constraint {name} ({live[name][2]}) is not one migration 0018 creates")
    return out


def _role_problems(cur, role):
    must, must_not = PRIVILEGES[role]
    out = [p for p in schema.problems(cur, role) if ": trigger " in p]     # its privileges are checked below
    for t in codec.TABLES:
        if not _one(cur, "select to_regclass(%s) is not null", (f"public.{t}",))[0]:
            continue
        for p in must:
            if not _one(cur, "select has_table_privilege(%s, %s, %s)", (role, f"public.{t}", p))[0]:
                out.append(f"{role} lacks {p} on {t}")
        for p in must_not:
            if _one(cur, "select has_table_privilege(%s, %s, %s)", (role, f"public.{t}", p))[0]:
                out.append(f"{role} has {p} on {t}: the table is append-only")
            elif p in COLUMN_PRIVILEGES:                  # granted on some columns only: as forbidden
                cur.execute("select a.attname from pg_attribute a where a.attrelid = %s::regclass and a.attnum > 0 "
                            "and not a.attisdropped and has_column_privilege(%s, a.attrelid, a.attnum, %s) "
                            "order by a.attnum", (f"public.{t}", role, p))
                cols = [r[0] for r in cur.fetchall()]
                if cols:
                    out.append(f"{role} has {p} on column(s) {', '.join(cols)} of {t}: the table is append-only")
    got = _one(cur, "select has_function_privilege(%s, p.oid, 'EXECUTE') from pg_proc p join pg_namespace n on "
                    "n.oid = p.pronamespace where n.nspname = 'public' and p.proname = %s", (role, GUARD_FUNCTION))
    if got is not None and got[0]:
        out.append(f"{role} has EXECUTE on the trigger function {GUARD_FUNCTION}")
    return out


def _may_become(cur, owner_role):
    return _one(cur, "select case when exists (select 1 from pg_roles where rolname = %s) "
                     "then pg_has_role(current_user, %s, 'SET') else false end", (owner_role, owner_role))[0]


def object_problems(conn, roles=tuple(PRIVILEGES), owner_role=OWNER_ROLE):
    """The database's 0018 objects as they are now, for each of `roles`. Read-only; run it on a connection with no open
    transaction (it rolls its own back). [] when every check passes."""
    from psycopg2 import sql
    out = []
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            if not _one(cur, "select has_schema_privilege(current_user, 'public', 'USAGE')")[0] and \
                    _may_become(cur, owner_role):
                cur.execute(sql.SQL("set local role {}").format(sql.Identifier(owner_role)))
            # pg_get_constraintdef names a referenced table without its schema only when the search path makes it
            # visible: under `public` alone, a reference into any other schema shows that schema
            cur.execute("set local search_path = public")
            missing = [t for t in codec.TABLES
                       if not _one(cur, "select to_regclass(%s) is not null", (f"public.{t}",))[0]]
            out += [f"{t} is missing (migration 0018 not applied?)" for t in missing]
            for t in codec.TABLES:
                if t in missing:
                    continue
                # contype 'n' (NOT NULL, catalogued as a constraint from PostgreSQL 18) is a column property here
                cur.execute("select conname, contype, convalidated, pg_get_constraintdef(oid) from pg_constraint "
                            "where conrelid = %s::regclass and contype <> 'n'", (f"public.{t}",))
                out += _constraint_problems(t, {n: (k, v, d) for n, k, v, d in cur.fetchall()})
            got = _one(cur, "select p.prosecdef from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                            "where n.nspname = 'public' and p.proname = %s", (GUARD_FUNCTION,))
            if got is None:
                out.append(f"function {GUARD_FUNCTION} is missing")
            elif got[0]:
                out.append(f"function {GUARD_FUNCTION} is SECURITY DEFINER")
            if not missing:
                for role in roles:
                    out += _role_problems(cur, role)
        conn.rollback()
    except Exception as exc:  # noqa: BLE001 - a preflight reports, it does not crash the caller
        conn.rollback()
        out.append(f"cannot check migration 0018's objects: {type(exc).__name__}: {exc}".strip())
    # one table can miss a trigger for both roles; report it once
    return list(dict.fromkeys(out))


def _ledger_access(cur):
    """'readable', 'missing' (the role can see that ops.schema_migrations does not exist) or 'denied'. Never touches
    the ledger table itself, so it cannot raise for lack of privilege."""
    return _one(cur, "select case when not has_schema_privilege(current_user, 'ops', 'USAGE') then 'denied' "
                     "when to_regclass('ops.schema_migrations') is null then 'missing' "
                     "when has_table_privilege(current_user, 'ops.schema_migrations', 'SELECT') then 'readable' "
                     "else 'denied' end")[0]


def ledger_check(conn, owner_role=OWNER_ROLE):
    """Whether ops.schema_migrations records 0018 at its pin: {'status': verified|failed|unverifiable, 'problems',
    'read_as'}. Read-only (its transaction is rolled back); never claims 'verified' without reading the row."""
    from psycopg2 import sql
    read_as = None
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            if not _one(cur, "select exists (select 1 from pg_namespace where nspname = 'ops')")[0]:
                conn.rollback()
                return {"status": "failed", "problems": ["there is no migration ledger (schema ops is missing)"],
                        "read_as": None}
            access = _ledger_access(cur)
            if access != "denied":
                read_as = _one(cur, "select current_user")[0]
            elif _may_become(cur, owner_role):
                cur.execute(sql.SQL("set local role {}").format(sql.Identifier(owner_role)))
                access = _ledger_access(cur)
                if access != "denied":
                    read_as = owner_role
            if read_as is None:
                who = _one(cur, "select current_user")[0]
                conn.rollback()
                return {"status": "unverifiable", "read_as": None, "problems": [
                    f"role {who} cannot read ops.schema_migrations, and may not SET ROLE {owner_role}: the ledger "
                    f"entry of {MIGRATION} is NOT verified. Run the preflight as cse_reader, or the owner-level check "
                    "as the migration role: CSE_DB_USER=cse_migrator python -m worker.financial_f4_store preflight "
                    "--migration-role"]}
            if access == "missing":
                conn.rollback()
                return {"status": "failed", "read_as": read_as,
                        "problems": ["there is no migration ledger (ops.schema_migrations is missing)"]}
            cur.execute("select version, filename, sha256 from ops.schema_migrations where version = %s or "
                        "filename = %s order by version", (MIGRATION_VERSION, MIGRATION))
            rows = cur.fetchall()
        conn.rollback()
    except Exception as exc:  # noqa: BLE001 - reported, never mistaken for a pass
        conn.rollback()
        return {"status": "unverifiable", "read_as": read_as,
                "problems": [f"cannot read the migration ledger: {type(exc).__name__}: {exc}".strip()]}
    if not rows:
        problems = [f"{MIGRATION} is not in the migration ledger"]
    elif rows != [(MIGRATION_VERSION, MIGRATION, MIGRATION_SHA256)]:
        problems = [f"the migration ledger records {rows}, expected "
                    f"{[(MIGRATION_VERSION, MIGRATION, MIGRATION_SHA256)]}"]
    else:
        problems = []
    return {"status": "failed" if problems else "verified", "problems": problems, "read_as": read_as}


def report(conn, roles=tuple(PRIVILEGES), repo=REPO, owner_role=OWNER_ROLE):
    """The three checks, kept apart. `clean` only when the file and the objects have no problem AND the ledger was
    read and verified; an unverifiable ledger is never clean."""
    out = {"file": file_problems(repo), "objects": object_problems(conn, roles, owner_role),
           "ledger": ledger_check(conn, owner_role), "roles": list(roles)}
    out["clean"] = not out["file"] and not out["objects"] and out["ledger"]["status"] == "verified"
    return out
