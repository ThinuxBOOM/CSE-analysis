# R-F4 checkpoint restore: Revision 16-I, consolidated correction note

| | |
|---|---|
| Document | Revision 16-I, consolidated correction note (design only) |
| Relationship to earlier revisions | Replaces Revision 16-H as the consolidated note. Corrects Revisions 16 to 16-H where stated; everything else in them remains in force. Revisions 16 to 16-D are not reproduced here |
| Date | 2026-10-26 |
| Repository | CSE-analysis, branch `claude/rf4-implementation`, commit `eb91d3e` (no new reads for 16-F to 16-I; [R] citations are carried from 16-E) |
| Prepared by | Claude (Anthropic), at the owner's request |
| Prepared for | Independent audit |
| Status | **NOT READY. DO NOT MERGE. DO NOT EXECUTE RESTORE. R-F4 NOT ACCEPTED / NOT FROZEN. No restore is authorized.** |
| Amendments | A1 and A2 (2026-10-26), design text only, listed in the amendment record below. They are not a new revision. Every open item of this note is tracked in the consolidated register, `docs/R_F4_IMPLEMENTATION.md` §25 |

## Contents

0. What changed from 16-H
1. Executive verdict
2. Resolution matrix (16-E findings, updated)
3. Canonical state machines and rules
4. Equations and boundary cases
5. Mandatory gate and precondition inventory
6. Fixture inventory
7. Owner decisions, assumptions, unresolved items
8. Source and version evidence requirements
9. RTM registry and collision requirements
10. Status summary (open items by type, traceability, final status by dimension)

---

**Labels**

| Label | Meaning |
|---|---|
| [R] | Repository fact, read at commit `eb91d3e` (during 16-E; nothing was re-read for 16-F to 16-I). Amendment A2 re-read `worker/ops/backup.py` and `worker/ops/restore_check.py` at `7074ea5`; neither has changed since `43f6b85` |
| [E] | External behavior documented by an exact pinned source. **None qualifies.** Every earlier reading — the PostgreSQL 17 documentation and the psql `REL_17_STABLE` source — was unpinned, so it is cited as [E?] |
| [E?] | Unverified external claim |
| [P] | Design proposal |
| [X] | Executed evidence: **none** |

**Work done for 16-I:** design text only. The author read the 16-H note, which was written earlier in the same session. When 16-I was written, 16-H was kept outside the repository; it is now committed in `docs/` beside this one. No repository file was read or changed, no external source was read, and no test, build, connection, transfer, restore, commit, push or merge happened. **No test or fixture has been executed, and no implementation exists or has been verified. Nothing in this note has been independently verified or source-reviewed.**

**History available to the author:**
- Revisions 16-G and 16-H: verbatim.
- Revisions 16 to 16-F: through 16-G's consolidated text and a session summary. Their definitions are carried as 16-G states them. **ST_A and ST_G (16-C) were not re-read**; §6.13 defines its own starting state so that no CAN expectation depends on their exact content.
- Revisions 4 to 15: only through a summary. Their items are carried **by name**; where a definition could not be re-checked, that is flagged (UR-2, UR-14). **The archive/RTM blocker for Revisions 4–15 stands** (PC-D-DOCS, AG-RTM).

---

## Amendment record (after issue)

**16-I remains the current consolidated design for the isolated checkpoint restore.** No Revision 16-J exists, and none
is planned. A later change is made only as a numbered amendment here, tied to an item of the consolidated register
(`docs/R_F4_IMPLEMENTATION.md` §25) and allowed by its stopping rule (§25.2). The text as issued is commit `7074ea5`.
An amendment changes design text only: nothing in it is implemented, executed, independently reviewed or
owner-approved.

| # | Date | Register item | Text changed | Why |
|---|---|---|---|---|
| A1 | 2026-10-26 | REG-C01 | §3.2.1 (the `validation` row); §3.2.2 (the `inst_digest` row, with the snapshots S4 and S6 named; a new `currency_digest` row); §3.2.4 (the report; the sufficiency argument's step 7); §3.3 step 8; §3.6 item 1; §5.3 (AG-STATUS); §6.0; §6.4 (FR-HIST-14, FR-HIST-15; new FR-HIST-16); §6.14; §9; §10.5 | §3.3 step 8 recorded every validation's `inst_digest` after step 6, but §3.6 item 1 compared R's latest review with the `use` validation's state **after step 4**. What U itself presents at step 6 — ledger entries, a newer head, rejection objects for R found at step 6 — escaped that comparison. A head first seen through U could therefore leave R "current", while the same head imported earlier forces a new review: contrary to item 1's own consequence ("any instance added after R's last seal or review") and to OI-4. Separately, the `use` record held a digest that was not the value its decision used. **The correction follows from existing requirements:** the comparison covers everything in state after step 6 (item 1) except U's own attestation, which no earlier review can contain; without that one exception no use could ever be eligible, contrary to §3.6, LIFE-1, LIFE-2 and FR-HIST-15. No owner decision is involved, and nothing is weakened: every instance other than U's own attestation now counts, where 16-I left out everything U presented |
| A2 | 2026-10-26 | REG-C02 | §3.18 (the UR-7 bullet); §3.22 (the bootstrap-superuser sentence); §7.3 (UR-3 and UR-7); §8.1 (the UR-7 pin bullet); §1, §10.2, §10.5 and §10.6 (the unresolved range) | The [R] citations carried from 16-E were incomplete. Reading `worker/ops/backup.py:105-130` and `worker/ops/restore_check.py:71-77` at `7074ea5` shows that the manifest records `pg_dump_version`, `server_version`, `data_checksums` and `bootstrap_superuser`, and that P1's restore check builds its cluster with the manifest's bootstrap superuser, falling back to `cse_restore_check` only when the field is missing. 16-I said that direct mode used `cse_restore_check` and that the `pg_dump` version was not known to be recorded. UR-7 is closed by that reading. C-b (UR-3) stays open: see §3.22 and the register's REG-B04 |

**Scope of the status line (register REG-C04).** The status line above repeats R-F4's governing status. It adds no
condition to R-F4's component acceptance. Under `docs/R_F4_DESIGN.md` §10, that acceptance (milestones 6 and 7) does not
depend on this note's machinery. The isolated-restore procedure this note designs is a prerequisite of the restore steps
of later milestones and of the runbook's checkpoints (`docs/ops/PHASE2_BACKFILL.md` §6; the register's REG-B01).

---

## 0. What changed from 16-H

Evidence status for every row: **design only. No source review, no fixture execution, no implementation, no independent evidence review, no owner approval.** "Item" refers to the 16-I request: (1) the identity of import results in the order-independence projection; (2) the starting-state condition of OI-3. Item 3 covers further inconsistencies found while reassessing the rules those two items touch (rows 3a to 3g). Each is corrected here and documented; none fills a gap with an owner decision.

| # | Item | 16-H text or defect | 16-I correction | Where | Fixtures (all proposed, unexecuted) |
|---|---|---|---|---|---|
| 1a | 1 | Proj(S) held a final import result "for each `raw_sha256`". The same raw bytes can carry different signatures with different outcomes (CAN-8). The same bytes and signature can sit in different roles with different outcomes (RKN-17) | Import outcomes are keyed by the **input identity κ**. For a signed object, κ = (role, `raw_sha256`, `sig_sha256`), with ⊥ for a role that cannot be derived or a signature file that is missing. For a kit file, κ = (`kit`, `sha256`). For a VERIFIED input, κ equals the instance identity ι of §3.2.1. Source references are left out, because no pipeline step reads a position except to derive the role; that invariant is now stated, with the rule for extending κ if it ever changes | §3.2.1, §3.2.4 | CAN-5, CAN-8, CAN-15, CAN-16, CAN-17 |
| 1b | 1 | Idempotence was stated per (role, b, s) only for inputs already VERIFIED. "Final import result" was undefined when a result changes over time | Idempotence and re-processing are both by κ. An input with a VERIFIED instance adds at most an `import` note for a new source reference. An input without one is processed again at every import step. A note is written for a new source reference or a changed result. r_S(κ) is VERIFIED(ι) if an instance exists, otherwise the result in κ's latest note | §3.2.1, §3.3 step 4 | CAN-5, CAN-9, CAN-11, CAN-16 |
| 1c | 1 | OI-1 held "the kit store" fixed, although CAN-11 delivers a kit file. OI-2 tied finality to "the import step that sees the last relevant approval" | OI-1 now compares the states after the final import step of two reorderings, with every delivered file still present at that step. The starting kit store is fixed; delivered kit files belong to the file set. OI-2 says that intermediate states may differ, and gives the design reason the final results agree | §3.2.4 | CAN-9, CAN-11, CAN-20 |
| 2a | 2 | OI-3 compared states with equal Proj. But Proj omits things that change a validation's result: the run's validation history, `genesis`, the consistency of stored derived records, pending intents, the stores, the owner signers file, the validator build and the BT-0 configuration | OI-3 now requires equality of a **decision-input tuple Δ**: Proj; internal consistency with no pending intent; the pre-state inputs; the run's own validation history H_R; the set A of every run's `accepted` records; the stores; the presented inputs; clock readings, parameters and option; and lock outcome. A step-by-step table argues that Δ is sufficient. The report's byte content is defined | §3.2.4 | CAN-9, CAN-18, CAN-19, FR-HIST-13 |
| 2b | 2 | — | OI-3 is a claim. It is argued, not proved, and untested | §3.2.4 | — |
| 3a | 3 | §3.3 step 4 imported "every file … not yet VERIFIED", but §3.1.2A said owner-class files are not re-tried | Every input without a VERIFIED instance is processed again. For the owner class the result cannot change while the anchor is unchanged, so this is harmless; it makes the final result of every input that of the final import step | §3.1.2A, §3.3 step 4 | CAN-9 |
| 3b | 3 | Kit files had no re-scan rule, and a signers file named only by a stored AK's pin needs that AK to be stored first. CAN-11 relied on a re-scan | Kit files not yet stored are examined again at every import step, repeated until nothing more is added, before V_E is recomputed | §3.1.2A, §3.3 step 4 | CAN-11, CAN-20 |
| 3c | 3 | Step 6 recorded only VERIFIED presented objects, but RKN-9, RKN-10 and RKN-17 expect an `import` note for UNVERIFIABLE ones | Step 6 writes an `import` note for every presented input, under the same note rule | §3.3 step 6 | RKN-9, RKN-10, RKN-17 |
| 3d | 3 | FR-REC-8 said "only `raw_sha256` is computed" | κ, including `sig_sha256`, is computed | §6.2 | FR-REC-8 |
| 3e | 3 | §3.6 item 1, "a review completed after the latest import", depended on record order, which Proj excludes. Eligibility evaluations were not a step-8 kind. Item 1 had no code, and the §3.6 codes were missing from the §3.1.5 precedence | New validation kind `use`. Every `validation` record holds **`inst_digest`**, a digest of the instance set and kit store. Item 1 compares the digest of R's latest seal or review with the use validation's digest after its step 4. ELIGIBILITY_NOT_ESTABLISHED gets sub-codes NOT_VALID, REVIEW_NOT_CURRENT and ACCEPTED_RECORD_ONLY. Precedence group 11 is added. `use` records never change status | §3.1.5, §3.2.2, §3.3 step 8, §3.5, §3.6 | FR-HIST-12, FR-HIST-14, FR-HIST-15, LIFE-13 |
| 3f | 3 | Ledger-entry positions in the evidence manifest had no stated key | Entry positions are keyed by the entry's content `seq`. File names are not read, consistent with the role-only invariant of row 1a | §3.2.3 | — |
| 3g | 3 | 16-H's eligibility wording allowed a second use to rely on a review made before the first use's attestation, so `current-floor` or the interim review rule were never applied to that later owner statement | With row 3e's currency rule, a second use needs a new review. Under `current-floor` or the interim rule that review fails with CLOCK_ROLLBACK once a later owner statement exists, so a run is eligible for at most one use. Recorded as a consequence for PC-D-REVIEW (C-c); no decision is made | §3.6, §7.1, §10.2 | FR-HIST-9, FR-HIST-15 |

**Preserved without change:**
- **UR-15** stays open until an owner-approved, independent trust-anchor channel and record exist. No channel is invented, selected or approved.
- **UR-16** stays a hard blocker until a concrete reviewer-state integrity boundary meets SI-R1 to SI-R7 and the PC-D-TRUST (b, c) decisions are made. **BT-0 still fails closed.** The fixture-only BT-0 stub is a test harness, **not protection**, and cannot be part of an approved validator.
- The evidence-key lifecycle (§3.12A) and the exact-instance canonicality rules (§3.2.3) are unchanged, apart from the clarification in row A6.
- Unchanged: the source/version pin and independent source-review requirements; every fixture remains unexecuted; the missing Revisions 4–15 archive and RTM prerequisites; C-a, C-b and C-c; every owner decision; every change-control requirement; every existing release gate; the prohibition on merge and on executing a restore.

---

## 1. Executive verdict

| Dimension | Status |
|---|---|
| Design consistency | Consistent on paper, apart from the open items in §10.2 — one parameter decision, three design defects (two of them the hard blockers UR-15 and UR-16) and one pending policy choice — and the unresolved items UR-1 to UR-16, apart from UR-7, which amendment A2 closed (§7.3). 16-H corrected the 16-G inconsistencies it found; 16-I corrects the order-independence definitions and the further inconsistencies in §0 rows 3a to 3g. None of these corrections has been independently reviewed |
| Source review | None |
| Fixture execution | None. No test or fixture has been executed |
| Independent evidence review | None |
| Owner approval | None of the decisions in §7.1 |
| Repository implementation | None of the machinery |
| Acceptance or freeze authorization | Not given |

**Implementation readiness: NOT READY.** What blocks it:
- **UR-15 (hard blocker):** no independent trust anchor for the owner key. Until one exists, every validation aborts with TRUST_ANCHOR_MISSING.
- **UR-16 (hard blocker, new in 16-H):** no reviewer-state integrity boundary (§3.7A). Until one exists, every validation aborts with STATE_INTEGRITY_UNESTABLISHED, and with TRUST_ANCHOR_MISSING, because the anchor recorded in state cannot be authenticated.
- **Owner decisions** (§7.1), none of which is decided:
  - PC-D-OKEY (including the anchor channel), PC-D-EKEY (including rotation and eligibility), PC-D-TRUST (extended in 16-H: which adversaries the state-integrity boundary must resist, and who holds its evidence);
  - PC-D-REVIEW, PC-D-SKEW (C-a), PC-D-EMAX, PC-D-BASE, PC-D-INDEP;
  - PC-D-FRESH (floors from rejected objects; FORK coverage);
  - PC-D-RGI, PC-D-QCAP, PC-D-LEASE, PC-D-PGCHK, PC-HR-KEYS.
- **Missing evidence:** the PC-D-DOCS archive for Revisions 4–15 does not exist, and no AG-PINSRC evidence exists.
- **UR-3 (C-b):** the globals-restore design defect.
- **Change control:** neither PC-CC-CKPT-IMPL nor P1-CC-PROV has been authorized or implemented.

---

## 2. Resolution matrix (16-E findings, updated)

Every row has evidence status **design only; no source review, fixtures, or evidence.** "§" refers to sections of this note. **▲ marks rows changed in 16-I.** (Earlier markers are removed. 16-G changed A1, A6, A7, D25 and D26; 16-H changed the same rows.)

| # | History | Correction | Fixtures | Expected |
|---|---|---|---|---|
| A1 ▲ | 16-B C3 order contradiction; 16-C §1 VS pipeline; self-inflicted rollback (16-C); 16-F roles; 16-G bootstrap and instances; 16-H input identity and Δ | §3.1–§3.3: one pipeline over five roles. Pre-state checks (BT-0 to BT-2) first; purpose resolution and BT-3 to BT-7 at step 6a. Signers-file resolution (§3.1.2A). Logical objects, instances and derived records (§3.2). Floors raised only by VERIFIED owner-class logical objects. Floors evaluated before the run's own objects are recorded. Fixed reason-code precedence. Whether rejected objects raise floors is an open decision (PC-D-FRESH b) | FR-REC-1 to FR-REC-8; FB-13 to FB-16; FR-NONCE-3; RKN-*; BOOT-*; CAN-* | UNVERIFIABLE objects never raise floors. Under the interim rule, verified-but-rejected owner-class objects do. Independence from import order is a design claim (OI-1 to OI-4, with the input identity κ and the decision-input tuple Δ of 16-I), not established |
| A2 | 16-C decision 2; 16-D §1 options; FR-LATE-7 gap | §3.4: three options plus an interim rule. FORK coverage is an open decision (PC-D-FRESH c); the interim checks every seen head and entry | FR-LATE-1 to FR-LATE-10; FR-HIST-4 to FR-HIST-7 | Option-dependent results are tabulated. Under the interim rule, FR-LATE-7 is FORK |
| A3 | 16-D §2 note; CLOCK_SKEW referenced m1 | §4.1–§4.2: interval model; CLOCK_STEP and DELIVERY_AMBIGUOUS. Values are fixture parameters only | FB-1 to FB-10, FB-28 to FB-35 | Derived from fixture parameters |
| A4 | 16-D formulas; 16-C decision on E > E_max | §4.3; interim: RUN_EXCEEDS_EMAX rejects (PC-D-EMAX open) | FB-17 to FB-27, FB-36, FB-37 | FB-20 = {RUN_EXCEEDS_EMAX, APPROVAL_EXPIRED} under the interim rule |
| A5 | 16-D FR-LATE-10 contradiction | §3.3 step 5: authenticating `floor_p0` is mandatory under every option | FR-LATE-10, FR-HIST-4 | SNAPSHOT_INVALID under all options |
| A6 ▲ | 16-D: `accepted` records persist; 16-F rejection transitions; 16-G lifecycle; 16-H eligibility currency | §3.5–§3.6 and §3.12A: immutable records; status derived from them; eligibility evaluated by `use` validations, with review currency by `inst_digest` (16-I); evidence-key lifecycle by purpose, with evidence files resolved through V_E so that rotated keys stay verifiable | FR-HIST-1 to FR-HIST-15; RKN-5, RKN-18, RKN-21 to RKN-23; LIFE-1 to LIFE-18 | An old `accepted` record never implies current validity |
| A7 | 16-C FR-REC-6 defect; 16-D revision; 16-G instances | §3.2.1 record model (every instance has an `instance` record; `seen` is content-only); §6.2 | FR-REC-6, FR-REC-8, CAN-1 to CAN-5 | FR-REC-6 records +instance(b6); `seen`(h10) is unchanged |
| B8 | 16-C §3, 16-D §4 | §8.1: pin-record fields per component | SRC-P1, SRC-N1 to SRC-N14 | AG-PINSRC and AG-QBYPASS stay closed |
| B9 | 16-D §4 criteria; 16-F precedence | §8.2–§8.3 with precedence rules 1–6 | SRC-P1 to SRC-P3, SRC-N1 to SRC-N18 | — |
| B10 | 16-B, 16-C register; 16-F classes | §8.4: class M or F per behavior | SBX-*, SM-1 to SM-4, FR-INT-*, FR-CONC-* | All UNVERIFIED |
| B11 | Rev 16 §2 (unpinned psql reading) | §8.1: pinned tuple required before any version-specific claim | All BLOCKED-PIN fixtures | — |
| B12 | Rev 16 §3; 16-A A2; 16-B qcap admission | §3.10: identity chain and supported message set; anything else fails closed | PROTO-1 to PROTO-16; QID-1 to QID-7; QB-1 to QB-13 | Unsupported → QCAP_UNSUPPORTED → UNKNOWN |
| B13 | Rev 16 §2 (EB) | §3.14: supported construct set; EB equals qcap byte for byte; multi-statement Query → UNKNOWN | FX-EB, ADV-1 to ADV-22, SPL-N1 to SPL-N3 | — |
| B14 | Rev 13 to 16 (jsonlog, FC classes) | §3.15: record shapes are hypotheses; unknown or ambiguous → UNKNOWN | FX-JSON, FX-FAIL-1 to FX-FAIL-5, CMPL-1 to CMPL-10, LRC-1 to LRC-16 | — |
| B15 | 16-B SV checks; 16-C | §3.9 plus a threat-model statement | SBX-*, SBX-AUD | — |
| C16 | Earlier revisions: LC-BASE/PIN/END/OBS, CFGO | §3.16: snapshot algorithm with a stability condition | CFG-1 to CFG-10, EXPCFG | — |
| C17 | Earlier correction: data_checksums is not a configuration setting | §3.17. The expectation X is an open decision (PC-D-PGCHK) | CKS-1 to CKS-5, FX-DC | — |
| C18 | Rev 16 §6 | §3.18: artifact, versions, commands; direct and script modes kept separate | FX-SM, SM-N1, PVR-1 to PVR-4 | No credit transferred between modes |
| C19 | Earlier in-session guard, R2b | §3.19: guard text and the guard-to-side-effect boundary | GRD-1 to GRD-8 | — |
| C20 | Rev 16 §4 | §3.20: six-level relations, none assumed 1:1 | OPR-1 to OPR-9, FX-RS | — |
| C21 | Earlier correction: `pg_sequences` has no `is_called` | §3.21, §4.5 | FX-SEQ, SQV-1 to SQV-9 | — |
| C22 | Earlier SCH/GLB | §3.22: byte comparison; normalization only once proven | FX-SCH, FX-GLB, SGR-1 to SGR-10 | — |
| C23 | Rev 16 §6, P1-CC-PROV | §3.23 | PROV-B1 to PROV-B9, PVR-1 to PVR-4 | Current dumps ineligible |
| D24 | Rev 15/16 RGI; §1.2 | §3.24: validator contract | RGV-1 to RGV-8, PCV-1 to PCV-11 | — |
| D25 ▲ | Rev 16 §5; 16-A A3; 16-B C3; 16-C to 16-H | §3.1–§3.7A, §4.4, with the trust bootstrap, the instance model, the input identity κ, the state-integrity threat model and the OI claims | LED-*, FR-*, FB-*, RKN-*, BOOT-*, CAN-* | Blocked by UR-15 and UR-16 |
| D26 | Seal, events log (earlier); 16-F evidence key; 16-G lifecycle | §3.1.2A, §3.11, §3.12A, §3.13, §3.25 | SLV-1 to SLV-9, RKN-*, BOOT-7 to BOOT-9, LIFE-*, CAN-13, CAN-14 | Blocked by UR-15 and UR-16 |
| D27 | Earlier: lease, hypervisor, single host | §3.12, §3.26, with authenticated rejection records | ORC-1 to ORC-11, RKN-5, RKN-18, RKN-21, RKN-23 | — |
| D28 | 16-A to 16-D RTM | §9 | RTM-* | Fails until all prerequisites hold |

---

## 3. Canonical state machines and rules

### 3.1 Signed-object pipeline, role contract and trust bootstrap [P]

The pipeline applies to every signed object, whether presented, imported, persisted or reloaded. Each object arrives as (role r, raw bytes b, signature s, source reference).

#### 3.1.1 Role contract

There are five roles in two **key classes**. The role is assigned from the object's position, never from its content. The key class, namespace, principal and signers file all follow from the role.

| Role | Key class | Namespace ns(r) | Signer principal | Signers file | Schema (exact key set) | Position |
|---|---|---|---|---|---|---|
| entry | owner | `cse-checkpoint-approvals` | `<owner principal>` | `allowed_signers.owner` | Revision 16 §5: `ledger_id, seq, prev_hash, kind, id, subject_kind, subject_sha256, tuple, ak_sha256, rgi_sha256, approved_at, not_after, revokes` | `NNNNNN.entry.json` with `NNNNNN.entry.sig` in the approvals-ledger directory |
| head | owner | `cse-checkpoint-approvals-head` | `<owner principal>` | `allowed_signers.owner` | Revision 16 §5: `ledger_id, kind` (= `"head"`), `head_seq, head_entry_sha256, issued_at, not_after_head` | `head.json` with `head.sig` |
| attestation | owner | `cse-checkpoint-confirm` | `<owner principal>` | `allowed_signers.owner` | 16-B C3: `attestation_version, head_seq, head_sha256, ledger_id, nonce, run_id, signer_principal, t_att` | `attestation.json` in the run's evidence (or, for a downstream use, in that use's evidence) |
| seal | evidence | `cse-checkpoint-seal` | `<evidence principal>` | An evidence signers file in V_E (§3.1.2A) | §3.25, including `signer_principal` | `seal.json` with `seal.sig` in the run's production-side evidence |
| rejection | evidence | `cse-checkpoint-rejection` | `<evidence principal>` | An evidence signers file in V_E (§3.1.2A) | §3.12: `rejection_version, run_id, nonce` (or null), `signer_principal, stage, codes, streams` | The run's directory in the primary or secondary rejection store |

**The principal strings, keys and both signers files are undecided** (PC-HR-KEYS, PC-D-EKEY, PC-D-OKEY). This note does not choose them.

**Defense against cross-role substitution and replay.** These are listed in the order the pipeline relies on them:
1. **Role from position:** a verifier never chooses a namespace or principal from the object's own content.
2. **Namespace binding:** SSHSIG binds the namespace into the signed data [E?] (E127, class M). A signature made under one namespace fails verification under any other.
3. **Disjoint key classes:** the owner signers file shares no key and no principal with any evidence signers file in V_E. An owner key never verifies as evidence, and an evidence key never verifies as owner.
4. **Distinct schemas:** each role has its own discriminator (`kind`, `attestation_version`, `seal_version`, `rejection_version`) and its own exact key set. Bytes made for one role fail VS-6 in any other.

Acceptance relies on items 1–3. Item 4 is defense in depth only and is not counted as evidence.

#### 3.1.2 Signers-file policy (grammar revised in 16-H)

- There are two **kinds** of signers file:
  - the owner file, `allowed_signers.owner`;
  - evidence files. The one currently in use is `allowed_signers.evidence`. Earlier evidence files stay in the kit store (§3.1.2A).
- **Pins and registrations:**
  - Each AK pins one owner file and one evidence file, by SHA-256.
  - A PC-HR-KEYS registration names the hashes of one owner file and one evidence file, plus both principals. The owner approves it in the ledger.
  - **Neither an AK pin nor a registration authenticates the owner key.** Only the trust anchor does (§3.1.4).
- **Exact grammar (new in 16-H).** A signers file is exactly one line and nothing else:

  `<principal>` SP `namespaces="<list>"` SP `<keytype>` SP `<base64>` LF

  - SP is one space (0x20) and LF is 0x0A. There is no comment, no other option, no leading or trailing whitespace, and no other line.
  - `<principal>` matches `[A-Za-z0-9._@+-]{1,64}`, so it cannot contain a wildcard (`*`, `?`), a list separator (`,`) or a negation (`!`). That such a principal matches only itself in `ssh-keygen -Y verify` is part of E127 (§8.4).
  - `<list>` is exactly `cse-checkpoint-approvals,cse-checkpoint-approvals-head,cse-checkpoint-confirm` in the owner file, and exactly `cse-checkpoint-seal,cse-checkpoint-rejection` in an evidence file, in that order.
  - `<keytype>` and `<base64>` are the public key. The accepted key types are part of PC-D-OKEY and PC-D-EKEY.
- **Consequence:** the owner file is a fixed function of the anchor. **Line_owner(anchor)** is the grammar above filled in with the anchor's principal, key type and base64 blob. BT-2 compares the file with it byte for byte.
- **Rejected** (each gives SIGNERS_INVALID with a sub-code). The rows are checked in order, and the first failure gives the code. For the owner file, a file that passes all three grammar rows but differs from Line_owner(anchor) gives TRUST_ANCHOR_MISMATCH (BT-2).

| Condition | Sub-code | Checked at |
|---|---|---|
| The file does not match the grammar, apart from the option and namespace list | LINE_POLICY | BT-2 for the owner file; V_E construction (§3.1.2A) and BT-5 for evidence files |
| An option other than `namespaces` | OPTION_POLICY | Same |
| A namespace list other than that file kind's exact list | NAMESPACE_POLICY | Same |
| The same public key in the owner file and an evidence file | KEY_ROLE_OVERLAP | V_E construction and BT-5 |
| The same principal in the owner file and an evidence file | PRINCIPAL_ROLE_OVERLAP | V_E construction and BT-5 |
| Two distinct evidence files in V_E contain the same public key (new in 16-H) | EVIDENCE_KEY_REUSE | V_E construction (§3.3 steps 3 and 4) |
| The purpose AK's pin differs from the file's hash, or no file with the pinned hash can be retrieved | FILE_HASH | BT-5 (§3.3 step 6a), after the purpose AK is approved |

- **Abort or exclusion:**
  - A failure for the owner file, a failure for the purpose AK's evidence file, and EVIDENCE_KEY_REUSE all abort the validation.
  - Any other evidence file that fails the policy is left out of V_E and noted in an `import` record. Objects that verify only under it stay UNVERIFIABLE, which blocks whatever depends on them.
- **Key rotation:**
  - **Evidence key:** a new evidence file with a new key; a new AK pinning it, approved under PC-D-AKIT; a new PC-HR-KEYS registration; and revocation of the old registration (PC-D-EKEY). It is never a second line in an existing file. The earlier file stays in the kit store and in V_E, so objects it signed stay verifiable. Whether they are authorized is decided by §3.12A.
  - **Owner key:** also requires a new anchor through the anchor channel, and an explicit rebuild (§3.1.4, §3.7).

#### 3.1.2A Signers-file resolution (new in 16-H)

This section makes §3.12A's lifecycle, which uses several evidence files over time, consistent with the verification pipeline.

**Kit store.**
- Reviewer state keeps a content-addressed copy of every AK and signers file it uses, and of every file a VERIFIED owner approval names, keyed by the file's SHA-256 (`kit` records, §3.7).
- A file is retrieved by its hash. Stored bytes whose hash differs are STATE_CORRUPT.
- At step 4, files in the primary store's kit directory are added if an approval or a pin names their hash, as below. **Revised in 16-I:** files not yet stored are examined again at every import step, and the examination repeats until no further file is added, because a signers file can be named only by the pin of an AK that is itself added in the same step (CAN-20).

**Owner file O.** Always Line_owner(owner_anchor), checked at BT-2. There is only one at a time.

**Purpose AK, A_p.** Each validation purpose names the AK it is checked against:

| Purpose | A_p |
|---|---|
| P0 of run R | The AK presented with R |
| Seal validation and review of R | The AK whose hash R's P0 `validation` record holds. The seal's `ak_sha256` must equal it |
| Eligibility of R for a use U | The AK presented for U |

**Evidence verifier set V_E.** V_E is the set of evidence files F for which both of these hold:
1. F is the evidence file pinned by an AK whose hash is the subject of some VERIFIED PC-D-AKIT approval entry, **or** hash(F) is named by some VERIFIED PC-HR-KEYS approval entry. A later revocation does not remove F: revocation ends authorization (§3.12A), not verifiability.
2. F is in the kit store and passes the §3.1.2 policy against O.

V_E depends only on the set of VERIFIED owner-class approvals and on the kit store, so it does not depend on the order in which they arrived (OI-1, §3.2.4).

**Verifying an evidence-class object.**
- VS-5 is run under each F in V_E in ascending hash order, each time with F's principal.
- No two files in V_E share a key (EVIDENCE_KEY_REUSE). An SSHSIG signature carries its signer's public key, and `-Y verify` checks it against the file's key [E?], which is within E127's source review. So at most one F can succeed.
- If one does, the instance is VERIFIED, and its `verified_by` is hash(F). §3.12A takes E from `verified_by`.

