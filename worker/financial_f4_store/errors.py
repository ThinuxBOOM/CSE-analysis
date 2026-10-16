"""R-F4's typed refusals (docs/R_F4_DESIGN.md sections 4.2 and 5). A refusal is never repaired or worked around."""


class F4Refused(Exception):
    """Base class. `kind` names the refusal."""
    kind = "refused"

    def __init__(self, detail):
        super().__init__(f"{self.kind}: {detail}")
        self.detail = detail


# --- writing ------------------------------------------------------------------------------------------------------

class UnsupportedValue(F4Refused):
    """A value R-F4 cannot store exactly (NUL, an unpaired surrogate, a non-finite number, an unexpected type, a cell
    that differs from what it copies from its statement, row and column). Refused, never coerced."""
    kind = "unsupported_value"


class SchemaUnavailable(F4Refused):
    """Migration 0018's schema is not usable, so nothing is written (the store's in-band check, schema.problems, run
    before every save): one of the five tables is missing; the connection's role lacks SELECT or INSERT on one; or a
    guard or append-only trigger is missing, disabled (or replica-only), or differs from how 0018 creates it: another
    function (by schema, signature, language or definition), another timing, a WHEN condition, arguments or a column
    list."""
    kind = "schema_unavailable"


class ConflictingEvidence(F4Refused):
    """Evidence with the same key is already saved with different content: an extraction with the same key, or the F3
    classification the extraction would name (persisted under its id, its content differs from the classification F5
    consumed: f3_input_sha256)."""
    kind = "conflicting"


# --- reading ------------------------------------------------------------------------------------------------------

EVIDENCE_KINDS = ("missing", "incomplete", "corrupt", "version_incompatible", "f5_rebuild_refused", "f5_mismatch")


class EvidenceRefused(F4Refused):
    """Saved evidence that cannot be used as it is:
        missing              no extraction for the key
        incomplete           the child rows differ from the extraction's counts
        corrupt              an encoding, the content hash or the F3 input hash does not match
        version_incompatible a store version or F4 extractor version this reader does not support
        f5_rebuild_refused   F5's own consistency check refused the rebuild under the declared decimal context
        f5_mismatch          the rebuilt F5 content_sha256 differs from the stored one (verify)"""

    def __init__(self, kind, detail):
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence refusal {kind!r}")
        self.kind = kind
        super().__init__(detail)
