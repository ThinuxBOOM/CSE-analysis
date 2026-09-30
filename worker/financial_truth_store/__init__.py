"""
Stage F6.4: durable persistence of the financial-truth domain (docs/F6.4_DESIGN.md, frozen).

The frozen, pure F6.3 layer (worker/financial_truth) computes; this package only loads its inputs from PostgreSQL,
serialises its outputs, stores them append-only (migration 0015) and chooses which already-computed objects to
reconcile. It never re-implements an F6.3 rule and adds nothing to worker/financial_truth.

    loader      database rows -> F6.3 inputs (f6.store.1 rendering rules, section 17.3)
    codec       envelopes E1-E6, decoders for E3 / E4 / E5, the section 11.6 decomposition and its Python check
    writer      idempotent, atomic inserts
    selection   the current input set (M4), partitions (9.3) and the partition fingerprint (11.4)
    jobs        validate / reconcile / cleanup, the job ledger, the advisory lock and the pinned Decimal context
    preflight   the worker role's security preflight (15.6)
    verify      re-hashes, re-decodes and re-proves the stored rows; reproduces sampled validation runs
    cli         python -m worker.financial_truth_store <command>

No PDF, document text, network, CSE, Gemini, ML, scheduler or availability policy is involved.
"""
import decimal

STORE_VERSION = "f6.store.1"

# The migration runner holds ...311 and P2's capture lock ...312 (P3 shares it); F6.4 uses ...313.
F6_LOCK_KEY = 4_346_836_117_002_313

# Python's documented default context, built fresh (section 17.4). Every F6.4 call into F6.3 runs inside
# decimal.localcontext(F6_DECIMAL_CONTEXT), so F6.1's compare_values (whose abs() uses the ambient context) behaves
# exactly as validated whatever context the calling process set. F6.1 itself is not changed.
F6_DECIMAL_CONTEXT = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                                     capitals=1, clamp=0,
                                     traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow])

# The side-effect-free helper functions of migration 0015 that the worker's guards call (section 11.6.3); the
# worker holds EXECUTE on exactly these and on no trigger function (preflight checks both).
HELPER_FUNCTIONS = (
    "f6_field_eq(jsonb, text[], jsonb)", "f6_mismatch(jsonb, jsonb)", "f6_num_json(numeric)", "f6_date_json(date)",
    "f6_nil_forms_json(text[])", "f6_source_key_json(uuid, smallint, smallint, smallint, smallint, text)",
    "f6_sha256_hex(text)", "f6_identity_json(financial_economic_facts)",
    "f6_candidate_reported_json(financial_fact_candidates)",
    "f6_reported_json(boolean, text, numeric, text, smallint, text, bigint, text, text, text)", "f6_fail(text, text)",
    "f6_comparison_check(jsonb, text, text, boolean, numeric, numeric, numeric, numeric, numeric, numeric, jsonb, jsonb)",
)

EXIT_OK, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 4, 5
