"""
Real-data validation on PostgreSQL 17 (docs/REAL_DATA_VALIDATION_DESIGN.md sections 5-15): V1-V11.

Skipped unless CSE_F6_CORPUS_DIR, CSE_F0_CAPTURE_DIR and P1_PG_BINDIR are set (Linux). The evidence is not in Git.
These tests never contact CSE; run them with `docker run --network none`.

The real evidence is replayed through the frozen F1 / P2 / F5 / F3 stores into a throwaway database. F6.4's own jobs
then validate, configure, designate (owner path) and reconcile it.

The expected numbers were measured by tests/rdv_report.py on the pinned evidence (manifest fed17a92...) and are
pinned here. A different number is a change in the behaviour of a frozen layer or in the evidence, and fails loudly.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rdv_evidence as E  # noqa: E402
import rdv_measure as M  # noqa: E402
from test_rdv_unit import EXPECTED_DECISIONS  # noqa: E402
from worker.financial_truth_store import preflight, verify  # noqa: E402

BUNDLE = E.locate()
BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(BUNDLE is None or not BINDIR or os.name != "posix",
                                reason=f"{E.CORPUS_ENV}, {E.CAPTURE_ENV} (the evidence is not in the repository) and "
                                       f"P1_PG_BINDIR (PostgreSQL 17, Linux) are not all set")
PROJECTION_SHA256 = "6ed7dd195fe070461baf84f6a8a489cbf2291e7cafa8c3c2dd7f94d80a45614e"


@pytest.fixture(scope="module")
def run():
    base = tempfile.mkdtemp(prefix="rdv", dir="/tmp")          # short: Unix socket paths are limited to 107 bytes
    cluster = S.start_cluster(BINDIR, base)
    try:
        built = E.build_database(cluster, BUNDLE)
        reader = E.reader_conn(cluster, built["database"])
        yield {"cluster": cluster, "built": built, "db": built["database"], "reader": reader,
               "coverage": M.measure(reader)}
        reader.close()
    finally:
        cluster.cleanup()
        shutil.rmtree(base, ignore_errors=True)


def q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.rollback()
    return rows


# ------------------------------------------------------------------------------------------------ V1 the replay

def test_v1_the_replay_used_frozen_paths_only_and_invented_nothing(run):
    rep, r = run["built"]["replay"], run["reader"]
    assert rep["manifest_sha256"] == E.manifest_digest()
    assert rep["security_master"] == {"entries": 327, "created": 327, "duplicate_symbols": [],
                                      "not_created_no_name": []}
    assert rep["issuer"] == {"observations_new": 348, "securities": {"issuers_new": 11, "decisions_new": 11,
                                                                     "evidenced": 11, "conflict": 0,
                                                                     "no_evidence": 316}}
    assert [(x["evidence"], x["key"]) for x in rep["f1"]][:9] == [("E-B1", str(y)) for y in range(2019, 2027)] + [
        ("E-B2", "COMB.N0000")]
    assert sum(x["items"] for x in rep["f1"]) == 12922 and all(x["rejected"] == x["failures"] == 0 for x in rep["f1"])
    assert [(f["f3"], f["f5"]) for f in rep["filings"]] == [("inserted", "inserted")] * 26
    # every issuer rests on real secId evidence, decided by f5.issuer.2; nothing provisional or synthetic exists
    assert q(r, "select count(*) from issuers where identity_basis <> 'cse_sec_id'") == [(0,)]
    assert q(r, "select count(*) from issuers i where not exists (select 1 from issuer_identifier_observations o "
                "where o.cse_sec_id = i.cse_sec_id) or not exists (select 1 from issuer_securities s where "
                "s.issuer_id = i.issuer_id and s.link_status = 'evidenced')") == [(0,)]
    assert q(r, "select distinct rule_version from filing_issuer_links union select distinct rule_version from "
                "issuer_securities union select distinct created_rule_version from issuers") == [("f5.issuer.2",)]
    assert q(r, "select count(*), count(distinct cse_filing_id) from filing_issuer_links") == [(26, 26)]
    # the persisted decisions are exactly design section 6.4 (and the database-free prediction)
    got = {f: (s, b, p, list(ls), list(rs)) for f, s, b, p, ls, rs in q(
        r, "select cse_filing_id, status, basis, path_sec_id, listing_symbols, reasons from filing_issuer_links")}
    assert got == EXPECTED_DECISIONS
    assert run["coverage"]["f1"]["filings"] == 12493
    assert q(r, "select count(*), sum(rows_returned) from report_discovery_runs where status = 'succeeded'") == \
        [(18, 12922)]


# ------------------------------------------------------------------------------------------------ V2 the population

def test_v2_population_and_publication_instants(run):
    p, pub = run["coverage"]["population"], run["coverage"]["publication"]
    assert (p["f5_runs"], p["filings"], p["source_symbols"], p["candidates"]) == (26, 26, 19, 2248)
    assert p["document_types"] == {
        "annual_report|annual_report": 4, "audited_financial_statements|audited_financial_statements": 1,
        "errata_or_reissue|annual_report": 1, "errata_or_reissue|interim_financial_statements": 2,
        "interim_financial_statements|interim_financial_statements": 16, "undetermined|undetermined": 1,
        "unreadable|unreadable": 1}
    assert p["document_status"] == {"ocr_untrusted": 1, "partial": 24, "unreadable": 1}
    assert p["candidate_status"] == {"ambiguous": 17, "conflicting": 24, "proposed": 2017, "unresolved": 190}
    assert p["mapping_status"] == {"ambiguous": 17, "mapped": 2231}
    assert p["zero_candidate_runs"] == [48576, 49117]
    # T1 holds report_filings.uploaded_at by value; the F5 snapshot agrees; legacy date-only times are kept as they are
    assert pub["t1_differs_from_f1"] == [] and pub["f5_snapshot_differs_from_f1_by_1s_or_more"] == []
    assert pub["f1_uploaded_at_source"] == {"financials": 4, "getFinancialAnnouncement": 22}
    assert pub["path_epoch_missing"] == [48292, 49117, 50613, 52620, 53129]
    assert pub["date_only_upload_times"] == [32216] == pub["path_epoch_differs_from_upload_by_1s_or_more"]


# ------------------------------------------------------------------------------------------------ V3 admission

def test_v3_every_candidate_is_validated_and_every_reason_counted(run):
    v = run["coverage"]["validation"]
    assert (v["validation_runs"], v["candidates"]) == (26, 2248)
    assert v["admission"] == {"admitted_nil": 8, "admitted_numeric": 440, "not_admitted": 1800}
    assert v["f6_1_eligibility"] == {"eligible": 519, "ineligible": 1718, "normalization_required": 11}
    assert v["f6_1_ineligible_reasons"] == {
        "candidate_status_ambiguous": 17, "candidate_status_conflicting": 24, "candidate_status_unresolved": 190,
        "duration_months_missing": 111, "issuer_evidence_unresolved": 1523, "mapping_not_single_concept": 17,
        "operations_section_derived_on_total_row": 20, "period_end_after_publication": 16, "role_untrusted": 197}
    assert v["f6_1_normalization_reasons"] == {"currency_not_reported": 49, "per_share_unit_not_stated": 16,
                                               "scale_conflicting": 34, "scale_unresolved": 49,
                                               "value_reported_nil": 65, "value_type_unknown": 17}
    assert v["admission_reasons"] == {
        "issuer_link_not_evidenced:unresolved": 1523, "issuer_link_path_prefix_only": 102,
        "maturity_undetermined:section_not_maturity": 6, "normalization_not_admissible": 116,
        "operations_section_derived_unvalidated:insufficient_evidence": 14, "validation_ineligible": 1712}
    assert v["refusal_reasons"] == {
        "candidate_status_ambiguous": 17, "candidate_status_conflicting": 24, "candidate_status_unresolved": 190,
        "currency_not_reported": 49, "duration_months_missing": 111, "issuer_evidence_unresolved": 1523,
        "issuer_link_not_evidenced:unresolved": 1523, "issuer_link_path_prefix_only": 102,
        "mapping_not_single_concept": 17, "maturity_undetermined:section_not_maturity": 6,
        "operations_section_derived_on_total_row": 14,
        "operations_section_derived_unvalidated:insufficient_evidence": 14, "per_share_unit_not_stated": 16,
        "period_end_after_publication": 16, "role_untrusted": 197, "scale_conflicting": 34, "scale_unresolved": 49,
        "value_reported_nil": 12, "value_type_unknown": 17}
    assert v["normalization_required"] == {"admitted_nil|value_reported_nil": 8, "refused|value_reported_nil": 3}
    assert v["eligible_not_admitted"] == {"issuer_link_path_prefix_only": 79}
    assert v["refused_only_for_issuer_evidence"] == {
        "candidates": 1378,
        "by_link": {"evidenced|document_path_prefix": 88, "unresolved|document_path_prefix": 1184,
                    "unresolved|none": 106},
        "by_symbol": {"ACL": 36, "ASPH": 60, "BLUE": 35, "CTC": 68, "DIAL": 104, "DIMO": 126, "HPL": 112, "KHC": 176,
                      "LOLC": 88, "PABC": 56, "RWSL": 104, "SEYB": 112, "SLTL": 108, "TESS": 36, "TILE": 80,
                      "UCAR": 77}}
    assert v["by_link"] == {"evidenced|both": 3, "evidenced|document_path_prefix": 1,
                            "evidenced|listing_symbol_sec_id": 1, "unresolved|document_path_prefix": 17,
                            "unresolved|none": 4}


# ------------------------------------------------------------------------------------------------ V4 OP1

def test_v4_op1_on_real_section_derived_rows(run):
    o, r = run["coverage"]["op1"], run["reader"]
    assert o == {"records": 12, "record_outcomes": {"insufficient_evidence": 9, "pass": 3},
                 "record_reasons": {"missing:total_or_unstated": 4, "nil:continuing": 2, "nil:discontinued": 5,
                                    "nil:total_or_unstated": 2},
                 "records_by_filing": {"52684|insufficient_evidence": 9, "52684|pass": 3},
                 "validated_candidate_ids": 6, "section_derived_candidates": 20,
                 "section_derived_outcomes": {"insufficient_evidence": 14, "pass": 6}, "section_derived_admitted": 0,
                 "section_derived_by_filing": {"52684": 20}}
    # every LOLC candidate is refused by A-2: its link rests on the path prefix alone
    assert q(r, "select count(*), count(*) filter (where 'issuer_link_path_prefix_only' = any(admission_reasons)) "
                "from financial_candidate_validations c join financial_validation_runs v using (validation_run_key) "
                "where v.cse_filing_id = 52684") == [(102, 102)]


# ------------------------------------------------------------------------------------------------ V5 SOs and facts

def test_v5_source_observations_facts_and_reconciliation(run):
    c = run["coverage"]
    so, f, rec = c["source_observations"], c["facts"], c["reconciliation"]
    assert (so["count"], so["members"], so["spanning_two_or_more_columns"]) == (404, 448, 26)
    assert so["by_status"] == {"consistent:nil": 6, "consistent:numeric": 390, "internally_conflicting:numeric": 8}
    assert so["member_count"] == {"1": 372, "2": 20, "3": 12} and so["annotations"] == {}
    assert so["member_comparisons"] == {"agree|-|False": 48, "disagree|-|False": 8}
    assert so["by_filing"] == {"47026": 94, "49384": 94, "50613": 60, "50738": 156}
    assert (f["count"], f["concepts"], f["issuers"]) == (321, 23, {"secid:369": 321})
    assert f["currency"] == {"LKR": 261, "USD": 60}
    assert f["scope"] == {"bank": 123, "group": 180, "unlabelled": 18}
    assert f["period"] == {"duration:12": 112, "duration:3": 50, "duration:9": 81, "instant:-": 78}
    assert (f["operations"], f["maturity"]) == ({"total_or_unstated": 321}, {"not_applicable": 321})
    assert rec["states"] == {"conflicting": 8, "corroborated:numeric": 73, "single_source:nil": 6,
                             "single_source:numeric": 234}
    assert rec["conflicting"] == {"internal_conflict": 8} and rec["reason_kinds"] == {"internal_conflict": 8}
    assert rec["annotations"] == {"internal_conflict": 8, "multi_currency_presentation": 120}
    assert rec["documents_per_fact"] == {"1": 248, "2": 63, "3": 10}
    assert (rec["current_facts"], rec["records"], rec["facts_not_current"], rec["configurations"]) == (321, 321, 0, 1)
    assert (rec["representative_ambiguous"], rec["representative_outside_interval"]) == (0, 0)
    assert rec["batches"] == [{"cse_sec_id": 369, "sequence": 1, "results_count": 321, "records_appended": 321}]
    assert rec["excluded_observations"] == {}


# ------------------------------------------------------------------------------------------------ V6 negative cases

def test_v6_nothing_refused_ambiguous_or_conflicting_becomes_a_fact(run):
    r = run["reader"]
    zero = [(0,)]
    # every F5 candidate has exactly one candidate validation; F5 ambiguous / conflicting / unresolved are never
    # admitted
    assert q(r, "select count(*) from financial_fact_candidates fc where (select count(*) from "
                "financial_candidate_validations c where c.candidate_id = fc.id) <> 1") == zero
    assert q(r, "select count(*), count(*) filter (where c.admitted) from financial_candidate_validations c join "
                "financial_fact_candidates fc on fc.id = c.candidate_id where fc.candidate_status <> 'proposed'") == \
        [(231, 0)]
    # no refused candidate is a member of any SO, and none carries an ef_key
    assert q(r, "select count(*) from financial_so_members m join financial_candidate_validations c using "
                "(candidate_validation_key) where not c.admitted") == zero
    assert q(r, "select count(*) from financial_candidate_validations where not admitted and ef_key is not null") == \
        zero
    # no SO or fact from an issuer decision A-2 does not admit (unresolved, or path prefix only)
    assert q(r, "select count(*) from financial_source_observations s join financial_validation_runs v using "
                "(validation_run_key) join filing_issuer_links l on l.id = v.issuer_link_id where not (l.status = "
                "'evidenced' and l.basis in ('listing_symbol_sec_id', 'both'))") == zero
    assert q(r, "select count(*) from financial_validation_runs v join filing_issuer_links l on l.id = "
                "v.issuer_link_id where not (l.status = 'evidenced' and l.basis in ('listing_symbol_sec_id', 'both')) "
                "and (v.so_count <> 0 or v.admitted_numeric + v.admitted_nil <> 0)") == zero
    # a conflicting fact carries no value, interval or representative; nil is never zero
    assert q(r, "select count(*) from financial_reconciliation_records where state = 'conflicting' and (value_kind "
                "is not null or interval_low is not null or interval_high is not null or representative_so_key is "
                "not null)") == zero
    assert q(r, "select count(*) from financial_so_members where value_kind = 'nil' and (normalized_value is not "
                "null or half_unit is not null)") == zero
    assert q(r, "select count(*) from financial_reconciliation_records where value_kind = 'nil' and (interval_low is "
                "not null or representative_normalized_value is not null)") == zero
    # every conflicting input is kept with its role; nothing is selected
    assert q(r, "select count(*) from financial_reconciliation_inputs i join financial_reconciliation_records r "
                "using (record_id) where r.state = 'conflicting' and i.role_in_outcome <> 'conflicting'") == zero
    assert q(r, "select count(*) from financial_reconciliation_inputs i join financial_reconciliation_records r "
                "using (record_id) where r.state = 'conflicting'") == q(
        r, "select sum(so_count) from financial_reconciliation_records where state = 'conflicting'")


# ------------------------------------------------------------------------------------------------ V7 determinism

def test_v7_d3_d4_every_validation_run_is_reproduced_and_order_independent(run):
    report = verify.verify(run["reader"], sample=26)
    assert report["ok"], report["problems"][:5]
    assert report["counts"] == {"validation_runs": 26, "source_observations": 404, "facts": 321, "configurations": 1,
                                "batches": 1, "records": 321, "reproduced": 26}
    got = M.recompute(run["reader"])
    assert got["ok"], got["problems"][:5]
    assert got["counts"] == {"batches": 1, "candidates": 2248, "op1_records": 12, "records": 321,
                             "source_observations": 404, "validation_runs": 26}


def test_v7_d1_d2_d6_repeats_change_nothing_and_a_different_result_is_refused(run):
    before = M.projection(run["reader"])
    assert before["digest"] == PROJECTION_SHA256
    rep = E.repeat_jobs(run["cluster"], run["db"])
    assert rep["validate"] == {"already_present": 26} and rep["rows_unchanged"]
    assert rep["reconcile"] == {"state": "succeeded", "partitions": 1, "written": 0, "unchanged": 1, "failed": []}
    refused = E.nondeterminism_refused(run["cluster"], run["db"], 49384)
    assert refused["content_key_differs"] and refused["codec_problems"] == []
    assert "uq_fvr_input_set" in refused["refused"] and refused["stored_unchanged"]
    assert M.projection(run["reader"]) == before                  # nothing was overwritten or added


def test_v7_d5_a_second_replay_gives_the_identical_projection(run):
    second = E.build_database(run["cluster"], BUNDLE)
    assert second["database"] != run["db"]
    r2 = E.reader_conn(run["cluster"], second["database"])
    try:
        assert M.projection(r2) == M.projection(run["reader"])
    finally:
        r2.close()


# ------------------------------------------------------------------------------------------------ V8 provenance

def test_v8_every_sampled_and_failed_chain_is_complete(run):
    p = M.provenance(run["reader"])
    assert p["complete"], p["problems"][:5]
    assert (p["facts_traced"], p["candidates_traced"]) == (20, 113)
    assert p["fact_states_traced"] == {"conflicting:None": 8, "corroborated:numeric": 3, "single_source:nil": 6,
                                       "single_source:numeric": 3}
    assert set(p["refusal_reasons_covered"]) == set(run["coverage"]["validation"]["refusal_reasons"])


# ------------------------------------------------------------------------------------------------ V9 persistence

def test_v9_persistence_integrity_versions_and_the_job_ledger(run):
    w = S.conn(run["cluster"], run["db"], "cse_worker")
    try:
        for name, sql in S.ELEMENT_CHECKS.items():
            assert q(w, sql) == [(0,)], name
        assert S.numeric_text_mismatches(w) == {}
        assert preflight.problems(w) == []
    finally:
        w.close()
    c = run["coverage"]
    assert c["row_counts"] == {
        "financial_validation_runs": 26, "financial_candidate_validations": 2248, "financial_op1_records": 12,
        "financial_economic_facts": 321, "financial_source_observations": 404, "financial_so_members": 448,
        "financial_so_comparisons": 56, "financial_reconciliation_configurations": 1,
        "financial_reconciliation_designations": 1, "financial_reconciliation_batches": 1,
        "financial_reconciliation_records": 321, "financial_reconciliation_inputs": 404,
        "financial_reconciliation_comparisons": 109, "financial_reconciliation_batch_results": 321}
    assert c["jobs"] == {"states": {"reconcile|succeeded": 1, "validate|succeeded": 26}, "problems": []}
    v = c["versions"]
    assert v["f6"] == [{"validation_version": "f6.validation.1", "input_policy_version": "f6.inputs.1",
                        "op1_version": "f6.op1.partition.1", "admission_version": "f6.admission.1",
                        "identity_version": "f6.identity.1", "store_version": "f6.store.1"}]
    assert v["f3_f4_f5"] == [{"classifier_version": "f3.1", "text_extractor": "pdftotext 24.02.0 (poppler) -layout",
                              "word_extractor": "poppler-pdftotext 24.02.0 -bbox-layout",
                              "f4_extractor_version": "f4.1", "builder_version": "f5.1",
                              "mapper_version": "f5.map.1", "vocabulary_version": "v1"}]
    assert v["issuer_rules"] == ["f5.issuer.2"]
    assert v["migrations"][-1] == ["0015_financial_truth_persistence.sql",
                                   "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2"]
    assert len(v["migrations"]) == 14 and not any(m[0].startswith("0006") for m in v["migrations"])


# ------------------------------------------------------------------------------------------------ V10 differential

def test_v10_the_issuer_evidence_differential_explains_every_difference(run):
    d = M.differential(run["reader"])
    assert d["unexplained"] == [] and d["candidates_compared"] == 2248
    assert d["explained"] == {"identical_except_issuer": 448, "identical_refusal": 175,
                              "issuer_only:issuer_link_not_evidenced:unresolved:f6.1": 1523,
                              "issuer_only:issuer_link_path_prefix_only": 102}
    assert d["withheld_by_missing_issuer_evidence"]["candidates"] == {"admitted_nil": 35, "admitted_numeric": 1343}
    assert d["admissible_issuer_facts"] == {"real": 321, "proxy": 321, "mismatches": [], "mismatch_count": 0}
    # the counterfactual proxy totals are the frozen F6.2 section 14 measurements, exactly (never persisted)
    a, rc = d["proxy_totals_counterfactual"]["admission"], d["proxy_totals_counterfactual"]["reconciliation"]
    assert (a["candidates"], a["admission"]) == (2248, {"admitted_nil": 43, "admitted_numeric": 1783,
                                                        "not_admitted": 422})
    assert a["op1_section_derived_rows"] == {"insufficient_evidence": 14, "pass": 6}
    assert (rc["facts"], rc["observations"]) == (1488, 1746)
    assert rc["states"] == {"conflicting": 35, "corroborated:nil": 7, "corroborated:numeric": 216,
                            "single_source:nil": 27, "single_source:numeric": 1203}
    assert rc["conflicting"] == {"across_documents": 23, "internal_conflict": 12}
    assert rc["documents_per_fact"] == {1: 1240, 2: 238, 3: 10}
    assert rc["facts_by_scope"] == {"bank": 169, "company": 433, "group": 666, "unlabelled": 220}
    assert rc["facts_by_currency"] == {"LKR": 1424, "USD": 64}


# ------------------------------------------------------------------------------------------------ V11 anomalies

EXPECTED_ANOMALIES = {
    "P-1": ("E", 16), "P-2": ("B", 111), "P-3": ("A", 50), "P-4": ("B", 197), "P-5": ("B", 190), "P-6": ("B", 24),
    "P-7": ("B", 17), "P-8": ("B", 17), "P-9": ("B", 49), "P-10": ("B", 49), "P-11": ("B", 34), "P-12": ("B", 16),
    "P-13": ("B", 17), "P-14": ("B", 6), "P-15": ("A", 12), "P-16": ("B", 21), "P-17": ("B", 1), "P-18": ("E", 2502),
    "P-19": ("C", 2), "P-20": ("A", 60), "P-21": ("A", 14), "P-22": ("A", 37), "P-23": ("E", 8), "P-24": ("A", 0),
    "P-25": ("A", 24), "P-26": ("A", 36), "P-27": ("D", 3), "P-28": ("D", 19), "P-29": ("A", 6), "P-30": ("A", 0),
    "P-31": ("B", 2), "P-32": ("D", 1), "P-33": ("C", 0), "P-34": ("A", 1)}


def test_v11_every_anomaly_is_detected_and_classified_exactly_once(run):
    cat = M.anomalies(run["reader"])
    got = {a["id"]: (a["classification"], a["count"]) for a in cat["anomalies"]}
    assert len(got) == len(cat["anomalies"])                      # one record per anomaly
    assert got == EXPECTED_ANOMALIES
    assert set(cat["classes"]) == {"A", "B", "C", "D", "E"}
    assert cat["by_classification"] == {"A": 11, "B": 15, "C": 2, "D": 3, "E": 3}
    by_id = {a["id"]: a for a in cat["anomalies"]}
    assert by_id["P-1"]["filings"] == {"52713": 16}
    assert by_id["P-17"]["filings"] == {"52684": "LOLC"}
    assert by_id["P-18"]["filings"] == {str(f): "population" for f in (48292, 49117, 50613, 52620, 53129)}
    assert by_id["P-19"]["filings"] == {"NAVF.N0000": "listing_only", "NEST.N0000": "secid_evidence"}
    assert {i["cse_filing_id"] for i in by_id["P-23"]["examples"]} == {50738}
    assert by_id["P-27"]["filings"] == {"52860": "errata_or_reissue|interim_financial_statements|unresolved",
                                        "53067": "errata_or_reissue|annual_report|unresolved",
                                        "53129": "errata_or_reissue|interim_financial_statements|unresolved"}
