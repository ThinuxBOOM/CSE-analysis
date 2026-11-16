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


def world(n_sessions=30, **kw):
    snaps = [snap(i, **kw) for i in range(n_sessions)]
    return snaps, labels_for(snaps)


# ------------------------------------------------------------------------------------------------ determinism


def test_d1_deterministic_and_order_free_and_bound_to_its_sources():
    snaps, labs = world()
    a = ds.build(manifest(), snaps, labs)
    b = ds.build(manifest(), list(reversed(snaps)), list(reversed(labs)))
    assert a.version == b.version and a.rows == b.rows and a.labels == b.labels
    assert len(a.rows) == 30 * 8 and len(a.labels) == 30 * 8 * 4
    changed = list(labs)
    changed[5] = replace(changed[5], row_hash="f" * 64)
    assert ds.build(manifest(), snaps, changed).version != a.version
    s2 = list(snaps)
    s2[3] = replace(s2[3], rows={**s2[3].rows, "S0.N0000": replace(s2[3].rows["S0.N0000"], row_hash="e" * 64)})
    assert ds.build(manifest(), s2, labs).version != a.version
    assert ds.build(manifest(label_definition_version="targets.2"), snaps, labs).version != a.version


def test_d2_labels_are_apart_and_features_carry_sources():
    snaps, labs = world(3)
    d = ds.build(manifest(), snaps, labs)
    r = d.rows[0]
    assert not any(k.startswith(("label", "target")) for k in r.features)
    assert r.sources[0] == snaps[0].rows[r.symbol].row_hash and len(r.sources) == 3
    assert set(r.features) >= {"mf.turnover_avg_21", "f7.roe_end", "f7.eligibility", "de.fund_growth"}


# ------------------------------------------------------------------------------------------------ the cutoff rule


def test_c1_one_snapshot_per_session_the_last_before_the_rule():
    early, late = snap(0, hour=16, sid=1), snap(0, hour=20, sid=2)
    after = replace(snap(0, sid=3), cutoff=datetime.combine(day(1), time(0, 30), COL))    # past 23:59:59 on D
    d = ds.build(manifest(), [early, late, after], labels_for([early, late, after]))
    assert {r.snapshot_id for r in d.rows} == {2}
    assert d.report["snapshots_superseded_in_session"] == 1 and d.report["snapshots_after_cutoff_rule"] == 1
    out = replace(snap(500), session=day(500))
    assert ds.build(manifest(), [late, out], labels_for([late])).report["snapshots_outside_range"] == 1


def test_c2_ineligible_lines_are_counted_never_silent():
    s = snap(0, rows={"X.N0000": line("X.N0000", 0, 99, eligible="low_liquidity"),
                      "Y.N0000": line("Y.N0000", 0, 98, eligible="no_data")})
    d = ds.build(manifest(), [s], labels_for([s]))
    assert d.report["excluded_by_eligibility"] == {"low_liquidity": 1, "no_data": 1}
    assert {r.symbol for r in d.rows} == {f"S{k}.N0000" for k in range(8)}


def test_c3_withheld_test_labels_are_not_built():
    snaps, labs = world(10)
    m = manifest(withheld=((t_of(5), t_of(9)),))
    d = ds.build(m, snaps, labs)
    assert all(k[1] < day(5) for k in d.labels)
    assert d.report["labels_withheld_test_period"] == 5 * 8 * 4
    assert len(d.rows) == 80                                   # the feature rows stay


# ------------------------------------------------------------------------------------------------ MLK-18


def test_mlk18_the_worst_label_is_carried_and_a_second_build_refused():
    a = [snap(i) for i in range(5)]
    b = [snap(i, mode="AVAILABLE", horizon=H, label="reconstructed", surv="survivors_only") for i in range(5, 10)]
    c = [snap(i, mode="AVAILABLE", horizon=H, label="reconstructed", surv="unknown") for i in range(10, 12)]
    m = manifest(eras=(ds.Era(day(0), day(4), "KNOWN"), ds.Era(day(5), day(400), "AVAILABLE", H)))
    d = ds.build(m, a + b + c, labels_for(a + b + c))
    assert (d.label_class, d.survivorship, d.fundamental_coverage) == ("reconstructed", "unknown", "all")
    assert ds.build(m, a, labels_for(a)).label_class == "point_in_time"
    assert ds.build(m, a + b, labels_for(a + b)).survivorship == "survivors_only"
    cov = [replace(s, rows={k: replace(v, f7=replace(v.f7, fundamental_coverage="captured_only"))
                            for k, v in s.rows.items()}) for s in b]
    assert ds.build(m, a + cov, labels_for(a + cov)).fundamental_coverage == "captured_only"
    other = labels_for(a[:1], build=8)
    with pytest.raises(ds.Refused, match="second_target_build"):
        ds.build(m, a, labels_for(a) + other)


