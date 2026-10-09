#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v systemd-run >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1 || [ ! -f /sys/fs/cgroup/cgroup.controllers ] || ! systemctl --user show-environment >/dev/null 2>&1; then
  printf '%s\n' 'recovery routing: skipped (systemd/cgroup v2/user manager unavailable)'
  exit 0
fi
UNIT="workload-guard-recovery-$$.scope"
PID_FILE="/tmp/linux-workload-guard-recovery.$$.pid"
PHASE_FILE="/tmp/linux-workload-guard-recovery.$$.phase"
WORKER="/tmp/linux-workload-guard-recovery.$$.py"
STATE_FILE="/tmp/linux-workload-guard-recovery.$$.routes.tsv"
ROUTER_PID=''
WORK_PID=''
cleanup() {
  [ -n "$ROUTER_PID" ] && kill "$ROUTER_PID" 2>/dev/null || true
  [ -n "$WORK_PID" ] && kill "$WORK_PID" 2>/dev/null || true
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  rm -f "$PID_FILE" "$PHASE_FILE" "$STATE_FILE" "$WORKER" "/tmp/linux-workload-guard-recovery.$$.log"
}
trap cleanup EXIT INT TERM
printf '%s' hot > "$PHASE_FILE"
systemctl --user start protected-workload.slice heavy-workload.slice
cat > "$WORKER" <<PY
import time
phase_file = "$PHASE_FILE"
open("$PID_FILE", "w").write(str(__import__("os").getpid()))
x = 0
while True:
    if open(phase_file).read().strip() == "hot":
        end = time.monotonic() + 0.2
        while time.monotonic() < end:
            x = (x * 1664525 + 1013904223) & 0xffffffff
    else:
        time.sleep(0.1)
PY
systemd-run --user --scope --slice=protected-workload.slice --unit="$UNIT" python3 "$WORKER" >/dev/null 2>&1 &
for _ in $(seq 1 40); do [ -s "$PID_FILE" ] && break; sleep 0.1; done
WORK_PID=$(cat "$PID_FILE")
SOURCE_CGROUP=$(awk -F: '$1=="0" {print "/sys/fs/cgroup" $3}' "/proc/$WORK_PID/cgroup")
test -n "$SOURCE_CGROUP"
PARENT_CGROUP=$(systemctl --user show protected-workload.slice -p ControlGroup --value)
PARENT_CGROUP="/sys/fs/cgroup$PARENT_CGROUP"
env \
  WORKLOAD_GUARD_SAMPLE_SEC=0.25 \
  WORKLOAD_GUARD_SUSTAINED_SAMPLES=2 \
  WORKLOAD_GUARD_CPU_THRESHOLD=10 \
  WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD=35 \
  WORKLOAD_GUARD_RECOVERY_SAMPLES=2 \
  WORKLOAD_GUARD_RECOVERY_DWELL_SEC=1 \
  WORKLOAD_GUARD_PARENT_UNIT=protected-workload.slice \
  WORKLOAD_GUARD_HEAVY_UNIT=heavy-workload.slice \
  WORKLOAD_GUARD_STATE_FILE="$STATE_FILE" \
  python3 "$ROOT/bin/workload-router.py" >/tmp/linux-workload-guard-recovery.$$.log 2>&1 &
ROUTER_PID=$!
HEAVY_CGROUP=$(systemctl --user show heavy-workload.slice -p ControlGroup --value)
HEAVY_CGROUP="/sys/fs/cgroup$HEAVY_CGROUP"
for _ in $(seq 1 40); do
  if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then break; fi
  sleep 0.25
done
grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs"
awk -F '\t' -v pid="$WORK_PID" '$1 == pid { found=1 } END { exit !found }' "$STATE_FILE"
kill "$ROUTER_PID"
ROUTER_PID=''
env \
  WORKLOAD_GUARD_SAMPLE_SEC=0.25 \
  WORKLOAD_GUARD_SUSTAINED_SAMPLES=2 \
  WORKLOAD_GUARD_CPU_THRESHOLD=10 \
  WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD=35 \
  WORKLOAD_GUARD_RECOVERY_SAMPLES=2 \
  WORKLOAD_GUARD_RECOVERY_DWELL_SEC=1 \
  WORKLOAD_GUARD_PARENT_UNIT=protected-workload.slice \
  WORKLOAD_GUARD_HEAVY_UNIT=heavy-workload.slice \
  WORKLOAD_GUARD_STATE_FILE="$STATE_FILE" \
  python3 "$ROOT/bin/workload-router.py" >/tmp/linux-workload-guard-recovery.$$.log 2>&1 &
ROUTER_PID=$!
sleep 0.5
RECOVERY_START=$(python3 -c 'import time; print(time.monotonic())')
printf '%s' idle > "$PHASE_FILE"
for _ in $(seq 1 40); do
  if grep -qx "$WORK_PID" "$SOURCE_CGROUP/cgroup.procs" 2>/dev/null || grep -qx "$WORK_PID" "$PARENT_CGROUP/cgroup.procs" 2>/dev/null; then
    RECOVERY_END=$(python3 -c 'import time; print(time.monotonic())')
    python3 - "$RECOVERY_START" "$RECOVERY_END" <<'PY'
import sys
print(f'recovery routing: ok (idle-to-recovery {float(sys.argv[2])-float(sys.argv[1]):.2f}s)')
PY
    exit 0
  fi
  sleep 0.25
done
printf '%s\n' 'recovery routing: failed'
sed -n '1,100p' "/tmp/linux-workload-guard-recovery.$$.log" 2>/dev/null || true
exit 1
