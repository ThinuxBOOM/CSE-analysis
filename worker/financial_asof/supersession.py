"""
f8.supersession.1 (docs/F8_DESIGN.md §6.3, §12; OD-2, owner-approved 2026-10-05; AC-1): source-declared supersession
only. It is a pure function of the visible set of one fact and the rule version (§6.4). Nothing is stored and nothing
is deleted.

Observation s supersedes observation o, for one ef_key, only when all four hold:
1. the same admissible ef_key (the caller passes the observations of one fact);
2. different documents;
3. available_at(s) > available_at(o), strictly, both known (f8.availability.1 at the governing horizon);
4. a source-declared basis:
   S-1  s's document is F3-classified errata_or_reissue or amendment, with document-type status confirmed or
        document_only (read in the document itself, not only in the listing title);
   S-2  some member of s carries the restated marker (the column header prints "restated");
   S-3  s and o are different bytes under the same CSE filing, and the source's own document times order them: for
        every common filing, every document-level time present for both (CDN Last-Modified against Last-Modified,
        path epoch against path epoch) is strictly later for s, and at least one such pair exists. Retrieval order
        never decides (AC-1).

Effect: o is dropped only while s is visible in the same selection. Chains follow, because every superseding s is
itself visible. Cycles are impossible, because availability strictly increases along every edge.

Never a basis: a later filing because it is later, interim against annual, unaudited against audited, a differing
comparative without the restated marker, path or filename similarity, symbol similarity, arrival order, two errata
with different values, or incomplete evidence.

Ambiguity is detected for the fact as a whole. When the evidence is partial or contradictory, the fact is flagged
ambiguous_supersession, NOTHING is dropped, and the reconciliation over every visible observation stands (§12). The
triggers are these (implementation of §12's list; docs/F8_IMPLEMENTATION.md §3):
- two_errata:           two S-1 observations whose values disagree;
- unknown_availability: a pair with a source-declared basis, or under a common filing, where either availability is
                        unknown;
- unclear_basis:        an errata/amendment document whose type status is not confirmed or document_only, strictly
                        later than o, with no other valid basis;
- unordered_versions:   two different documents under a common filing, both availabilities known, and no valid
                        supersession either way.
"""
import decimal
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..financial_truth.observations import SourceObservation, compare_members
from ..financial_truth_store import F6_DECIMAL_CONTEXT
from .times import iso
from .versions import SUPERSESSION_VERSION

S1_TYPES = ("errata_or_reissue", "amendment")
S1_STATUSES = ("confirmed", "document_only")


@dataclass(frozen=True)
class Candidate:
    """One visible observation of the fact, with what the rule reads."""
    observation: SourceObservation
    available_at: Optional[datetime]     # A-6 over the versions visible in the mode, at the governing horizon
    filings: tuple                       # the visible filings that carry its document

    @property
    def so_key(self):
        return self.observation.so_key

    @property
    def document_sha256(self):
        return self.observation.document_sha256


@dataclass(frozen=True)
class SupersessionRecord:
    superseding: str                     # so_key
    superseded: str                      # so_key
    bases: tuple                         # the bases that hold: S-1, S-2, S-3
    rule_version: str
    evidence: tuple                      # (name, value...) references to the evidence each basis read


@dataclass(frozen=True)
class Ambiguity:
    kind: str                            # two_errata | unknown_availability | unclear_basis | unordered_versions
    a: str                               # so_key (the would-be superseding observation, where there is a direction)
    b: str
    detail: tuple


@dataclass(frozen=True)
class FactSupersession:
    records: tuple                       # applied supersession records (none when ambiguous)
    superseded_by: tuple                 # (superseded so_key, (superseding so_keys...)), sorted
    ambiguities: tuple                   # empty unless the fact is ambiguous_supersession


def s1(c):
    doc = c.observation.document
    return doc.document_type in S1_TYPES and doc.document_type_status in S1_STATUSES


def s1_unclear(c):
    doc = c.observation.document
    return doc.document_type in S1_TYPES and doc.document_type_status not in S1_STATUSES


def s2(c):
    return any(m.restated for m in c.observation.members)


def common_filings(a, b):
    return tuple(sorted(set(a.filings) & set(b.filings)))


