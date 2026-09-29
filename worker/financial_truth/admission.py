"""
f6.admission.1 (docs/F6.2_DESIGN.md §3, §4): for EVERY F5 candidate of one F5 run, F6.1 (f6.validation.1) runs
unchanged and admission decides whether its result becomes a source-observation member. Admission only ever adds
refusals, with exactly one lift: an OP1 pass lifts F6.1's operations_section_derived_on_total_row, and nothing else.

  A-1 value     numeric: F6.1 eligible. nil: F6.1 normalization_required whose ONLY normalisation reason is
                value_reported_nil (a printed dash) or value_reported_nil_word (Nil / None), with no ineligibility.
                Anything else is refused: blank, N/A, non-numeric, scale or currency problems, any other reason, and
                every F6.1 ineligibility (F5 unresolved / ambiguous / conflicting included). Nil is never zero.
  A-2 issuer    the filing's issuer-link decision is 'evidenced' AND its basis is listing_symbol_sec_id or both. A link
                resting on document_path_prefix alone is refused (issuer_link_path_prefix_only). F5's rules decide the
                link; F6 only refuses to rely on the weak basis.
  A-3 ops       basis row_label / none: F6.1's result stands. basis section_label, on any row: admitted only through an
                OP1 pass for the row's group (operations_section_derived_unvalidated:<outcome>).
  A-4 maturity  interest_bearing_borrowings needs F6.1 maturity current or non_current (maturity_undetermined:<basis>).
  A-5 scope     group, company and bank as reported; F5 'unstated' becomes 'unlabelled'. Scope is never inferred.
  A-6 identity  every identity field present (checked once A-1..A-5 hold).

Every rule is evaluated for every candidate and every refusal is recorded. F6.1's result, reasons included, is kept
unchanged beside the admission.
"""
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .. import financial_validation as fv
from . import op1 as op1_rule
from .canonical import digest
from .identity import MATURITY_NOT_APPLICABLE, EconomicFactIdentity
from .inputs import (DocumentContext, F5RunRef, InputError, IssuerLinkDecision, PublicationInput, check_consistency,
                     publication_input)
from .versions import ADMISSION_VERSION, IDENTITY_VERSION, VersionSet

NIL_ONLY = (("value_reported_nil",), ("value_reported_nil_word",))
ADMISSIBLE_ISSUER_BASES = ("listing_symbol_sec_id", "both")
IDENTITY_SCOPE = {"group": "group", "company": "company", "bank": "bank", "unstated": "unlabelled"}
OP1_LIFTABLE = "operations_section_derived_on_total_row"
UMBRELLA_REASONS = ("validation_ineligible", "normalization_not_admissible")


@dataclass(frozen=True)
class Admission:
    version: str
    admitted: bool
    value_kind: Optional[str]            # numeric | nil (admitted candidates only)
    nil_form: Optional[tuple]            # ('reported_nil', <dash glyph names>...) | ('reported_nil_word', <word>)
    reasons: tuple                       # admission refusals, in rule order A-1..A-6
    lifted_reasons: tuple                # F6.1 reasons an OP1 pass lifted: () or (operations_section_derived_on_total_row,)
    operations_route: Optional[str]      # row_label | none | section_label+op1 (None: operations not admissible)
    op1_record: Optional[str]            # key of the OP1 record that judged this row
    identity: Optional[EconomicFactIdentity]
    ef_key: Optional[str]


@dataclass(frozen=True)
class CandidateAttributes:
    """F5 column attributes a source observation keeps (never part of the identity), exactly as F5 persisted them."""
    role_basis: Optional[str]
    period_evidence_source: Optional[str]
    printed_start: Optional[date]
    reported_scope_basis: Optional[str]
    canonical_scope_basis: Optional[str]
    audit_label_reported: Optional[str]
    audit_evidence_source: Optional[str]
    audit_trust: Optional[str]
    audit_rule_id: Optional[str]
    restated: bool


@dataclass(frozen=True)
class CandidateResult:
    key: str                             # candidate-validation key: SHA-256 of (validation-run key, source key)
    source_key: tuple                    # (F5 run id, statement, row, column, value ordinal, concept)
    candidate_id: Optional[int]          # financial_fact_candidates.id; None for an in-memory F5 result
    input_hash: str                      # SHA-256 of CandidateContext + RunEvidence (docs §4)
    context: fv.CandidateContext         # F6.1's input, exactly
    attributes: CandidateAttributes
    validation: fv.CandidateValidation   # F6.1's output, unchanged
    admission: Admission
    output_hash: str                     # SHA-256 of validation + admission


