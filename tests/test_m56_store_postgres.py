"""
Phase 5 M5-6: the registry store against migrations 0001 - 0021. The lifecycle end to end (configuration, dataset,
protocol and its test periods, a run with a fold model and metrics, a model, validation by gates 2 to 8, the one
access, the owner's promotion with its designation, the alert when a source is later rejected); gate 1 enforced by
the database through the store; MLK-4 (a dataset that does not withhold an unspent period; a second access; a nominee
of another protocol; a failed result leaves no access) and MLK-5 (rows inside another protocol's unspent interval; the
multiplicity follows the sessions). Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import os
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

import f64_support as fs
import test_m56_gates_unit as gu
from worker.ml_dataset import dataset as ds
from worker.ml_models.artifact import Artifact
from worker.ml_registry import store

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")
UTC = timezone.utc


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m56s"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def dataset(version="a" * 64, date_to=date(2023, 12, 31)):
    m = ds.Manifest("mf", "f7", "de", date(2015, 1, 1), date_to, (ds.Era(date(2015, 1, 1), date_to, "AVAILABLE",
                                                                          datetime(2026, 1, 1, tzinfo=UTC)),),
                    7, "targets.1")
    return ds.Dataset(m, version, (), {}, "reconstructed", "complete", "captured_only", {"rows": 0})


CFG = {"adapter": "linear", "horizon": 5}
CFG_ID = store.sha(store.canonical({**CFG, "kind": "model", "status": "provisional"}))


def declaration(test=("2024-07-01T00:00:00+00:00", "2025-06-30T00:00:00+00:00"), folds=None, family=None):
    return {"family": family or [CFG_ID], "primary": ["direction", 5], "nomination": "best_validation_loss",
            "embargo_sessions": 5,
            "criterion": {"form": "gate_test_every_baseline_bonferroni_over_nominees"},
            "folds": folds or [{"v_start": f"20{18 + i}-01-01T00:00:00+00:00", "v_end": f"20{18 + i}-06-30T00:00:00+00:00"}
                               for i in range(5)],
            "test_periods": [list(test)]}


def registry_world(cluster, db, synthetic=False, **decl):
    w = fs.conn(cluster, db, user="cse_worker")
    cid = store.register_configuration(w, "model", {"adapter": "linear", "horizon": 5})
    did = store.register_dataset(w, dataset(), synthetic)
    pid, state, reasons = store.declare_protocol(w, declaration(**decl), gu.GOOD_PLAN)
    assert state == "sufficient" and reasons == []
    rid = store.start_run(w, pid, cid, did, "direction", 5, "code.1")
    art = Artifact.make("linear", {"w": [1.0, 2.0]})
    store.record_fold_model(w, rid, 1, {"c_train": "2017-12-31"}, art, "c" * 64)
    store.record_metrics(w, "fold", "overall", {"log_loss": 0.9}, run_id=rid, fold_no=1)
    mid = store.register_model(w, kind="linear", configuration_id=cid, output="direction", horizon=5,
                               dataset_id=did, protocol_id=pid, training_cutoff=datetime(2023, 6, 30, tzinfo=UTC),
                               evidence_label_class="reconstructed", artifact=art, environment_json={"threads": 1})
    return w, dict(config=cid, dataset=did, protocol=pid, run=rid, model=mid, artifact=art)


def period_of(w, pid):
    with w.cursor() as cur:
        cur.execute("select period_id from ml_test_periods where protocol_id = %s", (pid,))
        out = cur.fetchone()[0]
    w.rollback()
    return out


def test_r1_the_lifecycle_end_to_end(cluster):
    db = fs.fresh_db(cluster)
    w, ids = registry_world(cluster, db)
    assert store.status(w, ids["model"]) == "candidate"
    assert store.load_artifact(w, ids["artifact"].sha256) == ids["artifact"].text
    rep = store.validate(w, ids["model"], gu.GOOD, "code.1")
    assert rep["passed"] and store.status(w, ids["model"]) == "validated"
    pid = period_of(w, ids["protocol"])
    res = store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                       lambda: {"nominees_passed": [ids["model"]]})
    assert res["nominees_passed"] == [ids["model"]] and store.lifetime_accesses(w) == 1
    with pytest.raises(Exception):                                                     # one access
        store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                     lambda: {"nominees_passed": []})
    with pytest.raises(store.Refused, match="owner_path"):
        store.promote(w, ids["model"], gu.GOODP, purpose="route1", output="direction", horizon=5,
                      list_ids=[ids["model"]], note="the owner promotes it", code_version="code.1")
    o = fs.conn(cluster, db, user="cse_migrator")
    prod, prep = store.promote(o, ids["model"], gu.GOODP, purpose="route1", output="direction", horizon=5,
                               list_ids=[ids["model"]], note="the owner promotes it", code_version="code.1")
    assert prep["passed"] and prod and store.status(w, prod) == "production"
    assert store.alerts(w) == []
    store.transition(w, ids["model"], "rejected", "a later finding")
    assert store.alerts(w) == [{"production": prod, "rejected_source": ids["model"]}]   # reported, not retired
    assert store.status(w, prod) == "production"


def test_r2_a_failing_validation_is_recorded_and_changes_nothing(cluster):
    db = fs.fresh_db(cluster)
    w, ids = registry_world(cluster, db)
    rep = store.validate(w, ids["model"], replace(gu.GOOD, size_shown=False), "code.1")
    assert not rep["passed"] and store.status(w, ids["model"]) == "candidate"
    with w.cursor() as cur:
        cur.execute("select passed from ml_gate_reports where model_id = %s", (ids["model"],))
        assert cur.fetchall() == [(False,)]
    w.rollback()
    o = fs.conn(cluster, db, user="cse_migrator")
    prod, _ = store.promote(o, ids["model"], replace(gu.GOODP, access_done=False), purpose="route1",
                            output="direction", horizon=5, list_ids=[ids["model"]], note="the owner tries it",
                            code_version="code.1")
    assert prod is None


def test_r3_gate1_a_synthetic_dataset_is_never_validated(cluster):
    db = fs.fresh_db(cluster)
    w, ids = registry_world(cluster, db, synthetic=True)
    with pytest.raises(Exception, match="synthetic"):
        store.validate(w, ids["model"], gu.GOOD, "code.1")
    assert store.status(w, ids["model"]) == "candidate"
    with w.cursor() as cur:
        cur.execute("select count(*) from ml_gate_reports")
        assert cur.fetchone()[0] == 0                                                  # the report rolled back too
    w.rollback()


def test_mlk4_test_periods_withheld_accessed_once_and_only_by_their_nominees(cluster):
    db = fs.fresh_db(cluster)
    w, ids = registry_world(cluster, db)
    m = dataset(date_to=date(2025, 12, 31)).manifest
    with pytest.raises(store.Refused, match="test_labels"):
        store.check_manifest(w, m)                                                     # asks for test labels
    start, end = store.withheld_for(w, m.date_from, m.date_to)[0]
    assert store.check_manifest(w, replace(m, withheld=((start, end),)))
    assert store.check_manifest(w, dataset(date_to=date(2023, 12, 31)).manifest)       # before the period
    pid = period_of(w, ids["protocol"])
    with pytest.raises(store.Refused, match="not validated"):                       # audit M4: a candidate
        store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                     lambda: {"nominees_passed": []})
    assert store.validate(w, ids["model"], gu.GOOD, "code.1")["passed"]
    dec = declaration(test=("2026-07-01T00:00:00+00:00", "2027-06-30T00:00:00+00:00"))
    other, _s, _r = store.declare_protocol(w, dec, gu.GOOD_PLAN)
    with pytest.raises(store.Refused, match="nominee"):
        store.access(w, other, period_of(w, other), [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                     lambda: {"nominees_passed": []})
    for bad in ("", "z" * 64):                                                        # audit M4: hex only
        with pytest.raises(store.Refused, match="frozen"):
            store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": bad}], lambda: {})
    def boom():
        raise RuntimeError("the criterion failed to compute")
    with pytest.raises(RuntimeError):
        store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": "d" * 64}], boom)
    assert store.lifetime_accesses(w) == 1                     # audit M5: looking is an access, whatever follows
    assert all(p[0] != pid for p in store.unspent_periods(w))                         # spent: no longer withheld
    with pytest.raises(Exception):                                                     # and it cannot be retried
        store.access(w, ids["protocol"], pid, [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                     lambda: {"nominees_passed": []})
    with w.cursor() as cur:
        cur.execute("select metrics_json from ml_model_metrics where scope = 'test'")
        assert "the criterion failed to compute" in cur.fetchone()[0]                  # the failure is the result
    w.rollback()


def test_mlk5_other_protocols_intervals_and_the_multiplicity(cluster):
    db = fs.fresh_db(cluster)
    w, ids = registry_world(cluster, db)
    dec = declaration(test=("2026-07-01T00:00:00+00:00", "2027-06-30T00:00:00+00:00"), family=["a", "b"])
    other, _s, _r = store.declare_protocol(w, dec, gu.GOOD_PLAN)
    inside = datetime(2024, 9, 1, tzinfo=UTC)
    with pytest.raises(store.Refused, match="unspent_interval"):
        store.check_rows_outside_unspent(w, other, [inside])                           # the first protocol's period
    assert store.check_rows_outside_unspent(w, ids["protocol"], [inside])              # its own period is not
    assert store.multiplicity(w, "direction", 5, other) == 2                           # its declared family
    for k in range(3):                                                                 # three more configurations
        cid = store.register_configuration(w, "model", {"adapter": "gbt", "k": k})     # tried on its sessions
        rid = store.start_run(w, ids["protocol"], cid, ids["dataset"], "direction", 5, "code.1")
        store.record_metrics(w, "fold", "overall", {"log_loss": 1.0}, run_id=rid, fold_no=1)
    assert store.multiplicity(w, "direction", 5, other) == 4                           # 1 + 3 tried: the larger
    assert store.multiplicity(w, "return", 5, other) == 2


def test_protocol_declarations_are_complete_and_runs_follow_them(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    bad = declaration()
    del bad["family"]
    with pytest.raises(store.Refused, match="family"):
        store.declare_protocol(w, bad, gu.GOOD_PLAN)
    weak = declaration()
    weak["criterion"] = {"form": "beats_prior_once"}
    with pytest.raises(store.Refused, match="criterion"):
        store.declare_protocol(w, weak, gu.GOOD_PLAN)
    pid, state, reasons = store.declare_protocol(w, declaration(test=("2030-01-01T00:00:00+00:00",
                                                                      "2030-12-31T00:00:00+00:00")),
                                                 replace(gu.GOOD_PLAN, test_dates=10))
    assert state == "exploratory" and reasons
    early = [{"v_start": f"201{i}-01-01T00:00:00+00:00", "v_end": f"201{i}-06-30T00:00:00+00:00"} for i in range(5)]
    old, _s, _r = store.declare_protocol(w, declaration(test=("2021-01-01T00:00:00+00:00",
                                                              "2021-06-30T00:00:00+00:00"), folds=early), gu.GOOD_PLAN)
    with pytest.raises(store.Refused, match="test_period"):        # audit M3: inside its own validation folds
        store.declare_protocol(w, declaration(test=("2019-01-01T00:00:00+00:00", "2019-06-30T00:00:00+00:00")),
                               gu.GOOD_PLAN)
    with pytest.raises(store.Refused, match="test_period"):        # inside the embargo after the last V_end
        store.declare_protocol(w, declaration(test=("2022-07-03T00:00:00+00:00", "2022-12-31T00:00:00+00:00")),
                               gu.GOOD_PLAN)
    nofold = declaration()
    del nofold["embargo_sessions"]
    with pytest.raises(store.Refused, match="embargo_sessions"):
        store.declare_protocol(w, nofold, gu.GOOD_PLAN)
    with w.cursor() as cur:
        cur.execute("select timing, previously_scored from ml_test_periods where protocol_id = %s", (pid,))
        assert cur.fetchone() == ("prospective", False)
        cur.execute("select timing, previously_scored from ml_test_periods where protocol_id = %s", (old,))
        assert cur.fetchone() == ("retrospective", True)              # §5.4: retrospective is previously_scored
    w.rollback()