**Processing inputs again (revised in 16-I).**
- At every import step, each input in the stores whose identity κ (§3.2.1) has no VERIFIED instance is processed again: owner-class inputs under O, evidence-class inputs against the current V_E.
- So a rejection or seal that arrived before the approval naming its key becomes VERIFIED once that approval has been imported (CAN-11).
- For owner-class inputs the result cannot change while the anchor is unchanged, so processing them again is harmless. 16-H said owner-class files were not re-tried, which contradicted §3.3 step 4. Processing every such input again makes each input's final result the result of the final import step (OI-2).
- V_E only grows while the anchor is unchanged: its members come from VERIFIED approvals and pins, and neither revocation nor later imports remove one. An exclusion (§3.1.2) depends only on the file and O. So an evidence-class instance, once VERIFIED, stays VERIFIED under the same `verified_by`.

**VERIFIED is still not authorized.** An object VERIFIED under F is authorized only as §3.12A says.

#### 3.1.3 Pipeline steps

| Step | Rule | On failure |
|---|---|---|
| VS-1 | **Role.** Role from position (§3.1.1). Key class, ns(r), principal and signers file follow from the role | UNVERIFIABLE(ROLE) |
| VS-2 | **Size and bytes.** \|b\| ≤ 4096, and every byte is 0x09, 0x0A, 0x0D or in 0x20–0x7E | UNVERIFIABLE(ENCODING) |
| VS-3 | **Signature format.** s is exactly one armored SSHSIG block, followed by one LF | UNVERIFIABLE(SIG_FORMAT). The alias BAD_SIG_FORMAT (16-B) is archive-only |
| VS-4 | **Signers files for the role's class are resolved** (revised in 16-H): for the owner class, O after BT-2 (§3.1.4); for the evidence class, V_E (§3.1.2A) | SIGNERS_INVALID(sub-code) or a BT code: the validation aborts. What is already recorded is stated in §3.3 |
| VS-5 | **Signature.** The pinned `ssh-keygen -Y verify -f F -I p(F) -n ns(r) -s s`, with stdin = b and environment {`LC_ALL=C`}, exits 0. F is O for the owner class, or each member of V_E in turn for the evidence class (§3.1.2A). **p(F) is the principal in F itself** (revised in 16-H; 16-G said "the registered principal", which is undefined without a PC-HR-KEYS registration) | UNVERIFIABLE(BAD_SIG) |
| VS-6 | **Strict parse** of b against schema(r): one JSON value with optional whitespace, no duplicate keys, the exact key set, integers only (no fraction or exponent) within range, strings matching their regexes, and timestamps that are real instants written `YYYY-MM-DDTHH:MM:SSZ` | UNVERIFIABLE(SCHEMA) |
| VS-7 | **Principal binding.** For attestation, seal and rejection, `signer_principal` equals p(F) for the F under which VS-5 passed | UNVERIFIABLE(PRINCIPAL). The alias WRONG_PRINCIPAL is archive-only |
| → | **VERIFIED.** Compute `content_sha256` = SHA-256(C(parsed)), `raw_sha256` = SHA-256(b) and `sig_sha256` = SHA-256(s), and set `verified_by` = SHA-256(F) | — |
| Record point | §3.2.1. Only VERIFIED instances reach it | — |
| VS-8 | **Canonicality** is a function of an instance's bytes: canonical(b) ⇔ b = C(parse(b)). It is stored on each `instance` record for audit. **The stored value is never a decision input:** wherever canonicality decides, it is recomputed from the bytes of the exact instance named for that position (§3.2.3) | NONCANONICAL. This blocks acceptance only, and only as §3.2.3 says |
| VS-9 | Semantic rules: §3.3 step 7, §3.12, §3.12A, §3.13, §4 | Reason codes. These block acceptance only |

**VERIFIED is not the same as authorized.** For an evidence-class object, VERIFIED means only that the evidence key signed it under the right namespace. Whether that key was authorized for a given purpose is decided in §3.12A.

**Canonical bytes.** C(x) = `json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)`, ASCII-encoded, with no trailing newline (Revision 16 §5). The Python version must be pinned in the AK [E?].

#### 3.1.4 Trust bootstrap (new in 16-G; step order and pre-state checks revised in 16-H)

**What is missing. Two hard blockers apply.**
- **UR-15:** no independent trust anchor for the first owner key is defined or approved.
- **UR-16 (new in 16-H):** no boundary protects the reviewer state that records the anchor, the history and the floors (§3.7A).

Every anchor candidate in the earlier revisions has been checked, and none qualifies:

| Candidate | Why it cannot anchor the owner key |
|---|---|
| PC-HR-KEYS (owner-signed ledger entry registering the signers files) | It is signed by the key it would authenticate, so it is circular. It is also unapproved |
| PC-D-AKIT (owner-signed approval of the AK) | Circular for the same reason. The AK pins the owner signers file; the ledger signed by that key approves the AK |
| PC-HR-PROD | It registers the driver host, not a key |
| Revision 16's out-of-band confirmation (F9) | It confirmed head hashes, not keys. 16-A replaced it with an owner-signed attestation, which depends on the owner key itself |
| Any repository artifact | None was identified. This note does not assume one, for example signed commits, because none was checked |

**What the anchor must provide (requirements only).** The mechanism is an **open owner decision (PC-D-OKEY b)**. This note does not invent, select or claim approval of a channel.
- It must give each validating party — the driver host for P0 validations, and every reviewer — the owner's **full public key line** (key type plus base64 blob) **and the owner principal**.
- It must arrive through a channel that does **not** depend on the owner key, the AK, the ledger, the driver host, the VM, or any object those supply.
- Each validating party must record it independently, at explicit `init` or `rebuild`.
  - 16-G proposed the `owner_anchor` field of the reviewer-state `genesis` record (§3.7).
  - **That record can be trusted only as far as the state that holds it.** That is UR-16 (§3.7A), so the record's adequacy is not established. How the anchor is recorded is part of PC-D-OKEY (b), together with the channel.
- Replacing it, including when the owner key rotates, must go through the same channel.
- It is never derived from existing reviewer state, the AK or the ledger.

**Bootstrap steps (step order revised in 16-H).**
- The chain check, BT-0, BT-1 and BT-2 are the **pre-state checks**. They run at §3.3 step 2, before any record is trusted beyond the log's structure.
- BT-3 to BT-7 run at §3.3 step 6a, once the presented objects have named the purpose head and the purpose AK. In 16-G they ran at step 4a, before the purpose head was known at P0.

| Step | Check | Failure |
|---|---|---|
| Chain | The committed state log is structurally intact: it starts with `genesis`, every record parses, and each record's back-link equals the SHA-256 of the record before it. **This shows only how the log was built, not who kept it** (§3.7A) | STATE_CORRUPT(CHAIN). Pre-state (rule below) |
| BT-0 (new in 16-H) | An approved reviewer-state integrity boundary (§3.7A) is configured, and it authenticates `genesis` and the state history up to the last committed record. **No such boundary exists (UR-16), so this step cannot pass today** | STATE_INTEGRITY_UNESTABLISHED. Pre-state |
| BT-1 | The chain check and BT-0 both passed, **and** `genesis` holds `owner_anchor` | TRUST_ANCHOR_MISSING. Pre-state. An anchor read from state that is broken or unauthenticated is not independently authenticated, so it counts as missing (revised in 16-H) |
| BT-2 | `allowed_signers.owner` equals Line_owner(owner_anchor) byte for byte (§3.1.2). Only now is O trusted, and it is trusted because of the anchor, not the AK | SIGNERS_INVALID(LINE_POLICY, OPTION_POLICY or NAMESPACE_POLICY); otherwise TRUST_ANCHOR_MISMATCH. Abort |
| BT-3 | The presented ledger entries up to the purpose head, and the purpose head itself, were VERIFIED under O at §3.3 step 6, and the chain replays (Revision 16 §5). For a head taken from state rather than presented (h_latest, §3.12A), the entries in state up to it must be VERIFIED and replay; a gap leaves InForce undecidable, which §3.12A treats as **not in force** | **Not an abort** (revised in 16-H, consistent with FR-REC-8): the group-2 UNVERIFIABLE codes for the failing objects; NOT_EVALUATED for BT-4 to BT-7 and every rule that depends on them; the existing ledger codes and FORK for chain faults |
| BT-4 | SHA-256(A_p) is the subject of a PC-D-AKIT approval in force at the purpose head. A_p is the purpose AK (§3.1.2A) | AK_UNAPPROVED. Abort |
| BT-5 | A_p's owner pin equals SHA-256(O). A_p's evidence pin names a file that is in the kit store and passes the §3.1.2 policy against O | SIGNERS_INVALID(FILE_HASH or the policy sub-code). Abort |
| BT-6 | The PC-HR-KEYS approval in force at the purpose head names SHA-256(O) and O's principal as its owner part | KEYS_REGISTRATION_MISMATCH. Abort. A missing registration, or one whose evidence part names another file, is **not** an abort: it leaves that evidence file unauthorized (BT-7) |
| BT-7 | Evidence-key authorization is decided per purpose (§3.12A), with E = the object's `verified_by`, using the PC-HR-KEYS approvals in force at the heads that purpose names | EVIDENCE_SIGNERS_UNAUTHORIZED, REJECTION_UNVERIFIABLE or REJECTION_KEY_REVOKED, per §3.12A |

**Pre-state rule (new in 16-H).** The chain check, BT-0 and BT-1 are all evaluated. Each one that fails emits its code, in that order; then the validation aborts with nothing recorded.

| Situation | Codes |
|---|---|
| **Today**, with an intact chain and a valid anchor in `genesis` | {STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING} (BOOT-15) |
| Today, with no anchor in `genesis` | The same two codes |
| Today, with a broken chain | {STATE_CORRUPT(CHAIN), STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING} |
| Fixture runs with the BT-0 stub (§6.11) and a broken chain | {STATE_CORRUPT(CHAIN), TRUST_ANCHOR_MISSING} (BOOT-11) |

**So TRUST_ANCHOR_MISSING is emitted by every validation that reaches step 2 without an independently authenticated anchor.** A validation that stops earlier, with STATE_UNAVAILABLE at step 1, evaluates nothing and gives no result.

**Aborts after step 6.** BT-4, BT-5 and BT-6 abort after the presented objects have been recorded (§3.3 step 6).
- No `validation` or `accepted` record is written.
- Instances already recorded at steps 4 and 6 stay. They were verified under O or V_E, neither of which depends on the purpose AK.

**Circularity rule.** An AK, a ledger and a registration that only vouch for each other are never accepted as a root. Without BT-1 and BT-2, the result is always TRUST_ANCHOR_MISSING, however consistent the rest is (BOOT-4).

**Threat-model boundary.**
- The validator program run by each party is trusted under PC-D-TRUST, extended to the reviewer platform. BT-4 checks that the AK is consistent with the ledger; it cannot protect against a party running a substituted validator.
- **The reviewer state is not trusted on that basis** (§3.7A). Without BT-0, anyone who can rewrite the state directory, the owner signers file, the AK and the ledger directory can substitute a self-consistent root, anchor included (BOOT-13).

**Until UR-15 and UR-16 are both resolved, every validation aborts.** AG-FRESH, AG-APPROVALS, AG-SEAL, AG-STATUS and AG-PRECOND cannot pass.

**Fixtures.** BOOT-1 to BOOT-17 use a **synthetic** anchor and, except where stated, a fixture-only BT-0 stub (§6.11). They test the order and logic of these steps. They cannot establish the real anchor or a real integrity boundary.

#### 3.1.5 Reason-code precedence

Each validation emits one deterministic, ordered list. Acceptance happens if and only if the list is empty; for a `use` validation, ELIGIBLE(R, U) holds if and only if it is empty.

1. **Aborts**, in §3.3 step order:
   - step 1: STATE_UNAVAILABLE;
   - step 2, pre-state, **all evaluated** (§3.1.4): STATE_CORRUPT(CHAIN), STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING; then BT-2: SIGNERS_INVALID(LINE_POLICY, OPTION_POLICY, NAMESPACE_POLICY), TRUST_ANCHOR_MISMATCH;
   - step 3: STATE_CORRUPT (recomputation, §3.2.1), SIGNERS_INVALID(EVIDENCE_KEY_REUSE);
   - step 4: IMPORT_UNAVAILABLE;
   - step 5: SNAPSHOT_INVALID;
   - step 6a: AK_UNAPPROVED, SIGNERS_INVALID(FILE_HASH or a policy sub-code), KEYS_REGISTRATION_MISMATCH.

   Apart from the pre-state group, nothing is evaluated after an abort.
2. **UNVERIFIABLE codes for required objects,** each followed by NOT_EVALUATED(rule) for every rule whose inputs include that object. An evidence position whose instance is unavailable (EVIDENCE_LAYOUT_INVALID(INSTANCE_UNAVAILABLE), §3.2.3) is treated the same way for NOT_EVALUATED.
3. **Integrity:** EQUIVOCATION, FORK, ATTESTATION_CONFLICT, SEAL_CONFLICT.
4. **Floors:** ROLLBACK, CLOCK_ROLLBACK, each flagged LATE_EVIDENCE where §3.4 says so.
5. **Canonicality and layout:** NONCANONICAL, then EVIDENCE_LAYOUT_INVALID with sub-code DUPLICATE_POSITION, MISSING_POSITION, P0_MISMATCH or INSTANCE_UNAVAILABLE (§3.2.3).
6. **Binding:** ATTESTATION_REPLAY, HEAD_MISMATCH.
7. **Time:** ATTESTATION_STALE, CLOCK_STEP, CLOCK_SKEW, DELIVERY_AMBIGUOUS, FUTURE_ENTRY, FUTURE_HEAD, HEAD_BEFORE_ENTRY, NONMONOTONIC, APPROVAL_EXPIRED, HEAD_EXPIRED, HEAD_STALE, HEAD_WINDOW_TOO_LONG, RUN_EXCEEDS_EMAX.
8. **Ledger semantics:** REVOKE_MISMATCH, INVALID_REVOKE_TARGET, DOUBLE_REVOKE, DEPENDENCY_INEFFECTIVE, STALE_RGI, STALE_TUPLE, STALE_AK, UNEXPECTED_BINDING, DUPLICATE, CONFLICT.
9. **Preconditions** (Revision 16 §1.2): missing, duplicated, conflicting, stale, wrongly bound.
10. **Evidence** (seal and review validations only; §3.12, §3.12A, §3.13), in this order:
    - rejection: RUN_REJECTED, REJECTION_KEY_REVOKED, REJECTION_UNVERIFIABLE, REJECTION_UNSIGNED, REJECTION_MISFILED;
    - seal: SEAL_INVALID, EVIDENCE_SIGNERS_UNAUTHORIZED, SEAL_RUN_MISMATCH, SEAL_BINDING_MISMATCH;
    - streams: STREAM_BROKEN, STREAM_HEAD_MISMATCH;
    - registry and recomputation: REGISTRY_*, EVIDENCE_STREAM_MISSING, DRIVER_VERDICT_MISMATCH, GATE_RECOMPUTE_FAIL;
    - identity and history: ARTIFACT_MISMATCH, UNKNOWN_PRESENT, STATE_REPLAY_INVALID.
11. **Eligibility** (`use` validations only, §3.6; added in 16-I, because 16-H gave these codes no place in the order): ELIGIBILITY_NOT_ESTABLISHED (sub-code NOT_VALID, REVIEW_NOT_CURRENT or ACCEPTED_RECORD_ONLY), DEPENDENCY_REVOKED_NOW, APPROVAL_EXPIRED_NOW.
    - A `use` validation also evaluates groups 1 to 9 for U's attestation and head, the integrity flags for R, and the rejection codes of group 10 for R (§3.6 items 2 and 6).
    - STALE_TUPLE and STALE_AK stay in group 8.

**Ordering and evaluation:**
- Within a group, codes appear in the order listed, then by (role, seq).
- Every rule outside group 1 is evaluated, apart from those marked NOT_EVALUATED.
- UNTRUSTED_TIME (16-B) is kept as an umbrella label. It is emitted before the specific code whenever the attestation is UNVERIFIABLE, NONCANONICAL or HEAD_MISMATCH.

### 3.2 Record point: logical objects, instances, canonicality, flags and floors [P]

**Revised in 16-H.** In 16-G, `seen` stored the first VERIFIED instance and `instance` stored later ones, so the same inputs produced different records depending on arrival order (CAN-1 against CAN-2). 16-G's claim that this "affects no decision" also failed in one place: `floor_p0` sealed the raw bytes of "the highest-seq VERIFIED head" without saying which instance. 16-H separates authoritative inputs from derived records (§3.2.1), defines the aggregation (§3.2.2), ties canonicality to named instances (§3.2.3), and states order independence as testable claims (§3.2.4).

#### 3.2.1 Persisted model (input identity revised in 16-I)

**Identities.**
- An **instance** is ι = (role, `raw_sha256`, `sig_sha256`).
- A **logical object** is λ = (role, `content_sha256`), with `content_sha256` = SHA-256(C(parse(b))).
- An **input identity** κ (new in 16-I) is the key under which every import or presentation outcome is recorded:

| Input | κ |
|---|---|
| A signed-object file with its companion signature file, found at a store position or presented | (role, `raw_sha256`, `sig_sha256`). The role is derived from the position by VS-1, or is ⊥ if the position maps to no role. `sig_sha256` is the SHA-256 of the signature file's bytes as read, or ⊥ if there is no signature file |
| A file in a kit directory | (`kit`, `sha256`) |

- **How κ relates to ι:**
  - For a VERIFIED input, κ = ι. So every instance identity is an input identity.
  - An input identity whose role or signature is ⊥ can never be VERIFIED (VS-1, VS-3).
- **Why κ has these parts.** Role, raw bytes and signature can each change the outcome, and κ keeps them apart:
  - the same bytes with a different signature give a different κ (CAN-8, CAN-17);
  - the same bytes and signature in a different role give a different κ (RKN-17, CAN-15).
- **Role-only invariant.**
  - VS-1 to VS-8 read an input's position only to derive its role. So the source reference (store, directory, file name) cannot change a verification outcome, and κ leaves it out.
  - If a later revision adds a check that reads more of the position, the part it reads must be added to κ.
  - Classification that depends on location reads the stores directly, through R's presented inputs at the validation of R (§3.3 step 6), and never `import` notes. The example is a rejection-role object found in run R's directory (§3.12A).
- **Round-trip check:** at the record point, the strict parse (VS-6) of c = C(parse(b)) must equal parse(b); otherwise the instance is UNVERIFIABLE(SCHEMA). So every field derived from c equals the same field read from b, without assuming anything about C.

**Records, and which fields are authoritative:**

| Record | One per | Fields | Status |
|---|---|---|---|
| `instance` | Distinct VERIFIED instance ι, **including the first** of its logical object | role, b, s, `raw_sha256`, `sig_sha256`, key class, `verified_by`, `content_sha256`, `canonical` | **role, b and s are authoritative.** Every other field is derived from them and from O or V_E |
| `seen` | Logical object λ | role, key class, `content_sha256`, c = C(parse(b)) | Derived. Its fields depend only on λ, so it is byte-identical whichever instance arrived first |
| `kit` | File in the kit store | `sha256`, the bytes | Authoritative bytes, addressed by hash (§3.1.2A) |
| `equivocation`, `fork`, `attestation-conflict`, `seal-conflict` | Flagged pair | kind; the unordered pair {λ1, λ2} | Derived audit records. `attestation-conflict` is new in 16-H: 16-G derived that flag but had no record kind for it |
| `import` (revised in 16-I) | Processing of an input identity κ at a source reference | κ; the source reference (a store position, or the presentation slot at step 6); the result: VERIFIED(ι) or UNVERIFIABLE(code), or for a kit file STORED or UNNAMED | Audit only. **Never a decision input.** Written when κ is first seen at a source reference, and whenever κ's result changes |
| `genesis` | State | `owner_anchor` | Authoritative only once the pre-state checks pass (§3.1.4). Today they cannot |
| `validation`, `accepted` | Validation | §3.3 step 8, including `inst_digest` (new in 16-I) and, in a `use` record, `currency_digest` (amendment A1) | `validation` records are the authoritative history for status (§3.5) and for review currency (§3.6) |
| `recovered` | Recovery | §3.7 | Audit |

**Rules:**
1. **Decision inputs** are only:
   - each instance's (role, b, s);
   - the kit-store bytes;
   - `owner_anchor`, once the pre-state checks pass;
   - the `validation` and `accepted` records;
   - for a run, the files in its own evidence directory and rejection directories (§3.2.3, §3.12A).

   Every other stored field is derived. §3.2.4 lists everything, inside and outside state, that a validation's result depends on (Δ).
2. **At load** (§3.3 step 3), every derived record is recomputed from the decision inputs. The result is STATE_CORRUPT if:
   - a stored derived record differs from its recomputation;
   - a `seen` record has no `instance`, or an `instance` has no `seen`;
   - two `instance` records have the same ι;
   - a persisted instance no longer verifies under O, or under its `verified_by` file.

   This detects stored records that disagree with each other. **It does not detect the removal of instances, or a rewrite that keeps the records consistent** (§3.7A).
3. **Idempotence and processing again, both by κ (revised in 16-I):**
   - An input whose κ already has a VERIFIED instance creates no `instance` record. If its source reference is new, only an `import` note is added (CAN-5, CAN-16).
   - An input whose κ has no VERIFIED instance is processed again at every import step (§3.1.2A). A note is written only if its source reference is new or its result has changed.
   - **The result of κ in a state S**, r_S(κ), is VERIFIED(ι) if an `instance` record with ι = κ exists, or STORED if κ is a kit identity whose file is in the kit store. Otherwise it is the result in κ's most recent `import` note.
4. **UNVERIFIABLE inputs** appear only in `import` notes.
5. **Fixture states.** ST_A, ST_G and H0 are re-expressed under this model: each 16-G `seen` record becomes a `seen` record plus an `instance` record for the instance it held. ST_CAN is defined in §6.13.

**Notation in §6:** "+seen(X)" means +seen(λ(X)) if λ(X) is new, together with +instance(ι(X)) if ι(X) is new.

#### 3.2.2 Aggregation and replay

Let I be the set of `instance` records in state; all are VERIFIED. Every function below depends only on I, the kit store and, for Res, the `import` notes.

| Function | Definition |
|---|---|
| Obj(I) | {λ(i) : i ∈ I} |
| Inst(λ) | {i ∈ I : λ(i) = λ} |
| rep(λ) | The element of Inst(λ) that comes first in this order: canonical before non-canonical, then `raw_sha256` ascending, then `sig_sha256` ascending. Used **only** where one instance must be named and no run manifest names it: when sealing `floor_p0` objects and flagged pairs (§3.3 step 5) |
| `seen_high` | The maximum `head_seq` over head logical objects in Obj, with the set of `content_sha256` values at that seq. More than one value means EQUIVOCATION |
| `max_time_seen` | The maximum, over owner-class logical objects in Obj, of `approved_at`, `issued_at` and `t_att`. `not_after` fields never count. Its **carrier** is the logical object attaining it that comes first in the order entry < head < attestation, then `content_sha256` ascending |
| Flags(I) | For each flag kind, the set of unordered pairs {λ1, λ2} in Obj that meet that kind's condition (table below) |
| V_E | §3.1.2A |
| h_latest | The highest `head_seq` in Obj (§3.12A) |
| Res(S) (new in 16-I) | {(κ, r_S(κ))} over every κ that an import step or a presentation has processed. **Audit only; no decision reads it.** Compared by OI-1 |
| `inst_digest` (new in 16-I; snapshot stated by A1) | SHA-256(C({"instances": the ascending list of [role, `raw_sha256`, `sig_sha256`] over I, "kit": the ascending list of kit-store `sha256` values})), over **S6**, the state after step 6 (S4 is the state after step 4). Recorded in every `validation` record. The `inst_digest` of R's latest seal or review is the reference §3.6 item 1 compares with. **No rule reads a digest of S4** |
| `currency_digest` (amendment A1; `use` validations only) | The same function over S6 with exactly one instance left out: the instance of U's own presented attestation, ι(att_U), whether step 6 recorded it or it was already in state. Nothing else is left out. Recorded in the `use` record, and compared by §3.6 item 1. If U's attestation was already recorded before R's latest review, that review's digest contains it and `currency_digest` does not, so R is not current for U: a fresh attestation is needed (fail-closed) |

- **Floors and PC-D-FRESH (b).** The floors above are computed over every VERIFIED owner-class logical object; that is the interim rule, and these unfiltered floors are what Proj holds (§3.2.4). If the owner chooses "accepted objects only", step 5 restricts Obj to logical objects belonging to accepted runs. That restriction depends on the `accepted` records (Δ5, §3.2.4), not on import order.
- **Replay** (§3.3 step 3): verify each `instance`; rebuild Obj, `seen`, the floors and their carriers, Flags and V_E from these functions; and compare them with the stored derived records (§3.2.1 rule 2).
- **Import order** (§3.3 step 4; revised in 16-I) is fixed:
  1. owner-class inputs;
  2. kit files, repeated until nothing more is added;
  3. V_E is recomputed;
  4. evidence-class inputs.

  Within each group, inputs are taken by role, then `raw_sha256`, then `sig_sha256` (kit files by `sha256`). The functions above do not depend on this order. The fixed order only makes one import step's record sequence reproducible.

**Integrity flags.** Each is derived from logical objects, and written as an audit record when first derived. It blocks every acceptance and every current-validity result it covers, and is checked against **current** state under every review option.

| Flag | Condition | Scope |
|---|---|---|
| **EQUIVOCATION** | Two VERIFIED heads with equal `head_seq` and different `content_sha256` | Global |
| **FORK** (interim coverage; PC-D-FRESH item c is open) | Any of: (a) two VERIFIED entries with equal `seq` and different `content_sha256`; (b) a VERIFIED head whose `head_entry_sha256` differs from the `content_sha256` of a VERIFIED entry at that seq; (c) a VERIFIED entry n whose `prev_hash` differs from the `content_sha256` of a VERIFIED entry n−1 | Global |
| **ATTESTATION_CONFLICT** | Two VERIFIED attestations with equal (`run_id`, `nonce`) and different content | That run only |
| **SEAL_CONFLICT** | Two VERIFIED seals with equal `run_id` and different content | That run only |

Identical content with different raw bytes or signatures is **never** a conflict. It is the same logical object.

**FORK coverage is an open decision** (PC-D-FRESH, item c).
- **Interim, fail-closed:** check every seen head and entry, as defined above.
- **The alternative** — checking only the highest head — leaves the FR-LATE-7 gap, where an older conflicting head goes undetected.
- Neither is decided.

**Trusted floors** come **only from VERIFIED owner-class logical objects** (entry, head, attestation): `seen_high` and `max_time_seen`, as defined above.

**Evidence-class objects** (seal, rejection):
- are recorded for deduplication, audit and run status;
- **never raise a floor;**
- their timestamps, if any, are never treated as owner time.

**UNVERIFIABLE objects never raise floors,** in any class.

**Whether verified-but-rejected owner-class objects raise floors is an open decision** (PC-D-FRESH, item b). "Verified-but-rejected" means non-canonical, future-dated, stale, replayed, conflicting, expired, or belonging to rejected runs.
- **Interim, fail-closed:** they do.
  - *Reason:* replay protection can then account for every authentic owner statement, and raising a floor can only ever cause rejections.
  - *Consequence:* an erroneous owner signature (for example, a future-dated one) blocks acceptance until real time passes it.
  - *Recovery under PC-D-OKEY:* rotate the owner key through the anchor channel, then run an explicit rebuild under the new signers file. Objects signed with the old key become UNVERIFIABLE and stop contributing.
- **The alternative** — raising floors only from accepted objects — weakens replay protection.
- Neither is decided.

#### 3.2.3 Canonicality of a run's evidence instances

**Positions.** Run R's evidence positions are:
- the attestation;
- each ledger entry up to h_R, keyed by the entry's content `seq` (clarified in 16-I: file names are not read, §3.2.1);
- the head h_R;
- the seal.

**Which instance fills each position:**
- **At P0,** the presented instance at each position other than the seal is checked at §3.3 step 6. The P0 `validation` record lists their keys ι as **`evidence_instances`** (new in 16-H).
- **The seal** is R's `seal.json` with `seal.sig` (§3.1.1): one file pair per run directory. A second seal with different content is SEAL_CONFLICT.
- **The seal's `evidence_manifest`** lists exactly one (`raw_sha256`, `sig_sha256`) for each other position. The bytes and signature of every listed instance are stored beside the seal, in R's evidence directory on the production side.

**Checks at seal and review, in order:**
1. Exactly one manifest entry per position. Otherwise EVIDENCE_LAYOUT_INVALID(DUPLICATE_POSITION) or EVIDENCE_LAYOUT_INVALID(MISSING_POSITION). Check 2 is then not evaluated for that position.
2. Each manifest entry equals the ι that the P0 record lists for that position. Otherwise EVIDENCE_LAYOUT_INVALID(P0_MISMATCH).
3. Each listed instance's bytes and signature are retrieved **from R's evidence directory** and hash-checked. Otherwise EVIDENCE_LAYOUT_INVALID(INSTANCE_UNAVAILABLE). A copy held in reviewer state is never used in its place.
4. VS-1 to VS-7 run on the retrieved bytes (§3.3 step 6).
5. Canonicality is computed from those bytes. Any non-canonical position gives NONCANONICAL.

**At P0,** canonicality is computed the same way, from the presented bytes.

**Never consulted:** the `canonical` field of any `instance` record, and every instance not named for a position. By construction, therefore:
- a canonical copy found elsewhere cannot cure a non-canonical evidence instance (CAN-3);
- a non-canonical copy found elsewhere cannot taint a canonical one (CAN-4).

**`floor_p0` objects** are checked for authenticity and content (§3.3 step 5), **not** for canonicality. Their sealed representatives are rep(λ).

**Two valid signatures over identical bytes** are two instances of one logical object. They are not a conflict, and they affect no decision unless one is named for a position. Whether the pinned tool can produce such a pair is assumption E143; CAN-7 is BLOCKED-PIN on it.

#### 3.2.4 Order-independence claims (design claims, not established; revised in 16-I)

**Definitions:**
- **Import step:** step 4 of any validation, or of an import-only invocation (§3.3).
- **File set F:** a finite set of files, each a location and its bytes, delivered to the primary store, the secondary rejection store or a kit directory. Kit files are part of F (16-H wrongly held the kit store fixed).
- **Reordering:** two sequences σ1 and σ2 of import-only invocations that start from the same state S0 and deliver the same F in different orders, where:
  - no validation of any run happens during either sequence;
  - every delivered file stays in its store until the end;
  - the last invocation of each sequence runs with all of F present.
- **Late arrival:** a file reaching state after a validation instead of before it. It is not reordering.
- **Proj(S)** (revised in 16-I) is computed from the authoritative inputs, not read from stored derived records. It consists of:
  - the set I, with every instance's bytes, signature, `verified_by` and canonical flag;
  - Obj and the `seen` records;
  - the floors over every VERIFIED owner-class logical object, with their carriers (unfiltered: under the "accepted objects only" alternative of PC-D-FRESH (b), step 5 filters them by Δ5);
  - Flags;
  - V_E and the kit store (its hashes; the bytes follow from them);
  - **Res(S):** every processed input identity κ with its result r_S(κ) (§3.2.1 rule 3).
- **Not in Proj:** record order, record numbers, state-log prefix hashes, write times, source references, the order of `import` notes, and pending intent files.

**Why κ, and not `raw_sha256`.** 16-H recorded import results per `raw_sha256`. That key cannot tell apart:
- identical bytes with a valid signature and with a corrupted one (CAN-8), which would give one key two results;
- identical bytes and signature in two roles (RKN-17, CAN-15), which are VERIFIED as a head but UNVERIFIABLE as an attestation.

