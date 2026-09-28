"""
P2 market capture against REAL PostgreSQL 17 (throwaway initdb clusters; migrations 0001-0012 through the P1 runner,
roles from the P1 bootstrap) with a FAKE CSE transport built from the real captured fixtures and a fake clock:
no network, no CSE, no real sleeping.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p2_capture_postgres.py

Covers: a full capture (archive both copies, exact bytes, linkage run -> attempt -> body -> raw observation ->
canonical row), request discipline in the orchestrated path, failure/recovery (spool ok + DB down, crash between spool
and DB, canonicalisation / mapping failure after archive, retry after a partial run, identical and changed responses),
completeness dimensions, trading-date handling, the security model and G-1 (block stop + owner acknowledgement, no
parallel capture, no purge path for the worker/backup roles), and P1 regressions with 0012 applied.
"""
import base64
import hashlib
import json
import os
import socket
import sys
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from p2_fakes import ABSENT, TRADED, FakeClock, FakeCSE, R, dumps  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
TD = date(2026, 9, 4)
EMAIL = "p2-tests@example.org"
P2_TABLES = ("market_capture_runs", "market_capture_run_events", "market_capture_block_acknowledgements",
             "market_response_bodies", "market_source_responses", "market_capture_security_results")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    from worker.ops import migrate as mig
    from worker.ops.ephemeral_pg import EphemeralCluster
    base = tmp_path_factory.mktemp("p2_cluster")
    ec = EphemeralCluster(BINDIR, base_dir=str(base), port=free_port(), durable=True, superuser="postgres").start()
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", BOOTSTRAP)
    c = ec.connect(dbname="cse", user="cse_migrator")
    mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()
    yield ec
    ec.cleanup()


def conn(pg, user="cse_worker", autocommit=False):
    c = pg.connect(dbname="cse", user=user)
    c.autocommit = autocommit
    return c


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
    c.commit()
    return rows


def cfg_for(pg, root, **extra):
    from worker.market_capture import config as cfgmod
    env = {"CSE_DB_NAME": "cse", "CSE_DB_HOST": pg.sockdir, "CSE_DB_PORT": str(pg.port), "CSE_DB_USER": "cse_worker",
           "CSE_BACKUP_ROOT": str(root), "CSE_PG_BINDIR": BINDIR, "CSE_CAPTURE_CONTACT_EMAIL": EMAIL}
    env.update(extra)
    os.makedirs(os.path.join(str(root), "spool"), exist_ok=True)
    return cfgmod.load(env, require_contact=True)


class Env:
    """One capture environment: fresh backup root/spool, fake clock and fake CSE; connections as cse_worker."""

    def __init__(self, pg, root, script=None, clock_start=None, **fake_kw):
        from worker.market_capture import capture
        self.pg, self.root = pg, root
        self.cfg = cfg_for(pg, root)
        self.clock = FakeClock(clock_start) if clock_start else FakeClock()
        self.cse = FakeCSE(self.clock, script=script, **fake_kw)
        self.logs = []
        self.rt = capture.Runtime(transport=self.cse, clock=self.clock.monotonic, wall=self.clock.wall,
                                  sleep=self.clock.sleep, log=self.logs.append)
        self.ctl, self.work = conn(pg), conn(pg)

    def capture(self, trading_date=TD, mode="post_close", **pol):
        from worker.market_capture import capture, config as cfgmod
        return capture.start(self.ctl, self.work, self.cfg, trading_date=trading_date,
                             policy=cfgmod.daily_policy(mode, **pol), rt=self.rt)

    def close(self):
        for c in (self.ctl, self.work):
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
def env(pg, tmp_path):
    envs = []

    def make(root=None, **kw):
        e = Env(pg, root or (tmp_path / f"e{len(envs)}" / "backup"), **kw)
        envs.append(e)
        return e
    yield make
    for e in envs:
        e.close()


@pytest.fixture(autouse=True)
def isolate(pg):
    """Each test starts with a clean capture ledger view: runs from earlier tests are closed out (acknowledged if
    blocked) so a left-over block or running run never leaks into the next test. Nothing is deleted."""
    yield
    su = conn(pg, "postgres", autocommit=True)
    for (rid,) in q(su, "select run_id from market_capture_run_state where state = 'blocked' and run_id not in "
                        "(select run_id from market_capture_block_acknowledgements)"):
        q(su, "insert into market_capture_block_acknowledgements (run_id, note) values (%s, 'test cleanup ack')",
          (rid,))
    for (rid,) in q(su, "select run_id from market_capture_run_state where state = 'running'"):
        q(su, "insert into market_capture_run_events (run_id, seq, state, reason) select %s, max(seq) + 1, "
              "'abandoned', 'test cleanup' from market_capture_run_events where run_id = %s", (rid, rid))
    su.close()


def state_of(c, run_id):
    return q(c, "select state from market_capture_run_state where run_id = %s", (run_id,))[0][0]


# ------------------------------------------------------------------------------------------------ full capture

