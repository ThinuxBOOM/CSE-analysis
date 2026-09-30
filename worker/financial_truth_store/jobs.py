"""
F6.4 jobs (docs/F6.4_DESIGN.md sections 5.3, 6, 9.4, 15.7, 18): validate, reconcile, cleanup, plus the two
configuration actions (register a configuration; the owner's designation).

Job ledger: a job row and its `started` event commit first (autocommit-style, outside the data transaction).
- validate: the data rows and the final `succeeded` event commit in ONE transaction; a rerun is `already_present`;
  any failure rolls back and records `failed` with the error class and a redacted message.
- reconcile: each partition commits atomically on its own; the final event follows the last partition. A job that
  never got its final event is marked `abandoned` by `cleanup` (or by the next reconcile); its committed partitions
  stay complete and valid.

Locking (F6_LOCK_KEY, session-level advisory lock): reconcile and cleanup take it exclusively and end `refused`
(busy) when it is held; validate takes it shared (waits, or is refused with nowait). A reconcile job's missing
validations run as child validate jobs in its own session, under its exclusive lock.

Every call into F6.3 runs inside decimal.localcontext(F6_DECIMAL_CONTEXT) (section 17.4).
"""
import decimal
import getpass
import json
import os
import platform
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from ..financial_truth import admission, inputs, observations, reconciliation, versions
from ..ops import settings as ops_settings
from . import F6_DECIMAL_CONTEXT, F6_LOCK_KEY, STORE_VERSION, codec, loader, selection, writer

MAX_MESSAGE = 400


class JobRefused(RuntimeError):
    """Refused before anything was written (busy lock, missing validations with --no-validate, wrong configuration)."""


def utcnow():
    return datetime.now(timezone.utc)


def _redact(exc):
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text[:MAX_MESSAGE]


# ------------------------------------------------------------------------------------------------ ledger

def start_job(conn, kind, *, f5_run_id=None, configuration_id=None, scope, parameters=None, code_revision=None):
    """T10 + its `started` event, committed before any work. Returns job_id."""
    job_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("insert into financial_f6_jobs (job_id, kind, f5_run_id, configuration_id, scope, store_version, "
                    "code_revision, parameters, host, pid, os_user) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (job_id, kind, f5_run_id, configuration_id, scope, STORE_VERSION, code_revision,
                     psycopg2.extras.Json(parameters or {}), platform.node(), os.getpid(), _os_user()))
        cur.execute("insert into financial_f6_job_events (job_id, seq, state) values (%s, 1, 'started')", (job_id,))
    conn.commit()
    return job_id


def _os_user():
    try:
        return getpass.getuser()
    except Exception:           # no login name in some service contexts
        return None


def event(cur, job_id, state, details=None):
    """The job's next event (the guard enforces the transitions). The caller commits."""
    cur.execute("insert into financial_f6_job_events (job_id, seq, state, details) values (%s, "
                "(select coalesce(max(seq), 0) + 1 from financial_f6_job_events where job_id = %s), %s, %s)",
                (job_id, job_id, state, psycopg2.extras.Json(details or {})))


def final_event(conn, job_id, state, details=None):
    """A final event in its own transaction (after a rollback, or after a reconcile's last partition)."""
    with conn.cursor() as cur:
        event(cur, job_id, state, details)
    conn.commit()


# ------------------------------------------------------------------------------------------------ locks

def lock_shared(conn, wait=True):
    with conn.cursor() as cur:
        if wait:
            cur.execute("select pg_advisory_lock_shared(%s)", (F6_LOCK_KEY,))
            got = True
        else:
            cur.execute("select pg_try_advisory_lock_shared(%s)", (F6_LOCK_KEY,))
            got = cur.fetchone()[0]
    conn.commit()
    return got


def lock_exclusive(conn):
    with conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s)", (F6_LOCK_KEY,))
        got = cur.fetchone()[0]
    conn.commit()
    return got


def unlock(conn, shared):
    if conn.closed:
        return
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock_shared(%s)" if shared else "select pg_advisory_unlock(%s)",
                        (F6_LOCK_KEY,))
        conn.commit()
    except psycopg2.Error:
        conn.rollback()


# ------------------------------------------------------------------------------------------------ validate

def compute_validation(cur, f5_run_id, *, link_id="current", uploaded_at="current"):
    """F6.3 over one persisted F5 run with the current input set (M4), or with an explicit issuer decision and
    publication instant (reproduction). Returns (validation run, SOs, loaded result, run row, started, finished)."""
    loader.session(cur)
    result, run = loader.f5_result(cur, f5_run_id)
    ref = loader.f5_run_ref(cur, f5_run_id, result, run)
    if link_id == "current":
        link_id = loader.current_issuer_link_id(cur, run["cse_filing_id"])
    if uploaded_at == "current":
        uploaded_at = loader.current_uploaded_at(cur, run["cse_filing_id"])
    link = loader.issuer_link(cur, link_id)
    doc = loader.document(cur, run["classification_id"])
    started = utcnow()
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        vr = admission.validate_run(result, f5_run=ref, issuer_link=link, uploaded_at=uploaded_at, document=doc)
        sos = observations.build(vr)
    return vr, sos, result, run, started, utcnow()


def f5_reported(result):
    return {c["id"]: codec.candidate_reported_json(c) for c in result["candidates"]}


def validate(conn, f5_run_id, *, parent_job=None, take_lock=True, wait=True, code_revision=None):
    """One validate job for one F5 run (section 6.1). Returns a report dict with the job's final state."""
    f5_run_id = str(f5_run_id)
    if take_lock and not lock_shared(conn, wait=wait):
        job_id = start_job(conn, "validate", f5_run_id=f5_run_id, scope="f5_run", code_revision=code_revision)
        final_event(conn, job_id, "refused", {"reason": "busy"})
        return {"job_id": job_id, "state": "refused", "reason": "busy"}
    try:
        job_id = start_job(conn, "validate", f5_run_id=f5_run_id, scope="f5_run", code_revision=code_revision,
                           parameters={"parent_job": parent_job} if parent_job else None)
        try:
            with conn.cursor() as cur:
                vr, sos, result, _, started, finished = compute_validation(cur, f5_run_id)
                rows = codec.validation_rows(vr, sos, store_version=STORE_VERSION, code_revision=code_revision,
                                             job_id=job_id, started_at=started, finished_at=finished)
                problems = codec.check_validation(rows, f5_reported(result),
                                                  vr.issuer_link.issuer_id if vr.issuer_link is not None else None)
                if problems:
                    raise codec.CodecError("decomposition differs from its envelopes: " + "; ".join(problems[:5]))
                status = writer.insert_validation(cur, rows)
                details = {"validation_run_key": vr.key, "candidates": len(vr.candidates), "sos": len(sos),
                           "facts": len(rows["T4"]), "issuer_link_id": rows["T1"]["issuer_link_id"]}
                if status == "already_present":
                    conn.rollback()
                    final_event(conn, job_id, "already_present", details)
                    return {"job_id": job_id, "state": "already_present", **details}
                event(cur, job_id, "succeeded", details)
            conn.commit()
            return {"job_id": job_id, "state": "succeeded", **details}
        except (writer.NondeterminismError, inputs.InputError, loader.LoaderError, codec.CodecError,
                psycopg2.Error) as exc:
            conn.rollback()
            reason = "nondeterminism" if isinstance(exc, writer.NondeterminismError) else "error"
            if isinstance(exc, psycopg2.Error) and "EDI" in str(exc):
                reason = "decomposition_mismatch"
            final_event(conn, job_id, "failed", {"reason": reason, "error": _redact(exc)})
            return {"job_id": job_id, "state": "failed", "reason": reason, "error": _redact(exc)}
    finally:
        if take_lock:
            unlock(conn, shared=True)


