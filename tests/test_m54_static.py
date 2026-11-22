"""
MLK-14 (Phase 5 §7.2, §13), static and without the ML libraries, so it runs on Windows too: the capture, backfill and
financial-truth code imports no ML library, and the ML packages import no CSE transport or network library. Direct
imports, checked file by file.
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML = ("numpy", "scipy", "sklearn", "lightgbm", "joblib", "threadpoolctl")
GUARDED_PACKAGES = ("market_capture", "backfill_transport", "backfill_discovery", "backfill_documents", "backfill_f6",
                    "backfill_ops", "financial_backfill", "financial_truth", "financial_truth_store",
                    "financial_f4_store", "scheduler", "ops")
GUARDED_FILES = ("cse_client.py", "capture_single_company.py", "capture_multiple_companies.py",
                 "capture_market_open_burst.py", "document_retrieval.py", "retrieve_filing_documents.py",
                 "discover_financial_filings.py", "db.py")
ML_PACKAGES = ("ml_models", "ml_dataset", "ml_protocol", "evaluation_metrics")
NETWORK = ("cse_client", "backfill_transport", "market_capture", "requests", "urllib", "socket", "http")


def imports(path):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield ("." * node.level) + (node.module or "")


def files(pkg):
    for root, _d, fs in os.walk(os.path.join(REPO, "worker", pkg)):
        yield from (os.path.join(root, f) for f in fs if f.endswith(".py"))


def test_mlk14_capture_backfill_and_truth_import_no_ml_library():
    paths = [p for pkg in GUARDED_PACKAGES for p in files(pkg)]
    paths += [os.path.join(REPO, "worker", f) for f in GUARDED_FILES]
    assert len(paths) > 40
    for p in paths:
        bad = [m for m in imports(p) if m.split(".")[0] in ML]
        assert not bad, (p, bad)


def test_mlk14_ml_packages_import_no_cse_transport_or_network():
    for pkg in ML_PACKAGES:
        for p in files(pkg):
            bad = [m for m in imports(p) if any(part in NETWORK for part in m.lstrip(".").split("."))]
            assert not bad, (p, bad)
