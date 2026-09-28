# P2: Market capture and raw-response archive

**Status: implementation for review. This is not production scheduling, go-live or the first production capture — those belong to P3.**

P2 adds a persistent, auditable CSE market-data capture layer on top of the frozen Stage E code. You run it by hand; there is no capture timer.

> **G-1 is an owner-accepted risk, not CSE authorization** ([docs/governance/G-1_CSE_DATA_USE.md](../governance/G-1_CSE_DATA_USE.md)).
> - No CSE permission or licence exists.
> - Every mandatory control in that record applies to every command below that contacts CSE.
> - Raw CSE responses are private. Never redistribute them and never commit them to Git.

---

## 1. Boundary: what P2 owns, what Stage E keeps

```
P2 wrapper (worker/market_capture)        explicit trading date, run identity + state, request plan & sequencing,
    |                                     G-1 throttle/backoff/stop, retry policy, completeness, resume/recovery
    v
CSE request  (one at a time, >= 1.5 s apart, identifiable User-Agent, no proxies/cookies/redirects)
    v
exact response archive   journal intent -> spool (fsync, atomic, SHA-256 verified) -> PostgreSQL COMMIT
    v
FROZEN Stage E, unchanged                 mapping.map_* / build_raw_observation -> db.insert_raw_observation ->
    |                                     db.get_raw_observations_for_date -> reconciliation.reconcile ->
    |                                     db.get_previous_close -> validation.validate -> db.upsert_daily_market_data
    v
completeness / evidence (dimensions A-E; F read-only from P1's backup ledger)
```

| Frozen Stage E function | How P2 uses it |
|---|---|
| `cse_client.BASE_URL`, and the endpoint, method and body of `get_all_security_codes` / `get_trade_summary_all` / `get_company_info_summary` | Reused exactly. A test reads the frozen source and compares. |
| `cse_client.extract_symbol_list_from_all_security_codes` / `extract_symbol_row_from_trade_summary` | Reused to locate the universe and tradeSummary rows. |
| `mapping.map_company_info_summary`, `map_trade_summary_row`, `build_raw_observation` | Called unchanged. `map_company_info_summary(None)` is used when a traded security needs no companyInfoSummery, and `build_raw_observation` already falls back to tradeSummary. |
| `db.insert_raw_observation` | Called with `request_attempt_id = run id`, so a resume or reprocess never duplicates a row. |
| `db.get_raw_observations_for_date`, `reconciliation.reconcile`, `db.get_previous_close`, `validation.validate`, `db.upsert_daily_market_data` | Exactly Stage E's `_process_symbol` path. |
| `capture_multiple_companies.DEFAULT_TOLERANCES` | Fallback only. Tolerances are read from `system_config`, which 0001 seeded with identical values. |

**Not used:** Stage E's `fetch_and_map` / `capture_*.py` request path. It keeps only parsed JSON (not the exact bytes) and requests `companyInfoSummery` for every security, about 330 requests a day instead of the P0.5 plan's 55–65. It also defaults the observation date to the UTC date (defect D-1, §13).

No frozen file is modified. A unit test pins the SHA-256 of every Stage E module P2 relies on.

## 2. Files

| Path | Purpose |
|---|---|
| `supabase/migrations/0012_market_capture_archive.sql` | Run ledger, events, block acknowledgements, bodies, attempts and per-security results. All append-only. |
| `supabase/migrations/0013_market_capture_owner_acknowledgement.sql` | Review fix: block acknowledgements become owner-only. It revokes the worker's INSERT and adds a guard that refuses service-role sessions. |
| `worker/market_capture/config.py` | G-1 request policy (bounded), capture policies, endpoint specs, User-Agent. |
| `worker/market_capture/http.py` | Transport (exact bytes, no proxies or cookies, no redirects), throttle, classification, retry and backoff. |
| `worker/market_capture/archive.py` | Journal, then spool, then PostgreSQL archive; spool recovery. |
| `worker/market_capture/runs.py` | Run state, global capture lock, abandoned detection, G-1 block gate, security preflight. |
| `worker/market_capture/derive.py` | Derivation from archived bytes through frozen Stage E; session evidence; plan. |
| `worker/market_capture/completeness.py` | Dimensions A–F and the run-state decision. |
| `worker/market_capture/capture.py`, `cli.py`, `__main__.py` | Orchestration and command line. |
| `ops/bin/cse-capture` | Operator wrapper: runs as `cse-worker` (or read-only as `cse-backup` for `protection`). |
| `ops/config/capture.env.example` | Template for `/etc/cse/capture.env` (contact e-mail, request spacing). |
| `tests/test_p2_capture_unit.py`, `tests/test_p2_capture_postgres.py`, `tests/p2_fakes.py` | Tests (§15). |

## 3. Commands

Run every command as `sudo bash /opt/cse/app/ops/bin/cse-capture <command> …`. Output is a JSON report on stdout, with progress on stderr.

| Command | Contacts CSE | What it does |
|---|---|---|
| `capture --trading-date YYYY-MM-DD --mode post_close\|post_open` | yes | A new run for that **explicit** Colombo trading date. |
| `sweep --trading-date YYYY-MM-DD` | yes | The weekly metadata sweep (manual in P2). |
| `resume --run-id UUID` | yes | Continues a `partial` / `failed` / `abandoned` run, or a `blocked` one after the owner's acknowledgement. Requests only what the run still lacks. |
| `reprocess --run-id UUID` | **no** | Re-derives raw observations and canonical rows from the archive. |
| `recover` | no | Ingests spooled attempts missing from PostgreSQL and marks dead runs `abandoned`. |
| `status --run-id UUID \| --trading-date YYYY-MM-DD` | no | State history plus dimensions A–E (read-only). |
| `protection --run-id UUID` | no | The same as `status`, run as `cse-backup` so dimension F can be evaluated. |
| `record-missed --trading-date D --mode M --reason TEXT` | no | Records that a capture did not happen in its window. |
| `acknowledge-block --run-id UUID --note TEXT` | no | **Owner** review after CSE blocked or rate-limited a run (G-1). It runs through the owner path, **never as the capture worker** (§9). |
| `verify-archive --run-id UUID` | no | Recomputes every body's SHA-256 from PostgreSQL **and** from the spool. |
| `export-company-info --run-id UUID --out FILE` | no | Exports companyInfoSummery bodies in F5 `link_issuers --company-info-json` format. Refuses paths inside the repository. |

**The trading date is always an explicit argument.**
- There is no default, and nothing derives it from a clock.
- A date after today's Colombo date is refused before any request. The fixed offset is +05:30; Asia/Colombo has had no daylight saving since 2006.
- The snapshot must prove it belongs to that date (§8, E1). Otherwise nothing is derived.

**Modes** are the frozen Stage E windows `post_open` and `post_close`. P2 records the mode on the run and on every request. It does not define market close.

**Exit status:**

| Code | Meaning |
|---|---|
| 0 | `succeeded`, or the command completed |
| 2 | `partial` |
| 1 | `failed` |
| 3 | `blocked` |
| 4 | PostgreSQL unavailable. The spool holds the data; run `recover`. |
| 5 | Refused before any CSE request |

There is deliberately **no purge or delete command** (§12).

## 4. Request policy (G-1 controls enforced in code)

