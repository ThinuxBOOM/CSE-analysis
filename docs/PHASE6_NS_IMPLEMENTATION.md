# Phase 6: news and sentiment, implementation record

Branch `integration/cloud-phase6`, from `main` at `00ce584` (C-0119). Design: `docs/PHASE6_NEWS_SENTIMENT_DESIGN.md`,
revision 3. **Fakes only:** no real news host in code or tests (synthetic hosts use the reserved `.test` domain), no
CSE contact, and no lexicon download.

**The owner's items (design §12), as decided on 2026-11-23 (C-0121, taken as recommended under the owner's
instruction):**

| ID | Decided | Still the owner's |
|---|---|---|
| P6-R1 | The source criteria. `onward_disclosure: "none"` for every source. CSE-derived content in third-party news falls under G-1: a source made mainly of CSE price or market reports is not registered | Naming each source, reading its terms and accepting its risk: one governance record per source, signed off before `register-source` |
| P6-R2 | `metadata_only` for every source | `full_text` for a source whose terms expressly permit it |
| P6-R3 | The purge procedure, following P2 §12 (runbook); its tooling (`stop-source`, `purge-source`) is the next build step before any ingestion | What a source is promised about backups, residual retention, any deadline |
| P6-R4 | English only in v1 | |
| P6-R6 | v1 uses the project's own lexicon, still `provisional`: not used to train, select or promote a model until evaluated on real news | Any third-party lexicon and its licence |
| P6-R7 | The sector-sentiment deviation is accepted until P3-R5 | |
| P6-R8 | User-Agent `research-news-reader/1 (non-commercial; obeys robots.txt)`; no contact | A non-personal contact mailbox, if a source requires one |
| P6-R5 | | The merge and re-staging |

---

## NS-1: the governed transport and the fetch ledger

| Module | What it does |
|---|---|
| `worker/news_ingest/hosts.py` | The host rules, checked before any socket opens. CSE (`cse.lk` and every subdomain) is refused unconditionally. A CSE host in the path, query or fragment is refused, with percent-encoding decoded up to three times. Fetch-on-behalf services (archive "save" endpoints, translation proxies, URL relays) are refused unless the record names them. The allow-list is exact: no wildcards and no subdomains by implication. Only http and https are allowed, and no credentials may appear in a URL |
| `worker/news_ingest/governance.py` | The source's governance record: Markdown for people, with one fenced `ns-source` JSON block for the code. The record's SHA-256 is over the whole file and is pinned in `ns_sources`. With no record, a hash mismatch, or an `allowed_hosts` that differs from the one `ns_sources` holds (Minor 11), nothing runs. A record is refused if it names a CSE host or credentials (v1 has no credentialed scraping), names a relay without the owner's naming, omits the storage level or the identification (P6-R2, P6-R8), or does not obey robots.txt |
| `worker/news_ingest/transport.py` | The governed transport, on P2's pattern. Every request passes, in order: a stopped source; the host rules; robots.txt (read once a Colombo day through the same transport, then followed; an unreadable file means deny); the daily budget (attempts sent, per Colombo day); the spacing (seeded from the ledger, so it holds across processes); and the attempt is recorded before it is sent. Responses: 3xx is recorded and never followed automatically, and its target is checked again and requested as a separate attempt (at most 3 hops); 401, 403, 407 and 451 stop the source; 429 waits for a `Retry-After` within the bound, and a longer one stops the source; 5xx and timeouts back off exponentially; a body over the limit fails and is never truncated; a circuit breaker stops the source after repeated failures; an objection stops it at once. Also `ScriptedSender` (the fake), `MemoryLedger`, and `fake_clock` |
| `worker/news_ingest/http.py` | The only module that can open a connection: `requests` with `trust_env=False`, no proxies, a cookie policy that stores nothing, no automatic redirects, and a streamed body limit. No test uses it, and no source exists to configure it with |

Tests:
- `tests/test_ns1_unit.py` (28): the record (refusal without a record, hash and allow-list pins, every field
  refusal); CSE refused before any socket, by host and in the URL (encoded, double-encoded, in the path, in the
  fragment); the allow-list; relays; robots; spacing (also across processes) and the identification sent; the
  budget by Colombo day; record-before-send; redirects (to CSE, to a disallowed host, to an allowed host as a second
  attempt, relative, not followed, a loop); back-off and give-up; each blocking status; `Retry-After` in seconds and
  as a date; the circuit breaker and an objection; a body over the limit.
