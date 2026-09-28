"""
P2 orchestration: capture, resume, reprocess, recover, sweep, record-missed, acknowledge-block, export, verify.

Order for every CSE response (P0.5 / P2 contract):
    journal intent -> HTTP response -> spool (fsync, atomic, SHA-256 verified) -> PostgreSQL archive COMMIT
    -> (all responses of the run) -> raw observations -> canonicalise/reconcile -> run state (terminal event)
Backups are not part of any of this: P1's timers dump the database and sync the spool afterwards, and the protection
level (dimension F) is only ever computed read-only. Nothing in this module deletes or updates anything.

Two connections as the capture role: `ctl` (archive, run ledger, global lock - session-level, so a dead process frees
it) and `work` (the frozen Stage E db functions, which commit on their own).
"""
import contextlib
import json
import os
import time
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable

from ..ops import settings as ops_settings
from . import TOOL_VERSION, archive, completeness, config as cfgmod, derive, http, runs

EXIT_CODES = {"succeeded": 0, "failed": 1, "partial": 2, "blocked": 3, "missed": 0}
EXIT_DATABASE_UNAVAILABLE = 4
EXIT_REFUSED = 5


@dataclass
class Runtime:
    """Injectable runtime: tests pass a fake transport, clocks and sleep; production uses the defaults."""
    transport: object = None
    clock: Callable = time.monotonic
    wall: Callable = http.utcnow
    sleep: Callable = time.sleep
    log: Callable = print
    notes: list = field(default_factory=list)


def colombo_today(wall):
    return wall().astimezone(cfgmod.COLOMBO).date()


def parse_trading_date(text):
    """Strict YYYY-MM-DD. The trading date is ALWAYS an explicit input: nothing here derives it from a clock."""
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError(f"trading date must be YYYY-MM-DD, got {text!r}") from None
    if d.isoformat() != text:
        raise ValueError(f"trading date must be YYYY-MM-DD, got {text!r}")
    return d


@contextlib.contextmanager
def _locked(ctl, cfg, rt, contacting_cse):
    """Every write command runs inside this: security preflight, the global capture lock (held for the whole command,
    released at the end or when the process dies), spool recovery, abandoned-run detection and - for commands that
    contact CSE - the writable-spool and G-1 block gates. Yields the preflight facts."""
    problems = runs.security_preflight(ctl, cfg.expected_db_role)
    if problems:
        raise runs.RunRefused("security preflight failed: " + "; ".join(problems))
    if not runs.acquire_global_lock(ctl):
        raise runs.RunRefused("another P2 capture process holds the global capture lock (G-1: one request at a time)")
    try:
        out = {"recovered": archive.recover(archive.PgArchiveStore(ctl), cfg.spool_root, log=rt.log),
               "abandoned": runs.mark_abandoned(ctl, rt.log)}
        if contacting_cse:
            if not (os.path.isdir(cfg.spool_root) and os.access(cfg.spool_root, os.W_OK)):
                raise runs.RunRefused(f"spool {cfg.spool_root} is not a writable directory: no response could be "
                                      f"captured durably, so no request is made")
            blocks = runs.unacknowledged_blocks(ctl)
            if blocks:
                raise runs.RunRefused(
                    "G-1: CSE blocked or rate-limited earlier capture run(s) " + ", ".join(b["run_id"] for b in blocks)
                    + ". Automated capture stays stopped until the owner reviews it and records `acknowledge-block`.")
        yield out
    finally:
        try:
            ctl.rollback()
            runs.release_global_lock(ctl)
        except Exception:  # noqa: BLE001 — a dead connection has released it already
            pass


def _requester(ctl, cfg, run, policy, rt):
    store = archive.PgArchiveStore(ctl)
    archiver = archive.Archiver(store, cfg.spool_root, run, rt.log)
    throttle = http.Throttle(policy.request.min_interval_seconds, clock=rt.clock, sleep=rt.sleep)
    throttle.seed(runs.seconds_since_last_request(ctl, rt.wall()))       # spacing across processes too
    transport = rt.transport or http.RequestsTransport(policy.request.max_body_bytes)
    return http.Requester(transport, policy.request, run["user_agent"], archiver, throttle=throttle, clock=rt.clock,
                          wall=rt.wall, sleep=rt.sleep, max_requests=policy.max_requests, log=rt.log)


