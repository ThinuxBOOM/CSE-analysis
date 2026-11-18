"""
§9.3: the gates evaluated in application code. Each evaluation returns a report (every gate with its result, reason
and evidence) that the store writes to ml_gate_reports; migration 0021 refuses `validated` without a passing
validation report, and a production first event without a passing promotion report. Gate 1 (not synthetic), the
lifecycle and the clock order are the database's.

candidate -> validated (evidence about a configuration): gates 2 to 8.
promotion to production (evidence about the final-fit artifact; the owner's act): gates 9 to 12.
"""
from dataclasses import dataclass, field
from typing import Optional

from .sufficiency import SUFFICIENT

VALIDATION, PROMOTION = "validation", "promotion"
LABELS = ("survivorship_waived", "retrospective", "previously_scored", "rollforward_of", "no_engine_component",
          "reconstructed_evidence")


@dataclass(frozen=True)
class FoldEvidence:
    fold_no: int
    has_metrics: bool
    reliability_ok: Optional[bool]          # None: the output has no probabilities
    coverage_ok: Optional[bool]             # None: the output has no interval; per class, as calibration reports it
    refusal_rates_reported: bool


@dataclass(frozen=True)
class ValidationEvidence:
    protocol_sufficiency: str               # for this horizon (sufficiency.check)
    declared_before_first_run: bool         # the database enforces it; restated for the report
    in_family: bool
    leakage_suite_passed: bool
    code_version: str
    folds: tuple                            # FoldEvidence, every fold of the protocol
    declared_folds: int
    baseline_results: dict                  # {baseline id: object with .state and .rejects} (family_test, §9.3)
    required_baselines: tuple               # every baseline that fills the output, plus every gated
                                            # calibrated_engine for a no_engine_component combination
    size_shown: bool                        # MLK-23 at the protocol's own dependence: inside its acceptance region
    policy_status: dict                     # {policy id: provisional | validated}
    survivorship: str                       # the dataset's
    survivorship_waiver: Optional[int]      # an ml_waivers id covering it
    domain_recorded: bool
    ages_evaluated: tuple                   # (min, max) sessions since C_train with evaluated rows
    multiplicity: int                       # the m used: max(declared family, the registry's count; §5.4)
    no_engine_component: bool = False


def _g(ok, reason, **evidence):
    return {"pass": bool(ok), "reason": None if ok else reason, "evidence": evidence}


def validation_report(e: ValidationEvidence):
    """Gates 2 to 8. Returns {"kind", "gates": {n: {...}}, "passed", "labels", "multiplicity"}."""
    g = {}
    g[2] = _g(e.protocol_sufficiency == SUFFICIENT and e.declared_before_first_run and e.in_family,
              "protocol not sufficient for this horizon" if e.protocol_sufficiency != SUFFICIENT else
              "protocol declared after a run" if not e.declared_before_first_run else "not in the declared family",
              sufficiency=e.protocol_sufficiency)
    g[3] = _g(e.leakage_suite_passed, "the leakage suite has not passed on this code version",
              code_version=e.code_version)
    numbers = sorted(f.fold_no for f in e.folds)
    every = numbers == list(range(1, e.declared_folds + 1))
    bad = [f.fold_no for f in e.folds if not f.has_metrics or f.reliability_ok is False or f.coverage_ok is False
           or not f.refusal_rates_reported]
    g[4] = _g(every and not bad, "a fold is missing" if not every else f"folds {bad}: metrics, reliability, coverage "
                                                                     f"or refusal rates not as required",
              folds=numbers)
    missing = [b for b in e.required_baselines if b not in e.baseline_results]
    failed = [b for b in e.required_baselines if b in e.baseline_results and not (
        e.baseline_results[b].state == "tested" and e.baseline_results[b].rejects)]
    if not e.size_shown:
        g[5] = _g(False, "size_not_shown")
    else:
        g[5] = _g(bool(e.required_baselines) and not missing and not failed,
                  "no baseline" if not e.required_baselines else f"not tested against {missing}" if missing
                  else f"does not beat {failed}", baselines=list(e.required_baselines))
    prov = sorted(k for k, v in e.policy_status.items() if v != "validated")
    g[6] = _g(not prov, f"provisional policies: {prov}")
    waived = e.survivorship != "complete" and e.survivorship_waiver is not None
    g[7] = _g(e.survivorship == "complete" or waived, f"survivorship {e.survivorship} without a waiver",
              waiver=e.survivorship_waiver)
    g[8] = _g(e.domain_recorded and e.ages_evaluated and e.ages_evaluated[1] >= e.ages_evaluated[0] >= 0,
              "the validated domain or the ages evaluated are not recorded", ages=list(e.ages_evaluated or ()))
    labels = (["survivorship_waived"] if waived else []) + (["no_engine_component"] if e.no_engine_component else [])
    return {"kind": VALIDATION, "gates": g, "passed": all(x["pass"] for x in g.values()), "labels": labels,
            "multiplicity": max(1, e.multiplicity)}


@dataclass(frozen=True)
class PromotionEvidence:
    access_done: bool
    nominee_passed: bool
    backtest_valid: bool                    # Phase 10, valid by MA §62
    backtest_ends_at_last_v_end_or_post_access: bool
    point_in_time_share: float
    declared_point_in_time_share: float
    reconstructed_waiver: Optional[int]
    model_labels: tuple                     # every label the model carries
    designation_labels: tuple               # what the designation cites
    designation_cites_access_count: bool
    lifetime_test_accesses: int
    multiplicity: int = 1
    extra: dict = field(default_factory=dict)


def promotion_report(e: PromotionEvidence):
    """Gates 9 to 12 (the owner's promotion command evaluates them; 0021 needs the passing report)."""
    g = {}
    g[9] = _g(e.access_done and e.nominee_passed, "no test access" if not e.access_done else
              "the nominee did not pass the test criterion")
    g[10] = _g(e.backtest_valid and e.backtest_ends_at_last_v_end_or_post_access,
               "no valid laboratory backtest, ending at the last V_end or labelled post-access")
    g[11] = _g(e.point_in_time_share >= e.declared_point_in_time_share or e.reconstructed_waiver is not None,
               f"point_in_time share {e.point_in_time_share:.3f} < {e.declared_point_in_time_share:.3f} "
               f"without a waiver")
    missing = sorted(set(e.model_labels) - set(e.designation_labels))
    g[12] = _g(not missing and e.designation_cites_access_count,
               f"the designation omits {missing}" if missing else "the designation omits the lifetime access count",
               lifetime_test_accesses=e.lifetime_test_accesses)
    return {"kind": PROMOTION, "gates": g, "passed": all(x["pass"] for x in g.values()),
            "labels": sorted(set(e.model_labels) | ({"reconstructed_evidence"} if e.reconstructed_waiver is not None
                                                     else set())),
            "multiplicity": max(1, e.multiplicity)}
