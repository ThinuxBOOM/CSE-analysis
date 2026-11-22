"""
Phase 5 M5-2 (docs/PHASE5_ML_DESIGN.md §4, §12.2, §13): the dataset builder, its manifest and version, the strata.
Determinism; the cutoff rule; MLK-17 (strata at T unchanged by later data); MLK-18 (the worst label carried, a second
target build refused); MLK-20 (an upstream null or refusal reaches the dataset as a null with its reason, and later
plants change nothing at T); the MLK-24 refusals that belong to a dataset (two H values, a basis_not_raw input). Pure.
"""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from worker.ml_dataset import dataset as ds
from worker.ml_dataset import strata as st

COL = timezone(timedelta(hours=5, minutes=30))
D0 = date(2024, 1, 1)
H = datetime(2025, 6, 30, tzinfo=timezone.utc)


def day(i):
    return D0 + timedelta(days=i)


def t_of(i, hour=18):
    return datetime.combine(day(i), time(hour), COL)


def line(sym, i, k, eligible="eligible", **over):
    feats = {"turnover_avg_21": (D(1000 + 37 * k + i), "fresh", None), "vol_21": (D("0.01") * (1 + (k + i) % 7), "fresh", None),
             "ret_21": (D("0.001") * (k - 5), "fresh", None)}
    feats.update(over.pop("feats", {}))
    f7 = ds.F7In({"roe_end": (D("0.1") + D("0.01") * k, "fresh", None)}, "eligible", (f"{k:063x}a",))
    return ds.LineIn(eligible, feats, f"{(i * 100 + k):064x}", over.pop("f7", f7),
                     {"fund_growth": (D("0.5"), "scored", f"{k:063x}b")}, "general", **over)


def snap(i, n=8, mode="KNOWN", horizon=None, label="point_in_time", surv="complete", hour=18, sid=None, **over):
    rows = {f"S{k}.N0000": line(f"S{k}.N0000", i, k) for k in range(n)}
    rows.update(over.pop("rows", {}))
    return ds.SnapshotIn(sid or 1000 + i, day(i), t_of(i, hour), mode, horizon, "mf", label, surv, rows, "f7", "de",
                         **over)


def labels_for(snaps, build=7):
    out = []
    for s in snaps:
        for sym in s.rows:
            for h in ds.HORIZONS:
                out.append(ds.LabelIn(build, s.snapshot_id, sym, h, "resolved", {"ret": "0.01"},
                                      s.cutoff + timedelta(days=h + 1), f"{s.snapshot_id * 10 + h:060x}{len(sym):04x}"))
    return out


def manifest(**over):
    m = dict(mf_configuration_id="mf", f7_configuration_id="f7", de_configuration_id="de", date_from=day(0),
             date_to=day(400), eras=(ds.Era(day(0), day(400), "KNOWN"),), target_build_id=7,
             label_definition_version="targets.1")
    m.update(over)
    return ds.Manifest(**m)


def tb(m, **over):
    """The pinned build's header, matching the manifest's (single-mode) eras unless overridden."""
    e = m.eras[0]
    b = dict(build_id=m.target_build_id, mode=e.mode, horizon=e.horizon, mf_configuration_id=m.mf_configuration_id,
             target_definition_version=m.label_definition_version)
    b.update(over)
    return ds.TargetBuildIn(**b)


def build(m, snaps, labs, b=None):
    return ds.build(m, snaps, labs, b or tb(m))


def world(n_sessions=30, **kw):
    snaps = [snap(i, **kw) for i in range(n_sessions)]
    return snaps, labels_for(snaps)


# ------------------------------------------------------------------------------------------------ determinism


def test_d1_deterministic_and_order_free_and_bound_to_its_sources():
    snaps, labs = world()
    a = build(manifest(), snaps, labs)
    b = build(manifest(), list(reversed(snaps)), list(reversed(labs)))
    assert a.version == b.version and a.rows == b.rows and a.labels == b.labels
    assert len(a.rows) == 30 * 8 and len(a.labels) == 30 * 8 * 4
    changed = list(labs)
    changed[5] = replace(changed[5], row_hash="f" * 64)
    assert build(manifest(), snaps, changed).version != a.version
    s2 = list(snaps)
    s2[3] = replace(s2[3], rows={**s2[3].rows, "S0.N0000": replace(s2[3].rows["S0.N0000"], row_hash="e" * 64)})
    assert build(manifest(), s2, labs).version != a.version
    assert build(manifest(label_definition_version="targets.2"), snaps, labs).version != a.version


