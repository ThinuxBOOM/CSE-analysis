"""
Phase 2 HB-5 (F6 orchestration and audit), database-free: every pure rule of worker/backfill_f6, and its static
boundary with planted violations. Runs on Linux and Windows; nothing here contacts PostgreSQL or the network.

    the package    files, versions, no entry point, no network, the static boundary, the frozen pins and interfaces
    F6 outcomes    F6.4's validate and reconcile reports -> the ledger events HB-5 records
    readiness      the current path version only; superseded and out-of-window items never hold an issuer back
    promotion      persisted -> validated -> reconciled; needs_validation only after a validation existed
    the funnel     every stage and stop reason; stage 7 ignores issuer_evidence_* (design section 23.1); one unit per
                   filing at its furthest document; the counts, dimensions and digest
    review lists   E3 cadence gaps, identity gaps, E1 against a baseline
    anomalies      class mapping, statuses, operational stops, F1's listing parser
    the rest       the window, the report writer, the L10 shape
"""
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.backfill_f6 import (ANOMALY_RULE, COVERAGE_RULE, RULE_VERSION, TOOL_VERSION, anomalies, audit,  # noqa: E402
                                coverage, preflight, promotion, readiness, reconcile, reports, snapshot, validation)
from worker.backfill_f6.errors import F6Refused  # noqa: E402
from worker.financial_backfill import keys, states  # noqa: E402

PACKAGE = os.path.dirname(os.path.abspath(preflight.__file__))
REPO = preflight.REPO
COLOMBO = timezone(timedelta(hours=5, minutes=30))
W = (date(2025, 3, 1), date(2025, 4, 30))
IN_W = datetime(2025, 3, 2, 10, 0, tzinfo=COLOMBO)
OUT_W = datetime(2025, 5, 2, 10, 0, tzinfo=COLOMBO)


# ================================================================================================ the package

def test_u1_the_package_is_a_library_with_its_versions_and_no_entry_point():
    files = sorted(f for f in os.listdir(PACKAGE) if f.endswith(".py"))
    assert files == ["__init__.py", "anomalies.py", "audit.py", "configuration.py", "coverage.py", "errors.py",
                     "preflight.py", "promotion.py", "readiness.py", "reconcile.py", "reports.py", "snapshot.py",
                     "validation.py"]
    assert (TOOL_VERSION, RULE_VERSION, COVERAGE_RULE, ANOMALY_RULE) == ("hb.f6.1", "hb.f6.1", "hb.coverage.1",
                                                                         "hb.anomaly.1")
    assert preflight.static_problems() == []
    assert not os.path.exists(os.path.join(REPO, "ops", "backfill"))          # HB-6's, not HB-5's
    for f in files:
        src = open(os.path.join(PACKAGE, f), encoding="utf-8").read()
        assert "rdv" + "_" not in src, f                                        # RDV-B3, docstrings included


