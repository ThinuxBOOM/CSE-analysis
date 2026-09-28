"""
Security / provisioning verification (P1). Read-only: it changes nothing.

    sudo -u postgres env PYTHONPATH=/opt/cse/app python3 -m worker.ops.verify_server --repo /opt/cse/app

Connects as the postgres superuser over the local socket (needed to read pg_hba_file_rules and data_directory) and
checks the cluster against the P1 model: PostgreSQL 17 with data checksums and durable settings, socket-only listener,
peer-only local authentication, the five cse_* roles and their attributes/memberships, ownership by the NOLOGIN owner,
nothing granted to PUBLIC, least privilege for the worker, append-only triggers, and a clean migration ledger. With
OS checks enabled it also checks the Ubuntu release, time synchronisation, and the backup layout (separate device,
ownership, modes, credentials not world-readable). Exit status 1 if any check FAILs.
"""
import argparse
import json
import os
import stat
import subprocess
import sys

from . import migrate as ops_migrate, settings as ops_settings

ROLES = {"cse_owner": False, "cse_migrator": True, "cse_worker": True, "cse_reader": False, "cse_backup": True}
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
        can_login, sup, crole, cdb, repl, bypass, _inherit = got[name]
        rep.add(f"role_{name}", can_login == login and not any((sup, crole, cdb, repl, bypass)),
                f"login={can_login} superuser={sup} createrole={crole} createdb={cdb} replication={repl} "
                f"bypassrls={bypass}")
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
                      "and not (r.rolname = 'pg_read_all_data' and u.rolname = 'cse_backup')", (list(ROLES),))[0]
    rep.add("no_unexpected_memberships", other is None, other or "none")
    rb = _one(cur, "select pg_has_role('cse_backup', 'pg_read_all_data', 'MEMBER')")[0]
    rep.add("backup_reads_all_data", rb, f"cse_backup member of pg_read_all_data={rb}")

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

    if migrations_dir:
        try:
            cur.connection.rollback()
            d = ops_migrate.status(cur.connection, ops_migrate.discover(migrations_dir))
            ok = d["ledger"] == "present" and not d["problems"] and not d["pending"]
            rep.add("migration_ledger_clean", ok, json.dumps({k: d[k] for k in ("pending", "problems")}))
        except Exception as exc:  # noqa: BLE001
            rep.add("migration_ledger_clean", False, str(exc))


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
