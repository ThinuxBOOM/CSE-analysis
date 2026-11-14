# Phase 3: market feature foundation (design)

**Status: DESIGN, revision 6 (2026-11-14). ACCEPTED [delegated] for implementation on `integration/cloud`
(revision 5: C-0026; revision 6 records decisions and clarifications from the implementation's code audits, C-0051).
MF-1 to MF-5 implemented and accepted; MF-6 under re-audit; MF-7 not started. Not the owner's own acceptance, and not
accepted for `main`.**

*Revision 6 (2026-11-14) adds no new mechanism. It records the owner's decision on market history (TradeIQ, C-0035)
and how that source is declared (§14); the universe of a `row_absent` import (§5.4); a liquidity result of its own
(§9); CSE's carried close (§10.2); that a conflicting member's return stays out of m_u (§10.2); four clarifications of
§10.4; and, in §11, the conservative `resolved_at`, the realised volatility on F's price basis and a conflicting
reference close (C-0047, C-0050).* Written by the cloud session on the branch
`integration/cloud` under the owner's instruction of 2026-11-11 (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002,
C-0003). It authorizes no CSE contact, no capture, no purchase and no change to `main`.

*Revision 2 replaces revision 1 (commit `76e4d55`), which its audit failed with one Blocker and eleven Major findings
(§19). Every finding is dispositioned there. The main changes: prices carry the session they were printed on and are
adjusted by that session, not by the bar's date (§5, §7); the market cutoff D\* is defined at market level by a final
capture (§4.3); closures and all run evidence have their own clocks (§4, §6); imported history must state its price
basis (§14); MF has its own security identity (§8); targets have terminal states, a resolution instant and a written
formula (§11).*

*Revision 3 (2026-11-12) answers the second audit, which found the revision 1 Blocker fixed for captured data but
failed revision 2 on eight Major findings (§19). The main changes: the rights factor uses an adjusted cum price
(§7.2); imported bars must declare their no-trade convention (§5.4, §14); a non-final run's observations are
superseded at run level (§5.1); the
exact terminal event and derivation pass MF reads are named (§4.3); the payload cross-check is recomputed under MF's
own tolerances (§5.2); security identity is a deterministic key, not a table (§8.1); labels are protected against
late corporate actions (§11).*

*Revision 4 (2026-11-12) answers the third audit (§19). It found six of revision 2's eight Major findings resolved,
and a new Blocker in something revision 3 had added: the "degraded session", which let a capture that never became
final into the session sequence and so produced false zero returns and wrongly resolved labels for the whole market.
**The degraded session is removed.** A capture that never becomes final leaves a non-final session, as in revision
2: a known gap, with a stated cost and a tolerance. The label state for unexplained moves now stores its unadjusted
return and has a way to be cleared.*