@pytest.mark.parametrize("src, finding", [
    ("import requests\n", "network module"),
    ("from urllib import request\n", "network module"),
    ("from worker import cse_client\n", "worker.cse_client"),
    ("from ..backfill_transport import slice\n", "worker.backfill_transport"),
    ("from ..financial_backfill import owner\n", "worker.financial_backfill.owner"),
    ("from ..financial_asof import api\n", "worker.financial_asof"),
    ("from ..financial_truth_store import writer\n", "worker.financial_truth_store.writer"),
    ("from ..financial_truth import reconciliation\n", "worker.financial_truth.reconciliation"),
    ("from .. import extract_financial_candidates as f5cli\n", "worker.extract_financial_candidates"),
    ("from ..backfill_documents import worker\n", "worker.backfill_documents.worker"),
    ("from ..financial_truth_store import jobs\njobs.designate(c, 'x', 'note')\n", "jobs.designate"),
    ("from ..financial_truth_store import jobs\njobs.cleanup(c)\n", "jobs.cleanup"),
    ("from ..financial_truth_store import selection\nselection.run_issuer(c, k)\n", "selection.run_issuer"),
    ("from ..financial_truth import admission\nadmission.validate_run(r)\n", "admission.validate_run"),
    ("x = 'insert into financial_validation_runs values (1)'\n", "writes SQL"),
    ("x = 'update backfill_leases set state = 1'\n", "writes SQL"),
    ("x = 'delete from report_filings'\n", "writes SQL"),
    ("x = 4_346_836_117_002_313\n", "advisory-lock literal"),
    ("x = 'select pg_advisory_lock(1)'\n", "pg_advisory"),
    ("store.claim(c, i, l)\n", "claim"),
    ("f5cli._persist(stores, got)\n", "_persist"),
    ("PostgresIssuerStore(c).link_filing(1)\n", "link_filing"),
    ("x = 'rdv" + "_measure'\n", "rdv" + "_"),
    ("available_at = 1\n", "available_at"),
    ("if __name__ == '__main__':\n    pass\n", "entry point"),
])
def test_u2_every_planted_boundary_violation_is_found(src, finding):
    found = preflight.source_problems("planted.py", src)
    assert any(finding in p for p in found), found
    assert preflight.source_problems("planted.py", "x = 'not_persisted' + 'reclaimed'\n") == []


def test_u3_frozen_pins_and_interfaces(tmp_path):
    assert preflight.pin_problems() == [] and preflight.compat_problems() == []
    copy = tmp_path / "repo"
    for rel in preflight.PINNED_FILES:
        dst = copy / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(REPO, rel), dst)
    assert preflight.pin_problems(str(copy)) == []
    with open(copy / "worker" / "backfill_documents" / "planning.py", "a", encoding="utf-8") as f:
        f.write("\n")
    assert preflight.pin_problems(str(copy)) == ["frozen file worker/backfill_documents/planning.py changed (HB-B9: "
                                                 "HB-5 composes exactly the frozen code)"]
    assert set(preflight.TRANSITIONS_USED) <= states.TRANSITIONS
    with pytest.raises(F6Refused) as ei:
        preflight.require(None, lambda c: ["one", "two"])
    assert ei.value.codes == ["preflight"] and "one; two" in str(ei.value)
    assert preflight.require(None, lambda c: []) is None


# ================================================================================================ F6 outcomes

def test_u4_validate_reports_become_ledger_events():
    ok = {"state": "succeeded", "job_id": "j", "validation_run_key": "v"}
    assert validation.outcome_event(ok) == ("succeeded", {"f6_job_id": "j", "validation_run_key": "v"}, None)
    assert validation.outcome_event(dict(ok, state="already_present"))[0] == "succeeded"
    state, refs, why = validation.outcome_event({"state": "failed", "job_id": "j", "reason": "error",
                                                 "error": "InputError: x" * 100})
    assert (state, refs) == ("failed", {"f6_job_id": "j"}) and why.startswith("F6.4 validate error: InputError")
    assert len(why) <= validation.MAX_REASON
    assert validation.outcome_event({"state": "refused", "job_id": "j", "reason": "busy"}) is None
    with pytest.raises(ValueError):
        validation.outcome_event({"state": "started", "job_id": "j"})


def test_u5_reconcile_reports_become_ledger_events():
    assert reconcile.outcome_event({"state": "succeeded"}) == ("succeeded", None)
    state, why = reconcile.outcome_event({"state": "failed", "failed": [{"issuer_id": "i-1", "state": "failed"}],
                                          "failed_validations": []})
    assert state == "failed" and "i-1" in why and len(why) <= reconcile.MAX_REASON
    assert reconcile.outcome_event({"state": "refused", "reason": "busy"}) is None
    with pytest.raises(ValueError):
        reconcile.outcome_event({"state": "abandoned"})


# ================================================================================================ readiness

def _filing(fid, path="p", uploaded=IN_W, issuer="i-1"):
    return {"cse_filing_id": fid, "path": path, "uploaded_at": uploaded, "issuer_id": issuer}


