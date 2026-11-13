"""
Phase 3, step MF-1 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, sections 4, 5 and 16): modes and admission,
session finality and D*, the daily bar through the frozen reconcile, the feature-bar decision table, the reference
price with its source session.

Pure and offline: no database, no network, no clock. Evidence is built in memory in the shape the frozen Stage E and
P2 code produces. The leakage tests plant information that did not exist at the cutoff and require an unchanged
result, and each also asserts the converse (the same information at or before the cutoff does change it).
"""
import ast
import hashlib
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from worker.market_features import bars, canonical, modes, reference, sessions
from worker.market_features.bars import SecurityResult
from worker.market_features.modes import AVAILABLE, CURRENT, KNOWN, Query, Refused
from worker.market_features.sessions import TerminalEvent

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGE = os.path.join(REPO, "worker", "market_features")
UTC = timezone.utc
TOL = {"reconciliation_price_tolerance_pct": 0.1, "reconciliation_volume_tolerance": 0,
       "reconciliation_turnover_tolerance_pct": 0.5, "cross_check_closing_price_tolerance_pct": D("0.1"),
       "cross_check_last_traded_price_tolerance_pct": D("0.1"), "cross_check_turnover_tolerance_pct": D("0.5"),
       "cross_check_market_cap_tolerance_pct": D("0.5")}
MIN_COVERAGE = D("0.95")
D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def event(run_id, day, *, recorded=None, state="succeeded", e1=True, e2=True, universe=True, expected=100, produced=100,
          pass_no=1, kind="market_capture", mode="post_close"):
    recorded = recorded or at(day, 11)
    return TerminalEvent(run_id=run_id, trading_date=day, run_kind=kind, capture_mode=mode, state=state,
                         recorded_at=recorded, available_at=recorded - timedelta(minutes=2), pass_no=pass_no,
                         session_matches_trading_date=e1, all_closing_prices_published=e2, universe_known=universe,
                         expected=expected, produced=produced)


def obs(oid, run_id, *, close, window="post_close", observed=None, high=None, low=None, volume=None, trades=None,
        turnover=None, open_price=None, payload=None, source="CSE_API"):
    observed = observed or at(D1, 10)
    return {"id": oid, "request_attempt_id": run_id, "capture_window": window, "source": source, "observed_at": observed,
            "closing_price": close, "last_traded_price": close, "last_traded_date": None, "high": high, "low": low,
            "share_volume": volume, "trade_count": trades, "turnover": turnover, "open_price": open_price,
            "foreign_holding": None, "post_open_price": None, "raw_payload": payload or {}}


def result(run_id, symbol="ABC.N0000", *, universe=True, ts=True, status="produced", pass_no=1, oid="o1"):
    return SecurityResult(run_id=run_id, pass_no=pass_no, symbol=symbol, company_id="c1", in_universe=universe,
                          in_trade_summary=ts, raw_status=status, raw_observation_id=oid)


G = event("run-1", D1)
RUNS = {"run-1", "run-0"}


def bar(observations, res=None, governing=G, runs=RUNS, tolerances=TOL):
    return bars.feature_bar("ABC.N0000", res if res is not None else result(governing.run_id), observations,
                            governing, runs, tolerances)


# ------------------------------------------------------------------------------------------------ modes and admission

def test_m1_modes_admit_evidence_by_the_right_clock_and_refuse_malformed_queries():
    t = at(D2, 12)
    known = Query(KNOWN, t)
    assert known.label == "point_in_time"
    assert known.admits(t, t + timedelta(days=9))                          # KNOWN has no availability clause
    assert not known.admits(t + timedelta(seconds=1), t - timedelta(days=9))
    avail = Query(AVAILABLE, t, t + timedelta(days=30))
    assert avail.label == "reconstructed"
    assert avail.admits(t + timedelta(days=30), t)                         # published by T, recorded by H
    assert not avail.admits(t + timedelta(days=31), t)                     # recorded after H            (ML-17)
    assert not avail.admits(t, t + timedelta(seconds=1))                   # published after T, recorded (ML-16)
    current = Query(CURRENT, None, t)
    assert current.label == "retrospective_current" and current.admits(t, t + timedelta(days=99))
    for bad in (lambda: Query("LATEST", t), lambda: Query(KNOWN, t, t), lambda: Query(KNOWN, datetime(2026, 1, 1)),
                lambda: Query(AVAILABLE, t, t - timedelta(seconds=1)), lambda: Query(AVAILABLE, t),
                lambda: Query(CURRENT, t, t)):
        with pytest.raises(Refused):
            bad()


