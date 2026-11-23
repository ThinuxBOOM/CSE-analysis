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
        gv.load(str(p), "0" * 64, [HOST, OTHER])
    p.write_bytes(record_bytes())
    pinned = gv.sha256_of(record_bytes())
    assert gv.load(str(p), pinned, [OTHER, HOST]).source_id == "synthetic_news"
    with pytest.raises(gv.RecordRefused, match="record_hash"):
        gv.load(str(p), "a" * 64, [HOST, OTHER])
    p.write_bytes(record_bytes() + b" ")                                     # one byte changed after approval
    with pytest.raises(gv.RecordRefused, match="record_hash"):
        gv.load(str(p), pinned, [HOST, OTHER])
    p.write_bytes(record_bytes())
    with pytest.raises(gv.RecordRefused, match="allowed_hosts_mismatch"):   # Minor 11: ns_sources must equal it
        gv.load(str(p), pinned, [HOST])


def test_the_record_refuses_cse_credentials_relays_and_incomplete_fields():
    for over, code in ((dict(allowed_hosts=[HOST, "www.cse.lk"]), "cse_refused"),
                       (dict(allowed_hosts=["cdn.cse.lk"]), "cse_refused"),
                       (dict(credentials={"key": "x"}), "credentials"),
                       (dict(allowed_hosts=[HOST, "archive.org"]), "fetch_on_behalf"),
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
    rec = record(allowed_hosts=[HOST, "archive.org"], fetch_on_behalf_hosts=["archive.org"])
    assert "archive.org" in rec.allowed_hosts                                # only by the owner's naming
    # NS-1 audit minors: finite numbers, no booleans, bounds, no unknown keys, no control characters, nested
    # credentials
    disc = {"min_interval_seconds": 2.0, "daily_budget": 5}
    for rd in ({**disc, "daily_budget": float("nan")}, {**disc, "daily_budget": True},
               {**disc, "max_body_bytes": float("inf")}, {**disc, "retry_after_max_seconds": float("nan")},
               {**disc, "daily_budget": "5"}, {**disc, "daily_budget": 10 ** 9}, {**disc, "surprise": 1},
               {"daily_budget": 5}):
        with pytest.raises(gv.RecordRefused, match="request_discipline"):
            record(request_discipline=rd)
    for ident in ({"user_agent": "ok\r\nX-Injected: 1"}, {"user_agent": "ok", "contact": 5},
                  {"user_agent": "ok", "contact": "a\nb"}, {"user_agent": "ok", "extra": "x"}):
        with pytest.raises(gv.RecordRefused, match="identification"):
            record(identification=ident)
    with pytest.raises(gv.RecordRefused, match="unknown_keys"):
        record(surprise=True)
    with pytest.raises(gv.RecordRefused, match="credentials"):
        record(identification={"user_agent": "ok", "contact": None, "session_cookie": "x"})


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
                                 f"https://{HOST}/fetch?u=www%2Ecse%2Elk",                    # the dots encoded
                                 f"https://{HOST}/fetch?u=www%252Ecse%252Elk",                # twice
                                 f"https://{HOST}/fetch?u=www%25252Ecse%25252Elk",            # three times
                                 f"https://{HOST}/fetch?u=cse\u3002lk",                       # ideographic full stop
                                 f"https://{HOST}/fetch?u=\uff43\uff53\uff45.lk",            # fullwidth letters
                                 f"https://{HOST}/fetch?u=cse\u200b.lk",                      # zero-width split
                                 f"https://{HOST}/fetch?u=cse%26%2346%3Blk",                  # an HTML reference
                                 f"https://{HOST}/fetch?u=https%253A%252F%252Fcdn.cse.lk%252Fa.pdf",
                                 f"https://{HOST}/proxy/www.cse.lk/announcements",
                                 f"https://{HOST}/r#https://www.CSE.lk/",
                                 f"https://{HOST}/q?site=cse%2elk"])
def test_a_cse_host_in_the_path_or_query_is_refused(url):
    t, sender, _ = transport({})
    r = t.get(url, "article")
    assert (r.outcome, r.code) == ("refused", "cse_in_url") and sender.sent == []


def test_decoding_is_bounded_and_refuses_when_still_changing():
    from worker.news_ingest import hosts as h
    from urllib.parse import quote
    nested = "/"
    for _ in range(h.MAX_DECODE_ROUNDS + 2):
        nested = quote(nested, safe="")
    assert h.carries_cse(f"https://{HOST}/q?u={nested}")         # still changing after the bound: refused
    assert not h.carries_cse(f"https://{HOST}/q?u=%2541")        # settles quickly, no CSE


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
    assert t.get("https://archive.org/save/x", "article").code == "fetch_on_behalf"
    for bad in ("http://[cse.lk]/", "https://[::1", "http://news.example.test:notaport/"):
        r = t.get(bad, "article")
        assert (r.outcome, r.code) == ("refused", "bad_url") and t.ledger.attempts[-1]["state"] == "refused"


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


# ----------------------------------------------------------------------------------------------- NS-1 audit


def test_a_stop_holds_across_processes_until_the_owner_clears_it():
    a = f"https://{HOST}/a"
    led = tp.MemoryLedger()
    t1, s1, _ = transport({ROBOTS: robots_open(), a: [tp.Response(403)]}, ledger=led)
    assert t1.get(a, "article").outcome == "stopped"
    t2, s2, _ = transport({ROBOTS: robots_open(), a: [ok()]}, ledger=led)          # another process, same ledger
    assert t2.stopped == "http_403" and t2.get(a, "article").outcome == "stopped" and s2.sent == []
    with pytest.raises(ValueError):
        led.owner_clear_stop("synthetic_news", " ", T0)
    led.owner_clear_stop("synthetic_news", "the owner reviewed the block and resumed", T0)
    assert t2.stopped is None and t2.get(a, "article").outcome == "ok"


