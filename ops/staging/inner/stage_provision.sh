#!/usr/bin/env bash
# Provisioning inside the staging host container (docs/ops/STAGING.md, ST-05/06/08), as root, with NO network. It runs
# the frozen P1, P3 and HB-6 provisioning scripts unmodified, by explicit step lists:
#   - never `provision.sh all` or `selftest` (both run P1's restore check: DO NOT EXECUTE RESTORE);
#   - never `packages` (done at image build) or `firewall` (a container cannot own ufw; difference D-03);
#   - P1's `verify` step is replaced by the frozen probe `ops/tests/p3_container_probes.sh p1-verify`, which tolerates
#     only time_synchronised (a container is never NTP-synchronised; difference D-04);
#   - CSE_ALLOW_SAME_DEVICE=1, the frozen test-host flag: the backup volume shares the WSL disk (difference D-02).
# Before `provision.sh units`, the R1 gate: a drop-in that makes cse-backup-restore-check.service refuse to start
# (ConditionPathExists on a path no staging code creates); after it, the timer is disabled. Configuration only; no frozen
# file changes. The contact e-mail stays empty and nothing is ever armed.
#
#   stage_provision.sh fresh            P1 (through units) -> p1-verify -> P3 -> HB-6
#   stage_provision.sh to-0017          P1 preflight..bootstrap, then the runner up to 0017 only (ST-08, before)
#   stage_provision.sh finish-upgrade   P1 migrate + units (0018) -> p1-verify -> P3 -> HB-6 (ST-08, after)
#   stage_provision.sh again            the idempotent re-run (ST-06)
#   stage_provision.sh offsite          the staging test destination (P1 section 7, local path; OD-4 recommendation)
set -euo pipefail
APP=/opt/cse/app
export CSE_APP_DIR=$APP CSE_ALLOW_SAME_DEVICE=1
P1="$APP/ops/provision/provision.sh"
step() { printf '\n==> [staging %s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

r1_gate() {
  step "R1 gate: cse-backup-restore-check.service refuses to start without the owner's R1 authorization"
  install -d -m 0755 /etc/systemd/system/cse-backup-restore-check.service.d
  cat > /etc/systemd/system/cse-backup-restore-check.service.d/50-staging-refuse.conf <<'EOF'
# Staging only (docs/ops/STAGING.md): P1's restore check runs only with the owner's explicit R1 authorization.
# No staging code creates the path below. DO NOT EXECUTE RESTORE.
[Unit]
ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED
EOF
  systemctl daemon-reload
}

r1_timer_off() {
  step "R1 gate: the weekly restore-check timer disabled"
  systemctl disable --now cse-backup-restore-check.timer
  [[ "$(systemctl is-enabled cse-backup-restore-check.timer 2>/dev/null || true)" == disabled ]]
  [[ ! -e /etc/cse/staging/R1-AUTHORIZED ]]
}

p3_and_hb6() {
  step "P1 verifier (frozen probe, p1-verify mode)"
  bash "$APP/ops/tests/p3_container_probes.sh" p1-verify
  step "P3 provisioning"
  bash "$APP/ops/provision/provision_scheduler.sh" preflight migrate dirs units verify
  step "HB-6 provisioning"
  bash "$APP/ops/provision/provision_backfill.sh" preflight migrate dirs units verify
}

case "${1:-}" in
  fresh)
    r1_gate
    step "P1 provisioning (explicit steps)"
    bash "$P1" preflight users dirs postgres bootstrap migrate units
    r1_timer_off
    p3_and_hb6
    ;;
  to-0017)
    install -d -m 0755 /var/lib/cse-staging && echo "upgrade host (ST-08); ST-09's tamper scenario may run here" \
      > /var/lib/cse-staging/upgrade-host
    r1_gate
    step "P1 provisioning up to bootstrap"
    bash "$P1" preflight users dirs postgres bootstrap
    step "migration runner: apply up to 0017 only"
    bash "$APP/ops/bin/cse-ops" migrate apply --to 17
    bash "$APP/ops/bin/cse-ops" migrate status
    ;;
  finish-upgrade)
    step "P1 migrate (0018) and units"
    bash "$P1" migrate units
    r1_timer_off
    p3_and_hb6
    ;;
  again)
    step "idempotent re-run of every provisioning step used"
    bash "$P1" preflight users dirs postgres bootstrap migrate units
    r1_timer_off
    p3_and_hb6
    ;;
  offsite)
    step "off-site test destination: a local restic repository on its own volume inside the backup root"
    [[ -s /run/cse-staging/offsite-password ]] || { echo "the off-site password was not injected" >&2; exit 2; }
    install -d -o cse-backup -g cse-backup -m 0700 /srv/cse-backup/offsite-test
    printf '%s\n' /srv/cse-backup/offsite-test/restic \
      | install -o root -g cse-backup -m 0640 /dev/stdin /etc/cse/credentials/offsite-repository
    install -o root -g cse-backup -m 0640 /run/cse-staging/offsite-password /etc/cse/credentials/offsite-password
    shred -u /run/cse-staging/offsite-password
    sed -i -e 's/^CSE_OFFSITE_MODE=disabled/CSE_OFFSITE_MODE=restic/' \
           -e 's/^#CSE_OFFSITE_REPOSITORY_FILE=/CSE_OFFSITE_REPOSITORY_FILE=/' \
           -e 's/^#CSE_OFFSITE_PASSWORD_FILE=/CSE_OFFSITE_PASSWORD_FILE=/' /etc/cse/backup.env
    grep -E '^CSE_OFFSITE_' /etc/cse/backup.env
    bash "$APP/ops/bin/cse-ops" offsite init
    ;;
  *) echo "usage: $0 fresh|to-0017|finish-upgrade|again|offsite" >&2; exit 2 ;;
esac
step "done: $1"
