"""DE-4, static: what the store package may import, the wrapper's role rules, and the runbook."""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO, "worker", "feature_engine_store")
WRAPPER = os.path.join(REPO, "ops", "bin", "cse-engines")


def test_s1_nothing_reaches_a_network_and_no_mutable_table_is_read():
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(PACKAGE, name), encoding="utf-8").read()
        for word in ("requests", "urllib", "socket", "http.client", "cse.lk", "subprocess"):
            assert word not in src, (name, word)
        for word in ("report_filings", "report_discovery_runs", "trading_calendar"):
            assert word not in src, (name, word)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert m.split(".")[0] not in ("requests", "urllib", "socket", "http", "aiohttp"), (name, m)


def test_s2_the_wrapper_runs_designate_as_the_migrator_and_everything_else_as_the_worker():
    src = open(WRAPPER, encoding="utf-8").read()
    assert "[[ $EUID -eq 0 ]]" in src and "runuser -u \"$user\"" in src
    assert "user=cse-worker; role=cse_worker" in src
    assert "designate) user=cse-migrator; role=cse_migrator" in src
    assert "python3 -m worker.feature_engine_store" in src


def test_s3_every_cli_command_is_in_the_runbook():
    from worker.feature_engine_store import cli
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    book = open(os.path.join(REPO, "docs", "ops", "P4_FEATURES_AND_ENGINES.md"), encoding="utf-8").read()
    for name in sub.choices:
        assert f"`{name}" in book, name


def test_s4_writes_only_its_own_tables():
    import re
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            src = open(os.path.join(PACKAGE, name), encoding="utf-8").read().lower()
            for t in re.findall(r"insert into \{?(\w+)", src):
                # placeholders: {kind}_designations (f7 | de, argparse choices) and {t}/{table} from f7_/de_ literals
                assert t.startswith(("f7_", "de_")) or t in ("kind", "t", "table"), (name, t)
            for t in re.findall(r'\("(\w+_configurations)"', src):
                assert t in ("f7_configurations", "de_configurations"), (name, t)
