# Runbook: F7 financial features and DE engines (Phase 4, DE-4)

Design: `docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md` §10. Implementation record: `docs/PHASE4_IMPLEMENTATION.md`.
Storage: pending migration `supabase/migrations_pending/0020_financial_features_and_engines.sql` (applied after
0019; moving it into the chain is part of the owner's merge decision).

Nothing here contacts CSE or any network. F7 and DE read only what PostgreSQL already holds: a stored MF snapshot,
the F8 evidence and the issuer link decisions.

## Who runs what

| Command | OS user | PostgreSQL login |
|---|---|---|
| everything except `designate` | `cse-worker` | `cse_worker` (peer) |
| `designate` (owner decision) | `cse-migrator` | `cse_migrator`, acting as `cse_owner` |

All through `sudo bash /opt/cse/app/ops/bin/cse-engines <command>`. Exit status: 0 ok, 4 PostgreSQL unavailable,
5 refused or failed.

## Commands

- `preflight`: the worker role's security preflight (also run before every writing command).
- `register-configuration`: insert the F7 and DE configurations if absent (provisional; content-addressed).
- `designate --kind f7|de --configuration ID --note TEXT`: the owner's designation of a configuration. A KNOWN run
  is refused when the configuration designated at or before its T is not the one the code implements; an AVAILABLE
  run names `--f7-configuration`, `--de-configuration` and `--f8-configuration` explicitly (FL-10).
- `run --snapshot ID [--f8-configuration ID]`: for a stored KNOWN or AVAILABLE MF snapshot, read its rows, the link
  decisions and one F8 result per linked issuer; build the panels, features, eligibility and every engine's output;
  write them in one transaction. A CURRENT snapshot is refused. A run is refused until its governing time (T, or H
  in AVAILABLE) is at least 15 minutes old by the database clock: a link decision stamped before it may still be
  uncommitted until then. Run it again later.
- `verify`: recompute every stored hash (configuration ids, panel, feature row, output and run hashes); report
  disagreements. Read-only.
- `status`: counts and the latest runs.

There is no delete, reset or repair command: the tables are append-only for every role.

## Known limits

- `market_cap_reported` is not stored in an MF snapshot row, so the market-value features (`book_to_market`,
  `earnings_yield_mcap`, ...) are `missing` with reason `incomplete_lines` and `fund_valuation_mcap` scores
  `insufficient_inputs` (C-0079). Even when stored, they would be `untrusted` while that input is provisional (P4-D4).
- The reference baselines (DE-3) exist as functions (`worker/engines/baselines.py`) but `run` neither computes nor
  stores them.
- On a snapshot built only from imported history, no line has a `company_id`, so every line is `issuer_link_absent`
  and the fundamental engines score nothing: the design's "captured securities only" (§4.6) with no captured era yet.
  MF does not yet read P2's captured data (branch review, 2026-11-18).
- A run of an AVAILABLE snapshot, or of one that read imported history, marks every row and the run
  `fundamental_coverage: captured_only`: only captured securities can have an issuer link there (§4.6).
- Running the same snapshot twice stores two runs; neither replaces the other.
