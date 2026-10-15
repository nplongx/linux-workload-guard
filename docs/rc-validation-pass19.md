# RC validation pass 19 — safe gate preflight and evidence provenance

Date: 2026-10-15 (UTC+07:00). Version remains `0.6.5-rc.1`.

## Baseline audit

- Repository started at `8b870b8d61352e6a8ede166cbc77443f790768ac`, with `origin/main` equal to HEAD and no tracked modifications. There are 136 untracked historical evidence/artifact entries; they were preserved and not included in cleanup or overwritten.
- Hosted CI [run 38758494375](https://github.com/nplongx/linux-workload-guard/actions/runs/38758494375) passed both Ubuntu 22.04 and Ubuntu 24.04 at `f957bfbe4e234bc6f40295370b312f3c50d5cbb8`; the new preflight safety step passed in both jobs. Ubuntu 22.04 delegated CPU runtime integration remains `SKIP / OPEN`, so the matrix result is compatibility evidence only.
- The current connected execution environment reports `workload-router.service` **inactive**, `MainPID=0`, `NRestarts=0`, and `heavy-workload.slice` quota property `infinity`. This differs from the Pass 18 host observation. It is recorded as an environment observation only: no service, slice, quota, or production configuration was started or changed. Do not infer production host state from this connected environment.
- cgroup v2 is present and the current process resides in a user app scope. Root or current-process counters do not prove CPU controller delegation or quota actuation/readback on the actual benchmark test slice.

## Implementation

- Added `benchmarks/rc-gate-preflight.sh`, a read-only gate preparation tool. It requires a new output directory inside the repository, refuses existing paths, records host/kernel/OS, source revision, cgroup mode/controllers, process cgroup, user systemd availability, router/service quota observations, host PSI and load average, and writes explicit gate classifications plus a SHA-256 manifest.
- The preflight labels actual delegated `cpu.stat` counters as **NOT PROVEN**, quota actuation/readback as **NOT RUN**, and keeps independent-host ABBA, rollback, natural PSI crossing and soak unrun. It cannot write quotas, start/stop services or generate workload, and explicitly warns that it is not acceptance evidence.
- Added `tests/rc-gate-preflight-safety.sh` to test invalid arguments, output-path boundary, no-overwrite, checksum verification, required non-PASS gate labels, and a static read-only guard. Integrated it into `./test.sh` and hosted CI as a dedicated step.

## Validation and evidence

- The full local test suite, Python compilation, shell/Bash syntax, systemd verification, strict telemetry parser, aggregate evidence regression, soak fail-closed regression, preflight safety regression, version smoke and whitespace checks all passed. Command/status/log records and SHA-256 manifest are in [`benchmarks/results/pass19-validation-2026-10-15-r1/`](../benchmarks/results/pass19-validation-2026-10-15-r1/).
- This preflight is only a repeatable preparation/provenance improvement; it is not a substitute for a privileged delegated runtime test, an independent host, a rollback drill, natural production PSI event, throughput acceptance, or a 24-hour/72-hour soak.

## Gate status (unchanged)

| Gate | Status | Required evidence to close |
|---|---|---|
| Local test/compile/syntax/systemd/evidence checks | PASS — `pass19-validation-2026-10-15-r1` | Final bundle SHA-256 verification |
| Ubuntu 22.04 delegated CPU telemetry + quota actuation/readback | SKIP / OPEN | Real delegated test slice exposes `usage_usec`, `nr_periods`, `nr_throttled`; bounded quota write and effective readback verified |
| Ubuntu 24.04 compatibility | PASS — [run 38758494375](https://github.com/nplongx/linux-workload-guard/actions/runs/38758494375) | Compatibility only, not independent-host evidence |
| Independent-host preregistered ABBA | NOT RUN | Authorized independent host, unchanged Pass 15 protocol, all pairs retained, PSI controls satisfied |
| Throughput acceptance | OPEN / `CONFOUNDED_INCONCLUSIVE`, threshold `0.90` | Preregistered valid pairs on suitably quiet host; no sample exclusions; all acceptance limits satisfied |
| Dedicated-host rollback | NOT RUN / OPEN | Approved host/window; capture and verify exact config, quota, service, route, ownership and permissions restoration |
| Natural production PSI crossing at `0.40` | NOT RUN | Passive natural crossing with actual production-controller decision evidence; never force the event |
| RC soak | NOT RUN | Continuous >=24 h (72 h target), COMPLETE monitor artifact, workload phase log, journal and integrity review |
| Stable release | NO-GO | Every mandatory gate proven; keep `0.6.5-rc.1`; no `v1.0.0` tag |

## Safety boundary

No production policy, CPU quota, routing threshold, controller default, release version, or tag was changed. No CPU-bound workload was launched. Historical untracked evidence was not modified.
