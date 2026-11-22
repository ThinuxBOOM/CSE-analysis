# Milestone 14: the move to the server (design)

**Status: DESIGN, revision 2 (2026-11-22). Revision 1 ACCEPTED [delegated] (C-0114): its audit found no Blocker;
revision 2 answers its eleven Majors and seven Minors (§10). Not built. Nothing here may run before milestones 8 to
13 are complete and the owner decides to move (SD-R1).** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no provisioning, no
deployment, no transfer of data, no arming, no CSE contact and no change to `main`.

**Governing records:** Master Architecture (MA) §43, §43.1 ("Local production first", including the owner decisions of
2026-11-09: "Local only, accept the risk" and "Start from the local database"), §44, §46, §49, §50, §51, §54;
`docs/ops/LOCAL_PRODUCTION_DESIGN.md` (LP; LD-0 to LD-14, LP-D2, LP-D7, §11's new-build procedure);
`docs/ops/STAGING.md` (§4, D-01 to D-11); `docs/ops/ISOLATED_RESTORE.md` (REG-B01); `docs/ops/P1_PLATFORM.md`
(§3 roles, §7 off-site, §8 B recovery onto a new server); `docs/ops/P3_SCHEDULER.md` (missed sessions,
`max_catch_up_days`); `docs/ops/PHASE2_BACKFILL.md` (§6, REG-B27); `docs/governance/G-1_CSE_DATA_USE.md`. Where this
document and those differ, they win.

**Short name:** SD. Steps SD-1 to SD-10 (§4). Decisions SD-D*n* and SD-R*n* (§7).

---

## 1. Conclusion

```
BEFORE THE FREEZE (no data on the server)
  SD-1 server provisioned per P1 §8 B; the build LP runs installed      SD-2 rehearsal, smoke tests, power-loss and
  SD-3 off-site destination set up and proven on test data                   real-clock downtime tests, no CSE
THE FREEZE WINDOW (chosen over non-trading days; no capture anywhere)
  SD-4 LP quiesced: disarmed, detached, contact e-mail removed, container stopped with restart "no"; final dump;
       post-freeze digest
  SD-5 isolated restore of that dump verified against its manifest      SD-6 transfer (encrypted, checksummed)
  SD-7 restore on the server per P1 §8 B steps 3 to 6                   SD-8 verification against the dump manifest
AFTER
  SD-9 recorded differences closed; first server dump sent off-site and restored once
  SD-10 §54 gates re-run; the owner arms the server; the first capture produces §54's evidence
```

1. **One database, moved, never re-collected.** By the owner's decision of 2026-11-09 ("Start from the local
   database"), the server starts from the locally proven database: no second backfill, no duplicate CSE requests.
2. **One writer, as far as can be made so.** Capture never runs in two places. Host-bound arming alone does not ensure
   it (a manual P2 capture needs no arming), so the freeze removes LP's means to capture (§5). What remains is a
   procedural control, and it is called one.
3. **The copy is proven against the dump's own manifest**, the comparison P1's restore check makes, first on an
   isolated copy (REG-B01) and then on the server. A post-freeze digest shows that nothing was written on the
   workstation after the dump.
4. **Everything that needs no data happens before the freeze**, so the window with no capture is short, planned over
   non-trading days, and its missed sessions are stated in advance.
5. **The differences end here.** Each recorded difference of the local phase is met or verified on the server, with a
   record per item (§6). The off-site backup, deferred at the owner's accepted risk, starts here.

---

## 2. Requirements from the governing records

| Record | Requirement | Where |
|---|---|---|
| MA §43.1 | Ubuntu 24.04 with native PostgreSQL 17; provisioning, deployment rehearsal and smoke tests; encrypted off-site backup; verification of each recorded difference | SD-1 to SD-3, SD-9 |
| MA §43.1 | Start from the locally proven database; verify the backup first on an isolated copy; design how it moves and which §54 gates re-run | SD-4 to SD-8, SD-10 |
| MA §46, G-1 | Capture controls on the new host; the contact e-mail placed by the owner on the server | SD-10, §5 |
| MA §49, G-1 §6 | Backups, ledger, restore testing; an append-only off-site destination the server cannot prune | SD-3, SD-9 |
| MA §50, §51 | Roles and grants; migrations only through P1's runner and its hash ledger | SD-1, SD-7 |
| P1 §8 B | Recovery onto a new server: provision without bootstrap or migrate; restore into a cluster where `cse` does not exist | SD-1, SD-7 |
| LP §11 | LP runs only builds accepted through its new-build procedure | SD-1 |

---

## 3. Preconditions

- Milestones 8 to 13 complete and accepted by the owner; LP's checks green.
- REG-B01 (the isolated-restore procedure) approved by the owner, with its blocking findings 6 to 8 closed; and
  runbook §6 unblocked under REG-B27 (`docs/ops/PHASE2_BACKFILL.md`).
- **The build.** The build LP is running at the freeze, accepted through LP §11's new-build procedure (by then it
  carries the later phases and migrations 0019 onwards). Its identity on a native host is: the reviewed commit, the
  apt package versions pinned to `packages.lock`, and a hash-pinned Python requirements lock including the ML
  libraries. `provision.sh packages` installs current apt versions without a lock (and staging's ST-26 found changed
  revisions), so SD-1 installs the locked versions explicitly and compares the installed manifest with the lock: any
  difference is a finding for the owner, not a silent acceptance.
