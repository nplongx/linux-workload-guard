#!/usr/bin/env python3
"""Evaluate paired routing benchmark evidence against predeclared RC limits.

Usage: performance-review.py RESULT.tsv [REPORT.txt]
Writes RESULT.acceptance.txt (or the explicit report path) without overwriting prior evidence. A PASS applies
only to the declared workload and host; it does not close general representative-workload gates.
"""
import csv
import math
import pathlib
import random
import statistics
import sys

MIN_PAIRS = 5
MIN_STEADY_SAMPLES = 500
MAX_LATENCY_RATIO = 1.10
MIN_THROUGHPUT_RATIO = 0.90
MAX_ROUTER_CPU_PERCENT_ONE_CORE = 5.0
MAX_ROUTER_RSS_KIB = 32768
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261015


def finite_positive(row, key):
    try:
        value = float(row[key])
        return value if math.isfinite(value) and value > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def finite_nonnegative(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def read_raw_samples(path, max_duration_s):
    """Read and validate the probe-level evidence, not just summary claims."""
    required = {"mode", "rep", "relative_s", "wakeup_lateness_ms", "work_duration_ms"}
    with path.open(newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("raw sample schema is missing required columns")
        groups = {}
        previous_time = {}
        for line_number, row in enumerate(reader, 2):
            key = (row.get("mode", ""), row.get("rep", ""))
            if key[0] not in ("baseline", "router") or not key[1].isdigit() or int(key[1]) < 1:
                raise ValueError(f"raw sample row {line_number} has invalid mode/repetition")
            if any(finite_nonnegative(row.get(field)) is None for field in
                   ("relative_s", "wakeup_lateness_ms", "work_duration_ms")):
                raise ValueError(f"raw sample row {line_number} has negative/non-finite measurement")
            sample_time = float(row["relative_s"])
            if sample_time > max_duration_s + 1.0:
                raise ValueError(f"raw sample row {line_number} exceeds declared trial duration")
            if key in previous_time and sample_time <= previous_time[key]:
                raise ValueError(f"raw sample timestamps are not strictly increasing for {key}")
            previous_time[key] = sample_time
            groups.setdefault(key, []).append((sample_time, float(row["wakeup_lateness_ms"]),
                                               float(row["work_duration_ms"])))
    if not groups:
        raise ValueError("raw sample file contains no observations")
    return groups


def acceptance_limits_error(min_pairs=MIN_PAIRS, min_samples=MIN_STEADY_SAMPLES,
                            max_latency=MAX_LATENCY_RATIO, min_throughput=MIN_THROUGHPUT_RATIO,
                            max_cpu=MAX_ROUTER_CPU_PERCENT_ONE_CORE, max_rss=MAX_ROUTER_RSS_KIB):
    numeric = (max_latency, min_throughput, max_cpu, max_rss)
    if not isinstance(min_pairs, int) or min_pairs < 2 or not isinstance(min_samples, int) or min_samples < 1:
        return "acceptance sample-count limits must be positive (at least two pairs)"
    if not all(math.isfinite(value) for value in numeric):
        return "acceptance numeric limits must be finite"
    if max_latency <= 0 or min_throughput <= 0 or max_cpu <= 0 or max_rss <= 0:
        return "acceptance numeric limits must be positive"
    return None


def nearest_rank(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * p) - 1))]


