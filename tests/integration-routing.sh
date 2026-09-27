#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if ! command -v systemd-run >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
  printf '%s\n' 'integration routing: skipped (systemd tools unavailable)'
  exit 0
fi
if [ ! -f /sys/fs/cgroup/cgroup.controllers ]; then
  printf '%s\n' 'integration routing: skipped (cgroup v2 unavailable)'
  exit 0
fi
if ! systemctl --user is-system-running >/dev/null 2>&1; then
  printf '%s\n' 'integration routing: skipped (systemd user manager unavailable)'
  exit 0
fi

UNIT="workload-guard-e2e-$$.scope"
PID_FILE="/tmp/linux-workload-guard-e2e.$$.pid"
ROUTER_PID=''
WORK_PID=''
STATE_FILE="/tmp/linux-workload-guard-e2e.$$.routes.tsv"
cleanup() {
  if [ -n "$ROUTER_PID" ]; then kill "$ROUTER_PID" 2>/dev/null || true; fi
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  rm -f "$PID_FILE" "$STATE_FILE"
}
trap cleanup EXIT INT TERM

systemctl --user start protected-workload.slice heavy-workload.slice

systemd-run --user --scope --slice=protected-workload.slice --unit="$UNIT" \
  sh -c "python3 -c 'import os,time; open(\"$PID_FILE\",\"w\").write(str(os.getpid())); [x for x in iter(int, 1)]' & wait" >/dev/null 2>&1 &

for _ in $(seq 1 40); do
  if [ -s "$PID_FILE" ]; then
    WORK_PID=$(cat "$PID_FILE")
    break
  fi
  case "$WORK_PID" in
    ''|0) sleep 0.1 ;;
    *) break ;;
  esac
done
test -n "$WORK_PID" && test "$WORK_PID" != 0

env \
  WORKLOAD_GUARD_SAMPLE_SEC=0.25 \
  WORKLOAD_GUARD_SUSTAINED_SAMPLES=2 \
  WORKLOAD_GUARD_CPU_THRESHOLD=10 \
  WORKLOAD_GUARD_PARENT_UNIT=protected-workload.slice \
  WORKLOAD_GUARD_HEAVY_UNIT=heavy-workload.slice \
  WORKLOAD_GUARD_STATE_FILE="$STATE_FILE" \
  python3 "$ROOT/bin/workload-router.py" >/tmp/linux-workload-guard-e2e.$$.log 2>&1 &
ROUTER_PID=$!

HEAVY_CGROUP=$(systemctl --user show heavy-workload.slice -p ControlGroup --value)
test -n "$HEAVY_CGROUP"
HEAVY_CGROUP="/sys/fs/cgroup$HEAVY_CGROUP"

for _ in $(seq 1 40); do
  if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
    test -s "$STATE_FILE"
    grep -q "^$WORK_PID	.*	sustained-cpu	" "$STATE_FILE"
    grep -q "$HEAVY_CGROUP" "$STATE_FILE"
    printf '%s\n' 'integration routing: ok'
    exit 0
  fi
  sleep 0.25
done

printf '%s\n' 'integration routing: failed'
sed -n '1,80p' "/tmp/linux-workload-guard-e2e.$$.log" 2>/dev/null || true
exit 1