def test_full_post_close_capture_archives_derives_and_links(env):
    e = env()
    state, rep = e.capture()
    assert state == "succeeded", json.dumps(rep["summary"], default=str)[:3000]
    run = rep["run_id"]
    # the P0.5 request plan: universe, tradeSummary, fallback for the 2 absent, cross-check of the 5 traded
    keys = e.cse.keys()
    assert keys[:2] == ["allSecurityCode", "tradeSummary"]
    assert sorted(keys[2:4]) == [f"companyInfoSummery:{s}" for s in sorted(ABSENT)]
    assert sorted(keys[4:]) == sorted(f"companyInfoSummery:{s}" for s in TRADED) and len(keys) == 9
    # sequential, >= 1.5 s from the end of one request to the start of the next, identifiable User-Agent, no cookie
    assert e.cse.max_in_flight == 1
    for a, b in zip(e.cse.calls, e.cse.calls[1:]):
        assert b["started"] - a["ended"] >= 1.5 - 1e-9
    assert all(EMAIL in c["headers"]["User-Agent"] for c in e.cse.calls)
    assert all("cookie" not in {k.lower() for k in c["headers"]} for c in e.cse.calls)
    s = rep["summary"]
    assert s["A"]["status"] == "captured" and s["B"]["status"] == "known" and s["B"]["universe_size"] == 7
    assert sorted(s["B"]["absent_from_trade_summary"]) == sorted(ABSENT)
    assert s["C"] == {**s["C"], "status": "complete", "expected": 7, "produced": 7, "missing": []}
    assert s["D"]["written"] == 7 and s["D"]["failed"] == []
    assert s["session_evidence"]["session_matches_trading_date"] is True
    assert s["session_evidence"]["latest_session_date_colombo"] == "2026-09-04"
    assert sorted(s["cross_check"]["sampled"]) == sorted(TRADED) and s["cross_check"]["status"] == "complete"
    # linkage: run -> attempts -> bodies -> raw observations (request_attempt_id = run id) -> canonical rows
    c = e.ctl
    assert q(c, "select count(*) from market_source_responses where run_id = %s", (run,)) == [(9,)]
    assert q(c, "select count(*) from raw_market_observations where request_attempt_id = %s", (run,)) == [(7,)]
    assert q(c, "select count(*) from market_capture_security_results r join raw_market_observations o "
                "on o.id = r.raw_observation_id where r.run_id = %s and o.request_attempt_id = %s", (run, run)) == [(7,)]
    assert q(c, "select count(*) from daily_market_data d join companies co on co.id = d.company_id "
                "where d.trade_date = %s and co.ticker = any(%s)", (TD, TRADED + ABSENT)) == [(7,)]
    # observed_at of each observation is the archived response's time, never "now"
    obs = dict(q(c, "select co.ticker, o.observed_at from raw_market_observations o join companies co "
                    "on co.id = o.company_id where o.request_attempt_id = %s", (run,)))
    ts_obs = q(c, "select observed_at from market_source_responses where run_id = %s and request_key = "
                  "'tradeSummary'", (run,))[0][0]
    assert all(obs[t] == ts_obs for t in TRADED)
    # raw_payload keeps Stage E's keys; companyInfoSummery only where a body was archived and used
    payloads = dict(q(c, "select co.ticker, o.raw_payload from raw_market_observations o join companies co "
                         "on co.id = o.company_id where o.request_attempt_id = %s", (run,)))
    assert payloads["COMB.N0000"]["tradeSummary_matched_row"]["symbol"] == "COMB.N0000"
    assert payloads["COMB.N0000"]["companyInfoSummery"]["body"]["reqSymbolInfo"]["symbol"] == "COMB.N0000"
    assert payloads["ABSA.N0000"]["tradeSummary_matched_row"] is None
    assert payloads["COMB.N0000"]["p2"]["capture_run_id"] == run


def test_archive_holds_exact_bytes_in_both_copies_and_hashes_agree(env):
    from worker.market_capture import capture
    from worker.ops import spool
    odd = b'{"reqTradeSummery":[' + json.dumps(json.load(open(os.path.join(
        REPO, "tests", "fixtures", "multi_company", "real_tradeSummary_row_COMB_N0000.json")))).encode() + b"]}\r\n\t "
    e = env(script={"tradeSummary": [R(200, odd, {"Content-Type": "application/json; charset=utf-8"})]},
            ts_rows=None)
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    run = rep["run_id"]
    b64, sha, n, key = q(e.ctl, "select b.body_base64, r.body_sha256, r.body_bytes, r.spool_body_key from "
                                "market_source_responses r join market_response_bodies b using (body_sha256) "
                                "where r.run_id = %s and r.request_key = 'tradeSummary'", (run,))[0]
    assert base64.b64decode(b64) == odd and sha == hashlib.sha256(odd).hexdigest() and n == len(odd)
    assert spool.read(e.cfg.spool_root, key) == odd                       # spool copy: the same bytes
    assert capture.verify_archive(e.ctl, e.cfg.spool_root, run)["problems"] == []
    # the database refuses a body whose bytes do not match the recorded SHA-256
    su = conn(e.pg, "postgres", autocommit=True)
    with pytest.raises(Exception) as ei:
        q(su, "insert into market_response_bodies (body_sha256, body_bytes, body_base64) values (%s, 3, %s)",
          ("0" * 64, base64.b64encode(b"abc").decode()))
    assert ei.value.pgcode == "23514"                                     # check_violation (chk_mrb_exact)
    su.close()


def test_sensitive_headers_removed_and_names_recorded(env):
    e = env()
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    hdrs, removed, req_h = q(e.ctl, "select response_headers, removed_response_headers, request_headers from "
                                    "market_source_responses where run_id = %s and request_key = 'allSecurityCode'",
                             (rep["run_id"],))[0]
    assert "set-cookie" not in hdrs and removed == ["set-cookie"] and "sess=abc" not in json.dumps(hdrs)
    assert EMAIL in req_h["user-agent"] and not ({"cookie", "authorization"} & set(req_h))


# ------------------------------------------------------------------------------------------------ security model

