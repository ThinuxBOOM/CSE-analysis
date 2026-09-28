"""
P1 platform tests against REAL PostgreSQL (17 expected). Each module run creates its own throwaway clusters with
initdb (worker/ops/ephemeral_pg.py) - no existing server, no network, no CSE.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p1_postgres.py

Covers: cluster bootstrap, migration runner + ledger (hashes, edits, out-of-order, rollback, superuser refusal), the
role/privilege model, append-only enforcement (even for the owner), backup dump + manifest, corruption/missing artifact
detection, restore into a scratch cluster, independent recording of backup failures, off-site 'not_configured' and a
real encrypted restic repository (when restic is installed), and absence of secrets from logs/ledger/status files.
"""
import json
import os
import shutil
import socket
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
PROTECTED = ("raw_market_observations", "raw_index_observations", "report_filing_observations",
             "report_document_classifications", "report_statement_periods", "report_classification_evidence")
SECRET = "p1-offsite-secret-" + uuid.uuid4().hex
# a plain (non-identity) column of each table: identity columns reject UPDATE before any privilege or trigger check
KEYCOL = {"report_classification_evidence": "ordinal", "issuers": "issuer_id", "report_statement_periods": "first_page",
          "financial_fact_candidates": "page"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    from worker.ops.ephemeral_pg import EphemeralCluster
    base = tmp_path_factory.mktemp("p1_cluster")
    # bootstrap superuser 'postgres', exactly as on the Ubuntu server (the globals file's GRANTED BY depends on it)
    ec = EphemeralCluster(BINDIR, base_dir=str(base), port=free_port(), durable=True, superuser="postgres").start()
    # production-like hba: local sockets only (trust stands in for peer: the test OS user is not mapped)
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)
    yield ec
    ec.cleanup()


def conn(pg, user, db="cse", autocommit=True):
    c = pg.connect(dbname=db, user=user)
    c.autocommit = autocommit
    return c


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def migrated(pg):
    from worker.ops import migrate as mig
    c = conn(pg, "cse_migrator", autocommit=False)
    out = mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    again = mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()
    return out, again


def ops_env(pg, tmp, **extra):
    from worker.ops import settings as ops_settings
    env = {"CSE_DB_NAME": "cse", "CSE_DB_HOST": pg.sockdir, "CSE_DB_PORT": str(pg.port), "CSE_DB_USER": "cse_backup",
           "CSE_PG_BINDIR": BINDIR, "CSE_BACKUP_ROOT": str(tmp), "CSE_RESTORE_PORT": str(free_port())}
    env.update(extra)
    return ops_settings.load(env)


def make_ledger(s, redact=None):
    from worker.ops import backup as ops_backup
    from worker.ops.redact import Redactor
    return ops_backup.open_ledger(s, redact or Redactor())


# ------------------------------------------------------------------------------------------------ bootstrap + runner

def test_bootstrap_roles_have_no_elevated_attributes(pg):
    c = conn(pg, pg.superuser, db="cse")
    rows = {r[0]: r[1:] for r in q(c, "select rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
                                      "rolbypassrls from pg_roles where rolname like 'cse%%'")}
    assert rows == {"cse_owner": (False, False, False, False, False, False),
                    "cse_migrator": (True, False, False, False, False, False),
                    "cse_worker": (True, False, False, False, False, False),
                    "cse_reader": (False, False, False, False, False, False),
                    "cse_backup": (True, False, False, False, False, False)}
    assert q(c, "select m.inherit_option, m.set_option from pg_auth_members m join pg_roles r on r.oid = m.roleid "
                "join pg_roles u on u.oid = m.member where r.rolname = 'cse_owner' and u.rolname = 'cse_migrator'") == [(False, True)]
    assert q(c, "select pg_has_role('cse_backup', 'pg_read_all_data', 'MEMBER')") == [(True,)]
    # the bootstrap is idempotent
    r = pg.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)
    assert r.returncode == 0
    c.close()


