#!/bin/sh
# Read-only release-candidate gate preflight. This is discovery, not gate proof.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
usage() { echo "Usage: $0 NEW_OUTPUT_DIR"; }
if [ "$#" -ne 1 ] || [ "$1" = -h ] || [ "$1" = --help ]; then usage; [ "$#" -eq 1 ] && exit 0; exit 2; fi
OUT=$1
case "$OUT" in /*) ;; *) OUT="$ROOT/$OUT" ;; esac
case "$OUT" in *"/../"*|*/..) echo "parent traversal is not allowed in output path" >&2; exit 2;; esac
OUT_PARENT=$(CDPATH= cd -- "$(dirname -- "$OUT")" 2>/dev/null && pwd -P) || {
  echo "output parent must already exist" >&2; exit 2;
}
case "$OUT_PARENT" in "$ROOT"|"$ROOT"/*) ;; *) echo "output must resolve inside repository to keep evidence controlled" >&2; exit 2;; esac
OUT="$OUT_PARENT/$(basename -- "$OUT")"
if ! mkdir "$OUT" 2>/dev/null; then echo "refusing existing/uncreatable output path: $OUT" >&2; exit 2; fi
umask 077
record() { printf '%s\n' "$*"; }
{
  record "recorded_at=$(date --iso-8601=seconds 2>/dev/null || date)"
  record "hostname=$(hostname 2>/dev/null || echo unavailable)"
  record "kernel=$(uname -srmo 2>/dev/null || echo unavailable)"
  record "os_release=$(grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | head -1 || echo unavailable)"
  record "architecture=$(uname -m 2>/dev/null || echo unavailable)"
  record "uid=$(id -u)"
  record "version=$($ROOT/bin/workload-guard version 2>&1 | tail -1)"
  record "head=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unavailable)"
  record "branch=$(git -C "$ROOT" branch --show-current 2>/dev/null || echo unavailable)"
  record "tracked_worktree=$(if [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]; then echo clean; else echo dirty; fi)"
  record "cgroup_v2=$(if [ -r /sys/fs/cgroup/cgroup.controllers ]; then echo present; else echo absent; fi)"
  record "cgroup_controllers=$(if [ -r /sys/fs/cgroup/cgroup.controllers ]; then tr '\n' ' ' < /sys/fs/cgroup/cgroup.controllers; else echo unavailable; fi)"
  record "process_cgroup=$(cat /proc/self/cgroup 2>/dev/null | tr '\n' ';' || echo unavailable)"
  record "systemd_user_manager=$(if systemctl --user show-environment >/dev/null 2>&1; then echo available; else echo unavailable; fi)"
  # This project installs a user service and user slices. Record those as the
  # primary runtime observation; system-scope units with the same names are a
  # different manager and must never be used to infer user-service state.
  record "router_user_service=$(systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts 2>&1 | tr '\n' ';' || true)"
  record "heavy_user_slice_quota_observation=$(systemctl --user show heavy-workload.slice -p CPUQuotaPerSecUSec 2>&1 | tr '\n' ';' || true)"
  record "router_system_service_separate_scope=$(systemctl show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts 2>&1 | tr '\n' ';' || true)"
  record "heavy_system_slice_quota_separate_scope=$(systemctl show heavy-workload.slice -p CPUQuotaPerSecUSec 2>&1 | tr '\n' ';' || true)"
  record "host_cpu_psi=$(cat /proc/pressure/cpu 2>/dev/null | tr '\n' ';' || echo unavailable)"
  record "loadavg=$(cat /proc/loadavg 2>/dev/null || echo unavailable)"
  record "quota_or_policy_mutations=NONE"
  record "load_generation=NONE"
} > "$OUT/environment.txt"

# Distinguish host observations from actual delegated test-cgroup capability.
{
  echo 'Gate preflight classification (not release acceptance):'
  if [ -r /sys/fs/cgroup/cgroup.controllers ] && grep -qw cpu /sys/fs/cgroup/cgroup.controllers; then
    echo 'cgroup_v2_cpu_controller=OBSERVED'
  else echo 'cgroup_v2_cpu_controller=SKIP / OPEN'; fi
  if systemctl --user show-environment >/dev/null 2>&1; then
    echo 'user_systemd_manager=OBSERVED'
  else echo 'user_systemd_manager=SKIP / OPEN'; fi
  echo 'delegated_cpu_stat_counters=NOT PROVEN (requires the actual delegated test slice)'
  echo 'quota_actuation_and_readback=NOT RUN (this script is read-only)'
  echo 'independent_host_ABBA=NOT RUN'
  echo 'throughput_acceptance=OPEN / CONFOUNDED_INCONCLUSIVE; threshold=0.90'
  echo 'dedicated_host_rollback=NOT RUN / OPEN'
  echo 'natural_production_PSI_crossing_0.40=NOT RUN'
  echo 'continuous_RC_soak_24h_min_72h_target=NOT RUN'
  echo 'stable_release=NO-GO; version must remain 0.6.5-rc.1; no v1.0.0 tag'
  echo 'WARNING: compatibility CI, root-cgroup counters, synthetic tests, and this preflight do not close host-dependent gates.'
} > "$OUT/gates.txt"

{
  echo "command=$0 $1"
  echo "exit_status=0"
  echo "scope=read-only host/repository discovery; no quota writes, service lifecycle actions, or workload generation"
  echo "recorded_at=$(date --iso-8601=seconds 2>/dev/null || date)"
} > "$OUT/collection.txt"
(cd "$OUT" && sha256sum environment.txt gates.txt collection.txt > SHA256SUMS.txt && sha256sum -c SHA256SUMS.txt > manifest-verification.txt)
echo "RC gate preflight collected (not acceptance evidence): $OUT"
cat "$OUT/gates.txt"
