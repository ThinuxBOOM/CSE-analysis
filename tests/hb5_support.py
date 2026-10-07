"""
Support for the HB-5 PostgreSQL tests (not a test module). Nothing here opens a socket.

It builds on HB-4's environment (tests/hb4_support.py), which reaches every frozen stage through its own code:
  - the security master through P2's own capture, from P2's scripted fake CSE (test evidence only, never HB-P1);
  - the owner's arming through HB-1's owner path;
  - discovery, the IE-2 identity pass and the plan's IE-4 closure pass through HB-3's own slices and passes; here the
    COMB.N0000 listing names the filings marked `listed`, with COMB's real secId 369, so their issuer link is
    admissible ('both': listing and the path prefix 369), while an unlisted filing with prefix 369 rests on the path
    prefix alone and prefix 999 names no issuer at all;
  - documents through HB-4's own slices from a scripted cdn.cse.lk. F3 and F4 read a scripted text and word layer
    chosen per filing by its KIND, recognised from the PDF's own marker (hb4_support.pdf_for embeds the filing id).

The kinds and where the funnel stops them (each checked by the tests, not assumed):
    facts         a profit-or-loss statement for 2024 / 2023, printed in LKR millions -> every candidate eligible
    late_periods  the same statement dated 2025-12-31 / 2025-06-30, after the upload date -> F6.1 refuses all (7)
    no_currency   the same statement in "millions" with no currency -> normalisation required, none admitted (8)
    unmapped      rows outside the F5 vocabulary -> F4 extracts, F5 builds no candidate (6)
    no_text       the statement's words, but no text layer -> F3 unreadable (4)
    prose         prose only -> F4 finds no statement (5)
    text_error    the text extraction raises TextExtractionError -> consumer_failed (4)
    words_error   the word extraction raises WordExtractionError -> consumer_failed (5)
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import hb4_support as H
from test_statement_extraction import L, R, doc, page
from worker import document_text, pdf_words, statement_extraction as se
from worker.backfill_discovery import discovery, identity
from worker.backfill_discovery.discovery import DiscoverySlice, create_plan_items
from worker.financial_backfill import keys, store

COLOMBO = timezone(timedelta(hours=5, minutes=30))
COMB = "COMB.N0000"
COMB_SEC_ID = 369                                             # tests/fixtures/multi_company: COMB's real secId
JKH = "JKH.N0000"
SEC_IDS = {COMB: COMB_SEC_ID, JKH: 508}                       # the real secIds of the fixtures' companyInfoSummery
UNKNOWN_SEC_ID = 999                                          # no security, so no issuer
TEXT_EXTRACTOR = "pdftotext 24.02.0 (poppler) -layout"
FACT_LABELS = (("Revenue", "62,085", "58,001"), ("Cost of sales", "(40,000)", "(38,000)"),
               ("Gross profit", "22,085", "20,001"), ("Profit before income tax", "14,616", "13,000"),
               ("Income tax expense", "(4,000)", "(3,000)"), ("Profit for the period", "10,616", "10,000"))
UNMAPPED_LABELS = (("Foo bar", "1", "2"), ("Baz qux", "3", "4"))
KINDS = ("facts", "late_periods", "no_currency", "unmapped", "no_text", "prose", "text_error", "words_error")


def statement(cur="31.12.2024", prev="31.12.2023", scale="(all amounts in Sri Lanka Rupees millions)",
              labels=FACT_LABELS):
    rows = [(40, [L("STATEMENT OF PROFIT OR LOSS", 40)])]
    if scale:
        rows.append((52, [L(scale, 40)]))
    rows += [(70, [L("12 months ended", 262)]), (82, [R(cur, 300), R(prev, 370)]),
             (94, [R("Audited", 300), R("Audited", 370)])]
    y = 110
    for label, a, b in labels:
        rows.append((y, [L(label, 40), R(a, 300), R(b, 370)]))
        y += 12
    return doc(page(rows))


def text_of(words):
    lines = se.build_lines(words.pages[0])
    return document_text.from_pages(["\n".join(line.rendered for line in lines)], extractor=TEXT_EXTRACTOR)


def layer(kind):
    """(text, words) of a kind; an exception instead of either for the failing kinds."""
    if kind == "facts":
        words = statement()
    elif kind == "late_periods":
        words = statement(cur="31.12.2025", prev="30.06.2025")
    elif kind == "no_currency":
        words = statement(scale="(all amounts in millions)")
    elif kind == "unmapped":
        words = statement(labels=UNMAPPED_LABELS)
    elif kind in ("no_text", "text_error", "words_error"):
        words = statement()
    elif kind == "prose":
        words = doc(page([(40, [L("Chairman's message about the year", 40)]),
                          (60, [L("We thank our shareholders", 40)])]))
    else:
        raise ValueError(kind)
    text = document_text.from_pages([""], extractor=TEXT_EXTRACTOR) if kind == "no_text" else text_of(words)
    if kind == "text_error":
        text = document_text.TextExtractionError("no text layer could be read (scripted)")
    if kind == "words_error":
        words = pdf_words.WordExtractionError("the word layer could not be read (scripted)")
    return text, words


_MARK = re.compile(rb"% filing (\d+)")


def scripted_layers(kinds):
    """F3's extract_text and F4's extract_words for HB-4's Settings: each reads the temporary PDF's own marker."""
    built = {}

    def of(path):
        with open(path, "rb") as f:
            fid = int(_MARK.search(f.read()).group(1))
        if fid not in built:
            built[fid] = layer(kinds[fid])
        return built[fid]

    def extract_text(path):
        text = of(path)[0]
        if isinstance(text, BaseException):
            raise text
        return text

    def extract_words(path):
        words = of(path)[1]
        if isinstance(words, BaseException):
            raise words
        return words
    return extract_text, extract_words


# ------------------------------------------------------------------------------------------------ filings

@dataclass
class Filing:
    fid: int
    kind: str = "facts"
    listed: bool = False                    # in its symbol's listing (an admissible link)
    sec: int = COMB_SEC_ID                  # the path prefix
    symbol: str = COMB                      # the listing that names it, when listed
    day: int = 2                            # March 2025, 10:00 Colombo: the processing order
    path: object = "default"                # None: no document; any other text: that path
    cdn: object = "ok"                      # "ok", an HTTP status, or "skip" (never retrieved)
    dated: bool = True                      # False: the feed gives no upload time (F1 keeps it NULL)

    @property
    def doc_path(self):
        return H.path_of(self.fid, sec=self.sec) if self.path == "default" else self.path

    def feed(self):
        item = H.feed_item(self.fid, self.doc_path, day=self.day)
        return item if self.dated else dict(item, uploadedDate=None)

    def listing(self):
        uploaded = datetime(2025, 3, self.day, 10, 0, tzinfo=COLOMBO)
        return {"id": self.fid, "path": self.doc_path, "manualDate": None,
                "uploadedDate": int(uploaded.timestamp() * 1000), "fileText": "Interim Financial Statements",
                "path2": None, "authorizedDate": None}


def listing_body(filings, sec_id=COMB_SEC_ID):
    return {"reqFinancial": [{"secId": sec_id, "elmId": "1", "data": "x"}], "infoAnnualData": [],
            "infoQuarterlyData": [f.listing() for f in filings], "infoOtherData": [], "infoWebLink": []}


def listing_for(symbol, listed):
    """A symbol's /api/financials body: the listed filings of that symbol, with its own secId."""
    if symbol not in SEC_IDS:
        return H.listing_body()
    return listing_body([f for f in listed if f.symbol == symbol], SEC_IDS[symbol])


