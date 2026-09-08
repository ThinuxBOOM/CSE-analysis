"""
Maps CSE API responses into the raw_market_observations shape. Pure functions
only — no network calls, no database calls. Testable in complete isolation.

Hard rule enforced here: a field is populated ONLY if it is genuinely present
in the CSE response, under a field name we have positive evidence for. We
never invent, estimate, or rename a field to fill a gap, and we never
broaden the mapper to cover a field whose semantics aren't established yet
(e.g. tradeSummary's "status" field stays unmapped — see notes at bottom).

Every mapping decision is logged in `notes` so a human can see exactly what
was found, what was expected-but-missing, what showed up that wasn't
expected (WITH its value), and any place the mapper had to choose between
ambiguous options rather than silently picking one.

CHANGE LOG (real-data corrections, not guesses):
- companyInfoSummery's real response has FOUR nested blocks
  (reqSymbolBetaInfo, reqTagsLogo, reqLogo, reqSymbolInfo), not one. The
  previous "pick the first nested dict" heuristic picked the wrong one
  (reqSymbolBetaInfo). Replaced with a semantic match: score every nested
  dict by how many of our candidate field names it actually contains, and
  pick the highest-scoring one. Confirmed against a real captured response.
- tradeSummary's real per-company row uses "sharevolume" (all lowercase,
  not "shareVolume") for the day's cumulative share volume, and
  "tradevolume" (also lowercase — a count, despite the name) for trade
  count. The old candidate list matched "quantity" for share_volume, which
  is actually the size of the single most recent trade (confirmed: 25 vs.
  the real day total of 180,410) — removed "quantity" from that field's
  candidates entirely, since it's a different, wrong concept, not merely a
  lower-priority synonym.
"""
from dataclasses import dataclass
from typing import Any, Optional

# Candidate field names per source, in priority order, per confirmed real
# evidence where noted, or remaining (explicitly labeled) guesses otherwise.
COMPANY_INFO_FIELD_CANDIDATES = {
    "last_traded_price": ["lastTradedPrice", "last_traded_price", "ltp"],   # confirmed: lastTradedPrice
    "closing_price": ["closingPrice", "closing_price", "close"],             # confirmed: closingPrice
    "market_cap": ["marketCap", "market_cap"],                                 # confirmed: marketCap
                                                                                   # (no DB column yet —
                                                                                   # cross-check use only)
    "foreign_holding": ["foreignHoldings", "foreign_holding", "foreignHolding"],  # confirmed field name
                                                                                     # exists; was null in
                                                                                     # our one real sample
    "turnover": ["tdyTurnover", "turnover"],                                          # confirmed: tdyTurnover
                                                                                         # (today's turnover) —
                                                                                         # cross-check vs.
                                                                                         # tradeSummary's
                                                                                         # "turnover"
    "last_traded_date": ["lastTradedDate", "last_traded_date", "dateLastTraded"],   # STILL UNCONFIRMED —
                                                                                       # absent in our one
                                                                                       # real sample too
}

TRADE_SUMMARY_FIELD_CANDIDATES = {
    "last_traded_price": ["price", "lastTradedPrice", "last_traded_price"],   # confirmed: "price"
    "closing_price": ["closingPrice", "closing_price"],                         # confirmed present here too
                                                                                    # (cross-check vs.
                                                                                    # companyInfoSummery)
    "market_cap": ["marketCap", "market_cap"],                                    # confirmed present here
                                                                                      # too (cross-check)
    "high": ["high", "hiTrade"],                                                     # confirmed: "high"
    "low": ["low", "lowTrade"],                                                        # confirmed: "low"
    "turnover": ["turnover", "tdyTurnover"],                                             # confirmed: "turnover"
    "share_volume": ["sharevolume", "shareVolume", "tdyShareVolume"],                      # FIXED — confirmed
                                                                                              # real name is
                                                                                              # "sharevolume"
                                                                                              # (lowercase v);
                                                                                              # "quantity"
                                                                                              # deliberately
                                                                                              # excluded, see
                                                                                              # module docstring
    "trade_count": ["tradevolume", "tradeCount", "trades"],                                 # FIXED — confirmed
                                                                                              # real name is
                                                                                              # "tradevolume"
                                                                                              # (a count,
                                                                                              # despite the name)
    "open_price": ["open"],                                                                    # CONFIRMED via
                                                                                                   # dedicated
                                                                                                   # investigation
                                                                                                   # (2-day, 5-symbol
                                                                                                   # daily study +
                                                                                                   # a 20-min
                                                                                                   # post-open burst
                                                                                                   # on 2026-09-07):
                                                                                                   # session-scoped
                                                                                                   # reference price,
                                                                                                   # available at/
                                                                                                   # after market
                                                                                                   # open, fixed for
                                                                                                   # the session in
                                                                                                   # 15/15 symbol-
                                                                                                   # sessions
                                                                                                   # observed, zero
                                                                                                   # exceptions.
                                                                                                   # Functions as
                                                                                                   # CSE's
                                                                                                   # designated
                                                                                                   # daily opening
                                                                                                   # price — NOT
                                                                                                   # confirmed to be
                                                                                                   # the literal
                                                                                                   # first executed
                                                                                                   # trade or a
                                                                                                   # specific
                                                                                                   # opening
                                                                                                   # mechanism.
                                                                                                   # Distinct from,
                                                                                                   # and never
                                                                                                   # merged with,
                                                                                                   # post_open_price.
}

