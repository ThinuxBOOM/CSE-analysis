"""
Stage F6.1 - validation / normalisation primitives (worker/financial_validation.py), on synthetic F5-shaped
candidates. Pure: no database, no network, no documents.
"""
import copy
import os
import sys
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import financial_validation as fv

V = fv.VALIDATION_VERSION
RUN = fv.RunEvidence("evidenced", date(2026, 5, 15), "documented", "document_only", "document_only")


def ctx(**over):
    """A valid F5 candidate: revenue, current 3M to 31 Mar 2026, Rs '000, LKR."""
    base = dict(source_key=("run1", 0, 1, 0, 0, "revenue"), concept_key="revenue", mapping_status="mapped",
                candidate_status="proposed", value_type="currency_amount", attribution="not_applicable",
                period_kind="duration", period_class="3m", period_derivation="column", raw_value="1,234",
                parsed_value=Decimal("1234"), representation_class="numeric", printed_decimals=0,
                sign_as_printed="positive", reported_scale=1000, scale_basis="header_zone", reported_currency="LKR",
                statement_index=0, statement_root=0, statement_kind="profit_or_loss", column_index=0, role="current",
                role_trust="trusted", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31), duration_months=3,
                fiscal_label=None, reported_scope="group", canonical_scope="consolidated", row_index=1,
                label_raw="Revenue", section_label_raw=None, operations="total_or_unstated", operations_basis="none")
    base.update(over)
    return fv.CandidateContext(**base)


def val(**over):
    run = over.pop("run", RUN)
    return fv.validate_candidate(ctx(**over), run)


def num(raw, parsed, scale=1000, decimals=0, value_type="currency_amount", currency="LKR", rep="numeric"):
    return fv.normalize_value(raw, parsed, rep, decimals, "positive", value_type, scale, "header_zone", currency)


def test_version_is_exposed_and_carried():
    assert V == "f6.validation.1"
    v = val()
    assert v.version == V and fv.validate_run([ctx()], RUN).version == V


# --- eligibility ------------------------------------------------------------------------------------------------

def test_proposed_trusted_evidenced_is_eligible():
    v = val()
    assert (v.eligibility, v.ineligible_reasons, v.normalization_reasons) == ("eligible", (), ())
    assert v.value.normalized_value == Decimal("1234000") and v.period.status == "valid"


@pytest.mark.parametrize("status,extra", [("unresolved", {}),
                                          ("ambiguous", dict(concept_key=None, mapping_status="ambiguous", value_type=None,
                                                             ambiguous_concepts=("eps_basic", "eps_diluted"))),
                                          ("conflicting", {})])
def test_non_proposed_candidates_are_never_eligible(status, extra):
    v = val(candidate_status=status, **extra)
    assert v.eligibility == "ineligible" and f"candidate_status_{status}" in v.ineligible_reasons


def test_ambiguous_candidate_is_ineligible_for_two_reasons():
    v = val(candidate_status="ambiguous", concept_key=None, mapping_status="ambiguous", value_type=None)
    assert v.ineligible_reasons[:2] == ("candidate_status_ambiguous", "mapping_not_single_concept")


@pytest.mark.parametrize("role,trust", [("unknown", "untrusted"), ("current", "untrusted")])
def test_untrusted_role_is_ineligible(role, trust):
    assert "role_untrusted" in val(role=role, role_trust=trust).ineligible_reasons


@pytest.mark.parametrize("issuer,reason", [(None, "issuer_evidence_not_supplied"), ("conflict", "issuer_evidence_conflict"),
                                           ("unresolved", "issuer_evidence_unresolved")])
def test_issuer_evidence_is_an_input_and_must_be_evidenced(issuer, reason):
    v = val(run=replace(RUN, issuer_link_status=issuer))
    assert v.eligibility == "ineligible" and reason in v.ineligible_reasons


def test_section_only_discontinued_operation_on_a_total_row_is_not_eligible():
    v = val(concept_key="profit_for_period", source_key=("run1", 0, 9, 0, 0, "profit_for_period"), label_raw="Profit for the period",
            section_label_raw="Discontinued operations", operations="discontinued", operations_basis="section_label")
    assert v.eligibility == "ineligible" and v.operations.trust == "untrusted"
    assert "operations_section_derived_on_total_row" in v.ineligible_reasons