def test_runner_applies_everything_once_with_hashes(pg, migrated):
    from worker.ops import migrate as mig
    out, again = migrated
    names = [m.filename for m in mig.discover(MIGDIR)]
    assert out["applied"] == names and again["applied"] == [] and again["already_applied"] == names
    c = conn(pg, "cse_migrator")
    with pytest.raises(Exception, match="permission denied for schema ops"):
        q(c, "select 1 from ops.schema_migrations")              # NOINHERIT: no owner privileges without SET ROLE
    s = conn(pg, pg.superuser)
    ledger = q(s, "select version, filename, sha256, applied_by, applied_as, runner_version from ops.schema_migrations "
                  "order by version")
    s.close()
    assert [r[1] for r in ledger] == names and 6 not in [r[0] for r in ledger]
    assert all(r[3] == "cse_migrator" and r[4] == "cse_owner" and r[5] == mig.RUNNER_VERSION for r in ledger)
    assert {r[1]: r[2] for r in ledger} == {m.filename: m.sha256 for m in mig.discover(MIGDIR)}
    st = mig.status(c, mig.discover(MIGDIR))                    # the migration role reads the ledger via SET ROLE
    assert st["problems"] == [] and st["pending"] == [] and st["ledger"] == "present"
    c.autocommit = False
    assert mig.apply(c, mig.discover(MIGDIR), dry_run=True, log=lambda m: None)["applied"] == []
    c.close()


def test_runner_refuses_superuser(pg, migrated):
    from worker.ops import migrate as mig
    c = conn(pg, pg.superuser, autocommit=False)
    with pytest.raises(mig.MigrationError, match="superuser"):
        mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()


def test_runner_blocks_edited_and_out_of_order_migrations(pg, migrated, tmp_path):
    from worker.ops import migrate as mig
    d = tmp_path / "m"
    shutil.copytree(MIGDIR, d)
    with open(d / "0007_issuers.sql", "a", encoding="utf-8") as f:
        f.write("\n-- an in-place edit\n")
    c = conn(pg, "cse_migrator", autocommit=False)
    with pytest.raises(mig.MigrationError, match="hash mismatch: 0007_issuers.sql"):
        mig.apply(c, mig.discover(str(d)), log=lambda m: None)
    shutil.copy(os.path.join(MIGDIR, "0007_issuers.sql"), d / "0007_issuers.sql")
    (d / "0006_late.sql").write_text("create table late (x int);")
    with pytest.raises(mig.MigrationError, match="out of order: 0006_late.sql"):
        mig.apply(c, mig.discover(str(d)), log=lambda m: None)
    os.remove(d / "0009_local_security_boundary.sql")
    os.remove(d / "0006_late.sql")
    assert any("has no migration file" in p for p in mig.status(c, mig.discover(str(d)))["problems"])
    c.close()
    su = conn(pg, pg.superuser)
    assert q(su, "select to_regclass('public.late')") == [(None,)]
    su.close()


def test_failed_migration_rolls_back_completely(pg, migrated, tmp_path):
    from worker.ops import migrate as mig
    d = tmp_path / "m"
    shutil.copytree(MIGDIR, d)
    (d / "0099_broken.sql").write_text("create table half_done (x int);\nselect * from no_such_table;\n")
    c = conn(pg, "cse_migrator", autocommit=False)
    with pytest.raises(mig.MigrationError, match="0099_broken.sql failed and was rolled back"):
        mig.apply(c, mig.discover(str(d)), log=lambda m: None)
    c.close()
    su = conn(pg, pg.superuser)                                  # the migration role has no privileges of its own
    assert q(su, "select to_regclass('public.half_done')") == [(None,)]
    assert q(su, "select count(*) from ops.schema_migrations where version = 99") == [(0,)]
    su.close()


def test_migration_ledger_is_append_only(pg, migrated):
    c = conn(pg, "cse_migrator", autocommit=False)
    q(c, "set role cse_owner")
    for stmt in ("update ops.schema_migrations set sha256 = repeat('0', 64) where version = 1",
                 "delete from ops.schema_migrations where version = 1", "truncate ops.schema_migrations"):
        with pytest.raises(Exception) as ei:
            q(c, stmt)
        assert ei.value.pgcode == "23001", stmt                  # restrict_violation from the trigger
        c.rollback()
        q(c, "set role cse_owner")
    c.close()


