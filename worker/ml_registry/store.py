"""
The registry's reads and writes (migration 0021). Every write is one transaction; a refusal by the database leaves
nothing. The worker login writes research records; `promote` runs on the owner path (cse_migrator acting as
cse_owner), the only way a production model and its designation are written.

Test periods (§5.4), enforced here where the database cannot:
  - `unspent_periods` and `withheld_for`: a dataset built for training or validation must withhold every declared,
    unspent test period its range touches (the M5-2 carry): `check_manifest` refuses one that does not (MLK-4);
  - `check_rows_outside_unspent`: no protocol takes training or validation rows from another protocol's declared,
    unspent interval (MLK-5);
  - `multiplicity`: the count of configurations with validation evidence on overlapping sessions, which replaces the
    declared family size when larger (MLK-5);
  - `access`: the one access (MLK-4): the period is this protocol's and unspent, the nominees' final fits exist and
    their test-period predictions are frozen and hashed, then the access row, then the labels and the result.
"""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from worker.ml_dataset import dataset as ds

from . import gates as gt
from . import sufficiency as sf


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def _write(conn, work):
    try:
        with conn.cursor() as cur:
            out = work(cur)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise


def _read(conn, work):
    try:
        with conn.cursor() as cur:
            return work(cur)
    finally:
        conn.rollback()


# ------------------------------------------------------------------------------------------------ registrations


def register_configuration(conn, kind, body, status="provisional", evidence_cutoff=None):
    """Content-addressed; inserting an existing configuration is a no-op. Returns its id."""
    js = canonical({**body, "kind": kind, "status": status})
    cid = sha(js)
    _write(conn, lambda cur: cur.execute(
        "insert into ml_configurations (configuration_id, configuration_json, kind, status, evidence_cutoff) values "
        "(%s, %s, %s, %s, %s) on conflict (configuration_id) do nothing", (cid, js, kind, status, evidence_cutoff)))
    return cid


def register_dataset(conn, dataset, synthetic):
    """An ml_datasets row for a built dataset (M5-2); its manifest and report as canonical JSON. Returns its id.
    MLK-4 (M5-6 audit M10): a dataset that does not withhold every unspent test period its range touches is refused."""
    check_manifest(conn, dataset.manifest)

    def work(cur):
        row = _one(cur, "select dataset_id from ml_datasets where version = %s", (dataset.version,))
        if row:
            return row[0]
        return _one(cur, "insert into ml_datasets (version, manifest_json, report_json, label_class, survivorship, "
                         "fundamental_coverage, synthetic) values (%s, %s, %s, %s, %s, %s, %s) returning dataset_id",
                    (dataset.version, ds.canonical(dataset.manifest), ds.canonical(dataset.report),
                     dataset.label_class, dataset.survivorship, dataset.fundamental_coverage, synthetic))[0]
    return _write(conn, work)


