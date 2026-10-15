# RC validation pass 16 — environment and gate readiness

Date: 2026-10-15
Base revision: `346186a271c2d8daa3f28fa1fdfed32836737406`
Version: `0.6.5-rc.1`

## Purpose

Continue from Pass 15 without rerunning or rewriting accepted historical evidence. This pass adds CI coverage for Python byte-compilation and patch whitespace, and a CPU-100% diagnostic runbook. It does not authorize controller-policy or production-quota changes.

## Environment findings

- The local checkout initially matched `origin/main` at the base revision.
- Existing benchmark history includes numerous untracked evidence files. They are historical inputs and must remain untouched.
- The production user service was observed active with `NRestarts=0`; `heavy-workload.slice` read back `CPUQuotaPerSecUSec=1s` (100%).
- Docker is present, but the inventory contained shared application/database containers and no usable Ubuntu 22.04/24.04 image. No container was started or altered for this task.
- No configured SSH host alias was found. This does not prove no independent host exists, but no authorized second host was discoverable from the current environment. Independent-host benchmark and dedicated-host rollback therefore remain `NOT RUN`.

## Evidence and decision rules

- Keep Pass 13/14/15 samples, evaluator output, corrected and superseded analysis, and incomplete observer evidence unchanged.
- Pass 15 remains `CONFOUNDED_INCONCLUSIVE`: all steady intervals exceeded the preregistered host PSI 5% confounding line. Threshold `0.90` is unchanged. No controller/routing policy change is justified.
- A valid independent-host repeat must use the existing Pass 15 protocol, fresh unique output paths, an independent host, identical workload/aggregate cap/window, ABBA order, synchronized cgroup and PSI telemetry, raw paired samples, and unchanged evaluator. Preflight failure means `NOT RUN`; post-start confounding means retain all samples and report `CONFOUNDED_INCONCLUSIVE`.
- Ubuntu 22.04 delegated telemetry must expose real `usage_usec`, `nr_periods`, and `nr_throttled` on the actual test cgroup. Missing keys remain `SKIP/OPEN`, never synthetic PASS.
- Rollback and soak must execute on an authorized dedicated test host. The soak requires at least 24 continuous hours (72-hour target), representative workload phases, full samples/journal, no critical telemetry gaps, and reviewed error/restart/quota-transition behavior.

## Gate status

| Gate | Status | Evidence / blocker |
|---|---|---|
| Local source/runtime preflight | PASS | Base revision, remote equality, production service and quota readback recorded during this pass |
| Independent-host ABBA repeat | NOT RUN | No authorized second host discovered; current host has confounded prior trials |
| Ubuntu 22.04 hosted CI | PASS | Run [38749178783](https://github.com/nplongx/linux-workload-guard/actions/runs/38749178783) passed; privileged delegated CPU telemetry explicitly SKIP because `usage_usec`, `nr_periods`, and `nr_throttled` were not all exposed |
| Ubuntu 22.04 delegated CPU telemetry | SKIP / OPEN | Requires runner/host with actual delegated CPU counters; CI skip is not a telemetry PASS |
| Ubuntu 24.04 compatibility | PASS | Run [38749178783](https://github.com/nplongx/linux-workload-guard/actions/runs/38749178783) passed all jobs |
| Dedicated-host operator rollback | NOT RUN / OPEN | No dedicated test host available in current inventory |
| Natural production PSI crossing at 0.40 | NOT RUN | Must occur naturally and be tied to the actual production controller decision; no forcing |
| RC soak 24h minimum / 72h target | NOT RUN | No approved persistent host/workload window in current environment |
| Aggregate throughput acceptance | OPEN; `CONFOUNDED_INCONCLUSIVE` | Pass 15 B4 failed; all steady samples confounded by host PSI |
| Release hygiene | OPEN | Mandatory gates remain open; no stable release/tag permitted |

## Local validation to attach at commit time

Local verification evidence is recorded under `benchmarks/results/pass16-local-validation-2026-10-15-r3/`; each check has a separate log and exit-status file. The integrity manifest for this report revision is `benchmarks/results/pass16-integrity-2026-10-15-r4.txt`. Hosted CI run `38749178783` passed on both Ubuntu 22.04 and 24.04 at commit `e977f17`; Ubuntu 22.04's privileged delegated-counter stage remains skipped, as documented above.

## Release decision

**NO-GO for stable release.** Keep `0.6.5-rc.1`. Do not create `v1.0.0`. No production configuration, quota, PSI threshold, evaluator threshold, or routing policy was changed by this pass.