| G-1 control | Enforcement | Test |
|---|---|---|
| 1. Sparse, sequential | The P0.5 minimum plan (below); a non-reentrant lock around every request; a PostgreSQL advisory lock so only one P2 process runs at a time | `test_no_parallel_requests_even_from_threads`, `test_one_capture_process_at_a_time` |
| 2. At least 1.5 s apart | `Throttle`: at least `min_interval` from the **end** of one request to the **start** of the next. Also seeded from the archive's last request, so the gap holds across processes. `RequestPolicy` refuses values below 1.5 s. | `test_requests_are_sequential_spaced_and_identified`, `test_request_spacing_holds_across_processes`, `test_min_interval_can_never_go_below_one_and_a_half_seconds` |
| 3. Backoff on errors and rate limiting | 5xx, network errors, timeouts and unusable bodies back off at 5 s × 2^(n−1), capped at 120 s (ceiling 600 s), within bounded attempts per purpose (at most 5). 429 honours `Retry-After` up to 300 s; a longer request stops the run. **Circuit breaker:** any 5 consecutive non-OK attempts of **any** classification stop the run. That includes non-retryable ones (`http_error`, `unexpected_redirect`, `too_large`), across request keys, and is checked before the "not retried" decision. A block still stops the run at once, and 429 keeps its own `Retry-After` handling; if the 5th failure is a 429, the run ends as rate-limited (`blocked`). | `test_server_error_backoff_is_exponential_and_bounded`, `test_429_backoff_and_long_retry_after`, `test_circuit_breaker_stops_a_run_that_cannot_reach_cse`, `test_circuit_breaker_counts_non_retryable_http_errors`, `test_circuit_breaker_resets_on_success`, `test_circuit_breaker_stops_a_capture_run_on_non_retryable_errors` |
| 4. Identifiable User-Agent with a contact e-mail | `cse-analysis-capture/p2.capture.1 (personal non-commercial research; contact: <CSE_CAPTURE_CONTACT_EMAIL>)`. Commands that contact CSE refuse to start without a valid address. | `test_user_agent_requires_a_contact_email`, `test_capture_refused_without_contact_email_before_any_connection` |
| 5 and 6. Never bypass; no proxies or IP rotation | `trust_env=False` (environment proxies and `.netrc` ignored), `proxies={}`, no redirects followed. 401/403/407/451 stop the run immediately with no retry. After a block, **every** later capture or resume is refused until the **owner** records `acknowledge-block` (enforced in code and by a database trigger). The capture worker **cannot** record it (§9). | `test_transport_ignores_proxy_environment_and_never_sends_cookies`, `test_403_stops_the_run_at_once_and_gates_every_later_capture`, `test_worker_cannot_acknowledge_a_block_but_the_owner_path_can` |
| No credentials or cookies | The cookie policy stores none, so none is ever sent. `Set-Cookie`, `WWW-Authenticate` and similar headers are removed from the archive; only their names are kept. | `test_sensitive_headers_are_dropped_but_named` |
| 7 and 8. No redistribution; nothing in Git | There is no export except the local F5 file, which refuses repository paths. Bodies live only in PostgreSQL, the spool and encrypted backups. | `test_metadata_sweep_archives_only_and_exports_f5_input` |
| 10. Stop if CSE asks | Blocks stop the run and gate further capture. An explicit cessation request is an owner action: no capture runs without an operator (there are no timers in P2). | — |
| Hard budget | `max_requests` per run: daily 150, sweep 400, ceiling 500. Exhausting it ends the run as `partial`, never `succeeded`. | `test_budget_exhaustion_is_partial_never_succeeded` |

**Default plan (P0.5), configurable within the bounds:**

| Policy | Requests |
|---|---|
| `daily_post_close` | `allSecurityCode` (1) + `tradeSummary` (1) + `companyInfoSummery` for each universe security **absent** from tradeSummary (about 43–52) + a 10-security **cross-check** sample. About 55–65 requests, about 1.5–2 minutes. |
| `daily_post_open` | `allSecurityCode` + `tradeSummary` only. Mid-session, absent securities simply haven't traded. |
| `weekly_metadata_sweep` | `allSecurityCode` + `companyInfoSummery` for every universe security (about 330). **Archive only**: no market observations are derived from it. It is manual in P2. |

- The universe always comes from the archived `allSecurityCode` response. Its size is never an invariant.
- The cross-check sample is deterministic and rotates daily: traded securities in the universe, ordered by `sha256("<date>|<symbol>")`.
- Retries are bounded per purpose: universe and tradeSummary 3 attempts, companyInfoSummery 2.

## 5. The archive

**Order for every attempt (failures included):**
1. A journal intent line, fsync'd.
2. The HTTP request.
3. The exact body bytes go to a spool blob (write-once, fsync, atomic link, read-only), then are read back and SHA-256-verified.
4. A canonical metadata record goes to the spool, followed by a "spooled" journal line.
5. **One PostgreSQL transaction** writes the body (content-addressed) and the attempt row.

Only after every response of the run is archived are raw observations derived, canonical rows reconciled, and the terminal run state written.

**Guarantees:**
- **No request goes out unless the spool is writable.** The intent must be durable first.
- **A spool failure means the response is NOT durably captured.** A `spool_failed` attempt row is recorded without a body, and the run stops.
- **If PostgreSQL fails after the spool write,** the attempt stays in the spool and the run stops (exit 4). `recover` later ingests it with its original values, marked `recovered_from_spool`.
- **Numbers never clash.** `sequence_no` (per run) and `attempt_no` (per request key) continue across resumes and take journal intents into account, so a crash between the spool and the database can't produce a duplicate number.
- **An HTTP success alone never counts as captured.** Only an attempt with outcome `ok` and a committed row does.

**PostgreSQL (migrations 0012 and 0013), all tables append-only:**