def declare_protocol(conn, declaration, plan):
    """§5.4's declaration (written before any run; the database checks the order) and its sufficiency for the
    primary horizon (§5.5). declaration must name: family, primary (output, horizon), nomination, criterion, the
    fold schedule with each fold's validation range, and test_periods [[start, end], ...]. Returns
    (protocol_id, sufficiency, reasons)."""
    for key in ("family", "primary", "nomination", "criterion", "folds", "test_periods", "embargo_sessions"):
        if key not in declaration:
            raise Refused("declaration", f"a protocol declares its {key} (§5.4)")
    if not declaration["folds"]:
        raise Refused("declaration", "a protocol declares at least one fold")
    for f in declaration["folds"]:
        if not isinstance(f, dict) or "v_start" not in f or "v_end" not in f:
            raise Refused("declaration", "each fold declares its v_start and v_end")
    emb = declaration["embargo_sessions"]
    if isinstance(emb, bool) or not isinstance(emb, int) or emb < 1:          # M5-6 re-audit A
        raise Refused("declaration", "embargo_sessions is a whole number of sessions, at least 1")
    pol = declaration.get("policies")
    if not isinstance(pol, list) or not pol or not all(isinstance(x, str) and x for x in pol):   # re-audit E
        raise Refused("declaration", "a protocol declares every policy it uses (§5.4): a non-empty list of ids")
    alpha = declaration.get("alpha", 0.05)
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha <= 0.05:     # re-audit minor
        raise Refused("declaration", "alpha is in (0, 0.05]")
    fam = declaration["family"]
    ok = (isinstance(fam, list) and fam and all(isinstance(x, str) for x in fam)) or (
        isinstance(fam, dict) and fam and all(isinstance(k, str) and isinstance(v, list) and v and
                                              all(isinstance(x, str) for x in v) for k, v in fam.items()))
    if not ok:                                                                  # re-audit minor: the family's shape
        raise Refused("declaration", "family is a non-empty list of configuration ids, or {\"output:h\": [ids]}")
    last_v_end = max(_ts(f["v_end"]) for f in declaration["folds"])
    gap = timedelta(days=emb)
    for start, _end in declaration["test_periods"]:
        # §5.4: P_start at least the embargo after the last V_end. Sessions are at least days apart, so this is a
        # necessary condition checked here; the protocol's calendar check is the exact one (M5-6 audit M3)
        if _ts(start) <= last_v_end + gap:
            raise Refused("test_period", f"a test period starting {start} is not after the last V_end "
                                         f"{last_v_end.isoformat()} plus the embargo")
    if declaration["criterion"].get("form") != "gate_test_every_baseline_bonferroni_over_nominees":
        raise Refused("criterion", "the test criterion has at least §5.4's minimum form")
    state, reasons = sf.check(plan)
    js = canonical({**declaration, "sufficiency_reasons": reasons})

    def work(cur):
        pid = _one(cur, "insert into ml_protocols (declaration_json, declaration_hash, sufficiency) values (%s, %s, %s) "
                        "returning protocol_id", (js, sha(js), state))[0]
        for start, end in declaration["test_periods"]:
            timing = "prospective" if _ts(start) > _one(cur, "select now()")[0] else "retrospective"
            prev = timing == "retrospective" or bool(_one(
                cur, "select exists (select 1 from ml_datasets where (manifest_json::jsonb ->> 'date_to')::date >= "
                     "%s::date)", (_ts(start).date(),))[0])
            cur.execute("insert into ml_test_periods (protocol_id, period, timing, previously_scored) values "
                        "(%s, tstzrange(%s, %s, '[]'), %s, %s)", (pid, start, end, timing, prev))
        return pid
    return _write(conn, work), state, reasons


def _ts(x):
    if isinstance(x, datetime):
        return x
    t = datetime.fromisoformat(str(x))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------ test periods


def unspent_periods(conn, exclude_protocol=None):
    """[(period_id, protocol_id, start, end)] of every declared interval without an access."""
    return _read(conn, lambda cur: (cur.execute(
        "select p.period_id, p.protocol_id, lower(p.period), upper(p.period) from ml_test_periods p where not exists "
        "(select 1 from ml_test_accesses a where a.period_id = p.period_id) and (%s::bigint is null or "
        "p.protocol_id <> %s) order by lower(p.period)", (exclude_protocol, exclude_protocol)), cur.fetchall())[1])


def withheld_for(conn, date_from, date_to):
    """The declared, unspent periods a dataset over [date_from, date_to] must withhold (M5-2's carry)."""
    out = []
    for _pid, _prot, start, end in unspent_periods(conn):
        if start.date() <= date_to and end.date() >= date_from:
            out.append((start, end))
    return tuple(out)


def check_manifest(conn, manifest):
    """MLK-4: a dataset build for training or validation that does not withhold every unspent test period its range
    touches is refused. A withheld period of the manifest must cover each one."""
    for start, end in withheld_for(conn, manifest.date_from, manifest.date_to):
        if not any(a <= start and b >= end for a, b in manifest.withheld):
            raise Refused("test_labels", f"the manifest does not withhold the unspent test period {start} .. {end}")
    return True


def check_rows_outside_unspent(conn, protocol_id, instants, grace=timedelta(0)):
    """MLK-5: no row of a training or validation set inside another protocol's declared, unspent interval. Each item
    of `instants` is a row's T, or (T, window_end): then the row's whole label window [T, window_end], extended by
    `grace`, must miss every such interval (Phase 11 re-audit N3: a row at P_start - 1 whose label reaches into the
    interval reads its labels)."""
    periods = unspent_periods(conn, exclude_protocol=protocol_id)
    for item in instants:
        t, end_w = (item, item) if not isinstance(item, (tuple, list)) else (item[0], item[1] + grace)
        for pid, prot, start, end in periods:
            if t <= end and end_w >= start:
                raise Refused("unspent_interval", f"a row at {t} (label window to {end_w}) meets protocol {prot}'s "
                                                  f"unspent test period {pid}")
    return True


