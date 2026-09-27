"""
Stage F5 — secId reuse guard (B1 / I-13 / D1) against a REAL Postgres, through the actual
PostgresIssuerStore running as the RESTRICTED worker role.

A reused secId must never silently attach an unrelated security or filing to an existing
issuer. Every scenario gets its own FRESH database (0001 -> 0008 applied in order with the
documented worker grants), so insertion order can be compared: COMB first, NEWCO first,
or both in one batch.

Runs only when F5_TEST_DATABASE_URL points at a scratch Postgres 15+ server whose user may
CREATE DATABASE and CREATE ROLE; otherwise every test here is SKIPPED, never passed.
"""
import copy
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from test_f5_postgres import ADMIN_URL, COMB, MIGRATIONS, P369, REPO, _url, documented_grants, q

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set F5_TEST_DATABASE_URL to a scratch Postgres 15+ (CREATE DATABASE/ROLE)")

T1, T2 = "2026-09-26T10:00:00+00:00", "2026-09-27T10:00:00+00:00"
P999 = "cmt/upload_report_file/999_1762944976790.pdf"
COMB_FILING, NEWCO_FILING, PATH_ONLY_FILING, NEW_SEC_FILING = 60001, 60002, 60003, 60004


def company_info(symbol, security_id, sec_id, isin, name):
    b = copy.deepcopy(COMB)
    b["reqSymbolInfo"].update({"symbol": symbol, "id": security_id, "isin": isin, "name": name})
    b["reqSymbolBetaInfo"]["securityId"] = sec_id
    b["reqLogo"]["secId"] = sec_id
    return b


def comb_obs(at=T1):
    from worker import issuer_identity as ii
    return (ii.observations_from_company_info(company_info("COMB.N0000", 208, 369, "LK0053N00005",
                                                           "COMMERCIAL BANK OF CEYLON PLC"), "COMB.N0000", at, "test")
            + ii.observations_from_company_info(company_info("COMB.X0000", 396, 369, "LK0053X00003",
                                                             "COMMERCIAL BANK OF CEYLON PLC"), "COMB.X0000", at, "test"))


def newco_obs(sec_id=369, isin="LK9999N00001", name="UNRELATED NEWCO PLC", at=T2):
    from worker import issuer_identity as ii
    return ii.observations_from_company_info(company_info("NEWCO.N0000", 555, sec_id, isin, name), "NEWCO.N0000", at, "test")


@pytest.fixture(scope="module")
def fresh_db():
    """Factory: a new database per call -> (owner connection, PostgresIssuerStore as the worker role)."""
    import psycopg2
    from worker.issuer_store import PostgresIssuerStore
    role, pw = f"f5_reuse_{secrets.token_hex(4)}", secrets.token_hex(16)
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'create role "{role}" login password %s', (pw,))
        for api_role in ("anon", "authenticated"):
            cur.execute("select 1 from pg_roles where rolname = %s", (api_role,))
            if cur.fetchone() is None:
                cur.execute(f"create role {api_role} nologin")
    made, conns = [], []

    def make():
        db = f"f5_reuse_{secrets.token_hex(4)}"
        with admin.cursor() as cur:
            cur.execute(f'create database "{db}"')
        made.append(db)
        owner = psycopg2.connect(_url(ADMIN_URL, db))
        owner.autocommit = True
        conns.append(owner)
        with owner.cursor() as cur:
            cur.execute("revoke all on schema public from public")
            cur.execute(f'grant usage on schema public to "{role}"')
            cur.execute("alter default privileges in schema public grant all on tables to anon, authenticated")
            for name in MIGRATIONS:
                sql = open(os.path.join(REPO, "supabase", "migrations", name), encoding="utf-8").read()
                cur.execute(sql)
                for g in documented_grants(sql, f'"{role}"'):
                    cur.execute(g)
        for t in ("COMB.N0000", "COMB.X0000", "NEWCO.N0000"):
            q(owner, "insert into companies (ticker, company_name) values (%s, %s)", (t, t))
        for fid, path, symbols in ((COMB_FILING, P369, ["COMB.N0000", "COMB.X0000"]), (NEWCO_FILING, P369, ["NEWCO.N0000"]),
                                   (PATH_ONLY_FILING, P369, []), (NEW_SEC_FILING, P999, ["NEWCO.N0000"])):
            q(owner, "insert into report_filings (cse_filing_id, path, listing_symbols, uploaded_at, uploaded_at_raw, "
                     "first_seen_at, last_seen_at) values (%s, %s, %s, '2025-11-12T10:56:16.777+00', "
                     "'12 Nov 2025 04:26:16 PM', now(), now())", (fid, path, symbols))
        worker = psycopg2.connect(_url(ADMIN_URL, db, role, pw))
        conns.append(worker)
        return owner, PostgresIssuerStore(worker)

    try:
        yield make
    finally:
        for c in conns:
            c.close()
        with admin.cursor() as cur:
            for db in made:
                cur.execute(f'drop database "{db}" with (force)')
            cur.execute(f'drop role "{role}"')
        admin.close()


