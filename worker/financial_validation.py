"""
Stage F6.1: validation / normalisation PRIMITIVES over F5 fact candidates (pure, deterministic, versioned).

    F5 financial_fact_candidates -> [F6.1 primitives] -> F6.2 identity -> F6.3 reconciliation -> financial_facts

Only primitives. Nothing here persists, links issuers, builds an economic-fact identity, reconciles candidates or
picks one candidate over another. It never decides an availability time, a scope policy for unlabelled
statements, a precedence between documents, or a global sign / dash semantics: where such a decision would be
needed the result carries an explicit reason ('normalization_required', 'undetermined', 'insufficient_evidence').

Inputs
- CandidateContext: one F5 candidate joined with its column, row and statement (contexts_from_f5_result builds
  them from an F5 build() result; later phases can build them from the persisted F5 rows).
- RunEvidence: supplied by the CALLER - the run's issuer-link status (F5 filing_issuer_links snapshot) and the date
  the caller treats as the publication date, plus F3's fiscal-year-end fields. F6.1 never chooses among
  uploaded / authorized / CDN timestamps and never reads CSE titles or manualDate.

Determinism: no clock, locale, network or database is read; every collection in an output is a tuple in an
explicit order; Decimal arithmetic runs in a local context that traps inexact results; canonical_json() is
byte-stable. Floats are rejected as inputs.

Rules (VALIDATION_VERSION; evidence: F6.0 discovery over 26 real filings)
- Eligibility: F5 candidate_status 'proposed'; one mapped, active concept; a trusted current/comparative role; the
  supplied issuer evidence is 'evidenced'; operations taken from the ROW label (or none claimed) - an operations
  value inherited from a SECTION label is not trusted on total rows (TOTAL_ROW_CONCEPTS); a valid period (below).
- Period: instant = end date, no start / duration / class; duration = end date + a positive duration_months whose
  class matches F5's rule, a printed start date (when present) must match the duration arithmetic; month-end is
  NOT required (e.g. years ending on the 25th). period_end after the supplied publication date is rejected.
  Fiscal labels are never derived; one supplied without a documented, trusted F3 fiscal year-end is rejected.
- Value: printed dashes are 'reported_nil' (never 0); blank / nil / N/A words have their own states; only amounts
  are normalised: normalized_value = parsed_value x reported_scale (exact), with the printed precision
  (half a printed unit, in normalised units). Scale is applied only when F5 carries a resolved power-of-ten
  scale; currency must be printed (never defaulted, never converted); a per-share value is never multiplied by
  a statement scale (its scale must be 1).
- Comparison: two normalised values agree when |a - b| <= half_unit(a) + half_unit(b).
- Borrowings maturity: current / non_current from the row's section label only; otherwise None (unmerged).
- Sign: printed sign is never changed. A contribution-to-profit value is reported only when the convention is
  established by an arithmetic relation in the same statement column (A2 / A3 / A5), or for an expense printed
  negative; otherwise 'normalization_required'.
- Arithmetic A1-A5: diagnostics only (pass / fail / not_applicable / insufficient_evidence) with their exact inputs.
"""
import calendar
import json
import re
import unicodedata
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from typing import Optional

from . import financial_concepts as fc

VALIDATION_VERSION = "f6.validation.1"

TRUSTED_ROLES = ("current", "comparative")
TRUSTED_F3_STATUSES = ("confirmed", "document_only")
AMOUNT_CLASSES = ("numeric", "parenthesised_negative", "minus_negative", "negative_zero")
NEGATIVE_CLASSES = ("parenthesised_negative", "minus_negative")
INCOME_KINDS = ("profit_or_loss", "comprehensive_income")

# Bottom-line rows that aggregate continuing + discontinued operations: an operations value inherited from a
# section heading is not trusted on them (F6.0: LOLC's total 'Profit/(loss) for the period' printed after a
# 'Discontinued operations' section was tagged discontinued).
TOTAL_ROW_CONCEPTS = ("profit_for_period", "profit_attributable_to_owners", "profit_attributable_to_nci",
                      "eps_basic", "eps_diluted")
BORROWING_CONCEPTS = ("interest_bearing_borrowings",)

DASH_GLYPHS = {"-": "hyphen_minus", "–": "en_dash", "—": "em_dash", "−": "minus_sign",
               "‒": "figure_dash", "―": "horizontal_bar"}
NIL_WORDS = ("nil", "none")
NOT_APPLICABLE_WORDS = ("n/a", "na", "n.a", "n.a.", "not applicable")

_LIABILITIES_RE = re.compile(r"\bliabilit(?:y|ies)\b")
_MATURITY_WORD_RE = re.compile(r"\b(non\s*[-–—]?\s*)?current\b")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class ValidationInputError(TypeError):
    """A programming error in the caller's input (e.g. a float amount): never a data outcome."""