def multiplicity(conn, output, horizon, protocol_id):
    """§5.4: max(the protocol's declared family size, the number of configurations of any protocol with validation
    evidence on sessions overlapping this protocol's validation sessions, for this (output, horizon))."""
    def work(cur):
        decl = json.loads(_one(cur, "select declaration_json from ml_protocols where protocol_id = %s",
                               (protocol_id,))[0])
        family = len(decl["family"].get(f"{output}:{horizon}", [])) if isinstance(decl["family"], dict) else \
            len(decl["family"])
        mine = [(_ts(f["v_start"]), _ts(f["v_end"])) for f in decl["folds"]]
        cur.execute("select distinct r.configuration_id, p.declaration_json from ml_runs r join ml_protocols p using "
                    "(protocol_id) where r.output = %s and r.horizon = %s and exists (select 1 from "
                    "ml_model_metrics m where m.run_id = r.run_id and m.scope = 'fold')", (output, horizon))
        tried = set()
        for cid, dj in cur.fetchall():
            for f in json.loads(dj).get("folds", []):
                if not isinstance(f, dict) or "v_start" not in f or "v_end" not in f:
                    continue                    # re-audit minor: a malformed declaration never blocks validate
                a, b = _ts(f["v_start"]), _ts(f["v_end"])
                if any(a <= y and b >= x for x, y in mine):
                    tried.add(cid)
        return max(family, len(tried), 1)
    return _read(conn, work)


# ------------------------------------------------------------------------------------------------ runs and models


def start_run(conn, protocol_id, configuration_id, dataset_id, output, horizon, code_version):
    """A run of a configuration on a registered dataset. MLK-4 again at the run (M5-6 audit M10): a test period
    declared after the dataset was registered must be withheld by it too, or the run is refused."""
    js = _read(conn, lambda cur: _one(cur, "select manifest_json from ml_datasets where dataset_id = %s",
                                      (dataset_id,)))
    if js is None:
        raise Refused("dataset", f"no dataset {dataset_id}")
    m = json.loads(js[0])
    from types import SimpleNamespace
    check_manifest(conn, SimpleNamespace(date_from=datetime.fromisoformat(m["date_from"]).date(),
                                         date_to=datetime.fromisoformat(m["date_to"]).date(),
                                         withheld=tuple((_ts(x), _ts(y)) for x, y in m.get("withheld", []))))

    def work(cur):
        rid = _one(cur, "insert into ml_runs (protocol_id, configuration_id, dataset_id, output, horizon, code_version) "
                        "values (%s, %s, %s, %s, %s, %s) returning run_id",
                   (protocol_id, configuration_id, dataset_id, output, horizon, code_version))[0]
        cur.execute("insert into ml_run_events (run_id, seq, state) values (%s, 1, 'started')", (rid,))
        return rid
    return _write(conn, work)


def run_event(conn, run_id, state, detail=None):
    def work(cur):
        seq = _one(cur, "select coalesce(max(seq), 0) + 1 from ml_run_events where run_id = %s", (run_id,))[0]
        cur.execute("insert into ml_run_events (run_id, seq, state, detail_json) values (%s, %s, %s, %s)",
                    (run_id, seq, state, canonical(detail or {})))
    _write(conn, work)


def record_trial(conn, run_id, fold_no, trial_no, hyperparameters, select_score):
    _write(conn, lambda cur: cur.execute(
        "insert into ml_trials (run_id, fold_no, trial_no, hyperparameters_json, select_score) values "
        "(%s, %s, %s, %s, %s)", (run_id, fold_no, trial_no, canonical(hyperparameters), select_score)))


def _artifact(cur, artifact):
    body = artifact.text.encode("utf-8")
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise Refused("artifact_integrity", "the artifact's text does not match its SHA-256")
    cur.execute("insert into ml_artifacts (artifact_sha256, artifact_base64, artifact_size) values (%s, %s, %s) "
                "on conflict (artifact_sha256) do nothing",
                (artifact.sha256, base64.b64encode(body).decode("ascii"), len(body)))


def load_artifact(conn, sha256):
    """The artifact text by its hash, checked again (MLK-16): the bytes the registry holds are the ones registered."""
    row = _read(conn, lambda cur: _one(cur, "select artifact_base64 from ml_artifacts where artifact_sha256 = %s",
                                       (sha256,)))
    if row is None:
        raise Refused("artifact_missing", f"no artifact {sha256}")
    body = base64.b64decode(row[0])
    if hashlib.sha256(body).hexdigest() != sha256:
        raise Refused("artifact_integrity", "the stored artifact does not match its hash")
    return body.decode("utf-8")


