import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import reconciliation, validation

TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}


def test_single_source_reconciliation():
    obs = [
        {"id": "a1", "capture_window": "post_close", "source": "CSE_API", "observed_at": "2026-08-28T09:10:00",
         "closing_price": 105.5, "high": 106.2, "low": 104.9, "turnover": 1000, "share_volume": 500,
         "trade_count": None, "foreign_holding": None, "last_traded_price": 105.5, "last_traded_date": None,
         "post_open_price": None},
    ]
    result = reconciliation.reconcile(obs, TOLERANCES)
    assert result["closing_price"] == 105.5
    assert result["reconciliation_status"] == "single_source"
    assert result["field_provenance"]["closing_price"]["source"] == "CSE_API"
    print("PASS: single-source reconciliation")


def test_mixed_source_agreement():
    obs = [
        {"id": "a1", "capture_window": "post_close", "source": "CSE_API", "observed_at": "2026-08-28T09:10:00",
         "closing_price": 105.50, "high": None, "low": None, "turnover": None, "share_volume": None,
         "trade_count": None, "foreign_holding": None, "last_traded_price": 105.50, "last_traded_date": None,
         "post_open_price": None},
        {"id": "b2", "capture_window": "bulletin_daily", "source": "CSE_BULLETIN_DAILY", "observed_at": "2026-08-29T02:00:00",
         "closing_price": 105.52, "high": 106.20, "low": 104.90, "turnover": 1000, "share_volume": 500,
         "trade_count": None, "foreign_holding": None, "last_traded_price": None, "last_traded_date": None,
         "post_open_price": None},
    ]
    result = reconciliation.reconcile(obs, TOLERANCES)
    # closing_price: both sources have a value, within 0.1% tolerance (105.50 vs 105.52 = 0.019%) -> agreed, API wins by precedence
    assert result["closing_price"] == 105.50
    assert result["field_provenance"]["closing_price"]["source"] == "CSE_API"
    # high/low only from bulletin -> single value used directly
    assert result["high"] == 106.20
    assert result["field_provenance"]["high"]["source"] == "CSE_BULLETIN_DAILY"
    assert result["reconciliation_status"] == "agreed"
    print("PASS: mixed-source agreement, field-level provenance correct, no discrepancy falsely flagged")


def test_mixed_source_discrepancy_flagged_not_discarded():
    obs = [
        {"id": "a1", "capture_window": "post_close", "source": "CSE_API", "observed_at": "2026-08-28T09:10:00",
         "closing_price": 100.00, "high": None, "low": None, "turnover": None, "share_volume": None,
         "trade_count": None, "foreign_holding": None, "last_traded_price": 100.00, "last_traded_date": None,
         "post_open_price": None},
        {"id": "b2", "capture_window": "bulletin_daily", "source": "CSE_BULLETIN_DAILY", "observed_at": "2026-08-29T02:00:00",
         "closing_price": 110.00, "high": None, "low": None, "turnover": None, "share_volume": None,
         "trade_count": None, "foreign_holding": None, "last_traded_price": None, "last_traded_date": None,
         "post_open_price": None},
    ]
    result = reconciliation.reconcile(obs, TOLERANCES)
    # 10% apart, way outside 0.1% tolerance -> discrepancy flagged, API wins by precedence, BOTH retained
    assert result["closing_price"] == 100.00  # CSE_API precedence wins
    assert result["reconciliation_status"] == "discrepancy_flagged"
    assert result["discrepancy_notes"]["closing_price"]["winner"]["value"] == 100.00
    assert result["discrepancy_notes"]["closing_price"]["alternates"][0]["value"] == 110.00
    print("PASS: discrepancy flagged with BOTH values retained, canonical value chosen deterministically")


def test_post_open_price_never_touched_by_other_windows():
    obs = [
        {"id": "a1", "capture_window": "post_open", "source": "CSE_API", "observed_at": "2026-08-28T04:10:00",
         "closing_price": None, "high": None, "low": None, "turnover": None, "share_volume": None,
         "trade_count": None, "foreign_holding": None, "last_traded_price": 103.00, "last_traded_date": None,
         "post_open_price": 103.00},
        {"id": "b2", "capture_window": "post_close", "source": "CSE_API", "observed_at": "2026-08-28T09:10:00",
         "closing_price": 105.50, "high": 106.2, "low": 104.9, "turnover": 1000, "share_volume": 500,
         "trade_count": None, "foreign_holding": None, "last_traded_price": 105.50, "last_traded_date": None,
         "post_open_price": None},
    ]
    result = reconciliation.reconcile(obs, TOLERANCES)
    assert result["post_open_price"] == 103.00
    assert result["closing_price"] == 105.50
    print("PASS: post_open_price sourced only from the post_open window, unaffected by post_close data")


def test_validation_flags_large_move_without_rejecting():
    canonical = {"closing_price": 150.0, "high": 151.0, "low": 149.0}
    status, notes = validation.validate(canonical, previous_close=100.0, tolerances=TOLERANCES)
    assert status == "review_required"
    assert "large_day_over_day_change" in notes
    # value itself must NOT be altered/rejected by validation
    assert canonical["closing_price"] == 150.0
    print("PASS: validation flags anomaly but never mutates/rejects the observed value")


def test_validation_ok_for_normal_data():
    canonical = {"closing_price": 105.0, "high": 106.0, "low": 104.0}
    status, notes = validation.validate(canonical, previous_close=104.5, tolerances=TOLERANCES)
    assert status == "ok"
    assert notes is None
    print("PASS: normal data validates as ok with no notes")


if __name__ == "__main__":
    test_single_source_reconciliation()
    test_mixed_source_agreement()
    test_mixed_source_discrepancy_flagged_not_discarded()
    test_post_open_price_never_touched_by_other_windows()
    test_validation_flags_large_move_without_rejecting()
    test_validation_ok_for_normal_data()
    print("\nAll reconciliation/validation tests passed.")
