# Linux Workload Guard

Generic Linux/systemd resource guard for CPU-heavy workloads on developer and automation machines.

**Project planning:** [v1.0.0 release roadmap](ROADMAP.md) · [Changelog](CHANGELOG.md)

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

The router intentionally uses cgroup containment for automatic routing. It considers processes inside the configured parent cgroup and its nested systemd scopes, preventing unrelated system processes from being captured just because they happen to use CPU. The service starts the parent, stable recovery leaf, and heavy-workload slices before routing begins; if systemd cannot provide the required cgroup paths, the router pauses routing instead of silently proceeding without a safe recovery destination.

## Default budgets

| Workload class | CPU quota |
|---|---:|
| Protected application/agent | 150% |
| Heavy workload slice | 400% |
| Recovery destination | Inherits the protected slice budget |
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

### Controller safety and degraded telemetry

The quota controller now treats telemetry as a time-bounded observation window rather than assuming every readable counter is valid:

- CPU PSI parsing rejects incomplete rows, duplicate fields/`some` rows, malformed or non-finite averages, out-of-range averages, and invalid cumulative `total` values. The production high-pressure boundary remains `0.40`; deterministic tests exercise the full quota-decision path at exactly `0.40`, just below it, and with pressure/scheduler telemetry unavailable. This does **not** substitute for a live test that crosses `0.40` on a host.
- `cpu.stat` snapshots require `usage_usec`, `nr_periods`, and `nr_throttled`, with no duplicate keys, malformed rows, negative values, counter regressions, or impossible throttled-period deltas. Missing baselines, stale sample windows, or reset counters skip quota decisions and require a new valid interval before actuation resumes.
- Unknown PSI is not treated as low pressure. A missing or malformed signal cannot justify a quota increase; the independent production scheduler-delay high-pressure path remains available. AIMD proposals require valid PSI and remain log-only.
- CPU utilization uses elapsed sample time and process start time from `/proc/<pid>/stat`. Recovery pauses on unreadable, stale, or regressing process counters rather than interpreting them as idle. Route state persists process start time to detect PID reuse even when command lines match. An `exec(2)` command-line change resets routing/shadow classification samples but does not orphan an already-routed process whose kernel start time is unchanged; PID reuse still invalidates the route.
- Core sampling intervals have explicit lower bounds to prevent tiny positive values from creating a busy loop. A controller observation with an externally observed quota outside the configured min/max is rejected instead of applying a step from an inconsistent starting point; correct the quota/configuration and allow a fresh telemetry baseline before relying on dynamic control.
- When the heavy cgroup disappears or its `ControlGroup` changes, quota counters are re-baselined. Failed `systemctl` quota writes do not update the controller's confirmed quota and use bounded retry backoff. A successful command is not sufficient by itself: the controller reads back `CPUQuotaPerSecUSec` and confirms the requested value only when the effective integer percentage matches. Missing/mismatched readback is treated as failed actuation; the next observation reconciles any change that did apply. Missing workload cgroups are retried periodically.
- Recovery returns a process to its original source scope if that cgroup still exists. If systemd has removed the transient scope, recovery uses the stable `protected-workload-recovery.slice` leaf under the protected CPU budget; it does not fall back to the protected slice root, which can reject process migration with `EBUSY` under cgroup v2 domain-controller rules.
- Legacy route-state records without a process start time are deliberately not trusted for PID-based recovery after upgrade. If an old record referred to a process that remains in the heavy slice, the router drops that record rather than risk moving a reused PID; inspect the process and cgroup before taking any manual recovery action.