def record_fold_model(conn, run_id, fold_no, boundaries, artifact, predictions_sha256):
    def work(cur):
        _artifact(cur, artifact)
        cur.execute("insert into ml_fold_models (run_id, fold_no, boundaries_json, artifact_sha256, predictions_sha256) "
                    "values (%s, %s, %s, %s, %s)", (run_id, fold_no, canonical(boundaries), artifact.sha256,
                                                    predictions_sha256))
    _write(conn, work)


def record_metrics(conn, scope, stratum, metrics, run_id=None, model_id=None, fold_no=None):
    if scope == "test":                     # M5-6 re-audit B: the test result is written by access() alone
        raise Refused("access", "test-period metrics are written only by the one access")
    _write(conn, lambda cur: cur.execute(
        "insert into ml_model_metrics (model_id, run_id, scope, fold_no, stratum, metrics_json) values "
        "(%s, %s, %s, %s, %s, %s)", (model_id, run_id, scope, fold_no, stratum, canonical(metrics))))


def register_model(conn, *, kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff,
                   evidence_label_class, artifact, environment_json, validation=None, test_period_id=None,
                   domain=None, rollforward_of=None):
    """A research model and its first event (`candidate`), in one transaction. Returns its id."""
    def work(cur):
        _artifact(cur, artifact)
        mid = _one(cur, "insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, "
                        "training_cutoff, validation_json, test_period_id, evidence_label_class, environment_json, "
                        "artifact_sha256, domain_json, environment, rollforward_of) values (%s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s, %s, %s, %s, %s, 'research', %s) returning model_id",
                   (kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff,
                    canonical(validation or {}), test_period_id, evidence_label_class, canonical(environment_json),
                    artifact.sha256, canonical(domain or {}), rollforward_of))[0]
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'candidate', "
                    "'registered')", (mid,))
        return mid
    return _write(conn, work)


def status(conn, model_id):
    return _read(conn, lambda cur: (_one(cur, "select status from ml_model_events where model_id = %s order by seq "
                                              "desc limit 1", (model_id,)) or (None,))[0])


def _event(cur, model_id, new_status, reason):
    seq = _one(cur, "select coalesce(max(seq), 0) + 1 from ml_model_events where model_id = %s", (model_id,))[0]
    cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, %s, %s, %s)",
                (model_id, seq, new_status, reason))


def _report(cur, model_id, report, code_version):
    return _one(cur, "insert into ml_gate_reports (model_id, kind, passed, checks_json, multiplicity, code_version) "
                     "values (%s, %s, %s, %s, %s, %s) returning report_id",
                (model_id, report["kind"], report["passed"], canonical({"gates": report["gates"],
                                                                         "labels": report["labels"]}),
                 report["multiplicity"], code_version))[0]


def registry_facts(conn, model_id):
    """What gates 2, 4, 5 and 6 take from the registry, never from the caller (M5-6 audit M9): the protocol's
    sufficiency (for its primary output and horizon; any other is `exploratory` until declared, audit minor 14),
    whether the model's configuration is in the declared family, the declared folds, the level, the multiplicity
    (§5.4, with policies counted as tried when their evidence cutoff is not before the first V_start) and the status of
    every declared policy (validated only by the owner, 0021)."""
    def work(cur):
        m = _one(cur, "select m.protocol_id, m.configuration_id, m.output, m.horizon, d.survivorship, d.version "
                      "from ml_models m join ml_datasets d using (dataset_id) where m.model_id = %s", (model_id,))
        if m is None:
            raise Refused("model", f"no model {model_id}")
        pid, cid, output, horizon, survivorship, version = m
        # M5-6 re-audit D (gate 7): the dataset's survivorship, and an owner waiver of kind survivorship that names
        # this dataset version in covers_json["datasets"]
        waiver = None
        if survivorship != "complete":
            cur.execute("select waiver_id, covers_json from ml_waivers where kind = 'survivorship' order by waiver_id")
            for wid, cj in cur.fetchall():
                covers = json.loads(cj)
                if isinstance(covers, dict) and version in (covers.get("datasets") or []):
                    waiver = wid
        decl_js, suff = _one(cur, "select declaration_json, sufficiency from ml_protocols where protocol_id = %s",
                             (pid,))
        decl = json.loads(decl_js)
        primary = list(decl["primary"]) == [output, horizon]
        first_v = min(_ts(f["v_start"]) for f in decl["folds"])
        policies, late = {}, 0
        for p in decl.get("policies", []):
            row = _one(cur, "select status, evidence_cutoff from ml_configurations where configuration_id = %s", (p,))
            policies[p] = row[0] if row else "missing"
            if row is None or row[1] is None or row[1] >= first_v:
                late += 1
        return {"protocol_sufficiency": suff if primary else sf.EXPLORATORY, "declared_before_first_run": True,
                "in_family": cid in _family(decl, output, horizon), "declared_folds": len(decl["folds"]),
                "alpha": float(decl.get("alpha", 0.05)), "horizon": horizon, "policy_status": policies,
                "survivorship": survivorship, "survivorship_waiver": waiver,
                "late_policies": late, "output": output, "protocol_id": pid}
    facts = _read(conn, work)
    facts["multiplicity"] = multiplicity(conn, facts.pop("output"), facts["horizon"], facts.pop("protocol_id")) + \
        facts.pop("late_policies")
    return facts


