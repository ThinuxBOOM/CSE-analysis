"""
Stage F6.3 on the real 26-filing F6 corpus - OPTIONAL, skipped unless CSE_F6_CORPUS_DIR is set.

The corpus (the derived F5 results of 26 CSE filings from F6.0; the documents themselves were deleted) is NOT in the
repository: no raw or derived CSE data goes into Git. Point CSE_F6_CORPUS_DIR at a directory holding its *.json
files (one per filing: 'filing', 'classification', 'result') to run these tests.

The corpus has no issuer links, so the issuer is a documented PROXY: the source symbol, supplied as an evidenced
listing_symbol_sec_id decision. A-2 itself is covered by the synthetic tests. The expected numbers are the F6.2
design's measurements (docs/F6.2_DESIGN.md §14), now produced by the F6.3 code itself.
"""
import glob
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.financial_truth import admission, inputs, observations, reconciliation  # noqa: E402

CORPUS = os.environ.get("CSE_F6_CORPUS_DIR")
pytestmark = pytest.mark.skipif(not CORPUS, reason="CSE_F6_CORPUS_DIR is not set (the corpus is not in the repository)")


def load():
    docs = {}
    for path in sorted(glob.glob(os.path.join(CORPUS or "", "*.json"))):
        if path.endswith("__summary.json"):
            continue
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("result"):
            docs[d["result"]["run"]["cse_filing_id"]] = d
    return docs


def validation_runs(docs, rng=None):
    out = []
    for fid in sorted(docs):
        d = docs[fid]
        result = dict(d["result"])
        if rng is not None:
            for key in ("statements", "columns", "rows", "candidates"):
                result[key] = rng.sample(result[key], len(result[key]))
        run = inputs.f5_run_ref(result, f5_run_id=f"corpus:{fid}")
        proxy = inputs.IssuerLinkDecision(fid, "evidenced", "listing_symbol_sec_id", f"proxy:{d['filing']['source_symbol']}")
        out.append(admission.validate_run(result, f5_run=run, issuer_link=proxy,
                                          uploaded_at=result["run"]["timestamps"]["uploaded_at"],
                                          document=inputs.DocumentContext.from_classification(d["classification"])))
    return out


def configuration(vrs):
    return reconciliation.ReconciliationConfiguration(accepted_f3={v.f5_run.f3_version for v in vrs},
                                                      accepted_f4={v.f5_run.f4_version for v in vrs},
                                                      accepted_f5={v.f5_run.f5_version for v in vrs})


@pytest.fixture(scope="module")
def corpus():
    docs = load()
    vrs = validation_runs(docs)
    return docs, vrs, reconciliation.reconcile_validation_runs(vrs, configuration(vrs))


def test_real_corpus_admission(corpus):
    docs, vrs, _ = corpus
    s = admission.summarize(vrs)
    assert len(docs) == 26
    assert (s["candidates"], s["admission"]) == (2248, {"admitted_nil": 43, "admitted_numeric": 1783,
                                                        "not_admitted": 422})
    assert s["op1_section_derived_rows"] == {"insufficient_evidence": 14, "pass": 6}
    for reason, n in (("role_untrusted", 197), ("candidate_status_unresolved", 190), ("duration_months_missing", 111),
                      ("period_end_after_publication", 16), ("mapping_not_single_concept", 17),
                      ("maturity_undetermined:section_not_maturity", 6)):
        assert s["refusal_reasons"][reason] == n, reason


def test_real_corpus_source_observations(corpus):
    _, vrs, _ = corpus
    sos = [o for vr in vrs for o in observations.build(vr)]
    by_status = {}
    for o in sos:
        key = f"{o.observation_status}:{o.value_kind}"
        by_status[key] = by_status.get(key, 0) + 1
    assert len(sos) == 1746 and sum(len(o.members) for o in sos) == 1826
    assert by_status == {"consistent:numeric": 1693, "consistent:nil": 41, "internally_conflicting:numeric": 12}
    assert sum(1 for o in sos if len({(m.statement_index, m.column_index) for m in o.members}) > 1) == 54


def test_real_corpus_economic_facts_and_reconciliation(corpus):
    _, _, batch = corpus
    s = reconciliation.summarize(batch)
    assert s["facts"] == 1488
    assert s["states"] == {"conflicting": 35, "corroborated:nil": 7, "corroborated:numeric": 216,
                           "single_source:nil": 27, "single_source:numeric": 1203}
    assert s["conflicting"] == {"across_documents": 23, "internal_conflict": 12}
    assert s["documents_per_fact"] == {1: 1240, 2: 238, 3: 10}
    assert s["facts_by_scope"] == {"bank": 169, "company": 433, "group": 666, "unlabelled": 220}
    assert s["facts_by_currency"] == {"LKR": 1424, "USD": 64}
    assert s["annotations"]["agreement_within_precision_only"] == 12
    assert s["annotations"]["multi_currency_presentation"] == 128           # 64 USD facts and their 64 LKR twins
    assert "representative_ambiguous" not in s["annotations"] and s["representative_outside_interval"] == 0


def test_real_corpus_is_order_independent(corpus):
    docs, vrs, batch = corpus
    shuffled = validation_runs(docs, random.Random(7))
    random.Random(8).shuffle(shuffled)
    assert sorted(v.output_hash for v in shuffled) == sorted(v.output_hash for v in vrs)
    assert reconciliation.reconcile_validation_runs(shuffled, configuration(shuffled)).output_hash == batch.output_hash
