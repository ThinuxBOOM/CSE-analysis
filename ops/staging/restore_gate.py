"""
Host-side helper of ops/staging/staging.sh `restore` (ops/staging/restore_driver.sh; docs/ops/STAGING.md section 3;
docs/ops/ISOLATED_RESTORE.md). Fail closed: any check that cannot be made is a refusal.

    python restore_gate.py ra --ra FILE --operation R1|R2 --campaign C --revision SHA --label-revision SHA
                              --image-id ID --tagged-image-id ID --target NAME --consumed LOG --evidence DIR [--consume]
    docker inspect <r2 container> | python restore_gate.py r2inspect --campaign C --target NAME --image-id ID
    python restore_gate.py source --file SOURCE.json

`ra` validates a restore authorization (owner decision OD-12, DR-0002; the vault's CSE Restore Authorization Template)
against values the harness read itself (never from the RA or the caller): the operation, the campaign, the build SHA
(the harness's revision and the image's revision label), the image ID (the host container's image and the tagged host
image), the target, the expiry, and single use against the append-only consumption record. The RA file is opened
read-only, once; nothing is ever written next to it. With --consume, a valid RA is recorded as used (one appended line)
before anything runs; without it, nothing is written anywhere. Prints one PASS or FAIL line per check; exit 0 only when
every check passed.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

KEYS = ("type", "id", "operation", "campaign", "build_sha", "image_id", "target", "expires", "single_use", "decided",
        "owner_words", "tags")


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


def inside(path, root):
    path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def consumed_entries(log):
    """The consumption record's lines as (id, sha256) pairs; a missing record is empty, an unreadable one refuses."""
    if not os.path.exists(log):
        return []
    out = []
    with open(log, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                raise Malformed(f"the consumption record {log} has a malformed line")
            out.append((parts[1], parts[2]))
    return out


def validate(raw, ra_path, a, clock=None):
    """Yields (passed, text) for every check of the RA bytes `raw` against the harness's own values `a`."""
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
    yield ra["campaign"] == a.campaign, f"campaign: the RA's {ra['campaign']!r} is the harness's {a.campaign!r}"
    ok_sha = bool(re.fullmatch(r"[0-9a-f]{40}", ra["build_sha"]))
    yield ok_sha and ra["build_sha"] == a.revision == a.label_revision, \
        (f"build_sha: the RA's {ra['build_sha']!r} is the harness's revision {a.revision!r} and the image's revision "
         f"label {a.label_revision!r}")
    ok_id = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", ra["image_id"]))
    yield ok_id and ra["image_id"] == a.image_id == a.tagged_image_id, \
        (f"image_id: the RA's {ra['image_id']!r} is the host container's image {a.image_id!r} and the tagged host "
         f"image {a.tagged_image_id!r} (docker inspect)")
    yield ra["target"] == a.target, f"target: the RA's {ra['target']!r} is the harness's {a.target!r}"
    try:
        exp = datetime.datetime.fromisoformat(ra["expires"])
        if exp.tzinfo is None or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", ra["expires"]):
            raise ValueError("no explicit offset")
        yield now < exp, f"expires: {ra['expires']} is after now ({now.isoformat(timespec='seconds')})"
    except ValueError as exc:
        yield False, f"expires: {ra['expires']!r} is not YYYY-MM-DDTHH:MM:SS+HH:MM ({exc})"
    try:
        decided = datetime.date.fromisoformat(ra["decided"])
        yield decided <= now.date() + datetime.timedelta(days=1), f"decided: {ra['decided']} is not in the future"
    except ValueError:
        yield False, f"decided: {ra['decided']!r} is not YYYY-MM-DD"
    yield ra["single_use"] == "true", f"single_use is true ({ra['single_use']!r})"
    yield bool(ra["owner_words"].strip()), "owner_words: the owner's yes is recorded"
    try:
        used = consumed_entries(a.consumed)
        hit = [e for e in used if e[0] == ra["id"] or e[1] == sha]
        yield not hit, (f"single use: neither {ra['id']} nor this file's sha256 is in the consumption record "
                        f"{a.consumed} ({len(used)} earlier use(s))")
    except (OSError, Malformed, UnicodeDecodeError) as exc:
        yield False, f"single use: the consumption record cannot be read: {exc}"
    ra_dir = os.path.dirname(os.path.abspath(ra_path))
    yield not inside(a.evidence, ra_dir) and not inside(ra_path, a.evidence), \
        "the evidence directory and the RA's directory are separate (the harness never writes beside the RA)"


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
        ra = frontmatter(raw)
        os.makedirs(os.path.dirname(os.path.abspath(a.consumed)), exist_ok=True)
        line = "\t".join((datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), ra["id"],
                          hashlib.sha256(raw).hexdigest(), ra["operation"], ra["target"], ra["campaign"],
                          ra["build_sha"], ra["image_id"]))
        with open(a.consumed, "a", encoding="utf-8", newline="\n") as f:     # append-only
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"CONSUMED {ra['id']} (sha256 {hashlib.sha256(raw).hexdigest()}) in {a.consumed}")
    return 0


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
              "consumed", "evidence"):
        r.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    r.add_argument("--consume", action="store_true")
    i = sub.add_parser("r2inspect")
    for k in ("campaign", "target", "image_id"):
        i.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    s = sub.add_parser("source")
    s.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    try:
        return {"ra": cmd_ra, "r2inspect": cmd_r2inspect, "source": cmd_source}[a.command](a)
    except (OSError, ValueError) as exc:
        print(f"FAIL {a.command}: {type(exc).__name__}: {exc}")
        print("REFUSED: nothing was consumed or run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