def test_m2_a_current_result_is_refused_on_a_point_in_time_path():                                  # ML-12
    class Result:
        def __init__(self, mode, label):
            self.mode, self.label = mode, label
    assert modes.require_point_in_time(Result(KNOWN, "point_in_time")).mode == KNOWN
    assert modes.require_point_in_time(Result(AVAILABLE, "reconstructed")).mode == AVAILABLE
    for mode, label in ((CURRENT, "retrospective_current"), (KNOWN, "retrospective_current"), (CURRENT, "point_in_time")):
        with pytest.raises(Refused) as exc:
            modes.require_point_in_time(Result(mode, label))
        assert exc.value.code == "current_not_point_in_time"


# ------------------------------------------------------------------------------------------------ finality and D*

def test_s1_finality_needs_every_condition_and_the_latest_qualifying_event_governs():
    q = Query(KNOWN, at(D1, 23))
    assert sessions.governing_event([G], D1, q, MIN_COVERAGE) is G
    for broken in (event("r", D1, e1=False), event("r", D1, e2=False), event("r", D1, universe=False),
                   event("r", D1, produced=94), event("r", D1, pass_no=None), event("r", D1, state="failed"),
                   event("r", D1, kind="metadata_sweep", mode="metadata_sweep"), event("r", D1, mode="post_open")):
        assert sessions.governing_event([broken], D1, q, MIN_COVERAGE) is None
    assert sessions.governing_event([event("r", D1, state="partial", produced=95)], D1, q, MIN_COVERAGE) is not None
    later = event("run-2", D1, recorded=at(D1, 14))
    assert sessions.governing_event([G, later], D1, q, MIN_COVERAGE) is later
    # a LATER event that does not qualify (an operator's failed attempt) does not turn the date back into non-final
    assert sessions.governing_event([G, event("run-3", D1, recorded=at(D1, 15), e1=False)], D1, q, MIN_COVERAGE) is G


def test_s2_an_early_capture_leaves_a_non_final_session_and_d_star_never_advances_onto_it():      # ML-20
    early = event("run-e", D2, e2=False)                              # closes not published yet
    q = Query(KNOWN, at(D2, 23))
    assert sessions.classify([G, early], D2, q, MIN_COVERAGE) == sessions.NON_FINAL
    assert sessions.market_cutoff([G, early], q, MIN_COVERAGE) == D1
    # a second run for the same date that meets the conditions makes it final from its own recorded_at
    second = event("run-f", D2, recorded=at(D2, 16))
    assert sessions.market_cutoff([G, early, second], Query(KNOWN, at(D2, 15)), MIN_COVERAGE) == D1
    assert sessions.market_cutoff([G, early, second], Query(KNOWN, at(D2, 16)), MIN_COVERAGE) == D2
    # a date with no session evidence at all is neither
    assert sessions.classify([G], D3, q, MIN_COVERAGE) == sessions.NO_EVIDENCE
    assert sessions.classify([G], D3, q, MIN_COVERAGE, has_admitted_observation=True) == sessions.NON_FINAL


def test_s3_run_evidence_recorded_after_the_cutoff_changes_nothing_at_the_cutoff():               # ML-14
    q = Query(KNOWN, at(D2, 9))
    before = (sessions.market_cutoff([G], q, MIN_COVERAGE), sessions.final_sessions([G], q, MIN_COVERAGE))
    planted = [G, event("run-9", D2, recorded=at(D2, 11)), event("run-8", D1, recorded=at(D2, 10))]
    assert (sessions.market_cutoff(planted, q, MIN_COVERAGE), sessions.final_sessions(planted, q, MIN_COVERAGE)) == before
    # the converse: the same evidence recorded at the cutoff does change the result
    moved = [G, event("run-9", D2, recorded=at(D2, 9))]
    assert sessions.market_cutoff(moved, q, MIN_COVERAGE) == D2
    # AVAILABLE: published after T is invisible whatever H is; published by T and recorded by H is visible
    late = event("run-9", D2, recorded=at(D3, 9))
    assert sessions.market_cutoff([G, late], Query(AVAILABLE, at(D2, 9), at(D3, 23)), MIN_COVERAGE) == D1
    assert sessions.market_cutoff([G, late], Query(AVAILABLE, at(D3, 9), at(D3, 23)), MIN_COVERAGE) == D2


