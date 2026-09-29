"""
Stage F6.3 - determinism and purity. The same inputs and versions must give byte-identical output and hashes
whatever the order of the inputs; the package must read no database, network, file, clock or environment; F6.1, F5
and the F6.2 design it implements stay unchanged.
"""
import ast
import copy
import hashlib
import itertools
import os
import random
import re
import sys
from dataclasses import fields, is_dataclass
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f63_factories import Doc, configuration  # noqa: E402
from worker import financial_validation as fv  # noqa: E402
from worker.financial_truth import canonical, observations, reconciliation  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")
PACKAGE = os.path.join(REPO, "worker", "financial_truth")


def rich(n=1, **kw):
    """A document exercising every path: an OP1 partition (one column passes, one has a nil term), a total row,
    expenses, a text value, borrowings of both maturities and an undetermined one, a nil word, a company column and
    an instant printed at a cash-flow column's end."""
    d = Doc(doc=n, filing=9000 + n, **kw)
    pl = d.statement()
    cur, cmp_ = d.column(pl), d.column(pl, role="comparative", end="2025-03-31")
    for label, section, raws, concept in (("Revenue", "Continuing operations", ("1,000", "900"), "revenue"),
                                          ("Revenue", "Discontinued operations", ("500", "-"), "revenue"),
                                          ("Revenue", None, ("1,500", "900"), "revenue"),
                                          ("Cost of sales", None, ("(600)", "(500)"), "cost_of_sales"),
                                          ("Gross profit", None, ("900", "400"), "gross_profit"),
                                          ("Profit for the period", None, ("250", "N/A"), "profit_for_period")):
        ri = d.row(pl, label, section=section)
        for ci, raw in zip((cur, cmp_), raws):
            d.value(pl, ri, ci, raw, concept)
    bs = d.statement("financial_position")
    b0 = d.column(bs, kind="instant")
    b1 = d.column(bs, kind="instant", role="comparative", end="2025-03-31", scope="company")
    for label, section, raws, concept in (
            ("Total assets", None, ("9,000", "8,000"), "total_assets"),
            ("Interest bearing borrowings", "Current liabilities", ("1,200", "1,100"), "interest_bearing_borrowings"),
            ("Interest bearing borrowings", "Non-current liabilities", ("3,000", "Nil"), "interest_bearing_borrowings"),
            ("Interest bearing borrowings", None, ("4,200", "4,100"), "interest_bearing_borrowings")):
        ri = d.row(bs, label, section=section)
        for ci, raw in zip((b0, b1), raws):
            d.value(bs, ri, ci, raw, concept)
    cf = d.statement("cash_flows")
    d.value(cf, d.row(cf, "Cash and cash equivalents at the end of the period"), d.column(cf), "700",
            "cash_at_end_of_period")
    return d


def shuffled(result, rng):
    out = copy.deepcopy(result)
    for key in ("statements", "columns", "rows", "candidates"):
        rng.shuffle(out[key])
    return out


def walk(obj, path="root"):
    """Every leaf of a dataclass / tuple / dict tree, with its path."""
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            yield from walk(getattr(obj, f.name), f"{path}.{f.name}")
    elif isinstance(obj, (tuple, list)):
        for i, x in enumerate(obj):
            yield from walk(x, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}[{k!r}]")
    else:
        yield path, obj


# ------------------------------------------------------------------------------------------------ determinism

def test_rich_document_exercises_every_admission_path():
    vr = rich().validate()
    outcomes = {c.admission.admitted for c in vr.candidates}
    reasons = {r for c in vr.candidates for r in c.admission.reasons}
    assert outcomes == {True, False} and {r.outcome for r in vr.op1} == {"pass", "insufficient_evidence"}
    assert {"normalization_not_admissible", "maturity_undetermined:no_section_label",
            "operations_section_derived_unvalidated:insufficient_evidence"} <= reasons
    assert {c.admission.value_kind for c in vr.candidates} == {"numeric", "nil", None}