| Table | Contents |
|---|---|
| `market_capture_runs` | Immutable run identity: `id` (= the Stage E `request_attempt_id` of every observation the run derives), kind, **explicit** trading date and its basis (`operator`; `scheduler` reserved for P3), mode, full policy snapshot, User-Agent, tool version, code revision, host, OS user. |
| `market_capture_run_events` | The state history (§6). The latest event is the current state. The `market_capture_run_state` view shows it. |
| `market_capture_block_acknowledgements` | The **owner's** review of a blocked run. The note must be at least 10 characters. It is inserted only through the owner path; it records `acknowledged_by` (the login role) and the OS user plus the sudo operator. The capture worker can read it but not insert (0013). |
| `market_response_bodies` | Exact bytes, content-addressed by SHA-256, stored once however often they are received. |
| `market_source_responses` | One row per HTTP attempt: request key and purpose, sequence and attempt numbers, trading date, mode, endpoint, method, URL, params, sanitized request headers, User-Agent, security symbol, requested/observed time, elapsed ms, outcome, HTTP status, sanitized response headers plus the names of removed ones, body SHA-256 and size, whether identical bytes were already archived, parse status, error, spool keys, and `recovered_from_spool`. |
| `market_capture_security_results` | Per security per derivation pass (`capture` / `resume` / `reprocess`): universe and tradeSummary membership, role, cross-check flag, the response ids used, raw status and observation id, canonical status, reconciliation and validation status, reason. |

**Outcomes:** `ok`, `blocked`, `rate_limited`, `server_error`, `http_error`, `unexpected_redirect`, `network_error`, `timeout`, `empty_response`, `invalid_json`, `malformed_response`, `too_large`, `spool_failed`.
- A body is stored whenever bytes were received, including a 403 page, which is evidence.
- A timeout or network error has no body and no status.

**Spool** (P1 `worker.ops.spool`, on the backup disk, `cse-worker:cse-backup 2750`; the hourly P1 off-site sync picks it up):
```
<CSE_BACKUP_ROOT>/spool/blobs/sha256/ab/cd/<sha256>           exact response bytes (0440)
<CSE_BACKUP_ROOT>/spool/records/sha256/ab/cd/<sha256>.json    canonical metadata record of one attempt (0440)
<CSE_BACKUP_ROOT>/spool/journal/<run_id>.jsonl                append-only intent/spooled index of a run (0640)
```

**Body representation: base64 in a `text` column, verified by a CHECK constraint.**
- **Why the F2 guard exists.** The frozen F2 test `test_zero_archive_invariant` forbids the word for PostgreSQL's binary type, and document-path columns, in *every* migration. It enforces the F2 invariant that CSE PDFs/documents are temporary and never archived. It is a deliberately blunt text guard over all migrations.
- **The choice.** P2 does **not** weaken or scope it. A base64 `text` column meets every requirement:
  - byte-for-byte fidelity: base64 is a bijection;
  - no implicit encoding conversion: the value is pure ASCII;
  - exact export: `decode(body_base64,'base64')` or Python `base64.b64decode`;
  - SHA-256 verification: the database recomputes `encode(sha256(decode(body_base64,'base64')),'hex')` and the length in a CHECK on **every insert**.
- **The cost.** About 33 % more storage for a few hundred kB a day, which is negligible.
- **The limit is unchanged.** The archive holds CSE **market-API JSON responses only**, capped at 5 MB each. No PDFs, no documents, no HTML crawling: the F2 rule is untouched.

## 6. Run state machine

```
(none) -> pending -> running -> succeeded                       (final)
                  \          -> partial   -> running (resume / reprocess)
                   \         -> failed    -> running (resume / reprocess)
                    \        -> abandoned -> running (resume / reprocess)   [found dead by the next command]
                     \       -> blocked   -> running ONLY after an OWNER acknowledgement (G-1; owner path, §9)
                      -> missed                                  (final; `record-missed`)
```

- A database trigger enforces the transitions: consecutive sequence numbers, legal transitions only, and `blocked` → `running` only with an acknowledgement.
- **`succeeded`:** an `ok` tradeSummary attempt is committed in the PostgreSQL archive (A), universe known (B), every expected raw observation produced (C), and the snapshot shows the trading date (E1).
- **`partial`:** A is captured, but B is unknown, C is incomplete, or the run was stopped early (budget, circuit breaker or spool failure). **A stopped run is never `succeeded`.**
- **`failed`:** tradeSummary not archived, or E1 false.
- **`blocked`:** CSE refused (401/403/407/451) or kept rate-limiting.
- **`abandoned`:** the process ended without a terminal state. It is detected safely because the session-level global lock proves no P2 process is alive.
- **`missed`:** recorded by the operator.
- **Canonicalisation (D), validation (E) and backups (F) never influence the state.**

