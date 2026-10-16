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
with patch.object(router.os, "stat"), patch.object(router, "proc_cgroup", return_value=None):
    check(router.routed_process_membership(123, "/sys/fs/cgroup/protected/heavy.slice") == "unknown",
          "unreadable /proc cgroup membership must remain unknown, not look like an exited/moved process")
with patch.object(router.os, "stat", side_effect=FileNotFoundError):
    check(router.routed_process_membership(123, "/sys/fs/cgroup/protected/heavy.slice") == "gone",
          "confirmed missing PID must be distinguished from unreadable membership")
with patch.object(router.os, "stat"), patch.object(router, "proc_cgroup", return_value="/protected/heavy.slice/worker.scope"):
    check(router.routed_process_membership(123, "/sys/fs/cgroup/protected/heavy.slice") == "inside",
          "known descendant cgroup must retain route membership")
with patch.object(router.os, "stat"), patch.object(router, "proc_cgroup", return_value="/protected/other.slice"):
    check(router.routed_process_membership(123, "/sys/fs/cgroup/protected/heavy.slice") == "outside",
          "known different cgroup must not retain route membership")
with patch.object(router.os, "stat", side_effect=PermissionError):
    check(router.routed_process_membership(123, "/sys/fs/cgroup/protected/heavy.slice") == "unknown",
          "permission errors must not be misclassified as process exit")
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


# Aggregate routing is opt-in and requires multiple independently active
# workers plus sustained use of the observed parent CPU quota. It routes one
# process per sample, rotates fairly, applies hysteresis, and fails closed on
# missing quota telemetry.
check(router.AGGREGATE_ROUTING is False, "aggregate routing must remain opt-in by default")
aggregate = {"active": False, "high_samples": 0, "low_samples": 0, "cursor": -1}
for _ in range(2):
    pid, status = router.update_aggregate_route_policy(aggregate, {101: 45, 202: 45}, {101, 202}, 100)
    check(pid is None and status == "aggregate-saturation-pending", "aggregate policy triggered before sustained threshold")
pid, status = router.update_aggregate_route_policy(aggregate, {101: 45, 202: 45}, {101, 202}, 100)
check(pid == 101 and status == "aggregate-saturation", "two workers below individual trigger failed aggregate detection")
pid, status = router.update_aggregate_route_policy(aggregate, {101: 45, 202: 45}, {101, 202}, 100)
check(pid == 202, "aggregate routing did not rotate fairly across workers")
pid, status = router.update_aggregate_route_policy(aggregate, {101: 45}, {101}, 100)
check(pid == 101, "remaining active worker should remain routable while aggregate saturation persists")
for _ in range(1):
    pid, status = router.update_aggregate_route_policy(aggregate, {101: 10, 202: 10}, {101, 202}, 100)
    check(status != "aggregate-saturation-cleared", "aggregate hysteresis cleared too early")
pid, status = router.update_aggregate_route_policy(aggregate, {101: 10, 202: 10}, {101, 202}, 100)
check(pid is None and status == "aggregate-saturation-cleared" and not aggregate["active"],
      "aggregate saturation did not clear after sustained low demand")
for _ in range(5):
    pid, status = router.update_aggregate_route_policy(aggregate, {101: 45}, {101}, 100)
    check(pid is None, "single worker or short-lived burst caused aggregate route")
pid, status = router.update_aggregate_route_policy(aggregate, {101: 45, 202: 45}, {101, 202}, None)
check(pid is None and status == "aggregate-telemetry-unavailable" and not aggregate["active"],
      "missing aggregate quota telemetry must fail closed")
aggregate = {"active": False, "high_samples": 0, "low_samples": 0, "cursor": -1}
for _ in range(4):
    pid, status = router.update_aggregate_route_policy(aggregate, {101: 45, 202: 45}, {101, 202}, 200)
    check(pid is None and status == "aggregate-saturation-pending",
          "aggregate quota drift to 200% must re-normalize demand instead of routing on stale 100% quota")
aggregate = {"active": False, "high_samples": 0, "low_samples": 0, "cursor": -1}
for sample in ({101: 50, 202: 20}, {101: 50, 202: 0}, {101: 50, 202: 20}):
    pid, _ = router.update_aggregate_route_policy(aggregate, sample, {101, 202}, 100)
    check(pid is None, "flapping aggregate demand should reset sustained-entry hysteresis")
aggregate = {"active": True, "high_samples": 3, "low_samples": 0, "cursor": -1}
seen = []
for _ in range(6):
    pid, _ = router.update_aggregate_route_policy(aggregate, {101: 30, 202: 30, 303: 30}, {101, 202, 303}, 100)
    seen.append(pid)
check(seen == [101, 202, 303, 101, 202, 303], "aggregate routing fairness rotation/starvation invariant failed")
quota_state = {"active": True, "high_samples": 3, "low_samples": 0, "cursor": "lwg-workload-a.scope"}
observed_quota, changed = router.reconcile_aggregate_quota(quota_state, 100, 200)
check(observed_quota == 200 and changed and not quota_state["active"] and quota_state["high_samples"] == 0,
      "parent quota drift must reset aggregate hysteresis before new routing decisions")
observed_quota, changed = router.reconcile_aggregate_quota(quota_state, 200, None)
check(observed_quota is None and changed and not quota_state["active"],
      "missing parent quota must clear the aggregate latch")

