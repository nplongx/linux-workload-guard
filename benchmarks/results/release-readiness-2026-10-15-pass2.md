# Release-readiness assessment — 2026-10-15, hardening pass 2

## Decision

**Remain at `0.6.5-rc.1`. Do not tag or publish `v1.0.0`.** This pass closes code-level routing identity, numeric configuration, quota-boundary, and soak-observability defects. It does not close the live production PSI threshold, cross-host, representative performance, or RC3 soak gates.

## Reconciliation and live host state

- Repository branch: `main`; starting `HEAD` and `origin/main` both resolved to `1d29bb55503d622fa9e7594854ebe3723e831800` (`1d29bb5`, `docs: record release readiness CI results`).
- `VERSION`: `0.6.5-rc.1`; no `v1.0.0` tag.
- All 64 pre-existing untracked research/benchmark paths were preserved byte-for-byte by this pass. One new intended regression test, `tests/soak-monitor.sh`, is added; until committed it increases the visible untracked count from 64 to 65. No historical trial or smoke directory was removed, overwritten, or staged.
- The previous report's `CPUQuotaPerSecUSec=1s` value is confirmed by `systemctl --user show heavy-workload.slice`; a system-level `systemctl show heavy-workload.slice` query returns `infinity` because it addresses a different manager/unit context. The user service and its user slice are the relevant runtime.
- After the final local suite and monitor regression rerun: user `workload-router.service` is active/running, `MainPID=151705`, `NRestarts=0`, `ExecMainStatus=0`; user heavy slice quota is `1s` (100%). The service's effective configuration remains `WORKLOAD_GUARD_DYNAMIC_QUOTA=true`, `WORKLOAD_GUARD_AIMD_SHADOW=true`, `WORKLOAD_GUARD_SHADOW=true`, PSI high/low `0.40/0.10`, quota interval 5 s, bounds 100–400%, step 50%, dwell 10 s, scheduler high/low 20/5 ms. No live config or production threshold was changed.
- Host: Ubuntu 26.04.1 LTS, kernel `7.0.0-38-generic`, x86_64, 8 logical CPUs, cgroup v2, Python 3.14.4, systemd 259.5. Current PSI at final inspection: `some avg10=3.90`, `avg60=4.58`, `avg300=5.48`; `full` averages are zero. These are below 0.40 and do not constitute threshold validation.

## Defects corrected

1. **Route identity vs. command changes:** persisted route matching had required both starttime and command text. A legitimate `exec(2)` could therefore discard a route while leaving the process in the heavy cgroup. Recovery identity now uses kernel starttime; command changes reset hot/shadow classification samples but do not orphan a routed process. PID reuse remains rejected.
2. **Strict PSI input:** PSI parsing now requires one complete `some` row, required averages and cumulative counter, unique fields, finite in-range averages, and a valid non-negative total. Partial/duplicate observations are unavailable rather than treated as complete readings.
3. **Numeric loop/actuation safety:** core sample, statistics, history, and quota intervals have explicit minimums. Quota bounds/step must be whole percentage points because the actuator writes integer percentages. Decisions refuse to act from externally observed quota values outside configured bounds or with fractional precision the actuator cannot preserve.
4. **Soak evidence integrity:** the monitor uses private output permissions, refuses existing result directories, verifies routed records against `/proc` starttime and heavy-cgroup membership, and only computes scheduler-delay deltas for the same PID/starttime. It records sample count, expected count, estimated missed intervals, missing-metric/service failures, quota property transitions and direction reversals, service restart/PID changes, exit code, and `run_status=COMPLETE` only if duration and coverage checks pass. Graceful early termination is explicitly incomplete; missing final metadata after an uncatchable termination is also not acceptable evidence.

## Verification results

| Check | Result | Evidence / limitation |
|---|---|---|
| `./test.sh` | PASS | Controller hardening, replay checks, Python compile smoke, soak monitor failure-path tests |
| `./tests/install-uninstall.sh` | PASS | `systemctl` mocked; user config preservation checked |
| `./tests/integration-routing.sh` | PASS | 120 ms burst rejected; sustained route detected in 0.80 s |
| `./tests/recovery-routing.sh` | PASS | Idle-to-recovery in 1.85 s with accelerated test settings |
| `./tests/shadow-routing.sh` | PASS | Burst rejected; shadow route in 1.07 s; recovery sequence completed in 3.35 s |
| `./tests/controller-integration.sh` | PASS | Two workers routed; quota increase/decrease; AIMD log-only; both recovered; cgroup recreated and routing/recovery resumed |
| Python compile checks | PASS | Router, profile helper, all benchmark Python scripts, controller hardening tests |
| POSIX shell syntax | PASS | Install/uninstall/test helpers, all `tests/*.sh`, soak monitor, relevant benchmark scripts |
| Bash syntax | PASS | `benchmarks/quota-benchmark.sh` |
| `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice` | PASS | Repository unit paths |
| `git diff --check` | PASS | No whitespace errors |
| `./bin/workload-guard version` | PASS | Reports `0.6.5-rc.1` |
| `tests/soak-monitor.sh` | PASS | Invalid args, no-overwrite, 14-column TSV schema, private directory/file permissions, quota direction reversal accounting, missing service, SIGTERM/incomplete status |
| Hosted CI for `2306ab8` | PASS — [run 38673631025](https://github.com/nplongx/linux-workload-guard/actions/runs/38673631025) | Both Ubuntu 22.04/24.04 jobs passed. Ubuntu 22.04 explicitly SKIPPED dynamic-quota integration because delegated cgroup CPU counters were absent; Ubuntu 24.04 executed and passed the controller integration. The skip is not a pass. |

Hosted CI emitted a non-failing GitHub runner warning that `actions/checkout@v4` is being forced from Node.js 20 to Node.js 24. This did not affect either job's result; track the action's runtime support separately from controller acceptance.

The final controller integration output was a bounded diagnostic, not a performance claim: both workers routed in 0.806 s; first quota increase at 1.092 s; idle-to-decrease/recovery in 2.735 s; quota sequence `100→150→200→250→200→150→100%` (six transitions, one expected direction reversal between load and recovery); router CPU 0.070 s over 5.103 s (~1.37% of one logical CPU), peak RSS 18,500 KiB, router runqueue-delay delta 22.167 ms. Probe wake-up lateness p50/p95/p99 was 0.087/1.043/3.391 ms baseline and 0.087/0.744/1.749 ms enabled. Probe-work duration p50/p95/p99 was 1.606/18.918/22.283 ms baseline and 1.324/2.320/9.753 ms enabled. Aggregate Python-loop throughput was 36,583 iterations/s baseline and 55,763 enabled in this run; other immediate runs differed, so neither throughput nor tail-latency movement is generalized. Test-only thresholds kept the production PSI-high branch out of reach. This terminal-only rerun did not preserve another raw result directory; the existing raw repeated trials remain the primary evidence archive.

## Benchmark methodology audit

- The preserved `controller-hardening-smoke-2026-10-14-run7`, `run8`, and `run9` logs each show two distinct `route decision=accepted` PIDs, both workers recovered, six quota transitions, three AIMD log-only proposals, and a subsequent route/recovery cycle after heavy-cgroup recreation. The integration script checks actual worker membership in the temporary heavy cgroup before reporting both workers routed. PSI high/low overrides were `1.00/0.99` and scheduler-delay thresholds `100000/99999 ms`; these runs deliberately do **not** validate production PSI `0.40`.
- `routing-contention-isolated-1worker-5rep-2026-10-09.tsv` has five router trials with `trial_valid=1`, `workers_in_heavy=1/1`, and five baseline trials with no route. The paired method isolates the router from the baseline and uses a comparable late/steady window (baseline after 12 s; router after observed route plus 2 s). Median wake-up p95/p99 were 0.179/2.198 ms baseline and 0.118/0.984 ms routed; probe-work p95 medians were 6.829/3.342 ms. This remains synthetic, single-host evidence: host load differs across windows and there is no general application SLA inference.
- `aimd-shadow-paired-3rep-2026-10-09.tsv` records three single-worker router trials with one worker in the heavy cgroup and three baseline trials; it is not multi-worker evidence and does not demonstrate AIMD actuation (AIMD remains log-only).
- Earlier 2-worker and 4-worker router trials that failed to move all intended workers remain invalid for router-on/router-off comparisons. Their raw data is retained and excluded from performance claims.
- No new load benchmark was added solely to generate more short samples. The existing valid repetitions were reused; the fresh integration run above is a regression smoke, not an acceptance benchmark.

## Remaining release gates and evidence needed

| Gate | Status | Exact evidence needed to close |
|---|---|---|
| Production PSI-high threshold `0.40` | **OPEN** | A safe provisioned/isolated host or approved operating window with raw PSI samples at/above `0.40` at the controller decision timestamp, the matching decision log, bounded quota before/after, and proof the production threshold was unchanged. Deterministic exact-boundary/just-below/unavailable-signal tests are not live acceptance. |
| Representative performance | **OPEN** | Predefined representative workloads and acceptance limits; repeated paired baseline/enabled trials; every intended worker verified in the target cgroup; matching measurement windows; p50/p95/p99, throughput, PSI, quota transitions, detection/recovery, CPU/RSS and failures. Keep single-host synthetic results diagnostic. |
| Ubuntu 22.04 dynamic quota | **OPEN / environment SKIP** | Runner/delegation exposing `usage_usec`, `nr_periods`, `nr_throttled` and allowing isolated CPUQuota changes, followed by a real passing controller integration. Otherwise document the supported-environment limitation explicitly; do not count SKIP as PASS. Ubuntu 24.04 CI did execute this path in the prior run. |
| Separate second host | **OPEN** | Results collected on a genuinely separate host with environment metadata; same-host test slices and hosted CI matrix do not satisfy this gate. |
| RC3 soak | **OPEN** | At least 24 continuous hours (target 72 hours where practical), `run_status=COMPLETE`, full expected sample coverage, no unaccounted gaps/missing metrics, start/end timestamps, service errors/restarts, workload phase and route validity, quota transitions/oscillation, PSI/scheduler telemetry, CPU/RSS, and observed recovery events. |
| Lifecycle and rollback acceptance | **PARTIAL** | Existing tests cover service timeout, cgroup disappearance/recreation, recovery leaf, install/uninstall, and quota restoration tooling. Still review restart during active routed workloads, operator rollback from a known quota, and representative upgrade/uninstall behavior on each supported environment. |
| Release hygiene / v1.0.0 | **OPEN** | Close or formally scope all above gates, review final diff and CI for exact commit, finalize release notes, then make a separate deliberate version/tag decision. |

## Soak execution procedure

1. Choose a host and safe window; verify user `workload-router.service` is active and healthy, record its effective configuration/quota, and approve the workload phases. Do not change production thresholds to force a result.
2. From the repository directory, run the monitor as the same user via a transient user service so terminal disconnect does not end collection:

   ```sh
   SOAK_UNIT="lwg-soak-$(date +%Y%m%d-%H%M%S)"
   SOAK_DIR="$HOME/$SOAK_UNIT"
   systemd-run --user --unit="$SOAK_UNIT" --property=Type=exec --property=Restart=no \
     "$PWD/benchmarks/soak-monitor.sh" "$SOAK_DIR" 86400 60
   systemctl --user status "$SOAK_UNIT.service"
   ```

3. Follow it with `journalctl --user -u "$SOAK_UNIT.service" -f`. Stop early only with `systemctl --user stop "$SOAK_UNIT.service"`; an early stop is deliberately `INCOMPLETE` and must not count toward RC3.
4. Accept only if `environment.txt` says `run_status=COMPLETE`, actual duration/sample coverage meet the requested window, and there are no unreviewed errors/gaps. Review `errors.tsv`, `samples.tsv`, and `service-journal.log` together with workload-phase and quota/recovery logs. The monitor generates no load and cannot by itself establish workload representativeness.

No soak was started in this pass. `VERSION` remains `0.6.5-rc.1`; no release tag was created.