# ------------------------------------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class RunEvidence:
    """Evidence the caller supplies for one extraction run. F6.1 does not look any of it up."""
    issuer_link_status: Optional[str]                 # 'evidenced' | 'conflict' | 'unresolved' | None (not supplied)
    publication_date: Optional[date]                  # the caller's publication date; None = not supplied
    fiscal_year_end_basis: Optional[str] = None       # F3: documented | inferred_only | conflicting | none
    fiscal_year_end_status: Optional[str] = None      # F3 status of the fiscal year-end
    period_status: Optional[str] = None               # F3 document period status


@dataclass(frozen=True)
class CandidateContext:
    """One F5 candidate with the column / row / statement fields F6.1 reads (all as persisted by F5)."""
    source_key: tuple                 # (run key, statement_index, row_index, column_index, value_ordinal, concept or '')
    concept_key: Optional[str]
    mapping_status: str
    candidate_status: str
    value_type: Optional[str]
    attribution: str
    period_kind: Optional[str]
    period_class: Optional[str]
    period_derivation: str
    raw_value: str
    parsed_value: Optional[Decimal]
    representation_class: str
    printed_decimals: Optional[int]
    sign_as_printed: str
    reported_scale: Optional[int]
    scale_basis: Optional[str]
    reported_currency: Optional[str]
    statement_index: int
    statement_root: int               # first statement of a continuation chain (F5 continuation_of)
    statement_kind: str
    column_index: int
    role: str
    role_trust: str
    start_date: Optional[date]
    end_date: Optional[date]
    duration_months: Optional[int]
    fiscal_label: Optional[str]
    reported_scope: str
    canonical_scope: str
    row_index: int
    label_raw: str
    section_label_raw: Optional[str]
    operations: str
    operations_basis: str
    candidate_id: Optional[int] = None
    ambiguous_concepts: tuple = ()


# ------------------------------------------------------------------------------------------------ outputs

@dataclass(frozen=True)
class PeriodResult:
    status: str                       # valid | invalid
    period_kind: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    duration_months: Optional[int]
    period_class: Optional[str]
    fiscal_label: Optional[str]       # passed through only when F3 documented a trusted fiscal year-end
    reasons: tuple                    # why invalid (ineligibility reasons)
    notes: tuple                      # informational (e.g. end_not_month_end); never a rejection


@dataclass(frozen=True)
class ValueResult:
    state: str                        # numeric | reported_nil | reported_nil_word | reported_not_applicable | blank |
                                      # not_an_amount | not_a_number | inconsistent_input
    raw_value: str
    representation_class: str
    dash_glyphs: tuple                # e.g. ('en_dash',) for a printed '–'
    parsed_value: Optional[Decimal]
    printed_decimals: Optional[int]
    sign_as_printed: str
    value_type: Optional[str]         # currency_amount | per_share_amount
    reported_scale: Optional[int]
    scale_basis: Optional[str]
    currency: Optional[str]           # exactly as reported; never defaulted
    normalized_value: Optional[Decimal]
    printed_unit: Optional[Decimal]   # one printed unit, in normalised units
    half_unit: Optional[Decimal]      # the printed precision: half a printed unit
    status: str                       # normalized | normalization_required
    reasons: tuple                    # why normalization is not possible
    rule: str                         # how normalized_value was computed


@dataclass(frozen=True)
class OperationsResult:
    operations: str
    basis: str
    trust: str                        # trusted | untrusted
    reason: Optional[str]


@dataclass(frozen=True)
class MaturityResult:
    maturity: Optional[str]           # current | non_current | None
    basis: str                        # section_label | not_applicable | no_section_label | section_not_maturity |
                                      # section_maturity_ambiguous


@dataclass(frozen=True)
class CandidateValidation:
    version: str
    source_key: tuple
    candidate_id: Optional[int]
    concept_key: Optional[str]
    eligibility: str                  # eligible | ineligible | normalization_required
    ineligible_reasons: tuple
    normalization_reasons: tuple
    period: PeriodResult
    value: ValueResult
    operations: OperationsResult
    maturity: MaturityResult


@dataclass(frozen=True)
class ComparisonResult:
    outcome: str                      # agree | disagree | incomparable
    reason: Optional[str]
    a_value: Optional[Decimal]
    b_value: Optional[Decimal]
    a_half_unit: Optional[Decimal]
    b_half_unit: Optional[Decimal]
    tolerance: Optional[Decimal]
    abs_difference: Optional[Decimal]
    currency: Optional[str]
    value_type: Optional[str]


@dataclass(frozen=True)
class Term:
    role: str                         # total | term
    concept_key: str
    source_key: tuple
    raw_value: str
    normalized_value: Decimal
    half_unit: Decimal
    currency: str


