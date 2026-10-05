"""
Synthetic evidence for the F8 tests (not a test module). A World is a small append-only history in which every time is
chosen: a filing published in 2023 can be backfilled in 2026. That lets the unit tests exercise every clock of
docs/F8_DESIGN.md §4.1 without a database.

The source observations are produced by F6.3 itself:
- the documents are tests/f63_factories.Doc;
- each is validated with admission.validate_run against the issuer decision and publication instant the World
  records;
- observations.build turns the validation run into source observations.

The F1 listing entries have the shapes CSE returns:
- /api/financials gives epoch milliseconds;
- the feed gives local-time strings to the second;
- a legacy date-only value is Colombo midnight.

No real filing data, no network.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f63_factories import ISSUER, OTHER_ISSUER, RUN_VERSIONS, Doc  # noqa: E402,F401  (re-exported)
from worker import report_discovery as rd  # noqa: E402
from worker.financial_asof import config as f8config  # noqa: E402
from worker.financial_asof import query as f8query  # noqa: E402
from worker.financial_asof.model import (Classification, Designation, Evidence, ExtractionRun,  # noqa: E402
                                         F6Configuration, F8ConfigurationRow, FilingObservation, IssuerDecision,
                                         StoredBatch, StoredObservation, StoredRecord, ValidationRunRow)
from worker.financial_truth import admission, inputs, observations, reconciliation  # noqa: E402

UTC = timezone.utc
COLOMBO = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def at(text):
    """An instant from 'YYYY-MM-DD[THH:MM[:SS[.ffffff]]]' read as Colombo local time (the CSE clock)."""
    value = datetime.fromisoformat(text)
    return value.replace(tzinfo=COLOMBO).astimezone(UTC) if value.tzinfo is None else value.astimezone(UTC)


def epoch_ms(value):
    return (value - EPOCH) // timedelta(milliseconds=1)


def feed_text(value):
    return value.astimezone(COLOMBO).strftime("%d %b %Y %I:%M:%S %p")


def iso(value):
    return None if value is None else value.astimezone(UTC).isoformat()


CONFIG = reconciliation.ReconciliationConfiguration(
    accepted_f3={(RUN_VERSIONS["classifier_version"], RUN_VERSIONS["text_extractor"])},
    accepted_f4={(RUN_VERSIONS["word_extractor"], RUN_VERSIONS["f4_extractor_version"])},
    accepted_f5={(RUN_VERSIONS["builder_version"], RUN_VERSIONS["mapper_version"], RUN_VERSIONS["vocabulary_version"])})


class Processed:
    """One F5 run of one document version, with its validation run and source observations."""

    def __init__(self, run, validation_run, stored, doc):
        self.run, self.vr, self.stored, self.doc = run, validation_run, stored, doc

    @property
    def sos(self):
        return [s.so for s in self.stored]

    def so(self, concept="revenue", end=None):
        for s in self.stored:
            if s.so.identity.concept_key == concept and (end is None or s.so.identity.period_end.isoformat() == end):
                return s.so
        raise KeyError((concept, end))

    @property
    def ef_keys(self):
        return sorted({s.ef_key for s in self.stored})


class World:
    def __init__(self, issuer=ISSUER):
        self.issuer = issuer
        self.f1, self.classifications, self.decisions = [], [], []
        self.runs, self.vrs, self.stored = [], [], []
        self.f6_configurations, self.f6_designations = [], []
        self.f8_configurations, self.f8_designations, self.batches = [], [], []
        self._ids = {"decision": 0, "f6d": 0, "f8d": 0, "record": 0, "batch": 0}

    def _next(self, kind):
        self._ids[kind] += 1
        return self._ids[kind]

    # ------------------------------------------------------------------------------------------------ F1 listings

    def listing(self, filing, observed, *, uploaded=None, authorized=None, endpoint="financials", bucket="quarterly",
                symbol="COMB.N0000", path=None, text="Interim Financial Statements", raw_uploaded=None,
                raw_authorized=None, item_id=None):
        """One F1 observation: a CSE listing entry version observed at `observed`. uploaded / authorized are
        instants. They are rendered as epoch ms (/api/financials) or a local-time string (the feed). raw_* override
        the rendering verbatim (for example a date-only legacy value)."""
        def render(value, raw):
            if raw is not None:
                return raw
            if value is None:
                return None
            return epoch_ms(value) if endpoint == rd.LISTING_ENDPOINT else feed_text(value)
        item = {"id": filing if item_id is None else item_id, "path": path or f"upload_report_file/369_{filing}.pdf",
                "manualDate": None, "uploadedDate": render(uploaded, raw_uploaded),
                "authorizedDate": render(authorized, raw_authorized), "fileText": text}
        if endpoint == rd.FEED_ENDPOINT:
            item.update(name="COMMERCIAL BANK OF CEYLON PLC", symbol=symbol.split(".")[0])
            bucket, query_symbol = rd.FEED_BUCKET, None
        else:
            query_symbol = symbol
        digest = rd.metadata_hash(item)
        for existing in self.f1:              # F1 stores a version once per (filing, endpoint, bucket, hash) (0004)
            if (existing.cse_filing_id, existing.source_endpoint, existing.source_bucket,
                    existing.metadata_hash) == (filing, endpoint, bucket, digest):
                return existing
        row = FilingObservation(str(uuid.uuid4()), filing, str(uuid.uuid4()), endpoint, bucket, query_symbol,
                                digest, item, observed)
        self.f1.append(row)
        return row

    # ------------------------------------------------------------------------------------------------ F5 decisions

    def decision(self, filing, decided, *, issuer=None, status="evidenced", basis="listing_symbol_sec_id"):
        d = IssuerDecision(self._next("decision"), filing, status, basis,
                           (issuer or self.issuer) if status == "evidenced" else None, decided)
        self.decisions.append(d)
        return d

    # ------------------------------------------------------------------------------------------------ F3 / F5 / F6.4

    def process(self, doc, recorded, *, decision, uploaded_at, classified=None, validated=None, last_modified=None,
                path_epoch=None, retrieved=None, doc_type_status="confirmed", run_id=None):
        """An F5 run of `doc` recorded at `recorded` (its F3 classification at `classified`, by default the same
        transaction), then its F6.4 validation at `validated` (default: recorded) with `decision` and the publication
        instant `uploaded_at`: the F1 metadata current then."""
        run_id = run_id or str(uuid.uuid4())
        cls_id = str(uuid.uuid4())
        result = doc.result()
        result["run"]["timestamps"] = {"uploaded_at": iso(uploaded_at), "authorized_at": None,
                                       "cdn_last_modified": iso(last_modified), "path_epoch_at": iso(path_epoch),
                                       "document_retrieved_at": iso(retrieved)}
        ref = inputs.f5_run_ref(result, f5_run_id=run_id, recorded_at=recorded, classification_id=cls_id)
        self.classifications.append(Classification(cls_id, doc.filing, doc.sha, classified or recorded, doc.doc_type,
                                                   doc_type_status, doc.underlying, "confirmed"))
        run = ExtractionRun(ref, cls_id, recorded, last_modified, path_epoch, retrieved)
        self.runs.append(run)
        processed = self.validate(run, doc, validated or recorded, decision=decision, uploaded_at=uploaded_at,
                                  doc_type_status=doc_type_status)
        return processed

    def validate(self, run, doc, validated, *, decision, uploaded_at, doc_type_status="confirmed"):
        """One F6.4 validation run of an existing F5 run with an explicit input set (a re-validation is allowed)."""
        result = doc.result()
        result["run"]["timestamps"] = dict(run.ref.timestamps)
        link = None if decision is None else inputs.IssuerLinkDecision(
            decision.cse_filing_id, decision.status, decision.basis, decision.issuer_id, link_id=decision.id,
            decided_at=decision.decided_at)
        document = inputs.DocumentContext(doc.versions["classifier_version"], doc.versions["text_extractor"],
                                          doc.doc_type, doc_type_status, doc.underlying, "confirmed", "documented",
                                          "document_only", "document_only", classification_id=run.classification_id)
        vr = admission.validate_run(result, f5_run=run.ref, issuer_link=link, uploaded_at=uploaded_at,
                                    document=document)
        for existing in self.vrs:             # the same input set is already present (0015 uq_fvr_input_set)
            if existing.key == vr.key:
                return Processed(run, existing, [s for s in self.stored if s.validation_run_key == vr.key], doc)
        row = ValidationRunRow(vr.key, run.f5_run_id, run.cse_filing_id, run.document_sha256,
                               None if decision is None else decision.id, uploaded_at, vr.versions, validated)
        self.vrs.append(row)
        stored = [StoredObservation(so, validated) for so in observations.build(vr)]
        self.stored.extend(stored)
        return Processed(run, row, stored, doc)

    # ------------------------------------------------------------------------------------------------ configurations

    def configure(self, registered, *, designated=None, f6_designated=None, configuration=CONFIG):
        """Register the F6 and F8 configurations at `registered`, and designate both (T9 and f8_designations) at
        `designated` / `f6_designated` (default: registered). Returns the f8_configuration_id."""
        if not any(c.configuration_id == configuration.configuration_id for c in self.f6_configurations):
            self.f6_configurations.append(F6Configuration(configuration.configuration_id, configuration, registered))
        f8 = f8config.F8Configuration(configuration.configuration_id)
        if not any(c.f8_configuration_id == f8.f8_configuration_id for c in self.f8_configurations):
            self.f8_configurations.append(F8ConfigurationRow(f8.f8_configuration_id, f8, f8.canonical_json(),
                                                             registered))
        if f6_designated is not False:
            self.f6_designations.append(Designation(self._next("f6d"), "canonical", configuration.configuration_id,
                                                    f6_designated or registered))
        if designated is not False:
            self.f8_designations.append(Designation(self._next("f8d"), "canonical", f8.f8_configuration_id,
                                                    designated or registered))
        return f8.f8_configuration_id

    def register_f8(self, configuration, registered):
        """Register an arbitrary F8 configuration row (for example one naming a rule version this code lacks)."""
        self.f8_configurations.append(F8ConfigurationRow(configuration.f8_configuration_id, configuration,
                                                         configuration.canonical_json(), registered))
        return configuration.f8_configuration_id

    def designate_f8(self, f8_configuration_id, designated):
        self.f8_designations.append(Designation(self._next("f8d"), "canonical", f8_configuration_id, designated))

    def batch(self, recorded, processed, *, configuration=CONFIG, issuer=None):
        """An F6.4 reconciliation batch recorded at `recorded`: F6.3's reconcile over the given processed runs (their
        runs and observations), as F6.4's partition pass would store it."""
        runs = [p.run.ref for p in processed]
        sos = [so for p in processed for so in p.sos]
        result = reconciliation.reconcile(runs, sos, configuration)
        records = tuple(StoredRecord(self._next("record"), r.ef_key, recorded, r) for r in result.results)
        issuer = issuer or self.issuer
        sequence = 1 + sum(1 for b in self.batches if b.configuration_id == configuration.configuration_id
                           and b.issuer_id == issuer)
        b = StoredBatch(str(uuid.uuid4()), configuration.configuration_id, issuer, sequence, recorded,
                        result.output_hash, records)
        self.batches.append(b)
        return b

    # ------------------------------------------------------------------------------------------------ evidence

    def evidence(self, *, order=None):
        """Evidence of every row so far (`order` permutes the input lists, to show order never matters)."""
        lists = dict(filing_observations=self.f1, classifications=self.classifications,
                     issuer_decisions=self.decisions, runs=self.runs, validation_runs=self.vrs,
                     observations=self.stored, f6_configurations=self.f6_configurations,
                     f6_designations=self.f6_designations, f8_configurations=self.f8_configurations,
                     f8_designations=self.f8_designations, batches=self.batches)
        if order is not None:
            lists = {k: order(list(v)) for k, v in lists.items()}
        return Evidence(**lists)

    def query(self, mode, *, cutoff=None, horizon=None, facts=None, pinned=None, issuer=None):
        return f8query.query(issuer_id=issuer or self.issuer, facts=facts, mode=mode, information_cutoff=cutoff,
                             knowledge_horizon=horizon, f8_configuration_id=pinned)


def revenue_doc(filing, n, raw="1,000", *, end="2023-03-31", months=3, doc_type="interim_financial_statements",
                underlying=None, audit="unaudited", restated=False, role="current", concept="revenue", label="Revenue",
                issuer=ISSUER):
    """A one-value document: `concept` for the period ending `end`."""
    d = Doc(filing, doc=n, doc_type=doc_type, underlying=underlying, issuer=issuer)
    return d.one(raw, concept, label=label, column={"end": end, "months": months, "audit": audit,
                                                    "restated": restated, "role": role})
