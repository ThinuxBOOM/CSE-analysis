"""
M5-8 (design §15): an end-to-end synthetic walk-forward rehearsal of Phase 5, recorded. One synthetic world (a
planted signal of stated size in `mf.x`, irrelevant `mf.z`, a common date factor, per-security liquidity classes and
volatility, labels that resolve h sessions after T) goes through every step that exists:

  dataset (M5-2 shapes) -> expanding walk-forward folds and inner slices (M5-3) -> a `linear` model fitted on each
  fold's fit slice through its FitScope (M5-4) -> vector scaling on the calibrate slice and class-conditional
  conformal intervals on the conformal slice (M5-5) -> the validated domain from the fit slice and §11.2's refusals
  on every validation row (M5-7) -> per-fold metrics, reliability and coverage reports -> the gate test of the pooled
  date-level loss differences against `base_prior` (M5-1) -> MLK-23's size study at the dependence estimated on
  training rows only -> (optionally) the registry (M5-6): configuration, synthetic dataset, protocol, run, fold models
  with their artifacts, metrics, and the final model, whose validation gate 1 must refuse (a synthetic dataset is
  never validated).

`run` returns a report: every artifact hash, the prediction hash per fold, the metrics, the gate test, the size study
and the refusal counts; `report_hash` binds it. Run twice with the same seed, it gives the same hash (§15's exit:
"the whole chain, reproducible"). No network, no real data.
"""
import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from worker.evaluation_metrics import bootstrap, metrics, size
from worker.ml_dataset import dataset as ds
from worker.ml_models import baselines, calibration, data, linear
from worker.ml_protocol import folds as fd
from worker.ml_route1 import domain as dm

COLOMBO = timezone(timedelta(hours=5, minutes=30))
CLASSES = ("rise", "flat", "fall")
LIQ = ("low", "mid", "high")


@dataclass(frozen=True)
class World:
    seed: int = 1
    sessions: int = 640
    securities: int = 24
    horizon: int = 5
    signal: float = 0.6            # the planted effect of mf.x on the latent return (0: no signal)
    date_factor: float = 0.5       # a common shock per date
    persistence: float = 0.3       # serial dependence of the common shock


