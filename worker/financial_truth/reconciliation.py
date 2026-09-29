"""
f6.reconciliation.1 (docs/F6.2_DESIGN.md §7, §8, §13; D-4, D-5, D-6): the state and value F6 concludes for ONE economic
fact from an explicit set of source observations.

There is no precedence of any kind: not latest, audited, annual, amended, restated, errata or document type. Precision
only picks a REPRESENTATIVE among values that already agree, and never adjusts a value.

Configuration (explicit and versioned; configuration_id = SHA-256 of its content)
  - exactly one identity, validation, admission, OP1 and input-policy version (the implemented set);
  - the accepted F3, F4 and F5 versions;
  - ONE F5 run per document, by document SHA-256: the most recently recorded accepted run (D-6). This chooses between
    our own processing runs of the SAME document; it never chooses between documents. Filings that share a document
    SHA-256 are one document (annotated same_document_multiple_filings).

States (plus value_kind numeric | nil whenever not conflicting)
  single_source  one document, and its SO is consistent
  corroborated   two or more documents, every SO consistent and every pair of observed values agreeing (F6.1 V8,
                 inclusive; nil agrees only with nil)
  conflicting    an SO internally conflicting, a pair disagreeing, or nil against numeric (a printed 0 is numeric): no
                 value, no interval, no representative; every SO is kept with its value

Value (numeric): the interval [max(v - h), min(v + h)] over every observed value, i.e. the intersection of the SOs'
own intervals, never empty because every pair agrees. Agreement is tested on every pair of observed values across SOs;
for single-member SOs that is exactly V8 on the SOs. Representative: the observed value with the smallest half-unit,
exactly as printed. A tie at that half-unit with equal values gives that value (the first SO in canonical order is
cited); with different values there is none (representative_ambiguous). The representative is never moved into the
interval. Nil: the value nil, no interval, never 0.

Annotations never change the state.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from .. import financial_validation as fv
from .canonical import digest
from .identity import EconomicFactIdentity
from .inputs import F5RunRef, InputError, instant
from .observations import ReportedValue, compare_members, finest
from .observations import build as build_observations
from .versions import IMPLEMENTED, RECONCILIATION_VERSION, VersionSet

STATES = ("single_source", "corroborated", "conflicting")
VALUE_KINDS = ("numeric", "nil")
ANNOTATIONS = ("agreement_within_precision_only", "differs_across_document_versions", "differs_interim_vs_annual",
               "restated_comparative_present", "nil_vs_numeric", "internal_conflict", "sign_only_difference",
               "multi_currency_presentation", "representative_ambiguous", "same_document_multiple_filings",
               "audit_labels_differ")
VERSION_DOCUMENT_TYPES = ("errata_or_reissue", "amendment")          # F3 document types of a later document version
INTERIM_TYPES = ("interim_financial_statements",)                    # F3 underlying types
ANNUAL_TYPES = ("annual_report", "audited_financial_statements")
NO_AUDIT_LABEL = (None, "unknown")                                   # absence of a label, not a label


class ConfigurationError(ValueError):
    pass


class ReconciliationInputError(InputError):
    pass


def _none_first(values):
    return tuple((v is not None, "" if v is None else v) for v in values)


@dataclass(frozen=True)
class ReconciliationConfiguration:
    accepted_f3: tuple                   # (classifier_version, text_extractor) pairs
    accepted_f4: tuple                   # (word_extractor, f4_extractor_version) pairs
    accepted_f5: tuple                   # (builder_version, mapper_version, vocabulary_version) triples
    versions: VersionSet = IMPLEMENTED
    reconciliation_version: str = RECONCILIATION_VERSION

    def __post_init__(self):
        if self.reconciliation_version != RECONCILIATION_VERSION:
            raise ConfigurationError(f"this code implements {RECONCILIATION_VERSION} only")
        if self.versions != IMPLEMENTED:
            raise ConfigurationError(f"this code implements the F6 version set {IMPLEMENTED} only")
        for name, width in (("accepted_f3", 2), ("accepted_f4", 2), ("accepted_f5", 3)):
            items = {tuple(v) for v in getattr(self, name)}
            if not items or any(len(v) != width for v in items):
                raise ConfigurationError(f"{name} must list at least one {width}-tuple")
            object.__setattr__(self, name, tuple(sorted(items, key=_none_first)))

    @property
    def configuration_id(self):
        return digest(self)

    def refusal(self, run):
        """None when the configuration accepts the F5 run's versions, else the reason."""
        if run.f3_version not in self.accepted_f3:
            return "f3_version_not_accepted"
        if run.f4_version not in self.accepted_f4:
            return "f4_version_not_accepted"
        if run.f5_version not in self.accepted_f5:
            return "f5_version_not_accepted"
        return None