def test_worker_cannot_mutate_the_archive_and_owner_is_stopped_by_triggers(env, pg):
    import psycopg2
    e = env()
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    run = rep["run_id"]
    w = conn(pg, "cse_worker", autocommit=True)
    for t, col in (("market_source_responses", "outcome"), ("market_capture_runs", "run_kind"),
                   ("market_capture_run_events", "reason"), ("market_response_bodies", "body_base64"),
                   ("market_capture_security_results", "reason")):
        for stmt in (f"update {t} set {col} = {col}", f"delete from {t}", f"truncate {t}"):
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                q(w, stmt)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            q(w, f"alter table {t} disable trigger all")          # the purge-style bypass is owner-only
    w.close()
    owner = conn(pg, "cse_migrator")
    q(owner, "set role cse_owner")
    for t in P2_TABLES:
        has_rows = q(owner, f"select exists (select 1 from {t})")[0][0]
        # DELETE fires the row trigger only when a row exists (an empty table has nothing to protect);
        # TRUNCATE is always refused by the statement trigger (or earlier by a foreign key)
        for stmt in ([f"delete from {t}"] if has_rows else []) + [f"truncate {t}"]:
            with pytest.raises(Exception) as ei:
                q(owner, stmt)
            assert ei.value.pgcode in ("23001", "0A000"), (t, stmt)
            owner.rollback()
    owner.close()
    assert state_of(e.ctl, run) == "succeeded"


def test_reader_and_backup_roles_have_only_read_access(pg):
    import psycopg2
    b = conn(pg, "cse_backup", autocommit=True)
    assert q(b, "select count(*) >= 0 from market_source_responses") == [(True,)]
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        q(b, "insert into market_capture_runs (run_kind, trading_date, trading_date_basis, capture_mode, policy, "
             "tool_version) values ('market_capture', '2026-09-04', 'operator', 'post_close', '{}', 'x')")
    b.close()
    su = conn(pg, "postgres", autocommit=True)
    for t in P2_TABLES:
        assert q(su, "select has_table_privilege('cse_reader', %s, 'SELECT'), has_table_privilege('cse_reader', %s, "
                     "'INSERT'), has_table_privilege('cse_backup', %s, 'UPDATE'), has_table_privilege('cse_worker', %s, "
                     "'UPDATE') or has_table_privilege('cse_worker', %s, 'DELETE')", (t, t, t, t, t)) == \
            [(True, False, False, False)], t
    # PUBLIC has nothing on the new tables; nothing new is owned by a login role
    assert q(su, "select count(*) from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
                 "where c.relname = any(%s) and a.grantee = 0", (list(P2_TABLES),)) == [(0,)]
    assert q(su, "select count(*) from pg_class c join pg_roles r on r.oid = c.relowner where c.relname = any(%s) "
                 "and r.rolname <> 'cse_owner'", (list(P2_TABLES),)) == [(0,)]
    # no purge / delete routine exists that the worker or the backup role could call
    assert q(su, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in "
                 "('public', 'ops') and (p.proname ilike '%%purge%%' or p.proname ilike '%%delete%%')") == [(0,)]
    su.close()


def test_security_preflight_refuses_the_wrong_roles(pg, tmp_path):
    from worker.market_capture import runs
    for role in ("cse_backup", "cse_migrator"):
        c = conn(pg, role)
        problems = runs.security_preflight(c, "cse_worker")
        assert any("must run as 'cse_worker'" in p for p in problems), (role, problems)
        c.close()
    w = conn(pg, "cse_worker")
    assert runs.security_preflight(w, "cse_worker") == []
    w.close()


def test_verify_server_still_passes_with_0012(pg):
    from worker.ops import verify_server as vs
    rep = vs.Report()
    c = conn(pg, "postgres")
    with c.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=MIGDIR)
    c.close()
    assert not rep.failed, rep.failed


def test_migration_runner_recorded_0012_with_its_hash(pg):
    from worker.ops import migrate as mig
    su = conn(pg, "postgres")
    got = dict(q(su, "select filename, sha256 from ops.schema_migrations where version = 12"))
    su.close()
    m = {x.filename: x.sha256 for x in mig.discover(MIGDIR)}
    assert got == {"0012_market_capture_archive.sql": m["0012_market_capture_archive.sql"]}


# ------------------------------------------------------------------------------------------------ G-1 request policy

def test_403_stops_the_run_at_once_and_gates_every_later_capture(env):
    from worker.market_capture import capture, runs
    e = env(script={"tradeSummary": [R(403, b"<html>Access denied</html>", {"Content-Type": "text/html"})]})
    state, rep = e.capture()
    assert state == "blocked" and e.cse.keys() == ["allSecurityCode", "tradeSummary"]     # no retry, nothing more
    row = q(e.ctl, "select outcome, http_status, body_bytes from market_source_responses where run_id = %s and "
                   "request_key = 'tradeSummary'", (rep["run_id"],))
    assert row == [("blocked", 403, len(b"<html>Access denied</html>"))]            # the block page is evidence
    n = len(e.cse.calls)
    for attempt in (lambda: e.capture(trading_date=TD),
                    lambda: capture.resume(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)):
        with pytest.raises(runs.RunRefused, match="G-1"):
            attempt()
    assert len(e.cse.calls) == n                                                      # refused before any request
    with pytest.raises(Exception, match="illegal state transition"):                  # the database agrees
        runs.append_event(e.ctl, rep["run_id"], "running")
    with pytest.raises(Exception, match="chk_mcba_note"):
        capture.acknowledge_block(e.ctl, e.cfg, rep["run_id"], "short", rt=e.rt)     # a real note is required
    capture.acknowledge_block(e.ctl, e.cfg, rep["run_id"], "owner reviewed: test block, resuming", rt=e.rt)
    state2, rep2 = capture.resume(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)
    assert state2 == "succeeded" and e.cse.keys()[n] == "tradeSummary"
    assert q(e.ctl, "select array_agg(outcome order by attempt_no) from market_source_responses where run_id = %s "
                    "and request_key = 'tradeSummary'", (rep["run_id"],)) == [(["blocked", "ok"],)]


