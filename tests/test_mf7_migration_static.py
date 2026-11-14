"""MF-7: static checks of the pending migration 0019 (offline). It stays outside supabase/migrations until the owner's
merge decision, and it meets the guards every migration must meet (tests/test_document_retrieval.py)."""
import os
import re

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PENDING = os.path.join(REPO, "supabase", "migrations_pending", "0019_market_feature_foundation.sql")


def test_s1_pending_not_in_the_chain_and_meets_the_migration_guards():
    assert os.path.exists(PENDING)
    assert not any(n.startswith("0019") for n in os.listdir(os.path.join(REPO, "supabase", "migrations")))
    sql = open(PENDING, encoding="utf-8").read().lower()
    assert "bytea" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert not re.search(r"\b(password|secret|token|credential|api_key)\b", sql)
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())          # without comments
    assert "security definer" not in code and "create role" not in code
    assert sql.count("execute function f5_reject_mutation()") >= 2       # the append-only loop
