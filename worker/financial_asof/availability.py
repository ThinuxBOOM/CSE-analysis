"""
f8.availability.1 (docs/F8_DESIGN.md §5.2; OD-1, owner-approved 2026-10-05): when CSE made a document version public,
as known at an evidence horizon E. A version is v = (cse_filing_id, document_sha256).

A-1 Only CSE's own upload (U) and authorization (A) instants, from F1 listing evidence, are availability evidence.
    The path epoch and CDN Last-Modified enter only through A-5, and only to delay a later version. Observation,
    first-seen, retrieval, classification, processing and recorded times are never availability.
A-2 available_at is the LATEST of every U and A instant observed for the filing by E, across sources and metadata
    versions. So a later edit that moves a timestamp backward never makes anything visible earlier (I-7).
A-3 A date-only value counts as the end of that Colombo day: the next 00:00 Asia/Colombo.
A-4 No usable U or A: availability_unknown. No instant is invented, and no system time stands in.
A-5 The first version F2 retrieved for a filing takes the filing's availability. A later version takes
    max(filing availability, its CDN Last-Modified, its path epoch). With neither document-level time it is
    availability_unknown.
A-6 The same bytes under several filings are one document. Its availability is the earliest among the filings carrying
    it (document_availability; selection.py chooses the versions that count in each mode).
A-7 Contradictory evidence is kept and flagged, never repaired: availability_evidence_changed,
    availability_sources_disagree, last_modified_after_upload. (available_after_known needs known_at; selection.py.)

Implementation choices where the design is silent (docs/F8_IMPLEMENTATION.md §3):
- **Date-only.** A value at exactly 00:00:00.000000 Colombo local time is date-only. RDV P-32 shows that CSE's legacy
  date-only values arrive as Colombo midnight, as a feed string ("... 12:00:00 AM") or as epoch milliseconds. A genuine
  upload at that exact instant is delayed to the end of its day, never advanced.
- **Several runs of one version.** A version's document-level times are the latest of its runs known by E. That is
  the conservative bound, and it never decreases as E grows.
- **A-5's base version.** A filing with one version known by E has it as its base. With several, the base is the
  version whose earliest F2 retrieval time is strictly earliest. When a version has no retrieval time, or the earliest
  times tie, there is no base, and every version takes the later-version rule (never earlier than the filing).
- **availability_evidence_changed** compares the versions of one listing source. **availability_sources_disagree**
  compares feed values with listing values (1 second or more apart). Both look at every observation known by E.
- **last_modified_after_upload** compares a version's latest CDN Last-Modified with the latest effective upload
  instant (after A-3).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .. import report_discovery as rd
from ..financial_truth import canonical
from .errors import EvidenceError
from .times import colombo_date, colombo_end_of_day, instant, is_colombo_midnight, iso
from .versions import AVAILABILITY_VERSION

FEED, LISTING = rd.FEED_ENDPOINT, rd.LISTING_ENDPOINT
UPLOAD, AUTHORIZATION = "upload", "authorization"
_FIELDS = ((UPLOAD, "uploaded_at"), (AUTHORIZATION, "authorized_at"))
DISAGREE = timedelta(seconds=1)
INSTANT, DAY = "instant", "day"
BASE, LATER, UNORDERED = "base", "later", "unordered"
FLAG_CHANGED = "availability_evidence_changed"
FLAG_DISAGREE = "availability_sources_disagree"
FLAG_LAST_MODIFIED = "last_modified_after_upload"
FLAG_UNKNOWN = "availability_unknown"


@dataclass(frozen=True)
class SourceInstant:
    """One U or A value of one F1 observation."""
    kind: str                   # upload | authorization
    observation_id: str
    source: str                 # F1 source key: endpoint|bucket|query symbol
    source_endpoint: str
    raw: Optional[str]          # F1's raw text, verbatim
    value: datetime             # as F1 parsed it (UTC)
    effective: datetime         # A-3: the end of the Colombo day for a date-only value; else the value
    precision: str              # instant | day


@dataclass(frozen=True)
class FilingAvailability:
    cse_filing_id: int
    horizon: datetime
    at: Optional[datetime]      # A-2 over A-3; None: no usable U or A (A-4)
    precision: Optional[str]
    basis: tuple                # the kinds (upload / authorization) whose effective value is the maximum
    instants: tuple             # every SourceInstant observed by E
    flags: tuple
    observation_ids: tuple      # every F1 observation read (observed by E)


@dataclass(frozen=True)
class VersionAvailability:
    cse_filing_id: int
    document_sha256: str
    horizon: datetime
    at: Optional[datetime]      # None: availability_unknown
    precision: Optional[str]
    role: str                   # base | later | unordered (A-5)
    basis: str                  # filing_instants | later_version | no_cse_instant | later_version_without_document_time
    flags: tuple
    filing: FilingAvailability
    last_modified: Optional[datetime]     # the version's latest CDN Last-Modified among its runs known by E
    path_epoch: Optional[datetime]        # the version's latest path epoch among its runs known by E
    run_ids: tuple                        # the version's runs known by E
    evidence_hash: str


@dataclass(frozen=True)
class DocumentAvailability:
    """A-6: one document (SHA-256) over the versions that count. Its time is the earliest known one among them."""
    document_sha256: str
    at: Optional[datetime]
    precision: Optional[str]
    versions: tuple             # VersionAvailability, by filing
    flags: tuple


def _instants(observation):
    try:
        parsed = rd.parse_listing_item(observation.raw_item, observation.source_endpoint, observation.source_bucket,
                                       observation.query_symbol)
    except rd.ItemRejected as exc:
        raise EvidenceError(f"F1 observation {observation.id} is not a listing entry F1 accepts ({exc})") from None
    source = rd.source_key(observation.source_endpoint, observation.source_bucket, observation.query_symbol)
    present, out = {}, []
    for kind, name in _FIELDS:
        if name not in parsed.fields:
            continue                                        # the entry lacks the key: nothing observed
        value = parsed.fields[name]
        present[kind] = None if value is None else instant(value, name)
        if value is None:                                   # null or unparseable: no usable evidence (A-4)
            continue
        value = instant(value, name)
        day = is_colombo_midnight(value)
        effective = colombo_end_of_day(colombo_date(value)) if day else value
        out.append(SourceInstant(kind, observation.id, source, observation.source_endpoint,
                                 parsed.raw_texts.get(f"{name}_raw"), value, effective, DAY if day else INSTANT))
    return out, present, source


def filing_availability(evidence, cse_filing_id, horizon):
    """A-1 to A-4 and the filing-level A-7 flags, from the F1 observations observed at or before E."""
    rows = [o for o in evidence.filing_observations_of(cse_filing_id) if o.observed_at <= horizon]
    instants, per_source = [], {}
    for o in rows:
        found, present, source = _instants(o)
        instants.extend(found)
        for kind, value in present.items():
            per_source.setdefault((source, kind), set()).add(value)
    flags = set()
    if any(len(values) > 1 for values in per_source.values()):
        flags.add(FLAG_CHANGED)
    for kind, _ in _FIELDS:
        feed = [i.value for i in instants if i.kind == kind and i.source_endpoint == FEED]
        listing = [i.value for i in instants if i.kind == kind and i.source_endpoint == LISTING]
        if any(abs(a - b) >= DISAGREE for a in feed for b in listing):
            flags.add(FLAG_DISAGREE)
    instants.sort(key=lambda i: (i.effective, i.kind, i.observation_id))
    if not instants:
        at = precision = None
        basis = ()
    else:
        at = max(i.effective for i in instants)
        top = [i for i in instants if i.effective == at]
        precision = INSTANT if any(i.precision == INSTANT for i in top) else DAY
        basis = tuple(sorted({i.kind for i in top}))
    return FilingAvailability(cse_filing_id, horizon, at, precision, basis, tuple(instants), tuple(sorted(flags)),
                              tuple(sorted(o.id for o in rows)))


def versions_of_filing(evidence, cse_filing_id, horizon):
    """{document_sha256: the runs of that version recorded at or before E}."""
    out = {}
    for r in evidence.runs_of_filing(cse_filing_id):
        if r.recorded_at <= horizon:
            out.setdefault(r.document_sha256, []).append(r)
    return out


def _roles(versions):
    """A-5's base version among a filing's versions known by E (see the module notes)."""
    if len(versions) == 1:
        return {sha: BASE for sha in versions}
    first = {}
    for sha, runs in versions.items():
        times = [r.document_retrieved_at for r in runs if r.document_retrieved_at is not None]
        if not times:
            return {s: UNORDERED for s in versions}
        first[sha] = min(times)
    earliest = min(first.values())
    leaders = [sha for sha, t in first.items() if t == earliest]
    if len(leaders) != 1:
        return {s: UNORDERED for s in versions}
    return {sha: BASE if sha == leaders[0] else LATER for sha in versions}


def _latest(values):
    values = [v for v in values if v is not None]
    return max(values) if values else None


def _evidence_hash(filing, sha, versions):
    runs = sorted((r for runs in versions.values() for r in runs), key=lambda r: r.f5_run_id)
    return canonical.digest({
        "policy": AVAILABILITY_VERSION, "cse_filing_id": filing.cse_filing_id, "document_sha256": sha,
        "observations": list(filing.observation_ids),
        "instants": [[i.observation_id, i.kind, i.raw, iso(i.value)] for i in filing.instants],
        "runs": [[r.f5_run_id, r.document_sha256, iso(r.recorded_at), iso(r.cdn_last_modified), iso(r.path_epoch_at),
                  iso(r.document_retrieved_at)] for r in runs]})


def version_availability(evidence, cse_filing_id, document_sha256, horizon, *, filing=None, versions=None):
    """f8.availability.1 for one document version at evidence horizon E."""
    filing = filing or filing_availability(evidence, cse_filing_id, horizon)
    versions = versions if versions is not None else versions_of_filing(evidence, cse_filing_id, horizon)
    runs = versions.get(document_sha256)
    if not runs:
        raise EvidenceError(f"version ({cse_filing_id}, {document_sha256}) has no F5 run known at {iso(horizon)}")
    role = _roles(versions)[document_sha256]
    last_modified = _latest(r.cdn_last_modified for r in runs)
    path_epoch = _latest(r.path_epoch_at for r in runs)
    flags = set(filing.flags)
    uploads = [i.effective for i in filing.instants if i.kind == UPLOAD]
    if last_modified is not None and uploads and last_modified > max(uploads):
        flags.add(FLAG_LAST_MODIFIED)
    if filing.at is None:
        at, precision, basis = None, None, "no_cse_instant"
    elif role == BASE:
        at, precision, basis = filing.at, filing.precision, "filing_instants"
    else:
        document_times = [t for t in (last_modified, path_epoch) if t is not None]
        if not document_times:
            at, precision, basis = None, None, "later_version_without_document_time"
        else:
            at = max([filing.at] + document_times)
            precision = INSTANT if at in document_times else filing.precision
            basis = "later_version"
    if at is None:
        flags.add(FLAG_UNKNOWN)
    return VersionAvailability(cse_filing_id, document_sha256, horizon, at, precision, role, basis,
                               tuple(sorted(flags)), filing, last_modified, path_epoch,
                               tuple(sorted(r.f5_run_id for r in runs)),
                               _evidence_hash(filing, document_sha256, versions))


def document_availability(document_sha256, versions):
    """A-6: the earliest known availability among the given versions of one document."""
    versions = tuple(sorted(versions, key=lambda v: v.cse_filing_id))
    known = [v for v in versions if v.at is not None]
    flags = set(f for v in versions for f in v.flags if f != FLAG_UNKNOWN)
    if not known:
        flags.add(FLAG_UNKNOWN)
        return DocumentAvailability(document_sha256, None, None, versions, tuple(sorted(flags)))
    at = min(v.at for v in known)
    precision = INSTANT if any(v.precision == INSTANT for v in known if v.at == at) else DAY
    return DocumentAvailability(document_sha256, at, precision, versions, tuple(sorted(flags)))


class Cache:
    """Availability at one evidence horizon, computed once per filing and version (a pure memo)."""

    def __init__(self, evidence, horizon):
        self.evidence, self.horizon = evidence, horizon
        self._filings, self._versions, self._each = {}, {}, {}

    def version(self, cse_filing_id, document_sha256):
        key = (cse_filing_id, document_sha256)
        if key not in self._each:
            if cse_filing_id not in self._filings:
                self._filings[cse_filing_id] = filing_availability(self.evidence, cse_filing_id, self.horizon)
                self._versions[cse_filing_id] = versions_of_filing(self.evidence, cse_filing_id, self.horizon)
            self._each[key] = version_availability(self.evidence, cse_filing_id, document_sha256, self.horizon,
                                                   filing=self._filings[cse_filing_id],
                                                   versions=self._versions[cse_filing_id])
        return self._each[key]

    def document(self, document_sha256, filings):
        return document_availability(document_sha256, [self.version(f, document_sha256) for f in filings])
