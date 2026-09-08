"""
Reconciliation: derives the canonical daily_market_data row from a list of
raw_market_observations for one (company, date). Pure function — no I/O.
Fully re-runnable: pass it different raw observations, get a different
(but equally valid, equally derivable) canonical row. Nothing here is
authoritative on its own; it's always a function of the raw layer.
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


def _source_rank(source: str) -> int:
    try:
        return SOURCE_PRECEDENCE.index(source)
    except ValueError:
        return len(SOURCE_PRECEDENCE)  # unknown sources sort last


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

    for field_name in RECONCILABLE_FIELDS:
        candidates = [
            (o["source"], o.get(field_name), o["id"])
            for o in raw_observations
            if o.get(field_name) is not None
        ]
        if not candidates:
            continue

        if len(candidates) == 1:
            source, value, obs_id = candidates[0]
            result[field_name] = value
            result["field_provenance"][field_name] = {"observation_id": str(obs_id), "source": source}
            source_field_counts[source] = source_field_counts.get(source, 0) + 1
            contributing_ids.add(str(obs_id))
            continue

        # Multiple sources have a value — check agreement pairwise against
        # the highest-precedence one.
        candidates_sorted = sorted(candidates, key=lambda c: _source_rank(c[0]))
        winner_source, winner_value, winner_id = candidates_sorted[0]
        all_agree = True
        alt_values = []
        for source, value, obs_id in candidates_sorted[1:]:
            if not _values_agree(field_name, winner_value, value, tolerances):
                all_agree = False
            alt_values.append({"observation_id": str(obs_id), "source": source, "value": value})

        result[field_name] = winner_value
        result["field_provenance"][field_name] = {
            "observation_id": str(winner_id), "source": winner_source,
            **({"alternate_values": alt_values} if not all_agree else {}),
        }
        source_field_counts[winner_source] = source_field_counts.get(winner_source, 0) + 1
        contributing_ids.add(str(winner_id))
        for _, _, obs_id in candidates_sorted[1:]:
            contributing_ids.add(str(obs_id))

        if not all_agree:
            discrepancies[field_name] = {
                "winner": {"observation_id": str(winner_id), "source": winner_source, "value": winner_value},
                "alternates": alt_values,
            }

    result["contributing_observation_ids"] = sorted(contributing_ids)

    if discrepancies:
        result["reconciliation_status"] = "discrepancy_flagged"
        result["discrepancy_notes"] = discrepancies
    elif source_field_counts:
        # single_source if literally every populated field came from one
        # source with only one candidate each; 'agreed' if multiple sources
        # contributed but all agreed where compared.
        num_sources_used = len(source_field_counts)
        result["reconciliation_status"] = "single_source" if num_sources_used <= 1 else "agreed"
    # else: no reconcilable fields populated at all — stays 'pending'

    if source_field_counts:
        result["primary_source"] = max(source_field_counts.items(), key=lambda kv: kv[1])[0]

    return result
