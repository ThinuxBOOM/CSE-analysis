# Phase 6: news and sentiment (design)

**Status: DESIGN, revision 2 (2026-11-15). ACCEPTED [delegated] (C-0071): its re-audit found no Blocker. Not
implemented; the re-audit's Majors (§16) are fixed before NS-1.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no contact with any news
source, no CSE contact and no change to `main`.

*Revision 2 answers the first audit (§16), which failed revision 1 on one Blocker (nothing in the news transport stopped
it reaching CSE) and twelve Majors. Each is answered in place; §16 lists where.*

**Governing records:** Master Architecture (MA) §10, §11, §15, §16, §33, §38, §39, §41, §42, §55 (Phase 6), §56 to
§59, §68; `docs/governance/G-1_CSE_DATA_USE.md` (as the model for a source's governance record);
`docs/PHASE3_MARKET_FEATURES_DESIGN.md` (the temporal model and modes this phase reuses). Where this document and
those differ, they win.

**Short name:** NS. Steps are NS-1 to NS-7 (§13). Decisions are P6-D*n* ([delegated]) and P6-R*n* (reserved) (§12).

---

## 1. Conclusion

```
news source ─▶ governed ingestion ─▶ article record (append-only, two clocks)
            ─▶ entity identification ─▶ relevance ─▶ event extraction ─▶ sentiment     (each a versioned processor)
            ─▶ deterministic time decay ─▶ sentiment snapshot per issuer and market as of T
```

1. **Sentiment is an independent subsystem** (MA §15). It reads no Route 1 model, forecast or feature (only MF's
   session sequence and calendar and F5's issuer identity, which are truth layers) and writes nothing into market or
   financial truth. Its only consumers are Route 2 (Phase 7) and, as optional features once Phase 5 adopts §8.1,
   Phase 5. If sentiment enters both routes, Route 2's statistical independence from Route 1 weakens; Phase 7's
   `independent` pack excludes it.
2. **No source is chosen here.** Which news sources are read, under what terms, and what of each article is stored
   are owner decisions (P6-R1, P6-R2). The design is source-agnostic: a source is an adapter plus a governance
   record, and nothing is fetched until the owner approves both.
3. **Every article has two clocks**, and the system's own `observed_at` is the one that makes a sentiment snapshot
   point-in-time. News collected from the day ingestion starts is point-in-time. News loaded later from an archive
   has only a publication time, which sources revise and back-date; it is `reconstructed`, exactly as imported market
   history is.
4. **Processing is deterministic and versioned in v1**: rule-based entity matching, rule-based event types and a
   lexicon sentiment score. A model-based processor (including an LLM) is a later processor version that must go
   through Phase 7's provider boundary; nothing in v1 calls a provider.
5. **It is buildable now against synthetic articles and unvalidatable now.** Whether lexicon sentiment about CSE
   issuers means anything is an empirical question that needs real news and real outcomes.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Where met |
|---|---|---|
| The pipeline: source, article record, entity, relevance, event, sentiment, decay, snapshot | §15 | §4 to §8 |
| Each item retains source, publication time, observed time, entity, event, relevance, sentiment, processing version | §15 | §4, §5 |
| Time decay deterministic and versioned; `effective = raw × relevance × decay(age)` as the conceptual form | §15 | §7 |
| Company, sector and macro sentiment | §39 | Company and market: §8. **Sector: not met in v1**, for the reason Phase 3 gives (no point-in-time sector source; P3-R5); the deviation is the owner's, P6-R7. Macro: as the market-wide snapshot |
| No future news; `news_data_cutoff` and `sentiment_data_cutoff` per forecast | §11, §57 | §3, §10 |
| Historical news and sentiment for backtests | §33, §38 | §3, §9 |
| Provenance forecast → sentiment feature → news item → source | §42 | §8, §11 |
| Feature source, formula, units, availability rule, version, quality state | §39, §41 | §8 |

---

## 3. Temporal model

NS reuses Phase 3 §4 without change.

| Evidence | `recorded_at` | `available_at` |
|---|---|---|
| An article fetched by the system | The article row's database-default timestamp. The row also keeps `observed_at`, the fetch's own time | The same `observed_at`: the system saw it public then |
| An article loaded from an archive | The row's timestamp | The source's stated `published_at`, under the source's `availability_policy` (a date-only value counts from the end of that day in the source's time zone, then converted). An assumption, so `reconstructed` |

- Modes `KNOWN`, `KNOWN_RECORDED`, `AVAILABLE` and `CURRENT`, their labels and their guards are Phase 3 §4.2's.
- **`published_at` is evidence, never a clock for `KNOWN`.** A source can publish at 09:00 an article stamped 08:00,
  or edit an article and keep its original stamp. In `KNOWN`, an article exists from the system's `recorded_at`.
- **A revised article is a new version**, a new row linked to the earlier one. As of T, the version in force is the
  latest one admitted at T. An article later withdrawn by its source stays in history with a `withdrawn` observation;
  it is not deleted (G-1's purge principle is the model for a real deletion request: a deliberate, owner-controlled
  process, P6-R3).
- A forecast's `news_data_cutoff` and `sentiment_data_cutoff` are both its T; the snapshot records the newest admitted
  article's instants.

---

## 4. Sources and ingestion (NS-1, NS-2)

### 4.1 A source

A source exists in the system only as two things together:

- **A governance record** in `docs/governance/`, in the form of G-1: what the source's terms allow, what the owner
  accepts, the request discipline (rate, back-off, stop conditions), what may be stored (P6-R2) and for how long, and
  (revision 2, M10):
  - **the exact hosts** the adapter may contact (`allowed_hosts`); nothing else is ever requested;
  - **onward disclosure**: whether the source's titles, text or derived scores may be sent to an AI provider
    (Phase 7 §4); default **no**;
  - robots.txt handling, and the stop-on-objection and cessation rule (as G-1's: stop at once, record it, tell the
    owner);
  - **the identification sent** (the User-Agent and any contact address: an owner choice, since a contact address is
    personal data; P6-R8).
  The record's SHA-256 is pinned in `ns_sources`; without the record, or on a hash mismatch, the adapter refuses to
  run.
- **An adapter** implementing one interface: `list_new(since) -> item references`, `fetch(reference) -> raw item`, and
  `refetch(reference) -> raw item or gone` (§4.3). All HTTP goes through one governed transport of this phase, built
  on the pattern of P2's: one request at a time, a minimum spacing per source, back-off, the recorded identification,
  no proxies, no credentialed scraping, a daily budget, every attempt recorded before it is sent.

**The transport cannot reach CSE (revision 2, Blocker B1).** P2's transport pins its hosts and never follows a
redirect; this one does the same, and more:
- every request's host must be one of the source's `allowed_hosts`, checked **before** any socket opens; a reference
  to any other host is refused and recorded as a refused attempt;
- redirects are **never** followed automatically: a redirect response is recorded, and its target is requested only
  if it is itself an allowed host of the same source (as a new, separately recorded attempt);
- **CSE hosts are refused unconditionally**, whatever a configuration or governance record says: `cse.lk` and every
  subdomain, and every host CSE's own pages are served from (the list is a constant of the transport, shared with
  the static guards). News pages that link CSE announcement PDFs are recorded with the link as text, never fetched;
