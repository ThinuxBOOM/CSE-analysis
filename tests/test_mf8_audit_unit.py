"""
MF-8 audit (at 1b2a2be), pure tests for its findings and its surviving mutants: the mapping of P2's terminal-event
summary (E1, E2, universe, coverage, the pass); the merge (F2: no agreement without a comparison, imported-only
values kept, an import that disagrees with itself; the tolerance edge; trade_count compared); a captured final
session's membership (F4); non-final sessions as gaps and as pending after D*; pending bound by the snapshot hash;
the captured evidence in the input digest exactly when there is some; captured observation ids in the provenance;
DE-4's market-cap state filter; the configuration's capture block (F7).
"""
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from worker.market_feature_store import captured, combined, configuration, history_bars as hb, snapshot as sn, store
from worker.market_features import bars
from worker.market_features.modes import AVAILABLE, KNOWN, Query

UTC = timezone.utc
TOL = configuration.CaptureConfig().tolerance_dict()
T0 = datetime(2026, 9, 4, 12, tzinfo=UTC)


def summary(e1=True, e2=True, universe="known", expected=10, produced=10, pass_no=1):
    d = {"summary": {"session_evidence": {"session_matches_trading_date": e1, "all_closing_prices_published": e2},
                     "B": {"status": universe}, "C": {"expected": expected, "produced": produced}}}
    if pass_no is not None:
        d["derivation"] = {"pass_no": pass_no}
    return d


def ev(**kw):
    return captured._summary_event("r1", "market_capture", date(2026, 9, 4), "post_close", "succeeded", T0, T0,
                                   summary(**kw))


def test_a1_the_summary_mapping():
    from worker.market_features import sessions
    ok = ev()
    assert (ok.session_matches_trading_date, ok.all_closing_prices_published, ok.universe_known, ok.pass_no,
            ok.expected, ok.produced) == (True, True, True, 1, 10, 10)
    assert sessions.is_final_evidence(ok, D("0.95"))
    for kw in (dict(e2=False), dict(e1=False), dict(universe="unknown"), dict(produced=9, expected=10),
               dict(pass_no=None)):
        assert not sessions.is_final_evidence(ev(**kw), D("0.95")), kw
    assert ev(expected=10, produced=9).coverage() == D("0.9")          # produced over expected, never swapped
    assert ev(pass_no=None).pass_no is None


def cbar(close="100", **kw):
    b = hb.from_row("A.N0000", {"close": close, "shares": "10", "trades": "3", "turnover": "1000", "high": "101",
                                "low": "99"})
    return replace(b, contributing_observation_ids=("o1",), **kw)


def ibar(**r):
    base = {"close": "100", "shares": "10", "trades": "3", "turnover": "1000", "high": "101", "low": "99"}
    base.update(r)
    return hb.from_row("A.N0000", base)


def test_a2_f2_no_agreement_without_a_comparison_and_nothing_dropped():
    missing = bars.FeatureBar(symbol="A.N0000", row=5, traded=None, px_close=None, price_unknown=True,
                              share_volume=None, trade_count=None, turnover=None, open_price=None, high=None,
                              low=None, quality="missing", flags=("no_observation",))
    b = combined.bar(missing, ibar(), TOL)
    assert "imported_agrees" not in b.flags and "imported_not_compared" in b.flags
    assert "imported_only:px_close=100" in b.flags                      # the imported close is kept, not dropped
    unpublished = replace(cbar(), px_close=None, price_unknown=True, row=2)
    assert "imported_only:px_close=100" in combined.bar(unpublished, ibar(), TOL).flags
    for flag in ("quarantined", "zero_price"):
        q = replace(ibar(), flags=(flag,), px_close=None)
        b = combined.bar(cbar(), q, TOL)
        assert b.quality == "conflicting" and f"imported_{flag}" in b.flags and "imported_agrees" not in b.flags
    assert "imported_agrees" in combined.bar(cbar(), ibar(), TOL).flags


def test_a3_tolerance_edges_and_trade_count():
    # 100 against 99.9: exactly 0.1 % of the larger value, the tolerance itself: within (">", not ">=")
    assert combined.bar(cbar("100"), ibar(close="99.9"), TOL).quality == "fresh"
    assert combined.bar(cbar("99.9"), ibar(close="100"), TOL).quality == "fresh"                 # symmetric
    assert combined.bar(cbar("100"), ibar(close="99.89"), TOL).quality == "conflicting"
    b = combined.bar(cbar(), ibar(trades="4"), TOL)
    assert b.quality == "conflicting" and "imported_differs:trade_count=4" in b.flags
    with pytest.raises(KeyError):
        combined.bar(cbar(), ibar(), {})                                # F7: a missing tolerance is never 0


