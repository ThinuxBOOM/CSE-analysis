"""
F8 anti-leakage, invariants, reproducibility and the point-in-time interfaces, on synthetic evidence.

The sources are docs/F8_DESIGN.md:
- §7.7 (the information set Ω, F-1);
- §11 (result_hash, settled replay, F-4);
- §8.2 (F-5);
- §15 (T-19, T-20, T-22, T-36, T-40; I-1 to I-12);
- Appendix B (the ten adversarial cases).

**The strongest check is differential.** A KNOWN (T) result is computed twice: from every row, and from only the rows
whose recorded time is at or before T. The two must be byte-identical. That proves no row outside Ω reaches any part of
the result: rows, counts, aggregates, metadata, envelope or hash. AVAILABLE (T, H) is checked the same way against its
own Ω. The worlds are random but seeded, and so reproducible.
"""
import json
import os
import random
import sys
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f8_factories import ISSUER, World, at, epoch_ms, revenue_doc  # noqa: E402
from worker.financial_asof import availability as av  # noqa: E402
from worker.financial_asof import explain, knowledge, metadata, pit, selection  # noqa: E402
from worker.financial_asof import config as f8config  # noqa: E402
from worker.financial_asof.errors import Refused  # noqa: E402
from worker.financial_asof.model import Evidence  # noqa: E402
from worker.financial_asof.result import STATES  # noqa: E402
from worker.financial_asof.query import AVAILABLE, CURRENT, KNOWN, KNOWN_RECORDED  # noqa: E402
from worker.financial_asof.times import colombo_date, colombo_end_of_day  # noqa: E402

PUB = at("2023-05-15T09:30")
BACKFILL = at("2026-01-12T10:00")
NOW = at("2026-10-01T00:00")
US = timedelta(microseconds=1)
SEEDS = range(40)


def evaluate(world, mode, **kw):
    return selection.evaluate(world.evidence(), world.query(mode, **kw))


@pytest.fixture
def world():
    w = World()
    w.configure(at("2020-01-01"))
    return w


def backfilled(world, filing, n, *, published=PUB, raw="1,000", recorded=BACKFILL, **doc):
    world.listing(filing, recorded - timedelta(hours=2), uploaded=published)
    d = world.decision(filing, recorded - timedelta(hours=1))
    return world.process(revenue_doc(filing, n, raw, **doc), recorded, decision=d, uploaded_at=published)


# ------------------------------------------------------------------------------------------------ random worlds

