"""
Phase 2 HB-1 (the historical-backfill ledger) without a database: the state machine and its mirror in migration 0016,
natural keys, Colombo days and budget accounting, the F2 retrieval-record mapping, the owner's arming decision against
0016's CHECKs, 0016's static rules (the frozen guards every migration must meet), the migration lineage, the frozen
pins, and the package boundary. No network, no CSE, no database.
"""
import dataclasses
import hashlib
import os
import re
import shutil
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.document_retrieval import RetrievalRecord  # noqa: E402
from worker.financial_backfill import (HELPER_FUNCTIONS, LEDGER_MIGRATION, LEDGER_MIGRATION_SHA256,  # noqa: E402
                                       TRIGGER_FUNCTIONS, keys, owner, preflight, records, states, store)
from worker.ops import migrate as mig  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
MIGDIR = os.path.join(REPO, "supabase", "migrations")
PACKAGE = os.path.join(REPO, "worker", "financial_backfill")
SHA_0015 = "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2"


def sql0016():
    with open(os.path.join(MIGDIR, LEDGER_MIGRATION), encoding="utf-8") as f:
        return f.read()


def check_values(sql, name):
    """The quoted values of one CHECK constraint ... in (...) list or array[...] of migration 0016."""
    m = re.search(rf"constraint {name} check \((.*?)\),?\n\s*(?:constraint|\))", sql, re.S)
    assert m, name
    return re.findall(r"'([^']*)'", m.group(1))


def transitions_in_migration(sql):
    stmt = sql[sql.index("insert into backfill_item_transitions"):]
    stmt = stmt[:stmt.index(";")]
    out = set()
    for part in stmt.split("union all"):
        m = re.search(r"unnest\(array\[([^\]]*)\]\)", part)
        kinds = re.findall(r"'([a-z_]+)'", m.group(1)) if m else re.findall(r"select '([a-z_]+)'", part)
        for f, t, a in re.findall(r"\('([a-z_]*)', '([a-z_]+)', '([a-z_]+)'\)", part):
            out |= {(k, f, t, a) for k in kinds}
    return out


# ------------------------------------------------------------------------------------------------ the state machine

def test_u1_the_state_machine_is_exactly_the_migrations_rule_data():
    sql = sql0016()
    assert transitions_in_migration(sql) == states.TRANSITIONS and len(states.TRANSITIONS) == 91
    assert set(check_values(sql, "chk_bfie_state")) == states.ALL_STATES
    assert check_values(sql, "chk_bfie_action") == list(states.ACTIONS)
    assert check_values(sql, "chk_bfie_excluded") == ["excluded", "", *states.EXCLUSION_REASONS]  # '' = coalesce
    assert check_values(sql, "chk_bfi_kind") == list(states.KINDS)
    assert "length(btrim(coalesce(reason, ''))) >= 10" in sql and states.REQUEUE_REASON_MIN == 10


def test_u2_the_state_machine_has_the_designed_shape():
    T = states.TRANSITIONS
    assert states.FIRST_STATES == {**{k: {"pending"} for k in states.KINDS}, "document": {"discovered", "excluded"}}
    # in-flight states exist only for CSE work, are entered only by a claim (requesting) or a record, and are left for
    # 'abandoned' only by a lease expiry
    assert {k for k, _, t, _ in T if t in states.IN_FLIGHT} == set(states.CSE_KINDS)
    assert {(f, t) for _, f, t, a in T if a == "claim"} == {("pending", "requesting"), ("retry_wait", "requesting")}
    assert {(f, t) for _, f, t, a in T if a == "expire"} == {("requesting", "abandoned"), ("processing", "abandoned")}
    assert {a for _, _, t, a in T if t == "abandoned"} == {"expire"}
    assert {(f, t) for _, f, t, a in T if a == "resume"} == {("blocked", "pending")}
    # every final state leaves only by an explicit operator re-queue to pending
    for kind, finals in states.FINAL.items():
        for s in finals:
            assert states.next_states(kind, s) == {"pending": "requeue"}, (kind, s)
    assert states.FINAL["document"] == {"excluded", "retrieval_failed", "consumer_failed", "cleanup_failed", "failed"}
    assert states.FINAL["listing"] == {"succeeded", "partial", "failed"}
    # every state is reachable from a first state
    for kind in states.KINDS:
        seen, todo = set(), list(states.FIRST_STATES[kind])
        while todo:
            s = todo.pop()
            if s not in seen:
                seen.add(s)
                todo += list(states.next_states(kind, s))
        assert seen == states.STATES[kind], kind
    # non-CSE work: pending -> succeeded | failed only
    for kind in states.OTHER_KINDS:
        assert states.STATES[kind] == {"pending", "succeeded", "failed"}


