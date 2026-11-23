"""
NS-10: the ingest command (`cse-news ingest`), and the owner's first-listing inspection (`cse-news inspect`).

`ingest --source ID` runs one pass for one registered source, as the worker:
1. **Refusals**, in order: no adapter; an unregistered source; not started (no `ingestion_started` in force now);
   stopped; busy (its advisory lock held: one ingest per source, and the purge takes the same lock); a governance
   record that no longer hashes to the pinned SHA-256 with the pinned hosts (`governance.load`).
2. **The window** (`_window`). After a complete pass, a fresh window: from that pass's upper bound minus an overlap
   (GDELT indexes late), or the source's start, up to now. After an incomplete pass, the SAME window, continued
   backwards from where it stopped (`resume`): a backlog larger than one pass is worked through over several passes
   and never wedges (NS-10 audit Major 1; re-audit Major).
3. **The listing, paged.** Newest first, page by page: while a page is full, the next page ends one second after its
   oldest first-seen time (`enddatetime`), up to `MAX_PAGES`. A page that is not full, or that reaches back to
   `since`, completes the window; otherwise the pass is `partial` and records where it stopped.
4. **Each listed reference** goes through `articles.ingest` with this source's stored versions and observations,
   read directly (no admission filter): a new article is version 1, a changed title a new version. A confirmation is
   written only when the article was last seen more than `CONFIRM_SPACING` ago (they matter only for withdrawals,
   which this adapter never infers).
5. **Processing** with the aliases admitted now and the configuration designated for `sentiment` now (NL-6). A
   version with no result under that configuration is processed when it is stored **or listed again**; with none
   designated, versions are stored and processed on a later listing (designate before `start-ingestion`).
6. **The run row** for today (Colombo): `ran`; `partial` (not complete, malformed entries, or a processing error);
   `failed` (a listing failed or stopped, or the pass raised: the row is written, then the error re-raised).

`inspect --record FILE` is for a record **not yet registered** (it refuses a registered source id: then the source
is governed by the ledger, its stop and its budget). It makes ONE listing with an in-memory ledger and writes
nothing; it prints the field names and the first entries for the owner to check before `register-source`.
"""
import json
import os
from datetime import datetime, timedelta

from worker.market_features.modes import KNOWN, Query
from worker.news_ingest import articles as ar
from worker.news_ingest import gdelt as gd
from worker.news_ingest import governance as gv
from worker.news_ingest import transport as tp
from worker.news_sentiment import aliases as al
from worker.news_sentiment import process as pr

from . import store

ADAPTERS = {"gdelt_doc": gd.GdeltDocAdapter}
OVERLAP = timedelta(hours=6)
MAX_PAGES = 4                                # 1 robots + 4 listings an hour: 120 a day, under the budget of 200
CONFIRM_SPACING = timedelta(hours=24)
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RECORDS = os.path.join(REPO, "docs", "governance")
DETAIL = ("since", "upper", "resume", "listed_at", "complete", "pages", "listing", "listed", "new_articles",
          "revisions", "confirmed", "processed", "unprocessed", "malformed", "dropped_cse", "processing_errors", "error")


def _source(cur, source_id):
    cur.execute("select governance_record_ref, governance_record_sha256, allowed_hosts_json, storage_level from "
                "ns_sources where source_id = %s", (source_id,))
    row = cur.fetchone()
    if row is None:
        raise store.Refused("unknown_source", f"no source {source_id!r} is registered")
    cur.execute("select effective_at from ns_source_events where source_id = %s and kind = 'ingestion_started' "
                "and effective_at <= now() order by effective_at", (source_id,))
    starts = [r[0] for r in cur.fetchall()]
    if not starts:
        raise store.Refused("not_started", "the owner has not started this source (`start-ingestion`)")
    cur.execute("select detail_json from ns_ingestion_runs where source_id = %s order by run_id desc", (source_id,))
    state = None                                     # the latest pass that listed anything decides where to go on
    for (dj,) in cur.fetchall():
        d = json.loads(dj)
        if d.get("upper"):
            state = d
            break
    return dict(ref=row[0], sha256=row[1], hosts=json.loads(row[2]), storage=row[3], started=starts[0], state=state)


def _mine(cur, source_id):
    """This source's versions (with their ids) and observations, read directly: no admission filter (audit Minor 3)."""
    cur.execute("select source_id, reference, version_no, supersedes, title, published_at, published_precision, "
                "language, body_sha256, text_sha256, extracted_text, observed_at, available_at, first_available_at, "
                "recorded_at, origin, article_version_id from ns_articles where source_id = %s "
                "order by article_version_id", (source_id,))
    versions, ids = [], {}
    for r in cur.fetchall():
        v = ar.Version(*r[:14], recorded_at=r[14], origin=r[15])
        versions.append(v)
        ids[(v.reference, v.version_no)] = r[16]
    cur.execute("select source_id, reference, kind, observed_at, available_at, detail, recorded_at from "
                "ns_article_observations where source_id = %s", (source_id,))
    obs = [ar.Observation(*r) for r in cur.fetchall()]
    return versions, ids, obs