**Governing records:** Master Architecture (MA) §4 to §6, §10, §11, §22, §33 to §36, §39 to §42, §45, §55 (Phase 3),
§56 to §59 and §68; `docs/ops/P2_MARKET_CAPTURE.md` and `docs/ops/P3_SCHEDULER.md` (what market capture records);
`docs/F8_DESIGN.md` §11 and §14 (reproducibility and the consumers' rules). Where this document and those differ, they
win.

**Short name:** MF. Steps are MF-1 to MF-7 (§16). Decisions are P3-D*n* (taken, [delegated]) and P3-R*n* (reserved
for the owner) (§15).

---

## 1. Conclusion

Phase 3 turns the market capture archive into versioned, point-in-time market features, a historical security
universe, and the forecast targets. It is a read-only layer above P2 and P3, as F8 is above F6.4:

```
P2 archive + raw_market_observations (append-only, frozen)      imported history (new, append-only; source is P3-R1)
        \                                                         /
         MF-1  daily bar as of T      (frozen reconcile() over the observations admitted at T)
         MF-2  session calendar as of T
         MF-3  corporate actions and adjustment factors (explicit, versioned)
         MF-4  universe and market-data eligibility as of T
         MF-5  features (registry: source, formula, units, window, availability rule, version, quality state)
         MF-6  targets (the four horizons of MA §4; labels, never features)
         MF-7  snapshot store (append-only, hashed; the provenance anchor of every forecast)
```

1. **The mutable canonical table is not the source.** `daily_market_data` is upserted in place, so it cannot say what
   was known at an earlier time (MA §59). MF reads the append-only `raw_market_observations` and rebuilds each daily
   bar by calling the frozen, pure `worker.reconciliation.reconcile` on the observations admitted at the cutoff (§5).
   The frozen Stage E defect D-2 is in the upsert, not in `reconcile`, so it does not affect MF.
2. **Market data has two clocks** (MA §10), and so does every other piece of evidence MF uses: run results, universe
   bodies, closures, corporate actions, identity links, imports. MF exposes F8's labelled modes (§4).
3. **There is no market history yet, and none is recoverable from the capture path.** `tradeSummary` serves only the
   latest session, and no production capture has run. History can come only from another source, a reserved owner
   decision (P3-R1, §14). The design is source-agnostic so that the decision does not block the build.
4. **The software can be built and frozen on synthetic data. The numbers cannot.** Thresholds ship labelled
   `provisional`; so does one rule about CSE's behaviour that the project has not confirmed (MFB-3, §5).
5. **Not everything MA asks of this layer is met in v1.** Sector-relative features and the index context are not
   built, because no point-in-time source exists for either (§2, P3-R3, P3-R5).
6. **Nothing frozen needs to change** beyond three guards that state the migration range (§13.3).

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Status and where |
|---|---|---|
| Forecasts use only information available at or before the cutoff; explicit automated leakage tests | §11, §57 | Met: §4, §5, §12 |
| Two clocks: publication/availability time and system-knowledge time | §10 | Met: §4 |
| The previous completed trading day is the main market-data basis; the listed market features | §5 | Met, except "market/index context" (self-computed aggregate only) and "sector context" (not built): §10 |
| The trading calendar, not calendar-day arithmetic, determines the target date | §4, §45 | Met: §6, §11 |
| Target definitions are versioned; `return = (future_close - reference_close) / reference_close`; volatility for every horizon | §4 | Met: §11 |
| Eligibility by measurable data and activity criteria, versioned and empirically tested | §6, §22 | Mechanism met; values `provisional`: §9 |
| Inactive and delisted securities stay in history; today's list never defines yesterday's universe | §6, §34, §35 | Met for captured sessions; for imports it depends on the source and is labelled: §8 |
| Raw and analysis-adjusted series distinguishable; adjustments explicit and versioned; splits, consolidations, rights, dividends, bonus shares, ticker changes, mergers, delistings accounted for | §40 | Met for price-changing actions: §7. Ticker changes, mergers and delistings are identity events only: a link or an ending, never a carried-over series (§7.2, §8.1) |
| Every feature has source, formula, units, availability rule, version, quality state | §39 | Met: §10.1 |
| **Sector-relative features** | §5, §12.1, §39 | **Not met in v1.** No point-in-time sector classification exists (§8). Reserved: P3-R5. Phase 4 consequently has no sector-relative engine |
| Quality states `fresh`, `stale`, `partial`, `conflicting`, `missing`, `unlabelled`, `untrusted` | §41 | Met: §10.4 |
| Provenance forecast → market feature → daily observation → capture run → raw CSE response | §42 | Met by stored digests plus deterministic re-derivation (`explain`), not by a stored id list per window: §13 |
| No mutable latest-value tables; no future data in historical forecasts | §59 | Met: §3, §5, §6 |
| The fifteen questions of §68 | §68 | §18 |

---

## 3. Boundary

**MF reads** (never writes), and only these:

| Source | Used for |
|---|---|
| `raw_market_observations` (0001, 0002; append-only since 0010) | Every price, volume, turnover and trade-count value, and the verbatim `raw_payload` |
| `market_capture_runs`, `market_capture_run_events`, `market_capture_security_results`, `market_source_responses`, `market_response_bodies` (0012; append-only) | Which run produced an observation; the run's kind, mode, trading date, terminal state and session evidence (E1, E2); universe and `tradeSummary` membership; the archived `allSecurityCode` bodies |
| `market_schedule_items`, `market_schedule_item_events` (0014; append-only) | P3's no-session evidence for a date (`not_applicable`) |
| `issuer_securities` (0007) | The link from a security to its F5 issuer, for Phase 4 |
| MF's own tables (§13) | Imports, calendar and corporate-action evidence, identity, configurations, snapshots |

**MF never reads** `daily_market_data`, `daily_index_data`, `trading_calendar`, `corporate_actions`,
`symbol_history`, `system_config` or `companies`. A static test enforces the read
list. Closures that P3's operator declares in `trading_calendar` reach MF only as copies in MF's own append-only
evidence table (§6).

**MF never** contacts CSE or any network, changes a frozen stage or migration, writes to a table it does not own, or
treats a staging or synthetic row as production evidence.

**Not owned:** fundamental features and valuation (Phase 4), ML datasets and models (Phase 5), news (Phase 6), index
capture (a P2 change, P3-R3).

---

## 4. Temporal model

### 4.1 The times

| Symbol | Meaning |
|---|---|
| D | A session date: a Colombo calendar date |
| T | The **information cutoff** of a query: an instant |
| H | The **knowledge horizon** of an `AVAILABLE` or `CURRENT` query: the instant up to which recorded evidence may be used. `KNOWN` has no H |

Every piece of evidence has two instants. `recorded_at` is when the system durably held it; `available_at` is when the
source made it public.

| Evidence | `recorded_at` (system knowledge) | `available_at` (source availability) |
|---|---|---|
| A captured observation | `raw_market_observations.created_at` | `observed_at` (the response's receipt; see the notes on clocks below) |
| A run's results (terminal state, E1, E2, membership, universe body) | The `occurred_at` of the **governing terminal event** (§4.3) | The latest `observed_at` of the run's `ok` responses |
| P3 no-session evidence | The time of a `not_applicable` item event **that names a run** (the event P3 writes after no-session snapshots; §6) | The same |
| An imported bar | The import row's own database-default timestamp | A rule of the configuration (P3-D4): the configured local time on D, default 18:00 Colombo. An assumption, so the result is labelled `reconstructed` |
| A closure, corporate action or identity link entered as evidence | The evidence row's database-default timestamp | The source's own notice or announcement date, which the row must carry (a date counts from the end of that Colombo day, F8's rule). A `no_action_confirmed` statement has no such date: its `available_at` is its `recorded_at` |

**Notes on clocks, recorded as limits.**
- Timestamps set by a database default are the transaction's start, not its commit. A row can therefore become visible
  slightly after its `recorded_at`. MF adopts F8's rule (`docs/F8_DESIGN.md` §11): a `KNOWN` result is exactly
  replayable once its T is **settled**, meaning every writing transaction that began at or before T has ended. A live
  snapshot computed at its own T is reproduced from its stored rows, not by replay.
- `observed_at` is the capture host's clock, `created_at` the database's, and T the caller's. In production they are
  one host under P1's time-synchronisation check; the assumed skew is small against the daily cycle and is not
  otherwise corrected.
- For a security that needed a `companyInfoSummery` cross-check, the frozen path stamps the observation with the
  `tradeSummary` response's `observed_at`, a little earlier than the value was actually received. Frozen behaviour;
  it cannot make a value visible before its session's capture began.

### 4.2 Modes

| Mode | Admits evidence with | Label | For |
|---|---|---|---|
| `KNOWN` | `recorded_at` ≤ T | `point_in_time` | Live forecasts; backtests of the live era |
| `KNOWN_RECORDED` | Nothing is recomputed. Among stored snapshots of mode `KNOWN`, under the configuration designated for the purpose, **written at or before the requested instant** (`mf_snapshots.recorded_at`), the one with the latest T is returned (the latest `recorded_at` if two share a T) | `point_in_time` | Reproducing exactly what a forecast used. A forecast pins its snapshot by id and hash |
| `AVAILABLE` | `available_at` ≤ T **and** `recorded_at` ≤ H | `reconstructed` | Backtests and training over an era whose data arrived later |
| `CURRENT` | `recorded_at` ≤ H | `retrospective_current` | Research reports only; never point-in-time |

- A live prediction uses `KNOWN` or `KNOWN_RECORDED` only. No point-in-time use takes a `CURRENT` result. These are
  F8's two consumer rules (`docs/F8_DESIGN.md` §14); a guard in code refuses a `CURRENT` result on a point-in-time
  path.
- A result's hash covers its mode, T, the configuration id, the inputs and the values. H is covered only in
  `AVAILABLE` and `CURRENT`, where it is an input. So the same `KNOWN` query on settled evidence gives the same bytes
  whenever it is run.
- `AVAILABLE` and `CURRENT` require H ≥ T.
- **How this differs from F8.** The mode names are F8's; the labels are MF's own (`point_in_time` where F8 says
  `known` and `known_recorded`). MF's `KNOWN` has no availability clause: what the system held at T is admitted
  whatever its source's publication time. A `KNOWN` result that used an imported bar is point-in-time about the
  system's knowledge and carries the flag `imported_inputs`, because the values are as the source held them on the
  batch's `values_as_of` (§14).
- `AVAILABLE` is monotone in H for fixed T only in what it can see, not in its values: later evidence about a time
  before T may change the answer. That is its meaning, and why it is labelled.

### 4.3 The market cutoff D\* and session finality

MA §11 gives a forecast a `market_data_cutoff`. MF defines it as (T, D\*), where D\* is an output: the latest **final
session** admitted at T.

A captured date D is one of:

| Kind | Condition (all evidence admitted at T under the mode) |
|---|---|
| **Final session** | A `post_close` capture run for D (kind capture, never a sweep) has an admitted terminal event (`succeeded` or `partial`) whose recorded summary shows E1 true (the snapshot shows D's own session), the universe known (P2's dimension B), E2 true (no `tradeSummary` row with an unpublished close), and coverage at or above `min_coverage` (provisional default 0.95). **Coverage** is the summary's raw observations produced divided by expected (P2's dimension C). The **governing terminal event** is the latest such event by `occurred_at`; its run is the **governing run** |
| **Non-final session** | A session is proven for D (E1 true on any run, or any derived observation for D, which migration 0014's own guard also treats as proof), but no run meets the conditions above |
| **Closure** | §6 |
| **Unknown** | §6 |

- D\* never advances onto a non-final session. A capture taken before CSE published the closes (E2 false) therefore
  does not turn "not published yet" into a market-wide zero return: the snapshot stays on the previous D\* and
  records D as pending.
- **Which rows MF reads from a run.** P2 writes its session evidence, universe result, plan, completeness and symbol
  lists into the `details.summary` of each terminal event, and names the derivation pass in `details.derivation`.
  MF reads the governing terminal event's summary and **only the per-security rows of the pass that event names**.
  A pass that no admitted terminal event names (a `reprocess` of a finished run writes one, and a pass in progress
  has rows before its event) is ignored. This is what makes a run's evidence admissible as one unit.
- A terminal event whose details name no derivation pass is never governing.
- **An early capture is not repaired automatically, and what it costs.** P3 resumes the same run and never requests
  an archived `tradeSummary` again, so a capture taken before the closes were published stays E2-false unless an
  operator starts a new run. That is possible for a while: `tradeSummary` shows a session until the next session's
  first trade, so a new run later the same evening, or before the next open, can still be final. After that the date
  stays a **non-final session** for ever: a session MF knows happened and has no bars for. Nothing is taken from a
  snapshot that was mid-session. The cost is stated in §6 and §11: windows that span the gap are `partial` or
  `missing`, and every label whose horizon contains it is `unknown_session_in_horizon`, up to 21 reference sessions
  for the whole market at the one-month horizon. Whether P3 should capture again automatically is a change to frozen
  P3 and is reserved (P3-R7).
- D\* is always a final session. A later non-final session is recorded on the snapshot as pending.
- An imported date is final if its batch admits it (§14).

---

## 5. MF-1: the daily bar as of T

### 5.1 The raw bar

For a security s and a final session D with governing run G:

1. Take the admitted rows of `raw_market_observations` for (s, D) whose `request_attempt_id` is a P2 capture run
   (not a sweep). A row that belongs to no run is not used. A production database holds no synthetic or staging row
   (MA §43.1), and MF's preflight refuses a database that has any.
2. Apply **MFB-7 `superseded_run`**: of the end-of-day observations from `post_close` capture runs, only the
   **governing run's** are passed on. Observations of any other `post_close` run for D stay in the raw layer and are
   listed in the bar's notes. Without this rule the frozen `reconcile` would flag a discrepancy whenever two captures
   of one day differ, which they do whenever the first was early: it compares same-source, same-window values, a
   close of 0 never agrees with a price, and its volume tolerance is 0. Observations from other windows and other
   sources are passed unchanged.
3. Call the frozen `worker.reconciliation.reconcile(rows, tolerances)`. The tolerances are **MF's own**, pinned in the
   MF configuration; P2 reads them at run time from the mutable `system_config`, which MF does not follow. A static
   test pins the SHA-256 of `worker/reconciliation.py`, as P2 does.
4. The result is the **raw bar**: `open_price`, `high`, `low`, `closing_price`, `last_traded_price`,
   `last_traded_date`, `share_volume`, `trade_count`, `turnover`, with `reconcile`'s provenance and discrepancy notes.

**Payload fields.** Two values that the frozen mapper reads only for cross-checks have no column and survive
verbatim in the append-only `raw_payload` of the observation `reconcile` took the close from. MF reads them from
there as bar fields:

- `market_cap_reported`: CSE's `marketCap` for the security, from the `tradeSummary` row when the security traded and
  from `companyInfoSummery` otherwise (P2 requests that endpoint for the securities absent from `tradeSummary`).
- `quantity_issued`: `companyInfoSummery`'s `quantityIssued`, present only when that endpoint was requested for the
  security (non-traded securities and P2's small rotating sample).

Their semantics are **`provisional`**: in the five real fixture pairs in `tests/fixtures/multi_company/`,
`marketCap` equals `price` × `quantityIssued` of that one security exactly, so on that evidence it is per security.
`price` equals `closingPrice` in all five, so they do not show which of the two it is based on. Five rows on one day
establish neither (§17). On a session without a trade the value is CSE's last price times a share count that may
have changed since, so it can be stale in both factors.

`reconcile` uses float tolerances internally and returns `Decimal` values inside its provenance. It is deterministic;
MF's canonical JSON serialises `Decimal` exactly. The "no float" rule of §10.1 is about MF's own arithmetic.

### 5.2 The feature bar

Derived from the raw bar and the governing run's membership evidence by one ordered decision. The first matching row
applies. Nothing here alters the raw bar.

| # | Evidence for (s, D) | `traded` | `px_close` | Volumes in the feature bar | Bar quality |
|---|---|---|---|---|---|
| 1 | In `tradeSummary`; end-of-day observation with `closing_price` > 0 | true | That close | As observed | `fresh`, or as rules MFB-6 and MFB-8 below set it |
| 2 | In `tradeSummary`; end-of-day observation with `closing_price` = 0 | true | null (`close_unpublished`) | As observed | `partial`. The bar has **no reference price for that session** (`price_unknown`, §5.3). Rare on a final session but reachable: E2 reads `tradeSummary`, while for a cross-checked security the frozen mapper takes the close from `companyInfoSummery` first |
| 3 | In the universe, absent from `tradeSummary`; a fallback observation exists | false (rule MFB-3) | null. A non-zero `closingPrice` CSE printed for it is kept as `printed_close_no_trade` and is not a price | 0, with the rule id | `stale` once a carried price exists; `missing` before the security's first traded bar |
| 4 | In the universe, absent from `tradeSummary`; no observation (P2's `source_missing`, `company_missing`, `not_derived`) | false (rule MFB-3) | null | 0, with the rule id | As row 3, flagged `no_observation` |
| 5 | Not covered by the governing run's plan at all, or the observation failed to derive (P2's `mapping_failed`, `insert_failed`, or `company_missing` for a symbol that is in `tradeSummary`) | unknown, or true if the symbol is in `tradeSummary` | null | null | `missing` |

Further flags, applied after the row:

| Rule | Condition | Effect |
|---|---|---|
| MFB-5 `close_outside_range` | `closing_price` outside [`low`, `high`] | Kept: a genuine CSE value (README: 16 of 284 rows). Flag only |
| MFB-6 `discrepancy` | `reconcile` flagged a disagreement | Bar quality `conflicting`. MF does not pick differently from `reconcile` and does not hide the flag |
| MFB-8 `cross_check_disagreement` | The observation's `raw_payload.cross_source_comparison` holds both values of a field (`tradeSummary`'s and `companyInfoSummery`'s). **MF recomputes agreement itself under its own pinned tolerances** and never reads the payload's `agree_exactly`, which is exact equality: in all five real fixtures the two turnover figures differ by a rupee or two, so exact equality would flag every sampled security every day | A disagreement beyond tolerance on a **price** field (`closing_price`, `last_traded_price`) makes the bar `conflicting`. One on turnover or market capitalisation is recorded on that field only and does not change the bar's quality. Both values are `CSE_API`, so `reconcile` cannot see either case |

- **MFB-3 `absent_from_trade_summary_means_no_trade` is `provisional`.** The project has only observed that
  `tradeSummary` "has so far only listed securities with ≥1 trade" (README, open investigations). MF adopts it as a
  stated, versioned assumption, flags every bar that relies on it, and lists it in §17. It is never applied without
  the governing run's recorded membership.
- A volume the raw layer left null is zero only by MFB-3. Otherwise it stays null (`unlabelled`): MA §9's "printed nil
  is never zero", applied to market data.

### 5.3 The reference price and its source session

- `px_close(s, D)` is a close CSE printed for a session on which s traded.
- **`px_ref(s, D)`** is the latest `px_close(s, u)` with u ≤ D, together with **`ref_session(s, D)` = that u**. On a
  traded session `ref_session` = D. `stale_sessions` is the number of final sessions in (u, D].
- **Price unknown.** A bar is `price_unknown` when the security traded, or may have traded, and MF has no close for
  it: row 2 of §5.2, and row 5 unless the governing run's evidence shows the symbol absent from `tradeSummary`. On
  such a session `px_ref` is **undefined**, and it stays undefined on the following sessions until the security next
  prints a close. Carrying the earlier close would assert a zero return over a day it traded. (A final session may
  have a few such bars: `min_coverage` allows up to 5% of observations to be missing.)
- A carried price always travels with its source session. Adjustment (§7) is applied by the source session, never by
  the date of the bar that carries it.

### 5.4 Imported history

Imported bars (§14) supply dates with no captured observation. They never enter `raw_market_observations`.

**An imported row becomes a feature bar by the batch's declared no-trade convention**, never by reading its close as
a traded close. Many series, including "raw" ones, repeat the last close on days without a trade. Taken as a printed
close, such a row would carry its own date as source session, and a repeated cum price on an ex-date would then get
no factor: the revision 1 error again, for imported history.

| Batch's `no_trade_convention` | Rule |
|---|---|
| `volume_zero` | `traded` = volume > 0, or trade count > 0 where the batch has one (either is enough). On a row with `traded = false` the close is not a price: `px_close` is null and the printed value is kept as `printed_close_no_trade` |
| `row_absent` | A row exists only for traded days: every row is `traded = true`. A security with rows before and after a session but none on it has a no-trade bar there, provided the batch declares itself complete **for that security** over the range (`security_complete`); otherwise the bar is `missing`. **Revision 6: the universe of an imported date** under `row_absent` is the securities with a row in the batch on that date **plus** those for which the batch is `security_complete` over a range containing the date (they did not trade). Without `security_complete` a security without a row is not a member that day: its absence is not evidence that it left (Phase 5 counts such labels apart, `era_absence`). A (security, date) the import quarantined is excluded from `security_complete` and is `missing`, never a no-trade bar |
| `unknown` | The batch is admitted to `CURRENT` only |

`mf_history_bars` requires, per row: the security's symbol, the date, the close, and the volume (unless the
convention is `row_absent`); high, low, turnover and trade count are optional. The symbol must be in CSE's own form
(for example `ABC.N0000`), or the batch declares a mapping to it; otherwise one security would get two keys (§8.1). `px_ref` and `ref_session` then follow
§5.3 for imported bars exactly as for captured ones.

Where both
exist for (s, D): `KNOWN` uses the captured evidence alone until the import's `recorded_at`, and from then on, as in
`AVAILABLE` and `CURRENT`, the two are compared field by field under the configuration's tolerances. Agreement is
recorded; disagreement makes the bar `conflicting` with both values kept. No source is silently preferred (MA §9,
§59).

---

## 6. MF-2: the session calendar as of T

CSE's closures follow no computable rule. MF derives sessions and closures from evidence and keeps its own
append-only copy of any evidence that lives in a mutable table. The one use of the weekday is P3's own: Monday to
Friday are candidate days. A Saturday or Sunday is not a candidate and needs no closure evidence; if session evidence
exists for one (a special session), it is a session like any other.

**`mf_calendar_observations`** (append-only) holds closure evidence, each row with its date, kind, source reference,
`available_at` (the notice date) and `recorded_at`:

- `declared_closed`: copied from a `trading_calendar` row `closed` by the MF command `calendar-sync`, or entered
  directly through the owner path. The copy is what MF reads; `verify` reports any later difference between the copy
  and `trading_calendar`, which is mutable and whose `established_at` is only a default. `calendar-sync` and that
  `verify` comparison are the only code allowed to read `trading_calendar`; the static read-list test names them as
  the exception, and neither the pure package nor the loader may.
- `import_calendar_complete`: an import batch's declaration that it contains every session of a date range. Every
  Monday to Friday date in the range for which the batch has no row is then a closure, available on the date itself.

A date at or before D\* is, at T under the mode:

| Kind | Condition |
|---|---|
| Final or non-final session | §4.3; or an admitted import batch holds at least one traded bar for the date |
| **Closure** | A `declared_closed` or `import_calendar_complete` observation is admitted; or P3 recorded a `not_applicable` event **naming a run** for the date (its no-session evidence: snapshots taken on the date whose latest trade is on an earlier date). P3 also writes `not_applicable` without a run when the operator declared the date closed; that case reaches MF only through the `declared_closed` copy, so MF ignores the run-less event |
| **Unknown** | A Monday to Friday date that is none of the above. A missed capture leaves one. It is never counted as a session and never assumed closed (MA §45) |

- **Session evidence beats a closure.** If a date has both, it is a session, and the contradiction is flagged (P3
  flags the same case and overwrites nothing).
- The **session sequence** as of T is the ordered list of final sessions up to D\*. All windows and horizons count
  positions in it. It is per market: a security that did not trade on a final session has a no-trade bar, not a gap.
- A window that spans an unknown date or a non-final session is `partial`. The configuration sets the tolerance:
  0 such dates for windows of 5 sessions or fewer; 1 for windows of 6 to 63; 2% of the window rounded down beyond
  that (2 for 126, 5 for 252) (P3-D5). Beyond it the feature is `missing`.
- **Returns across a gap.** The sequence holds final sessions only, so the return between two consecutive final
  sessions that have an unknown date or a non-final session between them covers more than one trading day. It is
  computed, flagged `spans_gap`, and is what makes a window `partial`. It is never split into invented daily
  returns.

---

## 7. MF-3: corporate actions and adjustment

### 7.1 Evidence

- The 0001 table `corporate_actions` is not used: nothing writes it, it is mutable, and it supersedes by an in-place
  flag. MF keeps `mf_corporate_action_observations` (append-only): one row per statement about an action, verbatim
  terms, source reference, the source's announcement date and ex-date, `available_at` and `recorded_at`.
- **Revision 6 addendum (C-0055): P3-R2 decided.** Splits come from TradeIQ's split workbook; bonus and rights issues from TradeIQ's workbooks with their ex-dates reconstructed by the strict rule of C-0055, outside MF, as evidence marked `reconstructed_from_prices.v1` (`docs/research/TRADEIQ_VALIDATION.md` §6). Events the rule cannot date stay unentered. The owner path remains for anything else.
- **Where the evidence comes from was reserved (P3-R2), until C-0055.** No component captures CSE announcements. Until a source is
  decided, actions are entered through the **owner path** (`cse_migrator` acting as `cse_owner`), from CSE's published
  notices, with the reference kept. This is deliberately stricter than P3's `declare-closed`, which is an operator
  command run as the capture worker: an action changes every adjusted price, a closure only the calendar.
- A later statement about the same action names the row it supersedes. Arrival order alone supersedes nothing. A
  cancelled action is a statement of kind `withdrawn` naming the row it withdraws; from its own instants the action
  no longer applies.

### 7.2 Factors

Each action has an ex-date e and a price factor f (exact rational, numerator and denominator):

| Action | f |
|---|---|
| Sub-division, consolidation | old shares / new shares |
| Capitalisation of reserves (bonus), scrip dividend | old / (old + new) |
| Rights issue, m new per n held at P_sub | TERP / P_cum, with TERP = (n·P_cum + m·P_sub) / (n + m). **P_cum is the reference price on the last session before e, brought onto that session's own basis:** adj(`px_ref`, `ref_session` → the last session before e), by §7.3 |
| Merger, delisting, suspension | No price factor. Identity events (§8.1): the security's series ends, and its open labels become `terminated` (§11). A share-exchange merger is not carried into the acquirer's series in v1 |
| Cash dividend | 1 in `adjustment_policy` v1 (P3-D2). Recorded as evidence; Phase 4 may use it |

- **Factors are built in ex-date order**, because a rights factor depends on the earlier ones. Example: a 2-for-1
  split at e1, then rights 1-for-4 at 40 at e2, last trade 100 before e1 and none since. The raw carried price is
  100, but on the basis of the last cum session it is 50: TERP = (4·50 + 40) / 5 = 48 and f = 0.96. Using the raw
  100 would give 0.88 and misprice everything before e2 by about 8%.
- Actions of one security with the same ex-date are applied in a fixed order: sub-division or consolidation, then
  bonus or scrip, then rights. "The last session before e" is the last **final** session before e. If an earlier
  factor is `unresolved`, a rights factor that depends on it is `unresolved` too.
- **Rights edge cases.** If P_sub ≥ P_cum, f = 1 (`out_of_the_money`: no adjustment, the convention). If P_cum is a
  carried price (the security did not trade on the last cum session), it is used and the factor is flagged
  `cum_price_stale`. If no `px_ref` exists, or the bar that supplies it is `conflicting`, the factor is `unresolved`
  and is never estimated.
- Factors are **computed inside each snapshot** from the evidence admitted at (T, mode), because P_cum is itself an
  as-of value. They are not a global table. The snapshot stores its factor list, one row per security and action
  with kind, ex-date, factor, status and flags, and the hash of that list (§13.1). Consumers that restate per-share
  values (Phase 4 §6.3) read those rows.
- Volume is adjusted by 1/f for the share-count actions (sub-division, consolidation, bonus, scrip) only. Rights
  change the share count by (n + m)/n, not by 1/f, so volume is not adjusted across a rights ex-date; volume windows
  that cross one are `partial`, reason `share_count_changed`. Turnover, a money amount, is never adjusted.

### 7.3 Adjusting a price: by source session

For a price p printed on source session u, expressed on the basis of session b ≥ u, at (T, mode):

```
adj(p, u → b) = p × Π f_k   over the admitted actions k of the security with  u < e_k ≤ b
```

- The condition is on the **session the price was printed on**. A price carried forward across an ex-date without a
  trade was printed cum, so it gets the factor, exactly as if it were still dated u. (Revision 1 applied factors by
  the bar's date, which gave a split-sized false return to any security that did not trade on its ex-date.)
- **`adj_ref(s, t)`** = adj(`px_ref(s, t)`, `ref_session(s, t)` → D\*). **`adj_close(s, t)`** is the same for
  `px_close`, defined on traded sessions only. All series of one snapshot are on the basis of its D\*.
- An unresolved factor inside (u, b] makes the adjusted value `untrusted` and null.
- **By mode.** In `KNOWN` an action applies if its evidence was recorded by T. In `AVAILABLE` it applies if it was
  announced by T (and recorded by H). In both, only actions with e ≤ D\* apply: an announced action with a later
  ex-date changes nothing yet, because prices have not moved. An action recorded after T does not change a `KNOWN`
  result at T even if its ex-date was before T: the series as of T then shows the unexplained jump that the system
  really saw.

**The unexplained-move detector.** It compares consecutive `px_close` values of a security **after applying the
admitted factors between their sessions**, so a cash dividend or an out-of-the-money rights issue (factor 1) between
them does not hide an unentered split. A remaining change beyond the configuration's threshold, not covered by an
admitted `no_action_confirmed` statement for that security and session pair, flags `unlabelled` on
every window containing it, and on every label whose horizon contains it (§11). Each firing is also stored by date
with the snapshot (§13.1), because a consumer may need to know about a move older than any feature window. MF never
infers an action from a price move.

A carried price that sits behind an admitted ex-date is a different case: it is explained and correctly adjusted. It
is `stale` like any carried price, with the reason `ex_date_since_last_trade`, so that a consumer knows the next
trade will print on a new share basis.

Raw and adjusted values are separate fields (`px_ref`, `adj_ref`), and every adjusted value carries the snapshot's
`adjustment_set_hash` (MA §40).

---

## 8. MF-4a: identity and the universe as of T

### 8.1 Security identity

**MF's security key is computed, not stored:** `mf_security_id` = SHA-256 of the canonical pair
(`"cse_symbol"`, the symbol exactly as CSE prints it). A security therefore has the same key in every snapshot and
every replay, whenever it was first seen, and a security that exists only in imported history or that P2 could not
match to a `companies` row has a key like any other. There is no registry table to write, so nothing about identity
depends on when MF first ran.

- The symbol of a captured security comes from the governing run's per-security rows
  (`market_capture_security_results`, append-only), which also carry the `company_id` where P2 had one. MF does not
  read `companies` at all: its `ticker` column is mutable.
- The `company_id` is kept beside the key only so that Phase 4 can follow F5's `issuer_securities` to the issuer.
- Raw observations are keyed by `company_id`, so a security's observations are reached through the per-security
  row's `raw_observation_id`, in the pass the governing terminal event names.
- **Two limits of a symbol-based key, stated:** CSE reusing a symbol for a different security would merge two series
  (no case is known; an identity observation of kind `symbol_reused` splits them from its date), and a link between
  two keys joins their bar series for features and labels only from the link's admission.

- **Ticker changes.** A renamed security appears as a new symbol and so a new key; its series breaks. MF links
  nothing automatically. A link is an identity observation (`mf_identity_observations`, owner path, with a source reference).
  It takes effect from its `recorded_at` in `KNOWN` and from its announcement date in `AVAILABLE`.
- **Security class.** `universe_rule` v1 (versioned, `provisional`) classifies a line by its ticker's class suffix.
  Ordinary voting and non-voting shares are **equity lines**. Rights, warrants, preference shares, debentures and any
  unrecognised class are in the universe but are `not_applicable` for features, eligibility and aggregates. The exact
  suffix table is fixed at MF-4 from the archived `allSecurityCode` evidence and listed in the configuration.

### 8.2 The universe of a session

- **Captured session D:** the symbols of the `allSecurityCode` body archived by D's governing run (§4.3), united with
  that run's `tradeSummary` symbols (P2's own plan is that union). A run without a known universe is never governing.
  CSE's `active` flag in that body is recorded per member and is not a filter (the project's rule that status never
  gates membership).
- **Imported date:** the securities with a row in the admitted import for that date. Whether an import includes
  securities that later delisted is a property of the source, recorded per batch as
  `survivorship: complete | survivors_only | unknown`, and carried into every dataset built on that era (MA §34).
- A captured era is `survivorship: complete` for its final sessions, because each session's universe is read from
  that day's own archive. Unknown dates and non-final sessions are gaps in it, recorded as such. A dataset spanning several eras carries the worst of their labels.
- A security stays in every historical universe it was in.

### 8.3 Sector

`companies.sector` is a mutable current value with no history. MF v1 builds **no sector feature**; this is an unmet
requirement (§2), and its source is reserved (P3-R5).

## 9. MF-4b: market-data eligibility

A security moves through `CSE listed → financially eligible → market-data eligible → forecast eligible` (MA §6). MF
decides the third step for equity lines. Eligibility at (s, D\*, T) is a pure function of the session sequence, the
feature bars and a versioned `eligibility_policy`:

| Criterion (policy v0, `provisional`) | Parameter | Provisional value |
|---|---|---|
| History: final sessions on which s has a feature bar (traded or no-trade) in the universe | `min_history_sessions` | 252 |
| Activity: share of the last 63 final sessions with `traded = true` | `min_traded_ratio` | 0.50 |
| Liquidity: median turnover over the traded sessions of that window | `min_median_turnover_lkr` | 100 000 |
| Freshness: `stale_sessions` at D\* | `max_stale_sessions` | 5 |
| Quality: no `conflicting` or `untrusted` bar in the last `quality_block_window` sessions | `quality_block_window` | 21 |

- Results: `eligible`, `no_data` (no bar at all), `price_unknown` (no reference price at D\*, §5.3), `insufficient_history`, `low_activity` (the traded share), `low_liquidity` (the median turnover; revision 6: a result of its own), `stale`, `blocked_quality`,
  `not_applicable` (security class), each with the measured value of every criterion. They map onto MA §22's
  blockers.
- An older flagged bar does not block the security; it makes the individual features whose windows contain it
  `conflicting` (§10.4).
- **The values are placeholders, labelled `provisional` in the configuration** (P3-D3). The store refuses to mark a
  configuration `validated` without a recorded validation report id, and Phase 5 refuses to pass a model trained
  under a `provisional` policy. Real values are chosen later **on data before any declared final test period** (MA
  §36): tuning eligibility on the test period would be the same leak as tuning a model on it, so the choice is made
  under a Phase 5 experiment protocol.
- Market capitalisation is not a criterion (MA §6).
- Under forward capture alone, no security is eligible for the first 252 sessions, and market-context features need
  eligible members on every session of their window on top of that: `mkt_ret_ew_63` first exists after about 316
  sessions and `beta_126` after about 333 at the earliest (§14).
- A security that has not traded since capture began has no reference price at all, whatever CSE prints as its last
  price, so thinly traded lines take longer still.

---

## 10. MF-5: features

### 10.1 The registry

```
feature_id        stable, e.g. "ret_21"
version           integer; any change to formula, inputs, window or rounding is a new version
family            return | return_history | volatility | momentum | drawdown | volume | liquidity | size | range |
                  market_context
source            the evidence it derives from: captured bars, imported bars, or both; and which bar fields
formula           the definition in words and symbols (below)
window            sessions looked back (and skipped), counted in the session sequence
min_observations  the fewest valid inputs for a value
units             ratio per session | log_ratio per session | lkr | shares | count | sessions | rank
availability_rule which bars must be final and admitted at T (always: sessions up to D* only)
quality           how input states combine (§10.4)
```

MF's own arithmetic is Python `Decimal` in one fixed context (precision 28, half-even), with `ln` and `sqrt` from
`decimal`, results quantized to 12 decimal places. No float is used in MF's arithmetic, so the same inputs give the
same bytes on every platform (P3-D7). Nothing is annualised: every rate is per session.

### 10.2 Definitions common to the set

For a snapshot with cutoff session t = D\*, and final sessions u in the sequence:

- **c_u** = `adj_ref(s, u)`: the carried, adjusted reference price. It exists on every final session from the
  security's first traded bar, except while the price is unknown (§5.3).
- **r_u** = ln(c_u / c_{u−1}): the **session return on the carried series**. It is undefined when either price is
  unknown; the first return after such a stretch runs from the last known price and is flagged `spans_unknown`, as
  a return across a calendar gap is flagged `spans_gap` (§6). m_u inherits both flags from its members. On a
  no-trade session it is 0. On the
  first traded session after no-trade sessions it holds the whole move since the last trade. This is the return a
  holder marking to the last trade would record, and it is defined on every session, so thinly traded securities are
  measured rather than dropped. Each window also reports `traded_sessions`, so a consumer can tell a quiet security
  from an untraded one.
- A window "of n sessions ending at t" is the n consecutive final sessions ending at t.
- **CSE's carried close (revision 6).** From 2015 CSE's official close stays at the previous close on a day too
  little trades, although the security traded (`docs/research/TRADEIQ_VALIDATION.md`, F11; the same in P2's capture,
  MFB-5). MF uses CSE's close as published: such a day is a traded session with r_u = 0. Features based on |r|
  (amihud) understate illiquidity on those days, and `hist_flat_share_h` rises; the move appears on the next printed
  close. This is CSE's definition and is not corrected.
- **A conflicting member (revision 6).** A member whose bar on u is `conflicting`, or whose r_u rests on a price
  printed by a `conflicting` bar, contributes no return to m_u; m_u is then computed from the others under the same
  `min_constituents`.
- **Market return m_u** = the equal-weighted mean of r_u over the equity lines that were market-data eligible **as of
  session u−1**, with that eligibility recomputed from the snapshot's own evidence at (T, mode) and with adjusted
  values on the snapshot's D\* basis. Membership uses no information from session u or later, as an index fixes its
  constituents beforehand; but because it is recomputed from T's evidence, a late correction can give the same u
  different members in two snapshots, which is the point-in-time behaviour intended. It needs at least `min_constituents` members with a return (provisional default 30);
  otherwise it is `missing`. The security itself is a member if eligible; with 30 or more members the effect is small
  and is stated here rather than removed.
- Eligibility does not depend on any market-context feature, so this definition is not circular.
- m_u is a mean of log returns. `mkt_ret_ew_n` compounds it; it is the return of the cross-sectional geometric mean,
  not of an equal-weighted portfolio, and the name `ew` means equal weights across members only.
- One snapshot needs the reconciled bars of every equity line over the longest window (about 300 securities by 380
  sessions). The store caches bars by (security, session, the admitted observation ids), which is safe because the
  cache key is the evidence itself.

### 10.3 Feature set v1

| Family | Feature ids | Definition | `min_observations` |
|---|---|---|---|
| Return | `ret_1`, `ret_5`, `ret_10`, `ret_21`, `ret_63`, `ret_126`, `ret_252` | c_t / c_{t−n} − 1 | Both ends |
| Volatility | `vol_21`, `vol_63` | Sample standard deviation of r over the window | 15, 40 sessions with a defined r |
| | `vol_parkinson_21` | sqrt( mean( ln(high/low)² ) / (4 ln 2) ) over the window's traded sessions with both values (a ratio, so no adjustment) | 10 |
| Momentum | `mom_252_21`, `mom_126_21` | c_{t−21} / c_{t−252} − 1; c_{t−21} / c_{t−126} − 1 | Both ends |
| Drawdown | `dd_252` | c_t / max(c_u over the window) − 1 | 126 |
| | `dd_days_252` | Sessions since that maximum (the latest, if tied) | 126 |
| Volume | `vol_shares_avg_21` | Mean adjusted share volume, no-trade sessions counted as 0 | 15 |
| | `turnover_avg_21`, `trades_avg_21` | Mean turnover and trade count (unadjusted), no-trade sessions as 0 | 15 |
| | `rel_volume_21` | Adjusted share volume on t / mean over the 21 sessions before t; null (`zero_base`) if that mean is 0 | 15 |
| Liquidity | `amihud_21` | mean( \|r_u\| / turnover_u ) over the window's traded sessions with turnover > 0 | 10 |
| | `zero_trade_ratio_63` | Share of the window with `traded = false` | 40 |
| | `stale_sessions` | §5.3 | — |
| Return history | `hist_up_share_h`, `hist_flat_share_h`, `hist_down_share_h`, `hist_mean_ret_h`, `hist_n_h` for h ∈ {1, 5, 10, 21} | Over the 252 h-session returns c_u / c_{u−h} − 1 whose **end** session u lies in the window ending at t: the shares that are > 0, = 0, < 0, their mean, and their number n. `hist_n_h` is always present, even below `min_observations`. An h-session return that spans a gap or an unknown price is left out. For h > 1 the returns overlap, so n is a count, not an effective sample size Every return used ends at or before D\*, so nothing here is a label | 126 |
| Size | `market_cap_reported` | §5.1's payload field on t (`provisional` semantics); null with reason when absent | — |
| | `shares_implied` | `market_cap_reported` / `last_traded_price` on t, only on a traded session; `provisional` | — |
| Range | `range_1` | (high − low) / `px_close` on t; traded sessions only | — |
| | `gap_1` | `open_price`_t / c_{t−1} − 1, with c_{t−1} on t's basis (`open_price` is CSE's session reference price, migration 0002; not asserted to be the first trade) | — |
| Market context | `mkt_ret_ew_1`, `_5`, `_21`, `_63` | exp(Σ m_u over the window) − 1 | Every m_u |
| | `mkt_breadth_1` | Share of the members of m_t with r_t > 0 | `min_constituents` |
| | `rel_ret_21`, `rel_ret_63` | ln(c_t / c_{t−n}) − Σ m_u over the window: the log excess return | Both |
| | `beta_126` | Least-squares slope of r_u on m_u over the window | 80 |
| | `xs_rank_ret_21` | Percentile rank of `ret_21` among the members of m_t with a value; ties take the average rank | `min_constituents` |

- Revision 1's `foreign_holding_chg_21` and turnover-weighted market returns are removed. Foreign holding comes only
  from `companyInfoSummery`, which the daily plan requests for non-traded securities and a small rotating sample, so a
  traded security has a value on few days (§19, finding 8; P3-R4). The turnover-weighted aggregate added a second
  membership-and-weight rule for little v1 benefit.
- No index series is captured (P3-R3) and no sector history exists (§8.3), so market context is the self-computed
  aggregate only.
- The return-history family uses the same pure return primitive as the labels, from a module that both the feature
  path and the target path import. The ban of §11 is on the feature path importing the **target** module; a shared
  arithmetic primitive is not a label.
- `px_ref`, `ref_session` and `stale_sessions` at D\* are stored with every snapshot row (§13.1), so Phase 4 reads
  them from the snapshot and never from the raw layer.
- Adding, removing or changing a feature is a new registry version and a new configuration. Old snapshots keep theirs.

### 10.4 Quality state of a feature value

The state describes the **value**. `missing`, `conflicting` and `untrusted` values are null; `unlabelled`, `partial`,
`stale` and `fresh` values are present. The reason for a null is recorded beside it.

| State | When |
|---|---|
| `fresh` | Every input bar is final, admitted and unflagged, and the ends of the window are traded sessions where the formula uses a price |
| `stale` | An end of the window is a carried price, within `max_stale_sessions` |
| `partial` | The window spans an unknown date or non-final session within tolerance (§6), or has fewer inputs than the window but at least `min_observations` |
| `unlabelled` | The window contains an unexplained move (§7.3) or a null volume that MFB-3 could not resolve |
| `missing` | Fewer than `min_observations`; no bar; the staleness limit exceeded; an end of the window at a session where the price is unknown; more gaps than the tolerance (§6); a zero base |
| `conflicting` | An input bar is `conflicting` (MFB-6, MFB-8, or a disagreement with imported history) |
| `untrusted` | An input's adjustment crosses an `unresolved` factor |

Where several apply, the first in the order `untrusted`, `conflicting`, `missing`, `unlabelled`, `partial`, `stale`,
`fresh` is recorded, with the others listed.

**Clarifications (revision 6, from the code audits):**
- A `conflicting` input is any bar the formula reads, at either end or inside the window, and, for formulas that use
  prices, any price the formula uses that was printed by a `conflicting` bar, including the base of a return bridged
  over unknown prices from before the window. Features that use no price (volume means, Parkinson, the activity
  ratio) are not affected by a carried price.
- `amihud_21` and `vol_parkinson_21` take traded sessions only, so "fewer inputs than the window" is their normal case
  and does not make them `partial`; `min_observations` alone applies.
- `dd_days_252` counts from the latest session at the window's maximum of c_u, which on a carried series can be a
  no-trade session carrying that price.
- History (§9) counts every session with a feature bar, including a bar with `traded = unknown`. A null is never replaced by zero or by a cross-sectional fill in MF.
Imputation, if any, is a Phase 5 decision made on the training side only.

---

## 11. MF-6: targets

Targets are **labels**. They are computed by MF's code but live in a separate registry, a separate table and a
separate API, and nothing in the feature path can import the target module (a static test checks the import graph).

A **target build** is one computation of labels: it has its own (T_eval, H, mode), a configuration id and a
`target_definition_version`. A label is keyed by (target build, **reference snapshot row**, horizon): the reference
snapshot row is the stored row of security s in the snapshot whose D\* is the reference session D. The label therefore
uses exactly the reference close a forecast made from that snapshot saw, not a later-corrected one.

For horizon h ∈ {1, 5, 10, 21} sessions (MA §4):

- **Reference close** P_D = `px_ref(s, D)` from the reference snapshot row, printed on s_D = `ref_session(s, D)`.
- **Target session** F = the h-th final session after D in the session sequence admitted at the target build. It is
  not known when the forecast is made. The forecast stores h; an *expected* target date is display-only and labelled
  an estimate.
- **Future close** P_F = `px_ref(s, F)` at the target build, printed on s_F = `ref_session(s, F)`.
- **Return**, on D's basis, by source sessions (§7.3):

  ```
  return = ( P_F / Π f_k ) / P_D − 1      over the admitted actions k with  s_D < e_k ≤ s_F
  ```

  A split inside the horizon is divided out. If the security has not traded since before an ex-date, s_F is before it,
  no factor applies, and the carried cum price is compared with itself.
- **Direction:** `rise` if return > 0, `fall` if < 0, `flat` if = 0. How `flat` is scored is the shared scoring
  policy's versioned choice (Phase 5 §6).
- **Realised volatility:** for h ≥ 5, the sample standard deviation of r_u (§10.2) over the h sessions in (D, F],
  as the target build sees them, with all h required. For h = 1, where a standard deviation does not exist, the target is the **absolute return**
  |ln(1 + return)|, the usual one-session proxy, stored under its own name `abs_ret_1` so that it is never mistaken
  for a standard deviation (P3-D10). MA §4 asks for expected volatility at every horizon; this is how the one-session
  case is met.

**States.** Every label has exactly one:

| State | Meaning |
|---|---|
| `pending` | F has not happened or is not final and admitted yet |
| `resolved` | s traded on F |
| `resolved_carried` | s did not trade on F; the return uses a carried price (`stale_sessions` recorded). It is a real outcome for a holder and is not called unresolvable |
| `terminated` | s left the universe inside the horizon and did not return within the policy's grace period (provisional: 21 final sessions), or an identity observation records a delisting, suspension or merger. **No return is stored**: the label keeps the last `px_ref`, the session it left, and the evidence. It is terminal, so the worst outcomes do not drop out of evaluation as eternal `pending` (MA §34). A one-session label of such a security therefore stays `pending` for up to the grace period before it terminates |
| `unexplained_move_in_horizon` | The detector of §7.3 fires between s_D and s_F: a move beyond the threshold with no admitted action. Corporate actions are entered by hand and can arrive after the target session; without this state a split not yet entered would be stored as a real fall of about half. **The label stores its return under the adjustment set admitted at the build, marked provisional**, so that a scoring policy can use it. The state ends in one of two ways: the action is entered, or the owner enters a `no_action_confirmed` statement for the security and the two sessions (owner path, with a source reference). Either way a later build resolves the label, with a later `resolved_at`. A genuine large move therefore does not stay unresolved for ever |
| `unresolvable_quality` | A `conflicting` or `untrusted` input, or an unresolved factor, in the horizon |
| `unresolvable_missing_price` | The reference price at D or at F is undefined (§5.3), or, for the volatility label, a return inside the horizon is. No value is stored. A label is built for every equity line in the reference snapshot, so this state is also where a line with no price yet ends up |
| `unknown_session_in_horizon` | An unknown date or non-final session lies in (D, F], so "the h-th session" is not certain |

**Precedence**, when several apply: `pending` first (F is not yet final, or the security has left the universe and
its grace period is still running); then `terminated`;
`unknown_session_in_horizon` (no value stored); `unresolvable_missing_price`; `unresolvable_quality`;
`unexplained_move_in_horizon`; `resolved_carried`; `resolved`.

**Revision 6 (C-0047, C-0050).** A reference close printed by a `conflicting` bar, in the stored row or in the
build's own view at D, is an input: the label is `unresolvable_quality`. The realised volatility is computed from the
prices of (D, F] adjusted to **F's** basis, so a corporate action after F changes nothing. `resolved_at` is
**conservative by construction**: the latest admission instant over every session from the build's last printed close
at or before D through the furthest session the label read (including a session after F whose bar ended an absence's
grace period), over every statement behind the security's factors and confirmed moves, and over the instants of any
withdrawn or superseding statement and of calendar or identity evidence. A later `resolved_at` only delays a label's
use. An absence already running at D is examined like one that starts inside the horizon. When the build has no close from the stored s_D through F
(s_F before s_D), the stored reference close is one the build no longer supports and the return is undefined: the
label is `unresolvable_quality`, evidence `reference_not_in_build` (C-0058). The caller passes in `extra_instants` the
latest admission instant of all the build's calendar evidence and identity evidence, and of every withdrawn or
superseded statement of the security. A label whose reference close is undefined resolves at once to
`unresolvable_missing_price`, ahead of `pending` and `terminated` (Minor 13 of §19).

Each label stores **`resolved_at`**: the latest admission instant, under the build's mode, over **every** input it
used, namely the target session's finality (or the terminating evidence), each bar in the horizon, each
corporate-action or `no_action_confirmed` statement applied, and any calendar or identity evidence it rests on. A label recomputed after a late action therefore carries the later instant, and Phase 5's rule "the label
was known by the training cutoff" (MA §11, "future training observations") stays true. It also stores the adjustment
set it used, and the flag `reference_revised` when the build's own view of `px_ref(s, D)` differs from the reference
snapshot row's.

**Several builds.** A later build never replaces an earlier one; both stay. A consumer always names the build it
uses: a Phase 5 dataset pins the target-build id in its manifest, and Phase 9 records the build made at evaluation
time. Two builds that disagree on a label are reported by `verify`. MF never drops a label silently. Which states are
trained on and scored, and how, is the shared scoring policy's decision (Phase 5 §6), used by Phases 5, 9 and 10.

---

## 12. Leakage tests (MA §11, §57)

Each test builds a small synthetic archive, computes a snapshot at T, plants information that did not exist at T, and
requires the snapshot's bytes to be unchanged. Each also asserts the converse: the same information planted at or
before T does change the result.

| ID | Planted | Must not change |
|---|---|---|
| ML-1 | A raw observation for a later session | Any feature at T |
| ML-2 | A later observation for an earlier session (`recorded_at` > T), for the same or **another** security | `KNOWN` at T, including the market aggregates and ranks |
| ML-3 | A changed close on a later session | Any feature at T |
| ML-4 | A corporate action recorded after T with an ex-date before T | `KNOWN` at T |
| ML-5 | A corporate action announced before T with an ex-date after D\* | Any adjusted value at T |
| ML-6 | A new security in a later universe | The universe, aggregates and ranks at T |
| ML-7 | A security absent from every later universe | Its membership and features at T, and the aggregates that included it |
| ML-8 | A closure observation recorded after T; a `trading_calendar` row mutated or backdated after `calendar-sync` | The session sequence at T; `verify` reports the mutation |
| ML-9 | An import batch recorded after T | `KNOWN` at T |
| ML-10 | An identity link recorded after T | `KNOWN` at T |
| ML-11 | Any target value | No feature depends on it; the feature modules cannot import the target module |
| ML-12 | A `CURRENT` result passed to a point-in-time consumer | The guard refuses |
| ML-13 | A stored snapshot's inputs altered (staged in a disposable test database by a superuser with the triggers dropped, as R-F4's tamper tests do) | `verify` reports the hash mismatch |
| ML-14 | Run evidence recorded after T: a terminal event, E1/E2, membership rows, a universe body | D\*, finality, membership and bars at T |
| ML-15 | A configuration or designation recorded after T | Results pinned to the configuration in force at T |
| ML-16 | In `AVAILABLE`: an observation with `available_at` > T and `recorded_at` ≤ H | The result at (T, H) |
| ML-17 | In `AVAILABLE`: evidence with `available_at` ≤ T recorded after H | The result at (T, H) |
| ML-18 | Data after a label's target session; an action with an ex-date after it | A `resolved` label |
| ML-19 | A split with **no trade on the ex-date**; a split inside a horizon with no trade through the target session; a split followed by a rights issue with no trade between (§7.2's example); the same split in **imported** history under each admissible no-trade convention | No spurious return in any feature or label, and the hand-computed factors (the revision 1 Blocker and its two residual cases) |
| ML-20 | A capture before closes were published (E2 false), then a second run the same day; and the same with no second run | D\* does not advance on the first; after the second, no `conflicting` bar, including for a security that had a fallback observation in the first run and traded later (MFB-7). With no second run, the date stays a non-final session: no bar from it is used, a label whose horizon contains it is `unknown_session_in_horizon` and never a `resolved` zero return, and returns across it are flagged `spans_gap` |
| ML-21 | A corporate action entered after a label's target session; and a genuine move later covered by `no_action_confirmed` | A build before gives `unexplained_move_in_horizon` with a provisional return; a build after gives the adjusted (or confirmed) return, `resolved`, with a later `resolved_at` |
| ML-24 | On a final session, a traded security whose observation failed to derive (row 5) or whose close is unpublished (row 2), not trading the next day | No zero return and no carried price over that day; `px_ref` undefined until the next printed close; the labels that need it are `unresolvable_missing_price`; the first later return is flagged `spans_unknown` |
| ML-22 | A `reprocess` pass, or the rows of a pass in progress, with no terminal event naming it | Not read |
| ML-23 | A stored `AVAILABLE` snapshot, or a `KNOWN` snapshot written after the requested instant | Never returned by `KNOWN_RECORDED` |

---

## 13. MF-7: storage, provenance and frozen contracts

### 13.1 Migration `0019_market_feature_foundation.sql` (additive; append-only, in the manner of 0015 and 0017)

| Table | Contents |
|---|---|
| `mf_configurations` | Canonical JSON and SHA-256: feature registry version, `adjustment_policy`, `availability_policy`, `universe_rule`, `eligibility_policy` and its status, `target_definition_version`, tolerances, thresholds |
| `mf_designations` | Owner-only (the F8 pattern): which configuration is in force for which purpose from when |
| `mf_identity_observations` | §8.1 (security keys are computed and need no table) |
| `mf_history_imports`, `mf_history_bars` | An import batch and its verbatim rows (§14) |
| `mf_calendar_observations` | §6 |
| `mf_corporate_action_observations` | §7.1, including `withdrawn` and `no_action_confirmed` statements |
| `mf_snapshots` | One row per snapshot: its own `recorded_at`, T, H where the mode has one, mode, label, D\*, pending non-final sessions, configuration id, code version tuple, the input digest, `adjustment_set_hash`, the row count |
| `mf_snapshot_factors` | One row per (snapshot, security, action): the action kind, ex-date, factor as numerator and denominator, status (`applied`, `out_of_the_money`, `no_price_factor`, `unresolved`), flags (`cum_price_stale`), and the ids of the statements it came from. It lists every admitted action of the security with an ex-date at or before D\*, whatever the feature windows, including those whose factor is 1 by policy (status `no_price_factor`). This is the snapshot's factor list; `adjustment_set_hash` covers it |
| `mf_snapshot_move_flags` | One row per (snapshot, security, detector firing): the two sessions and the two raw `px_close` values of an unexplained move (§7.3), over the security's whole admitted history, not only the feature windows, with a status `open` or `confirmed_no_action`. Covered by the snapshot's input digest. Phase 4 needs these by date |
| `mf_snapshot_rows` | One row per (snapshot, security): the security key, its symbol, and the `company_id` from the latest governing run that names the symbol (null if none ever did); eligibility result; the feature vector as canonical JSON with each value's state and reasons; `px_ref`, `ref_session`, `stale_sessions` and **`adj_ref`** at D\* (the price on the snapshot's own share basis, which differs from `px_ref` when an ex-date lies between the last trade and D\*; it is the one Phase 4 must use against per-share values); the ids of the D\* bar's contributing raw observations; a digest of the ids behind every window; `row_hash` |
| `mf_target_builds`, `mf_target_rows` | §11. Each label row stores its state, values, `resolved_at`, the adjustment set used and a `row_hash` |

- Every table refuses UPDATE, DELETE and TRUNCATE for every role, with the trigger pattern of 0010 and 0015.
- **The migration's text must satisfy the existing guards over all migrations:** no binary column type, no column
  named in the manner of a file, document, storage or local path or a blob (`tests/test_document_retrieval.py`
  scans every migration for these), and no credential-like name. An import batch's source file is therefore recorded
  as `source_sha256` and `source_label`, never as a path. The guard scans comments too, so the migration's comments
  avoid those words as well.
- No raw CSE response is copied: rows refer to the raw layer by id and digest (G-1 controls 7 and 8 are unaffected).
- Roles: `cse_worker` inserts snapshots and targets; `cse_reader` reads; designations and owner-entered evidence go
  through P1's owner path. No new role, no `SECURITY DEFINER`.
- **Provenance, stated exactly.** The D\* bar's observation ids are stored. For the windows, a digest is stored, and
  `explain` re-derives the full id list from the append-only raw layer and checks it against the digest. The chain of
  MA §42 is therefore recomputable and verified, not materialised row by row.

### 13.2 Packages

- `worker/market_features/`: pure. Bars, calendar, adjustment, universe, eligibility, features, targets, hashing. No
  database, no clock, no network.
- `worker/market_feature_store/`: the loader (one REPEATABLE READ, READ ONLY transaction per read, as F8), the writer,
  `calendar-sync`, `preflight`, `explain` and `verify` (re-derive settled stored snapshots and compare hashes).