def test_d2_labels_are_apart_and_features_carry_sources():
    snaps, labs = world(3)
    d = build(manifest(), snaps, labs)
    r = d.rows[0]
    assert not any(k.startswith(("label", "target")) for k in r.features)
    assert r.sources[0] == snaps[0].rows[r.symbol].row_hash and len(r.sources) == 3
    assert set(r.features) >= {"mf.turnover_avg_21", "f7.roe_end", "f7.eligibility", "de.fund_growth"}


# ------------------------------------------------------------------------------------------------ the cutoff rule


def test_c1_one_snapshot_per_session_the_last_before_the_rule():
    early, late = snap(0, hour=16, sid=1), snap(0, hour=20, sid=2)
    after = replace(snap(0, sid=3), cutoff=datetime.combine(day(1), time(0, 30), COL))    # past 23:59:59 on D
    d = build(manifest(), [early, late, after], labels_for([early, late, after]))
    assert {r.snapshot_id for r in d.rows} == {2}
    assert d.report["snapshots_superseded_in_session"] == 1 and d.report["snapshots_after_cutoff_rule"] == 1
    out = replace(snap(500), session=day(500))
    assert build(manifest(), [late, out], labels_for([late])).report["snapshots_outside_range"] == 1


def test_c2_ineligible_lines_are_counted_never_silent():
    s = snap(0, rows={"X.N0000": line("X.N0000", 0, 99, eligible="low_liquidity"),
                      "Y.N0000": line("Y.N0000", 0, 98, eligible="no_data")})
    d = build(manifest(), [s], labels_for([s]))
    assert d.report["excluded_by_eligibility"] == {"low_liquidity": 1, "no_data": 1}
    assert {r.symbol for r in d.rows} == {f"S{k}.N0000" for k in range(8)}


def test_c3_withheld_test_labels_are_not_built():
    snaps, labs = world(10)
    m = manifest(withheld=((t_of(5), t_of(9)),))
    d = build(m, snaps, labs)
    assert all(k[1] < day(5) for k in d.labels)
    assert d.report["labels_withheld_test_period"] == 5 * 8 * 4
    assert len(d.rows) == 80                                   # the feature rows stay


# ------------------------------------------------------------------------------------------------ MLK-18


def test_mlk18_the_worst_label_is_carried_and_a_second_build_refused():
    a = [snap(i) for i in range(5)]
    b = [snap(i, surv="survivors_only") for i in range(5, 10)]
    c = [snap(i, surv="unknown") for i in range(10, 12)]
    m = manifest()
    assert build(m, a + b + c, labels_for(a + b + c)).survivorship == "unknown"
    assert build(m, a, labels_for(a)).survivorship == "complete"
    assert build(m, a + b, labels_for(a + b)).survivorship == "survivors_only"
    av = manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE", H),))
    r = [snap(i, mode="AVAILABLE", horizon=H, label="reconstructed") for i in range(3)]
    d = build(av, r, labels_for(r))
    assert (d.label_class, d.fundamental_coverage) == ("reconstructed", "all")
    cov = [replace(s, rows={k: replace(v, f7=replace(v.f7, fundamental_coverage="captured_only"))
                            for k, v in s.rows.items()}) for s in r]
    assert build(av, cov, labels_for(cov)).fundamental_coverage == "captured_only"
    other = labels_for(a[:1], build=8)
    with pytest.raises(ds.Refused, match="second_target_build"):
        build(m, a, labels_for(a) + other)


