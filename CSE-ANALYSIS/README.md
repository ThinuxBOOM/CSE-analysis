# Stage B — Single-Company Vertical Slice

## Setup
    pip install -r requirements.txt
    export DATABASE_URL="postgresql://cse_worker:<password>@<supabase-host>:5432/postgres"

(Use the restricted `cse_worker` role from the migration's grant block —
never the Supabase service_role key.)

## Run against the REAL CSE API
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close

This will:
1. Call the real companyInfoSummery and tradeSummary endpoints
2. Print the exact raw response bodies
3. Print mapping notes (found / expected-but-missing / unexpected fields)
4. Insert a raw_market_observations row
5. Run reconciliation + validation
6. Upsert the canonical daily_market_data row
7. Print the canonical row before writing it

## Run the unit tests (no network/DB needed)
    python tests/test_mapping.py
    python tests/test_reconciliation.py

## Resuming a crashed run
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close \
        --resume-attempt-id <the uuid printed by the crashed run>

## IMPORTANT
If the real CSE response doesn't match the field names in worker/mapping.py's
COMPANY_INFO_FIELD_CANDIDATES / TRADE_SUMMARY_FIELD_CANDIDATES, the script
will print exactly what was expected-but-missing and what unexpected fields
showed up. Do not edit the mapping to "make it work" without flagging the
discrepancy first — that's a deliberate design decision, not an oversight.
