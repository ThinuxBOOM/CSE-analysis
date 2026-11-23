"""
Phase 5 M5-8 against migrations 0001 - 0021: the rehearsal recorded in the registry as a synthetic dataset. Every
fold's model and artifact, its metrics and the final candidate are written; validation writes its report from the
rehearsal's own evidence (exploratory: a small world) and the model stays `candidate`; with evidence that passes
gates 2 to 8, the database's gate 1 refuses `validated` for a synthetic dataset. Skipped unless P1_PG_BINDIR is set.
"""
import os

import pytest

import f64_support as fs
import test_m56_gates_unit as gu
import test_m56_store_postgres as sp

pytest.importorskip("numpy")
pytest.importorskip("scipy")

from worker.ml_registry import store                              # noqa: E402
from worker.ml_rehearsal import rehearsal as rh                   # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m58"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def test_r1_the_rehearsal_recorded_and_never_validated(cluster):
    db = fs.fresh_db(cluster)
    policy = sp.owner_policy(cluster, db)
    w = fs.conn(cluster, db, user="cse_worker")
    world = rh.World()
    kept = {}
    report = rh.run(world, size_seeds=200, keep=kept)
    out = rh.record(w, report, kept, world, policy)
    assert out["sufficiency"] == "exploratory" and any("securities" in r for r in out["sufficiency_reasons"])
    assert out["refused"] is None and not out["validation"]["passed"] and out["status"] == "candidate"
    gates = out["validation"]["gates"]
    gate = lambda n: gates.get(n) or gates.get(str(n))                         # noqa: E731
    assert not gate(3)["pass"]                         # the rehearsal runs no leakage suite: never claimed
    assert not gate(4)["pass"]                         # fold 2's reliability is outside its tolerance
    assert gate(8)["pass"]                             # the real domain is recorded
    with w.cursor() as cur:
        cur.execute("select domain_json from ml_models where model_id = %s", (out["model"],))
        import json
        assert json.loads(cur.fetchone()[0]) == report["domain"]
    w.rollback()
    with w.cursor() as cur:
        cur.execute("select synthetic from ml_datasets where dataset_id = %s", (out["dataset"],))
        assert cur.fetchone() == (True,)
        cur.execute("select fold_no, artifact_sha256, predictions_sha256 from ml_fold_models where run_id = %s "
                    "order by fold_no", (out["run"],))
        rows = cur.fetchall()
        cur.execute("select count(*) from ml_model_metrics where run_id = %s and scope = 'fold'", (out["run"],))
        assert cur.fetchone()[0] == 5
        cur.execute("select state from ml_run_events where run_id = %s order by seq", (out["run"],))
        assert [r[0] for r in cur.fetchall()] == ["started", "finished"]
        cur.execute("select passed from ml_gate_reports where model_id = %s", (out["model"],))
        assert cur.fetchall() == [(False,)]
    w.rollback()
    assert rows == [(f["fold"], f["artifact"], f["predictions"]) for f in report["folds"]]
    for (_n, sha, _p), art in zip(rows, kept["artifacts"]):
        assert store.load_artifact(w, sha) == art.text                   # MLK-16: the bytes registered
    # gate 1: the registry's own facts keep the rehearsal exploratory, so `validate` never reaches the event; a
    # `validated` event written directly is refused by the database for a synthetic dataset, before any report check
    import test_m56_migration_postgres as mp
    why = mp.refused(w, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', "
                        "'forced')", (out["model"],))
    assert why and "gate 1" in why and "synthetic" in why
    w.rollback()
    rep = store.validate(w, out["model"], gu.GOOD, "rehearsal.1")         # the caller's evidence cannot override it
    assert not rep["passed"] and store.status(w, out["model"]) == "candidate"