def _fetch_phase(ctl, run, policy, req, rt):
    """Request whatever the run still lacks, in plan order; responses already archived OK are never re-requested."""
    ok = derive.ok_attempts(ctl, run["id"])

    def get(spec):
        if spec.request_key in ok:
            return derive.body_of(ctl, ok[spec.request_key]["body_sha256"])[1]
        res = req.fetch(spec)
        return derive.body_of(ctl, res.ok.body_sha256)[1] if res.ok else None

    universe_parsed = get(cfgmod.universe_spec())
    if run["run_kind"] == "metadata_sweep":
        if universe_parsed is None:
            return
        symbols = [e["symbol"] for e in derive.universe_entries(universe_parsed)[0]]
        for s in symbols[: policy.sweep_limit] if policy.sweep_limit is not None else symbols:
            get(cfgmod.company_info_spec(s, "metadata_sweep"))
        return
    ts_parsed = get(cfgmod.trade_summary_spec())
    if ts_parsed is None:
        return
    ev = derive.session_evidence(ts_parsed, run["trading_date"], run["capture_mode"])
    for w in ev["warnings"]:
        rt.log(f"WARNING: {w}")
    if not ev["session_matches_trading_date"]:
        rt.log("session evidence does not match the trading date: no further requests, nothing derived")
        return
    if universe_parsed is None:
        return                         # without the universe the absent-security fallback cannot be planned
    universe = derive.universe_entries(universe_parsed)[0]
    ts_by = derive.trade_summary_by_symbol(ts_parsed)[0]
    p = derive.plan(universe, ts_by, run["trading_date"], policy)
    for s in p["absent_fallback"]:
        get(cfgmod.company_info_spec(s, "absent_fallback"))
    for s in p["cross_check"]:
        get(cfgmod.company_info_spec(s, "cross_check"))


def _next_pass(ctl, run_id):
    with ctl.cursor() as cur:
        cur.execute("select coalesce(max(pass_no), 0) + 1 from market_capture_security_results where run_id = %s",
                    (run_id,))
        n = cur.fetchone()[0]
    ctl.commit()
    return n


def _record(ctl, run_id, pass_no, pass_kind, sym, **kw):
    try:
        with ctl.cursor() as cur:
            cur.execute("insert into market_capture_security_results (run_id, pass_no, pass_kind, symbol, company_id, "
                        "in_universe, in_trade_summary, role, cross_check, trade_summary_response_id, "
                        "company_info_response_id, raw_status, raw_observation_id, canonical_status, "
                        "reconciliation_status, validation_status, reason) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s, %s, %s, %s, %s, %s)",
                        (run_id, pass_no, pass_kind, sym, kw.get("company_id"), kw["in_universe"], kw["in_ts"],
                         kw["role"], kw["cross"], kw.get("ts_id"), kw.get("ci_id"), kw["raw_status"],
                         kw.get("raw_observation_id"), kw.get("canonical_status", "not_attempted"),
                         kw.get("reconciliation_status"), kw.get("validation_status"), kw.get("reason")))
        ctl.commit()
    except BaseException:
        ctl.rollback()
        raise