def test_429_honours_retry_after_then_succeeds(env):
    e = env(script={"tradeSummary": [R(429, b"", {"Retry-After": "7"})]})
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    assert state == "succeeded" and e.cse.keys() == ["allSecurityCode", "tradeSummary", "tradeSummary"]
    assert 7.0 in e.clock.slept
    assert q(e.ctl, "select array_agg(outcome order by attempt_no) from market_source_responses where run_id = %s "
                    "and request_key = 'tradeSummary'", (rep["run_id"],)) == [(["rate_limited", "ok"],)]


def test_429_asking_for_too_long_stops_as_blocked(env):
    e = env(script={"tradeSummary": [R(429, b"", {"Retry-After": "3600"})]})
    state, rep = e.capture()
    assert state == "blocked" and e.cse.keys() == ["allSecurityCode", "tradeSummary"]
    assert max(e.clock.slept, default=0) < 3600


def test_server_errors_back_off_exponentially_and_stay_bounded(env):
    e = env(script={"tradeSummary": [R(500, b"oops"), R(502, b""), R(503, b"")]})
    state, rep = e.capture()
    assert state == "failed" and e.cse.keys() == ["allSecurityCode"] + ["tradeSummary"] * 3   # 3 attempts, then stop
    assert [s for s in e.clock.slept if s >= 5] == [5.0, 10.0]                                 # 5 s, 10 s
    assert rep["summary"]["A"]["status"] == "not_captured"
    assert q(e.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s",
             (rep["run_id"],)) == [(0,)]


def test_failed_attempts_are_archived_without_fake_bodies(env):
    e = env(script={"allSecurityCode": [R(error_kind="timeout"), R(200, b"not json at all")],
                    "tradeSummary": [R(200, b""), R(200, b'{"no": "rows"}')]})
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    assert state == "succeeded"
    rows = q(e.ctl, "select request_key, attempt_no, outcome, http_status, body_bytes, parse_status from "
                    "market_source_responses where run_id = %s order by sequence_no", (rep["run_id"],))
    assert rows == [("allSecurityCode", 1, "timeout", None, None, None),
                    ("allSecurityCode", 2, "invalid_json", 200, len(b"not json at all"), "not_json"),
                    ("allSecurityCode", 3, "ok", 200, rows[2][4], "json_ok"),
                    ("tradeSummary", 1, "empty_response", 200, 0, "empty"),
                    ("tradeSummary", 2, "malformed_response", 200, len(b'{"no": "rows"}'), "json_ok"),
                    ("tradeSummary", 3, "ok", 200, rows[5][4], "json_ok")]


def test_changed_response_on_retry_keeps_both_and_uses_the_good_one(env):
    bad = b'{"reqTradeSummery": "temporarily unavailable"}'
    e = env(script={"tradeSummary": [R(200, bad)]})
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    shas = q(e.ctl, "select body_sha256 from market_source_responses where run_id = %s and request_key = "
                    "'tradeSummary' order by attempt_no", (rep["run_id"],))
    assert state == "succeeded" and len({s for (s,) in shas}) == 2 and shas[0][0] == hashlib.sha256(bad).hexdigest()
    used = q(e.ctl, "select distinct o.raw_payload -> 'p2' ->> 'trade_summary_body_sha256' from "
                    "raw_market_observations o where o.request_attempt_id = %s", (rep["run_id"],))
    assert used == [(shas[1][0],)]


def test_identical_response_is_recognised_and_stored_once(env):
    e = env()
    s1, r1 = e.capture(absent_fallback=False, cross_check_size=0)
    s2, r2 = e.capture(absent_fallback=False, cross_check_size=0)
    rows = q(e.ctl, "select run_id, body_sha256, body_already_archived from market_source_responses where "
                    "request_key = 'allSecurityCode' and run_id in (%s, %s)", (r1["run_id"], r2["run_id"]))
    by_run = {str(r): (sha, already) for r, sha, already in rows}
    assert by_run[r2["run_id"]] == (by_run[r1["run_id"]][0], True)
    assert q(e.ctl, "select count(*) from market_response_bodies where body_sha256 = %s",
             (by_run[r1["run_id"]][0],)) == [(1,)]
    assert s1 == s2 == "succeeded" and r1["run_id"] != r2["run_id"]


def test_budget_exhaustion_is_partial_never_succeeded(env):
    e = env()
    state, rep = e.capture(max_requests=4)
    assert state == "partial" and len(e.cse.calls) == 4
    s = rep["summary"]
    assert s["C"]["status"] == "complete" and s["cross_check"]["status"] == "partial"
    assert rep["stop"]["type"] == "BudgetExhausted"


def test_one_capture_process_at_a_time(env, pg):
    from worker.market_capture import runs
    e = env()
    holder = conn(pg, "cse_worker")
    assert runs.acquire_global_lock(holder)
    with pytest.raises(runs.RunRefused, match="global capture lock"):
        e.capture()
    assert e.cse.calls == []
    holder.close()                                        # a dead process releases it
    assert e.capture(absent_fallback=False, cross_check_size=0)[0] == "succeeded"


def test_request_spacing_holds_across_processes(env):
    e1 = env()
    e1.capture(absent_fallback=False, cross_check_size=0)
    e2 = env()                                            # a "new process" whose clock knows nothing of e1
    e2.capture(absent_fallback=False, cross_check_size=0)
    assert e2.clock.slept and e2.clock.slept[0] >= 1.5 - 1e-9


# ------------------------------------------------------------------------------------------------ failure / recovery

