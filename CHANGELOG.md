# Changelog

## [Unreleased]

### Fixed

- Core routing environment values are now parsed into explicit invalid sentinels and validated before the routing loop. Malformed/non-finite values, invalid sample intervals/counts, and unsafe thresholds no longer crash module import or enter an unsafe loop; invalid core routing configuration logs an error and leaves routing disabled until corrected.
- Positive-but-tiny sample, statistics, history, and quota intervals are now rejected below explicit lower bounds, preventing valid-looking numeric settings from creating busy loops. The quota decision path also refuses actuation when the observed quota is outside its configured bounds.
- PSI parsing now rejects incomplete `some` rows, duplicate `some` rows/fields, malformed averages, and invalid cumulative counters rather than accepting a partial line as a complete observation.
- Routing hot/shadow samples reset when a process command changes. Persisted recovery identity is based on kernel PID starttime, not command text, so a legitimate `exec(2)` does not orphan an already-routed process; PID reuse remains rejected.
- Dynamic quota telemetry now rejects malformed, duplicate, negative, missing, reset, or impossible cgroup counters, and skips stale observation windows instead of silently converting counter regressions to zero.
- Missing, malformed, non-finite, or out-of-range CPU PSI is reported as unavailable; unknown pressure no longer counts as low pressure for quota increases.
- Process sampling now rejects malformed `/proc/<pid>/stat` data and measures CPU usage over real sample intervals. Recovery does not treat failed process reads, counter regressions, or stale intervals as idle CPU.
- Route state now persists process start time to detect PID reuse, including same-command reuse. Legacy route-state records without start time are no longer trusted for recovery actions.
- Recovery falls back to a stable leaf slice under `protected-workload.slice` when a transient source scope disappears. It avoids moving processes into a slice root that can reject the move with `EBUSY` when child controllers are enabled.
- Quota telemetry baselines reset when the heavy cgroup path disappears or changes. Missing cgroups trigger bounded unit-start retries, and failed quota writes retain the last confirmed quota with capped exponential retry backoff.
- Dynamic quota demand now uses current routed-process samples instead of only the CPU sample that originally triggered routing. AIMD remains an independent log-only proposal path.
- Routed CPU demand is averaged over the full interval between quota observations. A pre-fix integration trace exposed an upward quota rebound after an idle transition; the integration test now verifies no upward quota transition after route recovery and a complete dwell window.
- Recent throttling can no longer trigger quota growth after demand falls below the corroboration threshold; scheduler delay is sampled through dwell windows to avoid treating accumulated delay as one sudden interval spike.
- The earlier static quota benchmark restoration fix still modified the production heavy-slice quota and stopped the installed service. The harness is now isolated to a temporary user slice and refuses to overwrite historical results.
- The static quota benchmark validates required cgroup counters and monotonic deltas, measures trial duration with `time.monotonic_ns()`, records a host/workload manifest, and refuses existing result/manifest paths. The 2026-10-09 raw results remain historical evidence and are not overwritten.
- The quota observation loop now reconciles the live systemd quota property at each eligible decision window. Missing properties invalidate the cache; recovered/out-of-band observations reset the counter baseline and dwell before any new actuation. The quota actuator and both proposal helpers also refuse non-integral or out-of-bounds current/target values.
- Quota actuation now reads back `CPUQuotaPerSecUSec` after `systemctl set-property` and reports success only when the effective value matches the requested integer percentage. Missing/mismatched readback retains the last confirmed controller value and enters bounded retry/backoff; deterministic tests cover exact, mismatched, and unavailable readback.

### Added