def test_u3_illegal_transitions_are_refused():
    with pytest.raises(states.IllegalTransition):
        states.check("listing", "pending", "succeeded", "record")          # never without a request in flight
    with pytest.raises(states.IllegalTransition):
        states.check("document", "persisted", "pending", "requeue", "a good operator reason")  # persisted is not final
    with pytest.raises(states.IllegalTransition):
        states.check("validate", "pending", "requesting", "claim")         # no CSE request, no lease
    with pytest.raises(states.IllegalTransition, match="excluded needs"):
        states.check("document", "", "excluded", "create", "too_old")
    with pytest.raises(states.IllegalTransition, match="reason of at least"):
        states.check("document", "failed", "pending", "requeue", "short")
    states.check("document", "", "excluded", "create", "no_document")
    states.check("document", "failed", "pending", "requeue", "operator: the CDN object is back")
    assert states.rows() == sorted(states.TRANSITIONS)


# ------------------------------------------------------------------------------------------------ keys, days, budgets

def test_u4_natural_keys_are_the_migrations_subjects():
    path = "cmt/upload_report_file/771_1653995188923 (1).pdf"
    v = hashlib.sha256(path.encode("utf-8")).hexdigest()
    assert keys.path_version(path) == v and keys.path_version(None) is None
    assert keys.document(52713, path) == {"item_kind": "document", "natural_key": f"document:52713:{v}",
                                          "cse_filing_id": 52713, "path_sha256": v}
    assert keys.document(52713, None)["natural_key"] == "document:52713:none"
    assert keys.feed_window(2021, 4) == {"item_kind": "feed_window", "natural_key": "feed_window:2021-04",
                                         "window_month": date(2021, 4, 1)}
    assert keys.feed_window_dates(date(2024, 2, 1)) == ("2024-02-01", "2024-02-29")
    assert keys.feed_window_dates(date(2021, 12, 1)) == ("2021-12-01", "2021-12-31")
    assert keys.listing("COMB.N0000")["natural_key"] == "listing:COMB.N0000"
    assert keys.link_pass(1)["natural_key"] == "link_pass:1" and keys.audit(3)["natural_key"] == "audit:3"
    run = "0b5b9c1e-2a7d-4c5e-9f00-1234567890ab"
    assert keys.validate(run.upper())["natural_key"] == f"validate:{run}"
    assert keys.reconcile()["natural_key"] == "reconcile:all_issuers"
    assert keys.reconcile(run)["natural_key"] == f"reconcile:{run}"
    for bad in (lambda: keys.feed_window(2021, 13), lambda: keys.listing("comb.n0000"), lambda: keys.listing("A B"),
                lambda: keys.document(0, "x"), lambda: keys.document(1, b"x"), lambda: keys.link_pass(0),
                lambda: keys.validate("not-a-uuid"), lambda: keys.audit(True)):
        with pytest.raises(keys.InvalidKey):
            bad()
    assert f"query_symbol ~ '{keys.SYMBOL_RE.pattern}'" in sql0016()          # the same symbol rule in both places
    assert keys.SUBJECT_COLUMNS == tuple(c for c in store.ITEM_COLUMNS if c in keys.SUBJECT_COLUMNS)


def test_u5_colombo_days_and_budget_accounting():
    utc = timezone.utc
    assert keys.colombo_day_bounds(date(2026, 10, 2)) == (datetime(2026, 10, 1, 18, 30, tzinfo=utc),
                                                          datetime(2026, 10, 2, 18, 30, tzinfo=utc))
    assert keys.colombo_date(datetime(2026, 10, 1, 18, 29, 59, tzinfo=utc)) == date(2026, 10, 1)
    assert keys.colombo_date(datetime(2026, 10, 1, 18, 30, tzinfo=utc)) == date(2026, 10, 2)
    with pytest.raises(ValueError):
        keys.colombo_date(datetime(2026, 10, 1, 12, 0))                    # never from a naive time
    disarmed = keys.budget_status(None, 0, 0)
    assert disarmed["armed"] is False and disarmed["remaining"] == 0 and disarmed["exhausted"] is True
    armed = {"armed": True, "daily_request_budget": 600, "combined_daily_ceiling": 800}
    assert keys.budget_status(armed, 599, 0)["remaining"] == 1 and not keys.budget_status(armed, 599, 0)["exhausted"]
    assert keys.budget_status(armed, 600, 0)["exhausted"]
    combined = keys.budget_status(armed, 599, 201)
    assert combined["combined_remaining"] == 0 and combined["exhausted"]
    assert keys.budget_status({"armed": False, "daily_request_budget": 600}, 0, 0)["daily_request_budget"] == 0


