"""
Synthetic evidence for the Phase 4 F7 tests (not a test module).

Two kinds:
- `fact(...)` and `result(...)`: F8 FactViews and a sealed AsOfResult built directly, for the pure rules of the
  panel (every row of design §4.2's table, scopes, currencies, anchors) without running F8.
- `fin_doc(...)` and `publish(...)`: multi-concept, multi-period documents processed through F6.3 and F8's own
  World (tests/f8_factories.py builds one value per document), for the leakage tests FL-1 to FL-5 and FL-19, where
  F8 itself must decide what is visible at T.

No real filing data, no network.
"""
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f8_factories import ISSUER, Doc, World, at  # noqa: E402,F401  (re-exported)
from worker.financial_asof.query import AVAILABLE, KNOWN, LABELS, FactFilter  # noqa: E402,F401
from worker.financial_asof.result import (AsOfResult, AvailabilityView, Exclusion, FactView,  # noqa: E402
                                         KnowledgeView, ObservationView, seal)
from worker.financial_asof.versions import IMPLEMENTED  # noqa: E402
from worker.financial_truth.identity import EconomicFactIdentity  # noqa: E402
from worker.financial_truth.versions import IDENTITY_VERSION  # noqa: E402
from worker import financial_concepts as fc  # noqa: E402

D = Decimal
CONCEPT_KIND = {"profit_for_period": "duration", "revenue": "duration", "total_equity": "instant",
                "total_assets": "instant", "cost_of_sales": "duration", "impairment_charges": "duration",
                "finance_costs": "duration", "income_tax_expense": "duration", "cash_at_end_of_period": "instant",
                "cash_and_cash_equivalents": "instant"}


@dataclass(frozen=True)
class FakeF6:
    """The part of an F6.3 ReconciliationResult the panel reads (its annotations)."""
    annotations: tuple = ()


def fact(concept="profit_for_period", end=date(2024, 3, 31), months=12, *, value="100", state="single_source",
         scope="group", currency="LKR", operations="total_or_unstated", nil=False, flags=(), annotations=(),
         counts=(), excluded=(), issuer=ISSUER, maturity="not_applicable", available=(), avail_flags=()):
    kind = fc.BY_KEY[concept].period_kind
    ident = EconomicFactIdentity(IDENTITY_VERSION, issuer, concept, kind, end, months if kind == "duration" else None,
                                 scope, operations, maturity, currency)
    numeric = state in ("single_source", "corroborated") and not nil and value is not None
    v = D(value) if numeric else None
    return FactView(f"ef:{concept}:{end}:{months}:{scope}:{currency}:{operations}:{maturity}", ident, state,
                    "nil" if nil else ("numeric" if numeric else None), v, v, None, v, None,
                    FakeF6(tuple(annotations)) if annotations else None, tuple(observation(a, avail_flags)
                                                                               for a in available),
                    tuple(excluded), (), (), tuple(flags), tuple(counts))


def observation(available_at, flags=(), precision="instant"):
    """A visible observation with the given availability instant (ISO text, or None for availability_unknown)."""
    av = AvailabilityView("f8.availability.1", available_at, precision if available_at else None, (), tuple(flags))
    kn = KnowledgeView("f8.knowledge.1", available_at or "2026-01-01T00:00:00+00:00", ())
    return ObservationView("so", "h" * 64, "d" * 64, 1, "run", "vr", av, kn, ())


def result(facts, *, mode=KNOWN, cutoff="2024-06-30T00:00:00+00:00", horizon=None, configuration="c" * 64,
           issuer=ISSUER, label=None):
    return seal(AsOfResult(mode, label or LABELS[mode], cutoff, horizon, issuer, FactFilter(), configuration,
                           "f" * 64, IMPLEMENTED, None, tuple(facts)))


def not_yet(concept="profit_for_period", end=date(2024, 6, 30), months=12, **kw):
    """A fact whose observation is in the set but not yet available (KNOWN): state none, not_yet_available."""
    ex = Exclusion("so-x", "d" * 64, 1, "run", "vr", "not_yet_available", ())
    return fact(concept, end, months, state="none", value=None, counts=(("not_yet_available", 1),), excluded=(ex,),
                **kw)


# ------------------------------------------------------------------------------------------------ real F8 worlds

def fin_doc(filing, n, values, *, end="2024-03-31", months=12, scope="group", currency="LKR",
            doc_type="annual_report", issuer=ISSUER):
    """One document: `values` maps concept -> raw printed value, all for one period ending `end`. Duration concepts
    go into a profit-or-loss statement, instant concepts into a financial-position statement."""
    d = Doc(filing, doc=n, doc_type=doc_type, issuer=issuer)
    income = {c: v for c, v in values.items() if fc.BY_KEY[c].period_kind == "duration"}
    position = {c: v for c, v in values.items() if fc.BY_KEY[c].period_kind == "instant"}
    if income:
        st = d.statement("profit_or_loss", currency=currency)
        ci = d.column(st, end=end, months=months, scope=scope)
        for c, raw in income.items():
            d.value(st, d.row(st, c.replace("_", " ").title()), ci, raw, c)
    if position:
        st = d.statement("financial_position", currency=currency)
        ci = d.column(st, kind="instant", end=end, scope=scope)
        for c, raw in position.items():
            d.value(st, d.row(st, c.replace("_", " ").title()), ci, raw, c)
    return d


def publish(world, doc, *, published, recorded):
    """List the filing (observed just before `recorded`), decide its issuer link, and process it at `recorded`."""
    world.listing(doc.filing, recorded - timedelta(hours=2), uploaded=published)
    d = world.decision(doc.filing, recorded - timedelta(hours=1))
    return world.process(doc, recorded, decision=d, uploaded_at=published)
