# Phase 6: news and sentiment (design)

**Status: DESIGN, revision 1 (2026-11-11). Not implemented. Not accepted. Not audited yet.** Written by the cloud
session on the branch `integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no
contact with any news source, no CSE contact and no change to `main`.

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

1. **Sentiment is an independent subsystem** (MA §15). It reads nothing from Route 1 and writes nothing into market
   or financial truth. Its only consumers are Route 2 (Phase 7) and, as optional features, Phase 5.
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
| Company, sector and macro sentiment | §39 | Company and market: §8. **Sector: not met in v1**, for the reason Phase 3 gives (no point-in-time sector source; P3-R5). Macro: as the market-wide snapshot |
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
  accepts, the request discipline (rate, identification, back-off, stop conditions), what may be stored (P6-R2) and
  for how long. Without the record, the adapter refuses to run.
- **An adapter** implementing one interface: `list_new(since) -> item references` and `fetch(reference) -> raw item`.
  All HTTP goes through one governed transport of this phase, built on the pattern of P2's: one request at a time, a
  minimum spacing per source, back-off, an identifiable User-Agent, no proxies, no credentialed scraping, a daily
  budget, every attempt recorded before it is sent.

CSE's own announcements, if the owner wants them as a source, are CSE contact and fall under G-1 and its extension
decisions, not under this phase's delegation (P6-R1).

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

---

## 5. Processors (NS-3 to NS-5)

Each processor is a pure function with an id and a version; its output rows carry both. A change of rules, lexicon or
alias evidence is a new version. Results are never overwritten: a new version adds rows.

### 5.1 Language

v1 processes **English** text only. Articles in Sinhala or Tamil are recorded with their language and marked
`language_unsupported`; they contribute to no score. That is a stated coverage limit (§14), not a silent drop: the
snapshot reports how many admitted articles were unsupported.

### 5.2 Entity identification (`ns.entity.1`)

- **Aliases are evidence, as of T.** `ns_entity_aliases` (append-only, owner path) maps strings to issuers: the
  issuer's names as F5's issuer evidence records them, its tickers from MF's security identity (Phase 3 §8.1), and
  owner-entered aliases with a source reference. An alias applies from its `recorded_at` in `KNOWN`.
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
  the repository with its provenance and licence recorded; a general finance lexicon is the starting point
  (`provisional`, P6-D3).
- A label `positive`, `negative`, `neutral` from fixed thresholds, and `unscored` when fewer than the minimum number
  of lexicon terms were found. `unscored` is not `neutral`: it contributes nothing.
- A market-wide score is computed the same way around market terms.

**Model-based processors.** A later `ns.sentiment.2` may use a model, including an LLM. It then runs through Phase
7's provider boundary (adapter, registered configuration, recorded request and response, no silent fallback), and its
outputs carry the provider configuration version. v1 contains no such call, and the existing guard
`tests/test_ai_provider_boundary.py` stays as it is.

---

## 6. Deduplication and syndication

The same story appears on several sources and is edited over time. Counting each copy inflates a score.

- A **story key** is computed per article from normalised title tokens and the issuer set (a deterministic
  shingle-and-threshold rule, versioned). Articles sharing a key within a window are one story.
- A snapshot counts each story once per issuer: the earliest admitted article of the story sets its age; the
  story's score is the mean of its admitted articles' scores; the number of sources is kept as a separate field.
- The rule is deliberately conservative and `provisional`; its error rate is measurable only on real news (§14).

---

## 7. Time decay (`ns.decay.1`)

For a story with raw sentiment s, relevance ρ, and age a (in **sessions** of MF's session sequence between the
story's availability under the mode and the snapshot's D\*, plus the fraction of the current day):

```
effective = s × ρ × 2^(−a / half_life[event_type])
```

- This is MA §15's conceptual form with an exponential decay. Half-lives are per event type, constants of the
  version, `provisional` (for example 5 sessions for `earnings`, 2 for `other`).
- Age is counted in sessions so that a weekend or a holiday does not age news the market could not react to.
- A story older than `max_age_sessions` contributes nothing and is not loaded.
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
- **Quality states** (Phase 3 §10.4's order): `fresh`; `stale` (newest story older than the policy's limit);
  `partial` (ingestion gaps inside the window: the snapshot knows the days on which a source's ingestion did not
  run, as MF knows unknown dates); `conflicting` (ambiguous entity mentions above the policy's share);
  `unlabelled` (mixed processor versions in the window, or unsupported-language articles above the policy's share);
  `missing`.
- Each row stores the ids of the stories and article versions it used, the processor versions, the configuration id
  and a `row_hash`. That is MA §42's chain: forecast → sentiment feature → news item → source.
- **Coverage is uneven by construction.** Large issuers are written about; small ones are not. A model given these
  features can learn "is covered" as a proxy for size. The snapshot therefore always carries the counts, and Phase 5
  must treat sentiment features as optional inputs evaluated by the sentiment-versus-no-sentiment comparison MA §31
  requires.

---

## 9. History for backtests

- **From ingestion start:** `KNOWN`, point-in-time.
- **Before it:** only what an archive provides, admitted in `AVAILABLE` as `reconstructed`. An import batch declares,
  or is refused: the source and terms reference; whether `published_at` is the original or a last-modified time;
  whether the archive holds articles as first published or as later edited (`as_published` or `as_edited`); and
  whether it is complete for its date range or a selection.
- An `as_edited` or selected archive is admitted to `CURRENT` only, labelled, because the text of an edited article
  can contain what happened afterwards.
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
| NL-6 | A new processor version, lexicon or configuration designated after T | Results pinned to the versions in force at T |
| NL-7 | A later session or closure | Ages and decay at T |
| NL-8 | A `CURRENT` snapshot offered to a point-in-time consumer; an `as_edited` archive offered to `AVAILABLE` | Refused |
| NL-9 | An article about another issuer | This issuer's snapshot at T, except through a shared story key, which is asserted separately |
| NL-10 | Any market, financial or label value | No sentiment feature depends on it: the NS packages cannot import MF's target module, F7 or the ML packages |

---

## 11. Storage and packages

**Migration `0022_news_sentiment.sql`** (additive; append-only, the pattern of 0015 and 0017):

| Table | Contents |
|---|---|
| `ns_sources` | A source's id, its governance record's reference and hash, storage level, `availability_policy`; owner path |
| `ns_fetch_attempts` | Every request: source, purpose, times, outcome, status, sizes; recorded before sending, completed by an event row |
| `ns_articles` | One row per article version: source, reference, title, `published_at` and its precision, `observed_at`, language, body SHA-256, the extracted text only under `full_text`, the version it supersedes |
| `ns_article_observations` | Withdrawals and other later statements about an article |
| `ns_archive_imports` | §9's batch declarations |
| `ns_entity_aliases` | §5.2 |
| `ns_configurations`, `ns_designations` | Processor versions, lexicon hash, taxonomy, weights, half-lives, policies; the configuration in force (owner path) |
| `ns_article_results` | Per (article version, processor version): entities, relevance, events, sentiment |
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
| P6-D3 | A general finance lexicon as the provisional starting point | Something versioned and replaceable to build against |
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
| P6-R3 | The purge procedure for a source's deletion request | Before the first `full_text` source |
| P6-R4 | Sinhala and Tamil coverage | A later version |
| P6-R5 | The merge into `main` and the re-staging | Production use |

---

## 13. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| NS-1 | The governed ingestion transport and fetch ledger, with a scripted fake transport | Unit tests of spacing, back-off, budget, record-before-send; no real host anywhere in code or tests |
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

*(Filled in by the audit under C-0005.)*