def random_world(seed):
    """A seeded random history. It has:
    - live and backfilled filings;
    - date-only and authorized times, and timestamps moved backward;
    - errata, amendments (some only metadata_only), restated comparatives, interim/annual pairs;
    - the same bytes under two filings;
    - replaced documents, with and without document-level times (A-5 unknown);
    - F1 metadata ties (the same id twice in one response);
    - re-validations after F1 edits, and stored F6.4 batches.
    Every validation run uses the F1 metadata current at its own time, as F6.4's M4 does."""
    rnd = random.Random(seed)
    w = World()
    w.configure(at("2020-01-01"))
    base = at("2023-01-01T00:00")
    processed, n = [], 0
    for i in range(rnd.randint(3, 7)):
        filing = 1000 + i
        pub = base + timedelta(days=rnd.randint(0, 500), minutes=rnd.randint(0, 1439))
        live = rnd.random() < 0.5
        observed = pub + timedelta(minutes=rnd.randint(1, 900)) if live else             at("2026-01-01") + timedelta(days=rnd.randint(0, 200), minutes=rnd.randint(0, 1439))
        if rnd.random() < 0.2:                                           # a legacy date-only value
            w.listing(filing, observed, raw_uploaded=epoch_ms(at(colombo_date(pub).isoformat() + "T00:00")))
        else:
            auth = pub + timedelta(hours=rnd.randint(0, 30)) if rnd.random() < 0.3 else None
            endpoint = "getFinancialAnnouncement" if rnd.random() < 0.3 else "financials"
            w.listing(filing, observed, uploaded=pub, authorized=auth, endpoint=endpoint)
            if rnd.random() < 0.12:                                      # the same id twice in one response
                w.listing(filing, observed, uploaded=pub + timedelta(minutes=1), authorized=auth,
                          endpoint=endpoint, text="Interim Financial Statements (revised title)")
        decided = observed + timedelta(minutes=rnd.randint(1, 120))
        d = w.decision(filing, decided)
        kind = rnd.choice(["interim", "interim", "interim", "errata", "amendment", "amendment_meta", "restated",
                           "annual"])
        doc_kw = {"interim": {}, "errata": dict(doc_type="errata_or_reissue",
                                                underlying="interim_financial_statements"),
                  "amendment": dict(doc_type="amendment", underlying="interim_financial_statements"),
                  "amendment_meta": dict(doc_type="amendment", underlying="interim_financial_statements"),
                  "restated": dict(doc_type="annual_report", role="comparative", restated=True),
                  "annual": dict(doc_type="annual_report")}[kind]
        status = "metadata_only" if kind == "amendment_meta" else "confirmed"
        end = rnd.choice(["2022-12-31", "2023-03-31"])
        n += 1
        doc = revenue_doc(filing, n, rnd.choice(["1,000", "1,000", "1,050", "1,100"]), end=end, **doc_kw)
        recorded = decided + timedelta(minutes=rnd.randint(1, 120))
        uploaded = metadata.as_of(w.evidence(), filing, recorded).uploaded_at[0]
        p = w.process(doc, recorded, decision=d, uploaded_at=uploaded, retrieved=recorded - timedelta(minutes=1),
                      last_modified=pub + timedelta(minutes=rnd.randint(0, 5)),
                      path_epoch=pub if rnd.random() < 0.7 else None, doc_type_status=status)
        processed.append(p)
        if rnd.random() < 0.25:                                          # CSE edits the listing later
            edited = recorded + timedelta(days=rnd.randint(1, 90))
            w.listing(filing, edited, uploaded=pub - timedelta(days=rnd.randint(-3, 3)))
            redo = edited + timedelta(hours=1)
            processed.append(w.validate(p.run, doc, redo, decision=d, doc_type_status=status,
                                        uploaded_at=metadata.as_of(w.evidence(), filing, redo).uploaded_at[0]))
        if rnd.random() < 0.3:                                           # replaced bytes under the same filing
            n += 1
            later = recorded + timedelta(days=rnd.randint(1, 60))
            uploaded = metadata.as_of(w.evidence(), filing, later).uploaded_at[0]
            timed = rnd.random() < 0.6                                   # else: no document time (A-5 unknown)
            processed.append(w.process(revenue_doc(filing, n, "1,075", end=end), later, decision=d,
                                       uploaded_at=uploaded, retrieved=later - timedelta(minutes=1),
                                       last_modified=pub + timedelta(days=rnd.randint(1, 20)) if timed else None,
                                       path_epoch=pub + timedelta(days=rnd.randint(-2, 20)) if timed else None))
        if rnd.random() < 0.15 and i > 0:                                # the same bytes under another filing
            other = 2000 + i
            w.listing(other, observed + timedelta(days=3), uploaded=pub + timedelta(days=rnd.randint(-10, 10)))
            d2 = w.decision(other, observed + timedelta(days=3, minutes=5))
            moved = revenue_doc(other, n, doc.candidates[0]["raw_value"], end=end, **doc_kw)
            when = observed + timedelta(days=3, minutes=10)
            processed.append(w.process(moved, when, decision=d2, doc_type_status=status,
                                       uploaded_at=metadata.as_of(w.evidence(), other, when).uploaded_at[0]))
    for k in range(rnd.randint(0, 2)):                                   # stored F6.4 batches at random times
        when = at("2026-01-01") + timedelta(days=rnd.randint(0, 270))
        known = [p for p in processed if p.vr.recorded_at < when]
        latest = {}
        for p in known:                                                  # M4 at `when`, as F6.4 would have done
            latest[p.run.f5_run_id] = p
        if known:
            w.batch(when, list(latest.values()))
    return w


