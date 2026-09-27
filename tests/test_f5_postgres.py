"""
Stage F5 persistence against a REAL Postgres (migrations 0007 + 0008), as the RESTRICTED worker role.

Runs only when F5_TEST_DATABASE_URL points at a scratch Postgres 15+ server whose
user may CREATE DATABASE and CREATE ROLE (e.g. a throwaway local container).
Otherwise every test here is SKIPPED, never passed.

The fixture creates a fresh database, simulates Supabase's default grants to the
`anon` / `authenticated` API roles, applies 0001 -> 0008 IN ORDER and runs each
migration's documented worker grant block for a throwaway login role. The stores
then run through the worker role's own connection; the owner connection is used
to seed F1 rows, to prove the database-level guards (constraints, triggers) and
to read the catalog.

The candidates come from the real F3 -> F4 -> F5 chain over the synthetic word
layer of the F4 tests (no PDF, no network).
"""
import copy
import json
import os
import re
import secrets
import sys
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

ADMIN_URL = os.environ.get("F5_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set F5_TEST_DATABASE_URL to a scratch Postgres 15+ (CREATE DATABASE/ROLE)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATIONS = ["0001_phase1_data_foundation.sql", "0002_add_open_price.sql", "0003_eod_observation_completeness.sql",
              "0004_report_filings.sql", "0005_report_classification.sql", "0007_issuers.sql",
              "0008_financial_candidates.sql"]
F5_TABLES = ("issuers", "issuer_identifier_observations", "issuer_securities", "filing_issuer_links", "financial_concepts",
             "financial_extraction_runs", "financial_statement_extracts", "financial_statement_columns",
             "financial_statement_rows", "financial_fact_candidates")
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
COMB = json.load(open(os.path.join(FIX, "multi_company", "real_companyInfoSummery_COMB_N0000.json"), encoding="utf-8"))
HNB = json.load(open(os.path.join(FIX, "multi_company", "real_companyInfoSummery_HNB_N0000.json"), encoding="utf-8"))
P369 = "cmt/upload_report_file/369_1762944976777.pdf"
P373 = "cmt/upload_report_file/373_1762944976778.pdf"


def _url(base, db=None, user=None, password=None):
    p = urlsplit(base)
    netloc = p.netloc
    if user:
        netloc = f"{user}:{password}@{netloc.split('@')[-1]}"
    return urlunsplit((p.scheme, netloc, "/" + db if db else p.path, p.query, p.fragment))


def documented_grants(sql, role):
    out = []
    for line in sql.splitlines():
        m = re.match(r"^--\s+(grant\s.+?;)", line.strip(), re.I)
        if m:
            out.append(m.group(1).replace("cse_worker", role))
    return out


@pytest.fixture(scope="module")
def env():
    import psycopg2
    db, role, pw = f"f5_accept_{secrets.token_hex(4)}", f"f5_worker_{secrets.token_hex(4)}", secrets.token_hex(16)
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'create database "{db}"')
        cur.execute(f'create role "{role}" login password %s', (pw,))
        for api_role in ("anon", "authenticated"):
            cur.execute("select 1 from pg_roles where rolname = %s", (api_role,))
            if cur.fetchone() is None:
                cur.execute(f"create role {api_role} nologin")
    owner = psycopg2.connect(_url(ADMIN_URL, db))
    owner.autocommit = True
    with owner.cursor() as cur:
        cur.execute("revoke all on schema public from public")
        cur.execute(f'grant usage on schema public to "{role}"')
        # Supabase-style default privileges: every new table is granted to the API roles
        cur.execute("alter default privileges in schema public grant all on tables to anon, authenticated")
        for name in MIGRATIONS:
            sql = open(os.path.join(REPO, "supabase", "migrations", name), encoding="utf-8").read()
            cur.execute(sql)
            for g in documented_grants(sql, f'"{role}"'):
                cur.execute(g)
    worker = psycopg2.connect(_url(ADMIN_URL, db, role, pw))
    try:
        yield {"owner": owner, "worker": worker, "role": role, "db": db}
    finally:
        worker.close()
        owner.close()
        with admin.cursor() as cur:
            cur.execute(f'drop database "{db}" with (force)')
            cur.execute(f'drop role "{role}"')
        admin.close()