def _item(fid, path, state):
    return {"cse_filing_id": fid, "path_sha256": keys.path_version(path), "state": state}


def test_u6_readiness_judges_the_current_path_version_of_every_in_window_filing():
    assert readiness.FINAL_DOCUMENT == {"excluded", "retrieval_failed", "consumer_failed", "cleanup_failed", "failed",
                                        "persisted", "validated", "reconciled", "needs_validation"}
    assert readiness.FINAL_DOCUMENT == set(states.FINAL["document"]) | set(readiness.PERSISTED)
    filings = [_filing(1, "new"), _filing(2), _filing(3, uploaded=OUT_W), _filing(4, None), _filing(5, issuer=None),
               _filing(6, issuer="i-2")]
    items = [_item(1, "old", "pending"), _item(1, "new", "persisted"),       # 1: superseded old version ignored
             _item(2, "p", "validated"),
             _item(3, "p", "pending"),                                           # 3: left W - not judged at all
             _item(4, None, "excluded"),                                         # 4: no document, excluded: final
             _item(5, "p", "retry_wait"),                                        # 5: no issuer, not final
             _item(6, "p", "blocked")]                                           # 6: blocked is not final
    rd = readiness.evaluate(filings, items, W)
    assert sorted(rd.filings) == [1, 2, 4, 5, 6]
    assert rd.issuers == ["i-1", "i-2"] and rd.ready_issuers == ["i-1"] and not rd.issuer_ready("i-2")
    assert rd.open_filings() == [5, 6] and rd.open_filings("i-1") == [] and not rd.all_final
    assert rd.summary() == {"window": ["2025-03-01", "2025-04-30"], "filings": 5, "open_filings": 2, "issuers": 2,
                            "ready_issuers": 1, "all_final": False}
    no_item = readiness.evaluate([_filing(7)], [], W)                            # planning has not reached it yet
    assert no_item.filings[7] == {"issuer_id": "i-1", "state": None, "final": False} and not no_item.all_final
    for state in states.STATES["document"]:
        got = readiness.evaluate([_filing(8)], [_item(8, "p", state)], W)
        assert got.all_final == (state in readiness.FINAL_DOCUMENT), state


# ================================================================================================ promotion

@pytest.mark.parametrize("state, canonical, ever, rec, want", [
    ("persisted", None, False, False, []),                              # never validated: stays persisted
    ("persisted", "vr", True, False, ["validated"]),
    ("persisted", "vr", True, True, ["validated", "reconciled"]),
    ("needs_validation", "vr", True, False, ["validated"]),
    ("validated", "vr", True, False, []),
    ("validated", "vr", True, True, ["reconciled"]),
    ("reconciled", "vr", True, True, []),
    ("persisted", None, True, False, ["needs_validation"]),             # M4: a validation existed, none canonical
    ("validated", None, True, False, ["needs_validation"]),
    ("reconciled", None, True, False, ["needs_validation"]),
    ("needs_validation", None, True, False, []),
])
def test_u7_promotions_follow_f6_evidence(state, canonical, ever, rec, want):
    assert promotion.decide(state, canonical=canonical, ever_validated=ever, reconciled=rec) == want
    path = [state] + want
    assert all(states.allowed("document", a, b, "promote") for a, b in zip(path, path[1:]))


# ================================================================================================ the funnel

def _t2(admitted=False, inel=(), norm=(), adm=(), lifted=(), kind=None, eligibility=None):
    return {"admitted": admitted, "ineligible_reasons": list(inel), "normalization_reasons": list(norm),
            "admission_reasons": list(adm), "lifted_reasons": list(lifted), "value_kind": kind,
            "eligibility": eligibility or ("ineligible" if inel else "eligible"), "operations_route": None}


