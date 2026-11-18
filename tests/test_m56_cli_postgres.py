"""
Phase 5 M5-6: the registry's command line against migrations 0001 - 0021. The worker preflight holds and sees a
disabled guard; owner acts only on the owner path; `promote` takes the access, its result, the labels and the
access count from the registry (the evidence file may state only the backtest and the point-in-time share), and a
promotion that fails a gate writes only its report; `transition` and `alerts`; `verify` re-proves the hashes and
finds a tampered declaration. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import json
import os
from datetime import datetime, timezone

import pytest

import f64_support as fs
import test_m56_gates_unit as gu
import test_m56_store_postgres as sp
from worker.ml_models.artifact import Artifact
from worker.ml_registry import cli, store

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
UTC = timezone.utc
EVIDENCE = {"backtest_valid": True, "backtest_ends_at_last_v_end_or_post_access": True, "point_in_time_share": 0.6}


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m56c"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(sp.PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def setup(cluster, db, share=0.5):
    w = fs.conn(cluster, db, user="cse_worker")
    cid = store.register_configuration(w, "model", {"adapter": "linear", "horizon": 5})
    did = store.register_dataset(w, sp.dataset(), False)
    decl = {**sp.declaration(), **({"point_in_time_share": share} if share is not None else {})}
    pid, state, _ = store.declare_protocol(w, decl, gu.GOOD_PLAN)
    assert state == "sufficient"
    art = Artifact.make("linear", {"w": [1.0, 2.0]})
    mid = store.register_model(w, kind="linear", configuration_id=cid, output="direction", horizon=5,
                               dataset_id=did, protocol_id=pid, training_cutoff=datetime(2023, 6, 30, tzinfo=UTC),
                               evidence_label_class="reconstructed", artifact=art, environment_json={"threads": 1})
    assert store.validate(w, mid, gu.GOOD, "code.1")["passed"]
    return w, pid, mid


def call(cluster, db, user, *argv):
    return cli.run(cli.parser().parse_args(list(argv)), lambda: fs.conn(cluster, db, user=user))


def out(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def evidence_file(tmp_path, obj):
    p = tmp_path / "evidence.json"
    p.write_text(json.dumps(obj))
    return str(p)


def test_c1_preflight_holds_and_sees_a_disabled_guard(cluster, capsys):
    db = fs.fresh_db(cluster)
    assert call(cluster, db, "cse_worker", "preflight") == cli.EXIT_OK and out(capsys)["problems"] == []
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table ml_models disable trigger trg_mlm_guard")
    su.commit()
    assert call(cluster, db, "cse_worker", "preflight") == cli.EXIT_REFUSED
    assert any("trg_mlm_guard" in p for p in out(capsys)["problems"])
    assert call(cluster, db, "cse_worker", "transition", "--model", "1", "--to", "retired",
                "--reason", "x") == cli.EXIT_REFUSED and out(capsys)["reason"] == "preflight"


def test_c2_owner_acts_only_on_the_owner_path(cluster, capsys):
    db = fs.fresh_db(cluster)
    args = ("waive", "--kind", "survivorship", "--covers", '{"datasets": ["v1"]}', "--note", "accepted for v1 only")
    assert call(cluster, db, "cse_worker", *args) == cli.EXIT_REFUSED and "owner step" in out(capsys)["reason"]
    assert call(cluster, db, "cse_migrator", *args) == cli.EXIT_OK and out(capsys)["id"] == 1
    assert call(cluster, db, "cse_migrator", "waive", "--kind", "survivorship", "--covers", "[]",
                "--note", "an empty cover") == cli.EXIT_REFUSED
    flag = ("flag", "--from", "2026-01-01T00:00:00+00:00", "--to", "2026-01-05T00:00:00+00:00", "--note",
            "a feed incident")
    assert call(cluster, db, "cse_worker", *flag) == cli.EXIT_REFUSED
    assert call(cluster, db, "cse_migrator", *flag) == cli.EXIT_OK
    with fs.conn(cluster, db, user="cse_worker").cursor() as cur:
        cur.execute("select approved_by, note from ml_waivers")
        who, note = cur.fetchone()
    assert who == "cse_migrator" and note == "accepted for v1 only"


def test_c3_promotion_evidence_comes_from_the_registry(cluster, capsys, tmp_path):
    db = fs.fresh_db(cluster)
    w, pid, mid = setup(cluster, db)
    base = ("promote", "--model", str(mid), "--purpose", "route1", "--list", str(mid), "--note",
            "the owner promotes it", "--code-version", "code.1", "--evidence")
    # no access yet: gate 9 fails, only the report is written
    assert call(cluster, db, "cse_migrator", *base, evidence_file(tmp_path, EVIDENCE)) == cli.EXIT_REFUSED
    r = out(capsys)
    assert r["production_model_id"] is None and not r["report"]["gates"]["9"]["pass"]
    # the file cannot claim what the registry knows
    for extra in ({"access_done": True}, {"nominee_passed": True}, {"lifetime_test_accesses": 0},
                  {"model_labels": []}):
        assert call(cluster, db, "cse_migrator", *base, evidence_file(tmp_path, {**EVIDENCE, **extra})) == \
            cli.EXIT_REFUSED and "unknown" in out(capsys)["reason"]
    assert call(cluster, db, "cse_migrator", *base, evidence_file(tmp_path, {"backtest_valid": True})) == \
        cli.EXIT_REFUSED and "missing" in out(capsys)["reason"]
    period = sp.period_of(w, pid)
    store.access(w, pid, period, [{"model_id": mid, "predictions_sha256": "d" * 64}],
                 lambda: {"nominees_passed": [mid]})
    assert call(cluster, db, "cse_worker", *base, evidence_file(tmp_path, EVIDENCE)) == cli.EXIT_REFUSED
    assert "owner step" in out(capsys)["reason"]
    assert call(cluster, db, "cse_migrator", *base, evidence_file(tmp_path, EVIDENCE)) == cli.EXIT_OK
    r = out(capsys)
    prod = r["production_model_id"]
    assert prod and store.status(w, prod) == "production"
    assert r["report"]["labels"] == ["previously_scored", "retrospective"]      # the 2024 period, declared later
    with w.cursor() as cur:
        cur.execute("select list_json, labels_json, note from ml_designations")
        lst, labels, note = cur.fetchone()
    w.rollback()
    assert json.loads(lst) == [prod] and json.loads(labels)["lifetime_test_accesses"] == 1
    assert note.startswith("the owner promotes it")


def test_c4_a_nominee_that_did_not_pass_and_an_undeclared_share_are_not_promoted(cluster, capsys, tmp_path):
    db = fs.fresh_db(cluster)
    w, pid, mid = setup(cluster, db)
    store.access(w, pid, sp.period_of(w, pid), [{"model_id": mid, "predictions_sha256": "d" * 64}],
                 lambda: {"nominees_passed": []})
    assert call(cluster, db, "cse_migrator", "promote", "--model", str(mid), "--purpose", "route1", "--list",
                str(mid), "--note", "the owner tries it", "--code-version", "code.1", "--evidence",
                evidence_file(tmp_path, EVIDENCE)) == cli.EXIT_REFUSED
    r = out(capsys)
    assert r["report"]["gates"]["9"]["reason"] == "the nominee did not pass the test criterion"
    db2 = fs.fresh_db(cluster)
    _w2, _pid2, mid2 = setup(cluster, db2, share=None)
    o = fs.conn(cluster, db2, user="cse_migrator")
    with pytest.raises(store.Refused, match="point_in_time_share"):
        store.promotion_evidence(o, mid2, EVIDENCE)
    with pytest.raises(store.Refused, match="waiver"):
        store.promotion_evidence(fs.conn(cluster, db, user="cse_migrator"), mid,
                                 {**EVIDENCE, "reconstructed_waiver": 99})


def test_c5_transition_alerts_status_and_verify(cluster, capsys):
    db = fs.fresh_db(cluster)
    w, pid, mid = setup(cluster, db)
    assert call(cluster, db, "cse_worker", "status") == cli.EXIT_OK
    s = out(capsys)
    assert s["ml_models"] == 1 and s["models"][0]["status"] == "validated" and len(s["unspent_test_periods"]) == 1
    assert call(cluster, db, "cse_worker", "verify") == cli.EXIT_OK and out(capsys)["ok"]
    assert call(cluster, db, "cse_worker", "transition", "--model", str(mid), "--to", "retired",
                "--reason", "superseded") == cli.EXIT_OK
    assert store.status(w, mid) == "retired"
    assert call(cluster, db, "cse_worker", "alerts") == cli.EXIT_OK and out(capsys)["alerts"] == []
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table ml_protocols disable trigger trg_ml_protocols_append_only")
        cur.execute("update ml_protocols set declaration_json = replace(declaration_json, 'best', 'worst')")
    su.commit()
    assert call(cluster, db, "cse_worker", "verify") == cli.EXIT_REFUSED
    assert any("declaration hash" in p for p in out(capsys)["problems"])


def test_c6_the_access_names_this_model_the_share_is_the_protocols_the_labels_are_the_reports(cluster, capsys,
                                                                                             tmp_path):
    from dataclasses import replace
    db = fs.fresh_db(cluster)
    w, pid, mid = setup(cluster, db, share=0.7)
    other = store.register_model(w, kind="linear", configuration_id=store.register_configuration(
        w, "model", {"adapter": "linear", "horizon": 5}), output="direction", horizon=5,
        dataset_id=store.register_dataset(w, sp.dataset(), False), protocol_id=pid,
        training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="reconstructed",
        artifact=Artifact.make("linear", {"w": [3.0]}), environment_json={"threads": 1})
    assert store.validate(w, other, gu.GOOD, "code.1")["passed"]
    store.access(w, pid, sp.period_of(w, pid), [{"model_id": other, "predictions_sha256": "e" * 64}],
                 lambda: {"nominees_passed": [other]})
    o = fs.conn(cluster, db, user="cse_migrator")
    e = store.promotion_evidence(o, mid, EVIDENCE)
    assert not e.access_done and not e.nominee_passed                   # the access named another model
    assert e.declared_point_in_time_share == 0.7                        # the protocol's, not the file's
    with pytest.raises(store.Refused, match="owner_path"):
        store.promotion_evidence(w, mid, EVIDENCE)
    db2 = fs.fresh_db(cluster)
    w2, pid2, _ = setup(cluster, db2)
    m2 = store.register_model(w2, kind="linear", configuration_id=store.register_configuration(
        w2, "model", {"adapter": "linear", "horizon": 5}), output="direction", horizon=5,
        dataset_id=store.register_dataset(w2, sp.dataset(), False), protocol_id=pid2,
        training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="reconstructed",
        artifact=Artifact.make("linear", {"w": [4.0]}), environment_json={"threads": 1})
    assert store.validate(w2, m2, replace(gu.GOOD, no_engine_component=True), "code.1")["passed"]
    e2 = store.promotion_evidence(fs.conn(cluster, db2, user="cse_migrator"), m2, EVIDENCE)
    assert "no_engine_component" in e2.model_labels                     # carried from the validation report


def test_c7_retiring_production_is_an_owner_command(cluster, capsys, tmp_path):
    db = fs.fresh_db(cluster)
    w, pid, mid = setup(cluster, db)
    store.access(w, pid, sp.period_of(w, pid), [{"model_id": mid, "predictions_sha256": "d" * 64}],
                 lambda: {"nominees_passed": [mid]})
    assert call(cluster, db, "cse_migrator", "promote", "--model", str(mid), "--purpose", "route1", "--list",
                str(mid), "--note", "the owner promotes it", "--code-version", "code.1", "--evidence",
                evidence_file(tmp_path, EVIDENCE)) == cli.EXIT_OK
    prod = out(capsys)["production_model_id"]
    assert call(cluster, db, "cse_worker", "transition", "--model", str(prod), "--to", "retired", "--reason",
                "the worker tries") == cli.EXIT_REFUSED and "owner" in out(capsys)["reason"]
    assert call(cluster, db, "cse_worker", "retire", "--model", str(prod), "--reason", "x") == cli.EXIT_REFUSED
    assert call(cluster, db, "cse_migrator", "retire", "--model", str(prod), "--reason", "superseded") == cli.EXIT_OK
    assert store.status(w, prod) == "retired"


def test_c8_validate_policy_is_an_owner_command(cluster, capsys):
    db = fs.fresh_db(cluster)
    args = ("validate-policy", "--body", '{"name": "eligibility.1", "min_turnover": "1000"}', "--evidence-cutoff",
            "2017-01-01T00:00:00+00:00")
    assert call(cluster, db, "cse_worker", *args) == cli.EXIT_REFUSED
    assert call(cluster, db, "cse_migrator", *args) == cli.EXIT_OK
    cid = out(capsys)["configuration_id"]
    with fs.conn(cluster, db, user="cse_worker").cursor() as cur:
        cur.execute("select status, kind, registered_by from ml_configurations where configuration_id = %s", (cid,))
        assert cur.fetchone() == ("validated", "policy", "cse_migrator")
