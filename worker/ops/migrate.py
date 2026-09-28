"""
Migration runner with a hash ledger (P1).

- Files: supabase/migrations/NNNN_slug.sql, applied in version order. Gaps are allowed (0006 is intentionally
  unused); a new file numbered BELOW the highest applied version is refused (out of order).
- Hash: SHA-256 of the file content with CRLF normalised to LF (a Windows checkout and the server's Linux checkout
  of the same commit hash identically). The executed SQL is exactly the hashed, normalised text.
- Ledger: ops.schema_migrations, append-only (UPDATE/DELETE/TRUNCATE rejected by trigger). An applied file whose hash
  no longer matches, or a ledger row whose file is gone, blocks every further apply: frozen migrations are never
  edited in place (the 0007 lesson).
- Each migration runs in ONE transaction, under an advisory lock, as the NOLOGIN owner role (SET LOCAL ROLE), so every
  object is owned by cse_owner and never by the login role that ran it. A failure rolls the whole file back and
  leaves the ledger unchanged.
- The runner refuses to run as a PostgreSQL superuser and requires SET membership in the owner role.

    python -m worker.ops.migrate status|verify|apply [--to NNNN] [--dry-run]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

from . import settings as ops_settings
from .redact import Redactor

RUNNER_VERSION = "p1.migrate.1"
DEFAULT_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "supabase", "migrations"))
NAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<slug>[a-z0-9][a-z0-9_]*)\.sql$")
LOCK_KEY = 4_346_836_117_002_311        # arbitrary, fixed: serialises concurrent runners

BOOTSTRAP_SQL = """
create schema if not exists ops;
comment on schema ops is 'Operational metadata (migration ledger, backup ledger). Not market or financial data.';

create table if not exists ops.schema_migrations (
  version integer primary key,
  filename text not null unique,
  sha256 text not null,
  applied_at timestamptz not null default clock_timestamp(),
  applied_by text not null default session_user,
  applied_as text not null default current_user,
  runner_version text not null,
  duration_ms integer,
  constraint chk_schema_migrations_sha256 check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_schema_migrations_version check (version > 0)
);
comment on table ops.schema_migrations is
  'Append-only migration ledger: one row per applied migration file with the SHA-256 of its LF-normalised content.';

create or replace function ops.reject_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'table %.% is append-only: % is not allowed', tg_table_schema, tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$$;

create or replace trigger trg_schema_migrations_append_only before update or delete on ops.schema_migrations
  for each row execute function ops.reject_mutation();
create or replace trigger trg_schema_migrations_no_truncate before truncate on ops.schema_migrations
  for each statement execute function ops.reject_mutation();
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: str
    sha256: str

    def sql(self):
        return normalized_bytes(self.path).decode("utf-8")


@dataclass
class Plan:
    applied: list = field(default_factory=list)        # [(Migration, ledger row)]
    pending: list = field(default_factory=list)        # [Migration]
    mismatched: list = field(default_factory=list)     # [(Migration, ledger sha256)]
    missing_files: list = field(default_factory=list)  # [ledger row]
    renamed: list = field(default_factory=list)        # [(Migration, ledger filename)]
    out_of_order: list = field(default_factory=list)   # [Migration]

    def problems(self):
        out = [f"hash mismatch: {m.filename} is {m.sha256[:12]}… but was applied as {sha[:12]}…"
               for m, sha in self.mismatched]
        out += [f"ledger row {r[0]:04d} ({r[1]}) has no migration file" for r in self.missing_files]
        out += [f"version {m.version:04d} was applied as {old} but the file is now {m.filename}" for m, old in self.renamed]
        out += [f"out of order: {m.filename} is pending but a higher version is already applied" for m in self.out_of_order]
        return out

    def to_dict(self):
        return {"applied": [m.filename for m, _ in self.applied], "pending": [m.filename for m in self.pending],
                "problems": self.problems()}


def normalized_bytes(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def file_sha256(path):
    return hashlib.sha256(normalized_bytes(path)).hexdigest()


def discover(directory=DEFAULT_DIR):
    found, bad = {}, []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".sql"):
            continue
        m = NAME_RE.match(name)
        if not m:
            bad.append(name)
            continue
        v = int(m.group("version"))
        if v == 0:
            bad.append(name)
            continue
        if v in found:
            raise MigrationError(f"duplicate migration version {v:04d}: {found[v].filename} and {name}")
        p = os.path.join(directory, name)
        found[v] = Migration(v, name, p, file_sha256(p))
    if bad:
        raise MigrationError(f"migration files with invalid names (expected NNNN_slug.sql): {bad}")
    return [found[v] for v in sorted(found)]


def plan(migrations, ledger_rows):
    """ledger_rows: [(version, filename, sha256)] from ops.schema_migrations."""
    p = Plan()
    by_version = {m.version: m for m in migrations}
    applied_versions = set()
    for version, filename, sha in sorted(ledger_rows):
        m = by_version.get(version)
        if m is None:
            p.missing_files.append((version, filename, sha))
            continue
        applied_versions.add(version)
        if m.filename != filename:
            p.renamed.append((m, filename))
        elif m.sha256 != sha:
            p.mismatched.append((m, sha))
        else:
            p.applied.append((m, (version, filename, sha)))
    top = max([v for v, _, _ in ledger_rows], default=0)
    for m in migrations:
        if m.version in applied_versions:
            continue
        (p.out_of_order if m.version < top else p.pending).append(m)
    return p


