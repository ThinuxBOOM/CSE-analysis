"""
Reconciliation: derives the canonical daily_market_data row from a list of
raw_market_observations for one (company, date). Pure function — no I/O.
Fully re-runnable: pass it different raw observations, get a different
(but equally valid, equally derivable) canonical row. Nothing here is
authoritative on its own; it's always a function of the raw layer.

FROZEN 2026-09-24 at the window-aware policy below. Do not change without an
explicit decision. Operational requirements (migration 0003 before live
capture; re-run reconciliation for historical days with both post_open and
post_close observations) and the open Stage F investigations are listed at
the top of README.md.
"""
from typing import Optional

SOURCE_PRECEDENCE = ["CSE_API", "CSE_BULLETIN_DAILY", "CSE_BULLETIN_MONTHLY"]

RECONCILABLE_FIELDS = [
    "high", "low", "closing_price", "last_traded_price", "last_traded_date",
    "turnover", "share_volume", "trade_count", "foreign_holding", "open_price",
]

PRICE_FIELDS = {"high", "low", "closing_price", "last_traded_price", "open_price"}
VOLUME_FIELDS = {"share_volume", "trade_count"}
TURNOVER_FIELDS = {"turnover"}

# Window-aware field policy. Established from real CSE responses
# (2026-09-23 post-close vs 2026-09-24 mid-session snapshots), not assumed:
#
# - Capture windows taken while the session is open. Their values for
#   END_OF_DAY_FIELDS are session-to-date (partial) or, for closing_price,
#   0.0 = "not published yet" (276/276 tradeSummary rows mid-session; 0/284
#   post-close). Such observations stay in the raw layer untouched, but never
#   supply an end-of-day canonical value.
INTRADAY_WINDOWS = {"post_open"}
#
# - Fields describing the FINAL state of the trading day. Only observations
#   from non-intraday windows (post_close, bulletins, manual) are eligible.
#   If none exist yet, the canonical value stays NULL and field_provenance
#   records why, plus the intraday values seen — never a partial value
#   presented as the day's figure. Zero is NOT treated as missing: a
#   post_close share_volume/trade_count/turnover of 0 is a real value.
END_OF_DAY_FIELDS = {"closing_price", "high", "low", "turnover", "share_volume", "trade_count"}
#
# - All other RECONCILABLE_FIELDS (open_price, last_traded_price,
#   last_traded_date, foreign_holding) are eligible from any window; the most
#   recent observation of the highest-precedence source wins. open_price is
#   session-fixed (see migration 0002), so every window should agree anyway.
#   last_traded_price is whatever CSE reports in price/lastTradedPrice at the
#   winning observation. After the close that equalled closingPrice in every
#   real post-close row seen (284/284 on 2026-09-23, plus all repo fixtures),
#   including 16 rows where it lay outside the day's reported high/low — so a
#   post_close last_traded_price must NOT be read as the final executed trade.
#
# Tie-break within a source: most recent observed_at wins (previously the
# earliest won, which let a post_open value beat post_close). Source
# precedence across sources is unchanged.
#
# Disagreements: a cross-source disagreement is always flagged, as before.
# A same-source older value is recorded as "superseded" (kept in
# field_provenance, not flagged) when it describes a different moment of a
# moving value — i.e. it came from a different window, or the windows are
# intraday. Two same-source observations from the same non-intraday window
# claim the same final fact, so disagreement there is still flagged.
# SESSION_FIXED_FIELDS are established as constant for the session, so any
# disagreement in them is flagged regardless of window.
SESSION_FIXED_FIELDS = {"open_price"}


def _source_rank(source: str) -> int:
    try:
        return SOURCE_PRECEDENCE.index(source)
    except ValueError:
        return len(SOURCE_PRECEDENCE)  # unknown sources sort last


def _describe(obs: dict, field: str) -> dict:
    return {"observation_id": str(obs["id"]), "source": obs["source"],
            "capture_window": obs["capture_window"], "observed_at": str(obs["observed_at"]),
            "value": obs.get(field)}


def _values_agree(field: str, a, b, tolerances: dict) -> bool:
    if a is None or b is None:
        return True  # nothing to disagree about
    if field in VOLUME_FIELDS:
        tol = tolerances.get("reconciliation_volume_tolerance", 0)
        return abs(a - b) <= tol
    if field in TURNOVER_FIELDS:
        tol_pct = tolerances.get("reconciliation_turnover_tolerance_pct", 0.5)
        if a == 0 and b == 0:
            return True
        base = max(abs(a), abs(b), 1e-9)
        return abs(a - b) / base * 100 <= tol_pct
    if field in PRICE_FIELDS:
        tol_pct = tolerances.get("reconciliation_price_tolerance_pct", 0.1)
        if a == 0 and b == 0:
            return True
        base = max(abs(a), abs(b), 1e-9)
        return abs(a - b) / base * 100 <= tol_pct
    return a == b


