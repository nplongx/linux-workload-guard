# Roadmap: v0.6.5 Release Candidate to v1.0.0

This roadmap describes the project's intended path from the current `v0.6.5-rc.1` release candidate to a stable `v1.0.0`, followed by possible post-1.0 development. It is a plan, not a release-date commitment: milestones are gated by evidence and may move when validation finds issues.

## Current status

- **Current release:** [`v0.6.5-rc.1`](https://github.com/nplongx/linux-workload-guard/releases/tag/v0.6.5-rc.1) (pre-release)
- **CI:** Ubuntu 22.04 and 24.04 checks passed for the release candidate.
- **Scope:** Dynamic quota control is opt-in. AIMD remains a log-only shadow experiment; experimental replay results are not production controller behavior.
- **Known validation limit:** Live testing has exercised the scheduler-delay path. The production PSI high-pressure threshold has not yet been directly crossed in live decision-making at its default setting; deterministic tests cover the relevant logic.

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

**Exit gate:** evidence is repeatable, limitations are explicit, and no unexplained regression exceeds the project's documented acceptance limits.

### Phase 3 — RC3 soak and recovery

**Focus:** stability over time rather than adding features.

- Complete a 24-hour soak, aiming for 72 hours before stable release where resources permit.
- Exercise restart/recovery and workload start/stop during controlled tests.
- Check for oscillation, stuck quota states, stale telemetry, service crashes, and resource leaks.
- Publish a concise release-readiness report with commands, environment, results, and known limitations.

**Exit gate:** no release-blocking issue is found; any remaining limitation has a documented mitigation.

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
