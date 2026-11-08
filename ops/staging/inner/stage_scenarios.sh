#!/usr/bin/env bash
# Behaviour scenarios inside the staging host container (docs/ops/STAGING.md), as root, with NO network. Each one uses
# the frozen units and wrappers as they are; the only test-specific changes are configuration values, each restored
# before the scenario ends and each named in its output. Every scenario keeps the environment disarmed, never runs or
# enables a restore, and never resolves a host name.
#
#   wakeups          ST-12  one disarmed P3 and one disarmed HB-6 wake-up through their units
#   concurrency      ST-14  a second runner while the first is held: P3 and HB-6 each record 'busy'
#   migrate-lock     ST-14  a pending migration waits for the runner's advisory lock (upgrade container only)
#   backups          ST-20  dump -> verify -> off-site sync -> off-site check -> status (no restore)
#   offsite-failure  ST-21  an unavailable off-site repository (configuration) -> 'failed', redacted
#   midrun-outage    ST-21  PostgreSQL stopped during a dump -> 'unrecorded' -> the next dump closes it as failed
#   outage           ST-16  PostgreSQL stopped: the runners exit 4; a dump is not a success; recovery
#   disk-gate        ST-19  the database-disk path on a 64 MiB tmpfs (configuration) -> refused (5)
#   crash-prepare    ST-15  a dump and an HB-6 wake-up frozen mid-run; the driver then kills the container
#   crash-verify     ST-15  after the restart: recovery, the orphans, and their closure
#   age-dump-stamp   ST-13  the dump timer's stamp aged 3 days before a restart (Persistent catch-up)
#   restart-verify   ST-13  after the restart: what ran, and that the restore check did not
#   tamper           ST-09  an edited, a missing and a renamed applied migration file: verify and apply refuse, the
#                           ledger is unchanged; each change undone before the next. The upgrade host only (to-0017
#                           marks it), after its evidence: it changes the application tree of a throwaway container
set -uo pipefail
APP=/opt/cse/app
fail=0
pass() { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }
info() { echo "INFO $*"; }
psu() { runuser -u postgres -- psql -XAt -d cse -v ON_ERROR_STOP=1 -c "$1"; }
wait_for() {  # wait_for SECONDS SQL  -> prints the first non-null result. Polled server-side every 2 ms by a session-local
              # pg_temp function: the claims and 'running' rows of small synthetic runs live too briefly for client polling
  local r
  r=$(psu "create function pg_temp.stg_wait(q text, secs int) returns text language plpgsql as \$f\$
             declare r text; t0 timestamptz := clock_timestamp();
             begin loop
               execute q into r;
               if r is not null or clock_timestamp() - t0 > make_interval(secs => secs) then return r; end if;
               perform pg_sleep(0.002);
             end loop; end \$f\$;
           select pg_temp.stg_wait(\$q\$$2\$q\$, $1);" 2>/dev/null | tail -n1)  # tail: psql also prints the CREATE FUNCTION tag
  [[ -n "$r" ]] && echo "$r"
}
last_wakeup() { psu "select id || ' ' || state || ' ' || trigger_kind || ' ' || coalesce(result, '-') from $1 order by id desc limit 1"; }
# an HB-6 runner that reached the database: exit 0, or exit 2 whose only attention is the pending F6.4 designation (after
# the synthetic seed every wake-up reports it; difference D-11). Prints the attention kinds.
runner_ok() {  # runner_ok RC OUTPUT-FILE
  local att
  att=$(python3 -c "import json,sys;print(','.join(a[0] for a in json.load(open(sys.argv[1])).get('attention') or []))" "$2" 2>/dev/null)
  echo "${att:-none}"
  [[ $1 -eq 0 || ($1 -eq 2 && "$att" == designation) ]]
}
last_pg_start() { grep -E "database system was " /var/log/postgresql/postgresql-17-main.log | tail -n 1; }
# unit_done UNIT INVOCATION SECONDS -> "result=<Result> status=<ExecMainStatus>" once that invocation has ended (H-1).
# ActiveState, never `systemctl is-active`: a oneshot unit is 'activating' while its process runs, is-active is already
# false then, and Result still holds the previous run's value. Exit 1: still running at the deadline; 2: the invocation
# was replaced by another (its result is then unknown).
unit_done() {
  local u=$1 inv=$2 end=$((SECONDS + $3)) k v now state result status
  while :; do
    now="" state="" result="" status=""
    while IFS='=' read -r k v; do
      case $k in InvocationID) now=$v ;; ActiveState) state=$v ;; Result) result=$v ;; ExecMainStatus) status=$v ;; esac
    done < <(systemctl show -p InvocationID -p ActiveState -p Result -p ExecMainStatus "$u")
    [[ -n $inv && $now == "$inv" ]] || { echo "invocation ${inv:-unknown} replaced by ${now:-none}"; return 2; }
    [[ $state == inactive || $state == failed ]] && { echo "result=$result status=$status"; return 0; }
    ((SECONDS < end)) || { echo "still $state after $3 s"; return 1; }
    sleep 1
  done
}
# what systemd itself logged about one invocation (it tags every message about a unit run with INVOCATION_ID)
unit_journal() { journalctl --no-pager -o cat INVOCATION_ID="$1" | grep -E 'Main process exited|Deactivated successfully|Failed with result' | tr '\n' ' '; }
# The off-site password exactly as P1 reads it (worker/ops/offsite.py: text mode, then strip()), so neither a CR nor a
# trailing newline in the file can become part of the pattern (H-2). Written only into a pipe for grep -f: never a
# file, an argument or a log line. Exit 1 when fewer than 4 characters remain (P1's redactor ignores those too).
secret_pattern() { python3 -c 'import sys; s = open(sys.argv[1], encoding="utf-8").read().strip(); len(s) >= 4 or sys.exit(1); sys.stdout.buffer.write(s.encode() + b"\n")' "$1"; }
# leak_scan CREDENTIAL TARGET...: 0 the secret occurs in a target, 1 it occurs in none, 2 the scan could not be made
leak_scan() {
  local cred=$1 rc; shift
  secret_pattern "$cred" > /dev/null || return 2
  grep -rqF -f <(secret_pattern "$cred") "$@" 2>/dev/null; rc=$?
  ((rc <= 1)) && return $rc
  return 2
}

