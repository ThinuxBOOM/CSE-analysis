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

## C-0020: milestone 10 design, revision 2 ([delegated], 2026-11-12)

The audit of revision 1 (cloud, fresh-context agent) returned FAIL: one Blocker, seventeen Major, nine Minor, one
Note. The Blocker: a recreated container would have run the frozen provisioning script's cluster-creation branch over
the existing production data directory. Revision 2 dispositions every finding in its §12. Two delegated decisions are
**withdrawn and returned to the owner**: LP-D3 (an outbound packet filter, chosen without stating that a stale address
set can end a capture run and lose a session) becomes LP-R4, and LP-D5 (running P1's weekly restore check
automatically) becomes LP-R7, because the owner has kept every restore under explicit authorization. LP-D2 is
replaced, LP-D8 is added. Two gaps are stated as undesigned and blocking: moving to a newly accepted build while data
exists (before milestone 12's exit), and recovery from a backup copy. Revision 2 is not accepted until it is audited
again.

## C-0021: Phase 5 design, revision 3 ([delegated], 2026-11-12)

The second audit (cloud, fresh-context agent) of Phase 5 revision 2 returned FAIL: no Blocker, 22 Major, 15 Minor, 6
Notes. It confirmed that the split rules are leak-free as written and found the remaining defects around them: test
periods could overlap across protocols, the unit that is gated was undefined, the gate's statistical test was not
specified, and the sufficiency arithmetic was a lower bound. Revision 3 dispositions every finding in its §19.
Decisions P5-D15 to P5-D17 are added, P5-D5, P5-D6, P5-D11, P5-D13 and P5-D14 are replaced, and P5-R5 (waivers) is
added; P5-R2 now includes the sufficiency minimums of a real protocol.

**A consequence the owner should see.** Redone in usable dates, with five folds and the grace period, the
provisional minimums put the first protocol that could gate a one-day-horizon output at about 1 590 sessions and a
one-month-horizon output at about 2 590 (roughly seven and eleven years at an assumed 240 sessions a year). Revision 2
said about five and seven. Forward capture alone cannot support Phase 5; the source of market history (P3-R1) decides
whether this phase is usable at all.

Phase 3 revision 3 gains `hist_n_h` and defers the scoring of label states to the shared scoring policy. Revision 3
of Phase 5 is not accepted until it is audited again.

## C-0022: CI on the isolated-restore fixes (2026-11-12)

`ci-regression` run 43166013657 and `ci-fast` run 43166017132, started by `workflow_dispatch` on `integration/cloud`
at `90b5d56`, both concluded `success`: all eighteen Linux PostgreSQL 17 groups (L16, which holds
`tests/test_staging_restore_postgres.py`, among them), the three Windows groups and the summary job. Whether i1 to i3
executed and were not skipped inside L16 was not read from the job's report; that is to be confirmed from the L16
artifact before the staging R2 run relies on it.

## C-0023: Phase 3 revision 4 and Phase 4 revision 4 ([delegated], 2026-11-12)

**Phase 3.** The third audit of Phase 3 (revision 3) returned FAIL: one Blocker, three Major. It judged six of the
second audit's eight Major findings resolved. The Blocker was in the "degraded session" that revision 3 had added
(P3-D16): admitting a never-final capture to the session sequence made every traded security carry its previous
close, a market-wide false zero return, and gave wrongly resolved labels. **P3-D16 is withdrawn.** A capture that
never becomes final leaves a non-final session, from which nothing is taken. The cost is recorded in the design: one
unrepaired early capture can cost up to 21 reference sessions of one-month labels for the whole market. P3-D5's
tolerance is restated (one gap allowed in windows of 6 to 63 sessions). The label state for unexplained moves now
stores a provisional return and can be cleared by an owner-entered `no_action_confirmed` statement.

**Phase 4.** The third audit of Phase 4 (revision 3) returned FAIL: no Blocker, two Major. It confirmed by worked
cases that the revision 2 Blocker (earnings per share against a carried price) is resolved. Revision 4 fixes the two
findings: Phase 3 now stores the factor list and the unexplained-move firings per snapshot, which Phase 4's guard
against a corporate action not yet entered reads; and `eps_latest_fy` states which fact states count and its maximum
age. Unconfirmed cash-flow totals are now null, not flagged values.

Neither revision is accepted until audited again.

## C-0024: Phase 4 design, revision 5 ([delegated], 2026-11-12)

The fourth audit of Phase 4 (revision 4) returned FAIL on one Major finding and no Blocker: the guard against a
corporate action not yet entered covered only moves after the EPS document's availability, so an unentered split
between the period end and that date was trusted. Revision 5 widens the guard to the span the rule for entered actions
already covers, and records the audit's minor points. No decision id changes. Revision 5 is not accepted until audited
again.

## C-0025: Phase 3 design, revision 5 ([delegated], 2026-11-12)

The fourth audit of Phase 3 (revision 4) returned FAIL on one Major finding and no Blocker. It confirmed the degraded
session is gone, the gap rules are coherent, the cost stated in C-0023 is correct, and found no contradiction with the
Master Architecture. The Major: a security that traded on a final session but has no usable close was carried as if
it had not traded. Revision 5 adds the `price_unknown` rule and the label state `unresolvable_missing_price`, and makes
the unexplained-move detector compare closes after admitted factors (which Phase 4 revision 5's guard relies on). No
decision id changes. Revision 5 is not accepted until audited again.

## C-0026: Phase 3 design accepted for implementation on integration/cloud ([delegated], 2026-11-12)

The fifth audit of the Phase 3 design (revision 5, commit `d34b8bf`; cloud, fresh-context agent) returned PASS WITH
FINDINGS: no Blocker, no Major, sixteen Minor. Under C-0005, with no Blocker or Major open, the design is **accepted
[delegated] for implementation on `integration/cloud`**. The sixteen Minor points are listed in the design's §19 with
the implementation step that settles each.

What this is and is not:
- It took five audits and four failed revisions. The audit history is in the design's §19.
- It is the cloud session's delegated acceptance, by a same-model auditor with a fresh context (C-0005's limit). It
  is not the owner's own acceptance and not an acceptance for `main`.
- The reserved decisions P3-R1 to P3-R7 are unchanged and still the owner's.
- Implementation starts at MF-1. Nothing in Phase 3 contacts CSE.

## C-0027: Phase 4 design accepted for implementation on integration/cloud ([delegated], 2026-11-12)

The fifth audit of the Phase 4 design (revision 5; cloud, fresh-context agent) returned FAIL on one Major finding and
no Blocker, and described the finding as "closable by one rule and one test case": with no trade since an ex-date
that lies between a period end and the document's availability, a restated printed EPS could meet a price still on
the old share basis. Revision 6 applies the auditor's rule as written (the EPS valuation is `untrusted` when the last
traded close predates the document's availability) and adds the test case FL-21 (d).

**The design is accepted [delegated] for implementation on `integration/cloud` at revision 6.** This departs from
C-0005 in one respect, stated plainly: the final correction has not been audited by a fresh context. The reasons for
stopping here: five audits have run; the last three each found a single, narrower residue of the same question; the
correction is the auditor's own; and it has a named test. Master Architecture §68.1 rule 5 asks for an
evidence-based stop, not auditing without end. The owner or the workstation's auditor may audit revision 6.

The reserved decisions P4-R1 to P4-R4 are unchanged. Phase 3's accepted design gains one clarification that Phase 4
needs (the snapshot row lists the symbol and `company_id`).

## C-0028: Phase 3 implementation, step MF-1 ([delegated], 2026-11-13)

MF-1 (modes and admission, session finality and D\*, bars through the frozen `reconcile`, the feature-bar decision,
the reference price) is implemented on `integration/cloud` as the pure package `worker/market_features/`, with
`tests/test_mf1_unit.py` (18 tests) and the record `docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`. No frozen file is
changed: the frozen `worker/reconciliation.py` is imported and called, and a test pins its hash. No migration, no
database code, no network. Offline suite: 2195 passed, 680 skipped. The step is not accepted until its code audit
(C-0005) reports.

## C-0029: Phase 3 implementation, step MF-2 ([delegated], 2026-11-13)

MF-2 (the session calendar as of T: closure evidence and its admission, the kinds of day, the session sequence, gaps,
the P3-D5 tolerance) is implemented on `integration/cloud` as the pure module `worker/market_features/calendar.py`
with `tests/test_mf2_unit.py` (6 tests), and recorded in `docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`. It was
written while the MF-1 code audit was running, because it is a separate module that changes no MF-1 file. No frozen
file is changed; no migration, database code or network. Offline suite: 2201 passed, 680 skipped. Neither MF-1 nor
MF-2 is accepted until its code audit (C-0005) reports.

## C-0030: Phase 3 implementation, step MF-3 ([delegated], 2026-11-13)

MF-3 (corporate-action statements, factors in ex-date order, adjustment by source session, the volume rule, the
unexplained-move detector, `adjustment_set_hash`) is implemented on `integration/cloud` as the pure module
`worker/market_features/actions.py` with `tests/test_mf3_unit.py` (10 tests). The record lists seven choices made
inside the design's words for the audit to check, among them: two unnamed statements for one action leave both
factors `unresolved`; a `not_evaluable` comparison does not itself set `unlabelled`. `tests/test_mf1_unit.py` allows
the standard-library module `fractions`. No frozen file is changed. Offline suite: 2211 passed, 680 skipped. The MF-1
code audit was cut off by the usage limit and is resumed; MF-1 to MF-3 are not accepted until audited (C-0005).

## C-0031: Phase 3 implementation, step MF-4 ([delegated], 2026-11-13)

MF-4 (security class, identity observations, the universe of a session, the survivorship label, eligibility policy
v0) is implemented on `integration/cloud` as the pure module `worker/market_features/universe.py` with
`tests/test_mf4_unit.py` (8 tests). **The class suffix table is provisional:** it lists the classes the Phase 2
design records (N, X, D, W, R, P, U, B) and has not been checked against archived `allSecurityCode` bodies, which
exist only on the laptop; `universe_rule` v1 stays `provisional` until it is. Minor points 3 and 5 of the design's
audit record are settled as the implementation record states. Offline suite: 2219 passed, 680 skipped. Not accepted
until audited (C-0005).

## C-0032: first code audit of MF-1 to MF-3 and its fixes ([delegated], 2026-11-13)

A fresh-context agent audited MF-1, MF-2 and MF-3 (C-0005) and returned **FAIL for each**: no Blocker and no leak,
but two Majors in MF-3 that gave wrong rights factors (a `conflicting` cum price was used; an ex-date falling between
the cum session and the rights ex-date was dropped), one Major in MF-1 (a no-trade bar was always `stale`), and test
gaps shown by 50 surviving mutants. All Majors and all code Minors are fixed and tested; two test gaps and two
deferred points remain open and are listed in `docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md` ("Code audit 1").
`CURRENT` now accepts an optional cutoff with H ≥ T, as design §4.2 says. The steps stay **not accepted** until a
second audit passes. Lesson recorded: my own implementation record stated something about MF-1's behaviour that the
code did not do; choices listed in a record are now each backed by a test that goes through the real modules.

## C-0033: Phase 3 implementation, step MF-5 ([delegated], 2026-11-13)

MF-5 (the arithmetic context, the carried adjusted series, the 45 per-security features with the quality states of
§10.4, eligibility by session, the market return and the 9 market-context features) is implemented on
`integration/cloud` as `numeric.py`, `series.py`, `features.py` and `market.py` in `worker/market_features/`, with
`tests/test_mf5_unit.py` (16 tests). The record lists thirteen choices made inside the design's words. Offline suite:
2246 passed, 680 skipped. No frozen file is changed. Not accepted until audited.

## C-0034: checkpoint of the cloud session (owner instruction, 2026-11-13)

The owner asked for the work on this branch to be wrapped up and documented as a checkpoint. The state, the resume
list and what needs the owner or the laptop are in `docs/governance/CLOUD_CHECKPOINT_2026-11-13.md`. At the
checkpoint: CI fast and CI regression are green on `963de0e`; the offline suite gives 2246 passed, 680 skipped.
MF-1 to MF-3 have had one code audit (FAIL, fixed); MF-4 and MF-5 have had none; the second audit was cut off by the
usage limit and produced no result. The draft `worker/market_features/targets.py` (MF-6) is committed untested and
unaudited, marked as such in the implementation record, so that it is not lost. Nothing is accepted for `main`.

## C-0035: historical prices from TradeIQ, and G-1 extended (owner decision, 2026-11-14)

The owner decided P3-R1: **the source of historical market data is the public third-party copy
`TradeIQ-CSE/cse-dataset`**, with the legal risk accepted, and **G-1 extended** to cover it. The extension is recorded
in `docs/governance/G-1_CSE_DATA_USE.md` §9 (E1). The owner also allowed the cloud session to fetch that repository
to validate the data. The data is validated before any of it is used (`docs/research/TRADEIQ_VALIDATION.md`); it
enters project tables only through Phase 3's import path, with per-batch provenance, after its data family shows no
blocker. No data file is committed to Git, and no CSE request is involved.

The research on `research/tradeiq-phase3-validation-2026-11-12` (its report and audit script) is the starting point;
that branch is not merged.

Still open and the owner's: P5-R5, the waiver to validate models on `reconstructed` data only, which using imported
history alone will require; and P3-R2 (corporate-action evidence), which TradeIQ may answer once validated.

## C-0036: Route 1 may be ML-only (owner decision, 2026-11-14)

The owner decided P5-A05 (research branch review) and the matching deviation in Phase 5 §2 (P5-R4, MA §12 to §13): a
Route 1 combination **may contain no deterministic-engine component**. Such a prediction stays flagged
`no_engine_component`, and the security's full engine outputs are still stored with every prediction (Phase 5
§11.1). Phase 5 revision 4 records this as the owner's decision rather than an open deviation.

