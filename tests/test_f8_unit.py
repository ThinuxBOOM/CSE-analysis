"""
F8 semantics on synthetic evidence (docs/F8_DESIGN.md §15: T-1 to T-40, I-1 to I-12; implementation notes in
docs/F8_IMPLEMENTATION.md). Pure: no database, no network. Every time is chosen (tests/f8_factories.World), so a filing
published in 2023 can be backfilled in 2026.

This module covers:
- the temporal model and the availability rules (§4, §5);
- knowledge time (§4.2);
- the modes (§7.3);
- supersession (§6);
- the F6.3 / F6.4 integration (§7.4).

Leakage, reproducibility and the point-in-time interfaces are in tests/test_f8_leakage.py. The static boundary checks
are in tests/test_f8_static.py.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f8_factories import (CONFIG, ISSUER, OTHER_ISSUER, RUN_VERSIONS, Doc, World, at, epoch_ms,  # noqa: E402
                          revenue_doc)
from worker import report_discovery as rd  # noqa: E402
from worker.financial_asof import availability as av  # noqa: E402
from worker.financial_asof import config as f8config  # noqa: E402
from worker.financial_asof import knowledge, metadata, query, selection, supersession  # noqa: E402
from worker.financial_asof.errors import EvidenceError, Refused  # noqa: E402
from worker.financial_asof.query import AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED  # noqa: E402
from worker.financial_asof.times import colombo_end_of_day  # noqa: E402
from worker.financial_truth import reconciliation  # noqa: E402

UTC = timezone.utc
FEED, LISTING = rd.FEED_ENDPOINT, rd.LISTING_ENDPOINT
PUB = at("2023-05-15T09:30")                  # CSE upload instant (Colombo local)
BACKFILL = at("2026-01-12T10:00")             # when the Phase 2 backfill recorded it
NOW = at("2026-10-01T00:00")
US = timedelta(microseconds=1)


def evaluate(world, mode, **kw):
    return selection.evaluate(world.evidence(), world.query(mode, **kw))


def states(result):
    return {f.ef_key: f.state for f in result.facts}


def shown(result, ef_key):
    f = result.fact(ef_key)
    return [] if f is None else [o.so_key for o in f.visible]


def backfilled(world, filing, n, *, published=PUB, raw="1,000", recorded=BACKFILL, endpoint=LISTING, **doc):
    """A filing published at `published`, first observed by the system (F1) at `recorded` - 2 h, its issuer decided
    at `recorded` - 1 h, processed (F3 + F5) at `recorded` and validated then (F6.4)."""
    world.listing(filing, recorded - timedelta(hours=2), uploaded=published, endpoint=endpoint)
    d = world.decision(filing, recorded - timedelta(hours=1))
    return world.process(revenue_doc(filing, n, raw, **doc), recorded, decision=d, uploaded_at=published)


@pytest.fixture
def world():
    w = World()
    w.configure(at("2020-01-01"))
    return w


# ------------------------------------------------------------------------------------------------ §4.5, §7.2 (T-16)

def test_t16_colombo_end_of_day_naive_and_date_cutoffs_refused():
    assert colombo_end_of_day(date(2024, 6, 30)) == datetime(2024, 6, 30, 18, 30, tzinfo=UTC)
    assert colombo_end_of_day(date(2024, 6, 30)) == at("2024-07-01T00:00")
    for bad in (datetime(2024, 6, 30, 12, 0), "2024-06-30", "2024-06-30T12:00"):     # naive: no zone is assumed
        with pytest.raises(Refused) as e:
            query.query(issuer_id=ISSUER, mode=KNOWN, information_cutoff=bad)
        assert e.value.reason == "naive_timestamp"
    with pytest.raises(Refused):
        colombo_end_of_day(datetime(2024, 6, 30, tzinfo=UTC))                           # a datetime is not a date
    with pytest.raises(Refused) as e:
        query.query(issuer_id=ISSUER, mode=KNOWN, information_cutoff=date(2024, 6, 30))
    assert e.value.reason == "not_a_timestamp"


def test_query_contract_refusals():
    cases = {
        "cutoff_not_allowed": dict(mode=CURRENT, information_cutoff=NOW),
        "cutoff_required": dict(mode=KNOWN),
        "horizon_not_allowed": dict(mode=KNOWN, information_cutoff=NOW, knowledge_horizon=NOW),
        "horizon_before_cutoff": dict(mode=AVAILABLE, information_cutoff=NOW, knowledge_horizon=NOW - US),
        "unknown_mode": dict(mode="LATEST", information_cutoff=NOW),
        "identity_unresolved": dict(mode=KNOWN, information_cutoff=NOW, issuer_id="COMB.N0000"),
        "invalid_fact_filter": dict(mode=KNOWN, information_cutoff=NOW, facts={"concept": "revenue"}),
        "invalid_configuration": dict(mode=KNOWN, information_cutoff=NOW, f8_configuration_id="abc"),
    }
    for reason, kw in cases.items():
        with pytest.raises(Refused) as e:
            query.query(**dict({"issuer_id": ISSUER}, **kw))
        assert e.value.reason == reason, (reason, e.value)
    q = query.query(issuer_id=ISSUER, mode=AVAILABLE, information_cutoff=NOW, knowledge_horizon=NOW)
    assert q.governing_horizon == NOW                                   # H = T is allowed (inclusive)


# ------------------------------------------------------------------------------------------------ availability (§5)

def test_a3_date_only_values_count_as_the_end_of_the_colombo_day():
    w = World()
    # RDV P-32: TILE 32216's feed value, and an /api/financials epoch at Colombo midnight
    w.listing(32216, at("2026-09-24T19:00"), endpoint=FEED, raw_uploaded="07 Feb 2019 12:00:00 AM")
    w.listing(32532, at("2026-09-24T19:00"), raw_uploaded=epoch_ms(at("2019-03-05T00:00")))
    w.listing(32533, at("2026-09-24T19:00"), raw_uploaded=epoch_ms(at("2019-03-05T00:00")) + 1)   # 1 ms later
    ev = w.evidence()
    tile = av.filing_availability(ev, 32216, NOW)
    assert (tile.at, tile.precision) == (at("2019-02-08T00:00"), "day")       # never placed at midnight
    comb = av.filing_availability(ev, 32532, NOW)
    assert (comb.at, comb.precision) == (at("2019-03-06T00:00"), "day")
    instant = av.filing_availability(ev, 32533, NOW)
    assert (instant.at, instant.precision) == (at("2019-03-05T00:00") + timedelta(milliseconds=1), "instant")


def test_t26_authorization_later_than_upload_is_the_availability(world):
    world.listing(101, BACKFILL - timedelta(hours=2), uploaded=PUB, authorized=PUB + timedelta(hours=4))
    d = world.decision(101, BACKFILL - timedelta(hours=1))
    p = world.process(revenue_doc(101, 1), BACKFILL, decision=d, uploaded_at=PUB)
    fa = av.filing_availability(world.evidence(), 101, NOW)
    assert fa.at == PUB + timedelta(hours=4) and fa.basis == ("authorization",)
    ef = p.ef_keys[0]
    before = evaluate(world, AVAILABLE, cutoff=PUB + timedelta(hours=4) - US, horizon=NOW)
    assert before.facts == ()                                            # never the earlier upload instant
    assert shown(evaluate(world, AVAILABLE, cutoff=PUB + timedelta(hours=4), horizon=NOW), ef) == [p.so().so_key]


def test_t23_t27_backward_moved_timestamp_never_makes_anything_earlier(world):
    """MA §57: a later edit moving the publication time backward; I-7: availability never decreases with H."""
    world.listing(101, at("2023-05-15T10:00"), uploaded=PUB)
    world.listing(101, at("2023-06-01T10:00"), uploaded=PUB - timedelta(days=5))   # CSE moved it backward
    world.listing(101, at("2023-07-01T10:00"), uploaded=PUB + timedelta(days=2))   # and later forward
    d = world.decision(101, at("2023-05-15T10:30"))
    world.process(revenue_doc(101, 1), at("2023-05-15T11:00"), decision=d, uploaded_at=PUB)
    ev = world.evidence()
    horizons = [at("2023-05-15T10:00") + timedelta(hours=h) for h in range(0, 24 * 60, 7)]
    series = [av.version_availability(ev, 101, revenue_doc(101, 1).sha, h).at for h in horizons
              if h >= at("2023-05-15T11:00")]
    assert all(a <= b for a, b in zip(series, series[1:]))                         # I-7 / T-27
    assert av.version_availability(ev, 101, revenue_doc(101, 1).sha, at("2023-06-02")).at == PUB    # T-23
    assert av.version_availability(ev, 101, revenue_doc(101, 1).sha, NOW).at == PUB + timedelta(days=2)
    assert "availability_evidence_changed" in av.filing_availability(ev, 101, NOW).flags


def unknown_version(world, filing, raw="1,000", end="2023-03-31"):
    """A filing whose LATER document version has no document-level time (A-5): availability_unknown. (A filing with
    no usable CSE instant at all has a null F1 uploaded_at, so F6.1 admits nothing and no observation exists.)"""
    world.listing(filing, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(filing, BACKFILL - timedelta(hours=1))
    world.process(revenue_doc(filing, 900 + filing, "77", concept="profit_before_tax", label="Profit before tax",
                              end=end), BACKFILL, decision=d, uploaded_at=PUB, retrieved=BACKFILL - timedelta(days=1))
    return world.process(revenue_doc(filing, filing, raw, end=end), BACKFILL + timedelta(days=1), decision=d,
                         uploaded_at=PUB, retrieved=BACKFILL + timedelta(hours=23))


def test_t28_no_usable_evidence_is_unknown_and_no_system_time_stands_in(world):
    for filing, raw in ((101, None), (102, "not a date")):
        world.listing(filing, BACKFILL - timedelta(hours=2), raw_uploaded=raw)
        d = world.decision(filing, BACKFILL - timedelta(hours=1))
        world.process(revenue_doc(filing, filing), BACKFILL, decision=d, uploaded_at=None,
                      retrieved=BACKFILL - timedelta(minutes=5), last_modified=PUB, path_epoch=PUB)
    ev = world.evidence()
    for filing in (101, 102):                                                     # A-4
        v = av.version_availability(ev, filing, revenue_doc(filing, filing).sha, NOW)
        assert v.at is None and v.basis == "no_cse_instant" and "availability_unknown" in v.flags
    later = unknown_version(world, 103)                                           # A-5 without document time
    v = av.version_availability(world.evidence(), 103, later.run.document_sha256, NOW)
    assert v.at is None and v.basis == "later_version_without_document_time"
    ef = later.so().ef_key
    assert evaluate(world, AVAILABLE, cutoff=NOW, horizon=NOW).fact(ef) is None     # excluded from AVAILABLE
    fact = evaluate(world, KNOWN, cutoff=NOW + timedelta(days=1)).fact(ef)         # kept in KNOWN, flagged
    assert fact.state == "single_source" and "availability_unknown" in fact.visible[0].flags
    assert fact.visible[0].availability.available_at is None                      # no system time stands in


def test_t29_contradictory_evidence_is_kept_and_flagged_never_repaired(world):
    world.listing(101, at("2023-05-15T10:00"), uploaded=PUB, endpoint=FEED)
    world.listing(101, at("2023-05-15T10:05"), uploaded=PUB + timedelta(seconds=3))           # listing: 3 s later
    d = world.decision(101, at("2023-05-15T10:30"))
    world.process(revenue_doc(101, 1), at("2023-05-15T11:00"), decision=d, uploaded_at=PUB + timedelta(seconds=3),
                  last_modified=PUB + timedelta(days=1))                                     # re-uploaded later
    v = av.version_availability(world.evidence(), 101, revenue_doc(101, 1).sha, NOW)
    assert v.at == PUB + timedelta(seconds=3)                                       # A-2 over every value
    assert set(v.flags) == {"availability_sources_disagree", "last_modified_after_upload"}
    assert [i.value for i in v.filing.instants] == [PUB, PUB + timedelta(seconds=3)]          # every value kept
    flags = evaluate(world, KNOWN, cutoff=NOW).facts[0].visible[0].flags                    # carried by the result
    assert {"availability_sources_disagree", "last_modified_after_upload"} <= set(flags)
    # within one source, a changed value (and only that) is availability_evidence_changed
    world.listing(101, at("2023-05-16T10:00"), uploaded=PUB + timedelta(seconds=4))
    assert "availability_evidence_changed" in av.filing_availability(world.evidence(), 101, NOW).flags
    assert "availability_evidence_changed" not in av.filing_availability(world.evidence(), 101,
                                                                         at("2023-05-15T23:00")).flags


def test_a5_later_versions_and_the_base_version():
    sha1, sha2 = revenue_doc(201, 1).sha, revenue_doc(201, 2).sha

    def build(retrieved1, retrieved2, lm2=at("2023-06-01T08:00"), epoch2=at("2023-06-01T07:59")):
        w = World()
        w.listing(201, at("2026-01-01"), uploaded=PUB)
        d = w.decision(201, at("2026-01-01T01:00"))
        w.process(revenue_doc(201, 1), at("2026-01-10"), decision=d, uploaded_at=PUB, retrieved=retrieved1,
                  last_modified=PUB + timedelta(minutes=5))
        w.process(revenue_doc(201, 2), at("2026-02-10"), decision=d, uploaded_at=PUB, retrieved=retrieved2,
                  last_modified=lm2, path_epoch=epoch2)
        return w.evidence()

    ev = build(at("2026-01-09"), at("2026-02-09"))
    v1, v2 = (av.version_availability(ev, 201, s, NOW) for s in (sha1, sha2))
    assert (v1.role, v1.at, v1.basis) == ("base", PUB, "filing_instants")
    assert (v2.role, v2.at, v2.basis, v2.precision) == ("later", at("2023-06-01T08:00"), "later_version", "instant")
    # before the second version was known, the first was the filing's only version: its availability never moves
    assert av.version_availability(ev, 201, sha1, at("2026-01-20")).at == PUB
    # a later version without any document-level time is unknown
    ev = build(at("2026-01-09"), at("2026-02-09"), lm2=None, epoch2=None)
    v2 = av.version_availability(ev, 201, sha2, NOW)
    assert v2.at is None and v2.basis == "later_version_without_document_time"
    # no retrieval time for a version, or tied first retrievals: no base (both take the later-version rule)
    for r1, r2 in ((None, at("2026-02-09")), (at("2026-01-09"), at("2026-01-09"))):
        ev = build(r1, r2)
        roles = {av.version_availability(ev, 201, s, NOW).role for s in (sha1, sha2)}
        assert roles == {"unordered"}
        assert av.version_availability(ev, 201, sha1, NOW).at == PUB + timedelta(minutes=5)   # never earlier


def test_a6_same_bytes_under_two_filings_take_the_earliest_availability(world):
    doc = revenue_doc(301, 7)
    world.listing(301, at("2023-05-15T10:00"), uploaded=PUB)
    world.listing(302, at("2025-01-10T10:00"), uploaded=at("2025-01-10T09:00"))
    d1, d2 = world.decision(301, at("2023-05-15T10:30")), world.decision(302, at("2025-01-10T10:30"))
    p1 = world.process(doc, at("2023-05-15T11:00"), decision=d1, uploaded_at=PUB)
    doc2 = revenue_doc(302, 7)                                              # the same bytes under another filing
    p2 = world.process(doc2, at("2025-01-10T11:00"), decision=d2, uploaded_at=at("2025-01-10T09:00"))
    assert doc.sha == doc2.sha
    r = evaluate(world, CURRENT, horizon=NOW)
    (fact,) = r.facts
    assert fact.f6_result.document_count == 1 and fact.state == "single_source"      # one document (T-8)
    (o,) = fact.visible
    assert o.availability.available_at == PUB.isoformat()                            # the earliest of the two
    assert o.so_key == p2.so().so_key                                                # D-6: the newest run
    assert "same_document_multiple_filings" in fact.f6_result.annotations
    assert p1.so().ef_key == p2.so().ef_key


def test_system_times_are_never_availability(world):
    """first-seen, observation, retrieval and recorded times never stand in for CSE's own instants (A-1)."""
    world.listing(101, at("2023-04-01T10:00"), uploaded=PUB)                 # observed before the upload instant
    d = world.decision(101, at("2023-04-01T10:30"))
    world.process(revenue_doc(101, 1), at("2023-04-01T11:00"), decision=d, uploaded_at=PUB,
                  retrieved=at("2023-04-01T10:45"))
    ef = selection.evaluate(world.evidence(), world.query(CURRENT, horizon=NOW)).facts[0].ef_key
    for mode, kw in ((AVAILABLE, dict(cutoff=at("2023-05-01"), horizon=NOW)), (KNOWN, dict(cutoff=at("2023-05-01")))):
        r = evaluate(world, mode, **kw)
        assert shown(r, ef) == []                                            # neither observed_at nor recorded_at
    k = evaluate(world, KNOWN, cutoff=at("2023-05-01"))
    assert [e.reason for e in k.fact(ef).excluded] == ["not_yet_available"]
    later = evaluate(world, KNOWN, cutoff=PUB).fact(ef)
    assert later.state == "single_source" and "available_after_known:instant" in later.visible[0].flags


