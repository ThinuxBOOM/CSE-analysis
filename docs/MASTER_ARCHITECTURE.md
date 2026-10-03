# CSE Financial Intelligence & Forecasting Platform

## Master Architecture & Implementation Specification

**Status:** Master source-of-truth architecture\
**Runtime:** Local Linux home server\
**Database:** Local PostgreSQL\
**Source control:** GitHub only\
**Primary external sources:** Colombo Stock Exchange (CSE), approved
financial/news sources, Gemini API

------------------------------------------------------------------------

# 1. Project Definition

This project is a **locally hosted, auditable CSE financial intelligence
and forecasting platform**.

Its complete lifecycle is:

``` text
CSE financial reports ───────┐
                             │
CSE daily market data ───────┤
                             ▼
                    Source acquisition
                             │
                             ▼
                  Validated financial/market
                       source evidence
                             │
                             ▼
                   Historical feature data
                             │
                  ┌──────────┴──────────┐
                  │                     │
                  ▼                     ▼
          ROUTE 1: QUANTITATIVE   MARKET NEWS/SENTIMENT
          deterministic engines         │
                 + ML                    │
                  │                      │
                  ▼                      ▼
          Quantitative prediction     Gemini API
                  │                      │
                  │                      ▼
                  │                 AI prediction
                  │                      │
                  └──────────┬───────────┘
                             ▼
                  User-facing forecast
                  • both predictions
                  • reasoning
                  • uncertainty
                  • agreement/disagreement
                             │
                             ▼
                    Actual market outcome
                             │
                   ┌─────────┴─────────┐
                   ▼                   ▼
              Evaluate Route 1    Evaluate Route 2
                   │                   │
                   └─────────┬─────────┘
                             ▼
                    Performance history
                             │
                             ▼
                Backtesting / monitoring
                             │
                             ▼
                 Controlled adaptation
                             │
                             ▼
                   New model versions
```

The project is **not** merely an ML model and is **not** merely a Gemini
wrapper. The data, provenance, deterministic analysis, ML, sentiment,
AI, evaluation and backtesting layers are all first-class components.

------------------------------------------------------------------------

# 2. Canonical Prediction Architecture

The canonical requirement is:

> **Two independently produced prediction routes. Route 1 is the
> quantitative route, using deterministic financial/data-analysis
> engines and ML models to produce a raw quantitative prediction. Route
> 2 takes the quantitative inputs together with independently generated
> market-sentiment information and passes them to Gemini to produce an
> AI prediction. The final forecast presentation displays both
> predictions and a statement describing their degree of
> agreement/disagreement. Each route is independently retained and
> evaluated against eventual outcomes.**

The routes must never be collapsed into a single unexplained prediction.

Route 1 remains useful even if Route 2 exists. Route 2 remains useful
even if Route 1 is stronger historically in some periods.

------------------------------------------------------------------------

# 3. Core Objectives

The finished platform must:

1.  collect approximately five years of historical CSE financial
    reports;
2.  continuously discover/process new filings;
3.  collect daily CSE market data;
4.  build validated financial facts and market observations;
5.  create point-in-time feature datasets;
6.  run deterministic financial/data-analysis engines;
7.  run ML models;
8.  produce Route 1 predictions;
9.  ingest relevant market/company/economic news;
10. extract entities, relevance and events;
11. calculate time-decayed sentiment;
12. provide quantitative inputs + sentiment to Gemini;
13. produce Route 2 predictions;
14. explain both predictions using their actual inputs;
15. show agreement/disagreement;
16. predict multiple horizons;
17. evaluate both routes against actual outcomes;
18. preserve all forecast/evaluation/model provenance;
19. provide a historical backtesting laboratory;
20. prevent look-ahead, survivorship and data leakage;
21. adapt models through controlled versioned retraining/recalibration;
22. operate reliably despite local-server downtime;
23. preserve backups and restore capability.

------------------------------------------------------------------------

# 4. Forecast Targets

The forecasting system supports four horizons:

  Horizon   Meaning
  --------- -------------------------------
  1 day     next trading day
  1 week    approximately 5 trading days
  2 weeks   approximately 10 trading days
  1 month   approximately 21 trading days

The actual trading calendar, not calendar-day arithmetic, determines the
final target date.

For every horizon, the prediction contract should contain:

-   probability of rise;
-   probability of fall;
-   predicted direction;
-   expected percentage change/return;
-   predicted future price where meaningful;
-   expected volatility;
-   uncertainty/range;
-   supporting evidence;
-   route-specific reasoning.

The primary numerical return can be represented as:

``` text
return = (future_close - reference_close) / reference_close
```

where `reference_close` is the last eligible close available at the
prediction cutoff.

The target definitions must be versioned.

------------------------------------------------------------------------

# 5. Daily Market Forecast Cycle

The primary production cycle is **daily**, not intraday.

Conceptually:

``` text
Trading day D
    ↓
capture/validate D market data
    ↓
D closes
    ↓
prepare point-in-time dataset
    ↓
generate forecasts
    ↓
predict D+1, D+5, D+10, D+~21
```

The system therefore uses the previous completed trading day as its main
market-data basis.

Market features may include:

-   close;
-   high;
-   low;
-   return;
-   turnover;
-   volume;
-   trade count;
-   volatility;
-   momentum;
-   rolling returns;
-   rolling volume;
-   drawdown;
-   liquidity/activity;
-   market/index context;
-   sector context.

------------------------------------------------------------------------

# 6. Security Universe

The platform should cover companies with:

-   sufficient financial history;
-   usable financial statements;
-   sufficient daily market observations;
-   meaningful trading activity.

It must **not** restrict itself to only large companies.

Eligibility should be based on measurable data/activity criteria rather
than market capitalization alone.

A security may therefore move through:

``` text
CSE listed
→ financially eligible
→ market-data eligible
→ forecast eligible
```

Eligibility thresholds must be versioned and empirically tested.

Inactive/delisted securities must remain in historical data for
backtesting, preventing survivorship bias.

------------------------------------------------------------------------

# 7. Financial Data Pipeline

## 7.1 Initial backfill

The initial dataset covers approximately five years of CSE financial
reports.

This data supports:

-   financial trend analysis;
-   ratios;
-   earnings analysis;
-   balance-sheet analysis;
-   cash-flow analysis;
-   ML training;
-   deterministic models;
-   historical backtesting.

## 7.2 Continuous collection

After the initial backfill:

``` text
filing discovery
→ retrieval
→ interpretation
→ extraction
→ candidate generation
→ validation
→ reconciliation
→ economic facts
→ feature generation
```

## 7.3 PDFs

PDFs are temporary extraction inputs.

They are not the canonical financial database.

The project retains structured evidence and provenance, not permanent
PDF binaries in PostgreSQL.

------------------------------------------------------------------------

# 8. Existing Frozen Foundation

The following stages are already established and should not be casually
rewritten.

## Stage E --- Market Capture

Frozen/accepted.

Includes:

-   `allSecurityCode`;
-   `tradeSummary`;
-   `companyInfoSummery`;
-   multi-company capture;
-   market mapping;
-   EOD completeness;
-   market reconciliation.

EOD data includes:

-   close;
-   high;
-   low;
-   turnover;
-   share volume;
-   trade count.

`tradeSummary.open` is treated as a session-scoped reference/open value;
exact opening-auction semantics remain unresolved.

## F1 --- Filing Discovery

Frozen/accepted.

CSE discovery uses:

-   `/api/financials`;
-   `/api/getFinancialAnnouncement`;
-   CSE CDN resources.

## F2 --- PDF Retrieval

Frozen/accepted.

Uses temporary files, response validation, PDF signature/EOF checks,
ETag/SHA256 and cleanup.

## F3 --- Document/Period Interpretation

Frozen/accepted.

The document is the semantic period authority.

Important cases include:

-   Q2/Q3 standalone + YTD;
-   Q4 interim 3M + 12M;
-   annual reports containing quarterly figures;
-   documented/inferred/conflicting/no FYE.

