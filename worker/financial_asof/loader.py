"""
PostgreSQL -> model.Evidence for one issuer (docs/F8_DESIGN.md §3.1, §3.2). Read-only: the caller (api.py) runs it in
one REPEATABLE READ, READ ONLY transaction, so every row comes from one snapshot.

It reads only append-only tables:
- F1 report_filing_observations;
- F3 report_document_classifications;
- F5 filing_issuer_links and financial_extraction_runs;
- F6.4 T1, T4, T5, T8, T9, T12, T13, T16;
- F8's own f8_configurations and f8_designations.

It never reads the mutable report_filings, report_discovery_runs or companies, or the views that read them (I-6, §3.2).
F6.4's frozen loader renders the F5 run references (rules L2 and L5), and its frozen codec decodes the E3, E4 and E5
envelopes.

The rows loaded are a superset of every information set the query can need. selection.py keeps only the rows of Ω,
using their recorded times:
- the issuer's facts and their source observations;
- every F5 run of their documents, under any filing;
- every F5 run of the filings involved (for A-5's base version);
- the validation runs, classifications, issuer decisions and F1 observations of those runs and filings.
"""
from ..financial_truth.reconciliation import ConfigurationError
from ..financial_truth.versions import VersionSet
from ..financial_truth_store import codec
from ..financial_truth_store import loader as f64_loader
from . import config
from .errors import Refused
from .model import (Classification, Designation, Evidence, ExtractionRun, F6Configuration, F8ConfigurationRow,
                    FilingObservation, IssuerDecision, StoredBatch, StoredObservation, StoredRecord, ValidationRunRow)


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _f6_configuration(row):
    try:
        cfg = codec.decode_configuration(row["configuration_json"])
    except (ConfigurationError, codec.CodecError):
        cfg = None                                      # not of the implemented F6 version set: refused if used
    return F6Configuration(row["configuration_id"], cfg, row["recorded_at"])


def _f8_configuration(row):
    try:
        cfg = config.decode(row["configuration_json"])
    except Refused:
        cfg = None
    return F8ConfigurationRow(row["f8_configuration_id"], cfg, row["configuration_json"], row["recorded_at"])


def configurations(cur):
    """T8, T9, f8_configurations and f8_designations (every row: they are few, and selection picks by time)."""
    return dict(
        f6_configurations=[_f6_configuration(r) for r in _rows(
            cur, "select configuration_id, configuration_json, recorded_at from "
                 "financial_reconciliation_configurations order by configuration_id")],
        f6_designations=[Designation(r["id"], r["purpose"], r["configuration_id"], r["recorded_at"]) for r in _rows(
            cur, "select id, purpose, configuration_id, recorded_at from financial_reconciliation_designations "
                 "order by id")],
        f8_configurations=[_f8_configuration(r) for r in _rows(
            cur, "select f8_configuration_id, configuration_json, recorded_at from f8_configurations "
                 "order by f8_configuration_id")],
        f8_designations=[Designation(r["id"], r["purpose"], r["f8_configuration_id"], r["recorded_at"]) for r in _rows(
            cur, "select id, purpose, f8_configuration_id, recorded_at from f8_designations order by id")])