# ------------------------------------------------------------------------------------------------ knowledge (§4.2)

def test_known_at_is_the_latest_of_the_six_recorded_times(world):
    world.listing(101, at("2026-01-10T10:00"), uploaded=PUB)
    d = world.decision(101, at("2026-01-10T11:00"))
    p = world.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB,
                      classified=at("2026-01-12T09:00"), validated=at("2026-01-13T10:00"))
    stored = world.stored[0]
    k = knowledge.known_at(world.evidence(), stored)
    assert k.at == at("2026-01-13T10:00") and not k.missing
    assert [t[0] for t in k.terms] == ["f1_first_observation", "classification", "issuer_decision", "f5_run",
                                       "validation_run", "source_observation"]
    assert {s[0] for s in k.set_by} == {"validation_run", "source_observation"}
    # a decision recorded after the validation run that used it (synthetic) sets known_at
    w = World()
    w.listing(101, at("2026-01-10T10:00"), uploaded=PUB)
    late = w.decision(101, at("2026-02-01T00:00"))
    w.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=late, uploaded_at=PUB)
    k = knowledge.known_at(w.evidence(), w.stored[0])
    assert k.at == at("2026-02-01T00:00") and k.set_by == (("issuer_decision", "filing_issuer_links", str(late.id)),)
    assert p.vr.recorded_at == at("2026-01-13T10:00")
    # F1's observed_at comes from the F1 run's clock (AC-4): a sighting stamped after the database times sets it
    s = World()
    first = s.listing(101, at("2026-01-12T12:00"), uploaded=PUB)
    d = s.decision(101, at("2026-01-12T09:00"))
    s.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB)
    k = knowledge.known_at(s.evidence(), s.stored[0])
    assert k.at == at("2026-01-12T12:00") and k.set_by == (("f1_first_observation", "report_filing_observations",
                                                            first.id),)
    s.configure(at("2020-01-01"))
    assert selection.evaluate(s.evidence(), s.query(KNOWN, cutoff=at("2026-01-12T11:00"))).facts == ()
    assert selection.evaluate(s.evidence(), s.query(KNOWN, cutoff=at("2026-01-12T12:00"))).facts


