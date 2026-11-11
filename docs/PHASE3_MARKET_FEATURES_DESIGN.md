# Phase 3: market feature foundation (design)

**Status: DESIGN, revision 1 (2026-11-11). Not implemented. Not accepted.** Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact, no capture, no purchase and no change to `main`.

**Governing records:** Master Architecture (MA) §4 to §6, §10, §11, §22, §33 to §36, §39 to §42, §45, §55 (Phase 3),
§56 to §59 and §68; `docs/ops/P2_MARKET_CAPTURE.md` and `docs/ops/P3_SCHEDULER.md` (what market capture records);
`docs/F8_DESIGN.md` §14 (the consumers' rules). Where this document and those differ, they win.

**Short name:** MF. Steps are MF-1 to MF-7 (§16). Decisions are P3-D*n* (taken, [delegated]) and P3-R*n* (reserved
for the owner) (§15).

---

## 1. Conclusion

Phase 3 turns the market capture archive into versioned, point-in-time market features, a historical security
universe, and the forecast targets. It is a read-only layer above P2 and P3, as F8 is above F6.4:

```
P2 archive + raw_market_observations (append-only, frozen)      imported history (new, append-only; source is P3-R1)
        \                                                         /
         MF-1  daily bar as of T      (frozen reconcile() over the observations known at T)
         MF-2  session calendar as of T
         MF-3  corporate actions and adjustment factors (explicit, versioned)
         MF-4  universe and market-data eligibility as of T
         MF-5  features (registry: formula, units, window, availability rule, version, quality state)
         MF-6  targets (the four horizons of MA §4; labels, never features)
         MF-7  snapshot store (append-only, hashed; the provenance anchor of every forecast)
```

Five conclusions shape everything below.

1. **The mutable canonical table is not the source.** `daily_market_data` is upserted in place, so it cannot say what
   was known at an earlier time, and MA §59 forbids "mutable latest-value tables" as a casual source. MF reads the
   append-only `raw_market_observations` and rebuilds each daily bar by calling the frozen, pure
   `worker.reconciliation.reconcile` on exactly the observations known at the cutoff (§5). The frozen Stage E defect
   D-2 lives in the upsert, not in `reconcile`, so it does not affect MF.
2. **Market data has the same two clocks as financial data** (MA §10). A bar has a session date, and the system has a
   time at which it first held each observation. MF exposes the same labelled modes as F8 (§4), so one forecast can
   state one consistent information cutoff for both.
3. **There is no market history yet, and none is recoverable from the capture path.** P2's `tradeSummary` serves only
   the latest session (`docs/ops/P3_SCHEDULER.md` §5), and no production capture has run. History can only come from
   another source, which is a reserved owner decision (P3-R1, §14). The design is source-agnostic so that the decision
   does not block the build.
4. **The software can be built and frozen on synthetic data. The numbers cannot.** Eligibility thresholds must be
   "versioned and empirically tested" (MA §6). MF ships the mechanism and a threshold set labelled `provisional`;
   nothing downstream may treat it as validated until real data exists (§9, §17).
5. **Nothing frozen needs to change.** MF adds a package, a store package and one migration. The only frozen files
   touched are list-pinning tests that enumerate migrations or packages; they are named in §13 and changed under
   change control at implementation, not by this design.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Where met |
|---|---|---|
| Forecasts use only information available at or before the cutoff; explicit automated leakage tests | §11, §57 | §4, §5, §12 |
| Two clocks: publication/availability time and system-knowledge time | §10 | §4 |
| The previous completed trading day is the main market-data basis; the listed market features | §5 | §10 |
| The trading calendar, not calendar-day arithmetic, determines the target date | §4, §45 | §6, §11 |
| Target definitions are versioned; `return = (future_close - reference_close) / reference_close` | §4 | §11 |
| Eligibility by measurable data and activity criteria, versioned and empirically tested; not market capitalisation alone | §6, §22 | §9 |
| Inactive and delisted securities stay in history; today's list never defines yesterday's universe | §6, §34, §35 | §8 |
| Raw and analysis-adjusted series distinguishable; adjustments explicit and versioned | §40 | §7 |
| Every feature has source, formula, units, availability rule, version, quality state | §39 | §10 |
| Quality states `fresh`, `stale`, `partial`, `conflicting`, `missing`, `unlabelled`, `untrusted` | §41 | §10.3 |
| Provenance forecast → market feature → daily observation → capture run → raw CSE response | §42 | §13 |
| No mutable latest-value tables; no future data in historical forecasts | §59 | §5, §13 |
| The fifteen questions of §68 | §68 | §18 |

---

## 3. Boundary

**MF reads** (never writes):

| Source | Used for | Why this one |
|---|---|---|
| `raw_market_observations` (0001, 0002; append-only since 0010) | Every price, volume, turnover, trade count and foreign holding value | Verbatim, append-only, carries `observed_at` and `created_at` |
| `market_capture_runs`, `market_capture_run_events`, `market_capture_security_results`, `market_source_responses`, `market_response_bodies` (0012; append-only) | Which run produced an observation, its trading date and session evidence (E1), universe and `tradeSummary` membership, and the archived `allSecurityCode` bodies | The run ledger is the evidence for sessions and for the universe |
| `companies` (identity columns only: `id`, `ticker`) | The security key of an observation | `id` is the foreign key the raw layer uses. Its mutable descriptive columns (`sector`, `trading_status`, `cse_active_flag`, dates) are **not** read (§8) |
| `issuer_securities` (0007) | The link from a security to its F5 issuer, for Phase 4's joins | F5's append-only issuer evidence |
| MF's own tables (§13) | Imported history, corporate-action evidence, configurations, snapshots | — |

**MF never reads** `daily_market_data`, `daily_index_data`, `trading_calendar` rows as point-in-time truth (§6 says
how the calendar is used), `corporate_actions` (§7.1), or any mutable "latest" value. A static test enforces the list.

**MF never** contacts CSE or any network, changes a frozen stage or migration, writes to a table it does not own, or
treats a staging or synthetic row as production evidence.

**MF does not own:** fundamental features and valuation (Phase 4, through F8), the ML dataset assembly and models
(Phase 5), news (Phase 6), index capture (a P2 change, P3-R3).

---

## 4. Temporal model

### 4.1 The times

| Symbol | Meaning | Source |
|---|---|---|
| D | A session date: a Colombo calendar date on which the exchange traded | E1 evidence of a run (P2 §7), or an imported history row's date |
| `observed_at` | When the system received the response an observation was derived from | `raw_market_observations.observed_at` (P2 sets it from the archived response) |
| `recorded_at` | When the system durably held the observation | `raw_market_observations.created_at`; for imported history, the import batch's commit time |
| T | The **information cutoff** of a query: an instant | The caller (a forecast run, a dataset builder) |
| H | The **knowledge horizon**: the instant up to which recorded evidence may be used to answer "what was published by T" | The caller; defaults to the query time |

A session date D becomes a usable end-of-day bar only at a real instant. MF never treats "D" as an instant.

### 4.2 Modes (the labels match F8's, deliberately)

| Mode | Uses | Label | For |
|---|---|---|---|
| `KNOWN` | Observations with `recorded_at` ≤ T | `point_in_time` | Live forecasts; backtests of the live era |
| `AVAILABLE` | Observations with **source availability** ≤ T, using every observation recorded by H | `reconstructed` | Backtests and training over an era whose data arrived later (imported history) |
| `CURRENT` | Everything recorded by H | `retrospective_current` | Research reports only; never point-in-time |

- **Source availability of an end-of-day bar for D.** For a captured observation it is its own `observed_at`: the
  system saw CSE publish it then. For imported history, which has no observation instant, it is a rule of the
  configuration's `availability_policy`: by default the end of session date D in Colombo plus a configured lag (P3-D4),
  and the result is labelled `reconstructed` because the rule is an assumption.
- A live prediction uses `KNOWN` only. No point-in-time use takes a `CURRENT` result. These are F8's two consumer
  rules (`docs/F8_DESIGN.md` §14), applied to market data, and `pit.require_point_in_time` style guards enforce them
  in code.
- Every result carries its mode, T, H, the configuration id and a hash of the inputs it used, so the same query on the
  same evidence gives the same bytes.

### 4.3 The market-data cutoff of a forecast

MA §11 gives a forecast a `market_data_cutoff`. MF defines it as the pair (T, D\*), where D\* is the latest session
whose end-of-day bar is visible at T under the mode. D\* is an output, recorded with the forecast, never an input the
caller asserts. A forecast made on a morning before that day's capture has D\* = the previous session, whatever the
calendar says.

---

## 5. MF-1: the daily bar as of T

For a security s and a date D, at cutoff T in a mode:

1. Select the rows of `raw_market_observations` for (s, D) that the mode admits (§4.2).
2. Call the frozen `worker.reconciliation.reconcile` on them, with the tolerances P2 uses. Nothing is re-implemented;
   a static test pins the SHA-256 of `worker/reconciliation.py`, as P2 pins its Stage E modules.
3. The result is the **raw bar**: `open_price`, `high`, `low`, `closing_price`, `last_traded_price`,
   `last_traded_date`, `share_volume`, `trade_count`, `turnover`, `foreign_holding`, with `reconcile`'s own provenance
   and discrepancy notes kept beside it.

Then MF derives the **feature bar**, by rules that have stable ids and never alter the raw bar:

| Rule | Condition | Effect |
|---|---|---|
| MFB-1 `eod_required` | No admitted observation from an end-of-day window | No bar: quality `missing`. An intraday (`post_open`) value never stands in for a close (the frozen policy already says so) |
| MFB-2 `zero_close_is_not_a_price` | `closing_price` is 0 | `px_close` is null with reason `close_not_published_or_no_trade`. Zero is never used as a price (README, "Open investigations": 0.0 is CSE's value before publication and for non-traded securities) |
| MFB-3 `no_trade_day` | The run's evidence shows s in the universe and absent from `tradeSummary` (`market_capture_security_results`), or volume and trade count are both 0 | `traded = false`; volume, trade count and turnover are 0 **in the feature bar only**, with the rule id; the raw nulls stay as they are |
| MFB-4 `carry_forward_close` | `traded = false` or `px_close` null, and an earlier feature bar has a close | `px_ref` = the latest earlier close, with `stale_sessions` = the number of sessions since; `px_close` stays null |
| MFB-5 `close_outside_range` | `closing_price` outside [`low`, `high`] | Kept. It is a genuine CSE value (README: 16 of 284 rows, all with close equal to the previous close). The bar carries the flag |
| MFB-6 `discrepancy` | `reconcile` flagged a cross-source disagreement | The bar's quality is `conflicting`; MF does not pick differently from `reconcile` and does not hide the flag |

- `px_close` is a close CSE printed for a session with trades. `px_ref` is the last such close carried forward. Every
  feature states which one it uses.
- Whether a volume that the raw layer left null is zero is decided only by MFB-3, from the run's recorded membership
  evidence. Without that evidence it stays null (`unlabelled`). This is the "printed nil is never zero" rule of MA §9
  applied to market data.

**Imported history** (§14) supplies bars for dates with no captured observation. Where both exist for (s, D), the
captured observations are used in `KNOWN`, and in `AVAILABLE` and `CURRENT` the two are compared field by field under
the same tolerances: agreement is recorded, disagreement makes the bar `conflicting` and both values are kept. No
source is silently preferred (MA §9, §59).

---

## 6. MF-2: the session calendar as of T

CSE's closures follow no computable rule (0001's comment on `trading_calendar`; P3 §5). MF therefore derives sessions
from evidence and never from weekday arithmetic.

- **A proven session** is a date D for which a run recorded E1 evidence visible at T, or for which admitted imported
  history holds at least one traded bar.
- **A proven closure** is a date with a `trading_calendar` row `closed` whose `established_at` ≤ T. That table is
  insert-only for the worker (P3's preflight), so a row's existence at T is stable; MF reads only `trade_date`,
  `market_status`, `established_by` and `established_at`.
- **An unknown date** is a Monday to Friday date at or before D\* that is neither. A missed capture leaves one. It is
  never counted as a session and never assumed closed (MA §45: "a failed capture must never itself prove a market
  closure").

The **session sequence** as of T is the ordered list of proven sessions up to D\*. All windows and horizons count
positions in that sequence.

- A window that spans an unknown date is `partial`. The configuration sets how many unknown dates a window tolerates
  before the feature is `missing` (default 0 for windows of 5 sessions or fewer, otherwise 2% of the window; P3-D5).
- The sequence is per market, not per security: a security that did not trade on a proven session has a no-trade bar
  (MFB-3), not a gap.

---

## 7. MF-3: corporate actions and adjustment

### 7.1 Evidence

- The 0001 table `corporate_actions` is **not used**. Nothing writes it, it is not append-only (0010 lists it among
  the tables left mutable), and it supersedes by an in-place flag. MF records its own evidence instead (§13):
  `mf_corporate_action_observations`, append-only, each row a verbatim statement of one action with its source
  reference, the source's own announcement and ex-dates, and `recorded_at`.
- **Where the evidence comes from is reserved (P3-R2).** No component captures CSE announcements today. Until a source
  is decided, actions are entered by the owner through an owner path from CSE's published notices, with the reference
  kept, as P3's `declare-closed` does for holidays.
- A later statement about the same action is a new row that names the row it supersedes. Supersession needs that
  declared link; arrival order alone supersedes nothing (the F8 rule).

### 7.2 What is adjusted

| Action | Effect on the price series | Factor applied to bars before the ex-date |
|---|---|---|
| Sub-division (split), consolidation | Mechanical | old shares / new shares |
| Capitalisation of reserves (bonus), scrip dividend | Mechanical | old / (old + new) |
| Rights issue | Mechanical, given the terms | TERP / cum-rights close, where TERP = (n·P_cum + m·P_sub) / (n + m) for m new per n held at P_sub |
| Cash dividend | **None in `adjustment_policy` v1** (P3-D2) | 1. Recorded as evidence; available to Phase 4 for yield |
| Ticker change, merger, delisting | Identity events, not price factors | §8 |

- Factors are exact rationals stored as numerator and denominator, with the observation ids they were computed from.
- Volume is adjusted by the inverse factor for the mechanical actions. Turnover (a money amount) is never adjusted.
- A rights factor needs the cum-rights close. If that bar is `missing` or `conflicting`, the factor is `unresolved`
  and every adjusted value that crosses the ex-date is `untrusted`. It is never estimated.

### 7.3 Adjustment as of T

The adjusted series as of T applies exactly the actions whose evidence is visible at T under the mode **and** whose
ex-date is at or before D\*. An action announced by T with a later ex-date changes nothing yet: prices have not moved.
An action recorded after T changes nothing at T, even if its ex-date is before T. In that case the series as of T shows
the unexplained jump that the system really saw, and MF-3's detector (below) is what a live user would have had.

**The unexplained-move detector.** A session-to-session change in `px_close` beyond the configuration's threshold with
no visible action on that date is flagged `unlabelled` on every window that contains it. MF never infers an action
from a price move.

Raw and adjusted values are separate fields with separate names (`px_close` and `adj_close`), and each adjusted value
carries the `adjustment_set_hash` of the factors applied (MA §40).

---

## 8. MF-4a: the universe as of T

- **For a captured session D**, the universe is the set of securities in the `allSecurityCode` body archived by D's
  run, read from the archive (`market_response_bodies`), exactly as HB-3's security master reads it. `companies` is
  not consulted for membership: its `cse_active_flag` is updated in place.
- **For an imported date**, the universe is the set of securities with a row in the admitted import for that date.
  Whether an import contains securities that later delisted is a property of the source, recorded per import batch as
  `survivorship: complete | survivors_only | unknown`. A dataset built on a `survivors_only` or `unknown` era carries
  that label, and Phase 5 and the backtesting laboratory must show it (MA §34). MF does not pretend otherwise.
- A security stays in every historical universe it was in. Nothing is removed when it later leaves.

**Identity.** The key is `companies.id`, one per ticker. Two limits are recorded, not solved:

- **Ticker changes.** A renamed security appears as a new ticker and so a new `companies` row; its series breaks.
  `symbol_history` exists (0001) but nothing writes it. MF v1 links nothing automatically. A link is an owner-entered
  identity observation in MF's own table, with its source reference, and takes effect only from its `recorded_at` in
  `KNOWN`.
- **Sector.** `companies.sector` is a mutable current value with no history. MF v1 therefore builds **no sector
  feature** (P3-D6). Market-relative features use cross-sectional aggregates that MF computes itself (§10.2). Sector
  context waits for a point-in-time sector source.

## 9. MF-4b: market-data eligibility

A security moves through `CSE listed → financially eligible → market-data eligible → forecast eligible` (MA §6). MF
decides only the third step. Phase 4 decides the second, and the forecast layer combines them (MA §22).

Eligibility at (s, D\*, T) is a pure function of the session sequence, the feature bars and a versioned
`eligibility_policy`:

| Criterion (policy v0, `provisional`) | Parameter | Provisional value |
|---|---|---|
| History: proven sessions since the security's first bar | `min_history_sessions` | 252 |
| Activity: share of the last `activity_window` sessions with `traded = true` | `min_traded_ratio` over 63 | 0.50 |
| Liquidity: median turnover over the traded days of the window | `min_median_turnover_lkr` over 63 | 100 000 |
| Freshness: sessions since the last traded bar | `max_stale_sessions` | 5 |
| Quality: no `conflicting` or `untrusted` bar inside the longest feature window | `block_on` | `conflicting`, `untrusted` |

- The result is one of `eligible`, `insufficient_history`, `low_activity`, `stale`, `blocked_quality`, with the
  measured value of every criterion. These map onto MA §22's blockers; MF never returns a bare boolean.
- **The values above are placeholders, labelled `provisional` in the configuration itself** (P3-D3). They are not
  empirically tested, because no data exists. The store refuses to mark a configuration `validated` without a recorded
  validation report id, and Phase 5 refuses to train a production candidate on a `provisional` policy. Choosing real
  values is a later, data-dependent step (§17).
- Market capitalisation is not a criterion (MA §6).

---

## 10. MF-5: features

### 10.1 The registry

Every feature is an entry in a code registry, and the registry's canonical JSON is part of the configuration hash:

```
feature_id        stable, e.g. "ret_21"
version           integer; any change to formula, inputs, window or rounding is a new version
family            return | volatility | momentum | drawdown | volume | liquidity | range | foreign | market_context
formula           the definition in words and symbols (below), also the docstring of the function
inputs            which bar fields, raw or adjusted, px_close or px_ref
window            sessions looked back (and skipped), counted in the session sequence
min_observations  the fewest valid inputs for a value
units             ratio | log_ratio | lkr | shares | count | sessions
availability_rule which bar must be visible at T (always: bars up to D* only)
quality           how input states combine (§10.3)
```

Computation is pure and deterministic: Python `Decimal` with one fixed context (precision 28, half-even), `ln` and
`sqrt` from `decimal`, results quantized to 12 decimal places. No float is used, so the same inputs give the same
bytes on every platform (P3-D7).

### 10.2 Feature set v1

With c_t = `adj_close` on session t (carried forward as `px_ref` only where stated), r_t = ln(c_t / c_{t-1}) over
consecutive sessions on which both closes are real, and D\* written as t:

| Family | Feature ids | Definition |
|---|---|---|
| Return | `ret_1`, `ret_5`, `ret_10`, `ret_21`, `ret_63`, `ret_126`, `ret_252` | c_t / c_{t-n} - 1, using `px_ref` at both ends, with `stale_sessions` reported beside it |
| Volatility | `vol_21`, `vol_63` | Sample standard deviation of r over the window's traded sessions |
| | `vol_parkinson_21` | sqrt( (1 / (4 ln 2)) · mean( ln(high/low)² ) ) over traded sessions with both values |
| Momentum | `mom_252_21` | c_{t-21} / c_{t-252} - 1 |
| | `mom_126_21` | c_{t-21} / c_{t-126} - 1 |
| Drawdown | `dd_252` | c_t / max(c over the last 252 sessions) - 1 |
| | `dd_days_252` | Sessions since that maximum |
| Volume | `vol_shares_avg_21`, `turnover_avg_21`, `trades_avg_21` | Means over the window, no-trade days counted as 0 |
| | `rel_volume_21` | Share volume on t / `vol_shares_avg_21` of the 21 sessions before t |
| Liquidity | `amihud_21` | mean( \|r\| / turnover ) over the window's traded sessions |
| | `zero_trade_ratio_63` | Share of the last 63 sessions with `traded = false` |
| | `stale_sessions` | Sessions since the last traded bar |
| Range | `range_1` | (high - low) / `px_close` on t, traded sessions only |
| | `gap_1` | `open_price`_t / c_{t-1} - 1 (`open_price` is CSE's session reference price, migration 0002; not asserted to be the first trade) |
| Foreign | `foreign_holding_chg_21` | Foreign holding on t minus 21 sessions earlier, adjusted for share-count actions |
| Market context | `mkt_ret_ew_1`, `_5`, `_21` | Equal-weighted mean of the same-window return over the market-data-eligible universe of t |
| | `mkt_ret_tw_1`, `_5`, `_21` | The same, weighted by `turnover_avg_21` as of the window's start |
| | `mkt_breadth_1` | Share of that universe with `ret_1` > 0 |
| | `rel_ret_21`, `rel_ret_63` | `ret_n` minus `mkt_ret_ew_n` |
| | `beta_126` | Slope of r on the equal-weighted market r over 126 sessions |
| | `xs_rank_ret_21` | Percentile rank of `ret_21` in that universe |

- **Why self-computed market context.** No index series is captured (P3-R3), and no sector history exists (§8). A
  cross-sectional aggregate over the as-of universe is point-in-time by construction and needs no new source. Its
  universe is the eligible set of the **same** T, so a security that is later delisted still counts on the days it
  was eligible.
- The market aggregates use weights fixed at the window's start, so a security's own move inside the window does not
  change its weight.
- Adding, removing or changing a feature is a new registry version and a new configuration. Old snapshots keep the
  version they were built with.

### 10.3 Quality state of a feature value

| State | When |
|---|---|
| `fresh` | Every input bar is visible, traded where the formula needs a real close, and none is flagged |
| `stale` | An end of the window uses a carried-forward close (`stale_sessions` > 0) within `max_stale_sessions` |
| `partial` | The window spans an unknown date within tolerance (§6), or fewer inputs than the window but at least `min_observations` |
| `missing` | Fewer than `min_observations`, the security has no bar, or the staleness limit is exceeded. The value is null |
| `conflicting` | An input bar is `conflicting` (MFB-6, or a disagreement with imported history) |
| `unlabelled` | The window contains an unexplained move (§7.3) or a null volume that MFB-3 could not resolve |
| `untrusted` | The window crosses an ex-date whose factor is `unresolved` |

The worst state of the inputs wins, in the order `untrusted`, `conflicting`, `unlabelled`, `missing`, `partial`,
`stale`, `fresh`. A null value is never replaced by zero or by a cross-sectional fill in MF. Imputation, if any, is a
Phase 5 decision made on the training side only and recorded there.

---

## 11. MF-6: targets

Targets are **labels**. They are computed by the same code as features but live in a separate registry, a separate
table and a separate API, and nothing in the feature path can import the target module (a static test checks the
import graph).

For reference session D and horizon h ∈ {1, 5, 10, 21} sessions (MA §4):

- **Reference close.** `px_ref` on D as of the forecast's own (T, mode): "the last eligible close available at the
  prediction cutoff".
- **Target session.** The h-th proven session after D in the session sequence as of the evaluation time. It is not
  known when the forecast is made, because closures are not computable. The forecast stores h; an *expected* target
  date (h weekdays ahead, less known closures) is display-only and labelled as an estimate.
- **Future close.** `px_ref` on the target session, adjusted back onto D's basis with the actions whose ex-dates fall
  in (D, target]: a split inside the horizon must not look like a crash.
- **Return.** (future_close - reference_close) / reference_close, exactly MA §4's formula, on that common basis.
- **Direction.** `rise` if return > 0, `fall` if < 0, `flat` if = 0. How `flat` is scored is the evaluation layer's
  versioned choice (Phase 9), not MF's.
- **Realised volatility over the horizon.** Sample standard deviation of r over the h sessions, for h ≥ 5.

A target has its own states: `resolved`; `pending` (the target session has not happened or is not visible yet);
`unresolvable_no_trade` (no trade on the target session: the return uses a carried close and says so);
`unresolvable_quality` (a `conflicting` or `untrusted` bar, or an unresolved factor, in the horizon);
`unknown_session_in_horizon` (an unknown date lies in the horizon, so "the h-th session" is not certain). Phase 9
decides which states are scored. MF never drops a row silently.

`target_definition_version` covers all of the above. A change is a new version; old labels are kept.

---

## 12. Leakage tests (MA §11, §57)

Each test builds a small synthetic archive, computes a snapshot at T, then plants information that did not exist at
T and requires the snapshot's bytes to be unchanged.

| ID | Planted after T | Must not change |
|---|---|---|
| ML-1 | A raw observation for a later session | Any feature at T |
| ML-2 | A later observation for an **earlier** session (a late capture or a correction) with `recorded_at` > T | `KNOWN` at T (it may change `AVAILABLE` only if its source availability ≤ T) |
| ML-3 | A changed close on a later session | Any feature at T |
| ML-4 | A corporate action recorded after T with an ex-date before T | `KNOWN` at T |
| ML-5 | A corporate action announced before T with an ex-date after D\* | Any adjusted value at T |
| ML-6 | A new security appearing in a later universe | The universe, the market aggregates and the ranks at T |
| ML-7 | A security removed from every later universe (a delisting) | Its membership and features at T, and the aggregates that included it |
| ML-8 | A `closed` calendar row established after T | The session sequence at T |
| ML-9 | An imported history batch committed after T | `KNOWN` at T |
| ML-10 | An identity link recorded after T | `KNOWN` at T |
| ML-11 | Any of the above | That no feature value depends on a target value, and that the feature modules cannot import the target module |
| ML-12 | A `CURRENT` result passed to a point-in-time consumer | The guard refuses |
| ML-13 | An `observed_at` moved earlier by tampering | Detected: the input hash of the snapshot no longer matches |

The reverse is tested too: planted information at or before T **does** change the result, so a test cannot pass by
ignoring its input.

---

## 13. MF-7: storage, provenance and frozen contracts

### 13.1 Migration `0019_market_feature_foundation.sql` (additive; append-only tables, in the manner of 0015 and 0017)

| Table | Contents |
|---|---|
| `mf_configurations` | Canonical JSON and SHA-256 of: the feature registry version, `adjustment_policy`, `availability_policy`, `eligibility_policy` and its status (`provisional` or `validated` with a report id), `target_definition_version`, tolerances |
| `mf_designations` | Owner-only (the F8 pattern): which configuration is in force for which purpose from when |
| `mf_history_imports`, `mf_history_bars` | An import batch (source name, licence or terms reference, file SHA-256, survivorship label, who, when) and its verbatim rows |
| `mf_corporate_action_observations` | §7.1 |
| `mf_identity_observations` | §8, ticker links |
| `mf_adjustment_factors` | Factor per action as a rational, with its input observation ids, or `unresolved` with a reason |
| `mf_snapshots` | One row per computed snapshot: T, H, mode, label, D\*, configuration id, code version tuple, the input digest, the row count |
| `mf_snapshot_rows` | One row per (snapshot, security): eligibility result, the feature vector as canonical JSON with each value's quality state, the ids of the bars' contributing raw observations as a digest, `row_hash` |
| `mf_target_rows` | One row per (target build, security, reference session, horizon): state, values, `target_definition_version`, input digest |

- Every table refuses UPDATE, DELETE and TRUNCATE for every role, with the trigger pattern of 0010 and 0015.
- No raw CSE response is copied: rows refer to the raw layer by id and digest (G-1 controls 7 and 8 are unaffected).
- Roles: `cse_worker` inserts snapshots and targets; `cse_reader` reads; designations and the owner-entered evidence
  go through P1's owner path (`cse_migrator` acting as `cse_owner`), as P2's acknowledgements and F8's designations do.
  No new role, no `SECURITY DEFINER`.
- **Why store what can be recomputed.** A forecast must point at the exact features it used (MA §23, §42). The stored
  `row_hash` is that anchor, and recomputing a `KNOWN` snapshot later must reproduce it; `verify` (below) checks this.

### 13.2 Packages

- `worker/market_features/`: pure. Bars, calendar, adjustment, universe, eligibility, features, targets, hashing. No
  database, no clock, no network.
- `worker/market_feature_store/`: the loader (one REPEATABLE READ, READ ONLY transaction per read, as F8), the writer,
  `preflight` (the migration's objects, as R-F4's), and `verify` (re-derive stored snapshots from the raw layer and
  compare hashes).
- An operator wrapper `ops/bin/cse-features` and its runbook come with the store step. No timer in Phase 3: the daily
  run is wired when the forecast cycle exists (Phase 8), behind P3's scheduler pattern.

### 13.3 Frozen contracts affected

MF modifies no frozen stage, migration, role or runbook. At implementation, these list-pinning guards will need a
change-control entry each, because they enumerate what exists:

- tests that enumerate migrations (for example `tests/test_ai_provider_boundary.py`, which lists the migration files)
  and any staging or ledger check that states "0001 to 0018";
- P1's `verify_server` append-only list, if MF's tables are to be covered by it (P2's precedent is not to: P2 checks
  its own tables in its own preflight; MF follows that precedent, so P1 is not changed).

The exact list is established by running the full suite with the migration present, and each edit is recorded in
`docs/governance/CLOUD_DECISION_LOG.md` before it is made.

**Consequence for the release sequence.** The build staged for the backfill is `main`'s. MF lives on
`integration/cloud` and reaches `main` only by the owner's decision, after which that build is staged again (MA
§43.1). MF is not needed for the backfill.

---

## 14. The history problem (reserved: P3-R1)

Forward capture alone gives one bar per security per trading day from the first production capture. A 252-session
history then exists a year later, and walk-forward ML needs several years. The options, none of which this design
selects:

| Option | Contact | History | Notes |
|---|---|---|---|
| A. Forward capture only | CSE, already within G-1's scope | Starts at zero | No decision needed. **Every trading day before the first production capture is lost to this route**, so the date of milestone 10 sets the start of the series |
| B. Purchase from CSE's Data Library | A purchase from CSE | As sold | A licensed source; would also change G-1's position for that data. Coverage of delisted securities and of corporate actions must be confirmed before buying |
| C. CSE's own published daily or monthly bulletins | CSE | As published | The 0001 schema already foresees it (`CSE_BULLETIN_DAILY`, `bulletin_recovery_attempts`). Needs a G-1 extension like HB-X2, and a governed transport |
| D. A CSE API endpoint that serves price history, if one exists | CSE | Unknown | Not verified by this project. It would need investigation under G-1 before any use |
| E. A third-party data vendor | The vendor | As sold | The vendor's terms govern. Adjusted and unadjusted prices must be distinguishable, and survivorship is usually `survivors_only` |

**What the design does so that the decision is not a blocker:**

- `mf_history_imports` accepts any source as verbatim rows with its terms reference and survivorship label; no option
  is built in.
- Imported bars never enter `raw_market_observations` (a frozen Stage E table whose `source` values the frozen
  `reconcile` ranks). They stay in MF's own tables and meet captured data only in §5's comparison.
- Import is a file-based, owner-run step. MF itself never fetches.

**Recommendation to the owner.** Decide P3-R1 early, because it determines when Phase 5 can produce anything real,
and start forward capture as soon as milestone 10 allows whatever is chosen. Option B is the only one that also
reduces the G-1 risk rather than adding to it.

---

## 15. Decisions

### Taken [delegated] (recorded in `docs/governance/CLOUD_DECISION_LOG.md`, C-0007)

| ID | Decision | Reason |
|---|---|---|
| P3-D1 | MF reads the append-only raw layer and calls the frozen `reconcile`; it never reads `daily_market_data` | Point-in-time correctness (MA §11, §59); also independent of D-2 |
| P3-D2 | `adjustment_policy` v1 adjusts for share-count actions and rights, not for cash dividends | MA §4 defines the target as a price return. A total-return series can be added as a new policy version without changing v1 |
| P3-D3 | Eligibility ships as policy v0 with status `provisional`; no threshold is final | MA §6 requires empirical testing, and no data exists |
| P3-D4 | Imported history is available at the end of its session date in Colombo plus a configured lag (default: 18:00 Colombo on D), labelled `reconstructed` | The honest statement of an assumption; the lag is configuration |
| P3-D5 | Unknown-date tolerance: 0 for windows ≤ 5 sessions, 2% of the window otherwise | A missed capture should not silently shift short windows |
| P3-D6 | No sector feature in v1; market context is self-computed | No point-in-time sector source exists |
| P3-D7 | Decimal arithmetic with a fixed context; no float | Byte-reproducible hashes (MA §42) |
| P3-D8 | The 0001 `corporate_actions` and `symbol_history` tables are not used | Mutable or unwritten; MF keeps append-only evidence of its own |
| P3-D9 | Targets live in Phase 3, apart from features | They are pure market calculations, and MA §4 wants them versioned; the separation is what prevents label leakage |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P3-R1 | The source of market history (§14) | Any real training or backtest (not before the build) |
| P3-R2 | The source of corporate-action evidence; until then, owner entry from CSE notices | Real adjusted series |
| P3-R3 | Whether P2 should also capture the indices (ASPI, S&P SL20): a P2 change and extra daily requests under G-1 | Index-relative features (not in v1) |
| P3-R4 | The merge of MF into `main`, and with it the re-staging | Production use |

---

## 16. Implementation sequence

Each step is implemented, tested, audited (C-0005) and recorded before the next. All of it runs offline on synthetic
archives built through P2's own fakes (`tests/p2_fakes.py`), so the inputs have the shape real captures have.

| Step | Content | Exit |
|---|---|---|
| MF-1 | Pure package skeleton; bars as of T through the frozen `reconcile`; feature-bar rules MFB-1 to MFB-6; modes and labels | Unit tests; ML-1 to ML-3, ML-12 |
| MF-2 | Session calendar and sequence | Unit tests; ML-8 |
| MF-3 | Corporate-action evidence model, factors, adjusted series, the unexplained-move detector | Unit tests with worked split, bonus and rights examples; ML-4, ML-5 |
| MF-4 | Universe and eligibility | Unit tests; ML-6, ML-7, ML-10 |
| MF-5 | Feature registry and feature set v1 | Every formula against a hand-computed vector; determinism across runs and platforms |
| MF-6 | Targets | Unit tests of each state; ML-11 |
| MF-7 | Migration 0019, store, preflight, `verify`, wrapper, runbook | PostgreSQL 17 tests in CI; append-only for every role; mutation tests of the migration's guards; ML-9, ML-13; change control of the list-pinning tests |

---

## 17. What stays open until real data exists

- The eligibility thresholds (§9) and the unexplained-move threshold (§7.3).
- Whether CSE's closing price behaves as §5's rules assume across years, not days.
- The availability lag of imported history (P3-D4) against whatever the chosen source documents.
- Whether cash dividends matter enough at the one-month horizon to justify a total-return policy version.
- Storage growth: about 300 securities × one snapshot row per day, measured rather than estimated once it runs.

None of these blocks the build. Each blocks calling MF's configuration `validated`.

## 18. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new read-only layer above P2 and P3: market features, universe, targets |
| 2 | Source of truth | The append-only raw observations and run ledger; MF's own append-only evidence; never a mutable table |
| 3 | Information at its timestamp | Defined by mode, T and H (§4); D\* is derived |
| 4 | Provenance retained | Snapshot → row hash → raw observation ids → run → archived response (§13) |
| 5 | Frozen components | None modified. List-pinning tests need change control at MF-7 (§13.3) |
| 6 | Migration | Yes: 0019, additive, at MF-7 |
| 7 | Leakage prevention | Modes and guards (§4), the import-graph separation of targets (§11), tests ML-1 to ML-13 (§12) |
| 8 | Testing | §12, §16; synthetic archives through P2's fakes; PostgreSQL 17 in CI |
| 9 | Reproduction | Deterministic Decimal arithmetic; configuration and input hashes; `verify` re-derives stored snapshots |
| 10 | Route 1 and Route 2 | Route 1's market engines and ML read snapshots; Route 2 receives the same snapshot as structured input. Neither reads the raw layer directly |
| 11 | Later evaluation | Targets (§11) are what Phase 9 scores forecasts against |
| 12 | After downtime | A missed capture is an unknown date: windows become `partial` or `missing`, never silently shifted (§6) |
| 13 | Failure representation | Quality states per value (§10.3), eligibility reasons (§9), target states (§11); null is never zero |
| 14 | Versions | Registry, policies and target definition versions in the configuration hash; code version tuple per snapshot |
| 15 | Grounded reasoning | Every value names its formula, window and inputs, so Route 1's explanation can cite the actual numbers and their quality |

## 19. Audit record

*(Filled in by the audit under C-0005.)*
