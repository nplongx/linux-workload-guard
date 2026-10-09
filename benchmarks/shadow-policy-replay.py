#!/usr/bin/env python3
"""Replay deterministic CPU traces through production threshold and shadow policies.

This is a policy-shape comparison, not a substitute for live systemd/cgroup tests.
"""
import argparse
import importlib.util
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("router", ROOT / "bin/workload-router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

TRACES = {
    "single_sample_spike": [100] + [0] * 20,
    "short_burst": [90] * 2 + [0] * 20,
    "noisy_short_burst": [90, 65] * 2 + [0] * 20,
    "sustained_then_idle": [90] * 10 + [0] * 20,
    "near_threshold_oscillation": [76, 62] * 12 + [0] * 20,
    "moderate_sustained_then_idle": [72] * 10 + [0] * 20,
    "noisy_sustained_then_idle": [90, 65] * 10 + [0] * 30,
}

def baseline_trace(samples, interval, route_threshold, route_samples, clear_threshold, recovery_samples, dwell):
    active = False
    hot = low = 0
    low_since = None
    events = []
    for i, cpu in enumerate(samples):
        now = i * interval
        if not active:
            hot = hot + 1 if cpu >= route_threshold else 0
            if hot >= route_samples:
                active = True; low = 0; low_since = None
                events.append(("route", now))
        else:
            if cpu < clear_threshold:
                low += 1
                if low_since is None: low_since = now
                if low >= recovery_samples and now - low_since >= dwell:
                    active = False; hot = low = 0; low_since = None
                    events.append(("recover", now))
            else:
                low = 0; low_since = None
    return events

def shadow_trace(samples, interval, alpha, route_threshold, route_samples, clear_threshold, recovery_samples, dwell):
    state = {}
    events = []
    for i, cpu in enumerate(samples):
        action = router.update_shadow_policy(
            state, cpu, i * interval, alpha=alpha, route_threshold=route_threshold,
            clear_threshold=clear_threshold, sustained_samples=route_samples,
            recovery_samples=recovery_samples, recovery_dwell_sec=dwell)
        if action: events.append((action, i * interval))
    return events

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--alpha", type=float, default=0.35)
    ap.add_argument("--route-threshold", type=float, default=70)
    ap.add_argument("--route-samples", type=int, default=4)
    ap.add_argument("--baseline-route-samples", type=int, default=None,
                    help="baseline count; defaults to --route-samples")
    ap.add_argument("--clear-threshold", type=float, default=35)
    ap.add_argument("--recovery-samples", type=int, default=10)
    ap.add_argument("--dwell", type=float, default=20)
    ap.add_argument("--output", type=pathlib.Path)
    a = ap.parse_args()
    if a.interval <= 0 or not 0 < a.alpha <= 1 or a.clear_threshold >= a.route_threshold:
        ap.error("require interval > 0, 0 < alpha <= 1, and clear threshold < route threshold")
    baseline_samples = a.route_samples if a.baseline_route_samples is None else a.baseline_route_samples
    if a.route_samples < 1 or baseline_samples < 1:
        ap.error("route sample counts must be at least 1")
    rows = []
    for name, samples in TRACES.items():
        base = baseline_trace(samples, a.interval, a.route_threshold, baseline_samples,
                              a.clear_threshold, a.recovery_samples, a.dwell)
        shadow = shadow_trace(samples, a.interval, a.alpha, a.route_threshold, a.route_samples,
                              a.clear_threshold, a.recovery_samples, a.dwell)
        for policy, events in (("baseline", base), ("shadow", shadow)):
            routes = [t for action, t in events if action == "route"]
            recoveries = [t for action, t in events if action == "recover"]
            rows.append({"trace": name, "policy": policy, "samples": len(samples),
                         "route_time_s": "" if not routes else f"{routes[0]:.2f}",
                         "recovery_time_s": "" if not recoveries else f"{recoveries[0]:.2f}",
                         "route_events": len(routes), "recovery_events": len(recoveries),
                         "events": ";".join(f"{k}@{t:.2f}s" for k,t in events) or "none"})
        b_route = next((t for k,t in base if k == "route"), None)
        s_route = next((t for k,t in shadow if k == "route"), None)
        print(f"{name:32} baseline_route={b_route!s:>5} shadow_route={s_route!s:>5} "
              f"baseline_events={base or 'none'} shadow_events={shadow or 'none'}")
    if a.output:
        import csv
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter="\t")
            w.writeheader(); w.writerows(rows)
        print(f"wrote {a.output}")

if __name__ == "__main__": main()
