# Milestone 8 staging: restore under a restore authorization (RA). Sourced by ops/staging/staging.sh for its `restore`
# subcommand only; it uses staging.sh's variables and helpers (CAMPAIGN, REV, EVID, HOST_IMAGE, docker, guard, note,
# host_name, new_log, winpath, sha_of). docs/ops/STAGING.md sections 3 and 11; docs/ops/ISOLATED_RESTORE.md.
#
#   staging.sh restore R1|R2 RA-FILE [suffix]
#
# Owner decisions OD-10 to OD-14 (DR-0002, 2026-11-04): each restore campaign needs the owner's own yes in chat, which
# the orchestrator records as an RA file; the harness adds automatic technical checks. In this order, fail closed:
#   0. the evidence directory must be the fixed D:\cse-staging\<campaign> (RESTORE_EVID_ROOT): the RA records live
#      there and no setting moves them (AR-0006-1 F-1);
#   1. the RA is validated READ-ONLY against values read here (restore_gate.py ra): operation, campaign, build SHA
#      (this revision and the image's revision label), image ID (docker inspect), target, expiry (at most 24 hours after
#      `decided`), single use against every campaign's record. Any mismatch refuses, and nothing below runs;
#   2. the technical checks: R1, the newest dump verifies, the scratch area is empty and the R1 gate is closed; R2, the
#      target container does not exist yet and the source record (identity, ledger run, P1's verify) is complete;
#   3. the RA is recorded as used (one appended line in D:\cse-staging\<campaign>\restore\ra-consumed.log), before
#      anything restores;
#   4. R1: one run of P1's restore check in the host container (stage_restore.sh r1), and the restore_check run it
#      produced bound to the RA (restore_gate.py bind-r1; r1-runs.log; F-2). R2: a NEW container (--network none, no
#      volume, no mount, no added capability), the dump copied in with `docker cp` from the host's backup volume, the
#      procedure in it (stage_restore.sh r2), its evidence copied out, the copy torn down, and the container stopped
#      and DELETED once its evidence is saved (owner, 2026-11-04: throwaway containers are deleted afterwards).
# The RA file is only ever read (by restore_gate.py); nothing here writes beside it or into the vault.
RESTORE_EVID_ROOT=/d/cse-staging                # fixed: restore takes no other evidence root (F-1)
RESTORE_TOOLS=/opt/cse-restore                  # in the containers; never /opt/cse-staging, which `run` executes from
R2_STATE=/var/lib/cse-restore

r2_name() { echo "cse-stg-$CAMPAIGN${1:+-$1}-r2"; }

restore_refuse() {  # restore_refuse LOG WHY
  echo "REFUSED: $2; nothing was restored" >> "$1"
  echo "exit 1" >> "$1"
  note "restore: REFUSED ($2)"
  return 1
}

ra_gate() {  # ra_gate LOG [--consume]: restore_gate.py ra with the values read by cmd_restore
  python "$HERE/restore_gate.py" ra --ra "$(winpath "$RA_FILE")" --operation "$RA_OP" --campaign "$CAMPAIGN" \
    --revision "$REV" --label-revision "$RA_LABEL" --image-id "$RA_IMAGE" --tagged-image-id "$RA_TAGGED" \
    --target "$RA_TARGET" --evidence "$(winpath "$EVID")" "${@:2}" >> "$1" 2>&1
}

copy_restore_script() {  # copy_restore_script CONTAINER FILE...: LF copies into RESTORE_TOOLS, each hash recorded
  local name=$1 tmp f; shift
  tmp=$(mktemp -d)
  for f in "$@"; do tr -d '\r' < "$f" > "$tmp/$(basename "$f")"; done
  docker exec "$name" install -d -m 0755 "$RESTORE_TOOLS"
  docker cp "$(winpath "$tmp")/." "$name:$RESTORE_TOOLS/"
  docker exec "$name" sh -c "chown -R root:root $RESTORE_TOOLS && chmod 0755 $RESTORE_TOOLS/*.sh 2>/dev/null; chmod 0644 $RESTORE_TOOLS/*.py 2>/dev/null; true"
  rm -rf "$tmp"
  for f in "$@"; do echo "PROVENANCE $(basename "$f") sha256=$(sha_of "$f") (copied into $name:$RESTORE_TOOLS)"; done
}

