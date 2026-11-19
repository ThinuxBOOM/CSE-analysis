"""MF-7c third part, static: what the store package may read and import, and the wrapper's role rules."""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO, "worker", "market_feature_store")
WRAPPER = os.path.join(REPO, "ops", "bin", "cse-features")


def code(path):
    src = open(path, encoding="utf-8").read()
    return src, "\n".join(line.split("#")[0] for line in src.split('"""')[::2])     # outside docstrings, comments


def test_s1_only_calendar_sync_names_trading_calendar_and_nothing_reaches_a_network():
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        src, c = code(os.path.join(PACKAGE, name))
        if name != "calendar_sync.py":
            assert "trading_calendar" not in c, name
        # MF-8 audit F1: captured.py reads P2's stored completeness count, the JSON path summary -> requests ->
        # attempts; that one exact SQL fragment is data, not a network library (the import check below still holds)
        allowed = "'summary' -> 'requests' ->> 'attempts'" if name == "captured.py" else None
        assert allowed is None or c.count(allowed) == 1, name
        checked = c.replace(allowed, "") if allowed else c
        for word in ("requests", "urllib", "socket", "http.client", "cse.lk", "subprocess"):
            assert word not in checked, (name, word)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert not m.split(".")[0] in ("requests", "urllib", "socket", "http", "aiohttp"), (name, m)


def test_s2_the_wrapper_runs_owner_steps_as_the_migrator_and_everything_else_as_the_worker():
    src = open(WRAPPER, encoding="utf-8").read()
    assert "[[ $EUID -eq 0 ]]" in src and "runuser -u \"$user\"" in src
    assert "user=cse-worker; role=cse_worker" in src
    assert "designate|import-tradeiq|action|withdraw|confirm-no-action|identity) user=cse-migrator; role=cse_migrator" \
        in src
    assert "python3 -m worker.market_feature_store" in src


def test_s3_every_cli_command_is_in_the_runbook():
    from worker.market_feature_store import cli
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    book = open(os.path.join(REPO, "docs", "ops", "P3_MARKET_FEATURES.md"), encoding="utf-8").read()
    for name in sub.choices:
        assert f"`{name}" in book, name
    assert set(cli.OWNER) == {"designate", "import-tradeiq", "action", "withdraw", "confirm-no-action", "identity"}
