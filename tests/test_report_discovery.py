"""
Stage F1 — financial filing discovery (metadata only).

Network is mocked at the HTTP layer (requests.post/get/head/request), so the
real cse_client code runs and every outbound call is recorded — that record is
what proves F1 never requests a document. Fixtures are trimmed REAL CSE
responses captured during Stage F0 (tests/fixtures/filings/).

Postgres-store tests run only when F1_TEST_DATABASE_URL points at a scratch
database with migrations 0001-0004 applied (they write rows); otherwise they
are reported as skipped, never as passed.
"""
import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from worker import report_discovery as rd

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "filings")
with open(os.path.join(FIX, "real_feed_response_sample.json")) as f:
    FEED = json.load(f)
with open(os.path.join(FIX, "real_feed_response_sample_ids.json")) as f:
    FEED_IDS = json.load(f)
with open(os.path.join(FIX, "real_financials_COMB_N0000_trimmed.json")) as f:
    LISTING = json.load(f)

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
PERIOD_WORDS = ("period", "quarter", "fiscal", "fy_", "duration")


def _skip(reason):
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    print(f"SKIPPED: {reason}")


class _Resp:
    def __init__(self, status, body=None, text=None):
        self.status_code, self._body, self.text = status, body, text if text is not None else json.dumps(body)
        self.ok = 200 <= status < 400

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeCSE:
    """Routes the two F1 endpoints; records EVERY call made through requests."""

    def __init__(self, feed=None, listings=None):
        self.feed = feed if feed is not None else {}          # (from,to) -> _Resp or body; "*" = default
        self.listings = listings if listings is not None else {}
        self.calls = []

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url, dict(data or {})))
        if url.endswith("/api/getFinancialAnnouncement"):
            r = self.feed.get((data.get("fromDate"), data.get("toDate")), self.feed.get("*", {"reqFinancialAnnouncemnets": []}))
        elif url.endswith("/api/financials"):
            r = self.listings.get(data.get("symbol"), {"apierror": {"status": "BAD_REQUEST"}})
        else:
            raise AssertionError(f"unexpected POST {url}")
        return r if isinstance(r, _Resp) else _Resp(200, copy.deepcopy(r))

    def forbidden(self, method):
        def _f(url, *a, **kw):
            self.calls.append((method, url, None))
            raise AssertionError(f"F1 must not make {method} requests (attempted {url})")
        return _f

    def __enter__(self):
        self._orig = (requests.post, requests.get, requests.head, requests.request, requests.Session.request)
        requests.post = self.post
        requests.get = self.forbidden("GET")
        requests.head = self.forbidden("HEAD")
        requests.request = lambda method, url, *a, **kw: self.forbidden(method.upper())(url)
        requests.Session.request = lambda s, method, url, *a, **kw: self.forbidden(method.upper())(url)
        return self

    def __exit__(self, *exc):
        requests.post, requests.get, requests.head, requests.request, requests.Session.request = self._orig
        return False


def _clock(start=T0):
    t = [start]

    def now():
        t[0] = t[0] + timedelta(seconds=1)
        return t[0]
    return now


def _feed(*items):
    return {"reqFinancialAnnouncemnets": list(items)}


def _item(key):
    return next(x for x in FEED["reqFinancialAnnouncemnets"] if x["id"] == FEED_IDS[key])


def _run_feed(store, fake, frm="2019-01-01", to="2019-01-31", clock=None):
    with fake:
        return rd.discover_feed_window(store, frm, to, now_fn=clock or _clock())


# 1 -----------------------------------------------------------------------------
def test_normal_filing_discovery():
    store, fake = rd.InMemoryFilingStore(), FakeCSE(feed={"*": FEED})
    s = _run_feed(store, fake)
    n = len(FEED["reqFinancialAnnouncemnets"])
    assert s["status"] == "succeeded" and s["rows_returned"] == n and len(store.filings) == n
    item = _item("normal")
    row = store.filings[item["id"]]
    assert row["source_symbol"] == item["symbol"] and row["source_name"] == item["name"]
    assert row["file_text"] == item["fileText"] and row["path"] == item["path"]
    assert row["manual_date_raw"] == item["manualDate"]
    assert row["uploaded_at_raw"] == item["uploadedDate"]
    # feed strings are CSE local time (+05:30), verified against epoch values in F0
    assert row["uploaded_at"] == datetime.strptime(item["uploadedDate"], rd.FEED_DATE_FORMAT).replace(tzinfo=rd.CSE_LOCAL_TZ)
    assert row["company_id"] is None and row["company_resolution"] == "unresolved"
    assert row["source_endpoints"] == ["getFinancialAnnouncement"] and row["source_buckets"] == []
    for key in row:   # F1 must not create period semantics of any kind
        assert not any(w in key for w in PERIOD_WORDS), key
    obs = [o for o in store.observations.values() if o["cse_filing_id"] == item["id"]]
    assert len(obs) == 1 and obs[0]["raw_item"] == item and obs[0]["source_bucket"] == "none"
    print("PASS 1: normal discovery records filings verbatim, no period fields, unresolved company")


