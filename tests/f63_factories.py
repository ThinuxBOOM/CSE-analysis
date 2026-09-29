"""
Synthetic inputs for the F6.3 tests: tiny F5 build() results of the same shape F5 persists (statements, period
columns, mapped rows, candidates), plus their F5 run references, issuer-link decisions and F3 document contexts.
Values are parsed with F5's own parser and operations with F5's own rule. No real filing data.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker import financial_candidates as f5
from worker import financial_concepts as fc
from worker.financial_truth import admission, inputs, observations, reconciliation
from worker.financial_values import parse_value

ISSUER = "11111111-1111-4111-8111-111111111111"
OTHER_ISSUER = "22222222-2222-4222-8222-222222222222"
UPLOADED = "2026-05-15T04:00:00+00:00"                     # 09:30 Colombo on 15 May 2026
RUN_VERSIONS = {"word_extractor": "pdftotext-bbox 24.02", "f4_extractor_version": "f4.test",
                "classifier_version": "f3.test", "text_extractor": "pdftotext 24.02 -layout",
                "builder_version": f5.F5_BUILDER_VERSION, "mapper_version": fc.MAPPER_VERSION,
                "vocabulary_version": fc.VOCABULARY_VERSION}


def sha(n):
    """A deterministic stand-in document SHA-256."""
    return f"{n:064x}"


def _start(end, months):
    e = date.fromisoformat(end) + timedelta(days=1)
    y, m = divmod(e.month - 1 - months, 12)
    return date(e.year + y, m + 1, e.day).isoformat()


class Doc:
    """One synthetic document processed by one F5 run: add statements, columns, rows and values, then validate."""

    def __init__(self, filing=9001, *, doc=1, run_id=None, recorded_at="2026-05-16T00:00:00+00:00",
                 doc_type="interim_financial_statements", underlying=None, issuer=ISSUER, link_status="evidenced",
                 link_basis="listing_symbol_sec_id", uploaded_at=UPLOADED, versions=None):
        self.filing, self.sha = filing, sha(doc)
        self.run_id = run_id or f"run-{filing}-{doc}"
        self.recorded_at, self.uploaded_at = recorded_at, uploaded_at
        self.doc_type, self.underlying = doc_type, underlying or doc_type
        self.issuer, self.link_status, self.link_basis = issuer, link_status, link_basis
        self.versions = dict(RUN_VERSIONS, **(versions or {}))
        self.statements, self.columns, self.rows, self.candidates = [], [], [], []

    # --- structure ------------------------------------------------------------------------------------------------

    def statement(self, kind="profit_or_loss", *, currency="LKR", scale=1000, continuation_of=None):
        i = len(self.statements)
        self.statements.append({"statement_index": i, "statement_kind": kind, "continuation_of": continuation_of,
                                "currency": currency, "scale": scale, "scale_basis": "header_zone"})
        return i

    def column(self, st, *, kind="duration", end="2026-03-31", months=3, start="auto", role="current", scope="group",
               audit="unaudited", restated=False, role_trust="trusted", fiscal_label=None):
        ci = sum(1 for c in self.columns if c["statement_index"] == st)
        duration = kind == "duration"
        self.columns.append({
            "statement_index": st, "column_index": ci, "column_status": "resolved", "period_kind": kind,
            "period_class": f5.period_class(kind, months if duration else None),
            "start_date": (_start(end, months) if start == "auto" else start) if duration else None,
            "end_date": end, "duration_months": months if duration else None, "period_evidence_source": "header",
            "fiscal_label": fiscal_label, "role": role, "role_basis": "f3.statement_period", "role_trust": role_trust,
            "reported_scope": scope, "reported_scope_basis": "header_word" if scope != "unstated" else None,
            "audit_label_reported": audit, "audit_evidence_source": "column_header_word", "audit_trust": "trusted",
            "audit_rule_id": f5.AUDIT_RULE_ID, "restated": restated, "reasons": []})
        return ci

    def row(self, st, label, *, section=None):
        ri = sum(1 for r in self.rows if r["statement_index"] == st)
        ops, basis = fc.operations(label, section)
        self.rows.append({"statement_index": st, "row_index": ri, "label_raw": label, "section_label_raw": section,
                          "operations": ops, "operations_basis": basis})
        return ri

    def value(self, st, ri, ci, raw, concept="revenue", *, status="proposed", ordinal=0, scale="statement",
              currency="statement", scale_basis="header_zone", mapping_status="mapped", candidate_id=None):
        stmt = self.statements[st]
        col = next(c for c in self.columns if (c["statement_index"], c["column_index"]) == (st, ci))
        con = fc.BY_KEY.get(concept) if concept else None
        if con is None or con.period_kind == col["period_kind"]:
            pkind, pclass, derivation = col["period_kind"], col["period_class"], "column"
        elif con.instant_from_duration_column_end:
            pkind, pclass, derivation = "instant", None, "duration_column_end"
        else:
            raise ValueError(f"{concept} cannot sit in a {col['period_kind']} column")
        pv = parse_value(raw)
        self.candidates.append({
            "statement_index": st, "row_index": ri, "column_index": ci, "value_ordinal": ordinal, "concept_key": concept,
            "mapping_status": mapping_status, "mapping_rule_ids": [], "ambiguous_concepts": [],
            "period_kind": pkind, "period_class": pclass, "period_derivation": derivation,
            "value_type": con.value_type if con else None, "attribution": con.attribution if con else "not_applicable",
            "raw_value": raw, "parsed_value": pv.parsed, "representation_class": pv.representation_class,
            "printed_decimals": pv.decimals, "sign_as_printed": f5.sign_as_printed(pv.representation_class, pv.parsed),
            "reported_scale": stmt["scale"] if scale == "statement" else scale, "scale_basis": scale_basis,
            "reported_currency": stmt["currency"] if currency == "statement" else currency,
            "candidate_status": status, "id": candidate_id})

    def one(self, raw="1,234", concept="revenue", *, label="Revenue", kind=None, section=None, column=None, **value):
        """A statement with one column and one row holding one value. Returns self."""
        con = fc.BY_KEY[concept]
        stmt_kind = kind or {"income": "profit_or_loss", "position": "financial_position",
                             "cash_flow": "cash_flows"}[con.family]
        st = self.statement(stmt_kind, **({"scale": value.pop("stmt_scale")} if "stmt_scale" in value else {}))
        col = dict(column or {})
        if con.period_kind == "instant" and not con.instant_from_duration_column_end:
            col.setdefault("kind", "instant")
        ci = self.column(st, **col)
        self.value(st, self.row(st, label, section=section), ci, raw, concept, **value)
        return self

    # --- F5 result and F6 inputs ------------------------------------------------------------------------------------

    def result(self):
        has_group = {c["statement_index"] for c in self.columns if c["reported_scope"] == "group"}
        columns = []
        for c in self.columns:
            reported = c["reported_scope"]
            canon, basis = f5.canonical_scope(None if reported == "unstated" else reported,
                                              c["statement_index"] in has_group)
            columns.append(dict(c, canonical_scope=canon, canonical_scope_basis=basis))
        run = dict(self.versions, cse_filing_id=self.filing, document_sha256=self.sha,
                   timestamps={"uploaded_at": self.uploaded_at, "authorized_at": None})
        return {"run": run, "statements": list(self.statements), "columns": columns, "rows": list(self.rows),
                "candidates": list(self.candidates)}

    def run_ref(self, result=None):
        return inputs.f5_run_ref(result or self.result(), f5_run_id=self.run_id, recorded_at=self.recorded_at)

    def issuer_link(self):
        if self.link_status is None:
            return None
        evidenced = self.link_status == "evidenced"
        return inputs.IssuerLinkDecision(self.filing, self.link_status, self.link_basis,
                                         self.issuer if evidenced else None, link_id=self.filing * 10)

    def document(self):
        return inputs.DocumentContext(self.versions["classifier_version"], self.versions["text_extractor"],
                                      self.doc_type, "confirmed", self.underlying, "confirmed", "documented",
                                      "document_only", "document_only")

    def validate(self, result=None):
        result = result or self.result()
        return admission.validate_run(result, f5_run=self.run_ref(result), issuer_link=self.issuer_link(),
                                      uploaded_at=self.uploaded_at, document=self.document())

    def observations(self):
        return observations.build(self.validate())


def only(validation_run):
    """The single candidate result of a one-value document."""
    (c,) = validation_run.candidates
    return c


def configuration(*docs):
    return reconciliation.ReconciliationConfiguration(
        accepted_f3={d.run_ref().f3_version for d in docs}, accepted_f4={d.run_ref().f4_version for d in docs},
        accepted_f5={d.run_ref().f5_version for d in docs})


def reconcile(*docs):
    return reconciliation.reconcile_validation_runs([d.validate() for d in docs], configuration(*docs))


def single(batch):
    (r,) = batch.results
    return r
