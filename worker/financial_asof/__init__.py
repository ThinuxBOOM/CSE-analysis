"""
F8: availability, supersession and point-in-time financial views (docs/F8_DESIGN.md, revision 3, frozen and accepted;
implementation notes in docs/F8_IMPLEMENTATION.md).

    F6.4 (and F1 / F3 / F5): immutable financial truth and evidence history
             |  read-only
    F8: availability / supersession / as-of selection / point-in-time views   (versioned rules, pure functions)
             |
    F7 / features / analytics / backtesting / ML                              (choose the mode and the cutoffs)

F8 is an interpretation and selection layer over immutable evidence:
- It never writes, mutates or redefines an F1 to F6.4 row, and never reads the mutable report_filings,
  report_discovery_runs or companies for history.
- It re-implements no F6.3 rule. It calls F6.3's select_runs and reconcile_fact, and F6.4's frozen loader and codec.
- Its only writes are its own configuration rows (migration 0017).
- No CSE request and no network: nothing here imports a network library.

Modules
  versions      the rule versions f8.selection.1, f8.availability.1, f8.supersession.1, f8.knowledge.1
  errors        Refused (a query F8 will not answer) and EvidenceError (evidence that breaks a frozen invariant)
  times         aware instants, the Colombo day (colombo_end_of_day)
  query         the modes, labels, cutoffs and fact filter, and their refusals
  model         the immutable evidence rows and Evidence
  config        the content-addressed F8 configuration; the designation in force at a time
  metadata      F1 metadata as known at a horizon (F1's own merge; ties never broken)
  availability  f8.availability.1 (A-1 to A-7)
  knowledge     f8.knowledge.1 (known_at)
  supersession  f8.supersession.1 (S-1 to S-3; ambiguity)
  selection     the four modes (§7.3 to §7.7): evaluate(evidence, query) -> AsOfResult
  result        AsOfResult, FactView and the result_hash
  pit           point-in-time interfaces: timeline, dataset, require_point_in_time, require_live
  explain       the provenance chain and the optional audit section (outside result_hash)
  loader        PostgreSQL -> Evidence (read-only; append-only tables only)
  store         f8_configurations (insert-if-absent) and f8_designations (owner path)
  api           as_of / timeline / availability / explain / register_configuration / designate against PostgreSQL
"""
from .errors import EvidenceError, Refused
from .pit import dataset, require_live, require_point_in_time, timeline
from .query import (AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED, LABELS, LIVE_MODES, MODES, POINT_IN_TIME_MODES,
                    FactFilter, Query)
from .result import AsOfResult, FactView
from .selection import evaluate
from .times import colombo_end_of_day
from .versions import (AVAILABILITY_VERSION, IMPLEMENTED, KNOWLEDGE_VERSION, SELECTION_VERSION, SUPERSESSION_VERSION,
                       RuleVersions)