# 2 -----------------------------------------------------------------------------
def test_duplicate_cse_filing_id_in_one_response():
    item = _item("normal")
    store, fake = rd.InMemoryFilingStore(), FakeCSE(feed={"*": _feed(item, copy.deepcopy(item))})
    s = _run_feed(store, fake)
    assert len(store.filings) == 1 and len(store.observations) == 1
    assert s["duplicate_ids_in_response"] == {str(item["id"]): "none"}
    assert s["outcomes"] == {"new_filing": 1, "unchanged": 1}
    print("PASS 2: a duplicated id in one response is one filing, one observation, and is counted")


# 3 -----------------------------------------------------------------------------
def test_repeated_discovery_window_is_idempotent():
    store, fake = rd.InMemoryFilingStore(), FakeCSE(feed={"*": FEED})
    clock = _clock()
    _run_feed(store, fake, clock=clock)
    first = copy.deepcopy(store.filings)
    n_obs = len(store.observations)
    s2 = _run_feed(store, fake, clock=clock)
    n = len(FEED["reqFinancialAnnouncemnets"])
    assert s2["outcomes"] == {"unchanged": n} and len(store.filings) == n and len(store.observations) == n_obs
    for fid, row in store.filings.items():
        before = first[fid]
        assert row["first_seen_at"] == before["first_seen_at"] and row["last_seen_at"] > before["last_seen_at"]
        assert row["metadata_changed_at"] is None
        assert {k: v for k, v in row.items() if k not in ("last_seen_at", "last_discovery_run_id")} == \
               {k: v for k, v in before.items() if k not in ("last_seen_at", "last_discovery_run_id")}
    print("PASS 3: re-running the same window adds nothing; only last_seen moves")


# 4 -----------------------------------------------------------------------------
def test_changed_metadata_keeps_original_evidence():
    orig = _item("normal")
    changed = dict(orig, authorizedDate="30 Sep 2019 10:00:00 AM", fileText=orig["fileText"] + " (Revised)")
    store = rd.InMemoryFilingStore()
    clock = _clock()
    _run_feed(store, FakeCSE(feed={"*": _feed(orig)}), clock=clock)
    s = _run_feed(store, FakeCSE(feed={"*": _feed(changed)}), clock=clock)
    assert s["outcomes"] == {"metadata_changed": 1}
    row = store.filings[orig["id"]]
    assert row["file_text"] == changed["fileText"] and row["authorized_at_raw"] == changed["authorizedDate"]
    assert row["metadata_changed_at"] is not None
    versions = [o["raw_item"] for o in store.observations.values() if o["cse_filing_id"] == orig["id"]]
    assert orig in versions and changed in versions and len(versions) == 2
    # reverting to the original is a change of the CURRENT listing (flagged), but the
    # version already exists, so no new observation row is added
    s3 = _run_feed(store, FakeCSE(feed={"*": _feed(orig)}), clock=clock)
    assert s3["outcomes"] == {"metadata_changed": 1}
    assert len([o for o in store.observations.values() if o["cse_filing_id"] == orig["id"]]) == 2
    assert store.filings[orig["id"]]["file_text"] == orig["fileText"]
    print("PASS 4: changed metadata adds a version; the original raw listing is preserved; a revert is detected")


# 5 -----------------------------------------------------------------------------
def test_null_path_is_kept_and_counted():
    item = _item("null_path")
    store = rd.InMemoryFilingStore()
    s = _run_feed(store, FakeCSE(feed={"*": _feed(item)}))
    assert store.filings[item["id"]]["path"] is None and s["missing_path_ids"] == [item["id"]]
    assert s["status"] == "succeeded"   # a filing without a document path is still a filing
    print("PASS 5: null path retained as NULL and reported as missing")