# ------------------------------------------------------------------------------------------------ one run per document

@dataclass(frozen=True)
class RunSelection:
    document_sha256: str
    selected_run: str                    # f5_run_id of the most recently recorded accepted run
    selected_filing: int
    accepted_runs: tuple                 # (f5_run_id, cse_filing_id, recorded_at) of every accepted run, by run id
    filings: tuple                       # distinct cse_filing_ids through which the configuration knows the document


@dataclass(frozen=True)
class Selection:
    configuration_id: str
    documents: tuple                     # RunSelection, by document SHA-256
    excluded_runs: tuple                 # (f5_run_id, reason) for runs whose versions the configuration refuses


def select_runs(runs, configuration):
    """D-6: one F5 run per document. The runs of one document are OUR processing runs of the same bytes; the most
    recently recorded accepted one is used (ties on recorded_at: the greater run id). Documents are never ranked."""
    unique = {}
    for r in runs:
        if not isinstance(r, F5RunRef):
            raise ReconciliationInputError("runs must be F5RunRef")
        if unique.get(r.f5_run_id, r) != r:
            raise ReconciliationInputError(f"two different F5 runs share the id {r.f5_run_id}")
        unique[r.f5_run_id] = r
    by_doc, excluded = defaultdict(list), []
    for r in sorted(unique.values(), key=lambda r: r.f5_run_id):
        why = configuration.refusal(r)
        if why is None:
            by_doc[r.document_sha256].append(r)
        else:
            excluded.append((r.f5_run_id, why))
    documents = []
    for sha in sorted(by_doc):
        accepted = by_doc[sha]
        if len(accepted) == 1:
            chosen = accepted[0]
        elif any(r.recorded_at is None for r in accepted):
            raise ReconciliationInputError(f"document {sha}: several accepted F5 runs, not all with recorded_at")
        else:
            chosen = max(accepted, key=lambda r: (instant(r.recorded_at, "recorded_at"), r.f5_run_id))
        documents.append(RunSelection(sha, chosen.f5_run_id, chosen.cse_filing_id,
                                      tuple((r.f5_run_id, r.cse_filing_id, r.recorded_at) for r in accepted),
                                      tuple(sorted({r.cse_filing_id for r in accepted}))))
    return Selection(configuration.configuration_id, tuple(documents), tuple(excluded))


# ------------------------------------------------------------------------------------------------ one fact

@dataclass(frozen=True)
class ObservationRef:
    """One SO as a reconciliation input (reconciliation_input), with its value exactly as the SO records it."""
    so_key: str
    so_output_hash: str
    document_sha256: str
    cse_filing_id: int
    f5_run_id: str
    role_in_outcome: str                 # representative | supporting | conflicting
    observation_status: str
    value_kind: Optional[str]
    reported: Optional[ReportedValue]
    normalized_value: Optional[Decimal]
    half_unit: Optional[Decimal]
    interval_low: Optional[Decimal]
    interval_high: Optional[Decimal]
    roles: tuple
    document_type: Optional[str]
    underlying_type: Optional[str]


@dataclass(frozen=True)
class PairComparison:
    a_so: str
    b_so: str
    a_member: tuple
    b_member: tuple
    outcome: str                         # agree | disagree
    reason: Optional[str]                # nil_vs_numeric
    comparison: Optional[fv.ComparisonResult]
    sign_only: bool


