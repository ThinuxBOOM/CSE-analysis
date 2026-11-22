"""DE-4: static checks of the pending migration 0020 (offline). Like 0019 it stays outside supabase/migrations until
the owner's merge decision, and meets the guards every migration must meet."""
import os
import re

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PENDING = os.path.join(REPO, "supabase", "migrations_pending", "0020_financial_features_and_engines.sql")


def test_s1_pending_not_in_the_chain_and_meets_the_migration_guards():
    assert os.path.exists(PENDING)
    assert not any(n.startswith("0020") for n in os.listdir(os.path.join(REPO, "supabase", "migrations")))
    sql = open(PENDING, encoding="utf-8").read().lower()
    assert "bytea" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert not re.search(r"\b(password|secret|token|credential|api_key)\b", sql)
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert "security definer" not in code and "create role" not in code
    assert "row level security" not in code
    assert sql.count("execute function f5_reject_mutation()") >= 2


def test_s2_no_column_holds_a_raw_response_or_document():
    sql = open(PENDING, encoding="utf-8").read().lower()
    assert not re.search(r"^\s*\w*(raw_response|raw_payload|payload|response_body|body|document_text)\w*\s+"
                         r"(text|jsonb?|varchar)", sql, re.M)


def test_s3_designations_are_owner_only_and_the_worker_cannot_insert_them():
    sql = open(PENDING, encoding="utf-8").read().lower()
    grant = re.search(r"grant insert on ([^;]+) to cse_worker;", sql).group(1)
    assert "designations" not in grant
    assert "trg_f7d_owner" in sql and "trg_ded_owner" in sql
