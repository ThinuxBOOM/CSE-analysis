"""
Stage E tests: partial failure isolation and idempotency at multi-company
scale, plus verification of open_price/post_open_price/closing_price/
last_traded_price/volume/turnover/trade_count across MULTIPLE REAL
securities (not synthetic data) — the fixtures in
tests/fixtures/multi_company/ are real captured CSE responses for 5
distinct symbols, extracted from the user's own investigation runs.

Tests requiring a real database (idempotency, new-attempt-vs-dedup) skip
gracefully if DATABASE_URL isn't set, rather than failing — but they were
run and verified against a real local Postgres instance before delivery
(see the conversation's verification log).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import cse_client, capture_multiple_companies as cmc, mapping

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "multi_company")

REAL_SYMBOLS = ["COMB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000"]


def load_real_fixtures():
    """Loads the 5 real per-company fixtures into {symbol: (ci_body, ts_row)}."""
    data = {}
    for symbol in REAL_SYMBOLS:
        safe = symbol.replace(".", "_")
        with open(os.path.join(FIXTURES_DIR, f"real_companyInfoSummery_{safe}.json")) as f:
            ci_body = json.load(f)
        with open(os.path.join(FIXTURES_DIR, f"real_tradeSummary_row_{safe}.json")) as f:
            ts_row = json.load(f)
        data[symbol] = (ci_body, ts_row)
    return data


def install_mock_cse_client(real_data, failing_symbols=None):
    """
    Monkeypatches cse_client so tradeSummary returns ALL symbols' rows in ONE
    response (as it does for real), and companyInfoSummery returns each
    symbol's real captured body — with optional injected failures for
    specific symbols to test isolation.
    """
    failing_symbols = failing_symbols or set()

    def mock_get_company_info_summary(symbol):
        if symbol in failing_symbols:
            return cse_client.CSEResponse(
                endpoint="companyInfoSummery", request_method="POST", request_params={"symbol": symbol},
                status_code=500, ok=False, error="Simulated failure for isolation test",
            )
        ci_body, _ = real_data[symbol]
        return cse_client.CSEResponse(
            endpoint="companyInfoSummery", request_method="POST", request_params={"symbol": symbol},
            status_code=200, ok=True, body=ci_body,
        )

    def mock_get_trade_summary_all():
        # ONE response containing every symbol's real row — proves the batch
        # only needs one such call, and that per-symbol extraction from a
        # shared response works. Symbols in failing_symbols are deliberately
        # EXCLUDED here too, so a "failure" genuinely means "both endpoints
        # have nothing for this symbol" (matching fetch_and_map's actual
        # failure condition), not just one endpoint being unavailable while
        # the other still has real data (which is graceful degradation, not
        # a failure — confirmed by testing this the wrong way first).
        all_rows = [ts_row for sym, (_, ts_row) in real_data.items() if sym not in failing_symbols]
        return cse_client.CSEResponse(
            endpoint="tradeSummary", request_method="POST", request_params={},
            status_code=200, ok=True, body=all_rows,
        )

    cse_client.get_company_info_summary = mock_get_company_info_summary
    cse_client.get_trade_summary_all = mock_get_trade_summary_all


def test_tradeSummary_fetched_exactly_once_per_batch():
    """Requirement 4: one tradeSummary call regardless of symbol count."""
    real_data = load_real_fixtures()
    call_count = {"count": 0}
    original_get_ts = cse_client.get_trade_summary_all

    def counting_get_ts():
        call_count["count"] += 1
        return original_get_ts()

    install_mock_cse_client(real_data)
    cse_client.get_trade_summary_all = counting_get_ts

    report = cmc.capture_multiple_companies(
        conn=None, symbols=REAL_SYMBOLS, window="post_close",
        request_attempt_id="test-attempt-1", observation_date="2026-09-08",
        dry_run=True, verbose=False,
    )

    assert call_count["count"] == 1, f"Expected exactly 1 tradeSummary call, got {call_count['count']}"
    assert len(report["successful_symbols"]) == 5
    print("PASS: tradeSummary fetched exactly once for a 5-symbol batch, all 5 succeeded")


def test_partial_failure_isolation_http():
    """Requirements 6 & 7: one symbol's HTTP failure must not affect others,
    and must be recorded with a specific reason, not silently skipped."""
    real_data = load_real_fixtures()
    install_mock_cse_client(real_data, failing_symbols={"JKH.N0000"})

    report = cmc.capture_multiple_companies(
        conn=None, symbols=REAL_SYMBOLS, window="post_close",
        request_attempt_id="test-attempt-2", observation_date="2026-09-08",
        dry_run=True, verbose=False,
    )

    assert "JKH.N0000" in report["failed_symbols"]
    assert "JKH.N0000" in report["failures_by_category"].get("http_failure", [])
    assert set(report["successful_symbols"]) == set(REAL_SYMBOLS) - {"JKH.N0000"}
    jkh_result = next(r for r in report["per_symbol"] if r["symbol"] == "JKH.N0000")
    assert jkh_result["reason"] is not None and "Simulated failure" in jkh_result["reason"]
    print("PASS: one symbol's HTTP failure isolated — 4/5 others succeeded, "
          "failure recorded with a specific reason, not silently skipped")


def test_multiple_failure_categories_isolated_simultaneously():
    """Two different symbols failing for the same reason (http) still isolate
    correctly from each other and from the successful ones."""
    real_data = load_real_fixtures()
    install_mock_cse_client(real_data, failing_symbols={"JKH.N0000", "SAMP.N0000"})

    report = cmc.capture_multiple_companies(
        conn=None, symbols=REAL_SYMBOLS, window="post_close",
        request_attempt_id="test-attempt-3", observation_date="2026-09-08",
        dry_run=True, verbose=False,
    )

    assert set(report["failed_symbols"]) == {"JKH.N0000", "SAMP.N0000"}
    assert set(report["successful_symbols"]) == {"COMB.N0000", "LOLC.N0000", "HNB.N0000"}
    assert len(report["failures_by_category"]["http_failure"]) == 2
    print("PASS: two simultaneous failures isolated correctly, 3/5 succeeded")


def _expected_values_from_fixture(ci_body: dict, ts_row: dict) -> dict:
    """
    Computes expected canonical values directly from a real fixture's raw
    JSON, mirroring build_raw_observation's actual precedence rules
    (companyInfoSummery preferred for last_traded_price/closing_price,
    falling back to tradeSummary if absent; open_price/high/low/turnover/
    share_volume/trade_count always from tradeSummary). This is ground
    truth derived from the fixture itself, not a heuristic like "must
    differ from other companies" — different securities can legitimately
    share a price.
    """
    symbol_info = ci_body.get("reqSymbolInfo", {})
    return {
        "open_price": ts_row.get("open"),
        "closing_price": symbol_info.get("closingPrice") if symbol_info.get("closingPrice") is not None else ts_row.get("closingPrice"),
        "last_traded_price": symbol_info.get("lastTradedPrice") if symbol_info.get("lastTradedPrice") is not None else ts_row.get("price"),
        "share_volume": ts_row.get("sharevolume"),
        "turnover": ts_row.get("turnover"),
        "trade_count": ts_row.get("tradevolume"),
    }


def test_real_multi_security_field_values():
    """Requirement 11: verify open_price, post_open_price, closing_price,
    last_traded_price, volume, turnover, trade_count for MULTIPLE real
    securities (not one, not synthetic) — checked against each symbol's OWN
    known fixture values, not against a "must differ from other companies"
    heuristic. Different securities can legitimately share a price."""
    real_data = load_real_fixtures()
    install_mock_cse_client(real_data)

    report = cmc.capture_multiple_companies(
        conn=None, symbols=REAL_SYMBOLS, window="post_open",
        request_attempt_id="test-attempt-4", observation_date="2026-09-08",
        dry_run=True, verbose=False,
    )

    assert len(report["successful_symbols"]) == 5
    by_symbol = {r["symbol"]: r for r in report["per_symbol"]}

    for symbol in REAL_SYMBOLS:
        ci_body, ts_row = real_data[symbol]
        expected = _expected_values_from_fixture(ci_body, ts_row)
        actual = by_symbol[symbol]
        for field_name, expected_value in expected.items():
            assert actual[field_name] == expected_value, (
                f"{symbol}.{field_name}: expected {expected_value} (from fixture), "
                f"got {actual[field_name]}"
            )
        # post_open_price: sourced from last_traded_price only when window='post_open'
        assert actual["post_open_price"] == expected["last_traded_price"], (
            f"{symbol}.post_open_price should equal last_traded_price on a post_open capture"
        )

    print("PASS: every one of the 5 real securities' open_price/closing_price/last_traded_price/"
          "post_open_price/share_volume/turnover/trade_count matches its own known fixture values "
          "exactly (no assumption that different companies must have different prices)")
    for sym in REAL_SYMBOLS:
        r = by_symbol[sym]
        print(f"  {sym}: open_price={r['open_price']} closing_price={r['closing_price']} "
              f"last_traded_price={r['last_traded_price']} share_volume={r['share_volume']} "
              f"turnover={r['turnover']} trade_count={r['trade_count']}")


def test_company_not_found_is_a_distinct_category_not_silently_skipped():
    """A symbol not present in the security master must be recorded as
    'company_not_found', never silently dropped from the report."""
    # This test needs a real (or fake) conn since company_id lookup only
    # happens in the non-dry-run path — using a minimal fake conn/cursor
    # that always returns "not found" is enough to test this in isolation.
    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a, **kw): pass
        def fetchone(self): return None  # simulates "no company row found"

    class FakeConn:
        def cursor(self, *a, **kw): return FakeCursor()

    real_data = load_real_fixtures()
    install_mock_cse_client(real_data)

    report = cmc.capture_multiple_companies(
        conn=FakeConn(), symbols=["NOTAREAL.N0000"], window="post_close",
        request_attempt_id="test-attempt-5", observation_date="2026-09-08",
        dry_run=False, verbose=False,
    )

    assert report["failed_symbols"] == ["NOTAREAL.N0000"]
    assert "NOTAREAL.N0000" in report["failures_by_category"]["company_not_found"]
    print("PASS: an unrecognized symbol is recorded as 'company_not_found', not silently skipped")


def test_capture_report_has_all_required_fields():
    """Requirement 12: the report must contain every field specified."""
    real_data = load_real_fixtures()
    install_mock_cse_client(real_data, failing_symbols={"LOLC.N0000"})

    report = cmc.capture_multiple_companies(
        conn=None, symbols=REAL_SYMBOLS, window="post_open",
        request_attempt_id="test-attempt-6", observation_date="2026-09-08",
        dry_run=True, verbose=False,
    )

    required_top_level = [
        "requested_symbols", "successful_symbols", "failed_symbols",
        "failures_by_category", "per_symbol", "total_duration_ms",
        "request_attempt_id", "capture_window", "observation_date",
        "started_at", "finished_at",
    ]
    for field_name in required_top_level:
        assert field_name in report, f"Report missing required field: {field_name}"

    required_per_symbol = [
        "symbol", "status", "reason", "duration_ms", "reconciliation_status", "validation_status",
    ]
    for symbol_report in report["per_symbol"]:
        for field_name in required_per_symbol:
            assert field_name in symbol_report, f"Per-symbol report missing: {field_name}"

    assert isinstance(report["total_duration_ms"], int)
    assert all(isinstance(r["duration_ms"], int) for r in report["per_symbol"])
    print("PASS: capture report contains every required field, including per-symbol duration")


if __name__ == "__main__":
    test_tradeSummary_fetched_exactly_once_per_batch()
    test_partial_failure_isolation_http()
    test_multiple_failure_categories_isolated_simultaneously()
    test_real_multi_security_field_values()
    test_company_not_found_is_a_distinct_category_not_silently_skipped()
    test_capture_report_has_all_required_fields()
    print("\nAll Stage E (non-database) tests passed.")


# --- Database-dependent tests (require DATABASE_URL; skip gracefully if absent) ---
# These were run and verified against a real local Postgres instance with a
# 5-company test fixture before delivery. They're included here so they run
# again in YOUR environment against YOUR test database, not just trusted
# from this conversation's verification log.

def _db_tests_available():
    return os.environ.get("DATABASE_URL") is not None


def test_idempotent_retry_and_new_attempt_multi_company():
    if not _db_tests_available():
        print("SKIPPED: test_idempotent_retry_and_new_attempt_multi_company (no DATABASE_URL set)")
        return

    import uuid
    from worker import db

    real_data = load_real_fixtures()
    install_mock_cse_client(real_data)
    conn = db.get_connection()

    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_market_observations WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        cur.execute("DELETE FROM daily_market_data WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        conn.commit()

    attempt_alpha = str(uuid.uuid4())
    attempt_beta = str(uuid.uuid4())

    report1 = cmc.capture_multiple_companies(conn, REAL_SYMBOLS, "post_close", attempt_alpha,
                                              "2026-09-08", dry_run=False, verbose=False)
    assert len(report1["successful_symbols"]) == 5

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_market_observations WHERE request_attempt_id = %s", (attempt_alpha,))
        assert cur.fetchone()[0] == 5

    # Same attempt retried — must NOT create duplicates
    report2 = cmc.capture_multiple_companies(conn, REAL_SYMBOLS, "post_close", attempt_alpha,
                                              "2026-09-08", dry_run=False, verbose=False)
    assert len(report2["successful_symbols"]) == 5
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_market_observations WHERE request_attempt_id = %s", (attempt_alpha,))
        assert cur.fetchone()[0] == 5, "Retry of the same attempt must not create duplicate rows"

    # New attempt — must create NEW rows, not dedupe against the previous attempt
    report3 = cmc.capture_multiple_companies(conn, REAL_SYMBOLS, "post_close", attempt_beta,
                                              "2026-09-08", dry_run=False, verbose=False)
    assert len(report3["successful_symbols"]) == 5
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_market_observations WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        assert cur.fetchone()[0] == 10, "A new attempt must add new rows, not dedupe against the old one"
        cur.execute("SELECT count(*) FROM daily_market_data WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        assert cur.fetchone()[0] == 5, "Canonical daily_market_data must remain one row per company/date"

    conn.close()
    print("PASS: idempotent retry created zero duplicates (5), new attempt created new rows (10 total), "
          "canonical stayed at 5 — all verified against a real database")


def test_database_failure_isolation_with_rollback():
    """The critical fix: a mid-batch DB failure must not poison the shared
    connection for subsequent companies. This test failed before the
    conn.rollback() fix was added — kept as a permanent regression guard."""
    if not _db_tests_available():
        print("SKIPPED: test_database_failure_isolation_with_rollback (no DATABASE_URL set)")
        return

    import uuid
    from worker import db

    real_data = load_real_fixtures()
    install_mock_cse_client(real_data)
    conn = db.get_connection()

    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_market_observations WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        cur.execute("DELETE FROM daily_market_data WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        conn.commit()

    original_upsert = db.upsert_daily_market_data

    def failing_upsert(conn, *, company_id, trade_date, canonical):
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM companies WHERE id = %s", (company_id,))
            ticker = cur.fetchone()[0]
        if ticker == "JKH.N0000":
            raise RuntimeError("Injected database failure for JKH.N0000 (test)")
        return original_upsert(conn, company_id=company_id, trade_date=trade_date, canonical=canonical)

    db.upsert_daily_market_data = failing_upsert
    try:
        report = cmc.capture_multiple_companies(conn, REAL_SYMBOLS, "post_close", str(uuid.uuid4()),
                                                  "2026-09-08", dry_run=False, verbose=False)
    finally:
        db.upsert_daily_market_data = original_upsert

    assert report["failed_symbols"] == ["JKH.N0000"]
    assert "JKH.N0000" in report["failures_by_category"]["database_failure"]
    # The two companies captured AFTER JKH in the list (HNB, SAMP) must have
    # succeeded too — this is what proves the connection wasn't poisoned.
    assert "HNB.N0000" in report["successful_symbols"]
    assert "SAMP.N0000" in report["successful_symbols"]
    assert len(report["successful_symbols"]) == 4

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM daily_market_data WHERE company_id IN "
                     "(SELECT id FROM companies WHERE ticker = ANY(%s))", (REAL_SYMBOLS,))
        assert cur.fetchone()[0] == 4

    conn.close()
    print("PASS: a mid-batch database failure (JKH) did not poison the shared connection — "
          "companies captured AFTER it (HNB, SAMP) still succeeded")


if __name__ == "__main__":
    test_idempotent_retry_and_new_attempt_multi_company()
    test_database_failure_isolation_with_rollback()
