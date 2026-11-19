"""
MF-7c third part: the MF commands end to end in a throwaway cluster: preflight, calendar-sync and its drift,
configurations and designations (ML-15), snapshots and KNOWN_RECORDED (ML-23), verify and its tamper checks (ML-13),
explain's provenance, target builds, and the command line's role rules. Skipped unless P1_PG_BINDIR is set. No
network, no CSE.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

import f64_support as fs
from worker.market_feature_store import (calendar_sync, cli, configuration, explain, jobs, preflight, selection,
                                         targets_store, verify)
from worker.market_feature_store import history_bars as hb
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, KNOWN, Query

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
UTC = timezone.utc
CFG = configuration.Configuration(policy=universe.EligibilityPolicy(min_history_sessions=5, min_median_turnover_lkr=1),
                                  feature_config=FeatureConfig(min_constituents=3))


def weekdays(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = weekdays(40)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("pg"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(os.path.join(fs.REPO, "supabase", "migrations_pending")),
              log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def sql(ec, db, text, args=(), user="postgres"):
    c = fs.conn(ec, db, user=user)
    with c.cursor() as cur:
        cur.execute(text, args)
        out = cur.fetchall() if cur.description else None
    c.commit()
    c.close()
    return out


def now(ec, db):
    return sql(ec, db, "select clock_timestamp()")[0][0]


def seed(ec, db, split=True):
    """An import of 4 securities over DAYS; S0 halves at day 30 (a split when `split`)."""
    o = fs.conn(ec, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                    "price_basis, survivorship, no_trade_convention, values_as_of) values ('2024 Data.xls', "
                    "repeat('a', 64), 't', 't', 'raw', 'complete', 'row_absent', '2026-10-19') returning import_id")
        imp = cur.fetchone()[0]
        for k in range(4):
            for i, d in enumerate(DAYS):
                close = (50 if i >= 30 else 100) if k == 0 else 100 + k + (i % 3)
                cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, "
                            "source_sheet, source_row) values (%s,%s,%s,%s,1000,200000,'s',%s)",
                            (imp, f"S{k}.N0000", d, close, k * 100 + i))
            cur.execute("insert into mf_history_security_complete values (%s,%s,%s,%s)",
                        (imp, f"S{k}.N0000", DAYS[0], DAYS[-1]))
        if split:
            cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, "
                        "terms_json, available_at, source_reference) values ('sp','S0.N0000','subdivision',%s,"
                        "'{\"new_shares\":2,\"old_shares\":1}',%s,'t')", (DAYS[30], hb.available_at(DAYS[20])))
    o.commit()
    o.close()


def worker(ec, db):
    return fs.conn(ec, db, user="cse_worker")


def registered(ec, db):
    state, cid = configuration.register(worker(ec, db), CFG)
    return cid


def test_o1_preflight_and_the_owner_path(cluster):
    db = fs.fresh_db(cluster)
    assert preflight.problems(worker(cluster, db)) == []
    assert any(p.startswith("connected as") for p in preflight.problems(fs.conn(cluster, db, user="cse_migrator")))
    cid = registered(cluster, db)
    with pytest.raises(cli.OwnerPathRequired):
        cli.designate(worker(cluster, db), cid, "the canonical configuration")
    before = now(cluster, db)
    cli.designate(fs.conn(cluster, db, user="cse_migrator"), cid, "the canonical configuration", "owner")
    w = worker(cluster, db)
    with w.cursor() as cur:
        assert selection.designated(cur, before) is None                       # ML-15: not in force before
        assert selection.designated(cur, now(cluster, db)) == cid
    sql(cluster, db, "alter table mf_snapshots disable trigger trg_mf_snapshots_append_only")
    assert any("trg_mf_snapshots_append_only" in p for p in preflight.problems(worker(cluster, db)))


def test_o2_configuration_round_trip_and_refusal(cluster):
    db = fs.fresh_db(cluster)
    assert configuration.register(worker(cluster, db), CFG)[0] == "registered"
    assert configuration.register(worker(cluster, db), CFG)[0] == "present"
    body = CFG.body()
    body["adjustment_policy"] = "adjustment_policy.0"
    with pytest.raises(configuration.ConfigurationError):
        configuration.parse(json.dumps(body, sort_keys=True, separators=(",", ":")))
    assert configuration.parse(CFG.json()) == CFG


def test_o3_calendar_sync_and_drift(cluster):
    db = fs.fresh_db(cluster)
    sql(cluster, db, "insert into trading_calendar (trade_date, market_status, established_by, established_at) "
                     "values ('2024-01-25', 'closed', 'bulletin', '2024-01-01T00:00:00Z')")
    first = calendar_sync.sync(worker(cluster, db))
    assert (first["copied"], first["already"]) == (1, 0)
    assert calendar_sync.sync(worker(cluster, db))["already"] == 1
    row = sql(cluster, db, "select kind, date_from, available_at, recorded_at from mf_calendar_observations")[0]
    assert row[0] == "declared_closed" and row[1] == date(2024, 1, 25)
    assert row[2] >= row[3] - timedelta(seconds=1)       # available from the sync, not from the mutable established_at
    assert verify.verify(worker(cluster, db))["calendar_drift"] == []
    sql(cluster, db, "update trading_calendar set market_status = 'open', established_by = 'live_capture' "
                     "where trade_date = '2024-01-25'")
    rep = verify.verify(worker(cluster, db))
    assert not rep["ok"] and len(rep["calendar_drift"]) == 1


def _snapshot(ec, db, query, cid):
    return jobs.snapshot(worker(ec, db), query, cid)


def test_o4_verify_replays_settled_snapshots_and_detects_tampering(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    h = now(cluster, db)
    sid, snap = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[-1]), h), cid)
    rep = verify.verify(worker(cluster, db), margin=timedelta(0))
    assert rep["ok"] and rep["snapshots"][0]["replayed"], rep
    assert not verify.verify(worker(cluster, db))["snapshots"][0]["replayed"]    # within the margin: rows only
    # a stored row altered (triggers dropped by a superuser, as R-F4's tamper tests do)
    sql(cluster, db, "alter table mf_snapshot_rows disable trigger trg_mf_snapshot_rows_append_only")
    sql(cluster, db, "update mf_snapshot_rows set px_ref = px_ref + 1 where snapshot_id = %s and symbol = 'S1.N0000'",
        (sid,))
    probs = verify.verify(worker(cluster, db), margin=timedelta(0))["snapshots"][0]["problems"]
    assert any("S1.N0000: row_hash" in p for p in probs)


def test_o5_a_replay_detects_evidence_altered_after_a_settled_time(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    sid, _ = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db)), cid)
    sql(cluster, db, "alter table mf_history_bars disable trigger trg_mf_history_bars_append_only")
    sql(cluster, db, "update mf_history_bars set close = 77 where symbol = 'S2.N0000' and trade_date = %s",
        (DAYS[-1],))
    probs = verify.verify(worker(cluster, db), margin=timedelta(0))["snapshots"][0]["problems"]
    assert any("S2.N0000: replay row differs" in p for p in probs), probs


def test_o6_known_recorded_selection(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    cli.designate(fs.conn(cluster, db, user="cse_migrator"), cid, "the canonical configuration")
    t1 = now(cluster, db)
    k1, _ = _snapshot(cluster, db, Query(KNOWN, t1), cid)
    _a, _ = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db)), cid)
    at = now(cluster, db)
    k2, _ = _snapshot(cluster, db, Query(KNOWN, now(cluster, db)), cid)                   # written after `at`
    w = worker(cluster, db)
    with w.cursor() as cur:
        assert selection.known_recorded(cur, at)[0] == k1             # never the AVAILABLE one, never k2 (ML-23)
        assert selection.known_recorded(cur, now(cluster, db))[0] == k2
        assert selection.known_recorded(cur, t1 - timedelta(seconds=1)) is None


def test_o7_explain_rederives_provenance(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    sid, _ = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db)), cid)
    e = explain.explain(worker(cluster, db), sid, "S0.N0000")
    ids = [r[0] for r in sql(cluster, db, "select id from mf_history_bars where symbol = 'S0.N0000' order by id")]
    assert e["provenance_ok"] and e["dstar_observation_ids"] == [ids[-1]] and e["window_observation_ids"] == ids
    assert [f["status"] for f in e["factors"]] == ["applied"]
    with pytest.raises(LookupError):
        explain.explain(worker(cluster, db), sid, "NOPE.N0000")


def test_o8_target_build_labels_and_verify(cluster):
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    cid = registered(cluster, db)
    h = now(cluster, db)
    ref, _ = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[25]), h), cid)    # D = day 25
    tb = Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db))       # H after the reference was written
    rep = targets_store.build(worker(cluster, db), tb, cid, [ref])
    assert rep["labels"] == 16 and rep["not_labelled"] == {}
    rows = sql(cluster, db, "select symbol, horizon, state, values_json, resolved_at from mf_target_rows "
                            "order by symbol, horizon")
    s0 = {r[1]: r for r in rows if r[0] == "S0.N0000"}
    assert {r[2] for r in rows if r[1] <= 10} == {"resolved"} and {r[2] for r in rows if r[1] == 21} == {"pending"}
    assert s0[10][2] == "resolved" and json.loads(s0[10][3])["ret"] in ("0E-12", "0.000000000000")  # split adjusted
    assert all(r[4] is None or r[4] <= hb.available_at(DAYS[-1]) for r in rows)        # never after the build's T
    assert all(r[4] is None or r[4] >= hb.available_at(DAYS[25 + r[1]]) for r in rows if r[2] == "resolved")
    assert verify.verify(worker(cluster, db))["target_builds"][0]["problems"] == []
    sql(cluster, db, "alter table mf_target_rows disable trigger trg_mf_target_rows_append_only")
    sql(cluster, db, "update mf_target_rows set state = 'unresolvable_quality' where symbol = 'S1.N0000' and horizon = 1")
    assert verify.verify(worker(cluster, db))["target_builds"][0]["problems"]


def test_o8b_review_m1_a_label_resolves_at_its_own_window_not_at_the_builds_t(cluster):
    """Branch review M1: on imported history, calendar evidence and corporate-action statements dated after a label's
    window no longer push its resolved_at up to the build's T, so a historical build has training labels."""
    db = fs.fresh_db(cluster)
    seed(cluster, db)
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("select import_id from mf_history_imports")
        imp = cur.fetchone()[0]
        cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, import_id) "
                    "values ('import_calendar_complete', %s, %s, 'the batch is calendar complete', %s)",
                    (DAYS[0], DAYS[-1], imp))
        cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                    "available_at, source_reference) values ('late', 'S1.N0000', 'bonus', %s, "
                    "'{\"held\":10,\"new\":1}', %s, 't')", (DAYS[38], hb.available_at(DAYS[38])))
        weekend = DAYS[35] + timedelta(days=(5 - DAYS[35].weekday()) % 7)    # a Saturday after the window
        cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, available_at, source_reference) "
                    "values ('declared_closed', %s, %s, %s, 'a late notice')", (weekend, weekend,
                                                                                 hb.available_at(DAYS[38])))
    o.commit()
    cid = registered(cluster, db)
    ref, _ = _snapshot(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[25]), now(cluster, db)), cid)
    tb = Query(AVAILABLE, hb.available_at(DAYS[-1]), now(cluster, db))
    rep = targets_store.build(worker(cluster, db), tb, cid, [ref])
    rows = {(r[0], r[1]): r for r in sql(cluster, db, "select symbol, horizon, state, resolved_at from "
                                                       "mf_target_rows where build_id = %s", (rep["build_id"],))}
    one = rows[("S1.N0000", 1)]
    assert one[2] == "resolved" and one[3] == hb.available_at(DAYS[26])   # its own F, not DAYS[-1] (the build's T)
    five = rows[("S1.N0000", 5)]
    assert five[3] == hb.available_at(DAYS[30])


