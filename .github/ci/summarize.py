"""
Summarise a CI run's test groups from their JUnit reports (docs/CI_TEST_STRATEGY.md). Standard library only.

    python3 .github/ci/summarize.py <reports dir> <json out> [group ...]

<reports dir> holds one directory per group artifact (ci-<workflow>-<group>/), each with junit.xml, exit_code,
environment.txt and pytest.log. Prints a Markdown summary (also written to <json out>.md, and appended to
$GITHUB_STEP_SUMMARY when set), writes the same facts as JSON, and, under GitHub Actions, prints workflow commands that
become annotations anyone can read through the public API: one
notice per call with every group's counts, a warning for each failure that matches the recorded baseline, an error for
everything else.

Counts come from each JUnit report and are checked three ways before they are trusted: against the report's own
totals (testsuite attributes), against pytest's summary line in pytest.log, and against the group's exit code. Any
disagreement is reported as an inconsistent report, never silently resolved.

KNOWN_BASELINE holds the one failure the earlier full local runs (controls I4 and I5) also had. A failure counts as
that baseline only if every part of its recorded signature matches: the test, its group, a failure (not an error), the
failing line, the assertion's text, and the elapsed time it reports, which must equal the time since the test's fixed
fake clock (the established cause). A different failure of the same test is a new failure. The baseline is still a
failure: its group's job fails, and so does this summary.

Verdict and exit status: 0 "clean" (no failure, no missing or inconsistent report); otherwise 1, with the verdict
"baseline-only" (the only failures are the recorded baseline), or "new-failures", "missing-reports",
"inconsistent-reports" (several may apply; the JSON lists each).
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

KNOWN_BASELINE = {
    "tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives": {
        "group": "L02",
        "kind": "failed",
        "location": "tests/test_hb2_postgres.py:588: AssertionError",
        "message": re.compile(r"assert \(([0-9.]+) is not None and \1 < 60\)"),
        "fake_clock": datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc),       # tests/hb2_fakes.py BASE_WALL
        "tolerance_s": 3600,
        "why": "recorded in controls I4 and I5 (docs/R_F4_IMPLEMENTATION.md sections 9.5, 11.6): the test's fake clock "
               "is fixed at 2026-10-03 03:00 UTC (tests/hb2_fakes.py BASE_WALL) and the time since the last ledger "
               "request is measured against the database's real now(), so `since < 60` cannot hold after that minute. "
               "A frozen HB-2 test; not changed by CI",
    },
}
# The last full local run (control I5, docs/R_F4_IMPLEMENTATION.md section 11.6): Claude-reported, Docker on the
# owner's machine without Poppler, not GitHub Actions. A reference for comparison only, never a gate.
REFERENCE_I5 = {
    "L01": "47 passed", "L02": "1 failed, 240 passed", "L03": "55 passed", "L04": "57 passed", "L05": "11 skipped",
    "L06": "48 passed", "L07": "25 passed", "L08": "270 passed, 4 skipped", "L09": "27 passed",
    "L10": "171 passed, 3 skipped", "L11": "12 passed", "L12": "378 passed, 4 skipped", "L13": "10 passed, 17 skipped",
    "L14": "248 passed, 9 skipped", "L15": "339 passed, 2 xfailed", "L16": "587 passed, 37 skipped",
    "W1": "266 passed, 218 skipped", "W2": "829 passed, 67 skipped", "W3": "963 passed, 259 skipped",
}
SUMMARY_LINE = re.compile(r"^=*\s*((?:\d+ [a-z]+(?:, )?)+) in [0-9.]+s")
COUNT_KEYS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
ENV_KEYS = ("os", "python", "pytest", "psycopg2", "postgres", "pdftotext", "workspace", "temp dir (runner default)",
            "temp dir", "temp dir (realpath)")


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
    return "::".join(["/".join(parts[:module + 1]) + ".py"] + parts[module + 1:] + [case.get("name", "")])


def suite_of(root):
    return root if root.tag == "testsuite" else root.find("testsuite")


def suite_start(suite):
    """The test session's start, from the JUnit testsuite timestamp (naive means UTC: the Linux container's clock)."""
    try:
        ts = datetime.fromisoformat(suite.get("timestamp"))
    except (TypeError, ValueError, AttributeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def baseline_match(group, failure, started):
    """(True, why) if `failure` matches its KNOWN_BASELINE signature in every part; otherwise (False, the part that
    did not match), or (False, None) if the test has no baseline entry."""
    spec = KNOWN_BASELINE.get(failure["test"])
    if spec is None:
        return False, None
    if group != spec["group"] or failure["kind"] != spec["kind"]:
        return False, f"recorded in {spec['group']} as {spec['kind']}, here {group} {failure['kind']}"
    if spec["location"] not in failure["text"]:
        return False, f"it does not fail at {spec['location']}"
    m = spec["message"].fullmatch(failure["message"].strip())
    if not m:
        return False, f"its message {failure['message'][:120]!r} is not the recorded assertion"
    if started is None:
        return False, "the report has no start time, so the elapsed time cannot be checked"
    expected = (started - spec["fake_clock"]).total_seconds()
    if abs(float(m.group(1)) - expected) > spec["tolerance_s"]:
        return False, (f"it reports {m.group(1)} s, not the {expected:.0f} s since the fake clock's fixed instant "
                       f"(tolerance {spec['tolerance_s']} s)")
    return True, spec["why"]


def pytest_counts(line):
    """{passed, failed, errors, skipped, xfailed, xpassed: n} from pytest's summary line."""
    out = {}
    for n, word in re.findall(r"(\d+) ([a-z]+)", line or ""):
        key = {"error": "errors", "warning": None, "warnings": None, "deselected": None}.get(word, word)
        if key:
            out[key] = out.get(key, 0) + int(n)
    return out


