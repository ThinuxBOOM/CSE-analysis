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
    assert inst[d20] == max(y, hb.available_at(d20))


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


CHANGES = [("mf_snapshots", "d_star = d_star - 1"), ("mf_snapshots", "code_version = 'x'"),
           ("mf_snapshots", "imported_inputs = false"), ("mf_snapshots", "input_digest = repeat('0', 64)"),
           ("mf_snapshot_move_flags", "status = 'confirmed_no_action'"),
           ("mf_snapshot_move_flags", "close_to = close_to + 1"),
           ("mf_target_builds", "cutoff = cutoff - interval '1 day'"), ("mf_target_builds", "code_version = 'x'")]


@pg
@pytest.mark.parametrize("table,change", CHANGES)
def test_p3_verify_catches_tampering_of_a_live_snapshot_and_of_a_target_build(cluster, table, change):   # M2
    db = fs.fresh_db(cluster)
    seed(cluster, db, split=False)                          # S0's fall is an unexplained move: one move flag
    cid = registered(cluster, db)
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
