"""
§11.2: the validated domain recorded with a model, and the refusal table. The same logic runs in validation, in the
test and in prediction (MLK-15), so a model that only predicts on quiet days is seen to do so.

A domain: the label classes of its validation rows; the eligibility policy version; the versions of the features in
its schema; its horizon; the industry groups present; the per-feature envelope (the 0.1st to 99.9th percentile of the
fit slice, widened by the policy's margin) and the features that are critical or rank-based; its training cutoff;
`max_model_age_sessions`.

The checks, in this order (the first that applies decides):
  invalid_input            the row's inputs failed their hash checks
  not_applicable           no designation for the purpose
  insufficient_data        not market-data eligible; no combination in the list has all its components; a critical
                           feature null or inadmissible (`missing_critical_features`)
  blocked                  a critical non-rank feature outside the envelope (`outside_validated_domain`); the
                           eligibility policy version, or the version of a feature IN THE SCHEMA, differs
                           (`version_changed`; an unrelated feature added to the registry does not block); the
                           row's label class or industry group not in the domain and no waiver (`outside_domain`;
                           the C-0039 waiver: a model validated on reconstructed evidence may predict point_in_time
                           rows, flagged `reconstructed_evidence`); older than max_model_age_sessions
                           (`model_stale`); an incident flag that covers T AND was recorded at or before T
                           (`incident`: a flag entered later does not remove a day from validation with hindsight)
  predicted                otherwise; non-critical features are clipped to the fit-slice bounds
"""
import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Domain:
    label_classes: tuple
    eligibility_policy: str
    feature_versions: dict                       # {feature: version} of the schema's features
    horizon: int
    industry_groups: tuple
    envelope: dict                               # {feature: (lo, hi)} (already widened)
    bounds: dict                                 # {feature: (lo, hi)} the fit slice's own min and max (clipping)
    critical: tuple
    rank_features: tuple = ()
    max_age_sessions: int = 252
    reconstructed_waiver: bool = False           # C-0039: reconstructed evidence may predict point_in_time rows


def _q(sorted_vals, q):
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def envelope_of(values, margin):
    """(0.1st percentile, 99.9th percentile) of the fit slice's non-null values, widened by `margin` times its
    width on each side; and (min, max) for clipping."""
    v = sorted(x for x in values if x is not None and math.isfinite(x))
    if not v:
        return None, None
    lo, hi = _q(v, 0.001), _q(v, 0.999)
    w = hi - lo
    return (lo - margin * w, hi + margin * w), (v[0], v[-1])


@dataclass(frozen=True)
class Row:
    features: dict                               # {feature: value or None}
    admissible: dict = field(default_factory=dict)   # {feature: False} for an inadmissible value
    label_class: str = "point_in_time"
    industry_group: str = None
    eligible: bool = True
    eligibility_policy: str = ""
    feature_versions: dict = field(default_factory=dict)
    hashes_ok: bool = True


def decide(row, domain, *, designated, routed, age_sessions, incidents, cutoff):
    """(state, reason, flags, clipped features). incidents: [(covers_from, covers_to, recorded_at)]."""
    if not row.hashes_ok:
        return "invalid_input", "hash_mismatch", (), None
    if not designated:
        return "not_applicable", "no_designation", (), None
    if not row.eligible:
        return "insufficient_data", "not_market_data_eligible", (), None
    if not routed:
        return "insufficient_data", "no_combination_with_all_components", (), None
    for f in domain.critical:
        if row.features.get(f) is None or row.admissible.get(f) is False:
            return "insufficient_data", "missing_critical_features", (), None
    for f in domain.critical:
        if f in domain.rank_features or f not in domain.envelope or domain.envelope[f] is None:
            continue
        lo, hi = domain.envelope[f]
        if not lo <= row.features[f] <= hi:
            return "blocked", "outside_validated_domain", (), None
    if row.eligibility_policy != domain.eligibility_policy or any(
            row.feature_versions.get(f) != v for f, v in domain.feature_versions.items()):
        return "blocked", "version_changed", (), None
    flags = []
    if row.label_class not in domain.label_classes:
        if domain.reconstructed_waiver and row.label_class == "point_in_time" and \
                "reconstructed" in domain.label_classes:
            flags.append("reconstructed_evidence")
        else:
            return "blocked", "outside_domain", (), None
    if domain.industry_groups and row.industry_group not in domain.industry_groups:
        return "blocked", "outside_domain", (), None
    if age_sessions > domain.max_age_sessions:
        return "blocked", "model_stale", (), None
    for a, b, recorded in incidents:
        if a <= cutoff <= b and recorded <= cutoff:
            return "blocked", "incident", (), None
    clipped = {}
    for f, v in row.features.items():
        if f in domain.critical or v is None or f not in domain.bounds or domain.bounds[f] is None:
            clipped[f] = v
            continue
        lo, hi = domain.bounds[f]
        clipped[f] = min(max(v, lo), hi)
    return "predicted", None, tuple(flags), clipped
