# Phase 12: the dashboard (design)

**Status: DESIGN, revision 2 (2026-11-22). Revision 1 ACCEPTED [delegated] (C-0114): its audit found no Blocker;
revision 2 answers its four Majors and seven Minors (§11). Not implemented.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no
network exposure and no change to `main`.

**Governing records:** Master Architecture (MA) §19 to §23, §42, §43, §46, §50, §55 (Phase 12), §59, §63, §64, §68;
`docs/governance/G-1_CSE_DATA_USE.md` (§3 scope, §4 controls 7 and 9); `docs/ops/LOCAL_PRODUCTION_DESIGN.md` (§5
network); the Phase 8 to Phase 11 designs (the views it shows). Where this document and those differ, they win.

**Short name:** DB. Steps DB-1 to DB-5 (§9). Decisions P12-D*n* and P12-R*n* (§8).

---

## 1. Conclusion

```
PostgreSQL (login cse_dashboard: SELECT on the dash.* views only) ─▶ a small server-rendered web application on the host
        ─▶ the owner's browser on the host (default), or over the LAN/VPN only if the owner opens it (P12-R1)
pages: security · forecasts · reasoning · accuracy · backtests · provenance · health
```

1. **Read-only.** The dashboard connects as a role that can only `select` from a set of views (§4). It has no write
   path to any table; it cannot designate, promote, arm, capture or change data. Owner actions stay on the owner path
   (the command lines), not in a browser.
2. **Personal use, by construction.** G-1 covers personal, non-commercial, private local analysis with no
   redistribution. So the dashboard serves the owner alone: it listens on the host only by default, requires the
   owner's sign-in even there, has no public exposure, no sharing or export of raw CSE data, and no CSE branding
   (G-1 §3, §4 controls 7 and 9).
3. **It shows, it does not compute.** Every number on a page comes from a stored row (forecast, outcome, report,
   check) and links to its provenance. The dashboard computes no metric, prediction or agreement of its own; layout
   only.
4. **Small and pinned.** Server-rendered HTML from a pinned Python web framework and templates, no client build chain
   and no third-party scripts or fonts fetched at run time, so it adds no outbound network need (LP §5). It is **not**
   in the milestone-9 build: it reaches local production only in a new build accepted through LP §11's new-build
   procedure (which must exist first), the same build that carries Phases 8 to 11 and migrations 0024 to 0028.

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §55 | Local/LAN/VPN UI; security pages; forecasts; reasoning; accuracy; backtesting; provenance; health | §3, §5 |
| §19, §20 | Both routes per horizon; agreement; data warnings | §3 (forecast page) |
| §42 | Provenance drill-down | §3 (provenance page) |
| §63 | Reasoning as evidence summaries | §3 (reasoning) |
| §64 | The questions the finished platform should answer | §3 (the pages; each §64 question is answered from one of them) |
| §50 | Dedicated roles and grants | §4 |

---

## 3. Pages (DB-3, DB-4)

| Page | Shows | Source |
|---|---|---|
| Home | Latest session D, the cycle's status, counts of forecasts by state, open alerts | `fc_forecasts`, `ad_alerts`, health views |
| Security | Identity (symbol, issuer link, industry group), recent closes and features at T, the latest forecasts for four horizons, its outcome history | MF rows, `fc_*`, `ev_outcomes` |
| Forecast | Phase 8's view: both routes side by side per horizon, the derived price, uncertainty as each route defines it (Route 2 labelled `as_stated`), agreement, warnings, the late Route 2 link if any | `fc_*` |
| Reasoning | Route 1's templated statements and Route 2's validated statements, each with its evidence ids resolved to the stored value | `fc_forecasts` record, `r1_predictions`, R2 tables |
| Accuracy | Phase 9's reports by horizon, route and stratum, with counts, intervals and block counts; revised outcomes marked | `ev_reports` |
| Backtests | Phase 10's runs: validity first (each check), then reports; `invalid` runs shown as invalid | `bt_runs`, `bt_checks`, `bt_reports` |
| Models | The registry: versions, statuses, designations in force, gate reports, alerts and shadow comparisons | Phase 5's registry, `ad_*` |
| Provenance | From any forecast: the R1 record, the MF/F7/DE rows and their hashes, the model artifact hash, the Route 2 run, pack hash and response hash; `verify` results where stored | the bound rows |
| Health | What the database records: capture runs and their states, the scheduler's wake-ups and leases, backups and the backup ledger, Phase 11's alerts, `verify` results stored in the database. LP's check records and `tick` output are files on the host, not rows: they are **not** shown in v1 (a table they would write is a later LP change, recorded as such) | P2, P3 and P1 tables; `ad_alerts` |

