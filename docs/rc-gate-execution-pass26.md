# RC gate execution addendum — Pass 26 evidence retention

Version remains `0.6.5-rc.1`. This pass improves retention and reviewability of approved-runner integration evidence. It does not authorize dispatch, provision infrastructure, or close a runtime gate.

## Runtime workflow evidence contract

`.github/workflows/approved-controller-runtime.yml` remains `workflow_dispatch` only. The job condition still requires `main`, `RUN_APPROVED_CONTROLLER_INTEGRATION=true`, `APPROVED_CONTROLLER_RUNTIME_APPROVED=true`, and runner labels `self-hosted`, `linux`, `x64`, and `lwg-approved-controller-test`.

Each approved run uses a unique `benchmarks/results/approved-runtime-<run-id>-<attempt>/` directory. Before running integration, a prerequisite step records host/kernel/OS/cgroup facts and requires Ubuntu 22.04, cgroup v2 with the CPU controller, and a user systemd manager. The controller integration then validates required counters inside the actual temporary test cgroup and performs exact quota readback. Its raw output goes into the `controller/` child. An `always()` finalizer records source revision, run identity, host/kernel/OS/cgroup context, step outcomes, and the explicit `OPEN_PENDING_INDEPENDENT_REVIEW` classification. It writes and verifies `SHA256SUMS.txt`; a separate `always()` artifact-upload step retains the directory, including failure evidence, for 90 days.

The bundle is supporting runtime evidence, not an acceptance decision. A successful workflow proves only that the recorded steps succeeded on that runner. The reviewer must verify the run came from the approved host and expected source, inspect raw logs and effective quota/cgroup readback, verify the manifest, retain the manifest digest independently, and record a signed/traceable gate decision. A failed or interrupted run stays preserved and OPEN; do not rerun into its path.

## Prerequisites to verify before dispatch

Repository owner/operator must verify all of these without relying on the existence of the workflow alone:

1. A registered, isolated, dedicated runner carries all four required labels and is approved for temporary cgroup workloads. The runner must not host production workloads.
2. Repository variables `RUN_APPROVED_CONTROLLER_INTEGRATION` and `APPROVED_CONTROLLER_RUNTIME_APPROVED` both equal the literal string `true`; only an authorized owner may set them after approval.
3. The runner is Ubuntu 22.04 for the delegated CPU counter gate, uses cgroup v2, has a functioning user systemd manager and delegated CPU controller, and exposes `usage_usec`, `nr_periods`, and `nr_throttled` inside the actual temporary test cgroup.
4. The reviewed source revision is on `main`; tracked worktree is clean; free disk space and a unique evidence path are available.
5. The owner explicitly approves the runtime dispatch and maintenance window. Routine CI or local validation must never dispatch it.

If any prerequisite is absent, do not dispatch. Record `OPEN` with the missing prerequisite. In particular, zero registered runners or absent approval variables are infrastructure blockers, not test results.

## Gate evidence and review

- **G1/G2:** retain workflow URL/run ID, source SHA, host inventory identity, `/etc/os-release`, kernel/systemd versions, cgroup path and identity, required `cpu.stat` interval deltas, exact `CPUQuotaPerSecUSec` before/after readback, command exit statuses, complete logs, and verified hashes. A capability skip fails the strict runtime job and remains OPEN.
- **G3/G4:** use the sealed preregistration created before results are observed; run A/B/B2/A2 on an independently verified host. Preserve every block and raw sample. The throughput median remains `>=0.90`; latency/work ratios remain `<=1.10`; existing PSI confounder rules and sample requirements remain unchanged. Missing PSI is not quiet PSI.
- **G5:** perform exact config/mode restoration and service health/restart/effective quota readback on an approved disposable host. Keep private configuration contents out of public artifacts; publish hashes and modes only.
- **G6:** passive observation only. A natural PSI `some avg10` crossing at `>=0.40` must correlate with the controller's decision and effective quota readback. No crossing means `NOT RUN`, not failure to generate enough load.
- **G7:** at least 86,400 uninterrupted monotonic seconds (259,200-second target), approved representative workload-phase logs, complete telemetry/journal, verified outer hashes, and explicit reviewer sign-off. `run_status=COMPLETE` is collection integrity only; `soak_gate_status=REQUIRES_WORKLOAD_PHASE_LOGS_AND_HUMAN_REVIEW` is not PASS.

The runtime workflow currently exercises only controller/lifecycle integration; it does not execute ABBA, throughput acceptance, rollback, natural PSI crossing, or soak. Those remain separate approved procedures and independent gate rows.

## Current infrastructure blocker and release state

At the Pass 26 audit, repository API reported zero registered Actions runners and zero repository Actions variables. The required label and approval variables were absent. No runtime dispatch was attempted. Until an authorized owner provisions and approves the dedicated runner and variables, G1/G2 runtime execution remains blocked.

All seven mandatory gates remain OPEN. Keep dynamic quota opt-in, version `0.6.5-rc.1`, and release **NO-GO**. Do not create `v1.0.0` while any gate is open.
