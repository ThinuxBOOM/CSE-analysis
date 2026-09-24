"""
Stage F1: financial filing discovery — metadata only.

Discovers which financial filings CSE lists and records them, verbatim, in
report_filings / report_filing_observations (migration 0004). Hard boundaries:

- It never requests a document. The only network calls are the two listing
  endpoints in cse_client (getFinancialAnnouncement, financials). Document
  retrieval (URL resolution, download, hashing) is Stage F2.
- It never derives a reporting period. `manualDate` is kept raw and untrusted;
  titles are kept verbatim. Periods come from the document itself (Stage F3).
- It does not depend on today's allSecurityCode universe: the feed is queried
  by upload-date window, so delisted/renamed issuers' filings are discovered.
- Company linkage is only recorded from direct evidence (see normalize_filing);
  otherwise a filing stays unresolved rather than guessed.

Identity and idempotency: cse_filing_id alone is the logical filing (one
report_filings row). Endpoint, bucket and symbol identify SOURCES of a filing,
never the filing: one filing has one or more report_filing_observations.
Re-seeing an identical listing entry changes nothing but last_seen; a changed
entry is added as a new observation (earlier versions are kept) and flags
metadata_changed_at. The normalised row is a pure function of each source's
current listing version plus a fixed precedence (see normalize_filing), so it
does not depend on the order in which sources were ingested.

Usage:
    python -m worker.discover_financial_filings ...   (see that module)
"""
import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import cse_client

FEED_ENDPOINT = "getFinancialAnnouncement"
LISTING_ENDPOINT = "financials"
FEED_BUCKET = "none"

# /api/financials list keys -> bucket names. Any other list-valued key is
# reported (not silently ignored) as unrecognised.
LISTING_BUCKETS = {
    "infoAnnualData": "annual",
    "infoQuarterlyData": "quarterly",
    "infoOtherData": "other",
    "infoWebLink": "web_link",
}
# Known non-filing keys in the /api/financials body (verified in F0).
LISTING_NON_FILING_KEYS = {"reqFinancial", "infoCompanyBannerAd"}

# Feed date strings are Sri Lanka local time: '24 Sep 2026 04:02:27 PM'
# matched /api/financials epoch-ms values at exactly +05:30 for 328/328
# filings seen in both sources (F0). Sri Lanka has no DST.
CSE_LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
FEED_DATE_FORMAT = "%d %b %Y %I:%M:%S %p"

# Listing fields that define a filing's metadata version. logoUrl is excluded:
# it is company branding, not filing metadata (the full item is still stored).
HASH_FIELDS = ("id", "path", "path2", "manualDate", "uploadedDate", "authorizedDate",
               "fileText", "name", "symbol")

# Current-value columns on report_filings and the listing key that feeds each.
FIELD_KEYS = {
    "file_text": "fileText",
    "path": "path",
    "path2": "path2",
    "manual_date_raw": "manualDate",
    "uploaded_at": "uploadedDate",
    "authorized_at": "authorizedDate",
    "source_name": "name",
    "source_symbol": "symbol",
}
MAX_DETAIL_ITEMS = 50


@dataclass
class FilingObservation:
    cse_filing_id: int
    source_endpoint: str
    source_bucket: str
    query_symbol: Optional[str]
    raw_item: dict
    metadata_hash: str
    fields: dict            # current-value column -> parsed value (only keys present in the item)
    raw_texts: dict         # uploaded_at_raw / authorized_at_raw
    warnings: list = field(default_factory=list)


class ItemRejected(Exception):
    """A listing entry that cannot be recorded as a filing (no usable id)."""