def _sessions(n):
    out, d = [], date(2022, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _t(d):
    return datetime.combine(d, time(18, 0), COLOMBO)


def world(w: World):
    """(Dataset, calendar of T instants, sessions)."""
    rnd = random.Random(w.seed)
    days = _sessions(w.sessions)
    shock, shocks = 0.0, []
    for _ in days:
        shock = w.persistence * shock + rnd.gauss(0, 1)
        shocks.append(shock)
    sec_vol = {i: 0.008 + 0.004 * (i % 3) for i in range(w.securities)}
    rows, labels = [], {}
    for j, d in enumerate(days):
        for i in range(w.securities):
            sym = f"S{i:02d}.N0000"
            x, z = rnd.gauss(0, 1), rnd.gauss(0, 1)
            vol = sec_vol[i]
            lat = w.signal * x + rnd.gauss(0, 1) + w.date_factor * shocks[j]
            ret = vol * lat
            feats = {"mf.x": (Decimal(str(round(x, 6))), "fresh", None),
                     "mf.z": (Decimal(str(round(z, 6))), "fresh", None),
                     "mf.vol_21": (Decimal(str(round(vol, 6))), "fresh", None)}
            regime = "stress" if abs(shocks[j]) > 1.5 else "calm"
            rows.append(ds.Row(sym, d, _t(d), j, feats, {"liquidity_class": LIQ[i % 3], "regime": regime},
                               (f"{j:032x}{i:032x}",)))
            if j + w.horizon < len(days):
                direction = "rise" if lat > 0.5 else "fall" if lat < -0.5 else "flat"
                labels[(sym, d, w.horizon)] = ds.Label(
                    "resolved", {"direction": direction, "ret": str(round(ret, 10)),
                                 "volatility": str(round(abs(ret), 10)), "volatility_state": "resolved"},
                    _t(days[j + w.horizon]), hashlib.sha256(f"{sym}{d}".encode()).hexdigest())
    manifest = ds.Manifest("rehearsal", "rehearsal", "rehearsal", days[0], days[-1],
                           (ds.Era(days[0], days[-1], "AVAILABLE", _t(days[-1])),), 0, "rehearsal")
    version = hashlib.sha256(json.dumps(["rehearsal", w.__dict__], sort_keys=True).encode()).hexdigest()
    dataset = ds.Dataset(manifest, version, tuple(rows), labels, "reconstructed", "complete", "all",
                         {"rows": len(rows), "labels": len(labels), "synthetic": True})
    return dataset, fd.Calendar([_t(d) for d in days]), days


def _fd_rows(dataset, h):
    return [fd.Row(r.key, r.t, dataset.labels[(r.symbol, r.session, h)].resolved_at
                   if (r.symbol, r.session, h) in dataset.labels else None) for r in dataset.rows]


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _loss(probs, actual):
    return -math.log(max(probs[CLASSES.index(actual)], 1e-15))


def run(w: World = World(), *, folds_n=5, level=0.05, size_seeds=200, keep=None):
    """The rehearsal; returns its report (a plain dict) with `report_hash`. `keep`, a dict, receives the fold
    artifacts, the fold objects and the dataset for `record`."""
    dataset, cal, days = world(w)
    h = w.horizon
    by_key = {r.key: r for r in dataset.rows}
    frows = _fd_rows(dataset, h)
    first = cal.sessions[260]
    v_ends = [cal.sessions[260 + 70 * k] for k in range(1, folds_n + 1)]
    folds = fd.walk_forward(first, v_ends, h, cal, grace=0, embargo=1)
    report = {"world": w.__dict__, "folds": [], "refusals": {}}
    pos = {t: i for i, t in enumerate(cal.sessions)}
    S = cal.sessions
    # two incident flags (§11.2): one recorded before the days it covers (those rows are blocked), one recorded
    # long after (those rows are predicted, flagged `incident_recorded_later`: no hindsight removal)
    incidents = ((S[400], S[403], S[399]), (S[500], S[503], S[600]))
    decisions, regimes = [], []
    diffs, dates, train_series, artifacts = [], [], None, []
    for k, fold in enumerate(folds, start=1):
        roles, unused = fold.assign(frows, cal)
        train = roles[fd.TRAINING]
        c = fold.c_train
        s1, s2, s3 = cal.shift(c, -150), cal.shift(c, -100), cal.shift(c, -50)
        slices = fd.Slices(s1, s2, s3, c, h, grace=0, embargo=1).assign(train, cal)
        rows_of = lambda name: [by_key[r.key] for r in slices[name]]          # noqa: E731
        fit_scope = fd.FitScope.of(slices, fd.FIT, h)
        art = linear.fit(data.train_set(dataset, fit_scope, rows_of(fd.FIT), h), seed=w.seed)
        schema = linear.schema_of(art)
        artifacts.append(art)

        def predict(rs):
            X, keys = data.matrix(rs, schema)
            return linear.predict(art, X, keys, horizon=h)
        cal_rows, conf_rows = rows_of(fd.CALIBRATE), rows_of(fd.CONFORMAL)
        scaling = calibration.fit_vector_scaling(fd.FitScope.of(slices, fd.CALIBRATE, h), cal_rows,
                                                 predict(cal_rows).probs, dataset, h)
        conformal = calibration.fit_intervals(fit_scope, rows_of(fd.FIT), fd.FitScope.of(slices, fd.CONFORMAL, h),
                                              conf_rows, predict(conf_rows).ret, dataset, h, level=0.9)
        prior = baselines.fit(data.train_set(dataset, fd.FitScope("training", frozenset(r.key for r in train), h),
                                             [by_key[r.key] for r in train], h))
        # the validated domain from the fit slice; every validation row through §11.2's refusals
        envelope, bounds = {}, {}
        for f in ("mf.x", "mf.z"):
            env, b = dm.envelope_of([float(r.features[f][0]) for r in rows_of(fd.FIT)], 0.1)
            envelope[f], bounds[f] = env, b
        domain = dm.Domain(("reconstructed",), "elig.1", {"mf.x": "1", "mf.z": "1"}, h, ("synthetic",), envelope,
                           bounds, ("mf.x",), training_cutoff=fold.c_train)
        val = [by_key[r.key] for r in roles[fd.VALIDATION]]
        decided = []
        for r in val:
            st, reason, flags, _c = dm.decide(dm.Row({f: float(r.features[f][0]) for f in ("mf.x", "mf.z")},
                                                     label_class="reconstructed", industry_group="synthetic",
                                                     eligibility_policy="elig.1",
                                                     feature_versions={"mf.x": "1", "mf.z": "1"}),
                                              domain, designated=True, routed=True,
                                              age_sessions=pos[r.t] - pos[fold.c_train], incidents=incidents,
                                              cutoff=r.t, horizon=h)
            report["refusals"][reason or st] = report["refusals"].get(reason or st, 0) + 1
            for fl in flags:
                report["refusals"][fl] = report["refusals"].get(fl, 0) + 1
            decisions.append((st, reason, flags))
            regimes.append(r.meta["regime"])
            if st == "predicted":
                decided.append(r)
        p = predict(decided)
        calibrated = calibration.apply_scaling(scaling, p.probs, horizon=h)
        ivals, _flags = calibration.intervals(conformal, decided, p.ret, horizon=h)
        base = baselines.predict(prior, decided, h)["base_prior"]
        actual = [dataset.labels[(r.symbol, r.session, h)].values["direction"] for r in decided]
        model_loss = [_loss(q, a) for q, a in zip(calibrated, actual)]
        base_loss = [_loss(q, a) for q, a in zip(base.probs, actual)]
        per_date = metrics.by_date([r.session for r in decided], [m - b for m, b in zip(model_loss, base_loss)])
        diffs += list(per_date.values())
        dates += list(per_date)
        if train_series is None:                   # MLK-23: dependence estimated on training rows only
            tr = [by_key[r.key] for r in train]
            tr_ret = metrics.by_date([r.session for r in tr],
                                     [float(dataset.labels[(r.symbol, r.session, h)].values["ret"]) for r in tr])
            train_series = list(tr_ret.values())
        cov = calibration.coverage_report(dataset, decided, ivals, h, 0.9, 0.05)
        rel = calibration.reliability_report(dataset, decided, calibrated, h)
        report["folds"].append({
            "fold": k, "c_train": str(fold.c_train), "v_end": str(fold.v_end),
            "rows": {role: len(v) for role, v in roles.items()}, "unused": unused,
            "slices": {name: len(v) for name, v in slices.items()},
            "artifact": art.sha256, "scaling": scaling.sha256, "conformal": conformal.sha256,
            "predictions": _hash([list(map(lambda v: round(v, 12), q)) for q in calibrated]),
            "log_loss": round(math.fsum(model_loss) / len(model_loss), 9),
            "base_log_loss": round(math.fsum(base_loss) / len(base_loss), 9),
            "coverage": {g: None if v["coverage"] is None else round(v["coverage"], 6) for g, v in cov.items()},
            "ece": round(rel["overall"][0], 9)})
    report["refusal_rates"] = {g: {k: round(v, 9) for k, v in d.items()}
                               for g, d in sorted(dm.refusal_rates(decisions, regimes).items())}
    test = bootstrap.gate_test(diffs, delta=0.0, h=h, level=level, seed=w.seed)
    report["gate_test"] = {"state": test.state, "rejects": test.rejects, "p_value": test.p_value,
                           "block_length": test.block_length, "replicates": test.replicates, "dates": len(diffs)}
    sz = size.size_study(train_series, len(diffs), h=h, level=level, seeds=size_seeds, block=test.block_length,
                         seed=w.seed)
    report["size_study"] = {"phi": round(sz.dependence.phi, 9), "arch": round(sz.dependence.arch, 9),
                            "tested": sz.tested, "rejected": sz.rejected, "region": list(sz.region),
                            "shown": sz.shown}
    report["report_hash"] = _hash({k: v for k, v in report.items()})
    if keep is not None:
        keep.update(artifacts=artifacts, folds=folds, dataset=dataset, test=test, sz=sz)
    return report


def plan_of(report, w: World, block_length):
    """§5.5's plan as the rehearsal's schedule gives it (dates with usable rows, distinct securities)."""
    from worker.ml_registry import sufficiency as sf
    f1 = report["folds"][0]["slices"]
    per_date = lambda n: n // w.securities                                    # noqa: E731
    return sf.Plan(w.horizon, block_length, per_date(f1[fd.FIT]),
                   tuple(per_date(f1[s]) for s in (fd.SELECT, fd.CALIBRATE, fd.CONFORMAL)),
                   tuple(per_date(f["rows"][fd.VALIDATION]) for f in report["folds"]), 0,
                   tuple(w.securities for _ in report["folds"]), w.securities)


def record(conn, report, kept, w: World, policy_id, code_version="rehearsal.1"):
    """The rehearsal in the registry (M5-6), as a synthetic dataset: configuration, dataset, protocol, run, each
    fold's model with its artifact and prediction hash, per-fold metrics, and the model of the last fold registered
    as a candidate. Then `validate` with the evidence the rehearsal itself gives (its sufficiency, folds, gate test
    and size study): the report is written, and the model is never `validated` (gate 1, enforced by the database,
    refuses any model of a synthetic dataset; a small world is also `exploratory` under §5.5). Returns the ids and
    the validation report."""
    from worker.ml_registry import gates as gt, store
    from worker.ml_registry import sufficiency as sf
    cid = store.register_configuration(conn, "model", {"adapter": "linear", "horizon": w.horizon,
                                                       "rehearsal": w.__dict__})
    did = store.register_dataset(conn, kept["dataset"], True)
    folds = kept["folds"]
    last = folds[-1].v_end
    t_start = last + timedelta(days=60)
    decl = {"family": [cid], "primary": ["direction", w.horizon], "nomination": "best_validation_loss",
            "embargo_sessions": 1, "policies": [policy_id], "point_in_time_share": 0.0,
            "criterion": {"form": "gate_test_every_baseline_bonferroni_over_nominees"},
            "folds": [{"v_start": f.v_start.isoformat(), "v_end": f.v_end.isoformat()} for f in folds],
            "test_periods": [[t_start.isoformat(), (t_start + timedelta(days=365)).isoformat()]],
            "rehearsal": True}
    block = report["gate_test"]["block_length"]
    plan = plan_of(report, w, block)
    pid, sufficiency, reasons = store.declare_protocol(conn, decl, plan)
    rid = store.start_run(conn, pid, cid, did, "direction", w.horizon, code_version)
    for f, fr, art in zip(folds, report["folds"], kept["artifacts"]):
        store.record_fold_model(conn, rid, fr["fold"], {"c_train": fr["c_train"], "v_end": fr["v_end"]}, art,
                                fr["predictions"])
        store.record_metrics(conn, "fold", "overall", {"log_loss": fr["log_loss"],
                                                       "base_log_loss": fr["base_log_loss"], "ece": fr["ece"],
                                                       "coverage": fr["coverage"]}, run_id=rid, fold_no=fr["fold"])
    store.run_event(conn, rid, "finished", {"report_hash": report["report_hash"]})
    mid = store.register_model(conn, kind="linear", configuration_id=cid, output="direction", horizon=w.horizon,
                               dataset_id=did, protocol_id=pid, training_cutoff=folds[-1].c_train,
                               evidence_label_class="reconstructed", artifact=kept["artifacts"][-1],
                               environment_json={"rehearsal": report["report_hash"]},
                               domain={"features": ["mf.x", "mf.z"]})
    evidence = gt.ValidationEvidence(
        protocol_sufficiency=sufficiency, declared_before_first_run=True, in_family=True, leakage_suite_passed=True,
        code_version=code_version,
        folds=tuple(gt.FoldEvidence(fr["fold"], True, fr["ece"] is not None,
                                    all(v is not None for v in fr["coverage"].values()), True)
                    for fr in report["folds"]),
        declared_folds=len(folds), baseline_results={"base_prior": kept["test"]}, required_baselines=("base_prior",),
        size_shown=kept["sz"].shown, policy_status={}, survivorship="complete", survivorship_waiver=None,
        domain_recorded=True, ages_evaluated=(0, 70), multiplicity=1, horizon=w.horizon)
    try:
        rep = store.validate(conn, mid, evidence, code_version)
        refused = None
    except Exception as e:                       # the database's gate 1, when gates 2 to 8 pass
        rep, refused = None, str(e)
    return {"configuration": cid, "dataset": did, "protocol": pid, "sufficiency": sufficiency,
            "sufficiency_reasons": reasons, "run": rid, "model": mid, "validation": rep, "refused": refused,
            "status": store.status(conn, mid)}
