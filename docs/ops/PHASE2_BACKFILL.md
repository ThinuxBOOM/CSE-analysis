# Phase 2 backfill: operations and pilot (HB-6)

**Status:** **HB-6 IMPLEMENTATION FROZEN / ACCEPTED** (final freeze audit 2026-10-13; code `9f05041` on
`claude/hb6-operations-pilot`, from `main` `88b29dd`). The freeze record is in the Phase 2 design (HB-6 implementation
status and HB-6 freeze). Nothing here has contacted CSE. The live pilot is an owner-run release event after Docker staging,
server provisioning and the HB-P1 chain (Master Architecture §43.1). G-1 is an owner-accepted risk, **not** CSE
authorization (`docs/governance/G-1_CSE_DATA_USE.md`).

Design: `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` (sections 14, 16, 17, 20, 21, 23.4, 25 and 27). Where this runbook
and the design differ, the design wins.

## 1. What HB-6 adds, and what it reuses

HB-6 is the runner, its command line and the operator and owner actions, composed over the frozen HB-1 … HB-5:

| Layer | Used for | Entry points |
|---|---|---|
| HB-1 (`worker/financial_backfill`) | Durable state: wake-ups, leases, items, events, blocks, holds, budget; the owner path | `store.*`, `owner.*`, `states.check` |
| HB-2 (`worker/backfill_transport`) | Every CSE request, inside HB-3's and HB-4's slices only | its gates and errors; its `Runtime` |
| HB-3 (`worker/backfill_discovery`) | Discovery, issuer evidence, link passes | `create_plan_items`, `DiscoverySlice`, `closure`, `identity_pass`, `closure_pass`, `late_pass` |
| HB-4 (`worker/backfill_documents`) | Documents (pilot HB-S3, bulk HB-S4) | `DocumentSlice(stage).run(filing_ids=…)`, `document_gate`, SIGTERM handling |
| HB-5 (`worker/backfill_f6`) | Validation, configuration, reconciliation, promotion, coverage, anomalies, reports | `validate_pending`, `register`, `reconcile_ready`, `promote_documents`, `audit.run`, `reports` |
| F6.4 | Read-only `verify`; the owner's designation stays F6.4's own command | `ops/bin/cse-financial designate` |

HB-6 adds **no** migration, schema object, grant, role, row-level security, `SECURITY DEFINER` or lock key, and changes
no frozen file. Its only frozen-test change is owner decision HB6-1 (section 13).

## 2. Files

| File | Role |
|---|---|
| `worker/backfill_ops/` | The package: `runner`, `claim`, `decisions`, `pilot`, `status`, `actions`, `reports`, `preflight`, `config`, `cli` |
| `ops/bin/cse-backfill` | The operator / owner wrapper (sudo; `cse-worker`, or `cse-migrator` for owner decisions) |
| `ops/backfill/cse-backfill.service`, `cse-backfill.timer` | One wake-up; boot + every 15 minutes (`*:07/15`), no `Persistent=` |
| `ops/config/backfill.env.example` | `/etc/cse/backfill.env`: the report directory, the optional database-disk path |
| `ops/provision/provision_backfill.sh` | Provisioning (after P1): migrations, directories, units, verification; never arms |
| `ops/tests/provision_backfill_in_docker.sh`, `backfill_container_probes.sh` | The clean-server Docker test (section 23.4) |

## 3. One wake-up (design section 20)

1. **The claim (single runner).** The runner records its own HB-1 wake-up row only inside a `SERIALIZABLE` transaction
   that finds no other live runner. A second runner (a manual `run` beside the timer) records a `busy` wake-up and does
   nothing; two racing runners cannot both commit (PostgreSQL aborts one). No new lock key. A dead runner's row is
   expired by the next runner (HB-1: a live session is never taken over).
