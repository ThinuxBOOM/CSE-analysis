"""
HB-5's audit on the REAL-DATA VALIDATION evidence (design section 23.3: "the audit must reproduce RDV's population
numbers for the 26 corpus filings: 2,248 candidates; 440 + 8 admitted; 1,378 refused only for issuer evidence; 404 SOs;
321 facts"; "detector parity with RDV's catalogue on the same evidence"). OPTIONAL: skipped unless CSE_F6_CORPUS_DIR,
CSE_F0_CAPTURE_DIR and P1_PG_BINDIR are set (Linux). The evidence is not in Git. These tests never contact CSE: run
them with `docker run --network none`.

The pinned evidence is replayed and taken through F6.4 exactly as tests/test_rdv_postgres.py does it (the harness's own
build_database). HB-5's audit then reads that database as cse_reader, records as the worker, and is compared with the
harness's own measures and catalogue, read from the same database. The harness is test code: HB-5 re-implements it in
production and never imports it (RDV-B3).
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rdv_evidence as E  # noqa: E402
import rdv_measure as M  # noqa: E402
from worker.backfill_f6 import anomalies, audit, coverage  # noqa: E402

BUNDLE = E.locate()
BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(BUNDLE is None or not BINDIR or os.name != "posix",
                                reason=f"{E.CORPUS_ENV}, {E.CAPTURE_ENV} (the evidence is not in the repository) and "
                                       f"P1_PG_BINDIR (PostgreSQL 17, Linux) are not all set")
COMB_FILINGS = {47026, 49384, 50613, 50738}
LLUB, CRL = 49117, 48576


def _month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@pytest.fixture(scope="module")
def run():
    base = tempfile.mkdtemp(prefix="rdv", dir="/tmp")              # short: Unix socket paths are limited
    cluster = S.start_cluster(BINDIR, base)
    try:
        built = E.build_database(cluster, BUNDLE)
        db = built["database"]
        w = S.conn(cluster, db, "cse_worker")
        reader = E.reader_conn(cluster, db)
        with w.cursor() as cur:                                    # W: every month any discovered filing was uploaded in
            cur.execute("select min(uploaded_at at time zone 'Asia/Colombo')::date, "
                        "max(uploaded_at at time zone 'Asia/Colombo')::date from report_filings")
            first, last = cur.fetchone()
        w.rollback()
        window = (date(first.year, first.month, 1), _month_end(last))
        result = audit.run(w, window=window, reader=reader)        # the real preflight, as the worker
        yield {"w": w, "reader": reader, "window": window, "audit": result, "measure": M.measure(reader),
               "catalogue": M.anomalies(reader)}
        reader.close()
        w.close()
    finally:
        cluster.cleanup()
        shutil.rmtree(base, ignore_errors=True)


def test_rdv1_the_population_numbers_of_section_23_3(run):
    lv = run["audit"]["coverage"]["levels"]
    assert lv["candidates"]["candidates"] == 2248
    assert lv["candidates"]["admission"] == {"admitted_nil": 8, "admitted_numeric": 440, "not_admitted": 1800}
    assert lv["candidates"]["refused_only_for_issuer_evidence"]["candidates"] == 1378
    assert lv["source_observations"]["count"] == 404
    assert lv["facts"]["count"] == 321 and lv["reconciliation"]["current_facts"] == 321


def test_rdv2_every_candidate_so_and_fact_measure_equals_the_real_data_validations(run):
    lv, m = run["audit"]["coverage"]["levels"], run["measure"]
    for k in ("candidates", "admission", "f6_1_eligibility", "f6_1_ineligible_reasons", "f6_1_normalization_reasons",
              "admission_reasons", "refusal_reasons", "refusal_profiles", "normalization_required",
              "eligible_not_admitted", "operations_route_admitted"):
        assert lv["candidates"][k] == m["validation"][k], k
    mine, theirs = lv["candidates"]["refused_only_for_issuer_evidence"], m["validation"][
        "refused_only_for_issuer_evidence"]
    assert (mine["candidates"], mine["by_link"], mine["by_symbol"]) == (theirs["candidates"], theirs["by_link"],
                                                                        theirs["by_symbol"])
    for k in ("candidate_status", "mapping_status", "document_status", "zero_candidate_runs"):
        assert lv["population"][k] == m["population"][k], k
    assert lv["population"]["validation_runs"] == m["validation"]["validation_runs"] == 26
    assert lv["source_observations"] == {k: m["source_observations"][k] for k in lv["source_observations"]}
    assert lv["facts"] == {k: m["facts"][k] for k in lv["facts"]}
    assert lv["reconciliation"] == {k: m["reconciliation"][k] for k in lv["reconciliation"]}


def test_rdv3_detector_parity_with_the_real_data_catalogue(run):
    ours = {r["detector_id"]: r for r in run["audit"]["anomalies"]}
    theirs = run["catalogue"]["anomalies"]
    assert [a["id"] for a in theirs if a["id"] not in ours] == []
    for a in theirs:
        r = ours[a["id"]]
        assert r["count"] == a["count"], a["id"]
        assert r["anomaly_class"] == anomalies.CLASS_OF_RDV[a["classification"]], a["id"]
        assert r["counts"]["catalogue_class"] == a["classification"], a["id"]
        assert r["subjects"] == a["filings"], a["id"]
        assert r["examples"] == a["examples"] or a["id"] in ("P-20", "P-29"), a["id"]
    assert ours["P-18"]["count"] == 2502 and ours["P-1"]["count"] == 16 and ours["P-23"]["count"] == 8
    assert run["catalogue"]["by_classification"] == {"A": 11, "B": 15, "C": 2, "D": 3, "E": 3}


def test_rdv4_the_funnel_on_the_real_evidence(run):
    table = {r["cse_filing_id"]: r for r in run["audit"]["coverage"]["table"]}
    links = {f["cse_filing_id"]: (f["status"], f["basis"]) for f in run["measure"]["issuer_evidence"]["filings"]}
    assert len(links) == 26 and set(links) <= set(table)
    for fid, (status, basis) in links.items():
        r = table[fid]
        if fid in COMB_FILINGS:
            assert (r["reached"], r["stop"]) == (9, None), fid
        elif fid == LLUB:
            assert r["stop"] == [4, "unreadable"], fid
        elif fid == CRL:
            assert r["stop"] == [5, "ocr_untrusted"], fid
        else:
            assert r["stop"] == [8, "issuer_evidence:" + coverage.link_category(status, basis)], (fid, r["stop"])
    funnel = run["audit"]["coverage"]["funnel"]
    others = [r for fid, r in table.items() if fid not in links]
    assert funnel["complete"] == 4 and funnel["reached"]["3"] == 26
    assert all(r["reached"] <= 2 for r in others)                     # discovered only: nothing else was retrieved
    assert sum(funnel["stops"]["3"].values()) == sum(1 for r in others if r["stop"][0] == 3)


def test_rdv5_the_snapshot_is_a_pure_function_of_the_evidence(run):
    again = audit.run(run["w"], window=run["window"], reader=run["reader"])
    first = run["audit"]
    assert (again["digest"], again["snapshot_id"], again["snapshot_created"]) == (first["digest"],
                                                                                    first["snapshot_id"], False)
    assert again["anomaly_ids"] == first["anomaly_ids"]