@dataclass(frozen=True)
class ReconciliationResult:
    reconciliation_version: str
    configuration_id: str
    ef_key: str
    identity: EconomicFactIdentity
    state: str                           # single_source | corroborated | conflicting
    value_kind: Optional[str]            # numeric | nil (None when conflicting)
    interval_low: Optional[Decimal]
    interval_high: Optional[Decimal]
    representative_so: Optional[str]
    representative_member: Optional[tuple]
    representative: Optional[ReportedValue]      # verbatim copy: the value exactly as printed
    representative_normalized_value: Optional[Decimal]
    representative_half_unit: Optional[Decimal]
    document_count: int
    so_count: int
    observations: tuple                  # ObservationRef, in canonical order (document SHA-256, so_key)
    comparisons: tuple                   # PairComparison for every pair of observed values across SOs
    annotations: tuple                   # sorted, from ANNOTATIONS
    reasons: tuple                       # why conflicting
    context: tuple                       # cross-fact inputs: filings and other presentation currencies per document
    input_hash: str
    output_hash: str


def _pair(a, b, ma, mb):
    c = compare_members(ma, mb)
    return PairComparison(a.so_key, b.so_key, ma.source_key, mb.source_key, c.outcome, c.reason, c.comparison,
                          c.sign_only)


def _interim_vs_annual(type_a, type_b):
    """F3 underlying types: one interim, the other annual / audited (never inferred from periods or titles)."""
    return (type_a in INTERIM_TYPES and type_b in ANNUAL_TYPES) or (type_b in INTERIM_TYPES and type_a in ANNUAL_TYPES)


