"""
Phase 6 NS-6 (design §7, §8, §13): decay with hand-computed vectors, and the snapshot: features, quality states and
their precedence, the active source set, provenance; leakage tests NL-6, NL-7, NL-9, NL-14 and NL-16, with converses
(NL-10 is static, in test_ns_static.py). Synthetic articles about invented issuers only.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from worker.market_features.modes import AVAILABLE, KNOWN, Query
from worker.news_ingest import articles as ar
from worker.news_sentiment import aliases as al
from worker.news_sentiment import config as cf
from worker.news_sentiment import decay as dc
from worker.news_sentiment import process as pr
from worker.news_sentiment import snapshot as sn

UTC = timezone.utc
CMB = dc.COLOMBO
# sessions: Mon 7 Sep to Fri 2 Oct 2026, without the weekends and without Tue 15 Sep (a holiday)
SESS = tuple(d for d in (date(2026, 9, 7) + timedelta(n) for n in range(26)) if d.weekday() < 5 and
             d != date(2026, 9, 15))
D_STAR = SESS[-1]                                            # Fri 2 Oct
T = datetime(2026, 10, 2, 18, 0, tzinfo=CMB)
EARLY = datetime(2026, 1, 1, tzinfo=UTC)
ALIASES = [al.Alias(1, "Alpha Bank PLC", "iss_alpha", "name", EARLY, EARLY),
           al.Alias(2, "Beta Holdings Limited", "iss_beta", "name", EARLY, EARLY),
           al.Alias(3, "Gamma", "iss_g1", "name", EARLY, EARLY), al.Alias(4, "Gamma", "iss_g2", "name", EARLY, EARLY)]
SOURCE = sn.Source("s1", datetime(2026, 9, 1, tzinfo=UTC), EARLY)


def at(day, hh=10, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=CMB)


def art(aid, title, text, when, source="s1", lang="en", origin="fetched", version=1):
    res = pr.process(title, text, lang, ALIASES)
    return sn.SnapArticle(aid, source, version, title, when, origin, lang, res)


def runs_all(source="s1", status="ran"):
    return [sn.IngestionRun(source, d, status, EARLY) for d in SESS]


def build(articles, sources=(SOURCE,), runs=None, query=None, issuers=("iss_alpha", "iss_beta", "iss_quiet"),
          sessions=SESS, cfg=None):
    return sn.build(query or Query(KNOWN, T), sessions, articles, list(sources),
                    runs_all() if runs is None else runs, cfg or cf.Config(), issuers)


def row(snap, subject):
    return next(r for r in snap.rows if r.subject == subject)


# ----------------------------------------------------------------------------------------------- decay vectors


def test_age_vectors():
    cases = [(at(D_STAR, 10), 0),                                            # fetched on D* before the close
             (at(D_STAR, 14, 30), 0),                                        # at the close: that session
             (at(D_STAR, 15), 0),                                            # after D*'s close, before T: floored 0
             (at(date(2026, 10, 1), 15), 0),                                 # after Thu's close: Fri, which is D*
             (at(date(2026, 10, 1), 9), 1),                                  # Thu before the close
             (at(date(2026, 9, 26), 11), 4),                                 # Saturday: Mon 28 Sep
             (at(date(2026, 9, 15), 11), 12),                                # the holiday: Wed 16 Sep
             (at(date(2026, 9, 14), 16), 12),                                # Mon after close: Wed 16 (holiday Tue)
             (datetime(2026, 9, 25, 3, 0, tzinfo=UTC), 5)]                   # 08:30 Colombo Fri 25 Sep
    for instant, expected in cases:
        assert dc.age(instant, SESS) == expected, instant
    with pytest.raises(ValueError):
        dc.age(datetime(2026, 9, 25, 3), SESS)


def test_weight_and_story_vectors():
    cfg = cf.Config()
    assert dc.weight(0, 5) == 1 and dc.weight(5, 5) == Decimal("0.5") and dc.weight(10, 5) == Decimal("0.25")
    # several events: the longest half-life (corporate_action 10 over earnings 5); relevance: the maximum
    s, rho, hl, e = dc.story_effective([0.8, 0.4], [0.5, 0.9], ["earnings", "corporate_action"], 10, cfg)
    assert (s, rho, hl) == (Decimal("0.6"), Decimal("0.9"), 10) and e == Decimal("0.27")      # 0.6 x 0.9 x 0.5
    s, rho, hl, e = dc.story_effective([1.0], [0.4], [], 2, cfg)                              # no event: other's 2
    assert hl == 2 and e == Decimal("0.2")
    assert dc.story_effective([], [0.4], ["earnings"], 1, cfg)[3] is None                      # unscored story


# ----------------------------------------------------------------------------------------------- the snapshot


def test_features_and_provenance_by_hand():
    a1 = art("s1:a1", "Alpha Bank PLC posts record profit", "Alpha Bank PLC posted record profit and strong growth.",
             at(date(2026, 9, 28)))                                          # age 4, earnings, score 1.0
    a2 = art("s2:a1", "Alpha Bank PLC posts record profit", "Alpha Bank PLC record profit drew strong gains.",
             at(date(2026, 9, 29)), source="s2")                             # same story key, other source
    a3 = art("s1:a2", "Alpha Bank PLC faces lawsuit", "Alpha Bank PLC faces a lawsuit and heavy losses in court.",
             at(date(2026, 9, 7)))                                           # age 18, legal
    snap = build([a1, a2, a3], sources=(SOURCE, sn.Source("s2", SOURCE.started_at, EARLY)),
                 runs=runs_all() + runs_all("s2"))
    r = row(snap, "iss_alpha")
    assert r.state == "fresh" and len(r.stories) == 2
    st = next(x for x in r.stories if len(x["articles"]) == 2)
    assert st["age"] == 4 and st["sources"] == ["s1", "s2"] and st["half_life"] == 5
    f = r.features
    assert f["news_count_5"] == (1, "fresh", None) and f["news_count_21"][0] == 2 and f["news_sources_21"][0] == 2
    assert f["news_recency"][0] == 4 and f["event_age_earnings"][0] == 4 and f["event_age_legal"][0] == 18
    # hand: story 1: s = 1.0, rho = max relevances, 2^(-4/5); story 2: s = -1.0 (loss, losses... negative)
    assert f["sent_pos_share"][0] == "0.500000000000" and f["sent_neg_share"][0] == "0.500000000000"
    assert snap.active_sources == ["s1", "s2"] and snap.processor_versions == [[cf.ENTITY, cf.RELEVANCE, cf.EVENT,
                                                                                cf.SENTIMENT]]
    assert r.row_hash == sn.row_hash(snap, r) and r.row_hash == row(build([a1, a2, a3], sources=(
        SOURCE, sn.Source("s2", SOURCE.started_at, EARLY)), runs=runs_all() + runs_all("s2")), "iss_alpha").row_hash


def test_no_news_is_missing_never_neutral():
    snap = build([art("s1:b", "Beta Holdings Ltd plant", "Beta Holdings Ltd opened a plant.", at(D_STAR))])
    q = row(snap, "iss_quiet")
    assert q.state == "missing" and q.features["sent_eff_mean"] == (None, "missing", None)
    assert q.features["news_count_21"] == (0, "missing", None) and q.features["news_count_5"][0] == 0
    b = row(snap, "iss_beta")
    assert b.state == "fresh" and b.features["sent_eff_mean"][0] is None     # an unscored story: no value
    assert b.features["news_count_5"][0] == 1


def test_quality_states_and_their_precedence():
    good = art("s1:a", "Alpha Bank PLC record profit", "Alpha Bank PLC posted record profit and strong growth.",
               at(date(2026, 9, 30)))
    assert row(build([good]), "iss_alpha").state == "fresh"
    # partial: a window session without a 'ran' row (also a failed or partial one); a later backlog does not clear it
    gappy = [r for r in runs_all() if r.day != date(2026, 9, 22)]
    assert row(build([good], runs=gappy), "iss_alpha").state == "partial"
    failed = runs_all()[:-1] + [sn.IngestionRun("s1", D_STAR, "failed", EARLY)]
    assert row(build([good], runs=failed), "iss_alpha").state == "partial"
    # stale: newest story older than the limit (10 sessions)
    old = art("s1:o", "Alpha Bank PLC record profit", "Alpha Bank PLC posted record profit and strong growth.",
              at(date(2026, 9, 10)))
    assert row(build([old]), "iss_alpha").state == "stale"
    # unlabelled: unsupported-language and result-less articles above the share; or mixed processor versions
    si = sn.SnapArticle("s1:si", "s1", 1, "x", at(D_STAR), "fetched", "si", pr.process("x", "y", "si", ALIASES))
    none = sn.SnapArticle("s1:n", "s1", 1, "x", at(D_STAR), "fetched", "en", None)
    snap = build([good, si, none])
    assert row(snap, "iss_alpha").state == "unlabelled" and snap.counts["unsupported_language"] == 1 and \
        snap.counts["without_result"] == 1
    mixed = sn.SnapArticle("s1:m", "s1", 1, good.title, good.instant, "fetched", "en",
                           pr.ArticleResult(("ns.entity.0",) + good.result.processors[1:], "x", "processed",
                                            good.result.issuers, [], {"wide": False, "events": []}))
    assert row(build([good, mixed]), "iss_alpha").state == "unlabelled"
    # conflicting: ambiguous mentions above the share; precedence over missing and partial
    gam = art("s1:g", "Gamma posts record profit", "Gamma posted strong growth.", at(D_STAR))
    snap = build([gam], runs=gappy, issuers=("iss_g1",))
    assert row(snap, "iss_g1").state == "conflicting" and row(snap, "iss_g1").features["sent_eff_mean"][0] is None
    # not_collected over everything
    assert row(build([gam, good], sources=()), "iss_alpha").state == "not_collected"


def test_nl16_a_source_starting_after_t_is_not_collected_never_zero_counts():
    late = sn.Source("s1", T + timedelta(days=1), EARLY)
    r = row(build([], sources=(late,)), "iss_quiet")
    assert r.state == "not_collected" and r.features["news_count_21"] == (None, "not_collected", None)
    # converse: started before T
    assert row(build([], sources=(SOURCE,)), "iss_quiet").features["news_count_21"][0] == 0
    # a start recorded after T in KNOWN is not active either; in AVAILABLE it is, once recorded by H
    rec_late = sn.Source("s1", SOURCE.started_at, T + timedelta(hours=1))
    assert row(build([], sources=(rec_late,)), "iss_quiet").state == "not_collected"
    assert row(build([], sources=(rec_late,), query=Query(AVAILABLE, T, T + timedelta(days=5))),
               "iss_quiet").state == "missing"


def test_nl6_the_configuration_in_force_at_the_knowledge_instant():
    d = [("cfg-a", T - timedelta(days=5)), ("cfg-b", T + timedelta(hours=1))]
    assert sn.configuration_in_force(d, Query(KNOWN, T)) == "cfg-a"
    assert sn.configuration_in_force(d, Query(AVAILABLE, T, T + timedelta(days=1))) == "cfg-b"      # by H
    assert sn.configuration_in_force(d, Query(KNOWN, T - timedelta(days=6))) is None


def test_nl7_a_session_after_t_cannot_enter_and_ages_follow_the_sequence_at_t():
    a = art("s1:a", "Alpha Bank PLC record profit", "Alpha Bank PLC posted record profit and strong growth.",
            at(date(2026, 9, 30)))
    with pytest.raises(ValueError, match="NL-7"):
        build([a], sessions=SESS + (date(2026, 10, 5),))
    # converse: at a later T, with the later session in the sequence, the story is one session older
    later_t = datetime(2026, 10, 5, 18, tzinfo=CMB)
    s_now = row(build([a]), "iss_alpha").stories[0]["age"]
    s_later = row(build([a], query=Query(KNOWN, later_t), sessions=SESS + (date(2026, 10, 5),),
                        runs=runs_all() + [sn.IngestionRun("s1", date(2026, 10, 5), "ran", EARLY)]),
                  "iss_alpha").stories[0]["age"]
    assert (s_now, s_later) == (2, 3)


def test_nl9_another_issuers_article_does_not_change_this_issuers_row():
    a = art("s1:a", "Alpha Bank PLC record profit", "Alpha Bank PLC posted record profit and strong growth.",
            at(date(2026, 9, 30)))
    b = art("s1:b", "Beta Holdings Ltd losses", "Beta Holdings Ltd reported heavy losses and a decline.",
            at(D_STAR))
    r1, r2 = row(build([a]), "iss_alpha"), row(build([a, b]), "iss_alpha")
    assert r1.features == r2.features and r1.stories == r2.stories
    assert row(build([a, b]), "iss_beta").features != row(build([a]), "iss_beta").features      # converse


def test_nl14_a_withdrawal_whose_confirming_refetch_is_after_t_in_available():
    v = ar.Version("s1", "a", 1, None, "Alpha Bank PLC record profit", None, "none", "en", "x", "y", None,
                   at(date(2026, 9, 30)), at(date(2026, 9, 30)), at(date(2026, 9, 30)), recorded_at=at(date(2026, 9, 30)))
    wd = ar.Observation("s1", "a", "withdrawn", T + timedelta(days=1), T + timedelta(days=1), "http_404_confirmed",
                        recorded_at=T + timedelta(days=1))
    q = Query(AVAILABLE, T, T + timedelta(days=10))
    assert (("s1", "a") in ar.in_force([v], [wd], q))                                     # still contributes at T
    q_after = Query(AVAILABLE, T + timedelta(days=2), T + timedelta(days=10))
    assert ("s1", "a") not in ar.in_force([v], [wd], q_after)                             # converse


def test_archive_articles_age_from_availability_and_mark_imported_inputs():
    a = art("s1:arc", "Alpha Bank PLC record profit", "Alpha Bank PLC posted record profit and strong growth.",
            at(date(2026, 9, 7)), origin="archive")                     # imported today, published long ago
    snap = build([a])
    assert snap.imported_inputs and row(snap, "iss_alpha").stories[0]["age"] == 18


def test_the_market_row():
    m = art("s1:m", "Colombo Stock Exchange gains", "The ASPI rose strongly with strong gains across the CSE.",
            at(D_STAR))
    snap = build([m])
    r = row(snap, "market")
    assert r.state == "fresh" and r.features["mkt_news_count_5"][0] == 1 and Decimal(r.features[
        "mkt_sent_eff_mean"][0]) > 0
    assert row(build([]), "market").state == "missing"


# ----------------------------------------------------------------------------------------------- NS-6/7 audit


GOOD_TEXT = "Alpha Bank PLC posted record profit and strong growth."


def test_major1_a_later_start_never_moves_a_sources_start_at_t():
    good = art("s1:a", "Alpha Bank PLC record profit", GOOD_TEXT, at(date(2026, 9, 30)))
    gappy = [r for r in runs_all() if r.day != date(2026, 9, 22)]
    restart_late = sn.Source("s1", datetime(2026, 9, 25, tzinfo=UTC), T + timedelta(hours=1))   # recorded after T
    restart_early = sn.Source("s1", datetime(2026, 9, 25, tzinfo=UTC), EARLY)                   # admitted restart
    for sources in ((SOURCE, restart_late), (restart_late, SOURCE), (SOURCE, restart_early), (restart_early, SOURCE)):
        assert row(build([good], sources=sources, runs=gappy), "iss_alpha").state == "partial", sources


def test_major3_a_backlog_run_recorded_after_t_never_clears_a_gap():
    good = art("s1:a", "Alpha Bank PLC record profit", GOOD_TEXT, at(date(2026, 9, 30)))
    backlog = [r for r in runs_all() if r.day != date(2026, 9, 22)] + [
        sn.IngestionRun("s1", date(2026, 9, 22), "ran", T + timedelta(hours=1))]
    assert row(build([good], runs=backlog), "iss_alpha").state == "partial"
    assert row(build([good], runs=backlog, query=Query(KNOWN, T + timedelta(hours=2))), "iss_alpha").state == "fresh"
    partial_run = [r for r in runs_all() if r.day != date(2026, 9, 22)] + [
        sn.IngestionRun("s1", date(2026, 9, 22), "partial", EARLY)]
    assert row(build([good], runs=partial_run), "iss_alpha").state == "partial"     # a partial run is a gap too


def test_major4_feature_values_by_hand():
    cfg = cf.Config()
    a1 = art("s1:a1", "Alpha Bank PLC posts record profit", GOOD_TEXT, at(date(2026, 9, 28)))        # age 4
    a2 = art("s1:a2", "Alpha Bank PLC record results again", "Alpha Bank PLC profit rose with strong gains.",
             at(date(2026, 9, 23)))                                                                   # age 7
    snap = build([a1, a2])
    r = row(snap, "iss_alpha")
    w = [Decimal(s["effective"]) for s in sorted(r.stories, key=lambda s: s["age"])]
    # by hand: s = 1.0 each; rho = each article's relevance; weight 2^(-age / 5) (earnings)
    rel = [Decimal(str(a.result.issuers["iss_alpha"]["relevance"])) for a in (a1, a2)]
    assert w[0] == (rel[0] * dc.weight(4, 5)).quantize(Decimal("0.000000000001"))
    assert w[1] == (rel[1] * dc.weight(7, 5)).quantize(Decimal("0.000000000001"))
    assert Decimal(r.features["sent_eff_sum"][0]) == w[0] + w[1]
    assert Decimal(r.features["sent_eff_mean"][0]) == ((w[0] + w[1]) / 2).quantize(Decimal("0.000000000001"))
    assert r.features["event_age_earnings"][0] == 4                     # the NEWEST age of the event type
    assert r.features["news_count_5"][0] == 1 and r.features["news_count_21"][0] == 2


def test_major4_boundaries():
    mk = lambda aid, title, when, src="s1": art(aid, title, GOOD_TEXT, when, source=src)      # noqa: E731
    # count_5: age 4 counts, age 5 does not; sources_21: age 20 counts; max_age 21: age 21 is out of the window
    s5 = row(build([mk("s1:b", "Alpha Bank PLC five", at(date(2026, 9, 25)))]), "iss_alpha")       # age 5
    assert s5.stories[0]["age"] == 5 and s5.features["news_count_5"][0] == 0
    two = (SOURCE, sn.Source("s2", SOURCE.started_at, EARLY))
    s20 = row(build([mk("s2:c", "Alpha Bank PLC twenty", at(date(2026, 9, 8)), "s2")], sources=two,
                    runs=runs_all() + runs_all("s2")), "iss_alpha")
    assert s20.stories[0]["age"] == 17 and s20.features["news_sources_21"][0] == 1
    long_seq = tuple(d for d in (date(2026, 8, 24) + timedelta(n) for n in range(40)) if d.weekday() < 5
                     and d <= D_STAR)
    runs_long = [sn.IngestionRun("s1", d, "ran", EARLY) for d in long_seq]
    src_long = sn.Source("s1", datetime(2026, 8, 1, tzinfo=UTC), EARLY)
    edge = {a: long_seq[-1 - a] for a in (20, 21)}
    r20 = row(build([mk("s1:e", "Alpha Bank PLC edge", at(edge[20]))], sources=(src_long,), runs=runs_long,
                    sessions=long_seq), "iss_alpha")
    r21 = row(build([mk("s1:f", "Alpha Bank PLC edge", at(edge[21]))], sources=(src_long,), runs=runs_long,
                    sessions=long_seq), "iss_alpha")
    assert r20.features["news_count_21"][0] == 1 and r21.state == "missing"
    # the 14:30 close on an earlier session
    assert dc.age(at(date(2026, 10, 1), 14, 30), SESS) == 1 and dc.age(at(date(2026, 10, 1), 14, 31), SESS) == 0
    # an instant before the sequence is outside every window, never mapped onto its first session
    assert dc.age(at(date(2025, 6, 1)), SESS) == dc.BEFORE_SEQUENCE
    # stale: newest age 10 (= the limit) is fresh, 11 is stale
    assert row(build([mk("s1:g", "Alpha Bank PLC ten", at(SESS[-11]))]), "iss_alpha").state == "fresh"
    assert row(build([mk("s1:h", "Alpha Bank PLC eleven", at(SESS[-12]))]), "iss_alpha").state == "stale"
    # a source starting on a window session: no gap before its start day
    late_start = sn.Source("s1", at(date(2026, 9, 28), 8), EARLY)
    runs_from = [r for r in runs_all() if r.day >= date(2026, 9, 28)]
    assert row(build([mk("s1:i", "Alpha Bank PLC start", at(D_STAR))], sources=(late_start,), runs=runs_from),
               "iss_alpha").state == "fresh"
    # the positive-label threshold, exactly 0.2: 3 positive and 2 negative terms
    at02 = art("s1:j", "Alpha Bank PLC mixed", "Alpha Bank PLC profit growth gains, weak, loss.", at(D_STAR))
    assert row(build([at02]), "iss_alpha").features["sent_pos_share"][0] == "1.000000000000"


def test_major4_precedence_of_adjacent_states():
    good = art("s1:a", "Alpha Bank PLC record profit", GOOD_TEXT, at(date(2026, 9, 30)))
    old = art("s1:o", "Alpha Bank PLC record profit", GOOD_TEXT, at(date(2026, 9, 10)))
    gappy = [r for r in runs_all() if r.day != date(2026, 9, 22)]
    si = sn.SnapArticle("s1:si", "s1", 1, "x", at(D_STAR), "fetched", "si", pr.process("x", "y", "si", ALIASES))
    none = sn.SnapArticle("s1:n", "s1", 1, "x", at(D_STAR), "fetched", "en", None)
    assert row(build([good, si, none], runs=gappy), "iss_alpha").state == "unlabelled"   # unlabelled over partial
    assert row(build([old], runs=gappy), "iss_alpha").state == "partial"                 # partial over stale
    gam = art("s1:g", "Gamma posts record profit", "Gamma posted strong growth.", at(D_STAR))
    assert row(build([gam, si, none], issuers=("iss_g1",)), "iss_g1").state == "conflicting"   # over unlabelled
    assert row(build([], sources=(sn.Source("s1", T + timedelta(days=1), EARLY),), issuers=("iss_g1",)),
               "iss_g1").state == "not_collected"                                        # over missing


# ----------------------------------------------------------------------------------------------- NS-6/7 re-audit


def _long():
    seq = tuple(d for d in (date(2026, 8, 24) + timedelta(n) for n in range(40)) if d.weekday() < 5 and d <= D_STAR)
    srcs = tuple(sn.Source(s, datetime(2026, 8, 1, tzinfo=UTC), EARLY) for s in ("s1", "s2"))
    runs = [sn.IngestionRun(s, d, "ran", EARLY) for s in ("s1", "s2") for d in seq]
    return seq, srcs, runs


def test_reaudit_minor1_news_sources_21_boundary():
    seq, srcs, runs = _long()
    for a, counted in ((20, 1), (21, 0)):
        x = art(f"s2:{a}", "Alpha Bank PLC posts record profit", GOOD_TEXT, at(seq[-1 - a]), source="s2")
        r = row(build([x], sources=srcs, runs=runs, sessions=seq), "iss_alpha")
        assert r.features["news_sources_21"][0] == counted, (a, r.features["news_sources_21"])
        assert (r.features["event_age_earnings"][0] == 20) if counted else r.state == "missing"


def test_reaudit_minor2_precedence_and_the_start_day():
    gam = art("s1:g", "Gamma posts record profit", "Gamma posted strong growth.", at(D_STAR))
    late = sn.Source("s1", T + timedelta(days=1), EARLY)
    assert row(build([gam], sources=(late,), issuers=("iss_g1",)), "iss_g1").state == "not_collected"
    si = sn.SnapArticle("s1:si", "s1", 1, "x", at(D_STAR), "fetched", "si", pr.process("x", "y", "si", ALIASES))
    none = sn.SnapArticle("s1:n", "s1", 1, "x", at(D_STAR), "fetched", "en", None)
    assert row(build([si, none]), "iss_alpha").state == "missing"                     # missing over unlabelled
    good = art("s1:a", "Alpha Bank PLC record profit", GOOD_TEXT, at(D_STAR))
    start = sn.Source("s1", at(date(2026, 9, 28), 8), EARLY)
    no_run_on_start = [r for r in runs_all() if r.day > date(2026, 9, 28)]
    assert row(build([good], sources=(start,), runs=no_run_on_start), "iss_alpha").state == "partial"