def reconcile(
    raw_observations: list[dict],
    tolerances: dict,
) -> dict:
    """
    raw_observations: list of dicts as returned by db.get_raw_observations_for_date,
    each with at least: id, capture_window, source, and the value columns.
    tolerances: the resolved system_config values (caller's responsibility to
    fetch — kept out of this pure function for testability).
    """
    result = {
        "post_open_price": None,
        "post_open_captured_at": None,
        "field_provenance": {},
        "contributing_observation_ids": [],
        "primary_source": None,
        "reconciliation_status": "pending",
        "discrepancy_notes": None,
        "has_eod_observation": False,
    }

    # post_open_price: only ever from a 'post_open' window observation.
    post_open_obs = [o for o in raw_observations if o["capture_window"] == "post_open"]
    if post_open_obs:
        chosen = sorted(post_open_obs, key=lambda o: o["observed_at"])[-1]  # most recent attempt
        result["post_open_price"] = chosen.get("post_open_price")
        result["post_open_captured_at"] = chosen.get("observed_at")
        result["contributing_observation_ids"].append(str(chosen["id"]))

    discrepancies = {}
    source_field_counts = {}
    contributing_ids = set(result["contributing_observation_ids"])
    has_end_of_day_observation = any(o["capture_window"] not in INTRADAY_WINDOWS for o in raw_observations)

    for field_name in RECONCILABLE_FIELDS:
        present = [o for o in raw_observations if o.get(field_name) is not None]
        if field_name in END_OF_DAY_FIELDS:
            intraday_only = [o for o in present if o["capture_window"] in INTRADAY_WINDOWS]
            present = [o for o in present if o["capture_window"] not in INTRADAY_WINDOWS]
            if not present:
                if intraday_only:
                    result[field_name] = None
                    result["field_provenance"][field_name] = {
                        "status": "not_yet_available",
                        "reason": "only intraday-window observations exist; their value is session-to-date, "
                                  "not the end-of-day figure",
                        "intraday_values": [_describe(o, field_name) for o in intraday_only],
                    }
                continue
        if not present:
            continue

        # Highest-precedence source first; within a source, most recent first.
        # Two stable sorts, so observed_at may be str or datetime (never mixed).
        candidates = sorted(present, key=lambda o: o["observed_at"], reverse=True)
        candidates = sorted(candidates, key=lambda o: _source_rank(o["source"]))
        winner = candidates[0]
        winner_value = winner.get(field_name)

        comparable = []
        any_disagreement = False
        superseded = []
        for o in candidates[1:]:
            value = o.get(field_name)
            agrees = _values_agree(field_name, winner_value, value, tolerances)
            is_moving_value_update = (
                o["source"] == winner["source"]
                and field_name not in SESSION_FIXED_FIELDS
                and (o["capture_window"] != winner["capture_window"]
                     or winner["capture_window"] in INTRADAY_WINDOWS)
            )
            if is_moving_value_update:
                if not agrees:
                    superseded.append(_describe(o, field_name))
                continue
            comparable.append({"observation_id": str(o["id"]), "source": o["source"], "value": value})
            any_disagreement = any_disagreement or not agrees
        # As before: on a discrepancy, every comparable candidate is listed
        # (agreeing ones included), so the full picture is preserved.
        alt_values = comparable if any_disagreement else []

        result[field_name] = winner_value
        result["field_provenance"][field_name] = {
            "observation_id": str(winner["id"]), "source": winner["source"],
            "capture_window": winner["capture_window"], "observed_at": str(winner["observed_at"]),
            **({"alternate_values": alt_values} if alt_values else {}),
            **({"superseded_values": superseded} if superseded else {}),
        }
        source_field_counts[winner["source"]] = source_field_counts.get(winner["source"], 0) + 1
        for o in candidates:
            contributing_ids.add(str(o["id"]))

        if alt_values:
            discrepancies[field_name] = {
                "winner": {"observation_id": str(winner["id"]), "source": winner["source"], "value": winner_value},
                "alternates": alt_values,
            }

    result["contributing_observation_ids"] = sorted(contributing_ids)
    # Persisted as daily_market_data.has_eod_observation (migration 0003) and
    # used by the daily_completeness view. Means "an end-of-day capture was
    # reconciled", NOT "CSE had finalised every field" — there is no
    # configured market-close time to verify a capture against.
    result["has_eod_observation"] = has_end_of_day_observation

    if discrepancies:
        result["reconciliation_status"] = "discrepancy_flagged"
        result["discrepancy_notes"] = discrepancies
    elif source_field_counts and has_end_of_day_observation:
        # single_source if literally every populated field came from one
        # source with only one candidate each; 'agreed' if multiple sources
        # contributed but all agreed where compared.
        num_sources_used = len(source_field_counts)
        result["reconciliation_status"] = "single_source" if num_sources_used <= 1 else "agreed"
    # else: stays 'pending' — either nothing reconcilable was populated, or
    # only intraday observations exist, so the day's end-of-day state has not
    # been observed yet and this row must not read as a finished EOD record.

    if source_field_counts:
        result["primary_source"] = max(source_field_counts.items(), key=lambda kv: kv[1])[0]

    return result
