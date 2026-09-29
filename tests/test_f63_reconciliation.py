"""
Stage F6.3 - source observations and reconciliation (f6.reconciliation.1), on synthetic F5-shaped inputs
(tests/f63_factories.py). Pure: no database, network, clock or document.

The invariants at the end state explicitly what reconciliation must never do: pick a winner in a conflict, change a
reported value, convert a currency, treat nil as zero, derive a period, merge scopes, use role as identity, or use a
document's type (or recency, or audit status) as precedence.
"""
import copy
import os
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f63_factories import Doc, configuration, reconcile, single  # noqa: E402
from worker.financial_truth import observations as obs  # noqa: E402
from worker.financial_truth import reconciliation as rec  # noqa: E402
from worker.financial_truth.versions import VersionSet  # noqa: E402

D = Decimal


def doc(n, raw="1,234", **kw):
    """Document n (its own filing and SHA-256) holding one revenue value."""
    kw.setdefault("filing", 9000 + n)
    one = {k: kw.pop(k) for k in ("concept", "label", "column", "stmt_scale", "section", "currency") if k in kw}
    d = Doc(doc=n, **kw)
    return d.one(raw, one.pop("concept", "revenue"), **one)


def two_rows(raws, labels=("Turnover", "Revenue"), **kw):
    """One document printing one fact twice (CTC's 'Turnover' and 'Revenue' both map to revenue)."""
    d = Doc(**kw)
    st = d.statement()
    ci = d.column(st)
    for label, raw in zip(labels, raws):
        d.value(st, d.row(st, label), ci, raw)
    (so,) = d.observations()
    return so


# ------------------------------------------------------------------------------------------------ source observations

def test_single_member_so_keeps_the_reported_value_verbatim_and_its_interval():
    d = doc(1, "(1,234.5)")
    (so,) = d.observations()
    cand = d.candidates[0]
    assert (so.observation_status, so.value_kind, len(so.members)) == ("consistent", "numeric", 1)
    rv = so.reported
    assert (rv.raw_value, rv.parsed_value, rv.representation_class, rv.printed_decimals, rv.sign_as_printed,
            rv.reported_scale, rv.scale_basis, rv.currency, rv.value_type) == \
        (cand["raw_value"], cand["parsed_value"], cand["representation_class"], cand["printed_decimals"],
         cand["sign_as_printed"], cand["reported_scale"], cand["scale_basis"], cand["reported_currency"],
         cand["value_type"])
    assert str(rv.parsed_value) == "-1234.5" and rv.raw_value == "(1,234.5)"
    assert (so.normalized_value, so.half_unit, so.interval_low, so.interval_high) == \
        (D("-1234500.0"), D("50"), D("-1234550.0"), D("-1234450.0"))
    assert so.representative_member == so.members[0].source_key


def test_so_records_its_run_filing_document_versions_and_members():
    d = doc(1)
    vr = d.validate()
    (so,) = obs.build(vr)
    m = so.members[0]
    assert (so.f5_run.f5_run_id, so.cse_filing_id, so.document_sha256) == (d.run_id, 9001, d.sha)
    assert so.validation_run_key == vr.key and so.versions == VersionSet()
    assert so.document.document_type == "interim_financial_statements"
    assert (m.statement_index, m.row_index, m.column_index, m.value_ordinal, m.value_kind) == (0, 0, 0, 0, "numeric")
    assert m.candidate_validation_key == vr.candidates[0].key
    assert (m.role, m.audit_label_reported, m.restated, m.operations_route, m.maturity_basis) == \
        ("current", "unaudited", False, "none", "not_applicable")
    assert so.so_key and so.output_hash and so.so_key != so.output_hash


def test_members_agreeing_exactly_are_one_consistent_so():
    so = two_rows(("1,234", "1,234"))
    assert (so.observation_status, len(so.members), so.annotations) == ("consistent", 2, ())
    assert so.representative_member == so.members[0].source_key and so.normalized_value == D(1234000)


