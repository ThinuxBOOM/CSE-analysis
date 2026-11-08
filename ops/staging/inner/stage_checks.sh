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

invariants() {
  local n
  n=$(psu "select count(*) from ops.backup_runs where run_kind = 'restore_check'")
  [[ "$n" == 0 ]] && pass "no restore check ever ran (ops.backup_runs)" || bad "restore_check rows: $n"
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

cmd=${1:-}
case "$cmd" in
  ledger|roles|preflights|units|filesystem|invariants|metrics|seqkinds) "$cmd" ;;
  isolation) shift; isolation "$@" ;;
  fingerprint) fingerprint "${2:-run}" ;;
  fpcompare) fpcompare "${2:?snapshot directory A}" "${3:?snapshot directory B}" ;;
  datadigest|dataverify) "$cmd" "${2:?a label}" ;;
  *) echo "usage: $0 isolation [CAP...]|ledger|roles|preflights|units|filesystem|invariants|fingerprint [L]|fpcompare A B|metrics|seqkinds|datadigest L|dataverify L" >&2; exit 2 ;;
esac
exit $fail
