"""
Stage F6.3 - inputs (f6.inputs.1), the operations-partition rule (f6.op1.partition.1), admission (f6.admission.1) and
the economic-fact identity (f6.identity.1), on synthetic F5-shaped inputs (tests/f63_factories.py). Pure: no
database, network, clock or document.
"""
import copy
import hashlib
import os
import sys
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f63_factories import ISSUER, OTHER_ISSUER, Doc, only  # noqa: E402
from worker import financial_validation as fv  # noqa: E402
from worker.financial_truth import identity as idm  # noqa: E402
from worker.financial_truth import admission, inputs, versions  # noqa: E402

GOLDEN_JSON = ('{"identity_version":"f6.identity.1","issuer_id":"11111111-1111-4111-8111-111111111111",'
               '"concept_key":"revenue","period_kind":"duration","period_end":"2026-03-31","duration_months":3,'
               '"scope":"group","operations":"total_or_unstated","maturity":"not_applicable","currency":"LKR"}')
GOLDEN_EF_KEY = "8bbe684dada0555642b9e6e218b39c2ae173ea30afb4210fc9152dfbddb24acc"


def adm(doc):
    return only(doc.validate()).admission


def ef(doc):
    return adm(doc).ef_key


# ------------------------------------------------------------------------------------------------ versions

def test_version_identifiers_are_exact_and_recorded():
    assert (versions.VALIDATION_VERSION, versions.INPUT_POLICY_VERSION, versions.OP1_VERSION,
            versions.ADMISSION_VERSION, versions.IDENTITY_VERSION, versions.RECONCILIATION_VERSION) == \
        ("f6.validation.1", "f6.inputs.1", "f6.op1.partition.1", "f6.admission.1", "f6.identity.1",
         "f6.reconciliation.1")
    vr = Doc().one().validate()
    assert vr.versions == versions.VersionSet() and vr.publication.policy_version == "f6.inputs.1"
    c = only(vr)
    assert (c.validation.version, c.admission.version, c.admission.identity.identity_version) == \
        ("f6.validation.1", "f6.admission.1", "f6.identity.1")


# ------------------------------------------------------------------------------------------------ f6.inputs.1

@pytest.mark.parametrize("uploaded,expected", [("2026-05-14T19:00:00+00:00", date(2026, 5, 15)),   # 00:30 Colombo
                                                ("2026-05-14T18:29:59+00:00", date(2026, 5, 14)),   # 23:59:59
                                                ("2026-05-15T00:30:00+05:30", date(2026, 5, 15))])
def test_publication_date_is_the_colombo_date_of_uploaded_at_never_utc(uploaded, expected):
    p = inputs.publication_input(uploaded)
    assert (p.policy_version, p.source_field, p.publication_date) == \
        ("f6.inputs.1", "report_filings.uploaded_at", expected)


def test_missing_uploaded_at_makes_every_candidate_ineligible_not_guessed():
    c = only(Doc(uploaded_at=None).one().validate())
    assert c.validation.eligibility == "ineligible" and "publication_date_not_supplied" in c.validation.ineligible_reasons
    assert not c.admission.admitted and c.admission.reasons == ("validation_ineligible",)


@pytest.mark.parametrize("bad", [datetime(2026, 5, 15, 9, 30), "2026-05-15T09:30:00", "2005-12-31T00:00:00+00:00",
                                 "yesterday"])
def test_naive_unparseable_or_pre_2006_timestamps_are_refused(bad):
    with pytest.raises(inputs.InputError):
        inputs.publication_input(bad)


def test_retrieval_and_first_seen_times_never_stand_in_for_publication():
    doc = Doc(uploaded_at="2026-03-30T04:00:00+00:00").one()     # uploaded BEFORE the 31 Mar period end
    result = doc.result()
    result["run"]["timestamps"].update(document_retrieved_at="2026-09-01T00:00:00+00:00",
                                       f1_first_seen_at="2026-06-01T00:00:00+00:00")
    vr = doc.validate(result)
    assert "period_end_after_publication" in only(vr).validation.ineligible_reasons
    assert vr.publication.publication_date == date(2026, 3, 30)
    assert ("document_retrieved_at", "2026-09-01T00:00:00+00:00") in vr.f5_run.timestamps     # kept, not used


