# Research branch checkpoint — TradeIQ archive / Phase 3 validation

**Checkpoint date:** 2026-11-13 (Asia/Colombo)  
**Repository:** `ThinuxBOOM/CSE-analysis`  
**Branch:** `research/tradeiq-phase3-validation-2026-11-12`  
**Comparison base:** `main` at `33377421381f2f95a2ed1933ac78a7fa24ec1ad0`  
**Checkpoint status:** Research and validation work documented; not merged; not accepted for canonical-data use.

> This is a handover/checkpoint record, not a declaration that the dataset has passed Phase 3 acceptance. It distinguishes completed repository work, reported CI evidence, unresolved audit results, and work that has not been done.

## 1. Executive summary

This branch isolates two related but distinct research threads:

1. **TradeIQ historical archive assessment:** determine whether the public `TradeIQ-CSE/cse-dataset` archive is a useful zero-cost candidate source for historical CSE market data, and independently assess its structure and conversions.
2. **Phase 5 design review record:** preserve six review findings discovered while comparing the Phase 5 ML design on `integration/cloud` with its upstream design dependencies and the Master Architecture.

The branch contains research documentation, an archive-audit script, and CI workflow changes. It does not implement a production importer, change canonical schemas, ingest rows into the production database, or authorize CSE requests. The Phase 5 audit is a separate documentation artifact; it does not change the Phase 5 design.

At the time of this checkpoint, GitHub comparison reports the branch as **9 commits ahead and 0 commits behind `main`**, with **five changed paths** relative to the recorded base. The branch is intentionally not merged.

## 2. Branch scope and changed files

Comparison:
- Base: `main` — `33377421381f2f95a2ed1933ac78a7fa24ec1ad0`
- Head ref: `research/tradeiq-phase3-validation-2026-11-12`
- Ahead / behind: **9 / 0**
- Changed paths: **5**

| Path | Change | Purpose |
|---|---:|---|
| `.github/workflows/ci-fast.yml` | Modified, +1 / -1 | Small CI workflow adjustment associated with the research branch work. |
| `.github/workflows/ci-regression.yml` | Modified, +43 / -1 | Adds the research-branch-only TradeIQ structural-audit job; the job fetches the public TradeIQ GitHub archive, runs the audit script, and uploads reports only. |
| `docs/research/TRADEIQ_CSE_DATA_RESEARCH_2026-11-12.md` | Added, +321 | Research inventory, governance constraints, source assessment, progress notes, validation plan, and unresolved data-quality questions. |
| `scripts/research/audit_tradeiq_archive.py` | Added, +157 | Reproducible structural inventory and independent parsing/reconciliation tool for the public archive. |
| `docs/research/PHASE5_ML_DESIGN_AUDIT_2026-11-12.md` | Added, +169 | Separate record of six Phase 5 ML design review findings. |

No source design document for Phase 5, Phase 3, Phase 4, or the Master Architecture is changed by this branch's Phase 5 audit record. The Phase 5 design remains on `integration/cloud`.

### Relevant recorded commits

- Baseline `main`: `33377421381f2f95a2ed1933ac78a7fa24ec1ad0`.
- Phase 5 audit report commit: `3bfb8d0a287370806a751c4e5668259145031667` — `docs: record Phase 5 ML design review findings`.
- TradeIQ reconciliation-script revision previously identified: `ddd2edff59b9ee74a26e56272c47421833c6d059` — `research: reconcile manifest counts against source worksheet rows`.

This list identifies known relevant commits; it is not intended to replace GitHub's complete branch commit history.

## 3. TradeIQ source and research scope