wakeups() {
  local u t
  for u in cse-capture-scheduler:market_schedule_wakeups cse-backfill:backfill_wakeups; do
    t=${u#*:}; u=${u%%:*}
    systemctl start "$u.service"; local rc=$?
    info "$u.service: start exit $rc, Result=$(systemctl show -p Result --value "$u.service"), ExecMainStatus=$(systemctl show -p ExecMainStatus --value "$u.service")"
    info "$t last: $(last_wakeup "$t")"
    [[ "$(systemctl show -p Result --value "$u.service")" == success ]] && pass "$u: a disarmed wake-up through its hardened unit succeeded" \
      || bad "$u: the wake-up unit did not succeed"
  done
  journalctl -u cse-capture-scheduler.service -u cse-backfill.service -n 40 --no-pager | tail -n 20 | sed 's/^/      /'
}

held_second_runner() {  # held_second_runner WRAPPER TABLE
  local wrapper=$1 table=$2 first second rc pid row
  local o=/var/lib/cse-staging/concurrency-$wrapper
  bash "$APP/ops/bin/$wrapper" run >"$o-first.out" 2>&1 & first=$!
  row=$(wait_for 30 "select id || ' ' || pid from $table where state = 'active' order by id desc limit 1") \
    || { bad "$wrapper: the first runner's claim never appeared"; wait "$first"; return; }
  pid=${row#* }
  # runuser (util-linux) stops itself when its child stops, to relay job control: it must be resumed too, or `wait` on
  # the wrapper never returns (the cause of the earlier hangs of this scenario)
  local ppid
  ppid=$(awk '/^PPid:/ {print $2}' /proc/"$pid"/status)
  kill -STOP "$pid"
  info "$wrapper: first runner holds claim ${row%% *} (pid $pid; after SIGSTOP its state is: $(awk '/^State:/ {print $2, $3}' /proc/"$pid"/status 2>&1); its command: $(tr '\0' ' ' < /proc/"$pid"/cmdline 2>&1 | cut -c1-80))"
  # The second runner is bounded (20 s): if it waits instead of returning, what it waits on is recorded, the first runner
  # is resumed, and both outcomes are reported. A frozen process may hold an open transaction, which a real runner does
  # only briefly, so a wait here is reported as an observation, not hidden.
  bash "$APP/ops/bin/$wrapper" run >"$o-second.out" 2>&1 & second=$!
  local waited=0
  for _ in $(seq 1 200); do kill -0 "$second" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$second" 2>/dev/null; then
    waited=1
    info "$wrapper: the second runner was still waiting after 20 s; its sessions: $(psu "select string_agg(a.pid || ' ' || coalesce(a.wait_event_type, '-') || '/' || coalesce(a.wait_event, '-') || ' ' || left(regexp_replace(a.query, '\s+', ' ', 'g'), 120), ' | ') from pg_stat_activity a where a.datname = 'cse' and a.usename = 'cse_worker' and a.state <> 'idle'")"
    info "$wrapper: ungranted locks: $(psu "select string_agg(l.locktype || ':' || l.mode || ' pid ' || l.pid || ' blocked by ' || array_to_string(pg_blocking_pids(l.pid), ','), ' | ') from pg_locks l where not l.granted")"
  fi
  info "$wrapper: before resuming, the parent ($ppid, $(tr '\0' ' ' < /proc/"$ppid"/cmdline 2>/dev/null | cut -c1-40)) is in state $(awk '/^State:/ {print $2}' /proc/"$ppid"/status 2>&1)"
  kill -CONT "$pid" "$ppid" 2>/dev/null
  wait "$second"; rc=$?
  wait "$first"
  local second_row
  second_row=$(psu "select id || ' ' || state || ' ' || coalesce(result, '-') from $table where id > ${row%% *} order by id limit 1")
  info "$wrapper second runner: exit $rc; its wake-up: ${second_row:-none}; it waited for the first: $([[ $waited == 1 ]] && echo yes || echo no)"
  if [[ $waited == 0 && $rc -eq 0 && "$second_row" == *" skipped busy" ]]; then
    pass "$wrapper: the second runner recorded 'busy' while the first held the claim, and did nothing (exit 0)"
  elif [[ $rc -eq 0 && "$second_row" == *" skipped busy" ]]; then
    pass "$wrapper: once the first resumed, the second recorded 'busy' (exit 0); it had waited on the frozen first runner (see INFO)"
  else
    bad "$wrapper: second runner exit $rc, wake-up ${second_row:-none}"
  fi
  info "$wrapper first runner after resume: $(psu "select state || ' ' || coalesce(result, '-') || ' started ' || started_at || ' finished ' || coalesce(finished_at::text, '-') from $table where id = ${row%% *}")"
  info "$wrapper second runner output (tail): $(tail -c 300 "$o-second.out" | tr '\n' ' ')"
}

concurrency() {
  held_second_runner cse-backfill backfill_wakeups
  held_second_runner cse-scheduler market_schedule_wakeups
}

migrate_lock() {
  local key=4346836117002311 holder applier
  runuser -u postgres -- psql -XAtq -d cse -c "select pg_advisory_lock($key); select pg_sleep(20);" >/dev/null 2>&1 & holder=$!
  sleep 2
  bash "$APP/ops/bin/cse-ops" migrate apply >/tmp/apply.out 2>&1 & applier=$!
  sleep 5
  local waiting
  waiting=$(psu "select count(*) from pg_locks l join pg_stat_activity a on a.pid = l.pid where l.locktype = 'advisory' and not l.granted and a.usename = 'cse_migrator'")
  [[ "$waiting" -ge 1 ]] && pass "a pending migration waits for the runner's advisory lock held by another session" \
    || bad "the migration runner did not wait for the lock (waiting=$waiting)"
  wait "$holder"; wait "$applier"; local rc=$?
  info "apply after the lock was released: exit $rc: $(tail -n 3 /tmp/apply.out | tr '\n' ' ')"
  [[ "$(psu "select count(*) from ops.schema_migrations where version = 18")" == 1 ]] && pass "0018 recorded exactly once" \
    || bad "0018 ledger rows: $(psu "select count(*) from ops.schema_migrations where version = 18")"
}

backups() {
  local u
  for u in cse-backup-dump cse-backup-offsite cse-backup-offsite-check; do
    systemctl start "$u.service"
    info "$u.service: Result=$(systemctl show -p Result --value "$u.service") ExecMainStatus=$(systemctl show -p ExecMainStatus --value "$u.service")"
    [[ "$(systemctl show -p Result --value "$u.service")" == success ]] && pass "$u through its unit" || bad "$u failed"
    if [[ $u == cse-backup-dump ]]; then
      bash "$APP/ops/bin/cse-ops" backup verify --all >/tmp/verify.out 2>&1 && pass "every local dump verifies" \
        || { bad "backup verify --all"; sed 's/^/      /' /tmp/verify.out | tail -5; }
    fi
  done
  psu "select id, run_kind, status, coalesce(error, '') from ops.backup_runs order by id" | sed 's/^/      /'
  bash "$APP/ops/bin/cse-ops" backup status --verify >/tmp/status.out 2>&1; local rc=$?
  info "backup status --verify: exit $rc (alerts expected while no restore check has run: DO NOT EXECUTE RESTORE)"
  sed 's/^/      /' /tmp/status.out | head -40
  [[ "$(psu "select count(*) from ops.backup_runs where run_kind = 'offsite_sync' and status = 'succeeded'")" -ge 1 ]] \
    && pass "an off-site sync recorded succeeded" || bad "no successful off-site sync"
}

offsite_failure() {
  local f=/etc/cse/credentials/offsite-repository orig
  orig=$(cat "$f")
  printf '%s\n' /srv/cse-backup/offsite-test/absent-repository > "$f"
  info "configuration: offsite-repository -> an absent path (restored afterwards)"
  systemctl start cse-backup-offsite.service
  printf '%s\n' "$orig" > "$f"
  local row
  row=$(psu "select status || ' | ' || coalesce(error, '') from ops.backup_runs where run_kind = 'offsite_sync' order by id desc limit 1")
  info "last off-site run: $row"
  [[ "$row" == failed* ]] && pass "an unavailable off-site repository is recorded failed, never success" || bad "off-site run: $row"
  # The controls come first: a clean scan means something only if the pattern finds the stored secret and an LF-free
  # copy of it inside a line, and stays silent on a file without it
  local cred=/etc/cse/credentials/offsite-password lrc
  leak_scan "$cred" "$cred"; lrc=$?
  ((lrc == 0)) && pass "control: the normalized pattern matches the stored credential" || bad "control: the stored credential is not matched (scan $lrc)"
  leak_scan "$cred" <(secret_pattern "$cred" | awk '{printf "{\"error\": \"x %s y\"}", $0}'); lrc=$?
  ((lrc == 0)) && pass "control: an LF-free copy inside a JSON line is detected" || bad "control: an LF-free copy is not detected (scan $lrc)"
  leak_scan "$cred" /etc/hostname; lrc=$?
  ((lrc == 1)) && pass "control: a file without the secret is clean" || bad "control: the negative control gave scan $lrc"
  psu "select coalesce(error, '') || ' ' || details::text from ops.backup_runs" > /tmp/ledger-text.out
  leak_scan "$cred" /srv/cse-backup/status /tmp/ledger-text.out; lrc=$?
  case $lrc in
    0) bad "the off-site password appears in a status file or ledger row" ;;
    1) pass "the off-site password appears in no status file or ledger row" ;;
    *) bad "the leak scan of the status files and ledger rows could not be made (scan $lrc)" ;;
  esac
  systemctl start cse-backup-offsite.service
  [[ "$(psu "select status from ops.backup_runs where run_kind = 'offsite_sync' order by id desc limit 1")" == succeeded ]] \
    && pass "off-site sync succeeds again once the repository is back" || bad "off-site sync did not recover"
}