@dataclass(frozen=True)
class ValidationRun:
    key: str                             # SHA-256 of (versions, F5 run id, input hash)
    versions: VersionSet
    f5_run: F5RunRef
    issuer_link: Optional[IssuerLinkDecision]
    publication: PublicationInput
    document: DocumentContext
    run_evidence: fv.RunEvidence         # exactly what F6.1 received
    candidates: tuple                    # CandidateResult for EVERY candidate, in source-key order
    arithmetic: tuple                    # F6.1 A1-A5 diagnostics
    signs: tuple                         # F6.1 sign interpretation (never used to make values agree)
    statement_sign_conventions: tuple
    op1: tuple                           # OP1Record
    input_hash: str
    output_hash: str


def _as_date(value):
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    return date.fromisoformat(str(value))


def _nil_form(value):
    if value.state == "reported_nil":
        return ("reported_nil",) + tuple(value.dash_glyphs)
    if value.state == "reported_nil_word":
        return ("reported_nil_word",
                " ".join(unicodedata.normalize("NFKC", value.raw_value or "").strip().casefold().split()))
    return None


def admit(ctx, validation, *, issuer_link, op1_records):
    """f6.admission.1 for one candidate. op1_records: {(arithmetic group key, concept): OP1Record} of its run."""
    route, op1_key, ops_reason, lift = op1_rule.operations_decision(ctx, op1_records)
    lifted = (OP1_LIFTABLE,) if lift and OP1_LIFTABLE in validation.ineligible_reasons else ()
    ineligible = tuple(r for r in validation.ineligible_reasons if r not in lifted)
    norm = tuple(validation.normalization_reasons)
    reasons, value_kind = [], None
    # A-1 value
    if ineligible:
        reasons.append("validation_ineligible")
    if norm and norm not in NIL_ONLY:
        reasons.append("normalization_not_admissible")
    if not ineligible:
        value_kind = "numeric" if not norm else ("nil" if norm in NIL_ONLY else None)
    # A-2 issuer
    issuer_id = None
    if issuer_link is None:
        reasons.append("issuer_link_not_supplied")
    elif issuer_link.status != "evidenced":
        reasons.append(f"issuer_link_not_evidenced:{issuer_link.status}")
    elif issuer_link.basis not in ADMISSIBLE_ISSUER_BASES:
        reasons.append("issuer_link_path_prefix_only")
    else:
        issuer_id = issuer_link.issuer_id
    # A-3 operations
    if ops_reason is not None:
        reasons.append(ops_reason)
    elif route in ("row_label", "none") and validation.operations.trust != "trusted":
        route = None                     # F6.1 refused the value itself (operations_invalid): validation_ineligible
    # A-4 maturity
    borrowing = ctx.concept_key in fv.BORROWING_CONCEPTS
    maturity = validation.maturity.maturity
    if borrowing and maturity not in ("current", "non_current"):
        reasons.append(f"maturity_undetermined:{validation.maturity.basis}")
    # A-5 scope
    scope = IDENTITY_SCOPE.get(ctx.reported_scope)
    if scope is None:
        reasons.append(f"scope_unrecognised:{ctx.reported_scope}")
    # A-6 identity completeness
    identity = None
    if not reasons:
        period, value = validation.period, validation.value
        present = (("issuer_id", issuer_id), ("concept_key", ctx.concept_key),
                   ("period_kind", period.period_kind), ("period_end", period.end_date), ("scope", scope),
                   ("operations", validation.operations.operations), ("currency", value.currency))
        missing = [name for name, v in present if v is None]
        if period.period_kind == "duration" and period.duration_months is None:
            missing.append("duration_months")
        if missing:
            reasons.extend(f"identity_incomplete:{name}" for name in missing)
        else:
            identity = EconomicFactIdentity(
                IDENTITY_VERSION, issuer_id, ctx.concept_key, period.period_kind, period.end_date,
                period.duration_months if period.period_kind == "duration" else None, scope,
                validation.operations.operations, maturity if borrowing else MATURITY_NOT_APPLICABLE, value.currency)
    admitted = not reasons
    return Admission(ADMISSION_VERSION, admitted, value_kind if admitted else None,
                     _nil_form(validation.value) if admitted and value_kind == "nil" else None, tuple(reasons), lifted,
                     route, op1_key, identity, identity.ef_key if identity is not None else None)


def candidate_attributes(result, f5_run_id):
    """{source key: CandidateAttributes} from an F5 build() result (keys as F6.1's contexts_from_f5_result forms them)."""
    columns = {(c["statement_index"], c["column_index"]): c for c in result["columns"]}
    out = {}
    for c in result["candidates"]:
        col = columns[(c["statement_index"], c["column_index"])]
        key = (f5_run_id, c["statement_index"], c["row_index"], c["column_index"], c["value_ordinal"],
               c["concept_key"] or "")
        out[key] = CandidateAttributes(col.get("role_basis"), col.get("period_evidence_source"),
                                       _as_date(col.get("start_date")), col.get("reported_scope_basis"),
                                       col.get("canonical_scope_basis"), col.get("audit_label_reported"),
                                       col.get("audit_evidence_source"), col.get("audit_trust"), col.get("audit_rule_id"),
                                       bool(col.get("restated")))
    return out