- An operator wrapper `ops/bin/cse-features` and its runbook come with MF-7. No timer in Phase 3.

### 13.3 Frozen contracts affected

MF modifies no frozen stage, migration, role or runbook. Three guards state the migration range and will need a
change-control entry each when 0019 is added:

- `ops/staging/inner/stage_checks.sh` (`ledger`: the literal version list "1 … 18");
- `ops/staging/inner/stage_scenarios.sh` (the upgrade scenario's "0018 recorded exactly once" and its to-0017 basis);
- `tests/test_rf4_runbook_rehearsal.py` (a step asserts that exactly `0018_f4_structure_persistence.sql` is applied).

`tests/test_ai_provider_boundary.py` only requires at least 16 migrations and needs no change (revision 1 named it
wrongly). P1's `verify_server` append-only list is not changed: MF checks its own tables in its own preflight, as P2
does. The final list is established by running the full suite with the migration present, and each edit is recorded
in `docs/governance/CLOUD_DECISION_LOG.md` before it is made.

**Consequence for the release sequence.** The build staged for the backfill is `main`'s. MF lives on
`integration/cloud` and reaches `main` only by the owner's decision, after which that build is staged again (MA
§43.1). MF is not needed for the backfill.

---

## 14. The history problem (reserved: P3-R1)

