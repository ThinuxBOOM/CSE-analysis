"""
The as-of query (docs/F8_DESIGN.md §7.1, §7.2, §7.3): its inputs, the four modes, their labels, and the refusals.

Mode            information cutoff T   knowledge horizon H        governing horizon G   label
KNOWN_RECORDED  required               refused                    T                     known_recorded
KNOWN           required               refused (T governs)        T                     known
AVAILABLE       required               H >= T (default: now)      H                     reconstructed
CURRENT         refused                default: now               H                     retrospective_current

Both cutoffs are inclusive ("at or before", MA §11). A naive timestamp is refused, and F8 accepts no date as a cutoff
(times.colombo_end_of_day converts one). The query time that stands in for an omitted H is read by the database
interface (api.py) and recorded in the result. The pure layer never reads a clock.

The mode names and labels are the design's own. OD-5 leaves only the words open.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .errors import Refused
from .times import instant

KNOWN_RECORDED, KNOWN, AVAILABLE, CURRENT = "KNOWN_RECORDED", "KNOWN", "AVAILABLE", "CURRENT"
MODES = (KNOWN_RECORDED, KNOWN, AVAILABLE, CURRENT)
LABELS = {KNOWN_RECORDED: "known_recorded", KNOWN: "known", AVAILABLE: "reconstructed",
          CURRENT: "retrospective_current"}
POINT_IN_TIME_MODES = (KNOWN_RECORDED, KNOWN, AVAILABLE)       # §8.2 (F-5): CURRENT is never point-in-time
LIVE_MODES = (KNOWN_RECORDED, KNOWN)                           # §7.3, §8.2: the only modes a live analysis may use

FILTER_FIELDS = ("concept_key", "period_kind", "period_end", "duration_months", "scope", "operations", "maturity",
                 "currency")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _filter_value(name, value):
    if name == "period_end":
        if isinstance(value, datetime) or not isinstance(value, (date, str)):
            raise Refused("invalid_fact_filter", "period_end must be a date or 'YYYY-MM-DD'")
        try:
            return (value if isinstance(value, date) else date.fromisoformat(value)).isoformat()
        except ValueError:
            raise Refused("invalid_fact_filter", f"period_end {value!r} is not a date") from None
    if name == "duration_months":
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise Refused("invalid_fact_filter", "duration_months must be a positive integer, or None for an instant")
        return value
    if not isinstance(value, str) or not value:
        raise Refused("invalid_fact_filter", f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class FactFilter:
    """Which facts a query asks for (§7.1). Either exact ef_keys, or identity fields (an absent field means "all"),
    or neither (every fact of the issuer). The values are canonical: period_end is an ISO date string, and the
    pairs are sorted."""
    ef_keys: tuple = ()
    fields: tuple = ()

    @classmethod
    def of(cls, facts=None):
        if facts is None:
            return cls()
        if isinstance(facts, FactFilter):
            return facts
        if isinstance(facts, dict):
            unknown = sorted(set(facts) - set(FILTER_FIELDS))
            if unknown:
                raise Refused("invalid_fact_filter", f"unknown identity fields {unknown}")
            return cls(fields=tuple(sorted((k, _filter_value(k, v)) for k, v in facts.items())))
        if isinstance(facts, (list, tuple, set, frozenset)):
            keys = tuple(sorted(set(facts)))
            if not keys or any(not isinstance(k, str) or not _HEX64.match(k) for k in keys):
                raise Refused("invalid_fact_filter", "exact facts are ef_keys: 64 lower-case hex digits each")
            return cls(ef_keys=keys)
        raise Refused("invalid_fact_filter", f"facts must be ef_keys or identity fields, not {type(facts).__name__}")

    def matches(self, ef_key, identity):
        """Does a fact with this ef_key and identity (an f6.identity.1 EconomicFactIdentity) fall in the filter?"""
        if self.ef_keys:
            return ef_key in self.ef_keys
        for name, want in self.fields:
            got = getattr(identity, name)
            if name == "period_end":
                got = got.isoformat()
            if got != want:
                return False
        return True


@dataclass(frozen=True)
class Query:
    issuer_id: str
    facts: FactFilter
    mode: str
    information_cutoff: Optional[datetime]       # T (UTC)
    knowledge_horizon: Optional[datetime]        # H (UTC)
    f8_configuration_id: Optional[str]           # pinned; None = the designation in force at G

    @property
    def governing_horizon(self):
        """G (§7.4): T for KNOWN_RECORDED and KNOWN, H for AVAILABLE and CURRENT. It is also the evidence horizon E of
        every recomputed mode (Appendix B.1), and the horizon of KNOWN_RECORDED's flags."""
        return self.information_cutoff if self.mode in (KNOWN_RECORDED, KNOWN) else self.knowledge_horizon

    def with_horizon(self, knowledge_horizon):
        return query(issuer_id=self.issuer_id, facts=self.facts, mode=self.mode,
                     information_cutoff=self.information_cutoff, knowledge_horizon=knowledge_horizon,
                     f8_configuration_id=self.f8_configuration_id)

    def with_cutoff(self, information_cutoff):
        return query(issuer_id=self.issuer_id, facts=self.facts, mode=self.mode,
                     information_cutoff=information_cutoff, knowledge_horizon=self.knowledge_horizon,
                     f8_configuration_id=self.f8_configuration_id)


def query(*, issuer_id, facts=None, mode, information_cutoff=None, knowledge_horizon=None,
          f8_configuration_id=None):
    """A validated query. H may be left out for AVAILABLE and CURRENT here. The database interface then supplies the
    query time, and the pure layer refuses to evaluate without one."""
    if mode not in MODES:
        raise Refused("unknown_mode", f"{mode!r} is not one of {MODES}")
    if not isinstance(issuer_id, str) or not _UUID.match(issuer_id):
        raise Refused("identity_unresolved", f"{issuer_id!r} is not an F5 issuer_id. Tickers and names are resolved "
                                             f"upstream, never inferred by F8 (§10)")
    t = None if information_cutoff is None else instant(information_cutoff, "information_cutoff")
    h = None if knowledge_horizon is None else instant(knowledge_horizon, "knowledge_horizon")
    if mode == CURRENT and t is not None:
        raise Refused("cutoff_not_allowed", "CURRENT refuses an information cutoff: it can never answer a "
                                            "point-in-time question (§7.3)")
    if mode != CURRENT and t is None:
        raise Refused("cutoff_required", f"{mode} needs an information cutoff T")
    if mode in (KNOWN_RECORDED, KNOWN) and h is not None:
        raise Refused("horizon_not_allowed", f"{mode} takes no knowledge horizon: its horizon is T")
    if mode == AVAILABLE and h is not None and t is not None and h < t:
        raise Refused("horizon_before_cutoff", "AVAILABLE needs H >= T")
    if f8_configuration_id is not None and (not isinstance(f8_configuration_id, str)
                                            or not _HEX64.match(f8_configuration_id)):
        raise Refused("invalid_configuration", "an f8_configuration_id is 64 lower-case hex digits")
    return Query(issuer_id, FactFilter.of(facts), mode, t, h, f8_configuration_id)