def q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None


def seed(owner):
    ids = {}
    for t in ("COMB.N0000", "COMB.X0000", "HNB.N0000", "BAD.N0000", "NEW.N0000"):
        ids[t] = q(owner, "insert into companies (ticker, company_name) values (%s, %s) returning id", (t, t))[0][0]
    for fid, path, symbols in ((49384, P369, ["COMB.N0000", "COMB.X0000"]), (49385, P369, ["HNB.N0000"]),
                               (49386, None, []), (49387, P373, ["HNB.N0000"]),
                               (49388, "cmt/upload_report_file/999_1762944976779.pdf", [])):
        q(owner, "insert into report_filings (cse_filing_id, path, listing_symbols, uploaded_at, uploaded_at_raw, "
                 "authorized_at, authorized_at_raw, first_seen_at, last_seen_at) values (%s, %s, %s, "
                 "'2025-11-12T10:56:16.777+00', '12 Nov 2025 04:26:16 PM', '2025-11-12T10:59:24.124+00', "
                 "'12 Nov 2025 04:29:24 PM', now(), now())", (fid, path, symbols))
    return ids


def body_for(base, symbol, security_id, sec_id=None):
    b = copy.deepcopy(base)
    b["reqSymbolInfo"]["symbol"], b["reqSymbolInfo"]["id"] = symbol, security_id
    if sec_id is not None:
        b["reqSymbolBetaInfo"]["securityId"] = sec_id
        b["reqLogo"]["secId"] = sec_id
    return b


@pytest.fixture(scope="module")
def seeded(env):
    from worker import issuer_identity as ii
    from worker.issuer_store import PostgresIssuerStore
    companies = seed(env["owner"])
    store = PostgresIssuerStore(env["worker"])
    at = "2026-09-26T10:00:00+00:00"
    obs = (ii.observations_from_company_info(COMB, "COMB.N0000", at)
           + ii.observations_from_company_info(body_for(COMB, "COMB.X0000", 396), "COMB.X0000", at)
           + ii.observations_from_company_info(HNB, "HNB.N0000", at)
           + ii.observations_from_company_info(body_for(COMB, "BAD.N0000", 1), "BAD.N0000", at)
           + ii.observations_from_company_info(body_for(COMB, "BAD.N0000", 1, 370), "BAD.N0000", "2026-09-27T10:00:00+00:00"))
    new = store.record_observations(obs)
    again = store.record_observations(obs)
    sec = store.resolve_securities()
    sec_again = store.resolve_securities()
    store.commit()
    return {"companies": companies, "store": store, "obs_new": new, "obs_again": again, "sec": sec, "sec_again": sec_again,
            "obs_count": len(obs)}


# --- schema --------------------------------------------------------------------------------------------------

def test_migrations_apply_and_create_every_f5_table(env):
    got = {r[0] for r in q(env["owner"], "select tablename from pg_tables where schemaname = 'public'")}
    assert set(F5_TABLES) <= got and "financial_facts" not in got
    assert q(env["owner"], "select count(*) from financial_concepts where status = 'active'")[0][0] == 36


def test_no_scaled_normalised_availability_or_fact_columns_exist(env):
    cols = q(env["owner"], "select table_name, column_name from information_schema.columns where table_schema = 'public' "
                           "and table_name = any(%s)", (list(F5_TABLES),))
    bad = [c for c in cols if re.search(r"scaled|normali[sz]|available|economic|supersed|^amount$|ticker", c[1])]
    assert bad == []