Under κ each of these has its own entry. Two files with the same κ at different source references share one entry and one result (CAN-5, CAN-16). Res is part of Proj for OI-1, but no rule reads it, so including it in Δ1 below only makes OI-3's condition stricter.

**Claims:**

| Claim | Statement |
|---|---|
| OI-1 | For a reordering σ1, σ2 of a file set F from a starting state S0, with a fixed anchor and a fixed starting kit store, the states after the last invocation have equal Proj, including equal Res |
| OI-2 | The states before the last invocation may differ. For example, an evidence-class κ can be UNVERIFIABLE in one order and VERIFIED in the other, and a kit file UNNAMED in one and STORED in the other. The design reasons the final states agree are: the last import step processes again every κ without a VERIFIED instance and every kit file not yet stored, against the final O and V_E (§3.1.2A, §3.3 step 4); V_E only grows; and a VERIFIED instance stays VERIFIED |
| OI-3 | Two validations V1 and V2 of the same kind, for the same run, whose decision-input tuples Δ (below) are equal, give the same code list, the same decision and byte-identical reports |
| OI-4 | Late arrival is outside OI-1 to OI-3; §3.4 (LATE_EVIDENCE) and §3.12A (observation order) govern it. Under the interim rules, the design claims that late arrival can never make a run VALID or ELIGIBLE when early arrival of the same files would not. The one designed exception is a review after PC-D-REVIEW is recorded (§3.5) |

**The decision-input tuple Δ(S, V) for OI-3 (new in 16-I).** S is the state when validation V takes the lock. OI-3 requires every component to be equal:

| # | Component | What must be equal | What reads it |
|---|---|---|---|
| Δ1 | Proj(S) | As defined above | Steps 3 to 7: floors, flags, V_E, h_latest, rep(λ), carriers, kit-store bytes, `inst_digest` |
| Δ2 | Consistency | In both states, every stored derived record equals its recomputation, and there is no pending intent file. A state that fails this aborts with STATE_CORRUPT, or runs R-PEND; OI-3 does not cover it | Steps 2a and 3 |
| Δ3 | Pre-state inputs | The `genesis` record; the chain-check outcome; the BT-0 outcome, meaning the same validator build (with or without the fixture-only stub) and the same boundary configuration; the bytes of `allowed_signers.owner` | Step 2 |
| Δ4 | H_R | The contents of run R's `validation` and `accepted` records, in record order, leaving out record numbers, prefix hashes and write times | Step 6a (R's AK as the purpose AK); §3.2.3 (`evidence_instances`); §3.13 (the `ak_sha256` binding); §3.5 (status); §3.6 item 1 (`inst_digest` of R's latest seal or review) |
| Δ5 | A | The set of every run's `accepted` records (their contents) | Floors, but only if the owner chooses "accepted objects only" under PC-D-FRESH (b). It is required anyway, so that OI-3 holds whichever way that decision goes |
| Δ6 | Stores | The files present to step 4 (location and bytes) in the primary store, the secondary rejection store and the kit directory, and whether each store is available | Step 4; IMPORT_UNAVAILABLE |
| Δ7 | Presented inputs | Everything §3.3 step 6 and §3.13 read for R, and for U at a `use` validation: the attestation; the ledger entries and head; the AK; R's evidence directory (seal, manifest instances, `floor_p0` objects); R's directories in both rejection stores; the streams, events log and VM journal; the artifact files and ledger row; the RGI | Steps 5 to 7; §3.13 |
| Δ8 | Clock, parameters, option | m0, m1, m_start and m_seal (and, at a `use` validation, m0_use and m), the wall-clock reading at each, and `t_use`; S, J, W, Δ_att, Δ_start, E_max; the review option in force | §3.4, §4 |
| Δ9 | Lock | Both validations take the lock | Step 1 |

**Why Δ is argued to be sufficient.** Each step's output is a function of Δ and of earlier steps' outputs:
- step 1: Δ9;
- step 2: Δ3;
- steps 2a and 3: Δ2, then Δ1;
- step 4: Δ1 and Δ6. Which inputs are processed depends only on which κ lack a VERIFIED instance and which kit hashes are stored, and the fixed order makes the processing deterministic;
- step 5: the Proj after step 4, with the audit reference kept out of the report; at seal and review, `floor_p0` from Δ7 and O from Δ3;
- step 6: Δ7;
- step 6a: the purpose head from Δ7; the purpose AK from Δ7 (P0 and `use`) or from Δ4 (seal and review);
- step 7: the Proj after step 6, together with Δ4, Δ5, Δ7 and Δ8. At a `use` validation, `currency_digest` is a function of the Proj after step 6 and of U's presented attestation (Δ7) (amendment A1).

**The report** — the bytes whose SHA-256 step 8 records — holds only:
- the kind, run_id and option;
- the code list, and the decision: PASS, FAIL, or ABORT with its codes;
- the floor reference, as carrier content hashes;
- the purpose heads and the purpose AK hash;
- `evidence_instances` (P0), `inst_digest`, and at a `use` validation `currency_digest` (amendment A1);
- at a `use` validation, U's attestation content hash and h_U.

Each of these is a function of Δ. Record numbers, prefix hashes, write times and source references are left out.

**What OI-3 does not cover:** the records each validation writes (their numbers and places in the log differ), and validations whose states fail Δ2.

**Status.** OI-1 to OI-4 are claims about the specified functions. The sufficiency of Δ is argued step by step above; **it is not proved and not tested.** The claims become evidence only when CAN-9, CAN-11, CAN-12, CAN-18 to CAN-20, LIFE-12 and FR-HIST-13 are executed under §8.3. **None has been.**

### 3.3 Validation sequence [P] (step order revised in 16-H; steps 4, 6 and 8 revised in 16-I)

16-G's step 2 (R-PEND), step 2a (BT-1, BT-2) and step 4a (BT-3 to BT-6) are replaced. The pre-state checks are now step 2, pending recovery is step 2a, and BT-3 to BT-7 are step 6a. Steps 1 and 3 to 9 keep their numbers.

1. **Lock.** Take the exclusive state lock, waiting at most 60 s; otherwise STATE_UNAVAILABLE.
2. **Pre-state checks:** the chain check, BT-0, BT-1 and BT-2 (§3.1.4). The first three are all evaluated; any failure aborts with nothing recorded.
2a. **Pending recovery.** Run R-PEND (§3.7). Pending `kit` records are applied first. Pending owner-class instances are re-verified under O, and pending evidence-class instances under the kit-store file their `verified_by` names. Records that fail are quarantined, not applied. Step 3 then checks everything, including V_E membership.
3. **Load state.** Recompute every derived record from the decision inputs (§3.2.1, §3.2.2).
   - Re-run VS-1 to VS-7 on every `instance`: the owner class under O; the evidence class under its `verified_by` file, which must still be in V_E.
   - Rebuild Obj, `seen`, floors, Flags and V_E.
   - Any mismatch → STATE_CORRUPT. EVIDENCE_KEY_REUSE in V_E → abort.
4. **Import** (revised in 16-I). From the primary store, the secondary rejection store and the kit directory, **excluding the directories of the run under validation**, take every input whose κ has no VERIFIED instance, and every kit file not yet stored:
   - in the fixed order of §3.2.2: owner-class inputs; kit files, repeated until nothing more is added; V_E recomputed; evidence-class inputs;
   - record VERIFIED instances, kit files, derived records and `import` notes (§3.2.1 rule 3), and write durably.
5. **Snapshot the trust floor F** (owner-class logical objects only).
   - **At P0:** F is `floor_p0`. It is sealed into the run's evidence as:
     - rep(λ), raw bytes and signature, for the highest-seq VERIFIED head;
     - rep(λ) for the carrier of `max_time_seen`;
     - rep(λ) for each logical object in a flagged pair;
     - **an audit reference:** the state log's record number and prefix SHA-256. **It is never a decision input** (§3.2.4, §3.7A).
   - **At seal or review:** F is `floor_now`. The validator **must** also load `floor_p0` from the sealed evidence and re-verify each of its objects through VS-1 to VS-7 under O. A failure, or a content or hash mismatch with the sealed values, gives **SNAPSHOT_INVALID and aborts under every option**, including `current-floor`.
6. **Presented objects.** Run VS-1 to VS-7 on the run's presented instances: the attestation, ledger entries and head, and, at seal or review, the seal and rejection objects. At a `use` validation (16-I), the presented instances are U's attestation, the ledger entries and head up to h_U, and R's seal and rejection objects (§3.6). At seal and review the instances are the ones the manifest names, retrieved from R's evidence directory (§3.2.3). Record VERIFIED ones and flags. Write an `import` note for every presented input under §3.2.1 rule 3, with the presentation slot as its source reference; this is added in 16-I, because RKN-9, RKN-10 and RKN-17 expect such notes. Write durably. F is not changed.
6a. **Purpose resolution and the remaining bootstrap** (moved from 16-G step 4a):
   - determine the purpose head(s) from the run's VERIFIED attestation (and, for a use U, from U's) per §3.12A, and the purpose AK per §3.1.2A;
   - run BT-3 to BT-7 (§3.1.4);
   - if the attestation is UNVERIFIABLE, BT-3 to BT-7 are NOT_EVALUATED (group 2);
   - an abort here writes no `validation` record. Instances recorded at steps 4 and 6 stay.
7. **Evaluate** in precedence order:
   - floors per §3.4;
   - integrity flags against current state (after step 6);
   - canonicality and layout of the run's evidence instances (§3.2.3);
   - time rules per §4;
   - at seal and review, the evidence rules of §3.12, §3.12A and §3.13.
8. **Write one `validation` record** holding:
   - run_id; kind (p0, seal, review, or `use` — new in 16-I, for §3.6); the option in force;
   - the floor reference: the carriers' content hashes, plus the audit reference, which is kept outside the report;
   - the purpose heads and the purpose AK's hash;
   - at P0, `evidence_instances` (§3.2.3);
   - `inst_digest` (§3.2.2) of the state after step 6 (new in 16-I);
   - at a `use` validation, `currency_digest` (§3.2.2; amendment A1);
   - at a `use` validation, U's attestation content hash and h_U;
   - the code list and the report SHA-256. The report's content is defined in §3.2.4.

   On PASS at seal or review, also write `accepted`. A `use` validation never writes `accepted`. If the write fails, the result is NOT ACCEPTED, and a would-be rejection follows §3.12's unrecorded path.
9. **Unlock.**

**Import-only invocation (new in 16-H).** It runs steps 1, 2, 2a, 3, 4 and 9 only, and writes no `validation` record. Fixtures use it to vary arrival order with no decision in between (CAN-9, CAN-11). Today it aborts at step 2, like every validation.

A run's own objects can never cause a rollback finding against themselves:
- at P0, F excludes them;
- at seal and review, they are inside `floor_now`, but for a run whose P0 passed every one of them has seq equal to its head and timestamps ≤ `t_att` + S (§4.4).

### 3.4 Review-order rule and later-import effects [P]

The choice of option is **PC-D-REVIEW, undecided.**

| Option | ROLLBACK | CLOCK_ROLLBACK |
|---|---|---|
| `p0-floor` | Against `floor_p0` only | Against `floor_p0` only |
| `issued-before` | Against `floor_p0`, plus any later VERIFIED head with seq > the run's head_seq and `issued_at` ≤ `t_att` | Against `floor_p0` only |
| `current-floor` | Against `floor_now` | Against `floor_now` |

- **In every option:** EQUIVOCATION, FORK, ATTESTATION_CONFLICT and SEAL_CONFLICT use current state, and SNAPSHOT_INVALID is mandatory.
- **Interim rule, until the owner decides (not a decision):** a result passes only if it passes against both `floor_p0` and `floor_now`. Any failure that occurs only against `floor_now` carries the flag LATE_EVIDENCE.

**How a later VERIFIED import affects an existing run's status** (statuses are defined in §3.5):

| Later import contains | `p0-floor` | `issued-before` | `current-floor` | Interim |
|---|---|---|---|---|
| Equivocation or fork, at any seq | INVALIDATED | INVALIDATED | INVALIDATED | INVALIDATED |
| A conflicting attestation, or a conflicting seal, for this run | INVALIDATED | INVALIDATED | INVALIDATED | INVALIDATED |
| A rejection-role object for this run | Per §3.12A | Per §3.12A | Per §3.12A | Per §3.12A |
| A head with seq > head_seq and `issued_at` ≤ `t_att` | No effect | INVALIDATED (ROLLBACK) | INVALIDATED | SUSPENDED/HELD (ROLLBACK + LATE_EVIDENCE) |
| A head with seq > head_seq and `issued_at` > `t_att` | No effect | No effect | INVALIDATED | SUSPENDED/HELD |
| A signed owner time τ with τ − S > `t_att` | No effect | No effect | INVALIDATED (CLOCK_ROLLBACK) | SUSPENDED/HELD |
| A revocation of the run's evidence-key registration | No historical effect; eligibility fails (§3.12A) | Same | Same, plus ROLLBACK from the later head | Same, plus SUSPENDED |
| UNVERIFIABLE objects; consistent older or equal objects; other instances of known logical objects; evidence-class objects for other runs | No effect | No effect | No effect | No effect |

Under `current-floor`, no run older than the latest head can pass a review. That is why the option is listed but not recommended.

### 3.5 Run status, derived and never stored [P]

| From | Event | To |
|---|---|---|
| — | P0 PASS | PENDING |
| — | P0 fails | REJECTED |
| PENDING | Seal PASS | VALID |
| PENDING | Seal fails, every failing code flagged LATE_EVIDENCE | HELD |
| PENDING | Seal fails otherwise; or the run is abandoned (§3.26) | REJECTED |
| VALID | Review PASS | VALID |
| VALID | Review fails, only LATE_EVIDENCE codes | SUSPENDED |
| VALID, SUSPENDED or HELD | Review fails with any other code, including SNAPSHOT_INVALID | INVALIDATED (from HELD: REJECTED) |
| SUSPENDED or HELD | PC-D-REVIEW recorded, then a review | VALID if PASS; otherwise INVALIDATED (from HELD: REJECTED) |
| Any non-terminal | An **authenticated** rejection for the run (§3.12A) | REJECTED (from PENDING or HELD) or INVALIDATED (from VALID or SUSPENDED); code RUN_REJECTED |
| Any non-terminal | SEAL_CONFLICT for the run | REJECTED (from PENDING or HELD) or INVALIDATED (from VALID or SUSPENDED) |
| Any | A rejection-role object for the run classified REJECTION_KEY_REVOKED, REJECTION_UNVERIFIABLE, REJECTION_UNSIGNED or REJECTION_MISFILED (§3.12A) | Current status **UNKNOWN** (not terminal). Acceptance and eligibility are blocked. Recorded history is unchanged |
| Any | Review aborts on the reviewer side (pre-state codes including STATE_INTEGRITY_UNESTABLISHED and TRUST_ANCHOR_MISSING, other BT codes, STATE_*, IMPORT_UNAVAILABLE, SIGNERS_INVALID) | Current status **UNKNOWN**. Recorded history is unchanged and no `validation` record is written |
| REJECTED or INVALIDATED | Anything | Unchanged (terminal). Later reviews are still recorded |

**How status is computed.** Status is computed by applying these transitions to the run's `validation` records of kind p0, seal and review, in record order. **Records of kind `use` (16-I) never change status** and are not part of this computation. **Each validation classifies evidence using the state it holds at that moment.** Replaying the same records gives the same status, and nothing is ever deleted or rewritten.

**Supersession and revocation.**
- A newer VALID run changes an older run's status only through the option in force (§3.4).
- A revocation in a later head does not change historical status. It affects current eligibility, and the classification of rejections first seen after it (§3.12A).

### 3.6 Current eligibility for downstream use [P]

A consumer — for example the runbook §6 checkpoint step — may rely on run R for a use U only if the validator computes ELIGIBLE(R, U). **From 16-I, ELIGIBLE(R, U) is computed by a validation of kind `use`** (§3.3), with U's attestation as the presented attestation, h_U as the purpose head and U's AK as the purpose AK. Its codes are ordered by §3.1.5 group 11. All of the following must hold:

1. **Status and review currency** (revised in 16-I):
   - R's derived status is VALID; otherwise ELIGIBILITY_NOT_ESTABLISHED(NOT_VALID);
   - R's most recent `validation` record of kind seal or review is a PASS, and its `inst_digest` equals this `use` validation's `currency_digest` (§3.2.2): the digest of its state after step 6, leaving out only U's own attestation. It is evaluated at step 7. Otherwise ELIGIBILITY_NOT_ESTABLISHED(REVIEW_NOT_CURRENT).
   - **Amended 2026-10-26 (A1).** As issued, 16-I compared with this validation's state after step 4. What U itself presents at step 6 — ledger entries, a newer head, rejection objects for R found at step 6 — then escaped the comparison (FR-HIST-16).
   - 16-H's wording, "a review completed after the latest import", depended on record order, which Proj and Δ leave out (§3.2.4).
   - **Consequence:** any instance added after R's last seal or review — whether by an import, by another run's presented objects, by an earlier use's attestation, or by what U itself presents other than its own attestation (A1) — makes that review not current, and R must be reviewed again before the next use. This is deliberately conservative (FR-HIST-14, FR-HIST-15).
   - **Consequence under the interim review rule (stated in 16-I).** Under `current-floor` and under the interim rule, a review fails with CLOCK_ROLLBACK (flagged LATE_EVIDENCE under the interim) once any owner statement later than R's `t_att` + S has been seen. A previous use's attestation is such a statement. Combined with this item, R can be relied on **for at most one use** under those rules, until PC-D-REVIEW is decided (FR-HIST-15). The floor rules come from 16-G and 16-H; 16-H's looser wording allowed a second use to rely on a review that predated the first use's attestation, and 16-I's exact currency rule closes that.
2. **Integrity:** no EQUIVOCATION or FORK flag in current state, and no SEAL_CONFLICT for R.
3. **Fresh attestation:** U has its own fresh owner attestation (nonce, `t_use`, bound head h_U) that passes the trust bootstrap, §3.1 and §4.
4. **Approvals still in force at h_U:** otherwise **DEPENDENCY_REVOKED_NOW**. This covers:
   - every approval R depended on;
   - the PC-HR-KEYS registration of the evidence signers file that signed R's seal, which is the seal's `verified_by` (§3.12A);
   - the PC-D-AKIT approval of the AK.

   Each one's `not_after` must be ≥ `t_use` + (m − m0_use) + S at the moment of evaluation; otherwise **APPROVAL_EXPIRED_NOW**.
5. **Same tuple and AK:** R's tuple and AK equal those currently approved for U; otherwise STALE_TUPLE or STALE_AK.
   - **Consequence (noted in 16-H):** the AK pins the evidence signers file (§3.1.2), so every evidence-key rotation changes the AK. Item 5 therefore fails with STALE_AK for every earlier run, whatever PC-D-EKEY (b) decides. Choosing "rotation preserves eligibility" would also require revising this item and the AK's pin (§7.1).
6. **No rejection-role object** of any classification exists for R.

**An `accepted` record on its own is never sufficient** (ELIGIBILITY_NOT_ESTABLISHED(ACCEPTED_RECORD_ONLY)).

### 3.7 Reviewer-state durability [P]

16-B and 16-C are retained, with these changes:

- **Record kinds** (§3.2.1 says which fields are authoritative):
  - `genesis` — includes **`owner_anchor`** (the owner public-key line and principal, entered from the anchor channel). It is trusted only once the pre-state checks pass, and today they cannot (§3.7A, UR-16);
  - `seen` — one per logical object, holding **content-determined fields only** (revised in 16-H);
  - `instance` — one per distinct VERIFIED instance, **including the first** (revised in 16-H), with b, s, `verified_by` and the canonical flag;
  - `kit` (new in 16-H) — content-addressed AK and signers files (§3.1.2A);
  - `import`, `equivocation`, `fork`, `attestation-conflict` (new in 16-H), `seal-conflict`, `recovered`, `accepted`, `validation`.
- **Write protocol:** a pending intent file → `state.log.next` → `rename` → fsync of the directory → the pending file is quarantined.
- **R-PEND** runs at §3.3 step 2a, after the pre-state checks. (In 16-G it ran at step 2, before the anchor was checked.)
  - It re-verifies, then re-applies only `seen`, `instance`, `kit`, `import` and flag records, as §3.3 step 2a describes.
  - **It never re-applies `accepted` or `validation` records.**
  - An interrupted `init` leaves no committed `genesis`. BT-1 then fails, and `init` must be run again.
- **Corruption, rebuild and unavailability:**
  - STATE_CORRUPT is recoverable only through an explicit `rebuild`.
  - **`rebuild` and `init` require `owner_anchor` to be entered again from the anchor channel.** They never copy it from old state; without it they refuse with TRUST_ANCHOR_MISSING.
  - **Neither can establish state integrity** (UR-16). After either one, validations still abort at BT-0.
  - A rebuild after an **owner**-key rotation makes the old key's owner-class instances UNVERIFIABLE. An **evidence**-key rotation needs no rebuild, because earlier evidence files stay in V_E (§3.1.2A).
  - STATE_UNAVAILABLE has no fallback.
- **Location:** a local Linux filesystem (ext4, xfs or btrfs). Who may write to it is not specified (UR-12, UR-16).
- **Concurrency:** validators serialize on `flock`.
- **Conflicting submissions:** these are two presentations racing; they serialize, and each is evaluated against the state the other has written (FR-CONC-1).

### 3.7A Reviewer-state integrity boundary (new in 16-H) [P]

**What 16-G assumed without saying so.**
- Every validation trusted the `owner_anchor` in `genesis`, and the floors and integrity flags read from reviewer state.
- 16-G did not say who may write the state directory, and reviewer-state records carry no signature (§3.25).
- BOOT-11 expected an in-place edit of the anchor to be caught "because the chain breaks".
- **A hash chain shows only that each record follows the one before it.** Anyone who can write the directory can recompute the chain, or put back an older log that is still valid. A chain alone does not prevent a complete rewrite.

**Assumed threat model.** This is a proposal. Which classes are in scope is an open owner decision (PC-D-TRUST, extended in 16-H). The interim, fail-closed assumption is that SA-0 to SA-2 are in scope.

| Class | Adversary | In scope (interim) | What the design detects | Not detected |
|---|---|---|---|---|
| SA-0 | Accidental damage: torn or partial writes, media corruption, power loss | Yes | R-PEND; a broken chain (pre-state check); records that fail re-verification or disagree with recomputation (§3.2.1 rule 2), giving STATE_CORRUPT. Power-loss durability itself is UR-10 | Loss of the log's tail that leaves a shorter, well-formed log |
| SA-1 | Operator error: restoring the state directory from a backup or snapshot; copying state between hosts or parties; re-running `init` over existing state; editing files by hand | Yes | Nothing beyond SA-0 | A restored older state. Floors fall back, so older heads can be replayed; integrity flags and validation history are lost |
| SA-2 | An unauthorized local writer: any principal that can write the state directory but cannot replace the validator program or the kernel | Yes | Nothing beyond SA-0. If it can write **only** the state directory, an edited anchor is caught at BT-2 (TRUST_ANCHOR_MISMATCH), because the owner signers file still holds the real key | (a) Rollback or truncation of history. (b) Removal of instances, and with them the flags and floors they support, with the chain recomputed. (c) If it can also write the owner signers file, the AK and the ledger directory: a consistent rewrite of `genesis` with a substituted anchor K′, matched by a K′ owner file, AK and ledger (BOOT-13) |
| SA-3 | Control of the validator program, the kernel or the platform's root account | **No.** Outside PC-D-TRUST, which trusts the validator program and platform | — | Everything. It is listed so that no local mechanism is credited with resisting it |

**Partial self-repair is not detection.** After a rollback, step 4 re-imports whatever the stores still hold, so some floors may come back (BOOT-14b). The design does not require the stores to keep every file, and a writer that can roll back state may also be able to delete store files (BOOT-14).

