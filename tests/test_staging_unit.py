"""
Milestone 8 Docker staging harness, static guards (docs/ops/STAGING.md). The harness's boundaries are checked here, not
only documented, so that a later edit cannot loosen one silently:

    s1  every staging container: --network none, --restart no, no bind mount, no Docker socket, no --privileged
    s2  no restore in the host container: nothing invokes P1's restore check, pg_restore, `provision.sh all` or `selftest`
        (DO NOT EXECUTE RESTORE); no test is deselected in the runner (owner, 2026-11-04: the nine restore-executing
        tests run in its no-network throwaway container); the host container's R1 gate is unchanged
    s3  the R1 gate: the restore-check service's refusal drop-in is installed before `provision.sh units`, and the
        timer is disabled after every `units`
    s4  never armed, never CSE: no arming or owner command, no CSE host name, no contact e-mail value
    s5  nothing destructive: no container, volume or image removal; evidence and secrets outside the repository
    s6  the image: the CI's digest-pinned base, P1's own `packages` step, Poppler 24.02.0 enforced, no secret in a layer
    s7  shell syntax (Linux)

The corrections made after the T1 campaign (docs/ops/STAGING.md section 8), each run against the harness's own code
(shell functions extracted from the scripts, or the driver with a stub `docker` first on PATH; no container, no
network). They need a POSIX bash (Linux, or Git Bash on Windows) and are skipped, not passed, without one:

    h1  ST-21b: a oneshot unit's result is read only once that invocation has ended (never from `is-active`)
    h2  ST-21a: the leak check uses the secret as P1 reads it (no CR, no newline) and keeps its controls
    h3  ST-04: the container's configuration (docker inspect) and its effective capabilities and mounts are asserted
    h4  every run records the exact harness file versions (PROVENANCE lines; inject manifests in the driver log)
    h5  fingerprint snapshots are never reused or overwritten, and are compared by `fpcompare`
    h6  the synthetic seed records its command, identity, parameters, input hash, timestamps and result

The procedures ST-27 to ST-29 (stage_checks.sh rf4verify, reader, regx03; docs/ops/STAGING.md section 9), checked
statically and run against stubs of the login's sessions (no container, no network):

    s8  the login's steps 1, 2, 4 and 5 run as `runuser -u cse-verifier` through the URL that names the role, never
        through SET ROLE or a superuser session; nothing in the new procedures restores, arms or reaches a network
    h7  the procedures' verdicts: each ST-27/ST-28 assertion fails when its condition does not hold
    h8  ST-29 requires PASS for the cse_verifier checks, owner_members_designed_only and the five ledger checks

Restore under the owner's restore authorization (RA; OD-12, DR-0002; `staging.sh restore R1|R2 RA`, restore_driver.sh,
restore_gate.py, restore/stage_restore.sh; the procedure ops/restore/isolated_restore.py; docs/ops/ISOLATED_RESTORE.md).
The guards above are unchanged: without a valid RA the harness behaves exactly as before.

    r1  the RA validation matrix: an expired, consumed, mismatched or malformed RA refuses (restore_gate.py)
    r2  the driver, against a stub docker: every invalid RA refuses before anything runs, the RA is consumed before the
        restore, a consumed RA refuses, and the RA's directory is byte-for-byte unchanged (no write into the vault)
    r3  no code path restores without validation: the only restore code is reached through `restore`, after the RA check
    r4  the harness never writes into the vault: the RA is opened read-only, once; nothing is written beside it
    r5  the R2 container has no network and no shared volume or mount (its creation and its docker inspect check)
    r6  nothing references P1 section 8 B step 5, except as a prohibition
    r7  an identity check precedes each verification command (the procedure's own code, run against stub identities)
    r8  the globals step (REG-B04): one exact error tolerated, every role and membership proven; the gates fail closed
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
STAGING = os.path.join(REPO, "ops", "staging")


def read(*parts):
    with open(os.path.join(STAGING, *parts), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def harness_files():
    out = []
    for d, _dirs, files in os.walk(STAGING):
        out += [os.path.join(d, f) for f in files if not f.endswith(".lock")]
    return sorted(out)


def code_lines(text):
    """Lines that are not comments (shell, Python and Dockerfile comments start with #)."""
    return [l for l in text.splitlines() if not l.lstrip().startswith("#")]


def test_s1_every_container_is_offline_never_restarted_and_has_no_bind_mount():
    drv = read("staging.sh")
    flags = drv[drv.index("rung_flags() {"):drv.index("wait_systemd() {")]
    assert "--network none --restart no --cgroupns private --cap-add SYS_ADMIN" in flags
    runner = drv[drv.index("cmd_runner() {"):drv.index("cmd_canary() {")]
    assert "docker create" in runner and "--network none --restart no" in runner
    assert drv.count("docker run ") == 2 and drv.count("docker create ") == 1      # trial, up; runner
    for f in harness_files():
        code = "\n".join(code_lines(open(f, encoding="utf-8").read()))
        # the socket may appear only in a check of its absence (stage_checks.sh, isolation_check.py)
        code = code.replace("! -e /var/run/docker.sock", "").replace("! -e /run/docker.sock", "")
        assert "docker.sock" not in code.replace('not any("docker.sock" in (', ""), f
        assert "type=bind" not in code, f
        assert not re.search(r"--privileged(?![^\n]*(echo|not implemented|L3))", code), f
    docker_lines = [l for l in code_lines(drv) if "docker " in l or l.lstrip().startswith("--")]
    assert docker_lines and not any(re.search(r"(^|\s)(-v|--volume)\s", l) for l in docker_lines)
    assert drv.count("type=volume,src=") == 3


# The nine regression tests that execute a restore into a disposable cluster. T1 deselected them in the runner; the owner
# authorized them on 2026-11-04, to run only inside the runner's no-network throwaway container (Decision Log).
RESTORE_TESTS = (
    "tests/test_p1_postgres.py::test_restore_check_restores_into_scratch_cluster",
    "tests/test_p1_postgres.py::test_restore_check_detects_a_dump_that_does_not_match_its_manifest",
    "tests/test_f64_postgres.py::test_p16_dump_restore_check_then_verify_on_the_restored_copy",
    "tests/test_rf4_runbook_rehearsal.py::test_c1_on_a_restored_copy_the_checkpoint_is_accepted",
    "tests/test_rf4_runbook_rehearsal.py::test_c2_corrupt_evidence_carried_by_the_dump_fails_verify_on_the_copy",
    "tests/test_rf4_runbook_rehearsal.py::test_c3_a_ledger_that_disagrees_with_the_pin_fails_the_preflight_on_the_copy",
    "tests/test_rf4_runbook_rehearsal.py::test_c4_row_level_security_on_the_copy_stops_the_sequence_before_verify",
    "tests/test_rf4_runbook_rehearsal.py::test_c5_an_unverifiable_ledger_on_the_copy_is_not_a_pass",
    "tests/test_rf4_runbook_rehearsal.py::test_c6_a_limited_scan_of_the_copy_is_not_accepted",
)


def test_s2_nothing_restores_in_the_host_container_and_no_test_is_deselected_in_the_runner():
    for f in harness_files():
        code = "\n".join(code_lines(open(f, encoding="utf-8").read()))
        for forbidden in ("worker.ops.restore_check", "pg_restore", "cse-ops\" restore_check", "cse-ops restore_check",
                          "provision.sh all", "selftest"):
            assert forbidden not in code, (f, forbidden)
        assert not re.search(r'"\$P1"\s+all\b', code), f
    # the nine tests exist under these node ids (a rename would silently leave the claim below untested)
    for node in RESTORE_TESTS:
        path, name = node.split("::")
        assert f"def {name}(" in open(os.path.join(REPO, path), encoding="utf-8").read(), node
    assert {n.split("::")[0] for n in RESTORE_TESTS} == {"tests/test_p1_postgres.py", "tests/test_f64_postgres.py",
                                                       "tests/test_rf4_runbook_rehearsal.py"}
    # no deselection list is applied (owner, 2026-11-04): no node id, no --deselect, no DESELECT array, and
    # PYTEST_ADDOPTS is passed to the runner container empty
    drv = read("staging.sh")
    for f in harness_files():
        text = open(f, encoding="utf-8").read()
        assert "--deselect" not in "\n".join(code_lines(text)), f
        assert "DESELECT" not in "\n".join(code_lines(text)), f
        if not f.endswith("test_staging_unit.py"):
            assert not any(node.split("::")[1] in text for node in RESTORE_TESTS), (f, "names a restore test")
    runner = drv[drv.index("cmd_runner() {"):drv.index("cmd_canary() {")]
    assert '-e "PYTEST_ADDOPTS="' in runner and "addopts" not in runner
    assert "PROVENANCE deselected: none (0 node ids;" in runner
    assert 'PYTEST_ADDOPTS="${PYTEST_ADDOPTS:-}"' in read("run_group.sh")
    # restore still never happens in the host container: the only pytest run is the runner container's (docker create from
    # the runner image), and no inner script, which runs in the host container, runs pytest or names a restore test
    assert "pytest" not in "\n".join(code_lines(drv)).replace("python3-pytest", "")
    assert drv.count("cse-staging-run-group") == 1 and "docker create" in runner and "$RUNNER_IMAGE" in runner
    for f in (n for n in os.listdir(os.path.join(STAGING, "inner")) if n.endswith(".sh")):
        inner = "\n".join(code_lines(read("inner", f)))
        assert "pytest" not in inner and "run-group" not in inner, f
    # the host container's R1 gate is unchanged (s3 asserts the drop-in and the timer)
    assert "ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED" in read("inner", "stage_provision.sh")


def test_s3_the_r1_gate_precedes_units_and_the_timer_is_disabled_after_them():
    prov = read("inner", "stage_provision.sh")
    assert "ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED" in prov
    assert "systemctl disable --now cse-backup-restore-check.timer" in prov
    for branch in ("fresh)", "to-0017)"):
        body = prov[prov.index(f"  {branch}"):]
        body = body[:body.index(";;")]
        assert body.index("r1_gate") < body.index('"$P1"'), branch
    for branch in ("fresh)", "finish-upgrade)", "again)"):
        body = prov[prov.index(f"  {branch}"):]
        body = body[:body.index(";;")]
        assert " units" in body and body.index(" units") < body.index("r1_timer_off"), branch
    assert "R1-AUTHORIZED" not in "\n".join(l for l in code_lines(prov) if ">" in l and "R1-AUTHORIZED" in l
                                            and "ConditionPathExists" not in l)


def test_s4_never_armed_never_cse_and_no_contact_address():
    for f in harness_files():
        code = "\n".join(code_lines(open(f, encoding="utf-8").read()))
        assert "cse.lk" not in code, f
        assert not re.search(r"cse-(backfill|scheduler|capture|financial)\"?\s+(arm|disarm|acknowledge-block|"
                             r"resolve-hold|designate|capture|sweep|resume)\b", code), f
        assert not re.search(r"worker\.(backfill_ops|scheduler|market_capture)\s+(arm|capture|sweep)\b", code), f
        assert "record_arming" not in code, f
        assert not re.search(r"CSE_CAPTURE_CONTACT_EMAIL=[^\s'\"]", code), f
        assert "RESTIC_PASSWORD=" not in code, f


def test_s5_nothing_destructive_and_evidence_and_secrets_stay_outside_the_repository():
    drv = read("staging.sh")
    code = "\n".join(code_lines(drv))
    for forbidden in ("docker rm", "docker volume rm", "docker rmi", "docker image rm", "prune", " --rm"):
        assert forbidden not in code, forbidden
    evid = re.search(r'EVID="\$\{CSE_STAGING_EVIDENCE:-([^}]+)\}"', drv).group(1)
    secrets = re.search(r'SECRETS="\$\{CSE_STAGING_SECRETS:-([^}]+)\}"', drv).group(1)
    assert evid.startswith("/d/cse-staging/") and secrets.startswith("$LOCALAPPDATA/cse-staging/")
    assert "$EVID" not in secrets and "$REPO" not in evid


def test_s6_the_image_uses_the_ci_base_p1s_packages_step_and_poppler_24_02_0_and_holds_no_secret():
    df = read("Dockerfile")
    ci = open(os.path.join(REPO, ".github", "ci", "linux.Dockerfile"), encoding="utf-8").read()
    base = re.search(r"ubuntu:24\.04@sha256:[0-9a-f]{64}", df).group(0)
    assert base in ci
    assert "bash /opt/cse-staging/provision.sh packages" in df
    assert 'grep -qx "pdftotext version 24.02.0"' in df
    assert "python3-pytest" not in df[df.index("AS host"):df.index("AS runner")]
    assert not re.search(r"(?i)(password|secret|token)\s*=", "\n".join(code_lines(df)))


def test_s7_shell_syntax():
    bash = shutil.which("bash")
    if not bash or os.name != "posix":
        return
    for f in harness_files():
        if f.endswith(".sh"):
            subprocess.run([bash, "-n", f], check=True)


# ---- the post-T1 corrections (h1-h6) -------------------------------------------------------------------------------

def posix_bash():
    """Linux's bash, or Git Bash on Windows (never the WSL launcher, whose paths are another machine's)."""
    if os.name == "posix":
        cands = [shutil.which("bash")]
    else:
        cands = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe")]
    for b in cands:
        if b and os.path.isfile(b):
            try:
                if subprocess.run([b, "-c", "printf ok"], capture_output=True, text=True, timeout=60).stdout == "ok":
                    return b
            except (OSError, subprocess.SubprocessError):
                pass
    pytest.skip("no POSIX bash (Linux bash or Git Bash) to run the harness's own shell code")


def bash_path(p):
    """A path as posix_bash() sees it (Git Bash: /c/...)."""
    p = str(p)
    if os.name == "nt" and len(p) > 1 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def shell_function(text, name):
    """The source of the shell function `name` (one line, or up to a `}` in column 0)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    if lines[start].rstrip().endswith("}"):
        return lines[start] + "\n"
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1]) + "\n"


def shell_assignment(text, name):
    """The source of `name=...` or a multi-line array `name=(...)`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}="))
    end = start
    if lines[start].startswith(f"{name}=("):
        end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(")"))
    return "\n".join(lines[start:end + 1]) + "\n"


def run_bash(tmp_path, script, env=None):
    f = tmp_path / "case.sh"
    f.write_text(script, encoding="utf-8", newline="\n")
    e = dict(os.environ, PYEXE=bash_path(sys.executable), **(env or {}))
    return subprocess.run([posix_bash(), bash_path(f)], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=e, timeout=300)


def sha_lf(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r", b"")).hexdigest()


STUB_DOCKER = """#!/usr/bin/env bash
# a stand-in for the docker CLI: records each call and answers what the driver asks; never touches Docker
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1" in
  inspect) if [[ $2 == -f ]]; then echo "sha256:0000stubimage"; else cat "$STUB_INSPECT"; fi ;;
  exec)
    shift; [[ $1 == -i ]] && shift; shift
    case "$*" in
      *"sha256sum -- *"*) cat "$STUB_HASHES" ;;
      *"sha256sum /var/lib/cse-staging/layouts.pkl"*) echo "$STUB_LAYOUTS_SHA  /var/lib/cse-staging/layouts.pkl" ;;
      *seed_synthetic.py*) echo '{"mode": "full", "filings": 3, "stub": true}' ;;
      *stage_checks.sh*|*stage_scenarios.sh*) echo "PASS stub check" ;;
    esac ;;
esac
exit 0
"""

INJECTED = ["seed_synthetic.py", "stage_checks.sh", "stage_provision.sh", "stage_scenarios.sh", "stage_tree.py"]


def inspect_record(campaign="ut", suffix="", **changes):
    """A staging host's `docker inspect` record as T1's rung L1 and cmd_up create it (then `changes` applied)."""
    prefix = f"cse-stg-{campaign}{'-' + suffix if suffix else ''}-"
    vols = {"pgdata": "/var/lib/postgresql", "backup": "/srv/cse-backup", "offsite": "/srv/cse-backup/offsite-test"}
    hc = {"Privileged": False, "NetworkMode": "none", "RestartPolicy": {"Name": "no"}, "CgroupnsMode": "private",
          "CapAdd": ["CAP_SYS_ADMIN"], "Binds": None, "Devices": [], "DeviceRequests": None, "PidMode": "",
          "IpcMode": "private", "UTSMode": "", "UsernsMode": "", "SecurityOpt": None,
          "Tmpfs": {"/run": "", "/run/lock": "", "/srv/cse-staging-smalldisk": "size=64m"}}
    rec = {"HostConfig": hc, "Config": {"Labels": {"cse.staging.campaign": campaign}},
           "Mounts": [{"Type": "volume", "Name": prefix + v, "Source": f"/var/lib/docker/volumes/{prefix}{v}/_data",
                       "Destination": d} for v, d in vols.items()]}
    for k, v in changes.items():
        if k == "Mounts":
            rec["Mounts"] = v
        else:
            hc[k] = v
    return [rec]


def driver(tmp_path, *args, inspect=None, hashes=None, layouts=None):
    """staging.sh with a stub docker first on PATH, its evidence and secrets in tmp_path; returns (process, evidence)."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "docker").write_text(STUB_DOCKER, encoding="utf-8", newline="\n")
    (stub / "python").write_text(f'#!/bin/sh\nexec "{bash_path(sys.executable)}" "$@"\n', encoding="utf-8", newline="\n")
    for f in stub.iterdir():
        f.chmod(0o755)
    evid = tmp_path / "evid"
    (tmp_path / "inspect.json").write_text(json.dumps(inspect or inspect_record()), encoding="utf-8")
    if hashes is None:
        hashes = {n: sha_lf(os.path.join(STAGING, "inner" if n.startswith("stage_") else "", n)) for n in INJECTED}
    (tmp_path / "hashes.txt").write_text("".join(f"{h}  {n}\n" for n, h in hashes.items()), encoding="utf-8",
                                         newline="\n")
    lay_sha = ""
    if layouts is not None:
        (evid / "runner" / "harvest").mkdir(parents=True, exist_ok=True)
        (evid / "runner" / "harvest" / "layouts.pkl").write_bytes(layouts)
        lay_sha = hashlib.sha256(layouts).hexdigest()
    env = {"PATH": str(stub) + os.pathsep + os.environ.get("PATH", ""),
           "CSE_STAGING_REV": "0123456789abcdef0123456789abcdef01234567", "CSE_STAGING_CAMPAIGN": "ut",
           "CSE_STAGING_EVIDENCE": bash_path(evid), "CSE_STAGING_SECRETS": bash_path(tmp_path / "secrets"),
           "STUB_LOG": bash_path(tmp_path / "docker-calls.log"), "STUB_INSPECT": bash_path(tmp_path / "inspect.json"),
           "STUB_HASHES": bash_path(tmp_path / "hashes.txt"), "STUB_LAYOUTS_SHA": lay_sha}
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    p = run_bash(tmp_path, f'bash "{bash_path(os.path.join(STAGING, "staging.sh"))}" {quoted}\n', env)
    return p, evid


def test_h1_a_oneshot_result_is_read_only_after_that_invocation_has_ended(tmp_path):
    sc = read("inner", "stage_scenarios.sh")
    assert "is-active" not in "\n".join(code_lines(sc))
    body = shell_function(sc, "midrun_outage")
    assert 'unit_done cse-backup-dump.service "$inv" 120' in body and 'unit_journal "$inv"' in body
    assert '"Failed with result"' in body and "status=0" in body
    fn = shell_function(sc, "unit_done")
    stub = ('systemctl() { local c; c=$(( $(cat "$CNT") + 1 )); echo $c > "$CNT"; local l; l=$(sed -n "${c}p" "$STATES")\n'
            '  [[ -n $l ]] || l=$(tail -n 1 "$STATES"); read -r i a r s <<< "$l"\n'
            "  printf 'InvocationID=%s\\nActiveState=%s\\nResult=%s\\nExecMainStatus=%s\\n' \"$i\" \"$a\" \"$r\" \"$s\"; }\n"
            "sleep() { :; }\n")

    def case(states, timeout):
        (tmp_path / "states").write_text(states, encoding="utf-8", newline="\n")
        (tmp_path / "cnt").write_text("0", encoding="utf-8")
        p = run_bash(tmp_path, stub + fn + f'out=$(unit_done cse-backup-dump.service abc {timeout}); '
                                          'echo "rc=$? out=$out polls=$(cat "$CNT")"\n',
                     {"STATES": bash_path(tmp_path / "states"), "CNT": bash_path(tmp_path / "cnt")})
        assert p.returncode == 0, p.stderr
        return p.stdout.strip()

    # while the new run is 'activating', Result still says the previous run's 'success': it must not be reported
    assert case("abc activating success 0\nabc activating success 0\nabc failed exit-code 1\n", 30) \
        == "rc=0 out=result=exit-code status=1 polls=3"
    assert case("abc activating success 0\nabc inactive success 0\n", 30) == "rc=0 out=result=success status=0 polls=2"
    assert case("abc activating success 0\n", 0) == "rc=1 out=still activating after 0 s polls=1"
    assert case("abc activating success 0\nxyz activating success 0\n", 30) \
        == "rc=2 out=invocation abc replaced by xyz polls=2"


def test_h2_the_leak_check_uses_the_secret_as_p1_reads_it_and_keeps_its_controls(tmp_path):
    sc = read("inner", "stage_scenarios.sh")
    body = shell_function(sc, "offsite_failure")
    assert "-f /etc/cse/credentials/offsite-password" not in sc
    assert body.count('pass "control:') == 3 and body.count('bad "control:') == 3
    assert 'leak_scan "$cred" /srv/cse-backup/status /tmp/ledger-text.out' in body
    assert body.index("control:") < body.index("/srv/cse-backup/status")
    fns = shell_function(sc, "secret_pattern") + shell_function(sc, "leak_scan")
    secret = "FAKEcanary-h2-0001"
    (tmp_path / "cred").write_bytes(secret.encode() + b"\r\n")          # as T1's secret was written on Windows
    (tmp_path / "short").write_bytes(b" ab\r\n")
    (tmp_path / "leak").mkdir()
    (tmp_path / "leak" / "status.json").write_bytes(b'{"error": "x ' + secret.encode() + b' y"}')   # LF-free, inline
    (tmp_path / "clean").mkdir()
    (tmp_path / "clean" / "status.json").write_bytes(b'{"error": "nothing"}\n')
    t = {k: bash_path(tmp_path / k) for k in ("cred", "short", "leak", "clean", "pattern")}
    p = run_bash(tmp_path, 'python3() { "$PYEXE" "$@"; }\n' + fns +
                 f'secret_pattern "{t["cred"]}" > "{t["pattern"]}"; echo "pattern=$?"\n'
                 f'leak_scan "{t["cred"]}" "{t["leak"]}"; echo "leak=$?"\n'
                 f'leak_scan "{t["cred"]}" "{t["clean"]}"; echo "clean=$?"\n'
                 f'leak_scan "{t["cred"]}" "{t["clean"]}/absent"; echo "unreadable=$?"\n'
                 f'leak_scan "{t["short"]}" "{t["leak"]}"; echo "short=$?"\n'
                 f'leak_scan "{t["cred"]}" <(secret_pattern "{t["cred"]}" | awk \'{{printf "{{\\"e\\": \\"x %s y\\"}}", $0}}\'); '
                 'echo "inline-control=$?"\n')
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "pattern").read_bytes() == secret.encode() + b"\n"     # no CR: the bytes P1 itself uses
    assert p.stdout.split() == ["pattern=0", "leak=0", "clean=1", "unreadable=2", "short=2", "inline-control=0"]
    assert secret not in p.stdout + p.stderr


def test_h3_isolation_is_asserted_from_outside_and_inside_the_container(tmp_path):
    chk = os.path.join(STAGING, "isolation_check.py")

    def check(record, *args):
        p = subprocess.run([sys.executable, chk, "--campaign", "ut", *args], input=json.dumps(record),
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout

    rc, out = check(inspect_record())
    assert rc == 0 and "FAIL" not in out and out.count("PASS") == 17, out
    rc, out = check(inspect_record(CapAdd=["CAP_SYS_ADMIN", "CAP_NET_ADMIN"]), "--cap-add", "SYS_ADMIN", "net_admin")
    assert rc == 0, out                                                     # rung L2 with its named capability
    bad = {"Privileged": True, "NetworkMode": "bridge", "RestartPolicy": {"Name": "unless-stopped"},
           "CgroupnsMode": "host", "CapAdd": ["CAP_SYS_ADMIN", "CAP_NET_ADMIN"], "Binds": ["C:\\data:/data"],
           "PidMode": "host", "SecurityOpt": ["seccomp=unconfined"], "Devices": [{"PathOnHost": "/dev/sda"}],
           "Tmpfs": {"/run": "", "/mnt/extra": ""},
           "Mounts": inspect_record()[0]["Mounts"] + [{"Type": "bind", "Source": "/var/run/docker.sock",
                                                      "Destination": "/var/run/docker.sock"}]}
    for k, v in bad.items():
        rc, out = check(inspect_record(**{k: v}))
        assert rc == 1 and "FAIL" in out, (k, out)
    rc, out = check(inspect_record(suffix="up"), "--suffix", "up")
    assert rc == 0, out
    rc, out = check(inspect_record(suffix="up"))                           # another host's volumes
    assert rc == 1 and "FAIL mounts" in out

    sch = read("inner", "stage_checks.sh")
    iso = shell_function(sch, "isolation")
    assert 'bad "capabilities:' in iso and 'bad "mounts:' in iso
    defs = "".join(shell_assignment(sch, n) for n in ("CAPS", "DOCKER_DEFAULT_CAPS", "HOST_MOUNTS"))
    defs += "".join(shell_function(sch, n) for n in ("cap_names", "want_caps", "mount_points"))
    pseudo = "proc /proc proc rw 0 0\ntmpfs /run tmpfs rw 0 0\noverlay / overlay rw 0 0\n"
    t1 = "".join(f"/dev/sdd {m} ext4 rw 0 0\n" for m in ("/etc/hostname", "/etc/hosts", "/etc/resolv.conf",
                                                          "/srv/cse-backup", "/srv/cse-backup/offsite-test",
                                                          "/var/lib/postgresql"))
    (tmp_path / "mounts-t1").write_text(pseudo + t1, encoding="utf-8", newline="\n")
    (tmp_path / "mounts-bind").write_text(pseudo + t1 + "C:\\ /mnt/host 9p rw 0 0\n", encoding="utf-8", newline="\n")
    p = run_bash(tmp_path, defs +
                 'echo "l1=$(cap_names 00000000a82425fb)"; echo "want=$(want_caps)"\n'
                 'echo "default=$(cap_names 00000000a80425fb)"; echo "privileged=$(cap_names 000001ffffffffff)"\n'
                 'echo "l2=$(want_caps SYS_ADMIN cap_net_admin)"\n'
                 f'[[ "$(mount_points "{bash_path(tmp_path / "mounts-t1")}")" == "$HOST_MOUNTS" ]]; echo "t1-mounts=$?"\n'
                 f'[[ "$(mount_points "{bash_path(tmp_path / "mounts-bind")}")" == "$HOST_MOUNTS" ]]; echo "bind=$?"\n')
    assert p.returncode == 0, p.stderr
    got = dict(line.split("=", 1) for line in p.stdout.splitlines())
    l1 = ("AUDIT_WRITE CHOWN DAC_OVERRIDE FOWNER FSETID KILL MKNOD NET_BIND_SERVICE NET_RAW SETFCAP SETGID SETPCAP "
          "SETUID SYS_ADMIN SYS_CHROOT")
    assert got["l1"] == got["want"] == l1                                   # T1's observed CapEff 0xa82425fb
    assert got["default"] != got["want"] and got["privileged"] != got["want"]
    assert "NET_ADMIN" in got["l2"].split() and "SYS_ADMIN" in got["l2"].split()
    assert got["t1-mounts"] == "0" and got["bind"] == "1"

    # the driver's `isolation`: the inspect check, then the in-container check, each with its own record
    for name, record, want in (("l1", inspect_record(), 0), ("bridged", inspect_record(NetworkMode="bridge"), 1)):
        (tmp_path / name).mkdir()
        p, evid = driver(tmp_path / name, "isolation", inspect=record)
        assert p.returncode == want, p.stdout + p.stderr
        log = (evid / "results" / "main-isolation-inspect.txt").read_text(encoding="utf-8")
        assert log.startswith("PROVENANCE ") and log.rstrip().endswith(f"exit {want}")
        assert ("PASS network: none" if want == 0 else "FAIL network: none") in log
        assert (evid / "results" / "main-stage_checks-isolation.txt").exists()


def test_h4_every_run_and_inject_records_the_exact_harness_versions(tmp_path):
    hashes = {n: sha_lf(os.path.join(STAGING, "inner" if n.startswith("stage_") else "", n)) for n in INJECTED}
    hashes["stage_tree.py"] = "0" * 64                                     # a copy that is not the working tree's
    p, evid = driver(tmp_path, "run", "", "stage_checks.sh", "invariants", hashes=hashes)
    assert p.returncode == 0, p.stdout + p.stderr
    lines = (evid / "results" / "main-stage_checks-invariants.txt").read_text(encoding="utf-8").splitlines()
    prov = [line for line in lines if line.startswith("PROVENANCE ")]
    assert lines[:len(prov)] == prov and lines[len(prov)] == "PASS stub check" and lines[-1] == "exit 0"
    assert "campaign=ut" in prov[0] and "revision=0123456789abcdef" in prov[0] and "image=sha256:0000stubimage" in prov[0]
    assert f"PROVENANCE staging.sh sha256={sha_lf(os.path.join(STAGING, 'staging.sh'))} (the driver)" in prov
    for n in INJECTED:
        flag = "DIFFERENT" if n == "stage_tree.py" else "same"
        assert f"PROVENANCE {n} sha256={hashes[n]} working-tree={flag}" in prov, n
    p, evid = driver(tmp_path, "inject")
    assert p.returncode == 0, p.stdout + p.stderr
    log = (evid / "logs" / "driver.log").read_text(encoding="utf-8")
    for n in INJECTED:
        assert f"inject: cse-stg-ut-host {n} sha256={sha_lf(os.path.join(STAGING, 'inner' if n.startswith('stage_') else '', n))}" in log, n
    runner = read("staging.sh")
    runner = runner[runner.index("cmd_runner() {"):runner.index("cmd_canary() {")]
    for item in ('"$EVID/runner/$run.provenance.txt"', "run_group.sh sha256", "PROVENANCE deselected: none (0 node ids;",
                 "tree under test"):
        assert item in runner, item


def test_h5_fingerprint_snapshots_are_never_reused_and_are_compared_by_fpcompare(tmp_path):
    sch = read("inner", "stage_checks.sh")
    fp = shell_function(sch, "fingerprint")
    assert 'snapshot_dir "${1:-run}"' in fp and "/var/lib/cse-staging/fingerprint" not in fp
    assert 'fpcompare) fpcompare "${2:?snapshot directory A}" "${3:?snapshot directory B}"' in sch
    defs = "fail=0\n" + "".join(shell_function(sch, n) for n in ("pass", "bad", "info", "snapshot_dir", "fpcompare"))
    defs += shell_assignment(sch, "FP_COMPONENTS")
    p = run_bash(tmp_path, f'STATE="{bash_path(tmp_path / "state")}"\n' + defs + """
a=$(snapshot_dir before-again); b=$(snapshot_dir before-again); c=$(snapshot_dir after-again)
echo "distinct=$([[ -d $a && -d $b && $a != "$b" ]] && echo yes)"
snapshot_dir 'bad/label' >/dev/null; echo "bad-label=$?"
for d in "$a" "$c"; do for f in "${FP_COMPONENTS[@]}"; do echo "same $f" > "$d/$f"; done; done
echo "attempt one left behind" > "$b/schema.sql"
fpcompare "$a" "$c" > "$STATE/cmp1"; echo "identical-pass=$(grep -c '^PASS identical' "$STATE/cmp1")"
echo "fail-after-identical=$fail"
echo changed >> "$c/grants.txt"; rm "$c/units.txt"
fpcompare "$a" "$c" > "$STATE/cmp2"; grep -E '^FAIL' "$STATE/cmp2"; echo "fail-after-change=$fail"
echo "kept=$(ls "$STATE/fingerprints" | wc -l) $(cat "$b/schema.sql")"
""")
    assert p.returncode == 0, p.stderr
    out = p.stdout.splitlines()
    assert "distinct=yes" in out and "bad-label=1" in out
    assert "identical-pass=7" in out and "fail-after-identical=0" in out
    assert "FAIL differs: grants.txt (1 lines)" in out and "FAIL missing: units.txt" in out and "fail-after-change=1" in out
    assert "kept=3 attempt one left behind" in out                          # the earlier attempts are all still there


def test_h6_the_seed_records_its_command_identity_parameters_input_and_result(tmp_path):
    p, evid = driver(tmp_path, "seed", "", "full", "990001", "3", layouts=b"synthetic layouts for the test")
    assert p.returncode == 0, p.stdout + p.stderr
    log = (evid / "results" / "main-seed-full.txt").read_text(encoding="utf-8")
    lay = hashlib.sha256(b"synthetic layouts for the test").hexdigest()
    assert log.startswith("PROVENANCE utc=") and "PROVENANCE seed_synthetic.py sha256=" in log
    assert "PROVENANCE seed: mode=full start-fid=990001 count=3 identity=cse-worker" in log
    assert f"PROVENANCE seed: layouts.pkl sha256={lay} (runner/harvest/layouts.pkl), in the container sha256={lay}" in log
    assert ("PROVENANCE seed: command: docker exec cse-stg-ut-host bash -c \"chmod 0644 /var/lib/cse-staging/layouts.pkl"
            " && cd / && runuser -u cse-worker -- env PYTHONDONTWRITEBYTECODE=1 python3 /opt/cse-staging/seed_synthetic.py"
            " --layouts /var/lib/cse-staging/layouts.pkl --mode full --start-fid 990001 --count 3\"") in log
    assert '{"mode": "full", "filings": 3, "stub": true}' in log
    assert re.search(r"PROVENANCE seed: finished utc=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ exit=0\nexit 0\n$", log)
    p, _ = driver(tmp_path, "seed", "", "full", "99000x", "3", layouts=b"x")
    assert p.returncode == 2                                                # parameters are validated before anything


# ---- the procedures ST-27 to ST-29 (s8, h7, h8) -------------------------------------------------------------------------

VERIFIER_URL = "postgresql://cse_verifier@/cse?host=/var/run/postgresql"
LOGIN_STEPS = ("reader_step1", "reader_step2", "reader_step4", "reader_step5")     # 20.6 steps 1, 2, 4, 5: as the login
NEW_FUNCTIONS = ("login_psql", "login_verify", "migration_preflight", "verifier_preflight", "super_counts", "rf4_dir",
                 "run_json", "jassert", "refused_with", "preflight_clean", "preflight_clean_as_login", "rf4verify", "reader_step1", "reader_peer_controls", "reader_step2",
                 "reader_step4", "reader_step5", "reader", "regx03")
# a SET ROLE / SET SESSION AUTHORIZATION statement (the prose "no SET ROLE" in a message or a comment is not one)
SET_ROLE = re.compile(r"\bset\s+(local\s+|session\s+)?role\s+(to\s+)?[\w\"']|\bset\s+(local\s+|session\s+)?authorization|"
                      r"\bset\s+session\s+authorization|\bsession\s+authorization\b", re.I)


def code_of(text):
    """A shell function's code: no comment-only lines and no trailing `  # comment`."""
    return "\n".join(re.sub(r"\s#\s.*$", "", line) for line in code_lines(text))


def test_s8_the_logins_steps_run_as_the_login_by_peer_authentication_and_nothing_restores_arms_or_reaches_out():
    sch = read("inner", "stage_checks.sh")
    body = {n: shell_function(sch, n) for n in NEW_FUNCTIONS}
    code = {n: code_of(b) for n, b in body.items()}
    assert re.search(r"^VERIFIER_URL='" + re.escape(VERIFIER_URL) + "'$", sch, re.M)    # the URL names the role
    assert "cse_verifier@" in VERIFIER_URL and "cse-verifier@" not in VERIFIER_URL
    # the two ways into the login's session: the OS user cse-verifier, the URL, nothing else
    assert "runuser -u cse-verifier --" in code["login_psql"] and '-d "$VERIFIER_URL"' in code["login_psql"]
    assert "-U " not in code["login_psql"] and "postgres" not in code["login_psql"]
    assert "runuser -u cse-verifier --" in code["login_verify"] and 'DATABASE_URL="$VERIFIER_URL"' in code["login_verify"]
    assert "python3 -m worker.financial_f4_store verify" in code["login_verify"]
    assert "--limit" not in code["login_verify"] and "--no-rebuild" not in code["login_verify"]      # a full scan
    assert "psql" not in code["login_verify"] and "postgres" not in code["login_verify"]
    # section 12 step 2 and section 20.6 step 3: the preflight through the migration role
    assert "runuser -u cse-migrator --" in code["migration_preflight"] and "--migration-role" in code["migration_preflight"]
    # runbook section 12 step 2, second route (AR-0003-1, owner question): the preflight as the login, through DATABASE_URL
    assert "runuser -u cse-verifier --" in code["verifier_preflight"] and 'DATABASE_URL="$VERIFIER_URL"' in code["verifier_preflight"]
    assert "python3 -m worker.financial_f4_store preflight" in code["verifier_preflight"]
    assert "--migration-role" not in code["verifier_preflight"] and "CSE_DB_USER" not in code["verifier_preflight"]
    assert "psql" not in code["verifier_preflight"] and "postgres" not in code["verifier_preflight"]
    assert "verifier_preflight" in code["preflight_clean_as_login"] and "financial_f4_store" not in code["preflight_clean_as_login"]
    assert "clean" in code["preflight_clean_as_login"] and "RC -eq 0" in code["preflight_clean_as_login"]
    # steps 1, 2, 4 and 5: through the login, and through no other route
    for n in LOGIN_STEPS:
        assert re.search(r"\blogin_(psql|verify)\b", code[n]), n
        assert not SET_ROLE.search(code[n]), n
        assert not re.search(r"set_config|\bpsu\b|\bas_role\b|runuser -u (?!cse-verifier)|-U\s|\bsudo\b|\bsu\b|\bpostgres\b|pg_authid",
                             code[n], re.I), n
    assert "login_verify" in code["reader_step4"] and "super_counts" in code["reader_step4"]    # the superuser's counts
    assert "begin;" in body["reader_step5"] and "rollback;" in body["reader_step5"] \
        and "insufficient_privilege" in body["reader_step5"] and "financial_f4_extractions" in body["reader_step5"]
    # the superuser (psu) appears only where the text asks for it: the counts compared in step 4, and the peer controls
    assert {n for n, c in code.items() if re.search(r"\bpsu\b", c)} == {"super_counts", "reader_peer_controls"}
    # no SET ROLE anywhere in the new procedures
    for n, c in code.items():
        assert not SET_ROLE.search(c), n
    # ST-27 runs the preflight through the migration role and the verify as the login; ST-28 runs steps 1-5 (not 6)
    assert "preflight_clean " in code["rf4verify"] and "login_verify" in code["rf4verify"]
    assert "preflight_clean_as_login" in code["rf4verify"]                         # both preflight routes (runbook section 12)
    for n in ("reader_step1", "reader_peer_controls", "reader_step2", "preflight_clean", "reader_step4", "reader_step5"):
        assert n in code["reader"], n
    assert "reader_step6" not in sch and "step 6" in body["reader"]
    # nothing in them restores, arms, captures, starts a service, or reaches a network
    joined = "\n".join(code.values())
    for forbidden in ("pg_restore", "restore_check", "restore-check", "provision", "cse-ops", "systemctl", "docker", "curl",
                      "wget", "/dev/tcp", "ssh ", "scp ", "apt-get", "pip ", " nc ", "cse.lk", "cse-backfill",
                      "cse-scheduler", "cse-financial", "worker.scheduler", "worker.market_capture", "worker.backfill",
                      "arming", "R1-AUTHORIZED", "getent", "dig ", "nslookup"):
        assert forbidden not in joined, forbidden
    assert re.findall(r"worker\.[a-z_0-9.]+", joined) == ["worker.financial_f4_store"] * 3     # verify, both preflights
    # the dispatcher knows the three subcommands
    assert re.search(r"^  ledger\|roles\|.*\|rf4verify\|reader\|regx03\) \"\$cmd\" ;;$", sch, re.M)


def run_procedure(tmp_path, name, functions, driver_script, env=None):
    """The listed functions of stage_checks.sh, run in bash against stubs of the container's commands."""
    sch = read("inner", "stage_checks.sh")
    defs = "fail=0\nAPP=\"$APPDIR\"\nSTATE=\"$STATEDIR\"\n" + shell_assignment(sch, "RF4") + shell_assignment(sch, "VERIFIER_URL")
    defs += "".join(shell_function(sch, n) for n in ("pass", "bad", "info") + tuple(functions))
    base = {"APPDIR": bash_path(tmp_path / "app"), "STATEDIR": bash_path(tmp_path / "state")}
    (tmp_path / "state").mkdir(exist_ok=True)
    p = run_bash(tmp_path, 'python3() { "$PYEXE" "$@"; }\n' + defs + driver_script + '\necho "fail=$fail"\n',
                 dict(base, **(env or {})))
    assert p.returncode == 0, (name, p.stdout, p.stderr)
    return p.stdout.splitlines()


def verdicts(lines):
    return [line.split(" ", 1)[0] for line in lines if line.startswith(("PASS ", "FAIL "))]


GOOD_VERIFY = {"clean": True, "scope": {"complete": True, "limit": None}, "problems": [], "extractions": 3, "runs": 3,
               "rebuilt": 3, "snapshot": {"isolation": "repeatable read", "read_only": True, "pg_snapshot": "1:1:"}}
STUBS = """
login_verify() { cat "$VERIFY_JSON"; return "${VERIFY_RC:-0}"; }
migration_preflight() { cat "$PRE_JSON"; return "${PRE_RC:-0}"; }
verifier_preflight() { cat "$VPRE_JSON"; return "${VPRE_RC:-0}"; }
super_counts() { echo "${SUPER:-3 3}"; }
login_psql() {
  case "$2" in
    *"current_user, session_user"*) echo "${S1-cse_verifier|cse_verifier|none|t|t}" ;;
    *"format('login="*) echo "${S2-login=true inherit=true superuser=false createrole=false createdb=false replication=false bypassrls=false database_create=false database_temporary=false role_settings=0}" ;;
    *"string_agg(g.rolname"*) echo "cse_reader inherit=true set=false admin=false" ;;
    *"select (select count(*) from financial_f4_extractions)"*) echo "${MINE-3 3}" ;;
    *"begin;"*) printf '%s\\n' "${S5-NOTICE:  refused: sqlstate=42501 message=permission denied for table financial_f4_extractions}"; return "${S5_RC:-0}" ;;
  esac
}
"""
GOOD_ATTRS = ("login=true inherit=true superuser=false createrole=false createdb=false replication=false bypassrls=false "
              "database_create=false database_temporary=false role_settings=0")


def write_json(tmp_path, name, obj):
    f = tmp_path / name
    f.write_text(json.dumps(obj), encoding="utf-8", newline="\n")
    return bash_path(f)


def test_h7_the_procedures_fail_when_a_condition_does_not_hold(tmp_path):
    base = {"PRE_JSON": write_json(tmp_path, "pre_ok.json", {"clean": True, "command": "preflight"}),
            "VPRE_JSON": write_json(tmp_path, "vpre_ok.json", {"clean": True, "command": "preflight"}),
            "VERIFY_JSON": write_json(tmp_path, "ver_ok.json", GOOD_VERIFY)}
    pre_bad = write_json(tmp_path, "pre_bad.json", {"clean": False, "command": "preflight"})

    def run(name, funcs, script, **env):
        (tmp_path / name).mkdir(exist_ok=True)
        return run_procedure(tmp_path / name, name, funcs, STUBS + script, dict(base, **env))

    # ST-27: the good case, then each failing condition by itself
    funcs = ("jassert", "run_json", "rf4_dir", "preflight_clean", "preflight_clean_as_login", "rf4verify")
    out = run("ok", funcs, "rf4verify")
    assert verdicts(out) == ["PASS"] * 9 and out[-1] == "fail=0", out
    # the second preflight route (as the login): unclean, "unverifiable" (exit 2, as for a login that cannot read the ledger)
    # and a crash are each a FAIL, and the migration-role route does not stand in for it
    vpre_bad = write_json(tmp_path, "vpre_bad.json", {"clean": False, "command": "preflight"})
    vpre_unverifiable = write_json(tmp_path, "vpre_unv.json", {"clean": False, "ledger": {"status": "unverifiable"}})
    for label, env in (("login preflight unclean", dict(VPRE_JSON=vpre_bad, VPRE_RC="1")),
                       ("login preflight unverifiable", dict(VPRE_JSON=vpre_unverifiable, VPRE_RC="2")),
                       ("login preflight exit 0 but not clean", dict(VPRE_JSON=vpre_bad))):
        out = run("vpre", funcs, "rf4verify", **env)
        assert "FAIL" in verdicts(out) and out[-1] == "fail=1", (label, out)
        assert verdicts(out).count("PASS") >= 7, (label, out)                       # the other steps are not hidden by it
    limited = dict(GOOD_VERIFY, clean=False, scope={"complete": False, "limit": 5, "detail": "partial"})
    cases = (
        ("unclean preflight", dict(PRE_JSON=pre_bad), 1),
        ("preflight exit 2", dict(PRE_RC="2"), 1),
        ("verify exit 1", dict(VERIFY_RC="1"), 1),
        ("a problem", dict(VERIFY_JSON=write_json(tmp_path, "v1.json", dict(GOOD_VERIFY, clean=False,
                                                                              problems=[{"kind": "missing"}]))), 2),
        ("limited scan", dict(VERIFY_JSON=write_json(tmp_path, "v2.json", limited), VERIFY_RC="1"), None),
        ("no data", dict(VERIFY_JSON=write_json(tmp_path, "v3.json", dict(GOOD_VERIFY, extractions=0, runs=0, rebuilt=0))), 1),
        ("a run not rebuilt", dict(VERIFY_JSON=write_json(tmp_path, "v4.json", dict(GOOD_VERIFY, rebuilt=2))), 1),
    )
    for i, (label, env, nfail) in enumerate(cases):
        out = run(f"s27-{i}", funcs, "rf4verify", **env)
        got = verdicts(out).count("FAIL")
        assert got >= 1 and out[-1] == "fail=1" and nfail in (None, got), (label, out)

    # ST-28 step 1: the login's own identity, no SET ROLE
    assert verdicts(run("s1a", ("reader_step1",), "reader_step1")) == ["PASS"]
    for bad in ("cse_verifier|cse_verifier|cse_reader|t|t",       # SET ROLE cse_reader in effect
                "postgres|postgres|none|t|t",                      # a superuser session
                "cse_reader|cse_verifier|none|t|t", "cse_verifier|cse_verifier|none|f|t", "cse_verifier|cse_verifier|none|t|f", ""):
        assert verdicts(run("s1b", ("reader_step1",), "reader_step1", S1=bad)) == ["FAIL"], bad
    # step 2: the attributes, CREATE and TEMPORARY, the settings
    assert verdicts(run("s2a", ("reader_step2",), "reader_step2")) == ["PASS"]
    for field, value in (("superuser", "true"), ("createrole", "true"), ("createdb", "true"), ("replication", "true"),
                         ("bypassrls", "true"), ("database_create", "true"), ("database_temporary", "true"),
                         ("role_settings", "1"), ("login", "false"), ("inherit", "false")):
        mutated = re.sub(rf"\b{field}=\w+", f"{field}={value}", GOOD_ATTRS)
        assert mutated != GOOD_ATTRS and verdicts(run("s2b", ("reader_step2",), "reader_step2", S2=mutated)) == ["FAIL"], field
    # step 4: clean, complete, repeatable read + read-only, counts equal the superuser's
    s4 = ("jassert", "run_json", "reader_step4")
    d = tmp_path / "d4"
    d.mkdir()
    ok4 = run("s4a", s4, f'reader_step4 "{bash_path(d)}"')
    assert verdicts(ok4) == ["PASS"] * 6 and ok4[-1] == "fail=0", ok4
    assert verdicts(run("s4b", s4, f'reader_step4 "{bash_path(d)}"', MINE="2 3")).count("FAIL") == 1
    snap = dict(GOOD_VERIFY, snapshot={"isolation": "read committed", "read_only": True})
    assert "FAIL" in verdicts(run("s4c", s4, f'reader_step4 "{bash_path(d)}"', VERIFY_JSON=write_json(tmp_path, "v5.json", snap)))
    snap = dict(GOOD_VERIFY, snapshot={"isolation": "repeatable read", "read_only": False})
    assert "FAIL" in verdicts(run("s4d", s4, f'reader_step4 "{bash_path(d)}"', VERIFY_JSON=write_json(tmp_path, "v6.json", snap)))
    assert "FAIL" in verdicts(run("s4e", s4, f'reader_step4 "{bash_path(d)}"', SUPER="4 3", MINE="4 3"))   # verify read 3
    # step 5: refused with insufficient privilege (42501); an INSERT that succeeds, or fails otherwise, is a FAIL
    assert verdicts(run("s5a", ("reader_step5",), "reader_step5")) == ["PASS", "PASS"]
    for label, env in (("insert succeeded", dict(S5="INSERT 0 1")),
                       ("not-null violation", dict(S5="ERROR:  null value in column", S5_RC="3")),
                       ("refused, but another table", dict(S5="NOTICE:  refused: sqlstate=42501 message=permission denied for table x")),
                       ("not refused: no notice", dict(S5="ROLLBACK"))):
        assert "FAIL" in verdicts(run("s5b", ("reader_step5",), "reader_step5", **env)), label


def test_h8_st29_requires_pass_for_the_verifier_checks_the_owner_members_check_and_the_five_ledger_checks(tmp_path):
    sch = read("inner", "stage_checks.sh")
    body = shell_function(sch, "regx03")
    assert 'p3_container_probes.sh" p1-verify' in body and "/tmp/p1_verify_db.json" in body
    required = ("role_cse_verifier", "verifier_membership_reader_only", "verifier_no_database_create_or_temporary",
                "verifier_no_role_settings", "owner_members_designed_only", "ledger_writable_only_by_owner",
                "ledger_ordinary_table", "ledger_permanent", "ledger_no_row_level_security", "ledger_no_inheritance")
    for n in required:
        assert f'"{n}"' in body, n
    # fail closed (AR-0003-1 F-1): the evaluator's exit status and the count of the ten PASS lines decide, not the absence of FAIL
    assert "erc=$?" in body and "[[ $erc -ne 0 ]]" in body and "[[ $npass -ne 10 ]]" in body
    # the names exist in the frozen verify_server (role_<name> is built from ROLES)
    vs = open(os.path.join(REPO, "worker", "ops", "verify_server.py"), encoding="utf-8").read()
    assert 'rep.add(f"role_{name}"' in vs and '"cse_verifier": True' in vs
    for n in required[1:]:
        assert f'"{n}"' in vs, n
    # run the procedure against a stub probe that writes a report
    app = tmp_path / "app" / "ops" / "tests"
    app.mkdir(parents=True)
    checks = [{"check": n, "status": "PASS", "detail": "ok"} for n in required] \
        + [{"check": "time_synchronised", "status": "FAIL", "detail": "container"}]

    def nodetail(rows, name):
        return [{k: v for k, v in c.items() if k != "detail"} if c["check"] == name else c for c in rows]

    scenarios = {"ok": (checks, 0, 0),
                 "a check FAILs": ([dict(c, status="FAIL") if c["check"] == "ledger_no_inheritance" else c for c in checks], 0, 1),
                 "a check is absent": ([c for c in checks if c["check"] != "owner_members_designed_only"], 0, 1),
                 "a check only WARNs": ([dict(c, status="WARN") if c["check"] == "ledger_permanent" else c for c in checks], 0, 1),
                 "the probe fails": (checks, 1, 1),
                 # the auditor's case: a required check without `detail` makes the evaluator raise after printing its INFO
                 # lines (before any PASS line, and with some of them printed): ST-29 must FAIL, not pass on "no FAIL line"
                 "the evaluator crashes after its INFO lines": (nodetail(checks, "role_cse_verifier"), 0, 1),
                 "the evaluator crashes after some PASS lines": (nodetail(checks, "owner_members_designed_only"), 0, 1)}
    for label, (rep, probe_rc, want_fail) in scenarios.items():
        (tmp_path / "rep.json").write_text(json.dumps({"checks": rep}), encoding="utf-8", newline="\n")
        probe = ('#!/usr/bin/env bash\ncp "$REPORT" /tmp/p1_verify_db.json\necho \'{"checks": []}\' > /tmp/p1_verify_os.json\n'
                 f'exit {probe_rc}\n')
        (app / "p3_container_probes.sh").write_text(probe, encoding="utf-8", newline="\n")
        out = run_procedure(tmp_path, label, ("run_json", "rf4_dir", "regx03"), "regx03",
                            {"REPORT": bash_path(tmp_path / "rep.json")})
        v = verdicts(out)
        assert out[-1] == f"fail={want_fail}", (label, out)
        if label == "ok":
            assert v == ["PASS"] * 12, out          # the probe, the ten required checks, then the evaluator's own exit
            assert any(line.startswith("INFO verify_server time_synchronised: FAIL") for line in out)
        else:
            assert "FAIL" in v, (label, out)
            if "crashes" in label:
                assert any(line.startswith("FAIL regx03: the evaluator exited ") for line in out), (label, out)
                assert not any(line.startswith("PASS regx03:") for line in out), (label, out)


# ---- restore under the owner's restore authorization (r1-r8) ----------------------------------------------------------

import argparse  # noqa: E402
import datetime  # noqa: E402
import importlib.util  # noqa: E402

RESTORE_DIR = os.path.join(REPO, "ops", "restore")
TOOL_FILE = os.path.join(RESTORE_DIR, "isolated_restore.py")
REV40 = "0123456789abcdef0123456789abcdef01234567"
IMG = "sha256:" + "ab" * 32
NOW = datetime.datetime(2026, 11, 5, 12, 0, tzinfo=datetime.timezone.utc)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GATE = load(os.path.join(STAGING, "restore_gate.py"), "restore_gate")
IR = load(TOOL_FILE, "isolated_restore")


def ra_text(**changes):
    """An RA as the vault's CSE Restore Authorization Template fills it (YAML front matter, then the body)."""
    fields = {"type": "restore-authorization", "id": "RA-0001", "operation": "R1", "campaign": "ut",
              "build_sha": REV40, "image_id": IMG, "target": "cse-stg-ut-host", "expires": "2026-11-06T18:00:00+05:30",
              "single_use": "true", "decided": "2026-11-05", "owner_words": '"Yes, run R1 for campaign ut."',
              "tags": "[cse]"}
    drop = changes.pop("_drop", ())
    extra = changes.pop("_extra", "")
    fields.update(changes)
    lines = [f"{k}: {v}" for k, v in fields.items() if k not in drop]
    return "---\n" + "\n".join(lines) + "\n" + extra + "---\n# RA-0001: restore authorization\n\nBody text.\n"


def ra_args(tmp_path, **changes):
    a = {"operation": "R1", "campaign": "ut", "revision": REV40, "label_revision": REV40, "image_id": IMG,
         "tagged_image_id": IMG, "target": "cse-stg-ut-host", "consumed": str(tmp_path / "evid" / "restore" / "c.log"),
         "evidence": str(tmp_path / "evid")}
    a.update(changes)
    return argparse.Namespace(**a)


def verdict(raw, ra_path, a, clock=NOW):
    rows = list(GATE.validate(raw, ra_path, a, clock=clock))
    return all(ok for ok, _ in rows), [t for ok, t in rows if not ok]


def test_r1_the_ra_validation_matrix_refuses_every_invalid_ra(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ra = vault / "RA-0001.md"
    good = ra_text().encode()
    a = ra_args(tmp_path)
    ok, why = verdict(good, str(ra), a)
    assert ok and why == [], why
    assert verdict(good.replace(b"\n", b"\r\n"), str(ra), a)[0]          # a file saved with CRLF is the same RA
    cases = {
        # expired, or an expiry that is not an absolute time
        "expired": ra_text(expires="2026-11-05T17:29:59+05:30"),
        "expiry without an offset": ra_text(expires="2026-11-06T18:00:00"),
        "expiry that is not a time": ra_text(expires="tomorrow"),
        # mismatched against the harness's own values
        "operation": ra_text(operation="R2"),
        "operation not R1 or R2": ra_text(operation="R3"),
        "campaign": ra_text(campaign="t2s1"),
        "build_sha": ra_text(build_sha="f" * 40),
        "build_sha short": ra_text(build_sha=REV40[:12]),
        "image_id": ra_text(image_id="sha256:" + "cd" * 32),
        "image_id without sha256:": ra_text(image_id="ab" * 32),
        "target": ra_text(target="cse-stg-ut-r2"),
        "a production target": ra_text(target="production"),
        # malformed
        "template id": ra_text(id="RA-NNNN"),
        "type": ra_text(type="decision-request"),
        "single use false": ra_text(single_use="false"),
        "no owner words": ra_text(owner_words='""'),
        "decided in the future": ra_text(decided="2026-11-09"),
        "decided not a date": ra_text(decided="YYYY-MM-DD"),
        "missing key": ra_text(_drop=("target",)),
        "unknown key": ra_text(_extra="approved_by: someone\n"),
        "duplicate key": ra_text(_extra="operation: R1\n"),
        "not key: value": ra_text(_extra="just text\n"),
    }
    for label, text in cases.items():
        ok, why = verdict(text.encode(), str(ra), a)
        assert not ok and why, label
    for label, raw in {"no front matter": b"# RA-0001\noperation: R1\n", "unclosed": b"---\nid: RA-0001\n",
                       "empty": b"", "not UTF-8": b"---\nid: \xff\n---\n"}.items():
        ok, why = verdict(raw, str(ra), a)
        assert not ok and why[0].startswith("well-formed"), label
    # the harness's values disagree among themselves: the image label or the tagged image is another build
    for label, change in {"label": {"label_revision": "e" * 40}, "tagged image": {"tagged_image_id": "sha256:" + "0" * 64},
                          "harness revision": {"revision": "e" * 40}, "other target": {"target": "cse-stg-ut-x-host"},
                          "other campaign": {"campaign": "t3a"}, "operation asked": {"operation": "R2"}}.items():
        ok, why = verdict(good, str(ra), ra_args(tmp_path, **change))
        assert not ok, label
    # consumed: by id, or by the same file under another id
    log = tmp_path / "evid" / "restore" / "c.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"2026-11-05T00:00:00+00:00\tRA-0001\t{'0' * 64}\tR1\tcse-stg-ut-host\tut\n", encoding="utf-8")
    assert not verdict(good, str(ra), a)[0]
    import hashlib as _h
    log.write_text(f"2026-11-05T00:00:00+00:00\tRA-0009\t{_h.sha256(good).hexdigest()}\tR1\tx\tut\n", encoding="utf-8")
    assert not verdict(good, str(ra), a)[0]
    log.write_text("a malformed line\n", encoding="utf-8")
    ok, why = verdict(good, str(ra), a)
    assert not ok and any("cannot be read" in w for w in why)
    log.unlink()
    # the evidence directory inside the RA's directory (or the RA inside the evidence): refused
    assert not verdict(good, str(ra), ra_args(tmp_path, evidence=str(vault / "evid")))[0]
    assert not verdict(good, str(tmp_path / "evid" / "RA-0001.md"), a)[0]


def test_r1b_the_ra_command_reads_the_ra_once_consumes_it_only_when_valid_and_never_writes_beside_it(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ra = vault / "RA-0001.md"
    ra.write_bytes(ra_text(expires="2099-01-01T00:00:00+05:30").encode())
    before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns, sha_lf(p)) for p in vault.iterdir())
    log = tmp_path / "evid" / "restore" / "ra-consumed.log"
    base = ["ra", "--ra", str(ra), "--operation", "R1", "--campaign", "ut", "--revision", REV40, "--label-revision",
            REV40, "--image-id", IMG, "--tagged-image-id", IMG, "--target", "cse-stg-ut-host", "--consumed", str(log),
            "--evidence", str(tmp_path / "evid")]
    run = lambda *extra: subprocess.run([sys.executable, os.path.join(STAGING, "restore_gate.py"), *base, *extra],
                                        capture_output=True, text=True, timeout=60)
    p = run()
    assert p.returncode == 0 and not log.exists(), p.stdout                 # validation alone writes nothing
    p = run("--consume")
    assert p.returncode == 0 and "CONSUMED RA-0001" in p.stdout, p.stdout
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    p = run("--consume")
    assert p.returncode == 1 and "FAIL single use" in p.stdout and "REFUSED" in p.stdout, p.stdout
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1          # a refusal consumes nothing
    after = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns, sha_lf(p)) for p in vault.iterdir())
    assert after == before                                                   # the vault is untouched


