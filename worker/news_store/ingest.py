"""
NS-10: the ingest command (`cse-news ingest`), and the owner's first-listing inspection (`cse-news inspect`).

`ingest --source ID` runs one pass for one registered source, as the worker:
1. **Refusals.** The source must be registered, started (an `ingestion_started` row in force now), and not stopped.
   Its governance record must still hash to the pinned SHA-256 with the pinned hosts (`governance.load`). The source's
   advisory lock must be free (one ingest per source; the purge takes the same lock).
2. **One listing** through the governed transport, with the database ledger. `since` is the latest article this source
   was seen with, minus an overlap (GDELT indexes late), or the source's start, whichever is later.
3. **Each listed reference** is fetched from the listing (no request for a metadata-only adapter) and passed to
   `articles.ingest` with its stored versions and observations: a new article is version 1, a changed title a new
   version, the same title a `confirmed` observation. Known references are included, so a revision is caught.
4. **Processing.** Each new version is processed with the aliases admitted now and the configuration designated for
   `sentiment` now (NL-6); with none designated, versions are stored and processing waits (`unprocessed`).
5. **The run row** for today (Colombo): `ran`, or `partial` when the listing was full (`truncated`), entries were
   malformed, or a processing error occurred; `failed` when the listing failed. A stopped transport stops the pass.

Adapters are chosen by the source id (`ADAPTERS`). No re-fetch is planned for an adapter whose `REFETCH` is False.
`inspect --record FILE` makes ONE listing request with an in-memory ledger and writes nothing: it prints the field
names and the first entries, for the owner to check the API's JSON before `start-ingestion` (C-0125).
"""
import json
import os
from datetime import datetime, timedelta, timezone

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
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RECORDS = os.path.join(REPO, "docs", "governance")


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
    cur.execute("select max(observed_at) from ns_articles where source_id = %s and origin = 'fetched'", (source_id,))
    last = cur.fetchone()[0]
    return dict(ref=row[0], sha256=row[1], hosts=json.loads(row[2]), storage=row[3], started=starts[0], last=last)


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
        since = src["started"] if src["last"] is None else max(src["started"], src["last"] - OVERLAP)
        return _pass(conn, adapter, t, source_id, src["storage"], since)
    finally:
        store._write(conn, lambda cur: cur.execute("select pg_advisory_unlock(hashtext(%s))",
                                                   (f"ns_source:{source_id}",)))


def _pass(conn, adapter, t, source_id, storage, since):
    out = {"source_id": source_id, "listed": 0, "new_articles": 0, "revisions": 0, "confirmed": 0, "processed": 0,
           "unprocessed": 0, "processing_errors": 0}
    refs = adapter.list_new(since)
    out.update(listed=len(refs), malformed=adapter.malformed, dropped_cse=adapter.dropped_cse,
               duplicates=adapter.duplicates, truncated=adapter.truncated, listing=adapter.last_outcome)
    now = t.clock()
    q = Query(KNOWN, now)
    rows = store.load(conn, q)
    cid = store.configuration_in_force(conn, "sentiment", q)
    cfg = None
    if cid is not None:
        text = store._read(conn, lambda cur: (cur.execute(
            "select configuration_json from ns_configurations where configuration_id = %s", (cid,)),
            cur.fetchone()[0])[1])
        cfg = store.config_from_json(text)
    admitted = al.admitted(rows["aliases"], rows["ends"], q)
    for ref in refs:
        item = adapter.fetch(ref)
        if item is None:
            continue
        mine = [v for v in rows["versions"] if (v.source_id, v.reference) == (source_id, ref)]
        obs = [o for o in rows["observations"] if (o.source_id, o.reference) == (source_id, ref)]
        v, new_obs = ar.ingest(source_id, mine, obs, item, storage)
        for o in new_obs:
            store.write_observation(conn, o)
            out["confirmed"] += o.kind == "confirmed"
        if v is None:
            continue
        vid, _recorded = store.write_version(conn, v)
        out["new_articles" if v.version_no == 1 else "revisions"] += 1
        if cfg is None:
            out["unprocessed"] += 1
            continue
        try:
            res = pr.process(v.title, item.extracted_text, v.language, admitted, cfg,
                             full_text=storage == "full_text")
            store.write_result(conn, vid, res)
            out["processed"] += 1
        except ValueError:
            out["processing_errors"] += 1
    status = "ran"
    if out["truncated"] or out["malformed"] or out["processing_errors"]:
        status = "partial"
    if adapter.last_outcome != "ok":
        status = "failed"
    out["status"] = status
    store.record_run(conn, source_id, now.astimezone(store.COLOMBO).date(), status,
                     {k: out[k] for k in ("listed", "new_articles", "revisions", "malformed", "dropped_cse",
                                          "truncated", "processing_errors")})
    return out


def inspect(record_path, sender=None, clock=None, sleep=None, show=5):
    """ONE listing request for a record (not registered yet), with an in-memory ledger; nothing is written. Prints
    what the owner checks before `start-ingestion`: the JSON field names and the first entries as parsed."""
    data = open(record_path, "rb").read()
    record = gv.parse(data)
    if record.source_id not in ADAPTERS:
        raise store.Refused("no_adapter", f"no adapter for source {record.source_id!r}")
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
