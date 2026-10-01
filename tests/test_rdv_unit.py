"""
Real-data validation (docs/REAL_DATA_VALIDATION_DESIGN.md section 15): the database-free tests.
- The static boundary checks and the parity of the harness's own helpers with F6.3 always run.
- The tests that need the evidence are skipped unless CSE_F6_CORPUS_DIR and CSE_F0_CAPTURE_DIR point at it. It is
  not in Git, and these tests never contact CSE.
"""
import json
import os
import re
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f63_factories as F  # noqa: E402
import rdv_evidence as E  # noqa: E402
import rdv_measure as M  # noqa: E402
from worker.financial_truth import admission  # noqa: E402

HARNESS = ("rdv_evidence.py", "rdv_measure.py", "rdv_report.py")
BUNDLE = E.locate()
needs_bundle = pytest.mark.skipif(BUNDLE is None, reason=f"{E.CORPUS_ENV} and {E.CAPTURE_ENV} are not set (the "
                                                         f"real-data evidence is not in the repository)")
MANIFEST_SHA256 = "fed17a92bcc1a34462b9e4aa865d3a62702f44b5549f521c3e33e543c51c0435"

# design section 6.4: the expected filing -> issuer decisions (status, basis, path secId, listing symbols, reasons)
UNRESOLVED_PATH = {32216: 677, 45857: 1045, 47478: 694, 48576: 863, 49086: 388, 50553: 460, 50922: 460, 51372: 495,
                   51712: 948, 52157: 443, 52319: 500, 52713: 389, 52749: 390, 52860: 495, 52888: 547, 53067: 547,
                   53096: 781}
EXPECTED_DECISIONS = {
    **{f: ("unresolved", "document_path_prefix", sec, [], ["no_issuer_for_sec_id"])
       for f, sec in UNRESOLVED_PATH.items()},
    **{f: ("unresolved", "none", None, [], ["no_document_path_prefix"]) for f in (48292, 49117, 52620, 53129)},
    **{f: ("evidenced", "both", 369, ["COMB.N0000"], []) for f in (47026, 49384, 50738)},
    50613: ("evidenced", "listing_symbol_sec_id", None, ["COMB.N0000"], ["no_document_path_prefix"]),
    52684: ("evidenced", "document_path_prefix", 378, [], []),
}


def _source(name):
    with open(os.path.join(os.path.dirname(__file__), name), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------------------------------------ boundary (static)

def test_the_harness_never_contacts_cse_or_opens_a_network_connection():
    """RDV-B4. The only cse_client name used is the CSEResponse dataclass, which wraps captured bodies for F1's own
    parser; no request function is referenced."""
    for name in HARNESS:
        src = _source(name)
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|http|socket|aiohttp|httpx|ssl)\b", src, re.M), name
        assert re.findall(r"cse_client\.(\w+)", src) == [], name
        assert not re.search(r"\b(get_company_info_summary|get_company_financials|get_financial_announcements|"
                             r"discover_feed_window|discover_company_listing|discover)\(", src), name


def test_the_harness_writes_frozen_tables_only_through_the_frozen_stores():
    """RDV-B2: no INSERT / UPDATE / DELETE / TRUNCATE in the harness's own SQL. Rows of frozen tables are written by
    the frozen F1 / P2 / F5 / F3 stores and F6.4's jobs only. The harness creates only its throwaway database."""
    pattern = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|truncate)\b", re.I)
    for name in HARNESS:
        assert not pattern.search(_source(name)), name
    assert re.findall(r"create database (\S+)", _source("rdv_evidence.py")) == ["{name}"]
    assert 'DB_PREFIX = "rdv_"' in _source("rdv_evidence.py")


def test_no_production_module_imports_the_harness():
    """RDV-B3: the harness is validation tooling. Nothing in worker/ or ops/ imports it."""
    root = os.path.join(os.path.dirname(__file__), "..")
    for top in ("worker", "ops"):
        for dirpath, _, files in os.walk(os.path.join(root, top)):
            for f in files:
                if f.endswith((".py", ".sh")):
                    with open(os.path.join(dirpath, f), encoding="utf-8", errors="replace") as fh:
                        assert "rdv_" not in fh.read(), os.path.join(dirpath, f)


def test_the_report_runner_refuses_to_write_inside_the_repository():
    """G-1: the report holds CSE-derived labels, so it is never written into the repository."""
    import rdv_report
    with pytest.raises(SystemExit, match="outside the repository"):
        rdv_report.main(["--out", os.path.join(E.REPO, "rdv_report.json")])


# ------------------------------------------------------------------------------------------------ the manifest

def test_the_manifest_is_complete_and_pinned():
    m = E.manifest()
    assert len(m["corpus"]) == 26 and len(m["captures"]) == 4 and len(m["company_info"]) == 8
    assert sorted(v[0] for v in E.CAPTURES.values()) == ["E-B1", "E-B2", "E-B3", "E-B4"]
    assert E.manifest_digest() == MANIFEST_SHA256           # any change to the pinned evidence is deliberate
    for name in E.CAPTURES:
        assert E.captured_at(name).isoformat().startswith("2026-09-24T1")


