"""
MF-8 part 1 (branch review Blocker B1): MF reads Phase 2's captured data. Phase 2's real capture path runs against
the fake CSE (fixtures, a fake clock; no network, no CSE) into real PostgreSQL, and MF's captured loader reads what
it wrote: the final session and its governing run, the feature bars of the governing pass (traded rows 1, absent
rows 3 under MFB-3), each security's company_id, CSE's reported market cap from the stored payload, the query's
admission (nothing before its T), and a session that is proven but not final (a post_open run). Skipped unless
P1_PG_BINDIR is set.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from test_p2_capture_postgres import ABSENT, BINDIR, TD, TRADED, conn, env, isolate, pg, q  # noqa: F401
from worker.market_feature_store import captured
from worker.market_feature_store.configuration import CaptureConfig
from worker.market_features.modes import AVAILABLE, KNOWN, Query

pytestmark = pytest.mark.skipif(not BINDIR, reason="P1_PG_BINDIR not set")
CAP = CaptureConfig()


def known(t):
    return Query(KNOWN, t)


def load(pg, query):
    return captured.load(conn(pg), query, CAP.min_coverage, CAP.tolerance_dict())


def test_c1_a_post_close_capture_is_a_final_session_with_company_ids_and_bars(env, pg):
    e = env()
    state, rep = e.capture()
    assert state == "succeeded", json.dumps(rep["summary"], default=str)[:2000]
    run = rep["run_id"]
    cap = load(pg, known(datetime.now(timezone.utc)))
    assert [d for d, _g in cap.finals] == [TD] and str(cap.finals[0][1].run_id) == run
    assert cap.non_finals == () and cap.members[TD] == frozenset(TRADED + ABSENT)
    ids = dict(q(conn(pg), "select ticker, id::text from companies where ticker = any(%s)", (TRADED + ABSENT,)))
    assert cap.company_ids == ids                                     # the link Phase 4 follows
    for s in TRADED:
        b = cap.bar_on(s, TD)
        assert b.row == 1 and b.traded and b.px_close is not None and b.px_close > 0, (s, b.flags)
        assert b.market_cap_reported is not None                     # CSE's marketCap from the stored payload
        assert b.contributing_observation_ids
    for s in ABSENT:
        b = cap.bar_on(s, TD)
        assert b.row == 3 and b.traded is False and "MFB-3" in b.flags and b.px_close is None
    assert cap.evidence["runs"] == [[run, rep["derivation"]["pass_no"]]]
    assert captured.dstar_observation_ids(cap, TRADED[0], TD) == sorted(cap.bar_on(TRADED[0], TD)
                                                                      .contributing_observation_ids)


def test_c2_nothing_is_admitted_after_the_querys_cutoff(env, pg):
    first = env()
    assert first.capture()[0] == "succeeded"
    before = datetime.now(timezone.utc)
    e = env()
    state, rep = e.capture()                                          # a second post_close run of the same day
    assert state == "succeeded"
    newer = load(pg, known(datetime.now(timezone.utc)))
    assert str(dict(newer.finals)[TD].run_id) == rep["run_id"]        # the latest admitted final event governs
    older = load(pg, known(before))
    assert str(dict(older.finals)[TD].run_id) != rep["run_id"]        # recorded after T: not admitted
    far = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert str(dict(load(pg, Query(AVAILABLE, before, before)).finals)[TD].run_id) != rep["run_id"]   # H < its record
    assert str(dict(load(pg, Query(AVAILABLE, before, far)).finals)[TD].run_id) == rep["run_id"]      # H admits it


def test_c3_a_post_open_run_never_governs(env, pg):
    e = env()
    state, rep = e.capture(mode="post_open")
    cap = load(pg, known(datetime.now(timezone.utc)))
    assert all(str(g.run_id) != rep["run_id"] for _d, g in cap.finals)
    assert all(g.capture_mode == "post_close" for _d, g in cap.finals)


def test_c4_coverage_below_the_minimum_is_not_final(env, pg):
    e = env()
    state, rep = e.capture()
    assert state == "succeeded"
    strict = captured.load(conn(pg), known(datetime.now(timezone.utc)), Decimal("1.01"), CAP.tolerance_dict())
    assert TD not in [d for d, _g in strict.finals] and TD in strict.non_finals


def test_c5_observations_are_admitted_by_the_query_and_events_need_a_received_response(env, pg):
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    e = env()
    assert e.capture()[0] == "succeeded"
    c = conn(pg)
    ids = {r[0] for r in q(c, "select id::text from companies where ticker = any(%s)", (TRADED,))}
    with c.cursor() as cur:
        assert captured._observations(cur, known(datetime.now(timezone.utc)), TD, ids)
        assert captured._observations(cur, known(before), TD, ids) == {}      # created after T: not admitted
    c.rollback()

    class Rows:                                                        # one terminal event of a run with no response
        def execute(self, *a):
            pass

        def fetchall(self):
            return [("r1", "market_capture", TD, "post_close", "succeeded", datetime.now(timezone.utc),
                     {"summary": {"B": {"status": "known"}}}, None)]
    assert captured.load_events(Rows(), known(datetime.now(timezone.utc))) == ()


def _events(cap, run_id):
    return [ev for ev in cap.events if str(ev.run_id) == str(run_id)]


def test_c6_audit_f1_a_resume_never_moves_an_earlier_events_availability(env, pg):
    from worker.market_capture import capture
    e = env()
    state, rep = e.capture(max_requests=4)                             # stops early: a partial run
    assert state == "partial"
    run = rep["run_id"]
    first = _events(load(pg, known(datetime.now(timezone.utc))), run)
    assert len(first) == 1
    expect = q(conn(pg), "select max(observed_at) from market_source_responses where run_id = %s and outcome = 'ok' "
                         "and sequence_no <= 4", (run,))[0][0]
    assert first[0].available_at == expect                             # the latest ok response that event saw
    capture.resume(e.ctl, e.work, e.cfg, run, rt=e.rt)                 # more responses, later
    after = _events(load(pg, known(datetime.now(timezone.utc))), run)
    assert len(after) == 2 and after[0].available_at == first[0].available_at
    later = q(conn(pg), "select max(observed_at) from market_source_responses where run_id = %s and outcome = 'ok'",
              (run,))[0][0]
    assert later > first[0].available_at and after[1].available_at == later


def _su(pg):
    c = conn(pg, "postgres")
    with c.cursor() as cur:
        cur.execute("set session_replication_role = replica")           # test surgery below the guards
    c.commit()
    return c


def test_c7_audit_f3_only_the_governing_pass_and_the_latest_member_company(env, pg):
    e1 = env()
    assert e1.capture()[0] == "succeeded"
    e2 = env()
    state, rep = e2.capture()
    run = rep["run_id"]
    su = _su(pg)
    other = q(su, "insert into companies (ticker, company_name) values ('ZZTEST.N0000', 'z') returning id::text")[0][0]
    sym = TRADED[0]
    # a pass no terminal event names (a pass in progress, say): never read
    q(su, "insert into market_capture_security_results (run_id, pass_no, pass_kind, symbol, company_id, in_universe, "
          "in_trade_summary, role, raw_status, canonical_status) values (%s, 99, 'reprocess', %s, %s, true, true, "
          "'traded', 'source_missing', 'not_attempted')", (run, sym, other))
    cap = load(pg, known(datetime.now(timezone.utc)))
    assert cap.bar_on(sym, TD).row == 1 and cap.company_ids[sym] != other
    # the latest governing run's company id wins (first: earlier run; then the later run names another company)
    q(su, "update market_capture_security_results set company_id = %s where run_id = %s and pass_no = %s and "
          "symbol = %s", (other, run, rep["derivation"]["pass_no"], sym))
    assert load(pg, known(datetime.now(timezone.utc))).company_ids[sym] == other
    # a row of the governing pass that is not a member gives no bar and no company link
    q(su, "update market_capture_security_results set in_universe = false, in_trade_summary = false where run_id = %s "
          "and pass_no = %s and symbol = %s", (run, rep["derivation"]["pass_no"], sym))
    cap = load(pg, known(datetime.now(timezone.utc)))
    assert cap.bar_on(sym, TD) is None and cap.company_ids.get(sym) != other and sym not in cap.members[TD]


def test_c8_audit_f3_an_admitted_observation_alone_proves_a_session(env, pg):
    import uuid
    before = datetime.now(timezone.utc)
    su = _su(pg)
    cid = q(su, "select id::text from companies where ticker = %s", (TRADED[1],))[0][0]
    d = TD - timedelta(days=10)
    q(su, "insert into raw_market_observations (request_attempt_id, company_id, observation_date, capture_window, "
          "source, observed_at, closing_price, raw_payload) values (%s, %s, %s, 'manual', 'CSE_API', %s, 10, '{}')",
      (str(uuid.uuid4()), cid, d, datetime(2026, 8, 25, 12, tzinfo=timezone.utc)))
    assert d in load(pg, known(datetime.now(timezone.utc))).non_finals          # proven, not final
    assert d not in load(pg, known(before)).non_finals                          # created after T: no proof
