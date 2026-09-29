"""
The F6 version identifiers (docs/F6.2_DESIGN.md §2-§7, §15.2 D-7). A rule change needs a new version; this code
implements exactly one version of each and refuses to label its output with any other.
"""
from dataclasses import dataclass

from ..financial_validation import VALIDATION_VERSION      # f6.validation.1 (F6.1, frozen)

INPUT_POLICY_VERSION = "f6.inputs.1"
OP1_VERSION = "f6.op1.partition.1"
ADMISSION_VERSION = "f6.admission.1"
IDENTITY_VERSION = "f6.identity.1"
RECONCILIATION_VERSION = "f6.reconciliation.1"


@dataclass(frozen=True)
class VersionSet:
    """The versions recorded on every validation run and source observation (reconciliation adds its own)."""
    validation_version: str = VALIDATION_VERSION
    input_policy_version: str = INPUT_POLICY_VERSION
    op1_version: str = OP1_VERSION
    admission_version: str = ADMISSION_VERSION
    identity_version: str = IDENTITY_VERSION


IMPLEMENTED = VersionSet()