# ------------------------------------------------------------------------------------------------ privileges

def test_everything_owned_by_nologin_owner_and_public_has_nothing(pg, migrated):
    c = conn(pg, pg.superuser)
    assert q(c, "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace join pg_roles r "
                "on r.oid = c.relowner where n.nspname in ('public', 'ops') and c.relkind in ('r','p','v','S') "
                "and r.rolname <> 'cse_owner'") == [(0,)]
    assert q(c, "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace, "
                "aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                "where n.nspname in ('public', 'ops') and a.grantee = 0") == [(0,)]
    q(c, "create role p1_stranger login")
    try:
        with pytest.raises(Exception, match="permission denied for database"):
            pg.connect(dbname="cse", user="p1_stranger")
    finally:
        q(c, "drop role p1_stranger")
    c.close()


def _seed_source_rows(pg):
    """One row in every protected table, inserted by the WORKER (proves its insert privileges)."""
    w = conn(pg, "cse_worker")
    cid = q(w, "insert into companies (ticker, company_name) values (%s, 'P1 test') returning id",
            (f"P1T{uuid.uuid4().hex[:6]}.N0000",))[0][0]
    att = str(uuid.uuid4())
    q(w, "insert into raw_market_observations (request_attempt_id, company_id, observation_date, capture_window, source, "
         "observed_at, raw_payload) values (%s, %s, '2099-01-05', 'post_close', 'TEST', now(), '{}')", (att, cid))
    q(w, "insert into raw_index_observations (request_attempt_id, index_name, observation_date, capture_window, source, "
         "observed_at, raw_payload) values (%s, 'ASPI', '2099-01-05', 'post_close', 'TEST', now(), '{}')", (att,))
    run = q(w, "insert into report_discovery_runs (source_endpoint, request_params) values ('test', '{}') returning id")[0][0]
    fid = int(uuid.uuid4().int % 10**9) + 10**9
    q(w, "insert into report_filings (cse_filing_id, first_seen_at, last_seen_at) values (%s, now(), now())", (fid,))
    q(w, "insert into report_filing_observations (cse_filing_id, discovery_run_id, source_endpoint, source_bucket, "
         "metadata_hash, raw_item) values (%s, %s, 'test', 'none', %s, '{}')", (fid, run, "a" * 64))
    rdc = q(w, "insert into report_document_classifications (cse_filing_id, document_sha256, classifier_version, "
               "text_extractor, classification_status, page_count, text_page_count, document_type, document_type_status, "
               "underlying_type, underlying_type_status, period_status, fiscal_year_end_status, fiscal_period_status) "
               "values (%s, %s, 'test', 'test', 'classified', 1, 1, 'other', 'undetermined', 'other', 'undetermined', "
               "'undetermined', 'undetermined', 'undetermined') returning id", (fid, "b" * 64))[0][0]
    q(w, "insert into report_statement_periods (classification_id, statement_kind, first_page, period_kind, end_date, "
         "role, audit_status) values (%s, 'financial_position', 1, 'instant', '2098-12-31', 'unknown', 'unknown')", (rdc,))
    q(w, "insert into report_classification_evidence (classification_id, ordinal, decision, source, rule_id, "
         "evidence_kind, outcome) values (%s, 0, 'document_type', 'document', 'test', 'test', 'note')", (rdc,))
    return w, {"filing": fid, "classification": rdc}


def test_worker_can_insert_but_never_update_delete_or_truncate_source_rows(pg, migrated):
    import psycopg2
    w, _ = _seed_source_rows(pg)
    for t in PROTECTED + ("financial_fact_candidates", "issuers"):
        col = KEYCOL.get(t, "id")
        for stmt in (f"update {t} set {col} = {col}", f"delete from {t}", f"truncate {t}"):
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                q(w, stmt)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(w, "create table public.worker_table (x int)")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(w, "select * from ops.schema_migrations")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(w, "select * from ops.backup_runs")
    w.close()


def test_append_only_triggers_stop_even_the_owner(pg, migrated):
    _seed_source_rows(pg)[0].close()
    c = conn(pg, "cse_migrator", autocommit=False)
    for t in PROTECTED:
        col = KEYCOL.get(t, "id")
        for stmt in (f"update {t} set {col} = {col}", f"delete from {t}", f"truncate {t}"):
            q(c, "set role cse_owner")
            with pytest.raises(Exception) as ei:
                q(c, stmt)
            # 23001 = the append-only trigger; a TRUNCATE of a table referenced by a foreign key is refused even
            # earlier (0A000). Either way nothing is removed.
            allowed = ("23001", "0A000") if stmt.startswith("truncate") else ("23001",)
            assert ei.value.pgcode in allowed, (t, stmt, ei.value.pgcode)
            c.rollback()
    c.close()


def test_f5_insert_triggers_still_fire_for_the_worker(pg, migrated):
    """0009 revokes EXECUTE from PUBLIC on functions; trigger functions must still run for inserts by the worker."""
    import psycopg2
    w, ids = _seed_source_rows(pg)
    with pytest.raises(psycopg2.errors.CheckViolation, match="differ from its F3 classification"):
        q(w, "insert into financial_extraction_runs (cse_filing_id, classification_id, document_sha256, word_extractor, "
             "f4_extractor_version, classifier_version, builder_version, mapper_version, vocabulary_version, template, "
             "template_basis, document_status, f3_period_status, counts, content_sha256) values "
             "(%s, %s, %s, 'w', 'f4', 'WRONG-VERSION', 'b', 'm', 'v1', 'general', 't', 'extracted', 'undetermined', '{}', %s)",
          (ids["filing"], ids["classification"], "b" * 64, "c" * 64))
    q(w, "insert into financial_extraction_runs (cse_filing_id, classification_id, document_sha256, word_extractor, "
         "f4_extractor_version, classifier_version, builder_version, mapper_version, vocabulary_version, template, "
         "template_basis, document_status, f3_period_status, counts, content_sha256) values "
         "(%s, %s, %s, 'w', 'f4', 'test', 'b', 'm', 'v1', 'general', 't', 'extracted', 'undetermined', '{}', %s)",
      (ids["filing"], ids["classification"], "b" * 64, "c" * 64))
    w.close()


def test_reader_and_backup_roles(pg, migrated):
    import psycopg2
    b = conn(pg, "cse_backup")
    assert q(b, "select count(*) >= 0 from raw_market_observations") == [(True,)]
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(b, "insert into system_config (key, value) values ('x', '1')")
    rid = q(b, "insert into ops.backup_runs (run_kind, status) values ('local_dump', 'running') returning id")[0][0]
    q(b, "update ops.backup_runs set status = 'failed', finished_at = now(), error = 'test' where id = %s", (rid,))
    for stmt in ("update ops.backup_runs set error = 'rewritten' where id = %s", "delete from ops.backup_runs where id = %s"):
        with pytest.raises(Exception) as ei:
            q(b, stmt, (rid,))
        assert ei.value.pgcode in ("23001", "42501")
    with pytest.raises(psycopg2.errors.CheckViolation):          # success needs its evidence
        q(b, "insert into ops.backup_runs (run_kind, status, finished_at) values ('offsite_sync', 'succeeded', now())")
    b.close()
    # cse_reader is NOLOGIN: check its privileges through has_table_privilege
    s = conn(pg, pg.superuser)
    assert q(s, "select has_table_privilege('cse_reader', 'public.raw_market_observations', 'SELECT'), "
                "has_table_privilege('cse_reader', 'public.raw_market_observations', 'INSERT'), "
                "has_table_privilege('cse_reader', 'ops.backup_runs', 'UPDATE')") == [(True, False, False)]
    s.close()


def test_verify_server_database_checks_pass(pg, migrated):
    from worker.ops import verify_server as vs
    rep = vs.Report()
    c = conn(pg, pg.superuser)
    with c.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=MIGDIR)
    c.close()
    failed = [i for i in rep.items if i["status"] == "FAIL"]
    assert not failed, failed
    assert not [i for i in rep.items if i["status"] == "WARN"]