# ------------------------------------------------------------------------------------------------ the feature bar

def test_b1_row_1_a_traded_security_with_a_published_close():
    b = bar([obs("o1", "run-1", close=D("100.5"), high=D("101"), low=D("99"), volume=1200, trades=14,
                 turnover=D("120000"), open_price=D("99.5"))])
    assert (b.row, b.traded, b.px_close, b.price_unknown, b.quality) == (1, True, D("100.5"), False, "fresh")
    assert (b.share_volume, b.trade_count, b.turnover, b.open_price) == (D(1200), D(14), D("120000"), D("99.5"))
    assert b.contributing_observation_ids == ("o1",) and b.flags == ()
    assert b.raw["closing_price"] == D("100.5")                         # reconcile's own result is kept untouched


def test_b2_row_2_a_zero_close_is_not_a_price_and_the_price_is_unknown():
    b = bar([obs("o1", "run-1", close=D("0"), volume=500, trades=3, turnover=D("9000"))])
    assert (b.row, b.traded, b.px_close, b.price_unknown, b.quality) == (2, True, None, True, "partial")
    assert "close_unpublished" in b.flags and b.raw["closing_price"] == D("0")


def test_b3_rows_3_and_4_absent_from_trade_summary_means_no_trade_only_by_recorded_membership():
    absent = result("run-1", ts=False)
    b3 = bar([obs("o1", "run-1", close=D("42"))], absent)               # a fallback observation with a printed close
    assert (b3.row, b3.traded, b3.px_close, b3.price_unknown) == (3, False, None, False)
    assert b3.printed_close_no_trade == D("42") and "MFB-3" in b3.flags
    assert (b3.share_volume, b3.trade_count, b3.turnover) == (D(0), D(0), D(0))
    b4 = bar([], result("run-1", ts=False, status="source_missing", oid=None))
    assert (b4.row, b4.traded, b4.price_unknown) == (4, False, False) and "no_observation" in b4.flags
    # a symbol in neither the universe nor tradeSummary is not a member that day
    assert bar([], result("run-1", universe=False, ts=False)) is None


def test_b4_row_5_a_traded_symbol_without_an_observation_has_an_unknown_price_never_a_zero_volume():   # ML-24
    failed = bar([], result("run-1", ts=True, status="insert_failed", oid=None))
    assert (failed.row, failed.traded, failed.px_close, failed.price_unknown, failed.quality) == (5, True, None, True,
                                                                                                 "missing")
    assert failed.share_volume is None and failed.turnover is None
    uncovered = bars.feature_bar("ABC.N0000", None, [], G, RUNS, TOL)   # no row in the governing pass at all
    assert (uncovered.row, uncovered.traded, uncovered.price_unknown) == (5, None, True)
    with pytest.raises(ValueError):                                     # a row of another pass is never read (ML-22)
        bar([], result("run-1", pass_no=2))
    with pytest.raises(ValueError):
        bar([], result("run-0"))


def test_b5_mfb7_only_the_governing_runs_post_close_observations_are_reconciled():                # ML-20
    early = obs("o0", "run-0", close=D("0"), volume=100, observed=at(D1, 8))
    final = obs("o1", "run-1", close=D("100"), volume=900, observed=at(D1, 10))
    b = bar([early, final])
    assert (b.row, b.px_close, b.quality, b.superseded_observation_ids) == (1, D("100"), "fresh", ("o0",))
    # an early run's NON-ZERO fallback close, superseded by a different close after the security traded: no conflict
    b = bar([obs("o0", "run-0", close=D("98"), observed=at(D1, 8)), final])
    assert b.quality == "fresh" and b.px_close == D("100")
    # without MFB-7 the frozen reconcile flags exactly this (the reason the rule exists)
    from worker import reconciliation
    assert reconciliation.reconcile([early, final], TOL)["reconciliation_status"] == "discrepancy_flagged"
    # a post_open observation is not superseded; it is passed on and never supplies an end-of-day value
    intraday = obs("o2", "run-0", close=D("55"), window="post_open", observed=at(D1, 4))
    b = bar([intraday, final])
    assert b.px_close == D("100") and b.superseded_observation_ids == () and "o2" not in b.superseded_observation_ids
    # an observation that belongs to no capture run is never used
    assert bar([obs("o9", "manual-import", close=D("1")), final]).px_close == D("100")


