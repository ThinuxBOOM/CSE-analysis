# Milestone 14: the move to the server (design)

**Status: DESIGN, revision 1 (2026-11-20). Not audited. Not built. Nothing here may run before milestones 8 to 13 are
complete and the owner decides to move (SD-R1).** Written by the cloud session on the branch `integration/cloud`
(`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no provisioning, no deployment, no transfer of
data, no arming, no CSE contact and no change to `main`.

**Governing records:** Master Architecture (MA) §43, §43.1 ("Local production first", including the owner decisions of
2026-11-09: "Local only, accept the risk" and "Start from the local database"), §44, §46, §49, §50, §51, §54;
`docs/ops/LOCAL_PRODUCTION_DESIGN.md` (LP; its recorded differences LD-0 to LD-14 and checks LPC-*);
`docs/ops/STAGING.md` (§4, differences D-01 to D-11); `docs/ops/ISOLATED_RESTORE.md` (REG-B01);
`docs/ops/P1_PLATFORM.md` (dumps, backup ledger, restore check, off-site); `docs/governance/G-1_CSE_DATA_USE.md`.
Where this document and those differ, they win.

**Short name:** SD. Steps SD-1 to SD-9 (§4). Decisions SD-D*n* and SD-R*n* (§7).

---

## 1. Conclusion

```
local production (proven: milestones 10 to 13)
  SD-1 server provisioned and rehearsed, empty ─▶ SD-2 smoke tests, no CSE
  SD-3 local quiesced (disarmed, spool drained, final dump, ledger) ─▶ SD-4 isolated restore of that dump verified
  SD-5 transfer (encrypted, checksummed) ─▶ SD-6 restore on the server ─▶ SD-7 verification equals the local one
  SD-8 every recorded difference verified; off-site backup configured and restored once
  SD-9 the owner arms the server; the workstation stays frozen as the fallback until the owner retires it
```

1. **One database, moved, never re-collected.** By the owner's decision of 2026-11-09 ("Start from the local
   database"), the server starts from the locally proven database: no second backfill, no duplicate CSE requests.
2. **One writer at a time.** Capture never runs in two places. The local environment is disarmed and quiesced before
   its final dump, and the server is armed only after its copy is verified. Between the two, no capture runs (§5).
3. **The copy is proven before it is trusted.** The final dump is first restored on an isolated copy with the
   isolated-restore procedure (which never targets production), then on the server, and both are checked against the
   local database's own verification results.
4. **The differences end here.** Each recorded difference of the local phase (LD-0 to LD-14, staging's D-01 to D-11
   where they apply) is either met natively on the server or verified there, with a record per item. The off-site
   backup, deferred at the owner's accepted risk, starts here.

---

## 2. Requirements from the governing records

| Record | Requirement | Where |
|---|---|---|
| MA §43.1 | Ubuntu 24.04 server with native PostgreSQL 17; its provisioning, deployment rehearsal and smoke tests; encrypted off-site backup; verification of each recorded difference | SD-1, SD-2, SD-8 |
| MA §43.1 | Start from the locally proven database; verify the backup first on an isolated copy; design how it moves and which §54 gates re-run | SD-3 to SD-7, §6 |
| MA §46, G-1 | The capture controls on the new host; the contact e-mail configured on the server, not in the repository | SD-9 |
| MA §49 | Backups, ledger, restore testing, off-site | SD-8 |
| MA §50, §51 | Roles and grants; migrations only through P1's runner and its hash ledger | SD-1, SD-7 |
| LP | Arming records name the host and refuse any other | SD-9 |

---

## 3. Preconditions

- Milestones 8 to 13 complete and accepted by the owner; the local environment's checks green.
- REG-B01 (the isolated-restore procedure) approved by the owner, with its blocking findings 6 to 8 closed.
- The owner's choices: the server and its location (SD-R2), the off-site destination (SD-R3), the move date (SD-R1).

---

## 4. Steps

| Step | What | Passes when |
|---|---|---|
| SD-1 | Provision the server: Ubuntu 24.04 LTS, native PostgreSQL 17, the dedicated roles and grants (§50), the firewall (`provision.sh firewall verify`), the time service, systemd timers, separate backup disk, the accepted build installed from the milestone-9 artifact (the same image digest and package lock) | `provision.sh` checks green; identity of the build equals the accepted one |
| SD-2 | Deployment rehearsal and production smoke tests on the empty server, with no outbound network to CSE | Every smoke test green; the network guard records no CSE contact |
| SD-3 | Quiesce local production: the owner disarms every stage; the scheduler stops; the spool is drained and archived; a final P1 dump is taken and entered in the backup ledger; the local database's own verification is run and recorded (migration ledger hashes, R-F4 `verify`, MF/F7/DE/ML `verify`, row counts and table checksums per table) | Disarmed state recorded; dump hash in the ledger; the verification record exists |
| SD-4 | Restore that dump on an isolated copy (ISOLATED_RESTORE.md) | Its verification equals SD-3's record, item by item |
| SD-5 | Transfer the dump to the server: encrypted in transit and at rest, its SHA-256 compared at both ends; nothing else from the workstation is copied | Hashes equal |
| SD-6 | Restore on the server through P1's restore path; the migration ledger checked by P1's runner | Restore succeeded; ledger hashes equal |
| SD-7 | Verify the server's database against SD-3's record (same checks, same results) | Every item equal; any difference stops the move (§6) |
| SD-8 | Verify each recorded difference (§5 table) and configure the encrypted off-site backup; take the first server dump, send it off-site, and restore it once on an isolated copy | Each item recorded as met or verified; off-site restore verified |
| SD-9 | The §54 gates re-run on the server; the owner arms the server stage by stage (arming names the new host) | Gates green; arming records on the server only |

---

## 5. The recorded differences, resolved

| Local difference | On the server |
|---|---|
| LD-0 container on WSL2 | Native Ubuntu 24.04 and PostgreSQL 17: met |
| LD-1 systemd in a container (D-01) | systemd on the host: timer and sandbox behaviour re-verified (P3 scheduler checks) |
| LD-2 backups on the database's disk (D-02) | A separate disk: P1's separate-device check passes without `CSE_ALLOW_SAME_DEVICE` |
| LD-3 no firewall (D-03) | `ufw` configured; `provision.sh firewall verify` |
| LD-4 the VM's clock (D-04) | The host's time service; "clock synchronized" verified |
| LD-5 packages from the image (D-05) | The same lock installed by `provision.sh packages`; manifest equal |
| LD-6 virtual disk, `fsync`, power loss (D-06, D-10) | Real storage; the free-space gate reads the real device; a power-loss recovery test on the server before arming (crash/recovery as staging ran it) |
| LD-7 no off-site backup | Configured and restored once (SD-8) |
| LD-8 outbound filter inside the container | The host firewall's outbound rule to CSE's API and CDN only (per the runbook) |
| LD-9 a workstation that sleeps | Ends |
| LD-10 one container | Peer authentication over local sockets: verified |
| LD-11 operator identity by convention | `sudo` on the host gives `SUDO_USER`: the enforced form returns |
| LD-12 Windows/Docker updates under the build | Ends; OS updates follow the server's own change control |
| LD-13 harness additions | Removed; only the product's units run |
| LD-14 restore testing off | P1's weekly restore check enabled |

---

## 6. Failure and rollback

- **Before SD-9 (nothing armed on the server):** any failed check stops the move. The server is wiped and
  re-provisioned; the workstation, frozen since SD-3 and untouched, is re-armed by the owner and resumes. The gap in
  capture between SD-3 and the resumption is recorded as missed sessions (P2 records them; historical sessions are
  filled only through the Phase 2 backfill's own governed process, never ad hoc).
- **After SD-9:** the server is production. The workstation's database stays frozen and read-only as a fallback
  until the owner retires it (SD-R4); it is never armed again while the server is armed. Its data is then purged under
  G-1 §6's process if the owner so decides.
- **Single writer.** The arming records are host-bound: an arming made on one host is refused on another, and the
  runbook forbids arming the workstation after SD-9 unless the owner first disarms the server and records why.

---

## 7. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| SD-D1 | Move by one final P1 dump, verified on an isolated copy and on the server against the local verification record | The owner's "start from the local database"; the isolated-restore rule |
| SD-D2 | Quiesce and disarm before the final dump; arm the server only after SD-8 | One writer; no data written after the dump is lost |
| SD-D3 | The workstation stays frozen as the fallback until the owner retires it | A tested way back |
| SD-D4 | Every recorded difference gets a record at SD-8 | MA §43.1: none waived |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| SD-R1 | The decision to move, and its date | SD-1 |
| SD-R2 | The server: hardware or provider, location, who can reach it | SD-1 |
| SD-R3 | The off-site backup destination and its key custody | SD-8 |
| SD-R4 | When the workstation's copy is retired and purged | After SD-9 |
| SD-R5 | Each arming on the server | SD-9 |

---

## 8. Tests and rehearsal

- The whole sequence is rehearsed first between two throwaway environments (a staging-like source and a target), with
  synthetic data, including a planted mismatch at SD-7 (a changed row) that must stop the move, and a planted second
  armed host that must be refused.
- No step contacts CSE; the network guard runs throughout the rehearsal.

---

## 9. Audit record

None yet.