# 6 -----------------------------------------------------------------------------
def test_empty_null_and_odd_path2_kept_verbatim():
    store = rd.InMemoryFilingStore()
    with FakeCSE(listings={"COMB.N0000": LISTING}):
        s = rd.discover_company_listing(store, "COMB.N0000", now_fn=_clock())
    assert s["status"] == "succeeded"
    by_raw = {x["id"]: x["path2"] for k in ("infoAnnualData", "infoQuarterlyData", "infoOtherData") for x in LISTING[k]}
    for fid, raw_path2 in by_raw.items():
        assert store.filings[fid]["path2"] == raw_path2   # None stays None, '' stays '', 'x.' stays 'x.'
    kinds = {("none" if v is None else "empty" if v == "" else "dot" if v.endswith(".") else "file") for v in by_raw.values()}
    assert {"none", "dot", "file"} <= kinds
    print("PASS 6: path2 values (NULL / trailing-dot / real file) stored exactly as received")


# 7 -----------------------------------------------------------------------------
def test_manual_date_1970_placeholder_kept_raw_and_untrusted():
    item = _item("manual_1970")
    null_item = _item("manual_null")
    store = rd.InMemoryFilingStore()
    _run_feed(store, FakeCSE(feed={"*": _feed(item, null_item)}))
    assert item["manualDate"] == -19800000
    assert store.filings[item["id"]]["manual_date_raw"] == -19800000
    assert store.filings[null_item["id"]]["manual_date_raw"] is None
    assert not any(isinstance(v, datetime) and v.year == 1970 for v in store.filings[item["id"]].values())
    print("PASS 7: manualDate -19800000 (1970-01-01 +05:30) stored raw only; never turned into a date")


# 8 -----------------------------------------------------------------------------
def test_delisted_or_unknown_symbol_is_retained_unresolved():
    nest = _item("delisted_nest")
    comb = _item("comb_also_in_listing")
    # even if a company 'COMB' existed, a feed symbol (no class suffix) is never auto-resolved
    store = rd.InMemoryFilingStore(companies_by_ticker={"COMB.N0000": "company-comb", "COMB": "wrong"})
    _run_feed(store, FakeCSE(feed={"*": _feed(nest, comb)}))
    for it in (nest, comb):
        assert store.filings[it["id"]]["company_id"] is None
        assert store.filings[it["id"]]["company_resolution"] == "unresolved"
    # a listing for an unknown symbol keeps its filings unresolved too
    unknown = copy.deepcopy(LISTING)
    with FakeCSE(listings={"GONE.N0000": unknown}):
        rd.discover_company_listing(store, "GONE.N0000", now_fn=_clock())
    assert all(store.filings[x["id"]]["company_resolution"] == "unresolved"
               for x in unknown["infoQuarterlyData"])
    print("PASS 8: delisted / unknown-symbol filings are kept with company_id NULL (unresolved)")


def test_company_resolution_requires_exact_listing_symbol_and_flags_conflicts():
    store = rd.InMemoryFilingStore(companies_by_ticker={"COMB.N0000": "company-comb", "COMB.X0000": "company-combx"})
    with FakeCSE(listings={"COMB.N0000": LISTING, "COMB.X0000": LISTING}):
        rd.discover_company_listing(store, "COMB.N0000", now_fn=_clock())
        fid = LISTING["infoAnnualData"][0]["id"]
        assert store.filings[fid]["company_id"] == "company-comb"
        assert store.filings[fid]["company_resolution"] == "exact_listing_symbol"
        rd.discover_company_listing(store, "COMB.X0000", now_fn=_clock())
    assert store.filings[fid]["company_id"] is None and store.filings[fid]["company_resolution"] == "conflict"
    assert store.filings[fid]["listing_symbols"] == ["COMB.N0000", "COMB.X0000"]
    print("PASS 8b: exact listing-symbol match links a company; two different companies -> conflict, cleared")


