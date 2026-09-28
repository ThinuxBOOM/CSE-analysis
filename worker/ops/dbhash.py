"""
Database inventory for backup manifests and restore checks: per-table row count and CONTENT digest, trigger list,
migration ledger. Computed inside the same snapshot pg_dump uses, then recomputed on the restored copy; any lost,
added or altered row changes the digest.

Digest: every row is streamed with COPY ... TO STDOUT (text format, fixed session settings below) and the SHA-256 of
each row line is summed modulo 2^256. The sum is independent of row order (no ORDER BY, no collation dependence) and
of the cluster it is computed on, and it streams in constant memory.
"""
import hashlib

from psycopg2 import sql

# Output formats that must be identical on the source and on the restored copy.
SESSION_SETTINGS = (
    "set timezone = 'UTC'",
    "set datestyle = 'ISO, YMD'",
    "set intervalstyle = 'postgres'",
    "set extra_float_digits = 3",
    "set bytea_output = 'hex'",
    "set client_encoding = 'UTF8'",
)
SCHEMAS = ("public", "ops")
_MOD = 1 << 256


def apply_session_settings(cur):
    for s in SESSION_SETTINGS:
        cur.execute(s)


class _RowDigest:
    """File-like sink for copy_expert: hashes each complete line."""

    def __init__(self):
        self.acc, self.rows, self._buf = 0, 0, b""

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            self.acc = (self.acc + int.from_bytes(hashlib.sha256(line).digest(), "big")) % _MOD
            self.rows += 1
        return len(data)

    def result(self):
        if self._buf:                               # COPY text rows always end with \n; defensive
            self.write(b"\n")
        return self.rows, format(self.acc, "064x")


def list_tables(cur, schemas=SCHEMAS):
    cur.execute("select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind in ('r', 'p') and n.nspname = any(%s) order by 1, 2", (list(schemas),))
    return [(s, t) for s, t in cur.fetchall()]


def table_digest(cur, schema, table):
    sink = _RowDigest()
    cur.copy_expert(sql.SQL("copy {}.{} to stdout").format(sql.Identifier(schema), sql.Identifier(table))
                    .as_string(cur), sink)
    return sink.result()


def list_triggers(cur, schemas=SCHEMAS):
    cur.execute("select n.nspname || '.' || c.relname, t.tgname, t.tgenabled from pg_trigger t "
                "join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace "
                "where not t.tgisinternal and n.nspname = any(%s) order by 1, 2", (list(schemas),))
    return [[r, n, e] for r, n, e in cur.fetchall()]


def list_migrations(cur):
    cur.execute("select to_regclass('ops.schema_migrations')")
    if cur.fetchone()[0] is None:
        return []
    cur.execute("select version, filename, sha256 from ops.schema_migrations order by version")
    return [[v, f, h] for v, f, h in cur.fetchall()]


def inventory(cur, schemas=SCHEMAS):
    """Call with SESSION_SETTINGS applied, inside the snapshot to describe."""
    tables = {}
    for s, t in list_tables(cur, schemas):
        rows, digest = table_digest(cur, s, t)
        tables[f"{s}.{t}"] = {"rows": rows, "digest": digest}
    return {"tables": tables, "triggers": list_triggers(cur, schemas), "migrations": list_migrations(cur)}


def compare(expected, actual):
    """Problems (list of str) between two inventories."""
    problems = []
    et, at = expected.get("tables", {}), actual.get("tables", {})
    for name in sorted(set(et) | set(at)):
        if name not in at:
            problems.append(f"table {name} missing after restore")
        elif name not in et:
            problems.append(f"table {name} not in manifest")
        elif et[name] != at[name]:
            problems.append(f"table {name}: manifest rows={et[name]['rows']} digest={et[name]['digest'][:12]}… "
                            f"restored rows={at[name]['rows']} digest={at[name]['digest'][:12]}…")
    if expected.get("triggers") != actual.get("triggers"):
        problems.append("trigger inventory differs")
    if expected.get("migrations") != actual.get("migrations"):
        problems.append("migration ledger differs")
    return problems
