# Aggregate-saturation routing (experimental, opt-in)

`WORKLOAD_GUARD_AGGREGATE_ROUTING=true` enables an additional scope-aware policy for multiple CPU-heavy terminal workloads that individually remain below the existing per-process trigger. It does **not** change `WORKLOAD_GUARD_CPU_THRESHOLD`, dynamic quota behavior, or the production default. Keep it disabled until representative workload, fairness, and cross-host gates are reviewed.

## Policy contract

- The controller recognizes only direct child scopes named `lwg-workload-*.scope` under the configured protected slice with the exact systemd description `Linux Workload Guard aggregate candidate`. The shipped `run-workload` helper creates this kind of scope for recognized build/compiler/test commands and its interactive shell mode only when the opt-in flag is explicitly true in the installed config. Ordinary scopes, processes outside the parent, and arbitrary PID descendants are not aggregate candidates.
- Candidate discovery is bounded to 64 direct workload scopes. If the bound is exceeded, no new route is proposed and existing controller-owned weights are reconciled/restored; the controller does not silently truncate demand and route on a partial aggregate.
- Scope CPU demand comes from the scope's real `cpu.stat` `usage_usec` delta and the scope cgroup inode. This deliberately does not require `nr_periods` or `nr_throttled`; those remain required for the separate dynamic-quota gate. Missing, stale, reset, or malformed usage counters fail closed for aggregate routing.
- A scope contributes only when its measured CPU usage is at least `WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU` (default 20% of one logical CPU). Entry requires at least two contributing scopes and summed observed demand at least `WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO` (default 0.85) of the live parent quota for three consecutive samples.
- Once active, at most one eligible scope per sample is assigned `CPUWeight=WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT` (default 20), in deterministic round-robin order. This is a **relative scheduler weight**, not a hard quota. The existing parent `CPUQuota` remains the hard aggregate budget; the controller never increases it for this policy.
- The active latch clears after three consecutive samples at or below `WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO` (default 0.65) of parent quota, or fewer than two contributing scopes. This is a 20-point entry/exit hysteresis band.
- Scope routing does not move PIDs or processes between cgroups. All descendants inherit the stable scope cgroup, so compiler subprocess churn does not change workload identity; systemd continues owning the scope. The controller verifies the unit's `ControlGroup`, active state, cgroup inode, and `CPUWeight` readback before accepting actuation.
- The original weight, target weight, cgroup path, and inode are atomically persisted in a mode-0600 scope-state file. Recovery restores the original weight only if the same cgroup instance still exists and its current weight still equals the controller's target; external weight changes are preserved. SIGTERM restores controlled weights; after SIGKILL, the next service start reconciles persisted records. Recreated scopes with the same name are treated as new identities.
- Missing quota/usage telemetry restores controller-owned weights and resets the active decision. A failed property write/readback is not counted as a successful route. The existing per-process routing/recovery policy remains separate.

## Configuration

```ini
WORKLOAD_GUARD_AGGREGATE_ROUTING=false
WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO=0.85
WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO=0.65
WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU=20
WORKLOAD_GUARD_AGGREGATE_SUSTAINED_SAMPLES=3
WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT=20
```

The example values are bounds/policy defaults, not a recommendation to enable this on an unvalidated host. A 100% parent quota is the primary tested scenario; behavior with other quotas must be validated against effective readback. CPUWeight affects competition among sibling scopes but does not itself guarantee that a worker consumes less CPU when no competing work exists. Do not lower the existing 70% per-process production threshold to manufacture a route. Keep `WORKLOAD_GUARD_DYNAMIC_QUOTA=false` unless the separate quota-controller gate has been accepted.

## Evidence and rollback

The deterministic controller-hardening suite exercises entry/exit hysteresis, missing/stale/reset usage telemetry, cgroup inode recreation, single-worker/burst false-positive resistance, and fair rotation. The isolated paired benchmark uses a common 100% parent quota and checks that each intended worker scope's live `cpu.weight` reached the configured target before the common steady window. Existing PID-oriented build smoke artifacts remain immutable and `INCOMPLETE`; the scope-aware implementation must earn its own clean-commit five-pair evidence before any performance conclusion.

To disable the experiment, set `WORKLOAD_GUARD_AGGREGATE_ROUTING=false` in the test host's configuration and restart only that test host's user service during an approved window. `run-workload` will then return recognized heavy commands to the existing `heavy-workload.slice` path. This flag does not alter quotas. Do not restart the production router for this experiment. The checked-in example remains disabled by default.
