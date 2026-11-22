"""
The content-addressed MF configuration (Phase 3 §13.1): every parameter a snapshot or a target build depends on, as
canonical JSON whose SHA-256 is `mf_configurations.configuration_id`. A stored result names its configuration, so
`verify` can rebuild it with exactly the same parameters, and refuses one whose rule versions this code does not
implement.
"""
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from fractions import Fraction

from worker.market_features import VERSION, actions, features, targets, universe
from worker.market_features.canonical import canonical_json, sha256_hex

from . import snapshot as sn

AVAILABILITY_POLICY = "end_of_colombo_day.1"     # an imported bar is available at the end of its Colombo day
UNIVERSE_RULE = "row_absent_security_complete.1"  # §5.4, revision 6 and C-0063


class ConfigurationError(ValueError):
    pass


# MF-8 (branch review B1): the captured path's parameters, pinned here because P2 reads its own from the mutable
# system_config, which MF does not follow (§5.1 step 3). Provisional values (§17).
DEFAULT_TOLERANCES = (("cross_check_closing_price_tolerance_pct", Decimal("0.1")),
                      ("cross_check_last_traded_price_tolerance_pct", Decimal("0.1")),
                      ("cross_check_market_cap_tolerance_pct", Decimal("0.5")),
                      ("cross_check_turnover_tolerance_pct", Decimal("0.5")),
                      ("reconciliation_price_tolerance_pct", Decimal("0.1")),
                      ("reconciliation_turnover_tolerance_pct", Decimal("0.5")),
                      ("reconciliation_volume_tolerance", Decimal("0")))


@dataclass(frozen=True)
class CaptureConfig:
    min_coverage: Decimal = Decimal("0.95")             # §4.3: a final session's coverage, provisional
    tolerances: tuple = DEFAULT_TOLERANCES              # §5.1 (reconcile), §5.2 (MFB-8), §5.4 (captured vs imported)

    def tolerance_dict(self):
        """As `reconcile` and `bars` read them: reconcile's own keys as floats (it compares floats), MF's as
        Decimals."""
        return {k: (float(v) if k.startswith("reconciliation_") else v) for k, v in self.tolerances}


@dataclass(frozen=True)
class Configuration:
    policy: universe.EligibilityPolicy = field(default_factory=universe.EligibilityPolicy)
    feature_config: features.FeatureConfig = field(default_factory=features.FeatureConfig)
    detector_threshold: Fraction = Fraction(1, 4)       # provisional (§7.3, §17)
    target_policy: targets.TargetPolicy = field(default_factory=targets.TargetPolicy)
    capture: CaptureConfig = field(default_factory=CaptureConfig)

    def body(self):
        pol = {k: str(v) for k, v in asdict(self.policy).items()}
        return {"code_version": VERSION, "feature_registry_version": features.REGISTRY_VERSION,
                "adjustment_policy": actions.ADJUSTMENT_POLICY, "availability_policy": AVAILABILITY_POLICY,
                "universe_rule": UNIVERSE_RULE, "market_lookback": sn.MARKET_LOOKBACK,
                "eligibility_policy": pol, "eligibility_policy_status": self.policy.status,
                "feature_config": {k: str(v) for k, v in asdict(self.feature_config).items()},
                "detector_threshold": str(self.detector_threshold),
                "target_definition_version": targets.TARGET_DEFINITION_VERSION,
                "target_policy": {k: str(v) for k, v in asdict(self.target_policy).items()},
                "capture": {"min_coverage": str(self.capture.min_coverage),
                            "tolerances": {k: str(v) for k, v in self.capture.tolerances}}}

    def json(self):
        return canonical_json(self.body())

    def id(self):
        return sha256_hex(self.json())


PINNED = ("code_version", "feature_registry_version", "adjustment_policy", "availability_policy", "universe_rule",
          "market_lookback", "target_definition_version")


def _capture(b):
    c = b.get("capture")
    if not isinstance(c, dict):
        raise ConfigurationError("configuration has no capture parameters (written before MF-8): register it again")
    tol = c.get("tolerances")
    if not isinstance(tol, dict) or set(tol) != {k for k, _v in DEFAULT_TOLERANCES}:      # MF-8 audit F7
        raise ConfigurationError("the capture block names exactly the tolerances this code reads")
    cov = Decimal(c["min_coverage"])
    if not Decimal(0) < cov <= Decimal(1):
        raise ConfigurationError("min_coverage is in (0, 1]")
    return CaptureConfig(cov, tuple(sorted((k, Decimal(v)) for k, v in tol.items())))


def parse(configuration_json):
    """The Configuration a stored JSON names. Refused if a rule version differs from this code's."""
    import json
    b = json.loads(configuration_json)
    mine = Configuration().body()
    wrong = [k for k in PINNED if b.get(k) != mine[k]]
    if wrong:
        raise ConfigurationError(f"configuration names rule versions this code does not implement: {wrong}")
    p = b["eligibility_policy"]
    policy = universe.EligibilityPolicy(
        version=p["version"], status=p["status"], min_history_sessions=int(p["min_history_sessions"]),
        activity_window=int(p["activity_window"]), min_traded_ratio=Fraction(p["min_traded_ratio"]),
        min_median_turnover_lkr=Decimal(p["min_median_turnover_lkr"]),
        max_stale_sessions=int(p["max_stale_sessions"]), quality_block_window=int(p["quality_block_window"]))
    fc = b["feature_config"]
    cfg = Configuration(policy, features.FeatureConfig(max_stale_sessions=int(fc["max_stale_sessions"]),
                                                       min_constituents=int(fc["min_constituents"])),
                        Fraction(b["detector_threshold"]),
                        targets.TargetPolicy(grace_sessions=int(b["target_policy"]["grace_sessions"])),
                        _capture(b))
    if cfg.json() != configuration_json:
        raise ConfigurationError("configuration JSON does not round-trip (not written by this code)")
    return cfg


def register(conn, cfg):
    """Insert the configuration if absent (worker). Returns (state, id)."""
    cid = cfg.id()
    with conn.cursor() as cur:
        cur.execute("select 1 from mf_configurations where configuration_id = %s", (cid,))
        if cur.fetchone():
            conn.rollback()
            return "present", cid
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, cfg.json(), cfg.policy.status))
    conn.commit()
    return "registered", cid


def load(cur, configuration_id):
    cur.execute("select configuration_json from mf_configurations where configuration_id = %s", (configuration_id,))
    row = cur.fetchone()
    if row is None:
        raise ConfigurationError(f"no configuration {configuration_id}")
    return parse(row[0])