def pending_runs(cur, vs=versions.IMPLEMENTED):
    """F5 runs lacking their canonical validation run (M4) under the implemented version set."""
    cur.execute("select r.id from financial_extraction_runs r where not exists (select 1 from "
                "financial_validation_run_current v where v.f5_run_id = r.id and v.validation_version = %s and "
                "v.input_policy_version = %s and v.op1_version = %s and v.admission_version = %s and "
                "v.identity_version = %s) order by r.id",
                (vs.validation_version, vs.input_policy_version, vs.op1_version, vs.admission_version,
                 vs.identity_version))
    return [str(r[0]) for r in cur.fetchall()]


# ------------------------------------------------------------------------------------------------ configurations

def configuration_row(configuration):
    v = configuration.versions
    return {"configuration_id": configuration.configuration_id,
            "reconciliation_version": configuration.reconciliation_version,
            "validation_version": v.validation_version, "input_policy_version": v.input_policy_version,
            "op1_version": v.op1_version, "admission_version": v.admission_version,
            "identity_version": v.identity_version,
            "configuration_json": codec.checked_envelope(codec.e5(configuration), configuration.configuration_id,
                                                         "E5")}


def register_configuration(conn, configuration):
    """T8, content-addressed and insert-if-absent. Returns (state, configuration_id)."""
    row = configuration_row(configuration)
    problems = codec.check_configuration(row)
    if problems:
        raise codec.CodecError("configuration decomposition differs from E5: " + "; ".join(problems))
    with conn.cursor() as cur:
        state = writer.insert_configuration(cur, row)
    conn.commit()
    return state, configuration.configuration_id


def load_configuration(cur, configuration_id):
    cur.execute("select configuration_json from financial_reconciliation_configurations where configuration_id = %s",
                (configuration_id,))
    row = cur.fetchone()
    if row is None:
        raise JobRefused(f"configuration {configuration_id} is not registered")
    try:
        return codec.decode_configuration(row[0])
    except reconciliation.ConfigurationError as exc:
        raise JobRefused(f"configuration {configuration_id} is not of the implemented version set: {exc}") from None


def designated_configuration(cur, purpose="canonical"):
    cur.execute("select configuration_id from financial_reconciliation_designated where purpose = %s", (purpose,))
    row = cur.fetchone()
    return row[0] if row else None


OWNER_PATH_ROLE = "cse_migrator"


class OwnerPathRequired(RuntimeError):
    pass


def designate(conn, configuration_id, note, os_user=None, purpose="canonical"):
    """The owner's decision (T9), accepted only from the owner-delegation login acting as cse_owner for ONE insert."""
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"designating the canonical configuration is an owner decision: connected as {who!r}; "
                                f"run `sudo bash ops/bin/cse-financial designate ...` (runs as {OWNER_PATH_ROLE})")
    try:
        with conn.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute("insert into financial_reconciliation_designations (purpose, configuration_id, note, os_user) "
                        "values (%s, %s, %s, %s) returning id", (purpose, configuration_id, note, os_user))
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise


# ------------------------------------------------------------------------------------------------ cleanup

def mark_abandoned(conn, exclude_job):
    """Every job still at `started` (except the caller's own) -> `abandoned`. Only while holding the exclusive lock:
    no validate or reconcile job can then be running."""
    with conn.cursor() as cur:
        cur.execute("select job_id from financial_f6_job_state where state = 'started' and job_id <> %s order by job_id",
                    (exclude_job,))
        stale = [str(r[0]) for r in cur.fetchall()]
        for j in stale:
            event(cur, j, "abandoned", {"marked_by": exclude_job})
    conn.commit()
    return stale


