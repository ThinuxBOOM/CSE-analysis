"""
Phase 2 HB-4 (the document worker): unit tests, no database and no network.

Covers the F2-record dispositions and their parity with F2's own categories and HB-1's guard, the item state after a
retrieval failure, the HB-1 transitions every path records, the tool pin, the dedicated temporary root (validation,
free space, the orphan sweep and its limits), SIGTERM -> SystemExit (the handler, and on POSIX a real SIGTERM delivered
mid-consumer through F2's own lifecycle, with and without the handler), the armed versions an F5 run must carry, the
planning rules, the stage refusals, and HB-4's static boundaries (with planted violations).
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker import (document_retrieval as f2, financial_candidates as f5, financial_concepts as fc, pdf_words,  # noqa: E402
                    report_classification as rc, statement_extraction as se)
from worker.backfill_documents import (FREE_SPACE_MARGIN_BYTES, POPPLER_VERSION, STAGES, errors, evidence,  # noqa: E402
                                       outcomes, planning, preflight, signals, temproot, tools, worker)
from worker.backfill_transport.errors import TransportStop  # noqa: E402
from worker.financial_backfill import keys, states  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
UTC = timezone.utc
COLOMBO = keys.COLOMBO
W = (date(2025, 3, 1), date(2025, 4, 30))


def record(**over):
    """An F2 RetrievalRecord.to_dict() of a primary document."""
    r = f2.RetrievalRecord(cse_filing_id=900001, role="primary", source_path="cmt/upload_report_file/1_2.pdf").to_dict()
    r.update(over)
    return r


SUCCEEDED = dict(outcome="succeeded", consumer_status="succeeded", cleanup_status="deleted", sha256="a" * 64)


# ------------------------------------------------------------------------------------------------ dispositions

@pytest.mark.parametrize("rec, leftovers, want", [
    (SUCCEEDED, [], outcomes.PERSIST),
    (dict(outcome="consumer_failed", consumer_status="failed", consumer_error="RuntimeError: x",
          cleanup_status="deleted"), [], outcomes.CONSUMER),
    (dict(outcome="cleanup_failed", consumer_status="succeeded", cleanup_status="failed", cleanup_error="e"), [],
     outcomes.CLEANUP),
    (dict(outcome="cleanup_failed", consumer_status="not_run", cleanup_status="failed", cleanup_error="e"), [],
     outcomes.CLEANUP),
    (SUCCEEDED, ["cse_f2_9_x"], outcomes.LEFTOVER),
    (dict(outcome="download_failed", failure_category="forbidden_or_missing", http_status=403), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="not_found", http_status=404), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="too_large", http_status=200), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="unexpected_redirect", http_status=302), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="too_many_redirects"), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="unexpected_status", http_status=400), [], outcomes.TERMINAL),
    (dict(outcome="download_failed", failure_category="unexpected_status", http_status=429), [], outcomes.RETRYABLE),
    (dict(outcome="download_failed", failure_category="server_error", http_status=503), [], outcomes.RETRYABLE),
    (dict(outcome="download_failed", failure_category="network_error"), [], outcomes.RETRYABLE),
    (dict(outcome="download_failed", failure_category="timeout"), [], outcomes.RETRYABLE),
    (dict(outcome="download_failed", failure_category="download_interrupted", http_status=200), [], outcomes.RETRYABLE),
    (dict(outcome="download_failed", failure_category="temp_root: gone"), [], outcomes.LOCAL),
    (dict(outcome="validation_failed", failure_category="not_pdf"), [], outcomes.TERMINAL),
    (dict(outcome="validation_failed", failure_category="truncated"), [], outcomes.RETRYABLE),
    (dict(outcome="validation_failed", failure_category="etag_mismatch"), [], outcomes.RETRYABLE),
    (dict(outcome="validation_failed", failure_category="length_mismatch"), [], outcomes.RETRYABLE),
    (dict(outcome="validation_failed", failure_category="truncated_or_malformed"), [], outcomes.RETRYABLE),
    (dict(outcome="validation_failed", failure_category="empty_body"), [], outcomes.RETRYABLE),
    (dict(outcome="hash_failed", failure_category="OSError: disk"), [], outcomes.RETRYABLE),
    (dict(outcome="internal_error", failure_category="KeyError: x"), [], outcomes.TERMINAL),
    (dict(outcome="no_document", failure_category="null_path"), [], outcomes.TERMINAL),
    (dict(outcome="invalid_path", failure_category="invalid_path"), [], outcomes.TERMINAL),
])
def test_u1_every_f2_record_has_one_disposition(rec, leftovers, want):
    assert outcomes.disposition(record(**rec), leftovers) == want


def _f2_categories():
    """Every failure category F2's own source can produce: the literals of the lines that set or return one (F2's
    outcomes, which share some names, are not categories)."""
    src = open(os.path.join(REPO, "worker", "document_retrieval.py"), encoding="utf-8").read()
    markers = ("failure_category", 'result["category"]', "return attempt,", "return None, \"", '"timeout" if')
    cats = set()
    for line in src.splitlines():
        if any(m in line for m in markers):
            cats |= set(re.findall(r'"([a-z_]+)"', line))
    return cats - set(f2.OUTCOMES) - {"valid", "category"}


def test_u2_every_category_f2_produces_is_classified_explicitly():
    cats = _f2_categories()
    assert {"forbidden_or_missing", "not_found", "server_error", "unexpected_status", "too_large", "not_pdf",
            "truncated", "etag_mismatch", "unexpected_redirect", "too_many_redirects", "null_path"} <= cats
    special = {"unexpected_status", "hash_failed", "no_companion_path"}           # by status / outcome / role
    unclassified = cats - outcomes.TERMINAL_CATEGORIES - outcomes.RETRYABLE_CATEGORIES - special
    assert unclassified == set()
    assert outcomes.TERMINAL_CATEGORIES.isdisjoint(outcomes.RETRYABLE_CATEGORIES)
    assert set(f2.OUTCOMES) - {"succeeded", "consumer_failed", "cleanup_failed"} == \
        set(outcomes.RETRIEVAL_FAILED_OUTCOMES)


def test_u3_the_retrieval_failed_outcomes_are_hb1s_guard_list():
    sql = open(os.path.join(REPO, "supabase", "migrations", "0016_historical_backfill_ledger.sql"),
               encoding="utf-8").read()
    m = re.search(r"new\.state = 'retrieval_failed' and rr\.outcome not in \(([^)]*)\)", sql)
    assert m is not None
    assert set(re.findall(r"'([a-z_]+)'", m.group(1))) == set(outcomes.RETRIEVAL_FAILED_OUTCOMES)


def test_u4_the_state_after_a_retrieval_failure():
    assert outcomes.live_state(outcomes.TERMINAL, 1, 3) == "retrieval_failed"
    assert outcomes.live_state(outcomes.RETRYABLE, 1, 3) == "retry_wait"
    assert outcomes.live_state(outcomes.RETRYABLE, 2, 3) == "retry_wait"
    assert outcomes.live_state(outcomes.RETRYABLE, 3, 3) == "retrieval_failed"       # the item maximum
    assert outcomes.live_state(outcomes.LOCAL, 3, 3) == "retry_wait"                 # never the document's failure
    with pytest.raises(ValueError):
        outcomes.live_state(outcomes.PERSIST, 1, 3)
    assert outcomes.retrieved(record(**SUCCEEDED)) and not outcomes.retrieved(record(outcome="download_failed"))


@pytest.mark.parametrize("path", [
    ["requesting", "processing", "persisted"], ["requesting", "processing", "consumer_failed"],
    ["requesting", "processing", "cleanup_failed"], ["requesting", "cleanup_failed"],
    ["requesting", "processing", "failed"], ["requesting", "retry_wait", "failed"],
    ["requesting", "processing", "retry_wait"], ["requesting", "retrieval_failed"], ["requesting", "retry_wait"],
    ["requesting", "blocked"], ["requesting", "abandoned"], ["requesting", "processing", "abandoned"],
])
def test_u5_every_recorded_path_is_legal_in_hb1(path):
    prev, action = "pending", "claim"
    for s in path:
        action = "claim" if prev in ("pending", "retry_wait") and s == "requesting" else \
            "expire" if s == "abandoned" else "record"
        assert states.allowed("document", prev, s, action), (prev, s, action)
        prev = s
    for frm, to, act in (("abandoned", "pending", "promote"), ("abandoned", "persisted", "promote"),
                         ("discovered", "pending", "promote"), ("discovered", "persisted", "promote"),
                         ("discovered", "excluded", "record"), ("pending", "persisted", "promote"),
                         ("retry_wait", "persisted", "promote"), ("cleanup_failed", "pending", "requeue"),
                         ("failed", "pending", "requeue"), ("blocked", "pending", "resume")):
        assert states.allowed("document", frm, to, act)
    assert not states.allowed("document", "requesting", "failed", "record")          # why G10 needs retry_wait
    assert not states.allowed("document", "pending", "excluded", "record")           # why a pending item is left
    assert preflight.compat_problems() == []


# ------------------------------------------------------------------------------------------------ tools

def test_u6_the_tool_pin_is_exactly_poppler_24_02_0():
    assert POPPLER_VERSION == "24.02.0" and POPPLER_VERSION in pdf_words.SUPPORTED_POPPLER_VERSIONS
    assert tools.pinned_word_extractor() == "poppler-pdftotext 24.02.0 -bbox-layout"
    assert tools.pinned_text_extractor() == "pdftotext 24.02.0 (poppler) -layout"
    assert tools.tool_refusals(lambda: "poppler-pdftotext 24.02.0 -bbox-layout") == []
    assert tools.tool_refusals(lambda: "poppler-pdftotext 25.03.0 -bbox-layout")[0][0] == "tools"   # F4 accepts it
    with pytest.raises(pdf_words.ExtractorUnavailable):
        pdf_words.check_identity("xpdf", "4.04", "pdftotext")

    def absent():
        raise pdf_words.ExtractorUnavailable("pdftotext not found on PATH")
    assert tools.tool_refusals(absent) == [("tools", "F4's tools are unavailable: ExtractorUnavailable: pdftotext "
                                                     "not found on PATH")]


# ------------------------------------------------------------------------------------------------ the temporary root

@pytest.fixture
def systmp(monkeypatch, tmp_path):
    base = tmp_path / "systemtmp"
    base.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(base))
    monkeypatch.delenv("RUNNER_TEMP", raising=False)
    root = base / "cse-backfill"
    root.mkdir(mode=0o700)
    return base, root


def test_u7_the_dedicated_root_rules(systmp, tmp_path):
    base, root = systmp
    assert temproot.root_problems(str(root)) == []
    assert temproot.root_problems(None) == ["no dedicated temporary root is configured (CSE_BACKFILL_TEMP_ROOT)"]
    assert "not the system temp directory itself" in temproot.root_problems(str(base))[0]
    assert "does not exist" in temproot.root_problems(str(base / "missing"))[0]
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert "inside the system temp directory" in temproot.root_problems(str(outside))[0]
    assert "outside" in temproot.root_problems(REPO)[0] or "inside the system temp" in temproot.root_problems(REPO)[0]
    if os.name == "posix":
        os.chmod(root, 0o777)
        assert "writable by others" in temproot.root_problems(str(root))[0]
        os.chmod(root, 0o700)
    assert temproot.configured({"CSE_BACKFILL_TEMP_ROOT": str(root)}) == str(root)
    assert temproot.configured({}) is None


def test_u8_the_free_space_precheck(systmp):
    _, root = systmp
    need = 2 * f2.DEFAULT_MAX_BYTES + FREE_SPACE_MARGIN_BYTES
    assert temproot.REQUIRED_FREE_BYTES == need
    usage = lambda free: (lambda p: SimpleNamespace(free=free))          # noqa: E731
    assert temproot.free_space_problem(str(root), disk_usage=usage(need)) is None
    assert "needs at least" in temproot.free_space_problem(str(root), disk_usage=usage(need - 1))
    assert temproot.free_space_problem(str(root), disk_usage=usage(10 ** 12)) is None


def test_u9_the_orphan_sweep_removes_only_f2_entries_of_the_dedicated_root(systmp):
    base, root = systmp
    for name in ("cse_f2_771_abc", "cse_f2_772_def"):
        d = root / name
        d.mkdir()
        (d / "document.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
    (root / "cse_f2_773_file").write_bytes(b"x")
    (root / "keep.txt").write_text("not F2's")
    (root / "other_dir").mkdir()
    sibling = base / "cse_f2_999_manual_cli_run"                       # a manual F2 run's dir: system temp, not ours
    sibling.mkdir()
    (sibling / "document.pdf").write_bytes(b"%PDF")
    assert temproot.temporary_entries(str(root)) == 3
    assert temproot.sweep(str(root)) == {"removed": 3, "other_entries": 2}
    assert sorted(os.listdir(root)) == ["keep.txt", "other_dir"] and sibling.exists()
    assert temproot.sweep(str(root)) == {"removed": 0, "other_entries": 2}      # idempotent


def test_u10_the_sweep_stops_for_an_operator_rather_than_guessing(systmp):
    _, root = systmp
    (root / "cse_f2_1_x").mkdir()

    def failing(path):
        raise f2.TemporaryFileCleanupError("temporary document dir still exists after deletion")
    with pytest.raises(temproot.SweepError, match="could not be removed"):
        temproot.sweep(str(root), remove_tree=failing)
    assert (root / "cse_f2_1_x").exists()
    if os.name == "posix":
        target = root.parent / "precious"
        target.mkdir()
        (target / "data").write_text("keep")
        os.symlink(str(target), str(root / "cse_f2_2_link"))               # a link to a directory ...
        with pytest.raises(temproot.SweepError, match="never followed or removed"):
            temproot.sweep(str(root))
        assert (target / "data").read_text() == "keep" and os.path.islink(root / "cse_f2_2_link")
        os.remove(root / "cse_f2_2_link")
        os.symlink(str(target / "data"), str(root / "cse_f2_3_link"))      # ... and a link to a file
        with pytest.raises(temproot.SweepError, match="never followed or removed"):
            temproot.sweep(str(root))
        assert (target / "data").read_text() == "keep" and os.path.islink(root / "cse_f2_3_link")


# ------------------------------------------------------------------------------------------------ SIGTERM

def test_u11_the_sigterm_handler_raises_system_exit_and_is_restored():
    before = signal.getsignal(signal.SIGTERM)
    with signals.sigterm_unwinds() as installed:
        assert installed is True
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not before
        with pytest.raises(SystemExit) as ei:
            handler(signal.SIGTERM, None)
        assert ei.value.code == 128 + signal.SIGTERM == signals.EXIT_STATUS
    assert signal.getsignal(signal.SIGTERM) is before
    got = {}

    def elsewhere():
        with signals.sigterm_unwinds() as installed2:
            got["installed"] = installed2
    t = threading.Thread(target=elsewhere)
    t.start()
    t.join()
    assert got == {"installed": False} and signal.getsignal(signal.SIGTERM) is before


SIGTERM_CHILD = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, {repo!r})
    sys.path.insert(0, {tests!r})
    from test_document_retrieval import PDF, URL, FakeFetcher, FakeResp
    from worker import document_retrieval as f2
    from worker.backfill_documents import signals
    root, ready, use_handler = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    path = "cmt/upload_report_file/771_1790000000001.pdf"

    def consumer(doc):
        open(ready, "w").write(doc.path)          # the document exists; now wait to be signalled
        time.sleep(60)

    def go():
        f2.process_batch([{{"cse_filing_id": 771, "path": path, "path2": None}}], consumer, role="primary",
                         fetcher=FakeFetcher({{URL(path): FakeResp(200, PDF)}}), temp_root=root, request_delay_seconds=0)
    if use_handler:
        with signals.sigterm_unwinds():
            go()
    else:
        go()
""")


@pytest.mark.skipif(os.name != "posix", reason="real signal delivery needs POSIX")
@pytest.mark.parametrize("use_handler", [True, False])
def test_u12_sigterm_mid_consumer_with_and_without_the_handler(tmp_path, use_handler):
    """Design section 23.2: with the handler installed, a SIGTERM delivered mid-consumer still deletes the document
    (F2's own cleanup unwinds); without it, the same SIGTERM leaves the document behind."""
    root = tempfile.mkdtemp(prefix="hb4sig_")
    try:
        ready = str(tmp_path / "ready")
        script = SIGTERM_CHILD.format(repo=REPO, tests=os.path.dirname(__file__))
        child = subprocess.Popen([sys.executable, "-c", script, root, ready, "1" if use_handler else "0"])
        end = time.monotonic() + 60
        while not os.path.exists(ready):
            assert child.poll() is None and time.monotonic() < end, "the child never reached the consumer"
            time.sleep(0.05)
        doc_path = open(ready).read()
        assert os.path.exists(doc_path)
        child.send_signal(signal.SIGTERM)
        code = child.wait(timeout=60)
        left = [n for n in os.listdir(root) if n.startswith("cse_f2_")]
        if use_handler:
            assert code == 128 + signal.SIGTERM and left == [] and not os.path.exists(doc_path)
        else:
            assert code == -signal.SIGTERM and len(left) == 1 and os.path.exists(doc_path)
            assert temproot.sweep(root) == {"removed": 1, "other_entries": 0}      # what the next slice does
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ evidence, planning

