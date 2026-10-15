#!/usr/bin/env python3
"""Deterministic regression coverage for telemetry, PID identity, and quota flow."""
import importlib.util
import math
import os
import subprocess
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location("workload_router_hardening", os.path.join(ROOT, "bin", "workload-router.py"))
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# Parse actual Linux PSI format strictly. Missing/malformed values are unknown,
# never synthetic zero-pressure readings.
check(router.parse_cpu_pressure("some avg10=40.00 avg60=8.00 avg300=2.00 total=123\nfull avg10=0.00\n") == 0.40,
      "default PSI high threshold must parse exactly as 0.40")
check(abs(router.parse_cpu_pressure("some avg10=39.99 avg60=8.00 avg300=2.00 total=123\n") - 0.3999) < 1e-9,
      "PSI immediately below the production threshold must remain below it")
for bad_psi in (
    "some avg60=1.00 avg300=1.00 total=1\n",
    "some avg10=nan avg60=1 avg300=1 total=1\n",
    "some avg10=inf avg60=1 avg300=1 total=1\n",
    "some avg10=-0.01 avg60=1 avg300=1 total=1\n",
    "some avg10=100.01 avg60=1 avg300=1 total=1\n",
    "some avg10=10 avg10=11 avg60=1 avg300=1 total=1\n",
    "some avg10=not-a-number avg60=1 avg300=1 total=1\n",
    "some avg10=10 avg60=1 avg300=1\n",
    "some avg10=10 avg60=1 avg300=1 total=not-a-counter\n",
    "some avg10=10 avg60=1 avg300=1 total=1\nsome avg10=10 avg60=1 avg300=1 total=2\n",
    "full avg10=99.0 avg60=99 avg300=99 total=1\n",
):
    check(router.parse_cpu_pressure(bad_psi) is None, f"invalid PSI accepted: {bad_psi!r}")


# A /proc stat command name may itself contain ')' characters. Field 22
# (starttime) protects route/recovery bookkeeping from PID reuse.
stat_fields = ["S"] + [str(i) for i in range(1, 20)]
parsed_proc = router.parse_proc_stat(f"123 (worker ) command) {' '.join(stat_fields)}")
check(parsed_proc == (1, 11, 12, 19), f"unexpected proc stat parse: {parsed_proc}")
check(router.parse_proc_stat("malformed") is None, "malformed proc stat must be rejected")
tracked_route = (80.0, "sustained-cpu", "/source", "/target", "python worker", 4567)
check(router.route_process_matches(tracked_route, "python worker", 4567), "matching PID identity rejected")
check(router.route_process_matches(tracked_route, "python new-image", 4567),
      "exec-style command change must not invalidate a stable PID starttime")
check(not router.route_process_matches(tracked_route, "python worker", 4568), "PID reuse with same command must be rejected")
check(not router.route_process_matches(tracked_route[:5] + (None,), "python worker", 4567), "legacy identity-less route must not be trusted")
with tempfile.TemporaryDirectory() as tempdir:
    check(router.cgroup_tree_pids(os.path.join(tempdir, "removed-cgroup")) == set(),
          "a disappeared cgroup must return an empty set compatible with set union")
check(router.cgroup_tree_pids(None) == set(), "missing cgroup path must return an empty set")


# Test the state-file upgrade boundary: new state persists starttime; legacy
# six-field state loads only as an identity-less entry, never as a trusted PID.
original_state_file = router.STATE_FILE
with tempfile.TemporaryDirectory() as tempdir:
    router.STATE_FILE = os.path.join(tempdir, "routes.tsv")
    router.write_state({123: tracked_route})
    loaded = router.load_state()
    check(loaded[123][5] == 4567, "route state did not persist PID starttime")
    with open(router.STATE_FILE, "w") as f:
        f.write("123\t80.0\tsustained-cpu\t/source\t/target\tpython worker\n")
    legacy = router.load_state()
    check(legacy[123][5] is None, "legacy route state unexpectedly gained identity")
router.STATE_FILE = original_state_file


# Production PSI default threshold behavior: >=0.40 reduces quota; just below
# it cannot masquerade as either high or low pressure. Missing PSI cannot grant
# a quota increase even when scheduler-delay measurement looks low.
check(router.QUOTA_PRESSURE_HIGH == 0.40, "production PSI high threshold drifted")
check(router.QUOTA_PRESSURE_LOW == 0.10, "production PSI low threshold drifted")
check(router.routing_config_error() is None, "checked-in core routing defaults are invalid")
check(router.dynamic_quota_target(300, 0.40, 0.9, sched_delay_ms=1.0) == 250,
      "PSI at the default threshold did not request a bounded decrease")
check(router.dynamic_quota_target(300, 0.3999, 0.9, sched_delay_ms=1.0) == 300,
      "PSI below the high threshold changed quota unexpectedly")