@pytest.mark.parametrize("seed", range(6))
def test_shuffled_f5_input_gives_byte_identical_validation_and_observations(seed):
    d = rich()
    result = d.result()
    base = d.validate(result)
    other = d.validate(shuffled(result, random.Random(seed)))
    assert (other.key, other.input_hash, other.output_hash) == (base.key, base.input_hash, base.output_hash)
    assert canonical.canonical_json(other) == canonical.canonical_json(base)
    assert [o.output_hash for o in observations.build(other)] == [o.output_hash for o in observations.build(base)]


def test_repeated_runs_are_byte_identical():
    a, b = rich().validate(), rich().validate()
    assert canonical.canonical_json(a) == canonical.canonical_json(b)
    docs = [rich(1), rich(2, doc_type="annual_report")]
    x = reconciliation.reconcile_validation_runs([d.validate() for d in docs], configuration(*docs))
    y = reconciliation.reconcile_validation_runs([d.validate() for d in docs], configuration(*docs))
    assert canonical.canonical_json(x) == canonical.canonical_json(y) and x.output_hash == y.output_hash


def test_reconciliation_is_independent_of_run_and_observation_order():
    docs = [rich(1), rich(2, doc_type="annual_report"), rich(3, recorded_at="2026-06-01T00:00:00+00:00")]
    vrs = [d.validate() for d in docs]
    runs = [vr.f5_run for vr in vrs]
    sos = [o for vr in vrs for o in observations.build(vr)]
    hashes, jsons = set(), set()
    for perm in itertools.permutations(range(3)):
        rng = random.Random(sum(perm))
        order = list(sos)
        rng.shuffle(order)
        batch = reconciliation.reconcile([runs[i] for i in perm], order, configuration(*[docs[i] for i in perm]))
        hashes.add(batch.output_hash)
        jsons.add(canonical.canonical_json(batch))
    assert len(hashes) == len(jsons) == 1


def test_reconcile_fact_is_order_independent():
    docs = [Doc(doc=n, filing=9000 + n).one(raw) for n, raw in ((1, "1,234"), (2, "1,234.4"), (3, "1,234,321"))]
    docs[2].statements[0]["scale"] = 1
    docs[2].candidates[0]["reported_scale"] = 1
    sos = [o for d in docs for o in d.observations()]
    cfg = configuration(*docs)
    results = {reconciliation.reconcile_fact(list(p), cfg).output_hash for p in itertools.permutations(sos)}
    assert len(results) == 1


def test_hashes_change_when_evidence_changes():
    a, b = Doc().one("1,234").validate(), Doc().one("1,235").validate()
    assert a.input_hash != b.input_hash and a.output_hash != b.output_hash
    assert a.candidates[0].admission.ef_key == b.candidates[0].admission.ef_key   # same fact, other evidence


# ------------------------------------------------------------------------------------------------ exact arithmetic

def test_no_binary_floating_point_anywhere_in_the_output():
    docs = [rich(1), rich(2)]
    vrs = [d.validate() for d in docs]
    batch = reconciliation.reconcile_validation_runs(vrs, configuration(*docs))
    for tree in (vrs, [observations.build(vr) for vr in vrs], batch):
        floats = [p for p, v in walk(tree) if isinstance(v, float)]
        assert floats == []
    for r in batch.results:
        for v in (r.interval_low, r.interval_high, r.representative_normalized_value, r.representative_half_unit):
            assert v is None or isinstance(v, Decimal)


def test_float_amounts_are_refused_by_f61():
    d = Doc().one("1,234")
    result = d.result()
    result["candidates"][0]["parsed_value"] = 1234.0
    with pytest.raises(fv.ValidationInputError):
        d.validate(result)


