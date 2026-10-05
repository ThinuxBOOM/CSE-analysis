"""
f8.knowledge.1 (docs/F8_DESIGN.md §4.2, §4.4; OD-3, owner-approved 2026-10-05): when the system knew a source
observation o. An observation is known only when every link of its chain was known:

    known_at(o) = max( observed_at  of the first F1 observation of o's filing,
                       classified_at of o's F3 classification (through its F5 run),
                       decided_at    of the issuer decision its validation run used,
                       recorded_at   of its F5 run, of its validation run (T1) and of o itself (T5) )

Every term is an immutable recorded time (clock C) of an append-only row. Nothing else ever enters: no commit
timestamp, knowledge watermark, job time, wall clock or query time (T-32).

recorded_at is the start of the writing transaction, and the row became visible at its commit (clock D, not measured).
That gap is OD-3's documented, bounded skew. It is accepted, not corrected (§4.4).

A chain with a missing link has no known_at. Its observation is outside every recomputed information set: it is never
treated as known.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

TERMS = (("f1_first_observation", "report_filing_observations"),
         ("classification", "report_document_classifications"),
         ("issuer_decision", "filing_issuer_links"),
         ("f5_run", "financial_extraction_runs"),
         ("validation_run", "financial_validation_runs"),
         ("source_observation", "financial_source_observations"))


@dataclass(frozen=True)
class Knowledge:
    at: Optional[datetime]      # None: a link of the chain is missing
    terms: tuple                # (term, table, key, time) for every present link, in TERMS order
    set_by: tuple               # (term, table, key) of every link whose time is the maximum
    missing: tuple              # the terms whose row is absent


def known_at(evidence, stored):
    """f8.knowledge.1 for one stored source observation (model.StoredObservation)."""
    found = {}
    first = min(evidence.filing_observations_of(stored.cse_filing_id), key=lambda o: (o.observed_at, o.id),
                default=None)
    if first is not None:
        found["f1_first_observation"] = (first.id, first.observed_at)
    run = evidence.runs.get(stored.f5_run_id)
    if run is not None:
        found["f5_run"] = (run.f5_run_id, run.recorded_at)
        classification = evidence.classifications.get(run.classification_id)
        if classification is not None:
            found["classification"] = (classification.id, classification.classified_at)
    vr = evidence.validation_runs.get(stored.validation_run_key)
    if vr is not None:
        found["validation_run"] = (vr.key, vr.recorded_at)
        decision = evidence.issuer_decisions.get(vr.issuer_link_id) if vr.issuer_link_id is not None else None
        if decision is not None:
            found["issuer_decision"] = (str(decision.id), decision.decided_at)
    found["source_observation"] = (stored.so_key, stored.recorded_at)
    needed = [term for term, _ in TERMS
              if term != "issuer_decision" or (vr is not None and vr.issuer_link_id is not None)]
    missing = tuple(term for term in needed if term not in found)
    terms = tuple((term, table, found[term][0], found[term][1]) for term, table in TERMS if term in found)
    if missing:
        return Knowledge(None, terms, (), missing)
    at = max(t[3] for t in terms)
    return Knowledge(at, terms, tuple((t[0], t[1], t[2]) for t in terms if t[3] == at), ())
