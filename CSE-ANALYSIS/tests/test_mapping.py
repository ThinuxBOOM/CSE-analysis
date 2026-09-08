"""
Unit tests for the mapping layer.

Two fixture sets:
- real_*_COMB_20260901.json — an ACTUAL captured CSE response (from the
  user's live dry-run). These are the primary regression tests now — they
  exist specifically so the two confirmed bugs (wrong nested-key selection,
  wrong share_volume/trade_count field names) can never silently recur.
- sample_*.json — older mock fixtures, kept only for tests that need a
  controlled/synthetic shape (e.g. deliberately testing an unwrap tie).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import mapping

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


# --- Regression tests against the REAL captured response ---

def test_real_response_selects_reqSymbolInfo_not_reqSymbolBetaInfo():
    """This is THE regression test for the confirmed bug: the real response
    has four nested blocks, and the old positional-first-dict logic picked
    reqSymbolBetaInfo (wrong). Must now correctly pick reqSymbolInfo."""
    body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    result = mapping.map_company_info_summary(body)

    assert result.notes["unwrapped_into_key"] == "reqSymbolInfo", (
        f"Expected reqSymbolInfo, got {result.notes['unwrapped_into_key']} — "
        f"the semantic-scoring unwrap fix has regressed."
    )
    # reqSymbolBetaInfo/reqTagsLogo/reqLogo must score 0 and not be chosen
    assert result.notes["unwrap_scores"]["reqSymbolBetaInfo"] == 0
    assert result.notes["unwrap_scores"]["reqSymbolInfo"] > 0
    assert "unwrap_ambiguous_candidates" not in result.notes  # no tie in the real data
    print("PASS: real response correctly unwraps into reqSymbolInfo, not reqSymbolBetaInfo")


def test_real_response_maps_expected_values_correctly():
    body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    result = mapping.map_company_info_summary(body)

    assert result.fields["last_traded_price"] == 203.75
    assert result.fields["closing_price"] == 203.75
    assert result.fields["market_cap"] == 317170605201.25
    assert result.fields["foreign_holding"] is None  # present in source, value is null —
                                                          # must be "found with null", not "missing"
    assert "foreign_holding" not in result.notes["expected_but_missing"]
    assert "last_traded_date" in result.notes["expected_but_missing"]  # still genuinely absent
    print("PASS: real companyInfoSummery values map correctly, null-but-present distinguished from missing")


def test_real_trade_summary_share_volume_bug_fixed():
    """THE regression test for the second confirmed bug: 'quantity': 25 must
    NOT be used for share_volume. The real day-total is 'sharevolume': 180410."""
    row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    result = mapping.map_trade_summary_row(row)

    assert result.fields["share_volume"] == 180410, (
        f"Got {result.fields.get('share_volume')} — this looks like the old bug "
        f"(matching 'quantity': 25, the last trade's size, not the day's volume) has regressed."
    )
    assert result.notes["found"]["share_volume"] == "sharevolume"
    print("PASS: share_volume correctly sourced from 'sharevolume' (180410), not 'quantity' (25)")


def test_real_trade_summary_trade_count_now_found():
    row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    result = mapping.map_trade_summary_row(row)

    assert result.fields["trade_count"] == 222
    assert result.notes["found"]["trade_count"] == "tradevolume"
    assert "trade_count" not in result.notes["expected_but_missing"]
    print("PASS: trade_count correctly found via 'tradevolume' (222), previously reported as missing")


def test_real_trade_summary_quantity_and_open_and_status_remain_unmapped():
    """SUPERSEDED — see test_real_trade_summary_quantity_and_status_remain_unmapped
    and test_open_price_mapped_from_real_tradeSummary_open_field below. 'open' is
    now intentionally mapped to open_price following the approved investigation,
    so the original three-field assertion no longer applies. Kept as a no-op
    stub (rather than silently deleted) so the historical intent is traceable."""
    pass


def test_cross_source_comparison_covers_all_four_confirmed_shared_fields():
    ci_body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    ts_row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    ci = mapping.map_company_info_summary(ci_body)
    ts = mapping.map_trade_summary_row(ts_row)
    obs = mapping.build_raw_observation(company_info_result=ci, trade_summary_result=ts, capture_window="post_close")

    comparison = obs["cross_source_comparison"]
    assert set(comparison.keys()) == {"last_traded_price", "closing_price", "market_cap", "turnover"}
    assert comparison["last_traded_price"]["agree_exactly"] is True   # 203.75 == 203.75
    assert comparison["closing_price"]["agree_exactly"] is True        # 203.75 == 203.75
    assert comparison["market_cap"]["agree_exactly"] is True             # exact match in real data
    assert comparison["turnover"]["agree_exactly"] is False                # 36,701,520.00 vs 36,701,518.75 — real drift
    assert comparison["turnover"]["companyInfoSummery_value"] == 36701520.0
    assert comparison["turnover"]["tradeSummary_value"] == 36701518.75
    print("PASS: cross-source comparison correctly covers all 4 confirmed shared fields, "
          "correctly flags the real turnover rounding difference")


def test_post_open_price_still_never_sourced_from_tradeSummary_open_field():
    """Explicit guard against the exact mistake the investigation is meant to
    prevent: post_open_price must come from last_traded_price, never from
    tradeSummary's 'open' field, until/unless that's explicitly decided."""
    ci_body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    ts_row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    ci = mapping.map_company_info_summary(ci_body)
    ts = mapping.map_trade_summary_row(ts_row)
    obs = mapping.build_raw_observation(company_info_result=ci, trade_summary_result=ts, capture_window="post_open")

    assert obs["post_open_price"] == 203.75  # last_traded_price, NOT tradeSummary's "open": 203.0
    assert obs["post_open_price"] != 203.0
    assert "open" not in obs
    print("PASS: post_open_price still sourced from last_traded_price, NOT from tradeSummary's 'open' "
          "field — architecture correctly left untouched pending investigation")


# --- Structural tests using synthetic data (unwrap tie-breaking) ---

def test_unwrap_tie_is_flagged_when_two_blocks_score_equally():
    body = {
        "blockA": {"lastTradedPrice": 100.0, "closingPrice": 99.0},
        "blockB": {"lastTradedPrice": 100.0, "closingPrice": 99.0},  # identical score, genuine tie
    }
    result = mapping.map_company_info_summary(body)
    assert result.notes["unwrapped_into_key"] in ("blockA", "blockB")
    assert set(result.notes["unwrap_ambiguous_candidates"]) == {"blockA", "blockB"}
    print("PASS: a genuine scoring tie between two plausible blocks is flagged, not silently resolved")


def test_no_matching_block_reports_error_not_silent_empty_result():
    body = {"reqSymbolBetaInfo": {"securityId": 369}, "reqLogo": {"id": 1}}
    result = mapping.map_company_info_summary(body)
    assert result.fields == {}
    assert "error" in result.notes
    print("PASS: when no nested block matches anything, this is reported as an error, not a silent empty result")


# --- open_price tests (added after the approved investigation) ---

def test_open_price_mapped_from_real_tradeSummary_open_field():
    row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    result = mapping.map_trade_summary_row(row)

    assert result.fields["open_price"] == 203.0
    assert result.notes["found"]["open_price"] == "open"
    assert "open_price" not in result.notes["expected_but_missing"]
    print("PASS: open_price correctly mapped from tradeSummary's real 'open' field (203.0)")


def test_open_no_longer_in_unexpected_fields():
    """Confirms 'open' moved from unexpected/unmapped to a real mapped field —
    the opposite assertion of the earlier test_real_trade_summary_quantity_and_open_and_status_remain_unmapped,
    which is updated below to drop 'open' from its checklist."""
    row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    result = mapping.map_trade_summary_row(row)
    assert "open" not in result.notes["unexpected_fields"], (
        "'open' should now be consumed by open_price mapping, not sitting in unexpected_fields"
    )
    print("PASS: 'open' is no longer reported as an unexpected/unmapped field")


def test_open_price_distinct_from_price_and_post_open_price():
    """The core distinctness guarantee: open_price, price (last_traded_price),
    and post_open_price must never collapse into the same value by accident.
    Uses the real fixture as-is, where last_traded_price (203.75) and
    open_price (203.0) already genuinely differ — no need to fabricate a
    divergence."""
    ci_body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    ts_row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    ci = mapping.map_company_info_summary(ci_body)
    ts = mapping.map_trade_summary_row(ts_row)

    obs_open_window = mapping.build_raw_observation(company_info_result=ci, trade_summary_result=ts, capture_window="post_open")
    assert obs_open_window["open_price"] == 203.0
    assert obs_open_window["last_traded_price"] == 203.75
    assert obs_open_window["post_open_price"] == 203.75   # still sourced from last_traded_price, NOT open_price
    assert obs_open_window["open_price"] != obs_open_window["post_open_price"]
    assert obs_open_window["open_price"] != obs_open_window["last_traded_price"]
    print("PASS: open_price, last_traded_price, and post_open_price remain three distinct values, "
          "post_open_price still sourced from last_traded_price (unchanged)")


def test_open_price_populated_regardless_of_capture_window():
    """Unlike post_open_price, open_price is a property of the trading day,
    not of when we polled — it should populate on post_close captures too."""
    ci_body = load_fixture("real_companyInfoSummery_COMB_20260901.json")
    ts_row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    ci = mapping.map_company_info_summary(ci_body)
    ts = mapping.map_trade_summary_row(ts_row)

    obs_close = mapping.build_raw_observation(company_info_result=ci, trade_summary_result=ts, capture_window="post_close")
    assert obs_close["open_price"] == 203.0          # populated even though window is post_close
    assert obs_close["post_open_price"] is None        # post_open_price correctly still null here — unchanged behavior
    print("PASS: open_price populates regardless of capture_window; post_open_price's "
          "window-gating behavior is completely unaffected")


def test_real_trade_summary_quantity_and_status_remain_unmapped():
    """Updated version of the earlier three-field test — 'open' is now
    intentionally mapped, so only quantity/status are checked here."""
    row = load_fixture("real_tradeSummary_row_COMB_20260901.json")
    result = mapping.map_trade_summary_row(row)

    for uncertain_field in ("quantity", "status"):
        assert uncertain_field not in result.fields, f"'{uncertain_field}' must not be mapped to any column yet"
        assert uncertain_field in result.notes["unexpected_fields"], (
            f"'{uncertain_field}' must be visible in unexpected_fields, not silently dropped"
        )
    assert result.notes["unexpected_fields"]["quantity"] == 25
    assert result.notes["unexpected_fields"]["status"] == 0
    print("PASS: quantity/status remain unmapped but visible (open is now correctly excluded from this check)")
if __name__ == "__main__":
    test_real_response_selects_reqSymbolInfo_not_reqSymbolBetaInfo()
    test_real_response_maps_expected_values_correctly()
    test_real_trade_summary_share_volume_bug_fixed()
    test_real_trade_summary_trade_count_now_found()
    test_real_trade_summary_quantity_and_open_and_status_remain_unmapped()
    test_cross_source_comparison_covers_all_four_confirmed_shared_fields()
    test_post_open_price_still_never_sourced_from_tradeSummary_open_field()
    test_unwrap_tie_is_flagged_when_two_blocks_score_equally()
    test_no_matching_block_reports_error_not_silent_empty_result()
    test_open_price_mapped_from_real_tradeSummary_open_field()
    test_open_no_longer_in_unexpected_fields()
    test_open_price_distinct_from_price_and_post_open_price()
    test_open_price_populated_regardless_of_capture_window()
    test_real_trade_summary_quantity_and_status_remain_unmapped()
    print("\nAll mapping tests passed.")


