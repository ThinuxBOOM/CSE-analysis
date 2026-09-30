"""
Choosing which already-computed objects to reconcile (docs/F6.4_DESIGN.md sections 6.5, 9.3, 11.4; rule f6.store.1).

M4  the canonical validation run of an F5 run under a version set is the one whose input set equals the CURRENT
    input set: the latest filing_issuer_links decision (highest id) and report_filings.uploaded_at as committed now
    (view financial_validation_run_current). There is never a fallback to a run with other inputs.
9.3 reconciliation is partitioned by issuer (the identity includes issuer_id);
11.4 a partition's fingerprint is digest({configuration_id, issuer_id, selection, [[so_key, output_hash] ...]}).

This is not F8 supersession: it chooses among our own validations of one F5 run, never between documents.
"""
from ..financial_truth import canonical, versions
from . import codec

IMPLEMENTED = versions.IMPLEMENTED


def canonical_validation_run(cur, f5_run_id, vs=IMPLEMENTED):
    """M4: the key of the canonical validation run of an F5 run under version set `vs`, or None."""
    cur.execute("select validation_run_key from financial_validation_run_current where f5_run_id = %s "
                "and validation_version = %s and input_policy_version = %s and op1_version = %s "
                "and admission_version = %s and identity_version = %s",
                (f5_run_id, vs.validation_version, vs.input_policy_version, vs.op1_version, vs.admission_version,
                 vs.identity_version))
    rows = cur.fetchall()
    if len(rows) > 1:            # impossible under uq_fvr_input_set; refuse rather than choose
        raise RuntimeError(f"F5 run {f5_run_id}: {len(rows)} canonical validation runs")
    return rows[0][0] if rows else None


def run_issuer(cur, validation_run_key):
    """The issuer of a validation run's SOs (one per run: its issuer decision), or None when it has no SO."""
    cur.execute("select distinct f.issuer_id from financial_source_observations s "
                "join financial_economic_facts f on f.ef_key = s.ef_key where s.validation_run_key = %s",
                (validation_run_key,))
    rows = cur.fetchall()
    if len(rows) > 1:
        raise RuntimeError(f"validation run {validation_run_key}: SOs of {len(rows)} issuers")
    return str(rows[0][0]) if rows else None


def stored_observations(cur, validation_run_key):
    """The decoded, stored SOs of a validation run (never silently recomputed ones)."""
    cur.execute("select so_json, output_hash from financial_source_observations where validation_run_key = %s "
                "order by so_key", (validation_run_key,))
    return [codec.decode_source_observation(j, h) for j, h in cur.fetchall()]


def issuers_with_facts(cur, configuration_id):
    """Issuers whose latest batch under the configuration still reports facts (they may have lost all of them)."""
    cur.execute("select issuer_id from (select distinct on (issuer_id) issuer_id, results_count "
                "from financial_reconciliation_batches where configuration_id = %s "
                "order by issuer_id, sequence desc) b where results_count > 0", (configuration_id,))
    return {str(r[0]) for r in cur.fetchall()}


def fingerprint(configuration_id, issuer_id, selection, sos):
    return canonical.digest({"configuration_id": configuration_id, "issuer_id": issuer_id, "selection": selection,
                             "observations": sorted([so.so_key, so.output_hash] for so in sos)})