def s3(s, o, document_times):
    """S-3 with AC-1: (holds, evidence). document_times(filing, sha) -> (last_modified values, path epoch values) of
    the version's runs known at the governing horizon."""
    common = common_filings(s, o)
    if not common:
        return False, ()
    evidence = []
    for f in common:
        (lm_s, ep_s), (lm_o, ep_o) = document_times(f, s.document_sha256), document_times(f, o.document_sha256)
        pairs = 0
        for kind, later, earlier in (("cdn_last_modified", lm_s, lm_o), ("path_epoch_at", ep_s, ep_o)):
            if later and earlier:
                pairs += 1
                if not min(later) > max(earlier):
                    return False, ()
                evidence.append((kind, f, iso(min(later)), iso(max(earlier))))
        if pairs == 0:
            return False, ()
    return True, tuple(evidence)


def bases(s, o, document_times):
    """The bases on which s supersedes o, with their evidence; () when any of conditions 2-4 fails."""
    if s.document_sha256 == o.document_sha256:
        return (), ()
    if s.available_at is None or o.available_at is None or not s.available_at > o.available_at:
        return (), ()
    found: list = []
    evidence: list = [("available_at", iso(s.available_at), iso(o.available_at))]
    doc = s.observation.document
    if s1(s):
        found.append("S-1")
        evidence.append(("S-1", doc.classification_id, doc.document_type, doc.document_type_status))
    if s2(s):
        found.append("S-2")
        evidence.append(("S-2",) + tuple(sorted(str(list(m.source_key)) for m in s.observation.members if m.restated)))
    holds, ev3 = s3(s, o, document_times)
    if holds:
        found.append("S-3")
        evidence.extend(("S-3",) + e for e in ev3)
    return (tuple(found), tuple(evidence)) if found else ((), ())


def disagree(a, b):
    """Do two observations of one fact claim different values (F6.1 V8 on every member pair; an internally
    conflicting observation claims no single value)?"""
    if "internally_conflicting" in (a.observation_status, b.observation_status):
        return True
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        return any(compare_members(ma, mb).outcome == "disagree" for ma in a.members for mb in b.members)


def derive(candidates, document_times):
    """f8.supersession.1 over the visible observations of ONE fact."""
    cands = sorted(candidates, key=lambda c: c.so_key)
    edges = {}
    for s in cands:
        for o in cands:
            if s is not o:
                found, evidence = bases(s, o, document_times)
                if found:
                    edges[(s.so_key, o.so_key)] = SupersessionRecord(s.so_key, o.so_key, found, SUPERSESSION_VERSION,
                                                                     evidence)
    ambiguities = []
    for s in cands:
        for o in cands:
            if s is o or s.document_sha256 == o.document_sha256:
                continue
            declared = s1(s) or s2(s) or s1_unclear(s) or bool(common_filings(s, o))
            if not declared:
                continue
            if s.available_at is None or o.available_at is None:
                ambiguities.append(Ambiguity("unknown_availability", s.so_key, o.so_key,
                                             (iso(s.available_at), iso(o.available_at))))
            elif s1_unclear(s) and s.available_at > o.available_at and (s.so_key, o.so_key) not in edges:
                doc = s.observation.document
                ambiguities.append(Ambiguity("unclear_basis", s.so_key, o.so_key,
                                             (doc.document_type, doc.document_type_status)))
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            if a.document_sha256 == b.document_sha256:
                continue
            if (common_filings(a, b) and None not in (a.available_at, b.available_at)
                    and (a.so_key, b.so_key) not in edges and (b.so_key, a.so_key) not in edges):
                ambiguities.append(Ambiguity("unordered_versions", a.so_key, b.so_key, common_filings(a, b)))
            if s1(a) and s1(b) and disagree(a.observation, b.observation):
                ambiguities.append(Ambiguity("two_errata", a.so_key, b.so_key, ()))
    if ambiguities:
        return FactSupersession((), (), tuple(sorted(set(ambiguities), key=lambda x: (x.kind, x.a, x.b))))
    superseded = {}
    for (s, o) in sorted(edges):
        superseded.setdefault(o, []).append(s)
    return FactSupersession(tuple(edges[k] for k in sorted(edges, key=lambda k: (k[1], k[0]))),
                            tuple((o, tuple(sorted(by))) for o, by in sorted(superseded.items())), ())