def test_visibility_requires_known_at_even_when_every_other_filter_passes():
    """§7.3: an observation is visible only once its whole chain is known. Here the F5 run, the validation run, the
    issuer decision and the F1 metadata are all known at T, and only the F3 classification is recorded after T
    (synthetic skew). Step 3's known_at test alone must hide it."""
    w = World()
    w.configure(at("2020-01-01"))
    w.listing(101, at("2026-01-12T08:00"), uploaded=PUB)
    d = w.decision(101, at("2026-01-12T09:00"))
    p = w.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB,
                  classified=at("2026-01-12T12:00"))
    assert knowledge.known_at(w.evidence(), w.stored[0]).at == at("2026-01-12T12:00")
    hidden = selection.evaluate(w.evidence(), w.query(KNOWN, cutoff=at("2026-01-12T11:00")))
    assert hidden.facts == () and p.so().so_key not in hidden.envelope()          # outside Ω: not even excluded
    shown_at = selection.evaluate(w.evidence(), w.query(KNOWN, cutoff=at("2026-01-12T12:00")))
    assert [o.so_key for o in shown_at.facts[0].visible] == [p.so().so_key]
    current = selection.evaluate(w.evidence(), w.query(CURRENT, horizon=at("2026-01-12T11:00")))
    assert current.facts == ()                                                   # CURRENT too: known by H only


def test_known_at_with_a_missing_link_is_never_known(world):
    p = backfilled(world, 101, 1)
    world.classifications.clear()                                            # the F3 row is missing
    k = knowledge.known_at(world.evidence(), world.stored[0])
    assert k.at is None and k.missing == ("classification",)
    for mode, kw in ((KNOWN, dict(cutoff=NOW)), (AVAILABLE, dict(cutoff=NOW, horizon=NOW)), (CURRENT,
                                                                                               dict(horizon=NOW))):
        assert evaluate(world, mode, **kw).facts == ()                       # outside every recomputed set
    assert p.ef_keys


