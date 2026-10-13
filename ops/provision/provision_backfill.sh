#!/usr/bin/env bash
# =============================================================================
# CSE Phase 2 backfill (HB-6) provisioning - run AFTER ops/provision/provision.sh (P1) and, where P3 runs,
# ops/provision/provision_scheduler.sh, on the same Ubuntu 24.04 server.
#
#   sudo CSE_APP_DIR=/opt/cse/app bash ops/provision/provision_backfill.sh all
#   sudo bash ops/provision/provision_backfill.sh <step>      # preflight migrate dirs units verify
#
# Idempotent. It applies pending migrations (0016, 0017) through P1's runner, prepares the backfill's state and report
# directories and its configuration template, installs and ENABLES the wake-only timer, and verifies the result.
# It NEVER arms the backfill and never contacts CSE: until the owner records `arm` (docs/ops/PHASE2_BACKFILL.md, the
# release gate), every wake-up runs the preflight and the F6 bookkeeping only, and contacts nobody.
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
STATE_DIR=/var/lib/cse-backfill
REPORT_DIR="${STATE_DIR}/reports"
UNITS=(cse-backfill.service cse-backfill.timer)

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
as_worker() { runuser -u cse-worker -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_worker \
                CSE_BACKUP_ROOT="${ROOT}" CSE_BACKFILL_REPORT_DIR="${REPORT_DIR}" \
                CSE_BACKFILL_TEMP_ROOT=/tmp/cse-backfill python3 -m worker.backfill_ops "$@"; }

step_preflight() {
  log "preflight (P1 must be provisioned first)"
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  [[ -f "${APP}/worker/backfill_ops/__main__.py" ]] || die "CSE_APP_DIR=${APP} has no HB-6 backfill runner"
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
  log "migrations (P1 runner as cse_migrator -> SET ROLE cse_owner; applies 0016 / 0017 if pending)"
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate apply
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate verify >/dev/null && echo "migration ledger verified"
}

step_dirs() {
  log "backfill state and report directories and configuration template"
  install -d -o cse-worker -g cse-worker -m 0750 "${STATE_DIR}" "${REPORT_DIR}"
  # the contact e-mail stays in /etc/cse/capture.env (P2's, EMPTY until the owner sets it: G-1 control 4)
  [[ -f /etc/cse/capture.env ]] || install -o root -g cse-worker -m 0640 "${APP}/ops/config/capture.env.example" /etc/cse/capture.env
  [[ -f /etc/cse/backfill.env ]] || install -o root -g cse-worker -m 0640 "${APP}/ops/config/backfill.env.example" /etc/cse/backfill.env
  stat -c '%U:%G %a %n' "${STATE_DIR}" "${REPORT_DIR}" /etc/cse/backfill.env
}

step_units() {
  if [[ "${NO_SYSTEMD}" == "1" ]]; then log "units: skipped (CSE_NO_SYSTEMD=1)"; return; fi
  log "backfill units: service + timer (wake-ups only)"
  local u
  for u in "${UNITS[@]}"; do
    sed -e "s#/opt/cse/app#${APP}#g" -e "s#/srv/cse-backup#${ROOT}#g" "${APP}/ops/backfill/${u}" > "/etc/systemd/system/${u}"
    chmod 0644 "/etc/systemd/system/${u}"
  done
  systemctl daemon-reload
  systemd-analyze verify "${UNITS[@]/#//etc/systemd/system/}"
  # enabling the timer contacts nobody: Phase 2 is disarmed until the owner records `arm`
  systemctl enable --now cse-backfill.timer
  systemctl list-timers cse-backfill.timer --no-pager
}

step_verify() {
  log "backfill verification (read-only)"
  local rc=0
  (cd / && as_worker verify) || rc=1
  (cd / && as_worker status >/dev/null) || { st=$?; [[ $st -eq 2 ]] || rc=1; }
  if [[ "${NO_SYSTEMD}" != "1" ]]; then
    systemctl is-enabled cse-backfill.timer || rc=1
    systemctl is-active cse-backfill.timer || rc=1
  fi
  [[ $rc -eq 0 ]] || die "backfill verification failed"
  echo "backfill verification ok (armed: see 'cse-backfill status'; provisioning never arms it)"
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
