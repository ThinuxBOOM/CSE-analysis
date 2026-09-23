"""
Tests for investigate_missing_n_securities.py — mocked CSE responses built
from real captured fixtures (field names/shapes are real; symbols are
re-tagged). No network, no database.

Unlike some older test files, every monkeypatch here is restored afterwards,
so this file can't leak mocks into other tests in the same pytest process.
"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import cse_client, investigate_missing_n_securities as inv

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "multi_company")

# Must equal the 19 tickers from the real 2026-09-21 coverage report.
EXPECTED_TICKERS = {
    "AMCL.N0000", "ALHP.N0000", "ACAP.N0000", "BRR.N0000", "BLI.N0000", "BLUE.N0000",
    "BBH.N0000", "CARG.N0000", "CARS.N0000", "CHOU.N0000", "CFI.N0000", "SOY.N0000",
    "ELPL.N0000", "HELA.N0000", "KDL.N0000", "SHAW.N0000", "CSF.N0000", "SLND.N0000",
    "LHL.N0000",
}


def _load(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


REAL_CI = _load("real_companyInfoSummery_COMB_N0000.json")
REAL_TS_ROW = _load("real_tradeSummary_row_COMB_N0000.json")


def _resp(endpoint, body=None, ok=True, status_code=200, error=None, raw_text=None):
    return cse_client.CSEResponse(endpoint=endpoint, request_method="POST", request_params={},
                                  status_code=status_code, ok=ok, body=body, error=error,
                                  raw_text=raw_text)


def _ts_row(symbol, **overrides):
    row = dict(REAL_TS_ROW)
    row["symbol"] = symbol
    row.update(overrides)
    return row


def _ci_body(symbol):
    body = copy.deepcopy(REAL_CI)
    body["reqSymbolInfo"]["symbol"] = symbol
    return body


class _Mocks:
    """Installs mocks on cse_client and restores the originals on exit."""

    def __init__(self, universe_resp, ts_resp, ci_fn):
        self.calls = {"allSecurityCode": 0, "tradeSummary": 0, "companyInfoSummery": 0}
        self._universe_resp, self._ts_resp, self._ci_fn = universe_resp, ts_resp, ci_fn

    def __enter__(self):
        self._orig = (cse_client.get_all_security_codes, cse_client.get_trade_summary_all,
                      cse_client.get_company_info_summary)

        def universe():
            self.calls["allSecurityCode"] += 1
            return self._universe_resp

        def ts():
            self.calls["tradeSummary"] += 1
            return self._ts_resp

        def ci(symbol):
            self.calls["companyInfoSummery"] += 1
            return self._ci_fn(symbol)

        cse_client.get_all_security_codes = universe
        cse_client.get_trade_summary_all = ts
        cse_client.get_company_info_summary = ci
        return self

    def __exit__(self, *exc):
        (cse_client.get_all_security_codes, cse_client.get_trade_summary_all,
         cse_client.get_company_info_summary) = self._orig
        return False


def _default_scenario(ts_resp=None):
    """Universe = the 19 + two liquid names. tradeSummary carries the two
    liquid names plus ONE of the 19 (BRR), which 'reappeared' today."""
    universe = sorted(EXPECTED_TICKERS) + ["COMB.N0000", "JKH.N0000"]
    universe_resp = _resp("allSecurityCode", body=[{"symbol": s, "id": i} for i, s in enumerate(universe)])
    if ts_resp is None:
        ts_resp = _resp("tradeSummary", body={"reqTradeSummery": [
            _ts_row("COMB.N0000"), _ts_row("JKH.N0000", sharevolume=0), _ts_row("BRR.N0000"),
        ]})
    return universe_resp, ts_resp, lambda s: _resp("companyInfoSummery", body=_ci_body(s))


def test_ticker_list_is_exactly_the_historical_19():
    assert len(inv.HISTORICAL_MISSING_N_TICKERS) == 19
    assert len(set(inv.HISTORICAL_MISSING_N_TICKERS)) == 19
    assert set(inv.HISTORICAL_MISSING_N_TICKERS) == EXPECTED_TICKERS
    print("PASS: ticker list is exactly the 19 historical tickers, no duplicates")


def test_presence_recorded_per_endpoint_and_reappearance_is_true():
    with _Mocks(*_default_scenario()) as m:
        report = inv.investigate(request_delay_seconds=0, verbose=False)

    assert m.calls == {"allSecurityCode": 1, "tradeSummary": 1, "companyInfoSummery": 19}
    by = {r["ticker"]: r for r in report["per_ticker"]}
    assert set(by) == EXPECTED_TICKERS

    # BRR reappeared today: must be True, not reported as "still missing".
    assert by["BRR.N0000"]["present_in_tradeSummary"] is True
    assert by["BRR.N0000"]["tradeSummary_rows_raw"][0]["symbol"] == "BRR.N0000"
    assert report["summary"]["present_in_tradeSummary_today"] == ["BRR.N0000"]

    for t in EXPECTED_TICKERS - {"BRR.N0000"}:
        assert by[t]["present_in_tradeSummary"] is False
        assert by[t]["tradeSummary_rows_raw"] == []
    for r in report["per_ticker"]:
        assert r["present_in_allSecurityCode"] is True
        assert r["present_in_companyInfoSummery"] is True
        assert r["companyInfoSummery_data_block_key"] == "reqSymbolInfo"
        assert r["companyInfoSummery_activity_fields_verbatim"]["symbol"] == r["ticker"]
    print("PASS: presence per endpoint recorded; a reappeared ticker is present_in_tradeSummary=True")


def test_status_field_recorded_verbatim_and_no_trad_false_positives():
    with _Mocks(*_default_scenario()):
        report = inv.investigate(request_delay_seconds=0, verbose=False)
    brr = next(r for r in report["per_ticker"] if r["ticker"] == "BRR.N0000")

    # Raw value 0 recorded as-is, uninterpreted.
    assert brr["tradeSummary_status_like_fields"] == {"[0].status": 0}
    # Trade-ish names must NOT be picked up.
    ts_keys = " ".join(brr["tradeSummary_status_like_fields"])
    ci_keys = " ".join(brr["companyInfoSummery_status_like_fields"])
    for noisy in ("lastTradedPrice", "tradevolume", "lastTradedTime", "crossingTradeVol"):
        assert noisy not in ts_keys
    for noisy in ("tdyTradeVolume", "hiTrade", "lowTrade", "lastTradedPrice"):
        assert noisy not in ci_keys
    # Nothing in the report tries to decode the value.
    assert "interpret" not in json.dumps(brr).lower()
    print("PASS: status recorded verbatim (0), no 'trad' false positives")


def test_unparseable_tradesummary_is_unknown_not_absent():
    """A 200 response with a non-JSON body leaves ok=True, body=None in
    cse_client. That must become presence=None (unknown), never False."""
    bad_ts = _resp("tradeSummary", body=None, ok=True, error="Response was not valid JSON",
                   raw_text="<html>maintenance</html>")
    universe_resp, _, ci_fn = _default_scenario()
    with _Mocks(universe_resp, bad_ts, ci_fn):
        report = inv.investigate(request_delay_seconds=0, verbose=False)

    health = report["endpoint_health"]["tradeSummary"]
    assert health["usable"] is False and "not parseable" in health["unusable_reason"]
    assert health["raw_text_if_not_json"] == "<html>maintenance</html>"
    assert all(r["present_in_tradeSummary"] is None for r in report["per_ticker"])
    assert report["summary"]["absent_from_tradeSummary_today"] == []
    assert len(report["summary"]["tradeSummary_presence_unknown"]) == 19
    assert report["todays_universe_comparison"]["in_allSecurityCode_not_in_tradeSummary"] is None
    assert report["tradeSummary_sharevolume_tally"] is None
    print("PASS: unparseable tradeSummary -> presence unknown (None), never False")


def test_failed_and_empty_tradesummary_are_unknown_not_absent():
    universe_resp, _, ci_fn = _default_scenario()
    for ts in (_resp("tradeSummary", ok=False, status_code=503, error="Simulated"),
               _resp("tradeSummary", body={"reqTradeSummery": []})):
        with _Mocks(universe_resp, ts, ci_fn):
            report = inv.investigate(request_delay_seconds=0, verbose=False)
        assert report["endpoint_health"]["tradeSummary"]["usable"] is False
        assert all(r["present_in_tradeSummary"] is None for r in report["per_ticker"])
    print("PASS: failed or empty tradeSummary -> presence unknown, never False")


def test_companyinfo_failure_is_unknown_and_recorded():
    universe_resp, ts_resp, _ = _default_scenario()

    def ci(symbol):
        if symbol == "HELA.N0000":
            return _resp("companyInfoSummery", ok=False, status_code=500, error="Simulated")
        return _resp("companyInfoSummery", body=_ci_body(symbol))

    with _Mocks(universe_resp, ts_resp, ci):
        report = inv.investigate(request_delay_seconds=0, verbose=False)
    hela = next(r for r in report["per_ticker"] if r["ticker"] == "HELA.N0000")
    assert hela["present_in_companyInfoSummery"] is None
    assert hela["companyInfoSummery_http"]["status_code"] == 500
    assert report["summary"]["companyInfoSummery_presence_unknown"] == ["HELA.N0000"]
    assert len(report["per_ticker"]) == 19
    print("PASS: a companyInfoSummery failure is recorded as unknown, run continues")


def test_universe_comparison_and_sharevolume_tally():
    with _Mocks(*_default_scenario()):
        report = inv.investigate(request_delay_seconds=0, verbose=False)
    comp = report["todays_universe_comparison"]
    assert comp["in_allSecurityCode_not_in_tradeSummary"] == sorted(EXPECTED_TICKERS - {"BRR.N0000"})
    assert comp["in_tradeSummary_not_in_allSecurityCode"] == []
    tally = report["tradeSummary_sharevolume_tally"]
    assert tally["rows"] == 3 and tally["sharevolume_zero"] == 1
    assert tally["symbols_with_zero_or_null"] == ["JKH.N0000"]
    print("PASS: today's universe diff and sharevolume tally computed from the shared responses")


def test_main_writes_report_at_workflow_upload_path():
    """The workflow uploads missing_n_securities_investigation_2026-09-21.json
    with if-no-files-found: error — the default file name must match it."""
    workflow = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows",
                            "missing-n-securities-investigation-2026-09-24.yml")
    with open(workflow) as f:
        assert f"path: {inv.DEFAULT_REPORT_FILE}" in f.read()

    old_cwd, old_argv = os.getcwd(), sys.argv
    with tempfile.TemporaryDirectory() as tmp, _Mocks(*_default_scenario()):
        try:
            os.chdir(tmp)
            sys.argv = ["investigate_missing_n_securities", "--request-delay-seconds", "0"]
            inv.main()
            with open(os.path.join(tmp, inv.DEFAULT_REPORT_FILE)) as f:
                report = json.load(f)
        finally:
            os.chdir(old_cwd)
            sys.argv = old_argv
    assert len(report["per_ticker"]) == 19
    assert report["raw_tradeSummary_body"] is not None
    print("PASS: main() writes the report at the exact path the workflow uploads")


def test_module_has_no_database_dependency():
    source_path = inv.__file__
    with open(source_path) as f:
        source = f.read()
    assert "import db" not in source and "from . import db" not in source
    assert "psycopg2" not in source and "DATABASE_URL" not in source
    print("PASS: investigation module has no database dependency")


if __name__ == "__main__":
    test_ticker_list_is_exactly_the_historical_19()
    test_presence_recorded_per_endpoint_and_reappearance_is_true()
    test_status_field_recorded_verbatim_and_no_trad_false_positives()
    test_unparseable_tradesummary_is_unknown_not_absent()
    test_failed_and_empty_tradesummary_are_unknown_not_absent()
    test_companyinfo_failure_is_unknown_and_recorded()
    test_universe_comparison_and_sharevolume_tally()
    test_main_writes_report_at_workflow_upload_path()
    test_module_has_no_database_dependency()
    print("\nAll missing-.N-securities investigation tests passed.")
