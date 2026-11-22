"""
§10: designation lists. A list is an ordered set of combinations for one (output, horizon); a row is predicted by
the FIRST whose components are all available, and a list is validated as it will be used: metrics are computed per
position on the rows routed to it (MLK-21), never on every row a position could score.

The designation in force at a prediction's cutoff is the latest recorded at or before it (MLK-19): one recorded later
changes nothing for that prediction.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    combination_id: str
    components: tuple            # configuration ids, all needed


def in_force(designations, at):
    """designations: [(designation_id, recorded_at, list_of_positions)] for one (purpose, output, horizon). The
    latest recorded at or before `at` (by recorded_at, then id), or None."""
    before = [d for d in designations if d[1] <= at]
    return max(before, key=lambda d: (d[1], d[0])) if before else None


def route(positions, available):
    """The index of the first position whose components are all in `available` (a set of configuration ids that
    have a prediction for the row), or None: `insufficient_data` (no combination has all its components)."""
    for i, p in enumerate(positions):
        if set(p.components) <= set(available):
            return i
    return None


def per_position(rows, positions, metric):
    """rows: [(available component ids, actual, predicted by position {index: value})]. Each row is scored by the
    position it is routed to, and only that one. Returns {index: (metric value or None, n)} for every position
    (positions no row reached report (None, 0)), plus {"unrouted": n}."""
    groups = {i: ([], []) for i in range(len(positions))}
    unrouted = 0
    for available, actual, predicted in rows:
        i = route(positions, available)
        if i is None:
            unrouted += 1
            continue
        groups[i][0].append(actual)
        groups[i][1].append(predicted[i])
    out = {i: ((metric(a, p) if a else None), len(a)) for i, (a, p) in groups.items()}
    out["unrouted"] = unrouted
    return out