# ------------------------------------------------------------------------------------------------ F2 retrieval records

def f2_record(**over):
    rec = RetrievalRecord(cse_filing_id=52713, role="primary",
                          source_path="cmt/upload_report_file/771_1653995188923.pdf", outcome="succeeded",
                          strategy="direct", final_url="https://cdn.cse.lk/cmt/upload_report_file/771_1653995188923.pdf",
                          http_status=200, content_type="application/pdf", content_length_header="1234",
                          etag='"' + "b" * 32 + '"', last_modified="Wed, 01 Oct 2026 10:00:00 GMT", byte_length=1234,
                          sha256="a" * 64, md5="b" * 32,
                          validation={"ok": True, "category": "valid", "warnings": [], "detected_kind": "pdf",
                                      "pdf_version": "1.7", "etag_check": "match"},
                          attempts=[{"strategy": "direct", "url": "https://cdn.cse.lk/cmt/upload_report_file/"
                                     "771_1653995188923.pdf", "status": 200, "error": None, "s3_error_code": None,
                                     "redirects": []}],
                          retrieved_at=datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc).isoformat(),
                          consumer_status="succeeded", cleanup_status="deleted")
    return dataclasses.replace(rec, **over).to_dict()


def test_u6_retrieval_records_keep_metadata_never_bytes_or_temporary_paths():
    assert set(records.F2_FIELDS) == {f.name for f in dataclasses.fields(RetrievalRecord)}
    row = records.retrieval_row(f2_record())
    assert tuple(row) == store.RETRIEVAL_COLUMNS
    assert row["cdn_object_key"] == "cmt/upload_report_file/771_1653995188923.pdf"
    assert row["document_sha256"] == "a" * 64 and row["retrieved_at"].tzinfo is not None
    assert not any(isinstance(v, (bytes, bytearray)) for v in row.values())
    temp = "/tmp/cse_f2_52713_k3j2/document.pdf"
    bad = records.retrieval_row(f2_record(
        outcome="consumer_failed", consumer_status="failed",
        consumer_error=f"TextExtractionError: pdftotext failed on {temp}: exit 1",
        attempts=[{"strategy": "direct", "url": "https://cdn.cse.lk/x.pdf", "status": 200,
                   "error": f"OSError: [Errno 28] No space left on device: '{temp}'", "s3_error_code": None,
                   "redirects": []}]), temp_roots=["/srv/cse-backfill-tmp"])
    assert bad["consumer_error_class"] == "TextExtractionError"
    assert "cse_f2_" not in repr(bad) and records.REDACTED in bad["consumer_error"]
    assert records.REDACTED in bad["attempts"][0]["error"]
    root = records.retrieval_row(f2_record(outcome="cleanup_failed", cleanup_status="failed",
                                           cleanup_error="could not delete /srv/cse-backfill-tmp/x: busy"),
                                 temp_roots=["/srv/cse-backfill-tmp"])
    assert "/srv/cse-backfill-tmp" not in root["cleanup_error"]
    assert len(records.redact_temporary("x" * 900)) == records.MAX_ERROR
    for over, match in (({"role": "companion"}, "primary"), ({"source_path": None}, "excluded"),
                        ({"consumer_error": "boom", "consumer_status": "failed"}, "F2 consumer error")):
        with pytest.raises(ValueError, match=match):
            records.retrieval_row(f2_record(**over))
    with pytest.raises(ValueError, match="not an F2 RetrievalRecord"):
        records.retrieval_row(dict(f2_record(), bytes=b"%PDF-"))


# ------------------------------------------------------------------------------------------------ owner decisions

def armed_decision(**over):
    d = dict(armed=True, note="owner pilot arming (test values only)", armed_stages=("HB-S2",),
             window_first_date=date(2021, 4, 1), window_last_date=date(2026, 9, 30), daily_request_budget=600,
             combined_daily_ceiling=800, slice_max_json_requests=30, slice_max_documents=10, slice_max_seconds=600,
             attempts_per_json_request=3, attempts_per_document=2, item_max_attempts=3,
             user_agent="test-agent/1.0 (contact configured on the server)", host="test-host",
             version_tuple={"issuer_rule_version": "f5.issuer.2"}, expected_requests={"feed": 66},
             stop_conditions=("any block",), g1_reference="G-1 accepted risk (test)")
    d.update(over)
    return owner.ArmingDecision(**d)