# 9 -----------------------------------------------------------------------------
def test_same_filing_through_multiple_buckets_and_sources():
    listing = copy.deepcopy(LISTING)
    dup = copy.deepcopy(listing["infoQuarterlyData"][0])
    listing["infoAnnualData"].append(dup)                      # same id listed in two buckets
    shared = _item("comb_also_in_listing")                      # same id also in the market feed
    store = rd.InMemoryFilingStore()
    clock = _clock()
    _run_feed(store, FakeCSE(feed={"*": _feed(shared)}), clock=clock)
    with FakeCSE(listings={"COMB.N0000": listing}):
        rd.discover_company_listing(store, "COMB.N0000", now_fn=clock)
    row = store.filings[dup["id"]]
    assert sorted(row["source_buckets"]) == ["annual", "quarterly"]
    srow = store.filings[shared["id"]]
    assert sorted(srow["source_endpoints"]) == ["financials", "getFinancialAnnouncement"]
    assert srow["source_symbol"] == shared["symbol"] and srow["listing_symbols"] == ["COMB.N0000"]
    assert len({(o["source_endpoint"], o["source_bucket"]) for o in store.observations.values()
                if o["cse_filing_id"] == dup["id"]}) == 2
    # one logical filing each, however many ways it was seen
    assert len([f for f in store.filings if f == dup["id"]]) == 1
    # re-running both sources changes nothing (no ping-pong between sources)
    with FakeCSE(feed={"*": _feed(shared)}, listings={"COMB.N0000": listing}):
        a = rd.discover_feed_window(store, "2019-01-01", "2019-01-31", now_fn=clock)
        b = rd.discover_company_listing(store, "COMB.N0000", now_fn=clock)
    assert set(a["outcomes"]) == {"unchanged"} and set(b["outcomes"]) == {"unchanged"}
    # fixed precedence: the listing (epoch ms) supplies timestamps; only the feed has name/symbol
    listing_key = next(k for k in srow["current_versions"] if k.startswith("financials|"))
    assert srow["field_sources"]["uploaded_at"] == listing_key
    assert srow["field_sources"]["source_name"] == "getFinancialAnnouncement|none|"
    assert a["cross_source_differences"] == [] and b["cross_source_differences"] == []
    print("PASS 9: one filing across buckets and endpoints; re-runs are stable across sources")


# Source-order determinism ---------------------------------------------------------
def _normalized(store, fid):
    row = store.filings[fid]
    return {c: row[c] for c in rd.NORMALIZED_COLUMNS}


def _both_orders(feed_items, listing_body, symbol="COMB.N0000", companies=None):
    results = []
    for order in (("feed", "listing"), ("listing", "feed")):
        store = rd.InMemoryFilingStore(companies_by_ticker=companies)
        clock = _clock()
        for step in order:
            with FakeCSE(feed={"*": _feed(*feed_items)}, listings={symbol: listing_body}):
                if step == "feed":
                    rd.discover_feed_window(store, "2019-01-01", "2019-12-31", now_fn=clock)
                else:
                    rd.discover_company_listing(store, symbol, now_fn=clock)
        results.append(store)
    return results


def test_source_order_determinism_with_conflicting_metadata():
    shared = _item("comb_also_in_listing")
    listing = copy.deepcopy(LISTING)
    lst_item = next(x for k in rd.LISTING_BUCKETS for x in listing.get(k, []) or [] if x["id"] == shared["id"])
    # make the two endpoints DISAGREE on shared fields
    lst_item["fileText"] = shared["fileText"] + " [listing wording]"
    lst_item["path"] = (shared["path"] or "") + ".alt"
    lst_item["manualDate"] = (shared["manualDate"] or 0) + 86_400_000
    a, b = _both_orders([shared], listing, companies={"COMB.N0000": "company-comb"})
    fid = shared["id"]
    assert _normalized(a, fid) == _normalized(b, fid), "normalised filing depends on ingestion order"
    for fid2 in a.filings:     # every filing, not just the shared one
        assert _normalized(a, fid2) == _normalized(b, fid2), fid2
    row = a.filings[fid]
    # the documented precedence decides, not arrival order
    assert row["file_text"] == lst_item["fileText"] and row["path"] == lst_item["path"]
    assert row["manual_date_raw"] == lst_item["manualDate"]
    assert row["source_name"] == shared["name"] and row["source_symbol"] == shared["symbol"]
    assert row["company_id"] == "company-comb"
    # both sides of the disagreement are kept as evidence
    raws = [o["raw_item"] for o in a.observations.values() if o["cse_filing_id"] == fid]
    assert shared in raws and lst_item in raws
    print("PASS: feed->financials and financials->feed give an identical normalised filing, "
          "even when the endpoints disagree; both versions kept as observations")


