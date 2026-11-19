# Phase 3 MF: market feature store, operations

**Status: built and tested on `integration/cloud`; not on `main`; never run on the server.** Migration 0019 is still
pending (`supabase/migrations_pending/`, outside the chain, C-0053). Nothing here runs until the owner merges MF into
`main`, applies 0019 under change control (design §13.3) and stages the build again (MA §43.1). There is no timer.

MF contacts no network and never CSE. It reads only PostgreSQL; the owner's TradeIQ import also reads local files.

## 1. Commands

All through `sudo bash /opt/cse/app/ops/bin/cse-features <command>`. A JSON report goes to stdout. Exit status: 0 ok
(or already present), 4 PostgreSQL unavailable, 5 refused or failed.

| Command | Runs as | Writes | What it does |
|---|---|---|---|
| `preflight` | worker | nothing | The worker role's security check: not a superuser or a member of an owner role; SELECT + INSERT on the worker's 8 tables, SELECT only on the owner's 7, no UPDATE / DELETE / TRUNCATE; every 0019 trigger enabled; no EXECUTE on a guard function. Every writing command runs it first |
| `register-configuration [--min-history-sessions N] [--min-constituents N] [--min-median-turnover LKR]` | worker | `mf_configurations` | Inserts the content-addressed configuration (provisional) if absent; prints its id |
| `designate --configuration ID --note TEXT` | **owner** (cse-migrator → cse_owner) | `mf_designations` | **Owner decision:** the canonical configuration from now on. Consumers read the one in force at their instant (ML-15) |
| `import-tradeiq --archive DIR --work DIR --summary JSON --reconstructed CSV` | **owner** | the import tables | **Owner step (C-0035):** the TradeIQ import in one transaction. The inputs are a local archive checkout and the validation outputs (`scripts/research/tradeiq/run_validation.py`, `run_ca_reconstruct.py`); needs `xlrd` |
| `action --id ID --symbol S --kind KIND --ex-date DATE --terms JSON --announced DATE --source TEXT [--supersedes ID]` | **owner** | `mf_corporate_action_observations` | **Owner step (§7.1; branch review M4):** one corporate-action statement, checked by the build's own validation before it is written. KIND is `subdivision`, `consolidation` (terms `old_shares`, `new_shares`), `bonus`, `scrip_dividend` (`held`, `new`), `rights` (`held`, `new`, `subscription_price`), `cash_dividend`, `merger`, `delisting` or `suspension`. It is available from the end of the announcement's Colombo day. `--supersedes` names an earlier row of the same security that this one corrects. Example: `action --id own:JKH:2026-03 --symbol JKH.N0000 --kind subdivision --ex-date 2026-03-10 --terms '{"old_shares":1,"new_shares":2}' --announced 2026-02-20 --source 'CSE notice 2026/41'` |
| `withdraw --id ID --withdraws ID --symbol S --announced DATE --source TEXT` | **owner** | `mf_corporate_action_observations` | **Owner step:** cancels the named statement of the same security from the end of the announcement's day |
| `confirm-no-action --id ID --symbol S --from DATE --to DATE --source TEXT` | **owner** | `mf_corporate_action_observations` | **Owner step (§11):** the move between the two sessions is genuine (no action). Labels built after it resolve with their real return instead of `unexplained_move_in_horizon`; it counts from the moment it is entered |
| `calendar-sync` | worker | `mf_calendar_observations` | Copies `trading_calendar`'s `closed` rows as `declared_closed` evidence, available from the sync instant. Idempotent |
| `snapshot --mode KNOWN\|AVAILABLE --cutoff ISO [--horizon ISO] (--configuration ID \| --designated)` | worker | the snapshot tables | One snapshot. A cutoff or horizon in the future is refused; `AVAILABLE` takes H = now unless given. `--designated` reads the designation in force at T (`KNOWN`) or H (`AVAILABLE`) |
| `targets ... --reference SNAPSHOT_ID [...]` | worker | `mf_target_builds`, `mf_target_rows` | A target build: labels of the reference snapshots' rows at horizons 1, 5, 10, 21. A reference snapshot outside the build's information set is refused (in `KNOWN`: a `KNOWN` snapshot written by T; in `AVAILABLE`: T and H no later than the build's, written by H) |
| `select --at ISO` | worker | nothing | `KNOWN_RECORDED`: the stored `KNOWN` snapshot under the configuration designated at that instant, written by then, with the latest T (ML-23) |
| `explain --snapshot ID --symbol S` | worker | nothing | One stored row with its factors and move flags, and the `mf_history_bars` ids behind its D\* bar and windows, re-derived and checked against the stored ids and digest |
| `verify [--sample N]` | worker | nothing | Re-proves what is stored (§3). Exit 5 on any problem |
| `status` | worker | nothing | Counts, the designation in force, the latest snapshots |

There is no delete, reset or repair command. Every MF table is append-only for every role.

## 2. First use (after 0019 is applied)

1. `preflight` → `{"problems": []}`.
2. `import-tradeiq ...` (owner). Expected counts on the archive of 2026-10-19: `docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`
   ("Real plan after the fixes").
3. `calendar-sync`.
4. `register-configuration` with the chosen parameters; then `designate` (owner).
5. `snapshot --mode AVAILABLE --cutoff <T> --designated`; then `verify`.

## 3. What `verify` proves

- Every snapshot's `snapshot_hash` (its header, row hashes, factors and move flags), so a live snapshot is proved too.
- Every snapshot row's `row_hash` (which covers its D\* bar ids and window digest) and `security_id`, and the row count.
- `adjustment_set_hash` from the stored factor list.
- **Replay** of a snapshot whose governing time (T in `KNOWN`, H in `AVAILABLE`) was settled when it was written,
  i.e. at least 15 minutes before its `recorded_at` (F8's rule with a margin; MF's writers are short). It is rebuilt
  from the evidence under its stored configuration and compared: D\*, input digest, adjustment set, rows, factors,
  move flags. A live snapshot (written within the margin) is proved from its stored rows only. Use `H` well before
  the build to make a snapshot replayable.
- Every target row's `row_hash` and every build's `build_hash`.
- Disagreements: settled labels on which two target builds under one configuration disagree are listed
  (`target_disagreements`) for the owner; they do not fail `verify` (a later build never replaces an earlier one).
- Calendar drift: every `calendar-sync` copy whose `trading_calendar` row has since changed or reopened. A copy is
  never repaired; the owner decides what the change means.

## 4. Security model

- The worker inserts configurations, `calendar-sync` copies (guarded by the `trading_calendar:` prefix), snapshots and
  target builds. Designations, imports, corporate actions and identity links are owner evidence (cse_owner only).
- `calendar_sync.py` is the only MF code that reads `trading_calendar` (a static test pins it).
- `CURRENT` results are never stored: `snapshot` and `targets` accept `KNOWN` and `AVAILABLE` only.

## 5. Known limits

- Identity evidence (`mf_identity_observations`: links and reused symbols) is written by the TradeIQ import but not yet
  read by the snapshot build, so there is deliberately no owner command to enter it yet (a task, before `main`).

- The eligibility policy and the unexplained-move threshold are **provisional** (design §17); a configuration is
  `validated` only with a validation report.
- `explain` re-derives provenance by rebuilding at the snapshot's query; for a live snapshot later evidence can make
  it differ (`provenance_ok: false`), as for a replay.
- Captured (P2) evidence is not read yet: there is no capture era in this build.
