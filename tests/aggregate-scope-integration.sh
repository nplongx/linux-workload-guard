#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v systemd-run >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1 || [ ! -f /sys/fs/cgroup/cgroup.controllers ]; then
  echo 'aggregate scope integration: SKIP (requires a live user systemd manager and cgroup v2)'
  [ "${WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER:-0}" != 1 ] || exit 1
  exit 0
fi
TAG="lwgagg$(id -u)$(date +%s)$$"
PARENT="$TAG-parent.slice"; HEAVY="$TAG-parent-heavy.slice"; RECOVERY="$TAG-parent-recovery.slice"
SCOPE1="lwg-workload-$TAG-worker-1.scope"; SCOPE2="lwg-workload-$TAG-worker-2.scope"
CONTROL="lwg-control-$TAG.scope"
UNIT_DIR="$HOME/.config/systemd/user"
PARENT_FILE="$UNIT_DIR/$PARENT"; HEAVY_FILE="$UNIT_DIR/$HEAVY"; RECOVERY_FILE="$UNIT_DIR/$RECOVERY"
PHASE="/tmp/$TAG.phase"; WORKER="/tmp/$TAG-worker.py"; IDLE="/tmp/$TAG-idle.py"
ROUTES="/tmp/$TAG.routes.tsv"; SCOPE_STATE="$ROUTES.scopes.tsv"; LOG="/tmp/$TAG.log"
ROUTER_PID=''; CREATED=0
cleanup() {
  rc=$?; set +e
  [ -n "$ROUTER_PID" ] && kill -TERM "$ROUTER_PID" 2>/dev/null
  [ -n "$ROUTER_PID" ] && wait "$ROUTER_PID" 2>/dev/null
  systemctl --user stop "$SCOPE1" "$SCOPE2" "$CONTROL" "$PARENT" "$HEAVY" "$RECOVERY" "$TAG.slice" >/dev/null 2>&1
  if [ "$CREATED" -eq 1 ]; then rm -f "$PARENT_FILE" "$HEAVY_FILE" "$RECOVERY_FILE"; systemctl --user daemon-reload >/dev/null 2>&1; fi
  rm -f "$PHASE" "$WORKER" "$IDLE" "$ROUTES" "$SCOPE_STATE" "$SCOPE_STATE.tmp" "$LOG"
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM
mkdir -p "$UNIT_DIR"
for f in "$PARENT_FILE" "$HEAVY_FILE" "$RECOVERY_FILE"; do [ ! -e "$f" ] || { echo "refusing to overwrite $f" >&2; exit 1; }; done
cat > "$PARENT_FILE" <<EOF
[Unit]
Description=Temporary aggregate scope integration parent
[Slice]
CPUQuota=100%
CPUWeight=1000
EOF
cat > "$HEAVY_FILE" <<EOF
[Unit]
Description=Temporary aggregate scope integration heavy child
[Slice]
CPUQuota=100%
CPUWeight=100
EOF
cat > "$RECOVERY_FILE" <<EOF
[Unit]
Description=Temporary aggregate scope integration recovery child
[Slice]
CPUWeight=1000
EOF
CREATED=1
systemctl --user daemon-reload
systemctl --user start "$PARENT" "$HEAVY" "$RECOVERY"
PARENT_CG=$(systemctl --user show "$PARENT" -p ControlGroup --value)
if [ -z "$PARENT_CG" ] || ! grep -qw cpu "/sys/fs/cgroup${PARENT_CG%/}/cgroup.controllers"; then
  echo 'aggregate scope integration: SKIP (CPU controller is not delegated to the temporary user slice)'
  [ "${WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER:-0}" != 1 ] || exit 1
  exit 0
fi
PARENT_QUOTA=$(systemctl --user show "$PARENT" -p CPUQuotaPerSecUSec --value)
[ "$PARENT_QUOTA" = 1s ] || { echo "aggregate scope integration: FAIL parent quota readback=$PARENT_QUOTA, expected 1s" >&2; exit 1; }
printf hot > "$PHASE"
cat > "$WORKER" <<'PY'
import pathlib,sys,time
phase=pathlib.Path(sys.argv[1]); x=0x123456789abcdef
while True:
    try: hot=phase.read_text().strip()=="hot"
    except OSError: hot=False
    if hot:
        for i in range(10000): x=(x*1664525+i+1013904223)&0xffffffff
    else: time.sleep(.02)
PY
cat > "$IDLE" <<'PY'
import time
while True: time.sleep(1)
PY
systemd-run --user --scope --quiet --unit="$SCOPE1" --slice="$PARENT" --description='Linux Workload Guard aggregate candidate' -- python3 "$WORKER" "$PHASE" >/dev/null 2>&1 &
systemd-run --user --scope --quiet --unit="$SCOPE2" --slice="$PARENT" --description='Linux Workload Guard aggregate candidate' -- python3 "$WORKER" "$PHASE" >/dev/null 2>&1 &
systemd-run --user --scope --quiet --unit="$CONTROL" --slice="$PARENT" -- python3 "$IDLE" >/dev/null 2>&1 &
env WORKLOAD_GUARD_PARENT_UNIT="$PARENT" WORKLOAD_GUARD_HEAVY_UNIT="$HEAVY" \
  WORKLOAD_GUARD_RECOVERY_UNIT="$RECOVERY" WORKLOAD_GUARD_STATE_FILE="$ROUTES" \
  WORKLOAD_GUARD_SCOPE_STATE_FILE="$SCOPE_STATE" WORKLOAD_GUARD_SAMPLE_SEC=0.5 \
  WORKLOAD_GUARD_CPU_THRESHOLD=100 WORKLOAD_GUARD_SUSTAINED_SAMPLES=100 \
  WORKLOAD_GUARD_AGGREGATE_ROUTING=true WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO=0.85 \
  WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO=0.65 WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU=20 \
  WORKLOAD_GUARD_AGGREGATE_SUSTAINED_SAMPLES=3 WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT=20 \
  WORKLOAD_GUARD_DYNAMIC_QUOTA=false WORKLOAD_GUARD_SHADOW=false \
  python3 "$ROOT/bin/workload-router.py" > "$LOG" 2>&1 &
ROUTER_PID=$!

read_weight() {
  weight=$(systemctl --user show "$1" -p CPUWeight --value 2>/dev/null || true)
  case "$weight" in ''|*[!0-9]*)
    cg=$(systemctl --user show "$1" -p ControlGroup --value 2>/dev/null || true)
    [ -n "$cg" ] || return 0
    weight=$(cat "/sys/fs/cgroup${cg%/}/cpu.weight" 2>/dev/null || true)
    ;;
  esac
  printf '%s\n' "$weight"
}
R1=; R2=
for _ in $(seq 1 40); do
  R1=$(read_weight "$SCOPE1"); R2=$(read_weight "$SCOPE2")
  [ "$R1" = 20 ] && [ "$R2" = 20 ] && break
  sleep 0.5
done
if [ "$R1" != 20 ] || [ "$R2" != 20 ]; then
  cat "$LOG" >&2; echo "aggregate scope integration: FAIL route weights worker1=$R1 worker2=$R2" >&2; exit 1
fi
CONTROL_WEIGHT=$(read_weight "$CONTROL")
[ "$CONTROL_WEIGHT" = 100 ] || { echo "aggregate scope integration: FAIL changed out-of-scope weight=$CONTROL_WEIGHT" >&2; exit 1; }
printf idle > "$PHASE"
R1=; R2=
for _ in $(seq 1 40); do
  R1=$(read_weight "$SCOPE1"); R2=$(read_weight "$SCOPE2")
  [ "$R1" = 100 ] && [ "$R2" = 100 ] && break
  sleep 0.5
done
if [ "$R1" != 100 ] || [ "$R2" != 100 ]; then
  cat "$LOG" >&2; echo "aggregate scope integration: FAIL recovery weights worker1=$R1 worker2=$R2" >&2; exit 1
fi
grep -q 'aggregate scope decision=aggregate-saturation' "$LOG"
grep -q 'aggregate scope restored' "$LOG"
echo 'aggregate scope integration: PASS (100% parent cap, both churn-safe scopes weighted, unrelated scope unchanged, original weights restored)'