def test_bucket_order_determinism_within_one_listing():
    listing_a = copy.deepcopy(LISTING)
    dup = copy.deepcopy(listing_a["infoQuarterlyData"][0])
    dup["fileText"] = "same id, different wording in the annual bucket"
    listing_a["infoAnnualData"].append(dup)
    listing_b = copy.deepcopy(listing_a)       # same content, buckets returned in the other order
    listing_b = {k: listing_b[k] for k in reversed(list(listing_b))}
    sa, sb = rd.InMemoryFilingStore(), rd.InMemoryFilingStore()
    with FakeCSE(listings={"COMB.N0000": listing_a}):
        rd.discover_company_listing(sa, "COMB.N0000", now_fn=_clock())
    with FakeCSE(listings={"COMB.N0000": listing_b}):
        rd.discover_company_listing(sb, "COMB.N0000", now_fn=_clock())
    assert _normalized(sa, dup["id"]) == _normalized(sb, dup["id"])
    assert sa.filings[dup["id"]]["file_text"] == dup["fileText"]      # 'annual' outranks 'quarterly'
    print("PASS: the same filing listed in two buckets normalises identically whatever the bucket order")


def test_identity_one_filing_many_observations():
    shared = _item("comb_also_in_listing")
    listing = copy.deepcopy(LISTING)
    item = next(x for k in rd.LISTING_BUCKETS for x in listing.get(k, []) or [] if x["id"] == shared["id"])
    other = copy.deepcopy(item); other["fileText"] = "variant"
    listing["infoOtherData"].append(other)          # same id, second bucket, different metadata
    store = rd.InMemoryFilingStore(companies_by_ticker={"COMB.N0000": "c1"})
    clock = _clock()
    with FakeCSE(feed={"*": _feed(shared)}, listings={"COMB.N0000": listing, "COMB.X0000": listing}):
        rd.discover_feed_window(store, "2019-01-01", "2019-12-31", now_fn=clock)
        rd.discover_company_listing(store, "COMB.N0000", now_fn=clock)
        rd.discover_company_listing(store, "COMB.X0000", now_fn=clock)
    fid = shared["id"]
    assert list(store.filings).count(fid) == 1                       # exactly one logical filing
    obs = [o for o in store.observations.values() if o["cse_filing_id"] == fid]
    assert len(obs) >= 3                                             # one or more observations
    row = store.filings[fid]
    assert len(row["current_versions"]) == 5   # feed + 2 buckets x 2 listing symbols
    assert row["listing_symbols"] == ["COMB.N0000", "COMB.X0000"]
    # identity never includes bucket, symbol, endpoint or company mapping
    assert set(store.filings) == {x["id"] for k in rd.LISTING_BUCKETS for x in listing.get(k, []) or []} | {fid}
    print("PASS: one cse_filing_id = one filing row; many sources = many observations")


# 10 ----------------------------------------------------------------------------
def test_malformed_optional_metadata_and_rejected_rows():
    base = _item("normal")
    weird = dict(base, id=base["id"] + 1, uploadedDate="not a date", manualDate="abc", fileText=123, authorizedDate=[1])
    no_id = dict(base); no_id.pop("id")
    bad_id = dict(base, id="12x")
    store = rd.InMemoryFilingStore()
    s = _run_feed(store, FakeCSE(feed={"*": _feed(weird, no_id, bad_id, "not-an-object", base)}))
    assert s["status"] == "partial" and len(s["rejected"]) == 3
    assert set(store.filings) == {base["id"], weird["id"]}
    row = store.filings[weird["id"]]
    assert row["uploaded_at"] is None and row["uploaded_at_raw"] == "not a date"
    assert row["manual_date_raw"] is None and row["file_text"] is None and row["authorized_at"] is None
    raw = next(o["raw_item"] for o in store.observations.values() if o["cse_filing_id"] == weird["id"])
    assert raw["manualDate"] == "abc" and raw["fileText"] == 123       # evidence kept verbatim
    assert any(w["cse_filing_id"] == weird["id"] for w in s["warnings"])
    assert store.runs[next(iter(store.runs))]["rows_rejected"] == 3
    print("PASS 10: malformed optional fields kept raw with warnings; id-less rows rejected and reported")


