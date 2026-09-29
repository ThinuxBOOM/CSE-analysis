"""
f6.op1.partition.1: the controlled operations-partition rule (docs/F6.2_DESIGN.md §3 OP1, §8.5, D-3).

A section heading such as "Discontinued operations" is weak evidence of a row's operations. OP1 is the only way a
section-derived operations value is admitted. Within ONE statement column (F6.1's arithmetic_group_key: statement
root, kind, period kind, end, duration, reported scope, role) and for ONE concept, the continuing and discontinued
values must add up to the total_or_unstated value within printed precision:

    |continuing + discontinued - total| <= half_unit(continuing) + half_unit(discontinued) + half_unit(total)

The test is inclusive and uses exact Decimal arithmetic. Terms are F5 'proposed' candidates with a column-derived
period. Each term needs at least one row; every row must be F6.1 numeric and normalised; the term must carry exactly
one distinct normalised value (printed at several precisions, the finest half-unit counts); all rows share one
currency and value type. Otherwise the outcome is insufficient_evidence.

  pass                   validates the section-derived continuing / discontinued rows of THIS group only
  fail                   the partition does not add up
  insufficient_evidence  a term is missing, nil, non-numeric, not normalised or has several values; or currencies or
                         value types differ

OP1 never relabels a row, never treats nil as zero, never creates a total, never derives a quarter or period, and
never borrows a value from another column, concept or document.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .. import financial_validation as fv
from . import arith
from .canonical import digest
from .versions import OP1_VERSION

TERMS = ("continuing", "discontinued", "total_or_unstated")
NIL_STATES = ("reported_nil", "reported_nil_word")
OUTCOMES = ("pass", "fail", "insufficient_evidence")


@dataclass(frozen=True)
class TermRow:
    source_key: tuple
    operations_basis: str
    raw_value: str
    value_state: str
    value_status: str
    normalized_value: Optional[Decimal]
    half_unit: Optional[Decimal]
    currency: Optional[str]
    value_type: Optional[str]


@dataclass(frozen=True)
class Term:
    operations: str                      # continuing | discontinued | total_or_unstated
    rows: tuple                          # TermRow, by source key
    status: str                          # usable | missing | nil | non_numeric | not_normalized | multiple_values
    value: Optional[Decimal]             # the one distinct normalised value (usable only)
    half_unit: Optional[Decimal]         # the finest half-unit printed for it (usable only)


@dataclass(frozen=True)
class OP1Record:
    version: str
    key: str                             # stable id of this record: SHA-256 of (version, group, concept)
    group_key: tuple                     # F6.1 arithmetic_group_key
    concept_key: str
    terms: tuple                         # Term for continuing, discontinued, total_or_unstated
    currency: Optional[str]
    value_type: Optional[str]
    computed: Optional[Decimal]          # continuing + discontinued
    total: Optional[Decimal]
    tolerance: Optional[Decimal]         # sum of the three half-units
    difference: Optional[Decimal]        # |computed - total|
    outcome: str                         # pass | fail | insufficient_evidence
    reasons: tuple
    validated: tuple                     # source keys of the section-derived rows a pass validates


def _order(source_key):
    return (str(source_key[0]),) + tuple(source_key[1:])


def _row(ctx, val):
    v = val.value
    return TermRow(tuple(ctx.source_key), ctx.operations_basis, ctx.raw_value, v.state, v.status, v.normalized_value,
                   v.half_unit, v.currency, v.value_type)


def _term(operations, pairs):
    rows = tuple(sorted((_row(c, v) for c, v in pairs), key=lambda r: _order(r.source_key)))
    if not rows:
        return Term(operations, rows, "missing", None, None)
    if any(r.value_state in NIL_STATES for r in rows):              # nil is never zero (D-3)
        return Term(operations, rows, "nil", None, None)
    if any(r.value_state != "numeric" for r in rows):
        return Term(operations, rows, "non_numeric", None, None)
    if any(r.value_status != "normalized" for r in rows):
        return Term(operations, rows, "not_normalized", None, None)
    values = {r.normalized_value for r in rows}
    if len(values) > 1:
        return Term(operations, rows, "multiple_values", None, None)
    return Term(operations, rows, "usable", next(iter(values)), min(r.half_unit for r in rows if r.half_unit is not None))


def _record(group_key, concept_key, members):
    by_ops = defaultdict(list)
    for ctx, val in members:
        by_ops[ctx.operations].append((ctx, val))
    terms = tuple(_term(ops, by_ops.get(ops, ())) for ops in TERMS)
    reasons = [f"{t.status}:{t.operations}" for t in terms if t.status != "usable"]
    rows = [r for t in terms for r in t.rows]
    currencies = sorted({r.currency for r in rows if r.currency is not None})
    value_types = sorted({r.value_type for r in rows if r.value_type is not None})
    if not reasons:
        if len(currencies) != 1 or any(r.currency is None for r in rows):
            reasons.append("currency_mismatch")
        if len(value_types) != 1 or any(r.value_type is None for r in rows):
            reasons.append("value_type_mismatch")
    key = digest(["op1", OP1_VERSION, list(group_key), concept_key])
    currency = currencies[0] if len(currencies) == 1 else None
    value_type = value_types[0] if len(value_types) == 1 else None
    if reasons:
        return OP1Record(OP1_VERSION, key, group_key, concept_key, terms, currency, value_type, None, None, None, None,
                         "insufficient_evidence", tuple(reasons), ())
    c, d, t = terms
    tolerance = arith.add(c.half_unit, d.half_unit, t.half_unit)
    computed = arith.add(c.value, d.value)
    difference = arith.abs_diff(computed, t.value)
    passed = difference <= tolerance
    validated = tuple(sorted((r.source_key for term in (c, d) for r in term.rows if r.operations_basis == "section_label"),
                             key=_order)) if passed else ()
    return OP1Record(OP1_VERSION, key, group_key, concept_key, terms, currency, value_type, computed, t.value, tolerance,
                     difference, "pass" if passed else "fail",
                     () if passed else ("difference_exceeds_printed_precision",), validated)


def evaluate(pairs):
    """pairs: [(CandidateContext, CandidateValidation)] of ONE F5 run. One OP1Record per (statement column, concept)
    that holds a section-derived candidate, in (group, concept) order."""
    wanted = {(fv.arithmetic_group_key(c), c.concept_key) for c, _ in pairs
              if c.operations_basis == "section_label" and c.concept_key is not None}
    members = defaultdict(list)
    for ctx, val in pairs:
        if ctx.candidate_status == "proposed" and ctx.period_derivation == "column" and ctx.concept_key is not None:
            members[(fv.arithmetic_group_key(ctx), ctx.concept_key)].append((ctx, val))
    return tuple(_record(gk, concept, members.get((gk, concept), ())) for gk, concept in sorted(wanted))


def operations_decision(ctx, records):
    """A-3 for one candidate: (route, op1 record key, refusal reason, lift).

    row_label / none: F6.1's operations result stands (route = the basis). section_label: validated only when this
    row is a continuing / discontinued term of a passing OP1 record; a pass lifts F6.1's
    operations_section_derived_on_total_row. Any other section-derived row is refused with the outcome of its group
    (a row that was not itself a term of a passing partition is insufficient_evidence)."""
    if ctx.operations_basis != "section_label":
        return ctx.operations_basis, None, None, False
    rec = records.get((fv.arithmetic_group_key(ctx), ctx.concept_key)) if ctx.concept_key is not None else None
    if rec is not None and rec.outcome == "pass" and tuple(ctx.source_key) in rec.validated:
        return "section_label+op1", rec.key, None, True
    outcome = rec.outcome if rec is not None and rec.outcome != "pass" else "insufficient_evidence"
    return None, rec.key if rec is not None else None, f"operations_section_derived_unvalidated:{outcome}", False
