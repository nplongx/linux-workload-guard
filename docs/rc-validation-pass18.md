# RC validation pass 18 — fail-closed soak verdicts and runtime gate visibility

Date: 2026-10-15 (local time, UTC+07:00)
Base revision: `ec8f05ada0b4c3fb881d47f55a171da3991dd583`
Version remains `0.6.5-rc.1`.

## Baseline audit

- Initial `HEAD` and `origin/main` both pointed to `ec8f05a`; tracked working tree was clean. The existing untracked benchmark/evidence corpus was inventoried and preserved.
- Hosted CI runs `38751816666` (`ec8f05a`) and `38751502735` (`51da479`) both concluded success on Ubuntu 22.04 and 24.04. The Ubuntu 22.04 job explicitly skipped live delegated CPU-controller integration because its test cgroup lacked `nr_periods` and `nr_throttled`; that skip does not become a PASS merely because the compatibility job is green.
- The local host is Ubuntu 26.04.1 with cgroup v2. Read-only production audit: `workload-router.service` active/running, MainPID `358400`, `NRestarts=0`; `heavy-workload.slice` quota property `1s` (100%). No production configuration, controller policy, threshold, or quota was changed.
- A read-only root-cgroup preflight found `usage_usec`, `nr_periods`, and `nr_throttled`, and the user systemd manager is available. This is **not** a delegated test-slice quota-actuation result and does not close the Ubuntu 22.04 runtime gate.
- Host discovery results from Pass 17 remain applicable: no configured SSH aliases or registered self-hosted runner; two previously known private LAN targets were unavailable; provisioning CLI/verified cloud authorization was unavailable. No shared container or external infrastructure was altered.

## New implementation and regression

- Fixed a false-success contract in `benchmarks/soak-monitor.sh`: after writing an `INCOMPLETE` verdict due to missing metrics, restart/PID discontinuity, deadline miss, clock issue, or journal failure, the process now exits nonzero instead of returning success. This prevents orchestration/CI from treating a failed soak as successful based on exit code alone.
- Removed the unconditional “soak monitor completed” message, which was misleading even when the final artifact was incomplete or the loop exited early. The message now directs operators to the recorded acceptance verdict.
- Updated `tests/soak-monitor.sh` to require exit status 1 for all simulated incomplete full-duration runs, while retaining the existing successful-completion and SIGTERM (`143`) assertions. Cases include restart, PID change, counter reset/unknown baseline, missed deadlines, failed journal, inactive service, slow final sample, monotonic-clock loss, and wall-clock discontinuity.
- Controller integration now writes a GitHub step summary explicitly labeled `PASS` or `SKIP / OPEN`, including the skip reason, kernel, cgroup mode and available controllers. The CI step is named “Delegated CPU runtime gate (SKIP is not PASS)”. A capability skip remains a successful compatibility job unless strict mode is requested, but is explicitly visible in the summary and never represented as runtime success.
- CI now exposes a dedicated “Soak monitor fail-closed acceptance regression” step, in addition to the full test suite.

## Validation

The current implementation passed `./test.sh`, Python byte-compilation, shell and Bash syntax checks, `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice`, telemetry parser, aggregate integrity, soak fail-closed regression, version smoke, and `git diff --check`. The soak regression specifically proves incomplete outcomes exit 1. Full command logs and SHA-256 manifest are stored in `benchmarks/results/pass18-validation-2026-10-15-r1/`; each command has a timestamped status record, and the bundle records OS/kernel/systemd/Python/cgroup mode, source commit, and final revision. `actionlint` is not installed in the local environment; workflow syntax/behavior is therefore subject to the post-push GitHub Actions result.

The live controller integration was not forced on this development host: it can create CPU-bound workers, and the host is not an approved dedicated benchmark machine. Hosted CI run 38753681218 passed both OS jobs but explicitly skipped the Ubuntu 22.04 delegated-counter integration; the independent-host ABBA/rollback/soak gates remain open.

## Gate status

| Gate | Status | Closure condition |
|---|---|---|
| Local tests, compile, syntax, systemd, diff | PASS | Final commit evidence bundle and CI run |
| Ubuntu 22.04/24.04 compatibility | PASS — [run 38753681218](https://github.com/nplongx/linux-workload-guard/actions/runs/38753681218), commit `5e7375d7c108a676eb11dbeea80203318b27baa4` | Both matrix jobs passed, including the dedicated soak fail-closed step and checkout v5 |
| Ubuntu 22.04 delegated CPU telemetry | SKIP / OPEN — same run, job annotation | Test cgroup missing `nr_periods` and `nr_throttled`; available keys `usage_usec,user_usec,system_usec,core_sched.force_idle_usec`. Requires a runner with real delegated counters and successful quota actuation/readback |
| Ubuntu 24.04 compatibility | PASS — same run | All CI steps passed; compatibility is not independent-host or soak evidence |
| Independent-host preregistered ABBA | NOT RUN | Approved independent host; unchanged Pass 15 protocol, all pairs and host-PSI controls retained |
| Throughput acceptance | OPEN / `CONFOUNDED_INCONCLUSIVE` | Threshold `0.90`; no sample exclusion; quieter host and preregistered paired replication required |
| Dedicated-host rollback | NOT RUN / OPEN | Authorized test host and maintenance window; exact config/mode/ownership/quota/service/route restoration verified |
| Natural production PSI crossing at `0.40` | NOT RUN | Natural event captured with actual controller decision; no forced load/threshold change |
| Continuous RC soak | NOT RUN | Approved workload host; >=24 hours (72-hour target), COMPLETE artifact, zero continuity failures and reviewed workload-phase evidence |
| Stable release | NO-GO | Keep `0.6.5-rc.1`; no `v1.0.0` tag |

## Safety boundary

The code change affects only soak reporting/exit semantics and CI reporting. Production routing policy, dynamic quota settings, `0.40` PSI threshold, `0.90` throughput threshold, and production quota remain unchanged. A successful CI matrix remains compatibility evidence, not proof of an independent host, delegated runtime counters, rollback drill, natural PSI event, performance acceptance, or a 24/72-hour soak.
