"""
F8's frozen boundary, checked statically. Sources: docs/F8_DESIGN.md §3.2, §3.3, §13.4, §16 (must remain frozen), T-25,
T-32, I-6 and I-8.

What is checked:
- F8 reads only append-only tables, and never report_filings, report_discovery_runs, companies or the "current" views.
- F8 writes only its own two tables.
- F8 imports no network library, calls no CSE client function and uses no advisory lock.
- known_at reads no clock, commit timestamp or watermark.
- The pure modules never read a clock.
- Migration 0017 is additive, isolated and HB-1-style.
- The frozen pins of HB-1, HB-2 and HB-3 still hold. They cover migrations 0001-0016, the F1-F6.4 modules, and the
  HB-1 and HB-2 packages.

The last test runs F8 with every network path made to fail, which proves that no CSE request is ever made.
"""
import ast
import os
import re
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker.ops import migrate as mig  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGE = os.path.join(REPO, "worker", "financial_asof")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
MIGRATION = "0017_f8_asof_configuration.sql"
FILES = ["__init__.py", "api.py", "availability.py", "config.py", "errors.py", "explain.py", "knowledge.py",
         "loader.py", "metadata.py", "model.py", "pit.py", "query.py", "result.py", "selection.py", "store.py",
         "supersession.py", "times.py", "versions.py"]
DATABASE_MODULES = ("api.py", "loader.py", "store.py")          # the only modules that touch PostgreSQL
READ_TABLES = {"report_filing_observations", "report_document_classifications", "filing_issuer_links",
               "financial_extraction_runs", "financial_economic_facts", "financial_source_observations",
               "financial_validation_runs", "financial_reconciliation_configurations",
               "financial_reconciliation_designations", "financial_reconciliation_batches",
               "financial_reconciliation_batch_results", "financial_reconciliation_records", "f8_configurations",
               "f8_designations", "issuers", "backfill_item_events", "backfill_request_attempts",
               "backfill_request_outcomes", "backfill_retrieval_records"}
WRITE_TABLES = {"f8_configurations", "f8_designations"}
NEVER_READ = ("report_filings", "report_discovery_runs", "companies", "financial_validation_run_current",
              "financial_reconciliation_current", "financial_fact_state", "financial_fact_provenance",
              "financial_reconciliation_designated", "issuer_securities")
NETWORK = {"requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl", "ftplib", "smtplib"}
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
SQL = re.compile(r"\b(select|insert|update|delete|truncate)\b", re.I)


def sources():
    return {f: open(os.path.join(PACKAGE, f), encoding="utf-8").read() for f in FILES}


