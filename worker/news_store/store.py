"""
The NS store (design §11, NS-7). Owner decisions go through the owner path (session `cse_migrator`, `SET LOCAL ROLE
cse_owner`); the worker writes what it fetched and computed. The database stamps every row (`recorded_at = now()`,
writer = the login), keeps every table append-only, keeps text only for `full_text` sources, and refuses a CSE host
in a source or in a sent attempt.

What is admitted at T is read here and decided by the pure modules (`news_ingest.articles`, `news_sentiment.*`).
NL-8: a CURRENT snapshot is never handed to a point-in-time consumer, and an `as_edited` or `selection` archive is
admitted to CURRENT only.
"""
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from worker.market_features.canonical import canonical_json
from worker.market_features.modes import AVAILABLE, CURRENT, KNOWN, LABELS, Query

from worker.news_ingest import articles as ar
from worker.news_ingest import governance as gv
from worker.news_sentiment import aliases as al
from worker.news_sentiment import config as cf
from worker.news_sentiment import process as pr
from worker.news_sentiment import results as rs
from worker.news_sentiment import snapshot as sn

OWNER_PATH_ROLE = "cse_migrator"
COLOMBO = timezone(timedelta(hours=5, minutes=30))


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _owner(cur):
    cur.execute("select session_user")
    who = cur.fetchone()[0]
    if who != OWNER_PATH_ROLE:
        raise Refused("owner_path", f"an owner step: connected as {who!r}; run it through the owner path "
                                    f"({OWNER_PATH_ROLE} acting as cse_owner)")
    cur.execute("set local role cse_owner")


def _write(conn, work, owner=False):
    try:
        with conn.cursor() as cur:
            if owner:
                _owner(cur)
            out = work(cur)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise


def _read(conn, work):
    try:
        with conn.cursor() as cur:
            return work(cur)
    finally:
        conn.rollback()


def _js(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ----------------------------------------------------------------------------------------------- owner decisions


def register_source(owner_conn, record_bytes, record_ref):
    """P6-R1: the owner registers a source from its governance record (validated by `governance.parse`); the record's
    SHA-256 and allowed hosts are pinned."""
    rec = gv.parse(record_bytes)
    return _write(owner_conn, lambda cur: cur.execute(
        "insert into ns_sources (source_id, governance_record_ref, governance_record_sha256, storage_level, "
        "availability_policy_json, allowed_hosts_json, fetch_on_behalf_hosts_json, onward_disclosure) values "
        "(%s, %s, %s, %s, %s, %s, %s, %s)", (rec.source_id, record_ref, rec.sha256, rec.storage_level,
                                             _js(rec.availability_policy), _js(list(rec.allowed_hosts)),
                                             _js(list(rec.fetch_on_behalf_hosts)), rec.onward_disclosure)),
        owner=True) or rec


def start_ingestion(owner_conn, source_id, effective_at, reason):
    _write(owner_conn, lambda cur: cur.execute(
        "insert into ns_source_events (source_id, kind, effective_at, reason) values (%s, 'ingestion_started', %s, %s)",
        (source_id, effective_at, reason)), owner=True)


def add_alias(owner_conn, text, issuer_id, kind, available_at, source_reference):
    def work(cur):
        cur.execute("insert into ns_entity_aliases (alias_text, issuer_id, kind, available_at, source_reference) "
                    "values (%s, %s, %s, %s, %s) returning alias_id", (text, issuer_id, kind, available_at,
                                                                       source_reference))
        return cur.fetchone()[0]
    if kind == "ticker" and not al.TICKER.fullmatch(text.strip().upper()):
        raise Refused("ticker_form", "a ticker alias is the full form (ABC.N0000)")
    return _write(owner_conn, work, owner=True)


def end_alias(owner_conn, alias_id, available_at, source_reference):
    _write(owner_conn, lambda cur: cur.execute(
        "insert into ns_alias_ends (ends_alias_id, available_at, source_reference) values (%s, %s, %s)",
        (alias_id, available_at, source_reference)), owner=True)


def declare_archive(owner_conn, source_id, terms_reference, published_at_kind, content_state, completeness,
                    date_from, date_to, batch_sha256):
    """§9: an archive batch is declared by the owner before any of its articles is admitted; the batch is a local
    file the owner supplies, never fetched."""
    def work(cur):
        cur.execute("insert into ns_archive_imports (source_id, terms_reference, published_at_kind, content_state, "
                    "completeness, date_from, date_to, batch_sha256) values (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "returning import_id", (source_id, terms_reference, published_at_kind, content_state,
                                            completeness, date_from, date_to, batch_sha256))
        return cur.fetchone()[0]
    return _write(owner_conn, work, owner=True)


