# Aggregate routing follow-up results — pass 15

Date: 2026-10-15. Protocol preregistered in [`aggregate-throughput-investigation-pass15.md`](aggregate-throughput-investigation-pass15.md) and committed as `8b7effc5608f7cbfc29c414a59bf72ba53283c70` before any pass-15 benchmark block. This is a new evidence set; pass-13 and pass-14 files are unchanged.

## Execution and integrity

Four additional ABBA-ordered blocks were run on the same x86-64 Linux host, with two Python integer-mixing workers, a 20 ms probe, five paired repetitions per block, 30 seconds per mode, alternating baseline/router order, a common steady window beginning at 12 seconds, and the unchanged evaluator. Every block used the same isolated cgroup-v2 parent with `CPUQuota=100%`; only the isolated workload scope's test-only `CPUWeight` varied. Dynamic quota actuation was disabled. The installed production service, configuration, threshold and quota were not changed.

Each block has an environment manifest, raw probe samples, raw per-worker throughput counters, one-second synchronized cgroup telemetry, router log, evaluator output and a preflight record. The evaluator validated telemetry coverage and recomputed throughput from raw per-worker counters. The analysis reran across the pass-9, pass-12, pass-13, pass-14 smoke, pass-14 ABBA, pass-14 repository/CLI and pass-15 files: 14 source result files and 62 paired rows; every pair was retained. The preserved full analysis includes all 62 rows. The CI regression test independently reruns the four Pass 15 blocks (20 paired rows) so it does not depend on untracked historical Pass 9–14 artifacts; it asserts the B4 failure, both Pass 15 contrasts and confounded interpretation.

## Pass-15 block results

| Block | Test-only weight | Evaluator | All five paired throughput ratios | Median | Range | Pairs below 0.90 |
|---|---:|---|---|---:|---:|---:|
| A3 | 20 | PASS | 1.033, 0.947, 1.073, 1.110, 1.264 | 1.073 | 0.947–1.264 | 0/5 |
| B3 | 80 | PASS | 0.983, 1.190, 0.779, 0.957, 1.322 | 0.983 | 0.779–1.322 | 1/5 |
| B4 | 80 | **FAIL** | 0.929, 1.035, 0.824, 0.708, 0.671 | 0.824 | 0.671–1.035 | 3/5 |
| A4 | 20 | PASS | 0.988, 1.062, 0.996, 0.848, 0.858 | 0.988 | 0.848–1.062 | 2/5 |

The ten weight-20 pairs have median `1.014`; the ten weight-80 pairs have median `0.943`, a difference of `-0.071` (required direction for H1: at least `+0.050`). The order-balanced contrasts are `B3−A3 = -0.090` and `B4−A4 = -0.165`; both favor weight 20, not weight 80. Block B4 failed the unchanged `0.90` median throughput gate. Weight 80 also produced three pairs below `0.80` across the two blocks (`0.779`, `0.708`, `0.671`). The predeclared material weight-improvement hypothesis therefore fails its throughput and repeatability criteria.

Median latency ratios for A3/B3/B4/A4 were wake-up p95 `0.997/0.996/1.007/0.996`, wake-up p99 `0.990/0.987/1.013/1.000`, and probe-work p95 `0.971/0.969/1.010/1.078`. Router CPU remained about `0.265–0.300%` of one logical CPU and peak RSS about `19.8–20.0 MiB`. Individual adverse pairs are retained: B4's last two pairs had probe-work p95 ratios `1.458` and `1.474`, while throughput ratios were `0.708` and `0.671`.

## Synchronized telemetry and confounding

For all four blocks, the median steady-window parent cgroup consumed approximately `17.0 CPU-seconds` over 17 seconds and `nr_throttled` increased by `170` over `170` periods in both baseline and router modes. This confirms that the isolated parent was saturated at the same 100% aggregate ceiling in both conditions. Worker-scope CPU usage was similar, but median throughput per worker CPU-second ratios were A3 `1.065`, B3 `0.976`, B4 `0.827`, and A4 `0.988`. Readback confirmed baseline workload weight `100` and router-mode weights matching the intended candidate: `20` for A3/A4 and `80` for B3/B4. The router process overhead was far below the 5% gate, so measured router CPU consumption is not a plausible primary explanation for B4's throughput deficit.

The low-PSI preflight passed before each block, but host pressure rose during measurement: median host CPU PSI `some avg10` means were approximately `28.26`/`29.75` for A3 baseline/router, `27.09`/`27.49` for B3, `28.95`/`29.53` for B4, and `28.21`/`28.71` for A4. **100% of steady telemetry intervals exceeded the protocol's 5% PSI confounding line.** This indicates the host became contended after preflight. Per protocol, the runs were completed without dropping pairs and the causal interpretation is `CONFOUNDED_INCONCLUSIVE`.

Analysis provenance note: the first generated summary (`routing-contention-aggregate-analysis-pass15-2026-10-15.*`) is retained but superseded because a grouping bug omitted PSI rows for the new blocks. The correction is documented in `pass15-analysis-correction-2026-10-15.txt`; corrected outputs are `routing-contention-aggregate-analysis-pass15b-2026-10-15.*`, and `tests/analyze-aggregate-routing.sh` now asserts the pass-14/pass-15 PSI-confounded decisions and block pairing counts. No raw sample or evaluator output was changed.

The telemetry demonstrates real parent quota saturation and the expected effective weight readback; it does not demonstrate that weight 80 causes the throughput loss, nor that weight 20 is universally faster. Across pass 14 and pass 15, both ABBA comparisons trend against weight 80, but the host-PSI rule blocks causal attribution. Do not change controller or routing policy from these single-host synthetic runs. A valid causal follow-up requires a genuinely quieter, independently controlled host and replication of the same preregistered protocol.

## Roadmap gate status

- Pass-15 synthetic A3: **PASS** for the declared workload/host; B3: **PASS**; B4: **FAIL**; A4: **PASS**. The aggregate performance investigation remains **OPEN** and the weight-effect result is **CONFOUNDED_INCONCLUSIVE**.
- Local real-repository compileall plus read-only CLI workload from pass 14: **PASS** for that declared workload on this host only. The full `./test.sh` regression suite and the new analyzer regression test passed in pass 15.
- Ubuntu 24.04 integration: retain previous **PASS**. Ubuntu 22.04 delegated CPU telemetry: **SKIP/OPEN** because the available delegated environment lacks genuine required counters/controller delegation; no telemetry is fabricated.
- Independent-host validation: **NOT RUN** (no second authorized host available).
- Natural live production-controller PSI-high crossing at `0.40`: **NOT RUN**. Pass-14/15 benchmark telemetry saw host PSI rise, but it remained below the production threshold and the benchmark did not actuate production quota. No pressure was forced.
- Dedicated-host operator rollback: **PARTIAL/OPEN**; isolated/mock recovery evidence is not a dedicated-host drill.
- RC soak: **NOT RUN**; minimum 24 hours and target 72 hours require a persistent monitor and representative workload on an authorized test host.
- Release hygiene: **OPEN**. `VERSION=0.6.5-rc.1`; no `v1.0.0` tag or stable release.

The SHA-256 manifest `benchmarks/results/pass15-artifact-sha256sums-2026-10-15.txt` covers this pass's protocol, block artifacts, analysis outputs, gate-status record and report. Production `workload-router.service` remained active with restart count zero; `heavy-workload.slice` remained at 100% (`CPUQuotaPerSecUSec=1s`).
