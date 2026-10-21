"""
Planted-fault (mutation) tests for R-F4's changes after `d6924e7` (docs/R_F4_IMPLEMENTATION.md section 18).
M1-M17 (section 5.7), last run at `d6924e7`, target F5's wiring, the store and HB-1's pin closure. None of them reaches
the later changes, which these mutants do:
    schema.py      D1 and D2 (1252c56)
    reader.py      the name-resolution guard on every public entry point (95da822)
    verify.py      one read-only REPEATABLE READ snapshot and the refusals (95da822), the snapshot's identity, the
                   autocommit refusal and the unmasked failure (144a213), the scope of a limited scan (b89cdec)
    __main__.py    --limit (b89cdec)
    preflight.py   the column check (b89cdec) and the table-property check (493bf73)

Each mutant plants one fault: one exact substitution, found exactly once, in a copy of the repository. Its named tests
then run in a fresh pytest process against the copy. The mutant is killed only when that process ends with test
failures (exit 1) and every named test is among them. A control runs every named test against the unchanged copy
first, and all must pass, so a kill is the fault's and not the environment's. The copy is restored after each mutant,
and no bytecode is written, so a stale .pyc never stands in for a restored file. Each child run has 10 minutes, and its
PostgreSQL sessions a one-minute lock timeout (PGOPTIONS), so a hang fails its mutant instead of the whole group.

The planted runs need PostgreSQL 17 (P1_PG_BINDIR, Linux) and RF4_MUTANTS=1. CI's group X2 sets both
(.github/ci/groups.py, .github/ci/linux_tests.sh); elsewhere they skip. They never change the working tree, and their
child processes run the R-F4 suites, which allow only Unix-socket connections. The first test runs everywhere: every
substitution still applies to the current code, and every named test still exists.

    RF4_MUTANTS=1 P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_mutants.py
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PLANTED = pytest.mark.skipif(os.environ.get("RF4_MUTANTS") != "1" or not os.environ.get("P1_PG_BINDIR")
                             or os.name != "posix",
                             reason="planted runs: RF4_MUTANTS=1 and P1_PG_BINDIR (Linux, PostgreSQL 17); CI group X2")
U = "tests/test_rf4_unit.py::"
P = "tests/test_rf4_postgres.py::"
PKG = "worker/financial_f4_store/"

# (mutant, the change it reverts, file, exact text, its replacement, the tests that must fail)
MUTANTS = [
    ("MS1", "1252c56 D1: only PostgreSQL's whitespace separates names", PKG + "schema.py",
     'PG_SPACE = " \\t\\n\\r\\v\\f"', 'PG_SPACE = " \\t\\n\\r\\v\\f\\xa0"',
     [U + "test_u21_only_postgresqls_whitespace_separates_names",
      P + "test_p36_a_raw_server_value_with_a_no_break_space_names_a_schema_searched_before_public"]),
    ("MS2", "1252c56 D2: current_schemas() is not read where it would create a temporary schema",
     PKG + "schema.py", "OPERATOR(pg_catalog.=) 0", "OPERATOR(pg_catalog.>=) 0",
     [P + "test_p39_a_path_listing_pg_temp_first_is_judged_without_creating_a_temporary_schema"]),
    ("MR1", "95da822: the reader's guard refuses an unsafe session", PKG + "reader.py",
     "    if problems:\n", "    if False:\n",
     [U + "test_u22_every_public_reader_entry_point_checks_the_session_before_its_first_unqualified_query",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader"]),
    ("MR2", "95da822: every public reader entry point checks (load_classification's check dropped)",
     PKG + "reader.py", "    require_safe_resolution(conn)\n    return _load_classification(conn, classification_id)",
     "    return _load_classification(conn, classification_id)",
     [U + "test_u22_every_public_reader_entry_point_checks_the_session_before_its_first_unqualified_query",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader"]),
    ("MV1", "95da822: verify refuses an unsafe session before any unqualified query", PKG + "verify.py",
     "        if unsafe:\n", "        if False:\n",
     [U + "test_u22_verify_refuses_an_unsafe_session_before_any_unqualified_query",
      P + "test_p40_an_unsafe_reader_path_is_refused_before_a_shadow_can_change_verify_or_the_reader"]),
    ("MV2", "95da822: verify's transaction is REPEATABLE READ", PKG + "verify.py",
     '"set transaction isolation level repeatable read, read only"',
     '"set transaction isolation level read committed, read only"',
     [U + "test_u22_verify_sets_one_read_only_repeatable_read_snapshot_and_checks_the_session_before_reading",
      P + "test_p42_a_safe_path_verifies_in_one_read_only_repeatable_read_transaction"]),
    ("MV3", "95da822: a refusal during verify is reported, never clean", PKG + "verify.py",
     '        report["problems"].append({"kind": "name_resolution", "detail": exc.detail,',
     '        [].append({"kind": "name_resolution", "detail": exc.detail,',
     [U + "test_u22_a_refusal_during_verify_ends_it_unclean"]),
    ("MV4", "144a213: an autocommit connection is refused", PKG + "verify.py",
     "    if conn.autocommit:\n", "    if False:\n",
     [U + "test_u23_an_autocommit_connection_is_refused_before_any_statement",
      P + "test_p45_an_autocommit_session_is_refused_before_verify_sends_a_statement"]),
    ("MV5", "144a213: a failing rollback does not mask the failure", PKG + "verify.py",
     "    except Exception as exc:\n        failure.add_note(",
     "    except ArithmeticError as exc:\n        failure.add_note(",
     [U + "test_u23_a_failure_is_raised_as_itself_when_the_rollback_fails_too",
      P + "test_p46_a_session_ended_mid_run_raises_its_own_error_not_the_rollbacks"]),
    ("MV6", "144a213: the report names the snapshot by its identity, not a time", PKG + "verify.py",
     '"pg_snapshot": pg_snapshot,', '"pg_snapshot": started.isoformat(),',
     [U + "test_u23_the_snapshot_identity_and_the_transaction_start_are_recorded_apart_and_named_for_what_they_are",
      P + "test_p44_every_statement_of_verify_runs_under_the_snapshot_its_report_names"]),
    ("MV7", "b89cdec: a partial scan is never clean", PKG + "verify.py",
     'report["clean"] = scope["complete"] and not report["problems"]', 'report["clean"] = not report["problems"]',
     [U + "test_u24_a_limited_scan_is_partial_and_never_clean_even_when_it_finds_nothing",
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1"]),
    ("MV8", "b89cdec: a limit below 1 is refused", PKG + "verify.py", "or limit < 1:", "or limit < 0:",
     [U + "test_u24_any_other_limit_is_refused_before_any_statement",
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1"]),
    ("MC1", "b89cdec: the command's --limit below 1 is a usage error", PKG + "__main__.py",
     "    if n < 1:\n", "    if n < 0:\n",
     [U + "test_u24_the_commands_limit_must_be_an_integer_of_at_least_1_before_any_connection",
      P + "test_p49_a_limited_verify_is_partial_never_clean_and_the_command_exits_1"]),
    ("MP1", "b89cdec: a dropped NOT NULL is reported", PKG + "preflight.py",
     "        if got_not_null != not_null:\n", "        if False:\n",
     [U + "test_u25_the_preflight_reports_every_kind_of_column_drift",
      P + "test_p50_the_preflight_reports_column_drift_that_no_constraint_shows"]),
    ("MP2", "b89cdec: the object check reads every table's columns", PKG + "preflight.py",
     "                out += _column_problems(t, cur.fetchall())\n", "                out += []\n",
     [P + "test_p50_the_preflight_reports_column_drift_that_no_constraint_shows"]),
    ("MP3", "493bf73: row-level security is reported", PKG + "preflight.py", "    if rls:\n", "    if False:\n",
     [U + "test_u26_every_table_property_drift_is_reported",
      P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift",
      P + "test_p52_row_level_security_can_hide_evidence_from_verify_and_the_preflight_refuses_it"]),
    ("MP4", "493bf73: the F3 and F5 tables verify reads are checked too", PKG + "preflight.py",
     "for t in codec.TABLES + READ_TABLES:", "for t in codec.TABLES:",
     [P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift",
      P + "test_p52_row_level_security_can_hide_evidence_from_verify_and_the_preflight_refuses_it"]),
    ("MP5", "493bf73: a table that is not permanent is reported", PKG + "preflight.py",
     '    if persistence != "p":\n', "    if False:\n",
     [U + "test_u26_every_table_property_drift_is_reported",
      P + "test_p51_the_preflight_refuses_row_level_security_persistence_and_inheritance_drift"]),
]
KILLERS = sorted({k for m in MUTANTS for k in m[5]})
CONTROL = {}


def test_every_mutation_applies_to_the_current_code_and_names_existing_tests():
    """Runs everywhere: each substitution's text is in its file exactly once and its replacement differs; each named
    test is defined; the mutant ids are distinct. A code change that moves a mutation point fails here, visibly,
    instead of letting a mutant silently apply nowhere."""
    assert len({m[0] for m in MUTANTS}) == len(MUTANTS)
    for mid, _, path, old, new, killers in MUTANTS:
        text = open(os.path.join(REPO, path), encoding="utf-8").read().replace("\r\n", "\n")
        assert text.count(old) == 1 and old != new, (mid, path, text.count(old))
        assert killers, mid
    for killer in KILLERS:
        path, name = killer.split("::")
        src = open(os.path.join(REPO, path), encoding="utf-8").read()
        assert re.search(rf"^def {re.escape(name)}\(", src, re.M), killer


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """A copy of the repository (no .git, no bytecode) for the planted runs."""
    dst = tmp_path_factory.mktemp("rf4_mutants") / "repo"
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc"))
    return str(dst)


def run_tests(tree, tests):
    """pytest on `tests` in `tree`, in a fresh process; (exit code, the set of failed test ids, the output's tail)."""
    # every PostgreSQL session the child opens gives up on a lock after a minute: a test whose clean-up would wait on a
    # lock under a mutant fails instead of holding the whole group until its job limit (the first X2 run, 2026-10-21)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PGOPTIONS=(os.environ.get("PGOPTIONS", "") + " -c lock_timeout=60s").strip())
    env.pop("RF4_MUTANTS", None)
    basetemp = os.path.join(os.path.dirname(tree), "child-basetemp")       # the child's own; never inside the copy
    proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rf",
                           f"--basetemp={basetemp}", *tests], cwd=tree, env=env, capture_output=True, text=True,
                          timeout=600)
    out = proc.stdout + proc.stderr
    failed = {m.group(1) for m in re.finditer(r"^FAILED (\S+?)(?:\[.*?\])?(?: - .*)?$", out, re.M)}
    return proc.returncode, failed, out[-3000:]


@PLANTED
def test_control_every_named_test_passes_on_the_unchanged_copy(tree):
    code, failed, tail = run_tests(tree, KILLERS)
    assert code == 0 and not failed, tail
    CONTROL["passed"] = True


@PLANTED
@pytest.mark.parametrize("mid, change, path, old, new, killers", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_is_killed_by_its_named_tests(tree, mid, change, path, old, new, killers):
    assert CONTROL.get("passed"), "the control did not pass: a kill could not be told from the environment"
    target = os.path.join(tree, path)
    original = open(target, encoding="utf-8").read()
    assert original.count(old) == 1, (mid, path)
    try:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(original.replace(old, new))
        code, failed, tail = run_tests(tree, killers)
    finally:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(original)
    assert code == 1, (mid, change, code, tail)                      # test failures, not a collection or usage error
    assert set(killers) <= failed, (mid, change, sorted(set(killers) - failed), tail)
