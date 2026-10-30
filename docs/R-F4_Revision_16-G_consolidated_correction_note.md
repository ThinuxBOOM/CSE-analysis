# R-F4 checkpoint restore: Revision 16-G, consolidated correction note

| | |
|---|---|
| Document | Revision 16-G, consolidated correction note (design only) |
| Relationship to earlier revisions | Replaces Revision 16-F as the consolidated note. Corrects Revisions 16 to 16-F where stated; everything else in them remains in force. Revisions 16 to 16-D are not reproduced here |
| Date | 2026-10-24 |
| Repository | CSE-analysis, branch `claude/rf4-implementation`, commit `eb91d3e` (no new reads for 16-G; [R] citations are carried from 16-E) |
| Prepared by | Claude (Anthropic), at the owner's request |
| Prepared for | Independent audit |
| Status | **NOT READY. DO NOT MERGE. DO NOT EXECUTE RESTORE. R-F4 NOT ACCEPTED / NOT FROZEN. No restore is authorized.** |

## Contents

0. What changed from 16-F
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
| [R] | Repository fact, read at commit `eb91d3e` (during 16-E; nothing was re-read for 16-F or 16-G) |
| [E] | External behavior documented by an exact pinned source. **None qualifies.** Every earlier reading — the PostgreSQL 17 documentation and the psql `REL_17_STABLE` source — was unpinned, so it is cited as [E?] |
| [E?] | Unverified external claim |
| [P] | Design proposal |
| [X] | Executed evidence: **none** |

**Work done for 16-G:** design text only. No repository file was read or changed, no external source was read, and no test, build, connection, transfer, restore, commit, push or merge happened. **Nothing in this note has been independently verified, source-reviewed or executed.**

**History available to the author:**
- Revisions 16 to 16-F: verbatim.
- Revisions 4 to 15: only through a summary. Their items are carried **by name**; where a definition could not be re-checked, that is flagged (UR-2, UR-14). **The archive/RTM blocker for Revisions 4–15 stands** (PC-D-DOCS, AG-RTM).

---

## 0. What changed from 16-F

Evidence status for every row: **design only. No source review, no fixture execution, no independent evidence review, no owner approval.**