def code_strings(src):
    """Every string literal that is not a docstring."""
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def code_without_docstrings(src):
    """The source with docstrings and comments removed (for token checks)."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def imports(src):
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
            out |= {f"{'.' * node.level}{node.module or ''}:{a.name}" for a in node.names}
    return out


def test_the_package_is_exactly_the_f8_modules():
    assert sorted(f for f in os.listdir(PACKAGE) if f.endswith(".py")) == FILES


def test_i6_t25_sql_reads_only_append_only_tables_and_writes_only_f8_tables():
    seen_read, seen_write = set(), set()
    for name, src in sources().items():
        for text in code_strings(src):
            if not SQL.search(text):
                continue
            low = text.lower()
            for bad in NEVER_READ:
                assert not re.search(rf"\b{bad}\b", low), (name, bad, text)
            seen_read |= set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", low))
            written = set(re.findall(r"\binsert\s+into\s+([a-z_][a-z0-9_]*)", low))
            seen_write |= written
            assert not re.search(r"\b(update\s+[a-z_]+\s+set|delete\s+from|truncate)\b", low), (name, text)
        if name not in DATABASE_MODULES:
            assert not any(SQL.search(t) and re.search(r"\b(from|into)\s+[a-z_]", t.lower())
                           for t in code_strings(src)), f"{name} is pure: no SQL"
    seen_read -= {"jsonb_object_keys"}                                      # (none expected; defensive)
    assert seen_read <= READ_TABLES, seen_read - READ_TABLES
    assert seen_write == WRITE_TABLES


def test_reused_frozen_code_is_called_never_reimplemented():
    srcs = sources()
    defined = {n.name for src in srcs.values() for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not defined & {"select_runs", "reconcile_fact", "reconcile", "normalize_filing", "parse_listing_item",
                          "validate_run", "decode_source_observation", "decode_result"}
    assert "reconciliation.select_runs(" in srcs["selection.py"] and "reconciliation.reconcile_fact(" in \
        srcs["selection.py"]
    assert "rd.normalize_filing(" in srcs["metadata.py"] and "rd.parse_listing_item(" in srcs["availability.py"]
    # F8 imports from F6.4 only its decimal context, its frozen loader (run references) and codec (decoders)
    used = {i for src in srcs.values() for i in imports(src) if "financial_truth_store" in i}
    assert {u.split(":")[1] for u in used if ":" in u} <= {"F6_DECIMAL_CONTEXT", "codec", "loader"}, used
    # from F1, only its pure merge and parser
    rd_names = set()
    for src in srcs.values():
        rd_names |= set(re.findall(r"\brd\.([A-Za-z_]+)", code_without_docstrings(src)))
    assert rd_names <= {"normalize_filing", "parse_listing_item", "source_key", "metadata_hash", "ItemRejected",
                        "FEED_ENDPOINT", "LISTING_ENDPOINT", "FEED_BUCKET"}, rd_names


def test_no_network_no_cse_client_no_lock_no_commit_timestamp():
    for name, src in sources().items():
        mods = {i.split(":")[0].lstrip(".").split(".")[0] for i in imports(src)}
        assert not mods & NETWORK, (name, mods & NETWORK)
        code = code_without_docstrings(src)
        assert "cse_client" not in code and "discover_" not in code, name
        assert not LOCK_LITERAL.search(src) and "pg_advisory" not in code and "pg_try_advisory" not in code, name
        assert "track_commit_timestamp" not in code and "pg_xact_commit_timestamp" not in code, name
        assert "watermark" not in code.lower(), name


def test_t32_i8_known_at_reads_only_recorded_times():
    code = code_without_docstrings(sources()["knowledge.py"])
    for token in ("now(", "utcnow", "time.time", "monotonic", "datetime.now", "date.today", "commit", "clock",
                  "first_seen", "retrieved", "uploaded", "authorized", "last_modified", "path_epoch", "job"):
        assert token not in code, token
    terms = set(re.findall(r"\.(observed_at|classified_at|decided_at|recorded_at)\b", code))
    assert terms == {"observed_at", "classified_at", "decided_at", "recorded_at"}


def test_the_pure_layer_reads_no_clock_and_imports_no_database_driver():
    for name, src in sources().items():
        code = code_without_docstrings(src)
        if name in DATABASE_MODULES:
            continue
        for token in ("datetime.now", "utcnow", "time.time", "monotonic", "date.today", "now()"):
            assert token not in code, (name, token)
        assert "psycopg2" not in code, name
    api = code_without_docstrings(sources()["api.py"])
    assert api.count("select now()") == 1                           # the one query time, for an omitted H only
    assert "set transaction isolation level repeatable read, read only" in api


def _functions_reading(src, attribute):
    out = set()
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, ast.FunctionDef) and any(isinstance(n, ast.Attribute) and n.attr == attribute
                                                    for n in ast.walk(fn)):
            out.add(fn.name)
    return out


def test_availability_reads_cse_instants_only_and_never_a_system_time():
    src = sources()["availability.py"]
    code = code_without_docstrings(src)
    for token in ("observed_at <= horizon", "recorded_at <= horizon"):              # only to bound the evidence
        assert token in code
    assert "first_seen" not in code and "classified_at" not in code and "decided_at" not in code
    # system times are read only to bound the evidence by E, to order A-5's base version, and to identify the
    # evidence in its hash; never as an availability time
    assert _functions_reading(src, "document_retrieved_at") == {"_roles", "_evidence_hash"}
    assert _functions_reading(src, "observed_at") == {"filing_availability"}
    assert _functions_reading(src, "recorded_at") == {"versions_of_filing", "_evidence_hash"}
    assert re.search(r"\bat = max\(\(?i\.effective for i in instants", code)        # A-2: the LATEST instant


# ------------------------------------------------------------------------------------------------ migration 0017

def migration():
    with open(os.path.join(MIGDIR, MIGRATION), encoding="utf-8") as f:
        return f.read()


def test_migration_0017_is_additive_isolated_and_hb1_style():
    sql = migration()
    low = sql.lower()
    assert "bytea" not in low and "cse.lk" not in low                                     # F2 / P2 guards
    assert not re.search(r"\b(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", low)
    assert not re.search(r"\balter\s+(table|view|function|role|default)|\bdrop\s", low)      # nothing existing
    assert "security definer" not in low and "row level security" not in low and "create role" not in low
    assert not LOCK_LITERAL.search(sql) and "pg_advisory" not in low
    assert set(re.findall(r"create table (\w+)", low)) == {"f8_configurations", "f8_designations"}
    for t in ("f8_configurations", "f8_designations"):
        assert re.search(rf"before update or delete on {t}\n\s+for each row execute function f5_reject_mutation", sql)
        assert re.search(rf"before truncate on {t}\n\s+for each statement execute function f5_reject_mutation", sql)
    assert set(re.findall(r"create function (\w+)", low)) == {"f8_configuration_guard", "f8_designation_guard"}
    grants = re.findall(r"^grant (.+?) on (.+?) to (\w+);", sql, re.M | re.S)
    assert sorted((g[0], " ".join(g[1].split()), g[2]) for g in grants) == sorted([
        ("select, insert", "f8_configurations", "cse_worker"), ("select", "f8_designations", "cse_worker"),
        ("select", "f8_configurations, f8_designations", "cse_reader")])
    assert "revoke all on f8_configurations, f8_designations from public;" in sql
    assert "no supersession-assertion table" in low and "supersession_assertion" not in re.sub(r"--.*", "", low)
    assert "current_user <> 'cse_owner'" in sql                                            # the runner only


def test_migration_lineage_and_every_frozen_pin_still_hold():
    from worker.backfill_discovery import preflight as hb3
    from worker.backfill_transport import preflight as hb2
    from worker.financial_backfill import preflight as hb1
    names = [m.filename for m in mig.discover(MIGDIR)]
    i = names.index("0016_historical_backfill_ledger.sql")
    assert names[i + 1] == MIGRATION                     # 0017 directly follows 0016 (later ones may follow)
    assert hb1.file_problems() == []                     # 0001-0016 lineage and hashes; the F1-F6.4 modules
    assert hb2.pin_problems() == [] and hb3.pin_problems() == []      # HB-1 / HB-2 / P2 / P3 files HB-2/3 pin


# ------------------------------------------------------------------------------------------------ no CSE request

def test_f8_makes_no_network_request(monkeypatch):
    """Every network path fails, and F8 still answers every mode and explains."""
    import requests

    from f8_factories import World, at, revenue_doc
    from worker import cse_client
    from worker.financial_asof import explain, selection
    from worker.financial_asof.query import AVAILABLE, CURRENT, KNOWN

    def refuse(*a, **k):
        raise AssertionError("F8 attempted a network request")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(requests.Session, "request", refuse)
    monkeypatch.setattr(cse_client, "_post", refuse)
    w = World()
    w.configure(at("2020-01-01"))
    w.listing(101, at("2026-01-10"), uploaded=at("2023-05-15T09:30"))
    d = w.decision(101, at("2026-01-10T01:00"))
    w.process(revenue_doc(101, 1), at("2026-01-12"), decision=d, uploaded_at=at("2023-05-15T09:30"))
    ev = w.evidence()
    for mode, kw in ((KNOWN, dict(cutoff=at("2026-02-01"))), (AVAILABLE, dict(cutoff=at("2024-01-01"),
                                                                               horizon=at("2026-02-01"))),
                     (CURRENT, dict(horizon=at("2026-02-01")))):
        r = selection.evaluate(ev, w.query(mode, **kw))
        assert r.facts and explain.explain(ev, r, audit=True)["facts"]


@pytest.mark.parametrize("name", FILES)
def test_every_module_compiles_and_documents_itself(name):
    src = sources()[name]
    tree = ast.parse(src)
    assert ast.get_docstring(tree), name
