# RC validation pass 20 — runtime scope correctness and acceptance hardening

Date: 2026-10-15. Version remains `0.6.5-rc.1`.

## Baseline audit

- Starting revision: `35059998158fefe2090f179313e776d47954234f`; `origin/main` matched HEAD and the tracked working tree was clean. Existing untracked historical evidence was preserved; none was cleaned or staged.
- Pass 19 evidence bundle `benchmarks/results/pass19-validation-2026-10-15-r1/` was re-verified with `sha256sum -c SHA256SUMS.txt`; all listed checksums passed. The recorded local checks all have exit status 0.
- Hosted CI at Pass 19 revision passed both Ubuntu 22.04 and Ubuntu 24.04: [run 38759440965](https://github.com/nplongx/linux-workload-guard/actions/runs/38759440965). Ubuntu 22.04 delegated CPU telemetry remains `SKIP / OPEN` because its test cgroup lacks required `nr_periods` and `nr_throttled` counters.
- **Pass 19 runtime observation scope issue found:** its preflight used system-manager `systemctl show` for `workload-router.service` and `heavy-workload.slice`, while this project uses user-manager units. The old snapshot (`inactive`, quota `infinity`) described system-scope units and must not be interpreted as the user production service/slice. Historical files remain unchanged. Pass 20 corrects the preflight to record user scope as primary and system scope separately.
- Correctly scoped read-only observations at Pass 20 baseline: `systemctl --user show workload-router.service` reports active/running, `MainPID=358400`, `NRestarts=0`, `ExecMainStatus=0`; `systemctl --user show heavy-workload.slice` reports `CPUQuotaPerSecUSec=1s` (100%). System-scope units with the same names are inactive/unlimited. No service lifecycle operation, quota write, production configuration change, or workload generation was performed.

## Changes and validation

- The router now distinguishes `gone`, `outside`, `inside`, and `unknown` cgroup membership. An unreadable `/proc/<pid>/cgroup` or permission error no longer drops a persisted route or starts idle recovery. It clears the sample baseline and recovery dwell until membership can be established again.
- The read-only preflight records user-manager service/slice state as the primary runtime observation and same-named system-manager units separately. Regression tests verify both scopes are recorded; no lifecycle or quota operation is invoked.
- The benchmark evaluator now rejects missing/malformed source provenance for telemetry-backed acceptance: full 40-hex commit, clean tracked source tree, and 64-hex SHA-256 of the tracked-source diff are required. It also rejects out-of-order telemetry and per-worker throughput timestamps.
- The benchmark manifest's untracked-artifact count now uses a separate read-only `git ls-files --others --exclude-standard` query; the previous `git status --untracked-files=no` invocation could never count untracked files. A temporary isolated Git repository verifies clean/dirty state, diff hash and untracked count.
- Added `tests/controller-integration-safety.sh` to prove that missing delegated-runtime capability is reported as `SKIP / OPEN` in compatibility mode and becomes a nonzero failure when `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1`. This is a synthetic skip-semantics test, not live runtime evidence.
- The evaluator applies the existing preregistered PSI noise rule without excluding samples: a mode/pair with more than 20% of steady-window samples above 5% host CPU PSI, or a baseline/router condition median imbalance above 5 percentage points, yields `CONFOUNDED_INCONCLUSIVE` (exit status 3). Missing/malformed telemetry remains `INCOMPLETE`; measured performance criteria remain visible and the throughput threshold stays 0.90.
- Deterministic regression fixtures cover clean vs dirty source provenance, quiet vs PSI-confounded paired runs, unordered raw probe samples, unordered interval telemetry, and unordered cumulative throughput counters. Local full validation and hosted CI results are recorded in the Pass 20 evidence bundle below.

## Release gates

| Gate | Status | Closure condition |
|---|---|---|
| Local deterministic tests / static checks | PASS | `./test.sh`, Python compile, shell/Bash syntax, systemd verify, version smoke, `git diff --check`; Pass 19 SHA-256 manifest re-verified |
| Ubuntu 22.04/24.04 compatibility CI | PENDING | Hosted CI for final commit |
| Delegated CPU telemetry + quota actuation/readback | SKIP / OPEN | Real delegated test cgroup exposes required counters; bounded write and effective readback proven |
| Independent-host preregistered ABBA | NOT RUN | Authorized independent host; all paired samples retained; PSI controls satisfied |
| Throughput acceptance | OPEN / CONFOUNDED_INCONCLUSIVE | Threshold remains `0.90`; valid pairs and preregistered noise limits required |
| Dedicated-host rollback | NOT RUN / OPEN | Approved host/window and exact restoration proof |
| Natural production PSI crossing at `0.40` | NOT RUN | Passive observation of a genuine crossing and corresponding controller decision |
| Continuous RC soak | NOT RUN | Minimum 24 h, target 72 h, COMPLETE artifact and reviewed telemetry/journal |
| Stable release | NO-GO | Every mandatory gate has valid evidence; no `v1.0.0` tag until then |

No host-dependent gate may be inferred from a green compatibility matrix, synthetic replay, root-cgroup counters, or a read-only preflight.
