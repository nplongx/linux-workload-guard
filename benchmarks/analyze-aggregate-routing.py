#!/usr/bin/env python3
"""Compare preserved aggregate-routing pairs and synchronized cgroup telemetry.

Usage: analyze-aggregate-routing.py --out PAIRS.tsv --summary REPORT.txt RESULT.tsv...
Both outputs are create-only. Historical runs without interval telemetry remain
visible but cannot contribute CPU-normalized or PSI-confound analysis.
"""
import argparse
import csv
import importlib.util
import math
import pathlib
import random
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from evidence_integrity import digest_file, manifest_path as evidence_manifest_path, verify_manifest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_REVIEW_SPEC = importlib.util.spec_from_file_location(
    "aggregate_recomputed_performance_review", pathlib.Path(__file__).with_name("performance-review.py"))
_REVIEW_MODULE = importlib.util.module_from_spec(_REVIEW_SPEC)
_REVIEW_SPEC.loader.exec_module(_REVIEW_MODULE)


def read_tsv(path):
    with path.open(newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def manifest_values(path):
    values = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if "=" in line:
            key, value = line.split("=", 1)
            if key in values:
                raise ValueError(f"duplicate key {key!r} in {path} at line {line_number}")
            values[key] = value
    return values


def verify_acceptance_binding(result, manifest, acceptance_path, env, acceptance):
    """Reject stale/copy-pasted evaluator reports for another result artifact."""
    errors = []
    result = pathlib.Path(result)
    manifest = pathlib.Path(manifest)
    expected_paths = {
        "result_tsv": result,
        "raw_samples_tsv": result.with_suffix(".samples.tsv"),
        "manifest": manifest,
    }
    for key, expected in expected_paths.items():
        recorded = acceptance.get(key, "").strip()
        if not recorded:
            errors.append(f"acceptance report missing {key}")
            continue
        recorded_path = pathlib.Path(recorded)
        if not recorded_path.is_absolute():
            recorded_path = pathlib.Path.cwd() / recorded_path
        if recorded_path.resolve() != expected.resolve():
            errors.append(f"acceptance report {key} does not bind to supplied result")
    for key in ("git_revision", "host", "workload_kind", "duration_seconds", "workers"):
        if key not in acceptance or acceptance.get(key) != env.get(key):
            errors.append(f"acceptance report {key} does not match the current environment manifest")
    status = acceptance.get("acceptance_status")
    if status not in {"PASS", "FAIL", "INCOMPLETE", "CONFOUNDED_INCONCLUSIVE", "INCONCLUSIVE"}:
        errors.append("acceptance report has missing/unknown acceptance_status")

    if env.get("preregistration_required", "false").strip().lower() in ("1", "true", "yes", "on"):
        for key in ("preregistration_required", "preregistration_sha256", "preregistration_block"):
            if acceptance.get(key) != env.get(key):
                errors.append(f"acceptance report {key} does not match the sealed run manifest")

    checksum = evidence_manifest_path(result)
    recorded_checksum_digest = acceptance.get("evidence_checksum_manifest_sha256", "unavailable")
    preregistration_required = env.get("preregistration_required", "false").strip().lower() in ("1", "true", "yes", "on")
    recorded_checksum_path = acceptance.get("evidence_checksum_manifest", "unavailable").strip()
    if preregistration_required and recorded_checksum_digest in ("", "unavailable"):
        errors.append("preregistered acceptance report is missing the per-run evidence checksum digest")
    if preregistration_required and recorded_checksum_path in ("", "unavailable"):
        errors.append("preregistered acceptance report is missing the per-run evidence checksum path")
    if recorded_checksum_digest not in ("", "unavailable"):
        if not re.fullmatch(r"[0-9a-f]{64}", recorded_checksum_digest):
            errors.append("acceptance report has malformed evidence checksum manifest digest")
        elif checksum.is_symlink() or not checksum.is_file():
            errors.append("acceptance report references a missing/unsafe evidence checksum manifest")
        else:
            recorded_path = pathlib.Path(recorded_checksum_path)
            if not recorded_path.is_absolute():
                recorded_path = pathlib.Path.cwd() / recorded_path
            if recorded_path.resolve() != checksum.resolve():
                errors.append("acceptance report evidence checksum path does not bind to supplied result")
            if digest_file(checksum) != recorded_checksum_digest:
                errors.append("evidence checksum manifest digest differs from acceptance report")
            errors.extend(verify_manifest(result))
    elif checksum.exists() or checksum.is_symlink():
        errors.extend(verify_manifest(result))

    # An acceptance report cannot relabel PSI status independently of the
    # underlying referenced interval telemetry. Recalculate the noise state.
    try:
        repetitions = int(env.get("repetitions", ""))
        psi_status, _ = _REVIEW_MODULE.steady_host_psi_noise(env, manifest, repetitions)
        if acceptance.get("psi_noise_status") and acceptance.get("psi_noise_status") != psi_status:
            errors.append("acceptance report PSI classification differs from recomputed telemetry classification")
    except (TypeError, ValueError, KeyError) as exc:
        errors.append(f"could not recompute acceptance PSI classification: {exc}")
    return errors


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
        "host_psi_avg10_mean": None, "host_psi_avg10_median": None, "host_psi_avg10_max": None,
        "host_psi_gt5_pct": None, "parent_usage_cpu_s": None,
        "parent_nr_periods_delta": None, "parent_nr_throttled_delta": None,
        "parent_throttled_usec_delta": None, "workers_usage_cpu_s": None,
        "worker_throughput_raw": None, "throughput_per_cpu_s": None,
        "host_psi_status": "NOT_AVAILABLE",
        "workload_weight_values": "", "telemetry_rows": 0,
    }
    telemetry_rows = []
    if telemetry_path and telemetry_path.is_file():
        telemetry_rows = [row for row in read_tsv(telemetry_path)
                          if row.get("mode") == mode and row.get("rep") == str(rep)
                          and (number(row.get("elapsed_s")) or 0) >= 12.0]
        output["telemetry_rows"] = len(telemetry_rows)
        host_rows = [row for row in telemetry_rows if row.get("cgroup_kind") == "parent"]
        parsed_psi = [number(row.get("host_psi_some_avg10")) for row in host_rows]
        # The shared steady window starts at 12s of a 30s trial, so there are
        # normally about 18 one-second PSI samples. Requiring 20 *steady*
        # samples would incorrectly downgrade valid evaluator-checked runs.
        if len(host_rows) >= 10 and all(value is not None and 0.0 <= value <= 100.0 for value in parsed_psi):
            psi = parsed_psi
            output["host_psi_avg10_mean"] = statistics.mean(psi)
            output["host_psi_avg10_median"] = statistics.median(psi)
            output["host_psi_avg10_max"] = max(psi)
            output["host_psi_gt5_pct"] = 100.0 * sum(value > 5 for value in psi) / len(psi)
            output["host_psi_status"] = "VALID"
        elif host_rows:
            output["host_psi_status"] = "INVALID" if any(value is None or value < 0 or value > 100 for value in parsed_psi) else "INSUFFICIENT"

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