def designate(owner_conn, purpose, configuration_id, note):
    _write(owner_conn, lambda cur: cur.execute(
        "insert into ns_designations (purpose, configuration_id, note) values (%s, %s, %s)",
        (purpose, configuration_id, note)), owner=True)


def clear_stop(owner_conn, source_id, reason):
    """The owner clears a stopped source (G-1's rule: only the owner resumes)."""
    _write(owner_conn, lambda cur: cur.execute(
        "insert into ns_source_events (source_id, kind, effective_at, reason) values (%s, 'stop_cleared', now(), %s)",
        (source_id, reason)), owner=True)


# ----------------------------------------------------------------------------------------------- the ledger


class DbLedger:
    """The governed transport's ledger in the database: attempts recorded (and committed) BEFORE they are sent; a stop
    holds for every process until the owner clears it; one request at a time per source across processes (a
    session-level advisory lock, taken by `lock`)."""

    def __init__(self, conn):
        self.conn = conn

    def lock(self, source_id):
        """Take the source's advisory lock for this session (False if another session holds it)."""
        return _write(self.conn, lambda cur: (cur.execute("select pg_try_advisory_lock(hashtext(%s))",
                                                          (f"ns_source:{source_id}",)), cur.fetchone()[0])[1])

    def open_attempt(self, source_id, purpose, url, host, at, state, redirect_of=None, retry_of=None):
        def work(cur):
            cur.execute("insert into ns_fetch_attempts (source_id, purpose, url, host, state, redirect_of, retry_of) "
                        "values (%s, %s, %s, %s, %s, %s, %s) returning attempt_id",
                        (source_id, purpose, url, host, state, redirect_of, retry_of))
            return cur.fetchone()[0]
        return _write(self.conn, work)

    def event(self, attempt_id, kind, at, **detail):
        _write(self.conn, lambda cur: cur.execute(
            "insert into ns_fetch_events (attempt_id, kind, detail_json) values (%s, %s, %s)",
            (attempt_id, kind, _js({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in detail.items()}))))

    def sent_on(self, source_id, day):
        start = datetime.combine(day, datetime.min.time(), COLOMBO)
        return _read(self.conn, lambda cur: (cur.execute(
            "select count(*) from ns_fetch_attempts where source_id = %s and state = 'sent' and recorded_at >= %s "
            "and recorded_at < %s", (source_id, start, start + timedelta(days=1))), cur.fetchone()[0])[1])

    def last_end(self, source_id, now=None):
        def work(cur):
            cur.execute("select exists (select 1 from ns_fetch_attempts a where a.source_id = %s and a.state = 'sent' "
                        "and not exists (select 1 from ns_fetch_events e where e.attempt_id = a.attempt_id and "
                        "e.kind in ('completed', 'failed')))", (source_id,))
            if cur.fetchone()[0] and now is not None:
                return now
            cur.execute("select max(e.recorded_at) from ns_fetch_events e join ns_fetch_attempts a on a.attempt_id = "
                        "e.attempt_id where a.source_id = %s and e.kind in ('completed', 'failed')", (source_id,))
            return cur.fetchone()[0]
        return _read(self.conn, work)

    def record_stop(self, source_id, reason, at):
        _write(self.conn, lambda cur: cur.execute(
            "insert into ns_source_events (source_id, kind, effective_at, reason) values (%s, 'stopped', now(), %s)",
            (source_id, reason if len(reason) >= 3 else f"stopped: {reason}")))

    def stopped(self, source_id):
        row = _read(self.conn, lambda cur: (cur.execute(
            "select kind, reason from ns_source_events where source_id = %s and kind in ('stopped', 'stop_cleared') "
            "order by event_id desc limit 1", (source_id,)), cur.fetchone())[1])
        return row[1] if row and row[0] == "stopped" else None


# ----------------------------------------------------------------------------------------------- worker writes


def record_run(conn, source_id, day, status, detail=None):
    _write(conn, lambda cur: cur.execute(
        "insert into ns_ingestion_runs (source_id, day, status, detail_json) values (%s, %s, %s, %s)",
        (source_id, day, status, _js(detail or {}))))


