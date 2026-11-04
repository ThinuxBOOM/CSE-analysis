"""
Change control P1-CC-1 (2026-11-03; M8 OD-7 = A, M8 OD-8 = a): unit tests of worker/ops/verify_server.py's new checks,
with a scripted cursor (no database; runs anywhere). The same checks run against real PostgreSQL 17 in
tests/test_p1_cc1_postgres.py.

    u1  ROLES holds the five P1 roles unchanged and the login cse_verifier (LOGIN)
    u2  the verifier's checks pass on the decided shape: one membership, cse_reader (inherit, set, admin) =
        (true, false, false); no CREATE or TEMPORARY; no role setting
    u3  each departure from that shape FAILs and the detail names the offender
    u4  a missing login FAILs every verifier check without querying privileges of an unknown role
    u5  the ledger's checks pass on an ordinary, permanent table without row-level security or inheritance, writable
        only by its owner
    u6  each ledger departure FAILs and names the offender (roles, PUBLIC, kind, partition, persistence, row-level
        security, inheritance)
    u7  a missing ledger FAILs every ledger check
    u8  inside db_checks: role_cse_verifier also needs INHERIT; no_unexpected_memberships exempts exactly
        cse_verifier -> cse_reader; the new checks run before the ledger-clean step's rollback
    u9  the static files: four pg_ident mappings, provision.sh's users step creates cse-verifier in group cse, and the
        bootstrap creates and enforces the login
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.ops import verify_server as vs  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
LEDGER_OID = 16500


class FakeConnection:
    def __init__(self, log):
        self.log = log

    def rollback(self):
        self.log.append(("ROLLBACK", None))


class FakeCursor:
    """Answers each query from the first rule whose substring occurs in the SQL; unknown queries get (None,) / []."""

    def __init__(self, rules):
        self.rules, self.log, self._last = rules, [], None
        self.connection = FakeConnection(self.log)

    def execute(self, sql, args=None):
        self.log.append((sql, args))
        self._last = None
        for needle, value in self.rules:
            if needle in sql:
                self._last = value
                break

    def fetchone(self):
        v = self._last
        if isinstance(v, list):
            return v[0] if v else None
        return (None,) if v is None else v

    def fetchall(self):
        v = self._last
        if v is None:
            return []
        return v if isinstance(v, list) else [v]


def verifier_rules(exists=True, members=(("cse_reader", True, False, False, "postgres"),), create=False, temp=False,
                   settings=None):
    return [("select exists (select 1 from pg_roles where rolname", (exists,)),
            ("from pg_auth_members m", list(members)),
            ("has_database_privilege", (create, temp)),
            ("from pg_db_role_setting", (settings,))]


def ledger_rules(oid=LEDGER_OID, roles=None, public=None, shape=("r", "p", False, False, False), inh=None):
    return [("to_regclass(%s)::oid", (oid,)),
            ("has_any_column_privilege", (roles,)),
            ("'PUBLIC:'", (public,)),
            ("relforcerowsecurity", shape),
            ("from pg_inherits", (inh,))]


def run(fn, rules):
    rep, cur = vs.Report(), FakeCursor(rules)
    fn(cur, rep)
    return {i["check"]: i for i in rep.items}, cur


# ------------------------------------------------------------------------------------------------ u1

def test_u1_roles_gain_the_login_and_keep_the_five():
    assert vs.ROLES == {"cse_owner": False, "cse_migrator": True, "cse_worker": True, "cse_reader": False,
                        "cse_backup": True, "cse_verifier": True}
    assert (vs.VERIFIER, vs.VERIFIER_GROUP, vs.LEDGER, vs.LEDGER_OWNER) == \
        ("cse_verifier", "cse_reader", "ops.schema_migrations", "cse_owner")
    assert vs.LEDGER_WRITES == ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


# ------------------------------------------------------------------------------------------------ u2 - u4

def test_u2_verifier_checks_pass_on_the_decided_shape():
    items, _ = run(vs.verifier_checks, verifier_rules())
    assert set(items) == set(vs.VERIFIER_CHECKS)
    assert all(i["status"] == "PASS" for i in items.values()), items


@pytest.mark.parametrize("kw, check, needle", [
    ({"members": (("cse_reader", False, False, False, "postgres"),)}, "verifier_membership_reader_only", "inherit=False"),
    ({"members": (("cse_reader", True, True, False, "postgres"),)}, "verifier_membership_reader_only", "set=True"),
    ({"members": (("cse_reader", True, False, True, "postgres"),)}, "verifier_membership_reader_only", "admin=True"),
    ({"members": (("cse_reader", True, False, False, "postgres"), ("cse_worker", True, True, False, "postgres"))},
     "verifier_membership_reader_only", "cse_verifier->cse_worker"),
    ({"members": (("cse_reader", True, False, False, "cse_owner"), ("cse_reader", True, False, False, "postgres"))},
     "verifier_membership_reader_only", "grantor=cse_owner"),
    ({"members": (("pg_read_all_data", True, True, False, "postgres"),)}, "verifier_membership_reader_only",
     "cse_verifier->pg_read_all_data"),
    ({"members": ()}, "verifier_membership_reader_only", "no membership"),
    ({"create": True}, "verifier_no_database_create_or_temporary", "CREATE=True"),
    ({"temp": True}, "verifier_no_database_create_or_temporary", "TEMPORARY=True"),
    ({"settings": "cse: search_path=public"}, "verifier_no_role_settings", "cse: search_path=public"),
])
def test_u3_each_verifier_departure_fails_and_names_it(kw, check, needle):
    items, _ = run(vs.verifier_checks, verifier_rules(**kw))
    assert items[check]["status"] == "FAIL" and needle in items[check]["detail"], items[check]
    assert [c for c, i in items.items() if i["status"] == "FAIL"] == [check]


def test_u4_a_missing_login_fails_every_verifier_check_without_querying_it():
    items, cur = run(vs.verifier_checks, verifier_rules(exists=False))
    assert {c: (i["status"], i["detail"]) for c, i in items.items()} == \
        {c: ("FAIL", "cse_verifier missing") for c in vs.VERIFIER_CHECKS}
    assert len(cur.log) == 1 and "has_database_privilege" not in cur.log[0][0]


# ------------------------------------------------------------------------------------------------ u5 - u7

def test_u5_ledger_checks_pass_on_the_ordinary_owner_only_table():
    items, cur = run(vs.ledger_checks, ledger_rules())
    assert set(items) == set(vs.LEDGER_CHECKS)
    assert all(i["status"] == "PASS" for i in items.values()), items
    privs = next(a for s, a in cur.log if "has_any_column_privilege" in s)
    assert privs == (["INSERT", "UPDATE", "DELETE", "TRUNCATE"], "cse_owner", LEDGER_OID, LEDGER_OID)
    sql = next(s for s, _ in cur.log if "has_any_column_privilege" in s)
    for clause in ("r.rolname <> %s", "not r.rolsuper", "r.rolname !~ '^pg_'",
                   "when p.priv in ('INSERT', 'UPDATE') then has_any_column_privilege",
                   "else has_table_privilege"):
        assert clause in sql, clause
    assert "cse_migrator" not in sql                      # no exemption by name: SET without INHERIT holds nothing


@pytest.mark.parametrize("kw, check, needle", [
    ({"roles": "cse_reader:INSERT, cse_verifier:INSERT"}, "ledger_writable_only_by_owner", "cse_reader:INSERT"),
    ({"public": "PUBLIC:TRUNCATE"}, "ledger_writable_only_by_owner", "PUBLIC:TRUNCATE"),
    ({"public": "PUBLIC:INSERT(version)", "roles": "cse_backup:INSERT"}, "ledger_writable_only_by_owner",
     "PUBLIC:INSERT(version), cse_backup:INSERT"),
    ({"shape": ("p", "p", False, False, False)}, "ledger_ordinary_table", "relkind='p'"),
    ({"shape": ("v", "p", False, False, False)}, "ledger_ordinary_table", "relkind='v'"),
    ({"shape": ("r", "p", False, False, True)}, "ledger_ordinary_table", "relispartition=True"),
    ({"shape": ("r", "u", False, False, False)}, "ledger_permanent", "relpersistence='u'"),
    ({"shape": ("r", "p", True, False, False)}, "ledger_no_row_level_security", "relrowsecurity=True"),
    ({"shape": ("r", "p", False, True, False)}, "ledger_no_row_level_security", "relforcerowsecurity=True"),
    ({"inh": "child ops.ledger_child"}, "ledger_no_inheritance", "child ops.ledger_child"),
    ({"inh": "parent ops.ledger_parent"}, "ledger_no_inheritance", "parent ops.ledger_parent"),
])
def test_u6_each_ledger_departure_fails_and_names_it(kw, check, needle):
    items, _ = run(vs.ledger_checks, ledger_rules(**kw))
    assert items[check]["status"] == "FAIL" and needle in items[check]["detail"], items[check]
    assert [c for c, i in items.items() if i["status"] == "FAIL"] == [check]


def test_u7_a_missing_ledger_fails_every_ledger_check():
    items, cur = run(vs.ledger_checks, ledger_rules(oid=None))
    assert {c: (i["status"], i["detail"]) for c, i in items.items()} == \
        {c: ("FAIL", "ops.schema_migrations missing") for c in vs.LEDGER_CHECKS}
    assert len(cur.log) == 1


# ------------------------------------------------------------------------------------------------ u8

def db_rules(verifier_inherit=True):
    roles = [("cse_owner", False, False, False, False, False, False, False),
             ("cse_migrator", True, False, False, False, False, False, False),
             ("cse_worker", True, False, False, False, False, False, True),
             ("cse_reader", False, False, False, False, False, False, True),
             ("cse_backup", True, False, False, False, False, False, True),
             ("cse_verifier", True, False, False, False, False, False, verifier_inherit)]
    return [("server_version_num", ("170011",)), ("listen_addresses", ("",)),
            ("from pg_roles where rolname = any(%s)", roles),
            ("where rolsuper order by rolname", [("postgres",)])] + verifier_rules() + ledger_rules()


@pytest.mark.parametrize("inherit, status", [(True, "PASS"), (False, "FAIL")])
def test_u8_db_checks_wiring(inherit, status, tmp_path):
    rep, cur = vs.Report(), FakeCursor(db_rules(verifier_inherit=inherit))
    vs.db_checks(cur, rep, migrations_dir=str(tmp_path))
    items = {i["check"]: i for i in rep.items}
    assert items["role_cse_verifier"]["status"] == status and f"inherit={inherit}" in items["role_cse_verifier"]["detail"]
    for other in ("cse_owner", "cse_migrator", "cse_worker", "cse_reader", "cse_backup"):
        assert items[f"role_{other}"]["status"] == "PASS" and "inherit" not in items[f"role_{other}"]["detail"]
    memberships = next(s for s, _ in cur.log if "string_agg(u.rolname || '->' || r.rolname" in s)
    assert "and not (r.rolname = 'cse_reader' and u.rolname = 'cse_verifier')" in memberships
    assert "and not (r.rolname = 'cse_owner' and u.rolname = 'cse_migrator')" in memberships
    assert "and not (r.rolname = 'pg_read_all_data' and u.rolname = 'cse_backup')" in memberships
    order = [i["check"] for i in rep.items]
    for check in vs.VERIFIER_CHECKS + vs.LEDGER_CHECKS:
        assert check in items and order.index(check) < order.index("migration_ledger_clean"), check
    first_rollback = [s for s, _ in cur.log].index("ROLLBACK")
    assert first_rollback > max(i for i, (s, _) in enumerate(cur.log) if "from pg_inherits" in s)


# ------------------------------------------------------------------------------------------------ u9

def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def test_u9_pg_ident_provision_and_bootstrap():
    ident = [l.split() for l in read("ops", "postgres", "pg_ident.conf").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    assert ident == [["cse", "cse-worker", "cse_worker"], ["cse", "cse-backup", "cse_backup"],
                     ["cse", "cse-migrator", "cse_migrator"], ["cse", "cse-verifier", "cse_verifier"]]
    prov = read("ops", "provision", "provision.sh")
    users = prov[prov.index("step_users() {"):prov.index("step_dirs() {")]
    assert "for u in cse-worker cse-backup cse-migrator cse-verifier; do" in users
    assert 'useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$u"' \
        in users and 'usermod -a -G cse "$u"' in users
    boot = read("ops", "provision", "sql", "bootstrap_cluster.sql")
    assert "('cse_verifier', true)) as t(name, can_login) loop" in boot
    assert "alter role cse_verifier inherit;" in boot
    assert "grant cse_reader to cse_verifier with admin false, inherit true, set false;" in boot
    assert "grant connect on database %I to cse_migrator, cse_worker, cse_reader, cse_backup'" in boot  # unchanged
