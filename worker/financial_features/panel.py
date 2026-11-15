"""
F7-1: the fundamental panel as of T (Phase 4 design §4). Pure: it reads one F8 AsOfResult and the issuer-link
decisions, and decides which facts are usable, the scope, the newest and anchor period, the currency, the anchor's
duration and the panel state. No database, no clock, no network.

Usability of a fact (§4.2)                     `usability(fact)`
Scope, anchor and currency (§4.3, §4.4)        `panel(result, pipeline=...)`
Sign (§4.5, f7.sign.2)                          `signed(concept, value)`
The security-to-issuer link (§4.6)             `security_link(decisions, mode, cutoff, horizon)`
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from worker.financial_asof import AVAILABLE, KNOWN, KNOWN_RECORDED, Refused, require_live, require_point_in_time
from worker.financial_asof.result import AMBIGUOUS, METADATA_TIE, NONE, NOT_YET_AVAILABLE

from .dates import months_before

SCOPE_POLICY, CURRENCY_POLICY, SIGN_POLICY, LINK_POLICY = "f7.scope.1", "f7.currency.1", "f7.sign.2", \
    "f7.security_link.1"
ANCHOR_CONCEPT, ANCHOR_OPERATIONS = "profit_for_period", "total_or_unstated"
SCOPE_ORDER = ("group", "bank", "company")
MAGNITUDE_CONCEPTS = frozenset({"cost_of_sales", "finance_costs", "interest_expense", "purchase_of_ppe",
                                "dividends_paid"})

# value states (Phase 3 §10.4 order: the state describes the value; missing, conflicting and untrusted are null)
FRESH, MISSING, CONFLICTING, UNTRUSTED = "fresh", "missing", "conflicting", "untrusted"
# panel states
OK, ANCHOR_ABSENT, SCOPE_UNAVAILABLE = "ok", "anchor_absent", "scope_unavailable"
CRITICAL_CONFLICT, ANCHOR_UNUSABLE, CURRENCY_AMBIGUOUS = "critical_conflict", "anchor_unusable", "currency_ambiguous"
# link states
LINKED, LINK_CONFLICT, LINK_ABSENT = "linked", "issuer_link_conflict", "issuer_link_absent"

RECORDED_AVAILABILITY_FLAGS = ("availability_sources_disagree", "availability_evidence_changed",
                               "last_modified_after_upload")
RECORDED_ANNOTATIONS = ("restated_comparative_present", "internal_conflict", "sign_only_difference")
PIPELINES = ("live", "point_in_time")


@dataclass(frozen=True)
class Usable:
    """What F7 may take from one fact (§4.2). `value` is the representative normalised value, or None with
    `nil=True` for a printed dash. `state` is FRESH for a usable number or nil, else the null state with `reason`."""
    ef_key: str
    state: str
    value: Optional[Decimal] = None
    low: Optional[Decimal] = None
    high: Optional[Decimal] = None
    nil: bool = False
    reason: Optional[str] = None
    recorded: tuple = ()                 # flags and annotations recorded beside the value

    @property
    def number(self):
        return self.state == FRESH and not self.nil and self.value is not None


def _availability_flags(fact):
    out = set()
    for o in fact.visible:
        out.update(f.split(":")[0] for f in o.flags)
        out.update(o.availability.flags)
    return out


def usability(fact):
    """§4.2's table, for one F8 FactView."""
    flags = set(fact.flags)
    recorded = set()
    for f in (AMBIGUOUS, METADATA_TIE):
        if f in flags:
            return Usable(fact.ef_key, UNTRUSTED, reason=f)
    if fact.state == "conflicting":
        return Usable(fact.ef_key, CONFLICTING, reason="conflicting")
    if fact.state == NONE:
        reasons = {r for r, _n in fact.counts}
        if NOT_YET_AVAILABLE in reasons or any(e.reason == NOT_YET_AVAILABLE for e in fact.excluded):
            return Usable(fact.ef_key, MISSING, reason=NOT_YET_AVAILABLE)
        return Usable(fact.ef_key, MISSING, reason="absent")
    annotations = set(fact.f6_result.annotations) if fact.f6_result is not None else set()
    if "representative_ambiguous" in annotations:
        return Usable(fact.ef_key, MISSING, reason="representative_ambiguous")
    recorded.update(a for a in annotations if a in RECORDED_ANNOTATIONS)
    avail = _availability_flags(fact)
    recorded.update(f for f in avail if f in RECORDED_AVAILABILITY_FLAGS or f == "available_after_known")
    if fact.value_kind == "nil":
        return Usable(fact.ef_key, FRESH, nil=True, recorded=tuple(sorted(recorded)))
    if fact.representative_normalized_value is None:
        return Usable(fact.ef_key, MISSING, reason="absent")
    return Usable(fact.ef_key, FRESH, value=fact.representative_normalized_value, low=fact.interval_low,
                  high=fact.interval_high, recorded=tuple(sorted(recorded)))


