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
if ! systemctl --user show-environment >/dev/null 2>&1; then
  printf '%s\n' 'integration routing: skipped (systemd user manager unavailable)'
  exit 0
fi

UNIT="workload-guard-e2e-$$.scope"
PID_FILE="/tmp/linux-workload-guard-e2e.$$.pid"
ROUTER_PID=''
WORK_PID=''
STATE_FILE="/tmp/linux-workload-guard-e2e.$$.routes.tsv"
PHASE_FILE="/tmp/linux-workload-guard-e2e.$$.phase"
WORKER="/tmp/linux-workload-guard-e2e.$$.py"
SERVICE_WAS_ACTIVE=0
systemctl --user is-active --quiet workload-router.service && SERVICE_WAS_ACTIVE=1 || true
cleanup() {
  if [ -n "$ROUTER_PID" ]; then kill "$ROUTER_PID" 2>/dev/null || true; fi
  if [ -n "$WORK_PID" ]; then kill "$WORK_PID" 2>/dev/null || true; fi
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  if [ "$SERVICE_WAS_ACTIVE" -eq 1 ]; then systemctl --user start workload-router.service >/dev/null 2>&1 || true; fi
  rm -f "$PID_FILE" "$STATE_FILE" "$PHASE_FILE" "$WORKER" "$PHASE_FILE.done" "/tmp/linux-workload-guard-e2e.$$.log"
}
trap cleanup EXIT INT TERM

if [ "$SERVICE_WAS_ACTIVE" -eq 1 ]; then systemctl --user stop workload-router.service; fi
systemctl --user start protected-workload.slice heavy-workload.slice

cat > "$WORKER" <<PY
import os, time
phase_file = "$PHASE_FILE"
open("$PID_FILE", "w").write(str(os.getpid()))
while True:
    phase = open(phase_file).read().strip() if os.path.exists(phase_file) else "idle"
    if phase == "burst":
        end = time.monotonic() + 0.12
        while time.monotonic() < end:
            pass
        open(phase_file + ".done", "w").write("done")
        open(phase_file, "w").write("idle")
    elif phase == "hot":
        x = 1
        while open(phase_file).read().strip() == "hot":
            x = (x * 1664525 + 1013904223) & 0xffffffff
    else:
        time.sleep(0.02)
PY
printf '%s' idle > "$PHASE_FILE"
rm -f "$PHASE_FILE.done"
systemd-run --user --scope --slice=protected-workload.slice --unit="$UNIT" python3 "$WORKER" >/dev/null 2>&1 &

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
  WORKLOAD_GUARD_SUSTAINED_SAMPLES=3 \
  WORKLOAD_GUARD_CPU_THRESHOLD=10 \
  WORKLOAD_GUARD_PARENT_UNIT=protected-workload.slice \
  WORKLOAD_GUARD_HEAVY_UNIT=heavy-workload.slice \
  WORKLOAD_GUARD_STATE_FILE="$STATE_FILE" \
  python3 "$ROOT/bin/workload-router.py" >/tmp/linux-workload-guard-e2e.$$.log 2>&1 &
ROUTER_PID=$!

HEAVY_CGROUP=$(systemctl --user show heavy-workload.slice -p ControlGroup --value)
test -n "$HEAVY_CGROUP"
HEAVY_CGROUP="/sys/fs/cgroup$HEAVY_CGROUP"

printf '%s' burst > "$PHASE_FILE"
for _ in $(seq 1 40); do [ -s "$PHASE_FILE.done" ] && break; sleep 0.05; done
sleep 0.8
if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
  printf '%s\n' 'integration routing: failed (false positive on 120ms burst)'
  sed -n '1,80p' "/tmp/linux-workload-guard-e2e.$$.log" 2>/dev/null || true
  exit 1
fi
HOT_START=$(python3 -c 'import time; print(time.monotonic())')
printf '%s' hot > "$PHASE_FILE"

for _ in $(seq 1 40); do
  if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
    test -s "$STATE_FILE"
    grep -q "^$WORK_PID	.*	sustained-cpu	" "$STATE_FILE"
    grep -q "$HEAVY_CGROUP" "$STATE_FILE"
    ROUTED_AT=$(python3 -c 'import time; print(time.monotonic())')
    python3 - "$HOT_START" "$ROUTED_AT" <<'PY'
import sys
print(f'integration routing: ok (120ms burst not routed; sustained detection {float(sys.argv[2])-float(sys.argv[1]):.2f}s)')
PY
    exit 0
  fi
  sleep 0.25
done

printf '%s\n' 'integration routing: failed'
sed -n '1,80p' "/tmp/linux-workload-guard-e2e.$$.log" 2>/dev/null || true
exit 1