def discover(env, filings, listed_extra=()):
    """HB-3 end to end, with each listed filing in its symbol's listing: plan, discovery slices, IE-2 and the plan's
    IE-4 closure pass (its link pass decides every filing's issuer link). The document gate is then open."""
    w = env.conn()
    create_plan_items(w, wall=env.clock.wall())
    listed = [f for f in filings if f.listed] + list(listed_extra)

    def route(url, params):
        if url.endswith("financials"):
            return H.ok_json(listing_for(params["symbol"], listed))
        return H.ok_json({"reqFinancialAnnouncemnets": [f.feed() for f in filings]
                          if params["fromDate"].startswith("2025-03") else []})
    for _ in range(10):
        if discovery.closed(w, discovery.current_plan(w, env.clock.wall())):
            break
        with DiscoverySlice(env.conn(), runtime=env.runtime(route=route)) as ds:
            ds.run()
    assert discovery.closed(w, discovery.current_plan(w, env.clock.wall()))
    identity.identity_pass(w, wall=env.clock.wall())
    identity.closure_pass(w, wall=env.clock.wall())
    return route


def rediscover(env, feed, listed):
    """Discovery slices until the current plan is closed again (after an operator re-queue of a discovery item), with
    the feed answering `feed` (Filing objects or raw feed items) and COMB.N0000's listing naming `listed`."""
    w = env.conn()
    items = [f.feed() if isinstance(f, Filing) else f for f in feed]

    def route(url, params):
        if url.endswith("financials"):
            return H.ok_json(listing_for(params["symbol"], listed))
        return H.ok_json({"reqFinancialAnnouncemnets": items if params["fromDate"].startswith("2025-03") else []})
    for _ in range(10):
        if discovery.closed(w, discovery.current_plan(w, env.clock.wall())):
            break
        with DiscoverySlice(env.conn(), runtime=env.runtime(route=route)) as ds:
            ds.run()
    assert discovery.closed(w, discovery.current_plan(w, env.clock.wall()))


