#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v systemd-run >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1 ||
   [ ! -f /sys/fs/cgroup/cgroup.controllers ] || ! systemctl --user show-environment >/dev/null 2>&1; then
  printf '%s\n' 'controller integration: skipped (systemd user manager/cgroup v2 unavailable)'
  exit 0
fi

# Unique test-only slice units isolate dynamic quota writes from the user's real
# protected/heavy slices and from the installed workload-router.service.
TAG="lwgcontrollerit$(id -u)$(date +%s)$$"
RESULT_DIR=${WORKLOAD_GUARD_CONTROLLER_RESULTS_DIR:-}
if [ -n "$RESULT_DIR" ]; then
  case "$RESULT_DIR" in /*) ;; *) RESULT_DIR="$ROOT/$RESULT_DIR" ;; esac
  mkdir -p "$(dirname -- "$RESULT_DIR")"
  if ! mkdir "$RESULT_DIR"; then
    printf 'controller integration: refusing to overwrite existing results path: %s\n' "$RESULT_DIR" >&2
    exit 1
  fi
fi
PARENT_UNIT="$TAG-parent.slice"
RECOVERY_UNIT="$TAG-parent-recovery.slice"
HEAVY_UNIT="$TAG-heavy.slice"
SCOPE_1="$TAG-worker-1.scope"
SCOPE_2="$TAG-worker-2.scope"
PROBE_SCOPE="$TAG-probe.scope"
UNIT_DIR="$HOME/.config/systemd/user"
PARENT_FILE="$UNIT_DIR/$PARENT_UNIT"
RECOVERY_FILE="$UNIT_DIR/$RECOVERY_UNIT"
HEAVY_FILE="$UNIT_DIR/$HEAVY_UNIT"
PID_FILE_1="/tmp/$TAG-worker-1.pid"
PID_FILE_2="/tmp/$TAG-worker-2.pid"
PROBE_PID_FILE="/tmp/$TAG-probe.pid"
PHASE_FILE="/tmp/$TAG.phase"
WORKER="/tmp/$TAG.py"
PROBE="/tmp/$TAG-probe.py"
PROBE_SAMPLES="/tmp/$TAG-probe.tsv"
LOG="/tmp/$TAG.log"
STATE="/tmp/$TAG.routes.tsv"
ROUTER_PID=''; WORK_PID_1=''; WORK_PID_2=''; PROBE_PID=''; UNITS_CREATED=0
TEST_DYNAMIC_QUOTA=true

if [ -e "$PARENT_FILE" ] || [ -e "$RECOVERY_FILE" ] || [ -e "$HEAVY_FILE" ]; then
  printf '%s\n' 'controller integration: skipped (unique test unit path already exists)'
  exit 0
fi
cleanup() {
  rc=$?
  set +e
  [ -n "$ROUTER_PID" ] && kill "$ROUTER_PID" 2>/dev/null
  [ -n "$WORK_PID_1" ] && kill "$WORK_PID_1" 2>/dev/null
  [ -n "$WORK_PID_2" ] && kill "$WORK_PID_2" 2>/dev/null
  [ -n "$PROBE_PID" ] && kill "$PROBE_PID" 2>/dev/null
  systemctl --user stop "$SCOPE_1" "$SCOPE_2" "$PROBE_SCOPE" "$PARENT_UNIT" "$RECOVERY_UNIT" "$HEAVY_UNIT" >/dev/null 2>&1
  # systemd synthesizes the tag's ancestor slice from the hyphenated unit
  # names. Stop that per-run ancestor too, but never stop shared parent slices.
  systemctl --user stop "$TAG.slice" >/dev/null 2>&1
  if [ "$UNITS_CREATED" -eq 1 ]; then
    rm -f "$PARENT_FILE" "$RECOVERY_FILE" "$HEAVY_FILE"
    systemctl --user daemon-reload >/dev/null 2>&1
  fi
  if [ "$rc" -ne 0 ] && [ -f "$LOG" ]; then
    failure_log="/tmp/$TAG.failure.log"
    cp "$LOG" "$failure_log"
    printf 'controller integration failure log preserved: %s\n' "$failure_log" >&2
    if [ -n "$RESULT_DIR" ]; then
      cp "$LOG" "$RESULT_DIR/router-failure.log"
      {
        printf 'result=FAIL\nexit_code=%s\nrecorded_at=%s\n' "$rc" "$(date --iso-8601=seconds)"
        printf 'host=%s\n' "$(uname -a)"
        printf 'command=WORKLOAD_GUARD_CONTROLLER_RESULTS_DIR=%s ./tests/controller-integration.sh\n' "$RESULT_DIR"
      } > "$RESULT_DIR/failure.txt"
    fi
  fi
  rm -f "$PID_FILE_1" "$PID_FILE_2" "$PID_FILE_1.done" "$PID_FILE_2.done" "$PID_FILE_1.throughput.tsv" "$PID_FILE_2.throughput.tsv" "$PROBE_PID_FILE" "$PHASE_FILE" "$WORKER" "$PROBE" "$PROBE_SAMPLES" "$LOG" "$STATE"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$UNIT_DIR"
cat > "$PARENT_FILE" <<EOF
[Unit]
Description=Temporary Linux Workload Guard integration parent
[Slice]
CPUQuota=150%
CPUWeight=1000
EOF
cat > "$HEAVY_FILE" <<EOF
[Unit]
Description=Temporary Linux Workload Guard integration heavy slice
[Slice]
CPUQuota=100%
CPUWeight=100
EOF
cat > "$RECOVERY_FILE" <<EOF
[Unit]
Description=Temporary Linux Workload Guard integration recovery leaf
[Slice]
CPUWeight=1000
EOF
UNITS_CREATED=1
systemctl --user daemon-reload
systemctl --user start "$PARENT_UNIT" "$RECOVERY_UNIT" "$HEAVY_UNIT"

# Exercise the production PSI boundary all the way through the real quota
# actuator/readback, but only on this test's temporary heavy slice and without
# generating load. This is deterministic decision/actuation coverage, not live
# host PSI-threshold acceptance evidence.
if ! env \
  WORKLOAD_GUARD_QUOTA_MIN=100 WORKLOAD_GUARD_QUOTA_MAX=400 WORKLOAD_GUARD_QUOTA_STEP=50 \
  WORKLOAD_GUARD_QUOTA_PRESSURE_LOW=0.10 WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH=0.40 \
  WORKLOAD_GUARD_QUOTA_INTERVAL_SEC=5 WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC=10 \
  WORKLOAD_GUARD_QUOTA_SCHED_DELAY_LOW_MS=5 WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS=20 \
  python3 - "$ROOT/bin/workload-router.py" "$HEAVY_UNIT" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("lwg_threshold_actuation", sys.argv[1])
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)
unit = sys.argv[2]
if router.QUOTA_PRESSURE_HIGH != 0.40:
    raise SystemExit("production PSI-high threshold is not 0.40")
original = router.unit_quota(unit)
if original != 100.0:
    raise SystemExit(f"unexpected temporary-slice baseline quota: {original!r}")
base = {"usage_usec": 1_000_000, "nr_periods": 100, "nr_throttled": 10}
sample = {"usage_usec": 1_500_000, "nr_periods": 105, "nr_throttled": 12}

def decide(current, pressure, demand=0.8, elapsed=5.0, scheduler_ms=0.0):
    decision, reason = router.quota_decision_from_sample(
        current, base, sample, elapsed, pressure, scheduler_ms, demand,
    )
    if reason != "ok" or decision is None:
        raise AssertionError(f"unexpected decision failure: {reason}")
    return decision["target"]

def apply(expected):
    if not router.set_unit_quota(unit, expected):
        raise AssertionError(f"temporary-slice quota actuation/readback failed: {expected}")
    actual = router.unit_quota(unit)
    if actual != float(expected):
        raise AssertionError(f"temporary-slice quota mismatch: expected={expected} actual={actual}")

try:
    apply(300)
    if decide(300, 0.3999) != 300 or router.unit_quota(unit) != 300.0:
        raise AssertionError("PSI just below 0.40 must hold the quota")
    if decide(300, 0.40) != 250:
        raise AssertionError("PSI exactly at 0.40 must request one bounded decrease")
    apply(250)
    if decide(250, 0.4001) != 200:
        raise AssertionError("PSI above 0.40 must request one bounded decrease")
    apply(200)
    if decide(200, None) != 200 or router.unit_quota(unit) != 200.0:
        raise AssertionError("missing PSI plus low scheduler delay must not authorize a quota change")
    stale, reason = router.quota_decision_from_sample(
        200, base, sample, 60.0, 0.40, 0.0, 0.8,
    )
    if stale is not None or reason != "stale-or-invalid-sample-window" or router.unit_quota(unit) != 200.0:
        raise AssertionError("stale telemetry must not authorize quota actuation")
    if decide(200, 0.05, demand=0.40) != 250:
        raise AssertionError("low PSI plus valid scheduler signal and meaningful throttled demand should corroborate growth")
    apply(250)
    print("production PSI boundary: below/equal/above 0.40; missing/stale hold; bounded quota readback passed in temporary slice")
finally:
    if not router.set_unit_quota(unit, original):
        raise RuntimeError("failed to restore temporary-slice quota after PSI threshold test")
PY
then
  echo 'controller integration: failed (production PSI threshold decision/actuation check)' >&2
  exit 1
fi

cat > "$WORKER" <<PY
import os, time
phase_file = "$PHASE_FILE"
pid_file = os.environ["WORKLOAD_GUARD_TEST_PID_FILE"]
stats_file = pid_file + ".throughput.tsv"
open(pid_file, "w").write(str(os.getpid()))
counter = 1
iterations = 0
last_phase = "idle"
phase_started = time.monotonic()
burst_completed = False
while True:
    try:
        phase = open(phase_file).read().strip()
    except OSError:
        phase = "idle"
    now = time.monotonic()
    if phase != last_phase:
        if last_phase.endswith("_hot"):
            with open(stats_file, "a") as output:
                output.write(f"{last_phase}\\t{iterations}\\t{now - phase_started:.6f}\\n")
        last_phase = phase
        phase_started = now
        iterations = 0
        burst_completed = False
    if phase == "burst":
        if not burst_completed:
            end = time.monotonic() + 0.12
            while time.monotonic() < end:
                counter = (counter * 1664525 + 1013904223) & 0xffffffff
            burst_completed = True
            open(pid_file + ".done", "w").write("done")
        else:
            time.sleep(0.02)
    elif phase.endswith("_hot"):
        while True:
            try:
                if open(phase_file).read().strip() != phase:
                    break
            except OSError:
                break
            counter = (counter * 1664525 + 1013904223) & 0xffffffff
            iterations += 1
    else:
        time.sleep(0.02)
PY

cat > "$PROBE" <<PY
import os, time
phase_file = "$PHASE_FILE"
sample_file = "$PROBE_SAMPLES"
pid_file = "$PROBE_PID_FILE"
open(pid_file, "w").write(str(os.getpid()))
interval = 0.020
deadline = time.monotonic() + interval
value = 1
with open(sample_file, "w", buffering=1) as output:
    output.write("phase\\twakeup_lateness_ms\\twork_duration_ms\\n")
    while True:
        now = time.monotonic()
        try:
            phase_name = open(phase_file).read().strip()
        except OSError:
            phase_name = "idle"
        phase = "baseline" if phase_name == "baseline_hot" else ("enabled" if phase_name == "enabled_hot" else None)
        lateness_ms = max(0.0, now - deadline) * 1000.0
        work_start = time.monotonic()
        for _ in range(3000):
            value = (value * 1664525 + 1013904223) & 0xffffffff
        duration_ms = (time.monotonic() - work_start) * 1000.0
        if phase:
            output.write(f"{phase}\\t{lateness_ms:.6f}\\t{duration_ms:.6f}\\n")
        deadline += interval
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        else:
            deadline = time.monotonic()
PY
printf idle > "$PHASE_FILE"
rm -f "$PID_FILE_1.done" "$PID_FILE_2.done" "$PID_FILE_1.throughput.tsv" "$PID_FILE_2.throughput.tsv" "$PROBE_SAMPLES"
systemd-run --user --scope --slice="$PARENT_UNIT" --unit="$SCOPE_1" env WORKLOAD_GUARD_TEST_PID_FILE="$PID_FILE_1" python3 "$WORKER" >/dev/null 2>&1 &
systemd-run --user --scope --slice="$PARENT_UNIT" --unit="$SCOPE_2" env WORKLOAD_GUARD_TEST_PID_FILE="$PID_FILE_2" python3 "$WORKER" >/dev/null 2>&1 &
for _ in $(seq 1 50); do [ -s "$PID_FILE_1" ] && [ -s "$PID_FILE_2" ] && break; sleep 0.1; done
test -s "$PID_FILE_1" && test -s "$PID_FILE_2"
WORK_PID_1=$(cat "$PID_FILE_1")
WORK_PID_2=$(cat "$PID_FILE_2")
systemd-run --user --scope --slice="$PARENT_UNIT" --unit="$PROBE_SCOPE" python3 "$PROBE" >/dev/null 2>&1 &
for _ in $(seq 1 50); do [ -s "$PROBE_PID_FILE" ] && break; sleep 0.1; done
test -s "$PROBE_PID_FILE"
PROBE_PID=$(cat "$PROBE_PID_FILE")

mono_ns() { python3 -c 'import time; print(time.monotonic_ns())'; }
read_psi_avg10() {
  awk '/^some / { for (i=1;i<=NF;i++) if ($i ~ /^avg10=/) { split($i,a,"="); print a[2]; exit } }' /proc/pressure/cpu
}
read_router_stats() {
  python3 - "$ROUTER_PID" <<'PY'
import os, sys
pid = sys.argv[1]
text = open(f"/proc/{pid}/stat").read()
fields = text[text.rfind(")") + 2:].split()
ticks = int(fields[11]) + int(fields[12])
starttime = int(fields[19])
with open(f"/proc/{pid}/status") as f:
    status = {row.split(":", 1)[0]: row.split(":", 1)[1].strip() for row in f if ":" in row}
with open(f"/proc/{pid}/schedstat") as f:
    sched_ns = int(f.read().split()[1])
rss = int(status.get("VmRSS", "0 kB").split()[0])
hwm = int(status.get("VmHWM", "0 kB").split()[0])
print(ticks, rss, hwm, sched_ns, starttime)
PY
}

# Bounded two-worker baseline with no router process running. Probe is placed
# in the same protected test slice so the later routed comparison is paired.
BASELINE_PSI_START=$(read_psi_avg10)
BASELINE_START_NS=$(mono_ns)
printf baseline_hot > "$PHASE_FILE"
sleep 5
BASELINE_END_NS=$(mono_ns)
printf idle > "$PHASE_FILE"
BASELINE_DURATION_SEC=$(awk -v s="$BASELINE_START_NS" -v e="$BASELINE_END_NS" 'BEGIN { printf "%.6f", (e-s)/1000000000 }')
BASELINE_PSI_END=$(read_psi_avg10)
sleep 0.25

# Test-only pressure thresholds make this a bounded low-pressure quota/recovery
# exercise. Production defaults are independently pinned by controller-hardening.py.
start_router() {
  env \
    WORKLOAD_GUARD_PARENT_UNIT="$PARENT_UNIT" \
    WORKLOAD_GUARD_RECOVERY_UNIT="$RECOVERY_UNIT" \
    WORKLOAD_GUARD_HEAVY_UNIT="$HEAVY_UNIT" \
    WORKLOAD_GUARD_STATE_FILE="$STATE" \
    WORKLOAD_GUARD_SAMPLE_SEC=0.25 \
    WORKLOAD_GUARD_SUSTAINED_SAMPLES=3 \
    WORKLOAD_GUARD_CPU_THRESHOLD=10 \
    WORKLOAD_GUARD_COOLDOWN_SEC=1 \
    WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD=35 \
    WORKLOAD_GUARD_RECOVERY_SAMPLES=2 \
    WORKLOAD_GUARD_RECOVERY_DWELL_SEC=1 \
    WORKLOAD_GUARD_SHADOW=true \
    WORKLOAD_GUARD_SHADOW_ROUTE_THRESHOLD=10 \
    WORKLOAD_GUARD_SHADOW_CLEAR_THRESHOLD=5 \
    WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES=3 \
    WORKLOAD_GUARD_SHADOW_RECOVERY_SAMPLES=2 \
    WORKLOAD_GUARD_SHADOW_RECOVERY_DWELL_SEC=1 \
    WORKLOAD_GUARD_DYNAMIC_QUOTA="$TEST_DYNAMIC_QUOTA" \
    WORKLOAD_GUARD_AIMD_SHADOW=true \
    WORKLOAD_GUARD_QUOTA_INTERVAL_SEC=1 \
    WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC=2 \
    WORKLOAD_GUARD_QUOTA_PRESSURE_LOW=0.99 \
    WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH=1.0 \
    WORKLOAD_GUARD_QUOTA_SCHED_DELAY_LOW_MS=99999 \
    WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS=100000 \
    python3 "$ROOT/bin/workload-router.py" >>"$LOG" 2>&1 &
  ROUTER_PID=$!
}
: > "$LOG"
start_router
for _ in $(seq 1 30); do grep -q 'started threshold=' "$LOG" && break; sleep 0.1; done
grep -q 'started threshold=' "$LOG"

HEAVY_CGROUP=$(systemctl --user show "$HEAVY_UNIT" -p ControlGroup --value)
test -n "$HEAVY_CGROUP"
HEAVY_CGROUP="/sys/fs/cgroup$HEAVY_CGROUP"
RECOVERY_CGROUP=$(systemctl --user show "$RECOVERY_UNIT" -p ControlGroup --value)
test -n "$RECOVERY_CGROUP"
RECOVERY_CGROUP="/sys/fs/cgroup$RECOVERY_CGROUP"

# Some hosted/containerized runners have cgroup v2 and a user systemd manager,
# but do not delegate the CPU controller counters needed by the dynamic quota
# decision (or reject CPUQuota writes). Distinguish that environment limitation
# from a controller regression before asserting live actuation.
CPU_STAT_READY=0
for _ in $(seq 1 20); do
  if python3 - "$HEAVY_CGROUP/cpu.stat" <<'PY'
import sys
try:
    data = {}
    for line in open(sys.argv[1]):
        fields = line.split()
        if len(fields) != 2 or fields[0] in data:
            raise ValueError("malformed cpu.stat")
        data[fields[0]] = int(fields[1])
    required = ("usage_usec", "nr_periods", "nr_throttled")
    if any(key not in data or data[key] < 0 for key in required):
        raise ValueError("required CPU controller counters unavailable")
except (OSError, ValueError):
    raise SystemExit(1)
PY
  then CPU_STAT_READY=1; break; fi
  sleep 0.1
done
if [ "$CPU_STAT_READY" -ne 1 ]; then
  printf '%s\n' 'controller integration: skipped (test cgroup does not expose usage_usec/nr_periods/nr_throttled required for quota decisions)'
  exit 0
fi
if ! systemctl --user set-property "$HEAVY_UNIT" CPUQuota=100% >/dev/null 2>&1; then
  printf '%s\n' 'controller integration: skipped (test environment rejects isolated CPUQuota property changes)'
  exit 0
fi
QUOTA_PROPERTY=$(systemctl --user show "$HEAVY_UNIT" -p CPUQuotaPerSecUSec --value 2>/dev/null || true)
case "$QUOTA_PROPERTY" in ''|infinity|infinity\ *)
  printf '%s\n' 'controller integration: skipped (isolated test quota property is unavailable)'
  exit 0
  ;;
esac

# A brief burst must be rejected before sustained load is introduced.
printf burst > "$PHASE_FILE"
for _ in $(seq 1 40); do [ -s "$PID_FILE_1.done" ] && [ -s "$PID_FILE_2.done" ] && break; sleep 0.05; done
test -s "$PID_FILE_1.done" && test -s "$PID_FILE_2.done"
sleep 0.5
if grep -Eq "^($WORK_PID_1|$WORK_PID_2)$" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
  echo 'controller integration: failed (120ms burst was routed)' >&2
  sed -n '1,120p' "$LOG" >&2
  exit 1
fi

set -- $(read_router_stats)
ROUTER_START_TICKS=$1; ROUTER_START_RSS_KIB=$2; ROUTER_START_HWM_KIB=$3; ROUTER_START_SCHED_NS=$4; ROUTER_START_STARTTIME=$5
ENABLED_PSI_START=$(read_psi_avg10)
ENABLED_START_NS=$(mono_ns)
printf enabled_hot > "$PHASE_FILE"
ROUTED_1=0; ROUTED_2=0
ROUTE_DETECTION_END_NS=''
for _ in $(seq 1 60); do
  grep -qx "$WORK_PID_1" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null && ROUTED_1=1 || true
  grep -qx "$WORK_PID_2" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null && ROUTED_2=1 || true
  if [ "$ROUTED_1" -eq 1 ] && [ "$ROUTED_2" -eq 1 ]; then ROUTE_DETECTION_END_NS=$(mono_ns); break; fi
  sleep 0.25
done
if [ "$ROUTED_1" -ne 1 ] || [ "$ROUTED_2" -ne 1 ]; then
  echo "controller integration: failed (multi-worker sustained routing incomplete: $ROUTED_1/2 routed)" >&2
  sed -n '1,160p' "$LOG" >&2
  exit 1
fi

# Require a real quota increase on the isolated test slice, not just a logged
# shadow proposal; this exercises router demand + valid cgroup counters together.
INCREASED=0
QUOTA_CHANGE_NS=''
for _ in $(seq 1 48); do
  if grep -Eq "quota changed unit=$HEAVY_UNIT .* new=(150|200|250|300|350|400)%" "$LOG"; then INCREASED=1; QUOTA_CHANGE_NS=$(mono_ns); break; fi
  sleep 0.25
done
if [ "$INCREASED" -ne 1 ]; then
  echo 'controller integration: failed (no bounded quota increase under sustained load)' >&2
  sed -n '1,180p' "$LOG" >&2
  exit 1
fi

# An operator can change the isolated test quota while the controller is live.
# The controller must adopt the observed value, reset its telemetry baseline,
# and hold through a fresh dwell window rather than acting on a stale cache.
systemctl --user set-property "$HEAVY_UNIT" CPUQuota=350% >/dev/null
EXTERNAL_QUOTA_RECONCILED=0
for _ in $(seq 1 36); do
  if grep -q 'quota property changed outside controller; adopting observed quota=350.0%' "$LOG"; then
    EXTERNAL_QUOTA_RECONCILED=1
    break
  fi
  sleep 0.1
done
if [ "$EXTERNAL_QUOTA_RECONCILED" -ne 1 ]; then
  echo 'controller integration: failed (out-of-band test-slice quota change was not reconciled)' >&2
  sed -n '1,220p' "$LOG" >&2
  exit 1
fi

# Match baseline window length for a useful, intentionally short paired probe.
while awk -v s="$ENABLED_START_NS" -v n="$(mono_ns)" 'BEGIN { exit !((n-s)/1000000000 < 5.0) }'; do sleep 0.1; done
set -- $(read_router_stats)
ROUTER_END_TICKS=$1; ROUTER_END_RSS_KIB=$2; ROUTER_END_HWM_KIB=$3; ROUTER_END_SCHED_NS=$4; ROUTER_END_STARTTIME=$5
if [ "$ROUTER_START_STARTTIME" != "$ROUTER_END_STARTTIME" ]; then
  echo 'controller integration: failed (router PID identity changed during overhead measurement window)' >&2
  exit 1
fi
ROUTER_PEAK_RSS_KIB=$(awk -v r0="$ROUTER_START_RSS_KIB" -v h0="$ROUTER_START_HWM_KIB" \
  -v r1="$ROUTER_END_RSS_KIB" -v h1="$ROUTER_END_HWM_KIB" \
  'BEGIN {m=r0; if(h0>m)m=h0; if(r1>m)m=r1; if(h1>m)m=h1; printf "%d",m}')
ENABLED_PSI_END=$(read_psi_avg10)
ENABLED_END_NS=$(mono_ns)
ENABLED_DURATION_SEC=$(awk -v s="$ENABLED_START_NS" -v e="$ENABLED_END_NS" 'BEGIN { printf "%.6f", (e-s)/1000000000 }')

# After collecting start/end CPU, RSS, and scheduler counters from the same
# router PID, restart the isolated controller while both workers remain routed.
# Persisted starttime identity must reattach them; then idle demand must recover.
STARTED_COUNT_BEFORE=$(grep -c 'started threshold=' "$LOG" || true)
kill "$ROUTER_PID"
wait "$ROUTER_PID" 2>/dev/null || true
ROUTER_PID=''
start_router
RESTARTED=0
for _ in $(seq 1 30); do
  STARTED_COUNT_NOW=$(grep -c 'started threshold=' "$LOG" || true)
  if [ "$STARTED_COUNT_NOW" -gt "$STARTED_COUNT_BEFORE" ] && \
     awk -F '\t' -v pid="$WORK_PID_1" '$1==pid {found=1} END {exit !found}' "$STATE" && \
     awk -F '\t' -v pid="$WORK_PID_2" '$1==pid {found=1} END {exit !found}' "$STATE"; then
    RESTARTED=1; break
  fi
  sleep 0.1
done
if [ "$RESTARTED" -ne 1 ] || ! grep -qx "$WORK_PID_1" "$HEAVY_CGROUP/cgroup.procs" || ! grep -qx "$WORK_PID_2" "$HEAVY_CGROUP/cgroup.procs"; then
  echo 'controller integration: failed (router restart did not preserve active routed worker identity/state)' >&2
  sed -n '1,220p' "$LOG" >&2
  exit 1
fi
LIFECYCLE_RESULT='external quota change reconciled; router restart with active routes recovered'

ROUTE_DETECTION_SEC=$(awk -v s="$ENABLED_START_NS" -v e="$ROUTE_DETECTION_END_NS" 'BEGIN { printf "%.3f", (e-s)/1000000000 }')
QUOTA_CHANGE_SEC=$(awk -v s="$ENABLED_START_NS" -v e="$QUOTA_CHANGE_NS" 'BEGIN { printf "%.3f", (e-s)/1000000000 }')

# Keep both workers alive but idle so current measured CPU demand—not the
# old route-trigger sample—must drive quota back down and both routes recover.
RECOVERY_START_NS=$(mono_ns)
printf idle > "$PHASE_FILE"
sleep 0.05
IDLE_LOG_LINES=$(wc -l < "$LOG")
sleep 1.25
DECREASED=0
RECOVERED=0
RECOVERY_END_NS=''
if tail -n +$((IDLE_LOG_LINES + 1)) "$LOG" | sed -n -E "s/.*quota changed unit=$HEAVY_UNIT old=([0-9]+)% new=([0-9]+)%.*/\1 \2/p" | awk '$2 + 0 < $1 + 0 { found=1 } END { exit !found }'; then DECREASED=1; fi
for _ in $(seq 1 64); do
  if tail -n +$((IDLE_LOG_LINES + 1)) "$LOG" | sed -n -E "s/.*quota changed unit=$HEAVY_UNIT old=([0-9]+)% new=([0-9]+)%.*/\1 \2/p" | awk '$2 + 0 < $1 + 0 { found=1 } END { exit !found }'; then DECREASED=1; fi
  if [ "$DECREASED" -eq 1 ] && ! grep -Eq "^($WORK_PID_1|$WORK_PID_2)$" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then RECOVERED=1; RECOVERY_END_NS=$(mono_ns); break; fi
  sleep 0.25
done
if [ "$DECREASED" -ne 1 ] || [ "$RECOVERED" -ne 1 ]; then
  echo 'controller integration: failed (quota did not decrease and recover after workers became idle)' >&2
  sed -n '1,240p' "$LOG" >&2
  exit 1
fi
RECOVERY_SEC=$(awk -v s="$RECOVERY_START_NS" -v e="$RECOVERY_END_NS" 'BEGIN { printf "%.3f", (e-s)/1000000000 }')
# After routes have recovered and demand has stayed low through a full quota
# dwell window, a new upward transition is a real rebound rather than an
# in-flight decision made from samples just before the phase change.
STEADY_LOG_LINES=$(wc -l < "$LOG")
sleep 2.25
if tail -n +$((STEADY_LOG_LINES + 1)) "$LOG" | sed -n -E "s/.*quota changed unit=$HEAVY_UNIT old=([0-9]+)% new=([0-9]+)%.*/\1 \2/p" | awk '$2 + 0 > $1 + 0 { found=1 } END { exit !found }'; then
  echo 'controller integration: failed (quota increased after routes recovered and the idle dwell window elapsed)' >&2
  sed -n '1,240p' "$LOG" >&2
  exit 1
fi
if grep -Eq "^($WORK_PID_1|$WORK_PID_2)$" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
  echo 'controller integration: failed (idle workers did not recover to parent slice)' >&2
  sed -n '1,220p' "$LOG" >&2
  exit 1
