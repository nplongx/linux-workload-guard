#!/usr/bin/env python3
"""Deterministic quota pressure/recovery replay using the production target function.

Synthetic signals only. Models the configured observation interval and minimum
post-change dwell; it does not replace a live host-pressure test.
"""
import argparse
import csv
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router", ROOT / "bin/workload-router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def replay():
    # Ten high-pressure observations followed by ten low-pressure/high-demand
    # observations. Each observation represents the configured quota interval.
    trace = [("high_pressure", 0.65, 0.95)] * 10 + [("recovery", 0.03, 0.90)] * 10
    quota = 300.0
    last_change = 0.0
    dwell_until = 0.0
    rows = []
    changes = []
    interval = router.QUOTA_INTERVAL_SEC
    dwell = router.QUOTA_MIN_DWELL_SEC
    for index, (phase, pressure, demand) in enumerate(trace, 1):
        elapsed = index * interval
        target = quota
        if elapsed >= dwell_until and elapsed - last_change >= interval:
            target = router.dynamic_quota_target(quota, pressure, demand)
        if target != quota:
            changes.append(elapsed)
            quota = target
            last_change = elapsed
            dwell_until = elapsed + dwell
        rows.append({"elapsed_s": elapsed, "phase": phase, "pressure": pressure,
                     "demand": demand, "quota_pct": f"{quota:.0f}",
                     "changed": int(bool(changes and changes[-1] == elapsed))})
    high = [float(r["quota_pct"]) for r in rows if r["phase"] == "high_pressure"]
    recovery = [float(r["quota_pct"]) for r in rows if r["phase"] == "recovery"]
    assert all(a >= b for a, b in zip(high, high[1:])), high
    assert all(a <= b for a, b in zip(recovery, recovery[1:])), recovery
    assert all(router.QUOTA_MIN <= float(r["quota_pct"]) <= router.QUOTA_MAX for r in rows)
    assert all(b - a >= dwell for a, b in zip(changes, changes[1:])), changes
    assert high[-1] == router.QUOTA_MIN, high
    assert recovery[-1] > recovery[0], recovery
    return rows, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=pathlib.Path,
                    default=ROOT / "benchmarks/results/quota-pressure-replay-2026-10-09.tsv")
    args = ap.parse_args()
    rows, changes = replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"pressure replay: ok; quota changes at {','.join(map(str, changes))} seconds")
    print(f"high-pressure end={next(float(r['quota_pct']) for r in rows if r['phase']=='high_pressure' and r['elapsed_s']==50)}%; final recovery quota={rows[-1]['quota_pct']}%")
    print("NOTE: synthetic signals; does not prove live behavior under host-wide pressure.")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
