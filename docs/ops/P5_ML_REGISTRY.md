# Runbook: the ML registry (Phase 5, M5-6)

Design: `docs/PHASE5_ML_DESIGN.md` §5.4, §5.5 and §9. Implementation record: `docs/PHASE5_ML_IMPLEMENTATION.md`.
Storage: pending migration `supabase/migrations_pending/0021_ml_registry.sql` (applied after 0019 and 0020; moving
it into the chain is part of the owner's merge decision).

Nothing here contacts CSE or any network. The registry reads and writes only PostgreSQL. Training, validation and
the one test access are done by the research code through `worker/ml_registry/store.py`; this command line is for
inspection and for the owner's decisions.

## Who runs what

| Command | OS user | PostgreSQL login |
|---|---|---|
| `preflight`, `status`, `verify`, `alerts`, `transition` | `cse-worker` | `cse_worker` (peer) |
| `retire`, `validate-policy`, `waive`, `flag`, `promote` (owner decisions) | `cse-migrator` | `cse_migrator`, acting as `cse_owner` |

All through `sudo bash /opt/cse/app/ops/bin/cse-ml <command>`. Exit status: 0 ok, 4 PostgreSQL unavailable,
5 refused or failed. Owner decisions record the operator (the human who ran `sudo`) in their note.

## Commands

- `preflight`: the worker role's security preflight (also run before `transition`): insert and select on the
  research tables, select only on the owner tables, no update, delete or truncate anywhere, every guard trigger
  present and enabled.
- `status`: row counts, every model with its latest status, the unspent test periods, the lifetime access count.
- `verify`: re-prove every artifact's SHA-256 and size, every configuration id and every protocol's declaration
  hash in Python; report models whose artifact is missing. Read-only.
- `alerts`: production models whose source version was later rejected. They are reported, never retired
  automatically (§9.2); retiring one is a decision.
- `transition --model ID --to rejected|retired --reason TEXT`: a research decision on a research model. `validated`
  comes only from the gates (research code); `production` only from `promote`.
- `retire --model ID --reason TEXT`: the owner retires a production model (§9.2; the database refuses it from the
  worker).
- `validate-policy --body JSON --evidence-cutoff TS`: the owner validates a policy (an eligibility policy, a
  tolerance, an effect size, a domain margin). It prints the policy's content-addressed id, which a protocol lists
  under `policies`; gate 6 reads each listed policy's status from the registry, and a policy whose evidence cutoff is
  not before the protocol's first V_start counts as a configuration tried (§5.4, MLK-6). Only the owner can record a
  validated configuration (0021).
- `waive --kind survivorship|mode|reconstructed_evidence --covers JSON --note TEXT`: the owner's waiver. `--covers`
  is a JSON object naming what it covers (dataset versions, eras, protocols). Gate 7 accepts a survivorship waiver;
  gate 11 a `reconstructed_evidence` waiver.
- `flag --from TS --to TS --note TEXT`: the owner's incident flag over an interval. It blocks only predictions with
  T at or after the flag's recording time.
- `promote --model ID --purpose TEXT --list ID[,ID...] --evidence FILE --note TEXT --code-version TEXT`: the owner's
  promotion of a validated research model (gates 9 to 12). The registry supplies whether the protocol's one access
  named this model and whether it passed, the declared point-in-time share, every label the model carries and the
  lifetime access count. `FILE` is a JSON object stating only what the registry cannot know:
  `backtest_valid`, `backtest_ends_at_last_v_end_or_post_access`, `point_in_time_share` and, optionally,
  `reconstructed_waiver` (an id from `waive --kind reconstructed_evidence`). On a pass, one transaction writes the
  report, the production copy (same artifact), the designation (every label and the access count) and its first
  event; on a fail, only the report. Exit 5 when refused.

There is no delete, reset or repair command: every registry table is append-only for every role.

## Known limits

- The laboratory backtest is Phase 10's; until it exists, gate 10 cannot honestly pass, so no model should be
  promoted.
- MLK-23 (the gate test's size at a protocol's own dependence) runs at M5-8; until then gates 5 and 9 report
  `size_not_shown`.
- The registry binds the sanctioned code path. Prices and labels for a test period are readable in the database; no
  software rule stops a person from looking (§5.4, "the limit, stated plainly").
