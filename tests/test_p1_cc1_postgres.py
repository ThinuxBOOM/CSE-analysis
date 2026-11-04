"""
Change control P1-CC-1 (2026-11-03; M8 OD-7 = A, M8 OD-8 = a) against REAL PostgreSQL 17, on throwaway clusters made
by initdb (worker/ops/ephemeral_pg.py), exactly as tests/test_p1_postgres.py does: no existing server, no network, no
CSE. Trust stands in for peer authentication (the test OS user is not mapped), so these tests show what the login is
and may do once connected; peer authentication through pg_ident is shown only at staging (docs/R_F4_IMPLEMENTATION.md
section 20.6, steps 1-6, not part of this change).

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p1_cc1_postgres.py

    g1  a cluster bootstrapped by ops/provision/sql/bootstrap_cluster.sql has cse_verifier: LOGIN, INHERIT, none of the
        elevated attributes, no password, exactly one membership (cse_reader; inherit true, set false, admin false),
        no role setting, no database privilege of its own; CONNECT (through cse_reader) but no CREATE or TEMPORARY
    g2  re-running the bootstrap succeeds and re-enforces the attributes and the membership's options after drift
    g3  verify_server passes every database check on the provisioned, migrated cluster (and its command line, with
        --skip-os, exits 0)
    g4  a session as cse_verifier: current_user = session_user = cse_verifier with no SET ROLE; it cannot SET ROLE
        cse_reader; it reads ops and R-F4 tables; its writes (public, R-F4, the ledger, CREATE, TEMP) are refused;
        the runbook's DATABASE_URL form (role named, socket directory as host) connects as the login
    g5  verify_server FAILs, naming the offender, for each negative case of acceptance criterion 3: an elevated
        attribute, an extra membership, INHERIT false, SET true, a role setting, CREATE / TEMPORARY on the database,
        INSERT / UPDATE / DELETE / TRUNCATE on ops.schema_migrations for cse_reader, cse_backup, cse_verifier or
        PUBLIC, an inheritance child (and parent), row-level security, an UNLOGGED ledger, a partitioned ledger
        (recreated in the transaction), the ledger as a partition, and the ledger replaced by a view. Each case is made
        by the superuser inside a transaction that is rolled back.
"""
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
RF4_TABLES = ("financial_f4_extractions", "financial_f4_statements", "financial_f4_columns", "financial_f4_rows",
              "financial_f4_cells")
NEW_CHECKS = ("role_cse_verifier", "verifier_membership_reader_only", "verifier_no_database_create_or_temporary",
              "verifier_no_role_settings", "ledger_writable_only_by_owner", "ledger_ordinary_table", "ledger_permanent",
              "ledger_no_row_level_security", "ledger_no_inheritance")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cluster(tmp_path_factory, name):
    from worker.ops.ephemeral_pg import EphemeralCluster
    base = tmp_path_factory.mktemp(name)
    ec = EphemeralCluster(BINDIR, base_dir=str(base), port=free_port(), durable=True, superuser="postgres").start()
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    r = ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)
    assert r.returncode == 0
    return ec


@pytest.fixture(scope="module")
def boot(tmp_path_factory):
    """Bootstrapped only: the bootstrap's own tests (g1, g2) may disturb it."""
    ec = make_cluster(tmp_path_factory, "p1cc1_boot")
    yield ec
    ec.cleanup()


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    """Bootstrapped and migrated through the runner (0001 onwards), as P1 provisions it."""
    from worker.ops import migrate as mig
    ec = make_cluster(tmp_path_factory, "p1cc1_migrated")
    c = ec.connect(dbname="cse", user="cse_migrator")
    mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()
    yield ec
    ec.cleanup()


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None


def su(ec, autocommit=True):
    c = ec.connect(dbname="cse", user="postgres")
    c.autocommit = autocommit
    return c