- **Display rules.** States and refusals are shown as states, never as blanks. Numbers show their units and, where a
  report gives one, their interval and count. Small strata show counts only (Phase 9 P9-D3). A metric is never shown
  without its population (live, late, simulated).
- **No raw responses.** Pages show derived values and hashes, not raw CSE response bodies (G-1 control 7 is about
  third parties, but the dashboard has no reason to render them, and not doing so keeps it out of scope).
- **No CSE resources.** No embedded image, script, style or frame from CSE; links to CSE pages, if any, are plain
  text (G-1 control 9).
- **The Models page** shows shadow comparisons, never shadow predictions as forecasts (Phase 11 ADL-3).

---

## 4. Access (DB-1, DB-2)

- **Database.** `cse_reader` already exists (MA §50, P1 §3): a NOLOGIN group with SELECT on `public` and `ops`,
  whose member `cse_verifier` R-F4's `verify` relies on. The dashboard does **not** use it and is **not** a member of
  it (membership would give it the raw archive). Migration `0028_dashboard.sql` creates a new login
  **`cse_dashboard`** (NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS, PASSWORD NULL, peer
  authentication only), with USAGE on schema `dash` and SELECT on its views only, a statement timeout and a
  connection limit. Its OS user and `pg_ident` mapping are a **P1 change control** (as P1-CC-1 was for
  `cse_verifier`), checked by P1's role invariants. `default_transaction_read_only = on` is set as defence in depth
  only; the control is the grants.
- **What the views exclude:** raw archive bodies and the spool's content, `r2_responses`, pack bytes, `ml_artifacts`
  bytes and `ad_shadow_predictions` (a test checks that no view reads them, DBT-8).
- **Network, local production.** LP's container has no published port and sits on an internal bridge (LP §3.2, §5,
  LD-3); its loopback is not the Windows host's. In v1 the dashboard binds to the container's own loopback, and the
  owner reaches it through a `docker exec`-started port forward to the host's loopback, for the session only. A
  published port would need the recreate procedure and a new recorded difference (LD-15), which is the owner's
  choice (P12-R1).
- **Network, server.** Bound to the server's loopback; the owner reaches it through an SSH tunnel. LAN or VPN exposure
  is P12-R1; if chosen, TLS with a host certificate and a firewall rule limited to the named network.
- **Sign-in.** One owner account; the password stored as an Argon2id hash (a pinned library, part of P12-R2) in a file
  under `/etc/cse/dashboard/` readable by the service's group only; sessions in server memory with a short idle
  timeout; cookies `HttpOnly`, `SameSite=Strict`, and `Secure` whenever TLS is used; a CSRF token on the sign-in form;
  failed attempts rate-limited and logged. No sign-up, no other users (personal use, §1 item 2). **Any viewer other
  than the owner is a change of G-1's scope** and requires a G-1 review first (G-1 §3, §5).
- **Headers.** A strict content security policy (no inline script, no third-party origin), no caching of pages, no
  external fonts or analytics, and a Host-header allowlist (refusing any other Host, against DNS rebinding of a
  loopback service).

---

## 5. Operation

- Runs as a service in the accepted build, under the same scheduler and logs as the other services, with no
  scheduled work of its own.
- Read load is small; pages are rendered from views with indexes the migration adds.
- When the database is unavailable or a view fails, the page says so; it never shows cached or stale numbers as
  current.

---

## 6. Point-in-time and honesty

- Every page states its data instant (the latest `recorded_at` it read). A forecast page shows the forecast's T and
  A, and the route states as stored.
- Nothing displayed changes a stored row; there is no "refresh forecast" or "re-run" button.

---

## 7. Storage

**Migration `0028_dashboard.sql`** (additive; waits in `migrations_pending/`): the login `cse_dashboard`, the `dash`
schema and its views, the indexes they need. No table. The login is a P1 change control (§4).

