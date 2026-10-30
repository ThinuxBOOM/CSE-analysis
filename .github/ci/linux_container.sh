#!/usr/bin/env bash
# Entry point inside the Linux CI container (docs/CI_TEST_STRATEGY.md), run as root by the workflow:
#   docker run --rm --network none -v "$PWD:/src:ro" -v "$OUT:/out" cse-ci-linux bash /src/.github/ci/linux_container.sh <group> /out
# The checkout is mounted read-only and copied for the unprivileged user `tester`, who runs the group (initdb refuses
# root); the network is absent for the whole run, so no test can reach CSE or anything else.
set -euo pipefail
group=$1 out=$2
cp -a /src /home/tester/app
find /home/tester/app -name __pycache__ -prune -exec rm -rf {} +
chown -R tester:tester /home/tester/app
exec runuser -u tester -- env GIT_SHA="${GIT_SHA:-}" GIT_HEAD_SHA="${GIT_HEAD_SHA:-}" \
    bash /home/tester/app/.github/ci/linux_tests.sh "$group" "$out"
