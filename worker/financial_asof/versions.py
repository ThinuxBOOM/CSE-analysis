"""
The F8 rule versions (docs/F8_DESIGN.md §11, §13.1). Each rule is a code constant, as F6's are: a rule change is a new
version string, and a result under an old version stays reproducible. This code implements exactly one version of
each rule and refuses to compute under any other: an F8 configuration naming another version is refused, never
reinterpreted (T-22).
"""
from dataclasses import dataclass

SELECTION_VERSION = "f8.selection.1"          # §7.4: as-of selection, the result envelope and its hash
AVAILABILITY_VERSION = "f8.availability.1"    # §5.2 (OD-1, owner-approved 2026-10-05)
SUPERSESSION_VERSION = "f8.supersession.1"    # §6.3 (OD-2, owner-approved 2026-10-05)
KNOWLEDGE_VERSION = "f8.knowledge.1"          # §4.2, §4.4 (OD-3, owner-approved 2026-10-05)


@dataclass(frozen=True)
class RuleVersions:
    selection_version: str = SELECTION_VERSION
    availability_version: str = AVAILABILITY_VERSION
    supersession_version: str = SUPERSESSION_VERSION
    knowledge_version: str = KNOWLEDGE_VERSION


IMPLEMENTED = RuleVersions()