def _ledger_rows(cur):
    cur.execute("select to_regclass('ops.schema_migrations')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute("select version, filename, sha256 from ops.schema_migrations order by version")
    return [tuple(r) for r in cur.fetchall()]


def _preflight(cur, owner_role):
    cur.execute("select session_user, rolsuper from pg_roles where rolname = session_user")
    user, is_super = cur.fetchone()
    if is_super:
        raise MigrationError(f"refusing to run migrations as superuser {user!r}: connect as the migration role")
    cur.execute("select exists (select 1 from pg_roles where rolname = %s)", (owner_role,))
    if not cur.fetchone()[0]:
        raise MigrationError(f"owner role {owner_role!r} does not exist (run ops/provision/sql/bootstrap_cluster.sql)")
    cur.execute("select pg_has_role(session_user, %s, 'SET')", (owner_role,))
    if not cur.fetchone()[0]:
        raise MigrationError(f"{user!r} cannot SET ROLE {owner_role!r}")
    return user


def _as_owner(cur, owner_role):
    from psycopg2 import sql
    cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    cur.execute(sql.SQL("set local role {}").format(sql.Identifier(owner_role)))
    cur.execute("set local search_path = public")


def status(conn, migrations, owner_role="cse_owner"):
    """Read-only. The migration role is NOINHERIT, so it reads the ledger as the owner (SET LOCAL ROLE inside a
    read-only transaction that is rolled back); other roles (e.g. the postgres superuser in verify_server) read
    directly."""
    from psycopg2 import sql
    was = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute("select case when exists (select 1 from pg_roles where rolname = %s) "
                        "then pg_has_role(session_user, %s, 'SET') else false end", (owner_role, owner_role))
            if cur.fetchone()[0]:
                cur.execute(sql.SQL("set local role {}").format(sql.Identifier(owner_role)))
            rows = _ledger_rows(cur)
    finally:
        conn.rollback()
        conn.autocommit = was
    if rows is None:
        p = plan(migrations, [])
        d = p.to_dict()
        d["ledger"] = "missing"
        return d
    d = plan(migrations, rows).to_dict()
    d["ledger"] = "present"
    return d


def apply(conn, migrations, owner_role="cse_owner", target=None, dry_run=False, log=None, redact=None):
    log = log or (lambda msg: print(msg, file=sys.stderr))
    redact = redact or Redactor()
    conn.autocommit = False
    with conn.cursor() as cur:
        user = _preflight(cur, owner_role)
        _as_owner(cur, owner_role)                 # the migration role reads/writes the ledger only as the owner
        if not dry_run:
            cur.execute(BOOTSTRAP_SQL)
        rows = _ledger_rows(cur) or []
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    p = plan(migrations, rows)
    if p.problems():
        raise MigrationError("migration ledger check failed; nothing applied:\n  " + "\n  ".join(p.problems()))
    todo = [m for m in p.pending if target is None or m.version <= target]
    applied = []
    for m in todo:
        if dry_run:
            log(f"would apply {m.filename} sha256={m.sha256}")
            applied.append(m.filename)
            continue
        t0 = time.monotonic()
        try:
            with conn.cursor() as cur:
                _as_owner(cur, owner_role)
                cur.execute("select 1 from ops.schema_migrations where version = %s", (m.version,))
                if cur.fetchone():
                    raise MigrationError(f"{m.filename} was applied concurrently")
                cur.execute(m.sql())
                cur.execute("insert into ops.schema_migrations (version, filename, sha256, runner_version, duration_ms) "
                            "values (%s, %s, %s, %s, %s)",
                            (m.version, m.filename, m.sha256, RUNNER_VERSION, int((time.monotonic() - t0) * 1000)))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — the whole file is rolled back, then reported
            conn.rollback()
            raise MigrationError(f"{m.filename} failed and was rolled back: {redact(exc)}") from None
        log(f"applied {m.filename} as {owner_role} (session {user}) in {int((time.monotonic() - t0) * 1000)} ms")
        applied.append(m.filename)
    return {"applied": applied, "already_applied": [m.filename for m, _ in p.applied], "dry_run": dry_run}


def main(argv=None):
    ap = argparse.ArgumentParser(description="CSE migration runner (ledger + hash verification)")
    ap.add_argument("command", choices=["status", "verify", "apply"])
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--to", type=int, default=None, help="apply up to and including this version")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--user", default=None, help="PostgreSQL role (default: CSE_DB_USER or cse_migrator)")
    args = ap.parse_args(argv)
    s = ops_settings.load()
    try:
        migrations = discover(args.dir)
        conn = ops_settings.connect(s, user=args.user or s.db_user or "cse_migrator")
    except (MigrationError, ops_settings.SettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — connection failures
        print(f"error: cannot connect: {Redactor()(exc)}", file=sys.stderr)
        return 1
    try:
        if args.command in ("status", "verify"):
            d = status(conn, migrations, owner_role=s.owner_role)
            print(json.dumps(d, indent=2))
            if args.command == "verify":
                bad = d["problems"] or d["ledger"] == "missing" or d["pending"]
                return 1 if bad else 0
            return 0
        out = apply(conn, migrations, owner_role=s.owner_role, target=args.to, dry_run=args.dry_run)
        print(json.dumps(out, indent=2))
        return 0
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