def test_u8_refusal_reasons_follow_f63_and_the_real_data_validation():
    assert coverage.refusal_reasons(_t2(admitted=True, kind="numeric")) == ()
    row = _t2(inel=["role_untrusted", "operations_section_derived_on_total_row"],
              lifted=["operations_section_derived_on_total_row"], norm=["currency_not_reported"],
              adm=["validation_ineligible", "normalization_not_admissible", "issuer_link_path_prefix_only"])
    assert coverage.refusal_reasons(row) == ("role_untrusted", "currency_not_reported", "issuer_link_path_prefix_only")
    assert coverage.refusal_reasons(_t2(norm=["scale_unresolved"], adm=["issuer_link_not_evidenced:unresolved"])) == \
        ("issuer_link_not_evidenced:unresolved",)                       # normalisation reasons only when not admissible


def test_u9_stage_7_ignores_issuer_evidence_and_issuer_only_needs_issuer_reasons_alone():
    assert coverage.eligible_issuer_aside(_t2(inel=["issuer_evidence_unresolved"]))
    assert not coverage.eligible_issuer_aside(_t2(inel=["issuer_evidence_unresolved", "role_untrusted"]))
    assert coverage.eligible_issuer_aside(_t2(norm=["currency_not_reported"], eligibility="normalization_required"))
    assert coverage.issuer_only(("issuer_evidence_unresolved", "issuer_link_not_evidenced:unresolved"))
    assert not coverage.issuer_only(("issuer_evidence_unresolved", "role_untrusted")) and not coverage.issuer_only(())
    # an unresolved-issuer document stops at stage 8 as issuer evidence, never at stage 7 (design D3, section 23.1)
    agg = coverage.run_aggregate([_t2(inel=["issuer_evidence_unresolved"],
                                      adm=["validation_ineligible", "issuer_link_not_evidenced:unresolved"])])
    run = dict(RUN, validation=agg, link_category="unresolved/none")
    assert coverage.evaluate_run(run, "cfg") == (7, (8, "issuer_evidence:unresolved/none"))


RUN = {"classification_status": "classified", "document_status": "extracted", "candidates": 3, "validation": None,
       "reconciled": False, "link_category": "both"}
OK_VR = coverage.finish_aggregate({"eligible_issuer_aside": True, "admitted": True, "issuer_only": False, "f61": set(),
                                   "rules": set()})


@pytest.mark.parametrize("change, designated, want", [
    ({"classification_status": "unreadable"}, "c", (3, (4, "unreadable"))),
    ({"classification_status": "partial", "document_status": "ocr_untrusted"}, "c", (4, (5, "ocr_untrusted"))),
    ({"document_status": "no_statements"}, "c", (4, (5, "no_statements"))),
    ({"document_status": "partial", "candidates": 0}, "c", (5, (6, "zero_candidates"))),
    ({}, "c", (6, (7, "not_validated"))),
    ({"validation": coverage.finish_aggregate({"eligible_issuer_aside": False, "admitted": False, "issuer_only": False,
                                               "f61": {"role_untrusted", "duration_months_missing"}, "rules": set()})},
     "c", (6, (7, "f6.1:duration_months_missing,role_untrusted"))),
    ({"validation": coverage.finish_aggregate({"eligible_issuer_aside": True, "admitted": False, "issuer_only": False,
                                               "f61": set(), "rules": {"currency_not_reported"}})},
     "c", (7, (8, "rules:currency_not_reported"))),
    ({"validation": coverage.finish_aggregate({"eligible_issuer_aside": True, "admitted": False, "issuer_only": True,
                                               "f61": set(), "rules": {"scale_unresolved"}}),
      "link_category": "path_prefix_only"}, "c", (7, (8, "issuer_evidence:path_prefix_only"))),
    ({"validation": OK_VR}, None, (8, (9, "no_designated_configuration"))),
    ({"validation": OK_VR}, "c", (8, (9, "not_reconciled"))),
    ({"validation": OK_VR, "reconciled": True}, "c", (9, None)),
])
def test_u10_every_stage_of_an_f5_run(change, designated, want):
    assert coverage.evaluate_run(dict(RUN, **change), designated) == want


