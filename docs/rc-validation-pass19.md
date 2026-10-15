# RC validation pass 19 — safe gate preflight and evidence provenance

Date: 2026-10-15 (UTC+07:00). Version remains `0.6.5-rc.1`.

## Baseline audit

- Repository started at `8b870b8d61352e6a8ede166cbc77443f790768ac`, with `origin/main` equal to HEAD and no tracked modifications. There are 136 untracked historical evidence/artifact entries; they were preserved and not included in cleanup or overwritten.
- Latest hosted CI before this pass was [run 38754112534](https://github.com/nplongx/linux-workload-guard/actions/runs/38754112534), conclusion `success`, commit `8b870b8d61352e6a8ede166cbc77443f790768ac`. Ubuntu 22.04 delegated CPU runtime integration remains `SKIP / OPEN`; the matrix success is not quota actuation proof.
- The current connected execution environment reports `workload-router.service` **inactive**, `MainPID=0`, `NRestarts=0`, and `heavy-workload.slice` quota property `infinity`. This differs from the Pass 18 host observation. It is recorded as an environment observation only: no service, slice, quota, or production configuration was started or changed. Do not infer production host state from this connected environment.
- cgroup v2 is present and the current process resides in a user app scope. Root or current-process counters do not prove CPU controller delegation or quota actuation/readback on the actual benchmark test slice.

## Implementation

- Added `benchmarks/rc-gate-preflight.sh`, a read-only gate preparation tool. It requires a new output directory inside the repository, refuses existing paths, records host/kernel/OS, source revision, cgroup mode/controllers, process cgroup, user systemd availability, router/service quota observations, host PSI and load average, and writes explicit gate classifications plus a SHA-256 manifest.
- The preflight labels actual delegated `cpu.stat` counters as **NOT PROVEN**, quota actuation/readback as **NOT RUN**, and keeps independent-host ABBA, rollback, natural PSI crossing and soak unrun. It cannot write quotas, start/stop services or generate workload, and explicitly warns that it is not acceptance evidence.
- Added `tests/rc-gate-preflight-safety.sh` to test invalid arguments, output-path boundary, no-overwrite, checksum verification, required non-PASS gate labels, and a static read-only guard. Integrated it into `./test.sh` and hosted CI as a dedicated step.

## Validation and evidence

- The preflight safety regression passed on this environment. Full local validation for the final revision is recorded in the new pass-19 evidence bundle.
- This preflight is only a repeatable preparation/provenance improvement; it is not a substitute for a privileged delegated runtime test, an independent host, a rollback drill, natural production PSI event, throughput acceptance, or a 24-hour/72-hour soak.

## Gate status (unchanged)

| Gate | Status | Required evidence to close |
|---|---|---|
| Local test/compile/syntax/systemd/evidence checks | To be recorded for final revision | Pass-19 bundle and hosted CI |
| Ubuntu 22.04 delegated CPU telemetry + quota actuation/readback | SKIP / OPEN | Real delegated test slice exposes `usage_usec`, `nr_periods`, `nr_throttled`; bounded quota write and effective readback verified |
| Ubuntu 24.04 compatibility | PASS on prior CI | New CI run confirms code compatibility only |
| Independent-host preregistered ABBA | NOT RUN | Authorized independent host, unchanged Pass 15 protocol, all pairs retained, PSI controls satisfied |
| Throughput acceptance | OPEN / `CONFOUNDED_INCONCLUSIVE`, threshold `0.90` | Preregistered valid pairs on suitably quiet host; no sample exclusions; all acceptance limits satisfied |
| Dedicated-host rollback | NOT RUN / OPEN | Approved host/window; capture and verify exact config, quota, service, route, ownership and permissions restoration |
| Natural production PSI crossing at `0.40` | NOT RUN | Passive natural crossing with actual production-controller decision evidence; never force the event |
| RC soak | NOT RUN | Continuous >=24 h (72 h target), COMPLETE monitor artifact, workload phase log, journal and integrity review |
| Stable release | NO-GO | Every mandatory gate proven; keep `0.6.5-rc.1`; no `v1.0.0` tag |

## Safety boundary

No production policy, CPU quota, routing threshold, controller default, release version, or tag was changed. No CPU-bound workload was launched. Historical untracked evidence was not modified.