midrun_outage() {
  systemctl start --no-block cse-backup-dump.service
  local row pid inv ended drc jr
  row=$(wait_for 30 "select id from ops.backup_runs where run_kind = 'local_dump' and status = 'running' order by id desc limit 1") \
    || { bad "the dump's running row never appeared"; return; }
  inv=$(systemctl show -p InvocationID --value cse-backup-dump.service)
  # the whole unit cgroup is frozen, not only MainPID (python): its pg_dump child would otherwise finish on its own
  pid=$(systemctl show -p MainPID --value cse-backup-dump.service)
  systemctl kill -s SIGSTOP cse-backup-dump.service; info "dump run $row frozen (invocation $inv, MainPID $pid and its children); stopping PostgreSQL"
  systemctl stop postgresql@17-main
  systemctl kill -s SIGCONT cse-backup-dump.service
  ended=$(unit_done cse-backup-dump.service "$inv" 120); drc=$?
  jr=$(unit_journal "$inv")
  info "the interrupted dump's invocation $inv: $ended; systemd's journal for it: ${jr:-none}"
  info "status file: $(python3 -c "import json;d=json.load(open('/srv/cse-backup/status/local_dump.json'));print(d.get('status'), d.get('outcome'), d.get('id'))" 2>&1)"
  # P1 section 6: a run whose terminal status cannot be committed is never reported as succeeded, exit code included
  if ((drc == 0)) && [[ $ended != "result=success "* && $ended != *" status=0" && $jr == *"Failed with result"* ]]; then
    pass "the interrupted dump failed: its completed invocation and systemd's journal agree it exited non-zero"
  else bad "the interrupted dump's invocation: $ended (journal: ${jr:-none})"; fi
  systemctl start postgresql@17-main
  info "row $row after PostgreSQL returned: $(psu "select status from ops.backup_runs where id = $row")"
  systemctl start cse-backup-dump.service
  local closed
  closed=$(psu "select status || ' | ' || coalesce(error, '') from ops.backup_runs where id = $row")
  info "row $row after the next dump: $closed"
  [[ "$closed" == failed* ]] && pass "the 'unrecorded' run was closed as failed by the next dump, never success" || bad "row $row: $closed"
  [[ "$(systemctl show -p Result --value cse-backup-dump.service)" == success ]] && pass "the next dump succeeded" || bad "the next dump failed"
}

