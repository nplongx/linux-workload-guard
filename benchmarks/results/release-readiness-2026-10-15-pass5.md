# Release-readiness assessment — 2026-10-15, hardening pass 5

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not create a `v1.0.0` tag or release.** This pass verifies quota actuation readback, strengthens telemetry and rollback regressions, integrates the existing isolated lifecycle/soak/benchmark fixes, and reruns the complete local verification matrix. It does not close the live production PSI threshold, representative performance, Ubuntu 22.04 quota-integration, second-host, or RC3 soak gates.

## Reconciled repository and runtime state

- Initial branch was `main`; `HEAD` and `git ls-remote origin refs/heads/main` both matched `9f9ee069003ea0eb3d9ddd19709ce5e88946bb5f` (`docs: record pass 3 readiness and benchmark correction`). The reported `cb60626` exists in history, followed by `2306ab8`, `5396a38`, and `9f9ee06`; it was not the actual starting `HEAD`.
- Code commit [`32e5542`](https://github.com/nplongx/linux-workload-guard/commit/32e5542) was committed and pushed to `main`; `HEAD` and remote `main` matched `32e5542917dd23459eccf932b93134ac6aedf309` immediately after push. Hosted CI [38689233587](https://github.com/nplongx/linux-workload-guard/actions/runs/38689233587) completed successfully on both Ubuntu 22.04 and 24.04.
- `VERSION` remains `0.6.5-rc.1`; `git tag --list v1.0.0` returned no tag.
- The 64 pre-existing historical/research artifacts remain present and unmodified. The pass-4 report remains a separate untracked historical report. Six reviewed pass-4 quota-benchmark smoke result/manifest files were added to the code commit; their contents were not regenerated or overwritten in this pass. Other untracked historical artifacts remain untracked.
- Local host: Ubuntu 26.04.1 LTS, kernel `7.0.0-38-generic`, x86_64, 8 logical CPUs, cgroup v2, systemd 259.5, Python 3.14.4. This is the same host as the prior pass, **not** second-host evidence.
- Current **user-level** `workload-router.service`: `active/running`, `MainPID=358400`, `NRestarts=0`, `ExecMainStatus=0`, cgroup `/user.slice/user-1000.slice/user@1000.service/app.slice/workload-router.service`. The user-level `heavy-workload.slice` remains `CPUQuotaPerSecUSec=1s` (100%). The separate **system-level** `heavy-workload.slice` is inactive and reports `CPUQuotaPerSecUSec=infinity`; it is not the service's quota.
- Live config remains unchanged: `WORKLOAD_GUARD_DYNAMIC_QUOTA=true`, `WORKLOAD_GUARD_AIMD_SHADOW=true`, routing shadow enabled, PSI high/low `0.40/0.10`, quota interval 5 s, bounds 100–400%, step 50%, dwell 10 s, scheduler-delay high/low 20/5 ms. No production config or quota was changed by this pass.
- Final sampled host CPU PSI after verification: `some avg10=0.74`, `avg60=2.94`, `avg300=3.44`; `full` averages were zero. These observations are far below the production 40% high threshold and are not live threshold-acceptance evidence. The runtime route-state file had zero routed records; no pass-5 test slices remained loaded or active.

## Integrated changes

1. **Quota observation reconciliation:** each eligible controller window reads the current systemd `CPUQuotaPerSecUSec` instead of trusting a cached value indefinitely. Missing/invalid properties invalidate the cache. First/recovered observations and out-of-band changes reset the counter baseline and skip that decision window; external changes also reset dwell.
2. **Actuation readback:** after `systemctl --user set-property`, `set_unit_quota()` reads back the effective property and reports success only if it matches the requested integral percentage. A successful command with missing/mismatched readback is treated as failed actuation: the confirmed cache is not advanced and bounded retry/backoff applies. Deterministic tests cover exact, mismatched, and unavailable readback.
3. **Normalized demand validation:** negative or above-one route demand is rejected by the production decision function and both proposal helpers rather than clamped into plausible evidence. Current quota bounds and integral precision are checked before proposals/actuation.
4. **Integrated controller/lifecycle regressions:** isolated test slices exercise burst rejection, two competing workers, quota increase/decrease, external quota changes, AIMD log-only proposals, router restart with active routes, cgroup recreation, recovery of both workers, and restart with dynamic quota disabled while preserving the explicitly selected temporary quota.
5. **Production-test isolation:** routing/recovery/shadow tests use unique temporary slice hierarchies and no longer stop/restart the installed service or mutate production slice quotas. Static safety checks prevent those calls from reappearing in the integration scripts.
6. **Benchmark safety:** the static quota sweep uses a unique temporary user slice, monotonic trial timing, required cgroup-counter validation, collision refusal for both TSV and manifest, environment metadata, and `INCOMPLETE` status on failed trials. It does not touch the installed router or production heavy slice. The three one-worker smoke runs were short harness checks only: work size 5,000,000 iterations, one repetition, 100% and 200% quota; elapsed times were 0.057–0.069 s and measured cgroup CPU was 0.039–0.043 s. The quota-period/throttling counters were too sparse for a speedup conclusion. Raw TSVs and manifests are preserved in the commit.
7. **Soak timing integrity:** duration/cadence/sample collection use a monotonic clock; wall-clock timestamps are checked against monotonic cadence. A slow final sample, missing monotonic clock, wall-clock discontinuity, missed deadline, restart/PID discontinuity, missing telemetry, or journal-capture failure prevents `run_status=COMPLETE`. The monitor remains passive: it does not generate load, change configuration, or write quota properties.

## Verification matrix

The full local matrix was rerun after the actuation-readback change. Results are direct command outcomes, not inferred from earlier reports.

| Command/check | Result | Evidence / limitations |
|---|---|---|
| `./test.sh` | **PASS** | Unit/replay checks, controller hardening, static integration-safety guards, quota benchmark safety, routing benchmark safety, soak-monitor tests. |
| `./tests/install-uninstall.sh` | **PASS** | Mocked systemd install/uninstall; user configuration preservation checked. |
| `./tests/integration-routing.sh` | **PASS** | Isolated slices; 120 ms burst rejected, sustained route observed in 1.07 s. |
| `./tests/recovery-routing.sh` | **PASS** | Isolated slices; idle-to-recovery 1.90 s. |
| `./tests/shadow-routing.sh` | **PASS** | Isolated slices; burst rejected, route in 1.07 s, recovery in 3.34 s. |
| `./tests/controller-integration.sh` | **PASS** | Two workers routed; dynamic quota increased/decreased; external quota reconciled; AIMD stayed log-only; active-route restart and cgroup recreation recovered; disabled controller preserved temporary quota. Detection 0.809 s, first increase 1.354 s, idle-to-decrease/recovery 1.605 s. |
| `./tests/soak-monitor.sh` | **PASS** | Argument/no-overwrite, schema/permissions, quota reversals, restart/PID/counter baseline/reset, sampling overruns including final sample, monotonic-clock loss, wall-clock discontinuity, journal failure, missing service, interruption/completeness. These are harness tests, not a soak. |
| `./tests/quota-benchmark-safety.sh` | **PASS** | Argument/collision guards, temporary-slice-only targeting, cgroup counter reset causes `INCOMPLETE`, no production unit calls. |
| `python3 -m py_compile bin/workload-router.py bin/workload-profile benchmarks/*.py tests/controller-hardening.py` | **PASS** | All requested Python compile targets. |
| `sh -n install.sh uninstall.sh test.sh bin/run-workload bin/workload-guard bin/limit-browser-automation-cgroup tests/*.sh benchmarks/soak-monitor.sh` | **PASS** | POSIX shell syntax. |
| `bash -n benchmarks/quota-benchmark.sh benchmarks/routing-contention-isolated.sh` | **PASS** | Bash syntax. |
| `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice` | **PASS** | Repository-supported unit paths. |
| `git diff --check` | **PASS** | Clean before documentation commit. |
| `./bin/workload-guard version` | **PASS** | `0.6.5-rc.1`. |

### Short synthetic paired probe

The final controller integration run's 5-second-per-mode probe measured:

- Wake-up lateness p50/p95/p99: baseline `0.084/0.445/17.244 ms`, enabled `0.084/0.390/2.229 ms`.
- Probe work-duration p50/p95/p99: baseline `1.389/16.943/17.473 ms`, enabled `1.514/1.921/17.257 ms`.
- Two-worker Python-loop throughput: baseline `48,413`, enabled `57,793` iterations/s in this one run.
- Router CPU: `0.070 CPU-s` over `5.211 s` (~1.34% of one logical CPU); peak RSS `18,740 KiB`; router runqueue-delay delta `51.085 ms`.
- Host PSI `some avg10`: baseline start/end `4.52/8.21`, enabled `8.21/9.31`. Quota transitions included `100→150`; the test then changed its temporary slice externally to `350%`, after which the controller stepped `350→300→250→200→150`. One direction reversal was observed.

This is a synthetic single-host diagnostic with variable host load, not an application SLA, representative workload result, or generalized performance claim. The static quota smoke is likewise too short to estimate speedup or throttling behavior.

## Hosted CI and environment coverage

- Code commit [`32e5542`](https://github.com/nplongx/linux-workload-guard/commit/32e5542) was pushed successfully; remote `main` matched the commit. Hosted CI [38689233587](https://github.com/nplongx/linux-workload-guard/actions/runs/38689233587) passed both jobs. Ubuntu 22.04 explicitly skipped dynamic-quota integration because the delegated test cgroup did not expose `usage_usec`, `nr_periods`, and `nr_throttled`; Ubuntu 24.04 executed and passed the integration. A skip is not a pass. This host's Ubuntu 26.04 counters do not establish Ubuntu 22.04 support or second-host coverage.

## Remaining release gates

| Gate | Status | Exact closure evidence |
|---|---|---|
| Production PSI-high threshold `0.40` | **OPEN** | Safe timestamped live PSI at/above `0.40`, matching controller decision trace, and bounded quota before/after on an isolated/capable setup while production settings remain unchanged. Deterministic tests cover below/equal/above, corroboration, and invalid signals but are not live acceptance. Current PSI is below threshold; do not manufacture host-wide contention. |
| Representative performance | **OPEN** | Predefine acceptance bounds, then collect repeated paired representative workloads with comparable measurement windows, verified worker routing, throughput, p50/p95/p99, PSI, scheduler delay, quota/recovery, CPU/RSS, restart/error counts, and multiple load shapes. Current 5-second synthetic probes and sub-0.1-second quota smoke are diagnostics only. |
| Ubuntu 22.04 quota integration | **OPEN / environment SKIP** | Obtain a runner or delegated test cgroup exposing all required counters and a real passing controller integration, or document a deliberate support limitation. Keep the explicit skip until functional evidence exists. |
| Second host | **OPEN** | Run and preserve tests on a genuinely separate machine with its own kernel/CPU/cgroup manifest. None was available/used in this pass. |
| RC3 soak | **OPEN** | Run at least 24 continuous hours, targeting 72 hours where practical. Require `run_status=COMPLETE`, full raw sample coverage, start/end timestamps, no missed intervals or critical missing metrics, stable service identity/restart count and journal, quota transitions/reversals, PSI, CPU/RSS overhead, approved workload phases, and reviewed recovery events. No long soak was started. |
| Lifecycle and rollback | **PARTIAL** | Isolated restart/recovery/cgroup recreation and dynamic-quota-disabled rollback pass. Still validate the documented operator config edit/restart/manual quota-restoration procedure and upgrade/uninstall behavior on representative supported hosts without disturbing the live service. |
| Release hygiene | **OPEN** | Final candidate hosted CI green, documentation commit CI green, all applicable gates reviewed, and a separate release decision. Do not tag or publish `v1.0.0` while any required gate remains open. |

## Soak procedure and acceptance

On a suitable host with a pre-approved workload plan and safe operating window, start the monitor as the service-owning user:

```sh
benchmarks/soak-monitor.sh "$HOME/lwg-soak-$(date +%Y%m%d-%H%M%S)" 86400 60
```

If a terminal disconnect is possible, supervise it through a transient `systemd --user` service. Check service/journal continuity periodically; do not change production configuration to force a threshold. A graceful early stop is an incomplete run. Review `environment.txt`, `samples.tsv`, `errors.tsv`, `service-journal.log`, workload phase logs, and quota/recovery events. Only accept the soak if `run_status=COMPLETE`, duration is at least 24 hours, sample cadence/coverage is valid, and all required metrics and recovery events have been reviewed. Use `259200` seconds for the 72-hour target.

Dynamic quota remains opt-in; AIMD remains log-only. Gradient2, PID, weighted fairness, eBPF, and `sched_ext` remain research/proposed work, not completed production features.