fi
grep -qx "$WORK_PID_1" "$RECOVERY_CGROUP/cgroup.procs"
grep -qx "$WORK_PID_2" "$RECOVERY_CGROUP/cgroup.procs"
grep -q 'action=log-only' "$LOG"
[ "$(grep -c 'unrouted pid=' "$LOG")" -ge 2 ]

# Exercise cgroup disappearance/recreation while the router stays running. This
# is best-effort only when systemd retains the empty slice cgroup after stop.
BASELINES_BEFORE=$(grep -c 'quota telemetry baseline reset' "$LOG" || true)
systemctl --user stop "$HEAVY_UNIT"
CGROUP_REMOVED=0
for _ in $(seq 1 40); do
  if [ ! -d "$HEAVY_CGROUP" ]; then CGROUP_REMOVED=1; break; fi
  sleep 0.1
done
if [ "$CGROUP_REMOVED" -eq 1 ]; then
  RECREATED=0
  for _ in $(seq 1 50); do
    if systemctl --user is-active --quiet "$HEAVY_UNIT" && [ -d "$HEAVY_CGROUP" ]; then RECREATED=1; break; fi
    sleep 0.1
  done
  if [ "$RECREATED" -ne 1 ]; then
    echo 'controller integration: failed (router did not recreate stopped heavy slice)' >&2
    sed -n '1,260p' "$LOG" >&2
    exit 1
  fi
  BASELINES_AFTER=$BASELINES_BEFORE
  for _ in $(seq 1 30); do
    BASELINES_AFTER=$(grep -c 'quota telemetry baseline reset' "$LOG" || true)
    [ "$BASELINES_AFTER" -gt "$BASELINES_BEFORE" ] && break
    sleep 0.1
  done
  if [ "$BASELINES_AFTER" -le "$BASELINES_BEFORE" ]; then
    echo 'controller integration: failed (quota telemetry baseline was not reset after cgroup recreation)' >&2
    sed -n '1,260p' "$LOG" >&2
    exit 1
  fi
  printf recreated_hot > "$PHASE_FILE"
  ROUTED_1=0; ROUTED_2=0
  for _ in $(seq 1 60); do
    grep -qx "$WORK_PID_1" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null && ROUTED_1=1 || true
    grep -qx "$WORK_PID_2" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null && ROUTED_2=1 || true
    [ "$ROUTED_1" -eq 1 ] && [ "$ROUTED_2" -eq 1 ] && break
    sleep 0.25
  done
  if [ "$ROUTED_1" -ne 1 ] || [ "$ROUTED_2" -ne 1 ]; then
    echo 'controller integration: failed (routing did not resume after cgroup recreation)' >&2
    sed -n '1,260p' "$LOG" >&2
    exit 1
  fi
  printf idle > "$PHASE_FILE"
  for _ in $(seq 1 64); do
    if ! grep -Eq "^($WORK_PID_1|$WORK_PID_2)$" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then break; fi
    sleep 0.25
  done
  if grep -Eq "^($WORK_PID_1|$WORK_PID_2)$" "$HEAVY_CGROUP/cgroup.procs" 2>/dev/null; then
    echo 'controller integration: failed (recreated-cgroup workers did not recover)' >&2
    sed -n '1,280p' "$LOG" >&2
    exit 1
  fi
  LIFECYCLE_RESULT="$LIFECYCLE_RESULT; cgroup recreated and routing/recovery resumed"
