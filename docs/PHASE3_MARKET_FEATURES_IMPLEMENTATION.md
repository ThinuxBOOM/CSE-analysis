# Phase 3: market feature foundation (implementation record)

**Design:** `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, revision 6 (C-0051), accepted [delegated] for implementation on
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0026). **Branch:** `integration/cloud` only. Nothing
here is on `main`, nothing is frozen, and nothing has run against real data: no production data exists.

Each step below is implemented, tested, audited by a fresh-context agent (C-0005) and recorded before the next.

## MF-1: modes, finality, bars, the reference price

**Status: ACCEPTED on the branch (C-0037: first code audit reported no Blocker; its findings are fixed or carried as recorded in "Code audit 1").**

### What was built

| File | Content | Design |
|---|---|---|
| `worker/market_features/__init__.py` | Package statement: pure, no database, clock, network or file | §3, §13.2 |
| `worker/market_features/modes.py` | `Query` (KNOWN, AVAILABLE, CURRENT), admission by the two instants, `require_point_in_time` | §4.1, §4.2 |
| `worker/market_features/canonical.py` | Canonical JSON with exact Decimals (a float is refused), SHA-256, the computed security key | §8.1, §10.1, §13.1 |
| `worker/market_features/sessions.py` | `TerminalEvent`; finality; the governing terminal event; final and non-final sessions; D\* | §4.3 |
| `worker/market_features/bars.py` | MFB-7 (`superseded_run`); the raw bar through the frozen `reconcile`; the five-row feature-bar decision; MFB-3, MFB-5, MFB-6, MFB-8; the payload fields | §5.1, §5.2 |
| `worker/market_features/reference.py` | `px_ref` with its source session, `stale_sessions`, and the `price_unknown` rule | §5.3 |
| `tests/test_mf1_unit.py` | 18 tests | §12, §16 |

`KNOWN_RECORDED` returns a stored snapshot and so belongs to the store (MF-7); it is not in this step.

### Design points settled here (the fifth audit's Minor list, design §19)

| # | Point | How it was settled |
|---|---|---|
| 2 | `stale_sessions` and the reference price while the price is unknown | `Reference` carries `None` for `px_ref`, `ref_session` and `stale_sessions`, with state `price_unknown` |
| 4 | A no-trade bar inside an unknown-price stretch | The stretch continues: the reference stays `price_unknown` until the next printed close (`test_r2`) |
| 5 | Row 5 for a symbol absent from `tradeSummary` | It is row 4, `traded = false` by MFB-3. Row 5 is only a symbol in `tradeSummary` without an observation (traded, price unknown) or a symbol with no row in the governing pass (traded unknown, price unknown) |
| 10 | Import cases of `price_unknown` | Not in this step: imported bars arrive with MF-3 and MF-4's inputs. Carried forward |
| 14 | ML-24's bar part | `test_b4` and `test_r2` |

Two further choices made in code, stated here because the design leaves them to the implementer:

- **Tie-break of the governing event:** the latest by `recorded_at`, then by run id, so the choice is deterministic
  when two events share an instant.
- **MFB-8's tolerances** are read from MF's tolerance set under the keys `cross_check_<field>_tolerance_pct`, default 0.
  The design says the frozen set has none for turnover and market capitalisation; the values used in the tests
  (0.1% for prices, 0.5% for turnover and market capitalisation) are test values, not a configuration.

### Tests and what they show

`tests/test_mf1_unit.py`, 18 tests, pure and offline. Run in the cloud session with `PYTHONDONTWRITEBYTECODE=1`,
Python 3.13.18: **18 passed**. Full offline suite afterwards: **2195 passed, 680 skipped** (2177 before this step,
plus these 18). `.github/ci/groups.py --check`: partition ok, 72 test files; the new file lands in group L15 on Linux
and W3 on Windows by the partition's own rule.

| Design test | Covered by | Part covered |
|---|---|---|
| ML-1 to ML-3 | `test_b8` | Bar level: an observation recorded after the cutoff does not change the bar; recorded at the cutoff, it does |
| ML-12 | `test_m2` | A `CURRENT` result, or a mislabelled one, is refused |
| ML-14 | `test_s3` | Run evidence recorded after the cutoff changes neither D\* nor the final sessions; at the cutoff it does |
| ML-16, ML-17 | `test_m1`, `test_s3` | `AVAILABLE` admission by both instants |
| ML-20 | `test_s2`, `test_b5` | D\* does not advance on an early capture; after a second run no bar is `conflicting`, including the fallback-then-traded case; and the frozen `reconcile` is shown to flag exactly that without MFB-7 |
| ML-22 | `test_b4` | A row of another pass or another run is refused |
| ML-24 | `test_b4`, `test_r2` | Bar and reference parts: no zero volume, no carried price over a day the security traded |
| Every row of §5.2 | `test_b1` to `test_b4` | Rows 1 to 5, and a symbol that is not a member |
| MFB-5, MFB-6, MFB-8 | `test_b6` | Including that `agree_exactly` is ignored in both directions, and the real fixtures' turnover difference is not a conflict |
| Frozen `reconcile` unchanged | `test_g1` | The SHA-256 P2 pins, and that MF calls that module |
| Purity and the read list | `test_g2` | Imports limited to the standard library, this package and the frozen `reconciliation`; no database, network, clock or forbidden table name in code |

### Not done, not verified

- No PostgreSQL test: this step has no database code. The loader that turns P2's rows into `TerminalEvent`,
  `SecurityResult` and observation dicts is MF-7, and the mapping from P2's `details.summary` to those fields is
  untested until then.
- The tests build evidence by hand in the frozen code's shapes; they do not run P2's capture path. Whether
  `tests/p2_fakes.py` can produce the needed variety (design §16) was not tried in this step.
- Nothing ran against real CSE data.
- The labels' parts of ML-20 and ML-24, and the aggregate parts of ML-2, belong to MF-5 and MF-6.

---

## MF-2: the session calendar as of T

**Status: ACCEPTED on the branch (C-0037: no Blocker; the Major, tests only, is fixed).**

### What was built

| File | Content | Design |
|---|---|---|
| `worker/market_features/calendar.py` | `CalendarObservation` (`declared_closed`, `import_calendar_complete`), `NoSessionEvidence` (P3's `not_applicable` event that names a run), admission of closure evidence under the query, `classify_day` (final, non-final, closure, unknown, not a candidate), `session_sequence` (final sessions up to D\*, the gap dates between consecutive ones, contradictions), `gap_tolerance` (P3-D5), `window_gap_state` | §6 |
| `tests/test_mf2_unit.py` | 6 tests | §6, §12 |

The module is pure. It takes the sets of final and non-final dates from the caller (MF-1's `sessions.py`, plus
admitted imports once MF-4/MF-7 exist), and closure evidence as values. `calendar-sync`, the table
`mf_calendar_observations` and the `verify` comparison with `trading_calendar` are MF-7.

Choices made inside the design's words, to be checked by the audit:

- An `import_calendar_complete` closure is admitted with `available_at` = the end of that Colombo day (23:59:59
  +05:30), the rule §4.1 gives a date-only source instant. The design says "available on the date itself".
- P3's no-session evidence carries one instant (the event's `recorded_at`); it is used as both instants.
- A gap is counted per date: two unknown dates between two consecutive final sessions count as two towards the
  tolerance.
- `window_gap_state(sequence, end, n)` covers the `n` returns ending at position `end`, that is, the stretch from
  position `end − n` to `end`.

### Tests and what they show

`tests/test_mf2_unit.py`: **6 passed**. Full offline suite: **2201 passed, 680 skipped**. `groups.py --check`:
partition ok, 73 test files.

| Design test | Covered by | Part covered |
|---|---|---|
| ML-8 (sequence part) | `test_c4` | A closure recorded after the cutoff changes nothing; recorded at the cutoff, it removes the gap; `AVAILABLE` admits by the notice date and H; an import's closure is not available before the end of its day |
| §6 kinds | `test_c1`, `test_c3` | Each kind of day; a weekend needs no evidence; a special weekend session is a session; the run-less event is refused as evidence; an import's range and its rows |
| Session evidence beats a closure | `test_c2` | The date stays a session and is flagged |
| Sequence, `spans_gap` | `test_c5` | Final sessions only; a non-final session is a gap, a closure and a weekend are not |
| P3-D5 | `test_c6` | The tolerance at 1, 5, 6, 21, 63, 64, 126, 252; ok, partial, missing, too short |

### Not done, not verified

- No database code and no PostgreSQL test in this step; ML-8's `verify` part is MF-7.
- Imported sessions are represented only by the caller's `final_dates`; the import path itself is MF-4/MF-7.

---

## MF-3: corporate actions and adjustment

**Status: ACCEPTED on the branch (C-0037: no Blocker; both Majors, wrong rights factors, are fixed).**

### What was built

| File | Content | Design |
|---|---|---|
| `worker/market_features/actions.py` | `Statement` and its validation; `applying` (admission, supersession by name only, withdrawal); `build_factors` (ex-date order, the fixed same-date order, rights by TERP with P_cum on the last cum session's basis, every rights edge case); `adj` by source session; `adj_volume`; `ex_date_since_last_trade`; `adjustment_set_hash`; `detect_moves` and `unlabelled` | §7 |
| `tests/test_mf3_unit.py` | 10 tests, every expected number worked by hand | §7, §12 |
| `tests/test_mf1_unit.py` | `test_g2`'s list of allowed standard-library modules gains `fractions` | §13.2 |

Factors and adjusted values are exact rationals (`fractions.Fraction`); nothing is rounded in this step.

Choices made inside the design's words, to be checked by the audit:

- **Two applying statements for the same security, kind and ex-date** (neither names the other): both factors are
  `unresolved`, flag `conflicting_statements`. The design says arrival order supersedes nothing; applying both would
  double the action. This does not apply to the kinds without a price factor.
- **Rights with the same ex-date as a sub-division or bonus:** P_cum is brought onto the last cum session's basis and
  then multiplied by that day's earlier factors, so the rights terms are read on the share basis the earlier action
  leaves ("applied in a fixed order").
- A statement naming a row of **another security** supersedes nothing and is returned for the caller to report.
- Rights `unresolved` reasons: `no_cum_session`, `no_cum_price`, `cum_price_unknown` (Minor 11),
  `earlier_factor_unresolved`. MF-1 gives a `conflicting` bar `price_unknown`, so "the bar that supplies it is
  conflicting" arrives here as `cum_price_unknown`.
- `ex_date_since_last_trade` counts every action with a price-factor kind, including an out-of-the-money rights
  issue and an unresolved factor (the share basis changed in each case).
- **Minor 12:** a comparison with an `unresolved` factor between the closes is stored as `not_evaluable` and does not
  itself set `unlabelled` (the unresolved factor already makes the adjusted values untrusted); a confirmed move is
  stored as `confirmed`; a confirmation matches only its exact session pair.
- A move makes a window or horizon over (start, end] `unlabelled` when the stretch between its two closes overlaps
  it.

### Tests and what they show

`tests/test_mf3_unit.py`: **10 passed**. Full offline suite: **2211 passed, 680 skipped**. `groups.py --check`:
partition ok, 74 test files.

| Design test | Covered by | Part covered |
|---|---|---|
| ML-4 | `test_a6` | An action or a withdrawal recorded after T changes nothing at T; recorded at T, it does; `AVAILABLE` by announcement and H |
| ML-5 | `test_a6` | An announced action with an ex-date after D\* gives no factor; once D\* reaches it, it does |
| ML-19 (factor and series parts) | `test_a4`, `test_a5` | §7.2's worked example (0.96, not 0.88); a split with no trade on the ex-date gives a flat adjusted series, and the by-bar-date rule is shown to give the false return |
| Worked examples, rights edge cases | `test_a2`, `test_a3` | Split, consolidation, bonus, no-factor kinds; TERP; `cum_price_stale`; out of the money (equal and above); no price; no cum session; unknown price |
| Detector | `test_a8` | Fires after the admitted factors; a factor-1 action hides nothing; confirmation by pair and by admission; not evaluable; threshold |
| `adjustment_set_hash` | `test_a9` | Covers factor-1 rows; independent of order |

### Not done, not verified

- No database code. The owner path that enters statements, and the table, are MF-7.
- ML-19's label part and its import part belong to MF-6 and MF-4/MF-7; ML-21 is MF-6.
- The threshold's value is configuration (MF-7); the tests pass it in.

---

## MF-4: identity, the universe, eligibility

**Status: ACCEPTED on the branch (C-0043: audit 2 reported no Blocker; its findings are fixed or carried as recorded in "Code audit 2").**

### What was built

| File | Content | Design |
|---|---|---|
| `worker/market_features/universe.py` | The class suffix table and `security_class`; `IdentityObservation` (`link`, `symbol_reused`) and `series_segments`; `universe` (the union, the `active` flag recorded and never a filter); `worst_survivorship`; `EligibilityPolicy` v0 (`provisional`) and `eligibility` with every measured value | §8, §9 |
| `tests/test_mf4_unit.py` | 8 tests | §8, §9 |
| `tests/test_mf1_unit.py` | `test_g2`'s list of allowed standard-library modules gains `re` | §13.2 |

Choices made inside the design's words, to be checked by the audit:

- **The suffix table is provisional and not from archived bodies.** The design fixes it "from the archived
  `allSecurityCode` evidence"; the cloud session has none. The table uses the classes the Phase 2 design records as
  occurring (N, X, D, W, R, P, U, B): N and X are equity lines, the others and anything unrecognised are
  `not_applicable`. The names given to U and B are placeholders. **It must be confirmed against real archived bodies
  before `universe_rule` v1 leaves `provisional`** (laptop).
- **Minor 3, the order when several results apply:** `not_applicable`, `no_data`, `price_unknown`,
  `insufficient_history`, `blocked_quality`, `low_activity`, `stale`. Every failing criterion is also listed in
  `failed`. Freshness is not evaluated while the price is unknown.
- **Liquidity has no result name of its own in §9**; a median turnover below the minimum (or no turnover at all)
  gives `low_liquidity` (`universe.py`), with the measured median stored.
- **Minor 5:** a bar with `traded = unknown` is left out of both the numerator and the denominator of the activity
  ratio; with no known session in the window the ratio is null and the result `low_activity`.
- A session of the window on which the security has no bar is likewise not in the ratio's denominator.
- `worst_survivorship` orders `complete` < `survivors_only` < `unknown`.
- `series_segments`: two admitted links with different content for one symbol, or a cycle, are refused rather than
  resolved; a link older than an admitted `symbol_reused` date of the same symbol is not followed.

### Tests and what they show

`tests/test_mf4_unit.py`: **8 passed**. Full offline suite: **2219 passed, 680 skipped**. `groups.py --check`:
partition ok, 75 test files.

| Subject | Covered by |
|---|---|
| Class table; unrecognised symbols | `test_u1` |
| Universe union; `active` not a filter; computed key | `test_u2` |
| Survivorship label of a dataset | `test_u3` |
| A link takes effect only from its admission (`KNOWN` by `recorded_at`, `AVAILABLE` by announcement), nothing is linked automatically; a reuse splits; conflicts and cycles refused | `test_u4` |
| Each criterion at its boundary, with measured values | `test_e1` |
| Precedence, the full `failed` list, Minor 3 | `test_e2` |
| Minor 5 | `test_e3` |
| The policy's values and `provisional` status | `test_e4` |

### Not done, not verified

- Imported universes (the rows of an admitted import for a date) and per-batch `survivorship` are MF-7's loader.
- The store's refusal to mark a configuration `validated` without a report id is MF-7.
- Joining bar series across a link when computing features is MF-5's use of `series_segments`.


---

## MF-5: arithmetic, the carried series, feature set v1, market context

**Status: ACCEPTED on the branch (C-0048: audit 4 reported no Blocker; its Majors are fixed as recorded in "Code audit 4").**

### What was built

| File | Content | Design |
|---|---|---|
| `worker/market_features/numeric.py` | The one Decimal context (precision 28, half-even), `ln`, `exp`, `sqrt`, quantization to 12 places, and the shared return primitive | §10.1, P3-D7 |
| `worker/market_features/series.py` | c_u and r_u for one security on the snapshot's D\* basis: zero on a no-trade session, the whole move on the next trade, `spans_unknown`, `spans_gap`, `untrusted` | §10.2 |
| `worker/market_features/features.py` | The 45 per-security features and the state rules of §10.4 (`Notes`, precedence, null states) | §10.3, §10.4 |
| `worker/market_features/market.py` | Eligibility by session, m_u with membership fixed on the session before, and the 9 market-context features | §10.2, §10.3 |
| `tests/test_mf5_unit.py` | 16 tests; expected values computed in the test independently of the code | §10, §12 |

Choices made inside the design's words, to be checked by the audit:

- **Windows.** A return window of n covers the stretch from position t−n to t. A session window of n ("the n
  consecutive final sessions ending at t") covers positions t−n+1 to t, and a gap before its first session is not
  counted. The tolerance is that of n in both cases.
- **`stale` and the staleness limit** apply to each end of a window whose formula uses a price. `gap_1` does not mark
  the previous price `stale` (it is a carried reference by definition), but the limit and an unknown price apply.
- **`ret_1` across a gap is `missing`** (tolerance 0 for windows of 5 or fewer), as is `gap_1`.
- **A known price directly after an `untrusted` or not-yet-priced stretch has no return** from before it; only an
  unknown-price stretch is bridged (`spans_unknown`).
- **Volume:** a traded bar with a null volume is left out and the value is `unlabelled`; a volume before a rights
  ex-date is left out and the value is `partial`, reason `share_count_changed`; below 15 inputs it is `missing`.
- **`vol_parkinson_21`** ignores unknown-price and adjustment states (it is a ratio of one session's own prints) and
  uses sessions with high ≥ low > 0.
- **`zero_trade_ratio_63`**: bars with `traded = unknown` and sessions without a bar are in neither part of the ratio.
- **Return history:** an h-return is left out when it spans a gap or one of its ends is not a known price (Minor 9:
  the ends only); an `untrusted` end makes the family `untrusted`. `hist_n_h` is always present.
- **`xs_rank_ret_21`** = (average rank − ½) / N with rank 1 the lowest; it is `missing` when the security is not a
  member of m_t with a value.
- **`beta_126`** leaves out a `spans_unknown` return (Minor 6) and is `missing` (`zero_base`) when the market
  returns have no variance.
- **`mkt_ret_ew_n`** needs every m_u of the window; a gap inside it is `partial` or, beyond the tolerance, `missing`.
- Session 0 of a sequence has no m_u (no earlier session fixes its membership).
- **Not optimised:** eligibility by session calls the eligibility function once per security and session.

### Tests and what they show

`tests/test_mf5_unit.py`: **16 passed**. Full offline suite: **2246 passed, 680 skipped**. `groups.py --check`:
partition ok, 76 test files.

| Subject | Covered by |
|---|---|
| Arithmetic context; no float anywhere in the four modules | `test_n1` |
| The carried series; bridging; gaps | `test_s1`, `test_s2` |
| ML-19 (feature part): a split with no trade on the ex-date gives a flat series; an unresolved factor is `untrusted` | `test_s3` |
| Every state of §10.4 and the precedence, on `ret_n` | `test_f1` |
| Momentum, volatility (sample and Parkinson), drawdown | `test_f2` to `test_f4` |
| Volume, adjustment for share-count actions, the rights rule, `zero_base`, null volume | `test_f5` |
| Liquidity, `stale_sessions` | `test_f6` |
| Return history (Minor 9; n always present) | `test_f7` |
| Size, range, the opening gap on t's basis | `test_f8` |
| A feature at t uses nothing after t, and the converse | `test_f9` |
| Membership fixed on the session before; `min_constituents`; a non-equity line is never a member | `test_k1` |
| Market return, breadth, relative return, beta (0.5, 1, 1.5 by construction), rank | `test_k2` |
| The feature path imports no target module | `test_g1` |

### Not done, not verified

- The registry as stored data (formula text, units, `min_observations` per feature) and the configuration are MF-7.
- Features across an identity link (`universe.series_segments`) are not exercised: the caller joins the bars.
- ML-2's aggregate part is covered only by construction (the series is built up to D\*); a planted-future test
  through the store is MF-7.
- No real data; no performance measurement.

---

## Code audit 1 (MF-1, MF-2, MF-3): fresh-context agent, C-0005

Audited at `9d9a01a` (MF-1) and `5e36376` (MF-2, MF-3). Verdicts: **MF-1 FAIL, MF-2 FAIL, MF-3 FAIL.** No Blocker and
no leak in the code; 153 mutants run in a scratch copy (103 killed). The auditor could not reproduce the 2195 figure
on an archive of `9d9a01a` (3 failures in that run, two in `tests/test_report_classification.py`, cause not
investigated); the working tree gave 2219 passed. That discrepancy is open and is checked by CI on this commit.

| # | Step | Severity | Finding | Disposition |
|---|---|---|---|---|
| 1 | MF-1 | Major | Rows 3 and 4 always `stale`; `missing` before the first trade and Minor 4 were not produced | **Fixed.** `Reference.bar_quality` and `bar_quality_reason`, decided with the history in `reference_series`; `test_x5` |
| 2 | MF-1 | Major (tests) | Guards whose removal no test detected (admission in `classify`, E1, `CURRENT` after H, rows of no capture run, a session without a bar inside an unknown stretch, turnover-only disagreement, null volumes, the purity test) | **Fixed.** `test_m1`, `test_x1`, `test_x3`, `test_x4`, `test_x5`; `test_g2` now checks the seven table names as whole words, clock attributes by AST, and dynamic import |
| 3 | MF-1 | Minor | A negative or non-finite close became a row-1 price | **Fixed.** Row 2 with flag `close_invalid`; `test_x4` |
| 4 | MF-1 | Minor | `CURRENT` refused a cutoff, against §4.2 | **Fixed.** T is optional in `CURRENT`; when given, H ≥ T; `test_m1` |
| 5 | MF-1 | Minor | The governing-event tie depended on row order | **Fixed.** Key is (`recorded_at`, run id, pass number); `test_x2` |
| 6 | MF-1 | Minor | Canonical JSON: one instant had two texts; naive instants; sets of Decimals; colliding keys; the key's value not pinned | **Fixed.** `test_x6` |
| 7 | MF-1 | Minor | Run ids compared without normalising | **Fixed.** Compared as text; `test_x3` |
| 8 | MF-1 | Minor | The docstring misstated the input; without `raw_payload` MFB-8 silently did nothing | **Fixed.** An observation without the key is refused; `test_x3` |
| 9 | MF-1 | Minor | Minor 10 and the `p2_fakes` check deferred | **Open, disclosed.** Both belong with the import path and the loader (MF-7) |
| 10 | MF-1 | Minor (tests) | Other surviving mutants | **Partly fixed.** Row 5 with only an intraday row, a zero printed close on row 3, `last_traded_price`, MFB-8's own key, the market-cap preference, a non-binary float: `test_x4`; a session without a bar counted as stale: `test_x5`. **Not covered:** the provenance order inside `_close_observation`, and that the raw bar is not mutated |
| 11 | MF-2 | Major (tests) | Admission of P3's no-session evidence untested | **Fixed.** `test_c7` |
| 12 | MF-2 | Minor | Duplicate final dates were kept | **Fixed.** `test_c8` |
| 13 | MF-2 | Minor (tests) | The far side of a window; contradictions on gap days | **Fixed.** `test_c8` |
| 14 | MF-3 | Major | A `conflicting` cum bar gave an applied rights factor; the record wrongly said MF-1 makes such a bar `price_unknown` | **Fixed.** `Reference.source_quality`; the factor is `unresolved`, reason `cum_price_conflicting`; `test_a3`, and `test_x5` goes through `bars` and `reference` |
| 15 | MF-3 | Major | Earlier factors with an ex-date strictly between the cum session and the rights ex-date were dropped (0.88 instead of 0.96) | **Fixed.** One product over the earlier factors after the price's source session up to the rights ex-date; `test_a11` |
| 16 | MF-3 | Minor | A misdirected statement still applied through `build_factors` and `detect_moves` | **Fixed.** Both take the statements of every security and filter after `applying`; `test_a12` |
| 17 | MF-3 | Minor | Validation could be bypassed; naive instants; duplicate ids; withdrawal of a withdrawal unstated | **Fixed.** The dataclass validates; duplicate ids are refused; a withdrawal of a withdrawal does not revive the action (stated and tested); `test_a12` |
| 18 | MF-3 | Minor (tests) | Surviving mutants | **Fixed** for other securities' statements, rises, the threshold boundary, the hash's fields, `ex_date_since_last_trade`, an ex-date on the cum session: `test_a11` to `test_a13` |

The auditor's judgement of the recorded choices: all sound except the two that findings 14 and 16 correct. Its note
on level windows (a session window must not count the gap before its first session) is met in MF-5 by construction.

---

## MF-6: targets (accepted, C-0060; drafted at the checkpoint of 2026-11-13)

**Status: ACCEPTED on the branch (C-0060: audit 9 reported no Blocker; an independent oracle agreed on every state and value over 313 000 cases).** The draft of the checkpoint (C-0034) was unchanged until audit 3;
`tests/test_mf6_unit.py` now covers it (10 tests).

### Tests added 2026-11-14

| Design test | Covered by | Part covered |
|---|---|---|
| Every state and the precedence | `test_l1` to `test_l8` | resolved, pending, resolved_carried, terminated (identity event; leaving the universe, with and without the grace period; coming back), unexplained_move_in_horizon, unresolvable_quality, unresolvable_missing_price, unknown_session_in_horizon; quality before an unexplained move; an unknown session before both |
| ML-19 (label part) | `test_l4` | A split inside the horizon with no trade on the ex-date is divided out (51 / ½ / 100 − 1 = 0.02); with no trade since, the cum price is compared with itself |
| ML-21 | `test_l5` | Before the confirmation: provisional return, `unexplained_move_in_horizon`; after it: `resolved`, the same return, a later `resolved_at`; `disagreements` reports the two builds |
| Minor 1, 13, 16 | `test_l9`, `test_l6`, `test_l3` | The volatility's own state keeps a computable return; a missing reference price resolves at once; `resolved_carried` needs a price at F |
| `abs_ret_1`, `realised_vol_5` | `test_l1`, `test_l9` | Worked by hand |
| `reference_revised` | `test_l9` | The label uses the stored row's close, and flags the difference |
| Import graph | `test_l10` | The feature path never imports `targets` |

Mutation check (scratch copy, 12 mutants): 11 killed. The survivor removes the volatility's `spans_unknown` and
`spans_gap` test; it is equivalent, because an unknown price inside the horizon already leaves a null return there and
a gap is caught first as `unknown_session_in_horizon`.

`worker/market_features/targets.py` was written just before the checkpoint (C-0034) and is committed so that the
work is not lost. (At the checkpoint it had no test file; tests from C-0041 on, below.) What it contains, against design §11:

- `ReferenceRow` (what a label takes from the stored reference snapshot row), `Label`, `TargetPolicy`
  (grace period 21, provisional), `label()` for one (row, horizon) at a target build, `_volatility`, and
  `disagreements()` for `verify`.
- The state precedence of §11; `abs_ret_1` for h = 1; the volatility's own state (Minor 1: a computable return is
  not discarded with its volatility); Minor 13 (a label whose reference price is undefined is resolved at once to
  `unresolvable_missing_price`); Minor 16 (`resolved_carried` needs a defined `px_ref` at F); `reference_revised`;
  `resolved_at` as the latest admission instant over the sessions, statements and extra evidence passed in.

Known gaps to settle when the step is resumed:

- **No tests.** MF-6's exit (design §15) is: unit tests of each state and of the precedence; ML-11, ML-18, ML-19
  (label part), ML-21; the label parts of ML-20 and ML-24. None exist.
- `resolved_at` is only as complete as the instants the caller passes; the loader (MF-7) must supply every input's
  admission instant, including calendar and identity evidence.
- The `terminated` rule from leaving the universe uses "a session without a bar" as leaving; whether a session on
  which the security is in the universe but has no bar can occur must be checked against MF-1's bar rules.
- The confirmations counted into the adjustment set are those whose move overlaps (s_D, s_F]; to be checked against
  the design's wording.
- The import-graph test (the feature path must not import this module) exists in `tests/test_mf5_unit.py`
  (`test_g1`) from the feature side only.


---

## Code audit 2 (MF-4, MF-5): fresh-context agent, C-0005 and C-0037

Audited at `241f78c`. Verdicts: **MF-4 ACCEPT (no Blocker); MF-5 REJECT (one Blocker).** No leakage found;
arithmetic matched an independent 50-digit implementation on random series and a random market; 56 mutants run (37
killed). Fixes on 2026-11-14; full suite 2268 passed, 680 skipped.

| # | Step | Severity | Finding | Disposition |
|---|---|---|---|---|
| B1 | MF-5 | **Blocker** | A `conflicting` input at the first session of a window (or carried into it) was not checked; the value came out `fresh` in ret_n, dd_252, Parkinson, return history, rel_volume and the session means | **Fixed.** `_stretch` checks [start, end]; `Point.source_conflicting` carries a close printed by a `conflicting` bar into every value that uses it; return history checks both ends of each h-return; rel_volume checks the step to t. `test_a1`; mutants for each part killed |
| M1 | MF-5 | Major | `shares_implied` divided by the close; §10.3 says the last traded price | **Fixed to the design:** CSE's last traded price from the reconciled bar; null with reason `no_last_traded_price` when absent. `test_f8` |
| M2 | MF-5 | Major (tests) | 13 surviving non-equivalent mutants | **Fixed:** `test_a1` to `test_a4` (vol window edge, momentum end, session minimum, history untrusted, gap_1 on t's basis and its gap guard, traded = unknown in zero_trade_ratio, precedence of conflicting over missing, breadth, the market gap guard, beta without bridged returns) |
| M3 | MF-4 | Major | The `untrusted` half of the quality criterion was never set by any caller | **Fixed.** `eligibility` takes the series points; `market.eligibility_by_session` passes them. `test_x1` through the real series |
| M4 | MF-4 | Major (tests) | Three untested guards | **Fixed.** `test_x2` (a link older than a reuse; the link filter; the median of an even count) |
| m1 | MF-5 | Minor | rel_volume ignored a gap between t−1 and t | **Fixed** with B1 |
| m2 | MF-5 | Minor | `breadth` raised on an empty market | **Fixed.** `test_a4` |
| m3 | MF-5 | Minor | xs_rank turned a `stale` own value into `partial`, and ignored m_t's spans flags | **Fixed** in code; the state carry-over is not separately tested (carried) |
| m4 | MF-5 | Minor | amihud and Parkinson never report `partial` for fewer inputs | **Carried:** by definition their inputs are traded sessions; to be stated in the design at its next revision |
| m5 | MF-5 | Minor | dd_days on a carried series takes the last carried session of a tied peak | **Carried:** literal to "latest, if tied"; design clarification at the next revision |
| m6 | MF-5 | Minor | A member's `conflicting` bar on u still enters m_u | **Carried:** the design is silent; design decision at the next revision |
| m7 | MF-4 | Minor | History counts bars with `traded = unknown` | **Carried:** design clarification |
| m8 | MF-4 | Minor | A rename back to an earlier symbol is refused as a cycle | **Carried:** fails loudly; safe |
| m9 | MF-4 | Minor | Symbol forms outside the regex are `not_applicable` without notice | **Carried:** to be checked against real CSE symbols with the suffix table (laptop) |

The auditor judged every recorded MF-5 choice sound. Of the MF-4 choices, the provisional suffix table "contradicts
the letter of §8.1" and is acceptable only with its stated gate; "liquidity failure gives `low_activity`" needs a
design change (a result name of its own) at the next revision. CSE's carried close (TradeIQ F11) is handled as the
design says: a traded session with r = 0.


---

## Code audit 3 (MF-5 re-audit, MF-6 first audit): fresh-context agent, C-0005 and C-0037

Audited at `7f4262e`. Verdicts: **MF-5 REJECT** (one Blocker), **MF-6 REJECT** (two Blockers). No leak in the label's
return. Fixes on 2026-11-14; 19 mutants on the fixes, 18 killed (the survivor, s_F in `resolved_at`, is defensive:
whenever s_F is before D it equals the build's own source session of P_D, which is checked).

| # | Step | Severity | Finding | Disposition |
|---|---|---|---|---|
| B1′ | MF-5 | **Blocker** | A window's first return bridged over an unknown price from a base before the window: a conflicting base fed vol_21 and amihud unmarked | **Fixed.** `_stretch` checks the base of a bridged return. `test_a5` |
| MJ-1 | MF-5 | Major | The B1 fix marked volume means, Parkinson and zero_trade_ratio `conflicting` because of a carried price they do not use | **Fixed.** `_stretch(prices=False)` for features that use no price. `test_a5` |
| MJ-2 | MF-5 | Major | rel_volume applied the gap tolerance twice | **Fixed.** Gaps counted once over t−21..t. `test_a6` |
| MJ-3 | MF-5 | Major (tests) | Survivors on the B1 fix and on M3 | **Fixed.** `test_a5` to `test_a7` (carried and bridged conflicts, history, gap_1 at t, market eligibility through the series) |
| B-T1 | MF-6 | **Blocker** | Only the first absence in the horizon was examined: leave, return, leave for good gave a return | **Fixed.** Every absence starting in (D, F] is examined. `test_l11` |
| B-T2 | MF-6 | **Blocker** | `resolved_at` ignored a price carried from before D (look-ahead through the training cutoff) | **Fixed.** The instant range starts at the earliest of D, the build's own source session of P_D, and s_F. `test_l12`, `test_l15` |
| MJ-T1 | MF-6 | Major | An unresolved factor after F made the label `unresolvable_quality` | **Fixed.** Only unresolved factors with ex-date in (s_D, F]. `test_l13` |
| MJ-T2 | MF-6 | Major (design) | P_D printed by a `conflicting` bar was not checked | **Decided conservatively (C-0047):** it is an input; `ReferenceRow.source_quality`; such a label is `unresolvable_quality`. `test_l13` |
| MJ-T3 | MF-6 | Major (tests) | 12 surviving mutants | **Fixed.** `test_l14`, `test_l15` |
| — | MF-5 | Minor | xs_rank's reason for a conflicting own value; its state mapping and spans flags untested; a float in `bar.raw` read as no last traded price; dead code; return history conservative on a skipped pair | **Carried** (the dead first-bar check is removed) |
| — | MF-6 | Minor | A delisting dated on D is ignored; `label()` raises when D is no longer final (MF-7 must map it); the volatility's spans check is narrowly non-equivalent | **Carried** to MF-7 and the next design revision |
| m6 | MF-5 | (carried from audit 2) | A member's conflicting bar enters m_u | **Still carried**: a design decision for the next revision |

The three failures both auditors saw on a `git archive` export are explained: the export was unpacked under `/tmp`,
which also holds the tests' temporary roots, and `tests/test_report_classification.py` refuses on purpose a temporary
root that contains the repository. Not a defect; CI's clean checkouts pass.


---

## Code audit 4 (MF-5, MF-6 re-audit): fresh-context agent

Audited at `53d15b2`. Verdicts: **MF-5 ACCEPT** (no Blocker), **MF-6 REJECT** (two Blockers). Every Blocker and Major
of audits 2 and 3 confirmed fixed. No look-ahead in features, membership or the return. 43 mutants, 30 killed.

| # | Step | Severity | Finding | Disposition |
|---|---|---|---|---|
| B-T3 | MF-6 | **Blocker** | An unresolved factor in (s_F, F] decided `unresolvable_quality` without its statement in `resolved_at` | **Fixed.** Its statement ids enter `resolved_at`. `test_l16` |
| B-T4 | MF-6 | **Blocker** | The build's own D bar `conflicting` (the volatility's base) was not checked | **Fixed.** `unresolvable_quality`. `test_l16` |
| — | MF-6 | Major | An absence already running at D was never examined | **Fixed.** `test_l17` |
| — | MF-6 | Major | An identity event returned `terminated` while an earlier absence's grace ran | **Fixed** to §11's order: pending first. `test_l17` |
| — | MF-6 | Major | Withdrawn or superseded statements leave no id for `resolved_at` | **Fixed in the contract:** `extra_instants` must carry them; MF-7's loader must supply them |
| — | MF-5 | Major | A bridged return's gaps before the window start were not counted | **Fixed.** `test_a8` |
| — | MF-5 | Major (tests) | Four non-equivalent survivors (bridged base carried conflict, Parkinson, market `spans_unknown`, the rank's state) | **Fixed.** `test_a8` |
| — | MF-5 | Minor | `stale_sessions` never `conflicting`; volatility state name after an unresolved factor beyond F; an untrusted window start; history over-marking; rel_volume step with prices | **Carried** |

### The CSE request found during audit 4 (C-0048)

The auditor saw the agent proxy refuse connections to `www.cse.lk`. Traced: `tests/test_capture_multiple_companies.py`
(`test_tradeSummary_fetched_exactly_once_per_batch`, a test from before this session) saved the **real**
`cse_client.get_trade_summary_all` before installing its fake, then called it: one real `POST
https://www.cse.lk/api/tradeSummary` per run. **No request reached CSE**: the cloud container's network policy refused
every connection, and CI runs Linux tests with `--network none` and Windows tests with CSE's host names blocked and
checked unreachable. Fixed: the counter wraps the fake. From now on this session runs the suite with an in-process
guard plugin that refuses and logs any request to a CSE host; the full suite logs **zero** attempts.


