#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m py_compile "$ROOT/bin/workload-router.py"
python3 -m py_compile "$ROOT/bin/workload-profile"
python3 -m py_compile "$ROOT/benchmarks/routing-contention.py"
python3 -m py_compile "$ROOT/benchmarks/shadow-policy-replay.py"
python3 -m py_compile "$ROOT/benchmarks/controller-replay.py"
python3 -m py_compile "$ROOT/benchmarks/quota-pressure-replay.py"
sh -n "$ROOT/tests/integration-routing.sh" "$ROOT/tests/recovery-routing.sh" "$ROOT/tests/shadow-routing.sh"
python3 - "$ROOT/bin/workload-router.py" <<'PY'
import importlib.util, sys
from types import SimpleNamespace
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("router", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for command in ("npm run build", "cargo test", "pytest -q", "ffmpeg -i in out"):
    assert mod.command_heavy(command), command
assert not mod.command_heavy("python app.py")
assert mod.is_excluded("python /home/x/workload-router.py")
assert not mod.is_excluded("/usr/bin/google-chrome")
assert mod.in_cgroup_tree
assert mod.command_class("npm run build") == "npm:run"
assert mod.adaptive_score(95, {"samples": 10, "mean": 30, "variance": 25}) is not None
assert mod.adaptive_score(80, {"samples": 2, "mean": 30, "variance": 25}) is None
assert mod.SHADOW_ENABLED is False
shadow = {}
assert mod.update_shadow_policy(shadow, 100, 0, alpha=0.35, route_threshold=70, sustained_samples=3) is None
assert mod.update_shadow_policy(shadow, 0, 1, alpha=0.35, route_threshold=70, sustained_samples=3) is None
assert not shadow.get("active", False), "transient spike should not trigger shadow route"
shadow = {}
assert mod.update_shadow_policy(shadow, 90, 0, alpha=1.0, route_threshold=70, sustained_samples=3) is None
assert mod.update_shadow_policy(shadow, 90, 1, alpha=1.0, route_threshold=70, sustained_samples=3) is None
assert mod.update_shadow_policy(shadow, 90, 2, alpha=1.0, route_threshold=70, sustained_samples=3) == "route"
assert mod.update_shadow_policy(shadow, 60, 3, alpha=1.0, clear_threshold=35, recovery_samples=2, recovery_dwell_sec=5) is None
assert shadow["active"], "hysteresis should not recover at an intermediate CPU level"
assert mod.update_shadow_policy(shadow, 0, 4, alpha=1.0, clear_threshold=35, recovery_samples=2, recovery_dwell_sec=5) is None
assert mod.update_shadow_policy(shadow, 0, 9, alpha=1.0, clear_threshold=35, recovery_samples=2, recovery_dwell_sec=5) == "recover"
assert not shadow["active"]
assert mod.shadow_config_error(alpha=0.0) is not None
assert mod.shadow_config_error(route_threshold=35, clear_threshold=35) is not None
assert mod.shadow_config_error(sustained_samples=0) is not None
assert mod.shadow_config_error(recovery_samples=0) is not None
assert mod.shadow_config_error(route_threshold=70, clear_threshold=35) is None
assert mod.dynamic_quota_target(200, 0.45, 0.9) == 150
assert mod.dynamic_quota_target(200, 0.01, 0.9) == 250
assert mod.dynamic_quota_target(200, 0.01, 0.2) == 200
assert mod.dynamic_quota_target(200, 0.01, 0.2, 0.2) == 250
assert mod.dynamic_quota_target(200, 0.10, 0.9, 0.0, 25) == 150
assert mod.dynamic_quota_target(200, 0.10, 0.2, 0.0, 2) == 200
assert mod.dynamic_quota_target(200, 0.30, 0.9) == 200
# Quota controller must remain bounded and react in the expected direction.
for q in range(100, 401, 10):
    assert mod.dynamic_quota_target(q, 0.90, 1.0) <= q, f"high pressure must not raise quota from {q}%"
    assert mod.dynamic_quota_target(q, 0.0, 1.0) >= q, f"high demand/low pressure must not lower quota from {q}%"
    assert mod.QUOTA_MIN <= mod.dynamic_quota_target(q, 0.90, 1.0) <= mod.QUOTA_MAX
    assert mod.QUOTA_MIN <= mod.dynamic_quota_target(q, 0.0, 1.0) <= mod.QUOTA_MAX
assert mod.dynamic_quota_target(mod.QUOTA_MIN, 0.90, 1.0) == mod.QUOTA_MIN
assert mod.dynamic_quota_target(mod.QUOTA_MAX, 0.0, 1.0) == mod.QUOTA_MAX
assert mod.dynamic_quota_target(200, 0.45, 0.9, sched_delay_ms=0) == 150
assert mod.dynamic_quota_target(200, 0.01, 0.9, sched_delay_ms=25) == 150
# Explicit branch coverage: high pressure wins, boundaries hold, and neutral
# telemetry does not make an arbitrary quota change.
assert mod.dynamic_quota_target(100, 0.90, 1.0) == 100
assert mod.dynamic_quota_target(400, 0.90, 1.0) == 350
assert mod.dynamic_quota_target(200, 0.90, 1.0, throttled_ratio=0.9) == 150
assert mod.dynamic_quota_target(200, 0.01, 0.9, throttled_ratio=0.2) == 250
assert mod.dynamic_quota_target(200, 0.01, 0.9, throttled_ratio=0.2, sched_delay_ms=10) == 200, "throttling must not raise quota in scheduler-pressure hysteresis band"
assert mod.dynamic_quota_target(200, 0.01, 0.9, sched_delay_ms=10) == 200
assert mod.dynamic_quota_target(200, 0.20, 0.30, throttled_ratio=0.0, sched_delay_ms=10) == 200
assert mod.dynamic_quota_target(200, 0.01, 0.10) == 150
assert mod.dynamic_quota_target(100, 0.01, 0.10) == 100
# AIMD proposal must stay within bounds and fail closed without usable PSI.
for q in range(100, 401, 10):
    decreased = mod.aimd_quota_target(q, 0.90, 1.0)
    increased = mod.aimd_quota_target(q, 0.01, 1.0)
    assert mod.QUOTA_MIN <= decreased <= q
    assert q <= increased <= mod.QUOTA_MAX
assert mod.aimd_quota_target(100, 0.90, 1.0) == 100
assert mod.aimd_quota_target(400, 0.01, 1.0) == 400
assert mod.aimd_quota_target(200, 0.45, 0.9) == 160
assert mod.aimd_quota_target(200, 0.01, 0.9) == 210
assert mod.aimd_quota_target(200, 0.01, 0.2) == 200
assert mod.aimd_quota_target(110, 0.45, 0.9) == 100
assert mod.aimd_quota_target(390, 0.01, 0.9) == 400
assert mod.aimd_quota_target(200, 0.45, 0.9, decrease=1.0) is None
assert mod.aimd_quota_target(300, 0.05, 0.0, sched_delay_ms=26.82) == 300, "low PSI plus router delay alone must not decrease AIMD quota"
assert mod.aimd_quota_target(300, None, 0.0, sched_delay_ms=26.82) == 300, "missing PSI must fail closed for AIMD decrease"
assert mod.aimd_quota_target(300, 0.20, 0.0, sched_delay_ms=26.82) == 240, "elevated PSI plus high router delay may corroborate decrease"
assert mod.PARENT_UNIT == "protected-workload.slice"
assert mod.HEAVY_UNIT == "heavy-workload.slice"
assert callable(mod.ensure_workload_units)
with patch.object(mod.subprocess, "run", side_effect=[
    SimpleNamespace(returncode=0, stderr=""),
    SimpleNamespace(returncode=0, stderr=""),
]) as start_units:
    mod.ensure_workload_units()
    assert start_units.call_count == 2
    assert start_units.call_args_list[0].args[0][-1] == mod.PARENT_UNIT
    assert start_units.call_args_list[1].args[0][-1] == mod.HEAVY_UNIT
print("unit checks: ok")
PY
env WORKLOAD_GUARD_SHADOW_EWMA_ALPHA=not-a-number WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES=bad \
  python3 - "$ROOT/bin/workload-router.py" <<'PY'
import importlib.util, sys, math
spec=importlib.util.spec_from_file_location("router_invalid_shadow_env", sys.argv[1])
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
assert math.isnan(mod.SHADOW_EWMA_ALPHA)
assert mod.SHADOW_SUSTAINED_SAMPLES == 0
assert mod.shadow_config_error() is not None
print("invalid shadow configuration: safely rejected")
PY
python3 "$ROOT/benchmarks/shadow-policy-replay.py" --output /tmp/shadow-replay-test.tsv >/dev/null
grep -q 'single_sample_spike.*baseline' /tmp/shadow-replay-test.tsv
grep -q 'noisy_short_burst.*shadow.*6.00.*30.00' /tmp/shadow-replay-test.tsv
rm -f /tmp/shadow-replay-test.tsv
python3 "$ROOT/benchmarks/controller-replay.py" --output /tmp/controller-replay-test.tsv >/dev/null
grep -q 'sustained_pressure.*aimd' /tmp/controller-replay-test.tsv
grep -q 'missing_psi.*gradient2' /tmp/controller-replay-test.tsv
rm -f /tmp/controller-replay-test.tsv
python3 "$ROOT/benchmarks/quota-pressure-replay.py" --output /tmp/quota-pressure-replay-test.tsv >/dev/null
grep -q 'high_pressure[[:space:]]0.65' /tmp/quota-pressure-replay-test.tsv
grep -q 'recovery[[:space:]]0.03' /tmp/quota-pressure-replay-test.tsv
rm -f /tmp/quota-pressure-replay-test.tsv
test "$("$ROOT/bin/workload-guard" version)" = "$(cat "$ROOT/VERSION")"
test -x "$ROOT/bin/workload-profile"
grep -q 'workload-guard quota' "$ROOT/bin/workload-guard"
grep -q 'workload-guard history' "$ROOT/bin/workload-guard"
grep -q 'schema_version' "$ROOT/bin/workload-guard"
tmp_history=$(mktemp)
trap 'rm -f "$tmp_history"' EXIT HUP INT TERM
python3 - "$tmp_history" <<'PY'
import json, sys, time
now=time.time()
rows=[
 {"schema_version":3,"ts":now-300,"cpu_psi_some_avg10":0.60,"router_runqueue_delay_ms":900,"guarded_cpu_pct":0,"unmanaged_cpu_pct":500,"top_cpu":[]},
 {"schema_version":3,"ts":now,"cpu_psi_some_avg10":0.05,"router_runqueue_delay_ms":2,"guarded_cpu_pct":0,"unmanaged_cpu_pct":500,"top_cpu":[]},
]
with open(sys.argv[1], 'w') as f:
 for row in rows: f.write(json.dumps(row)+'\n')
PY
diagnosis=$(WORKLOAD_GUARD_HISTORY_FILE="$tmp_history" "$ROOT/bin/workload-guard" diagnose)
printf '%s\n' "$diagnosis" | grep -q 'CPU pressure       : NORMAL'
printf '%s\n' "$diagnosis" | grep -q 'router process only; not host-wide'
printf '%s\n' "$diagnosis" | grep -q 'does not prove cause'
python3 - "$tmp_history" <<'PY'
import json, sys, time
with open(sys.argv[1], 'w') as f:
 f.write(json.dumps({"schema_version":3,"ts":time.time(),"cpu_psi_some_avg10":0.55,"router_runqueue_delay_ms":25,"guarded_cpu_pct":20,"unmanaged_cpu_pct":30})+'\n')
PY
diagnosis=$(WORKLOAD_GUARD_HISTORY_FILE="$tmp_history" "$ROOT/bin/workload-guard" diagnose)
printf '%s\n' "$diagnosis" | grep -q 'CPU pressure       : HIGH'
python3 - "$tmp_history" <<'PY'
import json, sys, time
with open(sys.argv[1], 'w') as f:
 f.write(json.dumps({"schema_version":3,"ts":time.time(),"guarded_cpu_pct":0,"unmanaged_cpu_pct":0})+'\n')
PY
diagnosis=$(WORKLOAD_GUARD_HISTORY_FILE="$tmp_history" "$ROOT/bin/workload-guard" diagnose)
printf '%s\n' "$diagnosis" | grep -q 'CPU pressure       : UNKNOWN (PSI missing)'
grep -q 'workload-profile' "$ROOT/install.sh"
test "$(env -u WORKLOAD_GUARD_QUOTA_INTERVAL_SEC -u WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC python3 -c 'import importlib.util,sys; s=importlib.util.spec_from_file_location("r",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(int(m.QUOTA_INTERVAL_SEC), int(m.QUOTA_MIN_DWELL_SEC))' "$ROOT/bin/workload-router.py")" = "5 10"
grep -q '^EnvironmentFile=-%h/.config/linux-workload-guard/workload-guard.env$' "$ROOT/systemd/workload-router.service"
grep -q '^Wants=protected-workload.slice heavy-workload.slice$' "$ROOT/systemd/workload-router.service"
sh -n "$ROOT/tests/shadow-routing.sh"
grep -q 'shadow decision=route' "$ROOT/tests/shadow-routing.sh"
grep -q 'shadow decision=recover' "$ROOT/tests/shadow-routing.sh"
grep -q 'CONFIG_DIR="$HOME/.config/linux-workload-guard"' "$ROOT/install.sh"
env -u WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD -u WORKLOAD_GUARD_RECOVERY_SAMPLES -u WORKLOAD_GUARD_RECOVERY_DWELL_SEC python3 -c 'import importlib.util,sys; s=importlib.util.spec_from_file_location("r",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.RECOVERY_CPU_THRESHOLD == 35 and m.RECOVERY_SAMPLES == 10 and m.RECOVERY_DWELL_SEC == 20' "$ROOT/bin/workload-router.py"
printf '%s\n' 'tests: ok'