def test_row_label_operations_are_trusted_and_section_operations_on_non_total_rows_are_recorded():
    v = val(concept_key="profit_for_period", label_raw="Profit/(loss) for the period from discontinued operations",
            operations="discontinued", operations_basis="row_label")
    assert v.eligibility == "eligible" and (v.operations.operations, v.operations.basis, v.operations.trust) == \
        ("discontinued", "row_label", "trusted")
    v = val(section_label_raw="Continuing operations", operations="continuing", operations_basis="section_label")
    assert v.eligibility == "eligible" and v.operations.basis == "section_label"
    assert val(operations="continuing", operations_basis="none").ineligible_reasons == ("operations_invalid",)


def test_reserved_concept_is_ineligible():
    assert "concept_not_active" in val(concept_key="insurance_revenue").ineligible_reasons


# --- periods ------------------------------------------------------------------------------------------------------

def test_valid_instant():
    v = val(concept_key="total_assets", statement_kind="financial_position", period_kind="instant", period_class=None,
            start_date=None, duration_months=None, end_date=date(2026, 3, 31))
    assert v.period.status == "valid" and v.eligibility == "eligible"
    assert (v.period.period_kind, v.period.end_date, v.period.duration_months, v.period.period_class) == \
        ("instant", date(2026, 3, 31), None, None)


@pytest.mark.parametrize("over,reason", [(dict(duration_months=3), "instant_with_duration"),
                                         (dict(start_date=date(2026, 1, 1)), "instant_with_start"),
                                         (dict(period_class="3m"), "instant_with_period_class")])
def test_instant_with_duration_semantics_is_rejected(over, reason):
    base = dict(concept_key="total_assets", period_kind="instant", period_class=None, start_date=None, duration_months=None)
    base.update(over)
    v = val(**base)
    assert v.period.status == "invalid" and reason in v.ineligible_reasons


@pytest.mark.parametrize("months,start,end", [(3, date(2026, 1, 1), date(2026, 3, 31)), (6, date(2025, 10, 1), date(2026, 3, 31)),
                                              (9, date(2025, 7, 1), date(2026, 3, 31)), (12, date(2025, 4, 1), date(2026, 3, 31))])
def test_valid_standard_durations(months, start, end):
    v = val(duration_months=months, period_class=f"{months}m", start_date=start, end_date=end)
    assert v.period.status == "valid" and v.period.period_class == f"{months}m"


def test_non_month_end_duration_is_preserved_not_rejected():
    """UCAR-style years ending on the 25th: no start printed, not a month end."""
    v = val(end_date=date(2026, 6, 25), start_date=None, duration_months=6, period_class="6m",
            run=replace(RUN, publication_date=date(2026, 8, 10)))
    assert v.eligibility == "eligible" and v.period.end_date == date(2026, 6, 25)
    assert v.period.notes == ("start_not_reported", "end_not_month_end")
    v = val(end_date=date(2026, 6, 25), start_date=date(2026, 3, 26), duration_months=3,
            run=replace(RUN, publication_date=date(2026, 8, 10)))
    assert v.period.status == "valid"                                # start = end + 1 day - 3 months, no month-end needed


def test_missing_or_inconsistent_duration_is_rejected():
    assert "duration_months_missing" in val(duration_months=None, period_class="unspecified", start_date=None).ineligible_reasons
    assert "period_class_inconsistent" in val(period_class="6m").ineligible_reasons
    assert "period_start_inconsistent_with_duration" in val(start_date=date(2025, 10, 1)).ineligible_reasons
    assert "duration_months_invalid" in val(duration_months=0).ineligible_reasons


def test_period_after_the_supplied_publication_date_is_rejected():
    """DIAL-style: a column dated 2026-12-31 in a filing published 2026-08-14."""
    v = val(concept_key="total_assets", period_kind="instant", period_class=None, start_date=None, duration_months=None,
            end_date=date(2026, 12, 31), run=replace(RUN, publication_date=date(2026, 8, 14)))
    assert v.eligibility == "ineligible" and "period_end_after_publication" in v.ineligible_reasons
    assert "period_end_after_publication" not in val(run=replace(RUN, publication_date=date(2026, 3, 31))).ineligible_reasons


def test_publication_date_is_a_required_explicit_input():
    assert "publication_date_not_supplied" in val(run=replace(RUN, publication_date=None)).ineligible_reasons
    with pytest.raises(fv.ValidationInputError):
        val(run=replace(RUN, publication_date=datetime(2026, 5, 15, 10, 0)))     # a datetime would hide a timezone choice


@pytest.mark.parametrize("basis,status", [("inferred_only", "undetermined"), ("conflicting", "conflicting"), ("none", "undetermined")])
def test_inferred_or_conflicting_fye_cannot_create_a_quarter(basis, status):
    run = replace(RUN, fiscal_year_end_basis=basis, fiscal_year_end_status=status)
    v = val(run=run)                                                 # a 3M column ending 31 March: no quarter is derived
    assert v.period.fiscal_label is None and v.eligibility == "eligible"
    v = val(fiscal_label="Q4", run=run)                              # a label that is not documented is rejected, not kept
    assert v.period.fiscal_label is None and "fiscal_label_without_documented_fye" in v.ineligible_reasons
    assert val(fiscal_label="Q4").period.fiscal_label == "Q4"       # documented + trusted FYE: passed through only


def test_twelve_month_and_quarter_are_never_derived_from_each_other():
    v = val(duration_months=12, period_class="12m", start_date=date(2025, 4, 1))
    assert v.period.duration_months == 12 and v.period.fiscal_label is None and v.period.period_class == "12m"


# --- scale / currency ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scale,basis,expected", [(1, "currency_only_header", "1234"), (1000, "header_zone", "1234000"),
                                                  (1000000, "header_zone", "1234000000")])
def test_resolved_scales_normalise_exactly(scale, basis, expected):
    v = val(reported_scale=scale, scale_basis=basis)
    assert v.value.normalized_value == Decimal(expected) and isinstance(v.value.normalized_value, Decimal)
    assert v.value.parsed_value == Decimal("1234") and v.value.raw_value == "1,234"             # originals preserved
    assert v.value.half_unit == Decimal(scale) / 2 and v.value.rule == "parsed_value*reported_scale"


@pytest.mark.parametrize("basis,reason", [("statement_scale_unresolved", "scale_unresolved"),
                                          ("statement_scale_conflicting", "scale_conflicting"), (None, "scale_not_reported")])
def test_unknown_or_conflicting_scale_requires_normalisation(basis, reason):
    v = val(reported_scale=None, scale_basis=basis)
    assert v.eligibility == "normalization_required" and v.normalization_reasons == (reason,)
    assert v.value.normalized_value is None and v.value.parsed_value == Decimal("1234")


def test_unrecognised_scale_is_never_applied():
    v = val(reported_scale=1500)
    assert v.value.normalized_value is None and "scale_unrecognised" in v.normalization_reasons


@pytest.mark.parametrize("cur", ["LKR", "USD"])
def test_printed_currency_is_kept_and_never_converted(cur):
    v = val(reported_currency=cur)
    assert v.value.currency == cur and v.value.normalized_value == Decimal("1234000")


@pytest.mark.parametrize("cur,reason,kept", [(None, "currency_not_reported", None), ("", "currency_not_reported", None),
                                             ("Rs.", "currency_unrecognised", "Rs.")])
def test_missing_currency_is_never_defaulted(cur, reason, kept):
    v = val(reported_currency=cur)
    assert v.eligibility == "normalization_required" and v.normalization_reasons == (reason,)
    assert v.value.currency == kept and v.value.normalized_value is None                  # never 'LKR' by default


def test_different_currencies_are_incomparable_not_converted():
    a, b = num("1,000", "1000", currency="USD"), num("310,000", "310000", currency="LKR")
    r = fv.compare_values(a, b)
    assert (r.outcome, r.reason) == ("incomparable", "currency_differs")


def test_exact_decimal_normalisation_and_float_rejection():
    v = num("1,234.56", "1234.56", scale=1000000, decimals=2)
    assert v.normalized_value == Decimal("1234560000.00") and v.half_unit == Decimal("5000.0000")
    with pytest.raises(fv.ValidationInputError):
        num("1,234.56", 1234.56)


def test_per_share_values_are_never_multiplied_by_a_statement_scale():
    v = val(concept_key="eps_basic", value_type="per_share_amount", raw_value="12.34", parsed_value=Decimal("12.34"),
            printed_decimals=2, reported_scale=1, scale_basis="per_share_in_full_unit_statement")
    assert v.value.normalized_value == Decimal("12.34") and v.value.rule == "parsed_value*1(per_share)"
    v = val(concept_key="eps_basic", value_type="per_share_amount", reported_scale=1000)
    assert v.value.normalized_value is None and "per_share_scale_not_unit" in v.normalization_reasons
    v = val(concept_key="eps_basic", value_type="per_share_amount", reported_scale=None, scale_basis="per_share_unit_not_stated")
    assert v.normalization_reasons == ("per_share_unit_not_stated",)


# --- dash / nil ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,glyph", [("-", "hyphen_minus"), ("–", "en_dash"), ("—", "em_dash")])
def test_printed_dashes_are_reported_nil_never_zero(raw, glyph):
    v = val(raw_value=raw, parsed_value=None, representation_class="dash_nil", sign_as_printed="nil", printed_decimals=None)
    assert v.value.state == "reported_nil" and v.value.dash_glyphs == (glyph,)
    assert v.value.normalized_value is None and v.value.parsed_value is None
    assert v.eligibility == "normalization_required" and v.normalization_reasons == ("value_reported_nil",)


@pytest.mark.parametrize("raw,state", [("", "blank"), ("   ", "blank"), ("nil", "reported_nil_word"), ("NIL", "reported_nil_word"),
                                       ("N/A", "reported_not_applicable"), ("n.a.", "reported_not_applicable"),
                                       ("Not applicable", "reported_not_applicable"), ("see note 4", "not_a_number")])
def test_blank_nil_and_na_words_have_explicit_states(raw, state):
    v = fv.normalize_value(raw, None, "text", None, "not_a_number", "currency_amount", 1000, "header_zone", "LKR")
    assert v.state == state and v.normalized_value is None and v.status == "normalization_required"


def test_no_nil_representation_ever_becomes_numeric_zero():
    for raw, rep in (("-", "dash_nil"), ("–", "dash_nil"), ("—", "dash_nil"), ("", "text"), ("nil", "text"), ("N/A", "text")):
        v = fv.normalize_value(raw, None, rep, None, "nil", "currency_amount", 1000, "header_zone", "LKR")
        assert v.normalized_value is None and v.parsed_value is None, raw
    with pytest.raises(fv.ValidationInputError):
        fv.normalize_value("-", 0.0, "dash_nil", None, "nil", "currency_amount", 1000, "header_zone", "LKR")
    assert fv.classify_value("-", Decimal(0), "dash_nil")[0] == "inconsistent_input"


# --- precision ----------------------------------------------------------------------------------------------------

def test_exact_agreement():
    r = fv.compare_values(num("1,234", "1234"), num("1,234", "1234"))
    assert (r.outcome, r.abs_difference, r.tolerance) == ("agree", Decimal(0), Decimal(1000))


def test_half_unit_boundary_and_just_outside():
    """Rs Mn 215,670 vs Rs '000 215,670,500: tolerance 500,000 + 500."""
    mn = num("215,670", "215670", scale=1000000)
    r = fv.compare_values(mn, num("215,670,500", "215670500"))                                        # Rs '000
    assert (r.outcome, r.abs_difference, r.tolerance) == ("agree", Decimal(500000), Decimal("500500"))   # 500,000 <= 500,500
    r = fv.compare_values(mn, num("215,670,501", "215670501"))                                        # just outside
    assert (r.outcome, r.abs_difference) == ("disagree", Decimal(501000))
    r = fv.compare_values(mn, num("215,671,000", "215671000"))
    assert (r.outcome, r.abs_difference) == ("disagree", Decimal(1000000))


def test_boundary_is_inclusive():
    a, b = num("100", "100", scale=1), num("101", "101", scale=1)            # diff 1 == 0.5 + 0.5
    assert fv.compare_values(a, b).outcome == "agree"
    a, b = num("100.0", "100.0", scale=1, decimals=1), num("101", "101", scale=1)   # diff 1 > 0.05 + 0.5
    assert fv.compare_values(a, b).outcome == "disagree"


def test_decimals_negatives_and_scaled_values():
    a = num("(1,234.5)", "-1234.5", scale=1000, decimals=1, rep="parenthesised_negative")
    b = num("(1,234,550)", "-1234550", scale=1, rep="parenthesised_negative")
    r = fv.compare_values(a, b)
    assert r.a_value == Decimal("-1234500.0") and r.abs_difference == Decimal("50.0") and r.tolerance == Decimal("50.50")
    assert r.outcome == "agree"
    assert fv.compare_values(num("12.34", "12.34", scale=1, decimals=2, value_type="per_share_amount"),
                             num("12.35", "12.35", scale=1, decimals=2, value_type="per_share_amount")).outcome == "agree"


def test_no_float_rounding_anywhere():
    a = num("0.1", "0.1", scale=1, decimals=1)
    b = num("0.2", "0.2", scale=1, decimals=1)
    r = fv.compare_values(a, b)
    assert r.abs_difference == Decimal("0.1") and all(isinstance(x, Decimal) for x in (r.a_value, r.tolerance, r.abs_difference))
    big = num("999,999,999,999.99", "999999999999.99", scale=1000000000, decimals=2)
    assert big.normalized_value == Decimal("999999999999990000000.00")


def test_incomparable_when_not_normalised_or_different_unit():
    nil = fv.normalize_value("-", None, "dash_nil", None, "nil", "currency_amount", 1000, "header_zone", "LKR")
    assert fv.compare_values(nil, num("0", "0")).outcome == "incomparable"
    assert fv.compare_values(num("1", "1", scale=1, value_type="per_share_amount"), num("1", "1", scale=1)).reason == "value_type_differs"


# --- borrowings maturity ------------------------------------------------------------------------------------------

def test_current_and_non_current_borrowings_stay_distinct():
    nc = fv.borrowing_maturity("interest_bearing_borrowings", "Non-current liabilities")
    cu = fv.borrowing_maturity("interest_bearing_borrowings", "Current Liabilities")
    assert (nc.maturity, cu.maturity) == ("non_current", "current") and nc != cu
    assert fv.borrowing_maturity("interest_bearing_borrowings", "NON CURRENT LIABILITIES").maturity == "non_current"
    v1 = val(concept_key="interest_bearing_borrowings", statement_kind="financial_position", period_kind="instant",
             period_class=None, start_date=None, duration_months=None, section_label_raw="Non-Current Liabilities")
    v2 = val(concept_key="interest_bearing_borrowings", statement_kind="financial_position", period_kind="instant",
             period_class=None, start_date=None, duration_months=None, section_label_raw="Current Liabilities",
             source_key=("run1", 0, 7, 0, 0, "interest_bearing_borrowings"))
    assert (v1.maturity.maturity, v2.maturity.maturity) == ("non_current", "current")


@pytest.mark.parametrize("section,basis", [(None, "no_section_label"), ("Equity", "section_not_maturity"),
                                           ("Financial Liabilities at Amortised Cost", "section_not_maturity"),
                                           ("Current and non-current liabilities", "section_maturity_ambiguous")])
def test_unrelated_section_labels_create_no_maturity(section, basis):
    assert fv.borrowing_maturity("interest_bearing_borrowings", section) == fv.MaturityResult(None, basis)


def test_maturity_only_for_borrowings():
    assert fv.borrowing_maturity("total_liabilities", "Current liabilities") == fv.MaturityResult(None, "not_applicable")


# --- statement columns for sign + arithmetic ----------------------------------------------------------------------

def column(values, kind="profit_or_loss", col=0, scale=1000, **over):
    """values: [(concept, raw, parsed, rep)] -> contexts in one statement column (rows in the given order)."""
    out = []
    for i, (concept, raw, parsed, rep) in enumerate(values):
        instant = kind == "financial_position"
        base = dict(source_key=("run1", 0, i, col, 0, concept), concept_key=concept, row_index=i, raw_value=raw,
                    parsed_value=None if parsed is None else Decimal(parsed), representation_class=rep,
                    sign_as_printed="negative" if rep == "parenthesised_negative" else "positive",
                    statement_kind=kind, column_index=col, reported_scale=scale,
                    value_type="per_share_amount" if concept.startswith("eps") else "currency_amount",
                    period_kind="instant" if instant else "duration", period_class=None if instant else "3m",
                    start_date=None if instant else date(2026, 1, 1), duration_months=None if instant else 3)
        base.update(over)
        out.append(ctx(**base))
    return out


P, N = "numeric", "parenthesised_negative"


def results(contexts, run=RUN):
    return fv.validate_run(contexts, run)


def only(rv, check):
    got = [a for a in rv.arithmetic if a.check_id == check]
    assert len(got) == 1, got
    return got[0]


def test_a1_pass_fail_and_insufficient():
    sfp = [("total_assets", "1,000", "1000", P), ("total_liabilities", "600", "600", P), ("total_equity", "400", "400", P)]
    assert only(results(column(sfp, "financial_position")), "A1").outcome == "pass"
    bad = sfp[:2] + [("total_equity", "390", "390", P)]
    r = only(results(column(bad, "financial_position")), "A1")
    assert r.outcome == "fail" and r.difference_as_printed == Decimal(10000) and r.tolerance == Decimal("1500")
    r = only(results(column(sfp[:2], "financial_position")), "A1")
    assert (r.outcome, r.reasons) == ("insufficient_evidence", ("missing:total_equity",))
    r = only(results(column(sfp[1:], "financial_position")), "A1")
    assert r.outcome == "not_applicable"


def test_a2_pass_with_cost_printed_negative():
    r = only(results(column([("revenue", "1,000", "1000", P), ("cost_of_sales", "(600)", "-600", N),
                             ("gross_profit", "400", "400", P)])), "A2")
    assert (r.outcome, r.variant) == ("pass", "as_printed")
    assert [t.concept_key for t in r.terms] == ["gross_profit", "revenue", "cost_of_sales"]
    assert [t.raw_value for t in r.terms] == ["400", "1,000", "(600)"]                         # exact inputs preserved


def test_a3_normal_sign_and_reversed_tax():
    normal = [("profit_before_tax", "500", "500", P), ("income_tax_expense", "(150)", "-150", N), ("profit_for_period", "350", "350", P)]
    r = only(results(column(normal)), "A3")
    assert (r.outcome, r.variant) == ("pass", "as_printed")
    reversed_tax = [("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P), ("profit_for_period", "350", "350", P)]
    r = only(results(column(reversed_tax)), "A3")
    assert (r.outcome, r.variant) == ("pass", "reversed_income_tax_expense")
    r = only(results(column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P),
                             ("profit_for_period", "300", "300", P)])), "A3")
    assert r.outcome == "fail" and r.difference_as_printed == Decimal(350000) and r.difference_reversed == Decimal(50000)


def test_a4_pass_and_fail_per_attribution_block():
    rows = [("profit_for_period", "350", "350", P), ("profit_attributable_to_owners", "300", "300", P),
            ("profit_attributable_to_nci", "50", "50", P), ("profit_attributable_to_owners", "320", "320", P),
            ("profit_attributable_to_nci", "60", "60", P)]
    got = [a for a in results(column(rows)).arithmetic if a.check_id == "A4"]
    assert [a.outcome for a in got] == ["pass", "fail"]                                        # the second block (e.g. TCI) fails
    assert [t.raw_value for t in got[1].terms] == ["350", "320", "60"]


def test_a5_pass():
    r = only(results(column([("interest_income", "1,000", "1000", P), ("interest_expense", "(700)", "-700", N),
                             ("net_interest_income", "300", "300", P)])), "A5")
    assert (r.outcome, r.variant) == ("pass", "as_printed")


def test_arithmetic_never_mutates_inputs_or_selects_a_value():
    cs = column([("total_assets", "1,000", "1000", P), ("total_liabilities", "600", "600", P),
                 ("total_equity", "400", "400", P), ("total_equity", "390", "390", P)], "financial_position")
    before = copy.deepcopy(cs)
    rv = results(cs)
    assert cs == before
    r = only(rv, "A1")
    assert (r.outcome, r.reasons, r.terms) == ("insufficient_evidence", ("multiple_values:total_equity",), ())
    assert all(v.value.normalized_value in (Decimal(1000000), Decimal(600000), Decimal(400000), Decimal(390000))
               for v in rv.candidates)


def test_columns_with_unknown_duration_or_period_do_not_break_grouping():
    """Real data (COMB): a duration column with no duration_months next to dated columns in one statement."""
    cs = column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "(150)", "-150", N),
                 ("profit_for_period", "350", "350", P)], col=0)
    cs += column([("profit_before_tax", "400", "400", P)], col=1, duration_months=None, period_class="unspecified",
                  start_date=None, candidate_status="unresolved")
    cs += column([("profit_before_tax", "300", "300", P)], col=2, period_kind=None, period_class=None, start_date=None,
                  end_date=None, duration_months=None, candidate_status="unresolved")
    rv = results(cs)
    assert [a.outcome for a in rv.arithmetic if a.check_id == "A3"].count("pass") == 1
    assert fv.canonical_json(rv) == fv.canonical_json(results(list(reversed(cs))))


def test_arithmetic_uses_values_regardless_of_issuer_evidence_but_needs_normalised_values():
    sfp = [("total_assets", "1,000", "1000", P), ("total_liabilities", "600", "600", P), ("total_equity", "400", "400", P)]
    assert only(results(column(sfp, "financial_position"), replace(RUN, issuer_link_status=None)), "A1").outcome == "pass"
    r = only(results(column(sfp, "financial_position", scale=None, scale_basis="statement_scale_unresolved")), "A1")
    assert (r.outcome, r.reasons) == ("insufficient_evidence", ("unusable:total_assets",))


# --- sign -----------------------------------------------------------------------------------------------------------

def signs(contexts):
    return {s.concept_key: s for s in results(contexts).signs}


def test_consistent_negative_cost_is_safe_as_printed():
    s = signs(column([("revenue", "1,000", "1000", P), ("cost_of_sales", "(600)", "-600", N), ("gross_profit", "400", "400", P)]))
    assert (s["cost_of_sales"].convention, s["cost_of_sales"].basis, s["cost_of_sales"].flip_required) == \
        ("contribution_as_printed", "A2_as_printed", False)
    assert s["cost_of_sales"].contribution_value == Decimal(-600000)
    s = signs(column([("finance_costs", "(80)", "-80", N)]))                                    # no relation: bracketed expense
    assert (s["finance_costs"].convention, s["finance_costs"].basis) == ("contribution_as_printed", "printed_negative_expense")


def test_mixed_tax_signs_are_decided_per_column_by_a3():
    col0 = column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "(150)", "-150", N),
                   ("profit_for_period", "350", "350", P)], col=0)
    col1 = column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P),
                   ("profit_for_period", "350", "350", P)], col=1, end_date=date(2025, 3, 31), start_date=date(2025, 1, 1))
    col2 = column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "20", "20", P),
                   ("profit_for_period", "520", "520", P)], col=2, role="comparative", end_date=date(2024, 3, 31),
                  start_date=date(2024, 1, 1))                                                  # a tax CREDIT printed positive
    rv = results(col0 + col1 + col2)
    tax = [s for s in rv.signs if s.concept_key == "income_tax_expense"]
    assert [(s.convention, s.flip_required, s.contribution_value) for s in tax] == [
        ("contribution_as_printed", False, Decimal(-150000)), ("magnitude_printed", True, Decimal(-150000)),
        ("contribution_as_printed", False, Decimal(20000))]
    assert ("run1", 0, "income_tax_expense", "mixed") in rv.statement_sign_conventions


def test_unsafe_convention_is_normalization_required_and_nothing_is_flipped():
    s = signs(column([("income_tax_expense", "150", "150", P)]))                                # positive, no A3 available
    assert (s["income_tax_expense"].status, s["income_tax_expense"].contribution_value, s["income_tax_expense"].reason) == \
        ("normalization_required", None, "sign_convention_unestablished")
    s = signs(column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P),
                      ("profit_for_period", "300", "300", P)]))                                  # A3 fails both ways
    assert s["income_tax_expense"].status == "normalization_required" and s["income_tax_expense"].basis == "A3_fail"


