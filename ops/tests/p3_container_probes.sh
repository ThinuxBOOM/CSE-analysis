#!/usr/bin/env bash
# Independent probes of a provisioned P1 + P3 server, run INSIDE the systemd test container by
# ops/tests/provision_p3_in_docker.sh after it was disconnected from every network. Read-mostly: the only writes are a
# systemctl-started wake-up (disarmed), refused worker attempts, a probe marker that starts P1's dump, and an
# idempotent re-run of the P3 provisioning. Never arms the scheduler; nothing can reach CSE (no network).
set -uo pipefail

# P1's verifier (ops/provision/provision.sh verify), run directly: every check must PASS or WARN, except
# time_synchronised - a container cannot be NTP-synchronised (the clock belongs to the Docker VM), so that one check is
# reported, never faked. On the real server `provision.sh verify` runs it for real.
p1_verify() {
  runuser -u postgres -- env PYTHONPATH=/opt/cse/app CSE_DB_NAME=cse CSE_BACKUP_ROOT=/srv/cse-backup \
    python3 -m worker.ops.verify_server --repo /opt/cse/app --skip-os > /tmp/p1_verify_db.json || true
  env PYTHONPATH=/opt/cse/app CSE_DB_NAME=cse CSE_BACKUP_ROOT=/srv/cse-backup python3 -m worker.ops.verify_server \
    --os-only --pgdata /var/lib/postgresql/17/main --allow-same-device > /tmp/p1_verify_os.json || true
  python3 - <<'PY'
import json, sys
checks = json.load(open("/tmp/p1_verify_db.json"))["checks"] + json.load(open("/tmp/p1_verify_os.json"))["checks"]
bad = [c for c in checks if c["status"] == "FAIL" and c["check"] != "time_synchronised"]
clock = [c["detail"] for c in checks if c["check"] == "time_synchronised"]
print(f"P1 verifier: {len(checks)} checks, {sum(c['status'] == 'PASS' for c in checks)} PASS, "
      f"{sum(c['status'] == 'WARN' for c in checks)} WARN, FAIL other than the container clock: {len(bad)} "
      f"(time_synchronised: {clock[0] if clock else 'n/a'})")
for c in bad:
    print("  FAIL", c["check"], c["detail"])
sys.exit(1 if bad else 0)
PY
}
if [[ "${1:-}" == "p1-verify" ]]; then p1_verify; exit $?; fi