def test_foreign_keys_and_indexes(env):
    fks = {(r[0], r[1]) for r in q(env["owner"], """
        select c.conrelid::regclass::text, c.confrelid::regclass::text from pg_constraint c
        where c.contype = 'f' and c.conrelid::regclass::text = any(%s)""", (list(F5_TABLES),))}
    assert {("financial_fact_candidates", "financial_concepts"), ("financial_fact_candidates", "financial_statement_rows"),
            ("financial_fact_candidates", "financial_statement_columns"), ("financial_fact_candidates", "financial_extraction_runs"),
            ("financial_extraction_runs", "report_document_classifications"), ("financial_extraction_runs", "report_filings"),
            ("financial_extraction_runs", "filing_issuer_links"), ("financial_extraction_runs", "issuers"),
            ("issuer_securities", "companies"), ("issuer_securities", "issuers"), ("filing_issuer_links", "report_filings"),
            ("filing_issuer_links", "issuers"), ("financial_statement_extracts", "financial_extraction_runs")} <= fks
    idx = {r[0] for r in q(env["owner"], "select indexname from pg_indexes where schemaname = 'public'")}
    assert {"uq_ffc_source", "idx_ffc_run", "idx_ffc_concept", "uq_issuers_cse_sec_id", "idx_fer_filing",
            "idx_filing_issuer_links_filing", "idx_issuer_securities_company"} <= idx


def test_api_roles_have_no_access_to_f5_tables_only(env):
    rows = q(env["owner"], "select table_name, grantee from information_schema.role_table_grants "
                           "where grantee in ('anon', 'authenticated') and table_schema = 'public'")
    f5_grants = [r for r in rows if r[0] in F5_TABLES]
    assert f5_grants == []
    assert any(r[0] == "companies" for r in rows)          # pre-F5 tables unchanged: that is 0006's job


def test_worker_role_can_only_select_and_insert(env):
    for t in F5_TABLES:
        privs = {r[0] for r in q(env["owner"], "select privilege_type from information_schema.role_table_grants "
                                               "where grantee = %s and table_name = %s", (env["role"], t))}
        assert privs <= {"SELECT", "INSERT"} and "SELECT" in privs, (t, privs)


# --- issuer identity (I-13) ------------------------------------------------------------------------------------

def test_observations_are_append_only_and_idempotent(env, seeded):
    assert seeded["obs_new"] == seeded["obs_count"] and seeded["obs_again"] == 0
    assert q(env["owner"], "select count(*) from issuer_identifier_observations")[0][0] == seeded["obs_count"]


def test_security_links_shared_sec_id_and_conflict(env, seeded):
    assert seeded["sec"]["issuers_new"] == 2 and seeded["sec"]["evidenced"] == 3 and seeded["sec"]["conflict"] == 1
    assert seeded["sec"]["no_evidence"] == 1                   # NEW.N0000: no row, no invented issuer
    assert seeded["sec_again"]["issuers_new"] == 0 and seeded["sec_again"]["decisions_new"] == 0
    rows = dict(q(env["owner"], """select c.ticker, s.link_status || ':' || coalesce(i.cse_sec_id::text, '-')
        from issuer_securities s join companies c on c.id = s.company_id left join issuers i on i.issuer_id = s.issuer_id"""))
    assert rows == {"COMB.N0000": "evidenced:369", "COMB.X0000": "evidenced:369", "HNB.N0000": "evidenced:373",
                    "BAD.N0000": "conflict:-"}
    # both COMB share classes -> one issuer
    assert q(env["owner"], "select count(distinct issuer_id) from issuer_securities s join companies c on c.id = s.company_id "
                           "where c.ticker like 'COMB.%'")[0][0] == 1


def test_filing_links_evidenced_conflict_unresolved_and_idempotent(env, seeded):
    store = seeded["store"]
    got = {fid: store.link_filing(fid) for fid in (49384, 49385, 49386, 49387, 49388)}
    store.commit()
    assert {k: (v["status"], v["basis"]) for k, v in got.items()} == {
        49384: ("evidenced", "both"), 49385: ("conflict", "both"), 49386: ("unresolved", "none"),
        49387: ("evidenced", "both"), 49388: ("unresolved", "document_path_prefix")}
    n = q(env["owner"], "select count(*) from filing_issuer_links")[0][0]
    assert {k: store.link_filing(k)["id"] for k in got} == {k: v["id"] for k, v in got.items()}
    store.commit()
    assert q(env["owner"], "select count(*) from filing_issuer_links")[0][0] == n
    assert q(env["owner"], "select count(*) from issuers")[0][0] == 2            # nothing created from path 999