- **no fetch on our behalf (re-audit N1):** a URL whose path or query contains a CSE host (in any encoding the
  transport decodes) is refused, and `allowed_hosts` may not name a fetch-on-behalf service (archive "save" endpoints,
  translation proxies, URL relays) unless the owner decides so in the governance record; archive batches (§9) are
  local files supplied by the owner, never fetched by the importer;
- tests (NS-1): a CSE URL is refused before any socket opens; a redirect to a CSE host is refused; a URL outside the
  allow-list is refused; a URL carrying a CSE host in its path or query is refused; a redirect to another allowed host
  of the source is a second recorded attempt.

CSE's own announcements, if the owner wants them as a source, are CSE contact and fall under G-1 and its extension
decisions, not under this phase's delegation (P6-R1); they would need their own design on P2's transport.

### 4.2 What is stored

Two storage levels, chosen per source in its governance record:

| Level | Stored | Use |
|---|---|---|
| `metadata_only` | Source, URL, title, `published_at`, `observed_at`, language, and the SHA-256 of the fetched body. The body is processed in memory and discarded | The default. Processing results are stored; the text is not |
| `full_text` | The above plus the extracted text | Only where the governance record says the source's terms allow keeping it. Needed for re-processing with a new processor version |

Under `metadata_only`, a new processor version cannot re-process old articles: their results stay at the version that
produced them, and a snapshot built from mixed versions says so (§8). That is the cost of not keeping text, and it is
the owner's trade-off (P6-R2).

Tables are append-only (§11). No article text or source response is committed to Git, and tests use synthetic
articles written for the purpose.

### 4.3 Revisions and withdrawals (revision 2, M5)

- **Re-fetch policy** (a constant of the ingestion configuration, `provisional`): each article is re-fetched once at
  1 session and once at 5 sessions after it was first observed, within the source's daily budget (new articles first;
  re-fetches that do not fit are skipped and recorded, never queued indefinitely).