Dynamic quota remains opt-in (`WORKLOAD_GUARD_DYNAMIC_QUOTA=false` by default). AIMD remains a log-only proposal path (`WORKLOAD_GUARD_AIMD_SHADOW=false` by default). Do not promote experimental replay-only policies to production enforcement based on synthetic traces.

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
- `WORKLOAD_GUARD_QUOTA_THROTTLE_HIGH` — heavy cgroup throttling ratio that may corroborate quota growth when demand is at least `0.30` and PSI plus scheduler delay both confirm low pressure, default `0.10`.
- `WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC` — minimum time between quota direction changes, default `10`.
- `WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS` / `LOW_MS` — scheduler runqueue-delay thresholds, default `20` / `5` ms.
- `WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC` — adaptive-statistics disk flush interval, default `10`.
- `WORKLOAD_GUARD_HISTORY_INTERVAL_SEC` — runtime health snapshot interval, default `300` seconds.
- `WORKLOAD_GUARD_EXCLUDE_PATTERNS` — comma-separated command-line patterns excluded from routing.
- `WORKLOAD_GUARD_RECOVERY_UNIT` — stable leaf slice used when a routed process's original source cgroup has disappeared. Default is a `-recovery.slice` leaf beneath the configured parent (normally `protected-workload-recovery.slice`); custom parent slices must provide this unit or set the variable to an existing safe leaf.
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
- `systemd/protected-workload-recovery.slice` — stable leaf destination for recovery if a transient source scope disappears; inherits the protected slice budget.
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
./tests/shadow-routing.sh
./tests/controller-integration.sh
./tests/soak-monitor.sh
./tests/quota-benchmark-safety.sh
```

Tests cover Python syntax, routing/exclusion logic, quota decisions and bounded actuation, telemetry reset/staleness, PID reuse and exec identity, service-command timeouts, soak-monitor completeness/failure paths, benchmark no-overwrite safety, real cgroup routing, recovery after sustained low CPU, out-of-band quota reconciliation, and route-state reconciliation across router restart. Routing/recovery/shadow/controller integration tests use unique temporary slice hierarchies; they do not stop/restart the installed service or write its production slice quotas. A static guard in `test.sh` prevents those operations from being reintroduced. The integration tests require a systemd user manager and cgroup v2; otherwise they print `SKIP` and exit successfully. The combined dynamic-quota integration additionally skips if the delegated test slice lacks `usage_usec`, `nr_periods`, or `nr_throttled`, or systemd rejects isolated `CPUQuota` changes. In hosted CI run [38690611049](https://github.com/nplongx/linux-workload-guard/actions/runs/38690611049), both Ubuntu 22.04 and 24.04 jobs passed; the deterministic PSI boundary-to-actuation check passed on both. Ubuntu 22.04 then explicitly skipped the longer dynamic-quota integration for missing CPU counters, while Ubuntu 24.04 executed it successfully. **Dynamic quota is not validated on that Ubuntu 22.04 runner.** Treat dynamic quota as unsupported on any environment whose delegated cgroup lacks the required counters until a functional integration run passes; this is a capability-based limitation, not a blanket claim about every Ubuntu 22.04 installation.

Core routing environment values are validated before the routing loop starts. Malformed/non-finite values, non-positive sample counts, unsafe sample intervals, and out-of-range thresholds disable routing with an explicit error instead of crashing during module import or entering a zero-delay loop. Correct the setting and restart the user service to recover; dynamic quota remains separately opt-in, and invalid core configuration does not trigger an automatic quota write.

Run the deterministic controller regression directly with `python3 tests/controller-hardening.py`. On a host with a working systemd user manager and cgroup v2, run `./tests/controller-integration.sh` to exercise the combined path using temporary, uniquely named slices: deterministic PSI values below/at/above production `0.40` through the actual decision function and quota actuator/readback, missing/stale-signal holds, burst rejection, two competing CPU-bound workers, routing, dynamic quota increase/decrease, AIMD log-only behavior, out-of-band quota reconciliation, active-route router restart, cgroup recreation, idle recovery, and restart with dynamic quota disabled while preserving the recorded temporary quota. The PSI observations are synthetic; the test does not force host PSI to cross the threshold. It does not change the installed service's environment or the normal `protected-workload.slice` / `heavy-workload.slice`. The test uses shortened sampling and test-only thresholds for its longer integration phase; it is functional regression coverage, not a production-latency or long-soak benchmark.

### Preparing a release soak

`benchmarks/soak-monitor.sh OUTPUT_DIR [DURATION_SECONDS=86400] [INTERVAL_SECONDS=60]` is a monitor-only collector for a separately approved soak window. It refuses an existing output directory, uses private output permissions, does not start workloads/restart the router/change configuration/write quota properties, validates metric availability, protects scheduler-delay deltas against PID reuse, and records verified routed processes only when PID starttime and cgroup membership match route state. Duration, cadence, and sample collection time use a monotonic clock; wall-clock timestamps are checked against monotonic cadence and a clock discontinuity invalidates the run. The summary includes expected/actual sample counts, estimated missed intervals, sampling failures, observed quota transitions/direction reversals, exit code, and `run_status=COMPLETE` only when duration and coverage checks pass. Service restarts, PID changes, unavailable/reset counters, missed sample deadlines, slow final collection, clock loss/discontinuity, and quota property transitions are recorded in `errors.tsv`; any router PID/restart-counter discontinuity or missing/failed final journal capture forces `INCOMPLETE`. A missing final summary means the monitor was killed without graceful finalization. Any `INCOMPLETE` result must not be accepted as a continuous soak.

First confirm that the user service is healthy and that the planned workload is approved. For a persistent monitor that survives a terminal disconnect, start a transient **user** service (run as the same account that owns `workload-router.service`):

```sh
SOAK_UNIT="lwg-soak-$(date +%Y%m%d-%H%M%S)"
SOAK_DIR="$HOME/$SOAK_UNIT"
systemd-run --user --unit="$SOAK_UNIT" --property=Type=exec --property=Restart=no \
  "$PWD/benchmarks/soak-monitor.sh" "$SOAK_DIR" 86400 60
