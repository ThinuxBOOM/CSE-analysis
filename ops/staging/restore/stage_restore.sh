#!/usr/bin/env bash
# The restore steps of the staging harness (docs/ops/ISOLATED_RESTORE.md; docs/ops/STAGING.md section 3, ST-30, ST-31).
# Reached ONLY through `staging.sh restore R1|R2 <RA>` (ops/staging/restore_driver.sh), which validates the owner's
# restore authorization first and records it as used. This file is not injected by `staging.sh inject` and lives
# outside /opt/cse-staging, so `staging.sh run` cannot reach it; the driver copies it in for one restore and removes it
# from the host container afterwards. NO NETWORK, NO CSE, NEVER ARMED; production is never a target.
#
#   stage_restore.sh r1-check         host container, read-only: R1's technical checks (the newest dump verifies, the
#                                     scratch area is empty, the R1 gate is closed); prints PASS/FAIL, exit 0 when all pass
#   stage_restore.sh r1               host container: ONE run of P1's cse-backup-restore-check.service, with the R1
#                                     gate's condition path present for that run only; then the path removed and the
#                                     timer disabled again (whatever happened); exactly one new restore_check row and an
#                                     empty scratch area asserted
#   stage_restore.sh source           host container, read-only: the source cluster's system identifier and the newest
#                                     succeeded local_dump of its backup ledger, verified by P1; JSON on standard output
#   stage_restore.sh r2 IN NAME       the isolated R2 container: its isolation checked, P1's provisioning steps users,
#                                     dirs and postgres (a fresh 17/main with P1's peer-only authentication), then the
#                                     procedure (ops/restore/isolated_restore.py run) on the dump IN/NAME
#   stage_restore.sh r2-teardown      the isolated R2 container: the copy stopped, after its identity is checked again
set -uo pipefail
APP=/opt/cse/app
TOOLS=/opt/cse-restore
R1_PATH=/etc/cse/staging/R1-AUTHORIZED
R2_STATE=/var/lib/cse-restore
fail=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }
info() { echo "INFO $*"; }
step() { printf '\n==> [restore %s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
psu() { runuser -u postgres -- psql -XAt -d cse -v ON_ERROR_STOP=1 -c "$1"; }
rc_rows() { psu "select count(*) from ops.backup_runs where run_kind = 'restore_check'"; }
newest_dump() {  # the dump P1's restore check restores by default (worker/ops/backup.py list_dumps, the last one)
  (set -a; . /etc/cse/cse.env; set +a; cd / && PYTHONPATH="$APP" PYTHONDONTWRITEBYTECODE=1 python3 -c \
    'from worker.ops import backup, settings; d = backup.list_dumps(settings.load()); print(d[-1] if d else "")')
}
gate_closed() {  # the R1 gate as stage_provision.sh leaves it: drop-in installed, path absent, timer disabled
  grep -q '^ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED$' /etc/systemd/system/cse-backup-restore-check.service.d/50-staging-refuse.conf \
    && [[ ! -e $R1_PATH ]] && [[ "$(systemctl is-enabled cse-backup-restore-check.timer 2>/dev/null || true)" == disabled ]]
}

r1_check() {
  local dump
  gate_closed && pass "R1 gate closed: the drop-in is installed, $R1_PATH is absent, the timer is disabled" \
    || bad "R1 gate not in its closed state"
  [[ "$(systemctl is-active cse-backup-restore-check.service 2>/dev/null || true)" != active ]] \
    && pass "the restore-check service is not running" || bad "the restore-check service is running"
  [[ -z "$(ls -A /srv/cse-backup/restore-scratch 2>/dev/null)" ]] && pass "restore-scratch is empty" || bad "restore-scratch is not empty"
  dump=$(newest_dump)
  if [[ -n $dump && -d $dump ]]; then
    info "the newest dump (P1's restore check takes it): $dump"
    if bash "$APP/ops/bin/cse-ops" backup verify "$dump" >/tmp/r1-verify.out 2>&1; then pass "the newest dump verifies (P1: cse-ops backup verify)"
    else bad "the newest dump does not verify"; sed 's/^/      /' /tmp/r1-verify.out | head -20; fi
  else bad "no local dump to restore"; fi
  info "restore_check rows in ops.backup_runs before R1: $(rc_rows)"
}

r1_close() {  # whatever happened: the condition path removed and the timer disabled again
  rm -f "$R1_PATH"
  systemctl disable --now cse-backup-restore-check.timer >/dev/null 2>&1 || true
}

r1() {
  local before after dump key started rc row
  r1_check
  [[ $fail -eq 0 ]] || { bad "R1 not run: a technical check failed"; return; }
  dump=$(newest_dump); key=${dump#/srv/cse-backup/}; before=$(rc_rows); started=$(date -u '+%F %T')
  step "R1: one run of P1's restore check (cse-backup-restore-check.service) on $key"
  trap r1_close EXIT
  install -d -m 0755 /etc/cse/staging
  install -m 0600 /dev/null "$R1_PATH"            # the R1 gate's condition, for this one run only
  systemctl start cse-backup-restore-check.service; rc=$?   # Type=oneshot: returns once the run has ended
  r1_close
  trap - EXIT
  info "systemctl start: exit $rc; $(systemctl show -p Result -p ExecMainStatus cse-backup-restore-check.service | tr '\n' ' ')"
  journalctl -u cse-backup-restore-check.service --since "$started" --no-pager -o short-iso 2>/dev/null | sed 's/^/    | /' | tail -40
  gate_closed && pass "after R1: $R1_PATH removed and the timer disabled again" || bad "after R1: the R1 gate is not closed"
  after=$(rc_rows)
  [[ $after -eq $((before + 1)) ]] && pass "exactly one new restore_check row ($before -> $after)" \
    || bad "restore_check rows: $before -> $after (exactly one new row expected)"
  row=$(psu "select id || ' ' || status || ' ' || coalesce(artifact_key, '-') || ' ' || covers::text from ops.backup_runs where run_kind = 'restore_check' order by id desc limit 1")
  info "the new restore_check row: $row"
  [[ "${row#* }" == "succeeded $key [\"$key\"]" ]] && pass "ST-30: the restore check succeeded and covers $key (every digest equal)" \
    || bad "ST-30: the restore check did not succeed on $key"
  psu "select details::text from ops.backup_runs where run_kind = 'restore_check' order by id desc limit 1" | sed 's/^/    | /'
  [[ -z "$(ls -A /srv/cse-backup/restore-scratch 2>/dev/null)" ]] && pass "restore-scratch is empty after R1" || bad "restore-scratch is not empty after R1"
}

source_record() {  # read-only: what the R2 copy is restored from, and its provenance (REG-B06)
  local sid run key dump vrc
  sid=$(psu "select system_identifier from pg_control_system()") || { echo "no system identifier" >&2; return 1; }
  run=$(psu "select row_to_json(r) from (select id, run_kind, status, host, artifact_key, artifact_sha256, manifest_sha256, started_at, finished_at from ops.backup_runs where run_kind = 'local_dump' and status = 'succeeded' order by finished_at desc, id desc limit 1) r")
  [[ -n $run ]] || { echo "no succeeded local_dump in ops.backup_runs" >&2; return 1; }
  key=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["artifact_key"])' "$run")
  dump=/srv/cse-backup/$key
  bash "$APP/ops/bin/cse-ops" backup verify "$dump" >&2; vrc=$?
  python3 - "$sid" "$run" "$dump" "$vrc" <<'PY'
import datetime, json, sys
print(json.dumps({"system_identifier": sys.argv[1], "ledger_run": json.loads(sys.argv[2]), "dump": sys.argv[3],
                  "verify_exit": int(sys.argv[4]),
                  "read_from": "the staging host's cluster 17/main: pg_control_system() and ops.backup_runs",
                  "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, sort_keys=True))
PY
}

mount_points() {  # the non-pseudo mount points of this process (as stage_checks.sh reads them)
  awk '$3 !~ /^(proc|sysfs|tmpfs|cgroup2|devpts|mqueue|overlay|devtmpfs|securityfs|debugfs|tracefs|bpf|fusectl|configfs|hugetlbfs|autofs|binfmt_misc|pstore|nsfs|ramfs)$/ {print $2}' \
    /proc/self/mounts | LC_ALL=C sort -u | paste -sd' '
}

isolated_env() {  # isolation by environment: no network, no volume, no other PostgreSQL
  local ifs
  ifs=$(awk -F: 'NR > 2 {gsub(/ /, "", $1); print $1}' /proc/net/dev | sort | tr '\n' ' ')
  [[ "$ifs" == "lo " ]] && pass "network: only the loopback interface" || bad "network interfaces: $ifs"
  [[ $(awk 'NR > 1' /proc/net/route | wc -l) -eq 0 ]] && pass "network: no IPv4 route" || bad "network: a route exists"
  [[ "$(mount_points)" == "/etc/hostname /etc/hosts /etc/resolv.conf" ]] \
    && pass "mounts: only Docker's /etc files (no volume, no bind mount)" || bad "mounts: [$(mount_points)]"
  [[ ! -e /var/run/docker.sock && ! -e /run/docker.sock ]] && pass "no Docker socket" || bad "a Docker socket is present"
  [[ -z "$(pg_lsclusters -h 2>/dev/null)" ]] && pass "no PostgreSQL cluster exists before provisioning" || bad "a cluster exists: $(pg_lsclusters -h)"
  [[ ! -e /etc/cse ]] && pass "no /etc/cse: a fresh environment" || bad "/etc/cse exists"
  ! grep -qx postgres /proc/[0-9]*/comm 2>/dev/null && pass "no PostgreSQL server process" || bad "a PostgreSQL server is running"
}

r2() {
  local in=${1:?IN} name=${2:?dump directory name} dump
  dump=$in/$name
  [[ -d $dump && -s $in/source.json ]] || { bad "R2: the dump $dump or $in/source.json is missing"; return; }
  step "R2: the isolated environment"
  isolated_env
  [[ $fail -eq 0 ]] || { bad "R2 not run: the environment is not isolated and fresh"; return; }
  step "R2: P1's provisioning steps users, dirs, postgres: the fresh copy cluster with P1's peer-only authentication"
  install -d -m 0755 /srv/cse-backup         # P1's preflight wants the backup root; a directory here (difference D-02)
  if ! CSE_NO_SYSTEMD=1 CSE_ALLOW_SAME_DEVICE=1 CSE_APP_DIR="$APP" bash "$APP/ops/provision/provision.sh" preflight users dirs postgres; then
    bad "R2: P1's provisioning failed"; return
  fi
  [[ "$(pg_lsclusters -h | awk '{print $1" "$2" "$3" "$4}')" == "17 main 5432 online" ]] \
    && pass "exactly one cluster here, 17/main on port 5432: the copy" || bad "clusters: $(pg_lsclusters -h)"
  [[ "$(find /var/run/postgresql -type s | paste -sd' ')" == "/var/run/postgresql/.s.PGSQL.5432" ]] \
    && pass "exactly one PostgreSQL socket in this environment" || bad "sockets: $(find /var/run/postgresql -type s | paste -sd' ')"
  [[ $fail -eq 0 ]] || { bad "R2 not run"; return; }
  # the copy's input: readable by postgres (pg_restore, psql) and cse-backup (P1's verify), both in group cse
  chmod 0755 "$R2_STATE" && chown root:cse "$in" && chmod 0750 "$in" && chown -R root:cse "$dump" && chmod -R u=rX,g=rX,o= "$dump"
  step "R2: the isolated-restore procedure (docs/ops/ISOLATED_RESTORE.md section 4)"
  python3 -B "$TOOLS/isolated_restore.py" run --dump "$dump" --source "$in/source.json" --evidence "$R2_STATE/evidence" --app "$APP" \
    || fail=1
}

r2_teardown() {
  step "R2: teardown of the copy only (its identity checked first)"
  python3 -B "$TOOLS/isolated_restore.py" teardown --evidence "$R2_STATE/evidence" || fail=1
}

case "${1:-}" in
  r1-check) r1_check ;;
  r1) r1 ;;
  source) source_record; exit $? ;;
  r2) shift; r2 "$@" ;;
  r2-teardown) r2_teardown ;;
  *) echo "usage: $0 r1-check|r1|source|r2 IN NAME|r2-teardown" >&2; exit 2 ;;
esac
exit $fail
