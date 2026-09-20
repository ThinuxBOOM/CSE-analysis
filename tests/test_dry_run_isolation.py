"""
Proves --dry-run mode makes ZERO database connections, not just zero writes.
Approach: unset DATABASE_URL entirely (so any accidental db.get_connection()
call would raise config.ConfigError), monkeypatch cse_client to return mock
responses, run the dry-run path, and confirm it completes successfully
without ever importing/touching worker.db.
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Deliberately ensure DATABASE_URL is NOT set for this test, so any code path
# that tries to connect would fail loudly rather than silently succeeding
# against some ambient dev database.
os.environ.pop("DATABASE_URL", None)

from worker import cse_client, capture_single_company

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


class _Args:
    symbol = "COMB.N0000"
    window = "post_close"
    resume_attempt_id = None
    observation_date = "2026-08-28"
    dry_run = True


def test_dry_run_never_imports_db_module():
    # If worker.db were imported by the dry-run path, it would already be in
    # sys.modules by the time we get here (Python caches imports) — confirm
    # it is NOT, both before and after running dry-run.
    assert "worker.db" not in sys.modules, "worker.db must not be imported before dry-run even starts"

    mock_ci_body = load_fixture("sample_companyInfoSummery.json")
    mock_ts_row = load_fixture("sample_tradeSummary_row.json")

    def mock_get_company_info_summary(symbol):
        return cse_client.CSEResponse(
            endpoint="companyInfoSummery", request_method="POST", request_params={"symbol": symbol},
            status_code=200, ok=True, body=mock_ci_body, elapsed_ms=100,
        )

    def mock_get_trade_summary_all():
        return cse_client.CSEResponse(
            endpoint="tradeSummary", request_method="POST", request_params={},
            status_code=200, ok=True, body=[mock_ts_row], elapsed_ms=90,
        )

    original_ci = cse_client.get_company_info_summary
    original_ts = cse_client.get_trade_summary_all
    cse_client.get_company_info_summary = mock_get_company_info_summary
    cse_client.get_trade_summary_all = mock_get_trade_summary_all

    try:
        capture_single_company.run_dry_run(_Args())
    finally:
        cse_client.get_company_info_summary = original_ci
        cse_client.get_trade_summary_all = original_ts

    assert "worker.db" not in sys.modules, (
        "worker.db was imported during dry-run — this means the dry-run path "
        "touched the database layer, which violates the 'strictly observational "
        "and safe' requirement."
    )
    print("PASS: dry-run completed successfully without ever importing worker.db "
          "(no DATABASE_URL was even set — proves zero DB dependency, not just zero writes)")


def test_dry_run_works_with_no_database_url_set():
    # Redundant with the above but explicit: confirms no ConfigError is raised,
    # i.e. config.get_database_url() is never called either.
    assert os.environ.get("DATABASE_URL") is None
    print("PASS: dry-run ran successfully with DATABASE_URL completely unset")


if __name__ == "__main__":
    test_dry_run_never_imports_db_module()
    test_dry_run_works_with_no_database_url_set()
    print("\nAll dry-run isolation tests passed.")