def requeue(w, subject, reason):
    """An explicit operator re-queue (HB-1's action; the reason is mandatory): HB-6's `requeue` command."""
    item = store.item_by_key(w, subject["natural_key"])
    return store.append_event(w, item["id"], "pending", "requeue", reason=reason)


def cdn_routes(filings):
    out = {}
    for f in filings:
        if f.doc_path is None or f.cdn == "skip":
            continue
        out[H.url_of(f.doc_path)] = H.ok_doc(f.fid) if f.cdn == "ok" else (f.cdn, {"Content-Type": "text/plain"}, b"")
    return out


def retrievable(f):
    if f.doc_path is None:
        return False
    try:
        H.f2.resolve_candidates(f.doc_path)
    except H.f2.InvalidPath:
        return False
    return True


def process(env, filings):
    """ONE HB-4 document slice over exactly the retrievable filings not marked cdn='skip'. HB-4 claims in upload
    order, so a skipped filing (dated last) stays unattempted: the slice stops after the others (max_items). Each
    scripted outcome is final at its first claim (a document, a consumer failure, a CDN 404)."""
    todo = [f for f in filings if retrievable(f) and f.cdn != "skip"]
    cdn = H.ScriptedCDN(env.clock, cdn_routes(todo))
    if not todo:
        return cdn
    assert all(s.day > max(f.day for f in todo) for s in filings if s.cdn == "skip"), "a skipped filing sorts last"
    text, words = scripted_layers({f.fid: f.kind for f in filings})
    ds = env.slice(cdn, extract_text=text, extract_words=words)
    with ds:
        ds.run(max_items=len(todo))
    return cdn


def world(env, filings, *, process_documents=True, **arming):
    """The whole upstream: HB-P1 test evidence, the owner's arming, discovery with listings, HB-4's documents."""
    env.capture_master()
    H.arm(env, **dict(dict(slice_max_documents=40, expected_requests={"feed": 2, "listings": 7, "documents": 40}),
                      **arming))
    env.sweep()
    route = discover(env, filings)
    cdn = process(env, filings) if process_documents else None
    return route, cdn


# ------------------------------------------------------------------------------------------------ reading

def item_of(w, fid, path="current"):
    if path == "current":
        path = H.q(w, "select path from report_filings where cse_filing_id = %s", (fid,))[0][0]
    return store.item_by_key(w, keys.document(fid, path)["natural_key"])


def state_of(w, fid):
    it = item_of(w, fid)
    return None if it is None else store.current_state(w, it["id"])["state"]


def runs_of(w, fid):
    return [r[0] for r in H.q(w, "select id::text from financial_extraction_runs where cse_filing_id = %s order by id",
                               (fid,))]


def f6_jobs(w, kind=None):
    sql = "select kind, state from financial_f6_job_state"
    rows = H.q(w, sql + (" where kind = %s" if kind else ""), (kind,) if kind else None)
    return sorted(rows)


def designate(env, configuration_id, note="the owner designates the backfill configuration (HB-5 test)"):
    from worker.financial_truth_store import jobs
    return jobs.designate(env.owner_conn(), configuration_id, note, os_user="tester")