cmd_restore() {
  local op=${1:-} ra=${2:-} suffix=${3:-} host log
  [[ ($op == R1 || $op == R2) && -n $ra ]] || { echo "restore R1|R2 RA-FILE [suffix]" >&2; exit 2; }
  [[ $CAMPAIGN =~ ^[a-z0-9]+$ ]] || { echo "restore: campaign '$CAMPAIGN' is not lower-case letters and digits" >&2; exit 2; }
  [[ "$EVID" == "$RESTORE_EVID_ROOT/$CAMPAIGN" ]] \
    || { echo "restore: the evidence directory must be $RESTORE_EVID_ROOT/$CAMPAIGN (the RA records are fixed there), not $EVID" >&2; exit 2; }
  guard
  host=$(host_name "$suffix")
  mkdir -p "$EVID/restore"
  log=$(new_log "${suffix:-main}-restore-$op")
  # the values the RA is checked against, read here: never taken from the RA or from the caller
  RA_OP=$op RA_FILE=$ra
  RA_TARGET=$host; [[ $op == R2 ]] && RA_TARGET=$(r2_name "$suffix")
  RA_IMAGE=$(docker inspect -f '{{.Image}}' "$host" 2>/dev/null || true)
  RA_TAGGED=$(docker image inspect -f '{{.Id}}' "$HOST_IMAGE" 2>/dev/null || true)
  RA_LABEL=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${RA_IMAGE:-none}" 2>/dev/null || true)
  { echo "PROVENANCE utc=$(date -u +%FT%TZ) campaign=$CAMPAIGN revision=$REV operation=$op target=$RA_TARGET host=$host"
    echo "PROVENANCE harness checkout: HEAD $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown), $(git -C "$REPO" status --porcelain -- ops/staging ops/restore 2>/dev/null | wc -l) uncommitted path(s) under ops/staging and ops/restore"
    echo "PROVENANCE staging.sh sha256=$(sha_of "$HERE/staging.sh") restore_driver.sh sha256=$(sha_of "$HERE/restore_driver.sh") restore_gate.py sha256=$(sha_of "$HERE/restore_gate.py")"
    echo "PROVENANCE read by the harness: host image $RA_IMAGE; $HOST_IMAGE is $RA_TAGGED; revision label $RA_LABEL"
    echo "RA $ra (read-only)"
  } > "$log"
  note "restore $op: validating the restore authorization (read-only) for $RA_TARGET -> $log"
  ra_gate "$log" || { restore_refuse "$log" "the restore authorization is not valid"; return 1; }
  if [[ $op == R1 ]]; then restore_r1 "$host" "$log"; else restore_r2 "$host" "$RA_TARGET" "$log"; fi
}

consumed_ra() {  # consumed_ra LOG -> "RA-NNNN <sha256>" of the CONSUMED line restore_gate.py printed, or nothing
  sed -n 's/^CONSUMED \(RA-[0-9]\{4\}\) (sha256 \([0-9a-f]\{64\}\)) in .*/\1 \2/p' "$1" | tail -n 1 | tr -d '\r'
}

