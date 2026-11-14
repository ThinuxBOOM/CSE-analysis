# Phase 3: market feature foundation (implementation record)

**Design:** `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, revision 5, accepted [delegated] for implementation on
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

**Status: implemented and tested; audit pending.**

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
  gives `low_activity`, with the measured median stored.
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

**Status: implemented and tested; audit pending.**

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

## MF-6: targets (DRAFT, checkpoint of 2026-11-13)

**Status: draft. Written, imports cleanly, NOT TESTED, NOT AUDITED. Do not build on it before its tests exist.**

`worker/market_features/targets.py` was written just before the checkpoint (C-0034) and is committed so that the
work is not lost. It has no test file. What it contains, against design §11:

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
