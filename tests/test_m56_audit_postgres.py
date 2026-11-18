"""
Phase 5 M5-6, the first audit's Majors (at 6678ca5), each pinned: M1 a test period only in its protocol's own
transaction (in test_m56_migration_postgres g3); M2 the database's own stamps; M3 the embargo after the last V_end
(in test_m56_store_postgres); M4 and M5 the access (in test_m56_store_postgres); M6 retiring a production model is the
owner's; M7 a validated configuration is the owner's and gate 6 reads the registry; M8 gate 5 (in
test_m56_gates_unit); M9 the registry's facts replace the caller's; M10 MLK-4 at registration and at the run; minor
11 a production copy equals its source. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
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
from worker.ml_registry import store

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
UTC = timezone.utc


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m56a"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(sp.PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def test_m2_stamps_are_the_databases_own(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    dec = json.dumps({"family": ["q"]})
    proto = "insert into ml_protocols (declaration_json, declaration_hash, sufficiency, recorded_at) values " \
            "(%s, %s, 'sufficient', %s)"
    assert "recorded_at" in mp.refused(w, proto, (dec, mp.sha(dec), "2000-01-01Z"))     # MLK-6: an earlier stamp
    js = json.dumps({"kind": "model", "status": "provisional", "a": 1}, sort_keys=True)
    assert "registered_by" in mp.refused(w, "insert into ml_configurations (configuration_id, configuration_json, "
                                            "kind, status, registered_by) values (%s, %s, 'model', 'provisional', "
                                            "'cse_owner')", (mp.sha(js), js))
    assert mp.refused(w, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values "
                         "(%s, %s, 'sufficient')", (dec, mp.sha(dec))) is None


def test_m6_retiring_a_production_model_is_the_owners(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    store.validate(w, ids["model"], gu.GOOD, "code.1")
    store.access(w, ids["protocol"], sp.period_of(w, ids["protocol"]),
                 [{"model_id": ids["model"], "predictions_sha256": "d" * 64}], lambda: {"nominees_passed": [ids["model"]]})
    o = fs.conn(cluster, db, user="cse_migrator")
    prod, _ = store.promote(o, ids["model"], gu.GOODP, purpose="route1", output="direction", horizon=5,
                            list_ids=[ids["model"]], note="the owner promotes it", code_version="code.1")
    with pytest.raises(store.Refused, match="owner"):
        store.transition(w, prod, "retired", "the worker tries")
    seq = 2
    assert "owner decision" in mp.refused(w, "insert into ml_model_events (model_id, seq, status, reason) values "
                                             "(%s, %s, 'retired', 'r')", (prod, seq))
    w.rollback()
    store.transition(o, prod, "retired", "the owner retires it")
    assert store.status(w, prod) == "retired"


def test_m7_validated_configurations_are_the_owners_and_gate6_reads_the_registry(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(Exception, match="owner decision"):
        store.register_configuration(w, "policy", {"name": "eligibility.1"}, status="validated")
    w.rollback()
    prov = store.register_configuration(w, "policy", {"name": "eligibility.1"})
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    o.commit()
    good = store.register_configuration(o, "policy", {"name": "scoring.1"}, status="validated",
                                        evidence_cutoff=datetime(2017, 1, 1, tzinfo=UTC))
    cid = store.register_configuration(w, "model", sp.CFG)
    did = store.register_dataset(w, sp.dataset(), False)
    for policies, ok in (([good], True), ([good, prov], False)):
        dec = {**sp.declaration(), "policies": policies}
        if not ok:
            dec["test_periods"] = [["2026-07-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"]]
        pid, _s, _r = store.declare_protocol(w, dec, gu.GOOD_PLAN)
        mid = store.register_model(w, kind="linear", configuration_id=cid, output="direction", horizon=5,
                                   dataset_id=did, protocol_id=pid, training_cutoff=datetime(2023, 6, 30, tzinfo=UTC),
                                   evidence_label_class="reconstructed",
                                   artifact=sp.Artifact.make("linear", {"w": [float(ok)]}),
                                   environment_json={"threads": 1})
        # the caller claims every policy validated; the registry says otherwise for `prov`
        rep = store.validate(w, mid, replace(gu.GOOD, policy_status={p: "validated" for p in policies}), "code.1")
        assert rep["gates"][6]["pass"] is ok, (policies, rep["gates"][6])
    facts = store.registry_facts(w, mid)
    assert facts["policy_status"][prov] == "provisional"
    assert facts["multiplicity"] >= 2              # MLK-6: a policy without an evidence cutoff counts as tried


def test_m9_the_registrys_facts_replace_the_callers(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    other = store.register_configuration(w, "model", {"adapter": "gbt", "horizon": 5})
    mid = store.register_model(w, kind="gbt", configuration_id=other, output="direction", horizon=5,
                               dataset_id=ids["dataset"], protocol_id=ids["protocol"],
                               training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="reconstructed",
                               artifact=sp.Artifact.make("gbt", {"t": 1}), environment_json={"threads": 1})
    rep = store.validate(w, mid, replace(gu.GOOD, in_family=True), "code.1")       # the caller says in family
    assert not rep["gates"][2]["pass"] and "family" in rep["gates"][2]["reason"]
    m21 = store.register_model(w, kind="linear", configuration_id=ids["config"], output="direction", horizon=21,
                               dataset_id=ids["dataset"], protocol_id=ids["protocol"],
                               training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="reconstructed",
                               artifact=sp.Artifact.make("linear", {"w": [9.0]}), environment_json={"threads": 1})
    rep = store.validate(w, m21, gu.GOOD, "code.1")                                   # minor 14: not its primary
    assert not rep["gates"][2]["pass"] and "not sufficient" in rep["gates"][2]["reason"]
    f = store.registry_facts(w, ids["model"])
    assert f["declared_folds"] == 5 and f["in_family"] and f["multiplicity"] == 1 and f["horizon"] == 5


def test_m10_a_dataset_must_withhold_unspent_periods_at_registration_and_at_the_run(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    reaching = sp.dataset(version="b" * 64, date_to=date(2025, 12, 31))
    with pytest.raises(store.Refused, match="test_labels"):
        store.register_dataset(w, reaching, False)
    # a period declared after a dataset was registered: the dataset's next run is refused
    m = replace(sp.dataset().manifest, date_to=date(2026, 12, 31),
                withheld=((datetime(2024, 7, 1, tzinfo=UTC), datetime(2025, 6, 30, tzinfo=UTC)),))
    early = store.register_dataset(w, replace(sp.dataset(version="c" * 64), manifest=m), False)
    store.declare_protocol(w, sp.declaration(test=("2026-07-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00")),
                           gu.GOOD_PLAN)
    with pytest.raises(store.Refused, match="test_labels"):
        store.start_run(w, ids["protocol"], ids["config"], early, "direction", 5, "code.1")


def test_minor11_a_production_copy_equals_its_source(cluster):
    db = fs.fresh_db(cluster)
    w, ids = mp.world(cluster, db)
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    copy = ("insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff, "
            "evidence_label_class, environment_json, artifact_sha256, environment, promoted_from) values ('linear', "
            "%s, %s, %s, %s, %s, '2025-01-01Z', 'reconstructed', '{}', %s, 'production', %s)")
    # validate the source first, so that only the copy's shape can refuse it
    with o.cursor() as cur:
        cur.execute("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                    "values (%s, 'validation', true, '{}', 1, 'c')", (ids["model"],))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', 'v')",
                    (ids["model"],))
    msg = mp.refused(o, copy, (ids["config"], "return", 5, ids["dataset"], ids["protocol"], ids["artifact"],
                               ids["model"]))
    assert msg and "keeps the artifact, kind" in msg
    o.rollback()


def test_audit_survivors_withheld_periods_cover_and_boundaries(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)                                     # its period: 2024-07-01 .. 2025-06-30
    m = sp.dataset(date_to=date(2024, 7, 1)).manifest                  # touches the period's first day only
    assert len(store.withheld_for(w, m.date_from, m.date_to)) == 1     # M39: a partial overlap counts
    part = replace(m, withheld=((datetime(2024, 7, 2, tzinfo=UTC), datetime(2025, 6, 30, tzinfo=UTC)),))
    with pytest.raises(store.Refused, match="test_labels"):            # M33: withholding must cover, not overlap
        store.check_manifest(w, part)
    dec = sp.declaration(test=("2026-07-01T00:00:00+00:00", "2027-06-30T00:00:00+00:00"))
    other, _s, _r = store.declare_protocol(w, dec, gu.GOOD_PLAN)
    for edge in (datetime(2024, 7, 1, tzinfo=UTC), datetime(2025, 6, 30, tzinfo=UTC)):   # M34: inclusive bounds
        with pytest.raises(store.Refused, match="unspent_interval"):
            store.check_rows_outside_unspent(w, other, [edge])
    assert store.check_rows_outside_unspent(w, other, [datetime(2025, 6, 30, 0, 0, 1, tzinfo=UTC)])


def test_audit_survivors_prospective_period_reached_by_a_dataset_is_previously_scored(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    m = replace(sp.dataset().manifest, date_to=date(2031, 12, 31))
    store.register_dataset(w, replace(sp.dataset(version="e" * 64), manifest=m), False)
    pid, _s, _r = store.declare_protocol(w, sp.declaration(test=("2031-01-01T00:00:00+00:00",
                                                                 "2031-06-30T00:00:00+00:00")), gu.GOOD_PLAN)
    with w.cursor() as cur:
        cur.execute("select timing, previously_scored from ml_test_periods where protocol_id = %s", (pid,))
        assert cur.fetchone() == ("prospective", True)                # M37: an earlier dataset built labels in it
    w.rollback()


def test_audit_survivors_first_events_production_artifact_and_designation(cluster):
    db = fs.fresh_db(cluster)
    w, ids = mp.world(cluster, db)
    js = json.dumps({"kind": "model", "status": "provisional", "adapter": "linear"}, sort_keys=True)
    second = mp.one(w, "insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, "
                       "training_cutoff, evidence_label_class, environment_json, artifact_sha256, environment) values "
                       "('linear', %s, 'direction', 5, %s, %s, '2025-01-01Z', 'reconstructed', '{}', %s, 'research') "
                       "returning model_id", (mp.sha(js), ids["dataset"], ids["protocol"], ids["artifact"]))
    for st in ("validated", "production", "retired", "rejected"):                    # M21: only candidate first
        assert mp.refused(w, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, %s, 'r')",
                          (second, st))
    w.rollback()
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
        cur.execute("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                    "values (%s, 'validation', true, '{}', 1, 'c'), (%s, 'promotion', true, '{}', 1, 'c')",
                    (ids["model"], ids["model"]))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', 'v')",
                    (ids["model"],))
        body = b"another"
        import base64
        import hashlib
        other_art = hashlib.sha256(body).hexdigest()
        cur.execute("insert into ml_artifacts (artifact_sha256, artifact_base64, artifact_size) values (%s, %s, %s)",
                    (other_art, base64.b64encode(body).decode(), len(body)))
    o.commit()
    copy = ("insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff, "
            "evidence_label_class, environment_json, artifact_sha256, environment, promoted_from) values ('linear', "
            "%s, 'direction', 5, %s, %s, '2025-01-01Z', 'reconstructed', '{}', %s, 'production', %s) "
            "returning model_id")
    assert "keeps the artifact" in mp.refused(o, copy, (ids["config"], ids["dataset"], ids["protocol"], other_art,
                                                        ids["model"]))                          # M8
    o.rollback()
    with o.cursor() as cur:                                              # M16: an earlier designation does not do
        cur.execute("set role cse_owner")
        cur.execute("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "('route1', 'direction', 5, '[1000]', '{}', 'an earlier designation')")
    o.commit()
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
        cur.execute(copy, (ids["config"], ids["dataset"], ids["protocol"], ids["artifact"], ids["model"]))
        prod = cur.fetchone()[0]
        cur.execute("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "('route1', 'return', 5, %s, '{}', 'the wrong output for it')", (json.dumps([prod]),))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', 'p')",
                    (prod,))
    with pytest.raises(Exception, match="designation"):                 # the output must match (minor 11)
        o.commit()
    o.rollback()
    assert mp.refused(w, copy, (ids["config"], ids["dataset"], ids["protocol"], ids["artifact"], ids["model"]))
    w.rollback()


def test_audit_survivors_the_designation_cites_the_lifetime_access_count(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    store.validate(w, ids["model"], gu.GOOD, "code.1")
    store.access(w, ids["protocol"], sp.period_of(w, ids["protocol"]),
                 [{"model_id": ids["model"], "predictions_sha256": "d" * 64}], lambda: {"nominees_passed": [ids["model"]]})
    o = fs.conn(cluster, db, user="cse_migrator")
    store.promote(o, ids["model"], replace(gu.GOODP, lifetime_test_accesses=7), purpose="route1", output="direction",
                  horizon=5, list_ids=[ids["model"]], note="the owner promotes it", code_version="code.1")
    with w.cursor() as cur:
        cur.execute("select labels_json from ml_designations")
        assert json.loads(cur.fetchone()[0])["lifetime_test_accesses"] == 7          # M41
    w.rollback()


def test_m4_an_out_of_family_nominee_is_refused_even_if_validated(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    other = store.register_configuration(w, "model", {"adapter": "gbt", "horizon": 5})
    mid = store.register_model(w, kind="gbt", configuration_id=other, output="direction", horizon=5,
                               dataset_id=ids["dataset"], protocol_id=ids["protocol"],
                               training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="reconstructed",
                               artifact=sp.Artifact.make("gbt", {"t": 2}), environment_json={"threads": 1})
    with w.cursor() as cur:            # validated by hand (validate() would refuse it at gate 2): defence in depth
        cur.execute("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                    "values (%s, 'validation', true, '{}', 1, 'c')", (mid,))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', 'v')",
                    (mid,))
    w.commit()
    with pytest.raises(store.Refused, match="family"):
        store.access(w, ids["protocol"], sp.period_of(w, ids["protocol"]),
                     [{"model_id": mid, "predictions_sha256": "d" * 64}], lambda: {"nominees_passed": []})
    assert store.lifetime_accesses(w) == 0