def read_group(name, directory):
    g = {"report": False, "failures": [], "problems": [], "exit_code": None, "pytest_summary": None,
         "environment": {}, **{k: 0 for k in COUNT_KEYS}}
    try:
        g["exit_code"] = open(os.path.join(directory, "exit_code")).read().strip()
    except OSError:
        pass
    try:
        for line in read_lines(os.path.join(directory, "environment.txt")):
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                g["environment"][k.strip()] = v.strip()
    except OSError:
        pass
    try:
        found = [l.strip() for l in read_lines(os.path.join(directory, "pytest.log")) if SUMMARY_LINE.match(l.strip())]
        g["pytest_summary"] = found[-1].strip("= ") if found else None
    except OSError:
        pass
    junit = os.path.join(directory, "junit.xml")
    if not os.path.exists(junit):
        return g
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as exc:
        g["problems"].append(f"junit.xml cannot be parsed: {exc}")
        return g
    g["report"] = True
    suite = suite_of(root)
    started = suite_start(suite)
    cases = list(root.iter("testcase"))
    for case in cases:
        failure, error, skipped = case.find("failure"), case.find("error"), case.find("skipped")
        for kind, el in (("failed", failure), ("error", error)):
            if el is not None:
                f = {"test": test_id(case), "kind": kind, "message": el.get("message") or "", "text": el.text or ""}
                f["baseline"], f["why"] = baseline_match(name, f, started)
                g["failures"].append(f)
        if failure is not None:
            g["failed"] += 1
        if error is not None:
            g["errors"] += 1
        if failure is None and error is None:
            if skipped is None:
                g["passed"] += 1
            else:
                g["xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"] += 1
    # check 1: the report's own totals
    own = {k: int(suite.get(k, -1)) for k in ("tests", "failures", "errors", "skipped")} if suite is not None else {}
    recount = {"tests": len(cases), "failures": g["failed"], "errors": g["errors"],
               "skipped": g["skipped"] + g["xfailed"]}
    if own != recount:
        g["problems"].append(f"the JUnit totals {own} differ from its test cases {recount}")
    # check 2: pytest's own summary line (a non-strict XPASS is a passed test case in JUnit)
    if g["pytest_summary"] is None:
        g["problems"].append("pytest.log has no pytest summary line (pytest did not finish, or its log is missing)")
    else:
        said = pytest_counts(g["pytest_summary"])
        said["passed"] = said.get("passed", 0) + said.pop("xpassed", 0)
        said = {k: v for k, v in said.items() if v}
        mine = {k: g[k] for k in COUNT_KEYS if g[k]}
        if said != mine:
            g["problems"].append(f"pytest's summary ({g['pytest_summary']}) differs from the JUnit report ({mine})")
    # check 3: the exit code
    if g["exit_code"] not in ("0", "1"):
        g["problems"].append(f"exit code {g['exit_code']}: pytest or the harness did not complete normally")
    elif (g["exit_code"] == "1") != bool(g["failed"] or g["errors"]):
        g["problems"].append(f"exit code {g['exit_code']} disagrees with {g['failed']} failed, {g['errors']} errors")
    return g


def counts(g):
    return ", ".join(f"{g[k]} {k}" for k in COUNT_KEYS if g[k]) or "no tests"


def environment(name, g):
    return f"{name} environment: " + "; ".join(f"{k} {g['environment'][k]}" for k in ENV_KEYS if k in g["environment"])


def escape_data(s):
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(s):
    return escape_data(s).replace(":", "%3A").replace(",", "%2C")


