"""
Phase 5 M5-2 part 2: the loader from stored rows (worker/ml_dataset/loader.py) against migrations 0001 - 0020 in a
throwaway cluster. The world is DE-4's test world (real MF snapshot rows, link decisions and a stored DE run; F8 stood
in), plus a target build. The dataset built from the database carries the run's F7 rows and engine scores, the pinned
build's labels only, survivorship from the import headers, and is the same twice. Skipped unless P1_PG_BINDIR is set.
"""
import json
from decimal import Decimal as D
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


def built(cluster, db, monkeypatch, mode="KNOWN"):
    sid, ids = dj.seed(cluster, db, mode=mode)
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
    snaps, labels, build = loader.load(c, m)
    assert len(snaps) == 1 and snaps[0].de_run and build.build_id == builds[0] and build.mode == "KNOWN"
    assert {x.build_id for x in labels} == {builds[0]}                        # the pinned build only
    d = ds.build(m, snaps, labels, build)
    assert {r.symbol for r in d.rows} == {f"S{k:02d}.N0000" for k in range(12)} | {"ZZZ.N0000"}
    assert d.report["excluded_by_eligibility"] == {"no_data": 1}               # S00.X0000
    r5 = next(r for r in d.rows if r.symbol == "S05.N0000")
    assert r5.features["f7.eligibility"] == ("eligible", "fresh", None)
    assert r5.features["f7.roe_end"][0] is not None and "f7.eps_latest_fy" in r5.features
    assert r5.features["de.fund_leverage"][1] == "scored" and r5.meta["industry_group"] == "general"
    # M5-4 part 2: full set and shares from the stored output; the shares add up to the score
    from worker.engines import registry
    with c.cursor() as cur:
        cur.execute("select engine_id, output_json from de_outputs where member = 'S05.N0000' and state = 'scored'")
        stored = {e: json.loads(j) for e, j in cur.fetchall()}
    c.rollback()
    assert stored
    for eid, js in stored.items():
        want = set(js["components_present"]) == {x.feature_id for x in registry.BY_ID[eid].components}
        assert r5.features[f"de.{eid}.full"][0] is want
        assert abs(sum(r5.features[f"de.{eid}.parts"][0].values()) - r5.features[f"de.{eid}"][0]) < D("1e-11")
    assert {r5.features[f"de.{e}.full"][0] for e in stored} == {True, False}      # both cases occur here
    assert len(r5.sources) == 1 + 2 + 15                                         # MF row, two F7 rows, 15 outputs
    # MLK-20 through real stored rows: F7's market value is missing (no market cap in MF, C-0079) and its yields
    # follow; each reaches the dataset as a null with the reason F7 stored, never as a value
    assert r5.features["f7.issuer_market_value"] == (None, "missing", "incomplete_lines")
    assert r5.features["f7.book_to_market"] == (None, "missing", "incomplete_lines")
    z = next(r for r in d.rows if r.symbol == "ZZZ.N0000")
    assert z.features["f7.eligibility"] == (None, "missing", "issuer_link_absent") and z.meta["industry_group"] is None
    assert snaps[0].rows["ZZZ.N0000"].link_state == "issuer_link_absent"     # read from DE's evidence
    assert d.labels[("S02.N0000", date(2024, 6, 30), 5)].state == "unresolvable_missing_price"
    assert (d.label_class, d.survivorship) == ("point_in_time", "complete")
    again = ds.build(m, *loader.load(c, m))
    assert again.version == d.version


