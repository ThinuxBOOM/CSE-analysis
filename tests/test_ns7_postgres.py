"""
Phase 6 NS-7 against the chain (0001 - 0021) plus pending 0022, in a throwaway cluster: the database's rules (owner
path, append-only, stamps, CSE refused, text only for full_text, version inheritance, configuration hash); the ledger
under the governed transport (record-before-send, a stop that holds across processes until the owner clears it, the
advisory lock); the whole path from a synthetic source to a stored snapshot and `verify` (with a tamper); NL-8.
Synthetic `.test` hosts and invented issuers only. Skipped unless P1_PG_BINDIR is set.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

import f64_support as fs
import test_ns1_unit as n1
import test_ns2_unit as n2
from worker.market_features.modes import AVAILABLE, CURRENT, KNOWN, Query
from worker.news_ingest import adapter as ad
from worker.news_ingest import articles as ar
from worker.news_ingest import transport as tp
from worker.news_sentiment import config as cf
from worker.news_sentiment import process as pr
from worker.news_store import preflight, store, verify

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")
UTC = timezone.utc
HOST = n1.HOST


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("ns7"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def refused(c, sql, args=()):
    with c.cursor() as cur:
        cur.execute("savepoint s")
        try:
            cur.execute(sql, args)
        except Exception as exc:                       # noqa: BLE001
            cur.execute("rollback to savepoint s")
            return str(exc).split("\n")[0]
        cur.execute("release savepoint s")
    return None


def world(cluster, db, storage="metadata_only"):
    o = fs.conn(cluster, db, user="cse_migrator")
    w = fs.conn(cluster, db, user="cse_worker")
    rec = store.register_source(o, n1.record_bytes(storage_level=storage), "NS-SYNTHETIC.md")
    store.start_ingestion(o, rec.source_id, datetime(2026, 1, 1, tzinfo=UTC), "the owner starts the synthetic source")
    return o, w, rec


# ----------------------------------------------------------------------------------------------- the database's rules


def test_p1_the_database_rules(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec = world(cluster, db)
    assert preflight.problems(w) == []
    # owner decisions: refused to the worker
    for sql, args in (("insert into ns_entity_aliases (alias_text, issuer_id, kind, available_at, source_reference) "
                       "values ('Alpha', 'i', 'name', now(), 'x y z')", ()),
                      ("insert into ns_source_events (source_id, kind, effective_at, reason) values "
                       "('synthetic_news', 'stop_cleared', now(), 'the worker tries')", ()),
                      ("insert into ns_sources (source_id, governance_record_ref, governance_record_sha256, "
                       "storage_level, availability_policy_json, allowed_hosts_json) values ('x1', 'X.md', "
                       "repeat('a', 64), 'full_text', '{}', '[\"a.example.test\"]')", ())):
        assert refused(w, sql, args), sql
    w.rollback()
    # CSE in a source (even the owner), and in a sent attempt
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    assert "CSE" in refused(o, "insert into ns_sources (source_id, governance_record_ref, governance_record_sha256, "
                               "storage_level, availability_policy_json, allowed_hosts_json) values ('x2', 'X.md', "
                               "repeat('a', 64), 'metadata_only', '{}', '[\"www.cse.lk\"]')")
    o.rollback()
    assert refused(w, "insert into ns_fetch_attempts (source_id, purpose, url, host, state) values ('synthetic_news', "
                      "'article', 'https://cdn.cse.lk/x', 'cdn.cse.lk', 'sent')")
    assert not refused(w, "insert into ns_fetch_attempts (source_id, purpose, url, host, state) values "
                          "('synthetic_news', 'article', 'https://cdn.cse.lk/x', 'cdn.cse.lk', 'refused')")
    # a supplied recorded_at; text for a metadata_only source; an orphan version; append-only for everyone
    assert refused(w, "insert into ns_ingestion_runs (source_id, day, status, recorded_at) values ('synthetic_news', "
                      "'2026-09-01', 'ran', '2020-01-01')")
    base = ("insert into ns_articles (source_id, reference, version_no, supersedes, title, published_precision, "
            "observed_at, available_at, first_available_at, language, body_sha256, text_sha256, extracted_text, "
            "origin) values ('synthetic_news', %s, %s, %s, 't', 'none', now(), now(), now(), "
            "'en', repeat('a', 64), repeat('b', 64), %s, 'fetched')")
    assert "full_text" in refused(w, base, ("r1", 1, None, "some text"))
    assert "supersedes" in refused(w, base, ("r2", 2, 1, None))
    assert not refused(w, base, ("r3", 1, None, None))
    assert "first available" in refused(w, base.replace("now(), now(), now(), ", "now(), now(), now() - interval '1 day', "),
                                        ("r4", 1, None, None))
    # the database's CSE checks also catch a trailing dot and a URL whose host was not recorded
    for host, url in (("www.cse.lk.", "https://www.cse.lk./x"), (None, "https://cse.lk/x"),
                      ("news.example.test", "https://user@cdn.cse.lk:443/x"),
                      ("news.example.test", "https://a@b@cse.lk/x"), ("news.example.test", "svn+https://cse.lk/x")):
        assert refused(w, "insert into ns_fetch_attempts (source_id, purpose, url, host, state) values "
                          "('synthetic_news', 'article', %s, %s, 'sent')", (url, host)), (host, url)
    w.commit()
    for c in (w, o):
        assert refused(c, "update ns_articles set title = 'x'") and refused(c, "delete from ns_articles")
        c.rollback()
    assert "SHA-256" in refused(w, "insert into ns_configurations (configuration_id, configuration_json, "
                                   "lexicon_sha256) values (repeat('a', 64), '{}', repeat('b', 64))")
    w.rollback()


# ----------------------------------------------------------------------------------------------- the ledger


def test_p2_the_ledger_under_the_transport(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec = world(cluster, db)
    led = store.DbLedger(w)
    assert led.lock("synthetic_news")
    other = store.DbLedger(fs.conn(cluster, db, user="cse_worker"))
    assert not other.lock("synthetic_news")                                 # one session at a time per source
    slept = []
    a = f"https://{HOST}/a"

    class Checking(tp.ScriptedSender):
        def send(self, url, headers, timeout, max_body_bytes):              # committed before it is sent
            row = store._read(fs.conn(cluster, db, user="cse_worker"), lambda cur: (cur.execute(
                "select state from ns_fetch_attempts where url = %s order by attempt_id desc limit 1", (url,)),
                cur.fetchone())[1])
            assert row == ("sent",)
            return super().send(url, headers, timeout, max_body_bytes)
    t = tp.GovernedTransport(rec, Checking({n1.ROBOTS: n1.robots_open(), a: [tp.Response(403)]}), led,
                             clock=tp.utcnow, sleep=slept.append)
    assert t.get(a, "article").outcome == "stopped"
    assert t.get("https://www.cse.lk/x", "article").outcome == "stopped"     # stopped first; nothing sent
    # another process: still stopped until the owner clears it
    t2 = tp.GovernedTransport(rec, tp.ScriptedSender({a: [n1.ok()]}), other, clock=tp.utcnow, sleep=slept.append)
    assert t2.stopped == "http_403" and t2.get(a, "article").outcome == "stopped"
    store.clear_stop(o, "synthetic_news", "the owner reviewed the block")
    assert t2.stopped is None
    t3 = tp.GovernedTransport(rec, tp.ScriptedSender({n1.ROBOTS: n1.robots_open(), "https://www.cse.lk/x": []}), other,
                              clock=tp.utcnow, sleep=slept.append)
    assert t3.get("https://www.cse.lk/x", "article").code == "cse_refused"
    rows = store._read(w, lambda cur: (cur.execute("select state, host from ns_fetch_attempts order by attempt_id"),
                                       cur.fetchall())[1])
    assert ("refused", "www.cse.lk") in rows and not any(s == "sent" and "cse.lk" in (h or "") for s, h in rows)
    assert led.sent_on("synthetic_news", datetime.now(UTC).astimezone(store.COLOMBO).date()) == 2   # robots + a


# ----------------------------------------------------------------------------------------------- the whole path


ISSUER_ALIAS = ("Alpha Bank PLC", "iss_alpha")


def _ingest_and_process(cluster, db, storage="metadata_only"):
    o, w, rec = world(cluster, db, storage)
    store.add_alias(o, *ISSUER_ALIAS, "name", datetime(2026, 1, 1, tzinfo=UTC), "synthetic issuer record")
    cfg = cf.Config()
    cid = store.register_configuration(w, cfg)
    store.designate(o, "sentiment", cid, "the owner designates the provisional NS configuration")
    url = f"https://{HOST}/a1"
    t = tp.GovernedTransport(rec, tp.ScriptedSender({
        n1.ROBOTS: n1.robots_open(), n2.LIST: [tp.Response(200, {}, json.dumps({"items": [{"ref": "a1", "url": url}]})
                                                          .encode())],
        url: [tp.Response(200, {}, n2.item_json(title="Alpha Bank PLC posts record profit",
                                                text="Alpha Bank PLC posted record profit and strong growth."))]}),
        store.DbLedger(w), clock=tp.utcnow, sleep=lambda s: None)
    adapter = ad.SyntheticAdapter(t, n2.LIST)
    adapter.list_new()
    item = adapter.fetch("a1")
    v, _obs = ar.ingest(rec.source_id, [], [], item, rec.storage_level)
    vid, recorded = store.write_version(w, v)
    rows = store.load(w, Query(KNOWN, datetime.now(UTC)))
    res = pr.process(v.title, item.extracted_text, "en",
                     [a for a in rows["aliases"]], cfg)
    store.write_result(w, vid, res)
    store.record_run(w, rec.source_id, datetime.now(UTC).astimezone(store.COLOMBO).date(), "ran")
    return o, w, rec, cfg, recorded


def test_p3_from_a_synthetic_source_to_a_verified_snapshot(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec, cfg, recorded = _ingest_and_process(cluster, db)
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    t_before = recorded - timedelta(seconds=1)
    q_before = Query(KNOWN, t_before)
    with pytest.raises(store.Refused, match="no_configuration"):               # NL-6: none designated yet at T
        store.build_snapshot(w, q_before, sessions, ["iss_alpha"], cfg)
    sid0, snap0 = store.build_snapshot(w, q_before, [d for d in sessions if d <= t_before.astimezone(
        store.COLOMBO).date()], ["iss_alpha"], cfg, configuration_id=cfg.configuration_id())   # named explicitly
    early_row = next(r for r in snap0.rows if r.subject == "iss_alpha")
    assert early_row.stories == [] and early_row.state != "fresh"            # NL-1: the article is not there yet
    q = Query(KNOWN, datetime.now(UTC))
    sid, snap = store.build_snapshot(w, q, sessions, ["iss_alpha", "iss_quiet"], cfg)
    a = next(r for r in snap.rows if r.subject == "iss_alpha")
    assert a.features["news_count_5"][0] == 1 and a.stories and a.stories[0]["articles"] == ["synthetic_news:a1"]
    assert next(r for r in snap.rows if r.subject == "iss_quiet").state == "missing"
    rep = verify.verify(w, margin=timedelta(0))
    assert rep["ok"], rep
    assert all(s["replayed"] for s in rep["snapshots"])
    # a tamper: a stored row changed by a superuser with the triggers disabled
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table ns_snapshot_rows disable trigger user")
        cur.execute("update ns_snapshot_rows set state = 'fresh' where subject = 'iss_quiet'")
        cur.execute("alter table ns_snapshot_rows enable trigger user")
    su.commit()
    rep = verify.verify(w, margin=timedelta(0))
    assert not rep["ok"] and any("row_hash" in p for s in rep["snapshots"] for p in s["problems"])


def test_p4_nl8_current_is_never_point_in_time_and_edited_archives_only_in_current(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec, cfg, _recorded = _ingest_and_process(cluster, db)
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    now = datetime.now(UTC)
    sid, _s = store.build_snapshot(w, Query(CURRENT, now, now + timedelta(seconds=1)), sessions, ["iss_alpha"], cfg)
    with pytest.raises(store.Refused, match="NL-8"):
        store.require_point_in_time(w, sid)
    sid2, _s = store.build_snapshot(w, Query(KNOWN, now), sessions, ["iss_alpha"], cfg)
    assert store.require_point_in_time(w, sid2) == sid2
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 0}
    for state, ref in (("as_edited", "e1"), ("as_published", "p1")):
        iid = store.declare_archive(o, "synthetic_news", "synthetic archive terms", "original", state, "complete",
                                    date(2026, 1, 1), date(2026, 6, 30), "c" * 64)
        v = ar.archive_version("synthetic_news", ref, "Alpha Bank PLC record profit", date(2026, 3, 2), "date", "en",
                               "Alpha Bank PLC record profit.", "metadata_only", pol)
        store.write_version(w, v, archive_import_id=iid)
    later = datetime.now(UTC) + timedelta(minutes=1)
    refs = lambda q: {v.reference for v in store.load(w, q)["versions"]}          # noqa: E731
    assert "e1" not in refs(Query(AVAILABLE, later, later)) and "p1" in refs(Query(AVAILABLE, later, later))
    assert {"e1", "p1"} <= refs(Query(CURRENT, None, later))
    with pytest.raises(Exception, match="owner"):                                  # declaring is the owner's
        store.declare_archive(w, "synthetic_news", "x y z", "original", "as_published", "complete",
                              date(2026, 1, 1), date(2026, 1, 2), "d" * 64)


def test_p5_ns67_audit_selection_archive_age_and_the_configuration_object(cluster):
    from worker.news_sentiment import decay as dc
    db = fs.fresh_db(cluster)
    o, w, rec, cfg, _recorded = _ingest_and_process(cluster, db)
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 0}
    day = today - timedelta(days=10)
    for completeness, ref in (("selection", "s1"), ("complete", "c1")):
        iid = store.declare_archive(o, "synthetic_news", "synthetic archive terms", "original", "as_published",
                                    completeness, day - timedelta(days=1), day + timedelta(days=1), "e" * 64)
        v = ar.archive_version("synthetic_news", ref, "Alpha Bank PLC record profit", day, "date", "en",
                               "Alpha Bank PLC record profit.", "metadata_only", pol)
        vid, _rec = store.write_version(w, v, archive_import_id=iid)
        aliases = store.load(w, Query(KNOWN, datetime.now(UTC)))["aliases"]
        store.write_result(w, vid, pr.process(v.title, "Alpha Bank PLC record profit.", "en", aliases, cfg))
    later = datetime.now(UTC) + timedelta(minutes=1)
    refs = lambda q: {v.reference for v in store.load(w, q)["versions"]}          # noqa: E731
    assert "s1" not in refs(Query(AVAILABLE, later, later)) and "c1" in refs(Query(AVAILABLE, later, later))
    assert "s1" in refs(Query(CURRENT, None, later))                              # a selection: CURRENT only
    # the archive's age through the store: its stated date, never the day it was imported
    _sid, snap = store.build_snapshot(w, Query(AVAILABLE, later, later), sessions, ["iss_alpha"], cfg)
    a = next(r for r in snap.rows if r.subject == "iss_alpha")
    story = next(s for s in a.stories if "synthetic_news:c1" in s["articles"])
    first = next(v for v in store.load(w, Query(AVAILABLE, later, later))["versions"] if v.reference == "c1")
    assert story["age"] == dc.age(first.first_available_at, sessions) and story["age"] >= 9
    assert snap.imported_inputs
    # the configuration object must be the one named or designated
    other = cf.Config(half_lives=tuple((e, h + 1 if e == "other" else h) for e, h in cfg.half_lives))
    with pytest.raises(store.Refused, match="configuration"):
        store.build_snapshot(w, Query(KNOWN, later), sessions, ["iss_alpha"], other)


def test_p6_ns67_reaudit_verify_preflight_and_0022(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec, cfg, _recorded = _ingest_and_process(cluster, db)
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    store.build_snapshot(w, Query(KNOWN, datetime.now(UTC)), sessions, ["iss_alpha"], cfg)
    assert verify.verify(w, margin=timedelta(0))["ok"]
    su = fs.conn(cluster, db, user="postgres")

    def tamper(table, sql):
        with su.cursor() as cur:
            cur.execute(f"alter table {table} disable trigger user")
            cur.execute(sql)
            cur.execute(f"alter table {table} enable trigger user")
        su.commit()
    # the session sequence is bound into the snapshot hash
    tamper("ns_snapshots", "update ns_snapshots set sessions_json = (sessions_json::jsonb - 0)::text")
    rep = verify.verify(w, margin=timedelta(0))
    assert not rep["ok"] and any("snapshot_hash" in p for s in rep["snapshots"] for p in s["problems"]), rep
    # a result's own alias context must equal its stored alias set
    tamper("ns_article_results", "update ns_article_results set result_json = jsonb_set(result_json::jsonb, "
                                 "'{context_alias_ids}', '[999]')::text")
    assert any("alias context differs" in p for p in verify.verify(w, margin=timedelta(0))["results"])
    # preflight: a guard trigger disabled, or set to fire only on replica, is a problem
    for how in ("disable trigger trg_nsart_guard", "enable replica trigger trg_nsart_guard"):
        with su.cursor() as cur:
            cur.execute(f"alter table ns_articles {how}")
        su.commit()
        assert any("trg_nsart_guard" in p for p in preflight.problems(w)), how
        w.rollback()
    with su.cursor() as cur:
        cur.execute("alter table ns_articles enable trigger trg_nsart_guard")
    su.commit()
    assert preflight.problems(w) == []
    # an archive version must come from an import of its own source
    store.register_source(o, n1.record_bytes(source_id="synthetic_other"), "NS-SYNTHETIC-OTHER.md")
    iid = store.declare_archive(o, "synthetic_other", "synthetic archive terms", "original", "as_published",
                                "complete", date(2026, 1, 1), date(2026, 6, 30), "f" * 64)
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 0}
    v = ar.archive_version("synthetic_news", "x1", "Alpha Bank PLC record profit", date(2026, 3, 2), "date", "en",
                           "Alpha Bank PLC record profit.", "metadata_only", pol)
    with pytest.raises(Exception, match="import"):
        store.write_version(w, v, archive_import_id=iid)
    w.rollback()
    # a fetch-on-behalf CSE host, with a trailing dot, is refused even to the owner
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    assert "CSE" in refused(o, "insert into ns_sources (source_id, governance_record_ref, governance_record_sha256, "
                               "storage_level, availability_policy_json, allowed_hosts_json, "
                               "fetch_on_behalf_hosts_json) values ('x3', 'X.md', repeat('a', 64), 'metadata_only', "
                               "'{}', '[\"a.example.test\"]', '[\"www.cse.lk.\"]')")
    o.rollback()


def test_p7_ns9_stop_and_purge_a_source(cluster):
    db = fs.fresh_db(cluster)
    t0 = datetime.now(UTC) - timedelta(seconds=1)                 # before anything of the source was recorded
    o, w, rec, cfg, _recorded = _ingest_and_process(cluster, db)
    other = store.register_source(o, n1.record_bytes(source_id="synthetic_other"), "NS-SYNTHETIC-OTHER.md")
    store.start_ingestion(o, other.source_id, datetime(2026, 1, 1, tzinfo=UTC), "the owner starts the other source")
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    store.record_run(w, other.source_id, today, "ran")
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    s0, _ = store.build_snapshot(w, Query(KNOWN, t0), [d for d in sessions if d <= t0.astimezone(store.COLOMBO)
                                                      .date()], ["iss_alpha"], cfg,
                                 configuration_id=cfg.configuration_id())
    s1, snap1 = store.build_snapshot(w, Query(KNOWN, datetime.now(UTC)), sessions, ["iss_alpha"], cfg)
    assert "synthetic_news" in snap1.active_sources
    # refused: not the owner path; not stopped; no request reference
    with pytest.raises(store.Refused, match="owner_path"):
        store.purge_source(w, "synthetic_news", "a deletion request")
    with pytest.raises(store.Refused, match="not_stopped"):
        store.purge_source(o, "synthetic_news", "a deletion request")
    with pytest.raises(store.Refused, match="request"):
        store.purge_source(o, "synthetic_news", " ")
    store.stop_source(o, "synthetic_news", "the publisher asked for deletion")
    assert store.DbLedger(w).stopped("synthetic_news") == "the publisher asked for deletion"
    # a dry run reports and changes nothing
    dry = store.purge_source(o, "synthetic_news", "deletion request 2026-11-23 (synthetic)", dry_run=True)
    assert dry["dry_run"] and dry["counts"]["ns_articles"] == 1 and dry["counts"]["ns_article_results"] == 1
    assert [x["snapshot_id"] for x in dry["snapshots_replaced"]] == [s1]
    count = lambda sql, *a: (lambda c: (c.execute(sql, a), c.fetchone()[0])[1])(w.cursor())   # noqa: E731
    assert count("select count(*) from ns_articles where source_id = 'synthetic_news'") == 1
    w.rollback()
    # the purge
    m = store.purge_source(o, "synthetic_news", "deletion request 2026-11-23 (synthetic)")
    assert m["counts"]["ns_articles"] == 1 and m["counts"]["ns_fetch_attempts"] >= 2 and len(m["manifest_sha256"]) == 64
    assert "body_sha256s" in m and not any("Alpha" in str(v) for v in m.values())       # no content in the manifest
    for t in ("ns_articles", "ns_article_results", "ns_article_observations", "ns_fetch_attempts",
              "ns_ingestion_runs"):
        assert count(f"select count(*) from {t} where source_id = 'synthetic_news'"
                     if t != "ns_article_results" else "select count(*) from ns_article_results") == 0, t
    assert count("select count(*) from ns_sources where source_id = 'synthetic_news'") == 1
    assert count("select count(*) from ns_source_events where source_id = 'synthetic_news' and kind = "
                 "'ingestion_started'") == 0
    assert count("select count(*) from ns_ingestion_runs where source_id = 'synthetic_other'") == 1   # untouched
    w.rollback()
    assert store.DbLedger(w).stopped("synthetic_news").startswith("purged: ")
    # the snapshot that named it is replaced; the one that did not is kept
    new = m["replaced_by"][str(s1)]
    assert count("select count(*) from ns_snapshots where snapshot_id = %s", s1) == 0
    assert count("select count(*) from ns_snapshots where snapshot_id = %s", s0) == 1
    assert "synthetic_news" not in json.loads((lambda c: (c.execute(
        "select active_sources_json from ns_snapshots where snapshot_id = %s", (new,)), c.fetchone()[0])[1])(
        w.cursor()))
    w.rollback()
    # every guard back on; append-only holds again; verify is clean
    assert preflight.problems(w) == []
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
    assert refused(o, "delete from ns_source_events") and refused(o, "update ns_snapshots set mode = 'KNOWN'")
    o.rollback()
    rep = verify.verify(w, margin=timedelta(0))
    assert rep["ok"], rep


def test_p7_the_source_match_is_exact():
    stories = [json.dumps([{"articles": ["ftx:a1"]}])]
    assert not store._names_source("ft", json.dumps(["ftx"]), stories)
    assert store._names_source("ftx", json.dumps([]), stories)
    assert store._names_source("ft", json.dumps(["ft"]), [])


def test_p7_ns9_audit_a_snapshot_that_counted_the_source_without_naming_it(cluster):
    db = fs.fresh_db(cluster)
    o, w, rec, cfg, _recorded = _ingest_and_process(cluster, db)
    b = store.register_source(o, n1.record_bytes(source_id="synthetic_b"), "NS-SYNTHETIC-B.md")      # never started
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    pol = {"time_zone_offset_minutes": 330, "date_only_counts_from": "end_of_day", "availability_lag_minutes": 0}
    iid = store.declare_archive(o, b.source_id, "synthetic archive terms", "original", "as_published", "complete",
                                today - timedelta(days=5), today, "a" * 64)
    v = ar.archive_version(b.source_id, "b1", "Alpha Bank PLC in another language", today - timedelta(days=3),
                           "date", "si", "x", "metadata_only", pol)
    store.write_version(w, v, archive_import_id=iid)
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    later = datetime.now(UTC) + timedelta(seconds=1)
    sid, snap = store.build_snapshot(w, Query(KNOWN, later), sessions, ["iss_alpha", "iss_quiet"], cfg)
    assert snap.counts["unsupported_language"] == 1 and b.source_id not in snap.active_sources
    store.stop_source(o, b.source_id, "the publisher asked for deletion")
    m = store.purge_source(o, b.source_id, "deletion request (synthetic)")
    assert [x["snapshot_id"] for x in m["snapshots_replaced"]] == [sid] and not m["snapshots_replaced"][0]["named"]
    assert m["counts"]["ns_snapshot_rows"] == len(snap.rows)
    rep = verify.verify(w, margin=timedelta(0))
    assert rep["ok"], rep
    new = m["replaced_by"][str(sid)]
    with w.cursor() as cur:
        cur.execute("select counts_json from ns_snapshots where snapshot_id = %s", (new,))
        assert json.loads(cur.fetchone()[0])["unsupported_language"] == 0
        cur.execute("select subject from ns_snapshot_rows where snapshot_id = %s order by subject", (new,))
        assert [r[0] for r in cur.fetchall()] == sorted(r.subject for r in snap.rows)   # the same subjects
    w.rollback()
    # a busy source (its lock held by an ingest session) is refused
    store.stop_source(o, "synthetic_news", "another request")
    held = fs.conn(cluster, db, user="cse_worker")
    assert store.DbLedger(held).lock("synthetic_news")
    with pytest.raises(store.Refused, match="source_busy"):
        store.purge_source(o, "synthetic_news", "deletion request (synthetic)")
    held.close()


# ----------------------------------------------------------------------------------------------- NS-10: ingest


class _ApiSender:
    """A fake GDELT-shaped API on a `.test` host: robots 404, then one scripted listing per call."""

    def __init__(self, host, listings):
        self.host, self.listings, self.sent = host, list(listings), []

    def send(self, url, headers, timeout, max_body_bytes):
        self.sent.append(url)
        if url == f"https://{self.host}/robots.txt":
            return tp.Response(404, {}, b"")
        assert url.startswith(f"https://{self.host}/api/v2/doc/doc?"), url
        return self.listings.pop(0)


def _api_listing(*items):
    return tp.Response(200, {}, json.dumps({"articles": [
        {"url": u, "title": t, "seendate": "20261120T023000Z", "language": "English", "domain": "news.example.test",
         "sourcecountry": "Sri Lanka"} for u, t in items]}).encode())


def test_p8_ns10_ingest_one_source_end_to_end(cluster, tmp_path):
    from worker.news_store import ingest
    db = fs.fresh_db(cluster)
    o = fs.conn(cluster, db, user="cse_migrator")
    w = fs.conn(cluster, db, user="cse_worker")
    api = "api.gdelt.test"
    body = n1.record_bytes(source_id="gdelt_doc", allowed_hosts=[api], request_discipline={
        "min_interval_seconds": 1.0, "daily_budget": 50, "max_retries": 0, "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 5.0, "retry_after_max_seconds": 5.0, "max_consecutive_failures": 4,
        "max_body_bytes": 1_000_000})
    (tmp_path / "NS-GDELT-TEST.md").write_bytes(body)
    sender = _ApiSender(api, [])
    run = lambda: ingest.run_ingest(w, "gdelt_doc", sender=sender, records_dir=str(tmp_path))     # noqa: E731
    with pytest.raises(store.Refused, match="unknown_source"):
        run()
    store.register_source(o, body, "NS-GDELT-TEST.md")
    with pytest.raises(store.Refused, match="not_started"):
        run()
    store.start_ingestion(o, "gdelt_doc", datetime.now(UTC) - timedelta(days=1), "the owner starts GDELT (test)")
    store.add_alias(o, *ISSUER_ALIAS, "name", datetime(2026, 1, 1, tzinfo=UTC), "synthetic issuer record")
    # first pass, no configuration designated yet: stored, not processed
    sender.listings = [_api_listing(("https://news.example.test/a1", "Alpha Bank PLC posts record profit"),
                                    ("https://www." + "cse" + ".lk/x", "dropped"))]
    out = run()
    assert (out["listed"], out["new_articles"], out["unprocessed"], out["dropped_cse"], out["status"]) == \
        (1, 1, 1, 1, "ran")
    assert sender.sent[0].endswith("/robots.txt") and len(sender.sent) == 2          # robots, one listing; nothing else
    cfg = cf.Config()
    cid = store.register_configuration(w, cfg)
    store.designate(o, "sentiment", cid, "the owner designates the provisional NS configuration")
    # second pass: the same article (confirmed), a changed title (a revision), and a new one; all processed
    sender.listings = [_api_listing(("https://news.example.test/a1", "Alpha Bank PLC posts record profit again"),
                                    ("https://news.example.test/a2", "Alpha Bank PLC strong growth"))]
    out = run()
    assert (out["new_articles"], out["revisions"], out["processed"], out["status"]) == (1, 1, 2, "ran")
    sender.listings = [_api_listing(("https://news.example.test/a2", "Alpha Bank PLC strong growth"))]
    out = run()
    assert out["confirmed"] == 0 and out["new_articles"] == out["revisions"] == 0      # seen recently: no confirmation
    # the listing failing is a failed run; the ledger recorded every attempt before sending it
    sender.listings = [tp.Response(503, {}, b"")]
    assert run()["status"] == "failed"
    with w.cursor() as cur:
        cur.execute("select status from ns_ingestion_runs where source_id = 'gdelt_doc' order by run_id")
        assert [r[0] for r in cur.fetchall()] == ["ran", "ran", "ran", "failed"]
        cur.execute("select detail_json from ns_ingestion_runs where source_id = 'gdelt_doc' order by run_id")
        details = [json.loads(r[0]) for r in cur.fetchall()]
        assert details[-1]["listing"] != "ok" and not details[-1]["complete"] and details[0]["complete"]
        cur.execute("select count(*) from ns_fetch_attempts where source_id = 'gdelt_doc' and state = 'sent'")
        assert cur.fetchone()[0] == len(sender.sent)
    w.rollback()
    # a snapshot from what was ingested; verify is clean
    today = datetime.now(UTC).astimezone(store.COLOMBO).date()
    sessions = [today - timedelta(days=k) for k in range(25, -1, -1)]
    _sid, snap = store.build_snapshot(w, Query(KNOWN, datetime.now(UTC)), sessions, ["iss_alpha"], cfg)
    assert next(r for r in snap.rows if r.subject == "iss_alpha").stories
    assert verify.verify(w, margin=timedelta(0))["ok"]
    # refusals: stopped; busy; an edited record
    held = fs.conn(cluster, db, user="cse_worker")
    assert store.DbLedger(held).lock("gdelt_doc")
    with pytest.raises(store.Refused, match="source_busy"):
        run()
    held.close()
    (tmp_path / "NS-GDELT-TEST.md").write_bytes(body + b"\nedited\n")
    with pytest.raises(Exception, match="(?i)sha|hash|pinned"):
        run()
    (tmp_path / "NS-GDELT-TEST.md").write_bytes(body)
    store.stop_source(o, "gdelt_doc", "the owner stops it (test)")
    with pytest.raises(store.Refused, match="stopped"):
        run()


def test_p8_ns10_inspect_writes_nothing(tmp_path):
    from worker.news_store import ingest
    api = "api.gdelt.test"
    body = n1.record_bytes(source_id="gdelt_doc", allowed_hosts=[api])
    (tmp_path / "R.md").write_bytes(body)
    sender = _ApiSender(api, [_api_listing(("https://news.example.test/a1", "Alpha Bank PLC record"))])
    out = ingest.inspect(str(tmp_path / "R.md"), sender=sender)
    assert out["outcome"] == "ok" and out["listed"] == 1 and "seendate" in out["fields"]
    assert out["first"][0]["title"] == "Alpha Bank PLC record" and len(sender.sent) == 2



def test_p8_ns10_audit_paging_cursor_spacing_reprocessing_and_failures(cluster, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from worker.news_store import ingest
    db = fs.fresh_db(cluster)
    o = fs.conn(cluster, db, user="cse_migrator")
    w = fs.conn(cluster, db, user="cse_worker")
    api = "api.gdelt.test"
    body = n1.record_bytes(source_id="gdelt_doc", allowed_hosts=[api], request_discipline={
        "min_interval_seconds": 1.0, "daily_budget": 100, "max_retries": 0, "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 5.0, "retry_after_max_seconds": 5.0, "max_consecutive_failures": 4,
        "max_body_bytes": 2_000_000})
    (tmp_path / "NS-R.md").write_bytes(body)
    store.register_source(o, body, "NS-R.md")
    start = datetime.now(UTC) - timedelta(days=3)
    store.start_ingestion(o, "gdelt_doc", start, "the owner starts GDELT (test)")
    store.add_alias(o, *ISSUER_ALIAS, "name", datetime(2026, 1, 1, tzinfo=UTC), "synthetic issuer record")
    clock_now = [datetime.now(UTC)]

    def page(n, first):                              # n entries, newest first: item k seen k minutes before now
        return tp.Response(200, {}, json.dumps({"articles": [
            {"url": f"https://news.example.test/p{first + i}", "title": f"Alpha Bank PLC item {first + i}",
             "seendate": (clock_now[0] - timedelta(minutes=first + i)).strftime("%Y%m%dT%H%M%SZ"),
             "language": "English"} for i in range(n)]}).encode())
    sender = _ApiSender(api, [])
    def page_s(n, first):                            # the same, k SECONDS before now: 8 full pages stay inside 6 h
        return tp.Response(200, {}, json.dumps({"articles": [
            {"url": f"https://news.example.test/s{first + i}", "title": f"Alpha Bank PLC item s{first + i}",
             "seendate": (clock_now[0] - timedelta(seconds=first + i)).strftime("%Y%m%dT%H%M%SZ"),
             "language": "English"} for i in range(n)]}).encode())

    def run(**kw):
        return ingest.run_ingest(w, "gdelt_doc", sender=sender, records_dir=str(tmp_path),
                                 clock=lambda: clock_now[0], **kw)
    # Major 1: a full first page is followed by a page ending at its oldest entry; complete when a page is not full
    sender.listings = [page(250, 0), page(10, 250)]
    out = run()
    assert (out["pages"], out["listed"], out["complete"], out["status"]) == (2, 260, True, "ran")
    q2 = parse_qs(urlsplit(sender.sent[-1]).query)
    assert "enddatetime" in q2 and q2["startdatetime"] == [max(start, clock_now[0] - gd_window()).astimezone(UTC)
                                                            .strftime("%Y%m%d%H%M%S")]
    # with no designation the 260 are unprocessed; a full listing that cannot finish keeps the cursor
    sender.listings = [page_s(250, 250 * k) for k in range(ingest.MAX_PAGES)]
    out = run()
    assert (out["complete"], out["status"], out["pages"]) == (False, "partial", ingest.MAX_PAGES)
    sender.listings = [page(1, 9000)]
    out = run()
    assert out["since"] == (clock_now[0] - ingest.OVERLAP).isoformat()           # from the last COMPLETE pass
    # Minor 1: designated later, a version is scored when it is listed again
    cfg = cf.Config()
    store.designate(o, "sentiment", store.register_configuration(w, cfg), "the owner designates (test)")
    clock_now[0] = datetime.now(UTC) + timedelta(seconds=1)          # the designation is in force from here
    sender.listings = [page(3, 0)]
    out = run()
    assert out["processed"] == 3 and out["confirmed"] == 0
    # Minor 4: a confirmation only after the spacing
    clock_now[0] += ingest.CONFIRM_SPACING + timedelta(hours=1)
    sender.listings = [page(3, 0)]
    assert run()["confirmed"] == 3
    # Minor 2: a pass that raises writes a failed run, re-raises, and releases the lock
    class Boom:
        def send(self, *a):
            raise RuntimeError("the network fell over")
    sender_saved, sender = sender, Boom()
    with pytest.raises(RuntimeError):
        run()
    sender = sender_saved
    with w.cursor() as cur:
        cur.execute("select status, detail_json from ns_ingestion_runs order by run_id desc limit 1")
        st, dj = cur.fetchone()
        assert st == "failed" and json.loads(dj)["error"] == "RuntimeError"
    w.rollback()
    assert store.DbLedger(fs.conn(cluster, db, user="cse_worker")).lock("gdelt_doc")      # released
    # Major 2: inspect refuses a registered source
    with pytest.raises(store.Refused, match="registered"):
        ingest.inspect(str(tmp_path / "NS-R.md"), conn=w, sender=_ApiSender(api, []))


def gd_window():
    from worker.news_ingest import gdelt
    return gdelt.WINDOW - timedelta(hours=1)



class _CorpusApi:
    """A fake API over a corpus [(seen_at, url, title)] that honours startdatetime/enddatetime (inclusive), newest
    first, at most `maxrecords` (250)."""

    def __init__(self, host, corpus):
        self.host, self.corpus, self.sent = host, corpus, []

    def send(self, url, headers, timeout, max_body_bytes):
        from urllib.parse import parse_qs, urlsplit
        self.sent.append(url)
        if url.endswith("/robots.txt"):
            return tp.Response(404, {}, b"")
        q = parse_qs(urlsplit(url).query)
        lo = datetime.strptime(q["startdatetime"][0], "%Y%m%d%H%M%S").replace(tzinfo=UTC) if "startdatetime" in q else None
        hi = datetime.strptime(q["enddatetime"][0], "%Y%m%d%H%M%S").replace(tzinfo=UTC) if "enddatetime" in q else None
        rows = sorted((c for c in self.corpus if (lo is None or c[0] >= lo) and (hi is None or c[0] <= hi)),
                      reverse=True)[:int(q["maxrecords"][0])]
        return tp.Response(200, {}, json.dumps({"articles": [
            {"url": u, "title": t, "seendate": at.strftime("%Y%m%dT%H%M%SZ"), "language": "English"}
            for at, u, t in rows]}).encode())


def test_p8_ns10_reaudit_a_backlog_is_worked_through_and_never_wedges(cluster, tmp_path):
    from worker.news_store import ingest
    db = fs.fresh_db(cluster)
    o = fs.conn(cluster, db, user="cse_migrator")
    w = fs.conn(cluster, db, user="cse_worker")
    api = "api.gdelt.test"
    body = n1.record_bytes(source_id="gdelt_doc", allowed_hosts=[api], request_discipline={
        "min_interval_seconds": 1.0, "daily_budget": 1000, "max_retries": 0, "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 5.0, "retry_after_max_seconds": 5.0, "max_consecutive_failures": 4,
        "max_body_bytes": 5_000_000})
    (tmp_path / "NS-R.md").write_bytes(body)
    store.register_source(o, body, "NS-R.md")
    now = datetime.now(UTC).replace(microsecond=0)
    store.start_ingestion(o, "gdelt_doc", now - timedelta(days=2), "the owner starts GDELT (test)")
    # 1,500 articles in the last 25 hours, 60 s apart in groups of three sharing a time (GDELT rounds seendate)
    corpus = [(now - timedelta(minutes=1) - timedelta(seconds=60 * (k // 3)), f"https://news.example.test/c{k}",
               f"Item {k}") for k in range(1500)]
    api_fake = _CorpusApi(api, corpus)
    clock = [now]
    run = lambda: ingest.run_ingest(w, "gdelt_doc", sender=api_fake, records_dir=str(tmp_path),   # noqa: E731
                                    clock=lambda: clock[0])
    first = run()
    assert (first["status"], first["pages"], first["complete"]) == ("partial", ingest.MAX_PAGES, False)
    clock[0] += timedelta(hours=1)
    api_fake.corpus.append((clock[0] - timedelta(minutes=1), "https://news.example.test/new", "Item new"))
    second = run()                                   # continues the same window backwards, does not restart at now
    assert second["since"] == first["since"] and second["upper"] == first["upper"]
    assert second["complete"] and second["status"] == "ran"
    with w.cursor() as cur:
        cur.execute("select count(distinct reference) from ns_articles where source_id = 'gdelt_doc'")
        assert cur.fetchone()[0] == 1500             # every article of the backlog, none lost at a page boundary
    w.rollback()
    clock[0] += timedelta(hours=1)
    third = run()                                    # then a fresh window picks up what arrived meanwhile
    assert datetime.fromisoformat(third["since"]) == datetime.fromisoformat(first["upper"]) - ingest.OVERLAP
    passes, new = [third], third["new_articles"]
    while not passes[-1]["complete"]:                 # a dense overlap may take another pass; it always ends
        assert len(passes) < 3
        clock[0] += timedelta(hours=1)
        passes.append(run())
        new += passes[-1]["new_articles"]
    assert new == 1 and passes[-1]["status"] == "ran"



@pytest.mark.parametrize("inclusive", [True, False])
def test_p8_ns10_more_than_a_page_sharing_one_second_never_stops_progress(cluster, tmp_path, inclusive):
    from worker.news_store import ingest
    db = fs.fresh_db(cluster)
    o = fs.conn(cluster, db, user="cse_migrator")
    w = fs.conn(cluster, db, user="cse_worker")
    api = "api.gdelt.test"
    body = n1.record_bytes(source_id="gdelt_doc", allowed_hosts=[api], request_discipline={
        "min_interval_seconds": 1.0, "daily_budget": 1000, "max_retries": 0, "backoff_base_seconds": 1.0,
        "backoff_max_seconds": 5.0, "retry_after_max_seconds": 5.0, "max_consecutive_failures": 4,
        "max_body_bytes": 5_000_000})
    (tmp_path / "NS-R.md").write_bytes(body)
    store.register_source(o, body, "NS-R.md")
    now = datetime.now(UTC).replace(microsecond=0)
    store.start_ingestion(o, "gdelt_doc", now - timedelta(hours=2), "the owner starts GDELT (test)")
    crowded = now - timedelta(minutes=30)
    corpus = [(crowded, f"https://news.example.test/x{k}", f"Item x{k}") for k in range(300)] + \
             [(crowded - timedelta(minutes=5), f"https://news.example.test/y{k}", f"Item y{k}") for k in range(10)]
    fake = _CorpusApi(api, corpus)
    fake_send = fake.send if inclusive else (lambda url, *a: _exclusive(fake, url, *a))   # an exclusive end
    sender = type("S", (), {"send": staticmethod(fake_send)})()
    out = ingest.run_ingest(w, "gdelt_doc", sender=sender, records_dir=str(tmp_path), clock=lambda: now)
    assert out["skipped_seconds"] >= 1 and out["complete"]
    with w.cursor() as cur:
        cur.execute("select count(*) from ns_articles where source_id = 'gdelt_doc' and reference in (%s)" %
                    ",".join(["%s"] * 10), [gd_ref(f"https://news.example.test/y{k}") for k in range(10)])
        assert cur.fetchone()[0] == 10               # the articles past the crowded second are reached
    w.rollback()


def _exclusive(fake, url, *a):
    from urllib.parse import parse_qs, urlencode, urlsplit
    parts = urlsplit(url)
    if not parts.query:
        return fake.send(url, *a)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    if "enddatetime" in q:
        end = datetime.strptime(q["enddatetime"], "%Y%m%d%H%M%S") - timedelta(seconds=1)
        q["enddatetime"] = end.strftime("%Y%m%d%H%M%S")
    return fake.send(f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode(q)}", *a)


def gd_ref(url):
    from worker.news_ingest import gdelt
    return gdelt.reference_of(url)


def test_p9_register_configuration_command_then_designate(cluster, capsys):
    from worker.news_store import cli
    db = fs.fresh_db(cluster)
    o, w, _rec = world(cluster, db)
    for _ in range(2):                                # registering twice is harmless
        assert cli.run(cli.parser().parse_args(["register-configuration"]),
                       lambda: fs.conn(cluster, db, user="cse_worker")) == cli.EXIT_OK
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["configuration_id"] == cf.Config().configuration_id()
    store.designate(o, "sentiment", out["configuration_id"], "the owner designates the v1 configuration")
    assert store.configuration_in_force(w, "sentiment", Query(KNOWN, datetime.now(UTC) + timedelta(seconds=1))) \
        == out["configuration_id"]