def login_state(c):
    return {
        "attrs": q(c, "select rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
                      "rolbypassrls, rolpassword is null from pg_authid where rolname = 'cse_verifier'"),
        "members": q(c, "select r.rolname, m.inherit_option, m.set_option, m.admin_option from pg_auth_members m "
                        "join pg_roles r on r.oid = m.roleid join pg_roles u on u.oid = m.member "
                        "where u.rolname = 'cse_verifier' order by 1"),
        "member_of_verifier": q(c, "select count(*) from pg_auth_members m join pg_roles r on r.oid = m.roleid "
                                   "where r.rolname = 'cse_verifier'"),
        "settings": q(c, "select count(*) from pg_db_role_setting where setrole = 'cse_verifier'::regrole"),
        "db_privs": q(c, "select has_database_privilege('cse_verifier', 'cse', 'CONNECT'), "
                         "has_database_privilege('cse_verifier', 'cse', 'CREATE'), "
                         "has_database_privilege('cse_verifier', 'cse', 'TEMPORARY')"),
        "own_acl": q(c, "select count(*) from pg_database d, aclexplode(d.datacl) a "
                        "where a.grantee = 'cse_verifier'::regrole"),
    }


DECIDED = {"attrs": [(True, True, False, False, False, False, False, True)],
           "members": [("cse_reader", True, False, False)], "member_of_verifier": [(0,)], "settings": [(0,)],
           "db_privs": [(True, False, False)], "own_acl": [(0,)]}


# ------------------------------------------------------------------------------------------------ g1, g2

def test_g1_the_bootstrap_creates_the_login_as_decided(boot):
    c = su(boot)
    assert login_state(c) == DECIDED
    assert q(c, "select rolname from pg_roles where rolname like 'cse%%' order by 1") == \
        [("cse_backup",), ("cse_migrator",), ("cse_owner",), ("cse_reader",), ("cse_verifier",), ("cse_worker",)]
    c.close()


def test_g2_rerunning_the_bootstrap_is_idempotent_and_reenforces(boot):
    c = su(boot)
    r = boot.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)       # plain re-run
    assert r.returncode == 0 and login_state(c) == DECIDED
    # drift: attributes, a password, and the membership's options (same grantor: the bootstrap superuser)
    q(c, "alter role cse_verifier nologin noinherit createdb createrole replication bypassrls password 'drift'")
    q(c, "grant cse_reader to cse_verifier with admin true, inherit false, set true")
    drifted = login_state(c)
    assert drifted["attrs"] == [(False, False, False, True, True, True, True, False)]
    assert drifted["members"] == [("cse_reader", False, True, True)]
    r = boot.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)
    assert r.returncode == 0, r.stderr
    assert login_state(c) == DECIDED
    # a lost membership is granted again
    q(c, "revoke cse_reader from cse_verifier")
    assert login_state(c)["members"] == []
    assert boot.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP).returncode == 0
    assert login_state(c) == DECIDED
    c.close()


# ------------------------------------------------------------------------------------------------ g3

def run_checks(cur):
    from worker.ops import verify_server as vs
    rep = vs.Report()
    vs.db_checks(cur, rep, expect_hba=("trust",))
    return {i["check"]: i for i in rep.items}


def test_g3_verify_server_passes_on_the_provisioned_cluster(pg, monkeypatch, capsys):
    from worker.ops import verify_server as vs
    c = su(pg)
    rep = vs.Report()
    with c.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=MIGDIR)
    c.close()
    items = {i["check"]: i for i in rep.items}
    assert not [i for i in rep.items if i["status"] != "PASS"], rep.items
    for check in NEW_CHECKS:
        assert items[check]["status"] == "PASS", check
    assert items["ledger_writable_only_by_owner"]["detail"] == "only cse_owner (and superusers)"
    # the command line, database checks only (the OS checks need a provisioned host: staging, not this container)
    for k, v in {"CSE_DB_HOST": pg.sockdir, "CSE_DB_PORT": str(pg.port), "CSE_DB_NAME": "cse"}.items():
        monkeypatch.setenv(k, v)
    capsys.readouterr()
    assert vs.main(["--skip-os", "--repo", REPO, "--expect-hba", "trust", "--user", "postgres"]) == 0
    assert '"failed": 0' in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ g4