---

## Code audit 5 (MF-6 re-audit): fresh-context agent

Audited at `2c645c3`. Verdict: **REJECT**, three Blockers, all in `resolved_at` (the return has no leak). Every earlier
finding confirmed fixed. Because three audits in a row found `resolved_at` gaps, the rule was rewritten to be
**conservative by construction** rather than patched case by case: `resolved_at` is the latest admission instant over
every session from the build's last printed close at or before D through the furthest session the label read
(including a bar after F that ended an absence's grace period), over every statement behind the security's factors
and confirmed moves whatever its ex-date, and over `extra_instants`. A later `resolved_at` only delays a label's use.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-T5 | **Blocker** | A return after F, inside an absence's grace period, decided the state but was after `resolved_at` | **Fixed** by the conservative rule. `test_l18` |
| B-T6 | **Blocker** | An unknown-price bar before D (the build's own P_D undefined) decided the state | **Fixed** by the conservative rule. `test_l18` |
| B-T7 | **Blocker** | The volatility on the D\* basis was nulled by an unresolved factor after F, whose statement was not covered | **Fixed at the root:** the volatility is computed on F's price basis (a factor after F changes nothing), and every factor statement is covered. `test_l18` |
| M0 | Major | Termination by absence did not cover F's finality | **Fixed.** `test_l18` |
| M1 to M5 | Major (tests) | `extra_instants`, event ids, an event on F, the earliest of two events, suspension, a conflicting price carried into D, a conflicting bar on F | **Fixed.** `test_l19`; 10 mutants, all killed |
| — | Minor | `disagreements` ignores labels in one build only and `left_session`; `abs_ret_1` uses the rounded return; a delisting on D | **Carried** |

**Second CSE finding.** A bare `pytest` at the repository root also collected `test_cse_endpoints.py`, a Phase 0
discovery script that sends real requests to CSE (68 attempts in the auditor's run, all refused in-process by the
guard). CI passes explicit file lists and never collected it. A root `pytest.ini` now sets `testpaths = tests`; a bare
`pytest` from the root collects only `tests/` and logs zero CSE attempts.


---

## Code audit 6 (MF-6 re-audit): fresh-context agent

Audited at `6d7479b`. Verdict: **REJECT**, one Blocker. The conservative `resolved_at` and the volatility on F's basis
were verified (hand-computed with a split inside the horizon and a bonus after F).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-T8 | **Blocker** | For a revised reference row, bars between the stored row's s_D and the build's last close at or before D could decide an unexplained move outside `resolved_at` | **Fixed.** The range also starts at the stored row's s_D and at the start of every move that ends after it. `test_l20`, and `test_l22` for each part alone (each part's removal is killed) |
| M-a | Major | The `extra_instants` contract named only withdrawals with an ex-date in (s_D, F] | **Fixed.** Every withdrawn or superseded statement of the security, confirmations included, whatever its date |
| M-b | Major (tests) | The unexplained-move window at the row's s_D; the volatility's `unresolvable_quality` | **Fixed.** `test_l21` |
| — | Minor | No-price factors out of the adjustment set (now tested); the confirmation filter; carried items of audit 5 | Partly fixed; the rest carried |

---

## MF-7a: migration 0019 (pending)

**Status: ACCEPTED on the branch (C-0057: audit reported no Blocker; its three Majors fixed).**

| File | Content |
|---|---|
| `supabase/migrations_pending/0019_market_feature_foundation.sql` | The 15 tables of design §13.1 (plus `mf_history_security_complete` and `mf_history_quarantine` for §5.4's revision-6 rule), guards, append-only triggers on every table for every role, grants |
| `tests/test_mf7_migration_postgres.py` | 5 tests on a throwaway cluster: applies on top of 0001 to 0018; both append-only triggers on every table, and UPDATE, DELETE and TRUNCATE refused for the owner on tables holding rows; owner-only tables refuse the worker; the configuration's hash and the `validated` rule; the worker's calendar copies only; reader read-only |
| `tests/test_mf7_migration_static.py` | The pending file is outside the chain and meets every migration's guards |

**Why "pending".** Design §13.3 names three frozen guards that state the migration range as ending at 0018. Putting
0019 into `supabase/migrations/` on this branch would need a change-control edit to each, and a re-staging. Kept in
`supabase/migrations_pending/`, it changes no frozen file; moving it into the chain is part of the owner's merge
decision (P3-R6). The tests apply it explicitly after the chain.
*(2026-11-23, C-0118: 0019 is now in the chain, `supabase/migrations/`, unchanged, with the three guards changed under change control; the tests apply the chain.)*

Choices for the audit: `company_id` is `uuid` (as `companies.id`); a configuration's `status` must equal its JSON's
`eligibility_policy_status`; the worker may insert a `declared_closed` row only as a `trading_calendar:` copy (the
calendar-sync rule); move flags may be `not_evaluable` (MF-3's state).

**Verified here:** on PostgreSQL 16 as an unprivileged user (the container has no PostgreSQL 17): 5 passed. The
authority is CI's PostgreSQL 17 job.


---

## Code audit 7 (MF-6 re-audit): fresh-context agent

Audited at `259738f`. Verdict: **REJECT**, one Blocker. The auditor wrote a fuzzer that perturbs one bar at a time
(moves and a rights factor recomputed from the bars) and checks that every session whose bar changes a label's state
or value is covered by `resolved_at`: 0 leaks for plain rows, 1 to 4 per 4 000 for revised rows.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-T9 | **Blocker** | For a revised row whose stored s_D has no close in the build, the build's previous close before it decided a move or a rights cum price outside `resolved_at` | **Fixed generally:** the lower bound walks back to the build's last printed close at or before the earliest covered session, repeated until nothing earlier is added. The earlier walk from D alone is subsumed by it and removed. `test_l24`; the auditor's fuzzer at full size: **0 leaks in 6 runs** (revised and plain, seeds 1 to 3, about 12 000 labels) |
| — | — | The fuzzer as a permanent test | **Added:** `tests/test_mf6_fuzz.py` (600 trials per seed and mode, about 9 s); a regression net, with the deterministic `test_l24` for the specific case |
| M-c | Major (tests) | The walk from D untested | **Moot:** removed as subsumed |
| M-d | Major (tests) | `unexplained_move_in_horizon` before `resolved_carried` untested | **Fixed.** `test_l23` |
| — | Minor | An event dated exactly D; confirmations in the stored adjustment set; `session_instants` should name membership facts | **Carried** to MF-7, where the caller fills these |


---

## Audit of MF-7a (migration 0019): fresh-context agent

Audited at `bde044a`. Verdict: **ACCEPT** (no Blocker). Every table append-only for every login with rows present
(including ON CONFLICT DO UPDATE); grants exactly as designed; owner-only evidence not insertable by other logins under
the designed grants; applies on the chain; nothing reads the pending folder but its tests. 25 SQL mutants, 11 killed.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| Ma-1 | Major | `mf_snapshot_rows` could not hold the quality of the bar that printed `px_ref`, which MF-6 reads | **Fixed.** `ref_source_quality` column with its check |
| Ma-2 | Major | Guards named NOLOGIN `cse_reader`, not the reader login `cse_verifier` | **Fixed** by an allow-list: owner evidence only as `cse_owner`; calendar copies only as `cse_worker` with the `trading_calendar:` prefix, which the owner may not use; prefix tested with `left()`, not `like` (mi-3) |
| Ma-3 | Major (tests) | Guards, grants and several constraints untested | **Fixed.** `test_x6` (exact grants by aclexplode), `test_x7` (every login but the owner refused under a mistaken grant; each owner-only table carries the guard), `test_x8` (a bad row per constraint), `test_s2` (no raw-response column). The guard and PUBLIC-grant mutants are now killed |
| mi-1 to mi-9 | Minor | A free-text validation report id; non-canonical JSON; spoofable defaults; looser constraints (factor kind, signs, JSON validity, empty symbols); no FK from labels to snapshot rows; the move-flag mapping; history import gaps; DISABLE TRIGGER by the owner (shared since 0007) | **Carried** to MF-7b and MF-7c, where the writer and the import define them |


---

## Code audit 8 (MF-6 re-audit, fuzzing every input kind): fresh-context agent

Audited at `bde044a`. Verdict: **REJECT**, one Blocker, a wrong value; **no look-ahead**. The auditor extended the
fuzzer to calendar dates, absences, identity events, corporate-action statements, confirmations and bars, for plain
and revised rows, checking both worlds: 0 leaks in every kind (and in a larger run up to 34 sessions and h = 21).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-T10 | **Blocker** | With no close in the build from the stored s_D through F (s_F before s_D), factors between s_F and s_D were never applied: a 1:2 split gave +100% instead of 0 | **Fixed conservatively:** such a label has no value, `unresolvable_quality`, evidence `reference_not_in_build` (an attempt to compare on s_D's basis made the label read moves after F, which the property test caught). `test_l25` |
| — | Major (tests) | A factor with ex-date in (s_D, D] untested | **Fixed.** `test_l26` |
| — | Major (contract) | Which calendar evidence goes into `extra_instants` | **Fixed:** the latest instant of all the build's calendar and identity evidence (the broad reading, 0 leaks in the fuzzer); docstring and §11 |
| — | Minor | §11's precedence text did not show Minor 13 | **Fixed** in §11 |
| — | Minor | An event dated exactly D; confirmations in the stored adjustment set; defensive move extensions | **Carried** |

After the fix: audit 7's fuzzer, 6 runs, 0 leaks; audit 8's extended fuzzer (all six input kinds, plain and revised,
seeds 1 to 5, 30 000 labels), 0 leaks.

---

## MF-7b: the TradeIQ import

**Status: accepted (C-0063) after a rejection (two Blockers) and fixes (C-0062). The record below is the first
version; the fixes follow the MF-7c section.**

| File | Content |
|---|---|
| `worker/market_feature_store/tradeiq_import.py` | `plan()` (from the research outputs: batches with their declarations, bars, the 1990s code mapping, quarantine, `security_complete`, calendar completeness against the index workbook, splits and reconstructed actions) and `write()` (one transaction through the owner path) |
| `tests/test_mf7_tradeiq_import.py` | The plan on a synthetic work directory; the write into a throwaway cluster, and the worker refused |
| `scripts/research/tradeiq/run_validation.py`, `run_ca_reconstruct.py` | Now also write the rejected rows (for the quarantine) and each reconstructed event's ratio and subscription price |

**Full run, local throwaway PostgreSQL 16 (2026-11-14; deleted afterwards):** 33 batches (8 with survivorship
`unknown`, 1991 to 2000; 25 `complete`), 31 of them calendar-complete against the index; **1 404 336 bars** written in
31 s; 1 902 quarantined pairs (765 days a workbook printed twice with different prices, 1 137 shifted 2023 rows);
9 002 `security_complete` ranges; 238 corporate-action statements (138 splits, 50 bonus, 50 rights reconstructed);
14 913 bars of 1990s codes left out because the code does not map to one ordinary voting line.

Choices for the audit: the batch is the source workbook; `security_complete` per security runs from its first to its
last row in the workbook (quarantined pairs override it); a split's `available_at` is the end of its announcement day,
else of its effective day; a split with a non-whole ratio is left out (one case); the 1990s mapping requires the code to
appear in 2001-2002 with ordinary voting shares only.


---

## Code audit 9 (MF-6 values, independent oracle): fresh-context agent

Audited at `6dee28e`. Verdict: **ACCEPT** (no Blocker). The auditor wrote an independent implementation of §11 from
the design alone and compared it with `label()` on 313 000 random cases (plain and revised rows, every factor kind,
absences, gaps, conflicting bars, moves, h up to 21): **no wrong state, return, direction, volatility, stale_sessions
or P_F**. The disagreements were three design readings (now stated in §11) and rounding of `abs_ret_1` at 1e-11.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Major (tests) | An identity event dated exactly D | **Decided and tested:** it terminates the label (the security has no future from D). `test_l27` |
| 2 | Major (tests) | Confirmations in the stored adjustment set | **Decided and tested:** they belong there. `test_l27` |
| 3, 4 | Minor (design) | `left_session` when an event and a run-out absence both apply; chronological absences against "pending first" | **Stated** in §11 (revision 6, C-0060) |
| 5 | Minor | `abs_ret_1` from the rounded return | **Fixed:** from the exact return |
| 6 | Minor | A conflicting close between the row's s_D and D reaches the label only through detector moves | **Carried** |

MF-6 took nine audits; the record above (audits 3 to 9) lists every Blocker and its fix.

---

## MF-7c (first part): imported bars and the snapshot builder

**Status: accepted (C-0063). The database loader and writer, `verify` and the CLI are
not yet written.**

| File | Content |
|---|---|
| `worker/market_feature_store/history_bars.py` | Imported rows as feature bars by §5.4's revision-6 rules (a row, no price printed, a printed zero, quarantined, `security_complete`, not a member); imported final sessions |
| `worker/market_feature_store/snapshot.py` | One snapshot at (T, mode): calendar, bars, references, factors, the detector, series, eligibility, the 54 features, the market; the rows, factors, move flags and `adjustment_set_hash` the writer stores |
| `worker/market_features/market.py` | `eligibility_by_session(start=...)`: membership computed only for the sessions a snapshot's market windows read (backward compatible) |
| `tests/test_mf7_history_bars_unit.py`, `tests/test_mf7_snapshot_unit.py` | Every §5.4 case; a synthetic snapshot with a split adjusted (and, without it, the fall flagged as unexplained) |

**Real run on the TradeIQ history** (research, in the cloud container; 2001 to 2025, `AVAILABLE` mode, T = the end of
2025-12-31, the 138 splits and 100 reconstructed actions, the import's per-year calendar): 692 securities over 5 958
sessions, built in about 85 s. Eligibility at D\*: 264 eligible, 300 not applicable (not ordinary shares), 91 low
activity, 24 low liquidity, 8 insufficient history, 4 price unknown, 1 stale. `ret_21` fresh for 269 securities
(missing mostly for securities that stopped trading long ago), `vol_63` fresh for 665, `beta_126` fresh for 652.
Factors: 233 applied, 2 unresolved. The market: `mkt_ret_ew_21` = −7.0%, `mkt_ret_ew_63` = −2.7%, breadth 56%.

**A finding for the owner and Phase 5:** with the provisional 25% detector threshold, **5 593 day-on-day moves have no
entered action** across the 25 years, mostly in thinly traded, low-priced lines. Labels spanning them are
`unexplained_move_in_horizon` and stay out of training (`scoring.1`). The threshold is a configuration value to be
calibrated on data before any declared test period (§17, Phase 5 §5.4).

**Annotation (audit Major 8):** this run predates the MF-7b fixes. It used one `security_complete` range per security
across 2001-2025, not one per workbook, and calendar observations built from row dates, not from the import's index
match. Its numbers are indicative only; they are to be re-run through `plan()`'s per-batch declarations.

An error of mine in the first real run: the import calendar was not passed, so every public holiday was an unknown
date and every window looked broken (all multi-session features `missing`); with the per-year calendar the results
above follow.

---

## Audit of MF-7b and MF-7c (first part): fresh-context agent

At `f5592b2`. Guard logs empty. **MF-7b: REJECT** (two Blockers). **MF-7c first part: ACCEPT** (no Blocker; Majors 6
and 7 to be done before the loader).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | Days a security traded stored as no-trade days: `security_complete` declared for the 1991-2000 batches although their exports lost rows (F3); rejected L1 rows (e.g. HAYL 1995-02-10, F14) not quarantined; 1990 rows admitted | **Fixed.** No `security_complete` through 2000; rejected L1 rows carry their code and date (`run_validation`) and are quarantined; rows before 1991 are not admitted. `test_t2`, `test_t3`, `test_t5` |
| B-2 | Blocker | A second series under the N symbol stored on the days only it printed (F10: OSEA 2004, MLL 2003, PCH 2015) | **Fixed.** A security a workbook prints with two closes on any day is quarantined for that whole workbook (15 securities, 2 770 rows). `test_t4` |
| 1 | Major | Calendar completeness lost for 1992, 1993 and 2001 (computed over a batch's years together) | **Fixed.** One observation per year whose dates equal the index's (stricter than before: 2010 has a date the index lacks and is not declared). `test_t6` |
| 2 | Major | 1990s codes renamed to `CODE.N0000` with no declaration | **Fixed.** Rows verbatim under the classless code; 219 identity links to `CODE.N0000` (effective from its first row, available from the end of 2002). `test_t1` |
| 3 | Major | F1's split exceptions written as statements | **Fixed.** A split is written only if the raw close falls by its ratio (the F1 test); 8 left out with their reason (4 other moves, one of them SEMB 2010 whose rows are now quarantined; 1 no drop; 3 no price series). `test_t7`, `test_t8` |
| 4 | Major | Untested: survivorship boundary, the X exclusion and 2001-2002 limit of the mapping, split `available_at` | **Tested.** `test_t1`, `test_t3`, `test_t7` |
| 5 | Major | `write()` not tested as one transaction | **Tested.** A failure after every batch leaves all seven tables empty. `test_t10` |
| 6 | Major | `security_complete` ranges not clipped at admission (look-ahead) | **Fixed** in `history_bars.bar_on` and stated in §5.4. `test_h1` |
| 7 | Major | Untested: C1 lookback, C6 `adj_ref`, C7 statement admission, C8 eligibility at D\* | **Tested.** `test_s2` to `test_s5`; each mutant (lookback 120 and 125, `adj_ref = px_ref`, statements admitted at H, eligibility from D\*−1) fails exactly its test |
| 8 | Major | The real-snapshot record used inputs that do not follow §14 | **Annotated** in the MF-7c record; to be re-run |
| — | Minors | `available_at` at end of day (later than §4.1, cannot leak); C10, C11 untested; snapshot rows lack §13.1 fields; rows for long-dead securities; dead `setdefault` | Dead code removed; the rest **carried** to the writer step |

**Real plan after the fixes** (offline, `scripts/research/tradeiq/run_import_plan.py`; counts only): 33 batches;
1 417 230 bars; 13 rows before 1991 not admitted; 2 770 rows of 15 two-series securities and 1 138 rejected rows
quarantined (67 rejected rows have no readable code and date); 25 batches with `security_complete` (2001 on);
calendar complete 1992-2009 and 2011-2025; 219 links, 37 codes unlinked; 230 statements (130 splits, 100
reconstructed), 8 splits left out. Rerunning `run_validation` reproduced every count in the committed summary.

The loader contract the audit set (one row per security and date across batches, agreeing rows merged, volume
conflicts, `terms_json` decoding, `imported_inputs` in `KNOWN`) is carried to the loader below and its audit.

## MF-7c (second part): the database load and write

**Status: accepted (C-0063); the audit's Majors fixed and tested (below). `verify`, the CLI and the target-build
writer remain.**

| File | Content |
|---|---|
| `worker/market_feature_store/store.py` | `load()`: imports, bars, quarantine, `security_complete`, calendar observations (with each batch's dates) and statements in one read-only REPEATABLE READ transaction, rows admitted by the query; two batches disagreeing on a close are quarantined. `write()`: the snapshot, its rows (canonical features, `row_hash`), factors and move flags in one transaction as the worker |
| `worker/market_feature_store/history_bars.py` | A complete range is read only up to the security's last admitted row (Major 6) |
| `tests/test_mf7_store_postgres.py` | Load, build and write round trip in a throwaway cluster |
| `tests/test_mf7_store_guards_postgres.py` | Each loader and writer guard (after the re-audit) |

## Re-audit of MF-7b and audit of MF-7c second part: fresh-context agent

At `ab41ed7`. Guard logs empty. The real plan reproduced byte for byte. **MF-7b: ACCEPT. MF-7c second part: ACCEPT.**
No Blocker; nothing stores a wrong state or price on the real data, nothing leaks.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| M-A | Major | 12 loader and writer guards untested (basis filter, horizon, conflict quarantine, quarantine and ranges loaded, batch dates, label, one transaction, `subscription_price`, move closes, quarantine order, read-only) | **Tested** in `test_mf7_store_guards_postgres.py`; with 4 more mutants of mine, 16 of 16 killed |
| M-B | Major | A row without a volume overrides one with a volume (last wins; only closes compared) | **Fixed.** `store.merge`: field by field, any disagreement quarantines the pair. `test_g3` |
| M-C | Major | An unadmitted batch's calendar observation, with empty dates, closes gaps | **Fixed.** Observations of unadmitted batches are skipped. `test_g1` |
| M-D | Major | `imported_inputs` not recorded; the digest changes with later evidence; `KNOWN` admits rows dated after T | **Fixed.** Column `imported_inputs` (pending 0019); the evidence lists admitted statements and calendar observation ids only; rows dated after T's day refused. `test_g2`, `test_g5`, `test_g6` |
| — | Minor | Quarantine against another batch's row | **Decided:** the quarantine wins (stated in §5.4). `test_h2`, `test_g4` |
| — | Minor | M7 (calendar from 1991), M11 (split tolerance), M18 (`b.calendar` with two ranges) survived | **Tested:** `test_t11`, `test_t12`; all three killed |
| — | Minor | "One observation per year" against merged years | **Wording fixed** (§14, C-0062) |
| — | Minors | The F12 rows (AAIC 2009) safe by evidence only; the SAMP sheet overlapping SAMP.N0000 in 2001 without links applied; the F1 filter reads prices after T (C-0062); per-row `window_digest`, `company_id`; one action available after its ex-date (later than the truth) | **Carried** |

## MF-7c (third part): the commands

**Status: implemented and tested (2026-11-15); rejected by its first audit (three Blockers, fixed, C-0065) and by
its re-audit (one Blocker, fixed, C-0066) and its second re-audit (one Blocker, fixed, C-0067) and its third (three Blockers, fixed, C-0074) and its fourth (one Blocker, fixed,
C-0078); accepted after the fifth re-audit (C-0086).** MF-7's remaining pieces (design §13.2): the
configuration, `calendar-sync`, `preflight`, `verify`, `explain`, `KNOWN_RECORDED`, the target-build writer, the
command line, the wrapper and the runbook.

| File | Content |
|---|---|
| `worker/market_feature_store/configuration.py` | The content-addressed configuration: every build parameter and every rule version, as canonical JSON; `parse` refuses a configuration whose rule versions differ from this code's |
| `worker/market_feature_store/jobs.py` | Load, build and write one snapshot under a stored configuration (the one path the CLI, `verify` and target builds use) |
| `worker/market_feature_store/calendar_sync.py` | Copies `trading_calendar`'s closures, available from the sync instant (its `established_at` is mutable, recorded but not trusted); the drift check. The only MF code naming `trading_calendar` |
| `worker/market_feature_store/preflight.py` | The worker role's privileges and every 0019 trigger, before each writing command |
| `worker/market_feature_store/selection.py` | The designation in force at an instant (ML-15); `KNOWN_RECORDED` (ML-23) |
| `worker/market_feature_store/verify.py` | Row hashes, the adjustment set, a replay of snapshots whose governing time was settled when written (F8's rule with a 15-minute margin), target row hashes, calendar drift (ML-13) |
| `worker/market_feature_store/explain.py` | One row with its factors and moves; the bar ids behind its D\* bar and windows re-derived and checked |
| `worker/market_feature_store/targets_store.py` | A target build: `targets.label` on every row of the reference snapshots, with admission instants derived mode by mode (module docstring) |
| `worker/market_feature_store/store.py` | Rows now carry their bar ids and admission instants; `dstar_observation_ids` and a per-row `window_digest` are stored and covered by `row_hash` |
| `worker/market_feature_store/cli.py`, `ops/bin/cse-features`, `docs/ops/P3_MARKET_FEATURES.md` | The command line (owner steps through cse-migrator), the wrapper and the runbook |
| `tests/test_mf7_ops_postgres.py`, `tests/test_mf7_ops_static.py` | Each command end to end, the tamper checks, the role rules; the read and network rules, the wrapper |

Choices for the audit:
- A `calendar-sync` copy is available from the sync instant, never from `established_at` (later than the truth).
- A snapshot is replayed only if its governing time lay 15 minutes or more before it was written; otherwise only its
  stored rows are proved (F8: a live result is reproduced from its stored inputs).
- `window_digest` covers the ids of every admitted row of the security at a session of the snapshot (every window
  reads within them).
- In a target build, a no-trade bar takes the instant of the security's next row (the row that closes the clipped
  range); calendar evidence enters `extra_instants` whole (audit 8's rule), an import range at the latest date the
  build admits.
- `CURRENT` results are never stored.

**Real run** (2026-11-15, throwaway PostgreSQL 16 in the container, deleted afterwards; TradeIQ through
`import-tradeiq`'s code path): import 45 s; two `AVAILABLE` snapshots (D\* 2025-06-30 and 2025-12-31; 941 and 945
rows) about 200 s each; a target build labelling the first at the second, 3 764 labels in 215 s (1 129 resolved,
27 resolved carried, 15 unexplained move, 32 missing price, 2 561 terminated); `verify` with full replay of both
snapshots: **ok, no problem**, 392 s. Eligibility at 2025-12-31: 263 eligible, 553 not applicable, 88 low activity,
24 low liquidity, 12 insufficient history, 4 price unknown, 1 stale. Peak memory 7.6 GB.

Two findings from the run, fixed: `bar_on` recomputed the last row on every call (quadratic; now once per security),
and snapshots kept every series in memory (now only for a target build; the first attempt was killed at 13.8 GB).

**Limit carried (for the owner and the audit):** a snapshot holds a row for every security that ever had an
admitted row, including the long-dead and the classless 1990s codes. They are `not_applicable` or ineligible, and
their labels are `terminated` (about 640 of 941 securities at mid-2025), but they cost time and memory: every build
walks every security over every session since 1991. A build restricted to securities with a row within a window
before D\* would be much cheaper; it changes stored rows, so it is a design change for a later step.

## Audit of MF-7c third part: fresh-context agent

At `e76c493`. Guard logs empty. **REJECT** (three Blockers, all look-aheads, each reproduced on PostgreSQL).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B1 | Blocker | A no-trade bar's instant was the next row's only; the batch whose `security_complete` range makes the bar was in no instant, so with a second batch a label resolved before its evidence existed | **Fixed.** Each range carries its batch's `recorded_at` (`Imported.complete_recorded`); a no-trade bar takes the latest of the closing row and every range containing the day (in `AVAILABLE`, at the end of that day). `test_a1` |
| B2 | Blocker | A target build accepted any reference snapshot, whose stored P_D, s_D and quality are label inputs | **Fixed.** `admissible_reference`: in `KNOWN` a `KNOWN` snapshot written by T; in `AVAILABLE` T_ref ≤ T, written by H, H_ref ≤ H; else refused. Its admission instant enters `extra_instants`. `test_a3`, `test_a5` |
| B3 | Blocker | `--designated` used today's designation, not the one in force at T (ML-15) | **Fixed.** Resolved at T (`KNOWN`) or H (`AVAILABLE`); refused when none was in force. `test_p1` |
| M1 | Major | 33 of 51 mutants survived (target instants, verify checks, selection filters, preflight) | **Tested:** `tests/test_mf7_ops_audit_postgres.py`; 17 targeted mutants, all killed |
| M2 | Major | No hash bound a live snapshot's header and move flags, or a target build's header | **Fixed.** `snapshot_hash` (header, row hashes, factors, move flags) and `build_hash` (header, rows), new columns of pending 0019, checked by `verify`. `test_p3` (8 tamper cases) |
| M3 | Major | Disagreeing target builds not reported (§11) | **Fixed.** `verify` reports settled labels on which two builds under one configuration disagree (`target_disagreements`), without failing either. `test_p4` |
| M4 | Major | An `AVAILABLE` horizon in the future was accepted | **Fixed.** Refused, as is any cutoff in the future. `test_p2` |
| — | Minor | Quarantine instant in `AVAILABLE` defaulted to the batch's `recorded_at` | **Fixed:** the end of the day |
| — | Minor | An import range's calendar row admitted whatever its own `recorded_at` | **Fixed:** admitted only by T (`KNOWN`) or H. `test_p7` |
| — | Minor | Database errors exited 1; `CURRENT` refused only by argparse | **Fixed:** exit 5; `jobs.snapshot` and `targets_store.build` refuse `CURRENT` |
| — | Minor | `VERSION` unchanged since MF-1 | **Bumped** to `mf.2` |
| — | Minors | The settled margin measured from the write, not the load (false alarms only); `eligibility_policy.version` not pinned (a label of the policy's own fields, which are all pinned by the round trip); unlabelled reference rows reported, not stored; a build not checked against its reference's configuration; no lock on `calendar-sync` (duplicate copies possible, harmless); drift fails `verify` until the owner acts; the 18:00 / end-of-day text conflict | **Carried** |

## Re-audit of MF-7c third part: fresh-context agent

At `87b60e2`. Guard logs empty. B1-B3 and the hash, disagreement and horizon Majors confirmed fixed. **REJECT**
(one Blocker). The auditor's contract test: every label rebuilt at T = its own `resolved_at` must come out the same;
99 of 2 168 differed.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| N-B1 | Blocker | A batch's quarantines, ranges and calendar row enter only with the batch's first admitted row, but their instants ignored that (AVAILABLE: the quarantined day itself, opened by C-0065's Minor fix; KNOWN: `recorded_at`, while a row dated after T's day is refused) | **Fixed.** Every batch-derived instant is at least the batch's admission instant (its first admitted row's); a KNOWN row's instant is the later of `recorded_at` and the end of its day (`store.row_instant`). `test_a1`, `test_a6`, and the contract tests `test_p8` (AVAILABLE) and `test_p9` (KNOWN, four variants) |
| M-a | Major | Untested `resolved_at` inputs and hash fields; KNOWN_RECORDED tie-break | **Tested.** The contract tests make each input the binding one in turn (calendar evidence enters every label whole, so each variant orders the evidence); targeted mutants: 20 of 21 killed. The survivor, the range calendar row's own date instant, is equivalent: the calendar admits each date of a range only from the end of that date |
| M-b | Major | `recorded_at`, `created_by`, `pending_sessions` and `company_id` (snapshots), `recorded_at` and `created_by` (target builds) outside the hashes | **Fixed.** Read inside the writing transaction (`now()`, `session_user`, which the column defaults take) and bound into `snapshot_hash` and `build_hash`. `test_p3` (19 tamper cases) |
| — | Minors | `conn` unbound in an error path; an instant without a time zone exited 1 | **Fixed** (a usage error, exit 2) |
| — | Minors | `--designated` at H in AVAILABLE (the design's ML-15 says "at T"); `admissible_reference` stricter than necessary for an AVAILABLE reference in a KNOWN build; `verify --sample` compares disagreements within the sample; `pending_sessions` never written; a live snapshot's configuration JSON not re-hashed | **Carried** (the first is the stated choice: the knowledge instant) |

## Second re-audit of MF-7c third part: fresh-context agent

At `8fe248c`. Guard logs empty. N-B1's fix holds (1 981 labels over 57 builds of a broad scenario, 0 differences);
62 of 62 column tampers caught. **REJECT** (one Blocker).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | Evidence that **removes** something was in no `resolved_at`: a late quarantine, disagreeing correction or zero-price row that makes a date stop being a session (a), and a late quarantine of a security's last row that clips its range so later bars become an absence (b) | **Fixed.** (a) Every date's instants (rows of every security, zero-price rows included, and quarantines) are carried by the next session, so a label reading across a removed date covers them. (b) A bar absent because its range is clipped takes the instants of the security's quarantines dated after it. `tests/test_mf7_ops_contract_postgres.py` (r1-r4, both modes): 5 of its 12 cases fail on the pre-fix code; the two fix mutants are killed |
| M-1 | Major | The merge's and the disagreement quarantine's instants (`max`) untested | **Tested:** `test_r5` (a late batch fills or disagrees on one close); both `min` mutants killed |
| M-2 | Major | KNOWN_RECORDED's tie-break at the same T untested | **Tested:** `test_p5` |
| — | Minor | `jobs.snapshot` and `targets_store.build` accepted a future cutoff or horizon outside the CLI | **Fixed:** refused against the database's `now()`. `test_p2` |
| — | Minors | Designations and configurations outside every hash (a tampered designation `recorded_at` would change selection unseen); on real-data shapes `resolved_at` equals the build's T (the audited calendar-whole rule); identity evidence not loaded (unused); earlier Minors | **Carried**; the designation hash for the owner's review with `main` |

## Third re-audit of MF-7c third part: fresh-context agent

At `f08b200`. Guard logs empty. A contract fuzzer over 240 random scenarios (5 856 labels, both modes) found no
difference outside the shapes below. **REJECT** (three Blockers).

| # | Severity | Finding | Disposition |
|---|---|---|---|
| B-1 | Blocker | A security whose every row was quarantined or disagreed dropped out of the loaded evidence, and with it the instants of the session it removed | **Fixed.** The loader keeps such a security without rows (a snapshot skips it). `tests/test_mf7_ops_contract2_postgres.py` c1, c2 |
| B-2 | Blocker | A supersession naming another security's statement stops applying once that statement is admitted; that statement's instant was not counted (AVAILABLE) | **Fixed.** Every statement a security's statements name counts, whatever its security. c3 |
| B-3 | Blocker | Quarantines dated after T were admitted with their batch and could put `resolved_at` after T | **Fixed.** Not admitted, as rows dated after T's day are not. c4 |
| M5, M7, M10 | Major | The clipped range's latest quarantine, the closing row's instant and a range batch admitted later untested | **Tested.** c5, c6, c7 |
| M13 | Major | `adjustment_set` in the target `row_hash` untested | **Tested:** a tamper case in `test_p3` |
| — | Minor | A confirmation whose `available_at` differs from its `recorded_at` raises `ValueError` on load; the CLI exited 1 | **Fixed:** the CLI refuses it (exit 5); nothing is written |
| — | Minor | `resolved_at` is conservative: median lag 4 sessions without import-calendar ranges, 15 with them (the calendar-whole rule) | **Carried** for the owner (the audited conservative rule) |

The new cases fail on `f08b200`'s code (7 of 13) and pass now.

## Fourth re-audit of MF-7c third part: fresh-context agent

At `ab40f9d`. Guard logs empty. An extended contract fuzzer: 1 489 scenarios on `ab40f9d` (45 964 labels re-checked)
and 3 513 with the suggested fix (107 700 re-checked), adding lone securities, whole-security removal and comeback,
cross-security supersessions and withdrawals, evidence dated after T, and references in the middle of events.
**REJECT** (one Blocker). The three C-0074 fixes hold; the snapshot side is unchanged by row-less securities.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| E-1 | Blocker | A security whose rows were all set aside and that came back through a later row the label never reads: its labels exist at the build but not at their own `resolved_at` | **Fixed:** the earliest instant of the security's surviving rows enters `extra_instants` (its presence in the build). With it the fuzzer's 3 513 scenarios showed no difference and no `resolved_at` after T. `test_c8` (both modes, quarantine and disagreement) |
| M-1 | Major | The snapshot's guard for row-less securities untested | **Tested:** `test_c10` |
| M-2 | Major | The boundary of the after-T quarantine rule (`>` against `>=`) untested | **Tested:** `test_c9` (a quarantine dated T's own day is admitted) |
| — | Minor | The CLI's `ValueError` refusal untested | **Tested** in `test_p2` |
| — | Minors | A database whose only evidence is a row-less security raises a different refusal; `explain` on a row-less symbol (unreachable); `resolved_at` conservative | **Carried** |

Each new guard, reverted, fails its test.

## Fifth re-audit of MF-7c third part: fresh-context agent (accepted)

No Blocker: MF-7c third part is accepted (C-0037), and with it all of Phase 3's steps, MF-1 to MF-7c (C-0086). A
larger fuzzer (5 600 scenarios, 215 876 labels rebuilt at their own `resolved_at`, with securities that lose every
row and come back through one to three rows of any kind, late joiners and statements on them) found no difference
and no `resolved_at` after T; with C-0078 reverted it finds the original fault. 800 scenarios gave identical snapshot
hashes before and after the row-less change. Of 26 mutants, the survivors that change results were two (M17, M18):
the presence rule counting only rows with a close, or skipping zero-price rows. Major MJ-1, a missing test, now
closed: `test_c8` also comes back through a close-less and a zero-price row, and fails under both mutants. The
carried Minors stand.


---

## After the branch review (2026-11-18): candidate actions and database stamps

- **Stamp guard (review M3):** `mf_stamp_guard()` makes `recorded_at` and the writer the database's own on
  `mf_calendar_observations`, `mf_snapshots`, `mf_target_builds` and `mf_candidate_actions`; a worker calendar copy is
  never back-dated. Preflight checks the triggers.
- **`mf_candidate_actions`** (new table in pending 0019; Phase 5 review M-2): known but unentered bonus and rights
  issues with the window their ex-date lies in. Owner-only, stamped, append-only; never a factor, never adjusts a
  price. Filled by `import-tradeiq` from `unentered_actions.csv`, which `run_ca_reconstruct.py` now writes beside
  `reconstructed_actions.csv` (absent: counted as `unentered_file_absent`). Read only by the ML dataset loader, for
  `scoring.1`'s `candidate_event`.

---

## MF-8: the captured path (branch review Blocker B1)

**Why.** MF read only the TradeIQ import; nothing read Phase 2's captured data, so no snapshot row had a
`company_id`, and Phase 4's fundamental engines could not reach an issuer on real captured data. The design already
specifies the captured path (§4.3, §5.1 to §5.4, §8, §13.1) and the pure package implements its pieces
(`sessions.py`, `bars.py`, `universe.py`); the loader and the snapshot path were missing. No new owner decision: the
design fixes the overlap rule (§5.4: compared field by field, a disagreement makes the bar `conflicting`) and market
cap (§5.1: CSE's reported `marketCap` from the stored payload, `provisional`).

**Plan.** Part 1, the loader (done, below). Part 2, the snapshot path: captured and imported bars merged per §5.4,
the session sequence with non-final sessions, `company_id`, `pending_sessions` and `dstar_observation_ids` stored,
and `verify`, `explain` and target builds on the combined evidence. Part 3, the audit.

**Part 1** (`worker/market_feature_store/captured.py`): one read-only, repeatable-read transaction over
`market_capture_runs`, `market_capture_run_events`, `market_source_responses`, `market_capture_security_results` and
`raw_market_observations`. Each terminal event carrying P2's completeness summary becomes a `TerminalEvent` (E1, E2,
universe known, expected and produced, the derivation pass it names; recorded_at = occurred_at, available_at = the
latest observed_at of the run's ok responses; a run with no ok response is no evidence). Final sessions and their
governing events come from `sessions.final_sessions`; non-final sessions from `sessions.classify` (E1 on any admitted
event, or an admitted observation). For each final session, only the governing pass's per-security rows are read, and
each security's admitted observations go through `bars.feature_bar` (MFB-7 keeps only the governing post_close run's).
`company_id` is taken from the latest governing run naming the security. The configuration gains `capture`:
`min_coverage` (0.95) and MF's own reconcile and cross-check tolerances (provisional; P2's `system_config` is not
followed, §5.1). **This changes every MF configuration id**; a configuration written before MF-8 is refused by
`parse` with "register it again". Tests: `tests/test_mf8_captured_postgres.py` (4) drive **Phase 2's real capture
path** against the fake CSE into PostgreSQL and read it back: the final session and its governing run, rows 1 and 3
bars, company ids equal to `companies.id`, the reported market cap, admission by T and by H, a `post_open` run that
never governs, coverage below the minimum giving a non-final session.

**Part 2a, snapshots over captured and imported evidence** (MF code version `mf.3`):
- `worker/market_feature_store/combined.py`: one bar per (security, final session) by §5.4. One source: that bar.
  Both: compared field by field (traded exactly; close, high, low, open by the price tolerance; share volume and
  trade count by the volume tolerance; turnover by its own). Agreement is flagged `imported_agrees`; a disagreement
  makes the bar `conflicting` and keeps the imported value in `imported_differs:<field>=<value>`. The captured bar
  carries the record because it holds the raw provenance, not because its values win.
- `snapshot.build` takes the captured evidence: the session sequence is the union of captured and imported final
  sessions, with captured non-final sessions as its gaps; securities are the union; each row carries `company_id`
  (the latest governing run naming it); non-final sessions after D* are stored as `pending_sessions`; the closes
  actually used are kept for the move flags.
- `store`: `company_id` and `pending_sessions` are written (and bound by `snapshot_hash`); a row's provenance lists
  the imported bar ids, then the raw observation ids of its captured bars (D* and the window); move flags store the
  closes of the bars used. `verify` replays with captured evidence and also compares each row's `company_id`;
  `explain` re-derives the provenance of captured-only securities. `jobs.build_all` loads both; the captured evidence
  enters the input digest only when there is some, so an import-only snapshot keeps its exact digest.
- DE-4's loader now reads the stored `market_cap_reported` feature (fresh, stale or partial) instead of passing
  nothing (branch review m7). Market-value features can therefore be computed on captured data; they remain
  `untrusted` while that input is provisional (P4-D4).
- **Not yet:** target builds over captured sessions refuse with "MF-8 part 2b": labels' `resolved_at` on captured
  sessions needs the governing events' and observations' instants (together with task "label resolution
  granularity").
Tests: `tests/test_mf8_unit.py` (4, the comparison and the configuration block), `tests/test_mf8_snapshot_postgres.py`
(2, end to end: P2's real capture path -> snapshot -> `verify` replay -> DE-4's loader sees every company id and the
market cap; a disagreeing import is `conflicting`, an agreeing one is not, another volume is), `tests/test_mf8_captured_postgres.py`
(5). Mutation check: 13 mutants (each comparison, the conflict mark, the company id in the row and in the loader,
observation admission, events without a received response, captured provenance, the captured evidence in the
digest, DE-4's market cap, the coverage default): all killed; 3 survived the first test set and were closed.

**Part 2b, target builds over captured sessions:** the refusal of part 2a is removed. The captured loader records,
under the query's mode, each date's latest admission instant of any admitted terminal event or observation
(`date_instants`) and each captured bar's own instant: its governing event and every observation reconcile read for
it, superseded ones too (`bar_instants`). `targets_store` folds them in exactly as it does imported rows: a session's
finality takes every instant dated in (previous session, session], so a date between sessions binds the next one; a
security's own bar takes its captured instant; its presence rests on its earliest captured or imported bar.
`resolved_at` therefore stays conservative across captured, imported and mixed windows. Tests: `test_s3` (a label from
a captured Friday to an imported Monday resolves with the right return, and no earlier than the governing capture
run, which is recorded after the import), `test_u5` (finality and the security's own captured instant, each alone).
Mutation check: 5 mutants (finality without captured instants, the bar instant, both, events in the date instants,
observations in the bar instant); 4 killed, and the last is recorded as defensive: under P2's own ordering an
observation is always admitted no later than its run's terminal event, so it cannot raise a bar's instant today.

---

## Label resolution on real data (branch review M1)

**Problem.** On imported history every label's `resolved_at` came out at about the build's T, which would leave Phase
5 no training rows. Two conservative rules read evidence from far outside a label's own window: calendar evidence
entered `extra_instants` whole (an import range up to the build's D\*, every declared closure whatever its date;
audit 8's broad reading), and every factor and move statement of the security entered, whatever its date.

**Change.** Both are now read per label, up to the last session the label read:
- `targets.label(..., calendar_upto=f)`: f(last) is the latest admission instant of the calendar evidence about dates
  up to `last`; `targets_store.calendar_instant` counts an import range up to that date and a declared closure only
  if it is about such a date.
- Factor statements of factors with an ex-date at or before that session, and move statements of moves starting at
  or before it (each label still passes the statement ids it used, as before).
Evidence about later dates cannot change what the label read: whether a later date is a session does not change the
sessions up to it, and a factor after F changes nothing on F's price basis (B-T7's root fix).

**Evidence.** `tests/test_mf6_fuzz.py` gains a property test that perturbs the factor itself (present against absent,
since before its statement is admitted the build has no such factor): 4 seeds x 800 cases, 0 leaks; and a test that a
factor after the window no longer delays the label and that calendar evidence is read up to F only. `test_l18` now
asserts the new rule (a factor after F no longer delays; one inside the window still does). `tests/test_mf7_ops_postgres.py`
`test_o8b`: an AVAILABLE build over imported history with a calendar range over the whole batch, a bonus announced
after the window and a closure notice after the window: the 1-day label resolves at the end of its own F, the 5-day
at the end of its F. Each part fails that test when reverted to the old rule (3 mutants). A finding stated plainly:
removing the factor-statement set altogether also passes every test, because each label already passes the
statement ids it used; the narrowed set is kept as a defensive superset within the window.

**Not changed, by design:** in KNOWN, an imported row is admitted only once its batch is recorded (§4.1), so labels
over imported history in KNOWN resolve at the import's own time. Training on imported history uses AVAILABLE eras
(labelled `reconstructed`), which is the design's intent.

## MF-8 audit (fresh-context agent, at 1b2a2be): accepted

No Blocker (C-0105): point-in-time admission in KNOWN, the mapping of P2's terminal events, the overlap tolerances,
`resolved_at`, DE-4's market-cap read and the configuration change were verified; an import-only snapshot is byte for
byte what it was before MF-8. Three Majors, all fixed:
- **F1** an event's `available_at` was the latest `observed_at` of all the run's ok responses, so resuming a partial
  run moved an earlier event's availability and an AVAILABLE snapshot stopped replaying. Now only the responses that
  event saw: attempts numbered up to the count P2's summary records when the event is written
  (`summary.requests.attempts`; attempts are numbered in order across resumes), which needs no comparison of clocks.
  Tested with P2's real resume (`test_c6`).
- **F2** an empty comparison was recorded as agreement: a missing captured bar against an imported close, or a
  quarantined or zero-price import, showed `imported_agrees`. Now agreement needs at least one compared field (else
  `imported_not_compared`), an imported-only value is kept (`imported_only:<field>=<value>`), and an import that
  disagrees with itself makes the bar `conflicting`.
- **F3** 22 of the audit's 36 mutants survived. New tests: `tests/test_mf8_audit_unit.py` (8: the summary mapping, the
  merge edge cases and the tolerance edge, non-final sessions as gaps and as pending, pending in the hash, the digest
  rule, provenance, DE-4's state filter, the capture block), `test_c6` to `test_c8` (resume, the governing pass only,
  the latest member's company, a session proven by an observation alone), `test_s4` (a stored pending session that
  verifies; verify's replay notices a company changed in P2's evidence; explain proves a captured security).
  Re-run of the audit's mutants plus 7 on the fixes (43): 36 killed. Survivors, recorded: m09 (responses of any
  outcome: P2's archive gives no non-ok response with a later time inside a run in the fake CSE), m10 (the first
  company wins: equivalent while every capture is of one date, the fake CSE's), m12 and m29 (defensive instants),
  m19 (a captured non-final session is a gap either way unless calendar evidence says the date was closed), m34
  (equivalent).
Minors: **F4** fixed (on a captured final session, an imported bar of a security the governing run does not list is
not used); **F7** fixed (the capture block must name every tolerance; `min_coverage` in (0, 1]; a missing tolerance
raises instead of becoming 0); **F5**, **F6** and **F8** recorded in the code (observations by company and date;
two read transactions, exact for a settled T; `snapshot_hash`, not `row_hash`, binds `company_id`); **F9** stale
docstrings fixed. The static network guard allows exactly P2's JSON path `summary -> requests -> attempts` in
`captured.py`; its import check is unchanged.

---

## Owner statement tooling (branch review M4)

`worker/market_feature_store/statements.py` and three owner commands of `cse-features` (owner path: cse_migrator
acting as cse_owner; `docs/ops/P3_MARKET_FEATURES.md`):
- `action`: one corporate-action statement (kind, ex-date, terms, announcement date, source; optionally the row it
  supersedes), available from the end of the announcement's Colombo day (§4.1);
- `withdraw`: a `withdrawn` statement naming the row it cancels;
- `confirm-no-action`: a `no_action_confirmed` statement for a security and two sessions; its `available_at` is its
  `recorded_at`, both the database's now() (§4.1).
Each statement passes the build's own validation (`actions.statement`) before it is written, so a row the build would
refuse is never stored; a superseded or withdrawn row must exist and be of the same security; the operator who ran
`sudo` is kept in the source reference. **A consequence of the design, stated for the owner:** a confirmation has no
announcement date, so it counts only from its entry: a build at an earlier T still sees the move open (nobody knew
then); a later target build (T after the entry) resolves the labels (§11). Test: `test_o10` (the commands through the
CLI; the worker refused; three invalid statements refused and not written; a withdrawal of another security's row
refused; the confirmation leaves an old-T build's move open and confirms it in a build after the entry). Mutation
check: 6 mutants, 5 killed; the owner-path check in Python survives because the database refuses the worker as well
(owner-only table; the worker cannot act as cse_owner): defence in depth.

**Not built, and why:** an `identity` command. `mf_identity_observations` is written by the TradeIQ import but the
snapshot build does not read it yet (`universe.series_segments` is not called), so a command would store evidence no
build uses. Wiring identity evidence into the build, then the command, is a task before `main`.

---

## Identity evidence in the build (§8.1), and the owner's `identity` command

Identity observations were written (the TradeIQ import's 1990s-code links) but never read. Now:
- `store.load_identity` reads every `mf_identity_observations` row; `jobs.build_all` passes them to the build, and the
  ids the query admits enter the input digest (only when there are some, so a snapshot without identity evidence
  keeps its exact digest).
- `snapshot.build` makes each security's series from `universe.series_segments` (admitted by the query: a link from
  its recorded_at in KNOWN, from its announcement in AVAILABLE): on each session the bar is that of the symbol whose
  segment holds it, the predecessor before a link's effective date, nothing before a reuse; the predecessor's
  corporate-action statements are the series' own. `snap.segments` and the admitted observations are kept.
- Provenance (`store.provenance`, so `explain` and `verify`) takes each session's rows from its segment's symbol.
- Target builds: a session's own instant is that of its segment's symbol; presence spans the series' symbols; the
  admission instant of every identity observation naming one of them enters `resolved_at`.
- `cse-features identity --kind link|symbol_reused ...` (owner path), checked by the build's own validation.
**Effect on real data:** the TradeIQ import's links (a 1990s classless code continued by `CODE.N0000`) now join those
series, so features over the late 1990s and 2001 see one security's history instead of two broken ones.
Tests: `test_o11` (a link joins NEW to OLD so a 21-session return exists; KNOWN before the entry and AVAILABLE before
the announcement do not join; the snapshot replays; explain names the predecessor's rows; a reuse cuts the earlier
bars; a link without a predecessor refused; the worker refused) and `test_o12` (the predecessor's bonus is a factor of
the series; the digest lists the admitted link and only when admitted; a label resting on a link announced late
resolves no earlier than the notice). Mutation check: 9 mutants, 8 killed; the survivor (a session's own instant taken
from the security's own symbol) is equivalent while one batch holds both symbols' rows, as in these tests.

---

## Snapshot market features and survivorship stored (owner decision C-0116)

Pending migration 0019 gains two columns on `mf_snapshots`:
- `market_json`: the market-wide features at D\* (`mkt_ret_ew_1/5/21/63`, `mkt_breadth_1`), canonical JSON in the
  rows' feature format;
- `survivorship`: the survivorship of the imported history the snapshot read.

`store.snapshot_survivorship` is the rule, moved unchanged from the ML loader. A snapshot that read no import is
`complete`. Otherwise it takes the worst survivorship of the admitted raw `row_absent` batches that have a bar in the
400 days before D\*; with no such batch in reach, `unknown`.

Both values are bound into `snapshot_hash`, and `verify`'s replay recomputes and compares them. The ML loader reads
them, which closes C-0089's departure, and the dataset's market-trend tag is no longer `undefined`.

**MF code version `mf.4`**: snapshots written by `mf.3` show a different code version in `verify`.

Tests:
- `test_mf8_snapshot_postgres` (both stored, equal to the build, tampering refused);
- `test_m52_loader_postgres` (l1, l2 and l6 to l9: the rule at MF, the column at the loader, the trend tag).
