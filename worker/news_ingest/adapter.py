"""
§4.1: the adapter interface. A source is an adapter plus its governance record; every request an adapter makes goes
through the governed transport it is given, so the host rules, robots, budget, spacing and the ledger apply to it.

  list_new(since)   -> [reference]          references the source lists (the caller drops the known ones)
  fetch(reference)  -> RawItem or None      None: the attempt failed (recorded in the ledger; not an absence)
  refetch(reference)-> RawItem, Gone or None

`SyntheticAdapter` reads a synthetic JSON format from a scripted fake sender, for tests. The first real source's
adapter is `gdelt.GdeltDocAdapter` (NS-8, C-0122); it runs only once the owner has signed its record.
"""
import hashlib
import json
from abc import ABC, abstractmethod
from datetime import datetime

from .articles import Gone, RawItem


class Adapter(ABC):
    source_id: str

    @abstractmethod
    def list_new(self, since=None):
        ...

    @abstractmethod
    def fetch(self, reference):
        ...

    @abstractmethod
    def refetch(self, reference):
        ...


class SyntheticAdapter(Adapter):
    """Listing: {"items": [{"ref", "url"}]}. Item: {"title", "published_at" (ISO or date or null), "language",
    "text", "removed" (optional, the source's own removal signal)}."""

    def __init__(self, transport, listing_url):
        self.transport, self.listing_url = transport, listing_url
        self.source_id = transport.record.source_id
        self._urls = {}

    def list_new(self, since=None):
        r = self.transport.get(self.listing_url, "listing")
        if r.outcome != "ok":
            return []
        items = json.loads(r.response.body.decode("utf-8"))["items"]
        self._urls.update({i["ref"]: i["url"] for i in items})
        return [i["ref"] for i in items]

    def _get(self, reference, purpose):
        url = self._urls[reference]
        r = self.transport.get(url, purpose)
        now = self.transport.clock()
        if r.outcome in ("http_404", "http_410"):
            return Gone(reference, now, int(r.outcome[5:]))
        if r.outcome != "ok":
            return None
        body = r.response.body
        doc = json.loads(body.decode("utf-8"))
        if doc.get("removed"):
            return Gone(reference, now, None, explicit=True)
        pub, prec = doc.get("published_at"), "none"
        if pub:
            if "T" in pub:
                pub, prec = datetime.fromisoformat(pub), "instant"
            else:
                pub, prec = datetime.fromisoformat(pub + "T00:00:00+00:00"), "date"
        return RawItem(reference, r.url, doc["title"], pub, prec, doc.get("language", "en"),
                       hashlib.sha256(body).hexdigest(), doc.get("text", ""), now)

    def fetch(self, reference):
        item = self._get(reference, "article")
        return item if isinstance(item, RawItem) else None

    def refetch(self, reference):
        return self._get(reference, "refetch")
