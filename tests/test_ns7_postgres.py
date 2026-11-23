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