def validate(conn, model_id, evidence, code_version):
    """Gates 2 to 8: write the report; on a pass, the `validated` event in the same transaction (the database
    re-checks gate 1 and the report). On a fail the model stays `candidate`. Returns the report. The registry's own
    facts (`registry_facts`) replace whatever the caller's evidence says about them."""
    from dataclasses import replace
    evidence = replace(evidence, **registry_facts(conn, model_id))
    report = gt.validation_report(evidence)

    def work(cur):
        _report(cur, model_id, report, code_version)
        if report["passed"]:
            _event(cur, model_id, "validated", "gates 1 to 8 passed")
        return report
    return _write(conn, work)


def transition(conn, model_id, new_status, reason):
    """rejected or retired (the database refuses any other move)."""
    if new_status not in ("rejected", "retired"):
        raise Refused("transition", "validated comes from validate(); production from promote()")

    def work(cur):
        owner = _one(cur, "select session_user")[0] == "cse_migrator"
        if owner:
            cur.execute("set local role cse_owner")
        last = _one(cur, "select status from ml_model_events where model_id = %s order by seq desc limit 1",
                    (model_id,))
        if last and last[0] == "production" and not owner:
            raise Refused("owner_path", "retiring a production model is the owner's act (§9.2)")
        _event(cur, model_id, new_status, reason)
    _write(conn, work)


def access(conn, protocol_id, period_id, nominees, compute_result):
    """§5.4's one access. nominees: [{"model_id", "predictions_sha256"}] - their final fits (registered models of
    this protocol) and the hash of their frozen test-period predictions. compute_result() is called only after the
    access row is written in this transaction; it builds the test labels and evaluates the criterion, returning a JSON
    object with "nominees_passed": [model ids]. Returns that result."""
    if not nominees:
        raise Refused("nominees", "an access names at least one nominee")
    import re

    def check(cur):
        decl = json.loads(_one(cur, "select declaration_json from ml_protocols where protocol_id = %s",
                               (protocol_id,))[0])
        for n in nominees:
            row = _one(cur, "select protocol_id, configuration_id, output, horizon from ml_models where model_id = %s",
                       (n["model_id"],))
            if row is None or row[0] != protocol_id:
                raise Refused("nominee", f"model {n['model_id']} is not a final fit of protocol {protocol_id}")
            if not re.fullmatch(r"[0-9a-f]{64}", str(n.get("predictions_sha256", ""))):
                raise Refused("frozen", f"model {n['model_id']}'s test-period predictions are not frozen and hashed")
            # M5-6 audit M4 (MLK-4): a nominee is validated and of the declared family
            st = _one(cur, "select status from ml_model_events where model_id = %s order by seq desc limit 1",
                      (n["model_id"],))
            if st is None or st[0] != "validated":
                raise Refused("nominee", f"model {n['model_id']} is not validated")
            if row[1] not in _family(decl, row[2], row[3]):
                raise Refused("nominee", f"model {n['model_id']}'s configuration is not in the declared family")
    _read(conn, check)
    combined = sha(canonical(sorted((n["model_id"], n["predictions_sha256"]) for n in nominees)))
    # §5.4 (M5-6 audit M5): the access row is written and committed FIRST; whatever happens after, the period is
    # spent and the access counts. A failure while building labels or evaluating is recorded as the result.
    aid = _write(conn, lambda cur: _one(
        cur, "insert into ml_test_accesses (period_id, protocol_id, nominees_json, predictions_sha256, result_json) "
             "values (%s, %s, %s, %s, '{}') returning access_id",
        (period_id, protocol_id, canonical(nominees), combined))[0])
    try:
        result = compute_result()
    except Exception as exc:
        failed = {"nominees_passed": [], "failed": f"{type(exc).__name__}: {exc}"[:300], "access_id": aid}
        _write(conn, lambda cur: cur.execute(
            "insert into ml_model_metrics (run_id, model_id, scope, stratum, metrics_json) values (null, %s, 'test', "
            "'access', %s)", (nominees[0]["model_id"], canonical(failed))))
        raise
    _write(conn, lambda cur: cur.execute(
        "insert into ml_model_metrics (run_id, model_id, scope, stratum, metrics_json) values (null, %s, 'test', "
        "'access', %s)", (nominees[0]["model_id"], canonical({**result, "access_id": aid}))))
    return {**result, "access_id": aid}