systemctl --user status "$SOAK_UNIT.service"
# Follow monitor stdout/stderr while it runs:
journalctl --user -u "$SOAK_UNIT.service" -f
```

Use `259200` seconds for the 72-hour target. To stop early, run `systemctl --user stop "$SOAK_UNIT.service"`; this sends a graceful termination and the artifacts are deliberately marked `INCOMPLETE`, not a passed soak. After the intended duration, review `environment.txt` (`run_status`, timestamps, sample coverage, missing metrics), `errors.tsv`, every column in `samples.tsv`, and `service-journal.log`; correlate these with workload phase logs and controller quota/recovery transition messages. `./tests/soak-monitor.sh` exercises output-directory protection, output permissions, schema, missing-service and interruption paths. The monitor alone is not a soak acceptance pass: the workload plan and route validity, quota-transition/oscillation, failure/recovery, and resource-overhead analysis must also be attached.

### Passive live PSI evidence

Collect timestamped observations without generating load or changing the installed service, configuration, or quota:

```sh
python3 benchmarks/live-psi-observe.py "$HOME/lwg-psi-$(date +%Y%m%d-%H%M%S)" 3600 5
```

The output directory is private and must not already exist. `samples.tsv` records wall and monotonic timestamps, host CPU PSI `some`/`full` averages, user-service identity/restart count, and the effective user-level heavy-slice quota. `controller-journal.log` captures the installed router journal over the same window; `manifest.txt` records start/end properties and preliminary crossing/decision counts. A collection reports `acceptance_status=NOT_RUN` unless sampled `some avg10` reaches the production 40% boundary and quota-change log evidence exists; even `REVIEW_REQUIRED` is **not an automatic PASS**. Review timestamp correlation, the controller pressure value and decision, actual effective quota before/after, service continuity, and whether the event was safe and representative. No crossing is a valid `NOT RUN`, not a reason to manufacture load or change the production threshold.

### Repeated performance acceptance

The isolated routing benchmark now nests its temporary heavy slice beneath the temporary 100% parent slice, so baseline and routed modes share the same aggregate CPU ceiling instead of granting routed mode an extra 100% sibling budget. It refuses to overwrite summaries, raw samples, manifest, or router log and verifies all intended workers route before accepting a routed trial. Example diagnostic run:

```sh
RESULT="$HOME/lwg-routing-$(date +%Y%m%d-%H%M%S).tsv"
REPS=5 DURATION=30 WORKERS=1 REQUIRE_ROUTED=true RESULT="$RESULT" \
  ./benchmarks/routing-contention-isolated.sh