def test_g4_a_session_as_the_login(pg):
    import psycopg2
    from worker.ops import migrate as mig
    c = pg.connect(dbname="cse", user="cse_verifier")
    c.autocommit = True
    assert q(c, "select current_user, session_user, current_setting('role'), "
                "pg_has_role(current_user, 'cse_reader', 'USAGE')") == [("cse_verifier", "cse_verifier", "none", True)]
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(c, "set role cse_reader")                                              # SET FALSE
    # reads: the ledger, the ops backup ledger, a P1 table and every R-F4 table
    assert q(c, "select count(*) from ops.schema_migrations")[0][0] == len(mig.discover(MIGDIR))
    q(c, "select count(*) from ops.backup_runs")
    q(c, "select count(*) from public.raw_market_observations")
    for t in RF4_TABLES:
        assert q(c, f"select count(*) from public.{t}") == [(0,)], t
    # writes are refused
    for sql in ("insert into public.raw_market_observations default values",
                "insert into public.financial_f4_extractions default values",
                "insert into ops.schema_migrations (version, filename, sha256, runner_version) "
                "values (9999, '9999_forged.sql', repeat('0', 64), 'forged')",
                "update ops.schema_migrations set runner_version = runner_version",
                "delete from ops.schema_migrations",
                "truncate ops.schema_migrations",
                "create table public.verifier_probe (x int)",
                "create schema verifier_probe",
                "create temporary table verifier_probe (x int)"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(c, sql)
    assert q(c, "select current_user, session_user") == [("cse_verifier", "cse_verifier")]
    c.close()
    # the runbook's DATABASE_URL form (docs/ops/PHASE2_BACKFILL.md section 12): the role named, the socket directory
    url = f"postgresql://cse_verifier@/cse?host={pg.sockdir}&port={pg.port}"
    c = psycopg2.connect(url)
    try:
        assert q(c, "select current_user, session_user, current_database()") == \
            [("cse_verifier", "cse_verifier", "cse")]
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ g5

LEDGER_GRANTS = [(g, p) for g in ("cse_reader", "cse_backup", "cse_verifier", "PUBLIC")
                 for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")]

NEGATIVE = [
    # the login's attributes
    *[(f"attr-{a}", [f"alter role cse_verifier {a}"], "role_cse_verifier", f"{d}=True")
      for a, d in (("createdb", "createdb"), ("createrole", "createrole"), ("replication", "replication"),
                   ("bypassrls", "bypassrls"), ("superuser", "superuser"))],
    ("attr-nologin", ["alter role cse_verifier nologin"], "role_cse_verifier", "login=False"),
    ("attr-noinherit", ["alter role cse_verifier noinherit"], "role_cse_verifier", "inherit=False"),
    # memberships
    ("extra-membership", ["grant cse_worker to cse_verifier"], "verifier_membership_reader_only",
     "cse_verifier->cse_worker"),
    ("extra-membership-listed", ["grant cse_worker to cse_verifier"], "no_unexpected_memberships",
     "cse_verifier->cse_worker"),
    ("extra-predefined", ["grant pg_read_all_data to cse_verifier"], "verifier_membership_reader_only",
     "cse_verifier->pg_read_all_data"),
    ("inherit-false", ["grant cse_reader to cse_verifier with inherit false"], "verifier_membership_reader_only",
     "inherit=False"),
    ("set-true", ["grant cse_reader to cse_verifier with set true"], "verifier_membership_reader_only", "set=True"),
    ("admin-true", ["grant cse_reader to cse_verifier with admin true"], "verifier_membership_reader_only",
     "admin=True"),
    ("no-membership", ["revoke cse_reader from cse_verifier"], "verifier_membership_reader_only", "no membership"),
    # role settings
    ("setting-all-databases", ["alter role cse_verifier set search_path = public"], "verifier_no_role_settings",
     "(all databases): search_path=public"),
    ("setting-in-cse", ["alter role cse_verifier in database cse set work_mem = '64MB'"], "verifier_no_role_settings",
     "cse: work_mem=64MB"),
    ("setting-other-database", ["alter role cse_verifier in database postgres set statement_timeout = 0"],
     "verifier_no_role_settings", "postgres: statement_timeout=0"),
    # database privileges, own or through cse_reader
    ("db-create", ["grant create on database cse to cse_verifier"], "verifier_no_database_create_or_temporary",
     "CREATE=True"),
    ("db-temporary", ["grant temporary on database cse to cse_verifier"], "verifier_no_database_create_or_temporary",
     "TEMPORARY=True"),
    ("db-create-via-reader", ["grant create on database cse to cse_reader"], "verifier_no_database_create_or_temporary",
     "CREATE=True"),
    # REG-X03: write privileges on the ledger
    *[(f"ledger-{g}-{p}".lower(), [f"grant {p} on ops.schema_migrations to {g}"], "ledger_writable_only_by_owner",
       f"{g}:{p}") for g, p in LEDGER_GRANTS],
    ("ledger-reader-inherited-by-verifier", ["grant insert on ops.schema_migrations to cse_reader"],
     "ledger_writable_only_by_owner", "cse_verifier:INSERT"),
    ("ledger-column-insert", ["grant insert (version, filename, sha256, runner_version) on ops.schema_migrations "
                              "to cse_backup"], "ledger_writable_only_by_owner", "cse_backup:INSERT"),
    ("ledger-column-insert-public", ["grant insert (version) on ops.schema_migrations to public"],
     "ledger_writable_only_by_owner", "PUBLIC:INSERT(version)"),
    ("ledger-migrator-direct", ["grant insert on ops.schema_migrations to cse_migrator"],
     "ledger_writable_only_by_owner", "cse_migrator:INSERT"),
    ("ledger-worker", ["grant insert on ops.schema_migrations to cse_worker"], "ledger_writable_only_by_owner",
     "cse_worker:INSERT"),
    ("ledger-via-pg-write-all-data", ["grant pg_write_all_data to cse_backup"], "ledger_writable_only_by_owner",
     "cse_backup:INSERT"),
    ("ledger-other-login", ["create role p1cc1_intruder login", "grant insert on ops.schema_migrations to "
                            "p1cc1_intruder"], "ledger_writable_only_by_owner", "p1cc1_intruder:INSERT"),
    ("ledger-inheriting-owner", ["create role p1cc1_heir login", "grant cse_owner to p1cc1_heir with inherit true"],
     "ledger_writable_only_by_owner", "p1cc1_heir:INSERT"),
    # REG-X03: the ledger's shape
    ("inheritance-child", ["create table ops.ledger_child () inherits (ops.schema_migrations)"],
     "ledger_no_inheritance", "child ops.ledger_child"),
    ("inheritance-parent", ["create table ops.ledger_parent (like ops.schema_migrations)",
                            "alter table ops.schema_migrations inherit ops.ledger_parent"],
     "ledger_no_inheritance", "parent ops.ledger_parent"),
    ("rls-enabled", ["alter table ops.schema_migrations enable row level security"], "ledger_no_row_level_security",
     "relrowsecurity=True"),
    ("rls-forced", ["alter table ops.schema_migrations force row level security"], "ledger_no_row_level_security",
     "relforcerowsecurity=True"),
    ("unlogged", ["alter table ops.schema_migrations set unlogged"], "ledger_permanent", "relpersistence='u'"),
    ("partitioned-recreated", ["alter table ops.schema_migrations rename to schema_migrations_orig",
                               "create table ops.schema_migrations (like ops.schema_migrations_orig) "
                               "partition by range (version)"], "ledger_ordinary_table", "relkind='p'"),
    ("a-partition", ["create table ops.ledger_root (like ops.schema_migrations) partition by range (version)",
                     "alter table ops.ledger_root attach partition ops.schema_migrations "
                     "for values from (minvalue) to (maxvalue)"], "ledger_ordinary_table", "relispartition=True"),
    ("a-partition-listed", ["create table ops.ledger_root (like ops.schema_migrations) partition by range (version)",
                            "alter table ops.ledger_root attach partition ops.schema_migrations "
                            "for values from (minvalue) to (maxvalue)"], "ledger_no_inheritance",
     "parent ops.ledger_root"),
    ("replaced-by-view", ["alter table ops.schema_migrations rename to schema_migrations_orig",
                          "create view ops.schema_migrations as select * from ops.schema_migrations_orig"],
     "ledger_ordinary_table", "relkind='v'"),
]


def test_g5_the_cases_are_distinct():
    assert len({n for n, *_ in NEGATIVE}) == len(NEGATIVE)


@pytest.mark.parametrize("name, statements, check, needle", NEGATIVE, ids=[n for n, *_ in NEGATIVE])
def test_g5_verify_server_fails_and_names_the_offender(pg, name, statements, check, needle):
    c = su(pg, autocommit=False)
    try:
        with c.cursor() as cur:
            before = run_checks(cur)
            assert not [k for k, i in before.items() if i["status"] != "PASS"], before
            for sql in statements:
                cur.execute(sql)
            after = run_checks(cur)
    finally:
        c.rollback()
        c.close()
    assert after[check]["status"] == "FAIL", after[check]
    assert needle in after[check]["detail"], after[check]


def test_g5_after_the_cases_the_cluster_passes_again(pg):
    c = su(pg, autocommit=False)
    try:
        with c.cursor() as cur:
            items = run_checks(cur)
    finally:
        c.rollback()
        c.close()
    assert not [k for k, i in items.items() if i["status"] != "PASS"], items