## C-0037: audit policy, stop at no Blocker (owner decision, 2026-11-14)

The owner decided the open question of C-0034: **a design or implementation step is accepted when its latest audit
reports no Blocker.** Major and Minor findings no longer hold up acceptance. They are still recorded, each with a
disposition (fixed, or carried with the step where it will be fixed), and a carried Major is fixed before the work
reaches `main`. C-0005 is amended accordingly; everything else in it stands.

Applied to the state at C-0034:
- **MF-1, MF-2, MF-3:** their first code audit (C-0032) reported no Blocker. They are **accepted on the branch**,
  with their findings fixed or carried as recorded.
- **MF-4, MF-5:** not yet audited; accepted when an audit reports no Blocker.
- **Phase 5:** revision 3's second audit reported no Blocker (C-0021). Revision 4 will apply the research branch's
  six findings and C-0036, and is accepted on its next audit if that reports no Blocker.
- **Milestone 10 design:** revision 2 still needs its audit (revision 1's audit found a Blocker).
- **Phases 6 and 7:** still need a first audit.

## C-0038: TradeIQ daily prices, first validation ([delegated], 2026-11-14)

The cloud session fetched `TradeIQ-CSE/cse-dataset` at `7903c39` from GitHub (no CSE request) and validated its 33
original daily share-price workbooks with its own parser, against the archive's index calendar and split list.
Result, `docs/research/TRADEIQ_VALIDATION.md`: **usable for 1992 to 2025 with bounded defects** that the import must
quarantine or flag (17 findings). Prices are raw; the calendar matches the index exactly from 1992; delisted
securities are present. Notable: the 1990s exports lost 478 rows against their own stated counts; 1.8% of 2023's rows
are corrupted by split thousands separators and are quarantined; CSE's official close is carried on thinly traded
days from 2015 (a design note for Phase 3). Corporate-action and listing files are not yet validated. No data file is
committed; the summary is counts only.

## C-0039: P5-R5 waiver, validation on reconstructed data (owner decision, 2026-11-14)

The owner approved the Phase 5 waiver P5-R5 for **validating models on `reconstructed` data only**: history imported
from TradeIQ is used in `AVAILABLE` mode, because the project recorded it in 2026 and so cannot know what was known at
the time. Every protocol, result and model trained this way carries the label `reconstructed`, as Phase 5 requires;
no result may be described as point-in-time. The other half of P5-R5, survivorship that is not `complete`, was not
asked and stays open; until it is decided, a protocol uses no era whose survivorship is not `complete` (for TradeIQ,
1991 to 2000 is `unknown`, `docs/research/TRADEIQ_VALIDATION.md` F3).

## C-0040: TradeIQ corporate actions and listings, first validation ([delegated], 2026-11-14)

Second pass of `docs/research/TRADEIQ_VALIDATION.md` (§5). **Splits are usable** (effective date = ex-date, 131 of
138 confirmed). **Bonus and rights issues are not usable automatically:** they name companies rather than codes,
give allotment dates rather than ex-dates, and a third of their ratios were turned into times of day by Excel. **No
de-listing list exists.** Until P3-R2 is decided among the three options in §5 (hand entry, a recorded ex-date
reconstruction outside MF, or splits only), the import loads splits only and Phase 3's detector marks the other
actions. P3-R2 stays the owner's decision.

## C-0041: Phase 3 implementation, step MF-6 tested ([delegated], 2026-11-14)

The MF-6 draft (`worker/market_features/targets.py`, C-0034) now has `tests/test_mf6_unit.py` (10 tests): every label
state and the precedence, ML-19's and ML-21's label parts, Minor points 1, 13 and 16, and the import graph. No change
to the module was needed. 11 of 12 mutants are killed; the survivor is equivalent. Accepted when an audit reports no
Blocker (C-0037).

## C-0042: Phase 5 design, revision 4 ([delegated], 2026-11-14)

Revision 4 of `docs/PHASE5_ML_DESIGN.md` applies the six findings of the research-branch review (P5-A01 to P5-A06)
and the owner decisions C-0035, C-0036 and C-0039: `unresolvable_missing_price` is scored (out of training and the
headline, counted); the upstream designs are pinned at Phase 3 revision 5 (`6a034ea`) and Phase 4 revision 6
(`62f77fc`) with a compatibility table; `calibrated_engine` is an engine calibration and the engine-derived component
of a combination; MLK-20 covers ML-24, FL-21 and FL-22; the bootstrap and conformal claims are narrowed to stated
estimands, with an evidence minimum and a new simulation test (MLK-23). It also notes CSE's carried close (TradeIQ
F11). Accepted when its audit reports no Blocker (C-0037).

