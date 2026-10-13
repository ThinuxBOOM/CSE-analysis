"""
The owner decisions (design sections 7.7, 16.3, 16.6 and 21), recorded only through HB-1's owner path (cse_migrator,
which only root reaches, acting as cse_owner for one INSERT). Recording a decision is not making one: every value comes
from the owner; nothing here chooses a window, a budget, a sample or a resolution, and an arming decision authorises
nothing by itself (G-1 is an accepted risk, not CSE authorization).

    arm                validated BEFORE the owner path opens, with the frozen layers' own rules:
                       - the stages (HB-1) and the whole-month Colombo window W (HB-3's window rule);
                       - the exact User-Agent P2 builds from a valid contact e-mail (owner decision A10, P3's check),
                         this host, and the running frozen version tuple (owner decision A3, recorded from this
                         process);
                       - the budgets and bounds (HB-Q8's settled values unless the owner gives others); HB-S2 needs
                         exactly one attempt per JSON request and an item maximum of at most 3 (D-HB3-1, HB-3's rule);
                       - HB-S3: the pilot sample the owner reviewed (expected_requests.pilot_filings);
                       - HB-S4: a recorded backup checkpoint (section 21: a manual dump, a restore check and both
                         verifies, citing P1's backup-ledger run ids) and a document block of at most 2,000;
                       - the G-1 stop conditions acknowledged, a G-1 reference and a release note.
    disarm             stops every Phase 2 request at the next request boundary (G-1 control 10)
    acknowledge_block  the owner's acknowledgement of a block (L9); blocked items are then resumed by the operator
    resolve_hold       an owner hold resolution (L7): applied by the next late-evidence pass
"""
import platform
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..backfill_discovery import STAGE as DISCOVERY_STAGE, discovery as hb3_discovery, plan as hb3_plan
from ..backfill_transport import gates as hb2_gates
from ..financial_backfill import owner
from ..market_capture import config as p2config, runs as p2runs
from . import BULK_STAGE, CHECKPOINT_DOCUMENTS, NOTE_MIN, PILOT_MAX, PILOT_STAGE
from .errors import OpsRefused

UA_EMAIL = re.compile(r"contact: ([^)\s]+)\)$")          # P3's own check (worker/scheduler/cli.py)

# HB-Q8's settled bounds (design section 16.4, the owner's proposed defaults; D-HB3-1 for JSON attempts).
DEFAULT_BOUNDS = {"daily_request_budget": 600, "combined_daily_ceiling": 800, "slice_max_json_requests": 30,
                  "slice_max_documents": 10, "slice_max_seconds": 600, "attempts_per_json_request": 1,
                  "attempts_per_document": 2, "item_max_attempts": 3}

# Section 16.3's stop rules, recorded with every arming decision (P3 records its own the same way).
STOP_CONDITIONS = (
    "CSE blocks access (API 401/403/407/451, CDN 401/407/451) or keeps rate-limiting (a 429 beyond 300 s or beyond its "
    "attempts): every Phase 2 stage stops until the owner records acknowledge-block; P3 is disarmed until reviewed "
    "(G-1 controls 5-6; design 16.3)",
    "5 consecutive non-OK attempts open the circuit and stop the slice; three consecutive stopped slices stop the stage "
    "until a newer owner arming decision (owner decision A5)",
    "the daily request budget, the combined ceiling or a slice bound: no further request that day or slice; work is "
    "deferred, never burst (design 16.4)",
    "a cleanup failure or a leftover temporary document: the document stage stops until an operator re-queues the item "
    "(design 17)",
    "CSE objects, requests cessation or deletion, or its published terms change materially: the owner disarms and "
    "reviews G-1 (G-1 section 5, control 10)",
    "the project becomes commercial or public, or raw data would be redistributed: stop and review G-1 first "
    "(control 12)",
)


@dataclass(frozen=True)
class Checkpoint:
    """Section 21's owner checkpoint before (a block of) HB-S4: P1's backup-ledger run ids of the manual dump and of
    its restore check, and the owner's statement that F6.4's verify and the Phase 2 verify were clean."""
    dump_run: str
    restore_check_run: str
    verified: bool
    note: str

    def entry(self):
        return {"backup_checkpoint": {"dump_backup_run": self.dump_run, "restore_check_backup_run":
                                      self.restore_check_run, "f64_and_phase2_verify_clean": self.verified,
                                      "note": self.note}}


def who(operator=None):
    return f"{p2runs.os_user()} (operator: {operator})" if operator else p2runs.os_user()


def user_agent_refusals(user_agent):
    m = UA_EMAIL.search(user_agent or "")
    try:
        expected = p2config.user_agent(m.group(1)) if m else None
    except p2config.ConfigError:
        expected = None
    if expected is None or expected != user_agent:
        return [("user_agent", "the User-Agent must be exactly the one P2 sends: 'cse-analysis-capture/<P2 version> "
                               "(personal non-commercial research; contact: <the contact e-mail configured in "
                               "/etc/cse/capture.env>)'")]
    return []


def arming_decision(*, stages, window_first: date, window_last: date, user_agent, host, g1_reference, note,
                    confirm_stop_conditions, expected_requests, pilot_filings=None, checkpoint: Optional[Checkpoint] = None,
                    security_master_max_age_days=None, bounds=None, this_host=None, versions=None):
    """The validated ArmingDecision, or OpsRefused naming every problem."""
    stages = tuple(dict.fromkeys(stages or ()))
    b = dict(DEFAULT_BOUNDS, **{k: v for k, v in (bounds or {}).items() if v is not None})
    refusals = []
    unknown = sorted(set(stages) - set(owner.STAGES))
    if not stages or unknown:
        refusals.append(("stages", f"name the armed stages from {owner.STAGES}" + (f"; unknown {unknown}" if unknown
                                                                                     else "")))
    refusals += hb3_plan.window_refusals(window_first, window_last)
    refusals += user_agent_refusals(user_agent)
    here = this_host if this_host is not None else platform.node()
    if host != here:
        refusals.append(("host", f"arm on the production host itself: --host {host!r} but this host is {here!r}"))
    if not confirm_stop_conditions:
        refusals.append(("stop_conditions", "--confirm-stop-conditions is required (section 16.3's stop rules are "
                                            "recorded with the decision; see the runbook)"))
    exp = {k: v for k, v in (expected_requests or {}).items() if v is not None}
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in exp.values()):
        refusals.append(("expected_requests", "expected request counts are non-negative integers"))
    elif not exp and not (PILOT_STAGE in stages and pilot_filings):
        refusals.append(("expected_requests", "state the expected requests (--expected-feed / --expected-listings / "
                                              "--expected-documents): the release gate records them"))
    if PILOT_STAGE in stages:
        if not pilot_filings:
            refusals.append(("pilot_sample", "HB-S3 needs the pilot sample the owner reviewed (--pilot-sample FILE)"))
        elif len(pilot_filings) > PILOT_MAX:
            refusals.append(("pilot_sample", f"a pilot names at most {PILOT_MAX} filings, not {len(pilot_filings)}"))
        else:
            exp["pilot_filings"] = sorted(int(i) for i in pilot_filings)
            exp.setdefault("documents", len(exp["pilot_filings"]))
    elif pilot_filings:
        refusals.append(("pilot_sample", "a pilot sample is recorded only with HB-S3"))
    stop: list = list(STOP_CONDITIONS)
    if BULK_STAGE in stages:
        if checkpoint is None or not (checkpoint.dump_run or "").strip() or not (checkpoint.restore_check_run or
                                                                                  "").strip():
            refusals.append(("checkpoint", "HB-S4 is armed only after a recorded backup checkpoint (section 21): "
                                           "--checkpoint-dump-run and --checkpoint-restore-check-run"))
        elif not checkpoint.verified:
            refusals.append(("checkpoint", "--checkpoint-verified: F6.4's verify and the Phase 2 verify must both have "
                                           "been clean on the restored copy"))
        else:
            stop.append(checkpoint.entry())
        block = exp.get("documents")
        if not isinstance(block, int) or not 1 <= block <= CHECKPOINT_DOCUMENTS:
            refusals.append(("checkpoint", f"HB-S4 is armed in blocks of at most {CHECKPOINT_DOCUMENTS} documents "
                                           f"(--expected-documents), each after its checkpoint"))
    elif checkpoint is not None:
        refusals.append(("checkpoint", "a backup checkpoint is recorded with HB-S4"))
    if security_master_max_age_days is not None:
        stop.append({"security_master_max_age_days": int(security_master_max_age_days)})
    decision = owner.ArmingDecision(
        armed=True, note=(note or "").strip(), armed_stages=stages, window_first_date=window_first,
        window_last_date=window_last, user_agent=user_agent, host=host,
        version_tuple=versions if versions is not None else hb2_gates.running_version_tuple(),
        expected_requests=exp, stop_conditions=tuple(stop), g1_reference=(g1_reference or "").strip() or None, **b)
    if DISCOVERY_STAGE in stages:
        refusals += [r for r in hb3_discovery.config_refusals(dict(decision.__dict__, armed=True))
                     if r[0] != "window"]
    if len((note or "").strip()) < NOTE_MIN:
        refusals.append(("note", f"the release note needs at least {NOTE_MIN} characters"))
    if refusals:
        raise OpsRefused(refusals)
    try:
        return decision.validate()
    except owner.InvalidDecision as exc:
        raise OpsRefused([("decision", str(exc))]) from None


def record_arming(conn, decision, operator=None):
    try:
        return owner.record_arming_as_owner(conn, decision, who(operator))
    except owner.OwnerPathRequired as exc:
        raise OpsRefused([("owner_path", str(exc))]) from None


def disarm(conn, note, operator=None):
    if len((note or "").strip()) < NOTE_MIN:
        raise OpsRefused([("note", f"a disarm note needs at least {NOTE_MIN} characters")])
    return record_arming(conn, owner.ArmingDecision.disarm(note.strip()), operator)


def acknowledge_block(conn, block_id, note, operator=None):
    try:
        return owner.acknowledge_block_as_owner(conn, block_id, note, operator)
    except owner.OwnerPathRequired as exc:
        raise OpsRefused([("owner_path", str(exc))]) from None
    except owner.InvalidDecision as exc:
        raise OpsRefused([("decision", str(exc))]) from None


def resolve_hold(conn, hold_id, resolution, note, operator=None):
    if resolution not in owner.HOLD_RESOLUTIONS:
        raise OpsRefused([("resolution", f"a hold resolution is one of {owner.HOLD_RESOLUTIONS}")])
    try:
        return owner.resolve_hold_as_owner(conn, hold_id, resolution, note, operator)
    except owner.OwnerPathRequired as exc:
        raise OpsRefused([("owner_path", str(exc))]) from None
    except owner.InvalidDecision as exc:
        raise OpsRefused([("decision", str(exc))]) from None