def write_version(conn, v, archive_import_id=None):
    def work(cur):
        cur.execute("insert into ns_articles (source_id, reference, version_no, supersedes, title, published_at, "
                    "published_precision, observed_at, available_at, first_available_at, language, body_sha256, "
                    "text_sha256, extracted_text, origin, archive_import_id) values (%s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s) returning article_version_id, recorded_at",
                    (v.source_id, v.reference, v.version_no, v.supersedes, v.title, v.published_at,
                     v.published_precision, v.observed_at, v.available_at, v.first_available_at, v.language,
                     v.body_sha256, v.text_sha256, v.extracted_text, v.origin, archive_import_id))
        return cur.fetchone()
    return _write(conn, work)


def write_observation(conn, o):
    _write(conn, lambda cur: cur.execute(
        "insert into ns_article_observations (source_id, reference, kind, observed_at, available_at, detail) values "
        "(%s, %s, %s, %s, %s, %s)", (o.source_id, o.reference, o.kind, o.observed_at, o.available_at, o.detail)))


def register_configuration(conn, cfg):
    body = _js(cfg.body())
    cid = hashlib.sha256(body.encode()).hexdigest()
    _write(conn, lambda cur: cur.execute(
        "insert into ns_configurations (configuration_id, configuration_json, lexicon_sha256) values (%s, %s, %s) "
        "on conflict (configuration_id) do nothing", (cid, body, cfg.lexicon_sha256)))
    return cid


def result_json(result):
    return _js(asdict(result))


def result_from_json(text):
    d = json.loads(text)
    return pr.ArticleResult(tuple(d["processors"]), d["configuration_id"], d["state"], d["issuers"], d["ambiguous"],
                            d["market"], tuple(d["alias_ids"]), d["alias_set_hash"], tuple(d["context_alias_ids"]))


def write_result(conn, article_version_id, result):
    _write(conn, lambda cur: cur.execute(
        "insert into ns_article_results (article_version_id, configuration_id, result_json, alias_ids_json, "
        "alias_set_hash) values (%s, %s, %s, %s, %s)", (article_version_id, result.configuration_id,
                                                        result_json(result), _js(list(result.context_alias_ids)),
                                                        result.alias_set_hash)))


# ----------------------------------------------------------------------------------------------- reading at T


def configuration_in_force(conn, purpose, query):
    rows = _read(conn, lambda cur: (cur.execute(
        "select configuration_id, recorded_at from ns_designations where purpose = %s", (purpose,)), cur.fetchall())[1])
    return sn.configuration_in_force(rows, query)


def load(conn, query):
    """Every row the pure modules need, each with its own instants; admission is theirs. Archive articles of an
    `as_edited` or `selection` batch are admitted to CURRENT only (NL-8)."""
    def work(cur):
        cur.execute("select source_id, storage_level from ns_sources")
        storage = dict(cur.fetchall())
        cur.execute("select a.source_id, a.reference, a.version_no, a.supersedes, a.title, a.published_at, "
                    "a.published_precision, a.language, a.body_sha256, a.text_sha256, a.extracted_text, a.observed_at, "
                    "a.available_at, a.first_available_at, a.recorded_at, a.origin, a.article_version_id, "
                    "i.content_state, i.completeness from ns_articles a left join ns_archive_imports i on "
                    "i.import_id = a.archive_import_id order by a.article_version_id")
        versions, ids = [], {}
        for r in cur.fetchall():
            if r[15] == "archive" and query.mode != CURRENT and (r[17] != "as_published" or r[18] != "complete"):
                continue
            v = ar.Version(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13],
                           recorded_at=r[14], origin=r[15])
            versions.append(v)
            ids[(v.source_id, v.reference, v.version_no)] = r[16]
        cur.execute("select source_id, reference, kind, observed_at, available_at, detail, recorded_at from "
                    "ns_article_observations")
        obs = [ar.Observation(*r) for r in cur.fetchall()]
        cur.execute("select alias_id, alias_text, issuer_id, kind, recorded_at, available_at, source_reference from "
                    "ns_entity_aliases")
        aliases = [al.Alias(*r) for r in cur.fetchall()]
        cur.execute("select ends_alias_id, recorded_at, available_at, source_reference from ns_alias_ends")
        ends = [al.AliasEnd(*r) for r in cur.fetchall()]
        cur.execute("select a.source_id, a.reference, a.version_no, r.configuration_id, r.result_json, r.recorded_at "
                    "from ns_article_results r join ns_articles a on a.article_version_id = r.article_version_id")
        results = [rs.StoredResult(s, ref, vn, cid, result_from_json(j), rec) for s, ref, vn, cid, j, rec in
                   cur.fetchall()]
        cur.execute("select source_id, effective_at, recorded_at from ns_source_events where kind = 'ingestion_started' "
                    "order by event_id")
        sources = [sn.Source(*r) for r in cur.fetchall()]
        cur.execute("select source_id, day, status, recorded_at from ns_ingestion_runs order by run_id")
        runs = [sn.IngestionRun(*r) for r in cur.fetchall()]
        return dict(storage=storage, versions=versions, version_ids=ids, observations=obs, aliases=aliases, ends=ends,
                    results=results, sources=sources, runs=runs)
    return _read(conn, work)