STUB_RESTORE = """#!/usr/bin/env bash
# a stand-in for the docker CLI for `staging.sh restore`: records each call; never touches Docker
printf '%s\\n' "$*" >> "$STUB_LOG"
consumed() { grep -q "$STUB_RA_ID" "$STUB_CONSUMED" 2>/dev/null && echo yes || echo no; }
case "$1" in
  inspect)
    if [[ $2 == -f ]]; then
      case "$3" in *State.Running*) echo true ;; *.Image*) echo "$STUB_IMAGE" ;; esac
    else cat "$STUB_R2_INSPECT"; fi ;;
  image) case "$4" in *.Id*) echo "$STUB_TAGGED" ;; *revision*) echo "$STUB_LABEL" ;; esac ;;
  ps) [[ -z ${STUB_EXISTS:-} ]] || echo "$STUB_EXISTS" ;;
  run) echo "consumed-at-run=$(consumed)" >> "$STUB_LOG"; echo stubcontainer ;;
  exec)
    case "$*" in
      *"stage_restore.sh r1-check"*) echo "PASS stub r1-check"; exit "${STUB_R1CHECK_RC:-0}" ;;
      *"stage_restore.sh r1"*) echo "consumed-at-r1=$(consumed)" >> "$STUB_LOG"; echo "PASS stub r1" ;;
      *"stage_restore.sh source"*) cat "$STUB_SOURCE" ;;
      *"stage_restore.sh r2-teardown"*) echo "PASS stub teardown" ;;
      *"stage_restore.sh r2 "*) echo "consumed-at-r2=$(consumed)" >> "$STUB_LOG"; echo "PASS stub r2" ;;
      *"sha256sum"*) : ;;
    esac ;;
esac
exit 0
"""


