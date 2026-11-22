"""
The Phase 5 registry amendments carried before `main` (C-0116; Phase 11 re-audit N1 to N3), against migrations
0001 - 0021 in a throwaway cluster:
- ADL-9 / N1: an owner designation is checked in EVERY transaction: a research model listed with no production event
  is refused at commit; an empty list designates nothing and is accepted;
- N3: the unspent-interval check by label window plus grace (a row at P_start - 1 whose label reaches into the
  interval is refused);
- N2: gate 9 by reference for a roll-forward, through its rollforward_of chain to the tested nominee;
- N2: promote_list promotes a whole list, with a component, in one transaction and one designation; if one version
  fails, nothing but the reports is written.
Skipped unless P1_PG_BINDIR is set.
"""
import json
import os
from datetime import datetime, timedelta, timezone

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
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("rega"))
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


def another_model(w, ids, **kw):
    return store.register_model(w, kind="linear", configuration_id=ids["config"], output="direction", horizon=5,
                                dataset_id=ids["dataset"], protocol_id=ids["protocol"],
                                training_cutoff=datetime(2023, 6, 30, tzinfo=UTC), evidence_label_class="point_in_time",
                                artifact=ids["artifact"], environment_json={"threads": 1}, **kw)


def accessed(w, ids, nominees, passed):
    pid = sp.period_of(w, ids["protocol"])
    store.access(w, ids["protocol"], pid, [{"model_id": m, "predictions_sha256": "d" * 64} for m in nominees],
                 lambda: {"nominees_passed": list(passed)})


def test_n1_a_designation_is_checked_in_every_transaction(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    o = owner(cluster, db)
    ins = ("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values ('route1', "
           "'direction', 5, %s, %s, 'an owner designation')")
    for lst, labels in ((json.dumps([ids["model"]]), "{}"),                       # a research model, no promotion
                        ("[]", json.dumps({"components": [ids["model"]]})),       # a research component
                        ('["x"]', "{}")):                                          # not a model id
        with o.cursor() as cur:
            cur.execute("set role cse_owner")
            cur.execute(ins, (lst, labels))
        with pytest.raises(Exception, match="only production models"):
            o.commit()                                                             # refused at commit
        o.rollback()
    with o.cursor() as cur:                                                        # an empty list: nothing designated
        cur.execute("set role cse_owner")
        cur.execute(ins, ("[]", "{}"))
    o.commit()


def test_n3_the_unspent_check_covers_the_label_window(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    p_start = datetime(2024, 7, 1, tzinfo=UTC)                                     # registry_world's test period
    other, _s, _r = store.declare_protocol(w, sp.declaration(
        test=("2026-07-01T00:00:00+00:00", "2027-06-30T00:00:00+00:00")), gu.GOOD_PLAN)
    before = p_start - timedelta(days=1)
    assert store.check_rows_outside_unspent(w, other, [before])                    # its T alone is outside
    with pytest.raises(store.Refused, match="label window"):                       # its label reaches in
        store.check_rows_outside_unspent(w, other, [(before, before + timedelta(days=7))])
    with pytest.raises(store.Refused, match="label window"):                       # the grace reaches in
        store.check_rows_outside_unspent(w, other, [(before - timedelta(days=9), before - timedelta(days=2))],
                                         grace=timedelta(days=3))
    assert store.check_rows_outside_unspent(w, other, [(before - timedelta(days=9), before - timedelta(days=2))])
    assert store.check_rows_outside_unspent(w, ids["protocol"], [(before, before + timedelta(days=7))])  # its own


def test_n2_gate_9_by_reference_for_a_roll_forward(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    assert store.validate(w, ids["model"], gu.GOOD, "code.1")["passed"]
    rf = another_model(w, ids, rollforward_of=ids["model"])
    rf2 = another_model(w, ids, rollforward_of=rf)                                 # a roll-forward of a roll-forward
    o = fs.conn(cluster, db, user="cse_migrator")
    ev = store.promotion_evidence(o, rf2, sp.EVIDENCE)
    assert not ev.access_done and not ev.nominee_passed                            # before the access
    accessed(w, ids, [ids["model"]], [ids["model"]])
    ev = store.promotion_evidence(o, rf2, sp.EVIDENCE)
    assert ev.access_done and ev.nominee_passed and f"gate9_by_reference:{ids['model']}" in ev.model_labels
    assert "rollforward_of" in ev.model_labels
    stray = another_model(w, ids)                                                  # not a roll-forward: no reference
    assert not store.promotion_evidence(o, stray, sp.EVIDENCE).nominee_passed


def test_n2_a_whole_list_is_promoted_in_one_transaction(cluster):
    db = fs.fresh_db(cluster)
    w, ids = sp.registry_world(cluster, db)
    a, b, comp = ids["model"], another_model(w, ids), another_model(w, ids)
    for m in (a, b, comp):
        assert store.validate(w, m, gu.GOOD, "code.1")["passed"]
    accessed(w, ids, [a, b, comp], [a, comp])                                      # b fails the test
    o = fs.conn(cluster, db, user="cse_migrator")
    ev = {m: sp.EVIDENCE for m in (a, b, comp)}
    prod, reports = store.promote_list(o, [a, b], ev, purpose="route1", output="direction", horizon=5,
                                       note="the owner promotes the list", code_version="code.1", components=[comp])
    assert prod is None and not reports[b]["passed"] and reports[a]["passed"]
    assert mp.one(w, "select count(*) from ml_models where environment = 'production'") == 0
    assert mp.one(w, "select count(*) from ml_designations") == 0
    w.rollback()
    prod, reports = store.promote_list(o, [a], ev, purpose="route1", output="direction", horizon=5,
                                       note="the owner promotes the list", code_version="code.1", components=[comp])
    assert set(prod) == {a, comp} and all(r["passed"] for r in reports.values())
    lst, labels = json.loads(mp.one(w, "select list_json from ml_designations")), \
        json.loads(mp.one(w, "select labels_json from ml_designations"))
    w.rollback()
    assert lst == [prod[a]] and labels["components"] == [prod[comp]]               # the component is not designated
    assert store.status(w, prod[a]) == store.status(w, prod[comp]) == "production"
    with pytest.raises(store.Refused, match="twice"):
        store.promote_list(o, [a, a], ev, purpose="route1", output="direction", horizon=5, note="twice over",
                           code_version="code.1")