def metadata_hash(item: dict) -> str:
    subset = {k: item[k] for k in HASH_FIELDS if k in item}
    return hashlib.sha256(json.dumps(subset, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def _parse_timestamp(value, source_endpoint, warnings, label):
    """Returns (parsed datetime or None, raw text or None). Unparseable values
    are kept raw with a warning — never guessed."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        warnings.append(f"{label}: unexpected boolean {value!r}")
        return None, str(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc), str(value)
        except (OverflowError, OSError, ValueError):
            warnings.append(f"{label}: epoch value out of range {value!r}")
            return None, str(value)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), FEED_DATE_FORMAT).replace(tzinfo=CSE_LOCAL_TZ), value
        except ValueError:
            warnings.append(f"{label}: unparseable date string {value!r}")
            return None, value
    warnings.append(f"{label}: unexpected type {type(value).__name__}")
    return None, json.dumps(value, default=str)


def parse_listing_item(item, source_endpoint: str, source_bucket: str,
                       query_symbol: Optional[str] = None) -> FilingObservation:
    if not isinstance(item, dict):
        raise ItemRejected(f"listing entry is {type(item).__name__}, not an object")
    raw_id = item.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
        # Accept a purely numeric string id; anything else cannot identify a filing.
        if isinstance(raw_id, str) and raw_id.strip().isdigit() and int(raw_id) > 0:
            raw_id = int(raw_id)
        else:
            raise ItemRejected(f"missing or invalid filing id {raw_id!r}")

    warnings = []
    fields, raw_texts = {}, {}
    for column, key in FIELD_KEYS.items():
        if key not in item:
            continue
        value = item[key]
        if column in ("uploaded_at", "authorized_at"):
            parsed, raw_text = _parse_timestamp(value, source_endpoint, warnings, key)
            fields[column] = parsed
            raw_texts[f"{column}_raw"] = raw_text
        elif column == "manual_date_raw":
            if value is None or (isinstance(value, int) and not isinstance(value, bool)):
                fields[column] = value
            else:
                warnings.append(f"manualDate: non-integer value {value!r} kept only in raw_item")
                fields[column] = None
        else:
            if value is None or isinstance(value, str):
                fields[column] = value
            else:
                warnings.append(f"{key}: non-string value {value!r} kept only in raw_item")
                fields[column] = None
    return FilingObservation(
        cse_filing_id=raw_id, source_endpoint=source_endpoint, source_bucket=source_bucket,
        query_symbol=query_symbol, raw_item=item, metadata_hash=metadata_hash(item),
        fields=fields, raw_texts=raw_texts, warnings=warnings,
    )


def extract_feed_items(response):
    """Returns (items, failure_category, failure_reason). Distinguishes a real
    empty window from a failed/unrecognised response."""
    if not response.ok:
        return None, "http_failure", f"status={response.status_code} error={response.error}"
    if response.body is None:
        return None, "non_json", f"status={response.status_code} error={response.error}"
    body = response.body
    if isinstance(body, dict) and isinstance(body.get("reqFinancialAnnouncemnets"), list):
        return body["reqFinancialAnnouncemnets"], None, None   # (sic) CSE's key spelling
    return None, "unexpected_schema", f"no reqFinancialAnnouncemnets list; keys={sorted(body) if isinstance(body, dict) else type(body).__name__}"


def extract_listing_buckets(response):
    """Returns ({bucket: items}, unrecognised_list_keys, failure_category, failure_reason)."""
    if not response.ok:
        return None, [], "http_failure", f"status={response.status_code} error={response.error}"
    if response.body is None:
        return None, [], "non_json", f"status={response.status_code} error={response.error}"
    body = response.body
    if not isinstance(body, dict) or not any(k in body for k in LISTING_BUCKETS):
        keys = sorted(body) if isinstance(body, dict) else type(body).__name__
        return None, [], "unexpected_schema", f"no listing buckets; keys={keys}"
    buckets = {}
    for key, bucket in LISTING_BUCKETS.items():
        value = body.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            return None, [], "unexpected_schema", f"{key} is {type(value).__name__}, not a list"
        buckets[bucket] = value
    unrecognised = sorted(k for k, v in body.items()
                          if isinstance(v, list) and k not in LISTING_BUCKETS and k not in LISTING_NON_FILING_KEYS)
    return buckets, unrecognised, None, None


# --- merge logic (pure; shared by every store) -----------------------------------

def _values_equal(a, b):
    # The feed's timestamps have 1-second precision; /api/financials gives epoch
    # milliseconds for the same instant — not a real disagreement.
    if isinstance(a, datetime) and isinstance(b, datetime):
        return abs((a - b).total_seconds()) < 1
    return a == b


# Deterministic normalisation policy ---------------------------------------------
#
# A "source" is (endpoint, bucket, query symbol). Each filing keeps the CURRENT
# listing version per source (report_filings.current_versions: source key ->
# metadata_hash); every version ever seen stays in report_filing_observations.
# The normalised columns are a pure function of that set of current versions:
# for each field, the highest-precedence source whose listing entry CONTAINS the
# field supplies it (its value is taken as-is, including NULL). Arrival order is
# never used, so the same inputs give the same row whatever order sources ran in.
#
# Precedence: /api/financials before the feed - it carries epoch-millisecond
# timestamps (no timezone assumption; the feed's are second-precision local-time
# strings), path2 and the bucket. The feed is the only source of name/symbol.
# Within /api/financials: annual, quarterly, other, web_link; then query symbol
# (alphabetical). F0: path, fileText and manualDate agreed for 328/328 filings
# seen in both sources; any disagreement is reported, never silently dropped.
ENDPOINT_PRECEDENCE = (LISTING_ENDPOINT, FEED_ENDPOINT)
BUCKET_PRECEDENCE = ("annual", "quarterly", "other", "web_link", FEED_BUCKET)

NORMALIZED_COLUMNS = (
    "company_id", "company_resolution", "source_symbol", "source_name", "listing_symbols", "file_text",
    "path", "path2", "manual_date_raw", "uploaded_at", "uploaded_at_raw", "authorized_at",
    "authorized_at_raw", "source_endpoints", "source_buckets", "field_sources", "current_versions",
)
# Discovery bookkeeping: legitimately depends on when things were seen.
BOOKKEEPING_COLUMNS = ("first_seen_at", "last_seen_at", "first_discovery_run_id",
                       "last_discovery_run_id", "metadata_changed_at")


def source_key(endpoint: str, bucket: str, query_symbol: Optional[str]) -> str:
    return f"{endpoint}|{bucket}|{query_symbol or ''}"


def split_source_key(key: str):
    endpoint, bucket, query_symbol = key.split("|", 2)
    return endpoint, bucket, (query_symbol or None)


def _precedence(key: str):
    endpoint, bucket, query_symbol = split_source_key(key)
    e = ENDPOINT_PRECEDENCE.index(endpoint) if endpoint in ENDPOINT_PRECEDENCE else len(ENDPOINT_PRECEDENCE)
    b = BUCKET_PRECEDENCE.index(bucket) if bucket in BUCKET_PRECEDENCE else len(BUCKET_PRECEDENCE)
    return (e, endpoint, b, bucket, query_symbol or "")


def classify_observation(existing: Optional[dict], obs: FilingObservation) -> str:
    """new_filing | new_source | unchanged | metadata_changed - relative to the
    source's CURRENT version (so A -> B -> A is a change back, not 'unchanged')."""
    if existing is None:
        return "new_filing"
    current = (existing.get("current_versions") or {}).get(
        source_key(obs.source_endpoint, obs.source_bucket, obs.query_symbol))
    if current is None:
        return "new_source"
    return "unchanged" if current == obs.metadata_hash else "metadata_changed"


def normalize_filing(current_items: dict, company_ids_by_symbol: dict):
    """
    current_items: {source key -> raw listing entry (its current version)}.
    company_ids_by_symbol: {full symbol -> companies.id or None}.
    Returns (normalised column dict, cross-source differences). Pure and
    order-independent: depends only on the mapping's contents.
    """
    keys = sorted(current_items, key=_precedence)
    parsed = {}
    for k in keys:
        endpoint, bucket, query_symbol = split_source_key(k)
        parsed[k] = parse_listing_item(current_items[k], endpoint, bucket, query_symbol)

    row = {"field_sources": {}}
    differences = []
    for column in FIELD_KEYS:
        providers = [k for k in keys if column in parsed[k].fields]
        if column in ("uploaded_at", "authorized_at"):
            row[f"{column}_raw"] = None
        if not providers:
            row[column] = None
            continue
        chosen = providers[0]
        row[column] = parsed[chosen].fields[column]
        if column in ("uploaded_at", "authorized_at"):
            row[f"{column}_raw"] = parsed[chosen].raw_texts.get(f"{column}_raw")
        row["field_sources"][column] = chosen
        for other in providers[1:]:
            value = parsed[other].fields[column]
            if not _values_equal(row[column], value):
                differences.append({"field": column, "chosen_source": chosen, "chosen": row[column],
                                    "other_source": other, "other": value})

    endpoints, buckets, symbols = set(), set(), set()
    for k in keys:
        endpoint, bucket, query_symbol = split_source_key(k)
        endpoints.add(endpoint)
        if bucket != FEED_BUCKET:
            buckets.add(bucket)
        if query_symbol:
            symbols.add(query_symbol)
    row["source_endpoints"] = sorted(endpoints, key=lambda e: _precedence(f"{e}||"))
    row["source_buckets"] = sorted(buckets, key=lambda b: (BUCKET_PRECEDENCE.index(b) if b in BUCKET_PRECEDENCE else 99, b))
    row["listing_symbols"] = sorted(symbols)

    # Company linkage from direct evidence only: CSE listed the filing under an
    # /api/financials query symbol exactly equal to companies.ticker. Feed symbols
    # (e.g. 'COMB') are never auto-resolved (no security class; F0 showed ids and
    # symbols shifting across restructurings). Two different companies -> conflict.
    linked = {company_ids_by_symbol.get(s) for s in row["listing_symbols"]} - {None}
    if len(linked) == 1:
        row["company_id"], row["company_resolution"] = linked.pop(), "exact_listing_symbol"
    elif len(linked) > 1:
        row["company_id"], row["company_resolution"] = None, "conflict"
    else:
        row["company_id"], row["company_resolution"] = None, "unresolved"
    return row, differences


def build_filing_row(existing: Optional[dict], obs: FilingObservation, outcome: str, current_versions: dict,
                     current_items: dict, company_ids_by_symbol: dict, run_id, now: datetime):
    """Full report_filings row = normalised columns + discovery bookkeeping."""
    normalized, differences = normalize_filing(current_items, company_ids_by_symbol)
    row = {"cse_filing_id": obs.cse_filing_id, **normalized, "current_versions": dict(current_versions)}
    if existing is None:
        row.update(first_seen_at=now, first_discovery_run_id=run_id, metadata_changed_at=None)
    else:
        row.update(first_seen_at=existing["first_seen_at"], first_discovery_run_id=existing["first_discovery_run_id"],
                   metadata_changed_at=existing.get("metadata_changed_at"))
    if outcome == "metadata_changed":
        row["metadata_changed_at"] = now
    row.update(last_seen_at=now, last_discovery_run_id=run_id)
    return row, differences


# --- in-memory store (zero DB: dry runs, tests, local smoke runs) -----------------

class InMemoryFilingStore:
    """Same semantics as PostgresFilingStore, held in memory. Optional JSON
    persistence (save/load) lets a separate process re-run a window against
    the same state. Never touches a database."""

    def __init__(self, companies_by_ticker: Optional[dict] = None, fail_on_ids: Optional[set] = None):
        self.runs = {}
        self.filings = {}
        self.observations = {}      # (id, endpoint, bucket, hash) -> record
        self.companies_by_ticker = dict(companies_by_ticker or {})
        self.fail_on_ids = set(fail_on_ids or ())

    def begin_run(self, source_endpoint, request_params, now):
        run_id = f"run-{len(self.runs) + 1}"
        self.runs[run_id] = {"id": run_id, "source_endpoint": source_endpoint,
                             "request_params": request_params, "started_at": now, "status": "running"}
        return run_id

    def finish_run(self, run_id, summary, now):
        self.runs[run_id].update(summary)
        self.runs[run_id]["finished_at"] = now

    def lookup_company_id(self, ticker):
        return self.companies_by_ticker.get(ticker)

    def apply_observation(self, obs, run_id, now):
        if obs.cse_filing_id in self.fail_on_ids:
            raise RuntimeError(f"injected store failure for filing {obs.cse_filing_id}")
        existing = self.filings.get(obs.cse_filing_id)
        outcome = classify_observation(existing, obs)
        key = source_key(obs.source_endpoint, obs.source_bucket, obs.query_symbol)
        current_versions = dict((existing or {}).get("current_versions") or {})
        current_versions[key] = obs.metadata_hash
        obs_key = (obs.cse_filing_id, obs.source_endpoint, obs.source_bucket, obs.metadata_hash)
        current_items = {}
        for k, h in current_versions.items():
            endpoint, bucket, _ = split_source_key(k)
            current_items[k] = obs.raw_item if k == key else \
                self.observations[(obs.cse_filing_id, endpoint, bucket, h)]["raw_item"]
        symbols = {split_source_key(k)[2] for k in current_versions} - {None}
        companies = {sym: self.lookup_company_id(sym) for sym in symbols}
        row, diffs = build_filing_row(existing, obs, outcome, current_versions, current_items,
                                      companies, run_id, now)
        # both writes only after all computation succeeded: atomic per filing
        self.filings[obs.cse_filing_id] = row
        if obs_key not in self.observations:
            self.observations[obs_key] = {
                "cse_filing_id": obs.cse_filing_id, "discovery_run_id": run_id,
                "source_endpoint": obs.source_endpoint, "source_bucket": obs.source_bucket,
                "query_symbol": obs.query_symbol, "metadata_hash": obs.metadata_hash,
                "raw_item": obs.raw_item, "observed_at": now}
        return outcome, diffs

    def commit(self):
        pass

    def save(self, path):
        def enc(o):
            return o.isoformat() if isinstance(o, datetime) else str(o)
        state = {"runs": list(self.runs.values()), "filings": list(self.filings.values()),
                 "observations": list(self.observations.values())}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, default=enc)

    @classmethod
    def load(cls, path):
        store = cls()
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        ts = ("first_seen_at", "last_seen_at", "metadata_changed_at", "uploaded_at", "authorized_at")
        for r in state["runs"]:
            store.runs[r["id"]] = r
        for row in state["filings"]:
            for k in ts:
                if row.get(k):
                    row[k] = datetime.fromisoformat(row[k])
            store.filings[row["cse_filing_id"]] = row
        for o in state["observations"]:
            store.observations[(o["cse_filing_id"], o["source_endpoint"], o["source_bucket"], o["metadata_hash"])] = o
        return store


# --- orchestration ---------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _ingest(store, run_id, observations, now, summary):
    for obs in observations:
        try:
            outcome, diffs = store.apply_observation(obs, run_id, now)
        except Exception as exc:  # noqa: BLE001 — one item's failure must not lose the rest
            summary["item_failures"].append({"cse_filing_id": obs.cse_filing_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
        summary["cross_source_differences"].extend(
            {"cse_filing_id": obs.cse_filing_id, **d} for d in diffs)
        if obs.warnings:
            summary["warnings"].append({"cse_filing_id": obs.cse_filing_id, "warnings": obs.warnings})


def _new_summary(source_endpoint, request_params):
    return {"source_endpoint": source_endpoint, "request_params": request_params, "status": None,
            "failure_category": None, "failure_reason": None, "http_status": None, "rows_returned": 0,
            "filing_ids": [], "bucket_counts": {}, "duplicate_ids_in_response": {}, "rejected": [],
            "item_failures": [], "outcomes": {}, "cross_source_differences": [], "warnings": [],
            "unrecognised_list_keys": [], "missing_path_ids": []}


def _finish(store, run_id, summary, now):
    if summary["failure_category"]:
        summary["status"] = "failed"
    elif summary["rejected"] or summary["item_failures"]:
        summary["status"] = "partial"
    else:
        summary["status"] = "succeeded"
    outcomes = summary["outcomes"]
    store.finish_run(run_id, {
        "status": summary["status"], "failure_category": summary["failure_category"],
        "http_status": summary["http_status"], "rows_returned": summary["rows_returned"],
        "filings_new": outcomes.get("new_filing", 0),
        "observations_new": sum(v for k, v in outcomes.items() if k != "unchanged"),
        "metadata_changes": outcomes.get("metadata_changed", 0),
        "rows_rejected": len(summary["rejected"]), "item_failures": len(summary["item_failures"]),
        "details": {"failure_reason": summary["failure_reason"],
                    "rejected": summary["rejected"][:MAX_DETAIL_ITEMS],
                    "item_failures": summary["item_failures"][:MAX_DETAIL_ITEMS],
                    "unrecognised_list_keys": summary["unrecognised_list_keys"],
                    "warnings": summary["warnings"][:MAX_DETAIL_ITEMS]},
    }, now)
    store.commit()
    return summary


def _collect(summary, items, source_endpoint, bucket, query_symbol):
    observations, seen = [], {}
    for item in items:
        summary["rows_returned"] += 1
        try:
            obs = parse_listing_item(item, source_endpoint, bucket, query_symbol)
        except ItemRejected as exc:
            summary["rejected"].append({"reason": str(exc), "raw_item": item})
            continue
        key = (obs.cse_filing_id, bucket)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            summary["duplicate_ids_in_response"][str(obs.cse_filing_id)] = bucket
        summary["filing_ids"].append(obs.cse_filing_id)
        summary["bucket_counts"][bucket] = summary["bucket_counts"].get(bucket, 0) + 1
        if not obs.fields.get("path"):
            summary["missing_path_ids"].append(obs.cse_filing_id)
        observations.append(obs)
    return observations


def discover_feed_window(store, from_date: str, to_date: str, now_fn=_utcnow) -> dict:
    """One getFinancialAnnouncement request for [from_date, to_date] (upload date)."""
    params = {"fromDate": from_date, "toDate": to_date}
    summary = _new_summary(FEED_ENDPOINT, params)
    run_id = store.begin_run(FEED_ENDPOINT, params, now_fn())
    response = cse_client.get_financial_announcements(from_date, to_date)
    summary["http_status"] = response.status_code
    items, category, reason = extract_feed_items(response)
    if category:
        summary["failure_category"], summary["failure_reason"] = category, reason
        return _finish(store, run_id, summary, now_fn())
    now = now_fn()
    _ingest(store, run_id, _collect(summary, items, FEED_ENDPOINT, FEED_BUCKET, None), now, summary)
    return _finish(store, run_id, summary, now_fn())


def discover_company_listing(store, symbol: str, now_fn=_utcnow) -> dict:
    """One /api/financials request for a full symbol (e.g. 'COMB.N0000')."""
    params = {"symbol": symbol}
    summary = _new_summary(LISTING_ENDPOINT, params)
    run_id = store.begin_run(LISTING_ENDPOINT, params, now_fn())
    response = cse_client.get_company_financials(symbol)
    summary["http_status"] = response.status_code
    buckets, unrecognised, category, reason = extract_listing_buckets(response)
    summary["unrecognised_list_keys"] = unrecognised
    if category:
        summary["failure_category"], summary["failure_reason"] = category, reason
        return _finish(store, run_id, summary, now_fn())
    now = now_fn()
    observations = []
    for bucket, items in buckets.items():
        observations.extend(_collect(summary, items, LISTING_ENDPOINT, bucket, symbol))
    _ingest(store, run_id, observations, now, summary)
    return _finish(store, run_id, summary, now_fn())


def date_windows(from_date: str, to_date: str, chunk_days: int):
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if end < start:
        raise ValueError(f"to_date {to_date} is before from_date {from_date}")
    while start <= end:
        stop = min(end, start + timedelta(days=chunk_days - 1))
        yield start.isoformat(), stop.isoformat()
        start = stop + timedelta(days=1)


def discover(store, *, from_date=None, to_date=None, symbols=(), chunk_days=31,
             request_delay_seconds=1.0, now_fn=_utcnow, sleep=time.sleep) -> dict:
    """Runs feed windows and/or company listings; returns an aggregate report."""
    started = time.monotonic()
    runs = []
    requests_made = 0
    if from_date or to_date:
        for a, b in date_windows(from_date, to_date, chunk_days):
            if requests_made and request_delay_seconds:
                sleep(request_delay_seconds)
            runs.append(discover_feed_window(store, a, b, now_fn=now_fn))
            requests_made += 1
    for symbol in symbols:
        if requests_made and request_delay_seconds:
            sleep(request_delay_seconds)
        runs.append(discover_company_listing(store, symbol, now_fn=now_fn))
        requests_made += 1
    return aggregate_report(runs, time.monotonic() - started)


def aggregate_report(runs, runtime_seconds) -> dict:
    ids = [i for r in runs for i in r["filing_ids"]]
    requests_per_id = Counter(i for r in runs for i in set(r["filing_ids"]))
    totals = {"requests": len(runs), "rows_returned": sum(r["rows_returned"] for r in runs),
              "unique_filing_ids": len(set(ids)), "bucket_counts": {}, "outcomes": {},
              "missing_path_filings": len({i for r in runs for i in r["missing_path_ids"]}),
              "duplicate_ids_within_a_response": sum(len(r["duplicate_ids_in_response"]) for r in runs),
              "ids_seen_in_more_than_one_request": sum(1 for n in requests_per_id.values() if n > 1),
              "rejected_rows": sum(len(r["rejected"]) for r in runs),
              "item_failures": sum(len(r["item_failures"]) for r in runs),
              "failed_requests": [r["request_params"] for r in runs if r["status"] == "failed"],
              "cross_source_differences": sum(len(r["cross_source_differences"]) for r in runs),
              "runtime_seconds": round(runtime_seconds, 2)}
    for r in runs:
        for k, v in r["bucket_counts"].items():
            totals["bucket_counts"][k] = totals["bucket_counts"].get(k, 0) + v
        for k, v in r["outcomes"].items():
            totals["outcomes"][k] = totals["outcomes"].get(k, 0) + v
    return {"totals": totals, "runs": runs}