| # | 16-F finding | 16-F text | 16-G correction, or remaining blocker | Where | Fixtures (all unexecuted) |
|---|---|---|---|---|---|
| 1a | No independent trust anchor | The AK pinned `allowed_signers.owner`; the owner-signed ledger approved the AK (PC-D-AKIT) and the signers files (PC-HR-KEYS). Each depended on the other, and nothing authenticated the first owner key | **Hard blocker UR-15.** No approved artifact or host registration supplies an anchor. PC-HR-KEYS and PC-D-AKIT are owner-signed ledger entries and cannot authenticate the key that signs them. PC-HR-PROD registers the driver host, not keys. Revision 16's out-of-band confirmation covered head hashes, not keys, and 16-A replaced it with an owner-signed attestation. The anchor channel is an **open owner decision (PC-D-OKEY)**; this note does not invent one | §3.1.4, §7.1, §7.3 | — |
| 1b | Bootstrap order undefined | — | Exact bootstrap steps BT-1 to BT-7, run at the start of every validation. The anchor is checked first; then the ledger, the AK approval, the AK pins and the key registration. A self-consistent AK-and-ledger loop is never accepted as a root (TRUST_ANCHOR_MISSING) | §3.1.4, §3.3 steps 2a and 4a | BOOT-1 to BOOT-12 |
| 2a | §3.12 (latest head) and §3.13 (run's head) conflicted | Two different heads were used for evidence-key authorization, without saying why | Three purposes, each with its own head: **historical validation** at h_R; **rejection classification** over [h_R, h_latest]; **current eligibility** at h_U | §3.12A, §3.12, §3.13, §3.6 | LIFE-1 to LIFE-14 |
| 2b | Rotation, revocation after sealing, and rejections signed before or after revocation | Not covered | Positional rules for when a key is in force. Revocation after sealing leaves historical status intact but fails eligibility. A rejection by a key revoked by h_latest gives REJECTION_KEY_REVOKED (UNKNOWN, not terminal). "Before or after" is judged by observation order, and that limit is stated. Whether routine rotation should preserve eligibility is an **open decision (PC-D-EKEY)**; the interim is fail-closed | §3.12A, §7.1 | LIFE-5 to LIFE-12 |
| 3a | Deduplication versus NONCANONICAL | Deduplication by (role, content hash) kept the first instance; VS-8 blocked acceptance, but it was unclear which representation VS-8 judged | **Logical objects and instances are separated.** Floors, flags and deduplication use logical objects. Canonicality decides acceptance only for the run's own evidence instances (exactly one per required position). Instances found elsewhere can neither cure nor taint the run's. Every distinct VERIFIED instance is recorded. Acceptance cannot depend on input order | §3.2 | CAN-1 to CAN-9; FR-REC-6 state effect revised |
| 4a | Status terminology | §10.2 called C-a, C-b and C-c "irreducible contradictions" | Reclassified: C-a is a **parameter inconsistency needing an owner decision**; C-b is an **unresolved design defect**; C-c is an **intentional policy choice pending an owner decision**. UR-15 is added as a **design defect and hard blocker**. Every UR item now has a type | §1, §7.3, §10.2 | — |

**Remaining blockers, all unchanged:** the Revisions 4–15 archive/RTM blocker; missing source/version pins and source reviews; every unapproved owner decision; every change-control requirement; every existing release gate; the prohibition on merge and on executing a restore.

---

## 1. Executive verdict

| Dimension | Status |
|---|---|
| Design consistency | Consistent on paper, apart from the open items in §10.2 — one parameter decision, two design defects (one of them the hard blocker UR-15) and one pending policy choice — and the unresolved items UR-1 to UR-15 (§7.3) |
| Source review | None |
| Fixture execution | None |
| Independent evidence review | None |
| Owner approval | None of the decisions in §7.1 |
| Repository implementation | None of the machinery |
| Acceptance or freeze authorization | Not given |

**Implementation readiness: NOT READY.** What blocks it:
- **UR-15 (hard blocker):** no independent trust anchor for the owner key. Until one exists, every validation aborts with TRUST_ANCHOR_MISSING.
- **Owner decisions** (§7.1), none of which is decided:
  - PC-D-OKEY (including the anchor channel), PC-D-EKEY (including rotation and eligibility);
  - PC-D-REVIEW, PC-D-SKEW (C-a), PC-D-EMAX, PC-D-BASE, PC-D-INDEP;
  - PC-D-FRESH (floors from rejected objects; FORK coverage);
  - PC-D-RGI, PC-D-QCAP, PC-D-LEASE, PC-D-PGCHK, PC-HR-KEYS.
- **Missing evidence:** the PC-D-DOCS archive for Revisions 4–15 does not exist, and no AG-PINSRC evidence exists.
- **UR-3 (C-b):** the globals-restore design defect.
- **Change control:** neither PC-CC-CKPT-IMPL nor P1-CC-PROV has been authorized or implemented.

---

## 2. Resolution matrix (16-E findings, updated)

Every row has evidence status **design only; no source review, fixtures, or evidence.** "§" refers to sections of this note. **▲ marks rows changed in 16-G.**

| # | History | Correction | Fixtures | Expected |
|---|---|---|---|---|
| A1 ▲ | 16-B C3 order contradiction; 16-C §1 VS pipeline; self-inflicted rollback (16-C); 16-F roles | §3.1–§3.3: one pipeline over five roles, preceded by the trust bootstrap. Logical objects and instances (§3.2). Floors raised only by VERIFIED owner-class logical objects. Floors evaluated before the run's own objects are recorded. Fixed reason-code precedence. Whether rejected objects raise floors is an open decision (PC-D-FRESH b) | FR-REC-1 to FR-REC-8; FB-13 to FB-16; FR-NONCE-3; RKN-*; BOOT-*; CAN-* | UNVERIFIABLE objects never raise floors. Under the interim rule, verified-but-rejected owner-class objects do. Acceptance does not depend on input order |
| A2 | 16-C decision 2; 16-D §1 options; FR-LATE-7 gap | §3.4: three options plus an interim rule. FORK coverage is an open decision (PC-D-FRESH c); the interim checks every seen head and entry | FR-LATE-1 to FR-LATE-10; FR-HIST-4 to FR-HIST-7 | Option-dependent results are tabulated. Under the interim rule, FR-LATE-7 is FORK |
| A3 | 16-D §2 note; CLOCK_SKEW referenced m1 | §4.1–§4.2: interval model; CLOCK_STEP and DELIVERY_AMBIGUOUS. Values are fixture parameters only | FB-1 to FB-10, FB-28 to FB-35 | Derived from fixture parameters |
| A4 | 16-D formulas; 16-C decision on E > E_max | §4.3; interim: RUN_EXCEEDS_EMAX rejects (PC-D-EMAX open) | FB-17 to FB-27, FB-36, FB-37 | FB-20 = {RUN_EXCEEDS_EMAX, APPROVAL_EXPIRED} under the interim rule |
| A5 | 16-D FR-LATE-10 contradiction | §3.3 step 5: authenticating `floor_p0` is mandatory under every option | FR-LATE-10, FR-HIST-4 | SNAPSHOT_INVALID under all options |
| A6 ▲ | 16-D: `accepted` records persist; 16-F rejection transitions | §3.5–§3.6 and §3.12A: immutable records; status derived from them; eligibility kept separate; evidence-key lifecycle by purpose | FR-HIST-1 to FR-HIST-13; RKN-5, RKN-18, RKN-21 to RKN-23; LIFE-1 to LIFE-14 | An old `accepted` record never implies current validity |
| A7 ▲ | 16-C FR-REC-6 defect; 16-D revision | §3.2 instances; §6.2 | FR-REC-6, FR-REC-8, CAN-1 to CAN-4 | FR-REC-6 now records +instance |
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
| D25 ▲ | Rev 16 §5; 16-A A3; 16-B C3; 16-C to 16-F | §3.1–§3.7, §4.4, with the trust bootstrap and the instance model | LED-*, FR-*, FB-*, RKN-*, BOOT-*, CAN-* | Blocked by UR-15 |
| D26 ▲ | Seal, events log (earlier); 16-F evidence key | §3.11, §3.12A, §3.13, §3.25 | SLV-1 to SLV-9, RKN-*, BOOT-7 to BOOT-9, LIFE-* | Blocked by UR-15 |
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
| seal | evidence | `cse-checkpoint-seal` | `<evidence principal>` | `allowed_signers.evidence` | §3.25, including `signer_principal` | `seal.json` in the run's production-side evidence |
| rejection | evidence | `cse-checkpoint-rejection` | `<evidence principal>` | `allowed_signers.evidence` | §3.12: `rejection_version, run_id, nonce` (or null), `signer_principal, stage, codes, streams` | The run's directory in the primary or secondary rejection store |

**The principal strings, keys and both signers files are undecided** (PC-HR-KEYS, PC-D-EKEY, PC-D-OKEY). This note does not choose them.

**Defense against cross-role substitution and replay.** These are listed in the order the pipeline relies on them:
1. **Role from position:** a verifier never chooses a namespace or principal from the object's own content.
2. **Namespace binding:** SSHSIG binds the namespace into the signed data [E?] (E127, class M). A signature made under one namespace fails verification under any other.
3. **Disjoint key classes:** the two signers files share no key and no principal. An owner key never verifies as evidence, and an evidence key never verifies as owner.
4. **Distinct schemas:** each role has its own discriminator (`kind`, `attestation_version`, `seal_version`, `rejection_version`) and its own exact key set. Bytes made for one role fail VS-6 in any other.

Acceptance relies on items 1–3. Item 4 is defense in depth only and is not counted as evidence.

#### 3.1.2 Signers-file policy

- There are **two files**, `allowed_signers.owner` and `allowed_signers.evidence`.
  - Each is pinned by SHA-256 in the AK.
  - **Both hashes form the subject of the PC-HR-KEYS registration,** which the owner approves in the ledger.
  - **Neither the AK pin nor the registration authenticates the owner key.** Only the trust anchor does (§3.1.4).
- **`allowed_signers.owner`:** exactly one line, for the registered owner principal. Its only option is `namespaces="cse-checkpoint-approvals,cse-checkpoint-approvals-head,cse-checkpoint-confirm"`.
- **`allowed_signers.evidence`:** exactly one line, for the registered evidence principal. Its only option is `namespaces="cse-checkpoint-seal,cse-checkpoint-rejection"`.
- **Rejected** (each gives SIGNERS_INVALID with a sub-code, and aborts the validation):

| Condition | Sub-code | Checked at |
|---|---|---|
| Any option other than `namespaces` (for example `cert-authority`, `valid-after`, `valid-before`) | OPTION_POLICY | BT-2 for the owner file; VS-4 for the evidence file |
| A namespace outside that file's class, or a missing one | NAMESPACE_POLICY | Same |
| The same public key in both files | KEY_ROLE_OVERLAP | Same |
| The same principal in both files | PRINCIPAL_ROLE_OVERLAP | Same |
| More or fewer than one line in a file | LINE_POLICY | Same |
| A file's hash differs from the AK's pin | FILE_HASH | BT-5, which runs **after** the AK is approved |

- **Key rotation** is a new PC-HR-KEYS registration (PC-D-OKEY, PC-D-EKEY). It is never a second line in an existing file. For the owner key, rotation also requires a new anchor through the anchor channel (§3.1.4).

#### 3.1.3 Pipeline steps

| Step | Rule | On failure |
|---|---|---|
| VS-1 | **Role.** Role from position (§3.1.1). Key class, ns(r), principal and signers file follow from the role | UNVERIFIABLE(ROLE) |
| VS-2 | **Size and bytes.** \|b\| ≤ 4096, and every byte is 0x09, 0x0A, 0x0D or in 0x20–0x7E | UNVERIFIABLE(ENCODING) |
| VS-3 | **Signature format.** s is exactly one armored SSHSIG block, followed by one LF | UNVERIFIABLE(SIG_FORMAT). The alias BAD_SIG_FORMAT (16-B) is archive-only |
| VS-4 | **Signers file for the role's class** has passed the trust bootstrap (§3.1.4: BT-2 for owner, BT-5 and the policy of §3.1.2 for evidence) | SIGNERS_INVALID(sub-code) or the BT code: the validation aborts and nothing from it is recorded |
| VS-5 | **Signature.** The pinned `ssh-keygen -Y verify -f <signers file for class(r)> -I <registered principal for class(r)> -n ns(r) -s s`, with stdin = b and environment {`LC_ALL=C`}, exits 0 | UNVERIFIABLE(BAD_SIG) |
| VS-6 | **Strict parse** of b against schema(r): one JSON value with optional whitespace, no duplicate keys, the exact key set, integers only (no fraction or exponent) within range, strings matching their regexes, and timestamps that are real instants written `YYYY-MM-DDTHH:MM:SSZ` | UNVERIFIABLE(SCHEMA) |
| VS-7 | **Principal binding.** For attestation, seal and rejection, `signer_principal` equals the registered principal of the role's class | UNVERIFIABLE(PRINCIPAL). The alias WRONG_PRINCIPAL is archive-only |
| → | **VERIFIED.** Compute `content_sha256` = SHA-256(C(parsed)), `raw_sha256` = SHA-256(b), and `sig_sha256` = SHA-256(s) | — |
| Record point | §3.2. Only VERIFIED instances reach it | — |
| VS-8 | **Canonicality,** computed for every VERIFIED instance: b = C(parsed). It is recorded for audit, and decides acceptance **only** for the run's evidence instances (§3.2) | NONCANONICAL. This blocks acceptance only, and only as §3.2 says |
| VS-9 | Semantic rules: §3.3 step 7, §3.12, §3.12A, §3.13, §4 | Reason codes. These block acceptance only |

**VERIFIED is not the same as authorized.** For an evidence-class object, VERIFIED means only that the evidence key signed it under the right namespace. Whether that key was authorized for a given purpose is decided in §3.12A.

**Canonical bytes.** C(x) = `json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)`, ASCII-encoded, with no trailing newline (Revision 16 §5). The Python version must be pinned in the AK [E?].

#### 3.1.4 Trust bootstrap (new in 16-G)

**What is missing. This is hard blocker UR-15.**

**No independent trust anchor for the first owner key is defined or approved.** Every candidate in the earlier revisions has been checked, and none qualifies:

| Candidate | Why it cannot anchor the owner key |
|---|---|
| PC-HR-KEYS (owner-signed ledger entry registering the signers files) | It is signed by the key it would authenticate, so it is circular. It is also unapproved |
| PC-D-AKIT (owner-signed approval of the AK) | Circular for the same reason. The AK pins the owner signers file; the ledger signed by that key approves the AK |
| PC-HR-PROD | It registers the driver host, not a key |
| Revision 16's out-of-band confirmation (F9) | It confirmed head hashes, not keys. 16-A replaced it with an owner-signed attestation, which depends on the owner key itself |
| Any repository artifact | None was identified. This note does not assume one, for example signed commits, because none was checked |

**What the anchor must provide (requirements only).** The mechanism is an **open owner decision (PC-D-OKEY)**, and this note does not choose or invent it.
- It must give each validating party — the driver host for P0 validations, and every reviewer — the owner's **full public key line** (key type plus base64 blob) **and the owner principal**.
- It must arrive through a channel that does **not** depend on the owner key, the AK, the ledger, the driver host, the VM, or any object those supply.
- Each validating party must record it independently, as the `owner_anchor` field of its reviewer-state `genesis` record (§3.7), at explicit `init` or `rebuild`.
- Replacing it, including when the owner key rotates, must go through the same channel.
- It is never derived from existing reviewer state, the AK or the ledger.

**Bootstrap steps.** They run at the start of every validation (§3.3 steps 2a and 4a), and any failure aborts the validation with nothing recorded.

| Step | Check | Failure (abort) |
|---|---|---|
| BT-1 | The reviewer-state `genesis` record holds `owner_anchor` | TRUST_ANCHOR_MISSING |
| BT-2 | `allowed_signers.owner` passes the §3.1.2 policy, and its single key line and principal equal `owner_anchor` **byte for byte**. Only now is the owner signers file trusted, and it is trusted because of the anchor, not the AK | TRUST_ANCHOR_MISMATCH, or SIGNERS_INVALID(sub-code) |
| BT-3 | The approvals ledger (entries and the purpose head; see §3.12A) verifies under the anchored owner file, and its chain replays (Revision 16 §5) | The owner-class codes (§3.1.3 precedence) |
| BT-4 | The AK's SHA-256 is the subject of the PC-D-AKIT approval in force at the purpose head | AK_UNAPPROVED |
| BT-5 | The AK's pinned hashes equal the hashes of both signers files; `allowed_signers.evidence` passes the §3.1.2 policy | SIGNERS_INVALID(FILE_HASH or the policy sub-code) |
| BT-6 | The PC-HR-KEYS approval in force at the purpose head has as its subject the hashes of both signers files and both principals. Its owner part must equal the anchor | KEYS_REGISTRATION_MISMATCH (owner part). A missing registration is **not** an abort; it makes the evidence key unauthorized (BT-7) |
| BT-7 | Evidence-key authorization is decided per purpose (§3.12A), using the PC-HR-KEYS approvals in force at the heads that purpose names | EVIDENCE_SIGNERS_UNAUTHORIZED, REJECTION_UNVERIFIABLE or REJECTION_KEY_REVOKED, per §3.12A |

**Circularity rule.** An AK, a ledger and a registration that only vouch for each other are never accepted as a root. Without BT-1 and BT-2, the result is always TRUST_ANCHOR_MISSING, however consistent the rest is (BOOT-4).

**Threat-model boundary.** The validator program run by each party is trusted under PC-D-TRUST, extended to the reviewer platform. BT-4 checks that the AK is consistent with the ledger; it cannot protect against a party running a substituted validator.

**Until UR-15 is resolved, every validation aborts with TRUST_ANCHOR_MISSING.** AG-FRESH, AG-APPROVALS, AG-SEAL, AG-STATUS and AG-PRECOND cannot pass. Fixtures BOOT-1 to BOOT-12 use a **synthetic** anchor. They test the bootstrap logic; they cannot establish the real anchor.

#### 3.1.5 Reason-code precedence

Each validation emits one deterministic, ordered list. Acceptance happens if and only if the list is empty.

1. **Aborts:** TRUST_ANCHOR_MISSING, TRUST_ANCHOR_MISMATCH, KEYS_REGISTRATION_MISMATCH, AK_UNAPPROVED, STATE_UNAVAILABLE, STATE_CORRUPT, IMPORT_UNAVAILABLE, SIGNERS_INVALID (with sub-code), SNAPSHOT_INVALID. After an abort, nothing else is evaluated.
2. **UNVERIFIABLE codes for required objects,** each followed by NOT_EVALUATED(rule) for every rule whose inputs include that object.
3. **Integrity:** EQUIVOCATION, FORK, ATTESTATION_CONFLICT, SEAL_CONFLICT.
4. **Floors:** ROLLBACK, CLOCK_ROLLBACK, each flagged LATE_EVIDENCE where §3.4 says so.
5. **Canonicality and layout:** NONCANONICAL, EVIDENCE_LAYOUT_INVALID (§3.2).
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

**Ordering and evaluation:**
- Within a group, codes appear in the order listed, then by (role, seq).
- Every rule outside group 1 is evaluated, apart from those marked NOT_EVALUATED.
- UNTRUSTED_TIME (16-B) is kept as an umbrella label. It is emitted before the specific code whenever the attestation is UNVERIFIABLE, NONCANONICAL or HEAD_MISMATCH.

### 3.2 Record point: logical objects, instances, canonicality, flags and floors [P]

**Logical objects and instances (new in 16-G).**
- A **logical object** is (role, `content_sha256`).
- An **instance** is one VERIFIED representation of it: (role, `raw_sha256`, `sig_sha256`).
- Reviewer state holds:
  - one `seen` record per logical object, for its first VERIFIED instance;
  - one `instance` record for each further **distinct** VERIFIED instance of that logical object;
  - for every instance, whether VS-8 judged it canonical.
- **Repeated identical raw bytes with an identical signature** create no new record; importing them is idempotent.
- **UNVERIFIABLE instances** appear only in `import` notes.
- **Which instance a `seen` record stores affects no decision.**

**Order independence.**
- Floors, integrity flags, deduplication and equivocation are computed on **logical objects only.** After importing any set of instances, the set of logical objects, the set of instances, the floors and the flags are the same for every import order.
- Acceptance depends only on that order-independent state and on the run's own evidence instances (below). **Reordering inputs cannot change an acceptance result** (CAN-9).
- The only order-dependent results are the status transitions for rejection classification (§3.12A). They can move a run between REJECTED, INVALIDATED and UNKNOWN, **never toward acceptance or eligibility** (LIFE-12).

**Canonicality (VS-8) and the run's evidence instances.**
- **A run's evidence instances** are the exact representations listed in the run's sealed evidence manifest for its required positions:
  - the attestation;
  - each ledger entry up to h_R;
  - the head;
  - the seal.
- **Each required position must hold exactly one instance.** A duplicate or missing position gives EVIDENCE_LAYOUT_INVALID.
- **Acceptance requires every evidence instance to be canonical;** otherwise NONCANONICAL.
- **`floor_p0` objects** are checked for authenticity and content (§3.3 step 5), **not** for canonicality.
- **Instances outside the run's evidence never cure and never taint.**
  - A canonical copy found elsewhere does not cure a non-canonical evidence instance.
  - A non-canonical copy found elsewhere does not taint a canonical one.
- **Two valid signatures over identical bytes** are two instances of one logical object. They are not a conflict, and they affect no decision. Whether the pinned tool can produce such a pair is assumption E143, and CAN-7 is BLOCKED-PIN on it.

**Integrity flags.** Each is recorded once detected, blocks every acceptance and every current-validity result it covers, and is checked against **current** state under every review option.

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

**Trusted floors** come **only from VERIFIED owner-class logical objects** (entry, head, attestation):
- `seen_high` = the maximum `head_seq` over VERIFIED heads, with its `content_sha256`.
- `max_time_seen` = the maximum over VERIFIED owner-class objects of `approved_at`, `issued_at` and `t_att`. `not_after` fields never count.

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

### 3.3 Validation sequence [P]

1. **Lock.** Take the exclusive state lock, waiting at most 60 s; otherwise STATE_UNAVAILABLE.
2. **Pending recovery.** Run R-PEND (§3.7).
2a. **Trust anchor:** BT-1 and BT-2 (§3.1.4).
3. **Load state.** Re-run VS-1 to VS-7 on every persisted instance, under its recorded role and the anchored owner file (or the evidence file, after its policy check). Recompute floors and flags from logical objects. Any mismatch → STATE_CORRUPT.
4. **Import.** Take every evidence file not yet imported from the primary store and the secondary rejection store, **excluding the run under validation**. Run VS-1 to VS-7 per role, record VERIFIED instances and flags, and write durably.
4a. **Ledger, AK and key registration:** BT-3 to BT-6, at the purpose head(s) of §3.12A.
5. **Snapshot the trust floor F** (owner-class logical objects only).
   - **At P0:** F is `floor_p0`. It is sealed into the run's evidence as:
     - the raw bytes and signatures of the highest-seq VERIFIED head;
     - the VERIFIED owner-class object carrying `max_time_seen`;
     - any flagged pair;
     - the state log's record number and prefix SHA-256.
   - **At seal or review:** F is `floor_now`. The validator **must** also load `floor_p0` from the sealed evidence and re-verify each of its objects through VS-1 to VS-7, under the owner signers file recorded with the run, after checking that file against the anchor. A failure, or a mismatch with the sealed values, gives **SNAPSHOT_INVALID and aborts under every option**, including `current-floor`.
6. **Presented objects.** Run VS-1 to VS-7 on the run's presented instances — attestation, ledger, head and, at seal or review, seal and rejection objects. Record VERIFIED ones and flags, and write durably. F is not changed.
7. **Evaluate** in precedence order:
   - floors per §3.4;
   - integrity flags against current state (after step 6);
   - canonicality and layout of the run's evidence instances (§3.2);
   - time rules per §4;
   - at seal and review, the evidence rules of §3.12, §3.12A and §3.13.
8. **Write one `validation` record** holding run_id, kind (p0, seal or review), the option in force, the floor reference, the purpose heads used, the code list and the report SHA-256.
   - On PASS at seal or review, also write `accepted`.
   - If the write fails, the result is NOT ACCEPTED, and a would-be rejection follows §3.12's unrecorded path.
9. **Unlock.**

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
| Any | Review aborts on the reviewer side (BT codes, STATE_*, IMPORT_UNAVAILABLE, SIGNERS_INVALID) | Current status **UNKNOWN**. Recorded history is unchanged and no record is written |
| REJECTED or INVALIDATED | Anything | Unchanged (terminal). Later reviews are still recorded |

**How status is computed.** Status is computed by applying these transitions to the run's `validation` records, in record order. **Each validation classifies evidence using the state it holds at that moment.** Replaying the same records gives the same status, and nothing is ever deleted or rewritten.

**Supersession and revocation.**
- A newer VALID run changes an older run's status only through the option in force (§3.4).
- A revocation in a later head does not change historical status. It affects current eligibility, and the classification of rejections first seen after it (§3.12A).

### 3.6 Current eligibility for downstream use [P]

A consumer — for example the runbook §6 checkpoint step — may rely on run R for a use U only if the validator computes ELIGIBLE(R, U). All of the following must hold:

1. **Status:** R is VALID, according to a review completed after the latest import.
2. **Integrity:** no EQUIVOCATION or FORK flag in current state, and no SEAL_CONFLICT for R.
3. **Fresh attestation:** U has its own fresh owner attestation (nonce, `t_use`, bound head h_U) that passes the trust bootstrap, §3.1 and §4.
4. **Approvals still in force at h_U:** otherwise **DEPENDENCY_REVOKED_NOW**. This covers:
   - every approval R depended on;
   - the PC-HR-KEYS registration of the evidence signers file that signed R's seal (§3.12A);
   - the PC-D-AKIT approval of the AK.

   Each one's `not_after` must be ≥ `t_use` + (m − m0_use) + S at the moment of evaluation; otherwise **APPROVAL_EXPIRED_NOW**.
5. **Same tuple and AK:** R's tuple and AK equal those currently approved for U; otherwise STALE_TUPLE or STALE_AK.
6. **No rejection-role object** of any classification exists for R.

**An `accepted` record on its own is never sufficient** (ELIGIBILITY_NOT_ESTABLISHED).

### 3.7 Reviewer-state durability [P]

16-B and 16-C are retained, with these changes:

- **Record kinds:**
  - `genesis` — now includes **`owner_anchor`** (the owner public-key line and principal, entered from the anchor channel);
  - `seen` — one per logical object, for all five roles, with role, key class and canonical flag;
  - **`instance`** (new in 16-G) — one per further distinct VERIFIED instance, with its canonical flag;
  - `import`, `equivocation`, `fork`, `seal-conflict`, `recovered`, `accepted`, `validation`.
- **Write protocol:** a pending intent file → `state.log.next` → `rename` → fsync of the directory → the pending file is quarantined.
- **R-PEND:** re-verifies, then re-applies only `seen`, `instance`, `import`, `equivocation`, `fork` and `seal-conflict` records. **It never re-applies `accepted` or `validation` records.**
- **Corruption, rebuild and unavailability:**
  - STATE_CORRUPT is recoverable only through an explicit `rebuild`.
  - **`rebuild` and `init` require `owner_anchor` to be entered again from the anchor channel.** They never copy it from old state; without it they refuse with TRUST_ANCHOR_MISSING.
  - A rebuild under rotated signers files makes old-key instances of that class UNVERIFIABLE.
  - STATE_UNAVAILABLE has no fallback.
- **Location:** a local Linux filesystem (ext4, xfs or btrfs).
- **Concurrency:** validators serialize on `flock`.
- **Conflicting submissions:** these are two presentations racing; they serialize, and each is evaluated against the state the other has written (FR-CONC-1).

### 3.8 Restore execution [P]

On any failure, the run goes to the §3.12 rejection machine.

| State | Entry condition | Evidence | Typical failure codes |
|---|---|---|---|
| INIT | Driver lock held (§3.26); run_id assigned | Events-log genesis | DRIVER_LOCKED |
| PRECHECK | Trust bootstrap on the driver host (§3.1.4); validator: RGI and preconditions at the current head (§3.24), including PC-HR-KEYS for both signers files | Validator report | BT codes, REGISTRY_*, precondition codes |
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
- the reviewer state;
- the approvals ledger;
- the seal, any rejection records, and the run's sealed evidence manifest (§3.2).

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
- **E** is an evidence signers file, identified by its hash.
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
- **Rotation** means revoking a_E1 and approving a_E2 (a new PC-HR-KEYS registration), both by position in the ledger.
- **Revocation after sealing** (s_r > h_R):
  - historical status is unaffected, because the seal was authorized at h_R;
  - **current eligibility fails** (DEPENDENCY_REVOKED_NOW) for every use whose h_U ≥ s_r.
  - Whether a routine rotation should preserve the eligibility of earlier runs is an **open owner decision (PC-D-EKEY)**. The interim, which is fail-closed and not a decision, is that it does not. Any later re-endorsement mechanism is out of scope.
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

The seal is a **signed claim by the evidence key**. Neither it nor the driver's verdict nor the VM journal is ever sufficient on its own. **Prerequisite:** the trust bootstrap BT-1 to BT-6 has passed, with h_R as the purpose head (§3.1.4). Steps:

1. **Rejection first.** Classify every rejection-role object for the run, in both stores (§3.12A). Any classification stops acceptance.
2. **Seal authentication:**
   - the seal (role `seal`) is VERIFIED under `allowed_signers.evidence`, using the evidence principal and namespace `cse-checkpoint-seal`; otherwise SEAL_INVALID;
   - **historical authorization:** the evidence file that verified the seal is in force **at h_R** (§3.12A); otherwise **EVIDENCE_SIGNERS_UNAUTHORIZED**;
   - there is no SEAL_CONFLICT for the run.
3. **The seal is bound to this run:**
   - its `run_id`, `nonce`, `attestation_content_sha256`, `head_seq` and `head_content_sha256` equal the run's own VERIFIED attestation and head; otherwise SEAL_RUN_MISMATCH or SEAL_BINDING_MISMATCH;
   - its `floor_p0` hashes equal the objects re-verified in §3.3 step 5;
   - its evidence manifest lists exactly one instance per required position; otherwise EVIDENCE_LAYOUT_INVALID (§3.2).
4. **Every stream is retrieved independently** from production-side storage. For each, the chain is intact, the stream is closed, and its recomputed head equals the head in the seal; otherwise STREAM_BROKEN or STREAM_HEAD_MISMATCH. The VM journal is cross-checked against the events log and is never the only source for any fact.
5. **Recompute every required gate instance from raw evidence** (§3.24). The seal's `driver_verdict` is compared with the recomputation, never used in its place; a disagreement gives DRIVER_VERDICT_MISMATCH.
6. **Freshness and canonicality:** §3.3 at seal, including the canonicality of the run's evidence instances (§3.2).
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
- **UR-7:** the version of the `pg_dump` binary that produced the dump has not been confirmed to be recorded.

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

The bootstrap superuser must match production's. Direct mode used `cse_restore_check` ([R] `restore_check.py:71`), which would differ (UR-3).

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

**The seal** = C({seal_version, run_id, nonce, **signer_principal**, attestation_content_sha256, head_seq, head_content_sha256, floor_p0 (the objects' content and raw hashes), **evidence_manifest** (each required position with its instance's raw and signature hashes; §3.2), rgi_sha256, ak_sha256, tuple, artifact {dump_sha256, globals_sha256, manifest_sha256, ledger_row_id, provenance}, target {t0}, streams [{name, n, head_sha256}], driver_verdict {gates, final}}).
- It has role `seal`. It is signed by the **evidence key in force at h_R**, namespace `cse-checkpoint-seal`, and `signer_principal` is the evidence principal (§3.1.1).
- It is authenticated and replayed per §3.13.

**The rejection record** = C({rejection_version, run_id, nonce (or null), **signer_principal**, stage, codes, streams [{name, n, head_sha256}]}).
- It has role `rejection`. It is signed by the **evidence key in force when it is recorded**, namespace `cse-checkpoint-rejection`.
- It is classified per §3.12A.

**Not signed:** stream lines (§3.11), reviewer-state records, and the unsigned rejection marker.

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
| Trust bootstrap fails on the driver host | BT codes at PRECHECK | No run starts | — |

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
- **InForce(E, h)** ⇔ s_a(E) ≤ h ∧ ¬∃ a revocation of a_E at seq ≤ h.
- **Seal authorized for R** ⇔ InForce(E_seal, h_R).
- **Rejection authenticated for R** ⇔ ∃ h ∈ [h_R, h_latest] : InForce(E_rej, h), and InForce(E_rej, h_latest).
- **REJECTION_KEY_REVOKED** ⇔ ∃ h ∈ [h_R, h_latest] : InForce(E_rej, h), and ¬InForce(E_rej, h_latest).
- **Eligibility evidence condition** ⇔ InForce(E_seal, h_U).

---

## 5. Mandatory gate and precondition inventory

The inventory is reconstructed from Revision 16's list and the 16-A to 16-G additions. Completeness for revisions 4–15 is **unproven** until PC-D-DOCS exists and the RTM runs (UR-14). Every item is **unapproved or unpassed.**

### 5.1 Preconditions

**Owner decisions:**
- **Existing:** PC-D-TRUST, PC-D-PH, PC-D-VM, PC-D-XFER, PC-D-CON, PC-D-HV, PC-D-LEASE, PC-D-EKEY, PC-D-OKEY, PC-D-SEC, PC-D-RS, PC-D-PGVER, PC-D-PGCHK, PC-D-MGR, PC-D-RGI, PC-D-AKIT, PC-D-FRESH, PC-D-1HOST, PC-D-LOGFMT, PC-D-LOGALL, PC-D-QCAP, PC-D4, PC-D7, PC-D8, PC-D9, PC-D10, PC-VC-P1, PC-D-DOCS.
- **Added in 16-E:** PC-D-REVIEW, PC-D-SKEW, PC-D-EMAX, PC-D-BASE, PC-D-INDEP.
- **Subjects extended in 16-G:**
  - **PC-D-OKEY:** now includes the **trust-anchor channel and record** (§3.1.4; UR-15).
  - **PC-D-EKEY:** now includes **whether routine rotation preserves the eligibility of earlier runs** (§3.12A).

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
| BOOT (new in 16-G) | AG-FRESH (BOOT-1 to BOOT-6, BOOT-10 to BOOT-12) and AG-SEAL (BOOT-7 to BOOT-9) |
| LIFE (new in 16-G) | AG-SEAL and AG-STATUS |
| CAN (new in 16-G) | AG-FRESH |

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
- **No gate is added or removed in 16-G.**

**Conditions.** All of these gates are **closed**:

| Gate | Requires |
|---|---|
| AG-PINSRC | Complete pin records; every class-M behavior at SUPPORTS; every class-F behavior at SUPPORTS or INSUFFICIENT; no CONTRADICTS anywhere (precedence rules 1–4) |
| AG-QBYPASS | AG-PINSRC, plus every sandbox behavior (E116, E121, E122a, E122b, E122c, E123, E124, E125, E126a to E126e, E129, E130, E117, audit witness) FIXTURE-CONFIRMED under precedence rule 5 |
| AG-FRESH | **UR-15 resolved**; E127 and E128 at SUPPORTS and FIXTURE-CONFIRMED; the owner-class RKN rows; BOOT-1 to BOOT-6 and BOOT-10 to BOOT-12; CAN-1 to CAN-9; FB, FR and FR-HIST fixtures |
| AG-SEAL | **UR-15 resolved**; E127 at SUPPORTS and FIXTURE-CONFIRMED; the evidence-class RKN rows; BOOT-7 to BOOT-9; LIFE-1 to LIFE-14; SLV-1 to SLV-9 |
| AG-STATUS | **UR-15 resolved**; FR-HIST-1 to FR-HIST-13; LIFE-1 to LIFE-14 |
| AG-APPROVALS, AG-PRECOND | **UR-15 resolved**, plus their existing conditions |
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
| AG-FRESH, AG-SEAL, AG-STATUS, AG-APPROVALS, AG-PRECOND | UR-15 (the trust anchor, under PC-D-OKEY) |
| AG-E2E | Every fixture acceptance |
| P-PROV | PC-CC-P1PROV |
| F5 | PC-CC-VC |

---

## 6. Fixture inventory

**No fixture has been run. Every fixture below is unexecuted.**
- **BLOCKED-PIN:** the expectation must be pre-registered only after source review on the pinned tuple (§8.3).
- **Derivable:** the expectation follows from the rules in this note, using the synthetic fixture parameters of §4, a synthetic trust anchor, and the interim rules where a decision is open.

**Shorthand used in this section:**

| Shorthand | Meaning |
|---|---|
| ST_A, ST_G | Initial reviewer states (16-C). From 16-G, each also holds a synthetic `owner_anchor` matching the fixture owner key |
| Template | The template run `run-a` (16-C), with x = 0 and D = 60 s |
| PASS-A, PASS-G | PASS state changes starting from ST_A or ST_G (16-D) |
| FAIL-A, FAIL-G | P0 failure state changes starting from ST_A or ST_G (16-D) |
| FAIL-SEAL | P0 changes, then a seal failure with no `accepted` record |

### 6.1 Boundary fixtures FB-1 to FB-37 (derivable from fixture parameters)

Rows marked **✱** have results that changed in 16-E. Nothing changed in 16-F or 16-G.

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
- FR-REC-1: verified, non-canonical head 14 → +seen(head 14; its instance flagged non-canonical); `seen_high` = 14 (interim floor rule); then `h10` → {ROLLBACK}.
- FR-REC-2: unverifiable → no seen record; then PASS.
- FR-REC-3 → BAD_SIG (wrong namespace).
- FR-REC-4 → SCHEMA.
- FR-REC-5 → SIGNERS_INVALID(FILE_HASH) for `allowed_signers.owner`. The file still matches the anchor; its bytes differ from the AK pin (BT-5).
- FR-REC-7: as FB-14.
- **FR-REC-6 (state effect revised in 16-G)** (from ST_A):
  - b6 = `h10`'s content with reversed key order, re-signed with the owner key under `cse-checkpoint-approvals-head`.
  - Hashes: `raw_sha256`(b6) ≠ `raw_sha256`(`h10`), while `content_sha256`(b6) = `content_sha256`(`h10`).
  - Import → VERIFIED; same logical object as `h10`. State: +import; **+instance(b6, non-canonical)**. Floors unchanged.
  - Then presented as `run-a`'s head evidence instance → {NONCANONICAL}. State: +seen(att); `max_time_seen` = 12:00:00Z.
- **FR-REC-8:** b6 carrying `h10`'s original signature.
  - Import → UNVERIFIABLE(BAD_SIG). Only `raw_sha256` is computed. State: +import(1 unverifiable).
  - Presented → {BAD_SIG, NOT_EVALUATED(every head-dependent rule)}.

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
| FR-HIST-9 | A later head (issued 12:30:00Z) revokes `syn-3` without re-approving it; use `u-1` (`t_use` 13:00:00Z) binds that head | Historical result per option, as in FR-HIST-6 | — | **DEPENDENCY_REVOKED_NOW** |
| FR-HIST-10 | Use at `t_use` 2026-01-03T00:00:01Z, binding `h10` | Historical: VALID | — | **APPROVAL_EXPIRED_NOW** |
| FR-HIST-11 | Reviewer state corrupt | STATE_CORRUPT | Current UNKNOWN; no record written | No |
| FR-HIST-12 | A consumer presents only the `accepted` record | — | — | **ELIGIBILITY_NOT_ESTABLISHED** |
| FR-HIST-13 | The same review run twice from the same state | Identical report bytes | — | One `validation` record each |

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
- Reviewer state is ST_A, or H0 (§6.4) for seal and rejection rows, each with a synthetic owner anchor.
- Both signers files are valid and registered by PC-HR-KEYS at `h10`, with distinct keys and principals.
- `run-b` is a second sealed run.

**State-effect classes:**
- **VERIFIED owner-class:** +seen (or +instance); floors may change under the interim rule.
- **VERIFIED evidence-class:** +seen (or +instance); **no floor change**; run status changes per §3.5.
- **UNVERIFIABLE:** +import only, noting the object; no seen record; floors unchanged.
- **Abort codes:** nothing recorded.

| ID | Object and placement | Expected | State effect |
|---|---|---|---|
| RKN-1 | Entry; owner key; `cse-checkpoint-approvals`; entry position | VERIFIED | +seen(entry); floors per §3.2 |
| RKN-2 | Head; owner key; `cse-checkpoint-approvals-head`; head position | VERIFIED | +seen(head); `seen_high` per §3.2 |
| RKN-3 | Attestation; owner key; `cse-checkpoint-confirm`; `signer_principal` = owner | VERIFIED | +seen(att); `max_time_seen` per §3.2 |
| RKN-4 | Seal of `run-a`; evidence key; `cse-checkpoint-seal`; `signer_principal` = evidence; E in force at `run-a`'s head | VERIFIED; §3.13 proceeds | +seen(seal); no floor change |
| RKN-5 | Rejection of `run-b`; evidence key; `cse-checkpoint-rejection`; authenticated (§3.12A) | VERIFIED → RUN_REJECTED for `run-b` | +seen(rejection); `run-b` REJECTED or INVALIDATED; no floor change |
| RKN-6 | Head bytes signed by the **evidence** key under the head namespace; head position | UNVERIFIABLE(BAD_SIG) | +import (1 unverifiable); floors unchanged |
| RKN-7 | Seal bytes signed by the **owner** key under the seal namespace; seal position | UNVERIFIABLE(BAD_SIG) → SEAL_INVALID | No seen; `run-a` not accepted |
| RKN-8 | An entry signed by a key in neither file | UNVERIFIABLE(BAD_SIG) | +import only |
| RKN-9 | Attestation signed by the owner key, but `signer_principal` = the evidence principal | UNVERIFIABLE(PRINCIPAL) → {UNTRUSTED_TIME, PRINCIPAL, NOT_EVALUATED(…)} | +import only |
| RKN-10 | Seal signed by the evidence key, but `signer_principal` = the owner principal | UNVERIFIABLE(PRINCIPAL) → SEAL_INVALID | +import only |
| RKN-11 | The evidence key signs entry bytes under `cse-checkpoint-approvals`; entry position | UNVERIFIABLE(BAD_SIG): the owner file does not list the evidence key | +import only |
| RKN-12 | `allowed_signers.owner` also lists `cse-checkpoint-seal` | SIGNERS_INVALID(NAMESPACE_POLICY) at BT-2 | Abort; nothing recorded |
| RKN-13 | `allowed_signers.evidence` also lists `cse-checkpoint-approvals-head` | SIGNERS_INVALID(NAMESPACE_POLICY) at BT-5 | Abort |
| RKN-14 | The same public key in both files | SIGNERS_INVALID(KEY_ROLE_OVERLAP) | Abort |
| RKN-15 | The same principal string in both files | SIGNERS_INVALID(PRINCIPAL_ROLE_OVERLAP) | Abort |
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

### 6.11 BOOT: trust bootstrap fixtures (new in 16-G; unexecuted; derivable)

All use a **synthetic** anchor and synthetic keys. They test the bootstrap logic and **cannot establish the real anchor** (UR-15). "Abort" means nothing is recorded and the validation produces no result other than the code.

| ID | Setup | Expected | State effect |
|---|---|---|---|
| BOOT-1 (positive: initial trust) | `owner_anchor` = owner key K and principal. `allowed_signers.owner` lists exactly K. The ledger is signed by K, with PC-D-AKIT (subject = this AK's hash) and PC-HR-KEYS (subject = both file hashes and principals) in force at `h10` | BT-1 to BT-6 pass; the owner-class pipeline proceeds; the evidence file is registered at `h10` | None beyond the normal validation |
| BOOT-2 (missing anchor) | `genesis` has no `owner_anchor` | TRUST_ANCHOR_MISSING | Abort |
| BOOT-3 (substituted owner key) | `allowed_signers.owner` lists K′ ≠ anchor. Ledger, AK pins and PC-HR-KEYS are all rebuilt consistently around K′ | TRUST_ANCHOR_MISMATCH at BT-2 | Abort |
| BOOT-4 (circular authorization) | No anchor. The AK pins the owner file (K), and the K-signed ledger approves this AK (PC-D-AKIT) and the signers files (PC-HR-KEYS) | TRUST_ANCHOR_MISSING: a self-consistent loop is never a root | Abort |
| BOOT-5 (substituted AK) | AK′, with a different hash and otherwise valid files. The ledger approves AK, not AK′ | AK_UNAPPROVED at BT-4 | Abort |
| BOOT-6 (AK pin mismatch) | The AK is approved. The owner file matches the anchor, but its bytes (for example a trailing space) differ from the AK pin | SIGNERS_INVALID(FILE_HASH) at BT-5 | Abort |
| BOOT-7 (positive: evidence-key registration) | As BOOT-1, then RKN-4's seal | E in force at `h10`; the seal is authorized for `run-a` | +seen(seal) |
| BOOT-8 (evidence key unregistered) | As BOOT-1, but no PC-HR-KEYS approval includes the evidence file | Owner-class validation unaffected. Seals → EVIDENCE_SIGNERS_UNAUTHORIZED. Rejections → REJECTION_UNVERIFIABLE + EVIDENCE_SIGNERS_UNAUTHORIZED | Evidence objects +seen; none authorized; affected runs UNKNOWN or not accepted |
| BOOT-9 (evidence-key self-registration) | A PC-HR-KEYS-shaped entry signed by the **evidence** key | UNVERIFIABLE(BAD_SIG): the evidence key is not in the owner file. Registration absent → as BOOT-8 | +import only |
| BOOT-10 (registration contradicts anchor) | The PC-HR-KEYS subject names an owner file whose key ≠ anchor | KEYS_REGISTRATION_MISMATCH at BT-6 | Abort |
| BOOT-11 (anchor edited in state) | The `genesis` record's `owner_anchor` is altered in place | STATE_CORRUPT (the chain breaks) | Abort |
| BOOT-12 (rebuild without the anchor) | `rebuild` is run without the anchor being entered from the channel | TRUST_ANCHOR_MISSING; rebuild refuses | Nothing; the quarantine is unchanged |

### 6.12 LIFE: evidence-key authorization lifecycle fixtures (new in 16-G; unexecuted; derivable)

**Setup:**
- Synthetic ledger. `run-a` has h_R = 10 and is sealed with E1 unless stated. Every later head is issued after `run-a`'s `t_att`.
- **"Historical"** gives the status under `p0-floor`, with the interim in brackets where a later head exists. For `issued-before` and `current-floor`, see FR-HIST-6.
- **"Eligibility"** assumes a fresh use attestation binding h_U, with everything else in §3.6 satisfied.

| ID | Ledger events (seq) | Object or event | Historical | Eligibility at h_U | Codes |
|---|---|---|---|---|---|
| LIFE-1 (positive) | a_E1 at 3; nothing later | seal(E1) | VALID | h_U = 10: ELIGIBLE | — |
| LIFE-2 (boundary: s_a = h_R) | a_E1 at 10 | seal(E1) | VALID | h_U = 10: ELIGIBLE | — |
| LIFE-3 (boundary: s_a = h_R + 1) | a_E1 at 11 | seal(E1) | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-4 (boundary: s_r = h_R) | a_E1 at 3; revoked at 10 | seal(E1) | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-5 (boundary: s_r = h_R + 1; revocation after sealing) | a_E1 at 3; revoked at 11 | seal(E1) | VALID [interim: SUSPENDED, LATE_EVIDENCE from `h11`] | h_U = 11: **No** | DEPENDENCY_REVOKED_NOW |
| LIFE-6 (rotation after sealing) | a_E1 at 3; revoked at 11; a_E2 at 12 | seal(E1) | VALID [SUSPENDED] | h_U = 12: **No** (interim PC-D-EKEY policy) | DEPENDENCY_REVOKED_NOW |
| LIFE-7 (seal by the successor key) | The rotation at 11 and 12 is recorded between P0 and seal; the driver signs the seal with E2 | seal(E2) | REJECTED (at seal) | No | EVIDENCE_SIGNERS_UNAUTHORIZED, plus ROLLBACK under `current-floor` or the interim |
| LIFE-8 (rejection before revocation is observed) | a_E1 at 3; h_latest = 10 when the rejection is classified | rejection(E1) for `run-a` | INVALIDATED (terminal) | No | RUN_REJECTED |
| LIFE-9 (rejection after revocation is observed) | a_E1 at 3; revocation at 11 observed first; the rejection is first classified afterwards | rejection(E1) for `run-a` | Current UNKNOWN (not terminal) | No | REJECTION_KEY_REVOKED |
| LIFE-10 (rejection by the successor key) | Rotation at 11 and 12; E2 in force at h_latest = 12 | rejection(E2) for `run-a` | INVALIDATED | No | RUN_REJECTED |
| LIFE-11 (rejection by a key revoked before the run) | E0 approved at 2, revoked at 5 | rejection(E0) for `run-a` | Current UNKNOWN | No | REJECTION_UNVERIFIABLE, EVIDENCE_SIGNERS_UNAUTHORIZED |
| LIFE-12 (observation-order property) | LIFE-8's rejection and LIFE-9's revocation, imported in both orders | — | INVALIDATED in one order, UNKNOWN in the other. **Never VALID or ELIGIBLE in either** | No | As LIFE-8 or LIFE-9 |
| LIFE-13 (old acceptance record only) | LIFE-6's state; a consumer presents only `run-a`'s `accepted` record | — | — | **No** | ELIGIBILITY_NOT_ESTABLISHED |
| LIFE-14 (run with no head) | `run-z` failed at PRECHECK; E1 in force at h_latest | rejection(E1) for `run-z` | REJECTED | No | RUN_REJECTED (classified at h_latest only) |

### 6.13 CAN: canonicality and deduplication fixtures (new in 16-G; unexecuted)

**Setup:**
- `c10` is the canonical instance of `h10` (the original bytes and signature).
- `b6` is FR-REC-6's re-signed, non-canonical instance of the same logical object.
- Reviewer state starts from ST_G with a synthetic anchor, unless stated.

| ID | Inputs, in order | Expected | State effect |
|---|---|---|---|
| CAN-1 (canonical first) | Import `c10`, then `b6`. Then `run-a` presents `c10` as its head evidence instance | PASS (with the other template inputs) | +seen(`h10` via `c10`); +instance(`b6`, non-canonical); floors from `h10` |
| CAN-2 (non-canonical first) | Import `b6`, then `c10`. Then `run-a` presents `c10` | PASS | +seen(`h10` via `b6`); +instance(`c10`, canonical). **The same sets of logical objects, instances, floors and flags as CAN-1** |
| CAN-3 (no cure from elsewhere) | As CAN-1, but `run-a` presents `b6` as its evidence instance | {NONCANONICAL} | As CAN-1, plus the run's records |
| CAN-4 (no taint from elsewhere) | As CAN-2 (`b6` seen first), and `run-a` presents `c10` | PASS | As CAN-2 |
| CAN-5 (repeated identical raw bytes) | Import `c10` three times | No record after the first; any later decision unchanged | +seen once; +import notes for each file |
| CAN-6 (duplicate evidence position) | `run-a`'s sealed evidence manifest lists both `c10` and `b6` for the head position | {EVIDENCE_LAYOUT_INVALID} | — |
| CAN-7 (two valid signatures over identical bytes) | `c10`'s bytes with a second, different, valid owner signature | +instance (signature variant); no conflict; no decision affected. **BLOCKED-PIN on E143** | +instance |
| CAN-8 (one valid, one invalid signature over identical bytes) | `c10` with its valid signature, and the same bytes with a corrupted signature | The valid instance is VERIFIED; the other is UNVERIFIABLE(BAD_SIG); no conflict | +seen or nothing (valid); +import note (invalid) |
| CAN-9 (order-independence property) | The set {`c10`, `b6`, an unverifiable `h15`, `h12` from FR-LATE-3}, imported in all 24 orders | Identical logical objects, instances, floors and flags, and identical acceptance results for `run-a`, in every order | — |

---

## 7. Owner decisions, assumptions, unresolved items

### 7.1 Decisions

**No owner decision has been made.** Each "interim" below is a fail-closed default applied until the owner decides. It is not a choice and does not imply approval.

| ID | Exact decision needed | Interim until decided |
|---|---|---|
| **PC-D-OKEY** (extended) | (a) The owner key and principal. (b) **The trust-anchor channel and record** (§3.1.4): how each validating party independently receives the owner public-key line and principal, and how that is replaced on rotation (UR-15). (c) Recovery from integrity flags and erroneous signatures (key rotation through the anchor channel, plus explicit rebuild) | (b) **None. Every validation aborts with TRUST_ANCHOR_MISSING.** (c) Integrity flags block indefinitely |
| **PC-D-EKEY** (extended) | (a) Custody of the evidence key; the evidence principal; the rotation procedure. (b) **Whether a routine evidence-key rotation preserves the current eligibility of earlier runs** (§3.12A) | (a) No evidence-class object can be authorized. (b) It does not: DEPENDENCY_REVOKED_NOW |
| PC-HR-KEYS | Register both signers files (their hashes and the two principals) through an owner-signed ledger entry | No seal can pass §3.13 step 2 |
| **PC-D-REVIEW** | Choose `p0-floor`, `issued-before` or `current-floor` (§3.4) | Pass against both floors; LATE_EVIDENCE leads to HELD or SUSPENDED |
| **PC-D-SKEW** | Set S and J, and resolve C-a (Δ_att > 2S): lower Δ_att to ≤ 2S, raise S, or accept DELIVERY_AMBIGUOUS (not recommended) | DELIVERY_AMBIGUOUS rejects. The fixture parameters are not values |
| **PC-D-EMAX** | Whether exceeding E_max is a rejection in its own right; the value of E_max | It is a rejection |
| **PC-D-BASE** | Distribution, dated archive snapshot, and base image for the AK image and the VM | None. AG-PINSRC is blocked |
| **PC-D-INDEP** | Name the second source readers, hash recomputers, fixture reviewers and RTM reproduction operators | None |
| **PC-D-FRESH** (extended) | (a) S, W, Δ_att, Δ_start, the location of reviewer state, and the reviewer platform (local Linux). (b) **Whether verified-but-rejected owner-class objects raise floors** (§3.2). (c) **FORK coverage:** every seen head and entry, or the highest head only (§3.2) | (a) none (fixture parameters only); (b) they raise floors; (c) every seen head and entry |
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
| UR-3 | **Design defect (C-b)** | **Globals restore:** pg_dumpall's bootstrap role versus ON_ERROR_STOP. Direct mode tolerates the error ([R] `restore_check.py:75`); script mode has no defined handling |
| UR-4 | Unknown external behavior | Whether psql transmits the COPY `\.` terminator |
| UR-5 | Unknown external behavior | The exact startup parameters psql sends |
| UR-6 | Unknown external behavior | Whether `\restrict` and `--restrict-key` are supported in the pinned and the production versions. Production's `pg_dumpall` call has no `--restrict-key` ([R] `backup.py:117`) |
| UR-7 | Unknown fact (repository) | Whether the producing `pg_dump` binary version is recorded |
| UR-8 | Unknown fact (production) | Which sequence kinds exist in production |
| UR-9 | Unknown fact (repository / freeze) | Freeze records covering `backup.py` |
| UR-10 | Unverifiable assumption | Power-loss durability, which cannot be fully established without fault injection |
| UR-11 | Design gap | The copy's `pg_hba` and `pg_ident` content |
| UR-12 | Design gap / owner decision | The path and permissions for production-side durable state on the driver host |
| UR-13 | Missing evidence | Whether DATA's method (revisions ≤15) fits §3.20 and §3.22 |
| UR-14 | Missing evidence | Completeness of the gate inventory from revisions 4–15 |
| **UR-15** (new) | **Design defect, hard blocker** | **No independent trust anchor for the owner key** (§3.1.4). The channel is an owner decision (PC-D-OKEY b). Until it exists, every validation aborts with TRUST_ANCHOR_MISSING |

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
- the production `pg_dump` and `pg_dumpall` versions (UR-7);
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

Every behavior is **UNVERIFIED**, and each one blocks the gate shown. **No behavior is added in 16-G** (E143 is an assumption on which no gate depends; §7.2).

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
| E127 | SSHSIG binds the namespace; `-Y verify` exits 0 only on a full match | Inherited from 16-C | OpenSSH `PROTOCOL.sshsig`, `sshsig.c` | FR-REC-3, FR-NS-1, RKN-11, RKN-17, RKN-19 | AG-FRESH, AG-SEAL |
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

This consolidates 16-A to 16-F. **No historical identifier is renamed.**

**Prerequisites. All must hold before any real-archive scan can pass:**
1. archived revisions whose hashes match (PC-D-DOCS). **The archive for Revisions 4–15 does not exist; this blocker stands;**
2. occurrence-level collision decisions for `F5`, `C3`, `C4`, `R1`, `R2`, `P1`, `S`, `R-F4`, and any collision a scan of revisions 4–15 reveals;
3. EXAMPLE spans for every quoted fixture input in 16-A to 16-G;
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
- **Not RTM candidates:**
  - record kinds and namespaces: `validation`, `fork`, `seal-conflict`, **`instance`**, `cse-checkpoint-seal`, `cse-checkpoint-rejection`;
  - the record field `owner_anchor`;
  - file names: `allowed_signers.owner`, `allowed_signers.evidence`;
  - reason codes and sub-codes, which contain underscores;
  - the section label "3.12A" (it contains a dot).
- **Prefix check:** BT, BOOT, LIFE and CAN are not used elsewhere in the revisions available to the author. Revisions 4–15 are not available; that is part of the archive blocker, and they must be scanned for these prefixes too.

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

- The trust-bootstrap order BT-1 to BT-7 and the circularity rule. **The anchor itself is not settled (UR-15).**
- The signed-object pipeline over five roles in two key classes, with disjoint signers files.
- The three-purpose evidence-key lifecycle: historical validation at h_R, rejection classification over [h_R, h_latest], eligibility at h_U.
- The logical-object and instance model; canonicality judged only on the run's own evidence instances; order-independent acceptance.
- Authentication of seals and rejection records, and the rule that evidence-class objects never raise floors.
- The record point, integrity flags and reason-code precedence.
- `floor_p0` authentication under every option.
- The delivery and skew interval model, and the expiry horizons, expressed as formulas.
- The status machine and the separate eligibility predicate.
- The source-review precedence rules and the class M / class F register.
- The re-derived FB, FR-LATE, FR-HIST, RKN, BOOT, LIFE and CAN expectations, under fixture parameters, a synthetic anchor and the interim rules.
- The qcap identity chain and the fail-closed message set; the supported script constructs; the configuration snapshot algorithm; the checksum checks; the guard boundary statement; the reconciliation levels; the sequence contract; the validator and orchestration rules.

### 10.2 Open items by type (replaces 16-F's "irreducible contradictions")

None of these is a contradiction in the rules. Each is open for a stated reason, and **every one still blocks.**

| ID | Type | Item | Needs |
|---|---|---|---|
| **UR-15** | **Design defect: hard blocker** | No independent trust anchor for the owner key (§3.1.4) | An owner decision on the anchor channel (PC-D-OKEY b) |
| **C-b** (UR-3) | **Design defect** | Script-mode globals restore has no defined handling for pg_dumpall's bootstrap-role output under ON_ERROR_STOP. A filtered tolerance like direct mode's ([R] `restore_check.py:75`) would be a transformation that EB has not modeled | A design correction, after source review of E139 |
| **C-a** | **Parameter inconsistency needing an owner decision** | The rules are consistent, but under the fail-closed delivery model delivery is limited to 2S. The values first proposed in 16-B (Δ_att = 900 s, S = 300 s) cannot both hold | PC-D-SKEW. This note does not choose values |
| **C-c** | **Intentional policy choice pending an owner decision** | The review-order options deliberately give different statuses for the same evidence | PC-D-REVIEW |

The other unresolved items (UR-1, UR-2, UR-4 to UR-14) are typed in §7.3.

### 10.3 Unverified

- Every [E?] item (§7.2, §8.4); every behavior in class M and class F.
- Every BLOCKED-PIN expectation.
- Every fixture: none has been executed.
- Everything carried from revisions 4–15 by name.
- The trust anchor: none exists (UR-15).

### 10.4 Evidence required next, in order

1. An owner decision on the trust-anchor channel (PC-D-OKEY b), resolving UR-15.
2. The PC-D-DOCS archive, including Revisions 4–15.
3. The other owner decisions in §7.1.
4. Pin records (PC-D-BASE, PC-D-PGVER, PC-D-VM).
5. Independent source reviews (§8.2), judged under the precedence rules (§8.3).
6. A design correction for C-b (UR-3).
7. Change-control authorization for the machinery (PC-CC-CKPT-IMPL) and for P1-CC-PROV.
8. Implementation.
9. Fixture execution with independent review (§8.3).
10. The RTM with two reproductions.

### 10.5 Traceability

**Retained unchanged from 16-F:**
- all gates, preconditions, aliases and owner decisions listed in §5;
- every change-control requirement (PC-CC-CKPT-IMPL, PC-CC-P1PROV, PC-CC-VC, PC-CC-RUNBOOK6, PC-CC-RF4);
- every source and version pin requirement, and the precedence rules;
- the archive/RTM blocker for Revisions 4–15;
- all fixtures not listed as revised below;
- the threat model (PC-D-TRUST);
- the separate P1-CC-PROV;
- zero supported tuples;
- current dumps ineligible.

**Revised in 16-G:**

| Item | Revision |
|---|---|
| §3.1.2 | The AK pin and the registration no longer authenticate the owner key. FILE_HASH is checked at BT-5, after AK approval |
| §3.1.3 VS-4, VS-8 | VS-4 runs after bootstrap. VS-8 is computed per instance and decides only for the run's evidence instances |
| §3.1.4 | New: the trust bootstrap and hard blocker UR-15 |
| §3.1.5 | Bootstrap aborts, EVIDENCE_LAYOUT_INVALID and REJECTION_KEY_REVOKED added to precedence |
| §3.2 | The logical-object and instance model; canonicality rule; order independence |
| §3.3 | Steps 2a and 4a; purpose heads recorded in `validation` |
| §3.5 | Authenticated versus unauthenticated rejection rows reference §3.12A |
| §3.6 | Evidence-key registration and AK approval in force at h_U; no rejection-role object of any kind |
| §3.7 | `owner_anchor` in `genesis`; the `instance` kind; `rebuild` and `init` require a fresh anchor |
| §3.12, §3.12A, §3.13, §3.25 | The lifecycle by purpose; the seal signed by the key in force at h_R; `evidence_manifest` in the seal |
| FR-REC-6 | State effect now +instance(b6) |
| FR-REC-1, FR-REC-5 | Wording aligned with instances and BT-5 |
| §10.2 | C-a, C-b and C-c reclassified; UR-15 added |
| §7.3 | A type column for every UR item |

**Superseded but kept as identifiers:**

| Item | Superseded by |
|---|---|
| 16-F §3.12's "latest head" authorization and §3.13's "run's head" authorization, as separate unexplained rules | §3.12A (16-G) |
| 16-F §3.2's "the first `seen` record keeps its raw bytes" as the only retained representation | The instance model (16-G) |
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

**Unresolved:** UR-1 to UR-15; C-a, C-b, C-c; every decision in §7.1.

### 10.6 Final status by dimension

| Dimension | Status |
|---|---|
| Design consistency | On paper, apart from the §10.2 open items (including hard blocker UR-15) and UR-1 to UR-15 |
| Source review | None |
| Fixture execution | None |
| Independent evidence review | None |
| Owner approval | None of §7.1 |
| Repository implementation | None |
| Acceptance / freeze authorization | None |

**NOT READY. DO NOT MERGE. DO NOT EXECUTE RESTORE. R-F4 NOT ACCEPTED / NOT FROZEN. No restore is authorized.**