def derive_run(ctl, work, run, policy, pass_kind, rt):
    """Derive raw observations + canonical rows for every expected security from the run's ARCHIVED responses."""
    ok = derive.ok_attempts(ctl, run["id"])
    ts_att = ok["tradeSummary"]
    ts_by = derive.trade_summary_by_symbol(derive.body_of(ctl, ts_att["body_sha256"])[1])[0]
    universe = (derive.universe_entries(derive.body_of(ctl, ok["allSecurityCode"]["body_sha256"])[1])[0]
                if "allSecurityCode" in ok else [])
    p = derive.plan(universe, ts_by, run["trading_date"], policy)
    # the active flags come from allSecurityCode, so that response's own time decides whether they are newer
    snapshot_at = ok["allSecurityCode"]["observed_at"] if "allSecurityCode" in ok else None
    ids, companies = derive.ensure_companies(work, universe, ts_by, snapshot_at)
    tol = derive.load_tolerances(work)
    pass_no = _next_pass(ctl, run["id"])
    last = derive.last_outcomes(ctl, run["id"])
    usyms = [e["symbol"] for e in universe]
    in_u, cross = set(usyms), set(p["cross_check"])
    for sym in usyms + sorted(set(ts_by) - in_u):
        ts_row = ts_by.get(sym)
        role = ("traded" if sym in in_u else "traded_not_in_universe") if ts_row is not None else (
            "absent_fallback" if policy.absent_fallback else "absent_not_expected")
        ci_key = f"companyInfoSummery:{sym}"
        ci_att = ok.get(ci_key)
        base = dict(in_universe=sym in in_u, in_ts=ts_row is not None, role=role, cross=sym in cross,
                    ts_id=ts_att["id"], ci_id=ci_att["id"] if ci_att else None, company_id=ids.get(sym))
        if role == "absent_not_expected":
            _record(ctl, run["id"], pass_no, pass_kind, sym, raw_status="not_expected",
                    reason=f"absent from tradeSummary; no companyInfoSummery fallback in {run['capture_mode']} mode",
                    **base)
            continue
        if ts_row is None and ci_att is None:
            lo = last.get(ci_key)
            why = (f"companyInfoSummery not archived (last attempt: {lo[0]}" + (f", HTTP {lo[1]})" if lo[1] else ")")
                   if lo else "companyInfoSummery not requested (smoke limit, request budget or the run was stopped)")
            _record(ctl, run["id"], pass_no, pass_kind, sym, raw_status="source_missing", reason=why, **base)
            continue
        if not ids.get(sym):
            _record(ctl, run["id"], pass_no, pass_kind, sym, raw_status="company_missing",
                    reason="no name in allSecurityCode/tradeSummary to create its security-master row (not invented)",
                    **base)
            continue
        ci_body = derive.body_of(ctl, ci_att["body_sha256"])[1] if ci_att else None
        res = derive.derive_security(work, run=run, company_id=ids[sym], ts_row=ts_row, ts_attempt=ts_att,
                                     ci_body=ci_body, ci_attempt=ci_att,
                                     ci_purpose=ci_att["purpose"] if ci_att else None, tolerances=tol)
        _record(ctl, run["id"], pass_no, pass_kind, sym, **base, **res)
    rt.log(f"derivation pass {pass_no} ({pass_kind}) done for {len(usyms) + len(set(ts_by) - in_u)} securities")
    return {"pass_no": pass_no, "pass_kind": pass_kind, "companies": companies, "tolerances": tol}


def _stop_info(stop):
    if stop is None:
        return None
    attempt = getattr(stop, "attempt", None)
    return {"type": type(stop).__name__, "message": str(stop),
            "attempt": {"request_key": attempt.request_key, "attempt_no": attempt.attempt_no,
                        "outcome": attempt.outcome, "http_status": attempt.http_status} if attempt else None}


def _execute(ctl, work, cfg, run, policy, rt, pass_kind):
    req = _requester(ctl, cfg, run, policy, rt)
    stop = None
    try:
        _fetch_phase(ctl, run, policy, req, rt)
    except (http.StopRun, archive.SpoolUnavailable) as exc:
        stop = exc
        rt.log(f"STOPPED: {exc}")
    # archive.ArchiveDatabaseUnavailable propagates: nothing more can be recorded; the spool holds the attempt
    summary = completeness.summarize(ctl, run)
    derivation = None
    ev = summary.get("session_evidence")
    if policy.derive and summary["A"]["status"] == "captured" and ev and ev["session_matches_trading_date"]:
        derivation = derive_run(ctl, work, run, policy, pass_kind, rt)
        summary = completeness.summarize(ctl, run)
    stopped = "blocked" if isinstance(stop, http.Blocked) else ("stopped" if stop else None)
    state = completeness.decide_state(summary, stopped)
    reason = str(stop) if stop else {"succeeded": "capture complete", "partial": "capture incomplete (see C)",
                                     "failed": "required source not archived or session evidence mismatch"}.get(state)
    details = {"pass_kind": pass_kind, "requests_made": req.requests_made, "throttle_waits": len(req.throttle.waits),
               "stop": _stop_info(stop), "derivation": derivation, "summary": summary}
    runs.append_event(ctl, run["id"], state, reason, details)
    rt.log(f"run {run['id']}: {state} ({req.requests_made} CSE request(s) in this invocation)")
    return state, {"run_id": run["id"], "state": state, "reason": reason, **details}


def start(ctl, work, cfg, *, trading_date, policy, rt=None, basis="operator"):
    """A new capture (or metadata sweep) run for an EXPLICIT trading date."""
    rt = rt or Runtime()
    today = colombo_today(rt.wall)
    if trading_date > today:
        raise runs.RunRefused(f"trading date {trading_date} is after today's Colombo date {today}; nothing requested")
    policy = replace(policy, request=cfg.request_policy) if policy.request != cfg.request_policy else policy
    user_agent = cfg.user_agent
    with _locked(ctl, cfg, rt, contacting_cse=True) as pre:
        run_id = runs.create_run(ctl, run_kind=policy.run_kind, trading_date=trading_date,
                                 capture_mode=policy.capture_mode, policy=policy.as_json(), user_agent=user_agent,
                                 tool_version=TOOL_VERSION, code_revision=ops_settings.code_revision(), basis=basis)
        runs.append_event(ctl, run_id, "running", "started", {"preflight": pre, "colombo_date_at_start": str(today)})
        rt.log(f"run {run_id}: {policy.name} for trading date {trading_date} ({policy.capture_mode})")
        return _execute(ctl, work, cfg, runs.get_run(ctl, run_id), policy, rt, "capture")


