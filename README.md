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

### Routing-policy baseline and validation

Before changing the routing algorithm, capture the current behavior as a baseline. A successful route log records the decision reason (`known-heavy`, `sustained-cpu`, or `adaptive`), sampled CPU, configured sustained threshold, and hot-sample count. Failed cgroup moves are warnings that include the target and reason. Recovery events are logged separately. These are operational decision records, not a complete per-sample audit trail.

Run the systemd/cgroup-v2 integration checks on a host with a working user systemd manager:

```bash
./tests/integration-routing.sh
./tests/recovery-routing.sh
```

The routing test first applies a 120 ms synthetic CPU burst and checks that it is not routed, then applies sustained CPU load and reports observed detection latency. The recovery test reports elapsed time from switching the worker to idle until it returns to its source cgroup. These are controlled regression measurements, not estimates of general application responsiveness. The scripts stop the installed router temporarily when necessary and restore its active state; run them when a short synthetic CPU workload is acceptable.

On 2026-10-09, five repeated trials on one host produced **0 false routes in 5 transient-burst trials**. Sustained-load detection had a median of **1.05 s** (range 0.79–1.07 s); idle-to-recovery had a median of **1.57 s** (range 1.57–1.82 s). These tests used accelerated settings (250 ms sampling, 3 hot samples, and 1 s recovery dwell), so the timings are regression baselines for the test configuration—not the production defaults of 2 s sampling and 20 s recovery dwell. Raw per-trial results are in [`benchmarks/results/routing-policy-baseline-2026-10-09.tsv`](benchmarks/results/routing-policy-baseline-2026-10-09.tsv).

For router-off/router-on probe latency and quota scaling, use the reproducible benchmarks in the section below. Keep workload, quotas, sample interval, and background conditions consistent when comparing future policy changes; report per-trial results and dispersion rather than only a best run.

### Experimental EWMA + hysteresis shadow mode

The experimental shadow policy evaluates an alternative CPU signal without changing production routing or quota. It tracks an EWMA per PID and uses separate entry and exit thresholds; route/recovery candidates are emitted as `shadow decision=route` or `shadow decision=recover` log entries. Shadow tracking includes both protected and heavy slices so the hypothetical recovery path can be observed even when the existing router has already moved a process. It never calls `move()` or `set_unit_quota()` on behalf of the shadow policy.

Enable it explicitly in `~/.config/linux-workload-guard/workload-guard.env`:

```bash
WORKLOAD_GUARD_SHADOW=true
WORKLOAD_GUARD_SHADOW_EWMA_ALPHA=0.35
WORKLOAD_GUARD_SHADOW_ROUTE_THRESHOLD=70
WORKLOAD_GUARD_SHADOW_CLEAR_THRESHOLD=35
WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES=4
WORKLOAD_GUARD_SHADOW_RECOVERY_SAMPLES=10
WORKLOAD_GUARD_SHADOW_RECOVERY_DWELL_SEC=20
```

Then restart the user service and inspect `journalctl --user -u workload-router.service`. The default is disabled. Shadow output is emitted only on hypothetical state transitions to avoid noisy per-sample logs. Compare shadow route/recovery times with actual `route decision=accepted` and `unrouted` events; because the existing policy can move a PID before the shadow policy does, this comparison measures policy differences, not an isolated A/B test. Do not promote the shadow policy to enforcement until transient bursts, sustained load, recovery, and probe latency have been evaluated across repeated trials.

An initial live shadow run on 2026-10-09 observed two sustained-load cycles and one periodic-burst workload. Shadow route candidates were logged 7–11 ms before production route events in both sustained trials; in the one trial that ran long enough to complete shadow recovery, its recovery candidate appeared about 4.0 s after production recovery. No route event was observed during the periodic 120 ms burst workload. This is only a smoke test (two sustained cycles, one burst run), not statistically sufficient to claim superiority. The timestamps and caveats are recorded in [`benchmarks/results/shadow-live-2026-10-09.tsv`](benchmarks/results/shadow-live-2026-10-09.tsv).

