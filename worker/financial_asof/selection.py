"""
As-of selection, f8.selection.1 (docs/F8_DESIGN.md §7.3 to §7.7): the four modes, evaluated purely over immutable
evidence (model.Evidence).

KNOWN_RECORDED (T)  F6.4's stored reconciliation as of T, returned unchanged (F6.2 Q1). For the issuer and the F6
                    configuration designated by T9 at T: the latest T12 batch recorded at or before T, its T16/T13
                    records. F8 only adds flags computed from the evidence observed by T (I-2r).
KNOWN (T)           recomputed at G = T: what the system could have concluded with the evidence it held at T.
AVAILABLE (T, H)    recomputed at G = H, keeping only document versions available by T (as known at H), with known
                    availability. Labelled reconstructed.
CURRENT (H)         recomputed at G = H, with no availability condition. Labelled retrospective_current; never
                    point-in-time.

Recomputation (§7.4):
1. Runs.          The F5 runs known at G whose document version is visible in the mode. The version filter comes
                  BEFORE D-6 (F-2, I-12). F6.3's own select_runs then picks one run per document.
2. Validation.    Each selected run contributes its validation run canonical at G: F6.4's M4 with the issuer
                  decision and the F1 metadata known at G. There is never a fallback to another run.
3. Visibility.    The visible set V: observations whose known_at (f8.knowledge.1) is at or before G. The mode's
                  availability test is repeated for each observation.
4. Context.       F6.3's context (the filings carrying each document, the other currencies it presents), from V only.
5. Supersession.  f8.supersession.1 over V, per fact.
6. Reconciliation. F6.3 reconcile_fact over the rest, under the F6 configuration.

The information set Ω (§7.7, F-1): a result names only rows of Ω. Exclusions are listed only when in-set:
not_yet_available (KNOWN only), metadata_version_tie, superseded_by. A fact is listed only when it has an observation
in V or an in-set exclusion, or when it was requested by its exact ef_key (then `none`, with no identity).

F8 has no "latest filing" rule. The only ordering F8 applies itself is available_at, inside a supersession chain with
a source-declared basis. F6.3's D-6, which is delegated, orders only our own processing runs of the same bytes.
"""
import decimal
from collections import defaultdict
from dataclasses import dataclass

from ..financial_truth import reconciliation
from ..financial_truth_store import F6_DECIMAL_CONTEXT
from . import availability as av
from . import config, knowledge, metadata, supersession
from .errors import EvidenceError, Refused
from .query import AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED, LABELS
from .result import (AMBIGUOUS, AVAILABLE_AFTER_KNOWN, EXCLUSION_REASONS, METADATA_TIE, NONE, NOT_YET_AVAILABLE,
                     SUPERSEDED, AsOfResult, AvailabilityView, Exclusion, FactView, KnowledgeView, ObservationView,
                     RecordedBatch, VersionView, seal)
from .times import iso
from .versions import AVAILABILITY_VERSION, KNOWLEDGE_VERSION


@dataclass(frozen=True)
class Canonical:
    """M4 at G for one F5 run: one validation run, none, or a tie that is never broken."""
    kind: str                       # one | none | tie
    validation_run: object          # ValidationRunRow (kind 'one')
    candidates: tuple               # ValidationRunRow (kind 'tie'): every run matching a value the tie allows


def canonical_validation_run(evidence, run, versions, horizon):
    """F6.4's M4 evaluated at G (§7.3): among the validation runs of `run` under `versions` recorded at or before G,
    the one whose input set is the input set current at G. That input set is:
    - the issuer decision: the highest filing_issuer_links id decided at or before G;
    - the publication instant: F1's merged uploaded_at as known at G.

    Never the current M4 (the view financial_validation_run_current reads the mutable report_filings)."""
    decided = [d.id for d in evidence.decisions_of(run.cse_filing_id) if d.decided_at <= horizon]
    link = max(decided) if decided else None
    meta = metadata.as_of(evidence, run.cse_filing_id, horizon)
    known = [v for v in evidence.validation_runs_of(run.f5_run_id)
             if v.versions == versions and v.recorded_at <= horizon and v.issuer_link_id == link]
    if meta.tie:
        allowed = set(meta.uploaded_at)
        tied = [v for v in known if not allowed or v.publication_uploaded_at in allowed]
        return Canonical("tie", None, tuple(sorted(tied, key=lambda v: v.key)))
    matching = [v for v in known if v.publication_uploaded_at == meta.uploaded_at[0]]
    if len(matching) > 1:                       # impossible under 0015's uq_fvr_input_set; refused, never chosen
        raise EvidenceError(f"F5 run {run.f5_run_id}: {len(matching)} canonical validation runs at {iso(horizon)}")
    return Canonical("one", matching[0], ()) if matching else Canonical("none", None, ())


