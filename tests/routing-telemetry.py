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

    # A missing delegated-controller counter must not look like valid telemetry.
    (cgroup / "cpu.stat").write_text("usage_usec 9\nnr_periods 2\n")
    assert module.read_cpu_stat(cgroup) == {}
    for malformed in (
        "usage_usec 9\nnr_periods 2\nnr_throttled nope\n",
        "usage_usec -1\nnr_periods 2\nnr_throttled 1\n",
        "usage_usec 9\nnr_periods 2\nnr_throttled 1\nusage_usec 10\n",
        "usage_usec 9\nnr_periods 2\nnr_throttled 3\n",
        "usage_usec 9\nnr_periods 2\nmalformed\nnr_throttled 1\n",
    ):
        (cgroup / "cpu.stat").write_text(malformed)
        assert module.read_cpu_stat(cgroup) == {}, repr(malformed)

    (cgroup / "cpu.stat").unlink()
    assert module.read_cpu_stat(cgroup) == {}

    # PSI is host/cgroup telemetry, not a default-zero signal. Incomplete,
    # duplicate, non-finite, out-of-range, or malformed rows are unavailable.
    for malformed in (
        "some avg10=1 avg60=2 avg300=3\n",
        "some avg10=nan avg60=2 avg300=3 total=1\n",
        "some avg10=101 avg60=2 avg300=3 total=1\n",
        "some avg10=1 avg10=2 avg60=2 avg300=3 total=1\n",
        "some avg10=1 avg60=2 avg300=3 total=1\nsome avg10=1 avg60=2 avg300=3 total=1\n",
        "some avg10=1 avg60=2 avg300=3 total=1\nfull avg10=1 avg10=2 avg60=2 avg300=3 total=1\n",
    ):
        (cgroup / "cpu.pressure").write_text(malformed)
        assert module.read_pressure(cgroup) == {}, repr(malformed)
    (cgroup / "cpu.pressure").unlink()
    assert module.read_pressure(cgroup) == {}

print("routing interval telemetry parsing: ok (required counters, malformed/reset-safe snapshots, strict PSI, missing-data fail-closed)")
