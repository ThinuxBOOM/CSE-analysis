#!/usr/bin/env bash
# Milestone 8 Docker staging driver (docs/ops/STAGING.md). Host side, Git Bash on the Windows workstation (or any bash
# with the docker CLI). Plain docker commands, as the existing ops/tests/*.sh scripts use; no Compose.
#
# Boundaries enforced here, not only documented:
#   - every staging container runs with --network none, --restart no, no host bind mount and no Docker socket; files
#     go in and out with `docker cp` only, and images are built from a context outside the repository;
#   - privilege rungs L1 and L2 only (CAP_SYS_ADMIN, private cgroup namespace). --privileged is not implemented;
#   - nothing here calls `provision.sh all` or `selftest`; the only restore is `restore` (restore_driver.sh), which runs
#     nothing without a valid, unused restore authorization of the owner (OD-12) and never targets production;
#   - nothing here arms, captures, backfills or contacts CSE; the only network use is `docker build`'s apt access;
#   - nothing here removes a container, volume, image or evidence file (the owner reviews them first).
#
#   staging.sh context              export the commit (git archive, LF) + this directory into the build context
#   staging.sh build [--no-cache]   build cse-staging:<rev12> (host) and cse-staging-runner:<rev12>; --no-cache: the
#                                   host image only, every layer rebuilt (ST-26)
#   staging.sh volumes [suffix]     create the campaign's named volumes (labelled)
#   staging.sh trial L1|L2 [caps]   a ladder trial container: boot systemd, probe the frozen units' sandboxing
#   staging.sh up L1|L2 [suffix] [caps]   the staging host container on the campaign volumes
#   staging.sh start [suffix]       start the stopped host container again (same flags and volumes; after stop, kill or
#                                   the RAM guard)
#   staging.sh stop [suffix]        clean shutdown: systemd's SIGRTMIN+3, the container's 400 s grace (ST-17)
#   staging.sh kill [suffix]        unclean stop: SIGKILL, no shutdown (ST-15)
#   staging.sh inject [suffix]      copy the inner scripts (and the seed) into the host container
#   staging.sh secret [suffix]      generate (once per campaign) and copy in the off-site test password (/run only)
#   staging.sh run [suffix] SCRIPT ARGS...   run an inner script in the host container, output to the evidence
#   staging.sh isolation [suffix] [L1|L2 caps]   ST-04: docker inspect against the approved rung, then the inside view
#   staging.sh seed [suffix] full|filings-f3 START COUNT   the synthetic seed (needs `runner harvest` first)
#   staging.sh runner GROUP [attempt]   one CI group (or `harvest`) in a fresh runner container, results to the
#                                   evidence; a repeat names an attempt (its own container and evidence)
#   staging.sh guard                one RAM and disk check (exit 1 below the floors)
#   staging.sh canary               scan the evidence, journals, inspects and image histories for the test secret
#   staging.sh collect [suffix]     journal export, docker inspect, image metadata into the evidence
#   staging.sh restore R1|R2 RA [suffix]   ST-30 / ST-31 under the owner's restore authorization RA (a file, read only):
#                                   restore_driver.sh, docs/ops/STAGING.md section 3, docs/ops/ISOLATED_RESTORE.md
#
# Provenance (H-4): every run, isolation and seed record opens with PROVENANCE lines (campaign, revision, container and
# image, the driver's SHA-256, and the SHA-256 of each harness file as the container holds it, compared with the working
# tree); every inject logs the injected files' SHA-256 to logs/driver.log; every runner writes runner/<run>.provenance.txt.
set -euo pipefail
# Container-side paths must reach docker literally (Git Bash would rewrite /opt/... into Windows paths); host paths are
# converted explicitly with winpath. Other tools (git, tar, python) keep Git Bash's normal conversion.
docker() { MSYS_NO_PATHCONV=1 command docker "$@"; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
REV="${CSE_STAGING_REV:-$(git -C "$REPO" rev-parse HEAD)}"
REV12="${REV:0:12}"
CAMPAIGN="${CSE_STAGING_CAMPAIGN:-t1s1}"
EVID="${CSE_STAGING_EVIDENCE:-/d/cse-staging/$CAMPAIGN}"
SECRETS="${CSE_STAGING_SECRETS:-$LOCALAPPDATA/cse-staging/$CAMPAIGN}"
BUILD="${CSE_STAGING_BUILD:-}"                 # a later build of the same revision: its own context, logs and tag
CTX="$EVID/build/context${BUILD:+-$BUILD}"
HOST_IMAGE="cse-staging:$REV12${BUILD:+-$BUILD}"
RUNNER_IMAGE="cse-staging-runner:$REV12${BUILD:+-$BUILD}"
RAM_FLOOR_PCT=5
DISK_FLOOR_GB=20
# No test is deselected (owner, 2026-11-04): the nine regression tests that execute a restore into a disposable cluster
# (tests/test_p1_postgres.py 2, tests/test_f64_postgres.py 1, tests/test_rf4_runbook_rehearsal.py 6) run inside the
# runner container, which has no network, in the container's own throwaway clusters. In the host container the R1 gate
# is unchanged: only `restore R1` under a valid, unused restore authorization opens it, for one run (restore_driver.sh).

winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi; }
note() { mkdir -p "$EVID/logs"; echo "[staging $(date -u +%FT%TZ)] $*" | tee -a "$EVID/logs/driver.log"; }
host_name() { echo "cse-stg-$CAMPAIGN${1:+-$1}-host"; }
vol() { echo "cse-stg-$CAMPAIGN${2:+-$2}-$1"; }

