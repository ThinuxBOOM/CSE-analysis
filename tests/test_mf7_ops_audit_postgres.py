"""
MF-7c third part: tests for the findings of its audit (B1-B3, M1, M2, M4): the instants of a no-trade bar, reference
snapshots outside a build's information set, the designation in force at T, header and move-flag tampering of a live
snapshot, an AVAILABLE horizon in the future, disagreeing target builds, the selection's filters and preflight's
privilege checks. The pure parts run always; the rest needs P1_PG_BINDIR. No network, no CSE.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from worker.market_feature_store import cli, configuration, jobs, preflight, selection, store, targets_store, verify
from worker.market_feature_store import history_bars as hb
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, KNOWN, Query

UTC = timezone.utc
BINDIR = os.environ.get("P1_PG_BINDIR")
pg = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------- pure (always run)

def test_a1_a_no_trade_bar_takes_the_instant_of_the_batch_whose_range_makes_it():               # B1
    d0, d20, d39 = date(2024, 1, 1), date(2024, 1, 29), date(2024, 2, 26)
    y, x = T0, T0 + timedelta(days=5)                       # batch Y recorded first; X (the range) later
    imp = hb.Imported(rows={d0: {"close": "1", "flags": "", "instant": y}, d39: {"close": "1", "flags": "", "instant": y}},
                      complete=((d0, d39),), complete_recorded=((d0, d39, x),))
    known = Query(KNOWN, T0 + timedelta(days=10))
    inst = targets_store.session_instants("S.N0000", {"S.N0000": imp}, [d20], {}, known)
    assert inst[d20] == x                                   # not Y's instant: the range is X's evidence
    avail = Query(AVAILABLE, hb.available_at(d39), T0 + timedelta(days=10))
    inst = targets_store.session_instants("S.N0000", {"S.N0000": imp}, [d20], {}, avail)
    assert inst[d20] == x                                   # the third element is the batch's admission instant


def test_a2_admission_instants_by_mode():
    k, a = Query(KNOWN, T0), Query(AVAILABLE, T0, T0 + timedelta(days=1))
    rec, avail = T0 - timedelta(days=1), T0 - timedelta(days=3)
    assert store.admission_instant(k, rec, avail) == rec and store.admission_instant(a, rec, avail) == avail


def test_a3_a_reference_snapshot_must_lie_inside_the_builds_information_set():                    # B2
    k = Query(KNOWN, T0)
    assert targets_store.admissible_reference(k, KNOWN, T0, None, T0 - timedelta(1)) == (T0 - timedelta(1), None)
    assert targets_store.admissible_reference(k, KNOWN, T0, None, T0 + timedelta(seconds=1))[0] is None
    assert targets_store.admissible_reference(k, AVAILABLE, T0, T0, T0)[0] is None
    a = Query(AVAILABLE, T0, T0 + timedelta(days=2))
    assert targets_store.admissible_reference(a, AVAILABLE, T0 - timedelta(5), T0, T0)[0] == T0 - timedelta(5)
    assert targets_store.admissible_reference(a, AVAILABLE, T0 + timedelta(1), T0, T0)[0] is None       # T_ref > T
    assert targets_store.admissible_reference(a, AVAILABLE, T0, T0 + timedelta(3), T0)[0] is None       # H_ref > H
    assert targets_store.admissible_reference(a, KNOWN, T0, None, T0 + timedelta(3))[0] is None         # written > H


def test_a5_the_reference_snapshots_admission_instant_reaches_resolved_at():                     # B2
    from worker.market_feature_store import snapshot as sn
    from worker.market_features import targets
    days, d = [], date(2024, 1, 1)
    while len(days) < 30:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    imported = {f"S{k}.N0000": hb.Imported(
        rows={x: {"close": str(100 + k), "shares": "10", "turnover": "1000", "flags": "",
                  "instant": hb.available_at(x)} for x in days}, complete=((days[0], days[-1]),)) for k in range(3)}
    q = Query(AVAILABLE, hb.available_at(days[-1]), hb.available_at(days[-1]) + timedelta(days=1))
    snap = sn.build(imported, [], q, policy=universe.EligibilityPolicy(min_history_sessions=5,
                                                                        min_median_turnover_lkr=1),
                    config=FeatureConfig(min_constituents=3), keep_series=True)
    row = targets.ReferenceRow("S0.N0000", days[10], snap.references["S0.N0000"][days[10]].px_ref, days[10])
    late = hb.available_at(days[-1]) + timedelta(hours=1)
    labs, _ = targets_store.labels(snap, imported, [], [], q, {7: [row]}, targets.TargetPolicy(), {7: late})
    resolved = [lab for _sid, lab in labs if lab.state == "resolved"]
    assert resolved and all(lab.resolved_at >= late for lab in resolved)


def test_a6_row_instants_and_current_refusals():                                               # N-B1, m17
    d = date(2024, 1, 2)
    late = hb.available_at(d) + timedelta(hours=3)
    assert store.row_instant(Query(KNOWN, late), hb.available_at(d) - timedelta(hours=9), d) == hb.available_at(d)
    assert store.row_instant(Query(KNOWN, late), late, d) == late
    assert store.row_instant(Query(AVAILABLE, late, late), late, d) == hb.available_at(d)
    with pytest.raises(configuration.ConfigurationError):
        targets_store.build(None, Query("CURRENT", None, late), "c" * 64, [1])


def test_a4_configuration_refuses_a_rule_version_and_a_foreign_json():
    body = configuration.Configuration().body()
    body["feature_registry_version"] = "features.0"
    with pytest.raises(configuration.ConfigurationError):
        configuration.parse(json.dumps(body, sort_keys=True, separators=(",", ":")))
    with pytest.raises(configuration.ConfigurationError):
        configuration.parse(json.dumps(configuration.Configuration().body(), sort_keys=True))   # not canonical


# ------------------------------------------------------------------------------------------------ PostgreSQL

if BINDIR:
    from test_mf7_ops_postgres import DAYS, cluster, now, registered, seed, sql, worker  # noqa: F401,E402
    import f64_support as fs  # noqa: E402


def _cfg(n):
    return configuration.Configuration(policy=universe.EligibilityPolicy(min_history_sessions=5,
                                                                         min_median_turnover_lkr=1),
                                       feature_config=FeatureConfig(min_constituents=n))


def _run(cluster, db, argv, user="cse_worker"):
    return cli.run(cli.parser().parse_args(argv), lambda: fs.conn(cluster, db, user=user))


@pg
def test_p1_designated_means_the_designation_in_force_at_t(cluster, capsys):                     # B3, ML-15
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    c1 = configuration.register(worker(cluster, db), _cfg(3))[1]
    t_before = now(cluster, db)
    assert _run(cluster, db, ["snapshot", "--mode", "KNOWN", "--cutoff", t_before.isoformat(), "--designated"]) == 5
    cli.designate(fs.conn(cluster, db, user="cse_migrator"), c1, "the canonical configuration")
    t = now(cluster, db)
    c2 = configuration.register(worker(cluster, db), _cfg(4))[1]
    cli.designate(fs.conn(cluster, db, user="cse_migrator"), c2, "a later canonical configuration")
    capsys.readouterr()
    assert _run(cluster, db, ["snapshot", "--mode", "KNOWN", "--cutoff", t.isoformat(), "--designated"]) == 0
    assert json.loads(capsys.readouterr().out)["configuration_id"] == c1


@pg
def test_p2_an_available_horizon_in_the_future_and_errors_exit_5(cluster):                        # M4
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert _run(cluster, db, ["snapshot", "--mode", "AVAILABLE", "--cutoff", hb.available_at(DAYS[-1]).isoformat(),
                              "--horizon", future, "--configuration", cid]) == 5
    assert _run(cluster, db, ["designate", "--configuration", "f" * 64, "--note", "an unknown configuration"],
                user="cse_migrator") == 5
    with pytest.raises(configuration.ConfigurationError):
        jobs.snapshot(worker(cluster, db), Query("CURRENT", None, now(cluster, db)), cid)
    later = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(configuration.ConfigurationError):
        jobs.snapshot(worker(cluster, db), Query(KNOWN, later), cid)               # refused below the CLI too
    with pytest.raises(configuration.ConfigurationError):
        targets_store.build(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[-1]), later), cid, [1])


CHANGES = [("mf_snapshots", "d_star = d_star - 1"), ("mf_snapshots", "code_version = 'x'"),
           ("mf_snapshots", "imported_inputs = false"), ("mf_snapshots", "input_digest = repeat('0', 64)"),
           ("mf_snapshot_move_flags", "status = 'confirmed_no_action'"),
           ("mf_snapshot_move_flags", "close_to = close_to + 1"),
           ("mf_target_builds", "cutoff = cutoff - interval '1 day'"), ("mf_target_builds", "code_version = 'x'"),
           ("mf_snapshots", "cutoff = cutoff - interval '1 second'"),
           ("mf_snapshots", "knowledge_horizon = knowledge_horizon - interval '1 second'"),
           ("mf_snapshots", "configuration_id = (select max(configuration_id) from mf_configurations "
                            "where configuration_id <> mf_snapshots.configuration_id)"),
           ("mf_snapshots", "recorded_at = recorded_at - interval '1 hour'"),
           ("mf_snapshots", "created_by = 'someone'"), ("mf_snapshots", "pending_sessions = '[\"2024-01-01\"]'"),
           ("mf_snapshot_rows", "company_id = '00000000-0000-0000-0000-000000000001'"),
           ("mf_target_builds", "knowledge_horizon = knowledge_horizon - interval '1 second'"),
           ("mf_target_builds", "configuration_id = (select max(configuration_id) from mf_configurations "
                                "where configuration_id <> mf_target_builds.configuration_id)"),
           ("mf_target_builds", "target_definition_version = 'targets.0'"),
           ("mf_target_builds", "mode = 'KNOWN'"),
           ("mf_target_builds", "recorded_at = recorded_at - interval '1 hour'"),
           ("mf_target_builds", "created_by = 'someone'"),
           ("mf_target_rows", "adjustment_set = '[\"x\"]'")]


@pg
@pytest.mark.parametrize("table,change", CHANGES)
def test_p3_verify_catches_tampering_of_a_live_snapshot_and_of_a_target_build(cluster, table, change):   # M2
    db = fs.fresh_db(cluster)
    seed(cluster, db, split=False)                          # S0's fall is an unexplained move: one move flag
    cid = registered(cluster, db)
    configuration.register(worker(cluster, db), _cfg(5))    # another configuration a tamper can point to
    sid, _ = jobs.snapshot(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[35]), now(cluster, db)), cid)
    targets_store.build(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db)), cid, [sid])
    rep = verify.verify(worker(cluster, db))
    assert rep["ok"] and not rep["snapshots"][0]["replayed"]                            # live: not replayed
    assert sql(cluster, db, "select count(*) from mf_snapshot_move_flags")[0][0] >= 1
    sql(cluster, db, f"alter table {table} disable trigger trg_{table}_append_only")
    sql(cluster, db, f"update {table} set {change}")
    assert not verify.verify(worker(cluster, db))["ok"]


@pg
def test_p4_disagreeing_target_builds_are_reported(cluster):                                      # M3 (§11)
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    ref, _ = jobs.snapshot(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[25]), now(cluster, db)), cid)
    q1 = Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db))
    targets_store.build(worker(cluster, db), q1, cid, [ref])
    o = fs.conn(cluster, db, user="cse_migrator")                         # a correction batch: S1 disagrees on day 30
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                    "price_basis, survivorship, no_trade_convention, values_as_of) values ('fix', repeat('b', 64), "
                    "'t', 't', 'raw', 'complete', 'row_absent', '2026-10-19') returning import_id")
        imp = cur.fetchone()[0]
        cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, source_sheet, "
                    "source_row) values (%s,'S1.N0000',%s,999,1000,200000,'s',1)", (imp, DAYS[30]))
    o.commit()
    targets_store.build(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db)), cid, [ref])
    rep = verify.verify(worker(cluster, db))
    assert rep["ok"]                                        # reported, not a failure of either build
    assert {(d["symbol"], d["horizon"]) for d in rep["target_disagreements"]} == {("S1.N0000", 5), ("S1.N0000", 10)}


@pg
def test_p5_known_recorded_filters_mode_and_configuration(cluster):                                # M1, ML-23
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    c1 = configuration.register(worker(cluster, db), _cfg(3))[1]
    c2 = configuration.register(worker(cluster, db), _cfg(4))[1]
    cli.designate(fs.conn(cluster, db, user="cse_migrator"), c1, "the canonical configuration")
    k1, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, now(cluster, db)), c1)
    t_late = now(cluster, db)
    jobs.snapshot(worker(cluster, db), Query(AVAILABLE, t_late, t_late), c1)            # a later T, but AVAILABLE
    jobs.snapshot(worker(cluster, db), Query(KNOWN, now(cluster, db)), c2)              # a later T, not designated
    k2, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, t_late - timedelta(microseconds=1)), c1)
    w = worker(cluster, db)
    with w.cursor() as cur:
        assert selection.known_recorded(cur, now(cluster, db))[0] == k2          # the latest T among KNOWN under c1
    assert k1 < k2
    t_b = now(cluster, db)
    k3, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, t_b), c1)               # the latest T, written first
    jobs.snapshot(worker(cluster, db), Query(KNOWN, t_late), c1)                    # earlier T, written later
    with worker(cluster, db).cursor() as cur:
        assert selection.known_recorded(cur, now(cluster, db))[0] == k3          # T first, then recorded_at
    k4, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, t_b), c1)               # the same T, written later
    with worker(cluster, db).cursor() as cur:
        assert selection.known_recorded(cur, now(cluster, db))[0] == k4          # same T: the latest written


@pg
def test_p6_preflight_checks_actual_privileges(cluster):                                          # M1
    for change, expect in (("revoke insert on mf_snapshots from cse_worker", "lacks INSERT on mf_snapshots"),
                           ("grant update on mf_snapshot_rows to cse_worker", "has UPDATE on mf_snapshot_rows"),
                           ("grant insert on mf_history_bars to cse_worker", "has INSERT on mf_history_bars"),
                           ("grant execute on function mf_calendar_guard() to cse_worker",
                            "EXECUTE on the guard function mf_calendar_guard()")):
        db = fs.fresh_db(cluster)
        assert preflight.problems(worker(cluster, db)) == []
        sql(cluster, db, change)
        assert any(expect in p for p in preflight.problems(worker(cluster, db))), change


@pg
def test_p7_a_range_declared_after_known_t_is_not_admitted(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    t = now(cluster, db)
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, import_id) "
                    "select 'import_calendar_complete', %s, %s, 'later', min(import_id) from mf_history_imports",
                    (DAYS[0], DAYS[-1]))
    o.commit()
    w = worker(cluster, db)
    assert store.load(w, Query(KNOWN, t))[2] == []
    assert len(store.load(worker(cluster, db), Query(KNOWN, now(cluster, db)))[2]) == 1
    imported = store.load(worker(cluster, db), Query(KNOWN, now(cluster, db)))[0]
    assert all(rec <= t for lo, hi, rec in imported["S0.N0000"].complete_recorded)


def _owner(cluster, db, statements):
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        out = None
        for text, args in statements:
            cur.execute(text, args)
            out = cur.fetchone() if cur.description else out
    o.commit()
    o.close()
    return out


def _batch(cluster, db, label, rows, quarantine=(), ranges=(), calendar=None):
    stmts = [("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, price_basis, "
              "survivorship, no_trade_convention, values_as_of) values (%s, repeat('c', 64), 't', 't', 'raw', "
              "'complete', 'row_absent', '2026-10-19') returning import_id", (label,))]
    imp = _owner(cluster, db, stmts)[0]
    more = [("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, source_sheet, "
             "source_row) values (%s,%s,%s,%s,1000,200000,'s',%s)", (imp, sym, d, c, i)) for i, (sym, d, c) in
            enumerate(rows)]
    more += [("insert into mf_history_quarantine (import_id, symbol, trade_date, reason) values (%s,%s,%s,'t')",
              (imp, sym, d)) for sym, d in quarantine]
    more += [("insert into mf_history_security_complete values (%s,%s,%s,%s)", (imp, sym, lo, hi))
             for sym, lo, hi in ranges]
    if calendar:
        more.append(("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, import_id) "
                     "values ('import_calendar_complete',%s,%s,'t',%s)", (calendar[0], calendar[1], imp)))
    _owner(cluster, db, more)
    return imp


def _statement(cluster, db, sid, sym, kind, ex, terms, avail_day, supersedes=None):
    _owner(cluster, db, [("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, "
                          "terms_json, available_at, source_reference, supersedes) values (%s,%s,%s,%s,%s,%s,'t',%s)",
                          (sid, sym, kind, ex, terms, hb.available_at(avail_day), supersedes))])


def _key(lab):
    return (lab.state, lab.ret, lab.volatility, lab.volatility_state, lab.target_session)


def _contract(cluster, db, build, ref, cfg, h):
    """Rebuild every label at T = its own resolved_at (same mode; H as the build's): it must come out the same, and
    the reference snapshot must be admissible there. Returns (labels checked, distinct resolved_at)."""
    w = worker(cluster, db)
    with w.cursor() as cur:
        rows = targets_store.reference_rows(cur, ref)
        cur.execute("select mode, cutoff, knowledge_horizon, recorded_at from mf_snapshots where snapshot_id = %s",
                    (ref,))
        head = cur.fetchone()
    w.rollback()
    snap, imp, _e, st, cal = jobs.build_all(worker(cluster, db), build, cfg, keep_series=True)
    labs, _ = targets_store.labels(snap, imp, st, cal, build, {ref: rows}, cfg.target_policy,
                                   {ref: targets_store.admissible_reference(build, *head)[0]})
    full = {(lab.symbol, lab.horizon): lab for _s, lab in labs}
    stored = {(r[0], r[1]): r[2] for r in sql(cluster, db, "select symbol, horizon, resolved_at from mf_target_rows "
                                                           "where reference_snapshot_id = %s", (ref,))}
    for key, lab in full.items():                            # the stored path gives the same resolved_at
        assert stored.get(key) == lab.resolved_at, key
    checked = 0
    times = sorted({lab.resolved_at for lab in full.values() if lab.resolved_at is not None})
    for r_at in times:
        q = Query(build.mode, r_at) if build.mode == KNOWN else Query(build.mode, r_at, h)
        inst, why = targets_store.admissible_reference(q, *head)
        assert why is None, (r_at, why)
        s2, i2, _e2, st2, cal2 = jobs.build_all(worker(cluster, db), q, cfg, keep_series=True)
        again, _skipped = targets_store.labels(s2, i2, st2, cal2, q, {ref: rows}, cfg.target_policy, {ref: inst})
        again = {(lab.symbol, lab.horizon): lab for _s, lab in again}
        for key, lab in full.items():
            if lab.resolved_at == r_at:
                assert key in again and _key(again[key]) == _key(lab), (key, r_at, lab.state, again.get(key))
                checked += 1
    return checked, len(times)


@pg
def test_p8_every_stored_label_is_the_same_at_its_own_resolved_at(cluster):                     # N-B1, M-a
    """The contract of resolved_at, checked as the re-audit did: rebuilt at T = its resolved_at, every stored label
    comes out the same. Several batches recorded in turn: a late batch that only quarantines a day and whose single
    row is dated at the end, a correction, a range batch closing a day, a declared closure, a split, and a statement
    withdrawn after it was available."""
    db = fs.fresh_db(cluster)
    d = DAYS
    base = [(f"S{k}.N0000", x, (50 if i >= 30 else 100) if k == 0 else 100 + k + (i % 3))
            for k in range(4) for i, x in enumerate(d) if i not in (15, 17)]
    _batch(cluster, db, "A", base, ranges=[(f"S{k}.N0000", d[0], d[-1]) for k in range(4)])
    _statement(cluster, db, "sp", "S0.N0000", "subdivision", d[30], '{"new_shares":2,"old_shares":1}', d[20])
    _owner(cluster, db, [("insert into mf_calendar_observations (kind, date_from, date_to, available_at, "
                          "source_reference) values ('declared_closed', %s, %s, %s, 'bulletin')",
                          (d[15], d[15], hb.available_at(d[14])))])
    # day 17 closed by a range batch whose only row is dated later: the batch (and its range) enter on day 25
    _batch(cluster, db, "R", [("S3.N0000", d[25], 100 + 3 + (25 % 3))], calendar=(d[16], d[18]))
    _statement(cluster, db, "bad", "S2.N0000", "subdivision", d[24], '{"new_shares":2,"old_shares":1}', d[11])
    _statement(cluster, db, "wd", "S2.N0000", "withdrawn", None, "{}", d[26], supersedes="bad")
    _batch(cluster, db, "Q", [("S3.N0000", d[39], 100 + 3 + (39 % 3))], quarantine=[("S1.N0000", d[12])])
    _batch(cluster, db, "C", [("S2.N0000", d[33], 150)])                          # a correction that disagrees
    cid = registered(cluster, db)
    cfg = configuration.parse(sql(cluster, db, "select configuration_json from mf_configurations")[0][0])
    h_ref = now(cluster, db)
    ref, _ = jobs.snapshot(worker(cluster, db), Query(AVAILABLE, hb.available_at(d[10]), h_ref), cid)
    h = now(cluster, db)
    build = Query(AVAILABLE, hb.available_at(d[-1]), h)
    targets_store.build(worker(cluster, db), build, cid, [ref])
    stored = sql(cluster, db, "select symbol, horizon, state, resolved_at from mf_target_rows")
    assert "resolved" in {r[2] for r in stored}             # days 15 and 17 are closed by evidence, not gaps
    checked, distinct = _contract(cluster, db, build, ref, cfg, h)
    assert checked >= 12
    assert distinct >= 3                                    # a sensitive test: the labels resolve at different times


@pg
@pytest.mark.parametrize("last", ["quarantine", "range", "statements", "finality"])
def test_p9_the_contract_in_known_with_evidence_recorded_in_turn(cluster, last):                # N-B1, M-a
    """KNOWN: instants are recorded times, so the evidence recorded last binds the labels that read it. Calendar
    evidence enters every label whole, so it comes first (the range, then the declared closure, which binds the
    labels reading day 15); then, in four variants, one of these is recorded after the others: a late quarantine,
    a later batch whose range makes a no-trade day (B1), a withdrawn split, a session whose only evidence is a late
    batch (finality). A second reference, written last, checks the reference's own instant."""
    db = fs.fresh_db(cluster)
    d = DAYS
    px = lambda k, i: (50 if i >= 30 else 100) if k == 0 else 100 + k + (i % 3)  # noqa: E731
    # S4 has no evidence but its rows: its labels are bound by the latest calendar item (the declared closure)
    _batch(cluster, db, "A1", [(f"S{k}.N0000", d[i], px(k, i)) for k in range(5) for i in range(11)])
    t_ref = now(cluster, db)
    cid = registered(cluster, db)
    ref, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, t_ref), cid)       # D* = day 10, written early
    skip = {15, 17, 22}
    # S3 trades on days 11 and 12, then only on day 39: without a range it leaves the universe (terminated);
    # with batch X's range it has no-trade days and comes back (B1's case)
    _batch(cluster, db, "A2", [(f"S{k}.N0000", d[i], px(k, i)) for k in range(5) for i in range(11, 40)
                               if i not in skip and not (k == 3 and i > 12)],
           ranges=[("S2.N0000", d[11], d[39])])
    _statement(cluster, db, "bad", "S2.N0000", "subdivision", d[24], '{"new_shares":2,"old_shares":1}', d[11])
    _batch(cluster, db, "R", [("S0.N0000", d[18], px(0, 18))], calendar=(d[16], d[18]))      # closes day 17
    _owner(cluster, db, [("insert into mf_calendar_observations (kind, date_from, date_to, available_at, "
                          "source_reference) values ('declared_closed', %s, %s, %s, 'bulletin')",
                          (d[15], d[15], hb.available_at(d[14])))])
    steps = {
        "quarantine": lambda: _batch(cluster, db, "Q", [("S3.N0000", d[39], px(3, 39))],
                                     quarantine=[("S1.N0000", d[12])]),
        # X's own row is another security's: S3's closing row (day 39) is Q's, so only X's range carries X
        "range": lambda: _batch(cluster, db, "X", [("S0.N0000", d[39], px(0, 39))],
                                ranges=[("S3.N0000", d[11], d[39])]),
        "statements": lambda: (_statement(cluster, db, "sp", "S0.N0000", "subdivision", d[30],
                                          '{"new_shares":2,"old_shares":1}', d[5]),
                               _statement(cluster, db, "wd", "S2.N0000", "withdrawn", None, "{}", d[12],
                                          supersedes="bad")),
        "finality": lambda: _batch(cluster, db, "L", [(f"S{k}.N0000", d[22], px(k, 22)) for k in (0, 1)]),
    }
    for name in [n for n in steps if n != last] + [last]:
        steps[name]()
    cfg = configuration.parse(sql(cluster, db, "select configuration_json from mf_configurations")[0][0])
    late, _ = jobs.snapshot(worker(cluster, db), Query(KNOWN, t_ref), cid)      # the same D*, written last
    assert {r[0] for r in sql(cluster, db, "select d_star from mf_snapshots")} == {d[10]}
    build = Query(KNOWN, now(cluster, db))
    targets_store.build(worker(cluster, db), build, cid, [ref, late])
    rec = sql(cluster, db, "select recorded_at from mf_snapshots where snapshot_id = %s", (late,))[0][0]
    assert all(r[0] is None or r[0] >= rec for r in sql(cluster, db, "select resolved_at from mf_target_rows "
                                                                     "where reference_snapshot_id = %s", (late,)))
    checked, distinct = _contract(cluster, db, build, ref, cfg, None)
    assert checked >= 12 and distinct >= 3                  # a sensitive test: the labels resolve at different times