## 7. Completeness: separate dimensions, never one boolean

| Dimension | Question | Where it comes from |
|---|---|---|
| **A** Source snapshot | Does the PostgreSQL archive hold a successful (`ok`) tradeSummary attempt for the run? Per-request attempts and last outcome. A checks the **database only**. Every `ok` row is committed only after its bytes were spooled and read back SHA-256-verified, but agreement between the database and the spool is verified **separately, on demand**, by `verify-archive`. | `market_source_responses` |
| **B** Universe | Was allSecurityCode archived? Size, duplicate symbols, members, tradeSummary members, absent securities, tradeSummary-only securities | The archived bodies |
| **C** Raw observations | Expected (the deterministic plan: universe ∪ tradeSummary for post_close, tradeSummary for post_open), produced, and missing with a reason (`source_missing`, `company_missing`, `mapping_failed`, `insert_failed`, `not_derived`) | `market_capture_security_results` checked against the plan, **never** row counts |
| **D** Canonicalisation | Written, or failed with reasons. Can be partial while the run is `succeeded`. | Same |
| **E** Reconciliation/validation | Counts of `single_source` / `agreed` / `discrepancy_flagged` / `pending`, and `ok` / `review_required` | Same |
| **F** Protection | `local_only` → `local_backup` (a dump started after the run finished) → `offsite` (that dump in an off-site snapshot) → `restore_verified` | P1's `ops.backup_runs`, **read-only**, evaluated by the backup role (`protection`). **Never stored in capture state.** |

The reports also give:
- the absent-fallback list and what was archived;
- the cross-check sample and its status;
- every failed request (key, attempt, outcome, HTTP status);
- the session evidence.

**Session evidence** is evidence only; P2 adds no market-close definition:
- **E1:** the latest `lastTradedTime` (epoch ms) falls on the trading date in Colombo. Without it, nothing is derived and the run is `failed`. A snapshot is never labelled with a date it does not show.
- **E2:** no row has `closingPrice` 0.0, which was the pre-publication value in 276/276 mid-session rows. For `post_close` it is reported as a warning only. The values are kept verbatim, and the frozen validation flags them exactly as before.

## 8. Failure and recovery semantics

| Failure | What happens |
|---|---|
| DNS or network failure, timeout | The attempt is archived (no body, no status); bounded backoff and retry; circuit breaker after 5 in a row |
| HTTP 403 (also 401/407/451) | Archived, with the body kept as evidence. The run stops at once (`blocked`); no retry, no alternative path; later capture is gated on the owner's acknowledgement |
| HTTP 429 | Archived. `Retry-After` is honoured up to 300 s; a longer one, or persistent 429s, ends the run as `blocked` |
| HTTP 5xx | Archived; exponential backoff within the attempt bound |
| Malformed response, invalid JSON, empty response | Archived with its exact bytes; retried within the bound. A later different response is archived too, **both kept** |
| Spool failure | The response is **not durably captured**: a `spool_failed` row without a body; the run stops |
| PostgreSQL failure after the spool write | The run stops (exit 4); the spool keeps the attempt; `recover` ingests it; `resume` continues without re-requesting it |
| Crash between the spool write and the database commit | Journal intent and spooled lines exist; the next command recovers the attempt and marks the run `abandoned`; `resume` continues |
| Mapping failure | Per security: `mapping_failed`, and the archive is untouched. The run is `partial`; `reprocess` after a fix |
| Reconciliation or canonicalisation failure | Per security: canonical `failed` with its reason; the raw observation and archive stand; the **run state is unaffected**; `reprocess` after a fix |
| Snapshot of another session (E1) | No further requests, nothing derived, the run is `failed`. Start a new run at the right time |

**Idempotency:**
- A resumed run keeps its id, and so its Stage E `request_attempt_id`. Re-derivation hits the `(request_attempt_id, company, window)` unique constraint and records `already_present`.
- A new run is a new attempt id, so both observations are preserved and the frozen reconciliation decides between them.
- Identical bytes are recognised (`body_already_archived`) and stored once.
- Nothing is ever overwritten or deleted.

## 9. Security model

