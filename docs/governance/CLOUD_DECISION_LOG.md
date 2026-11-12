# Cloud session decision log (integration/cloud)

**What this is.** The record of the owner's instructions to the cloud session that works on the branch
`integration/cloud` while the owner's workstation is unavailable, and of every decision that session takes on the
owner's behalf. The project's decision log lives in the owner's vault on the workstation, which the cloud session
cannot read. This file stands in for it for work done here, and is to be reconciled with the vault's log when the
workstation is back. Until then it is the authority for the branch `integration/cloud` only: it changes nothing on
`main`.

**Conventions.**
- An **owner decision** is quoted or summarised from the owner's own message, with its date.
- A **[delegated]** decision is taken by the cloud session under the owner's delegation (C-0003 below). It gives its
  reasoning, and the owner may reverse it. A reversal is a new entry; earlier entries are not edited.
- **Reserved** means the cloud session does not decide it, whatever the delegation says (C-0004).
- Entry numbers `C-nnnn` are this file's own and never reused.

Where this file and the Master Architecture (`docs/MASTER_ARCHITECTURE.md`) differ, the Master Architecture wins,
except where an entry here records an owner decision that changes it.

---

## C-0001: the integration branch (owner decision, 2026-11-11)

The owner asked for the work to continue on a fork. The session is authenticated as the account that owns the
repository, and GitHub cannot fork a repository into its own account. The owner then decided: "I approve the
integration branch."

- The branch is `integration/cloud`, created at `main`'s commit `33377421381f2f95a2ed1933ac78a7fa24ec1ad0`.
- The cloud session commits, pushes and merges there. `main` is not written by the cloud session; bringing
  `integration/cloud` into `main` is the owner's later decision.

## C-0002: the roadmap order (owner decision, 2026-11-11)

"yes, start with Phase 3 design, then 4 and 5", and "Start with designing each component that is not yet built."

- Order: Phase 3 (market feature foundation), Phase 4 (deterministic engines), Phase 5 (ML), then the remaining
  unbuilt components of Master Architecture §55 and the milestone designs that need no workstation.

## C-0003: roles, authority and delegation (owner decision, 2026-11-11)

The owner's words: "The dev workflow changed on my laptop, the orchestrator agent know it, so I run 3 agents,
orchestrator, implementor and auditor. All Claude agents, because the independent auditor was getting unreliable. So
you need to audit as well. [...] You are also authorized to write them into integration/cloud without waiting. You are
allowed to make owner decisions for me as well, anything goes as long as it doesn't contact CSE. I need to get as much
work done as possible while the laptop is away so that I don't waste time. So do everything that doesn't require the
laptop."

The owner then confirmed the cloud session's statement of what this means ("Go", 2026-11-11):

- **Roles.** The cloud session acts as orchestrator and implementer, and has each piece of work audited by a separate
  agent with a fresh context that did not write it. Findings are recorded and dispositioned before the work is called
  accepted.
- **Writing.** Designs and implementations are committed to `integration/cloud` without waiting for the owner.
- **Delegation.** The cloud session takes owner decisions, each recorded here as [delegated] with its reasoning.
- **The fixed limit.** Nothing contacts CSE, and nothing is run that would.

## C-0004: what stays reserved ([delegated], 2026-11-11; confirmed by the owner's "Go")

The delegation is broad, and the cloud session keeps these outside it. Each is either a decision whose effect is CSE
contact, an acceptance of legal risk, or something that cannot honestly be decided without the workstation's evidence.

| Reserved item | Why |
|---|---|
| HB-X2(a): extending G-1's accepted risk to bulk discovery and document retrieval | An acceptance of legal risk whose only effect is to permit CSE contact |
| Any arming decision (P3, HB-S1 to HB-S4), the contact e-mail, the pilot sample, the F6.4 designation in production | Each authorizes or shapes live CSE requests |
| ST-33, the acceptance of staging (REG-G01), and milestone 9 | They rest on Phase C's restore runs (ST-30, ST-31), which need the workstation |
| How past daily market prices are obtained, where the route involves CSE, a purchase or a third party's terms | CSE contact, money or legal terms. The cloud session designs the options and recommends |
| Merging `integration/cloud` into `main` | C-0001 |

Other limits the cloud session holds:

- **No live external service.** News sources and AI providers are built behind adapters and tested with fakes; no key
  is requested or used.
- **Frozen code.** New work is additive. A frozen file is changed only when the work cannot be done otherwise, under a
  change-control entry here that names the file, the reason and the re-staging consequence (Master Architecture §43.1:
  a changed build is staged again).
- **No real-data claim.** No production data exists. Nothing here trains, compares or promotes a model on real data,
  chooses an empirical threshold as final, or reports an accuracy figure.

## C-0005: the audit route ([delegated], 2026-11-11)

Under C-0003 the auditor is a Claude agent. In the cloud session:

- each design or implementation is audited by a separate agent started with no part of the author's context beyond
  the repository itself and a statement of the audit's scope;
- the audit reports findings by severity (Blocker, Major, Minor, Note) against the Master Architecture, the frozen
  components' contracts and the document's own claims;
