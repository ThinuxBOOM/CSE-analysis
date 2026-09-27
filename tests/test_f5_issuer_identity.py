"""
Stage F5 — issuer identity (worker/issuer_identity.py), I-13: a filing links to an
issuer only with evidence; conflicts stay 'conflict'; a reused secId never silently
attaches an unrelated security or filing to an existing issuer (rule f5.issuer.2).
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
    assert c.reasons == ["sec_ids_disagree"] and d.reasons == []
    assert c.first_evidence_at < c.last_evidence_at
    assert ii.decide_security("NEW.N0000", [_o(1, None)]) is None
    assert d.evidence_sha256 != ii.decide_security("COMB.N0000", [_o(1, 369), _o(2, 370)]).evidence_sha256


LINKS = {"COMB.N0000": ("evidenced", 369), "COMB.X0000": ("evidenced", 369), "HNB.N0000": ("evidenced", 373),
         "BAD.N0000": ("conflict", None)}
ISSUERS = {369: "11111111-1111-1111-1111-111111111111", 373: "22222222-2222-2222-2222-222222222222"}
P369 = "cmt/upload_report_file/369_1762944976777.pdf"


def test_filing_evidenced_by_path_and_both_share_classes():
    d = ii.decide_filing(49384, P369, ["COMB.X0000", "COMB.N0000"], LINKS, ISSUERS, set())
    assert (d.status, d.basis, d.issuer_id, d.path_sec_id, d.listing_sec_ids) == ("evidenced", "both", ISSUERS[369], 369, [369])


def test_filing_evidenced_by_path_or_listing_alone():
    assert ii.decide_filing(1, P369, [], LINKS, ISSUERS, set()).basis == "document_path_prefix"
    d = ii.decide_filing(2, None, ["HNB.N0000"], LINKS, ISSUERS, set())
    assert (d.status, d.basis, d.issuer_id) == ("evidenced", "listing_symbol_sec_id", ISSUERS[373])
    assert "no_document_path_prefix" in d.reasons


def test_filing_conflicts_are_kept_as_conflicts():
    d = ii.decide_filing(3, P369, ["HNB.N0000"], LINKS, ISSUERS, set())            # path says 369, listing says 373
    assert (d.status, d.issuer_id, d.reasons[-1]) == ("conflict", None, "sec_ids_disagree")
    d = ii.decide_filing(4, P369, ["COMB.N0000", "BAD.N0000"], LINKS, ISSUERS, set())
    assert (d.status, d.issuer_id, d.listing_conflicts) == ("conflict", None, ["BAD.N0000"])


def test_filing_unresolved_without_evidence_or_without_an_issuer():
    d = ii.decide_filing(5, None, [], LINKS, ISSUERS, set())
    assert (d.status, d.basis, d.issuer_id) == ("unresolved", "none", None)
    d = ii.decide_filing(6, "cmt/upload_report_file/777_1762944976777.pdf", ["UNKNOWN.N0000"], LINKS, ISSUERS, set())
    assert (d.status, d.sec_id, d.issuer_id) == ("unresolved", 777, None)       # no issuer is invented from a path
    assert "no_issuer_for_sec_id" in d.reasons and "listing_symbol_without_issuer_evidence:UNKNOWN.N0000" in d.reasons
    d = ii.decide_filing(7, "cmt/upload_report_file/report.pdf", [], LINKS, ISSUERS, set())
    assert d.status == "unresolved" and d.path_sec_id is None


def test_filing_decision_is_deterministic():
    a = ii.decide_filing(49384, P369, ["COMB.X0000", "COMB.N0000"], LINKS, ISSUERS, set())
    b = ii.decide_filing(49384, P369, ["COMB.N0000", "COMB.X0000"], dict(LINKS), dict(ISSUERS), set())
    assert a == b and a.evidence_sha256 == b.evidence_sha256


# --- secId reuse guard (B1 / I-13 / D1): a shared secId alone never puts two securities under one issuer ---------

COMB_NAME = "COMMERCIAL BANK OF CEYLON PLC"
NEWCO_NAME = "UNRELATED NEWCO PLC"
LATER = "2027-01-01T00:00:00+00:00"


def _io(i, sec, isin, name, at=AT):
    return {"id": i, "cse_sec_id": sec, "isin": isin, "name": name, "observed_at": at}


def _status(decisions):
    return {s: (d.link_status, d.sec_id) for s, d in decisions.items()}


def test_isin_issuer_code_and_name_normalisation():
    assert ii.isin_issuer_code("LK0053N00005") == "0053" and ii.isin_issuer_code(" lk0053x00003 ") == "0053"
    assert [ii.isin_issuer_code(x) for x in (None, "", "US0378331005", "LK53N5", "LK0053N0000")] == [None] * 5
    assert ii.normalise_name("  commercial bank of  ceylon plc. ") == COMB_NAME
    assert ii.normalise_name(" - ") is None and ii.normalise_name(None) is None


def test_reuse_guard_case1_same_identity_evidence_keeps_share_classes_under_one_issuer():
    obs = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME), _io(2, 369, "LK0053N00005", COMB_NAME)],
           "COMB.X0000": [_io(3, 369, "LK0053X00003", COMB_NAME)]}
    got = ii.decide_securities(obs)
    assert _status(got) == {"COMB.N0000": ("evidenced", 369), "COMB.X0000": ("evidenced", 369)}
    assert got["COMB.N0000"] == ii.decide_security("COMB.N0000", obs["COMB.N0000"])     # unchanged evidence path
    assert ii.disputed_sec_ids(obs) == {}
    # the ISIN issuer code decides when both sides carry one: a name change under the same code is one issuer
    obs["COMB.X0000"].append(_io(4, 369, "LK0053X00003", "COMMERCIAL BANK OF CEYLON PLC (RENAMED)"))
    assert {d.link_status for d in ii.decide_securities(obs).values()} == {"evidenced"}


def test_reuse_guard_case2_conflicting_isin_is_a_conflict_for_every_claimant():
    obs = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME)],
           "COMB.X0000": [_io(2, 369, "LK0053X00003", COMB_NAME)],
           "NEWCO.N0000": [_io(3, 369, "LK9999N00001", NEWCO_NAME, LATER)]}
    got = ii.decide_securities(obs)
    assert _status(got) == {s: ("conflict", None) for s in obs}                          # never guessed
    assert got["NEWCO.N0000"].reasons == ["sec_id_identity_disputed:369", "isin_issuer_code_differs:COMB.N0000",
                                          "isin_issuer_code_differs:COMB.X0000"]
    assert got["COMB.N0000"].reasons == ["sec_id_identity_disputed:369", "isin_issuer_code_differs:NEWCO.N0000"]
    for d in got.values():                                                               # the cause is kept
        assert (d.observed_sec_ids, d.evidence_observation_ids) == ([369], [1, 2, 3])
        assert (d.first_evidence_at, d.last_evidence_at) == (AT, LATER)
    assert set(ii.disputed_sec_ids(obs)) == {369}


def test_reuse_guard_result_does_not_depend_on_order():
    a = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME), _io(4, 369, "LK0053N00005", COMB_NAME, LATER)],
         "NEWCO.N0000": [_io(2, 369, "LK9999N00001", NEWCO_NAME), _io(3, 369, "LK9999N00001", NEWCO_NAME, LATER)]}
    b = {s: list(reversed(obs)) for s, obs in reversed(list(a.items()))}
    da, db = ii.decide_securities(a), ii.decide_securities(b)
    assert da == db and [d.evidence_sha256 for d in da.values()] == [db[s].evidence_sha256 for s in da]


def test_reuse_guard_case3_without_isin_names_decide():
    obs = {"COMB.N0000": [_io(1, 369, None, COMB_NAME)], "NEWCO.N0000": [_io(2, 369, None, NEWCO_NAME)]}
    got = ii.decide_securities(obs)
    assert _status(got) == {"COMB.N0000": ("conflict", None), "NEWCO.N0000": ("conflict", None)}
    assert got["COMB.N0000"].reasons == ["sec_id_identity_disputed:369", "name_differs:NEWCO.N0000"]
    # an ISIN on one side only: the names are compared
    obs["COMB.N0000"] = [_io(1, 369, "LK0053N00005", COMB_NAME)]
    assert got["NEWCO.N0000"].reasons == ii.decide_securities(obs)["NEWCO.N0000"].reasons
    # the same name, differently written, establishes the same issuer
    obs["NEWCO.N0000"] = [_io(2, 369, None, " commercial bank of  ceylon plc ")]
    assert {d.link_status for d in ii.decide_securities(obs).values()} == {"evidenced"}
    # nothing comparable (e.g. a /api/financials sighting: secId only) does NOT establish it
    obs["NEWCO.N0000"] = [_io(2, 369, None, None)]
    assert ii.decide_securities(obs)["NEWCO.N0000"].reasons == ["sec_id_identity_disputed:369",
                                                                  "identity_evidence_insufficient:COMB.N0000"]
    # ... while a lone security has nothing to be confused with
    assert _status(ii.decide_securities({"NEWCO.N0000": [_io(2, 369, None, None)]})) == {"NEWCO.N0000": ("evidenced", 369)}


def test_reuse_guard_one_security_whose_own_identity_changes_under_its_sec_id():
    got = ii.decide_securities({"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME), _io(2, 369, "LK9999N00001", NEWCO_NAME)]})
    assert got["COMB.N0000"].reasons == ["sec_id_identity_disputed:369", "isin_issuer_code_differs:COMB.N0000"]
    got = ii.decide_securities({"COMB.N0000": [_io(1, 369, None, COMB_NAME), _io(2, 369, None, NEWCO_NAME)]})
    assert got["COMB.N0000"].link_status == "conflict"                     # a rename seen without an ISIN is flagged


def test_reuse_guard_counts_a_security_that_also_reported_another_sec_id():
    """NEWCO reported 500 and later 369: it is already a conflict itself, and it still disputes 369."""
    obs = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME)],
           "NEWCO.N0000": [_io(2, 500, "LK9999N00001", NEWCO_NAME), _io(3, 369, "LK9999N00001", NEWCO_NAME, LATER)]}
    got = ii.decide_securities(obs)
    assert _status(got) == {"COMB.N0000": ("conflict", None), "NEWCO.N0000": ("conflict", None)}
    assert got["NEWCO.N0000"].reasons == ["sec_ids_disagree", "sec_id_identity_disputed:369",
                                          "isin_issuer_code_differs:COMB.N0000"]
    assert got["NEWCO.N0000"].observed_sec_ids == [369, 500] and got["COMB.N0000"].evidence_observation_ids == [1, 3]


def test_reuse_guard_case4_new_sec_id_takes_the_normal_evidence_path():
    obs = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME)],
           "NEWCO.N0000": [_io(2, 999, "LK9999N00001", NEWCO_NAME)]}
    got = ii.decide_securities(obs)
    assert _status(got) == {"COMB.N0000": ("evidenced", 369), "NEWCO.N0000": ("evidenced", 999)}
    assert all(d.reasons == [] for d in got.values()) and ii.disputed_sec_ids(obs) == {}


def test_reuse_guard_case5_later_agreeing_evidence_does_not_revert_the_conflict():
    obs = {"COMB.N0000": [_io(1, 369, "LK0053N00005", COMB_NAME)],
           "NEWCO.N0000": [_io(2, 369, "LK9999N00001", NEWCO_NAME)]}
    first = ii.decide_securities(obs)
    # CSE later shows only COMB again, many times: the NEWCO sighting is still recorded, so the dispute stays
    obs["COMB.N0000"] += [_io(10 + i, 369, "LK0053N00005", COMB_NAME, LATER) for i in range(5)]
    again = ii.decide_securities(obs)
    assert {d.link_status for d in again.values()} == {"conflict"}
    assert again["COMB.N0000"].reasons == first["COMB.N0000"].reasons
    assert again["COMB.N0000"].evidence_sha256 == first["COMB.N0000"].evidence_sha256   # no new decision row


def test_reuse_guard_case6_filing_on_a_disputed_sec_id_is_never_evidenced_to_the_old_issuer():
    links = {"COMB.N0000": ("conflict", None), "NEWCO.N0000": ("conflict", None)}
    stale = {"COMB.N0000": ("evidenced", 369)}                      # decisions not yet re-resolved
    for sym_links, symbols in ((links, ["NEWCO.N0000"]), (links, []), (stale, ["COMB.N0000"]), (stale, [])):
        d = ii.decide_filing(50001, P369, symbols, sym_links, ISSUERS, {369})
        assert (d.status, d.issuer_id) == ("conflict", None), (symbols, sym_links)
    d = ii.decide_filing(50001, P369, [], stale, ISSUERS, {369})
    assert (d.basis, d.sec_id, d.reasons) == ("document_path_prefix", 369, ["sec_id_identity_disputed"])
    # another, undisputed secId is unaffected
    d = ii.decide_filing(50002, "cmt/upload_report_file/373_1762944976778.pdf", ["HNB.N0000"], LINKS, ISSUERS, {369})
    assert (d.status, d.issuer_id) == ("evidenced", ISSUERS[373])
