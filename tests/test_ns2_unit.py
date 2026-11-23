"""
Phase 6 NS-2 (design §3, §4.2, §4.3, §9, §13): the adapter interface over the governed transport, article versions
by normalised text, observations with their own clocks, withdrawals on evidence only, the re-fetch policy, and the
version in force at T; leakage tests NL-1 to NL-4, each with its converse. Synthetic sources only (`.test` hosts).
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import test_ns1_unit as n1
from worker.market_features.modes import AVAILABLE, CURRENT, KNOWN, Query
from worker.news_ingest import adapter as ad
from worker.news_ingest import articles as ar
from worker.news_ingest import transport as tp

UTC = timezone.utc
T0 = n1.T0
HOST = n1.HOST
LIST = f"https://{HOST}/list.json"
SRC = "synthetic_news"


def item_json(title="Bank posts record profit", text="Alpha Bank PLC reported higher profit.", published="2026-09-01T02:00:00+00:00",
              language="en", removed=False):
    return json.dumps({"title": title, "published_at": published, "language": language, "text": text,
                       "removed": removed}).encode()


def synthetic(script):
    t, sender, slept = n1.transport({n1.ROBOTS: n1.robots_open() * 3, **script})
    return ad.SyntheticAdapter(t, LIST), t, sender


def stamped(v, recorded):
    return ar.replace(v, recorded_at=recorded)


def raw(ref="a1", text="Alpha Bank PLC reported higher profit.", at=T0, body=b"x"):
    return ar.RawItem(ref, f"https://{HOST}/{ref}", "t", T0 - timedelta(hours=1), "instant", "en",
                      ar.text_sha256(body.decode()), text, at)


# ----------------------------------------------------------------------------------------------- the adapter


def test_the_synthetic_adapter_goes_through_the_governed_transport():
    a, t, sender = synthetic({LIST: [tp.Response(200, {}, json.dumps({"items": [
        {"ref": "a1", "url": f"https://{HOST}/a1"}, {"ref": "a2", "url": "https://www.cse.lk/x"}]}).encode())],
        f"https://{HOST}/a1": [tp.Response(200, {}, item_json())]})
    assert a.list_new() == ["a1", "a2"]
    item = a.fetch("a1")
    assert isinstance(item, ar.RawItem) and item.published_precision == "instant" and item.language == "en"
    assert a.fetch("a2") is None                                         # refused by the transport, never sent
    assert all("cse.lk" not in u for u, _h in sender.sent)
    assert t.ledger.attempts[-1]["state"] == "refused"


def test_refetch_distinguishes_gone_explicit_removal_and_failure():
    url = f"https://{HOST}/a1"
    a, t, _s = synthetic({LIST: [tp.Response(200, {}, json.dumps({"items": [{"ref": "a1", "url": url}]}).encode())],
                          url: [tp.Response(404), tp.Response(200, {}, item_json(removed=True)), tp.Response(503),
                                tp.Response(503), tp.Response(503)]})
    a.list_new()
    g = a.refetch("a1")
    assert isinstance(g, ar.Gone) and g.status == 404 and not g.explicit
    g = a.refetch("a1")
    assert isinstance(g, ar.Gone) and g.explicit
    assert a.refetch("a1") is None                                       # a 5xx is a failed attempt, never an absence


# ----------------------------------------------------------------------------------------------- versions


def test_revisions_are_by_normalised_text_and_inherit_first_availability():
    v1, obs = ar.ingest(SRC, [], [], raw(), "metadata_only")
    assert v1.version_no == 1 and v1.extracted_text is None and v1.available_at == v1.first_available_at == T0
    # the same text with different typography and whitespace, and a different raw body: confirmed, not a revision
    same = raw(text="Alpha  Bank PLC reported higher profit.", at=T0 + timedelta(days=1), body=b"other page")
    v, obs = ar.ingest(SRC, [v1], [], same, "metadata_only")
    assert v is None and [o.kind for o in obs] == ["confirmed"]
    later = T0 + timedelta(days=2)
    v2, obs = ar.ingest(SRC, [v1], [], raw(text="Alpha Bank PLC reported lower profit.", at=later), "full_text")
    assert (v2.version_no, v2.supersedes, v2.available_at, v2.first_available_at) == (2, 1, later, T0)
    assert v2.extracted_text == "Alpha Bank PLC reported lower profit."       # kept only under full_text
    assert ar.normalise_text("“A” — b\n c") == '"A" - b c'
    with pytest.raises(ValueError):
        ar.ingest(SRC, [], [], raw(), "everything")


def test_withdrawals_only_on_evidence():
    v1, _ = ar.ingest(SRC, [], [], raw(), "metadata_only")
    d1 = T0 + timedelta(days=1)
    _v, o1 = ar.ingest(SRC, [v1], [], ar.Gone("a1", d1, 404), "metadata_only")
    assert [o.kind for o in o1] == ["gone_first"]                       # a single 404 is not a withdrawal
    _v, o2 = ar.ingest(SRC, [v1], o1, ar.Gone("a1", d1 + timedelta(hours=23), 410), "metadata_only")
    assert [o.kind for o in o2] == ["gone_first"]                       # less than a day apart
    _v, o3 = ar.ingest(SRC, [v1], o1, ar.Gone("a1", d1 + timedelta(days=1), 404), "metadata_only")
    assert [o.kind for o in o3] == ["withdrawn"]
    assert o3[0].available_at == d1 + timedelta(days=1)                 # the confirming re-fetch's observed_at
    _v, o4 = ar.ingest(SRC, [v1], [], ar.Gone("a1", d1, None, explicit=True), "metadata_only")
    assert [(o.kind, o.detail) for o in o4] == [("withdrawn", "explicit_removal")]
    with pytest.raises(ValueError, match="evidence"):
        ar.ingest(SRC, [v1], [], ar.Gone("a1", d1, 500), "metadata_only")
    # a reappearance between two absences resets the count
    v2 = ar.Version(**{**v1.__dict__, "version_no": 2, "supersedes": 1, "observed_at": d1 + timedelta(hours=12)})
    _v, o5 = ar.ingest(SRC, [v1, v2], o1, ar.Gone("a1", d1 + timedelta(days=1), 404), "metadata_only")
    assert [o.kind for o in o5] == ["gone_first"]


def test_the_refetch_plan_new_first_within_budget_and_skips_recorded():
    sessions = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9)]
    v = lambda ref, day: ar.Version(SRC, ref, 1, None, "t", None, "none", "en", "x", "y", None,                # noqa
                                    datetime(2026, 9, day, 4, tzinfo=UTC), datetime(2026, 9, day, 4, tzinfo=UTC),
                                    datetime(2026, 9, day, 4, tzinfo=UTC))
    vs = [v("old1", 1), v("old2", 2)]
    # old1 first seen on session 1 Sep: session_1 is 2 Sep, session_5 is 8 Sep; old2 (2 Sep): 3 Sep and 9 Sep
    f, r, s = ar.plan(["n1", "n2"], vs, [], sessions, date(2026, 9, 2), budget=5)
    assert f == ["n1", "n2"] and r == [("old1", "session_1")] and s == []
    f, r, s = ar.plan(["n1"], vs, [], sessions, date(2026, 9, 8), budget=1)
    assert f == ["n1"] and r == [] and s == [("old1", "session_5")]        # new articles first; the rest skipped
    gone = [ar.Observation(SRC, "old2", "gone_first", datetime(2026, 9, 3, 4, tzinfo=UTC),
                           datetime(2026, 9, 3, 4, tzinfo=UTC))]
    f, r, s = ar.plan([], vs, gone, sessions, date(2026, 9, 4), budget=3)
    assert ("old2", "confirm_gone") in r
    wd = [ar.Observation(SRC, "old1", "withdrawn", None, None)]
    assert ar.plan([], vs, wd, sessions, date(2026, 9, 2), budget=3)[1] == []   # a withdrawn article is done


# ----------------------------------------------------------------------------------------------- in force at T
# (NL-1 to NL-4, each with its converse)

T = datetime(2026, 9, 10, 12, tzinfo=UTC)
H = T + timedelta(days=30)


def test_nl1_an_article_recorded_after_t():
    v, _ = ar.ingest(SRC, [], [], raw(at=T - timedelta(hours=2)), "metadata_only")
    late = stamped(v, T + timedelta(seconds=1))
    assert ar.in_force([late], [], Query(KNOWN, T)) == {}
    assert ar.in_force([late], [], Query(AVAILABLE, T, H)) != {}       # observed before T, known by H
    assert ar.in_force([stamped(v, T)], [], Query(KNOWN, T)) != {}     # converse: recorded at T


def test_nl2_a_back_dated_article_is_not_known_at_t():
    fetched = ar.RawItem("b1", f"https://{HOST}/b1", "t", T - timedelta(days=5), "instant", "en", "x", "text",
                         T + timedelta(hours=1))                          # stamped five days back, seen after T
    v, _ = ar.ingest(SRC, [], [], fetched, "metadata_only")
    rows = [stamped(v, T + timedelta(hours=1))]
    for q in (Query(KNOWN, T), Query(AVAILABLE, T, H)):
        assert ar.in_force(rows, [], q) == {}                            # published_at is never a clock for it
    assert ar.in_force(rows, [], Query(KNOWN, T + timedelta(hours=2))) != {}      # converse
    # an archive article: available from its declared publication, in AVAILABLE only once recorded by H
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 0}
    arc = stamped(ar.archive_version(SRC, "c1", "t", T - timedelta(days=5), "instant", "en", "text", "metadata_only",
                                     pol), T + timedelta(days=3))
    assert ar.in_force([arc], [], Query(KNOWN, T)) == {}
    assert ar.in_force([arc], [], Query(AVAILABLE, T, H)) != {}


def test_nl3_a_revision_recorded_after_t():
    v1, _ = ar.ingest(SRC, [], [], raw(at=T - timedelta(days=2)), "metadata_only")
    v1 = stamped(v1, T - timedelta(days=2))
    v2, _ = ar.ingest(SRC, [v1], [], raw(text="Alpha Bank PLC reported a loss.", at=T + timedelta(hours=1)),
                      "metadata_only")
    v2 = stamped(v2, T + timedelta(hours=1))
    assert ar.in_force([v1, v2], [], Query(KNOWN, T))[(SRC, "a1")].version_no == 1
    assert ar.in_force([v1, v2], [], Query(AVAILABLE, T, H))[(SRC, "a1")].version_no == 1
    assert ar.in_force([v1, v2], [], Query(KNOWN, T + timedelta(hours=2)))[(SRC, "a1")].version_no == 2   # converse


def test_nl4_a_withdrawal_recorded_after_t():
    v1 = stamped(ar.ingest(SRC, [], [], raw(at=T - timedelta(days=3)), "metadata_only")[0], T - timedelta(days=3))
    wd = ar.Observation(SRC, "a1", "withdrawn", T - timedelta(hours=1), T - timedelta(hours=1), "explicit_removal",
                        recorded_at=T + timedelta(minutes=5))
    assert (SRC, "a1") in ar.in_force([v1], [wd], Query(KNOWN, T))
    assert (SRC, "a1") not in ar.in_force([v1], [wd], Query(KNOWN, T + timedelta(minutes=5)))      # converse
    # NL-14's shape: in AVAILABLE a withdrawal counts by its confirming re-fetch's observed_at
    wd_later = ar.replace(wd, observed_at=T + timedelta(days=1), available_at=T + timedelta(days=1))
    assert (SRC, "a1") in ar.in_force([v1], [wd_later], Query(AVAILABLE, T, H))
    assert (SRC, "a1") not in ar.in_force([v1], [wd], Query(AVAILABLE, T, H))
    assert (SRC, "a1") not in ar.in_force([v1], [wd], Query(CURRENT, None, H))


def test_archive_availability_policy():
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 30}
    got = ar.archive_available_at(date(2026, 9, 1), "date", pol)
    assert got == datetime(2026, 9, 1, 23, 59, 59, tzinfo=timezone(timedelta(minutes=330))) + timedelta(minutes=30)
    assert ar.archive_available_at(datetime(2026, 9, 1, 2, tzinfo=UTC), "instant", pol) == \
        datetime(2026, 9, 1, 2, 30, tzinfo=UTC)
    with pytest.raises(ValueError):
        ar.archive_available_at(None, "none", pol)
