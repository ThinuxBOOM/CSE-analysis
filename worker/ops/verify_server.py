"""
Security / provisioning verification (P1). Read-only: it changes nothing.

    sudo -u postgres env PYTHONPATH=/opt/cse/app python3 -m worker.ops.verify_server --repo /opt/cse/app

Connects as the postgres superuser over the local socket (needed to read pg_hba_file_rules and data_directory) and
checks the cluster against the P1 model: PostgreSQL 17 with data checksums and durable settings, socket-only listener,
peer-only local authentication, the six cse_* roles and their attributes/memberships, ownership by the NOLOGIN owner,
nothing granted to PUBLIC, least privilege for the worker, append-only triggers, and a clean migration ledger. With
OS checks enabled it also checks the Ubuntu release, time synchronisation, and the backup layout (separate device,
ownership, modes, credentials not world-readable). Exit status 1 if any check FAILs.

Change control P1-CC-1 (2026-11-03; M8 OD-7 and OD-8; docs/ops/P1_PLATFORM.md) added:
- the reader login cse_verifier (docs/R_F4_IMPLEMENTATION.md section 20.6), the sixth role: LOGIN and INHERIT, no
  elevated attribute; exactly one membership, in cse_reader WITH INHERIT TRUE, SET FALSE and ADMIN false, which
  no_unexpected_memberships exempts by name; no CREATE or TEMPORARY on the database; no pg_db_role_setting row in any
  database;
- REG-X03, the migration ledger ops.schema_migrations: no role other than its owner cse_owner holds INSERT, UPDATE,
  DELETE or TRUNCATE on it, directly or through an inheriting membership (INSERT and UPDATE also as column grants),
  and PUBLIC holds none; it is an ordinary, permanent table, neither partitioned nor a partition, without row-level
  security (enabled or forced), and neither an inheritance child nor an inheritance parent.
Each violation is a FAIL whose detail names the offender.
"""
import argparse
import json
import os
import stat
import subprocess
import sys

from . import migrate as ops_migrate, settings as ops_settings

ROLES = {"cse_owner": False, "cse_migrator": True, "cse_worker": True, "cse_reader": False, "cse_backup": True,
         "cse_verifier": True}
VERIFIER, VERIFIER_GROUP = "cse_verifier", "cse_reader"         # P1-CC-1: the one login for R-F4's `verify`
LEDGER, LEDGER_OWNER = "ops.schema_migrations", "cse_owner"     # REG-X03
LEDGER_WRITES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
APPEND_ONLY = {
    "public.raw_market_observations", "public.raw_index_observations", "public.report_filing_observations",
    "public.report_document_classifications", "public.report_statement_periods", "public.report_classification_evidence",
    "public.issuers", "public.issuer_identifier_observations", "public.issuer_securities", "public.filing_issuer_links",
    "public.financial_concepts", "public.financial_extraction_runs", "public.financial_statement_extracts",
    "public.financial_statement_columns", "public.financial_statement_rows", "public.financial_fact_candidates",
    "ops.schema_migrations", "ops.backup_runs",
}
WORKER_NO_WRITE = sorted(APPEND_ONLY)