def test_u11_documents_without_an_f5_run():
    rec = {"outcome": "download_failed", "failure_category": "not_found", "consumer_status": "not_run",
           "consumer_error_class": None}
    assert coverage.evaluate_item({"state": "retrieval_failed", "retrieval": rec}) == (2, (3, "not_found"))
    odd = dict(rec, outcome="hash_failed", failure_category="OSError: disk on fire")
    assert coverage.evaluate_item({"state": "retry_wait", "retrieval": odd}) == (2, (3, "hash_failed"))
    text = dict(rec, outcome="consumer_failed", failure_category=None, consumer_status="failed",
                consumer_error_class="TextExtractionError")
    assert coverage.evaluate_item({"state": "consumer_failed", "retrieval": text}) == (
        3, (4, "consumer_failed:TextExtractionError"))
    assert coverage.evaluate_item({"state": "consumer_failed", "retrieval": dict(
        text, consumer_error_class="ValueError")}) == (4, (5, "consumer_failed:ValueError"))
    consumed = dict(rec, outcome="succeeded", failure_category=None, consumer_status="succeeded")
    assert coverage.evaluate_item({"state": "retry_wait", "retrieval": consumed}) == (3, (4, "not_persisted"))
    for state, reason in (("pending", "not_attempted"), ("discovered", "not_attempted"), ("requesting", "in_flight"),
                          ("processing", "in_flight"), ("blocked", "blocked"), ("abandoned", "abandoned"),
                          ("failed", "item_failed")):
        assert coverage.evaluate_item({"state": state, "retrieval": None}) == (2, (3, reason))
    assert coverage.evaluate_classification({"classification_status": "unreadable"}) == (3, (4, "unreadable"))
    assert coverage.evaluate_classification({"classification_status": "classified"}) == (4, (5, "no_f5_run"))


def test_u12_one_unit_per_filing_at_its_furthest_document():
    assert coverage.path_refusal(None) == "no_document" and coverage.path_refusal("/abs/x.pdf") == "invalid_path"
    assert coverage.path_refusal("cmt/upload_report_file/369_1.pdf") is None
    f = {"path": "cmt/upload_report_file/369_1.pdf"}
    docs = [(3, (4, "unreadable"), {"key": "run:b"}), (8, (9, "not_reconciled"), {"key": "run:a"}),
            (2, (3, "not_attempted"), {"key": "item:x"})]
    assert coverage.evaluate_filing(f, docs) == (8, (9, "not_reconciled"), {"key": "run:a"})
    ties = [(9, None, {"key": "run:b"}), (9, None, {"key": "run:a"}), (8, (9, "not_reconciled"), {"key": "run:0"})]
    assert coverage.evaluate_filing(f, ties)[2] == {"key": "run:a"}
    assert coverage.evaluate_filing(f, list(reversed(ties)))[2] == {"key": "run:a"}
    pending = [(2, (3, "not_attempted"), {"key": "item:x"})]
    assert coverage.evaluate_filing({"path": None}, pending) == (1, (2, "no_document"), None)
    assert coverage.evaluate_filing(f, pending) == (2, (3, "not_attempted"), {"key": "item:x"})
    assert coverage.evaluate_filing(f, []) == (2, (3, "not_attempted"), None)
    assert coverage.evaluate_filing({"path": None}, [(5, (6, "zero_candidates"), {"key": "run:z"})])[0] == 5


def _row(fid, reached, stop, **dims):
    base = {d: "-" for d in coverage.DIMENSIONS}
    base.update(dims)
    return dict(base, cse_filing_id=fid, reached=reached, stop=stop)


