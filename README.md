# Linux Workload Guard

Generic Linux/systemd resource guard for CPU-heavy workloads on developer and automation machines.

## Purpose

Keep one workload from monopolizing the machine while still allowing heavy work to use a controlled amount of CPU.

The design is workload-oriented, not application-oriented. A workload can be a build, test suite, compiler, inference job, browser automation process, agent child process, or another long-running CPU consumer.

## Current capabilities

- CPU quota for a protected workload.
- Dedicated heavy-work systemd slice capped at 400% CPU.
- Automatic routing by known heavy command patterns.
- Automatic routing of unknown child processes after sustained CPU usage.
- Separate CPU budget for browser automation.
- Explicit `run-workload` helper for heavy terminal work.
- User-level systemd; no root daemon required.
- Routing observability with PID, CPU, reason, source cgroup, destination cgroup, and command.
- Optional adaptive routing using lightweight online CPU statistics.
- Low-overhead runtime state/statistics persistence.
- Optional dynamic heavy-workload CPU quota using CPU pressure, scheduler pressure, throttling, and workload demand.
- Explicit `workload-guard protect` helper for placing latency-sensitive commands in the protected slice.
- Automatic recovery of long-lived routed workloads after sustained low CPU usage.
- Restart-safe route-state reconciliation and fail-safe quota telemetry handling.
- cgroup v2 resource enforcement.

## Architecture

```text
                    Linux Workload Guard
                            |
              +-------------+-------------+
              |                           |
        Known heavy task            Sustained CPU
        build/test/ML/...             detection
              |                           |
              +-------------+-------------+
                            |
                     heavy workload slice
                            |
                       CPU quota
```

The router intentionally uses cgroup containment for automatic routing. It considers processes inside the configured parent cgroup and its nested systemd scopes, preventing unrelated system processes from being captured just because they happen to use CPU. The service starts both configured workload slices before routing begins; if systemd cannot provide either cgroup path, the router logs a warning instead of silently skipping routing.

## Default budgets

| Workload class | CPU quota |
|---|---:|
| Protected application/agent | 150% |
| Heavy workload slice | 400% |
| Browser automation | 200% |

Protected workloads also get higher `CPUWeight` (`1000`) than heavy workloads (`100`). This makes scheduler contention favor latency-sensitive protected work while the heavy slice remains quota-bounded.

`100%` is approximately one logical CPU. Quotas are cgroup limits, not CPU priority scores. Dynamic quota also uses the heavy cgroup's `cpu.stat` throttling counters as a demand signal.

## CLI

After installation:

```bash
workload-guard status
workload-guard diagnose
workload-guard quota
workload-guard history
workload-guard profile
workload-guard version
```

`status` shows service state, CPU quotas, and currently routed workloads. Routed entries include PID, current CPU sample, routing reason (`known-heavy`, `sustained-cpu`, or `adaptive`), source cgroup, destination cgroup, and command. `diagnose` analyzes the last hour of history, treats a single old PSI spike as insufficient for a high-pressure finding, and clearly separates observed pressure from descriptive CPU attribution. Unmanaged CPU share does not establish causality. `protect` runs a command directly inside `protected-workload.slice`.

`profile` samples the cgroup v2 tree, reports top cgroups by CPU usage and throttling, shows system CPU PSI, and runs a scheduler wakeup-latency probe. Use `workload-guard profile --duration 5 --samples 200` for a longer snapshot. Scheduler latency is measured as timer-wakeup excess plus this process's `/proc/<pid>/schedstat` runqueue delay; it is a practical host-health signal, not a real-time scheduling guarantee.

`quota` shows the current heavy-slice quota, CPU PSI, throttling counters, and configured scheduler/PSI thresholds used by the dynamic controller.

`history` summarizes the last 1 hour, 6 hours, and 24 hours from lightweight 5-minute snapshots. It reports routing/recovery activity, quota changes, heavy-slice CPU and throttling, CPU PSI, router-process runqueue delay (not host-wide scheduler delay), guarded CPU, and unmanaged CPU. History schema v3 stores router-process runqueue delay under an explicit name; it is not host-wide scheduler delay. History schema v2 and later also store the top five CPU consumers with PID, command, CPU percentage, cgroup class, and cgroup path. Legacy scheduler-delay samples remain readable but are excluded from router-runqueue aggregates. History is stored under `~/.local/state/linux-workload-guard/history.jsonl` by default and is intended for operational visibility rather than high-resolution profiling.

## Configuration file

