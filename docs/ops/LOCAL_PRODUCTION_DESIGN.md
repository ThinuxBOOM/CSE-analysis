# Milestone 10: the local production environment (design)

**Status: DESIGN, revision 2 (2026-11-12). Not built. Not accepted.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no build, no deployment,
no arming, no capture and no CSE contact. Everything in it that runs, runs on the owner's workstation and waits for
it.

*Revision 2 replaces revision 1 (commit `1acfb15`), which its audit failed with one Blocker and seventeen Major
findings (§12). The Blocker: revision 1 called the container "rebuildable by re-provisioning", but in a new container
the frozen provisioning script would try to create a cluster over the existing production data directory and abort.
PostgreSQL's configuration directory is now on its own volume so that path is never reached (§3.3). Other main
changes: the network is attached between two bridges, which Docker supports, and is rehearsed (§5, §9); the egress
filter's cost in missed sessions is stated and the choice returned to the owner; the product's own clock gate is
respected; the weekly restore check is the owner's decision, not a delegated one; and what a recreate loses is listed
in full.*

**What it designs:** the environment the owner decided on 2026-11-09 (Master Architecture (MA) §43.1, "Local
production first"): a local production environment in Docker on the owner's workstation, separate from staging, with
its own persistent volumes, where the live prerequisites, the pilot and the bulk backfill run (milestones 11 to 13)
before any server deployment (milestone 14).

**Governing records:** MA §43, §43.1, §46 to §50, §54; `docs/R_F4_DESIGN.md` §10 (milestones 9 to 14);
`docs/ops/STAGING.md` (the harness this environment derives from, and its recorded differences D-01 to D-11);
`docs/ops/P1_PLATFORM.md`, `docs/ops/P2_MARKET_CAPTURE.md`, `docs/ops/P3_SCHEDULER.md`,
`docs/ops/PHASE2_BACKFILL.md`; `docs/governance/G-1_CSE_DATA_USE.md`. Where this document and those differ, they win.

**Identifiers.** The environment is "LP". Steps are LP-1 to LP-6 (§9), checks LPC-*n* (§8), recorded differences
LD-*n* (§7). Decisions are LP-D*n* ([delegated]) and LP-R*n* (reserved for the owner) (§10).

---

## 1. Conclusion

1. **LP is one long-lived container running the image staging accepted**, provisioned by the frozen scripts, on its
   own named volumes, with a fixed hostname. The product is unchanged: LP adds no migration, role, grant or entry
   point to it and edits no frozen unit. LP does add things **around** the product, each recorded as a difference:
   a driver on the host, a host-side schedule, and (if the owner keeps it) one environment unit for the outbound
   filter (§5).
2. **The deployed build is the accepted one, by image id, and a saved copy of that image is kept.** The LP driver is
   harness code outside the deployed tree, as the staging driver is.
3. **It starts on a network with no route out.** Provisioning, the rehearsal and the smoke tests run there. An
   outbound network is attached only by a separate driver step that requires the owner's recorded decision.
4. **Removing the container is a recovery procedure, not routine.** The data, the PostgreSQL configuration and the
   logs are on volumes; the rest is rebuilt by the frozen scripts. What is lost is listed (§3.3).
5. **Three things on the workstation need the owner before live work, and none is a software problem:** disk space,
   the workstation being on and signed in through capture windows, and its clock (§6).
6. **What this design does not yet cover blocks later milestones, and says so:** moving to a newly accepted build
   mid-campaign, and recovery from a backup copy (§11).

---

## 2. What LP must be, from the governing records

| Requirement | Source | Where met |
|---|---|---|
| Separate from staging, own persistent volumes; staging evidence never copied in | MA §43.1 | §3, §4 |
| The exact build accepted at milestone 9 | MA §43.1 | §3.1 |
| PostgreSQL 17; the roles and grants of §50; migrations only through P1's runner; spool and backup semantics; local dumps and the backup ledger | MA §43.1 ("Kept") | §3.4 |
| A deployment rehearsal, provisioning and smoke tests without CSE contact | MA §43.1, §54 | §9 |
| Every gate of §54 before the first capture, and the evidence the first capture must produce | MA §54 | §8, LP-5 |
| CSE contacted only through the governed transport, never ad hoc from the host | MA §43.1 | §5 |
| No off-site backup in the local phase, at the owner's accepted risk | Owner decision 2026-11-09 | §6.2 |
| G-1's controls apply to the workstation | MA §43.1, §46 | §5, §6.2 |
| Approved outbound access to CSE's API and CDN only | `docs/ops/PHASE2_BACKFILL.md` §12 | §5 (the owner's choice, LP-R4) |
| Poppler 24.02.0; the dedicated temporary root; at least 20 GiB free on the database disk | `docs/R_F4_DESIGN.md` §10 row 10 | §3.1, §3.3, §6.1 |