def test_u13_an_f5_run_counts_only_under_the_armed_versions():
    assert evidence.armed_versions() == {
        "classifier_version": rc.CLASSIFIER_VERSION, "text_extractor": "pdftotext 24.02.0 (poppler) -layout",
        "word_extractor": "poppler-pdftotext 24.02.0 -bbox-layout", "f4_extractor_version": se.F4_EXTRACTOR_VERSION,
        "builder_version": f5.F5_BUILDER_VERSION, "mapper_version": fc.MAPPER_VERSION,
        "vocabulary_version": fc.VOCABULARY_VERSION}
    assert (rc.CLASSIFIER_VERSION, se.F4_EXTRACTOR_VERSION, f5.F5_BUILDER_VERSION, fc.MAPPER_VERSION,
            fc.VOCABULARY_VERSION) == ("f3.1", "f4.1", "f5.1", "f5.map.1", "v1")
    sql = open(os.path.join(REPO, "supabase", "migrations", "0008_financial_candidates.sql"), encoding="utf-8").read()
    table = re.search(r"create table financial_extraction_runs \((.*?)\n\);", sql, re.S).group(1)
    assert all(re.search(rf"^\s+{c}\s", table, re.M) for c in evidence.VERSION_COLUMNS)


def test_u14_planning_rules():
    at = lambda *a: datetime(*a, tzinfo=COLOMBO)                          # noqa: E731
    assert planning.in_window(at(2025, 3, 1, 0, 0), W) and planning.in_window(at(2025, 4, 30, 23, 59), W)
    assert not planning.in_window(at(2025, 5, 1, 0, 0), W) and not planning.in_window(at(2025, 2, 28, 23, 59), W)
    assert not planning.in_window(None, W)
    assert planning.in_window(datetime(2025, 2, 28, 18, 30, tzinfo=UTC), W)          # = 2025-03-01 00:00 Colombo
    ok = {"uploaded_at": at(2025, 3, 2, 10, 0), "path": "cmt/upload_report_file/1_2.pdf"}
    assert planning.first_state(ok) == ("discovered", None)
    assert planning.first_state(dict(ok, uploaded_at=None)) == ("excluded", "window_undetermined")
    assert planning.first_state(dict(ok, path=None)) == ("excluded", "no_document")
    for bad in ("/abs/x.pdf", "x/../y.pdf", "report.", " lead.pdf", "a\\b.pdf"):
        assert planning.first_state(dict(ok, path=bad)) == ("excluded", "invalid_path"), bad
    item = {"path": "cmt/a.pdf", "path_sha256": keys.path_version("cmt/a.pdf")}
    assert planning.path_is_current(item)
    assert not planning.path_is_current(dict(item, path="cmt/b.pdf")) and not planning.path_is_current(
        dict(item, path=None))


