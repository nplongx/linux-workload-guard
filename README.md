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

The router intentionally uses cgroup containment for automatic routing. It considers processes inside the configured parent cgroup and its nested systemd scopes, preventing unrelated system processes from being captured just because they happen to use CPU.

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
workload-guard profile
workload-guard version
```

`status` shows service state, CPU quotas, and currently routed workloads. Routed entries include PID, current CPU sample, routing reason (`known-heavy`, `sustained-cpu`, or `adaptive`), source cgroup, destination cgroup, and command. `diagnose` checks cgroup v2 and the configured workload units. `protect` runs a command directly inside `protected-workload.slice`.

`profile` samples the cgroup v2 tree, reports top cgroups by CPU usage and throttling, shows system CPU PSI, and runs a scheduler wakeup-latency probe. Use `workload-guard profile --duration 5 --samples 200` for a longer snapshot. Scheduler latency is measured as timer-wakeup excess plus this process's `/proc/<pid>/schedstat` runqueue delay; it is a practical host-health signal, not a real-time scheduling guarantee.

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
- `WORKLOAD_GUARD_DYNAMIC_QUOTA` — enable dynamic heavy-slice quota, default `false`.
- `WORKLOAD_GUARD_QUOTA_INTERVAL_SEC` — quota controller interval, default `5`.
- `WORKLOAD_GUARD_QUOTA_MIN` / `MAX` — quota bounds, default `100` / `400`.
- `WORKLOAD_GUARD_QUOTA_STEP` — maximum quota change per controller step, default `50`.
- `WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH` / `LOW` — CPU PSI pressure thresholds, default `0.40` / `0.10`.
- `WORKLOAD_GUARD_QUOTA_THROTTLE_HIGH` — heavy cgroup throttling ratio that permits quota growth, default `0.10`.
- `WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC` — minimum time between quota direction changes, default `10`.
- `WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS` / `LOW_MS` — scheduler runqueue-delay thresholds, default `20` / `5` ms.
- `WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC` — adaptive-statistics disk flush interval, default `10`.
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
```

Tests cover Python syntax, routing/exclusion logic, and quota decisions. The integration test starts a nested systemd scope, drives a CPU-bound child through the real router, and verifies both cgroup movement and recorded routing state.

## Release

Releases use semantic versioning. See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT
