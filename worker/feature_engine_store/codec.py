"""DE-4: canonical records and their hashes (pure). Every hash binds the record's own columns; a row binds its
panel's hash (not the database id), a run binds every output's hash, so `verify` can recompute the chain from the
stored columns alone (§10.3)."""
import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from worker.engines import registry as reg
from worker.engines import scoring as sc
from worker.financial_features import eligibility as el
from worker.financial_features import features as ft

MODES = {"KNOWN": "known", "KNOWN_RECORDED": "known_recorded", "AVAILABLE": "reconstructed"}
STATUSES = ("provisional", "validated")


def _plain(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if is_dataclass(x):
        return _plain(asdict(x))
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def canonical(obj):
    """Sorted keys, no spaces, ASCII only (the migration's printable-ASCII checks)."""
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------ configurations

def f7_configuration(status="provisional", policy=el.FinancialEligibilityPolicy(), n_min=sc.N_MIN):
    """(configuration_id, configuration_json, status) of the F7 configuration (§10.1)."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    from worker.financial_features import panel as pn, periods as pr, valuation as va
    js = canonical({
        "registry": ft.REGISTRY_VERSION, "industry": ft.INDUSTRY_POLICY, "scope": pn.SCOPE_POLICY,
        "currency": pn.CURRENCY_POLICY, "sign": pn.SIGN_POLICY, "security_link": pn.LINK_POLICY,
        "eps_basis": va.EPS_BASIS_POLICY, "cashflow_sign": pr.CASHFLOW_SIGN_POLICY,
        "period_rules": list(pr.TTM_RULES) + ["F7P-5", "F7P-6", "F7P-7", "F7P-8"],
        "financial_eligibility_policy": _plain(policy), "N_min": n_min, "status": status})
    return sha(js), js, status


def de_configuration(status="provisional", n_min=sc.N_MIN):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    js = canonical({"registry": reg.REGISTRY_VERSION, "engines": [_plain(e) for e in reg.ENGINES],
                    "N_min": n_min, "blocking": list(sc.BLOCKING), "admissible": list(reg.ADMISSIBLE),
                    "places": str(sc.PLACES), "status": status})
    return sha(js), js, status


# ------------------------------------------------------------------------------------------------ records

PANEL_COLUMNS = ("issuer_id", "mode", "result_label", "cutoff", "knowledge_horizon", "f7_configuration_id",
                 "f8_configuration_id", "f8_result_hash", "state", "scope", "currency", "newest_period",
                 "anchor_period", "anchor_months", "flags", "counts_json", "created_by", "recorded_at")


def panel_record(p, f7_configuration_id, stamp):
    """The f7_panels columns of one Panel, with panel_hash binding every column. stamp: (recorded_at, created_by),
    read by the writer in its transaction, so the hash binds who wrote the row and when."""
    if p.mode not in MODES or MODES[p.mode] != p.f8_label:
        raise ValueError(f"panel mode {p.mode!r} with label {p.f8_label!r} is not storable")
    rec = dict(issuer_id=p.issuer_id, mode=p.mode, result_label=p.f8_label, cutoff=p.information_cutoff,
               knowledge_horizon=p.knowledge_horizon, f7_configuration_id=f7_configuration_id,
               f8_configuration_id=p.f8_configuration_id, f8_result_hash=p.f8_result_hash, state=p.state,
               scope=p.scope, currency=p.currency,
               newest_period=None if p.newest_period is None else p.newest_period.isoformat(),
               anchor_period=None if p.anchor_period is None else p.anchor_period.isoformat(),
               anchor_months=p.anchor_months, flags=",".join(sorted(p.flags)), counts_json=canonical(dict(p.counts)), recorded_at=stamp[0], created_by=stamp[1])
    rec["panel_hash"] = panel_hash(rec)
    return rec


def panel_hash(rec):
    return sha(canonical({k: _norm(rec[k]) for k in PANEL_COLUMNS}))


def _norm(v):
    """A column as hashed: instants as UTC ISO text, whether given as text or read back as a datetime; an instant
    without a zone is refused (never guessed)."""
    if isinstance(v, str) and len(v) >= 19 and v[10:11] in ("T", " ") and v[:4].isdigit():
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise ValueError(f"instant {v.isoformat()} has no time zone")
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def feature_row(panel_rec, subject, level, values, eligibility, *, snapshot_id=None, link_decision_id=None,
                mf_row_hash=None, label_class="point_in_time"):
    """The f7_feature_rows columns of one subject. values: {feature_id: FeatureValue}; eligibility: an Eligibility
    (or a plain dict)."""
    rec = dict(subject=subject, level=level, snapshot_id=snapshot_id, link_decision_id=link_decision_id,
               mf_row_hash=mf_row_hash, label_class=label_class,
               features_json=canonical({k: values[k] for k in sorted(values)}),
               eligibility_json=canonical(eligibility))
    rec["row_hash"] = row_hash(panel_rec["panel_hash"], rec)
    return rec


ROW_COLUMNS = ("subject", "level", "snapshot_id", "link_decision_id", "mf_row_hash", "label_class", "features_json",
               "eligibility_json")


def row_hash(panel_hash_, rec):
    return sha(canonical({"panel_hash": panel_hash_, **{k: rec[k] for k in ROW_COLUMNS}}))


def output_record(o, issuer_id=None):
    """The de_outputs columns of one EngineOutput (fan-out: the issuer whose output the security carries)."""
    js = canonical({"engine_id": o.engine_id, "engine_version": o.engine_version, "member": o.member,
                    "state": o.state, "score": o.score, "components": list(o.components),
                    "components_present": list(o.components_present), "universe": o.universe,
                    "inputs_hash": o.inputs_hash, "evidence": o.evidence, "label_class": o.label_class,
                    "issuer_id": issuer_id})
    return dict(engine_id=o.engine_id, engine_version=o.engine_version, member=o.member, issuer_id=issuer_id,
                state=o.state, output_json=js, output_hash=sha(js))


RUN_COLUMNS = ("mode", "cutoff", "knowledge_horizon", "snapshot_id", "f7_configuration_id", "de_configuration_id",
               "label_class", "universes_json", "counts_json", "created_by", "recorded_at")


def run_record(*, mode, cutoff, knowledge_horizon, snapshot_id, f7_configuration_id, de_configuration_id,
               label_class, outputs, stamp):
    """The de_runs columns, with run_hash binding every column and every output's (engine, member, hash)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {tuple(MODES)}")
    if (mode == "AVAILABLE") != (knowledge_horizon is not None):
        raise ValueError("AVAILABLE needs H; KNOWN and KNOWN_RECORDED have none")
    universes, counts = {}, {}
    for o in outputs:
        u = json.loads(o["output_json"])["universe"]
        universes.setdefault(o["engine_id"], {})[u.get("group") or ""] = u
        counts[o["state"]] = counts.get(o["state"], 0) + 1
    rec = dict(mode=mode, cutoff=cutoff, knowledge_horizon=knowledge_horizon, snapshot_id=snapshot_id,
               f7_configuration_id=f7_configuration_id, de_configuration_id=de_configuration_id,
               label_class=label_class, universes_json=canonical(universes), counts_json=canonical(counts),
               recorded_at=stamp[0], created_by=stamp[1])
    rec["run_hash"] = run_hash(rec, outputs)
    return rec


def run_hash(rec, outputs):
    outs = sorted((o["engine_id"], o["member"], o["output_hash"]) for o in outputs)
    return sha(canonical({"run": {k: _norm(rec[k]) for k in RUN_COLUMNS}, "outputs": outs}))
