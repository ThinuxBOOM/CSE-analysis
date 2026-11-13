# Phase 3: market feature foundation (implementation record)

**Design:** `docs/PHASE3_MARKET_FEATURES_DESIGN.md`, revision 5, accepted [delegated] for implementation on
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0026). **Branch:** `integration/cloud` only. Nothing
here is on `main`, nothing is frozen, and nothing has run against real data: no production data exists.

Each step below is implemented, tested, audited by a fresh-context agent (C-0005) and recorded before the next.

## MF-1: modes, finality, bars, the reference price

**Status: implemented and tested; audit pending.**

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

**Status: implemented and tested; audit pending.**

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

**Status: implemented and tested; audit pending.**

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