def days(n, start=date(2026, 8, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def imported(syms, ds):
    rows = {sym: {d: {"close": "100", "shares": "10", "turnover": "1000", "ids": (i,), "instant": T0}
                  for i, d in enumerate(ds)} for sym in syms}
    return {sym: hb.Imported(rows=r) for sym, r in rows.items()}


def test_a4_non_final_sessions_are_gaps_before_dstar_and_pending_after_and_f4_membership():
    ds = days(8)
    q = Query(KNOWN, datetime(2026, 9, 30, tzinfo=UTC))
    imp = imported(["A.N0000", "B.N0000"], ds[:6])
    gap, after = ds[6] + timedelta(days=0), ds[7]
    cap = captured.Captured(finals=((ds[5], ev()),), non_finals=(gap, after),
                            bars={"A.N0000": {ds[5]: cbar()}}, members={ds[5]: frozenset({"A.N0000"})},
                            company_ids={"A.N0000": "c-a"})
    snap = sn.build(imp, [], q, captured=cap, tolerances=TOL)
    assert snap.d_star == ds[5] and snap.pending == (gap, after)       # both after D*: pending, in order
    # a non-final session between two finals is a gap of the sequence, not pending
    cap2 = replace(cap, finals=((ds[5], ev()), (ds[7], ev())), non_finals=(ds[6],),
                   bars={"A.N0000": {ds[5]: cbar(), ds[7]: cbar()}},
                   members={ds[5]: frozenset({"A.N0000"}), ds[7]: frozenset({"A.N0000"})})
    snap2 = sn.build(imp, [], q, captured=cap2, tolerances=TOL)
    assert snap2.pending == () and ds[6] in snap2.sequence.gaps_after[-1]
    # F4: B is not in the governing run's universe on the captured session: its imported bar there is not used
    row_b = {r["symbol"]: r for r in snap.rows}["B.N0000"]
    assert row_b["ref_session"] == ds[4]                               # its last bar is the imported day before
    row_a = {r["symbol"]: r for r in snap.rows}["A.N0000"]
    assert row_a["company_id"] == "c-a" and row_a["ref_session"] == ds[5]


def test_a5_pending_is_bound_by_the_snapshot_hash_and_provenance_lists_captured_ids():
    header = ("KNOWN", "point_in_time", T0, None, date(2026, 9, 4), "c", "mf.3", "d", "a", 0, False)
    assert store.snapshot_hash(header, [], [], [], (T0, "w", "[]")) != \
        store.snapshot_hash(header, [], [], [], (T0, "w", '["2026-09-07"]'))
    ds = days(3)
    cap = captured.Captured(bars={"A.N0000": {ds[2]: cbar()}})
    snap = sn.Snapshot(ds[2], tuple(ds))
    snap.captured = cap
    dstar, digest, window = store.provenance(snap, {}, "A.N0000")
    assert dstar == ["o1"] and window == ["o1"]
    snap.captured = None
    assert store.provenance(snap, {}, "A.N0000")[1] != digest


def test_a6_the_captured_evidence_enters_the_digest_exactly_when_there_is_some(monkeypatch):
    from worker.market_feature_store import jobs
    ds = days(6)
    q = Query(AVAILABLE, datetime(2026, 9, 30, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))
    monkeypatch.setattr(store, "load", lambda conn, query: (imported(["A.N0000"], ds), [], [], {"imports": [1]}))
    monkeypatch.setattr(store, "load_identity", lambda conn, query: ((), []))
    for cap, has in ((captured.Captured(), False),
                     (captured.Captured(non_finals=(ds[-1] + timedelta(days=3),), evidence={"runs": []}), True)):
        monkeypatch.setattr(captured, "load", lambda *a, _c=cap: _c)
        _snap, _imp, evidence = jobs.build(None, q, configuration.Configuration())
        assert ("captured" in evidence) is has


def test_a7_de4_reads_the_market_cap_only_in_an_admissible_state():
    from worker.feature_engine_store import loader
    for state, expect in (("fresh", D("5")), ("stale", D("5")), ("partial", D("5")), ("conflicting", None),
                          ("missing", None), ("untrusted", None)):
        assert loader._market_cap({"market_cap_reported": {"value": "5", "state": state}}) == expect, state
    assert loader._market_cap({}) is None


def test_a8_f7_the_capture_block_is_complete_and_in_range():
    body = configuration.Configuration().body()
    for change in ({"min_coverage": "0"}, {"min_coverage": "1.01"},
                   {"tolerances": {k: v for k, v in list(body["capture"]["tolerances"].items())[1:]}}):
        b = json.loads(json.dumps(body))
        b["capture"].update(change)
        with pytest.raises(configuration.ConfigurationError):
            configuration.parse(json.dumps(b, sort_keys=True, separators=(",", ":")))
