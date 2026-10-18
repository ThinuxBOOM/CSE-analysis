"""
Summarise a CI run's test groups from their JUnit reports (docs/CI_TEST_STRATEGY.md). Standard library only.

    python3 .github/ci/summarize.py <reports dir> <json out> [group ...]

<reports dir> holds one directory per group artifact (ci-<workflow>-<group>/), each with junit.xml, exit_code,
environment.txt and pytest.log. Prints a Markdown summary (for $GITHUB_STEP_SUMMARY) and writes the same facts as JSON.

Counts come from the JUnit reports: passed, failed, errors, skipped and xfailed are reported separately, and pytest's own
summary line is shown next to them. Each failure is classed against the recorded baseline: KNOWN_BASELINE lists the one
failure the earlier full local runs (controls I4 and I5) also had. It is still a failure, and the group's own job
still fails; this summary only says whether it is that known one or a new one.

Exit status: 1 if any failure or error is not in KNOWN_BASELINE, or if a named group has no report (its job failed
before or during provisioning, or never ran); otherwise 0.
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

KNOWN_BASELINE = {
    "tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives":
        "recorded in controls I4 and I5 (docs/R_F4_IMPLEMENTATION.md sections 9.5, 11.6): the test's fake clock is "
        "fixed at 2026-10-03 03:00 UTC (tests/hb2_fakes.py BASE_WALL) and is compared with the database's real now(), "
        "so `since < 60` cannot hold after that minute. A frozen HB-2 test; not changed by CI",
}
# The last full local run (control I5, docs/R_F4_IMPLEMENTATION.md section 11.6): Claude-reported, Docker on the
# owner's machine, not GitHub Actions. A reference for comparison only, never a gate.
REFERENCE_I5 = {
    "L01": "47 passed", "L02": "1 failed, 240 passed", "L03": "55 passed", "L04": "57 passed", "L05": "11 skipped",
    "L06": "48 passed", "L07": "25 passed", "L08": "270 passed, 4 skipped", "L09": "27 passed",
    "L10": "171 passed, 3 skipped", "L11": "12 passed", "L12": "378 passed, 4 skipped", "L13": "10 passed, 17 skipped",
    "L14": "248 passed, 9 skipped", "L15": "339 passed, 2 xfailed", "L16": "587 passed, 37 skipped",
    "W1": "266 passed, 218 skipped", "W2": "829 passed, 67 skipped", "W3": "963 passed, 259 skipped",
}
SUMMARY = re.compile(r"^=*\s*(?:\d+ (?:passed|failed|skipped|xfailed|xpassed|errors?|deselected|warnings?)"
                     r"(?:, )?)+.* in [0-9.]+s")


def read_lines(path):
    """A text file's lines, whatever wrote it: UTF-8 (with or without a byte-order mark) or UTF-16 (Windows
    PowerShell 5.1's Tee-Object)."""
    data = open(path, "rb").read()
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig", "replace")
    return text.splitlines()


def test_id(case):
    """tests/test_x.py::test_y[param] from a testcase's classname (tests.test_x, or tests.test_x.Class) and name."""
    parts = case.get("classname", "").split(".")
    module = next((i for i, p in enumerate(parts) if p.startswith("test_")), len(parts) - 1)
    path = "/".join(parts[:module + 1]) + ".py"
    return "::".join([path] + parts[module + 1:] + [case.get("name", "")])


