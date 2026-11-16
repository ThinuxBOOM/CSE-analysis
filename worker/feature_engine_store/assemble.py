"""
DE-4: one run assembled from its inputs (pure; no database). Inputs are what the loader read for one MF snapshot:
the snapshot's rows (MF features, eligibility, the reference price and factors), the issuer link decisions, and one
F8 result per linked issuer. Output: the F7 panel batches and the DE run batch the writer stores.

  1. each line's link at T (KNOWN) or H (AVAILABLE; labelled reconstructed), f7.security_link.1 (§4.6);
  2. each issuer's panel, its fundamental and market-value features, annotated, and its financial eligibility (§11);
  3. each linked line's per-share valuation features (§6.3) on its own MF row;
  4. the engines (§7.2): fundamental issuer engines rank eligible issuers within their industry group and fan out to
     every linked line; fund_valuation_eps ranks lines within the issuer's group; market engines rank the
     market-data-eligible lines. A line or issuer outside an engine's group gets not_applicable.

market_cap_reported is not stored in an MF snapshot row (C-0079): the loader passes it as absent, so the market-value
features are missing (incomplete_lines) until MF stores it.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from worker.engines import registry as rg
from worker.engines import scoring as sc
from worker.financial_features import eligibility as el
from worker.financial_features import features as ft
from worker.financial_features import panel as pn
from worker.financial_features import valuation as va

from .writer import PanelBatch, RunBatch

MF_LABEL = {"KNOWN": "point_in_time", "AVAILABLE": "reconstructed"}
F8_MODE = {"KNOWN": "KNOWN", "AVAILABLE": "AVAILABLE"}
PIPELINE = {"KNOWN": "live", "AVAILABLE": "point_in_time"}
MARKET_ELIGIBLE = "eligible"


@dataclass(frozen=True)
class MFRow:
    """One mf_snapshot_rows row as F7 and DE read it."""
    security: va.SecurityRow
    eligibility: str
    features: dict                       # {feature_id: scoring.Value}
    row_hash: str


@dataclass
class Assembled:
    panels: list = field(default_factory=list)
    run: Optional[RunBatch] = None
    links: dict = field(default_factory=dict)      # {symbol: Link}


def mf_values(features_json_obj):
    """MF's stored features ({fid: {value, state, reasons}}) as scoring Values (the first reason code)."""
    out = {}
    for fid, x in features_json_obj.items():
        v = x.get("value")
        reasons = x.get("reasons") or []          # MF stores [[state, reason], ...] (store.row_payload)
        out[fid] = sc.Value(None if v is None else Decimal(v), x.get("state"),
                            reasons[0][1] if reasons and len(reasons[0]) > 1 else None)
    return out


def _aware(x):
    """An instant as an aware datetime (ISO text accepted); an instant without a zone is refused, never guessed."""
    from datetime import datetime
    if x is None:
        return None
    d = datetime.fromisoformat(x) if isinstance(x, str) else x
    if d.tzinfo is None:
        raise ValueError(f"instant {x} has no time zone")
    return d


def _same_query(res, iss, mode, t, h):
    """An F8 result must answer the snapshot's own question: same mode, T and H (first DE-4 audit M-2). A result
    whose H was filled by F8 with its query time would otherwise be stored as of the snapshot's H."""
    if res.issuer_id != iss or res.mode != F8_MODE[mode] or _aware(res.information_cutoff) != t \
            or _aware(res.knowledge_horizon) != h:
        raise ValueError(f"the F8 result for {iss} is not for the snapshot's mode, T and H")


def _value(fv):
    return sc.Value(fv.value, fv.state, fv.reason)


