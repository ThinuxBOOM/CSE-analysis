"""ST-02 inside the staging host container (docs/ops/STAGING.md): /opt/cse/app is exactly the commit's tree. Every path of
`git ls-tree -r` (copied in by the driver) must exist with the same git blob id (sha1 of "blob <size>\\0" + content),
the same kind (file or symlink) and the same owner-executable bit; no other file may exist. Read-only.

    python3 stage_tree.py /var/lib/cse-staging/ls-tree.txt /opt/cse/app
"""
import hashlib
import os
import sys


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main(listing, root):
    expected, problems = {}, []
    with open(listing, encoding="utf-8") as f:
        for line in f:
            meta, path = line.rstrip("\n").split("\t", 1)
            mode, kind, sha = meta.split()
            if kind == "blob":
                expected[path] = (mode, sha)
    for path, (mode, sha) in expected.items():
        full = os.path.join(root, path)
        if mode == "120000":
            if not os.path.islink(full) or blob_id(os.readlink(full).encode()) != sha:
                problems.append(f"symlink differs: {path}")
            continue
        if not os.path.isfile(full) or os.path.islink(full):
            problems.append(f"missing: {path}")
            continue
        with open(full, "rb") as fh:
            if blob_id(fh.read()) != sha:
                problems.append(f"content differs: {path}")
        if bool(os.stat(full).st_mode & 0o100) != (mode == "100755"):
            problems.append(f"mode differs: {path}")
    present = set()
    for d, _dirs, files in os.walk(root):
        for name in files:
            present.add(os.path.relpath(os.path.join(d, name), root).replace(os.sep, "/"))
    extra = sorted(present - set(expected))
    problems += [f"not in the commit: {p}" for p in extra]
    print(f"INFO {len(expected)} tree entries checked, {len(present)} files present")
    for p in problems[:50]:
        print("FAIL", p)
    print("PASS the application is exactly the commit's tree" if not problems else f"FAIL {len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