def test_u13_funnel_and_dimension_counts():
    table = [_row(1, 1, [2, "no_document"], source="feed"), _row(2, 9, None, source="both"),
             _row(3, 7, [8, "rules:x"], source="both"), _row(4, 7, [8, "rules:x"], source="feed")]
    got = coverage.funnel_counts(table)
    assert (got["filings"], got["complete"]) == (4, 1)
    assert got["reached"] == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 3, "6": 3, "7": 3, "8": 1, "9": 1}
    assert got["stops"] == {"2": {"no_document": 1}, "8": {"rules:x": 2}}
    dims = coverage.dimension_counts(table)
    assert dims["source"] == {"both": {"filings": 2, "complete": 1, "stops": {"8": 1}},
                              "feed": {"filings": 2, "complete": 0, "stops": {"2": 1, "8": 1}}}
    assert set(dims) == set(coverage.DIMENSIONS)


def test_u14_link_categories_and_sources():
    assert coverage.link_category("evidenced", "document_path_prefix") == "path_prefix_only"
    assert coverage.link_category("unresolved", "none") == "unresolved/none"
    assert coverage.link_category("conflict", "both") == "conflict/both"
    assert coverage.link_category(None, None) == "no_decision"
    assert coverage.link_category("unresolved", "none", held=True) == "held"
    assert [coverage.source_of(e) for e in (["getFinancialAnnouncement", "financials"], ["financials"],
                                            ["getFinancialAnnouncement"], None)] == ["both", "listing", "feed", "none"]


def test_u15_the_digest_is_a_pure_function_of_the_table_and_what_judged_it():
    cov = {"rule": COVERAGE_RULE, "window": ["2025-03-01", "2025-04-30"], "designated_configuration": "c",
           "versions": {"a": "1"}, "table": [_row(1, 9, None), _row(2, 1, [2, "no_document"])]}
    d = coverage.table_digest(cov)
    assert coverage.table_digest(dict(cov, versions={"a": "1"})) == d
    for change in ({"window": ["2025-03-01", "2025-05-31"]}, {"designated_configuration": None},
                   {"versions": {"a": "2"}}, {"table": cov["table"][:1]}, {"rule": "hb.coverage.2"}):
        assert coverage.table_digest(dict(cov, **change)) != d, change


# ================================================================================================ review lists

def test_u16_review_lists_are_labelled_heuristics():
    ends = {369: [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2024, 3, 31), date(2024, 6, 30)],
            370: [date(2023, 12, 31), date(2024, 12, 31)]}                     # two ends: no cadence
    got = coverage.possible_missing_filings(ends)
    assert got == [{"issuer_sec_id": 369, "after": "2023-09-30", "before": "2024-03-31", "gap_months": 6,
                    "cadence_months": 3}]
    tie = coverage.possible_missing_filings({1: [date(2020, 1, 31), date(2020, 4, 30), date(2020, 10, 31),
                                                 date(2021, 4, 30)]})            # gaps 3, 6, 6: mode 6
    assert tie == []
    facts = [{"issuer_sec_id": 369, "concept_key": "revenue", "period_kind": "duration", "duration_months": 12,
              "period_end": date(y, 12, 31)} for y in (2021, 2023)]
    assert coverage.identity_gaps(facts, {369: {2020, 2021, 2022, 2023, 2024}}) == [
        {"issuer_sec_id": 369, "concept_key": "revenue", "period_kind": "duration", "duration_months": 12,
         "period_end": "12-31", "missing_years": [2022]}]
    baseline = {101: IN_W.isoformat(), 102: IN_W, 103: OUT_W}
    assert coverage.disappeared_since_baseline(baseline, {101}, W) == [102]


# ================================================================================================ anomalies

