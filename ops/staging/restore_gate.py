"""
Host-side helper of ops/staging/staging.sh `restore` (ops/staging/restore_driver.sh; docs/ops/STAGING.md sections 3
and 11; docs/ops/ISOLATED_RESTORE.md). Fail closed: any check that cannot be made is a refusal.

    python restore_gate.py ra --ra FILE --operation R1|R2 --campaign C --revision SHA --label-revision SHA
                              --image-id ID --tagged-image-id ID --target NAME --evidence DIR [--consume]
    python restore_gate.py bind-r1 --campaign C --ra-id RA-NNNN --ra-sha SHA256 --run-id N
    python restore_gate.py r1-crosscheck --campaign C --rows-file ROWS --bindings-file BINDINGS
    docker inspect <r2 container> | python restore_gate.py r2inspect --campaign C --target NAME --image-id ID
    python restore_gate.py source --file SOURCE.json

`ra` validates a restore authorization (owner decision OD-12, DR-0002; the vault's CSE Restore Authorization Template)
against values the harness read itself (never from the RA or the caller): the operation, the campaign, the build SHA
(the harness's revision and the image's revision label), the image ID (the host container's image and the tagged host
image), the target, the expiry (at most 24 hours after `decided`), and single use.

**The records are fixed (AR-0006-1 F-1).** The consumption record is always
`D:\\cse-staging\\<campaign>\\restore\\ra-consumed.log` (RECORD_ROOT below), and single use is checked against the
records of EVERY campaign under RECORD_ROOT. No setting or argument moves them: `restore` also refuses any evidence
directory other than `D:\\cse-staging\\<campaign>`, and any record, evidence or RA path inside the vault (VAULT_ROOT) or
inside the other's directory. The RA file is opened read-only, once; nothing is ever written next to it. With
--consume, a valid RA is recorded as used (one appended line) before anything runs; without it, nothing is written.

`bind-r1` records the `restore_check` run that a consumed R1 RA produced (`r1-runs.log` beside the consumption record;
AR-0006-1 F-2). `r1-crosscheck` is the R1 invariant's host-side half (AR-0006-2 G-1): `staging.sh run` calls it after
`stage_checks.sh invariants` and `stage_scenarios.sh restart-verify`, with the container's restore_check rows and its
r1-authorized-runs, and checks every binding against these host records. Prints one PASS or FAIL line per check; exit 0
only when every check passed.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sys

RECORD_ROOT = "D:/cse-staging"          # fixed: the evidence root and the home of every RA record (never overridable)
VAULT_ROOT = "D:/Main Vault"            # fixed: nothing the harness writes may be inside it
MAX_WINDOW = datetime.timedelta(hours=24)
KEYS = ("type", "id", "operation", "campaign", "build_sha", "image_id", "target", "expires", "single_use", "decided",
        "owner_words", "tags")
STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"


class Malformed(ValueError):
    pass


def frontmatter(raw):
    """The RA's front matter as a dict of strings. Strict: the file opens with ---, every line up to the closing --- is
    `key: value`, every key is known and appears once, and every key is present."""
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise Malformed("the file does not open with front matter (---)")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise Malformed("the front matter is not closed (---)") from None
    out = {}
    for n, line in enumerate(lines[1:end], 2):
        m = re.fullmatch(r"([a-z_]+):(?: (.*))?", line)
        if not m:
            raise Malformed(f"line {n} is not 'key: value'")
        key, value = m.group(1), (m.group(2) or "").strip()
        if key not in KEYS:
            raise Malformed(f"unknown key {key!r}")
        if key in out:
            raise Malformed(f"key {key!r} appears twice")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        out[key] = value
    missing = [k for k in KEYS if k not in out]
    if missing:
        raise Malformed(f"missing keys {missing}")
    return out


def norm(path):
    return os.path.normcase(os.path.realpath(path))


def inside(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def record_dir(campaign, record_root=RECORD_ROOT):
    return os.path.join(record_root, campaign, "restore")


def consumption_record(campaign, record_root=RECORD_ROOT):
    return os.path.join(record_dir(campaign, record_root), "ra-consumed.log")


def r1_runs_record(campaign, record_root=RECORD_ROOT):
    return os.path.join(record_dir(campaign, record_root), "r1-runs.log")


def records(record_root=RECORD_ROOT):
    """Every campaign's consumption record: an RA used in one campaign stays used in all of them."""
    return sorted(glob.glob(os.path.join(record_root, "*", "restore", "ra-consumed.log")))