def test_u7_the_arming_decision_mirrors_the_migrations_checks():
    sql = sql0016()
    assert check_values(sql, "chk_bfad_stages") == list(owner.STAGES)
    assert "attempts_per_json_request between 1 and 5 and attempts_per_document between 1 and 5" in sql
    assert owner.ATTEMPT_BOUNDS == (1, 5) and "length(btrim(note)) >= 10" in sql and owner.NOTE_MIN == 10
    assert check_values(sql, "chk_bfhr_resolution") == list(owner.HOLD_RESOLUTIONS)
    armed_decision().validate()
    owner.ArmingDecision.disarm("owner disarm: pilot paused").validate()
    for over in ({"armed_stages": ()}, {"armed_stages": ("HB-S9",)}, {"note": "short"},
                 {"window_last_date": date(2021, 3, 31)}, {"daily_request_budget": 0}, {"attempts_per_document": 6},
                 {"user_agent": " "}, {"g1_reference": None}, {"stop_conditions": ()}, {"version_tuple": {}},
                 {"slice_max_seconds": None}, {"item_max_attempts": True}):
        with pytest.raises(owner.InvalidDecision):
            armed_decision(**over).validate()
    with pytest.raises(owner.InvalidDecision):
        owner.ArmingDecision(armed=False, note="disarm with a stage", armed_stages=("HB-S2",)).validate()


# ------------------------------------------------------------------------------------------------ migration 0016

def test_u8_migration_0016_meets_every_frozen_migration_guard():
    sql = sql0016()
    low = sql.lower()
    # F2's zero-document-archive guard (tests/test_document_retrieval.py), exactly
    assert "bytea" not in low and not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+"
                                                r"(text|varchar)", low)
    # the design's stricter rule (HB-B8): no column named like *path at all
    assert not re.search(r"^\s+\w*path\s+(text|varchar)", low, re.M)
    assert "security definer" not in low and "row level security" not in low
    assert not re.search(r"^\s*(alter|drop)\s", low, re.M)                    # additive only
    assert low.index("if current_user <> 'cse_owner'") < low.index("create function")
    functions = re.findall(r"^create function (\w+)\(", sql, re.M)
    assert len(functions) == len(HELPER_FUNCTIONS) + len(TRIGGER_FUNCTIONS) == 15
    assert all(f.startswith("hb_") for f in functions)
    assert not [f for f in functions if "delete" in f or "purge" in f]        # P2's no-purge-routine test
    assert {f.split("(")[0] for f in HELPER_FUNCTIONS + TRIGGER_FUNCTIONS} == set(functions)
    tables = re.findall(r"^create table (\w+)", sql, re.M)
    assert sorted(tables) == sorted(preflight.TABLES) and len(tables) == 16
    assert sorted(re.findall(r"^create view (\w+)", sql, re.M)) == sorted(preflight.VIEWS)
    triggers = {}
    for name, table in re.findall(r"^create trigger (\w+) before [a-z ]+ on (\w+)", sql, re.M):
        triggers.setdefault(table, set()).add(name)
    assert triggers == {t: set(n) for t, n in preflight.TRIGGERS.items()} and sum(map(len, triggers.values())) == 42
    for t in tables:                                                         # append-only for every role
        assert re.search(rf"before truncate on {t}\n", sql), t
        if t in preflight.WORKER_GUARDED_UPDATE:                             # heartbeat / release / expiry, guarded
            pattern = rf"before insert or update or delete on {t}\n"
        elif t == "backfill_item_transitions":                               # the rule data admits no change at all
            pattern = rf"before insert or update or delete on {t}\n  for each row execute function hb_rule_data_guard"
        else:
            pattern = rf"before update or delete on {t}\n  for each row execute function f5_reject_mutation"
        assert re.search(pattern, sql), t
    grants = sql[sql.index("-- Privileges: PUBLIC nothing"):]
    assert not re.search(r"grant (all|delete|truncate|[a-z, ]*(delete|truncate))", grants)
    worker_insert = grants[grants.index("grant select, insert on"):grants.index("to cse_worker;", grants.index(
        "grant select, insert on"))]
    assert not any(t in worker_insert for t in preflight.OWNER_DECISION_TABLES)
    execute = grants[grants.index("grant execute on function"):]
    execute = execute[:execute.index(";")]
    assert sorted(re.findall(r"(hb_\w+)\(", execute)) == sorted(f.split("(")[0] for f in HELPER_FUNCTIONS)