def cutoffs(seed, evidence=None):
    """Four random cutoffs. With the evidence, also cutoffs at and one microsecond before some recorded times and
    publication instants, where the boundaries are."""
    rnd = random.Random(seed * 7919)
    out = [at("2023-01-01") + timedelta(days=rnd.randint(0, 1400), minutes=rnd.randint(0, 1439)) for _ in range(4)]
    if evidence is not None:
        moments = sorted({r.recorded_at for r in evidence.runs.values()}
                         | {o.observed_at for o in evidence.filing_observations.values()}
                         | {v.recorded_at for v in evidence.validation_runs.values()})
        moments += sorted({av.filing_availability(evidence, f, NOW).at for f in evidence.filings()} - {None})
        for m in rnd.sample(moments, min(6, len(moments))):
            out += [m, m - US]
    return out


def restricted_known(evidence, cutoff):
    """Evidence restricted to the rows known at or before `cutoff` (every recorded time)."""
    return Evidence(
        filing_observations=[o for o in evidence.filing_observations.values() if o.observed_at <= cutoff],
        classifications=[c for c in evidence.classifications.values() if c.classified_at <= cutoff],
        issuer_decisions=[d for d in evidence.issuer_decisions.values() if d.decided_at <= cutoff],
        runs=[r for r in evidence.runs.values() if r.recorded_at <= cutoff],
        validation_runs=[v for v in evidence.validation_runs.values() if v.recorded_at <= cutoff],
        observations=[s for s in evidence.observations.values() if s.recorded_at <= cutoff],
        f6_configurations=[c for c in evidence.f6_configurations.values() if c.recorded_at <= cutoff],
        f6_designations=[d for d in evidence.f6_designations if d.recorded_at <= cutoff],
        f8_configurations=[c for c in evidence.f8_configurations.values() if c.recorded_at <= cutoff],
        f8_designations=[d for d in evidence.f8_designations if d.recorded_at <= cutoff],
        batches=[b for b in evidence.batches if b.recorded_at <= cutoff])


def restricted_available(evidence, cutoff, horizon):
    """Ω (AVAILABLE, T, H): rows known by H that belong to versions available by T (as known at H), with every
    availability evidence observed by H (F1 observations and F5 run snapshots, per Appendix B.1)."""
    known = restricted_known(evidence, horizon)
    cache = av.Cache(known, horizon)
    visible = {r.f5_run_id for r in known.runs.values()
               if (a := cache.version(r.cse_filing_id, r.document_sha256).at) is not None and a <= cutoff}
    return Evidence(
        filing_observations=known.filing_observations.values(), classifications=known.classifications.values(),
        issuer_decisions=known.issuer_decisions.values(), runs=known.runs.values(),
        validation_runs=[v for v in known.validation_runs.values() if v.f5_run_id in visible],
        observations=[s for s in known.observations.values() if s.f5_run_id in visible],
        f6_configurations=known.f6_configurations.values(), f6_designations=known.f6_designations,
        f8_configurations=known.f8_configurations.values(), f8_designations=known.f8_designations, batches=())


def q(mode, **kw):
    from worker.financial_asof import query
    return query.query(issuer_id=ISSUER, mode=mode, information_cutoff=kw.get("cutoff"),
                       knowledge_horizon=kw.get("horizon"))


# ------------------------------------------------------------------------------------------------ closure (T-36, I-11)

@pytest.mark.parametrize("seed", SEEDS)
def test_t36_known_result_is_byte_identical_without_every_row_known_after_t(seed):
    ev = random_world(seed).evidence()
    for t in cutoffs(seed, ev):
        full = selection.evaluate(ev, q(KNOWN, cutoff=t))
        alone = selection.evaluate(restricted_known(ev, t), q(KNOWN, cutoff=t))
        assert full.envelope() == alone.envelope() and full.result_hash == alone.result_hash, (seed, t)


@pytest.mark.parametrize("seed", SEEDS)
def test_t36_known_recorded_result_is_byte_identical_without_every_row_known_after_t(seed):
    ev = random_world(seed).evidence()
    for t in cutoffs(seed, ev) + [NOW]:
        full = selection.evaluate(ev, q(KNOWN_RECORDED, cutoff=t))
        alone = selection.evaluate(restricted_known(ev, t), q(KNOWN_RECORDED, cutoff=t))
        assert full.envelope() == alone.envelope(), (seed, t)


