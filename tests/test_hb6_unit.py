"""
Phase 2 HB-6 (operations and pilot), pure rules: no database, no network.

    u1-u3    the package, its one entry point and the operational files (wrapper, units, provisioning, the clean-server
             test, the runbook); the frozen-test edit of owner decision HB6-1
    u4-u6    the static boundary (every planted violation found; the real package clean), the pins of HB-5 and the
             compatibility with HB-1 .. HB-5
    u7-u9    the pilot sample: labels, strata, determinism, the sample file; the report's E1 statement
    u10-u13  the owner's arming validation: every refusal, and the recorded decision (sample, checkpoint, versions)
    u14-u16  the runner's pure helpers (summary, results, waiting / hard refusals), the claim SQL, the CLI parser
"""
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.backfill_ops import (BULK_STAGE, CHECKPOINT_DOCUMENTS, EXIT_ATTENTION, EXIT_BLOCKED,  # noqa: E402
                                 EXIT_DATABASE_UNAVAILABLE, EXIT_OK, EXIT_REFUSED, PILOT_MAX, PILOT_STAGE, TOOL_VERSION,
                                 claim, cli, decisions, pilot, preflight, runner)
from worker.backfill_ops.errors import OpsRefused  # noqa: E402
from worker.backfill_transport import gates as hb2_gates  # noqa: E402
from worker.financial_backfill import states  # noqa: E402
from worker.market_capture import config as p2config  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(REPO, "worker", "backfill_ops")
COLOMBO = timezone(timedelta(hours=5, minutes=30))


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


# ================================================================================================ the package and files

def test_u1_the_package_has_one_entry_point_and_its_versions():
    files = sorted(f for f in os.listdir(PKG) if f.endswith(".py"))
    assert files == ["__init__.py", "__main__.py", "actions.py", "claim.py", "cli.py", "config.py", "decisions.py",
                     "errors.py", "pilot.py", "preflight.py", "reports.py", "runner.py", "status.py", "util.py"]
    assert read("worker", "backfill_ops", "__main__.py").strip().endswith("sys.exit(main())")
    assert TOOL_VERSION == "hb.ops.1"
    assert (EXIT_OK, EXIT_ATTENTION, EXIT_BLOCKED, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED) == (0, 2, 3, 4, 5)
    for f in files:
        src = read("worker", "backfill_ops", f)
        assert "rdv_" not in src and not re.findall(r"4_?346_?836_?117_?002_?31\d", src), f
    assert not os.path.exists(os.path.join(REPO, "worker", "financial_backfill", "__main__.py"))   # A1: HB-1 untouched


def test_u2_the_operational_files_follow_p3s_conventions():
    assert sorted(os.listdir(os.path.join(REPO, "ops", "backfill"))) == ["cse-backfill.service", "cse-backfill.timer"]
    svc, timer = read("ops", "backfill", "cse-backfill.service"), read("ops", "backfill", "cse-backfill.timer")
    for line in ("Type=oneshot", "User=cse-worker", "Group=cse-worker", "PrivateTmp=yes", "ProtectSystem=strict",
                 "NoNewPrivileges=yes", "ExecStart=/usr/bin/python3 -m worker.backfill_ops run --trigger timer",
                 "ExecStartPre=/usr/bin/install -d -m 0700 /tmp/cse-backfill", "EnvironmentFile=/etc/cse/cse.env",
                 "EnvironmentFile=-/etc/cse/capture.env", "EnvironmentFile=-/etc/cse/backfill.env",
                 "StateDirectory=cse-backfill", "ReadWritePaths=/srv/cse-backup/spool"):
        assert line in svc.splitlines(), line
    assert "OnBootSec=5min" in timer and "OnCalendar=*-*-* *:07/15:00" in timer
    assert not any(ln.startswith("Persistent=") for ln in timer.splitlines())         # design 20: no Persistent=
    wrapper = read("ops", "bin", "cse-backfill")
    assert "arm|disarm|acknowledge-block|resolve-hold) user=cse-migrator; role=cse_migrator" in wrapper
    assert "exec python3 -m worker.backfill_ops" in wrapper and "[[ $EUID -eq 0 ]]" in wrapper
    prov = read("ops", "provision", "provision_backfill.sh")
    assert "systemctl enable --now cse-backfill.timer" in prov and "ops/backfill/${u}" in prov
    assert not re.search(r"worker\.backfill_ops\"? +arm\b|as_worker arm|\barm --", prov)    # provisioning never arms
    env = read("ops", "config", "backfill.env.example")
    assert "CSE_BACKFILL_REPORT_DIR=/var/lib/cse-backfill/reports" in env and "@" not in env
    docker = read("ops", "tests", "provision_backfill_in_docker.sh")
    assert docker.index("network disconnect") < docker.index("provision_backfill.sh units verify")
    assert 'docker rmi "$IMAGE"' in docker                                            # the owner's image rule
    probes = read("ops", "tests", "backfill_container_probes.sh")
    assert "the worker cannot arm (owner path only)" in probes and "still no CSE request" in probes
    assert sorted(os.listdir(os.path.join(REPO, "ops", "systemd"))) == sorted(
        n for n in os.listdir(os.path.join(REPO, "ops", "systemd")) if n.startswith("cse-backup-"))     # HB-B8