def test_t15_exact_cutoff_boundaries_on_both_clocks(world):
    p = backfilled(world, 101, 1)
    ef, so = p.ef_keys[0], p.so().so_key
    assert shown(evaluate(world, AVAILABLE, cutoff=PUB, horizon=NOW), ef) == [so]            # available_at = T
    assert evaluate(world, AVAILABLE, cutoff=PUB - US, horizon=NOW).facts == ()              # T - 1 microsecond
    assert shown(evaluate(world, KNOWN, cutoff=BACKFILL), ef) == [so]                         # known_at = T
    assert evaluate(world, KNOWN, cutoff=BACKFILL - US).facts == ()


# ------------------------------------------------------------------------------------------------ modes (§7.3)

def test_t1_original_filing_visible_in_every_mode_once_available_or_known(world):
    world.listing(101, PUB + timedelta(minutes=10), uploaded=PUB)
    d = world.decision(101, PUB + timedelta(minutes=20))
    p = world.process(revenue_doc(101, 1), PUB + timedelta(minutes=30), decision=d, uploaded_at=PUB)
    world.batch(PUB + timedelta(hours=1), [p])
    ef = p.ef_keys[0]
    for mode, kw in ((KNOWN_RECORDED, dict(cutoff=NOW)), (KNOWN, dict(cutoff=NOW)),
                     (AVAILABLE, dict(cutoff=NOW, horizon=NOW)), (CURRENT, dict(horizon=NOW))):
        r = evaluate(world, mode, **kw)
        assert r.fact(ef).state == "single_source" and r.label == query.LABELS[mode], mode


def test_t4_t5_t6_late_discovery(world):
    """Published 2023, first discovered and backfilled 2026 (Appendix B case 1)."""
    p = backfilled(world, 101, 1)
    ef = p.ef_keys[0]
    for cutoff in (at("2023-12-31"), at("2024-06-30"), BACKFILL - US):
        assert evaluate(world, KNOWN, cutoff=cutoff).facts == ()            # T-4: invisible, not even mentioned
    r = evaluate(world, AVAILABLE, cutoff=at("2023-12-31"), horizon=NOW)    # T-6: published before the cutoff
    assert r.label == "reconstructed" and r.knowledge_horizon == NOW.isoformat() and shown(r, ef)
    assert evaluate(world, AVAILABLE, cutoff=PUB - US, horizon=NOW).facts == ()         # T-5: not available yet


def test_t7_published_after_the_cutoff(world):
    p = backfilled(world, 101, 1, published=at("2024-02-01T09:00"))
    ef = p.ef_keys[0]
    assert evaluate(world, AVAILABLE, cutoff=at("2023-12-31"), horizon=NOW).facts == ()
    assert evaluate(world, KNOWN, cutoff=at("2023-12-31")).facts == ()
    assert shown(evaluate(world, CURRENT, horizon=NOW), ef)


def test_t17_current_uses_later_restatements_and_refuses_a_cutoff(world):
    old = backfilled(world, 101, 1, raw="2,000", end="2023-03-31", months=12, doc_type="annual_report",
                     published=at("2023-05-30T09:00"))
    new = backfilled(world, 102, 2, raw="2,100", end="2023-03-31", months=12, doc_type="annual_report",
                     published=at("2024-05-30T09:00"), role="comparative", restated=True)
    ef = old.ef_keys[0]
    assert new.ef_keys == [ef]
    cur = evaluate(world, CURRENT, horizon=NOW)
    assert shown(cur, ef) == [new.so().so_key] and cur.fact(ef).state == "single_source"
    assert [e.reason for e in cur.fact(ef).excluded] == ["superseded_by"]
    hist = evaluate(world, AVAILABLE, cutoff=at("2024-01-01"), horizon=NOW)
    assert shown(hist, ef) == [old.so().so_key]
    with pytest.raises(Refused) as e:
        world.query(CURRENT, cutoff=at("2024-01-01"), horizon=NOW)
    assert e.value.reason == "cutoff_not_allowed"


def test_t18_historical_modes_never_use_a_version_unavailable_or_unknown_at_t(world):
    a = backfilled(world, 101, 1, published=at("2023-05-15T09:00"))
    b = backfilled(world, 102, 2, published=at("2023-08-15T09:00"), raw="1,000")
    c = unknown_version(world, 103)
    ef = a.ef_keys[0]
    r = evaluate(world, AVAILABLE, cutoff=at("2023-06-30"), horizon=NOW)
    assert shown(r, ef) == [a.so().so_key]                                   # b unavailable, c unknown
    k = evaluate(world, KNOWN, cutoff=at("2023-06-30"))
    assert k.facts == ()                                                     # nothing known yet
    k = evaluate(world, KNOWN, cutoff=NOW)
    assert sorted(shown(k, ef)) == sorted([a.so().so_key, b.so().so_key, c.so().so_key])     # unknown: held
    assert b.so().ef_key == c.so().ef_key == ef


def test_designation_in_force_at_the_governing_time_never_today(world):
    p = backfilled(world, 101, 1)
    other = reconciliation.ReconciliationConfiguration(
        accepted_f3={(RUN_VERSIONS["classifier_version"], RUN_VERSIONS["text_extractor"]), ("f3.other", "x")},
        accepted_f4=CONFIG.accepted_f4, accepted_f5=CONFIG.accepted_f5)
    second = world.configure(at("2026-03-01"), configuration=other)
    first = f8config.F8Configuration(CONFIG.configuration_id).f8_configuration_id
    assert evaluate(world, KNOWN, cutoff=at("2026-02-01")).f8_configuration_id == first
    assert evaluate(world, KNOWN, cutoff=at("2026-03-01")).f8_configuration_id == second
    assert evaluate(world, AVAILABLE, cutoff=at("2024-01-01"), horizon=at("2026-02-01")).f8_configuration_id == first
    fresh = World()
    fresh.configure(at("2026-03-01"))
    backfilled(fresh, 101, 1)
    with pytest.raises(Refused) as e:                                        # nothing designated by T: pin one
        evaluate(fresh, KNOWN, cutoff=at("2026-02-01"))
    assert e.value.reason == "no_designated_configuration"
    pinned = evaluate(fresh, KNOWN, cutoff=at("2026-02-01"), pinned=first)
    assert pinned.f8_configuration_id == first and p.ef_keys


def test_pinned_configuration_must_be_registered_and_implemented(world):
    backfilled(world, 101, 1)
    with pytest.raises(Refused) as e:
        evaluate(world, KNOWN, cutoff=NOW, pinned="a" * 64)
    assert e.value.reason == "configuration_not_registered"
    future = f8config.F8Configuration(CONFIG.configuration_id, selection_version="f8.selection.2")
    world.register_f8(future, at("2026-05-01"))
    with pytest.raises(Refused) as e:
        evaluate(world, KNOWN, cutoff=NOW, pinned=future.f8_configuration_id)
    assert e.value.reason == "configuration_not_implemented"


