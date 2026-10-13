#!/usr/bin/env bash
# Independent probes of a provisioned P1 + HB-6 server, run INSIDE the systemd test container by
# ops/tests/provision_backfill_in_docker.sh after it was disconnected from every network. Read-mostly: the only writes
# are systemctl-started wake-ups (disarmed), refused worker attempts and an idempotent re-run of the HB-6
# provisioning. Never arms the backfill; nothing can reach CSE (no network).
set -uo pipefail

fail=0
probe()  { if eval "$2" >/dev/null 2>&1; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
nprobe() { if eval "$2" >/dev/null 2>&1; then echo "FAIL $1"; fail=1; else echo "PASS $1"; fi; }
q()  { runuser -u postgres -- psql -XAt -d cse -c "$1"; }
wq() { runuser -u cse-worker -- psql -XAt -v ON_ERROR_STOP=1 -U cse_worker -d cse -c "$1"; }
W="runuser -u cse-worker -- env PYTHONPATH=/opt/cse/app CSE_DB_NAME=cse CSE_DB_USER=cse_worker CSE_BACKUP_ROOT=/srv/cse-backup CSE_BACKFILL_TEMP_ROOT=/tmp/cse-backfill CSE_BACKFILL_REPORT_DIR=/var/lib/cse-backfill/reports python3 -m worker.backfill_ops"
U=/etc/systemd/system
RUNNER="tool_version = 'hb.ops.1'"

# wait for the timer's FIRST real firing (OnBootSec=5min, counted from the container's systemd start) and for that
# wake-up to finish - up to 8 minutes; nothing below starts the runner before it
fired() { [ "$(systemctl show -p LastTriggerUSec --value cse-backfill.timer)" != "n/a" ] &&
          [ "$(q "select count(*) from backfill_wakeups where $RUNNER and state <> 'active'")" -ge 1 ]; }
for _ in $(seq 1 480); do fired && break; sleep 1; done
echo "timer: last trigger $(systemctl show -p LastTriggerUSec --value cse-backfill.timer)"

nprobe "offline: no TCP route out"                          "timeout 5 bash -c 'echo > /dev/tcp/1.1.1.1/443'"
nprobe "offline: www.cse.lk does not resolve"               "timeout 5 getent hosts www.cse.lk"
probe  "units pass systemd-analyze verify"                  "systemd-analyze verify $U/cse-backfill.service $U/cse-backfill.timer"
probe  "backfill timer enabled and active"                  "systemctl is-enabled cse-backfill.timer && systemctl is-active cse-backfill.timer"
# a pipe into `grep -q` is never used under pipefail: grep exits at the first match, a longer writer then dies of SIGPIPE
# (141) and the probe would fail although the text is there; `grep -c` reads all of its input
probe  "backfill timer listed with a next elapse"           "[ \"\$(systemctl list-timers --all --no-pager | grep -c cse-backfill.timer)\" -ge 1 ]"
probe  "the timer fired by itself and woke the runner"      "fired"
probe  "service runs as cse-worker, never root"             "[ \"\$(systemctl show -p User --value cse-backfill.service)\" = cse-worker ]"
probe  "systemctl start of a wake-up succeeds"              "systemctl start cse-backfill.service && [ \"\$(systemctl show -p Result --value cse-backfill.service)\" = success ]"
probe  "every runner wake-up released, none refused"        "[ \"\$(q \"select count(*) from backfill_wakeups where $RUNNER and not (state = 'released' and result in ('idle', 'worked'))\")\" = 0 ] && [ \"\$(q \"select count(*) from backfill_wakeups where $RUNNER\")\" -ge 2 ]"
probe  "no arming, no lease, no CSE request, no response"   "[ \"\$(q 'select (select count(*) from backfill_arming_decisions) + (select count(*) from backfill_leases) + (select count(*) from backfill_request_attempts) + (select count(*) from backfill_response_bodies)')\" = 0 ]"
probe  "journal: disarmed, no Phase 2 CSE request"          "[ \"\$(journalctl -u cse-backfill.service --no-pager | grep -c 'disarmed: no Phase 2 CSE request')\" -ge 1 ]"
probe  "status (empty, disarmed) as the worker exits 0 or 2" "(cd / && $W status); st=\$?; [ \$st -eq 0 ] || [ \$st -eq 2 ]"
probe  "plan (no CSE contact) as the worker exits 0"        "(cd / && $W plan)"
probe  "HB-6 preflight and F6.4 verify (verify) pass"       "(cd / && $W verify)"
probe  "state + report directories cse-worker 0750"         "[ \"\$(stat -c '%U:%G %a' /var/lib/cse-backfill /var/lib/cse-backfill/reports | sort -u)\" = 'cse-worker:cse-worker 750' ]"
probe  "capture.env has no contact e-mail (none invented)"  "grep -qx 'CSE_CAPTURE_CONTACT_EMAIL=' /etc/cse/capture.env"
nprobe "the worker cannot arm (owner path only)"            "(cd / && $W arm --stages HB-S2 --window-first 2021-04-01 --window-last 2026-09-30 --user-agent x --host \$(hostname) --g1-reference worker-attempt --note worker-attempts-to-arm --confirm-stop-conditions)"
nprobe "the worker cannot insert an arming directly"        "wq \"insert into backfill_arming_decisions (armed, armed_stages, note, os_user) values (false, '{}', 'worker insert attempt', 'x')\""
nprobe "the worker cannot delete wake-up history"           "wq 'delete from backfill_wakeups'"
nprobe "the worker cannot truncate work items"              "wq 'truncate backfill_work_items cascade'"
probe  "migration ledger records 0016 and 0017"             "[ \"\$(q \"select count(*) from ops.schema_migrations where filename in ('0016_historical_backfill_ledger.sql', '0017_f8_asof_configuration.sql')\")\" = 2 ]"
probe  "HB-6 provisioning is idempotent"                    "CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision_backfill.sh all"
probe  "still no CSE request"                               "[ \"\$(q 'select count(*) from backfill_request_attempts')\" = 0 ]"
echo
systemctl list-timers --all --no-pager | grep -E 'cse-(backfill|backup)' || true
q "select id, state, trigger_kind, result, tool_version from backfill_wakeups order by id"
exit $fail