- `tests/test_ns_static.py` (3): only `http.py` imports a network library (`urllib.parse` and `urllib.robotparser`
  are allowed); no real host in Phase 6 code or `test_ns*` tests, apart from the refused constants; `cse.lk` in code
  only in `hosts.py`.

**Note.** The fetch-on-behalf list in `hosts.py` names real relay services. They are refused constants, like CSE's
domain, never hosts the code requests.


### NS-1 audit (fresh-context agent, at `88cff17`): accepted

No Blocker (C-0037). The auditor found no way for a request to reach a CSE host, after trying uppercase, trailing
dots, userinfo tricks, fullwidth and ideographic dots, brackets, absolute, relative and scheme-relative redirects, a
hop through another allowed host, and robots fetching. The attempt is always recorded before it is sent. Three
Majors, all fixed:

| ID | Finding | Fix |
|---|---|---|
| Major 1 | A stopped source (a block, an objection, a long Retry-After, the circuit breaker) was stopped only in memory: a new process sent again | The stop is recorded in the ledger. `stopped` is read from it on every request, and only the owner clears it (`owner_clear_stop`, with a reason). Tested with two transports on one ledger |
| Major 2 | The static guard did not walk subpackages, skipped relative imports (any module could use the real sender), missed P2's transport, `asyncio` and `ssl`, did not scan bytes, and exempted every subdomain of the relay list | It walks recursively. Only `wiring.py` may import the real sender. It forbids the project's other network layers, more network modules, and `__import__`, `exec`, `eval` and `import_module`. It scans bytes constants. Relay domains are exempt only as exact constants. The guard tests itself with planted cases |
| Major 3 | The "encoded" CSE cases still held a literal `cse.lk`, so a mutant without any decoding survived | Cases with the dots encoded once, twice and three times, a nested encoding past the bound, an ideographic full stop, fullwidth letters, a zero-width split and an HTML reference |

**Minors fixed:**
- Decoding repeats until stable (percent-encoding and HTML references, NFKC, other full stops, invisible
  characters). A URL still changing after 8 rounds is refused.
- The record:
  - allows finite numbers only, never booleans, within bounds;
  - refuses unknown keys, credential keys at any depth, and control characters in the identification;
  - requires `pinned_allowed_hosts`.
- The transport:
  - one request at a time (a lock);
  - an in-flight attempt counts as ending now for spacing;
  - `robots` is the transport's own purpose;
  - a malformed URL is a recorded `bad_url` refusal, and a sender exception is recorded as `failed`;
  - headers are read case-insensitively, and a 429 with no `Retry-After` stops the source;
  - robots is cached per scheme, host and port.
- The real sender has a total deadline as well as the per-read timeout.
- The CSE-constant test skips real docstrings only.
- New tests:
  - the budget day at the Colombo, not UTC, boundary;
  - the exact redirect limit;
  - a failed attempt ending the spacing interval;
  - the wiring module refusing without a record.

**Open:** the per-source advisory lock across processes belongs to the store (NS-7).
---

## NS-2: the adapter interface, article versions and observations

| Module | What it does |
|---|---|
| `worker/news_ingest/adapter.py` | The adapter interface: `list_new`, `fetch` and `refetch`, whose result is a `RawItem`, a `Gone`, or `None` for a failed attempt. Every request goes through the governed transport the adapter is given. `SyntheticAdapter` reads a synthetic JSON format and is the only adapter in v1; the first real one comes after P6-R1 |
| `worker/news_ingest/articles.py` | Pure, so the store and the tests share one rule. **Versions** by the SHA-256 of the normalised extracted text (NFKC, typographic quotes and dashes to ASCII, whitespace collapsed; case and wording kept). The raw body hash is evidence only. The same text gives a `confirmed` observation; changed text gives a new version that supersedes the latest. Every version inherits the article's first availability (N5). **Withdrawals** only on evidence: the source's explicit removal, or 404/410 on two re-fetches at least a day apart; a reappearance resets the count, and 5xx, 429 or a single 404 never count (N6). A withdrawal's `available_at` is the confirming re-fetch's `observed_at` (N2). **In force at T**: the latest admitted version of each article, dropped once a withdrawal is admitted (KNOWN by `recorded_at`; AVAILABLE by `available_at` with `recorded_at` ≤ H). **Archives** (§9): available from the declared publication under the source's policy (a date-only value counts from the end of the day in the source's time zone, plus any lag). **The re-fetch plan**: new references first; re-fetches at 1 and 5 sessions after first observation, and a confirming re-fetch a day after a first 404/410; what does not fit the budget is skipped and returned for recording, never queued; withdrawn articles are done |

