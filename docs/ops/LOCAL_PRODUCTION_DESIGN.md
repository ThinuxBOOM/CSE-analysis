# Milestone 10: the local production environment (design)

**Status: DESIGN, revision 1 (2026-11-11). Not built. Not accepted. Not audited yet.** Written by the cloud session on
the branch `integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no build, no
deployment, no arming, no capture and no CSE contact. Everything in it that runs, runs on the owner's workstation and
waits for it.

**What it designs:** the environment the owner decided on 2026-11-09 (Master Architecture (MA) §43.1, "Local
production first"): a local production environment in Docker on the owner's workstation, separate from staging, with
its own persistent volumes, where the live prerequisites, the pilot and the bulk backfill run (milestones 11 to 13)
before any server deployment (milestone 14).

**Governing records:** MA §43, §43.1, §46 to §50, §54; `docs/R_F4_DESIGN.md` §10 (milestones 9 to 14);
`docs/ops/STAGING.md` (the harness this environment is derived from, and its recorded differences D-01 to D-11);
`docs/ops/P1_PLATFORM.md`, `docs/ops/P2_MARKET_CAPTURE.md`, `docs/ops/P3_SCHEDULER.md`,
`docs/ops/PHASE2_BACKFILL.md`; `docs/governance/G-1_CSE_DATA_USE.md`. Where this document and those differ, they win.

**Identifiers.** The environment is "LP". Its steps are LP-1 to LP-6 (§9), its checks LPC-*n* (§8), its recorded
differences LD-*n* (§7). Decisions are LP-D*n* ([delegated]) and LP-R*n* (reserved for the owner) (§10).

---

## 1. Conclusion

1. **LP is one long-lived container built from the image staging accepted**, provisioned by the frozen scripts,
   on its own named volumes, with a fixed hostname. Nothing in the product changes: LP adds no migration, role, grant,
   entry point or unit to it.
2. **The deployed build is the accepted one, by image id.** The LP driver is harness code that lives outside the
   deployed tree, exactly as the staging driver does, so adding it does not change what is deployed.
3. **It starts with no network.** Provisioning, the rehearsal and the smoke tests run with the container on no
   network at all. A network is attached only by a separate, explicit driver step, after the owner's decisions that
   permit CSE contact.
4. **Two things on the workstation need the owner's attention before the bulk backfill, and neither is a software
   problem:** disk space (local dumps are full copies and are never pruned, on a drive that had about 30 GB free
   during staging) and the workstation being on, awake and time-synchronised when captures are due (§6).
5. **What the workstation cannot do as production is recorded, not hidden** (§7), and verified at the server
   deployment, as MA §43.1 already lays down.

---

## 2. What LP must be, from the governing records

| Requirement | Source | Where met |
|---|---|---|
| Separate from staging, own persistent volumes; staging evidence never copied in | MA §43.1 | §3, §4 |
| The exact build accepted at milestone 9: same commit, same pinned dependencies | MA §43.1 | §3.1 |
| PostgreSQL 17; the roles and grants of §50; migrations only through P1's runner; spool and backup semantics; local dumps and the backup ledger | MA §43.1 ("Kept") | §3.3 |
| A deployment rehearsal, provisioning and smoke tests without CSE contact | MA §43.1, §54; `docs/R_F4_DESIGN.md` §10 row 10 | §9 |
| Every gate of §54 before the first capture: clock synchronised, backups configured (local dumps), scheduler verified, governance recorded, request budget confirmed, tests proven offline, no test contacting CSE | MA §54 | §8 |
| CSE contacted only through the governed transport inside the environment, never ad hoc from the host | MA §43.1 | §5 |
| No off-site backup in the local phase, at the owner's accepted risk | Owner decision 2026-11-09 | §6.2 |
| G-1's controls apply to the workstation | MA §43.1, §46 | §5, §6.2 |
| Approved outbound access to CSE's API and CDN only | `docs/ops/PHASE2_BACKFILL.md` §12 | §5 |
| Poppler 24.02.0; the dedicated temporary root; at least 20 GiB free on the database disk | `docs/R_F4_DESIGN.md` §10 row 10 | §3, §6.1 |

---

## 3. The environment

### 3.1 The image

LP runs **the staging host image of the accepted commit, by image id**: the image built by
`ops/staging/staging.sh build` from the commit the owner accepts at milestone 9, whose id and package manifest the
acceptance campaign recorded. It is not rebuilt. A rebuild from a moving apt archive can differ in package revisions
(staging's ST-26 found exactly that), and the accepted build is defined by what staging validated.

- If milestone 9 accepts a commit other than the one Phase B staged (`af1fdd16cc40`), that commit is staged first
  (MA §43.1), and LP uses that campaign's image.
- The image carries labels and an init script named for staging. They are names only; nothing in them is specific
  to staging behaviour. LP records the image id and the revision label, and refuses any other.

### 3.2 The container

| Property | Value | Reason |
|---|---|---|
| Name, hostname | `cse-prod-host`, hostname `cse-prod` | P3's and HB-6's arming record the host and refuse any other. The hostname is fixed and never reused by staging |
| Privilege | Staging's rung L1: `CAP_SYS_ADMIN`, private cgroup namespace; not `--privileged` | The rung staging established for systemd as PID 1 with the frozen units' sandboxing |
| Network | None at creation. Later, one dedicated user-defined bridge network, attached by an explicit step (§5). No published port | No inbound path exists; outbound exists only after the owner's decision |
| Restart policy | `unless-stopped` | After a workstation or Docker restart the scheduler must come back by itself, or captures are missed. This is safe: nothing contacts CSE unless armed. (Staging uses `no` so that it stays stopped.) |
| Memory, processes | 4 GiB, 4096 processes, stop timeout 400 s | Document extraction and PostgreSQL; staging used 2 GiB. To be confirmed in the rehearsal (LP-2) |
| Mounts | Named volumes only (§3.3). No host bind mount, no Docker socket | Staging's boundary; NTFS bind mounts would also break P1's ownership and mode checks |
| Labels | `cse.env=production-local`, the revision | So that no staging command can select it, and the LP driver can refuse anything else |

### 3.3 Volumes and what survives

| Volume | Mounted at | Holds |
|---|---|---|
| `cse-prod-pgdata` | `/var/lib/postgresql` | The PostgreSQL 17 cluster: every production row |
| `cse-prod-backup` | `/srv/cse-backup` | Local dumps, the spool, backup status, restore scratch |
| `cse-prod-etc` | `/etc/cse` | `cse.env`, `capture.env` (the contact e-mail), `backfill.env`, `backup.env` |
| `cse-prod-state` | `/var/lib/cse-backfill` | HB-6's reports and the dedicated temporary root |

**The container's own filesystem is not durable storage.** Operating-system users, systemd unit enablement,
PostgreSQL's configuration under `/etc/postgresql` and the R-F4 and P1 provisioning results live in the container's
writable layer. They survive stop and start. They do not survive removal of the container.

- LP therefore treats the container as **rebuildable**: the frozen provisioning scripts are idempotent (staging's
  ST-05/ST-06 showed `fresh` then `again` leaves an identical fingerprint), so a removed container is recreated on
  the same volumes and provisioned again with the same explicit step list.
- **Recreation is a recorded procedure, not an accident:** the driver never removes the container, and a recreate
  step first requires a fresh dump, then checks afterwards that the cluster's system identifier, the migration ledger
  and the row digests are unchanged (LPC-9).
- Whether P1's `postgres` step accepts an existing cluster on the volume when the container is new is the one part
  staging did not exercise in this form. It is the first thing the rehearsal tests (LP-2); if it does not, the design
  changes before anything real is provisioned.

### 3.4 Provisioning

Inside the container, the frozen scripts unmodified, by explicit step lists, in staging's order:

```
ops/provision/provision.sh            preflight users dirs postgres bootstrap migrate units
ops/provision/provision_scheduler.sh  (P3)
ops/provision/provision_backfill.sh   (HB-6)
```

- Migrations 0001 to 0018 through P1's runner only.
- `provision.sh firewall` is not run (LD-3). `provision.sh all` and `selftest` are not run, as at staging.
- `CSE_ALLOW_SAME_DEVICE=1` is set, because the backup volume shares the virtual disk with the database (LD-2). §6.2
  says what compensates.
- Staging's R1 gate is **not** installed. In LP, P1's weekly restore check is P1's own designed behaviour: it
  restores the newest dump into a throwaway cluster under `restore-scratch` and compares it with the manifest. It
  never touches the production cluster. "DO NOT EXECUTE RESTORE" was a rule for staging campaigns; it is not a rule
  of the product.
- The contact e-mail stays empty after provisioning. Every CSE-contacting command refuses without it.

---

## 4. Separation from staging

- Different container name, hostname, labels and volume names; the LP driver acts only on objects labelled
  `cse.env=production-local`, and the staging driver only on its campaign labels. A unit test asserts that neither
  driver's commands can name the other's objects.
- Evidence goes to `D:\cse-prod\` (records, checks, driver log), never to `D:\cse-staging\` and never into the
  vault. Records hold no CSE response and no secret.
- Nothing is copied from a staging volume into an LP volume: no security master, no dump, no spool entry. HB-P1 is
  satisfied only by LP's own first governed capture (MA §43.1).
- The synthetic seed (`ops/staging/seed_synthetic.py`) is never run in LP. LPC-2 checks that the database holds no
  row before the first capture.

---

## 5. Network and CSE contact

**Before the owner's decisions.** The container has no network. Provisioning, the rehearsal and the smoke tests all
run in that state, so none of them can contact CSE, by construction (MA §54: "no test accidentally contacts CSE").

**Attaching the network** is one driver step, `net-attach`, that refuses unless a file the owner writes records the
decision that permits contact: for the first market capture, HB-X2(b) (the contact e-mail) and the P3 arming's
prerequisites; for Phase 2 discovery and documents, HB-X2(a) as well. The driver does not decide these; it only
refuses to attach without the record.

**Inbound.** No port is published, so nothing on the workstation or the network can connect in. This is the
workstation's form of P1's "incoming denied" (LD-3).

**Outbound: what is and is not restricted.**

- The only code in the image that makes a CSE request is the governed transport (P2's, and HB-2's for Phase 2), which
  enforces G-1's controls: one request at a time, at least 1.5 s apart, back-off, the identifiable User-Agent, no
  proxies, no redirects off CSE's hosts.
- The runbook also asks for outbound access "to CSE's API and CDN only" at the network level. On a server that is a
  firewall rule. In Docker on the workstation the equivalent is a packet filter inside the container's network
  namespace: an output policy of drop, allowing DNS to Docker's resolver and TCP 443 to the addresses of
  `www.cse.lk` and `cdn.cse.lk`.
- **LP-D3 chooses to install that filter, as an environment control outside the product:** a root-owned unit that
  loads a fixed rule set before the network is attached, with the address set refreshed from DNS by the same unit
  before each scheduler wake-up. It needs `CAP_NET_ADMIN`, one capability more than staging's rung, which the
  rehearsal must show works with the frozen units' sandboxing.
- **Its limits, stated:** a CDN's addresses change, so a refresh that lags blocks legitimate requests (the transport
  then records a network error and backs off; nothing is bypassed). The filter is not a proxy and alters no request,
  so G-1 control 6 is not engaged. If the rehearsal shows it cannot be made reliable, the fallback is no packet
  filter, with the difference recorded as LD-8 and verified by firewall at the server deployment; that fallback is
  the owner's call (LP-R4), because it relaxes a stated prerequisite.

**No ad hoc contact from the host.** The driver has no command that fetches anything from CSE, and its unit tests
assert that no CSE hostname appears in it outside the filter's rule set and comments.

---

## 6. The workstation's two real constraints

### 6.1 Disk

- **P1's local dumps are full copies and are never pruned** (`docs/ops/P1_PLATFORM.md`: a local retention policy is
  an open owner decision). The dump timer is nightly.
- During staging, drive D: had about 30 GB free against a 20 GB floor. Phase 2's design estimates the database at a
  few gigabytes after the bulk backfill (about 200 to 260 KB per filing for the F4 structure alone). Nightly full
  dumps of a database of that size, kept for ever, fill the remaining space within days to weeks of the bulk stage.
- HB-6's runner refuses to work below 20 GiB free on the database disk, read from Docker's virtual disk, not from the
  host drive (LD-6). So the host can run out first while the runner still sees room.
- **What LP does:** the driver's `guard` checks the host drive and Docker's virtual disk before every step and
  refuses below a floor; `status` reports dump count, total dump size and days of headroom at the current rate.
- **What only the owner can do (LP-R1), before HB-S4 at the latest:** decide a local dump retention policy, or move
  Docker's data and the backup copy to a drive with room. HB-Q9 (capacity) already requires the owner's confirmation
  before HB-S4; this is the same question, raised now because the answer may need hardware.

### 6.2 Backups without an off-site copy, on one virtual disk

The owner accepted having no off-site backup locally ("Local only, accept the risk"). Separately, the dumps sit on
the same virtual disk as the database (LD-2), so a single corrupted virtual disk loses both.

- **LP-D4:** after each successful nightly dump, the driver's `backup-copy` step copies the newest dump set out of
  the backup volume to a directory on a **different physical drive** of the workstation, verifies it against the
  manifest's hashes, and records the copy. It is a plain file copy made by harness code; it changes nothing in P1.
- It runs on the host, so it only happens when the driver's host-side schedule runs (§6.3).
- The copy contains raw CSE responses inside the dump. G-1's controls apply to it: it stays on the workstation, is
  not synced to any cloud folder, and the directory is excluded from any such sync. Encrypting that drive is the
  owner's choice (LP-R2); G-1 control 8 names "encrypted backups".
- P1's off-site units report `not_configured`, which is never a success, and LP's checks expect exactly that.
- The weekly restore check runs as P1 designed.

### 6.3 Being on, awake and on time

- **The schedule depends on the workstation.** CSE's `tradeSummary` serves only the latest session, so a session can
  be captured only on its own day (window 15:15 to 23:59 Colombo). If the workstation is off, asleep or Docker is not
  running through that window, the session is missed for ever. P3 records it as missed and never relabels it; the
  market series simply has a hole (Phase 3 treats it as an unknown date).
- **Clock.** The container's clock is the Docker virtual machine's (LD-4), which can drift after the host sleeps.
  MA §54 requires a synchronised clock before the first capture. LPC-5 compares the container's time with the host's
  and the host's synchronisation status before arming, and the host-side schedule repeats it daily and before each
  capture window, alerting on a difference above 2 seconds. P2's own E1 check refuses a snapshot whose latest trade
  is not on the stated date, which is the product's last line of defence against a wrong date.
- **A host-side schedule** (a Windows scheduled task that runs the driver's `tick`) does only harness work: the
  clock check, the disk guard, `backup-copy`, and a status line. It never starts a capture; the container's own
  systemd timers do that.
- **Supervision.** "So that you can also supervise it" (the owner, 2026-11-09): the driver's `status` gathers, read
  only, P1's backup status, P3's and HB-6's `status`, the alerts they list, disk headroom and the clock check into
  one record. P1's alert channel is still an open P1 decision; until it is decided, `status` is where alerts are
  seen.

---

## 7. Recorded differences of the local environment

Each is a way LP differs from the production target of MA §43. None is waived; each is verified at the server
deployment (milestone 14), as MA §43.1 lists.

| | Difference | Consequence |
|---|---|---|
| LD-1 | systemd runs in a container on the WSL2 kernel (staging D-01) | Timer and sandbox behaviour as staging validated, not as on Ubuntu's kernel |
| LD-2 | Backups share the virtual disk with the database; `CSE_ALLOW_SAME_DEVICE=1` (staging D-02) | Compensated in part by `backup-copy` to another physical drive (§6.2) |
| LD-3 | No `ufw`; no published port instead (staging D-03) | Inbound is closed by Docker, not by a host firewall |
| LD-4 | The clock is the Docker VM's (staging D-04) | LPC-5 and the daily check (§6.3) |
| LD-5 | Packages come with the accepted image, not from `provision.sh packages` at provisioning (staging D-05) | The package set is exactly what staging validated |
| LD-6 | Free space is read from Docker's virtual disk; `fsync` reaches a virtual disk on NTFS; host power loss untested (staging D-06, D-10) | The driver's host-side guard (§6.1) |
| LD-7 | No off-site backup (owner decision, accepted risk) | A lost workstation means redoing the backfill and contacting CSE again |
| LD-8 | Outbound restriction is a packet filter inside the container, or absent if LP-R4 so decides, not a server firewall | §5 |
| LD-9 | The host is a workstation that sleeps and restarts, not a server | Missed sessions are possible (§6.3) |
| LD-10 | Peer authentication never crosses a container boundary here: every service and every verification runs inside the one container | Same as the target in effect |

Staging's D-07 (the off-site test destination), D-08 (the R1 gate), D-09 (simulated downtime) and D-11 (no
designation) do not apply: LP has no off-site destination, no R1 gate, real time, and the owner's designation.

---

## 8. Checks (smoke tests and the §54 gates)

All run with no network attached, all are read-only except where stated, and each writes a record to
`D:\cse-prod\checks\`. They reuse the staging checks that are not staging-specific.

| ID | Check | Passes when |
|---|---|---|
| LPC-1 | Identity | The container runs the accepted image id; the revision label is the accepted commit; the application tree equals the commit's tree, allowing only the six root-owned bytecode files of staging finding F-1 |
| LPC-2 | Empty and clean | The migration ledger is 0001 to 0018 with 0006 unused and verifies; every data table is empty; no armed decision exists; the contact e-mail is empty |
| LPC-3 | Roles and append-only | Staging's `roles` check on LP's own cluster; because the tables are empty, it also inserts and rolls back a probe row per append-only table inside one transaction |
| LPC-4 | Preflights | P1 `verify` (tolerating only the recorded differences), the migration runner, the R-F4 preflight by both routes and the full `verify` as `cse_verifier` (runbook §12 steps 2 to 5), P3, HB-6, F6.4 |
| LPC-5 | Clock | Container and host times agree within 2 s; the host reports itself synchronised |
| LPC-6 | Units | Every frozen unit is installed and enabled as provisioned; a disarmed wake-up of P3 and of HB-6 does nothing and contacts nobody |
| LPC-7 | Backups | A dump runs and verifies; the backup ledger records it; off-site is `not_configured`; `backup-copy` produces a verified copy on the other drive; the restore check restores that dump into a throwaway cluster and matches |
| LPC-8 | Isolation | `docker inspect`: no network, no published port, no bind mount, no Docker socket, exactly the approved capabilities, the four LP volumes only. After `net-attach`: exactly the one network |
| LPC-9 | Recreate | After a recorded recreate: the same system identifier, an identical ledger, identical row digests |
| LPC-10 | Disk | Host drive and virtual disk above their floors; the headroom estimate is recorded |
| LPC-11 | No test data | No row carries a synthetic marker; no staging volume or evidence path is referenced |
| LPC-12 | Egress filter (if LP-D3 stands) | With the network attached and nothing armed: a connection to an address outside the allowed set is dropped; DNS resolves. No request is sent to CSE by this check |

The §54 gates map as: frozen build validated in staging (milestones 8, 9); provisioned (LP-3); smoke tests (LPC-1
to LPC-11); clock (LPC-5); backups (LPC-7); scheduler verified (LPC-6); governance recorded (G-1, and the owner's
records of §5); request budget confirmed (the P3 arming's own arguments); tests proven offline (staging's ST-25);
no test contacts CSE (no network).

---

## 9. Sequence

Each step needs the workstation. Each is the owner's to authorize; LP-1 can be prepared in the cloud.

| Step | Content | Exit |
|---|---|---|
| LP-1 | The driver `ops/localprod/localprod.sh` and its inner checks, written from the staging harness; unit tests with a stub `docker` (no bind mount, no staging object, no CSE host, nothing destructive, network only through `net-attach`) | Tests pass in CI. No container is involved |
| LP-2 | **Rehearsal** on throwaway objects (`cse-prod-reh-*`), no network: create, provision, LPC-1 to LPC-11, a recreate on the same volumes, a stop and start, a kill and start, an aged timer stamp. Then remove the rehearsal objects | Every check recorded. The existing-cluster question of §3.3 answered |
| LP-3 | **Provision the real environment**, no network | LPC-1 to LPC-4, LPC-6, LPC-8 |
| LP-4 | **Smoke tests**: all of LPC-1 to LPC-11 on the real environment; the host-side schedule installed | The owner's acceptance of milestone 10 |
| LP-5 | *(milestone 11)* The owner's records; the contact e-mail; `net-attach`; LPC-12; then the HB-P1 chain by the owner's arming | — |
| LP-6 | *(milestones 11 to 13)* Operation under the owner's arming decisions, supervised through `status` | — |

LP-5 and LP-6 are named so the order is clear. This design does not authorize them.

---

## 10. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0014)

| ID | Decision | Reason |
|---|---|---|
| LP-D1 | LP runs the accepted staging host image by image id; it is not rebuilt | The accepted build is what staging validated; a rebuild can differ (ST-26) |
| LP-D2 | One long-lived container on four named volumes, rebuildable by re-provisioning; restart policy `unless-stopped` | The frozen scripts are idempotent; captures must resume after a restart |
| LP-D3 | An outbound packet filter inside the container, as an environment control | The runbook asks for outbound access to CSE only; the product is unchanged |
| LP-D4 | A verified copy of each nightly dump to another physical drive | Compensates for the shared virtual disk; changes nothing in P1 |
| LP-D5 | P1's weekly restore check runs as designed; staging's R1 gate is not installed | It is the product's behaviour and never touches production |
| LP-D6 | No network until `net-attach`, which requires the owner's recorded decision | Smoke tests cannot contact CSE by construction |
| LP-D7 | The LP driver lives outside the deployed tree | Adding harness code must not change the deployed build |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| LP-R1 | Disk: a local dump retention policy, or more room (with HB-Q9) | HB-S4 at the latest; better before LP-3 |
| LP-R2 | Which physical drive receives the dump copies, and whether it is encrypted | LP-3 |
| LP-R3 | How the workstation is kept on and awake through capture windows (power settings, sleep) | The first capture |
| LP-R4 | If the packet filter proves unreliable: run without it, as a recorded difference | Only if LP-2 shows the need |
| LP-R5 | Authorization of each of LP-2, LP-3, LP-4, and acceptance of milestone 10 | Each step |
| LP-R6 | Everything that permits CSE contact: HB-X2(a) and (b), the contact e-mail, every arming | Milestone 11 |

---

## 11. What this design does not cover

- Milestone 14, the move to the server: how the locally proven database is transferred, which §54 gates re-run, and
  the verification of every recorded difference. A separate design.
- The runbook §6 change that unblocks backup checkpoints (REG-B27), and the audit of the isolated-restore procedure
  (REG-B01). Separate items, both needed before the pilot's checkpoint.
- Phase C of staging (ST-30, ST-31) and the owner's acceptance of staging and of the build (milestones 8 and 9),
  which come before any of this.

## 12. Audit record

*(Filled in by the audit under C-0005.)*
