"""
Phase 6 NS-7, static: migration 0022 waits outside the chain (C-0119), meets the guards every migration meets (no
binary, path or credential column; no SECURITY DEFINER, new role or row-level security), never grants the worker an
owner decision, and refuses CSE in the database too; the wrapper runs owner steps as the migrator; every command is in
the runbook; there is no ingest command in v1.
"""
import os
import re

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PENDING = os.path.join(REPO, "supabase", "migrations_pending", "0022_news_sentiment.sql")
WRAPPER = os.path.join(REPO, "ops", "bin", "cse-news")


def test_pending_outside_the_chain_and_the_migration_guards():
    assert os.path.exists(PENDING)
    assert not [n for n in os.listdir(os.path.join(REPO, "supabase", "migrations")) if n.startswith("0022")]
    sql = open(PENDING, encoding="utf-8").read().lower()
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert "bytea" not in code
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", code)
    assert not re.search(r"\b(password|secret|token|credential|api_key)\b", code)
    assert "security definer" not in code and "create role" not in code and "row level security" not in code
    assert "extracted_text text," in code                                    # the one text column, nullable
    assert "rtrim(lower(host), '.') like '%.cse.lk'" in code and "rtrim(lower(x.h), '.') like '%.cse.lk') then" in code


def test_owner_decisions_are_never_granted_to_the_worker():
    sql = open(PENDING, encoding="utf-8").read().lower()
    grant = re.search(r"grant insert on ([^;]+) to cse_worker;", sql).group(1)
    for t in ("ns_sources", "ns_archive_imports", "ns_entity_aliases", "ns_alias_ends", "ns_designations"):
        assert re.search(rf"\b{t}\b", grant) is None, t
    for t in ("ns_archive_imports", "ns_entity_aliases", "ns_alias_ends", "ns_designations"):
        assert f"before insert on {t} for each row execute function ns_owner_only_guard()" in sql


def test_the_wrapper_and_the_runbook():
    src = open(WRAPPER, encoding="utf-8").read()
    assert "[[ $EUID -eq 0 ]]" in src and "runuser -u \"$user\"" in src and "user=cse-worker; role=cse_worker" in src
    assert ("register-source|start-ingestion|clear-stop|alias|end-alias|declare-archive|designate) user=cse-migrator; "
            "role=cse_migrator") in src
    from worker.news_store import cli
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    assert "ingest" not in sub.choices and not any("fetch" in c for c in sub.choices)
    assert set(cli.OWNER) <= set(sub.choices)
    book = open(os.path.join(REPO, "docs", "ops", "P6_NEWS_SENTIMENT.md"), encoding="utf-8").read()
    for name in sub.choices:
        assert f"`{name}" in book, name