- **Operation.** The orchestrator on the server follows LP-D7's rule: a closed command list, never an owner-path,
  arming or CSE-contacting command. It has no `sudo` for owner commands; the owner runs them with `sudo`, so
  `SUDO_USER` is the owner's. LD-11's identity is then enforced to the extent the sudoers rules are, and the design
  says so (§6).
- The owner's choices: the server (SD-R2), the off-site destination and its key custody (SD-R3), the freeze window
  (SD-R1).

---

## 4. Steps

| Step | What | Passes when |
|---|---|---|
| SD-1 | Provision the server per **P1 §8 B steps 1 and 2** (Ubuntu 24.04, the backup disk at `/srv/cse-backup`, the commit at `/opt/cse/app`, `provision.sh preflight packages users dirs postgres` **without** bootstrap or migrate), the locked packages and requirements, the firewall (`provision.sh firewall`, then its verification) and the time service; a hostname distinct from LP's `cse-prod` | Checks green; installed manifests equal the locks |
| SD-2 | On a **separate throwaway cluster** (never the production cluster's `cse` database): bootstrap, migrate, the deployment rehearsal, the production smoke tests with no outbound route to CSE, a **power-loss recovery test** (as staging's crash/recovery ran it), and a **real-clock downtime test** (the scheduler's catch-up after the host is off across a scheduled wake-up, staging D-09). Then the throwaway cluster is dropped and the production cluster is verified to have no `cse` database | All green; the network guard records no CSE contact; `cse` absent |
| SD-3 | The off-site destination (SD-R3): its network back end, its **append-only** property (a `forget` or `prune` from the server is refused, G-1 §6) and key custody, proven with a test repository and test data | Proven; staging D-07 closed |
| SD-4 | **Freeze LP** in the chosen window, in this order: the owner disarms every stage; `net-detach`; the contact e-mail removed from LP's `capture.env` by the owner; the scheduler stopped; the spool drained and archived; the final P1 dump taken and entered in the backup ledger; after the last service but PostgreSQL has stopped, a **post-freeze digest** of the workstation's database (per-table digests read through `cse_verifier`, which can only read); then the container stopped with its restart policy set to `no` (LP-D2's `unless-stopped` would restart it) | Each step recorded with its time and operator |
| SD-5 | Restore that dump on an isolated copy (ISOLATED_RESTORE.md) and compare it with **the dump's own manifest** (as P1's restore check does). Compare the post-freeze digest with the manifest: the only expected difference is the final dump's own ledger row (`running` inside the dump, `succeeded` live); any other difference means something was written after the dump, and stops the move | Equal, with that one named difference |
| SD-6 | Transfer the dump: encrypted in transit and at rest, its SHA-256 compared at both ends; nothing else from the workstation is copied | Hashes equal |
| SD-7 | Restore on the server per **P1 §8 B steps 4 to 6** (verify the dump, prove it with a throwaway restore, restore into the cluster where `cse` does not exist), then P1's runner checks the migration ledger | Restore succeeded; ledger hashes equal |
| SD-8 | Verify the server's database against the dump's manifest (all table digests); `cse-financial verify` and `cse-features verify` run **in full** (not their default samples), and their results compared with the same commands run in full on the SD-5 isolated copy | Every item equal; any difference stops the move (§8) |
| SD-9 | Close each recorded difference (§6); take the first server dump, send it off-site, restore it once on an isolated copy; enable P1's weekly restore check | Each item recorded; off-site restore verified |
| SD-10 | The §54 gates re-run on the server; the owner places the contact e-mail, the off-site credentials and any provider keys (they are not transferred); the owner arms the server stage by stage (arming names the new host); the first capture produces §54's first-capture evidence | Gates green; arming records on the server only; §54 evidence recorded |

---

## 5. One writer, and the freeze window

- **Means removed, not only permission.** After SD-4, LP cannot capture without the owner reversing four recorded
  steps: it is disarmed, detached from the network, has no contact e-mail (P2 refuses to send without it, G-1
  control 4), and its container is stopped with no restart. Re-enabling LP after SD-10 is forbidden by the runbook
  unless the owner first disarms the server and records why. This is a **procedural control**: someone with access
  to the workstation could reverse it. Arming rows name their host and the server's hostname differs from LP's, so an
  LP arming can never authorize the server.
- **The window.** SD-4 to SD-10 is planned over non-trading days (a weekend plus a holiday where possible), because a
  market session can be captured only on its own day: a session missed in the window is recorded `missed` by P3 and
  is **not** recoverable later (Phase 2's backfill concerns financial filings, not market sessions). The runbook
  states the expected missed sessions before SD-4. If the window exceeds `max_catch_up_days` (default 14), the
  server's first wake-up refuses until the owner confirms the catch-up (`run --confirm-catch-up-through`).
- **What moves and what does not.** The database moves. The spool is drained and archived into the database before
  the dump (SD-4), so the PostgreSQL archive is authoritative for `verify-archive` on the server. Credentials, the
  contact e-mail and keys are placed by the owner on the server (SD-10). After SD-8, the transit copies of the dump
  are deleted (G-1 control 8), and the deletion is recorded.

---

## 6. The recorded differences, resolved

| Local difference | On the server |
|---|---|
| LD-0 container on WSL2 | Native Ubuntu 24.04 and PostgreSQL 17: met |
| LD-1 systemd in a container (D-01) | systemd on the host: timer and sandbox behaviour re-verified (P3 scheduler checks) |
| LD-2 backups on the database's disk (D-02) | A separate disk: P1's separate-device check passes without `CSE_ALLOW_SAME_DEVICE` |
| LD-3 no firewall (D-03) | `ufw` configured by `provision.sh firewall` and checked by its verification step (two P1 steps) |
| LD-4 the VM's clock (D-04) | The host's time service; "clock synchronized" verified |
| LD-5 packages from the image (D-05) | The locked versions installed and compared (§3); differences reported to the owner |
| LD-6 virtual disk, `fsync`, power loss (D-06, D-10) | Real storage; the free-space gate reads the real device; the power-loss test at SD-2, on the empty server |
| LD-7 no off-site backup | SD-3 and SD-9 |
| LD-8 outbound filter inside the container | **Not met by P1 as written:** `provision.sh firewall` sets `ufw default allow outgoing`, and no runbook rule restricts outbound traffic. An outbound rule limited to CSE's API and CDN would change a frozen P1 step and inherit LP §5's cost (CDN addresses change). It is the owner's choice (SD-R6), as LP-R4 was: a P1 change control adding the rule, or the recorded absence of an outbound restriction on the server |
| LD-9 a workstation that sleeps | Ends; the real-clock downtime test (SD-2) |
| LD-10 one container | Peer authentication over local sockets: verified |
| LD-11 operator identity by convention | `sudo` gives `SUDO_USER`; enforced only as far as the sudoers rules are (§3): the orchestrator has no `sudo` for owner commands |
| LD-12 Windows/Docker updates under the build | Ends; OS updates follow the server's own change control |
| LD-13 harness additions | Removed; only the product's units run. `tick`'s alerts go with it, so the alert channel (P1's open decision, LP-R3, P11-R1) must be settled before unattended operation |
| LD-14 restore testing off | P1's weekly restore check enabled (SD-9) |
| D-07 off-site test destination only | Network back end, append-only property and key custody proven (SD-3) |
| D-09 downtime simulated | The real-clock downtime test (SD-2) |

**Expected baseline after the restore** (recorded in `status`, not findings): the final dump's ledger row stays
`running` in the restored copy; the workstation's dump artifacts are absent from the server's backup root; P3 leases
and wake-ups restored from LP name the old host and are closed by the first server wake-up.

---

## 7. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| SD-D1 | Move by one final P1 dump, compared with its own manifest on an isolated copy and on the server | The owner's "start from the local database"; P1's restore check compares the same way |
| SD-D2 | Remove LP's means to capture at the freeze, not only its arming | A manual capture needs no arming |
| SD-D3 | Everything without data before the freeze; the window over non-trading days | Missed sessions cannot be recovered |
| SD-D4 | Every recorded difference gets a record at SD-9 | MA §43.1: none waived |
| SD-D5 | After the server's first capture, the fallback is the server's own dumps and off-site copy; a return to the workstation is this procedure in reverse | The workstation copy is stale from then on |
| SD-D6 | Provisioning follows P1 §8 B exactly; smoke tests on a throwaway cluster | P1 §8 B requires `cse` absent at the restore |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| SD-R1 | The decision to move and the freeze window | SD-1 |
| SD-R2 | The server: hardware or provider, location, who can reach it, **with the owner's G-1 determination** that it is the owner's own server in G-1 §3's sense (a hosted machine may not be) | SD-1 |
| SD-R3 | The off-site destination and its key custody | SD-3 |
| SD-R4 | When the workstation's copy is retired and purged (G-1 §6) | After SD-10 |
| SD-R5 | Each arming on the server | SD-10 |
| SD-R6 | The server's outbound rule: a P1 change control restricting outbound traffic to CSE, or its recorded absence | SD-1 |

---

## 8. Failure and rollback

- **Before SD-10 (nothing armed on the server):** any failed check stops the move. The server's cluster is dropped
  and re-provisioned; the workstation, frozen since SD-4 and untouched (the post-freeze digest proves it), is
  re-enabled by the owner reversing SD-4's steps and resumes. The sessions missed in the window are recorded
  `missed`.
- **After SD-10:** the server is production. Its own dumps and off-site copies are the fallback (SD-D5). The
  workstation's frozen copy is kept read-only until the owner retires it (SD-R4) and is never re-enabled while the
  server is armed. A return to the workstation would be this procedure in reverse, with its own freeze.

---

## 9. Tests and rehearsal

- The whole sequence, forward **and in reverse**, is rehearsed first between two throwaway environments (a
  staging-like source and a target) with synthetic data, including:
  - a row written on the source after the dump, which SD-5's digest comparison must catch;
  - a changed row on the target, which SD-8 must catch;
  - a manual capture attempted on the frozen source, which must fail for lack of network and contact e-mail;
  - an arming row from the source host, which the target must refuse.
- No step contacts CSE; the network guard runs throughout the rehearsal.

---

## 10. Audit record

### Audit 1 (revision 1; fresh-context agent, 2026-11-22): ACCEPT

No Blocker. Eleven Majors, fixed in revision 2:

| ID | Finding | Fix |
|---|---|---|
| SD-M1 | Host-bound arming does not stop a manual capture | Means removed at the freeze; a procedural control named as such (§5) |
| SD-M2 | Writes on the workstation after the dump would be lost silently | Post-freeze digest compared with the manifest (SD-4, SD-5) |
| SD-M3 | Item-by-item equality could never hold (the ledger row; sampled `verify`) | The manifest comparison with one named difference; `verify` in full (SD-5, SD-8) |
| SD-M4 | Provisioning contradicted P1 §8 B | P1 §8 B exactly; smoke tests on a throwaway cluster (SD-1, SD-2, SD-7) |
| SD-M5 | No image digest or package lock on a native host | The build identity defined; manifests compared (§3) |
| SD-M6 | The milestone-9 build would not match the database | The build LP runs, accepted through LP §11 (§3) |
| SD-M7 | No outbound restriction exists in P1 | Recorded; the owner's choice SD-R6 (§6) |
| SD-M8 | Staging D-07 not carried | SD-3 proves the back end, append-only property and custody |
| SD-M9 | Missed sessions cannot be backfilled; the window was not sized | Everything without data before the freeze; a non-trading window; expected misses stated (§5) |
| SD-M10 | The fallback lost the server's writes | SD-D5; reverse procedure rehearsed (§8, §9) |
| SD-M11 | Server operation unspecified | LP-D7's rule; no orchestrator `sudo` for owner commands (§3) |

Minors fixed: the power-loss test on the empty server; a real-clock downtime test; the restored baseline; what moves,
what the owner places, transit copies purged; the owner's G-1 determination of the server; the firewall's two
steps and the alert channel; REG-B27, §54's first-capture evidence and a distinct hostname.
