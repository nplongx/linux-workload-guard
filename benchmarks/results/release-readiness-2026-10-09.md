# Release readiness assessment — 2026-10-09

## Decision

**Candidate for a limited release candidate (RC); do not yet label the build stable for broad deployment.** No commit, tag, or push was created as part of this assessment.

## Validation completed

- `./test.sh`: passed, including quota bounds/direction, scheduler-pressure hysteresis, and AIMD fail-closed checks.
- `./tests/integration-routing.sh`: passed; transient burst was not routed and sustained load was routed.
- `./tests/recovery-routing.sh`: passed.
- `./tests/shadow-routing.sh`: passed.
- `./tests/install-uninstall.sh`: passed with `systemctl` mocked; all installed helpers, including `workload-profile`, are removed by uninstall while user configuration is preserved. This test caught and fixed an uninstall omission for `workload-profile`.
- Release payload audit: exact-content benchmark renames were verified by SHA-256; proposed include/exclude lists are recorded in `release-candidate-manifest-2026-10-09.md`.
- `python3 -m py_compile` for router and replay scripts: passed.
- Shell syntax checks: passed.
- `systemd-analyze verify systemd/*.service systemd/*.timer systemd/*.slice`: passed.
- `git diff --check`: passed.
- Local Markdown links in top-level docs: all resolve.
- `./bin/workload-guard version`: reports the candidate version from `VERSION` (`0.6.5-rc.1`).

## Live quota tests

- Three bounded soak cycles used 2 CPU workers, each with 20 seconds of load and 20 seconds of observation/recovery. Quota moved up under demand and then stepped down after demand cleared. The 20-second observation window did not always end at the minimum quota, so a further 60-second recovery observation was run.
- The 60-second recovery observation returned quota to 100% by the 52-second sample after load ended. Quota transitions were bounded; no rapid back-and-forth transition was observed within the recovery trace.
- Under the original production thresholds (`QUOTA_PRESSURE_HIGH=0.40`, scheduler-delay high threshold `20 ms`), a live soak log captured a decrease from 200% to 150% at router runqueue-delay delta `20.96 ms` while PSI was 0.156 and demand 0.65. This verifies the scheduler-delay high-pressure branch in a live run with default thresholds.
- A separate diagnostic run temporarily lowered the PSI high threshold to 0.25 and observed quota decrease from 150% to 100% at pressure 0.279 while demand was 1.00. The original config was restored immediately afterward. This confirms the PSI-driven branch responds under an injected threshold, but does **not** prove the default 0.40 PSI threshold branch was crossed at the decision point.
- Final runtime state after all tests: router service active, `NRestarts=0`, heavy-slice quota 100%. User config thresholds were restored to 0.40/0.10.

Raw live traces and controller logs are the paired `quota-soak-*-20261009.tsv` / `-logs.tsv`, `quota-soak-recovery60-20261009.tsv` / `-logs.tsv`, and `quota-high-branch-20261009.tsv` / `-logs.tsv` files in this directory.

## Remaining release gates

1. The latest successful remote CI run is for the already-published `a188317` commit and its old single-runner workflow; it does not validate these local changes. The updated Ubuntu 22.04/24.04 matrix and new install/uninstall test still need a remote run after the changes are deliberately staged and pushed.
2. If claiming direct live validation of the default PSI threshold branch, obtain a safe trace where PSI is at least 0.40 at the controller decision and quota decreases while above its floor. Existing unit/replay tests cover the deterministic branch, and the live scheduler-delay branch has been observed.
3. Before tagging, review the 12 changed/deleted tracked paths, 69 untracked files, and 3 tracked deletions in the current worktree. Some are experiment artifacts; stage only intentional source, test, docs, and selected evidence files. Preserve the deletions unless their owner explicitly decides to restore them.
4. Keep AIMD in log-only shadow mode and describe Gradient2, weighted fairness, PID, and sched_ext/eBPF as experimental or future work—not release-ready enforcement.

## Interpretation

The observed evidence supports an RC with a narrow scope and explicit caveats. It is not a multi-host, multi-hour soak and does not establish universal latency improvements or production readiness for every workload. Dynamic quota remains an opt-in capability and should be rolled out conservatively.
