"""
Planted-fault (mutation) tests for R-F4 (docs/R_F4_IMPLEMENTATION.md sections 18 and 20).

Section 18's mutants plant faults in the changes after `d6924e7`:
    schema.py      D1 and D2 (1252c56)
    reader.py      the name-resolution guard on every public entry point (95da822)
    verify.py      one read-only REPEATABLE READ snapshot and the refusals (95da822), the snapshot's identity, the
                   autocommit refusal and the unmasked failure (144a213), the scope of a limited scan (b89cdec)
    __main__.py    --limit (b89cdec)
    preflight.py   the column check (b89cdec) and the table-property check (493bf73)
Section 20's plant faults in the earlier code that had none in CI:
    M15-M17        F5's _persist committing per filing, the duplicate-key read-back, ON CONFLICT (section 5.7's
                   mutants, written again for this harness)
    store.py       the persisted-F3 check (audit Finding 1, f67c181)
    reader.py      the F3 check before a rebuild (f67c181)
    schema.py      the trigger function's definition and configuration, an extra enabled trigger (f67c181, 2bc66c1),
                   and the name-resolution invariant (d6924e7)
    preflight.py   the constraint definitions (f67c181), the migration ledger and each role's search path (d6924e7)

Each mutant plants one fault: one exact substitution, found exactly once, in a copy of the repository. Its named tests
then run in a fresh pytest process against the copy, which writes a JUnit report. The mutant is KILLED only when that
process ends with test failures (exit 1), no test errored, and every named test failed with its intended reason (a
regex that its failure's message and traceback must match). Anything else is not a kill:
    SURVIVED      a named test passed
    INVALID       a timeout, no report, a setup or teardown error, a collection, usage or internal error, a named
                  test that did not run or was skipped, or a named test that failed for another reason
    INAPPLICABLE  the substitution's text is not in its file exactly once
A control runs every named test against the unchanged copy first, and all must pass, so a kill is the fault's and not
the environment's. The copy is restored after each mutant, and no bytecode is written, so a stale .pyc never stands in
for a restored file. Each child run has 10 minutes, and its PostgreSQL sessions a one-minute lock timeout (PGOPTIONS);
a server a child leaves running (one killed at its timeout never cleans up) is stopped. So a hang fails its mutant
instead of the whole group, and leaves nothing behind.

The planted runs need PostgreSQL 17 (P1_PG_BINDIR, Linux) and RF4_MUTANTS=1. CI's group X2 sets both
(.github/ci/groups.py, .github/ci/linux_tests.sh); elsewhere they skip. They never change the working tree, and their
child processes run the R-F4 suites, which allow only Unix-socket connections. The other tests run everywhere: every
substitution still applies to the current code, every named test still exists, and the kill rule and the report reader
behave as above.

    RF4_MUTANTS=1 P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_mutants.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PLANTED = pytest.mark.skipif(os.environ.get("RF4_MUTANTS") != "1" or not os.environ.get("P1_PG_BINDIR")
                             or os.name != "posix",
                             reason="planted runs: RF4_MUTANTS=1 and P1_PG_BINDIR (Linux, PostgreSQL 17); CI group X2")
U = "tests/test_rf4_unit.py::"
P = "tests/test_rf4_postgres.py::"
PKG = "worker/financial_f4_store/"
F5 = "worker/extract_financial_candidates.py"
# pytest 7 says DID NOT RAISE <class 'module.Name'>, pytest 8 and later DID NOT RAISE Name
NOT_RAISED = r"DID NOT RAISE (?:<class ')?(?:[\w.]+\.)?{}\b"

# (mutant, the change it reverts, file, exact text, its replacement,
#  {each test that must fail: its intended reason, a regex its failure must match})
MUTANTS = [
    # ------------------------------------------------------------------------- section 18: the changes after d6924e7
    ("MS1", "1252c56 D1: only PostgreSQL's whitespace separates names", PKG + "schema.py",
     'PG_SPACE = " \\t\\n\\r\\v\\f"', 'PG_SPACE = " \\t\\n\\r\\v\\f\\xa0"',
     {U + "test_u21_only_postgresqls_whitespace_separates_names": r"== \['public\\xa0', 'public'\]",
      P + "test_p36_a_raw_server_value_with_a_no_break_space_names_a_schema_searched_before_public":
          NOT_RAISED.format("SchemaUnavailable")}),
    ("MS2", "1252c56 D2: current_schemas() is not read where it would create a temporary schema",
     PKG + "schema.py", "OPERATOR(pg_catalog.=) 0", "OPERATOR(pg_catalog.>=) 0",
     {P + "test_p39_a_path_listing_pg_temp_first_is_judged_without_creating_a_temporary_schema":
          "permission denied to create temporary tables"}),
    ("MR1", "95da822: the reader's guard refuses an unsafe session", PKG + "reader.py",
     "    if problems:\n", "    if False:\n",
     {U + "test_u22_every_public_reader_entry_point_checks_the_session_before_its_first_unqualified_query":
          r"reader\.py:\d+: in _extraction_row.*TypeError: argument 2 must be a connection, cursor or None",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader":
          "missing: no F4 extraction"}),
    ("MR2", "95da822: every public reader entry point checks (load_classification's check dropped)",
     PKG + "reader.py", "    require_safe_resolution(conn)\n    return _load_classification(conn, classification_id)",
     "    return _load_classification(conn, classification_id)",
     {U + "test_u22_every_public_reader_entry_point_checks_the_session_before_its_first_unqualified_query":
          r"reader\.py:\d+: in _load_classification.*TypeError: argument 2 must be a connection, cursor or None",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader":
          "missing: no F3 classification"}),
    ("MV1", "95da822: verify refuses an unsafe session before any unqualified query", PKG + "verify.py",
     "        if unsafe:\n", "        if False:\n",
     {U + "test_u22_verify_refuses_an_unsafe_session_before_any_unqualified_query":
          r"from financial_f4_extractions order by id', 'select id from financial_extraction_runs order by id'\] "
          r"== \[\]",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader":
          "'clean': True"}),
    ("MV2", "95da822: verify's transaction is REPEATABLE READ", PKG + "verify.py",
     '"set transaction isolation level repeatable read, read only"',
     '"set transaction isolation level read committed, read only"',
     {U + "test_u22_verify_sets_one_read_only_repeatable_read_snapshot_and_checks_the_session_before_reading":
          "'set transaction isolation level read committed, read only' == "
          "'set transaction isolation level repeatable read, read only'",
      P + "test_p42_a_safe_path_verifies_in_one_read_only_repeatable_read_transaction":
          "verify's transaction is read committed"}),
    ("MV3", "95da822: a refusal during verify is reported, never clean", PKG + "verify.py",
     '        report["problems"].append({"kind": "name_resolution", "detail": exc.detail,',
     '        [].append({"kind": "name_resolution", "detail": exc.detail,',
     {U + "test_u22_a_refusal_during_verify_ends_it_unclean":
          r'assert not report\["clean"\] and report\["extractions"\] == 1\s*\nE\s+assert \(?not True\b'}),
    ("MV4", "144a213: an autocommit connection is refused", PKG + "verify.py",
     "    if conn.autocommit:\n", "    if False:\n",
     {U + "test_u23_an_autocommit_connection_is_refused_before_any_statement": NOT_RAISED.format("ValueError"),
      P + "test_p45_an_autocommit_session_is_refused_before_verify_sends_a_statement":
          "verify ran on an autocommit connection"}),
    ("MV5", "144a213: a failing rollback does not mask the failure", PKG + "verify.py",
     "    except Exception as exc:\n        failure.add_note(",
     "    except ArithmeticError as exc:\n        failure.add_note(",
     {U + "test_u23_a_failure_is_raised_as_itself_when_the_rollback_fails_too":
          "InterfaceError: connection already closed",
      P + "test_p46_a_session_ended_mid_run_raises_its_own_error_not_the_rollbacks":
          r"InterfaceError\('connection already closed'\)"}),
    ("MV6", "144a213: the report names the snapshot by its identity, not a time", PKG + "verify.py",
     '"pg_snapshot": pg_snapshot,', '"pg_snapshot": started.isoformat(),',
     {U + "test_u23_the_snapshot_identity_and_the_transaction_start_are_recorded_apart_and_named_for_what_they_are":
          r"'pg_snapshot': '2026-10-20T00:00:00\+00:00'",
      P + "test_p44_every_statement_of_verify_runs_under_the_snapshot_its_report_names":
          r"Extra items in the right set:\s*(?:E\s+)?\('\d{4}-\d\d-\d\dT"}),
    ("MV7", "b89cdec: a partial scan is never clean", PKG + "verify.py",
     'report["clean"] = scope["complete"] and not report["problems"]', 'report["clean"] = not report["problems"]',
     {U + "test_u24_a_limited_scan_is_partial_and_never_clean_even_when_it_finds_nothing":
          r"\[\] == \[\].*?and True is False\)",         # pytest 9 puts a diff between the two halves
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1":
          r"\(3, 3, \[\], True\) == \(3, 3, \[\], False\)"}),
    ("MV8", "b89cdec: a limit below 1 is refused", PKG + "verify.py", "or limit < 1:", "or limit < 0:",
     {U + "test_u24_any_other_limit_is_refused_before_any_statement": NOT_RAISED.format("ValueError"),
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1": NOT_RAISED.format("ValueError")}),
    ("MC1", "b89cdec: the command's --limit below 1 is a usage error", PKG + "__main__.py",
     "    if n < 1:\n", "    if n < 0:\n",
     {U + "test_u24_the_commands_limit_must_be_an_integer_of_at_least_1_before_any_connection":
          "Failed: the command connected",
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1":
          "ValueError: verify's limit must be None"}),
    ("MP1", "b89cdec: a dropped NOT NULL is reported", PKG + "preflight.py",
     "        if got_not_null != not_null:\n", "        if False:\n",
     {U + "test_u25_the_preflight_reports_every_kind_of_column_drift":
          r"\[\] == \['financial_f4_extractions: column f4_content_sha256 is nullable, expected NOT NULL'\]",
      P + "test_p50_the_preflight_reports_column_drift_that_no_constraint_shows": r"drop not null', \[\]\)"}),
    ("MP2", "b89cdec: the object check reads every table's columns", PKG + "preflight.py",
     "                out += _column_problems(t, cur.fetchall())\n", "                out += []\n",
     {P + "test_p50_the_preflight_reports_column_drift_that_no_constraint_shows": r"drop not null', \[\]\)"}),
    ("MP3", "493bf73: row-level security is reported", PKG + "preflight.py", "    if rls:\n", "    if False:\n",
     {U + "test_u26_every_table_property_drift_is_reported":
          r"\[\] == \['financial_f4_cells has row-level security enabled",
      P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift":
          r"\('alter table financial_f4_cells enable row level security', \[\]\)",
      P + "test_p52_row_level_security_can_hide_evidence_from_verify_and_the_preflight_refuses_it":
          r"AssertionError: \['financial_extraction_runs has row-level security policy\(ies\) rf4_hide; expected "
          r"none'\]"}),
    ("MP4", "493bf73: the F3 and F5 tables verify reads are checked too", PKG + "preflight.py",
     "for t in codec.TABLES + READ_TABLES:", "for t in codec.TABLES:",
     {P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift":
          r"\('alter table financial_extraction_runs enable row level security', \[\]\)",
      P + "test_p52_row_level_security_can_hide_evidence_from_verify_and_the_preflight_refuses_it":
          r"assert \[\] == \['financial_extraction_runs has row-level security enabled"}),
    ("MP5", "493bf73: a table that is not permanent is reported", PKG + "preflight.py",
     '    if persistence != "p":\n', "    if False:\n",
     {U + "test_u26_every_table_property_drift_is_reported": r"\[\] == \['financial_f4_cells is UNLOGGED",
      P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift":
          r"\('alter table financial_f4_cells set unlogged', \[\]\)"}),
    # ------------------------------------------------- section 20: earlier code, and section 5.7's M15-M17 in CI
    ("M15", "design section 6: run()'s batch commits once, at its end (section 5.7's M15: _persist commits each "
     "filing)", F5,
     '    status, run_id = stores["candidates"].save(got["result"], cid, link)\n',
     '    status, run_id = stores["candidates"].save(got["result"], cid, link)\n    stores["conn"].commit()\n',
     {P + "test_p21_a_failure_at_filing_k_leaves_runs_batch_uncommitted_and_a_rerun_saves_every_filing_once":
          r"\(1, 1, 1\) == \(0, 0, 0\)"}),
    ("M16", "8f1f9fd: a duplicate key is already_present only once the saved extraction reads back intact",
     PKG + "store.py", "                    reader.load_extraction(self.conn, str(existing[0]))\n",
     "                    str(existing[0])\n",
     {P + "test_p17_a_rerun_is_already_present_only_when_the_saved_extraction_reads_back_intact":
          NOT_RAISED.format("EvidenceRefused")}),
    ("M17", "77cfb84: the insert skips an existing key (ON CONFLICT DO NOTHING)", PKG + "store.py",
     'on conflict (cse_filing_id, document_sha256, "\n'
     '                            "word_extractor, f4_extractor_version, classification_id) do nothing returning id"',
     'returning id"',
     {P + "test_p7_a_rerun_is_already_present_and_different_content_is_refused": "UniqueViolation",
      P + "test_p10_concurrent_writers_of_one_key_leave_one_set_of_rows": "UniqueViolation",
      P + "test_p24_two_concurrent_runs_of_one_filing_leave_one_set_of_rows_and_no_deadlock": "UniqueViolation"}),
    ("MF1", "f67c181 Finding 1: the store refuses an F3 row persisted with content other than F5 consumed",
     PKG + "store.py", '                if persisted != row["f3_input_sha256"]:\n', "                if False:\n",
     {P + "test_p25_a_persisted_f3_other_than_the_one_f5_consumed_refuses_the_save_and_verify_reports_history":
          NOT_RAISED.format("ConflictingEvidence"),
      P + "test_p25_run_with_a_differing_persisted_f3_raises_and_leaves_its_whole_batch_uncommitted":
          NOT_RAISED.format("ConflictingEvidence")}),
    ("MR3", "f67c181 Finding 1 (in this form since 95da822): a rebuild refuses F3 evidence other than the input F5 "
     "consumed", PKG + "reader.py",
     '    if codec.f3_input_sha256(cls) != _extraction_row(conn, eid)["f3_input_sha256"]:\n', "    if False:\n",
     {P + "test_p25_a_persisted_f3_other_than_the_one_f5_consumed_refuses_the_save_and_verify_reports_history":
          "Extra items in the right set"}),
    ("MT1", "f67c181 Finding 2: a trigger function defined other than in its migration is refused", PKG + "schema.py",
     '        if (got["language"], got["security_definer"], got["body_sha256"]) != (language, False, body):\n',
     "        if False:\n",
     {U + "test_u18_a_trigger_is_judged_by_its_functions_identity_and_definition_and_by_its_own_properties":
          r"not enough values to unpack \(expected 1, got 0\)",
      P + "test_p26_a_trigger_altered_beyond_its_name_is_refused_before_any_insert_and_reported":
          NOT_RAISED.format("SchemaUnavailable")}),
    ("MT2", "2bc66c1 Finding 4: a trigger function's configuration is compared with its migration's",
     PKG + "schema.py", '        if tuple(got["config"] or ()) != config:\n', "        if False:\n",
     {U + "test_u19_a_trigger_functions_configuration_is_compared_with_its_migrations":
          r'\[\] == \["financial_f4_extractions: trigger trg_ff4e_guard runs '
          r'public\.financial_f4_extraction_guard\(\) configured \[\'search_path=rf4_other, public\'\]',
      P + "test_p28_a_search_path_set_on_the_guard_function_is_refused_by_the_store_and_the_preflight":
          "is not of filing|" + NOT_RAISED.format("SchemaUnavailable")}),
    ("MT3", "2bc66c1 Finding 5: an enabled trigger 0018 does not create is refused", PKG + "schema.py",
     "        for trigger in sorted(set(live) - set(TRIGGERS[name])):\n", "        for trigger in []:\n",
     {U + "test_u19_an_enabled_trigger_0018_does_not_create_is_a_problem":
          r"\[\] == \['financial_f4_extractions: trigger trg_ff4e_zz_after_the_guard \(runs public\.rf4_rewrite, "
          r"tgtype 7\) is enabled and is not one migration 0018 creates'\]",
      P + "test_p29_an_enabled_trigger_0018_does_not_create_is_refused_by_the_store_and_reported_by_the_preflight":
          NOT_RAISED.format("SchemaUnavailable")}),
    ("MP6", "f67c181 Finding 3: a constraint's definition is compared with 0018's", PKG + "preflight.py",
     '        if got_definition.removesuffix(" NOT VALID") != definition:\n', "        if False:\n",
     {U + "test_u18_the_preflight_reports_a_replaced_weakened_unvalidated_missing_or_extra_constraint":
          r'\[\] == \["financial_f4_cells: constraint chk_ff4c_decimal is CHECK \(true\), expected CHECK',
      P + "test_p27_the_preflight_verifies_each_constraint_by_type_and_definition":
          r"constraint chk_ff4c_decimal is CHECK [^\n]*[\"'], \[\]\)"}),
    ("MP7", "dc6302d (Stage 1): the migration ledger records 0018 at its pin", PKG + "preflight.py",
     "    elif rows != [(MIGRATION_VERSION, MIGRATION, MIGRATION_SHA256)]:\n", "    elif False:\n",
     {P + "test_p13_a_tampered_or_misnumbered_ledger_entry_fails": "'verified' == 'failed'"}),
    ("MN1", "d6924e7: a schema searched before public breaks the invariant", PKG + "schema.py",
     '        out.append(f"schema {_shown(name)} is searched before public',
     '        [].append(f"schema {_shown(name)} is searched before public',
     {U + "test_u20_the_name_resolution_invariant":
          r"\[\] == \['schema rf4_shadow is searched before public, whether or not it exists or holds a table now'\]",
      P + "test_p31_a_database_level_search_path_is_refused_even_before_its_schema_holds_a_table_or_exists":
          "Regex pattern did not match|" + NOT_RAISED.format("SchemaUnavailable")}),
    ("MN2", "d6924e7: pg_catalog after public breaks the invariant", PKG + "schema.py",
     '    if "pg_catalog" in elements[i + 1:] and "pg_catalog" not in elements[:i]:\n', "    if False:\n",
     {U + "test_u20_the_name_resolution_invariant":
          r'\[\] == \["pg_catalog comes after public, so public\'s operators and functions come before the '
          r'built-in ones"\]',
      P + "test_p38_a_real_operator_in_public_ahead_of_pg_catalog_subverts_the_guard_and_is_refused_and_reported":
          NOT_RAISED.format("SchemaUnavailable")}),
    ("MN3", "d6924e7: relations in the session's temporary schema are refused", PKG + "schema.py",
     "    if temp_objects:\n", "    if False:\n",
     {U + "test_u20_the_in_band_check_reads_the_sessions_own_resolution":
          r'\[\] == \["this session\'s temporary schema pg_temp_3 holds 2 relation\(s\) or type\(s\)',
      P + "test_p33_a_temporary_table_searched_before_public_is_refused":
          "missing: no F3 classification|is not of filing|" + NOT_RAISED.format("SchemaUnavailable")}),
    ("MN4", "d6924e7: the preflight judges each role's configured search path", PKG + "preflight.py",
     '    return [f"{role}: the search_path its sessions get ({source}: {value!r}): {problem}"\n',
     '    return [] and [f"{role}: the search_path its sessions get ({source}: {value!r}): {problem}"\n',
     {U + "test_u20_the_preflight_reads_a_roles_search_path_in_postgresqls_precedence":
          r'\[\] == \["cse_worker: the search_path its sessions get \(this database\'s setting \(ALTER DATABASE '
          r'\.\.\. SET\): \'rf4_shadow, public\'\): schema rf4_shadow is searched before public',
      P + "test_p30_a_role_level_search_path_ahead_of_public_is_refused_by_the_store_and_reported_by_the_preflight":
          r"\[\] == \[[\"']cse_worker: the search_path its sessions get"}),
]
KILLERS = sorted({k for m in MUTANTS for k in m[5]})
CONTROL = {}


def test_every_mutation_applies_to_the_current_code_and_names_existing_tests():
    """Runs everywhere: each substitution's text is in its file exactly once and its replacement differs; each named
    test is defined and has a reason that compiles; the mutant ids are distinct. A code change that moves a mutation
    point fails here, visibly, instead of letting a mutant silently apply nowhere."""
    assert len({m[0] for m in MUTANTS}) == len(MUTANTS)
    for mid, _, path, old, new, killers in MUTANTS:
        text = open(os.path.join(REPO, path), encoding="utf-8").read().replace("\r\n", "\n")
        assert text.count(old) == 1 and old != new, (mid, path, text.count(old))
        assert killers and all(reason and re.compile(reason) for reason in killers.values()), mid
    for killer in KILLERS:
        path, name = killer.split("::")
        src = open(os.path.join(REPO, path), encoding="utf-8").read()
        assert re.search(rf"^def {re.escape(name)}\(", src, re.M), killer


def read_report(path):
    """{test id: [(outcome, text)]} from a pytest JUnit report, one entry per case (a parametrized test has several);
    outcome is passed, failed, error or skipped, and text a failure's or error's message and traceback. None when
    there is no report."""
    if not os.path.exists(path):
        return None
    out = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        test = (case.get("classname") or "").replace(".", "/") + ".py::" + (case.get("name") or "").split("[")[0]
        got = {c.tag: f"{c.get('message') or ''}\n{c.text or ''}" for c in case}
        outcome = ("error" if "error" in got else "failed" if "failure" in got else
                   "skipped" if "skipped" in got else "passed")
        out.setdefault(test, []).append((outcome, got.get("failure", "") + got.get("error", "")))
    return out


def verdict(code, cases, killers):
    """('killed' | 'survived' | 'invalid', why) for a planted run. code: the child's exit code, None when it timed out;
    cases: read_report's; killers: {test id: the reason its failure must match}. Killed only when the child ended with
    test failures, no test errored, and every named test failed for its reason (one case is enough for a parametrized
    test)."""
    if code is None:
        return "invalid", "the child timed out"
    if cases is None:
        return "invalid", f"the child wrote no report (exit {code})"
    errored = sorted(t for t, got in cases.items() if any(o == "error" for o, _ in got))
    if errored:
        return "invalid", f"a setup or teardown error, not a test failure: {errored}"
    if code not in (0, 1):
        return "invalid", f"exit {code}: a collection, usage or internal error, not test failures"
    survived, other = [], []
    for test, reason in sorted(killers.items()):
        got = cases.get(test)
        if not got:
            return "invalid", f"{test} did not run"
        if any(o == "skipped" for o, _ in got):
            return "invalid", f"{test} was skipped"
        failed = [text for o, text in got if o == "failed"]
        if not failed:
            survived.append(test)
        elif not any(re.search(reason, text, re.S) for text in failed):
            other.append(f"{test}: {failed[0][:300]!r}")
    if survived:
        return "survived", f"passed: {survived}"
    if other:
        return "invalid", f"failed for another reason: {other}"
    return "killed", "every named test failed for its reason"


def control_problems(code, cases, killers):
    """[] when every named test passed on the unchanged copy, each case of it."""
    if code is None:
        return ["the control timed out"]
    if cases is None:
        return [f"the control wrote no report (exit {code})"]
    out = [f"{t}: {[o for o, _ in cases.get(t, [])] or 'did not run'}" for t in killers
           if not cases.get(t) or any(o != "passed" for o, _ in cases[t])]
    return out + ([f"exit {code}"] if code != 0 else [])


def test_the_kill_rule_counts_only_a_failure_for_its_intended_reason():
    """Runs everywhere: a timeout, no report, an error, a collection or usage error, a named test that did not run or
    was skipped, or one that failed for another reason is never a kill; a named test that passed is a survivor."""
    a, b = "t.py::test_a", "t.py::test_b"
    killers = {a: "DID NOT RAISE", b: r"\(1, 1\) == \(0, 0\)"}
    both = {a: [("failed", "Failed: DID NOT RAISE <class 'E'>")], b: [("failed", "assert (1, 1) == (0, 0)")]}
    assert verdict(1, both, killers) == ("killed", "every named test failed for its reason")
    assert verdict(1, {**both, b: [("passed", ""), ("failed", "x\nassert (1, 1) == (0, 0)")]}, killers)[0] == "killed"
    assert verdict(None, both, killers)[0] == "invalid"                                # timed out
    assert verdict(1, None, killers)[0] == "invalid"                                   # no report
    assert verdict(2, both, killers)[0] == "invalid"                                   # interrupted: collection
    assert verdict(4, {}, killers)[0] == "invalid"                                     # usage
    assert verdict(1, {**both, "t.py::test_c": [("error", "fixture")]}, killers)[0] == "invalid"
    assert verdict(1, {a: both[a]}, killers) == ("invalid", f"{b} did not run")
    assert verdict(0, {**both, b: [("skipped", "")]}, killers)[0] == "invalid"
    assert verdict(1, {**both, b: [("failed", "OperationalError: server closed the connection")]}, killers)[0] == \
        "invalid"                                                                      # another reason
    assert verdict(1, {**both, b: [("passed", "")]}, killers) == ("survived", f"passed: {[b]}")
    assert verdict(0, {a: [("passed", "")], b: [("passed", "")]}, killers)[0] == "survived"
    assert control_problems(0, {a: [("passed", "")], b: [("passed", ""), ("passed", "")]}, killers) == []
    assert control_problems(1, {a: [("passed", "")], b: [("failed", "x")]}, killers) == [f"{b}: ['failed']", "exit 1"]
    assert control_problems(0, {a: [("passed", "")]}, killers) == [f"{b}: did not run"]
    assert control_problems(None, None, killers) == ["the control timed out"]


def test_the_report_reader_reads_each_outcome(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_x" name="test_p" />'
        '<testcase classname="tests.test_x" name="test_q[one]"><failure message="assert 1 == 2">trace</failure>'
        '</testcase><testcase classname="tests.test_x" name="test_q[two]" />'
        '<testcase classname="tests.test_x" name="test_r"><error message="fixture">boom</error></testcase>'
        '<testcase classname="tests.test_x" name="test_s"><skipped message="no" /></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    assert read_report(str(report)) == {
        "tests/test_x.py::test_p": [("passed", "")],
        "tests/test_x.py::test_q": [("failed", "assert 1 == 2\ntrace"), ("passed", "")],
        "tests/test_x.py::test_r": [("error", "fixture\nboom")],
        "tests/test_x.py::test_s": [("skipped", "")]}
    assert read_report(str(tmp_path / "none.xml")) is None


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """A copy of the repository (no .git, no bytecode) for the planted runs."""
    dst = tmp_path_factory.mktemp("rf4_mutants") / "repo"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc"))
    return str(dst)


def running_servers():
    """The data directories under /tmp with a running PostgreSQL server's postmaster.pid (the R-F4 suites' clusters
    are /tmp/rf4*/cse_pg_*/data)."""
    if os.name != "posix":
        return set()
    return {os.path.dirname(p) for depth in ("*", "*/*", "*/*/*") for p in glob.glob(f"/tmp/{depth}/postmaster.pid")}


def stop_servers(data_dirs):
    """Stop each server: a child killed at its timeout never runs its fixtures' clean-up."""
    pg_ctl = os.path.join(os.environ.get("P1_PG_BINDIR", ""), "pg_ctl")
    for data in sorted(data_dirs):
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "-w", "stop"], capture_output=True, timeout=60,
                       check=False)