def test_changed_evidence_appends_a_new_decision_and_keeps_the_old(env, seeded):
    from worker import issuer_identity as ii
    store = seeded["store"]
    before = store.link_filing(49387)
    # HNB.N0000 now also reports secId 369: its security link becomes a conflict, the filing too
    store.record_observations(ii.observations_from_company_info(body_for(HNB, "HNB.N0000", 209, 369), "HNB.N0000",
                                                                "2026-09-28T10:00:00+00:00"))
    store.resolve_securities()
    after = store.link_filing(49387)
    store.commit()
    assert before["status"] == "evidenced" and after["status"] == "conflict" and after["id"] > before["id"]
    hist = q(env["owner"], "select status from filing_issuer_links where cse_filing_id = 49387 order by id")
    assert [h[0] for h in hist] == ["evidenced", "conflict"]
    assert q(env["owner"], "select count(*) from issuers")[0][0] == 2             # never merged, never re-keyed


def test_issuer_guards(env, seeded):
    import psycopg2
    o = env["owner"]
    for sql in ("insert into issuers (identity_basis, cse_sec_id, created_rule_version) values ('cse_sec_id', 369, 'x')",
                "insert into issuers (identity_basis, cse_sec_id, created_rule_version) values ('provisional', 5, 'x')",
                "insert into filing_issuer_links (cse_filing_id, status, basis, rule_version, evidence_sha256) "
                "values (49386, 'evidenced', 'both', 'x', repeat('a', 64))",
                "update issuers set display_name = 'x'", "delete from issuers",
                "update issuer_identifier_observations set name = 'x'", "delete from filing_issuer_links",
                "truncate issuer_securities cascade"):
        with pytest.raises(psycopg2.Error):
            q(o, sql)


# --- candidates ------------------------------------------------------------------------------------------------

def _chain(fid):
    """Real F3 -> F4 -> F5 over the synthetic statement (no PDF)."""
    from worker import document_text, financial_candidates as f5, report_classification as rc, statement_extraction as se
    from test_statement_extraction import doc, page, pl_rows
    words = doc(page(pl_rows()))
    text = document_text.from_pages(["\n".join(l.rendered for l in se.build_lines(words.pages[0]))],
                                    extractor="pdftotext 24.02.0 (poppler) -layout")
    sha = "cd" * 32
    meta = {"cse_filing_id": fid, "path": P369, "file_text": "Interim Financial Statements", "source_buckets": ["quarterly"]}
    cls = rc.classify(text, meta, cse_filing_id=fid, sha256=sha)
    ext = se.extract_from_words(words, cls, filing_id=fid, sha256=sha)
    res = f5.build(ext, cls, filing={**meta, "uploaded_at": "2025-11-12T10:56:16.777+00:00",
                                     "uploaded_at_raw": "12 Nov 2025 04:26:16 PM"},
                   retrieval={"last_modified": "Wed, 12 Nov 2025 10:56:16 GMT", "retrieved_at": "2026-09-27T05:00:00+00:00"})
    return cls.to_dict(), res


@pytest.fixture(scope="module")
def saved(env, seeded):
    from worker.financial_candidates_store import PostgresCandidateStore, classification_id
    from worker.report_classification_store import PostgresClassificationStore
    w = env["worker"]
    cls, res = _chain(49384)
    PostgresClassificationStore(w).save(cls, 1234)
    cid = classification_id(w, cls)
    link = seeded["store"].link_filing(49384)
    store = PostgresCandidateStore(w)
    first = store.save(res, cid, link)
    second = store.save(copy.deepcopy(res), cid, link)
    w.commit()
    return {"cls": cls, "res": res, "cid": cid, "link": link, "first": first, "second": second, "store": store}


def test_candidate_persistence_round_trip(env, saved):
    assert saved["first"][0] == "inserted" and saved["second"] == ("already_present", saved["first"][1])
    o, res = env["owner"], saved["res"]
    run_id = saved["first"][1]
    counts = q(o, """select (select count(*) from financial_statement_extracts where run_id = %(r)s),
        (select count(*) from financial_statement_columns c join financial_statement_extracts s on s.id = c.statement_id where s.run_id = %(r)s),
        (select count(*) from financial_statement_rows w join financial_statement_extracts s on s.id = w.statement_id where s.run_id = %(r)s),
        (select count(*) from financial_fact_candidates where run_id = %(r)s)""", {"r": run_id})[0]
    assert counts == (len(res["statements"]), len(res["columns"]), len(res["rows"]), len(res["candidates"]))
    assert counts[3] == 28
    got = q(o, "select raw_value, parsed_value::text, sign_as_printed, reported_scale from financial_fact_candidates "
               "where run_id = %s and concept_key = 'cost_of_sales' order by id limit 1", (run_id,))[0]
    assert got == ("(40,000)", "-40000", "negative", 1000000)


