# Aggregate-saturation routing (experimental, opt-in)

`WORKLOAD_GUARD_AGGREGATE_ROUTING=true` enables an additional routing proposal for multiple long-lived CPU workers that individually remain below the existing per-process trigger. It does **not** change `WORKLOAD_GUARD_CPU_THRESHOLD`, dynamic quota behavior, or the production default. Keep it disabled until representative workload, fairness, and cross-host gates are reviewed.

## Policy contract

- The controller reads the parent's effective systemd `CPUQuotaPerSecUSec` every five seconds. Missing, unlimited, malformed, or otherwise unavailable quota telemetry fails closed.
- A worker contributes only when its latest valid per-process CPU sample is at least `WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU` (default 20% of one logical CPU).
- Entry requires at least two contributing workers and summed observed demand at least `WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO` (default 0.85) of the observed parent quota for three consecutive controller samples.
- Once active, at most one eligible protected worker is routed per controller sample, with deterministic rotating PID order. Existing process-starttime and cgroup-membership checks remain mandatory before the cgroup write.
- The active latch clears after three consecutive samples at or below `WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO` (default 0.65) of parent quota, or fewer than two contributing workers. This is a 20-point entry/exit hysteresis band.
- Invalid quota telemetry clears the pending/active decision; no new aggregate route is proposed. Existing per-process routing and recovery behavior remain separate.
- The policy routes individual processes, not entire process trees/scopes. Short-lived compiler/build subprocesses may churn too quickly to be routed reliably; this is a known scope limitation, not an accepted build-workload pass.

## Configuration

```ini
WORKLOAD_GUARD_AGGREGATE_ROUTING=false
WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO=0.85
WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO=0.65
WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU=20
WORKLOAD_GUARD_AGGREGATE_SUSTAINED_SAMPLES=3
```

The example values are bounds/policy defaults, not a recommendation to enable this on an unvalidated host. A 100% parent quota is the tested scenario; behavior with other quotas must be validated against the effective readback. Do not lower the existing 70% per-process production threshold to manufacture a route. Keep `WORKLOAD_GUARD_DYNAMIC_QUOTA=false` unless the separate quota-controller gate has been accepted.

## Evidence and rollback

The deterministic controller-hardening suite exercises entry/exit hysteresis, missing quota telemetry, oscillating demand, single-worker/burst false-positive resistance, and fair rotation. The isolated two-worker synthetic smoke benchmark uses a common 100% parent quota and temporary child slice. Its single pair is functional evidence only, not performance acceptance. The compiler/build smoke currently fails routing validity because the worker model creates short-lived compiler subprocesses; retain that result as `INCOMPLETE` and do not interpret its latency numbers as a paired performance result.

To disable the experiment, set `WORKLOAD_GUARD_AGGREGATE_ROUTING=false` in the test host's configuration and restart only that test host's user service during an approved window. This flag does not alter quotas. Do not restart the production router for this experiment. The checked-in example remains disabled by default.
