# Operator rollback and cross-host acceptance checklist

This is an operator procedure, not authorization to modify a live workload host. Perform the state-changing steps only in an approved maintenance window on a representative test host. Never use the current production host merely to manufacture release evidence.

## Before a controlled install/upgrade test

1. Record `date --iso-8601=seconds`, `uname -a`, `/etc/os-release`, `systemd --version`, `python3 --version`, `stat -fc %T /sys/fs/cgroup`, and `nproc`.
2. Confirm cgroup v2 and record `systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus`.
3. Save the exact config file permissions and contents to a private, access-controlled backup. Do not paste it into public CI or reports. Record the user-level `heavy-workload.slice` effective `CPUQuotaPerSecUSec` before making changes.
4. Record `systemctl --user show protected-workload.slice heavy-workload.slice -p ControlGroup -p CPUQuotaPerSecUSec`, `cat /proc/pressure/cpu`, and the relevant service journal window. Verify the intended user manager/account; the system-level and user-level slices are different units.
5. Keep a second terminal open to the same user manager. Define the exact prior configuration and quota restoration commands before starting; do not rely on a guessed default.

## Controlled rollback exercise

1. On the test host only, back up the config file with mode-preserving tools and note the original effective quota. Edit the test config to disable dynamic quota; do not change the production PSI threshold to create an event.
2. Restart only the test host's user service during the approved window. Verify `ActiveState=active`, `SubState=running`, a stable MainPID, no increasing restart count, and no unexpected route-state residue.
3. Confirm the effective heavy-slice quota remains at the deliberately recorded value while dynamic quota is disabled. If it changes unexpectedly, stop the exercise and restore the recorded quota.
4. Restore the original config byte-for-byte and its original permissions, then restart the test service. Verify health and effective quota again; inspect the complete journal for failed actuation, recovery, or configuration errors.
5. Capture before/after commands, exact exit statuses, config hashes (not config contents), quota readbacks, service identities, and journal excerpts in a private evidence directory. Mark the exercise `PASS` only if restoration is exact and the service is healthy; otherwise mark `FAIL` and preserve the failed evidence.

Suggested read-only observations:

```sh
systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus
systemctl --user show heavy-workload.slice -p ActiveState -p CPUQuotaPerSecUSec -p ControlGroup
cat /proc/pressure/cpu
journalctl --user -u workload-router.service --since '10 minutes ago' --no-pager
```

## Ubuntu/cgroup and second-host evidence

- On Ubuntu 22.04, run the full controller integration and preserve its unabridged output. Confirm the test slice exposes `usage_usec`, `nr_periods`, and `nr_throttled` in `cpu.stat`. Missing counters mean `SKIP`/unsupported environment, not `PASS`; do not edit the test to fake these counters.
- `tests/controller-integration.sh` reports a structured `SKIP` reason with kernel, cgroup mode, and visible controllers; missing-counter skips also list the available `cpu.stat` keys. In a runner explicitly provisioned to support live quota integration, set `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1` so a capability skip becomes a failing exit rather than a green-but-skipped step. Do not set that flag on generic hosted runners unless their delegated CPU controller is expected to be available.
- A second-host pass requires a physically/virtually independent host and a fresh manifest containing OS, kernel, architecture, CPU topology, systemd/Python versions, cgroup mode, delegation/counter availability, test commit, command, timestamps, raw output, and exit status. A second CI job or a second run on this same host is not a second host.
- For performance evidence, run `benchmarks/routing-contention-isolated.sh` with a unique `RESULT` path and `REPS=5 DURATION=30 WORKERS=1`; use `WORKLOAD_KIND=terminal-openssl` to exercise an actual CPU-bound OpenSSL SHA-256 CLI workload alongside the default synthetic integer loop. Keep the raw `.samples.tsv`, `.environment.txt`, `.router.log`, and evaluator report together. The evaluator checks raw/summary consistency and reports only workload-specific acceptance; it does not replace compiler/build workload or second-host validation.
- A soak requires `benchmarks/soak-monitor.sh` to finish its approved 24-hour minimum (72-hour target), `run_status=COMPLETE`, exit status `0`, full raw samples and service journal, a workload-phase log, and human review of transitions, restarts, telemetry gaps, oscillation, CPU/RSS, and recovery. An `INCOMPLETE` run now exits nonzero; preserve its artifacts and diagnose the recorded error instead of retrying into the same output path. A monitor-only run without approved representative workload phases is not sufficient.

## Status vocabulary

- `PASS`: required operation completed and all defined assertions/readbacks passed.
- `FAIL`: an assertion, rollback, service-health, data-integrity, or cleanup check failed.
- `SKIP`: the required capability/environment was unavailable and the test explicitly reported that condition.
- `NOT RUN`: the test was not executed or the required event did not occur (for example, no safe live PSI crossing).
- `INCOMPLETE`: an interrupted run, missing finalization, or insufficient sample continuity; never upgrade it to `PASS` by inference.