def validate_run(f5_result, *, f5_run, issuer_link, uploaded_at, document):
    """One validation run: F6.1 over every candidate of ONE F5 run, the OP1 records, then admission.

    f5_result: the F5 build() result (in memory or its JSON form); f5_run: its F5RunRef; issuer_link: the filing's
    IssuerLinkDecision or None; uploaded_at: report_filings.uploaded_at (f6.inputs.1); document: DocumentContext."""
    if issuer_link is not None and not isinstance(issuer_link, IssuerLinkDecision):
        raise InputError("issuer_link must be an IssuerLinkDecision or None")
    check_consistency(f5_result, f5_run, issuer_link, document)
    publication = publication_input(uploaded_at)
    evidence = fv.run_evidence(issuer_link.status if issuer_link is not None else None, publication.publication_date,
                               document.f6_1_classification())
    contexts = fv.contexts_from_f5_result(f5_result, run_key=f5_run.f5_run_id)
    by_key = {c.source_key: c for c in contexts}
    if len(by_key) != len(contexts):
        raise InputError("two F5 candidates share one source key")
    attributes = candidate_attributes(f5_result, f5_run.f5_run_id)
    checked = fv.validate_run(contexts, evidence)                      # F6.1, unchanged
    pairs = [(by_key[v.source_key], v) for v in checked.candidates]
    records = op1_rule.evaluate(pairs)
    index = {(r.group_key, r.concept_key): r for r in records}
    versions = VersionSet()
    input_hash = digest({"versions": versions, "f5_run": f5_run, "issuer_link": issuer_link, "publication": publication,
                         "document": document, "run_evidence": evidence,
                         "candidates": [[ctx, attributes[ctx.source_key]] for ctx, _ in pairs]})
    key = digest(["validation_run", versions, f5_run.f5_run_id, input_hash])
    results = []
    for ctx, val in pairs:
        adm = admit(ctx, val, issuer_link=issuer_link, op1_records=index)
        results.append(CandidateResult(digest(["candidate_validation", key, list(ctx.source_key)]), tuple(ctx.source_key),
                                       ctx.candidate_id, digest({"context": ctx, "run_evidence": evidence}), ctx,
                                       attributes[ctx.source_key], val, adm,
                                       digest({"validation": val, "admission": adm})))
    output_hash = digest({"candidates": [[r.key, r.output_hash] for r in results], "arithmetic": checked.arithmetic,
                          "signs": checked.signs, "statement_sign_conventions": checked.statement_sign_conventions,
                          "op1": records})
    return ValidationRun(key, versions, f5_run, issuer_link, publication, document, evidence, tuple(results),
                         checked.arithmetic, checked.signs, checked.statement_sign_conventions, records, input_hash,
                         output_hash)


def refusal_reasons(result):
    """Every reason one candidate was not admitted: the F6.1 reasons that still apply, then admission's own."""
    a, v = result.admission, result.validation
    if a.admitted:
        return ()
    out = [r for r in v.ineligible_reasons if r not in a.lifted_reasons]
    if "normalization_not_admissible" in a.reasons:
        out.extend(v.normalization_reasons)
    out.extend(r for r in a.reasons if r not in UMBRELLA_REASONS)
    return tuple(out)


def summarize(validation_runs):
    """Deterministic counts over validation runs (for reports)."""
    def ordered(counter):
        return dict(sorted(counter.items()))
    admission, refusals, own, eligibility, op1_records, op1_rows = (Counter() for _ in range(6))
    prefix = "operations_section_derived_unvalidated:"
    for vr in validation_runs:
        for c in vr.candidates:
            a = c.admission
            admission[f"admitted_{a.value_kind}" if a.admitted else "not_admitted"] += 1
            eligibility[c.validation.eligibility] += 1
            own.update(a.reasons)
            refusals.update(refusal_reasons(c))
            if c.context.operations_basis == "section_label":          # the OP1 outcome for this row
                op1_rows[next((r[len(prefix):] for r in a.reasons if r.startswith(prefix)), "pass")] += 1
        op1_records.update(r.outcome for r in vr.op1)
    return {"candidates": sum(admission.values()), "admission": ordered(admission),
            "f6_1_eligibility": ordered(eligibility), "admission_reasons": ordered(own),
            "refusal_reasons": ordered(refusals), "op1_records": ordered(op1_records),
            "op1_section_derived_rows": ordered(op1_rows)}