def test_u3_the_runbook_and_owner_decision_hb6_1():
    book = read("docs", "ops", "PHASE2_BACKFILL.md")
    for needed in ("ready for its acceptance / freeze", "Owner decision HB6-1", "E1 (disappeared since F0) is not "
                   "measurable", "pilot-sample", "checkpoint", "requeue", "late-pass", "`busy`"):
        assert needed in book, needed
    for f in ("test_hb1_unit.py", "test_hb2_unit.py", "test_hb5_unit.py"):
        src = read("tests", f)
        assert "owner decision HB6-1" in src and 'assert units in ([], ["cse-backfill.service", ' \
                                                 '"cse-backfill.timer"])' in src, f


# ================================================================================================ boundaries and pins

PLANTED = [
    ("import requests\n", "network module"),
    ("import socket\n", "network module"),
    ("from ..backfill_transport.slice import open_slice\n", "CSE slices are opened only by HB-3 and HB-4"),
    ("import worker.backfill_transport.slice\n", "only `from ... import Runtime`"),
    ("from ..financial_truth_store import jobs\n", "imports worker.financial_truth_store.jobs"),
    ("from ..financial_truth_store.writer import insert_validation\n", "financial_truth_store.writer"),
    ("from .. import cse_client\n", "imports worker.cse_client"),
    ("from ..market_capture import capture\n", "imports worker.market_capture.capture"),
    ("from ..scheduler import wakeup\n", "imports worker.scheduler.wakeup"),
    ("from ..financial_asof import modes\n", "imports worker.financial_asof"),
    ("x = jobs.designate(c, 1, 'n')\n", "mentions designate"),
    ("x = s.open_slice(c)\n", "mentions open_slice"),
    ("x = p.acquire_global_lock(c)\n", "mentions acquire_global_lock"),
    ("x = s.record_outcome(c)\n", "mentions record_outcome"),
    ("q = 'insert into backfill_wakeups values (1)'\n", "writes SQL"),
    ("q = 'update backfill_items set x = 1'\n", "writes SQL"),
    ("q = 'select pg_advisory_lock(1)'\n", "mentions pg_advisory"),
    ("k = 4_346_836_117_002_312\n", "advisory-lock literal"),
    ("h = 'rdv" + "_report'\n", "mentions rdv_"),
    ("if __name__ == '__main__':\n    pass\n", "script entry point"),
]


@pytest.mark.parametrize("src, expect", PLANTED)
def test_u4_every_planted_boundary_violation_is_found(src, expect):
    got = preflight.source_problems("planted.py", src)
    assert any(expect in p for p in got), got


def test_u5_the_real_package_is_clean_and_the_allowed_runtime_import_passes():
    assert preflight.static_problems() == []
    assert preflight.source_problems("ok.py", "from ..backfill_transport.slice import Runtime\n") == []
    assert preflight.source_problems("claim.py", "x = 1\n")[0] == "claim.py lacks the composition 'set transaction " \
                                                                 "isolation level serializable'"


