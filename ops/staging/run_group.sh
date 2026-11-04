#!/usr/bin/env bash
# Runner entry point (docs/ops/STAGING.md, ST-24/ST-25): .github/ci/linux_container.sh's job without a bind mount. The
# driver copies the tree under test to /home/tester/app (docker cp) before the container starts, with no network; the
# group runs as `tester` through CI's own, unmodified .github/ci/linux_tests.sh. PYTEST_ADDOPTS is passed through
# (the driver passes it empty: nothing is deselected, so the tests that execute a restore into a disposable cluster run
# here, in this no-network container; owner, 2026-11-04).
#   cse-staging-run-group <group>          a CI group, results in /home/tester/out/<group>
#   cse-staging-run-group harvest          the synthetic F4 word layers for the seed, /home/tester/out/layouts.pkl
set -euo pipefail
group=$1
mkdir -p /home/tester/out
find /home/tester/app -name __pycache__ -prune -exec rm -rf {} +
chown -R tester:tester /home/tester/app /home/tester/out
if [ "$group" = harvest ]; then
    exec runuser -u tester -- env PYTHONDONTWRITEBYTECODE=1 python3 /home/tester/app/ops/staging/inner/harvest_layouts.py \
        /home/tester/app /home/tester/out/layouts.pkl
fi
out=/home/tester/out/$group
runuser -u tester -- mkdir -p "$out"
exec runuser -u tester -- env GIT_SHA="${GIT_SHA:-}" GIT_HEAD_SHA="${GIT_HEAD_SHA:-}" \
    PYTEST_ADDOPTS="${PYTEST_ADDOPTS:-}" bash /home/tester/app/.github/ci/linux_tests.sh "$group" "$out"
