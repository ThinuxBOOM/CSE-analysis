"""
§8: the sentiment snapshot (NS-6), per issuer at (T, mode) and one market-wide row. Pure: the store hands it what is
admitted at T (article versions in force, their admissible results, the active sources, the ingestion runs, MF's
session sequence at T) and writes what it returns.

Features (each with a quality state; `event_flags` is one numeric age per event type, null where none):
  sent_eff_sum, sent_eff_mean     sum and mean of `effective` over the issuer's scored stories
  sent_pos_share, sent_neg_share  shares of scored stories by the label of the story's mean score
  news_count_5, news_count_21     stories with age < 5, < 21 sessions
  news_sources_21                 distinct sources of the stories in the last 21 sessions
  news_recency                    sessions since the newest story
  event_flags                     per event type, the newest age within 21 sessions
  mkt_sent_eff_mean, mkt_news_count_5   the same for market-wide stories (the market row)

**No news is not neutral** (P6-D7): with no admitted story, sentiment features are `missing` and counts are 0.
**Not collected is not "no news"** (N4): with no source active at T every feature, counts included, is null with
`not_collected`; after a source's start, a window session with no `ns_ingestion_runs` row (or a failed or partial one)
for an active source makes the row `partial` (a backlog fetched later does not clear it).

Quality states, in Phase 3 §10.4's precedence: not_collected; untrusted (unused in v1); conflicting (ambiguous
mentions of the issuer above the policy's share; value null); missing (no admitted story; value null); unlabelled
(mixed processor versions in the window, or unsupported-language articles and articles without an admissible result
above the policy's share of the admitted articles: such articles cannot be attributed to an issuer, so the share is
the snapshot's and marks every row); partial; stale (newest story older than the policy's limit); fresh.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from worker.market_features import numeric
from worker.market_features.canonical import canonical_json

from . import config as cf
from . import decay as dc
from . import dedup as dd

ISSUER_FEATURES = ("sent_eff_sum", "sent_eff_mean", "sent_pos_share", "sent_neg_share", "news_count_5",
                   "news_count_21", "news_sources_21", "news_recency") + tuple(f"event_age_{e}" for e in cf.EVENT_TYPES)
MARKET_FEATURES = ("mkt_sent_eff_mean", "mkt_news_count_5")
COUNTS = ("news_count_5", "news_count_21", "news_sources_21", "mkt_news_count_5")


@dataclass(frozen=True)
class SnapArticle:
    """An admitted article version in force at T, with its admissible result (None: none) for the snapshot."""
    article_id: str                     # "<source_id>:<reference>"
    source_id: str
    version_no: int
    title: str
    instant: datetime                   # its first availability (§7's age clock)
    origin: str                         # fetched | archive
    language: str
    result: object = None               # process.ArticleResult or None


@dataclass(frozen=True)
class Source:
    source_id: str
    started_at: datetime                # ingestion start (its available_at)
    recorded_at: datetime


@dataclass(frozen=True)
class IngestionRun:
    source_id: str
    day: date
    status: str                         # ran | partial | failed
    recorded_at: datetime


@dataclass
class Row:
    subject: str                        # an issuer id, or "market"
    state: str
    features: dict                      # {feature: (value or None, state, reason)}
    stories: list = field(default_factory=list)          # [{"story_id", "articles": [...], "age", ...}]
    row_hash: str = ""


@dataclass
class Snapshot:
    mode: str
    cutoff: datetime
    horizon: Optional[datetime]
    d_star: Optional[date]
    configuration_id: str
    active_sources: list
    processor_versions: list
    imported_inputs: bool
    counts: dict
    rows: list


def _active(sources, query):
    """Sources approved and with ingestion started by T under the mode (N4): the start record admitted at the query,
    and the start itself at or before T (in KNOWN as well: a source recorded early that starts after T is not yet
    collecting at T)."""
    return sorted(s.source_id for s in sources if query.admits(s.recorded_at, s.started_at)
                  and (query.cutoff is None or s.started_at <= query.cutoff))


def _gaps(active, sources, runs, query, window_sessions):
    """Sessions in the window, on or after a source's start day, with no 'ran' run row admitted at T."""
    start = {s.source_id: s.started_at.astimezone(dc.COLOMBO).date() for s in sources}
    ran = {(r.source_id, r.day) for r in runs if r.status == "ran" and query.admits(r.recorded_at, r.recorded_at)}
    return sorted((sid, d) for sid in active for d in window_sessions if d >= start[sid] and (sid, d) not in ran)


