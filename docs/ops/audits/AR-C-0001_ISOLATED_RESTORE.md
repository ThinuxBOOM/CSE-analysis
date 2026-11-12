# AR-C-0001: audit of the isolated-restore procedure (REG-B01)

**Audit:** 2026-11-12, cloud session, by a fresh-context agent that did not write the procedure
(`docs/governance/CLOUD_DECISION_LOG.md`, C-0005). This is the audit that `docs/ops/ISOLATED_RESTORE.md` has been
waiting for since 2026-11-04. It is a reading audit: no PostgreSQL was started, no restore was executed, no Docker
command was run. One offline call exercised the pure parser and gate functions.

**Recommendation: APPROVE WITH CONDITIONS.** The owner's approval is still required and is not given here. REG-B01,
REG-B04, REG-B06, REG-B27, REG-B31 and REG-B34 stay OPEN. Runbook §6 stays BLOCKED.

**Scope read:** `docs/ops/ISOLATED_RESTORE.md`; `ops/restore/isolated_restore.py`; `ops/staging/restore_driver.sh`,
`restore_gate.py`, `restore/stage_restore.sh`; `tests/test_staging_restore_postgres.py`; parts of
`tests/test_staging_unit.py`; the governing records; the three wrappers in `ops/bin`; `worker/ops/settings.py`,
`backup.py` (`verify_dump`), `dbhash.compare`, `restore_check.security_invariants`; `provision.sh` `step_postgres`.

---

## 1. What the audit found sound

- Every step's command runs with a cleaned environment; `psql` is called with `-X` and explicit host, port, user and
  database. Environment files that set `PG*` or `DATABASE_URL`, TCP hosts and host-less URLs are refused.
- The wrappers and the R-F4 preflight connect through the settings the identity check reads, and the reader login
  through its `DATABASE_URL`, so the route checked is the route used.
- P1's dump verification does not connect to a database. The table comparison flags missing tables, extra tables and
  row or digest differences, so an empty or partial restore fails.
- No command in the tool targets anything but the recorded host and port. The tool and the driver contain no
  `dropdb`, `rm` or `pg_dropcluster`, apart from the provisioning path of finding 7 and the removal of the R2
  container by its exact name.
- No path in the staging driver restores without a valid restore authorization. The authorization is consumed before
  the container is created, and a failed isolation check burns it, which is the safe direction.