outage() {
  systemctl stop postgresql@17-main
  local rc
  bash "$APP/ops/bin/cse-backfill" run >/tmp/o1.out 2>&1; rc=$?
  [[ $rc -eq 4 ]] && pass "HB-6 runner exits 4 when PostgreSQL is unavailable" || bad "HB-6 runner exit $rc"
  bash "$APP/ops/bin/cse-scheduler" run >/tmp/o2.out 2>&1; rc=$?
  [[ $rc -eq 4 ]] && pass "P3 scheduler exits 4 when PostgreSQL is unavailable" || bad "P3 scheduler exit $rc"
  systemctl start cse-backup-dump.service; rc=$?
  info "dump with PostgreSQL down: start exit $rc, Result=$(systemctl show -p Result --value cse-backup-dump.service)"
  info "status file: $(python3 -c "import json;d=json.load(open('/srv/cse-backup/status/local_dump.json'));print(d.get('status'), d.get('outcome'))" 2>&1)"
  [[ "$(python3 -c "import json;print(json.load(open('/srv/cse-backup/status/local_dump.json')).get('status'))" 2>/dev/null)" != succeeded ]] \
    && pass "a dump without a database is never reported succeeded" || bad "a dump without a database reported succeeded"
  systemctl start postgresql@17-main
  bash "$APP/ops/bin/cse-backfill" run >/tmp/o3.out 2>&1; rc=$?
  local att ok=0
  att=$(runner_ok "$rc" /tmp/o3.out) || ok=1
  info "HB-6 runner after recovery: exit $rc, wake-up $(last_wakeup backfill_wakeups), attention: $att"
  ((ok == 0)) && pass "HB-6 runner recovers once PostgreSQL is back" || bad "HB-6 runner after recovery: exit $rc (attention: $att)"
}