def test_b1_the_pinned_builds_header_must_answer_the_manifest():
    """First M5-2 audit B1: mode, H, MF configuration and label definition of the build are checked."""
    snaps, labs = world(3)
    m = manifest()
    with pytest.raises(ds.Refused, match="build_era_mismatch"):
        build(m, snaps, labs, tb(m, mode="AVAILABLE", horizon=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    with pytest.raises(ds.Refused, match="label_definition"):
        build(m, snaps, labs, tb(m, target_definition_version="targets.9"))
    with pytest.raises(ds.Refused, match="configuration_mismatch"):
        build(m, snaps, labs, tb(m, mf_configuration_id="other"))
    with pytest.raises(ds.Refused, match="target_build"):
        build(m, snaps, labs, tb(m, build_id=99))
    with pytest.raises(ds.Refused, match="target_build"):
        ds.build(m, snaps, labs, None)
    av = manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE", H),))
    with pytest.raises(ds.Refused, match="build_era_mismatch"):
        build(av, [], [], tb(av, horizon=H + timedelta(days=1)))
    mixed = manifest(eras=(ds.Era(day(0), day(4), "KNOWN"), ds.Era(day(5), day(400), "AVAILABLE", H)))
    with pytest.raises(ds.Refused, match="build_era_mismatch"):              # one build cannot serve two clocks
        build(mixed, [], [])
    a = build(m, snaps, labs)
    assert ds.version(m, a.rows, a.labels, tb(m)) != ds.version(m, a.rows, a.labels, None)


# ------------------------------------------------------------------------------------------------ MLK-17


def test_mlk17_strata_at_t_unchanged_by_later_data():
    pol = st.RegimePolicy(min_history=10)
    snaps, labs = world(40)
    base = build(manifest(regime_policy=pol), snaps[:25], labels_for(snaps[:25]))
    later = []
    for s in snaps[25:]:                                         # later sessions with extreme values
        rows = {k: replace(v, features={**v.features, "vol_21": (D(9), "fresh", None),
                                        "turnover_avg_21": (D(-1) if k == "S0.N0000" else D(10 ** 9), "fresh", None)})
                for k, v in s.rows.items()}
        later.append(replace(s, rows=rows))
    full = build(manifest(regime_policy=pol), snaps[:25] + later, labels_for(snaps[:25] + later))
    at = {r.key: r for r in full.rows}
    for r in base.rows:
        assert at[r.key].meta == r.meta and at[r.key].features == r.features
    assert {r.meta["regime_volatility"] for r in base.rows} - {"undefined"}       # defined after 10 sessions
    assert any(r.meta["regime_volatility"] == "high" for r in full.rows if r.session >= day(25))
    assert [st.age_stratum(x) for x in (0, 20, 21, 62, 63, 125, 126, 251, 252, 5000)] == [
        "age_0_21", "age_0_21", "age_21_63", "age_21_63", "age_63_126", "age_63_126", "age_126_252", "age_126_252",
        "age_252_plus", "age_252_plus"]
    with pytest.raises(ValueError):
        st.age_stratum(-1)


def test_mlk17_liquidity_terciles_share_ties_and_unknowns():
    t = {"a": (D(1), "fresh", None), "b": (D(2), "fresh", None), "c": (D(2), "fresh", None),
         "d": (D(3), "fresh", None), "e": (None, "missing", "no_trades"), "f": (D(9), "untrusted", "x")}
    c = st.liquidity_classes(t)
    assert c["b"] == c["c"] and c["e"] == c["f"] == "unknown" and c["a"] == "low" and c["d"] == "high"


def test_regime_trend_needs_the_market_aggregate():
    snaps, labs = world(3)
    assert {r.meta["regime_trend"] for r in build(manifest(), snaps, labs).rows} == {"undefined"}
    assert st.trend_tag(None) == ("undefined", "market_aggregate_not_stored")
    assert st.trend_tag((D("0.05"), "fresh", None))[0] == "up"
    assert st.trend_tag((D("-0.05"), "stale", None))[0] == "down"
    assert st.trend_tag((D("0.0"), "fresh", None))[0] == "flat"
    assert st.trend_tag((D("0.05"), "unlabelled", "pending_action")) == ("undefined", "pending_action")
    s = replace(snaps[0], market={"mkt_ret_ew_21": (D("0.05"), "fresh", None)})
    assert {r.meta["regime_trend"] for r in build(manifest(), [s], labels_for([s])).rows} == {"up"}


# ------------------------------------------------------------------------------------------------ MLK-20


def test_mlk20_upstream_nulls_and_refusals_arrive_as_nulls_with_their_reason():
    bad_f7 = ds.F7In({"earnings_yield_eps": (D("0.08"), "untrusted", "eps_basis_ambiguous"),
                      "eps_latest_fy": (None, "conflicting", "conflicting"),
                      "pe_eps": (None, "missing", "no_close_since_document"),
                      "roe_end": (D("0.1"), "partial", "has_discontinued")}, "stale", ("c" * 64,))
    s = snap(0, rows={"Z.N0000": line("Z.N0000", 0, 50, f7=bad_f7, feats={
        "ret_21": (D("0.3"), "unlabelled", "pending_action"), "ret_5": (None, "missing", "unknown_price_in_window"),
        "vol_21": (D("0.02"), "conflicting", "conflicting_input")})})
    d = build(manifest(), [s], labels_for([s]))
    z = next(r for r in d.rows if r.symbol == "Z.N0000").features
    assert z["f7.earnings_yield_eps"] == (None, "untrusted", "eps_basis_ambiguous")
    assert z["f7.eps_latest_fy"] == (None, "conflicting", "conflicting")
    assert z["f7.pe_eps"] == (None, "missing", "no_close_since_document")
    assert z["f7.roe_end"] == (D("0.1"), "partial", "has_discontinued")              # admissible: kept
    assert z["mf.ret_21"] == (None, "unlabelled", "pending_action")
    assert z["mf.ret_5"] == (None, "missing", "unknown_price_in_window")
    assert z["mf.vol_21"] == (None, "conflicting", "conflicting_input")
    assert z["f7.eligibility"] == ("stale", "fresh", None)
    assert d.report["nulls_by_state"]["f7:untrusted"] >= 1 and d.report["nulls_by_state"]["mf:unlabelled"] >= 1
    nolink = snap(1, rows={"N.N0000": line("N.N0000", 1, 51, f7=None)})
    n = next(r for r in build(manifest(), [nolink], labels_for([nolink])).rows if r.symbol == "N.N0000")
    assert n.features["f7.eligibility"] == (None, "missing", "issuer_link_absent")
    assert not any(k.startswith("f7.") and k != "f7.eligibility" for k in n.features)


def test_mlk20_label_states_pass_through_and_a_label_resolved_by_t_is_refused():
    snaps, labs = world(2)
    labs = [replace(x, state="unresolvable_missing_price", values={}) if x.symbol == "S1.N0000" else x for x in labs]
    d = build(manifest(), snaps, labs)
    assert d.labels[("S1.N0000", day(0), 5)].state == "unresolvable_missing_price"
    assert d.report["labels_by_state"][5]["unresolvable_missing_price"] == 2
    leak = [replace(labs[0], resolved_at=snaps[0].cutoff)] + labs[1:]
    with pytest.raises(ds.Refused, match="label_before_t"):
        build(manifest(), snaps, leak)


def test_mlk20_rows_at_t_unchanged_by_plants_after_t():
    snaps, labs = world(6)
    base = build(manifest(), snaps[:3], labels_for(snaps[:3]))
    plant = [replace(s, rows={**s.rows, "NEW.N0000": line("NEW.N0000", 9, 77)}) for s in snaps[3:]]
    full = build(manifest(), snaps[:3] + plant, labels_for(snaps[:3] + plant))
    at = {r.key: r for r in full.rows}
    assert all(at[r.key] == r for r in base.rows)
    assert all(full.labels[k] == v for k, v in base.labels.items())


# ------------------------------------------------------------------------------------------------ refusals (MLK-24)


def test_refusals():
    s = snap(0)
    with pytest.raises(ds.Refused, match="two_h_values"):
        build(manifest(eras=(ds.Era(day(0), day(9), "AVAILABLE", H),
                                ds.Era(day(10), day(400), "AVAILABLE", H + timedelta(days=1)))), [s], [])
    with pytest.raises(ds.Refused, match="basis_not_raw"):
        build(manifest(), [replace(s, basis_raw=False)], [])
    with pytest.raises(ds.Refused, match="mode_not_point_in_time"):
        build(manifest(), [replace(s, mode="CURRENT")], [])
    other = build(manifest(), [replace(s, mode="AVAILABLE", horizon=H)], [])          # another mode: not chosen
    assert other.rows == () and other.report["snapshots_other_mode_or_h"] == 1
    with pytest.raises(ds.Refused, match="configuration_mismatch"):
        build(manifest(), [replace(s, mf_configuration_id="other")], [])
    with pytest.raises(ds.Refused, match="configuration_mismatch"):
        build(manifest(), [replace(s, de_configuration_id="other")], [])
    with pytest.raises(ds.Refused, match="no_era"):
        build(manifest(eras=(ds.Era(day(100), day(400), "KNOWN"),)), [s], [])
    with pytest.raises(ds.Refused, match="horizon_mismatch"):
        ds.build(manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE"),)), [s], [], tb(manifest()))
    with pytest.raises(ds.Refused, match="row_filter"):
        build(manifest(row_filter="all_lines"), [s], [])
    with pytest.raises(ds.Refused, match="horizon"):
        build(manifest(), [s], [replace(labels_for([s])[0], horizon=2)])
    with pytest.raises(ds.Refused, match="duplicate_label"):
        build(manifest(), [s], labels_for([s]) + labels_for([s])[:1])
    with pytest.raises(ds.Refused, match="unknown_label"):
        build(manifest(), [replace(s, survivorship="maybe")], [])


def test_mlk17_strata_use_the_eligible_same_t_universe_and_only_earlier_sessions():
    rows = {f"E{k}.N0000": line(f"E{k}.N0000", 0, k, feats={"turnover_avg_21": (D(k + 1), "fresh", None)})
            for k in range(3)}
    rows.update({f"I{k}.N0000": line(f"I{k}.N0000", 0, 10 + k, eligible="low_liquidity",
                                      feats={"turnover_avg_21": (D(0), "fresh", None)}) for k in range(6)})
    s = ds.SnapshotIn(1, day(0), t_of(0), "KNOWN", None, "mf", "point_in_time", "complete", rows, "f7", "de")
    d = build(manifest(), [s], labels_for([s]))
    assert {r.symbol: r.meta["liquidity_class"] for r in d.rows} == {"E0.N0000": "low", "E1.N0000": "mid",
                                                                     "E2.N0000": "high"}
    tags = st.volatility_tags([(i, D(i + 1)) for i in range(4)], st.RegimePolicy(min_history=3))
    assert [tags[i][0] for i in range(4)] == ["undefined", "undefined", "undefined", "high"]
    a = replace(snap(0), market={"mkt_ret_ew_21": (D("0.05"), "fresh", None)})
    b = replace(snap(1), market={"mkt_ret_ew_21": (D("-0.05"), "fresh", None)})
    got = {r.session: r.meta["regime_trend"] for r in build(manifest(), [a, b], labels_for([a, b])).rows}
    assert got == {day(0): "up", day(1): "down"}


# ------------------------------------------------------------------------------------------------ first M5-2 audit


def test_m1_other_modes_and_hs_in_a_session_are_set_aside_whatever_their_ids():
    av = manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE", H),))
    t = datetime.combine(day(0), time(23, 59, 59), COL)
    mine = replace(snap(0, mode="AVAILABLE", horizon=H, label="reconstructed", sid=1), cutoff=t)
    for theirs in (replace(mine, snapshot_id=2, horizon=H + timedelta(days=30)),     # higher id, another H
                   replace(mine, snapshot_id=2, mode="KNOWN", horizon=None, label_class="point_in_time")):
        for order in ([mine, theirs], [theirs, mine]):
            d = build(av, order, labels_for([mine]))
            assert {r.snapshot_id for r in d.rows} == {1} and d.report["snapshots_other_mode_or_h"] == 1


def test_m2_the_cutoff_rule_is_per_era_and_inclusive_at_its_instant():
    av = manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE", H),))
    last = datetime.combine(day(0), time(23, 59, 59, 999999), COL)
    at = replace(snap(0, mode="AVAILABLE", horizon=H, label="reconstructed"), cutoff=last)
    assert len(build(av, [at], labels_for([at])).rows) == 8                  # T exactly at the instant: used
    past = replace(at, cutoff=last + timedelta(microseconds=1))
    assert build(av, [past], labels_for([past])).report["snapshots_after_cutoff_rule"] == 1
    early = manifest(eras=(ds.Era(day(0), day(400), "KNOWN", None, time(18, 0)),))
    s18, s19 = snap(0, hour=18, sid=1), snap(0, hour=19, sid=2)
    d = build(early, [s18, s19], labels_for([s18, s19]))
    assert {r.snapshot_id for r in d.rows} == {1} and d.report["snapshots_after_cutoff_rule"] == 1
    tie_a, tie_b = snap(0, sid=5), snap(0, sid=6)                              # same T: the higher id
    assert {r.snapshot_id for r in build(manifest(), [tie_b, tie_a], labels_for([tie_a, tie_b])).rows} == {6}


def test_m3_labels_reaching_into_a_test_period_are_not_built():
    snaps, labs = world(12)
    m = manifest(withheld=((t_of(6), t_of(11)),))
    d = build(m, snaps, labs)
    p_start = t_of(6)
    assert all(lab.resolved_at < p_start for lab in d.labels.values())
    assert d.report["labels_reaching_test_period"] > 0
    pend = [replace(x, state="pending", resolved_at=None) if x.horizon == 21 else x for x in labs]
    d2 = build(m, snaps, pend)
    assert not any(k[2] == 21 for k in d2.labels)                              # pending before a later period
    d3 = build(manifest(), snaps, pend)
    assert any(k[2] == 21 for k in d3.labels)                                  # no period: kept as pending


def test_m4_a_snapshot_without_an_engine_run_is_recorded_not_mislabelled():
    from worker.engines import registry
    s = replace(snap(0), rows={k: replace(v, f7=None, de={}) for k, v in snap(0).rows.items()}, de_run=False)
    d = build(manifest(), [s], labels_for([s]))
    r = d.rows[0]
    assert r.features["f7.eligibility"] == (None, "missing", "no_engine_run")
    assert all(r.features[f"de.{e.engine_id}"] == (None, "missing", "no_engine_run") for e in registry.ENGINES)
    assert d.report["snapshots_without_de_run"] == 1
    conflict = snap(1, rows={"C.N0000": line("C.N0000", 1, 60, f7=None, link_state="issuer_link_conflict")})
    c = next(r for r in build(manifest(), [conflict], labels_for([conflict])).rows if r.symbol == "C.N0000")
    assert c.features["f7.eligibility"] == (None, "missing", "issuer_link_conflict")
    assert c.features["de.fund_growth"] == (D("0.5"), "scored", None)
    n = replace(conflict.rows["C.N0000"], de={"fund_growth": (None, "insufficient_inputs", "h" * 64)})
    nn = replace(conflict, rows={"C.N0000": n})
    x = next(r for r in build(manifest(), [nn], labels_for([nn])).rows if r.symbol == "C.N0000")
    assert x.features["de.fund_growth"] == (None, "insufficient_inputs", "insufficient_inputs")


def test_m6_counts_policy_and_admissible_states():
    snaps, labs = world(2)
    d = build(manifest(), snaps, labs[:-3])
    assert sum(d.report["labels_absent"].values()) == 3
    pol = st.RegimePolicy(min_history=2, max_history=2)
    tags = st.volatility_tags([(0, D(10)), (1, D(10)), (2, D(1)), (3, D(1)), (4, D(5))], pol)
    assert tags[4][0] == "high"                    # against the last two (1, 1) only, not all four
    fresh_only = manifest(admissible=("fresh",))
    s = snap(0, rows={"Z.N0000": line("Z.N0000", 0, 50, feats={"turnover_avg_21": (D(10 ** 9), "stale", None)})})
    z = next(r for r in build(fresh_only, [s], labels_for([s])).rows if r.symbol == "Z.N0000")
    assert z.features["mf.turnover_avg_21"] == (None, "stale", "stale") and z.meta["liquidity_class"] == "unknown"
    av = manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE", H),))
    u = snap(0, mode="AVAILABLE", horizon=H, label="reconstructed",
             rows={"U.N0000": line("U.N0000", 0, 61, f7=None)})
    u = replace(u, cutoff=datetime.combine(day(0), time(23, 59, 59), COL))
    assert next(r for r in build(av, [u], labels_for([u])).rows if r.symbol == "U.N0000").meta[
        "fundamental_coverage"] == "captured_only"


def test_version_binds_t_snapshot_and_metadata():
    snaps, labs = world(3)
    a = build(manifest(), snaps, labs)
    s2 = [replace(snaps[0], survivorship="survivors_only")] + snaps[1:]
    assert build(manifest(), s2, labs).version != a.version
    s3 = [replace(snaps[0], cutoff=snaps[0].cutoff - timedelta(hours=1))] + snaps[1:]
    assert build(manifest(), s3, labs).version != a.version



# ------------------------------------------------------------------------------------------------ M5-2 re-audit


def test_r1_ties_by_id_in_either_order_and_vol_median_from_admissible_eligible_values():
    a, b = snap(0, sid=5), snap(0, sid=6)
    for order in ([a, b], [b, a]):
        assert {r.snapshot_id for r in build(manifest(), order, labels_for([a, b])).rows} == {6}
    pol = st.RegimePolicy(min_history=1)
    base = [snap(i) for i in range(3)]
    ref = build(manifest(regime_policy=pol), base, labels_for(base))
    noisy = list(base)
    noisy[2] = replace(base[2], rows={**base[2].rows,
                                     "U.N0000": line("U.N0000", 2, 70, feats={"vol_21": (D(99), "untrusted", "x")}),
                                     "I.N0000": line("I.N0000", 2, 71, eligible="no_data",
                                                     feats={"vol_21": (D(99), "fresh", None)})})
    got = build(manifest(regime_policy=pol), noisy, labels_for(noisy))
    tag = {r.session: r.meta["regime_volatility"] for r in ref.rows}
    assert all(r.meta["regime_volatility"] == tag[r.session] for r in got.rows)


def test_r2_manifest_shape_and_imported_coverage():
    s = snap(0)
    for bad in (dict(date_from=day(5), date_to=day(1)),
                dict(eras=(ds.Era(day(0), day(500), "KNOWN"),)),
                dict(eras=(ds.Era(day(0), day(10), "KNOWN"), ds.Era(day(10), day(400), "KNOWN"))),
                dict(eras=(ds.Era(day(0), day(400), "KNOWN", None, time(13, tzinfo=timezone.utc)),)),
                dict(withheld=((t_of(9), t_of(5)),)),
                dict(withheld=((datetime(2024, 1, 5), datetime(2024, 1, 9)),))):
        with pytest.raises(ds.Refused, match="manifest_shape"):
            build(manifest(**bad), [s], [])
    imp = replace(snap(1, rows={"N.N0000": line("N.N0000", 1, 51, f7=None)}), imported=True)
    n = next(r for r in build(manifest(), [imp], labels_for([imp])).rows if r.symbol == "N.N0000")
    assert n.meta["fundamental_coverage"] == "captured_only"


def test_r3_the_version_binds_the_builders_code(monkeypatch):
    import worker.ml_dataset as pkg
    snaps, labs = world(2)
    a = build(manifest(), snaps, labs).version
    monkeypatch.setattr(pkg, "VERSION", "ml_dataset.x")
    assert build(manifest(), snaps, labs).version != a


def test_m54_engine_detail_reaches_the_dataset_only_for_scored_outputs():
    """M5-4 part 2: whether the engine used its full component set, and the components' shares, travel with a scored
    output; never into the null counts or an ML schema."""
    detail = {"fund_growth": (True, {"rev_g_cum": D("0.3"), "profit_g_cum": D("0.2")}, "1")}
    s = snap(0, rows={"Q.N0000": replace(line("Q.N0000", 0, 80), de_detail=detail)})
    d = build(manifest(), [s], labels_for([s]))
    q = next(r for r in d.rows if r.symbol == "Q.N0000")
    assert q.features["de.fund_growth.full"] == (True, "fresh", None)
    assert q.features["de.fund_growth.parts"][0] == {"rev_g_cum": D("0.3"), "profit_g_cum": D("0.2")}
    assert q.features["de.fund_growth.version"] == ("1", "fresh", None)
    other = next(r for r in d.rows if r.symbol == "S0.N0000")
    assert "de.fund_growth.full" not in other.features                      # no detail given: none invented
    unscored = replace(line("U.N0000", 0, 81), de={"fund_growth": (None, "insufficient_inputs", "h" * 64)},
                       de_detail=detail)
    u = next(r for r in build(manifest(), [snap(0, rows={"U.N0000": unscored})],
                              labels_for([snap(0, rows={"U.N0000": unscored})])).rows if r.symbol == "U.N0000")
    assert "de.fund_growth.full" not in u.features


def test_m54_detail_comes_from_the_stored_output_alone():
    """First M5-4 part 2 audit, M1 and m2: `full` from the output's own component list (weight-0 components never
    count; an n_below_min or an own-value absence makes it partial), the version from the output, never from the
    running registry."""
    from worker.ml_dataset.loader import _detail
    def out(components, present, version="1"):
        return {"engine_version": version, "components_present": present, "components": components}
    full = out([{"feature_id": "a", "weight": 1, "contribution": "0.2"},
                {"feature_id": "b", "weight": 1, "contribution": "0.4"},
                {"feature_id": "beta", "weight": 0, "absent": "n_below_min"}], ["a", "b"])
    f, parts, v = _detail("mkt_relative", full)
    assert f is True and v == "1" and parts == {"a": D("0.1"), "b": D("0.2")}
    partial = out([{"feature_id": "a", "weight": 1, "contribution": "0.2"},
                   {"feature_id": "b", "weight": 1, "absent": "n_below_min"}], ["a"])
    assert _detail("x", partial)[0] is False and _detail("x", partial)[1] == {"a": D("0.2")}
    own = out([{"feature_id": "a", "weight": 1, "contribution": "0.2"},
               {"feature_id": "b", "weight": 1, "absent": "missing"}], ["a"], version="2")
    assert _detail("fund_growth", own)[0] is False and _detail("fund_growth", own)[2] == "2"
    assert _detail("no_such_engine", full)[0] is True                       # the running registry is not consulted



# ------------------------------------------------------------------------------------------------ scoring flags


ERA = ds.AbsentEra(1, date(1995, 1, 2), date(2010, 12, 31), {"S1.N0000": ((date(2001, 1, 2), date(2008, 6, 30)),)})
CAND = ds.Candidate("tiq:bonus:x:1", "S1.N0000", "bonus", date(2005, 3, 1), date(2005, 4, 15))
EV = ds.ScoringEvidence((ERA,), (CAND,))


def test_review_m2_candidate_event_is_an_unexplained_move_meeting_an_unentered_window():
    f = lambda s, st, v, sym="S1.N0000": ds.scoring_flags(sym, s, st, v, EV)     # noqa: E731
    u = "unexplained_move_in_horizon"
    move = {"reference_px_session": "2005-02-25", "target_session": "2005-03-04"}
    assert f(date(2005, 2, 25), u, move) == ("candidate_event",)
    assert f(date(2005, 2, 25), u, move, sym="S2.N0000") == ()                     # another security's event
    assert f(date(2005, 2, 25), "resolved", move) == ()                              # only unexplained moves
    # (s_D, s_F] is half-open at s_D: a window ending at s_D does not meet it; one starting at s_F does
    assert f(date(2005, 4, 15), u, {"reference_px_session": "2005-04-15", "target_session": "2005-04-20"}) == ()
    assert f(date(2005, 2, 20), u, {"reference_px_session": "2005-02-20", "target_session": "2005-03-01"}) == \
        ("candidate_event",)
    assert f(date(2005, 2, 1), u, {"reference_px_session": "2005-02-01", "target_session": "2005-02-28"}) == ()


def test_review_m2_era_row_absent_where_security_complete_does_not_speak():
    f = lambda s, st, v, sym="S1.N0000": ds.scoring_flags(sym, s, st, v, EV)     # noqa: E731
    t, m = "terminated", "unresolvable_missing_price"
    left = lambda d: {"evidence": ["left_universe"], "left_session": d}           # noqa: E731
    assert f(date(1998, 3, 2), t, left("1998-03-10")) == ("era_row_absent",)        # before 2001: no coverage
    assert f(date(2009, 3, 2), t, left("2009-03-10")) == ()                         # after its last row: it left
    assert f(date(1998, 3, 2), t, {"evidence": ["merger", "s1"], "left_session": "1998-03-10"}) == ()   # an event
    assert f(date(2011, 3, 2), t, left("2011-03-10")) == ()                         # outside the batch
    assert f(date(1998, 3, 2), t, left("1998-03-10"), sym="S9.N0000") == ("era_row_absent",)   # no range at all
    assert f(date(1998, 3, 2), m, {"evidence": ["no_reference_price_at_F"], "target_session": "1998-03-09"}) == \
        ("era_row_absent",)
    assert f(date(1998, 3, 2), m, {"evidence": ["no_reference_price_at_D"], "target_session": "2009-03-09"}) == \
        ("era_row_absent",)                                                         # D is the absent session
    assert f(date(2009, 3, 2), m, {"evidence": ["no_reference_price_at_F"], "target_session": "2009-03-09"}) == ()
    assert f(date(1998, 3, 2), "resolved", left("1998-03-10")) == ()


def test_review_m2_flags_reach_the_dataset_its_report_its_version_and_scoring():
    pytest.importorskip("numpy")                  # ml_models needs the ML libraries (Linux CI image only)
    from worker.evaluation_metrics import scoring
    from worker.ml_models import data
    m = manifest()
    snaps = [snap(i, n=2) for i in range(3)]
    s1 = snaps[1]
    labs = []
    for x in labels_for(snaps):
        if (x.snapshot_id, x.symbol, x.horizon) == (s1.snapshot_id, "S1.N0000", 5):
            x = replace(x, state="unexplained_move_in_horizon",
                        values={"ret": "-0.3", "direction": "fall", "reference_px_session": day(1).isoformat(),
                                "target_session": day(6).isoformat()})
        labs.append(x)
    cand = ds.Candidate("c1", "S1.N0000", "bonus", day(3), day(9))
    plain = ds.build(m, snaps, labs, tb(m))
    flagged = ds.build(m, snaps, labs, tb(m), ds.ScoringEvidence((), (cand,)))
    assert plain.labels[("S1.N0000", day(1), 5)].flags == ()
    assert flagged.labels[("S1.N0000", day(1), 5)].flags == ("candidate_event",)
    assert flagged.report["labels_by_flag"][5] == {"candidate_event": 1}
    assert flagged.version != plain.version                                         # the flags are bound
    other = ds.build(m, snaps, labs, tb(m), ds.ScoringEvidence((), (replace(cand, candidate_id="c2",
                                                                            window_from=day(30), window_to=day(31)),)))
    assert other.version != plain.version and other.labels[("S1.N0000", day(1), 5)].flags == ()   # evidence bound
    rows = data.label_rows(flagged, [("S1.N0000", day(1))], 5)
    assert rows[0].candidate_event and not rows[0].era_row_absent
    p = scoring.population(rows)
    assert p.counts["unexplained_candidate_event"] == 1 and p.unexplained_raw == []
