"""
F1 filing metadata as the system knew it at a horizon (docs/F8_DESIGN.md §4.3, AC-5).

The mutable report_filings row is never read for history (I-6). The metadata is rebuilt from the append-only
report_filing_observations with F1's own pure merge (worker/report_discovery.normalize_filing), never re-implemented:
1. take the observations observed at or before H;
2. per source (endpoint, bucket, query symbol), take the latest version by observed_at;
3. apply F1's merge to those versions.

**Ties are never broken by guessing.** Two versions of one source can share an observed_at (the same id twice in one
response). No append-only row records which of them F1 applied last. F8 needs the metadata only for the uploaded_at
that F6.4's M4 compares (§7.3). So every combination of tied versions is merged:
- if the combinations give one uploaded_at, the tie does not matter;
- if they give several, the reconstruction depends on the tie (metadata_version_tie), and the dependent observations
  are hidden (selection.py).

Availability does not use this reconstruction. A-2 reads every value of every observation (availability.py).

Known limit, inherited from F1's frozen evidence (docs/F8_IMPLEMENTATION.md §4): F1 stores a listing version once per
(filing, endpoint, bucket, metadata hash). So a source that changes back to an earlier version (A -> B -> A), or a
second query symbol that lists a version already stored under another symbol, leaves no new row. The reconstruction
then shows the last stored version of that source. It reads only rows known by H, so it can never leak. At worst it
names a different, already known validation run, or none (the document is then hidden: there is no fallback).
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import product

from .. import report_discovery as rd
from .errors import EvidenceError
from .times import instant

MAX_TIE_COMBINATIONS = 256              # beyond this, a tie is treated as dependent without enumerating (conservative)


@dataclass(frozen=True)
class MetadataAsOf:
    cse_filing_id: int
    horizon: datetime
    uploaded_at: tuple                  # every uploaded_at F1's merge can give at H: one value unless a tie changes it
    tie: bool                           # True: the uploaded_at depends on an unbroken tie (metadata_version_tie)
    observation_ids: tuple              # the current versions used at H, tied candidates included


def _normal(value):
    return None if value is None else instant(value, "report_filings.uploaded_at (as merged)")


def current_versions(observations, horizon):
    """{source key: the latest observation(s) at H}, ties kept: every observation sharing the latest observed_at."""
    by_source = defaultdict(list)
    for o in observations:
        if o.observed_at <= horizon:
            by_source[rd.source_key(o.source_endpoint, o.source_bucket, o.query_symbol)].append(o)
    out = {}
    for key, rows in by_source.items():
        latest = max(o.observed_at for o in rows)
        out[key] = tuple(sorted((o for o in rows if o.observed_at == latest), key=lambda o: (o.metadata_hash, o.id)))
    return out


def as_of(evidence, cse_filing_id, horizon):
    current = current_versions(evidence.filing_observations_of(cse_filing_id), horizon)
    keys = sorted(current)
    ids = tuple(sorted(o.id for k in keys for o in current[k]))
    combinations = 1
    for k in keys:
        combinations *= len(current[k])
    if combinations > MAX_TIE_COMBINATIONS:
        return MetadataAsOf(cse_filing_id, horizon, (), True, ids)
    values = set()
    for combo in product(*(current[k] for k in keys)):
        try:
            row, _ = rd.normalize_filing({k: o.raw_item for k, o in zip(keys, combo)}, {})
        except rd.ItemRejected as exc:
            raise EvidenceError(f"filing {cse_filing_id}: a stored F1 observation is not a listing entry F1 "
                                f"accepts ({exc})") from None
        values.add(_normal(row["uploaded_at"]))
    ordered = tuple(sorted(values, key=lambda v: (v is not None, v or datetime.min)))
    return MetadataAsOf(cse_filing_id, horizon, ordered, len(ordered) > 1, ids)