disk_gate() {
  local f=/etc/cse/backfill.env orig
  orig=$(grep '^CSE_BACKFILL_DB_DISK_PATH=' "$f")
  sed -i 's#^CSE_BACKFILL_DB_DISK_PATH=.*#CSE_BACKFILL_DB_DISK_PATH=/srv/cse-staging-smalldisk#' "$f"
  info "configuration: CSE_BACKFILL_DB_DISK_PATH -> a 64 MiB tmpfs (restored afterwards); df: $(df -h /srv/cse-staging-smalldisk | tail -1)"
  systemctl start cse-backfill.service
  local st res
  st=$(systemctl show -p ExecMainStatus --value cse-backfill.service)
  res=$(psu "select coalesce(result, '-') || ' ' || details::text from backfill_wakeups order by id desc limit 1")
  sed -i "s#^CSE_BACKFILL_DB_DISK_PATH=.*#${orig}#" "$f"
  info "wake-up exit $st; last wake-up: ${res:0:400}"
  [[ "$st" == 5 && "$res" == *db_disk* ]] && pass "under 20 GiB free on the database disk the wake-up is refused (5) before any work" \
    || bad "disk gate: exit $st"
  grep -q "^${orig}$" "$f" && pass "configuration restored: $orig" || bad "configuration not restored"
}