@pytest.mark.parametrize("seed", SEEDS)
def test_t36_available_result_depends_only_on_its_information_set(seed):
    ev = random_world(seed).evidence()
    for t in cutoffs(seed, ev):
        for h in (max(t, at("2026-03-01")), max(t, NOW)):
            full = selection.evaluate(ev, q(AVAILABLE, cutoff=t, horizon=h))
            alone = selection.evaluate(restricted_available(ev, t, h), q(AVAILABLE, cutoff=t, horizon=h))
            assert full.envelope() == alone.envelope(), (seed, t, h)


@pytest.mark.parametrize("seed", SEEDS)
def test_current_result_depends_only_on_rows_known_by_h(seed):
    ev = random_world(seed).evidence()
    for h in cutoffs(seed, ev):
        full = selection.evaluate(ev, q(CURRENT, horizon=h))
        alone = selection.evaluate(restricted_known(ev, h), q(CURRENT, horizon=h))
        assert full.envelope() == alone.envelope(), (seed, h)


def test_t36_available_never_mentions_a_document_unavailable_or_unknown_by_t(world):
    a = backfilled(world, 101, 1)
    later = backfilled(world, 102, 2, published=at("2024-02-01T09:00"))            # published after T
    world.listing(103, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(103, BACKFILL - timedelta(hours=1))
    world.process(revenue_doc(103, 903, "5", concept="profit_before_tax", label="Profit before tax"), BACKFILL,
                  decision=d, uploaded_at=PUB, retrieved=BACKFILL - timedelta(days=1))
    unknown = world.process(revenue_doc(103, 3), BACKFILL + timedelta(days=1), decision=d, uploaded_at=PUB,
                            retrieved=BACKFILL + timedelta(hours=23))                  # later version, unknown
    r = evaluate(world, AVAILABLE, cutoff=at("2023-12-31"), horizon=NOW)
    text = r.envelope()
    for hidden in (later, unknown):
        for needle in (hidden.run.f5_run_id, hidden.run.document_sha256, hidden.vr.key, hidden.so().so_key):
            assert needle not in text
    (fact,) = [f for f in r.facts if f.ef_key == a.ef_keys[0]]
    assert fact.counts == () and fact.excluded == () and fact.state == "single_source"


def test_t36_known_never_names_a_row_known_after_t(world):
    a = backfilled(world, 101, 1, recorded=at("2024-01-10T10:00"))
    t = at("2024-06-30")
    before = evaluate(world, KNOWN, cutoff=t)
    late = backfilled(world, 102, 2, published=at("2023-05-01T09:00"))           # older publication, known in 2026
    world.listing(101, at("2026-02-01"), uploaded=PUB - timedelta(days=30))       # a later listing edit
    world.designate_f8(f8config.F8Configuration(world.f6_configurations[0].configuration_id).f8_configuration_id,
                       at("2026-03-01"))                                          # a later designation row
    after = evaluate(world, KNOWN, cutoff=t)
    assert after.envelope() == before.envelope()
    for needle in (late.run.f5_run_id, late.so().so_key, "not_yet_known"):
        assert needle not in after.envelope()


# ------------------------------------------------------------------------------------------------ invariants

def instant(text):
    return None if text is None else datetime.fromisoformat(text)


@pytest.mark.parametrize("seed", SEEDS)
def test_invariants_i1_i2_i3_i4_i10_on_random_histories(seed):
    ev = random_world(seed).evidence()
    for t in cutoffs(seed, ev):
        known = selection.evaluate(ev, q(KNOWN, cutoff=t))
        available = selection.evaluate(ev, q(AVAILABLE, cutoff=t, horizon=max(t, NOW)))
        recorded = selection.evaluate(ev, q(KNOWN_RECORDED, cutoff=t))
        for result in (known, recorded):                                          # I-1
            for f in result.facts:
                assert all(instant(o.knowledge.known_at) <= t for o in f.visible), (seed, t)
                assert all(knowledge.known_at(ev, ev.observations[e.so_key]).at <= t for e in f.excluded)
        for f in known.facts:                                                     # I-2 (KNOWN)
            assert all(o.availability.available_at is None or instant(o.availability.available_at) <= t
                       for o in f.visible), (seed, t)
        for f in available.facts:                                                 # I-2 (AVAILABLE)
            assert all(o.availability.available_at is not None and instant(o.availability.available_at) <= t
                       for o in f.visible), (seed, t)
        for result in (known, available, recorded, selection.evaluate(ev, q(CURRENT, horizon=NOW))):
            assert result.verify()
            for f in result.facts:
                assert f.state in STATES
                if f.state == "conflicting":                                      # I-4
                    assert f.value_kind is None and f.interval_low is None and f.representative is None
                for rec in f.supersession:                                        # I-3
                    assert rec.bases and set(rec.bases) <= {"S-1", "S-2", "S-3"}
                if f.f6_result is not None and result.mode != KNOWN_RECORDED:     # I-10: context from V only
                    in_context = {fl for _, filings in f.f6_result.context[0][1] for fl in filings}
                    visible_versions = {v.cse_filing_id for o in f.visible for v in o.availability.versions}
                    assert in_context <= visible_versions, (seed, t)


@pytest.mark.parametrize("seed", SEEDS)
def test_i7_version_availability_never_decreases_as_the_horizon_grows(seed):
    ev = random_world(seed).evidence()
    horizons = sorted({r.recorded_at for r in ev.runs.values()} | {o.observed_at for o in
                                                                   ev.filing_observations.values()} | {NOW})
    for (filing, sha) in sorted({(r.cse_filing_id, r.document_sha256) for r in ev.runs.values()}):
        series = []
        for h in horizons:
            if any(r.recorded_at <= h for r in ev.runs_of_filing(filing) if r.document_sha256 == sha):
                series.append(av.version_availability(ev, filing, sha, h).at)
        for a, b in zip(series, series[1:]):
            assert a is None or b is None or a <= b, (seed, filing, sha)       # never earlier; may become unknown
            if a is not None and b is None:
                continue


@pytest.mark.parametrize("seed", SEEDS)
def test_t19_input_order_never_matters(seed):
    w = random_world(seed)
    rnd = random.Random(seed)

    def shuffle(xs):
        xs = list(xs)
        rnd.shuffle(xs)
        return xs
    for t in cutoffs(seed, w.evidence()):
        for mode, kw in ((KNOWN, dict(cutoff=t)), (AVAILABLE, dict(cutoff=t, horizon=max(t, NOW))),
                         (CURRENT, dict(horizon=NOW)), (KNOWN_RECORDED, dict(cutoff=t))):
            a = selection.evaluate(w.evidence(), q(mode, **kw))
            b = selection.evaluate(w.evidence(order=shuffle), q(mode, **kw))
            assert a.result_hash == b.result_hash and a.envelope() == b.envelope()


# ------------------------------------------------------------------------------------------------ Appendix B

def test_case1_available_in_2023_backfilled_in_2026(world):
    p = backfilled(world, 101, 1)
    for t in (at("2023-06-01"), at("2025-12-31")):
        assert evaluate(world, KNOWN, cutoff=t).facts == ()
        assert evaluate(world, KNOWN_RECORDED, cutoff=t).facts == ()
    r = evaluate(world, AVAILABLE, cutoff=at("2023-12-31"), horizon=NOW)
    assert r.label == "reconstructed" and r.knowledge_horizon == NOW.isoformat() and r.facts[0].visible
    assert evaluate(world, CURRENT, horizon=NOW).label == "retrospective_current"
    assert p.ef_keys


def test_case2_discovered_before_t_available_after_t(world):
    """U later than the system's own sighting (synthetic): hidden in KNOWN as an in-set exclusion, absent in
    AVAILABLE, kept and flagged in KNOWN_RECORDED, visible in CURRENT."""
    world.listing(101, at("2023-05-10T10:00"), uploaded=PUB)
    d = world.decision(101, at("2023-05-10T10:30"))
    p = world.process(revenue_doc(101, 1), at("2023-05-10T11:00"), decision=d, uploaded_at=PUB)
    world.batch(at("2023-05-10T12:00"), [p])
    t = at("2023-05-12")
    k = evaluate(world, KNOWN, cutoff=t).facts[0]
    assert k.state == "none" and [e.reason for e in k.excluded] == ["not_yet_available"]
    assert evaluate(world, AVAILABLE, cutoff=t, horizon=NOW).facts == ()
    kr = evaluate(world, KNOWN_RECORDED, cutoff=t).facts[0]
    assert kr.state == "single_source" and "available_after_known:instant" in kr.visible[0].flags
    assert evaluate(world, CURRENT, horizon=NOW).facts[0].state == "single_source"


def test_cases3_4_exactly_at_t_and_one_microsecond_after(world):
    p = backfilled(world, 101, 1)
    assert evaluate(world, AVAILABLE, cutoff=PUB, horizon=NOW).facts[0].visible
    assert evaluate(world, AVAILABLE, cutoff=PUB - US, horizon=NOW).facts == ()
    assert evaluate(world, KNOWN, cutoff=BACKFILL).facts[0].visible
    assert evaluate(world, KNOWN, cutoff=BACKFILL - US).facts == ()
    assert p.ef_keys


def test_case5_a_later_superseding_document_discovered_after_the_cutoff(world):
    o = backfilled(world, 101, 1, recorded=at("2023-05-16T10:00"))                # live
    s = backfilled(world, 102, 2, raw="1,050", published=at("2023-06-20T09:00"), doc_type="amendment",
                   underlying="interim_financial_statements")                     # published by T, found in 2026
    ef, t = o.ef_keys[0], at("2023-12-31")
    k = evaluate(world, KNOWN, cutoff=t)
    assert [v.so_key for v in k.fact(ef).visible] == [o.so().so_key] and s.so().so_key not in k.envelope()
    a = evaluate(world, AVAILABLE, cutoff=t, horizon=NOW)
    assert [v.so_key for v in a.fact(ef).visible] == [s.so().so_key]               # what an always-on reader had
    published_late = World()
    published_late.configure(at("2020-01-01"))
    o2 = backfilled(published_late, 101, 1, recorded=at("2023-05-16T10:00"))
    backfilled(published_late, 102, 2, raw="1,050", published=at("2024-02-01T09:00"), doc_type="amendment",
               underlying="interim_financial_statements")
    a = selection.evaluate(published_late.evidence(), published_late.query(AVAILABLE, cutoff=t, horizon=NOW))
    assert [v.so_key for v in a.fact(ef).visible] == [o2.so().so_key]


def test_case6_future_validation_and_reconciliation_runs_never_enter(world):
    world.listing(101, at("2026-01-10T08:00"), uploaded=PUB)
    d = world.decision(101, at("2026-01-10T09:00"))
    p = world.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB,
                      validated=at("2026-02-01T00:00"))                          # validated only later
    world.batch(at("2026-02-02"), [p])
    t = at("2026-01-20")
    assert evaluate(world, KNOWN, cutoff=t).facts == ()
    assert evaluate(world, KNOWN_RECORDED, cutoff=t).facts == ()