Tests: `tests/test_ns2_unit.py` (10).
- The adapter: through the transport, with a CSE URL in a listing refused; gone, explicit removal and failure kept
  distinct.
- Revisions by normalised text.
- Withdrawals: under a day, a day apart, explicit, a non-evidence status, a reappearance.
- The re-fetch plan.
- NL-1 to NL-4, each with its converse. NL-4 also covers NL-14's shape in AVAILABLE.
- The archive availability policy.

---

## NS-3 to NS-5: aliases, entities, relevance, events, sentiment and deduplication

| Module | What it does |
|---|---|
| `worker/news_sentiment/config.py` | The NS configuration: processor versions (`ns.entity.1`, `ns.relevance.1`, `ns.event.1`, `ns.sentiment.1`, `ns.decay.1`), the twelve event types, weights, windows, thresholds, half-lives per event type, the policies and the lexicon's SHA-256, as one canonical document with a SHA-256 id. Every constant is `provisional`; `max_age_sessions` must be at least 21 |
| `worker/news_sentiment/lexicon_v1.json` | **The hand-written provisional lexicon (P6-D3)**: positive and negative terms, negators and intensifiers, with provenance and licence recorded. No third-party terms and no download; v1 uses it, still `provisional`; a third-party lexicon and its licence are the owner's (P6-R6, C-0121) |
| `worker/news_sentiment/aliases.py` | Dated aliases (F5 names, MF tickers in their full form, owner aliases) and alias ends as new rows, each admitted by its own two clocks (§5.2; N2, NL-5, NL-13, NL-15); the alias-set hash |
| `worker/news_sentiment/process.py` | The pure v1 processors. **Entity**: exact matching on normalised tokens (company suffixes normalised; a full ticker is one token; the longest overlapping match wins); an alias mapping to two issuers is kept as `ambiguous` and scores neither; a ticker that is also a word matches only in full form unless an owner alias adds the short form; positions are title, first paragraph and body; `market_wide` comes from a fixed list of terms. **Relevance**: the stated weights for position, the issuer's share of mentions and an attached event. **Events**: the fixed taxonomy by rule phrases within a window of a mention; the rule id always, an excerpt only under `full_text`. **Sentiment**: lexicon terms in a window around the mentions, with negation and intensifiers, normalised by the number of scored terms and clamped to [−1, 1]; `unscored` below the minimum, never `neutral`; the market-wide score the same way. Articles not in English are `language_unsupported` and score nothing |
| `worker/news_sentiment/results.py` | §5.5: a stored result counts at T only if its own `recorded_at` is admitted (≤ T in KNOWN, ≤ H otherwise), every alias it used is admitted, and it is under the configuration in force. Otherwise `full_text` recomputes from what is admitted at T, and `metadata_only` has no result (N3) |
| `worker/news_sentiment/dedup.py` | §6: the story key is the SHA-256 of the sorted title tokens (after a stop list) and the issuer set. Stories are grouped per snapshot from admitted articles only. A story's id is its key plus its earliest member's id, its instant is the earliest member's, and its sources are kept (NL-12) |

Tests: `tests/test_ns3_unit.py` (23).
- Matching: suffixes, positions, ambiguity, ticker as a word, word boundaries and no fuzzy matching.
- NL-5, NL-13 and NL-15, with alias ends.
- Relevance by hand; each event rule; proximity and excerpts; market events.
- Sentiment by hand: plain, negation, intensifiers with clamping, a mixed case.
- `unscored` and the language rule; market sentiment.
- Story keys and ids; NL-12 and its converse.
- NL-11: a result recorded after T, an alias recorded after T, recomputation under `full_text`, the stored result
  in AVAILABLE, another configuration's result.


### NS-2 to NS-5 audit (fresh-context agent, at `3d01c40`): accepted

No Blocker (C-0037). Five Majors, all fixed:

