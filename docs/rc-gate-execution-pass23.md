# RC gate execution protocol — Pass 23

This is a controlled execution plan, not approval to run workloads. Current release remains `0.6.5-rc.1`; do not create `v1.0.0` while any mandatory gate is open. Never run workload/actuation/soak steps on a development or production host merely to produce evidence.

## Evidence classes

- **Code/static validation:** unit, parser, mocked failure-path tests, syntax/compile checks, systemd unit verification, workflow guard tests. These validate code contracts only.
- **Protocol integrity:** preregistration JSON schema and SHA-256 seal. A valid seal does not prove execution, host independence, approval, or performance.
- **Runtime collection:** raw measurements with exact command, exit status, host identity, source revision, monotonic/wall timestamps, and complete manifests. Collection completeness alone is not gate acceptance.
- **Reviewed acceptance:** designated reviewer confirms every gate's criteria and provenance. No inference from missing telemetry, `SKIP`, interrupted runs, or green generic CI.

## Approved-runner preflight

Before any runtime command, an operator must independently confirm the runner is dedicated, isolated from production workloads, has cgroup v2 CPU delegation and required `cpu.stat` counters, and has an approved maintenance window. Record a unique operator ID and a stable independent-host ID; the latter is an operator assertion backed by infrastructure inventory, not merely the local hostname. Do not alter repository approval variables as part of this procedure.

On the approved host, after checking out the exact reviewed commit and confirming tracked worktree cleanliness, create the sealed protocol in a new path under `benchmarks/results/` using `benchmarks/preregister_abba.py create` with explicit `--host-id`, `--operator-id`, `--workload-kind`, `--confirm-independent-host`, and `--confirm-approved-window` arguments. Inspect the generated `protocol.json` and `SHA256SUMS.txt`; verify with the tool's `verify` command. Preserve the protocol directory permanently. Do not create it on this development host while the repository is dirty or approval is absent.

The declared independent host ID must match the benchmark's observed host binding exactly. The hostname is separately recorded as machine-level provenance. Neither value alone proves independence; attach the approved runner/inventory record to the evidence bundle.

## ABBA execution

Only on the approved dedicated runner, execute all four sealed blocks in order: A (CPUWeight 20), B (80), B2 (80), A2 (20). Use a fresh result path for each block and bind each invocation to the same sealed preregistration directory with `BENCH_REQUIRE_PREREGISTRATION=true`, `BENCH_PREREGISTRATION_DIR`, and the correct `BENCH_PREREGISTRATION_BLOCK`. Keep `BENCH_AGGREGATE_ROUTING=true`, block-specific `BENCH_AGGREGATE_SCOPE_WEIGHT`, declared `WORKLOAD_KIND`, `REPS=5`, `DURATION=30`, and `WORKERS=2` aligned with the sealed protocol. Do not edit sealed thresholds, reorder blocks, omit pairs, or delete outliers.

Run the aggregate analyzer and performance reviewer only after all raw outputs and telemetry have been preserved. Throughput acceptance remains `0.90`; latency/work ratio remains `1.10`. For any mode/pair, more than 20% of steady-window host PSI `some avg10` samples above 5%, or baseline/router condition-median imbalance above 5 percentage points, yields `CONFOUNDED_INCONCLUSIVE`. Missing/invalid PSI or incomplete pair coverage cannot be called quiet or PASS. Keep every sample.

## Runtime gates beyond ABBA

1. **Delegated CPU telemetry/readback:** on approved Ubuntu 22.04 and 24.04 environments, capture actual test-cgroup `usage_usec`, `nr_periods`, `nr_throttled`, and effective systemd quota readback. Missing counters are `SKIP / OPEN`, not PASS.
2. **Controller runtime integration:** run only through the workflow's dedicated runner job after both approval variables are authorized by repository owners. The generic matrix must continue to report `SKIP / OPEN`. This gate does not close ABBA, rollback, PSI crossing, or soak.
3. **Rollback drill:** use a representative disposable test host and approved window. Record exact config hash/permissions, service identity and restart count, and effective quota before and after; disable dynamic quota only in the test config, verify no actuation while disabled, restore config byte-for-byte, then verify health/readback. Preserve failures. Never perform this drill on production.
4. **Natural PSI crossing:** run `benchmarks/live-psi-observe.py` passively only during a naturally occurring approved observation window. Never generate load or change thresholds to force `some avg10 >= 40%`. Acceptance requires a genuine crossing correlated by timestamp with controller decision logs and verified effective quota before/after, followed by human review. A completed trace without a crossing is collection evidence only; a crossing without matching decision/readback is not acceptance.
5. **Soak:** run `benchmarks/soak-monitor.sh` only alongside approved representative workload phases on the dedicated host. Minimum continuous duration is 24 hours; target is 72 hours. Require exit 0, `run_status=COMPLETE`, complete sample cadence and telemetry, journal capture, workload-phase logs, SHA-256 integrity, and reviewer sign-off. Interrupted/incomplete runs remain incomplete and must be preserved, not overwritten.

## Release decision and evidence bundle

Every command record must include the literal command, start/end timestamps, exit status, host/source provenance, stdout/stderr log paths, and evidence class. Generate `SHA256SUMS.txt` only after the bundle is complete, then run `sha256sum -c SHA256SUMS.txt` and preserve its output. Keep prior Pass 21/22 and historical untracked artifacts immutable. Stage only reviewed source, tests, docs, and the new uniquely named validation bundle.

Mandatory gates: delegated CPU counters/effective quota readback; approved-runner controller integration; independent-host preregistered ABBA; throughput acceptance at `0.90` under PSI rules; dedicated-host rollback; natural production PSI crossing at `0.40` with correlated decision/readback; and continuous soak >=24 hours (72-hour target). Any `OPEN`, `SKIP`, `NOT RUN`, `INCOMPLETE`, or `CONFOUNDED_INCONCLUSIVE` mandatory gate means **NO-GO**. Do not create a stable tag or claim stable release until all mandatory gates have qualifying reviewed evidence.
