"""M5-6, static: what the registry package may import, the wrapper's role rules, the runbook, and its tables."""
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO, "worker", "ml_registry")
WRAPPER = os.path.join(REPO, "ops", "bin", "cse-ml")


def test_s1_nothing_reaches_a_network():
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(PACKAGE, name), encoding="utf-8").read()
        for word in ("requests", "urllib", "socket", "http.client", "cse.lk", "subprocess"):
            assert word not in src, (name, word)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert m.split(".")[0] not in ("requests", "urllib", "socket", "http", "aiohttp"), (name, m)


def test_s2_the_wrapper_runs_owner_acts_as_the_migrator_and_everything_else_as_the_worker():
    src = open(WRAPPER, encoding="utf-8").read()
    assert "[[ $EUID -eq 0 ]]" in src and "runuser -u \"$user\"" in src
    assert "user=cse-worker; role=cse_worker" in src
    assert "waive|flag|promote) user=cse-migrator; role=cse_migrator" in src
    assert "python3 -m worker.ml_registry" in src
    from worker.ml_registry import cli
    assert set(cli.OWNER) == {"waive", "flag", "promote"}


def test_s3_every_cli_command_is_in_the_runbook():
    from worker.ml_registry import cli
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    book = open(os.path.join(REPO, "docs", "ops", "P5_ML_REGISTRY.md"), encoding="utf-8").read()
    for name in sub.choices:
        assert f"`{name}" in book, name


def test_s4_writes_only_registry_tables_and_the_preflight_covers_every_one():
    from worker.ml_registry import cli, preflight
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            src = open(os.path.join(PACKAGE, name), encoding="utf-8").read().lower()
            for t in re.findall(r"insert into (\w+)", src):
                assert t.startswith("ml_") or t == "r1_predictions", (name, t)
    sql = open(os.path.join(REPO, "supabase", "migrations_pending", "0021_ml_registry.sql"), encoding="utf-8").read()
    created = set(re.findall(r"^create table (\w+)", sql, re.M))
    assert created == set(preflight.WRITE_TABLES) | set(preflight.READ_ONLY) == set(cli.TABLES)
    for trig in re.findall(r"create (?:constraint )?trigger (trg_ml\w+)", sql):
        assert any(trig in v for v in preflight.GUARDS.values()), trig