def consumed_entries(log):
    """A consumption record's lines as (id, sha256, operation, campaign) tuples; an unreadable record refuses."""
    out = []
    with open(log, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6 or not re.fullmatch(r"RA-\d{4}", parts[1]) or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
                raise Malformed(f"the consumption record {log} has a malformed line")
            out.append((parts[1], parts[2], parts[3], parts[5]))
    return out


def stamp(value, what):
    """An explicit, offset-carrying time (YYYY-MM-DDTHH:MM:SS+HH:MM)."""
    if not re.fullmatch(STAMP, value):
        raise ValueError(f"{what} {value!r} is not YYYY-MM-DDTHH:MM:SS+HH:MM")
    return datetime.datetime.fromisoformat(value)


def window_checks(ra, now):
    """The expiry, `decided`, and the 24-hour maximum window. `decided` is a time with an offset, or a date (read as the
    start of that day in the expiry's offset, the strictest reading)."""
    try:
        exp = stamp(ra["expires"], "expires")
    except ValueError as exc:
        yield False, f"expires: {exc}"
        return
    yield now < exp, f"expires: {ra['expires']} is after now ({now.isoformat(timespec='seconds')})"
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", ra["decided"]):
            dec = datetime.datetime.combine(datetime.date.fromisoformat(ra["decided"]), datetime.time(), exp.tzinfo)
        else:
            dec = stamp(ra["decided"], "decided")
    except ValueError as exc:
        yield False, f"decided: {exc} (nor YYYY-MM-DD)"
        return
    yield dec <= now, f"decided: {ra['decided']} is not in the future"
    yield dec < exp <= dec + MAX_WINDOW, \
        f"window: expires {ra['expires']} is after decided {ra['decided']} and at most 24 hours after it"


def validate(raw, ra_path, a, clock=None, record_root=RECORD_ROOT, vault_root=VAULT_ROOT):
    """Yields (passed, text) for every check of the RA bytes `raw` against the harness's own values `a`. The roots are
    module constants; only the tests pass others, to this function directly (the command line takes none)."""
    now = clock or datetime.datetime.now(datetime.timezone.utc)
    sha = hashlib.sha256(raw).hexdigest()
    try:
        ra = frontmatter(raw)
    except (Malformed, UnicodeDecodeError) as exc:
        yield False, f"well-formed: {exc}"
        return
    yield True, f"well-formed: every key present once ({', '.join(KEYS)}); sha256 {sha}"
    yield ra["type"] == "restore-authorization", f"type is restore-authorization ({ra['type']!r})"
    yield bool(re.fullmatch(r"RA-\d{4}", ra["id"])), f"id is RA-<4 digits> ({ra['id']!r})"
    yield ra["operation"] in ("R1", "R2") and ra["operation"] == a.operation, \
        f"operation: the RA's {ra['operation']!r} is the requested {a.operation}"
    yield bool(re.fullmatch(r"[a-z0-9]+", a.campaign)) and ra["campaign"] == a.campaign, \
        f"campaign: the RA's {ra['campaign']!r} is the harness's {a.campaign!r} (lower-case letters and digits)"
    ok_sha = bool(re.fullmatch(r"[0-9a-f]{40}", ra["build_sha"]))
    yield ok_sha and ra["build_sha"] == a.revision == a.label_revision, \
        (f"build_sha: the RA's {ra['build_sha']!r} is the harness's revision {a.revision!r} and the image's revision "
         f"label {a.label_revision!r}")
    ok_id = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", ra["image_id"]))
    yield ok_id and ra["image_id"] == a.image_id == a.tagged_image_id, \
        (f"image_id: the RA's {ra['image_id']!r} is the host container's image {a.image_id!r} and the tagged host "
         f"image {a.tagged_image_id!r} (docker inspect)")
    yield ra["target"] == a.target, f"target: the RA's {ra['target']!r} is the harness's {a.target!r}"
    yield from window_checks(ra, now)
    yield ra["single_use"] == "true", f"single_use is true ({ra['single_use']!r})"
    yield bool(ra["owner_words"].strip()), "owner_words: the owner's yes is recorded"
    try:
        logs = records(record_root)
        used = [e for log in logs for e in consumed_entries(log)]
        hit = [e for e in used if e[0] == ra["id"] or e[1] == sha]
        yield not hit, (f"single use: neither {ra['id']} nor this file's sha256 is in any consumption record under "
                        f"{record_root} ({len(logs)} record(s), {len(used)} earlier use(s))")
    except (OSError, Malformed, UnicodeDecodeError) as exc:
        yield False, f"single use: a consumption record cannot be read: {exc}"
    fixed = os.path.join(record_root, a.campaign)
    yield norm(a.evidence) == norm(fixed), \
        f"evidence: {a.evidence} is the fixed evidence directory {fixed} (restore takes no other)"
    ra_dir = os.path.dirname(os.path.abspath(ra_path))
    rec = consumption_record(a.campaign, record_root)
    yield not any(inside(p, vault_root) or inside(p, ra_dir) for p in (a.evidence, rec)), \
        f"no write inside the vault ({vault_root}) or beside the RA: evidence {a.evidence}, record {rec}"
    yield not inside(ra_path, record_root), f"the RA is not inside the evidence root {record_root}"


def cmd_ra(a):
    with open(a.ra, "rb") as f:                      # read-only, once
        raw = f.read()
    ok = True
    for passed, text in validate(raw, a.ra, a):
        print(("PASS " if passed else "FAIL ") + text)
        ok = ok and passed
    if not ok:
        print("REFUSED: the restore authorization is not valid for this operation; nothing was consumed or run")
        return 1
    if a.consume:
        ra, sha = frontmatter(raw), hashlib.sha256(raw).hexdigest()
        rec = consumption_record(a.campaign)
        os.makedirs(os.path.dirname(rec), exist_ok=True)
        line = "\t".join((datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), ra["id"], sha,
                          ra["operation"], ra["target"], ra["campaign"], ra["build_sha"], ra["image_id"]))
        with open(rec, "a", encoding="utf-8", newline="\n") as f:     # append-only
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"CONSUMED {ra['id']} (sha256 {sha}) in {rec}")
    return 0


