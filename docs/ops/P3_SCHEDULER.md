# P3: Capture scheduler, catch-up and production operations

**Status: implementation for review.** P3 makes the frozen P2 capture pipeline run reliably on the intermittently-online Linux server. It does **not** perform the first production capture: that is a separate, owner-controlled operation after P3 is accepted (§15).

> **G-1 is an owner-accepted risk, not CSE authorization** ([docs/governance/G-1_CSE_DATA_USE.md](../governance/G-1_CSE_DATA_USE.md)).
> - No CSE permission or licence exists, and nothing here may claim one.
> - Every mandatory control in that record applies. The scheduler adds automation; it relaxes nothing.
> - Raw CSE responses are private. Never redistribute them and never commit them to Git.

---

## 1. What P3 adds, and what it reuses

P3 is **not** a new capture implementation. Every CSE request is still made by P2's code, unchanged:
- one request at a time, at least 1.5 s apart;
- bounded backoff and the circuit breaker;
- stop on blocks;
- the identifiable User-Agent;
- spool first, then PostgreSQL.

| Need | Reused as is | Added by P3 |
|---|---|---|
| Per-attempt state | P2 runs + trigger-enforced events (0012) | A **work item** per Colombo trading date that references P2 runs |
| Retry without duplicate evidence | P2 `resume` (same run id, so the same Stage E `request_attempt_id`) and `reprocess` (archive only) | Deciding **when** to resume or reprocess, with back-off and limits |
| One capture process at a time | P2's session-level global advisory lock, freed by PostgreSQL when a process dies | Held for a **whole wake-up**, plus a persisted lease with a heartbeat |
| Dead-process detection | P2 `recover` (spool ingestion + `mark_abandoned`) | Run at every wake-up; dead leases expired |
| Explicit Colombo trading date and session proof | P2's fixed +05:30 date and E1 evidence | Which dates are due, and when |
| Calendar | `trading_calendar` (0001: three-state, empirical) | Its first writer: `open` from live capture, `closed` only from an operator's CSE notice |
| Backups | P1's dump / off-site / restore units and ledger | A path unit that makes a dump eligible after each capture (asynchronous) |

```
systemd timer  (boot + every 15 min: a wake-up, never a schedule)
  -> python -m worker.scheduler run  (cse-worker -> cse_worker, peer)
       -> PostgreSQL decides what is due  (market_schedule_*: owner settings, items, history, leases)
            -> P2 capture / resume / reprocess  (worker.market_capture, frozen)
                 -> P2 archive, completeness, reconciliation (frozen Stage E)
                      -> item state (consistent with P2 evidence, trigger-checked)
                           -> marker -> P1 dump (asynchronous; never part of capture success)
```

## 2. Files