def reconcile_fact(observations, configuration, *, filings=None, other_currencies=None):
    """Reconcile ONE economic fact from the source observations the configuration selected (one per document).

    filings: {document_sha256: cse_filing_ids of the configuration's accepted runs}; other_currencies:
    {document_sha256: other currencies in which that document presents this identity}. Both default to empty."""
    sos = sorted(observations, key=lambda o: (o.document_sha256, o.so_key))
    if not sos:
        raise ReconciliationInputError("a fact needs at least one source observation")
    if len({o.ef_key for o in sos}) != 1:
        raise ReconciliationInputError("source observations of different facts")
    if any(o.versions != configuration.versions for o in sos):
        raise ReconciliationInputError("a source observation was produced under versions the configuration excludes")
    docs = [o.document_sha256 for o in sos]
    if len(set(docs)) != len(docs):
        raise ReconciliationInputError("more than one source observation per document: select one F5 run per document")
    filings, other_currencies = filings or {}, other_currencies or {}
    context = (("filings", tuple((d, tuple(sorted(filings.get(d, ())))) for d in docs)),
               ("other_currencies", tuple((d, tuple(sorted(other_currencies.get(d, ())))) for d in docs)))
    comparisons = tuple(_pair(a, b, ma, mb) for i, a in enumerate(sos) for b in sos[i + 1:]
                        for ma in a.members for mb in b.members)
    internal = [o for o in sos if o.observation_status == "internally_conflicting"]
    disagreeing = [p for p in comparisons if p.outcome == "disagree"]
    members = [(o, m) for o in sos for m in o.members]
    annotations, reasons = set(), ()
    value_kind = low = high = rep_so = rep_member = None
    if internal or disagreeing:
        state = "conflicting"
        reasons = tuple(f"internal_conflict:{o.so_key}" for o in internal) + \
            tuple(sorted({f"{p.reason or 'values_disagree'}:{p.a_so}:{p.b_so}" for p in disagreeing}))
        by_key = {o.so_key: o for o in sos}
        for p in disagreeing:
            a, b = by_key[p.a_so].document, by_key[p.b_so].document
            if a.document_type in VERSION_DOCUMENT_TYPES or b.document_type in VERSION_DOCUMENT_TYPES:
                annotations.add("differs_across_document_versions")
            if _interim_vs_annual(a.underlying_type, b.underlying_type):
                annotations.add("differs_interim_vs_annual")
    else:
        state = "single_source" if len(sos) == 1 else "corroborated"
        value_kind = sos[0].value_kind           # every SO consistent and every pair agreeing: one kind
        if value_kind == "numeric":
            low, high = max(o.interval_low for o in sos), min(o.interval_high for o in sos)
            if low > high:
                raise RuntimeError("an empty interval despite pairwise agreement")      # impossible (1-D Helly)
            chosen, ambiguous = finest([(m.value.half_unit, m.value.normalized_value, (o, m)) for o, m in members])
            if ambiguous:
                annotations.add("representative_ambiguous")
            else:
                rep_so, rep_member = chosen
            if len({m.value.normalized_value for _, m in members}) > 1:
                annotations.add("agreement_within_precision_only")
        else:                                    # nil: every value is nil; the first SO in canonical order is cited
            rep_so, rep_member = sos[0], sos[0].members[0]
    if internal:
        annotations.add("internal_conflict")
    if {m.value_kind for _, m in members} == {"nil", "numeric"}:
        annotations.add("nil_vs_numeric")
    if any(m.restated for _, m in members):
        annotations.add("restated_comparative_present")
    if any(p.sign_only for p in comparisons) or any(c.sign_only for o in sos for c in o.comparisons):
        annotations.add("sign_only_difference")
    if any(other_currencies.get(d) for d in docs):
        annotations.add("multi_currency_presentation")
    if any(len(filings.get(d, ())) > 1 for d in docs):
        annotations.add("same_document_multiple_filings")
    if len({m.audit_label_reported for _, m in members} - set(NO_AUDIT_LABEL)) > 1:
        annotations.add("audit_labels_differ")

    def role(o):
        if state == "conflicting":
            return "conflicting"
        return "representative" if rep_so is not None and o.so_key == rep_so.so_key else "supporting"
    refs = tuple(ObservationRef(o.so_key, o.output_hash, o.document_sha256, o.cse_filing_id, o.f5_run.f5_run_id,
                                role(o), o.observation_status, o.value_kind, o.reported, o.normalized_value,
                                o.half_unit, o.interval_low, o.interval_high, o.roles, o.document.document_type,
                                o.document.underlying_type) for o in sos)
    ef_key = sos[0].ef_key
    input_hash = digest({"reconciliation_version": RECONCILIATION_VERSION,
                         "configuration_id": configuration.configuration_id, "ef_key": ef_key,
                         "observations": [[o.so_key, o.output_hash] for o in sos], "context": context})
    numeric_rep = rep_member is not None and value_kind == "numeric"
    result = ReconciliationResult(
        RECONCILIATION_VERSION, configuration.configuration_id, ef_key, sos[0].identity, state, value_kind, low, high,
        rep_so.so_key if rep_so is not None else None, rep_member.source_key if rep_member is not None else None,
        rep_member.reported if rep_member is not None else None,
        rep_member.value.normalized_value if numeric_rep else None, rep_member.value.half_unit if numeric_rep else None,
        len(set(docs)), len(sos), refs, comparisons, tuple(a for a in ANNOTATIONS if a in annotations), reasons,
        context, input_hash, "")
    return replace(result, output_hash=digest(result))


# ------------------------------------------------------------------------------------------------ every fact

@dataclass(frozen=True)
class ReconciliationBatch:
    configuration: ReconciliationConfiguration
    configuration_id: str
    selection: Selection
    excluded_observations: tuple         # (so_key, reason): versions_not_configured | f5_run_not_selected
    results: tuple                       # ReconciliationResult, by ef_key
    output_hash: str

    @property
    def facts(self):
        """The economic-fact identities (no values), by ef_key."""
        return tuple(r.identity for r in self.results)