def _family(decl, output, horizon):
    fam = decl["family"]
    return fam.get(f"{output}:{horizon}", []) if isinstance(fam, dict) else fam


def lifetime_accesses(conn):
    return _read(conn, lambda cur: _one(cur, "select count(*) from ml_test_accesses")[0])


EXTERNAL = ("backtest_valid", "backtest_ends_at_last_v_end_or_post_access", "point_in_time_share")


def promotion_evidence(conn, model_id, external):
    """Gates 9 to 12's evidence, read from the registry wherever the registry holds it, so that the owner's command
    states only what the registry cannot know yet: the Phase 10 backtest (`backtest_valid`,
    `backtest_ends_at_last_v_end_or_post_access`), the validation's `point_in_time_share`, and optionally a
    `reconstructed_waiver` id (checked: an owner waiver of kind reconstructed_evidence). From the registry: whether
    the protocol's one access named this model and whether it passed; the declared point-in-time share (the
    protocol's `point_in_time_share`); the labels the model carries (its passing validation report's, the access
    period's `retrospective` and `previously_scored`, `rollforward_of`); the lifetime access count. Owner path only."""
    missing = [k for k in EXTERNAL if k not in external]
    unknown = sorted(set(external) - set(EXTERNAL) - {"reconstructed_waiver"})
    if missing or unknown:
        raise Refused("evidence", f"the owner states exactly {list(EXTERNAL)} (+ reconstructed_waiver); "
                                  f"missing {missing}, unknown {unknown}")

    def work(cur):
        if _one(cur, "select session_user")[0] != "cse_migrator":
            raise Refused("owner_path", "promotion evidence is read on the owner path (cse_migrator as cse_owner)")
        cur.execute("set local role cse_owner")
        m = _one(cur, "select protocol_id, rollforward_of, environment, evidence_label_class from ml_models where "
                      "model_id = %s", (model_id,))
        if m is None or m[2] != "research":
            raise Refused("model", f"model {model_id} is not a registered research model")
        protocol_id, rollforward_of, evidence_class = m[0], m[1], m[3]
        # M5-6 re-audit G (MLK-24): evidence that is not point_in_time carries reconstructed_evidence, and a model
        # validated only on reconstructed rows has no point-in-time share to state
        if evidence_class == "reconstructed" and float(external["point_in_time_share"]) > 0:
            raise Refused("evidence", "a reconstructed-only model has no point_in_time share")
        decl = json.loads(_one(cur, "select declaration_json from ml_protocols where protocol_id = %s",
                               (protocol_id,))[0])
        if "point_in_time_share" not in decl:
            raise Refused("declaration", "the protocol declares no point_in_time_share (gate 11)")
        access_done, passed, timing = False, False, set()
        cur.execute("select a.access_id, a.nominees_json, p.timing, p.previously_scored from ml_test_accesses a "
                    "join ml_test_periods p on p.period_id = a.period_id where a.protocol_id = %s", (protocol_id,))
        for aid, nominees, tm, prev in cur.fetchall():
            if model_id not in [n["model_id"] for n in json.loads(nominees)]:
                continue
            access_done = True
            timing |= {"retrospective"} if tm == "retrospective" else set()
            timing |= {"previously_scored"} if prev else set()
            # the one result of this access (re-audit B: a unique index keeps it single and unrewritten)
            res = _one(cur, "select metrics_json from ml_model_metrics where scope = 'test' and stratum = 'access' "
                            "and (metrics_json::jsonb ->> 'access_id')::bigint = %s", (aid,))
            if res is not None and model_id in json.loads(res[0]).get("nominees_passed", []):
                passed = True
        # Phase 11 re-audit N2 (Phase 5 §5.6): a roll-forward inherits gate 9 by reference, through its
        # rollforward_of chain, from the version that was its protocol's tested nominee
        ref = None
        if not access_done and rollforward_of is not None:
            seen, target = set(), rollforward_of
            while target is not None and target not in seen and len(seen) < 64:
                seen.add(target)
                t_prot, t_parent = _one(cur, "select protocol_id, rollforward_of from ml_models where model_id = %s",
                                        (target,))
                cur.execute("select a.access_id, a.nominees_json, p.timing, p.previously_scored from "
                            "ml_test_accesses a join ml_test_periods p on p.period_id = a.period_id where "
                            "a.protocol_id = %s", (t_prot,))
                for aid, nominees, tm, prev in cur.fetchall():
                    if target not in [n["model_id"] for n in json.loads(nominees)]:
                        continue
                    res = _one(cur, "select metrics_json from ml_model_metrics where scope = 'test' and stratum = "
                                    "'access' and (metrics_json::jsonb ->> 'access_id')::bigint = %s", (aid,))
                    access_done = True
                    timing |= {"retrospective"} if tm == "retrospective" else set()
                    timing |= {"previously_scored"} if prev else set()
                    if res is not None and target in json.loads(res[0]).get("nominees_passed", []):
                        passed, ref = True, target
                if access_done:
                    break
                target = t_parent
        row = _one(cur, "select checks_json from ml_gate_reports where model_id = %s and kind = 'validation' and "
                        "passed order by report_id desc limit 1", (model_id,))
        labels = set(json.loads(row[0])["labels"]) if row else set()
        labels |= timing | ({"rollforward_of"} if rollforward_of is not None else set()) | (
            {f"gate9_by_reference:{ref}"} if ref is not None else set()) | (
            {"reconstructed_evidence"} if evidence_class != "point_in_time" else set())
        waiver = external.get("reconstructed_waiver")
        if waiver is not None and _one(cur, "select kind from ml_waivers where waiver_id = %s", (waiver,)) != \
                ("reconstructed_evidence",):
            raise Refused("waiver", f"waiver {waiver} is not an owner waiver of reconstructed_evidence")
        lifetime = _one(cur, "select count(*) from ml_test_accesses")[0]
        return gt.PromotionEvidence(
            access_done=access_done, nominee_passed=passed, backtest_valid=bool(external["backtest_valid"]),
            backtest_ends_at_last_v_end_or_post_access=bool(external["backtest_ends_at_last_v_end_or_post_access"]),
            point_in_time_share=float(external["point_in_time_share"]),
            declared_point_in_time_share=float(decl["point_in_time_share"]), reconstructed_waiver=waiver,
            model_labels=tuple(sorted(labels)), designation_labels=tuple(sorted(labels)),
            designation_cites_access_count=True, lifetime_test_accesses=lifetime)
    return _read(conn, work)


