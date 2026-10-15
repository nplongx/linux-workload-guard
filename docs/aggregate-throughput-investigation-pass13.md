# Aggregate throughput regression investigation — pass 13

Date: 2026-10-15
Source revision for experiment: `e94a56b8b5dbf6dcd6a8889844fc2cd475fc9c5f`
Host: Linux x86_64, kernel `7.0.0-38-generic`, Intel Core i5-8250U, 8 logical CPUs, cgroup v2, systemd 259.

## Question and predeclared gate

Pass 12's two-worker Python integer-mixing benchmark failed the unchanged median throughput floor: ratio >= 0.90. Paired duration was 30 seconds per mode, 5 repetitions, 100% aggregate parent quota in both modes, common steady window `relative_s >= 12`, route converged >=2 seconds before the window, and 10,000-resample deterministic bootstrap descriptive intervals. Acceptance limits were not changed.

## Pass-12 observations

- Five of five router trials routed both worker scopes by 8.513–8.702 seconds.
- Median paired throughput ratio 0.880; pair ratios 0.717, 1.143, 0.982, 0.880, 0.821; range 0.717–1.143; bootstrap interval `[0.717, 1.143]`.
- Median wake-up p95/p99 ratios 0.996/1.007. Median probe-work p95 ratio 1.014.
- Router CPU was 0.264–0.364% of one logical CPU and peak RSS 19,872–19,992 KiB.
- PSI/load diagnostics varied during the run; they are not grounds to discard pairs. Raw samples and manifests are preserved in the pass-12 artifact family.

## Hypothesis

**H1 — relative CPUWeight is a material throughput tradeoff.** The aggregate policy sets each eligible workload scope to weight 20 while the probe scope is a sibling under the same capped parent. This can reduce workload share relative to the probe and other sibling scopes. Prediction: raising only the experimental scope target weight, keeping the same 100% parent cap and workload, should improve worker throughput; if probe latency remains unchanged, the lower weight is not buying a measurable latency benefit in this harness.

**Alternatives not ruled out:** host scheduling noise and workload/probe phase effects; Python interpreter/GIL and process placement; measurement variability in progress counters; other sibling tasks competing under the parent. Router CPU overhead is not a leading explanation given measured <0.4% of one logical CPU, but the available evidence is not a causal proof.

## Test of H1: candidate weight 80

New immutable artifacts:

- `benchmarks/results/routing-contention-scope-aggregate-synthetic-weight80-5rep30-pass13-2026-10-15.tsv`
- adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, `.acceptance.txt`

Only benchmark override changed: `BENCH_AGGREGATE_SCOPE_WEIGHT=80`. Both modes retained the same 100% parent quota; aggregate routing remained opt-in in the isolated router. Five valid pairs routed both scopes by 8.448–8.685 seconds. The unchanged evaluator returned **PASS**, with median throughput ratio 0.901, pair range 0.870–1.254 and descriptive bootstrap interval `[0.870, 1.254]`. Median wake-up p95/p99 ratios were 0.998/0.987 and median probe-work p95 ratio 1.007. Router CPU was 0.264–0.333% of one logical CPU; peak RSS 19,884–19,992 KiB.

This is borderline and variable: two pairs had throughput ratios below 0.90, and one pair's 1.254 ratio materially influenced the median. It supports H1 weakly but does not establish causality or generalized improvement. Do not exclude pairs, treat bootstrap intervals as a gate, change the acceptance floor, or promote weight 80 as a production default.

## Root-cause status and next experiment

Root cause is **not confirmed**. The next run should collect per-interval scope `cpu.stat` usage deltas, `nr_periods`, `nr_throttled`, observed sibling `cpu.weight`, PSI, and probe measurements under the same timebase. Predeclare at least two additional independent five-pair blocks or a justified sequential design before comparing candidate weights; preserve every block. Compare weights 20 and 80 in alternating/order-balanced blocks, and only accept a policy change if the existing throughput and latency gates pass without pair suppression and a predeclared regression/fairness rule is met. Keep production aggregate routing disabled and leave production quota/configuration untouched.

## Other release gates

No new evidence closes Ubuntu 22.04 delegated CPU telemetry, independent-host validation, natural PSI-high 0.40 crossing, dedicated-host operator rollback, real repository build/interactive workload, or the continuous 24-hour minimum / 72-hour target RC soak. Keep these `SKIP`, `NOT RUN`, `PARTIAL`, or `OPEN` as applicable. No release/tag is authorized.
