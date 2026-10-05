"""
The F8 configuration and its designation (docs/F8_DESIGN.md §7.1, §11, §13.1).

An F8 configuration names the four F8 rule versions and the F6 configuration it reconciles under. It is
content-addressed: f8_configuration_id = SHA-256 of its canonical JSON (F6.1's canonical_json: sorted keys, ASCII). The
database recomputes that hash (migration 0017).

The canonical configuration is chosen by the owner (f8_designations, owner path only, append-only). The designation in
force at a time G is the latest row (highest id) of its purpose recorded at or before G, as F6.4's T9 view does it. A
query that pins no configuration uses the designation in force at its governing horizon. Before any designation it is
refused, and today's designation is never used instead (§7.1).
"""
import json
import re
from dataclasses import dataclass

from ..financial_truth import canonical
from .errors import Refused
from .versions import (AVAILABILITY_VERSION, IMPLEMENTED, KNOWLEDGE_VERSION, SELECTION_VERSION, SUPERSESSION_VERSION,
                       RuleVersions)

PURPOSE = "canonical"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = {"selection_version": re.compile(r"^f8\.selection\.[0-9]+$"),
            "availability_version": re.compile(r"^f8\.availability\.[0-9]+$"),
            "supersession_version": re.compile(r"^f8\.supersession\.[0-9]+$"),
            "knowledge_version": re.compile(r"^f8\.knowledge\.[0-9]+$")}


@dataclass(frozen=True)
class F8Configuration:
    f6_configuration_id: str
    selection_version: str = SELECTION_VERSION
    availability_version: str = AVAILABILITY_VERSION
    supersession_version: str = SUPERSESSION_VERSION
    knowledge_version: str = KNOWLEDGE_VERSION

    def __post_init__(self):
        if not isinstance(self.f6_configuration_id, str) or not _HEX64.match(self.f6_configuration_id):
            raise Refused("invalid_configuration", "f6_configuration_id must be 64 lower-case hex digits")
        for name, pattern in _VERSION.items():
            if not isinstance(getattr(self, name), str) or not pattern.match(getattr(self, name)):
                raise Refused("invalid_configuration", f"{name} {getattr(self, name)!r} is not an F8 rule version")

    def canonical_json(self):
        return canonical.canonical_json(self)

    @property
    def f8_configuration_id(self):
        return canonical.sha256_hex(self.canonical_json())

    @property
    def rule_versions(self):
        return RuleVersions(self.selection_version, self.availability_version, self.supersession_version,
                            self.knowledge_version)

    @property
    def implemented(self):
        return self.rule_versions == IMPLEMENTED


def decode(configuration_json):
    """An F8Configuration from its stored canonical JSON. The round trip must be exact."""
    try:
        data = json.loads(configuration_json)
        cfg = F8Configuration(**data)
    except (ValueError, TypeError) as exc:
        raise Refused("invalid_configuration", f"stored F8 configuration is not an F8Configuration: {exc}") from None
    if cfg.canonical_json() != configuration_json:
        raise Refused("invalid_configuration", "stored F8 configuration JSON is not canonical")
    return cfg


def in_force(designations, at, purpose=PURPOSE):
    """The designation of `purpose` in force at `at`: the highest id recorded at or before `at`, or None."""
    rows = [d for d in designations if d.purpose == purpose and d.recorded_at <= at]
    return max(rows, key=lambda d: d.id) if rows else None


@dataclass(frozen=True)
class Resolved:
    configuration: F8Configuration
    designation: object             # the F8 designation in force at G, or None when pinned


def resolve(evidence, pinned_id, governing_horizon):
    """The F8 configuration a query computes under: pinned, or designated at G. It must be registered and must name
    exactly the rule versions this code implements, and its F6 configuration must be registered."""
    designation = None
    if pinned_id is None:
        designation = in_force(evidence.f8_designations, governing_horizon)
        if designation is None:
            raise Refused("no_designated_configuration", "no F8 configuration was designated at or before the "
                                                         "governing time; pin one (today's designation is never used)")
        pinned_id = designation.configuration_id
    row = evidence.f8_configurations.get(pinned_id)
    if row is None:
        raise Refused("configuration_not_registered", f"F8 configuration {pinned_id} is not registered")
    if row.configuration is None or not row.configuration.implemented:
        raise Refused("configuration_not_implemented", f"F8 configuration {pinned_id} names rule versions this code "
                                                       f"does not implement (it implements {IMPLEMENTED})")
    f6 = evidence.f6_configurations.get(row.configuration.f6_configuration_id)
    if f6 is None:
        raise Refused("f6_configuration_not_registered",
                      f"F6 configuration {row.configuration.f6_configuration_id} is not registered")
    if f6.configuration is None:
        raise Refused("f6_configuration_not_implemented", f"F6 configuration {f6.configuration_id} is not of the "
                                                          f"implemented F6 version set")
    return Resolved(row.configuration, designation)
