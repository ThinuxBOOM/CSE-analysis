"""
F8's point-in-time interfaces (docs/F8_DESIGN.md §8.1, §8.2, §14; F-5).

Two rules bind every consumer:
- **No point-in-time use ever takes a CURRENT result.** Every interface here accepts only KNOWN_RECORDED, KNOWN or
  AVAILABLE, and re-proves each result against its own hash first. A CURRENT envelope relabelled as historical fails
  that re-proof (T-40).
- **Live use is KNOWN or KNOWN_RECORDED only** (F6.2 §9.2). That is the check require_live performs.

A dataset built from mixed modes is refused (§8.1). Choosing cutoffs, lags and eras stays with the consumer (§14).
"""
from dataclasses import dataclass

from ..financial_truth import canonical
from .errors import Refused
from .query import AVAILABLE, CURRENT, LABELS, LIVE_MODES, POINT_IN_TIME_MODES
from .selection import evaluate


def require_point_in_time(result):
    """The result, if it re-proves its hash and is of a point-in-time mode; refused otherwise."""
    if not result.verify():
        raise Refused("result_hash_mismatch", "the result does not re-prove its result_hash")
    if result.mode not in POINT_IN_TIME_MODES or result.label != LABELS.get(result.mode):
        raise Refused("current_not_point_in_time", "a CURRENT (retrospective_current) result is never a "
                                                   "point-in-time input (§8.2, F-5)")
    return result


def require_live(result):
    """The result, if a live analysis or prediction may use it (KNOWN or KNOWN_RECORDED only, F6.2 §9.2)."""
    require_point_in_time(result)
    if result.mode not in LIVE_MODES:
        raise Refused("not_live_mode", f"{result.mode} ({result.label}) is never an input to a live analysis or "
                                       f"prediction")
    return result


@dataclass(frozen=True)
class PointInTimeDataset:
    mode: str
    label: str
    result_hashes: tuple             # in the order given
    results: tuple
    dataset_hash: str                # SHA-256 of the canonical (mode, label, result_hashes)


def dataset(results):
    """A point-in-time dataset: one mode only, never CURRENT, every result re-proved."""
    results = tuple(results)
    if not results:
        raise Refused("empty_dataset", "a dataset needs at least one result")
    for r in results:
        require_point_in_time(r)
    modes = sorted({r.mode for r in results})
    if len(modes) != 1:
        raise Refused("mixed_modes", f"a dataset built from mixed modes {modes} is refused (§8.1)")
    hashes = tuple(r.result_hash for r in results)
    mode = modes[0]
    return PointInTimeDataset(mode, LABELS[mode], hashes, results,
                              canonical.digest({"mode": mode, "label": LABELS[mode], "results": list(hashes)}))


def timeline(evidence, base_query, cutoffs):
    """One fact set of one issuer at many information cutoffs (a backtest timeline), from one evidence snapshot.
    CURRENT is refused. AVAILABLE keeps the base query's H for every cutoff (H >= every T)."""
    if base_query.mode == CURRENT:
        raise Refused("current_not_point_in_time", "a timeline is a point-in-time interface: CURRENT is refused")
    out = []
    for cutoff in cutoffs:
        q = base_query.with_cutoff(cutoff)
        if q.mode == AVAILABLE and q.knowledge_horizon is None:
            raise Refused("horizon_required", "an AVAILABLE timeline needs its knowledge horizon H")
        out.append(require_point_in_time(evaluate(evidence, q)))
    return tuple(out)