# Scope-based aggregate accounting survives short-lived PID churn: it consumes
# the scope's cgroup cpu.stat usage counter, and the scope inode is the identity
# boundary when systemd recreates a unit with the same name.
with tempfile.TemporaryDirectory() as tempdir:
    parent = os.path.join(tempdir, "protected-workload.slice")
    os.mkdir(parent)
    scope = os.path.join(parent, "lwg-workload-test-worker.scope")
    os.mkdir(scope)
    with open(os.path.join(scope, "cgroup.procs"), "w") as f:
        f.write("111\n222\n")
    with open(os.path.join(scope, "cpu.stat"), "w") as f:
        f.write("usage_usec 1000000\nuser_usec 900000\nsystem_usec 100000\n")
    spoof = os.path.join(parent, "lwg-workload-test-unmarked.scope")
    os.mkdir(spoof)
    open(os.path.join(spoof, "cgroup.procs"), "w").close()
    with open(os.path.join(spoof, "cpu.stat"), "w") as f:
        f.write("usage_usec 99999999\n")
    def fake_scope_properties(units, properties):
        result = {}
        for unit in units:
            path = scope if unit == "lwg-workload-test-worker.scope" else spoof
            result[unit] = {"Description": router.AGGREGATE_SCOPE_DESCRIPTION if unit.endswith("worker.scope") else "unrelated scope",
                            "ActiveState": "active", "ControlGroup": path}
        return result
    with patch.object(router, "unit_properties", side_effect=fake_scope_properties) as property_batch:
        candidates = router.workload_scope_candidates(parent)
        check(property_batch.call_count == 1, "uncached scope identities should use one batched systemd query")
    check(set(candidates) == {"lwg-workload-test-worker.scope"},
          "scope discovery must include only explicitly named direct workload scopes")
    check(candidates["lwg-workload-test-worker.scope"]["usage_usec"] == 1000000,
          "scope usage parser should not require quota-only cpu.stat counters")
    router.SCOPE_IDENTITY_CACHE.clear()
    with patch.object(router, "unit_properties", return_value=None):
        candidates = router.workload_scope_candidates(parent)
    check(router.SCOPE_DISCOVERY_ERROR and not candidates,
          "failed batched systemd scope query must fail closed without routing candidates")
    router.SCOPE_IDENTITY_CACHE.clear()
    with patch.object(router, "unit_properties", side_effect=fake_scope_properties):
        candidates = router.workload_scope_candidates(parent)
    previous = {"lwg-workload-test-worker.scope": (candidates["lwg-workload-test-worker.scope"]["inode"], 500000, 10.0)}
    measured, next_previous, valid = router.scope_cpu_samples(candidates, previous, 11.0, 10.0)
    check(valid and abs(measured["lwg-workload-test-worker.scope"] - 50.0) < 1e-9,
          "scope CPU percentage must derive from real usage_usec deltas")
    stale, _, valid = router.scope_cpu_samples(candidates, previous, 30.1, 10.0)
    check(not valid and not stale, "stale scope telemetry must fail closed")
    missing = dict(candidates)
    missing["lwg-workload-test-worker.scope"] = dict(candidates["lwg-workload-test-worker.scope"], usage_usec=None)
    missing_cpu, _, valid = router.scope_cpu_samples(missing, previous, 11.0, 10.0)
    check(not valid and not missing_cpu, "missing scope usage telemetry must fail closed")
    state_path = os.path.join(tempdir, "routes.scopes.tsv")
    state = {"lwg-workload-test-worker.scope": {"cgroup": scope, "inode": candidates["lwg-workload-test-worker.scope"]["inode"],
                                                "original_weight": 100, "route_weight": 20, "suppressed": False}}
    router.write_scope_state(state, state_path)
    check((os.stat(state_path).st_mode & 0o777) == 0o600,
          "scope route state must be persisted with owner-only permissions")
    restored = router.load_scope_state(state_path)
    check(restored == state, "scope route state did not persist scope inode/original/route weight")
    # A same-name cgroup recreation must drop the stale record, never write to
    # the new unit instance based only on its reusable name.
    state["lwg-workload-test-worker.scope"]["inode"] -= 1
    with patch.object(router, "unit_cpu_weight", return_value=20), patch.object(router, "set_unit_cpu_weight") as set_weight:
        changed = router.reconcile_scope_routes(state, candidates, False)
        check(changed and not state and not set_weight.called,
              "cgroup recreation must discard stale route identity without restoring the new scope")
    state = {"lwg-workload-test-worker.scope": {"cgroup": scope, "inode": candidates["lwg-workload-test-worker.scope"]["inode"],
                                                "original_weight": 100, "route_weight": 20, "suppressed": False}}
    with patch.object(router, "unit_cpu_weight", return_value=20), patch.object(router, "set_unit_cpu_weight", return_value=True) as set_weight:
        changed = router.reconcile_scope_routes(state, candidates, False)
        check(changed and not state, "scope recovery must clear a controller-owned route record")
        set_weight.assert_called_once_with("lwg-workload-test-worker.scope", 100, scope,
                                           candidates["lwg-workload-test-worker.scope"]["inode"])
    state = {"lwg-workload-test-worker.scope": {"cgroup": scope, "inode": candidates["lwg-workload-test-worker.scope"]["inode"],
                                                "original_weight": 100, "route_weight": 20, "suppressed": False}}
    with patch.object(router, "unit_cpu_weight", return_value=55), patch.object(router, "set_unit_cpu_weight") as set_weight:
        router.reconcile_scope_routes(state, candidates, False)
        check(state["lwg-workload-test-worker.scope"]["suppressed"] and not set_weight.called,
              "recovery must not overwrite a scope weight changed externally and must persist suppression")

with tempfile.TemporaryDirectory() as tempdir:
    for index in range(router.AGGREGATE_MAX_SCOPES + 1):
        os.mkdir(os.path.join(tempdir, f"lwg-workload-overflow-{index}.scope"))
    overflow_candidates = router.workload_scope_candidates(tempdir)
    check(router.SCOPE_DISCOVERY_OVERFLOW and not overflow_candidates,
          "excess candidate scopes must disable new routing instead of truncating aggregate demand")


# Production PSI default threshold behavior: >=0.40 reduces quota; just below
# it cannot masquerade as either high or low pressure. Missing PSI cannot grant
# a quota increase even when scheduler-delay measurement looks low.
check(router.QUOTA_PRESSURE_HIGH == 0.40, "production PSI high threshold drifted")
check(router.QUOTA_PRESSURE_LOW == 0.10, "production PSI low threshold drifted")
check(router.routing_config_error() is None, "checked-in core routing defaults are invalid")
check(router.dynamic_quota_target(300, 0.40, 0.9, sched_delay_ms=1.0) == 250,
      "PSI at the default threshold did not request a bounded decrease")
check(router.dynamic_quota_target(300, 0.4001, 0.9, sched_delay_ms=1.0) == 250,
      "PSI above the production high threshold did not request a bounded decrease")
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
check(router.dynamic_quota_target(200, 0.01, 1.1, sched_delay_ms=0.0) == 200,
      "out-of-range normalized demand must not permit quota increase")
check(router.aimd_quota_target(200, 0.01, -0.1, sched_delay_ms=0.0) == 200,
      "negative normalized demand must not permit an AIMD proposal")
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
check(router.cgroup_cpu_delta(
    {"usage_usec": 100, "nr_periods": 2, "nr_throttled": 1},
    {"usage_usec": 100, "nr_periods": 1, "nr_throttled": 0},
) == {"usage_usec": 0, "nr_periods": 1, "nr_throttled": 1},
      "valid zero-usage interval with one period must preserve exact counter deltas")
check(router.cgroup_cpu_delta(
    {"usage_usec": 1, "nr_periods": 0, "nr_throttled": 0},
    {"usage_usec": 1, "nr_periods": 0, "nr_throttled": 0},
) == {"usage_usec": 0, "nr_periods": 0, "nr_throttled": 0},
      "counter parser should preserve zero deltas; decision layer rejects absent period progress")
check(router.cgroup_cpu_delta(
    {"usage_usec": 1, "nr_periods": 1, "nr_throttled": 0},
    {"usage_usec": (1 << 64) - 2, "nr_periods": (1 << 64) - 1, "nr_throttled": 0},
) is None, "64-bit counter wrap must be treated as discontinuity, not modular progress")
check(router.reconcile_quota_observation(200, 200) == (200.0, "ok"),
      "unchanged systemd quota property should retain the confirmed baseline")
check(router.reconcile_quota_observation(200, 250) == (250.0, "quota-changed-externally"),
      "out-of-band quota changes must be detected and re-baselined")
check(router.reconcile_quota_observation(None, 200) == (200.0, "quota-baseline-reset"),
      "recovered quota telemetry must establish a new baseline before actuation")
check(router.reconcile_quota_observation(200, None) == (None, "quota-unavailable"),
      "missing quota property must invalidate the cached quota")
check(router.reconcile_quota_observation(200, float("nan")) == (None, "quota-unavailable"),
      "non-finite observed quota must invalidate the cached quota")
check(router.reconcile_quota_observation(200, "malformed") == (None, "quota-unavailable"),
      "malformed observed quota must invalidate the cached quota")
check(router.dynamic_quota_target(450, 0.40, 0.9, sched_delay_ms=1.0) == 450,
      "bounded-step helper must not turn an invalid observed quota into a step")
check(router.dynamic_quota_target(150.5, 0.40, 0.9, sched_delay_ms=1.0) == 150.5,
      "bounded-step helper must not quantize fractional current quota")
check(router.aimd_quota_target(450, 0.40, 0.9, sched_delay_ms=1.0) is None,
      "AIMD helper must refuse an out-of-bounds current quota")


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
no_period_progress = {"usage_usec": base["usage_usec"] + 1_000_000,
                      "nr_periods": base["nr_periods"],
                      "nr_throttled": base["nr_throttled"]}
decision, reason = router.quota_decision_from_sample(300, base, no_period_progress,
                                                       5.0, 0.05, 0.0, 0.9)
check(decision is None and reason == "quota-period-counter-not-advancing",
      "zero period-counter progress must not masquerade as zero throttling or authorize quota increase")
decision, reason = router.quota_decision_from_sample(300, base, high, 5.0, 0.4001, 0.0, 0.8)
check(reason == "ok" and decision["target"] == 250,
      "integrated production-threshold path must decrease quota above 0.40")
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
decision, reason = router.quota_decision_from_sample(250, base, high, 5.0, 0.05, 0.0, -0.1)
check(decision is None and reason == "invalid-route-demand",
      "negative route demand is inconsistent telemetry and must not be clamped into a valid decision")
decision, reason = router.quota_decision_from_sample(250, base, high, 5.0, 0.05, 0.0, 1.1)
check(decision is None and reason == "invalid-route-demand",
      "route demand above its normalized range must fail closed")


# External failures have explicit bounded timeouts and must not be reported as
# successful actuation or fabricated telemetry.
with patch.object(router.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("systemctl", 2)):
    check(router.user_cgroup("heavy-workload.slice") is None, "systemctl timeout must yield unavailable cgroup")
for rendered, expected in (("1s", 100.0), ("1.5s", 150.0), ("500ms", 50.0),
                           ("250ms", 25.0), ("250000us", 25.0)):
    with patch.object(router.subprocess, "check_output", return_value=rendered):
        observed = router.unit_quota("temporary-test.slice")
    check(observed is not None and abs(observed - expected) < 0.01,
          f"systemd CPUQuotaPerSecUSec {rendered!r} should parse as {expected}% (got {observed!r})")
for rendered in ("infinity", "", "500xs", "0ms", "nanms", "-100ms"):
    with patch.object(router.subprocess, "check_output", return_value=rendered):
        check(router.unit_quota("temporary-test.slice") is None,
              f"unsupported/invalid systemd quota property {rendered!r} must be unavailable")
with patch.object(router.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 3)):
    check(not router.set_unit_quota("heavy-workload.slice", 150), "quota timeout must not report success")
with patch.object(router.subprocess, "run") as run_set, patch.object(router, "unit_quota", return_value=150.0):
    check(router.set_unit_quota("temporary-test.slice", 150),
          "quota actuator should report success after exact effective-property readback")
    run_set.assert_called_once()
with patch.object(router.subprocess, "run") as run_set, patch.object(router, "unit_quota", return_value=200.0):
    check(not router.set_unit_quota("temporary-test.slice", 150),
          "quota actuator must not claim success when effective property differs")
    run_set.assert_called_once()
with patch.object(router.subprocess, "run") as run_set, patch.object(router, "unit_quota", return_value=None):
    check(not router.set_unit_quota("temporary-test.slice", 150),
          "quota actuator must not claim success when readback is unavailable")
    run_set.assert_called_once()
with patch.object(router.subprocess, "run") as run_set:
    for unsafe_quota in (50, 150.5, 450, float("nan"), float("inf")):
        check(not router.set_unit_quota("temporary-test.slice", unsafe_quota),
              f"quota actuator accepted an unsafe target: {unsafe_quota!r}")
    run_set.assert_not_called()
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
    ({"WORKLOAD_GUARD_AGGREGATE_ROUTING": "true", "WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT": "100"}, "aggregate routing thresholds"),
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

with patch.dict(os.environ, {"WORKLOAD_GUARD_AGGREGATE_ROUTING": "false",
                             "WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO": "nan"}, clear=False):
    spec = importlib.util.spec_from_file_location("router_aggregate_disabled_bad_env", os.path.join(ROOT, "bin", "workload-router.py"))
    candidate = importlib.util.module_from_spec(spec); spec.loader.exec_module(candidate)
    check(candidate.routing_config_error() is None,
          "invalid unused aggregate settings must not disable the existing default routing policy")
with patch.dict(os.environ, {"WORKLOAD_GUARD_AGGREGATE_ROUTING": "true",
                             "WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO": "nan"}, clear=False):
    spec = importlib.util.spec_from_file_location("router_aggregate_enabled_bad_env", os.path.join(ROOT, "bin", "workload-router.py"))
    candidate = importlib.util.module_from_spec(spec); spec.loader.exec_module(candidate)
    check("aggregate routing thresholds" in candidate.routing_config_error(),
          "invalid enabled aggregate policy must fail closed")

print("controller hardening: ok (strict PSI, production threshold path, quota bounds, PID/exec identity, recovery sequence, timeout fail-safe)")
