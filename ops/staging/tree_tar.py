"""Host-side helper of ops/staging/staging.sh: a tar of the tree under test with git's own file modes. Docker Desktop on
Windows marks every file it copies from a Windows directory executable, so trees go into images and containers as tar
streams (Dockerfile ADD of a local tar; `docker cp -`), never as directories (docs/ops/STAGING.md, ST-02).

    python tree_tar.py <repo> <rev> <out.tar> [--prefix app/] [--overlay]

The base is `git archive <rev>` (core.autocrlf=false: LF, exactly the commit's blobs and modes, owner root). With
--overlay, the working tree's changed and new files replace or join it, CRLF stripped, with the commit's mode for a
changed file and 0755 for a new *.sh (0644 otherwise); each overlaid path and its SHA-256 are printed.
"""
import argparse
import hashlib
import io
import subprocess
import sys
import tarfile


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false", *args], check=True,
                          capture_output=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("rev")
    ap.add_argument("out")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--overlay", action="store_true")
    a = ap.parse_args()
    base = tarfile.open(fileobj=io.BytesIO(git(a.repo, "archive", "--format=tar", a.rev)))
    modes = {}
    for line in git(a.repo, "ls-tree", "-r", "--full-tree", a.rev).decode().splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = 0o755 if meta.split()[0] == "100755" else 0o644
    overlay = {}
    if a.overlay:
        for path in git(a.repo, "ls-files", "-m", "-o", "--exclude-standard", "-z").decode().split("\0"):
            if path:
                with open(f"{a.repo}/{path}", "rb") as f:
                    overlay[path] = f.read().replace(b"\r\n", b"\n")
    with tarfile.open(a.out, "w", format=tarfile.PAX_FORMAT) as out:
        for m in base.getmembers():
            if m.name in overlay:
                continue
            m.name = a.prefix + m.name
            out.addfile(m, base.extractfile(m) if m.isfile() else None)
        for path, data in sorted(overlay.items()):
            ti = tarfile.TarInfo(a.prefix + path)
            ti.size, ti.uid, ti.gid, ti.uname, ti.gname = len(data), 0, 0, "root", "root"
            ti.mode = modes.get(path, 0o755 if path.endswith(".sh") else 0o644)
            out.addfile(ti, io.BytesIO(data))
            print(f"{hashlib.sha256(data).hexdigest()}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
