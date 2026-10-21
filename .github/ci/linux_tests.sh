#!/usr/bin/env bash
# One CI test group in the Linux container, as the unprivileged user `tester`, with no network
# (docs/CI_TEST_STRATEGY.md). This is the harness of the earlier local evidence (docs/R_F4_IMPLEMENTATION.md
# section 5.2), made tracked:
#   - a scratch PostgreSQL 17 cluster on 127.0.0.1:5433 (initdb: trust, UTF-8, data checksums) for the suites gated
#     by F1/F3/F5_TEST_DATABASE_URL: F1's database `f1scratch` with migrations 0001-0004 and their documented worker
#     grants for role f1w; F3 and F5 get the cluster's superuser, since they create their own databases and roles;
#   - P1_PG_BINDIR for the suites that create their own disposable clusters (each in its own temporary directory, on a
#     free port, removed by the suite);
#   - for group X1 only, DATABASE_URL to `f1scratch` as the cluster's superuser (the view test it runs reads
#     daily_completeness, which the documented worker grants do not cover; it rolls everything back);
#   - for group X2 only, RF4_MUTANTS=1: R-F4's planted-fault tests (tests/test_rf4_mutants.py), which plant each fault
#     in a copy of the checkout and run the R-F4 suites against it in child pytest processes, here, with no network;
#   - pytest on the group's tests (.github/ci/groups.py), with a JUnit report.
# Group "offline" sets none of the database variables: every file runs, and the PostgreSQL and database-URL suites
# skip by their gates.
# Before any test: check_no_network.py proves the container has no network (loopback only, no route); and
# check_cse_unreachable.py that CSE's names do not resolve.
# Writes to <out>: environment.txt, provision.log (cluster set-up and teardown), pytest.log, junit.xml, exit_code.
# Exit: pytest's own code (0 passed, 1 failed, 2-5 pytest could not complete); or the harness's own, which pytest never
# uses: 10 unknown group or broken partition, 11 provisioning failed, 12 a network is present or CSE resolves.
set -uo pipefail
group=$1 out=$2
cd "$(dirname "$0")/../.."
B=/usr/lib/postgresql/17/bin
note() { echo "[ci $(date -u +%FT%TZ)] $*" | tee -a "$out/provision.log"; }
stop() { note "$2"; echo "$1" > "$out/exit_code"; exit "$1"; }

{
    echo "commit (github.sha): ${GIT_SHA:-unknown}"
    echo "commit (head):       ${GIT_HEAD_SHA:-unknown}"
    echo "group: $group"
    echo "date: $(date -u +%FT%TZ)"
    echo "os: $(. /etc/os-release && echo "$PRETTY_NAME")"
    python3 .github/ci/env_report.py
    echo "postgres: $("$B/postgres" --version)"
    echo "restic: $(restic version 2>/dev/null | head -1)"
    echo "pdftotext: $(command -v pdftotext >/dev/null && pdftotext -v 2>&1 | head -1 || echo absent)"
    echo "poppler-utils package: $(dpkg-query -W -f='${Version}' poppler-utils 2>/dev/null || echo absent)"
    echo "workspace: $PWD"
} > "$out/environment.txt" 2>&1

echo "network:" >> "$out/environment.txt"
python3 .github/ci/check_no_network.py 2>&1 | sed 's/^/  /' >> "$out/environment.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || stop 12 "the container has a network: refusing to run"
echo "cse reachability:" >> "$out/environment.txt"
python3 .github/ci/check_cse_unreachable.py 2>&1 | sed 's/^/  /' >> "$out/environment.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || stop 12 "CSE resolves from the container: refusing to run"

tests=$(python3 .github/ci/groups.py "$group") || stop 10 "unknown group or broken partition"
echo "tests:" >> "$out/environment.txt"
echo "$tests" | sed 's/^/  /' >> "$out/environment.txt"

if [ "$group" != offline ]; then
    D=$(mktemp -d)
    teardown() {
        note "teardown: stopping the scratch cluster"
        "$B/pg_ctl" -D "$D/data" -m fast -w stop >> "$out/provision.log" 2>&1 || note "teardown: pg_ctl stop failed"
        cp "$D/log" "$out/scratch-cluster-server.log" 2>/dev/null || true
        rm -rf "$D"
    }
    trap teardown EXIT
    me=$(id -un)
    P=("$B/psql" -h 127.0.0.1 -p 5433 -U "$me" -Xq -v ON_ERROR_STOP=1)
    note "initdb"
    "$B/initdb" -D "$D/data" -U "$me" -A trust --data-checksums -E UTF8 --locale=C.UTF-8 >> "$out/provision.log" 2>&1 \
        || stop 11 "initdb failed"
    note "start (127.0.0.1:5433)"
    "$B/pg_ctl" -D "$D/data" -l "$D/log" -o "-c listen_addresses=127.0.0.1 -p 5433 -k $D" -w start \
        >> "$out/provision.log" 2>&1 || stop 11 "start failed"
    note "F1: database f1scratch, role f1w, migrations 0001-0004, their documented worker grants"
    (
        "${P[@]}" -d postgres -c "create database f1scratch" -c "create role f1w login" \
        && for m in 0001 0002 0003 0004; do "${P[@]}" -d f1scratch -f supabase/migrations/${m}_*.sql || exit 1; done \
        && grep -hE "^--\s+grant\s" supabase/migrations/0001_*.sql supabase/migrations/0004_*.sql \
           | sed -E "s/^--\s+//; s/cse_worker/f1w/g" | "${P[@]}" -d f1scratch
    ) >> "$out/provision.log" 2>&1 || stop 11 "F1 set-up failed"
    export P1_PG_BINDIR=$B
    export F1_TEST_DATABASE_URL="postgresql://f1w@127.0.0.1:5433/f1scratch"
    export F3_TEST_DATABASE_URL="postgresql://$me@127.0.0.1:5433/postgres"
    export F5_TEST_DATABASE_URL="postgresql://$me@127.0.0.1:5433/postgres"
    if [ "$group" = X1 ]; then
        export DATABASE_URL="postgresql://$me@127.0.0.1:5433/f1scratch"
        note "X1: DATABASE_URL=$DATABASE_URL"
    fi
    if [ "$group" = X2 ]; then
        export RF4_MUTANTS=1
        note "X2: RF4_MUTANTS=1 (R-F4's planted-fault tests)"
    fi
    note "provisioned; P1_PG_BINDIR=$P1_PG_BINDIR"
fi

export PYTHONDONTWRITEBYTECODE=1
# shellcheck disable=SC2086
python3 -m pytest -p no:cacheprovider -q -rfEs --junitxml="$out/junit.xml" -o junit_suite_name="$group" $tests \
    2>&1 | tee "$out/pytest.log"
rc=${PIPESTATUS[0]}
echo "$rc" > "$out/exit_code"
exit "$rc"
