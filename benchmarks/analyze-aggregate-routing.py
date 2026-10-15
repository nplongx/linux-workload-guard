#!/usr/bin/env python3
"""Compare preserved aggregate-routing pairs and synchronized cgroup telemetry.

Usage: analyze-aggregate-routing.py --out PAIRS.tsv --summary REPORT.txt RESULT.tsv...
Both outputs are create-only. Historical runs without interval telemetry remain
visible but cannot contribute CPU-normalized or PSI-confound analysis.
"""
import argparse
import csv
import math
import pathlib
import random
import re
import statistics
import sys


def read_tsv(path):
    with path.open(newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def manifest_values(path):
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def median_ci(values, seed=20261015, samples=10000):
    if not values:
        return None, None
    rng = random.Random(seed)
    medians = [statistics.median(rng.choice(values) for _ in values) for _ in range(samples)]
    ordered = sorted(medians)
    return ordered[max(0, math.ceil(.025 * len(ordered)) - 1)], ordered[min(len(ordered)-1, math.ceil(.975 * len(ordered))-1)]


def interval_metrics(result, env, mode, rep):
    telemetry_value = env.get("interval_telemetry", "").split(";", 1)[0].strip()
    throughput_value = env.get("throughput_raw", "").split(";", 1)[0].strip()
    telemetry_path = pathlib.Path(telemetry_value) if telemetry_value else None
    throughput_path = pathlib.Path(throughput_value) if throughput_value else None
    for item in (telemetry_path, throughput_path):
        if item is not None and not item.is_file() and not item.is_absolute():
            fallback = result.parent / item.name
            if fallback.is_file():
                if item == telemetry_path:
                    telemetry_path = fallback
                else:
                    throughput_path = fallback
    output = {
        "host_psi_avg10_mean": None, "host_psi_avg10_max": None,
        "host_psi_gt5_pct": None, "parent_usage_cpu_s": None,
        "parent_nr_periods_delta": None, "parent_nr_throttled_delta": None,
        "parent_throttled_usec_delta": None, "workers_usage_cpu_s": None,
        "worker_throughput_raw": None, "throughput_per_cpu_s": None,
        "workload_weight_values": "", "telemetry_rows": 0,
    }
    telemetry_rows = []
    if telemetry_path and telemetry_path.is_file():
        telemetry_rows = [row for row in read_tsv(telemetry_path)
                          if row.get("mode") == mode and row.get("rep") == str(rep)
                          and (number(row.get("elapsed_s")) or 0) >= 12.0]
        output["telemetry_rows"] = len(telemetry_rows)
        host_rows = [row for row in telemetry_rows if row.get("cgroup_kind") == "parent"]
        psi = [number(row.get("host_psi_some_avg10")) for row in host_rows]
        psi = [value for value in psi if value is not None]
        if psi:
            output["host_psi_avg10_mean"] = statistics.mean(psi)
            output["host_psi_avg10_max"] = max(psi)
            output["host_psi_gt5_pct"] = 100.0 * sum(value > 5 for value in psi) / len(psi)

        def counter_delta(kind, unit=None, field="usage_usec"):
            rows = [row for row in telemetry_rows if row.get("cgroup_kind") == kind
                    and (unit is None or row.get("unit") == unit)]
            rows = sorted(rows, key=lambda row: float(row["elapsed_s"]))
            values = [(number(row.get("elapsed_s")), number(row.get(field))) for row in rows]
            values = [(ts, value) for ts, value in values if ts is not None and value is not None]
            if len(values) < 2:
                return None
            delta = values[-1][1] - values[0][1]
            return delta if delta >= 0 else None

        for field, output_key in (("usage_usec", "parent_usage_cpu_s"),
                                  ("nr_periods", "parent_nr_periods_delta"),
                                  ("nr_throttled", "parent_nr_throttled_delta"),
                                  ("throttled_usec", "parent_throttled_usec_delta")):
            delta = counter_delta("parent", field=field)
            if delta is not None:
                output[output_key] = delta / 1_000_000 if field in ("usage_usec", "throttled_usec") else delta

        worker_units = sorted({row.get("unit", "") for row in telemetry_rows if row.get("cgroup_kind") == "workload"})
        worker_usages = [counter_delta("workload", unit, "usage_usec") for unit in worker_units]
        if worker_usages and all(value is not None for value in worker_usages):
            output["workers_usage_cpu_s"] = sum(worker_usages) / 1_000_000
        weights = sorted({row.get("cpu_weight", "") for row in telemetry_rows
                          if row.get("cgroup_kind") == "workload" and row.get("cpu_weight", "").isdigit()},
                         key=lambda value: int(value))
        output["workload_weight_values"] = ",".join(weights)

    if throughput_path and throughput_path.is_file():
        raw = [row for row in read_tsv(throughput_path)
               if row.get("mode") == mode and row.get("rep") == str(rep)]
        worker_rates = []
        for worker in sorted({row.get("worker") for row in raw}):
            values = sorted((number(row.get("elapsed_s")), number(row.get("counter")), row.get("metric_kind"))
                            for row in raw if row.get("worker") == worker)
            values = [row for row in values if row[0] is not None and row[1] is not None and row[0] >= 12]
            if not values:
                continue
            if values[-1][2] == "final_reported_rate":
                worker_rates.append(values[-1][1])
            elif len(values) >= 2 and values[-1][0] > values[0][0] and values[-1][1] >= values[0][1]:
                worker_rates.append((values[-1][1] - values[0][1]) / (values[-1][0] - values[0][0]))
        if worker_rates:
            output["worker_throughput_raw"] = sum(worker_rates)
            cpu = output["workers_usage_cpu_s"]
            if cpu and cpu > 0:
                output["throughput_per_cpu_s"] = output["worker_throughput_raw"] / cpu
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--summary", required=True, type=pathlib.Path)
    parser.add_argument("results", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    for path in (args.out, args.summary):
        if path.exists():
            print(f"refusing to overwrite analysis evidence: {path}", file=sys.stderr)
            return 2
    pair_rows = []
    block_data = {}
    for result in args.results:
        if not result.is_file():
            print(f"missing result TSV: {result}", file=sys.stderr)
            return 2
        manifest = result.with_suffix(".environment.txt")
        acceptance = result.with_suffix(".acceptance.txt")
        if not manifest.is_file() or not acceptance.is_file():
            print(f"missing manifest/evaluator output for {result}", file=sys.stderr)
            return 2
        env = manifest_values(manifest)
        acceptance_status = manifest_values(acceptance).get("acceptance_status", "UNKNOWN")
        match = re.search(r"CPUWeight=(\d+) per sample", env.get("aggregate_routing", ""))
        weight = int(match.group(1)) if match else None
        block_match = re.search(r"weight(20|80)-block(A2?|B2?)", result.name)
        block = block_match.group(2) if block_match else ("historical" if weight is None else "weight" + str(weight))
        rows = read_tsv(result)
        by_mode_rep = {(row.get("mode"), row.get("rep")): row for row in rows}
        reps = sorted({int(row["rep"]) for row in rows if row.get("rep", "").isdigit()})
        for rep in reps:
            baseline = by_mode_rep.get(("baseline", str(rep)))
            router = by_mode_rep.get(("router", str(rep)))
            if not baseline or not router:
                continue
            ratio = lambda key: (number(router.get(key)) / number(baseline.get(key))
                                 if number(router.get(key)) and number(baseline.get(key)) else None)
            base_metrics = interval_metrics(result, env, "baseline", rep)
            router_metrics = interval_metrics(result, env, "router", rep)
            throughput_ratio = ratio("worker_throughput_per_sec")
            cpu_efficiency_ratio = (router_metrics["throughput_per_cpu_s"] / base_metrics["throughput_per_cpu_s"]
                                    if router_metrics["throughput_per_cpu_s"] and base_metrics["throughput_per_cpu_s"] else None)
            pair_rows.append({
                "result": str(result), "block": block, "weight": weight or "legacy", "acceptance_status": acceptance_status,
                "duration_seconds": env.get("duration_seconds", ""), "repetitions": env.get("repetitions", ""),
                "workers": env.get("workers", ""), "workload_kind": env.get("workload_kind", ""),
                "rep": rep, "baseline_throughput": baseline.get("worker_throughput_per_sec"),
                "router_throughput": router.get("worker_throughput_per_sec"),
                "throughput_ratio": throughput_ratio, "wake_p95_ratio": ratio("steady_p95_ms"),
                "wake_p99_ratio": ratio("steady_p99_ms"), "probe_work_p95_ratio": ratio("steady_work_p95_ms"),
                "baseline_host_psi_avg10_mean": base_metrics["host_psi_avg10_mean"],
                "router_host_psi_avg10_mean": router_metrics["host_psi_avg10_mean"],
                "baseline_host_psi_gt5_pct": base_metrics["host_psi_gt5_pct"],
                "host_psi_router_gt5_pct": router_metrics["host_psi_gt5_pct"],
                "baseline_parent_usage_cpu_s": base_metrics["parent_usage_cpu_s"],
                "router_parent_usage_cpu_s": router_metrics["parent_usage_cpu_s"],
                "baseline_parent_nr_periods_delta": base_metrics["parent_nr_periods_delta"],
                "router_parent_nr_periods_delta": router_metrics["parent_nr_periods_delta"],
                "baseline_parent_nr_throttled_delta": base_metrics["parent_nr_throttled_delta"],
                "router_parent_nr_throttled_delta": router_metrics["parent_nr_throttled_delta"],
                "baseline_parent_throttled_usec_delta": base_metrics["parent_throttled_usec_delta"],
                "router_parent_throttled_usec_delta": router_metrics["parent_throttled_usec_delta"],
                "baseline_workers_usage_cpu_s": base_metrics["workers_usage_cpu_s"],
                "router_workers_usage_cpu_s": router_metrics["workers_usage_cpu_s"],
                "baseline_throughput_per_cpu_s": base_metrics["throughput_per_cpu_s"],
                "router_throughput_per_cpu_s": router_metrics["throughput_per_cpu_s"],
                "cpu_normalized_efficiency_ratio": cpu_efficiency_ratio,
                "baseline_weights": base_metrics["workload_weight_values"],
                "router_weights": router_metrics["workload_weight_values"],
                "router_at_s": router.get("routed_at_s"), "router_cpu_pct_one_core": router.get("router_cpu_pct_one_core"),
                "router_rss_kib": router.get("router_peak_rss_kib"),
            })
            if weight in (20, 80) and block in ("A", "A2", "B", "B2"):
                block_data.setdefault((block, weight), []).append(throughput_ratio)

    if not pair_rows:
        print("no paired rows found", file=sys.stderr)
        return 2
    fields = list(pair_rows[0])
    try:
        with args.out.open("x", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(pair_rows)
    except FileExistsError:
        print(f"refusing to overwrite analysis evidence: {args.out}", file=sys.stderr)
        return 2

    lines = [
        "Aggregate routing comparison — descriptive paired evidence",
        f"source_result_files={len(args.results)}",
        f"paired_rows={len(pair_rows)}",
        "acceptance_thresholds=unchanged: median throughput ratio >=0.90; median latency/work ratios <=1.10",
        "all_pairs_retained=true",
        "interpretation=single-host synthetic workload; not production policy acceptance",
        "",
        "Per block:",
    ]
    for (block, weight), values in sorted(block_data.items()):
        vals = [value for value in values if value is not None]
        if not vals:
            continue
        low, high = median_ci(vals)
        lines.append(f"block={block} weight={weight} n={len(vals)} median={statistics.median(vals):.3f} "
                     f"range=[{min(vals):.3f},{max(vals):.3f}] below_0.90={sum(value < .90 for value in vals)} "
                     f"bootstrap95CI_median=[{low:.3f},{high:.3f}]")
    for weight in (20, 80):
        vals = [row["throughput_ratio"] for row in pair_rows
                if row["weight"] == weight and row["throughput_ratio"] is not None
                and row["acceptance_status"] in ("PASS", "FAIL")
                and row["duration_seconds"] == "30.0" and row["repetitions"] == "5"
                and row["workers"] == "2" and row["workload_kind"] == "synthetic"]
        if vals:
            low, high = median_ci(vals)
            lines.append(f"pooled_weight={weight} n={len(vals)} median={statistics.median(vals):.3f} "
                         f"range=[{min(vals):.3f},{max(vals):.3f}] below_0.90={sum(value < .90 for value in vals)} "
                         f"bootstrap95CI_median=[{low:.3f},{high:.3f}]")
    lines.extend(["", "Telemetry interpretation:"])
    for block, weight in (("A", 20), ("B", 80), ("B2", 80), ("A2", 20)):
        values = [row for row in pair_rows if row["block"] == block and row["weight"] == weight]
        psi_base = [row["baseline_host_psi_avg10_mean"] for row in values if row["baseline_host_psi_avg10_mean"] is not None]
        psi_router = [row["router_host_psi_avg10_mean"] for row in values if row["router_host_psi_avg10_mean"] is not None]
        psi_high = [row[key] for row in values for key in ("baseline_host_psi_gt5_pct", "host_psi_router_gt5_pct") if row[key] is not None]
        ratios = [row["cpu_normalized_efficiency_ratio"] for row in values if row["cpu_normalized_efficiency_ratio"] is not None]
        if values:
            psi_base_mean = f"{statistics.mean(psi_base):.2f}" if psi_base else "unavailable"
            psi_router_mean = f"{statistics.mean(psi_router):.2f}" if psi_router else "unavailable"
            psi_high_mean = f"{statistics.mean(psi_high):.1f}" if psi_high else "unavailable"
            efficiency_median = f"{statistics.median(ratios):.3f}" if ratios else "unavailable"
            lines.append(f"block={block} weight={weight} baseline_host_psi_mean_avg10={psi_base_mean} "
                         f"router_host_psi_mean_avg10={psi_router_mean} samples_gt5pct_mean={psi_high_mean} "
                         f"cpu_normalized_efficiency_median={efficiency_median}")
    lines.extend([
        "",
        "Decision rule: weight effect is not causal unless both weight-80 blocks independently pass, pooled weight-80 median exceeds pooled weight-20 median by >=0.05, both ABBA contrasts favor weight 80, no weight-80 pair is <0.80, latency/overhead gates pass, and host PSI confounding rule is satisfied.",
        "Any host PSI >5% in >20% of samples or condition median imbalance >5 percentage points makes causal interpretation confounded/inconclusive; no sample is discarded.",
    ])
    try:
        with args.summary.open("x") as output:
            output.write("\n".join(lines) + "\n")
    except FileExistsError:
        print(f"refusing to overwrite analysis evidence: {args.summary}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
