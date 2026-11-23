# Phase 6: news and sentiment, runbook

Design: `docs/PHASE6_NEWS_SENTIMENT_DESIGN.md` (revision 3). Implementation: `docs/PHASE6_NS_IMPLEMENTATION.md`.
Migration: `supabase/migrations_pending/0022_news_sentiment.sql`. **Pending, outside the chain**: moving it into the
chain is part of the owner's merge (C-0119).

## Before anything runs: the owner's decisions

Decided on 2026-11-23 (C-0121). The code refuses to run a source without a registered, hash-pinned record.

| ID | Decision |
|---|---|
| P6-R1 | A source qualifies only if it: publishes terms that permit automated access, or offers a licensed feed or API; has robots.txt allowing the paths used; is English-language Sri Lankan business news; and needs no login, key or payment. Preference: a licensed API, then the publisher's RSS feed, then article pages. **The owner names each source and signs its record.** `onward_disclosure` is `"none"`. CSE-derived content in third-party news falls under G-1: a source made mainly of CSE price or market reports is not registered without a G-1 extension |
| P6-R2 | `metadata_only`, unless a source's terms expressly permit full text and the owner chooses `full_text` |
| P6-R3 | The purge procedure below (P2 §12's design). What a source is promised about backups, and any deadline, stay the owner's |
| P6-R6 | The project's own lexicon, `provisional`. Its scores are not used to train, select or promote a model until it is evaluated on real news. A third-party lexicon is the owner's choice, with its licence |
| P6-R8 | `"identification": {"user_agent": "research-news-reader/1 (non-commercial; obeys robots.txt)", "contact": null}`. A contact only if a source requires one: a non-personal mailbox the owner creates |

## Purge (P6-R3)

This follows P2's purge design (`docs/ops/P2_MARKET_CAPTURE.md` §12), for one source. Steps 1 to 5 are the owner
commands `stop-source` and `purge-source` (NS-9):
- `stop-source --source ID --reason TEXT`: the stop, recorded; it holds until `clear-stop`.
- `purge-source --source ID --request TEXT --dry-run`: the whole purge, rolled back; read its manifest first. It
  leaves gaps in row ids (PostgreSQL does not roll back identity numbers), and nothing else.
- A purge (or a dry run) rebuilds every stored snapshot twice in memory while holding the news tables locked: with
  many snapshots it takes a while, and nothing else can read or write news meanwhile. Run it in a quiet window.
- `purge-source --source ID --request TEXT`: steps 3 and 4 in **one transaction**. It refuses a source that is not
  stopped. It prints the manifest: the counts, article version ids and body hashes, the snapshots replaced and what
  replaced them, and the manifest's own SHA-256. Keep that output as the purge record (step 3.4).
1. **Record and stop.** Record the purge request (scope, authorisation, requester, time). Stop the source with
   `stop-source`; it holds until `clear-stop`.
2. **Manifest.** Write the pre-purge manifest: ids, hashes and counts only, never content. Keep it outside the
   purged set.
3. **Database.** As `cse_owner` (through `cse_migrator` and `SET ROLE`), in one transaction:
   1. `ALTER TABLE … DISABLE TRIGGER` on the named tables only.
   2. Delete the source's rows, in dependency order:
      - `ns_fetch_events`, `ns_fetch_attempts`;
      - `ns_article_results`, `ns_article_observations`, `ns_articles`, `ns_archive_imports`;
      - `ns_ingestion_runs`;
      - its `ingestion_started` rows in `ns_source_events`;
      - every snapshot, with its rows, that names the source (active sources, or a story's article id) **or**
        whose rebuild without the source's rows has a different hash. Counts and states count every admitted
        article, so a snapshot can depend on a source it never names (NS-9 audit).
   3. Re-enable the triggers. Keep `ns_sources` and the stop rows, and add a `stopped` row with reason `purged`
      (with the triggers back on, so it is stamped and checked).
   4. Write the purge record as a file in `docs/governance/`: the request, the manifest's hash, the counts deleted,
      the snapshot ids replaced. 0022 has no purge table.
   A source's articles are matched by the exact `<source_id>:` prefix of their ids.
4. **Rebuild and commit** (inside the same transaction, by `purge-source`). Build the deleted snapshots again at the
   same queries, then commit; any failure rolls everything back, the triggers included. They are post-purge reconstructions with new
   ids, no longer what was known at T. Re-pin or invalidate anything that pinned the old ids or hashes, and list
   them in the purge record. The source is absent from them, never shown as zero news.
5. **Check.** Run `preflight` and `verify`.
6. **Local dumps.** Take a fresh post-purge dump, restore-check it, then remove the pre-purge dumps.
7. **Off-site.** What happens depends on the destination, still an open owner decision (LP-R1):
   - `restic forget --prune` with admin credentials held off the server;
   - waiting out an object lock;
   - crypto-shredding.

**The owner's:** what a source is promised about its backups, any residual retention, and any deadline.

**Not purged by the tool, checked by hand:**
- an owner alias's `source_reference`, which the owner wrote and may cite an article;
- another source's fetch-event details.

A rebuilt snapshot keeps the stored snapshot's subjects. An issuer that only the purged source mentioned keeps a
`missing` row: its id stays, but nothing of the article does.

`purge-source` refuses while another session holds the source's lock (an ingest running).

The User-Agent's "non-commercial" is revisited (G-1 item 12) if the project becomes commercial or public.

**The first source:** GDELT's DOC 2.0 API, record `docs/governance/NS-GDELT-DOC.md`, **signed by the owner on
2026-11-23** (C-0125). A record still carrying the draft marker is refused (C-0122). On the laptop after the merge, in this
order: `inspect` (one live listing), `register-source`, `designate`, `start-ingestion`, then schedule `ingest`.

**The ingest command** (`ingest`, NS-10) runs one pass for one registered, started source; `inspect` makes one listing for the owner's check before `start-ingestion`. CSE is never a news
source here (G-1 governs CSE), and the transport refuses CSE's hosts whatever a record says.

## The governance record

A Markdown file in `docs/governance/`, written for people, holding exactly one fenced `ns-source` block of JSON with
these fields:
- `source_id`, `allowed_hosts`, `fetch_on_behalf_hosts`, `storage_level`, `onward_disclosure`;
- `availability_policy`, `identification`, `request_discipline`, and `robots: "obey"`.

`worker/news_ingest/governance.py` describes each field. Registering the source pins the record's SHA-256 and its
allowed hosts. Any later change to the file stops the source until it is registered again.

## Commands (`sudo bash /opt/cse/app/ops/bin/cse-news <command>`)

Worker (`cse-worker`):
- `inspect --record FILE`: for a record **not yet registered** (a registered source is refused: it is then governed
  by its ledger, stop and budget). ONE listing request with an in-memory ledger; nothing is written. It prints the
  JSON field names and the first entries. Run it once **before `register-source`** and check that the fields include
  `url`, `title`, `seendate` and `language`, and that the titles are Sri Lankan English news.
- `ingest --source ID`: one pass. It refuses, in order: a source with no adapter, unregistered, not started,
  stopped, busy (its lock held), or whose record no longer matches its pinned hash and hosts.
  - **The cursor:** it lists from the newest *complete* pass's time minus 6 hours, or from the start. An incomplete
    pass leaves the cursor where it was, so nothing is skipped.
  - **Paging:** while a page is full (250), the next page ends at that page's oldest article, up to 8 pages. A page
    that is not full, or one that reaches back to the cursor, completes the pass.
  - **What it writes:** new articles, title revisions, and a confirmation only for an article not seen for 24 hours.
  - **Scoring:** with the configuration designated now. A version left unscored (none was designated yet) is scored
    when it is listed again. **Designate before `start-ingestion`.**
  - **The day's run row:** `ran` (complete); `partial` (incomplete, malformed entries, or a processing error);
    `failed` (a listing failed or the source stopped, or the pass raised: the row is written first). Its details hold
    the cursor, the pages, the listing outcome and the counts.
  - **Exit codes:** 0 `ran`; 3 `partial` or `failed`; 4 database unavailable; 5 refused.
  - **Cadence and budget:** at most hourly. Each run reads robots.txt and lists at least once, so a normal day is
    about 48 requests against the record's 200. A busy hour pages up to 8 listings. If the budget runs out, the
    transport stops sending until the next Colombo day, and the cursor makes the next pass resume where the last
    complete one ended. Scheduling it is the owner's act on the laptop; each run contacts the source.

Worker, read-only checks:
- `preflight`: the 0022 tables, privileges and guard triggers.
- `status`: row counts and any stopped source.
- `verify`: every snapshot's rows reproduce their hashes; settled snapshots are rebuilt from stored inputs and must
  match; result alias-set hashes are checked.

Owner path (`cse-migrator` acting as `cse_owner`; the operator is recorded):
- `register-source --record FILE --ref NAME.md`: P6-R1. The record is validated (CSE hosts, credentials and
  undeclared relays are refused) and pinned.
- `start-ingestion --source ID --at ISO --reason TEXT`: from when the source counts as collecting. Before that, its
  snapshots are `not_collected`, never zero counts.
- `clear-stop --source ID --reason TEXT`: a source stopped by a block, a rate limit, the circuit breaker or an
  objection stays stopped for every process until the owner clears it.
- `alias --text T --issuer ID --kind name|ticker|owner --available-at ISO --source-ref TEXT`: dated alias evidence.
  A ticker is given in its full form (`ABC.N0000`); a short form is an owner alias.
- `end-alias --alias ID --available-at ISO --source-ref TEXT`: a rename, merger or reuse, as a new row.
- `declare-archive --source ID --terms TEXT --published-at-kind original|last_modified --content-state
  as_published|as_edited --completeness complete|selection --from DATE --to DATE --sha256 HEX`: §9. The batch is a
  local file the owner supplies, never fetched. Edited or selected batches are admitted to CURRENT only.
- `designate --purpose TEXT --configuration ID --note TEXT`: the NS configuration in force for a purpose
  (`sentiment`).

There is no delete, reset or repair command: every table is append-only for every role.

## What the database enforces (0022)

- Owner-only tables and kinds.
- Every row stamped by the database (`recorded_at = now()`, writer = the login), and append-only.
- No source may allow a CSE host, in its allowed or its fetch-on-behalf hosts (a trailing dot changes nothing).
- No attempt may be recorded as sent without a host, to a CSE host, or with a CSE host in its URL.
- `extracted_text` only for a `full_text` source.
- A first version's first availability is its own availability; a later version inherits its article's.
- An archive version comes from an archive import of the same source.
- A configuration's id is the SHA-256 of its JSON.

## Timing notes

- **Gaps:** a session day counts as collected only if its `ran` run is admitted at the query: recorded at or before
  T in KNOWN and AVAILABLE, at or before H in CURRENT. A run recorded later (a backlog) fixes later snapshots, never
  point-in-time snapshots at an earlier T. A `partial` run is a gap; so is a source's start day with no run.
- **The session sequence** passed to a snapshot should be longer than the longest window (21 sessions). An instant
  before the sequence's first day is treated as older than every window, which is conservative only when the
  sequence is long enough.
- **Restarts:** a source's start at T is its earliest start admitted at T; a later start never hides an earlier gap.
- **The lock:** the ledger's advisory lock (one session per source) is taken by `ingest` before any request and
  released after the run; `purge-source` refuses while it is held.