def signed(concept, value):
    """f7.sign.2: (value used, sign rule). The magnitude only for the gross-label concepts; every other concept is
    used with its printed sign, unchanged (impairment and tax charges/(reversals) included)."""
    if value is None:
        return None, None
    if concept in MAGNITUDE_CONCEPTS:
        return abs(value), "magnitude"
    return value, "as_printed"


@dataclass(frozen=True)
class Panel:
    issuer_id: str
    mode: str
    f8_label: str
    information_cutoff: Optional[str]
    knowledge_horizon: Optional[str]
    f8_configuration_id: str
    f8_result_hash: str
    state: str
    scope: Optional[str] = None
    currency: Optional[str] = None
    newest_period: Optional[date] = None
    anchor_period: Optional[date] = None
    anchor_months: Optional[int] = None
    flags: tuple = ()                    # scope_changed, has_discontinued, unlabelled_scope
    facts: dict = field(default_factory=dict, repr=False)    # (concept, period_end, months, operations) -> Usable
    counts: tuple = ()                   # (fact state, number), over the whole result

    def fact(self, concept, period_end, months=None, operations=ANCHOR_OPERATIONS):
        return self.facts.get((concept, period_end, months, operations))


def _guard(result, pipeline):
    if pipeline not in PIPELINES:
        raise ValueError(f"pipeline must be one of {PIPELINES}")
    if pipeline == "live":
        return require_live(result)
    result = require_point_in_time(result)
    if result.mode == AVAILABLE and (result.knowledge_horizon is None or not result.f8_configuration_id):
        raise Refused("horizon_required", "an AVAILABLE panel needs its knowledge horizon and configuration id")
    return result


def _anchor_facts(facts):
    return [f for f in facts if f.identity is not None and f.identity.concept_key == ANCHOR_CONCEPT
            and f.identity.operations == ANCHOR_OPERATIONS]