def test_members_agreeing_within_precision_pick_the_finest_and_intersect():
    so = two_rows(("1,234", "1,234.2"))                        # half-units 500 and 50
    assert so.observation_status == "consistent" and so.annotations == ("agreement_within_precision_only",)
    assert so.representative_member == so.members[1].source_key and so.normalized_value == D("1234200.0")
    assert (so.interval_low, so.interval_high, so.precision) == (D("1234150.0"), D("1234250.0"), D("50"))


def test_precision_tie_with_different_values_has_no_representative():
    so = two_rows(("1,234", "1,235"))                          # |1000| <= 500 + 500: agree, same precision
    assert so.observation_status == "consistent" and "representative_ambiguous" in so.annotations
    assert (so.representative_member, so.reported, so.normalized_value) == (None, None, None)
    assert (so.interval_low, so.interval_high) == (D(1234500), D(1234500))


def test_members_that_disagree_make_an_internally_conflicting_so():
    so = two_rows(("1,234", "1,300"))
    assert (so.observation_status, so.value_kind) == ("internally_conflicting", "numeric")
    assert (so.representative_member, so.interval_low, so.interval_high, so.normalized_value) == (None,) * 4
    (c,) = so.comparisons
    assert c.outcome == "disagree" and c.comparison.abs_difference == D(66000) and c.comparison.tolerance == D(1000)


def test_nil_mixed_with_numeric_is_internally_conflicting():
    so = two_rows(("-", "1,234"))
    assert (so.observation_status, so.value_kind) == ("internally_conflicting", None)
    assert so.comparisons[0].reason == "nil_vs_numeric"


def test_all_nil_members_make_a_nil_so():
    so = two_rows(("-", "Nil"))
    assert (so.observation_status, so.value_kind, so.normalized_value, so.interval_low) == \
        ("consistent", "nil", None, None)
    assert so.nil_forms == (("reported_nil", "hyphen_minus"), ("reported_nil_word", "nil"))
    assert so.reported.raw_value == "-"


def test_same_fact_as_current_and_comparative_in_one_document_notes_multiple_roles():
    d = Doc()
    for role in ("current", "comparative"):
        st = d.statement()
        d.value(st, d.row(st, "Revenue"), d.column(st, role=role), "1,234")
    (so,) = d.observations()
    assert so.roles == ("comparative", "current") and so.annotations == ("multiple_roles",)


def test_an_so_never_spans_f5_runs():
    batch = reconcile(doc(1), doc(2))
    r = single(batch)
    assert r.so_count == r.document_count == 2
    assert len({o.f5_run_id for o in r.observations}) == 2


def test_instant_from_a_duration_column_end_is_recorded_on_the_member():
    (so,) = doc(1, "7,000", concept="cash_at_end_of_period", label="Cash at end of the period").observations()
    assert so.members[0].period_derivation == "duration_column_end" and so.identity.period_kind == "instant"


# ------------------------------------------------------------------------------------------------ states

def test_one_source():
    r = single(reconcile(doc(1)))
    assert (r.state, r.value_kind, r.document_count, r.reasons) == ("single_source", "numeric", 1, ())
    assert (r.interval_low, r.interval_high, r.representative_normalized_value) == (D(1233500), D(1234500), D(1234000))
    assert r.representative.raw_value == "1,234" and r.observations[0].role_in_outcome == "representative"


def test_two_agreeing_documents_corroborate():
    r = single(reconcile(doc(1), doc(2)))
    assert (r.state, r.value_kind, r.document_count, r.annotations) == ("corroborated", "numeric", 2, ())
    assert sorted(o.role_in_outcome for o in r.observations) == ["representative", "supporting"]