def test_spool_written_but_database_down_then_recovered_and_resumed(env, monkeypatch):
    from worker.market_capture import archive, capture
    real = archive.PgArchiveStore.insert_attempt

    def flaky(self, record, record_key, body_b64, recovered=False):
        if record["request_key"] == "tradeSummary" and not recovered:
            raise RuntimeError("simulated: PostgreSQL went away")
        return real(self, record, record_key, body_b64, recovered)
    monkeypatch.setattr(archive.PgArchiveStore, "insert_attempt", flaky)
    e = env()
    with pytest.raises(archive.ArchiveDatabaseUnavailable, match="spooled"):
        e.capture()
    run = str(q(e.ctl, "select run_id from market_capture_run_state where state = 'running'")[0][0])
    assert e.cse.keys() == ["allSecurityCode", "tradeSummary"]                     # stopped at once
    assert q(e.ctl, "select count(*) from market_source_responses where run_id = %s and request_key = "
                    "'tradeSummary'", (run,)) == [(0,)]
    journal = archive.Journal(e.cfg.spool_root, run).entries()
    assert [j["event"] for j in journal] == ["intent", "spooled", "intent", "spooled"]
    monkeypatch.setattr(archive.PgArchiveStore, "insert_attempt", real)            # PostgreSQL is back
    e2 = env(root=e.root)                                                          # same spool, "new process"
    rec = capture.recover(e2.ctl, e2.cfg, rt=e2.rt)
    assert rec["recovered"]["recovered"] == 1 and rec["abandoned"] == [run]
    row = q(e2.ctl, "select recovered_from_spool, outcome, body_sha256 is not null from market_source_responses "
                    "where run_id = %s and request_key = 'tradeSummary'", (run,))
    assert row == [(True, "ok", True)]
    state, rep = capture.resume(e2.ctl, e2.work, e2.cfg, run, rt=e2.rt)
    assert state == "succeeded" and "tradeSummary" not in e2.cse.keys()             # never re-requested
    assert capture.verify_archive(e2.ctl, e2.cfg.spool_root, run)["problems"] == []