def resume(ctl, work, cfg, run_id, rt=None):
    """Continue a partial / failed / abandoned run (or a blocked one after the owner's acknowledgement) under the SAME
    run id: only the requests it still lacks, then an idempotent derivation pass."""
    rt = rt or Runtime()
    run = runs.get_run(ctl, run_id)
    if run is None:
        raise runs.RunRefused(f"no run {run_id}")
    if run["user_agent"] is None:
        raise runs.RunRefused(f"run {run_id} is a missed-capture record; start a new capture instead")
    with _locked(ctl, cfg, rt, contacting_cse=True) as pre:
        st = runs.current_state(ctl, run_id)["state"]
        if st not in runs.RESUMABLE:
            raise runs.RunRefused(f"run {run_id} is {st}; only {runs.RESUMABLE} runs can be resumed")
        policy = replace(completeness.policy_from_json(run["policy"]), request=cfg.request_policy)
        runs.append_event(ctl, run_id, "running", "resumed", {"preflight": pre, "resumed_from": st})
        return _execute(ctl, work, cfg, runs.get_run(ctl, run_id), policy, rt, "resume")


def reprocess(ctl, work, cfg, run_id, rt=None):
    """Re-derive raw observations / canonical rows from the ARCHIVE only (no CSE request). Recorded as a reprocess
    pass, never as a capture; a succeeded or blocked run keeps its state."""
    rt = rt or Runtime()
    with _locked(ctl, cfg, rt, contacting_cse=False):
        run = runs.get_run(ctl, run_id)
        if run is None or run["run_kind"] != "market_capture" or run["user_agent"] is None:
            raise runs.RunRefused(f"run {run_id} is not a market capture run")
        st = runs.current_state(ctl, run_id)["state"]
        if st in ("pending", "running", "missed"):
            raise runs.RunRefused(f"run {run_id} is {st}; nothing to reprocess")
        summary = completeness.summarize(ctl, run)
        ev = summary.get("session_evidence")
        if summary["A"]["status"] != "captured" or not ev or not ev["session_matches_trading_date"]:
            raise runs.RunRefused(f"run {run_id} has no archived tradeSummary for its trading date to derive from")
        policy = completeness.policy_from_json(run["policy"])
        events = st in ("partial", "failed", "abandoned")
        if events:
            runs.append_event(ctl, run_id, "running", "reprocess (archive only, no CSE request)", {"from_state": st})
        derivation = derive_run(ctl, work, run, policy, "reprocess", rt)
        summary = completeness.summarize(ctl, run)
        state = completeness.decide_state(summary) if events else st
        if events:
            runs.append_event(ctl, run_id, state, "reprocessed from the archive",
                              {"pass_kind": "reprocess", "requests_made": 0, "derivation": derivation,
                               "summary": summary})
        return state, {"run_id": run_id, "state": state, "derivation": derivation, "summary": summary}


def recover(ctl, cfg, rt=None):
    """Ingest spooled attempts whose PostgreSQL rows are missing, then mark dead runs abandoned. No CSE request."""
    rt = rt or Runtime()
    with _locked(ctl, cfg, rt, contacting_cse=False) as pre:
        return pre


def record_missed(ctl, cfg, *, trading_date, capture_mode, reason, rt=None):
    """Record that the capture for (trading date, mode) did not happen in its window (a run: pending -> missed)."""
    rt = rt or Runtime()
    if capture_mode not in cfgmod.MODES:
        raise runs.RunRefused(f"capture mode must be one of {cfgmod.MODES}")
    with _locked(ctl, cfg, rt, contacting_cse=False):
        with ctl.cursor() as cur:
            cur.execute("select run_id, state from market_capture_run_state where run_kind = 'market_capture' and "
                        "trading_date = %s and capture_mode = %s and state in ('running', 'succeeded', 'partial')",
                        (trading_date, capture_mode))
            clash = cur.fetchall()
        ctl.commit()
        if clash:
            raise runs.RunRefused(f"{trading_date} {capture_mode} already has run(s) "
                                  f"{[(str(r), s) for r, s in clash]}")
        run_id = runs.create_run(ctl, run_kind="market_capture", trading_date=trading_date, capture_mode=capture_mode,
                                 policy={"name": "missed_record", "reason": reason}, user_agent=None,
                                 tool_version=TOOL_VERSION, code_revision=ops_settings.code_revision())
        runs.append_event(ctl, run_id, "missed", reason)
        return "missed", {"run_id": run_id, "state": "missed", "reason": reason}


