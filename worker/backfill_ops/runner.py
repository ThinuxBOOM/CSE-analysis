"""
One wake-up of the Phase 2 runner (design section 20, "one wake-up, deterministic"), what the systemd timer starts:

    1. the claim         the single runner (claim.py): busy -> a 'busy' wake-up is recorded, nothing else happens;
                         then dead wake-ups are expired (HB-1: a live session is never taken over)
    2. the preflight     HB-6's composite (HB-1, F6.4, HB-3, HB-4, HB-5 and its own): any problem -> refused (5)
    3. the gates         an unacknowledged Phase 2 or P2 block stops EVERY stage (section 16.3) -> blocked (3);
                         while armed, a clock that is not NTP-synchronised or went backwards -> refused (5, P3's
                         rule; disarmed, only clock-independent F6 bookkeeping runs, as P3's disarmed wake-up)
    4. non-CSE work      between slices, never holding ...312 (section 16.5), each bounded:
                         - stage bookkeeping through HB-3: the IE-2 identity pass (HB-S1 armed), the plan items and,
                           once the current plan is closed, its IE-4 closure pass (HB-S2 armed)
                         - F6 work through HB-5: a validate-pending batch, the configuration registered (never
                           designated: F6.4's owner path), document promotion, the reconcile passes that are due,
                           promotion again
                         - the audit, at most once per Colombo day while a window W is armed; its per-filing table
                           is exported outside the repository
    5. one CSE slice     in stage order: HB-3's discovery slice while the current plan is open (HB-S2); otherwise
                         HB-4's document slice (HB-S3 over the owner's pilot sample, or HB-S4 up to the armed
                         checkpoint block). The slice takes P2's lock ...312, applies every HB-2 gate (arming, budget,
                         P3's quiet window, blocks, clock), recovers dead slices from evidence and releases the lock
                         after the release guard. Busy -> nothing taken over.
    6. the record        the runner's wake-up row is finished with its result (L8)

Failed work is never retried in a loop and never re-queued automatically (HB-1's rule): a failed validation or
reconcile pass, an item awaiting a re-queue after late evidence (M4), a cleanup failure, a missing designation, a due
backup checkpoint or a finished pilot is reported as needing attention (exit 2) for the operator or the owner.

SIGTERM raises SystemExit for the whole wake-up (HB-4's own handler, which HB-4 also installs inside its slices), so
the current unit unwinds: a slice keeps its lease for recovery (G2), an F6.4 job that never finished is marked
abandoned by F6.4's next reconcile and its run validated again, and the wake-up is recorded 'stopped'.
"""
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..backfill_discovery import IDENTITY_STAGE, LINK_PASS_IDENTITY, STAGE as DISCOVERY_STAGE
from ..backfill_discovery import discovery as hb3_discovery, identity as hb3_identity
from ..backfill_discovery.errors import DiscoveryRefused, SecurityMasterUnavailable
from ..backfill_documents import gate as hb4_gate, planning as hb4_planning, signals as hb4_signals
from ..backfill_documents.errors import DocumentRefused
from ..backfill_documents.worker import DocumentSlice
from ..backfill_f6 import RECONCILE_BATCH, VALIDATE_BATCH, audit as hb5_audit, configuration as hb5_configuration
from ..backfill_f6 import promotion as hb5_promotion, reconcile as hb5_reconcile, validation as hb5_validation
from ..backfill_f6.errors import F6Refused
from ..backfill_transport import gates as hb2_gates, ledger as hb2_ledger
from ..backfill_transport.errors import SliceBusy, SliceRefused
from ..financial_backfill import keys, states, store
from ..market_capture import runs as p2runs
from ..ops import settings as ops_settings
from . import (BULK_STAGE, EXIT_ATTENTION, EXIT_BLOCKED, EXIT_OK, EXIT_REFUSED, PILOT_STAGE, RULE_VERSION,
               TOOL_VERSION, claim, preflight as checks, reports)
from .util import jsonable, one

# Slice refusals that only mean "not now" (the next wake-up tries again); every other refusal needs the operator.
WAITING = {"disarmed", "budget", "quiet_window", "arming_changed", "discovery_open", "issuer_evidence"}
CLAIMABLE = {"discovered", "pending", "retry_wait"}
IN_FLIGHT = {"requesting", "processing", "abandoned"}