# Fields we now know exist in BOTH endpoints, per the real sample — used to
# generate an explicit cross-source comparison rather than silently picking
# one value with no record of the other having existed too.
CROSS_CHECK_FIELDS = ["last_traded_price", "closing_price", "market_cap", "turnover"]

# Fields observed in real responses whose semantics are NOT yet established.
# Deliberately never mapped to a column — preserved only in raw_payload,
# per the "do not silently broaden the mapper" instruction. Listed here so
# it's an explicit, documented decision, not an oversight.
KNOWN_UNMAPPED_UNCERTAIN_FIELDS = {
    "status": "Present in tradeSummary (observed value: 0 in our one real sample). Meaning "
              "unconfirmed — possibly a trading/suspension status flag. Needs more observations "
              "(e.g. a known-suspended security) before mapping.",
    "quantity": "Present in tradeSummary. Confirmed NOT to be the day's share volume (see "
                "share_volume fix above) — appears to be the most recent single trade's size, "
                "but this is inferred from one observation, not confirmed. Left unmapped.",
    # NOTE: "open" was previously listed here as unmapped/under-investigation. It has since
    # been CONFIRMED (see open_price in TRADE_SUMMARY_FIELD_CANDIDATES above) and moved out
    # of this dict. Kept as a comment, not a dict entry, so it doesn't appear as if still
    # unmapped — this line is the historical record of that resolution.
}


@dataclass
class MappingResult:
    fields: dict
    notes: dict


def _find_candidates_present(source: dict, candidates: list) -> list:
    return [(name, source[name]) for name in candidates if name in source]


def _map_generic(source, field_candidates: dict, not_found_error: Optional[str] = None) -> MappingResult:
    if not isinstance(source, dict):
        return MappingResult(
            fields={},
            notes={
                "found": {}, "expected_but_missing": list(field_candidates.keys()),
                "unexpected_fields": {}, "multiple_candidates_present": {},
                **({"error": not_found_error} if not_found_error else
                   {"error": f"Expected a dict, got {type(source).__name__}"}),
            },
        )

    fields_out = {}
    found = {}
    missing = []
    multiple_candidates = {}
    known_source_keys = set()

    for our_field, candidates in field_candidates.items():
        present = _find_candidates_present(source, candidates)
        if not present:
            missing.append(our_field)
            continue

        for name, _ in present:
            known_source_keys.add(name)

        if len(present) > 1:
            multiple_candidates[our_field] = {name: value for name, value in present}
            values = {v for _, v in present}
            if len(values) > 1:
                multiple_candidates[our_field]["_values_disagree"] = True

        chosen_name, chosen_value = present[0]
        fields_out[our_field] = chosen_value
        found[our_field] = chosen_name

    unexpected_fields = {k: v for k, v in source.items() if k not in known_source_keys}

    return MappingResult(
        fields=fields_out,
        notes={
            "found": found,
            "expected_but_missing": missing,
            "unexpected_fields": unexpected_fields,
            "multiple_candidates_present": multiple_candidates,
        },
    )


def _select_best_nested_dict(body: dict, field_candidates: dict):
    """
    Scores every top-level nested dict value in `body` by how many of our
    candidate field names it actually contains, and returns the
    highest-scoring key. This replaces the old "pick the first nested dict"
    positional guess, which a real response proved wrong (it has FOUR
    nested blocks, only one of which is data-bearing).

    Returns (chosen_key_or_None, {key: score, ...}) — the full score map is
    always returned so ties/near-ties are visible, not hidden.
    """
    all_candidate_names = set()
    for cands in field_candidates.values():
        all_candidate_names.update(cands)

    scores = {}
    for key, value in body.items():
        if isinstance(value, dict):
            scores[key] = sum(1 for name in all_candidate_names if name in value)

    if not scores or max(scores.values()) == 0:
        return None, scores

    best_score = max(scores.values())
    best_keys = [k for k, v in scores.items() if v == best_score]
    return best_keys[0], scores