| Role | P2 tables |
|---|---|
| `cse_worker` (the capture) | SELECT + INSERT only, **except `market_capture_block_acknowledgements`: SELECT only** (0013). No UPDATE, DELETE, TRUNCATE or ALTER. Must not be superuser, an owner, or a member of `cse_owner` / `cse_migrator` / `cse_backup` / `pg_read_all_data` |
| `cse_migrator` (P1's owner-delegation login; OS user `cse-migrator`, reachable only by root via sudo) | No privileges of its own on P2 tables. For `acknowledge-block` only, it acts as `cse_owner` through `SET LOCAL ROLE`, for exactly one INSERT in one transaction |
| `cse_owner` (NOLOGIN) | Owns everything. Append-only triggers stop even the owner |
| `cse_reader` | SELECT |
| `cse_backup` | Read via `pg_read_all_data`; no write |
| PUBLIC | Nothing |

- Every capture-role command that writes runs a **security preflight** first. It checks that:
  - the role is `cse_worker` and not elevated;
  - it owns nothing and is a member of none of those roles;
  - it has no UPDATE/DELETE/TRUNCATE on the archive or raw observations;
  - it **cannot INSERT a block acknowledgement** (capture is refused until 0013 is applied);
  - the append-only triggers are present and enabled.
- **The owner acknowledgement path (G-1).**
  - `sudo bash ops/bin/cse-capture acknowledge-block …` runs as OS `cse-migrator`, peer-authenticated as `cse_migrator`.
  - The code refuses any other login role. It then runs `SET LOCAL ROLE cse_owner` and one INSERT in one transaction, recording `acknowledged_by = session_user` and the sudo operator.
  - It uses no new role, grant or SECURITY DEFINER function. It is P1's existing owner-delegation path (the one migrations use), and this is its only non-migration use.
  - **The capture worker is refused three ways:**
    1. no INSERT privilege (0013);
    2. no membership in `cse_owner`, so it cannot `SET ROLE`;
    3. the table's guard trigger refuses any session whose *login* role (`session_user`, which `SET ROLE` cannot change) is `cse_worker`, `cse_backup` or `cse_reader`, even if INSERT were ever granted again by mistake.
  - **Service roles never become owner or superuser.** The `blocked` → `running` trigger rule is unchanged.
- The P1 verifier (`verify_server`) still passes with 0012 and 0013 applied.
- **Migration review of 0012:**
  - it is additive, with no ALTER/DROP;
  - PUBLIC gets nothing;
  - the worker gets SELECT and INSERT only;
  - it has append-only row and truncate triggers on all six tables;
  - the reader sees everything (explicit grants plus P1's default privileges);
  - the backup role can read it (`pg_read_all_data`);
  - its hash is recorded by the P1 runner (CRLF-normalised, the same on Windows and Linux).
- **Migration review of 0013 (review fix):**
  - it is additive: one REVOKE (the worker's INSERT on `market_capture_block_acknowledgements`) and a replaced trigger-function body (same name, signature and trigger);
  - it has no GRANT, no new role and no SECURITY DEFINER function;
  - PUBLIC still gets nothing;
  - its hash is recorded by the P1 runner;
  - 0012 stays byte-identical to its committed version, which a unit test pins, because it may already be applied somewhere.

## 10. Backup boundary

- **Capture success never depends on backups.** P2 triggers no backup.
- P1's nightly dump (02:30 Colombo, `Persistent=true`) protects the database.
- P1's hourly and at-boot off-site sync uploads `pg/dumps` **and the spool**.
- After a manual capture, the operator may take a dump at once with `sudo systemctl start --no-block cse-backup-dump.service`. That is asynchronous and outside the capture decision.
- Dimension F reports protection. A failed backup changes nothing in the capture record (`test_protection_is_computed_separately_and_a_backup_failure_never_changes_a_capture`).

## 11. Server installation (manual; no timers)

```bash
sudo git -C /opt/cse/app fetch && sudo git -C /opt/cse/app checkout <REVIEWED-COMMIT>
```

```bash
sudo bash /opt/cse/app/ops/bin/cse-ops migrate apply
```

```bash
sudo install -o root -g cse-worker -m 0640 /opt/cse/app/ops/config/capture.env.example /etc/cse/capture.env
```

```bash
sudoedit /etc/cse/capture.env
```

Set `CSE_CAPTURE_CONTACT_EMAIL` in that file.

```bash
sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision.sh verify
```

- `migrate apply` applies 0012 and 0013 as `cse_migrator`.
- `provision.sh verify` runs the P1 verifier. It checks that the migration ledger is clean and the roles unchanged.

## 12. Purge design (G-1 §6) — designed, NOT implemented, separately gated

A purge is a destructive, irreversible, owner-only procedure, for example after a CSE deletion request. P2 ships **no purge code**:
- no command;
- no database function;
- no grant that would allow one.

The worker and backup roles **cannot** delete or truncate P2 tables or disable their triggers; `test_worker_cannot_mutate_the_archive_and_owner_is_stopped_by_triggers` checks this. Implementing the purge requires its own FULL-gated phase.

**Authorisation and isolation**
- **Owner-authorised only.**
  - The database part runs as `cse_owner`, through `cse_migrator` + `SET ROLE`.
  - The filesystem part runs as root.
  - `cse_worker` and `cse_backup` can do neither.
- **Separate from capture.** First stop all capture (in P3, a disable switch the scheduler honours). Then hold the global capture advisory lock for the whole procedure.

**Inputs and pre-purge evidence**
- **The purge request is recorded first.** It contains:
  - the scope: everything from CSE, a trading-date range, or specific endpoints;
  - the authorisation reference, for example CSE's letter;
  - the requester and time.
- **The pre-purge manifest is written before anything is deleted.** It holds identifiers, hashes and counts only, never CSE content:
  - per-table counts in scope;
  - the body SHA-256s;
  - the spool record and blob keys;
  - the dump directories;
  - the off-site snapshot ids.

  Its own SHA-256 goes into the audit record, and the manifest is kept outside the purged set.

**PostgreSQL: one transaction, all or nothing**
1. `ALTER TABLE … DISABLE TRIGGER <append-only trigger>` on each table in scope.
2. Delete the scoped rows in dependency order:
   - `market_capture_security_results`;
   - `daily_market_data` rows derived from the purged observations (canonical data derived from CSE content);
   - `raw_market_observations` in scope;
   - `market_source_responses`;
   - `market_response_bodies` no longer referenced by any remaining attempt.
3. Re-enable every trigger.
4. Insert the audit record.
5. `COMMIT`.

Why this is safe:
- DDL is transactional, and `ALTER TABLE` holds an ACCESS EXCLUSIVE lock until commit. No other session can ever see or exploit the disabled triggers.
- Any failure rolls everything back, triggers included.
- **The normal append-only protections are never weakened.** No trigger function changes, and no standing deletion path exists at runtime.

Rejected alternative: a SECURITY DEFINER function gated by a session flag. It would require editing frozen trigger functions and would leave a callable deletion path.

**Owner decisions inside the scope:**
- whether to keep the minimal run ledger (ids, dates, states — no CSE content) as audit;
- whether CSE-derived master data (`companies` names from `allSecurityCode`) and F-stage data derived from CSE documents are in scope.

**Spool, dumps and off-site**
- **Spool (root, after the commit).** Remove in-scope records, journal files and blobs. A blob is content-addressed and may be shared, so it is removed only if no out-of-scope record references it. Then run `spool.verify` and check that no manifest key remains.
- **Local dumps.** Every pre-purge dump contains the data.
  1. Take a fresh post-purge dump.
  2. Run a restore check on it.
  3. Only then remove the pre-purge dumps (root). They are read-only by design.
- **Encrypted off-site (restic).** This is the honest limitation:
  - **The server cannot delete off-site history, by design.** The destination is append-only or object-locked. Options:
    - with `rest-server --append-only`, the owner uses the **admin credentials held off the server** to run `restic forget <snapshots> --prune` from an administrative machine;
    - with Object Lock (S3/B2), **deletion is impossible until the retention period expires**. The truthful statement to CSE is then: deleted from live systems and local backups, encrypted off-site copies (unreadable without the owner's key) expire on <date>;
    - for a total purge, destroying every copy of the repository password makes the ciphertext unreadable (crypto-shredding). The cost is all off-site history.
  - Which of these applies depends on the destination the owner picks (a P1 open decision). This must be decided **before** it is needed.

**Audit and verification**
- **Audit.** A future append-only `ops.purge_log` (its own migration, inserted only inside the owner's purge transaction). It records:
  - the purge id, requester, authorisation reference and scope;
  - the manifest SHA-256;
  - per-table counts before and after;
  - the spool files and dumps removed;
  - the off-site action and its status (`done` / `pending_retention` / `crypto_shredded`);
  - when and by whom it was executed;
  - the verification results.

  Rows are never deleted.
- **Verification.** All of these must hold:
  - database counts in scope = 0;
  - no remaining attempt references a missing body;
  - every append-only trigger enabled (P1 `verify_server` and the P2 preflight pass);
  - `spool.verify` clean and no manifest key present;
  - the dump listing checked;
  - the restic snapshot listing checked (manual);
  - a post-purge restore check succeeds;
  - the audit row written.

## 13. Frozen-stage issues found (reported, NOT fixed in P2)

- **D-2 (new, found by P2's real-database tests).**
  - `worker/db.upsert_daily_market_data` serialises `field_provenance` and `discrepancy_notes` with plain `json.dumps`.
  - `reconciliation.reconcile` copies raw-observation **values** into them (`intraday_values`, `superseded_values`, `alternate_values`).
  - `db.get_raw_observations_for_date` returns PostgreSQL `numeric` as Python `Decimal`, so the upsert raises `TypeError`.
  - It triggers for:
    - every **post_open-only** day;
    - any same-source multi-window day where a moving value changed (post_open, then post_close);
    - any discrepancy.
  - Stage E's own test fed in-memory floats through a fake connection, so the real database path was never exercised.
  - **P2 behaviour:** the canonical write fails *per security* with that reason. The archive, the raw observations and the run state are unaffected, and `reprocess` fills the canonical rows once it is fixed.
  - A **strict xfail** regression test using only frozen functions documents it: `test_frozen_stage_e_upsert_of_database_read_observations`. It fails loudly once D-2 is fixed, so the test gets updated.
  - The fix (for example `json.dumps(…, default=…)` in `db.py`, or normalising values in `reconcile`) needs an owner decision under its own gate.
- **D-1 (known since P0.5).** Stage E's `capture_*.py` default the observation date to the UTC date. P2 avoids it entirely: it has an explicit date and E1 evidence, and never calls those entry points.

## 14. Known limitations and open decisions

- **Not wired to P1's verifier.** P2's append-only tables are checked by the P2 preflight and tests, not by P1's frozen `verify_server` list. Adding them there is an owner decision (a P1 change).
- **Post_open canonical rows fail until D-2 is fixed.** The raw observations are fine.
- **No finality re-polling.** A `post_close` snapshot taken before CSE publishes closing prices is kept verbatim and warned about (E2). Scheduling a later capture is P3's job.
- **No trading calendar.** `trading_calendar` is not written by P2 (session evidence is recorded instead). Calendar truth is P3's.
- **No automatic abandoned detection.** A run is marked `abandoned` only when the next P2 command runs. P3's scheduler will run commands regularly.
- **Master data.** The security master is created from CSE's own `allSecurityCode` entries (never invented).
  - `companies.cse_active_flag` mirrors CSE's `active` **only forward in time**. A snapshot updates it only when the `allSecurityCode` response's `observed_at` is strictly newer than the stored `cse_active_flag_checked_at`.
  - So resuming or reprocessing an older run, or the same run again, never rolls it back. The archive keeps every snapshot.
- **Owner decision to confirm.** The acknowledgement owner path reuses P1's migration login, `cse_migrator` acting as `cse_owner`, rather than adding a role, since P1's bootstrap alone can create roles.
  - The alternative is the `postgres` superuser, which is stronger than needed.

## 15. Testing

```bash
pytest tests/test_p2_capture_unit.py
```

```bash
P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p2_capture_postgres.py
```

- The unit tests run anywhere: no PostgreSQL, no CSE. Only a local 127.0.0.1 test server is contacted.
- The PostgreSQL tests need Linux with PostgreSQL 17. They use throwaway clusters and a fake CSE transport built from the real fixtures.

No test contacts CSE or sleeps for real; fake clock, fake transport and local server only.

## 16. Controlled live smoke test (tiny, owner-run or at the P2 gate)

1. Pick a Colombo trading date that has had its session (today after about 14:40, or late the same evening).
2. Set `CSE_CAPTURE_CONTACT_EMAIL`.
3. Run:

   ```bash
   sudo bash /opt/cse/app/ops/bin/cse-capture capture --trading-date <DATE> --mode post_close --cross-check-size 1 --absent-fallback-limit 1 --max-requests 4
   ```

At most 4 CSE requests, at least 1.5 s apart: allSecurityCode, tradeSummary, 1 absent fallback, 1 cross-check.

- **Expected state: `partial`, by design.** The absent fallback is limited, so C is incomplete.
- **Stop immediately on any `blocked` result.** Only the owner records `acknowledge-block`, after review.
- **Check the archive** with `status` and `verify-archive`.
