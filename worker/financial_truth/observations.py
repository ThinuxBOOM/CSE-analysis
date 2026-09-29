"""
Source observations (docs/F6.2_DESIGN.md §5, §8.1): one F5 run's admitted evidence for one economic fact.

An SO belongs to exactly one validation run - so to one F5 run, one filing and one document - and to one ef_key. Its
members are that run's admitted candidates with that identity (several columns of one document can show one fact).
An SO never spans F5 runs or documents.

  consistent              every member nil; or every member numeric and every pair agreeing under F6.1's
                          compare_values (V8: |a - b| <= h_a + h_b, inclusive; same currency and value type)
  internally_conflicting  nil mixed with numeric, or two numeric members disagreeing: no representative, no interval

A consistent numeric SO has the interval [max(v - h), min(v + h)] over its members (never empty, because the members
agree pairwise) and a representative: the member with the smallest half-unit. Several at that half-unit with the SAME
value give that value (the first in member order is cited); with DIFFERENT values there is no representative
(representative_ambiguous). A nil SO has the value nil: no interval, no normalised value, never 0. Reported values are
copied verbatim from F5; F6.1's normalised values sit beside them.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Optional

from .. import financial_validation as fv
from . import arith
from .canonical import digest
from .identity import EconomicFactIdentity
from .inputs import DocumentContext, F5RunRef, InputError
from .versions import VersionSet

STATUSES = ("consistent", "internally_conflicting")
SO_ANNOTATIONS = ("agreement_within_precision_only", "multiple_roles", "representative_ambiguous")


@dataclass(frozen=True)
class ReportedValue:
    """A value exactly as F5 persisted it. Never mutated."""
    raw_value: str
    parsed_value: Optional[Decimal]
    representation_class: str
    printed_decimals: Optional[int]
    sign_as_printed: str
    reported_scale: Optional[int]
    scale_basis: Optional[str]
    currency: Optional[str]
    value_type: Optional[str]


def reported_value(ctx):
    return ReportedValue(ctx.raw_value, ctx.parsed_value, ctx.representation_class, ctx.printed_decimals,
                         ctx.sign_as_printed, ctx.reported_scale, ctx.scale_basis, ctx.reported_currency, ctx.value_type)


@dataclass(frozen=True)
class SOMember:
    source_key: tuple                    # (F5 run id, statement, row, column, value ordinal, concept)
    candidate_id: Optional[int]          # financial_fact_candidates.id
    candidate_validation_key: str
    statement_index: int                 # statement / row / column / ordinal index the retained F4 cell
    row_index: int
    column_index: int
    value_ordinal: int
    value_kind: str                      # numeric | nil
    nil_form: Optional[tuple]
    reported: ReportedValue              # verbatim F5
    value: fv.ValueResult                # F6.1 normalisation: normalized_value, half_unit, currency, value_type
    role: str                            # current | comparative (an attribute, never identity)
    role_basis: Optional[str]
    period_derivation: str               # column | duration_column_end
    printed_start: Optional[date]
    fiscal_label: Optional[str]          # passed through by F6.1 only under a documented, trusted F3 fiscal year-end
    reported_scope: str
    reported_scope_basis: Optional[str]
    canonical_scope: str
    canonical_scope_basis: Optional[str]
    operations_basis: str
    operations_route: str                # row_label | none | section_label+op1
    op1_record: Optional[str]
    maturity_basis: str
    audit_label_reported: Optional[str]
    audit_evidence_source: Optional[str]
    audit_trust: Optional[str]
    restated: bool
    statement_kind: str
    label_raw: str
    section_label_raw: Optional[str]


@dataclass(frozen=True)
class MemberComparison:
    a: tuple                             # member source keys
    b: tuple
    outcome: str                         # agree | disagree
    reason: Optional[str]                # nil_vs_numeric for a nil / numeric pair
    comparison: Optional[fv.ComparisonResult]    # F6.1's V8 detail for a numeric pair
    sign_only: bool                      # disagrees, but would agree with one sign reversed (annotation only)


@dataclass(frozen=True)
class SourceObservation:
    so_key: str                          # SHA-256 of (validation-run key, ef_key)
    ef_key: str
    identity: EconomicFactIdentity
    validation_run_key: str
    versions: VersionSet
    f5_run: F5RunRef                     # filing, document SHA-256, recorded_at and timestamp evidence
    document: DocumentContext            # F3 document / underlying type (attributes only)
    members: tuple                       # SOMember, in member order
    observation_status: str              # consistent | internally_conflicting
    value_kind: Optional[str]            # numeric | nil; None when nil and numeric members are mixed
    nil_forms: tuple
    representative_member: Optional[tuple]
    reported: Optional[ReportedValue]    # verbatim copy of the representative member's F5 value
    normalized_value: Optional[Decimal]  # the representative's (numeric only)
    half_unit: Optional[Decimal]         # the representative's (numeric only)
    precision: Optional[Decimal]         # the smallest member half-unit (consistent numeric SOs)
    interval_low: Optional[Decimal]
    interval_high: Optional[Decimal]
    roles: tuple                         # the members' roles
    annotations: tuple                   # SO_ANNOTATIONS that apply
    comparisons: tuple                   # MemberComparison for every member pair
    output_hash: str

    @property
    def document_sha256(self):
        return self.f5_run.document_sha256

    @property
    def cse_filing_id(self):
        return self.f5_run.cse_filing_id


def member_order(m):
    return (m.statement_index, m.row_index, m.column_index, m.value_ordinal, m.source_key[5])


def compare_members(a, b):
    """F6.1's compare_values (V8) for two observed values; nil agrees only with nil, never with a number (0 included)."""
    if a.value_kind == "nil" or b.value_kind == "nil":
        if a.value_kind == b.value_kind:
            return MemberComparison(a.source_key, b.source_key, "agree", None, None, False)
        return MemberComparison(a.source_key, b.source_key, "disagree", "nil_vs_numeric", None, False)
    cmp = fv.compare_values(a.value, b.value)
    if cmp.outcome == "incomparable":
        raise InputError(f"values {a.source_key} and {b.source_key} of one identity are incomparable ({cmp.reason})")
    sign_only = cmp.outcome == "disagree" and fv.compare_values(
        a.value, replace(b.value, normalized_value=arith.negate(b.value.normalized_value))).outcome == "agree"
    return MemberComparison(a.source_key, b.source_key, cmp.outcome, None, cmp, sign_only)