def test_three_documents_at_three_precisions_corroborate_within_precision():
    r = single(reconcile(doc(1, "1.2", stmt_scale=1000000), doc(2, "1,234"), doc(3, "1,234,321", stmt_scale=1)))
    assert (r.state, r.document_count, r.annotations) == ("corroborated", 3, ("agreement_within_precision_only",))
    assert (r.interval_low, r.interval_high) == (D("1234320.5"), D("1234321.5"))
    assert (r.representative.raw_value, r.representative.reported_scale, r.representative_normalized_value,
            r.representative_half_unit) == ("1,234,321", 1, D(1234321), D("0.5"))


def test_disagreement_is_conflicting_with_no_value_and_every_observation_kept():
    batch = reconcile(doc(1, "1,234"), doc(2, "1,300"))
    r = single(batch)
    assert (r.state, r.value_kind, r.interval_low, r.interval_high, r.representative_so, r.representative) == \
        ("conflicting", None, None, None, None, None)
    assert [o.role_in_outcome for o in r.observations] == ["conflicting", "conflicting"]
    assert sorted(o.normalized_value for o in r.observations) == [D(1234000), D(1300000)]
    (p,) = r.comparisons
    assert p.outcome == "disagree" and p.comparison.tolerance == D(1000) and p.comparison.abs_difference == D(66000)
    assert r.reasons == (f"values_disagree:{p.a_so}:{p.b_so}",)


def test_v8_boundary_is_inclusive_across_documents():
    assert single(reconcile(doc(1, "1,234"), doc(2, "1,235"))).state == "corroborated"      # |1000| <= 1000
    assert single(reconcile(doc(1, "1,234"), doc(2, "1,235.1"))).state == "conflicting"    # |1100| > 550


@pytest.mark.parametrize("raws,state", [(("-",), "single_source"), (("-", "Nil"), "corroborated"),
                                        (("-", "–", "None"), "corroborated")])
def test_nil_facts_are_nil_never_zero(raws, state):
    r = single(reconcile(*[doc(i + 1, raw) for i, raw in enumerate(raws)]))
    assert (r.state, r.value_kind, r.interval_low, r.representative_normalized_value) == (state, "nil", None, None)
    assert r.representative.raw_value == raws[0]                    # the first SO in canonical order


def test_nil_against_a_printed_zero_is_conflicting():
    r = single(reconcile(doc(1, "-"), doc(2, "0")))
    assert r.state == "conflicting" and "nil_vs_numeric" in r.annotations
    assert any(reason.startswith("nil_vs_numeric:") for reason in r.reasons)


def test_internally_conflicting_observation_makes_the_fact_conflicting():
    d = Doc(doc=1)
    st = d.statement()
    ci = d.column(st)
    for label, raw in (("Turnover", "1,234"), ("Revenue", "1,500")):
        d.value(st, d.row(st, label), ci, raw)
    r = single(reconcile(d, doc(2, "1,234")))
    assert r.state == "conflicting" and "internal_conflict" in r.annotations
    assert r.reasons[0].startswith("internal_conflict:")


# ------------------------------------------------------------------------------------------------ one run per document

def test_same_document_through_two_filings_counts_once():
    a = doc(1, filing=9001, recorded_at="2026-05-16T00:00:00+00:00")
    b = doc(1, filing=9002, recorded_at="2026-05-17T00:00:00+00:00")        # same SHA-256, another filing
    batch = reconcile(a, b)
    r = single(batch)
    assert (r.state, r.document_count, r.so_count) == ("single_source", 1, 1)
    assert r.annotations == ("same_document_multiple_filings",) and r.observations[0].cse_filing_id == 9002
    (sel,) = batch.selection.documents
    assert (sel.selected_run, sel.filings) == (b.run_id, (9001, 9002))
    assert [why for _, why in batch.excluded_observations] == ["f5_run_not_selected"]


def test_two_processing_runs_of_one_document_never_corroborate_each_other():
    old = doc(1, "1,234", run_id="r-old", recorded_at="2026-05-16T00:00:00+00:00")
    new = doc(1, "1,234", run_id="r-new", recorded_at="2026-06-01T00:00:00+00:00",
              versions={"mapper_version": "f5.map.2"})
    r = single(reconcile(old, new))
    assert (r.state, r.document_count, r.observations[0].f5_run_id) == ("single_source", 1, "r-new")


