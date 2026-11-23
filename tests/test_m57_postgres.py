"""
Phase 5 M5-7 against migrations 0001 - 0021: a Route 1 prediction is written by the worker, append-only, and its
hash reproduces from the stored record; MLK-19 at the database: the designation in force at a cutoff is the latest
recorded at or before it, and one recorded later changes nothing. Skipped unless P1_PG_BINDIR is set.
"""
import hashlib
import json
import os
from datetime import date, timedelta

import pytest

import f64_support as fs
import test_m56_migration_postgres as mp
import test_m56_store_postgres as sp
import test_m57_unit as mu
from worker.ml_route1 import record as rc

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("m57"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def test_p1_a_prediction_is_written_append_only_and_its_hash_reproduces(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    rec = rc.build("S.N0000", date(2026, 1, 2), mu.T0, 5, None, "predicted", **mu.PARTS)
    pid, h = rc.write(w, rec)
    stored = mp.one(w, "select record_json from r1_predictions where prediction_id = %s", (pid,))
    w.commit()
    assert rc.row_hash(json.loads(stored)) == h                       # the stored record reproduces its hash
    assert mp.refused(w, "update r1_predictions set state = 'blocked' where prediction_id = %s", (pid,))
    assert mp.refused(w, "delete from r1_predictions where prediction_id = %s", (pid,))
    w.rollback()
    pid2, h2 = rc.write(w, rec)                                       # a re-run: a new row, the same hash
    assert pid2 != pid and h2 == h


def test_p2_mlk19_the_designation_in_force_at_the_cutoff(cluster):
    db = fs.fresh_db(cluster)
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    o.commit()
    first = mp.one(o, "insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                      "('route1', 'return', 5, '[]', '{}', 'the first designation') returning designation_id")
    o.commit()
    t_between = mp.one(o, "select now()")
    o.commit()
    later = mp.one(o, "insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                      "('route1', 'return', 5, '[]', '{}', 'a later designation') returning designation_id")
    o.commit()
    w = fs.conn(cluster, db, user="cse_worker")
    assert rc.designation_in_force(w, "route1", "return", 5, t_between)[0] == first
    assert rc.designation_in_force(w, "route1", "return", 5, t_between + timedelta(days=1))[0] == later
    assert rc.designation_in_force(w, "route1", "return", 5, t_between - timedelta(days=1)) is None
    assert rc.designation_in_force(w, "route1", "direction", 5, t_between) is None


def test_p3_m6_a_row_is_its_record_and_its_hash(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    rec = rc.build("S.N0000", date(2026, 1, 2), mu.T0, 5, None, "predicted", **mu.PARTS)
    js, h = rc.canonical(rec), rc.row_hash(rec)
    ins = ("insert into r1_predictions (symbol, session, cutoff, horizon, designation_id, state, record_json, "
           "row_hash) values (%s, %s, %s, %s, %s, %s, %s, %s)")
    good = ["S.N0000", date(2026, 1, 2), mu.T0, 5, None, "predicted", js, h]
    for i, bad in ((7, "0" * 64), (6, "{}"), (0, "T.N0000"), (1, date(2026, 1, 3)),
                   (2, mu.T0 + timedelta(seconds=1)), (3, 10), (5, "blocked")):
        args = list(good)
        args[i] = bad
        if i == 6:
            args[7] = hashlib.sha256(bad.encode()).hexdigest()          # its own hash: the columns still differ
        assert mp.refused(w, ins, tuple(args)), (i, bad)
        w.rollback()
    with w.cursor() as cur:
        cur.execute(ins, tuple(good))
    w.commit()
    # a designation for another horizon
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    o.commit()
    d21 = mp.one(o, "insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "('route1', 'return', 21, '[]', '{}', 'a designation for h 21') returning designation_id")
    o.commit()
    rec21 = rc.build("S.N0000", date(2026, 1, 2), mu.T0, 5, d21, "predicted", **mu.PARTS)
    assert mp.refused(w, ins, ("S.N0000", date(2026, 1, 2), mu.T0, 5, d21, "predicted", rc.canonical(rec21),
                               rc.row_hash(rec21)))
    w.rollback()