guard() {
  local pct gb
  pct=$(powershell.exe -NoProfile -Command '$o=Get-CimInstance Win32_OperatingSystem; [math]::Round(100*$o.FreePhysicalMemory/$o.TotalVisibleMemorySize,1)' | tr -d '\r')
  gb=$(powershell.exe -NoProfile -Command '[math]::Round((Get-PSDrive D).Free/1GB,1)' | tr -d '\r')
  note "guard: host RAM free ${pct}% (floor ${RAM_FLOOR_PCT}%), D: free ${gb} GB (floor ${DISK_FLOOR_GB} GB)"
  awk -v p="$pct" -v f="$RAM_FLOOR_PCT" -v g="$gb" -v d="$DISK_FLOOR_GB" 'BEGIN { exit !(p >= f && g >= d) }'
}

rung_flags() {  # rung_flags L1|L2 [extra caps, comma-separated, L2 only]
  local f=(--network none --restart no --cgroupns private --cap-add SYS_ADMIN --tmpfs /run --tmpfs /run/lock
           --tmpfs /srv/cse-staging-smalldisk:size=64m --memory 2g --memory-swap 2g --pids-limit 4096 --stop-timeout 400
           --label "cse.staging.campaign=$CAMPAIGN" --label "cse.staging.revision=$REV")
  case "$1" in
    L1) [[ -z "${2:-}" ]] || { echo "L1 takes no extra capabilities" >&2; exit 2; } ;;
    L2) [[ -n "${2:-}" ]] || { echo "L2 needs the diagnosed capabilities" >&2; exit 2; }
        local c; IFS=, read -ra caps <<< "$2"; for c in "${caps[@]}"; do f+=(--cap-add "$c"); done ;;
    *) echo "rungs: L1 or L2 (L3 / --privileged needs the owner's explicit approval and is not implemented)" >&2; exit 2 ;;
  esac
  printf '%s\n' "${f[@]}"
}

wait_systemd() {  # wait_systemd CONTAINER -> prints the state, 0 when running/degraded
  local st=unknown
  for _ in $(seq 1 90); do
    st=$(docker exec "$1" systemctl is-system-running 2>/dev/null || true)
    [[ "$st" == running || "$st" == degraded ]] && { echo "$st"; return 0; }
    [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == true ]] || { echo "container-exited"; return 1; }
    sleep 1
  done
  echo "$st"; return 1
}