| Path | Purpose |
|---|---|
| `supabase/migrations/0014_market_capture_scheduler.sql` | Settings (owner-only), work items, item events (guarded against P2 evidence), wake-up leases, a state view |
| `worker/scheduler/schedule.py` | Pure rules: settings and bounds, Colombo dates, windows, candidate days, back-off, stop conditions |
| `worker/scheduler/planner.py` | Pure decisions: what P2's runs prove about a date, and what to do now |
| `worker/scheduler/store.py` | Database access: settings (and the owner path), leases, items, events, P2 evidence (read-only), calendar |
| `worker/scheduler/preflight.py` | Security preflight of the scheduler role (P2's checks plus the 0014 tables) |
| `worker/scheduler/wakeup.py` | One wake-up (discover, reconcile, capture) and the operator actions |
| `worker/scheduler/cli.py`, `__main__.py`, `__init__.py` | Command line |
| `ops/bin/cse-scheduler` | Operator wrapper: `cse-worker` for every command, `cse-migrator` for `arm` / `disarm` only |
| `ops/scheduler/cse-capture-scheduler.{service,timer}` | The wake-up unit and its timer |
| `ops/scheduler/cse-capture-backup-trigger.path` | After a capture, starts P1's `cse-backup-dump.service` asynchronously |
| `ops/provision/provision_scheduler.sh` | P3 provisioning step, run after P1's `provision.sh`. It never arms |
| `ops/tests/provision_p3_in_docker.sh`, `ops/tests/p3_container_probes.sh` | Clean Ubuntu 24.04 **with systemd** test, offline |
| `tests/test_p3_scheduler_unit.py`, `tests/test_p3_scheduler_postgres.py`, `tests/p3_fakes.py` | Tests (§17) |

No P1, P2, Stage E or F1–F6 file is modified. A unit test pins every frozen P1/P2 production file.

## 3. Persisted state (migration 0014)

| Table | Contents | Worker privileges |
|---|---|---|
| `market_schedule_settings` | One row per **owner decision** (arm, re-arm, disarm). The highest id is in force; **no row means disarmed**. An armed row records the release gate: exact User-Agent, host, first trading date, daily request budget, expected request count, the five stop conditions, note, approver. | **SELECT only** |
| `market_schedule_items` | **One row per (work kind, Colombo trading date), unique.** Due time, window close, discovery time, origin (`scheduled` / `catch_up` / `operator`), the operator's reason, and the settings snapshot it was created under. Immutable. | SELECT, INSERT |
| `market_schedule_item_events` | Append-only state history per item: state, action, the **P2 run it concerns**, the scheduler's clock, reason, details (attempt number, completeness A–E, session evidence, stop reason), wake-up id. | SELECT, INSERT |
| `market_schedule_wakeups` | One row per wake-up: the **lease** (holder host / pid / boot id, `heartbeat_at`), result, details. `active` → `released` / `expired`; `skipped` for wake-ups that found the lock held. Finished rows are immutable; nothing is ever deleted. | SELECT, INSERT, UPDATE (heartbeat / release only; guarded) |
| `market_schedule_item_state` (view) | The latest event per item | SELECT |

**Everything a work item must establish is persisted:**

| Required fact | Where |
|---|---|
| trading date | `items.trading_date` |
| when it became due | `items.due_at` |
| when the scheduler discovered it | `items.discovered_at` |
| when execution started / ended | `running` events (`action` start/resume) and the outcome events after them; P2's own run events |
| associated capture run | `item_events.run_id` |
| catch-up vs normal | `items.origin` |
| retry / recovery state | attempt number in each `running` event; back-off is deterministic from them (§6) |
| failure / block reason | outcome event `reason` and `details.stop` |
| successful completion already exists | final `succeeded` event naming a P2 run that is itself `succeeded` |

**Idempotency is database-backed.** The unique `(work_kind, trading_date)` key makes duplicate work impossible, whatever the cause:
- the timer firing twice;
- a scheduler restart;
- two scheduler processes;
- a reboot;
- an operator command.

## 4. Item state machine

States use the project vocabulary: `pending`, `running`, `succeeded`, `partial`, `failed`, `missed`, `blocked`, `abandoned`, `not_applicable`.

```
(none) -> pending -> running -> succeeded                                  (final)
                  \          -> partial | failed | blocked | abandoned -> running (resume / fresh snapshot)
                   \                     \-> finalize (window closed; partial / blocked stay as captured)
                    -> missed            (window closed, nothing of the date archived; final)
                    -> not_applicable    (declared closure, or no session proven twice; final)
running -> pending   only if a start created no P2 run (so no request was made)
```

**The guard trigger keeps the history consistent with P2's evidence.** An item event is refused when:
- a run-derived state (`succeeded`, `partial`, `failed`, `blocked`, `missed`, or `abandoned` with a run) names no run, or names a run whose **own** P2 state differs;
- it names a run of another date or mode;
- it is `missed` or `not_applicable` once any run of the date **captured the date's own session**: observations were derived from it, or P2 recorded E1 = true in its events. A snapshot showing *another* session, such as a holiday or stale data, proves neither;
- it follows a final state (`succeeded`, `missed`, `not_applicable`).

The scheduler therefore cannot claim a success, a miss or a non-trading day that P2's evidence contradicts.

**Capture state and canonicalisation are separate.** The item's state follows the P2 run's state, which depends only on dimensions A, B, C and E1. Canonicalisation (D) and reconciliation/validation (E) are recorded beside it in `details.completeness`. They never change or promote the state.

## 5. Trading-date semantics

- **A trading date is a Colombo date** (fixed UTC+05:30; no daylight saving since 2006), exactly as P2.
  - The machine's UTC calendar date is **never** used.
  - Example: at 20:00 UTC on Monday it is 01:30 on Tuesday in Colombo. Monday's window has closed, and Tuesday is "today".
- **Candidate days are Monday–Friday.** No holiday list is computed or invented: CSE closures (Poya and other holidays) follow no computable rule.
- **Known non-trading days** are `trading_calendar` rows with `closed` / `cse_notice`. The operator enters them from CSE's own published notice with `declare-closed`, and the reference is kept.
  - The scheduler never writes `closed` by itself.
  - A declaration is refused if live capture already proved a session on that date.
- **Unknown closures are proven by evidence.**
  - A snapshot taken on the date whose latest trade is on an **earlier** date is no-session evidence.
  - After `no_session_confirmations` (default 2) such snapshots, the item is `not_applicable`. Each costs 2 requests, and the calendar stays `unknown`.
  - A snapshot showing a **later** session is an anomaly (the clock?): no automatic retry, and an alert.
- **`open` / `live_capture`** is written to `trading_calendar` whenever P2's E1 proves the date's own session. If a `closed` declaration already exists, the contradiction is flagged and nothing is overwritten.
- **Explicit operator dates:**
  - `add --trading-date D --reason …` creates (or finds) the item for D. Use it for a special session, or to override a wrong declaration for a date still in its window.
  - `retry --trading-date D --reason …` makes an immediate, fully gated attempt.
- **The window.** A date's capture is due at `earliest_start_local` (default **15:15**, 45 minutes after CSE's 14:30 close) and closes at `window_close_local` (default **23:59:59**), both on the date itself.
  - CSE's `tradeSummary` serves only the latest session, so a date can only be captured inside its window.
  - P2's E1 check refuses any snapshot of another session anyway.

