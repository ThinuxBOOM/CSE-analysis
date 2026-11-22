"""M5-6: static checks of the pending migration 0021 (offline): outside the chain until the owner's merge decision,
the guards every migration must meet, no binary or path column (artifacts are base64 text), no SECURITY DEFINER or new
role, and the worker never granted INSERT on an owner decision."""
import os
import re

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PENDING = os.path.join(REPO, "supabase", "migrations_pending", "0021_ml_registry.sql")


def test_s1_pending_and_meets_the_migration_guards():
    assert os.path.exists(PENDING)
    assert not any(n.startswith("0021") for n in os.listdir(os.path.join(REPO, "supabase", "migrations")))
    sql = open(PENDING, encoding="utf-8").read().lower()
    assert "bytea" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert not re.search(r"\b(password|secret|token|credential|api_key)\b", sql)
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert "security definer" not in code and "create role" not in code and "row level security" not in code
    assert "artifact_base64 text not null" in code and "33554432" in code
    assert "exclude using gist (period with &&)" in code


def test_s2_owner_decisions_are_never_granted_to_the_worker():
    sql = open(PENDING, encoding="utf-8").read().lower()
    grant = re.search(r"grant insert on ([^;]+) to cse_worker;", sql).group(1)
    for t in ("ml_waivers", "ml_incident_flags", "ml_designations"):
        assert t not in grant and f"before insert on {t} for each row execute function ml_owner_only_guard()" in sql