For host-specific policy, edit `~/.config/linux-workload-guard/workload-guard.env`. The installer creates it from the example only when the file does not already exist, and never overwrites an existing config. `workload-router.service` loads this file automatically on restart.

## Install

```bash
./install.sh
```

The installer installs user-level systemd units and helper commands. The parent workload is configurable with `WORKLOAD_GUARD_PARENT_UNIT`; the default is the generic `protected-workload.slice`. The generic core is independent of a particular application.

After changing the config file, reload the user service:

```bash
systemctl --user restart workload-router.service
```

## Usage

```bash
run-workload npm run build
run-workload cargo test
run-workload pytest
run-workload docker build .
```

Or start an interactive shell inside the heavy workload slice:

```bash
run-workload
```

Normal commands are unchanged.

## Automatic detection

The router samples descendants of the configured parent workload every 2 seconds. Runtime route state is persisted only when it changes, and adaptive statistics are flushed periodically rather than on every sample.

Known heavy commands are routed immediately. Unknown commands are routed after four consecutive samples at or above 70% CPU, giving an 8-second sustained threshold.

This avoids reacting to short-lived CPU spikes.

Routed long-lived workloads are eligible for recovery after sustained CPU usage below 35% for 10 samples and at least 20 seconds. Recovery moves the process back to its original source cgroup when that cgroup still exists; if the source scope has disappeared, it falls back to the configured parent cgroup. A later sustained CPU burst can route it again.

Adaptive mode is deliberately opt-in. Dynamic quota is also opt-in; when enabled it adjusts only the heavy workload slice, using CPU PSI pressure, scheduler runqueue delay, heavy-cgroup throttling, learned demand, bounded steps, and a 100–400% default range. High PSI alone is treated as strong pressure only at 40% by default; scheduler delay has its own 20ms high-pressure threshold. This avoids shrinking heavy-workload capacity because of moderate host contention while still reacting when the host is genuinely under scheduling pressure. It keeps a small per-command-class CPU baseline using online mean/variance updates; it is not a black-box model and requires a minimum history before it can route. Known-heavy and sustained-CPU rules remain active regardless of adaptive mode.

## Configuration

Environment variables:

- `WORKLOAD_GUARD_SAMPLE_SEC` — sample interval, default `2`
- `WORKLOAD_GUARD_SUSTAINED_SAMPLES` — consecutive hot samples, default `4`
- `WORKLOAD_GUARD_CPU_THRESHOLD` — percent of one logical CPU, default `70`
- `WORKLOAD_GUARD_ADAPTIVE` — enable adaptive routing, default `false`.
- `WORKLOAD_GUARD_LEARNING_RATE` — EWMA-style learning rate, default `0.15`.
- `WORKLOAD_GUARD_ROUTE_THRESHOLD` — adaptive score required for routing, default `0.75`.
- `WORKLOAD_GUARD_COOLDOWN_SEC` — minimum time between route attempts for a PID, default `20`.
- `WORKLOAD_GUARD_MIN_SAMPLES` — historical samples required before adaptive scoring, default `8`.
- `WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD` — CPU threshold below which a routed workload can recover, default `35`.
- `WORKLOAD_GUARD_RECOVERY_SAMPLES` — consecutive low-CPU samples required for recovery, default `10`.
- `WORKLOAD_GUARD_RECOVERY_DWELL_SEC` — minimum low-CPU dwell before recovery, default `20` seconds.
- `WORKLOAD_GUARD_DYNAMIC_QUOTA` — enable dynamic heavy-slice quota, default `false`.
- `WORKLOAD_GUARD_QUOTA_INTERVAL_SEC` — quota controller interval, default `5`.
- `WORKLOAD_GUARD_QUOTA_MIN` / `MAX` — quota bounds, default `100` / `400`.
- `WORKLOAD_GUARD_QUOTA_STEP` — maximum quota change per controller step, default `50`.
- `WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH` / `LOW` — CPU PSI pressure thresholds, default `0.40` / `0.10`.
- `WORKLOAD_GUARD_QUOTA_THROTTLE_HIGH` — heavy cgroup throttling ratio that permits quota growth, default `0.10`.
- `WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC` — minimum time between quota direction changes, default `10`.
- `WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS` / `LOW_MS` — scheduler runqueue-delay thresholds, default `20` / `5` ms.
- `WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC` — adaptive-statistics disk flush interval, default `10`.
- `WORKLOAD_GUARD_HISTORY_INTERVAL_SEC` — runtime health snapshot interval, default `300` seconds.
- `WORKLOAD_GUARD_EXCLUDE_PATTERNS` — comma-separated command-line patterns excluded from routing.
- `WORKLOAD_GUARD_BROWSER_PROCESS_PATTERN` — process pattern for the optional browser automation budget; empty by default.