def run_tests(tree, tests):
    """pytest on `tests` in `tree`, in a fresh process; (exit code or None when it timed out, read_report's cases,
    the output's tail). -vv keeps each assertion's full explanation, which the reasons are matched against."""
    # every PostgreSQL session the child opens gives up on a lock after a minute: a test whose clean-up would wait on a
    # lock under a mutant fails instead of holding the whole group until its job limit (the first X2 run, 2026-10-21)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PGOPTIONS=(os.environ.get("PGOPTIONS", "") + " -c lock_timeout=60s").strip())
    env.pop("RF4_MUTANTS", None)
    basetemp = os.path.join(os.path.dirname(tree), "child-basetemp")       # the child's own; never inside the copy
    report = os.path.join(os.path.dirname(tree), "child-report.xml")
    if os.path.exists(report):
        os.remove(report)
    before = running_servers()
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-vv",
                               f"--basetemp={basetemp}", f"--junitxml={report}", *tests], cwd=tree, env=env,
                              capture_output=True, text=True, timeout=600)
        code, out = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out = None, "".join(x.decode(errors="replace") if isinstance(x, bytes) else x
                                  for x in (exc.stdout, exc.stderr) if x)
    finally:
        stop_servers(running_servers() - before)
    return code, read_report(report), out[-3000:]


@PLANTED
def test_control_every_named_test_passes_on_the_unchanged_copy(tree):
    code, cases, tail = run_tests(tree, KILLERS)
    problems = control_problems(code, cases, KILLERS)
    assert not problems, (problems, tail)
    CONTROL["passed"] = True


@PLANTED
@pytest.mark.parametrize("mid, change, path, old, new, killers", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_is_killed_by_its_named_tests_for_their_reasons(tree, record_property, mid, change, path, old, new,
                                                                killers):
    assert CONTROL.get("passed"), "INVALID: the control did not pass, so a kill could not be told from the environment"
    target = os.path.join(tree, path)
    original = open(target, encoding="utf-8").read()
    assert original.count(old) == 1, f"INAPPLICABLE: {mid}'s text is in {path} {original.count(old)} time(s)"
    try:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(original.replace(old, new))
        code, cases, tail = run_tests(tree, sorted(killers))
    finally:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(original)
    outcome, why = verdict(code, cases, killers)
    record_property("rf4_mutant", f"{mid}: {outcome}: {why}")
    assert outcome == "killed", f"{outcome.upper()}: {mid} ({change}): {why}\n{tail}"