@dataclass(frozen=True)
class ArithmeticResult:
    check_id: str                     # A1..A5
    name: str
    group_key: tuple
    outcome: str                      # pass | fail | not_applicable | insufficient_evidence
    variant: Optional[str]            # as_printed | reversed_<term> | either (second term ~ 0)
    terms: tuple                      # Term(...) exactly as used
    total: Optional[Decimal]
    computed_as_printed: Optional[Decimal]
    computed_reversed: Optional[Decimal]
    tolerance: Optional[Decimal]
    difference_as_printed: Optional[Decimal]
    difference_reversed: Optional[Decimal]
    reasons: tuple


@dataclass(frozen=True)
class SignResult:
    source_key: tuple
    concept_key: str
    natural_sign: str
    printed_sign: str
    convention: str                   # contribution_as_printed | magnitude_printed | zero | not_applicable | undetermined
    basis: str
    contribution_safe: bool
    flip_required: bool               # True only when an arithmetic relation shows the value was printed as a magnitude
    contribution_value: Optional[Decimal]
    status: str                       # ok | not_applicable | normalization_required
    reason: Optional[str]


@dataclass(frozen=True)
class RunValidation:
    version: str
    candidates: tuple                 # CandidateValidation, sorted by source_key
    arithmetic: tuple                 # ArithmeticResult, sorted
    signs: tuple                      # SignResult, sorted by source_key
    statement_sign_conventions: tuple # (run key, statement_index, concept_key, convention), sorted; report only


# ------------------------------------------------------------------------------------------------ helpers

def _dec(v):
    if v is None:
        return None
    if isinstance(v, (bool, float)):
        raise ValidationInputError(f"amounts must be Decimal, int or str, not {type(v).__name__}")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        raise ValidationInputError("dates must be date objects or 'YYYY-MM-DD' strings, not datetimes")
    if isinstance(v, date):
        return v
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return date.fromisoformat(v)
    raise ValidationInputError(f"unparseable date {v!r}")