## Host integrations

The repository contains generic workload primitives plus optional integrations for common workload classes. These are integrations, not the identity of the project:

- `systemd/protected-workload.slice` — 150% protected workload budget.
- `systemd/heavy-workload.slice` — 400% heavy workload budget.
- `systemd/browser-automation.slice` — 200% browser automation budget.

A host can attach any application or agent to the protected workload slice without changing the core router. For example: `systemd-run --user --scope --slice=protected-workload.slice <command>`. The optional browser automation timer only acts when `WORKLOAD_GUARD_BROWSER_PROCESS_PATTERN` is configured.

## Verify

```bash
systemctl --user status workload-router.service
systemctl --user status heavy-workload.slice
systemctl --user show heavy-workload.slice -p CPUQuotaPerSecUSec -p ControlGroup
journalctl --user -u workload-router.service -n 50 --no-pager
```

## Uninstall

```bash
./uninstall.sh
```

Only files installed by this project are removed. Application data and projects are not deleted.

## Requirements

- Linux with cgroup v2
- systemd user manager
- Python 3
- POSIX shell

The project is designed for user-level installation and does not require root.

## Testing

```bash
./test.sh
./tests/integration-routing.sh
./tests/recovery-routing.sh
```

Tests cover Python syntax, routing/exclusion logic, quota decisions, real cgroup routing, recovery after sustained low CPU, and route-state reconciliation across router restart. The integration tests require a systemd user manager and cgroup v2; otherwise they skip cleanly.

## Benchmark

### CPU quota scaling (2026-10-09)

This is a **fixed-work quota sweep**, not a benchmark of the dynamic controller's decision quality. It measures how long one CPU-bound workload takes when `heavy-workload.slice` is assigned different static CPU quotas.

**Test environment**

- Intel Core i5-8250U, 4 cores / 8 logical CPUs (Hyper-Threading)
- Linux kernel `7.0.0-38-generic`; Ubuntu systemd `259.5-0ubuntu3.4`
- GCC `15.2.0`; benchmark compiled with `-O2 -march=native`
- Python `3.14.4` is used by the guard runtime; the benchmark workload itself is C
- Desktop/browser activity was present; this was not an isolated or idle host

**Method**