def test_no_silent_sign_flip_of_the_value_itself():
    cs = column([("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P), ("profit_for_period", "350", "350", P)])
    rv = results(cs)
    tax_val = [v for v in rv.candidates if v.concept_key == "income_tax_expense"][0]
    assert tax_val.value.normalized_value == Decimal(150000) and tax_val.value.sign_as_printed == "positive"
    tax_sign = [s for s in rv.signs if s.concept_key == "income_tax_expense"][0]
    assert tax_sign.flip_required and tax_sign.basis == "A3_reversed" and tax_sign.contribution_value == Decimal(-150000)
    assert {s.concept_key: s.convention for s in rv.signs}["profit_before_tax"] == "contribution_as_printed"


def test_balance_sheet_and_cash_flow_signs_are_not_applicable():
    s = signs(column([("total_assets", "1,000", "1000", P)], "financial_position"))
    assert (s["total_assets"].convention, s["total_assets"].status) == ("not_applicable", "not_applicable")


# --- F5 adapter + determinism -----------------------------------------------------------------------------------------

F5_RESULT = {
    "statements": [{"statement_index": 0, "statement_kind": "financial_position", "continuation_of": None},
                   {"statement_index": 1, "statement_kind": "financial_position", "continuation_of": 0}],
    "columns": [{"statement_index": s, "column_index": 0, "period_kind": "instant", "period_class": None, "start_date": None,
                 "end_date": "2026-03-31", "duration_months": None, "role": "current", "role_trust": "trusted",
                 "fiscal_label": None, "reported_scope": "group", "canonical_scope": "consolidated"} for s in (0, 1)],
    "rows": [{"statement_index": 0, "row_index": 5, "label_raw": "Total assets", "section_label_raw": None,
              "operations": "total_or_unstated", "operations_basis": "none"},
             {"statement_index": 1, "row_index": 2, "label_raw": "Borrowings", "section_label_raw": "Non-current liabilities",
              "operations": "total_or_unstated", "operations_basis": "none"},
             {"statement_index": 1, "row_index": 9, "label_raw": "Total liabilities", "section_label_raw": None,
              "operations": "total_or_unstated", "operations_basis": "none"},
             {"statement_index": 1, "row_index": 12, "label_raw": "Total equity", "section_label_raw": None,
              "operations": "total_or_unstated", "operations_basis": "none"}],
    "candidates": [dict(statement_index=s, row_index=r, column_index=0, value_ordinal=0, concept_key=k, mapping_status="mapped",
                        ambiguous_concepts=[], period_kind="instant", period_class=None, period_derivation="column",
                        value_type="currency_amount", attribution="not_applicable", raw_value=raw, parsed_value=pv,
                        representation_class="numeric", printed_decimals=0, sign_as_printed="positive", reported_scale=1000,
                        scale_basis="header_zone", reported_currency="LKR", f4_status="extracted", candidate_status="proposed")
                   for s, r, k, raw, pv in ((0, 5, "total_assets", "1,000", "1000"), (1, 2, "interest_bearing_borrowings", "200", "200"),
                                            (1, 9, "total_liabilities", "600", "600"), (1, 12, "total_equity", "400", "400"))],
}


def test_f5_adapter_joins_continuation_pages_into_one_arithmetic_group():
    cs = fv.contexts_from_f5_result(F5_RESULT, run_key="run9")
    assert {c.statement_root for c in cs} == {0} and len(cs) == 4
    rv = fv.validate_run(cs, RUN)
    assert only(rv, "A1").outcome == "pass"
    b = [v for v in rv.candidates if v.concept_key == "interest_bearing_borrowings"][0]
    assert b.maturity.maturity == "non_current" and b.eligibility == "eligible"


def test_same_input_gives_byte_identical_output():
    cs = fv.contexts_from_f5_result(F5_RESULT, run_key="run9") + tuple(column(
        [("profit_before_tax", "500", "500", P), ("income_tax_expense", "150", "150", P), ("profit_for_period", "350", "350", P)]))
    a = fv.canonical_json(fv.validate_run(cs, RUN))
    b = fv.canonical_json(fv.validate_run(list(reversed(cs)), RUN))           # input order must not matter
    assert a == b and f'"version":"{V}"' in a
    assert fv.canonical_json(fv.summarize(fv.validate_run(cs, RUN))) == fv.canonical_json(fv.summarize(fv.validate_run(cs, RUN)))