## 2. Findings and their dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Major | Unknown statements in `globals.sql` were executed by `psql` before the procedure refused them: the parser ran first, but its result was consulted only in the proof step afterwards | **Fixed in this commit.** `restore()` refuses before the `restore-globals` step and records the reason. Test `r13` |
| 2 | Major | The line-based parser could be bypassed: `SET <anything> = <anything>;` was accepted, so an unterminated quote hid the following line, which the parser skipped as a comment while `psql` would run it. Confirmed offline | **Fixed.** Only the three `SET` lines `pg_dumpall` 17 writes are accepted, with exact values. A comment line carrying a statement terminator, a quote, a dollar sign, a block-comment opener or a backslash is refused. Test `r13` reproduces the bypass |
| 3 | Major | The proof was one-directional: it showed that what the file states exists, not that nothing else does. An extra role, an extra membership, or `ADMIN` where the file states none went undetected | **Fixed.** The copy's own roles must equal the file's, its memberships must equal the file's grants, and `ADMIN` not stated means not granted. PostgreSQL's predefined `pg_` roles are not compared. Test `r13` |
| 4 | Minor | Unknown words in `ALTER ROLE … WITH` were silently ignored | **Fixed.** Exactly the seven attributes, each once. Test `r13` |
| 5 | Major | Teardown would stop a cluster the procedure itself had refused, because the identity was recorded before the freshness check and teardown did not look at the result | **Fixed.** Teardown refuses unless the procedure accepted the cluster as its copy and a restore ran into it. Test `r13` |
| 6 | Major | Provenance is only as strong as `source.json`, which the tool cannot check. At staging the driver produces it; for a production checkpoint nothing says who produces it or how it is kept with the dump. Lesser gaps: `artifact_key` compared by basename only; the ledger run id, `verify_exit`, host and time not checked here; `globals.sql` bound only through the manifest | **Open.** Needs the production form of the procedure (§3). Not changed here, because the change belongs with that design |
| 7 | Major, for production use | The tool has no environment guard of its own. The isolation checks and the "no cluster before provisioning" check exist only in the staging script. A freshly provisioned production cluster would be accepted, and the provisioning step contains `pg_dropcluster --stop` for a cluster without checksums and without a `cse` database | **Open.** §3. At staging the R2 container (no network, no volume) bounds it |
| 8 | Major, for production use | The verification gate requires extractions and runs above zero. Runbook §6 asks for a checkpoint before HB-S4, when R-F4 may hold none, so a correct copy would fail and invite a bypass. The gate also does not compare its counts with a superuser's, or check the read-only snapshot | **Open.** The fix is to compare with the manifest inventory's row counts (zero allowed); it changes a gate and is left for the owner's review with §3 |
| 9 | Minor | `cse-financial verify` runs with its default sample of 3: a sample, not a full re-proof, and the gate does not require `ok is True` | **Open.** The document now states it (below) |
| 10 | Minor | The driver copied the evidence out before teardown and then deleted the container, so the teardown's own identity check and the state afterwards were lost | **Fixed.** The evidence is copied again after teardown |
| 11 | Minor | Exceptions other than the procedure's own produced a traceback and an incomplete record | **Fixed.** Every failure is recorded and exits 1. Test `r13` |
| 12 | Minor | Error detection relies on the English `ERROR:`; an accepted `SET lc_messages` could translate it | **Fixed** by finding 2: that `SET` is no longer accepted |
| 13 | Minor | Identity is in effect the system identifier alone: source and copy share port and data directory by construction, and a physical clone shares the identifier | **Open, documented.** Added to the procedure's limits |
| 14 | Minor | The stop command does not name the data directory, though the document says it does | **Open, documented.** Corrected in the document; the code change is small and left for the next round |
| 15 | Minor | No lock between an authorization's validation and its consumption, so two concurrent runs could both pass | **Open.** A single operator runs one restore at a time; recorded |
| 16 | Minor | Small differences between document and code (a comment's order; "before every command" does not hold for provisioning and for the first identity read; `--app` not passed to the wrappers; the database name hard-coded) | **Open, documented** |
| 17 | Note | The staging source read uses the container's default connection, read-only | Recorded |
| 18 | Note | The PostgreSQL tests are skipped where PostgreSQL is absent, so evidence must show them executed. They use trust authentication and the modules, not the wrappers. No test covered findings 1 to 5 | Tests for 1 to 5 and 11 added (`r13`). The rest is what Phase C's R2 run is for |

## 3. Conditions

**Before the staging R2 run (ST-31):** findings 1, 2, 3 and 5 fixed and tested, and the teardown evidence saved
(finding 10). Done in this commit, on the branch `integration/cloud`; they reach the workstation when that branch
does. The PostgreSQL tests `i1` to `i3` must be shown executed, not skipped, on PostgreSQL 17 after these changes.

**Before any production checkpoint, and before runbook §6 can be unblocked (REG-B27):**

1. Findings 6, 7 and 8 fixed and audited.
2. R2 evidence from staging showing peer authentication and the wrappers working on a copy.
3. A production form of the procedure, written and audited, covering: how the isolated environment is created and
   proven isolated (the tool must refuse on its own, not only the staging script); how `source.json` is produced
   read-only from production and travels with the dump; where the evidence goes; how the copy and the dump copy are
   destroyed.
4. The runbook change must then say: the copy is made only by this procedure, in a separate container or machine
   that is never the production environment; the exact command and inputs; the acceptance criteria per step; what to
   record in the HB-S4 arming decision (ledger run id, copy and source identifiers, the hash of `procedure.json`);
   that P1 §8 B step 5 and `restore_check --keep` remain forbidden; and that any refusal means the checkpoint is not
   passed.

## 4. Conditions of runbook §6, as the audit found them

| Condition | Verdict | Why |
|---|---|---|
| C1: isolated PostgreSQL 17 cluster, never production | Partly met | Met at staging by the container's isolation and the freshness check. The tool has no environment guard of its own (finding 7) |
| C2: the copy kept available | Met | It runs until teardown |
| C3: appropriate authentication | Partly met | Specified and coded; never executed on a real copy; no explicit check that no role has a password |
| C4: every verification command reaches the copy | Met at staging by design | Not yet executed with the wrappers and peer authentication |
| C5: identity check before every command | Partly met | Enforced for every step with a route. Not for provisioning, the first identity read, or the staging source read. Identity is in effect the system identifier (finding 13) |
| REG-B04: globals | Not met at audit; **met in code after this commit**, pending re-audit and R2 | Findings 1 to 4 |
| REG-B06: provenance | Partly met | The hash binding runs before any connection; it rests on an unauthenticated `source.json` (finding 6) |
| REG-B34: authentication on the copy | Partly met | Unverified on a copy; the tests use trust |

## 5. What could not be verified without executing a restore

Real `pg_dumpall` 17 output against the parser, including `\restrict`; `psql` and `pg_restore` on a real dump; peer
authentication and the `runuser` wrappers on a copy; the shape of `pg_hba_file_rules` on a real P1 configuration;
Docker's behaviour on Windows; and whether `i1` to `i3` pass after this commit's changes (they need PostgreSQL 17;
the offline unit tests `tests/test_staging_unit.py` pass, 32 of 32).

## 6. Limit of this audit

Auditor and author are the same model with different contexts (C-0005). The fixes in this commit were written by the
session that commissioned the audit and have not themselves been audited by a fresh context yet.