- The C workload performs the same fixed amount of integer-mixing work in **8 worker processes**, each running **250,000,000 iterations**.
- Tested quotas: 100%, 150%, 200%, 250%, 300%, 350%, and 400% (100% represents one logical CPU's worth of quota).
- **5 runs per quota, 35 trials total.** Quota order was interleaved across rounds; the script waits 2 seconds after changing quota and 2 seconds between trials. All 35 trials completed and are included; no results were intentionally excluded.
- Elapsed time is measured around the complete worker group. Cgroup `cpu.stat` deltas record CPU time and throttling counters. CPU PSI `some avg10/avg60` is sampled at the end of each trial and is host-wide rolling telemetry, not workload-only attribution.
- Summary values are arithmetic mean and sample standard deviation of elapsed time. Speedup is the 100% mean divided by each quota's mean. “Throttled periods” is the pooled `nr_throttled / nr_periods` percentage, not the percentage of wall-clock time spent throttled.

| Heavy quota | Mean elapsed | Std. dev. | Median | Periods throttled | Speedup vs 100% |
|---:|---:|---:|---:|---:|---:|
| 100% | 18.084 s | 1.378 s | 17.919 s | 99.3% | 1.00x |
| 150% | 11.895 s | 1.129 s | 12.049 s | 99.3% | 1.52x |
| 200% | 9.031 s | 0.442 s | 8.948 s | 98.0% | 2.00x |
| 250% | 7.688 s | 0.143 s | 7.707 s | 93.0% | 2.35x |
| 300% | 6.253 s | 0.653 s | 5.946 s | 89.5% | 2.89x |
| 350% | 5.468 s | 0.141 s | 5.458 s | 91.5% | 3.31x |
| 400% | 5.083 s | 0.304 s | 5.075 s | 71.7% | 3.56x |

**Interpretation:** On this machine and under the observed background load, increasing the quota from 100% to 400% reduced mean completion time by **71.9%** (18.084 s to 5.083 s, or 3.56x speedup). Returns diminish at the upper end: moving from 350% to 400% improved mean elapsed time by about **7.0%**. The run-to-run coefficient of variation ranged from 1.9% to 10.4%, so the 300% results in particular were noisier than the 250% and 350% results.

The host's sampled CPU PSI `some avg10` values were high during the sweep (roughly 36–82% across trials), while `full` PSI remained 0%. These are host-wide rolling measurements influenced by both the benchmark and other processes; they should not be read as isolated latency measurements or evidence that the guard improves interactive responsiveness. CPU frequency, thermal state, scheduler contention, and Hyper-Threading can all affect timings. This is one machine/run and does not establish a universal optimal quota or quantify the dynamic controller's policy benefits.

The per-trial raw data is checked in at [`benchmarks/results/lwg-quota-benchmark-2026-10-09.tsv`](benchmarks/results/lwg-quota-benchmark-2026-10-09.tsv). Reproduce the same workload size and repetition count with:

```bash
RESULT=/tmp/lwg-quota-results.tsv REPS=5 WORK=250000000 ./benchmarks/quota-benchmark.sh
```

The script stops `workload-router.service` for the static quota sweep and restarts it if it was active before the run. Its cleanup sets the quota to 400% before restarting; if dynamic quota is enabled, the controller can subsequently change the quota in response to live telemetry.

### Router contention test (2026-10-09)

This second test evaluates the **end-to-end routing behavior** with a small synthetic interactive task running alongside a CPU-bound process. It is separate from the static quota sweep above.

**Method and conditions**

- Five 20-second trials per mode (router off / router on), with the order alternated between rounds: **10 trials and 9,990 probe samples total**.
- One CPU-bound Python worker and one periodic probe run as separate systemd scopes under `protected-workload.slice`. The protected slice is temporarily set to a 100% CPU quota; `heavy-workload.slice` starts at 400%.
- The probe schedules a small 5,000-iteration task every 20 ms and records both wakeup lateness (actual start minus scheduled time) and task execution duration. These are synthetic scheduler/CPU-contention indicators, not application-level UI latency.
- In router-on trials, the installed router configuration was used, including its 2-second sampling, four sustained high-CPU samples, adaptive classification, and dynamic quota controller. The CPU-bound worker was successfully moved to `heavy-workload.slice` in **all five trials**, after 8.4–9.5 seconds. It remained in the protected slice in baseline trials with the router stopped.
- For steady-state comparisons, baseline samples are taken after the first 12 seconds; router-on samples are taken from two seconds after the actual move. Summary numbers below are the **median of the five per-trial percentiles**, which is less sensitive to a single noisy run than averaging all samples.

| Steady-state metric | Router off | Router on | Change |
|---|---:|---:|---:|
| Probe wakeup lateness, p95 | 0.176 ms | 0.156 ms | 11.7% lower |
| Probe wakeup lateness, p99 | 2.032 ms | 1.810 ms | 10.9% lower |
| Probe task execution time, p95 | 8.418 ms | 3.398 ms | 59.6% lower |

The steady-state task-execution p95 was consistent across router-on trials (3.15–3.57 ms), while baseline results varied more (3.37–11.65 ms). Wakeup-lateness results were noisy and the p95 improvement was modest; one router-on trial also had a high wakeup-lateness tail. Therefore, this test supports the narrower conclusion that routing this particular CPU worker out of the protected slice reduced the synthetic probe's execution time in this environment. It **does not prove a general 59.6% improvement in real interactive responsiveness**, and it cannot isolate routing from all background scheduling, CPU-frequency, or dynamic-quota effects.

Per-trial summaries are available in [`benchmarks/results/lwg-routing-contention-2026-10-09.tsv`](benchmarks/results/lwg-routing-contention-2026-10-09.tsv); all 9,990 raw probe samples are in [`benchmarks/results/lwg-routing-contention-2026-10-09.samples.tsv`](benchmarks/results/lwg-routing-contention-2026-10-09.samples.tsv). Reproduce the test with:

```bash
REPS=5 DURATION=20 WORKERS=1 \
  RESULT=/tmp/lwg-routing-contention.tsv \
  ./benchmarks/routing-contention.py
```

**This is a controlled stress test and temporarily changes runtime slice quotas and stops/restarts the user router.** The script restores the quotas and router active state observed at startup. Run it when a short burst of CPU load is acceptable; the router-on mode uses the installed dynamic quota settings.

## Release

Releases use semantic versioning. See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT
