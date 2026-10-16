#!/usr/bin/env python3
"""End-to-end synthetic-file/mock-systemd controller flow; never actuates host units."""
import importlib.util
import os
import pathlib
import subprocess
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workload_router_sample_flow", ROOT / "bin/workload-router.py")
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def stat_text(usage, periods, throttled):
    return (f"usage_usec {usage}\n"
            f"user_usec {usage // 2}\n"
            f"system_usec {usage // 2}\n"
            f"nr_periods {periods}\n"
            f"nr_throttled {throttled}\n"
            f"throttled_usec {throttled * 100}\n")


PSI_HIGH = "some avg10=40.00 avg60=15.00 avg300=5.00 total=1000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=100\n"
PSI_BELOW = "some avg10=39.99 avg60=15.00 avg300=5.00 total=1000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=100\n"
PSI_MALFORMED = "some avg10=nan avg60=15.00 avg300=5.00 total=1000\n"


with tempfile.TemporaryDirectory(prefix="lwg-controller-flow-") as tempdir:
    cgroup = pathlib.Path(tempdir) / "temporary-test.slice"
    cgroup.mkdir()
    cpu_stat = cgroup / "cpu.stat"
    cpu_stat.write_text(stat_text(1_000_000, 100, 5))
    baseline = router.cgroup_cpu_stat(str(cgroup))
    check(baseline == {"usage_usec": 1_000_000, "user_usec": 500_000,
                       "system_usec": 500_000, "nr_periods": 100,
                       "nr_throttled": 5, "throttled_usec": 500},
          f"complete cgroup cpu.stat snapshot was not preserved: {baseline}")

    cpu_stat.write_text(stat_text(1_500_000, 105, 7))
    current = router.cgroup_cpu_stat(str(cgroup))
    pressure = router.parse_cpu_pressure(PSI_HIGH)
    check(pressure == 0.40, f"PSI threshold input was not parsed exactly: {pressure}")
    decision, reason = router.quota_decision_from_sample(
        300, baseline, current, 5.0, pressure, 0.0, route_demand=0.8)
    check(reason == "ok" and decision and decision["target"] == 250,
          f"valid CPU/cgroup/PSI samples did not produce the expected guarded decision: {reason}, {decision}")

    # The only actuation edge in this test is a mocked systemctl call. Exact
    # effective readback is required before success can be returned.
    with patch.object(router.subprocess, "run") as systemctl, \
            patch.object(router, "unit_quota", return_value=250.0):
        check(router.set_unit_quota("temporary-test.slice", decision["target"]),
              "exact effective quota readback should accept the controller proposal")
        systemctl.assert_called_once_with(
            ["systemctl", "--user", "set-property", "temporary-test.slice", "CPUQuota=250%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)

    with patch.object(router.subprocess, "run") as systemctl, \
            patch.object(router, "unit_quota", return_value=200.0):
        check(not router.set_unit_quota("temporary-test.slice", decision["target"]),
              "effective quota mismatch must reject actuation")
        systemctl.assert_called_once()

    # Pressure just below threshold must hold the quota; no actuator call.
    below_pressure = router.parse_cpu_pressure(PSI_BELOW)
    below_decision, below_reason = router.quota_decision_from_sample(
        300, baseline, current, 5.0, below_pressure, 0.0, route_demand=0.8)
    check(below_reason == "ok" and below_decision["target"] == 300,
          f"pressure immediately below 0.40 changed quota unexpectedly: {below_decision}")

    # Missing/malformed telemetry and counter resets never produce a systemd
    # write, even if the workload-demand estimate would otherwise be high.
    cpu_stat.write_text("usage_usec 1500000\nnr_periods 105\n")
    missing = router.cgroup_cpu_stat(str(cgroup))
    failed, failed_reason = router.quota_decision_from_sample(
        300, baseline, missing, 5.0, pressure, 0.0, route_demand=0.9)
    check(failed is None and failed_reason == "cgroup-stat-unavailable",
          f"missing throttling counters were not failed closed: {failed_reason}")
    malformed_pressure = router.parse_cpu_pressure(PSI_MALFORMED)
    failed, failed_reason = router.quota_decision_from_sample(
        300, baseline, current, 5.0, malformed_pressure, None, route_demand=0.9)
    check(failed is None and failed_reason == "pressure-signals-unavailable",
          f"malformed PSI and absent scheduler telemetry were not failed closed: {failed_reason}")
    reset_stats = {"usage_usec": 10, "nr_periods": 1, "nr_throttled": 0}
    failed, failed_reason = router.quota_decision_from_sample(
        300, baseline, reset_stats, 5.0, pressure, 0.0, route_demand=0.9)
    check(failed is None and failed_reason == "counter-reset-or-anomaly",
          f"counter reset was accepted as a valid decision: {failed_reason}")
    with patch.object(router.subprocess, "run") as systemctl:
        check(failed is None, "failed sample should not expose a controller decision")
        systemctl.assert_not_called()

print("controller sample flow: ok (cgroup cpu.stat -> PSI -> guarded quota decision -> mocked exact readback; missing, malformed, reset and mismatch paths fail closed)")
