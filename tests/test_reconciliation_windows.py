"""
Window-aware reconciliation: post_open (intraday) vs post_close (end-of-day)
observations of the SAME source for the same company/day.

Grounded in real CSE behaviour observed 2026-09-23 (post-close) and
2026-09-24 (mid-session): during the session closingPrice is 0.0 in both
endpoints and volume/turnover/trade count/high/low are session-to-date.
The real mid-session CARS.N0000 responses are in fixtures/windows/.

Pure functions only — no network, no database.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import mapping, reconciliation, validation

TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}
WINDOWS_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "windows")
EOD_FIELDS = ["closing_price", "high", "low", "turnover", "share_volume", "trade_count"]


def obs(obs_id, window, observed_at, source="CSE_API", **values):
    row = {
        "id": obs_id, "capture_window": window, "source": source, "observed_at": observed_at,
        "post_open_price": None, "open_price": None, "last_traded_price": None,
        "last_traded_date": None, "closing_price": None, "high": None, "low": None,
        "turnover": None, "share_volume": None, "trade_count": None, "foreign_holding": None,
    }
    row.update(values)
    return row


def post_open(obs_id="po1", observed_at="2026-09-24T04:30:00+00:00", **overrides):
    values = dict(post_open_price=80.0, open_price=79.5, last_traded_price=80.0, closing_price=0.0,
                  high=80.0, low=79.5, turnover=1000.0, share_volume=12, trade_count=2)
    values.update(overrides)
    return obs(obs_id, "post_open", observed_at, **values)


def post_close(obs_id="pc1", observed_at="2026-09-24T09:30:00+00:00", **overrides):
    values = dict(open_price=79.5, last_traded_price=81.0, closing_price=81.0,
                  high=82.0, low=79.0, turnover=90000.0, share_volume=1100, trade_count=40)
    values.update(overrides)
    return obs(obs_id, "post_close", observed_at, **values)


def _reconcile_both_orders(observations):
    """The DB returns rows ORDER BY observed_at; the result must not depend
    on input order anyway."""
    a = reconciliation.reconcile(list(observations), TOLERANCES)
    b = reconciliation.reconcile(list(reversed(observations)), TOLERANCES)
    for field in reconciliation.RECONCILABLE_FIELDS + ["reconciliation_status", "post_open_price"]:
        assert a.get(field) == b.get(field), f"{field} depends on input order: {a.get(field)} vs {b.get(field)}"
    return a


def test_case_a_post_close_closing_price_beats_post_open_zero():
    result = _reconcile_both_orders([post_open(), post_close()])
    assert result["closing_price"] == 81.0
    prov = result["field_provenance"]["closing_price"]
    assert prov["observation_id"] == "pc1" and prov["capture_window"] == "post_close"
    assert result["reconciliation_status"] == "single_source"
    assert result["discrepancy_notes"] is None
    status, notes = validation.validate(result, previous_close=None, tolerances=TOLERANCES)
    assert status == "ok", notes
    print("PASS A: post_close closing_price (81) wins over post_open's 0.0")


def test_case_a_with_real_midsession_cse_response():
    """Same as A, but the post_open side goes through the REAL mapper using the
    real 2026-09-24 mid-session CARS.N0000 responses (closingPrice 0.0 in both
    endpoints). The post_close side is synthetic: no same-day real post-close
    capture exists for that date."""
    with open(os.path.join(WINDOWS_FIXTURES, "real_midsession_tradeSummary_row_CARS_N0000_20260924.json")) as f:
        ts_row = json.load(f)
    with open(os.path.join(WINDOWS_FIXTURES, "real_midsession_companyInfoSummery_CARS_N0000_20260924.json")) as f:
        ci_body = json.load(f)
    raw = mapping.build_raw_observation(
        company_info_result=mapping.map_company_info_summary(ci_body),
        trade_summary_result=mapping.map_trade_summary_row(ts_row),
        capture_window="post_open",
    )
    assert raw["closing_price"] == 0.0  # the real value; the mapper is deliberately unchanged
    real_po = obs("real_po", "post_open", "2026-09-24T07:20:30+00:00",
                  **{k: v for k, v in raw.items() if k != "cross_source_comparison" and k != "market_cap"})
    synthetic_pc = post_close(closing_price=745.0, last_traded_price=745.0, open_price=711.0,
                              high=750.0, low=705.0, turnover=900000.0, share_volume=1250, trade_count=40)

    only_po = reconciliation.reconcile([real_po], TOLERANCES)
    assert only_po["closing_price"] is None
    assert only_po["field_provenance"]["closing_price"]["intraday_values"][0]["value"] == 0.0

    result = _reconcile_both_orders([real_po, synthetic_pc])
    assert result["closing_price"] == 745.0
    assert result["share_volume"] == 1250 and result["trade_count"] == 40
    assert result["open_price"] == 711.0  # real tradeSummary.open agrees with post_close -> no flag
    assert result["post_open_price"] == 739.75  # real mid-session last price, untouched
    assert result["reconciliation_status"] == "single_source"
    print("PASS A (real fixture): real mid-session 0.0 closingPrice never reaches canonical once post_close exists")


def test_case_b_post_close_supplies_all_end_of_day_fields():
    result = _reconcile_both_orders([post_open(), post_close()])
    expected = {"closing_price": 81.0, "high": 82.0, "low": 79.0, "turnover": 90000.0,
                "share_volume": 1100, "trade_count": 40}
    for field, value in expected.items():
        assert result[field] == value, f"{field}: {result[field]} != {value}"
        prov = result["field_provenance"][field]
        assert prov["capture_window"] == "post_close"
        # the partial-day value is still traceable, not silently dropped
        assert "superseded_values" not in prov or all(
            s["capture_window"] == "post_open" for s in prov["superseded_values"])
    assert result["discrepancy_notes"] is None
    assert set(result["contributing_observation_ids"]) == {"po1", "pc1"}
    # last_traded_price: latest observation wins; intraday value kept as superseded
    assert result["last_traded_price"] == 81.0
    assert result["field_provenance"]["last_traded_price"]["superseded_values"][0]["value"] == 80.0
    print("PASS B: all end-of-day fields from post_close; intraday values traceable, not flagged")


def test_case_c_post_open_only_is_not_an_eod_record():
    result = reconciliation.reconcile([post_open()], TOLERANCES)
    for field in EOD_FIELDS:
        assert result[field] is None, f"{field} should be withheld, got {result[field]}"
        prov = result["field_provenance"][field]
        assert prov["status"] == "not_yet_available"
        assert prov["intraday_values"][0]["observation_id"] == "po1"
    # the raw 0.0 is preserved verbatim in provenance, not converted
    assert result["field_provenance"]["closing_price"]["intraday_values"][0]["value"] == 0.0
    assert result["field_provenance"]["share_volume"]["intraday_values"][0]["value"] == 12
    # session / latest-state fields are legitimately available
    assert result["post_open_price"] == 80.0
    assert result["open_price"] == 79.5
    assert result["last_traded_price"] == 80.0
    assert result["field_provenance"]["last_traded_price"]["capture_window"] == "post_open"
    assert result["reconciliation_status"] == "pending"
    status, notes = validation.validate(result, previous_close=None, tolerances=TOLERANCES)
    assert status == "ok", notes  # no bogus non-positive/outside-range flags from a 0.0 close
    print("PASS C: post_open-only -> EOD fields NULL with explicit reason, status 'pending'")


def test_case_d_post_close_only():
    result = reconciliation.reconcile([post_close()], TOLERANCES)
    for field, value in {"closing_price": 81.0, "high": 82.0, "low": 79.0, "turnover": 90000.0,
                         "share_volume": 1100, "trade_count": 40, "open_price": 79.5,
                         "last_traded_price": 81.0}.items():
        assert result[field] == value
        assert result["field_provenance"][field]["observation_id"] == "pc1"
    assert result["post_open_price"] is None
    assert result["reconciliation_status"] == "single_source"
    print("PASS D: post_close-only -> complete EOD record from that observation")


def test_case_e_two_post_open_observations_latest_wins():
    early = post_open("po1", "2026-09-24T04:05:00+00:00", post_open_price=80.0, last_traded_price=80.0,
                      share_volume=5)
    late = post_open("po2", "2026-09-24T04:40:00+00:00", post_open_price=80.5, last_traded_price=80.5,
                     share_volume=12)
    result = _reconcile_both_orders([early, late])
    assert result["post_open_price"] == 80.5  # existing rule: most recent post_open attempt
    assert result["post_open_captured_at"] == "2026-09-24T04:40:00+00:00"
    assert result["last_traded_price"] == 80.5
    assert result["field_provenance"]["last_traded_price"]["observation_id"] == "po2"
    assert result["field_provenance"]["last_traded_price"]["superseded_values"][0]["observation_id"] == "po1"
    assert result["share_volume"] is None  # still no EOD observation
    assert len(result["field_provenance"]["share_volume"]["intraday_values"]) == 2
    assert result["discrepancy_notes"] is None
    assert result["reconciliation_status"] == "pending"
    print("PASS E: two post_open observations -> latest intraday state, EOD still withheld")


def test_case_e_open_price_disagreement_is_still_flagged():
    """open_price is established as session-fixed; if two windows ever
    disagree, that contradicts the finding and must be flagged, not hidden."""
    result = _reconcile_both_orders([post_open(open_price=79.5), post_close(open_price=85.0)])
    assert result["reconciliation_status"] == "discrepancy_flagged"
    assert "open_price" in result["discrepancy_notes"]
    print("PASS E: a session-fixed open_price disagreement across windows is flagged")


def test_case_f_legitimate_zeros_are_kept():
    result = reconciliation.reconcile(
        [post_open(share_volume=0, trade_count=0, turnover=0.0),
         post_close(share_volume=0, trade_count=0, turnover=0.0)], TOLERANCES)
    for field in ("share_volume", "trade_count", "turnover"):
        assert result[field] == 0 and result[field] is not None
        assert result["field_provenance"][field]["capture_window"] == "post_close"

    # No generic zero->NULL rule: a post_close closing_price of 0.0 (observed
    # in real companyInfoSummery for non-traded securities after close) is
    # kept verbatim and surfaced by validation, not silently nulled here.
    result = reconciliation.reconcile([post_close(closing_price=0.0)], TOLERANCES)
    assert result["closing_price"] == 0.0
    status, notes = validation.validate(result, previous_close=None, tolerances=TOLERANCES)
    assert status == "review_required" and "non_positive_price" in notes
    print("PASS F: legitimate zero volume/trade count/turnover kept; no zero->NULL conversion")


def test_case_g_cross_source_precedence_unchanged():
    api_close = post_close(closing_price=100.0, last_traded_price=100.0)
    bulletin = obs("b1", "bulletin_daily", "2026-09-25T02:00:00+00:00", source="CSE_BULLETIN_DAILY",
                   closing_price=110.0)
    result = _reconcile_both_orders([post_open(), api_close, bulletin])
    assert result["closing_price"] == 100.0
    assert result["field_provenance"]["closing_price"]["source"] == "CSE_API"
    assert result["reconciliation_status"] == "discrepancy_flagged"
    notes = result["discrepancy_notes"]["closing_price"]
    assert notes["winner"]["value"] == 100.0
    assert [a["value"] for a in notes["alternates"]] == [110.0]  # post_open 0.0 is not an "alternate"
    print("PASS G: cross-source precedence and discrepancy flagging unchanged")


def test_case_g_bulletin_fills_eod_when_api_only_has_intraday():
    bulletin = obs("b1", "bulletin_daily", "2026-09-25T02:00:00+00:00", source="CSE_BULLETIN_DAILY",
                   closing_price=81.0, high=82.0, low=79.0, turnover=90000.0, share_volume=1100)
    result = _reconcile_both_orders([post_open(), bulletin])
    assert result["closing_price"] == 81.0
    assert result["field_provenance"]["closing_price"]["source"] == "CSE_BULLETIN_DAILY"
    assert result["trade_count"] is None  # bulletin didn't carry it; intraday value not promoted
    assert result["field_provenance"]["trade_count"]["status"] == "not_yet_available"
    assert result["last_traded_price"] == 80.0  # only the API observed it
    assert result["reconciliation_status"] == "agreed"
    print("PASS G: an intraday API value never outranks an end-of-day bulletin value")


def test_same_window_same_source_disagreement_still_flagged():
    """Two post_close captures (e.g. a new attempt) both claim the final close;
    disagreement there is a real conflict and stays flagged. Latest wins."""
    result = _reconcile_both_orders([
        post_close("pc1", "2026-09-24T09:30:00+00:00", closing_price=81.0),
        post_close("pc2", "2026-09-24T10:30:00+00:00", closing_price=83.0),
    ])
    assert result["closing_price"] == 83.0
    assert result["reconciliation_status"] == "discrepancy_flagged"
    assert result["discrepancy_notes"]["closing_price"]["alternates"][0]["observation_id"] == "pc1"
    print("PASS: same-window same-source disagreement still flagged; most recent wins")


if __name__ == "__main__":
    test_case_a_post_close_closing_price_beats_post_open_zero()
    test_case_a_with_real_midsession_cse_response()
    test_case_b_post_close_supplies_all_end_of_day_fields()
    test_case_c_post_open_only_is_not_an_eod_record()
    test_case_d_post_close_only()
    test_case_e_two_post_open_observations_latest_wins()
    test_case_e_open_price_disagreement_is_still_flagged()
    test_case_f_legitimate_zeros_are_kept()
    test_case_g_cross_source_precedence_unchanged()
    test_case_g_bulletin_fills_eod_when_api_only_has_intraday()
    test_same_window_same_source_disagreement_still_flagged()
    print("\nAll window-aware reconciliation tests passed.")