fail=0
probe()  { if eval "$2" >/dev/null 2>&1; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
nprobe() { if eval "$2" >/dev/null 2>&1; then echo "FAIL $1"; fail=1; else echo "PASS $1"; fi; }
q()  { runuser -u postgres -- psql -XAt -d cse -c "$1"; }
wq() { runuser -u cse-worker -- psql -XAt -v ON_ERROR_STOP=1 -U cse_worker -d cse -c "$1"; }
W="runuser -u cse-worker -- env PYTHONPATH=/opt/cse/app CSE_DB_NAME=cse CSE_DB_USER=cse_worker CSE_BACKUP_ROOT=/srv/cse-backup python3 -m worker.scheduler"
U=/etc/systemd/system
dumps() { q "select count(*) from ops.backup_runs where run_kind = 'local_dump' and status = 'succeeded'"; }

# wait for the timer's FIRST real firing (OnBootSec=3min, counted from the container's systemd start) and for that
# wake-up to finish - up to 6 minutes; nothing below starts the scheduler before it
fired() { [ "$(systemctl show -p LastTriggerUSec --value cse-capture-scheduler.timer)" != "n/a" ] &&
          [ "$(q "select count(*) from market_schedule_wakeups where state <> 'active'")" -ge 1 ]; }
for _ in $(seq 1 360); do fired && break; sleep 1; done
echo "timer: last trigger $(systemctl show -p LastTriggerUSec --value cse-capture-scheduler.timer)"

nprobe "offline: no TCP route out"                          "timeout 5 bash -c 'echo > /dev/tcp/1.1.1.1/443'"
nprobe "offline: www.cse.lk does not resolve"               "timeout 5 getent hosts www.cse.lk"
probe  "units pass systemd-analyze verify"                  "systemd-analyze verify $U/cse-capture-scheduler.service $U/cse-capture-scheduler.timer $U/cse-capture-backup-trigger.path"
probe  "scheduler timer enabled and active"                 "systemctl is-enabled cse-capture-scheduler.timer && systemctl is-active cse-capture-scheduler.timer"
probe  "scheduler timer listed with a next elapse"          "systemctl list-timers --all --no-pager | grep -q cse-capture-scheduler.timer"
probe  "backup trigger path unit enabled and active"        "systemctl is-enabled cse-capture-backup-trigger.path && systemctl is-active cse-capture-backup-trigger.path"
probe  "the timer fired by itself and woke the scheduler"   "fired"
probe  "service runs as cse-worker, never root"             "[ \"\$(systemctl show -p User --value cse-capture-scheduler.service)\" = cse-worker ]"
probe  "systemctl start of a wake-up succeeds"              "systemctl start cse-capture-scheduler.service && [ \"\$(systemctl show -p Result --value cse-capture-scheduler.service)\" = success ]"
probe  "every wake-up released as disarmed"                 "[ \"\$(q \"select count(*) from market_schedule_wakeups where not (state = 'released' and result = 'disarmed')\")\" = 0 ] && [ \"\$(q 'select count(*) from market_schedule_wakeups')\" -ge 2 ]"
probe  "no items, no capture runs, no CSE responses"        "[ \"\$(q 'select (select count(*) from market_schedule_items) + (select count(*) from market_capture_runs) + (select count(*) from market_source_responses)')\" = 0 ]"
probe  "journal: disarmed, contacts nobody"                 "journalctl -u cse-capture-scheduler.service --no-pager | grep -q disarmed"
probe  "status (empty, disarmed) as the worker exits 0"     "(cd / && $W status --days 7)"
probe  "scheduler security preflight (verify) passes"       "(cd / && $W verify)"
probe  "state directory cse-worker 0750"                    "[ \"\$(stat -c '%U:%G %a' /var/lib/cse-scheduler)\" = 'cse-worker:cse-worker 750' ]"
probe  "capture.env has no contact e-mail (none invented)"  "grep -qx 'CSE_CAPTURE_CONTACT_EMAIL=' /etc/cse/capture.env"
nprobe "the worker cannot arm (owner path only)"            "(cd / && $W arm --start-date 2099-01-05 --user-agent x --host \$(hostname) --expected-requests 55-65 --note worker-attempts-to-arm --confirm-stop-conditions)"
nprobe "the worker cannot insert settings directly"         "wq \"insert into market_schedule_settings (armed, earliest_start_local, window_close_local, retry_base_minutes, retry_max_minutes, max_attempts, no_session_confirmations, daily_request_budget, max_catch_up_days, stale_lease_minutes, note) values (false, '15:15', '23:59:59', 20, 120, 4, 2, 150, 14, 30, 'worker insert attempt')\""
nprobe "the worker cannot delete scheduler history"         "wq 'delete from market_schedule_wakeups'"
nprobe "the worker cannot truncate work items"              "wq 'truncate market_schedule_items cascade'"
probe  "migration ledger records 0014"                      "[ \"\$(q \"select count(*) from ops.schema_migrations where filename = '0014_market_capture_scheduler.sql'\")\" = 1 ]"
probe  "P1 verifier still passes with 0014 (container clock excepted)" "p1_verify"
probe  "P1 restore check recorded as succeeded"             "[ \"\$(q \"select status from ops.backup_runs where run_kind = 'restore_check' order by id desc limit 1\")\" = succeeded ]"
before="$(dumps)"
runuser -u cse-worker -- bash -c 'echo probe > /var/lib/cse-scheduler/.cf && mv /var/lib/cse-scheduler/.cf /var/lib/cse-scheduler/capture-finished'
for _ in $(seq 1 120); do [ "$(dumps)" -gt "$before" ] && break; sleep 1; done
probe  "capture marker starts P1's dump asynchronously"     "[ \"\$(dumps)\" -gt $before ]"
probe  "P3 provisioning is idempotent"                      "CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision_scheduler.sh all"
probe  "still nothing archived from CSE"                    "[ \"\$(q 'select count(*) from market_source_responses')\" = 0 ]"
echo
systemctl list-timers --all --no-pager | grep -E 'cse-(capture|backup)' || true
q "select id, state, trigger_kind, result, heartbeat_at - started_at as held from market_schedule_wakeups order by id"
exit $fail
