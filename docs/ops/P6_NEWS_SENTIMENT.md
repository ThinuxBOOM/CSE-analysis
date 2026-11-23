# Phase 6: news and sentiment, runbook

Design: `docs/PHASE6_NEWS_SENTIMENT_DESIGN.md` (revision 3). Implementation: `docs/PHASE6_NS_IMPLEMENTATION.md`.
Migration: `supabase/migrations_pending/0022_news_sentiment.sql`. **Pending, outside the chain**: moving it into the
chain is part of the owner's merge (C-0119).

## Before anything runs: the owner's decisions

Nothing fetches news until the owner has decided all of these. The code refuses to run a source without them.

| ID | Decision |
|---|---|
| P6-R1 | Which sources, each with its governance record in `docs/governance/`: terms read, risk accepted, request discipline, the exact hosts, onward disclosure, robots handling, and whether CSE-derived content in third-party news falls under G-1 |
| P6-R2 | Per source: `metadata_only` or `full_text` |
| P6-R3 | The purge procedure for a source's deletion request: text, titles, URLs, derived results, snapshots and backups |
| P6-R6 | Which sentiment lexicon, and accepting its licence. Until then the hand-written provisional lexicon is used, for building and tests only |
| P6-R8 | The identification sent to each news site: the User-Agent and any contact address |

**v1 has no ingest command.** The first real source's adapter is a later step, after P6-R1. CSE is never a news
source here (G-1 governs CSE), and the transport refuses CSE's hosts whatever a record says.

## The governance record

A Markdown file in `docs/governance/`, written for people, holding exactly one fenced `ns-source` block of JSON with
these fields:
- `source_id`, `allowed_hosts`, `fetch_on_behalf_hosts`, `storage_level`, `onward_disclosure`;
- `availability_policy`, `identification`, `request_discipline`, and `robots: "obey"`.

`worker/news_ingest/governance.py` describes each field. Registering the source pins the record's SHA-256 and its
allowed hosts. Any later change to the file stops the source until it is registered again.

## Commands (`sudo bash /opt/cse/app/ops/bin/cse-news <command>`)

Worker (`cse-worker`, read-only checks):
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
- No source may allow a CSE host, and no attempt to a CSE host may be recorded as sent.
- `extracted_text` only for a `full_text` source.
- A version inherits its article's first availability.
- A configuration's id is the SHA-256 of its JSON.

## Timing notes

- **Gaps:** a session day counts as collected only if its `ran` run is recorded before the snapshot's T. A run
  recorded later (a backlog) fixes snapshots built after it, never earlier ones. A `partial` run is a gap.
- **Restarts:** a source's start at T is its earliest start admitted at T; a later start never hides an earlier gap.
- **The lock:** the ledger's advisory lock (one session per source) is taken by the caller. The first ingest command
  must take it before any fetch.