def test_run_carries_versions_timestamps_and_issuer_snapshot(env, saved):
    r = q(env["owner"], """select mapper_version, vocabulary_version, builder_version, f4_extractor_version, classifier_version,
        uploaded_at_raw, cdn_last_modified_raw, path_epoch_ms, issuer_link_status, issuer_id is not null, recorded_at is not null,
        content_sha256 from financial_extraction_runs where id = %s""", (saved["first"][1],))[0]
    assert r[:11] == ("f5.map.1", "v1", "f5.1", "f4.1", "f3.1", "12 Nov 2025 04:26:16 PM", "Wed, 12 Nov 2025 10:56:16 GMT",
                      1762944976777, "evidenced", True, True)
    from worker import financial_candidates as f5
    assert r[11] == f5.content_sha256(saved["res"])


def test_complete_provenance_chain_candidate_to_filing_and_issuer(env, saved):
    rows = q(env["owner"], """
        select c.id, w.label_raw, col.end_date, col.period_class, s.statement_kind, r.document_sha256, k.classifier_version,
               f.cse_filing_id, i.cse_sec_id, c.page, c.bbox
        from financial_fact_candidates c
        join financial_statement_rows w on w.id = c.row_id
        join financial_statement_columns col on col.id = c.column_id
        join financial_statement_extracts s on s.id = w.statement_id and s.id = col.statement_id
        join financial_extraction_runs r on r.id = s.run_id and r.id = c.run_id
        join report_document_classifications k on k.id = r.classification_id
        join report_filings f on f.cse_filing_id = r.cse_filing_id
        join issuers i on i.issuer_id = r.issuer_id
        where r.id = %s""", (saved["first"][1],))
    assert len(rows) == 28
    assert all(r[5] == "cd" * 32 and r[7] == 49384 and r[8] == 369 and r[9] == 1 and len(r[10]) == 4 for r in rows)


def test_new_mapper_version_creates_a_new_run_and_keeps_the_old(env, saved, monkeypatch):
    from worker import financial_candidates as f5, financial_concepts as fc
    monkeypatch.setattr(fc, "MAPPER_VERSION", "f5.map.2")
    cls, res = _chain(49384)
    status, run2 = saved["store"].save(res, saved["cid"], saved["link"])
    env["worker"].commit()
    assert status == "inserted" and run2 != saved["first"][1]
    runs = q(env["owner"], "select mapper_version, (select count(*) from financial_fact_candidates c where c.run_id = r.id) "
                           "from financial_extraction_runs r where cse_filing_id = 49384 order by recorded_at, mapper_version")
    assert runs == [("f5.map.1", 28), ("f5.map.2", 28)]
    ids = q(env["owner"], "select min(id), max(id), count(*), count(distinct id) from financial_fact_candidates")[0]
    assert ids[2] == ids[3] == 56                         # ids never reused


def test_deterministic_repeated_ingestion(env, saved):
    cls, res = _chain(49384)
    assert saved["store"].save(res, saved["cid"], saved["link"]) == ("already_present", saved["first"][1])
    env["worker"].commit()
    assert q(env["owner"], "select count(*) from financial_extraction_runs where mapper_version = 'f5.map.1'")[0][0] == 1