def _issuer_observations(evidence, validation_run, issuer_id):
    return [s for s in evidence.observations_of(validation_run.key) if s.identity.issuer_id == issuer_id]


def _availability_view(document):
    return AvailabilityView(AVAILABILITY_VERSION, iso(document.at), document.precision,
                            tuple(VersionView(v.cse_filing_id, iso(v.at), v.precision, v.role, v.basis, v.flags,
                                              v.evidence_hash) for v in document.versions),
                            document.flags)


def _observation_view(stored, k, document):
    flags = set(document.flags)
    if document.at is not None and document.at > k.at:
        flags.add(f"{AVAILABLE_AFTER_KNOWN}:{document.precision}")         # A-7; carries its precision (AC-2)
    return ObservationView(stored.so_key, stored.so.output_hash, stored.document_sha256, stored.cse_filing_id,
                           stored.f5_run_id, stored.validation_run_key, _availability_view(document),
                           KnowledgeView(KNOWLEDGE_VERSION, iso(k.at), k.set_by), tuple(sorted(flags)))


def _exclusion(stored, reason, superseded_by=()):
    return Exclusion(stored.so_key, stored.document_sha256, stored.cse_filing_id, stored.f5_run_id,
                     stored.validation_run_key, reason, tuple(superseded_by))


def _fact_view(ef_key, identity, f6_result, visible, excluded, records=(), ambiguities=()):
    counts = defaultdict(int)
    for e in excluded:
        counts[e.reason] += 1
    excluded = tuple(sorted(excluded, key=lambda e: (e.reason, e.document_sha256, e.so_key)))
    r = f6_result
    some = r is not None
    flags = ((AMBIGUOUS,) if ambiguities else ()) + ((METADATA_TIE,) if counts[METADATA_TIE] else ())   # §12
    return FactView(ef_key, identity, r.state if some else NONE, r.value_kind if some else None,
                    r.interval_low if some else None, r.interval_high if some else None,
                    r.representative if some else None, r.representative_normalized_value if some else None,
                    r.representative_half_unit if some else None, r,
                    tuple(sorted(visible, key=lambda o: (o.document_sha256, o.so_key))), excluded, tuple(records),
                    tuple(ambiguities), flags,
                    tuple((reason, counts[reason]) for reason in EXCLUSION_REASONS if counts[reason]))


# ------------------------------------------------------------------------------------------------ recomputed modes

def _held_observations(evidence, runs, versions, horizon, issuer_id, f6):
    """D-6 over `runs`, then M4 at G: [(stored observation, Knowledge, tie?)] with known_at at or before G."""
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        chosen = reconciliation.select_runs([r.ref for r in runs], f6)
    out = []
    for d in chosen.documents:
        canon = canonical_validation_run(evidence, evidence.runs[d.selected_run], versions, horizon)
        vrs = canon.candidates if canon.kind == "tie" else ((canon.validation_run,) if canon.kind == "one" else ())
        for vr in vrs:
            for stored in _issuer_observations(evidence, vr, issuer_id):
                k = knowledge.known_at(evidence, stored)
                if k.at is not None and k.at <= horizon:          # outside Ω otherwise: absent, never an exclusion
                    out.append((stored, k, canon.kind == "tie"))
    return chosen, out