**Existing evidence that does not close the gap:**
- **The hash chain** shows how the log was built, not who kept it.
- **The audit reference sealed into `floor_p0`** (the state log's record number and prefix SHA-256, §3.3 step 5):
  - no check in 16-G used it;
  - the driver host signs seals with the evidence key, so it does not resist SA-2 on that host;
  - a legitimate `rebuild` would invalidate it;
  - it differs between parties and between import orders.

  In 16-H it is an audit reference only.
- **`init` and `rebuild` require the anchor from the channel.** This protects the commands, not the files they write.
- **PC-D-TRUST** trusts the validator program, not the custody of the state directory. UR-12 (the path and permissions on the driver host) is still a design gap.

**Finding.** The existing design does not establish adequate protection. It is recorded as **UR-16: design defect, hard blocker** (§7.3).

**Fail-closed rule.**
- BT-0 (§3.1.4) cannot pass until an approved boundary exists.
- Every validation therefore aborts with STATE_INTEGRITY_UNESTABLISHED.
- Because the anchor in `genesis` is then not independently authenticated, BT-1 also emits TRUST_ANCHOR_MISSING.

**Requirements for a correction.** These are requirements only; **no mechanism is chosen.**

| ID | Requirement |
|---|---|
| SI-R1 | No in-scope adversary can rewrite, truncate or roll back the boundary's evidence. That includes the principal that runs the validator and, on the driver host, the custodian of the evidence key, even if the validator may append to it |
| SI-R2 | It binds `genesis` (including `owner_anchor`) and the state history's length and last-record hash, so that a validator can detect a rewrite, a truncation or a rollback, not just an in-place edit |
| SI-R3 | It is monotonic: a validator can tell the current state from an older valid one |
| SI-R4 | It is checked at BT-0 of every validation, before `genesis` is read. A failure, or the boundary being unavailable, aborts |
| SI-R5 | Setting it up and replacing it (at `init`, at `rebuild` and on owner-key rotation) does not rely on the state it protects |
| SI-R6 | The correction states which of SA-0 to SA-2 it resists. BOOT-13, BOOT-14 and BOOT-14b then become detection fixtures, with their expectations registered again |
| SI-R7 | Whether it shares infrastructure with the anchor channel (PC-D-OKEY b) is an owner decision. This note does not assume that it does |

**Analysis, not a decision.** Obtaining the anchor afresh from the anchor channel at every validation would address anchor substitution (SA-2 c). It would not address rollback of floors and history (SA-1; SA-2 a and b). The two needs are distinct.

### 3.8 Restore execution [P]

On any failure, the run goes to the §3.12 rejection machine.

| State | Entry condition | Evidence | Typical failure codes |
|---|---|---|---|
| INIT | Driver lock held (§3.26); run_id assigned | Events-log genesis | DRIVER_LOCKED |
| PRECHECK | Pre-state checks and trust bootstrap on the driver host (§3.1.4); validator: RGI and preconditions at the current head (§3.24), including PC-HR-KEYS for both signers files | Validator report | Pre-state codes (today always STATE_INTEGRITY_UNESTABLISHED and TRUST_ANCHOR_MISSING), other BT codes, REGISTRY_*, precondition codes |
| P0 | Nonce issued (m0); attestation received (m1); §3.3 run at P0; `floor_p0` sealed | Attestation; `validation` record | Freshness codes |
| PROVISION | VM created per PC-D-VM; network checks (NET fixtures) | VM journal | Carried from revisions ≤15 |
| TRANSFER | Artifact copied; dump, globals and manifest hashes equal the ledger (§3.18); provenance present (§3.23) | Hashes | ARTIFACT_MISMATCH, PROV_ABSENT, PROV_MISMATCH |
| CLUSTER | Copy initialized; T0 = system identifier recorded; DC check (§3.17) | `pg_controldata` output | DC_MISMATCH |
| BASELINE | LC-BASE configuration snapshot (§3.16) | Snapshot | CFG_* |
| R0 | Target identity guard | Guard result | GUARD_REFUSED_NO_EFFECT |
| R1 | Globals through sandbox, qcap and psql (§3.9, §3.10, §3.14) | Streams | **UR-3** |
| R2 | One sandbox per segment: S0–S8, guard, statements | qcap, log, supervisor | EB_MISMATCH, QCAP_*, FAILED |
| POST | LC-END; SCH, GLB, SEQ, DATA; SESSION-IDENTITY; RS-MATCH; CMPL | Captured outputs | Gate codes |
| SEAL | Seal built and signed with the evidence key in force at h_R (§3.25, §3.12A) | Seal | SIGN_FAILED |
| SEAL-VALIDATE | §3.3 at seal plus §3.13 replay | `validation` | Any code |
| TEARDOWN | VM destroyed; proof recorded (AG-TEARDOWN) | Proof | TEARDOWN_UNPROVEN |
| DONE | Status derived (§3.5) | — | — |

**UR-1:** how gates P1 to P5 map onto these states is defined in revisions ≤15 and must be reconciled from the archive.

### 3.9 Sandbox launch and supervisor (retained, with corrections) [P]

**Retained from 16-B, as amended in 16-C and 16-D:**
- the corrected bwrap command;
- `ckpt-hold` on the fd-0 handshake;
- supervisor states S0 to S8;
- supervisor checks SV-1 to SV-10;
- `qcap` admission (pid, uid, starttime; one connection per registration).

**Independent verification.** A sandbox's own report is corroboration only. The supervisor's checks of kernel state are authoritative. They cover:
- namespaces;
- mounts;
- visible sockets;
- credentials;
- environment;
- descriptors;
- network;
- PID-namespace membership;
- Yama;
- the postmaster's listeners.

Release is refused unless every check passes.

**SV-7 is UNVERIFIED.** It expects the environment {PATH, PWD}, following the author's unverified recollection of bwrap's PWD behavior (E122b, class M), until a source review supports it and SBX-ENV runs.

**Threat model [P].** The VM root, host kernel, hypervisor, supervisor, driver, `qcap`, the custodian of the evidence key and the validator program on each party's platform are trusted (PC-D-TRUST, PC-D-EKEY).
- Nothing here protects against an adversary who controls any of them.
- Processes outside the sandbox are covered by detection only (SESSION-IDENTITY), never by prevention.
- The owner key's authenticity rests on the trust anchor (§3.1.4), which is undefined (UR-15).
- The integrity of reviewer state, which holds the recorded anchor, the floors and the history, rests on a boundary that does not exist (UR-16, §3.7A).

### 3.10 qcap identity and protocol [P]

**Identity chain:**
- **psql:** OS user `postgres` (PG_UID), inside the sandbox. Connects to `/run/qcap/.s.PGSQL.5432`, which is `/run/cse-qcap/<RUN>/` on the host.
- **qcap listener:** OS user `postgres`. Takes the peer's credentials with SO_PEERCRED, with the pid translated into `qcap`'s PID namespace [E?] (E129).
- **Admission rule:** admit if and only if all hold:
  - the peer's pid, uid and starttime match the supervisor's registration (§3.9);
  - one connection per registration;
  - the registration arrives within 5 s.
- **Upstream connection:** `qcap` opens a new connection to the copy's server socket as OS user `postgres`.
  - The copy's `pg_hba` must contain only `local all postgres peer` for this path. It mirrors the production template, [R] `ops/postgres/pg_hba.conf:8`; the copy's own file is [P] (UR-11).
  - **The server sees `qcap`'s identity, never psql's.**
- **Mapping:** psql pid ↔ qcap connection ↔ upstream backend pid (`BackendKeyData`) ↔ log session (pid and session_id) ↔ registry segment. SESSION-IDENTITY is a bijection over these.

**Startup:**
- Only protocol 3.0 is accepted [E?], with `user` = `postgres`, `database` = DB(segment), and `application_name` = `cse-ckpt-<SEG>-<RUN>`.
- Any other parameter is refused (QCAP_STARTUP_REJECTED) until PROTO-1 establishes on the pinned tuple exactly which parameters psql sends (UR-5).
- `qcap` forwards the startup bytes unchanged.

**Supported messages** [E? message codes]. Any other message gives **QCAP_UNSUPPORTED**: both sides close, the segment becomes STARTED_INTERRUPTED, and the run is UNKNOWN.
- **Client to server:** Query `Q`, CopyData `d`, CopyDone `c`, CopyFail `f`, Terminate `X`.
- **Server to client:** AuthenticationOk only (`R` with code 0), `S`, `K`, `Z`, `T`, `D`, `C`, `I`, `E`, `N`, CopyInResponse `G`.
- **Explicitly unsupported:** SSLRequest and GSSENCRequest (E142); extended-protocol messages; NotificationResponse; NegotiateProtocolVersion; non-OK authentication requests.

**Special cases:**
- **Cancellation:** a CancelRequest on a separate connection (E141) is refused, giving QCAP_CANCEL_REFUSED and UNKNOWN.
- **Errors:** an ErrorResponse is forwarded. psql exits 3, and the segment is FAILED at statement i.
- **Closure:** a segment is complete only when the client sends Terminate after the final ReadyForQuery. An abrupt close by either side gives STARTED_INTERRUPTED.

### 3.11 Evidence collection [P]

**Streams:**
- the events log, kept on the production host and authoritative;
- the VM journal;
- the `qcap` capture, one per connection and in both directions;
- the server log capture;
- the supervisor log;
- the audit log (fixture runs only);
- the reviewer state (unsigned; its integrity is UR-16, §3.7A);
- the approvals ledger;
- the seal, any rejection records, and the run's sealed evidence manifest with the instances it lists (§3.2.3).

**Line format:** C({"v":1, "stream", "run_id", "n", "prev", "body"}) followed by LF.
- `prev` is the SHA-256 of the previous line's C-bytes; for the first line, 64 zeros.
- The stream head is (n, SHA-256 of the last line).
- Stream lines are **not** individually signed. They are bound by the stream head that the seal records, and that head is recomputed independently (§3.13).

**The server's JSON log** is wrapped: each raw line is appended as its bytes, together with its file name and byte offset. Malformed lines are captured raw and flagged (§3.15).

**Lifecycle:** OPEN → APPENDING → CLOSED (head written to the events log) → EXPORTED (copied to the production host) → VERIFIED (the production side's recomputed head equals the recorded head). GAP, TRUNCATED, UNREADABLE or HEAD_MISMATCH all give **UNKNOWN**.

### 3.12 Rejection machine [P]

1. **TRIGGERED** (the codes).
2. **STOPPING:**
   - no further commands are sent;
   - `qcap` admission closes;
   - the sandbox is killed with SIGKILL;
   - the cluster stops.
   - If that isn't confirmed within T_stop (PC-D-LEASE), the hypervisor hard-stops the VM (PC-D-HV).
3. **TAINTED:** the event is recorded, and the copy is never reused.
4. **RECORDING:** a rejection record (role `rejection`, schema §3.1.1) is signed with the **evidence key currently in force** (namespace `cse-checkpoint-rejection`). It is written to the primary **and** secondary stores, each with fsync.
5. **RECORDED:** both writes have been confirmed. Then TEARDOWN.

**Failure paths:**
- **Signing fails:** an unsigned marker flagged UNSIGNED is written to both stores, and the driver refuses new runs.
- **Either store write fails:** the run becomes **UNRECORDED_REJECTION**, and the driver refuses new runs until it is recorded.
- In neither case is the rejection ever reported as recorded.

**Classification of rejection-role objects** is defined in §3.12A. A rejection can only take acceptance away, never give it. **A rejected run can never be accepted,** because acceptance requires a seal plus §3.13, and §3.13 step 1 checks for rejection-role objects first.

### 3.12A Evidence-key authorization lifecycle (new in 16-G) [P]

**Definitions:**
- **E** is an evidence signers file in V_E, identified by its hash (§3.1.2A). For an evidence-class object, E is the object's `verified_by` (revised in 16-H).
- **a_E** is the owner-signed PC-HR-KEYS approval entry whose subject includes E. Its seq is s_a.
- **A revocation of a_E** is a ledger entry at seq s_r, interpreted by position (Revision 16 §5).
- **"E is in force at head h"** means s_a ≤ h, and no revocation of a_E exists at seq ≤ h.
- **h_R** is the head bound by run R's attestation. If R never reached P0, h_R is undefined.
- **h_latest** is the highest VERIFIED head in the validating party's current state.
- **h_U** is the head bound by a downstream use U's attestation.

All heads are owner-class objects, verified under the anchored owner file (§3.1.4).

**Three purposes, each with its own head:**

| Purpose | Question | Head(s) used | Rule | Failure |
|---|---|---|---|---|
| **Historical validation** (seal validation, reviews) | Was R's seal made by an evidence key authorized for R? | h_R | The E that verified the seal is in force at h_R | **EVIDENCE_SIGNERS_UNAUTHORIZED**: the seal cannot be used; NOT ACCEPTED |
| **Rejection classification** | Is a rejection-role object for R an authenticated rejection? | Every head h with h_R ≤ h ≤ h_latest (or h_latest alone, if h_R is undefined) | **Authenticated** if and only if the E that verified it is in force at some such h **and** in force at h_latest | E in force at some h in the range, but not at h_latest → **REJECTION_KEY_REVOKED**. E never in force in the range → **REJECTION_UNVERIFIABLE** + EVIDENCE_SIGNERS_UNAUTHORIZED |
| **Current eligibility** | May R be relied on now? | h_U | The E that signed R's seal is in force at h_U, plus §3.6 | **DEPENDENCY_REVOKED_NOW** |

**Classification of rejection-role objects for R** (any object in R's directory in either store):

| Object | Classification | Effect on R |
|---|---|---|
| VERIFIED; `run_id` = R; authenticated per the table above | RUN_REJECTED | REJECTED or INVALIDATED (terminal) |
| VERIFIED; `run_id` = R; E in force in [h_R, h_latest] but revoked by h_latest | REJECTION_KEY_REVOKED | Current status UNKNOWN (not terminal); acceptance and eligibility blocked |
| VERIFIED; `run_id` = R; E never in force in [h_R, h_latest] | REJECTION_UNVERIFIABLE, EVIDENCE_SIGNERS_UNAUTHORIZED | Current status UNKNOWN; blocked |
| UNVERIFIABLE (any VS failure, including a substituted seal or a cross-role signature) | REJECTION_UNVERIFIABLE | Current status UNKNOWN; blocked |
| An unsigned marker | REJECTION_UNSIGNED | Current status UNKNOWN; blocked |
| VERIFIED, but `run_id` ≠ R | REJECTION_MISFILED | Current status UNKNOWN for R; blocked. For the run it names, it is classified normally |

**Rotation and revocation:**
- **Rotation** means revoking a_E1 and approving a_E2 (a new PC-HR-KEYS registration), both by position in the ledger. Because the AK pins the evidence file, it also means a new AK (§3.1.2).
  - **E1 stays in V_E** (revised in 16-H). Seals and rejections it signed stay VERIFIED, with `verified_by` = E1, so the rows below can be reached after a rotation (LIFE-15, LIFE-16).
  - A review of a run sealed under E1 uses that run's own AK as the purpose AK (§3.1.2A). It does not compare that AK with the current files on disk, which 16-G's BT-5 did.
- **Revocation after sealing** (s_r > h_R):
  - historical status is unaffected, because the seal was authorized at h_R;
  - **current eligibility fails** (DEPENDENCY_REVOKED_NOW) for every use whose h_U ≥ s_r.
  - Whether a routine rotation should preserve the eligibility of earlier runs is an **open owner decision (PC-D-EKEY)**. The interim, which is fail-closed and not a decision, is that it does not. Any later re-endorsement mechanism is out of scope.
  - Independently of that decision, §3.6 item 5 fails with STALE_AK after any rotation, because the AK changes (§3.6, §7.1).
- **Revocation at or before h_R** (s_r ≤ h_R): the seal is unauthorized → NOT ACCEPTED.
- **Approval after h_R** (s_a > h_R): the seal is unauthorized → NOT ACCEPTED. This covers a seal signed by a successor key.
- **Rejections signed by a successor key** that is in force at h_latest are authenticated. A rejection can only block, so authenticating it with the current key is safe.

**Rejections signed "before" or "after" a revocation.**
- Evidence-class objects carry **no trusted time**, so "before" and "after" are decided by **observation order**.
- Each validation classifies rejection-role objects using the ledger state it holds at that moment (§3.5).
  - A rejection classified while its key was still in force gives RUN_REJECTED, which is terminal.
  - A rejection first classified after the revocation was observed gives REJECTION_KEY_REVOKED, which is UNKNOWN and not terminal.
- **Limit:** this is observation order, not signing time. A rejection forged with a compromised key before its revocation is observed cannot be told apart from a genuine one. Because a rejection can only block, the failure is fail-closed (a lost valid run), never an unsafe acceptance (LIFE-12).

**The `accepted` record never establishes current eligibility** (§3.6, LIFE-13).

### 3.13 Final acceptance: authenticating the seal and replaying the evidence [P]

The seal is a **signed claim by the evidence key**. Neither it nor the driver's verdict nor the VM journal is ever sufficient on its own. **Prerequisite:** the pre-state checks and BT-2 to BT-6 have passed, with h_R as the purpose head and R's AK as the purpose AK (§3.1.4, §3.1.2A). Today the pre-state checks cannot pass (UR-15, UR-16). Steps:

1. **Rejection first.** Classify every rejection-role object for the run, in both stores (§3.12A). Any classification stops acceptance.
2. **Seal authentication:**
   - the seal (role `seal`) is VERIFIED under an evidence file in V_E, using that file's principal and namespace `cse-checkpoint-seal` (§3.1.2A); otherwise SEAL_INVALID;
   - **historical authorization:** the seal's `verified_by` file is in force **at h_R** (§3.12A); otherwise **EVIDENCE_SIGNERS_UNAUTHORIZED**;
   - there is no SEAL_CONFLICT for the run.
3. **The seal is bound to this run:**
   - its `run_id`, `nonce`, `attestation_content_sha256`, `head_seq` and `head_content_sha256` equal the run's own VERIFIED attestation and head; otherwise SEAL_RUN_MISMATCH or SEAL_BINDING_MISMATCH;
   - its `floor_p0` hashes equal the objects re-verified in §3.3 step 5;
   - its `ak_sha256` equals the AK recorded by R's P0 `validation` record; otherwise SEAL_BINDING_MISMATCH;
   - its evidence manifest passes the checks of §3.2.3: one instance per position, equal to the P0 record's `evidence_instances`, and retrievable from R's evidence directory with matching hashes. Otherwise EVIDENCE_LAYOUT_INVALID with sub-code DUPLICATE_POSITION, MISSING_POSITION, P0_MISMATCH or INSTANCE_UNAVAILABLE.
4. **Every stream is retrieved independently** from production-side storage. For each, the chain is intact, the stream is closed, and its recomputed head equals the head in the seal; otherwise STREAM_BROKEN or STREAM_HEAD_MISMATCH. The VM journal is cross-checked against the events log and is never the only source for any fact.
5. **Recompute every required gate instance from raw evidence** (§3.24). The seal's `driver_verdict` is compared with the recomputation, never used in its place; a disagreement gives DRIVER_VERDICT_MISMATCH.
6. **Freshness and canonicality:** §3.3 at seal, including the canonicality of the run's evidence instances, computed from the retrieved bytes (§3.2.3).
7. **Preconditions:** satisfied by current, valid approvals at h_R.
8. **Identities agree:**
   - the artifact: dump, globals and manifest hashes recomputed from the files equal both the seal and the ledger row; provenance holds (§3.23);
   - the target T0 differs from the source system identifier;
   - the run_id, tuple and AK match.

   Otherwise ARTIFACT_MISMATCH or the matching code.
9. **Nothing outstanding:** no UNKNOWN classification remains; otherwise UNKNOWN_PRESENT.
10. **State machine:** replay §3.8 from the production-side events log. Every transition must be permitted and the run must reach SEAL; otherwise STATE_REPLAY_INVALID.
11. **PASS:** write `validation` and `accepted`. This establishes **historical** acceptance only; current eligibility is §3.6.

**Limit:** comparisons that need the destroyed copy can only be recomputed from outputs captured inside the VM (under PC-D-TRUST). Examples are schema renders, sequence reads and data hashes. A compromised evidence key can forge a seal, but it cannot by itself produce acceptance, because steps 4–10 recompute from the streams. Forging the streams as well is outside the threat model (PC-D-TRUST).

### 3.14 Script transformation and capture [P]

**Script generation (pinned options; UR-6):**
- R2: `pg_restore -f <script> --create <dump>`, plus `--restrict-key=<K>` if and only if the pinned version supports it.
- Not used: `-L`, `--no-owner`, `--no-acl`, `--single-transaction`, `-j`.
- R1: the artifact's `globals.sql`.

**Supported constructs.** Anything else is UNSUPPORTED → UNKNOWN, detected **before** execution.
- SQL statements.
- `COPY … FROM stdin` with inline data ending in `\.`.
- `\connect <db>` as a **segment boundary only**:
  - EB splits the script there and removes the line;
  - each segment gets a new sandboxed psql with `PGDATABASE` = db;
  - the split is recorded.
- `\restrict K` and `\unrestrict K`, kept as they are. The R2b guard is SQL, which restricted mode allows [E?].
- **Everything else is unsupported,** including:
  - every other backslash command: `\i`, `\ir`, `\if`/`\elif`/`\else`/`\endif`, `\set`, `\unset`, `\gset`, `\gexec`, `\g*`, `\;`, `\copy`, `\bind`, `\parse`;
  - variable interpolation outside quotes, comments and dollar quotes.

**EB:**
1. split into segments;
2. insert the guard (§3.19);
3. run the psqlscan port, which yields the exact Query bytes psql sends.

On the bytes themselves:
- They include the trailing `;` and any leading `/* */` comment, and drop leading whitespace and `--` comments, as Revision 16 §2 read them from `REL_17_STABLE` [E?]. These must be re-read from the pinned tag.
- For COPY, EB yields the concatenated CopyData payload. Whether `\.` itself is transmitted is **UR-4**.

**Comparison rules:**
- **Equality:** the ordered Query payloads and concatenated CopyData that `qcap` observes must equal EB's, byte for byte. Otherwise EB_MISMATCH (a DEFECT class, per Revision 16).
- **Multi-statement Query:** if EB lexing finds more than one statement in a single Query → MULTI_STATEMENT_QUERY → UNKNOWN. (Under simple-query semantics, an error skips the rest of the message [E?] (E135), so outcomes cannot be mapped statement by statement.)
- **psql parser behavior at end of file:** EB's model must match; if not, EB fixtures fail and AG-EB stays closed.
- **Server parse errors:** ErrorResponse → FAILED.

### 3.15 Log and protocol reconciliation [P]

**Copy settings (earlier revisions):**
- `log_destination` = `jsonlog`;
- `log_statement` = `all`;
- `log_min_error_statement` = `error`;
- `log_connections` and `log_disconnections` on;
- `log_min_messages` = `warning`.

**Hypotheses about record shapes, all UNVERIFIED until FX-JSON and LRC run on the pinned tuple:**

| Operation | Expected records |
|---|---|
| Successful statement | One statement record |
| Syntax error | No statement record; an ERROR whose `statement` field holds the text [E?] (E134) |
| Analysis, planning or execution error | A statement record, then an ERROR |
| Warning | A WARNING record |
| Connection | The connection records |
| Disconnection | A disconnection record |
| COPY | A statement record only |
| Multiline statement | One message containing LF |

**Never recorded by `log_statement`:** statements run inside functions or DO blocks, and syntax-error statements. A session-level `SET log_statement` gives UNKNOWN (CMPL-4).

**Rule.** Sessions are matched through `BackendKeyData`. Within each session:
- each `qcap` (operation, outcome) pair must map, through the fixture-established shape function, to its expected records exactly once, in `line_num` order;
- interleaving across sessions is allowed;
- no ordering across sessions is used, except connection and disconnection brackets.

**Failures, each giving UNKNOWN:** LOG_UNKNOWN_SHAPE, LOG_MISSING, LOG_DUPLICATE, LOG_MALFORMED, LOG_GAP, and any ambiguous mapping.

### 3.16 Configuration snapshot and session pinning [P]

**Sets:**
- **G** = the parameters set by the AK's configuration files (the exact list lives in the AK).
- **S_set** = the parameters the script `SET`s, extracted by EB.
- G ∩ S_set must be empty; otherwise CFG_SET_CONFLICT before the run.

**Snapshot algorithm (deterministic):**
1. The driver hashes every file in the configuration chain — `postgresql.conf`, every include in include order, `postgresql.auto.conf`, `pg_hba.conf`, `pg_ident.conf` — into H1, sorted by path.
2. A registered observer session, in a single transaction, reads:
   - `pg_conf_load_time()` and `pg_postmaster_start_time()`;
   - every row of `pg_file_settings`, ordered by `seqno`;
   - `pg_settings` (name, setting, unit, source, sourcefile, sourceline, pending_restart) for G, ordered by name;
   - `pg_conf_load_time()` again.
3. The driver re-hashes, giving H2.
4. **Stable** if and only if H1 = H2 **and** both load times are equal. Retry up to 3 times; then CFG_UNSTABLE.
5. The snapshot is C(the object above), and its hash is recorded.

**Checks.** Configuration-source states are never mixed.
- **LC-BASE**, before R0. For every g in G:
  - source = `'configuration file'`, with the expected sourcefile;
  - setting equal to the expected value;
  - pending_restart = false;
  - exactly one `pg_file_settings` row for g in the chain, with applied = true;
  - no row anywhere with a non-null error;
  - `postgresql.auto.conf` hash equal to the expected value;
  - H1 equal to the AK's hashes.

  Otherwise CFG_BASELINE_MISMATCH, or CFG_PENDING where the cause is a pending change.
- **LC-PIN**, inside the guard, which is the first statement of every session. For every g in G: `current_setting(g)` equals the expected value, and source = `'configuration file'`.
- **LC-END**, after the last segment: take the snapshot again. It must be **identical** to LC-BASE (files, load time, postmaster start, rows). Otherwise CFG_CHANGED: the copy is tainted.
- **LC-OBS:** any reload or parameter-change record in the log between the two snapshots gives CFG_CHANGED.
- **Stable observation boundary:** the interval between LC-BASE and LC-END. Nothing inside it may change.

### 3.17 data_checksums [P]

`data_checksums` is **not** in G. The declared expectation is X ∈ {on, off}, recorded in the AK. **X is an open owner decision** (PC-D-PGCHK, extended); this note does not choose it. For reference, [R] `worker/ops/verify_server.py:54` expects `on` for production.

Three observations must agree with X:
1. `SHOW data_checksums` = X;
2. `pg_controldata`'s "Data page checksum version" = 1 if X is on, else 0 [E?] (E140);
3. the `initdb` invocation in the VM journal includes `--data-checksums` if and only if X is on.

**Optional DC-D** (PC-D-PGCHK): `pg_checksums --check` at teardown, with the cluster stopped.

**Failure codes:** DC_MISMATCH, DC_UNPARSABLE (UNKNOWN), DC_CORRUPT.

### 3.18 Restore artifact and restore paths

**The artifact [R]:**
- `pg_dump … -Fc --snapshot <id>`, `worker/ops/backup.py:112`;
- `globals.sql` from `pg_dumpall --globals-only --no-role-passwords`, `:10` and `:117`;
- manifest format `cse.backup.manifest.1` with `tool_version` and `code_revision`, `:31` and `:123`;
- files fsynced, set to 0440, renamed, and the directory set to 0550, `:140–149`;
- the ledger finish record carries `artifact_sha256`, `:151`;
- the snapshot query records `server_version`, `:105`.

**The existing direct mode [R]** (`worker/ops/restore_check.py:74–79`):
- globals go through psql with `check=False`, and "already exists" errors are filtered out of stderr;
- then `pg_restore -d postgres --create --exit-on-error`.

**The checkpoint path [P]** is script mode (§3.14):
- R1 and R2 go through the sandbox and `qcap`;
- **no credit transfers from direct mode** (AG-SCRIPTMODE, PC-FX-SM).

**Before TRANSFER and R0:**
- dump, globals and manifest SHA-256s equal the ledger row;
- the manifest format is known;
- provenance is valid (§3.23);
- pinned versions: the copy's PostgreSQL 17 minor must be ≥ the dump's `server_version`, and `pg_restore` is pinned.
- **UR-7 (closed by amendment A2, 2026-10-26):** the manifest records the producing `pg_dump` binary's version (`pg_dump_version`, [R] `worker/ops/backup.py:130`), the source's `server_version` and `data_checksums` (`:126`) and its bootstrap superuser (`:129`). The `pg_dumpall` version is not recorded separately.

### 3.19 Restore-session guard [P]

The R2b-inserted first statement of every segment. The exact text is in the AK:

```
DO $guard$ BEGIN
  IF (SELECT system_identifier FROM pg_control_system()) <> <T0>
     OR current_database() <> '<db>' OR current_user <> 'postgres'
     OR inet_server_addr() IS NOT NULL
     OR pg_postmaster_start_time() <> '<pm_start>'::timestamptz
     OR pg_conf_load_time() <> '<load_time>'::timestamptz
     OR <each g in G: current_setting(g) <> expected>
  THEN RAISE EXCEPTION 'cse-checkpoint guard refused'; END IF;
END $guard$;
```

**Guard-to-side-effect boundary:**
- **Same backend:** later statements run on the guard's backend. A new backend needs a new connection, which `qcap` refuses.
- **Restart:** the connection drops → STARTED_INTERRUPTED → UNKNOWN; the copy is tainted.
- **Reload or configuration change:** not prevented. It is detected by LC-END and LC-OBS → CFG_CHANGED → tainted.
- **Target change:** impossible within a session; every new session repeats the guard.

**Independent processes.** A cooperative lock (including any advisory lock the guard takes) **prevents nothing** for a process that doesn't cooperate.
- **Containment assumption:** only the driver, supervisor, `qcap` and registered sessions reach the copy (PC-D-TRUST, VM isolation).
- **Detection:** SESSION-IDENTITY, the log, and the end-state gates.

**Tainted copy:** any rejection or UNKNOWN after CLUSTER means the VM is destroyed (AG-TEARDOWN). It is never repaired or reused.

### 3.20 Operation-to-evidence reconciliation [P]

**Levels:**

| Level | Content |
|---|---|
| L_toc | `pg_restore -l` of the artifact |
| L_script | Generated segments |
| L_eb | Expected operations |
| L_wire | `qcap` operations and outcomes |
| L_log | Log records |
| L_state | End-state gates |

**Relations, none assumed one-to-one:**
- **L_toc ↔ L_script:** each TOC entry appears once, in order, as its header comment block [E?].
  - Entries that produce no statements are allowed only for types on a fixture-established allowlist.
  - Otherwise OP_TOC_MISMATCH → UNKNOWN.
- **L_script → L_eb:** deterministic (§3.14).
- **L_eb ↔ L_wire:** exact equality of sequence and bytes.
- **L_wire ↔ L_log:** §3.15.
- **L_state:** comparisons only (§3.21, §3.22, DATA). There is no per-operation mapping.

**Failure cases:**
- **Partial restore:** the first error gives FAILED → REJECTED. A partial restore is never accepted.
- **Skipped entries:** impossible without `-L`. A missing entry → UNKNOWN.
- **Duplicate, reordered or missing operations:** EB_MISMATCH or LOG_*.
- **Reconnects:** refused by `qcap`.

### 3.21 Sequence verification [P]

1. **Expectation:** only setval statements of the exact form `SELECT pg_catalog.setval('<literal>', <int8>, <true|false>);` found in the EB stream [E?] (E137). Any other form, or any `nextval` or `ALTER SEQUENCE … RESTART` in the script → SEQ_UNSUPPORTED.
2. **Mapping:** after the restore, an observer session resolves each literal with `'<literal>'::regclass::oid`. Each OID must have relkind `S`, and be targeted by exactly one setval (otherwise SEQ_DUPLICATE).
3. **Completeness:** the set of non-system sequence OIDs must equal the set of setval targets. Otherwise SEQ_UNMAPPED or SEQ_EXTRA.
4. **Capture:** `SELECT last_value, is_called FROM <schema>.<seq>`, read directly before any `nextval` [E?].
5. **Check:** §4.5.
6. **Not used:** `pg_sequences.last_value` (its NULL and cache semantics), and caching in general.
7. **Rejected** (SEQ_UNSUPPORTED) until FX-SEQ covers them: unlogged sequences, sequences belonging to extensions, identity-column sequences, temporary sequences.
8. **Ownership, identity and grants** are checked by §3.22, not here.

**UR-8:** the sequence kinds actually present in production have not been enumerated.

### 3.22 Schema and globals reconciliation [P]

**SCH:**
- R_src = `pg_restore --schema-only -f -` of the artifact.
- R_copy = the same command run on `pg_dump -Fc` of the copy, with the same pinned versions and the options of `backup.py:112` minus `--snapshot`.
- Both renders use the same fixed `--restrict-key` if it is supported.
- They are compared **byte for byte.**
- **Normalization N1** (exactly the two "Dumped from / by … version" header lines) is allowed only if FX-SCH proves that those lines are the only difference.

**GLB:**
- G_src = the artifact's `globals.sql` [R].
- G_copy = `pg_dumpall --globals-only --no-role-passwords` run on the copy.
- Compared byte for byte.
- **Normalization N2** (header lines; `\restrict`/`\unrestrict` key lines [E?]) is allowed only if FX-GLB proves it.

**What is covered:**
- role attributes, memberships, and `ALTER ROLE … SET` (GLB);
- ownership, grants, extensions and database settings (SCH) [E? where each one appears];
- predefined roles that are not dumped, and the bootstrap superuser.

The bootstrap superuser must match production's. The manifest records it (`bootstrap_superuser`, [R] `backup.py:129`), and P1's restore check creates its cluster with that name, falling back to `cse_restore_check` only when the field is missing ([R] `restore_check.py:71`). (Corrected by amendment A2: as issued, this sentence said that direct mode used `cse_restore_check`.) Restoring `globals.sql` into such a cluster raises "role … already exists" for that role, which direct mode tolerates (UR-3).

**If equivalence isn't proven,** AG-SCHEMA and AG-GLB stay unpassed.

### 3.23 Source-backup provenance (P1-CC-PROV, a separate change control) [P]

**What the change must do:**
1. Select `(SELECT system_identifier FROM pg_control_system())` **in the same statement** as `pg_export_snapshot()` ([R] `backup.py:105`), in the same transaction.
2. Keep that transaction open until `pg_dump` returns ([R], as read earlier).
3. Record the identifier in the manifest, with a new format value replacing `cse.backup.manifest.1` ([R] `:31`), and in the ledger finish record alongside `artifact_sha256` ([R] `:151`) and the manifest hash.
4. Keep finished ledger rows immutable (migration 0011's guard [R], as read earlier).

**How verification rejects artifacts:**

| Condition | Code |
|---|---|
| Legacy format `cse.backup.manifest.1` | PROV_ABSENT. This applies to **all current dumps** |
| Manifest and ledger identifiers differ | PROV_MISMATCH |
| A hash differs | ARTIFACT_MISMATCH |

**Sequence:** independent audit → acceptance (PC-CC-P1PROV) → deployment → fresh dumps only.

**Freeze status:** `backup.py` is **not** among HB-1's pinned `worker/ops` files ([R] `worker/financial_backfill/preflight.py:94–99` lists `__init__`, `dbhash`, `migrate`, `redact`, `settings`, `spool`). Other freeze records must be checked before making the change (UR-9).

### 3.24 Gate registry and validator contract [P]

**The RGI** = C({rgi_version, gates, preconditions, aliases, streams}):
- each gate is {id, kind, scope ∈ {global, run, segment, session}, required, inputs, depends_on};
- each precondition is {id, kind, subject_kind, tuple_bound, depends_on};
- each alias is {alias, targets, allowed_contexts: ["archive"]}.

**What the validator checks** (after the trust bootstrap):

| Check | Failure code |
|---|---|
| `rgi_sha256` equals the subject of the PC-D-RGI approval in force | STALE_RGI |
| The run's gate instances equal **exactly** the set derived from the RGI for this run | REGISTRY_MISSING / REGISTRY_DUPLICATE / REGISTRY_UNKNOWN_ID |
| No alias appears in any run, ledger or seal context | REGISTRY_ALIAS_USED |
| Every required stream is present and VERIFIED | EVIDENCE_STREAM_MISSING |
| The AK and tuple equal the approved ones | STALE_AK / STALE_TUPLE |
| Each gate instance is recomputed | DRIVER_VERDICT_MISMATCH |

**A driver PASS is never trusted.**

### 3.25 Canonical JSON, events, seal and rejection schemas [P]

**The seal** = C({seal_version, run_id, nonce, **signer_principal**, attestation_content_sha256, head_seq, head_content_sha256, floor_p0 (for each object, rep(λ)'s content and raw hashes; §3.2.2), **evidence_manifest** (each required position other than the seal itself, with its instance's raw and signature hashes; §3.2.3), rgi_sha256, ak_sha256, tuple, artifact {dump_sha256, globals_sha256, manifest_sha256, ledger_row_id, provenance}, target {t0}, streams [{name, n, head_sha256}], driver_verdict {gates, final}}).
- It has role `seal`. It is signed by the **evidence key in force at h_R**, namespace `cse-checkpoint-seal`, and `signer_principal` is the principal of that key's evidence file (§3.1.1, §3.1.2A).
- The bytes and signature of every instance in `evidence_manifest` are stored beside the seal in the run's evidence directory (§3.2.3).
- The state-log audit reference sealed with `floor_p0` is never a decision input (§3.7A).
- It is authenticated and replayed per §3.13.

**The rejection record** = C({rejection_version, run_id, nonce (or null), **signer_principal**, stage, codes, streams [{name, n, head_sha256}]}).
- It has role `rejection`. It is signed by the **evidence key in force when it is recorded**, namespace `cse-checkpoint-rejection`.
- It is classified per §3.12A.

**Not signed:** stream lines (§3.11), reviewer-state records (whose integrity is UR-16, §3.7A), and the unsigned rejection marker.

**A signed seal or a driver verdict is never sufficient on its own.**

### 3.26 Orchestration failure semantics [P]

**Production-side state is authoritative. The VM journal alone is not.**

| Failure | Detection | Action | Status |
|---|---|---|---|
| Lost communication | No heartbeat for the lease interval (PC-D-LEASE) | Hard stop (PC-D-HV); rejection | REJECTED (ABANDONED) |
| Interrupted command | Commands are {run_id, cmd_seq, body}, each run at most once and journaled | Re-sending the same seq returns the journaled result; never re-run | — |
| Same seq, different body | Journal comparison | Reject | COMMAND_CONFLICT |
| In-flight restore | STOP command | §3.12 STOPPING | REJECTED |
| Driver restart | An open run without a seal on the production side | Abandon; teardown; **never resume** | REJECTED |
| Duplicate driver start | Production-side exclusive lock plus host check (PC-D-1HOST) | The second driver refuses | DRIVER_LOCKED |
| Failed durable write | Write or fsync error | Stop; reject | REJECTED, or UNRECORDED_REJECTION |
| Rejection store unavailable | Write error | Driver lockout | UNRECORDED_REJECTION |
| Signing failure (seal or rejection) | Signing error | No acceptance; unsigned marker; lockout | REJECTED (UNSIGNED), shown to validators as REJECTION_UNSIGNED |
| Trust bootstrap fails on the driver host | Pre-state or BT codes at PRECHECK. Today this is always the case (STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING) | No run starts | — |

---

## 4. Equations and boundary cases

**All numeric values in this section are synthetic fixture parameters.** They exist only to derive the expected results of fixtures. **They are not proposed operating values,** and none is approved. Operating values are open decisions (PC-D-FRESH, PC-D-SKEW, PC-D-LEASE, PC-D-EMAX). When they are decided, every fixture expectation must be re-derived from the formulas below. Other numeric constants in this note (timeouts, retry counts) are design proposals, subject to AK review, and are not approved either.

### 4.1 Time model [P]

| Symbol | Meaning |
|---|---|
| m0 | Driver monotonic time when the nonce is first displayed |
| m_s | The unknown monotonic time at which the owner signed. Because the nonce is unpredictable, m0 ≤ m_s ≤ m1 |
| m1 | Attestation received |
| m_start | P0 |
| m_seal | Seal |
| `t_att` | The owner's clock at m_s |
| w(m) | The driver's wall clock at m |
| D | m1 − m0, the delivery window |
| d | m1 − m_s, the delivery delay; 0 ≤ d ≤ D |
| δ | The offset between the driver's wall clock and the owner's clock: w(m) = `t_att` + (m − m_s) + δ |

**Assumption E132:** rate drift is negligible over Δ_att + Δ_start + E_max.

### 4.2 Clock skew versus delivery delay [P]

**Observed values.** At each checkpoint k ∈ {m1, m_start, m_seal} observed so far:

  x_k = w(m_k) − `t_att` − (m_k − m1) = δ + d.

So δ ∈ [x_k − D, x_k].

**Rules:**
- **CLOCK_STEP** if max x_k − min x_k > J.
- Otherwise, let L = max x_k − D and U = min x_k:
  - **CLOCK_SKEW** if U < −S or L > S (the possible δ values are definitely outside the allowance);
  - **DELIVERY_AMBIGUOUS** if it is not CLOCK_SKEW but L < −S or U > S (delay and skew cannot be told apart, so fail closed);
  - **OK** otherwise.
- **For constant x:** OK if and only if D − S ≤ x ≤ S. Consequently, **no attestation can pass once D > 2S,** whatever values are chosen.

**Fixture parameters (synthetic):** S = 300 s, J = 2 s, Δ_att = 900 s, Δ_start = 900 s.
- These reproduce the values first proposed in 16-B, so that the fixtures expose C-a: with them, the delivery window is 2S = 600 s while Δ_att = 900 s.
- **C-a is an unresolved parameter decision** (PC-D-SKEW). Interim, not a decision: DELIVERY_AMBIGUOUS rejects.

### 4.3 Expiry horizons (from 16-D) [P]

- **T_hi(P0)** = `t_att` + (m_start − m0) + E_max + S.
- **T_hi(seal)** = `t_att` + (m_seal − m0) + S.
- E_max is the maximum allowed m_seal − m_start. **Fixture parameter (synthetic):** E_max = 3600 s.
- **RUN_EXCEEDS_EMAX** if m_seal − m_start > E_max. This is the **interim** rule; PC-D-EMAX is open.

| Code | Fires when |
|---|---|
| APPROVAL_EXPIRED | T_hi > `not_after` |
| HEAD_EXPIRED | T_hi > `not_after_head` |
| HEAD_STALE | T_hi − `issued_at` > W |
| HEAD_WINDOW_TOO_LONG | `not_after_head` − `issued_at` > W |

**Fixture parameter (synthetic):** W = 86400 s.

**Consequence:** if m_seal − m_start ≤ E_max, then T_hi(seal) ≤ T_hi(P0).

### 4.4 Freshness rules [P]

| Code | Fires when |
|---|---|
| FUTURE_ENTRY | `approved_at` > `t_att` + S |
| FUTURE_HEAD | `issued_at` > `t_att` + S |
| HEAD_BEFORE_ENTRY | `issued_at` < the last entry's `approved_at` |
| NONMONOTONIC | `approved_at` decreases as seq increases |
| ATTESTATION_STALE | m1 − m0 > Δ_att, or m_start − m1 > Δ_start |
| CLOCK_ROLLBACK | `t_att` < F.`max_time_seen` − S, with F as in §3.4. Skipped if F has no floor |
| ROLLBACK | head_seq < F.`seen_high`, or the §3.4 option's later-head condition holds |

**Implied co-occurrences (16-C):**
- FUTURE_ENTRY always comes with FUTURE_HEAD or HEAD_BEFORE_ENTRY.
- HEAD_STALE always comes with HEAD_EXPIRED or HEAD_WINDOW_TOO_LONG.

### 4.5 Sequence check [P]

Let Q be the set of setval targets and P the set of non-system sequence OIDs.
- Require |Q| = the number of setvals (no duplicates) and Q = P.
- Then, for every sequence s: (`last_value`_s, `is_called`_s) = (v_s, c_s), where v_s and c_s come from s's setval.

### 4.6 Status transitions and authorization

- **Status:** status(R) = fold(§3.5 transitions, the `validation` records of R in record order). Each validation classifies evidence with the state it held. Status is deterministic and never stored.
- **Order-independence claims:** OI-1 and OI-2 (§3.2.4) compare Proj(S), whose import results are keyed by the input identity κ; OI-3 compares the decision-input tuple Δ. They are claims, not established results.
- **E** for an evidence-class object is its `verified_by` (§3.1.2A). E_seal and E_rej below are the seal's and the rejection's.
- **InForce(E, h)** ⇔ s_a(E) ≤ h ∧ ¬∃ a revocation of a_E at seq ≤ h. If an entry needed to decide this is missing from state, InForce is false (§3.1.4 BT-3).
- **Seal authorized for R** ⇔ InForce(E_seal, h_R).
- **Rejection authenticated for R** ⇔ ∃ h ∈ [h_R, h_latest] : InForce(E_rej, h), and InForce(E_rej, h_latest).
- **REJECTION_KEY_REVOKED** ⇔ ∃ h ∈ [h_R, h_latest] : InForce(E_rej, h), and ¬InForce(E_rej, h_latest).
- **Eligibility evidence condition** ⇔ InForce(E_seal, h_U).

---

## 5. Mandatory gate and precondition inventory

The inventory is reconstructed from Revision 16's list and the 16-A to 16-H additions. Completeness for revisions 4–15 is **unproven** until PC-D-DOCS exists and the RTM runs (UR-14). Every item is **unapproved or unpassed.**

### 5.1 Preconditions

**Owner decisions:**
- **Existing:** PC-D-TRUST, PC-D-PH, PC-D-VM, PC-D-XFER, PC-D-CON, PC-D-HV, PC-D-LEASE, PC-D-EKEY, PC-D-OKEY, PC-D-SEC, PC-D-RS, PC-D-PGVER, PC-D-PGCHK, PC-D-MGR, PC-D-RGI, PC-D-AKIT, PC-D-FRESH, PC-D-1HOST, PC-D-LOGFMT, PC-D-LOGALL, PC-D-QCAP, PC-D4, PC-D7, PC-D8, PC-D9, PC-D10, PC-VC-P1, PC-D-DOCS.
- **Added in 16-E:** PC-D-REVIEW, PC-D-SKEW, PC-D-EMAX, PC-D-BASE, PC-D-INDEP.
- **Subjects extended in 16-G:**
  - **PC-D-OKEY:** now includes the **trust-anchor channel and record** (§3.1.4; UR-15).
  - **PC-D-EKEY:** now includes **whether routine rotation preserves the eligibility of earlier runs** (§3.12A).
- **Subjects extended in 16-H:**
  - **PC-D-TRUST:** now includes (b) **which adversary classes (SA-0 to SA-3) the reviewer-state integrity boundary must resist**, and (c) **custody of that boundary's evidence** (§3.7A; UR-16).
  - **PC-D-EKEY (b):** now records its dependency on §3.6 item 5 and on the AK's pin of the evidence file.
  - **PC-D-OKEY (b):** "the record" of the anchor explicitly covers whether `genesis` is an adequate place for it, which depends on UR-16.
- **No owner decision is added, removed or renamed in 16-H.**

**Fixture acceptances:**
- **Existing:** PC-FX-IMG, PC-FX-EXPCFG, PC-FX-DC, PC-FX-SEQ, PC-FX-JSON, PC-FX-FAIL, PC-FX-EB, PC-FX-QCAP, PC-FX-RS, PC-FX-SCH, PC-FX-GLB, PC-FX-SM, PC-FX-E2E, PC-FX-CI, PC-FX-VM.
- **Added in 16-E:** PC-FX-GRD (guard), PC-FX-ORC (orchestration).
- **Where the fixture families belong:**

| Family | Accepted under |
|---|---|
| PROTO | PC-FX-QCAP |
| LRC | PC-FX-JSON and PC-FX-FAIL |
| CFG | PC-FX-EXPCFG |
| CKS | PC-FX-DC |
| OPR | PC-FX-RS |
| SQV | PC-FX-SEQ |
| SGR | PC-FX-SCH and PC-FX-GLB |
| ADV-15 to ADV-22 | PC-FX-EB |
| RKN (16-F) | AG-FRESH (owner-class rows) and AG-SEAL (evidence-class rows) |
| BOOT (new in 16-G; BOOT-13 to BOOT-17 and BOOT-14b added in 16-H) | AG-FRESH (BOOT-1 to BOOT-6, BOOT-10 to BOOT-17, BOOT-14b) and AG-SEAL (BOOT-7 to BOOT-9) |
| LIFE (new in 16-G; LIFE-15 to LIFE-18 added in 16-H) | AG-SEAL and AG-STATUS |
| CAN (new in 16-G; CAN-10 to CAN-14 added in 16-H; CAN-15 to CAN-20 added in 16-I) | AG-FRESH (CAN-1 to CAN-10, CAN-12, CAN-15, CAN-17, CAN-18); AG-SEAL (CAN-11, CAN-13, CAN-14, CAN-16, CAN-19, CAN-20); AG-STATUS (CAN-12) |

None of these needs a new fixture-acceptance bundle.

**Change controls:** PC-CC-P1PROV (for P1-CC-PROV), PC-CC-VC, PC-CC-CKPT-IMPL, PC-CC-RUNBOOK6, PC-CC-RF4.

**Host registrations:** PC-HR-PROD, PC-HR-HV, PC-HR-KEYS.
- **PC-HR-KEYS:** the SHA-256 of **both** `allowed_signers.owner` and `allowed_signers.evidence`, plus the two principal strings. It is approved through an owner-signed ledger entry. **It does not authenticate the owner key;** the trust anchor does (§3.1.4).

**Dependencies:** as in Revision 16 §1.1, plus:

| Item | Depends on |
|---|---|
| PC-D-REVIEW | — |
| PC-D-SKEW | PC-D-FRESH |
| PC-D-EMAX | PC-D-LEASE |
| PC-D-BASE | — (PC-FX-IMG depends on it) |
| PC-D-INDEP | — |
| PC-FX-GRD | PC-FX-RS, PC-FX-EXPCFG |
| PC-FX-ORC | PC-D-LEASE, PC-D-HV, PC-D-SEC |
| PC-HR-KEYS | PC-D-OKEY (including the anchor), PC-D-EKEY |
| PC-D-AKIT | PC-D-OKEY (including the anchor) |
| PC-D-TRUST (b, c) | — (the correction for UR-16 depends on it) |
| PC-D-OKEY (b) | Its "record" part depends on the correction for UR-16 |

### 5.2 Aliases

These are permitted only in archived text, and are rejected everywhere else (REGISTRY_ALIAS_USED):

| Alias | Target |
|---|---|
| D1 | PC-CC-VC |
| D2/D3 | COMPOUND → PC-D-VM, PC-D-PH |
| D5 | COMPOUND → PC-FX-IMG, PC-D-AKIT |
| D6 | PC-CC-CKPT-IMPL |
| Unprefixed D-* and FX-* names (revisions 4–15) | The matching PC-D-* or PC-FX-* |
| D4, D7, D8, D9, D10 | PC-D4, PC-D7, PC-D8, PC-D9, PC-D10 |
| VC-P1 | PC-VC-P1 |
| BAD_SIG_FORMAT | SIG_FORMAT |
| WRONG_PRINCIPAL | PRINCIPAL |
| FR-STALE-1, FR-STALE-2, FR-CLK-1 to FR-CLK-4, FR-FUT-1 to FR-FUT-3, FR-EXP-2 to FR-EXP-4 | Their FB rows (16-C table) |

### 5.3 Gates

**Acceptance gates:**
- **Carried:** AG-NET, AG-EVID, AG-CONN, AG-IMG, AG-ID, AG-GAP, AG-CFGOBS, AG-DC, AG-SEQ, AG-ORD, AG-SCHEMA, AG-GLB, AG-SEAL, AG-MGR, AG-LOGFMT, AG-LOGCOV, AG-JSON, AG-MC8, AG-1HOST, AG-SCRIPTMODE, AG-RS, AG-FAIL, AG-EB, AG-QCAP, AG-JCOMP, AG-REG, AG-RGI, AG-APPROVALS, AG-EBVAR, AG-E2E, AG-BIND, AG-TEARDOWN.
- **Added in Revision 16:** AG-PRECOND, AG-RTM, AG-QID, AG-CMPL.
- **Added in 16-A:** AG-QBYPASS, AG-FRESH.
- **Added in 16-C:** AG-PINSRC.
- **Added in 16-E:** AG-STATUS (§3.5–§3.6), AG-ORCH (§3.26), AG-GUARD (§3.19). AG-GUARD may overlap the carried AG-ID or AG-BIND, whose definitions are in revisions ≤15 (UR-2).
- **No gate is added, removed or renamed in 16-G, 16-H or 16-I.** 16-H added UR-16 as a condition of five existing gates (below). 16-I adds fixtures to existing gate conditions only.

**Conditions.** All of these gates are **closed**:

| Gate | Requires |
|---|---|
| AG-PINSRC | Complete pin records; every class-M behavior at SUPPORTS; every class-F behavior at SUPPORTS or INSUFFICIENT; no CONTRADICTS anywhere (precedence rules 1–4) |
| AG-QBYPASS | AG-PINSRC, plus every sandbox behavior (E116, E121, E122a, E122b, E122c, E123, E124, E125, E126a to E126e, E129, E130, E117, audit witness) FIXTURE-CONFIRMED under precedence rule 5 |
| AG-FRESH | **UR-15 and UR-16 resolved**; E127 and E128 at SUPPORTS and FIXTURE-CONFIRMED; the owner-class RKN rows; BOOT-1 to BOOT-6, BOOT-10 to BOOT-17 and BOOT-14b, with BOOT-13, BOOT-14 and BOOT-14b re-registered as detection fixtures under the UR-16 correction (SI-R6); CAN-1 to CAN-10, CAN-12, CAN-15, CAN-17 and CAN-18; FB, FR and FR-HIST fixtures |
| AG-SEAL | **UR-15 and UR-16 resolved**; E127 at SUPPORTS and FIXTURE-CONFIRMED; the evidence-class RKN rows; BOOT-7 to BOOT-9; LIFE-1 to LIFE-18; CAN-11, CAN-13, CAN-14, CAN-16, CAN-19, CAN-20; SLV-1 to SLV-9 |
| AG-STATUS | **UR-15 and UR-16 resolved**; FR-HIST-1 to FR-HIST-16; LIFE-1 to LIFE-18; CAN-12 |
| AG-APPROVALS, AG-PRECOND | **UR-15 and UR-16 resolved**, plus their existing conditions |
| AG-QID | E129 and E113 (class F) FIXTURE-CONFIRMED; QID-1 to QID-7; PROTO-15 |

**Other items:** VC-1 to VC-8; ACC-1 to ACC-6; P1-CC-PROV.

**Per-run gates and identifiers** (meanings marked † are carried by name only, UR-2):
- P0, plus P1 to P5 †, and P-PROV;
- C3, R0, R1, R2, R2b, C4-LOG †, C4-AUTH †, G1 to G5 †, F5 †;
- LC-BASE, LC-PIN, LC-END, LC-OBS, CFGO †, DC, DC-D;
- EB-1 to EB-5 †; failure classes FC-1 to FC-7 †;
- RS-MATCH, MC †, JREC †, SCH, DATA, SEQ, GLB;
- SESSION-IDENTITY, S-QID, S-QRESP, S-SANDBOX, S-FRESH;
- T0.

**Dependencies** (selected; the full set is in the RGI):

| Gate | Depends on |
|---|---|
| AG-APPROVALS | AG-FRESH |
| AG-RTM | PC-D-DOCS, PC-D-RGI |
| AG-QBYPASS | AG-PINSRC |
| AG-SEAL | AG-STATUS, PC-HR-KEYS |
| AG-FRESH, AG-SEAL, AG-STATUS, AG-APPROVALS, AG-PRECOND | UR-15 (the trust anchor, under PC-D-OKEY) and UR-16 (the state-integrity boundary, under PC-D-TRUST b and c) |
| AG-E2E | Every fixture acceptance |
| P-PROV | PC-CC-P1PROV |
| F5 | PC-CC-VC |

---

## 6. Fixture inventory

### 6.0 Execution status of every test (new in 16-H)

**Every test in this note is proposed and unexecuted.** None has been implemented, run, or independently reviewed. No fixture expectation has been pre-registered in an approved archive (§8.3 item 2). This covers every family, including:
- FB-1 to FB-37; FR-* (including FR-REC, FR-NONCE, FR-NS, FR-EQ, FR-CONC, FR-INT, FR-IMP); FR-LATE-*; FR-HIST-*; LED-*, PCV-*;
- RGV-*, SLV-*, ORC-*; SBX-*, SM-*, QID-*, QB-*, PROTO-*;
- FX-*, ADV-*, SPL-*, CMPL-*, LRC-*; EXPCFG, CFG-*, CKS-*, GRD-*, OPR-*, SQV-*, SGR-*, PROV-B*, PVR-*, FX-E2E-*, NET-*;
- SRC-*; RKN-1 to RKN-24; BOOT-1 to BOOT-17 and BOOT-14b; LIFE-1 to LIFE-18; CAN-1 to CAN-20; FR-HIST-1 to FR-HIST-16; RTM-*.

**16-I adds or revises fixtures (CAN-5, CAN-8, CAN-9, CAN-11, CAN-15 to CAN-20, FR-HIST-12, FR-HIST-14, FR-HIST-15, FR-REC-8, LIFE-13); amendment A1 adds FR-HIST-16. Their expectations are proposed and derivable only; none has been executed.**

**What the labels mean:**
- **BLOCKED-PIN:** the expectation must be pre-registered only after source review on the pinned tuple (§8.3).
- **Derivable:** the expectation follows from the rules in this note, using the synthetic fixture parameters of §4, a synthetic trust anchor, the fixture-only BT-0 stub (§6.11) and the interim rules where a decision is open. **Derivable is not executed,** and it says nothing about whether an implementation meets the expectation.
- **Defect demonstration (new in 16-H):** the expectation records a result the design should **not** have (BOOT-13, BOOT-14). It documents UR-16. Under SI-R6 it must become a detection fixture when UR-16 is corrected.

Fixtures for the rules changed in 16-H are listed in §6.14.

**Shorthand used in this section:**

| Shorthand | Meaning |
|---|---|
| ST_A, ST_G | Initial reviewer states (16-C). From 16-G, each also holds a synthetic `owner_anchor` matching the fixture owner key. From 16-H, each is re-expressed under §3.2.1: every `seen` record has an `instance` record for the instance it held |
| ST_CAN | §6.13 (new in 16-H) |
| BT-0 stub | A fixture-only BT-0 that reports success without checking anything (§6.11). Present in every derivable fixture unless the row says otherwise |
| +seen(X) | +seen(λ(X)) if λ(X) is new, together with +instance(ι(X)) if ι(X) is new (§3.2.1) |
| Template | The template run `run-a` (16-C), with x = 0 and D = 60 s |
| PASS-A, PASS-G | PASS state changes starting from ST_A or ST_G (16-D) |
| FAIL-A, FAIL-G | P0 failure state changes starting from ST_A or ST_G (16-D) |
| FAIL-SEAL | P0 changes, then a seal failure with no `accepted` record |

### 6.1 Boundary fixtures FB-1 to FB-37 (derivable from fixture parameters)

Rows marked **✱** have results that changed in 16-E. Nothing changed in 16-F, 16-G or 16-H.

| ID | Inputs | Expected | State |
|---|---|---|---|
| FB-1 ✱ | m1 = 900 (D = 900), x = 0 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-2 ✱ | m1 = 901 | {ATTESTATION_STALE, DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-3 | m_start = m1 + 900 | PASS (T_hi 13:21:00Z / 12:30:00Z) | PASS-A |
| FB-4 | m_start = m1 + 901 | {ATTESTATION_STALE} | FAIL-A |
| FB-5 | x = +300 | PASS | PASS-A |
| FB-6 ✱ | x = +301 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-7 ✱ | x = −300 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-8 | x = −301 | {CLOCK_SKEW} | FAIL-A |
| FB-9 ✱ | x = 0, 0, then +300 at seal | {CLOCK_STEP} at seal | FAIL-SEAL |
| FB-10 ✱ | +301 at seal | {CLOCK_STEP} at seal | FAIL-SEAL |
| FB-11 | `run-z` attestation in state at 12:05:00Z | PASS | +seen(att); +accepted |
| FB-12 | `run-z` at 12:05:01Z | {CLOCK_ROLLBACK} | FAIL-A |
| FB-13 | `e10` and `h10` at 12:05:00Z (ST_G) | PASS | `seen_high` 10; max 12:05:00Z |
| FB-14 | Both at 12:05:01Z | {FUTURE_ENTRY, FUTURE_HEAD} | FAIL-G; max 12:05:01Z (interim floor rule) |
| FB-15 | `e10` at 12:05:01Z, `h10` at 12:05:00Z | {FUTURE_ENTRY, HEAD_BEFORE_ENTRY} | FAIL-G |
| FB-16 | `h10` at 12:05:01Z | {FUTURE_HEAD} | FAIL-G |
| FB-17 | `not_after` 13:07:00Z | PASS | PASS-G |
| FB-18 | 13:06:59Z | {APPROVAL_EXPIRED} | FAIL-G |
| FB-19 | `not_after` 13:07:00Z; run of 3600 s | PASS | PASS-G |
| FB-20 ✱ | Run of 3601 s | {RUN_EXCEEDS_EMAX, APPROVAL_EXPIRED} at seal (interim) | FAIL-SEAL |
| FB-21 | `not_after_head` 13:07:00Z | PASS | PASS-G |
| FB-22 | 13:06:59Z | {HEAD_EXPIRED} | FAIL-G |
| FB-23 | Issued 2025-12-31T13:07:00Z; `not_after_head` +W | PASS | PASS-G |
| FB-24 | Issued and `not_after_head` both one second earlier | {HEAD_STALE, HEAD_EXPIRED} | FAIL-G |
| FB-25 | Issued 2025-12-31T13:06:59Z; `not_after_head` 2026-01-01T13:07:00Z | {HEAD_STALE, HEAD_WINDOW_TOO_LONG} | FAIL-G |
| FB-26 | Template (window exactly W) | PASS | PASS-G |
| FB-27 | Window W + 1 | {HEAD_WINDOW_TOO_LONG} | FAIL-G |
| FB-28 | x = −240 | PASS | PASS-A |
| FB-29 | x = −241 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-30 | x = +360 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-31 | x = +361 | {CLOCK_SKEW} | FAIL-A |
| FB-32 | D = 600, x = +300 | PASS (T_hi 13:16:00Z / 12:25:00Z) | PASS-A |
| FB-33 | D = 601, x = +300 | {DELIVERY_AMBIGUOUS} | FAIL-A |
| FB-34 | Step of +2 at seal | PASS | PASS-A |
| FB-35 | Step of +3 at seal | {CLOCK_STEP} | FAIL-SEAL |
| FB-36 | Run of 3601 s, expiries far away | {RUN_EXCEEDS_EMAX} (interim) | FAIL-SEAL |
| FB-37 | Run of 3600 s, expiries far away | PASS | PASS-A |

### 6.2 Freshness and approvals (derivable unless marked)

**Positive fixtures:**
- FR-POS-1 → PASS.
- FR-POS-2: withdrawn (16-C).

**Attestation binding, namespaces and replay:**
- FR-NONCE-1 → {ATTESTATION_REPLAY}. Re-derived: x = 120 with D = 60 passes the skew rule.
- FR-NONCE-2 → {ATTESTATION_REPLAY}, +seen.
- FR-NONCE-3 → {UNTRUSTED_TIME, NONCANONICAL}, +seen. The attestation is the run's evidence instance.
- FR-NS-1 and FR-NS-2 → {UNTRUSTED_TIME, BAD_SIG} or {UNTRUSTED_TIME, PRINCIPAL}. Nothing recorded.

**Clock and expiry:**
- FR-CLK-2 → {CLOCK_SKEW}.
- FR-CLK-3 → {CLOCK_STEP}.
- FR-FUT-4: `run-b` → {FUTURE_ENTRY, FUTURE_HEAD, CLOCK_ROLLBACK} (interim floor rule); `run-c` → PASS.
- FR-EXP-1 → {APPROVAL_EXPIRED}.

**Newer heads (the FR-NEW chain, 16-C):**
- FR-NEW-1 → {HEAD_EXPIRED}.
- FR-NEW-2 → {ROLLBACK}.
- FR-NEW-3 → PASS.
- FR-NEW-4: superseded by FR-REC-1.

**Equivocation:**
- FR-EQ-1 and FR-EQ-1b → {EQUIVOCATION}.
- FR-EQ-2: deduplicated (identical instance: no new record).
- FR-EQ-3 → {EQUIVOCATION} found on import.

**Concurrency, interruption and import:**
- FR-CONC-1: only two outcomes are allowed — A then B both PASS, or B PASS then A ROLLBACK.
- FR-CONC-2 and FR-CONC-3 → STATE_UNAVAILABLE.
- FR-INT-1 to FR-INT-8: as in 16-B. FR-INT-4 is extended: a `validation` record is never re-applied from a pending file.
- FR-IMP-1 → ROLLBACK.
- FR-IMP-2: an unverifiable head is ignored.

**Record-point fixtures:**
- FR-REC-1: verified, non-canonical head 14 → +seen(head 14), whose `instance` record is flagged non-canonical; `seen_high` = 14 (interim floor rule); then `h10` → {ROLLBACK}.
- FR-REC-2: unverifiable → no seen record; then PASS.
- FR-REC-3 → BAD_SIG (wrong namespace).
- FR-REC-4 → SCHEMA.
- FR-REC-5 → SIGNERS_INVALID(FILE_HASH) for `allowed_signers.owner`. The file is exactly Line_owner(anchor), so BT-2 passes; the purpose AK's owner pin names a different hash (BT-5, step 6a). Revised in 16-H: under the exact grammar, the file's bytes cannot differ while still matching the anchor.
- FR-REC-7: as FB-14.
- **FR-REC-6 (state effect revised in 16-G; restated in 16-H)** (from ST_A, which holds `seen`(`h10`) and `instance`(`h10`'s canonical instance)):
  - b6 = `h10`'s content with reversed key order, re-signed with the owner key under `cse-checkpoint-approvals-head`.
  - Hashes: `raw_sha256`(b6) ≠ `raw_sha256`(`h10`), while `content_sha256`(b6) = `content_sha256`(`h10`).
  - Import → VERIFIED; same logical object as `h10`. State: +import; **+instance(b6, non-canonical)**. `seen`(`h10`) is unchanged. Floors unchanged.
  - Then presented as `run-a`'s head evidence instance → {NONCANONICAL}. State: +seen(att); `max_time_seen` = 12:00:00Z.
- **FR-REC-8:** b6 carrying `h10`'s original signature.
  - Import → UNVERIFIABLE(BAD_SIG). Its input identity κ = (head, `raw_sha256`(b6), `sig_sha256`(`h10`'s signature)) is computed; no `content_sha256` is recorded. (Revised in 16-I: 16-H said only `raw_sha256` was computed.) State: +import(κ, UNVERIFIABLE(BAD_SIG)).
  - Presented → {BAD_SIG, NOT_EVALUATED(every head-dependent rule)}. From 16-H, BT-3 to BT-7 are among the NOT_EVALUATED rules (step 6a), so this is not an abort.

**16-A fixtures:**
- FR-1 to FR-27: each mutation yields its own code plus the implied co-occurrences of §4.4. Their per-fixture inputs are in 16-A. These must be re-checked against §4 when archived.
- FR-B1 to FR-B4: superseded by FB.
- FR-SIG → NOT ACCEPTED.
- FR-TS → NONCANONICAL.
- FR-RE → effective.
- FR-DET → identical report bytes.

**Ledger and preconditions:** LED-1 to LED-16 and LED-R; PCV-1 to PCV-11. All retained from Revision 16.

### 6.3 FR-LATE (derivable; FORK and floors under the interim rules)

The setup is 16-D's: evidence arrives after P0 and is first imported at seal.

| ID | `p0-floor` | `issued-before` | `current-floor` | Interim | Interim state |
|---|---|---|---|---|---|
| FR-LATE-1 (consistent `h9`) | PASS | PASS | PASS | PASS | +seen(`h9`); +accepted |
| FR-LATE-2 (unverifiable) | PASS | PASS | PASS | PASS | +import only; +accepted |
| FR-LATE-3 (`h12` after `t_att`) | PASS | PASS | ROLLBACK | ROLLBACK + LATE_EVIDENCE → HELD | +seen(`e11`, `e12`, `h12`) |
| FR-LATE-4 (`h12` at 11:00:00Z) | PASS | ROLLBACK | ROLLBACK | Same + LATE_EVIDENCE → HELD | +seen(…) |
| FR-LATE-5 (`h10b`) | EQUIVOCATION | EQUIVOCATION | EQUIVOCATION | EQUIVOCATION → REJECTED | +equivocation |
| FR-LATE-6 (attestation at 12:05:01Z) | PASS | PASS | CLOCK_ROLLBACK | Same + LATE_EVIDENCE → HELD | +seen(att `run-q`) |
| FR-LATE-7 (`h9x`) | FORK | FORK | FORK | FORK → REJECTED | +seen(`h9x`); +fork. Under the interim FORK coverage; checking the highest head only would give PASS |
| FR-LATE-8 (review) | PASS | PASS | {ROLLBACK, CLOCK_ROLLBACK} | Same + LATE_EVIDENCE → SUSPENDED | +seen(…) |
| FR-LATE-9 (review) | PASS | ROLLBACK | ROLLBACK | Same + LATE_EVIDENCE → SUSPENDED | +seen(…) |
| FR-LATE-10 | SNAPSHOT_INVALID | SNAPSHOT_INVALID | SNAPSHOT_INVALID | SNAPSHOT_INVALID → INVALIDATED | None |

### 6.4 FR-HIST: previously accepted runs (derivable)

**Initial state H0:** after FR-LATE-1, so `run-a` is VALID and has an `accepted` record. Every review appends one `validation` record, and the `accepted` record is **always retained**.

| ID | Event | Result by option (`p0` / `issued-before` / `current` / interim) | Status | Eligibility |
|---|---|---|---|---|
| FR-HIST-1 | Review, no new evidence | PASS in all | VALID; +accepted | — |
| FR-HIST-2 | `h10b` imported | EQUIVOCATION in all | INVALIDATED | No |
| FR-HIST-3 | `h9x` imported | FORK in all (interim FORK coverage) | INVALIDATED | No |
| FR-HIST-4 | Sealed `floor_p0` altered | SNAPSHOT_INVALID in all | INVALIDATED | No |
| FR-HIST-5 | `h12` at 11:00:00Z | PASS / ROLLBACK / ROLLBACK / ROLLBACK + LATE_EVIDENCE | VALID / INVALIDATED / INVALIDATED / SUSPENDED | — |
| FR-HIST-6 | `h12` at 12:03:00Z | PASS / PASS / ROLLBACK / ROLLBACK + LATE_EVIDENCE | VALID / VALID / INVALIDATED / SUSPENDED | — |
| FR-HIST-7 | Attestation at 12:05:01Z | PASS / PASS / CLOCK_ROLLBACK / CLOCK_ROLLBACK + LATE_EVIDENCE | VALID / VALID / INVALIDATED / SUSPENDED | — |
| FR-HIST-8 | After FR-HIST-6 under the interim rule, PC-D-REVIEW is recorded as `issued-before` (a hypothetical decision used only for this fixture); then a review | PASS | SUSPENDED → VALID; +accepted | — |
| FR-HIST-9 | A later head (issued 12:30:00Z) revokes `syn-3` without re-approving it; a review follows; then use `u-1` (`t_use` 13:00:00Z) binds that head | Historical result per option, as in FR-HIST-6 | As FR-HIST-6 | **DEPENDENCY_REVOKED_NOW.** From 16-I, where the review leaves `run-a` INVALIDATED or SUSPENDED (`current-floor`, interim), ELIGIBILITY_NOT_ESTABLISHED(NOT_VALID) comes first (§3.1.5 group 11) |
| FR-HIST-10 | Use at `t_use` 2026-01-03T00:00:01Z, binding `h10` | Historical: VALID | — | **APPROVAL_EXPIRED_NOW** |
| FR-HIST-11 | Reviewer state corrupt | STATE_CORRUPT | Current UNKNOWN; no record written | No |
| FR-HIST-12 | A consumer presents only the `accepted` record | — | — | **ELIGIBILITY_NOT_ESTABLISHED(ACCEPTED_RECORD_ONLY)** |
| FR-HIST-13 | The same review run twice from the same state | Identical report bytes (Δ trivially equal; OI-3) | — | One `validation` record each |
| FR-HIST-14 (new in 16-I) | From FR-HIST-1, an owner entry `e11n` (seq 11, approved within `t_att` + S, so that no floor or time rule fires) is imported. Then a `use` validation `u-2`, binding the latest head | — | VALID | **No:** ELIGIBILITY_NOT_ESTABLISHED(REVIEW_NOT_CURRENT). The latest review's `inst_digest` lacks `e11n`'s instance, so it differs from `u-2`'s `currency_digest` (A1; 16-I as issued compared the digest after step 4, which gives the same result here) |
| FR-HIST-15 (new in 16-I) | After FR-HIST-14, a review of `run-a`, then a `use` validation with a fresh use `u-3` | PASS / PASS / CLOCK_ROLLBACK / CLOCK_ROLLBACK + LATE_EVIDENCE. `u-2`'s attestation (`t_use` later than `run-a`'s `t_att` + S) raised `max_time_seen` | VALID / VALID / INVALIDATED / SUSPENDED | `p0-floor` and `issued-before`: **ELIGIBLE**, because the review's `inst_digest` covers `e11n` and `u-2`'s attestation and equals `u-3`'s `currency_digest`, which leaves out only `u-3`'s own attestation (A1). `current-floor` and interim: **No**, ELIGIBILITY_NOT_ESTABLISHED(NOT_VALID). The digest is computed over sets (§3.2.2), so none of these results depends on the order in which the instances arrived |
| FR-HIST-16 (amendment A1) | From FR-HIST-1: `run-a` is VALID and its latest review is current. A `use` validation `u-4` binds a head `h11`, with its entry `e11`, issued after `run-a`'s `t_att`. No import has brought them into state; `u-4` presents them at step 6 | — | VALID | **No:** ELIGIBILITY_NOT_ESTABLISHED(REVIEW_NOT_CURRENT), because `currency_digest` contains `e11` and `h11`. Under 16-I as issued, the digest after step 4 lacked them and item 1 passed, so under `current-floor` or the interim `u-4` could be ELIGIBLE although a review that sees `h11` gives ROLLBACK. The same result is expected when `h11` was imported before `u-4` (early arrival), as OI-4 requires. After a new review: under `p0-floor` and `issued-before`, ELIGIBLE for a fresh use; under `current-floor` and the interim, the review gives ROLLBACK and `run-a` is not VALID |

### 6.5 Registry, seal and orchestration (derivable)

**Registry (RGV):**

| ID | Case | Expected |
|---|---|---|
| RGV-1 | Positive | PASS |
| RGV-2 | Missing gate | REGISTRY_MISSING |
| RGV-3 | Duplicate gate | REGISTRY_DUPLICATE |
| RGV-4 | Unknown ID | REGISTRY_UNKNOWN_ID |
| RGV-5 | `D-TRUST` used as an ID | REGISTRY_ALIAS_USED |
| RGV-6 | Driver PASS but recomputation fails | DRIVER_VERDICT_MISMATCH |
| RGV-7 | RGI hash not the approved one | STALE_RGI |
| RGV-8 | Stream missing | EVIDENCE_STREAM_MISSING |

**Seal and replay (SLV):**

| ID | Case | Expected |
|---|---|---|
| SLV-1 | Positive | PASS |
| SLV-2 | Bad seal signature | SEAL_INVALID |
| SLV-3 | Stream head differs | STREAM_HEAD_MISMATCH |
| SLV-4 | Chain break | STREAM_BROKEN |
| SLV-5 | R2 before R0 | STATE_REPLAY_INVALID |
| SLV-6 | A rejection record exists | RUN_REJECTED |
| SLV-7 | Raw evidence fails a gate | GATE_RECOMPUTE_FAIL |
| SLV-8 | An UNKNOWN is present | UNKNOWN_PRESENT |
| SLV-9 | Manifest hash differs | ARTIFACT_MISMATCH |

**Orchestration (ORC):**

| ID | Case | Expected |
|---|---|---|
| ORC-1 | Heartbeat lost | Hard stop → REJECTED (ABANDONED), recorded in both stores |
| ORC-2 | Driver killed mid-R2 | ABANDONED; no resume |
| ORC-3 | Second driver started | DRIVER_LOCKED |
| ORC-4 | Command replayed | Journaled result; no re-execution |
| ORC-5 | Same seq, different body | COMMAND_CONFLICT |
| ORC-6 | Primary store read-only | UNRECORDED_REJECTION + lockout |
| ORC-7 | Secondary store down | UNRECORDED_REJECTION + lockout |
| ORC-8 | Seal signing fails | No acceptance |
| ORC-8b | Rejection signing also fails | Unsigned marker + lockout; validators see REJECTION_UNSIGNED |
| ORC-9 | Disk full on the events log | Stop; reject |
| ORC-10 | The VM journal shows completion but production has no seal | Status is not VALID |
| ORC-11 | STOP during COPY | Stopped within T_stop, otherwise hard stop; tainted |

### 6.6 Sandbox and qcap

**Retained from 16-B to 16-F (expected outcomes unchanged):**
- **Positive and characterization:** SBX-P1; SBX-ENV, SBX-FD, SBX-FD0, SBX-STRUCT (BLOCKED-PIN); SBX-YAMA, SBX-ABS (BLOCKED-PIN); SBX-AUD (the positive control, which must pass first).
- **Pre-release refusals:** SBX-1 to SBX-24. Each must show: the stated SV or S-state code; psql never runs `exec`; zero `qcap` admissions; zero server connections. SBX-11's "N/A" path is valid only when backed by a source review.
- **After release:** SBX-25 and SBX-26.
- **SBX-27 (16-F):** a test-AK `ckpt-hold` holds one extra descriptor → SV-8 refuses before release.
- **Interruption:** SBX-I1 and SBX-I2.
- **State machine:** SM-1 to SM-4.
- **Earlier qcap fixtures:**
  - QID-1 to QID-7;
  - QB-1 to QB-13, with QB-8 BLOCKED-PIN on E117;
  - QB-1 to QB-5 are re-based onto the 16-B configuration (refusal before release).

**PROTO-1 to PROTO-16.** The refusal rules are derivable; PROTO-1 and PROTO-15 are BLOCKED-PIN.

| ID | Case | Expected |
|---|---|---|
| PROTO-1 | Characterize the startup parameters psql sends | Recorded, which establishes the allowlist |
| PROTO-2 | `user` ≠ postgres | QCAP_STARTUP_REJECTED |
| PROTO-3 | Wrong database | QCAP_STARTUP_REJECTED |
| PROTO-4 | Wrong `application_name` | QCAP_STARTUP_REJECTED |
| PROTO-5 | Extra `options` parameter | QCAP_STARTUP_REJECTED |
| PROTO-6 | SSLRequest or GSSENCRequest | QCAP_UNSUPPORTED |
| PROTO-7 | Parse message | QCAP_UNSUPPORTED → UNKNOWN |
| PROTO-8 | CancelRequest | QCAP_CANCEL_REFUSED → UNKNOWN |
| PROTO-9 | Mid-script error | FAILED, exit 3 |
| PROTO-10 | COPY flow | CopyData hash equals EB |
| PROTO-11 | CopyFail | FAILED |
| PROTO-12 | Client closes abruptly | STARTED_INTERRUPTED |
| PROTO-13 | Server sends FATAL | STARTED_INTERRUPTED |
| PROTO-14 | NotificationResponse or NegotiateProtocolVersion | QCAP_UNSUPPORTED |
| PROTO-15 | `BackendKeyData` pid equals the log pid | PASS; a mismatch is a DEFECT |
| PROTO-16 | A non-OK authentication request | QCAP_UNSUPPORTED |

### 6.7 Transformation, logs and completeness

**Retained:** FX-EB; ADV-1 to ADV-14, all BLOCKED-PIN on pinned psqlscan. Two are re-derived:
- **ADV-11** (`\gset`) → **UNSUPPORTED** (derivable);
- **ADV-12** (`\connect`) → segment boundary, PASS.

SPL-N1 to SPL-N3 are retained.

**ADV-15 to ADV-22:**

| ID | Case | Expected |
|---|---|---|
| ADV-15 | `\ir` | UNSUPPORTED |
| ADV-16 | `:var` interpolation | UNSUPPORTED |
| ADV-17 | `\if` | UNSUPPORTED |
| ADV-18 | `\set` | UNSUPPORTED |
| ADV-19 | Guard inside a `\restrict` window | BLOCKED-PIN |
| ADV-20 | A test client sends `SELECT 1; SELECT 1/0; SELECT 2;` as one Query | MULTI_STATEMENT_QUERY → UNKNOWN. The server's skip behavior is characterized (BLOCKED-PIN) |
| ADV-21 | Unterminated dollar quote at end of file | BLOCKED-PIN |
| ADV-22 | COPY terminator transmission | BLOCKED-PIN (UR-4) |

**Logs and completeness:**
- **Retained:** FX-JSON; FX-FAIL-1 to FX-FAIL-5; CMPL-1 to CMPL-10 (CMPL-4 → UNKNOWN).
- **LRC-1 to LRC-16.**
  - Shape fixtures, BLOCKED-PIN:

    | ID | Case |
    |---|---|
    | LRC-1 | Success |
    | LRC-2 | Syntax error |
    | LRC-3 | Analysis error |
    | LRC-4 | Execution error |
    | LRC-5 | WARNING |
    | LRC-6 | Connection and disconnection |
    | LRC-7 | COPY |
    | LRC-8 | Multiline statement |
    | LRC-15 | Statements inside a DO block |

  - Rule fixtures, derivable:

    | ID | Case | Expected |
    |---|---|---|
    | LRC-9 | Record removed | LOG_MISSING |
    | LRC-10 | Record duplicated | LOG_DUPLICATE |
    | LRC-11 | Malformed line | LOG_MALFORMED |
    | LRC-12 | `line_num` gap | LOG_GAP |
    | LRC-13 | Sessions interleaved | PASS |
    | LRC-14 | Unknown record shape | LOG_UNKNOWN_SHAPE |
    | LRC-16 | `SET log_statement` | UNKNOWN |

### 6.8 Configuration, checksums, guard, operations

**EXPCFG** is retained.

**Configuration (CFG):**

| ID | Case | Expected | Status |
|---|---|---|---|
| CFG-1 | Positive | PASS | |
| CFG-2 | Value set by `ALTER SYSTEM` (changes the `postgresql.auto.conf` hash) | CFG_BASELINE_MISMATCH | Also confirms the file-hash refusal used for E133 (§8.4) |
| CFG-3 | Value set on the command line | CFG_BASELINE_MISMATCH | |
| CFG-4 | File edited but not reloaded | CFG_PENDING | BLOCKED-PIN (E133) |
| CFG-5 | `pending_restart` | CFG_PENDING | |
| CFG-6 | File changing during the snapshot | CFG_UNSTABLE | |
| CFG-7 | Reload between LC-BASE and LC-END | CFG_CHANGED | |
| CFG-8 | `options=-c …` in the startup message | QCAP_STARTUP_REJECTED | |
| CFG-9 | Script `SET`s a member of G | CFG_SET_CONFLICT | |
| CFG-10 | Error row in `pg_file_settings` | CFG_BASELINE_MISMATCH | |

**Checksums (FX-DC retained, plus CKS).** All are BLOCKED-PIN on E140. Each runs against a fixture-declared X, and none implies a choice of X.

| ID | Case | Expected |
|---|---|---|
| CKS-1 | Checksums on, expectation on | PASS |
| CKS-2 | Checksums off, expectation off | PASS |
| CKS-3 | On versus expectation off | DC_MISMATCH |
| CKS-4 | `pg_controldata` output unparsable | DC_UNPARSABLE |
| CKS-5 | Injected corruption, with DC-D enabled | DC_CORRUPT |

**Guard (GRD):**

| ID | Case | Expected |
|---|---|---|
| GRD-1 | Positive | PASS |
| GRD-2 | Different cluster | GUARD_REFUSED_NO_EFFECT; `qcap` shows no further Query |
| GRD-3 | Load time changed | Refused |
| GRD-4 | Reload after the guard | CFG_CHANGED → tainted |
| GRD-5 | Restart | UNKNOWN |
| GRD-6 | Second connection | Refused → UNKNOWN |
| GRD-7 | Independent writer outside the sandbox | DEFECT, detected; shows that a lock prevents nothing |
| GRD-8 | Guard text altered | EB_MISMATCH |

**Operations (FX-RS retained, plus OPR):**

| ID | Case | Expected |
|---|---|---|
| OPR-1 | Positive | PASS |
| OPR-2 | TOC entry missing | OP_TOC_MISMATCH |
| OPR-3 | Duplicate statement | EB_MISMATCH |
| OPR-4 | Reordered statements | EB_MISMATCH |
| OPR-5 | Log record missing | LOG_MISSING |
| OPR-6 | Error mid-restore | FAILED → REJECTED |
| OPR-7 | Multi-statement Query | UNKNOWN |
| OPR-8 | Reconnect | UNKNOWN |
| OPR-9 | Zero-statement entry type outside the allowlist | OP_TOC_MISMATCH |

### 6.9 Sequences, schema and globals, provenance, script mode, end-to-end, source gate

**Sequences (FX-SEQ retained, plus SQV):**

| ID | Case | Expected |
|---|---|---|
| SQV-1 | Positive | PASS (BLOCKED-PIN on the setval form) |
| SQV-2 | Sequence without a setval | SEQ_UNMAPPED |
| SQV-3 | Two setvals for one sequence | SEQ_DUPLICATE |
| SQV-4 | `nextval` before capture | SEQ_MISMATCH |
| SQV-5 | `is_called` differs | SEQ_MISMATCH |
| SQV-6 | Different setval form | SEQ_UNSUPPORTED |
| SQV-7 | Identity column | SEQ_UNSUPPORTED |
| SQV-8 | Unlogged sequence | SEQ_UNSUPPORTED |
| SQV-9 | Never-read sequence | PASS via direct relation read (BLOCKED-PIN) |

**Schema and globals (FX-SCH and FX-GLB retained, plus SGR):**

| ID | Case | Expected |
|---|---|---|
| SGR-1 | Positive | PASS (BLOCKED-PIN) |
| SGR-2 | GRANT differs | SCH mismatch |
| SGR-3 | Role attribute differs | GLB mismatch |
| SGR-4 | Membership differs | GLB mismatch |
| SGR-5 | `ALTER ROLE … SET` differs | GLB mismatch |
| SGR-6 | Database setting differs | BLOCKED-PIN |
| SGR-7 | Extension version differs | SCH mismatch |
| SGR-8 | Version header lines differ | FAIL unless N1 is proven |
| SGR-9 | `\restrict` keys differ | FAIL unless N2 is proven or a fixed key is used |
| SGR-10 | Bootstrap role "already exists" during R1 | **UNRESOLVED (UR-3, design defect C-b)** |

**Provenance:**
- PROV-B1 to PROV-B9 and BIND-4: carried (UR-2).
- **PVR:**

| ID | Case | Expected |
|---|---|---|
| PVR-1 | Legacy manifest | PROV_ABSENT (all current dumps) |
| PVR-2 | Manifest and ledger identifiers differ | PROV_MISMATCH |
| PVR-3 | Hash differs | ARTIFACT_MISMATCH |
| PVR-4 | Positive | Blocked until P1-CC-PROV exists |

**Script mode:** FX-SM and SM-N1, carried.

**End-to-end and environment:** FX-E2E-1 to FX-E2E-14 and NET-1 to NET-5 carried (UR-2). PC-FX-CI and PC-FX-VM are bundles.

**Source gate** (record checks; nothing in the behavior register changes, and the gates stay closed):
- **Retained:** SRC-P1, SRC-N1 to SRC-N14 (16-C, 16-D).
- **16-F:**

| ID | Case | Expected |
|---|---|---|
| SRC-P2 | A class-F behavior (for example E126b) with INSUFFICIENT from both readers, with its confirming refusal fixture (SBX-27) recorded as FIXTURE-CONFIRMED | Source-review condition satisfied for that behavior |
| SRC-P3 | A class-M behavior (for example E127) with SUPPORTS from two independent blind readers | Source-review condition satisfied for that behavior |
| SRC-N15 | A class-M behavior (for example E126d) recorded as INSUFFICIENT | Rejected; AG-PINSRC stays closed |
| SRC-N16 | A class-F behavior recorded as INSUFFICIENT, but its confirming refusal fixture has not run | Not satisfied; AG-PINSRC stays closed |
| SRC-N17 | A behavior in neither list recorded as INSUFFICIENT | Treated as class M; rejected |
| SRC-N18 | A record marks a class-M behavior "fails closed" to claim the INSUFFICIENT exception (for example E122c citing SV-8) | Rejected: precedence rule 1 overrides; AG-PINSRC stays closed |

### 6.10 RKN: role, key and namespace fixtures (16-F; unexecuted; derivable)

**Setup:**
- Reviewer state is ST_A, or H0 (§6.4) for seal and rejection rows, each with a synthetic owner anchor and the BT-0 stub.
- Both signers files are valid under the §3.1.2 grammar and registered by PC-HR-KEYS at `h10`, with distinct keys and principals. The evidence file is the only member of V_E unless a row says otherwise.
- `run-b` is a second sealed run.

**State-effect classes:**
- **VERIFIED owner-class:** +seen, in the §6 notation (+seen for a new logical object, +instance for a new instance); floors may change under the interim rule.
- **VERIFIED evidence-class:** +seen, in the same notation, with `verified_by` set; **no floor change**; run status changes per §3.5.
- **UNVERIFIABLE:** +import only, noting the object; no seen record; floors unchanged.
- **Abort codes:** an abort at step 2 records nothing. An abort at step 6a writes no `validation` record, while instances recorded at steps 4 and 6 stay (§3.1.4; revised in 16-H).

| ID | Object and placement | Expected | State effect |
|---|---|---|---|
| RKN-1 | Entry; owner key; `cse-checkpoint-approvals`; entry position | VERIFIED | +seen(entry); floors per §3.2.2 |
| RKN-2 | Head; owner key; `cse-checkpoint-approvals-head`; head position | VERIFIED | +seen(head); `seen_high` per §3.2.2 |
| RKN-3 | Attestation; owner key; `cse-checkpoint-confirm`; `signer_principal` = owner | VERIFIED | +seen(att); `max_time_seen` per §3.2.2 |
| RKN-4 | Seal of `run-a`; evidence key; `cse-checkpoint-seal`; `signer_principal` = evidence; E in force at `run-a`'s head | VERIFIED; §3.13 proceeds | +seen(seal); no floor change |
| RKN-5 | Rejection of `run-b`; evidence key; `cse-checkpoint-rejection`; authenticated (§3.12A) | VERIFIED → RUN_REJECTED for `run-b` | +seen(rejection); `run-b` REJECTED or INVALIDATED; no floor change |
| RKN-6 | Head bytes signed by the **evidence** key under the head namespace; head position | UNVERIFIABLE(BAD_SIG) | +import (1 unverifiable); floors unchanged |
| RKN-7 | Seal bytes signed by the **owner** key under the seal namespace; seal position | UNVERIFIABLE(BAD_SIG) → SEAL_INVALID | No seen; `run-a` not accepted |
| RKN-8 | An entry signed by a key in neither file | UNVERIFIABLE(BAD_SIG) | +import only |
| RKN-9 | Attestation signed by the owner key, but `signer_principal` = the evidence principal | UNVERIFIABLE(PRINCIPAL) → {UNTRUSTED_TIME, PRINCIPAL, NOT_EVALUATED(…)} | +import only |
| RKN-10 | Seal signed by the evidence key, but `signer_principal` = the owner principal | UNVERIFIABLE(PRINCIPAL) → SEAL_INVALID | +import only |
| RKN-11 | The evidence key signs entry bytes under `cse-checkpoint-approvals`; entry position | UNVERIFIABLE(BAD_SIG): the owner file does not list the evidence key | +import only |
| RKN-12 | `allowed_signers.owner` also lists `cse-checkpoint-seal` | SIGNERS_INVALID(NAMESPACE_POLICY) at BT-2 | Abort at step 2; nothing recorded |
| RKN-13 | `allowed_signers.evidence` (the purpose AK's evidence file) also lists `cse-checkpoint-approvals-head` | SIGNERS_INVALID(NAMESPACE_POLICY) at BT-5 | Abort at step 6a. The file is also left out of V_E |
| RKN-14 | The same public key in both files | SIGNERS_INVALID(KEY_ROLE_OVERLAP) at BT-5 | Abort at step 6a. The evidence file is also left out of V_E |
| RKN-15 | The same principal string in both files | SIGNERS_INVALID(PRINCIPAL_ROLE_OVERLAP) at BT-5 | Abort at step 6a. The evidence file is also left out of V_E |
| RKN-16 | `allowed_signers.evidence` matches the AK, but E is **not** in force at `run-a`'s head | Seal VERIFIED, but EVIDENCE_SIGNERS_UNAUTHORIZED → NOT ACCEPTED | +seen(seal); `run-a` not accepted |
| RKN-17 | **Role substitution:** a VERIFIED head's (bytes, signature) placed as `run-a`'s attestation | Attestation UNVERIFIABLE(BAD_SIG) → {UNTRUSTED_TIME, BAD_SIG, NOT_EVALUATED(…)}; P0 refuses | +import (1 unverifiable); the head in its own position is unaffected |
| RKN-18 | **Role substitution:** `run-a`'s seal (bytes, signature) copied into `run-a`'s rejection directory | Rejection role UNVERIFIABLE(BAD_SIG) → REJECTION_UNVERIFIABLE | Acceptance blocked; current status UNKNOWN; history unchanged |
| RKN-19 | **Cross-role replay:** the signature of entry `e5` attached to head bytes | UNVERIFIABLE(BAD_SIG) | +import only |
| RKN-20 | **Same-role replay:** `run-b`'s VERIFIED seal presented as `run-a`'s seal | VERIFIED; SEAL_RUN_MISMATCH → NOT ACCEPTED | Seal already seen (identical instance: no new record); no floors |
| RKN-21 | **Same-role replay:** `run-b`'s VERIFIED rejection record found in `run-a`'s rejection directory | REJECTION_MISFILED for `run-a` | `run-a` current status UNKNOWN; acceptance blocked; no floors |
| RKN-22 | Two VERIFIED seals for `run-a` with different content | SEAL_CONFLICT | +seen ×2; +seal-conflict; `run-a` INVALIDATED (from VALID) or REJECTED (from PENDING) |
| RKN-23 | An unsigned rejection marker for `run-a` | REJECTION_UNSIGNED | Acceptance blocked; current status UNKNOWN |
| RKN-24 | Seal VERIFIED and authorized, but its `attestation_content_sha256` ≠ `run-a`'s attestation | SEAL_BINDING_MISMATCH → NOT ACCEPTED | +seen(seal); `run-a` not accepted |

**Where these fixtures belong:** RKN-1 to RKN-3, RKN-6, RKN-8, RKN-9, RKN-11, RKN-17 and RKN-19 count toward AG-FRESH. RKN-4, RKN-5, RKN-7, RKN-10, RKN-12 to RKN-16, RKN-18 and RKN-20 to RKN-24 count toward AG-SEAL.

**What they depend on:** RKN-11, RKN-17 and RKN-19 also serve as establishing fixtures for E127 (class M). They count as confirmation only under §8.3.

### 6.11 BOOT: trust bootstrap and state-integrity fixtures (16-G; revised and extended in 16-H; proposed, unexecuted)

**Setup:**
- Every row uses a **synthetic** anchor and synthetic keys. The rows test the order and logic of §3.1.4. They **cannot** establish the real anchor (UR-15) or a real integrity boundary (UR-16).
- **The BT-0 stub** is used unless a row says "no stub". It is a fixture-only BT-0 that reports success without checking anything. It exists only in fixture builds: a validator binary that contains it has a different hash, so it can never be the AK-pinned validator (§8.1).
- "Abort at step 2" means nothing is recorded. "Abort at step 6a" means no `validation` record is written, while instances recorded at steps 4 and 6 stay.
- Unless stated, `run-a` presents `h10`, which is the purpose head.

| ID | Setup | Expected | State effect |
|---|---|---|---|
| BOOT-1 (positive: initial trust) | `owner_anchor` = owner key K and principal. `allowed_signers.owner` = Line_owner(anchor). The ledger is signed by K, with PC-D-AKIT (subject = this AK's hash) and PC-HR-KEYS (subject = SHA-256(O), the evidence file's hash, and both principals) in force at `h10` | The pre-state checks and BT-2 pass at step 2; BT-3 to BT-6 pass at step 6a; the evidence file is registered at `h10` | None beyond the normal validation |
| BOOT-2 (missing anchor) | `genesis` has no `owner_anchor` | {TRUST_ANCHOR_MISSING} | Abort at step 2 |
| BOOT-3 (substituted owner key) | `allowed_signers.owner` = Line_owner(K′), with K′ ≠ anchor. Ledger, AK pins and PC-HR-KEYS are all rebuilt consistently around K′. `genesis` still holds K | {TRUST_ANCHOR_MISMATCH} at BT-2 | Abort at step 2 |
| BOOT-4 (circular authorization) | No anchor. The AK pins the owner file (K), and the K-signed ledger approves this AK (PC-D-AKIT) and the signers files (PC-HR-KEYS) | {TRUST_ANCHOR_MISSING}: a self-consistent loop is never a root | Abort at step 2 |
| BOOT-5 (substituted AK) | AK′, with a different hash and otherwise valid files. The ledger approves AK, not AK′. `run-a` presents AK′ | {AK_UNAPPROVED} at BT-4 | Abort at step 6a |
| BOOT-6 (AK pin mismatch; revised in 16-H) | The AK is approved. `allowed_signers.owner` = Line_owner(anchor). The AK's owner pin is the hash of a different file, for example Line_owner(anchor) with a trailing space | {SIGNERS_INVALID(FILE_HASH)} at BT-5 | Abort at step 6a |
| BOOT-7 (positive: evidence-key registration) | As BOOT-1, then RKN-4's seal | The seal is VERIFIED with `verified_by` = the evidence file; that file is in force at `h10`; the seal is authorized for `run-a` | +seen(seal) |
| BOOT-8 (evidence key unregistered; reasoning revised in 16-H) | As BOOT-1, but no PC-HR-KEYS approval names the evidence file. It is in V_E only through the approved AK's pin | Owner-class validation is unaffected. Evidence objects are VERIFIED, using the principal in the evidence file (VS-5, §3.1.3). Seals → EVIDENCE_SIGNERS_UNAUTHORIZED. Rejections → REJECTION_UNVERIFIABLE + EVIDENCE_SIGNERS_UNAUTHORIZED | Evidence objects +seen; none authorized; affected runs UNKNOWN or not accepted |
| BOOT-9 (evidence-key self-registration; placement fixed in 16-H) | A PC-HR-KEYS-shaped entry signed by the **evidence** key, placed as entry seq 11, beyond the purpose head `h10`, so it is not part of BT-3's chain | Import → UNVERIFIABLE(BAD_SIG): the evidence key is not in O. Registration absent, so as BOOT-8. (At a seq at or below the purpose head, BT-3 would fail instead, with group-2 codes) | +import only |
| BOOT-10 (registration contradicts anchor) | The PC-HR-KEYS approval in force at `h10` names an owner file whose hash ≠ SHA-256(O) | {KEYS_REGISTRATION_MISMATCH} at BT-6 | Abort at step 6a |
| BOOT-11 (anchor edited in place; re-scoped in 16-H) | The `owner_anchor` in `genesis` is altered in place, and the chain is **not** recomputed | {STATE_CORRUPT(CHAIN), TRUST_ANCHOR_MISSING} (pre-state rule). No stub: {STATE_CORRUPT(CHAIN), STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING}. **It detects only an edit that leaves the chain as it was;** see BOOT-13. (In 16-G, BT-2 would have read the edited anchor first and given TRUST_ANCHOR_MISMATCH) | Abort at step 2 |
| BOOT-12 (rebuild without the anchor) | `rebuild` is run without the anchor being entered from the channel | TRUST_ANCHOR_MISSING; rebuild refuses. A rebuild with the anchor would still leave BT-0 unable to pass (UR-16) | Nothing; the quarantine is unchanged |
| BOOT-13 (full consistent rewrite; new; defect demonstration) | An SA-2 writer replaces `genesis` with `owner_anchor` = K′, rewrites the state log with the chain recomputed, and replaces `allowed_signers.owner`, the AK and the ledger directory with K′-based equivalents | **With the stub:** the pre-state checks and BT-2 to BT-6 pass under K′. **Defect demonstrated (UR-16):** nothing in the design detects this. **No stub (production):** {STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING} | With the stub: a normal validation under K′. No stub: abort at step 2 |
| BOOT-14 (rollback; new; defect demonstration) | From a state that has seen `h12`, an SA-1 or SA-2 action puts back an older, consistent copy of reviewer state taken before `h12` was seen. `h12` is also removed from the stores. `run-x` then presents `h10` | **With the stub:** P0 PASS; ROLLBACK is not raised. **Defect demonstrated (UR-16).** No stub: as BOOT-15 | With the stub: +seen(att `run-x`); a PASS `validation` record. No stub: abort at step 2 |
| BOOT-14b (rollback, file still stored; new) | As BOOT-14, but `h12` is still in the primary store | **With the stub:** step 4 re-imports `h12`, so P0 → {ROLLBACK}. This is partial self-repair, **not detection** of the rollback: it depends on the stores still holding the file | +seen(`h12`); a FAIL `validation` record |
| BOOT-15 (production configuration; new) | No stub, because no approved boundary exists. `genesis` holds a valid anchor; everything else as BOOT-1 | {STATE_INTEGRITY_UNESTABLISHED, TRUST_ANCHOR_MISSING}. **This is today's outcome for every validation** | Abort at step 2; nothing recorded |
| BOOT-16 (owner file not in exact form; new) | `allowed_signers.owner` = Line_owner(anchor) with one trailing space before the LF | {SIGNERS_INVALID(LINE_POLICY)} at BT-2 | Abort at step 2 |
| BOOT-17 (pattern principal; new) | `allowed_signers.owner` has the anchor's key, but its principal is `*` | {SIGNERS_INVALID(LINE_POLICY)} at BT-2: the principal is outside the permitted character set | Abort at step 2 |

### 6.12 LIFE: evidence-key authorization lifecycle fixtures (16-G; extended in 16-H; proposed, unexecuted; derivable)

**Setup:**
- Synthetic ledger. `run-a` has h_R = 10 and is sealed with E1 unless stated. Every later head is issued after `run-a`'s `t_att`.
- **From 16-H:** every evidence file named here is in the kit store and in V_E (§3.1.2A). The AK changes with the evidence file: A1 pins E1, A2 pins E2. `run-a`'s AK is A1.
- **"Historical"** gives the status under `p0-floor`, with the interim in brackets where a later head exists. For `issued-before` and `current-floor`, see FR-HIST-6.
- **"Eligibility"** assumes a fresh use attestation binding h_U, a current seal or review (§3.6 item 1), everything else in §3.6 satisfied, and the AK current at h_U. The codes are given under `p0-floor`. From 16-I, a run that is SUSPENDED under the interim also gets ELIGIBILITY_NOT_ESTABLISHED(NOT_VALID) first.

| ID | Ledger events (seq) | Object or event | Historical | Eligibility at h_U | Codes |
|---|---|---|---|---|---|
| LIFE-1 (positive) | a_E1 at 3; nothing later | seal(E1) | VALID | h_U = 10: ELIGIBLE | — |
| LIFE-2 (boundary: s_a = h_R) | a_E1 at 10 | seal(E1) | VALID | h_U = 10: ELIGIBLE | — |
| LIFE-3 (boundary: s_a = h_R + 1) | a_E1 at 11 | seal(E1) | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-4 (boundary: s_r = h_R) | a_E1 at 3; revoked at 10 | seal(E1) | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-5 (boundary: s_r = h_R + 1; revocation after sealing) | a_E1 at 3; revoked at 11 | seal(E1) | VALID [interim: SUSPENDED, LATE_EVIDENCE from `h11`] | h_U = 11: **No** | DEPENDENCY_REVOKED_NOW |
| LIFE-6 (rotation after sealing; codes revised in 16-H) | a_E1 at 3; revoked at 11; a_E2 at 12 (with A2) | seal(E1) | VALID [SUSPENDED] | h_U = 12, with A2: **No** (interim PC-D-EKEY policy) | DEPENDENCY_REVOKED_NOW, **STALE_AK** (§3.6 item 5) |
| LIFE-7 (seal by the successor key) | The rotation at 11 and 12 is recorded between P0 and seal; the driver signs the seal with E2 | seal(E2), VERIFIED with `verified_by` = E2 | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED, plus ROLLBACK under `current-floor` or the interim |
| LIFE-8 (rejection before revocation is observed) | a_E1 at 3; h_latest = 10 when the rejection is classified | rejection(E1) for `run-a` | INVALIDATED (terminal) | No | RUN_REJECTED |
| LIFE-9 (rejection after revocation is observed) | a_E1 at 3; revocation at 11 observed first; the rejection is first classified afterwards | rejection(E1) for `run-a` | Current UNKNOWN (not terminal) | No | REJECTION_KEY_REVOKED |
| LIFE-10 (rejection by the successor key) | Rotation at 11 and 12; E2 in force at h_latest = 12 | rejection(E2) for `run-a` | INVALIDATED | No | RUN_REJECTED |
| LIFE-11 (rejection by a key revoked before the run) | E0 approved at 2, revoked at 5 | rejection(E0) for `run-a`, VERIFIED through E0 ∈ V_E | Current UNKNOWN | No | REJECTION_UNVERIFIABLE, EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-12 (observation-order property) | LIFE-8's rejection and LIFE-9's revocation, imported in both orders | — | INVALIDATED in one order, UNKNOWN in the other. **Never VALID or ELIGIBLE in either** | No | As LIFE-8 or LIFE-9 |
| LIFE-13 (old acceptance record only) | LIFE-6's state; a consumer presents only `run-a`'s `accepted` record | — | — | **No** | ELIGIBILITY_NOT_ESTABLISHED(ACCEPTED_RECORD_ONLY) |
| LIFE-14 (run with no head) | `run-z` failed at PRECHECK; E1 in force at h_latest | rejection(E1) for `run-z` | REJECTED | No | RUN_REJECTED (classified at h_latest only) |
| LIFE-15 (review after rotation; new in 16-H) | As LIFE-6. The current AK is A2. `run-a`'s AK A1 and both evidence files are in the kit store | Review of `run-a`, with purpose AK A1 | VALID [SUSPENDED]: the seal is VERIFIED with `verified_by` = E1 and authorized at h_R = 10. No abort at BT-5 (16-G's BT-5 compared A1 with the current files and aborted) | h_U = 12, with A2: No | DEPENDENCY_REVOKED_NOW, STALE_AK |
| LIFE-16 (rejection by a rotated-out key; new in 16-H) | a_E1 at 3; revoked at 11; a_E2 at 12; the revocation is observed first | rejection(E1) for `run-a`, first classified after the rotation | Current UNKNOWN (not terminal). The rejection is VERIFIED through E1 ∈ V_E, so the code is REJECTION_KEY_REVOKED, not REJECTION_UNVERIFIABLE | No | REJECTION_KEY_REVOKED |
| LIFE-17 (kit-store file missing; new in 16-H) | As LIFE-15, but E1's file has been removed from the kit store | Any validation | Abort at step 3: a persisted instance with `verified_by` = E1 can no longer be re-verified | — | STATE_CORRUPT |
| LIFE-18 (evidence key reused; new in 16-H) | A second evidence file E3, with a different principal but E1's public key, is registered at 13 | Any validation after it is imported | Abort: at step 4 when first imported, and at step 3 afterwards. Recovery is an owner action under PC-D-OKEY (c), as for other erroneous owner signatures | — | SIGNERS_INVALID(EVIDENCE_KEY_REUSE) |

### 6.13 CAN: canonicality, deduplication and order-independence fixtures (16-G; revised and extended in 16-H and 16-I; proposed, unexecuted)

**Setup:**
- **ST_CAN** (new in 16-H) is ST_G (16-C), re-expressed under §3.2.1, with every instance of the logical object λ(`h10`) and its `seen` record removed, and the floors recomputed.
  - If ST_G holds no instance of `h10`, ST_CAN = ST_G.
  - ST_G was not re-read for 16-H. This definition makes the CAN expectations independent of whether ST_G holds `h10`.
  - ST_CAN holds a synthetic anchor, and the BT-0 stub is used.
- `c10` is the canonical instance of `h10` (the original bytes and signature).
- `b6` is FR-REC-6's re-signed, non-canonical instance of the same logical object.
- **"Import X"** means an import-only invocation (§3.3) whose stores hold X as their only new file.
- **The compared projection** for the order fixtures is Proj(S) (§3.2.4).

| ID | Inputs, in order | Expected | State effect |
|---|---|---|---|
| CAN-1 (canonical first; revised in 16-H) | From ST_CAN: import `c10`; import `b6`; then `run-a`'s P0, presenting `c10` | PASS (with the other template inputs) | +seen(λ(`h10`)), with c = `c10`'s bytes; +instance(`c10`, canonical); +instance(`b6`, non-canonical); then `run-a`'s records |
| CAN-2 (non-canonical first; revised in 16-H) | From ST_CAN: import `b6`; import `c10`; then the same P0 | PASS | **The same three records as CAN-1, byte for byte,** written in a different order, so Proj equals CAN-1's. In both, `seen` holds c = C(parse(`b6`)), which is `c10`'s bytes |
| CAN-3 (no cure from elsewhere) | As CAN-1, but `run-a` presents `b6` as its head evidence instance | {NONCANONICAL} | As CAN-1, plus the run's records |
| CAN-4 (no taint from elsewhere; revised in 16-H, because 16-G's CAN-4 repeated CAN-2) | From ST_CAN: import `b6` only; then `run-a` presents `c10` | PASS | +seen(λ(`h10`)) and +instance(`b6`) at import; +instance(`c10`) at step 6 |
| CAN-5 (repeated identical raw bytes; restated in 16-I) | Three files with `c10`'s bytes and signature, at different paths, in one or more imports | One input identity, κ = ι(`c10`), with one result, VERIFIED. No record after the first instance; any later decision unchanged | +seen(λ(`h10`)) once; +instance(`c10`) once; one `import` note per source reference (three) |
| CAN-6 (duplicate evidence position) | `run-a` passed P0 presenting `c10`. Its sealed `evidence_manifest` lists both `c10` and `b6` for the head position | {EVIDENCE_LAYOUT_INVALID(DUPLICATE_POSITION)} at seal; P0_MISMATCH is not evaluated for that position | Seal-validation records |
| CAN-7 (two valid signatures over identical bytes) | `c10`'s bytes with a second, different, valid owner signature | +instance (signature variant); no conflict; no decision affected. **BLOCKED-PIN on E143** | +instance |
| CAN-8 (one valid and one invalid signature over identical bytes; made exact in 16-H; identities added in 16-I) | From ST_CAN, in one import: `c10` with its valid signature, and the same bytes with a corrupted signature | Two input identities that differ only in `sig_sha256`: κ1 = ι(`c10`) → VERIFIED; κ2 → UNVERIFIABLE(BAD_SIG). No conflict. Res holds both. (16-H's per-`raw_sha256` key would have given one key two results) | +seen(λ(`h10`)); +instance(`c10`); +import(κ1, VERIFIED); +import(κ2, UNVERIFIABLE(BAD_SIG)) |
| CAN-9 (reordering property; revised in 16-H and 16-I) | From ST_CAN: four files, each in its own import-only invocation, in all 24 orders: `c10`; `b6`; `h15u` (head-15 bytes signed by a key not in O); `h12` (FR-LATE-3's head 12, issued after `t_att`). Then `run-a`'s P0 presenting `c10` | **OI-1:** in all 24 orders, equal Proj after the last invocation, including Res = {ι(`c10`): VERIFIED; ι(`b6`): VERIFIED; ι(`h12`): VERIFIED; κ(`h15u`): UNVERIFIABLE(BAD_SIG)}. **OI-3:** the 24 P0 validations have equal Δ (equal Proj; consistent states; the same `genesis`, build and owner file; H_R empty; the same A, stores, presented inputs, clocks and option), so each gives the code list {ROLLBACK}, since `seen_high` = 12 > 10 (as FR-LATE-3 under `current-floor`, which gives ROLLBACK alone), and byte-identical reports. Record order, record numbers and prefix hashes may differ | +seen(λ(`h10`)), +instance(`c10`), +instance(`b6`); +seen(`h12`); an `import` note for `h15u`; `run-a`'s FAIL records |
| CAN-10 (derived-record recomputation; new in 16-H) | After CAN-1, two separate cases, each with the chain recomputed: (a) the stored `seen`(λ(`h10`)).c is altered; (b) `instance`(`b6`).`canonical` is flipped to true | {STATE_CORRUPT} at step 3 in both: the stored derived record differs from its recomputation (§3.2.1 rule 2). **This shows only that stored records must agree with each other.** Removing `b6`'s `instance` record entirely, with the chain recomputed, is not detected (UR-16; BOOT-14) | Abort at step 3 |
| CAN-11 (evidence-class reordering; new in 16-H) | From H0 plus `e11` (a neutral entry at seq 11): four files, each in its own import, in all 24 orders: `r2` (a rejection of `run-a` signed by E2); F_E2 (E2's signers file, in the kit directory); `e12` (a PC-HR-KEYS approval naming E2); `h12` (the head over `e12`). Then a review of `run-a` | In all 24 orders: `r2` ends VERIFIED with `verified_by` = E2, including orders where it arrived before `e12` or F_E2 (it is re-tried, §3.1.2A); equal Proj; the same review codes, {ROLLBACK flagged LATE_EVIDENCE (interim), RUN_REJECTED}, since E2 is in force at h_latest = 12; status INVALIDATED. Res ends with κ(`r2`): VERIFIED and κ(F_E2): STORED in every order, although in orders where `r2` or F_E2 arrives before `e12` the intermediate results are UNVERIFIABLE(BAD_SIG) and UNNAMED (OI-1, OI-2) | +seen(`e12`), +seen(`h12`), +kit(F_E2), +seen(`r2`) |
| CAN-12 (late-arrival property; new in 16-H) | From ST_CAN plus `c10`: `run-a` has a P0, a seal and a final review. Three files — `h12`; `h11r` (head 11, whose entry `e11` revokes a_E1, delivered with `e11`); `r1` (an authenticated rejection of `run-a` by E1) — are each placed in one of four slots: never delivered, before P0, between P0 and seal, or between seal and review. All 64 placements, under the interim rules | Property (OI-4): after the final review, `run-a` is VALID, and ELIGIBLE for a use binding the latest head, **only** in the placement where no file is delivered. Each placement's status is derivable from §3.4, §3.5 and §3.12A, and must be pre-registered (§8.3 item 2) before execution. This note states only the property | Per placement |
| CAN-13 (manifest differs from P0; new in 16-H) | `run-a` passed P0 presenting `c10`. Its sealed manifest lists `b6` for the head position, and `b6` is stored in the run's evidence directory | {NONCANONICAL, EVIDENCE_LAYOUT_INVALID(P0_MISMATCH)} at seal → REJECTED | Seal-validation records |
| CAN-14 (evidence instance unavailable; new in 16-H) | The manifest lists `c10` for the head position, but `run-a`'s evidence directory lacks `c10`'s bytes, or holds bytes with a different hash. Reviewer state does hold `instance`(`c10`) | {EVIDENCE_LAYOUT_INVALID(INSTANCE_UNAVAILABLE), NOT_EVALUATED(rules on the head position)}. The reviewer-state copy is not used | Seal-validation records |
| CAN-15 (same bytes and signature in two roles; new in 16-I) | From ST_CAN, in both orders: import `c10` at a head position, and a copy of `c10`'s bytes and signature placed as `run-q`'s `attestation.json` with its signature file | Two input identities that differ only in role. (head, …) → VERIFIED. (attestation, …) → UNVERIFIABLE(BAD_SIG), because the head namespace is not the attestation namespace (E127). The attestation-role failure does not affect the head instance. Equal Proj in both orders | +seen(λ(`h10`)), +instance(`c10`); an `import` note for each identity |
| CAN-16 (one identity in both rejection stores; new in 16-I) | From H0: `r1`, an authenticated rejection of `run-b`, is written to both the primary and the secondary rejection store, as §3.12 requires, and imported in either order | One input identity, one instance, one result (VERIFIED); two `import` notes, one per store; equal Proj in both orders | +seen(`r1`) once |
| CAN-17 (identities with ⊥ parts; new in 16-I) | From ST_CAN, in all 6 orders: (a) `c10`'s bytes, with its signature file, at a position that maps to no role (for example `stray.json` in the ledger directory); (b) `c10`'s bytes at a head position with no signature file; (c) `c10` itself | (a) κ = (⊥, raw, sig) → UNVERIFIABLE(ROLE). (b) κ = (head, raw, ⊥) → UNVERIFIABLE(SIG_FORMAT). (c) κ = ι(`c10`) → VERIFIED. Three distinct identities with the same raw bytes; neither failure affects (c). Equal Proj in all 6 orders | `import` notes for (a) and (b); +seen(λ(`h10`)), +instance(`c10`) |
| CAN-18 (Δ5 is needed; new in 16-I) | Two states S1 and S2, built directly for the fixture, with equal Proj; both hold `h12`, from `run-q`. In S1 `run-q` has an `accepted` record; in S2 it has none. `run-a`'s P0 with identical presented inputs, under a hypothetical "accepted objects only" decision for PC-D-FRESH (b), used only for this fixture as FR-HIST-8 uses one | S1: {ROLLBACK}, because `h12` counts. S2: PASS, because it does not. So equal Proj is not enough, and Δ5 is needed. Under the interim rule both give {ROLLBACK} | Per state |
| CAN-19 (Δ4 is needed; new in 16-I) | Two states S1 and S2, built directly for the fixture, with equal Proj and equal A. `run-a`'s P0 record holds AK hash A1 in S1 and A1′ in S2; both AKs are approved at `h10` and in the kit store. The seal's `ak_sha256` is A1. A seal validation with identical presented inputs | S1: PASS. S2: {SEAL_BINDING_MISMATCH} (§3.13 step 3), with A1′ as the purpose AK. So equal Proj is not enough, and Δ4 is needed | Per state |
| CAN-20 (kit files to a fixpoint; new in 16-I) | From ST_CAN: four files, each in its own import-only invocation, in all 24 orders: `e11a` (a PC-D-AKIT approval of AK A3); A3 (in the kit directory); F3 (an evidence signers file named only by A3's pin, in the kit directory); `r3` (a rejection of `run-b` signed by F3's key) | In every order, after the last invocation: A3 and F3 STORED, F3 ∈ V_E, `r3` VERIFIED with `verified_by` = F3, and equal Proj. Where F3 or `r3` arrives before `e11a` or A3, its intermediate result is UNNAMED or UNVERIFIABLE (OI-2). When `e11a` arrives last, the last step must store A3 and then F3, which needs the repeated kit examination | +seen(`e11a`); +kit(A3); +kit(F3); +seen(`r3`) |

### 6.14 Fixtures for the rules changed in 16-H and 16-I (all proposed, unexecuted)

**Rules changed in 16-H:**

| Changed rule | Fixtures that exercise it | New or revised in 16-H |
|---|---|---|
| §3.2.1: content-only `seen`; every instance recorded; round-trip check; derived records recomputed at load | CAN-1, CAN-2, CAN-4, CAN-5, CAN-8, CAN-10; FR-REC-1, FR-REC-6 | CAN-10 new; the others revised |
| §3.2.2: aggregation, rep(λ), carriers, flags as pairs, fixed import order | CAN-9, CAN-11; FR-LATE-10 (the sealed `floor_p0` representatives) | CAN-11 new; CAN-9 revised |
| §3.2.3: exact evidence instances, `evidence_instances`, the four layout sub-codes | CAN-3, CAN-6, CAN-13, CAN-14 | CAN-13 and CAN-14 new |
| §3.2.4: OI-1 to OI-4 | CAN-9, CAN-11, CAN-12, LIFE-12 | CAN-12 new |
| §3.1.2: exact grammar, principal character set | BOOT-6, BOOT-16, BOOT-17, RKN-12, RKN-13 | BOOT-16 and BOOT-17 new; BOOT-6 revised |
| §3.1.2A: purpose AK, kit store, V_E, `verified_by`, re-try, EVIDENCE_KEY_REUSE | BOOT-8, LIFE-6, LIFE-15 to LIFE-18, CAN-11 | LIFE-15 to LIFE-18 new |
| §3.1.3: principal taken from the signers file used | BOOT-8, RKN-9, RKN-10 | BOOT-8 reasoning revised |
| §3.3: step order (pre-state checks, R-PEND after them, BT-3 to BT-7 at step 6a); import-only invocation | BOOT-1, BOOT-5, BOOT-6, BOOT-9, BOOT-10, BOOT-11, FR-REC-8; CAN-9, CAN-11 | BOOT-9 placement fixed; BOOT-11 re-scoped |
| §3.1.4 BT-0, BT-1 and §3.7A (UR-16) | BOOT-11, BOOT-13, BOOT-14, BOOT-14b, BOOT-15 | BOOT-13 to BOOT-15 and BOOT-14b new |
| §3.6 item 5 and PC-D-EKEY (b) | LIFE-6, LIFE-15 | LIFE-6 codes revised |

None of these has been implemented, run or reviewed. BOOT-13, BOOT-14 and BOOT-14b document a defect, and must be re-registered as detection fixtures when UR-16 is corrected (SI-R6).

**Rules changed in 16-I:**

| Changed rule | Fixtures that exercise it | New or revised in 16-I |
|---|---|---|
| §3.2.1: input identity κ; role-only invariant; idempotence and processing again by κ; r_S(κ) | CAN-5, CAN-8, CAN-15, CAN-16, CAN-17; FR-REC-8; RKN-17 | CAN-15 to CAN-17 new; CAN-5, CAN-8 and FR-REC-8 revised |
| §3.2.2: Res(S), `inst_digest`, fixed import order with kit files to a fixpoint | CAN-9, CAN-11, CAN-20; FR-HIST-14, FR-HIST-15 | CAN-20, FR-HIST-14 and FR-HIST-15 new; CAN-9 and CAN-11 revised |
| §3.2.4: OI-1 and OI-2 over Proj with Res; reordering defined with the starting kit store fixed | CAN-9, CAN-11, CAN-15, CAN-16, CAN-17, CAN-20 | As above |
| §3.2.4: OI-3 over Δ; the report's content | CAN-9 (positive), CAN-18 (Δ5 needed), CAN-19 (Δ4 needed), FR-HIST-13 (positive), BOOT-3 (Δ3 matters) | CAN-18 and CAN-19 new |
| §3.1.2A and §3.3 step 4: every input without a VERIFIED instance processed again; kit files to a fixpoint | CAN-9, CAN-11, CAN-20 | CAN-20 new |
| §3.3 step 6: `import` notes for presented inputs | RKN-9, RKN-10, RKN-17 | Expectations unchanged; the rule now produces them |
| §3.1.5 group 11; §3.3 step 8 kind `use`; §3.6 item 1 currency; ELIGIBILITY_NOT_ESTABLISHED sub-codes | FR-HIST-9, FR-HIST-12, FR-HIST-14, FR-HIST-15; LIFE-13 | FR-HIST-14 and FR-HIST-15 new; the others' codes revised |
| Amendment A1: `currency_digest` (§3.2.2, §3.6 item 1) | FR-HIST-14, FR-HIST-15, FR-HIST-16 | FR-HIST-16 new; FR-HIST-14 and FR-HIST-15 reworded, expectations unchanged |

**None of the 16-I fixtures has been implemented, run or reviewed.** Their expectations are derived from this note's rules only. CAN-18 and CAN-19 start from states built directly for the fixture, and CAN-18 uses a hypothetical owner decision that has not been made.

---

## 7. Owner decisions, assumptions, unresolved items

### 7.1 Decisions

**No owner decision has been made.** Each "interim" below is a fail-closed default applied until the owner decides. It is not a choice and does not imply approval.

| ID | Exact decision needed | Interim until decided |
|---|---|---|
| **PC-D-OKEY** (extended) | (a) The owner key, principal and key type. (b) **The trust-anchor channel and record** (§3.1.4): how each validating party independently receives the owner public-key line and principal, how it is recorded (16-G proposed `genesis`; its adequacy depends on UR-16), and how it is replaced on rotation (UR-15). (c) Recovery from integrity flags and erroneous owner signatures, including EVIDENCE_KEY_REUSE (key rotation through the anchor channel, plus explicit rebuild) | (b) **None. No channel is chosen or approved. Every validation aborts with TRUST_ANCHOR_MISSING** (with STATE_INTEGRITY_UNESTABLISHED while UR-16 is open). (c) Integrity flags and EVIDENCE_KEY_REUSE block indefinitely |
| **PC-D-EKEY** (extended) | (a) Custody of the evidence key; the evidence principal and key type; the rotation procedure. (b) **Whether a routine evidence-key rotation preserves the current eligibility of earlier runs** (§3.12A). **Dependency (16-H):** choosing "preserve" also requires revising §3.6 item 5 (same AK) and the AK's pin of the evidence file, because every rotation changes the AK | (a) No evidence-class object can be authorized. (b) It does not: DEPENDENCY_REVOKED_NOW, and STALE_AK in any case |
| **PC-D-TRUST** (extended in 16-H) | (b) **Which adversary classes the reviewer-state integrity boundary must resist** (SA-0 to SA-3, §3.7A). (c) **Who holds that boundary's evidence**, within SI-R1. The mechanism itself is a design correction for UR-16 that this note does not choose | (b) SA-0 to SA-2 are in scope (fail-closed). (c) None: BT-0 cannot pass |
| PC-HR-KEYS | Register both signers files (their hashes and the two principals) through an owner-signed ledger entry | No seal can pass §3.13 step 2 |
| **PC-D-REVIEW** | Choose `p0-floor`, `issued-before` or `current-floor` (§3.4) | Pass against both floors; LATE_EVIDENCE leads to HELD or SUSPENDED. **Consequence (16-I):** with §3.6 item 1, a run is eligible for at most one use while this interim, or `current-floor`, applies (FR-HIST-15) |
| **PC-D-SKEW** | Set S and J, and resolve C-a (Δ_att > 2S): lower Δ_att to ≤ 2S, raise S, or accept DELIVERY_AMBIGUOUS (not recommended) | DELIVERY_AMBIGUOUS rejects. The fixture parameters are not values |
| **PC-D-EMAX** | Whether exceeding E_max is a rejection in its own right; the value of E_max | It is a rejection |
| **PC-D-BASE** | Distribution, dated archive snapshot, and base image for the AK image and the VM | None. AG-PINSRC is blocked |
| **PC-D-INDEP** | Name the second source readers, hash recomputers, fixture reviewers and RTM reproduction operators | None |
| **PC-D-FRESH** (extended) | (a) S, W, Δ_att, Δ_start, the location of reviewer state, and the reviewer platform (local Linux). (b) **Whether verified-but-rejected owner-class objects raise floors** (§3.2.2). (c) **FORK coverage:** every seen head and entry, or the highest head only (§3.2.2) | (a) none (fixture parameters only); (b) they raise floors; (c) every seen head and entry |
| PC-D-RGI (extended) | The RTM collision decisions 1–8 from 16-C §4; the CT; EXAMPLE spans | AG-RTM fails |
| PC-D-QCAP (extended) | Sandbox mechanism; protocol allowlist once PROTO-1 has run | Empty allowlist beyond the three keys |
| PC-D-LEASE (extended) | Lease interval, T_stop, E_max | — |
| PC-D-PGCHK (extended) | Whether DC-D is used; **the data_checksums expectation X** (§3.17) | No X declared; the DC gate cannot pass |
| PC-D-SEC, PC-D-PGVER, PC-D-VM, PC-D-DOCS, PC-D-HV | As in Revision 16 | — |

All other existing PC-D-* decisions are unchanged and unapproved.

### 7.2 Assumptions

- **E1 to E130:** carried.
- **Added in 16-E, all [E?] and unverified unless noted:**

| ID | Assumption |
|---|---|
| E131 | The owner cannot sign before m0, because the nonce is unpredictable. This is cryptographic, not a timing claim |
| E132 | Clock-rate drift is negligible over a run |
| E133 | `pg_file_settings` re-reads the configuration files at query time and shows unapplied values |
| E134 | `log_statement` omits syntax-error statements; `log_min_error_statement` records their text |
| E135 | In a multi-statement simple Query, an error skips the remaining statements |
| E136 | `psql -f` sends one statement per Query |
| E137 | `pg_dump` emits setval with `is_called` for each sequence |
| E138 | `pg_restore` renders deterministically, given a fixed `--restrict-key` and fixed versions |
| E139 | How `pg_dumpall` treats the bootstrap role on restore. **UNRESOLVED** |
| E140 | `pg_controldata` reports the data page checksum version |
| E141 | CancelRequest arrives on a separate connection |
| E142 | libpq does not attempt SSL or GSS encryption over Unix sockets |

- **Added in 16-G:**

| ID | Assumption |
|---|---|
| E143 | [E?] The pinned `ssh-keygen` can produce two different valid signatures over identical bytes, for example through a different hash-algorithm option. **No gate depends on it.** It only determines whether CAN-7 can be constructed. If it is false, CAN-7 is recorded as not constructible, backed by a source review |

- **Added in 16-H:** no new assumption. Two points are placed inside existing class-M behavior E127 (§8.4): that a principal made only of `[A-Za-z0-9._@+-]` matches only itself in `-Y verify`, and that an SSHSIG signature carries its signer's public key, which `-Y verify` checks against the file's key.

### 7.3 Unresolved items

**Types:**

| Type | Meaning |
|---|---|
| Design defect | The design is incomplete or wrong, and blocks |
| Design gap | Something is not yet specified |
| Unknown external behavior | Needs a pinned source review or a fixture |
| Missing evidence | Needs the archive or a record |
| Unknown fact | Repository or production, not yet checked |
| Unverifiable assumption | Cannot be fully established |

| ID | Type | Item |
|---|---|---|
| UR-1 | Missing evidence | Mapping of gates P1 to P5 onto §3.8's states (needs the archive) |
| UR-2 | Missing evidence | Meanings of items carried by name (marked †, FX-E2E, NET, PROV-B, BIND-4, SM-N1) |
| UR-3 | **Design defect (C-b)** | **Globals restore:** pg_dumpall's bootstrap role versus ON_ERROR_STOP. Direct mode tolerates the error ([R] `restore_check.py:75`); script mode has no defined handling. **A2:** the error is the `CREATE ROLE` of the bootstrap superuser, because the restore cluster's own bootstrap superuser has the same name ([R] `restore_check.py:71`); in CI it was the only error (`docs/R_F4_IMPLEMENTATION.md` §24.8) |
| UR-4 | Unknown external behavior | Whether psql transmits the COPY `\.` terminator |
| UR-5 | Unknown external behavior | The exact startup parameters psql sends |
| UR-6 | Unknown external behavior | Whether `\restrict` and `--restrict-key` are supported in the pinned and the production versions. Production's `pg_dumpall` call has no `--restrict-key` ([R] `backup.py:117`) |
| UR-7 | Unknown fact (repository) | **Closed (amendment A2, 2026-10-26).** It is recorded: `pg_dump_version` in the manifest ([R] `worker/ops/backup.py:130`) |
| UR-8 | Unknown fact (production) | Which sequence kinds exist in production |
| UR-9 | Unknown fact (repository / freeze) | Freeze records covering `backup.py` |
| UR-10 | Unverifiable assumption | Power-loss durability, which cannot be fully established without fault injection |
| UR-11 | Design gap | The copy's `pg_hba` and `pg_ident` content |
| UR-12 | Design gap / owner decision | The path and permissions for production-side durable state on the driver host. Permissions alone would not close UR-16, because SA-1 and SA-2 act with write access |
| UR-13 | Missing evidence | Whether DATA's method (revisions ≤15) fits §3.20 and §3.22 |
| UR-14 | Missing evidence | Completeness of the gate inventory from revisions 4–15 |
| **UR-15** (16-G) | **Design defect, hard blocker** | **No independent trust anchor for the owner key** (§3.1.4). The channel is an owner decision (PC-D-OKEY b), which is **not** made. Until it exists, every validation aborts with TRUST_ANCHOR_MISSING |
| **UR-16** (new in 16-H) | **Design defect, hard blocker** | **No reviewer-state integrity boundary** (§3.7A). An in-scope adversary (SA-1, SA-2) can rewrite, truncate or roll back `genesis` (including `owner_anchor`) and the state history without detection. The hash chain detects only edits that leave it as it was. Needs a design correction meeting SI-R1 to SI-R7, and the PC-D-TRUST (b, c) decisions. Until then, BT-0 cannot pass and every validation aborts with STATE_INTEGRITY_UNESTABLISHED and TRUST_ANCHOR_MISSING |

---

## 8. Source and version evidence requirements

### 8.1 Pins

The pin records and source-evidence records are as specified in 16-C §3 and 16-D §4. They apply to all of the following:
- the distribution and its dated archive snapshot (signed Release chain and key fingerprint);
- the base image (manifest digest, recipe, build log, package list);
- the VM image and kernel (image hash, `/proc/version`, config hash, LSM list, `ptrace_scope`, kernel source package);
- bubblewrap;
- util-linux (`setpriv`);
- OpenSSH (`ssh-keygen`);
- audit userspace;
- PostgreSQL 17 server, client binaries (`psql`, `pg_restore`, `pg_dump`, `pg_dumpall`, `pg_controldata`, `pg_checksums`) and libpq, for the copy;
- the production `pg_dump` and `pg_dumpall` versions (the manifest records `pg_dump_version`, A2; pinning is still required);
- the Python runtime (C(x));
- our own code: `ckpt-hold`, supervisor, `qcap`, EB, validator, RTM extractor. For these the record is commit, recipe, compiler and binary hash, and each needs an **independent code review** rather than source evidence.

**No version-specific claim** may rest on upstream branches, recollection, or documentation for another version. **No pin record exists yet.**

### 8.2 Independent source review

As in 16-D §4:
1. the reader independently obtains and verifies the source, then applies its patches;
2. the citation gives the file, its hash, the function and lines, the code path, and the selecting conditions, with build-flag evidence;
3. the conclusion is SUPPORTS, CONTRADICTS or INSUFFICIENT;
4. a second, blind reader must agree; if not, the result is INSUFFICIENT;
5. forbidden sources: as listed there.

**No source review has been performed.**

### 8.3 Fixture confirmation and source-review precedence

**Fixture confirmation**, as in 16-D §4:
1. the exact tuple equals the pin records;
2. the expectation is pre-registered by its hash in the approved archive;
3. synthetic data, disposable environment, no outbound network;
4. SBX-AUD has passed first, in the same environment instance. **A negative result with zero audit records is not evidence without that positive control;**
5. raw outputs are kept with hashes;
6. a second person reviews the result;
7. a behavior becomes FIXTURE-CONFIRMED only as precedence rule 5 below allows;
8. any change to a pin invalidates every dependent confirmation.

**Source-review precedence rules (16-F).** These replace 16-D §4 criterion 7 and 16-E §8.3 item 7. **No other text in Revisions 16-C to 16-G may be read as relaxing them.**

1. **Class M (SUPPORTS required) is closed and overriding.** For a behavior in class M, only SUPPORTS satisfies source review. INSUFFICIENT or CONTRADICTS blocks every gate that depends on it. No general rule, annotation or "fails closed" claim can move a behavior out of class M.
2. **Class F (INSUFFICIENT permitted) is an explicit closed list** (§8.4). A behavior is in class F only if all three hold:
   - (a) every way the behavior could differ from the claim leads to refusal by a **named independent check**, whose own correctness does not depend on this behavior;
   - (b) that refusal happens at a stated point: before release for sandbox behaviors; before any restore statement reaches the server for protocol behaviors; before acceptance for evidence behaviors;
   - (c) the refusal check is itself confirmed by a **named fixture**.

   INSUFFICIENT for a class-F behavior satisfies source review only once (c) is FIXTURE-CONFIRMED.
3. **Default to M.** Any behavior not listed in class F, including any behavior added later, is in class M until a reviewed revision lists it in class F with (a) to (c).
4. **CONTRADICTS always blocks,** for every behavior in either class, until a reviewed design change resolves it.
5. **FIXTURE-CONFIRMED** requires both:
   - the source-review result that rules 1–3 require for that class;
   - the behavior's own establishing fixture, executed under the criteria above and matching its pre-registered expectation.

   A class-F behavior with INSUFFICIENT also needs its refusal check (2c) confirmed.
6. **Precedence.** Where any two statements conflict, the stricter requirement applies. A specific class-M requirement always prevails over a generic exception.

### 8.4 Behavior register

Every behavior is **UNVERIFIED**, and each one blocks the gate shown. **No behavior is added in 16-G or 16-H.** E143 is an assumption on which no gate depends (§7.2). 16-H widens E127's stated scope (below); it stays in class M.

**Class M (SUPPORTS required).** It includes every requirement inherited from 16-C (E122c, E123, E126d, E126e, the audit witness, E127, E128), plus the 16-F additions E122b, E125, E116, E117 and E139.

| ID | Behavior | Why class M | Source review can establish | Establishing fixture | Blocks |
|---|---|---|---|---|---|
| E122b | bwrap leaves exactly {PATH, PWD} in the environment | 16-B requires SV-7's expected set to cite the pinned source line. Without SUPPORTS there is no derivable expected set, and setting it from observation would be unsafe | bwrap's clearenv, setenv and chdir code paths | SBX-ENV | AG-QBYPASS |
| E122c | Descriptors ≥ 3 are passed through or closed | Inherited from 16-C ("must be source-read") | bwrap's child and init descriptor handling | SBX-FD | AG-QBYPASS |
| E123 | Process structure and PID-1 | Inherited from 16-C | bwrap's clone and init code | SBX-STRUCT | AG-QBYPASS |
| E125 | `--die-with-parent` kills the sandbox when the supervisor dies | If this fails after registration, psql keeps running unsupervised; the refusal does not happen before restore statements reach the copy | bwrap's PDEATHSIG handling | SBX-24, SBX-I1 | AG-QBYPASS |
| E126d | Yama scope 2 restricts ptrace attach | Inherited from 16-C ("silent") | Kernel docs, config, active LSM | SBX-YAMA | AG-QBYPASS |
| E126e | `/proc/<pid>/root` shows H's mount view | Inherited from 16-C ("silent") | Kernel | SBX-1 | AG-QBYPASS |
| E116 | Abstract Unix sockets are scoped to the network namespace | SV-5 reads H's own namespace list, so wrong scoping would be invisible to it (silent) | Kernel | SBX-ABS | AG-QBYPASS |
| E117 | "Connection received" is logged before startup options apply | If wrong, detection silently misses sessions — 16-A's NOT DEMONSTRATED case | PostgreSQL source | QB-8 | AG-QBYPASS |
| E127 | SSHSIG binds the namespace; `-Y verify` exits 0 only on a full match. **Scope widened in 16-H:** a principal made only of `[A-Za-z0-9._@+-]` matches only itself; the signature carries its signer's public key, which `-Y verify` checks against the file's key | Inherited from 16-C | OpenSSH `PROTOCOL.sshsig`, `sshsig.c`, and the allowed-signers matching code | FR-REC-3, FR-NS-1, RKN-11, RKN-17, RKN-19, CAN-15; BOOT-17 (refusal by the policy, not by `ssh-keygen`) | AG-FRESH, AG-SEAL |
| E128 | `flock`, `rename` and `fsync` serialization and durability | Inherited from 16-C. The power-loss part stays an assumption (UR-10) | Kernel and filesystem docs | FR-CONC-1 to FR-CONC-3, FR-INT-1 to FR-INT-3 | AG-FRESH |
| Audit witness | Audit records every `execve` of the watched path | Inherited from 16-C ("silent") | Audit userspace and kernel docs | SBX-AUD | Every negative SBX fixture |
| E139 | `pg_dumpall` bootstrap-role behavior on restore | Tied to design defect UR-3 (C-b); class M until resolved | `pg_dumpall` source | SGR-10 | AG-GLB |

**Class F (INSUFFICIENT permitted only under precedence rule 2).** The list is closed.

| ID | Behavior | (a) Independent refusal check | (b) Where refusal happens | (c) Confirming fixture for the refusal | Establishing fixture | Blocks |
|---|---|---|---|---|---|---|
| E122a | bwrap passes fds 0–2 to the command | S3 finds no holder → FAIL_NO_HOLDER | Before release | SBX-23 | SBX-P1 | AG-QBYPASS |
| E124 | `setpriv` sets credentials and leaves fds and environment alone | SV-2, SV-7 and SV-8 read the kernel state | Before release | SBX-6 to SBX-10 | SBX-P1 | AG-QBYPASS |
| E126a | A pidfd refers to one process and signals its exit | Directory-fd re-check and S5 → FAIL_HOLDER_EXIT; a release to a dead holder runs nothing | Before release | SM-2, SBX-22 | SM-2, SBX-22 | AG-QBYPASS |
| E126b | `close_range(3, ~0U, 0)` closes every fd ≥ 3 | SV-8 refuses any extra descriptor | Before release | SBX-27 | SBX-FD0 | AG-QBYPASS |
| E126c | `/proc/<pid>/syscall` shows the blocked read | S3 cannot confirm the hold point → FAIL_NO_HOLDER | Before release | SBX-23 | SBX-STRUCT | AG-QBYPASS |
| E121 | A socket can be connected to through a read-only bind mount | If false, nothing connects; SESSION-IDENTITY shows no session | Before any restore statement reaches the server | QB-1 | SBX-P1 | AG-QBYPASS |
| E130 | Path-named sockets are reachable across network namespaces | As E121 | Before any restore statement reaches the server | QB-1 | SBX-P1 | AG-QBYPASS |
| E129 | SO_PEERCRED's pid is translated into `qcap`'s namespace | Admission refuses any pid, uid or starttime mismatch | Before the upstream connection opens | SBX-26 | SBX-P1, PROTO-15 | AG-QBYPASS, AG-QID |
| E113 | The log's pid equals the `BackendKeyData` pid | A mismatch is a DEFECT and ambiguity is UNKNOWN | Before acceptance | QID-7, PROTO-15 (mismatch case) | PROTO-15 | AG-QID |
| E133 | `pg_file_settings` semantics | The LC-BASE file-hash comparison (H1 = AK hashes) catches any edit | Before R0 | CFG-2 | CFG-4 | AG-CFGOBS |
| E134 | `log_statement` and the syntax-error caveat | Missing or unknown shapes → UNKNOWN | Before acceptance | LRC-9, LRC-14 | LRC-2 | AG-LOGCOV, AG-JCOMP |
| E135 | Multi-statement simple-query error semantics | Any multi-statement Query → UNKNOWN, whatever the server does | Before acceptance | OPR-7 | ADV-20 | AG-EB |
| E136 | `psql -f` sends one statement per Query | EB_MISMATCH or MULTI_STATEMENT_QUERY | Before acceptance | OPR-3, OPR-7 | FX-EB, ADV-1 to ADV-14 | AG-EB |
| E137 | `pg_dump`'s setval form | SEQ_UNSUPPORTED | Before acceptance | SQV-6 | SQV-1 | AG-SEQ |
| E138 | `pg_restore` render determinism | A SCH or GLB mismatch → no acceptance | Before acceptance | SGR-2 | SGR-1 | AG-SCHEMA, AG-GLB |
| E140 | `pg_controldata`'s checksum line | DC_UNPARSABLE or DC_MISMATCH | Before R0 | CKS-3, CKS-4 | CKS-1 | AG-DC |
| E141 | CancelRequest arrives on a separate connection | Any unsupported message → QCAP_UNSUPPORTED | Before any further statement reaches the server | PROTO-7 | PROTO-8 | AG-QCAP |
| E142 | libpq does not attempt SSL or GSS on Unix sockets | SSLRequest or GSSENCRequest → QCAP_UNSUPPORTED | Before startup completes | PROTO-6 | PROTO-6 | AG-QCAP |

E120 is the umbrella for the bwrap and `setpriv` rows above. E131 and E132 are not source-reviewable (§7.2).

**AG-PINSRC and AG-QBYPASS stay CLOSED.** No behavior is verified.

---

## 9. RTM registry and collision requirements

This consolidates 16-A to 16-I. **No historical identifier is renamed.**

**Prerequisites. All must hold before any real-archive scan can pass:**
1. archived revisions whose hashes match (PC-D-DOCS). **The archive for Revisions 4–15 does not exist; this blocker stands;**
2. occurrence-level collision decisions for `F5`, `C3`, `C4`, `R1`, `R2`, `P1`, `S`, `R-F4`, and any collision a scan of revisions 4–15 reveals;
3. EXAMPLE spans for every quoted fixture input in 16-A to 16-I;
4. approved registries (no canonical string in two registries) and an approved CT, including the collision list and occurrence rows;
5. exact identifier meanings, and reviewed exceptions (PAIR-INDEPENDENT rows);
6. two independent reproductions, by different operators on different machines, producing byte-identical RTM output (16-D §5).

**Without all of these, the verdict is FAIL even with zero diagnostics.**

**An unlisted collision remains a review risk:** the scanner cannot detect it.

**Identifiers to register.**
- **Added in 16-E:**
  - VS-1 to VS-9, FB-28 to FB-37, FR-HIST-1 to FR-HIST-13;
  - PROTO-1 to PROTO-16, ADV-15 to ADV-22;
  - LRC-1 to LRC-16, CFG-1 to CFG-10, CKS-1 to CKS-5;
  - GRD-1 to GRD-8, OPR-1 to OPR-9, SQV-1 to SQV-9, SGR-1 to SGR-10, PVR-1 to PVR-4;
  - RGV-1 to RGV-8, SLV-1 to SLV-9, ORC-1 to ORC-11, ORC-8b;
  - PC-D-REVIEW, PC-D-SKEW, PC-D-EMAX, PC-D-BASE, PC-D-INDEP, PC-FX-GRD, PC-FX-ORC;
  - AG-STATUS, AG-ORCH, AG-GUARD;
  - E131 to E142, UR-1 to UR-14.
- **Added in 16-F:** RKN-1 to RKN-24, SBX-27, SRC-P2, SRC-P3, SRC-N15 to SRC-N18.
- **Added in 16-G:**
  - BT-1 to BT-7 (bootstrap steps);
  - BOOT-1 to BOOT-12, LIFE-1 to LIFE-14, CAN-1 to CAN-9;
  - E143, UR-15.
- **Added in 16-H:**
  - BT-0;
  - SA-0 to SA-3 (adversary classes), SI-R1 to SI-R7 (requirements), OI-1 to OI-4 (claims);
  - BOOT-13 to BOOT-17, BOOT-14b, LIFE-15 to LIFE-18, CAN-10 to CAN-14;
  - UR-16.
- **Added in 16-I:** CAN-15 to CAN-20, FR-HIST-14, FR-HIST-15. No new prefix is introduced.
- **Added by amendment A1 (2026-10-26):** FR-HIST-16.
- **Not RTM candidates:**
  - record kinds and namespaces: `validation`, `fork`, `seal-conflict`, `instance`, **`kit`**, **`attestation-conflict`**, `cse-checkpoint-seal`, `cse-checkpoint-rejection`;
  - record fields and names: `owner_anchor`, **`verified_by`**, **`evidence_instances`**, **`evidence_manifest`**, **Line_owner**, **ST_CAN**, **V_E**, **A_p** (each contains an underscore);
  - file names: `allowed_signers.owner`, `allowed_signers.evidence`;
  - reason codes and sub-codes, which contain underscores;
  - the section labels "3.12A" and "3.7A" (each contains a dot);
  - from 16-I: Δ1 to Δ9 (they begin with a Greek letter), κ, Res, the record fields `inst_digest` and `currency_digest` (A1), the validation kind `use`, the sub-codes NOT_VALID, REVIEW_NOT_CURRENT and ACCEPTED_RECORD_ONLY, and the import results STORED and UNNAMED.
- **Prefix check:**
  - BT, BOOT, LIFE and CAN (16-G) and SA, SI-R and OI (16-H) are not used elsewhere in the revisions available to the author.
  - **The adversary classes were deliberately not named A0 to A3**, because A0 is an existing identifier.
  - Revisions 4–15 are not available; that is part of the archive blocker, and they must be scanned for all of these prefixes too.

**Fixtures:**
- **Revision 16:** RTM-1, RTM-2.
- **16-A:** RTM-PROP, RTM-R.
- **16-B and earlier:**
  - RTM-P1 to RTM-P9;
  - RTM-N1 to RTM-N26, including RTM-N15d, RTM-N16b, RTM-N16c;
  - RTM-SELF.
- **16-C:** RTM-P10, RTM-N27 to RTM-N32.
- **16-D:** RTM-P11, RTM-N33 to RTM-N38.

None has been run.

---

## 10. Status summary

### 10.1 Settled on paper (design consistency only; nothing verified)

- The trust-bootstrap order and the circularity rule: the pre-state checks (chain, BT-0, BT-1, BT-2) at step 2, then BT-3 to BT-7 at step 6a. **Neither the anchor (UR-15) nor the state-integrity boundary (UR-16) exists.**
- The signed-object pipeline over five roles in two key classes, with the exact signers-file grammar and signers-file resolution (purpose AK, kit store, V_E, `verified_by`).
- The three-purpose evidence-key lifecycle: historical validation at h_R, rejection classification over [h_R, h_latest], eligibility at h_U. After 16-H it can also be reached after a key rotation.
- The persisted model: the authoritative inputs are named; `seen` holds content-determined fields only; every instance is recorded; derived records are recomputed at load.
- Canonicality is judged only on the exact instances named for a run's evidence positions, from the bytes in the run's own evidence directory.
- The order-independence claims OI-1 to OI-4 are **specified, not established.** From 16-I, import results are keyed by the input identity κ, and OI-3 compares the decision-input tuple Δ.
- The reviewer-state threat model (SA-0 to SA-3) and the fail-closed BT-0 rule.
- Authentication of seals and rejection records, and the rule that evidence-class objects never raise floors.
- The record point, integrity flags and reason-code precedence.
- `floor_p0` authentication under every option.
- The delivery and skew interval model, and the expiry horizons, expressed as formulas.
- The status machine and the separate eligibility predicate, now evaluated by `use` validations with review currency by `inst_digest`.
- The source-review precedence rules and the class M / class F register.
- The re-derived FB, FR-LATE, FR-HIST, RKN, BOOT, LIFE and CAN expectations, under fixture parameters, a synthetic anchor, the BT-0 stub and the interim rules.
- The qcap identity chain and the fail-closed message set; the supported script constructs; the configuration snapshot algorithm; the checksum checks; the guard boundary statement; the reconciliation levels; the sequence contract; the validator and orchestration rules.

### 10.2 Open items by type

None of these is a contradiction in the rules. Each is open for a stated reason, and **every one still blocks.**

| ID | Type | Item | Needs |
|---|---|---|---|
| **UR-15** | **Design defect: hard blocker** | No independent trust anchor for the owner key (§3.1.4) | An owner decision on the anchor channel and record (PC-D-OKEY b). **Not made;** this note neither makes nor proposes it |
| **UR-16** (new in 16-H) | **Design defect: hard blocker** | No reviewer-state integrity boundary. `genesis`, including `owner_anchor`, and the state history can be rewritten, truncated or rolled back without detection by in-scope adversaries (§3.7A) | A design correction meeting SI-R1 to SI-R7, and owner decisions PC-D-TRUST (b, c). This note chooses no mechanism |
| **C-b** (UR-3) | **Design defect** | Script-mode globals restore has no defined handling for pg_dumpall's bootstrap-role output under ON_ERROR_STOP. A filtered tolerance like direct mode's ([R] `restore_check.py:75`) would be a transformation that EB has not modeled | A design correction, after source review of E139 |
| **C-a** | **Parameter inconsistency needing an owner decision** | The rules are consistent, but under the fail-closed delivery model delivery is limited to 2S. The values first proposed in 16-B (Δ_att = 900 s, S = 300 s) cannot both hold | PC-D-SKEW. This note does not choose values |
| **C-c** | **Intentional policy choice pending an owner decision** | The review-order options deliberately give different statuses for the same evidence. Under `current-floor` and the interim rule, a run is also eligible for at most one use (§3.6, 16-I) | PC-D-REVIEW |

The other unresolved items (UR-1, UR-2, UR-4 to UR-6, UR-8 to UR-14) are typed in §7.3. UR-7 is closed (A2).

### 10.3 Unverified

- Every [E?] item (§7.2, §8.4); every behavior in class M and class F.
- Every BLOCKED-PIN expectation.
- Every fixture: none has been executed (§6.0).
- Everything carried from revisions 4–15 by name.
- The trust anchor: none exists (UR-15).
- **Order independence:** OI-1 to OI-4 are specified, not demonstrated. The sufficiency of Δ for OI-3 is argued step by step, not proved, and not tested.
- **Tamper detection:** the only detection specified is of stored records that disagree with each other, and of a chain left as it was after an edit. None of it is demonstrated. Detection of a full rewrite, a truncation or a rollback does not exist (UR-16).
- **The 16-H and 16-I corrections themselves** (16-H's rows 4a to 4f; 16-I's §0) have not been independently reviewed.

### 10.4 Evidence required next, in order

1. An owner decision on the trust-anchor channel and record (PC-D-OKEY b), resolving UR-15.
2. A design correction for UR-16 meeting SI-R1 to SI-R7, with the PC-D-TRUST (b, c) decisions; then BOOT-13, BOOT-14 and BOOT-14b re-registered as detection fixtures.
3. The PC-D-DOCS archive, including Revisions 4–15.
4. The other owner decisions in §7.1.
5. Pin records (PC-D-BASE, PC-D-PGVER, PC-D-VM).
6. Independent source reviews (§8.2), judged under the precedence rules (§8.3).
7. A design correction for C-b (UR-3).
8. Change-control authorization for the machinery (PC-CC-CKPT-IMPL) and for P1-CC-PROV.
9. Implementation.
10. Fixture execution with independent review (§8.3).
11. The RTM with two reproductions.

### 10.5 Traceability

**Retained unchanged from 16-H** (16-H's own changes from 16-G are listed further below):
- all gates, preconditions and aliases listed in §5. Owner decisions are unchanged apart from the subject extensions of PC-D-TRUST, PC-D-EKEY (b) and PC-D-OKEY (b);
- every change-control requirement (PC-CC-CKPT-IMPL, PC-CC-P1PROV, PC-CC-VC, PC-CC-RUNBOOK6, PC-CC-RF4);
- every source and version pin requirement, and the precedence rules;
- the archive/RTM blocker for Revisions 4–15;
- UR-15 and PC-D-OKEY, both open; UR-16, SI-R1 to SI-R7 and the fail-closed BT-0 rule; the BT-0 stub as a fixture-only harness;
- the rules of §3.12A; the exact-instance canonicality rules of §3.2.3 (16-I only clarifies how entry positions are keyed); the status transitions of §3.5;
- all fixtures not listed as revised below;
- the threat model (PC-D-TRUST) for every component other than reviewer state;
- the separate P1-CC-PROV;
- zero supported tuples;
- current dumps ineligible.

**Revised in 16-I:**

| Item | Revision |
|---|---|
| §0 | Change matrix against 16-H |
| §3.1.2A | Kit files examined again, to a fixpoint; every input without a VERIFIED instance processed again; V_E only grows |
| §3.1.5 | Group 11 (eligibility); ELIGIBLE(R, U) defined by the empty list |
| §3.2.1 | Input identity κ; role-only invariant; `import` record fields and writing rule; idempotence and processing again by κ; r_S(κ); decision inputs include `accepted` records and R's rejection directories |
| §3.2.2 | Res(S); `inst_digest`; unfiltered floors in Proj; fixed import order with kit files |
| §3.2.3 | Entry positions keyed by content `seq` |
| §3.2.4 | Proj with Res; reordering and file set defined; OI-1 and OI-2 restated; OI-3 over Δ (Δ1 to Δ9) with a sufficiency argument and the report's content |
| §3.3 | Step 4 processing set and order; step 6 `import` notes; step 8 kind `use`, `inst_digest`, use fields |
| §3.5 | `use` records never change status |
| §3.6 | Computed by a `use` validation; item 1 currency by `inst_digest`; sub-codes; the at-most-one-use consequence under `current-floor` and the interim |
| §4.6 | OI references |
| §5 | Fixture-family ranges and gate conditions (CAN-15 to CAN-20; FR-HIST-14, FR-HIST-15) |
| §6 | §6.0; FR-REC-8; FR-HIST-9, FR-HIST-12, FR-HIST-14, FR-HIST-15; LIFE setup and LIFE-13; CAN-5, CAN-8, CAN-9, CAN-11 revised; CAN-15 to CAN-20 new; §6.14 |
| §7.1 | PC-D-REVIEW interim consequence |
| §8.4 | CAN-15 added to E127's establishing fixtures |
| §9 | 16-I identifiers |
| §10 | This section |

**Revised in 16-H:**

| Item | Revision |
|---|---|
| §3.1.1 | The evidence roles' signers file is a member of V_E |
| §3.1.2 | Exact one-line grammar; restricted principal character set; Line_owner; EVIDENCE_KEY_REUSE; abort versus exclusion; rotation keeps earlier evidence files |
| §3.1.2A | New: purpose AK, kit store, V_E, `verified_by`, re-trying evidence-class files |
| §3.1.3 VS-4, VS-5, VS-7, VS-8 | Signers-file resolution; the principal comes from the file used; `verified_by`; the stored canonical flag is never a decision input |
| §3.1.4 | Pre-state checks (chain, BT-0); BT-1 requires BT-0; BT-2 is a byte comparison with Line_owner; BT-3 is not an abort; BT-3 to BT-7 at step 6a; what stays recorded after a step-6a abort |
| §3.1.5 | Aborts in step order; the pre-state group is evaluated as a whole; EVIDENCE_LAYOUT_INVALID sub-codes |
| §3.2 | Split into §3.2.1 to §3.2.4: persisted model, aggregation, exact evidence instances, order-independence claims |
| §3.3 | Step order; the import-only invocation; fixed import order; `evidence_instances`; the audit reference kept outside the report |
| §3.5 | The abort row names the pre-state codes |
| §3.6 | `verified_by`; item 5's consequence for rotation |
| §3.7 | Record kinds `kit` and `attestation-conflict`; content-only `seen`; every instance recorded; R-PEND after the pre-state checks; rebuild cannot establish integrity |
| §3.7A | New: the threat model, finding UR-16, the fail-closed rule, SI-R1 to SI-R7 |
| §3.8, §3.9, §3.11, §3.26 | References to the pre-state codes and UR-16 |
| §3.12A | E = `verified_by`; earlier evidence files stay in V_E; rotation changes the AK |
| §3.13 | Prerequisite; seal verified under V_E; `ak_sha256` binding; manifest checks per §3.2.3 |
| §3.25 | `floor_p0` representatives; the manifest excludes the seal itself; instance bytes stored beside the seal; the audit reference |
| §4.6 | E = `verified_by`; a missing entry means not in force; reference to OI-1 to OI-4 |
| §5 | Subject extensions of PC-D-TRUST, PC-D-EKEY (b) and PC-D-OKEY (b); UR-16 in five gate conditions; fixture-family ranges |
| §6 | §6.0 execution status; ST_CAN, the BT-0 stub, the +seen notation; FR-REC-1, FR-REC-5, FR-REC-6 and FR-REC-8 wording; BOOT-6, BOOT-8, BOOT-9, BOOT-11 and BOOT-12 revised; BOOT-13 to BOOT-17 and BOOT-14b new; LIFE-6 codes and LIFE-7, LIFE-11 wording revised; LIFE-15 to LIFE-18 new; CAN-1, CAN-2, CAN-4, CAN-5, CAN-6, CAN-8 and CAN-9 revised; CAN-10 to CAN-14 new; §6.14 |
| §7 | PC-D-OKEY, PC-D-EKEY and PC-D-TRUST rows; E127 scope note; UR-12 note; UR-16 |
| §8.4 | E127's scope widened; still class M |
| §9 | 16-H identifiers; prefix check |
| §10 | This section |

**Superseded but kept as identifiers:**

| Item | Superseded by |
|---|---|
| 16-H §3.2.4: Proj's import results "for each `raw_sha256`" | Input identity κ and Res(S) (16-I) |
| 16-H OI-1: "a fixed … kit store" | The starting kit store is fixed; delivered kit files are part of F (16-I) |
| 16-H OI-3: "starting from states with equal Proj" | Equal Δ (16-I) |
| 16-H §3.1.2A: "owner-class files are not re-tried" | Every input without a VERIFIED instance is processed again (16-I) |
| 16-H §3.6 item 1: "a review completed after the latest import" | Review currency by `inst_digest` (16-I) |
| 16-I §3.6 item 1 as issued: the comparison with the `use` validation's state after step 4 | `currency_digest` over the state after step 6 (amendment A1) |
| 16-I §3.22 as issued: "Direct mode used `cse_restore_check`"; UR-7 open | The manifest's bootstrap superuser; UR-7 closed (amendment A2) |
| 16-H FR-REC-8: "only `raw_sha256` is computed" | κ is computed (16-I) |
| 16-G §3.2 and §3.7: `seen` holds the first VERIFIED instance, and `instance` records only later ones | §3.2.1 (16-H) |
| 16-G §3.2: "Reordering inputs cannot change an acceptance result", stated as fact | OI-1 to OI-4, stated as claims (16-H) |
| 16-G §3.3 steps 2 (R-PEND), 2a (BT-1, BT-2) and 4a (BT-3 to BT-6) | §3.3 steps 2, 2a and 6a (16-H) |
| 16-G: a BT-3 failure aborts with nothing recorded | Group-2 codes (16-H) |
| 16-G VS-5 and VS-7: "the registered principal" | p(F), the principal in the file used (16-H) |
| 16-G: one `allowed_signers.evidence` at VS-4 and BT-5 | Signers-file resolution, §3.1.2A (16-H) |
| 16-G CAN-4, which repeated CAN-2 | CAN-4 revised (16-H) |
| 16-G BOOT-11: "STATE_CORRUPT (the chain breaks)" as tamper detection | BOOT-11 re-scoped; BOOT-13 to BOOT-15 (16-H) |
| 16-G §10.1: "order-independent acceptance" as settled | §10.1 and §10.3 (16-H) |
| 16-F §3.12's "latest head" authorization and §3.13's "run's head" authorization, as separate unexplained rules | §3.12A (16-G) |
| 16-F §3.2's "the first `seen` record keeps its raw bytes" as the only retained representation | The instance model (16-G), revised again in 16-H |
| 16-F §10.2's "irreducible contradictions" label | §10.2 open items by type (16-G) |
| 16-E §3.1's three-role contract and single signers file | §3.1 (16-F) |
| 16-D §4 criterion 7 and 16-E §8.3 item 7 (generic INSUFFICIENT exception) | Precedence rules (16-F) |
| 16-E's statements that floors and FORK coverage were "resolved" | Open decisions PC-D-FRESH b and c (16-F) |
| FR-POS-2 | Withdrawn (16-C) |
| FR-B1 to FR-B4 | FB rows (16-C) |
| FR-NEW-4 | FR-REC-1 (16-C) |
| The fd-3 handshake | fd 0 (16-B) |
| 16-A's enforcement claim | Withdrawn (16-A corrections) |
| 16-B's rename to FRS-* | Withdrawn (16-C) |
| F9's unsigned confirmation | The time attestation (16-A corrections) |

**Amended after issue (2026-10-26):** A1 and A2; see the amendment record at the top.

**Unresolved:** UR-1 to UR-6 and UR-8 to UR-16 (UR-7 closed by A2); C-a, C-b, C-c; every decision in §7.1.

### 10.6 Final status by dimension

| Dimension | Status |
|---|---|
| Design consistency | On paper, apart from the §10.2 open items (including the hard blockers UR-15 and UR-16) and UR-1 to UR-16 (UR-7 closed by A2). The 16-H and 16-I corrections, and amendments A1 and A2, are unreviewed |
| Source review | None |
| Fixture execution | None. No test or fixture has been executed |
| Independent evidence review | None |
| Owner approval | None of §7.1 |
| Repository implementation | None. No implementation exists, so none has been verified |
| Acceptance / freeze authorization | None |

**NOT READY. DO NOT MERGE. DO NOT EXECUTE RESTORE. R-F4 NOT ACCEPTED / NOT FROZEN. No restore is authorized.**