def r2_inspect(target="cse-stg-ut-r2", **hc_changes):
    hc = {"NetworkMode": "none", "Privileged": False, "CapAdd": None, "Binds": None, "Tmpfs": None, "VolumesFrom": None,
          "Mounts": None, "RestartPolicy": {"Name": "no"}, "Devices": [], "DeviceRequests": None, "PidMode": "",
          "IpcMode": "private", "UTSMode": "", "UsernsMode": "", "CgroupnsMode": "private", "SecurityOpt": None}
    mounts = hc_changes.pop("_mounts", [])
    name = hc_changes.pop("_name", "/" + target)
    image = hc_changes.pop("_image", IMG)
    hc.update(hc_changes)
    return [{"Name": name, "Image": image, "HostConfig": hc, "Mounts": mounts,
             "Config": {"Labels": {"cse.staging.campaign": "ut", "cse.staging.role": "r2"}}}]


SOURCE = {"system_identifier": "7400000000000000001", "dump": "/srv/cse-backup/pg/dumps/2026/11/cse_20261105T000000Z_ab12",
          "verify_exit": 0, "ledger_run": {"id": 7, "run_kind": "local_dump", "status": "succeeded",
                                           "artifact_key": "pg/dumps/2026/11/cse_20261105T000000Z_ab12",
                                           "artifact_sha256": "1" * 64, "manifest_sha256": "2" * 64}}