def test_crash_between_spool_finalize_and_database_commit(env, monkeypatch):
    from worker.market_capture import archive, capture
    real = archive.PgArchiveStore.insert_attempt

    def crash(self, record, record_key, body_b64, recovered=False):
        if record["request_key"].startswith("companyInfoSummery:") and not recovered:
            raise SystemExit("simulated: process killed after the spool write")
        return real(self, record, record_key, body_b64, recovered)
    monkeypatch.setattr(archive.PgArchiveStore, "insert_attempt", crash)
    e = env()
    with pytest.raises(SystemExit):
        e.capture()
    e.ctl.close()                                                                   # the "process" is gone
    monkeypatch.setattr(archive.PgArchiveStore, "insert_attempt", real)
    e2 = env(root=e.root)
    run = str(q(e2.ctl, "select run_id from market_capture_run_state where state = 'running'")[0][0])
    state, rep = capture.resume(e2.ctl, e2.work, e2.cfg, run, rt=e2.rt)             # recovers, then continues
    assert state == "succeeded"
    seqs = [r[0] for r in q(e2.ctl, "select sequence_no from market_source_responses where run_id = %s order by 1",
                            (run,))]
    assert seqs == sorted(set(seqs))
    assert q(e2.ctl, "select count(*) from market_source_responses where run_id = %s and recovered_from_spool",
             (run,)) == [(1,)]
    assert q(e2.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s", (run,)) == [(7,)]


def test_spool_failure_is_not_a_durable_capture(env, monkeypatch):
    from worker.ops import spool
    real = spool.write_blob

    def failing(root, data):
        if b"reqTradeSummery" in data:
            raise OSError(28, "No space left on device")
        return real(root, data)
    monkeypatch.setattr(spool, "write_blob", failing)
    e = env()
    state, rep = e.capture()
    assert state == "failed" and e.cse.keys() == ["allSecurityCode", "tradeSummary"]
    assert q(e.ctl, "select outcome, body_sha256, error like 'spool write failed%%' from market_source_responses "
                    "where run_id = %s and request_key = 'tradeSummary'", (rep["run_id"],)) == \
        [("spool_failed", None, True)]
    assert rep["summary"]["A"]["status"] == "not_captured"


def test_unwritable_spool_refuses_before_any_request(env):
    from worker.market_capture import runs
    e = env()
    os.chmod(e.cfg.spool_root, 0o500)
    try:
        with pytest.raises(runs.RunRefused, match="spool"):
            e.capture()
    finally:
        os.chmod(e.cfg.spool_root, 0o750)
    assert e.cse.calls == []


def test_canonicalisation_failure_keeps_archive_and_capture_state(env, monkeypatch):
    from worker import db as stage_e_db
    from worker.market_capture import capture
    real = stage_e_db.upsert_daily_market_data
    calls = []

    def once_failing(conn, *, company_id, trade_date, canonical):
        calls.append(company_id)
        if len(calls) == 1:
            raise RuntimeError("simulated canonical write failure")
        return real(conn, company_id=company_id, trade_date=trade_date, canonical=canonical)
    monkeypatch.setattr(stage_e_db, "upsert_daily_market_data", once_failing)
    e = env()
    state, rep = e.capture()
    s = rep["summary"]
    assert state == "succeeded" and s["C"]["status"] == "complete"            # capture success is not canonicalisation
    assert s["D"]["status"] == "partial" and len(s["D"]["failed"]) == 1 and s["D"]["written"] == 6
    monkeypatch.setattr(stage_e_db, "upsert_daily_market_data", real)
    state2, rep2 = capture.reprocess(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)
    d = rep2["summary"]["D"]
    assert state2 == "succeeded" and (d["status"], d["failed"], d["written"]) == ("complete", [], 7)
    assert rep2["summary"]["C"]["pass_kind"] == "reprocess"
    assert len(e.cse.calls) == 9                                                   # reprocess made no request


def test_mapping_failure_after_archive_is_partial_and_reprocessable(env, monkeypatch):
    from worker import mapping
    from worker.market_capture import capture
    real = mapping.build_raw_observation
    n = []

    def second_fails(**kw):
        n.append(1)
        if len(n) == 2:
            raise ValueError("simulated mapping bug")
        return real(**kw)
    monkeypatch.setattr(mapping, "build_raw_observation", second_fails)
    e = env()
    state, rep = e.capture()
    assert state == "partial"
    miss = rep["summary"]["C"]["missing"]
    assert len(miss) == 1 and miss[0]["raw_status"] == "mapping_failed" and "simulated mapping bug" in miss[0]["reason"]
    assert capture.verify_archive(e.ctl, e.cfg.spool_root, rep["run_id"])["problems"] == []   # archive untouched
    monkeypatch.setattr(mapping, "build_raw_observation", real)
    state2, _ = capture.reprocess(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)
    assert state2 == "succeeded"
    assert q(e.ctl, "select array_agg(state order by seq) from market_capture_run_events where run_id = %s",
             (rep["run_id"],)) == [(["pending", "running", "partial", "running", "succeeded"],)]
    assert q(e.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s",
             (rep["run_id"],)) == [(7,)]


def test_partial_run_resume_requests_only_what_is_missing(env):
    from worker.market_capture import capture
    e = env(script={"companyInfoSummery:ABSA.N0000": [R(500, b""), R(500, b"")]})
    state, rep = e.capture()
    assert state == "partial"
    miss = rep["summary"]["C"]["missing"]
    assert [m["symbol"] for m in miss] == ["ABSA.N0000"] and "server_error" in miss[0]["reason"]
    before = len(e.cse.calls)
    state2, rep2 = capture.resume(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)
    assert state2 == "succeeded" and e.cse.keys()[before:] == ["companyInfoSummery:ABSA.N0000"]
    assert q(e.ctl, "select attempt_no, outcome from market_source_responses where run_id = %s and request_key = "
                    "'companyInfoSummery:ABSA.N0000' order by attempt_no", (rep["run_id"],)) == \
        [(1, "server_error"), (2, "server_error"), (3, "ok")]
    assert q(e.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s",
             (rep["run_id"],)) == [(7,)]
    assert rep2["summary"]["C"]["pass_kind"] == "resume"


def test_abandoned_run_is_detected_and_resumable(env, pg):
    from worker.market_capture import capture, config as cfgmod, runs
    e = env()
    dead = conn(pg)
    rid = runs.create_run(dead, run_kind="market_capture", trading_date=TD, capture_mode="post_close",
                          policy=cfgmod.daily_policy("post_close").as_json(), user_agent=e.cfg.user_agent,
                          tool_version="t", code_revision=None)
    runs.append_event(dead, rid, "running", "started")
    dead.close()                                                                    # crashed without a terminal state
    rec = capture.recover(e.ctl, e.cfg, rt=e.rt)
    assert rid in rec["abandoned"] and state_of(e.ctl, rid) == "abandoned"
    state, rep = capture.resume(e.ctl, e.work, e.cfg, rid, rt=e.rt)
    assert state == "succeeded"


# ------------------------------------------------------------------------------------------------ dates, modes, states

def test_future_trading_date_is_refused_before_any_request(env):
    from worker.market_capture import runs
    e = env()
    with pytest.raises(runs.RunRefused, match="after today's Colombo date"):
        e.capture(trading_date=TD + timedelta(days=1))
    assert e.cse.calls == []


def test_snapshot_of_another_session_is_never_labelled_with_the_trading_date(env):
    e = env()
    state, rep = e.capture(trading_date=TD - timedelta(days=1))
    assert state == "failed" and e.cse.keys() == ["allSecurityCode", "tradeSummary"]
    ev = rep["summary"]["session_evidence"]
    assert ev["session_matches_trading_date"] is False and ev["latest_session_date_colombo"] == "2026-09-04"
    assert q(e.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s",
             (rep["run_id"],)) == [(0,)]


def test_post_open_capture_never_supplies_end_of_day_fields(env):
    later = TD + timedelta(days=7)
    e = env(shift_days=7, clock_start=datetime(2026, 9, 11, 4, 30, tzinfo=timezone.utc))   # 10:00 Colombo
    state, rep = e.capture(trading_date=later, mode="post_open")
    assert state == "succeeded" and e.cse.keys() == ["allSecurityCode", "tradeSummary"]    # post_open: 2 requests
    s = rep["summary"]
    assert s["C"]["expected"] == 5 and s["C"]["status"] == "complete"
    assert sorted(s["C"]["not_expected"]) == sorted(ABSENT)
    raws = q(e.ctl, "select capture_window, observation_date, post_open_price is not null from raw_market_observations "
                    "where request_attempt_id = %s", (rep["run_id"],))
    assert raws == [("post_open", later, True)] * 5
    # the FROZEN reconcile, fed exactly these observations from the database, withholds every end-of-day field
    from worker import db as stage_e_db, reconciliation
    tol = __import__("worker.market_capture.derive", fromlist=["x"]).load_tolerances(e.work)
    for (cid,) in q(e.ctl, "select id from companies where ticker = any(%s)", (TRADED,)):
        canon = reconciliation.reconcile(stage_e_db.get_raw_observations_for_date(
            e.work, company_id=cid, observation_date=later), tol)
        assert canon["has_eod_observation"] is False and canon["reconciliation_status"] == "pending"
        for f in reconciliation.END_OF_DAY_FIELDS:
            assert canon[f] is None and canon["field_provenance"][f]["status"] == "not_yet_available", f
    e.work.rollback()
    # canonical rows: written with the EOD fields NULL, or - while frozen Stage E defect D-2 stands (see the xfail
    # test below) - failed per security with that reason, the capture state untouched either way
    assert s["D"]["written"] + len(s["D"]["failed"]) == 5
    assert all("Decimal is not JSON serializable" in f["reason"] for f in s["D"]["failed"])
    rows = q(e.ctl, "select d.closing_price, d.high, d.low, d.turnover, d.share_volume, d.trade_count, "
                    "d.has_eod_observation from daily_market_data d join companies c on c.id = d.company_id "
                    "where d.trade_date = %s and c.ticker = any(%s)", (later, TRADED))
    assert len(rows) == s["D"]["written"] and all(r == (None,) * 6 + (False,) for r in rows)


@pytest.mark.xfail(strict=True, raises=TypeError, reason=(
    "FROZEN STAGE E DEFECT D-2 - reported for separate review, NOT fixed in P2: worker/db.upsert_daily_market_data "
    "serialises field_provenance / discrepancy_notes with plain json.dumps, but reconciliation.reconcile copies raw "
    "observation VALUES into them (intraday_values, superseded_values, alternate_values) and "
    "db.get_raw_observations_for_date returns PostgreSQL numeric columns as Decimal -> TypeError. Stage E's own test "
    "used in-memory floats and a fake connection, so the real database path was never exercised. When D-2 is fixed "
    "this test XPASSes and, being strict, fails: update it then."))
@pytest.mark.parametrize("case", ["post_open_only", "post_open_then_post_close_moving_price"])
def test_frozen_stage_e_upsert_of_database_read_observations(pg, case):
    """Uses ONLY frozen Stage E functions (db, reconciliation, validation) against a real database."""
    import uuid
    from worker import db as stage_e_db, reconciliation, validation
    from worker.capture_multiple_companies import DEFAULT_TOLERANCES as TOL
    w = conn(pg)
    ticker = f"D2T{uuid.uuid4().hex[:6].upper()}.N0000"
    cid = q(w, "insert into companies (ticker, company_name) values (%s, 'D-2 regression') returning id", (ticker,))[0][0]
    day = date(2031, 1, 6)
    windows = [("post_open", 100.25)] + ([("post_close", 101.5)] if case != "post_open_only" else [])
    for i, (window, price) in enumerate(windows):
        fields = {"last_traded_price": price, "closing_price": price if window == "post_close" else 0.0,
                  "high": 102.0, "low": 99.0, "turnover": 1000.5, "share_volume": 10, "trade_count": 2,
                  "post_open_price": price if window == "post_open" else None}
        stage_e_db.insert_raw_observation(w, request_attempt_id=str(uuid.uuid4()), ingestion_job_id=None,
                                          company_id=str(cid), observation_date=day, capture_window=window,
                                          source="CSE_API", observed_at=datetime(2031, 1, 6, 4 + 5 * i,
                                                                                 tzinfo=timezone.utc),
                                          fields=fields, raw_payload={"test": "D-2"})
    canonical = reconciliation.reconcile(stage_e_db.get_raw_observations_for_date(
        w, company_id=str(cid), observation_date=day), TOL)
    canonical["validation_status"], canonical["validation_notes"] = validation.validate(canonical, None, TOL)
    try:
        stage_e_db.upsert_daily_market_data(w, company_id=str(cid), trade_date=day, canonical=canonical)
    finally:
        w.rollback()
        w.close()


def test_record_missed_is_final(env):
    from worker.market_capture import capture, runs
    e = env()
    state, rep = capture.record_missed(e.ctl, e.cfg, trading_date=date(2026, 9, 2), capture_mode="post_close",
                                       reason="server was powered off during the window", rt=e.rt)
    assert state == "missed" and state_of(e.ctl, rep["run_id"]) == "missed"
    with pytest.raises(runs.RunRefused):
        capture.resume(e.ctl, e.work, e.cfg, rep["run_id"], rt=e.rt)
    with pytest.raises(Exception, match="illegal state transition"):
        runs.append_event(e.ctl, rep["run_id"], "running")
    assert e.cse.calls == []


def test_state_machine_is_enforced_by_the_database(env, pg):
    from worker.market_capture import runs
    e = env()
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    for bad in ("running", "pending", "failed"):
        with pytest.raises(Exception, match="illegal state transition"):
            runs.append_event(e.ctl, rep["run_id"], bad)
    su = conn(pg, "postgres", autocommit=True)
    with pytest.raises(Exception, match="next event must be seq"):
        q(su, "insert into market_capture_run_events (run_id, seq, state) values (%s, 99, 'running')",
          (rep["run_id"],))
    su.close()


# ------------------------------------------------------------------------------------------------ completeness / F / F5

def test_completeness_answers_every_question_from_the_database(env):
    from worker.market_capture import capture
    e = env()
    state, rep = e.capture()
    st = capture.status(e.ctl, run_id=rep["run_id"])
    c = st["completeness"]
    assert st["run"]["trading_date"] == TD and st["run"]["trading_date_basis"] == "operator"
    assert c["requests"]["by_endpoint"] == {"allSecurityCode": 1, "tradeSummary": 1, "companyInfoSummery": 7}
    assert c["failed_requests"] == []
    assert sorted(c["B"]["universe_symbols"]) == sorted(TRADED + ABSENT)
    assert sorted(c["B"]["trade_summary_symbols"]) == sorted(TRADED)
    assert c["absent_fallback"]["requested"] == ABSENT and c["absent_fallback"]["not_archived"] == []
    assert sorted(c["cross_check"]["sampled"]) == sorted(TRADED)
    assert c["C"]["produced"] == 7 and c["D"]["written"] == 7
    assert set(c["E"]["reconciliation_status"]) <= {"single_source", "agreed", "discrepancy_flagged", "pending"}
    assert [h["state"] for h in st["history"]] == ["pending", "running", "succeeded"]
    assert st["F_protection"]["available"] is False                                  # the worker cannot read ops
    per = q(e.ctl, "select symbol, role from market_capture_security_results where run_id = %s", (rep["run_id"],))
    assert dict(per) == {**{s: "traded" for s in TRADED}, **{s: "absent_fallback" for s in ABSENT}}


def test_protection_is_computed_separately_and_a_backup_failure_never_changes_a_capture(env, pg):
    from worker.market_capture import completeness
    e = env()
    state, rep = e.capture(absent_fallback=False, cross_check_size=0)
    run = rep["run_id"]
    b = conn(pg, "cse_backup")
    assert completeness.protection(b, run)["level"] == "local_only"
    su = conn(pg, "postgres", autocommit=True)
    q(su, "insert into ops.backup_runs (run_kind, status, started_at, finished_at, error) values "
          "('local_dump', 'failed', now() + interval '1 hour', now() + interval '2 hours', 'disk full')")
    assert completeness.protection(b, run)["level"] == "local_only" and state_of(e.ctl, run) == "succeeded"
    key = "pg/dumps/2099/01/cse_test"
    q(su, "insert into ops.backup_runs (run_kind, status, started_at, finished_at, artifact_key, artifact_sha256, "
          "manifest_sha256) values ('local_dump', 'succeeded', now() + interval '1 hour', now() + interval '2 hours', "
          "%s, %s, %s)", (key, "a" * 64, "b" * 64))
    assert completeness.protection(b, run)["level"] == "local_backup"
    q(su, "insert into ops.backup_runs (run_kind, status, started_at, finished_at, offsite_snapshot, covers) values "
          "('offsite_sync', 'succeeded', now() + interval '3 hours', now() + interval '4 hours', %s, %s)",
      ("c" * 64, json.dumps([key])))
    q(su, "insert into ops.backup_runs (run_kind, status, started_at, finished_at, artifact_key, covers) values "
          "('restore_check', 'succeeded', now() + interval '5 hours', now() + interval '6 hours', %s, %s)",
      (key, json.dumps([key])))
    assert completeness.protection(b, run)["level"] == "restore_verified" and state_of(e.ctl, run) == "succeeded"
    b.close()
    su.close()


def test_f5_issuer_linking_still_finds_company_info_bodies(env, pg):
    from worker import link_issuers
    e = env()
    e.capture()                                              # bodies for all 7 (2 fallback + 5 cross-check)
    e.capture(absent_fallback=False, cross_check_size=0)     # later tradeSummary-only observations
    w = conn(pg)
    got = {b["query_symbol"]: b for b in link_issuers.market_observation_bodies(w)}
    w.close()
    for s in TRADED + ABSENT:
        assert got[s]["body"]["reqSymbolInfo"]["symbol"] == s     # a later TS-only row never hides a real body


def test_metadata_sweep_archives_only_and_exports_f5_input(env, tmp_path):
    from worker.market_capture import capture, config as cfgmod, runs
    e = env()
    state, rep = capture.start(e.ctl, e.work, e.cfg, trading_date=TD, policy=cfgmod.sweep_policy(), rt=e.rt)
    assert state == "succeeded" and e.cse.keys()[0] == "allSecurityCode" and len(e.cse.calls) == 8
    assert "tradeSummary" not in e.cse.keys()
    assert q(e.ctl, "select count(*) from raw_market_observations where request_attempt_id = %s",
             (rep["run_id"],)) == [(0,)]
    out = capture.export_company_info(e.ctl, rep["run_id"], str(tmp_path / "ci.json"))
    items = json.load(open(out["file"]))
    assert out["bodies"] == 7 and {i["query_symbol"] for i in items} == set(TRADED + ABSENT)
    assert all(set(i) == {"query_symbol", "observed_at", "body", "source_ref"} for i in items)
    with pytest.raises(runs.RunRefused, match="inside the repository"):
        capture.export_company_info(e.ctl, rep["run_id"], os.path.join(REPO, "exported.json"))


# ------------------------------------------------------------------------------------------------ command line

def test_command_line_end_to_end(env, capsys, tmp_path):
    """The real CLI (argument parsing, connections from the environment, JSON report, exit codes), with only the
    transport and clocks injected."""
    from worker.market_capture import cli
    e = env()
    envvars = {"CSE_DB_NAME": "cse", "CSE_DB_HOST": e.pg.sockdir, "CSE_DB_PORT": str(e.pg.port),
               "CSE_DB_USER": "cse_worker", "CSE_BACKUP_ROOT": str(e.root), "CSE_PG_BINDIR": BINDIR,
               "CSE_CAPTURE_CONTACT_EMAIL": EMAIL}
    assert cli.main(["capture", "--trading-date", "2026-09-04", "--mode", "post_close"], rt=e.rt, env=envvars) == 0
    rep = json.loads(capsys.readouterr().out)
    run = rep["run_id"]
    assert rep["state"] == "succeeded" and len(e.cse.calls) == 9
    assert cli.main(["status", "--run-id", run], env=envvars) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["state"]["state"] == "succeeded" and st["completeness"]["C"]["status"] == "complete"
    assert cli.main(["verify-archive", "--run-id", run], env=envvars) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []
    assert cli.main(["reprocess", "--run-id", run], rt=e.rt, env=envvars) == 0
    capsys.readouterr()
    out = str(tmp_path / "ci.json")
    assert cli.main(["export-company-info", "--run-id", run, "--out", out], env=envvars) == 0
    assert cli.main(["export-company-info", "--run-id", run, "--out", out], env=envvars) == 1   # never overwritten
    capsys.readouterr()
    assert cli.main(["record-missed", "--trading-date", "2026-09-03", "--mode", "post_close", "--reason",
                     "power cut during the window"], env=envvars) == 0
    assert cli.main(["capture", "--trading-date", "2026-09-05", "--mode", "post_close"], rt=e.rt,
                    env=envvars) == 5                                                   # future date: refused
    assert cli.main(["capture", "--trading-date", "2026-09-04", "--mode", "post_close"], rt=e.rt,
                    env={**envvars, "CSE_DB_USER": "cse_backup"}) == 5                  # wrong role: refused
    assert len(e.cse.calls) == 9                                                         # no request from refusals