## 6. Catch-up and downtime

Every wake-up does the same deterministic work under the global lock:

1. **Discover.** Create the items for every weekday from the armed `start_date` to today (Colombo) that has none.
   - An item is `scheduled` if created before it became due, otherwise `catch_up`.
   - If the gap reaches back more than `max_catch_up_days` (default 14), the wake-up **refuses** and records nothing, because the clock may have jumped. The operator checks the clock and confirms once with `run --confirm-catch-up-through <today>`.
2. **Reconcile, oldest date first, with no CSE request:**
   - record what P2's runs prove, adopting manual runs and runs P2 marked abandoned;
   - declared closure → `not_applicable`;
   - window closed, nothing of the date archived → a P2 **missed record** (made by the scheduler, `basis=scheduler`) and `missed`;
   - window closed, the date's session archived but the run died → P2 `reprocess` (archive only), then `finalize`;
   - window closed on a partial or blocked capture → `finalize` (closed once; never re-requested).
3. **Capture: at most ONE capture action per wake-up.** It goes to the oldest item that is due, inside its window, not backing off, and has attempts left:
   - `resume` of the item's run (the same run id);
   - or a **new** P2 run when there is none, or when a fresh snapshot is needed to confirm no-session evidence.

**Retry policy (persisted in settings, snapshotted on each item).**
- Back-off after the n-th capture action is `retry_base_minutes × 2^(n−1)` (default 20 → 40 → 80 → 120, capped at `retry_max_minutes`).
- Automatic attempts are capped at `max_attempts` (default 4).
- `retry` skips only the back-off, up to a hard maximum of 8 actions per item.

**A server that was off for days** (e.g. Monday–Wednesday, back Thursday 16:00):
- Monday, Tuesday and Wednesday become `missed`, in date order, with no CSE contact. Their snapshots are no longer served.
- Thursday is captured as `catch_up`: one capture, about 55–65 requests.
- Each date keeps its own identity: nothing is relabelled and nothing pretends a past date occurred.

