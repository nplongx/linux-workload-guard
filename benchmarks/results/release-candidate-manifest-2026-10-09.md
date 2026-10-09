# Release-candidate file audit — 2026-10-09

This is a review aid only. Nothing has been staged, committed, tagged, pushed, or deleted by this manifest.

## Proposed release payload

### Existing tracked files to keep in the candidate

- `.github/workflows/ci.yml`
- `CHANGELOG.md`
- `README.md`
- `benchmarks/routing-contention.py`
- `bin/workload-router.py`
- `test.sh`
- `tests/integration-routing.sh`
- `tests/recovery-routing.sh`
- `uninstall.sh` (includes the fix for uninstalling `workload-profile`)

### New scripts/tests to include

- `benchmarks/controller-replay.py`
- `benchmarks/quota-pressure-replay.py`
- `benchmarks/quota-stability-live.sh`
- `benchmarks/routing-contention-isolated.sh`
- `benchmarks/shadow-policy-replay.py`
- `tests/install-uninstall.sh`
- `tests/shadow-routing.sh`

### Evidence files referenced by README or release-readiness assessment

- `benchmarks/results/routing-policy-baseline-2026-10-09.tsv`
- `benchmarks/results/shadow-live-2026-10-09.tsv`
- `benchmarks/results/shadow-regression-2026-10-09.tsv`
- `benchmarks/results/shadow-replay-2026-10-09.tsv`
- `benchmarks/results/shadow-replay-5sample-candidate-2026-10-09.tsv`
- `benchmarks/results/shadow-live-regression-2026-10-09.tsv`
- `benchmarks/results/quota-benchmark-2026-10-09.tsv`
- `benchmarks/results/routing-contention-2026-10-09.tsv`
- `benchmarks/results/routing-contention-2026-10-09.samples.tsv`
- `benchmarks/results/routing-contention-isolated-1worker-5rep-2026-10-09.tsv`
- `benchmarks/results/quota-soak-1-20261009.tsv` and `benchmarks/results/quota-soak-1-20261009-logs.tsv`
- `benchmarks/results/quota-soak-2-20261009.tsv` and `benchmarks/results/quota-soak-2-20261009-logs.tsv`
- `benchmarks/results/quota-soak-3-20261009.tsv` and `benchmarks/results/quota-soak-3-20261009-logs.tsv`
- `benchmarks/results/quota-soak-recovery60-20261009.tsv` and `benchmarks/results/quota-soak-recovery60-20261009-logs.tsv`
- `benchmarks/results/quota-high-branch-20261009.tsv` and `benchmarks/results/quota-high-branch-20261009-logs.tsv`
- `benchmarks/results/release-readiness-2026-10-09.md`

The README-linked files should ship together with the README to avoid broken evidence links. The raw contention samples are about 375 KB; keep them if preserving reproducibility is preferred over minimizing repository size.

## Exact-content renames

These three tracked deletions are exact-content renames, not lost measurements (SHA-256 matches the old `HEAD` version byte-for-byte):

- `benchmarks/results/lwg-quota-benchmark-2026-10-09.tsv` → `benchmarks/results/quota-benchmark-2026-10-09.tsv`
- `benchmarks/results/lwg-routing-contention-2026-10-09.tsv` → `benchmarks/results/routing-contention-2026-10-09.tsv`
- `benchmarks/results/lwg-routing-contention-2026-10-09.samples.tsv` → `benchmarks/results/routing-contention-2026-10-09.samples.tsv`

Keep these deletions paired with their replacement additions; do not restore the old names unless the README references are changed back too.

## Keep out of the minimal product release by default

The following are research/diagnostic evidence and are not linked from the user-facing README. Keep them in the working tree or publish as a separate research bundle unless maintainers want the complete experiment archive in the release commit:

- `benchmarks/results/aimd-shadow-*` (multiple live samples, logs, and trial summaries)
- `benchmarks/results/controller-research-assessment-2026-10-09.md`
- `benchmarks/results/operational-hardening-assessment-2026-10-09.md`
- `benchmarks/results/shadow-policy-assessment-2026-10-09.md`
- `benchmarks/results/controller-replay-2026-10-09.tsv`
- `benchmarks/results/quota-pressure-replay-2026-10-09.tsv`
- `benchmarks/results/quota-repeat-*-20261009.tsv` and matching logs
- `benchmarks/results/quota-long-20261009.tsv` and matching logs
- `benchmarks/results/quota-stability-live-2026-10-09.tsv` and matching logs (unless retained as extra evidence)
- `benchmarks/results/quota-stress-*.tsv` and matching logs (historical diagnostic runs)
- `benchmarks/results/routing-contention-isolated-1worker-2026-10-09.*`
- `benchmarks/results/routing-contention-longrun-2026-10-09.tsv`

Do not delete these files as part of selection; exclusion from the release payload does not mean removing local evidence.

## Gates before tagging

1. Review/stage only the intended files above, plus any evidence explicitly selected by maintainers.
2. Run the updated CI matrix on Ubuntu 22.04 and 24.04 after the candidate changes are pushed to a branch.
3. Confirm CI checks out the candidate SHA and includes install/uninstall, routing, recovery, shadow routing, and systemd validation steps.
4. Keep dynamic quota opt-in, AIMD log-only, and Gradient2/weighted fairness/PID/sched_ext/eBPF described as experimental or future work.
5. Do not call this stable for broad deployment based on the current single-host stress evidence.
