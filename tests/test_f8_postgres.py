"""
F8 against REAL PostgreSQL 17. Sources: docs/F8_DESIGN.md §13, §19 (F8-2, F8-3); T-21, T-24, T-25, T-32, T-39; I-6.

A throwaway initdb cluster gets the P1 bootstrap roles, and migrations 0001-0017 are applied through the P1 runner;
every test gets a fresh database. Evidence is written ONLY through the frozen writers:
- F1 through report_filings_store.PostgresFilingStore;
- the F3 classification and the F5 issuer decision as the F6.4 tests write them;
- F5 through financial_candidates_store.PostgresCandidateStore, with F5's own timestamp snapshot (P-2);
- F6.4 through its own validate / reconcile jobs.
The recorded times are therefore the database's own. F8 then reads, as the worker or a probe role.

No network, no CSE: run with `docker run --network none`.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_f8_postgres.py
"""
import hashlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
from f8_factories import ISSUER, at, epoch_ms, feed_text, revenue_doc  # noqa: E402
from worker import report_discovery as rd  # noqa: E402
from worker.financial_asof import api, store  # noqa: E402
from worker.financial_asof.errors import Refused  # noqa: E402
from worker.financial_asof.query import AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED  # noqa: E402
from worker.financial_truth_store import jobs  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
UTC = timezone.utc
US = timedelta(microseconds=1)
PUB_A = at("2023-05-15T09:30")
PUB_B = at("2023-06-20T09:00")
F8_TABLES = ("f8_configurations", "f8_designations")


# ------------------------------------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    ec = S.start_cluster(BINDIR, tmp_path_factory.mktemp("f8_cluster"))
    yield ec
    ec.cleanup()


class Env:
    def __init__(self, cluster):
        self.cluster, self.db, self._open = cluster, S.fresh_db(cluster), []

    def conn(self, user="cse_worker", autocommit=False):
        c = S.conn(self.cluster, self.db, user, autocommit)
        self._open.append(c)
        return c

    def close(self):
        for c in self._open:
            try:
                c.close()
            except Exception:                    # already closed
                pass


@pytest.fixture
def env(cluster):
    e = Env(cluster)
    yield e
    e.close()


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
    c.commit()
    return rows


def clock(c):
    return q(c, "select clock_timestamp()")[0][0]


# ------------------------------------------------------------------------------------------------ frozen writers

def publish(w, filing, uploaded, *, authorized=None, endpoint=rd.LISTING_ENDPOINT, symbol="COMB.N0000"):
    """One CSE listing entry through F1's own store (observed now)."""
    from worker.report_filings_store import PostgresFilingStore
    observed = datetime.now(UTC)
    feed = endpoint == rd.FEED_ENDPOINT
    item = {"id": filing, "path": f"upload_report_file/369_{epoch_ms(uploaded)}.pdf", "manualDate": None,
            "uploadedDate": feed_text(uploaded) if feed else epoch_ms(uploaded),
            "authorizedDate": None if authorized is None else (feed_text(authorized) if feed
                                                               else epoch_ms(authorized)),
            "fileText": "Interim Financial Statements"}
    if feed:
        item.update(name="COMMERCIAL BANK OF CEYLON PLC", symbol="COMB")
    filing_store = PostgresFilingStore(w)
    run = filing_store.begin_run(endpoint, {"symbol": symbol} if not feed else {"fromDate": "2023-01-01"}, observed)
    obs = rd.parse_listing_item(item, endpoint, rd.FEED_BUCKET if feed else "quarterly", None if feed else symbol)
    filing_store.apply_observation(obs, run, observed)
    w.commit()
    return observed


def decide(w, filing, issuer=ISSUER, status="evidenced", basis="listing_symbol_sec_id"):
    with w.cursor() as cur:
        link_id = S.new_link(cur, filing, status, basis, issuer)
    w.commit()
    return link_id