def reconcile(runs, observations, configuration):
    """Reconcile every economic fact the observations support.

    runs: EVERY F5 run of the documents concerned - including runs that produced no admitted observation, so that
    selecting a newer run can never fall back to an older run's observations; observations: SourceObservations of
    those runs. At most one validation run per selected F5 run may contribute (F6.3 refuses rather than chooses)."""
    runs = tuple(runs)
    selection = select_runs(runs, configuration)
    selected = {d.selected_run for d in selection.documents}
    known = {r.f5_run_id: r for r in runs}
    kept, excluded, seen = [], [], {}
    for o in sorted(observations, key=lambda o: o.so_key):
        if o.so_key in seen:
            if seen[o.so_key] != o.output_hash:
                raise ReconciliationInputError(f"two different source observations share the key {o.so_key}")
            continue
        seen[o.so_key] = o.output_hash
        if known.get(o.f5_run.f5_run_id) != o.f5_run:
            raise ReconciliationInputError(f"observation {o.so_key}: its F5 run was not supplied (or differs)")
        if o.versions != configuration.versions:
            excluded.append((o.so_key, "versions_not_configured"))
        elif o.f5_run.f5_run_id not in selected:
            excluded.append((o.so_key, "f5_run_not_selected"))
        else:
            kept.append(o)
    per_run = defaultdict(set)
    for o in kept:
        per_run[o.f5_run.f5_run_id].add(o.validation_run_key)
    for run_id, keys in sorted(per_run.items()):
        if len(keys) > 1:
            raise ReconciliationInputError(f"F5 run {run_id}: {len(keys)} validation runs under the configured "
                                           f"versions; supply exactly one")
    filings = {d.document_sha256: d.filings for d in selection.documents}
    presented = defaultdict(set)
    for o in kept:
        presented[(o.document_sha256, o.identity.without_currency())].add(o.identity.currency)
    by_ef = defaultdict(list)
    for o in kept:
        by_ef[o.ef_key].append(o)
    results = []
    for ef_key in sorted(by_ef):
        sos = by_ef[ef_key]
        others = {o.document_sha256: tuple(sorted(presented[(o.document_sha256, o.identity.without_currency())]
                                                  - {o.identity.currency})) for o in sos}
        results.append(reconcile_fact(sos, configuration, filings=filings, other_currencies=others))
    batch = ReconciliationBatch(configuration, configuration.configuration_id, selection, tuple(excluded),
                                tuple(results), "")
    return replace(batch, output_hash=digest({"configuration_id": batch.configuration_id, "selection": selection,
                                              "excluded_observations": batch.excluded_observations,
                                              "results": [[r.ef_key, r.output_hash] for r in results]}))


def reconcile_validation_runs(validation_runs, configuration):
    """Convenience: the runs and observations of these validation runs, reconciled."""
    vrs = tuple(validation_runs)
    return reconcile([vr.f5_run for vr in vrs], [o for vr in vrs for o in build_observations(vr)], configuration)


def summarize(batch):
    """Deterministic counts over a reconciliation batch (for reports)."""
    def ordered(counter):
        return dict(sorted(counter.items()))
    results = batch.results
    return {
        "facts": len(results),
        "states": ordered(Counter(f"{r.state}:{r.value_kind}" if r.value_kind else r.state for r in results)),
        "conflicting": ordered(Counter(                     # an internally conflicting SO, else documents disagree
            "internal_conflict" if "internal_conflict" in r.annotations else "across_documents"
            for r in results if r.state == "conflicting")),
        "annotations": ordered(Counter(a for r in results for a in r.annotations)),
        "documents_per_fact": ordered(Counter(r.document_count for r in results)),
        "facts_by_scope": ordered(Counter(r.identity.scope for r in results)),
        "facts_by_currency": ordered(Counter(r.identity.currency for r in results)),
        "representative_outside_interval": sum(
            1 for r in results if r.representative_normalized_value is not None
            and not (r.interval_low <= r.representative_normalized_value <= r.interval_high)),
        "observations": sum(r.so_count for r in results),
        "excluded_observations": ordered(Counter(why for _, why in batch.excluded_observations)),
    }