python3 benchmarks/performance-review.py "$RESULT"
```

Predeclared candidate acceptance limits for the RC performance review are: at least five valid paired trials; at least 500 steady-state probe samples per mode/trial; all intended workers routed in every enabled trial; median paired ratios for wake-up p95, wake-up p99, and probe-work p95 no worse than `1.10`; representative workload throughput at least `0.90x` baseline; router CPU no more than `5%` of one logical CPU over the aligned window; and router peak RSS no more than `32 MiB`. Report every pair and dispersion, not just aggregate medians. These are proposed project acceptance limits for review, not measured results or an automatic release decision. The harness now measures synthetic Python-loop iterations/second and isolated-router CPU/RSS overhead, but those proxies do not replace representative application workload evidence. A multi-worker trial that does not route every worker is invalid; do not lower the production route threshold just to make a benchmark pass.

For controlled operator rollback, Ubuntu/cgroup prerequisite evidence, second-host manifests, and the exact PASS/FAIL/SKIP/NOT RUN vocabulary, see [the operator rollback and cross-host acceptance checklist](docs/operator-rollback.md).

## Benchmark

### Historical CPU quota scaling (2026-10-09)

This is a **historical fixed-work quota sweep**, not a benchmark of the dynamic controller's decision quality. It measures how long one CPU-bound workload took under different static quota ceilings. The raw 2026-10-09 results below are retained, but the original harness changed the installed `heavy-workload.slice` quota and stopped `workload-router.service`; it is not the current safe execution procedure.

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

The per-trial raw data is preserved at [`benchmarks/results/quota-benchmark-2026-10-09.tsv`](benchmarks/results/quota-benchmark-2026-10-09.tsv). The current `benchmarks/quota-benchmark.sh` uses a unique temporary user slice, changes only that temporary slice's `CPUQuota`, validates cgroup counter continuity, measures elapsed time with a monotonic clock, records a manifest, and refuses to overwrite either the result or its manifest. It never stops/restarts the installed service or writes `heavy-workload.slice`. The current default is deliberately smaller than the historical eight-worker sweep and is **not** an exact reproduction of those results. For a bounded isolated run:

```bash
WORKERS=2 WORK=100000000 REPS=3 QUOTAS=100,200,400 \
  RESULT="/tmp/quota-results-$(date +%Y%m%d-%H%M%S).tsv" \
  ./benchmarks/quota-benchmark.sh
```

The `.environment.txt` sidecar records host/kernel, CPU count, cgroup mode, systemd/Python versions, quota levels, worker count, workload size, and the temporary slice. The run still consumes CPU and should only be performed in a suitable window. A short harness smoke is a safety/schema check, not performance acceptance evidence. Historical quota-sweep results are not overwritten or silently regenerated by the current script.

### Router contention test (2026-10-09)

This second test evaluates the **end-to-end routing behavior** with a small synthetic interactive task running alongside a CPU-bound process. It is separate from the static quota sweep above.

**Method and conditions**

- Historical run: five 20-second trials per mode (router off / router on), with the order alternated between rounds: **10 trials and 9,990 probe samples total**.
- One CPU-bound Python worker and one periodic probe ran as separate systemd scopes under `protected-workload.slice`. The historical script set that slice to a 100% CPU quota and `heavy-workload.slice` to 400%.
- The probe schedules a small 5,000-iteration task every 20 ms and records both wakeup lateness (actual start minus scheduled time) and task execution duration. These are synthetic scheduler/CPU-contention indicators, not application-level UI latency.
- In the historical router-on trials, the installed router configuration was used. The worker was moved to `heavy-workload.slice` in all five trials, after 8.4–9.5 seconds; baseline trials stopped the router and left the worker in the protected slice.
- For steady-state comparisons, baseline samples are taken after the first 12 seconds; router-on samples are taken from two seconds after the actual move. Summary numbers below are the **median of the five per-trial percentiles**, which is less sensitive to a single noisy run than averaging all samples.

| Historical steady-state observation (not causal evidence) | Router off | Router on | Observed difference only |
|---|---:|---:|---:|
| Probe wakeup lateness, p95 | 0.176 ms | 0.156 ms | 11.7% lower |
| Probe wakeup lateness, p99 | 2.032 ms | 1.810 ms | 10.9% lower |
| Probe task execution time, p95 | 8.418 ms | 3.398 ms | 59.6% lower |

The historical measurements remain useful as raw observations, but **they are not a valid controlled router-on/router-off comparison**: the two modes had different effective quota budgets (100% protected versus 400% heavy), and the router-on run also used the installed controller configuration. Do not infer that routing alone caused the observed difference. The raw results are preserved and superseded for methodology by the isolated equal-per-slice-quota harness below.

Per-trial summaries are available in [`benchmarks/results/routing-contention-2026-10-09.tsv`](benchmarks/results/routing-contention-2026-10-09.tsv); all 9,990 raw probe samples are in [`benchmarks/results/routing-contention-2026-10-09.samples.tsv`](benchmarks/results/routing-contention-2026-10-09.samples.tsv). To run the **current safe methodology** (not reproduce those historical results), use:

```bash
REPS=5 DURATION=20 WORKERS=1 \
  RESULT=/tmp/routing-contention.tsv \
  ./benchmarks/routing-contention.py
