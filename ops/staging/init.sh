#!/bin/sh
# PID 1 of the staging host container (docs/ops/STAGING.md): systemd. At the reduced-privilege rungs L1/L2 (CAP_SYS_ADMIN,
# a PRIVATE cgroup namespace, no --privileged) Docker mounts the namespace's own cgroup2 filesystem read-only, and systemd
# needs it writable to manage its own sub-tree; it is remounted read-write inside the private namespace only, which
# exposes no other container's or the VM's cgroups. Nothing else is changed.
set -e
opts=$(awk '$2 == "/sys/fs/cgroup" && $3 == "cgroup2" {print $4}' /proc/self/mounts)
case ",$opts," in
  *,ro,*) mount -o remount,rw /sys/fs/cgroup || { echo "cse-staging-init: cannot remount /sys/fs/cgroup rw" >&2; exit 70; } ;;
esac
exec /lib/systemd/systemd
