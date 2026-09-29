"""
Explicit inputs of one validation run (docs/F6.2_DESIGN.md §4, §9, §15.2 D-1, D-7, D-9). F6.3 looks nothing up: the
caller supplies each input, and the validation run records exactly what it used.

  F5RunRef            the F5 run (financial_extraction_runs): its key, its F3/F4/F5 versions, recorded_at (system-
                      knowledge time) and the raw timestamp snapshot. The snapshot is source-availability EVIDENCE,
                      kept verbatim and never interpreted here; choosing an availability time is F8's policy.
  IssuerLinkDecision  the filing's filing_issuer_links decision the run uses (D-1), or None when none exists.
  DocumentContext     the F3 classification fields F6 reads: fiscal-year-end and period status (for F6.1), and the
                      document / underlying type as attributes only. Document type never gates admission and never
                      sets precedence (D-9).
  f6.inputs.1 (D-7)   the publication date given to F6.1 is the Asia/Colombo calendar date of
                      report_filings.uploaded_at: never first-seen, retrieval, processing or scheduled time. Missing
                      gives None, and F6.1 then rejects every candidate (publication_date_not_supplied).
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .versions import INPUT_POLICY_VERSION

# The same definition as worker/market_capture/config.py. A fixed offset keeps the date independent of the host's
# time-zone database. Sri Lanka has used UTC+05:30 without daylight saving since 2006-04-15 00:30 local time
# (2006-04-14 18:30 UTC); earlier instants had other offsets, so f6.inputs.1 refuses them rather than guess.
COLOMBO = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")
COLOMBO_OFFSET_VALID_FROM = datetime(2006, 4, 14, 18, 30, tzinfo=timezone.utc)
PUBLICATION_DATE_SOURCE = "report_filings.uploaded_at"

LINK_STATUSES = ("evidenced", "conflict", "unresolved")                            # migration 0007
LINK_BASES = ("document_path_prefix", "listing_symbol_sec_id", "both", "none")
F5_RUN_FIELDS = ("cse_filing_id", "document_sha256", "word_extractor", "f4_extractor_version", "classifier_version",
                 "text_extractor", "builder_version", "mapper_version", "vocabulary_version")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InputError(ValueError):
    """Malformed or inconsistent caller input: a loading or programming error, never a data outcome."""


def instant(value, what):
    """An aware datetime (or an ISO-8601 string with an offset) as UTC. A naive value is refused: no zone is assumed."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as e:
            raise InputError(f"{what}: unparseable timestamp {value!r}") from e
    if not isinstance(value, datetime):
        raise InputError(f"{what}: expected a timestamp, not {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InputError(f"{what}: naive timestamp {value.isoformat()} (no time zone is assumed)")
    return value.astimezone(timezone.utc)


def _iso(value, what):
    return None if value is None else instant(value, what).isoformat()


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{what} must be an integer, not {value!r}")
    return value


# ------------------------------------------------------------------------------------------------ f6.inputs.1

@dataclass(frozen=True)
class PublicationInput:
    policy_version: str                  # f6.inputs.1
    source_field: str                    # report_filings.uploaded_at
    uploaded_at: Optional[str]           # the instant used, ISO-8601 UTC
    publication_date: Optional[date]     # its Asia/Colombo calendar date


def publication_input(uploaded_at):
    """f6.inputs.1 (D-7): the date F6.1 checks period ends against (period_end_after_publication)."""
    if uploaded_at is None:
        return PublicationInput(INPUT_POLICY_VERSION, PUBLICATION_DATE_SOURCE, None, None)
    at = instant(uploaded_at, PUBLICATION_DATE_SOURCE)
    if at < COLOMBO_OFFSET_VALID_FROM:
        raise InputError(f"{PUBLICATION_DATE_SOURCE} {at.isoformat()} predates Colombo's fixed UTC+05:30 offset "
                         f"(2006-04-15); f6.inputs.1 does not guess an older offset")
    return PublicationInput(INPUT_POLICY_VERSION, PUBLICATION_DATE_SOURCE, at.isoformat(), at.astimezone(COLOMBO).date())


# ------------------------------------------------------------------------------------------------ issuer link

@dataclass(frozen=True)
class IssuerLinkDecision:
    """One filing_issuer_links row (migration 0007). F5's rules make the decision; F6 only reads it."""
    cse_filing_id: int
    status: str                          # evidenced | conflict | unresolved
    basis: str                           # document_path_prefix | listing_symbol_sec_id | both | none
    issuer_id: Optional[str]             # issuers.issuer_id; present exactly when evidenced
    link_id: Optional[int] = None        # filing_issuer_links.id
    decided_at: Optional[str] = None     # when F5 decided (system-knowledge time; provenance only)

    def __post_init__(self):
        _int(self.cse_filing_id, "cse_filing_id")
        if self.link_id is not None:
            _int(self.link_id, "link_id")
        if self.status not in LINK_STATUSES:
            raise InputError(f"issuer-link status {self.status!r} is not one of {LINK_STATUSES}")
        if self.basis not in LINK_BASES:
            raise InputError(f"issuer-link basis {self.basis!r} is not one of {LINK_BASES}")
        if self.issuer_id is not None and (not isinstance(self.issuer_id, str) or not self.issuer_id.strip()):
            raise InputError(f"issuer_id must be a non-empty string, not {self.issuer_id!r}")
        if (self.status == "evidenced") != (self.issuer_id is not None):   # 0007 chk_fil_consistent
            raise InputError("an issuer link carries an issuer_id exactly when it is evidenced")
        if self.status == "evidenced" and self.basis == "none":
            raise InputError("an evidenced issuer link needs a basis other than 'none'")
        object.__setattr__(self, "decided_at", _iso(self.decided_at, "decided_at"))


# ------------------------------------------------------------------------------------------------ F3 context

@dataclass(frozen=True)
class DocumentContext:
    """The F3 classification fields F6 reads (report_document_classifications, migration 0005)."""
    classifier_version: Optional[str]
    text_extractor: Optional[str]
    document_type: Optional[str]
    document_type_status: Optional[str]
    underlying_type: Optional[str]           # the content type beneath an errata or amendment
    underlying_type_status: Optional[str]
    fiscal_year_end_basis: Optional[str]
    fiscal_year_end_status: Optional[str]
    period_status: Optional[str]
    classification_id: Optional[str] = None

    @classmethod
    def from_classification(cls, classification, classification_id=None):
        c = classification.to_dict() if hasattr(classification, "to_dict") else dict(classification or {})
        return cls(c.get("classifier_version"), c.get("text_extractor"), c.get("document_type"),
                   c.get("document_type_status"), c.get("underlying_type"), c.get("underlying_type_status"),
                   c.get("fiscal_year_end_basis"), c.get("fiscal_year_end_status"), c.get("period_status"),
                   classification_id if classification_id is not None else c.get("id"))

    def f6_1_classification(self):
        """The fields F6.1's run_evidence() reads."""
        return {"fiscal_year_end_basis": self.fiscal_year_end_basis,
                "fiscal_year_end_status": self.fiscal_year_end_status, "period_status": self.period_status}


# ------------------------------------------------------------------------------------------------ F5 run

@dataclass(frozen=True)
class F5RunRef:
    """One F5 run (financial_extraction_runs, migration 0008): the processing of ONE document from ONE filing."""
    f5_run_id: str
    cse_filing_id: int
    document_sha256: str
    word_extractor: str
    f4_extractor_version: str
    classifier_version: str
    text_extractor: Optional[str]
    builder_version: str
    mapper_version: str
    vocabulary_version: str
    recorded_at: Optional[str] = None        # ISO-8601 UTC; None only for an in-memory run that was never persisted
    classification_id: Optional[str] = None
    content_sha256: Optional[str] = None
    timestamps: tuple = ()                   # the F5 raw timestamp snapshot: sorted (field, value) pairs, verbatim

    def __post_init__(self):
        if not isinstance(self.f5_run_id, str) or not self.f5_run_id:
            raise InputError(f"f5_run_id must be a non-empty string, not {self.f5_run_id!r}")
        _int(self.cse_filing_id, "cse_filing_id")
        if not isinstance(self.document_sha256, str) or not _SHA256_RE.match(self.document_sha256):
            raise InputError(f"document_sha256 must be 64 lowercase hex digits, not {self.document_sha256!r}")
        for name in ("word_extractor", "f4_extractor_version", "classifier_version", "builder_version",
                     "mapper_version", "vocabulary_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise InputError(f"{name} must be a non-empty string")
        object.__setattr__(self, "recorded_at", _iso(self.recorded_at, "recorded_at"))
        ts = self.timestamps.items() if isinstance(self.timestamps, dict) else self.timestamps
        object.__setattr__(self, "timestamps", tuple(sorted((str(k), v) for k, v in ts)))

    @property
    def f3_version(self):
        return (self.classifier_version, self.text_extractor)

    @property
    def f4_version(self):
        return (self.word_extractor, self.f4_extractor_version)

    @property
    def f5_version(self):
        return (self.builder_version, self.mapper_version, self.vocabulary_version)


def f5_run_ref(result, *, f5_run_id, recorded_at=None, classification_id=None, content_sha256=None):
    """F5RunRef from an F5 build() result's run block (in memory or its JSON form)."""
    run = result["run"]
    return F5RunRef(f5_run_id, run["cse_filing_id"], run["document_sha256"], run["word_extractor"],
                    run["f4_extractor_version"], run["classifier_version"], run.get("text_extractor"),
                    run["builder_version"], run["mapper_version"], run["vocabulary_version"], recorded_at,
                    classification_id, content_sha256, tuple((run.get("timestamps") or {}).items()))


def check_consistency(result, f5_run, issuer_link, document):
    """Refuse inputs that belong to different runs, filings or documents."""
    run = result.get("run") or {}
    for field in F5_RUN_FIELDS:
        if field in run and run[field] != getattr(f5_run, field):
            raise InputError(f"the F5 result and the F5 run reference disagree on {field}")
    if issuer_link is not None and issuer_link.cse_filing_id != f5_run.cse_filing_id:
        raise InputError(f"the issuer-link decision is for filing {issuer_link.cse_filing_id}, "
                         f"the F5 run for filing {f5_run.cse_filing_id}")
    if document.classifier_version is not None and document.classifier_version != f5_run.classifier_version:
        raise InputError("the F3 classification and the F5 run disagree on classifier_version")
    if None not in (document.text_extractor, f5_run.text_extractor) and document.text_extractor != f5_run.text_extractor:
        raise InputError("the F3 classification and the F5 run disagree on text_extractor")
    if None not in (document.classification_id, f5_run.classification_id) \
            and document.classification_id != f5_run.classification_id:
        raise InputError("the F3 classification and the F5 run disagree on classification_id")
