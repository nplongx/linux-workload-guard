# Changelog

## [0.6.1] - 2026-09-27

Operational history telemetry.

### Added

- Persistent 5-minute runtime health snapshots.
- `workload-guard history` summaries for the last 1h, 6h, and 24h.
- Historical visibility into routing/recovery events, quota changes, heavy-slice CPU/throttling, CPU PSI, and scheduler delay.

## [0.6.0] - 2026-09-27

Router hardening and workload recovery.

### Added

- Lightweight persistent runtime history with 5-minute health snapshots and `workload-guard history` for 1h/6h/24h summaries.
- Sustained low-CPU recovery for long-lived routed workloads.
- Runtime route-state reconciliation after router restart, including stale PID/command identity checks.
- Fail-safe dynamic quota behavior when required cgroup or host-pressure telemetry is unavailable.
- `workload-guard quota` for current heavy-slice quota and controller telemetry.
- Recovery tuning via `WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD`, `WORKLOAD_GUARD_RECOVERY_SAMPLES`, and `WORKLOAD_GUARD_RECOVERY_DWELL_SEC`.

### Changed

- Persisted routes are loaded and reconciled on router startup instead of being treated as empty after restart.
- Route state is removed when a tracked PID exits, changes process identity, or leaves the heavy cgroup unexpectedly.

## [0.5.4] - 2026-09-27

Scheduler-aware dynamic quota control.

### Changed

- Dynamic quota pressure decisions now combine CPU PSI with the router process's accumulated runqueue delay.
- Raised default high/low CPU PSI thresholds to 40%/10% to avoid throttling heavy workloads solely because of moderate host contention.
- Added WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS and WORKLOAD_GUARD_QUOTA_SCHED_DELAY_LOW_MS for scheduler-pressure hysteresis.

## [0.5.3] - 2026-09-27

System profiling and faster quota response.

### Added

- `workload-guard profile` for cgroup CPU usage, throttling, CPU PSI, and scheduler-latency measurements.

### Changed

- Dynamic quota controller defaults to a 5s evaluation interval.
- Minimum quota dwell defaults to 10s while retaining strong pressure/demand hysteresis.

## [0.5.2] - 2026-09-27

Router overhead reduction.

### Changed

- Route state is written only when routing state changes instead of every sample.
- Adaptive statistics are flushed periodically instead of every sampling cycle.
- Added `WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC` for tuning the statistics flush interval.
- Fixed the test suite's version assertion to match the current release.
- Added `workload-guard profile` for system-wide cgroup CPU usage, CPU PSI, and scheduler-latency profiling.
- Reduced the default quota control interval to 5s and minimum quota dwell to 10s; pressure/demand thresholds remain hysteretic to avoid rapid reversals.
- Installer now deploys the `workload-profile` helper with the CLI.

## [0.5.1] - 2026-09-27

Performance and quota-controller fixes.

### Fixed

- Avoided scanning unrelated `/proc` processes during routing.
- Added heavy-cgroup `cpu.stat` throttling feedback to dynamic quota decisions.
- Added quota dwell time to reduce rapid quota direction changes.
- Added `workload-guard protect` for explicit protected cgroup placement.
- Integration test cleanup now terminates its CPU worker.
- Protected workloads now receive higher scheduler weight than heavy workloads.
- Dynamic quota demand now uses heavy-cgroup CPU usage plus throttling, including workloads launched directly in the heavy slice.

## [0.5.0] - 2026-09-27

Dynamic quota allocation.

### Added

- Optional CPU PSI-aware heavy-slice quota controller.
- Bounded quota floor, ceiling, step size, and control interval.
- Workload demand signal derived from routed workload CPU/adaptive score.
- Dynamic quota remains opt-in and does not change protected workload quota.


## [0.4.0] - 2026-09-27

Lightweight adaptive routing.

### Added

- Optional online CPU statistics per normalized command class.
- Adaptive routing score with a minimum history requirement.
- Atomic persistent adaptive statistics under the user state directory.
- `workload-guard stats` for learned workload baselines.
- Adaptive mode is opt-in; static known-heavy and sustained-CPU routing remains the safety fallback.

## [0.3.0] - 2026-09-27

Routing observability.

### Added

- `workload-guard status` now shows routed PIDs, CPU usage, routing reason, source cgroup, destination cgroup, and command.
- Router maintains an atomic runtime route-state file under the user runtime directory.
- Routing state distinguishes `known-heavy` command matches from `sustained-cpu` threshold routing.

## [0.2.0] - 2026-09-27

Real cgroup-tree routing.

### Changed

- Automatic routing now discovers processes anywhere inside the configured parent cgroup tree, including nested systemd scopes.
- Removed the old process-ancestry root-PID limitation.
- Added a real integration test that verifies a CPU-bound nested workload is moved into the heavy workload slice.

## [0.1.1] - 2026-09-27

Configuration and installation hardening.

### Changed

- `workload-router.service` automatically loads the host config file when present.
- Installer creates the host config from the example only when no config exists.
- Existing host configuration is never overwritten by upgrades.
- Tests cover the config wiring.

## [0.1.0] - 2026-09-27

First public release.

### Added

- Generic systemd/cgroup v2 workload CPU guard.
- Protected and heavy workload slices.
- Sustained CPU based automatic routing.
- Command-pattern based routing for common build/test/inference workloads.
- Configurable parent/target units and exclusion patterns.
- Optional browser automation CPU budget.
- `run-workload` helper for explicit heavy workloads.
- `workload-guard` status and diagnostics CLI.
- Shell/Python tests and GitHub Actions CI.
- Contributor and security documentation.