def test_a_newer_run_without_the_fact_never_falls_back_to_an_older_run():
    old = doc(1, "1,234", run_id="r-old", recorded_at="2026-05-16T00:00:00+00:00")
    new = Doc(doc=1, filing=9001, run_id="r-new", recorded_at="2026-06-01T00:00:00+00:00").one("N/A")
    batch = reconcile(old, new)
    assert batch.results == () and [why for _, why in batch.excluded_observations] == ["f5_run_not_selected"]


def test_runs_whose_versions_the_configuration_refuses_are_excluded():
    a, b = doc(1), doc(2, versions={"f4_extractor_version": "f4.old"})
    cfg = configuration(a)
    batch = rec.reconcile_validation_runs([a.validate(), b.validate()], cfg)
    assert batch.selection.excluded_runs == ((b.run_id, "f4_version_not_accepted"),)
    assert single(batch).document_count == 1


def test_one_selected_run_with_two_validation_runs_is_refused_not_chosen():
    a = doc(1, link_basis="listing_symbol_sec_id")
    b = doc(1, link_basis="both")                                              # a second issuer decision
    with pytest.raises(rec.ReconciliationInputError, match="validation runs"):
        rec.reconcile_validation_runs([a.validate(), b.validate()], configuration(a))


def test_several_runs_of_one_document_need_recorded_at():
    a, b = doc(1, run_id="r1", recorded_at=None), doc(1, run_id="r2")
    with pytest.raises(rec.ReconciliationInputError, match="recorded_at"):
        reconcile(a, b)


def test_observations_must_come_with_their_run():
    a, b = doc(1), doc(2)
    with pytest.raises(rec.ReconciliationInputError, match="not supplied"):
        rec.reconcile([a.run_ref()], list(a.observations()) + list(b.observations()), configuration(a))


def test_reconcile_fact_refuses_mixed_facts_or_two_observations_of_one_document():
    (x,) = doc(1).observations()
    (y,) = doc(2, concept="gross_profit", label="Gross profit").observations()
    (z,) = doc(1, filing=9002).observations()
    cfg = configuration(doc(1))
    with pytest.raises(rec.ReconciliationInputError):
        rec.reconcile_fact([x, y], cfg)
    with pytest.raises(rec.ReconciliationInputError, match="per document"):
        rec.reconcile_fact([x, z], cfg)


def test_configuration_is_explicit_versioned_and_content_addressed():
    a = configuration(doc(1))
    b = rec.ReconciliationConfiguration(accepted_f3=list(reversed(a.accepted_f3)), accepted_f4=a.accepted_f4,
                                        accepted_f5=a.accepted_f5)
    assert a.configuration_id == b.configuration_id and len(a.configuration_id) == 64
    assert configuration(doc(1), doc(2, versions={"mapper_version": "x"})).configuration_id != a.configuration_id
    with pytest.raises(rec.ConfigurationError):
        rec.ReconciliationConfiguration(a.accepted_f3, a.accepted_f4, a.accepted_f5,
                                        versions=VersionSet(admission_version="f6.admission.2"))
    with pytest.raises(rec.ConfigurationError):
        rec.ReconciliationConfiguration((), a.accepted_f4, a.accepted_f5)
    with pytest.raises(rec.ConfigurationError):
        rec.ReconciliationConfiguration(a.accepted_f3, a.accepted_f4, a.accepted_f5,
                                        reconciliation_version="f6.reconciliation.2")


# ------------------------------------------------------------------------------------------------ annotations

def test_interim_against_annual():
    agree = single(reconcile(doc(1, "1,234"), doc(2, "1,234", doc_type="annual_report")))
    differ = single(reconcile(doc(1, "1,234"), doc(2, "1,280", doc_type="annual_report")))
    assert agree.state == "corroborated" and "differs_interim_vs_annual" not in agree.annotations
    assert differ.state == "conflicting" and "differs_interim_vs_annual" in differ.annotations