sandbox_probe() {  # the sandboxing directives the frozen CSE units use, on a transient unit
  docker exec "$1" systemd-run --wait --pipe --collect -p User=nobody -p PrivateTmp=yes -p ProtectSystem=strict \
    -p ProtectHome=yes -p PrivateDevices=yes -p ProtectKernelTunables=yes -p ProtectKernelModules=yes \
    -p ProtectKernelLogs=yes -p ProtectControlGroups=yes -p ProtectClock=yes -p ProtectHostname=yes \
    -p RestrictSUIDSGID=yes -p RestrictRealtime=yes -p RestrictNamespaces=yes -p LockPersonality=yes \
    -p SystemCallArchitectures=native -p "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" -p CapabilityBoundingSet= \
    -p NoNewPrivileges=yes -p UMask=0027 -p Nice=10 -p IOSchedulingClass=idle -p StateDirectory=cse-staging-probe \
    /bin/sh -c 'test -w "$STATE_DIRECTORY" && touch /tmp/probe && echo sandbox-ok'
}

cmd_context() {
  [[ ! -e "$CTX" ]] || { echo "build context exists: $CTX (not overwritten)" >&2; exit 2; }
  mkdir -p "$CTX/app" "$EVID/build"
  # app/ only feeds the base stage's copy of provision.sh; the tree itself enters the image as app.tar (git's modes)
  git -C "$REPO" -c core.autocrlf=false archive --format=tar "$REV" | tar -x -C "$CTX/app"
  python "$HERE/tree_tar.py" "$REPO" "$REV" "$CTX/app.tar"
  for f in Dockerfile init.sh run_group.sh; do tr -d '\r' < "$HERE/$f" > "$CTX/$f"; done
  git -C "$REPO" ls-tree -r --full-tree "$REV" > "$EVID/build/ls-tree.txt"
  note "context: $REV exported ($(wc -l < "$EVID/build/ls-tree.txt") tree entries; app.tar $(sha256sum "$CTX/app.tar" | cut -c1-16)…) to $CTX"
}

