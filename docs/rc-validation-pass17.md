# RC validation pass 17 — telemetry integrity and CI hardening

Date: 2026-10-15 (local time, UTC+07:00)
Base revision: `5f3ac084f387595cd7fc0f23c03dafe470544895`
Version: `0.6.5-rc.1`

## Implementation

- The isolated routing benchmark's `cpu.stat` reader now rejects partial snapshots, malformed rows, duplicate keys, negative/non-decimal counters, and impossible `nr_throttled > nr_periods` snapshots. It requires real `usage_usec`, `nr_periods`, and `nr_throttled` before recording a usable row. Missing delegated counters are represented as unavailable; they are not zero-filled.
- cgroup PSI parsing now requires a complete, valid `some` row and rejects duplicate/unknown/malformed fields, non-finite values, negative totals, and averages outside 0–100. Host PSI likewise returns unavailable for incomplete or malformed observations. The optional `full` row is not required because kernel/cgroup availability can vary.
- Regression tests cover valid counters/PSI plus missing counters, malformed/duplicate/negative counters, impossible throttling, incomplete/duplicate/non-finite/out-of-range PSI, and absent files.
- CI uses `actions/checkout@v5`, which uses Node.js 24 and requires runner `v2.327.1` or newer. GitHub-hosted Ubuntu runners satisfy the supported runner baseline; the push CI result below is the compatibility verification for this repository workflow.
- CI explicitly runs the strict telemetry parser regressions and aggregate-evidence analyzer regressions in addition to `./test.sh`, Python compile, systemd verification, shell syntax and integrations.

## Environment and host discovery

- Checkout started at `5f3ac084f387595cd7fc0f23c03dafe470544895`, equal to `origin/main`; no existing untracked benchmark artifact was overwritten or deleted.
- Local machine is Ubuntu 26.04.1, kernel `7.0.0-38-generic`, cgroup v2. The installed `workload-router.service` was read-only checked as active/running with `NRestarts=0`; `heavy-workload.slice` remained at `CPUQuotaPerSecUSec=1s` (100%). Version remains `0.6.5-rc.1`.
- `gh` is authenticated, but the repository has no registered self-hosted Actions runner or configured repository environment. No SSH config/host aliases exist. The two private-LAN addresses present in `known_hosts` were tested read-only: `192.168.1.3` timed out and `192.168.1.9` refused port 22. Neither was accessed.
- A Google Cloud config directory exists, but `gcloud` and other cloud provisioning CLIs are unavailable. No cloud VM was provisioned: there is no verified project/quota/instance authorization in the active toolchain, and provisioning could incur cost or alter external infrastructure. Existing Docker containers are shared services; no container was started or changed. A container would not satisfy the independent-host gate in any case.
- The host's root `cpu.stat` exposes the required field names but has `nr_periods=0`; that is not evidence of a delegated test cgroup with working quota/throttling counters. Ubuntu 22.04 hosted-runner limitation remains open.

## Validation evidence

Primary local evidence bundle: [`benchmarks/results/pass17-validation-2026-10-15-r3/`](../benchmarks/results/pass17-validation-2026-10-15-r3/). It records command text, start/finish timestamps, exit status, logs, environment and `SHA256SUMS.txt`. Manifest verification completed successfully. All checks below exited 0:

| Gate | Status | Artifact |
|---|---|---|
| Full `./test.sh` | PASS | `test-suite.log` / `test-suite.status.txt` |
| Python byte compilation | PASS | `python-compile.log` / status |
| POSIX shell syntax | PASS | `shell-syntax.log` / status |
| Bash syntax | PASS | `bash-syntax.log` / status |
| `systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice` | PASS | `systemd-verify.log` / status |
| Strict benchmark telemetry regression | PASS | `strict-telemetry.log` / status |
| Aggregate evidence analyzer regression | PASS | `benchmark-integrity.log` / status |
| `git diff --check` | PASS | `diff-check.log` / status |
| Version smoke | PASS (`0.6.5-rc.1`) | `version-smoke.log` / status |
| SHA-256 manifest self-check | PASS | command output retained in task log; manifest at `SHA256SUMS.txt` |

The first evidence attempt (`r1`) called a nonexistent `systemd` executable for the environment-version field; its blank field is retained and not represented as a verified systemd version. The `r2` bundle passed all checks but predates the final host-PSI duplicate-row guard. Both are preserved; `r3` is the authoritative final-code validation bundle.

## Release gates

| Gate | Status | Blocker / closure condition |
|---|---|---|
| Ubuntu 22.04/24.04 hosted CI | Pending after this commit until push run finishes | Both matrix jobs must finish successfully; a capability skip is not a runtime PASS |
| Ubuntu 22.04 delegated CPU telemetry | SKIP / OPEN | Need a real test cgroup exposing valid `usage_usec`, `nr_periods`, and `nr_throttled`, with quota actuation/readback on the target runner |
| Ubuntu 24.04 compatibility | Prior PASS; revalidate on this change | Current workflow matrix must pass |
| Independent-host preregistered ABBA | NOT RUN | No authorized reachable independent host found; use Pass 15 protocol unchanged and a unique evidence path |
| Throughput acceptance | OPEN / `CONFOUNDED_INCONCLUSIVE` | Keep threshold `0.90`; replicate paired ABBA on a quieter independent host, retain every pair, and satisfy predeclared host-PSI confounder controls |
| Dedicated-host rollback drill | NOT RUN / OPEN | Requires approved dedicated host and maintenance window; record config hash/mode/ownership, quota, service/route state, then verify exact restoration and workload continuity |
| Natural production PSI crossing at `0.40` | NOT RUN | Observe a natural event tied to the actual production controller decision; do not manufacture load or change the threshold |
| Continuous RC soak | NOT RUN | Authorized host and representative workload plan required; minimum 24 h, target 72 h, complete samples/journal and no critical continuity gaps |
| Stable release | NO-GO | Keep `0.6.5-rc.1`; no `v1.0.0` tag or stable release |

No production quota, routing/controller policy, PSI threshold, throughput acceptance threshold, or release version was changed. The strict parser and CI changes improve evidence integrity; they do not substitute for the outstanding runtime gates.