# ------------------------------------------------------------------------------------------------ MLK-17


def test_mlk17_strata_at_t_unchanged_by_later_data():
    pol = st.RegimePolicy(min_history=10)
    snaps, labs = world(40)
    base = ds.build(manifest(regime_policy=pol), snaps[:25], labels_for(snaps[:25]))
    later = []
    for s in snaps[25:]:                                         # later sessions with extreme values
        rows = {k: replace(v, features={**v.features, "vol_21": (D(9), "fresh", None),
                                        "turnover_avg_21": (D(-1) if k == "S0.N0000" else D(10 ** 9), "fresh", None)})
                for k, v in s.rows.items()}
        later.append(replace(s, rows=rows))
    full = ds.build(manifest(regime_policy=pol), snaps[:25] + later, labels_for(snaps[:25] + later))
    at = {r.key: r for r in full.rows}
    for r in base.rows:
        assert at[r.key].meta == r.meta and at[r.key].features == r.features
    assert {r.meta["regime_volatility"] for r in base.rows} - {"undefined"}       # defined after 10 sessions
    assert any(r.meta["regime_volatility"] == "high" for r in full.rows if r.session >= day(25))
    for x in (0, 20, 21, 251, 252):                              # model age depends on T and C_train only
        assert st.age_stratum(x) == st.age_stratum(x)
    with pytest.raises(ValueError):
        st.age_stratum(-1)


def test_mlk17_liquidity_terciles_share_ties_and_unknowns():
    t = {"a": (D(1), "fresh", None), "b": (D(2), "fresh", None), "c": (D(2), "fresh", None),
         "d": (D(3), "fresh", None), "e": (None, "missing", "no_trades"), "f": (D(9), "untrusted", "x")}
    c = st.liquidity_classes(t)
    assert c["b"] == c["c"] and c["e"] == c["f"] == "unknown" and c["a"] == "low" and c["d"] == "high"


def test_regime_trend_needs_the_market_aggregate():
    snaps, labs = world(3)
    assert {r.meta["regime_trend"] for r in ds.build(manifest(), snaps, labs).rows} == {"undefined"}
    assert st.trend_tag(None) == ("undefined", "market_aggregate_not_stored")
    assert st.trend_tag((D("0.05"), "fresh", None))[0] == "up"
    assert st.trend_tag((D("-0.05"), "stale", None))[0] == "down"
    assert st.trend_tag((D("0.0"), "fresh", None))[0] == "flat"
    assert st.trend_tag((D("0.05"), "unlabelled", "pending_action")) == ("undefined", "pending_action")
    s = replace(snaps[0], market={"mkt_ret_ew_21": (D("0.05"), "fresh", None)})
    assert {r.meta["regime_trend"] for r in ds.build(manifest(), [s], labels_for([s])).rows} == {"up"}


# ------------------------------------------------------------------------------------------------ MLK-20


def test_mlk20_upstream_nulls_and_refusals_arrive_as_nulls_with_their_reason():
    bad_f7 = ds.F7In({"earnings_yield_eps": (D("0.08"), "untrusted", "eps_basis_ambiguous"),
                      "eps_latest_fy": (None, "conflicting", "conflicting"),
                      "pe_eps": (None, "missing", "no_close_since_document"),
                      "roe_end": (D("0.1"), "partial", "has_discontinued")}, "stale", ("c" * 64,))
    s = snap(0, rows={"Z.N0000": line("Z.N0000", 0, 50, f7=bad_f7, feats={
        "ret_21": (D("0.3"), "unlabelled", "pending_action"), "ret_5": (None, "missing", "unknown_price_in_window"),
        "vol_21": (D("0.02"), "conflicting", "conflicting_input")})})
    d = ds.build(manifest(), [s], labels_for([s]))
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
    n = next(r for r in ds.build(manifest(), [nolink], labels_for([nolink])).rows if r.symbol == "N.N0000")
    assert n.features["f7.eligibility"] == (None, "missing", "issuer_link_absent")
    assert not any(k.startswith("f7.") and k != "f7.eligibility" for k in n.features)


