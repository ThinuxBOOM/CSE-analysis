"""
The serialization boundary (docs/F6.4_DESIGN.md sections 11.3, 11.6, 17.5).

Out: F6.3 objects -> envelopes E1-E6 (F6.3's own canonical_json; no other JSON format exists) and their relational
decomposition (typed columns, ordinals, element copies). In: E3 / E4 / E5 -> F6.3 dataclasses, checked by the
round-trip invariant canonical_json(decode(e)) == e.

check_* are the Python mirror of the database's section 11.6 invariant (EDI-1 to EDI-5): the writer asserts them
before any INSERT (early, readable failures), `verify` re-runs them on stored rows. The database checks remain the
authority. Every comparison is on canonical JSON values with the exact rules of section 11.6.3.
"""
import dataclasses
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .. import financial_validation as fv
from ..financial_truth import admission, canonical
from ..financial_truth import identity as identity_mod
from ..financial_truth import inputs, observations, reconciliation, versions

cj = canonical.canonical_json


class CodecError(ValueError):
    """An envelope or a decomposition that is not exactly the F6.3 object: never written."""


# ------------------------------------------------------------------------------------------------ envelopes

def e1(vr):
    return cj({"candidates": [[r.key, r.output_hash] for r in vr.candidates], "arithmetic": vr.arithmetic,
               "signs": vr.signs, "statement_sign_conventions": vr.statement_sign_conventions, "op1": vr.op1})


def e2(r):
    return cj({"validation": r.validation, "admission": r.admission})


def e3(so):
    return cj(dataclasses.replace(so, output_hash=""))


def e4(result):
    return cj(dataclasses.replace(result, output_hash=""))


def e5(configuration):
    return cj(configuration)


def e6(batch):
    return cj({"configuration_id": batch.configuration_id, "selection": batch.selection,
               "excluded_observations": batch.excluded_observations,
               "results": [[r.ef_key, r.output_hash] for r in batch.results]})


def sha256_hex(text):
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def integers_only(obj, where="envelope"):
    """Every JSON number an envelope carries is an integer (Decimals and dates are strings), so jsonb equality in
    PostgreSQL is byte equality of the canonical text (section 11.6.1)."""
    if isinstance(obj, float):
        raise CodecError(f"{where}: a JSON number that is not an integer")
    if isinstance(obj, list):
        for x in obj:
            integers_only(x, where)
    elif isinstance(obj, dict):
        for x in obj.values():
            integers_only(x, where)


def checked_envelope(text, expected_hash, what):
    """The envelope text, asserted ASCII-printable, integer-only and hashing to F6.3's hash."""
    if not text or any(not (" " <= ch <= "~") for ch in text):
        raise CodecError(f"{what}: the canonical JSON is not printable ASCII")
    if sha256_hex(text) != expected_hash:
        raise CodecError(f"{what}: SHA-256 of the envelope is not F6.3's hash")
    integers_only(json.loads(text), what)
    return text


# ------------------------------------------------------------------------------------------------ decoding

def _dec(x):
    return None if x is None else Decimal(x)


def _date(x):
    return None if x is None else date.fromisoformat(x)


def _tup(x):
    if isinstance(x, list):
        return tuple(_tup(v) for v in x)
    return x


_D = ("decimal",)
SPEC = {
    fv.ValueResult: {"parsed_value": _D, "normalized_value": _D, "printed_unit": _D, "half_unit": _D},
    fv.ComparisonResult: {k: _D for k in ("a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance",
                                           "abs_difference")},
    observations.ReportedValue: {"parsed_value": _D},
    observations.SOMember: {"reported": observations.ReportedValue, "value": fv.ValueResult, "printed_start": _date},
    observations.MemberComparison: {"comparison": fv.ComparisonResult},
    identity_mod.EconomicFactIdentity: {"period_end": _date},
    versions.VersionSet: {},
    inputs.F5RunRef: {},
    inputs.DocumentContext: {},
    observations.SourceObservation: {
        "identity": identity_mod.EconomicFactIdentity, "versions": versions.VersionSet, "f5_run": inputs.F5RunRef,
        "document": inputs.DocumentContext, "members": [observations.SOMember], "reported": observations.ReportedValue,
        "comparisons": [observations.MemberComparison], "normalized_value": _D, "half_unit": _D, "precision": _D,
        "interval_low": _D, "interval_high": _D},
    reconciliation.ObservationRef: {"reported": observations.ReportedValue, "normalized_value": _D, "half_unit": _D,
                                    "interval_low": _D, "interval_high": _D},
    reconciliation.PairComparison: {"comparison": fv.ComparisonResult},
    reconciliation.ReconciliationResult: {
        "identity": identity_mod.EconomicFactIdentity, "interval_low": _D, "interval_high": _D,
        "representative": observations.ReportedValue, "representative_normalized_value": _D,
        "representative_half_unit": _D, "observations": [reconciliation.ObservationRef],
        "comparisons": [reconciliation.PairComparison]},
    reconciliation.ReconciliationConfiguration: {"versions": versions.VersionSet},
}


def _decode(cls, d):
    if not isinstance(d, dict):
        raise CodecError(f"{cls.__name__}: expected an object")
    spec = SPEC[cls]
    names = [f.name for f in dataclasses.fields(cls)]
    if set(d) != set(names):
        raise CodecError(f"{cls.__name__}: fields {sorted(set(d) ^ set(names))} differ from the F6.3 dataclass")
    kw = {}
    for name in names:
        v, conv = d[name], spec.get(name)
        if v is None:
            kw[name] = None
        elif conv is _D:
            kw[name] = _dec(v)
        elif isinstance(conv, list):
            kw[name] = tuple(_decode(conv[0], x) for x in v)
        elif isinstance(conv, type):
            kw[name] = _decode(conv, v)
        elif conv is not None:
            kw[name] = conv(v)
        else:
            kw[name] = _tup(v)
    return cls(**kw)