def test_u15_stage_refusals_and_stop_signals():
    a = {"armed": True, "armed_stages": ["HB-S3", "HB-S4"]}
    assert STAGES == ("HB-S3", "HB-S4")
    assert worker.stage_refusals(a, "HB-S4") == [] and worker.stage_refusals(a, "HB-S3") == []
    assert worker.stage_refusals(a, "HB-S2")[0][0] == "stage"
    assert worker.stage_refusals(dict(a, armed_stages=["HB-S3"]), "HB-S4")[0][0] == "stage"
    assert worker.stage_refusals({"armed": False, "armed_stages": []}, "HB-S4")[0][0] == "disarmed"
    assert worker.stage_refusals(None, "HB-S4")[0][0] == "disarmed"
    assert issubclass(errors.CleanupStop, TransportStop) and errors.CleanupStop.lease_result == "cleanup_stop"
    from worker.backfill_discovery.errors import InFlightItems
    assert errors.InFlightItems is InFlightItems and not issubclass(InFlightItems, TransportStop)
    e = errors.DocumentRefused([("tools", "x"), ("temp_root", "y")])
    assert e.codes == ["tools", "temp_root"] and "tools: x" in str(e)


# ------------------------------------------------------------------------------------------------ static boundaries

def test_u16_the_frozen_baseline_and_this_package_pass_hb4s_offline_checks():
    assert preflight.pin_problems() == [] and preflight.static_problems() == [] and preflight.compat_problems() == []
    from worker.backfill_discovery import preflight as hb3
    from worker.backfill_transport import preflight as hb2
    from worker.financial_backfill import preflight as hb1
    assert hb1.file_problems() == [] and hb2.pin_problems() == [] and hb3.pin_problems() == []
    assert hb2.static_problems() == [] and hb3.static_problems() == []