check(router.dynamic_quota_target(200, None, 0.9, sched_delay_ms=0.0) == 200,
      "missing PSI plus low router delay must not be treated as low pressure")
check(router.dynamic_quota_target(200, 0.01, 0.9) == 200,
      "missing scheduler-delay sample must not permit quota increase")
check(router.aimd_quota_target(200, 0.01, 0.9) == 200,
      "missing scheduler-delay sample must not permit AIMD additive proposal")
check(router.dynamic_quota_target(200, float("nan"), 0.9, sched_delay_ms=0.0) == 200,
      "non-finite PSI must not permit quota increase")
check(router.dynamic_quota_target(200, 0.01, 0.10, throttled_ratio=0.50, sched_delay_ms=0.0) == 150,
      "idle demand should decrease quota even when recent throttling remains high")
check(router.dynamic_quota_target(200, 0.01, 0.40, throttled_ratio=0.50, sched_delay_ms=0.0) == 250,
      "throttling can corroborate non-trivial demand under low pressure")
check(router.aimd_quota_target(200, 0.01, 0.10, throttled_ratio=0.50, sched_delay_ms=0.0) == 200,
      "AIMD throttling alone must not propose quota growth at low demand")
check(router.monotonic_counter_delta(100, 90) == 10, "valid scheduler counter delta failed")
check(router.monotonic_counter_delta(5, 90) is None, "scheduler counter reset must not be converted to zero")
check(router.monotonic_counter_delta(float("nan"), 90) is None, "invalid scheduler counter must be rejected")
check(router.cgroup_cpu_delta(
    {"usage_usec": 101, "nr_periods": 10, "nr_throttled": 3},
    {"usage_usec": 100, "nr_periods": 10, "nr_throttled": 2},
) is None, "impossible throttled-period delta must be rejected")


# Cross-step quota sequence using the same guarded observation function called
# by the runtime loop: baseline -> high PSI decrease -> counter reset fail-closed
# -> re-baseline -> low-load recovery. AIMD remains a proposal only.
base = {"usage_usec": 1_000_000, "nr_periods": 100, "nr_throttled": 5}
high = {"usage_usec": 1_500_000, "nr_periods": 105, "nr_throttled": 7}
below = {"usage_usec": 1_500_000, "nr_periods": 105, "nr_throttled": 7}
decision, reason = router.quota_decision_from_sample(300, base, below, 5.0, 0.3999, 0.0, 0.8)
check(reason == "ok" and decision["target"] == 300,
      "integrated production-threshold path must hold quota just below 0.40")
decision, reason = router.quota_decision_from_sample(300, base, high, 5.0, 0.40, 0.0, 0.8)
check(reason == "ok" and decision["target"] == 250,
      "integrated production-threshold path must decrease quota at exactly 0.40")
decision, reason = router.quota_decision_from_sample(300, base, high, 5.0, None, None, 0.8)
check(decision is None and reason == "pressure-signals-unavailable",
      "unavailable PSI and scheduler telemetry must not authorize quota changes")
decision, reason = router.quota_decision_from_sample(450, base, high, 5.0, 0.40, 0.0, 0.8)
check(decision is None and reason == "quota-out-of-bounds",
      "out-of-range observed quota must not authorize a controller step")
decision, reason = router.quota_decision_from_sample(150.5, base, high, 5.0, 0.40, 0.0, 0.8)
check(decision is None and reason == "quota-not-integral",
      "quota precision the actuator cannot preserve must not authorize a controller step")
decision, reason = router.quota_decision_from_sample(300, None, base, 5.0, 0.40, 0.0, 0.8)
check(decision is None and reason == "counter-baseline-unavailable", "first sample must only establish baseline")
decision, reason = router.quota_decision_from_sample(300, base, high, 5.0, 0.40, 0.0, 0.8)
check(reason == "ok" and decision["target"] == 250, f"integrated high-PSI decision failed: {reason}, {decision}")
check(decision["aimd_target"] == 240, "AIMD shadow proposal should remain separate from step-controller target")
reset = {"usage_usec": 100, "nr_periods": 2, "nr_throttled": 0}
decision, reason = router.quota_decision_from_sample(250, high, reset, 5.0, 0.05, 0.0, 0.8)
check(decision is None and reason == "counter-reset-or-anomaly", "counter reset must suppress all quota proposals")
after_reset = {"usage_usec": 250_100, "nr_periods": 7, "nr_throttled": 0}
decision, reason = router.quota_decision_from_sample(250, reset, after_reset, 5.0, 0.05, 0.0, 0.8)
check(reason == "ok" and decision["target"] == 300, "controller did not safely resume after reset re-baseline")
idle = {"usage_usec": 250_100, "nr_periods": 12, "nr_throttled": 0}
decision, reason = router.quota_decision_from_sample(300, after_reset, idle, 5.0, 0.05, 0.0, 0.0)
check(reason == "ok" and decision["target"] == 250, "current low route demand should permit bounded quota recovery")
check(decision["aimd_target"] == 300, "AIMD must remain an independent log-only proposal")
decision, reason = router.quota_decision_from_sample(250, idle, {**idle, "usage_usec": idle["usage_usec"] + 1}, 60.0, 0.05, 0.0, 0.8)
check(decision is None and reason == "stale-or-invalid-sample-window", "stale sample window must suppress actuation")
bad_ratio = {"usage_usec": 1_000_001, "nr_periods": 1, "nr_throttled": 100}
decision, reason = router.quota_decision_from_sample(250, base, bad_ratio, 5.0, 0.05, 0.0, 0.8)
check(decision is None and reason == "counter-reset-or-anomaly", "impossible throttling ratio must be rejected")
decision, reason = router.quota_decision_from_sample(250, base, high, 5.0, None, 0.0, 0.9)
check(reason == "ok" and decision["target"] == 250 and decision["aimd_target"] == 250,
      "missing PSI must hold both controller proposals when scheduler delay is low")


# External failures have explicit bounded timeouts and must not be reported as
# successful actuation or fabricated telemetry.
with patch.object(router.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("systemctl", 2)):
    check(router.user_cgroup("heavy-workload.slice") is None, "systemctl timeout must yield unavailable cgroup")
with patch.object(router.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 3)):
    check(not router.set_unit_quota("heavy-workload.slice", 150), "quota timeout must not report success")
check(router.quota_config_error() is None, "checked-in production quota configuration is invalid")
check(router.dynamic_quota_target("invalid", 0.05, 0.9) == "invalid", "invalid current quota must not actuate")
check(router.aimd_quota_target("invalid", 0.05, 0.9) is None, "invalid AIMD quota must not propose actuation")
check(router.aimd_quota_target(200, -0.1, 0.9, sched_delay_ms=1.0) == 200,
      "negative PSI must not trigger AIMD proposal")

for env, expected in (
    ({"WORKLOAD_GUARD_QUOTA_INTERVAL_SEC": "1e-100"}, "at least 0.25s"),
    ({"WORKLOAD_GUARD_QUOTA_MIN": "100.5"}, "whole percentage points"),
    ({"WORKLOAD_GUARD_QUOTA_MAX": "99"}, "quota bounds"),
):
    with patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location("router_bad_quota_env", os.path.join(ROOT, "bin", "workload-router.py"))
        candidate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(candidate)
        error = candidate.quota_config_error()
        check(error is not None and expected in error,
              f"unsafe quota config was not rejected: {env!r}, got {error!r}")

# Core routing settings used to be parsed directly during module import, so a
# malformed environment value could crash the service before fail-safe checks.
for env, expected in (
    ({"WORKLOAD_GUARD_SAMPLE_SEC": "not-a-number"}, "finite"),
    ({"WORKLOAD_GUARD_SAMPLE_SEC": "nan"}, "finite"),
    ({"WORKLOAD_GUARD_SAMPLE_SEC": "0"}, "interval"),
    ({"WORKLOAD_GUARD_SAMPLE_SEC": "120"}, "interval"),
    ({"WORKLOAD_GUARD_SAMPLE_SEC": "1e-100"}, "interval"),
    ({"WORKLOAD_GUARD_SUSTAINED_SAMPLES": "bad"}, "sustained sample"),
    ({"WORKLOAD_GUARD_SUSTAINED_SAMPLES": "0"}, "sustained sample"),
    ({"WORKLOAD_GUARD_RECOVERY_SAMPLES": "-2"}, "recovery sample"),
    ({"WORKLOAD_GUARD_CPU_THRESHOLD": "inf"}, "finite"),
    ({"WORKLOAD_GUARD_CPU_THRESHOLD": "101"}, "CPU thresholds"),
    ({"WORKLOAD_GUARD_COOLDOWN_SEC": "-1"}, "non-negative"),
    ({"WORKLOAD_GUARD_HISTORY_INTERVAL_SEC": "broken"}, "finite"),
    ({"WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC": "0"}, "statistics interval"),
    ({"WORKLOAD_GUARD_HISTORY_INTERVAL_SEC": "0.0001"}, "history interval"),
):
    with patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location("router_bad_core_env", os.path.join(ROOT, "bin", "workload-router.py"))
        candidate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(candidate)
        error = candidate.routing_config_error()
        check(error is not None and expected in error,
              f"unsafe core config was not rejected: {env!r}, got {error!r}")
        with patch.object(candidate, "ensure_workload_units") as ensure:
            candidate.main()
            ensure.assert_not_called()

print("controller hardening: ok (strict PSI, production threshold path, quota bounds, PID/exec identity, recovery sequence, timeout fail-safe)")