def ratio_variation(values):
    """Descriptive uncertainty only; the predeclared decision remains median-based."""
    rng = random.Random(BOOTSTRAP_SEED)
    medians = [statistics.median(rng.choice(values) for _ in values) for _ in range(BOOTSTRAP_RESAMPLES)]
    return {
        "minimum": min(values),
        "q1": nearest_rank(values, 0.25),
        "q3": nearest_rank(values, 0.75),
        "maximum": max(values),
        "ci_low": nearest_rank(medians, 0.025),
        "ci_high": nearest_rank(medians, 0.975),
    }


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: performance-review.py RESULT.tsv [REPORT.txt]", file=sys.stderr)
        return 2
    limits_error = acceptance_limits_error()
    if limits_error:
        print(f"INCOMPLETE: invalid predeclared acceptance limits: {limits_error}", file=sys.stderr)
        return 2
    result = pathlib.Path(sys.argv[1])
    manifest = result.with_suffix(".environment.txt")
    raw_path = result.with_suffix(".samples.tsv")
    report = pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else result.with_suffix(".acceptance.txt")
    if not result.is_file() or not manifest.is_file() or not raw_path.is_file():
        print("INCOMPLETE: result TSV, raw samples, or environment manifest missing", file=sys.stderr)
        return 2
    if report.exists():
        print(f"refusing to overwrite acceptance report: {report}", file=sys.stderr)
        return 2
    env = {}
    for line in manifest.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    findings = []
    status = "INCOMPLETE"
    manifest_required = ("git_revision", "host", "python", "cpu_model", "logical_cpus", "systemd",
                         "cgroup_mode", "cgroup_controllers", "workload_kind", "repetitions", "workers",
                         "duration_seconds", "parent_cpu_quota", "result_status")
    missing_manifest = [key for key in manifest_required if not env.get(key, "").strip()]
    if missing_manifest:
        print("INCOMPLETE: manifest missing required metadata: " + ", ".join(missing_manifest), file=sys.stderr)
        return 2
    if env.get("workload_kind") not in ("synthetic", "terminal-openssl", "terminal-gzip", "terminal-build"):
        print("INCOMPLETE: manifest workload_kind is missing or unsupported", file=sys.stderr)
        return 2
    try:
        declared_duration = float(env["duration_seconds"])
        declared_repetitions = int(env["repetitions"])
        declared_workers = int(env["workers"])
        declared_cpus = int(env["logical_cpus"])
        if (not math.isfinite(declared_duration) or declared_duration < 20 or declared_repetitions < 1
                or declared_workers < 1 or declared_cpus < 1 or env["cgroup_mode"] != "v2"):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        print("INCOMPLETE: invalid duration/repetition/worker/CPU/cgroup metadata", file=sys.stderr)
        return 2
    try:
        raw_groups = read_raw_samples(raw_path, declared_duration)
    except (OSError, ValueError) as exc:
        print(f"INCOMPLETE: raw sample validation failed: {exc}", file=sys.stderr)
        return 2
    with result.open(newline="") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    required = {"mode", "rep", "samples", "routed_at_s", "trial_valid", "workers_in_heavy", "worker_count", "steady_n",
                "all_mean_ms", "all_p50_ms", "all_p95_ms", "all_p99_ms", "all_max_ms", "work_p95_ms",
                "steady_mean_ms", "steady_p50_ms", "steady_p95_ms", "steady_p99_ms", "steady_max_ms",
                "steady_work_p95_ms", "worker_iterations_per_sec", "worker_throughput_per_sec",
                "router_cpu_pct_one_core", "router_peak_rss_kib"}
    if not rows or not required.issubset(rows[0]):
        findings.append("INCOMPLETE: benchmark schema/rows lack required fields")
    elif env.get("result_status") != "COMPLETE":
        findings.append("INCOMPLETE: benchmark manifest result_status is not COMPLETE")
    else:
        malformed = False
        try:
            reps = sorted({int(row["rep"]) for row in rows})
            if any(not row["rep"].isdigit() or int(row["rep"]) < 1 for row in rows):
                raise ValueError
        except (KeyError, ValueError):
            reps = []
            malformed = True
            findings.append("INCOMPLETE: summary contains invalid repetition identifiers")
        pairs = []
        if any(row["mode"] not in ("baseline", "router") for row in rows):
            malformed = True
            findings.append("INCOMPLETE: unexpected benchmark mode in result TSV")
        if len(rows) != 2 * len(reps):
            malformed = True
            findings.append("INCOMPLETE: result TSV does not contain exactly two rows per repetition")
        try:
            expected_workers = int(env["workers"])
            if expected_workers < 1:
                raise ValueError
        except (KeyError, ValueError):
            expected_workers = None
            malformed = True
            findings.append("INCOMPLETE: manifest has missing/invalid worker count")
        if reps and reps != list(range(1, len(reps) + 1)):
            malformed = True
            findings.append("INCOMPLETE: repetition identifiers must be contiguous starting at 1")
        if len(reps) != declared_repetitions:
            malformed = True
            findings.append(f"INCOMPLETE: manifest declares {declared_repetitions} repetitions but summary contains {len(reps)}")
        summary_keys = {(row["mode"], row["rep"]) for row in rows}
        raw_keys = {(mode, str(rep)) for mode, rep in raw_groups}
        if summary_keys != raw_keys:
            malformed = True
            findings.append("INCOMPLETE: summary and raw sample mode/repetition pairs do not match")
        for rep_num in reps:
            rep = str(rep_num)
            baseline = [r for r in rows if r["rep"] == rep and r["mode"] == "baseline"]
            enabled = [r for r in rows if r["rep"] == rep and r["mode"] == "router"]
            if len(baseline) != 1 or len(enabled) != 1:
                malformed = True
                findings.append(f"INCOMPLETE: repetition {rep} does not have exactly one baseline and one router row")
                continue
            b, e = baseline[0], enabled[0]
            try:
                routed_field = "workers_routed" if "workers_routed" in e else "workers_in_heavy"
                if expected_workers is not None and (
                    int(b["worker_count"]) != expected_workers
                    or int(e["worker_count"]) != expected_workers
                ):
                    findings.append(f"FAIL: repetition {rep} worker count does not match manifest")
                if int(e["trial_valid"]) != 1 or int(e[routed_field]) != int(e["worker_count"]):
                    findings.append(f"FAIL: repetition {rep} did not route every intended worker")
                else:
                    findings.append(f"PASS: repetition {rep} routed all {e['worker_count']} intended worker(s)")
                baseline_routed_field = "workers_routed" if "workers_routed" in b else "workers_in_heavy"
                if int(b["trial_valid"]) != 1 or int(b[baseline_routed_field]) != 0:
                    findings.append(f"FAIL: repetition {rep} baseline routing state is inconsistent")
                if int(b["steady_n"]) < MIN_STEADY_SAMPLES or int(e["steady_n"]) < MIN_STEADY_SAMPLES:
                    findings.append(f"FAIL: repetition {rep} has fewer than {MIN_STEADY_SAMPLES} steady samples")
                else:
                    findings.append(f"PASS: repetition {rep} steady samples baseline/router={b['steady_n']}/{e['steady_n']}")
                for summary_row in (b, e):
                    mode_key = (summary_row["mode"], rep)
                    raw_rows = raw_groups.get(mode_key, [])
                    if int(summary_row["samples"]) != len(raw_rows):
                        malformed = True
                        findings.append(f"INCOMPLETE: repetition {rep} {summary_row['mode']} summary/raw sample counts differ")
                    for metric in ("all_mean_ms", "all_p50_ms", "all_p95_ms", "all_p99_ms", "all_max_ms",
                                   "work_p95_ms", "steady_mean_ms", "steady_p50_ms", "steady_p95_ms",
                                   "steady_p99_ms", "steady_max_ms", "steady_work_p95_ms"):
                        if finite_nonnegative(summary_row.get(metric)) is None:
                            malformed = True
                            findings.append(f"INCOMPLETE: repetition {rep} {summary_row['mode']} has invalid {metric}")
                            break
                    route_at = finite_nonnegative(summary_row.get("routed_at_s")) if summary_row["mode"] == "router" else 12.0
                    if summary_row["mode"] == "router" and (route_at is None or route_at <= 0):
                        malformed = True
                        findings.append(f"INCOMPLETE: repetition {rep} has invalid routed_at_s")
                    elif summary_row["mode"] == "router" and route_at + 2.0 > 12.0:
                        findings.append(f"FAIL: repetition {rep} routing did not converge at least 2s before the common 12s steady window")
                    elif raw_rows:
                        raw_steady_n = sum(1 for sample in raw_rows if sample[0] >= 12.0)
                        if raw_steady_n != int(summary_row["steady_n"]):
                            malformed = True
                            findings.append(f"INCOMPLETE: repetition {rep} {summary_row['mode']} steady sample count disagrees with raw samples ({summary_row['steady_n']} vs {raw_steady_n})")
            except (TypeError, ValueError):
                malformed = True
                findings.append(f"INCOMPLETE: repetition {rep} has malformed route/sample counts")
                continue
            ratios = {}
            for metric in ("steady_p95_ms", "steady_p99_ms", "steady_work_p95_ms", "worker_throughput_per_sec"):
                bv, ev = finite_positive(b, metric), finite_positive(e, metric)
                if bv is None or ev is None:
                    malformed = True
                    findings.append(f"INCOMPLETE: repetition {rep} has missing/non-finite {metric}")
                else:
                    ratios[metric] = ev / bv
            try:
                cpu = float(e["router_cpu_pct_one_core"])
                rss = float(e["router_peak_rss_kib"])
                if not math.isfinite(cpu) or not math.isfinite(rss):
                    raise ValueError
                if cpu > MAX_ROUTER_CPU_PERCENT_ONE_CORE:
                    findings.append(f"FAIL: repetition {rep} router CPU {cpu:.3f}% exceeds {MAX_ROUTER_CPU_PERCENT_ONE_CORE:.1f}% of one logical CPU")
                else:
                    findings.append(f"PASS: repetition {rep} router CPU={cpu:.3f}% of one logical CPU")
                if rss > MAX_ROUTER_RSS_KIB:
                    findings.append(f"FAIL: repetition {rep} router peak RSS {rss:.0f} KiB exceeds {MAX_ROUTER_RSS_KIB} KiB")
                else:
                    findings.append(f"PASS: repetition {rep} router peak RSS={rss:.0f} KiB")
            except (TypeError, ValueError):
                malformed = True
                findings.append(f"INCOMPLETE: repetition {rep} lacks valid router CPU/RSS overhead")
            if len(ratios) == 4:
                pairs.append((rep, ratios))
                for metric, ratio in ratios.items():
                    findings.append(f"PAIR: repetition {rep} {metric} ratio={ratio:.3f}")
        if len(reps) < MIN_PAIRS:
            findings.append(f"INCOMPLETE: only {len(reps)} paired repetitions; need {MIN_PAIRS}")
        if not malformed and len(reps) >= MIN_PAIRS and len(pairs) == len(reps):
            for metric in ("steady_p95_ms", "steady_p99_ms", "steady_work_p95_ms", "worker_throughput_per_sec"):
                median_ratio = statistics.median(pair[1][metric] for pair in pairs)
                limit = MIN_THROUGHPUT_RATIO if metric == "worker_throughput_per_sec" else MAX_LATENCY_RATIO
                passed = median_ratio >= limit if metric == "worker_throughput_per_sec" else median_ratio <= limit
                findings.append(f"{'PASS' if passed else 'FAIL'}: median paired {metric} ratio={median_ratio:.3f} limit={'≥' if metric == 'worker_throughput_per_sec' else '≤'}{limit:.2f}")
                spread = ratio_variation([pair[1][metric] for pair in pairs])
                findings.append(
                    f"VARIATION: {metric} ratio min={spread['minimum']:.3f} q1={spread['q1']:.3f} "
                    f"median={median_ratio:.3f} q3={spread['q3']:.3f} max={spread['maximum']:.3f}; "
                    f"bootstrap95CI=[{spread['ci_low']:.3f},{spread['ci_high']:.3f}] "
                    f"(resamples={BOOTSTRAP_RESAMPLES}, seed={BOOTSTRAP_SEED}; descriptive, not a gate override)"
                )
        has_fail = any(line.startswith("FAIL:") for line in findings)
        has_incomplete = any(line.startswith("INCOMPLETE:") for line in findings)
        status = "INCOMPLETE" if has_incomplete else ("FAIL" if has_fail else "PASS")
    lines = [
        f"acceptance_status={status}",
        f"result_tsv={result}",
        f"raw_samples_tsv={raw_path}",
        f"manifest={manifest}",
        f"git_revision={env.get('git_revision', 'unavailable')}",
        f"workload_kind={env.get('workload_kind', 'unavailable')}",
        f"host={env.get('host', 'unavailable')}",
        f"duration_seconds={env.get('duration_seconds', 'unavailable')}",
        f"workers={env.get('workers', 'unavailable')}",
        f"minimum_pairs={MIN_PAIRS}",
        f"minimum_steady_samples_per_mode={MIN_STEADY_SAMPLES}",
        f"max_median_paired_latency_ratio={MAX_LATENCY_RATIO:.2f}",
        f"min_median_paired_throughput_ratio={MIN_THROUGHPUT_RATIO:.2f}",
        f"max_router_cpu_pct_one_core={MAX_ROUTER_CPU_PERCENT_ONE_CORE:.1f}",
        f"max_router_peak_rss_kib={MAX_ROUTER_RSS_KIB}",
        ("scope=synthetic Python integer-mixing workload and periodic probe only; PASS does not close terminal-workload release gate"
         if env.get("workload_kind") == "synthetic" else
         "scope=OpenSSL SHA-256 CLI throughput workload and periodic probe only; PASS does not generalize to compiler builds or all terminal workloads"
         if env.get("workload_kind") == "terminal-openssl" else
         "scope=GNU gzip -1 compression of /dev/zero and periodic probe only; PASS does not generalize to other CLI workloads"
         if env.get("workload_kind") == "terminal-gzip" else
         "scope=bounded repeated GCC/CC multi-translation-unit compile-and-link workload and periodic probe only; generated source is a reproducible compiler stress workload, not a production repository build"),
        f"variation_method=paired-ratio range/IQR plus deterministic bootstrap percentile interval; resamples={BOOTSTRAP_RESAMPLES}; seed={BOOTSTRAP_SEED}; descriptive only",
        *findings,
    ]
    try:
        with report.open("x") as output:
            output.write("\n".join(lines) + "\n")
    except FileExistsError:
        print(f"refusing to overwrite acceptance report: {report}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[status]


if __name__ == "__main__":
    raise SystemExit(main())