## F4 --- Structured Extraction

Frozen/accepted.

Primary extraction uses Poppler `-bbox-layout`, coordinate/right-edge
mapping, transient cells, XLSX cross-checking and OCR trust rules.

## F5 --- Financial Candidates

Frozen/accepted.

``` text
F4 cells
→ statement extracts
→ columns/rows
→ financial_fact_candidates
→ F6 validation/reconciliation
```

Preserves:

-   instant/duration;
-   duration;
-   current/comparative;
-   Group/Company/Bank/unknown scope;
-   operations;
-   attribution;
-   concept;
-   exact Decimal values;
-   provenance.

No silent sign/scale/currency transformation.

------------------------------------------------------------------------

# 9. Financial Truth Layer

The financial database distinguishes:

``` text
source observation
→ extraction
→ candidate
→ validation
→ reconciliation
→ economic fact
```

Economic identity includes:

-   issuer;
-   concept;
-   period kind;
-   period end;
-   duration;
-   scope;
-   operations;
-   maturity where applicable;
-   currency.

Rules:

-   Group/Company/Bank values are never silently merged;
-   unlabeled scope remains `unlabelled`;
-   reported currency remains explicit;
-   no silent FX conversion;
-   printed nil/zero is distinct from missing;
-   conflicts are preserved;
-   no arbitrary winner is selected;
-   errata/amendments are represented through version/supersession
    logic.

Possible reconciliation states (`f6.reconciliation.1`; defined in
`docs/F6.2_DESIGN.md` §7):

``` text
single_source
corroborated
conflicting
```

When the state is not `conflicting`, the result also carries
`value_kind = numeric | nil`. A nil fact (a printed dash, or the words
`Nil` / `None`) is therefore `single_source` or `corroborated` with
`value_kind = nil`; there is no separate `nil_reported` state. Nil is
never zero: nil against a numeric value, including a printed `0`, is
`conflicting`.

------------------------------------------------------------------------

# 10. Two-Clock Rule

Every important information source has two time concepts:

### Publication/availability time

When the information became public.

### System-knowledge time

When this project actually discovered/retrieved/recorded it.

They can differ because of downtime or delayed discovery.

Backtesting must reconstruct what was knowable at the historical cutoff.

------------------------------------------------------------------------

# 11. Point-in-Time / Anti-Leakage Architecture

This is a critical requirement.

Every forecast has:

``` text
forecast_timestamp
information_cutoff_timestamp
market_data_cutoff
financial_data_cutoff
news_data_cutoff
sentiment_data_cutoff
```

A forecast may use only information available at or before the cutoff.

Examples of prohibited leakage:

-   using a report before its publication;
-   using tomorrow's close;
-   using future news;
-   using later revised facts as though they were already known;
-   using future corporate actions;
-   using future training observations;
-   calculating a feature using future data.

The system must contain explicit automated leakage tests.

------------------------------------------------------------------------

# 12. Route 1 --- Quantitative Engine

Route 1 consists of:

``` text
deterministic financial/data-analysis engines
                    +
                   ML
                    ↓
          quantitative prediction
```

## 12.1 Deterministic engines

Potential families:

### Fundamental

-   revenue growth;
-   earnings growth;
-   EPS;
-   margins;
-   ROE/ROA;
-   leverage;
-   liquidity;
-   cash flow;
-   working capital;
-   dividends;
-   valuation;
-   balance-sheet strength.

### Market/statistical

-   momentum;
-   trend;
-   volatility;
-   volume;
-   turnover;
-   drawdown;
-   mean reversion;
-   correlations;
-   market-relative performance;
-   sector-relative performance.

Each engine produces structured, versioned outputs.

## 12.2 ML

Potential model families may include:

-   regression;
-   classification;
-   gradient/tree models;
-   time-series models;
-   neural models where justified.

No algorithm is permanently mandated.

Every model requires:

-   ID;
-   version;
-   training dataset;
-   feature schema;
-   training cutoff;
-   validation period;
-   hyperparameters;
-   artifact hash;
-   metrics;
-   deployment status.

------------------------------------------------------------------------

# 13. Route 1 Combination

The combination of deterministic engines and ML is itself versioned.

The system must retain component outputs, not only the final number.

This allows the platform to answer:

> Which deterministic signals and ML features contributed to this
> prediction?

Deterministic engines are first-class baselines, not fallbacks.

------------------------------------------------------------------------

# 14. Route 1 Reasoning

The reasoning must be grounded in the actual forecast inputs.

It can summarize:

-   revenue/earnings trends;
-   margin changes;
-   valuation;
-   leverage;
-   cash flow;
-   price momentum;
-   volatility;
-   volume;
-   deterministic signals;
-   ML feature contributions.

Appropriate explainability methods may include feature importance,
permutation importance, SHAP or equivalent techniques.

The system should present evidence summaries, not hidden model
chain-of-thought.

------------------------------------------------------------------------

# 15. News and Sentiment System

Sentiment is an independent subsystem.

Pipeline:

``` text
news source
→ article record
→ entity identification
→ relevance
→ event extraction
→ sentiment
→ time decay
→ company/sector sentiment snapshot
```

Each news item should retain:

-   source;
-   publication time;
-   observed time;
-   entity;
-   event;
-   relevance;
-   sentiment;
-   processing version.

Possible structured sentiment fields:

``` text
sentiment_score
sentiment_label
relevance_score
entity
event_type
published_at
observed_at
```

Time decay must be deterministic and versioned.

A simplified conceptual structure is:

``` text
effective_sentiment
=
raw_sentiment × relevance × decay(age)
```

The exact formula is a later controlled implementation decision.

------------------------------------------------------------------------

# 16. Route 2 --- Gemini

Route 2 receives:

-   validated quantitative inputs;
-   relevant Route 1 quantitative outputs;
-   market/company/economic news;
-   entity relevance;
-   events;
-   time-decayed sentiment;
-   data-quality warnings;
-   explicit information cutoff.

Then:

``` text
structured inputs
→ Gemini
→ AI prediction
→ reasoning
```

Gemini is an interpretation/prediction layer, not the financial truth
authority.

Gemini must never be allowed to silently create canonical financial
facts.

------------------------------------------------------------------------

# 17. Gemini Request/Response Provenance

Each Gemini run must retain:

-   provider;
-   model identifier;
-   timestamp;
-   prompt version;
-   input record IDs;
-   input hash;
-   raw response;
-   parsed output;
-   usage/cost data where available;
-   error state;
-   response hash.

Prompt changes create new prompt versions.

Historical forecasts retain the original prompt/model metadata.

------------------------------------------------------------------------

# 18. Route 2 Reasoning

The AI reasoning must be evidence-grounded.

It should explain:

-   which quantitative factors matter;
-   which news matters;
-   which sentiment matters;
-   which events matter;
-   why the AI prediction differs from Route 1;
-   important uncertainty.

Example:

``` text
Route 1 expected return: +4.1%
Route 2 expected return: +1.6%

Route 1 is driven primarily by improving earnings,
valuation and momentum.

Route 2 is more conservative because recent sector/regulatory
news has negative sentiment and high relevance.
```

This is a concise evidence explanation, not hidden chain-of-thought.

------------------------------------------------------------------------

# 19. Final User Forecast

The user must see both routes.

For each horizon:

``` text
Security
Reference date
Information cutoff

ROUTE 1 — QUANTITATIVE
P(rise)
P(fall)
direction
expected return
expected price
expected volatility
uncertainty
reasoning

ROUTE 2 — AI + SENTIMENT
P(rise)
P(fall)
direction
expected return
expected price
expected volatility
uncertainty
reasoning

AGREEMENT
direction agreement
return difference
probability difference
volatility difference
overall divergence

DATA WARNINGS
```

------------------------------------------------------------------------

# 20. Agreement/Disagreement

Agreement is a first-class output.

Compare:

-   direction;
-   probabilities;
-   expected return;
-   price;
-   volatility;
-   uncertainty.

The system should show meaningful divergence rather than hiding it
behind a single ensemble result.