def test_case7_a_timestamp_moved_backward_later(world):
    world.listing(101, at("2026-01-10T08:00"), uploaded=PUB)
    world.listing(101, at("2026-02-10T08:00"), uploaded=PUB - timedelta(days=5))
    d = world.decision(101, at("2026-01-10T09:00"))
    world.process(revenue_doc(101, 1), at("2026-01-12T10:00"), decision=d, uploaded_at=PUB)
    world.validate(world.runs[0], revenue_doc(101, 1), at("2026-02-10T09:00"), decision=d,
                   uploaded_at=PUB - timedelta(days=5))                           # F6.4 re-validates after the edit
    assert evaluate(world, AVAILABLE, cutoff=PUB - US, horizon=NOW).facts == ()
    assert evaluate(world, AVAILABLE, cutoff=PUB, horizon=NOW).facts[0].visible


def test_case8_a_date_only_source_timestamp(world):
    world.listing(101, at("2023-05-15T10:00"), raw_uploaded=epoch_ms(at("2023-05-15T00:00")))
    d = world.decision(101, at("2023-05-15T10:30"))
    world.process(revenue_doc(101, 1), at("2023-05-15T11:00"), decision=d, uploaded_at=at("2023-05-15T00:00"))
    eod = colombo_end_of_day(colombo_date(at("2023-05-15T00:00")))
    assert eod == at("2023-05-16T00:00")
    assert evaluate(world, AVAILABLE, cutoff=eod, horizon=NOW).facts[0].visible[0].availability.precision == "day"
    assert evaluate(world, AVAILABLE, cutoff=eod - US, horizon=NOW).facts == ()
    assert evaluate(world, KNOWN, cutoff=at("2023-05-15T23:59")).facts[0].state == "none"