else
  systemctl --user start "$HEAVY_UNIT"
  LIFECYCLE_RESULT="$LIFECYCLE_RESULT; cgroup removal/recreation SKIP (systemd retained empty cgroup)"
fi

# Rollback contract: stop only the isolated test router, pin a known temporary
# quota, then restart it with dynamic actuation disabled. The controller must
# leave that operator-selected quota unchanged rather than silently restoring a
# hard-coded default or continuing to actuate from shadow proposals.
kill "$ROUTER_PID"
wait "$ROUTER_PID" 2>/dev/null || true
ROUTER_PID=''
systemctl --user set-property "$HEAVY_UNIT" CPUQuota=250% >/dev/null
ROLLBACK_QUOTA_BEFORE=$(systemctl --user show "$HEAVY_UNIT" -p CPUQuotaPerSecUSec --value)
ROLLBACK_LOG_LINES=$(wc -l < "$LOG")
STARTED_COUNT_ROLLBACK_BEFORE=$(grep -c 'started threshold=' "$LOG" || true)
TEST_DYNAMIC_QUOTA=false
start_router
for _ in $(seq 1 30); do
  if [ "$(grep -c 'started threshold=' "$LOG" || true)" -gt "$STARTED_COUNT_ROLLBACK_BEFORE" ]; then break; fi
  sleep 0.1