An agreement record may contain:

``` text
direction_agreement
return_difference
probability_difference
volatility_difference
overall_divergence
agreement_version
```

------------------------------------------------------------------------

# 21. Confidence and Uncertainty

Confidence must be statistically grounded.

Possible outputs:

-   probability;
-   prediction interval;
-   expected error range;
-   calibrated confidence;
-   historical error band.

Gemini must not invent a statistically meaningful confidence score
without a defined methodology.

------------------------------------------------------------------------

# 22. Forecast Eligibility

The system must sometimes refuse to forecast.

Possible blockers:

-   insufficient financial history;
-   insufficient market history;
-   low activity;
-   stale data;
-   missing critical features;
-   unresolved critical financial conflict;
-   insufficient news where Route 2 requires it;
-   model outside validated domain;
-   known data-quality incident.

Possible forecast states:

``` text
generated
blocked
insufficient_data
invalid_input
not_applicable
```

No prediction is better than fabricated precision.

------------------------------------------------------------------------

# 23. Forecast Storage

Each forecast retains:

### Identification

-   forecast ID;
-   issuer/security;
-   horizon;
-   timestamp;
-   information cutoff.

### Route 1

-   deterministic engine versions;
-   ML model version;
-   feature dataset version;
-   probability;
-   direction;
-   return;
-   price;
-   volatility;
-   uncertainty;
-   feature contributions.

### Route 2

-   Gemini model;
-   prompt version;
-   news/sentiment snapshot;
-   prediction;
-   reasoning;
-   uncertainty.

### Agreement

-   comparison values;
-   agreement version.

### Provenance

-   input record IDs;
-   dataset hashes;
-   model hashes;
-   prompt hash.

------------------------------------------------------------------------

# 24. Outcome Evaluation

At the end of each horizon:

``` text
forecast
→ wait for target date
→ obtain actual market data
→ calculate actual return/direction/volatility
→ evaluate Route 1
→ evaluate Route 2
```

Evaluation is a separate immutable record.

Predictions are never overwritten.

------------------------------------------------------------------------

# 25. Evaluation Metrics

Evaluation must be deeper than "correct/incorrect".

## Direction

-   directional accuracy;
-   confusion matrix;
-   balanced metrics where appropriate.

## Probability

-   Brier score;
-   log loss;
-   calibration;
-   reliability analysis.

## Return

-   MAE;
-   RMSE;
-   median absolute error;
-   signed error;
-   error distribution.

## Price

-   absolute error;
-   percentage error.

## Volatility

-   forecast error;
-   realized-vs-predicted volatility.

All metrics are separated by:

-   horizon;
-   security;
-   sector;
-   liquidity/activity class;
-   model version;
-   market regime;
-   time period.

------------------------------------------------------------------------

# 26. Independent Route Evaluation

Route 1 and Route 2 must be evaluated independently.

Example:

``` text
Route 1
prediction = +4.2%
actual     = +3.1%
error      = -1.1%

Route 2
prediction = +2.8%
actual     = +3.1%
error      = +0.3%
```

The system records both.

It does not automatically replace Route 1 because Route 2 happened to
perform better in this instance.

------------------------------------------------------------------------

# 27. Adaptation

Adaptation means controlled improvement.

It does **not** mean:

``` text
prediction wrong today
→ mutate model tonight
```

Instead:

``` text
forecast outcomes
→ evaluation history
→ performance/drift monitoring
→ candidate retraining/recalibration
→ chronological validation
→ compare to incumbent
→ version new model
→ controlled deployment
```

A production model is never silently modified.

------------------------------------------------------------------------

# 28. Model Registry

Registry entries require:

-   model ID;
-   version;
-   training data version;
-   feature version;
-   training cutoff;
-   validation period;
-   test period;
-   artifact hash;
-   metrics;
-   creation timestamp;
-   deployment status.

Statuses:

``` text
candidate
validated
production
retired
rejected
```

------------------------------------------------------------------------

# 29. Backtesting Laboratory

The backtesting lab is mandatory.

Its fundamental question is:

> **If the system had existed on a previous date, what would it have
> predicted using only the information that was actually available at
> that time?**

This is not the same as running today's model against today's cleaned
database.

------------------------------------------------------------------------

# 30. Historical Replay

For a historical date `T`:

``` text
T
↓
reconstruct information available at T
↓
construct features
↓
run Route 1
↓
construct historical sentiment/news
↓
run Route 2
↓
store simulated forecasts
↓
advance to target horizon
↓
retrieve actual outcome
↓
evaluate
```

The system then advances to the next historical prediction date.

------------------------------------------------------------------------

# 31. Backtesting Modes

The lab should support:

-   full historical replay;
-   point-in-time single-date prediction;
-   single-security replay;
-   whole-universe replay;
-   model comparison;
-   route comparison;
-   feature ablation;
-   deterministic-vs-ML comparison;
-   sentiment/no-sentiment comparison;
-   market-regime analysis.

------------------------------------------------------------------------

# 32. Walk-Forward Validation

The primary research method is chronological.

For example:

``` text
train → validate → predict future
                 ↓
              evaluate
                 ↓
expand training window
                 ↓
predict next period
```

Future observations must never enter earlier training/validation
windows.

------------------------------------------------------------------------

# 33. Backtest Data Availability

A historical backtest must use:

-   publication time;
-   system knowledge time;
-   historical market data;
-   historical financial versions;
-   historical news;
-   historical sentiment;
-   historical security universe.

It must not query today's "latest" value and pretend it existed
historically.

------------------------------------------------------------------------

# 34. Survivorship Bias

Backtesting must include securities that were historically eligible,
even if they later became inactive or delisted.

Otherwise:

``` text
only today's surviving companies
```

would make historical performance look artificially strong.

------------------------------------------------------------------------

# 35. Selection Bias

The historical universe must reflect what could actually have been known
at that date.

Today's liquidity ranking or company list must not silently define
yesterday's universe.

------------------------------------------------------------------------

# 36. Data Snooping

The final test period must remain isolated.

Repeated optimization against the same test set is prohibited.

Feature/model selection belongs in training/validation periods.

------------------------------------------------------------------------

# 37. Market Regimes

Evaluation should eventually be segmented into:

-   high volatility;
-   low volatility;
-   rising markets;
-   falling markets;
-   sideways markets;
-   market stress;
-   sector-specific events.

This helps determine when each route is useful.

------------------------------------------------------------------------

# 38. Historical AI Backtesting

Gemini introduces an additional reproducibility issue because provider
behavior can change.

For historical Route 2 results, store:

-   model;
-   prompt;
-   exact input;
-   input hash;
-   raw response;
-   parsed prediction;
-   timestamp.

Historical AI results are immutable records even if the provider later
changes its model.

------------------------------------------------------------------------

# 39. Financial Feature Store

The feature layer should include versioned:

## Fundamental

-   revenue;
-   earnings;
-   EPS;
-   margins;
-   assets;
-   liabilities;
-   equity;
-   debt;
-   cash;
-   operating cash flow;
-   free cash flow where derivable;
-   working capital;
-   dividends;
-   valuation.

## Market

-   returns;
-   volatility;
-   momentum;
-   turnover;
-   volume;
-   trade count;
-   liquidity;
-   drawdown;
-   market-relative features;
-   sector-relative features.

## Sentiment

-   sentiment;
-   relevance;
-   event;
-   recency;
-   entity;
-   company sentiment;
-   sector sentiment;
-   macro sentiment.

Every feature requires:

-   source;
-   formula;
-   units;
-   availability rule;
-   version;
-   quality state.

------------------------------------------------------------------------

# 40. Corporate Actions

Future market architecture must account for:

-   splits;
-   consolidations;
-   rights;
-   dividends;
-   bonus shares;
-   ticker changes;
-   mergers;
-   delistings.

Raw market observations and analysis-adjusted series must be
distinguishable.

Adjustments are explicit and versioned.

------------------------------------------------------------------------

# 41. Data Quality

Quality states should distinguish:

``` text
fresh
stale
partial
conflicting
missing
unlabelled
untrusted
```

A critical conflict can block forecasting.

A noncritical warning can be displayed to the user.

------------------------------------------------------------------------

# 42. Provenance

A financial forecast should be traceable:

``` text
forecast
→ feature
→ economic fact
→ source observation
→ filing
→ CSE source
```

Market:

``` text
forecast
→ market feature
→ daily observation
→ capture run
→ raw CSE response
```

Sentiment:

``` text
forecast
→ sentiment feature
→ news item
→ source
```

------------------------------------------------------------------------

# 43. Local Infrastructure

Target environment:

-   Ubuntu 24.04 LTS;
-   PostgreSQL 17;
-   Python environment with pinned dependencies;
-   systemd;
-   local Unix socket/peer authentication;
-   local server storage;
-   encrypted off-site backup.

GitHub remains source control only.

------------------------------------------------------------------------

# 44. Scheduler

P3 establishes:

> **PostgreSQL is the logical scheduler authority; systemd only wakes
> it.**

Persistent scheduler state includes:

-   work items;
-   events;
-   leases;
-   heartbeats;
-   run states.

The scheduler supports:

-   catch-up;
-   missed runs;
-   stale leases;
-   duplicate timer protection;
-   manual/scheduled concurrency protection.

Catch-up must never burst beyond the CSE request envelope.

------------------------------------------------------------------------

# 45. Trading Calendar

Calendar semantics are Colombo-local.

The system distinguishes:

-   expected;
-   open/evidenced;
-   closed/evidenced.

A failed capture must never itself prove a market closure.

The scheduler must use explicit trading-date keys.

------------------------------------------------------------------------

# 46. CSE Capture Governance

Governance state:

``` text
G-1 = accepted_risk
```

This is **not** equivalent to CSE permission, legal clearance or
licensing.

Established controls include:

-   sparse sequential polling;
-   minimum request spacing;
-   backoff;
-   identifiable User-Agent;
-   no proxies/IP rotation;
-   no bypass;
-   no redistribution;
-   no raw CSE data in Git;
-   personal/noncommercial use;
-   stop on objection;
-   purge path.

If the project becomes commercial/public or CSE objects, operations must
stop pending review.

------------------------------------------------------------------------

# 47. Market Capture Request Budget

The established design is approximately:

-   `allSecurityCode`: once/day;
-   `tradeSummary`: once/day plus controlled retries;
-   `companyInfoSummery`: only where required;
-   limited cross-checking;
-   approximately 55--65 requests/day under the original scope;
-   at least 1.5 seconds between requests;
-   weekly full metadata refresh.

Exact operational parameters remain governed by the P2/P3 runbooks.

------------------------------------------------------------------------

# 48. Raw Market Archive

Raw CSE market responses are retained as source evidence.

Archive lifecycle:

``` text
request intent
→ spool
→ fsync/atomic rename/hash
→ PostgreSQL archive
→ session evidence
→ raw observation
→ canonicalization
```

Capture success is independent from canonicalization and backups.

------------------------------------------------------------------------

# 49. Backup Architecture

Backups remain independent of capture.

Core sequence:

``` text
spool
→ PostgreSQL commit
→ capture finalization
→ local dump
→ encrypted off-site sync
→ restore check
```

Requirements:

-   local dumps;
-   backup ledger;
-   encrypted off-site storage;
-   restore testing;
-   age monitoring;
-   free-space monitoring;
-   corruption checks.

Backup failure must not redefine whether the CSE capture itself
succeeded.

------------------------------------------------------------------------

# 50. Security Roles

Established local role model:

-   `cse_owner`;
-   `cse_migrator`;
-   `cse_worker`;
-   `cse_reader`;
-   `cse_backup`.

Production services must not be:

-   superuser;
-   CREATEDB;
-   CREATEROLE;
-   REPLICATION;
-   BYPASSRLS.

Worker code must not operate as the database owner.

------------------------------------------------------------------------

# 51. Migration Lineage Audit

Current migration sequence reaches 0016 (Phase 2 HB-1's additive
`0016_historical_backfill_ledger.sql`, after F6.4's additive
`0015_financial_truth_persistence.sql`); `0006` remains unused.

Historical notes:

-   `0006` was associated with the old Supabase security/RLS boundary
    and was never used as the local security migration;
-   later local security work starts with 0009;
-   an older deployment/edit-in-place concern existed around 0007.

This must be forensically verified before final architecture freeze.

Audit requirements:

1.  inspect 0001--0014;
2.  determine whether any migration was edited in place;
3.  verify clean PostgreSQL 17 installation;
4.  identify Supabase-only assumptions;
5.  verify migration hashes/ledger;
6.  verify dependency/order assumptions;
7.  document the exact 0006/0007 historical state.

No historical issue should be declared "fixed" without
repository/history evidence.

------------------------------------------------------------------------

# 52. Current Project State

Frozen/accepted:

-   Stage E;
-   F1;
-   F2;
-   F3;
-   F4;
-   F5;
-   F6.0;
-   F6.1;
-   F6.3;
-   F6.4;
-   real-data validation;
-   P0.5;
-   G-1;
-   P1;
-   P2;
-   P3;
-   Phase 2 HB-1 (governed backfill ledger);
-   Phase 2 HB-2 (governed CSE transport).

F6.2 is an accepted design. Its storage amendments (F6.2 §4--§7,
§10--§11) are implemented by F6.4 (migration 0015).

P3 was implemented, independently reviewed and passed its acceptance
gate (§53). It is **frozen/accepted** at commit `40e15bcc`.

F6.3 --- the pure financial-truth layer (`worker/financial_truth/`:
validation, admission, source observations, economic-fact identity and
reconciliation) --- is **implemented and frozen/accepted** at commit
`3c497c7d`.

F6.4 is **implemented and frozen/accepted** at commit `54d71c47`, built
from the frozen design `docs/F6.4_DESIGN.md` (commit `84804a14`). It is
the durable PostgreSQL persistence layer of the financial truth layer
(migration `0015_financial_truth_persistence.sql`, package
`worker/financial_truth_store/`, operator wrapper
`ops/bin/cse-financial`). It provides:

-   validation-run persistence: every candidate validation, OP1 records,
    economic-fact identities, and source observations with their members
    and member comparisons;
-   reconciliation persistence: configurations, the owner's designation,
    per-issuer batches, per-fact record history, inputs,
    cross-observation comparisons and batch results;
-   provenance from each fact to its source observations, candidates, F5
    run, classification, filing and listing observations, and back;
-   immutable history: every table is append-only (no UPDATE, DELETE or
    TRUNCATE for any role), so new results are appended and nothing is
    overwritten;
-   a job ledger: jobs, job events, locking and cleanup of abandoned
    jobs;
-   projections (six rebuildable views) and verification (`verify`
    re-proves the stored hashes, decompositions, seals and chains and
    reproduces validation runs; a security preflight);
-   database-enforced integrity: each F6.3 output is stored as its
    byte-exact canonical JSON envelope, and guard, deferred completeness
    and seal triggers prove that its typed columns and child rows are
    exactly that envelope (F6.4 design §11.6, EDI-1 to EDI-6).

F6.4 implements no availability, supersession or as-of policy (F8) and
no forecasting.

Real-data validation is **implemented and frozen/accepted** at commit
`12bc8f2c`. That one commit holds both its design
(`docs/REAL_DATA_VALIDATION_DESIGN.md`) and its implementation
(`docs/REAL_DATA_VALIDATION_IMPLEMENTATION.md` and the validation
harness `tests/rdv_*.py` and `tests/test_rdv_*.py`). It checked whether
the frozen financial-truth layers (F6.1, F6.3, F6.4) behave correctly
on actual persisted F1/F3/F5 evidence. It changed no frozen code, added
no migration and introduced no new financial semantics.

Its scope:

-   only the existing real evidence, pinned by a SHA-256 manifest: the
    26-filing F6 corpus, four F0 captures and eight Stage E fixtures;
    the evidence and the reports stay outside Git;
