# Release-readiness assessment — 2026-10-15, pass 11

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not create a `v1.0.0` tag or release.** The scope-aware aggregate policy now has a five-pair two-worker synthetic benchmark plus five-pair bounded compiler/build and GNU gzip CLI benchmarks on this host. All three pass the predeclared *median-based, workload-local* evaluator gates. The paired ranges and bootstrap intervals still include meaningful regressions on individual runs, so these are not a universal performance claim or a cross-host release acceptance result.

## Source, CI, and runtime integrity

- Code commit: [`2a451246771f2529507453c5b7a89e924a008d3b`](https://github.com/nplongx/linux-workload-guard/commit/2a451246771f2529507453c5b7a89e924a008d3b). It is synchronized with `origin/main`; hosted CI [run 38717776334](https://github.com/nplongx/linux-workload-guard/actions/runs/38717776334) passed on Ubuntu 22.04 and 24.04. The gzip harness support was introduced in `71b7750` and compiler progress accounting in `2a45124`; the latest hosted run includes both.
- The synthetic five-pair benchmark manifest records clean revision `5d08ee1b71292dc34bdfa1a835f05299a9bc2722`; the compiler/build and gzip benchmark manifests record clean revision `2a451246771f2529507453c5b7a89e924a008d3b`. All three report `git_dirty=false` and an empty tracked-diff SHA-256. Each workload has separate summary, raw sample, environment manifest, router log, and evaluator output files.
- `VERSION` is `0.6.5-rc.1`.
- Production user `workload-router.service`: active/running, MainPID `358400`, `NRestarts=0`, `ExecMainStatus=0`. User `heavy-workload.slice` reads back `CPUQuotaPerSecUSec=1s` (100%); system-level heavy slice is inactive with unlimited quota. Route-state file has zero rows. No temporary `lwgb*` benchmark unit remains. Existing persistent `lwg` test/benchmark slices are present and were not removed.
- Aggregate routing is not enabled in the installed production configuration. No production service/configuration/quota/threshold was changed. The aggregate mode changes relative `CPUWeight` on explicitly opted-in workload scopes; it is not a per-scope hard CPU cap. The parent quota remains the hard aggregate ceiling.
- Full local matrix is recorded in [`local-verification-pass11-2026-10-15.log`](local-verification-pass11-2026-10-15.log). `./test.sh`, install/uninstall smoke, routing/recovery/shadow/controller integration, aggregate-scope integration, quota/routing/PSI/evaluator/soak safety, Python/shell syntax, systemd verification, diff check, and version smoke passed. Live PSI acceptance and soak were not performed by these tests.

## Paired benchmark methodology

The three five-pair workloads use two opted-in worker scopes under a temporary parent with an effective 100% aggregate CPU quota and a nested 100% heavy child; baseline and router modes share the same ceiling. Trials alternate order. Each mode lasts 30 seconds. Evaluation uses the common elapsed steady-state window `relative_s >= 12`, requires both worker scopes to reach the routed weight at least two seconds before the window, requires at least 500 samples per mode per pair, and applies the predeclared limits: median paired wake-up p95/p99 and probe-work p95 ratios `<=1.10`, median throughput ratio `>=0.90`, router CPU `<=5%` of one logical CPU, and peak RSS `<=32768 KiB`. The evaluator reports each pair, range/IQR, and a deterministic 10,000-resample bootstrap percentile interval. The interval is descriptive and does not override the median gate.

### Five-pair two-worker synthetic workload

Artifact family: [`routing-contention-scope-aggregate-synthetic-5rep-pass9-2026-10-15`](routing-contention-scope-aggregate-synthetic-5rep-pass9-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- Evaluator: **PASS** for the declared synthetic workload on this host.
- Median paired steady wake-up p95 ratio `1.000` (range `0.993–1.010`, bootstrap 95% interval `[0.993,1.010]`); p99 `0.975` (range `0.948–1.007`, interval `[0.948,1.007]`); probe-work p95 `1.002` (range `0.995–1.005`, interval `[0.995,1.005]`).
- Median throughput ratio `1.039` (range `0.858–1.265`, interval `[0.858,1.265]`); one pair regressed throughput by about 14.2%. Router CPU was `0.330–0.366%` of one logical CPU and peak RSS `19,700–19,804 KiB`.
- All five router trials routed both scopes, converging around `8.7–9.0s`. This demonstrates stable-scope routing function and a median-gate pass, not a broad latency or throughput improvement.

### Five-pair generated compiler/build workload

Artifact family: [`routing-contention-scope-build-5rep30-pass11-2026-10-15`](routing-contention-scope-build-5rep30-pass11-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- Workload repeatedly compiles and links a generated 12-translation-unit C stress workload using the host C compiler. It is deliberately bounded and reproducible, but is **not a build of this repository or a claim about every real project build**.
- Evaluator: **PASS** for this declared workload/host. Median paired steady wake-up p95 ratio `1.004` (range `0.980–1.021`, bootstrap interval `[0.980,1.021]`); p99 `0.980` (range `0.939–0.993`, interval `[0.939,0.993]`); probe-work p95 `0.693` (range `0.673–1.232`, interval `[0.673,1.232]`).
- Median compile/link-step throughput ratio `1.278` (range `0.969–1.430`, interval `[0.969,1.430]`). One pair's probe-work p95 regressed `23.2%`, so the median pass is not a guarantee against per-run regression. Router CPU was `0.299–0.399%` of one logical CPU; peak RSS `19,708–19,892 KiB`.
- All five router trials routed both stable workload scopes before the common steady window despite the compiler's short-lived child processes. This closes the earlier *harness identity limitation* for this scope-wrapped generated workload; it does not close independent-host or production build acceptance.

### Five-pair GNU gzip CPU-bound CLI workload

Artifact family: [`routing-contention-scope-gzip-5rep30-pass11-2026-10-15`](routing-contention-scope-gzip-5rep30-pass11-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- Workload runs `gzip -1 -c /dev/zero` and measures compressed bytes per second. This is a representative CPU-bound CLI microbenchmark, but input compressibility and workload specifics limit generalization.
- Evaluator: **PASS** for this declared workload/host. Median paired steady wake-up p95 ratio `1.002` (range `0.992–1.015`, bootstrap interval `[0.992,1.015]`); p99 `1.006` (range `0.999–1.010`, interval `[0.999,1.010]`); probe-work p95 `1.000` (range `0.701–1.422`, interval `[0.701,1.422]`).
- Median throughput ratio `0.984` (range `0.834–1.131`, interval `[0.834,1.131]`). One pair regressed throughput `16.6%`; probe-work p95 also had a `42.2%` single-pair regression. Router CPU was `0.330–0.365%` of one logical CPU and peak RSS `19,700–19,900 KiB`.
- All five router trials routed both worker scopes before the common steady window. The median-based acceptance pass coexists with meaningful pairwise variability and must not be summarized as a universal performance improvement.

## Release gates

| Gate | Status | Evidence / remaining work |
|---|---|---|
| Deterministic controller, fault injection, lifecycle/recovery | **PASS** | Full local suite plus aggregate-scope integration: real temporary 100% parent cap, two scopes weighted, unrelated scope untouched, weights restored. |
| Aggregate routing two-worker function under 100% parent cap | **PASS — tested scope/workload only** | Five valid synthetic, compiler, and gzip pairs routed both scopes; CPUWeight remains a relative fairness mechanism, not a hard per-scope cap. |
| Predeclared paired median performance limits | **PASS — three declared workloads, one host** | Five pairs each for synthetic, generated compile/link, gzip. Individual regressions and wide intervals remain documented; no general release-performance claim. |
| Real repository build / additional interactive CLI | **OPEN** | Generated compile workload and gzip are bounded representatives. Validate a real repository build and a user-relevant CLI mix when safely available. |
| Ubuntu 24.04 hosted CI | **PASS** | CI run `38717776334`. |
| Ubuntu 22.04 delegated CPU counters | **SKIP / OPEN** | Existing hosted runner's delegated `cpu.stat` lacks `nr_periods` and `nr_throttled`. Use a runner with real delegation and `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1`; never synthesize counters. |
| Independent second host | **NOT RUN** | No independent host accessible in this execution context. |
| Natural live PSI-high `some avg10 >=0.40` correlated to controller action | **NOT RUN** | No safe natural crossing recorded; do not induce host-wide load or modify production settings. |
| Operator install/upgrade/rollback on a dedicated host | **PARTIAL** | Mocked install/uninstall and isolated recovery passed; separate operator procedure remains unexecuted on a dedicated representative host. |
| RC soak, minimum 24h / target 72h | **NOT RUN** | Safety tests exercise interruption/completeness logic only. A supervised safe-host run with continuous telemetry and `run_status=COMPLETE` is still required. |
| Production unchanged / artifact integrity | **PASS** | Runtime readback after benchmarks and tests; all new evidence uses unique pass-11 paths; earlier incomplete smoke artifacts preserved. |
| Release hygiene | **OPEN** | Keep `0.6.5-rc.1`; no `v1.0.0` tag/release while required gates remain open. |

## Next steps

1. Validate on an independent host and a real repository build / interactive workload without changing production thresholds or quota.
2. Obtain an Ubuntu 22.04 runner with genuine delegated CPU quota/throttling counters and rerun the live integration in required mode.
3. Wait for a natural PSI-high event only if it occurs safely; record timestamped PSI, decision, quota readback, and service state.
4. Complete operator rollback on a dedicated test host and a continuous 24-hour minimum / 72-hour target RC soak with immutable raw telemetry and explicit interruption handling.

No release gate is inferred closed from an environmental skip, smoke test, synthetic threshold injection, or median alone.
