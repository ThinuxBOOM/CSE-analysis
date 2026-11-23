"""
Phase 6 NS-8: the GDELT DOC-shaped adapter, metadata only, on a scripted fake sender. The API's host is a synthetic
`.test` host here; the real host appears only in the owner's governance record (docs), never in code or tests.
Publishers' pages are never requested; CSE never appears as an article.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

import test_ns1_unit as n1
from worker.news_ingest import articles as ar
from worker.news_ingest import gdelt as gd
from worker.news_ingest import transport as tp
from worker.news_sentiment import process as pr

UTC = timezone.utc
API = "api.gdelt.test"
ROBOTS = f"https://{API}/robots.txt"
SRC = "gdelt_doc"


def rec(**over):
    m = dict(source_id=SRC, allowed_hosts=[API], request_discipline={
        "min_interval_seconds": 6.0, "daily_budget": 300, "max_retries": 2, "backoff_base_seconds": 5.0,
        "backoff_max_seconds": 60.0, "retry_after_max_seconds": 120.0, "max_consecutive_failures": 4,
        "max_body_bytes": 2_000_000})
    m.update(over)
    return n1.record(**m)


def entry(url="https://news.example.test/a/1", title="Alpha Bank PLC posts record profit",
          seen="20260901T023000Z", lang="English"):
    return {"url": url, "url_mobile": "", "title": title, "seendate": seen, "socialimage": "",
            "domain": "news.example.test", "language": lang,
            "sourcecountry": "Sri Lanka"}


def listing(*entries):
    return [tp.Response(200, {}, json.dumps({"articles": list(entries)}).encode())]


def adapter(entries, since=None, record=None, **kw):
    record = record or rec()
    a0 = gd.GdeltDocAdapter.__new__(gd.GdeltDocAdapter)        # the URL only; no transport needed for it
    a0.base, a0.query, a0.max_records = f"https://{API}/api/v2/doc/doc", kw.get("query", gd.DEFAULT_QUERY), \
        kw.get("max_records", gd.MAX_RECORDS)
    url = gd.GdeltDocAdapter.listing_url(a0, since)
    t, sender, _ = n1.transport({ROBOTS: n1.robots_open(), url: entries}, rec=record)
    return gd.GdeltDocAdapter(t, **kw), t, sender, url


def test_one_listing_request_through_the_governed_transport_and_nothing_else():
    a, t, sender, url = adapter(listing(entry(), entry(url="https://news.example.test/a/2", title="Beta Finance PLC dividend")))
    refs = a.list_new()
    assert len(refs) == 2 and all(r.startswith("u") and len(r) == 65 for r in refs)
    assert [u for u, *_ in sender.sent] == [ROBOTS, url]                  # robots, then the listing: nothing else
    q = parse_qs(urlsplit(url).query)
    assert q == {"query": [gd.DEFAULT_QUERY], "mode": ["artlist"], "format": ["json"], "maxrecords": ["250"],
                 "sort": ["datedesc"]}
    item = a.fetch(refs[0])
    assert isinstance(item, ar.RawItem) and item.url == "https://news.example.test/a/1"
    assert item.title == "Alpha Bank PLC posts record profit" and item.language == "en"
    assert item.published_at == datetime(2026, 9, 1, 2, 30, tzinfo=UTC) and item.published_precision == "instant"
    assert item.extracted_text == "" and item.observed_at >= n1.T0
    assert a.refetch(refs[0]) is None and a.REFETCH is False
    assert [u for u, *_ in sender.sent] == [ROBOTS, url]                  # fetch and refetch sent nothing


def test_since_is_utc_and_the_reference_is_stable():
    since = datetime(2026, 9, 1, 8, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    a, t, sender, url = adapter(listing(entry()), since=since)
    assert parse_qs(urlsplit(url).query)["startdatetime"] == ["20260901030000"]
    assert a.list_new(since) == [gd.reference_of("https://news.example.test/a/1")]
    assert gd.reference_of("https://news.example.test/a/1") == "u" + hashlib.sha256(
        b"https://news.example.test/a/1").hexdigest()
    with pytest.raises(ValueError):
        a.listing_url(datetime(2026, 9, 1))


def test_malformed_entries_are_skipped_and_counted_never_raised():
    bad = [entry(title=""), entry(seen="2026-09-01"), entry(url=None), "x", entry(url="ftp://news.example.test/f"),
           entry(url="https://news.example.test/" + "a" * 4100)]
    a, *_ = adapter(listing(entry(), *bad))
    assert len(a.list_new()) == 1 and a.malformed == len(bad)
    for body in (b"not json", b"\xff\xfe", json.dumps({"articles": "x"}).encode(), json.dumps([1]).encode()):
        a2, *_ = adapter([tp.Response(200, {}, body)])
        assert a2.list_new() == [] and a2.malformed == 1


def test_cse_never_becomes_an_article():
    cse = "cse" + ".lk"
    entries = [entry(url=f"https://www.{cse}/x"), entry(url=f"https://{cse}./y"),
               entry(url=f"https://news.example.test/r?u=https%3A%2F%2F{cse}%2Fz")]
    a, *_ = adapter(listing(entry(), *entries))
    assert len(a.list_new()) == 1 and a.dropped_cse == 3


def test_a_failed_listing_is_no_items_and_is_in_the_ledger():
    a, t, sender, url = adapter([tp.Response(503, {}, b"")] * 5)
    assert a.list_new() == []
    assert t.ledger.attempts and any(e["kind"] in ("failed", "completed") for e in t.ledger.events)


def test_the_record_must_fit_the_adapter():
    for over in (dict(allowed_hosts=[API, "other.example.test"]), dict(storage_level="full_text")):
        t, *_ = n1.transport({}, rec=rec(**over))
        with pytest.raises(ValueError):
            gd.GdeltDocAdapter(t)
    t, *_ = n1.transport({}, rec=rec())
    for kw in (dict(max_records=0), dict(max_records=251), dict(query="a\nb"), dict(query="")):
        with pytest.raises(ValueError):
            gd.GdeltDocAdapter(t, **kw)


def test_from_listing_to_a_scored_title_only_result():
    a, *_ = adapter(listing(entry(title="Alpha Bank PLC posts record profit and strong growth")))
    item = a.fetch(a.list_new()[0])
    v, _obs = ar.ingest(SRC, [], [], item, "metadata_only")
    assert v.extracted_text is None and v.first_available_at == item.observed_at
    from worker.news_sentiment import aliases as al
    early = datetime(2026, 1, 1, tzinfo=UTC)
    res = pr.process(v.title, item.extracted_text, item.language,
                     [al.Alias(1, "Alpha Bank PLC", "iss_alpha", "name", early, early)])
    assert res.state == "processed" and "iss_alpha" in res.issuers
    sinhala, *_ = adapter(listing(entry(lang="Sinhala")))
    it = sinhala.fetch(sinhala.list_new()[0])
    assert it.language == "si"
    assert pr.process(it.title, "", it.language, []).state == "language_unsupported"


def test_the_draft_record_is_refused_until_signed_and_then_fits_the_adapter():
    import os
    from worker.news_ingest import governance as gv
    path = os.path.join(n1.__file__.rsplit(os.sep, 2)[0], "docs", "governance", "NS-GDELT-DOC.md")
    data = open(path, "rb").read()
    if gv.UNSIGNED.lower().encode() in data.lower():
        with pytest.raises(gv.RecordRefused, match="unsigned"):
            gv.parse(data)
    if gv.UNSIGNED.lower().encode() in data.lower():
        import re
        data = re.sub(rb"(?i)not signed", b"signed (test copy)", data)
    r = gv.parse(data)
    assert (r.source_id, r.storage_level, r.onward_disclosure, r.contact, len(r.allowed_hosts)) == \
        ("gdelt_doc", "metadata_only", "none", None, 1)
    assert r.user_agent == "research-news-reader/1 (non-commercial; obeys robots.txt)" and r.robots == "obey"
    t, *_ = n1.transport({}, rec=r)
    assert gd.GdeltDocAdapter(t).listing_url().startswith(f"https://{r.allowed_hosts[0]}/api/v2/doc/doc?")


# ----------------------------------------------------------------------------------------------- NS-8 audit


def test_major1_no_entry_can_raise_and_lose_the_listing():
    bad = [entry(url="https://[::1/x"), entry(url="https://news.example.test/\ud800"), entry(title="T \ud800"),
           entry(seen="２０２６0901T023000Z"), entry(title="x" * 1001), {"url": 1, "title": None},
           dict(entry(), language=7)]
    a, *_ = adapter(listing(entry(), *bad[:-1], bad[-1] | {"url": "https://news.example.test/ok2"}))
    refs = a.list_new()
    assert len(refs) == 2 and a.malformed == len(bad) - 1
    assert a.fetch(refs[1]).language == "und"
    for r in refs:
        assert a.fetch(r).title.encode("utf-8")


def test_major2_cse_look_alikes_are_dropped():
    from worker.news_ingest import hosts
    cse = "cse" + ".lk"
    looks = ["https://ｃｓｅ.lk/x", "https://cse。lk/x", "https://cse.lk​/x",
             "https://www.CSE.LK/x", "https://www.cse．lk./x"]
    a, *_ = adapter(listing(entry(), *[entry(url=u) for u in looks]))
    assert len(a.list_new()) == 1 and a.dropped_cse == len(looks)
    assert hosts.is_cse_host(cse) and not hosts.is_cse_host("notcse.lk.example.test")


def test_minors_duplicates_truncation_cache_and_unsigned_case():
    from worker.news_ingest import governance as gv
    first = entry(title="Alpha Bank PLC first")
    a, *_ = adapter(listing(first, entry(title="Alpha Bank PLC second")), max_records=2)
    refs = a.list_new()
    assert len(refs) == 1 and a.duplicates == 1 and a.truncated
    assert a.fetch(refs[0]).title == "Alpha Bank PLC first"
    b, *_ = adapter(listing(entry()))
    b.list_new()
    assert not b.truncated
    draft = n1.record_bytes().replace(b"tests only.", b"tests only. Status: Draft, not signed.")
    with pytest.raises(gv.RecordRefused, match="unsigned"):
        gv.parse(draft)


def test_the_cache_holds_one_listing():
    a, t, sender, url = adapter(listing(entry()) + listing(entry(url="https://news.example.test/a/2")))
    first = a.list_new()
    second = a.list_new()
    assert a.fetch(first[0]) is None and a.fetch(second[0]) is not None


def test_the_purge_commands_refuse_an_empty_reason_before_the_operator_is_appended(monkeypatch, capsys):
    pytest.importorskip("psycopg2")
    from worker.news_store import cli

    class Conn:
        def rollback(self):
            pass

        def close(self):
            pass
    monkeypatch.setenv("CSE_OPERATOR", "someone")
    for argv in (["stop-source", "--source", "s", "--reason", " "],
                 ["purge-source", "--source", "s", "--request", ""]):
        assert cli.run(cli.parser().parse_args(argv), lambda: Conn()) == cli.EXIT_REFUSED
        assert "say why" in capsys.readouterr().out