- the author dispositions every finding (fixed, or rejected with a stated reason) in the document's audit record;
- a Blocker or Major finding that is not fixed keeps the item unaccepted.

**Limit, stated plainly.** The auditor and the author are the same model, so this is a second reading with a fresh
context, not independence in the sense the earlier records use the word. Records written here say "audited
(cloud, fresh-context agent)" and never "independently audited". The owner or the workstation's auditor may audit
again.

## C-0006: verification environment ([delegated], 2026-11-11)

- Offline tests run in the cloud session with `PYTHONDONTWRITEBYTECODE=1`, as CI sets it
  (`.github/ci/linux_tests.sh`). Baseline on `integration/cloud` at `3337742`, Python 3.13.18: 2176 passed, 680
  skipped, the same counts as CI's offline group at the same tree (`docs/ops/STAGING.md` §12). Without that variable
  six `tests/test_staging_unit.py` tests fail on a bytecode file the run itself writes under `ops/staging/`; that is
  an artefact of the environment, not a defect.
- The cloud session has PostgreSQL 16 client tools and no running Docker daemon. PostgreSQL 17 suites run through the
  repository's GitHub Actions workflows, started by `workflow_dispatch` on the branch. Results obtained on any other
  PostgreSQL version are labelled as such and are not acceptance evidence.
- Nothing that needs the staging containers, volumes or evidence of the workstation is attempted here.

## C-0007: Phase 3 design decisions ([delegated], 2026-11-11)

The decisions P3-D1 to P3-D9 of `docs/PHASE3_MARKET_FEATURES_DESIGN.md` §15 are taken as stated there, each with its
reason. P3-R1 (the source of market history), P3-R2 (the source of corporate-action evidence), P3-R3 (index capture)
and P3-R4 (the merge into `main`) are reserved for the owner (C-0004). The design is a design only: it adds no code,
no migration and no test, and changes no frozen file.

## C-0008: Phase 4 design decisions ([delegated], 2026-11-11)

The decisions P4-D1 to P4-D8 of `docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md` §13 are taken as stated there, each with
its reason. P4-R1 (extending the F5 concept vocabulary), P4-R2 (fixing the open findings P-18, P-23 and P-1 before the
pilot) and P4-R3 (the merge into `main`) are reserved for the owner. P4-R1 and P4-R2 are within the delegation in
principle, but each changes frozen F3/F5 code that the staged build contains, so each would force the build to be
staged again on the workstation; they are left for the owner for that reason (C-0004, "Frozen code"). The design adds
no code, no migration and no test, and changes no frozen file.

## C-0009: Phase 5 design decisions ([delegated], 2026-11-11)

The decisions P5-D1 to P5-D8 of `docs/PHASE5_ML_DESIGN.md` §14 are taken as stated there, each with its reason.
P5-R1 (promotion to production), P5-R2 (the final test period of each real protocol), P5-R3 (accepting the ML
dependency set into a deployable build) and P5-R4 (a survivorship waiver) are reserved for the owner. The design adds
no code, no migration and no test, and changes no frozen file.

## C-0010: Phase 3 design, revision 2 ([delegated], 2026-11-11)

The audit of revision 1 (cloud, fresh-context agent; C-0005) returned FAIL: one Blocker, eleven Major, nine Minor and
three Notes. Revision 2 dispositions every finding in its §19. Decisions P3-D10 to P3-D15 are added and P3-D6 is
withdrawn: the absence of sector-relative features is recorded as an unmet Master Architecture requirement with a
reserved owner decision (P3-R5), not as a design choice. P3-R4 (daily foreign-holding capture, a P2 change) is added to
the reserved list. Revision 2 also registers the fields Phase 4's audit showed that layer needs from Phase 3
(`market_cap_reported`, `shares_implied`, the return-history family, `mkt_ret_ew_63`). Revision 2 is not accepted until
it is audited again.

## C-0011: Phase 4 design, revision 2 ([delegated], 2026-11-11)

The audit of revision 1 (cloud, fresh-context agent; C-0005) returned FAIL: three Blockers, fifteen Major, seven Minor
and three Notes. Revision 2 dispositions every finding in its §17. P4-D3 is replaced (sign rule `f7.sign.2`, limited to
five gross concepts), P4-D9 to P4-D14 are added, and P4-R4 (exposing F6.1's sign-convention result through F8, a
change to frozen F8) is added to the reserved list. Revision 2 is not accepted until it is audited again.

## C-0012: Phase 5 design, revision 2 ([delegated], 2026-11-11)

The audit of revision 1 (cloud, fresh-context agent; C-0005) returned FAIL: three Blockers, fifteen Major, sixteen
Minor and six Notes. Revision 2 dispositions every finding in its §19. P5-D5 and P5-D6 are replaced (one test access
per protocol against a pre-declared criterion; sufficiency minimums in sessions), P5-D9 to P5-D14 are added, and P5-R4
(the deviation from Master Architecture §25: no sector strata until a sector source exists) is added to the reserved
list. Phase 3 revision 2 gains two clarifications that Phase 5 relies on (a row hash on label rows; the survivorship
label of a captured era). Revision 2 is not accepted until it is audited again.

