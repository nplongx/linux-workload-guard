#!/usr/bin/env python3
"""Deterministic screening replay for step, AIMD, and latency-gradient quota policies.

Inputs are synthetic telemetry, not captured host performance. This compares policy
shape and quota movement only; it does not claim real latency or throughput gains.
"""
import argparse
import csv
import importlib.util
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router", ROOT / "bin/workload-router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

def build_traces():
    rows = {}
    rows["stable_low_pressure"] = [(0.04, 2.0, 0.75, 0.05)] * 12
    rows["sustained_pressure"] = [(0.55, 28.0, 0.95, 0.15)] * 8 + [(0.04, 2.0, 0.80, 0.15)] * 8
    rows["burst_pressure"] = [(0.04, 2.0, 0.70, 0.0)] * 3 + [(0.60, 35.0, 0.95, 0.25)] * 2 + [(0.04, 2.0, 0.75, 0.0)] * 10
    rows["oscillating_pressure"] = [(p, d, 0.90, 0.15) for p, d in
        [(0.12, 7), (0.43, 22), (0.14, 8), (0.46, 24)] * 5]
    rows["high_demand_throttled"] = [(0.03, 1.0, 0.95, 0.30)] * 10
    rows["missing_psi"] = [(None, 2.0, 0.90, 0.20)] * 4 + [(None, 24.0, 0.90, 0.20)] * 3 + [(None, 2.0, 0.90, 0.20)] * 4
    return rows

def gradient2_target(current, latency, previous, low=5.0, high=20.0):
    """Conservative Gradient2-inspired proposal using a short/long latency gradient."""
    if latency is None or previous is None:
        return current
    gradient = latency / max(1.0, previous)
    if latency >= high and gradient > 1.05:
        return max(router.QUOTA_MIN, current * 0.90)
    if latency <= low and gradient <= 1.05:
        return min(router.QUOTA_MAX, current + 10.0)
    return current

def run_policy(samples, policy, start=200.0):
    quota = start
    previous_latency = None
    values = []
    changes = 0
    total_delta = 0.0
    for pressure, latency, demand, throttled in samples:
        if policy == "step":
            target = router.dynamic_quota_target(quota, pressure, demand, throttled, latency)
        elif policy == "aimd":
            target = router.aimd_quota_target(quota, pressure, demand, throttled, latency)
        else:
            target = gradient2_target(quota, latency, previous_latency)
        if target is None:
            target = quota
        target = min(router.QUOTA_MAX, max(router.QUOTA_MIN, target))
        if target != quota:
            changes += 1
            total_delta += abs(target - quota)
        values.append((quota, target, pressure, latency))
        quota = target
        previous_latency = latency
    return values, changes, total_delta

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=pathlib.Path, default=ROOT / "benchmarks/results/controller-replay-2026-10-09.tsv")
    args = ap.parse_args()
    result = []
    for trace, samples in build_traces().items():
        for policy in ("step", "aimd", "gradient2"):
            values, changes, delta = run_policy(samples, policy)
            result.append({"trace": trace, "policy": policy, "samples": len(samples),
                "quota_changes": changes, "total_absolute_quota_delta": f"{delta:.1f}",
                "start_quota": f"{values[0][0]:.1f}", "end_quota": f"{values[-1][1]:.1f}",
                "quota_path": ";".join(f"{v[1]:.1f}" for v in values)})
            print(f"{trace:25} {policy:9} changes={changes:2} delta={delta:5.1f} end={values[-1][1]:.1f}%")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=result[0].keys(), delimiter="\t")
        writer.writeheader(); writer.writerows(result)
    print(f"wrote {args.output}")
    print("NOTE: synthetic telemetry replay only; no real-system latency or throughput claims.")

if __name__ == "__main__":
    main()