def test_m4_is_evaluated_as_of_the_horizon_never_the_current_input_set(world):
    """An issuer decision and an F1 metadata edit recorded later never change what was canonical at T."""
    world.listing(101, at("2026-01-10T08:00"), uploaded=PUB)
    d1 = world.decision(101, at("2026-01-10T09:00"))
    p = world.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d1, uploaded_at=PUB)
    world.listing(101, at("2026-03-01T08:00"), uploaded=PUB + timedelta(minutes=1))          # CSE edited it
    d2 = world.decision(101, at("2026-03-01T09:00"))                                          # and a new decision
    p2 = world.validate(p.run, revenue_doc(101, 1), at("2026-03-01T10:00"), decision=d2,
                        uploaded_at=PUB + timedelta(minutes=1))
    ef = p.ef_keys[0]
    assert shown(evaluate(world, KNOWN, cutoff=at("2026-02-01")), ef) == [p.so().so_key]
    assert shown(evaluate(world, KNOWN, cutoff=at("2026-03-01T10:00")), ef) == [p2.so().so_key]
    # between the new inputs and their validation run: no canonical run at T, and no fallback to the old one
    assert evaluate(world, KNOWN, cutoff=at("2026-03-01T09:30")).facts == ()


def test_t35_metadata_version_tie_hides_the_dependent_observations(world):
    observed = at("2026-01-10T08:00")
    world.listing(101, observed, uploaded=PUB, text="Interim A")                         # the same id twice in
    world.listing(101, observed, uploaded=PUB + timedelta(minutes=1), text="Interim B")  # one response
    d = world.decision(101, at("2026-01-10T09:00"))
    p = world.process(revenue_doc(101, 1), BACKFILL, decision=d, uploaded_at=PUB)
    ev = world.evidence()
    assert metadata.as_of(ev, 101, NOW).tie
    ef = p.ef_keys[0]
    for order in (lambda xs: xs, lambda xs: list(reversed(xs))):
        r = selection.evaluate(world.evidence(order=order), world.query(KNOWN, cutoff=NOW))
        fact = r.fact(ef)
        assert fact.state == "none" and fact.visible == ()
        assert [e.reason for e in fact.excluded] == ["metadata_version_tie"]
        assert fact.counts == (("metadata_version_tie", 1),) and fact.flags == ("metadata_version_tie",)   # §12
    # a tie that does not change uploaded_at does not matter
    w = World()
    w.configure(at("2020-01-01"))
    w.listing(101, observed, uploaded=PUB, text="Interim A")
    w.listing(101, observed, uploaded=PUB, text="Interim B")
    d = w.decision(101, at("2026-01-10T09:00"))
    p = w.process(revenue_doc(101, 1), BACKFILL, decision=d, uploaded_at=PUB)
    assert not metadata.as_of(w.evidence(), 101, NOW).tie
    assert shown(selection.evaluate(w.evidence(), w.query(KNOWN, cutoff=NOW)), ef) == [p.so().so_key]


def test_t35_available_after_known_day_and_instant_precision(world):
    """Appendix B case 8: a date-only filing seen during its own publication day is held to the end of the day."""
    day = at("2023-05-15T00:00")
    world.listing(101, at("2023-05-15T10:00"), raw_uploaded=epoch_ms(day))
    d = world.decision(101, at("2023-05-15T10:30"))
    p = world.process(revenue_doc(101, 1), at("2023-05-15T11:00"), decision=d, uploaded_at=day)
    ef, eod = p.ef_keys[0], colombo_end_of_day(date(2023, 5, 15))
    mid = evaluate(world, KNOWN, cutoff=at("2023-05-15T15:00")).fact(ef)
    assert mid.state == "none" and [e.reason for e in mid.excluded] == ["not_yet_available"]
    assert mid.counts == (("not_yet_available", 1),)
    after = evaluate(world, KNOWN, cutoff=eod).fact(ef)
    assert after.state == "single_source" and "available_after_known:day" in after.visible[0].flags
    assert evaluate(world, KNOWN, cutoff=eod - US).fact(ef).state == "none"


# ------------------------------------------------------------------------------------------------ KNOWN_RECORDED (§7.3, I-2r)

def test_t38_known_recorded_returns_the_stored_record_unchanged_and_flags_it(world):
    day = at("2023-05-15T00:00")
    world.listing(101, at("2023-05-15T10:00"), raw_uploaded=epoch_ms(day))
    d = world.decision(101, at("2023-05-15T10:30"))
    p = world.process(revenue_doc(101, 1), at("2023-05-15T11:00"), decision=d, uploaded_at=day)
    b = world.batch(at("2023-05-15T12:00"), [p])
    r = evaluate(world, KNOWN_RECORDED, cutoff=at("2023-05-15T13:00"))
    (fact,) = r.facts
    assert fact.f6_result is b.records[0].result and fact.state == "single_source"      # unchanged (F-3)
    assert "available_after_known:day" in fact.visible[0].flags                         # I-2r: flagged, kept
    assert r.recorded_batch.batch_id == b.batch_id and r.label == "known_recorded"
    assert evaluate(world, KNOWN_RECORDED, cutoff=at("2023-05-15T12:00") - US).facts == ()   # before the batch


def test_known_recorded_uses_the_batch_in_force_at_t_under_the_t9_designation_at_t(world):
    a = backfilled(world, 101, 1, raw="1,000")
    b1 = world.batch(at("2026-01-13"), [a])
    c = backfilled(world, 102, 2, raw="1,000", recorded=at("2026-02-01T10:00"))
    b2 = world.batch(at("2026-02-02"), [a, c])
    ef = a.ef_keys[0]
    r1 = evaluate(world, KNOWN_RECORDED, cutoff=at("2026-01-20"))
    r2 = evaluate(world, KNOWN_RECORDED, cutoff=at("2026-02-03"))
    assert (r1.recorded_batch.sequence, r1.fact(ef).state) == (1, "single_source")
    assert (r2.recorded_batch.sequence, r2.fact(ef).state) == (2, "corroborated")
    assert b1.sequence == 1 and b2.sequence == 2
    # the F8 configuration must name the F6 configuration T9 designated at T
    other = reconciliation.ReconciliationConfiguration(
        accepted_f3={(RUN_VERSIONS["classifier_version"], RUN_VERSIONS["text_extractor"]), ("f3.other", "x")},
        accepted_f4=CONFIG.accepted_f4, accepted_f5=CONFIG.accepted_f5)
    pinned = world.configure(at("2026-03-01"), designated=False, f6_designated=False, configuration=other)
    with pytest.raises(Refused) as e:
        evaluate(world, KNOWN_RECORDED, cutoff=at("2026-03-02"), pinned=pinned)
    assert e.value.reason == "f6_configuration_mismatch"
    fresh = World()
    fresh.configure(at("2026-01-01"), f6_designated=False)
    with pytest.raises(Refused) as e:
        evaluate(fresh, KNOWN_RECORDED, cutoff=NOW)
    assert e.value.reason == "no_f6_designation"
    late = World()                                     # T9 designated only AFTER T: never today's designation
    late.configure(at("2026-01-01"), f6_designated=at("2026-03-01"))
    p = backfilled(late, 101, 1)
    late.batch(at("2026-01-13"), [p])
    with pytest.raises(Refused) as e:
        evaluate(late, KNOWN_RECORDED, cutoff=at("2026-02-01"))
    assert e.value.reason == "no_f6_designation"
    assert evaluate(late, KNOWN_RECORDED, cutoff=at("2026-03-01")).facts


