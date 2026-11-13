"""
Modes and admission (design section 4).

Every piece of evidence has two instants: recorded_at (when the system durably held it) and available_at (when its
source made it public). A query has a mode, an information cutoff T and, in AVAILABLE and CURRENT, a knowledge
horizon H:

    KNOWN       recorded_at <= T                              point_in_time
    AVAILABLE   available_at <= T and recorded_at <= H        reconstructed
    CURRENT     recorded_at <= H                              retrospective_current   (never point-in-time)

KNOWN_RECORDED recomputes nothing (it returns a stored snapshot), so it is the store's, not this package's.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

KNOWN, AVAILABLE, CURRENT = "KNOWN", "AVAILABLE", "CURRENT"
LABELS = {KNOWN: "point_in_time", AVAILABLE: "reconstructed", CURRENT: "retrospective_current"}
POINT_IN_TIME_MODES = (KNOWN, AVAILABLE)


class Refused(ValueError):
    """A request this package will not serve. `code` is stable; the message is for the operator."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _instant(name, value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Refused("invalid_instant", f"{name} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class Query:
    mode: str
    cutoff: Optional[datetime]          # T; None only in CURRENT
    horizon: Optional[datetime] = None  # H; None in KNOWN

    def __post_init__(self):
        if self.mode not in LABELS:
            raise Refused("invalid_mode", f"mode must be one of {sorted(LABELS)}")
        if self.mode == KNOWN:
            _instant("cutoff", self.cutoff)
            if self.horizon is not None:
                raise Refused("invalid_query", "KNOWN has no knowledge horizon")
        elif self.mode == AVAILABLE:
            _instant("cutoff", self.cutoff)
            _instant("horizon", self.horizon)
            if self.horizon < self.cutoff:
                raise Refused("invalid_query", "the knowledge horizon H must not be before the cutoff T")
        else:
            _instant("horizon", self.horizon)
            if self.cutoff is not None:
                raise Refused("invalid_query", "CURRENT has no information cutoff")

    @property
    def label(self):
        return LABELS[self.mode]

    def admits(self, recorded_at, available_at):
        """Is a piece of evidence with these two instants inside the query's information set?"""
        _instant("recorded_at", recorded_at)
        if self.mode == KNOWN:
            return recorded_at <= self.cutoff
        if self.mode == CURRENT:
            return recorded_at <= self.horizon
        _instant("available_at", available_at)
        return available_at <= self.cutoff and recorded_at <= self.horizon


def require_point_in_time(result):
    """The result, if a point-in-time consumer may use it; a CURRENT result is refused (design section 4.2)."""
    mode, label = getattr(result, "mode", None), getattr(result, "label", None)
    if mode not in POINT_IN_TIME_MODES or label != LABELS.get(mode):
        raise Refused("current_not_point_in_time", "a retrospective_current result is never a point-in-time input")
    return result