**Request budget (G-1).**
- `daily_request_budget` (default 150, equal to P2's per-run cap) is checked before every capture action. It counts every request archived that Colombo day by **any** run, scheduler or manual.
- A new run needs at least 10 remaining, and is capped at what remains.
- A resume needs its worst case, which is what the run still lacks × its attempt limit.
- Otherwise the item is **deferred**, not burst. Downtime therefore never multiplies the normal envelope.

## 7. Stale-run recovery, leases and heartbeats

- **The lock is the guarantee.** P2's global advisory lock (session-level) is held on the scheduler's `ctl` connection for the whole wake-up. PostgreSQL releases it when the process or its connection dies.
- **The lease is the evidence.** Each wake-up that gets the lock records an `active` lease and refreshes `heartbeat_at` on a separate connection, at every P2 progress line and between items. It releases the lease at the end.
- **A process died:**
  - The next wake-up gets the lock and expires the old lease (`expired_by`).
  - P2 `recover` marks the dead `running` run `abandoned` and ingests anything that was spooled but not committed.
  - The item is resumed under the **same run id**. Archived requests are never repeated, and nothing is deleted.
- **The lock is held and the heartbeat is old** (older than `stale_lease_minutes`, default 30):
  - The wake-up records `skipped` / `stale_lease` and exits 2.
  - It **never takes over**: while the lock is held, the holder may still be alive and mid-request, and a takeover could make two parallel CSE requests.
  - systemd's `TimeoutStartSec=2h` kills a hung wake-up. Its lock is then freed and the case above applies.
- **The lock is held and the heartbeat is fresh**, or a manual P2 capture holds it: `skipped` / `busy`, exit 0.
- **The window closed before recovery:** the run is re-derived from the archive, never re-requested (§6).

## 8. Concurrency

- **Two schedulers, a duplicate timer firing, a restart, or a scheduler during a manual `cse-capture` command:** exactly one process holds the global lock. The others record `skipped` (or P2 refuses) and make no request.
- **A manual P2 capture that succeeded for a date** is adopted by the date's item. It is never captured again.
- **Duplicate item creation** is impossible: the unique key, plus `ON CONFLICT DO NOTHING` inside one transaction.
- **Isolation:** no filesystem lock is relied on, and the worker is never given owner privileges.

## 9. systemd

| Unit | What it does |
|---|---|
| `cse-capture-scheduler.timer` | `OnBootSec=3min` + `OnCalendar=*-*-* *:00/15:00`. **No business schedule** (no weekdays, times or dates). `Persistent=` is deliberately not relied on: a firing missed while the server was off is caught up by the next wake-up from PostgreSQL state. |
| `cse-capture-scheduler.service` | `Type=oneshot`, **`User=cse-worker`** (peer → `cse_worker`), `/etc/cse/cse.env` + `/etc/cse/capture.env`, `TimeoutStartSec=2h`, `StateDirectory=cse-scheduler`. Hardened: `NoNewPrivileges`, `ProtectSystem=strict` with **only the spool** writable, `CapabilityBoundingSet=` empty, `PrivateTmp`, `PrivateDevices`, `ProtectClock`, `RestrictNamespaces`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. Exit codes 2–5 leave the unit failed, so they appear in `systemctl --failed`. |
| `cse-capture-backup-trigger.path` | `PathChanged=/var/lib/cse-scheduler/capture-finished` → `cse-backup-dump.service` (P1, unchanged) |

The unit files live in `ops/scheduler/`, not P1's `ops/systemd/`, which stays backup-only (P1's frozen boundary, which a P1 test enforces). `provision_scheduler.sh units` renders them with the configured paths, runs `systemd-analyze verify`, and enables the timer and the path unit.

**Installing or enabling the timer contacts nobody.** Until the owner records `arm`, every wake-up only does P2 hygiene (spool recovery, abandoned runs) and returns `disarmed`.