# 11 ----------------------------------------------------------------------------
def test_store_failure_is_isolated_per_filing():
    items = FEED["reqFinancialAnnouncemnets"]
    victim = items[2]["id"]
    store = rd.InMemoryFilingStore(fail_on_ids={victim})
    s = _run_feed(store, FakeCSE(feed={"*": FEED}))
    assert s["status"] == "partial" and [f["cse_filing_id"] for f in s["item_failures"]] == [victim]
    assert victim not in store.filings and len(store.filings) == len(items) - 1
    assert not any(k[0] == victim for k in store.observations)      # no half-written evidence
    store.fail_on_ids.clear()
    s2 = _run_feed(store, FakeCSE(feed={"*": FEED}))
    assert s2["outcomes"] == {"new_filing": 1, "unchanged": len(items) - 1}
    print("PASS 11: one filing's store failure doesn't lose the others; a re-run completes it")


def test_failed_responses_are_categorised_not_empty():
    cases = {"http_failure": _Resp(503, {"x": 1}), "non_json": _Resp(200, None, text="<html>maint</html>"),
             "unexpected_schema": _Resp(200, {"somethingElse": []})}
    for category, resp in cases.items():
        store = rd.InMemoryFilingStore()
        s = _run_feed(store, FakeCSE(feed={"*": resp}))
        assert s["status"] == "failed" and s["failure_category"] == category and not store.filings
    s = _run_feed(rd.InMemoryFilingStore(), FakeCSE(feed={"*": _feed()}))
    assert s["status"] == "succeeded" and s["rows_returned"] == 0     # a real empty window
    with FakeCSE(listings={}):
        ls = rd.discover_company_listing(rd.InMemoryFilingStore(), "NOPE.N0000", now_fn=_clock())
    assert ls["status"] == "failed" and ls["failure_category"] == "unexpected_schema"
    print("PASS 11b: HTTP / non-JSON / unrecognised responses fail loudly; a genuinely empty window succeeds")


# 12 ----------------------------------------------------------------------------
def test_f1_performs_zero_document_downloads():
    fake = FakeCSE(feed={"*": FEED}, listings={"COMB.N0000": LISTING})
    store = rd.InMemoryFilingStore()
    with fake:
        rd.discover(store, from_date="2019-01-01", to_date="2019-03-15", symbols=["COMB.N0000"],
                    chunk_days=31, request_delay_seconds=0, now_fn=_clock())
    assert fake.calls, "expected listing calls"
    for method, url, _ in fake.calls:
        assert method == "POST", (method, url)
        assert url in ("https://www.cse.lk/api/getFinancialAnnouncement", "https://www.cse.lk/api/financials"), url
        assert "cdn.cse.lk" not in url
    assert len(fake.calls) == 4    # 3 monthly feed windows + 1 listing, nothing else
    for module in ("report_discovery.py", "report_filings_store.py", "discover_financial_filings.py"):
        src = open(os.path.join(os.path.dirname(__file__), "..", "worker", module), encoding="utf-8").read()
        assert "cdn.cse.lk" not in src and "cmt/" not in src and "hashlib.sha256(r" not in src, module
    print("PASS 12: F1 made only listing POSTs — no CDN, no GET/HEAD, no document code paths")


def test_date_windows_are_inclusive_and_contiguous():
    w = list(rd.date_windows("2024-01-01", "2024-03-05", 31))
    assert w == [("2024-01-01", "2024-01-31"), ("2024-02-01", "2024-03-02"), ("2024-03-03", "2024-03-05")]
    assert list(rd.date_windows("2024-01-01", "2024-01-01", 31)) == [("2024-01-01", "2024-01-01")]
    print("PASS: feed windows cover the range exactly, no gaps or overlaps")


def test_memory_cli_never_imports_db_and_local_json_is_idempotent():
    import worker
    from worker import discover_financial_filings as cli
    hidden = (sys.modules.pop("worker.db", None), worker.__dict__.pop("db", None),
              sys.modules.pop("worker.report_filings_store", None), worker.__dict__.pop("report_filings_store", None))
    try:
        with tempfile.TemporaryDirectory() as tmp, FakeCSE(feed={"*": FEED}):
            state = os.path.join(tmp, "state.json")
            args = ["--store", "local-json", "--state-file", state, "--from-date", "2019-01-01",
                    "--to-date", "2019-01-31", "--request-delay-seconds", "0"]
            assert cli.main(args + ["--report-file", os.path.join(tmp, "r1.json")]) == 0
            assert cli.main(args + ["--report-file", os.path.join(tmp, "r2.json")]) == 0
            r1 = json.load(open(os.path.join(tmp, "r1.json")))
            r2 = json.load(open(os.path.join(tmp, "r2.json")))
        n = len(FEED["reqFinancialAnnouncemnets"])
        assert r1["totals"]["outcomes"] == {"new_filing": n}
        assert r2["totals"]["outcomes"] == {"unchanged": n}
        assert r1["store_state"]["filings"] == r2["store_state"]["filings"] == n
        assert r1["store_state"]["observations"] == r2["store_state"]["observations"] == n
        assert "worker.db" not in sys.modules and "worker.report_filings_store" not in sys.modules
    finally:
        if hidden[0] is not None:
            sys.modules["worker.db"] = hidden[0]
        if hidden[1] is not None:
            worker.db = hidden[1]
        if hidden[2] is not None:
            sys.modules["worker.report_filings_store"] = hidden[2]
        if hidden[3] is not None:
            worker.report_filings_store = hidden[3]
    print("PASS: CLI memory/local-json mode is DB-free; a second process run of the same window is a no-op")


