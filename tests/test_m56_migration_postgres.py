"""
Phase 5 M5-6: pending migration 0021 (the ML registry) applied on top of 0001 - 0020 in a throwaway cluster, and
every rule the database enforces (design §9.3's left column, §5.4): legal first events and transitions; `validated`
only with a passing gate report and a non-synthetic dataset; a protocol declared before its first run; disjoint test
periods and one access each; production only through the owner path with a validated source, the same artifact, a
passing promotion report and a designation in the same transaction; owner-only decisions; append-only for every
role. MLK-12 and the database half of MLK-4 to MLK-6. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import base64
import hashlib
import json
import os

import pytest

import f64_support as fs

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")
TABLES = ["ml_configurations", "ml_datasets", "ml_protocols", "ml_test_periods", "ml_runs", "ml_run_events",
          "ml_trials", "ml_artifacts", "ml_fold_models", "ml_models", "ml_model_metrics", "ml_gate_reports",
          "ml_test_accesses", "ml_model_events", "ml_waivers", "ml_incident_flags", "ml_designations",
          "r1_predictions"]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m56"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def refused(c, sql, args=()):
    with c.cursor() as cur:
        cur.execute("savepoint s")
        try:
            cur.execute(sql, args)
        except Exception as exc:                       # noqa: BLE001
            cur.execute("rollback to savepoint s")
            return type(exc).__name__ + ": " + str(exc).split("\n")[0]
        cur.execute("release savepoint s")
    return None


def one(c, sql, args=()):
    with c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0] if cur.description else None


def owner(cluster, db):
    c = fs.conn(cluster, db, user="cse_migrator")
    one(c, "set role cse_owner")
    return c


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def world(cluster, db, synthetic=False):
    """A configuration, a dataset, a protocol (committed earlier), an artifact and a research model with its first
    event. Returns the worker connection and the ids."""
    w = fs.conn(cluster, db, user="cse_worker")
    js = json.dumps({"kind": "model", "status": "provisional", "adapter": "linear"}, sort_keys=True)
    one(w, "insert into ml_configurations (configuration_id, configuration_json, kind, status) values (%s,%s,'model',"
           "'provisional')", (sha(js), js))
    ds = one(w, "insert into ml_datasets (version, manifest_json, report_json, label_class, survivorship, synthetic) "
                "values (%s, '{}', '{}', 'reconstructed', 'complete', %s) returning dataset_id", ("a" * 64, synthetic))
    dec = json.dumps({"family": ["x"]})
    pr = one(w, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values (%s, %s, "
                "'sufficient') returning protocol_id", (dec, sha(dec)))
    body = b"model text"
    art = hashlib.sha256(body).hexdigest()
    one(w, "insert into ml_artifacts (artifact_sha256, artifact_base64, artifact_size) values (%s, %s, %s)",
        (art, base64.b64encode(body).decode(), len(body)))
    w.commit()
    model = one(w, "insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, "
                   "training_cutoff, evidence_label_class, environment_json, artifact_sha256, environment) values "
                   "('linear', %s, 'direction', 5, %s, %s, '2025-01-01Z', 'reconstructed', '{}', %s, 'research') "
                   "returning model_id", (sha(js), ds, pr, art))
    one(w, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'candidate', 'registered')",
        (model,))
    w.commit()
    return w, dict(config=sha(js), dataset=ds, protocol=pr, artifact=art, model=model)


def test_g1_applies_after_0020_append_only_everywhere(cluster):
    db = fs.fresh_db(cluster)
    c = fs.conn(cluster, db, user="postgres")
    assert [r for r in (one(c, "select max(version) from ops.schema_migrations"),)] == [21]
    with c.cursor() as cur:
        cur.execute("select c.relname, count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_proc p "
                    "on p.oid = t.tgfoid where p.proname = 'f5_reject_mutation' and c.relname = any(%s) group by 1",
                    (TABLES,))
        assert dict(cur.fetchall()) == {t: 2 for t in TABLES}
    assert not any(n.startswith("0021") for n in os.listdir(fs.MIGDIR))
    w, ids = world(cluster, db)
    o = owner(cluster, db)
    for t in ("ml_models", "ml_model_events", "ml_artifacts", "ml_configurations"):
        assert refused(o, f"delete from {t}") and refused(w, f"update {t} set recorded_at = now()")


def test_g2_configurations_protocols_artifacts_are_what_they_say(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    js = json.dumps({"kind": "policy", "status": "provisional"})
    ins = "insert into ml_configurations (configuration_id, configuration_json, kind, status) values (%s,%s,%s,%s)"
    assert refused(w, ins, ("0" * 64, js, "policy", "provisional"))
    assert refused(w, ins, (sha(js), js, "model", "provisional"))
    assert refused(w, ins, (sha(js), js, "policy", "validated"))
    assert refused(w, ins, (sha(js), js, "policy", "provisional")) is None
    assert refused(w, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values ('{}', %s, "
                      "'sufficient')", ("1" * 64,))
    body = b"abc"
    good = hashlib.sha256(body).hexdigest()
    art = "insert into ml_artifacts (artifact_sha256, artifact_base64, artifact_size) values (%s, %s, %s)"
    assert refused(w, art, ("0" * 64, base64.b64encode(body).decode(), 3))          # MLK-16: bytes vs hash
    assert refused(w, art, (good, base64.b64encode(body).decode(), 4))
    assert refused(w, art, (good, base64.b64encode(body).decode(), 3)) is None


def test_g3_test_periods_disjoint_timed_by_the_clock_and_accessed_once(cluster):
    db = fs.fresh_db(cluster)
    w, ids = world(cluster, db)
    tp = "insert into ml_test_periods (protocol_id, period, timing, previously_scored) values (%s, %s, %s, true)"
    assert "own transaction" in refused(w, tp, (ids["protocol"], "[2030-01-01Z,2030-06-30Z]", "prospective"))
    w.rollback()                                       # audit M1: never after the protocol's transaction (its runs)
    proto = "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values (%s, %s, " \
            "'exploratory') returning protocol_id"
    dec, dec2 = json.dumps({"family": ["y"]}), json.dumps({"family": ["y2"]})
    pr = one(w, proto, (dec, sha(dec)))
    assert refused(w, tp, (pr, "[2024-01-01Z,2024-06-30Z]", "retrospective")) is None
    assert refused(w, tp, (pr, "[2023-01-01Z,2023-06-30Z]", "prospective"))            # the clock says retrospective
    assert refused(w, tp, (pr, "[2022-01-01Z,)", "retrospective"))                     # unbounded
    w.commit()
    other = one(w, proto, (dec2, sha(dec2)))
    assert refused(w, tp, (other, "[2024-06-01Z,2024-12-31Z]", "retrospective"))      # overlaps another protocol's
    assert refused(w, tp, (other, "[2099-01-01Z,2099-06-30Z]", "prospective")) is None
    w.commit()
    pid = one(w, "select period_id from ml_test_periods where protocol_id = %s", (pr,))
    acc = ("insert into ml_test_accesses (period_id, protocol_id, nominees_json, predictions_sha256, result_json) "
           "values (%s, %s, '[]', repeat('b', 64), '{}')")
    assert refused(w, acc, (pid, other))                                               # not this protocol's period
    assert refused(w, acc, (pid, pr)) is None
    assert refused(w, acc, (pid, pr))                                                  # one access


def test_g4_a_protocol_precedes_its_runs(cluster):
    db = fs.fresh_db(cluster)
    w, ids = world(cluster, db)
    dec = json.dumps({"family": ["z"]})
    run = ("insert into ml_runs (protocol_id, configuration_id, dataset_id, output, horizon, code_version) values "
           "(%s, %s, %s, 'direction', 5, 'c')")
    pr = one(w, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values (%s, %s, "
                "'sufficient') returning protocol_id", (dec, sha(dec)))
    assert refused(w, run, (pr, ids["config"], ids["dataset"]))                        # same transaction
    w.rollback()
    assert refused(w, run, (ids["protocol"], ids["config"], ids["dataset"])) is None    # declared earlier


def test_g5_events_follow_the_lifecycle_and_validated_needs_its_evidence(cluster):
    db = fs.fresh_db(cluster)
    w, ids = world(cluster, db)
    m = ids["model"]
    ev = "insert into ml_model_events (model_id, seq, status, reason) values (%s, %s, %s, 'r')"
    assert refused(w, ev, (m, 3, "rejected"))                                          # out of sequence
    assert refused(w, ev, (m, 2, "retired"))                                           # candidate -> retired
    assert refused(w, ev, (m, 2, "production"))
    assert "validation gate report" in refused(w, ev, (m, 2, "validated"))
    gr = ("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) values "
          "(%s, %s, %s, '{}', 1, 'c')")
    assert refused(w, gr, (m, "validation", False)) is None
    assert "validation gate report" in refused(w, ev, (m, 2, "validated"))             # a failing report is none
    assert refused(w, gr, (m, "validation", True)) is None
    assert refused(w, ev, (m, 2, "validated")) is None
    assert refused(w, ev, (m, 3, "candidate"))                                         # no way back
    assert refused(w, ev, (m, 3, "retired")) is None
    w.commit()
    db2 = fs.fresh_db(cluster)
    w2, ids2 = world(cluster, db2, synthetic=True)
    one(w2, gr, (ids2["model"], "validation", True))
    assert "synthetic" in refused(w2, ev, (ids2["model"], 2, "validated"))            # gate 1


def promote_setup(cluster, db):
    w, ids = world(cluster, db)
    m = ids["model"]
    one(w, "insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) values "
           "(%s, 'validation', true, '{}', 1, 'c')", (m,))
    one(w, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', 'gates 1-8')",
        (m,))
    w.commit()
    assert "owner path" in refused(w, "insert into ml_gate_reports (model_id, kind, passed, checks_json, "
                                      "multiplicity, code_version) values (%s, 'promotion', true, '{}', 1, 'c')", (m,))
    w.rollback()                                       # M5-6 re-audit: a promotion report is the owner's
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                    "values (%s, 'promotion', true, '{}', 1, 'c')", (m,))
    o.commit()
    return w, ids


PROD = ("insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff, "
        "evidence_label_class, environment_json, artifact_sha256, environment, promoted_from) values ('linear', %s, "
        "'direction', 5, %s, %s, '2025-01-01Z', 'reconstructed', '{}', %s, 'production', %s) returning model_id")


def test_g6_production_only_through_the_owner_path_with_its_designation(cluster):
    db = fs.fresh_db(cluster)
    w, ids = promote_setup(cluster, db)
    args = (ids["config"], ids["dataset"], ids["protocol"], ids["artifact"], ids["model"])
    assert "owner decision" in refused(w, PROD, args)                                   # not the worker
    m = fs.conn(cluster, db, user="cse_migrator")
    assert refused(m, PROD, args)                                                       # not without the role
    o = owner(cluster, db)
    prod = one(o, PROD, args)
    one(o, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', 'promoted')",
        (prod,))
    with pytest.raises(Exception, match="no designation"):                              # checked at commit
        o.commit()
    o.rollback()
    o = owner(cluster, db)
    prod = one(o, PROD, args)
    one(o, "insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values ('route1', "
           "'direction', 5, %s, '[]', 'the owner designates it')", (json.dumps([prod]),))
    one(o, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', 'promoted')",
        (prod,))
    o.commit()
    o = owner(cluster, db)
    assert "already promoted" in refused(o, PROD, args)                                 # once per version
    body = b"another"
    one(o, "insert into ml_artifacts (artifact_sha256, artifact_base64, artifact_size) values (%s, %s, %s)",
        (hashlib.sha256(body).hexdigest(), base64.b64encode(body).decode(), len(body)))
    o.rollback()


def test_g7_production_needs_a_validated_source_and_its_promotion_report(cluster):
    db = fs.fresh_db(cluster)
    w, ids = world(cluster, db)                                                        # still a candidate
    o = owner(cluster, db)
    args = (ids["config"], ids["dataset"], ids["protocol"], ids["artifact"], ids["model"])
    assert "not a validated version" in refused(o, PROD, args)
    o.rollback()
    one(w, "insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) values "
           "(%s, 'validation', true, '{}', 1, 'c')", (ids["model"],))
    one(w, "insert into ml_model_events (model_id, seq, status, reason) values (%s, 2, 'validated', 'g')",
        (ids["model"],))
    w.commit()
    o = owner(cluster, db)
    prod = one(o, PROD, args)
    assert "promotion gate report" in refused(o, "insert into ml_model_events (model_id, seq, status, reason) values "
                                                 "(%s, 1, 'production', 'p')", (prod,))
    o.rollback()


def test_g8_owner_decisions_are_owner_only(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    m = fs.conn(cluster, db, user="cse_migrator")
    o = owner(cluster, db)
    cases = {"ml_waivers": "insert into ml_waivers (kind, covers_json, note) values ('survivorship', '{}', "
                           "'the owner accepts it')",
             "ml_incident_flags": "insert into ml_incident_flags (covers, note) values ('[2024-01-01Z,2024-01-02Z]', "
                                  "'an exchange outage')",
             "ml_designations": "insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) "
                                "values ('route1', 'return', 5, '[]', '[]', 'the owner designates none')"}
    for t, sql in cases.items():
        assert refused(w, sql), t
        assert refused(m, sql), t                                           # without the role: refused too
        assert refused(o, sql) is None, t
