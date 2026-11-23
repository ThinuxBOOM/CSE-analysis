"""
NS-8: an adapter for a GDELT DOC 2.0-shaped article-list API, metadata only (C-0121: P6-R2 `metadata_only`).

The host is never in code: it is the governance record's single allowed host, and every request goes through the
governed transport (host rules, robots, spacing, budget, the ledger). The adapter asks only the list endpoint; it
**never requests a publisher's page**, so the publishers' own sites are never contacted. An article is what the
listing says about it: its URL, title, language and the time the service first saw it.

  list_new(since)   one listing request: `?query=<q>&mode=artlist&format=json&maxrecords=<n>&sort=datedesc`
                    (+ `startdatetime=YYYYMMDDHHMMSS` in UTC when `since` is given)
  fetch(reference)  the item from the latest listing, with no request
  refetch(...)      None, with no request: the service gives no evidence of a withdrawal, so none is ever inferred
                    (`REFETCH = False`; an ingest command plans no re-fetch for this adapter)

Listing JSON: {"articles": [{"url", "title", "seendate": "YYYYMMDDTHHMMSSZ", "domain", "language", "sourcecountry"}]}.
- `published_at` is the service's first-seen time (`seendate`), an upper bound on publication, precision `instant`.
  Availability is the transport's observation time, as for every fetched article.
- The reference is `u` + the SHA-256 of the article URL (stable across listings; the URL itself can exceed limits).
- `body_sha256` is the hash of the item's canonical JSON; `extracted_text` is empty (metadata only).
- Malformed entries are skipped and counted (`malformed`), never raised. An entry whose URL is not http(s), or that
  names a CSE host anywhere (G-1, C-0121), is dropped and counted (`dropped_cse`).
"""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from . import hosts
from .adapter import Adapter
from .articles import RawItem

MAX_RECORDS = 250
LANGUAGES = {"english": "en", "sinhala": "si", "sinhalese": "si", "tamil": "ta"}
DEFAULT_QUERY = "sourcecountry:srilanka sourcelang:english"


def parse_seendate(s):
    """`YYYYMMDDTHHMMSSZ` (UTC) to an aware datetime; ValueError otherwise."""
    if not isinstance(s, str) or len(s) != 16 or s[8] != "T" or s[15] != "Z":
        raise ValueError(f"seendate {s!r}")
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def reference_of(url):
    return "u" + hashlib.sha256(url.encode("utf-8")).hexdigest()


class GdeltDocAdapter(Adapter):
    REFETCH = False

    def __init__(self, transport, query=DEFAULT_QUERY, max_records=MAX_RECORDS, path="/api/v2/doc/doc"):
        rec = transport.record
        if len(rec.allowed_hosts) != 1:
            raise ValueError("the record names exactly one host: the API's")
        if rec.storage_level != "metadata_only":
            raise ValueError("this adapter is metadata only (C-0121)")
        if not 1 <= int(max_records) <= MAX_RECORDS:
            raise ValueError(f"max_records is 1 to {MAX_RECORDS}")
        if not query or any(c in query for c in "\r\n"):
            raise ValueError("a one-line query")
        self.transport, self.query, self.max_records = transport, query, int(max_records)
        self.base = f"https://{rec.allowed_hosts[0]}{path}"
        self.source_id = rec.source_id
        self._items = {}
        self.malformed = 0
        self.dropped_cse = 0

    def listing_url(self, since=None):
        q = {"query": self.query, "mode": "artlist", "format": "json", "maxrecords": str(self.max_records),
             "sort": "datedesc"}
        if since is not None:
            if since.tzinfo is None:
                raise ValueError("since has a time zone")
            q["startdatetime"] = since.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{self.base}?{urlencode(q)}"

    def _entry(self, e):
        """(reference, item dict) or None for a malformed entry; raises nothing."""
        if not isinstance(e, dict):
            return None
        url, title, seen = e.get("url"), e.get("title"), e.get("seendate")
        if not (isinstance(url, str) and isinstance(title, str) and title.strip()) or len(url) > 4096:
            return None
        try:
            seen_at = parse_seendate(seen)
        except ValueError:
            return None
        lang = e.get("language")
        lang = LANGUAGES.get(lang.strip().lower(), lang.strip().lower()[:8]) if isinstance(lang, str) else "und"
        return reference_of(url), {"url": url, "title": title.strip(), "seendate": seen, "seen_at": seen_at,
                                   "language": lang or "und", "domain": e.get("domain"),
                                   "sourcecountry": e.get("sourcecountry")}

    def list_new(self, since=None):
        r = self.transport.get(self.listing_url(since), "listing")
        if r.outcome != "ok":
            return []
        try:
            doc = json.loads(r.response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.malformed += 1
            return []
        entries = doc.get("articles", []) if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            self.malformed += 1
            return []
        refs = []
        for e in entries:
            got = self._entry(e)
            if got is None:
                self.malformed += 1
                continue
            ref, item = got
            parts = urlsplit(item["url"])
            if parts.scheme not in ("http", "https") or not parts.hostname:
                self.malformed += 1
                continue
            if hosts.is_cse_host(parts.hostname) or hosts.carries_cse(item["url"]):
                self.dropped_cse += 1
                continue
            item["observed_at"] = self.transport.clock()
            self._items[ref] = item
            if ref not in refs:
                refs.append(ref)
        return refs

    def fetch(self, reference):
        i = self._items.get(reference)
        if i is None:
            return None
        canon = json.dumps({k: i[k] for k in ("url", "title", "seendate", "language", "domain", "sourcecountry")},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return RawItem(reference, i["url"], i["title"], i["seen_at"], "instant", i["language"],
                       hashlib.sha256(canon).hexdigest(), "", i["observed_at"])

    def refetch(self, reference):
        return None
