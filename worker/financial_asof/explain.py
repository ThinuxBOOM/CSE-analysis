"""
"Why did F8 select this?" (docs/F8_DESIGN.md §11, §13.2, §7.7; T-20).

For one result, explain gives:
- the re-proof: the envelope's own hash, and the result recomputed from its own query and pinned configuration;
- the configuration and the designation in force at the governing horizon;
- for every visible observation and every in-set exclusion, its provenance chain down to the CSE evidence:
  - the source observation and its members (the F5 candidates);
  - the validation run and the issuer decision it used;
  - the F5 run with its raw timestamp snapshot, and the F3 classification;
  - the F1 listing entries, verbatim, as observed by the governing horizon;
  - when HB-1 recorded them (Phase 2), the request attempts and the archived response bodies behind those entries,
    and the retrieval record of the document;
- the supersession records and the ambiguity candidates.

**The audit section** is optional (audit=True). It lists rows OUTSIDE the result's information set: rows known after
the horizon, and versions not available by T in AVAILABLE. It is labelled "audit", lies outside result_hash, and is
never a point-in-time input (§7.7, I-11). explain itself is never part of a result.
"""
from .availability import Cache
from .config import in_force
from .knowledge import known_at
from .query import AVAILABLE, KNOWN, KNOWN_RECORDED
from .times import iso, instant


def _so(stored):
    so = stored.so
    return {"so_key": so.so_key, "ef_key": so.ef_key, "output_hash": so.output_hash,
            "recorded_at": iso(stored.recorded_at), "observation_status": so.observation_status,
            "members": [{"candidate_validation_key": m.candidate_validation_key, "candidate_id": m.candidate_id,
                         "source_key": list(m.source_key), "restated": m.restated,
                         "audit_label_reported": m.audit_label_reported} for m in so.members]}


def chain(evidence, so_key, horizon, hb1=None):
    """The provenance chain of one observation, using only rows known at or before the horizon."""
    stored = evidence.observations[so_key]
    run = evidence.runs.get(stored.f5_run_id)
    vr = evidence.validation_runs.get(stored.validation_run_key)
    cls = evidence.classifications.get(run.classification_id) if run is not None else None
    decision = (evidence.issuer_decisions.get(vr.issuer_link_id)
                if vr is not None and vr.issuer_link_id is not None else None)
    f1 = [o for o in evidence.filing_observations_of(stored.cse_filing_id) if o.observed_at <= horizon]
    k = known_at(evidence, stored)
    out = {
        "source_observation": _so(stored),
        "known_at": {"at": iso(k.at), "terms": [[t[0], t[1], t[2], iso(t[3])] for t in k.terms],
                     "missing": list(k.missing)},
        "validation_run": None if vr is None else {
            "validation_run_key": vr.key, "recorded_at": iso(vr.recorded_at), "issuer_link_id": vr.issuer_link_id,
            "publication_uploaded_at": iso(vr.publication_uploaded_at)},
        "issuer_decision": None if decision is None else {
            "id": decision.id, "status": decision.status, "basis": decision.basis, "issuer_id": decision.issuer_id,
            "decided_at": iso(decision.decided_at)},
        "f5_run": None if run is None else {
            "f5_run_id": run.f5_run_id, "cse_filing_id": run.cse_filing_id, "document_sha256": run.document_sha256,
            "recorded_at": iso(run.recorded_at), "snapshot": dict(run.ref.timestamps)},
        "classification": None if cls is None else {
            "id": cls.id, "classified_at": iso(cls.classified_at), "document_type": cls.document_type,
            "document_type_status": cls.document_type_status, "underlying_type": cls.underlying_type},
        "filing_observations": [{"id": o.id, "discovery_run_id": o.discovery_run_id,
                                 "source": [o.source_endpoint, o.source_bucket, o.query_symbol],
                                 "metadata_hash": o.metadata_hash, "observed_at": iso(o.observed_at),
                                 "raw_item": o.raw_item} for o in f1],
    }
    if hb1 is not None:
        out["cse_responses"] = {
            "listing_attempts": [a for o in f1 for a in hb1.get("f1_runs", {}).get(o.discovery_run_id, ())],
            "document_retrievals": list(hb1.get("retrievals", {}).get((stored.cse_filing_id,
                                                                       stored.document_sha256), ()))}
    return out