done
if [ "$(grep -c 'started threshold=' "$LOG" || true)" -le "$STARTED_COUNT_ROLLBACK_BEFORE" ]; then
  echo 'controller integration: failed (isolated router did not restart in dynamic-quota-disabled mode)' >&2
  sed -n '1,300p' "$LOG" >&2
  exit 1
fi
sleep 2.25
ROLLBACK_QUOTA_AFTER=$(systemctl --user show "$HEAVY_UNIT" -p CPUQuotaPerSecUSec --value)
if [ "$ROLLBACK_QUOTA_AFTER" != "$ROLLBACK_QUOTA_BEFORE" ]; then
  echo "controller integration: failed (dynamic-quota rollback changed isolated quota: before=$ROLLBACK_QUOTA_BEFORE after=$ROLLBACK_QUOTA_AFTER)" >&2
  sed -n '1,300p' "$LOG" >&2
  exit 1
fi
if tail -n +$((ROLLBACK_LOG_LINES + 1)) "$LOG" | grep -q "quota changed unit=$HEAVY_UNIT "; then
  echo 'controller integration: failed (quota actuation continued after dynamic quota was disabled)' >&2
  sed -n '1,300p' "$LOG" >&2
  exit 1
fi
LIFECYCLE_RESULT="$LIFECYCLE_RESULT; dynamic quota disabled without changing the recorded temporary quota"