@pytest.mark.parametrize("doc_type,underlying", [("errata_or_reissue", "interim_financial_statements"),
                                                 ("amendment", "interim_financial_statements")])
def test_errata_and_amendments_are_annotated_never_resolved(doc_type, underlying):
    original = doc(1, "1,234", recorded_at="2026-05-16T00:00:00+00:00")
    later = doc(2, "1,299", doc_type=doc_type, underlying=underlying, recorded_at="2026-07-01T00:00:00+00:00")
    r = single(reconcile(original, later))
    assert (r.state, r.representative) == ("conflicting", None)
    assert "differs_across_document_versions" in r.annotations
    assert single(reconcile(original, doc(2, "1,234", doc_type=doc_type, underlying=underlying))).state == "corroborated"


def test_sign_only_difference_is_conflicting_and_nothing_is_flipped():
    r = single(reconcile(doc(1, "1,234"), doc(2, "(1,234)")))
    assert r.state == "conflicting" and "sign_only_difference" in r.annotations
    assert sorted(o.normalized_value for o in r.observations) == [D(-1234000), D(1234000)]
    assert sorted(o.reported.raw_value for o in r.observations) == ["(1,234)", "1,234"]


def test_representative_ambiguity_keeps_the_interval_and_no_representative():
    r = single(reconcile(doc(1, "1,234"), doc(2, "1,235")))
    assert (r.state, r.representative_so, r.representative) == ("corroborated", None, None)
    assert "representative_ambiguous" in r.annotations and (r.interval_low, r.interval_high) == (D(1234500),) * 2
    assert all(o.role_in_outcome == "supporting" for o in r.observations)


def test_representative_is_never_moved_into_the_interval():
    """Standard printed half-units (5 x 10^k) keep the representative inside; synthetic half-units show that it is
    reported as observed even when outside."""
    fine, coarse = doc(1, "100.00", stmt_scale=1), doc(2, "100.904", stmt_scale=1)
    vrs = [fine.validate(), coarse.validate()]
    widened = replace(vrs[1].candidates[0], validation=replace(
        vrs[1].candidates[0].validation, value=replace(vrs[1].candidates[0].validation.value, half_unit=D("0.9"))))
    sos = [obs.observation(vrs[0], list(vrs[0].candidates)), obs.observation(vrs[1], [widened])]
    r = rec.reconcile_fact(sos, configuration(fine, coarse))
    assert r.state == "corroborated" and (r.interval_low, r.interval_high) == (D("100.004"), D("100.005"))
    assert r.representative_normalized_value == D("100.00") and r.representative.raw_value == "100.00"


def test_restated_comparative_is_annotated_state_unchanged():
    plain = single(reconcile(doc(1), doc(2, column=dict(role="comparative"))))
    restated = single(reconcile(doc(1), doc(2, column=dict(role="comparative", restated=True))))
    assert restated.annotations == ("restated_comparative_present",)
    assert (plain.state, plain.interval_low, plain.representative_so is None) == \
        (restated.state, restated.interval_low, restated.representative_so is None)


def test_audit_labels_differ_is_annotation_only_and_unknown_is_not_a_label():
    r = single(reconcile(doc(1, column=dict(audit="unaudited")), doc(2, column=dict(audit="audited"))))
    assert r.state == "corroborated" and r.annotations == ("audit_labels_differ",)
    assert single(reconcile(doc(1, column=dict(audit="unknown")), doc(2, column=dict(audit="audited")))).annotations == ()


