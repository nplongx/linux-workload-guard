# Roadmap: v0.6.5 Release Candidate to v1.0.0

This roadmap describes the project's intended path from the current `v0.6.5-rc.1` release candidate to a stable `v1.0.0`, followed by possible post-1.0 development. It is a plan, not a release-date commitment: milestones are gated by evidence and may move when validation finds issues.

## Current status

- **Current release:** [`v0.6.5-rc.1`](https://github.com/nplongx/linux-workload-guard/releases/tag/v0.6.5-rc.1) (pre-release)
- **Hosted CI:** latest code commit `2bd431f` passed the Ubuntu 22.04 and 24.04 matrix in [run 38671055102](https://github.com/nplongx/linux-workload-guard/actions/runs/38671055102). Both jobs passed. Ubuntu 22.04 explicitly skipped only the dynamic-quota integration stage because its delegated cgroup did not expose `usage_usec`, `nr_periods`, and `nr_throttled`; Ubuntu 24.04 ran and passed that stage. This is an environment-specific coverage gap, not a claim that the controller path was validated on Ubuntu 22.04. Earlier hardening CI evidence remains in [run 38532132779](https://github.com/nplongx/linux-workload-guard/actions/runs/38532132779).
- **Scope:** Dynamic quota control is opt-in. AIMD remains a log-only shadow experiment; experimental replay results are not production controller behavior.
- **Known validation limit:** Live testing has exercised the scheduler-delay path. The production PSI high-pressure threshold has not yet been directly crossed in live decision-making at its default setting; deterministic tests cover the relevant logic.
- **Latest readiness assessment:** [`release-readiness-2026-10-15.md`](benchmarks/results/release-readiness-2026-10-15.md) records this pass's verification, diagnostic run, and remaining gates. It does not authorize a version or release change.

### RC1 hardening evidence — 2026-10-14

The current hardening pass closes several code-level failure modes without changing release version or promoting experimental controllers:

- PSI parsing rejects missing, malformed, non-finite, duplicate, and out-of-range `some.avg10` values. The default `0.40` decision boundary and a just-below-boundary value are covered deterministically; this is **not** a live crossing of `0.40`.
- cgroup CPU telemetry rejects malformed/incomplete snapshots, missing baselines, stale intervals, counter regressions, and impossible throttling deltas. Invalid windows yield no quota proposal and require a new valid measurement interval before resuming.
- Process samples use `/proc/<pid>/stat` start time and actual elapsed sample time. PID reuse and unreadable/regressing samples cannot be treated as valid low-CPU recovery observations. Route state carries the start time; legacy records without it are not trusted for PID-based recovery.
- A stable `protected-workload-recovery.slice` leaf is the fallback when a transient source scope disappears. A live integration failure reproduced `EBUSY` when recovery targeted the parent slice root; this fallback now targets a leaf and regression tests cover cgroup removal/recreation.
- ControlGroup changes/disappearance reset quota baselines. Failed quota writes keep the last confirmed internal value with capped retry backoff. Current per-process demand is used after routing, rather than indefinitely reusing the route-trigger sample.
- A new isolated integration test exercises burst rejection, two competing CPU-bound workers, dynamic quota increase/decrease, AIMD log-only proposals, idle recovery, and cgroup disappearance/recreation. The current version also records a short synthetic paired baseline/enabled probe with p50/p95/p99 wake-up lateness and work duration, Python-loop throughput, host PSI, router process CPU/RSS/HWM, runqueue-delay delta, quota transitions, and routing/quota/recovery timing.
- Three repeated runs of the final bounded controller smoke test (raw files plus aggregation in `benchmarks/results/controller-hardening-smoke-2026-10-14.md`) measured median route detection of 1.066 s, first quota increase at 1.353 s, idle-to-quota-decrease/recovery at 2.377 s, router CPU near 1.16% of one logical CPU, and peak RSS near 17.8 MiB (18,224 KiB). The test-only pressure settings deliberately did not exercise the production PSI-high branch.
- The static quota benchmark cleanup now attempts to restore its initially observed quota instead of unconditionally leaving the heavy slice at `400%`.
- Core routing environment parsing now represents malformed/non-finite values as invalid and validates sample intervals, thresholds, counts, and dwell values before entering the routing loop. Invalid core routing configuration is logged and routing stays disabled rather than crashing at import or spinning with a zero interval. Deterministic regressions cover malformed, non-finite, zero, negative, and out-of-range examples.
- Added `benchmarks/soak-monitor.sh`, a monitor-only collector for a separately approved soak window. A two-second smoke verified its output schema and that it leaves the running service unchanged; this is harness verification only, not a soak.

The evidence remains host-limited. Existing repeated latency comparisons are synthetic, single-host measurements. Earlier 2- and 4-worker benchmark trials did not route the workers and are not valid router-on/router-off comparisons. The three 5-second-per-mode integration probes are smoke measurements; they are not stable performance or overhead acceptance evidence. There is no confirmed second-host validation or 24-hour/72-hour soak. Broad routed-workload throughput remains uncharacterized.

## v1.0.0 goal

Deliver a predictable, observable, recoverable Linux workload-routing and CPU-quota controller with documented configuration and operational behavior. A stable release should prioritize correctness and safe operation over adding new algorithms or kernel-level complexity.

### Release acceptance criteria

A `v1.0.0` release should not be cut until all of the following are satisfied:

- [ ] **Controller correctness:** quota bounds, step size, hysteresis, dwell time, and pressure signals behave as documented.
- [ ] **Signal validation:** tests distinguish scheduler delay, CPU pressure, and quota throttling; throttling alone must not trigger an unsafe quota increase.
- [ ] **Telemetry degradation:** missing, stale, or malformed telemetry results in a documented safe behavior rather than uncontrolled actuation.
- [ ] **Recovery and lifecycle:** service restart, cgroup removal/recreation, install, upgrade, and uninstall paths are tested and documented.
- [ ] **Automated regression:** unit/replay, routing integration, recovery, shadow-mode, and install/uninstall tests pass in CI.
- [ ] **Cross-environment validation:** repeatable tests run on supported Ubuntu versions and, where practical, more than one host/workload profile.
- [ ] **Soak testing:** complete at least one 24-hour soak (target 72 hours before stable release where practical), recording errors, restarts, quota transitions, and resource overhead.
- [ ] **Performance evidence:** publish reproducible baseline-versus-enabled results, including latency p50/p95/p99, throughput, recovery time, CPU/memory overhead, and repeated trials.
- [ ] **Operator experience:** configuration defaults, supported settings, logs, troubleshooting, limitations, and upgrade/rollback procedures are documented.
- [ ] **Release hygiene:** changelog, version, tagged artifacts/release notes, and CI status agree; unresolved release-blocking defects are closed or explicitly documented.

## Milestones

The time ranges below are planning estimates from the current RC, not promises. The next milestone depends on the exit criteria being met.

### Phase 1 — RC1 hardening

**Focus:** close correctness and operational gaps before expanding scope.

- Review controller state transitions and edge cases in the dynamic quota path.
- Add deterministic replay/regression coverage for high/low pressure, hysteresis, throttling, missing telemetry, and boundary values.
- Verify service lifecycle, cgroup disappearance/reappearance, and safe recovery behavior.
- Keep AIMD in shadow/log-only mode until comparative evidence justifies a production change.

**Exit gate:** all automated tests pass, behavior is documented, and no known critical correctness or recovery defect remains.

### Phase 2 — RC2 validation across environments

**Focus:** establish reproducible evidence under different load shapes and environments.

- Run bounded CPU contention tests at multiple worker counts and repeat each scenario.
- Validate scheduler-delay and PSI signals independently where possible, including live validation of the default production PSI threshold.
- Compare routing enabled versus baseline; record latency percentiles, throughput, quota changes, recovery, and overhead.
- Confirm behavior on supported Ubuntu versions and record kernel/cgroup/environment details with results.
- Retain trial validity metadata, raw samples, actual route count, initial/effective quota, service restarts, PSI, scheduler-delay deltas, and elapsed detection/recovery times. Do not compare a router trial unless all required workers actually route.
- Include a valid two-or-more-worker contention scenario or document an explicit supported-load limitation if the per-process routing trigger prevents reliable routing.

**Exit gate:** evidence is repeatable, limitations are explicit, and no unexplained regression exceeds the project's documented acceptance limits.

**Current RC2 blockers:** direct live observation of PSI at the production `0.40` decision boundary; valid repeated multi-worker comparisons across environments (the available 5-second smoke runs are diagnostic, not acceptance-level performance evidence); and cross-host evidence where another host is available. Hosted CI is green, but the dynamic-quota integration stage was environment-skipped on Ubuntu 22.04 because its delegated cgroup lacked required CPU counters; obtain CPU-controller integration evidence on that version or document the supported-environment limit before stable release. If a safe live crossing cannot be produced without excessive load, keep the branch explicitly unvalidated rather than changing production thresholds to manufacture a result.

### Phase 3 — RC3 soak and recovery

**Focus:** stability over time rather than adding features.

- Complete a 24-hour soak, aiming for 72 hours before stable release where resources permit.
- Exercise restart/recovery and workload start/stop during controlled tests.
- Check for oscillation, stuck quota states, stale telemetry, service crashes, and resource leaks.
- Publish a concise release-readiness report with commands, environment, results, and known limitations.

**Exit gate:** no release-blocking issue is found; any remaining limitation has a documented mitigation.

**Soak evidence status:** no 24-hour run or 72-hour target run is recorded as of 2026-10-15. Existing load traces are short experiments and cannot be counted toward this gate. The soak report must record its continuous start/end timestamps, host/kernel/cgroup details, router restarts/errors, routed process counts, quota transitions/oscillation, PSI and scheduler-delay telemetry availability, CPU/memory overhead, and recovery events.

`benchmarks/soak-monitor.sh OUTPUT_DIR [DURATION_SECONDS=86400] [INTERVAL_SECONDS=60]` prepares monitor-only artifacts without generating load or changing the live service. It has been smoke-tested for two seconds only. Before a release soak, select a safe host/window and define the representative workload phases; then analyze its service journal and metrics alongside workload and transition logs. The monitor alone does not satisfy the soak gate.

### Phase 4 — v1.0.0 release preparation

**Focus:** stable public contract and upgrade confidence.

- Freeze the v1.0 configuration/API surface unless a critical fix requires a change.
- Review defaults, examples, logging, CLI/service behavior, install/upgrade/uninstall, and rollback instructions.
- Finalize README, changelog, troubleshooting guide, and performance methodology/results.
- Cut a final release candidate if meaningful changes land after RC3; promote to `v1.0.0` only after acceptance criteria pass.

**Exit gate:** release checklist complete, CI green, documentation aligned with behavior, and release notes accurately state tested environments and limitations.

## Post-v1.0 opportunities

These are candidate directions, not commitments for the 1.0 release.

### Priority A — Safety controller

Introduce explicit states such as `NORMAL`, `DEGRADED`, `RECOVERY`, and `SAFE_MODE`. Define bounded actions for missing telemetry, controller restart, cgroup lifecycle events, and inconsistent observations. Test transitions with replay scenarios before enabling new live behavior.

### Priority A — Explainable decisions and diagnostics

Make each quota/routing decision explainable through structured logs and a small operator-facing interface. Candidate commands:

- `workload-guard status` — service and controller state
- `workload-guard explain` — latest decision and the signals behind it
- `workload-guard history` — recent transitions
- `workload-guard doctor` — configuration and environment checks

### Priority B — Workload profiles

Provide explicit, documented profiles such as `interactive`, `balanced`, `throughput`, and `background`. Each profile should have measurable intent, safe bounds, and predictable overrides rather than hidden heuristics.

### Priority B — Adaptive quota controller 2.0

Keep sensing, decision, and actuation separated. Compare candidate controllers in offline replay and shadow mode first; promote only with pre-defined acceptance thresholds, bounded actions, and a rollback path. Do not replace the current controller based on a single-host result.

### Research / longer-term

- **Multi-resource control:** memory and I/O only after CPU control is stable and resource-specific telemetry is validated.
- **Fairness:** explicit per-workload fairness goals and starvation tests.
- **Predictive control:** begin with simple, interpretable smoothing (for example EWMA) and evaluate against reactive baselines before considering more complex models.
- **Kernel-level experiments:** eBPF or `sched_ext` remain research tracks until they demonstrate a clear benefit, portability, maintainability, and a safe fallback.
- **Fleet management:** central policy and multi-host coordination only if a real deployment need emerges.

## Engineering principles

1. **Evidence before complexity.** Prefer reproducible measurements and replay tests over intuition.
2. **Safe by default.** New adaptive behavior starts disabled or in shadow mode until validated.
3. **Bounded actuation.** Every controller action has explicit limits, dwell/hysteresis rules, and a recovery path.
4. **Explainability.** Operators should be able to understand why a decision occurred.
5. **Scope discipline.** Keep `v1.0.0` focused on correctness, stability, documentation, and reproducibility; defer speculative features.

## How to contribute

Issues and pull requests that improve a release acceptance criterion are especially welcome. Please include the environment, reproduction steps, expected versus observed behavior, and test evidence. For controller or performance changes, include baseline comparisons and explain how the change can be disabled or rolled back.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [CHANGELOG.md](CHANGELOG.md) for shipped changes.

## Operational rollback and release checklist

For an operator rollback of the experimental adaptive quota path, edit `~/.config/linux-workload-guard/workload-guard.env` and set `WORKLOAD_GUARD_DYNAMIC_QUOTA=false`; set `WORKLOAD_GUARD_AIMD_SHADOW=false` if its diagnostic logs are also unwanted, then run `systemctl --user restart workload-router.service`. Verify the service is active, check its journal for configuration/telemetry errors, and inspect `CPUQuotaPerSecUSec` on `heavy-workload.slice`. If quota was changed before the rollback, restore only the previously recorded/known quota value; do not assume `400%` is the correct runtime restoration value. The router does not automatically reset the heavy slice quota merely because dynamic control is disabled.

Before promoting a candidate, verify every item below with fresh evidence:

- [ ] Local regression, routing, recovery, shadow, install/uninstall, systemd, and shell checks pass; record environmental skips separately from passes.
- [ ] Hosted CI for Ubuntu 22.04 and 24.04 passes at the candidate SHA; record whether runtime integration tests ran or skipped.
- [ ] PSI `0.40` default live-decision validation completed safely, or release notes explicitly retain it as unvalidated.
- [ ] Repeated valid baseline/enabled trials report p50/p95/p99, throughput, actual route validity, recovery, quota transitions, and guard CPU/memory overhead.
- [ ] Additional host/workload evidence collected where available, with kernel/cgroup details.
- [ ] RC3 soak of at least 24 hours completed (target 72 hours where practical) with report and no release-blocking stability issue.
- [ ] Upgrade, rollback, legacy route-state behavior, and operator troubleshooting reviewed.
- [ ] Release version, changelog, tag, release notes, and candidate SHA all agree.

Do not release `v1.0.0` while any required acceptance gate remains unverified. AIMD, Gradient2-inspired replay, PID, weighted fairness, eBPF, and `sched_ext` are not production controller features.
