"""
Stage F6.3: the PURE financial-truth domain layer. The canonical design is docs/F6.2_DESIGN.md; module-by-module notes
are in docs/F6.3_IMPLEMENTATION.md.

    F5 candidate
      -> F6.1 validation (f6.validation.1, worker/financial_validation.py, unchanged)
      -> admission (f6.admission.1; operations partition OP1 f6.op1.partition.1; publication date f6.inputs.1)
      -> economic-fact identity (f6.identity.1)
      -> source observations (one per validation run and ef_key)
      -> reconciliation (f6.reconciliation.1): single_source | corroborated | conflicting, value_kind numeric | nil

Pure and deterministic: no database, network, filesystem, clock, scheduler, PDF, Gemini or market data is read. The
same inputs and versions give byte-identical output and hashes, whatever the order of the inputs. Persistence is F6.4.

When the evidence conflicts, the conflict is preserved: nothing here picks a winner, changes a reported value,
converts a currency, treats nil as zero, derives a period, merges scopes or uses a document's role, type, audit
status or recency as precedence.

Modules
  versions        the version identifiers
  canonical       byte-stable JSON and SHA-256 (F6.1's canonical_json; the ordered identity form)
  arith           exact Decimal arithmetic (inexact results raise)
  inputs          explicit inputs: F5 run reference, issuer-link decision, F3 document context, f6.inputs.1
  op1             the controlled operations-partition rule
  admission       a validation run: F6.1 plus admission for every candidate of one F5 run
  identity        the economic-fact identity and its ef_key
  observations    source observations
  reconciliation  configuration, one F5 run per document, reconciliation and annotations
"""