def test_the_company_info_fixtures_in_git_are_the_pinned_evidence():
    """E-C is in Git since Stage E: checked on every run, with LF-normalised hashes."""
    for rel, (symbol, sha, _, _) in E.COMPANY_INFO.items():
        path = os.path.join(E.REPO, *rel.split("/"))
        assert E.sha256_of(path, lf=True)[0] == sha, rel
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["reqSymbolInfo"]["symbol"] == symbol, rel
        assert E.company_info_observed_at(rel).utcoffset().total_seconds() == 19800, rel


def test_the_sample_fixture_is_not_evidence():
    """tests/fixtures/sample_companyInfoSummery.json is a hand-made sample, not a CSE capture: never used."""
    assert all("sample_" not in rel for rel in E.COMPANY_INFO)


# ------------------------------------------------------------------------------------------------ helper parity

def _row(c):
    return {"admitted": c.admission.admitted, "ineligible_reasons": list(c.validation.ineligible_reasons),
            "normalization_reasons": list(c.validation.normalization_reasons),
            "admission_reasons": list(c.admission.reasons), "lifted_reasons": list(c.admission.lifted_reasons)}


def test_the_measured_refusal_reasons_are_f63s_on_synthetic_documents():
    """rdv_measure.refusal_reasons over stored T2 columns equals F6.3 admission.refusal_reasons. The documents cover:
    an admissible link, an unresolved link, a path-prefix-only link, a printed nil, an ambiguous mapping and an
    unresolved F5 status."""
    ambiguous = F.Doc(9006)
    st = ambiguous.statement()
    ci = ambiguous.column(st)
    ambiguous.value(st, ambiguous.row(st, "Earnings per share"), ci, "1.23", None, mapping_status="ambiguous",
                    status="ambiguous")
    docs = [F.Doc(9001).one(), F.Doc(9002, link_status="unresolved", link_basis="document_path_prefix").one(),
            F.Doc(9003, link_basis="document_path_prefix").one(), F.Doc(9004).one("-"),
            F.Doc(9005).one(status="unresolved"), ambiguous]
    seen = Counter()
    for d in docs:
        for c in d.validate().candidates:
            assert M.refusal_reasons(_row(c)) == admission.refusal_reasons(c)
            seen.update(admission.refusal_reasons(c))
    assert {"issuer_evidence_unresolved", "issuer_link_not_evidenced:unresolved", "issuer_link_path_prefix_only",
            "candidate_status_unresolved", "candidate_status_ambiguous", "mapping_not_single_concept"} <= set(seen)


def test_issuer_only_means_nothing_but_issuer_evidence():
    assert M.issuer_only(("issuer_evidence_unresolved", "issuer_link_not_evidenced:unresolved"))
    assert M.issuer_only(("issuer_link_path_prefix_only",))
    assert not M.issuer_only(())
    assert not M.issuer_only(("issuer_link_path_prefix_only", "role_untrusted"))


def test_the_projection_maps_database_values_and_leaves_shared_content_raw():
    s = M._Subst()
    s.add("u-1", "run:1")
    s.add("h", "vr.output:1")
    s.add("h", "vr.output:2")                    # two labels share it: content-only, left raw
    s.add("h", "vr.output:3")
    assert s.walk({"a": ["u-1", "h", "values_disagree:u-1:x", 3]}) == {
        "a": ["run:1", "h", "values_disagree:run:1:x", 3]}


# ---------------------------------------------------------------------------------------- the evidence (optional)

@needs_bundle
def test_the_bundle_is_exactly_the_pinned_evidence():
    assert E.manifest_problems(BUNDLE) == []


@needs_bundle
def test_f1_parses_every_replayed_listing_item():
    runs = E.f1_runs(BUNDLE)
    assert [(r.evidence, r.key) for r in runs][:8] == [("E-B1", str(y)) for y in range(2019, 2027)]
    assert sum(len(r.observations) for r in runs if r.evidence == "E-B1") == 12133
    assert sum(len(r.observations) for r in runs if r.evidence != "E-B1") == 789
    assert all(not r.rejected and not r.unrecognised_list_keys for r in runs)
    feed_ids = {o.cse_filing_id for r in runs if r.evidence == "E-B1" for o in r.observations}
    assert set(E.corpus(BUNDLE)) <= feed_ids                    # every corpus filing is in the real feed capture


@needs_bundle
def test_the_identifier_evidence_is_what_the_captures_carry():
    obs = E.identifier_observations(BUNDLE)
    assert Counter(o["source_endpoint"] for o in obs) == {"allSecurityCode": 327, "companyInfoSummery": 16,
                                                          "financials": 8}
    secids = {(o["symbol"], o["cse_sec_id"]) for o in obs if o["cse_sec_id"] is not None}
    assert secids == {("AAIC.N0000", 364), ("CARS.N0000", 502), ("COMB.N0000", 369), ("CTEA.N0000", 489),
                      ("DIPD.N0000", 670), ("HAYL.N0000", 505), ("HNB.N0000", 373), ("JKH.N0000", 508),
                      ("LOLC.N0000", 378), ("NEST.N0000", 487), ("SAMP.N0000", 431), ("SOY.N0000", 488)}


@needs_bundle
def test_the_expected_issuer_decisions_of_design_section_6_4():
    """The frozen F5 decision functions over the real evidence, with no database. The PostgreSQL test then requires
    the persisted decisions to be exactly these."""
    got = {f: (d.status, d.basis, d.path_sec_id, d.listing_symbols, d.reasons)
           for f, d in E.predicted_decisions(BUNDLE).items()}
    assert got == EXPECTED_DECISIONS