def _audit(evidence, result, horizon):
    """Rows outside the result's information set, labelled audit (never in result_hash, never a PIT input)."""
    issuer = result.issuer_id
    rows = []
    for stored in sorted(evidence.observations.values(), key=lambda s: s.so_key):
        if stored.identity.issuer_id != issuer:
            continue
        k = known_at(evidence, stored)
        if k.at is None:
            rows.append({"so_key": stored.so_key, "reason": "knowledge_chain_incomplete", "missing": list(k.missing)})
        elif k.at > horizon:
            rows.append({"so_key": stored.so_key, "reason": "known_after_horizon", "known_at": iso(k.at)})
    filings = sorted({s.cse_filing_id for s in evidence.observations.values() if s.identity.issuer_id == issuer})
    for f in filings:
        rows.extend({"f1_observation": o.id, "cse_filing_id": f, "reason": "observed_after_horizon",
                     "observed_at": iso(o.observed_at)}
                    for o in evidence.filing_observations_of(f) if o.observed_at > horizon)
        rows.extend({"f5_run_id": r.f5_run_id, "cse_filing_id": f, "reason": "recorded_after_horizon",
                     "recorded_at": iso(r.recorded_at)}
                    for r in evidence.runs_of_filing(f) if r.recorded_at > horizon)
    if result.mode == AVAILABLE:
        cutoff = instant(result.information_cutoff, "information_cutoff")
        cache = Cache(evidence, horizon)
        for f in filings:
            for r in sorted({(r.cse_filing_id, r.document_sha256) for r in evidence.runs_of_filing(f)
                             if r.recorded_at <= horizon}):
                v = cache.version(*r)
                if v.at is None:
                    rows.append({"version": list(r), "reason": "availability_unknown", "basis": v.basis})
                elif v.at > cutoff:
                    rows.append({"version": list(r), "reason": "not_available_by_cutoff",
                                 "available_at": iso(v.at)})
    return {"label": "audit", "outside_result_hash": True, "point_in_time_input": False, "rows": rows}


def explain(evidence, result, *, recomputed=None, hb1=None, audit=False):
    """The provenance of `result` (an AsOfResult) from `evidence` (the rows the recomputation used)."""
    horizon = instant(result.information_cutoff if result.mode in (KNOWN_RECORDED, KNOWN)
                      else result.knowledge_horizon, "governing horizon")
    designation = in_force(evidence.f8_designations, horizon)
    out = {
        "result_hash": result.result_hash,
        "envelope_reproves": result.verify(),
        "recomputed_hash": None if recomputed is None else recomputed.result_hash,
        "reproved": recomputed is not None and recomputed.result_hash == result.result_hash and result.verify(),
        "mode": result.mode, "label": result.label, "information_cutoff": result.information_cutoff,
        "knowledge_horizon": result.knowledge_horizon,
        "configuration": {"f8_configuration_id": result.f8_configuration_id,
                          "f6_configuration_id": result.f6_configuration_id,
                          "rule_versions": vars(result.rule_versions).copy(),
                          "designated_at_horizon": None if designation is None else {
                              "id": designation.id, "f8_configuration_id": designation.configuration_id,
                              "recorded_at": iso(designation.recorded_at)}},
        "recorded_batch": None if result.recorded_batch is None else vars(result.recorded_batch).copy(),
        "facts": [],
    }
    for fact in result.facts:
        out["facts"].append({
            "ef_key": fact.ef_key, "state": fact.state, "flags": list(fact.flags),
            "visible": [chain(evidence, o.so_key, horizon, hb1) for o in fact.visible],
            "excluded": [dict(chain(evidence, e.so_key, horizon, hb1), reason=e.reason,
                              superseded_by=list(e.superseded_by)) for e in fact.excluded],
            "supersession": [{"superseding": r.superseding, "superseded": r.superseded, "bases": list(r.bases),
                              "rule_version": r.rule_version, "evidence": [list(e) for e in r.evidence]}
                             for r in fact.supersession],
            "ambiguities": [{"kind": a.kind, "a": a.a, "b": a.b, "detail": list(a.detail)} for a in fact.ambiguities],
        })
    if audit:
        out["audit"] = _audit(evidence, result, horizon)
    return out
