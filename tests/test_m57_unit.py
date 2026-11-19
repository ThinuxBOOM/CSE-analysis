"""
Phase 5 M5-7 (design §10, §11): combinations (MLK-10), designation lists and routing (MLK-19, MLK-21), the validated
domain and its refusals (MLK-15), the §11.1 record and its hash. Pure.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from worker.ml_route1 import combine as cb
from worker.ml_route1 import domain as dm
from worker.ml_route1 import record as rc
from worker.ml_route1 import routing as rt

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def cp(cid, key, fold, probs=None, ret=None, in_sample=False, resolved=T0):
    return cb.ComponentPrediction(cid, key, fold, probs, ret, None, in_sample, resolved)


def test_mlk10_a_combiner_reads_out_of_fold_predictions_resolved_by_its_cutoff_only():
    ok = [cp("a", ("S", 1), 1, ret=0.1), cp("b", ("S", 1), 2, ret=0.2)]
    assert cb.check_out_of_fold(ok, 3, T0)
    for bad, code in ((cp("a", ("S", 2), 1, in_sample=True), "in_sample"), (cp("a", ("S", 2), 3), "not_out_of_fold"),
                      (cp("a", ("S", 2), 4), "not_out_of_fold"), (cp("a", ("S", 2), None), "not_out_of_fold"),
                      (cp("a", ("S", 2), 1, resolved=T0 + timedelta(seconds=1)), "resolves_after_cutoff"),
                      (cp("a", ("S", 2), 1, resolved=None), "resolves_after_cutoff")):
        with pytest.raises(cb.Refused) as e:
            cb.check_out_of_fold(ok + [bad], 3, T0)
        assert e.value.code == code
    with pytest.raises(cb.Refused, match="fold_1"):
        cb.fit(("a", "b"), "return", ok, {("S", 1): 0.1}, 1, T0)


def test_combine_mean_needs_every_component_and_probabilities_for_direction():
    preds = []
    labels = {}
    for i in range(40):
        k = ("S", i)
        preds += [cp("a", k, 1, ret=0.01 * i), cp("b", k, 1, ret=0.03 * i)]
        labels[k] = 0.02 * i + (0.05 if i % 2 else -0.05)
    preds.append(cp("a", ("S", 99), 1, ret=5.0))            # its other component is missing: not a row
    body = cb.fit(("a", "b"), "return", preds, {**labels, ("S", 99): 0.0}, 2, T0)
    assert body["rows"] == 40 and body["quantile"] == pytest.approx(0.05)
    out = cb.apply(body, [cp("a", ("S", 0), None, ret=1.0), cp("b", ("S", 0), None, ret=3.0)])
    assert out["value"] == 2.0 and out["interval"] == (pytest.approx(1.95), pytest.approx(2.05))
    with pytest.raises(cb.Refused, match="components"):
        cb.apply(body, [cp("b", ("S", 0), None, ret=1.0), cp("a", ("S", 0), None, ret=3.0)])
    with pytest.raises(cb.Refused, match="components"):
        cb.apply(body, [cp("a", ("S", 0), None, ret=1.0)])
    with pytest.raises(cb.Refused, match="no_probabilities"):
        cb.mean_outputs([cp("a", ("S", 0), 1, probs=(0.2, 0.5, 0.3)), cp("base_rate", ("S", 0), 1)], "direction")


def test_combine_mean_direction_scales_the_mean_of_calibrated_probabilities():
    pytest.importorskip("scipy")
    import random
    rng = random.Random(1)
    preds, labels = [], {}
    for i in range(300):
        k = ("S", i)
        y = rng.randrange(3)
        sharp = [0.1, 0.1, 0.1]
        sharp[y] = 0.8                                       # informative but under-confident after averaging
        flat = (1 / 3, 1 / 3, 1 / 3)
        preds += [cp("a", k, 1, probs=tuple(sharp)), cp("b", k, 1, probs=flat)]
        labels[k] = y
    body = cb.fit(("a", "b"), "direction", preds, labels, 2, T0)
    assert body["converged"] and min(body["scale"]) > 1               # it sharpens the averaged probabilities
    out = cb.apply(body, [cp("a", ("S", 0), None, probs=(0.8, 0.1, 0.1)),
                          cp("b", ("S", 0), None, probs=(1 / 3, 1 / 3, 1 / 3))])
    assert out["probs"][0] > out["raw_mean"][0] and abs(sum(out["probs"]) - 1) < 1e-12


def test_mlk19_the_designation_in_force_at_the_cutoff():
    ds = [(1, T0, ["x"]), (2, T0 + timedelta(days=5), ["y"]), (3, T0 + timedelta(days=5), ["z"])]
    assert rt.in_force(ds, T0 + timedelta(days=1))[0] == 1
    assert rt.in_force(ds, T0 + timedelta(days=5))[0] == 3            # same instant: the later id
    assert rt.in_force(ds, T0 - timedelta(seconds=1)) is None


def test_mlk21_each_position_is_scored_on_the_rows_routed_to_it_only():
    full = rt.Position("c-full", ("mkt", "fund"))
    market = rt.Position("c-mkt", ("mkt",))
    rows = []
    for i in range(10):                                     # rows WITH fundamentals: position 0 predicts well
        rows.append(({"mkt", "fund"}, 1.0, {0: 1.0, 1: 0.0}))     # position 1 would be poor on them
    for i in range(10):                                     # rows WITHOUT fundamentals: only position 1 can score
        rows.append(({"mkt"}, 2.0, {1: 1.0}))
    rows.append((set(), 0.0, {}))
    mae = lambda a, p: sum(abs(x - y) for x, y in zip(a, p)) / len(a)          # noqa: E731
    out = rt.per_position(rows, (full, market), mae)
    assert out[0] == (0.0, 10) and out[1] == (1.0, 10) and out["unrouted"] == 1   # never position 1 on all 20 rows
    assert rt.route((full, market), {"mkt"}) == 1 and rt.route((full, market), set()) is None


DOM = dm.Domain(label_classes=("point_in_time",), eligibility_policy="elig.1", feature_versions={"ret_21": "1",
                "vol_21": "1"}, horizon=5, industry_groups=("banks",), envelope={"ret_21": (-0.5, 0.5),
                "vol_21": (0.0, 0.1)}, bounds={"ret_21": (-0.4, 0.4), "vol_21": (0.0, 0.08), "turn": (0.0, 9.0)},
                critical=("ret_21", "vol_21"), max_age_sessions=100)


def row(**kw):
    a = dict(features={"ret_21": 0.1, "vol_21": 0.02, "turn": 20.0}, label_class="point_in_time",
             industry_group="banks", eligibility_policy="elig.1", feature_versions={"ret_21": "1", "vol_21": "1",
                                                                                    "new_feature": "1"})
    a.update(kw)
    return dm.Row(**a)


def decide(r, domain=DOM, **kw):
    a = dict(designated=True, routed=True, age_sessions=10, incidents=(), cutoff=T0)
    a.update(kw)
    return dm.decide(r, domain, **a)


def test_mlk15_every_refusal_of_the_table():
    state, reason, flags, clipped = decide(row())
    assert (state, reason) == ("predicted", None) and clipped["turn"] == 9.0 and clipped["ret_21"] == 0.1
    cases = [(dict(r=row(hashes_ok=False)), ("invalid_input", "hash_mismatch")),
             (dict(designated=False), ("not_applicable", "no_designation")),
             (dict(r=row(eligible=False)), ("insufficient_data", "not_market_data_eligible")),
             (dict(routed=False), ("insufficient_data", "no_combination_with_all_components")),
             (dict(r=row(features={"ret_21": None, "vol_21": 0.02})), ("insufficient_data",
                                                                     "missing_critical_features")),
             (dict(r=row(admissible={"vol_21": False})), ("insufficient_data", "missing_critical_features")),
             (dict(r=row(features={"ret_21": 0.6, "vol_21": 0.02})), ("blocked", "outside_validated_domain")),
             (dict(r=row(feature_versions={"ret_21": "2", "vol_21": "1"})), ("blocked", "version_changed")),
             (dict(r=row(eligibility_policy="elig.2")), ("blocked", "version_changed")),
             (dict(r=row(label_class="reconstructed")), ("blocked", "outside_domain")),
             (dict(r=row(industry_group="hotels")), ("blocked", "outside_domain")),
             (dict(age_sessions=101), ("blocked", "model_stale")),
             (dict(incidents=((T0 - timedelta(days=1), T0, T0 - timedelta(hours=1)),)), ("blocked", "incident"))]
    for kw, expect in cases:
        r = kw.pop("r", row())
        assert decide(r, **kw)[:2] == expect, (kw, expect)
    # a flag entered after T removes nothing with hindsight; an unrelated registry feature never blocks
    assert decide(row(), incidents=((T0 - timedelta(days=1), T0, T0 + timedelta(hours=1)),))[0] == "predicted"
    # a rank feature has no envelope check
    ranked = dm.Domain(**{**DOM.__dict__, "rank_features": ("ret_21",)})
    assert decide(row(features={"ret_21": 0.9, "vol_21": 0.02}), domain=ranked)[0] == "predicted"
    # the C-0039 waiver: a model validated on reconstructed evidence predicts point_in_time rows, flagged
    rec = dm.Domain(**{**DOM.__dict__, "label_classes": ("reconstructed",), "reconstructed_waiver": True})
    assert decide(row(), domain=rec)[:3] == ("predicted", None, ("reconstructed_evidence",))
    assert decide(row(), domain=dm.Domain(**{**DOM.__dict__, "label_classes": ("reconstructed",)}))[0] == "blocked"


def test_the_envelope_is_the_fit_slices_tails_widened():
    vals = [float(i) for i in range(1001)]
    env, bounds = dm.envelope_of(vals + [None], 0.1)
    assert env == (pytest.approx(1.0 - 99.8), pytest.approx(999.0 + 99.8)) and bounds == (0.0, 1000.0)
    assert dm.envelope_of([None], 0.1) == (None, None)


PARTS = dict(outputs={"return": {"combination": "c1", "position": 0, "value": 0.01, "interval": [-0.02, 0.04],
                                 "level": 0.9}},
             components=[{"configuration_id": "a", "direction": {"raw_probs": [0.5, 0.3, 0.2],
                                                                 "calibrated_probs": [0.4, 0.35, 0.25],
                                                                 "contributions_class": "rise"}}],
             engines={"fund_growth": {"score": "0.5", "state": "scored"}, "mkt_mom": {"score": None,
                                                                                    "state": "not_applicable"}},
             source_hashes={"mf": "a" * 64}, artifact_hashes=["b" * 64], row_label_class="point_in_time",
             evidence_label_class="point_in_time", code_version={"ml_route1": "1"})


def test_the_record_carries_every_part_and_its_hash_reproduces():
    r = rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **PARTS)
    assert rc.row_hash(r) == rc.row_hash(rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **PARTS))
    other = dict(PARTS, outputs={"return": dict(PARTS["outputs"]["return"], value=0.02)})
    assert rc.row_hash(rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **other)) != rc.row_hash(r)
    with pytest.raises(ValueError, match="every part"):
        rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **{k: v for k, v in PARTS.items()
                                                                          if k != "engines"})
    with pytest.raises(ValueError, match="raw and calibrated"):
        rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted",
                 **dict(PARTS, components=[{"configuration_id": "a", "direction": {"raw_probs": [1, 0, 0]}}]))
    rec = rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **dict(PARTS, evidence_label_class="mixed"))
    assert "reconstructed_evidence" in rec["flags"]
    with pytest.raises(ValueError):
        rc.canonical({"x": float("nan")})


def test_conformal_index_and_age_boundary():
    preds, labels = [], {}
    for i in range(1, 10):                                  # residuals 1..9, distinct
        k = ("S", i)
        preds += [cp("a", k, 1, ret=0.0), cp("b", k, 1, ret=0.0)]
        labels[k] = float(i)
    body = cb.fit(("a", "b"), "return", preds, labels, 2, T0, level=0.8)
    assert body["quantile"] == 8.0                          # ceil((9 + 1) x 0.8) = 8th smallest
    assert cb.fit(("a", "b"), "return", preds, labels, 2, T0, level=0.95)["quantile"] is None   # too few rows
    assert decide(row(), age_sessions=100)[0] == "predicted"                      # at the limit: still valid
