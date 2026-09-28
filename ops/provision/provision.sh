#!/usr/bin/env bash
# =============================================================================
# CSE P1 server provisioning - Ubuntu 24.04 LTS, PostgreSQL 17 (PGDG), local socket + peer auth only.
#
#   sudo CSE_APP_DIR=/opt/cse/app bash ops/provision/provision.sh all
#   sudo bash ops/provision/provision.sh <step>      # preflight packages users dirs postgres bootstrap migrate
#                                               # units firewall verify selftest
#
# Idempotent: every step can be re-run. It never touches the live CSE API, never installs a market-capture job,
# and never deletes data: it refuses to recreate a cluster that already holds the CSE database.
#
# Environment (defaults):
#   CSE_APP_DIR=/opt/cse/app          reviewed git checkout (root-owned, read-only to services)
#   CSE_DB_NAME=cse
#   CSE_BACKUP_ROOT=/srv/cse-backup   MUST be a mounted, separate backup disk
#   CSE_PG_VERSION=17
#   CSE_NO_SYSTEMD=0                  1 = container test host: no systemctl, no timers, no firewall
#   CSE_ALLOW_SAME_DEVICE=0           1 = test host only: backup root may share PGDATA's device
#   CSE_ALLOW_DIRTY=0                 1 = test host only: allow an app checkout with uncommitted changes
# =============================================================================
set -euo pipefail

APP="${CSE_APP_DIR:-/opt/cse/app}"
DB="${CSE_DB_NAME:-cse}"
ROOT="${CSE_BACKUP_ROOT:-/srv/cse-backup}"
PGV="${CSE_PG_VERSION:-17}"
NO_SYSTEMD="${CSE_NO_SYSTEMD:-0}"
ALLOW_SAME_DEVICE="${CSE_ALLOW_SAME_DEVICE:-0}"
ALLOW_DIRTY="${CSE_ALLOW_DIRTY:-0}"
PGETC="/etc/postgresql/${PGV}/main"
PGBIN="/usr/lib/postgresql/${PGV}/bin"

log()  { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
as_pg() { runuser -u postgres -- "$@"; }

step_preflight() {
  log "preflight"
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  . /etc/os-release
  [[ "${ID}" == "ubuntu" && "${VERSION_ID}" == "24.04" ]] || die "deployment target is Ubuntu 24.04 LTS (found ${ID} ${VERSION_ID})"
  [[ -d "${APP}/supabase/migrations" && -f "${APP}/worker/ops/migrate.py" ]] || die "CSE_APP_DIR=${APP} is not a CSE checkout"
  if [[ -d "${APP}/.git" ]] && command -v git >/dev/null; then
    if [[ "${ALLOW_DIRTY}" != "1" && -n "$(git -C "${APP}" status --porcelain 2>/dev/null)" ]]; then
      die "${APP} has uncommitted changes; deploy a clean reviewed commit"
    fi
    echo "app commit: $(git -C "${APP}" rev-parse HEAD 2>/dev/null || echo unknown)"
  fi
  if [[ "${NO_SYSTEMD}" != "1" ]]; then
    [[ -d /run/systemd/system ]] || die "systemd is not running (set CSE_NO_SYSTEMD=1 only on a container test host)"
  fi
  [[ -d "${ROOT}" ]] || die "backup root ${ROOT} does not exist: mount the backup disk there first"
  if [[ "${ALLOW_SAME_DEVICE}" != "1" ]]; then
    mountpoint -q "${ROOT}" || die "${ROOT} is not a mount point: the backup root must be a separate disk"
    if [[ "$(stat -c %d "${ROOT}")" == "$(stat -c %d /var/lib)" ]]; then die "${ROOT} is on the same device as /var/lib (PGDATA)"; fi
  else
    echo "WARNING: CSE_ALLOW_SAME_DEVICE=1 - backups share the database device (test hosts only)"
  fi
  echo "preflight ok"
}

step_packages() {
  log "packages (PGDG PostgreSQL ${PGV}, python3, restic)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl gnupg postgresql-common
  if ! ls /etc/apt/sources.list.d/ 2>/dev/null | grep -q pgdg; then
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
  fi
  # every cluster created from now on gets data checksums
  if ! grep -Eq "^[[:space:]]*initdb_options[[:space:]]*=.*--data-checksums" /etc/postgresql-common/createcluster.conf; then
    printf "\n# CSE P1: data checksums on every new cluster\ninitdb_options = '--data-checksums'\n" >> /etc/postgresql-common/createcluster.conf
  fi
  apt-get install -y --no-install-recommends "postgresql-${PGV}" python3 python3-psycopg2 python3-requests restic util-linux
  if [[ "${NO_SYSTEMD}" != "1" ]]; then apt-get install -y --no-install-recommends ufw; fi
}

step_users() {
  log "service users and groups"
  getent group cse >/dev/null || groupadd --system cse
  for u in cse-worker cse-backup cse-migrator; do
    if ! id -u "$u" >/dev/null 2>&1; then
      useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$u"
    fi
    usermod -a -G cse "$u"
  done
  usermod -a -G cse postgres        # lets the server set group 'cse' on its socket (unix_socket_group)
}

step_dirs() {
  log "configuration and backup layout under ${ROOT}"
  install -d -o root -g root -m 0755 /etc/cse
  install -d -o root -g cse-backup -m 0750 /etc/cse/credentials
  [[ -f /etc/cse/cse.env ]] || install -o root -g root -m 0644 "${APP}/ops/config/cse.env.example" /etc/cse/cse.env
  [[ -f /etc/cse/backup.env ]] || install -o root -g cse-backup -m 0640 "${APP}/ops/config/backup.env.example" /etc/cse/backup.env
  install -d -o root -g root -m 0755 "${ROOT}"
  install -d -o cse-backup -g cse-backup -m 0750 "${ROOT}/pg" "${ROOT}/pg/dumps" "${ROOT}/pg/staging" "${ROOT}/status"
  install -d -o cse-backup -g cse-backup -m 0700 "${ROOT}/restore-scratch" "${ROOT}/restic-cache"
  # spool: written once by the worker (P2), readable by the backup user for off-site copies; setgid keeps the group
  install -d -o cse-worker -g cse-backup -m 2750 "${ROOT}/spool"
}

step_postgres() {
  log "PostgreSQL ${PGV} cluster (checksums, socket-only, peer)"
  if ! pg_lsclusters -h | awk '{print $1" "$2}' | grep -qx "${PGV} main"; then
    pg_createcluster "${PGV}" main -- --data-checksums
  fi
  pg_ctlcluster "${PGV}" main start 2>/dev/null || true
  local sums
  sums="$(as_pg psql -XAtc 'show data_checksums')"
  if [[ "${sums}" != "on" ]]; then
    if [[ -n "$(as_pg psql -XAtc "select 1 from pg_database where datname = '${DB}'")" ]]; then
      die "cluster ${PGV}/main has data_checksums=off and already holds '${DB}': enable them offline with pg_checksums; not recreating"
    fi
    echo "fresh cluster without checksums: recreating it with --data-checksums"
    pg_dropcluster --stop "${PGV}" main
    pg_createcluster "${PGV}" main -- --data-checksums
    pg_ctlcluster "${PGV}" main start
  fi
  install -o postgres -g postgres -m 0644 "${APP}/ops/postgres/90-cse.conf" "${PGETC}/conf.d/90-cse.conf"
  sed "s/@DBNAME@/${DB}/g" "${APP}/ops/postgres/pg_hba.conf" > "${PGETC}/pg_hba.conf.cse"
  install -o postgres -g postgres -m 0640 "${PGETC}/pg_hba.conf.cse" "${PGETC}/pg_hba.conf" && rm -f "${PGETC}/pg_hba.conf.cse"
  install -o postgres -g postgres -m 0640 "${APP}/ops/postgres/pg_ident.conf" "${PGETC}/pg_ident.conf"
  if [[ "${NO_SYSTEMD}" == "1" ]]; then pg_ctlcluster "${PGV}" main restart; else systemctl restart "postgresql@${PGV}-main"; systemctl enable "postgresql@${PGV}-main" >/dev/null 2>&1 || true; fi
  as_pg psql -XAtc "select 'version='||current_setting('server_version')||' checksums='||current_setting('data_checksums')||' listen='''||current_setting('listen_addresses')||''''"
}

step_bootstrap() {
  log "roles and database '${DB}' (postgres superuser, once per cluster)"
  as_pg psql -X -v ON_ERROR_STOP=1 -v dbname="${DB}" -f "${APP}/ops/provision/sql/bootstrap_cluster.sql"
}

step_migrate() {
  log "migrations (runner as cse_migrator -> SET ROLE cse_owner)"
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate apply
  runuser -u cse-migrator -- env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_migrator \
    python3 -m worker.ops.migrate verify >/dev/null && echo "migration ledger verified"
}

step_units() {
  if [[ "${NO_SYSTEMD}" == "1" ]]; then log "units: skipped (CSE_NO_SYSTEMD=1)"; return; fi
  log "backup systemd units (no market-capture units exist in P1)"
  local f
  for f in "${APP}"/ops/systemd/cse-backup-*.service "${APP}"/ops/systemd/cse-backup-*.timer; do
    # the unit files name the default paths; render the configured ones
    sed -e "s#/opt/cse/app#${APP}#g" -e "s#/srv/cse-backup#${ROOT}#g" "$f" > "/etc/systemd/system/$(basename "$f")"
    chmod 0644 "/etc/systemd/system/$(basename "$f")"
  done
  systemctl daemon-reload
  systemctl enable --now cse-backup-dump.timer cse-backup-offsite.timer cse-backup-offsite-check.timer \
    cse-backup-restore-check.timer cse-backup-status.timer
  systemctl list-timers 'cse-backup-*' --no-pager
}

step_firewall() {
  if [[ "${NO_SYSTEMD}" == "1" ]]; then log "firewall: skipped (CSE_NO_SYSTEMD=1)"; return; fi
  log "firewall: deny incoming except SSH (PostgreSQL has no TCP listener at all)"
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow OpenSSH
  ufw --force enable
  ufw status verbose
}

step_verify() {
  log "security verification (read-only)"
  local extra=()
  [[ "${ALLOW_SAME_DEVICE}" == "1" ]] && extra+=(--allow-same-device)
  local rc=0
  # database checks as the postgres superuser (pg_hba_file_rules needs it) ...
  as_pg env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_BACKUP_ROOT="${ROOT}" \
    python3 -m worker.ops.verify_server --repo "${APP}" --skip-os || rc=1
  # ... host / filesystem checks as root (postgres cannot and must not read the backup directories)
  env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_BACKUP_ROOT="${ROOT}" \
    python3 -m worker.ops.verify_server --os-only --pgdata "/var/lib/postgresql/${PGV}/main" "${extra[@]}" || rc=1
  [[ $rc -eq 0 ]] || die "security verification failed (see FAIL lines above)"
}

step_selftest() {
  log "backup self-test: dump -> verify -> restore check (as cse-backup)"
  local envs=(env PYTHONPATH="${APP}" CSE_DB_NAME="${DB}" CSE_DB_USER=cse_backup CSE_BACKUP_ROOT="${ROOT}" CSE_PG_BINDIR="${PGBIN}")
  if [[ -f /etc/cse/backup.env ]]; then
    while IFS= read -r kv; do envs+=("$kv"); done < <(grep -E '^[A-Z_]+=' /etc/cse/backup.env || true)
  fi
  (cd /tmp && runuser -u cse-backup -- "${envs[@]}" python3 -m worker.ops.backup dump)
  (cd /tmp && runuser -u cse-backup -- "${envs[@]}" python3 -m worker.ops.backup verify --all)
  (cd /tmp && runuser -u cse-backup -- "${envs[@]}" python3 -m worker.ops.restore_check)
  (cd /tmp && runuser -u cse-backup -- "${envs[@]}" python3 -m worker.ops.offsite sync) || \
    echo "off-site sync did not succeed (expected while CSE_OFFSITE_MODE=disabled: recorded as not_configured)"
}

ALL=(preflight packages users dirs postgres bootstrap migrate units firewall verify selftest)
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