def restore_driver(tmp_path, op, ra_body, *, image=IMG, tagged=IMG, label=REV40, r1check_rc=0, exists="",
                   inspect=None, source=None):
    """`staging.sh restore OP RA` with a stub docker (and a stub RAM probe) first on PATH."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "docker").write_text(STUB_RESTORE, encoding="utf-8", newline="\n")
    (stub / "python").write_text(f'#!/bin/sh\nexec "{bash_path(sys.executable)}" "$@"\n', encoding="utf-8", newline="\n")
    (stub / "powershell.exe").write_text("#!/bin/sh\necho 50\n", encoding="utf-8", newline="\n")
    for f in stub.iterdir():
        f.chmod(0o755)
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    ra = vault / "RA-0001.md"
    if ra_body is not None:
        ra.write_bytes(ra_body.encode())
    evid = tmp_path / "evid"
    (tmp_path / "r2.json").write_text(json.dumps(inspect or r2_inspect()), encoding="utf-8")
    (tmp_path / "source.json").write_text(json.dumps(source or SOURCE), encoding="utf-8")
    env = {"PATH": str(stub) + os.pathsep + os.environ.get("PATH", ""), "CSE_STAGING_REV": REV40,
           "CSE_STAGING_CAMPAIGN": "ut", "CSE_STAGING_EVIDENCE": bash_path(evid),
           "CSE_STAGING_SECRETS": bash_path(tmp_path / "secrets"), "STUB_LOG": bash_path(tmp_path / "docker-calls.log"),
           "STUB_IMAGE": image, "STUB_TAGGED": tagged, "STUB_LABEL": label, "STUB_R1CHECK_RC": str(r1check_rc),
           "STUB_EXISTS": exists, "STUB_R2_INSPECT": bash_path(tmp_path / "r2.json"),
           "STUB_SOURCE": bash_path(tmp_path / "source.json"), "STUB_RA_ID": "RA-0001",
           "STUB_CONSUMED": bash_path(evid / "restore" / "ra-consumed.log")}
    p = run_bash(tmp_path, f'bash "{bash_path(os.path.join(STAGING, "staging.sh"))}" restore {op} "{bash_path(ra)}"\n', env)
    calls = (tmp_path / "docker-calls.log").read_text(encoding="utf-8") if (tmp_path / "docker-calls.log").exists() else ""
    consumed = evid / "restore" / "ra-consumed.log"
    return p, calls, (consumed.read_text(encoding="utf-8") if consumed.exists() else ""), vault


def vault_state(vault):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns, sha_lf(p)) for p in vault.iterdir())


ACTING = ("exec", "cp ", "run ", "stop ")       # docker calls that change something (inspect, image and ps only read)


def acting_calls(calls):
    return [c for c in calls.splitlines() if c.startswith(ACTING) and not c.startswith("consumed-at")]


def test_r2_the_driver_refuses_every_invalid_ra_before_anything_runs_and_consumes_a_valid_one_first(tmp_path):
    good_r1 = ra_text(expires="2099-01-01T00:00:00+05:30", decided="2026-11-04")
    good_r2 = ra_text(expires="2099-01-01T00:00:00+05:30", decided="2026-11-04", operation="R2", target="cse-stg-ut-r2")
    # invalid: each refuses with nothing executed, copied, created or consumed, and the vault unchanged
    invalid = {"expired": dict(op="R1", ra_body=ra_text(expires="2020-01-01T00:00:00+05:30")),
               "operation mismatch": dict(op="R2", ra_body=good_r1),
               "image of another build": dict(op="R1", ra_body=good_r1, image="sha256:" + "0" * 64),
               "tag moved to another image": dict(op="R1", ra_body=good_r1, tagged="sha256:" + "0" * 64),
               "image label of another revision": dict(op="R1", ra_body=good_r1, label="e" * 40),
               "malformed": dict(op="R1", ra_body="no front matter\n"),
               "missing file": dict(op="R1", ra_body=None),
               "R1 technical check fails": dict(op="R1", ra_body=good_r1, r1check_rc=1),
               "R2 target exists": dict(op="R2", ra_body=good_r2, exists="cse-stg-ut-r2"),
               "R2 source incomplete": dict(op="R2", ra_body=good_r2, source=dict(SOURCE, verify_exit=1))}
    for i, (label, kw) in enumerate(invalid.items()):
        d = tmp_path / f"inv{i}"
        d.mkdir()
        op = kw.pop("op")
        p, calls, consumed, vault = restore_driver(d, op, **kw)
        assert p.returncode != 0, (label, p.stdout, p.stderr)
        assert consumed == "", label
        acts = acting_calls(calls)
        assert not any("stage_restore.sh r1 " in c + " " or "stage_restore.sh r2 " in c for c in acts), (label, acts)
        assert not any(c.startswith("run ") for c in acts), (label, acts)
        if label not in ("R1 technical check fails", "R2 source incomplete"):
            assert acts == [], (label, acts)        # refused at the RA: not even the read-only checks were copied in
        log = next((d / "evid" / "results").glob(f"main-restore-{op}*.txt"))
        assert "REFUSED" in log.read_text(encoding="utf-8"), label
    # a valid R1: technical checks, then the RA consumed, then one run; the RA's directory is unchanged
    d = tmp_path / "r1"
    d.mkdir()
    p, calls, consumed, vault = restore_driver(d, "R1", good_r1)
    snap = vault_state(vault)
    assert p.returncode == 0, p.stdout + p.stderr
    lines = calls.splitlines()
    i_check = next(i for i, c in enumerate(lines) if "stage_restore.sh r1-check" in c)
    i_run = next(i for i, c in enumerate(lines) if c.endswith("stage_restore.sh r1"))
    assert i_check < i_run and "consumed-at-r1=yes" in lines
    assert len(consumed.splitlines()) == 1 and "\tRA-0001\t" in consumed and "\tR1\tcse-stg-ut-host\tut\t" in consumed
    assert any(c.endswith("rm -f /opt/cse-restore/stage_restore.sh") for c in lines)   # no restore code stays in the host
    assert not any("/opt/cse-staging/" in c and "stage_restore" in c for c in lines)
    # the same RA again: consumed, refused, nothing run
    p, calls2, consumed2, vault = restore_driver(d, "R1", None)
    assert p.returncode != 0 and consumed2 == consumed
    assert calls2[len(calls):].count("stage_restore.sh r1") == 0
    assert vault_state(vault) == snap
    # a valid R2: consumed before the container exists; the container has no network and no mount; the procedure runs
    d = tmp_path / "r2"
    d.mkdir()
    p, calls, consumed, _ = restore_driver(d, "R2", good_r2)
    assert p.returncode == 0, p.stdout + p.stderr
    run = next(c for c in calls.splitlines() if c.startswith("run "))
    assert "--network none --restart no" in run and "--name cse-stg-ut-r2" in run and f" {IMG} infinity" in run
    for forbidden in ("--mount", "-v ", "--volume", "--tmpfs", "--privileged", "--cap-add", "--volumes-from", "--rm"):
        assert forbidden not in run, forbidden
    assert "consumed-at-run=yes" in calls and "consumed-at-r2=yes" in calls
    assert "cp cse-stg-ut-host:/srv/cse-backup/pg/dumps/2026/11/cse_20261105T000000Z_ab12 -" in calls
    assert calls.index("stage_restore.sh r2 ") < calls.index("stage_restore.sh r2-teardown") < calls.index("stop cse-stg-ut-r2")
    # an R2 container that is not isolated: stopped, nothing copied into it, the procedure not run
    for k, bad in {"bridge": dict(NetworkMode="bridge"), "a volume": dict(_mounts=[{"Type": "volume", "Name": "v"}]),
                   "a bind": dict(Binds=["C:\\x:/x"]), "tmpfs": dict(Tmpfs={"/x": ""}),
                   "another image": dict(_image="sha256:" + "9" * 64)}.items():
        d = tmp_path / f"r2-{k.replace(' ', '-')}"
        d.mkdir()
        p, calls, consumed, _ = restore_driver(d, "R2", good_r2, inspect=r2_inspect(**bad))
        assert p.returncode != 0 and "stop cse-stg-ut-r2" in calls, k
        assert "stage_restore.sh r2 " not in calls, k
        assert not any(c.startswith("cp ") and "cse-stg-ut-r2:" in c for c in calls.splitlines()), k


def test_r3_no_code_path_restores_without_validation():
    drv = read("staging.sh")
    rd = read("restore_driver.sh")
    inner = read("restore", "stage_restore.sh")
    # staging.sh reaches the restore code through its `restore` case only
    assert [l for l in drv.splitlines() if "restore_driver.sh" in l and not l.lstrip().startswith("#")] == \
        ['  restore) shift; . "$HERE/restore_driver.sh"; cmd_restore "$@" ;;']
    # stage_restore.sh: named by the restore driver only; never injected (inject copies inner/ and seed only) and never
    # copied under /opt/cse-staging, from which `run` executes
    for f in harness_files():
        if f.endswith(("restore_driver.sh", "stage_restore.sh")):
            continue
        assert "stage_restore" not in open(f, encoding="utf-8").read(), f
    inject = drv[drv.index("cmd_inject() {"):drv.index("sha_of() {")]
    assert '"$HERE"/inner/*.sh' in inject and "restore/" not in inject
    assert re.search(r"^RESTORE_TOOLS=/opt/cse-restore\b", rd, re.M) and "/opt/cse-staging" not in code_of(rd)
    # in cmd_restore, the RA check precedes both operations; in each, the consuming check precedes the restore
    cmd = shell_function(rd, "cmd_restore")
    assert cmd.index('ra_gate "$log" ||') < cmd.index("restore_r1 ") and cmd.index('ra_gate "$log" ||') < cmd.index("restore_r2 ")
    r1 = shell_function(rd, "restore_r1")
    assert r1.index("stage_restore.sh\" r1-check") < r1.index("--consume") < r1.index("stage_restore.sh\" r1 ")
    r2 = shell_function(rd, "restore_r2")
    assert r2.index("--consume") < r2.index("docker run ") < r2.index("stage_restore.sh\" r2 ")
    assert r2.index("restore_gate.py\" r2inspect") < r2.index("docker cp \"$host:$dump\"")
    # the R1 path is created in exactly one place, r1(), which removes it again whatever happens (trap)
    for f in harness_files():
        code = "\n".join(code_lines(open(f, encoding="utf-8").read()))
        if f.endswith("stage_restore.sh"):
            continue
        assert not re.search(r"(install|touch|>)[^\n]*R1-AUTHORIZED", code.replace("ConditionPathExists=", "")), f
    body = shell_function(inner, "r1")
    assert body.index("trap r1_close EXIT") < body.index('install -m 0600 /dev/null "$R1_PATH"') \
        < body.index("systemctl start cse-backup-restore-check.service") < body.index("r1_close\n")
    assert body.index("r1_check") < body.index('install -m 0600 /dev/null "$R1_PATH"')
    assert len([l for l in code_lines(inner) if re.search(r'(install|touch|cp|>)\s[^|&;]*"?\$R1_PATH', l)]) == 1
    close = shell_function(inner, "r1_close")
    assert 'rm -f "$R1_PATH"' in close and "systemctl disable --now cse-backup-restore-check.timer" in close
    # the procedure's restore commands exist only in the tool's Procedure.restore, and only with the copy's
    # connection arguments; no harness file runs them itself
    tool = open(TOOL_FILE, encoding="utf-8").read()
    assert tool.count('t.tool("pg_restore")') == 1 and tool.count('"-f", globals_sql') == 1
    restore_src = tool[tool.index("    def restore(self):"):tool.index("    def run(self, file_steps")]
    assert 't.tool("pg_restore"), "-h", t.host, "-p", str(t.port)' in restore_src
    assert restore_src.count("self.run_step(Step(") == 3
    # the inner script restores only through the tool, after its environment checks and P1's provisioning
    r2b = shell_function(inner, "r2")
    assert r2b.index("isolated_env") < r2b.index("provision.sh\" preflight users dirs postgres") \
        < r2b.index("isolated_restore.py\" run")
    assert "bootstrap" not in r2b and "migrate" not in r2b                     # everything comes from the dump


def test_r4_the_harness_never_writes_into_the_vault():
    gate = read("restore_gate.py")
    # the RA is opened once, read-only; the only file opened for writing is the consumption record, append-only
    assert gate.count("open(a.ra, \"rb\")") == 1
    opens = re.findall(r"open\(([^)]*)\)", gate)
    writes = [o for o in opens if re.search(r'"(a|w|x|r\+|wb|ab)"', o)]
    assert writes == ['a.consumed, "a", encoding="utf-8", newline="\\n"'], writes
    assert "os.remove" not in gate and "unlink" not in gate and "rename" not in gate
    for f in harness_files() + [TOOL_FILE]:
        text = open(f, encoding="utf-8").read()
        assert "Main Vault" not in text and "Obsidian" not in text, f
    rd = "\n".join(code_lines(read("restore_driver.sh")))
    # the RA path reaches only restore_gate.py, as --ra (winpath), and the log's echo line; never a write target
    for line in rd.splitlines():
        if re.search(r'\$(RA_FILE|ra)\b', line):
            assert not re.search(r'>\s*"?\$(RA_FILE|ra)\b|tee[^|]*\$(RA_FILE|ra)\b|cp [^\n]*\$(RA_FILE|ra)\b', line), line
    assert '"$EVID/restore/ra-consumed.log"' in rd


def test_r5_the_r2_container_has_no_network_and_no_shared_volume_or_mount():
    rd = read("restore_driver.sh")
    r2 = shell_function(rd, "restore_r2")
    run = r2[r2.index("docker run "):r2.index("> /dev/null", r2.index("docker run "))]
    assert "--network none --restart no" in run and "--entrypoint /bin/sleep" in run
    for forbidden in ("--mount", " -v ", "--volume", "--tmpfs", "--privileged", "--cap-add", "--volumes-from", "--device",
                      "--pid=", "--pid host", "--ipc", "--userns", "--net=", "--network host", "docker.sock"):
        assert forbidden not in run, forbidden
    # the inspect check refuses every departure
    ok = list(GATE.r2_checks(r2_inspect()[0], "ut", "cse-stg-ut-r2", IMG))
    assert all(p for p, _ in ok), ok
    bad = {"network": dict(NetworkMode="bridge"), "a named volume": dict(_mounts=[{"Type": "volume", "Name": "x"}]),
           "an anonymous volume": dict(_mounts=[{"Type": "volume", "Name": "0f3a"}]), "a bind": dict(Binds=["/:/host"]),
           "a tmpfs": dict(Tmpfs={"/run": ""}), "volumes from": dict(VolumesFrom=["cse-stg-ut-host"]),
           "HostConfig mounts": dict(Mounts=[{"Type": "volume", "Source": "cse-stg-ut-backup"}]),
           "privileged": dict(Privileged=True), "a capability": dict(CapAdd=["CAP_SYS_ADMIN"]),
           "restart": dict(RestartPolicy={"Name": "unless-stopped"}), "a device": dict(Devices=[{"PathOnHost": "/dev/sda"}]),
           "host pid": dict(PidMode="host"), "unconfined": dict(SecurityOpt=["seccomp=unconfined"]),
           "another name": dict(_name="/cse-stg-ut-host"), "another image": dict(_image="sha256:" + "9" * 64)}
    for label, change in bad.items():
        assert not all(p for p, _ in GATE.r2_checks(r2_inspect(**change)[0], "ut", "cse-stg-ut-r2", IMG)), label
    # inside the container, before anything is provisioned: only lo, no route, only Docker's /etc files, no cluster
    env = shell_function(read("restore", "stage_restore.sh"), "isolated_env")
    for item in ('"lo "', "/proc/net/route", '"/etc/hostname /etc/hosts /etc/resolv.conf"', "pg_lsclusters", "docker.sock"):
        assert item in env, item


P1_8B5 = re.compile(r"(§|section|sec\.?)\s*8\s*,?\s*B\b|8\s*B\s*,?\s*step\s*5|8B[-\s]?5|8 B-5", re.I)


def test_r6_nothing_references_p1_section_8b_step_5_except_as_a_prohibition():
    for f in harness_files() + [TOOL_FILE]:
        text = open(f, encoding="utf-8").read()
        assert not P1_8B5.search(text), f
        # nor its commands: a restore without the copy's connection arguments
        assert "sudo -u postgres psql -X -f" not in text and "sudo -u postgres pg_restore" not in text, f
    doc = open(os.path.join(REPO, "docs", "ops", "ISOLATED_RESTORE.md"), encoding="utf-8").read().replace("\r\n", "\n")
    hits = [s for s in re.split(r"(?<=[.;:])\s+|\n\n", doc) if P1_8B5.search(s)]
    assert hits, "the procedure states the prohibition"
    for s in hits:
        assert re.search(r"\b(never|not|must not|forbid|forbidden|prohibit)", s, re.I), s


def test_r7_an_identity_check_precedes_each_verification_command(tmp_path):
    tool = open(TOOL_FILE, encoding="utf-8").read()
    body = tool[tool.index("    def run_step(self, step):"):tool.index("    # ---- steps 5-7")]
    assert body.index("read_identity(self.target, route)") < body.index("identity_problems(") \
        < body.index("subprocess.run(step.argv")
    assert body.index("if problems:") < body.index("subprocess.run(step.argv")
    # the only subprocess that runs a step's command is in run_step; everything else is psql identity/fresh reads
    assert tool.count("subprocess.run(step.argv") == 1
    # the server form: the seven verification commands, in order, each with a route; the only step without one is
    # P1's file check of the dump, which connects to nothing
    target = IR.server_target()
    files, verification = IR.server_steps(target, "/opt/cse/app", "/var/lib/cse-restore/in/d", 7)
    assert [s.label for s in verification] == list(IR.SERVER_LABELS)
    assert all(s.route is not None for s in verification)
    assert [(s.label, s.route) for s in files] == [("p1-dump-verify", None)]
    assert files[0].argv[-3:] == ["backup", "verify", "/var/lib/cse-restore/in/d"]
    joined = {s.label: " ".join(s.argv) for s in verification}
    assert "cse-financial verify" in joined["cse-financial-verify"] and "cse-backfill verify" in joined["cse-backfill-verify"]
    assert "--limit" not in joined["rf4-verify-as-login"] and "runuser -u cse-verifier" in joined["rf4-verify-as-login"]
    assert "--migration-role" in joined["rf4-preflight-migration-role"] and "runuser -u cse-migrator" in joined["rf4-preflight-migration-role"]
    assert "--expect-p1-auth" in joined["p1-manifest-and-invariants"]
    # behaviour: stub identities; a mismatch refuses and the command never runs; the copy's identity lets it run
    recorded = {"system_identifier": "111", "port": 5432, "data_directory": "/var/lib/postgresql/17/main",
                "server_version": "17.11", "unix_socket": True}
    marker = tmp_path / "ran"
    touch = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    proc = IR.Procedure(IR.Target("/nonexistent", "/var/run/postgresql", 5432, "postgres"), str(tmp_path / "d"),
                        {"system_identifier": "222"}, str(tmp_path / "ev"))
    proc.recorded = recorded
    route = lambda: IR.Route("/var/run/postgresql", 5432, "test")
    wrong = {"the source": dict(recorded, system_identifier="222"), "another cluster": dict(recorded, system_identifier="333"),
             "another port": dict(recorded, port=5433), "another data directory": dict(recorded, data_directory="/x"),
             "TCP": dict(recorded, unix_socket=False), "nothing answers": {"error": "no server"}}
    original = IR.read_identity
    try:
        for label, found in wrong.items():
            IR.read_identity = lambda _t, _r, found=found: found
            with pytest.raises(IR.ProcedureError):
                proc.run_step(IR.Step(label, touch, IR.gate_exit0, route=route, env=dict(os.environ)))
            assert not marker.exists(), label
            assert proc.record["steps"][-1]["ran"] is False
        IR.read_identity = lambda _t, _r: dict(recorded)
        proc.run_step(IR.Step("the copy", touch, IR.gate_exit0, route=route, env=dict(os.environ)))
        assert marker.exists() and proc.record["steps"][-1]["identity_check"]["refused"] == []
    finally:
        IR.read_identity = original
    assert IR.identity_problems(dict(recorded, system_identifier="222"), dict(recorded, system_identifier="222"), "222")
    # routes: read the way each command reads its settings; a route the check cannot pin down refuses
    assert IR.route_from_url("postgresql://cse_verifier@/cse?host=/var/run/postgresql&port=5432").as_dict() == \
        {"host": "/var/run/postgresql", "port": 5432, "from": "DATABASE_URL"}
    assert IR.route_from_url("host=/tmp/s port=54332 dbname=cse user=cse_verifier").port == 54332
    for url in ("postgresql://cse_verifier@/cse", "postgresql://cse_verifier@db.example/cse?host=/x",
                "postgresql://u@/cse?host=127.0.0.1", "dbname=cse"):
        with pytest.raises(IR.ProcedureError):
            IR.route_from_url(url)
    sys.path.insert(0, REPO)
    from worker.ops import settings as ops_settings
    assert (IR.P1_DEFAULT_HOST, str(IR.P1_DEFAULT_PORT)) == (ops_settings.DEFAULTS["CSE_DB_HOST"], ops_settings.DEFAULTS["CSE_DB_PORT"])
    envf = tmp_path / "cse.env"
    envf.write_text(open(os.path.join(REPO, "ops", "config", "cse.env.example"), encoding="utf-8").read(), encoding="utf-8")
    assert IR.route_from_env_file(str(envf)).as_dict()["host"] == "/var/run/postgresql"
    envf.write_text("CSE_DB_HOST=$(other)\n", encoding="utf-8")
    with pytest.raises(IR.ProcedureError):
        IR.route_from_env_file(str(envf))


GLOBALS_PG17 = """--
-- PostgreSQL database cluster dump
--