```

**The historical implementation used for those measurements is not safe for the current production-hardening workflow**: it modified the installed router state and production slice quotas. The current `benchmarks/routing-contention.py` and `benchmarks/routing-contention-isolated.sh` use unique temporary user slices and a directly spawned test router. They do not edit `workload-guard.env`, stop/restart the installed service, or write production slice quotas. The current pass-6 methodology nests the temporary heavy slice beneath the temporary parent; both modes share the same 100% aggregate CPU ceiling. Dynamic quota and AIMD are disabled for this routing-only comparison.

The harness counterbalances mode order, refuses to overwrite any result/sidecar, writes an environment manifest and router log, uses nearest-rank percentiles, records per-trial host PSI/load, throughput, router CPU/RSS, and defaults to `REQUIRE_ROUTED=true`; every router trial must actually move all intended workers. Both modes now use the same elapsed steady window (samples at or after 12 seconds), and a route must converge at least two seconds before that window. Use 30 seconds per mode to provide at least 500 steady-state samples:

```bash
DURATION=30 REPS=5 WORKERS=1 REQUIRE_ROUTED=true \
  RESULT=benchmarks/results/routing-contention-isolated-new.tsv \
  ./benchmarks/routing-contention-isolated.sh
python3 benchmarks/performance-review.py benchmarks/results/routing-contention-isolated-new.tsv
```

For a real CPU-bound command-line workload in addition to the synthetic integer loop, run the same isolated paired harness with OpenSSL SHA-256 throughput. It requires the `openssl` CLI, keeps the work inside the same temporary 100% aggregate CPU ceiling, records SHA-256 throughput in kB/s, and still requires every worker to be observed in the heavy cgroup:

```bash
WORKLOAD_KIND=terminal-openssl DURATION=30 REPS=5 WORKERS=1 REQUIRE_ROUTED=true \
  RESULT=benchmarks/results/routing-contention-terminal-openssl-new.tsv \
  ./benchmarks/routing-contention-isolated.sh
