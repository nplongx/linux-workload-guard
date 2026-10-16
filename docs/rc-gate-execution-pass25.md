# RC gate execution addendum — Pass 25 soak eligibility

Version remains `0.6.5-rc.1`. This addendum clarifies soak evidence semantics. It does not authorize workload generation, quota changes, production configuration changes, runtime workflow dispatch, or execution on this host.

## Collection status is not gate acceptance

`benchmarks/soak-monitor.sh` is a passive collector. It does not create representative workload phases, and it cannot independently approve release evidence.

- `run_status=INCOMPLETE` means telemetry, timing, service identity/health, journal, or continuity checks failed. Preserve the artifact; do not treat it as qualifying soak evidence.
- `run_status=COMPLETE` means only that the requested collection interval passed the collector's integrity checks.
- `soak_gate_status=OPEN_RUN_INCOMPLETE` means the run failed internal continuity/integrity checks.
- `soak_gate_status=OPEN_DURATION_BELOW_24H` means the monotonic elapsed duration was under 86,400 seconds. This is never qualifying evidence for the mandatory soak gate.
- `soak_gate_status=REQUIRES_WORKLOAD_PHASE_LOGS_AND_HUMAN_REVIEW` means at least 86,400 seconds of collector duration completed with internal checks intact. It is not `PASS`; approved representative workload-phase logs, outer evidence hashes, journal review, and explicit reviewer sign-off remain required.

The 72-hour target is 259,200 seconds. Use a unique output directory for every attempt. Do not restart an interrupted run and combine its fragments into a purported continuous soak. Do not remove or overwrite failed historical artifacts.

## Approved-host procedure

Only after independent host/operator approval, start the collector in a supervised session and run the approved representative workload phases separately with their own timestamped logs. Example collector command:

```sh
benchmarks/soak-monitor.sh "benchmarks/results/pass25-soak-<unique-id>" 86400 60
```

For the target duration, use `259200` seconds. Before review, verify all of the following:

1. The collector's actual monotonic elapsed duration is at least 86,400 seconds, with no interruption, service restart, PID discontinuity, missed sampling deadline, clock discontinuity, telemetry gap, or journal failure.
2. `run_status=COMPLETE`, process exit status is zero, and `soak_gate_status=REQUIRES_WORKLOAD_PHASE_LOGS_AND_HUMAN_REVIEW`.
3. Approved workload-phase logs cover the declared representative phases and align with the collector's time window. The monitor does not create or validate those logs automatically.
4. Review PSI, effective quota observations/transitions, router CPU/RSS, routed-process counts, service health, restart counters, errors, and full journal. Investigate every warning/error; do not interpret missing telemetry as zero.
5. Generate and verify an outer SHA-256 manifest after all evidence is finalized. Retain the manifest digest in the independent review record.
6. An authorized reviewer explicitly records the gate decision. The collector itself never emits release-gate `PASS`.

No workload, quota actuation, live integration, PSI-forcing activity, or soak was run during Pass 25 local validation.

## Other mandatory gates remain open

| Gate | Status | Required evidence |
|---|---|---|
| Ubuntu 22.04 delegated CPU counters/effective quota readback | OPEN | Approved temporary cgroup counters and exact effective quota/unit identity readback |
| Approved-runner controller integration | OPEN | Explicitly approved dispatch-only workflow on dedicated runner |
| Independent-host preregistered ABBA | OPEN | Sealed protocol, independent host, all A/B/B2/A2 raw evidence |
| Throughput acceptance | OPEN | Median paired throughput ratio >= 0.90, latency/work ratios <= 1.10, valid PSI confounder evidence |
| Dedicated-host rollback | OPEN | Exact restoration, service health/restart evidence, config hashes/modes, effective quota readback |
| Natural PSI crossing >= 0.40 | OPEN | Passive timestamp-correlated crossing, controller decision and effective quota readback |
| Continuous soak | OPEN | Requirements above plus independent review |

Keep dynamic quota opt-in, preserve all existing bounds and PSI rules, keep version `0.6.5-rc.1`, do not create `v1.0.0`, and retain release NO-GO while any mandatory gate remains open.