def test_robots_is_the_transports_own_purpose():
    t, sender, _ = transport({})
    with pytest.raises(ValueError, match="robots"):
        t.get(f"https://{HOST}/private/x", "robots")
    script = {ROBOTS: [tp.Response(200, {}, b"User-agent: *\nDisallow: /private\n")]}
    for purpose in ("listing", "refetch", "article"):
        t, sender, _ = transport(dict(script))
        assert t.get(f"https://{HOST}/private/x", purpose).outcome == "robots_disallowed", purpose


def test_robots_is_per_host_and_port():
    p8 = f"https://{HOST}:8443/a"
    t, sender, _ = transport({ROBOTS: robots_open(), f"https://{HOST}:8443/robots.txt": robots_open(),
                              f"https://{HOST}/a": [ok()], p8: [ok()]})
    t.get(f"https://{HOST}/a", "article")
    t.get(p8, "article")
    assert [u for u, _h in sender.sent].count(f"https://{HOST}:8443/robots.txt") == 1


def test_an_in_flight_attempt_counts_as_ending_now():
    led = tp.MemoryLedger()
    led.open_attempt("synthetic_news", "article", f"https://{HOST}/z", HOST, T0 - timedelta(seconds=30), "sent")
    t, sender, slept = transport({ROBOTS: robots_open(), f"https://{HOST}/a": [ok()]}, ledger=led)
    t.get(f"https://{HOST}/a", "article")
    assert slept and slept[0] == pytest.approx(2.0)                # waited the full interval from "now"


def test_a_sender_exception_is_recorded_never_left_open():
    class Boom(tp.ScriptedSender):
        def send(self, url, headers, timeout, max_body_bytes):
            if url.endswith("/a"):
                raise RuntimeError("socket gone")
            return super().send(url, headers, timeout, max_body_bytes)
    clock, sleep, _ = tp.fake_clock(T0)
    led = tp.MemoryLedger()
    t = tp.GovernedTransport(record(), Boom({ROBOTS: robots_open()}), led, clock=clock, sleep=sleep)
    with pytest.raises(RuntimeError):
        t.get(f"https://{HOST}/a", "article")
    assert led.events[-1]["kind"] == "failed" and led.events[-1]["error_kind"] == "exception"


def test_headers_are_read_without_regard_to_case_and_429_without_retry_after_stops():
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(302, {"LOCATION": b}, b"")], b: [ok()]})
    assert t.get(a, "article").url == b
    t, sender, slept = transport({ROBOTS: robots_open(), a: [tp.Response(429, {"RETRY-AFTER": "30"}), ok()]})
    assert t.get(a, "article").outcome == "ok" and 30.0 in slept
    t, sender, _ = transport({ROBOTS: robots_open(), a: [tp.Response(429)]})
    assert t.get(a, "article").outcome == "stopped" and t.stopped == "rate_limited"


def test_the_budget_day_is_colombo_not_utc():
    rec = record(request_discipline={"min_interval_seconds": 1, "daily_budget": 2})
    late = datetime(2026, 9, 1, 18, 20, tzinfo=UTC)              # 23:50 Colombo, 1 Sep
    t, sender, _ = transport({ROBOTS: robots_open() * 2, **{f"https://{HOST}/{i}": [ok()] for i in range(3)}},
                             rec=rec, start=late)
    assert t.get(f"https://{HOST}/0", "article").outcome == "ok"            # robots + 0: the budget of 1 Sep
    t.clock = lambda: datetime(2026, 9, 1, 18, 35, tzinfo=UTC)               # 00:05 Colombo, 2 Sep (same UTC day)
    assert t.get(f"https://{HOST}/1", "article").outcome == "ok"


def test_the_redirect_limit_is_exact():
    urls = [f"https://{HOST}/r{i}" for i in range(5)]
    script = {ROBOTS: robots_open(), **{urls[i]: [tp.Response(302, {"Location": urls[i + 1]}, b"")] for i in range(4)},
              urls[4]: [ok()]}
    t, sender, _ = transport(dict(script))
    assert t.get(urls[1], "article").outcome == "ok"                       # 3 hops: r1 -> r2 -> r3 -> r4
    t, sender, _ = transport(dict(script))
    assert t.get(urls[0], "article").outcome == "redirect_limit"           # 4 hops: refused at the fourth


def test_a_failed_attempt_also_ends_the_spacing_interval():
    a, b = f"https://{HOST}/a", f"https://{HOST}/b"
    t, sender, slept = transport({ROBOTS: robots_open(), a: [tp.Response(None, {}, None, "timeout")] * 3, b: [ok()]})
    t.get(a, "article")
    before = len(slept)
    t.get(b, "article")
    assert slept[before] == pytest.approx(2.0)


def test_the_real_sender_is_built_only_by_the_wiring_module(tmp_path):
    from worker.news_ingest import wiring
    with pytest.raises(gv.RecordRefused, match="record_missing"):           # nothing runs without a record
        wiring.real_transport(str(tmp_path / "none.md"), "0" * 64, [HOST], tp.MemoryLedger())