def test_known_recorded_refuses_a_batch_that_breaks_l7(world):
    """I-1 for KNOWN_RECORDED rests on L7 (F6.4's lock discipline). Evidence breaking it is refused, never returned
    labelled known_recorded."""
    p = backfilled(world, 101, 1)
    world.batch(BACKFILL - timedelta(hours=1), [p])                          # recorded before its inputs (synthetic)
    with pytest.raises(EvidenceError):
        evaluate(world, KNOWN_RECORDED, cutoff=BACKFILL - timedelta(minutes=30))
    assert evaluate(world, KNOWN_RECORDED, cutoff=NOW).facts[0].state == "single_source"     # I-1 holds at NOW


# ------------------------------------------------------------------------------------------------ supersession (§6)

def _filing(world, filing, n, published, raw, **doc):
    return backfilled(world, filing, n, published=published, raw=raw, **doc)


def test_t2_amendment_supersedes_overlapping_facts_only_after_its_availability(world):
    original = Doc(101, doc=1)
    st = original.statement()
    ci = original.column(st, end="2023-03-31")
    original.value(st, original.row(st, "Revenue"), ci, "1,000")
    original.value(st, original.row(st, "Profit before tax"), ci, "500", "profit_before_tax")
    world.listing(101, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(101, BACKFILL - timedelta(hours=1))
    o = world.process(original, BACKFILL, decision=d, uploaded_at=PUB)
    amended = _filing(world, 102, 2, at("2023-06-20T09:00"), "1,050", doc_type="amendment",
                      underlying="interim_financial_statements")
    rev, pbt = o.so("revenue").ef_key, o.so("profit_before_tax").ef_key
    cur = evaluate(world, CURRENT, horizon=NOW)
    assert shown(cur, rev) == [amended.so().so_key] and cur.fact(rev).state == "single_source"
    (rec,) = cur.fact(rev).supersession
    assert (rec.superseding, rec.superseded, rec.bases) == (amended.so().so_key, o.so("revenue").so_key, ("S-1",))
    assert shown(cur, pbt) == [o.so("profit_before_tax").so_key]                   # non-overlapping fact stays
    early = evaluate(world, AVAILABLE, cutoff=at("2023-06-01"), horizon=NOW)
    assert shown(early, rev) == [o.so("revenue").so_key] and early.fact(rev).supersession == ()
    late = evaluate(world, AVAILABLE, cutoff=at("2023-07-01"), horizon=NOW)
    assert shown(late, rev) == [amended.so().so_key]


def test_t3_restatement_supersedes_from_the_later_reports_availability(world):
    old = _filing(world, 101, 1, at("2023-05-30T09:00"), "2,000", end="2023-03-31", months=12,
                  doc_type="annual_report")
    new = _filing(world, 102, 2, at("2024-05-30T09:00"), "2,100", end="2023-03-31", months=12,
                  doc_type="annual_report", role="comparative", restated=True)
    ef = old.ef_keys[0]
    cur = evaluate(world, CURRENT, horizon=NOW)
    (rec,) = cur.fact(ef).supersession
    assert rec.bases == ("S-2",) and shown(cur, ef) == [new.so().so_key]
    (excluded,) = cur.fact(ef).excluded                                      # still shown in provenance
    assert (excluded.so_key, excluded.reason, excluded.superseded_by) == (old.so().so_key, "superseded_by",
                                                                          (new.so().so_key,))
    assert shown(evaluate(world, AVAILABLE, cutoff=at("2024-05-30T08:59"), horizon=NOW), ef) == [old.so().so_key]


NEVER = {
    "later_filing": [dict(), dict()],
    "interim_vs_annual": [dict(months=12, doc_type="interim_financial_statements"),
                          dict(months=12, doc_type="annual_report")],
    "unaudited_vs_audited": [dict(audit="unaudited"), dict(audit="audited")],
    "differing_comparative_without_restated": [dict(), dict(role="comparative")],
    "path_and_symbol_similarity": [dict(), dict()],
}


@pytest.mark.parametrize("case", sorted(NEVER))
def test_t30_the_never_list_is_never_supersession(world, case):
    first, second = NEVER[case]
    a = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000", **first)
    world.listing(102, BACKFILL - timedelta(hours=3), uploaded=at("2023-08-15T09:00"), symbol="COMB.X0000",
                  path="upload_report_file/369_101.pdf")                     # similar path, similar symbol
    b = _filing(world, 102, 2, at("2023-08-15T09:00"), "1,050", **second)
    ef = a.ef_keys[0]
    assert b.ef_keys == [ef]
    for mode, kw in ((CURRENT, dict(horizon=NOW)), (AVAILABLE, dict(cutoff=NOW, horizon=NOW)),
                     (KNOWN, dict(cutoff=NOW))):
        fact = evaluate(world, mode, **kw).fact(ef)
        assert fact.state == "conflicting" and fact.value_kind is None and fact.interval_low is None    # I-4
        assert fact.supersession == () and fact.excluded == () and len(fact.visible) == 2              # T-10
    if case == "interim_vs_annual":
        assert "differs_interim_vs_annual" in evaluate(world, CURRENT, horizon=NOW).fact(ef).f6_result.annotations


def test_t30_arrival_order_never_decides(world):
    """The later-published document is retrieved and processed FIRST: still no supersession either way."""
    late = _filing(world, 102, 2, at("2023-08-15T09:00"), "1,050", recorded=at("2026-01-12T10:00"))
    early = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000", recorded=at("2026-02-12T10:00"))
    fact = evaluate(world, CURRENT, horizon=NOW).fact(early.ef_keys[0])
    assert fact.state == "conflicting" and fact.supersession == () and late.ef_keys == early.ef_keys


def _replaced(world, *, first_retrieved_is_old, lm_new=at("2023-05-20T11:30"), epoch_new=at("2023-05-20T11:29"),
              lm_old=at("2023-05-15T09:35"), epoch_old=at("2023-05-15T09:30")):
    """Two different documents under ONE filing: the old bytes, then the bytes CSE replaced them with."""
    world.listing(301, at("2026-01-05"), uploaded=PUB)
    d = world.decision(301, at("2026-01-05T01:00"))
    when = {True: (at("2026-01-10"), at("2026-02-10")), False: (at("2026-02-10"), at("2026-01-10"))}
    t_old, t_new = when[first_retrieved_is_old]
    old = world.process(revenue_doc(301, 1, "1,000"), t_old, decision=d, uploaded_at=PUB, retrieved=t_old,
                        last_modified=lm_old, path_epoch=epoch_old)
    new = world.process(revenue_doc(301, 2, "1,050"), t_new, decision=d, uploaded_at=PUB, retrieved=t_new,
                        last_modified=lm_new, path_epoch=epoch_new)
    return old, new


def test_t31_replaced_document_s3_needs_the_sources_own_times(world):
    old, new = _replaced(world, first_retrieved_is_old=True)               # oldest-first
    ef = old.ef_keys[0]
    fact = evaluate(world, CURRENT, horizon=NOW).fact(ef)
    (rec,) = fact.supersession
    assert (rec.superseding, rec.bases) == (new.so().so_key, ("S-3",)) and shown(
        evaluate(world, CURRENT, horizon=NOW), ef) == [new.so().so_key]


def test_t31_newest_first_retrieval_is_ambiguous_never_the_older_winning():
    w = World()
    w.configure(at("2020-01-01"))
    old, new = _replaced(w, first_retrieved_is_old=False)
    fact = selection.evaluate(w.evidence(), w.query(CURRENT, horizon=NOW)).fact(old.ef_keys[0])
    assert fact.supersession == () and fact.flags == ("ambiguous_supersession",)
    assert {a.kind for a in fact.ambiguities} == {"unordered_versions"}
    assert len(fact.visible) == 2 and fact.state == "conflicting"            # nothing dropped


@pytest.mark.parametrize("times", [
    dict(lm_new=at("2023-05-20T11:30"), epoch_new=at("2023-05-15T09:00")),   # the source's times disagree
    dict(lm_new=None, epoch_new=at("2023-05-20T11:29"), lm_old=at("2023-05-15T09:35"), epoch_old=None),  # no pair
])
def test_ac1_unordered_source_times_are_ambiguous(times):
    w = World()
    w.configure(at("2020-01-01"))
    old, new = _replaced(w, first_retrieved_is_old=True, **times)
    fact = selection.evaluate(w.evidence(), w.query(CURRENT, horizon=NOW)).fact(old.ef_keys[0])
    assert fact.supersession == () and "ambiguous_supersession" in fact.flags and len(fact.visible) == 2


def test_t11_two_errata_with_different_values_are_ambiguous(world):
    o = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000")
    _filing(world, 102, 2, at("2023-06-15T09:00"), "1,050", doc_type="errata_or_reissue",
            underlying="interim_financial_statements")
    _filing(world, 103, 3, at("2023-07-15T09:00"), "1,075", doc_type="errata_or_reissue",
            underlying="interim_financial_statements")
    fact = evaluate(world, CURRENT, horizon=NOW).fact(o.ef_keys[0])
    assert "two_errata" in {a.kind for a in fact.ambiguities} and fact.supersession == ()
    assert len(fact.visible) == 3 and fact.state == "conflicting"


def test_t11_unknown_availability_is_ambiguous(world):
    o = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000")
    world.listing(102, BACKFILL - timedelta(hours=2), uploaded=at("2023-06-15T09:00"))
    d = world.decision(102, BACKFILL - timedelta(hours=1))
    world.process(revenue_doc(102, 902, "77", concept="profit_before_tax", label="Profit before tax"), BACKFILL,
                  decision=d, uploaded_at=at("2023-06-15T09:00"), retrieved=BACKFILL - timedelta(days=1))
    world.process(revenue_doc(102, 2, "1,050", doc_type="amendment", underlying="interim_financial_statements"),
                  BACKFILL + timedelta(days=1), decision=d, uploaded_at=at("2023-06-15T09:00"),
                  retrieved=BACKFILL + timedelta(hours=23))       # a later version with no document time: unknown
    fact = evaluate(world, CURRENT, horizon=NOW).fact(o.ef_keys[0])
    assert {a.kind for a in fact.ambiguities} == {"unknown_availability"} and fact.supersession == ()
    assert len(fact.visible) == 2 and fact.state == "conflicting"


def test_s1_needs_a_document_confirmed_or_document_only_type(world):
    o = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000")
    world.listing(102, BACKFILL - timedelta(hours=2), uploaded=at("2023-06-15T09:00"))
    d = world.decision(102, BACKFILL - timedelta(hours=1))
    world.process(revenue_doc(102, 2, "1,050", doc_type="amendment", underlying="interim_financial_statements"),
                  BACKFILL, decision=d, uploaded_at=at("2023-06-15T09:00"), doc_type_status="metadata_only")
    fact = evaluate(world, CURRENT, horizon=NOW).fact(o.ef_keys[0])
    assert fact.supersession == () and {a.kind for a in fact.ambiguities} == {"unclear_basis"}
    w = World()                                                              # document_only is admissible
    w.configure(at("2020-01-01"))
    o = _filing(w, 101, 1, at("2023-05-15T09:00"), "1,000")
    w.listing(102, BACKFILL - timedelta(hours=2), uploaded=at("2023-06-15T09:00"))
    d = w.decision(102, BACKFILL - timedelta(hours=1))
    a = w.process(revenue_doc(102, 2, "1,050", doc_type="amendment", underlying="interim_financial_statements"),
                  BACKFILL, decision=d, uploaded_at=at("2023-06-15T09:00"), doc_type_status="document_only")
    fact = selection.evaluate(w.evidence(), w.query(CURRENT, horizon=NOW)).fact(o.ef_keys[0])
    assert [r.superseding for r in fact.supersession] == [a.so().so_key]


def test_equal_availability_is_never_supersession(world):
    o = _filing(world, 101, 1, at("2023-05-15T09:00"), "1,000")
    _filing(world, 102, 2, at("2023-05-15T09:00"), "1,050", doc_type="amendment",
            underlying="interim_financial_statements")
    fact = evaluate(world, CURRENT, horizon=NOW).fact(o.ef_keys[0])
    assert fact.supersession == () and fact.state == "conflicting" and len(fact.visible) == 2


def test_supersession_chains_follow_available_at(world):
    o = _filing(world, 101, 1, at("2023-05-30T09:00"), "2,000", end="2023-03-31", months=12,
                doc_type="annual_report")
    s1 = _filing(world, 102, 2, at("2023-07-01T09:00"), "2,050", end="2023-03-31", months=12,
                 doc_type="errata_or_reissue", underlying="annual_report")
    s2 = _filing(world, 103, 3, at("2024-05-30T09:00"), "2,100", end="2023-03-31", months=12,
                 doc_type="annual_report", role="comparative", restated=True)
    ef = o.ef_keys[0]
    fact = evaluate(world, CURRENT, horizon=NOW).fact(ef)
    assert shown(evaluate(world, CURRENT, horizon=NOW), ef) == [s2.so().so_key]
    assert {(r.superseding, r.superseded) for r in fact.supersession} == {
        (s1.so().so_key, o.so().so_key), (s2.so().so_key, o.so().so_key), (s2.so().so_key, s1.so().so_key)}
    mid = evaluate(world, AVAILABLE, cutoff=at("2024-01-01"), horizon=NOW)
    assert shown(mid, ef) == [s1.so().so_key]                                 # s2 not yet available


def test_supersession_is_a_pure_function_of_the_candidates():
    """Order of the candidates never matters (I-5, I-9)."""
    w = World()
    w.configure(at("2020-01-01"))
    o = _filing(w, 101, 1, at("2023-05-15T09:00"), "1,000")
    s = _filing(w, 102, 2, at("2023-06-20T09:00"), "1,050", doc_type="amendment",
                underlying="interim_financial_statements")
    cands = [supersession.Candidate(o.so(), PUB, (101,)), supersession.Candidate(s.so(), at("2023-06-20T09:00"),
                                                                                 (102,))]
    a = supersession.derive(cands, lambda f, sha: ((), ()))
    b = supersession.derive(list(reversed(cands)), lambda f, sha: ((), ()))
    assert a == b and [r.bases for r in a.records] == [("S-1",)]


# ------------------------------------------------------------------------------------------------ F6 integration (§7.4)

def test_t33_d6_runs_only_over_runs_known_at_the_horizon_and_never_falls_back(world):
    world.listing(101, at("2026-01-10T08:00"), uploaded=PUB)
    d = world.decision(101, at("2026-01-10T09:00"))
    first = world.process(revenue_doc(101, 1, "1,000"), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB)
    rerun_doc = revenue_doc(101, 1, "1,000", concept="profit_before_tax", label="Profit before tax")
    rerun = world.process(rerun_doc, at("2026-03-01T10:00"), decision=d, uploaded_at=PUB)    # same bytes, re-run
    assert rerun.run.document_sha256 == first.run.document_sha256
    rev = first.ef_keys[0]
    assert shown(evaluate(world, KNOWN, cutoff=at("2026-02-01")), rev) == [first.so().so_key]
    later = evaluate(world, KNOWN, cutoff=at("2026-03-02"))
    assert later.fact(rev) is None                                           # no fallback to the older run
    assert later.fact(rerun.ef_keys[0]).state == "single_source"
    requested = evaluate(world, KNOWN, cutoff=at("2026-03-02"), facts=[rev])
    assert requested.fact(rev).state == "none" and requested.fact(rev).identity is None


def test_t37_version_visibility_comes_before_d6_and_the_later_filing_appears_nowhere(world):
    doc = revenue_doc(301, 7)
    world.listing(301, at("2026-01-05"), uploaded=at("2023-05-15T09:00"))
    world.listing(302, at("2026-01-05"), uploaded=at("2025-01-10T09:00"))
    d1, d2 = world.decision(301, at("2026-01-05T01:00")), world.decision(302, at("2026-01-05T01:00"))
    p1 = world.process(doc, at("2026-01-10"), decision=d1, uploaded_at=at("2023-05-15T09:00"))
    p2 = world.process(revenue_doc(302, 7), at("2026-02-10"), decision=d2, uploaded_at=at("2025-01-10T09:00"))
    r = evaluate(world, AVAILABLE, cutoff=at("2024-01-01"), horizon=NOW)
    (fact,) = r.facts
    assert shown(r, fact.ef_key) == [p1.so().so_key]                         # F1's run, not F2's newer one
    text = r.envelope()
    assert p2.run.f5_run_id not in text and p2.so().so_key not in text and '"302"' not in text
    assert all(302 not in x for x in fact.f6_result.context[0][1][0][1:])    # T-34: no second filing
    assert "same_document_multiple_filings" not in fact.f6_result.annotations
    cur = evaluate(world, CURRENT, horizon=NOW).facts[0]
    assert "same_document_multiple_filings" in cur.f6_result.annotations


def test_i12_d6_never_receives_a_run_of_an_invisible_version(world, monkeypatch):
    doc = revenue_doc(301, 7)
    world.listing(301, at("2026-01-05"), uploaded=at("2023-05-15T09:00"))
    world.listing(302, at("2026-01-05"), uploaded=at("2025-01-10T09:00"))
    d1, d2 = world.decision(301, at("2026-01-05T01:00")), world.decision(302, at("2026-01-05T01:00"))
    world.process(doc, at("2026-01-10"), decision=d1, uploaded_at=at("2023-05-15T09:00"))
    p2 = world.process(revenue_doc(302, 7), at("2026-02-10"), decision=d2, uploaded_at=at("2025-01-10T09:00"))
    seen = []
    real = reconciliation.select_runs

    def spy(runs, configuration):
        seen.append({r.f5_run_id for r in runs})
        return real(runs, configuration)
    monkeypatch.setattr(reconciliation, "select_runs", spy)
    evaluate(world, AVAILABLE, cutoff=at("2024-01-01"), horizon=NOW)
    assert seen and all(p2.run.f5_run_id not in s for s in seen)


def test_t9_changed_path_with_the_same_bytes_is_no_new_version(world):
    p = backfilled(world, 101, 1)
    before = av.version_availability(world.evidence(), 101, p.run.document_sha256, NOW)
    world.listing(101, at("2026-05-01"), uploaded=PUB, path="upload_report_file/369_999.pdf")
    after = av.version_availability(world.evidence(), 101, p.run.document_sha256, NOW)
    assert (before.at, before.role) == (after.at, after.role) == (PUB, "base")
    assert av.versions_of_filing(world.evidence(), 101, NOW).keys() == {p.run.document_sha256}


def test_t14_multiple_periods_in_one_document_are_independent_facts(world):
    doc = Doc(101, doc=1)
    st = doc.statement()
    c0, c1 = doc.column(st, end="2023-03-31"), doc.column(st, end="2022-03-31", role="comparative")
    doc.value(st, doc.row(st, "Revenue"), c0, "1,000")
    doc.value(st, doc.row(st, "Revenue (prior)"), c1, "900")
    world.listing(101, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(101, BACKFILL - timedelta(hours=1))
    p = world.process(doc, BACKFILL, decision=d, uploaded_at=PUB)
    r = evaluate(world, CURRENT, horizon=NOW)
    assert len(r.facts) == 2 and {f.state for f in r.facts} == {"single_source"}
    assert len(p.ef_keys) == 2


def test_t12_t13_identity_is_upstream_and_never_inferred(world):
    a = backfilled(world, 101, 1)                                            # listed as COMB.N0000
    world.listing(102, BACKFILL - timedelta(hours=3), uploaded=PUB, symbol="COMBX.N0000")    # renamed security
    d = world.decision(102, BACKFILL - timedelta(hours=1))
    b = world.process(revenue_doc(102, 2, end="2022-03-31"), BACKFILL, decision=d, uploaded_at=PUB)
    world.listing(103, BACKFILL - timedelta(hours=2), uploaded=PUB)          # delisted: no admissible link
    d3 = world.decision(103, BACKFILL - timedelta(hours=1), status="unresolved", basis="none")
    c = world.process(revenue_doc(103, 3, end="2021-03-31"), BACKFILL, decision=d3, uploaded_at=PUB)
    r = evaluate(world, CURRENT, horizon=NOW)
    assert {f.ef_key for f in r.facts} == {a.ef_keys[0], b.ef_keys[0]}      # continuous under one issuer_id
    assert c.stored == []                                                    # no observation exists at all
    with pytest.raises(Refused) as e:
        world.query(CURRENT, horizon=NOW, issuer="COMB")
    assert e.value.reason == "identity_unresolved"
    assert evaluate(world, CURRENT, horizon=NOW, issuer=OTHER_ISSUER).facts == ()


def test_fact_filter_by_identity_fields(world):
    doc = Doc(101, doc=1)
    st = doc.statement()
    ci = doc.column(st, end="2023-03-31")
    doc.value(st, doc.row(st, "Revenue"), ci, "1,000")
    doc.value(st, doc.row(st, "Profit before tax"), ci, "500", "profit_before_tax")
    world.listing(101, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(101, BACKFILL - timedelta(hours=1))
    p = world.process(doc, BACKFILL, decision=d, uploaded_at=PUB)
    r = evaluate(world, CURRENT, horizon=NOW, facts={"concept_key": "revenue", "period_end": date(2023, 3, 31)})
    assert [f.ef_key for f in r.facts] == [p.so("revenue").ef_key]
    assert evaluate(world, CURRENT, horizon=NOW, facts={"concept_key": "revenue", "period_end": "2022-03-31"}).facts \
        == ()