Forward capture alone gives one bar per security per trading day from the first production capture. A 252-session
history exists a year later, and walk-forward ML needs several years. The options, none of which this design selects:

| Option | Contact | Notes |
|---|---|---|
| A. Forward capture only | CSE, already within G-1's scope | No decision needed. **Every trading day before the first production capture is lost to this route** |
| B. Purchase from CSE's Data Library | A purchase from CSE | A licensed source; would also change G-1's position for that data. Coverage of delisted securities, the price basis and corporate actions must be confirmed before buying |
| C. CSE's own published daily or monthly bulletins | CSE | The 0001 schema already foresees it. Needs a G-1 extension like HB-X2, and a governed transport |
| D. A CSE API endpoint that serves price history, if one exists | CSE | Not verified by this project. It would need investigation under G-1 before any use |
| E. A third-party data vendor | The vendor | The vendor's terms govern. Usually back-adjusted and survivors-only |

**What an import batch must declare** (`mf_history_imports`), or it is refused:

| Field | Values | Effect |
|---|---|---|
| `price_basis` | `raw`, `adjusted`, `unknown` | Only `raw` is admitted to `KNOWN` and `AVAILABLE` and to MF-3's adjustment. A back-adjusted series has later splits and rights built into earlier prices: using it at an earlier T would be "using future corporate actions" (MA §11), and adjusting it again would be wrong. `adjusted` and `unknown` batches are admitted to `CURRENT` only, labelled `basis_not_raw` |
| `survivorship` | `complete`, `survivors_only`, `unknown` | Carried into every dataset (§8.2) |
| `no_trade_convention` | `volume_zero`, `row_absent`, `unknown` | How a row becomes a traded or no-trade bar (§5.4). `unknown` is admitted to `CURRENT` only |
| `calendar_complete`, `security_complete` | A date range, or none | Enable closures in that range (§6) and no-trade bars for absent rows (§5.4) |
| `values_as_of` | The date the source's values were taken | Vendors restate. MF cannot know what the value was on D; the batch is "as held on `values_as_of`", and every result from it is `reconstructed` |
| Source name, terms or licence reference, `source_sha256`, who, when | — | Provenance |