def psi_gate_status(rows_by_block, block_weights, expected_pairs=5):
    """Apply PSI controls per mode/pair; never average away a noisy pair.

    A single pair with >20% of samples above 5% PSI confounds causal
    interpretation. Missing per-pair telemetry is INCONCLUSIVE, not quiet.
    Condition imbalance is computed from the median of each pair's PSI median,
    so every paired repetition contributes one equally weighted observation.
    """
    required_blocks = tuple(block_weights)
    baseline_medians, router_medians = [], []
    observed_high = []
    missing = []
    for block in required_blocks:
        rows = rows_by_block.get(block, [])
        if len(rows) != expected_pairs:
            missing.append(f"{block}: expected {expected_pairs} pairs, observed {len(rows)}")
        for row in rows:
            rep = row.get("rep", "?")
            for mode, median_key, high_key, status_key in (
                ("baseline", "baseline_host_psi_avg10_median", "baseline_host_psi_gt5_pct", "baseline_host_psi_status"),
                ("router", "router_host_psi_avg10_median", "host_psi_router_gt5_pct", "router_host_psi_status"),
            ):
                value = row.get(median_key)
                high_pct = row.get(high_key)
                if row.get(status_key) != "VALID" or value is None or high_pct is None:
                    missing.append(f"{block}/rep{rep}/{mode}: host PSI series unavailable or incomplete")
                    continue
                if high_pct > 20.0:
                    observed_high.append(f"{block}/rep{rep}/{mode}={high_pct:.1f}% samples >5% PSI")
                (baseline_medians if mode == "baseline" else router_medians).append(value)
    imbalance = None
    if baseline_medians and router_medians:
        imbalance = abs(statistics.median(baseline_medians) - statistics.median(router_medians))
    if observed_high or (imbalance is not None and imbalance > 5.0):
        details = []
        if observed_high:
            details.append("per-pair confounding: " + ", ".join(observed_high))
        if imbalance is not None:
            details.append(f"baseline/router condition median imbalance={imbalance:.2f} percentage points")
        if missing:
            details.append("additional missing telemetry: " + "; ".join(missing))
        return "CONFOUNDED_INCONCLUSIVE", "; ".join(details), imbalance
    if missing:
        return "INCONCLUSIVE", "; ".join(missing), imbalance
    return "WITHIN_LIMIT", f"all per-pair PSI rates <=20%; condition median imbalance={imbalance:.2f} percentage points", imbalance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--summary", required=True, type=pathlib.Path)
    parser.add_argument("--require-preregistration", action="store_true",
                        help="require all four inputs to bind to one verified sealed ABBA protocol")
    parser.add_argument("results", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    if args.require_preregistration and len(args.results) != 4:
        print("release-gate ABBA analysis requires exactly four A/B/B2/A2 result files", file=sys.stderr)
        return 2
    for path in (args.out, args.summary):
        if path.exists():
            print(f"refusing to overwrite analysis evidence: {path}", file=sys.stderr)
            return 2
    pair_rows = []
    block_data = {}
    block_status = {}
    preregistration_bindings = []
    expected_blocks = ("A", "B", "B2", "A2")
    for input_index, result in enumerate(args.results):
        if result.is_symlink() or not result.is_file():
            print(f"missing or unsafe result TSV: {result}", file=sys.stderr)
            return 2
        manifest = result.with_suffix(".environment.txt")
        acceptance = result.with_suffix(".acceptance.txt")
        if (any(path.is_symlink() for path in (manifest, acceptance)) or
                not manifest.is_file() or not acceptance.is_file()):
            print(f"missing or unsafe manifest/evaluator output for {result}", file=sys.stderr)
            return 2
        try:
            env = manifest_values(manifest)
            acceptance_values = manifest_values(acceptance)
        except (OSError, ValueError, UnicodeError) as exc:
            print(f"invalid evidence manifest/report for {result}: {exc}", file=sys.stderr)
            return 2
        if args.require_preregistration:
            prereg_errors = _REVIEW_MODULE.validate_preregistration_manifest(env, required=True)
            if prereg_errors:
                print(f"release-gate preregistration invalid for {result}: " + "; ".join(prereg_errors), file=sys.stderr)
                return 2
            expected_block = expected_blocks[input_index]
            if env.get("preregistration_block") != expected_block:
                print(f"release-gate ABBA inputs must be ordered A/B/B2/A2; expected {expected_block} for {result}", file=sys.stderr)
                return 2
            name_match = re.search(r"weight(20|80)-block([AB]\d?)", result.name)
            weight_match = re.search(r"CPUWeight=(\d+) per sample", env.get("aggregate_routing", ""))
            expected_weight = 80 if expected_block in ("B", "B2") else 20
            if (not name_match or name_match.group(2) != expected_block or int(name_match.group(1)) != expected_weight
                    or not weight_match or int(weight_match.group(1)) != expected_weight):
                print(f"release-gate result filename/CPUWeight does not bind to block {expected_block}", file=sys.stderr)
                return 2
            preregistration_bindings.append((
                env.get("preregistration_sha256"), env.get("git_revision"), env.get("host"), env.get("workload_kind")
            ))
        binding_errors = verify_acceptance_binding(result, manifest, acceptance, env, acceptance_values)
        if binding_errors:
            print(f"invalid evaluator binding for {result}: " + "; ".join(binding_errors), file=sys.stderr)
            return 2
        if args.require_preregistration and acceptance_values.get("preregistration_gate_required") != "true":
            print(f"release-gate evaluator was not run with --require-preregistration for {result}", file=sys.stderr)
            return 2
        acceptance_status = acceptance_values["acceptance_status"]
        match = re.search(r"CPUWeight=(\d+) per sample", env.get("aggregate_routing", ""))
        weight = int(match.group(1)) if match else None
        block_match = re.search(r"weight(20|80)-block([AB]\d?)", result.name)
        block = block_match.group(2) if block_match else ("historical" if weight is None else "weight" + str(weight))
        if weight in (20, 80) and block in ("A", "A2", "B", "B2", "A3", "B3", "B4", "A4"):
            block_status[(block, weight)] = acceptance_status
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
                "baseline_host_psi_avg10_median": base_metrics["host_psi_avg10_median"],
                "baseline_host_psi_status": base_metrics["host_psi_status"],
                "router_host_psi_avg10_mean": router_metrics["host_psi_avg10_mean"],
                "router_host_psi_avg10_median": router_metrics["host_psi_avg10_median"],
                "router_host_psi_status": router_metrics["host_psi_status"],
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
            if weight in (20, 80) and block in ("A", "A2", "B", "B2", "A3", "B3", "B4", "A4"):
                block_data.setdefault((block, weight), []).append(throughput_ratio)

    if args.require_preregistration:
        if len(set(preregistration_bindings)) != 1:
            print("release-gate ABBA inputs do not share one sealed protocol digest, source revision, host and workload", file=sys.stderr)
            return 2
        if len({row["block"] for row in pair_rows}) != 4:
            print("release-gate ABBA inputs do not contain four distinct bound blocks", file=sys.stderr)
            return 2

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
        f"release_gate_preregistration_required={'true' if args.require_preregistration else 'false'}",
        (f"release_gate_preregistration_sha256={preregistration_bindings[0][0]}" if args.require_preregistration
         else "release_gate_preregistration_sha256=not-required-descriptive-mode"),
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
    designs = {
        "pass14": ("A", "B", "B2", "A2"),
        "pass15": ("A3", "B3", "B4", "A4"),
    }
    design_results = {}
    for design, (a_key, b_key, b2_key, a2_key) in designs.items():
        values20 = [value for key in ((a_key, 20), (a2_key, 20)) for value in block_data.get(key, []) if value is not None]
        values80 = [value for key in ((b_key, 80), (b2_key, 80)) for value in block_data.get(key, []) if value is not None]
        med20 = statistics.median(values20) if values20 else None
        med80 = statistics.median(values80) if values80 else None
        contrast1 = statistics.median(block_data[(b_key, 80)]) - statistics.median(block_data[(a_key, 20)]) if block_data.get((b_key, 80)) and block_data.get((a_key, 20)) else None
        contrast2 = statistics.median(block_data[(b2_key, 80)]) - statistics.median(block_data[(a2_key, 20)]) if block_data.get((b2_key, 80)) and block_data.get((a2_key, 20)) else None
        weight80_statuses_pass = all(block_status.get((key, 80)) == "PASS" for key in (b_key, b2_key))
        weight80_block_medians_pass = all(block_data.get((key, 80)) and statistics.median(block_data[(key, 80)]) >= .90 for key in (b_key, b2_key))
        no_severe_pair = bool(values80) and all(value >= .80 for value in values80)
        effect_size_pass = med20 is not None and med80 is not None and med80 - med20 >= .05
        contrasts_pass = contrast1 is not None and contrast2 is not None and contrast1 > 0 and contrast2 > 0
        block_weights = {a_key: 20, b_key: 80, b2_key: 80, a2_key: 20}
        rows_by_block = {
            key: [row for row in pair_rows if row["block"] == key and row["weight"] == weight]
            for key, weight in block_weights.items()
        }
        psi_status, psi_detail, psi_imbalance = psi_gate_status(rows_by_block, block_weights)
        psi_confounded = psi_status == "CONFOUNDED_INCONCLUSIVE"
        psi_complete = psi_status == "WITHIN_LIMIT"
        lines.append(f"{design}_psi_noise_status={psi_status}")
        lines.append(f"{design}_psi_noise_detail={psi_detail}")
        lines.append(f"{design}_baseline_router_condition_median_imbalance_points={psi_imbalance if psi_imbalance is not None else 'unavailable'}")
        criteria_pass = (weight80_statuses_pass and weight80_block_medians_pass and no_severe_pair
                         and effect_size_pass and contrasts_pass and psi_complete)
        interpretation = ("CONFOUNDED_INCONCLUSIVE" if psi_confounded else
                          "INCONCLUSIVE" if not psi_complete else
                          "CONSISTENT_WITH_H1" if criteria_pass else
                          "H1_NOT_SUPPORTED_BY_PREDECLARED_CRITERIA")
        design_results[design] = interpretation
        lines.append(f"{design}_weight20_n={len(values20)} median={med20 if med20 is not None else 'unavailable'}")
        lines.append(f"{design}_weight80_n={len(values80)} median={med80 if med80 is not None else 'unavailable'}")
        if med20 is not None and med80 is not None:
            lines.append(f"{design}_weight80_minus_weight20_median_delta={med80-med20:.3f} required_for_H1>=0.050")
        if contrast1 is not None and contrast2 is not None:
            lines.append(f"{design}_contrast_{b_key}_minus_{a_key}={contrast1:.3f} (must be >0 for weight80 to favor throughput)")
            lines.append(f"{design}_contrast_{b2_key}_minus_{a2_key}={contrast2:.3f} (must be >0 for weight80 to favor throughput)")
        lines.append(f"{design}_predeclared_weight_effect_interpretation={interpretation}")
        lines.append(f"{design}_both_weight80_blocks_pass={weight80_statuses_pass}")
        lines.append(f"{design}_both_weight80_block_medians_ge_0.90={weight80_block_medians_pass}")
        lines.append(f"{design}_no_weight80_pair_below_0.80={no_severe_pair}")
        lines.append(f"{design}_median_delta_ge_0.05={effect_size_pass}")
        lines.append(f"{design}_both_order_balanced_contrasts_favor_weight80={contrasts_pass}")
    lines.extend(["", "Telemetry interpretation:"])
    for block, weight in (("A", 20), ("B", 80), ("B2", 80), ("A2", 20),
                          ("A3", 20), ("B3", 80), ("B4", 80), ("A4", 20)):
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
    for design, interpretation in design_results.items():
        lines.append(f"decision_{design}={interpretation}")
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
