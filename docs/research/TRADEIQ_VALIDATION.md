# TradeIQ historical data: validation of the daily share prices

**Status: first validation pass of the daily share prices, 2026-11-14.** Owner decision C-0035 (G-1 extension E1).
Done by the cloud session on `integration/cloud`. Nothing has been imported into project tables. No data file is
committed: the repository holds the scripts, the tests and a counts-only summary
(`docs/research/tradeiq_validation_summary.json`, which also lists every source file's SHA-256).

| | |
|---|---|
| Source | `https://github.com/TradeIQ-CSE/cse-dataset`, commit `7903c390c1b94e050e62440e32b6a5b2d0c399e7` (2026-10-19), fetched from GitHub; no CSE request |
| Files validated | The 33 original daily share-price workbooks under `historical_data/` (1991 to 2025), read independently of TradeIQ's converted CSVs; the daily index workbook as an independent trading calendar; the share-split workbook |
| Code | `scripts/research/tradeiq/daily_parse.py` (parser), `validate_daily.py` (checks), `run_validation.py` (one command, about one minute); tests `tests/test_tradeiq_parse_unit.py` (synthetic rows only) |
| Reproduce | `python3 -I scripts/research/tradeiq/run_validation.py <checkout> <work dir outside the repo> <summary.json>` with xlrd 2.0.1 and openpyxl 3.1.5 |

## 1. Answer in one paragraph

The archive is **usable as the project's historical price source from 1992 to 2025, with known, bounded defects that
the import must quarantine or flag.** It holds 1,420,779 daily price records for 945 securities. Prices are **raw**
(not back-adjusted): 131 of the 138 listed share splits show the exact split-ratio drop on the effective date. From
1992 on, the trading days in the price files match the index calendar **exactly in every year** (one extra date in
2010). Delisted securities are present (640 securities stop trading before 2025). The defects are listed in §3; none
of them is silent once the import applies the rules in §4.

## 2. What the data is

| Years | Layout | Fields | Records | Trading days vs index |
|---|---|---|---|---|
| 1991 | database export (date, code, close) | close only; no class, no volume | 4,342 (incl. 13 stray 1990 rows) | from about mid-1991: 120 of 239 days |
| 1992 to 2000 | database export | close only | 178,067 | 2,162 of 2,162 |
| 2001 | export, two tables side by side | close, share volume | 19,071 | 240 of 240 |
| 2002 to 2016 | printed report, one block per security | high, low, close, trades, shares, turnover | 688,754 | 3,596 vs 3,595 (2010-03-08 is not an index day) |
| 2017 to 2025 | one table (2023 is a CSV) | as above, plus CSE's reference "open" | 530,545 | 2,122 of 2,122 |

Rows exist only for days a security traded (no zero-volume rows from 2002 on), so the **no-trade convention is
`row_absent`**. Security keys are `CODE.CLASS` + sub-type, as CSE prints them (`AAF.N0000`); classes N, X, W, R, P, D,
U, B occur.

## 3. Findings

| # | Finding | Size | Disposition |
|---|---|---|---|
| F1 | **Prices are raw.** Split test: 131 of 138 splits drop by the ratio; 1 shows no drop; 3 other moves; 3 symbols in the split list match no price series | 138 splits | `price_basis = raw`. The 7 exceptions are checked by hand at import |
| F2 | **Trading calendar matches the index exactly from 1992**, year by year | 34 years | The index workbook is the import's calendar evidence (`import_calendar_complete` per year) |
| F3 | **1990s exports lose rows.** Each export states "N rows selected"; the files hold fewer: 1991-93 −436, 1994 −13, 1995 −10, 1996 −11, 1997 −8, 2000 −1; 1999 exact; 1998 has no footer | 479 rows | Those days are unknown, not no-trade. Survivorship for 1991-2000: `unknown` |
| F4 | **1990s prices have no share class** (`ABAN`, not `ABAN.N0000`). Of 256 codes, 219 later exist only as N, 5 as N and X, 32 do not appear in 2001-02 | 182,470 records | Linked to N only where the code later has N alone; the other 37 codes stay unlinked. An identity observation per link (Phase 3 §8.1) |
| F5 | **Zero prices in 1991-1997** (meaning undocumented) | 3,975 | Not a price: flagged `zero_price`, never used as a close |
| F6 | **2023 CSV: prices of 1,000 or more were split at the thousands comma**, shifting the row | 1,137 rows (1.8% of 2023) | Quarantined; the pieces cannot be rejoined without guessing. Those days are unknown for those securities |
| F7 | **2016 has no "open" column**; positions shift | whole year | Fixed: columns read by name |
| F8 | **2010 and 2012: numbers overflowed into the next cell** in the printed report | 32 + 6 rows | Repaired exactly (split is unambiguous); flagged `repaired_overflow` |
| F9 | **Traded, no price printed** (shares and turnover present, prices blank) | 74 rows | Phase 3 row 2: traded, price unknown (`no_price_printed`) |
| F10 | **Same security printed in two blocks in one year with different prices** (2003 to 2015; e.g. a second block that is probably another share class labelled N) | 765 security-days | Quarantined (both), until resolved by continuity with the neighbouring years |
| F11 | **CSE's close is carried when too little trades** (from 2015): the close equals the previous close although the security traded; 99.9% of closes outside the day's high-low are this | 53,507 rows | A property of CSE's official close, also present in our own capture (MFB-5). **Design note for Phase 3**: such a day's return is zero by CSE's definition |
| F12 | **2009's first block has no company line** | 53 rows | Rejected (identity unknown) |
| F13 | Duplicates across overlapping files (2001/2002, the separate SAMP sheet, the 2001 side table) agree | 5,178 keys agree, 1 disagrees | One copy kept; the one disagreement is a 1995 zero price (F5) |
| F14 | 1995 typo `194..75` | 1 row | Rejected |
| F15 | Turnover per share outside the day's range by more than 5% | 288 rows | Flagged; not used to correct anything |
| F16 | Close-to-close moves above 100% | 179 | Raw prices: splits, bonus and rights explain part. Phase 3's detector (MF-3) classifies them once corporate actions are loaded |
| F17 | Split announcement dates are missing for 108 of 138 splits | 108 | For `AVAILABLE` mode, use the effective date as `available_at` when no announcement date exists: later than the truth, so it cannot leak |

## 4. Rules for the import (MF-7)

1. Admit 1992 to 2025; 1991 is partial and is admitted with its known gaps; the 13 rows dated 1990 are not admitted.
2. Per batch: `price_basis = raw`, `no_trade_convention = row_absent`, survivorship `unknown` for 1991-2000 (F3) and
   `complete` from 2001 pending the delisting-list check (§5), provenance (repository, commit, file, sheet, row,
   SHA-256).
3. Quarantine F6, F10, F12, F14; flag F5, F8, F9, F15; never fill a quarantined day.
4. Calendar from the index workbook; imported history is usable in `AVAILABLE` (reconstructed) mode only, which is
   why Phase 5 needs the owner's waiver P5-R5.

## 5. Not done yet

- Bonus issues, rights issues, dividends and listings/delistings workbooks: not yet parsed or checked (needed for
  P3-R2 and for survivorship from 2001).
- Resolving F10 by continuity, and the 7 split exceptions (F1), by hand.
- A cross-check against the project's own captured days: none exist in this sandbox.
- No other data family (indices beyond the calendar, market statistics, GICS sectors, foreign holding) is validated.
