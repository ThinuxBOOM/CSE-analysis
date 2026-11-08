#!/usr/bin/env bash
# Read-mostly checks inside the staging host container (docs/ops/STAGING.md), as root, with NO network. Every write is
# either a refused attempt inside a transaction that is rolled back, or nothing at all. Never arms, never restores,
# never resolves a host name.
#
#   stage_checks.sh isolation [CAP...]   ST-04  interfaces, routes, a literal-IP connect, Docker socket, cgroup namespace,
#                                 the exact mounts and the exact effective capabilities (Docker's default set plus the
#                                 rung's: SYS_ADMIN when no CAP is named, i.e. L1); the driver's `isolation` passes them
#   stage_checks.sh ledger        ST-07  the migration ledger, 0001-0018 (0006 unused)
#   stage_checks.sh roles         ST-10  roles, socket, listener, and every role x append-only table x UPDATE/DELETE/TRUNCATE
#   stage_checks.sh preflights    ST-11  every frozen component's own verification
#   stage_checks.sh units         ST-12  units, timers, R1 gate
#   stage_checks.sh filesystem    ST-18  application, spool, state, reports, temporary root
#   stage_checks.sh invariants    every phase: never armed, no restore ran, contact e-mail empty
#   stage_checks.sh fingerprint [L]   ST-06, ST-26  schema, roles, grants, ledger, units, packages (hashes + listings) into
#                                 a new snapshot directory under the label L, never reused; its path is printed
#   stage_checks.sh fpcompare A B ST-06, ST-26  two snapshot directories, component by component
#   stage_checks.sh metrics       ST-23  relation and database sizes
#   stage_checks.sh seqkinds      ST-32  REG-B32 / UR-8: the sequence kinds (read-only catalogue query)
#   stage_checks.sh datadigest L  ST-08  every table's rows (sorted row texts) under the label L, before the upgrade
#   stage_checks.sh dataverify L  ST-08  every row digested under L is still present and unchanged
#   stage_checks.sh rf4verify     ST-27  R-F4's full `verify` (runbook section 12 steps 2-5): the preflight through the
#                                 migration role and, as the second route, as the login; then `verify` AS THE LOGIN
#                                 cse_verifier; exit 0, clean, complete scope
#   stage_checks.sh reader        ST-28  the real reader login, R-F4 section 20.6 steps 1-5 (REG-A06); steps 1, 2, 4 and
#                                 5 run as the OS user cse-verifier over the Unix socket, by peer authentication: never
#                                 SET ROLE, never a superuser session (step 6 needs a restored copy: not run here)
#   stage_checks.sh regx03        ST-29  P1's verify_server (REG-X03, P1-CC-1) through the frozen p1-verify probe
# Exit 0 when every check passed; 1 otherwise. Output: PASS/FAIL/INFO lines.
set -uo pipefail
APP=/opt/cse/app
STATE=/var/lib/cse-staging
fail=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }
info() { echo "INFO $*"; }
check() { local name=$1; shift; if "$@" >/tmp/stg.out 2>&1; then pass "$name"; else bad "$name"; sed 's/^/      /' /tmp/stg.out | head -15; fi; }
refused() { local name=$1; shift; if "$@" >/tmp/stg.out 2>&1; then bad "$name (NOT refused)"; else pass "$name"; fi; }
psu() { runuser -u postgres -- psql -XAt -d cse -v ON_ERROR_STOP=1 -c "$1"; }
as_role() {  # as_role worker|backup|owner SQL  (owner = cse-migrator, SET LOCAL ROLE cse_owner)
  case "$1" in
    worker) runuser -u cse-worker -- psql -XAt -U cse_worker -d cse -v ON_ERROR_STOP=1 -c "$2" ;;
    backup) runuser -u cse-backup -- psql -XAt -U cse_backup -d cse -v ON_ERROR_STOP=1 -c "$2" ;;
    owner) runuser -u cse-migrator -- psql -XAt -U cse_migrator -d cse -v ON_ERROR_STOP=1 -c "$2" ;;
  esac
}