def test_case9_availability_unknown(world):
    world.listing(101, BACKFILL - timedelta(hours=2), uploaded=PUB)
    d = world.decision(101, BACKFILL - timedelta(hours=1))
    world.process(revenue_doc(101, 901, "5", concept="profit_before_tax", label="Profit before tax"), BACKFILL,
                  decision=d, uploaded_at=PUB, retrieved=BACKFILL - timedelta(days=1))
    p = world.process(revenue_doc(101, 1), BACKFILL + timedelta(days=1), decision=d, uploaded_at=PUB,
                      retrieved=BACKFILL + timedelta(hours=23))
    ef = p.so().ef_key
    assert evaluate(world, AVAILABLE, cutoff=NOW, horizon=NOW).fact(ef) is None
    assert evaluate(world, KNOWN, cutoff=NOW).fact(ef).state == "single_source"
    assert evaluate(world, CURRENT, horizon=NOW).fact(ef).state == "single_source"


def test_case10_current_can_never_pass_as_point_in_time(world):
    backfilled(world, 101, 1)
    cur = evaluate(world, CURRENT, horizon=NOW)
    with pytest.raises(Refused) as e:
        pit.require_point_in_time(cur)
    assert e.value.reason == "current_not_point_in_time"
    relabelled = replace(cur, mode=KNOWN, label="known")                          # T-40
    with pytest.raises(Refused) as e:
        pit.require_point_in_time(relabelled)
    assert e.value.reason == "result_hash_mismatch"
    with pytest.raises(Refused) as e:
        pit.dataset([cur])
    assert e.value.reason == "current_not_point_in_time"
    with pytest.raises(Refused):
        pit.timeline(world.evidence(), world.query(CURRENT, horizon=NOW), [NOW])