def decode(cls, text):
    """An F6.3 object from its envelope, with the round-trip invariant checked: canonical_json(decode(e)) == e."""
    obj = _decode(cls, json.loads(text))
    again = cj(dataclasses.replace(obj, output_hash="")) if hasattr(obj, "output_hash") else cj(obj)
    if again != text:
        raise CodecError(f"{cls.__name__}: canonical_json(decode(envelope)) differs from the envelope")
    return obj


def decode_source_observation(so_json, output_hash):
    obj = decode(observations.SourceObservation, so_json)
    return dataclasses.replace(obj, output_hash=output_hash)


def decode_result(result_json, output_hash):
    obj = decode(reconciliation.ReconciliationResult, result_json)
    return dataclasses.replace(obj, output_hash=output_hash)


def decode_configuration(configuration_json):
    return decode(reconciliation.ReconciliationConfiguration, configuration_json)


# ------------------------------------------------------------------------------------------------ JSON projections
# The Python mirror of migration 0015's section 11.6.3 helpers: a typed value as canonical JSON.

def jnum(x):
    return None if x is None else format(x, "f")


def jdate(d):
    return None if d is None else d.isoformat()


def juuid(u):
    return None if u is None else str(u)


def jnil_forms(forms):
    return None if forms is None else [f.split(":") for f in forms]


def jsource_key(run, si, ri, ci, vo, concept):
    return [str(run), si, ri, ci, vo, concept or ""]