PLANTED = {
    "network": "import requests\n",
    "network_from": "from urllib import request\n",
    "stage_e_client": "from worker import cse_client\n",
    "transport_internals": "from ..backfill_transport import fetcher\n",
    "p2": "from ..market_capture import http\n",
    "owner_path": "from ..financial_backfill import owner\n",
    "requests_fetcher": "x = f2.RequestsFetcher()\n",
    "process_filing": "f2.process_filing(filing, consumer)\n",
    "f5_run": "f5cli.run([filing])\n",
    "direct_f3": "rc.classify(text, meta)\n",
    "direct_f4": "se.extract_document(path, cls)\n",
    "direct_f5_store": "stores['candidates'].save(result, cid, link)\n",
    "link_outside_persist": "issuer.link_filing(1)\n",
    "frozen_write": "cur.execute('insert into financial_extraction_runs (id) values (1)')\n",
    "frozen_update": "cur.execute('update report_filings set path = null')\n",
    "lock_literal": "KEY = 4346836117002312\n",
    "lock_call": "cur.execute('select pg_advisory_unlock_all()')\n",
    "rdv": "import tests." + "rdv_measure\n",
    "entry_point": "if __name__ == '__main__':\n    pass\n",
    "with_slice": "with open_slice(conn, stage='HB-S4', kind='document') as s:\n    pass\n",
    "close_outside_guard": "def f(self):\n    self.sl.close(None)\n",
    "arming_write": "def f(self):\n    self.sl.arming = {}\n",
    "fetcher_built": "GovernedFetcher(sl, item)\n",
}


