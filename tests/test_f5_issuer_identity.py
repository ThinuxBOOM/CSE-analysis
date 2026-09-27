"""
Stage F5 — issuer identity (worker/issuer_identity.py), I-13: a filing links to an
issuer only with evidence; conflicts stay 'conflict'; issuers are never merged.
Real CSE shapes from tests/fixtures (companyInfoSummery COMB/HNB, /api/financials COMB).
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import issuer_identity as ii

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
COMB = json.load(open(os.path.join(FIX, "multi_company", "real_companyInfoSummery_COMB_N0000.json"), encoding="utf-8"))
HNB = json.load(open(os.path.join(FIX, "multi_company", "real_companyInfoSummery_HNB_N0000.json"), encoding="utf-8"))
FIN = json.load(open(os.path.join(FIX, "filings", "real_financials_COMB_N0000_trimmed.json"), encoding="utf-8"))
AT = "2026-09-26T10:00:00+00:00"


def test_company_info_gives_one_observation_per_sec_id_field():
    obs = ii.observations_from_company_info(COMB, "COMB.N0000", AT, "fixture")
    assert [(o["source_field"], o["symbol"], o["cse_security_id"], o["cse_sec_id"]) for o in obs] == [
        ("reqSymbolBetaInfo.securityId", "COMB.N0000", 208, 369), ("reqLogo.secId", "COMB.N0000", 208, 369)]
    assert all(len(o["payload_sha256"]) == 64 and o["observed_at"] == AT for o in obs)
    # identical payload -> identical hash (idempotent re-observation)
    assert [o["payload_sha256"] for o in ii.observations_from_company_info(copy.deepcopy(COMB), "COMB.N0000", AT)] == \
        [o["payload_sha256"] for o in obs]


def test_disagreeing_sec_id_fields_both_stay_visible():
    body = copy.deepcopy(COMB)
    body["reqLogo"]["secId"] = 999
    obs = ii.observations_from_company_info(body, "COMB.N0000", AT)
    assert sorted(o["cse_sec_id"] for o in obs) == [369, 999]


def test_body_without_sec_id_is_still_observed_without_one():
    body = {"reqSymbolInfo": COMB["reqSymbolInfo"]}
    [o] = ii.observations_from_company_info(body, "COMB.N0000", AT)
    assert o["cse_sec_id"] is None and o["cse_security_id"] == 208


def test_financials_and_all_security_code_observations():
    [o] = ii.observations_from_financials(FIN, "COMB.X0000", AT)
    assert (o["symbol"], o["cse_sec_id"], o["source_field"]) == ("COMB.X0000", 369, "reqFinancial.secId")
    items = [{"id": 208, "name": "COMMERCIAL BANK OF CEYLON PLC", "symbol": "COMB.N0000", "active": 1},
             {"id": 396, "name": "COMMERCIAL BANK OF CEYLON PLC", "symbol": "COMB.X0000", "active": 1}]
    got = ii.observations_from_all_security_codes(items, AT)
    assert [(o["symbol"], o["cse_security_id"], o["cse_sec_id"], o["active"]) for o in got] == [
        ("COMB.N0000", 208, None, True), ("COMB.X0000", 396, None, True)]


def _o(i, sec, at=AT):
    return {"id": i, "cse_sec_id": sec, "observed_at": at}


def test_security_decisions_evidenced_conflict_and_none():
    d = ii.decide_security("COMB.N0000", [_o(1, 369), _o(2, 369), _o(3, None)])
    assert (d.link_status, d.sec_id, d.observed_sec_ids, d.evidence_observation_ids) == ("evidenced", 369, [369], [1, 2])
    c = ii.decide_security("XYZ.N0000", [_o(1, 369), _o(2, 370, "2027-01-01T00:00:00+00:00")])
    assert (c.link_status, c.sec_id, c.observed_sec_ids) == ("conflict", None, [369, 370])     # never guessed
    assert c.first_evidence_at < c.last_evidence_at
    assert ii.decide_security("NEW.N0000", [_o(1, None)]) is None
    assert d.evidence_sha256 != ii.decide_security("COMB.N0000", [_o(1, 369), _o(2, 370)]).evidence_sha256


LINKS = {"COMB.N0000": ("evidenced", 369), "COMB.X0000": ("evidenced", 369), "HNB.N0000": ("evidenced", 373),
         "BAD.N0000": ("conflict", None)}
ISSUERS = {369: "11111111-1111-1111-1111-111111111111", 373: "22222222-2222-2222-2222-222222222222"}
P369 = "cmt/upload_report_file/369_1762944976777.pdf"


def test_filing_evidenced_by_path_and_both_share_classes():
    d = ii.decide_filing(49384, P369, ["COMB.X0000", "COMB.N0000"], LINKS, ISSUERS)
    assert (d.status, d.basis, d.issuer_id, d.path_sec_id, d.listing_sec_ids) == ("evidenced", "both", ISSUERS[369], 369, [369])


def test_filing_evidenced_by_path_or_listing_alone():
    assert ii.decide_filing(1, P369, [], LINKS, ISSUERS).basis == "document_path_prefix"
    d = ii.decide_filing(2, None, ["HNB.N0000"], LINKS, ISSUERS)
    assert (d.status, d.basis, d.issuer_id) == ("evidenced", "listing_symbol_sec_id", ISSUERS[373])
    assert "no_document_path_prefix" in d.reasons


def test_filing_conflicts_are_kept_as_conflicts():
    d = ii.decide_filing(3, P369, ["HNB.N0000"], LINKS, ISSUERS)            # path says 369, listing says 373
    assert (d.status, d.issuer_id, d.reasons[-1]) == ("conflict", None, "sec_ids_disagree")
    d = ii.decide_filing(4, P369, ["COMB.N0000", "BAD.N0000"], LINKS, ISSUERS)
    assert (d.status, d.issuer_id, d.listing_conflicts) == ("conflict", None, ["BAD.N0000"])


def test_filing_unresolved_without_evidence_or_without_an_issuer():
    d = ii.decide_filing(5, None, [], LINKS, ISSUERS)
    assert (d.status, d.basis, d.issuer_id) == ("unresolved", "none", None)
    d = ii.decide_filing(6, "cmt/upload_report_file/777_1762944976777.pdf", ["UNKNOWN.N0000"], LINKS, ISSUERS)
    assert (d.status, d.sec_id, d.issuer_id) == ("unresolved", 777, None)       # no issuer is invented from a path
    assert "no_issuer_for_sec_id" in d.reasons and "listing_symbol_without_issuer_evidence:UNKNOWN.N0000" in d.reasons
    d = ii.decide_filing(7, "cmt/upload_report_file/report.pdf", [], LINKS, ISSUERS)
    assert d.status == "unresolved" and d.path_sec_id is None


def test_filing_decision_is_deterministic():
    a = ii.decide_filing(49384, P369, ["COMB.X0000", "COMB.N0000"], LINKS, ISSUERS)
    b = ii.decide_filing(49384, P369, ["COMB.N0000", "COMB.X0000"], dict(LINKS), dict(ISSUERS))
    assert a == b and a.evidence_sha256 == b.evidence_sha256