As a repeatability check, the integration routing test and recovery test were each run five additional times with accelerated test-only settings. All five 120 ms bursts were left unrouted; sustained detection was 0.79–0.80 s (median 0.79 s), and idle-to-recovery was 1.56–1.57 s (median 1.56 s). These figures characterize the existing integration harness, not EWMA shadow latency; they validate that the production baseline is repeatable before policy comparison. Per-trial results: [`benchmarks/results/shadow-regression-2026-10-09.tsv`](benchmarks/results/shadow-regression-2026-10-09.tsv).

To compare policy behavior on exactly the same CPU samples, `benchmarks/shadow-policy-replay.py` replays deterministic traces through a CPU-threshold baseline and the EWMA/hysteresis function. With the default production-like parameters (2 s sampling, 70% route threshold, 4 samples, 35% clear threshold, 10 recovery samples and 20 s dwell), both policies rejected single-sample and two-sample flat bursts, and both routed sustained 90% and 72% traces at 6 s. On recovery, the baseline returned at 40 s; shadow returned at 44 s for the 90% trace and 42 s for the 72% trace. The alternating 76%/62% trace triggered neither policy. However, the shadow policy **did route a short noisy burst** alternating 90%/65% for only four samples, while the baseline did not; on a longer noisy 90%/65% workload, shadow routed at 6 s while baseline did not route because no individual sample stayed above threshold for four consecutive samples. This exposes a real trade-off: smoothing can detect elevated average load, but can also prolong a brief noisy burst into a route candidate. Recovery was slower in the tested sustained traces. These deterministic cases do not establish a general improvement, and argue against enabling enforcement with current parameters. Results are in [`benchmarks/results/shadow-replay-2026-10-09.tsv`](benchmarks/results/shadow-replay-2026-10-09.tsv). Re-run with `python3 benchmarks/shadow-policy-replay.py --output /tmp/shadow-replay.tsv` when tuning parameters.

A first tuning candidate uses five shadow high samples while leaving the current baseline at four (`python3 benchmarks/shadow-policy-replay.py --route-samples 5 --baseline-route-samples 4`). In this trace set, it avoids the short noisy-burst route candidate, while still routing the longer noisy workload; sustained flat-load detection is 2 s slower than baseline (8 s vs 6 s) and recovery remains slower. Candidate results are in [`benchmarks/results/shadow-replay-5sample-candidate-2026-10-09.tsv`](benchmarks/results/shadow-replay-5sample-candidate-2026-10-09.tsv). This is a tuning hypothesis for more live shadow observation, not a recommendation to change production defaults.

The live shadow integration test (`tests/shadow-routing.sh`) runs the router in an isolated test configuration with accelerated sampling and verifies the full sequence: a 120 ms burst is not routed by production or shadow, sustained load creates both a real route and a shadow candidate, and idle load results in real recovery and a shadow recovery candidate. Three consecutive live trials passed on 2026-10-09: sustained route latency was 1.05–1.08 s (median 1.06 s), and completion of both recovery paths took 3.08–3.36 s after the observed route (median 3.35 s). These are test-only accelerated timings, not production-default latency estimates. Results: [`benchmarks/results/shadow-live-regression-2026-10-09.tsv`](benchmarks/results/shadow-live-regression-2026-10-09.tsv). The test restores the user service to its prior active state on exit.

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
- `WORKLOAD_GUARD_AIMD_SHADOW` — log-only AIMD quota proposals alongside telemetry, default `false`; it never applies quota changes.
- `WORKLOAD_GUARD_AIMD_DECREASE` — AIMD multiplicative decrease factor, default `0.80`.
- `WORKLOAD_GUARD_AIMD_ADD` — AIMD additive increase in CPU quota percentage points, default `10`.
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

### Controller research and safe comparison

`WORKLOAD_GUARD_AIMD_SHADOW=true` enables a log-only AIMD proposal path using PSI, router runqueue-delay deltas, demand, and throttling telemetry. It does not call `set_unit_quota()` and does not change the production quota policy. Unlike the existing bounded-step controller, AIMD will not multiplicatively decrease quota from router runqueue delay alone: PSI must be available and either cross the high threshold or be above the low threshold with high router delay as corroboration. Missing PSI therefore fails closed for AIMD decrease. Keep `WORKLOAD_GUARD_DYNAMIC_QUOTA=false` on a new host while observing shadow proposals. Example:

```sh
mkdir -p "$HOME/.config/linux-workload-guard"
printf '%s\n' 'WORKLOAD_GUARD_AIMD_SHADOW=true' >> "$HOME/.config/linux-workload-guard/workload-guard.env"
systemctl --user restart workload-router.service
journalctl --user -u workload-router.service -f | grep 'quota shadow=aimd'
```

For a deterministic pressure-to-recovery quota replay using the production target function plus the configured observation interval and dwell, run `python3 benchmarks/quota-pressure-replay.py --output /tmp/quota-pressure-replay.tsv`. This is synthetic regression coverage, not a live host-pressure benchmark. For policy screening across step, AIMD, and Gradient2-inspired proposals, run `python3 benchmarks/controller-replay.py --output /tmp/controller-replay.tsv`. It compares the current bounded step controller, AIMD, and a conservative Gradient2-inspired latency proposal across synthetic stable, bursty, oscillating, high-demand, and missing-PSI traces. The replay measures quota movement only; it is **not** a real latency/throughput benchmark and cannot establish which controller is best on a host. `cpu.weight`-based weighted fairness and `sched_ext` remain research directions rather than enabled policies; they need separate multi-workload and kernel-compatibility evaluations before implementation.

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

The per-trial raw data is checked in at [`benchmarks/results/quota-benchmark-2026-10-09.tsv`](benchmarks/results/quota-benchmark-2026-10-09.tsv). Reproduce the same workload size and repetition count with:

```bash
RESULT=/tmp/quota-results.tsv REPS=5 WORK=250000000 ./benchmarks/quota-benchmark.sh
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

Per-trial summaries are available in [`benchmarks/results/routing-contention-2026-10-09.tsv`](benchmarks/results/routing-contention-2026-10-09.tsv); all 9,990 raw probe samples are in [`benchmarks/results/routing-contention-2026-10-09.samples.tsv`](benchmarks/results/routing-contention-2026-10-09.samples.tsv). Reproduce the test with:

```bash
REPS=5 DURATION=20 WORKERS=1 \
  RESULT=/tmp/routing-contention.tsv \
  ./benchmarks/routing-contention.py
```

**This is a controlled stress test and temporarily changes runtime slice quotas and stops/restarts the user router.** The script restores the quotas and router active state observed at startup. Run it when a short burst of CPU load is acceptable; by default, router-on mode uses the installed dynamic quota settings, which can confound routing-only comparisons.

For a routing-only comparison, use `benchmarks/routing-contention-isolated.sh`. It temporarily sets `WORKLOAD_GUARD_DYNAMIC_QUOTA=false` in the user environment file, restores the original file contents on exit, and restores whether the user service was active at startup. It preserves the existing setting afterward. `REQUIRE_ROUTED=true` marks the run as failed if any router trial does not route every worker; trial summaries exclude invalid router trials. Example:

```bash
DURATION=20 REPS=3 WORKERS=1 REQUIRE_ROUTED=true \
  RESULT=/tmp/routing-isolated.tsv \
  ./benchmarks/routing-contention-isolated.sh
```

The multi-worker case is a routing test only if the router actually moves all worker processes into `heavy-workload.slice`. Because the router evaluates CPU thresholds per process, multi-worker trials may not trigger routing; inspect `trial_valid` and `validity_reason` in the TSV before comparing results.

A follow-up isolated run with five paired trials per mode routed the single worker in all five router trials (8.4–9.5 s). Median per-trial wake-up p95 was 0.179 ms baseline versus 0.118 ms with routing; wake-up p99 was 2.198 ms versus 0.984 ms; probe-work p95 was 6.829 ms versus 3.342 ms. Median paired changes were -24.1%, -63.9%, and -51.1%, respectively. This is encouraging but still synthetic, single-host evidence; one baseline trial had a much higher work-duration tail, and it does not establish general UI-latency improvement. Raw results are in [`benchmarks/results/routing-contention-isolated-1worker-5rep-2026-10-09.tsv`](benchmarks/results/routing-contention-isolated-1worker-5rep-2026-10-09.tsv).

## Release

Releases use semantic versioning. See [CHANGELOG.md](CHANGELOG.md) for release history.

The project is intentionally named **Linux Workload Guard** and is workload-oriented rather than tied to a particular agent or application. The CI workflow checks Ubuntu 22.04 and 24.04, including unit/replay tests, systemd unit verification, routing, recovery, and shadow-mode integration tests where a user systemd manager is available.

## License

MIT