---

## 8. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| P12-D1 | A new login `cse_dashboard`, not a member of `cse_reader`, over views only; no write path | Owner actions stay on the owner path; `cse_reader` would expose the raw archive |
| P12-D2 | Loopback only by default (container loopback plus a session port forward in LP; SSH tunnel on the server); one owner account | G-1 §3 personal use; LP has no published port |
| P12-D3 | Server-rendered, pinned, no external assets | No new outbound need; part of the accepted build |
| P12-D4 | No computation on the page; every number from a stored row with provenance | MA §42, §63 |
| P12-D5 | No raw CSE response bodies rendered | Not needed; keeps G-1 exposure minimal |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P12-R1 | LAN or VPN exposure, and which network | Any access other than from the host |
| P12-R2 | The web framework and the password-hashing library, among pinned candidates the implementation proposes | DB-3 |
| P12-R3 | Which reports appear by default (shared with P9-R1) | DB-4 |

---

## 9. Implementation sequence and tests

| Step | What | Exit |
|---|---|---|
| DB-1 | Migration 0028: login, views; the P1 change control | PostgreSQL 17: `cse_dashboard` cannot write any table, read any table directly, or call any owner function (DBT-1); `cse_verifier` unaffected |
| DB-2 | Sign-in, sessions, headers | DBT-2 to DBT-4 |
| DB-3 | Forecast, reasoning, provenance pages | Every number traced to a row (DBT-5) |
| DB-4 | Accuracy, backtests, models, health | Populations and intervals shown (DBT-6) |
| DB-5 | Service unit, runbook; carried in a new build accepted through LP §11 | Loopback bind verified; no outbound request (DBT-7) |

| ID | Test | Must hold |
|---|---|---|
| DBT-1 | Every insert, update, delete, direct table select and owner function as `cse_dashboard` | Refused |
| DBT-2 | A request without a session | Redirected to sign-in; no data |
| DBT-3 | Repeated wrong passwords | Rate-limited, logged |
| DBT-4 | The page headers | CSP without external origins; no-store |
| DBT-5 | A rendered forecast page | Each number equals the stored value it names |
| DBT-6 | An `invalid` backtest | Shown as invalid, its reasons first |
| DBT-7 | Start the service under the network guard | No outbound connection; bound to loopback |
| DBT-8 | Every `dash` view's dependencies | None reads the excluded tables of §4 |
| DBT-9 | A request with another Host header; a sign-in without the CSRF token; the cookie flags | Refused; refused; flags set |

---

## 10. The fifteen questions (MA §68)

1. Layer: presentation to the owner. 2. Sources: views over stored rows. 3. At its instant: what is recorded when the
page is rendered, stated on the page. 4. Provenance: drill-down to every bound row and hash. 5. Frozen: none.
6. Migration: 0028 (role and views). 7. Leakage: not applicable to display; no write path. 8. Tests: §9. 9. Reproduction:
a page is a rendering of named rows. 10. Routes: shown side by side. 11. Evaluation: shows Phase 9 and 10's reports.
12. Downtime: says the database is unavailable. 13. Failures: states shown as states. 14. Versions: every row's
versions shown. 15. Reasoning: shown with its evidence.

---

## 11. Audit record

### Audit 1 (revision 1; fresh-context agent, 2026-11-22): ACCEPT

No Blocker. Four Majors, fixed in revision 2:

| ID | Finding | Fix |
|---|---|---|
| P12-M1 | `cse_reader` already exists and serves `verify` | A new login `cse_dashboard`, not a member, under a P1 change control (§4) |
| P12-M2 | LP has no published port; server access unstated | Container loopback with a session port forward; SSH tunnel on the server (§4) |
| P12-M3 | "Part of the accepted build" was wrong | Carried by a new build through LP §11's procedure (§1 item 4, DB-5) |
| P12-M4 | Health read host files as if they were rows | Database-recorded health only in v1 (§3) |

Minors fixed: the hash file's permissions and the pinned library; `default_transaction_read_only` as defence in depth;
cookie flags, CSRF and a Host allowlist; no CSE resources; the §64 wording; any other viewer is a G-1 review; the
excluded tables listed and tested.