crash_prepare() {
  systemctl start --no-block cse-backup-dump.service
  local d b
  d=$(wait_for 30 "select id from ops.backup_runs where run_kind = 'local_dump' and status = 'running' order by id desc limit 1") \
    || { bad "no running dump row"; return; }
  systemctl kill -s SIGSTOP cse-backup-dump.service  # the whole cgroup: python and its pg_dump child
  systemctl start --no-block cse-backfill.service
  b=$(wait_for 30 "select id || ' ' || pid from backfill_wakeups where state = 'active' order by id desc limit 1") \
    || { bad "no active HB-6 claim"; return; }
  kill -STOP "${b#* }"
  [[ "$(psu "select state from backfill_wakeups where id = ${b%% *}")" == active ]] \
    || { bad "HB-6 claim ${b%% *} was released before the freeze took effect"; return; }
  echo "${d} ${b%% *}" > /var/lib/cse-staging/crash-ids
  pass "frozen mid-run: dump row $d, HB-6 claim ${b%% *}; ready for the container kill"
}

crash_verify() {
  local d b
  read -r d b < /var/lib/cse-staging/crash-ids
  [[ "$(psu "show data_checksums")" == on ]] && pass "PostgreSQL is back after the kill, data checksums on" || bad "PostgreSQL state"
  local st
  st=$(last_pg_start)
  info "PostgreSQL's last startup: $st; $(grep -E 'not properly shut down|redo (starts|done)' /var/log/postgresql/postgresql-17-main.log | tail -n 3 | tr '\n' ' ')"
  [[ "$st" == *"not properly shut down"* || "$st" == *interrupted* ]] && pass "PostgreSQL ran crash recovery after the kill" \
    || bad "PostgreSQL's last startup: $st"
  info "orphaned dump row $d: $(psu "select status || ' started ' || started_at from ops.backup_runs where id = $d")"
  systemctl start cse-backup-dump.service
  local s
  s=$(psu "select status from ops.backup_runs where id = $d")
  [[ "$s" == running ]] && pass "a new dump proceeds; the orphan younger than CSE_STALE_RUN_HOURS (6) stays 'running' (P1 section 6)" \
    || info "orphan after a new dump: $s"
  sed -i 's/^CSE_STALE_RUN_HOURS=.*/CSE_STALE_RUN_HOURS=0/' /etc/cse/backup.env
  info "configuration: CSE_STALE_RUN_HOURS=0 for one dump (restored afterwards)"
  systemctl start cse-backup-dump.service
  sed -i 's/^CSE_STALE_RUN_HOURS=.*/CSE_STALE_RUN_HOURS=6/' /etc/cse/backup.env
  s=$(psu "select status || ' | ' || coalesce(error, '') from ops.backup_runs where id = $d")
  info "orphan after a dump with the stale limit at 0: $s"
  [[ "$s" == failed*abandoned* ]] && pass "the next run of that kind past the stale limit closed the orphan as failed ('abandoned')" || bad "orphan: $s"
  grep -q '^CSE_STALE_RUN_HOURS=6$' /etc/cse/backup.env && pass "configuration restored: CSE_STALE_RUN_HOURS=6" || bad "stale-hours not restored"
  info "HB-6 claim $b before the next runner: $(psu "select state from backfill_wakeups where id = $b")"
  bash "$APP/ops/bin/cse-backfill" run >/tmp/c1.out 2>&1; local rc=$? att ok=0
  att=$(runner_ok "$rc" /tmp/c1.out) || ok=1
  s=$(psu "select state || ' expired_by=' || coalesce(expired_by::text, '-') from backfill_wakeups where id = $b")
  info "HB-6 claim $b after a manual runner (exit $rc, attention: $att): $s; that runner: $(last_wakeup backfill_wakeups)"
  [[ "$s" == expired* && $ok -eq 0 ]] && pass "the dead runner's claim was expired by the next runner, which proceeded" || bad "dead claim: $s (exit $rc)"
}