\\restrict abc123

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Roles
--

CREATE ROLE cse_backup;
ALTER ROLE cse_backup WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE cse_migrator;
ALTER ROLE cse_migrator WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE cse_owner;
ALTER ROLE cse_owner WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE cse_reader;
ALTER ROLE cse_reader WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE cse_verifier;
ALTER ROLE cse_verifier WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;

--
-- Role memberships
--

GRANT cse_owner TO cse_migrator WITH INHERIT FALSE, SET TRUE GRANTED BY postgres;
GRANT cse_reader TO cse_verifier WITH INHERIT TRUE, SET FALSE GRANTED BY postgres;
GRANT pg_read_all_data TO cse_backup WITH INHERIT TRUE GRANTED BY postgres;

\\unrestrict abc123

--
-- PostgreSQL database cluster dump complete
--
"""


def test_r8_globals_one_exact_error_tolerated_every_role_and_membership_proven_and_the_gates_fail_closed():
    spec = IR.parse_globals(GLOBALS_PG17)
    assert spec["unknown"] == [] and spec["roles"][-1] == "postgres" and len(spec["grants"]) == 3
    assert spec["grants"][0] == {"role": "cse_owner", "member": "cse_migrator", "options": {"inherit": False, "set": True}}
    assert IR.parse_globals(GLOBALS_PG17 + "DROP ROLE cse_reader;\n")["unknown"]           # an unknown statement
    err = 'psql:/d/globals.sql:31: ERROR:  role "postgres" already exists'
    assert IR.globals_errors(err, "postgres") == []
    assert IR.globals_errors(err + "\n" + err, "postgres") == [err]                       # a second time: not tolerated
    assert IR.globals_errors(err, "cse_restore_check") == [err]                           # another bootstrap name
    other = 'psql:/d/globals.sql:9: ERROR:  role "cse_backup" already exists'
    assert IR.globals_errors(other, "postgres") == [other]                                # not "any already exists"
    assert IR.globals_errors("psql: FATAL:  terminating connection", "postgres")
    attrs = lambda login: {"SUPERUSER": False, "INHERIT": True, "CREATEROLE": False, "CREATEDB": False, "LOGIN": login,
                           "REPLICATION": False, "BYPASSRLS": False}
    roles = {"cse_backup": attrs(True), "cse_migrator": attrs(True), "cse_owner": attrs(False), "cse_reader": attrs(False),
             "cse_verifier": attrs(True), "postgres": {k: True for k in attrs(True)}}
    members = [["cse_owner", "cse_migrator", False, False, True], ["cse_reader", "cse_verifier", False, True, False],
               ["pg_read_all_data", "cse_backup", False, True, True]]
    assert IR.globals_proof(spec, roles, members) == []
    assert IR.globals_proof(spec, {k: v for k, v in roles.items() if k != "cse_verifier"}, members)
    assert IR.globals_proof(spec, dict(roles, cse_owner=attrs(True)), members)             # NOLOGIN not applied
    assert IR.globals_proof(spec, roles, members[1:])                                      # a membership missing
    assert IR.globals_proof(spec, roles, [["cse_owner", "cse_migrator", False, True, True]] + members[1:])  # inherit
    # the gates (runbook section 12 steps 3 and 5): nothing but a clean, complete, non-vacuous result passes
    good = {"clean": True, "scope": {"complete": True, "limit": None}, "problems": [], "extractions": 3, "runs": 3,
            "rebuilt": 3}
    assert IR.gate_verify(0, json.dumps(good), "")[0]
    for bad in (dict(good, clean=False), dict(good, scope={"complete": False, "limit": 1}),
                dict(good, scope={"complete": True, "limit": 5}), dict(good, problems=[{"k": 1}]),
                dict(good, extractions=0, runs=0, rebuilt=0), dict(good, rebuilt=2)):
        assert not IR.gate_verify(0, json.dumps(bad), "")[0], bad
    assert not IR.gate_verify(1, json.dumps(good), "")[0] and not IR.gate_verify(0, "not json", "")[0]
    assert IR.gate_preflight(0, '{"clean": true}', "")[0]
    assert not IR.gate_preflight(2, '{"clean": false, "ledger": {"status": "unverifiable"}}', "")[0]
    assert not IR.gate_preflight(0, '{"clean": false}', "")[0]
    assert IR.gate_f64_verify(0, '{"ok": true, "problems": []}', "")[0] and not IR.gate_f64_verify(0, '{"ok": false}', "")[0]
    assert IR.gate_hb6_verify(0, '{"problems": [], "f64_verify": {"ok": true, "problems": []}}', "")[0]
    assert not IR.gate_hb6_verify(0, '{"problems": ["x"], "f64_verify": {"problems": []}}', "")[0]
    assert IR.gate_p1check(0, '{"problems": []}', "")[0] and not IR.gate_p1check(0, '{"problems": ["d"]}', "")[0]
