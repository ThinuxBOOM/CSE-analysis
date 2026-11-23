# Phase 6: news and sentiment, implementation record

Branch `integration/cloud-phase6`, from `main` at `00ce584` (C-0119). Design: `docs/PHASE6_NEWS_SENTIMENT_DESIGN.md`,
revision 3. **Fakes only:** no real news host in code or tests (synthetic hosts use the reserved `.test` domain), no
CSE contact, and no lexicon download.

**What the owner must decide before any real ingestion or scoring** (reserved, not assumed; design §12):

| ID | Decision | Needed before |
|---|---|---|
| P6-R1 | Which sources, each with its governance record: the terms read, the risk accepted, the request discipline, the exact hosts, onward disclosure to AI providers, robots handling, and whether CSE-derived content in third-party news falls under G-1. CSE announcements as a source fall under G-1 | Any ingestion |
| P6-R2 | Per source: `metadata_only` or `full_text` (long ML histories need `full_text`, §8.1) | Any ingestion |
| P6-R3 | The purge procedure for a source's deletion request, covering stored text, titles, URLs, derived results, snapshots and backups | Any ingestion |
| P6-R6 | Which sentiment lexicon, and accepting its licence | Any real scoring |
| P6-R8 | The identification sent to news sites, per source: the User-Agent and any contact address (personal data) | Any ingestion |

The other reserved items: P6-R4 (Sinhala and Tamil, a later version), P6-R5 (the merge and re-staging), and P6-R7
(the sector-sentiment deviation, for accepting Phase 6 against the Master Architecture).

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
| `worker/news_sentiment/lexicon_v1.json` | **The hand-written provisional lexicon (P6-D3)**: positive and negative terms, negators and intensifiers, with provenance and licence recorded. No third-party terms and no download; the real lexicon and its licence are the owner's (P6-R6) |
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
