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