def _recomputed(evidence, q, f6):
    mode, cutoff, horizon, issuer = q.mode, q.information_cutoff, q.governing_horizon, q.issuer_id
    cache = av.Cache(evidence, horizon)
    known_runs = [r for r in evidence.runs.values() if r.recorded_at <= horizon]

    def visible(run):
        a = cache.version(run.cse_filing_id, run.document_sha256)
        if mode == KNOWN:
            return a.at is None or a.at <= cutoff
        if mode == AVAILABLE:
            return a.at is not None and a.at <= cutoff
        return True                                                    # CURRENT: no availability condition

    # step 1: the version filter before D-6 (F-2, I-12)
    shown = [r for r in known_runs if visible(r)]
    filings_of = defaultdict(set)
    for r in shown:
        filings_of[r.document_sha256].add(r.cse_filing_id)
    documents = {sha: cache.document(sha, sorted(fs)) for sha, fs in filings_of.items()}
    # steps 1-3
    selection, held = _held_observations(evidence, shown, f6.versions, horizon, issuer, f6)
    V, excluded = [], []
    for stored, k, tie in held:
        if tie:
            excluded.append(_exclusion(stored, METADATA_TIE))
            continue
        doc = documents[stored.document_sha256]
        if (mode == KNOWN and not (doc.at is None or doc.at <= cutoff)) or \
                (mode == AVAILABLE and not (doc.at is not None and doc.at <= cutoff)):
            raise EvidenceError("a visible document failed its own availability test")      # §7.3, repeated per o
        V.append((stored, k, doc))
    if mode == KNOWN:                                   # held at T, not yet available: in-set exclusions (KNOWN only)
        pending = [r for r in known_runs if not visible(r) and r.document_sha256 not in filings_of]
        for stored, _, _ in _held_observations(evidence, pending, f6.versions, horizon, issuer, f6)[1]:
            excluded.append(_exclusion(stored, NOT_YET_AVAILABLE))
    # step 4: F6.3's context from V only (AC-3, I-10)
    filings_ctx = {d.document_sha256: d.filings for d in selection.documents}
    presented = defaultdict(set)
    for stored, _, _ in V:
        presented[(stored.document_sha256, stored.identity.without_currency())].add(stored.identity.currency)
    by_fact, identities = defaultdict(list), {}
    for item in V:
        by_fact[item[0].ef_key].append(item)
        identities[item[0].ef_key] = item[0].identity
    excluded_by_fact = defaultdict(list)
    for e in excluded:
        stored = evidence.observations[e.so_key]
        excluded_by_fact[stored.ef_key].append(e)
        identities.setdefault(stored.ef_key, stored.identity)

    def document_times(filing, sha):
        runs = [r for r in evidence.runs_of_filing(filing) if r.document_sha256 == sha and r.recorded_at <= horizon]
        return (tuple(r.cdn_last_modified for r in runs if r.cdn_last_modified is not None),
                tuple(r.path_epoch_at for r in runs if r.path_epoch_at is not None))

    facts = {}
    for ef_key in sorted(set(by_fact) | set(excluded_by_fact)):
        items = by_fact.get(ef_key, [])
        # step 5: supersession over V
        sup = supersession.derive([supersession.Candidate(s.so, d.at, tuple(sorted(filings_of[s.document_sha256])))
                                   for s, _, d in items], document_times)
        dropped = dict(sup.superseded_by)
        kept = [(s, k, d) for s, k, d in items if s.so_key not in dropped]
        fact_excluded = excluded_by_fact.get(ef_key, []) + [_exclusion(s, SUPERSEDED, dropped[s.so_key])
                                                            for s, _, _ in items if s.so_key in dropped]
        # step 6: F6.3 reconciliation of the rest
        f6_result = None
        if kept:
            others = {s.document_sha256: tuple(sorted(presented[(s.document_sha256, s.identity.without_currency())]
                                                      - {s.identity.currency})) for s, _, _ in kept}
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                f6_result = reconciliation.reconcile_fact([s.so for s, _, _ in kept], f6, filings=filings_ctx,
                                                          other_currencies=others)
        facts[ef_key] = _fact_view(ef_key, identities[ef_key], f6_result,
                                   [_observation_view(s, k, d) for s, k, d in kept], fact_excluded, sup.records,
                                   sup.ambiguities)
    return facts


