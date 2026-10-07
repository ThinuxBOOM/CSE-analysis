"""
Which issuers may be reconciled (design section 10.3: "Reconcile per issuer, once its in-window document items are all
terminal ... then a final full pass"; section 14.1, HB-S5: "per issuer after its items are terminal").

An in-window filing is judged by the document item of its CURRENT path version, HB-1's natural key document:<filing>:
<SHA-256 of F1's current path>. F5's composition retrieves only F1's current path, so an older path version is never
claimed and never final; HB-4 records it as a class-4 anomaly and it holds nothing back. A filing whose upload date left
W is not in W, so its still-pending item (HB-4: class 3) holds nothing back either. This is HB-U5's current-plan rule,
applied to documents.

A document item is FINAL when it will get no new F5 run without an operator: HB-1's final document states (excluded,
retrieval_failed, consumer_failed, cleanup_failed, failed) and the persisted family (persisted, validated, reconciled,
needs_validation). A filing in W without an item for its current path is not final: planning has not reached it, so,
like a plan item not created yet, it keeps its issuer open.

The issuer of a filing is its CURRENT issuer decision (the latest filing_issuer_links row: what M4 validates against).
A filing without an issuer holds no issuer back, but it holds back the final full pass, which waits for every
in-window filing.
"""
from dataclasses import dataclass

from ..backfill_documents import planning
from ..financial_backfill import keys, states
from .snapshot import bounds, in_window, read_only, rows

PERSISTED = ("persisted", "validated", "reconciled", "needs_validation")
FINAL_DOCUMENT = frozenset(states.FINAL["document"]) | frozenset(PERSISTED)


@dataclass(frozen=True)
class Readiness:
    window: tuple
    filings: dict          # cse_filing_id -> {"issuer_id", "state" (None: no item for the current path), "final"}

    @property
    def issuers(self):
        return sorted({f["issuer_id"] for f in self.filings.values() if f["issuer_id"] is not None})

    def open_filings(self, issuer_id=None):
        return sorted(fid for fid, f in self.filings.items()
                      if not f["final"] and (issuer_id is None or f["issuer_id"] == issuer_id))

    def issuer_ready(self, issuer_id):
        return not self.open_filings(issuer_id)

    @property
    def ready_issuers(self):
        return [i for i in self.issuers if self.issuer_ready(i)]

    @property
    def all_final(self):
        return not self.open_filings()

    def summary(self):
        return {"window": [d.isoformat() for d in self.window], "filings": len(self.filings),
                "open_filings": len(self.open_filings()), "issuers": len(self.issuers),
                "ready_issuers": len(self.ready_issuers), "all_final": self.all_final}


def current_item(filing, items_by_filing):
    """The document item of the filing's current path version, or None."""
    version = keys.path_version(filing["path"])
    found = [i for i in items_by_filing.get(filing["cse_filing_id"], ()) if i["path_sha256"] == version]
    return found[0] if found else None


def evaluate(filings, items, window):
    """Pure: filings [{cse_filing_id, path, uploaded_at, issuer_id}], document items as HB-4's
    planning.document_items returns them."""
    by_filing = {}
    for i in items:
        by_filing.setdefault(i["cse_filing_id"], []).append(i)
    out = {}
    for f in filings:
        if not in_window(f["uploaded_at"], window):
            continue
        item = current_item(f, by_filing)
        state = None if item is None else item["state"]
        out[f["cse_filing_id"]] = {"issuer_id": f["issuer_id"], "state": state,
                                   "final": state is not None and state in FINAL_DOCUMENT}
    return Readiness(tuple(window), out)


FILINGS_SQL = """
    select f.cse_filing_id, f.path, f.uploaded_at, cur.issuer_id::text as issuer_id
      from report_filings f
      left join lateral (select l.issuer_id from filing_issuer_links l where l.cse_filing_id = f.cse_filing_id
                          order by l.id desc limit 1) cur on true
     where f.uploaded_at >= %s and f.uploaded_at < %s
     order by f.cse_filing_id"""


def of(conn, window):
    """Readiness in W now (HB-4's own item read, then the filings and their current decisions)."""
    items = planning.document_items(conn)
    with read_only(conn) as cur:
        filings = rows(cur, FILINGS_SQL, bounds(window))
    return evaluate(filings, items, window)
