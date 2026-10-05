"""
F8 on the REAL-DATA VALIDATION evidence (docs/F8_DESIGN.md §18 item 2, §19 F8-1: "the availability rules can use the
RDV corpus (TILE's date-only time, missing path epochs)"). OPTIONAL: skipped unless CSE_F6_CORPUS_DIR,
CSE_F0_CAPTURE_DIR and P1_PG_BINDIR are set (Linux). The evidence is not in Git. These tests never contact CSE: run
them with `docker run --network none`.

The pinned real evidence is replayed exactly as tests/test_rdv_postgres.py replays it:
- through the frozen F1 / P2 / F5 / F3 stores;
- then F6.4's own jobs.
F8 is then configured through its owner path, and read as cse_reader.

The real CSE listing entries are the F1 observations. Their upload instants range from 2019 to 2026, while every row
is recorded at replay time. That is exactly the late-discovery situation of §9: nothing is KNOWN before the replay,
and AVAILABLE reconstructs from CSE's own instants.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rdv_evidence as E  # noqa: E402
from worker.financial_asof import api, store  # noqa: E402
from worker.financial_asof.query import AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED  # noqa: E402
from worker.financial_asof.times import colombo_end_of_day  # noqa: E402

BUNDLE = E.locate()
BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(BUNDLE is None or not BINDIR or os.name != "posix",
                                reason=f"{E.CORPUS_ENV}, {E.CAPTURE_ENV} (the evidence is not in the repository) and "
                                       f"P1_PG_BINDIR (PostgreSQL 17, Linux) are not all set")
UTC = timezone.utc
TILE = 32216


def q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.rollback()
    return rows


@pytest.fixture(scope="module")
def run():
    base = tempfile.mkdtemp(prefix="rdv", dir="/tmp")              # short: Unix socket paths are limited
    cluster = S.start_cluster(BINDIR, base)
    try:
        built = E.build_database(cluster, BUNDLE)
        db = built["database"]
        w = S.conn(cluster, db, "cse_worker")
        cfg = q(w, "select configuration_id from financial_reconciliation_designated")[0][0]
        _, f8_id = store.register_configuration(w, cfg)
        m = S.conn(cluster, db, "cse_migrator")
        store.designate(m, f8_id, "real-data validation: the F8 configuration over the rdv.1 F6 configuration", "rdv")
        m.close()
        replayed = q(w, "select min(recorded_at), max(recorded_at) from financial_extraction_runs")[0]
        reader = E.reader_conn(cluster, db)
        yield {"cluster": cluster, "db": db, "w": w, "reader": reader, "f8_id": f8_id, "replayed": replayed,
               "issuers": [str(r[0]) for r in q(w, "select distinct issuer_id from financial_economic_facts "
                                                   "order by 1")]}
        reader.close()
        w.close()
    finally:
        cluster.cleanup()
        shutil.rmtree(base, ignore_errors=True)


def test_every_mode_on_the_real_evidence(run):
    reader, (first, last) = run["reader"], run["replayed"]
    now = q(reader, "select now()")[0][0]
    assert run["issuers"], "the replay produced no economic facts"
    summary = {}
    for issuer in run["issuers"]:
        before = api.as_of(reader, issuer_id=issuer, mode=KNOWN, information_cutoff=first - timedelta(microseconds=1),
                           f8_configuration_id=run["f8_id"])
        assert before.facts == ()                                   # nothing was known before the replay
        results = {
            KNOWN: api.as_of(reader, issuer_id=issuer, mode=KNOWN, information_cutoff=now),
            KNOWN_RECORDED: api.as_of(reader, issuer_id=issuer, mode=KNOWN_RECORDED, information_cutoff=now),
            AVAILABLE: api.as_of(reader, issuer_id=issuer, mode=AVAILABLE,
                                 information_cutoff=colombo_end_of_day(date(2025, 12, 31)), knowledge_horizon=now),
            CURRENT: api.as_of(reader, issuer_id=issuer, mode=CURRENT, knowledge_horizon=now),
        }
        for mode, r in results.items():
            assert r.verify()
            again = api.as_of(reader, issuer_id=issuer, mode=mode,
                              information_cutoff=None if mode == CURRENT else datetime.fromisoformat(
                                  r.information_cutoff),
                              knowledge_horizon=None if mode in (KNOWN, KNOWN_RECORDED) else now)
            assert again.result_hash == r.result_hash                # deterministic replay (settled horizon)
            for f in r.facts:
                for o in f.visible:
                    if mode in (KNOWN, KNOWN_RECORDED):              # I-1
                        assert datetime.fromisoformat(o.knowledge.known_at) <= now
                    if mode == AVAILABLE:                            # I-2
                        assert o.availability.available_at is not None
                        assert datetime.fromisoformat(o.availability.available_at) <= colombo_end_of_day(
                            date(2025, 12, 31))
            summary[(issuer[:8], mode)] = (len(r.facts), sorted({f.state for f in r.facts}))
    print("F8 over the RDV evidence:", summary)


def test_tile_date_only_upload_counts_as_the_end_of_its_colombo_day(run):
    reader = run["reader"]
    rows = q(reader, "select distinct document_sha256 from financial_extraction_runs where cse_filing_id = %s",
             (TILE,))
    assert rows, "TILE 32216 is part of the pinned corpus"
    v = api.availability(reader, TILE, rows[0][0], horizon=q(reader, "select now()")[0][0])
    assert v.at == colombo_end_of_day(date(2019, 2, 7)) and v.precision == "day" and v.role == "base"
    assert v.last_modified is not None and v.last_modified < v.at              # Last-Modified 16:54 the same day
    assert "last_modified_after_upload" not in v.flags


def test_every_real_version_takes_its_availability_from_cse_instants_only(run):
    reader = run["reader"]
    now = q(reader, "select now()")[0][0]
    versions = q(reader, "select distinct cse_filing_id, document_sha256 from financial_extraction_runs order by 1, 2")
    system_times = {t for (t,) in q(reader, "select observed_at from report_filing_observations union "
                                            "select recorded_at from financial_extraction_runs union "
                                            "select document_retrieved_at from financial_extraction_runs "
                                            "where document_retrieved_at is not null")}
    days = 0
    for filing, sha in versions:
        v = api.availability(reader, filing, sha, horizon=now)
        effective = {i.effective for i in v.filing.instants}
        if v.at is None:
            assert v.basis in ("no_cse_instant", "later_version_without_document_time")
            continue
        assert v.at not in system_times                              # no system time ever stands in
        if v.role == "base":
            assert v.at == max(effective)                            # A-2 over every observed instant
        days += v.precision == "day"
    assert days >= 1                                                 # at least TILE's legacy date-only time


def test_explain_reproves_on_the_real_evidence(run):
    reader = run["reader"]
    issuer = run["issuers"][0]
    r = api.as_of(reader, issuer_id=issuer, mode=CURRENT)
    out = api.explain(reader, r, audit=True)
    assert out["reproved"] and out["audit"]["label"] == "audit"
    for fact in out["facts"]:
        for entry in fact["visible"]:
            assert entry["filing_observations"] and entry["f5_run"]["snapshot"]