**Stopping capture:**
- `disarm` stops new capture actions from the next wake-up on.
- A capture already in flight finishes, usually within a few minutes.
- To interrupt one at once, run `systemctl stop cse-capture-scheduler.service`. The run is then `abandoned`, and while the scheduler is disarmed it is never resumed.

```bash
sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision_scheduler.sh all
```

```bash
systemctl list-timers cse-capture-scheduler.timer
```

## 10. Operator commands

Run every command as `sudo bash /opt/cse/app/ops/bin/cse-scheduler <command> …`. The report is JSON on stdout.

| Command | Contacts CSE | What it does |
|---|---|---|
| `status [--days N]` | no | Settings (armed?), lease and recent wake-ups, G-1 blocks, today's requests vs budget, open items, alerts |
| `list [--state S …] [--from D] [--to D]` | no | Items by state: `pending`, `missed`, `failed`, `blocked`, `abandoned`, `partial`, … |
| `show --trading-date D` | no | One item, its history, its P2 runs with completeness A–E, the calendar row |
| `retry --trading-date D --reason TEXT` | only via P2 | An **immediate** attempt for one due item. It skips the back-off only; window, budget, G-1 gate, arming and host still apply. A final item is never recaptured |
| `add --trading-date D --reason TEXT` | no | Create (or find) the work item for one date, justified (at most 14 days ahead) |
| `reprocess --trading-date D` | no | Re-derive from the archive (P2 `reprocess`), e.g. after the D-2 fix. Allowed while disarmed |
| `declare-closed --trading-date D --reference TEXT` | no | A known closure from CSE's notice (refused if a session was proven) |
| `calendar --from D --to D`, `settings`, `verify` | no | Read-only / security preflight |
| `run [--confirm-catch-up-through D]` | only via P2 | One wake-up by hand (what the timer runs) |
| **`arm …`**, **`disarm --note …`** | no | **Owner decisions** (§11) |

There is deliberately **no** delete, reset, purge or "rerun everything" command. P2's owner-only purge design (P2 runbook §12) is unchanged.

**Exit status:**

| Code | Meaning |
|---|---|
| 0 | OK / busy / disarmed |
| 2 | Needs attention: a missed date, a finalized partial, a stale lease, a deferral, attempts used up, an anomaly or contradiction |
| 3 | An unacknowledged G-1 block stops all capture |
| 4 | PostgreSQL unavailable |
| 5 | Refused: security preflight, host, clock, User-Agent, catch-up gap, or a refused operator action |

## 11. Security model