-   the evidence was replayed into throwaway PostgreSQL 17 databases
    through the frozen P2, F1, F5 and F3 stores, then validated and
    reconciled by F6.4's jobs, offline and with no CSE contact;
-   issuer links were decided only by the frozen F5 issuer rule, from
    real secId and listing evidence: no issuer was invented, and none
    was inferred from a document path prefix alone.

Measured coverage:

-   the F1 replay discovered 12,493 filings; the validation population
    is the 26 filings with F3/F5 evidence (19 issuers by source symbol,
    2,248 F5 candidates);
-   issuer decisions: 4 COMB filings have an evidenced listing-based
    link (admissible under F6.3 A-2); 1 LOLC filing is linked by its
    path prefix only (refused); 21 filings are unresolved (refused);
-   admission: 440 numeric and 8 nil candidates admitted and 1,800 not
    admitted, with every refusal reason counted; 1,378 candidates are
    refused only for issuer evidence;
-   404 source observations and **321 persisted economic facts, all for
    COMB, from 4 of the 26 filings**: 234 single-source numeric, 6
    single-source nil, 73 corroborated and 8 conflicting (all within
    one document);
-   determinism holds: repeated jobs write nothing new; results are
    reproduced from the persisted inputs and do not depend on input
    order; two replays are identical apart from database-generated
    identifiers; a different result for an existing input is refused
    and nothing is overwritten;
-   provenance traces every sampled fact to its listing and issuer
    evidence, and `verify` reproduces all 26 validation runs.

The real-world limit is issuer evidence, not financial-truth logic. 17
corpus issuers lack secId evidence and LOLC lacks a listing, so F6.3
A-2 refuses their filings. A counterfactual differential (a proxy
issuer for every filing, computed in memory and never persisted)
explains every difference from the real result and reproduces the F6.2
§14 measurements exactly (1,488 facts).

Suspected frozen-layer defects, recorded as change-control findings
and **not fixed** (each needs its own change-control decision):

-   P-18, F5 document-path parsing: `PATH_RE` reads no secId or upload
    epoch from 2,502 of 12,486 real CSE document paths (5 corpus
    filings). It fails closed, but loses the path/listing cross-check
    and the path epoch;
