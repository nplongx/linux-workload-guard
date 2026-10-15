# Release-readiness assessment — 2026-10-15, hardening pass 3

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not tag or publish `v1.0.0`.** This pass closes soak-completeness defects, adds active-route restart integration coverage, corrects benchmark methodology and evidence labeling, and records one valid but diagnostic equal-per-slice-quota three-pair comparison. Live production PSI `0.40`, cross-host/Ubuntu 22.04 quota coverage, representative performance, rollback review, and the RC3 soak remain open.

## Reconciliation and live runtime

- Starting branch was `main`; starting `HEAD` and `origin/main` both matched `cb60626099bf53ccac8fbe1f949fb46a2a8d2be3` (`cb60626`). Version remained `0.6.5-rc.1`; no `v1.0.0` tag exists.
- The 64 pre-existing untracked research/benchmark paths were preserved. This pass adds one safety-test script, four explicitly named benchmark evidence files, and this readiness report; no previous raw trial, failed trial, or smoke directory was overwritten or removed.
- At final runtime reconciliation after the complete local suite and isolated benchmark checks, the user-level `workload-router.service` was active/running with `MainPID=245947`, `NRestarts=0`, `ExecMainStatus=0`. The relevant **user-level** `heavy-workload.slice` was at `CPUQuotaPerSecUSec=1s` (100%). The similarly named system-level unit is a different manager context and is not the production unit being evaluated.
- The effective user environment remains `WORKLOAD_GUARD_DYNAMIC_QUOTA=true`, `WORKLOAD_GUARD_AIMD_SHADOW=true`, `WORKLOAD_GUARD_SHADOW=true`, PSI high/low `0.40/0.10`, quota interval 5 s, quota bounds 100–400%, step 50%, dwell 10 s, scheduler-delay high/low 20/5 ms. No production configuration or threshold was changed by this pass.
- A host-wide PSI sample at final reconciliation was `some avg10=8.62`, `avg60=6.61`, `avg300=4.21`; `full` averages were zero. Those readings are below the 40% high threshold and are not live threshold-acceptance evidence.

## Implementation and regressions

1. **Soak continuity is stricter.** A router MainPID change, unavailable restart-counter baseline, restart-counter increase/reset, invalid/unavailable final identity/counter, missed sample deadline, or failed/missing journal capture increments sampling failures and forces `run_status=INCOMPLETE`, including discontinuities discovered only during finalization. This prevents an otherwise complete-looking monitor run from passing after a restart, counter reset, missed interval, or loss of service-log evidence.
2. **Active-route restart is integrated.** `tests/controller-integration.sh` now stops and restarts its isolated router while both CPU workers remain in the heavy test cgroup. It verifies the new router reloads persisted PID-starttime-backed route state, the same workers remain routed, and they recover after the workload goes idle. The same test subsequently exercises heavy-cgroup disappearance/recreation and renewed routing/recovery.
3. **RSS peak reporting is corrected.** The controller integration reports the maximum of start/end `VmRSS` and `VmHWM` observations. `/proc/status` values are KiB; the reported peak can no longer be lower than a sampled resident-memory value merely because only the final high-water reading was passed to the metrics formatter.
4. **The router-on/router-off benchmark is now isolated.** `benchmarks/routing-contention-isolated.sh` no longer edits the installed environment file, stops/restarts the installed service, or changes production slice quotas. It creates unique temporary user slices, uses equal 100% protected/heavy ceilings, starts a dedicated direct router with dynamic quota and AIMD disabled, alternates mode order, verifies actual worker placement, refuses existing result paths, and records environment metadata, raw probe samples, and router logs. The safety test rejects too-short windows and output collisions without starting workloads.

## Benchmark methodology correction

Review of the old `routing-contention.py` implementation found that the historical router-off/on trials set `protected-workload.slice` to 100% but `heavy-workload.slice` to 400%. Therefore the modes had unequal effective CPU ceilings, and the old `router_was_active` column described service state **before** the mode switch rather than service state during the trial. Those 2026-10-09 raw results remain preserved, but are no longer considered causal router-only comparisons. The README and prior pass-2 report have been corrected accordingly.

### New bounded equal-per-slice-quota diagnostic

Raw evidence is preserved in:

- [`routing-contention-isolated-pass3-2026-10-15.tsv`](routing-contention-isolated-pass3-2026-10-15.tsv) — per-trial summary and route-validity flag.
- [`routing-contention-isolated-pass3-2026-10-15.samples.tsv`](routing-contention-isolated-pass3-2026-10-15.samples.tsv) — raw 20 ms probe samples.
- [`routing-contention-isolated-pass3-2026-10-15.environment.txt`](routing-contention-isolated-pass3-2026-10-15.environment.txt) — host, kernel, Python/systemd versions, CPU/cgroup details, settings, timestamps, and run status.
- [`routing-contention-isolated-pass3-2026-10-15.router.log`](routing-contention-isolated-pass3-2026-10-15.router.log) — route decision trace.

Environment: Ubuntu 26.04.1 LTS, kernel `7.0.0-38-generic`, x86_64, 8 logical CPUs, cgroup v2, Python 3.14.4, systemd 259.5. Three alternating paired trials; one CPU-bound Python worker; 20 seconds per mode; protected and heavy slices each capped at 100%; dynamic quota disabled for this routing-only comparison. Each mode/trial produced 999 probe samples. All three router trials were valid and moved the intended worker into the heavy test cgroup; route detection was 8.469–8.571 s (median 8.537 s). Baseline trials had 0/1 workers in heavy, as expected. This holds the per-slice ceilings equal, not the total hierarchy budget: baseline worker and probe share one 100% parent slice; router mode places them in separate 100% sibling slices, allowing up to 200% aggregate CPU. Treat the result as routing isolation, not an equal-total-capacity comparison.

| Metric (median of per-trial values) | Baseline | Router enabled |
|---|---:|---:|
| All-window wake-up lateness p50 | 0.090 ms | 0.087 ms |
| All-window wake-up lateness p95 | 0.459 ms | 0.288 ms |
| All-window wake-up lateness p99 | 3.519 ms | 2.250 ms |
| Steady-state wake-up lateness p95 | 0.456 ms | 0.220 ms |
| Steady-state wake-up lateness p99 | 4.520 ms | 2.250 ms |
| Steady-state probe-work duration p95 | 5.955 ms | 4.079 ms |

This is **one host and three pairs**, not repeated acceptance evidence across hosts or days. Per-pair steady p95 wake-up change ranged from 1.0% worse to 81.3% lower, and host PSI varied between windows; do not generalize these synthetic probe results to application responsiveness. This routing-only trial kept quotas fixed, so quota transitions/oscillation are not applicable. It did not measure aggregate application throughput or router CPU/RSS overhead; the controller integration smoke is separate evidence.

### Integrated controller diagnostic

The final local controller integration run passed with two workers actually routed and recovered, dynamic quota increased/decreased within the test bounds, AIMD remained log-only, the router restarted while both routes were active, and routing/recovery resumed after heavy-cgroup recreation. The bounded probe reported baseline/enabled wake-up lateness p50/p95/p99 of `0.100/0.150/4.800` and `0.105/0.356/2.119` ms; probe-work duration p50/p95/p99 was `1.362/17.798/18.571` and `1.319/1.978/5.135` ms. Aggregate two-worker Python-loop throughput was `56,473` baseline and `55,973` enabled iterations/s in this single run (no meaningful throughput conclusion). The router used `0.070 CPU-s` over `5.112 s` (~1.37% of one logical CPU); observed RSS was `18,500/18,516 KiB`, corrected peak `18,516 KiB`; router runqueue-delay delta was `32.627 ms`, with both cumulative-counter and PID-identity checks valid. Route detection was `0.799 s`, first quota increase `1.080 s`, and idle-to-quota-decrease/recovery `1.628 s`. Test-only PSI settings kept production `0.40` validation out of reach. These are regression diagnostics, not generalized performance claims.

## Verification