def test_inputs_of_different_filings_or_documents_are_refused():
    doc = Doc(filing=9001).one()
    link = inputs.IssuerLinkDecision(9002, "evidenced", "listing_symbol_sec_id", ISSUER)
    with pytest.raises(inputs.InputError, match="filing"):
        admission.validate_run(doc.result(), f5_run=doc.run_ref(), issuer_link=link, uploaded_at=doc.uploaded_at,
                               document=doc.document())
    other = Doc(filing=9001, doc=2).one()
    with pytest.raises(inputs.InputError, match="document_sha256"):
        admission.validate_run(other.result(), f5_run=doc.run_ref(), issuer_link=doc.issuer_link(),
                               uploaded_at=doc.uploaded_at, document=doc.document())


# ------------------------------------------------------------------------------------------------ A-1 value

@pytest.mark.parametrize("raw,kind,form", [("1,234", "numeric", None), ("(1,234)", "numeric", None),
                                           ("0", "numeric", None),
                                           ("-", "nil", ("reported_nil", "hyphen_minus")),
                                           ("–", "nil", ("reported_nil", "en_dash")),
                                           ("Nil", "nil", ("reported_nil_word", "nil")),
                                           ("None", "nil", ("reported_nil_word", "none"))])
def test_numeric_and_nil_values_are_admitted_with_their_kind(raw, kind, form):
    a = adm(Doc().one(raw))
    assert (a.admitted, a.value_kind, a.nil_form, a.reasons) == (True, kind, form, ())


def test_nil_is_never_zero():
    nil, zero = only(Doc().one("-").validate()), only(Doc().one("0").validate())
    assert nil.validation.value.normalized_value is None and nil.admission.value_kind == "nil"
    assert zero.validation.value.normalized_value == 0 and zero.admission.value_kind == "numeric"
    assert nil.admission.ef_key == zero.admission.ef_key          # one fact, two different observations


@pytest.mark.parametrize("raw,f61_reason", [("", "value_blank"), ("N/A", "value_reported_not_applicable"),
                                            ("n.a.", "value_reported_not_applicable"), ("see note", "value_not_a_number"),
                                            ("12.5%", "value_not_an_amount")])
def test_blank_na_and_non_numeric_values_are_not_admitted(raw, f61_reason):
    c = only(Doc().one(raw).validate())
    assert c.validation.eligibility == "normalization_required" and f61_reason in c.validation.normalization_reasons
    assert not c.admission.admitted and c.admission.reasons == ("normalization_not_admissible",)
    assert c.admission.value_kind is None and c.admission.identity is None


@pytest.mark.parametrize("over,f61_reason", [
    (dict(scale=None, scale_basis="statement_scale_unresolved"), "scale_unresolved"),
    (dict(scale=None, scale_basis="statement_scale_conflicting"), "scale_conflicting"),
    (dict(currency=None), "currency_not_reported"),
    (dict(currency="Rs."), "currency_unrecognised")])
def test_scale_and_currency_problems_are_not_admitted_nor_defaulted(over, f61_reason):
    c = only(Doc().one("1,234", **over).validate())
    assert f61_reason in c.validation.normalization_reasons and not c.admission.admitted
    assert c.validation.value.normalized_value is None


@pytest.mark.parametrize("raw", ["-", "Nil"])
def test_nil_with_any_other_normalisation_reason_is_not_admitted(raw):
    c = only(Doc().one(raw, currency=None).validate())
    assert len(c.validation.normalization_reasons) == 2 and not c.admission.admitted
    assert c.admission.reasons == ("normalization_not_admissible",)


@pytest.mark.parametrize("status", ["unresolved", "ambiguous", "conflicting"])
def test_unresolved_ambiguous_and_conflicting_candidates_are_not_admitted(status):
    over = dict(mapping_status="ambiguous") if status == "ambiguous" else {}
    c = only(Doc().one("1,234", status=status, **over).validate())
    assert f"candidate_status_{status}" in c.validation.ineligible_reasons
    assert not c.admission.admitted and c.admission.reasons[0] == "validation_ineligible"


