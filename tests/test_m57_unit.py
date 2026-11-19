"""
Phase 5 M5-7 (design §10, §11): combinations (MLK-10), designation lists and routing (MLK-19, MLK-21), the validated
domain and its refusals (MLK-15), the §11.1 record and its hash. Pure.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from worker.ml_route1 import combine as cb
from worker.ml_route1 import domain as dm
from worker.ml_route1 import record as rc
from worker.ml_route1 import routing as rt

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def cp(cid, key, fold, probs=None, ret=None, in_sample=False, resolved=T0):
    return cb.ComponentPrediction(cid, key, fold, probs, ret, None, in_sample, resolved)


AB = (cb.Component("a", "model", True), cb.Component("b", "model", True))


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
        cb.fit(AB, "return", ok, {("S", 1): 0.1}, 1, T0, horizon=5)


def test_combine_mean_needs_every_component_and_probabilities_for_direction():
    preds, labels, meta = [], {}, {}
    for i in range(40):
        k = ("S", i)
        preds += [cp("a", k, 1, ret=0.01 * i), cp("b", k, 1, ret=0.03 * i)]
        labels[k] = 0.02 * i + (0.05 if i % 2 else -0.05)
        meta[k] = cb.RowMeta(0.01, "mid")
    preds.append(cp("a", ("S", 99), 1, ret=5.0))            # its other component is missing: not a row
    art = cb.fit(AB, "return", preds, {**labels, ("S", 99): 0.0}, 2, T0, horizon=4, meta=meta)
    body = art.load(cb.COMBINE_MEAN)
    # §8.2: |y - mean| / max(vol_21 x sqrt(h), floor) = 0.05 / 0.02 = 2.5, in the row's class
    assert body["rows"] == 40 and body["quantile"]["mid"] == pytest.approx(2.5) and body["quantile"]["low"] is None
    m = cb.RowMeta(0.01, "mid")
    out = cb.apply(art, [cp("a", ("S", 0), 2, ret=1.0), cp("b", ("S", 0), 2, ret=3.0)], horizon=4, fold=2, meta=m)
    assert out["value"] == 2.0 and out["interval"] == (pytest.approx(1.95), pytest.approx(2.05))
    wide = cb.apply(art, [cp("a", ("S", 0), 2, ret=1.0), cp("b", ("S", 0), 2, ret=3.0)], horizon=4, fold=2,
                    meta=cb.RowMeta(0.04, "mid"))
    assert wide["interval"] == (pytest.approx(1.8), pytest.approx(2.2))     # scale-normalised: twice the width
    low = cb.apply(art, [cp("a", ("S", 0), 2, ret=1.0), cp("b", ("S", 0), 2, ret=3.0)], horizon=4, fold=2,
                   meta=cb.RowMeta(None, "low"))
    assert low["interval"] is None and low["flags"] == ["vol_21_null", "insufficient_rows"]
    with pytest.raises(cb.Refused, match="components"):
        cb.apply(art, [cp("b", ("S", 0), 2, ret=1.0), cp("a", ("S", 0), 2, ret=3.0)], horizon=4, fold=2, meta=m)
    with pytest.raises(cb.Refused, match="components"):
        cb.apply(art, [cp("a", ("S", 0), 2, ret=1.0)], horizon=4, fold=2, meta=m)
    with pytest.raises(cb.Refused, match="no_probabilities"):
        cb.mean_outputs([cp("a", ("S", 0), 1, probs=(0.2, 0.5, 0.3)), cp("base_rate", ("S", 0), 1)], "direction")
    with pytest.raises(cb.Refused, match="row_meta"):
        cb.fit(AB, "return", preds, labels, 2, T0, horizon=4)


def test_m1_the_interval_is_per_liquidity_class_and_covers_each():
    """M5-7 audit M1: two classes of different volatility each get their nominal coverage."""
    import random
    rng = random.Random(3)

    def world(n, fold):
        preds, labels, meta = [], {}, {}
        for i in range(n):
            k = ("S", fold, i)
            cls, vol = ("low", 0.05) if i % 2 else ("high", 0.01)
            preds += [cp("a", k, fold, ret=0.0), cp("b", k, fold, ret=0.0)]
            labels[k] = rng.gauss(0, 1) * vol * 2
            meta[k] = cb.RowMeta(vol, cls)
        return preds, labels, meta
    p, lab, meta = world(2000, 1)
    art = cb.fit(AB, "return", p, lab, 2, T0, horizon=4, meta=meta)
    p2, lab2, meta2 = world(4000, 2)
    hits = {"low": [], "high": []}
    for i in range(4000):
        k = ("S", 2, i)
        out = cb.apply(art, [cp("a", k, 2, ret=0.0), cp("b", k, 2, ret=0.0)], horizon=4, fold=2, meta=meta2[k])
        lo, hi = out["interval"]
        hits[meta2[k].liquidity_class].append(lo <= lab2[k] <= hi)
    for cls, h in hits.items():
        assert abs(sum(h) / len(h) - 0.9) < 0.03, (cls, sum(h) / len(h))


def test_m2_a_combiner_is_an_artifact_bound_to_its_horizon_and_fold():
    preds, labels, meta = [], {}, {}
    for i in range(30):
        k = ("S", i)
        preds += [cp("a", k, 1, ret=0.0), cp("b", k, 1, ret=0.0)]
        labels[k], meta[k] = 0.01 * i, cb.RowMeta(0.01, "mid")
    art = cb.fit(AB, "return", preds, labels, 3, T0, horizon=5, meta=meta)
    assert art.sha256 and art.load(cb.COMBINE_MEAN)["fold"] == 3
    parts = lambda f: [cp("a", ("S", 0), f, ret=0.0), cp("b", ("S", 0), f, ret=0.0)]       # noqa: E731
    assert cb.apply(art, parts(3), horizon=5, fold=3, meta=meta[("S", 0)])["value"] == 0.0
    with pytest.raises(cb.Refused, match="horizon"):
        cb.apply(art, parts(3), horizon=10, fold=3, meta=meta[("S", 0)])
    for f, fold in ((1, 3), (1, 1), (None, 3)):
        with pytest.raises(cb.Refused, match="fold"):
            cb.apply(art, parts(f), horizon=5, fold=fold, meta=meta[("S", 0)])
    final = cb.fit_final(AB, "return", preds, labels, 5, T0, horizon=5, meta=meta)
    assert final.load(cb.COMBINE_MEAN)["fold"] == cb.FINAL
    assert cb.apply(final, parts(None), horizon=5, fold=cb.FINAL, meta=meta[("S", 0)])["value"] == 0.0
    with pytest.raises(cb.Refused, match="fold"):
        cb.apply(final, parts(5), horizon=5, fold=cb.FINAL, meta=meta[("S", 0)])
    with pytest.raises(cb.Refused, match="duplicate"):
        cb.fit(AB, "return", preds + [cp("a", ("S", 0), 1, ret=1.0)], labels, 3, T0, horizon=5, meta=meta)


def test_m4_components_are_gated_models_or_calibrated_engines():
    assert cb.check_components(AB) is True                                  # no engine component
    assert cb.check_components(AB + (cb.Component("e", "calibrated_engine", True),)) is False
    for bad, code in (((cb.Component("r2", "route2", True),), "component_kind"),
                      ((cb.Component("base_rate", "baseline", True),), "component_kind"),
                      ((cb.Component("a", "model", False),), "component_not_gated"),
                      ((AB[0], AB[0]), "components"), ((), "components")):
        with pytest.raises(cb.Refused) as e:
            cb.check_components(bad)
        assert e.value.code == code
    with pytest.raises(cb.Refused, match="component_kind"):
        cb.fit((cb.Component("r2", "route2", True),), "return", [], {}, 2, T0, horizon=5)


def test_constants_match_calibration():
    pytest.importorskip("numpy")
    from worker.ml_models import calibration
    assert (cb.FLOOR_QUANTILE, cb.LIQUIDITY) == (calibration.FLOOR_QUANTILE, calibration.LIQUIDITY)


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
    art = cb.fit(AB, "direction", preds, labels, 2, T0, horizon=5)
    body = art.load(cb.COMBINE_MEAN)
    assert min(body["scale"]) > 1                                     # it sharpens the averaged probabilities
    assert sum(body["offset"]) == pytest.approx(0, abs=1e-12)          # offsets sum to zero
    raw = ((0.8, 0.1, 0.1), (1 / 3, 1 / 3, 1 / 3))
    out = cb.apply(art, [cp("a", ("S", 0), 2, probs=raw[0]), cp("b", ("S", 0), 2, probs=raw[1])], horizon=5, fold=2)
    assert out["probs"][0] > out["raw_mean"][0] and abs(sum(out["probs"]) - 1) < 1e-12
    # the scaling acts on the LOGARITHM of the mean: softmax(a * log(m) + b), by hand
    import math
    m = [(x + y) / 2 for x, y in zip(*raw)]
    z = [a * math.log(v) + b for v, a, b in zip(m, body["scale"], body["offset"])]
    e = [math.exp(v) for v in z]
    assert out["probs"] == pytest.approx(tuple(v / sum(e) for v in e), abs=1e-12)
    two = {k: (0 if v == 2 else v) for k, v in labels.items()}
    with pytest.raises(cb.Refused, match="classes"):
        cb.fit(AB, "direction", preds, two, 2, T0, horizon=5)
    with pytest.raises(cb.Refused, match="invalid_probabilities"):
        cb.mean_outputs([cp("a", ("S", 0), 1, probs=(5.0, -1.0, -3.0))], "direction")


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
    a = dict(designated=True, routed=True, age_sessions=10, incidents=(), cutoff=T0, horizon=5)
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
             (dict(incidents=((T0 - timedelta(days=1), T0 + timedelta(days=1), T0 - timedelta(hours=1)),)),
              ("blocked", "incident"))]
    for kw, expect in cases:
        r = kw.pop("r", row())
        assert decide(r, **kw)[:2] == expect, (kw, expect)
    # a flag entered after T removes nothing with hindsight; an unrelated registry feature never blocks
    assert decide(row(), incidents=((T0 - timedelta(days=1), T0 + timedelta(days=1), T0 + timedelta(hours=1)),)
                  )[:3] == ("predicted", None, ("incident_recorded_later",))     # M5: §11.2's reported stratum
    # half-open [from, to), as ml_incident_flags.covers: a flag ending at T does not cover T
    assert decide(row(), incidents=((T0 - timedelta(days=1), T0, T0 - timedelta(hours=1)),))[:3] == \
        ("predicted", None, ())
    assert decide(row(), incidents=((T0, T0 + timedelta(days=1), T0),))[:2] == ("blocked", "incident")
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
             components=[{"configuration_id": "a", "kind": "model", "direction": {"raw_probs": [0.5, 0.3, 0.2],
                                                                 "calibrated_probs": [0.4, 0.35, 0.25],
                                                                 "contributions_class": "rise"}}],
             engines={"fund_growth": {"score": "0.5", "state": "scored"}, "mkt_mom": {"score": None,
                                                                                    "state": "not_applicable"}},
             source_hashes={"mf": "a" * 64, "f7": None, "de": None}, artifact_hashes=["b" * 64], row_label_class="point_in_time",
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
                 **dict(PARTS, components=[{"configuration_id": "a", "kind": "model",
                                               "direction": {"raw_probs": [1, 0, 0]}}]))
    rec = rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, "predicted", **dict(PARTS, evidence_label_class="mixed"))
    assert "reconstructed_evidence" in rec["flags"]
    with pytest.raises(ValueError):
        rc.canonical({"x": float("nan")})


def test_conformal_index_and_age_boundary():
    preds, labels, meta = [], {}, {}
    for i in range(1, 10):                                  # residuals 1..9, distinct, scale 1
        k = ("S", i)
        preds += [cp("a", k, 1, ret=0.0), cp("b", k, 1, ret=0.0)]
        labels[k], meta[k] = float(i), cb.RowMeta(0.5, "mid")
    q = lambda lv: cb.fit(AB, "return", preds, labels, 2, T0, horizon=4, meta=meta, level=lv).load(   # noqa: E731
        cb.COMBINE_MEAN)["quantile"]["mid"]
    assert q(0.8) == 8.0                                    # ceil((9 + 1) x 0.8) = 8th smallest
    assert q(0.95) is None                                  # too few rows
    assert decide(row(), age_sessions=100)[0] == "predicted"                      # at the limit: still valid


def test_m3_the_domain_fails_closed():
    with pytest.raises(ValueError, match="envelope"):
        dm.Domain(**{**DOM.__dict__, "envelope": {"ret_21": (-0.5, 0.5)}})          # vol_21 critical, no envelope
    with pytest.raises(ValueError, match="envelope"):
        dm.Domain(**{**DOM.__dict__, "envelope": {"ret_21": (-0.5, 0.5), "vol_21": None}})
    assert dm.Domain(**{**DOM.__dict__, "envelope": {"ret_21": (-0.5, 0.5)}, "rank_features": ("vol_21",)})
    none = dm.Domain(**{**DOM.__dict__, "industry_groups": ()})
    assert decide(row(), domain=none)[:2] == ("blocked", "outside_domain")          # an empty list admits nothing
    with pytest.raises(ValueError, match="h = 5"):
        decide(row(), horizon=10)


def test_domain_order_waiver_and_clipping():
    # a changed version AND an out-of-envelope value: reported as changed
    assert decide(row(features={"ret_21": 0.9, "vol_21": 0.02}, feature_versions={"ret_21": "2", "vol_21": "1"})
                  )[:2] == ("blocked", "version_changed")
    # the waiver lets point_in_time rows through only, never another class
    rec = dm.Domain(**{**DOM.__dict__, "label_classes": ("reconstructed",), "reconstructed_waiver": True})
    assert decide(row(label_class="survivors_only"), domain=rec)[:2] == ("blocked", "outside_domain")
    # a critical feature inside the envelope but beyond the fit bounds is NOT clipped; a non-critical one is
    st, _r, _f, clipped = decide(row(features={"ret_21": 0.45, "vol_21": 0.02, "turn": -3.0}))
    assert st == "predicted" and clipped == {"ret_21": 0.45, "vol_21": 0.02, "turn": 0.0}


def test_m5_refusal_rates_by_regime():
    decisions = [("predicted", None, ()), ("blocked", "outside_validated_domain", ()),
                 ("predicted", None, ("incident_recorded_later",)), ("predicted", None, ())]
    out = dm.refusal_rates(decisions, ["calm", "calm", "stress", "stress"])
    assert out == {"calm": {"rows": 2, "predicted": 0.5, "outside_validated_domain": 0.5},
                   "stress": {"rows": 2, "predicted": 1.0, "incident_recorded_later": 0.5}}


def test_mlk21_a_row_is_scored_with_its_own_positions_prediction():
    a, b = rt.Position("c-a", ("x",)), rt.Position("c-b", ("y",))
    rows = [({"y"}, 1.0, {0: 9.0, 1: 1.0})]                  # routed to position 1: its own value, not position 0's
    out = rt.per_position(rows, (a, b), lambda act, pred: sum(abs(x - y) for x, y in zip(act, pred)))
    assert out[1] == (0.0, 1) and out[0] == (None, 0)


def test_record_validation_flags_and_canonical_form():
    def build(state="predicted", reason=None, **kw):
        return rc.build("S.N0000", date(2026, 1, 2), T0, 5, 7, state, reason, **dict(PARTS, **kw))
    assert "no_engine_component" in build()["flags"]                                    # derived from the kinds
    eng = PARTS["components"] + [{"configuration_id": "e", "kind": "calibrated_engine"}]
    assert "no_engine_component" not in build(components=eng)["flags"]
    assert "reconstructed_evidence" in build(evidence_label_class="mixed")["flags"]
    assert "survivors_only_evidence" in build(evidence_label_class="survivors_only")["flags"]
    for kw in (dict(outputs={}), dict(outputs={"return": {}}), dict(engines={}), dict(artifact_hashes=[]),
               dict(source_hashes={"mf": "a" * 64}), dict(components=[])):
        with pytest.raises(ValueError):
            build(**kw)
    with pytest.raises(ValueError, match="kind"):
        build(components=[{"configuration_id": "a"}])
    with pytest.raises(ValueError, match="reason"):
        build("blocked", None, outputs={}, components=[])
    assert build("blocked", "incident", outputs={}, components=[])["state"] == "blocked"
    # canonical: one text per value, keys sorted, small differences kept
    assert rc.canonical({"a": 1, "b": 2}) == rc.canonical({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert rc.canonical(-0.0) == rc.canonical(0.0)
    assert rc.canonical(Decimal("0.50")) == rc.canonical(Decimal("0.5")) == '"0.5"'
    assert rc.canonical(Decimal("-0")) == '"0"' and rc.canonical(Decimal("1E+2")) == '"100"'
    assert rc.canonical(0.1234567891) != rc.canonical(0.1234567892)
    with pytest.raises(ValueError, match="keys"):
        rc.canonical({1: "x"})
    with pytest.raises(ValueError):
        rc.canonical({"x": object()})
