"""
DE-4 (docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md §10.1): pending migration 0020 applied on top of 0001 - 0019 in a
throwaway cluster. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import hashlib
import json
import os

import pytest

import f64_support as fs

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")
TABLES = ["f7_configurations", "de_configurations", "f7_designations", "de_designations", "f7_panels",
          "f7_feature_rows", "de_runs", "de_outputs"]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("de4"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def owner(cluster, db):
    c = fs.conn(cluster, db, user="cse_migrator")
    with c.cursor() as cur:
        cur.execute("set role cse_owner")
    return c


def config(status="provisional"):
    js = json.dumps({"registry": "f7.1", "status": status}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(js.encode()).hexdigest(), js, status


def refused(c, sql, args=()):
    with c.cursor() as cur:
        cur.execute("savepoint s")
        try:
            cur.execute(sql, args)
        except Exception as exc:                       # noqa: BLE001
            cur.execute("rollback to savepoint s")
            return type(exc).__name__
        cur.execute("release savepoint s")
    return None


def test_d1_0020_applies_after_0019_and_stays_pending(cluster):
    db = fs.fresh_db(cluster)
    c = fs.conn(cluster, db, user="postgres")
    with c.cursor() as cur:
        cur.execute("select version from ops.schema_migrations order by version")
        assert [r[0] for r in cur.fetchall()][-2:] == [19, 20]
        cur.execute("select c.relname, count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid "
                    "join pg_proc p on p.oid = t.tgfoid where p.proname = 'f5_reject_mutation' "
                    "and c.relname = any(%s) group by 1", (TABLES,))
        assert dict(cur.fetchall()) == {t: 2 for t in TABLES}
    assert not any(n.startswith("0020") for n in os.listdir(fs.MIGDIR))


def test_d2_configuration_guard_and_append_only(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    cid, js, st = config()
    ins = "insert into f7_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)"
    assert refused(w, ins, ("0" * 64, js, st))                       # id is not the hash
    assert refused(w, ins, (cid, js, "validated"))                   # status differs from the JSON
    assert refused(w, ins, (cid, js, st)) is None
    assert refused(w, "insert into de_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                   (cid, js, st)) is None
    assert refused(w, "update f7_configurations set status = status")
    w.commit()
    o = owner(cluster, db)
    assert refused(o, "delete from f7_configurations")
    assert refused(o, "truncate de_outputs")


def test_d3_designations_owner_only(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    cid, js, st = config()
    with w.cursor() as cur:
        cur.execute("insert into f7_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
    w.commit()
    ins = "insert into f7_designations (purpose, configuration_id, note) values ('canonical', %s, 'owner chose this')"
    assert refused(w, ins, (cid,))                                    # no privilege
    o = owner(cluster, db)
    assert refused(o, ins, (cid,)) is None
    assert refused(o, ins.replace("'owner chose this'", "'short'"), (cid,))      # the note's minimum
    # an owner-privileged login without the role switch is refused by the guard, not only the grant
    m = fs.conn(cluster, db, user="cse_migrator")
    assert refused(m, ins, (cid,))


def test_d4_panel_and_run_checks(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    cid, js, st = config()
    with w.cursor() as cur:
        cur.execute("insert into f7_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
    panel = ("insert into f7_panels (issuer_id, mode, result_label, cutoff, knowledge_horizon, f7_configuration_id, "
             "f8_configuration_id, f8_result_hash, state, panel_hash) values ('I1', %s, %s, now(), %s, %s, 'f8', "
             "repeat('a',64), 'ok', repeat('b',64))")
    assert refused(w, panel, ("KNOWN", "known", None, cid)) is None
    assert refused(w, panel, ("KNOWN", "reconstructed", None, cid))  # label must match the mode
    assert refused(w, panel, ("AVAILABLE", "reconstructed", None, cid))   # AVAILABLE needs H
    assert refused(w, panel, ("KNOWN", "known", "2024-01-01", cid))  # KNOWN has no H
    assert refused(w, panel, ("AVAILABLE", "reconstructed", "2024-01-01", cid)) is None
