"""
NS-8: an adapter for a GDELT DOC 2.0-shaped article-list API, metadata only (C-0121: P6-R2 `metadata_only`).

The host is never in code: it is the governance record's single allowed host, and every request goes through the
governed transport (host rules, robots, spacing, budget, the ledger). The adapter asks only the list endpoint; it
**never requests a publisher's page**, so the publishers' own sites are never contacted. An article is what the
listing says about it: its URL, title, language and the time the service first saw it.

  list_new(since)   one listing request: `?query=<q>&mode=artlist&format=json&maxrecords=<n>&sort=datedesc`
                    (+ `startdatetime=YYYYMMDDHHMMSS` in UTC when `since` is given)
  fetch(reference)  the item from the latest listing, with no request
  refetch(...)      None, with no request: the service gives no evidence of a withdrawal, so none is ever inferred.
                    `REFETCH = False` is a requirement on the future ingest command: plan no re-fetch for this
                    adapter (a planned refetch would record a failure). To catch a changed title, that command
                    re-`fetch`es known references that are listed again (no request) and lets `ingest` decide.

Listing JSON: {"articles": [{"url", "title", "seendate": "YYYYMMDDTHHMMSSZ", "domain", "language", "sourcecountry"}]}.
- `published_at` is the service's first-seen time (`seendate`), an upper bound on publication, precision `instant`.
  Availability is the transport's observation time, as for every fetched article.
- The reference is `u` + the SHA-256 of the article URL (stable across listings; the URL itself can exceed limits).
- `body_sha256` is the hash of the item's canonical JSON; `extracted_text` is empty (metadata only).
- Malformed entries (no title or URL, a bad `seendate`, a URL that will not parse, text that cannot be stored) are
  skipped and counted (`malformed`), never raised. An entry whose URL names a CSE host anywhere, look-alikes
  included (G-1, C-0121), is dropped and counted (`dropped_cse`). A repeated URL keeps its first (newest) entry
  (`duplicates`). A full listing sets `truncated`.
- Unmapped languages are `und` (not processed: §5.1).
- Checked against GDELT's documentation (C-0124): the endpoint, `mode`, `format`, `sort`, `maxrecords` (75 by default,
  at most 250), the `sourcecountry:`/`sourcelang:` operators, and `startdatetime` (`YYYYMMDDHHMMSS`, within the
  last 3 months; an older `since` is raised to just inside the window). Not documented there: the JSON field
  names, which the first live listing is inspected for before ingestion.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from . import hosts
from .adapter import Adapter
from .articles import RawItem

MAX_RECORDS = 250
MAX_TITLE = 1000
WINDOW = timedelta(days=89)                # the API's rolling window is 3 months (its documentation)
LANGUAGES = {"english": "en", "sinhala": "si", "sinhalese": "si", "tamil": "ta"}   # anything else: "und"
DEFAULT_QUERY = "sourcecountry:srilanka sourcelang:english"


def parse_seendate(s):
    """`YYYYMMDDTHHMMSSZ` (UTC) to an aware datetime; ValueError otherwise."""
    if not isinstance(s, str) or len(s) != 16 or not s.isascii() or s[8] != "T" or s[15] != "Z":
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
        self.duplicates = 0
        self.truncated = False

    def listing_url(self, since=None):
        q = {"query": self.query, "mode": "artlist", "format": "json", "maxrecords": str(self.max_records),
             "sort": "datedesc"}
        if since is not None:
            if since.tzinfo is None:
                raise ValueError("since has a time zone")
            floor = self.transport.clock() - WINDOW + timedelta(hours=1)   # the API refuses a start older than 3 months
            q["startdatetime"] = max(since, floor).astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{self.base}?{urlencode(q)}"

    def _entry(self, e):
        """("ok", reference, item), ("cse",) or ("malformed",) for one listing entry. Nothing in an entry can raise
        out of here and lose the rest of the listing (NS-8 audit Major 1)."""
        try:
            if not isinstance(e, dict):
                return ("malformed",)
            url, title, seen = e.get("url"), e.get("title"), e.get("seendate")
            if not (isinstance(url, str) and isinstance(title, str)):
                return ("malformed",)
            title = title.strip()
            if not title or len(title) > MAX_TITLE or len(url) > 4096:
                return ("malformed",)
            url.encode("utf-8"), title.encode("utf-8")        # a lone surrogate cannot be stored: malformed
            seen_at = parse_seendate(seen)
            parts = urlsplit(url)
            hostname = parts.hostname
            if parts.scheme not in ("http", "https") or not hostname:
                return ("malformed",)
            if hosts.is_cse_host(hostname) or hosts.carries_cse(url):
                return ("cse",)
            lang = e.get("language")
            lang = LANGUAGES.get(lang.strip().lower(), "und") if isinstance(lang, str) else "und"
            item = {"url": url, "title": title, "seendate": seen, "seen_at": seen_at, "language": lang,
                    "domain": e.get("domain") if isinstance(e.get("domain"), str) else None,
                    "sourcecountry": e.get("sourcecountry") if isinstance(e.get("sourcecountry"), str) else None}
            return ("ok", reference_of(url), item)
        except (ValueError, TypeError, UnicodeError, AttributeError, OverflowError):
            return ("malformed",)

    def list_new(self, since=None):
        """References in the listing, newest first. The previous listing's items are forgotten (the cache is one
        listing). `truncated` is set when the listing is full: older items since `since` may be missing, so the
        caller narrows the window or overlaps the next `since` (deduplication is by reference)."""
        self._items = {}
        self.truncated = False
        r = self.transport.get(self.listing_url(since), "listing")
        if r.outcome != "ok":
            return []
        try:
            doc = json.loads(r.response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            self.malformed += 1
            return []
        entries = doc.get("articles", []) if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            self.malformed += 1
            return []
        self.truncated = len(entries) >= self.max_records
        now = self.transport.clock()
        refs = []
        for e in entries:
            got = self._entry(e)
            if got[0] == "malformed":
                self.malformed += 1
            elif got[0] == "cse":
                self.dropped_cse += 1
            elif got[1] in self._items:                  # the first (newest) occurrence wins
                self.duplicates += 1
            else:
                got[2]["observed_at"] = now
                self._items[got[1]] = got[2]
                refs.append(got[1])
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
