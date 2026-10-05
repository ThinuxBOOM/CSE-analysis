"""
The immutable evidence F8 reads (docs/F8_DESIGN.md §3.1): one frozen record per append-only row, and Evidence, the rows
one query may draw on. loader.py fills it from PostgreSQL; tests build it directly. Nothing here interprets a time.
The rules do that: availability.py, knowledge.py, metadata.py, supersession.py and selection.py.

Deliberately not representable (§3.2):
- report_filings, report_discovery_runs and companies, which are mutable;
- operational times (job schedules, compute durations).

Every time is normalised to a UTC instant on construction, and a naive value is refused.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..financial_truth.inputs import F5RunRef
from ..financial_truth.observations import SourceObservation
from ..financial_truth.reconciliation import ReconciliationConfiguration, ReconciliationResult
from ..financial_truth.versions import VersionSet
from .errors import EvidenceError
from .times import instant, optional_instant


def _set(obj, name, value):
    object.__setattr__(obj, name, value)


@dataclass(frozen=True)
class FilingObservation:
    """report_filing_observations (0004; append-only by 0010): one version of one CSE listing entry."""
    id: str
    cse_filing_id: int
    discovery_run_id: Optional[str]
    source_endpoint: str
    source_bucket: str
    query_symbol: Optional[str]
    metadata_hash: str
    raw_item: Any                        # the listing entry exactly as received (a JSON object)
    observed_at: datetime                # F1's receipt time (clock C, set by the F1 run; AC-4)

    def __post_init__(self):
        _set(self, "id", str(self.id))
        _set(self, "observed_at", instant(self.observed_at, "report_filing_observations.observed_at"))


@dataclass(frozen=True)
class Classification:
    """report_document_classifications (0005; append-only by 0010): the F3 row an F5 run used."""
    id: str
    cse_filing_id: int
    document_sha256: str
    classified_at: datetime
    document_type: Optional[str] = None
    document_type_status: Optional[str] = None
    underlying_type: Optional[str] = None
    underlying_type_status: Optional[str] = None

    def __post_init__(self):
        _set(self, "id", str(self.id))
        _set(self, "classified_at", instant(self.classified_at, "classified_at"))


@dataclass(frozen=True)
class IssuerDecision:
    """filing_issuer_links (0007; append-only): an F5 issuer decision for a filing. The current one at a time is the
    highest id decided by then."""
    id: int
    cse_filing_id: int
    status: str
    basis: str
    issuer_id: Optional[str]
    decided_at: datetime

    def __post_init__(self):
        _set(self, "issuer_id", None if self.issuer_id is None else str(self.issuer_id))
        _set(self, "decided_at", instant(self.decided_at, "decided_at"))


@dataclass(frozen=True)
class ExtractionRun:
    """financial_extraction_runs (0008; append-only): one F5 processing run of one document version."""
    ref: F5RunRef                                   # the F6.3 run reference (the D-6 input), as F6.4's loader renders it
    classification_id: str
    recorded_at: datetime                           # clock C: the run's transaction start
    cdn_last_modified: Optional[datetime] = None    # the retrieved bytes' CDN Last-Modified (a source time, A-5 only)
    path_epoch_at: Optional[datetime] = None        # the document path's epoch (a source time, A-5 only)
    document_retrieved_at: Optional[datetime] = None  # F2's retrieval time: orders A-5's base version, never a time

    def __post_init__(self):
        if not isinstance(self.ref, F5RunRef):
            raise EvidenceError("ExtractionRun.ref must be an F5RunRef")
        _set(self, "classification_id", str(self.classification_id))
        _set(self, "recorded_at", instant(self.recorded_at, "financial_extraction_runs.recorded_at"))
        if self.ref.recorded_at is None or instant(self.ref.recorded_at, "F5RunRef.recorded_at") != self.recorded_at:
            raise EvidenceError(f"F5 run {self.ref.f5_run_id}: its reference and its row disagree on recorded_at")
        for name in ("cdn_last_modified", "path_epoch_at", "document_retrieved_at"):
            _set(self, name, optional_instant(getattr(self, name), name))

    @property
    def f5_run_id(self):
        return self.ref.f5_run_id

    @property
    def cse_filing_id(self):
        return self.ref.cse_filing_id

    @property
    def document_sha256(self):
        return self.ref.document_sha256

    @property
    def version(self):
        """The document version (cse_filing_id, document_sha256): the availability unit (§5.2, §6.1)."""
        return (self.ref.cse_filing_id, self.ref.document_sha256)


@dataclass(frozen=True)
class ValidationRunRow:
    """financial_validation_runs (T1, 0015): one validation of one F5 run with one input set."""
    key: str
    f5_run_id: str
    cse_filing_id: int
    document_sha256: str
    issuer_link_id: Optional[int]
    publication_uploaded_at: Optional[datetime]     # F6.1's sanity input only (D-7), never availability
    versions: VersionSet
    recorded_at: datetime

    def __post_init__(self):
        _set(self, "f5_run_id", str(self.f5_run_id))
        _set(self, "publication_uploaded_at", optional_instant(self.publication_uploaded_at, "publication_uploaded_at"))
        _set(self, "recorded_at", instant(self.recorded_at, "financial_validation_runs.recorded_at"))


@dataclass(frozen=True)
class StoredObservation:
    """financial_source_observations (T5, 0015): one source observation, its E3 envelope decoded."""
    so: SourceObservation
    recorded_at: datetime

    def __post_init__(self):
        _set(self, "recorded_at", instant(self.recorded_at, "financial_source_observations.recorded_at"))

    @property
    def so_key(self):
        return self.so.so_key

    @property
    def ef_key(self):
        return self.so.ef_key

    @property
    def validation_run_key(self):
        return self.so.validation_run_key

    @property
    def f5_run_id(self):
        return self.so.f5_run.f5_run_id

    @property
    def document_sha256(self):
        return self.so.document_sha256

    @property
    def cse_filing_id(self):
        return self.so.cse_filing_id

    @property
    def identity(self):
        return self.so.identity


@dataclass(frozen=True)
class F6Configuration:
    """financial_reconciliation_configurations (T8). `configuration` is None when the stored E5 is not of the F6
    version set this code implements."""
    configuration_id: str
    configuration: Optional[ReconciliationConfiguration]
    recorded_at: datetime

    def __post_init__(self):
        _set(self, "recorded_at", instant(self.recorded_at, "recorded_at"))


@dataclass(frozen=True)
class Designation:
    """A designation row: F6.4's T9 (financial_reconciliation_designations) or F8's f8_designations (0017). The latest
    row of a purpose recorded by a time is the one in force at that time."""
    id: int
    purpose: str
    configuration_id: str
    recorded_at: datetime

    def __post_init__(self):
        _set(self, "recorded_at", instant(self.recorded_at, "recorded_at"))


@dataclass(frozen=True)
class F8ConfigurationRow:
    """f8_configurations (0017). `configuration` is None when the stored JSON names rule versions this code does not
    implement."""
    f8_configuration_id: str
    configuration: Any                  # config.F8Configuration, or None
    configuration_json: str
    recorded_at: datetime

    def __post_init__(self):
        _set(self, "recorded_at", instant(self.recorded_at, "recorded_at"))


@dataclass(frozen=True)
class StoredRecord:
    """financial_reconciliation_records (T13): one stored F6.3 reconciliation result, its E4 envelope decoded."""
    record_id: int
    ef_key: str
    recorded_at: datetime
    result: ReconciliationResult

    def __post_init__(self):
        _set(self, "recorded_at", instant(self.recorded_at, "recorded_at"))


@dataclass(frozen=True)
class StoredBatch:
    """financial_reconciliation_batches (T12) with the records of its results (T16 -> T13). `records` is None when
    the loader did not load them (only the batch in force at the query's cutoff is loaded in full)."""
    batch_id: str
    configuration_id: str
    issuer_id: str
    sequence: int
    recorded_at: datetime
    output_hash: str
    records: Optional[tuple] = None

    def __post_init__(self):
        _set(self, "batch_id", str(self.batch_id))
        _set(self, "issuer_id", str(self.issuer_id))
        _set(self, "recorded_at", instant(self.recorded_at, "recorded_at"))
        if self.records is not None:
            _set(self, "records", tuple(sorted(self.records, key=lambda r: r.ef_key)))


def _unique(rows, key, what):
    out = {}
    for row in rows:
        k = key(row)
        if k in out and out[k] != row:
            raise EvidenceError(f"two different {what} rows share the key {k!r}")
        out[k] = row
    return out


class Evidence:
    """The append-only rows one query may draw on, indexed. Rows are kept exactly as given. The rules choose among
    them by their recorded times, so the same Evidence answers queries at every horizon."""

    def __init__(self, *, filing_observations=(), classifications=(), issuer_decisions=(), runs=(),
                 validation_runs=(), observations=(), f6_configurations=(), f6_designations=(),
                 f8_configurations=(), f8_designations=(), batches=()):
        self.filing_observations = _unique(filing_observations, lambda o: o.id, "report_filing_observations")
        self.classifications = _unique(classifications, lambda c: c.id, "report_document_classifications")
        self.issuer_decisions = _unique(issuer_decisions, lambda d: d.id, "filing_issuer_links")
        self.runs = _unique(runs, lambda r: r.f5_run_id, "financial_extraction_runs")
        self.validation_runs = _unique(validation_runs, lambda v: v.key, "financial_validation_runs")
        self.observations = _unique(observations, lambda s: s.so_key, "financial_source_observations")
        self.f6_configurations = _unique(f6_configurations, lambda c: c.configuration_id,
                                         "financial_reconciliation_configurations")
        self.f6_designations = tuple(sorted(_unique(f6_designations, lambda d: d.id,
                                                    "financial_reconciliation_designations").values(),
                                            key=lambda d: d.id))
        self.f8_configurations = _unique(f8_configurations, lambda c: c.f8_configuration_id, "f8_configurations")
        self.f8_designations = tuple(sorted(_unique(f8_designations, lambda d: d.id, "f8_designations").values(),
                                            key=lambda d: d.id))
        self.batches = tuple(sorted(_unique(batches, lambda b: b.batch_id, "financial_reconciliation_batches").values(),
                                    key=lambda b: (b.configuration_id, b.issuer_id, b.sequence)))
        self._by_filing_obs = defaultdict(list)
        for o in self.filing_observations.values():
            self._by_filing_obs[o.cse_filing_id].append(o)
        self._decisions = defaultdict(list)
        for d in self.issuer_decisions.values():
            self._decisions[d.cse_filing_id].append(d)
        self._runs_by_filing = defaultdict(list)
        self._runs_by_document = defaultdict(list)
        for r in self.runs.values():
            self._runs_by_filing[r.cse_filing_id].append(r)
            self._runs_by_document[r.document_sha256].append(r)
        self._vrs_by_run = defaultdict(list)
        for v in self.validation_runs.values():
            self._vrs_by_run[v.f5_run_id].append(v)
        self._sos_by_vr = defaultdict(list)
        for s in self.observations.values():
            self._sos_by_vr[s.validation_run_key].append(s)
        for index in (self._by_filing_obs, self._decisions, self._runs_by_filing, self._runs_by_document,
                      self._vrs_by_run, self._sos_by_vr):
            for k in index:
                index[k].sort(key=_order)

    def filing_observations_of(self, cse_filing_id):
        return tuple(self._by_filing_obs.get(cse_filing_id, ()))

    def decisions_of(self, cse_filing_id):
        return tuple(self._decisions.get(cse_filing_id, ()))

    def runs_of_filing(self, cse_filing_id):
        return tuple(self._runs_by_filing.get(cse_filing_id, ()))

    def runs_of_document(self, document_sha256):
        return tuple(self._runs_by_document.get(document_sha256, ()))

    def validation_runs_of(self, f5_run_id):
        return tuple(self._vrs_by_run.get(str(f5_run_id), ()))

    def observations_of(self, validation_run_key):
        return tuple(self._sos_by_vr.get(validation_run_key, ()))

    def filings(self):
        return sorted(set(self._by_filing_obs) | set(self._runs_by_filing) | set(self._decisions))


def _order(row):
    """A fixed order for every index (never arrival order)."""
    if isinstance(row, FilingObservation):
        return (row.observed_at, row.id)
    if isinstance(row, IssuerDecision):
        return (row.id,)
    if isinstance(row, ExtractionRun):
        return (row.recorded_at, row.f5_run_id)
    if isinstance(row, ValidationRunRow):
        return (row.recorded_at, row.key)
    if isinstance(row, StoredObservation):
        return (row.so_key,)
    raise TypeError(type(row).__name__)