- Deterministic controller hardening tests for PSI threshold boundaries, counter resets/anomalies, stale windows, PID identity, legacy state, service timeouts, and the quota/AIMD/recovery decision sequence.
- Mocked quota-benchmark safety tests cover argument validation, immutable existing output, isolated temporary-slice targeting, cgroup counter/schema collection, and the absence of calls targeting the installed service or production heavy slice.
- Routing, recovery, and shadow integration tests now use uniquely named temporary parent/heavy/recovery slices and no longer stop/restart `workload-router.service` or write production slice quotas. A static regression guard rejects reintroducing those production-unit operations.
- The combined isolated controller integration now verifies rollback semantics: restarting with dynamic quota disabled leaves a known operator-selected temporary-slice quota unchanged and emits no further quota-actuation transition.
- The isolated controller integration now feeds valid synthetic PSI observations just below, at, and above the production `0.40` threshold into the actual quota decision function and verifies bounded quota changes/readback on a temporary systemd slice. Missing PSI and stale windows must hold; a corroborated low-pressure demand case is also checked. This is deterministic threshold-path coverage, not live host-PSI acceptance evidence.
- Isolated systemd integration coverage for transient-burst rejection, routing two competing workers, dynamic quota increase/decrease, AIMD log-only behavior, and recovery. The test uses temporary slice units and does not change the installed service configuration.
- `protected-workload-recovery.slice` provides a stable leaf fallback inherited from the protected CPU budget; the installer, uninstaller, and service dependencies now include it.
- The bounded integration smoke test reports synthetic probe p50/p95/p99, CPU-bound loop throughput, host PSI observations, router process CPU/RSS and scheduler-delay delta, quota transitions, and detection/recovery timings. Single-run smoke metrics are not performance acceptance evidence.
- Added a monitor-only soak collector for service state/restarts, PSI, quota property, router CPU/RSS, route-state count, and service journal. It does not create load or alter service/configuration; it is tooling, not soak evidence.
- Hardened soak monitoring with private output permissions, verified route-state/PID/cgroup membership, PID-starttime-safe scheduler deltas, sampling-gap and missing-metric accounting, quota transition/direction-reversal counts, service restart/PID-change logging, expected-versus-observed sample coverage, and explicit COMPLETE/INCOMPLETE final status. Added safe tests for invalid arguments, existing directories, quota reversals, missing service telemetry, and interrupted runs.
- Soak acceptance now fails closed if the router MainPID changes, the restart-counter baseline is unavailable, the counter increases or resets (including changes detected only at finalization), a counter becomes unavailable, a sampling deadline is missed, or final service-journal capture fails. The monitor regression suite exercises restart, PID change, counter baseline/reset, sampling overrun, and journal-loss cases.
- Soak duration/deadline/collection timing now uses a monotonic clock. Wall-clock timestamp jumps, unavailable/regressed monotonic timing, and a slow final sample mark the run incomplete; previously the final collection had no following scheduling-loop iteration to account for its overrun, and wall-clock jumps could distort duration/continuity checks.
- The routing contention benchmark no longer stops/restarts the installed service, edits its environment file, or writes production slice quotas. It uses unique temporary user slices with equal 100% per-slice ceilings, a dedicated direct router process with dynamic quota disabled, verified worker placement, immutable result paths, and an environment manifest. Historical 2026-10-09 paired results are retained but are not causal router-only comparisons because the prior method used unequal protected/heavy quota ceilings.
- The controller integration now restarts its isolated router while two workers remain actively routed, verifies persisted route identities, and confirms both workers can recover afterward before testing cgroup recreation.
- Controller integration peak RSS reporting now takes the maximum of start/end `VmRSS` and `VmHWM` samples in KiB, rather than labeling only the final high-water sample as the run peak.

## [0.6.5-rc.1] - 2026-10-09

Release candidate; not yet recommended for broad production deployment.

### Added

- Routing-policy baseline checks for transient-burst false positives, sustained-load detection latency, and idle-to-recovery latency, with repeated per-trial results.
- Opt-in EWMA + hysteresis shadow policy that logs hypothetical route/recovery transitions without moving cgroups or changing quota.
- Opt-in log-only AIMD quota proposals using the existing PSI, scheduler-delay, demand, and throttling telemetry; production quota enforcement remains unchanged unless dynamic quota is explicitly enabled.
- Deterministic controller replay comparing bounded-step, AIMD, and a Gradient2-inspired proposal on synthetic telemetry, explicitly labeled as policy screening rather than host-performance evidence.
- Deterministic replay traces comparing the existing CPU-threshold policy with shadow EWMA/hysteresis, including noisy-burst cases and a five-sample tuning candidate.
- Live shadow integration test covering transient-burst rejection, sustained-load routing, and recovery, with three repeated trials recorded.
- CI coverage across Ubuntu 22.04 and 24.04, including routing recovery and shadow integration checks.

### Fixed

- Dynamic quota no longer increases solely because of throttling while scheduler pressure remains in the hysteresis band.
- Uninstall removes the installed `workload-profile` helper while preserving user configuration.

### Changed

- AIMD shadow decrease now requires available PSI; router-process runqueue delay alone cannot trigger a multiplicative decrease, and only corroborates elevated PSI. The existing bounded-step controller is unchanged.
- Route-decision logs now include the trigger reason and relevant threshold/sample context; failed cgroup moves emit warnings with target and reason.
- Integration tests detect user-systemd availability through the manager API, allowing them to run even when the manager reports a degraded state for unrelated units.
- Invalid shadow-policy parameters now disable shadow mode with an explicit error instead of running with unsafe thresholds.
- Malformed shadow-only numeric settings are now converted to invalid sentinels and rejected by validation, rather than crashing the router during module initialization or silently clamping sample counts.

## [0.6.4] - 2026-10-09

Make workload routing startup resilient.

### Fixed

- The router service explicitly wants the protected and heavy workload slices, so systemd starts them before the router.
- The router also attempts to start both configured slices when launched directly and logs a clear warning when their cgroup paths are unavailable, instead of silently skipping all routing work.

### Added

- Reproducible CPU-quota and router-contention benchmarks, with per-trial summaries and raw probe samples documented in the README.

## [0.6.3] - 2026-10-09

More accurate runtime diagnosis.

### Changed

- History schema v3 names `/proc/<router-pid>/schedstat` telemetry `router_runqueue_delay_ms` so it cannot be mistaken for host-wide scheduler delay.
- `diagnose` no longer labels CPU consumers as causal sources based only on CPU share; it reports CPU composition as descriptive evidence.
- CPU pressure classification uses current PSI or repeated high-PSI samples, rather than a single historical peak; missing PSI is reported as unknown.
- Added regression checks for transient pressure spikes, router-only runqueue telemetry, and missing PSI.

## [0.6.2] - 2026-09-30

Host health attribution and corrected scheduler history.

### Added

- History schema v2 with top CPU consumers, guarded CPU, and unmanaged CPU attribution.
- `workload-guard diagnose` now classifies recent CPU pressure from stored telemetry.

### Fixed

- History scheduler delay now stores interval runqueue delay instead of cumulative `/proc/<pid>/schedstat` delay.
- Legacy scheduler-delay samples are excluded from v2 scheduler aggregates.

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