def test_o9_the_command_line(cluster, capsys):
    db = fs.fresh_db(cluster)
    seed(cluster, db)

    def run(argv, user="cse_worker"):
        return cli.run(cli.parser().parse_args(argv), lambda: fs.conn(cluster, db, user=user))
    assert run(["preflight"]) == 0
    assert run(["register-configuration", "--min-history-sessions", "5", "--min-constituents", "3",
                "--min-median-turnover", "1"]) == 0
    cid = json.loads(capsys.readouterr().out.split("\n}\n")[-2] + "\n}")["configuration_id"]
    assert run(["designate", "--configuration", cid, "--note", "the canonical configuration"]) == 5   # worker: refused
    assert run(["designate", "--configuration", cid, "--note", "the canonical configuration"], user="cse_migrator") == 0
    capsys.readouterr()
    assert run(["snapshot", "--mode", "AVAILABLE", "--cutoff", hb.available_at(DAYS[-1]).isoformat(),
                "--designated"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 4 and out["label"] == "reconstructed"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert run(["snapshot", "--mode", "KNOWN", "--cutoff", future, "--designated"]) == 5   # not point-in-time
    assert run(["verify"]) == 0 and run(["status"]) == 0
    assert run(["snapshot", "--mode", "AVAILABLE", "--cutoff", hb.available_at(DAYS[-1]).isoformat(),
                "--designated"], user="cse_migrator") == 5                                  # preflight refuses