python3 benchmarks/performance-review.py benchmarks/results/routing-contention-terminal-openssl-new.tsv
```

The acceptance evaluator cross-checks the raw sample file against summary counts and steady-window membership, rejects missing/non-finite/out-of-order samples and incomplete manifests, validates the predeclared thresholds, and refuses to overwrite an acceptance report. A synthetic PASS applies only to the synthetic probe; a terminal-openssl PASS applies only to this SHA-256 command-line workload, not to compiler builds or all terminal applications. Do not pool throughput values across workload kinds.

The 2026-10-15 pass-3 three-pair result remains a historical diagnostic; its old methodology had separate protected/heavy sibling ceilings and is superseded by the equal-aggregate-capacity pass-6 run. The pass-6 five-pair results, synthetic throughput/overhead, and acceptance verdict are documented in [`benchmarks/results/release-readiness-2026-10-15-pass6.md`](benchmarks/results/release-readiness-2026-10-15-pass6.md) with raw TSV, samples, manifest, router log, and acceptance review.

Older 2026-10-09 result files, including `routing-contention-isolated-1worker-5rep-2026-10-09.tsv`, remain untouched. Their `router_was_active` field captured whether the service was active before each mode switch, not whether it was active during the trial, and their protected/heavy quotas differed. Treat them as historical diagnostics only. Multi-worker trials remain valid only if every intended worker is verified in the target cgroup; inspect `trial_valid` and `validity_reason` before comparing. Unknown commands route only after each process exceeds the 70%-of-one-logical-CPU trigger for four consecutive samples by default. Under a shared 100% parent quota, two unknown CPU-bound workers may each remain near 50% and therefore not route; this is a known per-process-policy limitation, not a reason to lower the production trigger without false-positive/fairness evidence. Known-heavy command matching remains a separate immediate-route path.

The 2026-10-15 release-readiness reports record bounded integration metrics, benchmark-method corrections, verification results, controller/monitor hardening, and remaining RC2/RC3/stable blockers: [`release-readiness-2026-10-15.md`](benchmarks/results/release-readiness-2026-10-15.md), [`release-readiness-2026-10-15-pass2.md`](benchmarks/results/release-readiness-2026-10-15-pass2.md), [`release-readiness-2026-10-15-pass3.md`](benchmarks/results/release-readiness-2026-10-15-pass3.md), [`release-readiness-2026-10-15-pass4.md`](benchmarks/results/release-readiness-2026-10-15-pass4.md), [`release-readiness-2026-10-15-pass5.md`](benchmarks/results/release-readiness-2026-10-15-pass5.md), and [`release-readiness-2026-10-15-pass6.md`](benchmarks/results/release-readiness-2026-10-15-pass6.md).

The historical five-pair measurements cited in earlier revisions (wake-up p95/p99 `0.179/2.198` ms baseline versus `0.118/0.984` ms routed, probe-work p95 `6.829/3.342` ms) are retained in [`benchmarks/results/routing-contention-isolated-1worker-5rep-2026-10-09.tsv`](benchmarks/results/routing-contention-isolated-1worker-5rep-2026-10-09.tsv), but the quota mismatch described above invalidates a causal interpretation. They must not be presented as a controlled estimate of routing-only improvement.

**Hardening integration evidence (2026-10-14):** on Ubuntu 26.04.1 LTS, kernel `7.0.0-38-generic` (`x86_64`, Python 3.14.4, systemd 259.5, cgroup v2, 8 logical CPUs), three repeated final-code runs exercised burst rejection, routing two competing workers, dynamic quota increase/decrease, AIMD log-only proposals, recovery to a dedicated fallback leaf, and routing/recovery after heavy-cgroup removal/recreation. Across those three runs, median worker routing was 1.066 s, first quota increase 1.353 s, and idle-to-decrease/recovery 2.377 s. Probe p95 wake-up lateness medians were 0.997 ms baseline / 0.420 ms enabled; p99 medians were 18.181 / 1.453 ms. The probe is synthetic and runs in this test's CPU-limited hierarchy. The Python-loop throughput median was 44,039 iterations/s baseline and 54,466 enabled, but load/PSI varied between short runs; do not infer generalized performance improvement. Router CPU measured about 1.16% of one logical CPU and peak RSS median was about 17.8 MiB (18,224 KiB) over a 5.1-second interval. These are one-host smoke evidence only. Full per-run output and raw samples are in [`benchmarks/results/controller-hardening-smoke-2026-10-14.md`](benchmarks/results/controller-hardening-smoke-2026-10-14.md).

**Evidence limits to keep in view:** all performance probes here are synthetic and single-host. The pass-6 two-worker attempt did not route either worker and is preserved as incomplete; it is not a valid comparison. The five-pair pass-6 one-worker benchmark meets sample, routing, throughput-median, and overhead bounds but fails the predefined p99 and probe-work p95 latency limits; the overall synthetic acceptance verdict is FAIL. Representative terminal-workload evidence remains NOT RUN. The default production PSI high branch at `0.40` has deterministic boundary coverage but has not been observed crossing the threshold in a live controller decision. No second-host validation or 24-hour/72-hour soak has been completed.

## Release

Releases use semantic versioning. See [CHANGELOG.md](CHANGELOG.md) for release history.

The project is intentionally named **Linux Workload Guard** and is workload-oriented rather than tied to a particular agent or application. The CI workflow checks Ubuntu 22.04 and 24.04, including unit/replay tests, systemd unit verification, routing, recovery, and shadow-mode integration tests where a user systemd manager is available.

## License

MIT