def map_company_info_summary(body: Optional[dict]) -> MappingResult:
    if not isinstance(body, dict):
        return _map_generic(body, COMPANY_INFO_FIELD_CANDIDATES)

    top_level_hit = any(
        any(c in body for c in cands) for cands in COMPANY_INFO_FIELD_CANDIDATES.values()
    )
    if top_level_hit:
        result = _map_generic(body, COMPANY_INFO_FIELD_CANDIDATES)
        result.notes["unwrapped_into_key"] = None
        result.notes["unwrap_scores"] = None
        return result

    chosen_key, scores = _select_best_nested_dict(body, COMPANY_INFO_FIELD_CANDIDATES)

    if chosen_key is None:
        result = _map_generic({}, COMPANY_INFO_FIELD_CANDIDATES)
        result.notes["unwrapped_into_key"] = None
        result.notes["unwrap_scores"] = scores
        result.notes["error"] = ("No nested block matched any expected field by name — could not "
                                  "identify a data-bearing key. Full body preserved in raw_payload.")
        return result

    result = _map_generic(body[chosen_key], COMPANY_INFO_FIELD_CANDIDATES)
    result.notes["unwrapped_into_key"] = chosen_key
    result.notes["unwrap_scores"] = scores

    tied = [k for k, v in scores.items() if v == scores[chosen_key] and v > 0]
    if len(tied) > 1:
        result.notes["unwrap_ambiguous_candidates"] = tied
    return result


def map_trade_summary_row(row: Optional[dict]) -> MappingResult:
    return _map_generic(row, TRADE_SUMMARY_FIELD_CANDIDATES,
                         not_found_error="Symbol not found in tradeSummary response")


def build_raw_observation(
    *,
    company_info_result: MappingResult,
    trade_summary_result: MappingResult,
    capture_window: str,
) -> dict:
    """
    Combines both mapped results into one raw_market_observations row.

    Invariants enforced here, verified by tests:
    - post_open_price is NEVER populated for any window other than
      'post_open', and is populated from last_traded_price — it is
      UNCHANGED by the addition of open_price below, never renamed, never
      merged, never repurposed.
    - open_price is populated from tradeSummary's "open" field, independent
      of capture_window — CSE's own designated opening price is a property
      of the trading day, not of when we happened to poll it. Semantic
      definition (established by dedicated investigation, not assumed):
      a session-scoped reference price, available at/after market open,
      empirically fixed for the remainder of the session in 15/15
      symbol-sessions observed (2-day daily study + a 20-min post-open
      burst on 2026-09-07). Functions as CSE's designated daily opening
      price — NOT confirmed to be the literal first executed trade or a
      specific opening mechanism (call auction, etc.).
    - Nothing in this function ever conflates open_price with post_open_price.
    - Cross-source comparison is generated for every field confirmed to
      exist in BOTH endpoints (CROSS_CHECK_FIELDS) — not just
      last_traded_price as in an earlier version — so a disagreement in
      closing_price, market_cap, or turnover is equally visible, never
      silently resolved by picking one value with no record of the other.
      (open_price is tradeSummary-only, so it has no cross-source check.)
    """
    merged = {}

    for field in ["last_traded_price", "closing_price", "market_cap"]:
        merged[field] = company_info_result.fields.get(field)
    # turnover/high/low/share_volume/trade_count/open_price only come from
    # tradeSummary currently (companyInfoSummery's candidate list doesn't
    # map them) — authoritative-source question for turnover specifically
    # is still being investigated (see investigate_open_price.py's
    # cross-endpoint logging).
    merged["turnover"] = trade_summary_result.fields.get("turnover")
    merged["high"] = trade_summary_result.fields.get("high")
    merged["low"] = trade_summary_result.fields.get("low")
    merged["share_volume"] = trade_summary_result.fields.get("share_volume")
    merged["trade_count"] = trade_summary_result.fields.get("trade_count")
    merged["open_price"] = trade_summary_result.fields.get("open_price")
    merged["last_traded_date"] = company_info_result.fields.get("last_traded_date")
    merged["foreign_holding"] = company_info_result.fields.get("foreign_holding")

    # If companyInfoSummery didn't have last_traded_price/closing_price/
    # market_cap for some reason, fall back to tradeSummary's value (both
    # confirmed to carry these fields too) rather than leaving it null
    # unnecessarily.
    for field in ["last_traded_price", "closing_price", "market_cap"]:
        if merged[field] is None:
            merged[field] = trade_summary_result.fields.get(field)

    cross_check = {}
    for field in CROSS_CHECK_FIELDS:
        ci_val = company_info_result.fields.get(field)
        ts_val = trade_summary_result.fields.get(field)
        if ci_val is not None and ts_val is not None:
            cross_check[field] = {
                "companyInfoSummery_value": ci_val,
                "tradeSummary_value": ts_val,
                "agree_exactly": ci_val == ts_val,
            }
    merged["cross_source_comparison"] = cross_check if cross_check else None

    if capture_window == "post_open":
        merged["post_open_price"] = merged["last_traded_price"]
    else:
        merged["post_open_price"] = None

    return merged
