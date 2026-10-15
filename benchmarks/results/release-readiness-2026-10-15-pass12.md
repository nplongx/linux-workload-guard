# Release-readiness assessment — 2026-10-15, pass 12

## Decision

**Keep `VERSION=0.6.5-rc.1`; do not tag or release `v1.0.0`.** Scope-aware aggregate routing is more robust and bounded after batched systemd identity discovery, and two current-revision workloads pass the predeclared median evaluator. The current-revision two-worker synthetic throughput gate **fails** (median ratio 0.880, below the 0.90 minimum). The controller therefore has not earned a general performance acceptance claim. Keep the feature opt-in and production configuration unchanged.

## Source and CI

- Current code revision: [`38556e48e2a73d6132492cf66dfa0e67028eebd0`](https://github.com/nplongx/linux-workload-guard/commit/38556e48e2a73d6132492cf66dfa0e67028eebd0), synchronized with `origin/main` when the benchmarks ran. Hosted CI [run 38720262761](https://github.com/nplongx/linux-workload-guard/actions/runs/38720262761) passed both Ubuntu 22.04 and 24.04 jobs. Ubuntu 22.04 still explicitly skips live controller integration because delegated `cpu.stat` lacks real `nr_periods` and `nr_throttled`; aggregate-scope integration also skips there because the CPU controller is not delegated. Ubuntu 24.04 runs and passes both integrations.
- All three pass-12 benchmark manifests record `git_revision=38556e48e2a73d6132492cf66dfa0e67028eebd0`, `git_dirty=false`, and the empty tracked-diff SHA-256. Each run uses a unique result path and retains its raw samples, manifest, router log, summary and evaluator output.
- The previous pass-11 artifacts remain committed and unchanged. The prior 25-second compiler smoke remains preserved as `INCOMPLETE`; it is not substituted for the current five-pair build evidence.

## Controller resource-bound change

The scope discovery loop previously performed up to three blocking `systemctl show` requests per uncached scope. With 64 candidate scopes and degraded D-Bus, that could stall one sampling cycle for an excessive time. It now batches `Id`, `Description`, `ActiveState`, and `ControlGroup` identity reads for all uncached scopes in one `systemctl show` command with a three-second timeout. A failed batch fails closed for that sample; scopes that disappear during the query are skipped and rediscovered on the next cycle. Deterministic tests verify one batched query, unavailable-batch fail-closed behavior, scope usage deltas, cgroup inode recreation, external weight changes, quota drift, and overflow handling.

The policy still considers only explicitly opted-in direct child scopes (maximum 64), measures real `cpu.stat usage_usec` deltas against the observed parent quota, requires at least two scopes at >=20% CPU each and >=85% aggregate quota for three samples, clears at <=65% for three samples or fewer than two contributors, and changes at most one scope's `CPUWeight` per sample. This is a relative fairness control, **not** a hard per-scope CPU cap. The parent quota is the hard aggregate limit. Missing/stale/reset telemetry, quota drift, or discovery failure fails closed; original weights are restored only on the same cgroup identity and only when no external override is detected.

## Current-revision paired benchmarks

All use two workload scopes, a temporary 100% parent CPU quota, a nested 100% heavy child, alternating baseline/router trial order, 30 seconds per mode, the common elapsed steady window `relative_s >= 12`, route convergence at least two seconds before that window, and at least 500 steady samples per mode. Predeclared evaluator limits remain median paired wake-up p95/p99 and probe-work p95 ratios <=1.10, throughput ratio >=0.90, router CPU <=5% of one logical CPU, peak RSS <=32768 KiB. Paired ratios, range/IQR and deterministic 10,000-resample bootstrap intervals are reported; intervals are descriptive and do not override the median gate.

### Two-worker synthetic integer-mixing workload — **FAIL**

Artifact family: [`routing-contention-scope-aggregate-synthetic-5rep30-pass12-2026-10-15`](routing-contention-scope-aggregate-synthetic-5rep30-pass12-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- All five router trials routed both worker scopes, converging at `8.513–8.702s`. Every pair had >=500 steady samples; router CPU was `0.264–0.364%` of one logical CPU and peak RSS `19,872–19,992 KiB`.
- Median paired steady wake-up p95 ratio `0.996` (range `0.991–1.014`, bootstrap 95% interval `[0.991,1.014]`); p99 `1.007` (range `0.975–1.084`, interval `[0.975,1.084]`); probe-work p95 `1.014` (range `0.727–1.478`, interval `[0.727,1.478]`).
- Median paired worker throughput ratio `0.880` (range `0.717–1.143`, interval `[0.717,1.143]`) **fails the predeclared >=0.90 gate**. Do not exclude individual pairs or change the limit after seeing this result. This is a real acceptance failure for this workload/host, even though latency medians pass.

### Generated 12-translation-unit compiler/build workload — **PASS, narrow scope**

Artifact family: [`routing-contention-scope-build-5rep30-pass12-2026-10-15`](routing-contention-scope-build-5rep30-pass12-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- Repeated compile-and-link of generated C source, not a build of the repository and not a claim about all compiler workloads. All five router trials routed both scopes before the common steady window.
- Median paired wake-up p95 ratio `1.000` (range `0.993–1.008`, interval `[0.993,1.008]`); p99 `0.992` (range `0.979–1.024`, interval `[0.979,1.024]`); probe-work p95 `0.998` (range `0.987–1.043`, interval `[0.987,1.043]`).
- Median compile/link-step throughput ratio `1.030` (range `0.831–1.036`, interval `[0.831,1.036]`). One pair regressed throughput by `16.9%`. Router CPU was `0.300–0.367%` of one logical CPU; peak RSS `19,816–20,016 KiB`.
- Evaluator passes the declared median gates, but the throughput interval includes a meaningful regression. This is not broad performance acceptance.

### GNU gzip `-1` CPU-bound CLI workload — **PASS, narrow scope**

Artifact family: [`routing-contention-scope-gzip-5rep30-pass12-2026-10-15`](routing-contention-scope-gzip-5rep30-pass12-2026-10-15.tsv), with adjacent `.samples.tsv`, `.environment.txt`, `.router.log`, and `.acceptance.txt`.

- Measures `gzip -1 -c /dev/zero` compressed output bytes/s. The repetitive input makes this a bounded CLI microbenchmark; do not generalize to other compression ratios or CLI workloads. All five router trials routed both scopes.
- Median paired wake-up p95 ratio `0.994` (range `0.982–1.020`, interval `[0.982,1.020]`); p99 `1.002` (range `0.983–1.018`, interval `[0.983,1.018]`); probe-work p95 `0.721` (range `0.688–0.999`, interval `[0.688,0.999]`).
- Median throughput ratio `0.992` (range `0.980–1.457`, interval `[0.980,1.457]`). The upper extreme is an outlier-like positive ratio; the median and all five ratios still meet the throughput floor. Router CPU was `0.265–0.364%` of one logical CPU; peak RSS `19,800–20,012 KiB`.
- Evaluator passes the declared median gates for this workload/host only.

## Local verification and runtime

Full local matrix: [`local-verification-pass12-2026-10-15.log`](local-verification-pass12-2026-10-15.log). `./test.sh`, mocked install/uninstall, routing/recovery/shadow/controller integration, live aggregate-scope integration, recovery unit, quota/routing/PSI/evaluator/soak safety tests, Python compilation, POSIX shell and Bash syntax, `systemd-analyze verify`, `git diff --check`, and version smoke all passed after the batch-discovery change. Live aggregate-scope integration used a temporary 100% parent cap, lowered both opted-in scopes to weight 20, left an unrelated scope at 100, then restored both workload scopes to 100.

The production user service remained active/running with MainPID `358400`, restart count `0`, and exit status `0`; user `heavy-workload.slice` remained at 100% (`CPUQuotaPerSecUSec=1s`), system-level heavy slice remained inactive/unlimited, route state had zero rows, and no temporary `lwgb*` unit remained. Aggregate routing remains disabled in installed production configuration. `VERSION=0.6.5-rc.1`.

## Release gates

| Gate | Status | Evidence / remaining work |
|---|---|---|
| Deterministic controller/fault/lifecycle/recovery tests | **PASS** | Full local matrix and hosted CI pass; batch identity-query failure now explicitly tested fail-closed. |
| Scope-aware aggregate route function under 100% parent cap | **PASS — function scope only** | All current-revision 5-pair workloads routed both scopes; live integration restored weights and preserved unrelated scope. |
| Aggregate-policy performance gate | **FAIL / OPEN** | Current-revision synthetic throughput median `0.880` fails the predeclared `0.90` floor. Build and gzip median gates pass, but pairwise variability remains. Investigate scheduler-weight tradeoffs without relaxing acceptance criteria. |
| Real repository build / interactive workload | **OPEN** | Current compiler workload is generated; no repository build or interactive CLI acceptance. |
| Ubuntu 24.04 hosted CI and live scope integration | **PASS** | Run `38720262761`. |
| Ubuntu 22.04 real delegated CPU counters | **SKIP / OPEN** | `cpu.stat` lacks `nr_periods`/`nr_throttled`; aggregate scope integration skips because CPU controller is not delegated. Need a properly provisioned runner and `WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1`. |
| Independent second host | **NOT RUN** | No independent host accessible. |
| Natural live PSI-high >=0.40 with correlated actuation | **NOT RUN** | No safe natural crossing observed; none induced. |
| Operator-level rollback on dedicated host | **PARTIAL** | Mocked install/uninstall and isolated recovery pass; dedicated-host operator procedure remains unrun. |
| RC soak minimum 24h / target 72h | **NOT RUN** | No dedicated safe test host/window. Monitor safety tests are not a soak. |
| Production runtime unchanged and artifacts intact | **PASS** | Post-test service/quota/route-state checks; unique artifact paths and checksum verification. |
| Release hygiene | **OPEN** | Keep `0.6.5-rc.1`; no stable tag/release. |

## Next actions

1. Investigate why the current synthetic two-worker throughput gate fails under the scope-weight policy. Do not lower the predeclared throughput floor or silently change `CPUWeight` to manufacture a pass; if a policy change is justified, document the hypothesis and run a new immutable paired set.
2. Validate the generated compiler and gzip results on a genuinely independent host and add a real repository build or interactive CLI workload where safely available.
3. Provision an Ubuntu 22.04 runner with real CPU delegation/counters; retain `SKIP` until the live test actually executes.
4. Observe natural PSI-high only if it occurs safely, execute operator rollback on a dedicated test host, and run a supervised continuous 24-hour minimum / 72-hour target RC soak with complete raw telemetry.

No gate is closed by a skip, a synthetic threshold injection, a single smoke, or a median alone. The synthetic performance failure keeps the overall performance release gate open.