| Role | P3 tables |
|---|---|
| `cse_worker` (scheduler and capture) | Settings: **SELECT only**. Items and events: SELECT + INSERT. Wake-ups: SELECT + INSERT + UPDATE (heartbeat and release; a trigger freezes identity and finished rows). No DELETE or TRUNCATE anywhere. It never owns anything, is never a member of `cse_owner` / `cse_migrator` / `cse_backup`, and is never a superuser |
| `cse_migrator` (P1's owner-delegation login, root via sudo only) | Nothing of its own. For `arm` / `disarm` only, it acts as `cse_owner` through `SET LOCAL ROLE` for one INSERT, and reads the current settings the same way in a rolled-back transaction |
| `cse_owner` | Owns everything. Append-only triggers stop even the owner |
| `cse_reader` | SELECT |
| `cse_backup` | Reads via `pg_read_all_data`; no write |
| PUBLIC | Nothing |

**Arming is refused to the worker three ways, as P2's block acknowledgement is:**
1. no INSERT privilege;
2. no membership in `cse_owner`;
3. a guard trigger that refuses any service-role `session_user`, even after a mistaken grant.

**Other boundaries:**
- The scheduler never records or bypasses a G-1 acknowledgement: `acknowledge-block` stays P2's owner path.
- There is no SECURITY DEFINER function, no new role and no network listener. PostgreSQL stays socket-only with peer authentication.
- **Preflight before every wake-up.** It runs P2's worker checks plus the 0014 privileges and triggers. A mistaken grant, a disabled guard trigger, or a missing 0014 refuses the whole wake-up (exit 5) before any request.

**Migration review of 0014:**
- additive only: four tables, a view, three trigger functions, grants;
- no ALTER or DROP of anything existing, no binary column type, no document columns;
- PUBLIC gets nothing, and no DELETE or TRUNCATE is granted to anyone;
- append-only triggers on settings, items and events; a guard on wake-ups;
- the P1 verifier still passes (no FAIL or WARN);
- its hash is recorded by the P1 runner.

## 12. Failure semantics

| Failure | What happens |
|---|---|
| Network / 5xx / timeouts during a capture | P2 retries within its bounds; the item is `failed` or `partial`; the scheduler resumes the **same run** after back-off, within the window |
| CSE block (401/403/407/451) or persistent 429 | P2 stops and records `blocked`. **Every** later capture is gated (exit 3) until the owner records `cse-capture acknowledge-block`. Then the scheduler resumes the same run if the window is open |
| Circuit breaker, per-run request cap | The run ends `partial`; attempts and the daily budget still apply |
| Daily budget reached | Deferred, not retried in a burst (exit 2) |
| Process killed / power loss mid-capture | Lease expired, run `abandoned`, resumed under the same id; spooled attempts ingested first |
| PostgreSQL unavailable | Exit 4; the spool keeps any response; the next wake-up recovers |
| Clock not NTP-synchronised, clock moved back > 5 min, wrong host, User-Agent differs from the approved one, spool not writable | Refused (exit 5) before any request. A User-Agent or spool problem still allows the no-CSE bookkeeping |
| No session on the date | Two fresh snapshots on the date, then `not_applicable`; nothing derived; calendar stays `unknown` |
| Window closed | `missed` (nothing archived), `reprocess` + `finalize` (session archived), or `finalize` (partial / blocked) |
| Canonicalisation failure (e.g. D-2) | Recorded as D failed per security. The capture state is unchanged and never promoted, and the scheduler never recaptures to "fix" it (§14) |

## 13. Capture, canonicalisation, validation and backups

- **Capture success depends only on P2's A, B, C and E1.** It never depends on canonicalisation (D), reconciliation/validation (E) or backups (F).
- **Backups become eligible after capture, asynchronously.** After every capture action, the scheduler atomically rewrites `/var/lib/cse-scheduler/capture-finished`. The path unit then starts P1's dump unit.
  - The dump records its own outcome in `ops.backup_runs`.
  - The nightly dump, hourly off-site sync and weekly restore check run regardless.
  - The scheduler never reads or writes the backup ledger. P2's `protection` command (as `cse-backup`) reports dimension F read-only.
- **A failed or missing backup never changes a capture or an item.**

## 14. D-2 (frozen Stage E defect): impact on scheduling

- **The defect.** `db.upsert_daily_market_data` serialises provenance with plain `json.dumps`, and `reconcile` copies database `Decimal` values into it when a date has **more than one observation that disagree**. That covers post_open observations, and any second post_close observation with different values. The canonical upsert then raises `TypeError`.
- **How P3 avoids triggering it.** It schedules post_close only, and never creates a second deriving run for a date:
  - retries **resume** the same run (same `request_attempt_id`, so there is no second observation);
  - new runs are only started when no session was derived (no-session confirmation).
- **Where it can still appear:** a manual post_open or second post_close capture of the same date, outside the scheduler.
- **When it occurs:**
  - the canonical write fails per security with that reason (D);
  - the capture stays `succeeded` / `partial` (A–C) and the archive stays intact (`verify-archive` clean);
  - the item records `D.failed` and `failed_reasons`;
  - nothing is recaptured, rewritten or promoted.
- **Recovery.** After a separately gated D-2 fix, `reprocess --trading-date D` fills the canonical rows from the archive.
- `test_frozen_d2_canonicalisation_failure_is_recorded_never_promoted_and_archive_intact` reproduces it with the real frozen code.

## 15. First production capture: prerequisites (release gate)

P3 makes the system **ready**. The first real CSE capture happens only after the owner explicitly establishes every item below and records them with `arm`, which runs through the owner path on the production host:

| Prerequisite | How it is established |
|---|---|
| Contact e-mail | Set by the owner in `/etc/cse/capture.env` (`CSE_CAPTURE_CONTACT_EMAIL`). It is **empty** after provisioning; nothing invents or derives one |
| Exact User-Agent | `--user-agent` must equal exactly what P2 sends for that e-mail. Every wake-up re-checks the configured User-Agent against the approved one |
| Intended first trading date | `--start-date` (today or later, Colombo). The scheduler never claims earlier dates |
| Intended capture mode | `daily_post_close` (the only work kind) |
| Request budget | `--daily-request-budget` (default 150) |
| Expected request count | `--expected-requests` (e.g. `55-65`: allSecurityCode + tradeSummary + absent fallbacks + 10 cross-checks) |
| Production host | `--host` must equal this machine's hostname; any other host refuses to act |
| Stop conditions | `--confirm-stop-conditions` records the five conditions (`worker/scheduler/schedule.py` `STOP_CONDITIONS`) |

```bash
sudo bash /opt/cse/app/ops/bin/cse-scheduler arm --start-date YYYY-MM-DD \
  --user-agent "cse-analysis-capture/p2.capture.1 (personal non-commercial research; contact: <e-mail>)" \
  --host "$(hostname)" --expected-requests 55-65 --daily-request-budget 150 \
  --note "release gate: <who approved, when, reference>" --confirm-stop-conditions
```

- `disarm --note …` stops all automated capture at once, for example when CSE requests cessation (G-1 §5).
- Disarming does not stop the manual P2 commands. After a cessation request, do not run them either.

## 16. Known limitations and owner decisions

- **Late capture beyond the date itself is not enabled.** CSE keeps serving Friday's snapshot until Monday's session opens, but the P0.5 roadmap deferred it to M7 pending probe evidence. A server off during a whole window records `missed`.
- **No finality re-polling.** A `post_close` snapshot whose closing prices are not all published (E2) is kept and warned about. A second capture of that date would also run into D-2. The default earliest start of 15:15 is meant to avoid the case. Changing it is an owner decision.
- **The weekly metadata sweep stays manual** (P2 `sweep`, about 330 requests). Automating it is an owner decision.
- **The session values need owner confirmation:** 15:15 start, 23:59:59 window close, 20/120-minute back-off, 4 attempts, 2 no-session confirmations, 150 requests a day. These are the P0.5 proposals (B-7) and they are recorded at arming.
- **The owner path** reuses P1's migration login for `arm` / `disarm`, as P2 did for `acknowledge-block`. Confirm this, or use the `postgres` superuser.
- **Alerts** surface as failed units (`systemctl --failed`), the journal and `status`. There is no e-mail or push delivery (P1 open decision).
- **Not wired into P1's frozen `verify_server` list.** The P3 tables are checked by the scheduler preflight (`cse-scheduler verify`) and the tests.
- **The clock check needs `timedatectl`.** Without NTP synchronisation an armed scheduler refuses to act.

## 17. Testing

```bash
pytest tests/test_p3_scheduler_unit.py
```

```bash
P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p3_scheduler_postgres.py
```

```bash
bash ops/tests/provision_p3_in_docker.sh
```

- **Unit tests** run anywhere: no database, no network, no sleeping.
- **PostgreSQL tests:**
  - a real PostgreSQL 17 cluster, with a fresh migrated database per test;
  - P2 unchanged;
  - a fake CSE transport built from the real fixtures that serves the latest session as of a fake clock.
- **The container test:**
  - provisions a fresh Ubuntu 24.04 **with systemd** (P1, then P3);
  - **disconnects the network before any scheduler unit exists**;
  - checks the units, the timer firing, the disarmed wake-ups, the role boundaries, the P1 verifier, the restore check and the backup trigger.
- No test contacts CSE.