# ------------------------------------------------------------------------------------------------ backup / restore

@pytest.fixture(scope="module")
def dumped(pg, migrated, tmp_path_factory):
    from worker.ops import backup as ops_backup
    from worker.ops.redact import Redactor
    _seed_source_rows(pg)[0].close()
    root = tmp_path_factory.mktemp("p1_backup")
    s = ops_env(pg, root)
    led, c = make_ledger(s)
    logs = []
    code, rec = ops_backup.dump(s, led, Redactor(), logs.append)
    c.close()
    assert code == 0, rec
    return s, rec


def test_dump_is_complete_immutable_and_recorded(pg, dumped):
    from worker.ops import backup as ops_backup, dbhash
    s, rec = dumped
    d = os.path.join(s.backup_root, *rec["artifact_key"].split("/"))
    assert sorted(os.listdir(d)) == ["database.dump", "globals.sql", "manifest.json", "manifest.json.sha256"]
    assert all(oct(os.stat(os.path.join(d, n)).st_mode & 0o777) == "0o440" for n in os.listdir(d))
    assert ops_backup.verify_dump(d, BINDIR) == []
    assert os.listdir(os.path.join(s.backup_root, "pg", "staging")) == []
    m = ops_backup.load_manifest(d)
    assert m["data_checksums"] == "on" and m["server_version"].startswith("17") and m["bootstrap_superuser"] == "postgres"
    c = conn(pg, "cse_backup")
    with c.cursor() as cur:
        dbhash.apply_session_settings(cur)
        live = {k: v["rows"] for k, v in m["inventory"]["tables"].items()}
        for name, rows in live.items():
            sch, tab = name.split(".")
            cur.execute(f'select count(*) from "{sch}"."{tab}"')
            if name != "ops.backup_runs":                        # the dump's own ledger row changed status afterwards
                assert cur.fetchone()[0] == rows, name
    row = q(c, "select status, artifact_key, artifact_sha256, manifest_sha256 from ops.backup_runs where id = %s", (rec["id"],))
    assert row == [("succeeded", rec["artifact_key"], m["files"]["database.dump"]["sha256"], rec["manifest_sha256"])]
    globals_sql = open(os.path.join(d, "globals.sql")).read()
    assert "PASSWORD" not in globals_sql.upper().replace("NOPASSWORD", "")
    c.close()


