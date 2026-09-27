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

The router intentionally uses process ancestry for automatic routing. This prevents unrelated system processes from being captured just because they happen to use CPU.

## Default budgets

| Workload class | CPU quota |
|---|---:|
| Protected application/agent | 150% |
| Heavy workload slice | 400% |
| Browser automation | 200% |

`100%` is approximately one logical CPU. Quotas are cgroup limits, not CPU priority scores.

## CLI

After installation:

```bash
workload-guard status
workload-guard diagnose
workload-guard version
```

`status` shows service state and CPU quotas. `diagnose` checks cgroup v2 and the configured workload units.

## Configuration file

For host-specific policy, copy `config/workload-guard.env.example` to `~/.config/linux-workload-guard/workload-guard.env` and use the variables as the environment source for the user service. The example is intentionally not installed automatically. The installed `workload-guard` CLI keeps its version under `~/.local/share/linux-workload-guard/`.

## Install

```bash
./install.sh
```

The installer installs user-level systemd units and helper commands. The parent workload is configurable with `WORKLOAD_GUARD_PARENT_UNIT`; the default is the generic `protected-workload.slice`. The generic core is independent of a particular application.

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

The router samples descendants of the configured parent workload every 2 seconds.

Known heavy commands are routed immediately. Unknown commands are routed after four consecutive samples at or above 70% CPU, giving an 8-second sustained threshold.

This avoids reacting to short-lived CPU spikes.

## Configuration

Environment variables:

- `WORKLOAD_GUARD_SAMPLE_SEC` — sample interval, default `2`
- `WORKLOAD_GUARD_SUSTAINED_SAMPLES` — consecutive hot samples, default `4`
- `WORKLOAD_GUARD_CPU_THRESHOLD` — percent of one logical CPU, default `70`
- `WORKLOAD_GUARD_MAX_ANCESTRY` — parent traversal limit, default `32`
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

Tests cover Python syntax and routing/exclusion logic. A real end-to-end routing test requires a child process attached to a configured workload parent and is not faked by the test suite.

## Release

Releases use semantic versioning. See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT
