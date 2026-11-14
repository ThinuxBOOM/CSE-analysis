"""
Folds and slices (Phase 5 §5.2, §5.3; step M5-3). Every split is on two instants per row and horizon: T, the row's
information cutoff, and R_h, its label's resolution instant (`resolved_at`), both on the admission clock of the row's
era. Session arithmetic ("g sessions before V_end", "an embargo of k sessions") uses the protocol's calendar.

Outer fold (C_train < V_start <= V_end; g = h + grace; V_start at least `embargo` sessions after C_train):
    training         R_h <= C_train
    validation       V_start <= T <= V_end - g  and  R_h <= V_end
    resolved_late    V_start <= T <= V_end - g  and  R_h > V_end          scored only, never fitted (revision 5)
    unused           everything else, counted with its reason

Inner slices of a fold's training rows (S1 < S2 < S3 < C_train), each boundary with the same purge and embargo:
    fit        R_h <= S1
    select     S1 + embargo <= T <= S2 - g  and  R_h <= S2
    calibrate  S2 + embargo <= T <= S3 - g  and  R_h <= S3
    conformal  S3 + embargo <= T <= C - g   and  R_h <= C_train
"""
import bisect
from dataclasses import dataclass, field
from typing import Optional

TRAINING, VALIDATION, RESOLVED_LATE, UNUSED = "training", "validation", "resolved_late", "unused"
FIT, SELECT, CALIBRATE, CONFORMAL = "fit", "select", "calibrate", "conformal"
SLICES = (FIT, SELECT, CALIBRATE, CONFORMAL)


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Row:
    key: tuple
    t: object                     # T (any totally ordered instant: datetime, session index)
    r: Optional[object]           # R_h; None while the label is pending


class Calendar:
    """The protocol's session instants, ascending. `shift(x, k)` is the instant of the session k positions after the
    last session at or before x (k may be negative)."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        if not self.sessions or any(a >= b for a, b in zip(self.sessions, self.sessions[1:])):
            raise Refused("bad_calendar", "sessions are strictly increasing and not empty")

    def shift(self, x, k):
        i = bisect.bisect_right(self.sessions, x) - 1
        j = i + k
        if i < 0 or not 0 <= j < len(self.sessions):
            raise Refused("outside_calendar", "the shifted session lies outside the calendar")
        return self.sessions[j]


@dataclass(frozen=True)
class Fold:
    c_train: object
    v_start: object
    v_end: object
    horizon: int
    grace: int = 21
    embargo: int = 1

    @property
    def g(self):
        return self.horizon + self.grace

    def check(self, cal: Calendar):
        if self.horizon < 1 or self.grace < 0 or self.embargo < 0:
            raise Refused("bad_parameters", "horizon >= 1, grace >= 0, embargo >= 0")
        if not (self.c_train < self.v_start <= self.v_end):
            raise Refused("bad_fold", "C_train < V_start <= V_end")
        if self.v_start < cal.shift(self.c_train, self.embargo):
            raise Refused("embargo", "V_start is at least the embargo after C_train")
        if cal.shift(self.v_end, -self.g) < self.v_start:
            raise Refused("empty_validation", "the validation window is shorter than g")

    def assign(self, rows, cal: Calendar):
        """{role: [rows]} with every row in exactly one role, and {reason: count} for the unused."""
        self.check(cal)
        last_t = cal.shift(self.v_end, -self.g)
        out = {TRAINING: [], VALIDATION: [], RESOLVED_LATE: [], UNUSED: []}
        reasons = {}
        for row in rows:
            if row.r is not None and not row.r > row.t:
                raise Refused("bad_row", "a label resolves after its row's cutoff: R_h > T (§5.1)")
            if row.r is not None and row.r <= self.c_train:
                out[TRAINING].append(row)
            elif self.v_start <= row.t <= last_t and row.r is not None:
                out[VALIDATION if row.r <= self.v_end else RESOLVED_LATE].append(row)
            else:
                reason = ("pending" if row.r is None else "before_validation" if row.t < self.v_start
                          else "purged_tail" if row.t <= self.v_end else "after_fold")
                out[UNUSED].append(row)
                reasons[reason] = reasons.get(reason, 0) + 1
        return out, reasons


@dataclass(frozen=True)
class Slices:
    s1: object
    s2: object
    s3: object
    c_train: object
    horizon: int
    grace: int = 21
    embargo: int = 1

    def check(self, cal: Calendar):
        if self.horizon < 1 or self.grace < 0 or self.embargo < 0:
            raise Refused("bad_parameters", "horizon >= 1, grace >= 0, embargo >= 0")
        if not (self.s1 < self.s2 < self.s3 < self.c_train):
            raise Refused("bad_slices", "S1 < S2 < S3 < C_train")
        g = self.horizon + self.grace
        for lo, hi in ((self.s1, self.s2), (self.s2, self.s3), (self.s3, self.c_train)):
            if cal.shift(hi, -g) < cal.shift(lo, self.embargo):
                raise Refused("empty_slice", "a slice is shorter than its purge and embargo")

    def assign(self, training_rows, cal: Calendar):
        """{slice: [rows]} over a fold's training rows; rows straddling a boundary are unused and counted."""
        self.check(cal)
        g = self.horizon + self.grace
        bounds = ((SELECT, self.s1, self.s2), (CALIBRATE, self.s2, self.s3), (CONFORMAL, self.s3, self.c_train))
        out = {s: [] for s in SLICES}
        out[UNUSED] = []
        for row in training_rows:
            if row.r is None or row.r > self.c_train:
                raise Refused("not_training", "only a fold's training rows are sliced")
            if row.r <= self.s1:
                out[FIT].append(row)
                continue
            for name, lo, hi in bounds:
                if cal.shift(lo, self.embargo) <= row.t <= cal.shift(hi, -g) and row.r <= hi:
                    out[name].append(row)
                    break
            else:
                out[UNUSED].append(row)
        return out


def walk_forward(first_c_train, v_ends, horizon, cal: Calendar, grace=21, embargo=1):
    """Expanding folds: each fold's C_train is the previous fold's V_end; V_start is the embargo after it."""
    folds, c = [], first_c_train
    for v_end in v_ends:
        fold = Fold(c, cal.shift(c, embargo), v_end, horizon, grace, embargo)
        fold.check(cal)
        folds.append(fold)
        c = v_end
    return folds


@dataclass
class FitScope:
    """MLK-3: anything fitted (a scaler, imputer, calibration map, early-stopping loop, conformal quantile) is bound
    to one slice and refuses rows outside it."""
    slice_name: str
    allowed: frozenset = field(default_factory=frozenset)

    @classmethod
    def of(cls, slices_out, name):
        return cls(name, frozenset(r.key for r in slices_out[name]))

    def admit(self, rows):
        stray = [r.key for r in rows if r.key not in self.allowed]
        if stray:
            raise Refused("fit_scope", f"{len(stray)} row(s) outside the {self.slice_name} slice")
        return list(rows)
