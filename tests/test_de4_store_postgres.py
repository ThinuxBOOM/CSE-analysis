"""
DE-4 (design §10): the writer and verify against pending migration 0020 in a throwaway cluster. A panel with its
feature rows and a DE run with its outputs are written in one transaction; every hash re-proves; any tampering of a
stored column is reported; a refused insert leaves nothing behind. Skipped unless P1_PG_BINDIR is set. No CSE.
"""
import os
from decimal import Decimal as D

import pytest

import f64_support as fs
import f7_factories as ff
from worker.engines import registry as rg
from worker.engines import scoring as sc
from worker.feature_engine_store import codec, verify, writer
from worker.financial_features import eligibility as el
from worker.financial_features import features as ft
from worker.financial_features import panel as pn

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("de4s"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def snapshot(cluster, db):
    """One MF snapshot header for the run's foreign key (owner path, as MF's tests do)."""
    import hashlib
    import json
    js = json.dumps({"eligibility_policy_status": "provisional"}, sort_keys=True, separators=(",", ":"))
    cid = hashlib.sha256(js.encode()).hexdigest()
    c = fs.conn(cluster, db, user="cse_migrator")
    with c.cursor() as cur:
        cur.execute("set role cse_owner")
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values "
                    "(%s, %s, 'provisional')", (cid, js))
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, d_star, configuration_id, code_version, "
                    "input_digest, adjustment_set_hash, row_count, snapshot_hash) values ('KNOWN', 'point_in_time', "
                    "'2024-06-30T00:00:00+00:00', '2024-06-28', %s, 'mf.1', repeat('c',64), repeat('d',64), 0, "
                    "repeat('e',64)) returning snapshot_id", (cid,))
        sid = cur.fetchone()[0]
    c.commit()
    c.close()
    return sid


def batch(sid):
    p = pn.panel(ff.result([ff.fact(), ff.fact("revenue", value="1000")]), "live")
    vals = ft.annotate(p, ft.compute(p), "point_in_time")
    elig = el.eligibility(p, vals)
    pb = writer.PanelBatch(p, [dict(subject=p.issuer_id, level="issuer", values=vals, eligibility=elig,
                                    snapshot_id=sid)])
    e = rg.BY_ID["fund_leverage"]
    m = {f"I{i:02d}": {"equity_to_assets": sc.Value(D(i), "fresh")} for i in range(12)}
    outs = sc.score(e, m, group="general")
    from dataclasses import replace
    # every output cites the issuer's feature row, as assemble's fan-out does (verify checks the citation)
    run = writer.RunBatch("KNOWN", "2024-06-30T00:00:00+00:00", None, sid, "point_in_time",
                          [(replace(o, evidence={"f7_subject": p.issuer_id}), p.issuer_id) for o in outs.values()])
    return pb, run


def test_w1_write_and_verify_clean(cluster):
    db = fs.fresh_db(cluster)
    sid = snapshot(cluster, db)
    pb, run = batch(sid)
    w = fs.conn(cluster, db, user="cse_worker")
    out = writer.write(w, f7_config=codec.f7_configuration(), de_config=codec.de_configuration(), panels=[pb],
                       run=run)
    assert out["rows"] == 1 and out["run_id"] and len(out["panels"]) == 1
    assert verify.report(w) == []
    # writing the same configurations again is a no-op, and a second batch verifies too
    out2 = writer.write(w, f7_config=codec.f7_configuration(), de_config=codec.de_configuration(), panels=[pb],
                        run=run)
    assert out2["run_hash"] != out["run_hash"]          # the stamp differs: two runs are two records
    assert verify.report(w) == []


TAMPER = [
    ("f7_panels", "state", "'anchor_absent'", "panel"),
    ("f7_panels", "created_by", "'someone'", "panel"),
    ("f7_panels", "recorded_at", "recorded_at + interval '1 second'", "panel"),
    ("f7_feature_rows", "features_json", "'{}'", "feature row"),
    ("f7_feature_rows", "mf_row_hash", "repeat('9',64)", "feature row"),
    ("de_outputs", "output_json", "replace(output_json, 'scored', 'blocked_quality')", "output"),
    ("de_outputs", "state", "'not_applicable'", "output"),
    ("de_runs", "counts_json", "'{}'", "run"),
]


@pytest.mark.parametrize("table,col,expr,kind", TAMPER)
def test_w2_every_tampered_column_is_reported(cluster, table, col, expr, kind):
    db = fs.fresh_db(cluster)
    sid = snapshot(cluster, db)
    pb, run = batch(sid)
    w = fs.conn(cluster, db, user="cse_worker")
    writer.write(w, f7_config=codec.f7_configuration(), de_config=codec.de_configuration(), panels=[pb], run=run)
    su = fs.conn(cluster, db, user="postgres")          # bypass the append-only trigger, as an attacker with DDL
    with su.cursor() as cur:
        cur.execute(f"alter table {table} disable trigger user")
        cur.execute(f"update {table} set {col} = {expr} where ctid = (select min(ctid) from {table})")
        cur.execute(f"alter table {table} enable trigger user")
    su.commit()
    problems = verify.report(w)
    assert any(p.startswith(kind) for p in problems), problems


def test_w3_a_missing_output_breaks_the_run_hash(cluster):
    db = fs.fresh_db(cluster)
    sid = snapshot(cluster, db)
    pb, run = batch(sid)
    w = fs.conn(cluster, db, user="cse_worker")
    writer.write(w, f7_config=codec.f7_configuration(), de_config=codec.de_configuration(), panels=[pb], run=run)
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table de_outputs disable trigger user")
        cur.execute("delete from de_outputs where member = 'I00'")
        cur.execute("alter table de_outputs enable trigger user")
    su.commit()
    assert any(p.startswith("run") for p in verify.report(w))


def test_w4_a_refusal_leaves_nothing(cluster):
    db = fs.fresh_db(cluster)
    sid = snapshot(cluster, db)
    pb, run = batch(sid)
    bad = writer.RunBatch("KNOWN", run.cutoff, None, sid + 999, run.label_class, run.outputs)   # no such snapshot
    w = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(Exception):
        writer.write(w, f7_config=codec.f7_configuration(), de_config=codec.de_configuration(), panels=[pb], run=bad)
    with w.cursor() as cur:
        for t in ("f7_configurations", "f7_panels", "f7_feature_rows", "de_runs", "de_outputs"):
            cur.execute(f"select count(*) from {t}")
            assert cur.fetchone()[0] == 0, t
    w.rollback()


def test_w5_codec_refuses_what_the_tables_would():
    with pytest.raises(ValueError):
        codec.run_record(mode="AVAILABLE", cutoff="2024-06-30T00:00:00+00:00", knowledge_horizon=None, snapshot_id=1,
                         f7_configuration_id="a", de_configuration_id="b", label_class="reconstructed", outputs=[],
                         stamp=("2024-07-01T00:00:00+00:00", "w"))
    with pytest.raises(ValueError):
        codec._norm("2024-06-30T00:00:00")              # an instant without a zone is never guessed
    with pytest.raises(ValueError):
        codec.f7_configuration("final")
