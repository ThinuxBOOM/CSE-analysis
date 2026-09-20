"""
Tests for validate_universe_coverage.py. Uses a synthetic universe at
realistic scale (~50 symbols) mixing real fixture data with deliberately
injected failures of every category, since we can't run against the live
CSE API from this environment — the real run happens on the user's side
(see worker/validate_universe_coverage.py's usage instructions).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import cse_client, validate_universe_coverage as vuc

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "multi_company")
REAL_SYMBOLS = ["COMB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000"]


def load_real_fixtures():
    data = {}
    for symbol in REAL_SYMBOLS:
        safe = symbol.replace(".", "_")
        with open(os.path.join(FIXTURES_DIR, f"real_companyInfoSummery_{safe}.json")) as f:
            ci_body = json.load(f)
        with open(os.path.join(FIXTURES_DIR, f"real_tradeSummary_row_{safe}.json")) as f:
            ts_row = json.load(f)
        data[symbol] = (ci_body, ts_row)
    return data


def build_synthetic_universe(real_data, n=50):
    """
    A realistic-scale synthetic universe: cycles through the 5 real
    fixtures for most entries (renamed per-entry so each has a unique
    symbol, but with genuinely real field shapes/values), plus deliberately
    injected special cases covering every category.
    """
    universe = []
    real_syms = list(real_data.keys())
    for i in range(n - 6):  # leave room for the 6 special cases below
        universe.append(f"SYN{i:03d}.N0000")
    universe += [
        "HTTPFAIL.N0000",       # will fail companyInfoSummery
        "NOTRADESUMM.N0000",     # exists in companyInfo but absent from tradeSummary
        "BADSCHEMA.N0000",        # companyInfoSummery returns totally unrecognizable shape
        "DUPLICATE.N0000",         # appears twice in the discovered list
        "DUPLICATE.N0000",
        "COMB.N0000",               # one real, genuinely successful entry mixed in
    ]
    return universe


def install_universe_mocks(real_data, universe):
    real_syms = list(real_data.keys())

    def mock_get_all_security_codes():
        return cse_client.CSEResponse(
            endpoint="allSecurityCode", request_method="GET", request_params={},
            status_code=200, ok=True, body=[{"symbol": s} for s in universe],
        )

    def mock_get_company_info_summary(symbol):
        if symbol == "HTTPFAIL.N0000":
            return cse_client.CSEResponse(endpoint="companyInfoSummery", request_method="POST",
                request_params={"symbol": symbol}, status_code=500, ok=False, error="Simulated HTTP failure")
        if symbol == "BADSCHEMA.N0000":
            return cse_client.CSEResponse(endpoint="companyInfoSummery", request_method="POST",
                request_params={"symbol": symbol}, status_code=200, ok=True,
                body={"totallyUnrecognized": {"nothingWeExpect": True}})
        if symbol.startswith("SYN") or symbol == "NOTRADESUMM.N0000" or symbol.startswith("DUPLICATE"):
            # Reuse a real fixture's shape/values, cycling through the 5 real ones
            source_symbol = real_syms[hash(symbol) % len(real_syms)]
            ci_body, _ = real_data[source_symbol]
            return cse_client.CSEResponse(endpoint="companyInfoSummery", request_method="POST",
                request_params={"symbol": symbol}, status_code=200, ok=True, body=ci_body)
        # Real symbols (e.g. COMB.N0000 mixed in)
        ci_body, _ = real_data[symbol]
        return cse_client.CSEResponse(endpoint="companyInfoSummery", request_method="POST",
            request_params={"symbol": symbol}, status_code=200, ok=True, body=ci_body)

    def mock_get_trade_summary_all():
        rows = []
        for symbol in universe:
            if symbol in ("NOTRADESUMM.N0000", "HTTPFAIL.N0000", "BADSCHEMA.N0000"):
                continue  # deliberately absent from the batch response
            source_symbol = real_syms[hash(symbol) % len(real_syms)] if (
                symbol.startswith("SYN") or symbol.startswith("DUPLICATE")
            ) else symbol
            _, ts_row = real_data.get(source_symbol, real_data[real_syms[0]])
            row = dict(ts_row)
            row["symbol"] = symbol  # tag with the actual symbol we're pretending this row is for
            rows.append(row)
        return cse_client.CSEResponse(
            endpoint="tradeSummary", request_method="POST", request_params={},
            status_code=200, ok=True, body=rows,
        )

    cse_client.get_all_security_codes = mock_get_all_security_codes
    cse_client.get_company_info_summary = mock_get_company_info_summary
    cse_client.get_trade_summary_all = mock_get_trade_summary_all


def test_full_synthetic_universe_all_categories_covered():
    real_data = load_real_fixtures()
    universe = build_synthetic_universe(real_data, n=50)
    install_universe_mocks(real_data, universe)

    report = vuc.validate_universe_coverage(universe, request_delay_seconds=0, verbose=False)

    assert report["requested_count"] == 50
    assert report["unique_count"] == 49  # one duplicate pair -> 49 unique symbols
    assert report["duplicate_symbols"] == {"DUPLICATE.N0000": 2}

    categories = report["counts_by_category"]
    assert categories.get("companyinfo_http_failure") == 1
    assert categories.get("missing_from_tradesummary") == 1
    assert categories.get("unexpected_response_schema") == 1
    assert categories.get("duplicate_symbol") == 1
    assert categories.get("successful", 0) >= 45  # everything else

    assert "HTTPFAIL.N0000" in report["symbols_by_category"]["companyinfo_http_failure"]
    assert "NOTRADESUMM.N0000" in report["symbols_by_category"]["missing_from_tradesummary"]
    assert "BADSCHEMA.N0000" in report["symbols_by_category"]["unexpected_response_schema"]
    assert "DUPLICATE.N0000" in report["symbols_by_category"]["duplicate_symbol"]
    assert "COMB.N0000" in report["symbols_by_category"]["successful"]

    # Every requested symbol accounted for somewhere — nothing silently dropped
    total_accounted = sum(categories.values())
    assert total_accounted == 50, f"Expected all 50 requested symbols accounted for, got {total_accounted}"

    print("PASS: 50-symbol synthetic universe — every category (successful, "
          "companyinfo_http_failure, missing_from_tradesummary, unexpected_response_schema, "
          "duplicate_symbol) correctly identified, all 50 requested symbols accounted for")


def test_tradesummary_fetched_exactly_once_at_scale():
    real_data = load_real_fixtures()
    universe = build_synthetic_universe(real_data, n=50)
    install_universe_mocks(real_data, universe)

    call_count = {"n": 0}
    original = cse_client.get_trade_summary_all
    def counting():
        call_count["n"] += 1
        return original()
    cse_client.get_trade_summary_all = counting

    vuc.validate_universe_coverage(universe, request_delay_seconds=0, verbose=False)
    assert call_count["n"] == 1, f"Expected exactly 1 tradeSummary call for 50 symbols, got {call_count['n']}"
    print("PASS: tradeSummary fetched exactly once for a 50-symbol universe run")


def test_tradesummary_batch_failure_categorizes_all_symbols():
    """If the shared tradeSummary call itself fails, every symbol should be
    categorized as tradesummary_api_failure — not silently processed as if
    nothing happened, and not crashing the run."""
    real_data = load_real_fixtures()
    universe = ["COMB.N0000", "JKH.N0000", "LOLC.N0000"]
    install_universe_mocks(real_data, universe)

    def failing_ts():
        return cse_client.CSEResponse(endpoint="tradeSummary", request_method="POST",
            request_params={}, status_code=503, ok=False, error="Simulated total batch failure")
    cse_client.get_trade_summary_all = failing_ts

    report = vuc.validate_universe_coverage(universe, request_delay_seconds=0, verbose=False)
    assert report["counts_by_category"] == {"tradesummary_api_failure": 3}
    assert set(report["symbols_by_category"]["tradesummary_api_failure"]) == set(universe)
    print("PASS: a total tradeSummary batch failure correctly categorizes every symbol, "
          "does not crash, does not silently proceed as if nothing happened")


def test_report_has_all_required_top_level_fields():
    real_data = load_real_fixtures()
    universe = build_synthetic_universe(real_data, n=20)
    install_universe_mocks(real_data, universe)

    report = vuc.validate_universe_coverage(universe, request_delay_seconds=0, verbose=False)

    required = [
        "started_at", "finished_at", "total_duration_ms", "requested_count", "unique_count",
        "duplicate_symbols", "tradesummary_fetched_once", "tradesummary_call_ok",
        "counts_by_category", "symbols_by_category", "unexpected_fields_by_symbol", "per_symbol",
    ]
    for field_name in required:
        assert field_name in report, f"Report missing required field: {field_name}"

    for symbol_result in report["per_symbol"]:
        assert "symbol" in symbol_result and "category" in symbol_result and "duration_ms" in symbol_result

    print("PASS: universe coverage report contains all required fields, per-symbol duration included")


if __name__ == "__main__":
    test_full_synthetic_universe_all_categories_covered()
    test_tradesummary_fetched_exactly_once_at_scale()
    test_tradesummary_batch_failure_categorizes_all_symbols()
    test_report_has_all_required_top_level_fields()
    print("\nAll universe-coverage-validation tests passed (synthetic, at realistic scale).")
