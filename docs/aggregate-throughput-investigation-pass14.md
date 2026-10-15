# Aggregate throughput investigation — pass 14 protocol

Date: 2026-10-15

Harness/evaluator revision: `90b21d519294ea42d85b91bfb1b3b17f486fd151`

Workload: two independent Python integer-mixing workers plus a 20 ms probe; synthetic only.

## Question and falsifiable hypotheses

- **H1 (weight effect):** increasing the test-only workload-scope `CPUWeight` from 20 to 80 improves aggregate worker throughput by a repeatable, material amount under the same 100% parent quota.
- **H0 (no material weight effect):** throughput differences are small, inconsistent, or explained by time-varying host contention, quota throttling, measurement noise, or worker CPU efficiency rather than the weight setting.
- **H2 (host contention confound):** throughput/probe changes co-vary with host CPU PSI or parent throttling; if so, retain all observations but do not claim a causal weight effect.

## Predeclared design

Run four independent blocks in **ABBA** order: weight 20 (A), weight 80 (B), weight 80 (B2), weight 20 (A2). Each block has five paired repetitions, 30 seconds per mode, two workers, baseline/router mode order alternated within repetition, the same isolated cgroup-v2 parent with `CPUQuota=100%` and the unchanged evaluator. The installed router service and production configuration are not modified. Candidate weight is supplied only through the isolated benchmark environment.

Each interval is sampled on a monotonic timebase at 1 s: parent/heavy/recovery/workload/probe `cpu.stat` cumulative counters (`usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`), effective `cpu.weight`, cgroup and host PSI, load average, isolated router CPU ticks/RSS, and newly emitted route-decision log lines. Per-worker progress counters and the 20 ms probe samples are retained raw. Deltas are computed only within one `(mode, repetition, cgroup)`; cumulative counters are never differenced across trials.

### Acceptance and interpretation rules

The existing evaluator thresholds are unchanged: five valid pairs per block; at least 500 steady probe samples per mode; route convergence by 10 s for the common steady window starting at 12 s; median wake-up p95/p99 and probe-work p95 ratios <=1.10; median throughput ratio >=0.90; router CPU <=5% of one logical CPU; peak RSS <=32768 KiB. Every pair remains in the results, including adverse pairs. A block that misses these requirements is `FAIL` or `INCOMPLETE`, not a partial pass.

Evidence will be considered **consistent with a material weight effect** only if all of these hold:

1. Both weight-80 blocks independently pass the unchanged evaluator and each has median throughput ratio >=0.90.
2. The median of all ten weight-80 throughput ratios exceeds the median of all ten weight-20 ratios by at least 0.05, and both order-balanced contrasts (B vs A, B2 vs A2) favor weight 80.
3. Neither weight-80 block has a per-pair throughput ratio below 0.80; the latency and router-overhead gates pass without suppressing a pair.
4. The direction is not explained by an imbalance in host CPU PSI: if host `some avg10` exceeds 5% for >20% of samples in either condition, or the condition medians differ by >5 percentage points, preserve the results but classify causal interpretation as **confounded/inconclusive**.

These are investigation criteria, not a new production acceptance threshold. Even if met, a single host and synthetic Python workload are insufficient by themselves to promote a production policy change. No threshold is lowered and no controller behavior changes based on these blocks alone.

## Smoke instrumentation observations (not acceptance trials)

Two 20-second, one-pair instrumentation smokes were retained separately and are not eligible for acceptance. The second smoke's evaluator validated both interval telemetry and recomputed throughput from raw worker counters, but correctly returned `INCOMPLETE` because it had only one pair and fewer than 500 steady samples. Its host PSI `some avg10` was approximately 16.8% during baseline and 23.7% during router mode, with parent `nr_throttled` increasing in 190/190 quota periods in both modes. The throughput ratio was 0.850 and probe wake-up p95 was about 19.9 ms. This is strong evidence that the host was contended during that smoke, not evidence that weight 20 caused the throughput loss.

## Release boundary

No policy change is authorized by this protocol alone. Keep aggregate routing opt-in and production configuration/quota unchanged. Ubuntu 22.04 delegated CPU telemetry, independent-host validation, natural PSI-high crossing at 0.40, dedicated-host operator rollback, a representative repository build/interactive routed workload, and the 24 h minimum / 72 h target soak remain separate gates. No stable tag/release is authorized.
