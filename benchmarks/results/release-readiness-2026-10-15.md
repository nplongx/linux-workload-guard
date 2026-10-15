# Release-readiness assessment — 2026-10-15

## Decision

**Remain on 0.6.5-rc.1; do not create or publish v1.0.0.** This pass closes a configuration-validation defect and prepares a monitor-only soak collector. RC2/RC3/stable acceptance remains incomplete.

## Reconciled repository and runtime state

- Branch: main
- HEAD and origin/main before this pass: a163ca3ae1cff7d66505baf94060b39b03f8c16d (docs: correct controller RSS unit conversion)
- Version: 0.6.5-rc.1
- Initial working tree: no tracked modifications; 64 pre-existing untracked research/benchmark paths, preserved.
- User service before testing: active/running, MainPID 3294, NRestarts=0.
- Dynamic quota setting in the existing user configuration: WORKLOAD_GUARD_DYNAMIC_QUOTA=true; AIMD remains shadow/log-only.
- Host at inspection: Ubuntu 26.04.1 LTS, kernel 7.0.0-38-generic, x86_64, 8 logical CPUs, cgroup v2, Python 3.14.4, systemd 259.5.
- Observed CPU PSI before testing was below the production 0.40 high threshold. No attempt was made to induce host-wide pressure.

## Changes in this pass

- Core routing numeric environment settings previously used direct float()/int() conversion at module import. Malformed values could crash startup before safety validation. They now become explicit invalid sentinels; routing_config_error() rejects non-finite values, unsafe sample intervals, thresholds, counts, and negative dwell/cooldown. main() logs the reason and exits before unit startup or routing when core configuration is invalid.
- Added deterministic cases for malformed, non-finite, zero, negative, and out-of-range core settings. Each verifies that the main path rejects configuration before attempting unit startup.
- Added benchmarks/soak-monitor.sh. It creates a new output directory only, samples service state/PID/restarts/exit status, host CPU PSI, quota property, router CPU/RSS/runqueue-delay delta, route-state count, and captures the user service journal. It does not start workloads, restart the service, or write configuration/quota.
- Added the monitor's shell syntax check to local and hosted CI checks.

## Verification results

| Check | Result | Notes |
|---|---|---|
| ./test.sh | PASS | Includes controller hardening, malformed core configuration, quota/recovery sequence, replay checks, and Python compilation |
| ./tests/install-uninstall.sh | PASS | systemctl mocked; user configuration preserved |
| ./tests/integration-routing.sh | PASS | 120 ms burst rejected; sustained route detected in 0.81 s |
| ./tests/recovery-routing.sh | PASS | Idle-to-recovery observed in 1.83 s |
| ./tests/shadow-routing.sh | PASS | Burst rejected; sustained shadow route in 1.05 s; recovery in 3.62 s |
| ./tests/controller-integration.sh | PASS | Both competing workers routed; isolated quota increase/decrease; AIMD log-only; cgroup recreation and recovery |
| Python compile checks | PASS | Router, profile, replay/benchmark scripts, controller regression |
| POSIX shell syntax | PASS | Includes benchmarks/soak-monitor.sh |
| Bash syntax | PASS | benchmarks/quota-benchmark.sh |
| systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice | PASS | |
| git diff --check | PASS | |
| Soak monitor smoke | PASS | Two-second monitor-only check; expected schema/metrics present. Not a soak. |
| Hosted CI run 38671055102 | PASS | Ubuntu 22.04 and 24.04 jobs succeeded. Ubuntu 22.04 explicitly skipped dynamic-quota integration for missing cgroup CPU counters; Ubuntu 24.04 executed and passed it. |

After the first suite, workload-router.service was active/running with NRestarts=0 and PID 88584. After the final repeated suite, it remained active/running with NRestarts=0 and PID 96788. Integration scripts intentionally stop/start it to isolate routing tests; no service crash/restart was observed. The live heavy-slice CPUQuotaPerSecUSec property remained 1s (100%). No live configuration or production threshold was edited.

### Bounded controller integration diagnostic

One additional bounded test-only integration run produced:

- Both workers routed: 0.809 s.
- First isolated quota increase: 1.110 s.
- Idle-to-quota-decrease/recovery: 2.388 s.
- Quota sequence: 100→150→200→250→200→150→100%; one direction reversal across six transitions.
- Router CPU: 0.080 CPU-seconds over 5.247 s, approximately 1.52% of one logical CPU.
- Router RSS: 18,316 KiB peak in the measured window.
- Router-process runqueue-delay delta: 55.489 ms; this is not system-wide scheduler delay.
- Probe wakeup lateness p50/p95/p99: baseline 0.094/2.323/17.394 ms, enabled 0.093/1.321/3.375 ms.
- Probe work duration p50/p95/p99: baseline 1.491/16.712/17.716 ms, enabled 1.511/2.502/9.287 ms.
- Two-worker aggregate Python-loop iterations/s: baseline 40,806.562, enabled 43,498.758.
- Host CPU PSI some avg10 at window endpoints: baseline 6.12%/9.98%, enabled 10.53%/12.02%.

These are one short synthetic diagnostic run on one host, with test-only quota pressure thresholds. They are not statistically sufficient for a performance claim, and did not exercise the production PSI 0.40 decision boundary. Keep the existing three-run curated smoke report and all preliminary raw trials; do not infer generalized improvement from this run.

A final full-suite rerun also passed the same isolated controller integration. Its diagnostic values were: both workers routed in 0.791 s; first quota increase in 1.070 s; idle-to-decrease/recovery in 2.379 s; quota sequence again 100→150→200→250→200→150→100%; router CPU 0.060 CPU-seconds over 5.101 s (about 1.18% of one logical CPU); peak RSS 18,220 KiB; router-process runqueue-delay delta 27.258 ms; baseline/enabled probe wakeup lateness p50/p95/p99 0.100/0.369/18.862 and 0.091/0.253/1.596 ms; aggregate Python-loop throughput 56,314 and 62,987 iterations/s; PSI endpoint avg10 3.54%/7.25% baseline and 7.38%/8.47% enabled. These repeat measurements vary with host load and remain diagnostic only.

## Existing evidence validity

- The final controller integration verifies both intended worker PIDs are present in the isolated heavy cgroup before accepting the multiworker scenario. Its per-worker throughput files contain baseline_hot, enabled_hot, and recreated_hot phases.
- Earlier historical 2-worker and 4-worker benchmark trials that did not route their intended workers remain invalid as router-on/router-off comparisons, as documented in ROADMAP.md; they are preserved.
- Three prior successful controller smoke runs use test-only PSI thresholds 0.99/1.00, deliberately keeping the high-pressure branch out of reach. They do not validate production PSI 0.40.
- The two-second soak-monitor smoke validates only the collector's mechanics and output schema. No continuous soak was started.

## Remaining release blockers

1. **Production PSI 0.40 live validation:** no safe live decision trace with host CPU PSI at/above 0.40 and a corresponding bounded quota decrease has been recorded. Needed: a provisioned/isolated host or approved bounded operating window, raw PSI/controller log timestamps, quota before/after, and confirmation that the production threshold was unchanged.
2. **Representative repeated performance comparisons:** existing short synthetic runs are diagnostic. Needed: repeated paired runs with actual worker route validity, representative routed workloads, p50/p95/p99, throughput, quota transitions, detection/recovery, CPU/RSS, PSI, and limitations.
3. **Cross-environment evidence:** no separate second-host run is recorded. Ubuntu 22.04 hosted CI explicitly skips dynamic-quota integration because delegated CPU cgroup counters are unavailable; Ubuntu 24.04 executed and passed it. Needed: capable Ubuntu 22.04 delegation/runner or an explicit supported-environment limitation.
4. **RC3 soak:** no 24-hour soak (72-hour target where practical). Needed: uninterrupted timestamps, environment, service journal/errors/restarts, pressure, quota transitions/oscillation, workload phases and route validity, resource overhead, and recovery events.
5. **Release hygiene:** hosted CI passed on commit 2bd431f in [run 38671055102](https://github.com/nplongx/linux-workload-guard/actions/runs/38671055102). Ubuntu 22.04's dynamic-quota stage was explicitly skipped for missing required cgroup CPU counters, not functionally passed. Review all roadmap gates again before any version/tag decision.

No release tag was created. VERSION remains 0.6.5-rc.1.
