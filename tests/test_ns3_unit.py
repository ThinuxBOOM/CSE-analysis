"""
Phase 6 NS-3 to NS-5 (design §5, §6, §13): aliases and entity identification, relevance and events, sentiment and
deduplication; leakage tests NL-5, NL-11, NL-12, NL-13 and NL-15 with their converses. Hand-built synthetic articles
about invented issuers only.
"""
from datetime import datetime, timedelta, timezone

import pytest

from worker.market_features.modes import AVAILABLE, KNOWN, Query
from worker.news_ingest import articles as ar
from worker.news_sentiment import aliases as al
from worker.news_sentiment import config as cf
from worker.news_sentiment import dedup as dd
from worker.news_sentiment import process as pr
from worker.news_sentiment import results as rs

UTC = timezone.utc
T = datetime(2026, 9, 10, 12, tzinfo=UTC)
H = T + timedelta(days=30)
EARLY = T - timedelta(days=30)


def alias(i, text, issuer, kind="name", rec=EARLY, avail=EARLY):
    return al.Alias(i, text, issuer, kind, rec, avail, "synthetic")


ALIASES = [alias(1, "Alpha Bank PLC", "iss_alpha"), alias(2, "ALPH.N0000", "iss_alpha", "ticker"),
           alias(3, "Beta Holdings Limited", "iss_beta"), alias(4, "Gamma", "iss_gamma"),
           alias(5, "Gamma", "iss_gamma2"), alias(6, "Alpha", "iss_alpha_short", "owner"),
           alias(7, "LION.N0000", "iss_lion", "ticker")]


def run(title, text, language="en", aliases=ALIASES, full_text=False):
    return pr.process(title, text, language, aliases, full_text=full_text)


# ----------------------------------------------------------------------------------------------- NS-3: entities


def test_exact_matching_suffixes_and_positions():
    r = run("Alpha Bank PLC lifts profit", "Alpha Bank Public Limited Company said results rose.\n\n"
            "Analysts covered ALPH.N0000 and Beta Holdings Ltd in the body.")
    a = r.issuers["iss_alpha"]
    assert a["mentions"] == 3 and a["positions"] == ["body", "first_paragraph", "title"]   # suffixes normalised
    assert r.issuers["iss_beta"]["positions"] == ["body"]
    assert "iss_alpha_short" not in r.issuers                     # "Alpha Bank PLC" is the longer, overlapping match
    assert r.alias_ids == (1, 2, 3)


def test_ambiguity_is_kept_and_scores_neither_issuer():
    r = run("Gamma reports strong growth", "Gamma posted record profit and strong gains.")
    assert r.ambiguous == [{"text": "gamma", "candidates": ["iss_gamma", "iss_gamma2"]}] * 2
    assert "iss_gamma" not in r.issuers and "iss_gamma2" not in r.issuers


def test_a_ticker_that_is_a_word_matches_only_in_full_form():
    r = run("The lion share", "A lion and LION.N0000 shares rose on strong gains.")
    assert r.issuers["iss_lion"]["mentions"] == 1                 # "lion" the word is not the issuer
    r = run("The lion share", "A lion roared.", aliases=ALIASES + [alias(8, "lion", "iss_lion", "owner")])
    assert r.issuers["iss_lion"]["mentions"] == 2                 # only when an owner alias says so


def test_word_boundaries_and_no_fuzzy_matching():
    r = run("Alphabet news", "Alpha Banks and AlphaBank PLC are not Alpha Bank PLC.")
    assert r.issuers["iss_alpha"]["mentions"] == 1                # only the exact name
    assert "iss_alpha_short" in r.issuers                         # "Alpha" before "Banks" (the owner's short alias)


# NL-5, NL-13, NL-15: aliases by their own clocks


def test_nl5_nl13_nl15_aliases_and_alias_ends_by_their_own_clocks():
    late = alias(9, "Delta Finance", "iss_delta", rec=T + timedelta(hours=1), avail=T - timedelta(days=1))
    q_known, q_avail = Query(KNOWN, T), Query(AVAILABLE, T, H)
    assert late not in al.admitted([late], [], q_known)                     # NL-5: recorded after T
    assert late in al.admitted([late], [], q_avail)                         # announced before T, known by H
    future = alias(10, "Delta Finance", "iss_delta", rec=EARLY, avail=T + timedelta(days=1))
    assert future not in al.admitted([future], [], q_avail)                 # NL-13: available after T
    assert future in al.admitted([future], [], q_known)                     # KNOWN goes by recorded_at
    end_late = al.AliasEnd(4, T + timedelta(hours=1), T - timedelta(days=2))
    assert alias(4, "Gamma", "iss_gamma") in al.admitted(ALIASES, [end_late], q_known)      # NL-15
    assert alias(4, "Gamma", "iss_gamma") not in al.admitted(ALIASES, [end_late], q_avail)  # converse
    assert alias(4, "Gamma", "iss_gamma") not in al.admitted(ALIASES, [end_late],
                                                             Query(KNOWN, T + timedelta(hours=2)))
    # once ended, Gamma is no longer ambiguous
    r = run("Gamma posts record profit", "Gamma reported strong growth.",
            aliases=al.admitted(ALIASES, [end_late], Query(KNOWN, T + timedelta(hours=2))))
    assert r.issuers["iss_gamma2"]["mentions"] == 2 and r.ambiguous == []