def test_u6_the_pins_of_hb5_and_the_compatibility_with_hb1_to_hb5(tmp_path):
    assert preflight.pin_problems() == [] and preflight.compat_problems() == []
    assert len(preflight.PINNED_FILES) == 13
    assert set(preflight.PINNED_FILES) == {f"worker/backfill_f6/{f}" for f in os.listdir(
        os.path.join(REPO, "worker", "backfill_f6")) if f.endswith(".py")}
    copy = tmp_path / "repo"
    shutil.copytree(os.path.join(REPO, "worker", "backfill_f6"), copy / "worker" / "backfill_f6")
    with open(copy / "worker" / "backfill_f6" / "validation.py", "a", encoding="utf-8") as f:
        f.write("\n# a change\n")
    assert preflight.pin_problems(str(copy)) == ["frozen file worker/backfill_f6/validation.py changed (HB-B9: HB-6 "
                                                 "composes exactly the frozen code)"]
    assert all(t in states.TRANSITIONS for t in preflight.TRANSITIONS_USED)
    assert ("validate", "failed", "pending", "requeue") in preflight.TRANSITIONS_USED
    assert ("document", "blocked", "pending", "resume") in preflight.TRANSITIONS_USED


# ================================================================================================ the pilot sample

def filing(fid, year=2023, buckets=(), title="", status="evidenced", basis="both", symbols=("COMB.N0000",)):
    return {"cse_filing_id": fid, "uploaded_at": datetime(year, 3, 2, 10, 0, tzinfo=COLOMBO), "source_buckets":
            list(buckets), "file_text": title, "link_status": status, "link_basis": basis,
            "listing_symbols": list(symbols)}


def test_u7_document_type_and_link_labels_are_sampling_labels():
    assert pilot.document_type(["annual"], "Annual Report 2023") == "annual"
    assert pilot.document_type(["quarterly"], "") == "interim"
    assert pilot.document_type([], "Interim Financial Statements - 31.12.2024") == "interim"
    assert pilot.document_type(["annual"], "ERRATA - Annual Report") == "errata"
    assert pilot.document_type(["other"], "Corrigendum to the interim statements") == "errata"
    assert pilot.document_type([], "Notice of AGM") == "other"
    assert pilot.link_label(None, None) == "no_decision" and pilot.link_label("conflict", "both") == "conflict/both"
    k = pilot.stratum(filing(1, title="Annual Report"), ["COMB.N0000"])
    assert k == (2023, "annual", "evidenced/both", "listed")
    assert pilot.stratum(filing(2, symbols=()), ["COMB.N0000"])[3] == "not_listed"


def test_u8_the_sample_is_round_robin_deterministic_and_bounded():
    cands = ([filing(100 + i, 2022, ["annual"]) for i in range(6)] + [filing(200 + i, 2023, ["quarterly"])
                                                                      for i in range(6)]
             + [filing(300 + i, 2024, [], "Errata", "unresolved", "document_path_prefix", ()) for i in range(2)])
    a = pilot.select(cands, ["COMB.N0000"], size=6)
    b = pilot.select(list(reversed(cands)), ["COMB.N0000"], size=6)
    assert [f["cse_filing_id"] for f, _ in a] == [f["cse_filing_id"] for f, _ in b]          # order independent
    strata = [k for _, k in a]
    assert len(set(strata)) == 3 and all(strata.count(k) == 2 for k in set(strata))         # round robin
    assert len(pilot.select(cands, ["COMB.N0000"], size=100)) == len(cands)                 # never more than there is
    assert pilot.select([], ["COMB.N0000"], size=5) == []
    assert pilot.order_key(5) == pilot.order_key(5) != pilot.order_key(6)


def test_u9_the_sample_file_and_the_e1_statement():
    assert pilot.sample_filing_ids({"filings": [3, 1, 2]}) == [1, 2, 3]
    assert pilot.sample_filing_ids([7]) == [7]
    for bad in ({"filings": []}, {"filings": [1, 1]}, {"filings": [0]}, {"filings": ["1"]}, {"filings": [True]}, {}):
        with pytest.raises(ValueError):
            pilot.sample_filing_ids(bad)
    assert "lost on 2026-10-10" in pilot.E1_NOT_MEASURED and "governed way later" in pilot.E1_NOT_MEASURED