def cleanup(conn, code_revision=None):
    if not lock_exclusive(conn):
        job_id = start_job(conn, "cleanup", scope="cleanup", code_revision=code_revision)
        final_event(conn, job_id, "refused", {"reason": "busy"})
        return {"job_id": job_id, "state": "refused", "reason": "busy"}
    try:
        job_id = start_job(conn, "cleanup", scope="cleanup", code_revision=code_revision)
        stale = mark_abandoned(conn, job_id)
        final_event(conn, job_id, "succeeded", {"abandoned": stale})
        return {"job_id": job_id, "state": "succeeded", "abandoned": stale}
    finally:
        unlock(conn, shared=False)


# ------------------------------------------------------------------------------------------------ reconcile

def _records_for(cur, so_keys):
    """{so_key: {"row": T5 row, "members": {cvk: T6 row}}} for the Python record check."""
    cur.execute("select * from financial_source_observations where so_key = any(%s)", (list(so_keys),))
    names = [d[0] for d in cur.description]
    out = {r[names.index("so_key")]: {"row": dict(zip(names, r)), "members": {}} for r in cur.fetchall()}
    cur.execute("select * from financial_so_members where so_key = any(%s)", (list(so_keys),))
    names = [d[0] for d in cur.description]
    for r in cur.fetchall():
        m = dict(zip(names, r))
        out[m["so_key"]]["members"][m["candidate_validation_key"]] = m
    return out


def _fact(cur, ef_key):
    cur.execute("select * from financial_economic_facts where ef_key = %s", (ef_key,))
    names = [d[0] for d in cur.description]
    return dict(zip(names, cur.fetchone()))


