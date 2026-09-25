"""
Stage F3 persistence against a REAL Postgres, as the RESTRICTED worker role.

Runs only when F3_TEST_DATABASE_URL points at a scratch Postgres server whose
user may CREATE DATABASE and CREATE ROLE (e.g. a throwaway local container).
Otherwise every test here is reported as SKIPPED, never as passed.

The fixture creates a fresh database, then applies 0001 -> 0005 IN ORDER, and
after each migration runs that migration's documented worker grant block (the
commented `grant ...` lines) for a throwaway login role — i.e. the grants exist
exactly as they would after a real deployment in that order. Everything the
store does is then executed through the worker role's own connection; the
admin connection is used only to set up, to read the catalog, and to tear down.
"""
import os
import re
import secrets
import sys
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

ADMIN_URL = os.environ.get("F3_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set F3_TEST_DATABASE_URL to a scratch Postgres (CREATE DATABASE/ROLE)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATIONS = ["0001_phase1_data_foundation.sql", "0002_add_open_price.sql", "0003_eod_observation_completeness.sql",
              "0004_report_filings.sql", "0005_report_classification.sql"]
F3_TABLES = ("report_document_classifications", "report_statement_periods", "report_classification_evidence")


def _url(base, db=None, user=None, password=None):
    p = urlsplit(base)
    netloc = p.netloc
    if user:
        host = netloc.split("@")[-1]
        netloc = f"{user}:{password}@{host}"
    return urlunsplit((p.scheme, netloc, "/" + db if db else p.path, p.query, p.fragment))


def documented_grants(sql, role):
    """The commented `grant ...;` lines of a migration's worker-permission block."""
    out = []
    for line in sql.splitlines():
        m = re.match(r"^--\s+(grant\s.+?;)", line.strip(), re.I)
        if m:
            out.append(m.group(1).replace("cse_worker", role))
    return out


@pytest.fixture(scope="module")
def env():
    import psycopg2
    db, role, pw = f"f3_accept_{secrets.token_hex(4)}", f"f3_worker_{secrets.token_hex(4)}", secrets.token_hex(16)
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'create database "{db}"')
        cur.execute(f'create role "{role}" login password %s', (pw,))
    owner = psycopg2.connect(_url(ADMIN_URL, db))
    owner.autocommit = True
    applied = {}
    with owner.cursor() as cur:
        cur.execute(f'revoke all on schema public from public')
        cur.execute(f'grant usage on schema public to "{role}"')
        for name in MIGRATIONS:
            sql = open(os.path.join(REPO, "supabase", "migrations", name), encoding="utf-8").read()
            cur.execute(sql)
            grants = documented_grants(sql, f'"{role}"')
            for g in grants:
                cur.execute(g)
            applied[name] = grants
    worker = psycopg2.connect(_url(ADMIN_URL, db, role, pw))
    try:
        yield {"owner": owner, "worker": worker, "role": role, "db": db, "applied": applied}
    finally:
        worker.close()
        owner.close()
        with admin.cursor() as cur:
            cur.execute(f'drop database "{db}" with (force)')
            cur.execute(f'drop role "{role}"')
        admin.close()


def _classification(fid, sha="a" * 64, version=None):
    from worker import document_text as dt, report_classification as rc
    sys.path.insert(0, os.path.dirname(__file__))
    from test_report_classification import ACL_META, acl_march_fye_q1
    r = rc.classify(acl_march_fye_q1(), ACL_META, cse_filing_id=fid, sha256=sha).to_dict()
    if version:
        r["classifier_version"] = version
    return r


def _filing(owner, fid):
    with owner.cursor() as cur:
        cur.execute("insert into report_filings (cse_filing_id, first_seen_at, last_seen_at) values (%s, now(), now()) "
                    "on conflict do nothing", (fid,))


def _counts(owner, fid):
    with owner.cursor() as cur:
        cur.execute("select count(*) from report_document_classifications where cse_filing_id = %s", (fid,))
        parents = cur.fetchone()[0]
        cur.execute("select count(*) from report_statement_periods p join report_document_classifications c "
                    "on c.id = p.classification_id where c.cse_filing_id = %s", (fid,))
        periods = cur.fetchone()[0]
        cur.execute("select count(*) from report_classification_evidence e join report_document_classifications c "
                    "on c.id = e.classification_id where c.cse_filing_id = %s", (fid,))
        return parents, periods, cur.fetchone()[0]


def test_migrations_applied_with_documented_worker_grants(env):
    assert env["applied"]["0005_report_classification.sql"], "0005 documents no worker grants"
    with env["owner"].cursor() as cur:
        cur.execute("""select table_name, string_agg(privilege_type, ',' order by privilege_type)
                       from information_schema.role_table_grants where grantee = %s and table_name = any(%s)
                       group by table_name order by table_name""", (env["role"], list(F3_TABLES)))
        grants = dict(cur.fetchall())
        cur.execute("select pg_get_serial_sequence('report_statement_periods', 'id')")
        seq = cur.fetchone()[0]
        cur.execute("select a.attidentity from pg_attribute a where a.attrelid = 'report_statement_periods'::regclass "
                    "and a.attname = 'id'")
        identity = cur.fetchone()[0]
        cur.execute("select has_sequence_privilege(%s, %s, 'USAGE')", (env["role"], seq))
        seq_usage = cur.fetchone()[0]
    assert grants == {t: "INSERT,SELECT" for t in F3_TABLES}
    assert identity == "a"                        # GENERATED ALWAYS AS IDENTITY
    # 0001's `grant usage, select on all sequences` ran BEFORE 0005 existed, so the worker
    # holds no privilege on 0005's identity sequence. Postgres does not require one for
    # identity columns (unlike bigserial) — proven by the inserts in the next test. If
    # this column is ever changed to bigserial, that test fails without a sequence grant.
    assert seq_usage is False
    print(f"\ncatalog: grants={grants} identity_sequence={seq} worker_sequence_usage={seq_usage}")


