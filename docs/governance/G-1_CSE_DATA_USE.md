# G-1: CSE data use — owner decision record

| Field | Value |
|---|---|
| Item | G-1: CSE governance (automated capture and storage of cse.lk data) |
| **Status** | **`accepted_risk`** |
| Decided by | Project owner |
| Decision date | 2026-09-28 |
| Recorded at | P1 frozen at `43f6b85f25683517f7d8cda7636f2f7fe1c568c2` |
| Supersedes | G-1 as an unresolved blocker for P3/live capture |

> **This is NOT `resolved`, `cleared`, `authorized`, or `approved_by_CSE`.**
> No written CSE permission and no CSE market-data licence has been obtained. Nothing in this project may state or
> imply that CSE has permitted, licensed or endorsed it. This record is an **owner governance decision, not a legal
> conclusion**.

## 1. Decision

The owner has chosen to accept G-1 as a known legal/compliance risk ("Option 2") rather than wait for written
permission from CSE. P2/P3 may proceed, **subject to the scope in §3 and the mandatory controls in §4**.

## 2. Basis

- The owner has reviewed CSE's published website disclaimer/terms (https://www.cse.lk/disclaimer, read 2026-09-28).
  They limit use to viewing and printing one copy for personal, non-commercial use. They also prohibit storing the
  contents "in an electronic retrieval system" without CSE's prior written permission, and they prohibit caching.
- The owner understands that **permanent private storage of CSE market-data responses may conflict with that
  published storage restriction**.
- **No written CSE permission has been obtained.**
- **No CSE market-data licence has been obtained.** CSE sells Market Data Subscriptions and Data Library products;
  none has been purchased.
- The owner is **knowingly accepting this risk** for the project.

## 3. Scope of the accepted risk

The decision covers **only**:
- personal use;
- non-commercial use;
- private, local analysis (the owner's own server and database);
- no redistribution or publication of raw CSE market data;
- no commercial resale and no public market-data service.

Any use outside this scope is **not** covered, and G-1 must be reviewed first (§5).

## 4. Mandatory operating controls (project requirements)

These are requirements for every component that contacts CSE or holds CSE data (P2 onwards). A design or
implementation that cannot meet them must not be deployed.

1. **Sparse, sequential polling.** Send one request at a time, with no parallel or burst requests, and only the
   minimum request set the capture design needs.
2. **At least 1.5 seconds between consecutive CSE requests**, wherever more than one request is made.
3. **Back off on errors and rate limiting** (HTTP 429/5xx, timeouts), with increasing delays. Never retry tightly.
4. **An identifiable User-Agent that includes a contact email.** The address is configured on the server and kept
   out of the repository.
5. **Never bypass CSE access controls** (authentication, tokens, blocks, CAPTCHAs, or any other restriction).
6. **No proxies, no IP rotation, no block circumvention.** If CSE blocks access, capture stops and alerts; it
   does not work around the block.
7. **No redistribution of raw CSE responses**, in whole or in part, to any third party or service.
8. **No raw CSE responses committed to Git.** Raw responses live only in the server's PostgreSQL archive, the local
   spool and encrypted backups.
9. **No CSE branding, and no implication of CSE endorsement**, in any output, name or interface.
10. **Stop automated capture if CSE explicitly requests cessation.**
11. **Deletion requests go through a deliberate, owner-controlled purge process.** Normal append-only protections are
    not weakened to delete data (see §6).
12. **Review G-1 before continuing automated capture if the project becomes commercial or public.**

## 5. Escalation

- Written CSE permission or licensing **may still be sought later**. If it is granted, this record is updated and the
  status changes. Until then the status stays `accepted_risk`.
- Automated CSE capture **must stop pending owner review** if:
  - CSE objects, blocks access, or requests cessation or deletion;
  - CSE's published terms change materially;
  - the project's use changes materially (redistribution, publication, commercial use, a public service, or
    publishing market predictions or recommendations).
- If permission is ever requested and refused, the refusal overrides this decision. Capture stops.

## 6. Architectural requirement: purge without weakening protections

P1's protections stay exactly as they are. This decision does **not** weaken:
- the append-only triggers (0007, 0010, `ops.reject_mutation`);
- backup immutability (read-only dumps, no `restic forget`/`prune` from the server, append-only off-site
  destination);
- source-observation retention;
- migration protections (hash ledger, owner-only DDL, FULL gate).

**P2 must design (not P1, and not this task) a controlled, owner-authorised purge/retention procedure.** It must
cover:
- active database data: archived responses, raw observations and anything derived from CSE content;
- spool copies (content-addressed, write-once files);
- backups: local dumps that contain the data, and encrypted off-site snapshots. This includes the fact that an
  append-only destination is designed to resist deletion, and the procedure must be documented honestly.

The procedure must be deliberate (owner-only, never available to the worker or backup roles), recorded, verifiable,
and separate from normal operation. It must not be implemented by relaxing the normal protections.

## 7. Investigation findings (2026-09-28, retained for the record)

This summarises the research-only investigation the owner reviewed before deciding. It is not legal advice.

| Claim considered | Finding |
|---|---|
| robots.txt is not legally binding | Generally true, but irrelevant. CSE's robots.txt allows everything except `/cgi-bin/`, and the restriction comes from the terms of use. |
| Other CSE scrapers exist and are legal | 7+ unofficial scrapers/clients exist. None states CSE permission, and no takedowns were found. This suggests tolerance and low enforcement risk, not legality. |
| CSE is building a free public API | Unverified. No announcement was found; CSE's official offer is paid data subscriptions. |
| The project is within CSE's legal framework | Not supported: the storage clause requires prior written permission. |

Risk by activity:
- automated polling: low;
- permanent raw storage: the core conflict with the terms;
- temporary PDF extraction (PDFs deleted after extraction): low;
- redistribution or publication: prohibited without permission.

Sri Lankan law (IP Act No. 36 of 2003, Computer Crime Act No. 24 of 2007) was reviewed briefly. The realistic
consequences are an IP block, a cessation/deletion request, or (unlikely for private non-commercial use) legal
action.

## 8. Change log

| Date | Change |
|---|---|
| 2026-09-27 | G-1 raised as a blocker for P3/live capture (P0.5) |
| 2026-09-28 | Investigation completed; owner decision recorded: `accepted_risk` |