class Report:
    def __init__(self):
        self.items = []

    def add(self, check, ok, detail="", warn=False):
        self.items.append({"check": check, "status": "PASS" if ok else ("WARN" if warn else "FAIL"), "detail": detail})

    @property
    def failed(self):
        return [i for i in self.items if i["status"] == "FAIL"]


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def db_checks(cur, rep, expect_hba=("peer",), allow_listen=("",), migrations_dir=None):
    ver = int(_one(cur, "select current_setting('server_version_num')")[0])
    rep.add("postgresql_version_17", ver // 10000 == 17, f"server_version_num={ver}", warn=ver >= 150000)
    for name, want in (("data_checksums", "on"), ("fsync", "on"), ("full_page_writes", "on"),
                       ("synchronous_commit", "on")):
        v = _one(cur, "select current_setting(%s)", (name,))[0]
        rep.add(f"setting_{name}", v == want, f"{name}={v}")
    la = _one(cur, "select current_setting('listen_addresses')")[0]
    rep.add("socket_only_listener", la.strip() in allow_listen, f"listen_addresses={la!r}",
            warn=la.strip() == "localhost")
    try:
        cur.execute("select type, database, user_name, auth_method, error from pg_hba_file_rules")
        rules = cur.fetchall()
        bad = [r for r in rules if r[4] or r[0] != "local" or r[3] not in set(expect_hba) | {"reject"}]
        rep.add("pg_hba_local_peer_only", not bad and bool(rules),
                "ok" if not bad else f"non-local or non-{'/'.join(expect_hba)} rules: {bad}")
    except Exception as exc:  # noqa: BLE001 — needs superuser
        cur.connection.rollback()
        rep.add("pg_hba_local_peer_only", False, f"cannot read pg_hba_file_rules: {exc}")

    cur.execute("select rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls, "
                "rolinherit from pg_roles where rolname = any(%s)", (list(ROLES),))
    got = {r[0]: r[1:] for r in cur.fetchall()}
    for name, login in ROLES.items():
        if name not in got:
            rep.add(f"role_{name}", False, "missing")
            continue
        can_login, sup, crole, cdb, repl, bypass, inherit = got[name]
        ok = can_login == login and not any((sup, crole, cdb, repl, bypass))
        detail = (f"login={can_login} superuser={sup} createrole={crole} createdb={cdb} replication={repl} "
                  f"bypassrls={bypass}")
        if name == VERIFIER:                                     # P1-CC-1: the login also has the INHERIT attribute
            ok = ok and bool(inherit)
            detail += f" inherit={inherit}"
        rep.add(f"role_{name}", ok, detail)
    cur.execute("select rolname from pg_roles where rolsuper order by rolname")
    supers = [r[0] for r in cur.fetchall()]
    rep.add("only_bootstrap_superuser", supers == ["postgres"], f"superusers={supers}",
            warn=len(supers) == 1)
    m = _one(cur, "select m.inherit_option, m.set_option from pg_auth_members m join pg_roles r on r.oid = m.roleid "
                  "join pg_roles u on u.oid = m.member where r.rolname = 'cse_owner' and u.rolname = 'cse_migrator'")
    rep.add("migrator_set_role_owner_only", m == (False, True), f"(inherit, set)={m}")
    other = _one(cur, "select string_agg(u.rolname || '->' || r.rolname, ', ') from pg_auth_members m "
                      "join pg_roles r on r.oid = m.roleid join pg_roles u on u.oid = m.member "
                      "where u.rolname = any(%s) and not (r.rolname = 'cse_owner' and u.rolname = 'cse_migrator') "
                      "and not (r.rolname = 'pg_read_all_data' and u.rolname = 'cse_backup') "
                      "and not (r.rolname = 'cse_reader' and u.rolname = 'cse_verifier')", (list(ROLES),))[0]
    rep.add("no_unexpected_memberships", other is None, other or "none")
    rb = _one(cur, "select pg_has_role('cse_backup', 'pg_read_all_data', 'MEMBER')")[0]
    rep.add("backup_reads_all_data", rb, f"cse_backup member of pg_read_all_data={rb}")
    verifier_checks(cur, rep)

    owner = _one(cur, "select r.rolname from pg_database d join pg_roles r on r.oid = d.datdba "
                      "where d.datname = current_database()")[0]
    rep.add("database_owner", owner == "cse_owner", f"owner={owner}")
    wrong = _one(cur, "select string_agg(n.nspname || '.' || c.relname || '=' || r.rolname, ', ') from pg_class c "
                      "join pg_namespace n on n.oid = c.relnamespace join pg_roles r on r.oid = c.relowner "
                      "where n.nspname in ('public', 'ops') and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f') "
                      "and r.rolname <> 'cse_owner'")[0]
    rep.add("objects_owned_by_nologin_owner", wrong is None, wrong or "all relations owned by cse_owner")
    # extension members are excluded: PostgreSQL installs a TRUSTED extension's objects (0001's pgcrypto) as the
    # bootstrap superuser even when cse_owner runs CREATE EXTENSION
    wrongf = _one(cur, "select string_agg(n.nspname || '.' || p.proname, ', ') from pg_proc p "
                       "join pg_namespace n on n.oid = p.pronamespace join pg_roles r on r.oid = p.proowner "
                       "where n.nspname in ('public', 'ops') and r.rolname <> 'cse_owner' and not exists ("
                       "select 1 from pg_depend d where d.classid = 'pg_proc'::regclass and d.objid = p.oid "
                       "and d.deptype = 'e')")[0]
    rep.add("functions_owned_by_nologin_owner", wrongf is None, wrongf or "ok (extension members excluded)")

    pub_db = _one(cur, "select exists (select 1 from pg_database d, aclexplode(coalesce(d.datacl, "
                       "acldefault('d', d.datdba))) a where d.datname = current_database() and a.grantee = 0)")[0]
    rep.add("public_no_database_privileges", not pub_db, f"PUBLIC has database privileges={pub_db}")
    pub_ns = _one(cur, "select string_agg(n.nspname, ', ') from pg_namespace n, aclexplode(coalesce(n.nspacl, "
                       "acldefault('n', n.nspowner))) a where n.nspname in ('public', 'ops') and a.grantee = 0")[0]
    rep.add("public_no_schema_privileges", pub_ns is None, pub_ns or "none")
    pub_tab = _one(cur, "select string_agg(distinct n.nspname || '.' || c.relname, ', ') from pg_class c "
                        "join pg_namespace n on n.oid = c.relnamespace, aclexplode(coalesce(c.relacl, "
                        "acldefault('r', c.relowner))) a where n.nspname in ('public', 'ops') and a.grantee = 0")[0]
    rep.add("public_no_table_privileges", pub_tab is None, pub_tab or "none")

    worker_bad = []
    for t in WORKER_NO_WRITE:
        if _one(cur, "select to_regclass(%s)", (t,))[0] is None:
            continue
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            if _one(cur, "select has_table_privilege('cse_worker', %s, %s)", (t, priv))[0]:
                worker_bad.append(f"{priv} {t}")
    rep.add("worker_cannot_modify_source_evidence", not worker_bad, ", ".join(worker_bad) or "ok")
    wc = _one(cur, "select has_schema_privilege('cse_worker', 'public', 'CREATE') "
                   "or has_schema_privilege('cse_worker', 'ops', 'USAGE')")[0]
    rep.add("worker_no_create_no_ops", not wc, f"create-on-public or usage-on-ops={wc}")
    trunc = _one(cur, "select string_agg(distinct r.rolname || ':' || n.nspname || '.' || c.relname, ', ') "
                      "from pg_class c join pg_namespace n on n.oid = c.relnamespace, "
                      "aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                      "join pg_roles r on r.oid = a.grantee where n.nspname in ('public', 'ops') "
                      "and a.privilege_type in ('TRUNCATE', 'DELETE') and r.rolname <> 'cse_owner'")[0]
    rep.add("no_delete_or_truncate_grants", trunc is None, trunc or "only the owner")

    cur.execute("select n.nspname || '.' || c.relname, t.tgname, t.tgenabled, t.tgtype "
                "from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace "
                "where not t.tgisinternal")
    trig = {}
    for rel, name, enabled, tgtype in cur.fetchall():
        trig.setdefault(rel, []).append((name, enabled, tgtype))
    missing = []
    for rel in sorted(APPEND_ONLY):
        if _one(cur, "select to_regclass(%s)", (rel,))[0] is None:
            missing.append(f"{rel} (table missing)")
            continue
        ts = [t for t in trig.get(rel, []) if t[1] == "O"]
        row_ud = any(t[2] & 1 and t[2] & (8 | 16) for t in ts)      # ROW + (DELETE | UPDATE)
        stmt_trunc = any(t[2] & 32 for t in ts)                      # TRUNCATE
        if not (row_ud and stmt_trunc):
            missing.append(rel)
    rep.add("append_only_triggers_enabled", not missing, ", ".join(missing) or f"{len(APPEND_ONLY)} tables protected")
    ledger_checks(cur, rep)

    if migrations_dir:
        try:
            cur.connection.rollback()
            d = ops_migrate.status(cur.connection, ops_migrate.discover(migrations_dir))
            ok = d["ledger"] == "present" and not d["problems"] and not d["pending"]
            rep.add("migration_ledger_clean", ok, json.dumps({k: d[k] for k in ("pending", "problems")}))
        except Exception as exc:  # noqa: BLE001
            rep.add("migration_ledger_clean", False, str(exc))


VERIFIER_CHECKS = ("verifier_membership_reader_only", "verifier_no_database_create_or_temporary",
                   "verifier_no_role_settings")


def verifier_checks(cur, rep):
    """P1-CC-1: the reader login cse_verifier (docs/R_F4_IMPLEMENTATION.md section 20.6). Its attributes are checked
    with the other roles (ROLES). Here: its one membership, its database privileges and its role settings. A missing
    login FAILs every check without querying it (has_database_privilege raises for an unknown role)."""
    if not _one(cur, "select exists (select 1 from pg_roles where rolname = %s)", (VERIFIER,))[0]:
        for check in VERIFIER_CHECKS:
            rep.add(check, False, f"{VERIFIER} missing")
        return
    # exactly one membership row: cse_reader with (inherit, set, admin) = (true, false, false), whoever granted it
    cur.execute("select r.rolname, m.inherit_option, m.set_option, m.admin_option, g.rolname from pg_auth_members m "
                "join pg_roles r on r.oid = m.roleid join pg_roles u on u.oid = m.member "
                "join pg_roles g on g.oid = m.grantor where u.rolname = %s order by 1, 5", (VERIFIER,))
    rows = cur.fetchall()
    rep.add("verifier_membership_reader_only", [tuple(r[:4]) for r in rows] == [(VERIFIER_GROUP, True, False, False)],
            "; ".join(f"{VERIFIER}->{r[0]} (inherit={r[1]} set={r[2]} admin={r[3]} grantor={r[4]})" for r in rows)
            or "no membership")
    # privileges held directly or through an inheriting membership (cse_reader's, PUBLIC's)
    create, temp = _one(cur, "select has_database_privilege(%s, current_database(), 'CREATE'), "
                             "has_database_privilege(%s, current_database(), 'TEMPORARY')", (VERIFIER, VERIFIER))
    rep.add("verifier_no_database_create_or_temporary", not (create or temp),
            f"{VERIFIER} on the current database: CREATE={create} TEMPORARY={temp}")
    # any database, including setdatabase = 0 (ALTER ROLE ... SET without IN DATABASE)
    settings = _one(cur, "select string_agg(coalesce(d.datname, '(all databases)') || ': ' "
                         "|| array_to_string(s.setconfig, ', '), '; ' order by d.datname) from pg_db_role_setting s "
                         "left join pg_database d on d.oid = s.setdatabase "
                         "where s.setrole = (select oid from pg_roles where rolname = %s)", (VERIFIER,))[0]
    rep.add("verifier_no_role_settings", settings is None, f"{VERIFIER} settings: {settings}" if settings else "none")


LEDGER_CHECKS = ("ledger_writable_only_by_owner", "ledger_ordinary_table", "ledger_permanent",
                 "ledger_no_row_level_security", "ledger_no_inheritance")


def ledger_checks(cur, rep):
    """REG-X03 (M8 OD-8 = a): who may write the migration ledger, and its table shape.

    Write privileges: every role except the ledger's owner cse_owner, the superusers and the predefined pg_* roles is
    tested with has_table_privilege (DELETE, TRUNCATE) and has_any_column_privilege (INSERT, UPDATE, which may also be
    granted per column). Both count privileges held directly, through PUBLIC, and through memberships WITH INHERIT.
    - Superusers hold every privilege; they are checked by only_bootstrap_superuser (postgres alone).
    - The predefined roles cannot log in; pg_write_all_data would always report INSERT/UPDATE/DELETE. A role that
      inherits one of them, or any grant made to one of them, is reported through that role.
    - cse_migrator is a member of cse_owner WITH INHERIT FALSE, SET TRUE. It holds no privilege of the owner's, so it
      is reported only if it gains a write privilege of its own or through an inheriting membership. Its SET ROLE route
      to cse_owner is the designed owner path: the migration runner writes the ledger as cse_owner inside each
      migration transaction. That route is checked by migrator_set_role_owner_only, not exempted here by name.
    PUBLIC's own grants (table and column ACLs) are named separately as PUBLIC.

    Shape: an ordinary table (relkind 'r'), not a partition (relispartition) and so not partitioned; permanent
    (relpersistence 'p'); no row-level security, enabled or forced; no pg_inherits row naming it as child or parent."""
    oid = _one(cur, "select to_regclass(%s)::oid", (LEDGER,))[0]
    if oid is None:
        for check in LEDGER_CHECKS:
            rep.add(check, False, f"{LEDGER} missing")
        return
    roles = _one(cur, "select string_agg(r.rolname || ':' || p.priv, ', ' order by r.rolname, p.ord) "
                      "from pg_roles r cross join unnest(%s::text[]) with ordinality as p(priv, ord) "
                      "where r.rolname <> %s and not r.rolsuper and r.rolname !~ '^pg_' and case "
                      "when p.priv in ('INSERT', 'UPDATE') then has_any_column_privilege(r.oid, %s::oid, p.priv) "
                      "else has_table_privilege(r.oid, %s::oid, p.priv) end",
                 (list(LEDGER_WRITES), LEDGER_OWNER, oid, oid))[0]
    public = _one(cur, "select string_agg(distinct x, ', ') from ("
                       "select 'PUBLIC:' || a.privilege_type as x from pg_class c, "
                       "aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                       "where c.oid = %s and a.grantee = 0 and a.privilege_type = any(%s) "
                       "union all select 'PUBLIC:' || a.privilege_type || '(' || t.attname || ')' "
                       "from pg_attribute t, aclexplode(t.attacl) a "
                       "where t.attrelid = %s and t.attacl is not null and a.grantee = 0 "
                       "and a.privilege_type = any(%s)) q",
                  (oid, list(LEDGER_WRITES), oid, list(LEDGER_WRITES)))[0]
    offenders = ", ".join(x for x in (public, roles) if x)
    rep.add("ledger_writable_only_by_owner", not offenders,
            f"{LEDGER} writable by: {offenders}" if offenders else f"only {LEDGER_OWNER} (and superusers)")
    kind, persistence, rls, force, is_partition = _one(
        cur, "select relkind, relpersistence, relrowsecurity, relforcerowsecurity, relispartition from pg_class "
             "where oid = %s", (oid,))
    rep.add("ledger_ordinary_table", kind == "r" and not is_partition,
            f"{LEDGER} relkind={kind!r} relispartition={is_partition}")
    rep.add("ledger_permanent", persistence == "p", f"{LEDGER} relpersistence={persistence!r}")
    rep.add("ledger_no_row_level_security", not rls and not force,
            f"{LEDGER} relrowsecurity={rls} relforcerowsecurity={force}")
    inh = _one(cur, "select string_agg(x, ', ' order by x) from ("
                    "select 'child ' || i.inhrelid::regclass::text as x from pg_inherits i where i.inhparent = %s "
                    "union all select 'parent ' || i.inhparent::regclass::text from pg_inherits i "
                    "where i.inhrelid = %s) q", (oid, oid))[0]
    rep.add("ledger_no_inheritance", inh is None,
            f"{LEDGER} inheritance: {inh}" if inh else "no inheritance parent or child")


def _owner_mode(path):
    import grp
    import pwd
    st = os.stat(path)
    try:
        user = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        user = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return user, group, stat.S_IMODE(st.st_mode), st.st_dev


def os_checks(rep, s, data_directory, allow_same_device=False):
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            osr = dict(l.strip().split("=", 1) for l in f if "=" in l)
        name = f"{osr.get('ID', '').strip(chr(34))} {osr.get('VERSION_ID', '').strip(chr(34))}"
        rep.add("os_ubuntu_24_04", name == "ubuntu 24.04", name, warn=True)
    except OSError:
        rep.add("os_ubuntu_24_04", False, "no /etc/os-release", warn=True)
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True,
                           timeout=10)
        rep.add("time_synchronised", r.stdout.strip() == "yes", f"NTPSynchronized={r.stdout.strip() or r.stderr.strip()}",
                warn=r.returncode != 0)
    except (OSError, subprocess.SubprocessError) as exc:
        rep.add("time_synchronised", False, f"timedatectl unavailable: {exc}", warn=True)
    paths = ops_settings.backup_paths(s)
    root = paths["root"]
    if not os.path.isdir(root):
        rep.add("backup_root_exists", False, root)
        return
    rep.add("backup_root_exists", True, root)
    if data_directory and os.path.exists(data_directory):
        same = os.stat(root).st_dev == os.stat(data_directory).st_dev
        rep.add("backup_on_separate_device", not same, f"backup root and PGDATA on the same device={same}",
                warn=same and allow_same_device)
    expectations = ((paths["dumps"], "cse-backup", None, 0o750), (paths["staging"], "cse-backup", None, 0o750),
                    (paths["status"], "cse-backup", None, 0o750), (paths["spool"], "cse-worker", "cse-backup", 0o2750),
                    (paths["restore_scratch"], "cse-backup", None, 0o700))
    for p, user, group, mode in expectations:
        try:
            if not stat.S_ISDIR(os.stat(p).st_mode):
                raise FileNotFoundError
        except FileNotFoundError:
            rep.add(f"layout_{os.path.basename(p)}", False, f"{p} missing")
            continue
        except PermissionError:
            rep.add(f"layout_{os.path.basename(p)}", False, f"{p} cannot be inspected as this OS user: run the OS "
                    "checks as root (--os-only)")
            continue
        u, g, m, _ = _owner_mode(p)
        ok = u == user and (group is None or g == group) and m == mode
        rep.add(f"layout_{os.path.basename(p)}", ok, f"{p} {u}:{g} {oct(m)} (expected {user}:{group or '*'} {oct(mode)})")
    cred = "/etc/cse/credentials"
    if os.path.isdir(cred):
        leaks = []
        for dirpath, _, files in os.walk(cred):
            for n in files + [""]:
                p = os.path.join(dirpath, n) if n else dirpath
                if stat.S_IMODE(os.stat(p).st_mode) & 0o007:
                    leaks.append(p)
        rep.add("credentials_not_world_accessible", not leaks, ", ".join(leaks) or "ok")
    else:
        rep.add("credentials_not_world_accessible", True, f"{cred} not present (off-site not configured yet)", warn=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify the P1 server / PostgreSQL security model (read-only)")
    ap.add_argument("--repo", default=None, help="repository checkout whose migrations the ledger must match")
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--skip-os", action="store_true", help="database checks only (run as the postgres OS user)")
    ap.add_argument("--os-only", action="store_true", help="host/filesystem checks only (run as root)")
    ap.add_argument("--pgdata", default="/var/lib/postgresql/17/main", help="PGDATA for the separate-device check")
    ap.add_argument("--allow-same-device", action="store_true", help="test hosts only")
    ap.add_argument("--expect-hba", default="peer", help="comma-separated allowed local auth methods")
    args = ap.parse_args(argv)
    s = ops_settings.load()
    rep = Report()
    data_dir = args.pgdata
    if not args.os_only:
        conn = ops_settings.connect(s, user=args.user)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                mdir = os.path.join(args.repo, "supabase", "migrations") if args.repo else None
                db_checks(cur, rep, expect_hba=tuple(args.expect_hba.split(",")), migrations_dir=mdir)
                try:
                    data_dir = _one(cur, "select current_setting('data_directory')")[0]
                except Exception:  # noqa: BLE001
                    pass
        finally:
            conn.close()
    if not args.skip_os:
        os_checks(rep, s, data_dir, allow_same_device=args.allow_same_device)
    print(json.dumps({"checks": rep.items, "failed": len(rep.failed)}, indent=2))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