| ID | Finding | Fix |
|---|---|---|
| Major 1 | Leakage through stored results. Take an alias end recorded before T but announced after T. A result computed under that end still passed "every alias it used is admitted", so in AVAILABLE "Gamma" was clear although both aliases were in force | A result records its whole alias context: every alias admitted to it, ends applied. A stored result is reused only when that context equals the one admitted at T; otherwise it is recomputed (`full_text`) or there is none. **The design's rule (N3) is not enough as written, and this note supersedes it** |
| Major 2 | NL-11 never tested the result's own clock on its own | A result recorded after T, with all its aliases early: not used at T; used once T passes its `recorded_at` |
| Major 3 | Negation crossed clauses and scored terms ("no loss, strong growth" read as negative) | Clause punctuation is kept as a boundary token; the negation scope ends at a boundary or at an earlier scored term. Hand cases for both sentences and for the reach |
| Major 4 | A same-text reappearance (a `confirmed` observation) did not reset a pending absence | The count restarts after the latest sighting, a new version or a `confirmed` observation |
| Major 5 | A date-only archive date carried as UTC midnight moved to the previous day in a source time zone west of UTC, so an article became available a day early | The calendar date is taken as stated, never converted. Tested with −05:00 |

**Minors fixed:**
- The title is part of the revision hash: a changed title is a revision.
- A `ticker` alias must have the full form.
- Every matched alias span, including other issuers', is left out of sentiment windows.
- The lexicon must match the configuration's hash, and the rule tables (events, market terms, suffixes, tokeniser,
  stop list) are hashed into the configuration id.
- The plan:
  - confirms once 24 hours have passed;
  - drops a confirmation after a reappearance;
  - re-fetches an article at most once a day;
  - reports new references over the budget as skipped;
  - uses the Colombo first day.
- An archive's date-only publication is kept.
- Invisible format characters are stripped before hashing.
- A row with no `recorded_at` is never admitted (tested).

The tests kill the surviving mutants for the result clock, the first day, the negation reach, the threshold boundary
and the alias spans.

**Open, for the first real adapter:** `SyntheticAdapter` keeps its URL map in memory, and malformed items raise
instead of failing.
---

## NS-6: decay and the snapshot

