#!/usr/bin/env bash
# Clean-server test WITH systemd (P3): a fresh ubuntu:24.04 with systemd as PID 1 - what the real server runs - then
# P1 provisioning (ops/provision/provision.sh: every step except the firewall, which a container cannot own) and P3
# provisioning (ops/provision/provision_scheduler.sh). The container is DISCONNECTED from every network BEFORE the
# scheduler units are installed and enabled, so nothing could reach CSE even by mistake; the independent probes in
# ops/tests/p3_container_probes.sh then run inside it.
#
# Needs Docker with privileged containers (systemd) and network access for apt during P1 provisioning only.
# Never contacts CSE; never arms the scheduler.
#
#   ops/tests/provision_p3_in_docker.sh [path-to-repo]
set -euo pipefail
REPO="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
SRC="$REPO"
if command -v cygpath >/dev/null 2>&1; then SRC="$(cygpath -w "$REPO")"; fi   # Docker Desktop on Windows
IMAGE=cse-systemd-ubuntu24:test
NAME="cse-p3-provision-test-$$"

docker build -q -t "$IMAGE" - >/dev/null <<'EOF'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive container=docker
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && systemctl mask getty.target console-getty.service systemd-logind.service systemd-udevd.service \
       systemd-udev-trigger.service systemd-firstboot.service
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
EOF

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
MSYS_NO_PATHCONV=1 docker run -d --name "$NAME" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock -v "${SRC}:/src:ro" "$IMAGE" >/dev/null
st=unknown
for _ in $(seq 1 60); do
  st="$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)"
  [[ "$st" == running || "$st" == degraded ]] && break
  sleep 1
done
echo "==> systemd in the container: $st ($(docker exec "$NAME" systemd --version | head -1))"

echo "==> P1 provisioning (with systemd; firewall skipped) + the P3 steps that install no unit"
# P1's `verify` step is run through p3_container_probes.sh p1-verify: identical checks, but the one a container cannot
# satisfy (NTP time synchronisation) is reported instead of aborting; every other check must pass.
MSYS_NO_PATHCONV=1 docker exec "$NAME" bash -euo pipefail -c '
mkdir -p /opt/cse /srv/cse-backup
cp -r /src /opt/cse/app && rm -rf /opt/cse/app/.git && chown -R root:root /opt/cse/app && chmod -R go-w /opt/cse/app
export CSE_ALLOW_SAME_DEVICE=1 CSE_ALLOW_DIRTY=1 CSE_APP_DIR=/opt/cse/app
bash /opt/cse/app/ops/provision/provision.sh preflight packages users dirs postgres bootstrap migrate units
bash /opt/cse/app/ops/tests/p3_container_probes.sh p1-verify
bash /opt/cse/app/ops/provision/provision.sh selftest
bash /opt/cse/app/ops/provision/provision_scheduler.sh preflight migrate dirs
'

echo "==> disconnecting the container from every network BEFORE any scheduler unit exists"
for net in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$NAME"); do
  docker network disconnect "$net" "$NAME"
done

echo "==> P3 units + verification (offline)"
MSYS_NO_PATHCONV=1 docker exec "$NAME" bash -euo pipefail -c \
  'CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision_scheduler.sh units verify'

echo "==> independent probes (offline)"
MSYS_NO_PATHCONV=1 docker exec "$NAME" bash /opt/cse/app/ops/tests/p3_container_probes.sh