if [ -n "$RESULT_DIR" ]; then
  cp "$PROBE_SAMPLES" "$RESULT_DIR/probe-samples.tsv"
  cp "$PID_FILE_1.throughput.tsv" "$RESULT_DIR/worker-1-throughput.tsv"
  cp "$PID_FILE_2.throughput.tsv" "$RESULT_DIR/worker-2-throughput.tsv"
  cp "$LOG" "$RESULT_DIR/router.log"
  {
    printf 'recorded_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'command=WORKLOAD_GUARD_CONTROLLER_RESULTS_DIR=%s ./tests/controller-integration.sh\n' "$RESULT_DIR"
    printf 'host=%s\n' "$(uname -a)"
    printf 'python=%s\n' "$(python3 --version 2>&1)"
    printf 'logical_cpus=%s\n' "$(nproc)"
    grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
    printf 'cgroup_version=2\n'
    systemctl --version | head -1
    printf 'baseline_duration_seconds=%s\n' "$BASELINE_DURATION_SEC"
    printf 'enabled_duration_seconds=%s\n' "$ENABLED_DURATION_SEC"
    printf 'test_parent_unit=%s\n' "$PARENT_UNIT"
    printf 'test_recovery_unit=%s\n' "$RECOVERY_UNIT"
    printf 'test_heavy_unit=%s\n' "$HEAVY_UNIT"
    printf 'test_dynamic_quota=true\n'
    printf 'test_dynamic_quota_rollback_phase=false\n'
    printf 'test_aimd_shadow=true\n'
    printf 'test_psi_threshold_override=0.99/1.00 (test-only; production .10/.40 not modified)\n'
    printf 'test_scheduler_delay_threshold_override=99999/100000ms (test-only)\n'
  } > "$RESULT_DIR/environment.txt"
