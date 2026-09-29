"""
f6.identity.1 (docs/F6.2_DESIGN.md §2, §6): WHAT an economic fact is. An identity never carries a value; values exist
only in reconciliation results.

ef_key = SHA-256 of the JSON object that holds exactly these fields, in this order:

    identity_version, issuer_id, concept_key, period_kind, period_end, duration_months, scope, operations, maturity,
    currency

Encoding: keys in that order, separators ',' and ':', ASCII only; period_end 'YYYY-MM-DD'; duration_months an integer
(null for an instant); every other field a string.

Never part of the identity: role, period start, audit status, fiscal label, reported scale or printed representation,
F5 canonical scope, F3 document type, filing, F5 run, document hash, processing times, or any F3 / F4 / F5 / F6.1
version. Those are attributes of the source observation.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .. import financial_concepts as fc
from .. import financial_validation as fv
from .canonical import ordered_json, sha256_hex
from .versions import IDENTITY_VERSION

IDENTITY_FIELDS = ("identity_version", "issuer_id", "concept_key", "period_kind", "period_end", "duration_months",
                   "scope", "operations", "maturity", "currency")
PERIOD_KINDS = ("instant", "duration")
SCOPES = ("group", "company", "bank", "unlabelled")
OPERATIONS = ("total_or_unstated", "continuing", "discontinued")
MATURITIES = ("current", "non_current")
MATURITY_NOT_APPLICABLE = "not_applicable"
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class IdentityError(ValueError):
    """An identity that f6.identity.1 cannot represent: a programming error, never a data outcome."""


@dataclass(frozen=True)
class EconomicFactIdentity:
    identity_version: str
    issuer_id: str
    concept_key: str
    period_kind: str
    period_end: date
    duration_months: Optional[int]
    scope: str
    operations: str
    maturity: str
    currency: str

    def __post_init__(self):
        if self.identity_version != IDENTITY_VERSION:
            raise IdentityError(f"this code implements {IDENTITY_VERSION} only, not {self.identity_version!r}")
        if not isinstance(self.issuer_id, str) or not self.issuer_id.strip():
            raise IdentityError("issuer_id must be a non-empty string")
        concept = fc.BY_KEY.get(self.concept_key)
        if concept is None or concept.status != "active":
            raise IdentityError(f"concept {self.concept_key!r} is not an active v1 concept")
        if self.period_kind not in PERIOD_KINDS or concept.period_kind != self.period_kind:
            raise IdentityError(f"period_kind {self.period_kind!r} does not fit concept {self.concept_key!r}")
        if not isinstance(self.period_end, date) or isinstance(self.period_end, datetime):
            raise IdentityError("period_end must be a date")
        months = self.duration_months
        if self.period_kind == "duration":
            if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
                raise IdentityError("a duration needs a positive integer duration_months")
        elif months is not None:
            raise IdentityError("an instant has no duration_months")
        if self.scope not in SCOPES:
            raise IdentityError(f"scope {self.scope!r} is not one of {SCOPES}")
        if self.operations not in OPERATIONS:
            raise IdentityError(f"operations {self.operations!r} is not one of {OPERATIONS}")
        wanted = MATURITIES if self.concept_key in fv.BORROWING_CONCEPTS else (MATURITY_NOT_APPLICABLE,)
        if self.maturity not in wanted:
            raise IdentityError(f"maturity {self.maturity!r} is not one of {wanted} for {self.concept_key!r}")
        if not isinstance(self.currency, str) or not _CURRENCY_RE.match(self.currency):
            raise IdentityError(f"currency {self.currency!r} is not a 3-letter code")

    def identity_fields(self):
        """(name, value) in f6.identity.1 order, values plain."""
        return tuple((name, self.period_end.isoformat() if name == "period_end" else getattr(self, name))
                     for name in IDENTITY_FIELDS)

    def canonical_json(self):
        return ordered_json(self.identity_fields())

    @property
    def ef_key(self):
        return sha256_hex(self.canonical_json())

    def without_currency(self):
        """Every identity field except currency: the key for 'the same fact presented in two currencies'."""
        return tuple(value for name, value in self.identity_fields() if name != "currency")
