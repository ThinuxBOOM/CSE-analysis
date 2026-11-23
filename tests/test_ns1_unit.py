"""
Phase 6 NS-1 (design §4.1, §13): the governed news transport and the fetch ledger, on a scripted fake sender only.
No real news host appears here: synthetic hosts use the reserved `.test` domain (RFC 2606); CSE's hosts appear only
as URLs that must be refused before anything is sent.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from worker.news_ingest import governance as gv
from worker.news_ingest import hosts
from worker.news_ingest import transport as tp

UTC = timezone.utc
T0 = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)             # 08:30 Colombo
HOST, OTHER = "news.example.test", "media.example.test"
ROBOTS = f"https://{HOST}/robots.txt"


def record_bytes(**over):
    m = {"source_id": "synthetic_news", "allowed_hosts": [HOST, OTHER], "storage_level": "metadata_only",
         "onward_disclosure": "none",
         "availability_policy": {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day",
                                 "availability_lag_minutes": 0},
         "identification": {"user_agent": "synthetic-ns-test/1", "contact": None},
         "request_discipline": {"min_interval_seconds": 2.0, "daily_budget": 50, "max_retries": 2,
                                "backoff_base_seconds": 5.0, "backoff_max_seconds": 60.0,
                                "retry_after_max_seconds": 120.0, "max_consecutive_failures": 4,
                                "max_body_bytes": 1000},
         "robots": "obey"}
    m.update(over)
    return ("# Synthetic source (test)\n\nA governance record for tests only.\n\n```ns-source\n"
            + json.dumps(m, indent=1) + "\n```\n").encode()


def record(**over):
    return gv.parse(record_bytes(**over))


def ok(body=b"<html>ok</html>", **headers):
    return tp.Response(200, headers, body)


def robots_open():
    return [tp.Response(404, {}, b"")]


def transport(script, rec=None, ledger=None, start=T0):
    clock, sleep, slept = tp.fake_clock(start)
    sender = tp.ScriptedSender(script)
    t = tp.GovernedTransport(rec or record(), sender, ledger or tp.MemoryLedger(), clock=clock, sleep=sleep)
    return t, sender, slept


# ----------------------------------------------------------------------------------------------- the record


def test_refuse_without_record_or_on_a_hash_mismatch(tmp_path):
    p = tmp_path / "NS-SYNTHETIC.md"
    with pytest.raises(gv.RecordRefused, match="record_missing"):
        gv.load(str(p), "0" * 64)
    p.write_bytes(record_bytes())
    pinned = gv.sha256_of(record_bytes())
    assert gv.load(str(p), pinned, [OTHER, HOST]).source_id == "synthetic_news"
    with pytest.raises(gv.RecordRefused, match="record_hash"):
        gv.load(str(p), "a" * 64)
    p.write_bytes(record_bytes() + b" ")                                     # one byte changed after approval
    with pytest.raises(gv.RecordRefused, match="record_hash"):
        gv.load(str(p), pinned)
    p.write_bytes(record_bytes())
    with pytest.raises(gv.RecordRefused, match="allowed_hosts_mismatch"):   # Minor 11: ns_sources must equal it
        gv.load(str(p), pinned, [HOST])


def test_the_record_refuses_cse_credentials_relays_and_incomplete_fields():
    for over, code in ((dict(allowed_hosts=[HOST, "www.cse.lk"]), "cse_refused"),
                       (dict(allowed_hosts=["cdn.cse.lk"]), "cse_refused"),
                       (dict(credentials={"key": "x"}), "credentials"),
                       (dict(allowed_hosts=[HOST, "web.archive.org"]), "fetch_on_behalf"),
                       (dict(fetch_on_behalf_hosts=["elsewhere.example.test"]), "fetch_on_behalf"),
                       (dict(storage_level="everything"), "storage_level"),
                       (dict(onward_disclosure="all"), "onward_disclosure"),
                       (dict(identification={"user_agent": " "}), "identification"),
                       (dict(robots="ignore"), "robots"),
                       (dict(allowed_hosts=["News.Example.test"]), "allowed_hosts"),
                       (dict(allowed_hosts=[HOST, HOST]), "allowed_hosts"),
                       (dict(request_discipline={"min_interval_seconds": 0.5, "daily_budget": 5}),
                        "request_discipline")):
        with pytest.raises(gv.RecordRefused) as e:
            record(**over)
        assert e.value.code == code, (over, e.value.code)
    with pytest.raises(gv.RecordRefused, match="record_block"):
        gv.parse(b"# no block")
    rec = record(allowed_hosts=[HOST, "web.archive.org"], fetch_on_behalf_hosts=["web.archive.org"])
    assert "web.archive.org" in rec.allowed_hosts                            # only by the owner's naming


# ----------------------------------------------------------------------------------------------- CSE and hosts


@pytest.mark.parametrize("url", ["https://www.cse.lk/api/companyInfoSummery", "https://cdn.cse.lk/x.pdf",
                                 "http://CSE.LK./", "https://cse.lk", "https://a.b.cse.lk/z"])
def test_cse_refused_before_any_socket(url):
    t, sender, _ = transport({})
    r = t.get(url, "article")
    assert (r.outcome, r.code) == ("refused", "cse_refused") and sender.sent == []
    a = t.ledger.attempts[-1]
    assert a["state"] == "refused" and t.ledger.events[-1]["code"] == "cse_refused"   # recorded as refused


@pytest.mark.parametrize("url", [f"https://{HOST}/fetch?u=https%3A%2F%2Fwww.cse.lk%2Fapi",
                                 f"https://{HOST}/fetch?u=https%253A%252F%252Fcdn.cse.lk%252Fa.pdf",
                                 f"https://{HOST}/proxy/www.cse.lk/announcements",
                                 f"https://{HOST}/r#https://www.CSE.lk/",
                                 f"https://{HOST}/q?site=cse%2elk"])
def test_a_cse_host_in_the_path_or_query_is_refused(url):
    t, sender, _ = transport({})
    r = t.get(url, "article")
    assert (r.outcome, r.code) == ("refused", "cse_in_url") and sender.sent == []


def test_lookalikes_are_not_cse_but_still_need_the_allow_list():
    assert not hosts.is_cse_host("notcse.lk.example.test") and not hosts.carries_cse(f"https://{HOST}/mycse.lkx")
    assert hosts.is_cse_host("cse.lk") and hosts.is_cse_host("www.cse.lk")


def test_the_allow_list_is_exact():
    for url in ("https://elsewhere.example.test/a", f"https://sub.{HOST}/a", "ftp://news.example.test/a",
                f"https://user:pw@{HOST}/a"):
        t, sender, _ = transport({})
        r = t.get(url, "article")
        assert r.outcome == "refused" and sender.sent == [], url
    t, sender, _ = transport({})
    assert t.get("https://web.archive.org/save/x", "article").code == "fetch_on_behalf"


# ----------------------------------------------------------------------------------------------- robots, spacing,
# budget, record-before-send


def test_robots_is_read_once_a_day_and_followed():
    script = {ROBOTS: [tp.Response(200, {}, b"User-agent: *\nDisallow: /private\n")],
              f"https://{HOST}/a": [ok()], f"https://{HOST}/b": [ok()]}
    t, sender, _ = transport(script)
    assert t.get(f"https://{HOST}/a", "article").outcome == "ok"
    r = t.get(f"https://{HOST}/private/x", "article")
    assert r.outcome == "robots_disallowed"
    assert t.get(f"https://{HOST}/b", "article").outcome == "ok"
    assert [u for u, _h in sender.sent].count(ROBOTS) == 1                      # cached for the day
    t2, s2, _ = transport({ROBOTS: [tp.Response(503, {}, b"")] * 3})
    assert t2.get(f"https://{HOST}/a", "article").outcome == "robots_disallowed"   # unreadable: conservative
    assert all(u == ROBOTS for u, _h in s2.sent)


def test_spacing_also_across_processes_and_the_identification_sent():
    script = {ROBOTS: robots_open(), f"https://{HOST}/a": [ok()], f"https://{HOST}/b": [ok()]}
    led = tp.MemoryLedger()
    earlier = led.open_attempt("synthetic_news", "article", f"https://{HOST}/z", HOST, T0 - timedelta(seconds=1),
                               "sent")
    led.event(earlier, "completed", T0 - timedelta(seconds=0.5), status=200)    # another process, half a second ago
    t, sender, slept = transport(script, rec=record(identification={"user_agent": "synthetic-ns-test/1",
                                                                     "contact": "owner@example.test"}), ledger=led)
    t.get(f"https://{HOST}/a", "article")
    t.get(f"https://{HOST}/b", "article")
    assert slept[0] == pytest.approx(1.5) and len(slept) == 3 and all(s == pytest.approx(2.0) for s in slept[1:])
    assert all(h == {"User-Agent": "synthetic-ns-test/1", "From": "owner@example.test"} for _u, h in sender.sent)


def test_the_daily_budget_counts_sent_attempts_by_colombo_day():
    rec = record(request_discipline={"min_interval_seconds": 1, "daily_budget": 3})
    script = {ROBOTS: robots_open() * 2, **{f"https://{HOST}/{i}": [ok()] for i in range(5)}}
    t, sender, _ = transport(script, rec=rec)
    outs = [t.get(f"https://{HOST}/{i}", "article").outcome for i in range(3)]
    assert outs == ["ok", "ok", "budget_exhausted"]                              # robots + 2 articles = 3 sent
    assert len(sender.sent) == 3 and t.ledger.attempts[-1]["state"] == "refused"
    t.clock = lambda: T0 + timedelta(days=1)                                     # the next Colombo day
    assert t.get(f"https://{HOST}/3", "article").outcome == "ok"


def test_every_attempt_is_recorded_before_it_is_sent():
    led = tp.MemoryLedger()

    class Checking(tp.ScriptedSender):
        def send(self, url, headers, timeout, max_body_bytes):
            assert led.attempts and led.attempts[-1]["url"] == url and led.attempts[-1]["state"] == "sent"
            assert not any(e["attempt_id"] == led.attempts[-1]["id"] for e in led.events)   # no outcome yet
            return super().send(url, headers, timeout, max_body_bytes)
    clock, sleep, _ = tp.fake_clock(T0)
    t = tp.GovernedTransport(record(), Checking({ROBOTS: robots_open(), f"https://{HOST}/a": [ok()]}), led,
                             clock=clock, sleep=sleep)
    assert t.get(f"https://{HOST}/a", "article").outcome == "ok"
    assert [e["kind"] for e in led.events] == ["completed", "completed"]
    assert led.events[-1]["body_sha256"] and led.events[-1]["size"] == len(b"<html>ok</html>")


# ----------------------------------------------------------------------------------------------- redirects


def test_redirects_are_never_followed_automatically():
    a = f"https://{HOST}/a"
    for location, outcome, code in (("https://www.cse.lk/api/x", "redirect_refused", "cse_refused"),
                                    ("https://elsewhere.example.test/x", "redirect_refused", "not_allowed"),
                                    (f"https://{HOST}/x?u=www.cse.lk", "redirect_refused", "cse_in_url")):
        t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(302, {"Location": location}, b"")]})
        r = t.get(a, "article")
        assert (r.outcome, r.code) == (outcome, code) and [u for u, _h in sender.sent] == [ROBOTS, a], location
        red = [e for e in t.ledger.events if e["kind"] == "redirect"]
        assert red and red[0]["location"] == location                           # the redirect is recorded
        assert t.ledger.attempts[-1]["redirect_of"] == red[0]["attempt_id"] and t.ledger.attempts[-1]["state"] == \
            "refused"
    # to another allowed host of the source: a second, separately recorded attempt (its own robots first)
    b = f"https://{OTHER}/b"
    t, sender, _ = transport({ROBOTS: robots_open(), f"https://{OTHER}/robots.txt": robots_open(),
                              a: [tp.Response(301, {"Location": b}, b"")], b: [ok()]})
    r = t.get(a, "article")
    assert r.outcome == "ok" and r.url == b
    sent = [x for x in t.ledger.attempts if x["state"] == "sent" and x["purpose"] == "article"]
    assert [x["url"] for x in sent] == [a, b] and sent[1]["redirect_of"] == sent[0]["id"]
    # relative Location; not followed when asked not to
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(302, {"Location": "/c"}, b"")]})
    r = t.get(a, "article", follow_redirects=False)
    assert (r.outcome, r.code) == ("http_302", "redirect_not_followed") and len(sender.sent) == 2


def test_a_redirect_loop_stops_at_the_limit():
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(302, {"Location": b}, b"")] * 3,
                              b: [tp.Response(302, {"Location": a}, b"")] * 3})
    assert t.get(a, "article").outcome == "redirect_limit"


# ----------------------------------------------------------------------------------------------- failures


def test_backoff_on_server_errors_then_success_then_give_up():
    a = f"https://{HOST}/a"
    t, sender, slept = transport({ROBOTS: robots_open(), a: [tp.Response(503), tp.Response(None, {}, None, "timeout"),
                                                             ok()]})
    r = t.get(a, "article")
    assert r.outcome == "ok" and [s for s in slept if s > 2] == [5.0, 10.0]
    retries = [x for x in t.ledger.attempts if x["url"] == a]
    assert retries[1]["retry_of"] == retries[0]["id"] and retries[2]["retry_of"] == retries[1]["id"]
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(500)] * 3})
    assert (t.get(a, "article").outcome, len(sender.sent)) == ("failed", 4)       # max_retries 2: 3 tries


@pytest.mark.parametrize("status", [401, 403, 407, 451])
def test_a_block_stops_the_source(status):
    a = f"https://{HOST}/a"
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(status)]})
    assert (t.get(a, "article").outcome, t.stopped) == ("stopped", f"http_{status}")
    r = t.get(f"https://{HOST}/b", "article")
    assert r.outcome == "stopped" and len(sender.sent) == 2                       # nothing more is sent


def test_rate_limit_waits_within_the_bound_and_stops_beyond_it():
    a = f"https://{HOST}/a"
    t, sender, slept = transport({ROBOTS: robots_open(), a: [tp.Response(429, {"Retry-After": "30"}), ok()]})
    assert t.get(a, "article").outcome == "ok" and 30.0 in slept
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(429, {"Retry-After": "3600"})]})
    assert t.get(a, "article").outcome == "stopped" and t.stopped == "rate_limited"
    when = (T0 + timedelta(seconds=40)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    t, sender, slept = transport({ROBOTS: robots_open(), a: [tp.Response(429, {"Retry-After": when}), ok()]})
    assert t.get(a, "article").outcome == "ok"


def test_the_circuit_breaker_and_an_objection_stop_the_source():
    urls = [f"https://{HOST}/{i}" for i in range(6)]
    t, sender, _ = transport({ROBOTS: robots_open(), **{u: [tp.Response(400)] for u in urls}})
    outs = [t.get(u, "article").outcome for u in urls]
    assert outs[:3] == ["http_400"] * 3 and outs[3] == "http_400" and t.stopped == "circuit_breaker"
    assert outs[4:] == ["stopped", "stopped"]
    t, sender, _ = transport({ROBOTS: robots_open()})
    t.stop("objection_received")
    assert t.get(f"https://{HOST}/a", "article").code == "source_stopped:objection_received" and sender.sent == []


def test_a_body_over_the_limit_is_failed_never_truncated():
    a = f"https://{HOST}/a"
    t, sender, _ = transport({ROBOTS: robots_open(), a: [ok(b"x" * 1001)]})
    r = t.get(a, "article")
    assert (r.outcome, r.code) == ("failed", "too_large") and r.response.body is None
    assert t.ledger.events[-1] == {"attempt_id": t.ledger.attempts[-1]["id"], "kind": "failed", "at": T0 +
                                   timedelta(seconds=2), "error_kind": "too_large"}
