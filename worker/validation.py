"""
Deterministic validation. Flags for review; never discards or rejects a
value. Pure function — no I/O.
"""
from typing import Optional


def validate(canonical: dict, previous_close: Optional[float], tolerances: dict) -> tuple[str, Optional[dict]]:
    notes = {}

    high = canonical.get("high")
    low = canonical.get("low")
    closing_price = canonical.get("closing_price")

    if high is not None and low is not None and high < low:
        notes["high_below_low"] = {"high": high, "low": low}

    if closing_price is not None and high is not None and low is not None:
        if not (low <= closing_price <= high):
            notes["closing_price_outside_high_low"] = {"closing_price": closing_price, "high": high, "low": low}

    for field_name in ("high", "low", "closing_price", "last_traded_price"):
        v = canonical.get(field_name)
        if v is not None and v <= 0:
            notes.setdefault("non_positive_price", []).append({"field": field_name, "value": v})

    for field_name in ("turnover", "share_volume", "trade_count"):
        v = canonical.get(field_name)
        if v is not None and v < 0:
            notes.setdefault("negative_volume_or_turnover", []).append({"field": field_name, "value": v})

    if closing_price is not None and previous_close is not None and previous_close != 0:
        change_pct = abs(closing_price - previous_close) / abs(previous_close) * 100
        threshold = tolerances.get("anomaly_price_change_threshold_pct", 30)
        if change_pct > threshold:
            notes["large_day_over_day_change"] = {
                "previous_close": previous_close,
                "closing_price": closing_price,
                "change_pct": round(change_pct, 2),
                "threshold_pct": threshold,
                "note": "No same-day corporate_actions check performed in Stage B (single-company "
                        "slice, no corporate-action ingestion yet) — this flag alone does not "
                        "distinguish a legitimate move from an error.",
            }

    status = "review_required" if notes else "ok"
    return status, (notes or None)