def run_ingest(conn, source_id, sender=None, clock=None, sleep=None, records_dir=RECORDS):
    """One ingest pass. `sender`/`clock`/`sleep` are for tests (a scripted sender); the real ones by default.
    Returns a summary (counts only)."""
    if source_id not in ADAPTERS:
        raise store.Refused("no_adapter", f"no adapter for source {source_id!r}")
    src = store._read(conn, lambda cur: _source(cur, source_id))
    ledger = store.DbLedger(conn)
    if ledger.stopped(source_id):
        raise store.Refused("stopped", f"the source is stopped: {ledger.stopped(source_id)}")
    if not ledger.lock(source_id):
        raise store.Refused("source_busy", "another session holds the source's lock")
    try:
        path = os.path.join(records_dir, os.path.basename(src["ref"]))
        record = gv.load(path, src["sha256"], tuple(src["hosts"]))
        if sender is None:
            from worker.news_ingest import wiring
            t = wiring.real_transport(path, src["sha256"], tuple(src["hosts"]), ledger)
        else:
            t = tp.GovernedTransport(record, sender, ledger, clock=clock or tp.utcnow, sleep=sleep or (lambda s: None))
        adapter = ADAPTERS[source_id](t)
        since, upper, until, resume = _window(src, t.clock())
        out = {"source_id": source_id, "since": since.isoformat(), "upper": upper.isoformat(), "complete": False}
        if resume is not None:                       # a failure before any progress keeps the earlier resume point
            out["resume"] = resume.isoformat()
        try:
            return _pass(conn, adapter, t, source_id, src["storage"], since, upper, until, out)
        except Exception as exc:                      # the run row says so, then the error goes up (audit Minor 2)
            out.update(status="failed", complete=False, error=type(exc).__name__)
            try:
                conn.rollback()
                _record(conn, source_id, t.clock(), out)
            except Exception:                         # noqa: BLE001 - never hide the original error (re-audit Minor 3)
                pass
            raise
    finally:
        store._write(conn, lambda cur: cur.execute("select pg_advisory_unlock(hashtext(%s))",
                                                   (f"ns_source:{source_id}",)))


def _window(src, now):
    """(since, upper, until) for this pass (re-audit Major).
    - After a complete pass (or none): a fresh window, from its `upper` minus the overlap (or the start) up to now.
    - After an incomplete one: continue the SAME window backwards from where it stopped (`resume`), so a backlog
      larger than one pass's pages is worked through over several passes and never wedges. Newer articles wait
      for the next fresh window; the 3-month API window bounds how long."""
    st = src["state"]
    if st and not st.get("complete"):
        since, upper = datetime.fromisoformat(st["since"]), datetime.fromisoformat(st["upper"])
        resume = datetime.fromisoformat(st["resume"]) if st.get("resume") else None
        until = resume + timedelta(seconds=1) if resume else upper
        return since, upper, until, resume
    since = src["started"] if not st else max(src["started"], datetime.fromisoformat(st["upper"]) - OVERLAP)
    return since, now, None, None


def _record(conn, source_id, now, out):
    store.record_run(conn, source_id, now.astimezone(store.COLOMBO).date(), out["status"],
                     {k: out[k] for k in DETAIL if k in out})


def _pass(conn, adapter, t, source_id, storage, since, upper, until, out):
    now = t.clock()
    out.update(listed_at=now.isoformat(), complete=False, pages=0, listed=0, new_articles=0, revisions=0, confirmed=0,
               processed=0, unprocessed=0, malformed=0, dropped_cse=0, processing_errors=0, listing="not_sent")
    q = Query(KNOWN, now)
    rows = store.load(conn, q)                       # aliases and ends; admission is the pure module's
    admitted = al.admitted(rows["aliases"], rows["ends"], q)
    cid = store.configuration_in_force(conn, "sentiment", q)
    cfg = None
    if cid is not None:
        text = store._read(conn, lambda cur: (cur.execute(
            "select configuration_json from ns_configurations where configuration_id = %s", (cid,)),
            cur.fetchone()[0])[1])
        cfg = store.config_from_json(text)
    versions, ids, obs = store._read(conn, lambda cur: _mine(cur, source_id))
    with_result = set()
    if cid is not None:
        with_result = set(store._read(conn, lambda cur: (cur.execute(
            "select r.article_version_id from ns_article_results r join ns_articles a on a.article_version_id = "
            "r.article_version_id where a.source_id = %s and r.configuration_id = %s", (source_id, cid)),
            [r[0] for r in cur.fetchall()])[1]))
    seen = set()
    resume = datetime.fromisoformat(out["resume"]) if out.get("resume") else None
    for _page in range(MAX_PAGES):
        refs = adapter.list_new(since, until)
        out["pages"] += 1
        out["listing"] = adapter.last_outcome
        out["malformed"] += adapter.malformed
        out["dropped_cse"] += adapter.dropped_cse
        adapter.malformed = adapter.dropped_cse = 0
        if adapter.last_outcome != "ok":
            break
        fresh = [r for r in refs if r not in seen]
        for ref in fresh:
            seen.add(ref)
            item = adapter.fetch(ref)
            if item is not None:
                _one(conn, source_id, storage, item, versions, ids, obs, with_result, cfg, admitted, out)
        out["listed"] = len(seen)
        oldest = adapter.oldest_seen()
        if oldest is not None:
            resume = oldest if resume is None else min(resume, oldest)
        if not adapter.truncated or (oldest is not None and oldest <= since):
            out["complete"] = True                   # a page not full, or one reaching back to `since`: covered
            break
        if oldest is None or not fresh:
            break                                    # no progress: incomplete; the next pass resumes here
        # the next page ends one second after the oldest: an inclusive or exclusive `enddatetime` both keep the
        # articles sharing that time (repeats are dropped by `seen`) (re-audit Minor 1)
        until = oldest + timedelta(seconds=1)
    if not out["complete"] and resume is not None:
        out["resume"] = resume.isoformat()
    status = "ran"
    if not out["complete"] or out["malformed"] or out["processing_errors"]:
        status = "partial"
    if out["listing"] != "ok":
        status, out["complete"] = "failed", False
    out["status"] = status
    _record(conn, source_id, now, out)
    return out