def read_group(directory):
    junit = os.path.join(directory, "junit.xml")
    out = {"report": os.path.exists(junit), "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0,
           "failures": [], "exit_code": None, "pytest_summary": None, "environment": {}}
    try:
        out["exit_code"] = open(os.path.join(directory, "exit_code")).read().strip()
    except OSError:
        pass
    try:
        for line in read_lines(os.path.join(directory, "environment.txt")):
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                out["environment"][k.strip()] = v.strip()
    except OSError:
        pass
    try:
        lines = [l.strip() for l in read_lines(os.path.join(directory, "pytest.log"))]
        found = [l for l in lines if SUMMARY.match(l)]
        out["pytest_summary"] = found[-1].strip("= ") if found else None
    except OSError:
        pass
    if not out["report"]:
        return out
    for case in ET.parse(junit).getroot().iter("testcase"):
        tid = test_id(case)
        kinds = [child.tag for child in case]
        skipped = case.find("skipped")
        if "failure" in kinds:
            out["failed"] += 1
            out["failures"].append({"test": tid, "kind": "failed"})
        if "error" in kinds:
            out["errors"] += 1
            out["failures"].append({"test": tid, "kind": "error"})
        if "failure" in kinds or "error" in kinds:
            continue
        if skipped is not None:
            out["xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"] += 1
        else:
            out["passed"] += 1
    return out


def counts(g):
    parts = [f"{g[k]} {k}" for k in ("failed", "errors", "passed", "skipped", "xfailed") if g[k]]
    return ", ".join(parts) or "no tests"


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # a Windows console encoding must not stop it
    reports, json_out, wanted = argv[0], argv[1], argv[2:]
    found = {}
    for d in sorted(glob.glob(os.path.join(reports, "*"))):
        if os.path.isdir(d):
            found[os.path.basename(d).rsplit("-", 1)[-1]] = d
    names = wanted or sorted(found)
    groups = {n: read_group(found[n]) if n in found else None for n in names}
    sha = next((g["environment"].get("commit (head)") for g in groups.values() if g and g["environment"]), "unknown")
    new, missing, known = [], [], []
    for n, g in groups.items():
        if g is None or not g["report"]:
            missing.append(n)
            continue
        for f in g["failures"]:
            (known if f["test"] in KNOWN_BASELINE and f["kind"] == "failed" else new).append((n, f))
    total = {k: sum(g[k] for g in groups.values() if g) for k in ("passed", "failed", "errors", "skipped", "xfailed")}

    md = [f"## Test groups at `{sha}`", "",
          "| Group | This run (JUnit) | pytest's summary | Exit | Last local run I5 (Claude-reported; reference only) |",
          "|---|---|---|---|---|"]
    for n, g in groups.items():
        if g is None:
            md.append(f"| {n} | **no artifact** | | | {REFERENCE_I5.get(n, '')} |")
        elif not g["report"]:
            md.append(f"| {n} | **no JUnit report** (provisioning or job failure: see its logs) | "
                      f"{g['pytest_summary'] or ''} | {g['exit_code'] or ''} | {REFERENCE_I5.get(n, '')} |")
        else:
            md.append(f"| {n} | {counts(g)} | {g['pytest_summary'] or ''} | {g['exit_code'] or ''} | "
                      f"{REFERENCE_I5.get(n, '')} |")
    md += ["", f"**Total:** {total['passed']} passed, {total['failed']} failed, {total['errors']} errors, "
               f"{total['skipped']} skipped, {total['xfailed']} xfailed.", ""]
    if known:
        md += ["**Known baseline failure** (still a failure; its group's job fails):", ""]
        md += [f"- {n}: `{f['test']}`: {KNOWN_BASELINE[f['test']]}" for n, f in known] + [""]
    if new:
        md += ["**Failures not in the recorded baseline:**", ""]
        md += [f"- {n}: `{f['test']}` ({f['kind']})" for n, f in new] + [""]
    if missing:
        md += [f"**Groups without a JUnit report:** {', '.join(missing)}", ""]
    if not known and not new and not missing:
        md += ["No failures and no missing reports.", ""]
    envs = {n: g["environment"] for n, g in groups.items() if g and g["environment"]}
    if envs:
        first = next(iter(envs.values()))
        keys = [k for k in ("os", "python", "pytest", "psycopg2", "postgres", "restic", "pdftotext") if k in first]
        md += ["", "Environment (first group's; each group's is in its artifact): "
               + "; ".join(f"{k} {first[k]}" for k in keys)]
    print("\n".join(md))
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"commit": sha, "groups": groups, "total": total, "known_baseline_failures": known,
                   "new_failures": new, "missing_reports": missing}, f, indent=2, default=str)
    return 1 if new or missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
