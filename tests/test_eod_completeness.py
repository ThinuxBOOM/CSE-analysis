"""
EOD completeness: "captured something" vs "an end-of-day capture was
reconciled" (daily_market_data.has_eod_observation, migration 0003), plus the
documented limits of what a post_close label can establish.

Pure-function and static tests run everywhere. The daily_completeness view
test needs a migrated database (DATABASE_URL) and skips without one; it runs
entirely inside a transaction that is rolled back.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import mapping, reconciliation, validation

TOLERANCES = {
    "reconciliation_price_tolerance_pct": 0.1,
    "reconciliation_volume_tolerance": 0,
    "reconciliation_turnover_tolerance_pct": 0.5,
    "anomaly_price_change_threshold_pct": 30,
}
ROOT = os.path.join(os.path.dirname(__file__), "..")
MIGRATIONS = os.path.join(ROOT, "supabase", "migrations")
WINDOWS_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "windows")


def obs(obs_id, window, observed_at, source="CSE_API", **values):
    row = {
        "id": obs_id, "capture_window": window, "source": source, "observed_at": observed_at,
        "post_open_price": None, "open_price": None, "last_traded_price": None,
        "last_traded_date": None, "closing_price": None, "high": None, "low": None,
        "turnover": None, "share_volume": None, "trade_count": None, "foreign_holding": None,
    }
    row.update(values)
    return row


INTRADAY = dict(post_open_price=80.0, open_price=79.5, last_traded_price=80.0, closing_price=0.0,
                high=80.0, low=79.5, turnover=1000.0, share_volume=12, trade_count=2)
FINAL = dict(open_price=79.5, last_traded_price=81.0, closing_price=81.0, high=82.0, low=79.0,
             turnover=90000.0, share_volume=1100, trade_count=40)


def _skip(reason):
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    print(f"SKIPPED: {reason}")


# --- has_eod_observation (set by reconcile) ---------------------------------

def test_has_eod_observation_by_window():
    po = obs("po1", "post_open", "2026-09-24T04:30:00+00:00", **INTRADAY)
    pc = obs("pc1", "post_close", "2026-09-24T09:30:00+00:00", **FINAL)
    bd = obs("b1", "bulletin_daily", "2026-09-25T02:00:00+00:00", source="CSE_BULLETIN_DAILY",
             closing_price=81.0)
    cases = {
        "no observations": ([], False),
        "post_open only": ([po], False),
        "post_close only": ([pc], True),
        "post_open + post_close": ([po, pc], True),
        "post_open + bulletin_daily": ([po, bd], True),
    }
    for label, (observations, expected) in cases.items():
        result = reconciliation.reconcile(observations, TOLERANCES)
        assert result["has_eod_observation"] is expected, f"{label}: got {result['has_eod_observation']}"
    print("PASS: has_eod_observation is true only when a non-intraday window was reconciled")


def test_intraday_only_discrepancy_is_still_not_eod():
    """Why a separate flag is needed: a post_open-only row can be
    'discrepancy_flagged' (not 'pending'), so reconciliation_status alone
    cannot tell the view whether an EOD capture exists."""
    result = reconciliation.reconcile([
        obs("po1", "post_open", "2026-09-24T04:05:00+00:00", **{**INTRADAY, "open_price": 79.5}),
        obs("po2", "post_open", "2026-09-24T04:40:00+00:00", **{**INTRADAY, "open_price": 85.0}),
    ], TOLERANCES)
    assert result["reconciliation_status"] == "discrepancy_flagged"
    assert result["has_eod_observation"] is False
    assert result["closing_price"] is None
    print("PASS: an intraday-only row stays non-EOD even when its status is discrepancy_flagged")


def test_post_close_captured_before_close_is_an_attempt_not_proof_of_finalization():
    """The system has no configured market-close time (trading_calendar holds
    only open/closed/unknown per date), so observed_at cannot be checked
    against a close. A post_close capture taken while CSE still reports the
    mid-session state (closingPrice 0.0, as in 276/276 real mid-session rows)
    counts as an EOD attempt; its 0.0 is kept verbatim and surfaced by
    validation rather than hidden or nulled."""
    early_values = {**INTRADAY, "post_open_price": None}
    early = obs("pc_early", "post_close", "2026-09-24T05:30:00+00:00", **early_values)
    result = reconciliation.reconcile([early], TOLERANCES)
    assert result["has_eod_observation"] is True
    assert result["closing_price"] == 0.0
    assert result["share_volume"] == 12  # session-to-date, but the label is all we can check
    status, notes = validation.validate(result, previous_close=None, tolerances=TOLERANCES)
    assert status == "review_required" and "non_positive_price" in notes

    # observed_at plays no part in the EOD decision (nothing to compare it to)
    late = obs("pc_early", "post_close", "2026-09-24T12:00:00+00:00", **early_values)
    late_result = reconciliation.reconcile([late], TOLERANCES)
    for field in reconciliation.RECONCILABLE_FIELDS + ["has_eod_observation", "reconciliation_status"]:
        assert late_result.get(field) == result.get(field), field
    print("PASS: early post_close = EOD attempt; 0.0 close kept and flagged review_required, not nulled")


def test_real_post_close_close_outside_high_low_kept_verbatim():
    """Real 2026-09-23 post-close SOY.N0000: CSE reported closingPrice =
    price = previousClose = 1952.0 while the day's high/low were 2065/1987.
    Recorded as-is (no semantic reinterpretation); validation flags it."""
    with open(os.path.join(WINDOWS_FIXTURES, "real_postclose_tradeSummary_row_SOY_N0000_20260923.json")) as f:
        ts_row = json.load(f)
    with open(os.path.join(WINDOWS_FIXTURES, "real_postclose_companyInfoSummery_SOY_N0000_20260923.json")) as f:
        ci_body = json.load(f)
    raw = mapping.build_raw_observation(
        company_info_result=mapping.map_company_info_summary(ci_body),
        trade_summary_result=mapping.map_trade_summary_row(ts_row),
        capture_window="post_close",
    )
    fields = {k: v for k, v in raw.items() if k not in ("cross_source_comparison", "market_cap")}
    result = reconciliation.reconcile([obs("soy_pc", "post_close", "2026-09-23T18:23:00+00:00", **fields)],
                                      TOLERANCES)
    assert (result["closing_price"], result["last_traded_price"], result["high"], result["low"]) == \
        (1952.0, 1952.0, 2065.0, 1987.0)
    assert result["share_volume"] == 69 and result["trade_count"] == 4
    assert result["has_eod_observation"] is True
    status, notes = validation.validate(result, previous_close=1952.0, tolerances=TOLERANCES)
    assert status == "review_required" and "closing_price_outside_high_low" in notes
    print("PASS: real out-of-range post-close close kept verbatim and flagged, not reinterpreted")


# --- persistence --------------------------------------------------------------

def test_upsert_persists_has_eod_observation():
    from worker import db

    captured = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params

    class _Conn:
        def cursor(self, **_):
            return _Cursor()

        def commit(self):
            captured["committed"] = True

    for flag in (True, False):
        canonical = reconciliation.reconcile(
            [obs("pc1", "post_close", "2026-09-24T09:30:00+00:00", **FINAL)] if flag else
            [obs("po1", "post_open", "2026-09-24T04:30:00+00:00", **INTRADAY)], TOLERANCES)
        db.upsert_daily_market_data(_Conn(), company_id="c1", trade_date="2026-09-24", canonical=canonical)
        assert captured["params"]["has_eod_observation"] is flag
        assert "has_eod_observation = excluded.has_eod_observation" in captured["sql"]
        json.dumps(json.loads(captured["params"]["field_provenance"]))  # provenance stays JSON-clean
    print("PASS: upsert writes has_eod_observation on insert and on conflict-update")


# --- migration 0003 (static checks; no database needed) -------------------------

def _view_select_columns(sql_text):
    m = re.search(r"view daily_completeness as\s+select(.*?)\bfrom trading_calendar tc", sql_text,
                  re.S | re.I)
    assert m, "daily_completeness select list not found"
    body, depth, parts, current = m.group(1), 0, [], ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    names = []
    for part in parts:
        part = part.strip()
        alias = re.search(r"\bas\s+(\w+)\s*$", part, re.I)
        names.append(alias.group(1) if alias else part.split(".")[-1])
    return names


def test_migration_0003_view_is_a_valid_replacement():
    with open(os.path.join(MIGRATIONS, "0001_phase1_data_foundation.sql")) as f:
        v1 = f.read()
    with open(os.path.join(MIGRATIONS, "0003_eod_observation_completeness.sql")) as f:
        v3 = f.read()
    old_cols, new_cols = _view_select_columns(v1), _view_select_columns(v3)
    # CREATE OR REPLACE VIEW may only append columns; existing ones must keep name and order
    assert old_cols == ["trade_date", "market_status", "expected_count", "captured_count", "completeness_status"]
    assert new_cols == old_cols + ["eod_captured_count"], new_cols
    assert "create or replace view daily_completeness" in v3.lower()
    # 'complete' is decided by EOD-reconciled rows, 'missing' still by any capture
    assert re.search(r"eod_captured_count, 0\) = expected\.expected_count then 'complete'", v3)
    assert re.search(r"captured\.captured_count, 0\) = 0 then 'missing'", v3)
    # the SQL backfill uses the same intraday window set as reconcile()
    assert reconciliation.INTRADAY_WINDOWS == {"post_open"}
    assert "r.capture_window <> 'post_open'" in v3
    print("PASS: migration 0003 keeps the view's existing columns, appends eod_captured_count, "
          "and matches reconcile()'s window policy")


# --- the view itself (requires a migrated database) ----------------------------

def test_daily_completeness_view_distinguishes_intraday_from_eod():
    if not os.environ.get("DATABASE_URL"):
        _skip("test_daily_completeness_view_distinguishes_intraday_from_eod (no DATABASE_URL set)")
        return
    from worker import db
    conn = db.get_connection()
    trade_date = "2099-01-05"  # far-future date no real capture can use
    try:
        with conn.cursor() as cur:
            cur.execute("insert into trading_calendar (trade_date, market_status, established_by) "
                        "values (%s, 'open', 'live_capture')", (trade_date,))
            cur.execute("insert into companies (ticker, company_name) values ('ZZTEST.N0000', 'EOD test') "
                        "returning id")
            company_id = cur.fetchone()[0]
            cur.execute("insert into daily_market_data (company_id, trade_date, has_eod_observation) "
                        "values (%s, %s, false)", (company_id, trade_date))
            cur.execute("select captured_count, eod_captured_count, completeness_status "
                        "from daily_completeness where trade_date = %s", (trade_date,))
            captured, eod, status = cur.fetchone()
            assert (captured, eod) == (1, 0) and status == "partial", (captured, eod, status)

            cur.execute("update daily_market_data set has_eod_observation = true "
                        "where company_id = %s and trade_date = %s", (company_id, trade_date))
            cur.execute("select captured_count, eod_captured_count from daily_completeness "
                        "where trade_date = %s", (trade_date,))
            assert cur.fetchone() == (1, 1)
    finally:
        conn.rollback()  # nothing is ever committed by this test
        conn.close()
    print("PASS: daily_completeness counts a post_open-only row as captured but not EOD-complete")


if __name__ == "__main__":
    test_has_eod_observation_by_window()
    test_intraday_only_discrepancy_is_still_not_eod()
    test_post_close_captured_before_close_is_an_attempt_not_proof_of_finalization()
    test_real_post_close_close_outside_high_low_kept_verbatim()
    test_upsert_persists_has_eod_observation()
    test_migration_0003_view_is_a_valid_replacement()
    test_daily_completeness_view_distinguishes_intraday_from_eod()
    print("\nAll EOD-completeness tests passed (DB view test skipped if DATABASE_URL unset).")