def _feat(value, state, reason=None):
    return (value, state, reason)


def build(query, sessions, articles, sources, runs, cfg=None, issuers=()):
    """The snapshot at the query. `sessions`: MF's final sessions up to D* (ascending, the sequence at T);
    `articles`: [SnapArticle] admitted at T; `issuers`: the issuer ids to report (every issuer of the universe, so
    an issuer nobody wrote about gets a `missing` row)."""
    cfg = cfg or cf.Config()
    sessions = tuple(sessions)
    if query.cutoff is not None and sessions and sessions[-1] > query.cutoff.astimezone(dc.COLOMBO).date():
        raise ValueError("the session sequence holds a session after T: it must be MF's sequence as of T (NL-7)")
    if list(sessions) != sorted(set(sessions)):
        raise ValueError("the session sequence is ascending and has no repeats")
    active = _active(sources, query)
    d_star = sessions[-1] if sessions else None
    window = sessions[-cfg.max_age_sessions:]
    gaps = _gaps(active, sources, runs, query, window)
    in_window = [a for a in articles if dc.age(a.instant, sessions) < cfg.max_age_sessions] if sessions else []
    unsupported = sum(1 for a in in_window if a.language != "en")
    no_result = sum(1 for a in in_window if a.language == "en" and a.result is None)
    versions = sorted({tuple(a.result.processors) for a in in_window if a.result is not None
                       and a.result.state == "processed"})
    with_result = [a for a in in_window if a.result is not None and a.result.state == "processed"]
    unlabelled = (len(versions) > 1 or (in_window and (unsupported + no_result) / len(in_window)
                                         > cfg.unlabelled_share))
    counts = {"admitted_articles": len(in_window), "unsupported_language": unsupported, "without_result": no_result,
              "ingestion_gaps": len(gaps)}
    rows = []
    subjects = sorted(set(issuers) | {i for a in with_result for i in a.result.issuers})
    for issuer in subjects:
        rows.append(_issuer_row(issuer, with_result, sessions, cfg, active, gaps, unlabelled))
    rows.append(_market_row(with_result, sessions, cfg, active, gaps, unlabelled))
    snap = Snapshot(query.mode, query.cutoff, query.horizon, d_star, cfg.configuration_id(), active,
                    [list(v) for v in versions], any(a.origin == "archive" for a in in_window), counts, rows)
    for r in rows:
        r.row_hash = row_hash(snap, r)
    return snap


def _state(active, gaps, conflicting, has_stories, unlabelled, newest_age, cfg):
    if not active:
        return "not_collected"
    if conflicting:
        return "conflicting"
    if not has_stories:
        return "missing"
    if unlabelled:
        return "unlabelled"
    if gaps:
        return "partial"
    if newest_age is not None and newest_age > cfg.stale_sessions:
        return "stale"
    return "fresh"


def _label(score, cfg):
    return "positive" if score >= Decimal(str(cfg.positive_threshold)) else \
        "negative" if score <= Decimal(str(cfg.negative_threshold)) else "neutral"