def test_ineligible_nil_is_not_admitted():
    c = only(Doc().one("-", column=dict(role_trust="untrusted")).validate())
    assert "role_untrusted" in c.validation.ineligible_reasons and not c.admission.admitted


def test_f61_output_is_kept_exactly_and_never_rewritten():
    doc = Doc().one("1,234")
    vr = doc.validate()
    c = only(vr)
    assert c.validation == fv.validate_candidate(c.context, vr.run_evidence)       # admission adds, never edits
    assert vr.run_evidence == fv.run_evidence("evidenced", date(2026, 5, 15), doc.document().f6_1_classification())


# ------------------------------------------------------------------------------------------------ A-2 issuer

@pytest.mark.parametrize("basis", ["listing_symbol_sec_id", "both"])
def test_evidenced_issuer_link_on_listing_or_both_is_admitted(basis):
    a = adm(Doc(link_basis=basis).one())
    assert a.admitted and a.identity.issuer_id == ISSUER


def test_path_prefix_only_issuer_link_is_refused_by_admission_alone():
    c = only(Doc(link_basis="document_path_prefix").one().validate())
    assert c.validation.eligibility == "eligible"                  # F6.1 accepts an evidenced link ...
    assert not c.admission.admitted and c.admission.reasons == ("issuer_link_path_prefix_only",)   # ... A-2 refuses
    assert c.admission.ef_key is None


@pytest.mark.parametrize("status,basis", [("conflict", "listing_symbol_sec_id"), ("unresolved", "none"),
                                          ("conflict", "document_path_prefix")])
def test_conflicting_or_unresolved_issuer_links_are_refused(status, basis):
    c = only(Doc(link_status=status, link_basis=basis).one().validate())
    assert f"issuer_evidence_{status}" in c.validation.ineligible_reasons
    assert c.admission.reasons == ("validation_ineligible", f"issuer_link_not_evidenced:{status}")


def test_missing_issuer_link_is_refused():
    c = only(Doc(link_status=None).one().validate())
    assert "issuer_evidence_not_supplied" in c.validation.ineligible_reasons
    assert c.admission.reasons == ("validation_ineligible", "issuer_link_not_supplied")


@pytest.mark.parametrize("args", [(9001, "evidenced", "listing_symbol_sec_id", None),
                                  (9001, "conflict", "listing_symbol_sec_id", ISSUER),
                                  (9001, "evidenced", "none", ISSUER), (9001, "linked", "both", ISSUER),
                                  (9001, "evidenced", "isin", ISSUER), ("9001", "evidenced", "both", ISSUER)])
def test_malformed_issuer_link_decisions_are_input_errors(args):
    with pytest.raises(inputs.InputError):
        inputs.IssuerLinkDecision(*args)


def test_the_issuer_comes_only_from_the_decision():
    assert adm(Doc(issuer=OTHER_ISSUER).one()).identity.issuer_id == OTHER_ISSUER
    assert ef(Doc(issuer=OTHER_ISSUER).one()) != ef(Doc().one())


# ------------------------------------------------------------------------------------------------ A-5 scope

@pytest.mark.parametrize("reported,identity_scope", [("group", "group"), ("company", "company"), ("bank", "bank"),
                                                     ("unstated", "unlabelled")])
def test_scope_is_reported_scope_and_unstated_is_unlabelled(reported, identity_scope):
    assert adm(Doc().one(column=dict(scope=reported))).identity.scope == identity_scope


def test_scopes_never_merge():
    keys = {ef(Doc().one("1,234", column=dict(scope=s))) for s in ("group", "company", "bank", "unstated")}
    assert len(keys) == 4


def test_unrecognised_scope_is_not_admitted_nor_mapped():
    a = adm(Doc().one(column=dict(scope="consolidated")))
    assert not a.admitted and a.reasons == ("scope_unrecognised:consolidated",)


# ------------------------------------------------------------------------------------------------ A-4 maturity

def borrowing(section):
    return Doc().one("5,000", "interest_bearing_borrowings", label="Interest bearing borrowings", section=section)


@pytest.mark.parametrize("section,maturity", [("Current liabilities", "current"),
                                              ("Non-current liabilities", "non_current")])
def test_borrowing_maturity_current_and_non_current_are_distinct_facts(section, maturity):
    a = adm(borrowing(section))
    assert a.admitted and a.identity.maturity == maturity


def test_current_and_non_current_borrowings_never_merge():
    assert ef(borrowing("Current liabilities")) != ef(borrowing("Non-current liabilities"))


@pytest.mark.parametrize("section,basis", [(None, "no_section_label"), ("Liabilities", "section_not_maturity"),
                                           ("Equity", "section_not_maturity"),
                                           ("Current and non-current liabilities", "section_maturity_ambiguous")])
def test_undetermined_borrowing_maturity_is_not_admitted_nor_inferred(section, basis):
    c = only(borrowing(section).validate())
    assert c.validation.eligibility == "eligible" and c.validation.maturity.maturity is None
    assert not c.admission.admitted and c.admission.reasons == (f"maturity_undetermined:{basis}",)


def test_maturity_is_not_applicable_for_other_concepts():
    assert adm(Doc().one("9,000", "total_assets", label="Total assets")).identity.maturity == "not_applicable"


# ------------------------------------------------------------------------------------------------ A-3 / OP1

def test_row_label_and_no_claim_operations_are_admitted_as_f61_decided():
    row = adm(Doc().one("200", "profit_for_period", label="Profit for the period from discontinued operations"))
    none = adm(Doc().one("200", "profit_for_period", label="Profit for the period"))
    assert (row.admitted, row.operations_route, row.identity.operations) == (True, "row_label", "discontinued")
    assert (none.admitted, none.operations_route, none.identity.operations) == (True, "none", "total_or_unstated")


def partition(cont="1,000", disc="500", total="1,500", concept="revenue", *, scale=1000, total_col=0,
              total_concept=None, extra=()):
    """One statement: a continuing and a discontinued row under section headings, and an unlabelled total row."""
    doc = Doc()
    st = doc.statement(scale=scale)
    c0 = doc.column(st)
    c1 = doc.column(st, role="comparative", end="2025-03-31")
    rc = doc.row(st, "Revenue", section="Continuing operations")
    rd = doc.row(st, "Revenue", section="Discontinued operations")
    rt = doc.row(st, "Revenue")
    doc.value(st, rc, c0, cont, concept)
    doc.value(st, rd, c0, disc, concept)
    if total is not None:
        doc.value(st, rt, c0 if total_col == 0 else c1, total, total_concept or concept)
    for label, section, raw in extra:
        doc.value(st, doc.row(st, label, section=section), c0, raw, concept)
    return doc


def by_row(vr):
    return {(c.context.row_index, c.context.column_index): c for c in vr.candidates}


def test_section_derived_operations_pass_op1_and_are_admitted():
    vr = partition().validate()
    (rec,) = vr.op1
    rows = by_row(vr)
    assert (rec.version, rec.outcome, rec.difference, rec.tolerance) == \
        ("f6.op1.partition.1", "pass", Decimal(0), Decimal(1500))
    for ri in (0, 1):
        a = rows[(ri, 0)].admission
        assert a.admitted and a.operations_route == "section_label+op1" and a.op1_record == rec.key
    assert rows[(0, 0)].admission.identity.operations == "continuing"
    assert rows[(1, 0)].admission.identity.operations == "discontinued"
    assert rows[(2, 0)].admission.operations_route == "none"
    assert rec.validated == (rows[(0, 0)].source_key, rows[(1, 0)].source_key)