def promote(owner_conn, model_id, evidence, *, purpose, output, horizon, list_ids, note, code_version):
    """The owner's act (gates 9 to 12): on the owner path, write the promotion report for the validated version,
    then, if it passed, the production copy (same artifact), the designation naming it with every label, and its
    first event, in one transaction; the database checks the owner path, the validated source, the report and the
    designation at commit. Returns (production model id or None, report). `evidence` is the owner's statement (the
    dict `promotion_evidence` takes): everything else is read from the registry here (M5-6 re-audit, minor)."""
    if not isinstance(evidence, dict):
        raise Refused("evidence", "promote derives its evidence from the registry: pass the owner's statement")
    for x in list_ids:
        if x != model_id:
            row = _read(owner_conn, lambda cur: (cur.execute("set local role cse_owner"), _one(
                cur, "select m.environment, m.output, m.horizon, (select e.status from ml_model_events e where "
                     "e.model_id = m.model_id order by e.seq desc limit 1) from ml_models m where m.model_id = %s",
                (x,)))[1])
            if row != ("production", output, horizon, "production"):          # re-audit F (the database checks too)
                raise Refused("designation", f"model {x} is not a production model of {output}/{horizon}")
    evidence = promotion_evidence(owner_conn, model_id, evidence)
    report = gt.promotion_report(evidence)

    def work(cur):
        if _one(cur, "select session_user")[0] != "cse_migrator":
            raise Refused("owner_path", "promotion runs through the owner path (cse_migrator as cse_owner)")
        cur.execute("set local role cse_owner")
        _report(cur, model_id, report, code_version)
        if not report["passed"]:
            return None
        src = _one(cur, "select kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff, "
                        "validation_json, test_period_id, evidence_label_class, environment_json, artifact_sha256, "
                        "domain_json, rollforward_of from ml_models where model_id = %s", (model_id,))
        prod = _one(cur, "insert into ml_models (kind, configuration_id, output, horizon, dataset_id, protocol_id, "
                         "training_cutoff, validation_json, test_period_id, evidence_label_class, environment_json, "
                         "artifact_sha256, domain_json, rollforward_of, environment, promoted_from) values (%s, %s, "
                         "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'production', %s) returning model_id",
                    src + (model_id,))[0]
        ids = [prod if x == model_id else x for x in list_ids]
        cur.execute("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "(%s, %s, %s, %s, %s, %s)", (purpose, output, horizon, canonical(ids),
                                                 canonical({"labels": report["labels"],
                                                            "lifetime_test_accesses":
                                                                evidence.lifetime_test_accesses}), note))
        cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', %s)",
                    (prod, f"promoted from {model_id}"))
        return prod
    return _write(owner_conn, work), report