def _issuer_row(issuer, articles, sessions, cfg, active, gaps, unlabelled):
    mine = [a for a in articles if issuer in a.result.issuers]
    amb = sum(1 for a in articles for x in a.result.ambiguous if issuer in x["candidates"])
    clear = sum(a.result.issuers[issuer]["mentions"] for a in mine)
    conflicting = amb > 0 and amb / (amb + clear) > cfg.conflicting_share
    members = [dd.Member(a.article_id, a.source_id, a.title, tuple(sorted(a.result.issuers)), a.instant) for a in mine]
    by_id = {a.article_id: a for a in mine}
    stories = dd.stories(members)
    detail, eff, labels, ages, sources21, events = [], [], [], [], set(), {}
    for st in stories:
        a = dc.age(st.instant, sessions)
        res = [by_id[m.article_id].result.issuers[issuer] for m in st.members]
        scores = [r["sentiment"] for r in res if r["label"] != "unscored"]
        etypes = [e["type"] for r in res for e in r["events"]]
        s, rho, hl, effective = dc.story_effective(scores, [r["relevance"] for r in res], etypes, a, cfg)
        ages.append(a)
        if a < 21:
            sources21 |= set(st.sources)
            for e in etypes:
                events[e] = min(events.get(e, a), a)
        if effective is not None:
            eff.append(effective)
            labels.append(_label(s, cfg))
        detail.append({"story_id": st.story_id, "articles": [m.article_id for m in st.members],
                       "versions": [by_id[m.article_id].version_no for m in st.members], "age": a,
                       "sources": st.sources, "score": None if s is None else str(s), "relevance": str(rho),
                       "half_life": hl, "effective": None if effective is None else str(effective),
                       "events": sorted(set(etypes))})
    state = _state(active, gaps, conflicting, bool(stories), unlabelled, min(ages) if ages else None, cfg)
    f = {}
    if state == "not_collected":
        f = {k: _feat(None, "not_collected") for k in ISSUER_FEATURES}
    else:
        null = state in ("conflicting", "missing")
        sent_state = state
        f["sent_eff_sum"] = _feat(None if null or not eff else str(numeric.q(numeric.add(*eff))), sent_state,
                                  "no_scored_story" if not eff and not null else None)
        f["sent_eff_mean"] = _feat(None if null or not eff else str(numeric.q(numeric.mean(eff))), sent_state)
        f["sent_pos_share"] = _feat(None if null or not labels else str(numeric.q(
            Decimal(labels.count("positive")) / Decimal(len(labels)))), sent_state)
        f["sent_neg_share"] = _feat(None if null or not labels else str(numeric.q(
            Decimal(labels.count("negative")) / Decimal(len(labels)))), sent_state)
        count_state = "missing" if state == "missing" else state
        f["news_count_5"] = _feat(sum(1 for a in ages if a < 5), count_state)
        f["news_count_21"] = _feat(sum(1 for a in ages if a < 21), count_state)
        f["news_sources_21"] = _feat(len(sources21), count_state)
        f["news_recency"] = _feat(min(ages) if ages else None, state)
        for e in cf.EVENT_TYPES:
            f[f"event_age_{e}"] = _feat(events.get(e), state)
    return Row(issuer, state, f, detail)


def _market_row(articles, sessions, cfg, active, gaps, unlabelled):
    mkt = [a for a in articles if a.result.market.get("wide")]
    members = [dd.Member(a.article_id, a.source_id, a.title, ("market",), a.instant) for a in mkt]
    by_id = {a.article_id: a for a in mkt}
    eff, ages, detail = [], [], []
    for st in dd.stories(members):
        a = dc.age(st.instant, sessions)
        ms = [by_id[m.article_id].result.market for m in st.members]
        scores = [m["sentiment"] for m in ms if m["label"] != "unscored"]
        s, rho, hl, effective = dc.story_effective(scores, [1], [e["type"] for m in ms for e in m["events"]], a, cfg)
        ages.append(a)
        if effective is not None:
            eff.append(effective)
        detail.append({"story_id": st.story_id, "articles": [m.article_id for m in st.members], "age": a,
                       "effective": None if effective is None else str(effective)})
    state = _state(active, gaps, False, bool(detail), unlabelled, min(ages) if ages else None, cfg)
    if state == "not_collected":
        f = {k: _feat(None, "not_collected") for k in MARKET_FEATURES}
    else:
        f = {"mkt_sent_eff_mean": _feat(None if state == "missing" or not eff else str(numeric.q(numeric.mean(eff))),
                                        state),
             "mkt_news_count_5": _feat(sum(1 for a in ages if a < 5), state)}
    return Row("market", state, f, detail)


def row_hash(snap, row):
    body = {"mode": snap.mode, "cutoff": snap.cutoff.isoformat() if snap.cutoff else None,
            "horizon": snap.horizon.isoformat() if snap.horizon else None,
            "d_star": snap.d_star.isoformat() if snap.d_star else None, "configuration_id": snap.configuration_id,
            "active_sources": snap.active_sources, "subject": row.subject, "state": row.state,
            "features": {k: list(v) for k, v in sorted(row.features.items())}, "stories": row.stories}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def configuration_in_force(designations, query):
    """NL-6: the configuration designated at the query's knowledge instant (T in KNOWN, H otherwise):
    designations [(configuration_id, recorded_at)]; the latest recorded at or before it, or None."""
    at = query.cutoff if query.mode == "KNOWN" else query.horizon
    before = [d for d in designations if d[1] <= at]
    return max(before, key=lambda d: d[1])[0] if before else None