def test_l2_configurations_and_imports(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    other = manifest(cid, builds[0], de_configuration_id="0" * 64)            # no run under this configuration
    snaps, labels, build = loader.load(c, other)
    assert not snaps[0].de_run and all(ln.f7 is None and not ln.de for ln in snaps[0].rows.values())
    assert ds.build(other, snaps, labels, build).report["snapshots_without_de_run"] == 1
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
    snaps, labels, build = loader.load(c, manifest(cid, builds[0]))
    assert snaps[0].survivorship == "survivors_only"                          # the 2020 batch is out of reach
    assert ds.build(manifest(cid, builds[0]), snaps, labels, build).survivorship == "survivors_only"


def test_l3_a_snapshot_after_its_sessions_cutoff_rule_is_not_used(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    sid, ids = dj.seed(cluster, db)                  # T two days after D*: past the rule's instant on D*
    dj.stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    jobs.run(c, sid)
    builds = target_build(cluster, db, sid)
    m = manifest(mf_config(cluster, db), builds[0])
    d = ds.build(m, *loader.load(c, m))
    assert d.rows == () and d.report["snapshots_after_cutoff_rule"] == 1
    assert (d.label_class, d.survivorship, d.fundamental_coverage) == (None, None, None)


# ------------------------------------------------------------------------------------------------ first M5-2 audit


def sql(cluster, db, statements, role=None, disable=()):
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        for t in disable:
            cur.execute(f"alter table {t} disable trigger user")
        if role:
            cur.execute(f"set role {role}")
        out = []
        for q, args in statements:
            cur.execute(q, args)
            out.append(cur.fetchone() if cur.description else None)
        if role:
            cur.execute("reset role")
        for t in disable:
            cur.execute(f"alter table {t} enable trigger user")
    su.commit()
    su.close()
    return out


def test_b1_a_build_of_another_mode_h_or_definition_is_refused(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    (b,) = sql(cluster, db, [("insert into mf_target_builds (mode, cutoff, knowledge_horizon, configuration_id, "
                             "target_definition_version, code_version, build_hash) values ('AVAILABLE', "
                             "'2024-08-31T00:00:00Z', '2026-01-01T00:00:00Z', %s, 'targets.9', 'mf.1', repeat('9', 64)) "
                             "returning build_id", (cid,))])
    m = manifest(cid, b[0])
    with pytest.raises(ds.Refused, match="label_definition"):
        ds.build(m, *loader.load(c, m))
    m2 = manifest(cid, b[0], label_definition_version="targets.9")
    with pytest.raises(ds.Refused, match="build_era_mismatch"):
        ds.build(m2, *loader.load(c, m2))
    with pytest.raises(ds.Refused, match="target_build"):
        loader.load(c, manifest(cid, 9999))


def test_l4_the_latest_run_and_its_own_rows(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    first = {r.symbol: r.sources for r in ds.build(manifest(cid, builds[0]), *loader.load(c, manifest(cid, builds[0]))).rows}
    jobs.run(c, sid)                                                           # a second run of the same snapshot
    with c.cursor() as cur:
        cur.execute("select output_hash from de_outputs where run_id = (select max(run_id) from de_runs) "
                    "and member = 'S05.N0000'")
        latest = {r[0] for r in cur.fetchall()}
    c.rollback()
    d = ds.build(manifest(cid, builds[0]), *loader.load(c, manifest(cid, builds[0])))
    r5 = next(r for r in d.rows if r.symbol == "S05.N0000")
    assert latest <= set(r5.sources) and r5.sources != first["S05.N0000"]
    sql(cluster, db, [("delete from f7_feature_rows where subject = 'S05.N0000' and panel_id = (select max(panel_id) "
                       "from f7_panels where issuer_id = (select issuer_id from de_outputs where member = 'S05.N0000' "
                       "and issuer_id is not null limit 1))", ())], disable=("f7_feature_rows",))
    with pytest.raises(ds.Refused, match="f7_row_missing"):
        loader.load(c, manifest(cid, builds[0]))


def test_l5_an_f7_row_of_another_mf_row_is_refused(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    sql(cluster, db, [("update mf_snapshot_rows set row_hash = repeat('7', 64) where symbol = 'S05.N0000'", ())],
        disable=("mf_snapshot_rows",))
    with pytest.raises(ds.Refused, match="f7_row_mismatch"):
        loader.load(c, manifest(cid, builds[0]))


def imports(cluster, db, batches):
    """batches: [(survivorship, basis, convention, recorded_at, bar day)]."""
    for surv, basis, conv, rec, day in batches:
        sql(cluster, db, [("insert into mf_history_imports (source_label, source_sha256, source_origin, "
                           "terms_reference, price_basis, survivorship, no_trade_convention, values_as_of, recorded_at) "
                           "values ('w', repeat('a', 64), 'o', 't', %s, %s, %s, '2024-01-01', %s) returning import_id",
                           (basis, surv, conv, rec))], role="cse_owner")
        sql(cluster, db, [("insert into mf_history_bars (import_id, symbol, trade_date, close, source_sheet, source_row) "
                           "values ((select max(import_id) from mf_history_imports), 'S01.N0000', %s, 10, 'S', 7)",
                           (day,))], role="cse_owner")


def test_l6_survivorship_only_from_batches_mf_admits_by_the_governing_time(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch)
    sql(cluster, db, [("update mf_snapshots set imported_inputs = true", ())], disable=("mf_snapshots",))
    imports(cluster, db, [("unknown", "adjusted", "row_absent", "2024-01-01T00:00:00Z", "2024-03-01"),
                          ("unknown", "raw", "volume_zero", "2024-01-01T00:00:00Z", "2024-03-01"),
                          ("unknown", "raw", "row_absent", "2024-07-15T00:00:00Z", "2024-03-01"),     # after T
                          ("survivors_only", "raw", "row_absent", "2024-01-01T00:00:00Z", "2024-03-01")])
    snaps, _l, _b = loader.load(c, manifest(cid, builds[0]))
    assert snaps[0].survivorship == "survivors_only"


def test_l7_available_survivorship_is_governed_by_h(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch, mode="AVAILABLE")
    sql(cluster, db, [("update mf_snapshots set imported_inputs = true", ())], disable=("mf_snapshots",))
    imports(cluster, db, [("survivors_only", "raw", "row_absent", "2024-01-01T00:00:00Z", "2024-03-01"),
                          ("unknown", "raw", "row_absent", "2024-07-15T00:00:00Z", "2024-03-01")])   # T < rec <= H
    h = datetime(2024, 7, 31, tzinfo=timezone.utc)
    m = manifest(cid, builds[0], eras=(ds.Era(date(2024, 1, 1), date(2024, 12, 31), "AVAILABLE", h),))
    snaps, _l, _b = loader.load(c, m)
    assert snaps[0].survivorship == "unknown"


def test_l8_an_available_dataset_end_to_end_and_imports_out_of_reach(cluster, monkeypatch):
    """M5-2 re-audit: an AVAILABLE dataset under a matching AVAILABLE build builds; a snapshot that read imported
    history with no batch in reach is `unknown`, never `complete`."""
    db = fs.fresh_db(cluster)
    c, sid, builds, cid = built(cluster, db, monkeypatch, mode="AVAILABLE")
    h = datetime(2024, 7, 31, tzinfo=timezone.utc)
    (b,) = sql(cluster, db, [("insert into mf_target_builds (mode, cutoff, knowledge_horizon, configuration_id, "
                             "target_definition_version, code_version, build_hash) values ('AVAILABLE', "
                             "'2024-08-31T00:00:00Z', %s, %s, 'targets.1', 'mf.1', repeat('9', 64)) "
                             "returning build_id", (h, cid))])
    sql(cluster, db, [("insert into mf_target_rows (build_id, reference_snapshot_id, symbol, horizon, state, "
                       "values_json, resolved_at, row_hash) select %s, reference_snapshot_id, symbol, horizon, state, "
                       "values_json, resolved_at, row_hash from mf_target_rows where build_id = %s",
                       (b[0], builds[0]))])
    m = manifest(cid, b[0], eras=(ds.Era(date(2024, 1, 1), date(2024, 12, 31), "AVAILABLE", h),))
    snaps, labels, build = loader.load(c, m)
    assert (build.mode, build.horizon) == ("AVAILABLE", h)
    d = ds.build(m, snaps, labels, build)
    assert len(d.rows) == 13 and d.labels and d.label_class == "reconstructed"
    sql(cluster, db, [("update mf_snapshots set imported_inputs = true", ())], disable=("mf_snapshots",))
    imports(cluster, db, [("complete", "raw", "row_absent", "2024-01-01T00:00:00Z", "2019-01-02")])   # out of reach
    snaps, _l, _b = loader.load(c, m)
    assert snaps[0].survivorship == "unknown" and snaps[0].imported