def no_preflight(conn):
    """For the tests that are not about the preflight (each still runs it once on its own elsewhere)."""
    return []


def reader(env):
    """A session with exactly cse_reader's privileges (NOLOGIN: the bootstrap superuser switches to it, as the F6.4 and
    real-data tests do). No role is created or granted anything."""
    c = env.conn("postgres")
    with c.cursor() as cur:
        cur.execute("set role cse_reader")
    c.commit()
    return c


def track_transactions(env, tables, prefix="hb5"):
    """Test-only instrumentation of this throwaway database: the TOP-LEVEL transaction id of every row inserted into
    `tables` (xmin would show the stores' savepoints instead), as HB-4's d1 test records it."""
    su = env.conn("postgres")
    H.q(su, f"create table {prefix}_tx (relname text, txid bigint, seq bigserial)")
    H.q(su, f"create function {prefix}_tx_log() returns trigger language plpgsql as $$ begin insert into {prefix}_tx "
            f"(relname, txid) values (tg_table_name, txid_current()); return new; end $$")
    H.q(su, f"grant insert on {prefix}_tx to cse_worker")
    H.q(su, f"grant usage on sequence {prefix}_tx_seq_seq to cse_worker")
    H.q(su, f"grant execute on function {prefix}_tx_log() to cse_worker")
    for t in tables:
        H.q(su, f"create trigger {prefix}_tx_{t} after insert on {t} for each row execute function {prefix}_tx_log()")
    return lambda: H.q(su, f"select relname, txid from {prefix}_tx order by seq")


def persist_under_other_tuple(env, fid, sha="cd" * 32, word="25.03.0", text_release="25.03.0"):
    """One more F5 run of an existing filing (another document, `sha`) under ANOTHER version tuple: F4's word layer
    from Poppler `word` and F3's text from Poppler `text_release` (F4 supports 24.02.0 and 25.03.0; the backfill is
    armed for 24.02.0 only). Persisted by F5's own stores in _persist's order (F3, its id, the issuer link, the
    candidates), in one transaction, as the real-data validation persists its corpus. Test evidence only."""
    from worker import extract_financial_candidates as f5cli, financial_candidates as f5, report_classification as rc
    from worker.financial_candidates_store import PostgresCandidateStore, classification_id
    from worker.issuer_store import PostgresIssuerStore
    from worker.pdf_words import DocumentWords
    from worker.report_classification_store import PostgresClassificationStore
    base = statement()
    words = DocumentWords(base.pages, f"poppler-pdftotext {word} -bbox-layout")
    lines = se.build_lines(words.pages[0])
    text = document_text.from_pages(["\n".join(line.rendered for line in lines)],
                                    extractor=f"pdftotext {text_release} (poppler) -layout")
    w = env.conn()
    [meta] = f5cli.load_filings_from_db(w, [fid])
    cls = rc.classify(text, meta, cse_filing_id=fid, sha256=sha)
    ext = se.extract_document("unused", cls, filing_id=fid, sha256=sha, layout_text=text,
                              word_extractor=lambda path: words)
    result = f5.attach_timestamps(f5.build(ext, cls), meta, None)
    try:
        PostgresClassificationStore(w).save(cls.to_dict(), 1234)
        cid = classification_id(w, cls.to_dict())
        link = PostgresIssuerStore(w).link_filing(fid)
        _, run_id = PostgresCandidateStore(w).save(result, cid, link)
        w.commit()
    except Exception:
        w.rollback()
        raise
    return run_id


def table_rows(w, tables):
    return {t: H.q(w, f"select count(*) from {t}")[0][0] for t in tables}


F_STAGE_TABLES = ("report_discovery_runs", "report_filings", "report_filing_observations",
                  "report_document_classifications", "financial_extraction_runs", "financial_fact_candidates",
                  "filing_issuer_links", "issuers", "issuer_securities", "issuer_identifier_observations", "companies")
F6_TABLES = ("financial_validation_runs", "financial_candidate_validations", "financial_source_observations",
             "financial_economic_facts", "financial_reconciliation_configurations",
             "financial_reconciliation_batches", "financial_reconciliation_records")
