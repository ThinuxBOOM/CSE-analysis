"""
The F8 result and its hash (docs/F8_DESIGN.md §11, §13.2): AsOfResult(mode, label, T, H, configuration, rule versions,
facts=[FactView ...], result_hash).

Every element of a result is computed from its information set Ω and from nothing else (§7.7, I-11).
- **What the envelope holds.** The query itself (mode, T, H, issuer, fact filter), the F8 configuration (its id, the
  F6 configuration id and the four rule versions), the F6.4 batch in force (KNOWN_RECORDED only), and the fact views.
- **What it never holds.**
  - How the configuration was found (designated or pinned): a replay that pins the configuration it recorded gives the
    same hash.
  - The query time.
  - Any row outside Ω.

result_hash = SHA-256 of the canonical JSON (F6.1's canonical_json: sorted keys, Decimals as plain strings, ASCII) of
the result with result_hash = "". F6.3's output_hash uses the same convention. So a relabelled envelope (a CURRENT
result presented as historical, T-40) fails its own re-proof, and replaying the stored query re-proves a pinned hash
(T-19).
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from ..financial_truth import canonical
from ..financial_truth.identity import EconomicFactIdentity
from ..financial_truth.observations import ReportedValue
from ..financial_truth.reconciliation import ReconciliationResult
from .query import FactFilter
from .versions import RuleVersions

NONE = "none"                                   # F8's own state: no visible observation (§7.6)
STATES = ("single_source", "corroborated", "conflicting", NONE)
NOT_YET_AVAILABLE, METADATA_TIE, SUPERSEDED = "not_yet_available", "metadata_version_tie", "superseded_by"
EXCLUSION_REASONS = (NOT_YET_AVAILABLE, METADATA_TIE, SUPERSEDED)          # in-set reasons only (§7.7, F-1)
AMBIGUOUS = "ambiguous_supersession"
AVAILABLE_AFTER_KNOWN = "available_after_known"


@dataclass(frozen=True)
class VersionView:
    """One document version that counts for an observation's availability (A-6)."""
    cse_filing_id: int
    available_at: Optional[str]
    precision: Optional[str]
    role: str                                   # A-5: base | later | unordered
    basis: str
    flags: tuple
    evidence_hash: str


@dataclass(frozen=True)
class AvailabilityView:
    policy: str                                 # f8.availability.1
    available_at: Optional[str]                 # None: availability_unknown
    precision: Optional[str]                    # instant | day
    versions: tuple                             # VersionView, by filing
    flags: tuple


@dataclass(frozen=True)
class KnowledgeView:
    rule: str                                   # f8.knowledge.1
    known_at: str
    set_by: tuple                               # (term, table, key) of the rows whose time is known_at


@dataclass(frozen=True)
class ObservationView:
    so_key: str
    so_output_hash: str
    document_sha256: str
    cse_filing_id: int
    f5_run_id: str
    validation_run_key: str
    availability: AvailabilityView
    knowledge: KnowledgeView
    flags: tuple                                # availability flags, availability_unknown, available_after_known:<p>


@dataclass(frozen=True)
class Exclusion:
    """An excluded observation inside the information set (§7.7): never one from outside it."""
    so_key: str
    document_sha256: str
    cse_filing_id: int
    f5_run_id: str
    validation_run_key: str
    reason: str                                 # not_yet_available (KNOWN only) | metadata_version_tie | superseded_by
    superseded_by: tuple                        # so_keys (superseded_by only)


@dataclass(frozen=True)
class FactView:
    ef_key: str
    identity: Optional[EconomicFactIdentity]    # None only for a fact requested by ef_key with nothing in Ω
    state: str                                  # single_source | corroborated | conflicting (F6.3) | none
    value_kind: Optional[str]
    interval_low: Optional[Decimal]
    interval_high: Optional[Decimal]
    representative: Optional[ReportedValue]
    representative_normalized_value: Optional[Decimal]
    representative_half_unit: Optional[Decimal]
    f6_result: Optional[ReconciliationResult]   # exactly F6.3's (recomputed modes) or F6.4's stored record (Q1)
    visible: tuple                              # ObservationView: the observations the F6.3 result reconciles
    excluded: tuple                             # Exclusion
    supersession: tuple                         # supersession.SupersessionRecord applied
    ambiguities: tuple                          # supersession.Ambiguity (ambiguous_supersession)
    flags: tuple
    counts: tuple                               # (reason, number of excluded observations), in-set reasons only


@dataclass(frozen=True)
class RecordedBatch:
    """KNOWN_RECORDED: the F6.4 batch (T12) in force at T for the issuer and configuration."""
    batch_id: str
    sequence: int
    recorded_at: str
    output_hash: str


@dataclass(frozen=True)
class AsOfResult:
    mode: str
    label: str
    information_cutoff: Optional[str]
    knowledge_horizon: Optional[str]
    issuer_id: str
    facts_requested: FactFilter
    f8_configuration_id: str
    f6_configuration_id: str
    rule_versions: RuleVersions
    recorded_batch: Optional[RecordedBatch]
    facts: tuple
    result_hash: str = ""

    def envelope(self):
        """The canonical JSON that result_hash covers."""
        return canonical.canonical_json(replace(self, result_hash=""))

    def computed_hash(self):
        return canonical.sha256_hex(self.envelope())

    def verify(self):
        """Re-proof of the envelope against its own hash."""
        return bool(self.result_hash) and self.result_hash == self.computed_hash()

    def fact(self, ef_key):
        return next((f for f in self.facts if f.ef_key == ef_key), None)


def seal(result):
    """The result with its result_hash."""
    return replace(result, result_hash=result.computed_hash())