# ------------------------------------------------------------------------------------------------ PIT interfaces (§8)

def test_datasets_refuse_mixed_modes_and_live_use_is_known_only(world):
    backfilled(world, 101, 1)
    k = evaluate(world, KNOWN, cutoff=NOW)
    kr = evaluate(world, KNOWN_RECORDED, cutoff=NOW)
    a = evaluate(world, AVAILABLE, cutoff=NOW, horizon=NOW)
    with pytest.raises(Refused) as e:
        pit.dataset([k, a])
    assert e.value.reason == "mixed_modes"
    ds = pit.dataset([k, evaluate(world, KNOWN, cutoff=NOW - timedelta(days=1))])
    assert ds.mode == KNOWN and ds.label == "known" and len(ds.result_hashes) == 2
    assert pit.dataset([k, evaluate(world, KNOWN, cutoff=NOW - timedelta(days=1))]).dataset_hash == ds.dataset_hash
    assert pit.require_live(k) is k and pit.require_live(kr) is kr
    with pytest.raises(Refused) as e:
        pit.require_live(a)
    assert e.value.reason == "not_live_mode"
    with pytest.raises(Refused):
        pit.dataset([])


def test_timeline_one_fact_at_many_cutoffs(world):
    p = backfilled(world, 101, 1)
    ts = [at("2023-01-01"), PUB, at("2024-01-01")]
    series = pit.timeline(world.evidence(), world.query(AVAILABLE, cutoff=NOW, horizon=NOW, facts=p.ef_keys), ts)
    assert [r.facts[0].state for r in series] == ["none", "single_source", "single_source"]
    assert [r.information_cutoff for r in series] == [t.isoformat() for t in ts]
    assert all(r.verify() for r in series)


# ------------------------------------------------------------------------------------------------ reproducibility

