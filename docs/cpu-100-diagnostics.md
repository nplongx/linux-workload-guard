# CPU 100% diagnosis and safe recovery

This runbook is read-only until an operator explicitly authorizes a rollback on a dedicated test host. Do not restart the production router, change the production quota, or generate load to force a PSI event as part of diagnosis.

## 1. Distinguish the signals

- **CPU quota throttling:** `nr_throttled / nr_periods` and `throttled_usec` deltas from the workload cgroup's `cpu.stat`. High throttling means the cgroup hit its quota; it does not alone prove host-wide CPU contention.
- **CPU pressure:** PSI `some avg10` from `/proc/pressure/cpu` means at least some runnable tasks were stalled. PSI is a percentage, not CPU utilization. `full` is a separate signal and may be absent/zero on some kernels.
- **Host capacity:** compare `nproc`, `uptime`, `/proc/loadavg`, host PSI, and cgroup counters over the same monotonic interval. Do not compare cumulative counters without taking deltas.
- **Router overhead:** sample router CPU ticks/RSS separately from workload CPU. A small router CPU share does not explain away a throughput regression without paired workload evidence.
- **Routing correctness:** verify actual process placement and PID start-time identity; do not infer successful routing from a decision log alone.

## 2. Capture a read-only snapshot

```sh
date --iso-8601=seconds
uname -a
cat /etc/os-release
systemd --version | head -1
stat -fc %T /sys/fs/cgroup
nproc
uptime
cat /proc/loadavg
cat /proc/pressure/cpu
systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus
systemctl --user show heavy-workload.slice -p ActiveState -p CPUQuotaPerSecUSec -p ControlGroup
```

Read the `ControlGroup` property and inspect that cgroup's `cpu.stat`, `cpu.max`, `cpu.weight`, and `cpu.pressure` where available. Record the same values at the end of a defined interval. Never interpret missing or malformed counters as zero.

Use `./bin/workload-guard status`, `./bin/workload-guard diagnose`, and `./bin/workload-profile` for supplementary observations. Diagnostics are not causal attribution; retain raw outputs and timestamps.

## 3. Interpret safely

| Observation | Interpretation | Safe next action |
|---|---|---|
| High `nr_throttled` ratio, low host PSI | Workload may be hitting its configured quota without broad host contention | Verify effective quota and workload scope; do not raise quota automatically |
| High host PSI and high run queue | Host contention is plausible | Identify competing workloads and repeat only in an approved quiet window |
| High CPU utilization, low PSI, low throttling | CPUs may be busy but not persistently stalled or quota-limited | Inspect per-process CPU and throughput before changing policy |
| PSI missing/malformed or counters reset | Evidence is incomplete | Mark telemetry unavailable; do not treat it as low pressure |
| Router restart/PID change or route identity mismatch | Lifecycle/observability anomaly | Preserve journal and route state; stop acceptance interpretation |

## 4. Rollback boundary

Follow [`operator-rollback.md`](operator-rollback.md) only on a dedicated, authorized test host. Record the original config hash, file mode, service state, route state, and effective quota before changes. Disable dynamic quota only in that test environment, restart only its service, verify the previously recorded quota is preserved, restore the config byte-for-byte and permissions, restart, and verify health again. Never guess the prior quota or assume disabling dynamic quota restores it.

## 5. Performance evidence rules

- Keep threshold `0.90` unchanged. Include every pair, including adverse trials.
- Use preregistered ABBA order, same aggregate cap, same workload and common steady window; collect paired throughput, latency percentiles, effective weights, cgroup usage/throttling deltas, PSI and router overhead.
- If the preregistered host-PSI confounding limit is exceeded, report `CONFOUNDED_INCONCLUSIVE`; do not infer causality or change routing policy.
- A second CI OS job is not an independent host. A benchmark run is not production-controller PSI threshold validation.
- Evidence directories and manifests must be unique and immutable. Hash raw samples, evaluator output, environment, logs, protocol and source revision.
