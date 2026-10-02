"""
Phase 2 HB-1 (the historical-backfill ledger, migration 0016) against REAL PostgreSQL 17.

Design section 23.2 (additive, append-only for every role, least privilege, owner-only inserts, the preflight, no
SECURITY DEFINER), the records L1-L11 of section 11.4, the state machine and its evidence guard, lease liveness under
P2's global lock (sections 14.2 and 16.5), idempotency, rollback, and recovery after a dead slice. A throwaway initdb
cluster gets the P1 bootstrap roles; every migration is applied through the P1 runner into a template, and every test
gets a fresh copy. F1 / F3 / issuer / F5 / F6.4 / P2 rows are written only by their own frozen stores and jobs. No
network, no CSE, no PDF.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb1_postgres.py
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
from f63_factories import ISSUER, Doc  # noqa: E402
from test_hb1_unit import armed_decision, f2_record  # noqa: E402
from worker import report_discovery as f1  # noqa: E402
from worker.financial_backfill import LEDGER_MIGRATION, TOOL_VERSION, keys, owner, preflight, states, store  # noqa: E402
from worker.financial_truth_store import jobs, preflight as f64_preflight  # noqa: E402
from worker.market_capture import runs as p2runs  # noqa: E402
from worker.ops import migrate as mig  # noqa: E402
from worker.report_filings_store import PostgresFilingStore  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
UTC = timezone.utc
SHA_0015 = "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2"
LISTING_URL = "https://www.cse.lk/api/financials"
LISTING_INTENT = dict(request_class="json", request_host="www.cse.lk", endpoint="financials", http_method="POST",
                      url=LISTING_URL, user_agent="test-agent/1.0", params={"symbol": "COMB.N0000"})


# ------------------------------------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def cluster():
    base = tempfile.mkdtemp(prefix="hb1", dir="/tmp")           # short: Unix socket paths are limited to 107 bytes
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


class Env:
    """One fresh migrated database, and connections to it that are closed after the test."""

    def __init__(self, cluster):
        self.cluster, self.db, self._open, self._owner = cluster, S.fresh_db(cluster), [], None

    def conn(self, user="cse_worker", autocommit=False):
        c = S.conn(self.cluster, self.db, user, autocommit)
        self._open.append(c)
        return c

    def owner_conn(self):
        """One owner-path login (cse_migrator) per test, reused (the cluster has 40 connection slots)."""
        if self._owner is None:
            self._owner = self.conn("cse_migrator")
        return self._owner

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


# ------------------------------------------------------------------------------------------------ helpers

def now():
    return datetime.now(UTC)


def q(c, sql, args=None):
    try:
        with c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if cur.description else None
        c.commit()
    except Exception:
        c.rollback()
        raise
    return rows


def as_owner(env, sql, args=None):
    """One statement through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner)."""
    m = env.owner_conn()
    try:
        with m.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute(sql, args)
        m.commit()
    except Exception:
        m.rollback()
        raise


def refused(fn, *, pgcode=None, match=None):
    """The database (or the store) refused; nothing was written by this call."""
    import psycopg2
    with pytest.raises((psycopg2.Error, store.LedgerError)) as ei:
        fn()
    err = ei.value
    if pgcode:
        codes = (pgcode,) if isinstance(pgcode, str) else pgcode
        assert getattr(err, "pgcode", None) in codes, (getattr(err, "pgcode", None), str(err))
    if match:
        assert match in str(err), str(err)
    return err


def wakeup(c, trigger="manual"):
    return store.start_wakeup(c, trigger=trigger, runner_time=now(), tool_version=TOOL_VERSION, host="test-host",
                              pid=os.getpid(), rule_versions={"hb.ledger": TOOL_VERSION})


@contextmanager
def cse_slice(c):
    """One CSE slice on connection c, in the design's order: P2's lock, a wake-up, a lease; closed in reverse."""
    assert p2runs.acquire_global_lock(c)
    wid = wakeup(c)
    lid = store.open_lease(c, wid)
    try:
        yield wid, lid
    finally:
        store.release_lease(c, lid, "completed")
        store.finish_wakeup(c, wid, "completed")
        p2runs.release_global_lock(c)


def f1_run(c, endpoint, params, status="succeeded"):
    """A real F1 discovery run written by F1's own run helpers and store (as HB-U4 will compose them)."""
    st = PostgresFilingStore(c)
    summary = f1._new_summary(endpoint, params)
    run_id = st.begin_run(endpoint, params, now())
    if status == "failed":
        summary["failure_category"] = "http_failure"
    elif status == "partial":
        summary["rejected"] = [{"reason": "test rejection", "raw_item": {}}]
    f1._finish(st, run_id, summary, now())
    return run_id


def filing(c, fid):
    with c.cursor() as cur:
        S.ensure_filing(cur, fid, datetime(2024, 5, 1, tzinfo=UTC))
    c.commit()


def p2_archive_row(c, trading_date):
    """One P2 archive attempt (no body), written as P2's own run ledger allows the worker; returns its id."""
    run_id = p2runs.create_run(c, run_kind="metadata_sweep", trading_date=trading_date, capture_mode="metadata_sweep",
                               policy={}, user_agent="test-agent/1.0", tool_version="test", code_revision=None)
    seq = q(c, "select coalesce(max(sequence_no), 0) + 1 from market_source_responses where run_id = %s", (run_id,))[0][0]
    return str(q(c, "insert into market_source_responses (run_id, request_key, request_purpose, sequence_no, attempt_no, "
                    "trading_date, capture_mode, endpoint, http_method, url, user_agent, requested_at, outcome) values "
                    "(%s, 'companyInfoSummery:COMB.N0000', 'metadata_sweep', %s, 1, %s, 'metadata_sweep', "
                    "'companyInfoSummery', 'POST', 'https://www.cse.lk/api/companyInfoSummery', 'test-agent/1.0', "
                    "now(), 'network_error') returning id", (run_id, seq, trading_date))[0][0])


def observation(attempt_id, **over):
    o = {"source_endpoint": "financials", "source_field": "reqFinancial.secId", "query_symbol": "COMB.N0000",
         "symbol": "COMB.N0000", "cse_security_id": None, "cse_sec_id": 4001, "isin": None, "name": None,
         "active": None, "payload_sha256": "f" * 64, "observed_at": now(),
         "source_ref": f"backfill_request_attempts:{attempt_id}"}
    o.update(over)
    return o


def wait_until_gone(c, pid, started, timeout=10.0):
    end = time.monotonic() + timeout
    while q(c, "select hb_session_alive(%s, %s)", (pid, started))[0][0]:
        assert time.monotonic() < end, "the closed session did not end"
        time.sleep(0.05)


# ------------------------------------------------------------------------------------------------ M: the migration

def test_m1_a_clean_database_at_0015_takes_0016_exactly_once(cluster):
    """Migration 0016 applies on top of the frozen 0001-0015 state, once; re-running the runner is a no-op; the ledger
    keeps 0015 at position 14 with its frozen hash, 0006 unused, and 0016 after it."""
    name = f"hb1m_{uuid.uuid4().hex[:10]}"
    su = cluster.connect(dbname="postgres", user="postgres")
    su.autocommit = True
    with su.cursor() as cur:
        cur.execute(f"create database {name} owner cse_owner template template0")
        cur.execute(f"revoke all on database {name} from public")
        cur.execute(f"grant connect on database {name} to cse_migrator, cse_worker, cse_reader, cse_backup")
    su.close()
    m = S.conn(cluster, name, "cse_migrator")
    found = mig.discover(S.MIGDIR)
    names = [x.filename for x in found]
    try:
        first = mig.apply(m, found, target=15, log=lambda x: None)
        assert first["applied"] == names[:14] and names[13] == "0015_financial_truth_persistence.sql"
        second = mig.apply(m, found, log=lambda x: None)
        assert second["applied"] == [LEDGER_MIGRATION]
        third = mig.apply(m, found, log=lambda x: None)
        assert third["applied"] == [] and third["already_applied"] == names
        st = mig.status(m, found)
        assert st["problems"] == [] and st["pending"] == [] and st["ledger"] == "present"
    finally:
        m.close()
    s = S.conn(cluster, name, "postgres")
    try:
        rows = q(s, "select filename, sha256, applied_by, applied_as from ops.schema_migrations order by version")
    finally:
        s.close()
    ledger = [(r[0], r[1]) for r in rows]
    assert ledger[:14] == list(preflight.FROZEN_MIGRATIONS.items()) and ledger[13] == (names[13], SHA_0015)
    assert rows[14] == (LEDGER_MIGRATION, mig.file_sha256(os.path.join(S.MIGDIR, LEDGER_MIGRATION)), "cse_migrator",
                        "cse_owner")
    assert preflight.lineage_problems(ledger) == [] and not any(n.startswith("0006") for n, _ in ledger)


