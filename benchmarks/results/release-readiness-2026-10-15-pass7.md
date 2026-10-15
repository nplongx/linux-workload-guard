# Release-readiness assessment — 2026-10-15, pass 7

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not create a `v1.0.0` tag or release.** Paired measurements now use a common elapsed steady-state window and the evaluator reports paired-ratio variability/bootstrap intervals. The five-pair single-worker synthetic and OpenSSL SHA-256 CLI comparisons both meet the predeclared *median-based, workload-specific* evaluator thresholds. Their pair-to-pair ranges are wide, however, and the evidence is from one host and two narrow workload shapes; representative performance is therefore still **OPEN/INCOMPLETE**, not a general performance sign-off. The known two-worker routing limitation and environment-dependent release gates remain unresolved.

## Repository, CI and safety

- Previous pass-6 commits remain in history: [`1e03658`](https://github.com/nplongx/linux-workload-guard/commit/1e036586adea4069bd5777d2d1e300730dbcd9ef) and [`9f29a49`](https://github.com/nplongx/linux-workload-guard/commit/9f29a494c98bffa66786c562eca62f82649032cc).
- Paired benchmark/evaluator/harness changes: [`6c5f198`](https://github.com/nplongx/linux-workload-guard/commit/6c5f198261e1401fc1e231c36da35c2e8fa2ecb6).
- Variability reporting and workload-scoped evaluator output: [`446916e`](https://github.com/nplongx/linux-workload-guard/commit/446916ee0347a893e218cd82f4b2c2aa57c0401e).
- Hosted CI: [run 38702011488](https://github.com/nplongx/linux-workload-guard/actions/runs/38702011488) and [run 38703140529](https://github.com/nplongx/linux-workload-guard/actions/runs/38703140529) completed successfully on Ubuntu 22.04 and 24.04. Ubuntu 22.04 still explicitly skips the longer dynamic-quota integration when delegated `cpu.stat` lacks `usage_usec`, `nr_periods`, and `nr_throttled`; a green workflow does not close that gate.
- Reproducible full local matrix log: [`local-verification-pass7-2026-10-15.log`](local-verification-pass7-2026-10-15.log), including the test revision, version, all unit/integration/safety commands, Python/shell checks and systemd verification.
- Host for this pass: Ubuntu 26.04.1 LTS, x86_64, kernel `7.0.0-38-generic`, Intel Core i5-8250U, 8 logical CPUs, cgroup v2, systemd 259.5, Python 3.14.4. Benchmark CPU governor was `performance`. Temporary parent and heavy child each read back a 100% CPU quota; heavy child was nested beneath the same 100% aggregate parent for both modes. Dynamic quota was disabled in the harness.
- Final runtime audit after the test/benchmark sequence: user-level `workload-router.service` active/running, MainPID `358400`, restart count `0`, exit status `0`; user-level `heavy-workload.slice` remains at `CPUQuotaPerSecUSec=1s` (100%). System-level `heavy-workload.slice` remains inactive with unlimited quota. Route-state contains zero rows; no temporary `lwgb*` units or unit files remain. Host CPU PSI `some avg10=2.18`, `avg60=2.97`, `avg300=3.86`; `full` averages zero. This is not live 0.40 threshold evidence.
- No production config, service, threshold, or quota was changed. No host-wide contention was generated. No `v1.0.0` tag exists. Historical untracked research/artifacts were preserved; the increased untracked count reflects newly created run artifacts and pre-existing material, none was deleted.

## Integrated improvements

1. Baseline and router modes use the same elapsed steady-state window (`relative_s >= 12`), with routing required to converge at least two seconds before that window. This removes the earlier unequal-window/sample-count confound.
2. Paired harness records a workload-kind declaration, common 100% parent CPU ceiling, effective temporary cgroup quota/weight, host PSI/load at trial start/end, CPU model/topology, kernel, cgroup mode/controllers, tool versions, seed, duration, sample counts, route status, and final `COMPLETE`/`INCOMPLETE` state. Existing evidence paths are never overwritten.
3. Added `WORKLOAD_KIND=terminal-openssl`, a CPU-bound OpenSSL SHA-256 CLI workload, alongside the synthetic integer-mixing loop. It uses the same isolated paired harness and reports its own throughput units; results must not be pooled across workload kinds.
4. Evaluator checks manifest completeness, declared repetitions/workers/duration/cgroup mode, raw sample schema/order/finite values and duration bounds, summary/raw pair coverage, steady-window sample counts, route validity, throughput, latency, CPU and RSS limits, and refuses report overwrite. Tests cover PASS/FAIL/INCOMPLETE, throughput regression, malformed evidence and invalid acceptance limits.
5. Evaluator reports per-pair ratios, range, quartiles and deterministic bootstrap percentile interval (10,000 resamples, seed `20261015`). These are descriptive uncertainty, not an unannounced acceptance threshold or a post-hoc override of the predeclared median gates.
6. Controller integration skip diagnostics report missing cgroup counters/capabilities; `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1` makes a skip fatal on runners explicitly provisioned to support live delegated-controller integration.

## Paired benchmark evidence

Both runs: five pairs, one worker, 30 seconds per mode, at least 898 steady samples per mode per repetition, same 100% aggregate CPU ceiling, 70%-of-one-logical-CPU per-process routing threshold, four sustained samples, probe period 20 ms. Ratios below are enabled/baseline, and the gate thresholds were fixed before observing this run: median wake-up p95, wake-up p99 and probe-work p95 ratios `<=1.10`; throughput ratio `>=0.90`; router CPU `<=5%` of one logical CPU; peak RSS `<=32768 KiB`.

### Synthetic Python integer-mixing worker

- Summary: [`routing-contention-common-window-synthetic-pass7-2026-10-15.tsv`](routing-contention-common-window-synthetic-pass7-2026-10-15.tsv)
- Raw samples: [`routing-contention-common-window-synthetic-pass7-2026-10-15.samples.tsv`](routing-contention-common-window-synthetic-pass7-2026-10-15.samples.tsv)
- Environment manifest: [`routing-contention-common-window-synthetic-pass7-2026-10-15.environment.txt`](routing-contention-common-window-synthetic-pass7-2026-10-15.environment.txt)
- Router log: [`routing-contention-common-window-synthetic-pass7-2026-10-15.router.log`](routing-contention-common-window-synthetic-pass7-2026-10-15.router.log)
- Evaluator: [`routing-contention-common-window-synthetic-pass7-2026-10-15.acceptance-variation.txt`](routing-contention-common-window-synthetic-pass7-2026-10-15.acceptance-variation.txt)

| Criterion | Observed | Evaluator status |
|---|---:|---|
| Valid pairs / intended worker routing | 5/5; 1/1 routed each enabled trial | PASS |
| Steady samples per mode per trial | 898–899 | PASS |
| Median wake-up p95 ratio | 1.008x; range 0.387–1.097x | PASS |
| Median wake-up p99 ratio | 0.726x; range 0.390–2.166x | PASS, high pair variability |
| Median probe-work p95 ratio | 0.870x; range 0.263–3.869x | PASS, high pair variability |
| Median worker throughput ratio | 1.006x; range 0.877–1.182x | PASS |
| Router CPU / peak RSS | 0.155–0.187% one core / 18,364–18,488 KiB | PASS |
| Predeclared median-based evaluator | All criteria met for this synthetic workload | PASS, narrow scope only |

### OpenSSL SHA-256 command-line worker

- Summary: [`routing-contention-terminal-openssl-pass7-2026-10-15.tsv`](routing-contention-terminal-openssl-pass7-2026-10-15.tsv)
- Raw samples: [`routing-contention-terminal-openssl-pass7-2026-10-15.samples.tsv`](routing-contention-terminal-openssl-pass7-2026-10-15.samples.tsv)
- Environment manifest: [`routing-contention-terminal-openssl-pass7-2026-10-15.environment.txt`](routing-contention-terminal-openssl-pass7-2026-10-15.environment.txt)
- Router log: [`routing-contention-terminal-openssl-pass7-2026-10-15.router.log`](routing-contention-terminal-openssl-pass7-2026-10-15.router.log)
- Evaluator: [`routing-contention-terminal-openssl-pass7-2026-10-15.acceptance.txt`](routing-contention-terminal-openssl-pass7-2026-10-15.acceptance.txt)

| Criterion | Observed | Evaluator status |
|---|---:|---|
| Valid pairs / intended worker routing | 5/5; 1/1 routed each enabled trial | PASS |
| Steady samples per mode per trial | 898–899 | PASS |
| Median wake-up p95 ratio | 0.991x; range 0.569–1.913x | PASS, high pair variability |
| Median wake-up p99 ratio | 0.614x; range 0.394–2.571x | PASS, high pair variability |
| Median probe-work p95 ratio | 1.016x; range 0.189–2.863x | PASS, high pair variability |
| Median OpenSSL throughput ratio | 1.051x; range 0.891–1.095x | PASS |
| Router CPU / peak RSS | 0.100–0.199% one core / 18,400–18,504 KiB | PASS |
| Predeclared median-based evaluator | All criteria met for OpenSSL SHA-256 on this host | PASS, narrow scope only |

**Interpretation:** both narrow workload runs meet the original median gates, but the ranges and bootstrap intervals are broad (five pairs only), including substantial per-pair latency regressions. Do not claim that routing generally improves latency or that all terminal workloads pass. Next performance work should include a compiler/build or other representative interactive CLI workload, controlled repetitions on another host, and a separately designed two-worker policy test. Do not retroactively change thresholds to turn variability into a pass/fail criterion.

## Release gates

| Gate | Status | Evidence / next action |
|---|---|---|
| Deterministic unit, fault-injection, lifecycle and isolated controller tests | **PASS** | Local matrix below; hosted CI green on both Ubuntu versions, with environment skip noted separately. |
| Benchmark evaluator/harness correctness | **PASS** | Fixtures include PASS/FAIL/INCOMPLETE, no-overwrite, malformed raw data, invalid limits, throughput regression; actual workload runs have manifests/raw files/evaluator outputs. |
| Synthetic single-worker benchmark (declared median gates) | **PASS** | Raw/manifest/evaluator links above; high pair variability and single-host scope remain caveats. |
| OpenSSL terminal CLI single-worker benchmark (declared median gates) | **PASS** | Raw/manifest/evaluator links above; high pair variability and narrow workload scope remain caveats. |
| Representative terminal performance release gate | **INCOMPLETE / OPEN** | Only synthetic and OpenSSL SHA-256 workloads on one host; more representative command workload and independent host still required. |
| Two-worker routing under 100% aggregate cap | **FAIL for tested policy/scenario** | Per-process trigger is 70% for four samples; each worker can remain below threshold when the parent cap is 100%. Do not lower production threshold without false-positive/fairness tests. Design/test aggregate saturation or explicitly limit supported scope. |
| Live host PSI `0.40` crossing and correlated real controller actuation | **NOT RUN** | Passive smoke only; current host PSI remained well below threshold. Keep passive collector running only during a safe natural observation window; require timestamped PSI/decision/quota-before-after evidence. |
| Ubuntu 22.04 live dynamic-quota integration | **SKIP / OPEN** | CI runner lacks required delegated CPU counters. Use a capable delegated cgroup/runner and `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1`; never fake counters. |
| Ubuntu 24.04 controller integration | **PASS** | Hosted CI runs 38702011488 and 38703140529. |
| Independent second host | **NOT RUN** | Execute the documented manifest/test procedure on a separate host and preserve raw results. |
| RC3 soak, minimum 24h / target 72h | **NOT RUN** | Monitor harness tests pass but are not a soak. Start only in an approved window; require continuous timestamped evidence and `run_status=COMPLETE`. |
| Operator-level install/upgrade/rollback on representative test host | **PARTIAL** | Mocked install/uninstall and isolated recovery tests pass; a separate operator-controlled host exercise remains. |
| Production runtime unchanged / temporary cleanup | **PASS** | Runtime audit above; router remained active with PID 358400, zero restarts, 100% user quota, empty route state and no temporary units. |
| Release hygiene | **OPEN** | `0.6.5-rc.1` unchanged; no `v1.0.0` tag. Keep release blocked until all required gates close. |

## Local verification matrix

Full verification was rerun after the evaluator variation changes; all commands completed with exit code zero.

| Check | Status |
|---|---|
| `./test.sh` | PASS |
| `./tests/install-uninstall.sh` | PASS |
| `./tests/integration-routing.sh` | PASS |
| `./tests/recovery-routing.sh` | PASS |
| `./tests/shadow-routing.sh` | PASS |
| `./tests/controller-integration.sh` | PASS — deterministic PSI boundary/readback/recovery on temporary slices, not live host acceptance |
| `./tests/soak-monitor.sh` | PASS — monitor harness tests only, not a soak |
| `./tests/quota-benchmark-safety.sh` | PASS |
| `./tests/routing-benchmark-safety.sh` | PASS |
| `./tests/live-psi-observe-safety.sh` | PASS |
| `./tests/performance-review.sh` | PASS — evaluator fixtures; actual runs separately evaluated above |
| Python compile (`bin/*`, all `benchmarks/*.py`, hardening tests) | PASS |
| POSIX shell syntax checks | PASS |
| Bash syntax checks | PASS |
| `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice` | PASS |
| `git diff --check` | PASS |
| `./bin/workload-guard version` | `0.6.5-rc.1` |

## Next actions

1. Design a two-worker routing policy experiment with explicit aggregate-capacity and fairness criteria; test candidate logic in a temporary cgroup/shadow mode before any production-policy change.
2. Run a representative compiler/build or interactive terminal workload with the same paired manifest/evaluator, then repeat on an independent host. Keep per-pair variability visible.
3. Obtain Ubuntu 22.04 integration evidence on a runner whose delegated cgroup exposes real CPU usage/throttling counters.
4. Collect passive live PSI/controller/quota evidence only during a natural safe `0.40` crossing; do not induce contention or change production threshold/configuration.
5. Complete operator rollback on a dedicated test host and a 24-hour minimum soak (72-hour target) with `run_status=COMPLETE` and reviewed raw evidence.
6. Preserve all historical artifacts, keep `0.6.5-rc.1`, and do not tag/release `v1.0.0` until mandatory gates close.

Dynamic quota remains opt-in; AIMD remains log-only. Gradient2, PID, weighted fairness, eBPF and `sched_ext` remain research/proposed features, not production claims.