# The reader login (P1-CC-1; R-F4 section 20.6, runbook section 12 step 4). The URL names the role: without it libpq asks
# for the role `cse-verifier` (the OS user) and the peer map, which maps that OS user to cse_verifier only, refuses it.
VERIFIER_URL='postgresql://cse_verifier@/cse?host=/var/run/postgresql'
RF4="$STATE/rf4"
login_psql() {  # the login's own session: OS user cse-verifier, peer authentication over the socket, no password
  runuser -u cse-verifier -- env -u PGPASSWORD PGPASSFILE=/nonexistent/pgpass PGCONNECT_TIMEOUT=10 psql -XAt -v ON_ERROR_STOP=1 -d "$VERIFIER_URL" "$@"
}
login_verify() {  # runbook section 12 step 4, verbatim: the full verify (no --limit), as the login
  (cd / && runuser -u cse-verifier -- env PYTHONPATH="$APP" PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$VERIFIER_URL" \
    python3 -m worker.financial_f4_store verify)
}
migration_preflight() {  # runbook section 12 step 2: the ledger read through the migration role (owner-level)
  (cd / && runuser -u cse-migrator -- env PYTHONPATH="$APP" PYTHONDONTWRITEBYTECODE=1 CSE_DB_NAME=cse CSE_DB_USER=cse_migrator \
    python3 -m worker.financial_f4_store preflight --migration-role)
}
verifier_preflight() {  # runbook section 12 step 2, second route: the preflight AS THE LOGIN through DATABASE_URL (no --migration-role)
  (cd / && runuser -u cse-verifier -- env PYTHONPATH="$APP" PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$VERIFIER_URL" \
    python3 -m worker.financial_f4_store preflight)
}
super_counts() {  # the two tables verify enumerates, as the bootstrap superuser: "extractions runs"
  psu "select (select count(*) from financial_f4_extractions) || ' ' || (select count(*) from financial_extraction_runs)"
}
rf4_dir() { mkdir -p "$RF4" && mktemp -d "$RF4/$1-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX"; }  # a new directory each run, never reused
run_json() {  # run_json DIR NAME CMD...: stdout -> DIR/NAME.json, stderr -> DIR/NAME.err; the exit status in $RC; the JSON echoed
  local d=$1 n=$2; shift 2
  "$@" > "$d/$n.json" 2> "$d/$n.err"; RC=$?
  info "$n: exit $RC; saved $d/$n.json ($(wc -c < "$d/$n.json") bytes)"
  awk '{print "    | " $0}' "$d/$n.json"                # awk, not sed: the last line gets its newline even without one
  if [[ -s "$d/$n.err" ]]; then info "$n: stderr"; sed 's/^/    ! /' "$d/$n.err" | head -20; fi
}
jassert() {  # jassert NAME FILE PYTHON-EXPRESSION-OF-d: PASS when it evaluates to True
  local v
  v=$(python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$2" "$3" 2>&1 | tail -n 1)
  [[ $v == True ]] && pass "$1" || bad "$1 (evaluated: $v)"
}

# Capability names by bit (linux/capability.h) and Docker's default set. The approved rungs add SYS_ADMIN (L1) and, at L2
# only, the capabilities named to the driver; nothing else may be effective in the container (H-3)
CAPS=(CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER FSETID KILL SETGID SETUID SETPCAP LINUX_IMMUTABLE NET_BIND_SERVICE
      NET_BROADCAST NET_ADMIN NET_RAW IPC_LOCK IPC_OWNER SYS_MODULE SYS_RAWIO SYS_CHROOT SYS_PTRACE SYS_PACCT SYS_ADMIN
      SYS_BOOT SYS_NICE SYS_RESOURCE SYS_TIME SYS_TTY_CONFIG MKNOD LEASE AUDIT_WRITE AUDIT_CONTROL SETFCAP MAC_OVERRIDE
      MAC_ADMIN SYSLOG WAKE_ALARM BLOCK_SUSPEND AUDIT_READ PERFMON BPF CHECKPOINT_RESTORE)
DOCKER_DEFAULT_CAPS=(AUDIT_WRITE CHOWN DAC_OVERRIDE FOWNER FSETID KILL MKNOD NET_BIND_SERVICE NET_RAW SETFCAP SETGID SETPCAP
                     SETUID SYS_CHROOT)
# the host container's only non-pseudo mounts (staging.sh cmd_up): Docker's own /etc files and the campaign's volumes
HOST_MOUNTS="/etc/hostname /etc/hosts /etc/resolv.conf /srv/cse-backup /srv/cse-backup/offsite-test /var/lib/postgresql"
cap_names() {  # cap_names HEX -> the names in a /proc Cap* mask, sorted, on one line (an unnamed bit as BIT<n>)
  local m=$((16#$1)) i out=()
  for ((i = 0; i < 64; i++)); do (( (m >> i) & 1 )) && out+=("${CAPS[$i]:-BIT$i}"); done
  printf '%s\n' "${out[@]}" | LC_ALL=C sort | paste -sd' '
}
want_caps() { printf '%s\n' "${DOCKER_DEFAULT_CAPS[@]}" "${@:-SYS_ADMIN}" | tr '[:lower:]' '[:upper:]' | sed 's/^CAP_//' | LC_ALL=C sort -u | paste -sd' '; }
mount_points() {  # the non-pseudo mount points of a mounts table (default: this process's)
  awk '$3 !~ /^(proc|sysfs|tmpfs|cgroup2|devpts|mqueue|overlay|devtmpfs|securityfs|debugfs|tracefs|bpf|fusectl|configfs|hugetlbfs|autofs|binfmt_misc|pstore|nsfs|ramfs)$/ {print $2}' \
    "${1:-/proc/self/mounts}" | LC_ALL=C sort -u | paste -sd' '
}

isolation() {  # isolation [CAP...]: the extra capabilities of the rung (none named: SYS_ADMIN, rung L1)
  local ifs routes got want mounts
  ifs=$(awk -F: 'NR > 2 {gsub(/ /, "", $1); print $1}' /proc/net/dev | sort | tr '\n' ' ')
  [[ "$ifs" == "lo " ]] && pass "network: only the loopback interface ($ifs)" || bad "network interfaces: $ifs"
  routes=$(awk 'NR > 1' /proc/net/route | wc -l)
  [[ "$routes" -eq 0 ]] && pass "network: no IPv4 route" || bad "network: $routes IPv4 routes"
  refused "network: a TCP connect to a literal external address fails (no name is resolved)" \
    python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('1.1.1.1', 443))"
  [[ ! -e /var/run/docker.sock && ! -e /run/docker.sock ]] && pass "no Docker socket" || bad "a Docker socket is present"
  if grep -q '/docker' /proc/1/cgroup; then bad "cgroup: PID 1 sees the VM's hierarchy: $(cat /proc/1/cgroup)"
  else pass "cgroup: private namespace (PID 1 at $(cut -d: -f3 /proc/1/cgroup))"; fi
  got=$(cap_names "$(awk '/^CapEff:/ {print $2}' /proc/1/status)")
  want=$(want_caps "$@")
  [[ "$got" == "$want" ]] && pass "capabilities: PID 1's effective set is Docker's default set plus ${*:-SYS_ADMIN}, nothing else" \
    || bad "capabilities: PID 1 has [$got]; the approved rung gives [$want]"
  info "capabilities of PID 1: $(grep -E '^Cap(Eff|Bnd)' /proc/1/status | tr '\n' ' ')"
  mounts=$(mount_points)
  [[ "$mounts" == "$HOST_MOUNTS" ]] && pass "mounts: only Docker's /etc files and the three campaign volumes (no host bind mount)" \
    || bad "mounts: [$mounts]; expected [$HOST_MOUNTS]"
  info "seccomp of PID 1: $(grep -E '^Seccomp' /proc/1/status | tr -s '\t' ' ')"
  info "block devices visible: $(ls /dev | grep -E '^(sd|vd|nvme|loop|dm-)' | tr '\n' ' ' || true)"
}

ledger() {
  psu "select version || ' ' || filename || ' ' || sha256 from ops.schema_migrations order by version" | tee /tmp/ledger.txt
  local versions
  versions=$(cut -d' ' -f1 /tmp/ledger.txt | tr '\n' ' ')
  [[ "$versions" == "1 2 3 4 5 7 8 9 10 11 12 13 14 15 16 17 18 " ]] && pass "ledger: 0001-0018, 0006 unused" \
    || bad "ledger versions: $versions"
  check "migration runner verify (cse-ops)" bash "$APP/ops/bin/cse-ops" migrate verify
}

roles() {
  info "roles: $(psu "select string_agg(rolname || ':' || case when rolcanlogin then 'login' else 'nologin' end || case when rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls then ':ELEVATED' else '' end, ' ' order by rolname) from pg_roles where rolname like 'cse%'")"
  [[ -z "$(psu "select rolname from pg_roles where rolname like 'cse%' and (rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls)")" ]] \
    && pass "no cse_* role has an elevated attribute" || bad "a cse_* role is elevated"
  [[ "$(psu "select rolname from pg_roles where rolsuper order by 1" | tr '\n' ' ')" == "postgres " ]] \
    && pass "only the bootstrap superuser" || bad "superusers: $(psu "select string_agg(rolname, ' ') from pg_roles where rolsuper")"
  [[ "$(psu "select rolcanlogin from pg_roles where rolname = 'cse_reader'")" == f ]] && pass "cse_reader is NOLOGIN" || bad "cse_reader can log in"
  [[ "$(psu "select rolcanlogin from pg_roles where rolname = 'cse_owner'")" == f ]] && pass "cse_owner is NOLOGIN" || bad "cse_owner can log in"
  check "worker connects by the peer map" as_role worker "select 1"
  refused "the worker OS user cannot connect as another role" runuser -u cse-worker -- psql -XAt -U cse_backup -d cse -c "select 1"
  id -u stgprobe >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin stgprobe
  refused "an unmapped OS user is rejected" runuser -u stgprobe -- psql -XAt -U cse_worker -d cse -c "select 1"
  refused "the owner role cannot log in" runuser -u postgres -- psql -XAt -U cse_owner -d cse -c "select 1"
  [[ "$(stat -c '%U:%G %a' /var/run/postgresql/.s.PGSQL.5432)" == "postgres:cse 770" ]] && pass "socket postgres:cse 0770" \
    || bad "socket: $(stat -c '%U:%G %a' /var/run/postgresql/.s.PGSQL.5432)"
  [[ "$(psu "show listen_addresses")" == "" ]] && pass "listen_addresses = ''" || bad "listen_addresses: $(psu "show listen_addresses")"
  if awk 'NR > 1 && $4 == "0A" {print $2}' /proc/net/tcp /proc/net/tcp6 2>/dev/null | grep -qi ':1538$'; then bad "a TCP listener on 5432"
  else pass "no TCP listener on 5432"; fi
  [[ "$(psu "show data_checksums")" == on ]] && pass "data checksums on" || bad "data checksums off"
  # every role x every table carrying an append-only (UPDATE/DELETE) or no-truncate trigger
  psu "select distinct n.nspname || '.' || c.relname || ' ' || (t.tgname like '%no_truncate%') from pg_trigger t
         join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace
        where not t.tgisinternal and (t.tgname like '%append_only%' or t.tgname like '%no_truncate%')
          and n.nspname in ('public', 'ops') order by 1" > /tmp/ao.txt
  info "tables carrying append-only/no-truncate triggers: $(cut -d' ' -f1 /tmp/ao.txt | sort -u | wc -l)"
  local tbl kind col rows r
  local refusals=0 empties=0 notref=0
  while read -r tbl kind; do
    rows=$(psu "select count(*) from $tbl")
    col=$(psu "select quote_ident(attname) from pg_attribute where attrelid = '$tbl'::regclass and attnum > 0 and not attisdropped order by attnum limit 1")
    for r in worker backup owner; do
      local pre=""; [[ $r == owner ]] && pre="set local role cse_owner;"
      if [[ "$kind" == t ]]; then
        if as_role $r "begin; $pre truncate $tbl cascade; rollback;" >/tmp/stg.out 2>&1; then
          notref=$((notref + 1)); bad "$r TRUNCATE $tbl NOT refused"
        else refusals=$((refusals + 1)); fi
      else
        if [[ "$rows" -eq 0 ]]; then empties=$((empties + 1)); continue; fi
        for op in update delete; do
          local sql="update $tbl set $col = $col where ctid = (select ctid from $tbl limit 1)"
          [[ $op == delete ]] && sql="delete from $tbl where ctid = (select ctid from $tbl limit 1)"
          if as_role $r "begin; $pre $sql; rollback;" >/tmp/stg.out 2>&1; then
            notref=$((notref + 1)); bad "$r ${op^^} $tbl NOT refused"
          else refusals=$((refusals + 1)); fi
        done
      fi
    done
  done < /tmp/ao.txt
  info "append-only matrix: $refusals refused, $notref NOT refused, $empties role x empty-table cases untested (no row to change)"
  [[ $notref -eq 0 ]] && pass "append-only enforcement: every attempted UPDATE/DELETE/TRUNCATE refused" || true
}

preflights() {
  check "P1 verifier (frozen p1-verify probe; only the container clock may fail)" bash "$APP/ops/tests/p3_container_probes.sh" p1-verify
  check "migration ledger verifies (cse-ops)" bash "$APP/ops/bin/cse-ops" migrate verify
  check "R-F4 preflight, --migration-role (owner-level ledger read)" runuser -u cse-migrator -- env PYTHONPATH="$APP" \
    CSE_DB_NAME=cse CSE_DB_USER=cse_migrator python3 -m worker.financial_f4_store preflight --migration-role
  check "P3: cse-scheduler verify" bash "$APP/ops/bin/cse-scheduler" verify
  check "HB-6: cse-backfill verify (HB-1..HB-6 composite + F6.4)" bash "$APP/ops/bin/cse-backfill" verify
  check "F6.4: cse-financial verify" bash "$APP/ops/bin/cse-financial" verify
}

units() {
  local u
  for u in cse-backup-dump cse-backup-offsite cse-backup-offsite-check cse-backup-status cse-capture-scheduler cse-backfill; do
    [[ "$(systemctl is-enabled "$u.timer" 2>/dev/null)" == enabled ]] && pass "timer enabled: $u" || bad "timer not enabled: $u"
  done
  [[ "$(systemctl is-enabled cse-capture-backup-trigger.path 2>/dev/null)" == enabled ]] && pass "path unit enabled: cse-capture-backup-trigger" \
    || bad "path unit not enabled: cse-capture-backup-trigger"
  [[ "$(systemctl is-enabled cse-backup-restore-check.timer 2>/dev/null)" == disabled ]] && pass "R1 gate: restore-check timer disabled" \
    || bad "R1 gate: restore-check timer is $(systemctl is-enabled cse-backup-restore-check.timer 2>&1)"
  grep -q '^ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED$' /etc/systemd/system/cse-backup-restore-check.service.d/50-staging-refuse.conf \
    && [[ ! -e /etc/cse/staging/R1-AUTHORIZED ]] && pass "R1 gate: the restore-check service's refusal condition is installed and unmet" \
    || bad "R1 gate drop-in missing or its condition met"
  check "systemd-analyze verify of every CSE unit" bash -c 'systemd-analyze verify /etc/systemd/system/cse-*.service /etc/systemd/system/cse-*.timer /etc/systemd/system/cse-*.path'
  info "timers: $(systemctl list-timers --all --no-pager --no-legend 'cse-*' | awk '{print $NF}' | tr '\n' ' ')"
  local failed
  failed=$(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')
  info "failed units: ${failed:-none}"
}

filesystem() {
  [[ -z "$(find "$APP" -perm /022 -print -quit)" ]] && pass "application: no group/world-writable file" || bad "application has group/world-writable files"
  [[ "$(stat -c %U "$APP")" == root ]] && pass "application root-owned" || bad "application owner $(stat -c %U "$APP")"
  for u in cse-worker cse-backup cse-migrator cse-verifier; do      # cse-verifier: P1-CC-1's reader login
    refused "$u cannot write the application" runuser -u "$u" -- touch "$APP/stg-probe"
  done
  [[ "$(stat -c '%U:%G %a' /srv/cse-backup/spool)" == "cse-worker:cse-backup 2750" ]] && pass "spool cse-worker:cse-backup 2750" \
    || bad "spool: $(stat -c '%U:%G %a' /srv/cse-backup/spool)"
  [[ "$(stat -c '%a' /etc/cse/credentials)" == 750 ]] && pass "credentials directory 0750" || bad "credentials: $(stat -c '%a' /etc/cse/credentials)"
  [[ "$(stat -c '%U:%G %a' /var/lib/cse-backfill/reports)" == "cse-worker:cse-worker 750" ]] && pass "HB-6 reports cse-worker 0750 (outside the repository)" \
    || bad "reports: $(stat -c '%U:%G %a' /var/lib/cse-backfill/reports 2>&1)"
  [[ ! -e /tmp/cse-backfill || -z "$(ls -A /tmp/cse-backfill 2>/dev/null)" ]] && pass "no leftover in the host's /tmp/cse-backfill" \
    || bad "leftovers in /tmp/cse-backfill"
  [[ "$(systemctl show -p PrivateTmp --value cse-backfill.service)" == yes ]] && pass "backfill unit: PrivateTmp" || bad "backfill unit without PrivateTmp"
  [[ -z "$(ls -A /srv/cse-backup/restore-scratch 2>/dev/null)" ]] && pass "restore-scratch empty" || bad "restore-scratch not empty"
}

R1_RUNS=/var/lib/cse-staging/r1-authorized-runs   # "<run id> <RA id> <RA sha256>": written only by an authorized R1
restore_rows_ok() {  # restore_rows_ok LABEL: every restore_check row is an authorized R1 run, and every binding a row (F-2)
  local rows bound malformed
  rows=$(psu "select coalesce(string_agg(id::text, ' ' order by id), '') from ops.backup_runs where run_kind = 'restore_check'")
  malformed=0; bound=""
  if [[ -e $R1_RUNS ]]; then
    malformed=$(grep -cvE '^[1-9][0-9]* RA-[0-9]{4} [0-9a-f]{64}$' "$R1_RUNS")
    bound=$(awk '{print $1}' "$R1_RUNS" | sort -n | paste -sd' ')
    [[ -z "$(awk '{print $1}' "$R1_RUNS" | sort | uniq -d)$(awk '{print $2}' "$R1_RUNS" | sort | uniq -d)" ]] || malformed=1
  fi
  if [[ $malformed -ne 0 ]]; then bad "$1: $R1_RUNS has a malformed or repeated binding"
  elif [[ "$rows" == "$bound" ]]; then
    pass "$1: every restore_check row is an authorized R1 run bound to its RA (rows: ${rows:-none})"
  else bad "$1: restore_check rows [${rows:-none}] are not exactly the authorized R1 runs [${bound:-none}]"; fi
}

invariants() {
  local n
  restore_rows_ok "no restore check ran except an authorized R1 (ops.backup_runs)"
  [[ -z "$(ls -A /srv/cse-backup/restore-scratch 2>/dev/null)" ]] && pass "restore-scratch empty" || bad "restore-scratch not empty"
  [[ ! -e /etc/cse/staging/R1-AUTHORIZED ]] && pass "no R1 authorization path exists" || bad "R1 authorization path exists"
  n=$(psu "select (select count(*) from backfill_arming_decisions where armed) + (select count(*) from market_schedule_settings where armed)")
  [[ "$n" == 0 ]] && pass "never armed (no armed HB-1 or P3 decision recorded)" || bad "armed decisions recorded: $n"
  grep -qx 'CSE_CAPTURE_CONTACT_EMAIL=' /etc/cse/capture.env && pass "contact e-mail empty (G-1 control 4: every CSE command refuses)" \
    || bad "the contact e-mail is set"
}

FP_COMPONENTS=(schema.sql roles.txt memberships.txt grants.txt ledger.txt units.txt packages.txt)
# snapshot_dir LABEL -> a new, empty directory $STATE/fingerprints/LABEL-<UTC>-<random>: a retry never reuses or
# overwrites an earlier snapshot, failed attempts' included (H-5)
snapshot_dir() {
  [[ $1 =~ ^[A-Za-z0-9._-]+$ ]] || return 1
  mkdir -p "$STATE/fingerprints" && mktemp -d "$STATE/fingerprints/$1-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX"
}

fpcompare() {  # fpcompare DIR-A DIR-B: each component identical, or FAIL with its count of differing lines
  local c
  [[ -d $1 && -d $2 ]] || { bad "fpcompare: not two snapshot directories: $1 $2"; return; }
  for c in "${FP_COMPONENTS[@]}"; do
    if [[ ! -f $1/$c || ! -f $2/$c ]]; then bad "missing: $c"
    elif cmp -s "$1/$c" "$2/$c"; then pass "identical: $c"
    else bad "differs: $c ($(diff "$1/$c" "$2/$c" | grep -c '^[<>]') lines)"; fi
  done
}

fingerprint() {  # fingerprint [LABEL]
  local d
  d=$(snapshot_dir "${1:-run}") || { bad "fingerprint: no new snapshot directory for label '${1:-run}'"; return; }
  info "snapshot: $d"
  # pg_dump 17.6+ writes a random \restrict / \unrestrict key on every run (REG-B31, UR-6): dropped from the fingerprint
  runuser -u postgres -- pg_dump -d cse --schema-only 2>/dev/null | grep -v -e '^-- Dumped' -e '^\\restrict ' -e '^\\unrestrict ' \
    > "$d/schema.sql"
  psu "select rolname, rolcanlogin, rolinherit from pg_roles where rolname like 'cse%' order by 1" > "$d/roles.txt"
  psu "select r.rolname || '->' || m.rolname || ' inherit=' || a.inherit_option || ' set=' || a.set_option from pg_auth_members a join pg_roles r on r.oid = a.member join pg_roles m on m.oid = a.roleid where r.rolname like 'cse%' order by 1" > "$d/memberships.txt"
  psu "select n.nspname || '.' || c.relname || ' ' || coalesce(array_to_string(c.relacl, ','), '') from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('public','ops') and c.relkind in ('r','v','m','S','p') order by 1" > "$d/grants.txt"
  psu "select version || ' ' || filename || ' ' || sha256 from ops.schema_migrations order by version" > "$d/ledger.txt"
  (cd /etc/systemd/system && sha256sum cse-* 2>/dev/null; find cse-backup-restore-check.service.d -type f -exec sha256sum {} +) > "$d/units.txt"
  cp /usr/share/cse-staging/packages.txt "$d/packages.txt"
  (cd "$d" && sha256sum "${FP_COMPONENTS[@]}") | tee "$d/SHA256SUMS"
}

metrics() {
  psu "select 'database ' || pg_size_pretty(pg_database_size('cse')) || ' (' || pg_database_size('cse') || ' bytes)'"
  psu "select c.relname || ' ' || pg_total_relation_size(c.oid) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'r' and (c.relname like 'financial_%' or c.relname like 'report_%') order by pg_total_relation_size(c.oid) desc, c.relname"
  psu "select 'f4_extractions ' || count(*) from financial_f4_extractions"
  psu "select 'f5_runs ' || count(*) from financial_extraction_runs"
  psu "select 'f4_bytes_per_extraction ' || coalesce((select sum(pg_total_relation_size(c.oid)) from pg_class c where c.relname like 'financial_f4_%' and c.relkind = 'r') / nullif((select count(*) from financial_f4_extractions), 0), 0)"
}

seqkinds() {  # every sequence, in the terms of R-F4 16-I section 3.21 item 7 (unlogged, extension, identity, temporary)
  local list
  list=$(psu "select n.nspname || '.' || c.relname || ' type=' || format_type(s.seqtypid, null)
                || ' persistence=' || case c.relpersistence when 'p' then 'logged' when 'u' then 'unlogged' else 'temporary' end
                || ' extension=' || coalesce((select e.extname from pg_depend d join pg_extension e on e.oid = d.refobjid
                                              where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e'), 'none')
                || ' kind=' || coalesce((select case d.deptype when 'i' then 'identity-' || case a.attidentity when 'a' then 'always'
                                                 when 'd' then 'by-default' else 'unknown' end else 'serial' end
                                                 || ' column=' || a.attrelid::regclass || '.' || a.attname
                                         from pg_depend d join pg_attribute a on a.attrelid = d.refobjid and a.attnum = d.refobjsubid
                                         where d.classid = 'pg_class'::regclass and d.objid = c.oid
                                           and d.refclassid = 'pg_class'::regclass and d.deptype in ('a', 'i') limit 1), 'standalone')
              from pg_sequence s join pg_class c on c.oid = s.seqrelid join pg_namespace n on n.oid = c.relnamespace order by 1")
  echo "$list"
  local k
  for k in kind persistence extension type; do
    info "$k: $(grep -o " $k=[^ ]*" <<< "$list" | sort | uniq -c | awk '{printf "%s %s; ", $1, $2}')"
  done
}

user_tables() { psu "select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('public', 'ops') and c.relkind in ('r', 'p') order by 1"; }
table_rows() { runuser -u postgres -- psql -XAtq -d cse -v ON_ERROR_STOP=1 -c "copy (select t::text from $1 t) to stdout" | LC_ALL=C sort; }

datadigest() {  # every table's rows as sorted row texts (ST-08: taken before the upgrade)
  local d=/var/lib/cse-staging/datadigest-$1 t
  [[ ! -e $d ]] || { bad "$d exists (not overwritten)"; return; }
  mkdir -p "$d"
  for t in $(user_tables); do table_rows "$t" > "$d/$t.txt"; done
  (cd "$d" && sha256sum -- *.txt > SHA256SUMS)
  info "$(ls "$d"/*.txt | wc -l) tables, $(cat "$d"/*.txt | wc -l) rows digested into $d"
}

dataverify() {  # every row recorded by `datadigest LABEL` is still present and unchanged (rows added since are listed)
  local d=/var/lib/cse-staging/datadigest-$1 f t missing added bad_rows=0 grown=""
  [[ -d $d ]] || { bad "no digest $d"; return; }
  for f in "$d"/*.txt; do
    t=$(basename "$f" .txt)
    table_rows "$t" > /tmp/stg-rows.txt 2>/tmp/stg-rows.err || { bad "$t: unreadable: $(head -1 /tmp/stg-rows.err)"; bad_rows=1; continue; }
    missing=$(LC_ALL=C comm -23 "$f" /tmp/stg-rows.txt | wc -l)
    added=$(LC_ALL=C comm -13 "$f" /tmp/stg-rows.txt | wc -l)
    ((missing > 0)) && { bad "$t: $missing row(s) present at '$1' are missing or changed"; bad_rows=1; }
    ((added > 0)) && grown+="$t +$added; "
  done
  ((bad_rows == 0)) && pass "every row of the $(ls "$d"/*.txt | wc -l) tables digested at '$1' ($(cat "$d"/*.txt | wc -l) rows) is present and unchanged"
  info "rows added since '$1': ${grown:-none}"
}

# ---- the reader login: ST-27, ST-28, ST-29 (P1-CC-1; R-F4 section 20.6; runbook section 12) -------------------------------
# The login's own sessions go through `runuser -u cse-verifier` and VERIFIER_URL, nothing else: no SET ROLE, no superuser
# session (the whole point of section 20.6). The superuser appears only where the text asks for it: the counts that
# step 4 compares with (super_counts) and the controls that show the authentication is peer (reader_peer_controls).

refused_with() {  # refused_with NAME REGEX CMD...: the command fails AND its message matches (not a failure for another reason)
  local name=$1 re=$2; shift 2
  if "$@" >/tmp/stg.out 2>&1; then bad "$name (NOT refused)"
  elif grep -Eq "$re" /tmp/stg.out; then pass "$name: $(grep -Eo "$re" /tmp/stg.out | head -1)"
  else bad "$name (refused, but not for the expected reason)"; sed 's/^/      /' /tmp/stg.out | head -5; fi
}

preflight_clean() {  # preflight_clean DIR LABEL: the R-F4 preflight through the migration role: exit 0, "clean": true
  run_json "$1" preflight-migration-role migration_preflight
  [[ $RC -eq 0 ]] && pass "$2: the preflight through the migration role exits 0" || bad "$2: the preflight exits $RC"
  jassert "$2: the preflight is clean (\"clean\": true)" "$1/preflight-migration-role.json" 'd["clean"] is True'
}

preflight_clean_as_login() {  # preflight_clean_as_login DIR LABEL: the second route: exit 0 (2 is "unverifiable", not a pass), "clean": true
  run_json "$1" preflight-as-login verifier_preflight
  [[ $RC -eq 0 ]] && pass "$2: the preflight as the login (DATABASE_URL) exits 0" || bad "$2: the preflight as the login exits $RC (2 = the ledger could not be read: not a pass)"
  jassert "$2: the preflight as the login is clean (\"clean\": true)" "$1/preflight-as-login.json" 'd["clean"] is True'
}

rf4verify() {  # ST-27: runbook section 12 steps 2-5
  local d
  d=$(rf4_dir rf4verify) || { bad "rf4verify: no new evidence directory under $RF4"; return; }
  info "ST-27: evidence directory in the container: $d"
  info "ST-27: step 4 runs as the OS user cse-verifier with DATABASE_URL=$VERIFIER_URL (peer authentication, no password)"
  preflight_clean "$d" "step 2/3 (migration role)"
  preflight_clean_as_login "$d" "step 2/3 (second route)"
  run_json "$d" verify-as-login login_verify
  [[ $RC -eq 0 ]] && pass "step 4/5: verify as the login exits 0" || bad "step 4/5: verify as the login exits $RC"
  jassert "step 5: \"clean\": true" "$d/verify-as-login.json" 'd["clean"] is True'
  jassert "step 5: \"scope\": {\"complete\": true, ...} and no limit" "$d/verify-as-login.json" \
    'd["scope"]["complete"] is True and d["scope"]["limit"] is None'
  jassert "step 5: no problem reported" "$d/verify-as-login.json" 'd["problems"] == []'
  jassert "step 5: the scan read data (extractions > 0, runs > 0, every run rebuilt): not a vacuous pass" "$d/verify-as-login.json" \
    'd["extractions"] > 0 and d["runs"] > 0 and d["rebuilt"] == d["runs"]'
}

reader_step1() {  # section 20.6 step 1: connect through the URL as cse-verifier; current_user = session_user = the login; no SET ROLE
  local out
  out=$(login_psql -c "select current_user, session_user, current_setting('role'), pg_has_role(current_user, 'cse_reader', 'USAGE'), inet_client_addr() is null" 2>&1)
  info "step 1 (current_user | session_user | role setting | USAGE on cse_reader | Unix socket): $out"
  [[ "$out" == 'cse_verifier|cse_verifier|none|t|t' ]] \
    && pass "step 1: current_user = session_user = cse_verifier, no SET ROLE (role setting none), a member of cse_reader with USAGE, over the Unix socket" \
    || bad "step 1: unexpected session: [$out]"
}

reader_peer_controls() {  # what makes it peer authentication with no password (superuser and other OS users: controls, not steps)
  [[ "$(psu "select rolpassword is null from pg_authid where rolname = 'cse_verifier'")" == t ]] \
    && pass "peer authentication: cse_verifier has no password (pg_authid.rolpassword is null)" || bad "cse_verifier has a password"
  refused_with "peer authentication: the worker's OS user cannot connect as cse_verifier" 'Peer authentication failed' \
    runuser -u cse-worker -- env PGPASSFILE=/nonexistent/pgpass psql -XAt -U cse_verifier -d cse -c "select 1"
  refused_with "peer authentication: the login's OS user cannot connect as cse_worker" 'Peer authentication failed' \
    runuser -u cse-verifier -- env PGPASSFILE=/nonexistent/pgpass psql -XAt -U cse_worker -d cse -c "select 1"
  refused_with "the URL must name the role (without it libpq asks for the role cse-verifier, the OS user)" 'Peer authentication failed' \
    runuser -u cse-verifier -- env PGPASSFILE=/nonexistent/pgpass psql -XAt -d cse -c "select 1"
}

reader_step2() {  # section 20.6 step 2: pg_roles attributes, CREATE and TEMPORARY, pg_db_role_setting; read by the login itself
  local out want
  want="login=true inherit=true superuser=false createrole=false createdb=false replication=false bypassrls=false database_create=false database_temporary=false role_settings=0"
  out=$(login_psql -c "select format('login=%s inherit=%s superuser=%s createrole=%s createdb=%s replication=%s bypassrls=%s database_create=%s database_temporary=%s role_settings=%s',
      r.rolcanlogin::text, r.rolinherit::text, r.rolsuper::text, r.rolcreaterole::text, r.rolcreatedb::text, r.rolreplication::text,
      r.rolbypassrls::text, has_database_privilege(current_user, current_database(), 'CREATE')::text,
      has_database_privilege(current_user, current_database(), 'TEMPORARY')::text,
      (select count(*) from pg_db_role_setting s where s.setrole = r.oid)::text) from pg_roles r where r.rolname = current_user" 2>&1)
  info "step 2 (pg_roles, has_database_privilege, pg_db_role_setting): $out"
  [[ "$out" == "$want" ]] && pass "step 2: LOGIN and INHERIT, none of the five attributes, no CREATE or TEMPORARY, no pg_db_role_setting row" \
    || bad "step 2: [$out] differs from [$want]"
  info "step 2 (memberships, read by the login): $(login_psql -c "select coalesce(string_agg(g.rolname || ' inherit=' || m.inherit_option || ' set=' || m.set_option || ' admin=' || m.admin_option, '; '), 'none') from pg_auth_members m join pg_roles g on g.oid = m.roleid where m.member = (select oid from pg_roles where rolname = current_user)" 2>&1)"
}

reader_step4() {  # section 20.6 step 4: the full verify as the login; a repeatable-read read-only snapshot; counts equal a superuser's
  local d=$1 sc mine
  run_json "$d" step4-verify-as-login login_verify
  [[ $RC -eq 0 ]] && pass "step 4: verify as the login exits 0" || bad "step 4: verify as the login exits $RC"
  jassert "step 4: \"clean\": true" "$d/step4-verify-as-login.json" 'd["clean"] is True'
  jassert "step 4: \"scope\": {\"complete\": true, ...} and no limit" "$d/step4-verify-as-login.json" 'd["scope"]["complete"] is True and d["scope"]["limit"] is None'
  jassert "step 4: the snapshot is repeatable read and read-only" "$d/step4-verify-as-login.json" \
    'd["snapshot"]["isolation"] == "repeatable read" and d["snapshot"]["read_only"] is True'
  sc=$(super_counts)
  mine=$(login_psql -c "select (select count(*) from financial_f4_extractions) || ' ' || (select count(*) from financial_extraction_runs)" 2>&1)
  info "step 4: counts (extractions runs): the superuser reads [$sc], the login reads [$mine]"
  [[ "$sc" =~ ^[0-9]+\ [0-9]+$ && "$sc" == "$mine" ]] && pass "step 4: the login sees the same table counts as the superuser" \
    || bad "step 4: the login's table counts [$mine] differ from the superuser's [$sc]"
  jassert "step 4: verify's own counts equal the superuser's ($sc) and are not zero" "$d/step4-verify-as-login.json" \
    "[str(d['extractions']), str(d['runs'])] == '$sc'.split() and d['extractions'] > 0 and d['runs'] > 0"
}

reader_step5() {  # section 20.6 step 5: an INSERT into one R-F4 table as the login is refused (42501), in a transaction rolled back
  local before after out sql rc
  read -r -d '' sql <<'SQL'
begin;
do $$ begin
  begin
    insert into public.financial_f4_extractions default values;
    raise exception 'INSERT NOT REFUSED' using errcode = 'XX000';
  exception when insufficient_privilege then
    raise notice 'refused: sqlstate=% message=%', sqlstate, sqlerrm;
  end;
end $$;
rollback;
SQL
  before=$(super_counts)
  out=$(login_psql -c "$sql" 2>&1); rc=$?
  info "step 5 (INSERT INTO financial_f4_extractions as the login, inside begin ... rollback; exit $rc): $out"
  [[ $rc -eq 0 && "$out" == *"refused: sqlstate=42501 message=permission denied for table financial_f4_extractions"* ]] \
    && pass "step 5: the INSERT is refused with insufficient privilege (42501), inside a transaction that is rolled back" \
    || bad "step 5: the INSERT was not refused with insufficient privilege (exit $rc)"
  after=$(super_counts)
  [[ "$before" == "$after" ]] && pass "step 5: nothing changed ($after)" || bad "step 5: counts changed from [$before] to [$after]"
}

reader() {  # ST-28: REG-A06 and R-F4 section 20.6 steps 1-5
  local d
  d=$(rf4_dir reader) || { bad "reader: no new evidence directory under $RF4"; return; }
  info "ST-28: evidence directory in the container: $d"
  info "ST-28: every step 1, 2, 4 and 5 session is the OS user cse-verifier, DATABASE_URL=$VERIFIER_URL (peer, no password, no SET ROLE)"
  reader_step1
  reader_peer_controls
  reader_step2
  preflight_clean "$d" "step 3"
  reader_step4 "$d"
  reader_step5
  info "ST-28: step 6 (steps 3-4 on a restored copy) needs a restore: not run (R-F4 section 20.6; ST-30/ST-31)"
}

regx03() {  # ST-29: P1's verify_server (REG-X03, P1-CC-1) through the frozen probe that tolerates only the container clock
  local d rc out erc npass
  d=$(rf4_dir regx03) || { bad "regx03: no new evidence directory under $RF4"; return; }
  info "ST-29: evidence directory in the container: $d"
  rm -f /tmp/p1_verify_db.json /tmp/p1_verify_os.json
  bash "$APP/ops/tests/p3_container_probes.sh" p1-verify > "$d/p1-verify-probe.txt" 2>&1; rc=$?
  cp /tmp/p1_verify_db.json /tmp/p1_verify_os.json "$d/" 2>/dev/null
  sed 's/^/    | /' "$d/p1-verify-probe.txt"
  [[ $rc -eq 0 ]] && pass "P1's verify_server through the frozen probe: no FAIL except the container clock" || bad "the p1-verify probe exits $rc"
  out=$(python3 - "$d/p1_verify_db.json" <<'PY'
import json, sys
REQUIRED = ("role_cse_verifier", "verifier_membership_reader_only", "verifier_no_database_create_or_temporary",
            "verifier_no_role_settings", "owner_members_designed_only", "ledger_writable_only_by_owner",
            "ledger_ordinary_table", "ledger_permanent", "ledger_no_row_level_security", "ledger_no_inheritance")
checks = json.load(open(sys.argv[1]))["checks"]
by = {c["check"]: c for c in checks}
for c in checks:
    print("INFO verify_server %s: %s" % (c["check"], c["status"]))
for name in REQUIRED:
    c = by.get(name)
    print("PASS" if c and c["status"] == "PASS" else "FAIL", "verify_server %s: %s" % (name, c["detail"] if c else "absent from the report"))
PY
  )
  erc=$?                       # the evaluator's own exit status: a crash after its INFO lines must not look like a pass
  echo "$out"
  grep -q '^FAIL' <<< "$out" && fail=1
  npass=$(grep -c '^PASS verify_server ' <<< "$out")
  # fail closed (AR-0003-1 F-1): ST-29 passes only if the evaluator exited 0 AND all ten required checks are PASS lines
  if [[ $erc -ne 0 ]]; then bad "regx03: the evaluator exited $erc, so the ten required checks are not established"
  elif [[ $npass -ne 10 ]]; then bad "regx03: $npass of the 10 required checks are reported PASS"
  else pass "regx03: the evaluator exited 0 and all 10 required checks are PASS"; fi
}

cmd=${1:-}
case "$cmd" in
  ledger|roles|preflights|units|filesystem|invariants|metrics|seqkinds|rf4verify|reader|regx03) "$cmd" ;;
  isolation) shift; isolation "$@" ;;
  fingerprint) fingerprint "${2:-run}" ;;
  fpcompare) fpcompare "${2:?snapshot directory A}" "${3:?snapshot directory B}" ;;
  datadigest|dataverify) "$cmd" "${2:?a label}" ;;
  *) echo "usage: $0 isolation [CAP...]|ledger|roles|preflights|units|filesystem|invariants|fingerprint [L]|fpcompare A B|metrics|seqkinds|datadigest L|dataverify L|rf4verify|reader|regx03" >&2; exit 2 ;;
esac
exit $fail
