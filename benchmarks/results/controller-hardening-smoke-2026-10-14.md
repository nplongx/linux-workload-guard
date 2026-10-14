# Controller hardening smoke evidence — 2026-10-14

## Environment and method

Three repeat runs against the final interval-averaged route-demand implementation: `controller-hardening-smoke-2026-10-14-run7`, `run8`, and `run9`. Raw probe samples, each worker's throughput counters, full router log, and environment metadata are stored with each run directory.

- Ubuntu 26.04.1 LTS, kernel `7.0.0-38-generic`, x86_64, Python 3.14.4, systemd 259.5, cgroup v2, 8 logical CPUs.
- Test-only parent, recovery, and heavy slices. Two competing CPU-bound workers; 120 ms burst rejection; 5-second no-router baseline; approximately 5.1-second enabled interval; quota increase/decrease; idle recovery; heavy-slice stop/recreate and resumed routing.
- Test thresholds intentionally held the synthetic test in the low-pressure branch (`PSI low/high=0.99/1.00`, scheduler delay `99999/100000 ms`). These settings applied only to temporary test processes/slices; production config and live slices were not changed. Consequently, this run does not validate the production PSI-high branch at `0.40`.
- Probe period 20 ms. Percentiles below are the **median of three per-run percentiles**, not pooled estimates. CPU-bound throughput is aggregate Python-loop iterations/second and workload-specific. Router CPU is from process CPU ticks over the enabled interval; router scheduler delay is its `/proc/<pid>/schedstat` run-delay delta, not system-wide delay.

## Repeated observations (directly measured)

| Metric | No-router baseline, median (range) | Router enabled, median (range) |
|---|---:|---:|
| Probe wake-up lateness p50, ms | 0.086 (0.084–0.095) | 0.090 (0.084–0.093) |
| Probe wake-up lateness p95, ms | 0.997 (0.271–1.856) | 0.420 (0.226–0.972) |
| Probe wake-up lateness p99, ms | 18.181 (15.153–18.611) | 1.453 (1.450–2.103) |
| Probe work duration p50, ms | 1.498 (1.389–1.556) | 1.410 (1.262–1.413) |
| Probe work duration p95, ms | 17.026 (14.878–17.597) | 1.792 (1.779–1.800) |
| Probe work duration p99, ms | 18.984 (16.602–20.082) | 3.213 (2.534–4.503) |
| Two-worker aggregate Python-loop iterations/s | 44,039 (39,327–49,220) | 54,466 (52,421–57,862) |

Additional enabled observations over the same short runs:

- Router CPU: median **0.06 CPU seconds** over a median 5.174-second window; **1.16% of one logical CPU** (range 1.16–1.37%).
- Router RSS at end: approximately 18.2 MiB; median peak RSS **18,224 KiB** (range 18,184–18,280 KiB).
- Router process runqueue-delay delta: median **22.465 ms** accrued over the enabled interval (range 17.240–28.350 ms); not a per-second scheduler delay estimate.
- Both workers routed: median **1.066 s** (range 1.050–1.068 s). First quota increase: median **1.353 s** (range 1.335–1.357 s). Idle-to-quota-decrease-and-recovery: median **2.377 s** (range 2.371–2.655 s).
- Quota sequence in each final run: `100 -> 150 -> 200 -> 250 -> 200 -> 150 -> 100` percent. One direction reversal reflects changing from sustained-load increase to idle recovery. Integration test then waits through a complete quota dwell window and rejects any upward transition after routes have recovered.
- Observed host CPU PSI `some.avg10` stayed far below 40% in these runs; highest start/end reading among run7–run9 was 10.72%. These observations do not exercise the production `0.40` high-pressure threshold.
- Every final run rejected the 120 ms burst, routed both sustained workers, applied and lowered actual quota on an isolated temporary slice, logged AIMD proposals without applying them, recovered both workers to the recovery leaf, and resumed routing after cgroup recreation.

## Regression discovered during the hardening pass

An early iteration (run1, before route-demand interval averaging and the post-recovery dwell assertion) recorded quota transitions `100->150->200->250->200->250`: quota rose again after an initial decrease. The controller previously used the latest routed-process CPU sample as demand, even when that sample did not represent the full cgroup-counter interval. Demand now averages routed CPU samples across the interval between quota observations, and the integration test rejects renewed quota growth after recovery plus a dwell interval. Runs7–run9 show no rebound in that checked post-recovery window. Earlier run1–run4 artifacts are retained as iteration history, not pooled into the final three-run summary.

## Limits and interpretation

These are short, synthetic, single-host smoke measurements, not a stable performance claim. Background host load and PSI changed between baseline and enabled windows; tail values vary across runs. The Python-loop throughput is only a local contention proxy. There is no second-host validation, no production-default PSI threshold crossing, no statistically robust broad-workload throughput result, and no 24-hour/72-hour soak. Do not infer an application SLA or production-safe quota policy from this data alone.

To reproduce a new non-overwriting result directory:

```sh
WORKLOAD_GUARD_CONTROLLER_RESULTS_DIR=benchmarks/results/controller-hardening-smoke-YYYY-MM-DD-runN ./tests/controller-integration.sh
```

The script refuses to overwrite an existing result directory. Each result includes `environment.txt`, `metrics.txt`, `probe-samples.tsv`, both worker throughput files, and `router.log`.