def acknowledge_block(conn, run_id, note, operator=None):
    """G-1 owner review of a blocked run. NOT a capture-role command: `conn` must be P1's owner-delegation login
    (cse_migrator), which only root reaches through `sudo ops/bin/cse-capture acknowledge-block`; the capture worker
    is refused by privileges (0013), by role membership and by the table's guard trigger. No CSE request."""
    ack = runs.acknowledge_block_as_owner(conn, run_id, note, operator)
    return {"run_id": run_id, "acknowledgement_id": ack}


def verify_archive(conn, spool_root, run_id):
    """Problems (list) comparing the database archive with the spool for one run: every body's SHA-256 recomputed
    from the database AND from the spool file, every metadata record present and matching its content address."""
    problems = []
    with conn.cursor() as cur:
        cur.execute("select r.sequence_no, r.request_key, r.body_sha256, r.spool_body_key, r.spool_record_key, "
                    "b.body_base64 from market_source_responses r left join market_response_bodies b "
                    "on b.body_sha256 = r.body_sha256 where r.run_id = %s order by r.sequence_no", (run_id,))
        rows = cur.fetchall()
    conn.commit()
    import base64
    import hashlib
    for seq, key, sha, body_key, rec_key, b64 in rows:
        if sha is not None:
            if hashlib.sha256(base64.b64decode(b64)).hexdigest() != sha:
                problems.append(f"seq {seq} {key}: database body does not match its SHA-256")
            try:
                if hashlib.sha256(archive.spool.read(spool_root, body_key)).hexdigest() != sha:
                    problems.append(f"seq {seq} {key}: spool body does not match the database SHA-256")
            except OSError as exc:
                problems.append(f"seq {seq} {key}: spool body missing ({exc.strerror})")
        if rec_key is not None:
            try:
                archive.load_spooled(spool_root, rec_key)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"seq {seq} {key}: spool record problem: {exc}")
    return {"run_id": run_id, "attempts": len(rows), "problems": problems}


def export_company_info(conn, run_id, out_path, repo_root=None):
    """companyInfoSummery bodies of a run in F5 link_issuers --company-info-json format ({query_symbol, observed_at,
    body, source_ref}). G-1: raw CSE responses must never be committed to Git, so a path inside the repository is
    refused."""
    repo = os.path.realpath(repo_root or os.path.join(os.path.dirname(__file__), "..", ".."))
    target = os.path.realpath(out_path)
    if os.path.commonpath([repo, target]) == repo:
        raise runs.RunRefused(f"refusing to write raw CSE responses inside the repository ({repo}) - G-1 control 8")
    ok = derive.ok_attempts(conn, run_id)
    items = []
    for key, att in sorted(ok.items()):
        if key.startswith("companyInfoSummery:"):
            items.append({"query_symbol": att["symbol"], "observed_at": att["observed_at"].isoformat(),
                          "body": derive.body_of(conn, att["body_sha256"])[1],
                          "source_ref": f"market_source_responses:{att['id']}"})
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, sort_keys=True)
    return {"run_id": run_id, "file": target, "bodies": len(items)}


def status(conn, run_id=None, trading_date=None):
    """Read-only report: state history, A-E (recomputed from the database) and F (protection, if this role can read
    P1's backup ledger)."""
    if run_id is None:
        with conn.cursor() as cur:
            cur.execute("select run_id from market_capture_run_state where trading_date = %s order by created_at",
                        (trading_date,))
            ids = [str(r[0]) for r in cur.fetchall()]
        conn.commit()
        return [status(conn, run_id=i) for i in ids]
    run = runs.get_run(conn, run_id)
    if run is None:
        raise runs.RunRefused(f"no run {run_id}")
    return {"run": {k: run[k] for k in ("id", "run_kind", "trading_date", "capture_mode", "trading_date_basis",
                                        "user_agent", "tool_version", "created_at")},
            "state": runs.current_state(conn, run_id), "history": runs.history(conn, run_id),
            "completeness": completeness.summarize(conn, run), "F_protection": completeness.protection(conn, run_id)}


def exit_code(state):
    return EXIT_CODES.get(state, 1)