def assemble(*, mode, cutoff, horizon, snapshot_id, rows, results, decisions,
             policy=el.FinancialEligibilityPolicy(), n_min=sc.N_MIN, imported=False):
    """rows: {symbol: MFRow}; results: {issuer_id: AsOfResult}; decisions: [panel.Decision]. imported: the MF
    snapshot read imported history (mf_snapshots.imported_inputs)."""
    if mode not in MF_LABEL:
        raise ValueError(f"an MF snapshot's mode is KNOWN or AVAILABLE, not {mode!r}")
    if (mode == "AVAILABLE") != (horizon is not None):
        raise ValueError("AVAILABLE needs H; KNOWN has none")
    label = MF_LABEL[mode]
    # §4.6: over a reconstructed era (or any snapshot that read imported history) only captured securities can have
    # a link, so every row says its fundamental coverage is captured_only
    coverage = "captured_only" if mode == "AVAILABLE" or imported else "all"
    out = Assembled()
    t, h = _aware(cutoff), _aware(horizon)
    for sym, r in sorted(rows.items()):
        out.links[sym] = pn.security_link(decisions, r.security.company_id, F8_MODE[mode], t, h)
    lines_of = {}
    for sym, lk in out.links.items():
        if lk.state == pn.LINKED:
            lines_of.setdefault(lk.issuer_id, []).append(sym)

    issuer_info, issuer_vals, line_vals, hashes = {}, {}, {}, {}
    for iss in sorted(lines_of):
        res = results.get(iss)
        if res is None:
            raise ValueError(f"no F8 result for linked issuer {iss}")
        _same_query(res, iss, mode, t, h)
        p = pn.panel(res, PIPELINE[mode])
        vals = ft.compute(p)
        lines = [rows[s].security for s in lines_of[iss]]
        vals.update(va.mcap_features(p, lines, ft._cfo_ttm(p, p.anchor_period) if p.state == pn.OK else None,
                                     ft._fcf(p, p.anchor_period) if p.state == pn.OK else None))
        vals = ft.annotate(p, vals, label)
        elig = el.eligibility(p, vals, policy)
        rows_out = [dict(subject=iss, level="issuer", values=vals, eligibility=elig, snapshot_id=snapshot_id,
                         label_class=label, fundamental_coverage=coverage)]
        for s in lines_of[iss]:
            ev = ft.annotate(p, va.eps_features(p, rows[s].security), label)
            line_vals[s] = ev
            rows_out.append(dict(subject=s, level="security", values=ev, eligibility=elig, snapshot_id=snapshot_id,
                                 link_decision_id=out.links[s].decision_id, mf_row_hash=rows[s].row_hash,
                                 label_class=label, fundamental_coverage=coverage))
        out.panels.append(PanelBatch(p, rows_out))
        group = ft.industry(p) if p.state == pn.OK else None
        issuer_info[iss] = {"eligible": elig.result == el.ELIGIBLE, "group": group,
                            "lines": [(s, rows[s].eligibility == MARKET_ELIGIBLE) for s in lines_of[iss]]}
        issuer_vals[iss] = {k: _value(v) for k, v in vals.items()}
        # an issuer engine's inputs: the F8 result and every linked line's MF row (market value reads them)
        hashes[iss] = [p.f8_result_hash] + [rows[s].row_hash for s in lines_of[iss]]
        issuer_info[iss]["f8_result_hash"] = p.f8_result_hash
        issuer_info[iss]["eligibility"] = elig.result

    outputs = []
    for e in rg.ENGINES:
        if e.family == rg.FUNDAMENTAL and e.level == rg.ISSUER:
            outputs += _issuer_engine(e, issuer_info, issuer_vals, hashes, out.links, rows, label, n_min)
        elif e.engine_id == "fund_valuation_eps":
            outputs += _eps_engine(e, issuer_info, line_vals, rows, out.links, label, n_min)
        elif e.family == rg.MARKET:
            members = sc.market_universe({s: r.eligibility == MARKET_ELIGIBLE for s, r in rows.items()})
            res = sc.score(e, {s: rows[s].features for s in members}, group="all",
                           input_hashes={s: [rows[s].row_hash] for s in members},
                           evidence={s: {"mf_row_hash": rows[s].row_hash} for s in members},
                           label_class={s: label for s in members}, n_min=n_min)
            outputs += [(o, None) for _s, o in sorted(res.items())]
            for s in sorted(set(rows) - set(members)):
                outputs.append((_excluded(e, s, "all", f"market_ineligible:{rows[s].eligibility}",
                                          {"mf_row_hash": rows[s].row_hash}, label), None))
    out.run = RunBatch(mode, cutoff, horizon, snapshot_id, label, outputs, coverage)
    return out


