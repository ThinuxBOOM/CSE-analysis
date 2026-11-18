"""
Phase 5 M5-6, the re-audit's findings (at 9622e3b), each pinned: A a declared embargo is a whole number >= 1; B one
access result, never rewritten; C every writer column stamped (actor, created_by, approved_by); D gate 7 from the
registry (survivorship, the waiver that covers the dataset); E a declaration lists its policies; F a designation lists
only production models of its output and horizon; G reconstructed evidence carries its label and states no
point-in-time share. Minors: alpha bounded, the family's shape, the access id never overwritten by a result, a
retrospective period is previously_scored, the policy cutoff boundary, rollforward_of, multiplicity by overlap.
Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import json
import os
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

import f64_support as fs
import test_m56_gates_unit as gu
import test_m56_migration_postgres as mp
import test_m56_store_postgres as sp
from worker.ml_models.artifact import Artifact
from worker.ml_registry import store

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
UTC = timezone.utc


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m56r"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(sp.PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def owner(cluster, db):
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    o.commit()
    return o


def model(w, ids, **kw):
    a = dict(kind="linear", configuration_id=ids["config"], output="direction", horizon=5, dataset_id=ids["dataset"],
             protocol_id=ids["protocol"], training_cutoff=datetime(2023, 6, 30, tzinfo=UTC),
             evidence_label_class="point_in_time", artifact=Artifact.make("linear", {"w": [len(kw) + 7.0]}),
             environment_json={"threads": 1})
    a.update(kw)
    return store.register_model(w, **a)


def test_a_e_and_minor_declarations(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    for emb in (-400, 0, True, 2.5):
        with pytest.raises(store.Refused, match="embargo_sessions"):
            store.declare_protocol(w, {**sp.declaration(), "embargo_sessions": emb}, gu.GOOD_PLAN)
    for pol in (None, [], [""]):
        d = sp.declaration()
        d["policies"] = pol
        with pytest.raises(store.Refused, match="policy"):
            store.declare_protocol(w, d, gu.GOOD_PLAN)
    for alpha in (0.9, 0, -0.01):
        with pytest.raises(store.Refused, match="alpha"):
            store.declare_protocol(w, {**sp.declaration(), "alpha": alpha}, gu.GOOD_PLAN)
    for fam in ("cfg-linear", [], {"direction:5": "x"}, [1]):
        with pytest.raises(store.Refused, match="family"):
            store.declare_protocol(w, {**sp.declaration(), "family": fam}, gu.GOOD_PLAN)


def test_b_one_access_result_never_rewritten_and_its_id_is_the_access(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    assert store.validate(w, ids["model"], gu.GOOD, "code.1")["passed"]
    with pytest.raises(store.Refused, match="access"):
        store.record_metrics(w, "test", "access", {"access_id": 1, "nominees_passed": [ids["model"]]},
                             model_id=ids["model"])

    def boom():
        raise RuntimeError("evaluation failed")
    with pytest.raises(RuntimeError):
        store.access(w, ids["protocol"], sp.period_of(w, ids["protocol"]),
                     [{"model_id": ids["model"], "predictions_sha256": "d" * 64}], boom)
    js = json.dumps({"access_id": "1", "nominees_passed": [ids["model"]]})
    assert mp.refused(w, "insert into ml_model_metrics (model_id, scope, stratum, metrics_json) values "
                         "(%s, 'test', 'access', %s)", (ids["model"], js))              # a second result: refused
    w.rollback()
    o = fs.conn(cluster, db, user="cse_migrator")
    assert not store.promotion_evidence(o, ids["model"], sp.EVIDENCE).nominee_passed    # the failure is the result
    db2 = fs.fresh_db(cluster)
    w2, ids2 = sp.registry_world(cluster, db2)
    store.validate(w2, ids2["model"], gu.GOOD, "code.1")
    out = store.access(w2, ids2["protocol"], sp.period_of(w2, ids2["protocol"]),
                       [{"model_id": ids2["model"], "predictions_sha256": "d" * 64}],
                       lambda: {"nominees_passed": [ids2["model"]], "access_id": 999})
    assert out["access_id"] != 999                                                     # never overwritten
    o2 = fs.conn(cluster, db2, user="cse_migrator")                     # and the stored result is the access's own
    assert store.promotion_evidence(o2, ids2["model"], sp.EVIDENCE).nominee_passed


def test_c_writer_columns_are_stamped(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    assert "actor" in mp.refused(w, "insert into ml_model_events (model_id, seq, status, reason, actor) values "
                                    "(%s, 2, 'rejected', 'r', 'cse_migrator')", (ids["model"],))
    assert "created_by" in mp.refused(w, "insert into ml_runs (protocol_id, configuration_id, dataset_id, output, "
                                         "horizon, code_version, created_by) values (%s, %s, %s, 'direction', 5, "
                                         "'c', 'someone')", (ids["protocol"], ids["config"], ids["dataset"]))
    w.rollback()
    o = owner(cluster, db)
    assert "approved_by" in mp.refused(o, "insert into ml_waivers (kind, covers_json, note, approved_by) values "
                                          "('mode', '{}', 'a forged approver', 'someone')")


def test_d_gate7_reads_the_registry(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    ds = replace(sp.dataset(version="f" * 64), survivorship="survivors_only")
    did = store.register_dataset(w, ds, False)
    mid = model(w, ids, dataset_id=did)
    rep = store.validate(w, mid, replace(gu.GOOD, survivorship="complete", survivorship_waiver=4242), "code.1")
    assert not rep["gates"][7]["pass"]                                 # neither the caller's word nor a phantom waiver
    o = owner(cluster, db)
    with o.cursor() as cur:
        cur.execute("insert into ml_waivers (kind, covers_json, note) values ('survivorship', %s, 'accepted for this "
                    "dataset')", (json.dumps({"datasets": ["e" * 64]}),))           # another dataset: not covered
    o.commit()
    assert not store.validate(w, mid, gu.GOOD, "code.1")["gates"][7]["pass"]
    with o.cursor() as cur:
        cur.execute("insert into ml_waivers (kind, covers_json, note) values ('survivorship', %s, 'accepted for this "
                    "dataset')", (json.dumps({"datasets": ["f" * 64]}),))
    o.commit()
    rep = store.validate(w, mid, gu.GOOD, "code.1")
    assert rep["passed"] and "survivorship_waived" in rep["labels"]


def test_f_a_designation_lists_only_production_models_of_its_output_and_horizon(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    store.validate(w, ids["model"], gu.GOOD, "code.1")
    store.access(w, ids["protocol"], sp.period_of(w, ids["protocol"]),
                 [{"model_id": ids["model"], "predictions_sha256": "d" * 64}],
                 lambda: {"nominees_passed": [ids["model"]]})
    stray = model(w, ids, output="return", horizon=21)
    store.transition(w, stray, "rejected", "not good")
    o = fs.conn(cluster, db, user="cse_migrator")
    with pytest.raises(store.Refused, match="designation"):
        store.promote(o, ids["model"], sp.EVIDENCE, purpose="route1", output="direction", horizon=5,
                      list_ids=[ids["model"], stray], note="the owner tries a stray id", code_version="code.1")
    # the database refuses it too: a designation of this transaction naming a research model
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                    "values (%s, 'promotion', true, '{}', 1, 'c')", (ids["model"],))
        cur.execute(mp.PROD, (ids["config"], ids["dataset"], ids["protocol"], ids["artifact"].sha256, ids["model"]))
        prod = cur.fetchone()[0]
        cur.execute("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "('route1', 'direction', 5, %s, '{}', 'lists a research model')", (json.dumps([prod, stray]),))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', 'p')",
                    (prod,))
    with pytest.raises(Exception, match="only production models"):
        o.commit()
    o.rollback()


def test_g_reconstructed_evidence_is_labelled_and_states_no_share(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    mid = model(w, ids, evidence_label_class="reconstructed")
    store.validate(w, mid, gu.GOOD, "code.1")
    o = fs.conn(cluster, db, user="cse_migrator")
    with pytest.raises(store.Refused, match="no point_in_time share"):
        store.promotion_evidence(o, mid, sp.EVIDENCE)
    e = store.promotion_evidence(o, mid, {**sp.EVIDENCE, "point_in_time_share": 0})
    assert "reconstructed_evidence" in e.model_labels
    mixed = model(w, ids, evidence_label_class="mixed")
    assert "reconstructed_evidence" in store.promotion_evidence(o, mixed, sp.EVIDENCE).model_labels


def test_minor_retrospective_period_is_previously_scored_in_the_database(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    dec = json.dumps({"family": ["q"]})
    pid = mp.one(w, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values (%s, %s, "
                    "'exploratory') returning protocol_id", (dec, mp.sha(dec)))
    assert mp.refused(w, "insert into ml_test_periods (protocol_id, period, timing, previously_scored) values "
                         "(%s, '[2024-01-01Z,2024-06-30Z]', 'retrospective', false)", (pid,))


def test_survivors_alpha_policy_boundary_rollforward_and_overlap(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    # alpha comes from the registry: a protocol at 0.01 needs results at 0.01 / m whatever the caller says
    first_v = sp.declaration()["folds"][0]["v_start"]
    o = owner(cluster, db)
    edge = store.register_configuration(o, "policy", {"name": "edge"}, status="validated",
                                        evidence_cutoff=datetime.fromisoformat(first_v))
    dec = {**sp.declaration(test=("2026-07-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00")), "alpha": 0.01,
           "policies": [sp.POLICY_ID, edge]}
    pid, _s, _r = store.declare_protocol(w, dec, gu.GOOD_PLAN)
    mid = model(w, ids, protocol_id=pid)
    facts = store.registry_facts(w, mid)
    assert facts["alpha"] == 0.01
    assert facts["multiplicity"] == 2                 # a policy whose cutoff equals the first V_start counts as tried
    rep = store.validate(w, mid, replace(gu.GOOD, alpha=0.05), "code.1")
    assert not rep["gates"][5]["pass"]                # GOOD's results are at 0.05/3, above 0.01/2
    # rollforward_of is a label of the promotion evidence
    rf = model(w, ids, rollforward_of=ids["model"])
    o2 = fs.conn(cluster, db, user="cse_migrator")
    assert "rollforward_of" in store.promotion_evidence(o2, rf, sp.EVIDENCE).model_labels
    # multiplicity counts partial overlap of validation sessions, not only containment
    part = [{"v_start": "2018-03-01T00:00:00+00:00", "v_end": "2018-09-30T00:00:00+00:00"}]
    p2, _s, _r = store.declare_protocol(w, {**sp.declaration(test=("2027-07-01T00:00:00+00:00",
                                                                   "2027-12-31T00:00:00+00:00"), folds=part),
                                            "family": ["other"]}, gu.GOOD_PLAN)
    cid = store.register_configuration(w, "model", {"adapter": "gbt", "k": 1})
    rid = store.start_run(w, ids["protocol"], cid, ids["dataset"], "direction", 5, "code.1")
    store.record_metrics(w, "fold", "overall", {"log_loss": 1.0}, run_id=rid, fold_no=1)
    assert store.multiplicity(w, "direction", 5, p2) >= 2


def test_survivor_replicates_floor_grows_with_m():
    from types import SimpleNamespace as NS
    from worker.ml_registry import gates as gt
    r = NS(state="tested", rejects=True, level=0.05 / 1000, replicates=9999, block_length=10, simulation=False)
    e = replace(gu.GOOD, multiplicity=1000, baseline_results={b: r for b in gu.GOOD.required_baselines})
    assert not gt.validation_report(e)["gates"][5]["pass"]          # ceil(20 m / alpha) = 400 000 > 9 999
