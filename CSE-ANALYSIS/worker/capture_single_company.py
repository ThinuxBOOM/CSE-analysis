"""
Stage B orchestrator: single-company, single-capture-window vertical slice.

Two modes:

  --dry-run   Calls the REAL CSE endpoints, prints everything (raw responses,
              mapping notes, proposed raw observation, proposed canonical/
              reconciliation result) but makes ZERO database connections and
              ZERO writes. No DATABASE_URL needed at all in this mode — this
              is deliberate, not just "connect but don't write": the safest
              possible first real-CSE run touches nothing but cse.lk.

  (default)   Full path: real CSE calls + real DB writes, as tested in the
              original Stage B pass. Requires DATABASE_URL (the restricted
              cse_worker role's connection string).

Usage:
    # Safe first real-API run — no database involved at all:
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close --dry-run

    # Full path, once you're ready to actually write:
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close

Deliberately does NOT loop over the full ~290-company universe, does NOT
schedule itself, does NOT run bulletin recovery or indicator calculations.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

from . import cse_client, mapping, reconciliation, validation

# Same values as system_config's seeded defaults (Phase 1 migration). The
# live-DB path could read these from system_config instead; hardcoded here
# so --dry-run has zero DB dependency, including for config.
DEFAULT_TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}


def resolve_request_attempt_id(args) -> str:
    if args.resume_attempt_id:
        print(f"Resuming existing request_attempt_id: {args.resume_attempt_id}")
        return args.resume_attempt_id
    new_id = str(uuid.uuid4())
    print(f"New request_attempt_id generated: {new_id}")
    return new_id


def fetch_and_map(symbol: str, window: str):
    """Shared by both dry-run and live paths — the actual CSE calls + mapping,
    with zero DB involvement either way."""
    print(f"\nCalling companyInfoSummery for {symbol}...")
    ci_response = cse_client.get_company_info_summary(symbol)
    print(f"  status={ci_response.status_code} ok={ci_response.ok} error={ci_response.error}")
    print(f"  RAW BODY:\n{json.dumps(ci_response.body, indent=2)[:3000]}")

    print(f"\nCalling tradeSummary (all securities)...")
    ts_response = cse_client.get_trade_summary_all()
    print(f"  status={ts_response.status_code} ok={ts_response.ok} error={ts_response.error}")
    ts_row = cse_client.extract_symbol_row_from_trade_summary(ts_response, symbol)
    print(f"  Extracted row for {symbol}: {json.dumps(ts_row, indent=2) if ts_row else 'NOT FOUND'}")

    if not ci_response.ok and not ts_response.ok:
        print("\nBoth CSE calls failed — stopping. Not fabricating an observation.", file=sys.stderr)
        sys.exit(1)

    ci_mapped = mapping.map_company_info_summary(ci_response.body)
    ts_mapped = mapping.map_trade_summary_row(ts_row)

    print(f"\ncompanyInfoSummery mapping notes:\n{json.dumps(ci_mapped.notes, indent=2, default=str)}")
    print(f"\ntradeSummary mapping notes:\n{json.dumps(ts_mapped.notes, indent=2, default=str)}")

    if ci_mapped.notes.get("unexpected_fields"):
        print(f"\n*** UNEXPECTED FIELDS in companyInfoSummery (present in raw_payload, "
              f"not mapped to any column): {json.dumps(ci_mapped.notes['unexpected_fields'], default=str)}")
    if ts_mapped.notes.get("unexpected_fields"):
        print(f"\n*** UNEXPECTED FIELDS in tradeSummary row (present in raw_payload, "
              f"not mapped to any column): {json.dumps(ts_mapped.notes['unexpected_fields'], default=str)}")
    if ci_mapped.notes.get("multiple_candidates_present"):
        print(f"\n*** MULTIPLE CANDIDATE FIELD NAMES present simultaneously in companyInfoSummery "
              f"— not silently resolved: {json.dumps(ci_mapped.notes['multiple_candidates_present'], default=str)}")
    if ts_mapped.notes.get("multiple_candidates_present"):
        print(f"\n*** MULTIPLE CANDIDATE FIELD NAMES present simultaneously in tradeSummary "
              f"— not silently resolved: {json.dumps(ts_mapped.notes['multiple_candidates_present'], default=str)}")
    if ci_mapped.notes.get("unwrapped_into_key"):
        print(f"\nNote: companyInfoSummery response was unwrapped into key "
              f"'{ci_mapped.notes['unwrapped_into_key']}'.")
    if ci_mapped.notes.get("unwrap_ambiguous_candidates"):
        print(f"\n*** UNWRAP WAS AMBIGUOUS — multiple nested dict keys existed: "
              f"{ci_mapped.notes['unwrap_ambiguous_candidates']}. Picked the first one "
              f"('{ci_mapped.notes['unwrapped_into_key']}'). Verify this is correct.")

    raw_fields = mapping.build_raw_observation(
        company_info_result=ci_mapped, trade_summary_result=ts_mapped, capture_window=window,
    )
    cross_source_comparison = raw_fields.get("cross_source_comparison")
    if cross_source_comparison:
        disagreements = {k: v for k, v in cross_source_comparison.items() if not v["agree_exactly"]}
        print(f"\nCross-source comparison (fields present in BOTH endpoints):\n"
              f"{json.dumps(cross_source_comparison, indent=2, default=str)}")
        if disagreements:
            print(f"\n*** CROSS-SOURCE DISAGREEMENT on: {list(disagreements.keys())} — "
                  f"both values preserved in raw_payload, canonical value NOT silently chosen "
                  f"without a record.")

    raw_payload = {
        "companyInfoSummery": {
            "status_code": ci_response.status_code, "body": ci_response.body, "error": ci_response.error,
        },
        "tradeSummary_matched_row": ts_row,
        "tradeSummary_call_status_code": ts_response.status_code,
        "mapping_notes": {"companyInfoSummery": ci_mapped.notes, "tradeSummary": ts_mapped.notes},
        "cross_source_comparison": cross_source_comparison,  # explicit, top-level, present whenever
                                                                  # a field exists in both endpoints —
                                                                  # never requires diffing two blobs by hand
    }

    return raw_fields, raw_payload


def run_dry_run(args):
    print("=" * 70)
    print("DRY RUN — real CSE API calls, ZERO database connections or writes")
    print("=" * 70)

    request_attempt_id = resolve_request_attempt_id(args)
    observation_date = args.observation_date or datetime.now(timezone.utc).date().isoformat()
    observed_at = datetime.now(timezone.utc)

    raw_fields, raw_payload = fetch_and_map(args.symbol, args.window)

    proposed_raw_row = {
        "request_attempt_id": request_attempt_id,
        "company_id": "<NOT RESOLVED — dry-run makes no DB connection>",
        "observation_date": observation_date,
        "capture_window": args.window,
        "source": "CSE_API",
        "observed_at": observed_at.isoformat(),
        **{k: v for k, v in raw_fields.items() if k != "cross_source_comparison"},
    }
    print(f"\n{'=' * 70}\nPROPOSED raw_market_observations ROW (NOT inserted):\n{'=' * 70}")
    print(json.dumps(proposed_raw_row, indent=2, default=str))

    # Simulate reconciliation using ONLY this one observation (dry-run has no
    # DB access to fetch any other same-day observations that might already
    # exist) — this previews what the canonical row would look like if this
    # were the only observation for the day, not a live merge with real data.
    synthetic_obs = {
        "id": "DRY-RUN-NOT-A-REAL-ID",
        "capture_window": args.window,
        "source": "CSE_API",
        "observed_at": observed_at.isoformat(),
        **{k: v for k, v in raw_fields.items() if k != "cross_source_comparison"},
    }
    canonical = reconciliation.reconcile([synthetic_obs], DEFAULT_TOLERANCES)
    validation_status, validation_notes = validation.validate(canonical, previous_close=None, tolerances=DEFAULT_TOLERANCES)
    canonical["validation_status"] = validation_status
    canonical["validation_notes"] = validation_notes

    print(f"\n{'=' * 70}\nPROPOSED canonical/reconciliation RESULT (NOT written; "
          f"single-observation preview, previous_close unavailable in dry-run):\n{'=' * 70}")
    print(json.dumps(canonical, indent=2, default=str))

    print(f"\n{'=' * 70}\nDRY RUN COMPLETE — no database was contacted, nothing was written.")
    print(f"{'=' * 70}")


def run_live(args):
    from . import db  # imported only here — dry-run must not require psycopg2/DATABASE_URL at all

    request_attempt_id = resolve_request_attempt_id(args)
    observation_date = args.observation_date or datetime.now(timezone.utc).date().isoformat()
    observed_at = datetime.now(timezone.utc)

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("select id from companies where ticker = %s", (args.symbol,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(
                f"No company row found for ticker '{args.symbol}'. This script does not invent "
                f"security-master data — insert a companies row for this ticker first."
            )
        company_id = str(row[0])

    raw_fields, raw_payload = fetch_and_map(args.symbol, args.window)

    print(f"\nInserting raw_market_observations row "
          f"(attempt={request_attempt_id}, window={args.window})...")
    new_id = db.insert_raw_observation(
        conn,
        request_attempt_id=request_attempt_id,
        ingestion_job_id=None,
        company_id=company_id,
        observation_date=observation_date,
        capture_window=args.window,
        source="CSE_API",
        observed_at=observed_at,
        fields=raw_fields,
        raw_payload=raw_payload,
    )
    print(f"  {'Inserted new row: ' + new_id if new_id else 'No new row inserted — idempotent retry.'}")

    print(f"\nRunning reconciliation for {args.symbol} on {observation_date}...")
    raw_obs = db.get_raw_observations_for_date(conn, company_id=company_id, observation_date=observation_date)
    print(f"  Found {len(raw_obs)} raw observation(s) for this date.")

    canonical = reconciliation.reconcile(raw_obs, DEFAULT_TOLERANCES)
    previous_close = db.get_previous_close(conn, company_id=company_id, before_date=observation_date)
    validation_status, validation_notes = validation.validate(canonical, previous_close, DEFAULT_TOLERANCES)
    canonical["validation_status"] = validation_status
    canonical["validation_notes"] = validation_notes

    print(f"\nCanonical row to be written:\n{json.dumps(canonical, indent=2, default=str)}")

    db.upsert_daily_market_data(conn, company_id=company_id, trade_date=observation_date, canonical=canonical)
    print(f"\ndaily_market_data upserted for {args.symbol} on {observation_date}.")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--window", required=True, choices=["post_open", "post_close"])
    parser.add_argument("--resume-attempt-id", default=None)
    parser.add_argument("--observation-date", default=None, help="YYYY-MM-DD; defaults to today (UTC date)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Real CSE calls, zero database connection or writes.")
    args = parser.parse_args()

    if args.dry_run:
        run_dry_run(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