@pytest.mark.parametrize("what", sorted(PLANTED))
def test_u17_every_planted_boundary_violation_is_found(what):
    assert preflight.source_problems("planted.py", PLANTED[what]) != [], what


def test_u18_the_composition_must_be_present_and_prose_is_not_code():
    src = open(os.path.join(REPO, "worker", "backfill_documents", "worker.py"), encoding="utf-8").read()
    assert preflight.source_problems("worker.py", src) == []
    broken = src.replace("f5.attach_timestamps(", "f5.attach_timestamp_s(")
    assert any("lacks the frozen composition" in p for p in preflight.source_problems("worker.py", broken))
    assert preflight.source_problems("x.py", '"""process_filing and RequestsFetcher are never used."""\n') == []
    assert preflight.source_problems("x.py", "# an insert into financial_extraction_runs is F5's\n") == []
    guarded = "class D:\n    def close(self):\n        self.sl.close(None)\n"
    assert preflight.source_problems("worker.py", guarded + "load_filings_from_db process_batch make_consumer "
                                     "attach_timestamps _persist document_fetcher record_retrieval_in "
                                     "append_event_in\n".replace(" ", "\n")) == []


def test_u19_hb4_never_imports_a_network_module_and_has_no_entry_point():
    pkg = os.path.join(REPO, "worker", "backfill_documents")
    files = sorted(f for f in os.listdir(pkg) if f.endswith(".py"))
    assert "__main__.py" not in files
    assert set(files) == {"__init__.py", "errors.py", "evidence.py", "gate.py", "outcomes.py", "planning.py",
                          "preflight.py", "signals.py", "temproot.py", "tools.py", "worker.py"}
    for f in files:
        src = open(os.path.join(pkg, f), encoding="utf-8").read()
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib|socket|http|httpx|aiohttp|ssl)\b", src, re.M), f