restore_r1() {  # restore_r1 HOST LOG: ST-30, P1 section 8A's restore check, once, in the host container
  local host=$1 log=$2 rc ra_line ra_id ra_sha run
  [[ "$(docker inspect -f '{{.State.Running}}' "$host" 2>/dev/null)" == true ]] || { restore_refuse "$log" "$host is not running"; return 1; }
  copy_restore_script "$host" "$HERE/restore/stage_restore.sh" >> "$log"
  echo "== R1 technical checks (read-only)" >> "$log"
  if ! docker exec "$host" bash "$RESTORE_TOOLS/stage_restore.sh" r1-check >> "$log" 2>&1; then
    docker exec "$host" rm -f "$RESTORE_TOOLS/stage_restore.sh" || true
    restore_refuse "$log" "an R1 technical check failed (the RA was not consumed)"; return 1
  fi
  echo "== the RA recorded as used, before the restore" >> "$log"
  ra_gate "$log" --consume || { docker exec "$host" rm -f "$RESTORE_TOOLS/stage_restore.sh" || true
                                restore_refuse "$log" "the RA could not be consumed"; return 1; }
  ra_line=$(consumed_ra "$log"); ra_id=${ra_line%% *}; ra_sha=${ra_line#* }
  note "restore R1: one run of P1's restore check in $host under $ra_id"
  set +e
  docker exec "$host" bash "$RESTORE_TOOLS/stage_restore.sh" r1 "$ra_id" "$ra_sha" >> "$log" 2>&1
  rc=$?
  docker exec "$host" rm -f "$RESTORE_TOOLS/stage_restore.sh"     # no restore code stays in the host
  # F-2: the restore_check run R1 produced, bound to the RA on the host side too (the container holds its own binding)
  run=$(sed -n 's/^R1-RUN \([0-9][0-9]*\) .*/\1/p' "$log" | tail -n 1 | tr -d '\r')
  if [[ -n $run ]]; then
    python "$HERE/restore_gate.py" bind-r1 --campaign "$CAMPAIGN" --ra-id "$ra_id" --ra-sha "$ra_sha" --run-id "$run" >> "$log" 2>&1 || rc=1
  else
    echo "FAIL R1: no restore_check run was bound to $ra_id (no single new row)" >> "$log"; rc=1
  fi
  set -e
  echo "exit $rc" >> "$log"
  note "restore R1: exit $rc ($(grep -c '^PASS' "$log") PASS, $(grep -c '^FAIL' "$log") FAIL)"
  return $rc
}

r2_run() {  # r2_run HOST TARGET LOG RDIR DUMP: the isolation check, the copy-in, the procedure (sets R2_RAN)
  local host=$1 target=$2 log=$3 rdir=$4 dump=$5
  docker inspect "$target" > "$rdir/inspect-created.json"
  echo "== the R2 container's isolation (docker inspect)" >> "$log"
  python "$HERE/restore_gate.py" r2inspect --campaign "$CAMPAIGN" --target "$target" --image-id "$RA_IMAGE" \
    < "$rdir/inspect-created.json" >> "$log" 2>&1 || { echo "FAIL R2: the container is not isolated as required; nothing was copied in" >> "$log"; return 1; }
  docker exec "$target" install -d -m 0700 "$R2_STATE/in" || return 1
  docker cp "$host:$dump" - | docker cp - "$target:$R2_STATE/in/"        # from the host's backup volume, by docker cp
  [[ "${PIPESTATUS[0]}${PIPESTATUS[1]}" == 00 ]] || { echo "FAIL R2: copying the dump in failed" >> "$log"; return 1; }
  docker cp "$(winpath "$rdir/source.json")" "$target:$R2_STATE/in/source.json" || return 1
  copy_restore_script "$target" "$HERE/restore/stage_restore.sh" "$REPO/ops/restore/isolated_restore.py" >> "$log" || return 1
  note "restore R2: the procedure in $target on $dump"
  R2_RAN=1
  docker exec "$target" bash "$RESTORE_TOOLS/stage_restore.sh" r2 "$R2_STATE/in" "$(basename "$dump")" >> "$log" 2>&1
}

r2_finish() {  # r2_finish TARGET LOG RDIR RC: evidence out, teardown, stop; the container deleted once its evidence is saved
  local target=$1 log=$2 rdir=$3 rc=$4 saved=1
  if [[ ${R2_RAN:-0} == 1 ]]; then
    mkdir -p "$rdir/evidence"
    docker cp "$target:$R2_STATE/evidence/." "$(winpath "$rdir/evidence")" >> "$log" 2>&1 \
      && [[ -s "$rdir/evidence/procedure.json" ]] || { saved=0; echo "FAIL R2: the procedure's evidence could not be saved" >> "$log"; }
    docker exec "$target" bash "$RESTORE_TOOLS/stage_restore.sh" r2-teardown >> "$log" 2>&1 || rc=1
    # the evidence again, AFTER the teardown: procedure.json now holds the teardown's own identity check and the
    # copy's state afterwards (AR-C-0001 finding 10). The first copy above stays the fallback if this one fails.
    docker cp "$target:$R2_STATE/evidence/." "$(winpath "$rdir/evidence")" >> "$log" 2>&1 \
      || echo "NOTE R2: the evidence after teardown could not be copied; the copy taken before teardown is kept" >> "$log"
  fi
  docker inspect "$target" > "$rdir/inspect-after.json" 2>> "$log" || saved=0
  docker stop "$target" > /dev/null 2>> "$log"
  if [[ $saved == 1 ]]; then
    docker rm "$target" > /dev/null 2>> "$log" && echo "R2: $target deleted after its evidence was saved ($rdir)" >> "$log" \
      || { echo "FAIL R2: $target could not be deleted" >> "$log"; rc=1; }
  else
    echo "R2: $target stopped and KEPT: its evidence was not saved; the owner decides" >> "$log"; rc=1
  fi
  return $rc
}

restore_r2() {  # restore_r2 HOST TARGET LOG: ST-31, the isolated restored copy and its verification
  local host=$1 target=$2 log=$3 rdir dump rc
  rdir="$EVID/restore/r2-$(date -u +%Y%m%dT%H%M%SZ)"
  [[ ! -e $rdir ]] || { restore_refuse "$log" "$rdir exists"; return 1; }
  mkdir -p "$rdir"
  [[ "$(docker inspect -f '{{.State.Running}}' "$host" 2>/dev/null)" == true ]] || { restore_refuse "$log" "$host is not running"; return 1; }
  [[ -z "$(docker ps -a --filter "name=^${target}\$" --format '{{.Names}}')" ]] \
    || { restore_refuse "$log" "$target exists: R2 needs a container created fresh for it"; return 1; }
  echo "== the source (read-only, in $host): its system identifier and the newest succeeded dump's ledger run" >> "$log"
  copy_restore_script "$host" "$HERE/restore/stage_restore.sh" >> "$log"
  set +e
  docker exec "$host" bash "$RESTORE_TOOLS/stage_restore.sh" source > "$rdir/source.json" 2>> "$log"
  rc=$?
  docker exec "$host" rm -f "$RESTORE_TOOLS/stage_restore.sh"
  python "$HERE/restore_gate.py" source --file "$(winpath "$rdir/source.json")" >> "$log" 2>&1 || rc=1
  set -e
  [[ $rc -eq 0 ]] || { restore_refuse "$log" "the source record is incomplete (the RA was not consumed)"; return 1; }
  dump=$(sed -n 's/^DUMP //p' "$log" | tail -n 1 | tr -d '\r')
  echo "== the RA recorded as used, before the restore" >> "$log"
  ra_gate "$log" --consume || { restore_refuse "$log" "the RA could not be consumed"; return 1; }
  note "restore R2: a new isolated container $target (no network, no volume) from $RA_IMAGE"
  docker run -d --name "$target" --hostname cse-restore --network none --restart no --memory 1536m --memory-swap 1536m \
    --pids-limit 4096 --stop-timeout 60 --label "cse.staging.campaign=$CAMPAIGN" --label "cse.staging.revision=$REV" \
    --label cse.staging.role=r2 --entrypoint /bin/sleep "$RA_IMAGE" infinity > /dev/null \
    || { restore_refuse "$log" "the R2 container could not be created"; return 1; }
  set +e
  R2_RAN=0
  r2_run "$host" "$target" "$log" "$rdir" "$dump"
  rc=$?
  r2_finish "$target" "$log" "$rdir" "$rc"
  rc=$?
  set -e
  echo "exit $rc" >> "$log"
  note "restore R2: exit $rc ($(grep -c '^PASS' "$log") PASS, $(grep -c '^FAIL' "$log") FAIL); evidence $rdir"
  return $rc
}
