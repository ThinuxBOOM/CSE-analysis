"""
The document gate (design HB-U5 and HB-R3): "Only then may a filing enter the document pipeline." Read-only.

Discovery is closed when every item of the CURRENT plan is final (owner decision on HB-U5: current-plan closure) and
the issuer-evidence stage has run: the plan's IE-4 pass succeeded (owner decision: plan-versioned IE-4). Both come from
HB-3's own public functions, over HB-3's own plan constructor:

    plan.Plan.of(arming in force, security_master.require(...))   -- HB-P1 is its runtime gate
    discovery.closure(conn, plan)                                 -- closed: every plan item exists and is final
    identity.ie4_pass(conn, plan.fingerprint())                   -- the plan's IE-4 pass, succeeded

HB-3's discovery-stage checks (D-HB3-1: HB-S2 armed with one attempt per JSON request) are discovery's, not the
documents': the plan is built with HB-3's constructor directly, so a document stage needs no discovery stage armed.
"""
from dataclasses import dataclass

from ..backfill_discovery import discovery, identity, plan as hb3_plan, security_master
from ..backfill_discovery.errors import DiscoveryRefused
from ..financial_backfill import store
from .errors import DocumentRefused


@dataclass(frozen=True)
class Gate:
    plan: hb3_plan.Plan                # HB-3's Plan of the arming in force and the verified security master
    ie4_item: dict                     # the plan's IE-4 link pass (succeeded)

    @property
    def window(self):
        return self.plan.window

    def basis(self):
        return {"plan_fingerprint": self.plan.fingerprint(), "ie4_pass": self.ie4_item["id"],
                "window": [d.isoformat() for d in self.plan.window], "arming_id": self.plan.arming_id}


def document_gate(conn, wall, arming):
    """The open gate, or DocumentRefused (HB-P1 absent, discovery not closed, IE-4 not done)."""
    if not arming or not arming.get("armed"):
        raise DocumentRefused([("disarmed", "no arming decision in force")])
    try:
        p = hb3_plan.Plan.of(arming, security_master.require(conn, wall, arming))
    except DiscoveryRefused as exc:                       # includes SecurityMasterUnavailable (HB-P1)
        raise DocumentRefused(exc.refusals) from None
    status = discovery.closure(conn, p)
    if not status.closed:
        raise DocumentRefused([("discovery_open", f"the current discovery plan is not closed (HB-U5): "
                                                  f"{len(status.non_final)} of its items not final, "
                                                  f"{len(status.missing)} not planned yet")])
    ie4 = identity.ie4_pass(conn, p.fingerprint())
    if ie4 is None or (store.current_state(conn, ie4["id"]) or {}).get("state") != "succeeded":
        raise DocumentRefused([("issuer_evidence", "the current plan's IE-4 pass has not succeeded (HB-U5: the "
                                                   "issuer-evidence stage must have run)")])
    return Gate(p, ie4)
