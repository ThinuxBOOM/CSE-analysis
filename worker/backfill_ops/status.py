"""
The operator status and the plan (design section 20: `status` "armed state, stage, items by state, today's requests
against budget, blocks, holds, last wake-ups, temp root, DB size"; `plan` "the work a stage would do (counts), with no
CSE contact"). Read-only; JSON suitable for P1's status pattern. The User-Agent is never repeated (it carries the
contact e-mail).

Alerts (section 20): a block; a stopped stage (its last slices stopped on the circuit breaker, owner decision A5); a
cleanup failure; today's budget exhausted; the temporary root or the database disk under its threshold; a stale lease
(an active lease whose holder session is gone); a runner wake-up still active although its session is gone; items
awaiting an operator re-queue; a registered configuration not yet designated; a due backup checkpoint.
"""
import shutil

from ..backfill_discovery import IDENTITY_STAGE, LINK_PASS_IDENTITY, STAGE as DISCOVERY_STAGE
from ..backfill_discovery import discovery as hb3_discovery, identity as hb3_identity, security_master
from ..backfill_discovery.errors import DiscoveryRefused
from ..backfill_documents import gate as hb4_gate, temproot
from ..backfill_documents.errors import DocumentRefused
from ..backfill_f6 import configuration as hb5_configuration, readiness as hb5_readiness, validation as hb5_validation
from ..backfill_transport import STOPPED_STAGE_SLICES, gates as hb2_gates, ledger as hb2_ledger
from ..financial_backfill import keys, store
from . import (BULK_STAGE, DB_DISK_MIN_FREE_BYTES, DOCUMENT_STAGES, EXIT_ATTENTION, EXIT_BLOCKED, EXIT_OK,
               PILOT_STAGE, TOOL_VERSION, claim)
from .runner import audit_done_today, blocks_of, database_bytes, document_work, documents_under_arming, pilot_filings
from .util import jsonable, one, rows

PUBLIC_ARMING = ("id", "armed", "armed_stages", "window_first_date", "window_last_date", "daily_request_budget",
                 "combined_daily_ceiling", "slice_max_json_requests", "slice_max_documents", "slice_max_seconds",
                 "attempts_per_json_request", "attempts_per_document", "item_max_attempts", "host", "expected_requests",
                 "g1_reference", "recorded_at")


def _stages(arming):
    return list(arming.get("armed_stages") or []) if arming and arming.get("armed") else []


def items_by_kind(conn):
    out = {}
    for (kind, state), n in store.state_counts(conn).items():
        out.setdefault(kind, {})[state] = n
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def stale_lease(conn):
    lease = store.active_lease(conn)
    if lease is None:
        return None
    alive = one(conn, "select hb_session_alive(holder_pid, holder_started_at) from backfill_leases where id = %s",
                (lease["id"],))
    return None if alive else jsonable(lease)


def dead_runner_wakeups(conn):
    return [r["id"] for r in rows(conn, "select id from backfill_wakeups where state = 'active' and tool_version = %s "
                                        "and not hb_session_alive(backend_pid, backend_started_at) order by id",
                                  (TOOL_VERSION,))]


def disk(cfg_temp_root, db_disk_path, conn):
    out = {"database_bytes": database_bytes(conn), "temp_root": cfg_temp_root}
    alerts = []
    if cfg_temp_root:
        problems = temproot.root_problems(cfg_temp_root)
        if problems:
            out["temp_root_problems"] = problems
            alerts.append(("temp_root", "; ".join(problems)))
        else:
            out["temp_root_free_bytes"] = temproot.free_bytes(cfg_temp_root)
            space = temproot.free_space_problem(cfg_temp_root)
            if space:
                alerts.append(("temp_space", space))
    if db_disk_path:
        try:
            free = shutil.disk_usage(db_disk_path).free
            out["database_disk_free_bytes"] = free
            if free < DB_DISK_MIN_FREE_BYTES:
                alerts.append(("db_disk", f"{free} bytes free on the database disk ({db_disk_path}), under "
                                          f"{DB_DISK_MIN_FREE_BYTES}"))
        except OSError as exc:
            out["database_disk"] = f"unreadable: {exc}"
    return out, alerts


def status(conn, wall, *, temp_root=None, db_disk_path=None):
    """(exit status, report)."""
    arming = store.arming_in_force(conn)
    stages = _stages(arming)
    alerts = []
    rep = {"tool_version": TOOL_VERSION,
           "arming": jsonable({k: (arming or {}).get(k) for k in PUBLIC_ARMING}) if arming else None,
           "user_agent_recorded": bool(arming and arming.get("user_agent")),
           "armed_stages": stages, "items": items_by_kind(conn)}
    if arming and arming.get("armed"):
        rep["budget"] = jsonable(store.budget(conn, keys.colombo_date(wall)))
        if rep["budget"].get("exhausted"):
            alerts.append(("budget", "today's Phase 2 request budget is exhausted: work is deferred to the next "
                                     "Colombo day"))
        for stage in stages:
            if stage in (DISCOVERY_STAGE,) + DOCUMENT_STAGES and hb2_gates.stage_stopped(
                    hb2_ledger.completed_lease_results(conn, stage, arming["recorded_at"]), STOPPED_STAGE_SLICES):
                alerts.append(("stage_stopped", f"{stage}: its last {STOPPED_STAGE_SLICES} slices stopped on the "
                                                f"circuit breaker; a newer owner arming decision is required (A5)"))
    blocks = blocks_of(conn)
    rep["blocks"] = blocks
    if blocks:
        alerts.append(("block", "an unacknowledged block stops every Phase 2 stage until the owner acknowledges it"))
    holds = [h for h in store.hold_state(conn) if h["resolution"] in (None, "keep_held")]
    rep["holds"] = {"unresolved_or_kept": len(holds), "ids": [h["hold_id"] for h in holds]}
    rep["wakeups"] = jsonable(store.recent_wakeups(conn, 5))
    rep["runner"] = jsonable(claim.live_runners(conn))
    dead = dead_runner_wakeups(conn)
    if dead:
        alerts.append(("dead_runner", f"runner wake-up(s) {dead} are active but their sessions are gone: the next "
                                      f"runner expires them"))
    stale = stale_lease(conn)
    rep["active_lease"] = jsonable(store.active_lease(conn))
    if stale:
        alerts.append(("stale_lease", f"lease {stale['id']} is active but its holder session is gone: the next CSE "
                                      f"slice expires it and recovers its items from evidence"))
    rep["disk"], disk_alerts = disk(temp_root, db_disk_path, conn)
    alerts += disk_alerts
    docs = rep["items"].get("document", {})
    if docs.get("cleanup_failed"):
        alerts.append(("cleanup_failed", f"{docs['cleanup_failed']} document item(s) cleanup_failed: the document "
                                         f"stage is stopped until an operator re-queues them"))
    rep["f6"] = {"pending_validations": len(hb5_validation.pending_runs(conn)),
                 "designated_configuration": hb5_configuration.designated(conn),
                 "latest_snapshot": jsonable(rows(conn, "select id, rule_version, snapshot_digest, taken_at from "
                                                        "backfill_coverage_snapshots order by id desc limit 1"))}
    for kind in ("validate", "reconcile"):
        failed = rep["items"].get(kind, {}).get("failed")
        if failed:
            alerts.append((f"{kind}_failed", f"{failed} {kind} item(s) failed: investigate, then requeue"))
    if rep["f6"]["designated_configuration"] is None and one(conn, "select exists (select 1 from "
                                                                   "financial_reconciliation_configurations)"):
        alerts.append(("designation", "a configuration is registered but none is designated: the owner designates "
                                      "it through F6.4's owner path"))
    if BULK_STAGE in stages:
        block = (arming.get("expected_requests") or {}).get("documents")
        done = documents_under_arming(conn, arming["id"])
        rep["checkpoint_block"] = {"documents": done, "block": block}
        if block and done >= int(block):
            alerts.append(("checkpoint_due", f"the HB-S4 block of {block} documents is done: record a backup "
                                             f"checkpoint and arm the next block (section 21)"))
    rep["alerts"] = [list(a) for a in alerts]
    code = EXIT_BLOCKED if blocks else (EXIT_ATTENTION if alerts else EXIT_OK)
    return code, jsonable(rep)


def plan(conn, wall):
    """What the next wake-up would do, with no write and no CSE contact."""
    arming = store.arming_in_force(conn)
    stages = _stages(arming)
    out = {"tool_version": TOOL_VERSION, "armed_stages": stages}
    sm, refusals = security_master.status(conn, wall, arming)
    out["security_master"] = sm.provenance() if sm is not None else {"unavailable": refusals}
    if IDENTITY_STAGE in stages:
        first = store.item_by_key(conn, keys.link_pass(LINK_PASS_IDENTITY)["natural_key"])
        out["identity_pass"] = (store.current_state(conn, first["id"]) or {}).get("state") if first else "not_run"
    if DISCOVERY_STAGE in stages and sm is not None:
        try:
            p = hb3_discovery.current_plan(conn, wall, arming)
            out["discovery"] = hb3_discovery.closure(conn, p).summary() | {"plan_items": len(p.natural_keys)}
            ie4 = hb3_identity.ie4_pass(conn, p.fingerprint())
            out["ie4_pass"] = (store.current_state(conn, ie4["id"]) or {}).get("state") if ie4 else "not_run"
        except DiscoveryRefused as exc:
            out["discovery"] = {"refused": [list(r) for r in exc.refusals]}
    for stage in DOCUMENT_STAGES:
        if stage not in stages:
            continue
        try:
            gate = hb4_gate.document_gate(conn, wall, arming)
            ids = pilot_filings(arming) if stage == PILOT_STAGE else None
            out[stage] = {"gate": "open", "work": document_work(conn, gate, ids)}
            if stage == BULK_STAGE:
                out[stage]["checkpoint_block"] = {"documents": documents_under_arming(conn, arming["id"]),
                                                  "block": (arming.get("expected_requests") or {}).get("documents")}
        except DocumentRefused as exc:
            out[stage] = {"gate": [list(r) for r in exc.refusals]}
    out["f6"] = {"pending_validations": len(hb5_validation.pending_runs(conn)),
                 "designated_configuration": hb5_configuration.designated(conn)}
    if arming and arming.get("armed"):
        w = (arming["window_first_date"], arming["window_last_date"])
        out["f6"]["readiness"] = hb5_readiness.of(conn, w).summary()
        out["audit_due_today"] = not audit_done_today(conn, wall)
    return jsonable(out)