| Module | What it does |
|---|---|
| `worker/news_sentiment/decay.py` | `ns.decay.1` in MF's Decimal context. **Instant to session**: in Colombo time, at or before the 14:30 close on a final session it is that session; otherwise (after the close, a non-session day, an unknown date, a non-final session) it is the next final session; after D\\*'s close it is age 0. Age = index(D\\*) − index(session), floored at 0. Weight = 2^(−a / half-life). **A story** (N7): s is the mean of its scored articles' scores, ρ the maximum relevance, the half-life the longest among the story's event types (`other`'s when none); an unscored story has no effective value |
| `worker/news_sentiment/snapshot.py` | §8's rows per issuer, plus one market row, with full provenance (each story's articles, versions, age, sources, score, ρ, half-life, effective value, events), the processor versions, the configuration id, `imported_inputs`, the snapshot's counts (admitted, unsupported, without a result, ingestion gaps) and a `row_hash` over canonical JSON. **States** in Phase 3's precedence: `not_collected` (no source active at T: a start record admitted and a start at or before T; every feature null, counts included), `conflicting`, `missing` (no story: sentiment null, counts 0), `unlabelled`, `partial` (a window session on or after a source's start without a `ran` row), `stale`, `fresh`. An issuer nobody wrote about still gets a row. **`unlabelled` is snapshot-wide** when mixed processor versions appear, or when unsupported-language and result-less articles exceed the policy's share: such articles cannot be attributed to an issuer, so they mark every row (an interpretation of §8, stated here). The sequence passed must be MF's as of T: a session after T is refused (NL-7). `configuration_in_force`: the configuration designated at T (KNOWN) or H (otherwise) (NL-6) |

Tests:
- `tests/test_ns6_unit.py` (12):
  - age vectors: before, at and after the close, after D\\*'s close, a weekend, a holiday, a time in UTC;
  - weights and story vectors: several events, no event, different relevances, unscored;
  - features and provenance by hand, over two sources;
  - no news is `missing` (never neutral);
  - every quality state and the precedence;
  - NL-16, NL-6, NL-7, NL-9 and NL-14, with converses;
  - archive age and `imported_inputs`;
  - the market row.
- `tests/test_ns_static.py::test_nl10` (NL-10): the NS packages import, from the rest of the project, only MF's
  modes, calendar, numeric and canonical helpers and issuer identity; never MF's features, market, targets or store,
  F7, the engines or ML.

---

## NS-7: migration 0022, the store, the preflight, `verify` and the runbook

**Migration `supabase/migrations_pending/0022_news_sentiment.sql`**, outside the chain (C-0119). It has 15 tables, all
append-only for every role, with every row stamped by the database (`recorded_at = now()`, writer = the login):

| Tables | Who writes |
|---|---|
| `ns_sources` (record reference and SHA-256, storage level, availability policy, allowed and fetch-on-behalf hosts, onward disclosure) | owner |
| `ns_source_events`: `ingestion_started` and `stop_cleared` | owner |
| `ns_source_events`: `stopped` | worker |
| `ns_fetch_attempts`, `ns_fetch_events` (one outcome per attempt), `ns_ingestion_runs` | worker |
| `ns_archive_imports` (§9's declaration) | owner |
| `ns_articles`, `ns_article_observations` | worker |
| `ns_entity_aliases`, `ns_alias_ends` | owner |
| `ns_configurations` (id = SHA-256 of its JSON) | worker |
| `ns_designations` | owner |
| `ns_article_results` (with the alias-context hash) | worker |
| `ns_snapshots` (with MF's session sequence it used), `ns_snapshot_rows` | worker |

Database rules:
- No source may allow a CSE host, and no sent attempt may name one (defence in depth for §4.1).
- `extracted_text` only for a `full_text` source.
- A version must supersede a stored version and inherit its first availability.
- No binary, path or credential column; no SECURITY DEFINER, new role or row-level security.

| Module | What it does |
|---|---|
| `worker/news_store/store.py` | **Owner path:** `register_source` (the record validated by `governance.parse` and pinned), `start_ingestion`, `clear_stop`, `add_alias` (full-form tickers), `end_alias`, `declare_archive`, `designate`. **The ledger** (`DbLedger`): each attempt is committed before it is sent; a stop holds for every process until the owner clears it; the budget day is Colombo's; an in-flight attempt counts as ending now; one session per source through an advisory lock. **Worker writes:** versions, observations, configurations, results, ingestion runs. **Reading at T:** `load` (an `as_edited` or `selection` archive is admitted to CURRENT only, NL-8), `articles_at` (the versions in force, each with its admissible result), and `build_snapshot`, which uses the configuration named or designated at the knowledge instant (NL-6) and MF's sequence at T, and writes the snapshot and its rows. `require_point_in_time` refuses CURRENT (NL-8) |
| `worker/news_store/verify.py` | Each stored snapshot's row hashes and snapshot hash, and its configuration reproducing its id. A settled snapshot is rebuilt from the stored inputs with its stored sessions and must match. Each result's alias-set hash is checked |
| `worker/news_store/preflight.py` | The tables, the worker's exact privileges, every guard trigger enabled |
| `worker/news_store/cli.py`, `ops/bin/cse-news` | `preflight`, `status` and `verify` (worker); the owner path for the registrations. **There is no ingest command in v1** |
| `docs/ops/P6_NEWS_SENTIMENT.md` | The runbook, opening with the owner's decisions (C-0121) and the purge procedure |

Tests:
- `tests/test_ns7_static.py` (3): pending and outside the chain; the migration guards; owner tables never granted to
  the worker; CSE refused in the SQL; the wrapper; every command in the runbook; no ingest command.
- `tests/test_ns7_postgres.py` (6, on PostgreSQL 16 here and PostgreSQL 17 in CI):
  - the database rules;
  - the ledger under the transport: committed before send, a stop held across processes until the owner clears
    it, CSE refused and never recorded as sent, the advisory lock, the budget count;
  - a synthetic source through the transport and adapter, to stored versions, results and runs, to a stored
    snapshot (NL-1 at an earlier T, NL-6 with no designation, a `missing` issuer), then `verify` with a replay and a
    tamper;
  - NL-8;
  - the NS-6/7 audit cases below.

The full offline suite passes (2,709 tests after the NS-6/7 re-audit fixes; no CSE contact logged by the network guard).

### NS-6 and NS-7 audit (fresh-context agent, at `d8a7423`): accepted

No Blocker (C-0037). Four Majors, all fixed:

| ID | Finding | Fix |
|---|---|---|
| Major 1 | A source's start was the latest `ingestion_started` row, whatever its `recorded_at`. A restart recorded after T moved the start at T, and so hid a gap (look-ahead). `load` read starts and runs in no fixed order | A source's start at T is its earliest start that is admitted at T (`snapshot._starts`). `load` orders starts and runs by id. Tested both ways round, with a restart recorded after T and with an admitted one |
| Major 2 | The static NL-10 guard did not resolve relative imports, so `from . import x` could reach a forbidden module unseen | Relative imports are resolved against the file's package, and both `mod` and `mod.name` are checked. A planted case proves it |
| Major 3 | Three cases had no test: a backlog `ran` row recorded after T, a `selection` archive batch, and an archive's age through the store | A backlog run recorded after T leaves the snapshot at T `partial` (and clears it at a later T); a `partial` run is a gap. Through PostgreSQL: a `selection` batch is admitted to CURRENT only, and an archive's age comes from its stated date, never the import day |
| Major 4 | Too few hand values and boundaries | Hand values for `sent_eff_sum`, `sent_eff_mean`, the newest event age and the counts. Boundaries: `news_count_5` (age 4 and 5), `news_sources_21`, `max_age` (20 and 21), stale (10 and 11), the 0.2 positive threshold, the start day, and the 14:30 close on an earlier session. One case for each adjacent pair of quality states |

**Minors fixed:**
- `verify` compares the snapshot hash, which now binds the session sequence and the active sources, on replay too, and
  checks each result's alias ids against its stored alias set.
- 0022 refuses a CSE host with a trailing dot, a null host, and a CSE host in the URL; it checks the fetch-on-behalf
  hosts too.
- An archive version must come from an import of the same source, and a first version's first availability is its own.
- The preflight requires each guard trigger to be enabled (`tgenabled = 'O'`), matched by table.
- An instant before the session sequence is older than every window, never its first session.
- A test that the configuration object must be the one named or designated.

**Recorded, not changed:**
- `unlabelled` is snapshot-wide: when the share of in-window articles with no supported label or no result is above
  the configuration's limit, or the articles come from more than one processor version, every row is `unlabelled`,
  because no issuer can be shown free of it. This is a deliberate exception to NL-9's per-issuer states; the owner may revisit
  it with P6-R6.
- The ledger's advisory lock is taken by the caller. v1 has no ingest command, so nothing takes it yet; the first
  ingest command must (recorded in the runbook).
- A day counts as collected only if its run is admitted at the query (recorded at or before T in KNOWN and
  AVAILABLE, at or before H in CURRENT). A backlog run added later fixes later snapshots, never point-in-time ones at
  an earlier T (recorded in the runbook).

### NS-6 and NS-7 re-audit (fresh-context agent, at `6e83a55`): accepted

No Blocker and no new Major. All four Major fixes were confirmed correct, with no new look-ahead, and confirmed by
13 mutations. Seven Minors, all dealt with:

| ID | Finding | Fix |
|---|---|---|
| 1 | The `news_sources_21` boundary was claimed but tested at age 17 | A second-source story at ages 20 (counted, newest event age 20) and 21 (out) |
| 2 | Three adjacent states and the start day were not pinned: mutations swapping `not_collected`/`conflicting` or `missing`/`unlabelled`, or excluding the start day from gaps, passed | A case for each, including a start day with no run (`partial`). Each mutation now fails a test |
| 3 | The Minor fixes in `verify`, the preflight and 0022 had no tests | Through PostgreSQL: a `sessions_json` tamper and a `context_alias_ids` tamper are found by `verify`; a disabled or replica-only guard trigger is found by the preflight; an archive version from another source's import and a fetch-on-behalf `www.cse.lk.` are refused |
| 4 | 0022's URL check missed `https://a@b@cse.lk/` and schemes with `+`, `.` or `-` | The pattern now takes the user-info up to the last `@` and any URL scheme. Both tested. The static host guard in `tests/test_ns_static.py` read such a URL the same wrong way and is corrected too |
| 5 | The gap timing note held for KNOWN and AVAILABLE only | Reworded here and in the runbook: admitted at the query, at or before T, or H in CURRENT |
| 6 | The runbook's list of what 0022 enforces was out of date | Updated |
| 7 | `BEFORE_SEQUENCE` is conservative only on a long enough sequence | Recorded in the runbook: the sequence should be longer than 21 sessions |

## NS-8: the first real source's adapter, GDELT's DOC 2.0 API (C-0122), on fakes

| Module | What it does |
|---|---|
| `worker/news_ingest/gdelt.py` | `GdeltDocAdapter`, metadata only. It makes one listing request (`mode=artlist`, JSON, up to 250 records, newest first; `startdatetime` in UTC for `since`) through the governed transport. The host is the record's single allowed host. **No publisher page is ever requested**: `fetch` returns the item from the listing with no request. `refetch` returns None with no request (`REFETCH = False`), since the service gives no evidence of withdrawal. `published_at` is GDELT's first-seen time, an upper bound. The reference is `u` + the SHA-256 of the URL. Malformed entries are counted and skipped, never raised; URLs naming CSE are dropped and counted |
| `worker/news_ingest/governance.py` | Refuses a record containing `NOT SIGNED` (a draft) |
| `docs/governance/NS-GDELT-DOC.md` | The draft governance record, for the owner's checks and signature |

Tests: `tests/test_ns8_gdelt.py` (8 at first; 11 after the audit):
- only robots and the listing are sent, never the publisher's page;
- `since` is in UTC;
- references are stable;
- malformed entries and bodies are handled;
- CSE URLs (plain, with a trailing dot, encoded) are dropped;
- a failed listing is recorded in the ledger;
- the record must fit the adapter;
- a listing goes through to a title-only result (English processed, Sinhala unsupported);
- the draft record is refused until signed, and the signed copy fits the adapter.

The API's host is a `.test` host in tests; the real host is only in the record.

### NS-8 audit (fresh-context agent, at `6873710`): accepted

No Blocker. Two Majors, both fixed:

| ID | Finding | Fix |
|---|---|---|
| Major 1 | One entry could raise and lose the whole listing after the request was spent: an unparsable URL (`https://[::1/x`), or a lone surrogate in a URL or title | Each entry is classified inside its own guard: malformed, CSE, or kept. Nothing in an entry can raise. Text that cannot be stored is malformed. Tested with each case |
| Major 2 | The G-1 drop missed CSE written with look-alikes (fullwidth letters, `。`, a zero-width space): `is_cse_host` did not normalise | `hosts.is_cse_host` now also checks the host as a resolver would read it: NFKC, other full stops, invisible characters, IDNA labels. The transport benefits too. Tested with five spellings |

**Minors fixed:**
- the unsigned check ignores case, and signing means removing every "not signed";
- a repeated URL keeps its first (newest) entry and is counted;
- a full listing sets `truncated`;
- the item cache holds one listing;
- unmapped languages are `und`;
- `seendate` must be ASCII;
- an entry with a title over 1,000 characters is dropped as malformed;
- the stale texts are updated;
- the draft record now says the adapter's API assumptions were never checked live, and the first live listing is
  inspected by hand.

**Requirements recorded for the future ingest command:**
- plan no re-fetch for this adapter (`REFETCH = False`);
- re-`fetch` known references that are listed again (no request), so a changed title becomes a revision;
- overlap `since` and act on `truncated`.

## NS-9: `stop-source` and `purge-source` (P6-R3, C-0121, C-0123)

| Module | What it does |
|---|---|
| `worker/news_store/store.py` | `stop_source`: the owner's recorded stop. `purge_source` runs on the owner path, in one transaction, in this order: 1. refuses a source that is not stopped, or a purge without a request reference; 2. locks the purge tables; 3. writes the manifest (ids, hashes, counts; no content); 4. disables the user triggers of the named tables; 5. deletes the source's fetch events and attempts, results, observations, articles, archive imports, runs and `ingestion_started` events, and every snapshot whose active sources or stories name it (exact `<source_id>:` match); 6. re-enables the triggers; 7. adds a `stopped` row with reason `purged: …`, under the guards; 8. rebuilds the deleted snapshots at the same queries with their stored configuration. `dry_run` returns the manifest and changes nothing |
| `worker/news_store/cli.py`, `ops/bin/cse-news` | The two owner commands, run as `cse-migrator` |

Tests:
- `tests/test_ns7_postgres.py` `test_p7_ns9_stop_and_purge_a_source`:
  - refused to the worker, when not stopped, and without a request;
  - a dry run changes nothing;
  - the purge deletes only that source's rows and leaves another source untouched;
  - `ns_sources` and the stop are kept, and the source reads as stopped with `purged`;
  - the snapshot that named it is replaced and an earlier one is kept;
  - afterwards the preflight is clean, append-only holds again, and `verify` is clean.
- `test_p7_the_source_match_is_exact`: `ft` never matches `ftx`.

### NS-9 audit (fresh-context agent, at `e81a696`, with the NS-8 fixes): accepted

No Blocker. **Major 1, fixed: a purge missed snapshots that counted the source without naming it.** A snapshot's
counts and states count every admitted article. In the auditor's reproduction, an archive article of a source never
started was not selected, and `verify` failed after the purge.

Snapshots are now selected exactly:
- `purge-source` rebuilds every snapshot in memory before the deletion and again after it, inside the transaction;
- it replaces every snapshot whose hash changes, and every snapshot that names the source.

That costs two in-memory builds per stored snapshot. The test reproduces the case, checks that `verify` is clean,
and checks that the rebuilt snapshot keeps its subjects.

**Minors fixed:**
- The CLI checks the reason or request text before the operator's name is appended.
- The purge takes the source's advisory lock and refuses while an ingest holds it (tested).
- The stop is checked under the table locks.
- The lock order matches a worker's writes (snapshots before rows).
- The manifest counts the snapshot rows.
- The runbook's commit point is fixed.
- The runbook names what the tool does not purge (an owner alias's reference, other sources' fetch details) and the
  issuer-id trace a rebuild keeps.
- A test of the adapter's one-listing cache.
- The title-cap wording.

**Recorded:** the IDNA branch of `hosts._canonical_host` is not a protection. Python's codec refuses an `xn--` label
that would flatten to a CSE name. The fullwidth, full-stop and invisible-character cases are what it protects.

The re-audit, at `fde406f`, was accepted with no Major. Its Minors are fixed:
- the in-memory rebuilds load once per distinct query in each phase;
- the runbook says a purge locks the news tables for a while, and that a dry run leaves only gaps in row ids;
- the CLI's refusal of an empty reason or request is tested.

**CI on Windows, after the re-audit:** the draft-record test failed on Windows, because the checkout gives the
record CRLF line endings and the parser's block pattern accepted only LF. A record saved on Windows would have
been refused. `governance.parse` now accepts both, and a test covers it. The pinned SHA-256 is still over the file's
bytes, so re-saving a record with other line endings is a change that stops the source until it is registered again.

## NS-10: the ingest command and the owner's inspection (C-0126)

| Module | What it does |
|---|---|
| `worker/news_store/ingest.py` | `run_ingest(conn, source_id)`: one pass (runbook, "Commands"). It refuses, in order: an unknown adapter or source, a source not started or stopped, a busy source (its advisory lock, which the purge shares), and a record that fails `governance.load`'s pinned hash and hosts. It makes one listing through the governed transport with the database ledger, with `since` = the newest fetched article minus 6 hours, or the start. Each listed reference goes through `articles.ingest`, known ones included, so a changed title becomes a revision and the same title a confirmation. New versions are processed with the aliases admitted now and the configuration designated now (NL-6), or left unprocessed when none is designated. It then writes the run row (`ran` / `partial` / `failed`). `inspect(record)`: one listing with an in-memory ledger; it returns field names and the first entries and writes nothing |
| `worker/news_ingest/wiring.py` | `inspection_transport` added: still the only module that builds the real sender |
| `worker/news_ingest/gdelt.py` | `last_outcome`, `last_body` |
| `worker/news_store/cli.py` | `ingest`, `inspect` (worker); `inspect` opens no database connection |

Tests: `tests/test_ns7_postgres.py`:
- `test_p8_ns10_ingest_one_source_end_to_end`:
  - refusals, in order: unknown, not started, busy, an edited record, stopped;
  - a pass with no designation stores and leaves unprocessed, sending only robots and one listing, and drops a CSE
    URL;
  - a pass after designation catches a revision and a new article and processes both;
  - a third pass confirms;
  - a failing listing gives a `failed` run;
  - every sent request is in the ledger;
  - a snapshot from the ingested rows has stories, and `verify` is clean.
- `test_p8_ns10_inspect_writes_nothing`.

### NS-10 audit (fresh-context agent, at `9bac7c3`): accepted

No Blocker. Two Majors, both fixed:

| ID | Finding | Fix |
|---|---|---|
| Major 1 | A full (truncated) listing lost its oldest articles for good. The cursor advanced anyway, and a later `ran` the same day cleared the `partial` | The cursor is the latest *complete* pass's time, kept in the run's details; an incomplete pass does not move it. A full page is followed by a page ending at its oldest article (`enddatetime`), up to 8. A page that is not full, or one that reaches back to the cursor, completes the pass. Tested: two pages complete; 8 full pages give `partial` and the next pass lists from the last complete cursor |
| Major 2 | `inspect` got around a registered source's stop, budget, interval and ledger | `inspect` refuses a registered source (the CLI checks the database), so it serves only the check before `register-source`. Tested |

**Minors fixed:**
- A version without a result under the configuration in force is scored when listed again, and the runbook says to
  designate first.
- A pass that raises writes a `failed` run with the error's type and re-raises; the lock is released (tested). The
  run details hold the listing outcome, the cursor, the pages and the counts.
- The source's versions and observations are read directly, with no admission filter.
- A confirmation is written only after 24 hours unseen.
- The runbook's exit codes and budget arithmetic are corrected.
- Tests cover the cursor, paging, the spacing, re-scoring and failures.