def test_op1_records_every_term_value_half_unit_tolerance_difference_and_outcome():
    vr = partition("1,000", "500", "1,501").validate()
    (rec,) = vr.op1
    assert [t.operations for t in rec.terms] == ["continuing", "discontinued", "total_or_unstated"]
    assert [(t.status, t.value, t.half_unit) for t in rec.terms] == \
        [("usable", Decimal(1000000), Decimal(500)), ("usable", Decimal(500000), Decimal(500)),
         ("usable", Decimal(1501000), Decimal(500))]
    assert [len(t.rows) for t in rec.terms] == [1, 1, 1] and rec.terms[0].rows[0].raw_value == "1,000"
    assert (rec.computed, rec.total, rec.tolerance, rec.difference, rec.outcome) == \
        (Decimal(1500000), Decimal(1501000), Decimal(1500), Decimal(1000), "pass")
    assert (rec.currency, rec.value_type, rec.group_key[1], rec.concept_key) == \
        ("LKR", "currency_amount", "profit_or_loss", "revenue")


def test_op1_half_unit_boundary_is_inclusive_and_exact():
    ok = partition("100.3", "50.3", "150", scale=1).validate().op1[0]      # tolerance 0.05 + 0.05 + 0.5
    assert (ok.difference, ok.tolerance, ok.outcome) == (Decimal("0.6"), Decimal("0.60"), "pass")
    out = partition("100.4", "50.3", "150", scale=1).validate()
    assert (out.op1[0].difference, out.op1[0].outcome) == (Decimal("0.7"), "fail")
    assert by_row(out)[(1, 0)].admission.reasons == ("operations_section_derived_unvalidated:fail",)


def test_op1_fail_refuses_every_section_derived_row_of_the_group():
    vr = partition("1,000", "500", "1,900").validate()
    assert vr.op1[0].outcome == "fail" and vr.op1[0].reasons == ("difference_exceeds_printed_precision",)
    rows = by_row(vr)
    for ri in (0, 1):
        assert rows[(ri, 0)].admission.reasons == ("operations_section_derived_unvalidated:fail",)
    assert rows[(2, 0)].admission.admitted                 # the total row claims no operations: F6.1 stands


@pytest.mark.parametrize("kw,reason", [
    (dict(total=None), "missing:total_or_unstated"),
    (dict(disc="-"), "nil:discontinued"),                                  # nil is never zero (D-3)
    (dict(disc="N/A"), "non_numeric:discontinued"),
    (dict(total_col=1), "missing:total_or_unstated"),                      # never borrowed from another column
    (dict(total_concept="profit_before_tax"), "missing:total_or_unstated"),  # nor from another concept
    (dict(extra=[("Revenue", "Continuing operations", "999")]), "multiple_values:continuing")])
def test_op1_insufficient_evidence(kw, reason):
    vr = partition(**kw).validate()
    rec = vr.op1[0]
    assert rec.outcome == "insufficient_evidence" and reason in rec.reasons
    assert rec.difference is None and rec.tolerance is None
    assert by_row(vr)[(0, 0)].admission.reasons == ("operations_section_derived_unvalidated:insufficient_evidence",)


def test_op1_nil_discontinued_term_is_not_zero_even_when_continuing_equals_total():
    vr = partition("1,500", "-", "1,500").validate()
    assert vr.op1[0].outcome == "insufficient_evidence" and vr.op1[0].reasons == ("nil:discontinued",)


def test_op1_currency_mismatch_is_insufficient():
    doc = partition()
    doc.candidates[2]["reported_currency"] = "USD"                          # the total row printed in USD
    assert doc.validate().op1[0].reasons == ("currency_mismatch",)


def test_op1_same_value_at_two_precisions_is_one_value_with_the_finest_half_unit():
    vr = partition(extra=[("Revenue", "Continuing operations", "1,000.0")]).validate()
    term = vr.op1[0].terms[0]
    assert (term.status, term.value, term.half_unit, len(term.rows)) == ("usable", Decimal(1000000), Decimal(50), 2)


def test_op1_pass_lifts_only_the_total_row_reason_and_never_relabels():
    """profit_for_period under a 'Discontinued operations' heading: F6.1 refuses the section operations on a total
    row; a passing partition lifts exactly that reason and the row stays 'discontinued'."""
    doc = Doc()
    st = doc.statement()
    c0 = doc.column(st)
    for label, section, raw in (("Profit for the period", "Continuing operations", "300"),
                                ("Profit for the period", "Discontinued operations", "(50)"),
                                ("Profit for the period", None, "250")):
        doc.value(st, doc.row(st, label, section=section), c0, raw, "profit_for_period")
    rows = by_row(doc.validate())
    disc = rows[(1, 0)]
    assert "operations_section_derived_on_total_row" in disc.validation.ineligible_reasons   # F6.1 unchanged
    assert disc.admission.admitted and disc.admission.lifted_reasons == ("operations_section_derived_on_total_row",)
    assert disc.admission.identity.operations == "discontinued"