def filing_evidence(cur, filings, run_ids):
    """The F1 observations and issuer decisions of `filings`, and the F5 runs `run_ids` with their validation runs
    and classifications."""
    filings, run_ids = sorted(set(filings)), sorted({str(r) for r in run_ids})
    f1 = [FilingObservation(r["id"], r["cse_filing_id"], None if r["discovery_run_id"] is None
                            else str(r["discovery_run_id"]), r["source_endpoint"], r["source_bucket"],
                            r["query_symbol"], r["metadata_hash"], r["raw_item"], r["observed_at"])
          for r in _rows(cur, "select id, cse_filing_id, discovery_run_id, source_endpoint, source_bucket, query_symbol, "
                              "metadata_hash, raw_item, observed_at from report_filing_observations "
                              "where cse_filing_id = any(%s) order by id", (filings,))]
    decisions = [IssuerDecision(r["id"], r["cse_filing_id"], r["status"], r["basis"], r["issuer_id"], r["decided_at"])
                 for r in _rows(cur, "select id, cse_filing_id, status, basis, issuer_id, decided_at "
                                     "from filing_issuer_links where cse_filing_id = any(%s) order by id", (filings,))]
    rows = _rows(cur, "select id, classification_id, recorded_at, cdn_last_modified, path_epoch_at, "
                      "document_retrieved_at from financial_extraction_runs where id = any(%s::uuid[]) order by id",
                 (run_ids,))
    refs = {r.f5_run_id: r for r in f64_loader.all_run_refs(cur, run_ids)} if run_ids else {}
    runs = [ExtractionRun(refs[str(r["id"])], str(r["classification_id"]), r["recorded_at"], r["cdn_last_modified"],
                          r["path_epoch_at"], r["document_retrieved_at"]) for r in rows]
    classifications = [Classification(r["id"], r["cse_filing_id"], r["document_sha256"], r["classified_at"],
                                      r["document_type"], r["document_type_status"], r["underlying_type"],
                                      r["underlying_type_status"])
                       for r in _rows(cur, "select id, cse_filing_id, document_sha256, classified_at, document_type, "
                                           "document_type_status, underlying_type, underlying_type_status from "
                                           "report_document_classifications where id = any(%s::uuid[]) order by id",
                                      (sorted({r.classification_id for r in runs}),))]
    validation_runs = [
        ValidationRunRow(r["validation_run_key"], str(r["f5_run_id"]), r["cse_filing_id"], r["document_sha256"],
                         r["issuer_link_id"], r["publication_uploaded_at"],
                         VersionSet(r["validation_version"], r["input_policy_version"], r["op1_version"],
                                    r["admission_version"], r["identity_version"]), r["recorded_at"])
        for r in _rows(cur, "select validation_run_key, f5_run_id, cse_filing_id, document_sha256, issuer_link_id, "
                            "publication_uploaded_at, validation_version, input_policy_version, op1_version, "
                            "admission_version, identity_version, recorded_at from financial_validation_runs "
                            "where f5_run_id = any(%s::uuid[]) order by validation_run_key", (run_ids,))]
    return dict(filing_observations=f1, issuer_decisions=decisions, runs=runs, classifications=classifications,
                validation_runs=validation_runs)


def _batches(cur, issuer_id, recorded_cutoff):
    """KNOWN_RECORDED: per F6 configuration, the issuer's latest batch recorded at or before T, with its records."""
    heads = _rows(cur, "select distinct on (configuration_id) batch_id, configuration_id, issuer_id, sequence, "
                       "recorded_at, output_hash from financial_reconciliation_batches where issuer_id = %s "
                       "and recorded_at <= %s order by configuration_id, sequence desc", (issuer_id, recorded_cutoff))
    if not heads:
        return []
    records = {}
    for r in _rows(cur, "select br.batch_id, rr.record_id, rr.ef_key, rr.recorded_at, rr.result_json, rr.output_hash "
                        "from financial_reconciliation_batch_results br join financial_reconciliation_records rr "
                        "on rr.record_id = br.record_id where br.batch_id = any(%s::uuid[]) order by br.result_ordinal",
                   ([str(h["batch_id"]) for h in heads],)):
        records.setdefault(str(r["batch_id"]), []).append(
            StoredRecord(r["record_id"], r["ef_key"], r["recorded_at"],
                         codec.decode_result(r["result_json"], r["output_hash"])))
    return [StoredBatch(h["batch_id"], h["configuration_id"], h["issuer_id"], h["sequence"], h["recorded_at"],
                        h["output_hash"], tuple(records.get(str(h["batch_id"]), ()))) for h in heads]


def load(cur, issuer_id, *, recorded_cutoff=None):
    """Every row an as-of query on `issuer_id` may need (a superset of its information set)."""
    sos = _rows(cur, "select s.so_key, s.recorded_at, s.so_json, s.output_hash from financial_source_observations s "
                     "join financial_economic_facts f on f.ef_key = s.ef_key where f.issuer_id = %s "
                     "order by s.so_key", (issuer_id,))
    observations = [StoredObservation(codec.decode_source_observation(r["so_json"], r["output_hash"]),
                                      r["recorded_at"]) for r in sos]
    documents = sorted({o.document_sha256 for o in observations})
    filings = {o.cse_filing_id for o in observations}
    filings |= {r["cse_filing_id"] for r in _rows(cur, "select distinct cse_filing_id from financial_extraction_runs "
                                                       "where document_sha256 = any(%s)", (documents,))}
    run_ids = [r["id"] for r in _rows(cur, "select id from financial_extraction_runs where cse_filing_id = any(%s) "
                                           "or document_sha256 = any(%s)", (sorted(filings), documents))]
    parts = filing_evidence(cur, filings, run_ids)
    parts.update(configurations(cur))
    batches = _batches(cur, issuer_id, recorded_cutoff) if recorded_cutoff is not None else []
    return Evidence(observations=observations, batches=batches, **parts)