cmd_build() {  # --no-cache (ST-26): the host image only, every layer rebuilt; the runner is test tooling, not compared
  local tag="${BUILD:-first}" nocache=() images=("$HOST_IMAGE" "$RUNNER_IMAGE")
  [[ "${1:-}" == --no-cache ]] && nocache=(--no-cache) && images=("$HOST_IMAGE")
  guard
  note "build $HOST_IMAGE from $CTX ${nocache[*]} (network: apt only, during the build)"
  docker build --progress=plain "${nocache[@]}" --target host --build-arg "REVISION=$REV" -t "$HOST_IMAGE" "$(winpath "$CTX")" \
    > "$EVID/build/$tag-host-build.log" 2>&1
  if [[ ${#nocache[@]} -eq 0 ]]; then
    note "build $RUNNER_IMAGE"
    docker build --progress=plain --target runner --build-arg "REVISION=$REV" -t "$RUNNER_IMAGE" "$(winpath "$CTX")" \
      > "$EVID/build/$tag-runner-build.log" 2>&1
  fi
  docker image inspect "${images[@]}" > "$EVID/build/$tag-images-inspect.json"
  docker image ls --digests --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep -E '^cse-staging' \
    | tee "$EVID/build/$tag-images.txt"
}

cmd_volumes() {
  local v
  for v in pgdata backup offsite; do
    docker volume create --label "cse.staging.campaign=$CAMPAIGN" --label "cse.staging.revision=$REV" "$(vol $v "${1:-}")" >/dev/null
  done
  docker volume ls --filter "label=cse.staging.campaign=$CAMPAIGN" | tee -a "$EVID/logs/volumes.txt"
}

cmd_trial() {
  local rung=$1 caps=${2:-} name="cse-stg-$CAMPAIGN-trial-${1,,}${2:+-x}"
  guard
  mapfile -t flags < <(rung_flags "$rung" "$caps")
  note "ladder trial $rung (${caps:-no extra capabilities}): $name"
  docker run -d --name "$name" --hostname cse-staging "${flags[@]}" "$HOST_IMAGE" > /dev/null
  mkdir -p "$EVID/ladder"
  local st rc=0
  st=$(wait_systemd "$name") || rc=1
  note "$name: systemd state $st"
  { echo "rung: $rung"; echo "extra capabilities: ${caps:-none}"; echo "systemd: $st"
    docker inspect -f 'privileged={{.HostConfig.Privileged}} capadd={{.HostConfig.CapAdd}} cgroupns={{.HostConfig.CgroupnsMode}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} mounts={{len .Mounts}}' "$name"
  } > "$EVID/ladder/$name.txt"
  docker logs "$name" > "$EVID/ladder/$name.console.log" 2>&1 || true
  if [[ $rc -eq 0 ]]; then
    if sandbox_probe "$name" >> "$EVID/ladder/$name.txt" 2>&1; then echo "sandbox probe: PASS" >> "$EVID/ladder/$name.txt"
    else echo "sandbox probe: FAIL" >> "$EVID/ladder/$name.txt"; rc=1; fi
    docker exec "$name" systemctl --failed --no-legend --plain >> "$EVID/ladder/$name.txt" 2>&1 || true
    docker exec "$name" journalctl -b --no-pager -p warning >> "$EVID/ladder/$name.journal-warnings.log" 2>&1 || true
    docker stop "$name" > /dev/null
  fi
  cat "$EVID/ladder/$name.txt"
  return $rc
}

cmd_up() {
  local rung=$1 suffix=${2:-} caps=${3:-} name
  name=$(host_name "$suffix")
  guard
  mapfile -t flags < <(rung_flags "$rung" "$caps")
  note "host container $name at rung $rung (${caps:-no extra capabilities})"
  docker run -d --name "$name" --hostname cse-staging "${flags[@]}" \
    --mount "type=volume,src=$(vol pgdata "$suffix"),dst=/var/lib/postgresql" \
    --mount "type=volume,src=$(vol backup "$suffix"),dst=/srv/cse-backup" \
    --mount "type=volume,src=$(vol offsite "$suffix"),dst=/srv/cse-backup/offsite-test" \
    "$HOST_IMAGE" > /dev/null
  note "$name: systemd $(wait_systemd "$name")"
}

cmd_start() {
  local name
  name=$(host_name "${1:-}")
  guard
  docker start "$name" > /dev/null
  note "$name: started again; systemd $(wait_systemd "$name")"
}

cmd_stop() {
  local name t0=$SECONDS
  name=$(host_name "${1:-}")
  docker stop "$name" > /dev/null
  note "$name: stopped in $((SECONDS - t0)) s, exit code $(docker inspect -f '{{.State.ExitCode}}' "$name")"
}

cmd_kill() {
  local name
  name=$(host_name "${1:-}")
  docker kill "$name" > /dev/null
  note "$name: killed (SIGKILL), exit code $(docker inspect -f '{{.State.ExitCode}}' "$name")"
}

cmd_inject() {
  local name tmp
  name=$(host_name "${1:-}")
  tmp=$(mktemp -d)
  mkdir -p "$tmp/cse-staging"
  for f in "$HERE"/inner/*.sh "$HERE"/inner/stage_tree.py "$HERE"/seed_synthetic.py; do
    tr -d '\r' < "$f" > "$tmp/cse-staging/$(basename "$f")"
  done
  docker exec "$name" mkdir -p /opt/cse-staging /var/lib/cse-staging
  docker cp "$(winpath "$tmp/cse-staging")/." "$name:/opt/cse-staging/"
  [[ ! -s "$EVID/build/ls-tree.txt" ]] || docker cp "$(winpath "$EVID/build/ls-tree.txt")" "$name:/var/lib/cse-staging/ls-tree.txt"
  docker exec "$name" bash -c 'chown -R root:root /opt/cse-staging && chmod 0755 /opt/cse-staging/*.sh && chmod 0644 /opt/cse-staging/*.py'
  rm -rf "$tmp"
  note "inner scripts copied into $name:/opt/cse-staging"
  local h f
  docker exec "$name" sh -c 'cd /opt/cse-staging && sha256sum -- *' | while read -r h f; do note "inject: $name $f sha256=$h"; done
}

sha_of() { tr -d '\r' < "$1" | sha256sum | cut -c1-64; }  # a harness file as injected (LF), whatever the checkout's EOLs

provenance() {  # provenance CONTAINER -> PROVENANCE lines: what ran where, from exactly which harness files (H-4)
  local name=$1 h f src
  echo "PROVENANCE utc=$(date -u +%FT%TZ) campaign=$CAMPAIGN revision=$REV container=$name image=$(docker inspect -f '{{.Image}}' "$name" 2>/dev/null || echo unknown)"
  echo "PROVENANCE harness checkout: HEAD $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown), $(git -C "$REPO" status --porcelain -- ops/staging 2>/dev/null | wc -l) uncommitted path(s) under ops/staging"
  echo "PROVENANCE staging.sh sha256=$(sha_of "$HERE/staging.sh") (the driver)"
  # the copies the container actually runs, each compared with the working-tree file it was injected from
  docker exec "$name" sh -c 'cd /opt/cse-staging && sha256sum -- *' 2>/dev/null | while read -r h f; do
    src="$HERE/inner/$f"; [[ -f $src ]] || src="$HERE/$f"
    echo "PROVENANCE $f sha256=$h working-tree=$([[ -f $src && "$(sha_of "$src")" == "$h" ]] && echo same || echo DIFFERENT)"
  done
}

cmd_secret() {
  local name
  name=$(host_name "${1:-}")
  mkdir -p "$SECRETS"
  if [[ ! -s "$SECRETS/offsite-password" ]]; then
    # tr: Windows Python's print writes CRLF, and the CR would become part of the password and of the canary's pattern
    python -c "import secrets; print(secrets.token_urlsafe(48))" | tr -d '\r' > "$SECRETS/offsite-password"
    note "generated the campaign's off-site test password in the secrets directory (never in the evidence)"
  fi
  # standard input, not `docker cp` (which cannot write into the /run tmpfs) and never an argument or variable
  docker exec -i "$name" sh -c 'umask 077 && install -d -m 0700 /run/cse-staging && cat > /run/cse-staging/offsite-password' \
    < "$SECRETS/offsite-password"
  note "off-site test password written into $name:/run/cse-staging (tmpfs) through standard input"
}

new_log() {  # new_log NAME -> a results path that does not exist yet (evidence is never overwritten)
  local log="$EVID/results/$1.txt" n=2
  mkdir -p "$EVID/results"
  while [[ -e "$log" ]]; do log="$EVID/results/$1.run$n.txt"; n=$((n + 1)); done
  echo "$log"
}

RUN_SCRIPTS=" stage_checks.sh stage_provision.sh stage_scenarios.sh stage_tree.py "   # `run` executes these inner scripts only

r1_crosscheck() {  # r1_crosscheck CONTAINER: every restore_check row and binding against the host's fixed RA records (G-1)
  local name=$1 tmp rc
  tmp=$(mktemp -d)
  docker exec "$name" runuser -u postgres -- psql -XAt -d cse --set=ON_ERROR_STOP=1 \
    -c "select id from ops.backup_runs where run_kind = 'restore_check' order by id" > "$tmp/rows" 2> "$tmp/rows.err" \
    || { echo "FAIL r1-crosscheck: the restore_check rows could not be read: $(head -c 300 "$tmp/rows.err")"; rm -rf "$tmp"; return 1; }
  docker exec "$name" sh -c 'cat /var/lib/cse-staging/r1-authorized-runs 2>/dev/null || true' > "$tmp/bindings"
  python "$HERE/restore_gate.py" r1-crosscheck --campaign "$CAMPAIGN" --rows-file "$(winpath "$tmp/rows")" \
    --bindings-file "$(winpath "$tmp/bindings")"
  rc=$?
  rm -rf "$tmp"
  return $rc
}

cmd_run() {
  local suffix=$1 script=$2; shift 2
  local name tag log
  # an allowlisted name, never a path: no `/`, no `..`, no space, so no other file in the container can be reached
  # (AR-0006-1 N-3; AR-0006-2 N-2)
  [[ $script != */* && $script != *..* && $script != *" "* && "$RUN_SCRIPTS" == *" $script "* ]] \
    || { echo "run: '$script' is not one of the inner scripts:$RUN_SCRIPTS" >&2; exit 2; }
  name=$(host_name "$suffix")
  tag="${script%.*}"; [[ -z "${1:-}" || "$1" == */* ]] || tag+="-$1"
  log=$(new_log "${suffix:-main}-$tag")
  note "run $script $* in $name -> $log"
  local interp=bash; [[ "$script" == *.py ]] && interp=python3
  provenance "$name" > "$log"
  set +e
  docker exec "$name" "$interp" "/opt/cse-staging/$script" "$@" >> "$log" 2>&1
  local rc=$?
  # the R1 invariant's host-side half (AR-0006-2 G-1): wherever the container evaluates it, the harness cross-checks
  # the container's bindings and restore_check rows against the host's own records, which the container cannot write
  if [[ ($script == stage_checks.sh && ${1:-} == invariants) || ($script == stage_scenarios.sh && ${1:-} == restart-verify) ]]; then
    r1_crosscheck "$name" >> "$log" 2>&1 || rc=1
  fi
  set -e
  echo "exit $rc" >> "$log"
  note "$script $*: exit $rc ($(grep -c '^PASS' "$log") PASS, $(grep -c '^FAIL' "$log") FAIL)"
  return $rc
}

cmd_isolation() {  # ST-04 (H-3): what only docker inspect shows, against the approved rung; then the inside view
  local suffix=${1:-} rung=${2:-L1} caps=${3:-} name log rc extra=(SYS_ADMIN) more
  case "$rung" in
    L1) [[ -z "$caps" ]] || { echo "L1 takes no extra capabilities" >&2; exit 2; } ;;
    L2) [[ -n "$caps" ]] || { echo "L2: name the capabilities the container was started with" >&2; exit 2; }
        IFS=, read -ra more <<< "$caps"; extra+=("${more[@]}") ;;
    *) echo "isolation [suffix] L1|L2 [caps]" >&2; exit 2 ;;
  esac
  name=$(host_name "$suffix")
  log=$(new_log "${suffix:-main}-isolation-inspect")
  note "isolation: docker inspect $name against rung $rung -> $log"
  provenance "$name" > "$log"
  set +e
  docker inspect "$name" | python "$HERE/isolation_check.py" --campaign "$CAMPAIGN" --suffix "$suffix" --cap-add "${extra[@]}" >> "$log" 2>&1
  rc=$?
  set -e
  echo "exit $rc" >> "$log"
  note "isolation (inspect): exit $rc ($(grep -c '^PASS' "$log") PASS, $(grep -c '^FAIL' "$log") FAIL)"
  cmd_run "$suffix" stage_checks.sh isolation "${extra[@]}" || rc=1
  return $rc
}

cmd_seed() {  # the harvested layouts into the host, then seed_synthetic.py as cse-worker
  local suffix=$1 mode=$2 start=$3 count=$4 name log
  [[ $mode =~ ^(full|filings-f3)$ && $start =~ ^[0-9]+$ && $count =~ ^[0-9]+$ ]] || { echo "seed [suffix] full|filings-f3 START COUNT" >&2; exit 2; }
  [[ -s "$EVID/runner/harvest/layouts.pkl" ]] || { echo "no harvested layouts: run 'runner harvest' first" >&2; exit 2; }
  name=$(host_name "$suffix")
  docker cp "$(winpath "$EVID/runner/harvest/layouts.pkl")" "$name:/var/lib/cse-staging/layouts.pkl"
  log=$(new_log "${suffix:-main}-seed-$mode")
  note "seed $mode ($count synthetic filings from $start) in $name -> $log"
  local cmd="chmod 0644 /var/lib/cse-staging/layouts.pkl && cd / && runuser -u cse-worker -- env PYTHONDONTWRITEBYTECODE=1 python3 /opt/cse-staging/seed_synthetic.py --layouts /var/lib/cse-staging/layouts.pkl --mode $mode --start-fid $start --count $count"
  { provenance "$name"   # H-6: what was seeded, how, as whom, from which layouts; no credential is involved
    echo "PROVENANCE seed: mode=$mode start-fid=$start count=$count identity=cse-worker (runuser; the frozen stores as cse_worker)"
    echo "PROVENANCE seed: layouts.pkl sha256=$(sha256sum < "$EVID/runner/harvest/layouts.pkl" | cut -c1-64) (runner/harvest/layouts.pkl), in the container sha256=$(docker exec "$name" sha256sum /var/lib/cse-staging/layouts.pkl 2>/dev/null | cut -c1-64)"
    echo "PROVENANCE seed: command: docker exec $name bash -c \"$cmd\""
  } > "$log"
  set +e
  docker exec "$name" bash -c "$cmd" >> "$log" 2>&1
  local rc=$?
  set -e
  echo "PROVENANCE seed: finished utc=$(date -u +%FT%TZ) exit=$rc" >> "$log"
  echo "exit $rc" >> "$log"
  note "seed $mode: exit $rc"
  return $rc
}

cmd_runner() {  # the tree under test: the commit, overlaid with the working tree's changed and new files (LF, git modes)
  # a repeated run (e.g. after the RAM guard stopped one) names an attempt: its own container and evidence, the earlier
  # ones kept
  local group=$1 run="$1${2:+-$2}" tmp name
  name="cse-stg-$CAMPAIGN-runner-$run"
  guard
  mkdir -p "$EVID/runner"
  tmp=$(mktemp -d)
  python "$HERE/tree_tar.py" "$REPO" "$REV" "$tmp/tree.tar" --prefix app/ --overlay > "$EVID/runner/overlay-$run.txt"
  docker create --name "$name" --network none --restart no --memory 1536m --memory-swap 1536m --pids-limit 4096 \
    --label "cse.staging.campaign=$CAMPAIGN" --label "cse.staging.revision=$REV" \
    -e "GIT_SHA=$REV" -e "GIT_HEAD_SHA=$REV" -e "PYTEST_ADDOPTS=" \
    "$RUNNER_IMAGE" cse-staging-run-group "$group" > /dev/null
  { echo "PROVENANCE utc=$(date -u +%FT%TZ) campaign=$CAMPAIGN revision=$REV container=$name image=$RUNNER_IMAGE $(docker inspect -f '{{.Image}}' "$name")"
    echo "PROVENANCE staging.sh sha256=$(sha_of "$HERE/staging.sh") (the driver)"
    echo "PROVENANCE run_group.sh sha256: build context $([[ -f $CTX/run_group.sh ]] && sha256sum < "$CTX/run_group.sh" | cut -c1-64 || echo missing), working tree $(sha_of "$HERE/run_group.sh")"
    echo "PROVENANCE deselected: none (0 node ids; no deselection option is passed, so the restore-executing tests run); the container's $(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name" | grep '^PYTEST_ADDOPTS=' | sed 's/=$/ (empty)/')"
    echo "PROVENANCE tree under test: tar sha256=$(sha256sum < "$tmp/tree.tar" | cut -c1-64) (the commit plus the overlay in overlay-$run.txt)"
  } > "$EVID/runner/$run.provenance.txt"
  docker cp - "$name:/home/tester" < "$tmp/tree.tar"
  rm -rf "$tmp"
  note "runner $run: start ($name, no network, nothing deselected: the restore-executing tests run)"
  set +e
  docker start -a "$name" > "$EVID/runner/$run.console.log" 2>&1
  local rc=$?
  set -e
  mkdir -p "$EVID/runner/$run"
  docker cp "$name:/home/tester/out/." "$(winpath "$EVID/runner/$run")" || note "runner $run: copying its results failed"
  note "runner $run: exit $rc ($(tail -n 1 "$EVID/runner/$run.console.log" | tr -d '\r'))"
  return $rc
}

cmd_canary() {
  local f n list rc
  [[ -s "$SECRETS/offsite-password" ]] || { echo "no secret generated for $CAMPAIGN" >&2; exit 2; }
  mkdir -p "$EVID/canary"
  for f in $(docker ps -a --filter "label=cse.staging.campaign=$CAMPAIGN" --format '{{.Names}}'); do
    docker inspect "$f" > "$EVID/canary/inspect-$f.json"
    if [[ "$(docker inspect -f '{{.State.Running}}' "$f")" == true ]] && docker exec "$f" test -d /run/systemd/system 2>/dev/null; then
      docker exec "$f" journalctl --no-pager -o export > "$EVID/canary/journal-$f.export" 2>/dev/null || true
      docker exec "$f" ps -eo args > "$EVID/canary/ps-$f.txt" 2>/dev/null || true
    fi
  done
  docker history --no-trunc "$HOST_IMAGE" > "$EVID/canary/history-host.txt"
  docker history --no-trunc "$RUNNER_IMAGE" > "$EVID/canary/history-runner.txt"
  # The pattern without any CR: GNU grep on Linux keeps a CR from a file written on Windows in the pattern and then misses
  # every LF-ended copy (Git Bash's grep drops it). grep's status is read, not left to set -e: 1 is the clean result, 2 a
  # scan that failed (never a pass)
  set +e
  list=$(grep -rlF -f <(tr -d '\r' < "$SECRETS/offsite-password") "$EVID")
  rc=$?
  set -e
  [[ $rc -le 1 ]] || { echo "canary: the scan itself failed (grep exit $rc)" >&2; exit 2; }
  n=$(grep -c . <<< "$list" || true)
  echo "files under the evidence containing the test secret: $n ($(find "$EVID" -type f | wc -l) files scanned)" \
    | tee "$EVID/canary/result.txt"
  [[ $n -eq 0 ]] || { printf '%s\n' "$list" | tee -a "$EVID/canary/result.txt"; exit 1; }  # the file names only
}

cmd_collect() {
  local name
  name=$(host_name "${1:-}")
  mkdir -p "$EVID/collect"
  docker inspect "$name" > "$EVID/collect/inspect-$name.json"
  docker exec "$name" journalctl --no-pager -o short-iso-precise > "$EVID/collect/journal-$name.log" 2>&1 || true
  docker exec "$name" bash -c 'cat /usr/share/cse-staging/packages.txt' > "$EVID/collect/packages-$name.txt"
  docker exec "$name" bash -c 'tar -C /srv/cse-backup -cf - status 2>/dev/null' > "$EVID/collect/status-$name.tar" || true
  docker exec "$name" bash -c 'tar -C /var/lib/cse-staging -cf - . 2>/dev/null' > "$EVID/collect/cse-staging-$name.tar" || true
  note "collected $name"
}

case "${1:-}" in
  context) cmd_context ;;
  build) shift; cmd_build "$@" ;;
  volumes) shift; cmd_volumes "$@" ;;
  trial) shift; cmd_trial "$@" ;;
  up) shift; cmd_up "$@" ;;
  start) shift; cmd_start "$@" ;;
  stop) shift; cmd_stop "$@" ;;
  kill) shift; cmd_kill "$@" ;;
  inject) shift; cmd_inject "$@" ;;
  secret) shift; cmd_secret "$@" ;;
  run) shift; cmd_run "$@" ;;
  isolation) shift; cmd_isolation "$@" ;;
  seed) shift; cmd_seed "$@" ;;
  runner) shift; cmd_runner "$@" ;;
  guard) guard ;;
  canary) cmd_canary ;;
  collect) shift; cmd_collect "$@" ;;
  restore) shift; . "$HERE/restore_driver.sh"; cmd_restore "$@" ;;
  *) sed -n '2,39p' "$0"; exit 2 ;;
esac