def test_mislabelled_total_under_a_discontinued_heading_is_never_admitted_nor_relabelled():
    """LOLC 52684's shape: 'Profit from discontinued operations' and the real total 'Profit/(loss) for the period'
    both sit under the heading, and no unlabelled total exists."""
    doc = Doc()
    st = doc.statement()
    c0 = doc.column(st)
    doc.value(st, doc.row(st, "Profit from continuing operations"), c0, "300", "profit_for_period")
    doc.value(st, doc.row(st, "Profit for the period", section="Discontinued operations"), c0, "(50)",
              "profit_for_period")
    doc.value(st, doc.row(st, "Profit/(loss) for the period", section="Discontinued operations"), c0, "250",
              "profit_for_period")
    vr = doc.validate()
    rec = vr.op1[0]
    assert rec.outcome == "insufficient_evidence"
    assert set(rec.reasons) == {"multiple_values:discontinued", "missing:total_or_unstated"}
    total = by_row(vr)[(2, 0)]
    assert not total.admission.admitted and total.context.operations == "discontinued"


def test_section_derived_row_that_is_not_a_partition_term_is_insufficient():
    doc = partition()
    doc.candidates[1]["candidate_status"] = "conflicting"                  # the discontinued row is no term now
    vr = doc.validate()
    assert vr.op1[0].reasons == ("missing:discontinued",)
    assert by_row(vr)[(1, 0)].admission.reasons == \
        ("validation_ineligible", "operations_section_derived_unvalidated:insufficient_evidence")


# ------------------------------------------------------------------------------------------------ identity

def golden_identity(**over):
    base = dict(identity_version="f6.identity.1", issuer_id=ISSUER, concept_key="revenue", period_kind="duration",
                period_end=date(2026, 3, 31), duration_months=3, scope="group", operations="total_or_unstated",
                maturity="not_applicable", currency="LKR")
    base.update(over)
    return idm.EconomicFactIdentity(**base)


def test_identity_json_has_exactly_the_ten_fields_in_order_and_a_golden_hash():
    i = golden_identity()
    assert [name for name, _ in i.identity_fields()] == list(idm.IDENTITY_FIELDS) == [
        "identity_version", "issuer_id", "concept_key", "period_kind", "period_end", "duration_months", "scope",
        "operations", "maturity", "currency"]
    assert i.canonical_json() == GOLDEN_JSON
    assert i.ef_key == hashlib.sha256(GOLDEN_JSON.encode("ascii")).hexdigest() == GOLDEN_EF_KEY


def test_admitted_candidate_gets_the_golden_identity():
    a = adm(Doc().one("1,234"))
    assert a.identity == golden_identity() and a.ef_key == GOLDEN_EF_KEY


@pytest.mark.parametrize("doc_a,doc_b", [
    (dict(column=dict(role="current")), dict(column=dict(role="comparative"))),                    # role
    (dict(column=dict(audit="unaudited")), dict(column=dict(audit="audited"))),                    # audit label
    (dict(column=dict(start="auto")), dict(column=dict(start=None))),                              # period start
    (dict(column=dict(fiscal_label=None)), dict(column=dict(fiscal_label="Q4"))),                  # fiscal label
    (dict(column=dict(restated=False)), dict(column=dict(restated=True))),                         # restatement
    (dict(raw="1,234"), dict(raw="1.234", stmt_scale=1000000)),                                    # scale / print
    (dict(raw="1,234"), dict(raw="1,234,000", stmt_scale=1))])
def test_attributes_never_enter_the_identity(doc_a, doc_b):
    def build(kw, n):
        kw = dict(kw)
        return ef(Doc(doc=n, filing=9000 + n).one(kw.pop("raw", "1,234"), **kw))
    assert build(doc_a, 1) == build(doc_b, 2) == GOLDEN_EF_KEY


