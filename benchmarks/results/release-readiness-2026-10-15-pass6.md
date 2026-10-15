# Release-readiness assessment — 2026-10-15, pass 6

## Decision

**Keep VERSION=0.6.5-rc.1. Do not tag or release v1.0.0.** Pass 6 fixes a benchmark capacity confound, adds measured synthetic worker throughput and isolated-router CPU/RSS to repeated paired trials, adds a predeclared acceptance evaluator with PASS/FAIL/INCOMPLETE, adds a passive live-PSI evidence collector, and documents operator rollback and cross-host validation. The synthetic performance evaluator reports **FAIL** on p99 wake-up latency and probe-work p95. Live PSI, second host, Ubuntu 22.04 delegated-counter integration, representative application performance, and RC3 soak remain open.

## Environment and runtime safety

- Host: Ubuntu 26.04.1 LTS, x86_64, kernel 7.0.0-38-generic, 8 logical CPUs, cgroup v2, systemd 259.5-0ubuntu3.4, Python 3.14.4.
- Starting repository HEAD before this pass: [38148f5](https://github.com/nplongx/linux-workload-guard/commit/38148f5); main and origin/main were aligned.
- Pass-6 implementation/evidence commit: [1e03658](https://github.com/nplongx/linux-workload-guard/commit/1e036586adea4069bd5777d2d1e300730dbcd9ef), pushed to `main`.
- Hosted CI for that commit: [run 38699127061](https://github.com/nplongx/linux-workload-guard/actions/runs/38699127061) completed successfully on Ubuntu 22.04 and 24.04. Both jobs passed their workflow steps; Ubuntu 22.04 explicitly skipped the longer dynamic-quota integration because the delegated test cgroup lacked required counters. Ubuntu 24.04 ran the full controller integration successfully.
- VERSION remains 0.6.5-rc.1; no v1.0.0 tag exists.
- Final local runtime check after benchmark and test matrix: user-level workload-router.service active/running, MainPID=358400, NRestarts=0, ExecMainStatus=0; user-level heavy-workload.slice remains at CPUQuotaPerSecUSec=1s (100%). The distinct system-level heavy-workload.slice is inactive and has unlimited quota. The route-state file contains zero records and no temporary lwgb* units/unit files remain.
- Final sampled host PSI after the final local matrix: some avg10=2.62, avg60=2.58, avg300=2.11; full averages zero. This is below the 40% production threshold and is not live-threshold evidence.
- No production config, service, threshold, or quota was changed. The 100% CPU cap was applied only to temporary benchmark slices.
- The 65 pre-existing untracked historical/research artifacts, including the pass-4 readiness report, remain untouched. New pass-6 raw artifacts are separate and are explicitly listed below.

## Integrated changes

1. **Equal aggregate benchmark capacity.** The temporary heavy slice is now nested beneath the temporary 100% parent. Baseline and router-enabled modes therefore share the same 100% aggregate CPU ceiling; the routed mode no longer receives an extra 100% sibling budget.
2. **Throughput and overhead telemetry.** The isolated benchmark now records synthetic Python integer-mixing iterations/second, router CPU as a percentage of one logical CPU, and router peak RSS (VmHWM), alongside wake-up/work latency. These are synthetic workload diagnostics, not application-SLA or representative terminal-workload results.
3. **Predeclared evaluator.** benchmarks/performance-review.py enforces five paired repetitions, at least 500 steady-state samples per mode, all intended workers routed, median paired wake-up p95/p99 and work-p95 ratios at most 1.10, median throughput ratio at least 0.90, router CPU at most 5% of one logical CPU, and peak RSS at most 32 MiB. Its tests cover PASS, FAIL, INCOMPLETE, and no fabricated acceptance.
4. **Passive live-PSI evidence.** benchmarks/live-psi-observe.py records timestamped host PSI, service identity/restart count, effective quota, and the service journal. It does not create load or actuate quota. Its safety tests cover argument validation, no-overwrite behavior, and passive-only behavior.
5. **Operator procedure.** [docs/operator-rollback.md](../../docs/operator-rollback.md) defines controlled-host rollback steps, Ubuntu/cgroup prerequisites, second-host manifest expectations, soak acceptance, and the status vocabulary.
6. **Historical evidence integrity.** Earlier historical files and the aborted two-worker attempt were retained rather than overwritten. The aborted run is explicitly marked incomplete and is not used for performance comparison.

## Repeated paired benchmark — measured outcome

The five-pair run used one CPU worker, 30 seconds per mode, alternating paired modes, a 100% parent quota with a nested 100% heavy child, and the production-like 70% per-process route trigger. All five enabled trials routed 1/1 worker in 8.336–8.566 s; each mode produced 1,499 probe samples, with 898–899 steady baseline samples and 970–982 steady routed samples.

Raw evidence:

- [Paired summary TSV](routing-contention-paired-metrics-pass6-2026-10-15.tsv)
- [Raw probe samples](routing-contention-paired-metrics-pass6-2026-10-15.samples.tsv)
- [Host/config manifest and run status](routing-contention-paired-metrics-pass6-2026-10-15.environment.txt)
- [Isolated router log](routing-contention-paired-metrics-pass6-2026-10-15.router.log)
- [Detailed paired acceptance review](routing-contention-paired-metrics-pass6-2026-10-15.acceptance-final.txt)

| Acceptance criterion | Measured result | Status |
|---|---|---|
| Five complete pairs; all enabled workers routed | 5/5 enabled trials routed 1/1 | **PASS** |
| At least 500 steady samples per mode/trial | Baseline 898–899; routed 970–982 | **PASS** |
| Median paired wake-up p95 ratio ≤ 1.10 | 1.029x | **PASS** |
| Median paired wake-up p99 ratio ≤ 1.10 | 1.494x | **FAIL** |
| Median paired probe-work p95 ratio ≤ 1.10 | 1.417x | **FAIL** |
| Median paired synthetic worker throughput ratio ≥ 0.90 | 0.942x | **PASS** |
| Router CPU ≤ 5% of one logical CPU | 0.125–0.155% per enabled trial | **PASS** |
| Router peak RSS ≤ 32 MiB | 18,368–18,496 KiB | **PASS** |
| Overall synthetic benchmark acceptance | p99 and probe-work p95 regressions exceed the limit | **FAIL** |

The pair-to-pair variability is material: individual p99 and work-duration ratios include substantial regressions despite passing medians for p95 and throughput. Do not characterize this as a responsiveness improvement. The workload is a synthetic integer-mixing worker plus synthetic periodic probe; a representative terminal workload, additional workload shapes, and independent-host repetition remain necessary.

The earlier 20-second five-pair run is also preserved at routing-contention-equal-capacity-pass6-2026-10-15.*; it predates the throughput/overhead fields and has fewer than 500 steady baseline samples per trial, so it is diagnostic only.

### Multi-worker limitation

The attempted two-worker run was stopped after two enabled trials failed to route either worker. Its manifest is INCOMPLETE, and no latency comparison is valid. The run's sampled host PSI reached 28.07% some avg10, still below 40%; it was not intended to force a PSI crossing and was not repeated.

- [Incomplete two-worker summary](routing-contention-two-worker-incomplete-pass6-2026-10-15.tsv)
- [Raw samples](routing-contention-two-worker-incomplete-pass6-2026-10-15.samples.tsv)
- [Manifest showing INCOMPLETE](routing-contention-two-worker-incomplete-pass6-2026-10-15.environment.txt)
- [Router log](routing-contention-two-worker-incomplete-pass6-2026-10-15.router.log)

This confirms the known per-process threshold limitation under the tested 100% aggregate budget. Do not lower production routing thresholds merely to make a benchmark valid.

## Live PSI collector smoke

The passive collector completed a two-second smoke with four samples, stable service identity/restart count, and quota unchanged at 1s. It found zero samples at or above 40% and zero controller quota-change lines. The correct result is **NOT RUN**, not a live PSI acceptance pass.

- [Collector samples](live-psi-observation-smoke-pass6-2026-10-15/samples.tsv)
- [Collector manifest](live-psi-observation-smoke-pass6-2026-10-15/manifest.txt)
- [Captured service journal](live-psi-observation-smoke-pass6-2026-10-15/controller-journal.log)

## Local verification matrix

All commands below completed with exit code zero after the final pass-6 code and test changes. The complete captured output is in [final local verification log](local-verification-pass6-final4-2026-10-15.log).

| Check | Status |
|---|---|
| ./test.sh including controller hardening and benchmark-review PASS/FAIL/INCOMPLETE fixtures | **PASS** |
| ./tests/install-uninstall.sh | **PASS** |
| ./tests/integration-routing.sh | **PASS** |
| ./tests/recovery-routing.sh | **PASS** |
| ./tests/shadow-routing.sh | **PASS** |
| ./tests/controller-integration.sh | **PASS** — deterministic PSI boundary-to-actuation on temporary slices, routing/quota/recovery and rollback |
| ./tests/soak-monitor.sh | **PASS** — monitor harness tests only, not a soak |
| ./tests/quota-benchmark-safety.sh | **PASS** |
| ./tests/routing-benchmark-safety.sh | **PASS** |
| ./tests/live-psi-observe-safety.sh | **PASS** |
| ./tests/performance-review.sh | **PASS** — evaluator fixtures only; measured benchmark verdict remains FAIL |
| Python compile for runtime, all benchmark Python files, and hardening tests | **PASS** |
| POSIX shell syntax across project/test scripts | **PASS** |
| Bash syntax for quota and routing benchmark wrappers | **PASS** |
| systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice | **PASS** |
| git diff --check | **PASS** |
| ./bin/workload-guard version | **PASS** — 0.6.5-rc.1 |

## Release acceptance gate table

| Gate | Status | Evidence / closure requirement |
|---|---|---|
| Controller correctness, bounds, quota readback/retry, stale/missing telemetry | **PASS locally and in hosted CI where supported** | Full controller-hardening and temporary-slice integration suite; both hosted jobs passed. Ubuntu 22.04's longer dynamic-quota integration is explicitly skipped for missing delegated counters. |
| Deterministic production PSI 0.40 boundary-to-actuation | **PASS deterministic only** | Actual decision function and isolated quota actuator/readback tested below/at/above threshold; not live evidence. |
| Live production PSI-high 0.40 | **NOT RUN** | Collector smoke saw zero threshold samples. Need a safe natural crossing with correlated controller decision and effective quota before/after. |
| Representative performance | **FAIL / OPEN** | Synthetic paired run fails p99 and probe-work p95 limits. Need analyze controller policy/measurement noise and rerun only after correction; then validate representative terminal workloads, multiple load shapes, and another host. |
| Two-or-more-worker routing at production-like threshold | **FAIL for attempted scenario** | Two-worker run did not route either worker and is incomplete. Establish a documented supported-load boundary or safely improve the policy with tests; do not lower thresholds to manufacture evidence. |
| Ubuntu 22.04 dynamic-quota integration | **SKIP / OPEN** | Prior hosted CI [38690941245](https://github.com/nplongx/linux-workload-guard/actions/runs/38690941245) passed its jobs, but Ubuntu 22.04 explicitly skipped the longer integration because delegated cpu.stat lacked usage_usec, nr_periods, and nr_throttled. Need a capable delegated cgroup/runner or an explicit support limitation. |
| Ubuntu 24.04 controller integration | **PASS in prior CI** | Full integration ran and passed in [38690941245](https://github.com/nplongx/linux-workload-guard/actions/runs/38690941245). |
| Second host | **NOT RUN** | Current host and hosted CI are not independent second-host evidence. Use docs/operator-rollback.md manifest checklist on another machine. |
| RC3 soak (24h minimum, 72h target) | **NOT RUN** | No long soak started. Require approved workload phases, continuous timestamps/samples/journal, run_status=COMPLETE, and reviewed recovery/oscillation/overhead. |
| Operator rollback | **PARTIAL** | Mocked install/uninstall and isolated dynamic-quota-disabled rollback pass; controlled operator-level config/upgrade/rollback exercise remains to be run on a representative test host. |
| Hosted CI for pass-6 implementation commit | **PASS with Ubuntu 22.04 environment SKIP** | [Run 38699127061](https://github.com/nplongx/linux-workload-guard/actions/runs/38699127061): Ubuntu 22.04 and 24.04 jobs passed; 22.04 longer dynamic-quota integration skipped due to missing delegated counters. |
| v1.0.0 release hygiene | **OPEN** | Do not tag or release until required gates close and a deliberate release review approves the candidate. |

## Next actions

1. Review why the synthetic paired probe's p99 and work-duration p95 regressions exceed 1.10; keep raw data and do not claim performance gains.
2. Obtain a representative terminal workload and define workload-specific acceptance alongside the candidate synthetic limits.
3. On a safe observation window, run the passive PSI collector long enough to capture natural host behavior; never manufacture a 40% crossing.
4. Resolve or explicitly scope the two-worker routing limitation without lowering the production trigger merely to pass a benchmark.
5. Run dynamic-quota integration on a delegated cgroup exposing all required counters, validate rollback on a separate test host, and complete the 24-hour/72-hour soak.
6. Keep the release version unchanged and no v1.0.0 tag until every mandatory gate is closed. CI is green for the implementation commit, but the environment skip is still an open release gate.

Dynamic quota remains opt-in; AIMD remains log-only. Gradient2, PID, weighted fairness, eBPF, and sched_ext remain research/proposed work.