def read_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def database_bytes(conn):
    return one(conn, "select pg_database_size(current_database())")


def audit_done_today(conn, now):
    """Did an earlier runner wake-up of today (Colombo, by the runner's own clock: its recorded runner_time) record an
    audit snapshot? (A wake-up's details name the snapshot its audit recorded.)"""
    start, _ = keys.colombo_day_bounds(keys.colombo_date(now))
    return one(conn, """
        select exists (select 1 from backfill_wakeups where tool_version = %s and state <> 'skipped'
                          and runner_time >= %s and details -> 'audit' ->> 'snapshot_id' is not null)""",
               (TOOL_VERSION, start))


def documents_under_arming(conn, arming_id, stage=BULK_STAGE):
    """Distinct document items requested in slices of this arming decision and stage (section 21: the HB-S4 block)."""
    return one(conn, """
        select count(distinct a.item_id) from backfill_request_attempts a join backfill_leases l on l.id = a.lease_id
         where a.request_class = 'document' and l.details ->> 'arming_id' = %s and l.details ->> 'stage' = %s""",
               (str(arming_id), stage))


def pilot_filings(arming):
    ids = (arming or {}).get("expected_requests", {}).get("pilot_filings") if arming else None
    return sorted({int(i) for i in ids}) if ids else []


def document_work(conn, gate, filing_ids=None):
    """What a document slice would have to do in W (restricted to `filing_ids` for the pilot): filings without an item
    for their current path version, claimable items, items in flight (recovery), and the items already final."""
    wanted = None if filing_ids is None else set(filing_ids)
    current = {f["cse_filing_id"]: keys.path_version(f["path"])
               for f in hb4_planning.candidate_filings(conn, gate.window)
               if wanted is None or f["cse_filing_id"] in wanted}
    items = [i for i in hb4_planning.document_items(conn)
             if i["cse_filing_id"] in current and i["path_sha256"] == current[i["cse_filing_id"]]]
    have = {i["cse_filing_id"] for i in items}
    final = states.FINAL["document"] | {"persisted", "validated", "reconciled", "needs_validation"}
    out = {"filings": len(current), "unplanned": len(set(current) - have),
           "claimable": sum(1 for i in items if i["state"] in CLAIMABLE),
           "in_flight": sum(1 for i in items if i["state"] in IN_FLIGHT),
           "final": sum(1 for i in items if i["state"] in final),
           "missing_from_w": sorted(wanted - set(current)) if wanted is not None else []}
    out["due"] = bool(out["unplanned"] or out["claimable"] or out["in_flight"])
    return out


@dataclass
class Runner:
    connect: Callable[[], Any]                   # a NEW cse_worker connection (each slice gets its own)
    runtime: Any                                 # HB-2's slice Runtime (HB-3's and HB-4's slices use the same)
    documents: Any = None                        # HB-4's Settings (None: HB-4's defaults: the server's tools and root)
    trigger: str = "manual"
    report_dir: Optional[str] = None
    validate_limit: int = VALIDATE_BATCH
    reconcile_limit: int = RECONCILE_BATCH
    install_sigterm: bool = True
    code_revision: Optional[str] = None
    preflight: Optional[Callable] = None         # conn -> [problems]; default: HB-6's composite (preflight.py)
    stage_preflight: Optional[Callable] = None   # passed to HB-3 / HB-4 / HB-5 (None: each runs its own)

    # -------------------------------------------------------------------------------------------- identity
    def _ident(self, arming):
        rt = self.runtime
        return {"host": rt.hostname(), "pid": os.getpid(), "boot_id": read_boot_id(), "os_user": p2runs.os_user(),
                "tool_version": TOOL_VERSION, "code_revision": self.revision(),
                "rule_versions": {"hb.ops": RULE_VERSION}, "arming_id": (arming or {}).get("id")}

    def revision(self):
        return self.code_revision or ops_settings.code_revision()

    # -------------------------------------------------------------------------------------------- the wake-up
    def wake(self):
        """(exit status, report). The report is what the CLI prints; its summary is the wake-up's details."""
        rt = self.runtime
        now = rt.wall()
        rep = {"tool_version": TOOL_VERSION, "trigger": self.trigger, "runner_time": now.isoformat()}
        ctl = self.connect()
        conns = [ctl]
        try:
            wid, holder = claim.acquire(ctl, trigger=self.trigger, runner_time=now,
                                        ident=self._ident(store.arming_in_force(ctl)))
            if wid is None:
                rep.update(result="busy", holder=jsonable(holder))
                return EXIT_OK, rep
            rep["wakeup_id"] = wid
            result, error = "error", None
            try:
                with self._sigterm():
                    work = self.connect()
                    conns.append(work)
                    code = self._run(ctl, work, wid, now, rep)
                result = rep.get("result", "done")
                return code, rep
            except SystemExit as exc:
                result, error = "stopped", f"stopped (SystemExit {exc.code}; SIGTERM is 143): the current unit unwound"
                rep.update(result="stopped", stopped=error)
                raise
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"[:2000]
                rep.update(result="error", error=error)
                raise
            finally:
                try:
                    store.finish_wakeup(ctl, wid, result, details=summary(rep), error=error)
                except Exception:  # noqa: BLE001 — the next runner expires it once this session has ended
                    pass
        finally:
            for c in conns:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    def _sigterm(self):
        if self.install_sigterm:
            return hb4_signals.sigterm_unwinds()
        from contextlib import nullcontext
        return nullcontext(False)

    def _beat(self, ctl, wid):
        try:
            store.heartbeat_wakeup(ctl, wid)
        except Exception:  # noqa: BLE001 — the claim is the live session, not the heartbeat
            pass

    def _run(self, ctl, work, wid, now, rep):
        rt = self.runtime
        rep["expired_wakeups"] = store.expire_dead_wakeups(ctl, wid)
        problems = (self.preflight or checks.problems)(work)
        if problems:
            rep.update(result="refused", refused=[["preflight", p] for p in problems])
            return EXIT_REFUSED
        blocks = blocks_of(work)
        if blocks:
            rep.update(result="blocked", blocks=blocks)
            return EXIT_BLOCKED
        arming = store.arming_in_force(work)
        stages = list(arming.get("armed_stages") or []) if arming and arming.get("armed") else []
        if stages:                                        # P3's rule: an armed wake-up trusts only a sane clock
            clock = hb2_gates.clock_refusals(now, rt.clock_synchronized(), self._last_runner_time(work, wid))
            if clock:
                rep.update(result="refused", refused=[list(r) for r in clock])
                return EXIT_REFUSED
        rep["arming"] = {"id": (arming or {}).get("id"), "armed": bool(arming and arming.get("armed")),
                         "armed_stages": stages}
        rep["db_bytes"] = database_bytes(work)
        self.attention, self.hard = [], []
        rep["bookkeeping"] = self._bookkeeping(work, wid, arming, stages)
        self._beat(ctl, wid)
        rep["f6"] = self._f6(work, wid, arming)
        self._beat(ctl, wid)
        rep["audit"] = self._audit(work, wid, arming, now)
        self._beat(ctl, wid)
        rep["slice"] = self._slice(work, arming, stages, rep["bookkeeping"])
        self._beat(ctl, wid)
        rep["attention"] = [list(a) for a in self.attention]
        blocks = blocks_of(work)                          # a slice may have recorded one
        if blocks:
            rep.update(result="blocked", blocks=blocks)
            return EXIT_BLOCKED
        if self.hard:
            rep.update(result="refused", refused=[list(r) for r in self.hard])
            return EXIT_REFUSED
        rep["result"] = "worked" if worked(rep) else "idle"
        return EXIT_ATTENTION if self.attention else EXIT_OK

    def _last_runner_time(self, conn, wid):
        """The latest runner time of any EARLIER wake-up (this one's own is now)."""
        return one(conn, "select max(runner_time) from backfill_wakeups where state <> 'skipped' and id < %s", (wid,))

    # -------------------------------------------------------------------------------------------- stage bookkeeping
    def _bookkeeping(self, work, wid, arming, stages):
        rt = self.runtime
        out = {}
        wall = rt.wall()
        if IDENTITY_STAGE in stages:
            first = store.item_by_key(work, keys.link_pass(LINK_PASS_IDENTITY)["natural_key"])
            if first is None or store.current_state(work, first["id"])["state"] != "succeeded":
                try:
                    res = hb3_identity.identity_pass(work, wall=wall, wakeup_id=wid, preflight=self.stage_preflight)
                    out["identity_pass"] = {"item": res.get("item"), "already": res.get("already", False)}
                except DiscoveryRefused as exc:
                    out["identity_pass"] = {"refused": [list(r) for r in exc.refusals]}
                    self._refusal(exc, "identity_pass")
        if DISCOVERY_STAGE in stages:
            try:
                made = hb3_discovery.create_plan_items(work, wall=wall, arming=arming, wakeup_id=wid,
                                                       preflight=self.stage_preflight)
                out["plan_items"] = {k: made[k] for k in ("feed_window", "listing", "existing")}
                plan = hb3_discovery.current_plan(work, wall, arming)
                status = hb3_discovery.closure(work, plan)
                out["discovery"] = {"closed": status.closed, "non_final": len(status.non_final),
                                    "missing": len(status.missing), "out_of_plan": len(status.out_of_plan),
                                    "plan_fingerprint": plan.fingerprint()}
                ie4 = hb3_identity.ie4_pass(work, plan.fingerprint())
                ie4_done = ie4 is not None and store.current_state(work, ie4["id"])["state"] == "succeeded"
                if status.closed and not ie4_done:
                    res = hb3_identity.closure_pass(work, wall=wall, wakeup_id=wid, preflight=self.stage_preflight)
                    out["closure_pass"] = {"item": res.get("item"), "already": res.get("already", False)}
            except DiscoveryRefused as exc:
                out["discovery_refused"] = [list(r) for r in exc.refusals]
                self._refusal(exc, "discovery")
        return out

    def _refusal(self, exc, where):
        """A stage refused: HB-P1 (no verified security master) or a preflight problem is a refusal the operator must
        resolve (exit 5); anything else needs attention (exit 2)."""
        codes = set(getattr(exc, "codes", []))
        entry = (where, str(exc)[:500])
        if isinstance(exc, SecurityMasterUnavailable) or "preflight" in codes:
            self.hard.append(entry)
        elif not codes <= WAITING:
            self.attention.append(entry)

    # -------------------------------------------------------------------------------------------- F6 (HB-5)
    def _f6(self, work, wid, arming):
        out = {}
        cr = self.revision()
        pre = self.stage_preflight
        v = hb5_validation.validate_pending(work, limit=self.validate_limit, code_revision=cr, wakeup_id=wid,
                                            preflight=pre)
        out["validation"] = {k: (v[k] if k in ("pending", "attempted", "refused") else len(v[k]))
                             for k in ("pending", "attempted", "succeeded", "failed", "refused", "awaiting_requeue",
                                       "recovered")}
        if v["failed"]:
            self.attention.append(("validation_failed", f"{len(v['failed'])} validation(s) failed: investigate, then "
                                                        f"`requeue --item validate:<run>`"))
        if v["awaiting_requeue"]:
            self.attention.append(("awaiting_requeue", f"{len(v['awaiting_requeue'])} validate item(s) are final but "
                                                       f"their runs are pending again (late evidence, M4): an "
                                                       f"operator re-queue validates them"))
        designated = hb5_configuration.designated(work)
        if designated is None:
            try:
                reg = hb5_configuration.register(work, preflight=pre)
                out["configuration"] = {k: reg[k] for k in ("state", "configuration_id")}
                self.attention.append(("designation", f"configuration {reg['configuration_id']} is registered and "
                                                      f"not designated: the owner designates it (F6.4's owner path)"))
            except F6Refused as exc:
                if exc.codes != ["no_runs"]:
                    out["configuration"] = {"refused": [list(r) for r in exc.refusals]}
                    self.attention.append(("configuration", str(exc)[:500]))
        out["promotion"] = hb5_promotion.promote_documents(work, wakeup_id=wid, preflight=pre)
        out["designated_configuration"] = designated
        if designated is not None:
            try:
                rec = hb5_reconcile.reconcile_ready(work, limit=self.reconcile_limit, code_revision=cr,
                                                    wakeup_id=wid, preflight=pre)
            except F6Refused as exc:
                out["reconcile"] = {"refused": [list(r) for r in exc.refusals]}
                if exc.codes != ["window"]:
                    self.attention.append(("reconcile", str(exc)[:500]))
                return out
            fp = rec.get("final_pass") or {}
            out["reconcile"] = {k: rec.get(k) for k in ("state", "reason", "pending")} | {
                "runs": [[r["natural_key"], r["state"]] for r in rec.get("runs", [])],
                "waiting": len(rec.get("waiting", [])), "final_pass": fp.get("state"),
                "final_pass_ran": "job_id" in fp}
            failed = [r for r in rec.get("runs", []) if r["state"] == "failed"]
            failed += [f for f in rec.get("finished", []) if f["state"] == "failed"]
            if failed:
                self.attention.append(("reconcile_failed", f"{len(failed)} reconcile pass(es) failed: investigate, "
                                                           f"then `requeue --item reconcile:<issuer>`"))
            if rec.get("runs") or "job_id" in fp:
                more = hb5_promotion.promote_documents(work, wakeup_id=wid, preflight=pre)
                out["promotion_after_reconcile"] = more
        return out

    # -------------------------------------------------------------------------------------------- the audit
    def _audit(self, work, wid, arming, now):
        if not arming or not arming.get("armed"):
            return {"due": False, "reason": "no window W in force (disarmed): `audit --window-first ...` reads one"}
        if audit_done_today(work, now):
            return {"due": False, "reason": "today's audit is done"}
        try:
            res = hb5_audit.run(work, code_revision=self.revision(), wakeup_id=wid, preflight=self.stage_preflight)
        except F6Refused as exc:
            self.attention.append(("audit", str(exc)[:500]))
            return {"due": True, "refused": [list(r) for r in exc.refusals]}
        out = {"due": True, "item": res["item"]["natural_key"], "snapshot_id": res["snapshot_id"],
               "digest": res["digest"], "snapshot_created": res["snapshot_created"],
               "funnel": {k: res["coverage"]["funnel"][k] for k in ("filings", "complete")}}
        out["export"] = self._export(res)
        return out

    def _export(self, res):
        if not self.report_dir:
            self.attention.append(("report_dir", "CSE_BACKFILL_REPORT_DIR is not configured: the audit's per-filing "
                                                 "table was not exported (its digest is recorded)"))
            return None
        try:
            return reports.export_coverage(res, self.report_dir)
        except Exception as exc:  # noqa: BLE001 — the snapshot is recorded; the export is retried with the next audit
            self.attention.append(("report_dir", f"coverage export failed: {type(exc).__name__}: {exc}"[:500]))
            return None

    # -------------------------------------------------------------------------------------------- the CSE slice
    def _slice(self, work, arming, stages, books):
        if not stages:
            return {"due": False, "reason": "disarmed: no Phase 2 CSE request"}
        if DISCOVERY_STAGE in stages:
            disc = books.get("discovery")
            if disc is None:                              # HB-P1 or another refusal already reported
                return {"due": False, "reason": "discovery could not be planned"}
            if not disc["closed"]:
                return self._run_slice("discovery", DISCOVERY_STAGE, None)
        last = {"due": False, "reason": "no CSE work is due"}
        for stage in (PILOT_STAGE, BULK_STAGE):
            if stage not in stages:
                continue
            try:
                gate = hb4_gate.document_gate(work, self.runtime.wall(), arming)
            except DocumentRefused as exc:
                self._refusal(exc, "document_gate")
                return {"due": False, "stage": stage, "gate": [list(r) for r in exc.refusals]}
            ids, max_items = None, None
            if stage == PILOT_STAGE:
                ids = pilot_filings(arming)
                if not ids:
                    self.hard.append(("pilot_sample", "HB-S3 is armed without a pilot sample (expected_requests."
                                                      "pilot_filings): the owner re-arms with `arm --pilot-sample`"))
                    return {"due": False, "stage": stage}
            else:
                block = (arming.get("expected_requests") or {}).get("documents")
                done = documents_under_arming(work, arming["id"])
                if block and done >= int(block):
                    self.attention.append(("checkpoint_due", f"{done} documents under arming {arming['id']} reached "
                                                             f"its block of {block}: record a backup checkpoint and "
                                                             f"arm the next block (section 21)"))
                    return {"due": False, "stage": stage, "documents": done, "block": block}
                if block:
                    max_items = int(block) - done          # never past the block within one slice (section 21)
            todo = document_work(work, gate, ids)
            if todo["due"]:
                return dict(self._run_slice("documents", stage, ids, max_items), work=todo)
            if stage == PILOT_STAGE:
                missing = todo["missing_from_w"]
                self.attention.append(("pilot_complete", f"every pilot filing's document item is final ({todo['final']}"
                                                         f" of {todo['filings']}"
                                                         + (f"; {len(missing)} pilot filing(s) are no longer in W"
                                                            if missing else "")
                                                         + "): produce `pilot-report` for the owner's review "
                                                           "(section 27)"))
            last = {"due": False, "stage": stage, "work": todo}
        return last

    def _run_slice(self, kind, stage, filing_ids, max_items=None):
        rt = self.runtime
        conn = self.connect()
        out = {"due": True, "kind": kind, "stage": stage, "max_items": max_items}
        try:
            if kind == "discovery":
                with hb3_discovery.DiscoverySlice(conn, runtime=rt, trigger=self.trigger,
                                                  preflight=self.stage_preflight) as ds:
                    outcomes = ds.run()
            else:
                with DocumentSlice(conn, stage=stage, runtime=rt, settings=self.documents, trigger=self.trigger,
                                   preflight=self.stage_preflight) as ds:
                    outcomes = ds.run(max_items=max_items, filing_ids=filing_ids)
            out.update(outcomes=count_states(outcomes), stop=str(ds.stop)[:500] if ds.stop else None,
                       lease_kept=ds.lease_kept, lease_id=getattr(ds.sl, "lease_id", None),
                       wakeup_id=getattr(ds.sl, "wakeup_id", None))
            if kind == "documents" and any(o.state == "cleanup_failed" for o in outcomes):
                self.attention.append(("cleanup_failed", "a temporary document could not be verified deleted: the "
                                                         "document stage is stopped until an operator re-queues it"))
        except SliceBusy as exc:
            out.update(result="busy", reason=str(exc))
        except (SliceRefused, DiscoveryRefused, DocumentRefused) as exc:
            out.update(result="refused", refused=[list(r) for r in exc.refusals])
            codes = set(exc.codes)
            if not codes <= WAITING:
                (self.hard if isinstance(exc, SecurityMasterUnavailable) or codes & HARD else self.attention).append(
                    (f"{kind}_slice", str(exc)[:500]))
        finally:
            try:
                conn.close()                               # each slice is its own session, never reused
            except Exception:  # noqa: BLE001
                pass
        return out


# Slice refusals the operator must resolve before any CSE work can run (exit 5).
HARD = {"preflight", "contact", "user_agent", "host", "versions", "stage_stopped", "clock", "tools", "temp_root",
        "temp_space", "lock"}


def blocks_of(conn):
    """Every unacknowledged block (Phase 2 recorded or unrecorded, and P2's): any one stops every stage."""
    out = {"phase2": hb2_ledger.phase2_blocks(conn), "unrecorded": hb2_ledger.unblocked_block_attempts(conn),
           "p2": hb2_ledger.p2_blocks(conn)}
    return jsonable(out) if any(out.values()) else None


def count_states(outcomes):
    out = {}
    for o in outcomes or ():
        out[o.state] = out.get(o.state, 0) + 1
    return dict(sorted(out.items()))


def worked(rep):
    """Did this wake-up do anything (a slice, a job, a promotion, an audit, a pass, a planned item)?"""
    sl, f6, books = rep.get("slice") or {}, rep.get("f6") or {}, rep.get("bookkeeping") or {}
    v, rec = f6.get("validation") or {}, f6.get("reconcile") or {}
    planned = books.get("plan_items") or {}
    passes = [books.get(k) or {} for k in ("identity_pass", "closure_pass")]
    return bool(sl.get("due") or v.get("attempted") or v.get("recovered") or f6.get("promotion")
                or f6.get("promotion_after_reconcile") or rec.get("runs") or rec.get("final_pass_ran")
                or (rep.get("audit") or {}).get("snapshot_id") or planned.get("feed_window") or planned.get("listing")
                or any(p.get("item") and not p.get("already") for p in passes))


def summary(rep):
    """The wake-up's details (L8): JSON-safe and small."""
    keep = ("trigger", "result", "arming", "db_bytes", "attention", "refused", "blocks", "expired_wakeups")
    out = {k: rep[k] for k in keep if k in rep}
    sl = rep.get("slice") or {}
    out["slice"] = {k: sl.get(k) for k in ("due", "kind", "stage", "result", "outcomes", "lease_id", "wakeup_id")
                    if k in sl}
    v = ((rep.get("f6") or {}).get("validation")) or {}
    out["validation"] = v
    a = rep.get("audit") or {}
    out["audit"] = {k: a.get(k) for k in ("due", "snapshot_id", "digest") if k in a}
    return jsonable(out)
