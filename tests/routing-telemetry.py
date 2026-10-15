#!/usr/bin/env python3
"""Deterministic tests for real cgroup-v2 benchmark telemetry parsing."""
import importlib.util
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("routing_contention", ROOT / "benchmarks/routing-contention.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(prefix="lwg-telemetry-test-") as temporary:
    cgroup = pathlib.Path(temporary)
    (cgroup / "cpu.stat").write_text(
        "usage_usec 12345\nuser_usec 10000\nsystem_usec 2345\n"
        "nr_periods 42\nnr_throttled 7\nthrottled_usec 890\n"
    )
    (cgroup / "cpu.pressure").write_text(
        "some avg10=1.25 avg60=0.75 avg300=0.50 total=123456\n"
        "full avg10=0.10 avg60=0.05 avg300=0.01 total=1234\n"
    )
    (cgroup / "cpu.weight").write_text("80\n")
    counters = module.read_cpu_stat(cgroup)
    assert counters["usage_usec"] == 12345
    assert counters["nr_periods"] == 42
    assert counters["nr_throttled"] == 7
    assert counters["throttled_usec"] == 890
    pressure = module.read_pressure(cgroup)
    assert pressure["psi_some_avg10"] == 1.25
    assert pressure["psi_some_total"] == 123456
    assert pressure["psi_full_avg10"] == 0.10
    assert (cgroup / "cpu.weight").read_text().strip() == "80"

    (cgroup / "cpu.stat").write_text("usage_usec 9\nunknown_counter not-a-number\n")
    counters = module.read_cpu_stat(cgroup)
    assert counters == {"usage_usec": 9}

    (cgroup / "cpu.stat").unlink()
    assert module.read_cpu_stat(cgroup) == {}

print("routing interval telemetry parsing: ok (real counters, weight readback, PSI, missing-data fail-closed)")