def annotate(level, title, message):
    """A GitHub Actions workflow command (on stdout); the annotation is readable through the public check-run API."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title={escape_property(title)}::{escape_data(message)}", flush=True)


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # a Windows console encoding must not stop it
    reports, json_out = argv[0], argv[1]
    wanted = [w.strip() for w in argv[2:] if w.strip()]              # a Windows shell may pass "L01\r"
    found = {}
    for d in sorted(glob.glob(os.path.join(reports, "*"))):
        if os.path.isdir(d):
            found[os.path.basename(d).rsplit("-", 1)[-1]] = d
    names = wanted or sorted(found)
    groups = {n: read_group(n, found[n]) if n in found else None for n in names}
    sha = next((g["environment"].get("commit (head)") for g in groups.values() if g and g["environment"]), "unknown")

    known, new, missing, inconsistent = [], [], [], []
    for n, g in groups.items():
        if g is None or not g["report"]:
            missing.append(n)
        if g is None:
            continue
        inconsistent += [(n, p) for p in g["problems"]]
        for f in g["failures"]:
            (known if f["baseline"] else new).append((n, f))
    total = {k: sum(g[k] for g in groups.values() if g) for k in COUNT_KEYS}
    flags = [v for v, on in (("new-failures", new), ("missing-reports", missing),
                             ("inconsistent-reports", inconsistent)) if on]
    verdict = ", ".join(flags) or ("baseline-only" if known else "clean")

    md = [f"## Test groups at `{sha}`: **{verdict}**", "",
          "| Group | This run (JUnit) | pytest's summary | Exit | Last local run I5 (Claude-reported; reference only) |",
          "|---|---|---|---|---|"]
    for n, g in groups.items():
        if g is None:
            md.append(f"| {n} | **no artifact** | | | {REFERENCE_I5.get(n, '')} |")
        elif not g["report"]:
            md.append(f"| {n} | **no JUnit report** (provisioning or job failure: see its logs) | "
                      f"{g['pytest_summary'] or ''} | {g['exit_code'] or ''} | {REFERENCE_I5.get(n, '')} |")
        else:
            md.append(f"| {n} | {counts(g)} | {g['pytest_summary'] or '**none**'} | {g['exit_code'] or ''} | "
                      f"{REFERENCE_I5.get(n, '')} |")
    md += ["", "**Total:** " + ", ".join(f"{total[k]} {k}" for k in COUNT_KEYS) + ".", ""]
    if known:
        md += ["**The recorded baseline failure** (its whole signature matched; still a failure):", ""]
        md += [f"- {n}: `{f['test']}`: {f['why']}" for n, f in known] + [""]
    if new:
        md += ["**Failures not in the recorded baseline:**", ""]
        md += [f"- {n}: `{f['test']}` ({f['kind']}): {f['message'][:200]}"
               + (f" (not the baseline: {f['why']})" if f["why"] else "") for n, f in new] + [""]
    if missing:
        md += [f"**Groups without a JUnit report:** {', '.join(missing)}", ""]
    if inconsistent:
        md += ["**Inconsistent reports:**", ""] + [f"- {n}: {p}" for n, p in inconsistent] + [""]
    if verdict == "clean":
        md += ["No failures, no missing or inconsistent reports.", ""]
    md += [f"- {environment(n, g)}" for n, g in groups.items() if g and g["environment"]]
    print("\n".join(md))
    with open(os.path.splitext(json_out)[0] + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):                         # the job's summary page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"commit": sha, "verdict": verdict, "groups": groups, "total": total,
                   "known_baseline_failures": known, "new_failures": new, "missing_reports": missing,
                   "inconsistent_reports": inconsistent}, f, indent=2, default=str)

    lines = [f"{n}: " + ("NO ARTIFACT" if g is None else "NO JUNIT REPORT" if not g["report"] else
                         f"{counts(g)} (exit {g['exit_code']})") for n, g in groups.items()]
    if len(groups) == 1:
        lines += [environment(n, g) for n, g in groups.items() if g and g["environment"]]
    annotate("notice", f"Test groups at {sha[:12]}: {verdict}", "\n".join(lines))
    for n, f in known[:10]:
        annotate("warning", f"{n}: the recorded baseline failure", f"{f['test']}: {f['why']}")
    errors = ([(f"{n}: new {f['kind']}", f"{f['test']}: {f['message'][:400]}"
                + (f" (not the baseline: {f['why']})" if f["why"] else "")) for n, f in new]
              + [(f"{n}: no JUnit report", "the group's job failed before or during its tests") for n in missing]
              + [(f"{n}: inconsistent report", p) for n, p in inconsistent])
    for title, message in errors[:9]:
        annotate("error", title, message)
    if len(errors) > 9:
        annotate("error", f"and {len(errors) - 9} more", "see the job summary and summary.json")
    return 0 if verdict == "clean" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
