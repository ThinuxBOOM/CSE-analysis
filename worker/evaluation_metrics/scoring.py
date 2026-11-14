"""
`scoring.1` (Phase 5 §6, revision 5): which label states are trained on, which enter headline metrics, what is
counted and what gets a sensitivity. Phase 3 defines the states; this policy is versioned and shared by Phases 5, 9
and 10.
"""
from dataclasses import dataclass, field
from typing import Optional

POLICY = "scoring.1"
RESOLVED, RESOLVED_CARRIED, TERMINATED = "resolved", "resolved_carried", "terminated"
UNEXPLAINED, QUALITY = "unexplained_move_in_horizon", "unresolvable_quality"
MISSING_PRICE, UNKNOWN_SESSION, PENDING = "unresolvable_missing_price", "unknown_session_in_horizon", "pending"
STATES = (RESOLVED, RESOLVED_CARRIED, TERMINATED, UNEXPLAINED, QUALITY, MISSING_PRICE, UNKNOWN_SESSION, PENDING)
TRAINED = (RESOLVED, RESOLVED_CARRIED)


@dataclass(frozen=True)
class LabelRow:
    key: tuple                          # (security, reference session, horizon)
    state: str
    ret: Optional[float] = None
    direction: Optional[str] = None
    candidate_event: bool = False       # unexplained move near a known, unentered bonus or rights issue
    era_row_absent: bool = False        # from an era imported row_absent without security_complete
    resolved_late: bool = False         # resolved after the scored window (§5.2, §5.4)

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"unknown label state {self.state!r}")
        if self.state in (RESOLVED, RESOLVED_CARRIED, UNEXPLAINED) and (self.ret is None or self.direction is None):
            raise ValueError(f"a {self.state} label carries its return and direction")


@dataclass
class Population:
    training: list = field(default_factory=list)
    headline: list = field(default_factory=list)
    secondary_resolved_only: list = field(default_factory=list)
    resolved_late: list = field(default_factory=list)
    pessimistic_terminated: list = field(default_factory=list)   # headline + terminated as a total loss
    unexplained_raw: list = field(default_factory=list)          # headline + genuine unexplained moves at raw return
    counts: dict = field(default_factory=dict)


def _as_loss(row):
    """A terminated label in the pessimistic sensitivity: return -1, class fall."""
    return LabelRow(row.key, row.state, -1.0, "fall")


def population(rows, *, for_training_only=False):
    """Partition label rows by scoring.1. Counts are by state, plus the separately counted groups. A training call
    (for_training_only) must not receive late-resolving rows: those are scoring only."""
    p = Population(counts={s: 0 for s in STATES})
    for extra in ("unexplained_candidate_event", "unexplained_genuine", "era_absence", "resolved_late"):
        p.counts[extra] = 0
    for r in rows:
        p.counts[r.state] += 1
        if r.resolved_late:
            if for_training_only:
                raise ValueError("a label that resolved after the window is never a training row")
            p.counts["resolved_late"] += 1
        if r.state in TRAINED:
            if not r.resolved_late:
                p.training.append(r)
            p.headline.append(r)
            if r.state == RESOLVED:
                p.secondary_resolved_only.append(r)
            if r.resolved_late:
                p.resolved_late.append(r)
        elif r.state == TERMINATED:
            if r.era_row_absent:
                p.counts["era_absence"] += 1          # not evidence of leaving: out of the sensitivity
            else:
                p.pessimistic_terminated.append(_as_loss(r))
        elif r.state == UNEXPLAINED:
            if r.candidate_event:
                p.counts["unexplained_candidate_event"] += 1     # dilution, not a return: no sensitivity
            else:
                p.counts["unexplained_genuine"] += 1
                p.unexplained_raw.append(r)
        elif r.state == MISSING_PRICE and r.era_row_absent:
            p.counts["era_absence"] += 1
    p.pessimistic_terminated = p.headline + p.pessimistic_terminated
    p.unexplained_raw = p.headline + p.unexplained_raw
    return p