def _mul(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        return a * b


def _add(*xs):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        out = Decimal(0)
        for x in xs:
            out = out + x
        return out


def _sub(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        return a - b


def _half(a):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        return a / 2


def _is_power_of_ten(n):
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return False
    while n % 10 == 0:
        n //= 10
    return n == 1


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def expected_period_class(duration_months):
    """F5's duration classification (financial_candidates.period_class) for a duration column."""
    if not duration_months:
        return "unspecified"
    if duration_months in (3, 6, 9, 12):
        return f"{duration_months}m"
    return f"other_{int(duration_months)}m"


# ------------------------------------------------------------------------------------------------ eligibility

def check_operations(concept_key, operations, operations_basis):
    """Row-label operations are trusted; no operations claim ('total_or_unstated' / 'none') is trusted; a
    section-label operations value is NOT trusted on a total row (TOTAL_ROW_CONCEPTS)."""
    if operations not in ("total_or_unstated", "continuing", "discontinued") or \
            operations_basis not in ("row_label", "section_label", "none"):
        return OperationsResult(operations, operations_basis, "untrusted", "operations_invalid")
    if operations_basis == "none":
        if operations != "total_or_unstated":
            return OperationsResult(operations, operations_basis, "untrusted", "operations_invalid")
        return OperationsResult(operations, operations_basis, "trusted", None)
    if operations_basis == "row_label":
        return OperationsResult(operations, operations_basis, "trusted", None)
    if concept_key in TOTAL_ROW_CONCEPTS:
        return OperationsResult(operations, operations_basis, "untrusted", "operations_section_derived_on_total_row")
    return OperationsResult(operations, operations_basis, "trusted", None)


def eligibility_reasons(ctx: CandidateContext, run: RunEvidence):
    """Ineligibility reasons other than the period (ordered, stable codes)."""
    reasons = []
    if ctx.candidate_status != "proposed":
        reasons.append(f"candidate_status_{ctx.candidate_status}")
    concept = fc.BY_KEY.get(ctx.concept_key) if ctx.concept_key else None
    if ctx.mapping_status != "mapped" or ctx.concept_key is None:
        reasons.append("mapping_not_single_concept")
    elif concept is None:
        reasons.append("concept_unknown")
    elif concept.status != "active":
        reasons.append("concept_not_active")
    if ctx.role_trust != "trusted" or ctx.role not in TRUSTED_ROLES:
        reasons.append("role_untrusted")
    if run.issuer_link_status is None:
        reasons.append("issuer_evidence_not_supplied")
    elif run.issuer_link_status != "evidenced":
        reasons.append(f"issuer_evidence_{run.issuer_link_status}")
    return reasons


# ------------------------------------------------------------------------------------------------ period

def validate_period(period_kind, start_date, end_date, duration_months, period_class, fiscal_label, run: RunEvidence,
                    period_derivation="column"):
    """Period sanity. Never derives a quarter, a fiscal label, a start date or a fiscal year-end."""
    start, end = _date(start_date), _date(end_date)
    reasons, notes = [], []
    if period_derivation == "duration_column_end":       # an instant printed at a duration column's end
        start, duration_months, notes = None, None, ["instant_from_duration_column_end"]
    if period_kind not in ("instant", "duration"):
        reasons.append("period_kind_missing" if period_kind is None else "period_kind_invalid")
    if end is None:
        reasons.append("period_end_missing")
    if period_kind == "instant":
        if duration_months is not None:
            reasons.append("instant_with_duration")
        if start is not None:
            reasons.append("instant_with_start")
        if period_class is not None:
            reasons.append("instant_with_period_class")
    elif period_kind == "duration":
        if duration_months is None:
            reasons.append("duration_months_missing")
        elif isinstance(duration_months, bool) or not isinstance(duration_months, int) or duration_months <= 0:
            reasons.append("duration_months_invalid")
        else:
            if period_class != expected_period_class(duration_months):
                reasons.append("period_class_inconsistent")
            if start is None:
                notes.append("start_not_reported")
            elif end is not None:
                if start > end:
                    reasons.append("period_start_after_end")
                elif start != _add_months(end + timedelta(days=1), -duration_months):
                    reasons.append("period_start_inconsistent_with_duration")
    if end is not None and end.day != calendar.monthrange(end.year, end.month)[1]:
        notes.append("end_not_month_end")
    pub = _date(run.publication_date)
    if pub is None:
        reasons.append("publication_date_not_supplied")
    elif end is not None and end > pub:
        reasons.append("period_end_after_publication")
    label = None
    if fiscal_label is not None:
        if run.fiscal_year_end_basis is None:
            notes.append("fiscal_label_not_verifiable")
        elif run.fiscal_year_end_basis == "documented" and run.fiscal_year_end_status in TRUSTED_F3_STATUSES \
                and run.period_status in TRUSTED_F3_STATUSES:
            label = fiscal_label
        else:
            reasons.append("fiscal_label_without_documented_fye")
    return PeriodResult("invalid" if reasons else "valid", period_kind, start, end,
                        duration_months if period_kind == "duration" else None,
                        period_class if period_kind == "duration" else None, label, tuple(reasons), tuple(notes))


# ------------------------------------------------------------------------------------------------ value

def classify_value(raw_value, parsed_value, representation_class):
    """(state, dash glyph names). Printed nils are never numbers."""
    parsed = _dec(parsed_value)
    t = unicodedata.normalize("NFKC", raw_value or "").strip()
    if representation_class in AMOUNT_CLASSES:
        if parsed is None or (representation_class in NEGATIVE_CLASSES and not parsed < 0):
            return "inconsistent_input", ()
        return "numeric", ()
    if parsed is not None and representation_class == "dash_nil":
        return "inconsistent_input", ()
    if t == "":
        return "blank", ()
    compact = t.replace(" ", "")
    if 1 <= len(compact) <= 2 and all(ch in DASH_GLYPHS for ch in compact):
        return "reported_nil", tuple(DASH_GLYPHS[ch] for ch in compact)
    word = " ".join(t.casefold().split())
    if word in NIL_WORDS:
        return "reported_nil_word", ()
    if word in NOT_APPLICABLE_WORDS or word.replace(" ", "") in NOT_APPLICABLE_WORDS:
        return "reported_not_applicable", ()
    if representation_class in ("percentage", "comparison_bound", "spreadsheet_error"):
        return "not_an_amount", ()
    return "not_a_number", ()


def _scale_reason(scale_basis):
    return {"statement_scale_conflicting": "scale_conflicting", "statement_scale_unresolved": "scale_unresolved",
            "per_share_unit_not_stated": "per_share_unit_not_stated", "percentage_value": "scale_not_applicable",
            "row_label_percent": "scale_not_applicable"}.get(scale_basis, "scale_not_reported")


def normalize_value(raw_value, parsed_value, representation_class, printed_decimals, sign_as_printed, value_type,
                    reported_scale, scale_basis, reported_currency):
    """Exact normalisation of one printed value; never defaults, converts or invents anything."""
    parsed = _dec(parsed_value)
    state, glyphs = classify_value(raw_value, parsed, representation_class)
    reasons = []
    if state != "numeric":
        reasons.append(f"value_{state}")
    currency = reported_currency.strip() if isinstance(reported_currency, str) else None
    if not currency:
        reasons.append("currency_not_reported")
    elif not _CURRENCY_RE.match(currency):
        reasons.append("currency_unrecognised")
    if value_type not in ("currency_amount", "per_share_amount"):
        reasons.append("value_type_unknown")
    if reported_scale is None:
        reasons.append(_scale_reason(scale_basis))
    elif not _is_power_of_ten(reported_scale):
        reasons.append("scale_unrecognised")
    elif value_type == "per_share_amount" and reported_scale != 1:
        reasons.append("per_share_scale_not_unit")
    decimals = printed_decimals
    if state == "numeric" and decimals is None:
        decimals = max(0, -parsed.as_tuple().exponent)
    normalized = unit = half = None
    rule = "not_normalized"
    if not reasons:
        normalized = _mul(parsed, Decimal(reported_scale))
        unit = _mul(Decimal(1).scaleb(-decimals), Decimal(reported_scale))
        half = _half(unit)
        rule = "parsed_value*reported_scale" if value_type == "currency_amount" else "parsed_value*1(per_share)"
    return ValueResult(state, raw_value, representation_class, glyphs, parsed, decimals if state == "numeric" else None,
                       sign_as_printed, value_type, reported_scale, scale_basis, currency or None, normalized, unit, half,
                       "normalized" if not reasons else "normalization_required", tuple(reasons), rule)


def compare_values(a: ValueResult, b: ValueResult):
    """V8: agree when |a - b| <= half_unit(a) + half_unit(b); Decimal only."""
    if a.status != "normalized" or b.status != "normalized":
        return ComparisonResult("incomparable", "not_normalized", a.normalized_value, b.normalized_value,
                                a.half_unit, b.half_unit, None, None, None, None)
    if a.currency != b.currency:
        return ComparisonResult("incomparable", "currency_differs", a.normalized_value, b.normalized_value,
                                a.half_unit, b.half_unit, None, None, None, None)
    if a.value_type != b.value_type:
        return ComparisonResult("incomparable", "value_type_differs", a.normalized_value, b.normalized_value,
                                a.half_unit, b.half_unit, None, None, a.currency, None)
    tol = _add(a.half_unit, b.half_unit)
    diff = abs(_sub(a.normalized_value, b.normalized_value))
    return ComparisonResult("agree" if diff <= tol else "disagree", None, a.normalized_value, b.normalized_value,
                            a.half_unit, b.half_unit, tol, diff, a.currency, a.value_type)


# ------------------------------------------------------------------------------------------------ maturity

def borrowing_maturity(concept_key, section_label_raw):
    """current / non_current for borrowings, from the row's SECTION label only."""
    if concept_key not in BORROWING_CONCEPTS:
        return MaturityResult(None, "not_applicable")
    s = " ".join(unicodedata.normalize("NFKC", section_label_raw or "").casefold().split())
    if not s:
        return MaturityResult(None, "no_section_label")
    if not _LIABILITIES_RE.search(s):
        return MaturityResult(None, "section_not_maturity")
    kinds = sorted({"non_current" if m.group(1) else "current" for m in _MATURITY_WORD_RE.finditer(s)})
    if not kinds:
        return MaturityResult(None, "section_not_maturity")
    if len(kinds) > 1:
        return MaturityResult(None, "section_maturity_ambiguous")
    return MaturityResult(kinds[0], "section_label")


# ------------------------------------------------------------------------------------------------ candidate

def validate_candidate(ctx: CandidateContext, run: RunEvidence) -> CandidateValidation:
    ineligible = eligibility_reasons(ctx, run)
    ops = check_operations(ctx.concept_key, ctx.operations, ctx.operations_basis)
    if ops.trust != "trusted":
        ineligible.append(ops.reason)
    period = validate_period(ctx.period_kind, ctx.start_date, ctx.end_date, ctx.duration_months, ctx.period_class,
                             ctx.fiscal_label, run, ctx.period_derivation)
    ineligible.extend(period.reasons)
    value = normalize_value(ctx.raw_value, ctx.parsed_value, ctx.representation_class, ctx.printed_decimals,
                            ctx.sign_as_printed, ctx.value_type, ctx.reported_scale, ctx.scale_basis,
                            ctx.reported_currency)
    if value.state == "inconsistent_input":
        ineligible.append("value_inconsistent_input")
    norm = tuple(r for r in value.reasons if r != "value_inconsistent_input")
    eligibility = "ineligible" if ineligible else ("normalization_required" if norm else "eligible")
    return CandidateValidation(VALIDATION_VERSION, tuple(ctx.source_key), ctx.candidate_id, ctx.concept_key, eligibility,
                               tuple(ineligible), norm, period, value, ops,
                               borrowing_maturity(ctx.concept_key, ctx.section_label_raw))


# ------------------------------------------------------------------------------------------------ arithmetic

A_CHECKS = (
    # id, name, statement kinds, total concept, terms, form
    ("A1", "assets_equal_liabilities_plus_equity", ("financial_position",), "total_assets",
     ("total_liabilities", "total_equity"), "sum"),
    ("A2", "gross_profit_equals_revenue_pm_cost_of_sales", INCOME_KINDS, "gross_profit", ("revenue", "cost_of_sales"), "pm"),
    ("A3", "profit_equals_pbt_pm_tax", INCOME_KINDS, "profit_for_period", ("profit_before_tax", "income_tax_expense"), "pm"),
    ("A4", "owners_plus_nci_equals_profit", INCOME_KINDS, "profit_for_period",
     ("profit_attributable_to_owners", "profit_attributable_to_nci"), "pair"),
    ("A5", "nii_equals_interest_income_pm_interest_expense", INCOME_KINDS, "net_interest_income",
     ("interest_income", "interest_expense"), "pm"),
)
SIGN_RELATIONS = {"cost_of_sales": "A2", "income_tax_expense": "A3", "interest_expense": "A5"}


def arithmetic_group_key(ctx: CandidateContext):
    """One statement column, continuation pages included: (statement_root, statement_kind, period_kind, end,
    duration_months, reported_scope, role)."""
    months = ctx.duration_months if ctx.period_kind == "duration" and ctx.duration_months is not None else -1
    return (ctx.statement_root, ctx.statement_kind, ctx.period_kind or "", ctx.end_date.isoformat() if ctx.end_date else "",
            months, ctx.reported_scope or "", ctx.role or "")


def _usable(ctx, val: CandidateValidation):
    """A value usable in a document-internal diagnostic: F5 'proposed', normalised, column period, no operations claim.
    Issuer evidence and role trust are NOT required (the checks test the document, not the fact)."""
    return (ctx.candidate_status == "proposed" and val.value.status == "normalized" and ctx.concept_key is not None
            and ctx.period_derivation == "column" and ctx.operations == "total_or_unstated")


def _term(role, ctx, val):
    return Term(role, ctx.concept_key, tuple(ctx.source_key), ctx.raw_value, val.value.normalized_value,
                val.value.half_unit, val.value.currency)


def _group_members(pairs):
    by = {}
    for ctx, val in pairs:
        by.setdefault(ctx.concept_key, []).append((ctx, val))
    return by


def _single(members, concept):
    """(ctx, val) when the concept has exactly one distinct usable value in the group; else (None, reason)."""
    got = members.get(concept, [])
    usable = [(c, v) for c, v in got if _usable(c, v)]
    if not got:
        return None, f"missing:{concept}"
    if not usable:
        return None, f"unusable:{concept}"
    distinct = sorted({v.value.normalized_value for _, v in usable})
    if len(distinct) > 1:
        return None, f"multiple_values:{concept}"
    return sorted(usable, key=lambda cv: cv[0].source_key)[0], None


def _evaluate(check, key, members):
    cid, name, kinds, total, terms, form = check
    if total not in members:
        return ArithmeticResult(cid, name, key, "not_applicable", None, (), None, None, None, None, None, None,
                                (f"total_not_reported:{total}",))
    t, why = _single(members, total)
    if t is None:
        return ArithmeticResult(cid, name, key, "insufficient_evidence", None, (), None, None, None, None, None, None, (why,))
    picked, reasons = [], []
    for concept in terms:
        got, why = _single(members, concept)
        if got is None:
            reasons.append(why)
        else:
            picked.append(got)
    if reasons:
        return ArithmeticResult(cid, name, key, "insufficient_evidence", None, (), None, None, None, None, None, None,
                                tuple(reasons))
    used = [_term("total", *t)] + [_term("term", *p) for p in picked]
    return _decide(cid, name, key, form, used, terms[-1])


def _decide(cid, name, key, form, used, last_concept):
    if len({u.currency for u in used}) > 1:
        return ArithmeticResult(cid, name, key, "insufficient_evidence", None, tuple(used), None, None, None, None, None,
                                None, ("currency_mismatch",))
    total, first, second = used[0].normalized_value, used[1].normalized_value, used[2].normalized_value
    tol = _add(*[u.half_unit for u in used])
    as_printed = _add(first, second)
    d1 = abs(_sub(total, as_printed))
    ok1 = d1 <= tol
    if form in ("sum", "pair"):
        return ArithmeticResult(cid, name, key, "pass" if ok1 else "fail", "as_printed", tuple(used), total, as_printed,
                                None, tol, d1, None, () if ok1 else ("difference_exceeds_printed_precision",))
    reversed_ = _sub(first, second)
    d2 = abs(_sub(total, reversed_))
    ok2 = d2 <= tol
    variant = "either" if ok1 and ok2 else ("as_printed" if ok1 else (f"reversed_{last_concept}" if ok2 else None))
    return ArithmeticResult(cid, name, key, "pass" if (ok1 or ok2) else "fail", variant, tuple(used), total, as_printed,
                            reversed_, tol, d1, d2, () if (ok1 or ok2) else ("difference_exceeds_printed_precision",))


def _evaluate_pairs(check, key, members):
    """A4, per attribution block: each owners row with the first NCI row printed after it in the same statement."""
    cid, name, kinds, total, (owners_c, nci_c), form = check
    owners = sorted([cv for cv in members.get(owners_c, []) if _usable(*cv)], key=lambda cv: cv[0].source_key)
    if not members.get(owners_c):
        return [ArithmeticResult(cid, name, key, "not_applicable", None, (), None, None, None, None, None, None,
                                 (f"total_not_reported:{owners_c}",))]
    if not owners:
        return [ArithmeticResult(cid, name, key, "insufficient_evidence", None, (), None, None, None, None, None, None,
                                 (f"unusable:{owners_c}",))]
    t, why = _single(members, total)
    out = []
    ncis = sorted([cv for cv in members.get(nci_c, []) if _usable(*cv)],
                  key=lambda cv: (cv[0].statement_index, cv[0].row_index))
    for o in owners:
        if t is None:
            out.append(ArithmeticResult(cid, name, key, "insufficient_evidence", None, (), None, None, None, None, None,
                                        None, (why,)))
            continue
        after = [n for n in ncis if (n[0].statement_index, n[0].row_index) > (o[0].statement_index, o[0].row_index)]
        if not after:
            out.append(ArithmeticResult(cid, name, key, "insufficient_evidence", None, (_term("term", *o),), None, None,
                                        None, None, None, None, (f"missing:{nci_c}_after_owners_row",)))
            continue
        out.append(_decide(cid, name, key, "pair", [_term("total", *t), _term("term", *o), _term("term", *after[0])], nci_c))
    return out


def arithmetic_diagnostics(pairs):
    """pairs: [(CandidateContext, CandidateValidation)]. Diagnostics only; nothing is mutated or selected."""
    groups = {}
    for ctx, val in pairs:
        groups.setdefault(arithmetic_group_key(ctx), []).append((ctx, val))
    out = []
    for key in sorted(groups):
        members = _group_members(groups[key])
        kind = key[1]
        for check in A_CHECKS:
            if kind not in check[2]:
                continue
            if check[5] == "pair":
                out.extend(_evaluate_pairs(check, key, members))
            else:
                out.append(_evaluate(check, key, members))
    return tuple(out)


# ------------------------------------------------------------------------------------------------ sign

def detect_signs(pairs, arithmetic):
    """Per usable value: the printed sign, the convention a relation in the SAME statement column establishes, and a
    contribution-to-profit value only when that is safe. Printed / normalised values are never changed."""
    by_group = {}
    for r in arithmetic:
        by_group.setdefault((r.group_key, r.check_id), []).append(r)
    out = []
    for ctx, val in sorted(pairs, key=lambda cv: _order(cv[0].source_key)):
        if not _usable(ctx, val):
            continue
        concept = fc.BY_KEY.get(ctx.concept_key)
        natural = concept.natural_sign if concept else "unknown"
        v = val.value.normalized_value
        printed = val.value.sign_as_printed
        def res(conv, basis, safe, flip, contrib, status, reason=None):
            return SignResult(tuple(ctx.source_key), ctx.concept_key, natural, printed, conv, basis, safe, flip, contrib,
                              status, reason)
        if natural in ("balance", "as_printed", "outflow", "unknown"):
            out.append(res("not_applicable", f"natural_sign_{natural}", False, False, None, "not_applicable"))
            continue
        if natural == "income":
            out.append(res("contribution_as_printed", "income_as_printed", True, False, v, "ok"))
            continue
        if v == 0:                                            # expense printed zero / negative zero
            out.append(res("zero", "zero_value", True, False, Decimal(0), "ok"))
            continue
        rel = SIGN_RELATIONS.get(ctx.concept_key)
        found = [r for r in by_group.get((arithmetic_group_key(ctx), rel), []) if r.outcome in ("pass", "fail")
                 and any(t.source_key == tuple(ctx.source_key) for t in r.terms)] if rel else []
        if found:
            r = found[0]
            if r.outcome == "pass" and r.variant == "as_printed":
                out.append(res("contribution_as_printed", f"{rel}_as_printed", True, False, v, "ok"))
            elif r.outcome == "pass" and r.variant and r.variant.startswith("reversed_"):
                out.append(res("magnitude_printed", f"{rel}_reversed", True, True, -v, "ok"))
            else:
                out.append(res("undetermined", f"{rel}_{r.outcome}", False, False, None, "normalization_required",
                               "sign_convention_unestablished"))
            continue
        if v < 0:
            out.append(res("contribution_as_printed", "printed_negative_expense", True, False, v, "ok"))
        else:
            out.append(res("undetermined", "positive_expense_without_relation", False, False, None,
                           "normalization_required", "sign_convention_unestablished"))
    return tuple(out)


def statement_sign_conventions(signs):
    """Report only: per (statement, concept) the convention its columns established - one, 'mixed' or
    'undetermined'. Never used to flip or to fill in an undetermined column."""
    by = {}
    for s in signs:
        if s.status == "not_applicable" or s.convention == "zero":
            continue
        by.setdefault((s.source_key[0], s.source_key[1], s.concept_key), set()).add(s.convention)
    out = []
    for (run_key, stmt, concept), convs in sorted(by.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])):
        known = convs - {"undetermined"}
        conv = "undetermined" if not known else (next(iter(known)) if len(known) == 1 else "mixed")
        out.append((run_key, stmt, concept, conv))
    return tuple(out)