def finest(entries):
    """entries: [(half_unit, normalized_value, ref)] in canonical order -> (ref, ambiguous). The smallest half-unit
    wins; a tie with equal values gives the first ref; a tie with different values gives (None, True)."""
    h = min(e[0] for e in entries)
    tied = [e for e in entries if e[0] == h]
    if len({e[1] for e in tied}) > 1:
        return None, True
    return tied[0][2], False


def interval(values):
    """[max(v - h), min(v + h)] over ValueResults, exact."""
    return (max(arith.sub(v.normalized_value, v.half_unit) for v in values),
            min(arith.add(v.normalized_value, v.half_unit) for v in values))


def _member(result):
    ctx, val, adm, att = result.context, result.validation, result.admission, result.attributes
    return SOMember(result.source_key, result.candidate_id, result.key, ctx.statement_index, ctx.row_index,
                    ctx.column_index, result.source_key[4], adm.value_kind, adm.nil_form, reported_value(ctx), val.value,
                    ctx.role, att.role_basis, ctx.period_derivation, att.printed_start, val.period.fiscal_label,
                    ctx.reported_scope, att.reported_scope_basis, ctx.canonical_scope, att.canonical_scope_basis,
                    ctx.operations_basis, adm.operations_route, adm.op1_record, val.maturity.basis,
                    att.audit_label_reported, att.audit_evidence_source, att.audit_trust, att.restated,
                    ctx.statement_kind, ctx.label_raw, ctx.section_label_raw)


def observation(validation_run, results):
    """The SO of one validation run for one ef_key, from its admitted CandidateResults."""
    keys = {r.admission.ef_key for r in results}
    if not results or len(keys) != 1 or not all(r.admission.admitted for r in results):
        raise InputError("an SO needs admitted candidates that share one ef_key")
    ef_key = keys.pop()
    members = tuple(sorted((_member(r) for r in results), key=member_order))
    comparisons = tuple(compare_members(a, b) for i, a in enumerate(members) for b in members[i + 1:])
    kinds = {m.value_kind for m in members}
    roles = tuple(sorted({m.role for m in members}))
    annotations = {"multiple_roles"} if len(roles) > 1 else set()
    rep = precision = low = high = None
    if any(c.outcome == "disagree" for c in comparisons):
        status, value_kind = "internally_conflicting", (next(iter(kinds)) if len(kinds) == 1 else None)
    elif kinds == {"nil"}:
        status, value_kind, rep = "consistent", "nil", members[0]
    else:
        status, value_kind = "consistent", "numeric"
        values = [m.value for m in members]
        precision = min(v.half_unit for v in values)
        rep, ambiguous = finest([(m.value.half_unit, m.value.normalized_value, m) for m in members])
        if ambiguous:
            annotations.add("representative_ambiguous")
        if len({v.normalized_value for v in values}) > 1:
            annotations.add("agreement_within_precision_only")
        low, high = interval(values)
    numeric_rep = rep is not None and value_kind == "numeric"
    so = SourceObservation(
        digest(["source_observation", validation_run.key, ef_key]), ef_key, results[0].admission.identity,
        validation_run.key, validation_run.versions, validation_run.f5_run, validation_run.document, members, status,
        value_kind, tuple(sorted({m.nil_form for m in members if m.nil_form is not None})),
        rep.source_key if rep is not None else None, rep.reported if rep is not None else None,
        rep.value.normalized_value if numeric_rep else None, rep.value.half_unit if numeric_rep else None,
        precision, low, high, roles, tuple(sorted(annotations)), comparisons, "")
    return replace(so, output_hash=digest(so))


def build(validation_run):
    """Every source observation of one validation run, in ef_key order."""
    by_ef = defaultdict(list)
    for r in validation_run.candidates:
        if r.admission.admitted:
            by_ef[r.admission.ef_key].append(r)
    return tuple(observation(validation_run, results) for _, results in sorted(by_ef.items()))
