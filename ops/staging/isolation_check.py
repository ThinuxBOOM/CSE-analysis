"""Host-side helper of ops/staging/staging.sh (`isolation`, ST-04): a staging host's `docker inspect` against the approved
configuration (staging.sh rung_flags and cmd_up), which the container cannot see from inside: no network, restart
policy `no`, not privileged, a private cgroup namespace, exactly the rung's added capabilities, the campaign's three
named volumes and no other mount, no Docker socket, no host device, no host namespace and no unconfined security
option (docs/ops/STAGING.md).

    docker inspect cse-stg-t1s1-host | python isolation_check.py --campaign t1s1 [--suffix up] [--cap-add SYS_ADMIN ...]

Reads the inspect JSON on standard input; prints one PASS or FAIL line per check; exit 0 when every check passed.
"""
import argparse
import json
import sys

VOLUMES = {"pgdata": "/var/lib/postgresql", "backup": "/srv/cse-backup", "offsite": "/srv/cse-backup/offsite-test"}
TMPFS = {"/run", "/run/lock", "/srv/cse-staging-smalldisk"}


def cap(name):
    name = name.strip().upper()
    return name if name.startswith("CAP_") else "CAP_" + name


def checks(c, campaign, suffix, cap_add):
    hc = c.get("HostConfig") or {}
    mounts = c.get("Mounts") or []
    prefix = f"cse-stg-{campaign}{'-' + suffix if suffix else ''}-"
    restart = (hc.get("RestartPolicy") or {}).get("Name")
    want_caps = sorted({cap(x) for x in cap_add})
    got_caps = sorted(cap(x) for x in hc.get("CapAdd") or [])
    want_mounts = sorted((prefix + v, d) for v, d in VOLUMES.items())
    got_mounts = sorted((m.get("Name") or m.get("Source"), m.get("Destination")) for m in mounts)
    yield hc.get("Privileged") is False, f"not privileged (Privileged={hc.get('Privileged')})"
    yield hc.get("NetworkMode") == "none", f"network: none (NetworkMode={hc.get('NetworkMode')})"
    yield restart == "no", f"restart policy: no (RestartPolicy={restart})"
    yield hc.get("CgroupnsMode") == "private", f"cgroup namespace: private (CgroupnsMode={hc.get('CgroupnsMode')})"
    yield got_caps == want_caps, f"added capabilities: exactly {want_caps} (CapAdd={got_caps})"
    yield not hc.get("Binds"), f"no bind mount (Binds={hc.get('Binds')})"
    yield all(m.get("Type") == "volume" for m in mounts), \
        f"every mount is a named volume (types: {sorted({m.get('Type') for m in mounts})})"
    yield got_mounts == want_mounts, f"mounts: exactly the campaign's volumes {want_mounts} (got {got_mounts})"
    yield not any("docker.sock" in (m.get(k) or "") for m in mounts for k in ("Source", "Destination")), \
        "no Docker socket mounted"
    yield set(hc.get("Tmpfs") or {}) <= TMPFS, f"tmpfs only at {sorted(TMPFS)} (got {sorted(hc.get('Tmpfs') or {})})"
    yield not hc.get("Devices") and not hc.get("DeviceRequests"), "no host device"
    for k in ("PidMode", "IpcMode", "UTSMode", "UsernsMode"):
        yield hc.get(k) != "host", f"{k} is not the host's ({k}={hc.get(k)!r})"
    opts = hc.get("SecurityOpt") or []
    yield not any("unconfined" in o or "privileged" in o for o in opts), f"no unconfined security option ({opts})"
    labels = (c.get("Config") or {}).get("Labels") or {}
    yield labels.get("cse.staging.campaign") == campaign, \
        f"labelled for campaign {campaign} (label={labels.get('cse.staging.campaign')})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--suffix", default="")
    ap.add_argument("--cap-add", nargs="+", default=["SYS_ADMIN"])
    a = ap.parse_args()
    found = json.load(sys.stdin)
    if not isinstance(found, list) or len(found) != 1:
        print(f"FAIL expected the inspect record of exactly one container, got {len(found)}")
        return 1
    ok = True
    for passed, text in checks(found[0], a.campaign, a.suffix, a.cap_add):
        print(("PASS " if passed else "FAIL ") + text)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