| Command/check | Result | Notes |
|---|---|---|
| `./test.sh` | PASS | Includes controller hardening, soak-monitor failure paths, routing benchmark safety, replay tests, syntax and unit checks. |
| `tests/routing-benchmark-safety.sh` | PASS | Under-length run rejected; existing output preserved; wrapper executable. |
| `tests/soak-monitor.sh` | PASS | Includes service restart, PID change without restart-counter increment, missing restart-counter baseline, counter reset, missed sampling deadline, journal capture failure, missing service telemetry, quota reversal, output permissions/no-overwrite, SIGTERM/incomplete. |
| `tests/install-uninstall.sh` | PASS | `systemctl` mocked; user config preservation checked. |
| `tests/integration-routing.sh` | PASS | Burst rejected; sustained route in 1.08 s. |
| `tests/recovery-routing.sh` | PASS | Idle-to-recovery in 1.84 s with accelerated test settings. |
| `tests/shadow-routing.sh` | PASS | Burst rejected; shadow route in 1.06 s; recovery in 3.35 s. |
| `tests/controller-integration.sh` | PASS | Two workers, quota increase/decrease, AIMD log-only, active-route router restart, recovery, cgroup recreation. |
| Python compile checks | PASS | Router, profile helper, all benchmark Python files, controller hardening tests. |
| POSIX shell syntax | PASS | Install/uninstall helpers, all test scripts, soak and benchmark safety scripts. |
| Bash syntax | PASS | Quota benchmark and isolated routing benchmark wrapper. |
| `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice` | PASS | Repository-supported unit paths. |
| `git diff --check` | PASS | No whitespace errors at verification time. |
| `./bin/workload-guard version` | PASS | `0.6.5-rc.1`. |
| Hosted CI for `5396a38` | PASS — [run 38677591211](https://github.com/nplongx/linux-workload-guard/actions/runs/38677591211) | Both Ubuntu 22.04/24.04 jobs passed all configured steps. Ubuntu 22.04 explicitly SKIPPED dynamic-quota integration because delegated cgroup CPU counters were unavailable; Ubuntu 24.04 executed and passed controller integration. The skip is not a pass. |

## Remaining release gates

| Gate | Status | Evidence needed |
|---|---|---|
| Production PSI high threshold `0.40` | **OPEN** | Safely capture timestamped host PSI at/above `0.40`, matching production controller decision log, bounded quota before/after, and unchanged production threshold. Deterministic tests now cover below/equal/above `0.40` and unavailable telemetry; they are not live acceptance. |
| Representative repeated performance | **OPEN** | Predefined acceptance limits, repeated valid multi-worker/representative workload comparisons, matched windows, worker placement verification, throughput/latency/PSI/scheduler/quota/recovery/CPU/RSS and failure counts. The new three-pair result is preliminary. |
| Ubuntu 22.04 dynamic quota | **OPEN / environment SKIP** | A runner/delegated cgroup exposing `usage_usec`, `nr_periods`, and `nr_throttled`, plus a real passing integration; otherwise document an explicit support limitation. Do not count SKIP as PASS. |
| Separate host | **OPEN** | Results from a genuinely separate host with kernel/cgroup/environment metadata. |
| RC3 soak | **OPEN** | At least 24 continuous hours, targeting 72 hours where practical; `run_status=COMPLETE`, full sample coverage, no missed intervals or missing metrics, no service restart/journal loss, plus workload phases, route validity, quota transitions, pressure, resource overhead and recovery evidence. No soak was started. |
| Lifecycle/rollback | **PARTIAL** | Active-route restart and cgroup recreation now covered in isolated integration. Still validate operator rollback from a known quota and representative upgrade/uninstall behavior across supported environments. |
| Release hygiene | **OPEN** | All applicable gates reviewed, CI green at candidate SHA, docs aligned, then a separate deliberate release decision. |

The Ubuntu 22.04 dynamic-quota integration skip remains explicitly distinct from Ubuntu 24.04's passing execution. No second-host evidence exists. Gradient2, PID control, weighted fairness, eBPF, and `sched_ext` remain research/proposed work, not production features. Dynamic quota remains opt-in; AIMD remains log-only.

Both hosted jobs emitted a non-failing GitHub Actions warning that `actions/checkout@v4` is being forced from Node.js 20 to Node.js 24. This did not affect CI results; update the action/runtime pin as separate CI maintenance.

No production configuration or threshold was changed, and no long soak was started. Keep `0.6.5-rc.1`; do not create a `v1.0.0` tag until all applicable acceptance gates are supported by reviewed evidence.
