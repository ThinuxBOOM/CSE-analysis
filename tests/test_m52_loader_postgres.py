"""
Phase 5 M5-2 part 2: the loader from stored rows (worker/ml_dataset/loader.py) against migrations 0001 - 0020 in a
throwaway cluster. The world is DE-4's test world (real MF snapshot rows, link decisions and a stored DE run; F8 stood
in), plus a target build. The dataset built from the database carries the run's F7 rows and engine scores, the pinned
build's labels only, survivorship from the import headers, and is the same twice. Skipped unless P1_PG_BINDIR is set.
"""
import json
import os
from datetime import date, datetime, timezone

import pytest

import f64_support as fs
import test_de4_assemble_unit as w
import test_de4_jobs_postgres as dj
from worker.feature_engine_store import codec as dc
from worker.feature_engine_store import jobs
from worker.ml_dataset import dataset as ds
from worker.ml_dataset import loader

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
cluster = dj.cluster


def mf_config(cluster, db):
    c = fs.conn(cluster, db, user="postgres")
    with c.cursor() as cur:
        cur.execute("select configuration_id from mf_snapshots limit 1")
        cid = cur.fetchone()[0]
    c.rollback()
    c.close()
    return cid


def target_build(cluster, db, sid, build_states=None):
    """Two target builds labelling the snapshot (the second one is never pinned)."""
    su = fs.conn(cluster, db, user="postgres")
    ids = []
    with su.cursor() as cur:
        cur.execute("select configuration_id from mf_snapshots where snapshot_id = %s", (sid,))
        cid = cur.fetchone()[0]
        cur.execute("select symbol from mf_snapshot_rows where snapshot_id = %s", (sid,))
        syms = [r[0] for r in cur.fetchall()]
        for n in range(2):
            cur.execute("insert into mf_target_builds (mode, cutoff, configuration_id, target_definition_version, "
                        "code_version, build_hash) values ('KNOWN', '2024-08-31T00:00:00+00:00', %s, 'targets.1', "
                        "'mf.1', repeat('9', 64)) returning build_id", (cid,))
            b = cur.fetchone()[0]
            ids.append(b)
            for k, sym in enumerate(sorted(syms)):
                for h in (1, 5, 10, 21):
                    state = "unresolvable_missing_price" if sym == "S02.N0000" else "resolved"
                    cur.execute("insert into mf_target_rows (build_id, reference_snapshot_id, symbol, horizon, state, "
                                "values_json, resolved_at, row_hash) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                                (b, sid, sym, h, state, json.dumps({"ret": str(0.01 * (k + n))}),
                                 datetime(2024, 7, 1 + h, tzinfo=timezone.utc), f"{b:02x}{k:02x}{h:02x}" + "0" * 58))
    su.commit()
    su.close()
    return ids


def manifest(cid, build, **over):
    m = dict(mf_configuration_id=cid, f7_configuration_id=dc.f7_configuration()[0],
             de_configuration_id=dc.de_configuration()[0], date_from=date(2024, 1, 1), date_to=date(2024, 12, 31),
             eras=(ds.Era(date(2024, 1, 1), date(2024, 12, 31), "KNOWN"),), target_build_id=build,
             label_definition_version="targets.1")
    m.update(over)
    return ds.Manifest(**m)


def built(cluster, db, monkeypatch):
    sid, ids = dj.seed(cluster, db)
    dj.stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    jobs.run(c, sid)
    su = fs.conn(cluster, db, user="postgres")       # the session is T's own day, so the cutoff rule selects it
    with su.cursor() as cur:
        cur.execute("alter table mf_snapshots disable trigger user")
        cur.execute("update mf_snapshots set d_star = '2024-06-30' where snapshot_id = %s", (sid,))
        cur.execute("alter table mf_snapshots enable trigger user")
    su.commit()
    su.close()
    builds = target_build(cluster, db, sid)
    return c, sid, builds, mf_config(cluster, db)


def test_l1_the_stored_world_becomes_a_dataset(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    m = manifest(cid, builds[0])
    snaps, labels, counts = loader.load(c, m)
    assert counts == {"snapshots_without_de_run": 0} and len(snaps) == 1
    assert {x.build_id for x in labels} == {builds[0]}                        # the pinned build only
    d = ds.build(m, snaps, labels)
    assert {r.symbol for r in d.rows} == {f"S{k:02d}.N0000" for k in range(12)} | {"ZZZ.N0000"}
    assert d.report["excluded_by_eligibility"] == {"no_data": 1}               # S00.X0000
    r5 = next(r for r in d.rows if r.symbol == "S05.N0000")
    assert r5.features["f7.eligibility"] == ("eligible", "fresh", None)
    assert r5.features["f7.roe_end"][0] is not None and "f7.eps_latest_fy" in r5.features
    assert r5.features["de.fund_leverage"][1] == "scored" and r5.meta["industry_group"] == "general"
    assert len(r5.sources) == 1 + 2 + 15                                         # MF row, two F7 rows, 15 outputs
    z = next(r for r in d.rows if r.symbol == "ZZZ.N0000")
    assert z.features["f7.eligibility"] == (None, "missing", "issuer_link_absent") and z.meta["industry_group"] is None
    assert d.labels[("S02.N0000", date(2024, 6, 30), 5)].state == "unresolvable_missing_price"
    assert (d.label_class, d.survivorship) == ("point_in_time", "complete")
    again = ds.build(m, *loader.load(c, m)[:2])
    assert again.version == d.version


def test_l2_configurations_and_imports(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    other = manifest(cid, builds[0], de_configuration_id="0" * 64)            # no run under this configuration
    snaps, _labels, counts = loader.load(c, other)
    assert counts["snapshots_without_de_run"] == 1
    assert all(ln.f7 is None and not ln.de for ln in snaps[0].rows.values())
    su = fs.conn(cluster, db, user="postgres")                               # an imported, survivors-only batch
    with su.cursor() as cur:
        cur.execute("alter table mf_snapshots disable trigger user")
        cur.execute("update mf_snapshots set imported_inputs = true where snapshot_id = %s", (sid,))
        cur.execute("alter table mf_snapshots enable trigger user")
        cur.execute("set role cse_owner")
        for surv, day in (("survivors_only", "2024-03-01"), ("unknown", "2020-01-02")):
            cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                        "price_basis, survivorship, no_trade_convention, values_as_of, recorded_at) values ('w', "
                        "repeat('a', 64), 'o', 't', 'raw', %s, 'row_absent', '2024-01-01', '2024-01-01T00:00:00Z') "
                        "returning import_id", (surv,))
            imp = cur.fetchone()[0]
            cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, source_sheet, source_row) "
                        "values (%s, 'S01.N0000', %s, 10, 'Sheet1', 7)", (imp, day))
    su.commit()
    su.close()
    snaps, labels, _ = loader.load(c, manifest(cid, builds[0]))
    assert snaps[0].survivorship == "survivors_only"                          # the 2020 batch is out of reach
    assert ds.build(manifest(cid, builds[0]), snaps, labels).survivorship == "survivors_only"


def test_l3_a_snapshot_after_its_sessions_cutoff_rule_is_not_used(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    sid, ids = dj.seed(cluster, db)                  # T two days after D*: past the rule's instant on D*
    dj.stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    jobs.run(c, sid)
    builds = target_build(cluster, db, sid)
    m = manifest(mf_config(cluster, db), builds[0])
    d = ds.build(m, *loader.load(c, m)[:2])
    assert d.rows == () and d.report["snapshots_after_cutoff_rule"] == 1
    assert (d.label_class, d.survivorship, d.fundamental_coverage) == (None, None, None)