def test_op1_arithmetic_is_exact_beyond_the_default_decimal_precision():
    """31-digit sums: a 28-digit context would round them; F6.3's arithmetic traps inexact results instead."""
    def partition(total):
        d = Doc()
        st = d.statement(scale=1)
        ci = d.column(st)
        for label, section, raw in (("Revenue", "Continuing operations", "600000000000000000000000000001"),
                                    ("Revenue", "Discontinued operations", "400000000000000000000000000000"),
                                    ("Revenue", None, total)):
            d.value(st, d.row(st, label, section=section), ci, raw)
        return d.validate().op1[0]
    ok, off = partition("1000000000000000000000000000001"), partition("1000000000000000000000000000003")
    assert (ok.outcome, ok.difference, ok.computed) == ("pass", Decimal(0), Decimal("1000000000000000000000000000001"))
    assert (off.outcome, off.difference, off.tolerance) == ("fail", Decimal(2), Decimal("1.5"))


def test_agreement_uses_f61_compare_values(monkeypatch):
    calls = []
    real = fv.compare_values

    def spy(a, b):
        calls.append((a.normalized_value, b.normalized_value))
        return real(a, b)
    monkeypatch.setattr(fv, "compare_values", spy)
    docs = [Doc(doc=1, filing=9001).one("1,234"), Doc(doc=2, filing=9002).one("1,234.4")]
    batch = reconciliation.reconcile_validation_runs([d.validate() for d in docs], configuration(*docs))
    assert batch.results[0].state == "corroborated"
    assert (Decimal(1234000), Decimal("1234400.0")) in calls


# ------------------------------------------------------------------------------------------------ purity

ALLOWED_STDLIB = {"collections", "dataclasses", "datetime", "decimal", "hashlib", "json", "re", "typing",
                  "unicodedata"}
ALLOWED_PARENT = {"financial_validation", "financial_concepts"}         # F6.1 and the F5 vocabulary, read only
FORBIDDEN_ATTRIBUTES = {"now", "today", "utcnow", "fromtimestamp", "getenv", "environ", "urandom", "system", "popen",
                        "connect", "cursor", "execute", "urlopen", "request", "sleep", "perf_counter"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "print"}


def package_files():
    return sorted(os.path.join(PACKAGE, n) for n in os.listdir(PACKAGE) if n.endswith(".py"))


def test_the_package_reads_no_database_network_file_clock_or_environment():
    assert len(package_files()) == 10
    for path in package_files():
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in ALLOWED_STDLIB, (path, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert node.module.split(".")[0] in ALLOWED_STDLIB, (path, node.module)
                elif node.level == 2:
                    targets = {node.module} if node.module else {a.name for a in node.names}
                    assert targets <= ALLOWED_PARENT, (path, targets)
                else:
                    assert node.level == 1, path
            elif isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_ATTRIBUTES, (path, node.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, (path, node.func.id)


def test_the_package_holds_no_sql_or_storage():
    for path in package_files():
        text = open(path, encoding="utf-8").read().lower()
        assert not re.search(r"\b(create|alter|drop)\s+table\b|\binsert\s+into\b|\bdelete\s+from\b|psycopg", text), path


# ------------------------------------------------------------------------------------------------ frozen inputs

FROZEN = {   # SHA-256 (LF-normalised) at 40e15bc: F6.3 builds on these, never edits them
    "worker/financial_validation.py": "1e5f649016cff5ad8bee6fc89ad88d5783b4f165a020f24f82d37618669fe5d2",
    "tests/test_f6_validation.py": "608baf86d0554bedb470dcca9184ef873d744945dbd184ebbb7224971f550a16",
    "worker/financial_candidates.py": "4bd6c5e0bd065810a1f2093ba88dd6ca47d1f914f9eecf81bbc86f33c1f8ed9d",
    "worker/financial_concepts.py": "23c3f63feabc5a817f07ef1d56f0cdf07537e6eb4a30072b7305de1fccf5f4db",
    "worker/financial_values.py": "0a2bb60fc012d0c7748fcea6f9867c2449110db5d3d2e0d6c1f6847cb5607f23",
    "docs/F6.2_DESIGN.md": "4c97c6a816fe8ccd55fed38b44e260434f78f11b2fced527fc83d879ef07514c",
}


def test_f61_f5_and_the_f62_design_are_unchanged():
    for rel, sha in FROZEN.items():
        data = open(os.path.join(REPO, *rel.split("/")), "rb").read().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == sha, f"frozen file {rel} changed"
