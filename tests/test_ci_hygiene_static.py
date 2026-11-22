"""CI hygiene, static: no test module defines a module-level `setup` or `teardown` function. pytest versions with
nose support (CI's Linux image pins 7.4.4) call such a function as a fixture with no arguments, so a helper of that
name passes locally on a newer pytest and errors in CI (branch review, 2026-11-18: ci-regression L16 at 6678ca5)."""
import ast
import os

TESTS = os.path.dirname(os.path.abspath(__file__))
RESERVED = {"setup", "teardown", "setup_module", "teardown_module", "setup_function", "teardown_function"}


def test_no_module_level_nose_names_used_as_helpers():
    found = []
    for name in sorted(os.listdir(TESTS)):
        if name.endswith(".py"):
            tree = ast.parse(open(os.path.join(TESTS, name), encoding="utf-8").read())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in RESERVED and node.args.args:
                    found.append(f"{name}: {node.name}({', '.join(a.arg for a in node.args.args)})")
    assert not found, found


def test_the_ml_libraries_are_hash_pinned_and_reported():
    """C-0116 (branch review, before `main`): every ML requirement is pinned to one version with wheel SHA-256s,
    the CI image installs them with --require-hashes, and the environment report prints their versions."""
    import re
    ci = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "ci")
    req = open(os.path.join(ci, "requirements-ml.txt"), encoding="utf-8").read()
    entries = [e for e in re.split(r"\n(?=[A-Za-z])", "\n".join(ln for ln in req.splitlines()
                                                                   if not ln.startswith("#"))) if e.strip()]
    names = set()
    for e in entries:
        m = re.match(r"([A-Za-z0-9_.-]+)==([0-9][^ \\\n]*)", e)
        assert m, e[:60]
        names.add(m.group(1).lower())
        assert re.findall(r"--hash=sha256:[0-9a-f]{64}", e), m.group(1)
    assert names == {"numpy", "scipy", "scikit-learn", "lightgbm", "joblib", "threadpoolctl"}
    assert "--require-hashes" in open(os.path.join(ci, "linux.Dockerfile"), encoding="utf-8").read()
    report = open(os.path.join(ci, "env_report.py"), encoding="utf-8").read()
    for lib in ("numpy", "scipy", "sklearn", "lightgbm"):
        assert f'"{lib}"' in report