def bind_checks(a, record_root=RECORD_ROOT):
    """The R1 run to bind: its RA is a consumed R1 RA of this campaign, and neither is bound already."""
    yield bool(re.fullmatch(r"[a-z0-9]+", a.campaign)), f"campaign {a.campaign!r}"
    yield bool(re.fullmatch(r"[1-9]\d*", a.run_id)), f"run id {a.run_id!r} is a ledger id"
    entries = consumed_entries(consumption_record(a.campaign, record_root)) \
        if os.path.exists(consumption_record(a.campaign, record_root)) else []
    yield (a.ra_id, a.ra_sha, "R1", a.campaign) in entries, f"{a.ra_id} ({a.ra_sha}) is a consumed R1 RA of {a.campaign}"
    runs = r1_runs_record(a.campaign, record_root)
    bound = [l.split("\t") for l in open(runs, encoding="utf-8").read().splitlines()] if os.path.exists(runs) else []
    yield not any(b[1] == a.run_id or b[2] == a.ra_id for b in bound if len(b) > 2), \
        f"neither run {a.run_id} nor {a.ra_id} is bound already ({len(bound)} binding(s))"


def cmd_bind_r1(a):
    ok = True
    for passed, text in bind_checks(a):
        print(("PASS " if passed else "FAIL ") + "bind-r1: " + text)
        ok = ok and passed
    if not ok:
        return 1
    with open(r1_runs_record(a.campaign), "a", encoding="utf-8", newline="\n") as f:     # append-only
        f.write("\t".join((datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), a.run_id,
                           a.ra_id, a.ra_sha, a.campaign)) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(f"BOUND restore_check run {a.run_id} to {a.ra_id} in {r1_runs_record(a.campaign)}")
    return 0


BINDING = re.compile(r"([1-9][0-9]*) (RA-[0-9]{4}) ([0-9a-f]{64})")