- **A revision** is the same source reference returning a body with a different SHA-256: a new `ns_articles` version,
  linked to the one it supersedes, recorded at its own instants. The same hash is recorded as a `confirmed` observation.
- **A withdrawal** is recorded only on evidence: an explicit removal signal of the source, or HTTP 404 or 410 on two
  re-fetches at least one day apart. A timeout, a 5xx, a 429 or a single 404 is a failed attempt, never a withdrawal.
- **A withdrawn article stops contributing** from the instant its withdrawal is admitted; before that instant it
  contributes as before (NL-4 and its converse).

---

## 5. Processors (NS-3 to NS-5)

Each processor is a pure function with an id and a version; its output rows carry both. A change of rules, lexicon or
alias evidence is a new version. Results are never overwritten: a new version adds rows.

### 5.1 Language

v1 processes **English** text only. Articles in Sinhala or Tamil are recorded with their language and marked
`language_unsupported`; they contribute to no score. That is a stated coverage limit (§14), not a silent drop: the
snapshot reports how many admitted articles were unsupported.

### 5.2 Entity identification (`ns.entity.1`)

- **Aliases are dated evidence, as of T, and not part of the processor version** (revision 2, M3).
  `ns_entity_aliases` (append-only, owner path) maps strings to issuers: the issuer's names as F5's issuer evidence
  records them, its tickers from MF's security identity (Phase 3 §8.1), and owner-entered aliases with a source
  reference. Each alias has `recorded_at` and `available_at` (the announcement of the name or ticker, as Phase 3 §8.1
  dates an identity link) and an optional end (a rename, a merger, a reused ticker). An alias applies in `KNOWN` from
  its `recorded_at`, in `AVAILABLE` from its `available_at` (and only while `recorded_at` ≤ H), and never after its
  end. Adding an alias is new evidence, not a new processor version.
- Matching is exact on normalised tokens (case, punctuation and company-form suffixes normalised by a fixed table),
  with word boundaries. No fuzzy matching in v1: a wrong entity is worse than a missed one.
- **Ambiguity is kept.** A string that maps to two issuers yields both candidates with `ambiguous`, and an ambiguous
  mention contributes to neither issuer's score. A ticker that is also an ordinary English word matches only in its
  full form (`ABC.N0000`) unless an owner alias says otherwise.
- Output per article: the issuers mentioned, each with mention count, positions (title, first paragraph, body) and
  the alias evidence used; plus `market_wide` if a market term from a fixed list appears (the exchange, its indices,
  the central bank, the currency).

### 5.3 Relevance (`ns.relevance.1`)

A score in [0, 1] per (article, issuer), from stated components: where the issuer is mentioned (title weighs most),
the share of the article's entity mentions that are this issuer's, and whether an event (below) is attached to it.
The weights are constants of the version and `provisional`.

### 5.4 Events (`ns.event.1`)

A fixed taxonomy matched by rules on phrases near an entity mention: `earnings`, `dividend`, `corporate_action`,
`management_change`, `regulatory`, `legal`, `rating`, `merger_acquisition`, `capital_raise`, `operations`,
`macro_policy`, `other`. An article can carry several events; each records the matched rule and a short excerpt
(under `full_text` storage) or the rule id only (under `metadata_only`).

### 5.5 Sentiment (`ns.sentiment.1`)

- A lexicon score in [−1, 1] per (article, issuer): signed lexicon terms in a window around the issuer's mentions,
  with negation and intensifier rules, normalised by the number of scored terms. The lexicon is a versioned file in
  the repository with its provenance and licence recorded. **Revision 2 (M7):** which lexicon is used, and accepting
  its licence, is the owner's (P6-R6); third-party lexicons carry terms and are obtained by a download, which the
  cloud session may not do. v1 is built and tested against a small hand-written lexicon in the repository
  (`provisional`, P6-D3), replaced only by the owner's choice.
- A label `positive`, `negative`, `neutral` from fixed thresholds, and `unscored` when fewer than the minimum number
  of lexicon terms were found. `unscored` is not `neutral`: it contributes nothing.
- A market-wide score is computed the same way around market terms.

