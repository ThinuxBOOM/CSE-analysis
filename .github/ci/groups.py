"""
The established regression groups (docs/CI_TEST_STRATEGY.md; docs/R_F4_IMPLEMENTATION.md section 5.2), for CI.

Linux runs L01-L16 and Windows W1-W3. Each side runs every tests/test_*.py file exactly once. The partition is the one
the earlier local evidence used (controls I4 and I5): parts A (HB-1 to HB-6) and B (F6.3, F6.4, F8, RDV) by name, and
part C, every other file, in byte order, cut into slices of 11 for L14-L16. Adding a test file therefore lands it in
part C (and shifts L14-L16) without any change here, and --check fails if a file is missing or named twice.

Only files under tests/ are ever named. The repository root's test_cse_endpoints.py contacts CSE (live requests to
www.cse.lk) and is never run by CI; a bare `pytest` in the repository root would collect it, so CI never runs one.

    python .github/ci/groups.py L06       the group's files, one per line ("offline": every file)
    python .github/ci/groups.py --check   verify the partition; exit 1 if it does not cover every file exactly once
    python .github/ci/groups.py --list    every group and its files
"""
import os
import sys

TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests")

PART_A = ["hb1_postgres", "hb1_unit", "hb2_postgres", "hb2_unit", "hb3_postgres", "hb3_replay_postgres", "hb3_unit",
          "hb4_postgres", "hb4_unit", "hb5_postgres", "hb5_rdv_postgres", "hb5_unit", "hb6_postgres", "hb6_unit"]
PART_B = ["f63_admission", "f63_corpus", "f63_determinism", "f63_reconciliation", "f64_corpus_postgres",
          "f64_postgres", "f64_unit", "f6_validation", "f8_leakage", "f8_postgres", "f8_rdv_postgres", "f8_static",
          "f8_unit", "rdv_postgres", "rdv_unit"]
NAMED = {
    "L01": ["hb1_postgres", "hb1_unit"],
    "L02": ["hb2_postgres", "hb2_unit", "hb3_unit", "hb4_unit", "hb5_unit"],
    "L03": ["hb6_unit", "hb6_postgres"],
    "L04": ["hb3_postgres"],
    "L05": ["hb3_replay_postgres", "hb5_rdv_postgres"],
    "L06": ["hb4_postgres"],
    "L07": ["hb5_postgres"],
    "L08": ["f63_admission", "f63_corpus", "f63_determinism", "f63_reconciliation", "f6_validation"],
    "L09": ["f64_postgres"],
    "L10": ["f64_unit", "f64_corpus_postgres"],
    "L11": ["f8_postgres"],
    "L12": ["f8_leakage", "f8_rdv_postgres", "f8_static", "f8_unit"],
    "L13": ["rdv_postgres", "rdv_unit"],
}
LINUX = [f"L{n:02d}" for n in range(1, 17)]
WINDOWS = ["W1", "W2", "W3"]


def path(name):
    return f"tests/test_{name}.py"


def all_files():
    """Every tests/test_*.py, in byte order (the order `sort` gave the earlier driver with no locale set)."""
    return sorted(f"tests/{f}" for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))


def groups():
    files = all_files()
    a, b = [path(n) for n in PART_A], [path(n) for n in PART_B]
    c = [f for f in files if f not in a and f not in b]
    out = {g: [path(n) for n in names] for g, names in NAMED.items()}
    out.update({"L14": c[0:11], "L15": c[11:22], "L16": c[22:], "W1": a, "W2": b, "W3": c, "offline": files})
    return out


def problems():
    files, g, out = all_files(), groups(), []
    for side, names in (("Linux", LINUX), ("Windows", WINDOWS)):
        named = [f for n in names for f in g[n]]
        for f in sorted(set(named) - set(files)):
            out.append(f"{side}: {f} is named but does not exist")
        for f in sorted(set(files) - set(named)):
            out.append(f"{side}: {f} is in no group")
        for f in sorted({f for f in named if named.count(f) > 1}):
            out.append(f"{side}: {f} is in more than one group")
    for f in (f for gr in g.values() for f in gr):
        if not f.startswith("tests/test_"):
            out.append(f"{f} is outside tests/")
    return out


def main(argv):
    if argv == ["--check"]:
        bad = problems()
        print("\n".join(bad) if bad else f"partition ok: {len(all_files())} test files, each once on Linux "
                                         f"({len(LINUX)} groups) and once on Windows ({len(WINDOWS)} groups)")
        return 1 if bad else 0
    if argv == ["--list"]:
        for name, files in groups().items():
            print(f"{name}: {' '.join(files)}")
        return 0
    if len(argv) == 1 and argv[0] in groups():
        bad = problems()
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        print("\n".join(groups()[argv[0]]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
