# RC gate execution protocol — pass 21

Version remains `0.6.5-rc.1`. This document is an execution checklist, not evidence that any external gate ran. Never use production quota/service changes to manufacture a result. Create a new, unique evidence directory for every attempt; do not reuse or overwrite historical paths.

## 1. Capture identity and capabilities first

Run on the host intended for the gate, as the account owning the user systemd manager:

```sh
date --iso-8601=seconds
uname -a
cat /etc/os-release
systemd --version
python3 --version
stat -fc %T /sys/fs/cgroup
cat /sys/fs/cgroup/cgroup.controllers
systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus
systemctl --user show heavy-workload.slice -p ActiveState -p ControlGroup -p CPUQuotaPerSecUSec
cat /proc/pressure/cpu
```

Keep system-manager and user-manager observations separate. Record `git rev-parse HEAD`, tracked-tree cleanliness, version, command, UTC/local timestamps, exit status and hashes. Do not include secrets or private configuration contents in shared evidence.

## 2. Ubuntu 22.04 delegated CPU telemetry and quota readback

On a provisioned runner with user systemd and delegated cgroup v2 CPU controller, use a fresh evidence path:

```sh
WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 \
WORKLOAD_GUARD_CONTROLLER_RESULTS_DIR="$PWD/benchmarks/results/pass21-ubuntu2204-<unique-id>" \
./tests/controller-integration.sh
```

Replace `<unique-id>` before running; the path must not exist. Capture unabridged stdout/stderr and exit status. Confirm the actual test cgroup's `cpu.stat` contains `usage_usec`, `nr_periods`, and `nr_throttled`; record desired quota and `systemctl --user show <temporary-test-slice> -p CPUQuotaPerSecUSec` readback. Missing counters or unavailable user manager are `SKIP / OPEN`, never `PASS`. The integration writes only unique temporary test units, but still requires an explicitly approved test host/window.

## 3. Preregistered independent-host ABBA and throughput

Before any run, copy the protocol into a new immutable preregistration artifact and record its SHA-256, source revision, host identity, workload, pair count, mode order, steady-state window, PSI rule, evaluator command and acceptance threshold. Use a genuinely independent host, not another process/container on the same host.

- Execute baseline/router/router/baseline (ABBA), preserving every pair and raw sample.
- Keep throughput acceptance at `0.90`; latency/overhead gates remain those in the preregistered benchmark protocol.
- PSI confounding: if host CPU PSI exceeds 5% in more than 20% of steady-state samples in any mode/pair, or baseline/router median PSI differs by more than 5 percentage points, classify `CONFOUNDED_INCONCLUSIVE`.
- Never drop adverse samples, rerun only unfavorable pairs, or convert confounded data into causal `FAIL`/`PASS`.
- Recompute summaries from raw probe and worker-throughput files; validate monotonic ordering, route validity, cgroup telemetry, source provenance and manifest hashes.
- If preflight PSI cannot meet its declared limit, preserve a `NOT_RUN` record rather than adding load or changing the threshold.

Use `benchmarks/routing-contention-isolated.sh` only after host approval and preregistration. Its benchmark creates temporary cgroups and CPU workload, so it is not a read-only operation. A valid single-host run does not close independent-host acceptance.

## 4. Dedicated-host rollback drill

Use `docs/operator-rollback.md` on a dedicated test host during an approved window. Before starting, record exact config hash/mode and effective quota. Define exact restoration steps in advance. Run the drill only against the test user's service/config; verify service health, restart counter, journal, route-state behavior, and effective quota after disable/restore. Preserve failed evidence. A successful synthetic test or static inspection is not a rollback drill.

## 5. Natural PSI crossing at 0.40

Passive-only command (new output directory required):

```sh
python3 benchmarks/live-psi-observe.py \
  "benchmarks/results/pass21-live-psi-<unique-id>" 3600 5
```

This collector does not generate load or actuate quota. `collection_status=COMPLETE` means only that the trace passed integrity checks. Gate acceptance still requires a genuine `some avg10 >= 40%` observation, matching controller decision logs, and reviewed effective quota before/after evidence. No crossing means `NOT_RUN`; never force it.

## 6. RC soak

On an approved host, first define representative workload phases and their separate logs. Start the monitor in a persistent user systemd transient unit or equivalent supervised session so terminal disconnect does not terminate it. Example direct invocation:

```sh
benchmarks/soak-monitor.sh "benchmarks/results/pass21-soak-<unique-id>" 86400 60
```

Use `259200` seconds for the 72-hour target. The output path must be new. The monitor itself is passive and does not create representative workload phases; attach approved workload-phase logs and review service journal, restarts, quota transitions, PSI, CPU/RSS, route counts, telemetry gaps and recovery. Only `run_status=COMPLETE`, exit status 0, verified checksums and a human-reviewed workload record can be considered for the soak gate. An interrupted or incomplete run stays `INCOMPLETE` and must be preserved.

## 7. Evidence and release decision

For every attempt, retain command, exit status, timestamps, host/kernel/systemd/cgroup details, source revision, environment, raw data, evaluator output and SHA-256 manifest. Generate a new manifest after all files are finalized; verify it from the evidence directory. Historical manifests are immutable.

Required external gates remain independently tracked: delegated counters/readback, independent-host ABBA, throughput acceptance, dedicated-host rollback, natural PSI crossing, and continuous soak. CI success and local tests do not substitute for these. Keep `0.6.5-rc.1`, do not create `v1.0.0`, and retain NO-GO while any mandatory gate is open.