def host_runs(campaign, record_root=RECORD_ROOT):
    """r1-runs.log as (run id, RA id, RA sha256) triples; a malformed line refuses (fail closed)."""
    path = r1_runs_record(campaign, record_root)
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 5 or not BINDING.fullmatch(" ".join(parts[1:4])) or parts[4] != campaign:
                    raise Malformed(f"{path} has a malformed line")
                out.append(tuple(parts[1:4]))
    return out


def crosscheck(campaign, rows_text, bindings_text, record_root=RECORD_ROOT):
    """The R1 invariant's host-side half (AR-0006-2 G-1). Every binding line in the container's r1-authorized-runs must
    name a consumed R1 RA of this campaign (ra-consumed.log: id and sha256) and the run that bind-r1 recorded for it
    (r1-runs.log: run id, id and sha256). Every host binding must be in the container's file, and the container's
    restore_check rows must be exactly the bound runs. The host records are written only by the harness, outside the
    container, so a line forged inside the container cannot pass."""
    rows = rows_text.split()
    lines = [l for l in bindings_text.replace("\r\n", "\n").split("\n") if l.strip()]
    bindings = [BINDING.fullmatch(l) for l in lines]
    yield all(re.fullmatch(r"[1-9][0-9]*", r) for r in rows) and len(set(rows)) == len(rows), \
        f"the restore_check rows read from the container are run ids ({' '.join(rows) or 'none'})"
    yield all(bindings), f"every container binding is '<run id> <RA id> <sha256>' ({len(lines)} line(s))"
    triples = [m.groups() for m in bindings if m]
    yield len({t[0] for t in triples}) == len(triples) and len({t[1] for t in triples}) == len(triples), \
        "no run and no RA is bound twice in the container"
    try:
        rec = consumption_record(campaign, record_root)
        consumed = {(e[0], e[1]) for e in consumed_entries(rec) if e[2] == "R1" and e[3] == campaign} \
            if os.path.exists(rec) else set()
        hosted = host_runs(campaign, record_root)
    except (OSError, Malformed, UnicodeDecodeError) as exc:
        yield False, f"the host records cannot be read: {exc}"
        return
    for run, ra, sha in triples:
        yield (ra, sha) in consumed, f"binding of run {run}: {ra} ({sha[:12]}...) is a consumed R1 RA of {campaign} (host)"
        yield (run, ra, sha) in hosted, f"binding of run {run}: the host recorded this run for {ra} (r1-runs.log)"
    for run, ra, sha in hosted:
        yield (run, ra, sha) in triples, f"host binding of run {run} to {ra} is present in the container"
    ok_rows = all(re.fullmatch(r"[1-9][0-9]*", r) for r in rows)
    yield ok_rows and sorted(rows, key=int) == sorted((t[0] for t in triples), key=int), \
        (f"the container's restore_check rows ({' '.join(rows) or 'none'}) are exactly the bound runs "
         f"({' '.join(t[0] for t in triples) or 'none'})")


def cmd_r1_crosscheck(a):
    with open(a.rows_file, encoding="utf-8") as f:
        rows = f.read()
    with open(a.bindings_file, encoding="utf-8") as f:
        bindings = f.read()
    ok = True
    for passed, text in crosscheck(a.campaign, rows, bindings):
        print(("PASS " if passed else "FAIL ") + "r1-crosscheck: " + text)
        ok = ok and passed
    return 0 if ok else 1