def _one(conn, source_id, storage, item, versions, ids, obs, with_result, cfg, admitted, out):
    mine = [v for v in versions if v.reference == item.reference]
    latest, last_seen = None, None
    if mine:
        latest = max(mine, key=lambda v: v.version_no)
        last_seen = max([latest.observed_at or latest.available_at] + [
            o.observed_at for o in obs if o.reference == item.reference and o.kind == "confirmed"])
    v, new_obs = ar.ingest(source_id, mine, [o for o in obs if o.reference == item.reference], item, storage)
    for o in new_obs:
        if o.kind == "confirmed" and item.observed_at - last_seen < CONFIRM_SPACING:
            continue                                 # seen recently: a confirmation adds nothing (audit Minor 4)
        store.write_observation(conn, o)
        obs.append(o)
        out["confirmed"] += o.kind == "confirmed"
    if v is not None:
        vid, _recorded = store.write_version(conn, v)
        versions.append(v)
        ids[(v.reference, v.version_no)] = vid
        out["new_articles" if v.version_no == 1 else "revisions"] += 1
        target, tvid = v, vid
    else:
        target, tvid = latest, ids[(latest.reference, latest.version_no)]
    if cfg is None:
        out["unprocessed"] += v is not None
        return
    if tvid in with_result:
        return
    try:
        text = item.extracted_text                   # the same text hash: the listed item's text is the version's
        res = pr.process(target.title, text, target.language, admitted, cfg, full_text=storage == "full_text")
        store.write_result(conn, tvid, res)
        with_result.add(tvid)
        out["processed"] += 1
    except ValueError:
        out["processing_errors"] += 1


def inspect(record_path, conn=None, sender=None, clock=None, sleep=None, show=5):
    """ONE listing for a record NOT yet registered (refused when `conn` shows it registered), with an in-memory
    ledger; nothing is written. Returns the field names and the first entries as parsed."""
    data = open(record_path, "rb").read()
    record = gv.parse(data)
    if record.source_id not in ADAPTERS:
        raise store.Refused("no_adapter", f"no adapter for source {record.source_id!r}")
    if conn is not None:
        registered = store._read(conn, lambda cur: (cur.execute(
            "select 1 from ns_sources where source_id = %s", (record.source_id,)), cur.fetchone())[1])
        if registered:
            raise store.Refused("registered", "the source is registered: it is governed by its ledger, stop and "
                                              "budget; use `ingest` (NS-10 audit, Major 2)")
    if sender is None:
        from worker.news_ingest import wiring
        t = wiring.inspection_transport(data)
    else:
        t = tp.GovernedTransport(record, sender, tp.MemoryLedger(), clock=clock or tp.utcnow,
                                 sleep=sleep or (lambda s: None))
    adapter = ADAPTERS[record.source_id](t)
    refs = adapter.list_new(t.clock() - timedelta(days=1))
    raw = adapter.last_body or b""
    try:
        doc = json.loads(raw.decode("utf-8"))
        entries = doc.get("articles", []) if isinstance(doc, dict) else []
        fields = sorted({k for e in entries if isinstance(e, dict) for k in e})
    except (UnicodeDecodeError, ValueError):
        fields = []
    items = [adapter.fetch(r) for r in refs[:show]]
    return {"outcome": adapter.last_outcome, "listed": len(refs), "fields": fields, "malformed": adapter.malformed,
            "dropped_cse": adapter.dropped_cse, "truncated": adapter.truncated,
            "first": [{"title": i.title, "url": i.url, "published_at": i.published_at.isoformat(),
                       "language": i.language} for i in items if i is not None]}