def test_mlk20_label_states_pass_through_and_a_label_resolved_by_t_is_refused():
    snaps, labs = world(2)
    labs = [replace(x, state="unresolvable_missing_price", values={}) if x.symbol == "S1.N0000" else x for x in labs]
    d = ds.build(manifest(), snaps, labs)
    assert d.labels[("S1.N0000", day(0), 5)].state == "unresolvable_missing_price"
    assert d.report["labels_by_state"][5]["unresolvable_missing_price"] == 2
    leak = [replace(labs[0], resolved_at=snaps[0].cutoff)] + labs[1:]
    with pytest.raises(ds.Refused, match="label_before_t"):
        ds.build(manifest(), snaps, leak)


def test_mlk20_rows_at_t_unchanged_by_plants_after_t():
    snaps, labs = world(6)
    base = ds.build(manifest(), snaps[:3], labels_for(snaps[:3]))
    plant = [replace(s, rows={**s.rows, "NEW.N0000": line("NEW.N0000", 9, 77)}) for s in snaps[3:]]
    full = ds.build(manifest(), snaps[:3] + plant, labels_for(snaps[:3] + plant))
    at = {r.key: r for r in full.rows}
    assert all(at[r.key] == r for r in base.rows)
    assert all(full.labels[k] == v for k, v in base.labels.items())


# ------------------------------------------------------------------------------------------------ refusals (MLK-24)


def test_refusals():
    s = snap(0)
    with pytest.raises(ds.Refused, match="two_h_values"):
        ds.build(manifest(eras=(ds.Era(day(0), day(9), "AVAILABLE", H),
                                ds.Era(day(10), day(400), "AVAILABLE", H + timedelta(days=1)))), [s], [])
    with pytest.raises(ds.Refused, match="basis_not_raw"):
        ds.build(manifest(), [replace(s, basis_raw=False)], [])
    with pytest.raises(ds.Refused, match="mode_not_point_in_time"):
        ds.build(manifest(), [replace(s, mode="CURRENT")], [])
    with pytest.raises(ds.Refused, match="era_mismatch"):
        ds.build(manifest(), [replace(s, mode="AVAILABLE", horizon=H)], [])
    with pytest.raises(ds.Refused, match="configuration_mismatch"):
        ds.build(manifest(), [replace(s, mf_configuration_id="other")], [])
    with pytest.raises(ds.Refused, match="configuration_mismatch"):
        ds.build(manifest(), [replace(s, de_configuration_id="other")], [])
    with pytest.raises(ds.Refused, match="no_era"):
        ds.build(manifest(eras=(ds.Era(day(100), day(400), "KNOWN"),)), [s], [])
    with pytest.raises(ds.Refused, match="horizon_mismatch"):
        ds.build(manifest(eras=(ds.Era(day(0), day(400), "AVAILABLE"),)), [s], [])
    with pytest.raises(ds.Refused, match="unknown_label"):
        ds.build(manifest(), [replace(s, survivorship="maybe")], [])


def test_mlk17_strata_use_the_eligible_same_t_universe_and_only_earlier_sessions():
    rows = {f"E{k}.N0000": line(f"E{k}.N0000", 0, k, feats={"turnover_avg_21": (D(k + 1), "fresh", None)})
            for k in range(3)}
    rows.update({f"I{k}.N0000": line(f"I{k}.N0000", 0, 10 + k, eligible="low_liquidity",
                                      feats={"turnover_avg_21": (D(0), "fresh", None)}) for k in range(6)})
    s = ds.SnapshotIn(1, day(0), t_of(0), "KNOWN", None, "mf", "point_in_time", "complete", rows, "f7", "de")
    d = ds.build(manifest(), [s], labels_for([s]))
    assert {r.symbol: r.meta["liquidity_class"] for r in d.rows} == {"E0.N0000": "low", "E1.N0000": "mid",
                                                                     "E2.N0000": "high"}
    tags = st.volatility_tags([(i, D(i + 1)) for i in range(4)], st.RegimePolicy(min_history=3))
    assert [tags[i][0] for i in range(4)] == ["undefined", "undefined", "undefined", "high"]
    a = replace(snap(0), market={"mkt_ret_ew_21": (D("0.05"), "fresh", None)})
    b = replace(snap(1), market={"mkt_ret_ew_21": (D("-0.05"), "fresh", None)})
    got = {r.session: r.meta["regime_trend"] for r in ds.build(manifest(), [a, b], labels_for([a, b])).rows}
    assert got == {day(0): "up", day(1): "down"}