## C-0013: status of the three designs (2026-11-11)

Phase 3, 4 and 5 are each at revision 2 after a failed first audit. The second audits of Phases 3 and 4 were started
and interrupted by the session's usage limit before reporting; Phase 5's second audit has not started. **None of the
three designs is accepted, and no implementation has begun.** The next actions are: finish the three second audits,
disposition their findings, and only then start implementation at the steps that depend on nothing unaccepted
(Phase 5's metrics library, M5-1, has no dependency).

## C-0014: milestone 10 design decisions ([delegated], 2026-11-11)

The decisions LP-D1 to LP-D7 of `docs/ops/LOCAL_PRODUCTION_DESIGN.md` §10 are taken as stated there. LP-R1 to LP-R6
are reserved for the owner: disk and dump retention, the drive for dump copies, keeping the workstation awake through
capture windows, the fallback if the outbound filter is unreliable, the authorization of each workstation step, and
everything that permits CSE contact. The design is revision 1 and has not been audited; it builds nothing and
authorizes nothing.

## C-0015: Phase 6 design decisions ([delegated], 2026-11-11)

The decisions P6-D1 to P6-D8 of `docs/PHASE6_NEWS_SENTIMENT_DESIGN.md` §12 are taken as stated there. P6-R1 to P6-R5
are reserved for the owner: which news sources and their governance records, what of each article is stored, the purge
procedure, Sinhala and Tamil coverage, and the merge into `main`. The design is revision 1 and has not been audited;
it adds no code and contacts no source.

## C-0016: Phase 7 design decisions ([delegated], 2026-11-11)

The decisions P7-D1 to P7-D9 of `docs/PHASE7_AI_ANALYST_DESIGN.md` §12 are taken as stated there. P7-R1 to P7-R5 are
reserved for the owner: what CSE-derived data may be sent to a hosted provider under G-1 and acceptance of that
provider's terms, which provider configurations are approved, supplying a provider key, any fallback policy, and the
merge into `main`. The design is revision 1 and has not been audited; it adds no code and calls no provider.

## C-0017: Phase 3 design, revision 3 ([delegated], 2026-11-12)

The second audit (cloud, fresh-context agent) of Phase 3 revision 2 returned FAIL: no Blocker, eight Major, ten Minor,
five Notes. It confirmed by worked cases that the revision 1 Blocker is fixed for captured data, and found the same
class of error in two residual cases (a rights issue after a split with no trade between; imported history that
repeats the last close on days without a trade). Revision 3 dispositions every finding in its §19. Decisions P3-D16
to P3-D18 are added (degraded sessions; a computed security key in place of a registry table; label protection
against late corporate actions), P3-D15 is widened, and P3-R7 (a same-day re-capture rule, a change to frozen P3) is
reserved for the owner. Revision 3 is not accepted until it is audited again.

## C-0018: Phase 4 design, revision 3 ([delegated], 2026-11-12)

The second audit (cloud, fresh-context agent) of Phase 4 revision 2 returned FAIL: one Blocker, seven Major, nine
Minor, one Note. The Blocker: restated earnings per share was divided by a raw carried price that could still be on
the pre-action share basis. Revision 3 dispositions every finding in its §17. Decisions P4-D15 to P4-D19 are added
(the currency rule; valuation on Phase 3's `adj_ref` and EPS valuation at security level; a fiscal-year EPS usable at
interim dates; cash-flow totals used only when the cash identity confirms their sign; ratios that require a positive
denominator), and P4-R4 is widened. Revision 3 is not accepted until it is audited again.

## C-0019: audit of the isolated-restore procedure, and fixes to its tool ([delegated], 2026-11-12)

The isolated-restore procedure (REG-B01) was audited by a fresh-context agent: `docs/ops/audits/AR-C-0001_ISOLATED_RESTORE.md`,
recommendation APPROVE WITH CONDITIONS. The audit found that an unknown statement in `globals.sql` was executed before
it was refused, that the line parser could be bypassed, that the proof of the globals was one-directional, and that
teardown would stop a cluster the procedure had refused.

- **Changed** (harness code, not a frozen component; no test pins these files): `ops/restore/isolated_restore.py`
  (findings 1 to 5, 11), `ops/staging/restore_driver.sh` (finding 10), `tests/test_staging_unit.py` (new test `r13`),
  `docs/ops/ISOLATED_RESTORE.md` (a dated note). Offline suite after the change: 2177 passed, 680 skipped.
- **Not verified here:** `tests/test_staging_restore_postgres.py` i1 to i3 need PostgreSQL 17 and were not run in the
  cloud session. They must be shown executed on PostgreSQL 17 (CI group L16, or the staging runner) before the
  staging R2 run.
- **Consequence for Phase C.** The staging driver injects its harness files from the working tree, so these fixes
  reach an R2 run when the workstation uses a checkout that contains them; the accepted image is not rebuilt for
  them.
- **Reserved for the owner:** approval of the procedure (REG-B01), the runbook §6 change (REG-B27), and findings 6 to
  8, which block any production checkpoint and need a production form of the procedure.