# --- Postgres store (scratch database only) -------------------------------------

def _pg():
    url = os.environ.get("F1_TEST_DATABASE_URL")
    if not url:
        _skip("Postgres F1 store test (set F1_TEST_DATABASE_URL to a scratch DB with migrations 0001-0004)")
        return None
    import psycopg2
    return psycopg2.connect(url)


def test_postgres_store_idempotency_change_and_failure_isolation():
    conn = _pg()
    if conn is None:
        return
    from worker import report_filings_store as rfs
    base_id = 9_000_000_000 + int(datetime.now().timestamp())      # far outside CSE's id range
    items = [dict(copy.deepcopy(x), id=base_id + i) for i, x in enumerate(FEED["reqFinancialAnnouncemnets"])]
    try:
        store = rfs.PostgresFilingStore(conn)
        clock = _clock(datetime.now(timezone.utc))
        s1 = _run_feed(store, FakeCSE(feed={"*": _feed(*items)}), clock=clock)
        s2 = _run_feed(store, FakeCSE(feed={"*": _feed(*items)}), clock=clock)
        assert s1["outcomes"] == {"new_filing": len(items)} and s2["outcomes"] == {"unchanged": len(items)}

        changed = dict(items[0], fileText="changed title")
        s3 = _run_feed(store, FakeCSE(feed={"*": _feed(changed)}), clock=clock)
        assert s3["outcomes"] == {"metadata_changed": 1}

        # failure after a partial write must roll back only that filing
        orig_apply = store._apply
        victim = base_id + 100

        def failing(cur, obs, run_id, now):
            out = orig_apply(cur, obs, run_id, now)
            if obs.cse_filing_id == victim:
                raise RuntimeError("injected failure after writes")
            return out
        store._apply = failing
        extra = [dict(items[1], id=victim), dict(items[1], id=victim + 1)]
        s4 = _run_feed(store, FakeCSE(feed={"*": _feed(*extra)}), clock=clock)
        assert s4["status"] == "partial" and s4["outcomes"] == {"new_filing": 1}

        with conn.cursor() as cur:
            cur.execute("select count(*) from report_filings where cse_filing_id between %s and %s",
                        (base_id, base_id + 1000))
            assert cur.fetchone()[0] == len(items) + 1
            cur.execute("select count(*) from report_filings where cse_filing_id = %s", (victim,))
            assert cur.fetchone()[0] == 0
            cur.execute("select count(*) from report_filing_observations where cse_filing_id = %s", (items[0]["id"],))
            assert cur.fetchone()[0] == 2
            # verbatim edge values survive a real round trip
            by_key = {k: base_id + i for i, x in enumerate(FEED["reqFinancialAnnouncemnets"])
                      for k, v in FEED_IDS.items() if v == x["id"]}
            cur.execute("select path, manual_date_raw, company_id, company_resolution, source_symbol "
                        "from report_filings where cse_filing_id = %s", (by_key["null_path"],))
            path, _, cid, res, _ = cur.fetchone()
            assert path is None and cid is None and res == "unresolved"
            cur.execute("select manual_date_raw from report_filings where cse_filing_id = %s", (by_key["manual_1970"],))
            assert cur.fetchone()[0] == -19800000
            cur.execute("select manual_date_raw from report_filings where cse_filing_id = %s", (by_key["manual_null"],))
            assert cur.fetchone()[0] is None
            cur.execute("select source_symbol, company_id from report_filings where cse_filing_id = %s",
                        (by_key["delisted_nest"],))
            assert cur.fetchone() == ("NEST", None)
    finally:
        conn.close()
    print("PASS (postgres): idempotent re-run, versioned change, savepoint failure isolation, "
          "NULL path / 1970 manualDate / NEST kept verbatim and unresolved")