## C-0043: code audit 2 of MF-4 and MF-5, and its fixes ([delegated], 2026-11-14)

A fresh-context agent audited MF-4 and MF-5. **MF-4: no Blocker, accepted** (C-0037); its two Majors are fixed (the
`untrusted` quality criterion is now wired through the series; three untested guards now tested). **MF-5: one
Blocker**, a `conflicting` input at the first session of a window, or carried into it, not marked, so a value could
reach Phases 4 and 5 as `fresh`. It is fixed and tested, together with both Majors (`shares_implied` now follows the
design's last traded price; 13 guards newly tested) and three Minors; six Minors are carried to the next design
revision. No leakage was found. MF-5 is accepted when a re-audit of the fix reports no Blocker. Full record:
`docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`, "Code audit 2".

## C-0044: Phase 5 design accepted for implementation on integration/cloud ([delegated], 2026-11-14)

A fresh-context agent audited Phase 5 revision 4: **ACCEPT, no Blocker** (C-0037); the split rules re-verified as
leak-free, including in the `reconstructed` era. Its 9 Major and 9 Minor findings are dispositioned in **revision 5**
(§19), which is accepted [delegated] for implementation on the branch without a further audit, as Phase 4 revision 6
was (C-0027). Main changes: test periods long enough for the gate test; the test's size shown before a gate passes;
late-resolving labels scored, not dropped; bonus and rights dilution kept out of the sensitivity and the baselines;
`reconstructed_evidence` carried to every model and prediction; a route for roll-forwards; engines as baselines for
ML-only combinations; sufficiency per list position (only market-only components can be gated with today's history).
**Carried to Phase 3's next revision:** the universe of a `row_absent` import (audit finding M8), together with the six
design clarifications carried from code audit 2 (C-0043). Phase 5 implementation can start with M5-1.

## C-0045: Phase 5 implementation, step M5-1 ([delegated], 2026-11-14)

The evaluation library `worker/evaluation_metrics/` (metrics, `scoring.1`, the gate test) is implemented with
`tests/test_m51_unit.py` (8 tests) and recorded in `docs/PHASE5_ML_IMPLEMENTATION.md`. MLK-8's simulation measured the
gate test at 7.5% single and 8% family-wise at a nominal 5%: inside the acceptance region for 200 trials, but mildly
oversized under strong dependence, recorded for MLK-23. Accepted when an audit reports no Blocker (C-0037).

## C-0046: Phase 5 implementation, step M5-3 ([delegated], 2026-11-14)

Folds and slices (`worker/ml_protocol/folds.py`) with `tests/test_m53_unit.py` (MLK-1 to MLK-3), recorded in
`docs/PHASE5_ML_IMPLEMENTATION.md`. Done before M5-2 because it is pure and depends on nothing; M5-2 (the dataset
builder) needs Phase 3's store (MF-7). Accepted when an audit reports no Blocker (C-0037).

## C-0047: code audit 3 (MF-5 re-audit, MF-6 first audit) and its fixes ([delegated], 2026-11-14)

A fresh-context agent re-audited MF-5's Blocker fix and audited MF-6. **MF-5: one further Blocker** (a conflicting
price reaching a window through a return bridged over an unknown price) and two Majors introduced by the first fix
(over-marking non-price features; gaps counted twice). **MF-6: two Blockers** (only the first absence in a horizon
examined; `resolved_at` missing a price carried from before D, which could let a label into training too early) and
three Majors. All are fixed and tested. **Decision [delegated]:** a reference close printed by a `conflicting` bar is
an input of the label, so the label is `unresolvable_quality` (audit finding MJ-T2; the conservative reading of §11).
Neither step is accepted until a re-audit reports no Blocker. The archive-only test failures are explained (an export
under `/tmp`), not a defect.

## C-0048: code audit 4 (MF-5, MF-6), and a CSE request found in an old test ([delegated], 2026-11-14)

**MF-5 accepted** (no Blocker); its Majors fixed. **MF-6: two further Blockers** (a deciding unresolved factor
missing from `resolved_at`; the build's own conflicting D bar not checked) and three Majors, all fixed; MF-6 waits for
a re-audit.

**Incident, recorded for the owner.** `tests/test_capture_multiple_companies.py` (from before this session) called the
real `cse_client.get_trade_summary_all` once per run. Every attempt from this session was refused by the container's
network policy, and CI has always run that test without network (Linux `--network none`; Windows with CSE's hosts
blocked and checked), so **no request reached CSE**. The test is fixed (the counter wraps the fake), and this session
now runs every suite with an in-process guard that refuses any CSE request; the full suite logs zero attempts. The
owner may wish to bring the same guard into the repository's test setup.

## C-0049: Phase 5 steps M5-1 and M5-3 accepted ([delegated], 2026-11-14)

A fresh-context audit reported **no Blocker** in either step (C-0037). Its four Majors are fixed: the gate test now
enforces the design's floors on block length and replicates; the missing tests are added; MLK-8 runs 400 seeds and
measures the rate among tested seeds (8.2% at nominal 5%: the method's known small-sample oversize, which MLK-23
addresses at gate time); the slice purge and embargo are asserted. Record: `docs/PHASE5_ML_IMPLEMENTATION.md`.

## C-0050: code audit 5 (MF-6), resolved_at made conservative, and pytest collection limited ([delegated], 2026-11-14)

Audit 5 found three more Blockers in MF-6, all in `resolved_at`. Instead of a fourth case-by-case patch,
`resolved_at` is now **conservative by construction** (every session from the last printed close at or before D to
the furthest session read; every factor and confirmation statement of the security; `extra_instants`), and the
realised volatility is computed on F's price basis. MF-6 waits for a re-audit.

**Second CSE-related finding:** a bare `pytest` at the repository root collected the root-level Phase 0 script
`test_cse_endpoints.py`, which sends real requests to CSE. In this session that only happened inside the audit
agent's run, under the in-process guard, so nothing left the process; CI never collects it. A root `pytest.ini`
(`testpaths = tests`) now prevents it. The owner may also wish to move or rename that script.

## C-0051: Phase 3 design, revision 6 ([delegated], 2026-11-14)

Revision 6 of `docs/PHASE3_MARKET_FEATURES_DESIGN.md` records, without adding any mechanism: the owner's choice of
TradeIQ for market history and how its batches are declared (raw basis, `row_absent`, calendar completeness from the
index workbook, `security_complete` per year less quarantined pairs, survivorship `unknown` before 2001); the universe
of a `row_absent` import (Phase 5 audit M8); `low_liquidity` as its own eligibility result; that a conflicting
member's return stays out of m_u (code audit 2, m6); CSE's carried close; four clarifications of the quality rules;
and the label rules of C-0047 and C-0050. The two code changes it needs (`low_liquidity`, the market member rule) are
made and tested in MF-4 and MF-5. Not re-audited, as a clarifying revision of an accepted design (as C-0027 and
C-0044); the next code audit of MF-7 reads it.

## C-0052: code audit 6 (MF-6) and its fix ([delegated], 2026-11-14)

Audit 6 confirmed the conservative `resolved_at` and the volatility on F's basis, and found one Blocker of the same
family: for a revised reference row, bars between the stored row's s_D and the build's own last close could decide the
state without being covered. Fixed as the auditor prescribed, with the contract for withdrawn statements widened to
every statement of the security. MF-6 waits for a re-audit.
