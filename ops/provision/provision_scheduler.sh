#!/usr/bin/env bash
# =============================================================================
# CSE P3 scheduler provisioning - run AFTER ops/provision/provision.sh (P1), on the same Ubuntu 24.04 server.
#
#   sudo CSE_APP_DIR=/opt/cse/app bash ops/provision/provision_scheduler.sh all
#   sudo bash ops/provision/provision_scheduler.sh <step>      # preflight migrate dirs units verify
#
# Idempotent. It applies pending migrations (0014) through P1's runner, prepares the scheduler's state directory,
# installs and ENABLES the scheduler timer and the post-capture backup trigger, and verifies the result.
# It NEVER arms the scheduler and never contacts CSE: until the owner records `arm` (docs/ops/P3_SCHEDULER.md, the
# release gate), every wake-up does P2 spool/abandoned-run hygiene only and contacts nobody.
#
# Environment (defaults):
#   CSE_APP_DIR=/opt/cse/app  CSE_DB_NAME=cse  CSE_BACKUP_ROOT=/srv/cse-backup
#   CSE_NO_SYSTEMD=0          1 = container test host without systemd: no units, no timers
# =============================================================================
set -euo pipefail

APP="${CSE_APP_DIR:-/opt/cse/app}"
DB="${CSE_DB_NAME:-cse}"
ROOT="${CSE_BACKUP_ROOT:-/srv/cse-backup}"
NO_SYSTEMD="${CSE_NO_SYSTEMD:-0}"
STATE_DIR=/var/lib/cse-scheduler
UNITS=(cse-capture-scheduler.service cse-capture-scheduler.timer cse-capture-backup-trigger.path)

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
as_worker() { runuser -u cse-worker -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_worker \
                CSE_BACKUP_ROOT="${ROOT}" python3 -m worker.scheduler "$@"; }

step_preflight() {
  log "preflight (P1 must be provisioned first)"
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  [[ -f "${APP}/worker/scheduler/__main__.py" ]] || die "CSE_APP_DIR=${APP} has no P3 scheduler"
  for u in cse-worker cse-backup cse-migrator; do id -u "$u" >/dev/null 2>&1 || die "user $u missing: run provision.sh first"; done
  [[ -f /etc/cse/cse.env ]] || die "/etc/cse/cse.env missing: run provision.sh first"
  [[ -d "${ROOT}/spool" ]] || die "${ROOT}/spool missing: run provision.sh first"
  if [[ "${NO_SYSTEMD}" != "1" ]]; then
    [[ -d /run/systemd/system ]] || die "systemd is not running (set CSE_NO_SYSTEMD=1 only on a container test host)"
    systemctl cat cse-backup-dump.service >/dev/null 2>&1 || die "P1 backup units missing: run provision.sh units first"
  fi
  echo "preflight ok"
}

step_migrate() {
  log "migrations (P1 runner as cse_migrator -> SET ROLE cse_owner; applies 0014 if pending)"
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate apply
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate verify >/dev/null && echo "migration ledger verified"
}

step_dirs() {
  log "scheduler state directory and capture configuration template"
  install -d -o cse-worker -g cse-worker -m 0750 "${STATE_DIR}"
  # the contact e-mail stays EMPTY: nothing can contact CSE until the owner sets it (G-1 control 4) and arms
  [[ -f /etc/cse/capture.env ]] || install -o root -g cse-worker -m 0640 "${APP}/ops/config/capture.env.example" /etc/cse/capture.env
  stat -c '%U:%G %a %n' "${STATE_DIR}" /etc/cse/capture.env
}

step_units() {
  if [[ "${NO_SYSTEMD}" == "1" ]]; then log "units: skipped (CSE_NO_SYSTEMD=1)"; return; fi
  log "scheduler units: service + timer (wake-ups only) + post-capture backup trigger"
  local u
  for u in "${UNITS[@]}"; do
    sed -e "s#/opt/cse/app#${APP}#g" -e "s#/srv/cse-backup#${ROOT}#g" "${APP}/ops/scheduler/${u}" > "/etc/systemd/system/${u}"
    chmod 0644 "/etc/systemd/system/${u}"
  done
  systemctl daemon-reload
  systemd-analyze verify "${UNITS[@]/#//etc/systemd/system/}"
  # enabling the timer contacts nobody: the scheduler is disarmed until the owner records `arm`
  systemctl enable --now cse-capture-scheduler.timer cse-capture-backup-trigger.path
  systemctl list-timers cse-capture-scheduler.timer --no-pager
}

step_verify() {
  log "scheduler verification (read-only)"
  local rc=0
  (cd / && as_worker verify) || rc=1
  (cd / && as_worker status --days 7 >/dev/null) || { st=$?; [[ $st -eq 2 ]] || rc=1; }
  if [[ "${NO_SYSTEMD}" != "1" ]]; then
    systemctl is-enabled cse-capture-scheduler.timer cse-capture-backup-trigger.path || rc=1
    systemctl is-active cse-capture-scheduler.timer cse-capture-backup-trigger.path || rc=1
  fi
  [[ $rc -eq 0 ]] || die "scheduler verification failed"
  echo "scheduler verification ok (armed: see 'cse-scheduler status'; provisioning never arms it)"
}

ALL=(preflight migrate dirs units verify)
main() {
  [[ $# -ge 1 ]] || die "usage: $0 all | ${ALL[*]}"
  local steps=("$@")
  [[ "$1" == "all" ]] && steps=("${ALL[@]}")
  for s in "${steps[@]}"; do
    declare -F "step_${s}" >/dev/null || die "unknown step: ${s}"
    "step_${s}"
  done
  log "done: ${steps[*]}"
}
main "$@"