def articles_at(rows, query, cfg):
    """[SnapArticle] admitted at the query: the versions in force, each with its admissible result (§5.5)."""
    out = []
    for (sid, ref), v in sorted(ar.in_force(rows["versions"], rows["observations"], query).items()):
        result, _how = rs.admitted_result(v, rows["results"], rows["aliases"], rows["ends"], query, cfg)
        out.append(sn.SnapArticle(f"{sid}:{ref}", sid, v.version_no, v.title, v.first_available_at, v.origin,
                                  v.language, result))
    return out


def build_snapshot(conn, query, sessions, issuers, cfg, configuration_id=None, purpose="sentiment"):
    """Build and write the snapshot at the query. The configuration is the one named, or the one designated for
    `purpose` at the query's knowledge instant (NL-6); `cfg` must be that configuration. `sessions` is MF's session
    sequence at T (the caller's; NL-7 refuses a later session). Returns (snapshot_id, Snapshot)."""
    cid = configuration_id or configuration_in_force(conn, purpose, query)
    if cid is None:
        raise Refused("no_configuration", f"no NS configuration designated for {purpose!r} at the query")
    if cid != cfg.configuration_id():
        raise Refused("configuration", "the configuration object is not the one named or designated")
    rows = load(conn, query)
    snap = sn.build(query, sessions, articles_at(rows, query, cfg), rows["sources"], rows["runs"], cfg, issuers)
    h = snapshot_hash(snap, sessions)

    def work(cur):
        cur.execute("insert into ns_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, sessions_json, "
                    "configuration_id, active_sources_json, processor_versions_json, imported_inputs, counts_json, "
                    "snapshot_hash) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning snapshot_id",
                    (query.mode, LABELS[query.mode], query.cutoff, query.horizon, snap.d_star,
                     _js([d.isoformat() for d in sessions]), cid,
                     _js(snap.active_sources), _js(snap.processor_versions), snap.imported_inputs, _js(snap.counts), h))
        sid = cur.fetchone()[0]
        for r in snap.rows:
            cur.execute("insert into ns_snapshot_rows (snapshot_id, subject, state, features_json, stories_json, "
                        "row_hash) values (%s, %s, %s, %s, %s, %s)",
                        (sid, r.subject, r.state, canonical_json({k: list(v) for k, v in r.features.items()}),
                         canonical_json(r.stories), r.row_hash))
        return sid
    return _write(conn, work), snap


def snapshot_hash(snap, sessions):
    """Binds the rows, counts, imported_inputs, processor versions, active sources and the session sequence used."""
    return hashlib.sha256(canonical_json({"rows": sorted([r.subject, r.row_hash] for r in snap.rows),
                                          "counts": snap.counts, "imported_inputs": snap.imported_inputs,
                                          "processor_versions": snap.processor_versions,
                                          "active_sources": snap.active_sources,
                                          "sessions": [d.isoformat() for d in sessions]}).encode()).hexdigest()


def require_point_in_time(conn, snapshot_id):
    """NL-8: a point-in-time consumer never receives a CURRENT snapshot."""
    mode = _read(conn, lambda cur: (cur.execute("select mode from ns_snapshots where snapshot_id = %s",
                                                (snapshot_id,)), cur.fetchone())[1])
    if mode is None:
        raise Refused("no_snapshot", f"no NS snapshot {snapshot_id}")
    if mode[0] not in (KNOWN, AVAILABLE):
        raise Refused("not_point_in_time", "a CURRENT snapshot is never point-in-time evidence (NL-8)")
    return snapshot_id


def config_from_json(text):
    """The Config a stored configuration describes (its id must reproduce)."""
    from dataclasses import fields
    d = json.loads(text)
    kw = {f.name: d[f.name] for f in fields(cf.Config) if f.name in d}
    kw["half_lives"] = tuple(tuple(x) for x in kw["half_lives"])
    return cf.Config(**kw)