age_dump_stamp() {
  local s=/var/lib/systemd/timers/stamp-cse-backup-dump.timer
  touch "$s"; touch -d '3 days ago' "$s"
  info "stamp aged: $(stat -c '%y' "$s")"
  date -u +%s > /var/lib/cse-staging/restart-at
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

restart_verify() {
  local since st
  since=$(date -u -d "@$(cat /var/lib/cse-staging/restart-at)" '+%Y-%m-%d %H:%M:%S')
  st=$(last_pg_start)
  info "PostgreSQL's last startup: $st"
  [[ "$st" == *"was shut down at"* ]] && pass "clean shutdown: PostgreSQL started without crash recovery (ST-17)" || bad "PostgreSQL's last startup: $st"
  info "units started since the restart: $(journalctl --since "$since UTC" --no-pager -o cat | grep -oE 'Start(ing|ed) .*(cse-[a-z-]+\.service|CSE [^.]*)' | sort | uniq -c | tr '\n' ';')"
  [[ -n "$(journalctl -u cse-backup-dump.service --since "$since UTC" --no-pager -o cat | grep -E 'Started|Finished|Starting')" ]] \
    && pass "Persistent catch-up: the dump timer whose stamp was 3 days old ran after the restart" \
    || bad "no dump catch-up after the restart"
  if journalctl -u cse-backup-restore-check.service --since "$since UTC" --no-pager -o cat | grep -qE 'Started|Starting'; then
    info "restore-check service activity: $(journalctl -u cse-backup-restore-check.service --since "$since UTC" --no-pager -o cat | tr '\n' ' ')"
  fi
  restore_rows_ok "no restore check ran after the restart, except an authorized R1"
}

tamper() {
  [[ -e /var/lib/cse-staging/upgrade-host ]] || { bad "tamper runs on the upgrade host only, after its evidence"; return; }
  local dir=$APP/supabase/migrations f orig ledger0 c sub out rc
  f=$(cd "$dir" && ls 0017_*.sql)
  orig=/var/lib/cse-staging/tamper-original-$f
  [[ -e $orig ]] || cp -p "$dir/$f" "$orig"
  ledger0=$(psu "select count(*) || ' rows, max ' || max(version) || ', ' || md5(string_agg(version || filename || sha256, ',' order by version)) from ops.schema_migrations")
  info "applied file under test: $f; ledger: $ledger0"
  # a refusal counts only if verify passes before each change
  bash "$APP/ops/bin/cse-ops" migrate verify >/dev/null 2>&1 || { bad "'migrate verify' fails before any change"; return; }
  for c in edited missing renamed; do
    case $c in
      edited) printf '\n-- staging ST-09: an in-place edit of an applied migration\n' >> "$dir/$f" ;;
      missing) mv "$dir/$f" "$orig.moved" ;;
      renamed) mv "$dir/$f" "$dir/0017_renamed_by_staging.sql" ;;
    esac
    for sub in verify apply; do
      out=$(bash "$APP/ops/bin/cse-ops" migrate "$sub" 2>&1); rc=$?
      info "$c: migrate $sub exit $rc: $(grep -E 'hash mismatch|has no migration file|was applied as|nothing applied' <<< "$out" | head -2 | tr '\n' ' ' | cut -c1-300)"
      [[ $rc -ne 0 ]] && pass "$c applied migration file: 'migrate $sub' refuses (exit $rc)" || bad "$c applied migration file: 'migrate $sub' exit 0"
    done
    [[ "$(psu "select count(*) || ' rows, max ' || max(version) || ', ' || md5(string_agg(version || filename || sha256, ',' order by version)) from ops.schema_migrations")" == "$ledger0" ]] \
      && pass "$c: the ledger is unchanged" || bad "$c: the ledger changed"
    case $c in
      edited) cp -p "$orig" "$dir/$f" ;;
      missing) mv "$orig.moved" "$dir/$f" ;;
      renamed) mv "$dir/0017_renamed_by_staging.sql" "$dir/$f" ;;
    esac
    cmp -s "$orig" "$dir/$f" && bash "$APP/ops/bin/cse-ops" migrate verify >/dev/null 2>&1 \
      && pass "$c: undone; 'migrate verify' passes again" || bad "$c: not undone"
  done
}

cmd=${1:-}
case "$cmd" in
  wakeups) wakeups ;; concurrency) concurrency ;; migrate-lock) migrate_lock ;; backups) backups ;;
  offsite-failure) offsite_failure ;; midrun-outage) midrun_outage ;; outage) outage ;; disk-gate) disk_gate ;;
  crash-prepare) crash_prepare ;; crash-verify) crash_verify ;; age-dump-stamp) age_dump_stamp ;;
  restart-verify) restart_verify ;; tamper) tamper ;;
  *) echo "usage: $0 <scenario>" >&2; exit 2 ;;
esac
exit $fail