def test_b6_mfb5_mfb6_and_mfb8():
    b = bar([obs("o1", "run-1", close=D("100"), high=D("99"), low=D("97"))])
    assert "MFB-5" in b.flags and b.quality == "fresh" and b.px_close == D("100")     # a genuine CSE value: kept
    other = obs("o2", "run-1", close=D("120"), source="CSE_BULLETIN_DAILY", window="bulletin_daily")
    b = bar([obs("o1", "run-1", close=D("100")), other])
    assert "MFB-6" in b.flags and b.quality == "conflicting"                          # reconcile's own discrepancy
    # MFB-8: MF recomputes the cross-check under its own tolerances and ignores agree_exactly
    turnover_only = {"cross_source_comparison": {
        "turnover": {"companyInfoSummery_value": 41703944.0, "tradeSummary_value": 41703942.5, "agree_exactly": False},
        "closing_price": {"companyInfoSummery_value": 100.0, "tradeSummary_value": 100.0, "agree_exactly": True}}}
    b = bar([obs("o1", "run-1", close=D("100"), payload=turnover_only)])
    assert b.quality == "fresh" and b.flags == ()                                     # a rupee or two is not a conflict
    price = {"cross_source_comparison": {
        "closing_price": {"companyInfoSummery_value": 100.0, "tradeSummary_value": 104.0, "agree_exactly": False},
        "turnover": {"companyInfoSummery_value": 100.0, "tradeSummary_value": 200.0, "agree_exactly": False}}}
    b = bar([obs("o1", "run-1", close=D("100"), payload=price)])
    assert b.quality == "conflicting" and "MFB-8" in b.flags and "cross_check_differs:turnover" in b.flags
    lying = {"cross_source_comparison": {"closing_price": {"companyInfoSummery_value": 100.0,
                                                            "tradeSummary_value": 104.0, "agree_exactly": True}}}
    assert bar([obs("o1", "run-1", close=D("100"), payload=lying)]).quality == "conflicting"


def test_b7_payload_fields_come_from_trade_summary_when_traded_and_company_info_otherwise():
    traded = {"tradeSummary_matched_row": {"marketCap": 317170605201.25, "price": 203.75}}
    b = bar([obs("o1", "run-1", close=D("203.75"), payload=traded)])
    assert b.market_cap_reported == D("317170605201.25") and b.quantity_issued is None
    info = {"companyInfoSummery": {"body": {"reqSymbolInfo": {"marketCap": 5000.0, "quantityIssued": 1556665547}}}}
    b = bar([obs("o1", "run-1", close=D("0"), payload=info)], result("run-1", ts=False))
    assert b.market_cap_reported == D("5000.0") and b.quantity_issued == D(1556665547)


def test_b8_a_later_observation_changes_nothing_for_a_bar_built_from_the_admitted_ones():          # ML-1 to ML-3
    q = Query(KNOWN, at(D1, 12))
    rows = [dict(obs("o1", "run-1", close=D("100"), observed=at(D1, 10)), created_at=at(D1, 10, 1)),
            dict(obs("o2", "run-1", close=D("140"), source="CSE_BULLETIN_DAILY", window="bulletin_daily",
                     observed=at(D1, 11)), created_at=at(D1, 13))]            # recorded after the cutoff
    admitted = [o for o in rows if q.admits(o["created_at"], o["observed_at"])]
    assert [o["id"] for o in admitted] == ["o1"] and bar(admitted).px_close == D("100") and bar(admitted).quality == "fresh"
    # the converse: recorded at or before the cutoff, the same observation does change the bar
    late_q = Query(KNOWN, at(D1, 13))
    assert bar([o for o in rows if late_q.admits(o["created_at"], o["observed_at"])]).quality == "conflicting"