-   P-23, F5 v1 mapping: in COMB's annual report, 8 identities each
    receive two different printed rows (6 because the
    total-comprehensive-income attribution block is mapped onto the
    profit-attribution concepts, 2 from two different "Interest
    income" rows), giving 8 conflicting facts (F6.2 E5);
-   P-1, F3 period dating: DIAL 52713 is mis-dated (known since F6.0),
    and F6.1 refuses its 16 candidates.

Still open after real-data validation:

-   F8 availability/supersession: errata, amendment and restatement
    supersession, the choice of availability time, and commit-time
    as-of;
-   Phase 2 --- historical financial backfill --- remains the next
    major architectural phase. Only its first two implementation
    steps, HB-1 (the governed backfill ledger) and HB-2 (the governed
    CSE transport), both below, are implemented and frozen; HB-3
    (discovery and issuer evidence) is implemented and tested
    offline but not yet reviewed or frozen (below); HB-4 to HB-6 are
    not implemented, and Phase 2 as a whole is not implemented.

The real-data validation owner questions remain future decisions and
operational requirements, not completed work:

-   Q1: issuer evidence for every symbol (`/api/financials` listing
    discovery and secId evidence) belongs to the Phase 2 backfill
    design;
-   Q2: any change to F5 path parsing needs a separate F5
    version/change-control phase;
-   Q3: the external F6 evidence corpus needs a private backup outside
    Git.

Phase 2 HB-1 --- the governed backfill ledger --- is **implemented and
frozen/accepted** on `main` at merge commit `40748c3c` (pull request
#1). It is the first implementation step (HB-1, design §27) of the
Phase 2 design `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` (revision 3,
commit `75fd8354`), built under owner decisions HB-Q1 (the ledger
migration) and HB-Q3 (two frozen-test edits). It is the durable
PostgreSQL ledger and state-machine foundation of Phase 2: every later
Phase 2 step records its work, attempts, failures, holds, blocks,
leases and audits through it. Its implementation is frozen; a change to
it needs a new migration and its own change-control decision.

It consists of:

-   migration `0016_historical_backfill_ledger.sql` (additive; no
    existing table, function or migration 0001--0015 changed), holding
    the design's records L1--L11: owner arming decisions; work items
    with unique natural keys; append-only item events checked against a
    fixed transition table (91 rules, mirrored by
    `worker/financial_backfill/states.py`) and against the evidence rows
    they name; HTTP attempt intents and outcomes; exact JSON response
    bodies (never documents); F2 retrieval records (never bytes or
    temporary paths); holds and the owner's resolutions; wake-ups and
    leases; blocks and the owner's acknowledgements; anomaly records;
    coverage snapshots;
-   package `worker/financial_backfill/` (`states`, `keys`, `records`,
    `store`, `owner`, `preflight`): the worker's ledger operations, the
    owner path (`cse_migrator` → `SET LOCAL ROLE cse_owner`) for arming,
    hold resolutions and block acknowledgements, and a preflight
    (migration lineage, frozen-file and version pins, role and
    privilege model, triggers, the rule table and P2's lock key);
-   the two owner-approved HB-X3 frozen-test edits (RDV V9 and F6.4's
    migration-ledger/preflight test), in the durable form of the design
    §23.4: 0015 stays at position 14 with its frozen hash, `0006` stays
    unused, and later migrations are numbered after 0015.

Its invariants:

-   append-only for every role: no DELETE or TRUNCATE for anyone, and
    the transition rules cannot be changed without a migration; only
    the guarded heartbeat, release and expiry of wake-ups and leases are
    updates;
-   least privilege: `cse_worker` SELECT + INSERT (UPDATE only on
    wake-ups and leases), owner decisions only through the owner path,
    `cse_reader` SELECT; no `SECURITY DEFINER`, no row-level security
    and no new role;
-   one CSE slice at a time: a lease, request intent, outcome or
    retrieval record of an in-flight item is accepted only from the
    session that holds P2's global CSE advisory lock
    (`4346836117002312`) **exclusively**; a dead holder's lease is
    expired only by another session that then holds that lock. No new
    lock key is introduced.

Corrections included in the frozen baseline:

-   B-1 (commit `0de7a2eb`): a discovery item whose attempts all ended
    `unrecorded` (its F1 runs left `running`) can now reach `failed`
    with a stated reason; `succeeded` and `partial` still need a
    matching F1 run, and a bare `failed` is refused when an F1 run of
    the same request succeeded or partially succeeded (evidence wins);
-   D-1 (commit `f1899ec0`): only an exclusive hold of P2's exact lock
    key satisfies the ledger's lock check; a shared hold, or a
    neighbouring key, does not;
-   D-2 (commit `f1899ec0`): PostgreSQL regression tests pin every 0016
    guard clause that a planted fault previously left undetected;
-   D-3 (owner-confirmed): no pre-correction version of migration 0016
    was ever applied to a persistent database, so no migration-lineage
    remediation is needed.

HB-1 contacts no network and implements no CSE transport, discovery,
issuer acquisition, document retrieval, F6 orchestration, runner, CLI
or coverage reporting (HB-2 to HB-6). The attempt maximum and request
budgets recorded in the arming decision are enforced by HB-2, not by
the ledger. Owner decision HB-X2 (G-1's extension to Phase 2 and the
User-Agent contact) and prerequisite HB-P1 still gate any live Phase 2
request. P-18, P-23 and P-1 are unchanged.

Phase 2 HB-2 --- the governed CSE transport --- is **implemented and
frozen/accepted** on `main` at commit `18962805` (the HB-2
implementation `3a7512c8` plus the B-HB2-1 correction). It is the
second implementation step (HB-2, design §27). Every Phase 2 CSE
request goes through it; it is a library only, with no command, entry
point or timer (HB-6), and makes no request unless every gate passes.
Its implementation is frozen; a change to it needs its own
change-control decision.

It consists of the package `worker/backfill_transport/` (outside the
frozen HB-1 package, owner decision A1) and its tests. It adds no
migration, grant, role, row-level security, `SECURITY DEFINER`, lock
key or entry point, changes no frozen file, and writes only the HB-1
worker tables through HB-1's guards. Its scope, as approved in the HB-2
design review (owner decisions A1--A10):

-   the governed JSON requester (`getFinancialAnnouncement`,
    `financials`): per attempt, the gates, the HB-1 intent committed
    before the request, the spool journal, pacing, P2's own
    `RequestsTransport`, P2's classification order with F1's own shape
    acceptance, the exact body and outcome record spooled, then the
    outcome (with the JSON body and any block) committed in one
    transaction, then P2's block / circuit-breaker / retry decision;
-   the governed F2 fetcher: F2's own `fetch(url)` boundary, every
    request (including legacy fallbacks and redirects) separately
    admitted, paced and recorded; P2's hardened session (no
    environment proxies); documents streamed, never stored or spooled;
    CDN 401/407/451 are blocks, CDN 403/404 terminal attempts;
-   the release gates: an owner arming decision in force naming the
    stage, the exact P2 `user_agent()` value (A10), the host and the
    running frozen version tuple (A3); no unacknowledged Phase 2 or P2
    block; the stopped-stage rule (A5: three `circuit_open` slices,
    resumed only by a newer arming); daily and combined budgets, the
    combined ceiling reserving P3's armed daily budget (A7); P3's quiet
    window computed from P3's settings, the trading calendar and
    today's item (A6); P3's clock rules; arming re-read before every
    request, so a disarm stops the next one;
-   the item maximum counts claims since the last re-queue, not HTTP
    attempts (A2); the slice bounds (JSON requests, documents, and no
    new work after the time bound, A8); a document starts only if the
    budgets cover its six-request worst case (A9);
-   pacing of at least 1.5 s between any two CSE requests, seeded from
    P2's archive and the ledger, with a release guard before P2's lock
    is released;
-   one slice at a time under P2's exclusive lock (HB-1 D-1), with
    spool-first recovery of dead slices: a spooled response becomes
    `recovered_from_spool` without another request, otherwise
    `unrecorded`; recovery is idempotent;
-   its own preflight (pins of the P3 and HB-1 modules it reads, static
    boundaries, compatibility, database reads, the spool).

Correction included in the frozen baseline:

-   B-HB2-1 (commit `18962805`): a slice no longer releases its lease
    while any of its attempts lacks an outcome (an unexpected
    exception between intent and outcome caught by a caller); the
    lease stays active and the next slice recovers the attempt
    spool-first.

HB-2 makes no CSE request by itself: owner decision HB-X2 (G-1's
extension to Phase 2 and the User-Agent contact), prerequisite HB-P1
and an owner arming decision still gate any live Phase 2 request.

Phase 2 HB-3 --- discovery and issuer evidence --- is **implemented
and tested offline, not yet reviewed or frozen**. It is the library
package `worker/backfill_discovery/`, built under the HB-3 design
gate's owner decisions: D-HB3-1 (one F1 run per actual HTTP attempt:
discovery runs only under an arming with `attempts_per_json_request =
1` and an item maximum of at most 3, each claim being one separately
governed request with its own F1 run), D-HB3-2 / G2 (a discovery slice
never releases its lease while an item claimed under it is in
flight), G10 (an item at its claim maximum is made final atomically,
evidence first, with no request), HB-Q5 and HB-Q6. It adds no
migration, grant, role, row-level security, `SECURITY DEFINER`, lock
key or entry point, and changes no frozen file.

HB-P1 is a **deployment / runtime prerequisite, not an implementation
prerequisite**. HB-3 may be implemented and tested offline before any
production security-master capture exists. Live HB-3 discovery remains
forbidden until HB-P1 is satisfied in the deployment environment:
every HB-3 entry point that could make a discovery request refuses
unless the database holds a derived P2 market capture with a verified
archived `allSecurityCode` within the freshness bound, and nothing in
HB-3 creates that evidence. The deployment sequence is: software
freeze → server setup → HB-X2(b) → `CSE_CAPTURE_CONTACT_EMAIL` → P2
capture → `allSecurityCode` verification → derived security master →
HB-P1 satisfied → live HB-3.

Important accepted commits:

``` text
G-1:
79b7a5cc430d4f80ae57e62fc43b78ae3cacff4a

P1 fix:
43f6b85f25683517f7d8cda7636f2f7fe1c568c2

P2:
00af041dbb414bb15255a4434ad30a82f17d23ad

P3:
40e15bccbe0c4caf8ba74898237f69be6ad169f7

F6.3:
3c497c7d3b1fd02a3627299f11d9dd21cd959072

F6.4 design (frozen):
84804a142db6804bdde3b42a3aae3495eb4487b4

F6.4:
54d71c4782856ddd6855401673e10acaa465a2c8

Real-data validation (design and implementation, one commit):
12bc8f2ce0c7d06757299cbaebdc3fcc03164b51

Phase 2 design (revision 3):
75fd83549604398b02309d858fd1363a038fd7c5

Phase 2 HB-1 (implementation; with the design revision 3):
75fd83549604398b02309d858fd1363a038fd7c5

Phase 2 HB-1 B-1 correction:
0de7a2eb2e062da553f00e074e0737c6e1f1aab9

Phase 2 HB-1 D-1/D-2 hardening:
f1899ec00bb8c8cbb5c22b1c4a2ade2e8f219cae

Phase 2 HB-1 frozen baseline (merge into main):
40748c3c6a3f7c06f48526af3850a855abd1a96a

Phase 2 HB-2 (implementation):
3a7512c81408ec6d51f402f875c7a17e4bf82e8a

Phase 2 HB-2 B-HB2-1 correction (frozen baseline):
189628057c77b4211170b855df665ac05e041412
```

Migration 0015 (F6.4), as the migration ledger records it (LF-normalised
SHA-256):

``` text
afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2
```

Migration 0016 (Phase 2 HB-1, hardened), as the migration ledger
records it (LF-normalised SHA-256; pinned as `LEDGER_MIGRATION_SHA256`
in `worker/financial_backfill/__init__.py`):

``` text
f27c34a1b69e79b058b847fb4446ddcca403fc839d251f363386c8902dc8aae7
```

P3 reported tests included:

-   27 P3 unit tests;
-   37 P3 PostgreSQL tests;
-   Windows full suite: 771 passed, 207 skipped;
-   Linux full suite: 930 passed, 46 skipped, 2 expected D-2 xfails;
-   clean Ubuntu/systemd: 26/26;
-   P1 clean-server regression: 17/17.

These are implementation-agent-reported results, not an independent
execution by this document.

F6.4 was independently cross-checked against the repository: the
implementation boundary, the structure of migration 0015, the absence
of `SECURITY DEFINER`, the unmodified frozen migrations and stages, and
the writer's concurrency/idempotency handling. The cross-check did not
execute the test suites. F6.4 reported tests included:

-   171 F6.4 unit tests (U1--U10);
-   27 F6.4 PostgreSQL tests (P1--P16, P18) on PostgreSQL 17.11;
-   3 F6.4 real-corpus PostgreSQL tests (F6.2 §14 counts reproduced);
-   Linux full suite: 1321 passed, 46 skipped, 2 expected D-2 xfails;
-   Windows full suite: 1128 passed, 241 skipped;
-   clean Ubuntu 24.04 provisioning, with F6.4 exercised through
    `ops/bin/cse-financial`: 48/48;
-   mutation audit of migration 0015: 75 of 78 mutants killed (the three
    survivors are unreachable or shadowed checks); Python pre-insert
    mirror: 52 of 52.

These are implementation-agent-reported results, not an independent
execution by this document.

Real-data validation was independently cross-checked against the
repository. The cross-check covered:

-   exactly the seven added files, with no modified frozen stage or
    migration (0001--0015) and no new migration or database
    architecture;
-   the internal consistency of the reported population, candidate,
    admission, source-observation and fact counts;
-   the determinism checks;
-   the reporting of the three suspected defects as change-control
    findings.

The cross-check did not execute the test suites. Real-data validation
reported tests included:

-   27 real-data tests (14 database-free; 13 PostgreSQL, V1--V11) on
    PostgreSQL 17.11, offline, with the evidence mounted;
-   Linux full suite: 1348 passed, 46 skipped, 2 expected D-2 xfails;
-   Windows full suite: 1138 passed, 258 skipped;
-   mutation sanity check of the new tests: 5 of 5 planted faults
    killed.

These are implementation-agent-reported results, not an independent
execution by this document.

Phase 2 HB-1 passed its gates in order: a post-implementation audit
(which found B-1), the B-1 correction audit, the acceptance audit (which
recommended D-1 and D-2), the D-1/D-2 hardening, the owner's
acceptance, and the merge into `main`. HB-1 reported tests on the
frozen baseline included:

-   11 HB-1 unit tests and 36 HB-1 PostgreSQL tests;
-   Linux offline full suite: 1158 passed, 285 skipped;
-   PostgreSQL regression across HB-1, F6.4, P1, P2 and P3: 161 passed,
    75 skipped (the RDV and F6.4 corpus tests without the evidence, the
    F3/F5 suites without their scratch databases, P1's restic test), 2
    expected D-2 xfails, and 1 failure that is an artefact of running as
    root (P2's unwritable-spool test, failing identically before HB-1);
-   mutation audit of migration 0016: 37 of 37 planted faults killed.

These ran on PostgreSQL 16.14 with an audit-only libpq stand-in for
`psycopg2` and a version emulation that reports 17.11; they are not a
PostgreSQL 17.11 execution. Without the emulation, the only additional
failures are the six PostgreSQL 17 version checks. Not executed by the
implementation agent: PostgreSQL 17.11 with the real `psycopg2`, the
RDV and F6.4 corpus tests with the evidence mounted, and the Windows
full suite. These are implementation-agent-reported results, not an
independent execution by this document.

Phase 2 HB-2 passed its gates in order: the design review and the
owner's approval of A1--A10, the implementation, a freeze audit (which
found B-HB2-1), the B-HB2-1 correction, and the repeated freeze audit.
HB-2 reported tests on the frozen baseline included:

-   38 HB-2 unit tests and 20 HB-2 PostgreSQL tests, with HB-1's 11
    unit and 36 PostgreSQL tests still passing;
-   Linux offline full suite: 1196 passed, 305 skipped;
-   PostgreSQL regression across HB-2, HB-1, F6.4, P1, P2 and P3: 181
    passed, 75 skipped (the RDV and F6.4 corpus tests without the
    evidence, the F3/F5 suites without their scratch databases, P1's
    restic test), 2 expected D-2 xfails, and 1 failure that is an
    artefact of running as root (P2's unwritable-spool test, failing
    identically before HB-1);
-   mutation audit of the transport: 95 of 95 planted faults killed.

The same caveats as HB-1 apply: PostgreSQL 16.14 with an audit-only
libpq stand-in for `psycopg2` and a version emulation that reports
17.11, not a PostgreSQL 17.11 execution (without the emulation, every
slice is refused by the PostgreSQL 17 version check); no evidence
corpus; no Windows run; no live CSE request. Not executed by the
implementation agent: PostgreSQL 17.11 with the real `psycopg2`, the
corpus tests and the Windows full suite. These are
implementation-agent-reported results, not an independent execution by
this document.

------------------------------------------------------------------------

# 53. P3 Acceptance Gate

**Status: passed.** P3 was independently reviewed against the
repository and this architecture, passed this gate, and is
frozen/accepted at commit `40e15bcc` (§52). This section records what
the gate required:

-   independently inspect the repository;
-   verify migration 0014 is additive;
-   verify no frozen stage was modified;
-   verify PostgreSQL remains scheduler authority;
-   verify systemd is wake-only;
-   verify owner-only controls;
-   verify leases/heartbeats;
-   verify catch-up;
-   verify request budget;
-   verify backup independence;
-   verify clean provisioning;
-   verify no CSE contact in tests;
-   perform migration lineage audit;
-   document D-2.

The first production capture is a separate gate.

------------------------------------------------------------------------

# 54. First Production Capture

The first real CSE production capture must be treated as a controlled
release event.

Before it:

-   server provisioned;
-   clock synchronized;
-   backups configured;
-   scheduler verified;
-   governance recorded;
-   CSE request budget confirmed;
-   tests proven offline;
-   no test accidentally contacts CSE.

The first capture must produce explicit evidence of:

-   request discipline;
-   raw archive integrity;
-   session/date correctness;
-   market observation completeness;
-   canonicalization state;
-   backup state;
-   auditability.

------------------------------------------------------------------------

# 55. Forecasting Implementation Roadmap

The old roadmap should not be treated as a rigid label system. The
architecture should be implemented in dependency order.

## Phase 1 --- Complete financial truth

-   formalize F6.2 storage changes --- done: implemented by F6.4
    (migration 0015);
-   F6.3 reconciliation --- implemented and frozen (`3c497c7d`);
-   F6.4 persistence --- implemented and frozen (`54d71c47`);
-   real-data validation --- implemented and frozen (`12bc8f2c`; §52);
-   availability/supersession (F8; explicitly deferred by F6.4).

The remaining Phase 1 item (F8 availability/supersession) and every
later phase below are not yet implemented, apart from Phase 2's first
two steps, HB-1 and HB-2 (HB-3 is implemented offline and awaiting
review). Phase 2 --- historical financial backfill --- remains the
next major architectural phase; its design decides real-data
validation Q1 (issuer evidence; §52).

## Phase 2 --- Historical financial backfill

-   five-year filing universe;
-   extraction;
-   validation;
-   reconciliation;
-   coverage audit.

Design: `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` (revision 3).
HB-1, the governed backfill ledger (migration 0016,
`worker/financial_backfill/`), is implemented and frozen (`40748c3c`;
§52). HB-2, the governed CSE transport (`worker/backfill_transport/`),
is implemented and frozen (`18962805`; §52). HB-3, discovery and issuer
evidence (`worker/backfill_discovery/`), is implemented and tested
offline, not yet reviewed or frozen; live discovery waits for HB-P1, a
deployment prerequisite (§52). HB-4 to HB-6 (document worker; F6
orchestration and audit; operations and pilot) are not implemented.

## Phase 3 --- Market feature foundation

-   historical daily market data;
-   feature calculations;
-   activity/liquidity eligibility;
-   corporate actions;
-   survivorship-aware universe.

## Phase 4 --- Deterministic engines

-   fundamentals;
-   valuation;
-   profitability;
-   leverage;
-   cash flow;
-   momentum;
-   volatility;
-   volume/liquidity;
-   market/sector-relative signals.

## Phase 5 --- ML

-   feature dataset;
-   walk-forward training;
-   candidate models;
-   explainability;
-   model registry;
-   Route 1 combination.

## Phase 6 --- News/sentiment

-   news ingestion;
-   entity identification;
-   relevance;
-   events;
-   sentiment;
-   decay;
-   historical sentiment snapshots.

## Phase 7 --- Gemini

-   Gemini abstraction;
-   prompt registry;
-   structured input;
-   response parser;
-   Route 2;
-   provenance;
-   cost/rate tracking.

## Phase 8 --- Forecast presentation

-   four horizons;
-   both routes;
-   reasoning;
-   uncertainty;
-   agreement/disagreement.

## Phase 9 --- Outcome evaluation

-   automatic target-date evaluation;
-   errors;
-   calibration;
-   route comparisons;
-   historical performance.

## Phase 10 --- Backtesting laboratory

Mandatory before trusting live model performance.

## Phase 11 --- Adaptation

-   drift;
-   retraining;
-   recalibration;
-   candidate validation;
-   promotion/retirement.

## Phase 12 --- Dashboard

-   local/LAN/VPN UI;
-   security pages;
-   forecasts;
-   reasoning;
-   accuracy;
-   backtesting;
-   provenance;
-   health.

------------------------------------------------------------------------

# 56. Testing Requirements

Testing must cover:

## Data

-   extraction;
-   financial validation;
-   reconciliation;
-   market parsing;
-   source integrity.

## Point-in-time

-   future report exclusion;
-   future market exclusion;
-   future news exclusion;
-   revision handling;
-   timestamp correctness.

## Forecasting

-   target calculation;
-   probability calculation;
-   return calculation;
-   volatility;
-   deterministic engines;
-   ML;
-   agreement.

## Gemini

-   schema;
-   grounding;
-   failure;
-   rate limiting;
-   version tracking.

## Backtesting

-   chronological replay;
-   historical universe;
-   leakage;
-   survivorship;
-   reproducibility.

## Infrastructure

-   restart;
-   downtime;
-   stale jobs;
-   leases;
-   backups;
-   restore;
-   systemd.

------------------------------------------------------------------------

# 57. Forecast Data Leakage Test Examples

Tests should deliberately attempt:

-   adding a future filing;
-   adding a future news article;
-   changing a future close;
-   inserting a later revised financial value;
-   adding future sentiment;
-   moving a publication timestamp backward;
-   using today's security universe for a historical date.

The historical prediction must remain unaffected by information that did
not exist at the time.

------------------------------------------------------------------------

# 58. Research / Production Separation

Maintain:

``` text
production
research
backtesting
```

A research model may never silently become the production model.

A candidate must pass its defined validation gates and receive a new
immutable version.

------------------------------------------------------------------------

# 59. What Must Never Be Done Casually

Do not casually add:

-   Supabase runtime;
-   Vercel runtime;
-   GitHub Actions as production scheduler;
-   permanent PDFs in PostgreSQL;
-   arbitrary financial-fact precedence;
-   mutable latest-value tables;
-   unversioned Gemini prompts;
-   unversioned model changes;
-   future data into historical forecasts;
-   automatic model mutation after one bad forecast;
-   forced predictions with insufficient evidence;
-   deletion of historical forecasts;
-   automatic replacement of Route 1 by Route 2;
-   automatic trading execution.

------------------------------------------------------------------------

# 60. Definition of a Production-Quality Forecast

A production forecast requires:

-   eligible security;
-   sufficient market data;
-   sufficient financial data;
-   valid feature snapshot;
-   no critical unresolved data issue;
-   approved model versions;
-   explicit cutoff;
-   Route 1 result;
-   Route 2 result or explicit Route 2 failure;
-   evidence-grounded reasoning;
-   provenance;
-   immutable storage.

------------------------------------------------------------------------

# 61. Definition of a Production-Quality Model

A model requires:

-   registered version;
-   known training data;
-   known cutoff;
-   chronological validation;
-   leakage tests;
-   backtest;
-   documented metrics;
-   artifact hash;
-   feature schema;
-   deployment decision.

------------------------------------------------------------------------

# 62. Definition of a Valid Backtest

A backtest is valid only if:

-   point-in-time reconstruction works;
-   historical universe is correct;
-   financial availability is correct;
-   market availability is correct;
-   news availability is correct;
-   no future information leaks;
-   model versions are fixed;
-   results are reproducible.

Performance comes after methodological validity.

------------------------------------------------------------------------

# 63. Definition of Reasoning

The platform provides **evidence-grounded explanations**, not hidden
chain-of-thought.

Reasoning summarizes:

-   important measurable factors;
-   deterministic outputs;
-   ML contributions;
-   relevant financial changes;
-   market behavior;
-   relevant news/events;
-   sentiment;
-   uncertainty;
-   route divergence.

------------------------------------------------------------------------

# 64. Questions the Finished Platform Should Answer

The architecture should eventually support empirical research such as:

-   Do fundamentals improve 1-day forecasts?
-   Are fundamentals more useful over 1-month horizons?
-   Does sentiment add information beyond Route 1?
-   When do the two routes disagree?
-   Is disagreement associated with higher error?
-   Which deterministic signals are robust?
-   Which ML features matter?
-   Does sentiment matter more in high-volatility periods?
-   Which companies/horizons are most predictable?
-   Does Gemini add predictive value?
-   How does model performance change after retraining?

The system should answer these through stored evidence and evaluation
rather than assumptions.

------------------------------------------------------------------------

# 65. Completion Criteria

The core project is substantially complete when:

1.  five years of financial history are available;
2.  new filings are continuously collected;
3.  daily market data is continuously collected;
4.  financial facts are validated;
5.  point-in-time features are available;
6.  deterministic engines run;
7.  ML runs;
8.  Route 1 is produced;
9.  news/sentiment runs;
10. Gemini Route 2 is produced;
11. both routes are shown;
12. both routes have grounded reasoning;
13. agreement/disagreement is shown;
14. forecasts are stored with provenance;
15. actual outcomes are collected;
16. both routes are evaluated;
17. historical performance is retained;
18. backtesting works;
19. leakage tests pass;
20. model adaptation is versioned;
21. scheduler survives downtime;
22. backup/restore works;
23. the system remains auditable end-to-end.

------------------------------------------------------------------------

# 66. Master Mental Model

``` text
                    SOURCES
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   CSE filings     CSE market       News
        │              │              │
        ▼              ▼              ▼
   source evidence / raw observations
        │              │              │
        └───────┬──────┴───────┬──────┘
                ▼              ▼
        validated truth    sentiment
                │              │
                └──────┬───────┘
                       ▼
                    FEATURES
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
        ROUTE 1              ROUTE 2
     deterministic + ML   quantitative + sentiment
             │                   │
             ▼                   ▼
       prediction            Gemini
             │                   │
             │                   ▼
             │              prediction
             └─────────┬─────────┘
                       ▼
                 USER FORECAST
            predictions + reasoning
             + uncertainty + agreement
                       │
                       ▼
                 ACTUAL OUTCOME
                       │
                       ▼
                  EVALUATION
                       │
                       ▼
                  BACKTESTING
                       │
                       ▼
                   ADAPTATION
                       │
                       ▼
                 NEW VERSIONS
```

------------------------------------------------------------------------

# 67. Final North-Star Statement

**This project is a locally hosted, auditable CSE financial intelligence
and forecasting platform. It builds a five-year historical financial
foundation from CSE reports, continuously collects new filings and daily
market data, constructs validated point-in-time financial and market
datasets, and uses deterministic financial/data-analysis engines
together with ML models to produce a quantitative prediction. That
quantitative information is then combined with independently generated
market news and sentiment and supplied to Gemini to produce a second AI
prediction. Both prediction routes are retained and independently
evaluated. The user is shown both predictions, their evidence-grounded
reasoning, their uncertainty and their degree of agreement/disagreement.
At the end of each forecast horizon, actual market outcomes are
collected and both routes are evaluated. Performance history feeds
controlled, versioned retraining and recalibration. A dedicated
historical backtesting laboratory reconstructs what information would
have been available at previous dates and tests the complete system
without look-ahead, survivorship or data leakage. The entire system is
designed around provenance, reproducibility, point-in-time correctness,
security, persistent scheduling, backup/recovery and auditability.**

This statement is the project's architectural north star.

------------------------------------------------------------------------

# 68. Rule for Future Claude Prompts

Every implementation prompt must explicitly state:

1.  which architectural layer is changing;
2.  which source of truth it consumes;
3.  what information is available at its timestamp;
4.  what provenance must be retained;
5.  whether any frozen component is touched;
6.  whether a new migration is required;
7.  how leakage is prevented;
8.  how the change is tested;
9.  how it is reproduced;
10. how it interacts with Route 1 and Route 2;
11. how its outputs are evaluated later;
12. how it behaves after downtime;
13. how failures are represented;
14. how versions are recorded;
15. how the user-facing reasoning can be grounded in the actual inputs.

If those questions cannot be answered, the implementation is not ready.