def jeq(a, b):
    """JSON equality with bool and int kept apart (True is not 1)."""
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(jeq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(jeq(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


_MISSING = object()


def mismatch(e, expected):
    """The first key (sorted) of `expected` whose value differs from e's, or None; a missing key is a mismatch."""
    for k in sorted(expected):
        v = e.get(k, _MISSING) if isinstance(e, dict) else _MISSING
        if v is _MISSING or not jeq(v, expected[k]):
            return k
    return None


def identity_json(f):
    return {"identity_version": f["identity_version"], "issuer_id": juuid(f["issuer_id"]),
            "concept_key": f["concept_key"], "period_kind": f["period_kind"], "period_end": jdate(f["period_end"]),
            "duration_months": f["duration_months"], "scope": f["scope"], "operations": f["operations"],
            "maturity": f["maturity"], "currency": f["currency"]}


def reported_json(row, present=True):
    """The ReportedValue JSON of a row's typed reported_* columns (T5 / T13); None without a representative."""
    if not present:
        return None
    return {"raw_value": row["reported_raw_value"], "parsed_value": jnum(row["reported_parsed_value"]),
            "representation_class": row["reported_representation_class"],
            "printed_decimals": row["reported_printed_decimals"], "sign_as_printed": row["reported_sign_as_printed"],
            "reported_scale": row["reported_scale"], "scale_basis": row["reported_scale_basis"],
            "currency": row["reported_currency"], "value_type": row["reported_value_type"]}


def candidate_reported_json(c):
    """The F6.3 ReportedValue of an F5 candidate row (F6.3 copies the F5 fields verbatim)."""
    return {"raw_value": c["raw_value"], "parsed_value": jnum(c["parsed_value"]),
            "representation_class": c["representation_class"], "printed_decimals": c["printed_decimals"],
            "sign_as_printed": c["sign_as_printed"], "reported_scale": c["reported_scale"],
            "scale_basis": c["scale_basis"], "currency": c["reported_currency"], "value_type": c["value_type"]}


# ------------------------------------------------------------------------------------------------ key texts (11.6.5)

def validation_run_key_text(r):
    return ('["validation_run",{"admission_version":"%s","identity_version":"%s","input_policy_version":"%s",'
            '"op1_version":"%s","validation_version":"%s"},"%s","%s"]'
            % (r["admission_version"], r["identity_version"], r["input_policy_version"], r["op1_version"],
               r["validation_version"], juuid(r["f5_run_id"]), r["input_hash"]))


def candidate_validation_key_text(vrk, run, r):
    return ('["candidate_validation","%s",["%s",%d,%d,%d,%d,"%s"]]'
            % (vrk, juuid(run), r["statement_index"], r["row_index"], r["column_index"], r["value_ordinal"],
               r["concept_key"] or ""))


def op1_key_text(r):
    return ('["op1","%s",[%d,"%s","%s","%s",%d,"%s","%s"],"%s"]'
            % (r["op1_version"], r["statement_root"], r["statement_kind"], r["period_kind"] or "",
               jdate(r["period_end"]) or "", -1 if r["duration_months"] is None else r["duration_months"],
               r["reported_scope"] or "", r["role"] or "", r["concept_key"]))


def so_key_text(r):
    return '["source_observation","%s","%s"]' % (r["validation_run_key"], r["ef_key"])


def ef_key_text(f):
    return ('{"identity_version":"%s","issuer_id":"%s","concept_key":"%s","period_kind":"%s","period_end":"%s",'
            '"duration_months":%s,"scope":"%s","operations":"%s","maturity":"%s","currency":"%s"}'
            % (f["identity_version"], juuid(f["issuer_id"]), f["concept_key"], f["period_kind"],
               jdate(f["period_end"]), "null" if f["duration_months"] is None else f["duration_months"], f["scope"],
               f["operations"], f["maturity"], f["currency"]))


# ------------------------------------------------------------------------------------------------ decomposition

def _group(o):
    root, kind, pk, end, months, scope, role = o.group_key
    return {"statement_root": root, "statement_kind": kind, "period_kind": pk or None,
            "period_end": date.fromisoformat(end) if end else None, "duration_months": None if months == -1 else months,
            "reported_scope": scope or None, "role": role or None}


def _comparison_values(c):
    cmp = c.comparison
    if cmp is None:
        return dict.fromkeys(("a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance", "abs_difference"))
    return {"a_value": cmp.a_value, "b_value": cmp.b_value, "a_half_unit": cmp.a_half_unit,
            "b_half_unit": cmp.b_half_unit, "tolerance": cmp.tolerance, "abs_difference": cmp.abs_difference}


def _reported_columns(rv):
    keys = ("raw_value", "parsed_value", "representation_class", "printed_decimals", "sign_as_printed", "scale",
            "scale_basis", "currency", "value_type")
    if rv is None:
        return {"reported_" + k: None for k in keys}
    return {"reported_raw_value": rv.raw_value, "reported_parsed_value": rv.parsed_value,
            "reported_representation_class": rv.representation_class,
            "reported_printed_decimals": rv.printed_decimals, "reported_sign_as_printed": rv.sign_as_printed,
            "reported_scale": rv.reported_scale, "reported_scale_basis": rv.scale_basis,
            "reported_currency": rv.currency, "reported_value_type": rv.value_type}


def validation_rows(vr, sos, *, store_version, code_revision, job_id, started_at, finished_at):
    """The complete decomposition of one validation run and the SOs built from it (T1-T7), with ordinals and element
    copies, exactly as section 11.6.2 lists them."""
    summary = admission.summarize([vr])["admission"]
    classification_id = vr.document.classification_id or vr.f5_run.classification_id
    t1 = {"validation_run_key": vr.key, "f5_run_id": vr.f5_run.f5_run_id, "cse_filing_id": vr.f5_run.cse_filing_id,
          "document_sha256": vr.f5_run.document_sha256, "classification_id": classification_id,
          "issuer_link_id": vr.issuer_link.link_id if vr.issuer_link is not None else None,
          "publication_source_field": vr.publication.source_field,
          "publication_uploaded_at": vr.publication.uploaded_at, "publication_date": vr.publication.publication_date,
          "validation_version": vr.versions.validation_version,
          "input_policy_version": vr.versions.input_policy_version, "op1_version": vr.versions.op1_version,
          "admission_version": vr.versions.admission_version, "identity_version": vr.versions.identity_version,
          "input_hash": vr.input_hash, "output_hash": vr.output_hash,
          "output_json": checked_envelope(e1(vr), vr.output_hash, "E1"),
          "candidates_total": len(vr.candidates), "admitted_numeric": summary.get("admitted_numeric", 0),
          "admitted_nil": summary.get("admitted_nil", 0), "not_admitted": summary.get("not_admitted", 0),
          "so_count": len(sos), "op1_count": len(vr.op1), "store_version": store_version,
          "code_revision": code_revision, "job_id": job_id, "started_at": started_at, "finished_at": finished_at}
    by_source = {r.source_key: r for r in vr.candidates}
    t2 = []
    for i, r in enumerate(vr.candidates):
        v, a = r.validation, r.admission
        if r.candidate_id is None:
            raise CodecError("a candidate without an id cannot be persisted (L7)")
        t2.append({"candidate_validation_key": r.key, "validation_run_key": vr.key, "candidate_ordinal": i,
                   "candidate_id": r.candidate_id, "statement_index": r.source_key[1], "row_index": r.source_key[2],
                   "column_index": r.source_key[3], "value_ordinal": r.source_key[4], "concept_key": v.concept_key,
                   "eligibility": v.eligibility, "ineligible_reasons": list(v.ineligible_reasons),
                   "normalization_reasons": list(v.normalization_reasons), "admitted": a.admitted,
                   "value_kind": a.value_kind, "nil_form": list(a.nil_form) if a.nil_form is not None else None,
                   "admission_reasons": list(a.reasons), "lifted_reasons": list(a.lifted_reasons),
                   "operations_route": a.operations_route, "op1_key": a.op1_record, "ef_key": a.ef_key,
                   "input_hash": r.input_hash, "output_hash": r.output_hash,
                   "output_json": checked_envelope(e2(r), r.output_hash, "E2")})
    t3 = []
    for i, o in enumerate(vr.op1):
        t3.append(dict({"validation_run_key": vr.key, "op1_key": o.key, "op1_ordinal": i, "op1_json": cj(o),
                        "op1_version": o.version, "concept_key": o.concept_key, "outcome": o.outcome,
                        "reasons": list(o.reasons), "currency": o.currency, "value_type": o.value_type,
                        "computed": o.computed, "total": o.total, "tolerance": o.tolerance,
                        "difference": o.difference,
                        "validated_candidate_ids": [by_source[k].candidate_id for k in o.validated]}, **_group(o)))
    t4, t5, t6, t7 = [], [], [], []
    for so in sos:
        idt = so.identity
        t4.append({"ef_key": so.ef_key, "identity_version": idt.identity_version, "issuer_id": idt.issuer_id,
                   "concept_key": idt.concept_key, "period_kind": idt.period_kind, "period_end": idt.period_end,
                   "duration_months": idt.duration_months, "scope": idt.scope, "operations": idt.operations,
                   "maturity": idt.maturity, "currency": idt.currency, "first_validation_run_key": vr.key})
        rep = next((m for m in so.members if m.source_key == so.representative_member), None)
        t5.append(dict({"so_key": so.so_key, "ef_key": so.ef_key, "validation_run_key": vr.key,
                        "f5_run_id": so.f5_run.f5_run_id, "cse_filing_id": so.f5_run.cse_filing_id,
                        "document_sha256": so.f5_run.document_sha256, "observation_status": so.observation_status,
                        "value_kind": so.value_kind, "nil_forms": [":".join(f) for f in so.nil_forms],
                        "member_count": len(so.members),
                        "representative_candidate_validation_key": rep.candidate_validation_key if rep else None,
                        "normalized_value": so.normalized_value, "half_unit": so.half_unit,
                        "precision": so.precision, "interval_low": so.interval_low,
                        "interval_high": so.interval_high, "roles": list(so.roles),
                        "annotations": list(so.annotations), "output_hash": so.output_hash,
                        "so_json": checked_envelope(e3(so), so.output_hash, "E3")}, **_reported_columns(so.reported)))
        index = {m.source_key: (i, m) for i, m in enumerate(so.members)}
        for i, m in enumerate(so.members):
            t6.append({"so_key": so.so_key, "candidate_validation_key": m.candidate_validation_key,
                       "candidate_id": m.candidate_id, "member_ordinal": i, "member_json": cj(m),
                       "value_kind": m.value_kind, "normalized_value": m.value.normalized_value,
                       "half_unit": m.value.half_unit, "currency": m.value.currency,
                       "value_type": m.value.value_type, "role": m.role, "period_derivation": m.period_derivation,
                       "operations_route": m.operations_route, "maturity_basis": m.maturity_basis,
                       "audit_label_reported": m.audit_label_reported, "restated": m.restated})
        for k, c in enumerate(so.comparisons):
            (ia, ma), (ib, mb) = index[c.a], index[c.b]
            t7.append(dict({"so_key": so.so_key, "comparison_ordinal": k,
                            "a_candidate_validation_key": ma.candidate_validation_key, "a_member_ordinal": ia,
                            "b_candidate_validation_key": mb.candidate_validation_key, "b_member_ordinal": ib,
                            "comparison_json": cj(c), "outcome": c.outcome, "reason": c.reason,
                            "sign_only": c.sign_only}, **_comparison_values(c)))
    return {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T6": t6, "T7": t7}


def record_rows(result, so_by_key):
    """The decomposition of one reconciliation result: the T13 row (without chain / batch columns) and its T14 / T15
    children. so_by_key: {so_key: SourceObservation} of the result's inputs (decoded stored SOs)."""
    rep_cvk = None
    if result.representative_so is not None:
        rep_so = so_by_key[result.representative_so]
        rep_cvk = next(m.candidate_validation_key for m in rep_so.members
                       if m.source_key == result.representative_member)
    t13 = dict({"ef_key": result.ef_key, "configuration_id": result.configuration_id,
                "reconciliation_version": result.reconciliation_version, "input_hash": result.input_hash,
                "output_hash": result.output_hash, "state": result.state, "value_kind": result.value_kind,
                "interval_low": result.interval_low, "interval_high": result.interval_high,
                "representative_so_key": result.representative_so,
                "representative_candidate_validation_key": rep_cvk,
                "representative_normalized_value": result.representative_normalized_value,
                "representative_half_unit": result.representative_half_unit,
                "document_count": result.document_count, "so_count": result.so_count,
                "comparison_count": len(result.comparisons), "annotations": list(result.annotations),
                "reasons": list(result.reasons),
                "result_json": checked_envelope(e4(result), result.output_hash, "E4")},
               **_reported_columns(result.representative))
    obs_index = {o.so_key: i for i, o in enumerate(result.observations)}
    t14 = [{"observation_ordinal": i, "so_key": o.so_key, "observation_json": cj(o),
            "document_sha256": o.document_sha256, "so_output_hash": o.so_output_hash,
            "role_in_outcome": o.role_in_outcome} for i, o in enumerate(result.observations)]
    t15 = []
    for k, c in enumerate(result.comparisons):
        a_so, b_so = so_by_key[c.a_so], so_by_key[c.b_so]
        pa = next(i for i, m in enumerate(a_so.members) if m.source_key == c.a_member)
        pb = next(i for i, m in enumerate(b_so.members) if m.source_key == c.b_member)
        t15.append(dict({"comparison_ordinal": k, "a_so_key": c.a_so, "a_observation_ordinal": obs_index[c.a_so],
                         "a_candidate_validation_key": a_so.members[pa].candidate_validation_key,
                         "a_member_ordinal": pa, "b_so_key": c.b_so, "b_observation_ordinal": obs_index[c.b_so],
                         "b_candidate_validation_key": b_so.members[pb].candidate_validation_key,
                         "b_member_ordinal": pb, "comparison_json": cj(c), "outcome": c.outcome,
                         "reason": c.reason, "sign_only": c.sign_only}, **_comparison_values(c)))
    return t13, t14, t15


# ------------------------------------------------------------------------------------------------ the Python mirror
# Each check returns a list of problems (empty = the decomposition is exactly the envelope). The rows are dicts keyed
# by column name, either built above or read back from PostgreSQL (verify); the rules are the same either way.

def _num_tuple_order(keys):
    return all(a < b for a, b in zip(keys, keys[1:]))


COLOMBO = timezone(timedelta(hours=5, minutes=30))


def colombo_date(uploaded_at):
    """f6.inputs.1: the Asia/Colombo (fixed UTC+05:30) date of the publication instant (an ISO string as F6.3 holds
    it, or a timestamptz read back); the mirror of the T1 guard."""
    at = datetime.fromisoformat(uploaded_at) if isinstance(uploaded_at, str) else uploaded_at
    return at.astimezone(COLOMBO).date()


def _reconstruct(problems, what, envelope_array, children, element, ordinal):
    kids = sorted(children, key=lambda r: r[ordinal])
    if [r[ordinal] for r in kids] != list(range(len(kids))):
        problems.append(f"EDI-1 {what}: ordinals are not exactly 0..n-1")
    got = [element(r) for r in kids]
    if not jeq(envelope_array, got):
        problems.append(f"EDI-1 {what}: the children do not reconstruct the envelope array")
    return kids


def check_validation(d, f5_reported, issuer_id=None):
    """EDI-1..5 of one validation-run decomposition d = {"T1": row, "T2": [...], ...} (T4: the identity rows of the
    facts its SOs reference). f5_reported: {candidate_id: ReportedValue JSON of the F5 row} for every candidate of the
    run's F5 run; issuer_id: the issuer of the run's issuer decision (None: not checked)."""
    p = []
    t1 = d["T1"]
    vrk, run = t1["validation_run_key"], t1["f5_run_id"]
    if sha256_hex(validation_run_key_text(t1)) != vrk:
        p.append("EDI-4 validation_run_key")
    uploaded, published = t1["publication_uploaded_at"], t1["publication_date"]
    if (uploaded is None) != (published is None) or (
            uploaded is not None and t1["input_policy_version"] == "f6.inputs.1"
            and published != colombo_date(uploaded)):
        p.append("T1 publication_date is not the Asia/Colombo (UTC+05:30) date of the instant")
    env1 = json.loads(t1["output_json"])
    if t1["candidates_total"] != len(env1["candidates"]) or t1["op1_count"] != len(env1["op1"]):
        p.append("EDI-2 T1 counts")
    t2 = _reconstruct(p, "E1 candidates", env1["candidates"], d["T2"],
                      lambda r: [r["candidate_validation_key"], r["output_hash"]], "candidate_ordinal")
    if not _num_tuple_order([(r["statement_index"], r["row_index"], r["column_index"], r["value_ordinal"],
                              r["concept_key"] or "") for r in t2]):
        p.append("EDI-5 T2 order")
    for r in t2:
        e = json.loads(r["output_json"])
        bad = mismatch(e["validation"], {
            "source_key": jsource_key(run, r["statement_index"], r["row_index"], r["column_index"],
                                      r["value_ordinal"], r["concept_key"]),
            "concept_key": r["concept_key"], "candidate_id": r["candidate_id"], "eligibility": r["eligibility"],
            "ineligible_reasons": list(r["ineligible_reasons"]),
            "normalization_reasons": list(r["normalization_reasons"])}) or mismatch(e["admission"], {
            "admitted": r["admitted"], "value_kind": r["value_kind"],
            "nil_form": list(r["nil_form"]) if r["nil_form"] is not None else None,
            "reasons": list(r["admission_reasons"]), "lifted_reasons": list(r["lifted_reasons"]),
            "operations_route": r["operations_route"], "op1_record": r["op1_key"], "ef_key": r["ef_key"]})
        if bad:
            p.append(f"EDI-2 T2.{bad}")
        if sha256_hex(candidate_validation_key_text(vrk, run, r)) != r["candidate_validation_key"]:
            p.append("EDI-4 candidate_validation_key")
        if f5_reported and r["candidate_id"] not in f5_reported:
            p.append("T2 candidate_id is not a candidate of this F5 run")
    t3 = _reconstruct(p, "E1 op1", env1["op1"], d["T3"], lambda r: json.loads(r["op1_json"]), "op1_ordinal")
    if not _num_tuple_order([(r["statement_root"], r["statement_kind"], r["period_kind"] or "",
                              jdate(r["period_end"]) or "", -1 if r["duration_months"] is None else r["duration_months"],
                              r["reported_scope"] or "", r["role"] or "", r["concept_key"]) for r in t3]):
        p.append("EDI-5 T3 order")
    by_source = {json.dumps(jsource_key(run, r["statement_index"], r["row_index"], r["column_index"],
                                        r["value_ordinal"], r["concept_key"])): r for r in t2}
    for r in t3:
        e = json.loads(r["op1_json"])
        bad = mismatch(e, {"key": r["op1_key"], "version": r["op1_version"],
                           "group_key": [r["statement_root"], r["statement_kind"], r["period_kind"] or "",
                                         jdate(r["period_end"]) or "",
                                         -1 if r["duration_months"] is None else r["duration_months"],
                                         r["reported_scope"] or "", r["role"] or ""],
                           "concept_key": r["concept_key"], "outcome": r["outcome"], "reasons": list(r["reasons"]),
                           "currency": r["currency"], "value_type": r["value_type"],
                           "computed": jnum(r["computed"]), "total": jnum(r["total"]),
                           "tolerance": jnum(r["tolerance"]), "difference": jnum(r["difference"])})
        if bad:
            p.append(f"EDI-2 T3.{bad}")
        if sha256_hex(op1_key_text(r)) != r["op1_key"]:
            p.append("EDI-4 op1_key")
        mapped = [by_source.get(json.dumps(k)) for k in e["validated"]]
        if None in mapped or [m["candidate_id"] for m in mapped] != list(r["validated_candidate_ids"]):
            p.append("EDI-3 T3.validated_candidate_ids")
    admitted = [r for r in t2 if r["admitted"]]
    if (sum(1 for r in admitted if r["value_kind"] == "numeric") != t1["admitted_numeric"]
            or sum(1 for r in admitted if r["value_kind"] == "nil") != t1["admitted_nil"]
            or len(t2) - len(admitted) != t1["not_admitted"] or len(d["T5"]) != t1["so_count"]):
        p.append("completeness T1 counts")
    member_keys = sorted(r["candidate_validation_key"] for r in d["T6"])
    if member_keys != sorted(r["candidate_validation_key"] for r in admitted):
        p.append("completeness: admitted candidates are not exactly the SO members")
    facts = {f["ef_key"]: f for f in d["T4"]}
    cv_by_key = {r["candidate_validation_key"]: r for r in t2}
    for f in d["T4"]:
        if sha256_hex(ef_key_text(f)) != f["ef_key"]:
            p.append("T4 ef_key is not the f6.identity.1 hash of the typed identity")
        if issuer_id is not None and juuid(f["issuer_id"]) != juuid(issuer_id):
            p.append("the fact's issuer is not the validation run's issuer decision")
    for s in d["T5"]:
        p.extend(check_source_observation(s, [m for m in d["T6"] if m["so_key"] == s["so_key"]],
                                          [c for c in d["T7"] if c["so_key"] == s["so_key"]], t1,
                                          facts.get(s["ef_key"]), cv_by_key, f5_reported))
    return p


def check_source_observation(s, members, comparisons, t1, fact, cv_by_key, f5_reported):
    """EDI-1..5 of one SO (T5 + its T6 / T7). cv_by_key: candidate validations of its run; f5_reported: the F5 rows'
    ReportedValue JSON by candidate id; fact: its T4 row."""
    p = []
    e = json.loads(s["so_json"])
    run = t1["f5_run_id"]
    if sha256_hex(so_key_text(s)) != s["so_key"]:
        p.append("EDI-4 so_key")
    rep_present = s["representative_candidate_validation_key"] is not None
    bad = mismatch(e, {
        "so_key": s["so_key"], "ef_key": s["ef_key"], "validation_run_key": s["validation_run_key"],
        "observation_status": s["observation_status"], "value_kind": s["value_kind"],
        "nil_forms": jnil_forms(s["nil_forms"]), "normalized_value": jnum(s["normalized_value"]),
        "half_unit": jnum(s["half_unit"]), "precision": jnum(s["precision"]),
        "interval_low": jnum(s["interval_low"]), "interval_high": jnum(s["interval_high"]),
        "roles": list(s["roles"]), "annotations": list(s["annotations"]), "output_hash": "",
        "reported": reported_json(s, present=rep_present),
        "identity": identity_json(fact) if fact else None,
        "versions": {k: t1[k] for k in ("admission_version", "identity_version", "input_policy_version",
                                        "op1_version", "validation_version")}}) or mismatch(e["f5_run"], {
        "f5_run_id": juuid(s["f5_run_id"]), "cse_filing_id": s["cse_filing_id"],
        "document_sha256": s["document_sha256"]})
    if bad or s["member_count"] != len(e["members"]):
        p.append(f"EDI-2 T5.{bad or 'member_count'}")
    kids = _reconstruct(p, "E3 members", e["members"], members, lambda r: json.loads(r["member_json"]),
                        "member_ordinal")
    order = [(cv_by_key[m["candidate_validation_key"]]["statement_index"],
              cv_by_key[m["candidate_validation_key"]]["row_index"],
              cv_by_key[m["candidate_validation_key"]]["column_index"],
              cv_by_key[m["candidate_validation_key"]]["value_ordinal"],
              cv_by_key[m["candidate_validation_key"]]["concept_key"] or "")
             for m in kids if m["candidate_validation_key"] in cv_by_key]
    if len(order) != len(kids) or not _num_tuple_order(order):
        p.append("EDI-5 T6 order")
    mjson = {m["candidate_validation_key"]: json.loads(m["member_json"]) for m in kids}
    for m in kids:
        mj = mjson[m["candidate_validation_key"]]
        bad = mismatch(mj, {"candidate_validation_key": m["candidate_validation_key"],
                            "candidate_id": m["candidate_id"], "value_kind": m["value_kind"], "role": m["role"],
                            "period_derivation": m["period_derivation"], "operations_route": m["operations_route"],
                            "maturity_basis": m["maturity_basis"], "audit_label_reported": m["audit_label_reported"],
                            "restated": m["restated"]}) or mismatch(mj["value"], {
            "normalized_value": jnum(m["normalized_value"]), "half_unit": jnum(m["half_unit"]),
            "currency": m["currency"], "value_type": m["value_type"]})
        if bad:
            p.append(f"EDI-2 T6.{bad}")
        cv = cv_by_key.get(m["candidate_validation_key"])
        if cv is None or not cv["admitted"] or cv["ef_key"] != s["ef_key"] or cv["value_kind"] != m["value_kind"]:
            p.append("T6 member is not an admitted candidate validation of this run and fact")
            continue
        e2 = json.loads(cv["output_json"])
        bad = mismatch(mj, {"source_key": jsource_key(run, cv["statement_index"], cv["row_index"],
                                                      cv["column_index"], cv["value_ordinal"], cv["concept_key"]),
                            "candidate_id": cv["candidate_id"],
                            "statement_index": cv["statement_index"], "row_index": cv["row_index"],
                            "column_index": cv["column_index"], "value_ordinal": cv["value_ordinal"],
                            "value": e2["validation"]["value"], "nil_form": e2["admission"]["nil_form"],
                            "op1_record": e2["admission"]["op1_record"],
                            "value_kind": e2["admission"]["value_kind"],
                            "operations_route": e2["admission"]["operations_route"],
                            "reported": f5_reported.get(m["candidate_id"])})
        if bad:
            p.append(f"EDI-3 T6 member_json.{bad}")
    n = len(kids)
    ckids = _reconstruct(p, "E3 comparisons", e["comparisons"], comparisons,
                         lambda r: json.loads(r["comparison_json"]), "comparison_ordinal")
    if [(c["a_member_ordinal"], c["b_member_ordinal"]) for c in ckids] != [(i, j) for i in range(n)
                                                                           for j in range(i + 1, n)]:
        p.append("EDI-5 T7 pair sequence")
    by_ord = {m["member_ordinal"]: m for m in kids}
    for c in ckids:
        ma, mb = by_ord.get(c["a_member_ordinal"]), by_ord.get(c["b_member_ordinal"])
        if ma is None or mb is None or ma["candidate_validation_key"] != c["a_candidate_validation_key"] \
                or mb["candidate_validation_key"] != c["b_candidate_validation_key"]:
            p.append("T7 member ordinal / key mismatch")
            continue
        p.extend(_check_comparison(json.loads(c["comparison_json"]), c, mjson[ma["candidate_validation_key"]],
                                   mjson[mb["candidate_validation_key"]], "T7"))
    rep_member = e["representative_member"]
    if rep_member is None:
        if rep_present:
            p.append("EDI-2 T5 representative without E3 representative")
    else:
        mj = mjson.get(s["representative_candidate_validation_key"])
        if mj is None or not jeq(mj["source_key"], rep_member):
            p.append("EDI-2 T5 representative member")
        elif not (jeq(e["reported"], mj["reported"]) and jeq(e["normalized_value"], mj["value"]["normalized_value"])
                  and jeq(e["half_unit"], mj["value"]["half_unit"])):
            p.append("EDI-3 E3 representative copies")
        if f5_reported and rep_present:
            cv = cv_by_key.get(s["representative_candidate_validation_key"])
            if cv is None or not jeq(reported_json(s), f5_reported.get(cv["candidate_id"])):
                p.append("EDI-3 T5 reported copy differs from the F5 row")
    return p


def _check_comparison(e, c, ma, mb, table):
    p = []
    if table == "T7":
        if not (jeq(e["a"], ma["source_key"]) and jeq(e["b"], mb["source_key"])):
            p.append("EDI-3 T7 a / b source keys")
    bad = mismatch(e, {"outcome": c["outcome"], "reason": c["reason"], "sign_only": c["sign_only"]})
    if bad:
        p.append(f"EDI-2 {table}.{bad}")
    vals = ("a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance", "abs_difference")
    if e["comparison"] is None:
        if any(c[v] is not None for v in vals):
            p.append(f"EDI-2 {table}.comparison")
        return p
    bad = mismatch(e["comparison"], {v: jnum(c[v]) for v in vals})
    if bad:
        p.append(f"EDI-2 {table}.comparison.{bad}")
    bad = mismatch(e["comparison"], {"a_value": ma["value"]["normalized_value"], "a_half_unit": ma["value"]["half_unit"],
                                     "currency": ma["value"]["currency"], "value_type": ma["value"]["value_type"],
                                     "b_value": mb["value"]["normalized_value"], "b_half_unit": mb["value"]["half_unit"]})
    if bad:
        p.append(f"EDI-3 {table}.comparison.{bad}")
    return p


def check_record(t13, t14, t15, fact, sos, reconciliation_version=None):
    """EDI-1..5 of one reconciliation record (T13 + T14 / T15). sos: {so_key: {"row": T5 row, "members": {cvk: member
    row}}} of its inputs; fact: its T4 row; reconciliation_version: its configuration's (None: not checked)."""
    p = []
    e = json.loads(t13["result_json"])
    if reconciliation_version is not None and t13["reconciliation_version"] != reconciliation_version:
        p.append("T13 reconciliation_version differs from the configuration's")
    present = t13["representative_so_key"] is not None
    bad = mismatch(e, {
        "ef_key": t13["ef_key"], "configuration_id": t13["configuration_id"],
        "reconciliation_version": t13["reconciliation_version"], "input_hash": t13["input_hash"],
        "state": t13["state"], "value_kind": t13["value_kind"], "interval_low": jnum(t13["interval_low"]),
        "interval_high": jnum(t13["interval_high"]), "representative_so": t13["representative_so_key"],
        "representative_normalized_value": jnum(t13["representative_normalized_value"]),
        "representative_half_unit": jnum(t13["representative_half_unit"]),
        "document_count": t13["document_count"], "so_count": t13["so_count"],
        "annotations": list(t13["annotations"]), "reasons": list(t13["reasons"]), "output_hash": "",
        "representative": reported_json(t13, present=present), "identity": identity_json(fact) if fact else None})
    if bad or t13["so_count"] != len(e["observations"]) or t13["comparison_count"] != len(e["comparisons"]):
        p.append(f"EDI-2 T13.{bad or 'so_count / comparison_count'}")
    if present:
        m = sos.get(t13["representative_so_key"], {}).get("members", {}).get(
            t13["representative_candidate_validation_key"])
        mj = json.loads(m["member_json"]) if m else None
        if mj is None or not jeq(mj["source_key"], e["representative_member"]):
            p.append("EDI-3 T13 representative member")
        elif not (jeq(e["representative"], mj["reported"])
                  and jeq(e["representative_normalized_value"], mj["value"]["normalized_value"])
                  and jeq(e["representative_half_unit"], mj["value"]["half_unit"])):
            p.append("EDI-3 E4 representative copies")
    elif e["representative_member"] is not None:
        p.append("EDI-2 T13 representative member without a representative SO")
    ins = _reconstruct(p, "E4 observations", e["observations"], t14, lambda r: json.loads(r["observation_json"]),
                       "observation_ordinal")
    if not _num_tuple_order([(i["document_sha256"], i["so_key"]) for i in ins]):
        p.append("EDI-5 T14 order")
    for i in ins:
        o = json.loads(i["observation_json"])
        bad = mismatch(o, {"so_key": i["so_key"], "so_output_hash": i["so_output_hash"],
                           "document_sha256": i["document_sha256"], "role_in_outcome": i["role_in_outcome"]})
        if bad:
            p.append(f"EDI-2 T14.{bad}")
        so = sos.get(i["so_key"])
        if so is None:
            p.append("T14 SO missing")
            continue
        s = json.loads(so["row"]["so_json"])
        bad = mismatch(o, {"so_output_hash": so["row"]["output_hash"],
                           "document_sha256": s["f5_run"]["document_sha256"],
                           "cse_filing_id": s["f5_run"]["cse_filing_id"], "f5_run_id": s["f5_run"]["f5_run_id"],
                           "observation_status": s["observation_status"], "value_kind": s["value_kind"],
                           "reported": s["reported"], "normalized_value": s["normalized_value"],
                           "half_unit": s["half_unit"], "interval_low": s["interval_low"],
                           "interval_high": s["interval_high"], "roles": s["roles"],
                           "document_type": s["document"]["document_type"],
                           "underlying_type": s["document"]["underlying_type"]})
        if bad or i["document_sha256"] != so["row"]["document_sha256"]:
            p.append(f"EDI-3 T14 observation_json.{bad or 'document_sha256'}")
    cmps = _reconstruct(p, "E4 comparisons", e["comparisons"], t15, lambda r: json.loads(r["comparison_json"]),
                        "comparison_ordinal")
    counts = [len(sos[i["so_key"]]["members"]) if i["so_key"] in sos else 0 for i in ins]
    expected = [(i, j, a, b) for i in range(len(ins)) for j in range(i + 1, len(ins))
                for a in range(counts[i]) for b in range(counts[j])]
    if [(c["a_observation_ordinal"], c["b_observation_ordinal"], c["a_member_ordinal"], c["b_member_ordinal"])
            for c in cmps] != expected:
        p.append("EDI-5 T15 pair sequence")
    for c in cmps:
        ma = sos.get(c["a_so_key"], {}).get("members", {}).get(c["a_candidate_validation_key"])
        mb = sos.get(c["b_so_key"], {}).get("members", {}).get(c["b_candidate_validation_key"])
        if ma is None or mb is None or ma["member_ordinal"] != c["a_member_ordinal"] \
                or mb["member_ordinal"] != c["b_member_ordinal"]:
            p.append("T15 member reference")
            continue
        ma_j, mb_j = json.loads(ma["member_json"]), json.loads(mb["member_json"])
        el = json.loads(c["comparison_json"])
        bad = mismatch(el, {"a_so": c["a_so_key"], "b_so": c["b_so_key"], "a_member": ma_j["source_key"],
                            "b_member": mb_j["source_key"]})
        if bad:
            p.append(f"EDI-3 T15.{bad}")
        p.extend(_check_comparison(el, c, ma_j, mb_j, "T15"))
    if len({i["document_sha256"] for i in ins}) != t13["document_count"]:
        p.append("completeness T13 document_count")
    for i in ins:
        want = ("conflicting" if t13["state"] == "conflicting" else
                "representative" if i["so_key"] == t13["representative_so_key"] else "supporting")
        if i["role_in_outcome"] != want:
            p.append("completeness T14 roles")
            break
    return p


def check_configuration(t8):
    """EDI-2 of one configuration (T8 <-> E5): its typed versions are the envelope's."""
    e = json.loads(t8["configuration_json"])
    bad = mismatch(e, {"reconciliation_version": t8["reconciliation_version"]}) or mismatch(
        e.get("versions"), {k: t8[k] for k in ("validation_version", "input_policy_version", "op1_version",
                                               "admission_version", "identity_version")})
    return [f"EDI-2 T8.{bad}"] if bad else []


def check_batch(t12, t16, record_hashes):
    """EDI-1 / EDI-5 of one batch (T12 + T16). record_hashes: {record_id: output_hash}."""
    p = []
    e = json.loads(t12["output_json"])
    if e["configuration_id"] != t12["configuration_id"] or t12["results_count"] != len(e["results"]):
        p.append("EDI-2 T12")
    kids = _reconstruct(p, "E6 results", e["results"], t16,
                        lambda r: [r["ef_key"], record_hashes.get(r["record_id"])], "result_ordinal")
    if not _num_tuple_order([r["ef_key"] for r in kids]):
        p.append("EDI-5 T16 order")
    if sum(1 for r in t16 if r["appended"]) != t12["records_appended"]:
        p.append("completeness T12 records_appended")
    return p
