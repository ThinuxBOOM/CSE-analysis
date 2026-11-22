"""
F7-4: financial eligibility of an issuer (Phase 4 design §11; MA §6's second step). A pure function of the panel,
its features and a versioned policy, returning one result, every failed criterion and the measured values.
"""
from dataclasses import dataclass
from datetime import datetime

from . import panel as pn
from .features import SPECS

ELIGIBLE, NO_PANEL, SCOPE_UNAVAILABLE, CURRENCY_AMBIGUOUS = "eligible", "no_panel", "scope_unavailable", \
    "currency_ambiguous"
ANCHOR_UNUSABLE, CRITICAL_CONFLICT, INSUFFICIENT_HISTORY = "anchor_unusable", "critical_conflict", \
    "insufficient_history"
STALE, INSUFFICIENT_FEATURES = "stale", "insufficient_features"
# the order in which a failed criterion is reported as the result (§11's list)
PRECEDENCE = (NO_PANEL, SCOPE_UNAVAILABLE, CURRENCY_AMBIGUOUS, ANCHOR_UNUSABLE, CRITICAL_CONFLICT,
              INSUFFICIENT_HISTORY, STALE, INSUFFICIENT_FEATURES)
PANEL_RESULT = {pn.ANCHOR_ABSENT: NO_PANEL, pn.SCOPE_UNAVAILABLE: SCOPE_UNAVAILABLE,
                pn.CURRENCY_AMBIGUOUS: CURRENCY_AMBIGUOUS, pn.ANCHOR_UNUSABLE: ANCHOR_UNUSABLE,
                pn.CRITICAL_CONFLICT: CRITICAL_CONFLICT}
NON_NULL = (pn.FRESH, "partial", "stale")


@dataclass(frozen=True)
class FinancialEligibilityPolicy:
    version: str = "financial_eligibility_policy.0"
    status: str = "provisional"
    min_fiscal_years: int = 2
    max_anchor_age_days: int = 270


@dataclass(frozen=True)
class Eligibility:
    result: str
    failed: tuple
    fiscal_years: int = 0
    anchor_age_days: object = None
    growth_features: tuple = ()
    return_features: tuple = ()
    policy_version: str = ""


def _cutoff_date(p):
    if p.information_cutoff is None:
        return None
    return datetime.fromisoformat(p.information_cutoff).date()


def eligibility(p, features, policy=FinancialEligibilityPolicy()):
    """§11 for one issuer's panel and its features ({feature_id: FeatureValue})."""
    failed = set()
    if p.state in PANEL_RESULT:
        failed.add(PANEL_RESULT[p.state])
        return Eligibility(next(x for x in PRECEDENCE if x in failed), tuple(x for x in PRECEDENCE if x in failed),
                           policy_version=policy.version)
    for concept in ("total_assets", "total_equity"):
        u = p.fact(concept, p.anchor_period, None)
        if u is not None and u.state == pn.CONFLICTING:
            failed.add(CRITICAL_CONFLICT)
    years = sorted({k[1] for k, u in p.facts.items() if k[0] == pn.ANCHOR_CONCEPT and k[2] == 12
                    and k[3] == pn.ANCHOR_OPERATIONS and u.number})
    if len(years) < policy.min_fiscal_years:
        failed.add(INSUFFICIENT_HISTORY)
    t = _cutoff_date(p)
    age = None if t is None else (t - p.anchor_period).days
    if age is None or age > policy.max_anchor_age_days:
        failed.add(STALE)
    fam = {s.feature_id: s.family for s in SPECS}
    growth = tuple(sorted(k for k, v in features.items() if fam.get(k) == "growth" and v.state in NON_NULL
                          and v.value is not None))
    returns = tuple(sorted(k for k, v in features.items() if fam.get(k) == "returns" and v.state in NON_NULL
                           and v.value is not None))
    if not growth or not returns:
        failed.add(INSUFFICIENT_FEATURES)
    ordered = tuple(x for x in PRECEDENCE if x in failed)
    return Eligibility(ordered[0] if ordered else ELIGIBLE, ordered, len(years), age, growth, returns,
                       policy.version)