def test_worker_saves_classification_with_identity_ids(env):
    from worker.report_classification_store import PostgresClassificationStore
    _filing(env["owner"], 101)
    s = PostgresClassificationStore(env["worker"])
    assert s.save(_classification(101), 1234) == "inserted"
    s.commit()
    parents, periods, evidence = _counts(env["owner"], 101)
    r = _classification(101)
    assert (parents, periods, evidence) == (1, len(r["statement_periods"]), len(r["evidence"]))
    with env["owner"].cursor() as cur:
        cur.execute("select min(id), max(id), count(distinct id) from report_statement_periods")
        lo, hi, n = cur.fetchone()
        cur.execute("select document_type, period_end::text, fiscal_period, document_bytes from report_document_classifications "
                    "where cse_filing_id = 101")
        row = cur.fetchone()
    assert lo >= 1 and n == periods
    assert row == ("interim_financial_statements", "2026-06-30", "Q1", 1234)


def test_duplicate_save_is_idempotent(env):
    from worker.report_classification_store import PostgresClassificationStore
    s = PostgresClassificationStore(env["worker"])
    before = _counts(env["owner"], 101)
    assert s.save(_classification(101), 1234) == "already_present"
    s.commit()
    assert _counts(env["owner"], 101) == before


def test_new_classifier_version_or_new_document_hash_adds_a_row(env):
    from worker.report_classification_store import PostgresClassificationStore
    s = PostgresClassificationStore(env["worker"])
    assert s.save(_classification(101, version="f3.1-test-next")) == "inserted"
    assert s.save(_classification(101, sha="b" * 64)) == "inserted"
    s.commit()
    assert _counts(env["owner"], 101)[0] == 3


def test_worker_cannot_update_or_delete_f3_tables(env):
    import psycopg2
    w = env["worker"]
    for stmt in ("update report_document_classifications set document_type = 'other'",
                 "delete from report_document_classifications",
                 "update report_statement_periods set role = 'unknown'",
                 "delete from report_statement_periods",
                 "update report_classification_evidence set snippet = null",
                 "delete from report_classification_evidence",
                 "truncate report_classification_evidence"):
        with w.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(stmt)
        w.rollback()


def test_failed_child_insert_rolls_back_the_filing_without_poisoning_the_connection(env):
    import psycopg2
    from worker.report_classification_store import PostgresClassificationStore
    _filing(env["owner"], 202)
    _filing(env["owner"], 203)
    s = PostgresClassificationStore(env["worker"])
    bad = _classification(202)
    bad["evidence"][-1]["snippet"] = "x" * 161                      # violates chk_rce_snippet (a CHILD row)
    with pytest.raises(psycopg2.errors.CheckViolation):
        s.save(bad)
    assert s.save(_classification(203)) == "inserted"                # same connection, same transaction
    s.commit()
    assert _counts(env["owner"], 202) == (0, 0, 0)                   # parent rolled back with its children
    r = _classification(203)
    assert _counts(env["owner"], 203) == (1, len(r["statement_periods"]), len(r["evidence"]))


def test_constraints_reject_invalid_semantics(env):
    import psycopg2
    from worker.report_classification_store import PostgresClassificationStore
    _filing(env["owner"], 204)
    s = PostgresClassificationStore(env["worker"])
    for mutate in (lambda r: r.update(document_type="quarterly_guess"),
                   lambda r: r.update(fiscal_period="Q5"),
                   lambda r: r.update(classification_status="unreadable"),       # unreadable must have no period
                   lambda r: r["statement_periods"][0].update(period_kind="instant", duration_months=3),
                   # a quarter may never rest on an inferred fiscal year-end
                   lambda r: r.update(fiscal_year_end=None, fiscal_year_end_basis="inferred_only",
                                      fiscal_year_end_inferred="03-31"),
                   lambda r: r.update(fiscal_year_end_basis="none"),                  # FYE set but not documented
                   lambda r: r.update(fiscal_year_end_inferred="03-31")):             # inferred alongside documented
        r = _classification(204)
        mutate(r)
        with pytest.raises(psycopg2.errors.CheckViolation):
            s.save(r)
    s.commit()
    assert _counts(env["owner"], 204) == (0, 0, 0)


def test_unreadable_document_persists(env):
    from worker import document_text as dt, report_classification as rc
    from worker.report_classification_store import PostgresClassificationStore
    _filing(env["owner"], 205)
    r = rc.classify(dt.from_pages(["", " "]), {"file_text": "Financial Statements as of 30.09.2025"},
                    cse_filing_id=205, sha256="c" * 64).to_dict()
    s = PostgresClassificationStore(env["worker"])
    assert s.save(r) == "inserted"
    s.commit()
    with env["owner"].cursor() as cur:
        cur.execute("select classification_status, document_type, period_end from report_document_classifications "
                    "where cse_filing_id = 205")
        assert cur.fetchone() == ("unreadable", "unreadable", None)