External candidate source: [TradeIQ-CSE/cse-dataset](https://github.com/TradeIQ-CSE/cse-dataset).

The public repository contains original historical archive directories, converted CSV outputs, a conversion manifest, a historical-data report, and other market-wide data families. The archive is a candidate because it exposes both source workbooks and converted material; the presence of converted data alone would not be enough to establish provenance or correctness.

The research document records:
- Daily share-price archive groupings spanning 1991–2025.
- Daily indices, total-return indices, market ratios, market statistics, market capitalisation, quoted-security lists, and corporate-action categories.
- Schema variation between years/workbooks, empty sheets, ambiguous generated column names, date spillover, and incomplete security-identity evidence.
- The need to distinguish source-native/raw prices from adjusted series.
- The need to verify historical listing/delisting populations and point-in-time corporate-action semantics.

The TradeIQ repository's own source notes describe official historical archive coverage as ending on 2025-12-31. This is a source claim, not proof that every security and trading session is complete.

## 4. What the archive audit actually established

The CI audit job clones the public TradeIQ repository from GitHub (not CSE), records the cloned repository commit, installs workbook readers, and runs `scripts/research/audit_tradeiq_archive.py`. It uploads report artifacts, not the source archive.

The latest recorded audit observations available for this checkpoint are:

### 4.1 Structural inventory

- 1,040 files inventoried.
- Approximately 355.5 MB of archive material.
- 621 CSV files.
- 77 Excel workbooks.
- 593 manifest entries.
- Zero parsing errors reported by the structural parser.

These counts describe the audited archive snapshot and parser run; they do not establish source authenticity, licensing, or historical completeness.

### 4.2 Manifest/source/converted-row reconciliation

The initial comparison identified four notable manifest-count differences:
- Monthly statistics: manifest 31 versus 29 parsed non-empty records.
- 2012 daily: manifest 62,209 versus 62,208.
- 2016 High/Low: manifest 55,159 versus 54,851.
- 2025 High/Low: manifest 67,846 versus 67,543.

The later reconciliation revision compared non-empty source worksheet rows with non-empty converted CSV rows for these four cases. It found:
- Monthly statistics: 29 versus 29.
- 2012 daily: 62,208 versus 62,208.
- 2016 High/Low: 54,851 versus 54,851.
- 2025 High/Low: 67,543 versus 67,543.

This shows that the source and converted non-empty row counts match in those four cases. It does **not** prove that the manifest's physical row counts are correct, nor does it prove row-by-row semantic equivalence. The manifest discrepancies therefore should not be casually described as fully resolved.

### 4.3 Remaining reconciliation concerns

The later audit report also exposed:
- 550 unresolved row-count entries.
- Six source-versus-converted non-empty row-count differences of one row each, associated with 2002, 2006, 2007, 2008, 2009, and 2010 daily files.
- Two entries without a usable source worksheet profile.
- A negative aggregate metric, `manifest_count_differences_explained = -546`, indicating that the aggregate accounting/definition needs investigation before it is relied upon.

These findings remain open. Zero parser errors does not mean zero data-quality issues. A row-count match is not proof of correct values, dates, symbols, or semantics. The next audit iteration should clarify the metric definitions, inspect the six one-row differences, explain the 550 unresolved entries, and reconcile the two missing worksheet profiles.

### 4.4 What the archive audit does not establish

It does not establish:
- Complete trading-session coverage or an authoritative trading calendar.
- Complete historical security universe, including delisted securities.
- Correct permanent security identity across symbol changes, share classes, mergers, or relistings.
- Source-date authenticity for every row.
- Correct raw/adjusted price basis.
- Complete or correctly timed corporate actions.
- Correct zero-volume/no-trade semantics.
- Rights to store, use, or redistribute the underlying CSE data.
- Admission to canonical project tables or satisfaction of Phase 3 acceptance gates.

The script is a structural/reconciliation audit, not a full financial-data correctness or acceptance engine.

## 5. Project-side quality and architecture boundary

The existing project data-quality approach was reviewed at a static/documentary level in the associated work. The known validation controls include checks such as:
- OHLC consistency (including high/low and close-range constraints where applicable).
- Positive-price constraints for relevant price fields.
- Non-negative turnover, share volume, and trade-count constraints.
- Detection of extreme daily moves (default threshold previously noted as greater than 30%).

The project architecture also requires provenance, append-only raw observations, stable security identity, preservation of delisted securities, separate corporate-action handling, and point-in-time safety.

Important limits of that static review:
- It was not a fresh execution of the full quality-gate suite.
- Passing these row-level plausibility checks would not prove source-date authenticity, calendar completeness, historical-universe completeness, corporate-action completeness, or raw/adjusted-basis correctness.
- The raw market row's `post_open_price` must not be misrepresented as the actual session `open`.
- No independent local full-archive download/parse or production ingestion was completed in this work.
- No claim is made that the TradeIQ archive has passed the project's complete Phase 3 acceptance matrix.

## 6. Phase 5 ML design review findings

The separate report `docs/research/PHASE5_ML_DESIGN_AUDIT_2026-11-12.md` records six findings against the Phase 5 ML design revision 3 on `integration/cloud`.

Reviewed references recorded in that report:
- Phase 5 design revision 3, blob `c9cdef0b562a4431e0283bded706437dfd0a2639`.
- Phase 3 design revision 5, blob `a03336459f29d9c521c0280a6f1a6d25a1e37221`.
- Phase 4 design revision 6, blob `531b9447aa915a6e050e320313661737040644b2`.
- Master Architecture on `integration/cloud`, blob `bf95fa8aedf5ed9529109f09f23f8ca1bd9de9d4`.

| Finding | Severity / disposition | Summary |
|---|---|---|
| P5-A01 | Major | Phase 5 scoring policy omits Phase 3's `unresolvable_missing_price` label state. |
| P5-A02 | Major | `calibrated_engine` consumes a Phase 4 engine score while the v1 adapter rule says ML adapters take MF/F7 features, not engine scores. |
| P5-A03 | Major | Phase 5 governing references point to older Phase 3/4 revisions; every operative dependency reference needs reconciliation, not just a version-number update. |
| P5-A04 | Major | The inherited leakage-test mapping omits Phase 3 ML-24 and Phase 4 FL-21/FL-22. |
| P5-A05 | Blocker to design acceptance; owner decision required | Route 1 may produce a combination with no deterministic-engine component, despite the Master Architecture defining Route 1 as deterministic engines plus ML. |
| P5-A06 | Major review item; not yet established as a defect | Stationary-bootstrap and split-conformal assumptions need validation under temporal and cross-sectional dependence in financial data. |

### Required disposition

1. **P5-A05:** Owner must decide whether canonical Route 1 strictly requires an eligible deterministic-engine component, or whether an ML-only fallback is allowed as a separately named and governed mode. This report makes no choice.
2. **P5-A01:** Define explicit training/metric/exclusion accounting for `unresolvable_missing_price`; add tests so it cannot silently enter training or headline metrics.
3. **P5-A02:** Define a coherent adapter taxonomy and input contract for `calibrated_engine`.
4. **P5-A03/P5-A04:** Reconcile all Phase 3/4 dependencies and inherited tests, including ML-24, FL-21, and FL-22.
5. **P5-A06:** Define the estimands and coverage claims, validate simulation coverage/type-I error under realistic dependence and sample sizes, and narrow claims where evidence is insufficient.

The Phase 5 report is a repository review record, not a fresh-context formal audit, acceptance, owner decision, or implementation authorization. The Phase 5 design remains design-only and unaccepted. Nothing in this branch authorizes Phase 5 implementation.

## 7. CI and automation record

The regression workflow includes a dedicated job named **TradeIQ archive structural audit (research branch only)**, gated to the exact research branch ref. It:
1. Checks out the branch commit.
2. Clones the public TradeIQ dataset repository from GitHub.
3. Records the TradeIQ clone commit.
4. Installs pinned workbook readers.
5. Runs the archive audit script.
6. Uploads the generated audit report as a workflow artifact; it does not commit raw archive data.

The workflow's research job is structurally separate from CSE live capture. It does not send requests to CSE.

Previously reported run references include:
- Regression run: `43251178118`.
- Fast workflow run: `43251178130`.
- Earlier related runs: `43242465275`, `43242334787`, `43242320122`, `43242465043`, and `43242334972`.

A prior session reported 23/23 regression jobs green and an offline suite green for the then-tested revision. However, the workflow-run/status endpoint did not return run records when rechecked against the previously noted commit during checkpoint preparation. Therefore this checkpoint does **not** assert that the current branch head has a fresh, complete green CI result. Verify the exact head SHA and its current workflow runs before treating CI as accepted evidence. The archive audit's unresolved data findings remain unresolved regardless of CI job success.

## 8. Governance, constraints, and explicit non-actions

The following constraints remain in force:

- **Budget:** LKR 0. No paid feed, archive, subscription, or API.
- **CSE permission/licensing:** No written CSE permission or market-data licence has been obtained. Public accessibility of a repository or endpoint is not proof of rights to retain or redistribute underlying data.
- **CSE request behavior:** This work did not make CSE requests. Any future CSE requests remain subject to the existing owner decision and G-1 controls: sparse sequential requests with at least 1.5 seconds between consecutive requests, backoff on errors/rate limits, no proxies/IP rotation/block circumvention, and immediate stop if CSE requests cessation.
- **Raw data:** Do not commit raw CSE responses or private CSE payloads.
- **Canonical data:** Do not promote TradeIQ or community datasets to canonical tables without provenance, permission/licensing, historical integrity, and applicable acceptance gates.
- **Production:** No production DB writes, schema migration, scheduler activation, deployment, or live ingestion was performed or authorized.
- **Branching:** Keep work isolated on this research branch. Do not merge to `main` without explicit owner authorization.
- **Phase 5:** Do not modify `integration/cloud` Phase 5/3/4 design documents as part of closing this research branch. Any Phase 5 correction needs its own scoped review and owner decisions.

## 9. Open work and recommended continuation order

### A. Finish the archive audit interpretation
- Define precisely what each manifest/source/converted row-count metric counts (physical rows, non-empty rows, data rows excluding headers, or another unit).
- Explain the negative `manifest_count_differences_explained` aggregate; correct the metric if its sign or denominator is wrong.
- Investigate the six one-row source/converted differences from 2002, 2006, 2007, 2008, 2009, and 2010.
- Investigate the two entries without usable source worksheet profiles.
- Explain the 550 unresolved row-count entries and distinguish expected blank/padded rows from unexplained discrepancies.
- Compare actual records, not only counts, for every material discrepancy.
- Preserve per-file evidence, hashes, parser versions, and reproducible outputs.

### B. Move from structural checks to data-quality checks
- Independently inspect original workbooks and converted CSV values.
- Validate date fields, symbol fields, column meaning, units, duplicates, OHLC invariants, and source-date authenticity.
- Compare date coverage with an authoritative or independently verified trading calendar.
- Reconcile security identity and historical listed/delisted populations.
- Assess corporate-action completeness and point-in-time availability.
- Establish raw versus adjusted basis from evidence, not assumptions.

### C. Phase 3 acceptance mapping
- Map each applicable Phase 3 requirement to an executable test and evidence artifact.
- Report each gate as PASS, FAIL, BLOCKED, or NOT TESTED.
- Separate suitability as a test fixture from eligibility as canonical market data.
- Do not claim acceptance while any required gate is untested or blocked.

### D. Phase 5 review follow-up (separate workstream)
- Obtain the owner's P5-A05 Route 1 policy decision before editing the contract.
- Prepare a precise change proposal for P5-A01 through P5-A04.
- Have the statistical assumptions in P5-A06 evaluated with explicit estimands and simulations.
- Keep this separate from TradeIQ data validation; do not let branch closure imply Phase 5 acceptance.

### E. Closeout conditions for this branch
This branch can be considered administratively ready for owner review once the checkpoint and supporting artifacts are committed and the exact branch state is verified. It is **not** ready for data acceptance or production use. A future merge decision must separately review:
- The two CI workflow changes.
- The audit script and its dependency/security implications.
- The research and Phase 5 audit documents.
- The remaining data reconciliation findings.
- Data rights and provenance.

## 10. Key artifacts and navigation

- [TradeIQ research report](TRADEIQ_CSE_DATA_RESEARCH_2026-11-12.md)
- [Phase 5 ML design audit findings](PHASE5_ML_DESIGN_AUDIT_2026-11-12.md)
- [Archive audit script](../../scripts/research/audit_tradeiq_archive.py)
- [Regression workflow](../../.github/workflows/ci-regression.yml)
- [Fast workflow](../../.github/workflows/ci-fast.yml)
- [TradeIQ candidate source repository](https://github.com/TradeIQ-CSE/cse-dataset)

## 11. Final checkpoint statement

This branch preserves the research performed and the issues discovered without promoting uncertain data or design assumptions into accepted project behavior. The structural audit is useful but incomplete; row-count reconciliation still has unresolved cases; data rights and canonical admission remain blocked; and the six Phase 5 design findings remain open review items.

**Safe handoff state:** continue investigation on this branch, keep the evidence reproducible, do not weaken project gates, do not contact CSE or write production data without the required authorization, and do not merge to `main` without an explicit owner decision.