def _offset_listing(listing, offset):
    out = copy.deepcopy(listing)
    for k in rd.LISTING_BUCKETS:
        for x in out.get(k) or []:
            x["id"] += offset
    return out


def test_postgres_listing_path2_company_fk_append_only_and_order_determinism():
    conn = _pg()
    if conn is None:
        return
    import psycopg2
    import psycopg2.extras
    from worker import report_filings_store as rfs
    offset = 9_100_000_000 + int(datetime.now().timestamp())
    listing = _offset_listing(LISTING, offset)
    shared_src = _item("comb_also_in_listing")
    shared = dict(copy.deepcopy(shared_src), id=shared_src["id"] + offset)
    ticker = f"ZZF1T{offset}.N0000"          # a real companies row, unique per run
    try:
        with conn.cursor() as cur:
            cur.execute("insert into companies (ticker, company_name) values (%s, 'F1 validation company') "
                        "returning id", (ticker,))
            company_id = str(cur.fetchone()[0])
        conn.commit()

        store = rfs.PostgresFilingStore(conn)
        clock = _clock(datetime.now(timezone.utc))
        with FakeCSE(feed={"*": _feed(shared)}, listings={ticker: listing}):   # order: feed -> listing
            rd.discover_feed_window(store, "2019-01-01", "2019-12-31", now_fn=clock)
            s = rd.discover_company_listing(store, ticker, now_fn=clock)
        assert s["status"] == "succeeded"

        raw_path2 = {x["id"]: x["path2"] for k in rd.LISTING_BUCKETS for x in listing.get(k) or []}
        with conn.cursor() as cur:
            for fid, p2 in raw_path2.items():
                cur.execute("select path2, company_id, company_resolution from report_filings where cse_filing_id = %s", (fid,))
                got_p2, got_cid, res = cur.fetchone()
                assert got_p2 == p2, (fid, got_p2, p2)                 # NULL / 'x.' / file kept exactly
                assert str(got_cid) == company_id and res == "exact_listing_symbol"   # real FK to companies

            # append-only is enforced by the database for the worker role
            for stmt in ("update report_filing_observations set source_bucket = 'x' where cse_filing_id = %s",
                         "delete from report_filing_observations where cse_filing_id = %s"):
                cur.execute("savepoint ao")
                try:
                    cur.execute(stmt, (shared["id"],))
                    raise AssertionError(f"worker role was allowed to run: {stmt}")
                except psycopg2.errors.InsufficientPrivilege:
                    cur.execute("rollback to savepoint ao")
            # CHECK constraints are live
            cur.execute("savepoint ck")
            try:
                cur.execute("update report_filings set company_resolution = 'exact_listing_symbol', company_id = null "
                            "where cse_filing_id = %s", (shared["id"],))
                raise AssertionError("inconsistent company_resolution accepted")
            except psycopg2.errors.CheckViolation:
                cur.execute("rollback to savepoint ck")
        conn.commit()

        # order determinism across stores: Postgres (feed -> listing) vs memory (listing -> feed)
        mem = rd.InMemoryFilingStore(companies_by_ticker={ticker: company_id})
        with FakeCSE(feed={"*": _feed(shared)}, listings={ticker: listing}):
            rd.discover_company_listing(mem, ticker, now_fn=_clock())
            rd.discover_feed_window(mem, "2019-01-01", "2019-12-31", now_fn=_clock())
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for fid in mem.filings:
                cur.execute("select * from report_filings where cse_filing_id = %s", (fid,))
                pg_row = cur.fetchone()
                pg_norm = {c: (str(pg_row[c]) if c == "company_id" and pg_row[c] is not None else pg_row[c])
                           for c in rd.NORMALIZED_COLUMNS}
                mem_norm = {c: mem.filings[fid][c] for c in rd.NORMALIZED_COLUMNS}
                assert pg_norm == mem_norm, (fid, {c: (pg_norm[c], mem_norm[c]) for c in pg_norm if pg_norm[c] != mem_norm[c]})
    finally:
        conn.rollback()
        conn.close()
    print("PASS (postgres): path2 verbatim, company FK resolution, DB-enforced append-only, CHECKs live, "
          "and Postgres(feed->listing) == memory(listing->feed)")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nAll Stage F1 discovery tests passed (Postgres test skipped unless F1_TEST_DATABASE_URL is set).")