def promote_list(owner_conn, positions, evidence, *, purpose, output, horizon, note, code_version, components=()):
    """Phase 11 re-audit N2: the owner promotes a whole designation list in ONE transaction, so no mixed old/new list
    is ever in force. `positions`: the validated research versions, in list order (the combinations the list
    designates); `components`: validated versions promoted with them (production copies, so the production record
    names production artifacts) but never designated on their own; `evidence`: {model_id: the owner's statement}.
    Every version's promotion report is written; if any fails, nothing else is written and the reports say why.
    Returns ({research id: production id} or None, {research id: report})."""
    everyone = list(positions) + [c for c in components if c not in positions]
    if not positions or len(set(everyone)) != len(everyone):
        raise Refused("designation", "a list names at least one position and no version twice")
    reports = {}
    for mid in everyone:
        if mid not in evidence:
            raise Refused("evidence", f"no owner statement for model {mid}")
        reports[mid] = gt.promotion_report(promotion_evidence(owner_conn, mid, evidence[mid]))

    def work(cur):
        if _one(cur, "select session_user")[0] != "cse_migrator":
            raise Refused("owner_path", "promotion runs through the owner path (cse_migrator as cse_owner)")
        cur.execute("set local role cse_owner")
        for mid in everyone:
            _report(cur, mid, reports[mid], code_version)
        if not all(r["passed"] for r in reports.values()):
            return None
        prod = {}
        for mid in everyone:
            src = _one(cur, "select kind, configuration_id, output, horizon, dataset_id, protocol_id, training_cutoff, "
                            "validation_json, test_period_id, evidence_label_class, environment_json, artifact_sha256, "
                            "domain_json, rollforward_of from ml_models where model_id = %s", (mid,))
            if (src[2], src[3]) != (output, horizon):
                raise Refused("designation", f"model {mid} is not of {output}/{horizon}")
            prod[mid] = _one(cur, "insert into ml_models (kind, configuration_id, output, horizon, dataset_id, "
                                  "protocol_id, training_cutoff, validation_json, test_period_id, evidence_label_class, "
                                  "environment_json, artifact_sha256, domain_json, rollforward_of, environment, "
                                  "promoted_from) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                                  "'production', %s) returning model_id", src + (mid,))[0]
        labels = sorted({lb for r in reports.values() for lb in r["labels"]})
        lifetime = _one(cur, "select count(*) from ml_test_accesses")[0]
        cur.execute("insert into ml_designations (purpose, output, horizon, list_json, labels_json, note) values "
                    "(%s, %s, %s, %s, %s, %s)", (purpose, output, horizon, canonical([prod[m] for m in positions]),
                                                 canonical({"labels": labels, "lifetime_test_accesses": lifetime,
                                                            "components": [prod[c] for c in everyone
                                                                           if c not in positions]}), note))
        for mid in everyone:
            cur.execute("insert into ml_model_events (model_id, seq, status, reason) values (%s, 1, 'production', %s)",
                        (prod[mid], f"promoted from {mid} with its list"))
        return prod
    return _write(owner_conn, work), reports


def alerts(conn):
    """§9.2: a production model whose source version was later rejected is reported, never retired automatically."""
    return _read(conn, lambda cur: (cur.execute(
        "select p.model_id, p.promoted_from from ml_models p where p.environment = 'production' and "
        "(select status from ml_model_events e where e.model_id = p.promoted_from order by seq desc limit 1) = "
        "'rejected' and (select status from ml_model_events e where e.model_id = p.model_id order by seq desc "
        "limit 1) <> 'retired'"), [{"production": a, "rejected_source": b} for a, b in cur.fetchall()])[1])