def test_t19_repeated_queries_are_byte_identical_and_idempotent(world):
    backfilled(world, 101, 1)
    backfilled(world, 102, 2, raw="1,050", published=at("2023-06-20T09:00"), doc_type="amendment",
               underlying="interim_financial_statements")
    for mode, kw in ((KNOWN, dict(cutoff=NOW)), (AVAILABLE, dict(cutoff=NOW, horizon=NOW)),
                     (CURRENT, dict(horizon=NOW))):
        results = [evaluate(world, mode, **kw) for _ in range(3)]
        assert len({r.result_hash for r in results}) == 1 and len({r.envelope() for r in results}) == 1
        assert results[0].result_hash == results[0].computed_hash()
        json.loads(results[0].envelope())                                        # canonical JSON, ASCII
        assert results[0].envelope().isascii()


def test_t19_a_pinned_replay_reproves_a_designated_result(world):
    backfilled(world, 101, 1)
    designated = evaluate(world, KNOWN, cutoff=NOW)
    pinned = evaluate(world, KNOWN, cutoff=NOW, pinned=designated.f8_configuration_id)
    assert pinned.result_hash == designated.result_hash                          # the hash names no designation


def test_t22_rule_versions_stay_reproducible_after_a_new_version_appears(world):
    backfilled(world, 101, 1)
    before = evaluate(world, KNOWN, cutoff=NOW)
    future = f8config.F8Configuration(world.f6_configurations[0].configuration_id,
                                      availability_version="f8.availability.2")
    world.register_f8(future, at("2026-11-01"))
    world.designate_f8(future.f8_configuration_id, at("2026-11-01"))
    assert evaluate(world, KNOWN, cutoff=NOW).result_hash == before.result_hash   # .1 still answers at NOW
    with pytest.raises(Refused) as e:                                             # .2 is never reinterpreted
        evaluate(world, KNOWN, cutoff=at("2026-12-01"))
    assert e.value.reason == "configuration_not_implemented"
    assert evaluate(world, KNOWN, cutoff=at("2026-12-01"), pinned=before.f8_configuration_id).facts


def test_t20_explain_reaches_the_cse_evidence_and_audit_stays_outside_the_hash(world):
    o = backfilled(world, 101, 1)
    s = backfilled(world, 102, 2, raw="1,050", published=at("2023-06-20T09:00"), doc_type="amendment",
                   underlying="interim_financial_statements")
    late = backfilled(world, 103, 3, published=at("2023-04-10T09:00"), recorded=at("2026-09-01T10:00"))
    ev = world.evidence()
    r = selection.evaluate(ev, world.query(CURRENT, horizon=at("2026-06-01")))
    out = explain.explain(ev, r, recomputed=selection.evaluate(ev, world.query(CURRENT, horizon=at("2026-06-01"))),
                          audit=True)
    assert out["reproved"] and out["envelope_reproves"]
    (fact,) = out["facts"]
    for entry in fact["visible"] + fact["excluded"]:
        f1 = entry["filing_observations"]
        assert f1 and all(isinstance(x["raw_item"], dict) and "uploadedDate" in x["raw_item"] for x in f1)
        assert entry["f5_run"]["snapshot"] and entry["classification"] and entry["issuer_decision"]
    assert [x["reason"] for x in fact["excluded"]] == ["superseded_by"]
    assert fact["supersession"][0]["bases"] == ["S-1"]
    audit = out["audit"]
    assert audit["label"] == "audit" and audit["outside_result_hash"] and not audit["point_in_time_input"]
    assert any(row.get("so_key") == late.so().so_key and row["reason"] == "known_after_horizon"
               for row in audit["rows"])
    assert late.so().so_key not in r.envelope()                                   # only in the audit
    assert selection.evaluate(ev, world.query(CURRENT, horizon=at("2026-06-01"))).result_hash == r.result_hash
    assert o.ef_keys == s.ef_keys


def test_empty_information_sets(world):
    r = evaluate(world, KNOWN, cutoff=NOW)
    assert r.facts == () and r.verify()
    key = "f" * 64
    r = evaluate(world, KNOWN, cutoff=NOW, facts=[key])
    (fact,) = r.facts
    assert (fact.ef_key, fact.state, fact.identity, fact.visible, fact.excluded, fact.counts) == (
        key, "none", None, (), (), ())
    other = World(issuer="33333333-3333-4333-8333-333333333333")
    other.configure(at("2020-01-01"))
    assert selection.evaluate(other.evidence(), other.query(CURRENT, horizon=NOW)).facts == ()