# ------------------------------------------------------------------------------------------------ the reference price

def make(px=None, unknown=False, traded=None):
    return bars.FeatureBar(symbol="ABC.N0000", row=1 if px is not None else (2 if unknown else 3),
                           traded=traded if traded is not None else (px is not None or unknown), px_close=px,
                           price_unknown=unknown, share_volume=None, trade_count=None, turnover=None, open_price=None,
                           high=None, low=None, quality="fresh")


def test_r1_a_carried_price_travels_with_the_session_it_was_printed_on():
    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]
    series = reference.reference_series(list(zip(days, [None, make(D("100")), make(), make(), make(D("104"))])))
    assert [(r.px_ref, r.ref_session, r.stale_sessions, r.state) for r in series] == [
        (None, None, None, "no_price_yet"), (D("100"), days[1], 0, "known"), (D("100"), days[1], 1, "known"),
        (D("100"), days[1], 2, "known"), (D("104"), days[4], 0, "known")]


def test_r2_while_the_price_is_unknown_nothing_is_carried_until_the_next_printed_close():           # ML-24
    days = [date(2026, 11, d) for d in (2, 3, 4, 5)]
    # X closes 100; trades on the next session with no usable close; does not trade the day after; prints 111
    series = reference.reference_series(list(zip(days, [make(D("100")), make(unknown=True), make(), make(D("111"))])))
    assert [(r.px_ref, r.ref_session, r.stale_sessions, r.state) for r in series] == [
        (D("100"), days[0], 0, "known"), (None, None, None, "price_unknown"), (None, None, None, "price_unknown"),
        (D("111"), days[3], 0, "known")]
    # so no zero return and no carried price is asserted over the day it traded


# ------------------------------------------------------------------------------------------------ static guards

def test_g1_the_frozen_reconcile_is_unchanged_and_is_the_one_mf_calls():
    path = os.path.join(REPO, "worker", "reconciliation.py")
    digest = hashlib.sha256(open(path, "rb").read().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == "65b4d36cd22ece42ae5ed19da6de1aec157537616835b9708c34c5d364717326"      # P2's own pin
    from worker import reconciliation
    assert bars.reconciliation is reconciliation


def test_g2_the_package_is_pure_no_database_network_clock_or_forbidden_source():
    allowed = {"dataclasses", "datetime", "decimal", "typing", "hashlib", "json", "fractions"}
    forbidden_words = ("daily_market_data", "daily_index_data", "trading_calendar ", "from corporate_actions",
                       "symbol_history ", "system_config ", "psycopg2", "requests", "urllib", "socket", "datetime.now",
                       "time.time", "open(")
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(PACKAGE, name), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                assert {a.name.split(".")[0] for a in node.names} <= allowed, (name, [a.name for a in node.names])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert node.module.split(".")[0] in allowed, (name, node.module)
                else:                                  # relative: this package, or the frozen reconciliation only
                    assert (node.level == 1) or (node.level == 2 and node.module is None
                                                 and [a.name for a in node.names] == ["reconciliation"]), (name, node.module)
        code = "\n".join(line.split("#")[0] for line in src.split('"""')[::2])     # outside docstrings and comments
        for word in forbidden_words:
            assert word not in code, (name, word)


def test_g3_canonical_json_is_exact_and_the_security_key_is_computed_from_the_symbol():
    a = canonical.canonical_json({"b": D("1.10"), "a": [date(2026, 11, 2), at(D1, 1)], "c": None})
    assert a == '{"a":[{"date":"2026-11-02"},{"instant":"2026-11-02T01:00:00+00:00"}],"b":{"decimal":"1.10"},"c":null}'
    with pytest.raises(TypeError):
        canonical.canonical_json({"x": 1.5})
    key = canonical.security_id("ABC.N0000")
    assert key == canonical.security_id("ABC.N0000") != canonical.security_id("ABC.X0000") and len(key) == 64
    for bad in ("", " ABC.N0000", None):
        with pytest.raises(ValueError):
            canonical.security_id(bad)
