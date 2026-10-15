# Release-readiness assessment — 2026-10-15, pass 8

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not tag or release `v1.0.0`.** Aggregate-saturation routing is now implemented behind an explicit opt-in flag and has deterministic policy coverage plus a successful one-pair two-worker synthetic routing smoke. That is a meaningful controller improvement, but not a performance acceptance result. The compiler/build smoke did not route its benchmark worker PIDs because compiler subprocesses churn faster than the current PID-oriented routing model can reliably track; its paired result is `INCOMPLETE`. Representative performance, cross-host compatibility, live PSI, operator rollback, and soak gates remain open.

## Repository and runtime baseline

- Starting HEAD was `f84f5fd5f9e4285915b289fa1e74fa596492edae`, synchronized with `origin/main`; `VERSION` was `0.6.5-rc.1`.
- Numerous historical benchmark/result files were already untracked. They were preserved. New evidence uses pass-8-specific paths; no existing result was overwritten.
- Production user service remained active/running, MainPID `358400`, restart count `0`, exit status `0`. User-level `heavy-workload.slice` read back `CPUQuotaPerSecUSec=1s` (100%). System-level heavy slice remained inactive with unlimited quota. Route state had zero rows and no temporary `lwgb*` unit files remained after tests.
- `WORKLOAD_GUARD_AGGREGATE_ROUTING` was not set in the installed user config; its code default is `false`. No production service, configuration, quota, or threshold was changed.
- Host: Linux x86_64, kernel `7.0.0-38-generic`, cgroup v2, 8 logical CPUs, Intel Core i5-8250U, systemd 259, Python 3.14.4. PSI after the test matrix was below the `some avg10=0.40` trigger; no natural threshold crossing was observed or induced.

## Aggregate-saturation policy

The optional policy reads the effective parent systemd quota every five seconds and fails closed if it is missing/unlimited/invalid. It requires at least two observed workers at >=20% of one logical CPU each and aggregate demand >=85% of parent quota for three consecutive samples. The latch clears at <=65% for three samples (or fewer than two contributing workers). It proposes at most one route per sample, rotating by PID order. Existing process identity/cgroup checks are still required before moving a PID. The original per-process 70% threshold is unchanged; dynamic quota remains opt-in and separate.

Deterministic tests cover entry/exit hysteresis, single-worker/burst rejection, flapping demand, quota drift, missing quota telemetry, and fair rotation over three workers. See [`docs/aggregate-routing.md`](../../docs/aggregate-routing.md).

### One-pair two-worker synthetic smoke

The initial exploratory artifact family `routing-contention-aggregate-synthetic-smoke-2026-10-15.*` is preserved. After tightening the accounting so unrelated processes in a sibling heavy slice cannot inflate the protected-parent demand signal, the follow-up family is `routing-contention-aggregate-synthetic-smoke2-2026-10-15.*` with its evaluator report.

- One pair, 25 seconds per mode, two workers; same 100% parent quota and nested 100% heavy child. Both intended worker PIDs routed at 8.450 seconds. Router CPU 0.241% of one logical CPU; peak RSS 18,836 KiB.
- Router-mode steady wake-up p95/p99 were 19.746/39.096 ms versus 19.949/39.611 ms baseline; throughput was 852,373 versus 764,803 Python iterations/s in this single pair. The evaluator recorded p95 ratio 0.990, p99 ratio 0.987, probe-work p95 ratio 1.002, and throughput ratio 1.114.
- **Acceptance remains INCOMPLETE:** only one pair, baseline steady samples numbered 489 and router steady samples 457 (both below the evaluator minimum 500). Host PSI/load changed across trial windows. Do not infer a repeatable latency or throughput improvement from this smoke.
- Source-integrity caveat: the smoke was run before the pass-8 changes were committed, so its manifest's `git_revision` names the starting HEAD and `git_dirty=true`; the recorded tracked-source diff hash is `49031516dec5e1cb82421d54fc03ef2d10a7af6ea172cd1ebc62101a9de8e1d8`. Treat it as local functional evidence only, not a clean-commit benchmark artifact. The harness now records dirty-state and tracked-diff SHA-256 in future manifests; a clean-source, five-pair run is still required.

### Compiler/build workload smoke

Artifact family: `routing-contention-build-aggregate-smoke2-2026-10-15.*`.

- Bounded repeated GCC/CC compile-and-link of a generated 12-translation-unit local C workload; two workers, 25 seconds per mode, same aggregate CPU ceiling.
- Router trial routed `0/2` benchmark worker PIDs and is marked `routing-incomplete:0/2`; manifest `result_status=INCOMPLETE`, evaluator `acceptance_status=INCOMPLETE`.
- This exposes a genuine process-identity/scope limitation: the current controller routes individual PIDs while the build wrapper spawns short-lived compiler children. The summary latency values are not a valid paired router-on/router-off result. Do not retry repetitions just to create a pass; first design a safe process-tree/scope routing model or a correctly identified long-lived compiler workload, with explicit churn/fairness tests.

The existing five-pair OpenSSL SHA-256 CLI evidence from pass 7 remains available as narrow single-host evidence; it was not repeated just to refresh this report. It does not generalize to builds or all terminal workloads.

## Verification performed