def test_worker_cannot_update_or_delete_and_owner_is_stopped_by_triggers(env, saved):
    import psycopg2
    w, o = env["worker"], env["owner"]
    for sql in ("update financial_fact_candidates set parsed_value = 0", "delete from financial_fact_candidates",
                "update financial_extraction_runs set mapper_version = 'x'"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(w, sql)
        w.rollback()
    for t in ("financial_fact_candidates", "financial_statement_rows", "financial_statement_columns",
              "financial_statement_extracts", "financial_extraction_runs", "financial_concepts"):
        with pytest.raises(psycopg2.Error, match="append-only"):
            q(o, f"delete from {t}")
        with pytest.raises(psycopg2.Error, match="append-only"):
            q(o, f"truncate {t} cascade")
    with pytest.raises(psycopg2.Error, match="append-only"):
        q(o, "update financial_fact_candidates set raw_value = '0'")


# --- database-level invariants (owner inserts inside a rolled-back transaction) -----------------------------------

def _ids(o, run_id):
    return q(o, """select c.id, w.id, col.id, s.id from financial_fact_candidates c
                   join financial_statement_rows w on w.id = c.row_id
                   join financial_statement_columns col on col.id = c.column_id
                   join financial_statement_extracts s on s.id = w.statement_id
                   where c.run_id = %s and c.concept_key = 'revenue' order by c.id limit 1""", (run_id,))[0]


def _violates(env, sql, args=None):
    import psycopg2
    o = env["owner"]
    o.autocommit = False
    try:
        with pytest.raises(psycopg2.Error) as err:
            q(o, sql, args)
        assert err.value.pgcode and err.value.pgcode.startswith("23"), (err.value.pgcode, str(err.value))  # integrity class
        return err.value
    finally:
        o.rollback()
        o.autocommit = True


def _cand_sql(**over):
    base = {"concept_key": "'revenue'", "mapping_status": "'mapped'", "mapping_rule_ids": "'{revenue.1}'",
            "ambiguous_concepts": "'{}'", "period_kind": "'duration'", "period_class": "'3m'",
            "period_derivation": "'column'", "value_type": "'currency_amount'", "attribution": "'not_applicable'",
            "raw_value": "'100'", "parsed_value": "100", "representation_class": "'numeric'", "sign_as_printed": "'positive'",
            "f4_status": "'extracted'", "f4_confidence": "'high'", "cross_check": "'agree'", "page": "1",
            "bbox": "'{1,2,3,4}'", "candidate_status": "'proposed'", "value_ordinal": "9"}
    base.update(over)
    return ("insert into financial_fact_candidates (run_id, row_id, column_id, " + ", ".join(base) +
            ") values (%(run)s, %(row)s, %(col)s, " + ", ".join(base.values()) + ")")


@pytest.mark.parametrize("name,over", [
    ("I-1 concept/period kind mismatch", dict(concept_key="'total_assets'")),
    ("I-1 instant with a period class", dict(concept_key="'total_assets'", period_kind="'instant'")),
    ("I-2 dash as zero", dict(raw_value="'-'", representation_class="'dash_nil'", parsed_value="0", sign_as_printed="'zero'")),
    ("I-2 flipped parenthesised sign", dict(raw_value="'(100)'", representation_class="'parenthesised_negative'")),
    ("I-2 sign disagrees with value", dict(parsed_value="-100")),
    ("I-8 ambiguous with a concept", dict(mapping_status="'ambiguous'", ambiguous_concepts="'{eps_basic,eps_diluted}'")),
    ("I-8 mapped without a concept", dict(concept_key="null", value_type="null")),
    ("proposed but F4 unresolved", dict(f4_status="'unresolved'")),
    ("conflicting status without F4 conflict", dict(candidate_status="'conflicting'")),
    ("unknown concept", dict(concept_key="'insurance_float'")),
    ("I-11 period differs from its column", dict(period_class="'12m'")),
])
def test_candidate_constraints(env, saved, name, over):
    _, row_id, col_id, _ = _ids(env["owner"], saved["first"][1])
    _violates(env, _cand_sql(**over), {"run": saved["first"][1], "row": row_id, "col": col_id})


def test_valid_candidate_shape_is_accepted(env, saved):
    """The baseline of the parametrised cases above is itself valid: each case fails for its own change."""
    o = env["owner"]
    _, row_id, col_id, _ = _ids(o, saved["first"][1])
    o.autocommit = False
    try:
        q(o, _cand_sql(), {"run": saved["first"][1], "row": row_id, "col": col_id})
    finally:
        o.rollback()
        o.autocommit = True


def test_candidate_row_and_column_from_different_runs_are_rejected(env, saved):
    runs = [r[0] for r in q(env["owner"], "select id from financial_extraction_runs where cse_filing_id = 49384 order by mapper_version")]
    _, row_id, _, _ = _ids(env["owner"], runs[0])
    _, _, col_id, _ = _ids(env["owner"], runs[1])
    err = _violates(env, _cand_sql(), {"run": runs[0], "row": row_id, "col": col_id})
    assert "provenance chain" in str(err)


def _col_sql(**over):
    base = {"column_index": "99", "column_status": "'resolved'", "period_kind": "'duration'", "period_class": "'3m'",
            "end_date": "'2026-03-31'", "duration_months": "3", "fiscal_label_rule_id": "'x'", "role": "'current'",
            "role_trust": "'trusted'", "role_rule_id": "'x'", "reported_scope": "'group'", "canonical_scope": "'consolidated'",
            "canonical_scope_basis": "'x'", "scope_rule_id": "'x'", "audit_label_reported": "'unaudited'",
            "audit_evidence_source": "'column_header_word'", "audit_trust": "'trusted'", "audit_rule_id": "'f5.audit.v1'"}
    base.update(over)
    return ("insert into financial_statement_columns (statement_id, " + ", ".join(base) + ") values (%(s)s, "
            + ", ".join(base.values()) + ")")


def test_valid_column_shape_is_accepted(env, saved):
    o = env["owner"]
    sid = _ids(o, saved["first"][1])[3]
    o.autocommit = False
    try:
        q(o, _col_sql(), {"s": sid})
    finally:
        o.rollback()
        o.autocommit = True


@pytest.mark.parametrize("name,over", [
    ("instant with period class", dict(period_kind="'instant'", duration_months="null")),
    ("duration class disagrees with months", dict(period_class="'12m'")),
    ("unspecified with months", dict(period_class="'unspecified'")),
    ("fiscal label on instant", dict(period_kind="'instant'", period_class="null", duration_months="null", fiscal_label="'Q1'")),
    ("audit trusted with unknown label", dict(audit_label_reported="'unknown'", audit_evidence_source="'none'")),
    ("known label without a source", dict(audit_evidence_source="'none'")),
    ("unknown label with a source", dict(audit_label_reported="'unknown'", audit_trust="'untrusted'")),
    ("cover inference upgraded to an invented source", dict(audit_evidence_source="'column_level_proven'")),
    ("separate without company/bank", dict(canonical_scope="'separate'")),
    ("unstated defaulted to consolidated", dict(reported_scope="'unstated'")),
    ("bank as consolidated", dict(reported_scope="'bank'")),
    ("unknown role trusted", dict(role="'unknown'")),
])
def test_column_constraints(env, saved, name, over):
    sid = _ids(env["owner"], saved["first"][1])[3]
    _violates(env, _col_sql(**over), {"s": sid})


def test_run_must_match_its_classification_and_issuer_link(env, saved):
    o = env["owner"]
    base = q(o, "select cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id, "
                "classifier_version, filing_issuer_link_id, issuer_id from financial_extraction_runs where id = %s",
             (saved["first"][1],))[0]
    sql = ("insert into financial_extraction_runs (cse_filing_id, classification_id, document_sha256, word_extractor, "
           "f4_extractor_version, classifier_version, builder_version, mapper_version, vocabulary_version, template, "
           "template_basis, document_status, f3_period_status, counts, content_sha256, filing_issuer_link_id, issuer_id, "
           "issuer_link_status) values (%s, %s, %s, %s, %s, %s, 'f5.1', 'f5.map.9', 'v1', 'general', 'x', 'extracted', "
           "'document_only', '{}', repeat('a', 64), %s, %s, %s)")
    err = _violates(env, sql, (49385, base[4], base[1], base[2], base[3], base[5], None, None, None))
    assert "classification" in str(err)
    other_link = q(o, "select id from filing_issuer_links where cse_filing_id = 49385 limit 1")[0][0]
    err = _violates(env, sql, (base[0], base[4], base[1], base[2], base[3], base[5], other_link, None, "conflict"))
    assert "issuer snapshot" in str(err)