# ================================================================================================ the owner's arming

CONTACT = "owner@example.org"
UA = p2config.user_agent(CONTACT)
W = (date(2021, 4, 1), date(2026, 9, 30))


def decide(**over):
    kw = dict(stages=("HB-S1", "HB-S2"), window_first=W[0], window_last=W[1], user_agent=UA, host="srv",
              g1_reference="G-1 extension 2026-11 (HB-X2)", note="owner arms identity and discovery",
              confirm_stop_conditions=True, expected_requests={"feed": 66, "listings": 327}, this_host="srv",
              versions={"v": 1})
    kw.update(over)
    return decisions.arming_decision(**kw)


def refused(**over):
    with pytest.raises(OpsRefused) as ei:
        decide(**over)
    return ei.value.codes


def test_u10_a_valid_decision_records_every_release_gate_fact():
    d = decide()
    assert d.armed and d.armed_stages == ("HB-S1", "HB-S2") and d.user_agent == UA and d.host == "srv"
    assert (d.daily_request_budget, d.attempts_per_json_request, d.item_max_attempts) == (600, 1, 3)
    assert d.version_tuple == {"v": 1} and list(d.stop_conditions) == list(decisions.STOP_CONDITIONS)
    assert decide(versions=None).version_tuple == hb2_gates.running_version_tuple()        # A3, from this process
    assert decide(security_master_max_age_days=3).stop_conditions[-1] == {"security_master_max_age_days": 3}


def test_u11_every_arming_refusal():
    assert refused(stages=()) == ["stages"]
    assert refused(stages=("HB-S9",)) == ["stages"]
    assert "window" in refused(window_first=date(2021, 4, 2))
    assert refused(user_agent="cse-research-tool/1.0") == ["user_agent"]
    assert refused(user_agent=UA + " ") == ["user_agent"]
    assert refused(host="other") == ["host"]
    assert refused(confirm_stop_conditions=False) == ["stop_conditions"]
    assert refused(note="short") == ["note"]
    assert refused(expected_requests={"feed": -1}) == ["expected_requests"]
    assert refused(expected_requests={}) == ["expected_requests"]
    assert refused(bounds={"attempts_per_json_request": 2}) == ["d_hb3_1"]                   # D-HB3-1 for HB-S2
    assert refused(bounds={"item_max_attempts": 4}) == ["d_hb3_1"]
    assert decide(stages=("HB-S1",), bounds={"attempts_per_json_request": 2}).attempts_per_json_request == 2


def test_u12_the_pilot_sample_is_recorded_only_with_hb_s3():
    assert refused(stages=("HB-S2", PILOT_STAGE)) == ["pilot_sample"]
    assert refused(stages=("HB-S2", PILOT_STAGE), pilot_filings=list(range(1, PILOT_MAX + 2))) == ["pilot_sample"]
    assert refused(pilot_filings=[1, 2]) == ["pilot_sample"]
    d = decide(stages=("HB-S2", PILOT_STAGE), pilot_filings=[30, 10, 20])
    assert d.expected_requests["pilot_filings"] == [10, 20, 30] and d.expected_requests["documents"] == 3


def test_u13_hb_s4_needs_a_recorded_checkpoint_and_a_bounded_block():
    cp = decisions.Checkpoint("dump-41", "restore-42", True, "checkpoint before the first bulk block")
    assert "checkpoint" in refused(stages=(BULK_STAGE,), expected_requests={"documents": 2000})
    assert "checkpoint" in refused(stages=(BULK_STAGE,), checkpoint=decisions.Checkpoint("d", "r", False, ""),
                                   expected_requests={"documents": 10})
    assert "checkpoint" in refused(stages=(BULK_STAGE,), checkpoint=cp,
                                   expected_requests={"documents": CHECKPOINT_DOCUMENTS + 1})
    assert "checkpoint" in refused(checkpoint=cp)                                         # only with HB-S4
    d = decide(stages=(BULK_STAGE,), checkpoint=cp, expected_requests={"documents": 2000})
    assert d.stop_conditions[-1] == {"backup_checkpoint": {"dump_backup_run": "dump-41", "restore_check_backup_run":
                                                           "restore-42", "f64_and_phase2_verify_clean": True,
                                                           "note": "checkpoint before the first bulk block"}}
    assert d.expected_requests["documents"] == 2000