**Results are admitted by their own clock (revision 2, M4).** Each `ns_article_results` row records its
`recorded_at`, the processor and configuration ids, and the hash of the alias set it used (the aliases' ids). At T a
result is admitted only if its own `recorded_at` is admitted (≤ T in `KNOWN`, ≤ H in `AVAILABLE`) **and** every alias
it used is admitted at T (§5.2). A result whose alias set is not admitted at T is not used: under `full_text` the
article is processed again with the aliases admitted at T, under `metadata_only` it is `unlabelled` for that issuer.
Test NL-11.

**Model-based processors.** A later `ns.sentiment.2` may use a model, including an LLM. It then runs through Phase
7's provider boundary (adapter, registered configuration, recorded request and response, no silent fallback), and its
outputs carry the provider configuration version. v1 contains no such call, and the existing guard
`tests/test_ai_provider_boundary.py` stays as it is. Such a processor is **out of scope** for this design and needs
its own design: its outputs over past articles carry Phase 7 §9's knowledge-cutoff contamination and are labelled
the same way, and it needs its own capability contract (Minor 7).

---

## 6. Deduplication and syndication

The same story appears on several sources and is edited over time. Counting each copy inflates a score.

- **Revision 2 (M6): a true key, no similarity threshold.** The story key of an article is the SHA-256 of its
  normalised title tokens (sorted, after removing a fixed stop list) and its issuer set. Articles with the same key
  within `max_age_sessions` are one story. A similarity rule would need pairwise clustering, which is order-dependent
  and lets a later article bridge two earlier stories; v1 does not use one.
- **Story ids are derived per snapshot** from the admitted articles only (the key and the earliest admitted member's
  id), so an article recorded after T can never change the grouping at T (NL-12).
- A snapshot counts each story once per issuer: the earliest admitted article of the story sets its age; the
  story's score is the mean of its admitted articles' scores; the number of sources is kept as a separate field.
- The rule is deliberately conservative and `provisional`; its error rate is measurable only on real news (§14).

---

## 7. Time decay (`ns.decay.1`)

For a story with raw sentiment s, relevance ρ, and age a (in **sessions** of MF's session sequence, below):

```
effective = s × ρ × 2^(−a / half_life[event_type])
```

- This is MA §15's conceptual form with an exponential decay. Half-lives are per event type, constants of the
  version, `provisional` (for example 5 sessions for `earnings`, 2 for `other`).
- Age is counted in sessions so that a weekend or a holiday does not age news the market could not react to.
- **The age clock (revision 2, M2).** A story's instant is its earliest admitted article's **availability**: in
  `AVAILABLE`, the article's `available_at`; in `KNOWN`, its `observed_at` for a fetched article, and for an archive
  article its `available_at` (never its `recorded_at`: an archive imported today is old news, and a `KNOWN` result
  that used one carries `imported_inputs`, as Phase 3 §4.2's imported bars do).
- **Instant to session:** the instant is converted to Colombo time; an instant at or before the market's close on a
  session day belongs to that session, later instants (and instants on non-session days) to the next session.
  a = (index of D\*) − (index of the story's session), **floored at 0**: a story that became available after D\*'s close
  but before T has age 0 and weight 1, never more. The "fraction of the current day" of revision 1 is dropped.
- Hand-computed vectors cover each case (fetched, archive, after-close, weekend, holiday).
- A story older than `max_age_sessions` contributes nothing and is not loaded. `max_age_sessions` is at least 21, so
  the 21-session counts and `event_flags` are complete (Minor 2).
- The function is pure and deterministic: Decimal arithmetic in the fixed context of Phase 3 §10.1.

---

## 8. The sentiment snapshot (NS-6)

Per issuer at (T, mode), and one market-wide snapshot:

| Feature | Definition | Units |
|---|---|---|
| `sent_eff_sum`, `sent_eff_mean` | Sum and mean of `effective` over the issuer's admitted stories | score |
| `sent_pos_share`, `sent_neg_share` | Shares of scored stories labelled positive, negative | ratio |
| `news_count_5`, `news_count_21` | Stories in the last 5, 21 sessions | count |
| `news_sources_21` | Distinct sources in the last 21 sessions | count |
| `news_recency` | Sessions since the newest story | sessions |
| `event_flags` | Which event types occurred in the last 21 sessions, each with its newest age | list |
| `mkt_sent_eff_mean`, `mkt_news_count_5` | The same for market-wide stories | score, count |

- **No news is not neutral news.** With zero admitted stories the sentiment features are `missing` and
  `news_count_*` are 0. A consumer can tell an issuer nobody wrote about from one with balanced coverage.
- **Quality states**, in Phase 3 §10.4's precedence (revision 2, Minor 1): `missing` (no admitted story; value null);
  `conflicting` (ambiguous entity mentions above the policy's share; value null); `unlabelled` (mixed processor
  versions in the window, or unsupported-language articles above the policy's share); `partial` (ingestion gaps
  inside the window: days recorded in `ns_ingestion_runs` as not run, as MF knows unknown dates; a backlog fetched
  after downtime does not clear it, since those articles were known late); `stale` (newest story older than the
  policy's limit); `fresh`. Null states are Phase 3's: `missing` and `conflicting`.
- **Feature registry** (Minor 9): each feature has an entry, as MF §10.1's, with source, formula, units, availability
  rule, version and quality state. `event_flags` is encoded for consumers as one numeric age per event type (null
  where none). `sent_pos_share` counts stories by the label of the story's mean score.
- Each row stores the ids of the stories and article versions it used, the processor versions, the configuration id
  and a `row_hash`. That is MA §42's chain: forecast → sentiment feature → news item → source.
- **Coverage is uneven by construction.** Large issuers are written about; small ones are not. A model given these
  features can learn "is covered" as a proxy for size. The snapshot therefore always carries the counts, and Phase 5
  must treat sentiment features as optional inputs evaluated by the sentiment-versus-no-sentiment comparison MA §31
  requires.

---

### 8.1 The consumer contract for Phase 5 (revision 2, M1 and M12)

Phase 5 (accepted, C-0044) reads no NS table. **Until Phase 5 is revised to adopt this contract, no NS feature may
enter ML.** The contract the revision adopts:

- A dataset row's NS features come from the NS snapshot at **the same (T, mode, H)** as the row's MF snapshot; an NS
  snapshot whose T is later than the row's, or whose H differs, is refused (a leakage test in Phase 5's set).
- The manifest pins one NS configuration (`ns_configurations` id) beside the MF, F7 and DE ids.
- Eras: NS is point-in-time from ingestion start and `reconstructed` before it; a row is labelled with the weaker
  of its MF and NS labels, and Phase 5's rule against mixed H applies to the pair.
- NS states map to Phase 5 §4.4's admissible states as MF's do (`fresh`, `stale`, `partial` admissible; `missing`,
  `conflicting`, `unlabelled` not), and an issuer without news is distinguishable (counts are 0, sentiment missing).
- **Processor versions over history:** a training window must lie under one NS configuration. Under `metadata_only`
  a new processor version cannot be applied to old articles, so windows mixing versions are `unlabelled` and
  excluded; ML use over long histories therefore needs `full_text` for the sources concerned (an input to the owner's
  P6-R2).

The Phase 5 revision is delegable work for the cloud session, tracked in the decision log.

## 9. History for backtests

- **From ingestion start:** `KNOWN`, point-in-time.
- **Before it:** only what an archive provides, admitted in `AVAILABLE` as `reconstructed`. An import batch declares,
  or is refused: the source and terms reference; whether `published_at` is the original or a last-modified time;
  whether the archive holds articles as first published or as later edited (`as_published` or `as_edited`); and
  whether it is complete for its date range or a selection.
- An `as_edited` or selected archive is admitted to `CURRENT` only, labelled, because the text of an edited article
  can contain what happened afterwards.
- An archive's `published_at` is a declaration; the source's `availability_policy` may add an `availability_lag`
  (Minor 5), in the spirit of Phase 5's embargo.
- A backtest over a period with no admitted news runs Route 2 without news and is labelled so. It is never run with
  today's articles about that period.

---

## 10. Leakage tests

Construction as Phase 4 §12 (two evidence sets under one fixed H for `AVAILABLE`); each also asserts the converse.

| ID | Planted | Must not change |
|---|---|---|
| NL-1 | An article recorded after T | Any snapshot at T |
| NL-2 | An article recorded after T with `published_at` before T (back-dated) | `KNOWN` at T |
| NL-3 | A revised version recorded after T | The version in force and its scores at T |
| NL-4 | A withdrawal recorded after T | The article's presence at T |
| NL-5 | An alias recorded after T | Entity matches at T |
| NL-6 | A new processor version, lexicon or configuration designated after T | Results pinned to the configuration designated at the query's knowledge instant (T in `KNOWN`, H in `AVAILABLE`, as Phase 3's `--designated`), or to the configuration the query names explicitly (Minor 4) |
| NL-7 | A later session or closure | Ages and decay at T |
| NL-8 | A `CURRENT` snapshot offered to a point-in-time consumer; an `as_edited` archive offered to `AVAILABLE` | Refused |
| NL-9 | An article about another issuer | This issuer's snapshot at T, except through a shared story key, which is asserted separately |
| NL-10 | Any market, financial or label value | No sentiment feature depends on one. The permitted inputs from other layers are MF's session sequence and calendar and F5's issuer identity (truth layers); the NS packages cannot import MF's feature or target modules, F7 or the ML packages |
| NL-11 | A result computed after T, or with an alias admitted after T | Results and entity matches at T (§5.5) |
| NL-12 | An article recorded after T that shares a key with, or would bridge, earlier stories | The story grouping and story ids at T |
| NL-13 | An alias whose `available_at` is after T, in `AVAILABLE` | Entity matches at T (§5.2) |

Every leakage test also asserts the converse (the planted evidence does change the result once it is admitted).

**Requirement-to-test map (revision 2, M11):**

| Requirement | Test |
|---|---|
| No governance record, or a record hash mismatch: the adapter refuses | NS-1 `refuse_without_record` |
| Only the ingestion transport opens a socket (static) | NS-1 `static_single_transport` |
| CSE hosts, non-allowed hosts and redirects refused (§4.1) | NS-1 `cse_refused`, `allow_list`, `redirects` |
| An undeclared archive batch refused (§9) | NS-7 `archive_declaration` |
| No news gives `missing` with counts 0, never neutral | NS-6 `no_news_is_missing` |
| Every quality state and the precedence | NS-6 `quality_states` |
| `language_unsupported` counted, never scored | NS-5 `language_counts` |
| `unscored` is not `neutral` | NS-5 `unscored` |
| Revisions and withdrawals (§4.3) | NS-2 `revision_by_hash`, `withdrawal_evidence`, NL-4 |
| Age clock and floor (§7) | NS-6 `age_vectors` |
| The Phase 5 pairing (§8.1) | Phase 5 revision's leakage test |

---

## 11. Storage and packages

**Migration `0022_news_sentiment.sql`** (additive; append-only, the pattern of 0015 and 0017; it waits in
`supabase/migrations_pending/` outside the chain, as 0019 does, C-0053):

| Table | Contents |
|---|---|
| `ns_sources` | A source's id, its governance record's reference and hash, storage level, `availability_policy`, `allowed_hosts`, `onward_disclosure` (default `none`); owner path |
| `ns_fetch_attempts`, `ns_fetch_events` | Every request: source, purpose, host, times; recorded before sending; its outcome, status, sizes and any refused redirect in an event row |
| `ns_ingestion_runs` | Each ingestion run per source and day (ran, partial, did not run): what `partial` reads |
| `ns_articles` | One row per article version: source, reference, title, `published_at` and its precision, `observed_at`, language, body SHA-256, the extracted text only under `full_text`, the version it supersedes |
| `ns_article_observations` | Withdrawals and other later statements about an article |
| `ns_archive_imports` | §9's batch declarations |
| `ns_entity_aliases` | §5.2 |
| `ns_configurations`, `ns_designations` | Processor versions, lexicon hash, taxonomy, weights, half-lives, policies; the configuration in force (owner path) |
| `ns_article_results` | Per (article version, processor version): entities, relevance, events, sentiment; `recorded_at`, configuration id, the alias-set hash (§5.5) |
| `ns_snapshots`, `ns_snapshot_rows` | §8 |

- The migration's text satisfies the guards over all migrations (no binary type, no column named like a file path or
  blob; the text column is `extracted_text`). No credential column: a source that needs a key is refused in v1
  ("no credentialed scraping"), and any future key is server configuration only.
- Packages: `worker/news_sentiment/` (pure processors, decay, snapshot), `worker/news_ingest/` (transport, adapters,
  fetch ledger), `worker/news_store/` (loader, writer, preflight, `verify`). The ingestion transport is the only code
  in the phase that opens a network connection, and a static test enforces it.
- No timer in this design. Ingestion scheduling follows P3's pattern (the database decides, a timer only wakes) and
  is specified with the first approved source.

**Frozen contracts affected:** none modified. The range-stating guards of Phase 3 §13.3 apply to 0022.

---

## 12. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0015)

| ID | Decision | Reason |
|---|---|---|
| P6-D1 | NS reuses Phase 3's temporal model, modes and guards | One meaning of "as of T" across the platform |
| P6-D2 | v1 processors are deterministic and rule-based; English only | Reproducible, inspectable, no provider dependency; a stated coverage limit |
| P6-D3 | A small hand-written lexicon in the repository as the provisional starting point (revision 2) | Something versioned and replaceable to build against, with no third-party terms and no download; the real lexicon is P6-R6 |
| P6-D4 | `metadata_only` is the default storage level | The least that keeps provenance; text only where terms allow |
| P6-D5 | Exact alias matching; ambiguity kept and not scored | A wrong entity is worse than a missed one |
| P6-D6 | Decay is exponential in sessions with per-event half-lives | Deterministic, versioned, and holidays do not age news |
| P6-D7 | No news gives `missing`, never neutral | MA §9's nil-is-not-zero, applied here |
| P6-D8 | Edited or selected archives are admitted to `CURRENT` only | Edited text can contain the future |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P6-R1 | Which sources, each with its governance record (terms read, risk accepted, request discipline). CSE announcements as a source fall under G-1 | Any ingestion |
| P6-R2 | Per source: `metadata_only` or `full_text` | Any ingestion |
| P6-R3 | The purge procedure for a source's deletion request, covering stored text, titles, URLs, derived results, snapshots and backups (revision 2, M9) | Any ingestion |
| P6-R4 | Sinhala and Tamil coverage | A later version |
| P6-R5 | The merge into `main` and the re-staging | Production use |
| P6-R6 | Which sentiment lexicon, and accepting its licence (revision 2, M7) | Any real scoring |
| P6-R7 | The deviation from MA §15 and §39 on sector sentiment until P3-R5 gives a point-in-time sector source (revision 2, M8) | Acceptance of Phase 6 against the MA |
| P6-R8 | The identification sent to news sites (User-Agent, contact address), per source (revision 2, M10) | Any ingestion |
| — | Per source: onward disclosure to an AI provider (part of P6-R1; tied to Phase 7's P7-R1) | Any Route 2 use of that source |
| — | Whether CSE-derived content reproduced in third-party news (market reports quoting prices) falls under G-1; it is not covered by G-1 extension E1 (Minor 8; part of P6-R1 and P6-R2) | Any ingestion of such a source |

---

## 13. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| NS-1 | The governed ingestion transport and fetch ledger, with a scripted fake transport | Unit tests of spacing, back-off, budget, record-before-send, the host allow-list, redirect refusal and the unconditional CSE refusal (§4.1); no real host anywhere in code or tests |
| NS-2 | The adapter interface, article versions and observations; a synthetic-source adapter for tests | Unit tests; NL-1 to NL-4 |
| NS-3 | Aliases and entity identification | Hand-built cases, including ambiguity and ticker-as-word; NL-5 |
| NS-4 | Relevance and events | Hand-built cases per rule |
| NS-5 | Sentiment and deduplication | Hand-scored synthetic articles; negation and intensifier cases |
| NS-6 | Decay and snapshots | Hand-computed vectors; NL-6, NL-7, NL-9, NL-10 |
| NS-7 | Migration 0022, store, preflight, `verify`, runbook | PostgreSQL 17 in CI; append-only for every role; NL-8 |

NS depends on Phase 3's MF-1 and MF-2 (modes and the session sequence) and on F5's issuer identity. The first real
source's adapter is a later step, after P6-R1.

---

## 14. What stays open until real news exists

- Whether any source's terms allow what the design needs, and which sources cover CSE issuers at all.
- Coverage: the share of issuers with any news, and the share of relevant news that is in Sinhala or Tamil.
- Entity precision and recall with exact matching; the story-key rule's error rate.
- Whether a general finance lexicon reads Sri Lankan business English correctly.
- Every provisional constant: weights, thresholds, half-lives, limits.
- Whether sentiment adds anything to Route 1's inputs or to Route 2 (MA §31's comparison decides).

## 15. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new independent subsystem: news ingestion and sentiment |
| 2 | Source of truth | Its own append-only article and result rows; MF's session sequence; F5's issuer identity |
| 3 | Information at its timestamp | Phase 3's modes over each article's two instants |
| 4 | Provenance retained | Snapshot row → stories → article versions → source and fetch attempt |
| 5 | Frozen components | None modified |
| 6 | Migration | Yes: 0022, additive |
| 7 | Leakage prevention | `recorded_at` governs `KNOWN`; versions and designations pinned; edited archives refused; NL-1 to NL-10 |
| 8 | Testing | §10, §13; synthetic articles only; PostgreSQL 17 in CI |
| 9 | Reproduction | Deterministic processors and decay; configuration and input hashes; `verify` |
| 10 | Route 1 and Route 2 | Route 2's news input. Optional features for Phase 5, evaluated with and without. Never an input to market or financial truth |
| 11 | Later evaluation | Through Route 2's evaluation and MA §31's sentiment-versus-no-sentiment comparison |
| 12 | After downtime | Days without ingestion are recorded; snapshots over them are `partial`. Articles fetched late are known late |
| 13 | Failure representation | Fetch outcomes; `unscored`, `ambiguous`, `language_unsupported`; snapshot quality states |
| 14 | Versions | Processor, lexicon, taxonomy and configuration versions on every result and snapshot |
| 15 | Grounded reasoning | A snapshot names the stories, events and scores behind each number, so Route 2 can cite them |

## 16. Audit record

### Audit 1 (revision 1; fresh-context agent, 2026-11-15): REJECT

One Blocker, twelve Majors, twelve Minors. Revision 2's answers:

| # | Finding | Answer |
|---|---|---|
| B1 | Nothing in the news transport stopped it reaching CSE (no host allow-list, redirects, CSE refusal) | §4.1: per-source `allowed_hosts` checked before any socket; no automatic redirects; CSE hosts refused unconditionally; NS-1 tests |
| M1 | How sentiment reaches Phase 5 undefined; Phase 5 excludes it | §8.1: the consumer contract; no NS feature enters ML until Phase 5 adopts it |
| M2 | Story age ambiguous, could be negative | §7: the age clock per mode and evidence kind, instant-to-session rule, floor at 0 |
| M3 | Aliases had no `AVAILABLE` validity; alias change as a new version | §5.2: aliases are dated evidence with `available_at`; not part of the processor version; NL-13 |
| M4 | Results not admitted by their own clock | §5.5: results carry `recorded_at` and the alias-set hash; admission rule; NL-11 |
| M5 | Revisions and withdrawals undefined | §4.3: re-fetch policy, revision by hash, withdrawal evidence, effect after admission |
| M6 | Deduplication cannot work as written | §6: a true key; story ids per snapshot over admitted articles; NL-12 |
| M7 | Lexicon licence and download taken as delegated | P6-D3 now a hand-written lexicon; P6-R6 reserved |
| M8 | Sector sentiment deviation unreserved | P6-R7 |
| M9 | Purge trigger set too narrowly | P6-R3 before any ingestion, covering derived rows and backups |
| M10 | Governance record lacked onward disclosure and identification | §4.1's fields; P6-R8; `ns_sources.onward_disclosure` |
| M11 | Requirements without named tests | §10's requirement-to-test map |
| M12 | Processor versions over history vs Phase 5's one configuration | §8.1: one NS configuration per window; `full_text` needed for long ML histories |
| Minors 1-12 | Quality order; `max_age_sessions` ≥ 21; ingestion runs and fetch events; NL-6 naming; availability lag; NL-10 wording; LLM processor scope; CSE-derived news content; feature registry; pending migration; "reads nothing from Route 1"; Route 2 independence | Answered in §8, §7, §11, §9, §10, §5.5, §12, §1 |

### Audit 2 (revision 2; fresh-context agent, 2026-11-15): ACCEPT

No Blocker; B1 and the twelve Majors of audit 1 confirmed resolved. Seven new Majors, to be fixed before NS-1
(C-0037):

| # | Finding | Disposition |
|---|---|---|
| N1 | A third party could fetch from CSE on our behalf (relays, archive "save" endpoints); archive acquisition not local-only | **Fixed** in §4.1 |
| N2 | A later alias end and a withdrawal have no admission clocks | Carried: define both as observation rows with their own two clocks (a withdrawal's `available_at` = the confirming re-fetch's `observed_at`); NL tests |
| N3 | Stored-result admission conflicts with re-processed history in `KNOWN`; an admitted article with no result undefined | Carried: a pure processor may be recomputed at query time from inputs admitted at T; the own-clock rule applies to stored results used as they are; define the no-result case |
| N4 | Days before a source existed look like "no news" | Carried: no run row after a source's start is "not run"; the active sources recorded on every snapshot; a `not_collected` state before coverage; §8.1 pairing labels or refuses such rows |
| N5 | Raw body hashes make spurious revisions; a revision resets age | Carried: hash the normalised extracted text; every version inherits the article's first availability for age |
| N6 | Withdrawals rarely detected under the re-fetch schedule | Carried: a confirming re-fetch after a first 404; the detection window stated in §14 |
| N7 | The decay formula undefined for a story's relevance and event type | Carried: define the story's ρ and the half-life rule, with vectors |
| Minors 1-14 | Quality order (Phase 3's: untrusted, conflicting, missing, unlabelled, partial, stale, fresh); MF's security identity among permitted inputs; NL-11 to NL-13 placement; "no real host" wording; earlier-T pairing; grouping window; unknown dates; automatic aliases; `onward_disclosure` values; Phase 7's boundary for titles; one `allowed_hosts`; keyed sources as a v1 limit; converse under `metadata_only`; withdrawn text retention | Carried, fixed with the Majors |