def test_u9_the_migration_lineage_is_frozen_through_0015_and_0016_follows():
    found = mig.discover(MIGDIR)
    names = [m.filename for m in found]
    i = names.index("0015_financial_truth_persistence.sql")
    assert i == 13 and found[i].sha256 == SHA_0015 and names[i + 1] == LEDGER_MIGRATION
    assert not any(n.startswith("0006") for n in names) and all(n[:4] > "0015" for n in names[i + 1:])
    assert mig.file_sha256(os.path.join(MIGDIR, LEDGER_MIGRATION)) == LEDGER_MIGRATION_SHA256
    good = [(m.filename, m.sha256) for m in found]
    assert preflight.lineage_problems(good) == []
    sixth = ("0006_local_security.sql", "c" * 64)
    cases = {
        "0006": good[:5] + [sixth] + good[5:],
        "0015 hash": good[:13] + [(good[13][0], "0" * 64)] + good[14:],
        "0015 moved": good[:12] + [good[13], good[12]] + good[14:],
        "lower after 0015": good + [("0012_late.sql", "d" * 64)],
        "0016 changed": good[:14] + [(LEDGER_MIGRATION, "e" * 64)] + good[15:],
        "0016 missing": good[:14],
    }
    for name, entries in cases.items():
        assert preflight.lineage_problems(entries), name


def test_u10_frozen_pins_versions_and_drift_detection(tmp_path):
    assert preflight.file_problems() == [] and preflight.version_problems() == []
    assert list(preflight.FROZEN_MIGRATIONS)[-1] == "0015_financial_truth_persistence.sql"
    assert len(preflight.FROZEN_MIGRATIONS) == 14 and preflight.FROZEN_MIGRATIONS[
        "0015_financial_truth_persistence.sql"] == SHA_0015
    # a copy of the checkout with ONE frozen file changed by one byte is refused
    copy = tmp_path / "repo"
    shutil.copytree(MIGDIR, copy / "supabase" / "migrations")
    for rel in preflight.FROZEN_FILES:
        dst = copy / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(REPO, rel), dst)
    assert preflight.file_problems(str(copy)) == []
    with open(copy / "worker" / "issuer_identity.py", "a", encoding="utf-8") as f:
        f.write("\n")
    assert preflight.file_problems(str(copy)) == ["frozen file worker/issuer_identity.py changed (HB-B9: the "
                                                  "backfill runs exactly the frozen code)"]


def test_u11_the_package_is_the_ledger_only_offline_and_inside_the_frozen_test_boundaries(tmp_path):
    files = sorted(f for f in os.listdir(PACKAGE) if f.endswith(".py"))
    assert files == ["__init__.py", "keys.py", "owner.py", "preflight.py", "records.py", "states.py", "store.py"]
    sources = {f: open(os.path.join(PACKAGE, f), encoding="utf-8").read() for f in files}
    for f, src in sources.items():
        assert "rdv_" not in src, f                                          # RDV-B3
        assert not re.findall(r"4_?346_?836_?117_?002_?31\d", src), f         # F6.4 U4: no lock literal
        assert "cse.lk" not in src, f                                        # no CSE address in the ledger code
    assert preflight.network_problems() == []
    planted = tmp_path / "pkg"
    planted.mkdir()
    (planted / "transport.py").write_text("import requests\nfrom urllib import request\n", encoding="utf-8")
    assert preflight.network_problems(str(planted)) == ["transport.py imports requests: the ledger contacts no network",
                                                        "transport.py imports urllib: the ledger contacts no network"]
    # the ledger writes its own tables only: frozen tables keep their frozen writers (HB-B2)
    for f in ("store.py", "owner.py"):
        targets = re.findall(r"(?:insert into|update)\s+(\w+)", sources[f])
        assert targets and all(t.startswith("backfill_") for t in targets), (f, targets)
    assert not os.path.exists(os.path.join(REPO, "ops", "backfill"))         # HB-6, not HB-1
    assert sorted(os.listdir(os.path.join(REPO, "ops", "systemd"))) == sorted(
        n for n in os.listdir(os.path.join(REPO, "ops", "systemd")) if n.startswith("cse-backup-"))
