"""
MF-7c: the snapshot loader and writer (worker/market_feature_store/store.py) end to end in a throwaway cluster:
a synthetic import through the owner path, load at (T, AVAILABLE), build, write as the worker, read back. Admission:
a row after T is not loaded. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import json
import os
from datetime import date, timedelta
from fractions import Fraction as F

import pytest

import f64_support as fs
from worker.market_feature_store import history_bars as hb
from worker.market_feature_store import snapshot as sn
from worker.market_feature_store import store
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, Query

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")


def weekdays(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_t1_load_build_write_and_read_back(tmp_path):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path / "pg")
    try:
        c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
        mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(os.path.join(fs.REPO, "supabase", "migrations_pending")),
                  log=lambda m: None)
        c.close()
        db = fs.fresh_db(ec)
        days = weekdays(30)
        o = fs.conn(ec, db, user="cse_migrator")
        with o.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                        "price_basis, survivorship, no_trade_convention, values_as_of) values ('2024 Data.xls', "
                        "repeat('a', 64), 'test', 'test', 'raw', 'complete', 'row_absent', '2026-10-19') "
                        "returning import_id")
            imp = cur.fetchone()[0]
            for k in range(4):
                for i, d in enumerate(days):
                    close = 50 if (k == 0 and i >= 20) else 100 + k + (i % 3)
                    cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, "
                                "source_sheet, source_row) values (%s,%s,%s,%s,1000,200000,'s',%s)",
                                (imp, f"S{k}.N0000", d, close, k * 100 + i))
                cur.execute("insert into mf_history_security_complete values (%s,%s,%s,%s)",
                            (imp, f"S{k}.N0000", days[0], days[-1]))
            cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                        "available_at, source_reference) values ('sp','S0.N0000','subdivision',%s,"
                        "'{\"new_shares\":2,\"old_shares\":1}',%s,'TradeIQ split')", (days[20], hb.available_at(days[5])))
        o.commit()
        t = hb.available_at(days[24])                                     # the last 5 sessions are after T
        q = Query(AVAILABLE, t, t + timedelta(days=36500))                  # H after the import was recorded
        r = fs.conn(ec, db, user="cse_worker")
        imported, statements, cal_obs, evidence = store.load(r, q)
        assert max(max(i.rows) for i in imported.values()) == days[24]    # nothing after T is loaded
        assert [s.statement_id for s in statements] == ["sp"]
        policy = universe.EligibilityPolicy(min_history_sessions=10, min_median_turnover_lkr=1)
        snap = sn.build(imported, statements, q, calendar_observations=cal_obs, policy=policy,
                        config=FeatureConfig(min_constituents=3))
        assert snap.d_star == days[24]
        w = fs.conn(ec, db, user="cse_worker")
        cid_json = json.dumps({"eligibility_policy_status": "provisional"}, sort_keys=True, separators=(",", ":"))
        import hashlib
        cid = hashlib.sha256(cid_json.encode()).hexdigest()
        with w.cursor() as cur:
            cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values "
                        "(%s,%s,'provisional')", (cid, cid_json))
        w.commit()
        sid = store.write(w, snap, q, configuration_id=cid, code_version="mf.1", evidence=evidence, imported=imported)
        check = fs.conn(ec, db, user="postgres")
        with check.cursor() as cur:
            cur.execute("select mode, result_label, d_star, row_count from mf_snapshots where snapshot_id = %s", (sid,))
            assert cur.fetchone() == ("AVAILABLE", "reconstructed", days[24], 4)
            cur.execute("select features_json from mf_snapshot_rows where snapshot_id = %s and symbol = 'S0.N0000'",
                        (sid,))
            feats = json.loads(cur.fetchone()[0])
            assert feats["ret_1"]["state"] in ("fresh", "stale") and "ret_21" in feats
            cur.execute("select kind, numerator, denominator, status from mf_snapshot_factors where snapshot_id = %s",
                        (sid,))
            assert cur.fetchall() == [("subdivision", 1, 2, "applied")]
            cur.execute("select count(*) from mf_snapshot_move_flags where snapshot_id = %s", (sid,))
            assert cur.fetchone()[0] == 0                                  # the split explains S0's fall
    finally:
        ec.stop()