# ------------------------------------------------------------------------------------------------ run

def _order(source_key):
    return (str(source_key[0]),) + tuple(source_key[1:])


def validate_run(contexts, run: RunEvidence) -> RunValidation:
    """Every candidate of one run (one RunEvidence), then the A1-A5 diagnostics and sign detection over them."""
    ctxs = sorted(contexts, key=lambda c: _order(c.source_key))
    vals = [validate_candidate(c, run) for c in ctxs]
    pairs = list(zip(ctxs, vals))
    arithmetic = arithmetic_diagnostics(pairs)
    signs = detect_signs(pairs, arithmetic)
    return RunValidation(VALIDATION_VERSION, tuple(vals), arithmetic, signs, statement_sign_conventions(signs))


# ------------------------------------------------------------------------------------------------ F5 adapters

def run_evidence(issuer_link_status, publication_date, classification=None):
    """RunEvidence from caller-chosen issuer status + publication date and an F3 classification dict (FYE fields)."""
    cls = classification or {}
    return RunEvidence(issuer_link_status, _date(publication_date), cls.get("fiscal_year_end_basis"),
                       cls.get("fiscal_year_end_status"), cls.get("period_status"))


def contexts_from_f5_result(result, run_key=""):
    """CandidateContexts from an F5 financial_candidates.build() result (in memory, or its JSON form)."""
    statements = {s["statement_index"]: s for s in result["statements"]}
    columns = {(c["statement_index"], c["column_index"]): c for c in result["columns"]}
    rows = {(r["statement_index"], r["row_index"]): r for r in result["rows"]}

    def root(i):
        seen = set()
        while statements.get(i, {}).get("continuation_of") is not None and i not in seen:
            seen.add(i)
            i = statements[i]["continuation_of"]
        return i

    out = []
    for c in result["candidates"]:
        si = c["statement_index"]
        st, col, row = statements[si], columns[(si, c["column_index"])], rows[(si, c["row_index"])]
        column_period = c["period_derivation"] == "column"
        out.append(CandidateContext(
            source_key=(run_key, si, c["row_index"], c["column_index"], c["value_ordinal"], c["concept_key"] or ""),
            concept_key=c["concept_key"], mapping_status=c["mapping_status"], candidate_status=c["candidate_status"],
            value_type=c["value_type"], attribution=c["attribution"], period_kind=c["period_kind"],
            period_class=c["period_class"], period_derivation=c["period_derivation"], raw_value=c["raw_value"],
            parsed_value=_dec(c["parsed_value"]), representation_class=c["representation_class"],
            printed_decimals=c.get("printed_decimals"), sign_as_printed=c["sign_as_printed"],
            reported_scale=c["reported_scale"], scale_basis=c.get("scale_basis"), reported_currency=c["reported_currency"],
            statement_index=si, statement_root=root(si), statement_kind=st["statement_kind"],
            column_index=c["column_index"], role=col["role"], role_trust=col["role_trust"],
            start_date=_date(col["start_date"]) if column_period else None, end_date=_date(col["end_date"]),
            duration_months=col["duration_months"] if column_period else None, fiscal_label=col.get("fiscal_label"),
            reported_scope=col["reported_scope"], canonical_scope=col["canonical_scope"], row_index=c["row_index"],
            label_raw=row["label_raw"], section_label_raw=row.get("section_label_raw"), operations=row["operations"],
            operations_basis=row["operations_basis"], candidate_id=c.get("id"),
            ambiguous_concepts=tuple(c.get("ambiguous_concepts") or ())))
    return tuple(out)


# ------------------------------------------------------------------------------------------------ serialisation

def _plain(o):
    if is_dataclass(o):
        return {f.name: _plain(getattr(o, f.name)) for f in fields(o)}
    if isinstance(o, Decimal):
        return format(o, "f")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, (tuple, list)):
        return [_plain(x) for x in o]
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    return o


def canonical_json(obj):
    """Byte-stable JSON (sorted keys, Decimals as plain strings, dates ISO, ASCII only)."""
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def summarize(run_validation: RunValidation):
    """Counts for reporting (deterministic dict)."""
    def count(items):
        out = {}
        for i in items:
            out[i] = out.get(i, 0) + 1
        return dict(sorted(out.items()))
    cands = run_validation.candidates
    return {
        "version": run_validation.version,
        "candidates": len(cands),
        "eligibility": count(v.eligibility for v in cands),
        "ineligible_reasons": count(r for v in cands for r in v.ineligible_reasons),
        "normalization_reasons": count(r for v in cands if v.eligibility == "normalization_required"
                                       for r in v.normalization_reasons),
        "arithmetic": count(f"{a.check_id}:{a.outcome}" for a in run_validation.arithmetic),
        "signs": count(f"{s.convention}:{s.basis}" for s in run_validation.signs),
    }
