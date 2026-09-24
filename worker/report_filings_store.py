"""
Postgres persistence for Stage F1 filing discovery (migration 0004).

Same semantics as report_discovery.InMemoryFilingStore — both call the pure
report_discovery.classify_observation() / build_filing_row(). Imported only for real database runs, so
dry/memory runs never load psycopg2 or need DATABASE_URL.

Transaction model: begin_run() commits the run row immediately (a crashed run
stays visible as 'running'). Each filing is applied inside its own SAVEPOINT
with the filing row locked; a failure rolls back to that savepoint only, so
it cannot poison the rest of the batch (the Stage E lesson). finish_run()
writes the run summary and commits the batch.
"""
import json

import psycopg2.extras

from . import report_discovery

FILING_COLUMNS = ("cse_filing_id",) + report_discovery.NORMALIZED_COLUMNS + report_discovery.BOOKKEEPING_COLUMNS
JSON_COLUMNS = ("field_sources", "current_versions")


class PostgresFilingStore:
    def __init__(self, conn):
        self.conn = conn

    def begin_run(self, source_endpoint, request_params, now):
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into report_discovery_runs (source_endpoint, request_params, started_at) "
                "values (%s, %s, %s) returning id",
                (source_endpoint, psycopg2.extras.Json(request_params), now))
            run_id = str(cur.fetchone()[0])
        self.conn.commit()
        return run_id

    def finish_run(self, run_id, summary, now):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                update report_discovery_runs set
                    status = %(status)s, failure_category = %(failure_category)s, http_status = %(http_status)s,
                    rows_returned = %(rows_returned)s, filings_new = %(filings_new)s,
                    observations_new = %(observations_new)s, metadata_changes = %(metadata_changes)s,
                    rows_rejected = %(rows_rejected)s, item_failures = %(item_failures)s,
                    details = %(details)s, finished_at = %(finished_at)s
                where id = %(id)s
                """,
                {**summary, "details": psycopg2.extras.Json(summary["details"], dumps=_dumps),
                 "finished_at": now, "id": run_id})

    def commit(self):
        self.conn.commit()

    def lookup_company_id(self, ticker):
        with self.conn.cursor() as cur:
            cur.execute("select id from companies where ticker = %s", (ticker,))
            row = cur.fetchone()
            return str(row[0]) if row else None

    def apply_observation(self, obs, run_id, now):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("savepoint f1_obs")
            try:
                outcome, diffs = self._apply(cur, obs, run_id, now)
            except Exception:
                cur.execute("rollback to savepoint f1_obs")
                cur.execute("release savepoint f1_obs")
                raise
            cur.execute("release savepoint f1_obs")
            return outcome, diffs

    def _apply(self, cur, obs, run_id, now):
        cur.execute("select * from report_filings where cse_filing_id = %s for update", (obs.cse_filing_id,))
        existing = cur.fetchone()
        if existing is not None:
            existing = {k: (str(v) if k in ("company_id", "first_discovery_run_id", "last_discovery_run_id")
                            and v is not None else v)
                        for k, v in existing.items() if k in FILING_COLUMNS}
        rd = report_discovery
        outcome = rd.classify_observation(existing, obs)
        key = rd.source_key(obs.source_endpoint, obs.source_bucket, obs.query_symbol)
        current_versions = dict((existing or {}).get("current_versions") or {})
        current_versions[key] = obs.metadata_hash
        current_items = {key: obs.raw_item}
        for k, h in current_versions.items():
            if k == key:
                continue
            endpoint, bucket, _ = rd.split_source_key(k)
            cur.execute(
                "select raw_item from report_filing_observations where cse_filing_id = %s "
                "and source_endpoint = %s and source_bucket = %s and metadata_hash = %s",
                (obs.cse_filing_id, endpoint, bucket, h))
            found = cur.fetchone()
            if found is None:
                raise RuntimeError(f"current version {k}={h} of filing {obs.cse_filing_id} has no observation row")
            current_items[k] = found["raw_item"]
        symbols = {rd.split_source_key(k)[2] for k in current_versions} - {None}
        companies = {sym: self.lookup_company_id(sym) for sym in symbols}

        row, diffs = rd.build_filing_row(existing, obs, outcome, current_versions, current_items,
                                         companies, run_id, now)
        params = {**row, **{c: psycopg2.extras.Json(row[c]) for c in JSON_COLUMNS}}

        if existing is None:
            cols = ", ".join(FILING_COLUMNS)
            vals = ", ".join(f"%({c})s" for c in FILING_COLUMNS)
            cur.execute(f"insert into report_filings ({cols}) values ({vals}) "
                        f"on conflict (cse_filing_id) do nothing returning id", params)
            if cur.fetchone() is None:
                # Another writer inserted it between our SELECT and INSERT; fail
                # this item loudly rather than overwrite — a re-run will merge it.
                raise RuntimeError(f"concurrent insert of filing {obs.cse_filing_id}; retry the window")
        else:
            sets = ", ".join(f"{c} = %({c})s" for c in FILING_COLUMNS if c != "cse_filing_id")
            cur.execute(f"update report_filings set {sets}, updated_at = now() "
                        f"where cse_filing_id = %(cse_filing_id)s", params)

        if outcome != "unchanged":
            cur.execute(
                """
                insert into report_filing_observations
                    (cse_filing_id, discovery_run_id, source_endpoint, source_bucket, query_symbol,
                     metadata_hash, raw_item, observed_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (cse_filing_id, source_endpoint, source_bucket, metadata_hash) do nothing
                """,
                (obs.cse_filing_id, run_id, obs.source_endpoint, obs.source_bucket, obs.query_symbol,
                 obs.metadata_hash, psycopg2.extras.Json(obs.raw_item, dumps=_dumps), now))
        return outcome, diffs


def _dumps(value):
    return json.dumps(value, default=str)