# ================================================================================================ runner helpers, claim, CLI

def test_u14_the_runners_summary_results_and_refusal_classes():
    rep = {"trigger": "timer", "result": "worked", "arming": {"id": 3}, "db_bytes": 10, "attention": [["x", "y"]],
           "slice": {"due": True, "kind": "documents", "stage": "HB-S3", "outcomes": {"persisted": 2}, "lease_id": 4},
           "f6": {"validation": {"attempted": 2}}, "audit": {"due": True, "snapshot_id": 9, "digest": "d"},
           "when": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    s = runner.summary(rep)
    assert json.loads(json.dumps(s)) == s and s["slice"]["outcomes"] == {"persisted": 2}
    assert s["audit"] == {"due": True, "snapshot_id": 9, "digest": "d"} and "when" not in s
    assert runner.worked(rep) and not runner.worked({"slice": {"due": False}, "f6": {"validation": {"attempted": 0},
                                                                                    "promotion": {}},
                                                     "audit": {"due": False}, "bookkeeping": {}})
    assert runner.pilot_filings({"expected_requests": {"pilot_filings": [3, "2", 3]}}) == [2, 3]
    assert runner.pilot_filings({"expected_requests": {}}) == [] and runner.pilot_filings(None) == []
    assert {"budget", "quiet_window", "disarmed"} <= runner.WAITING and not runner.WAITING & runner.HARD
    assert {"preflight", "user_agent", "host", "versions", "clock"} <= runner.HARD


def test_u15_the_claim_reads_live_runners_only_in_a_serializable_transaction():
    assert "hb_session_alive(backend_pid, backend_started_at)" in claim.LIVE_RUNNERS_SQL
    assert "state = 'active'" in claim.LIVE_RUNNERS_SQL and "tool_version = %s" in claim.LIVE_RUNNERS_SQL
    src = read("worker", "backfill_ops", "claim.py")
    assert src.index("set transaction isolation level serializable") < src.index("store.start_wakeup(")
    assert claim.SERIALIZATION_FAILURE == "40001"


def test_u16_the_command_line():
    ap = cli.parser()
    for cmd in (["run"], ["status"], ["plan"], ["pilot-sample", "--out", "x"], ["pilot-report", "--out-dir", "d"],
                ["report", "--out-dir", "d"], ["audit"], ["anomalies"], ["verify"], ["late-pass"],
                ["requeue", "--item", "validate:x", "--reason", "r"], ["resume", "--item", "k", "--reason", "r"],
                ["disarm", "--note", "n"], ["acknowledge-block", "--block-id", "1", "--note", "n"],
                ["resolve-hold", "--hold-id", "1", "--resolution", "keep_held", "--note", "n"]):
        assert ap.parse_args(cmd).command == cmd[0]
    assert ap.parse_args(["run", "--trigger", "timer"]).trigger == "timer"
    with pytest.raises(SystemExit):
        ap.parse_args(["arm", "--stages", "HB-S1"])                                       # every release-gate fact
    a = ap.parse_args(["arm", "--stages", "HB-S1", "--window-first", "2021-04-01", "--window-last", "2026-09-30",
                       "--user-agent", UA, "--host", "h", "--g1-reference", "g", "--note", "nnnnnnnnnn",
                       "--confirm-stop-conditions", "--daily-request-budget", "500"])
    assert a.daily_request_budget == 500 and a.slice_max_documents is None
    assert cli.OWNER_COMMANDS == ("arm", "disarm", "acknowledge-block", "resolve-hold")
    for owner_cmd in cli.OWNER_COMMANDS:
        assert owner_cmd in read("ops", "bin", "cse-backfill")
    assert cli.main(["status"], env={"CSE_DB_PORT": "not-a-number"}) == EXIT_REFUSED     # a settings error refuses