def _writable_copy(src, dst):
    shutil.copytree(src, dst)
    for n in os.listdir(dst):
        os.chmod(os.path.join(dst, n), 0o640)
    os.chmod(dst, 0o750)
    return dst


def test_corrupted_or_missing_artifacts_are_detected(dumped, tmp_path):
    from worker.ops import backup as ops_backup
    s, rec = dumped
    src = os.path.join(s.backup_root, *rec["artifact_key"].split("/"))
    d = _writable_copy(src, str(tmp_path / "copy1"))
    with open(os.path.join(d, "database.dump"), "r+b") as f:
        f.seek(os.path.getsize(os.path.join(d, "database.dump")) // 2)
        b = f.read(1)
        f.seek(-1, 1)
        f.write(bytes([b[0] ^ 0xFF]))
    assert any("SHA-256 does not match" in p for p in ops_backup.verify_dump(d, BINDIR))
    d2 = _writable_copy(src, str(tmp_path / "copy2"))
    with open(os.path.join(d2, "database.dump"), "r+b") as f:
        f.truncate(100)
    probs = ops_backup.verify_dump(d2, BINDIR)
    assert any("size" in p for p in probs) and any("SHA-256" in p for p in probs)
    d3 = _writable_copy(src, str(tmp_path / "copy3"))
    os.remove(os.path.join(d3, "globals.sql"))
    assert "globals.sql missing" in ops_backup.verify_dump(d3, BINDIR)


def test_restore_check_restores_into_scratch_cluster(pg, dumped):
    from worker.ops import restore_check
    from worker.ops.redact import Redactor
    s, rec = dumped
    led, c = make_ledger(s)
    code, out = restore_check.run(s, led, Redactor(), lambda m: None)
    assert code == 0, out
    assert out["covers"] == [rec["artifact_key"]] and out["details"]["tables"] >= 30
    assert q(c, "select status from ops.backup_runs where id = %s", (out["id"],)) == [("succeeded",)]
    assert os.listdir(s.restore_scratch) == []                     # throwaway cluster removed
    c.close()


def test_restore_check_detects_a_dump_that_does_not_match_its_manifest(pg, dumped, tmp_path):
    """A manifest whose table digest disagrees with the restored data (e.g. a wrong or tampered dump) must fail."""
    import hashlib
    from worker.ops import backup as ops_backup, restore_check
    from worker.ops.redact import Redactor
    s, rec = dumped
    src = os.path.join(s.backup_root, *rec["artifact_key"].split("/"))
    d = _writable_copy(src, str(tmp_path / "tampered"))
    m = ops_backup.load_manifest(d)
    m["inventory"]["tables"]["public.raw_market_observations"]["rows"] += 1
    mb = json.dumps(m, indent=2, sort_keys=True).encode()
    open(os.path.join(d, "manifest.json"), "wb").write(mb)
    open(os.path.join(d, "manifest.json.sha256"), "w").write(f"{hashlib.sha256(mb).hexdigest()}  manifest.json\n")
    led, c = make_ledger(s)
    code, out = restore_check.run(s, led, Redactor(), lambda x: None, dump_dir=d)
    assert code == 1 and out["status"] == "failed" and "raw_market_observations" in out["error"]
    c.close()


def test_backup_failure_is_recorded_independently_of_capture_data(pg, dumped, tmp_path):
    from worker.ops import backup as ops_backup
    from worker.ops.redact import Redactor
    s0, _ = dumped
    s = ops_env(pg, s0.backup_root, CSE_PG_BINDIR=str(tmp_path / "missing-bin"))
    c = conn(pg, "cse_backup")
    before = q(c, "select (select count(*) from raw_market_observations), (select count(*) from daily_market_data)")
    dumps_before = ops_backup.list_dumps(s)
    led, lc = make_ledger(s)
    code, rec = ops_backup.dump(s, led, Redactor(), lambda m: None)
    lc.close()
    assert code == 1 and rec["status"] == "failed"
    assert q(c, "select status, error is not null, artifact_key from ops.backup_runs where id = %s", (rec["id"],)) == \
        [("failed", True, None)]
    assert q(c, "select (select count(*) from raw_market_observations), (select count(*) from daily_market_data)") == before
    assert ops_backup.list_dumps(s) == dumps_before and os.listdir(os.path.join(s.backup_root, "pg", "staging")) == []
    st = json.load(open(os.path.join(s.backup_root, "status", "local_dump.json")))
    assert st["status"] == "failed"
    c.close()


def test_abandoned_running_rows_are_closed_as_failed(pg, dumped):
    s, _ = dumped
    c = conn(pg, "cse_backup")
    rid = q(c, "insert into ops.backup_runs (run_kind, status, started_at) values ('restore_check', 'running', "
               "now() - interval '9 hours') returning id")[0][0]
    led, lc = make_ledger(s)
    assert led.close_abandoned("restore_check", 6) >= 1
    lc.close()
    assert q(c, "select status, error like 'abandoned%%' from ops.backup_runs where id = %s", (rid,)) == [("failed", True)]
    c.close()


def test_dump_whose_terminal_ledger_update_fails_is_never_reported_as_success(pg, migrated, tmp_path):
    """A real dump: the run row is inserted, then the LEDGER connection is killed; the dump itself completes and the
    terminal UPDATE fails. Nothing may claim success; the artifact is kept; capture data is untouched; the next run of
    the kind closes the orphaned row as failed (never succeeded), keeping what the run reported."""
    from worker.ops import backup as ops_backup, dbhash, ledger as ops_ledger
    from worker.ops.redact import Redactor
    s = ops_env(pg, tmp_path / "backup")
    su, b = conn(pg, pg.superuser), conn(pg, "cse_backup")

    def capture_state():
        with b.cursor() as cur:
            dbhash.apply_session_settings(cur)
            return {k: v for k, v in dbhash.inventory(cur)["tables"].items() if not k.startswith("ops.")}

    before = capture_state()
    led, lc = make_ledger(s)
    killed = []

    def log(msg):   # first call: run row inserted, snapshot taken -> drop the ledger's connection (only that one)
        if not killed:
            killed.append(q(su, "select pg_terminate_backend(%s, 10000)", (lc.get_backend_pid(),))[0][0])

    code, rec = ops_backup.dump(s, led, Redactor(), log)
    assert killed == [True]
    assert q(b, "select status from ops.backup_runs where id = %s", (rec["id"],)) == [("running",)]
    assert code != 0 and rec["status"] != "succeeded"
    assert (rec["outcome"], rec["status"], rec["ledger"]) == ("succeeded", "unrecorded", "update_failed")
    st = ops_ledger.read_status(os.path.join(s.backup_root, "status"), "local_dump")
    assert (st["id"], st["outcome"], st["status"], st["ledger"]) == (rec["id"], "succeeded", "unrecorded", "update_failed")
    # the backup operation itself completed: the artifact exists and verifies
    assert ops_backup.verify_dump(os.path.join(s.backup_root, *rec["artifact_key"].split("/")), BINDIR) == []
    # capture / market / financial state is untouched
    assert capture_state() == before
    # never counted as a successful local dump; the unrecorded outcome is an alert
    ps = ops_backup.protection_status(s, b)
    assert ps["runs"]["local_dump"]["last_status"] == "running"
    assert any("local_dump" in a and "NOT recorded" in a for a in ps["alerts"])
    # the next local_dump run closes the orphaned row at once: failed, with the reported outcome preserved
    led2, lc2 = make_ledger(s)
    assert led2.close_abandoned("local_dump", 6) >= 1
    lc2.close()
    status, error, details = q(b, "select status, error, details from ops.backup_runs where id = %s", (rec["id"],))[0]
    assert status == "failed" and "not recorded" in error
    assert details["operation_outcome"] == "succeeded" and details["reported"]["artifact_key"] == rec["artifact_key"]
    with pytest.raises(Exception) as ei:                           # and it stays that way (append-only ledger)
        q(b, "update ops.backup_runs set status = 'succeeded' where id = %s", (rec["id"],))
    assert ei.value.pgcode == "23001"
    for c in (su, b, lc):
        c.close()


def test_terminal_update_rejected_by_the_database_falls_back_to_a_non_success_status(pg, migrated, tmp_path):
    """The terminal UPDATE is refused by a constraint (connection still alive): a minimal NON-success terminal status is
    committed instead - 'failed' for a reported success, the same status otherwise ('not_configured' stays itself)."""
    from worker.ops import ledger as ops_ledger
    s = ops_env(pg, tmp_path / "backup")
    led, lc = make_ledger(s)
    b = conn(pg, "cse_backup")
    run = led.start("offsite_sync")
    rec = led.finish(run, "succeeded", offsite_snapshot="f" * 64, covers=["pg/dumps/2099/01/cse_x"],
                     artifact_sha256="not-a-sha256")                     # violates chk_backup_runs_sha256
    assert rec["status"] != "succeeded"
    assert (rec["outcome"], rec["status"], rec["ledger"]) == ("succeeded", "failed", "update_failed")
    assert "chk_backup_runs_sha256" in rec["ledger_error"] and ops_ledger.exit_code(rec) == 1
    status, covers, snap, details = q(b, "select status, covers, offsite_snapshot, details from ops.backup_runs "
                                         "where id = %s", (rec["id"],))[0]
    assert (status, covers, snap) == ("failed", [], None)                # nothing counts as off-site protection
    assert details["operation_outcome"] == "succeeded" and details["reported"]["offsite_snapshot"] == "f" * 64
    run2 = led.start("offsite_sync")
    rec2 = led.finish(run2, "not_configured", details=["not", "an", "object"])   # violates chk_backup_runs_details
    assert (rec2["outcome"], rec2["status"], rec2["ledger"]) == ("not_configured", "not_configured", "update_failed")
    assert q(b, "select status from ops.backup_runs where id = %s", (rec2["id"],)) == [("not_configured",)]
    assert ops_ledger.exit_code(rec2) == 1
    lc.close()
    b.close()


def test_offsite_not_configured_is_never_success(pg, dumped):
    from worker.ops import offsite
    s0, _ = dumped
    for extra in ({}, {"CSE_OFFSITE_MODE": "restic"}):
        s = ops_env(pg, s0.backup_root, **extra)
        target, redact = offsite.resolve(s)
        led, c = make_ledger(s, redact)
        code, rec = offsite.sync(s, led, target, lambda m: None)
        assert code == 2 and (rec["outcome"], rec["status"], rec["ledger"]) == ("not_configured",) * 2 + ("database",)
        assert q(c, "select status from ops.backup_runs where id = %s", (rec["id"],)) == [("not_configured",)]
        c.close()


@pytest.mark.skipif(not shutil.which("restic"), reason="restic not installed")
def test_offsite_restic_encrypted_repo_success_failure_and_no_secret_leak(pg, dumped, tmp_path, capsys):
    from worker.ops import backup as ops_backup, offsite
    s0, dump_rec = dumped
    cred = tmp_path / "credentials"
    cred.mkdir(mode=0o750)
    (cred / "repository").write_text(str(tmp_path / "offsite-repo") + "\n")
    (cred / "password").write_text(SECRET + "\n")
    (cred / "backend.env").write_text(f"AWS_SECRET_ACCESS_KEY={SECRET}-backend\n")
    extra = {"CSE_OFFSITE_MODE": "restic", "CSE_OFFSITE_REPOSITORY_FILE": str(cred / "repository"),
             "CSE_OFFSITE_PASSWORD_FILE": str(cred / "password"), "CSE_OFFSITE_ENV_FILE": str(cred / "backend.env")}
    s = ops_env(pg, s0.backup_root, **extra)
    target, redact = offsite.resolve(s)
    assert isinstance(target, offsite.ResticTarget)
    target.init()
    led, c = make_ledger(s, redact)
    logs = []
    code, rec = offsite.sync(s, led, target, logs.append)
    assert code == 0 and rec["status"] == "succeeded" and len(rec["offsite_snapshot"]) == 64
    assert dump_rec["artifact_key"] in rec["covers"]
    # the repository really is encrypted: the dump's bytes do not appear in any pack file
    raw = open(os.path.join(s.backup_root, *dump_rec["artifact_key"].split("/"), "globals.sql"), "rb").read()
    for dirpath, _, files in os.walk(tmp_path / "offsite-repo"):
        for n in files:
            assert raw[:64] not in open(os.path.join(dirpath, n), "rb").read()
    st = ops_backup.protection_status(s, c)
    assert next(d for d in st["dumps"] if d["artifact_key"] == dump_rec["artifact_key"])["protection"] == "offsite"
    # wrong password -> failed, never success; the secret never appears anywhere
    (cred / "password").write_text(SECRET + "-wrong\n")
    target2, redact2 = offsite.resolve(s)
    led2, c2 = make_ledger(s, redact2)
    code2, rec2 = offsite.sync(s, led2, target2, logs.append)
    assert code2 == 1 and rec2["status"] == "failed"
    everything = json.dumps([rec, rec2, logs, q(c, "select row_to_json(b) from ops.backup_runs b")], default=str)
    everything += open(os.path.join(s.backup_root, "status", "offsite_sync.json")).read()
    everything += capsys.readouterr().out + capsys.readouterr().err
    assert SECRET not in everything
    c.close()
    c2.close()


def test_protection_status_raises_alerts(pg, dumped):
    from worker.ops import backup as ops_backup
    s, _ = dumped
    c = conn(pg, "cse_backup")
    st = ops_backup.protection_status(s, c, verify=True)
    c.close()
    assert st["source"] == "database" and st["dumps"]
    assert any("off-site" in a for a in st["alerts"]) or all(d["protection"] == "offsite" for d in st["dumps"])
