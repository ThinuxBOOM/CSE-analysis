"""
Database access layer. Connects via DATABASE_URL (the restricted cse_worker
role's own connection string — never the Supabase service_role key).

Isolated from cse_client.py and mapping.py per the Stage B constraint: this
module only knows about rows and SQL, not about CSE response shapes.
"""
import json
from typing import Optional

import psycopg2
import psycopg2.extras

from . import config


def get_connection():
    return psycopg2.connect(config.get_database_url())


def insert_raw_observation(conn, *, request_attempt_id, ingestion_job_id, company_id,
                            observation_date, capture_window, source, observed_at,
                            fields: dict, raw_payload: dict) -> Optional[str]:
    """
    Inserts one raw_market_observations row. Idempotent via the
    (request_attempt_id, company_id, capture_window) unique constraint —
    a retry of the SAME attempt conflicts harmlessly; a NEW attempt always
    inserts. Returns the new row's id, or None if it was a harmless conflict
    (already existed under this exact attempt).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into raw_market_observations
                (request_attempt_id, ingestion_job_id, company_id, observation_date,
                 capture_window, source, observed_at, post_open_price, open_price, last_traded_price,
                 last_traded_date, closing_price, high, low, turnover, share_volume,
                 trade_count, foreign_holding, raw_payload)
            values
                (%(request_attempt_id)s, %(ingestion_job_id)s, %(company_id)s, %(observation_date)s,
                 %(capture_window)s, %(source)s, %(observed_at)s, %(post_open_price)s, %(open_price)s, %(last_traded_price)s,
                 %(last_traded_date)s, %(closing_price)s, %(high)s, %(low)s, %(turnover)s, %(share_volume)s,
                 %(trade_count)s, %(foreign_holding)s, %(raw_payload)s)
            on conflict (request_attempt_id, company_id, capture_window) do nothing
            returning id;
            """,
            {
                "request_attempt_id": request_attempt_id,
                "ingestion_job_id": ingestion_job_id,
                "company_id": company_id,
                "observation_date": observation_date,
                "capture_window": capture_window,
                "source": source,
                "observed_at": observed_at,
                "post_open_price": fields.get("post_open_price"),
                "open_price": fields.get("open_price"),
                "last_traded_price": fields.get("last_traded_price"),
                "last_traded_date": fields.get("last_traded_date"),
                "closing_price": fields.get("closing_price"),
                "high": fields.get("high"),
                "low": fields.get("low"),
                "turnover": fields.get("turnover"),
                "share_volume": fields.get("share_volume"),
                "trade_count": fields.get("trade_count"),
                "foreign_holding": fields.get("foreign_holding"),
                "raw_payload": json.dumps(raw_payload),
            },
        )
        row = cur.fetchone()
        conn.commit()
        return str(row[0]) if row else None


def get_raw_observations_for_date(conn, *, company_id, observation_date) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            select id, request_attempt_id, capture_window, source, observed_at,
                   post_open_price, open_price, last_traded_price, last_traded_date, closing_price,
                   high, low, turnover, share_volume, trade_count, foreign_holding
            from raw_market_observations
            where company_id = %(company_id)s and observation_date = %(observation_date)s
            order by observed_at;
            """,
            {"company_id": company_id, "observation_date": observation_date},
        )
        return [dict(r) for r in cur.fetchall()]


def get_previous_close(conn, *, company_id, before_date) -> Optional[float]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select closing_price from daily_market_data
            where company_id = %(company_id)s and trade_date < %(before_date)s
              and closing_price is not null
            order by trade_date desc limit 1;
            """,
            {"company_id": company_id, "before_date": before_date},
        )
        row = cur.fetchone()
        return float(row[0]) if row else None


def upsert_daily_market_data(conn, *, company_id, trade_date, canonical: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into daily_market_data
                (company_id, trade_date, post_open_price, post_open_captured_at, open_price, high, low,
                 closing_price, last_traded_price, last_traded_date, turnover, share_volume,
                 trade_count, foreign_holding, field_provenance, contributing_observation_ids,
                 primary_source, reconciliation_status, discrepancy_notes, validation_status,
                 validation_notes)
            values
                (%(company_id)s, %(trade_date)s, %(post_open_price)s, %(post_open_captured_at)s, %(open_price)s,
                 %(high)s, %(low)s, %(closing_price)s, %(last_traded_price)s, %(last_traded_date)s,
                 %(turnover)s, %(share_volume)s, %(trade_count)s, %(foreign_holding)s,
                 %(field_provenance)s, %(contributing_observation_ids)s::uuid[], %(primary_source)s,
                 %(reconciliation_status)s, %(discrepancy_notes)s, %(validation_status)s,
                 %(validation_notes)s)
            on conflict (company_id, trade_date) do update set
                post_open_price = excluded.post_open_price,
                post_open_captured_at = excluded.post_open_captured_at,
                open_price = excluded.open_price,
                high = excluded.high, low = excluded.low,
                closing_price = excluded.closing_price,
                last_traded_price = excluded.last_traded_price,
                last_traded_date = excluded.last_traded_date,
                turnover = excluded.turnover, share_volume = excluded.share_volume,
                trade_count = excluded.trade_count, foreign_holding = excluded.foreign_holding,
                field_provenance = excluded.field_provenance,
                contributing_observation_ids = excluded.contributing_observation_ids,
                primary_source = excluded.primary_source,
                reconciliation_status = excluded.reconciliation_status,
                discrepancy_notes = excluded.discrepancy_notes,
                validation_status = excluded.validation_status,
                validation_notes = excluded.validation_notes,
                derived_at = now();
            """,
            {
                "company_id": company_id,
                "trade_date": trade_date,
                "post_open_price": canonical.get("post_open_price"),
                "post_open_captured_at": canonical.get("post_open_captured_at"),
                "open_price": canonical.get("open_price"),
                "high": canonical.get("high"),
                "low": canonical.get("low"),
                "closing_price": canonical.get("closing_price"),
                "last_traded_price": canonical.get("last_traded_price"),
                "last_traded_date": canonical.get("last_traded_date"),
                "turnover": canonical.get("turnover"),
                "share_volume": canonical.get("share_volume"),
                "trade_count": canonical.get("trade_count"),
                "foreign_holding": canonical.get("foreign_holding"),
                "field_provenance": json.dumps(canonical.get("field_provenance", {})),
                "contributing_observation_ids": canonical.get("contributing_observation_ids", []),
                "primary_source": canonical.get("primary_source"),
                "reconciliation_status": canonical.get("reconciliation_status", "pending"),
                "discrepancy_notes": json.dumps(canonical.get("discrepancy_notes")) if canonical.get("discrepancy_notes") else None,
                "validation_status": canonical.get("validation_status", "ok"),
                "validation_notes": json.dumps(canonical.get("validation_notes")) if canonical.get("validation_notes") else None,
            },
        )
        conn.commit()