- Imported bars never enter `raw_market_observations`. Import is a file-based, owner-run step; MF never fetches.
- An imported date is a final session when an admitted `raw`-basis batch has at least one traded bar for it.
- **Decided (revision 6): the TradeIQ archive** (the public copy `TradeIQ-CSE/cse-dataset`; owner decision C-0035,
  G-1 extension E1; validation `docs/research/TRADEIQ_VALIDATION.md`). Its batches are declared, per source workbook:
  `price_basis = raw` (131 of 138 splits show the ratio's fall on the effective date); `no_trade_convention =
  row_absent` (rows only on traded days; a printed zero is not a price); `calendar_complete` for each year from 1992
  whose dates match the archive's daily index workbook; `security_complete` for each security between its first and
  last row in a year's workbook, less the quarantined (security, date) pairs, on the evidence that the workbook is
  CSE's full daily list of traded securities; `survivorship = unknown` for 1991 to 2000 (the exports lost rows against
  their own stated counts) and `complete` from 2001, pending a de-listing check, because no de-listing list exists;
  `values_as_of` = the archive commit's date; provenance = repository, commit, file, sheet, row and SHA-256. Splits
  are entered as corporate-action evidence with `available_at` = the announcement date, or the effective date where
  none is given (later than the truth, so never a leak). Bonus and rights issues wait for P3-R2.
- **Recommendation to the owner (revision 5, kept for the record).** Decide P3-R1 early, because it determines when Phase 5 can produce anything real,
  and require a raw price basis and delisted securities from whatever source is chosen. Option B is the only one that
  also reduces the G-1 risk rather than adding to it.

---

## 15. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0007, C-0010 and C-0017)

| ID | Decision | Reason |
|---|---|---|
| P3-D1 | MF reads the append-only raw layer and calls the frozen `reconcile`; never `daily_market_data` | Point-in-time correctness (MA §11, §59); independent of D-2 |
| P3-D2 | `adjustment_policy` v1 adjusts for share-count actions and rights, not cash dividends | MA §4 defines the target as a price return; a total-return policy can be a later version |
| P3-D3 | Eligibility ships as policy v0, `provisional`; real values are chosen on pre-test data under a Phase 5 protocol | MA §6, §36; no data exists |
| P3-D4 | Imported history is available at a configured local time on its session date (default 18:00 Colombo), labelled `reconstructed` | One stated assumption; the time is configuration |
| P3-D5 | Gap tolerance: 0 for windows ≤ 5 sessions, 1 up to 63, 2% of the window beyond | A missed capture should not silently shift short windows |
| P3-D6 | Superseded by §2 and P3-R5: the absence of sector features is an unmet requirement with a reserved decision, not a design choice | Audit finding 11 |
| P3-D7 | MF's arithmetic is Decimal in a fixed context | Byte-reproducible hashes (MA §42) |
| P3-D8 | The 0001 tables `corporate_actions`, `symbol_history` and `trading_calendar` are not read; MF keeps append-only evidence of its own | They are mutable or unwritten |
| P3-D9 | Targets live in Phase 3, apart from features | Pure market calculations that MA §4 wants versioned; the separation prevents label leakage |
| P3-D10 | The one-session volatility target is the absolute return, named `abs_ret_1` | A standard deviation of one return does not exist; MA §4 needs every horizon |
| P3-D11 | Session returns are defined on the carried series (0 on no-trade sessions) | Thin securities are measured, not dropped (MA §6); one reading, stated |
| P3-D12 | D\* requires a final session: E1, E2, a known universe and coverage ≥ `min_coverage` | An early or broken capture must not become a market-wide zero return |
| P3-D13 | MFB-3 (absent from `tradeSummary` means no trade) is adopted as a `provisional` assumption | Observed so far, not confirmed; needed for any thin security to have a bar |
| P3-D14 | Corporate actions and identity links are entered through the owner path | They change every adjusted value |
| P3-D15 | Only `raw`-basis imports with a declared no-trade convention are admitted to point-in-time modes | MA §11; §5.4 |
| P3-D16 | Withdrawn in revision 4. A capture that never becomes final leaves a non-final session; nothing is taken from it | The degraded session asserted end-of-day facts from a mid-session snapshot |
| P3-D17 | The security key is a hash of the symbol, not a registry row | Identity must not depend on when MF first ran |
| P3-D18 | Labels have the state `unexplained_move_in_horizon`, and `resolved_at` covers every input including actions | Actions are entered by hand and arrive late |
| P3-D19 | Revision 6: `resolved_at` is conservative by construction; the volatility is on F's basis; a conflicting reference close makes a label `unresolvable_quality` | Three code audits found gaps in a case-by-case rule (C-0047, C-0050) |
| P3-D20 | Revision 6: `low_liquidity` is an eligibility result of its own; a conflicting member's return stays out of m_u | Code audit 2 (m6, and the result name) |
| P3-D21 | Revision 6: TradeIQ batches declared as in §14, with `security_complete` from each year's workbook less the quarantined pairs | The universe of a `row_absent` import (Phase 5 audit, M8) |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P3-R1 | The source of market history (§14) | Any real training or backtest |
| P3-R2 | The source of corporate-action evidence; until then, owner entry from CSE notices | Real adjusted series |
| P3-R3 | Whether P2 should capture the indices (ASPI, S&P SL20): a P2 change and extra daily requests under G-1 | Index-relative features |
| P3-R4 | Whether P2 should capture foreign holding for every security daily: a P2 change and about 270 more requests a day | A foreign-holding feature |
| P3-R5 | The source of a point-in-time sector classification | Sector-relative features and engines (MA §5, §12.1, §39) |
| P3-R6 | The merge of MF into `main`, and with it the re-staging | Production use |
| P3-R7 | Whether P3 should capture a session again when the first capture was early (E2 false): a change to frozen P3, with extra requests under G-1. Until then an operator can do it by hand before the next session opens | Avoiding non-final sessions and their cost (§4.3) |

---

## 16. Implementation sequence

Each step is implemented, tested, audited (C-0005) and recorded before the next. All of it runs offline on synthetic
archives. Whether P2's fakes (`tests/p2_fakes.py`) can build archives with the needed variety is checked at MF-1; if
not, MF's tests build the rows directly through the frozen insert functions.

| Step | Content | Exit |
|---|---|---|
| MF-1 | Pure package skeleton; modes and admission; finality and D\*; bars through the frozen `reconcile`; the feature-bar decision table; `px_ref` with its source session | Unit tests of every row of §5.2; the bar-level parts of ML-1 to ML-3; ML-12, ML-14, ML-16, ML-17, ML-20, ML-22 |
| MF-2 | Calendar evidence, session sequence | Unit tests; ML-8's sequence part (its `verify` part runs at MF-7) |
| MF-3 | Corporate-action evidence, factors, adjustment by source session, the detector | Worked split, bonus and rights examples, including every rights edge case; ML-4, ML-5, ML-19 |
| MF-4 | Identity, universe rule, eligibility | Unit tests; ML-6, ML-7, ML-10 |
| MF-5 | Feature registry and set v1 | Every formula against a hand-computed vector; determinism across runs and platforms; the aggregate and rank parts of ML-2, ML-6 and ML-7 |
| MF-6 | Targets | Unit tests of each state and of the precedence; ML-11, ML-18, ML-19, ML-21; the label parts of ML-20 and ML-24 |
| MF-7 | Migration 0019, store, `calendar-sync`, preflight, `explain`, `verify`, wrapper, runbook | PostgreSQL 17 tests in CI; append-only for every role; mutation tests of the migration's guards; ML-9, ML-13, ML-15, ML-23; change control of the three guards |

---

## 17. What stays open until real data exists

- MFB-3: whether absence from `tradeSummary` always means no trade.
- Whether E2 and `min_coverage` identify a final capture reliably, and how often sessions stay non-final.
- The eligibility thresholds, `min_constituents`, the unexplained-move threshold and the tolerances.
- The security-class suffix table (§8.1), from real `allSecurityCode` bodies.
- The tolerances MFB-8 should use for turnover and market capitalisation (the frozen set has none for them).
- What `marketCap` means across many days, security classes and share-count actions (§5.1).
- The availability time of imported history against whatever the chosen source documents.
- Whether cash dividends matter enough at the one-month horizon to justify a total-return policy version.
- How often a capture is early and a session ends up non-final (§4.3), which decides whether P3-R7 matters.
- A corporate action smaller than the unexplained-move threshold, entered late, gives a `resolved` label that is
  wrong by the action's factor until a later build. The threshold trades that against flagging genuine moves.
- Which no-trade convention real history sources use (§5.4).
- Storage growth, measured once it runs.

None of these blocks the build. Each blocks calling MF's configuration `validated`.

## 18. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new read-only layer above P2 and P3: market features, universe, targets |
| 2 | Source of truth | The append-only raw observations and run ledger; MF's own append-only evidence; never a mutable table |
| 3 | Information at its timestamp | Defined by mode, T and H over each evidence type's two instants (§4); D\* is derived |
| 4 | Provenance retained | Snapshot → row hash → the D\* bar's observation ids and a verified digest of the windows' ids → run → archived response (§13.1) |
| 5 | Frozen components | None modified. Three range-stating guards need change control at MF-7 (§13.3) |
| 6 | Migration | Yes: 0019, additive, at MF-7 |
| 7 | Leakage prevention | Modes and guards (§4), finality (§4.3), adjustment by source session (§7.3), the import-graph separation of targets (§11), tests ML-1 to ML-24 (§12) |
| 8 | Testing | §12, §16; synthetic archives; PostgreSQL 17 in CI |
| 9 | Reproduction | Deterministic Decimal arithmetic; configuration and input hashes; exact replay for settled T, stored rows otherwise; `verify` |
| 10 | Route 1 and Route 2 | Route 1's market engines and ML read snapshots; Route 2 receives the same snapshot as structured input. Neither reads the raw layer |
| 11 | Later evaluation | Targets (§11), with terminal states and `resolved_at`, are what Phase 9 scores forecasts against |
| 12 | After downtime | A missed capture is an unknown date and an early one a non-final session: returns across the gap are flagged, windows become `partial` or `missing`, labels over it are `unknown_session_in_horizon`, and D\* does not advance |
| 13 | Failure representation | Quality states per value (§10.4), eligibility reasons (§9), label states (§11); null is never zero |
| 14 | Versions | Registry, policies, universe rule and target definition in the configuration hash; code version tuple per snapshot |
| 15 | Grounded reasoning | Every value names its formula, window, inputs and state, so Route 1's explanation can cite the actual numbers |

## 19. Audit record

**Revision 1 (commit `76e4d55`): audited 2026-11-11 (cloud, fresh-context agent; C-0005). Verdict: FAIL**, one
Blocker, eleven Major, nine Minor, three Notes. The auditor verified the design's factual claims about `reconcile`,
D-2, the append-only status of the raw layer and the 0012 tables, the archived `allSecurityCode` bodies, and the
adjustment formulas, and ran no tests. Dispositions in revision 2:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Blocker | Carried prices were adjusted by the bar's date, giving false returns and labels across an ex-date without a trade; the return formula named two different series | **Fixed.** Prices carry their source session; adjustment is by source session (§5.3, §7.3); every feature uses `adj_ref` as defined in §10.2; the label formula is written out (§11); test ML-19 |
| 2 | Major | D\* ignored run completeness and E2; a second capture made bars `conflicting`; one flagged bar blocked a security for a year | **Fixed.** Finality (§4.3); MFB-7 (§5.1); `quality_block_window` (§9); test ML-20 |
| 3 | Major | Closures were unprovable for imports and in `AVAILABLE`; P3's no-session evidence was ignored | **Fixed.** Closure evidence with two clocks, `import_calendar_complete`, P3's `not_applicable` events (§6, §3) |
| 4 | Major | `trading_calendar` was read as point-in-time truth although mutable | **Fixed.** MF reads only its own append-only copy; `verify` reports drift; session evidence beats a closure (§6); test ML-8 |
| 5 | Major | `KNOWN` was not reproducible as specified; no commit-time column exists; H was in the hash; `KNOWN_RECORDED` was dropped silently | **Fixed.** F8's settled rule, database-default timestamps with the stated skew, H out of the `KNOWN` hash, `KNOWN_RECORDED` added (§4.1, §4.2) |
| 6 | Major | Imports could inject future information (back-adjusted prices); delisted securities had no key; two availability rules | **Fixed.** Mandatory `price_basis` and `values_as_of` (§14); `mf_securities` (§8.1); one rule (P3-D4) |
| 7 | Major | Target gaps: no terminal state for delisting, no one-session volatility, undefined counts, a misnamed state, no snapshot key, no resolution instant, no formula | **Fixed.** §11: `terminated`, `abs_ret_1`, all h sessions required, `resolved_carried`, keyed by reference snapshot row, `resolved_at`, the formula |
| 8 | Major | `foreign_holding_chg_21` is not computable from what P2 captures | **Fixed.** Removed; reserved as P3-R4 (§10.3) |
| 9 | Major | No-trade handling ambiguous and resting on an unconfirmed rule; evidence visibility undefined for non-observation evidence; E1 only in the terminal event | **Fixed.** One ordered decision table (§5.2); MFB-3 labelled `provisional` (P3-D13, §17); instants per evidence type (§4.1); non-final sessions (§4.3) |
| 10 | Major | The universe of a captured session was under-defined | **Fixed.** Governing run, union with `tradeSummary`, security-class rule, `active` recorded not filtering, constituent rule and minimum (§4.3, §8, §10.2). Pre-P2 observations: none exist in any production database, and MF admits only observations that belong to a run |
| 11 | Major | Sector context dropped by a delegated decision and not shown as unmet | **Fixed.** Shown as not met (§2); reserved as P3-R5; P3-D6 withdrawn |
| 12 | Major | Volatility and return definitions ambiguous | **Fixed.** One reading, the carried series (§10.2, P3-D11); per-session units; guards for zero bases and ties (§10.3) |
| 13 | Minor | The named list-pinning test was wrong; migration text guards not mentioned | **Fixed.** §13.3 names the three real guards; §13.1 states the text constraints |
| 14 | Minor | The `declare-closed` analogy was inaccurate | **Fixed.** Owner path, stated as a deliberate difference (§7.1, P3-D14) |
| 15 | Minor | Rights edge cases; factors had no as-of key; foreign holding adjustment wrong | **Fixed.** §7.2: edge cases, factors computed per snapshot, volume not adjusted across rights; foreign holding removed |
| 16 | Minor | Statements not qualified by mode | **Fixed.** §7.3, §8.1 |
| 17 | Minor | Tolerances followed a mutable table; the float claim was too wide | **Fixed.** MF's own pinned tolerances; the claim scoped (§5.1, §10.1) |
| 18 | Minor | No `source` attribute; provenance overstated; the payload cross-check invisible | **Fixed.** Registry `source` (§10.1); provenance stated exactly (§13.1, §18 Q4); MFB-8, with its feasibility to be confirmed (§5.2, §17) |
| 19 | Minor | Whether a state describes the value or the inputs | **Fixed.** §10.4 |
| 20 | Minor | Missing leakage tests | **Fixed.** ML-14 to ML-20, and ML-2, ML-8, ML-13 extended (§12) |
| 21 | Minor | History counted sessions without a bar; MA §36 not addressed for thresholds; no `no_data` outcome | **Fixed.** §9 |
| 22 | Note | Cross-checked securities' `observed_at` is slightly early | Recorded as a limit (§4.1) |
| 23 | Note | Three clocks | Recorded as a limit (§4.1) |
| 24 | Note | Under forward capture nothing is eligible for a year | Stated (§9) |

**Revision 2 (commit `eeeb10d` and two later clarifications): audited 2026-11-11/12 (cloud, fresh-context agent).
Verdict: FAIL**, no Blocker, eight Major, ten Minor, five Notes. The auditor worked the split, bonus, rights and
label cases by hand and confirmed the source-session mechanism is correct for captured data, including the boundary
(a price printed on the ex-date gets no factor); it noted that N1 and N2 below are the revision 1 Blocker's class in
two residual cases. Of the 24 earlier findings it judged 14 resolved and 10 partly resolved, the remainder being the
findings below. Dispositions in revision 3:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| N1 | Major | The rights factor used a raw carried cum price across an earlier ex-date | **Fixed.** P_cum is adjusted onto the last cum session's basis; factors built in ex-date order, with the worked example (§7.2); ML-19 |
| N2 | Major | Imported bars had no traded / no-trade rule, so the Blocker returned for imported history | **Fixed.** Mandatory `no_trade_convention`, the mapping and the required columns (§5.4, §14); ML-19 |
| N3 | Major | Labels had no protection against late corporate actions; `resolved_at` ignored them; no rule for several builds | **Fixed.** State `unexplained_move_in_horizon`; `resolved_at` over every input; builds never replace each other and are pinned (§11); ML-21 |
| N4 | Major | MFB-7 was incomplete; the described recovery does not happen under P3; one early capture cost weeks of windows and labels | **Fixed in part; the degraded-session part was withdrawn in revision 4 (below).** Supersession at run level (§5.1); the permanence stated; degraded sessions (§4.3, P3-D16); P3-R7 reserved; ML-20 |
| N5 | Major | Which terminal event and derivation pass MF reads was undefined | **Fixed.** The governing terminal event and the pass it names (§4.3); ML-22 |
| N6 | Major | The cross-check rule would have flagged every sampled security daily | **Fixed.** MF recomputes agreement under its own tolerances; price fields only affect bar quality (§5.2, MFB-8) |
| N7 | Major | `KNOWN_RECORDED` selection was unsafe | **Fixed.** Mode, designated configuration and `recorded_at` (§4.2, §13.1); ML-23 |
| N8 | Major | The lifecycle of `mf_securities` was undefined | **Fixed.** The key is computed; no table; `companies` is no longer read (§8.1, §3, P3-D17) |
| N9 | Minor | Coverage had no formula; "latest" had no key | **Fixed.** §4.3 |
| N10 | Minor | Which rows count was only in the audit record | **Fixed.** §5.1 step 1 |
| N11 | Minor | The fixture claim about price basis; row 2 reachable on a final session | **Fixed.** §5.1, §5.2 |
| N12 | Minor | Two causes of `not_applicable`; `calendar-sync` reads a table MF "never reads"; weekends; rounding | **Fixed.** §6, §4.1 |
| N13 | Minor | Differences from F8's modes not stated; H ≥ T; import label wording | **Fixed.** §4.2 |
| N14 | Minor | Two flags mapped to no state; no withdrawal statement | **Fixed.** §7.2, §7.3, §7.1 |
| N15 | Minor | The snapshot row stored only the raw reference price | **Fixed.** `adj_ref` stored and named as the one Phase 4 uses (§13.1) |
| N16 | Minor | `terminated` and state precedence under-specified; return and volatility could disagree | **Fixed.** §11: no return stored, the precedence, `reference_revised` |
| N17 | Minor | Wrong cross-references; test sequencing; cold start understated; what `mkt_ret_ew` is | **Fixed.** §2, §9, §10.1, §10.2, §10.3, §16 |
| N18 | Minor | Mergers not addressed though §40 was marked met | **Fixed.** §2's row and §7.2 say what is and is not met |
| N19 | Note | The text guard scans comments | Stated (§13.1) |
| N20 | Note | The three guards verified; no other found | — |
| N21 | Note | "Complete from the first capture" too strong | **Fixed.** §8.2 |
| N22 | Note | Membership recomputed from T's evidence; cost | Stated, with the cache (§10.2) |
| N23 | Note | A never-traded security has no reference price | Stated (§9) |

**Revision 3 (commit `9992346`, with two later additions): audited 2026-11-12 (cloud, fresh-context agent). Verdict:
FAIL**, one Blocker, three Major, eleven Minor. It verified N1 by arithmetic, N5 and N6 against P2's code (the
terminal event's summary and pass number; the payload's comparison keys) and N8 against migration 0012, and judged
N1, N2, N5, N6, N7 and N8 resolved, N3 and N4 partly. All four new findings were in mechanisms revision 3 had added.
Dispositions in revision 4:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Blocker | On a degraded session every traded security carried the previous close: market-wide zero returns and `resolved` labels with a wrong return | **Fixed by removing the degraded session** (§4.3, P3-D16 withdrawn). Row 2 of §5.2 has no reference price (§5.3). ML-20 |
| 2 | Major | "Every other security is treated as on a final session" asserted end-of-day facts from a mid-session snapshot | **Fixed** with it: nothing is taken from a non-final capture |
| 3 | Major | The degraded session was not integrated with the governing run, the sequence, D\*, the quality states or market context | **Fixed** with it. The sequence is final sessions only, in every section; D\* is always final (§4.3); gaps and their returns are defined once (§6) |
| 4 | Major | `unexplained_move_in_horizon` was permanent for genuine moves and stored no return | **Fixed.** The provisional return is stored; `no_action_confirmed` clears it (§11, §13.1) |
| m1 to m11 | Minor | The import table and the finality of an imported date; `row_absent` and disagreeing volume fields; the join from the key; symbol format and reuse; same-day actions and the cum session; `KNOWN_RECORDED` tie-break; `pending` during the grace period and `resolved_at`'s inputs; that an early capture can still be repaired before the next open; stale text; MFB-8's tolerances; tests | **Fixed** in §14, §5.4, §8.1, §7.2, §4.2, §11, §4.3, §17, §12 |

**The cost of the fix, stated.** One early capture that is not repaired before the next session opens now costs up
to 21 reference sessions of one-month labels for the whole market and makes short windows over it `missing`. That is
the honest price of not inventing data; P3-R7 is how to avoid it.

**Revision 4 (commit `39e9d67`): audited 2026-11-12 (cloud, fresh-context agent). Verdict: FAIL**, no Blocker, one
Major, sixteen Minor. It found the degraded mechanism gone from all operative text, the sequence consistently final
sessions only, the gap rules coherent with §10.2, and the stated cost (21 reference sessions) correct by its own
count; it found no contradiction with the Master Architecture. The one Major: a security that traded on a final
session but has no usable close (row 2; and row 5, which the text did not except) was carried as if it had not
traded, a false zero return for that security, and no label state covered a missing price. **Fixed in revision 5:**
the `price_unknown` rule (§5.3), `spans_unknown` (§10.2), the label state `unresolvable_missing_price` (§11), the
eligibility result (§9), ML-24. Minors fixed with it: the detector compares closes after admitted factors and honours
`no_action_confirmed` (§7.3, §4.1); the snapshot tables' fields (§13.1); `hist_n_h` (§10.3); ML-21's wording.

**Revision 5 (commit `d34b8bf`): audited 2026-11-12 (cloud, fresh-context agent). Verdict: PASS WITH FINDINGS**, no
Blocker, no Major, sixteen Minor. The auditor worked the missing-close example session by session (rows 2 and 5) and
found no sentence that asserts a zero return or a carried price over a day the security traded; it checked the
detector on an unentered split behind an entered dividend, an entered split and a confirmed move, and found each
correct; and it confirmed against P2's code the status names and that E2 reads `tradeSummary` only. It did not re-read
the Master Architecture this round.

**The Minor points are carried into implementation** as items to settle at the named step, each recorded in the
implementation record when settled. None changes the design:

| # | Point | Settled at |
|---|---|---|
| 1 | Whether `unresolvable_missing_price` covers a whole label row or return and volatility get separate states. Default: separate states per value, so a computable return is not discarded | MF-6 |
| 2 | `stale_sessions`, and the stored `px_ref`, `ref_session`, `adj_ref`, are null with reason `price_unknown` while the price is unknown | MF-1 |
| 3 | The order of eligibility results when several apply; freshness is not evaluated while the price is unknown | MF-4 |
| 4 | A no-trade bar inside an unknown-price stretch is `missing`, reason `price_unknown` | MF-1 |
| 5 | Row 5 for a symbol absent from `tradeSummary` is `traded = false` by MFB-3; `traded = unknown` is left out of the activity ratios | MF-1, MF-4 |
| 6 | A `spans_unknown` return counts as an input like a `spans_gap` one and makes the window `partial`; it is left out of `beta_126` | MF-5 |
| 7, 8 | Wording of r_u's formula and of m_u (a member without a return is left out; denominators are members with a value) | MF-5 |
| 9 | Return history leaves out an h-return only when one of its ends is at an unknown price | MF-5 |
| 10 | The import cases of `price_unknown` (a `row_absent` bar without `security_complete`; a traded row without a close) | MF-1 |
| 11 | A rights factor whose cum session is `price_unknown` is `unresolved` | MF-3 |
| 12 | The detector with an `unresolved` factor between the closes (not evaluable, recorded as such); confirmed moves stay stored; a confirmation is tied to its session pair | MF-3 |
| 13 | A label whose reference price is already undefined may be resolved to `unresolvable_missing_price` at once | MF-6 |
| 14 | ML-24's bar part belongs to MF-1's exit and its `spans_unknown` part to MF-5's | MF-1, MF-5 |
| 15 | The header has no paragraph for revision 5 | This note |
| 16 | `resolved_carried` requires a defined `px_ref` at the target session | MF-6 |