def test_u17_classes_statuses_and_operational_stops():
    assert anomalies.CLASS_OF_RDV == {"A": 1, "B": 2, "C": 5, "D": 4, "E": 6}
    assert anomalies.STATUS_OF_CLASS == {1: "handled", 2: "rejected", 3: "open", 4: "asof_input",
                                         5: "owner_decision", 6: "change_control"}
    assert all(__import__("re").fullmatch(r"[a-z][a-z_]{1,30}", s) for s in anomalies.STATUS_OF_CLASS.values())
    assert set(anomalies.STATUS_OF_CLASS) == set(anomalies.CLASS_MEANING) == {1, 2, 3, 4, 5, 6}
    with pytest.raises(ValueError):
        anomalies.Catalogue().add("x", "p", 7, 0, "what")
    cat = anomalies.Catalogue()
    cat.rdv("P-0", "pattern", "D", 2, "what", {"1": "x"}, extra=3)
    assert cat.records[0]["anomaly_class"] == 4 and cat.records[0]["status"] == "asof_input"
    assert cat.records[0]["counts"] == {"catalogue_class": "D", "extra": 3}
    for stop, want in (((2, "no_document"), True), ((3, "not_found"), True), ((3, "blocked"), True),
                       ((3, "not_attempted"), False), ((3, "in_flight"), False), ((4, "unreadable"), False),
                       ((4, "consumer_failed:TextExtractionError"), True), ((4, "not_persisted"), True),
                       ((5, "no_statements"), False), ((5, "consumer_failed:ValueError"), True),
                       ((7, "f6.1:x"), False), ((8, "issuer_evidence:x"), False), (None, False)):
        assert anomalies.operational_stop(stop) is want, stop


def test_u18_listing_ids_use_f1s_own_parser():
    body = {"reqFinancial": [], "infoAnnualData": [{"id": 5, "path": "a"}], "infoQuarterlyData": [{"id": "7"}],
            "infoOtherData": [{"id": "x"}, "junk"], "infoWebLink": []}
    assert anomalies.listing_ids(body, "COMB.N0000") == {5, 7}
    assert anomalies.listing_ids({"unexpected": 1}, "COMB.N0000") is None


# ================================================================================================ the rest

def test_u19_the_window_is_the_arming_in_force_or_given_never_invented():
    armed = {"window_first_date": W[0], "window_last_date": W[1]}
    assert snapshot.window_of(armed) == W and snapshot.window_of(None, W) == W
    assert snapshot.window_of(armed, (date(2024, 1, 1), date(2024, 1, 31))) == (date(2024, 1, 1), date(2024, 1, 31))
    for arming, window in (({"window_first_date": None, "window_last_date": None}, None), (None, None),
                           (armed, (W[1], W[0])), (None, ("2025-03-01", "2025-04-30"))):
        with pytest.raises(F6Refused) as ei:
            snapshot.window_of(arming, window)
        assert ei.value.codes == ["window"]
    assert snapshot.in_window(IN_W, W) and not snapshot.in_window(OUT_W, W) and not snapshot.in_window(None, W)


def test_u20_reports_are_written_only_outside_the_repository_to_a_new_file(tmp_path):
    target = tmp_path / "r.json"
    assert reports.write_json(target, {"a": 1}) == os.path.realpath(target)
    with pytest.raises(FileExistsError):
        reports.write_json(target, {"a": 2})
    for inside in (os.path.join(REPO, "r.json"), os.path.join(REPO, "docs", "r.json")):
        with pytest.raises(F6Refused) as ei:
            reports.write_json(inside, {"a": 1})
        assert ei.value.codes == ["report_path"] and not os.path.exists(inside)


def test_u21_l10_rows_hold_subjects_and_counts_never_descriptions():
    rec = {"detector_id": "P-1", "detector_version": "hb.anomaly.1", "anomaly_class": 6, "status": "change_control",
           "count": 16, "subjects": {"52713": 16}, "examples": [{"column_end": "2026-12-31"}], "counts": {"x": 1},
           "pattern": "period interpretation", "what": "long text", "follow_up": "F3 change control"}
    assert audit.l10_row(rec) == {"detector_id": "P-1", "detector_version": "hb.anomaly.1", "anomaly_class": 6,
                                  "status": "change_control",
                                  "subject_ids": {"subjects": {"52713": 16}, "examples": [{"column_end": "2026-12-31"}]},
                                  "counts": {"count": 16, "x": 1}}