2. **The preflight:** HB-1, F6.4, HB-3, HB-4, HB-5 and HB-6 (pins, static boundaries, privileges). A problem refuses (5).
   Then **the disk** (design sections 20 and 24): under 20 GiB free on the database disk (`CSE_BACKFILL_DB_DISK_PATH`,
   default `/var/lib/postgresql`), or a disk that cannot be checked, refuses (5) before any work. Every wake-up records
   the database size and the free space of the database disk and of P1's dump destination (`CSE_BACKUP_ROOT`). The dump
   destination is monitored only: less free space than the database's size is an alert (2), never a refusal (MA §49).
3. **The gates:** an unacknowledged Phase 2 or P2 block stops **every** stage (3). While armed, the clock must be
   NTP-synchronised and must not have gone backwards (5, P3's rule).
4. **Non-CSE work** (never holding P2's lock `…312`):
   - HB-S1 armed: the IE-2 identity pass, once a qualifying P2 sweep exists;
   - HB-S2 armed: the plan items, then (once the current plan is closed) its IE-4 closure pass;
   - F6 (whatever is armed): a validate-pending batch (50), the configuration registered (never designated), document
     promotion, the reconcile passes that are due (5), promotion again;
   - the audit, at most once per Colombo day while armed; its per-filing table goes to `CSE_BACKFILL_REPORT_DIR`.
5. **At most one CSE slice**, in stage order: discovery while the current plan is open (HB-S2); otherwise documents
   (HB-S3 over the owner's pilot sample; HB-S4 up to the armed checkpoint block). HB-2 applies the arming, the budget,
   P3's quiet window and the release guard; a busy lock means `busy` (nothing taken over).
6. The wake-up is recorded with its result.

Exit status: **0** ok (idle, worked, or busy), **2** needs attention, **3** blocked, **4** PostgreSQL unavailable,
**5** refused. 2, 3, 4 and 5 leave the unit failed (`systemctl --failed`).

## 4. Commands

| Command | Role | Does |
|---|---|---|
| `status` | worker | Arming, stages, items by state, today's budget, blocks, holds, wake-ups, live runner, lease, disk, F6, **alerts** |
| `plan` | worker | What the next wake-up would do; no write, no CSE contact |
| `run` | worker | One wake-up (the timer runs `run --trigger timer`) |
| `pilot-sample --out FILE` | worker | The stratified pilot sample **proposal** (section 7) |
| `pilot-report --out-dir DIR` | worker | Section 27's pilot report, with the section 25 reports |
| `report --out-dir DIR [--with-audit]` | worker | Section 25's reports |
| `audit [--window-first D --window-last D]` | worker | One coverage audit now (single runner); `--window-*` after a disarm |
| `anomalies` | worker | The anomaly catalogue (every L10 record) |
| `verify` | worker | The HB-6 composite preflight and F6.4's `verify` |
| `requeue --item KEY --reason TEXT` | worker | Explicit re-queue of a **final** item (reason ≥ 10 characters) |
| `resume --item KEY --reason TEXT` | worker | A blocked item back to pending, after the owner's acknowledgement |
| `late-pass` | worker | The late-evidence link pass (single runner) |
| `arm …`, `disarm --note`, `acknowledge-block`, `resolve-hold` | **owner** | Owner decisions (cse-migrator → cse_owner) |

Reports are written only outside the repository, to new files (refused inside it; never overwritten). No report repeats
the User-Agent. There is no delete, reset, purge or "retry everything" command.

## 5. Stages: the owner's sequence

All of it after the deployment prerequisites (section 12). Each step is the owner's decision; the runner never moves a
stage on by itself.

1. **HB-S1 (identity).** P2's own sweep on a non-trading day (IE-2; `cse-capture sweep`), then
   `arm --stages HB-S1 …`: the runner's next wake-up imports it (`link_pass:1`).
2. **HB-S2 (discovery).** `arm --stages HB-S1 HB-S2 --attempts-per-json-request 1 …` (D-HB3-1): the runner plans the 66
   feed months and one listing per verified security, runs discovery slices until the current plan is closed, then the
   IE-4 closure pass. `status` / `plan` show progress; holds appear under `holds`.
3. **HB-S3 (pilot).**
   1. `cse-backfill pilot-sample --out /var/lib/cse-backfill/reports/pilot-sample.json` (read-only proposal);
   2. the owner reviews (and may edit) the file;
   3. `arm --stages HB-S2 HB-S3 --pilot-sample <file> …`: the sample is recorded in the arming decision
      (`expected_requests.pilot_filings`); pilot slices touch only those filings;
   4. once the pilot's first F5 runs exist, the runner registers the configuration and `status` asks for the owner's
      designation: `sudo bash ops/bin/cse-financial designate --configuration <id> --note …` (F6.4's owner path);
   5. validation, reconciliation and the daily audit then follow automatically;
   6. when every pilot item is final the runner reports `pilot_complete`: `cse-backfill pilot-report --out-dir …`.
4. **Owner review of the pilot report** (section 27's exit criteria) before any bulk arming.
5. **HB-S4 (bulk documents, HB-7)**, only after step 4's owner review of the pilot report and once R-F4 (full F4
   structure persistence; owner decision HB-Q7, 2026-10-13, Option A) is implemented, independently audited,
   accepted and frozen (Phase 2 design §14.1, §27). This adds to the requirements below and replaces none of them.
   `arm` does not check R-F4: the owner confirms it before arming. Then in checkpoint blocks: after a recorded backup
   checkpoint (section 6),
   `arm --stages HB-S4 --expected-documents ≤2000 --checkpoint-dump-run … --checkpoint-restore-check-run …
   --checkpoint-verified …`. At the block the runner stops HB-S4 slices and reports `checkpoint_due`.

Arming checks before the owner path opens: the stages, a whole-month W, the exact User-Agent P2 builds from the contact
e-mail, this host, the running frozen version tuple (A3), the budgets (HB-Q8 defaults unless given), D-HB3-1 for HB-S2,
the pilot sample for HB-S3, the checkpoint and block for HB-S4, `--confirm-stop-conditions`, a G-1 reference and a note.
`disarm` stops every Phase 2 request at the next request boundary.

## 6. Backups and checkpoints (design section 21)

P1 is unchanged (nightly dumps, off-site sync, weekly restore checks). Before HB-S4, after every 2,000 documents and at
the end, the owner runs a manual dump and a restore check, runs `cse-financial verify` and `cse-backfill verify` on the
restored copy, and records the checkpoint **in the next HB-S4 arming decision** (the P1 backup-ledger run ids). The
worker cannot read `ops.backup_runs`, so backup state controls Phase 2 only through the owner's arming (MA §49).

**The checkpoint's restored copy: BLOCKED (2026-10-23).** Do **not** use `P1_PLATFORM.md` §8 B, step 5 for it. That
step is disaster recovery: it restores the dump **into the production cluster**, and it must never be used to create the
throwaway checkpoint copy. P1's `restore_check` (§8 A) does not provide the copy either: it stops its throwaway cluster
when it finishes (`--keep` keeps only the stopped data directory), and its private socket uses `trust` authentication;
`--keep` does not satisfy the authentication condition below. `cse-financial` and `cse-backfill` load `/etc/cse/cse.env`
themselves, so connection settings exported before them do not reliably point them at another cluster. **The R-F4
checkpoint procedure below is blocked** until a separately reviewed procedure exists that:
- restores the checkpoint dump into an isolated PostgreSQL 17 cluster, never the production cluster;
- keeps that copy available for verification;
- configures appropriate authentication for it;
- ensures that every checkpoint verification command connects to that copy, not to production.

On a build that includes R-F4, the checkpoint also runs R-F4's two checks **on the restored copy**, in the order of
section 12, steps 2–5. Each command is pointed at the restored copy, not at production: `DATABASE_URL` for a reader
login, or `CSE_DB_HOST`, `CSE_DB_PORT` and `CSE_DB_NAME` for `--migration-role`.
1. **The R-F4 preflight**, by a route that can read the restored copy's migration ledger (section 12, step 2).
   Continue only on exit 0 with `"clean": true`.
2. **Then the full R-F4 `verify`** (no `--limit`), read-only, as the reader role:
   `python -m worker.financial_f4_store verify`. Continue only on exit 0 with `"clean": true` and
   `"scope": {"complete": true, …}`. Exit 0 means every extraction's seals and every F5 rebuild hold
   (`docs/R_F4_DESIGN.md` §5, §10).

The preflight comes first because `verify` reads only what its role can see: row-level security on a table it reads
can hide evidence from it, and the preflight refuses that configuration (`docs/R_F4_DESIGN.md` §5). A limited scan, an
unverifiable ledger or any nonzero exit does not pass the checkpoint: record the checkpoint in the next HB-S4 arming
decision only after both checks are clean.

## 7. The pilot sample and report

**The sample** (`hb.pilot.1`) is drawn from the in-window filings whose document F2 accepts. It is stratified by what is
known **before** any retrieval:
- the Colombo upload year;
- the document type F1's listing bucket or the CSE title states (annual, interim, errata, other);
- the template hint: `bank_like` when CSE's issuer name on the filing (else P2's company name) names a bank, finance,
  leasing or credit company, `general` otherwise, `unknown` with no name;
- the issuer link's status and basis;
- listed in the verified security master or not.

Strata are taken round-robin, each in a fixed SHA-256 order, so the same universe gives the same proposal. These are
sampling labels only (never F3's document type, F4's template, a period, or an input to anything). F4 chooses the
template from the document's own statements, so before retrieval only the hint exists; the report compares the two.

**The report** covers section 27's exit criteria over the pilot filings:
- the requests by class and outcome;
- the time per document (first claim to persisted or final);
- the failure rates by final state;
- the database growth (from the runner's wake-ups, plus the current sizes of the relations);
- `companyInfoSummery` and listing completeness;
- the funnel (HB-5's coverage restricted to the pilot);
- the template distribution, and the template hint against F4's actual template.

**E1 (disappeared since F0) is not measurable.** The F0 captures were lost on 2026-10-10. The owner decided on
2026-10-12 to collect a new evidence baseline the governed way later. No pilot acceptance criterion depends on the
lost RDV corpus, and the pilot never substitutes a new capture for it.

## 8. Failures and recovery

| Situation | Runner | Operator / owner |
|---|---|---|
| Block (CSE 401/403/407/451, a 429 beyond its bounds) | every stage stops (exit 3) | owner `acknowledge-block`; also `cse-scheduler disarm` until reviewed; then `resume --item` each blocked item |
| Circuit open three slices running | `stage_stopped` alert; HB-2 refuses that stage | owner reviews; a newer arming decision |
| Cleanup failure | `cleanup_failed` (2); the document stage stops | check the temp root, then `requeue --item document:…` |
| Failed validation | `validation_failed` (2); every reconcile deferred | investigate, then `requeue --item validate:<run>` |
| Failed reconcile pass | `reconcile_failed` (2) | investigate, then `requeue --item reconcile:<issuer>` (or `reconcile:all_issuers`) |
| Late evidence (M4) | `awaiting_requeue` (2) after promotion to `needs_validation` | `late-pass`; then re-queue the listed `validate:` items and the affected `reconcile:` items. Nothing is downloaded again |
| Interrupted wake-up (crash, kill, power) | the next runner expires the dead claim; slices expire dead leases and recover from evidence; F6.4 abandons a stale job at its next reconcile and HB-5 re-validates | none |
| SIGTERM (`systemctl stop`) | the current unit unwinds (F2 deletes and verifies the document; the slice keeps its lease); the wake-up is recorded `stopped` | none |
| PostgreSQL unavailable | exit 4 before any request | P1 runbook |
| Database disk under 20 GiB free, or not checkable | `db_disk` refused (5) before any work, every wake-up | free space, or a capacity decision (design section 24) |
| P1's dump destination with less free space than the database | `dump_disk` (2); work continues | free space before the next dump (HB-Q9) |
| Another runner active | `busy` (0) | none (manual commands that need the claim refuse with 5: retry) |

## 9. systemd

`cse-backfill.timer` only wakes the runner. The service is a hardened `oneshot` as `cse-worker`:
- `PrivateTmp`, with the dedicated temporary root `/tmp/cse-backfill` (0700, created by `ExecStartPre`);
- `ProtectSystem=strict`, with the spool writable;
- the state directory `/var/lib/cse-backfill`;
- `TimeoutStartSec=2h` and `TimeoutStopSec=5min`, so a document can finish or unwind.

## 10. Security model

The worker cannot do any of the following: arm, disarm, acknowledge a block, resolve a hold, designate, write any table
directly, or open a CSE slice outside HB-3 and HB-4. HB-6's static boundary enforces it (no network module, no CSE
client, no direct HB-2 slice, lease, lock or request, F6.4 only through HB-5, no SQL write, no lock literal), as do HB-1's
privileges and guards. Machine-specific values and the contact e-mail live only in `/etc/cse/*.env` on the server.

## 11. Testing

- `tests/test_hb6_unit.py` covers pure rules: the claim SQL, the pilot strata, arming validation, the CLI, the static
  boundary on planted violations, the pins, and the units, wrapper and provisioning text.
- `tests/test_hb6_postgres.py` runs end to end on PostgreSQL 17, with scripted CSE transports. It covers ordering,
  single-runner enforcement, resume and repeated runs, failure and re-queue workflows, M4 without re-download,
  SIGTERM, the gates, the reports and checkpoints, and transaction and lock boundaries.
- `ops/tests/provision_backfill_in_docker.sh` is the clean-server Docker test.
- On a build that includes R-F4: `tests/test_rf4_unit.py` and `tests/test_rf4_postgres.py` cover R-F4's store, reader,
  `verify` and preflight, and its persistence end to end through F5's `run()` and HB-4's document slice (rebuild,
  rollback and retry, idempotency). The results are recorded in `docs/R_F4_IMPLEMENTATION.md`; R-F4 is not yet
  frozen.

Verification at the freeze audit (2026-10-13, on `9f05041`):
- the HB-6 suites: 36 unit and 19 PostgreSQL tests pass;
- 51 of 52 planted faults are caught, including the 15 aimed at the audit's corrections (the template hint and the
  database disk). The survivor is equivalent: HB-1's transition table refuses a resume of a non-blocked item exactly as
  HB-6's own check does;
- the full repository: Linux 2190 passed, with only the known HB-2 clock test failing and 85 environment-only skips;
  Windows 1786 passed, 0 failed;
- the clean-server test above passed, 24 of 24 probes, including the disk record of every wake-up. Its first run, at
  `76350e3`, failed one probe falsely: a pipe into `grep -q` under `pipefail`, where the longer journal output then
  died of SIGPIPE. The probe was fixed in `0c6d9cc`.

## 12. Deployment prerequisites (owner-run; none is an implementation prerequisite)

- The local Ubuntu 24.04 server; native PostgreSQL 17; P1 provisioning (roles, peer authentication, backups and the
  off-site destination); migrations 0001–0017 through P1's runner, or 0001–0018 for a build that includes R-F4.
- **R-F4 (`docs/R_F4_DESIGN.md`, `docs/R_F4_IMPLEMENTATION.md`).** Only for a build that includes R-F4. R-F4 is
  implemented (Stages 1 and 2) on the branch `claude/rf4-implementation` and is **not** yet independently audited after
  Stage 2, accepted, frozen or part of any deployable build; per its release sequence Q1 it is frozen before Docker
  staging. Stage 2 wires F5's `_persist` to it, so such a build **needs** migration 0018: without it every filing's
  save is refused (`SchemaUnavailable`) and recorded as a failure. On such a build the owner runs, **in this order**,
  each step only after the one before it passed:
  1. **Migration 0018**, through P1's runner (the authorized migration process), after 0001–0017.
  2. **The R-F4 preflight**, on a connection that can verify the migration ledger. It checks three things separately:
     - the checked-out 0018 against the code's pin;
     - the migration ledger's 0018 entry and hash;
     - the live objects: tables, columns, constraints, privileges, triggers, and the table properties (row-level
       security, persistence, kind, inheritance) of 0018's tables and of the F3 and F5 tables `verify` reads.

     The ledger must be verified by a route that can read it:
     - the migration role (owner-level):
       `sudo -u cse-migrator env PYTHONPATH=/opt/cse/app CSE_DB_USER=cse_migrator python3 -m worker.financial_f4_store preflight --migration-role`;
     - or, with `DATABASE_URL` set to it, a login that inherits `cse_reader`'s privileges or whose session sets role
       `cse_reader` (none is provisioned yet). A member that does neither gets `unverifiable`, which is not a pass.
  3. **Continue only on exit 0 with `"clean": true`.** Exit 1 is a problem to fix. Exit 2 means the ledger could not
     be read (`"status": "unverifiable"`, as for a `cse_worker` connection, which has no access to `ops`): it is
     **not** a pass. Re-run by one of the routes above. P1's `cse-ops migrate verify` compares the ledger with the
     checked-out files only, not with R-F4's pin.
  4. **The full R-F4 `verify`**: no `--limit`, read-only, as the reader role, with `DATABASE_URL` set to a login that
     inherits `cse_reader`'s privileges or whose session sets role `cse_reader` (none is provisioned yet):
     `python -m worker.financial_f4_store verify`. It comes after the preflight because it reads only what its role
     can see: row-level security on a table it reads can hide evidence from it, and step 2 refuses that
     (`docs/R_F4_DESIGN.md` §5).
  5. **Continue only on exit 0 with `"clean": true` and `"scope": {"complete": true, …}`.** None of the following is
     acceptance:
     - a limited scan (`--limit N` is partial, never clean, and exits 1);
     - an unverifiable ledger in step 3;
     - any nonzero exit.

  The same two checks, in the same order, run on the restored copy at every backup checkpoint (section 6).
- NTP time synchronisation, and approved outbound network access to CSE's API and CDN only.
- P1's private spool and backup root; the dedicated temporary root with at least 464 MB free; Poppler 24.02.0; at least
  20 GiB free on the database disk (the runner refuses below it), and room at the dump destination for the growing dumps.
- `/etc/cse/capture.env` with the owner's contact e-mail (HB-X2(b), G-1 control 4), and `/etc/cse/backfill.env`.
- HB-X2(a) (G-1's extension to bulk discovery and temporary document retrieval).
- HB-P1: a fresh derived P2 security master (allSecurityCode verified, within 7 days), then IE-2 (a P2 sweep on a
  non-trading day).
- The owner's arming decisions (HB-S1 … HB-S4) and the F6.4 designation after the pilot's first runs.
- The backup checkpoints (section 6), and the P1 alert channel (an open P1 decision; `status` shows the alerts).
- The release sequence of MA §43.1: Docker staging, crash / recovery, backup / restore and permissions testing, a
  deployment rehearsal, server provisioning, production smoke tests, then live execution.
- A governed recollection of the RDV and F0 evidence, backed up outside Git (owner decision 2026-10-12).

## 13. Owner decision HB6-1 (2026-10-13)

Design sections 20 and 27 and HB-B8 place the timer units in `ops/backfill/`, but frozen HB-1, HB-2 and HB-5 tests
asserted that the directory does not exist. These were scope guards: "HB-6, not HB-1". The owner approved editing those
three assertions to a durable form: `ops/backfill`, if present, holds only HB-6's two units. The edited tests are
`tests/test_hb1_unit.py`, `tests/test_hb2_unit.py` and `tests/test_hb5_unit.py`; each keeps every other assertion
unchanged.
