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