def submit(store, obs, filings=(COMB_FILING, NEWCO_FILING, PATH_ONLY_FILING)):
    store.record_observations(obs)
    store.resolve_securities()
    out = {fid: store.link_filing(fid) for fid in filings}
    store.commit()
    return out


def current_state(o):
    """The CURRENT decisions, without surrogate ids / uuids, so databases can be compared."""
    sec = q(o, """select distinct on (s.company_id) c.ticker, s.link_status, s.issuer_id is not null, s.observed_sec_ids,
                         s.reasons
                  from issuer_securities s join companies c on c.id = s.company_id order by s.company_id, s.id desc""")
    fil = q(o, """select distinct on (cse_filing_id) cse_filing_id, status, issuer_id is not null, basis, reasons
                  from filing_issuer_links order by cse_filing_id, id desc""")
    return {"securities": sorted(tuple(r) for r in sec), "filings": sorted(tuple(r) for r in fil)}


def history(o, ticker):
    return [r[0] for r in q(o, "select s.link_status from issuer_securities s join companies c on c.id = s.company_id "
                               "where c.ticker = %s order by s.id", (ticker,))]


DISPUTED = ["sec_id_identity_disputed:369"]
EXPECTED_DISPUTED_STATE = {
    "securities": [
        ("COMB.N0000", "conflict", False, [369], DISPUTED + ["isin_issuer_code_differs:NEWCO.N0000"]),
        ("COMB.X0000", "conflict", False, [369], DISPUTED + ["isin_issuer_code_differs:NEWCO.N0000"]),
        ("NEWCO.N0000", "conflict", False, [369], DISPUTED + ["isin_issuer_code_differs:COMB.N0000",
                                                               "isin_issuer_code_differs:COMB.X0000"])],
    "filings": [
        (COMB_FILING, "conflict", False, "document_path_prefix", ["listing_security_in_conflict"]),
        (NEWCO_FILING, "conflict", False, "document_path_prefix", ["listing_security_in_conflict"]),
        (PATH_ONLY_FILING, "conflict", False, "document_path_prefix", ["sec_id_identity_disputed"])]}


def test_case1_same_identity_evidence_evidences_both_share_classes_to_one_issuer(fresh_db):
    o, store = fresh_db()
    got = submit(store, comb_obs(), (COMB_FILING, PATH_ONLY_FILING))
    assert {k: (v["status"], v["basis"]) for k, v in got.items()} == {
        COMB_FILING: ("evidenced", "both"), PATH_ONLY_FILING: ("evidenced", "document_path_prefix")}
    assert q(o, "select count(*), min(cse_sec_id), min(display_name) from issuers")[0] == (1, 369, "COMB.N0000")
    assert current_state(o)["securities"] == [("COMB.N0000", "evidenced", True, [369], []),
                                              ("COMB.X0000", "evidenced", True, [369], [])]


def test_case2_original_failure_comb_then_newco_is_a_conflict_not_a_silent_merge(fresh_db):
    """The audit reproduction: COMB (369, LK0053N00005) exists; NEWCO (369, LK9999N00001) is then submitted."""
    o, store = fresh_db()
    before = submit(store, comb_obs(), (COMB_FILING, PATH_ONLY_FILING))
    issuer = q(o, "select issuer_id, cse_sec_id, display_name, created_at from issuers")
    assert len(issuer) == 1 and before[COMB_FILING]["status"] == "evidenced"
    after = submit(store, newco_obs())
    # NEWCO is NOT evidenced to COMB's issuer; nothing that resolves to 369 is evidenced any more
    assert current_state(o) == EXPECTED_DISPUTED_STATE
    assert all(v["status"] == "conflict" and v["issuer_id"] is None for v in after.values())
    assert q(o, "select count(*) from issuer_securities s join companies c on c.id = s.company_id "
                "where c.ticker = 'NEWCO.N0000' and s.issuer_id is not null")[0][0] == 0
    assert q(o, "select count(*) from filing_issuer_links where cse_filing_id = %s and status = 'evidenced'",
             (NEWCO_FILING,))[0][0] == 0
    # the existing issuer is preserved exactly; nothing was merged, re-keyed or created
    assert q(o, "select issuer_id, cse_sec_id, display_name, created_at from issuers") == issuer
    # append-only history: COMB's earlier evidenced decisions are kept, the conflict is appended
    assert history(o, "COMB.N0000") == ["evidenced", "conflict"] and history(o, "NEWCO.N0000") == ["conflict"]
    assert [r[0] for r in q(o, "select status from filing_issuer_links where cse_filing_id = %s order by id",
                            (COMB_FILING,))] == ["evidenced", "conflict"]
    # the evidence that caused the conflict is referenced by the decision and still recorded
    ev = q(o, """select o.symbol, o.cse_sec_id, o.isin from issuer_securities s join companies c on c.id = s.company_id
                 join issuer_identifier_observations o on o.id = any(s.evidence_observation_ids)
                 where c.ticker = 'NEWCO.N0000' order by o.id""")
    assert {(r[0], r[2]) for r in ev} == {("COMB.N0000", "LK0053N00005"), ("COMB.X0000", "LK0053X00003"),
                                          ("NEWCO.N0000", "LK9999N00001")} and {r[1] for r in ev} == {369}


