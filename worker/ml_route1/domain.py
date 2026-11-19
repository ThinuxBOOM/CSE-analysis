"""
§11.2: the validated domain recorded with a model, and the refusal table. The same logic runs in validation, in the
test and in prediction (MLK-15), so a model that only predicts on quiet days is seen to do so.

A domain: the label classes of its validation rows; the eligibility policy version; the versions of the features in
its schema; its horizon; the industry groups present; the per-feature envelope (the 0.1st to 99.9th percentile of the
fit slice, widened by the policy's margin) and the features that are critical or rank-based; its training cutoff;
`max_model_age_sessions`.

A domain consulted at another horizon is a caller's error and raises. The domain fails closed (M5-7 audit M3): it cannot be built unless every critical non-rank feature has an envelope,
and an empty list of industry groups admits no row.

The checks, in this order (the first that applies decides):
  invalid_input            the row's inputs failed their hash checks
  not_applicable           no designation for the purpose
  insufficient_data        not market-data eligible; no combination in the list has all its components; a critical
                           feature null or inadmissible (`missing_critical_features`)
  blocked                  the eligibility policy version, or the version of a
                           feature IN THE SCHEMA, differs (`version_changed`; an unrelated feature added to the
                           registry does not block; checked before the envelope, so a changed feature is reported as
                           changed); a critical non-rank feature outside the envelope (`outside_validated_domain`);
                           the row's label class or industry group not in the domain and no waiver (`outside_domain`;
                           the C-0039 waiver: a model validated on reconstructed evidence may predict point_in_time
                           rows, flagged `reconstructed_evidence`); older than max_model_age_sessions
                           (`model_stale`); an incident flag that covers T AND was recorded at or before T
                           (`incident`: a flag entered later does not remove a day from validation with hindsight,
                           but the row is flagged `incident_recorded_later`, §11.2's reported stratum). A flag's
                           range is half-open [from, to), as `ml_incident_flags.covers` is
  predicted                otherwise; non-critical features are clipped to the fit-slice bounds

`refusal_rates` summarises the decisions of a validation or test period by regime (MLK-15: the same logic, the
rates reported).
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
    training_cutoff: object = None               # C_train of the model (§11.2), recorded with it

    def __post_init__(self):
        for f in self.critical:
            if f in self.rank_features:
                continue
            env = self.envelope.get(f)
            if env is None or env[0] is None or env[1] is None or not env[0] <= env[1]:
                raise ValueError(f"a domain needs an envelope for every critical non-rank feature: {f} has none")


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


def decide(row, domain, *, designated, routed, age_sessions, incidents, cutoff, horizon):
    """(state, reason, flags, clipped features). incidents: [(covers_from, covers_to, recorded_at)], each range
    half-open [covers_from, covers_to)."""
    if horizon != domain.horizon:
        raise ValueError(f"a domain for h = {domain.horizon} consulted at h = {horizon}")
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
    if row.eligibility_policy != domain.eligibility_policy or any(
            row.feature_versions.get(f) != v for f, v in domain.feature_versions.items()):
        return "blocked", "version_changed", (), None
    for f in domain.critical:
        if f in domain.rank_features:
            continue
        lo, hi = domain.envelope[f]
        if not lo <= row.features[f] <= hi:
            return "blocked", "outside_validated_domain", (), None
    flags = []
    if row.label_class not in domain.label_classes:
        if domain.reconstructed_waiver and row.label_class == "point_in_time" and \
                "reconstructed" in domain.label_classes:
            flags.append("reconstructed_evidence")
        else:
            return "blocked", "outside_domain", (), None
    if row.industry_group not in domain.industry_groups:
        return "blocked", "outside_domain", (), None
    if age_sessions > domain.max_age_sessions:
        return "blocked", "model_stale", (), None
    for a, b, recorded in incidents:
        if a <= cutoff < b:
            if recorded <= cutoff:
                return "blocked", "incident", (), None
            flags.append("incident_recorded_later")
    clipped = {}
    for f, v in row.features.items():
        if f in domain.critical or v is None or f not in domain.bounds or domain.bounds[f] is None:
            clipped[f] = v
            continue
        lo, hi = domain.bounds[f]
        clipped[f] = min(max(v, lo), hi)
    return "predicted", None, tuple(flags), clipped


def refusal_rates(decisions, regimes):
    """decisions: [(state, reason, flags)] of a period's rows; regimes: the regime of each row. Returns
    {regime: {"rows": n, "predicted": share, reason: share, ..., "incident_recorded_later": share}}."""
    out = {}
    for (state, reason, flags), g in zip(decisions, regimes):
        d = out.setdefault(g, {"rows": 0})
        d["rows"] += 1
        for k in [reason or state] + [f for f in flags if f == "incident_recorded_later"]:
            d[k] = d.get(k, 0) + 1
    return {g: {k: (v if k == "rows" else v / d["rows"]) for k, v in d.items()} for g, d in out.items()}