def r2_checks(c, campaign, target, image_id):
    hc = c.get("HostConfig") or {}
    labels = (c.get("Config") or {}).get("Labels") or {}
    yield c.get("Name") == "/" + target, f"the container is the RA's target {target} (Name={c.get('Name')})"
    yield c.get("Image") == image_id, f"image: the RA's {image_id} (Image={c.get('Image')})"
    yield hc.get("NetworkMode") == "none", f"network: none (NetworkMode={hc.get('NetworkMode')})"
    yield not c.get("Mounts"), f"no volume and no mount of any kind (Mounts={c.get('Mounts')})"
    yield not hc.get("Binds") and not hc.get("Tmpfs") and not hc.get("VolumesFrom"), \
        f"no bind, tmpfs or shared volume (Binds={hc.get('Binds')}, Tmpfs={hc.get('Tmpfs')}, VolumesFrom={hc.get('VolumesFrom')})"
    yield not hc.get("Mounts"), f"no mount requested (HostConfig.Mounts={hc.get('Mounts')})"
    yield hc.get("Privileged") is False, f"not privileged (Privileged={hc.get('Privileged')})"
    yield not hc.get("CapAdd"), f"no added capability (CapAdd={hc.get('CapAdd')})"
    yield (hc.get("RestartPolicy") or {}).get("Name") == "no", f"restart policy: no ({hc.get('RestartPolicy')})"
    yield not hc.get("Devices") and not hc.get("DeviceRequests"), "no host device"
    for k in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode"):
        yield hc.get(k) != "host", f"{k} is not the host's ({k}={hc.get(k)!r})"
    opts = hc.get("SecurityOpt") or []
    yield not any("unconfined" in o or "privileged" in o for o in opts), f"no unconfined security option ({opts})"
    yield labels.get("cse.staging.campaign") == campaign and labels.get("cse.staging.role") == "r2", \
        f"labelled for campaign {campaign}, role r2 ({labels.get('cse.staging.campaign')}, {labels.get('cse.staging.role')})"


def cmd_r2inspect(a):
    found = json.load(sys.stdin)
    if not isinstance(found, list) or len(found) != 1:
        print(f"FAIL expected the inspect record of exactly one container, got {len(found) if isinstance(found, list) else found!r}")
        return 1
    ok = True
    for passed, text in r2_checks(found[0], a.campaign, a.target, a.image_id):
        print(("PASS " if passed else "FAIL ") + text)
        ok = ok and passed
    return 0 if ok else 1


def source_checks(s):
    run = s.get("ledger_run") if isinstance(s.get("ledger_run"), dict) else {}
    key = str(run.get("artifact_key") or "")
    yield bool(re.fullmatch(r"\d+", str(s.get("system_identifier") or ""))), \
        f"the source's system identifier is read ({s.get('system_identifier')})"
    yield run.get("run_kind") == "local_dump" and run.get("status") == "succeeded" and isinstance(run.get("id"), int), \
        f"the backup ledger run is a succeeded local_dump (id {run.get('id')}, {run.get('run_kind')}, {run.get('status')})"
    yield bool(re.fullmatch(r"pg/dumps/\d{4}/\d{2}/[A-Za-z0-9_.-]+", key)) and s.get("dump") == "/srv/cse-backup/" + key, \
        f"the dump is the ledger run's artifact under the backup root ({s.get('dump')}, {key})"
    yield all(re.fullmatch(r"[0-9a-f]{64}", str(run.get(k) or "")) for k in ("artifact_sha256", "manifest_sha256")), \
        "the ledger run records the dump's and the manifest's SHA-256"
    yield s.get("verify_exit") == 0, f"P1's verify of the dump in the host: exit {s.get('verify_exit')}"


def cmd_source(a):
    with open(a.file, encoding="utf-8") as f:
        try:
            s = json.load(f)
        except ValueError as exc:
            print(f"FAIL the source record is not JSON: {exc}")
            return 1
    ok = True
    for passed, text in source_checks(s if isinstance(s, dict) else {}):
        print(("PASS " if passed else "FAIL ") + "source: " + text)
        ok = ok and passed
    if ok:
        print(f"DUMP {s['dump']}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("ra")
    for k in ("ra", "operation", "campaign", "revision", "label_revision", "image_id", "tagged_image_id", "target",
              "evidence"):
        r.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    r.add_argument("--consume", action="store_true")
    b = sub.add_parser("bind-r1")
    for k in ("campaign", "ra_id", "ra_sha", "run_id"):
        b.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    x = sub.add_parser("r1-crosscheck")
    for k in ("campaign", "rows_file", "bindings_file"):
        x.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    i = sub.add_parser("r2inspect")
    for k in ("campaign", "target", "image_id"):
        i.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    s = sub.add_parser("source")
    s.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    try:
        return {"ra": cmd_ra, "bind-r1": cmd_bind_r1, "r1-crosscheck": cmd_r1_crosscheck, "r2inspect": cmd_r2inspect,
                "source": cmd_source}[a.command](a)
    except (OSError, ValueError) as exc:
        print(f"FAIL {a.command}: {type(exc).__name__}: {exc}")
        print("REFUSED: nothing was consumed or run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