class PartitionPlan:
    """Everything one partition pass will write (section 9.4 step 4), computed from one snapshot."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def plan_partition(cur, job_id, configuration, issuer_id, documents):
    """Steps 4.1-4.3 inside the caller's REPEATABLE READ transaction: load and re-select the partition's runs, find
    each selected run's canonical validation run, decode its stored SOs, fingerprint, reconcile with F6.3 and build
    every row. Returns (state, plan_or_detail): 'inputs_changed' / 'unchanged' / 'planned'."""
    cid = configuration.configuration_id
    loader.session(cur)
    runs = [r for r in loader.all_run_refs(cur) if r.document_sha256 in documents]
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        sel = reconciliation.select_runs(runs, configuration)
    sos, chosen = [], []
    for d in sel.documents:
        vrk = selection.canonical_validation_run(cur, d.selected_run, configuration.versions)
        if vrk is None:
            return "inputs_changed", f"F5 run {d.selected_run} has no canonical validation run in this snapshot"
        run_sos = selection.stored_observations(cur, vrk)
        if any(o.identity.issuer_id != issuer_id for o in run_sos):
            return "inputs_changed", f"validation run {vrk} now belongs to another issuer"
        sos.extend(run_sos)
        chosen.append([d.selected_run, vrk])
    fp = selection.fingerprint(cid, issuer_id, sel, sos)
    last = writer.latest_batch(cur, cid, issuer_id)
    if last is not None and last[2] == fp:
        return "unchanged", chosen
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        batch = reconciliation.reconcile(runs, sos, configuration)
    so_by_key = {o.so_key: o for o in sos}
    items = []
    for r in batch.results:
        prev = writer.latest_record(cur, r.ef_key, cid)
        append = prev is None or prev[2] != r.input_hash
        items.append({"result": r, "prev": prev, "append": append,
                      "rows": codec.record_rows(r, so_by_key) if append else None})
    batch_row = {"batch_id": str(uuid.uuid4()), "job_id": job_id, "configuration_id": cid, "issuer_id": issuer_id,
                 "partition_input_hash": fp, "output_hash": batch.output_hash,
                 "output_json": codec.checked_envelope(codec.e6(batch), batch.output_hash, "E6"),
                 "results_count": len(batch.results), "records_appended": sum(1 for i in items if i["append"])}
    return "planned", PartitionPlan(issuer_id=issuer_id, configuration_id=cid, batch=batch, items=items,
                                    batch_row=batch_row, chosen=chosen)


def write_partition(cur, plan, check=True):
    """Step 4.4-4.5: T12, then per result T13 + T14 + T15 (when its input_hash changed) and T16, asserting the
    section 11.6 Python mirror first unless check=False (tests use that to prove the database refuses on its own)."""
    batch_id = writer.insert_batch(cur, plan.batch_row)
    results, record_hashes = [], {}
    for i, item in enumerate(plan.items):
        r, prev = item["result"], item["prev"]
        if item["append"]:
            t13, t14, t15 = item["rows"]
            if check:
                problems = codec.check_record(dict(t13), [dict(x) for x in t14], [dict(x) for x in t15],
                                              _fact(cur, r.ef_key), _records_for(cur, [o.so_key for o in r.observations]),
                                              plan.batch.configuration.reconciliation_version)
                if problems:
                    raise codec.CodecError("record decomposition differs from E4: " + "; ".join(problems[:5]))
            rid = writer.insert_record(cur, t13, t14, t15, batch_id, prev)
        else:
            rid = prev[0]
            if prev[3] != r.output_hash:
                raise writer.NondeterminismError(f"fact {r.ef_key}: same input_hash, different output_hash")
        record_hashes[rid] = r.output_hash
        results.append({"batch_id": batch_id, "result_ordinal": i, "ef_key": r.ef_key, "record_id": rid,
                        "appended": item["append"]})
    if check:
        problems = codec.check_batch(plan.batch_row, results, record_hashes)
        if problems:
            raise codec.CodecError("batch decomposition differs from E6: " + "; ".join(problems[:5]))
    writer.insert_results(cur, results)
    return batch_id


def reconcile_partition(conn, job_id, configuration, issuer_id, documents):
    """One partition pass in its own REPEATABLE READ transaction (section 9.4 step 4). Returns a report."""
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read")
            state, plan = plan_partition(cur, job_id, configuration, issuer_id, documents)
            if state == "inputs_changed":
                conn.rollback()
                return {"issuer_id": issuer_id, "state": state, "detail": plan}
            if state == "unchanged":
                conn.rollback()
                return {"issuer_id": issuer_id, "state": state, "chosen": plan}
            batch_id = write_partition(cur, plan)
        conn.commit()
        return {"issuer_id": issuer_id, "state": "written", "batch_id": batch_id, "results": len(plan.items),
                "appended": plan.batch_row["records_appended"], "chosen": plan.chosen}
    except (writer.NondeterminismError, codec.CodecError, inputs.InputError, psycopg2.Error) as exc:
        conn.rollback()
        return {"issuer_id": issuer_id, "state": "failed", "error": _redact(exc),
                "reason": "nondeterminism" if isinstance(exc, writer.NondeterminismError) else "error"}


def reconcile(conn, configuration_id, *, no_validate=False, code_revision=None, only_issuer=None):
    """The partition pass of section 9.4 over every issuer (or one). Returns a report dict."""
    with conn.cursor() as cur:
        configuration = load_configuration(cur, configuration_id)
    conn.rollback()
    scope = f"issuer:{only_issuer}" if only_issuer else "all_issuers"
    if not lock_exclusive(conn):
        job_id = start_job(conn, "reconcile", configuration_id=configuration_id, code_revision=code_revision,
                           scope=scope, parameters={"no_validate": no_validate})
        final_event(conn, job_id, "refused", {"reason": "busy"})
        return {"job_id": job_id, "state": "refused", "reason": "busy"}
    try:
        job_id = start_job(conn, "reconcile", configuration_id=configuration_id, code_revision=code_revision,
                           scope=scope, parameters={"no_validate": no_validate})
        mark_abandoned(conn, job_id)
        report = {"job_id": job_id, "configuration_id": configuration_id, "validations": [], "partitions": []}
        # steps 1-2: one F5 run per document; its canonical validation run, created if missing
        with conn.cursor() as cur:
            loader.session(cur)
            refs = loader.all_run_refs(cur)
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                sel = reconciliation.select_runs(refs, configuration)
            missing = [d.selected_run for d in sel.documents
                       if selection.canonical_validation_run(cur, d.selected_run, configuration.versions) is None]
        conn.rollback()
        if missing and no_validate:
            final_event(conn, job_id, "refused", {"reason": "missing_validations", "f5_runs": missing})
            return dict(report, state="refused", reason="missing_validations", f5_runs=missing)
        failed_validations = []
        for run_id in missing:
            v = validate(conn, run_id, parent_job=job_id, take_lock=False, code_revision=code_revision)
            report["validations"].append(v)
            if v["state"] not in ("succeeded", "already_present"):
                failed_validations.append(run_id)
        # step 3: partitions by issuer. A selected run that still has no canonical validation run (its validation
        # failed) blocks the partition of its filing's evidenced issuer: that partition is never reconciled without
        # the document (no silent loss of evidence, no fallback to another run).
        docs_by_issuer, blocked = {}, set()
        with conn.cursor() as cur:
            for d in sel.documents:
                vrk = selection.canonical_validation_run(cur, d.selected_run, configuration.versions)
                if vrk is None:
                    cur.execute("select l.issuer_id from filing_issuer_links l join financial_extraction_runs r "
                                "on r.cse_filing_id = l.cse_filing_id where r.id = %s order by l.id desc limit 1",
                                (d.selected_run,))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        blocked.add(str(row[0]))
                    continue
                issuer = selection.run_issuer(cur, vrk)
                if issuer is not None:
                    docs_by_issuer.setdefault(issuer, set()).add(d.document_sha256)
            partitions = set(docs_by_issuer) | selection.issuers_with_facts(cur, configuration_id) | blocked
        conn.rollback()
        if only_issuer:
            partitions &= {only_issuer}
        # step 4: each partition in issuer order, in its own transaction
        for issuer in sorted(partitions):
            if issuer in blocked:
                report["partitions"].append({"issuer_id": issuer, "state": "failed", "reason": "validation_failed",
                                             "detail": "a selected F5 run of this issuer has no canonical validation run"})
                continue
            report["partitions"].append(reconcile_partition(conn, job_id, configuration, issuer,
                                                            docs_by_issuer.get(issuer, set())))
        bad = [p for p in report["partitions"] if p["state"] in ("failed", "inputs_changed")]
        details = {"partitions": len(report["partitions"]),
                   "written": sum(1 for p in report["partitions"] if p["state"] == "written"),
                   "unchanged": sum(1 for p in report["partitions"] if p["state"] == "unchanged"),
                   "failed": [{"issuer_id": p["issuer_id"], "state": p["state"]} for p in bad],
                   "failed_validations": failed_validations,
                   "chosen": [c for p in report["partitions"] for c in p.get("chosen", [])]}
        state = "failed" if bad or failed_validations else "succeeded"
        final_event(conn, job_id, state, details)
        return dict(report, state=state, **{k: v for k, v in details.items() if k != "chosen"})
    finally:
        unlock(conn, shared=False)


def configuration_from_present_runs(cur):
    """A configuration accepting every F3 / F4 / F5 version tuple present among the persisted F5 runs (a convenience
    for `register-configuration --all-present`; adopting it for consumers remains the owner's designation)."""
    refs = loader.all_run_refs(cur)
    if not refs:
        raise JobRefused("no F5 run is persisted")
    return reconciliation.ReconciliationConfiguration(accepted_f3=tuple({r.f3_version for r in refs}),
                                                      accepted_f4=tuple({r.f4_version for r in refs}),
                                                      accepted_f5=tuple({r.f5_version for r in refs}))


def code_revision():
    return ops_settings.code_revision()


def dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True, default=str)
