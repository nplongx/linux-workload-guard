# Aggregate throughput follow-up — pass 15 preregistration

Date: 2026-10-15

Parent evidence: pass-12 throughput ratio `0.880`, pass-13 test-only weight-80 ratio `0.901`, and pass-14 ABBA blocks A/B/B2/A2. Pass 14 did not support the weight-80 improvement hypothesis and was confounded by host CPU PSI `some avg10` around 26–27% in every condition. This follow-up is a new, separately analyzed block set; it does not replace or pool away historical results.

## Falsifiable questions

- H1: at low host contention, weight 80 improves the median paired throughput ratio by at least 0.05 versus weight 20 and both order-balanced contrasts favor weight 80.
- H0: the effect is smaller, inconsistent, or the observed ratio is dominated by pair variability, quota saturation, or host interference.
- H2: high or changing host PSI explains at least part of the apparent weight effect; if host PSI `some avg10` exceeds 5% for >20% of samples in either condition or condition medians differ by >5 percentage points, label causal attribution `CONFOUNDED_INCONCLUSIVE` regardless of throughput.

## Fixed protocol

Run four fresh blocks in ABBA order: A3 weight 20, B3 weight 80, B4 weight 80, A4 weight 20. Each block has exactly five paired repetitions; each baseline and router mode lasts 30 seconds, with two Python integer-mixing workers, a 20 ms probe, identical steady-state selection and unchanged evaluator. Alternate baseline/router order within each repetition. Use the same isolated cgroup-v2 parent quota of 100% for all conditions. `CPUWeight` is changed only on the isolated workload scope via the benchmark environment. Dynamic quota actuation is disabled. Production service, configuration, thresholds and quota must not be modified.

Preflight requires three consecutive 5-second readings with host CPU PSI `some avg10 <=5%`, no active benchmark process, and production router active. If the preflight cannot be satisfied within 10 minutes, do not run the block; preserve a `NOT_RUN` record. Once a block starts, do not stop early because the PSI rises or a sample looks adverse; retain every pair and report confounding. Stop only for a safety incident or unrelated host workload risk, and mark the block `INCOMPLETE`.

Collect 1-second monotonic samples of parent/heavy/recovery/workload/probe `cpu.stat` (`usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`), effective `cpu.weight`, cgroup and host CPU PSI, load average, router CPU/RSS, route-decision logs, raw per-worker progress and raw probe timings. Preserve all summary, raw, environment, telemetry, log and evaluator outputs under unique pass-15 names. Verify raw-data recomputation, telemetry coverage and SHA-256 manifest before interpreting results.

## Unchanged acceptance and predeclared interpretation

Each five-pair block must meet the existing evaluator requirements: >=500 steady probe samples per mode, routing converged by 10 seconds for the common window beginning at 12 seconds, median paired wake-up p95/p99 and probe-work p95 ratios <=1.10, median paired throughput ratio >=0.90, router CPU <=5% of one logical CPU, and peak RSS <=32768 KiB. Do not change thresholds, discard pairs, or treat smoke runs as acceptance evidence.

The weight-effect hypothesis is only `CONSISTENT_WITH_H1` if both weight-80 blocks independently pass, each median >=0.90, all ten weight-80 pairs are >=0.80, the pooled ten-pair weight-80 median exceeds the pooled ten-pair weight-20 median by >=0.05, both order-balanced contrasts (B3−A3 and B4−A4) favor weight 80, latency/overhead gates pass, and the host PSI confounding rule is satisfied. Otherwise report the exact failed criteria. Even a successful result is single-host synthetic evidence and cannot alone authorize a production policy change. A production policy change requires independent-host replication and safety/regression evidence.

## Safety and release boundary

Do not change production quota/configuration, stop/restart the installed router, force PSI crossing, or add background contention. The 100% cap applies to the temporary benchmark parent only. Preserve pass-13/pass-14 evidence unchanged. Do not create a stable tag or release; `v1.0.0` remains blocked by outstanding mandatory gates.