def test_m2_the_p1_verifier_and_every_preflight_pass_with_0016(env):
    from worker.ops import verify_server as vs
    from worker.scheduler import preflight as p3_preflight
    su = env.conn("postgres")
    rep = vs.Report()
    with su.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=S.MIGDIR)
    su.rollback()
    assert not [i for i in rep.items if i["status"] != "PASS"], rep.items
    w = env.conn()
    assert p2runs.security_preflight(w, "cse_worker") == []
    assert p3_preflight.problems(w) == []
    assert f64_preflight.problems(w) == []
    assert preflight.problems(w) == []
    # the preflight is the worker's: any other login is refused before anything else is checked
    assert [p for p in preflight.problems(env.conn("cse_backup")) if p.startswith("connected as")]


# ------------------------------------------------------------------------------------------------ S: security

def test_s1_least_privilege_ownership_no_definer_no_rls(env):
    su = env.conn("postgres")
    privs = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")

    def has(role, rel, priv):
        return q(su, "select has_table_privilege(%s, %s, %s)", (role, f"public.{rel}", priv))[0][0]
    for rel in preflight.TABLES + preflight.VIEWS:
        want = ({"SELECT", "INSERT"} if rel in preflight.WORKER_WRITE else
                {"SELECT", "INSERT", "UPDATE"} if rel in preflight.WORKER_GUARDED_UPDATE else {"SELECT"})
        assert {p for p in privs if has("cse_worker", rel, p)} == want, rel
        assert {p for p in privs if has("cse_reader", rel, p)} == {"SELECT"}, rel
        assert {p for p in privs if has("cse_backup", rel, p)} == {"SELECT"}, rel          # pg_read_all_data
        assert not has("cse_migrator", rel, "SELECT"), rel                                # NOINHERIT
    assert q(su, "select count(*) from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                 "where c.relname like 'backfill\\_%%' and a.grantee = 0") == [(0,)]           # PUBLIC: nothing
    assert q(su, "select count(*) from pg_class c join pg_roles r on r.oid = c.relowner where "
                 "c.relname like 'backfill\\_%%' and (r.rolname <> 'cse_owner' or c.relrowsecurity)") == [(0,)]
    assert q(su, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = "
                 "'public' and p.proname like 'hb\\_%%' and (p.prosecdef or p.proowner <> 'cse_owner'::regrole)") == \
        [(0,)]
    assert q(su, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = "
                 "'public' and p.proname like 'hb\\_%%'") == [(15,)]
    assert q(su, "select count(*) from information_schema.role_routine_grants where grantee = 'PUBLIC' and "
                 "routine_name like 'hb\\_%%'") == [(0,)]
    assert q(su, "select rolname from pg_roles where rolname like 'cse%%' order by 1") == \
        [("cse_backup",), ("cse_migrator",), ("cse_owner",), ("cse_reader",), ("cse_worker",)]   # no new role


def test_s2_owner_decisions_are_refused_to_the_worker_three_ways(env):
    w, m, su = env.conn(), env.owner_conn(), env.conn("postgres")
    insert = "insert into backfill_arming_decisions (armed, note) values (false, 'a worker tries to disarm')"
    refused(lambda: q(w, insert), pgcode="42501")                                  # 1. no INSERT privilege
    refused(lambda: q(w, "set role cse_owner"), pgcode="42501")                     # 2. not a member of cse_owner
    q(su, "grant insert on backfill_arming_decisions, backfill_hold_resolutions, backfill_block_acknowledgements "
          "to cse_worker")                                                          # 3. even a mistaken grant ...
    try:
        refused(lambda: q(w, insert), pgcode="42501", match="owner decision")       # ... meets the guard
        refused(lambda: q(w, "insert into backfill_hold_resolutions (hold_id, resolution, note) values "
                             "(1, 'keep_held', 'a worker tries to resolve')"), pgcode="42501", match="owner decision")
        refused(lambda: q(w, "insert into backfill_block_acknowledgements (block_id, note) values "
                             "(1, 'a worker tries to acknowledge')"), pgcode="42501", match="owner decision")
    finally:
        q(su, "revoke insert on backfill_arming_decisions, backfill_hold_resolutions, backfill_block_acknowledgements "
              "from cse_worker")
    refused(lambda: q(m, insert), pgcode=("42501", "42P01"))   # the NOINHERIT migrator cannot even see the table
    with pytest.raises(owner.OwnerPathRequired):
        owner.record_arming_as_owner(w, armed_decision(), "tester")
    with pytest.raises(owner.OwnerPathRequired):
        owner.acknowledge_block_as_owner(w, 1, "a worker tries to acknowledge")
    with pytest.raises(owner.InvalidDecision):
        owner.record_arming_as_owner(m, armed_decision(note="short"), "tester")
    aid = owner.record_arming_as_owner(m, armed_decision(), "tester")             # the owner path works
    assert store.arming_in_force(w)["id"] == aid
    assert q(w, "select approved_by from backfill_arming_decisions") == [("cse_migrator",)]


def populate(env):
    """A ledger with a row in every 0016 table, built only through the store and the owner path."""
    w, m = env.conn(), env.owner_conn()
    owner.record_arming_as_owner(m, armed_decision(), "tester")
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    path = "cmt/upload_report_file/9101_1700000000000.pdf"
    filing(w, 9101)
    doc, _ = store.ensure_item(w, keys.document(9101, path))
    store.append_event(w, doc["id"], "pending", "promote")
    with cse_slice(w) as (wid, lid):
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        a1, _ = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        store.record_outcome(w, a1, "ok", "ok", http_status=200, body=b'{"reqFinancial": []}', parse_status="json_ok",
                             spool_body_key="body-1", spool_record_key="record-1")
        a2, _ = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        store.record_outcome(w, a2, "blocked", "block", http_status=403)
        hold, _ = store.record_hold(w, observation(a1), dispute={"sec_id": 4001}, rule_version="hb.acquire.1",
                                    attempt_id=a1)
        block, _ = store.record_block(w, a2, "HTTP 403 from the API")
        store.append_event(w, listing["id"], "blocked", "record", lease_id=lid, block_id=block)
        store.claim(w, doc["id"], lid, wakeup_id=wid)
        a3, _ = store.record_intent(w, doc["id"], lid, wid, request_class="document", request_host="cdn.cse.lk",
                                    endpoint="cdn", http_method="GET", url="https://cdn.cse.lk/" + path,
                                    user_agent="test-agent/1.0")
        store.record_outcome(w, a3, "forbidden_or_missing", "terminal", http_status=403)
        rid = store.record_retrieval(w, doc["id"], lid, f2_record(
            cse_filing_id=9101, source_path=path, outcome="download_failed", failure_category="forbidden_or_missing",
            final_url=None, http_status=403, content_type=None, content_length_header=None, etag=None,
            last_modified=None, byte_length=None, sha256=None, md5=None, validation=None, retrieved_at=None,
            consumer_status="not_run", cleanup_status="deleted"), attempt_ids=[a3])
        store.append_event(w, doc["id"], "retrieval_failed", "record", lease_id=lid, retrieval_id=rid)
    owner.resolve_hold_as_owner(m, hold, "keep_held", "owner: wait for the next P2 sweep", operator="tester")
    owner.acknowledge_block_as_owner(m, block, "owner reviewed the CSE block", operator="tester")
    snap, _ = store.record_snapshot(w, rule_version="hb.coverage.1", snapshot_digest="a" * 64,
                                    stage_counts={"discovered": 1})
    store.record_anomaly(w, detector_id="P-18", detector_version="hb.anomaly.1", anomaly_class=6,
                         subject_ids={"cse_filing_id": [9101]}, counts={"filings": 1}, status="open", snapshot_id=snap)
    return w


def test_s3_every_ledger_table_is_append_only_for_every_role(env):
    import psycopg2
    w = populate(env)
    counts = {t: q(w, f"select count(*) from {t}")[0][0] for t in preflight.TABLES}
    assert all(counts.values()), counts                       # every table really has a row to protect
    col = {t: q(w, "select column_name from information_schema.columns where table_name = %s and is_identity = 'NO' "
                   "order by ordinal_position limit 1", (t,))[0][0] for t in preflight.TABLES}
    for t in preflight.TABLES:
        for stmt in (f"update {t} set {col[t]} = {col[t]}", f"delete from {t}", f"truncate {t} cascade"):
            with pytest.raises(psycopg2.Error):
                q(w, stmt)                                     # the worker: no privilege, or the guard
            with pytest.raises(psycopg2.Error):
                as_owner(env, stmt)                            # the owner: the append-only triggers
        assert q(w, f"select count(*) from {t}")[0][0] == counts[t], t
    # the owner's refusal is the append-only trigger itself, not a missing privilege
    err = refused(lambda: as_owner(env, "update backfill_holds set name = 'x'"), pgcode="23001")
    assert "append-only" in str(err)


# ------------------------------------------------------------------------------------------------ L1-L11

def test_l1_the_latest_owner_decision_is_in_force_and_no_row_is_disarmed(env):
    w, m = env.conn(), env.owner_conn()
    assert store.arming_in_force(w) is None and owner.arming_in_force_as_owner(m) is None
    first = owner.record_arming_as_owner(m, armed_decision(), "tester")
    got = store.arming_in_force(w)
    assert got["id"] == first and got["armed"] and got["armed_stages"] == ["HB-S2"]
    assert got["window_first_date"] == date(2021, 4, 1) and got["user_agent"].startswith("test-agent")
    second = owner.record_arming_as_owner(m, owner.ArmingDecision.disarm("owner disarm: pause after review"), "tester")
    assert store.arming_in_force(w)["id"] == second and not store.arming_in_force(w)["armed"]
    assert owner.arming_in_force_as_owner(m)["id"] == second
    # an armed row without the release-gate facts is refused by the database itself
    refused(lambda: as_owner(env, "insert into backfill_arming_decisions (armed, armed_stages, note) values "
                                  "(true, '{HB-S2}', 'armed without any facts')"), pgcode="23514")
    refused(lambda: as_owner(env, "insert into backfill_arming_decisions (armed, armed_stages, note) values "
                                  "(false, '{HB-S9}', 'an unknown stage in a disarm')"), pgcode="23514")


def test_l2_work_items_are_unique_by_natural_key_and_idempotent(env):
    w = env.conn()
    filing(w, 9101)
    run = S.persist(w, Doc(9102).one())["run_id"]
    subjects = [keys.feed_window(2021, 4), keys.listing("COMB.N0000"),
                keys.document(9101, "cmt/upload_report_file/9101_1700000000000.pdf"), keys.link_pass(1),
                keys.validate(run), keys.reconcile(), keys.reconcile(ISSUER), keys.audit(1)]
    for s in subjects:
        a, created = store.ensure_item(w, s)
        b, again = store.ensure_item(w, s)
        assert created and not again and a == b and a["natural_key"] == s["natural_key"], s
        assert [e["state"] for e in store.events(w, a["id"])] == [sorted(states.FIRST_STATES[s["item_kind"]])[0]]
    none, _ = store.ensure_item(w, keys.document(9101, None), first_state="excluded", reason="no_document")
    assert none["natural_key"] == "document:9101:none" and store.current_state(w, none["id"])["state"] == "excluded"
    # duplicate work is impossible even without the store, and a key must be its subject's
    refused(lambda: q(w, "insert into backfill_work_items (item_kind, natural_key, sequence_no) values "
                         "('link_pass', 'link_pass:1', 1)"), pgcode="23505")
    for cols, vals in ((("item_kind", "natural_key", "sequence_no"), ("link_pass", "link_pass:2", 3)),
                       (("item_kind", "natural_key"), ("listing", "listing:")),
                       (("item_kind", "natural_key", "query_symbol"), ("listing", "listing:LOLC.N0000", "COMB.N0000")),
                       (("item_kind", "natural_key", "query_symbol"), ("listing", "listing:comb.n0000", "comb.n0000")),
                       (("item_kind", "natural_key", "window_month"), ("feed_window", "feed_window:2021-04",
                                                                       date(2021, 4, 2))),
                       (("item_kind", "natural_key", "cse_filing_id", "path_sha256"),
                        ("document", "document:9101:" + "A" * 64, 9101, "A" * 64))):
        refused(lambda: q(w, f"insert into backfill_work_items ({', '.join(cols)}) values "
                             f"({', '.join(['%s'] * len(vals))})", vals), pgcode="23514")
    refused(lambda: store.ensure_item(w, keys.document(999999, "x.pdf")), pgcode="23503")   # an unknown filing
    assert q(w, "select count(*) from backfill_work_items i where not exists (select 1 from backfill_item_events e "
                "where e.item_id = i.id)") == [(0,)]                            # never an item without its first event


def test_l3_the_database_enforces_the_state_machine(env):
    w = env.conn()
    assert {tuple(r) for r in q(w, "select item_kind, from_state, to_state, action from backfill_item_transitions")} \
        == states.TRANSITIONS
    link, _ = store.ensure_item(w, keys.link_pass(1))
    refused(lambda: store.append_event(w, link["id"], "requesting", "claim"), pgcode="23514",
            match="illegal transition")                                       # non-CSE work has no flight
    store.append_event(w, link["id"], "succeeded", "record", details={"filings_linked": 0})
    refused(lambda: store.append_event(w, link["id"], "failed", "record", reason="no"), match="illegal transition")
    refused(lambda: store.append_event(w, link["id"], "pending", "requeue", reason="short"), pgcode="23514")
    store.append_event(w, link["id"], "pending", "requeue", reason="operator: re-run after late evidence")
    refused(lambda: q(w, "insert into backfill_item_events (item_id, seq, state, action) values (%s, 9, 'failed', "
                         "'record')", (link["id"],)), match="next event must be seq")
    # a listing never succeeds without a request in flight
    lst, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    refused(lambda: store.append_event(w, lst["id"], "succeeded", "record"), match="illegal transition")
    # the first event must be a legal creation; an excluded document names an exclusion reason
    filing(w, 9101)
    refused(lambda: q(w, "with i as (insert into backfill_work_items (item_kind, natural_key, cse_filing_id) values "
                         "('document', 'document:9101:none', 9101) returning id) insert into backfill_item_events "
                         "(item_id, seq, state, action, reason) select id, 1, 'excluded', 'create', 'too_old' from i"),
            pgcode="23514")                                                    # and the item went with it
    item = q(w, "insert into backfill_work_items (item_kind, natural_key, cse_filing_id) values ('document', "
                "'document:9101:none', 9101) returning id")[0][0]
    refused(lambda: q(w, "insert into backfill_item_events (item_id, seq, state, action) values (%s, 1, 'persisted', "
                         "'create')", (item,)), match="illegal transition")
    # the rule data is immutable, even for the owner
    refused(lambda: as_owner(env, "update backfill_item_transitions set action = 'claim'"), pgcode="23001")
    refused(lambda: as_owner(env, "insert into backfill_item_transitions values ('document', 'failed', "
                                  "'persisted', 'promote')"))


def test_l3_discovery_states_name_their_own_f1_run(env):
    w = env.conn()
    feed, _ = store.ensure_item(w, keys.feed_window(2021, 4))
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    with cse_slice(w) as (wid, lid):
        store.claim(w, feed["id"], lid, wakeup_id=wid)
        april = {"fromDate": "2021-04-01", "toDate": "2021-04-30"}
        good = f1_run(w, f1.FEED_ENDPOINT, april)
        may = f1_run(w, f1.FEED_ENDPOINT, {"fromDate": "2021-05-01", "toDate": "2021-05-31"})
        failed = f1_run(w, f1.FEED_ENDPOINT, april, status="failed")
        listing_run = f1_run(w, f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"})
        refused(lambda: store.append_event(w, feed["id"], "succeeded", "record", lease_id=lid),
                match="needs an F1 run")
        refused(lambda: store.append_event(w, feed["id"], "succeeded", "record", lease_id=lid, f1_run_id=may),
                match="is not this item's request")
        refused(lambda: store.append_event(w, feed["id"], "succeeded", "record", lease_id=lid, f1_run_id=listing_run),
                match="is not this item's request")
        refused(lambda: store.append_event(w, feed["id"], "succeeded", "record", lease_id=lid, f1_run_id=failed),
                match="whose own status is succeeded")
        store.append_event(w, feed["id"], "succeeded", "record", lease_id=lid, f1_run_id=good)
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        other = f1_run(w, f1.LISTING_ENDPOINT, {"symbol": "LOLC.N0000"})
        refused(lambda: store.append_event(w, listing["id"], "partial", "record", lease_id=lid, f1_run_id=other),
                match="is not this item's request")
        partial = f1_run(w, f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"}, status="partial")
        store.append_event(w, listing["id"], "partial", "record", lease_id=lid, f1_run_id=partial)
    assert store.state_counts(w) == {("feed_window", "succeeded"): 1, ("listing", "partial"): 1}


def test_l3_document_and_f6_states_name_consistent_evidence(env):
    w, m = env.conn(), env.owner_conn()
    d = Doc(9201, doc=21).one()                     # distinct documents: F6.4 selects one run per document SHA-256
    p = S.persist(w, d)
    other = S.persist(w, Doc(9202, doc=22).one())
    doc, _ = store.ensure_item(w, keys.document(9201, "cmt/upload_report_file/9201_1700000000000.pdf"))
    refused(lambda: store.append_event(w, doc["id"], "persisted", "promote"), match="names its F5 run")
    refused(lambda: store.append_event(w, doc["id"], "persisted", "promote", f5_run_id=other["run_id"]),
            match="is not this item's")
    refused(lambda: store.append_event(w, doc["id"], "persisted", "promote", f5_run_id=p["run_id"],
                                       classification_id=other["classification_id"]), match="classification")
    store.append_event(w, doc["id"], "persisted", "promote", f5_run_id=p["run_id"],
                       classification_id=p["classification_id"], issuer_link_id=p["link_id"])
    refused(lambda: store.append_event(w, doc["id"], "validated", "promote", f5_run_id=p["run_id"]),
            match="current canonical validation run")
    assert jobs.validate(w, p["run_id"])["state"] == "succeeded"
    assert jobs.validate(w, other["run_id"])["state"] == "succeeded"
    vrk = q(w, "select validation_run_key from financial_validation_run_current where f5_run_id = %s",
            (p["run_id"],))[0][0]
    other_vrk = q(w, "select validation_run_key from financial_validation_run_current where f5_run_id = %s",
                  (other["run_id"],))[0][0]
    refused(lambda: store.append_event(w, doc["id"], "validated", "promote", f5_run_id=p["run_id"],
                                       validation_run_key=other_vrk), match="not the named F5 run's")
    store.append_event(w, doc["id"], "validated", "promote", f5_run_id=p["run_id"], validation_run_key=vrk)
    refused(lambda: store.append_event(w, doc["id"], "reconciled", "promote", f5_run_id=p["run_id"],
                                       validation_run_key=vrk), match="designated configuration")
    with w.cursor() as cur:
        cfg = jobs.configuration_from_present_runs(cur)
    w.rollback()
    jobs.register_configuration(w, cfg)
    jobs.designate(m, cfg.configuration_id, "owner designation for the HB-1 test", "tester")
    rec = jobs.reconcile(w, cfg.configuration_id)
    assert rec["state"] == "succeeded", rec
    store.append_event(w, doc["id"], "reconciled", "promote", f5_run_id=p["run_id"], validation_run_key=vrk)
    # M4: only a new issuer decision (or upload time) makes the canonical validation stale
    refused(lambda: store.append_event(w, doc["id"], "needs_validation", "promote", f5_run_id=p["run_id"]),
            match="no current canonical validation run")
    with w.cursor() as cur:
        S.new_link(cur, 9201, issuer_id=ISSUER, tag="late evidence")
    w.commit()
    store.append_event(w, doc["id"], "needs_validation", "promote", f5_run_id=p["run_id"])
    # validate items name their own validate job, in a matching final state
    v, _ = store.ensure_item(w, keys.validate(p["run_id"]))
    again = jobs.validate(w, p["run_id"])
    assert again["state"] == "succeeded"
    refused(lambda: store.append_event(w, v["id"], "succeeded", "record"), match="F6 job")
    refused(lambda: store.append_event(w, v["id"], "succeeded", "record", f6_job_id=rec["job_id"]),
            match="is not this item's")
    refused(lambda: store.append_event(w, v["id"], "failed", "record", f6_job_id=again["job_id"]),
            match="matching final state")
    store.append_event(w, v["id"], "succeeded", "record", f6_job_id=again["job_id"])
    # reconcile items name a reconcile job of their own scope
    all_issuers, _ = store.ensure_item(w, keys.reconcile())
    one_issuer, _ = store.ensure_item(w, keys.reconcile(ISSUER))
    store.append_event(w, all_issuers["id"], "succeeded", "record", f6_job_id=rec["job_id"])
    refused(lambda: store.append_event(w, one_issuer["id"], "succeeded", "record", f6_job_id=rec["job_id"]),
            match="is not this item's")
    # an audit names its coverage snapshot
    au, _ = store.ensure_item(w, keys.audit(1))
    refused(lambda: store.append_event(w, au["id"], "succeeded", "record"), match="coverage snapshot")
    snap, _ = store.record_snapshot(w, rule_version="hb.coverage.1", snapshot_digest="b" * 64, stage_counts={})
    store.append_event(w, au["id"], "succeeded", "record", snapshot_id=snap)
    # evidence of another kind of work is refused
    refused(lambda: store.append_event(w, all_issuers["id"], "pending", "requeue", reason="operator: wrong evidence",
                                       f5_run_id=p["run_id"]), match="another kind of work")


def test_l4_intents_precede_requests_inside_the_live_slice_and_outcomes_are_written_once(env):
    w, other = env.conn(), env.conn()
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    filing(w, 9101)
    doc, _ = store.ensure_item(w, keys.document(9101, "cmt/upload_report_file/9101_1700000000000.pdf"))
    store.append_event(w, doc["id"], "pending", "promote")
    with cse_slice(w) as (wid, lid):
        refused(lambda: store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT), match="state requesting")
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        a1, n1 = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        assert n1 == 1
        refused(lambda: store.record_intent(w, listing["id"], lid, wid,
                                            **dict(LISTING_INTENT, endpoint="getFinancialAnnouncement")),
                match="cannot record")
        refused(lambda: store.record_intent(w, listing["id"], lid, wid, **dict(
            LISTING_INTENT, request_host="example.org", url="https://example.org/api/financials")), pgcode="23514")
        refused(lambda: store.record_intent(w, listing["id"], lid, wid,
                                            **dict(LISTING_INTENT, headers={"cookie": "x"})), pgcode="23514")
        refused(lambda: store.record_intent(other, listing["id"], lid, wid, **LISTING_INTENT),
                match="not live in this session")                            # another session, same lease
        refused(lambda: store.record_outcome(w, a1, "ok", "ok", http_status=200), match="archived body")
        body = b'{"reqFinancial": [{"secId": 4001}]}'
        sha = store.record_outcome(w, a1, "ok", "ok", http_status=200, body=body, parse_status="json_ok",
                                   requested_at=now(), observed_at=now(), response_bytes=len(body),
                                   spool_body_key="body-1", spool_record_key="record-1")
        assert sha == hashlib.sha256(body).hexdigest()
        refused(lambda: store.record_outcome(w, a1, "server_error", "retryable", http_status=500), pgcode="23505")
        a2, n2 = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        assert n2 == 2
        refused(lambda: store.record_outcome(w, a2, "unrecorded", "unrecorded"), match="only after lease")
        refused(lambda: store.record_outcome(other, a2, "timeout", "retryable"), match="only the live slice")
        refused(lambda: store.record_outcome(w, a2, "blocked", "block", http_status=200), pgcode="23514")
        store.record_outcome(w, a2, "timeout", "retryable", error="ReadTimeout on /tmp/cse_f2_9_x/doc.pdf")
        assert "cse_f2_" not in q(w, "select error from backfill_request_outcomes where attempt_id = %s", (a2,))[0][0]
        refused(lambda: q(w, "insert into backfill_request_attempts (item_id, attempt_no, lease_id, wakeup_id, "
                             "request_class, request_host, endpoint, http_method, url, user_agent) values (%s, 9, %s, "
                             "%s, 'json', 'www.cse.lk', 'financials', 'POST', %s, 'ua')",
                             (listing["id"], lid, wid, LISTING_URL)), match="numbered consecutively")
        q(w, "insert into backfill_request_attempts (item_id, attempt_no, lease_id, wakeup_id, request_class, "
             "request_host, endpoint, http_method, url, user_agent, intended_at) values (%s, 3, %s, %s, 'json', "
             "'www.cse.lk', 'financials', 'POST', %s, 'ua', '2001-01-01')", (listing["id"], lid, wid, LISTING_URL))
        assert q(w, "select intended_at > now() - interval '1 hour' from backfill_request_attempts where item_id = %s "
                    "and attempt_no = 3", (listing["id"],)) == [(True,)]          # database time, never the client's
        # a document attempt never archives a body
        store.claim(w, doc["id"], lid, wakeup_id=wid)
        a4, _ = store.record_intent(w, doc["id"], lid, wid, request_class="document", request_host="cdn.cse.lk",
                                    endpoint="cdn", http_method="GET",
                                    url="https://cdn.cse.lk/cmt/upload_report_file/9101_1700000000000.pdf",
                                    user_agent="test-agent/1.0")
        refused(lambda: store.record_outcome(w, a4, "ok", "ok", http_status=200, body=b"{}", parse_status="json_ok",
                                             spool_body_key="b", spool_record_key="r"), match="never archived")
        store.record_outcome(w, a4, "ok", "ok", http_status=200, response_bytes=1234)
    refused(lambda: q(w, "update backfill_request_attempts set url = url"), pgcode="42501")
    refused(lambda: as_owner(env, "update backfill_request_outcomes set error = 'x'"), pgcode="23001")
    assert [a["outcome"] for a in store.attempts(w, listing["id"])] == ["ok", "timeout", None]


def test_l5_json_bodies_are_exact_and_never_documents(env):
    w = env.conn()
    body = b'{"reqFinancialAnnouncemnets": []}'
    with w.cursor() as cur:
        sha = store.archive_body_in(cur, body)
        assert store.archive_body_in(cur, body) == sha                       # content-addressed, stored once
    w.commit()
    assert q(w, "select count(*), bool_and(decode(body_base64, 'base64') = %s) from backfill_response_bodies",
             (body,)) == [(1, True)]
    import base64
    b64 = base64.b64encode(body).decode()
    refused(lambda: q(w, "insert into backfill_response_bodies (body_sha256, body_bytes, body_base64) values "
                         "(%s, %s, %s)", ("0" * 64, len(body), b64)), pgcode="23514")
    refused(lambda: q(w, "insert into backfill_response_bodies (body_sha256, body_bytes, body_base64) values "
                         "(%s, %s, %s)", (hashlib.sha256(body + b" ").hexdigest(), len(body) + 1, b64)),
            pgcode="23514")
    pdf = b"%PDF-1.7\n%%EOF\n"
    with pytest.raises(Exception) as ei:
        with w.cursor() as cur:
            store.archive_body_in(cur, pdf)
    w.rollback()
    assert getattr(ei.value, "pgcode", None) == "23514"


def test_l6_retrieval_records_belong_to_the_in_flight_item_and_hold_no_temporary_path(env):
    w = env.conn()
    path = "cmt/upload_report_file/9301_1700000000000.pdf"
    d = Doc(9301).one()
    p = S.persist(w, d)
    doc, _ = store.ensure_item(w, keys.document(9301, path))
    store.append_event(w, doc["id"], "pending", "promote")
    rec = f2_record(cse_filing_id=9301, source_path=path, final_url="https://cdn.cse.lk/" + path, sha256=d.sha)
    with cse_slice(w) as (wid, lid):
        refused(lambda: store.record_retrieval(w, doc["id"], lid, rec), match="not in flight")
        store.claim(w, doc["id"], lid, wakeup_id=wid)
        a, _ = store.record_intent(w, doc["id"], lid, wid, request_class="document", request_host="cdn.cse.lk",
                                   endpoint="cdn", http_method="GET", url="https://cdn.cse.lk/" + path,
                                   user_agent="test-agent/1.0")
        store.record_outcome(w, a, "ok", "ok", http_status=200, response_bytes=1234)
        refused(lambda: store.record_retrieval(w, doc["id"], lid, dict(rec, source_path="cmt/other.pdf")),
                match="path version")
        refused(lambda: store.record_retrieval(w, doc["id"], lid, rec, attempt_ids=[a + 1000]),
                match="does not belong")
        refused(lambda: q(w, "insert into backfill_retrieval_records (item_id, lease_id, cse_filing_id, role, "
                             "cdn_object_key, outcome, consumer_status, cleanup_status, cleanup_error) values (%s, %s, "
                             "9301, 'primary', %s, 'cleanup_failed', 'not_run', 'failed', "
                             "'could not delete /tmp/cse_f2_9301_x')", (doc["id"], lid, path)), pgcode="23514")
        bad = store.record_retrieval(w, doc["id"], lid, dict(rec, outcome="consumer_failed", consumer_status="failed",
                                                              consumer_error="TextExtractionError: x"),
                                     attempt_ids=[a])
        rid = store.record_retrieval(w, doc["id"], lid, rec, attempt_ids=[a], leftover_entries=0)
        store.append_event(w, doc["id"], "processing", "record", lease_id=lid)
        refused(lambda: store.append_event(w, doc["id"], "persisted", "record", lease_id=lid, f5_run_id=p["run_id"],
                                           retrieval_id=bad), match="succeeded retrieval record")
        refused(lambda: store.append_event(w, doc["id"], "persisted", "record", lease_id=lid, f5_run_id=p["run_id"]),
                match="succeeded retrieval record")
        store.append_event(w, doc["id"], "persisted", "record", lease_id=lid, f5_run_id=p["run_id"], retrieval_id=rid,
                           classification_id=p["classification_id"])
    row = q(w, "select cdn_object_key, document_sha256, consumer_error_class from backfill_retrieval_records where "
               "id = %s", (bad,))[0]
    assert row == (path, d.sha, "TextExtractionError")
    assert store.current_state(w, doc["id"])["state"] == "persisted"


def test_l7_holds_are_idempotent_and_resolved_only_by_the_owner(env):
    w, m = env.conn(), env.owner_conn()
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    with cse_slice(w) as (wid, lid):
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        a, _ = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        store.record_outcome(w, a, "ok", "ok", http_status=200, body=b'{"reqFinancial": [{"secId": 4001}]}',
                             parse_status="json_ok", spool_body_key="b", spool_record_key="r")
        dispute = {"sec_id": 4001, "reasons": ["identity_evidence_insufficient"]}
        h, created = store.record_hold(w, observation(a), dispute=dispute, rule_version="hb.acquire.1", attempt_id=a,
                                       wakeup_id=wid)
        assert created and store.record_hold(w, observation(a), dispute=dispute, rule_version="hb.acquire.1",
                                             attempt_id=a) == (h, False)
        refused(lambda: store.record_hold(w, observation(a, query_symbol="LOLC.N0000", payload_sha256="e" * 64),
                                          dispute=dispute, rule_version="hb.acquire.1", attempt_id=a),
                match="same query symbol")
        refused(lambda: store.record_hold(w, observation(a, payload_sha256="d" * 64), dispute=dispute,
                                          rule_version="hb.acquire.1"), pgcode="23514")      # no response reference
        refused(lambda: store.record_hold(w, observation(a, payload_sha256="c" * 64), dispute=dispute,
                                          rule_version="f5.issuer.2", attempt_id=a), pgcode="23514")
        store.append_event(w, listing["id"], "succeeded", "record", lease_id=lid, hold_id=h,
                           f1_run_id=f1_run(w, f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"}))
    p2 = p2_archive_row(w, date(2026, 10, 3))
    ie2 = observation(None, source_endpoint="companyInfoSummery", source_field="reqLogo.secId", payload_sha256="9" * 64,
                      source_ref=f"market_source_responses:{p2}")
    h2, _ = store.record_hold(w, ie2, dispute=dispute, rule_version="hb.acquire.1", p2_response_id=p2)
    refused(lambda: store.record_hold(w, dict(ie2, source_endpoint="financials", payload_sha256="8" * 64),
                                      dispute=dispute, rule_version="hb.acquire.1", p2_response_id=p2),
            match="companyInfoSummery")
    with pytest.raises(owner.OwnerPathRequired):
        owner.resolve_hold_as_owner(w, h, "keep_held", "a worker tries to resolve")
    with pytest.raises(owner.InvalidDecision):
        owner.resolve_hold_as_owner(m, h, "clear_dispute", "never a resolution of Phase 2")
    owner.resolve_hold_as_owner(m, h, "keep_held", "owner: wait for the next P2 sweep", operator="tester")
    owner.resolve_hold_as_owner(m, h, "acquire_evidence", "owner: observe companyInfoSummery next sweep")
    state = {r["hold_id"]: r for r in store.hold_state(w)}
    assert state[h]["resolution"] == "acquire_evidence" and state[h2]["resolution"] is None


def test_l8_a_lease_is_live_only_in_the_session_that_holds_p2s_lock(env):
    w, other = env.conn(), env.conn()
    wid = wakeup(w)
    refused(lambda: store.open_lease(w, wid), match="global CSE lock")
    assert p2runs.acquire_global_lock(w)
    assert store.holds_cse_lock(w) and not store.holds_cse_lock(other)
    lid = store.open_lease(w, wid)
    refused(lambda: store.open_lease(w, wid), pgcode="23505")                 # one CSE slice at a time
    owid = wakeup(other)
    refused(lambda: store.open_lease(other, owid), match="global CSE lock")
    refused(lambda: store.open_lease(other, wid), match="global CSE lock")
    assert store.heartbeat_lease(w, lid) is not None
    refused(lambda: q(other, "update backfill_leases set heartbeat_at = now() where id = %s", (lid,)),
            match="only its holder")
    refused(lambda: q(w, "update backfill_leases set heartbeat_at = heartbeat_at - interval '1 hour' where id = %s",
                      (lid,)), match="backwards")
    refused(lambda: q(w, "update backfill_leases set holder_pid = 1 where id = %s", (lid,)), match="identity")
    refused(lambda: q(w, "update backfill_leases set state = 'expired', released_at = now(), expired_by_wakeup = %s "
                         "where id = %s", (wid, lid)), match="cannot expire its own")
    refused(lambda: q(other, "update backfill_leases set state = 'expired', released_at = now(), "
                             "expired_by_wakeup = %s where id = %s", (owid, lid)), match="only a slice holding")
    assert store.active_lease(other)["id"] == lid
    assert store.release_lease(w, lid, "completed")
    refused(lambda: q(w, "update backfill_leases set result = 'x' where id = %s", (lid,)), match="immutable")
    refused(lambda: q(w, "delete from backfill_leases where id = %s", (lid,)), pgcode="42501")
    lid2 = store.open_lease(w, wid)
    p2runs.release_global_lock(w)
    refused(lambda: store.heartbeat_lease(w, lid2), match="only its holder")     # the lock is gone: no longer live
    # wake-ups: only the recording session refreshes or releases its own
    refused(lambda: q(other, "update backfill_wakeups set heartbeat_at = now() where id = %s", (wid,)),
            match="another session")
    refused(lambda: q(w, "update backfill_wakeups set tool_version = 'x' where id = %s", (wid,)), match="identity")
    assert store.heartbeat_wakeup(w, wid) is not None and store.finish_wakeup(w, wid, "completed")
    refused(lambda: q(w, "update backfill_wakeups set result = 'x' where id = %s", (wid,)), match="immutable")
    skipped = store.record_skipped_wakeup(other, trigger="timer", runner_time=now(), tool_version=TOOL_VERSION,
                                          result="skipped", details={"reason": "P2's lock is busy"})
    assert store.recent_wakeups(other, 3)[0]["id"] == skipped and store.recent_wakeups(other, 3)[0]["state"] == "skipped"
    refused(lambda: q(w, "insert into backfill_wakeups (state, trigger_kind, runner_time, tool_version) values "
                         "('expired', 'timer', now(), 'x')"), match="active or skipped")


def test_l8_in_flight_work_is_recorded_only_by_the_live_slice(env):
    """Claims and in-flight outcomes need the lease live in the writing session: another session is refused, and so is
    the holder itself once it no longer holds P2's lock."""
    w, other = env.conn(), env.conn()
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    feed, _ = store.ensure_item(w, keys.feed_window(2021, 4))
    assert p2runs.acquire_global_lock(w)
    wid = wakeup(w)
    lid = store.open_lease(w, wid)
    refused(lambda: store.claim(other, listing["id"], lid), match="live in this session")
    store.claim(w, listing["id"], lid, wakeup_id=wid)
    refused(lambda: store.append_event(other, listing["id"], "retry_wait", "record", lease_id=lid,
                                       reason="ReadTimeout"), match="live in this session")
    p2runs.release_global_lock(w)                                              # the lease is no longer live
    refused(lambda: store.claim(w, feed["id"], lid, wakeup_id=wid), match="live in this session")
    refused(lambda: store.append_event(w, listing["id"], "retry_wait", "record", lease_id=lid, reason="ReadTimeout"),
            match="live in this session")
    assert store.current_state(w, listing["id"])["state"] == "requesting"
    assert store.current_state(w, feed["id"])["state"] == "pending"


def test_l8_a_dead_slice_is_expired_and_its_work_recovered_by_the_next_slice(env):
    """The crash-point rows of design section 15.3 for an in-flight request: the intent was written, the slice died
    before the outcome. While the holder lives nobody takes over; once its session ends, PostgreSQL frees P2's lock,
    and the next slice expires the lease, closes the attempt 'unrecorded' and abandons the item, which then resumes
    with its attempt count."""
    a, b = env.conn(), env.conn()
    listing, _ = store.ensure_item(a, keys.listing("COMB.N0000"))
    assert p2runs.acquire_global_lock(a)
    wa = wakeup(a)
    la = store.open_lease(a, wa)
    store.claim(a, listing["id"], la, wakeup_id=wa)
    att, _ = store.record_intent(a, listing["id"], la, wa, **LISTING_INTENT)
    pid, started = q(a, "select backend_pid, backend_started_at from backfill_wakeups where id = %s", (wa,))[0]
    wb = wakeup(b)
    assert not p2runs.acquire_global_lock(b)                                   # the holder lives: never taken over
    with pytest.raises(store.LedgerError):
        store.expire_dead_leases(b, wb)
    assert store.expire_dead_wakeups(b, wb) == []
    refused(lambda: q(b, "update backfill_wakeups set state = 'expired', finished_at = now(), expired_by = %s where "
                         "id = %s", (wb, wa)), match="live session")
    a.close()                                                                  # the slice dies
    wait_until_gone(b, pid, started)
    end = time.monotonic() + 10
    while not p2runs.acquire_global_lock(b):                                   # PostgreSQL frees the dead lock
        assert time.monotonic() < end, "the dead session's lock was not released"
        time.sleep(0.05)
    out = store.expire_dead_leases(b, wb)
    assert [(o["lease_id"], o["attempts_closed"], o["items_abandoned"]) for o in out] == [(la, [att], [listing["id"]])]
    assert store.attempts(b, listing["id"])[0]["outcome"] == "unrecorded"
    assert store.current_state(b, listing["id"]) == {"state": "abandoned", "seq": 3, "action": "expire",
                                                     "lease_id": la}
    assert store.expire_dead_wakeups(b, wb) == [wa]
    assert q(b, "select state, expired_by from backfill_wakeups where id = %s", (wa,)) == [("expired", wb)]
    assert q(b, "select state, expired_by_wakeup from backfill_leases where id = %s", (la,)) == [("expired", wb)]
    store.append_event(b, listing["id"], "pending", "promote", reason="no F1 run finished: nothing to adopt")
    lb = store.open_lease(b, wb)
    store.claim(b, listing["id"], lb, wakeup_id=wb)
    assert store.record_intent(b, listing["id"], lb, wb, **LISTING_INTENT)[1] == 2   # attempts continue: 2
    assert store.release_lease(b, lb, "completed")
    p2runs.release_global_lock(b)


def test_l8_b1_a_discovery_item_whose_attempts_all_died_fails_with_a_reason(env):
    """HB-1 audit B-1. Every attempt of one listing dies mid-request: F1's run, begun before each request, stays
    'running'; the next slice expires the dead lease, closes the attempt 'unrecorded' and abandons the item, which
    returns to pending with its attempt count (design section 15.3). At the item maximum (section 16.4: "then
    terminal, with the reason") the item fails on a stated reason without a failed F1 run, so discovery can close
    (HB-U5). succeeded and partial still need their own F1 run, and a named F1 run must itself have failed."""
    owner.record_arming_as_owner(env.owner_conn(), armed_decision(), "tester")
    w = env.conn()
    item_max = store.arming_in_force(w)["item_max_attempts"]
    assert item_max == 3
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    for n in range(1, item_max + 1):
        a = env.conn()                                                         # a slice that will die
        assert p2runs.acquire_global_lock(a)
        wa = wakeup(a)
        la = store.open_lease(a, wa)
        store.claim(a, listing["id"], la, wakeup_id=wa)
        PostgresFilingStore(a).begin_run(f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"}, now())  # F1: before the request
        att, attempt_no = store.record_intent(a, listing["id"], la, wa, **LISTING_INTENT)
        assert attempt_no == n
        pid, started = q(a, "select backend_pid, backend_started_at from backfill_wakeups where id = %s", (wa,))[0]
        a.close()                                                              # the slice dies mid-request
        b = env.conn()                                                         # the next slice
        wait_until_gone(b, pid, started)
        end = time.monotonic() + 10
        while not p2runs.acquire_global_lock(b):                               # PostgreSQL frees the dead lock
            assert time.monotonic() < end, "the dead session's lock was not released"
            time.sleep(0.05)
        wb = wakeup(b)
        out = store.expire_dead_leases(b, wb)
        assert [(o["lease_id"], o["attempts_closed"], o["items_abandoned"]) for o in out] == \
            [(la, [att], [listing["id"]])]
        store.append_event(b, listing["id"], "pending", "promote", reason="no F1 run of the request finished")
        assert store.finish_wakeup(b, wb, "completed")
        p2runs.release_global_lock(b)
        b.close()
    assert [(x["attempt_no"], x["outcome"]) for x in store.attempts(w, listing["id"])] == \
        [(n, "unrecorded") for n in range(1, item_max + 1)]
    f1_runs = q(w, "select id, status from report_discovery_runs where source_endpoint = %s and "
                   "request_params ->> 'symbol' = %s", (f1.LISTING_ENDPOINT, "COMB.N0000"))
    assert len(f1_runs) == item_max and {s for _, s in f1_runs} == {"running"}
    assert store.current_state(w, listing["id"])["state"] == "pending"
    # the item maximum is reached: the next slice records the terminal state without a further request
    running = str(f1_runs[0][0])
    reason = f"item maximum of {item_max} attempts reached: every attempt ended unrecorded"
    with cse_slice(w) as (wid, lid):
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        refused(lambda: store.append_event(w, listing["id"], "succeeded", "record", lease_id=lid, reason=reason),
                match="succeeded needs an F1 run")
        refused(lambda: store.append_event(w, listing["id"], "partial", "record", lease_id=lid, reason=reason,
                                           f1_run_id=running), match="whose own status is partial")
        refused(lambda: store.append_event(w, listing["id"], "failed", "record", lease_id=lid, reason=reason,
                                           f1_run_id=running), match="whose own status is failed")
        refused(lambda: store.append_event(w, listing["id"], "failed", "record", lease_id=lid, reason=" "),
                match="needs a reason")
        store.append_event(w, listing["id"], "failed", "record", lease_id=lid, reason=reason)
    assert len(store.attempts(w, listing["id"])) == item_max                   # no request beyond the maximum
    final = store.current_state(w, listing["id"])["state"]
    assert final == "failed" and final in states.FINAL["listing"]
    assert states.next_states("listing", final) == {"pending": "requeue"}      # only an explicit operator re-queue
    # discovery closure (HB-U5) is no longer blocked by this item: every discovery item is in a final state
    assert q(w, "select count(*) from backfill_item_state where item_kind in ('feed_window', 'listing') and "
                "not state = any(%s)", (sorted(states.FINAL["listing"]),)) == [(0,)]


def test_l3_b1_a_failure_without_f1_evidence_never_contradicts_a_finished_f1_run(env):
    """B-1's correction, other half (evidence wins): with no failed F1 run named, a discovery item is refused 'failed'
    while an F1 run of its own request succeeded or partially succeeded (for example: the slice died after F1 finished
    the run, before the ledger recorded it); it terminates on that run's own status instead. Another request's
    finished run is not its evidence."""
    w = env.conn()
    april, _ = store.ensure_item(w, keys.feed_window(2021, 4))
    may, _ = store.ensure_item(w, keys.feed_window(2021, 5))
    comb, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    lolc, _ = store.ensure_item(w, keys.listing("LOLC.N0000"))
    april_run = f1_run(w, f1.FEED_ENDPOINT, {"fromDate": "2021-04-01", "toDate": "2021-04-30"}, status="partial")
    comb_run = f1_run(w, f1.LISTING_ENDPOINT, {"symbol": "COMB.N0000"})        # succeeded
    reason = "item maximum reached: every attempt ended unrecorded"
    with cse_slice(w) as (wid, lid):
        for item in (april, may, comb, lolc):
            store.claim(w, item["id"], lid, wakeup_id=wid)
        for item in (april, comb):
            refused(lambda: store.append_event(w, item["id"], "failed", "record", lease_id=lid, reason=reason),
                    match="succeeded or partially succeeded")
        store.append_event(w, may["id"], "failed", "record", lease_id=lid, reason=reason)     # April's run is not May's
        store.append_event(w, lolc["id"], "failed", "record", lease_id=lid, reason=reason)    # COMB's run is not LOLC's
        store.append_event(w, april["id"], "partial", "record", lease_id=lid, f1_run_id=april_run)
        store.append_event(w, comb["id"], "succeeded", "record", lease_id=lid, f1_run_id=comb_run)
    assert store.state_counts(w) == {("feed_window", "partial"): 1, ("feed_window", "failed"): 1,
                                     ("listing", "succeeded"): 1, ("listing", "failed"): 1}


def test_l9_a_block_stops_its_item_until_the_owner_acknowledges_it(env):
    w, m = env.conn(), env.owner_conn()
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    with cse_slice(w) as (wid, lid):
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        a1, _ = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        store.record_outcome(w, a1, "server_error", "retryable", http_status=503)
        refused(lambda: store.record_block(w, a1, "not a block"), match="no recorded block outcome")
        a2, _ = store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
        store.record_outcome(w, a2, "blocked", "block", http_status=403)
        bid, created = store.record_block(w, a2, "HTTP 403 from the API", wakeup_id=wid)
        assert created and store.record_block(w, a2, "again") == (bid, False)
        refused(lambda: store.append_event(w, listing["id"], "blocked", "record", lease_id=lid), match="names its block")
        store.append_event(w, listing["id"], "blocked", "record", lease_id=lid, block_id=bid, attempt_id=a2)
    assert [b["block_id"] for b in store.unacknowledged_blocks(w)] == [bid]
    refused(lambda: store.append_event(w, listing["id"], "pending", "resume"), match="not acknowledged")
    owner.acknowledge_block_as_owner(m, bid, "owner reviewed the CSE block; resume", operator="tester")
    refused(lambda: owner.acknowledge_block_as_owner(m, bid, "a second acknowledgement"), pgcode="23505")
    store.append_event(w, listing["id"], "pending", "resume")
    assert store.unacknowledged_blocks(w) == []


def test_l10_l11_anomalies_and_coverage_snapshots_are_immutable_and_idempotent(env):
    w = env.conn()
    snap, created = store.record_snapshot(w, rule_version="hb.coverage.1", snapshot_digest="c" * 64,
                                          stage_counts={"discovered": 3, "retrieved": 2}, details={"review": []})
    assert created and store.record_snapshot(w, rule_version="hb.coverage.1", snapshot_digest="c" * 64,
                                             stage_counts={"discovered": 3}) == (snap, False)
    for over in ({"snapshot_digest": "C" * 64}, {"rule_version": "coverage.1"}):
        refused(lambda: store.record_snapshot(w, **dict(dict(rule_version="hb.coverage.1", snapshot_digest="d" * 64,
                                                             stage_counts={}), **over)), pgcode="23514")
    a = dict(detector_id="P-18", detector_version="hb.anomaly.1", anomaly_class=6,
             subject_ids={"cse_filing_id": [52713]}, counts={"filings": 1}, status="open", snapshot_id=snap)
    first, created = store.record_anomaly(w, **a)
    assert created and store.record_anomaly(w, **a) == (first, False)          # re-detection adds nothing
    stored = q(w, "select record_sha256, encode(sha256(convert_to(jsonb_build_object('detector_id', detector_id, "
                  "'detector_version', detector_version, 'anomaly_class', anomaly_class, 'subject_ids', subject_ids, "
                  "'counts', counts, 'status', status, 'snapshot_id', snapshot_id, 'supersedes_id', supersedes_id)::text, "
                  "'UTF8')), 'hex') from backfill_anomalies where id = %s", (first,))[0]
    assert stored[0] == stored[1]                                              # the database's own content hash
    changed, created = store.record_anomaly(w, **dict(a, anomaly_class=5, supersedes_id=first))
    assert created and changed != first                                        # a reclassification is a new record
    for over in ({"anomaly_class": 7}, {"detector_version": "v1"}, {"status": "Open!"}):
        refused(lambda: store.record_anomaly(w, **dict(a, **over)), pgcode="23514")
    refused(lambda: as_owner(env, "update backfill_anomalies set status = 'closed'"), pgcode="23001")
    refused(lambda: as_owner(env, "delete from backfill_coverage_snapshots"), pgcode="23001")


def test_budget_accounting_counts_the_colombo_day_from_the_ledger(env):
    w, m = env.conn(), env.owner_conn()
    today = keys.colombo_date(now())
    assert store.budget(w, today) == {"armed": False, "phase2_requests": 0, "daily_request_budget": 0, "remaining": 0,
                                      "p2_requests": 0, "combined_daily_ceiling": None, "combined_remaining": None,
                                      "exhausted": True, "colombo_date": today.isoformat()}
    owner.record_arming_as_owner(m, armed_decision(daily_request_budget=3, combined_daily_ceiling=5), "tester")
    listing, _ = store.ensure_item(w, keys.listing("COMB.N0000"))
    with cse_slice(w) as (wid, lid):
        store.claim(w, listing["id"], lid, wakeup_id=wid)
        for _ in range(2):
            store.record_intent(w, listing["id"], lid, wid, **LISTING_INTENT)
    p2_archive_row(w, today)
    p2_archive_row(w, today)
    got = store.budget(w, today)
    assert (got["armed"], got["phase2_requests"], got["remaining"], got["p2_requests"], got["combined_remaining"],
            got["exhausted"]) == (True, 2, 1, 2, 1, False)
    for day in (today - timedelta(days=1), today + timedelta(days=1)):
        assert store.requests_on_colombo_day(w, day) == 0 and store.p2_requests_on_colombo_day(w, day) == 0
    owner.record_arming_as_owner(m, owner.ArmingDecision.disarm("owner disarm after the test"), "tester")
    assert store.budget(w, today)["remaining"] == 0 and store.budget(w, today)["armed"] is False


def test_rollback_leaves_nothing_and_the_connection_stays_usable(env):
    import psycopg2
    w = env.conn()

    def work(cur):
        cur.execute("insert into backfill_work_items (item_kind, natural_key, sequence_no) values ('audit', 'audit:9', 9) "
                    "returning id")
        item_id = cur.fetchone()[0]
        store.append_event_in(cur, item_id, "pending", "create")
        store.append_event_in(cur, item_id, "succeeded", "record")           # refused: an audit names its snapshot
    with pytest.raises(psycopg2.Error):
        store._tx(w, work)
    assert store.item_by_key(w, "audit:9") is None
    item, created = store.ensure_item(w, keys.audit(9))
    assert created and [e["state"] for e in store.events(w, item["id"])] == ["pending"]
    with pytest.raises(states.IllegalTransition):
        store.ensure_item(w, keys.audit(10), first_state="succeeded")
    assert store.item_by_key(w, "audit:10") is None


def test_the_preflight_detects_drift(env, monkeypatch):
    w, su = env.conn(), env.conn("postgres")
    assert preflight.problems(w) == []
    drift = (
        ("alter table backfill_item_events disable trigger trg_bfie_guard",
         "alter table backfill_item_events enable trigger trg_bfie_guard",
         "trigger trg_bfie_guard on backfill_item_events missing or disabled"),
        ("grant delete on backfill_holds to cse_worker", "revoke delete on backfill_holds from cse_worker",
         "cse_worker has DELETE on backfill_holds: the ledger is append-only"),
        ("grant insert on backfill_arming_decisions to cse_worker",
         "revoke insert on backfill_arming_decisions from cse_worker",
         "cse_worker has INSERT on backfill_arming_decisions: an owner decision (G-1)"),
        ("revoke execute on function hb_session_started() from cse_worker",
         "grant execute on function hb_session_started() to cse_worker",
         "cse_worker lacks EXECUTE on hb_session_started()"),
        ("grant execute on function hb_item_event_guard() to cse_worker",
         "revoke execute on function hb_item_event_guard() from cse_worker",
         "cse_worker has EXECUTE on the trigger function hb_item_event_guard()"),
        ("alter function hb_session_alive(integer, timestamptz) security definer",
         "alter function hb_session_alive(integer, timestamptz) security invoker",
         "function hb_session_alive is SECURITY DEFINER or not owned by cse_owner"),
        ("alter table backfill_holds enable row level security", "alter table backfill_holds disable row level security",
         "row-level security on backfill_holds"),
    )
    for do, undo, problem in drift:
        q(su, do)
        try:
            found = preflight.database_problems(w)
            assert [p for p in found if p.startswith(problem)], (do, found)
        finally:
            q(su, undo)
    assert preflight.problems(w) == []
    monkeypatch.setattr(states, "TRANSITIONS", states.TRANSITIONS - {("audit", "pending", "succeeded", "record")})
    assert [p for p in preflight.database_problems(w) if "transition rules differ" in p]