# ----------------------------------------------------------------------------------------------- NS-4: relevance,
# events


def test_relevance_components():
    cfg = cf.Config()
    r = run("Alpha Bank PLC declares dividend", "The board approved it.\n\nBeta Holdings Ltd was mentioned.")
    a, b = r.issuers["iss_alpha"], r.issuers["iss_beta"]
    assert a["relevance"] == pytest.approx(cfg.w_title + cfg.w_share * 0.5 + cfg.w_event)
    assert b["relevance"] == pytest.approx(cfg.w_body + cfg.w_share * 0.5)
    assert [e["type"] for e in a["events"]] == ["dividend"] and b["events"] == []


@pytest.mark.parametrize("phrase,etype", [("reported higher net income", "earnings"), ("declared an interim dividend",
                                          "dividend"), ("announced a rights issue", "corporate_action"),
                                          ("appointed a new chief executive", "management_change"),
                                          ("faces a lawsuit in court", "legal"), ("received a credit rating", "rating"),
                                          ("agreed a merger", "merger_acquisition"),
                                          ("issued debentures", "capital_raise"), ("opened a new factory",
                                                                                   "operations"),
                                          ("met the regulator", "regulatory")])
def test_each_event_rule(phrase, etype):
    r = run("Update", f"Beta Holdings Ltd {phrase}.")
    assert etype in [e["type"] for e in r.issuers["iss_beta"]["events"]]


def test_events_need_proximity_and_excerpts_only_under_full_text():
    far = "Beta Holdings Ltd met investors. " + "word " * 40 + "Elsewhere a dividend was paid."
    assert run("Update", far).issuers["iss_beta"]["events"] == []
    near = "Beta Holdings Ltd declared a final dividend."
    assert run("Update", near).issuers["iss_beta"]["events"][0]["excerpt"] is None
    assert "dividend" in run("Update", near, full_text=True).issuers["iss_beta"]["events"][0]["excerpt"]
    r = run("Central Bank raises policy rate", "The central bank raised the policy rate to fight inflation.")
    assert r.market["wide"] and {e["type"] for e in r.market["events"]} == {"macro_policy"}


# ----------------------------------------------------------------------------------------------- NS-5: sentiment


def test_sentiment_by_hand():
    # positive: "record", "profit", "strong", "growth" -> 4 terms, +4 / 4 = 1.0
    r = run("x", "Alpha Bank PLC posted record profit and strong growth.")
    assert (r.issuers["iss_alpha"]["sentiment"], r.issuers["iss_alpha"]["label"]) == (1.0, "positive")
    # negation: "not" before "strong" flips it: +1 (profit) -1 (strong) = 0 / 2
    r = run("x", "Alpha Bank PLC profit was not strong.")
    assert (r.issuers["iss_alpha"]["sentiment"], r.issuers["iss_alpha"]["label"]) == (0.0, "neutral")
    # intensifier: "sharply lower" = -1.5, "loss" = -1: -2.5 / 2 clamped to -1
    r = run("x", "Alpha Bank PLC posted a loss and sharply lower income.")
    assert r.issuers["iss_alpha"]["sentiment"] == -1.0
    # "slightly higher" 0.5, "decline" -1: -0.5 / 2 = -0.25
    r = run("x", "Alpha Bank PLC shares were slightly higher after a decline.")
    assert r.issuers["iss_alpha"]["sentiment"] == pytest.approx(-0.25) and r.issuers["iss_alpha"]["label"] == \
        "negative"


def test_unscored_is_not_neutral_and_language_counts():
    r = run("x", "Alpha Bank PLC held its meeting.")
    a = r.issuers["iss_alpha"]
    assert (a["sentiment"], a["label"], a["scored_terms"]) == (None, "unscored", 0)
    r = run("x", "Alpha Bank PLC profit.")                        # one term, below the minimum of two
    assert r.issuers["iss_alpha"]["label"] == "unscored"
    si = run("ඇල්ෆා", "ඇල්ෆා බැංකුව", language="si")
    assert si.state == "language_unsupported" and si.issuers == {} and si.market == {}