---

## 3. The environment

### 3.1 The image

LP runs **the staging host image of the accepted commit, by image id**, not a rebuild: a rebuild from a moving
package archive can differ (staging's ST-26 found two changed package revisions), and the accepted build is what
staging validated.

- **The image is saved.** Before LP-3 the driver runs `docker save` of the accepted image to the evidence drive and
  records its SHA-256. An image exists only in Docker Desktop's store, which a prune or a reset destroys, and it
  cannot be rebuilt identically. LPC-1 checks the running image id against the saved one.
- LPC-1 also compares the image's own package manifest with `ops/staging/packages.lock`.
- If milestone 9 accepts a commit other than the one Phase B staged (`af1fdd16cc40`), that commit is staged first
  (MA §43.1), and LP uses that campaign's image.

### 3.2 The container

| Property | Value | Reason |
|---|---|---|
| Name, hostname | `cse-prod-host`, hostname `cse-prod` | P3's and HB-6's arming record the host and refuse any other (`socket.gethostname()`, `platform.node()`). The server at milestone 14 will have its own name and need its own arming |
| Privilege | Staging's rung L1 (`CAP_SYS_ADMIN`, private cgroup namespace), plus `CAP_NET_ADMIN` only if the outbound filter is kept (§5). Not `--privileged` | The rung staging established |
| Mounts | `--tmpfs /run --tmpfs /run/lock` as at staging; the named volumes of §3.3. No host bind mount, no Docker socket. Staging's small test disk is not mounted | Staging's boundary |
| Network | Created on `cse-prod-internal`, a user-defined bridge made with `--internal`: no route out. No published port. The outbound bridge is connected later (§5) | Docker refuses to connect a network to a container created with network mode `none`; bridge to bridge is supported |
| Restart policy | `unless-stopped` | After a restart the scheduler must come back by itself. Safe: nothing contacts CSE unless armed |
| Memory, processes | 3 GiB with swap equal to memory, 4096 processes, stop timeout 400 s | Staging ran at 2 GiB on a host whose Docker VM has 8 GB and whose free RAM fell to a few per cent. Confirmed or changed in the rehearsal |
| Labels | `cse.env=production-local`, the revision | No staging command can select it |

### 3.3 Volumes, and what a recreate loses

| Volume | Mounted at | Holds |
|---|---|---|
| `cse-prod-pgdata` | `/var/lib/postgresql` | The PostgreSQL 17 data directory: every production row |
| `cse-prod-pgconf` | `/etc/postgresql` | The cluster's configuration (`postgresql.conf`, `pg_hba.conf`, `pg_ident.conf`), which Debian's layout keeps outside the data directory |
| `cse-prod-backup` | `/srv/cse-backup` | Local dumps, the spool, backup status, restore scratch |
| `cse-prod-etc` | `/etc/cse` | The environment files, including `capture.env` with the contact e-mail |
| `cse-prod-state` | `/var/lib/cse-backfill` | HB-6's reports |
| `cse-prod-log` | `/var/log` | The systemd journal and PostgreSQL's log: the record of every wake-up, refusal and backup run |

**Why the configuration has its own volume.** In a new container without it, `pg_lsclusters` lists nothing, so
`provision.sh postgres` takes its create branch and runs `pg_createcluster 17 main` against the existing data
directory. That command expects to find the configuration files inside the data directory, does not, fails, and its
cleanup calls `pg_dropcluster` on the cluster name. By the auditor's reading of the package's code the data survives,
but provisioning aborts and nothing in the design could bring the cluster back. With the configuration on a volume,
the cluster is listed and the create branch is never reached.

- **A hard rule of the driver:** before any provisioning step it checks that either both PostgreSQL volumes are empty
  (a first provision) or both hold the cluster. One populated and the other empty is refused. `pg_createcluster` is
  never to run against a populated production data directory.

**Lost when the container is removed** (kept across stop and start):

| Lost | Consequence | Restored by |
|---|---|---|
| Operating-system users and groups | File ownership on the volumes is by number. The same image and step order reproduce the same numbers; a different image may not | Provisioning; LPC-9 compares uids and gids with the owners of the volumes' files before anything starts |
| systemd unit files and enablement under `/etc/systemd/system` | No timers | Provisioning's `units` steps |
| Timer stamps under `/var/lib/systemd/timers` | `Persistent=true` catch-up of the dump, restore-check and status timers is forgotten once | The recreate procedure runs a dump by hand |
| `/var/lib/cse-scheduler` (P3's state directory and its capture-finished marker) | None that matters: the state of record is in PostgreSQL | Provisioning |
| The dedicated temporary root, `/tmp/cse-backfill` inside the backfill unit's private `/tmp` | None: it is temporary by design, and the runbook forbids making it persistent. Its free space is the container layer's (LPC-10) | — |
| The outbound filter's unit and rules, if kept | No filter until reinstalled | The driver's `filter-install` step |
| `/etc/machine-id`; the six root-owned bytecode files of staging finding F-1 | None | Provisioning |
| The network attachment and the container's flags | — | The recreate procedure |

**Kept, because it is in PostgreSQL or on a volume:** every arming decision, the backup ledger, the migration ledger,
the contact e-mail, any credentials file under `/etc/cse`.

**The recreate procedure** (owner-authorized each time; rehearsed in LP-2; the driver never removes the production
container outside it):

1. The owner disarms P3 and Phase 2, or records that they are disarmed. A recreated container would otherwise come up
   **armed**, because arming lives in the database and provisioning enables the timers at once.
2. A fresh dump, verified, and copied out (§6.2).
3. The outbound network is disconnected. The container is stopped and removed by its exact name.
4. A new container from the same saved image, on the internal network and the same volumes. LPC-9's ownership check.
5. Provisioning with the explicit step lists of §3.4. A dump by hand. LPC-1 to LPC-11.
6. Outbound access again only by a **new** owner record (§5), then re-arming by the owner.

### 3.4 Provisioning

Inside the container, the frozen scripts unmodified, by explicit step lists, in staging's order, with
`CSE_ALLOW_SAME_DEVICE=1` exported on every call (it is not stored; LD-2):

```
ops/provision/provision.sh            preflight users dirs postgres bootstrap migrate units
ops/provision/provision_scheduler.sh  preflight migrate dirs units verify
ops/provision/provision_backfill.sh   preflight migrate dirs units verify
```

- Migrations 0001 to 0018 through P1's runner only.
- `provision.sh firewall`, `all` and `selftest` are not run. P1's own `verify` step is replaced by LPC-4, which runs
  the same verifier and tolerates only the recorded differences, not the clock (§6.3).
- Staging's R1 gate is installed **exactly as at staging** and stays closed unless the owner decides otherwise
  (LP-R7): P1's weekly restore check does not run by itself.
- The contact e-mail stays empty after provisioning. Every CSE-contacting command refuses without it.
- Commands that record an operator read `SUDO_USER`. Run through `docker exec` as root they would record "root" on
  every owner decision, so the driver passes the owner's name in that variable for owner-path commands (LD-11).

---

## 4. Separation from staging

- Different container name, hostname, labels, networks and volume names. The LP driver acts only on objects labelled
  `cse.env=production-local` (or `production-local-rehearsal` in LP-2), and a unit test asserts that neither driver's
  commands can name the other's objects.
- Evidence goes to `D:\cse-prod\`, never to `D:\cse-staging\` and never into the vault. It holds no CSE response and
  no secret, and the contact e-mail is redacted from everything the driver writes there.
- Nothing is copied from a staging volume into an LP volume. HB-P1 is satisfied only by LP's own first governed
  capture (MA §43.1).
- The synthetic seed is never run in LP (LPC-11).
- **What the driver may remove:** rehearsal objects, by exact name and rehearsal label, at the end of LP-2. The
  production container, only inside the recreate procedure. Volumes and the saved image, never.

---

## 5. Network and CSE contact

**Before the owner's decisions.** The container is on a bridge with no route out, so provisioning, the rehearsal and
the smoke tests cannot reach CSE (MA §54: "no test accidentally contacts CSE").

**`net-attach`** connects the container to `cse-prod-egress`, an ordinary user-defined bridge. It refuses without a
**network authorization**, a file the owner writes, validated like staging's restore authorization
(`docs/ops/STAGING.md` §11): its type and id; its scope (`market` for P2 and P3, `phase2` for discovery and
documents); the container name and image id; the decision it rests on (HB-X2(b) for `market`; HB-X2(a) as well for
`phase2`); the owner's words; a decision time and an expiry at most 24 hours later; single use, recorded in an
append-only log. The driver decides nothing; it refuses to attach without the record. `net-detach` needs no
authorization.

**Inbound.** No port is published (LD-3).

**What can make a request.** It is not true that only the governed transport can. The image is the whole commit: it
contains the frozen Stage E client and the standalone F1 to F5 command-line tools, which predate the governed
transports, and `curl` is installed. The controls are these:

- nothing ungoverned is installed as a unit, a timer or an operator wrapper: the only scheduled entry points are P3's
  and HB-6's, which go through the governed transports and refuse unless armed;
- the driver's `exec` step runs only an allowlist of commands (the frozen wrappers under `ops/bin` and the read-only
  checks), and a unit test asserts it has no other way to run a command in the container;
- beyond that, root inside the container is governed by procedure, as root on a server would be.

**The outbound filter, its cost, and the owner's choice (LP-R4).** The runbook asks for outbound access "to CSE's API
and CDN only". On a server that is a firewall rule. Here the equivalent is a packet filter inside the container.

- **How it would be built.** With `iptables`, the only packet-filter tool in the accepted image (no package can be
  added to it). An environment unit, not a change to any frozen unit, with its own timer: an OUTPUT policy that
  accepts loopback and Docker's embedded resolver, accepts TCP 443 to the current addresses of `www.cse.lk` and
  `cdn.cse.lk`, and **rejects** everything else (reject, not drop, so that a refused connection fails at once).
  Docker's own NAT chains for the resolver are left alone, and IPv6 is rejected entirely. It needs `CAP_NET_ADMIN`.
- **Its cost.** The address set is refreshed from DNS on a timer. A content-delivery network changes addresses. If
  the set is stale, legitimate requests are refused, the transport records network errors against the day's request
  budget, and five in a row open its circuit breaker and end the run. For market capture that can lose the session,
  because a session can only be captured on its own day.
- **The choice.** Keep the filter and accept that risk, or run without it and record the difference (LD-8), relying
  on the controls above until a real firewall exists at the server. LP-D3 no longer decides this: revision 1 chose
  the filter without stating the cost. The rehearsal measures how the filter behaves (LP-2) so the owner decides on
  evidence.

**The host's own network.** A VPN or a Docker Desktop proxy on the workstation would change the path requests take.
The transports ignore proxy settings, but a system-wide VPN is below them. G-1 control 6 forbids proxies and IP
rotation, so `tick` (§6.3) records whether a VPN adapter is up and alerts if one is while armed.

**No ad hoc contact from the host.** The driver has no command that fetches anything from CSE, and its unit tests
assert that no CSE hostname appears in it outside the filter's rule set and comments.

---

## 6. The workstation's real constraints

### 6.1 Disk

- **Dumps are full copies, never pruned, and more frequent than nightly:** P1's timer runs one each night, and P3
  triggers another after every capture. A local retention policy is an open P1 owner decision.
- During staging, drive D: had about 30 GB free. Phase 2's design puts the database at a few gigabytes after the bulk
  backfill. Two or more full dumps a day of a database that size fill the remaining space within days.
- **Other consumers of the same space:** the weekly restore check, if enabled, needs about the database's size again
  for its throwaway cluster; the dump copies of §6.2 accumulate on their own drive; logs grow (§6.4).
- **Where the space is.** Docker's virtual disk file lives by default under the user profile on C:, not on D:, and it
  grows but never shrinks. HB-6's 20 GiB gate reads free space inside that virtual disk, not on the host drive that
  holds it (LD-6). The driver records the virtual disk's actual location and size at LP-2.
- **What the driver can and cannot do.** Its guard refuses the driver's own steps below a floor, and `status` reports
  dump count, total size and days of headroom. It does **not** stop the product: the container's timers keep dumping
  until the host drive is full, and a full host drive under a virtual disk can corrupt the database and the dumps
  together. Stopping a backup timer is an owner decision, not a driver action.
- **So the owner decides, before the first arming that grows data** (the P3 arming for small daily growth; the HB-S3
  pilot at the latest for real growth): a retention policy, or more room, or both (LP-R1). HB-Q9 asks the same
  question before HB-S4; on this workstation it cannot wait that long.

### 6.2 Backups without an off-site copy, on one virtual disk

The owner accepted having no off-site backup locally. Separately, the dumps share a virtual disk with the database
(LD-2), so one corrupted virtual disk loses both.

- **`backup-copy`.** After a dump whose backup-ledger row is `succeeded`, the driver copies that dump set and the
  spool entries added since the last copy out of the backup volume to a directory on a **different physical drive**,
  verifies the dump against its manifest and the manifest against its checksum file, and records the copy.
- **Encryption is required, or the owner records a deviation.** The copy holds raw CSE responses. G-1 control 8 names
  "encrypted backups" as where they may live outside the database and the spool. The target must therefore be an
  encrypted volume (BitLocker on that drive), or the owner records in G-1 that plaintext local copies are accepted
  (LP-R2). The directory is excluded from every cloud-sync client. The copies need their own retention rule (LP-R1).
- **Recovery from a copy is not designed here.** Restoring into production is P1 §8 B, which needs the owner's
  authorization and, for the verification before it, the isolated-restore procedure, which is audited with
  conditions and not approved (`docs/ops/audits/AR-C-0001_ISOLATED_RESTORE.md`). Until that route exists, the copies
  are insurance that cannot yet be claimed on (§11).
- P1's off-site units report `not_configured`, which is never a success. So P1's backup status is **permanently in
  alert** in LP. `status` therefore shows alerts against a stated expected baseline (exactly the off-site ones), so
  that a new alert is not hidden among the expected ones.

### 6.3 Being on, signed in, awake and on time

- **A session can be captured only on its own day** (window 15:15 to 23:59 Colombo). If the workstation is off,
  asleep, or Docker is not running through that window, the session is missed for ever.
- **Docker Desktop runs in the signed-in user's session.** After an unattended restart (a Windows update, for
  example) the engine is down until someone signs in, and `unless-stopped` restarts nothing until the engine runs.
  P3 also refuses to act when the gap since the last capture exceeds its catch-up limit, so after a long absence
  capture stays stopped until the owner acts.
- **The product has its own clock gate, and LP respects it.** An armed P3 or HB-6 wake-up refuses unless the system
  reports its clock synchronised, and refuses if the clock moved backwards by more than five minutes. In the
  container that flag is the WSL2 kernel's (LD-4). If it drops after sleep or resume, every capture is refused and
  the session is missed; if it says yes while the virtual machine's clock lags, the gate passes wrongly.
  - LPC-5 therefore requires the flag to be `yes` **inside the container** and the container's time to agree with
    the host's within 2 seconds, and LPC-4 does not tolerate a clock failure (staging's probe did).
  - Sleep and resume are rehearsed in LP-2, to see what the flag and the clock actually do on this machine.
  - The container cannot set the clock. The remedy for drift is on the host (restarting Docker Desktop
    resynchronises the virtual machine and restarts the container), and it is the owner's action.
- **`tick`, a host-side scheduled task**, does only harness work: the clock check; the disk guard; `backup-copy`;
  whether the container is running and its timers active; whether a VPN is up; a status line. It never starts a
  capture. When something is wrong it writes an alert file and exits non-zero. **Who is told, and how, is the
  owner's choice** (LP-R3); P1's alert channel is itself an open decision. Without one, alerts are seen only when
  someone looks.
- **What the owner must set up (LP-R3):** power and sleep settings; automatic sign-in or another way to have Docker
  running after a restart; the Windows update restart policy; Docker Desktop's and WSL's own automatic updates, which
  change the engine and the kernel underneath the accepted environment (LD-12).

### 6.4 Logs

The journal and PostgreSQL's log are on `cse-prod-log`. Nothing in the accepted image rotates PostgreSQL's log (no
`logrotate` or `cron` is installed), and journal limits are unset. The driver sets a journal size limit through an
environment configuration file, reports both sizes in `status`, and the rehearsal measures their growth.

---

## 7. Recorded differences of the local environment

None is waived. Each is verified at the server deployment (milestone 14) unless marked as a property of the
workstation that simply ends there.

| | Difference | Consequence |
|---|---|---|
| LD-0 | Ubuntu 24.04's user space and PostgreSQL 17 run in a container on the WSL2 kernel, not natively | As MA §43.1 lists |
| LD-1 | systemd runs in a container at rung L1 (staging D-01) | Timer and sandbox behaviour as staging validated |
| LD-2 | Backups share the virtual disk with the database; `CSE_ALLOW_SAME_DEVICE=1`, which P1 labels for test hosts (staging D-02) | Compensated in part by `backup-copy` (§6.2) |
| LD-3 | No `ufw`; no published port instead (staging D-03) | Inbound closed by Docker |
| LD-4 | The clock and its synchronisation flag are the Docker VM's (staging D-04) | §6.3 |
| LD-5 | Packages come with the accepted image, not from `provision.sh packages` (staging D-05) | Exactly what staging validated |
| LD-6 | Free space is read from Docker's virtual disk; `fsync` reaches a virtual disk on NTFS; host power loss untested (staging D-06, D-10) | §6.1 |
| LD-7 | No off-site backup (owner decision, accepted risk) | A lost workstation means redoing the backfill |
| LD-8 | Outbound restriction is a packet filter inside the container with one extra capability, or none (LP-R4) | §5 |
| LD-9 | The host is a workstation that sleeps, restarts and needs a signed-in user. Ends at milestone 14 | Missed sessions are possible (§6.3) |
| LD-10 | Every service and verification runs inside the one container, so peer authentication never crosses a container boundary | Same as the target in effect |
| LD-11 | Operator identity comes from a variable the driver sets, not from `sudo` | §3.4 |
| LD-12 | Windows, Docker Desktop and WSL update the kernel and engine under the accepted environment | `tick` records their versions; a change is reported |
| LD-13 | Harness additions around the product: the driver, `tick`, a journal size limit, the filter unit if kept | §1 |

Staging's D-07 (the off-site test destination), D-09 (simulated downtime) and D-11 (no designation) do not apply.
D-08 (the R1 gate) applies unchanged unless LP-R7 lifts it.

---

## 8. Checks (smoke tests and the §54 gates)

All run with no outbound network and are read-only in the real environment; the write probes run only in the
rehearsal. Each writes a record to `D:\cse-prod\checks\`.

| ID | Check | Passes when |
|---|---|---|
| LPC-1 | Identity | The running image id equals the saved image's; the revision label is the accepted commit; the package manifest equals `packages.lock`; the application tree equals the commit's tree, allowing only the six bytecode files of staging finding F-1 |
| LPC-2 | Clean | The migration ledger is 0001 to 0018 with 0006 unused and verifies. **The CSE-data tables are empty:** market capture runs, responses and observations; filings, classifications, candidates, F4, F6 and F8 tables; the backfill work items, attempts and outcomes; issuers. Expected to hold rows already: the migration ledger, the backup ledger, and the scheduler's and runner's wake-up tables (disarmed wake-ups write a row). No armed decision exists; the contact e-mail is empty |
| LPC-3 | Roles and append-only | In the real environment, by catalogue only: every append-only trigger present and enabled, every privilege as designed. In the rehearsal, staging's write-and-refuse probes as well |
| LPC-4 | Preflights | P1's verifier with no clock tolerance; the migration runner; the R-F4 preflight by both routes, clean; P3, HB-6 and F6.4 preflights. The R-F4 full `verify` on an empty database is recorded as **vacuous**: it shows the check runs as `cse_verifier`, not that any data verifies (staging's rule that an empty database is not a pass is why this is not called one) |
| LPC-5 | Clock | Inside the container the synchronisation flag is `yes`; container and host times agree within 2 s |
| LPC-6 | Units | Every frozen unit installed and enabled as provisioned; a disarmed wake-up of P3 and of HB-6 does nothing and contacts nobody; the R1 gate is closed |
| LPC-7 | Backups | A dump runs and verifies; the backup ledger records it; off-site is `not_configured`; `backup-copy` produces a verified copy on the other drive; no leftover throwaway cluster under `restore-scratch` |
| LPC-8 | Isolation | `docker inspect`: only the internal network, no published port, no bind mount, no Docker socket, exactly the approved capabilities, exactly the six LP volumes. After `net-attach`: the internal and the egress network and nothing else |
| LPC-9 | Recreate | Before: the uids and gids of the service users equal the owners of the volumes' files. After: the same system identifier, an identical ledger, identical row digests |
| LPC-10 | Disk | The host drives and the virtual disk above their floors; the temporary root's free space and mode; the headroom estimate recorded |
| LPC-11 | No test data | No synthetic marker anywhere; no staging volume or evidence path referenced |
| LPC-12 | Outbound | With the egress network attached and nothing armed: DNS resolves. If the filter is kept, a connection to an address outside the allowed set is rejected at once. No request is sent to CSE by this check |

**The §54 gates.** Frozen build validated in staging (milestones 8, 9); provisioned (LP-3); smoke tests (LPC-1 to
LPC-11); clock (LPC-5); backups (LPC-7); scheduler verified (LPC-6); governance recorded (G-1, and the network
authorization of §5); request budget confirmed (the owner's arming arguments, checked against P3's own defaults at
LP-5); tests proven offline (staging's ST-25); no test contacts CSE (no route out). **The evidence the first capture
must produce** (request discipline, archive integrity, session and date correctness, observation completeness,
canonicalisation state, backup state, auditability) is collected at LP-5 from P2's own run report, `verify-archive`
and the backup ledger, as one record for the owner.

---

## 9. Sequence

Each step needs the workstation and the owner's authorization; LP-1 can be prepared in the cloud.

| Step | Content | Exit |
|---|---|---|
| LP-1 | The driver `ops/localprod/localprod.sh`, its checks and `tick`, written from the staging harness; unit tests with a stub `docker` (no bind mount, no staging object, no CSE host, the `exec` allowlist, removal only by exact name and label, outbound only through `net-attach` with a valid authorization) | Tests pass in CI |
| LP-2 | **Rehearsal** on throwaway objects (`cse-prod-reh-*`, hostname `cse-prod-reh`, label `production-local-rehearsal`): create, provision, LPC-1 to LPC-11 with the write probes; **the recreate procedure on a copy**; stop and start; kill and start; an aged timer stamp; a Docker Desktop restart (staging's OD-16, never run); host sleep and resume with the clock check; `net-attach` to a second **internal** bridge with a local stub, LPC-12, the filter's behaviour, and a restart with the network attached; memory and log growth measured. Then the rehearsal objects are removed | Every check recorded. The owner has the evidence for LP-R4 and LP-R7 |
| LP-3 | **Provision the real environment**, no route out; the image saved first | LPC-1 to LPC-4, LPC-6, LPC-8, LPC-10 |
| LP-4 | **Smoke tests**: LPC-1 to LPC-11 on the real environment; `tick` installed | The owner's acceptance of milestone 10 |
| LP-5 | *(milestone 11)* The network authorization; the contact e-mail; `net-attach`; LPC-12; the HB-P1 chain by the owner's arming; the first-capture evidence record | — |
| LP-6 | *(milestones 11 to 13)* Operation under the owner's arming decisions, supervised through `status` | — |

LP-5 and LP-6 are named so the order is clear. This design does not authorize them.

---

## 10. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0014 and C-0020)

| ID | Decision | Reason |
|---|---|---|
| LP-D1 | LP runs the accepted staging host image by image id, and a saved copy of it is kept | The accepted build is what staging validated; an image is otherwise unrecoverable |
| LP-D2 | One long-lived container on six named volumes, with PostgreSQL's configuration on its own volume; restart policy `unless-stopped`; removal only through the recreate procedure | The create branch of provisioning must never meet a populated data directory |
| LP-D3 | Withdrawn. The outbound filter is the owner's choice (LP-R4), decided on the rehearsal's evidence | Revision 1 chose it without stating its cost in missed sessions |
| LP-D4 | A verified copy of each successful dump and of the new spool entries to another physical drive | Compensates for the shared virtual disk; changes nothing in P1 |
| LP-D5 | Withdrawn. The weekly restore check is the owner's decision (LP-R7); the R1 gate stays closed | The owner has kept every restore under explicit authorization, and P1's restore check has never run in a host container on this platform |
| LP-D6 | The container starts on a bridge with no route out; outbound only through `net-attach` with a network authorization | Smoke tests cannot contact CSE by construction |
| LP-D7 | The LP driver lives outside the deployed tree and runs only an allowlist of commands in the container | Harness code must not change the deployed build or become a way round the governed transports |
| LP-D8 | Write probes run only in the rehearsal; the real environment is checked read-only | A harness must not write to the production database |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| LP-R1 | Disk: a local dump retention policy and a retention rule for the copies, or more room (with HB-Q9) | The first arming that grows data; LP-3 if possible |
| LP-R2 | Which physical drive receives the copies; that it is encrypted, or a recorded acceptance in G-1 of plaintext local copies | LP-3 |
| LP-R3 | Keeping the workstation on, signed in and awake through capture windows; the update policies; who is alerted and how | The first capture |
| LP-R4 | The outbound filter: keep it and accept the missed-session risk, or run without it as a recorded difference | LP-5, on LP-2's evidence |
| LP-R5 | Authorization of each of LP-2, LP-3 and LP-4, of any recreate, and acceptance of milestone 10 | Each step |
| LP-R6 | Everything that permits CSE contact: HB-X2(a) and (b), the contact e-mail, each network authorization, every arming | Milestone 11 |
| LP-R7 | Whether P1's weekly restore check runs in LP (lifting the R1 gate), after staging's ST-30 has shown it working | Any time after ST-30 |

---

## 11. What this design does not cover

- **Moving to a newly accepted build while data exists.** The release sequence expects pilot defects to go back
  through change control and staging before HB-S4, which means a new image mid-campaign. The recreate procedure is
  the mechanical basis, but the full procedure is not designed: the new image's identity checks, pending migrations
  through the runner, HB-6's arming against the running version tuple, and rollback. **It must be designed and
  rehearsed before the pilot's exit (milestone 12).**
- **Recovery from a backup copy** (§6.2), which waits for the isolated-restore procedure's approval and a production
  form of it.
- **Milestone 14**, the move to the server.
- The runbook §6 change (REG-B27) and Phase C of staging (ST-30, ST-31), which come first.

## 12. Audit record

**Revision 1 (commit `1acfb15`): audited 2026-11-12 (cloud, fresh-context agent; C-0005). Verdict: FAIL**, one
Blocker, seventeen Major, nine Minor, one Note. The auditor read the frozen provisioning scripts, the units, the
transports and the installed `pg_createcluster` (the same package version as the image's), and ran nothing against
Docker. It verified the design's statements about the CSE hosts, the 20 GiB gate, dumps never being pruned, the
off-site status and that the restore check never touches the production cluster. Dispositions in revision 2:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Blocker | A new container would run `pg_createcluster` over the existing data directory and abort | **Fixed.** Configuration on its own volume; the both-or-neither rule (§3.3, LP-D2); rehearsed on a copy only |
| 2 | Major | The temporary root is not in `/var/lib/cse-backfill` | **Fixed.** §3.3; LPC-10 |
| 3 | Major | The list of what a removal loses was incomplete | **Fixed.** §3.3's table; logs on a volume; the ownership check in LPC-9 |
| 4 | Major | A recreated container comes up armed | **Fixed.** The recreate procedure (§3.3) |
| 5 | Major | No path to a newly accepted build | **Not designed; stated as a blocker for milestone 12** (§11) |
| 6 | Major | The accepted image was not preserved | **Fixed.** `docker save`, LPC-1 (§3.1) |
| 7 | Major | Attaching a network to a `none` container was assumed | **Fixed.** Internal bridge, then a second bridge (§3.2, §5) |
| 8 | Major | `net-attach` and the filter were never rehearsed | **Fixed.** LP-2 |
| 9 | Major | The filter's cost and tooling unstated | **Fixed.** §5; the choice returned to the owner (LP-R4) |
| 10 | Major | "LP adds no unit" contradicted the filter unit | **Fixed.** §1, LD-13 |
| 11 | Major | "Only the governed transport can make a request" was false | **Fixed.** §5 states the real controls; the `exec` allowlist (LP-D7) |
| 12 | Major | The product's clock gate ignored; LPC-4's tolerance contradicted it | **Fixed.** §6.3, LPC-4, LPC-5; sleep and resume in LP-2 |
| 13 | Major | Docker Desktop start-up and sign-in not covered | **Fixed.** §6.3, LP-R3; OD-16 in LP-2 |
| 14 | Major | The disk argument incomplete | **Fixed.** §6.1; LP-R1 moved earlier |
| 15 | Major | The weekly restore check enabled by a delegated decision | **Fixed.** LP-D5 withdrawn; LP-R7; the leftovers check in LPC-7 |
| 16 | Major | The harness wrote to the production database | **Fixed.** LP-D8; LPC-3 |
| 17 | Major | LPC-2 and LPC-4 not achievable as written | **Fixed.** LPC-2's table set; LPC-4's vacuous `verify` |
| 18 | Major | Plaintext dump copies against G-1 control 8; spool not copied; no recovery route | **Fixed** for encryption and the spool (§6.2, LP-R2); recovery **not designed** (§11) |
| 19 | Minor | Differences not recorded | **Fixed.** LD-0, LD-11, LD-12, LD-13; the VPN check (§5) |
| 20 | Minor | First-capture evidence had no step | **Fixed.** §8, LP-5 |
| 21 | Minor | Removal contradictory | **Fixed.** §4 |
| 22 | Minor | The `net-attach` gate under-specified | **Fixed.** The network authorization (§5) |
| 23 | Minor | Container flags | **Fixed.** §3.2 |
| 24 | Minor | Step lists | **Fixed.** §3.4 |
| 25 | Minor | Permanent alerts | **Fixed.** The expected baseline (§6.2) |
| 26 | Minor | Log growth | **Fixed.** §6.4 |
| 27 | Minor | The contact e-mail in evidence | **Fixed.** Redacted (§4) |
| 28 | Note | Hostname and the same-device flag | Stated (§3.2, §3.4) |

**Not verified by the audit, and so not established:** the runtime behaviour of `pg_createcluster` on the volume;
Docker's network connect behaviour and name resolution after it; `iptables` with the extra capability at rung L1 on
this kernel; the synchronisation flag and clock drift after sleep; where Docker's virtual disk is on the owner's
machine. Each is something LP-2 measures.

Revision 2 is to be audited again before LP-1 is built.