def test_reverse_order_newco_then_comb_gives_the_same_decisions(fresh_db):
    o, store = fresh_db()
    first = submit(store, newco_obs(at=T1), (NEWCO_FILING, PATH_ONLY_FILING))
    assert first[NEWCO_FILING]["status"] == "evidenced"            # alone, NEWCO has nothing to be confused with
    submit(store, comb_obs(at=T2))
    assert current_state(o) == EXPECTED_DISPUTED_STATE
    # the issuer row created first is kept untouched (its display name is informational: as first observed)
    assert q(o, "select count(*), min(display_name) from issuers")[0] == (1, "NEWCO.N0000")
    assert history(o, "NEWCO.N0000") == ["evidenced", "conflict"] and history(o, "COMB.N0000") == ["conflict"]


def test_both_in_one_batch_creates_no_issuer_for_a_disputed_sec_id(fresh_db):
    o, store = fresh_db()
    submit(store, comb_obs() + newco_obs())
    assert current_state(o) == EXPECTED_DISPUTED_STATE
    assert q(o, "select count(*) from issuers")[0][0] == 0
    assert q(o, "select count(*) from issuer_securities where link_status = 'evidenced'")[0][0] == 0


def test_case3_conflicting_names_without_isin_through_the_store(fresh_db):
    o, store = fresh_db()
    submit(store, comb_obs())
    submit(store, newco_obs(isin=None))
    state = current_state(o)
    assert {r[0]: (r[1], r[4][-1]) for r in state["securities"]}["NEWCO.N0000"] == (
        "conflict", "name_differs:COMB.X0000")
    assert {r[0]: r[1] for r in state["filings"]} == {COMB_FILING: "conflict", NEWCO_FILING: "conflict",
                                                      PATH_ONLY_FILING: "conflict"}


def test_case4_new_sec_id_takes_the_normal_evidence_path(fresh_db):
    o, store = fresh_db()
    submit(store, comb_obs())
    got = submit(store, newco_obs(sec_id=999), (COMB_FILING, NEWCO_FILING, PATH_ONLY_FILING, NEW_SEC_FILING))
    assert {k: v["status"] for k, v in got.items()} == {COMB_FILING: "evidenced", NEWCO_FILING: "conflict",
                                                        PATH_ONLY_FILING: "evidenced", NEW_SEC_FILING: "evidenced"}
    # NEWCO_FILING's path says 369 but its listing says 999: the pre-existing sec_ids_disagree conflict
    assert q(o, "select reasons from filing_issuer_links where cse_filing_id = %s", (NEWCO_FILING,))[0][0][-1] == \
        "sec_ids_disagree"
    assert q(o, "select count(*) from issuers")[0][0] == 2
    assert got[NEW_SEC_FILING]["issuer_id"] != got[COMB_FILING]["issuer_id"]
    assert [r[1:3] for r in current_state(o)["securities"]] == [("evidenced", True)] * 3


def test_case5_conflict_is_append_only_and_does_not_silently_revert(fresh_db):
    import psycopg2
    o, store = fresh_db()
    submit(store, comb_obs())
    submit(store, newco_obs())
    n = q(o, "select (select count(*) from issuer_securities), (select count(*) from filing_issuer_links)")[0]
    # CSE later shows only COMB again: nothing new is recorded, nothing reverts
    submit(store, comb_obs(at="2026-10-01T10:00:00+00:00"))
    submit(store, [])
    assert current_state(o) == EXPECTED_DISPUTED_STATE
    assert q(o, "select (select count(*) from issuer_securities), (select count(*) from filing_issuer_links)")[0] == n
    # the worker cannot rewrite a decision or delete the evidence; the owner is stopped by the triggers
    w = store.conn
    for sql in ("update issuer_securities set link_status = 'evidenced'", "delete from issuer_identifier_observations",
                "delete from filing_issuer_links"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(w, sql)
        w.rollback()
        with pytest.raises(psycopg2.Error, match="append-only"):
            q(o, sql)
    # the table shape: a conflict must say why; an evidenced decision has an issuer and no conflict reasons
    cid, iid = q(o, "select c.id, (select issuer_id from issuers) from companies c where ticker = 'NEWCO.N0000'")[0]
    ins = ("insert into issuer_securities (company_id, issuer_id, link_status, link_basis, observed_sec_ids, "
           "evidence_observation_ids, reasons, rule_version, evidence_sha256) values (%s, %s, %s, 'shared_cse_sec_id', "
           "'{369}', '{}', %s, 'x', repeat('b', 64))")
    for args in ((cid, None, "conflict", []), (cid, iid, "evidenced", ["sec_id_identity_disputed:369"]),
                 (cid, None, "evidenced", []), (cid, iid, "conflict", ["x"])):
        with pytest.raises(psycopg2.errors.CheckViolation):
            q(o, ins, args)
    assert current_state(o) == EXPECTED_DISPUTED_STATE
