#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v systemd-run >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1 || [ ! -f /sys/fs/cgroup/cgroup.controllers ] || ! systemctl --user show-environment >/dev/null 2>&1; then
  printf '%s\n' 'shadow routing: skipped (systemd/cgroup v2/user manager unavailable)'
  exit 0
fi
TAG="/tmp/lwg-shadow-e2e.$$"
SLICE_TAG="lwgshadow$(id -u)$(date +%s)$$"
PARENT_UNIT="$SLICE_TAG.slice"
RECOVERY_UNIT="$SLICE_TAG-recovery.slice"
HEAVY_UNIT="$SLICE_TAG-heavy.slice"
UNIT="$SLICE_TAG-worker.scope"
PID_FILE="$TAG.pid"; PHASE_FILE="$TAG.phase"; WORKER="$TAG.py"; LOG="$TAG.log"; STATE="$TAG.routes"
ROUTER_PID=''; WORK_PID=''
cleanup() {
  if [ -n "$ROUTER_PID" ]; then kill "$ROUTER_PID" 2>/dev/null || true; wait "$ROUTER_PID" 2>/dev/null || true; fi
  [ -n "$WORK_PID" ] && kill "$WORK_PID" 2>/dev/null || true
  systemctl --user stop "$UNIT" "$RECOVERY_UNIT" "$HEAVY_UNIT" "$PARENT_UNIT" >/dev/null 2>&1 || true
  systemctl --user reset-failed "$UNIT" "$RECOVERY_UNIT" "$HEAVY_UNIT" "$PARENT_UNIT" >/dev/null 2>&1 || true
  rm -f "$PID_FILE" "$PHASE_FILE" "$WORKER" "$LOG" "$STATE" "$PHASE_FILE.done"
}
trap cleanup EXIT INT TERM
systemctl --user start "$PARENT_UNIT" "$RECOVERY_UNIT" "$HEAVY_UNIT"
if ! systemctl --user set-property "$PARENT_UNIT" CPUQuota=150% >/dev/null 2>&1 ||
   ! systemctl --user set-property "$HEAVY_UNIT" CPUQuota=100% >/dev/null 2>&1; then
  printf '%s\n' 'shadow routing: skipped (test environment rejects isolated CPUQuota changes)'
  exit 0
fi
cat > "$WORKER" <<PY
import os,time
phase_file="$PHASE_FILE"
open("$PID_FILE","w").write(str(os.getpid()))
while True:
 p=open(phase_file).read().strip() if os.path.exists(phase_file) else "idle"
 if p in ("burst","hot"):
  end=time.monotonic()+(0.12 if p=="burst" else 60)
  while time.monotonic()<end and open(phase_file).read().strip()==p: pass
  if p=="burst":
   open(phase_file+".done","w").write("done"); open(phase_file,"w").write("idle")
 else: time.sleep(0.02)
PY
printf idle > "$PHASE_FILE"
systemd-run --user --scope --slice="$PARENT_UNIT" --unit="$UNIT" python3 "$WORKER" >/dev/null 2>&1 &
for _ in $(seq 1 50); do [ -s "$PID_FILE" ] && break; sleep 0.1; done
WORK_PID=$(cat "$PID_FILE")
env WORKLOAD_GUARD_SAMPLE_SEC=0.25 WORKLOAD_GUARD_SUSTAINED_SAMPLES=3 WORKLOAD_GUARD_CPU_THRESHOLD=10 \
 WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD=35 WORKLOAD_GUARD_RECOVERY_SAMPLES=2 WORKLOAD_GUARD_RECOVERY_DWELL_SEC=1 \
 WORKLOAD_GUARD_SHADOW=true WORKLOAD_GUARD_SHADOW_EWMA_ALPHA=0.35 WORKLOAD_GUARD_SHADOW_ROUTE_THRESHOLD=10 \
 WORKLOAD_GUARD_SHADOW_CLEAR_THRESHOLD=5 WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES=3 \
 WORKLOAD_GUARD_SHADOW_RECOVERY_SAMPLES=2 WORKLOAD_GUARD_SHADOW_RECOVERY_DWELL_SEC=1 \
 WORKLOAD_GUARD_PARENT_UNIT="$PARENT_UNIT" WORKLOAD_GUARD_RECOVERY_UNIT="$RECOVERY_UNIT" WORKLOAD_GUARD_HEAVY_UNIT="$HEAVY_UNIT" \
 WORKLOAD_GUARD_STATE_FILE="$STATE" python3 "$ROOT/bin/workload-router.py" >"$LOG" 2>&1 &
ROUTER_PID=$!
HEAVY_CGROUP=$(systemctl --user show "$HEAVY_UNIT" -p ControlGroup --value); HEAVY_CGROUP="/sys/fs/cgroup$HEAVY_CGROUP"
printf burst > "$PHASE_FILE"
for _ in $(seq 1 40); do [ -s "$PHASE_FILE.done" ] && break; sleep 0.05; done
sleep 0.5
if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null || grep -q "shadow decision=route pid=$WORK_PID " "$LOG"; then
 echo 'shadow routing: failed (120ms burst caused a route)'; cat "$LOG"; exit 1
fi
HOT_START=$(python3 -c 'import time;print(time.monotonic())'); printf hot > "$PHASE_FILE"
for _ in $(seq 1 60); do
 if grep -q "shadow decision=route pid=$WORK_PID " "$LOG" && grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then break; fi
 sleep 0.25
done
grep -q "shadow decision=route pid=$WORK_PID " "$LOG"
grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs"
ROUTED_AT=$(python3 -c 'import time;print(time.monotonic())')
printf idle > "$PHASE_FILE"
for _ in $(seq 1 60); do
 if grep -q "shadow decision=recover pid=$WORK_PID " "$LOG" && ! grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then break; fi
 sleep 0.25
done
grep -q "shadow decision=recover pid=$WORK_PID " "$LOG"
if grep -qx "$WORK_PID" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then echo 'shadow routing: failed (production recovery timeout)'; cat "$LOG"; exit 1; fi
RECOVERED_AT=$(python3 -c 'import time;print(time.monotonic())')
python3 - "$HOT_START" "$ROUTED_AT" "$RECOVERED_AT" <<'PY'
import sys
print(f"shadow routing: ok (120ms burst rejected; sustained route observed in {float(sys.argv[2])-float(sys.argv[1]):.2f}s; recovery completed in {float(sys.argv[3])-float(sys.argv[2]):.2f}s including idle phase)")
PY