def panel(result, pipeline):
    """The panel as of the result's T (§4.1 to §4.4). `pipeline` is "live" (require_live: KNOWN or KNOWN_RECORDED)
    or "point_in_time" (require_point_in_time; AVAILABLE must carry H and the F8 configuration id)."""
    result = _guard(result, pipeline)
    facts = [f for f in result.facts if f.identity is not None]
    counts = {}
    for f in result.facts:
        counts[f.state] = counts.get(f.state, 0) + 1
    base = dict(issuer_id=result.issuer_id, mode=result.mode, f8_label=result.label,
                information_cutoff=result.information_cutoff, knowledge_horizon=result.knowledge_horizon,
                f8_configuration_id=result.f8_configuration_id, f8_result_hash=result.result_hash,
                counts=tuple(sorted(counts.items())))
    anchors = _anchor_facts(facts)
    # §4.3: the newest period counts only facts F8 shows (a held but not yet public document is `none`: FL-19)
    shown = [f for f in anchors if f.state in ("single_source", "corroborated", "conflicting")]
    if not shown:
        return Panel(state=ANCHOR_ABSENT, **base)
    newest = max(f.identity.period_end for f in shown)
    at_newest = [f for f in anchors if f.identity.period_end == newest]
    scopes = {f.identity.scope for f in at_newest}
    all_scopes = {f.identity.scope for f in facts}
    scope = next((s for s in SCOPE_ORDER if s in scopes), None)
    flags = set()
    if scope is None:
        if scopes == {"unlabelled"} and all_scopes == {"unlabelled"}:
            scope = "unlabelled"
            flags.add("unlabelled_scope")
        else:
            return Panel(state=SCOPE_UNAVAILABLE, newest_period=newest, **base)
    in_scope = [f for f in at_newest if f.identity.scope == scope]
    currencies = {f.identity.currency for f in in_scope}
    if "LKR" in currencies:
        currency = "LKR"
    elif len(currencies) == 1:
        currency = next(iter(currencies))
    else:
        return Panel(state=CURRENCY_AMBIGUOUS, scope=scope, newest_period=newest, flags=tuple(sorted(flags)), **base)
    durations = [f.identity.duration_months for f in in_scope
                 if f.identity.currency == currency and f.identity.duration_months <= 12]
    months = max(durations) if durations else None
    table = {}
    for f in facts:
        i = f.identity
        if i.scope == scope and i.currency == currency:
            table[(i.concept_key, i.period_end, i.duration_months, i.operations)] = usability(f)
    out = dict(scope=scope, currency=currency, newest_period=newest, anchor_period=newest, anchor_months=months,
               facts=table)
    anchor = table.get((ANCHOR_CONCEPT, newest, months, ANCHOR_OPERATIONS)) if months else None
    if anchor is None or not anchor.number:
        state = CRITICAL_CONFLICT if anchor is not None and anchor.state == CONFLICTING else ANCHOR_UNUSABLE
        return Panel(state=state, flags=tuple(sorted(flags)), **out, **base)
    # scope_changed (§4.3): the chosen scope has no anchor fact twelve months before, while another scope has one
    prior = months_before(newest, 12)
    prior_scopes = {f.identity.scope for f in anchors if f.identity.period_end == prior}
    if prior_scopes and scope not in prior_scopes:
        flags.add("scope_changed")
    if any(f.identity.operations == "discontinued" and f.identity.period_end == newest
           and f.identity.scope == scope for f in facts):
        flags.add("has_discontinued")
    return Panel(state=OK, flags=tuple(sorted(flags)), **out, **base)


@dataclass(frozen=True)
class Decision:
    """A row of issuer_securities (0007)."""
    id: int
    company_id: str
    issuer_id: Optional[str]
    link_status: str                     # evidenced | conflict
    decided_at: object                   # aware datetime


@dataclass(frozen=True)
class Link:
    state: str
    issuer_id: Optional[str] = None
    decision_id: Optional[int] = None
    reconstructed: bool = False


def security_link(decisions, company_id, mode, cutoff, horizon=None):
    """f7.security_link.1: the highest-id decision for the security with decided_at <= T (KNOWN, KNOWN_RECORDED) or
    <= H (AVAILABLE: a link is the system's own conclusion with no publication time; labelled reconstructed)."""
    if company_id is None:
        return Link(LINK_ABSENT)
    if mode in (KNOWN, KNOWN_RECORDED):
        bound, rec = cutoff, False
    elif mode == AVAILABLE:
        if horizon is None:
            raise Refused("horizon_required", "an AVAILABLE link needs the knowledge horizon")
        bound, rec = horizon, True
    else:
        raise Refused("current_not_point_in_time", "a CURRENT link is never a point-in-time input")
    seen = [d for d in decisions if d.company_id == company_id and d.decided_at <= bound]
    if not seen:
        return Link(LINK_ABSENT, reconstructed=rec)
    d = max(seen, key=lambda x: x.id)
    if d.link_status == "evidenced" and d.issuer_id:
        return Link(LINKED, d.issuer_id, d.id, rec)
    return Link(LINK_CONFLICT, None, d.id, rec)