# ------------------------------------------------------------------------------------------------ KNOWN_RECORDED

def _recorded(evidence, q, resolved):
    """F6.2 Q1: F6.4's stored record as of T, unchanged. F8 adds only flags from the evidence observed by T."""
    cutoff, issuer = q.information_cutoff, q.issuer_id
    f6_id = resolved.configuration.f6_configuration_id
    designated = config.in_force(evidence.f6_designations, cutoff)
    if designated is None:
        raise Refused("no_f6_designation", "F6.4 had designated no canonical configuration at T: it concluded nothing "
                                           "canonical then")
    if designated.configuration_id != f6_id:
        raise Refused("f6_configuration_mismatch", f"the F8 configuration names F6 configuration {f6_id}, but T9 "
                                                   f"designated {designated.configuration_id} at T")
    batches = [b for b in evidence.batches if b.configuration_id == f6_id and b.issuer_id == issuer
               and b.recorded_at <= cutoff]
    batch = max(batches, key=lambda b: b.sequence) if batches else None
    if batch is None:
        return {}, None
    if batch.records is None:
        raise EvidenceError(f"the records of batch {batch.batch_id} were not loaded")
    cache = av.Cache(evidence, cutoff)
    facts = {}
    for record in batch.records:
        result = record.result
        views = []
        for ref in result.observations:
            stored = evidence.observations.get(ref.so_key)
            if stored is None:
                raise EvidenceError(f"record {record.record_id}: observation {ref.so_key} was not loaded")
            k = knowledge.known_at(evidence, stored)
            if k.at is None or k.at > cutoff:          # I-1 (Appendix B, L7): never returned as known_recorded
                raise EvidenceError(f"batch {batch.batch_id} references observation {ref.so_key}, which was not "
                                    f"known at T: F6.4's lock discipline (L7) does not hold for this evidence")
            filings = sorted({r.cse_filing_id for r in evidence.runs_of_document(stored.document_sha256)
                              if r.recorded_at <= cutoff})                              # A-6 over versions known at T
            views.append(_observation_view(stored, k, cache.document(stored.document_sha256, filings)))
        facts[record.ef_key] = _fact_view(record.ef_key, result.identity, result, views, ())
    return facts, RecordedBatch(batch.batch_id, batch.sequence, iso(batch.recorded_at), batch.output_hash)


# ------------------------------------------------------------------------------------------------ evaluation

def evaluate(evidence, q):
    """The F8 result of query q (query.Query) over evidence (model.Evidence). Pure: no clock, database or network."""
    horizon = q.governing_horizon
    if horizon is None:
        raise Refused("horizon_required", f"{q.mode} needs a knowledge horizon H; the database interface supplies "
                                          f"the query time when it is left out")
    resolved = config.resolve(evidence, q.f8_configuration_id, horizon)
    f6 = evidence.f6_configurations[resolved.configuration.f6_configuration_id].configuration
    if q.mode == KNOWN_RECORDED:
        facts, batch = _recorded(evidence, q, resolved)
    elif q.mode in (KNOWN, AVAILABLE, CURRENT):
        facts, batch = _recomputed(evidence, q, f6), None
    else:
        raise Refused("unknown_mode", q.mode)
    listed = [view for ef_key, view in sorted(facts.items()) if q.facts.matches(ef_key, view.identity)]
    present = {view.ef_key for view in listed}
    for ef_key in q.facts.ef_keys:                      # requested by exact key, nothing in Ω: none, no identity
        if ef_key not in present:
            listed.append(_fact_view(ef_key, None, None, (), ()))
    return seal(AsOfResult(q.mode, LABELS[q.mode], iso(q.information_cutoff), iso(q.knowledge_horizon), q.issuer_id,
                           q.facts, resolved.configuration.f8_configuration_id,
                           resolved.configuration.f6_configuration_id, resolved.configuration.rule_versions, batch,
                           tuple(sorted(listed, key=lambda v: v.ef_key))))