def _line_evidence(info, links, rows, sec, subject):
    lk = links[sec]
    return {"f8_result_hash": info[lk.issuer_id]["f8_result_hash"], "link_decision_id": lk.decision_id,
            "mf_row_hash": rows[sec].row_hash, "f7_subject": subject}


def _excluded(e, member, group, reason, evidence, label):
    """A member outside the engine's ranking universe (§7.2) gets an explicit output, never silence (C-0080):
    insufficient_inputs, no score, no components, the reason in its evidence."""
    return sc.EngineOutput(e.engine_id, e.version, member, sc.INSUFFICIENT, None, (), (),
                           {"level": e.level, "group": group, "size": 0, "digest": ""}, "",
                           dict(evidence, excluded=reason), label)


def _unlinked(e, sec, lk, rows, label):
    """A line with no issuer link at T (absent or in conflict) has no fundamental input (§4.6)."""
    return _excluded(e, sec, "", lk.state, {"link_decision_id": lk.decision_id, "mf_row_hash": rows[sec].row_hash},
                     label)


def _issuer_engine(e, info, vals, hashes, links, rows, label, n_min):
    groups = sorted({x["group"] for x in info.values() if x["group"]}) if e.group == "by_issuer_group" else [e.group]
    issuer_out = {}
    for g in groups:
        scoped = {i: x for i, x in info.items() if x["group"] is not None
                  and (e.group != "by_issuer_group" or x["group"] == g)}
        members, na = sc.fundamental_universe(e, scoped)
        res = sc.score(e, {i: vals[i] for i in members}, group=g, input_hashes={i: hashes[i] for i in members},
                       label_class={i: label for i in members}, n_min=n_min)
        issuer_out.update(res)
        for i in na:
            issuer_out.setdefault(i, sc.not_applicable(e, i, g))
    out = []
    for sec, lk in sorted(links.items()):
        if lk.state != pn.LINKED:
            out.append((_unlinked(e, sec, lk, rows, label), None))
            continue
        iss = lk.issuer_id
        ev = _line_evidence(info, links, rows, sec, iss)
        o = issuer_out.get(iss)
        if o is None:
            x = info[iss]
            why = "no_industry_group" if x["group"] is None else (
                f"financially_ineligible:{x['eligibility']}" if not x["eligible"] else "no_market_eligible_line")
            o = _excluded(e, iss, x["group"] or "", why, {}, label)
        from dataclasses import replace
        out.append((replace(o, member=sec, evidence=dict(o.evidence, **ev), label_class=label), iss))
    return out


def _eps_engine(e, info, line_vals, rows, links, label, n_min):
    out, ranked = [], set()
    for g, members in sorted(sc.valuation_eps_universe(info).items()):
        if not g:
            continue
        res = sc.score(e, {s: {k: _value(v) for k, v in line_vals[s].items()} for s in members}, group=g,
                       input_hashes={s: [rows[s].row_hash, info[links[s].issuer_id]["f8_result_hash"]]
                                     for s in members},
                       evidence={s: _line_evidence(info, links, rows, s, s) for s in members},
                       label_class={s: label for s in members}, n_min=n_min)
        out += [(o, links[s].issuer_id) for s, o in sorted(res.items())]
        ranked.update(members)
    for sec, lk in sorted(links.items()):
        if lk.state != pn.LINKED:
            out.append((_unlinked(e, sec, lk, rows, label), None))
        elif sec not in ranked:
            x = info[lk.issuer_id]
            why = ("no_industry_group" if x["group"] is None else f"financially_ineligible:{x['eligibility']}"
                   if not x["eligible"] else f"market_ineligible:{rows[sec].eligibility}")
            out.append((_excluded(e, sec, x["group"] or "", why, _line_evidence(info, links, rows, sec, sec), label),
                        lk.issuer_id))
    return out