fi
METRICS=$(python3 - "$PROBE_SAMPLES" "$PID_FILE_1.throughput.tsv" "$PID_FILE_2.throughput.tsv" "$LOG" "$HEAVY_UNIT" \
  "$BASELINE_DURATION_SEC" "$ENABLED_DURATION_SEC" "$ROUTER_START_TICKS" "$ROUTER_END_TICKS" \
  "$ROUTER_START_SCHED_NS" "$ROUTER_END_SCHED_NS" "$ROUTER_START_STARTTIME" "$ROUTER_END_STARTTIME" \
  "$ROUTER_START_RSS_KIB" "$ROUTER_END_RSS_KIB" "$ROUTER_PEAK_RSS_KIB" \
  "$BASELINE_PSI_START" "$BASELINE_PSI_END" "$ENABLED_PSI_START" "$ENABLED_PSI_END" \
  "$ROUTE_DETECTION_SEC" "$QUOTA_CHANGE_SEC" "$RECOVERY_SEC" <<'PY'
import math, os, re, sys
from collections import defaultdict

(probe_path, throughput_a, throughput_b, log_path, heavy_unit, baseline_duration,
 enabled_duration, start_ticks, end_ticks, start_sched, end_sched, start_identity,
 end_identity, start_rss,
 end_rss, peak_rss, baseline_psi_start, baseline_psi_end, enabled_psi_start,
 enabled_psi_end, route_detection, quota_change, recovery) = sys.argv[1:]
baseline_duration, enabled_duration = float(baseline_duration), float(enabled_duration)

def percentile(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[max(0, math.ceil(p * len(values)) - 1)]

samples = defaultdict(lambda: {"wake": [], "work": []})
with open(probe_path) as f:
    next(f, None)
    for row in f:
        fields = row.rstrip("\n").split("\t")
        if len(fields) != 3 or fields[0] not in ("baseline", "enabled"):
            continue
        try:
            samples[fields[0]]["wake"].append(float(fields[1]))
            samples[fields[0]]["work"].append(float(fields[2]))
        except ValueError:
            continue

def fmt(value):
    return "n/a" if not math.isfinite(value) else f"{value:.3f}"

for mode in ("baseline", "enabled"):
    wake, work = samples[mode]["wake"], samples[mode]["work"]
    if len(wake) < 10:
        raise SystemExit(f"controller integration metrics: insufficient {mode} probe samples ({len(wake)})")
    print(f"probe_{mode}: n={len(wake)} wakeup_lateness_ms p50/p95/p99="
          f"{fmt(percentile(wake,.50))}/{fmt(percentile(wake,.95))}/{fmt(percentile(wake,.99))}; "
          f"work_duration_ms p50/p95/p99="
          f"{fmt(percentile(work,.50))}/{fmt(percentile(work,.95))}/{fmt(percentile(work,.99))}")

throughput = defaultdict(lambda: {"iterations": 0, "durations": []})
for path in (throughput_a, throughput_b):
    if not os.path.exists(path):
        continue
    with open(path) as f:
        for row in f:
            fields = row.strip().split("\t")
            if len(fields) != 3 or fields[0] not in ("baseline_hot", "enabled_hot"):
                continue
            try:
                key = "baseline" if fields[0] == "baseline_hot" else "enabled"
                throughput[key]["iterations"] += int(fields[1])
                throughput[key]["durations"].append(float(fields[2]))
            except ValueError:
                continue
for mode in ("baseline", "enabled"):
    durations = throughput[mode]["durations"]
    rate = throughput[mode]["iterations"] / max(durations) if durations else float("nan")
    print(f"cpu_bound_{mode}: workers=2 aggregate_python_loop_iterations_per_second={fmt(rate)}")

ticks_per_second = os.sysconf("SC_CLK_TCK")
cpu_delta = int(end_ticks) - int(start_ticks)
cpu_status = "valid"
if start_identity != end_identity or cpu_delta < 0:
    cpu_seconds = float("nan"); cpu_pct = float("nan"); cpu_status = "invalid-identity-or-counter-regression"
else:
    cpu_seconds = cpu_delta / ticks_per_second
    cpu_pct = cpu_seconds / enabled_duration * 100.0 if enabled_duration > 0 else float("nan")
sched_delta = int(end_sched) - int(start_sched)
sched_status = "valid"
if start_identity != end_identity or sched_delta < 0:
    sched_ms = float("nan"); sched_status = "invalid-identity-or-counter-regression"
else:
    sched_ms = sched_delta / 1e6
print(f"guard_enabled_window: wall_s={enabled_duration:.3f} cpu_s={fmt(cpu_seconds)} "
      f"one_core_cpu_pct={cpu_pct:.2f} rss_start/end_kib={start_rss}/{end_rss} "
      f"peak_rss_kib={peak_rss} router_process_runqueue_delay_delta_ms={fmt(sched_ms)} "
      f"cpu_counter_status={cpu_status} scheduler_counter_status={sched_status}")
print(f"host_cpu_psi_some_avg10_pct: baseline_start/end={baseline_psi_start}/{baseline_psi_end}; "
      f"enabled_start/end={enabled_psi_start}/{enabled_psi_end}")
print(f"transition_timing_s: both_workers_routed={route_detection}; first_quota_increase={quota_change}; "
      f"idle_to_quota_decrease_and_recovery={recovery}")

changes = []
pattern = re.compile(r"quota changed unit=" + re.escape(heavy_unit) + r" old=(\d+)% new=(\d+)%")
with open(log_path) as f:
    for line in f:
        match = pattern.search(line)
        if match:
            old, new = int(match.group(1)), int(match.group(2))
            changes.append((old, new, 1 if new > old else (-1 if new < old else 0)))
reversals = sum(1 for before, after in zip(changes, changes[1:])
                if before[2] and after[2] and before[2] != after[2])
print(f"quota_stability: transitions={len(changes)} direction_reversals={reversals} "
      f"values=" + ",".join(str(old)+"->"+str(new) for old,new,_ in changes))
print("metrics_scope: one bounded synthetic host run; loop iterations and probe timings are workload-specific, not application SLA evidence")
PY
)
printf '%s\n' "$METRICS"
if [ -n "$RESULT_DIR" ]; then
  printf '%s\n' "$METRICS" > "$RESULT_DIR/metrics.txt"
  printf 'results_dir=%s\n' "$RESULT_DIR"
fi
printf '%s\n' "controller integration: ok (burst rejected; two competing workers routed; dynamic quota applied/decreased; AIMD log-only; both recovered; $LIFECYCLE_RESULT)"
