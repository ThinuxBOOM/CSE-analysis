#!/usr/bin/env bash
# Clean-server provisioning test: runs ops/provision/provision.sh on a FRESH ubuntu:24.04 container (no systemd, so
# units/firewall are skipped and the backup root shares the container's disk), then probes the result independently.
# Needs Docker and network access for apt (Ubuntu + apt.postgresql.org). Never contacts CSE.
#
#   ops/tests/provision_in_docker.sh [path-to-repo]
set -euo pipefail
REPO="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
SRC="$REPO"
if command -v cygpath >/dev/null 2>&1; then SRC="$(cygpath -w "$REPO")"; fi   # Docker Desktop on Windows

MSYS_NO_PATHCONV=1 docker run --rm -v "${SRC}:/src:ro" ubuntu:24.04 bash -euo pipefail -c '
mkdir -p /opt/cse /srv/cse-backup
cp -r /src /opt/cse/app && rm -rf /opt/cse/app/.git && chown -R root:root /opt/cse/app && chmod -R go-w /opt/cse/app
export CSE_NO_SYSTEMD=1 CSE_ALLOW_SAME_DEVICE=1 CSE_ALLOW_DIRTY=1 CSE_APP_DIR=/opt/cse/app
bash /opt/cse/app/ops/provision/provision.sh all

echo; echo "==> independent probes"
fail=0
probe() { if eval "$2" >/dev/null 2>&1; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
nprobe() { if eval "$2" >/dev/null 2>&1; then echo "FAIL $1"; fail=1; else echo "PASS $1"; fi; }
probe  "worker connects by peer map"          "runuser -u cse-worker -- psql -XAt -U cse_worker -d cse -c \"select 1\""
nprobe "worker cannot connect as another role" "runuser -u cse-worker -- psql -XAt -U cse_backup -d cse -c \"select 1\""
nprobe "unmapped OS user is rejected"          "useradd -M probeuser; runuser -u probeuser -- psql -XAt -U cse_worker -d cse -c \"select 1\""
nprobe "no TCP listener on 5432"               "timeout 3 bash -c \"echo > /dev/tcp/127.0.0.1/5432\""
nprobe "worker cannot UPDATE raw observations" "runuser -u cse-worker -- psql -XAt -v ON_ERROR_STOP=1 -U cse_worker -d cse -c \"update raw_market_observations set source = source\""
nprobe "worker cannot DELETE F3 evidence"      "runuser -u cse-worker -- psql -XAt -v ON_ERROR_STOP=1 -U cse_worker -d cse -c \"delete from report_classification_evidence\""
nprobe "owner cannot log in"                   "runuser -u postgres -- psql -XAt -U cse_owner -d cse -c \"select 1\""
probe  "data checksums on"                     "[ \"\$(runuser -u postgres -- psql -XAtc \"show data_checksums\")\" = on ]"
probe  "a dump exists and verifies (cse-ops)"   "bash /opt/cse/app/ops/bin/cse-ops backup verify --all"
probe  "restore check recorded as succeeded"   "[ \"\$(runuser -u postgres -- psql -XAt -d cse -c \"select status from ops.backup_runs where run_kind = '"'"'restore_check'"'"' order by id desc limit 1\")\" = succeeded ]"
probe  "off-site recorded as not_configured"   "[ \"\$(runuser -u postgres -- psql -XAt -d cse -c \"select status from ops.backup_runs where run_kind = '"'"'offsite_sync'"'"' order by id desc limit 1\")\" = not_configured ]"
nprobe "backup status raises an alert (off-site not configured)" "bash /opt/cse/app/ops/bin/cse-ops backup status"
probe  "migration ledger verifies (cse-ops)"   "bash /opt/cse/app/ops/bin/cse-ops migrate verify"
probe  "dry-run apply works as the migrator"   "bash /opt/cse/app/ops/bin/cse-ops migrate apply --dry-run"
probe  "spool dir cse-worker:cse-backup 2750"  "[ \"\$(stat -c \"%U:%G %a\" /srv/cse-backup/spool)\" = \"cse-worker:cse-backup 2750\" ]"
probe  "credentials dir not world-readable"    "[ \"\$(stat -c %a /etc/cse/credentials)\" = 750 ]"
probe  "provisioning is idempotent"            "bash /opt/cse/app/ops/provision/provision.sh users dirs postgres bootstrap migrate verify"
exit $fail
'