def test_market_wide_sentiment():
    r = run("CSE indices", "The ASPI rose strongly with strong gains across the Colombo Stock Exchange.")
    assert r.market["wide"] and r.market["label"] == "positive" and r.issuers == {}


# ----------------------------------------------------------------------------------------------- NS-5: dedup


def test_story_keys_and_ids_per_snapshot():
    m = lambda aid, src, title, iss, h: dd.Member(aid, src, title, tuple(iss), T - timedelta(hours=h))   # noqa
    a = m("s1:a", "s1", "Alpha Bank PLC posts record profit", ["iss_alpha"], 5)
    b = m("s2:b", "s2", "Record profit: Alpha Bank PLC posts", ["iss_alpha"], 3)       # same tokens, other source
    c = m("s1:c", "s1", "Alpha Bank PLC posts record profit", ["iss_alpha", "iss_beta"], 4)   # other issuer set
    st = dd.stories([a, b, c])
    assert len(st) == 2
    s = next(x for x in st if len(x.members) == 2)
    assert s.instant == a.instant and s.sources == ["s1", "s2"] and s.story_id.endswith(":s1:a")
    assert dd.story_key("The Alpha Bank PLC: posts a record profit", ["iss_alpha"]) == s.key   # stop list


def test_nl12_a_later_article_never_changes_grouping_or_ids_at_t():
    m = lambda aid, title, h: dd.Member(aid, "s1", title, ("iss_alpha",), T + timedelta(hours=h))   # noqa
    early = [m("s1:x", "Alpha Bank PLC record profit", -5), m("s1:y", "Alpha Bank PLC new plant", -4)]
    before = {(s.story_id, tuple(x.article_id for x in s.members)) for s in dd.stories(early)}
    late = m("s1:z", "Alpha Bank PLC record profit", +1)                  # recorded after T: not admitted at T
    admitted_at_t = [x for x in early + [late] if x.instant <= T]
    assert {(s.story_id, tuple(x.article_id for x in s.members)) for s in dd.stories(admitted_at_t)} == before
    after = dd.stories(early + [late])                                    # converse: once admitted, it joins
    assert any(len(s.members) == 2 for s in after)
    assert {s.story_id for s in after} == {s for s, _m in before}         # ids keep the earliest member


# ----------------------------------------------------------------------------------------------- NL-11


def test_nl11_results_by_their_own_clock_and_their_aliases():
    cfg = cf.Config()
    v = ar.Version("s1", "a1", 1, None, "Delta Finance posts record profit", None, "none", "en", "x", "y",
                   "Delta Finance reported strong growth.", T - timedelta(days=1), T - timedelta(days=1),
                   T - timedelta(days=1), recorded_at=T - timedelta(days=1))
    delta = alias(20, "Delta Finance", "iss_delta", rec=T + timedelta(hours=1), avail=T - timedelta(days=5))
    late_result = rs.StoredResult("s1", "a1", 1, cfg.configuration_id(),
                                  pr.process(v.title, v.extracted_text, "en", [delta], cfg), T + timedelta(hours=2))
    meta = ar.replace(v, extracted_text=None)
    # a result computed after T (and with an alias recorded after T) is not used in KNOWN; metadata_only: none
    assert rs.admitted_result(meta, [late_result], [delta], [], Query(KNOWN, T), cfg) == (None, "none")
    # full_text: recomputed with the aliases admitted at T (Delta not yet): no issuer
    got, how = rs.admitted_result(v, [late_result], [delta], [], Query(KNOWN, T), cfg)
    assert how == "recomputed" and "iss_delta" not in got.issuers
    # converse: known by H in AVAILABLE, and its alias admitted there
    got, how = rs.admitted_result(meta, [late_result], [delta], [], Query(AVAILABLE, T, H), cfg)
    assert how == "stored" and got.issuers["iss_delta"]["label"] == "positive"
    # a stored result whose alias set is not admitted at T is not used, even if the result itself is
    early_result = rs.replace(late_result, recorded_at=T - timedelta(hours=1)) if hasattr(rs, "replace") else \
        rs.StoredResult("s1", "a1", 1, cfg.configuration_id(), late_result.result, T - timedelta(hours=1))
    assert rs.admitted_result(meta, [early_result], [delta], [], Query(KNOWN, T), cfg) == (None, "none")
    # another configuration's result is not used (NL-6's pinning)
    other = rs.StoredResult("s1", "a1", 1, "0" * 64, late_result.result, T - timedelta(hours=1))
    assert rs.admitted_result(meta, [other], [], [], Query(KNOWN, T), cfg) == (None, "none")
