"""
Phase 5 M5-6 (design §5.5, §9.3, §13): the sufficiency rule and the gates evaluated in application code. MLK-13: one
case per gate, each alone blocking, including both branches of gate 5 (a baseline not beaten, and the size not shown),
the engine baselines of a no_engine_component combination, and each label a designation must cite (gate 12).
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from worker.ml_registry import gates as gt
from worker.ml_registry import sufficiency as sf

GOOD_PLAN = sf.Plan(5, 10, 504, (42, 42, 42), (63,) * 5, 200, (50,) * 5, 50)


def test_s1_sufficiency_each_minimum_alone():
    assert sf.check(GOOD_PLAN) == (sf.SUFFICIENT, [])
    cases = {"block_length": dict(block_length=9), "fit slice": dict(fit_dates=503),
             "calibrate slice": dict(inner_dates=(42, 41, 42)), "folds < 5": dict(fold_dates=(63,) * 4,
                                                                              fold_securities=(50,) * 4),
             "fold 3: 62": dict(fold_dates=(63, 63, 62, 63, 63)), "test period": dict(test_dates=199),
             "fold 2: 49 securities": dict(fold_securities=(50, 49, 50, 50, 50)),
             "test period: 49": dict(test_securities=49)}
    for words, change in cases.items():
        state, reasons = sf.check(replace(GOOD_PLAN, **change))
        assert state == sf.EXPLORATORY and any(words.split(":")[0] in r for r in reasons), (words, reasons)
    # revision 5: 20 blocks of L; at h = 21, L >= 21, so at least 420 test dates
    assert sf.check(replace(GOOD_PLAN, horizon=21, block_length=21, inner_dates=(126,) * 3, fold_dates=(168,) * 5,
                            test_dates=419))[0] == sf.EXPLORATORY


def passed():
    return NS(state="tested", rejects=True)


GOOD = gt.ValidationEvidence(
    protocol_sufficiency=sf.SUFFICIENT, declared_before_first_run=True, in_family=True, leakage_suite_passed=True,
    code_version="c1", folds=tuple(gt.FoldEvidence(i, True, True, True, True) for i in range(1, 6)), declared_folds=5,
    baseline_results={"base_prior": passed(), "base_rate_252": passed()}, required_baselines=("base_prior",
                                                                                              "base_rate_252"),
    size_shown=True, policy_status={"sufficiency.1": "validated", "scoring.1": "validated"}, survivorship="complete",
    survivorship_waiver=None, domain_recorded=True, ages_evaluated=(0, 210), multiplicity=3)


def test_mlk13_each_validation_gate_alone_blocks():
    ok = gt.validation_report(GOOD)
    assert ok["passed"] and ok["labels"] == [] and ok["multiplicity"] == 3
    cases = {
        2: [dict(protocol_sufficiency=sf.EXPLORATORY), dict(declared_before_first_run=False), dict(in_family=False)],
        3: [dict(leakage_suite_passed=False)],
        4: [dict(folds=GOOD.folds[:4]), dict(folds=GOOD.folds[:2] + (gt.FoldEvidence(3, True, False, True, True),)
                                              + GOOD.folds[3:]),
            dict(folds=GOOD.folds[:1] + (gt.FoldEvidence(2, True, True, False, True),) + GOOD.folds[2:]),
            dict(folds=GOOD.folds[:4] + (gt.FoldEvidence(5, True, True, True, False),)),
            dict(folds=(gt.FoldEvidence(1, False, None, None, True),) + GOOD.folds[1:])],
        5: [dict(baseline_results={"base_prior": passed(), "base_rate_252": NS(state="tested", rejects=False)}),
            dict(baseline_results={"base_prior": passed(), "base_rate_252": NS(state="insufficient_evidence",
                                                                                rejects=None)}),
            dict(baseline_results={"base_prior": passed()}),
            dict(size_shown=False), dict(required_baselines=())],
        6: [dict(policy_status={"sufficiency.1": "provisional", "scoring.1": "validated"})],
        7: [dict(survivorship="unknown")],
        8: [dict(domain_recorded=False), dict(ages_evaluated=())],
    }
    for gate, variants in cases.items():
        for change in variants:
            rep = gt.validation_report(replace(GOOD, **change))
            failing = [n for n, x in rep["gates"].items() if not x["pass"]]
            assert not rep["passed"] and failing == [gate], (gate, change, failing)
    assert gt.validation_report(replace(GOOD, size_shown=False))["gates"][5]["reason"] == "size_not_shown"
    w = gt.validation_report(replace(GOOD, survivorship="unknown", survivorship_waiver=4))
    assert w["passed"] and w["labels"] == ["survivorship_waived"]


def test_gate5_engines_are_baselines_of_a_no_engine_component_combination():
    req = ("base_prior", "calibrated_engine:fund_growth")
    e = replace(GOOD, no_engine_component=True, required_baselines=req,
                baseline_results={"base_prior": passed()})
    rep = gt.validation_report(e)
    assert not rep["gates"][5]["pass"] and "calibrated_engine:fund_growth" in rep["gates"][5]["reason"]
    ok = gt.validation_report(replace(e, baseline_results={b: passed() for b in req}))
    assert ok["passed"] and "no_engine_component" in ok["labels"]


GOODP = gt.PromotionEvidence(access_done=True, nominee_passed=True, backtest_valid=True,
                             backtest_ends_at_last_v_end_or_post_access=True, point_in_time_share=0.6,
                             declared_point_in_time_share=0.5, reconstructed_waiver=None,
                             model_labels=("retrospective", "previously_scored"),
                             designation_labels=("retrospective", "previously_scored"),
                             designation_cites_access_count=True, lifetime_test_accesses=2)


def test_mlk13_each_promotion_gate_alone_blocks():
    assert gt.promotion_report(GOODP)["passed"]
    cases = {9: [dict(access_done=False), dict(nominee_passed=False)],
             10: [dict(backtest_valid=False), dict(backtest_ends_at_last_v_end_or_post_access=False)],
             11: [dict(point_in_time_share=0.4)],
             12: [dict(designation_labels=("retrospective",)), dict(designation_cites_access_count=False)]}
    for gate, variants in cases.items():
        for change in variants:
            rep = gt.promotion_report(replace(GOODP, **change))
            assert not rep["passed"] and [n for n, x in rep["gates"].items() if not x["pass"]] == [gate], (gate,
                                                                                                          change)
    for label in gt.LABELS:                                     # each label a designation must cite
        rep = gt.promotion_report(replace(GOODP, model_labels=(label,), designation_labels=()))
        assert not rep["gates"][12]["pass"] and label in rep["gates"][12]["reason"]
    w = gt.promotion_report(replace(GOODP, point_in_time_share=0.0, reconstructed_waiver=7))
    assert w["gates"][11]["pass"] and "reconstructed_evidence" in w["labels"]