Full local matrix log: [`local-verification-pass8-2026-10-15.log`](local-verification-pass8-2026-10-15.log).
The focused hardening/config regression reruns are recorded in [`controller-hardening-pass8-final-2026-10-15.log`](controller-hardening-pass8-final-2026-10-15.log) and [`controller-hardening-pass8-postscope-2026-10-15.log`](controller-hardening-pass8-postscope-2026-10-15.log); after the final routing-scope adjustment, the routing benchmark safety, Python/shell syntax, systemd verification, diff check and version smoke were rerun alongside the latter.

- `./test.sh`: PASS.
- Install/uninstall smoke: PASS (systemctl mocked; user config preserved).
- Routing integration: PASS; 120ms burst rejected, sustained detection 0.81s on the final matrix run.
- Recovery routing: PASS; idle-to-recovery 1.83s.
- Shadow routing: PASS; short burst rejected, sustained route and recovery completed.
- Controller integration: PASS on temporary slices; production PSI boundary function exercised below/equal/above 0.40, missing/stale hold, bounded quota readback, two-worker route/recovery, quota reconciliation, router restart, and cgroup recreation.
- Quota/routing benchmark safety, live PSI collector safety, performance evaluator tests, soak-monitor tests: PASS.
- Python compilation, POSIX shell syntax, Bash syntax, `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice`, `git diff --check`: PASS.
- `./bin/workload-guard version`: `0.6.5-rc.1`.
- Hosted CI for pass-8 commit [`fc22fb1`](https://github.com/nplongx/linux-workload-guard/actions/runs/38710073650): **PASS** on Ubuntu 22.04 and 24.04. Ubuntu 22.04 still reports the explicit delegated `cpu.stat` skip detailed below; the workflow is green but that live-controller gate is not closed. A non-blocking Node.js 20 deprecation notice for `actions/checkout@v4` remains.

## Release gates

| Gate | Status | Evidence / next action |
|---|---|---|
| Deterministic routing/controller/fault-injection regression | **PASS** | Includes aggregate entry/exit, fairness, burst/single-worker, flapping, quota drift and missing quota; existing lifecycle/fault suites pass. |
| Aggregate two-worker route functionality under 100% parent cap | **PASS — smoke scope only** | Both stable Python worker PIDs routed in one paired smoke. Repeat five valid pairs before any performance claim. |
| Aggregate-policy performance acceptance | **INCOMPLETE / OPEN** | One pair only; baseline has 482 steady samples and fails the evaluator minimum. Five-pair two-worker run not completed. |
| Compiler/build representative performance | **INCOMPLETE / OPEN** | Smoke routes 0/2 benchmark worker PIDs due compiler process churn; redesign worker identity/scope routing before acceptance. |
| CLI CPU-bound evidence | **PASS — narrow prior evidence** | Pass-7 five-pair OpenSSL SHA-256 CLI result on this host; not a universal workload pass. |
| Ubuntu 24.04 controller integration | **PASS** | Hosted CI run 38710073650 passed the complete matrix including controller integration. |
| Ubuntu 22.04 real delegated CPU counters | **SKIP / OPEN** | Pass-8 hosted run 38710073650 again recorded kernel `6.8.0-1064-azure`, cgroup v2, controllers `cpuset cpu io memory hugetlb pids rdma misc`, but the test leaf's `cpu.stat` exposed only `usage_usec,user_usec,system_usec,core_sched.force_idle_usec` and lacked `nr_periods`/`nr_throttled`. This is a delegated-cgroup interface/counter limitation on that hosted runner, not a fabricated counter or a passing integration. Run with `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1` on a runner provisioned with real quota/throttling counters. |
| Natural live PSI `some avg10 >=0.40` correlated with controller action | **NOT RUN** | Current passive observations did not cross threshold. Keep collector passive; record timestamped PSI, decision, quota readback and service state only if a safe natural event occurs. |
| Independent second host | **NOT RUN** | No independent host was available in this execution context. Repeat tests and benchmark with a fresh environment manifest on a genuinely independent host. |
| Operator install/upgrade/rollback on dedicated test host | **PARTIAL** | Mocked install/uninstall and isolated recovery pass; operator procedure remains unexecuted on a separate representative host. |
| RC soak (minimum 24h, target 72h) | **NOT RUN** | Monitor tests are not a soak. Use a safe host/window, continuous telemetry, workload-phase log, journal, `run_status=COMPLETE`, and reviewed raw evidence. |
| Production runtime unchanged and temporary cleanup | **PASS** | Service identity/restart count, user quota, system quota, route-state row count and temporary unit cleanup checked after tests. |
| Release hygiene | **OPEN** | Keep `0.6.5-rc.1`; no `v1.0.0` tag/release while required gates remain open. |

## Next actions in dependency order

1. Review the pass-8 implementation/CI, then design process-tree or scope-aware routing for compiler/build workloads with resource bounds, PID-reuse safety, churn, fairness, recovery and rollback tests.
2. Run at least five valid paired aggregate two-worker trials and a valid compiler/build benchmark; keep the same common steady window, 100% aggregate CPU ceiling, immutable raw samples, manifest, predeclared evaluator, per-pair ratios and bootstrap intervals. Do not exclude noisy pairs after seeing results.
3. Obtain real delegated-counter evidence on Ubuntu 22.04 and repeat on an independent host.
4. Observe a natural PSI-high crossing only if it occurs safely; perform operator rollback on a dedicated test host.
5. Run a supervised RC soak for at least 24 continuous hours (72-hour target), with complete raw evidence and explicit interruption handling.