def test_multi_currency_presentation_is_annotated_on_both_sides_and_never_converted():
    d = Doc(doc=1)
    for currency, raw in (("LKR", "3,000,000"), ("USD", "10,000")):
        st = d.statement(currency=currency, scale=1)
        d.value(st, d.row(st, "Revenue"), d.column(st), raw)
    batch = reconcile(d)
    by_ccy = {r.identity.currency: r for r in batch.results}
    assert set(by_ccy) == {"LKR", "USD"} and len({r.ef_key for r in batch.results}) == 2
    for ccy, raw in (("LKR", "3,000,000"), ("USD", "10,000")):
        r = by_ccy[ccy]
        assert r.annotations == ("multi_currency_presentation",) and r.representative.raw_value == raw
        assert r.representative.currency == ccy and r.representative_normalized_value == D(raw.replace(",", ""))


def test_annotations_never_change_the_state():
    base = single(reconcile(doc(1), doc(2)))
    noisy = single(reconcile(doc(1, column=dict(audit="audited", restated=True)),
                             doc(2, column=dict(role="comparative", audit="unaudited"), doc_type="annual_report")))
    assert noisy.annotations and base.annotations == ()
    assert (noisy.state, noisy.value_kind, noisy.interval_low, noisy.interval_high,
            noisy.representative_normalized_value) == \
        (base.state, base.value_kind, base.interval_low, base.interval_high, base.representative_normalized_value)


# ------------------------------------------------------------------------------------------------ invariants

def test_never_picks_a_winner_whatever_recency_type_or_audit():
    """The annual, audited, most recent and most precise document still does not win a conflict."""
    interim = doc(1, "1,234", column=dict(audit="unaudited"), recorded_at="2026-05-16T00:00:00+00:00")
    annual = doc(2, "1,250,321", stmt_scale=1, doc_type="annual_report", column=dict(audit="audited"),
                 recorded_at="2026-09-01T00:00:00+00:00")
    for a, b in ((interim, annual), (annual, interim)):
        r = single(reconcile(a, b))
        assert (r.state, r.value_kind, r.interval_low, r.representative_so, r.representative) == \
            ("conflicting", None, None, None, None)
        assert {o.role_in_outcome for o in r.observations} == {"conflicting"}


def test_never_uses_document_type_as_precedence_when_values_agree():
    """Agreeing interim (Rs, finer) and annual (Rs '000): the representative is the finer print, not the annual."""
    r = single(reconcile(doc(1, "1,234,321", stmt_scale=1), doc(2, "1,234", doc_type="annual_report")))
    assert r.state == "corroborated" and r.representative.raw_value == "1,234,321"


def test_never_changes_a_reported_value():
    docs = [doc(1, "(1,234.50)"), doc(2, "(1,234.5)"), doc(3, "-1,234.5")]
    results = [d.result() for d in docs]
    before = copy.deepcopy(results)
    vrs = [d.validate(res) for d, res in zip(docs, results)]
    r = single(rec.reconcile_validation_runs(vrs, configuration(*docs)))
    assert results == before
    printed = {(o.reported.raw_value, str(o.reported.parsed_value)) for o in r.observations}
    assert printed == {("(1,234.50)", "-1234.50"), ("(1,234.5)", "-1234.5"), ("-1,234.5", "-1234.5")}


def test_never_converts_a_currency():
    batch = reconcile(doc(1, "10,000", currency="USD"), doc(2, "3,000,000"))
    assert {(r.identity.currency, r.representative.currency, r.representative_normalized_value)
            for r in batch.results} == {("USD", "USD", D(10000000)), ("LKR", "LKR", D(3000000000))}
    assert all(r.state == "single_source" for r in batch.results)


def test_never_treats_nil_as_zero():
    r = single(reconcile(doc(1, "–"), doc(2, "0"), doc(3, "0.00")))
    assert r.state == "conflicting" and "nil_vs_numeric" in r.annotations
    nil = [o for o in r.observations if o.value_kind == "nil"][0]
    assert (nil.normalized_value, nil.interval_low, nil.reported.raw_value) == (None, None, "–")


def test_never_derives_a_period():
    d = Doc(doc=1)
    st = d.statement()
    nine, year = d.column(st, months=9, end="2025-12-31"), d.column(st, months=12, end="2026-03-31")
    ri = d.row(st, "Revenue")
    d.value(st, ri, nine, "900")
    d.value(st, ri, year, "1,200")
    batch = reconcile(d)
    assert sorted((r.identity.period_end.isoformat(), r.identity.duration_months) for r in batch.results) == \
        [("2025-12-31", 9), ("2026-03-31", 12)]                          # no 3-month Q4 = 12M - 9M


def test_never_merges_scopes():
    batch = reconcile(doc(1, column=dict(scope="group")), doc(2, column=dict(scope="company")),
                      doc(3, column=dict(scope="unstated")))
    assert sorted(r.identity.scope for r in batch.results) == ["company", "group", "unlabelled"]
    assert all(r.state == "single_source" for r in batch.results)


def test_never_uses_role_as_identity():
    """A figure presented as current in one filing and as a comparative in the next is ONE fact (F6.2 E3)."""
    r = single(reconcile(doc(1, column=dict(role="current")), doc(2, column=dict(role="comparative"))))
    assert r.state == "corroborated" and sorted(o.roles for o in r.observations) == [("comparative",), ("current",)]


def test_results_carry_versions_configuration_and_hashes():
    batch = reconcile(doc(1), doc(2))
    r = single(batch)
    assert (r.reconciliation_version, r.configuration_id) == ("f6.reconciliation.1", batch.configuration_id)
    assert len(r.input_hash) == len(r.output_hash) == len(batch.output_hash) == 64
    assert batch.facts == (r.identity,) and r.identity.ef_key == r.ef_key
    s = rec.summarize(batch)
    assert (s["facts"], s["states"], s["documents_per_fact"]) == (1, {"corroborated:numeric": 1}, {2: 1})


def test_one_document_with_an_internal_conflict_is_conflicting_not_single_source():
    d = Doc(doc=1)
    st = d.statement()
    ci = d.column(st)
    for label, raw in (("Turnover", "1,234"), ("Revenue", "1,500")):
        d.value(st, d.row(st, label), ci, raw)
    r = single(reconcile(d))
    assert (r.state, r.document_count, r.value_kind, r.representative) == ("conflicting", 1, None, None)
    assert r.annotations == ("internal_conflict",)


def test_interim_vs_annual_reads_the_underlying_type_beneath_an_errata():
    errata_of_annual = doc(2, "1,280", doc_type="errata_or_reissue", underlying="annual_report")
    r = single(reconcile(doc(1, "1,234"), errata_of_annual))
    assert r.state == "conflicting"
    assert {"differs_across_document_versions", "differs_interim_vs_annual"} <= set(r.annotations)


def test_the_input_hash_covers_the_cross_fact_context():
    (so,) = doc(1).observations()
    cfg = configuration(doc(1))
    plain = rec.reconcile_fact([so], cfg)
    context = rec.reconcile_fact([so], cfg, filings={so.document_sha256: (9001, 9002)})
    assert plain.input_hash != context.input_hash and context.annotations == ("same_document_multiple_filings",)
    assert rec.reconcile_fact([so], cfg).input_hash == plain.input_hash


def test_observations_of_other_versions_are_excluded_not_mixed():
    a, b = doc(1), doc(2)
    (so_b,) = b.observations()
    foreign = replace(so_b, versions=VersionSet(admission_version="f6.admission.0"))
    batch = rec.reconcile([a.run_ref(), b.run_ref()], list(a.observations()) + [foreign], configuration(a, b))
    assert batch.excluded_observations == ((foreign.so_key, "versions_not_configured"),)
    assert single(batch).document_count == 1
    with pytest.raises(rec.ReconciliationInputError):
        rec.reconcile_fact([foreign], configuration(a))


def test_two_different_observations_cannot_share_a_key():
    a = doc(1)
    (so,) = a.observations()
    with pytest.raises(rec.ReconciliationInputError, match="share the key"):
        rec.reconcile([a.run_ref()], [so, replace(so, output_hash="0" * 64)], configuration(a))