def test_document_filing_run_and_document_type_never_enter_the_identity():
    keys = {ef(Doc(filing=f, doc=d, run_id=f"r{d}", doc_type=t).one())
            for f, d, t in ((9001, 1, "interim_financial_statements"), (9002, 2, "annual_report"),
                            (9003, 3, "errata_or_reissue"), (9004, 4, "undetermined"))}
    assert keys == {GOLDEN_EF_KEY}


@pytest.mark.parametrize("field,value", [("issuer_id", OTHER_ISSUER), ("concept_key", "gross_profit"),
                                         ("period_end", date(2026, 6, 30)), ("duration_months", 6),
                                         ("scope", "company"), ("operations", "continuing"), ("currency", "USD")])
def test_every_identity_field_changes_the_ef_key(field, value):
    assert golden_identity(**{field: value}).ef_key != GOLDEN_EF_KEY


@pytest.mark.parametrize("over", [dict(identity_version="f6.identity.2"), dict(scope="consolidated"),
                                  dict(duration_months=None), dict(duration_months=0), dict(period_kind="instant"),
                                  dict(maturity="current"), dict(currency="Rs"), dict(concept_key="insurance_revenue"),
                                  dict(period_end=datetime(2026, 3, 31)), dict(issuer_id=" ")])
def test_invalid_identities_are_refused(over):
    with pytest.raises(idm.IdentityError):
        golden_identity(**over)


def test_a_duration_column_end_instant_is_an_instant_identity():
    a = adm(Doc().one("7,000", "cash_at_end_of_period", label="Cash and cash equivalents at end of the period"))
    assert a.admitted and (a.identity.period_kind, a.identity.duration_months) == ("instant", None)


def test_twelve_month_and_quarter_are_separate_facts_never_derived():
    doc = Doc()
    st = doc.statement()
    q = doc.column(st, end="2026-03-31", months=3)
    y = doc.column(st, end="2026-03-31", months=12)
    ri = doc.row(st, "Revenue")
    doc.value(st, ri, q, "300")
    doc.value(st, ri, y, "1,200")
    vr = doc.validate()
    ids = sorted((c.admission.identity.duration_months, c.admission.ef_key) for c in vr.candidates)
    assert [m for m, _ in ids] == [3, 12] and ids[0][1] != ids[1][1]
    assert len(vr.candidates) == 2                                          # no 9M or Q4 is ever created


def test_validation_never_mutates_its_inputs():
    doc = Doc().one("(1,234)")
    result = doc.result()
    before = copy.deepcopy(result)
    doc.validate(result)
    assert result == before


def test_candidate_ids_and_keys_are_carried_for_persistence():
    doc = Doc().one("1,234", candidate_id=4242)
    c = only(doc.validate())
    assert c.candidate_id == 4242 and c.source_key == (doc.run_id, 0, 0, 0, 0, "revenue")
    assert len(c.key) == len(c.input_hash) == len(c.output_hash) == 64


def test_identity_error_is_not_raised_for_data_problems():
    """Data problems are refusals with reasons; only malformed caller input raises."""
    vr = Doc().one("garbage", column=dict(role_trust="untrusted", scope="consolidated")).validate()
    assert not only(vr).admission.admitted


def test_replace_of_identity_is_validated():
    with pytest.raises(idm.IdentityError):
        replace(golden_identity(), maturity="non_current")


def test_a_passing_partition_validates_only_its_own_term_rows():
    """A second section-derived continuing row that is not a term (F5 'conflicting') gains nothing from the pass."""
    doc = partition(extra=[("Revenue", "Continuing operations", "1,000")])
    doc.candidates[3]["candidate_status"] = "conflicting"
    vr = doc.validate()
    assert vr.op1[0].outcome == "pass" and len(vr.op1[0].validated) == 2
    outsider = by_row(vr)[(3, 0)].admission
    assert outsider.reasons == ("validation_ineligible", "operations_section_derived_unvalidated:insufficient_evidence")
    assert outsider.operations_route is None