def process(w, doc, link_id, *, last_modified=None, retrieved_at=None):
    """F3 + F5 through the frozen F5 store, with F5's own timestamp snapshot of F1's row and F2's record (P-2)."""
    from worker import financial_candidates as f5
    from worker.financial_candidates_store import PostgresCandidateStore
    with w.cursor() as cur:
        cid = S.ensure_classification(cur, doc)
        cur.execute("select uploaded_at, uploaded_at_raw, authorized_at, authorized_at_raw, path, first_seen_at "
                    "from report_filings where cse_filing_id = %s", (doc.filing,))      # as F5 reads it (not F8)
        filing_row = dict(zip(("uploaded_at", "uploaded_at_raw", "authorized_at", "authorized_at_raw", "path",
                               "first_seen_at"), cur.fetchone()))
        cur.execute("select id, issuer_id, status from filing_issuer_links where id = %s", (link_id,))
        i, issuer, status = cur.fetchone()
    result = S.pad(doc.result())
    retrieval = {"last_modified": None if last_modified is None else format_datetime(last_modified, usegmt=True),
                 "retrieved_at": retrieved_at or datetime.now(UTC)}
    result["run"]["timestamps"] = f5.timestamp_snapshot(filing_row, retrieval)
    state, run_id = PostgresCandidateStore(w).save(result, cid, {"id": i, "issuer_id": issuer, "status": status})
    w.commit()
    assert state == "inserted"
    return run_id


def reconcile_f6(env, w, designate=True):
    with w.cursor() as cur:
        cfg = jobs.configuration_from_present_runs(cur)
    w.rollback()
    jobs.register_configuration(w, cfg)
    rep = jobs.reconcile(w, cfg.configuration_id)
    assert rep["state"] == "succeeded", rep
    if designate:
        jobs.designate(env.conn("cse_migrator"), cfg.configuration_id, "owner: canonical F6 configuration (F8 tests)")
    return cfg


def configure_f8(env, w, cfg, designate=True):
    state, f8_id = store.register_configuration(w, cfg.configuration_id)
    assert state in ("registered", "already_present")
    if designate:
        store.designate(env.conn("cse_migrator"), f8_id, "owner: canonical F8 configuration (F8 tests)", "tester")
    return f8_id


def standard(env):
    """Two filings for one issuer, both backfilled now:
    - A, published 2023-05-15: an interim report, revenue 1,000;
    - B, published 2023-06-20: an amendment, revenue 1,050 for the same fact.
    F6.4 validates and reconciles them, and F8 is configured. Returns a namespace of the times and keys."""
    w = env.conn()
    t0 = clock(w)
    publish(w, 9001, PUB_A)
    a_link = decide(w, 9001)
    doc_a = revenue_doc(9001, 1, "1,000")
    run_a = process(w, doc_a, a_link, last_modified=PUB_A + timedelta(minutes=5))
    publish(w, 9002, PUB_B)
    b_link = decide(w, 9002)
    doc_b = revenue_doc(9002, 2, "1,050", doc_type="amendment", underlying="interim_financial_statements")
    run_b = process(w, doc_b, b_link, last_modified=PUB_B + timedelta(minutes=5))
    cfg = reconcile_f6(env, w)
    t_designated = clock(w)                                          # T9 designated, F8 not yet
    f8_id = configure_f8(env, w, cfg)
    t1 = clock(w)
    ef = q(w, "select distinct ef_key from financial_source_observations")
    assert len(ef) == 1
    sos = dict(q(w, "select f5_run_id::text, so_key from financial_source_observations"))
    return type("World", (), dict(w=w, t0=t0, t1=t1, t_designated=t_designated, cfg=cfg, f8_id=f8_id, ef=ef[0][0],
                                  run_a=run_a, run_b=run_b, so_a=sos[run_a], so_b=sos[run_b]))


# ------------------------------------------------------------------------------------------------ migration 0017

def test_0017_objects_owner_privileges_and_triggers(env):
    s = env.conn("postgres")
    owners = q(s, "select relname, pg_get_userbyid(relowner) from pg_class where relname = any(%s) order by 1",
               (list(F8_TABLES),))
    assert owners == [("f8_configurations", "cse_owner"), ("f8_designations", "cse_owner")]
    privileges = q(s, "select t, r, p, has_table_privilege(r, t, p) from unnest(%s::text[]) t, "
                      "unnest(array['cse_worker', 'cse_reader']) r, "
                      "unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p order by 1, 2, 3",
                   (list(F8_TABLES),))
    granted = {(t, r, p) for t, r, p, ok in privileges if ok}
    assert granted == {("f8_configurations", "cse_worker", "SELECT"), ("f8_configurations", "cse_worker", "INSERT"),
                       ("f8_designations", "cse_worker", "SELECT"), ("f8_configurations", "cse_reader", "SELECT"),
                       ("f8_designations", "cse_reader", "SELECT")}
    assert q(s, "select count(*) from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                "where c.relname = any(%s) and a.grantee = 0", (list(F8_TABLES),)) == [(0,)]          # PUBLIC
    assert q(s, "select proname, prosecdef, pg_get_userbyid(proowner), has_function_privilege('cse_worker', "
                "p.oid, 'EXECUTE') from pg_proc p where proname like 'f8\\_%%' order by 1") == [
        ("f8_configuration_guard", False, "cse_owner", False), ("f8_designation_guard", False, "cse_owner", False)]
    triggers = q(s, "select tgrelid::regclass::text, tgname from pg_trigger where not tgisinternal and "
                    "tgrelid = any(array['f8_configurations'::regclass, 'f8_designations'::regclass]) and "
                    "tgenabled = 'O' order by 1, 2")
    assert triggers == [("f8_configurations", "trg_f8c_append_only"), ("f8_configurations", "trg_f8c_guard"),
                        ("f8_configurations", "trg_f8c_no_truncate"), ("f8_designations", "trg_f8d_append_only"),
                        ("f8_designations", "trg_f8d_no_truncate"), ("f8_designations", "trg_f8d_owner_only")]
    ledger = [r[0] for r in q(s, "select filename from ops.schema_migrations order by version")]
    i = ledger.index("0016_historical_backfill_ledger.sql")
    assert ledger[i + 1] == "0017_f8_asof_configuration.sql"            # recorded by the runner, after 0016


def test_t21_f8_tables_are_append_only_and_designations_owner_only(env):
    import psycopg2
    s = standard(env)
    w = s.w
    for t in F8_TABLES:
        for stmt in (f"update {t} set recorded_at = recorded_at", f"delete from {t}", f"truncate {t}"):
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                q(w, stmt)
            w.rollback()
    own = env.conn("cse_migrator")
    for t in F8_TABLES:
        for stmt in (f"update {t} set recorded_at = recorded_at", f"delete from {t}", f"truncate {t}"):
            q(own, "set role cse_owner")
            with pytest.raises(Exception) as e:
                q(own, stmt)
            assert e.value.pgcode in (("23001", "0A000") if stmt.startswith("truncate") else ("23001",)), stmt
            own.rollback()
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):                    # no INSERT grant
        q(w, "insert into f8_designations (purpose, f8_configuration_id, note) values ('canonical', %s, %s)",
          (s.f8_id, "worker trying to designate"))
    w.rollback()
    su = env.conn("postgres")
    q(su, "grant insert on f8_designations to cse_worker")                        # even with a stray grant ...
    try:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="owner decision"):
            q(w, "insert into f8_designations (purpose, f8_configuration_id, note) values ('canonical', %s, %s)",
              (s.f8_id, "worker trying to designate"))                            # ... the guard refuses
        w.rollback()
    finally:
        q(su, "revoke insert on f8_designations from cse_worker")
    with pytest.raises(Refused) as e:
        store.designate(w, s.f8_id, "the worker through the API")
    assert e.value.reason == "owner_path_required"


def test_configuration_rows_are_content_addressed_and_checked_by_the_database(env):
    import psycopg2
    s = standard(env)
    w = s.w
    assert store.register_configuration(w, s.cfg.configuration_id) == ("already_present", s.f8_id)
    text = q(w, "select configuration_json from f8_configurations where f8_configuration_id = %s", (s.f8_id,))[0][0]
    cols = "(f8_configuration_id, selection_version, availability_version, supersession_version, knowledge_version, " \
           "f6_configuration_id, configuration_json)"
    forged = text.replace("f8.availability.1", "f8.availability.2")
    cases = (("0" * 64, "f8.availability.2"),                                  # the id is not the JSON's hash
             (hashlib.sha256(forged.encode("ascii")).hexdigest(), "f8.availability.1"))   # a column differs
    for f8_id, availability in cases:
        with pytest.raises(psycopg2.errors.CheckViolation):
            q(w, f"insert into f8_configurations {cols} values (%s, %s, %s, %s, %s, %s, %s)",
              (f8_id, "f8.selection.1", availability, "f8.supersession.1", "f8.knowledge.1",
               s.cfg.configuration_id, forged))
        w.rollback()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):                     # the F6 configuration must exist
        store.register_configuration(w, "e" * 64)
    w.rollback()
    assert q(w, "select count(*) from f8_configurations") == [(1,)]
    with pytest.raises(psycopg2.errors.CheckViolation):                          # a designation needs a real note
        q(env.conn("cse_migrator"), "set role cse_owner; insert into f8_designations (purpose, f8_configuration_id, "
                                    "note) values ('canonical', %s, 'short')", (s.f8_id,))


# ------------------------------------------------------------------------------------------------ the modes, end to end

def test_all_modes_end_to_end_on_persisted_evidence(env):
    s = standard(env)
    w = s.w
    r = api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t1)
    (fact,) = r.facts
    assert fact.state == "single_source" and [o.so_key for o in fact.visible] == [s.so_b]            # S-1
    assert [(e.so_key, e.reason) for e in fact.excluded] == [(s.so_a, "superseded_by")]
    early = api.as_of(w, issuer_id=ISSUER, mode=AVAILABLE, information_cutoff=at("2023-06-01"),
                      knowledge_horizon=s.t1)
    assert [o.so_key for o in early.facts[0].visible] == [s.so_a] and early.label == "reconstructed"
    assert api.as_of(w, issuer_id=ISSUER, mode=AVAILABLE, information_cutoff=PUB_A - US,
                     knowledge_horizon=s.t1).facts == ()
    cur = api.as_of(w, issuer_id=ISSUER, mode=CURRENT)                            # H = the query time, recorded
    assert cur.label == "retrospective_current" and cur.knowledge_horizon is not None
    assert datetime.fromisoformat(cur.knowledge_horizon) >= s.t1
    recorded = api.as_of(w, issuer_id=ISSUER, mode=KNOWN_RECORDED, information_cutoff=s.t1)
    stored = q(w, "select output_hash from financial_reconciliation_records")
    assert recorded.facts[0].f6_result.output_hash == stored[0][0]               # F6.4's own record, unchanged
    assert recorded.facts[0].state == "conflicting"                              # F6.4 has no precedence (C4)
    for result in (r, early, cur, recorded):
        assert result.verify()
    # before any of it was known: nothing, not even an exclusion; and KNOWN before F8 was designated must pin
    with pytest.raises(Refused) as e:
        api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t0)
    assert e.value.reason == "no_designated_configuration"
    assert api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t0,
                     f8_configuration_id=s.f8_id).facts == ()


def test_known_follows_the_real_recorded_times(env):
    s = standard(env)
    w = s.w
    run_recorded = q(w, "select min(recorded_at) from financial_extraction_runs")[0][0]
    first_vr, last_vr = q(w, "select min(recorded_at), max(recorded_at) from financial_validation_runs")[0]
    pin = dict(f8_configuration_id=s.f8_id)
    assert run_recorded < first_vr <= last_vr
    assert api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=run_recorded - US, **pin).facts == ()
    # the runs are known, their validation runs are not: no canonical run, no fallback, nothing listed
    assert api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=first_vr - US, **pin).facts == ()
    full = api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=last_vr, **pin)
    assert full.facts and full.facts[0].visible


def test_t24_editing_the_mutable_f1_row_changes_no_f8_result(env):
    s = standard(env)
    w = s.w
    queries = [dict(mode=KNOWN, information_cutoff=s.t1), dict(mode=AVAILABLE, information_cutoff=at("2023-12-31"),
                                                                knowledge_horizon=s.t1),
               dict(mode=CURRENT, knowledge_horizon=s.t1), dict(mode=KNOWN_RECORDED, information_cutoff=s.t1)]
    before = [api.as_of(w, issuer_id=ISSUER, **kw).result_hash for kw in queries]
    q(w, "update report_filings set uploaded_at = '2001-01-01T00:00:00Z', path = 'rewritten', "
         "first_seen_at = '2001-01-01T00:00:00Z'")
    after = [api.as_of(w, issuer_id=ISSUER, **kw).result_hash for kw in queries]
    assert after == before


def test_t25_f8_writes_nothing_and_reads_in_a_read_only_transaction(env, monkeypatch):
    import psycopg2
    s = standard(env)
    w = s.w
    tables = [r[0] for r in q(w, "select tablename from pg_tables where schemaname = 'public' order by 1")]
    reader = env.conn("postgres")

    def counts():
        return {t: q(reader, f"select count(*), coalesce(sum(hashtext(x::text)), 0) from {t} x")[0] for t in tables}
    before = counts()
    r = api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t1)
    api.as_of(w, issuer_id=ISSUER, mode=CURRENT)
    api.timeline(w, issuer_id=ISSUER, mode=AVAILABLE, cutoffs=[at("2023-06-01"), at("2023-07-01")],
                 knowledge_horizon=s.t1)
    api.explain(w, r, audit=True)
    api.availability(w, 9001, revenue_doc(9001, 1).sha, horizon=s.t1)
    assert counts() == before
    from worker.financial_asof import loader

    def writes(cur, issuer_id, **kw):
        cur.execute("insert into f8_configurations (f8_configuration_id) values ('x')")
    monkeypatch.setattr(loader, "load", writes)
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t1)
    assert w.info.transaction_status == psycopg2.extensions.TRANSACTION_STATUS_IDLE     # rolled back


def test_i6_f8_needs_no_privilege_on_any_mutable_table(env):
    """A probe role holding SELECT on the append-only tables F8 reads, and on nothing else: every F8 interface works.
    The database itself therefore shows that F8 never reads report_filings, report_discovery_runs, companies or a
    'current' view (I-6)."""
    s = standard(env)
    role = f"f8probe_{uuid.uuid4().hex[:8]}"
    su = env.conn("postgres")
    readable = ["report_filing_observations", "report_document_classifications", "filing_issuer_links",
                "financial_extraction_runs", "financial_economic_facts", "financial_source_observations",
                "financial_validation_runs", "financial_reconciliation_configurations",
                "financial_reconciliation_designations", "financial_reconciliation_batches",
                "financial_reconciliation_batch_results", "financial_reconciliation_records", "f8_configurations",
                "f8_designations", "issuers", "backfill_item_events", "backfill_request_attempts",
                "backfill_request_outcomes", "backfill_retrieval_records"]
    q(su, f"create role {role} login")
    try:
        q(su, f"grant connect on database {env.db} to {role}")
        q(su, f"grant usage on schema public to {role}")
        q(su, f"grant select on {', '.join(readable)} to {role}")
        probe = S.conn(env.cluster, env.db, role)
        try:
            for table in ("report_filings", "report_discovery_runs", "companies", "financial_validation_run_current",
                          "financial_fact_state"):
                with pytest.raises(Exception, match="permission denied"):
                    q(probe, f"select 1 from {table} limit 1")
                probe.rollback()
            r = api.as_of(probe, issuer_id=ISSUER, mode=KNOWN, information_cutoff=s.t1)
            assert r.facts[0].state == "single_source"
            assert api.as_of(probe, issuer_id=ISSUER, mode=KNOWN_RECORDED, information_cutoff=s.t1).facts
            assert api.as_of(probe, issuer_id=ISSUER, mode=AVAILABLE, information_cutoff=at("2023-12-31")).facts
            assert api.explain(probe, r, audit=True)["reproved"]
            assert api.availability(probe, 9001, revenue_doc(9001, 1).sha, horizon=s.t1).at == PUB_A
        finally:
            probe.close()
    finally:
        q(su, f"drop owned by {role}")
        q(su, f"drop role {role}")


def test_t32_t39_commit_skew_and_settled_replay(env):
    """A row whose knowledge time is at or before T but which commits after a live query appears on replay (OD-3's
    documented skew). Once T is settled, replays are byte-identical, and the live result re-proves itself from its
    own stored envelope."""
    s = standard(env)
    w = s.w
    writer = env.conn()
    with writer.cursor() as cur:                                     # a new issuer decision, not yet committed
        cur.execute("insert into filing_issuer_links (cse_filing_id, issuer_id, status, basis, rule_version, "
                    "evidence_sha256) values (9001, %s, 'evidenced', 'listing_symbol_sec_id', 'f8-test', %s) "
                    "returning decided_at", (ISSUER, "d" * 64))
        decided = cur.fetchone()[0]
    t = clock(w)
    assert t > decided
    live = api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=t)
    writer.commit()                                                  # commits after the live query
    replay = api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=t)
    assert replay.result_hash != live.result_hash                    # the documented skew (F-4), bounded by one tx
    assert [o.so_key for o in live.facts[0].visible] == [s.so_b]
    # the new decision is known at t, but no validation run with it exists at t: no fallback, nothing from 9001
    assert all(o.so_key != s.so_a for f in replay.facts for o in f.visible)
    settled = [api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=t) for _ in range(2)]
    jobs.reconcile(w, s.cfg.configuration_id)                        # later rows (after t) change nothing at t
    settled.append(api.as_of(w, issuer_id=ISSUER, mode=KNOWN, information_cutoff=t))
    assert {r.result_hash for r in settled} == {replay.result_hash}
    assert live.verify()                                             # a live result is reproduced from itself


def test_api_contract_refusals(env):
    s = standard(env)
    w = s.w
    with pytest.raises(Refused) as e:
        api.as_of(w, issuer_id=str(uuid.uuid4()), mode=CURRENT)
    assert e.value.reason == "identity_unresolved"
    with w.cursor() as cur:
        cur.execute("select 1")                                      # leave a transaction open
    with pytest.raises(Refused) as e:
        api.as_of(w, issuer_id=ISSUER, mode=CURRENT)
    assert e.value.reason == "connection_busy"
    w.rollback()
    auto = env.conn(autocommit=True)                                 # no single snapshot possible: refused
    with pytest.raises(Refused) as e:
        api.as_of(auto, issuer_id=ISSUER, mode=CURRENT)
    assert e.value.reason == "connection_autocommit"
    with pytest.raises(Refused) as e:
        api.timeline(w, issuer_id=ISSUER, mode=CURRENT, cutoffs=[s.t1])
    assert e.value.reason == "current_not_point_in_time"
    with pytest.raises(Refused) as e:
        api.availability(w, 9001, revenue_doc(9001, 1).sha, horizon=s.t1, policy="f8.availability.2")
    assert e.value.reason == "policy_not_implemented"


def test_timeline_explain_and_availability_interfaces(env):
    s = standard(env)
    w = s.w
    series = api.timeline(w, issuer_id=ISSUER, mode=AVAILABLE, knowledge_horizon=s.t1,
                          cutoffs=[at("2023-05-01"), PUB_A, PUB_B - US, PUB_B])
    assert [r.facts[0].state if r.facts else "none" for r in series] == ["none", "single_source", "single_source",
                                                                         "single_source"]
    assert [[o.so_key for o in r.facts[0].visible] for r in series[1:]] == [[s.so_a], [s.so_a], [s.so_b]]
    recorded = api.timeline(w, issuer_id=ISSUER, mode=KNOWN_RECORDED, cutoffs=[s.t_designated, s.t1],
                            f8_configuration_id=s.f8_id)                  # F8 was designated only after t_designated
    assert [len(r.facts) for r in recorded] == [1, 1]
    assert recorded[0].recorded_batch == recorded[1].recorded_batch                # the same batch in force
    with pytest.raises(Refused) as e:
        api.timeline(w, issuer_id=ISSUER, mode=KNOWN_RECORDED, cutoffs=[s.t0, s.t1], f8_configuration_id=s.f8_id)
    assert e.value.reason == "no_f6_designation"
    r = api.as_of(w, issuer_id=ISSUER, mode=CURRENT, knowledge_horizon=s.t1)
    out = api.explain(w, r, audit=True)
    assert out["reproved"] and out["configuration"]["designated_at_horizon"]["f8_configuration_id"] == s.f8_id
    (fact,) = out["facts"]
    entry = fact["visible"][0]
    assert entry["filing_observations"][0]["raw_item"]["uploadedDate"] == epoch_ms(PUB_B)          # CSE evidence
    assert entry["f5_run"]["snapshot"]["cdn_last_modified"] and entry["cse_responses"] == {
        "listing_attempts": [], "document_retrievals": []}                     # no Phase 2 ledger rows here
    assert fact["supersession"][0]["bases"] == ["S-1"]
    v = api.availability(w, 9002, revenue_doc(9002, 2).sha, horizon=s.t1)
    assert (v.at, v.precision, v.role) == (PUB_B, "instant", "base")


def test_hb1_f64_and_p2_preflights_still_pass_with_0017(env):
    from worker.financial_backfill import preflight as hb1
    from worker.financial_truth_store import preflight as f64
    from worker.market_capture import runs as p2
    w = env.conn()
    assert hb1.database_problems(w) == []
    assert f64.problems(w) == []
    assert p2.security_preflight(w, "cse_worker") == []
