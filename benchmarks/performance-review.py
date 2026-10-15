#!/usr/bin/env python3
"""Evaluate paired routing benchmark evidence against predeclared RC limits.

Usage: performance-review.py RESULT.tsv [REPORT.txt]
Writes RESULT.acceptance.txt (or the explicit report path) without overwriting prior evidence. A PASS applies
only to this synthetic probe; it does not close representative-workload gates.
"""
import csv
import math
import pathlib
import statistics
import sys

MIN_PAIRS = 5
MIN_STEADY_SAMPLES = 500
MAX_LATENCY_RATIO = 1.10
MIN_THROUGHPUT_RATIO = 0.90
MAX_ROUTER_CPU_PERCENT_ONE_CORE = 5.0
MAX_ROUTER_RSS_KIB = 32768


def finite_positive(row, key):
    try:
        value = float(row[key])
        return value if math.isfinite(value) and value > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: performance-review.py RESULT.tsv [REPORT.txt]", file=sys.stderr)
        return 2
    result = pathlib.Path(sys.argv[1])
    manifest = result.with_suffix(".environment.txt")
    report = pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else result.with_suffix(".acceptance.txt")
    if not result.is_file() or not manifest.is_file():
        print("INCOMPLETE: result TSV or environment manifest missing", file=sys.stderr)
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
    with result.open(newline="") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    required = {"mode", "rep", "trial_valid", "workers_in_heavy", "worker_count", "steady_n",
                "steady_p95_ms", "steady_p99_ms", "steady_work_p95_ms", "worker_iterations_per_sec",
                "router_cpu_pct_one_core", "router_peak_rss_kib"}
    if not rows or not required.issubset(rows[0]):
        findings.append("INCOMPLETE: benchmark schema/rows lack required fields")
    elif env.get("result_status") != "COMPLETE":
        findings.append("INCOMPLETE: benchmark manifest result_status is not COMPLETE")
    else:
        reps = sorted({row["rep"] for row in rows})
        pairs = []
        malformed = False
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
        for rep in reps:
            baseline = [r for r in rows if r["rep"] == rep and r["mode"] == "baseline"]
            enabled = [r for r in rows if r["rep"] == rep and r["mode"] == "router"]
            if len(baseline) != 1 or len(enabled) != 1:
                malformed = True
                findings.append(f"INCOMPLETE: repetition {rep} does not have exactly one baseline and one router row")
                continue
            b, e = baseline[0], enabled[0]
            try:
                if int(rep) < 1:
                    raise ValueError
                if expected_workers is not None and (
                    int(b["worker_count"]) != expected_workers
                    or int(e["worker_count"]) != expected_workers
                ):
                    findings.append(f"FAIL: repetition {rep} worker count does not match manifest")
                if int(e["trial_valid"]) != 1 or int(e["workers_in_heavy"]) != int(e["worker_count"]):
                    findings.append(f"FAIL: repetition {rep} did not route every intended worker")
                else:
                    findings.append(f"PASS: repetition {rep} routed all {e['worker_count']} intended worker(s)")
                if int(b["steady_n"]) < MIN_STEADY_SAMPLES or int(e["steady_n"]) < MIN_STEADY_SAMPLES:
                    findings.append(f"FAIL: repetition {rep} has fewer than {MIN_STEADY_SAMPLES} steady samples")
                else:
                    findings.append(f"PASS: repetition {rep} steady samples baseline/router={b['steady_n']}/{e['steady_n']}")
            except (TypeError, ValueError):
                malformed = True
                findings.append(f"INCOMPLETE: repetition {rep} has malformed route/sample counts")
                continue
            ratios = {}
            for metric in ("steady_p95_ms", "steady_p99_ms", "steady_work_p95_ms", "worker_iterations_per_sec"):
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
            for metric in ("steady_p95_ms", "steady_p99_ms", "steady_work_p95_ms", "worker_iterations_per_sec"):
                median_ratio = statistics.median(pair[1][metric] for pair in pairs)
                limit = MIN_THROUGHPUT_RATIO if metric == "worker_iterations_per_sec" else MAX_LATENCY_RATIO
                passed = median_ratio >= limit if metric == "worker_iterations_per_sec" else median_ratio <= limit
                findings.append(f"{'PASS' if passed else 'FAIL'}: median paired {metric} ratio={median_ratio:.3f} limit={'≥' if metric == 'worker_iterations_per_sec' else '≤'}{limit:.2f}")
        has_fail = any(line.startswith("FAIL:") for line in findings)
        has_incomplete = any(line.startswith("INCOMPLETE:") for line in findings)
        status = "INCOMPLETE" if has_incomplete else ("FAIL" if has_fail else "PASS")
    lines = [
        f"acceptance_status={status}",
        f"result_tsv={result}",
        f"manifest={manifest}",
        f"minimum_pairs={MIN_PAIRS}",
        f"minimum_steady_samples_per_mode={MIN_STEADY_SAMPLES}",
        f"max_median_paired_latency_ratio={MAX_LATENCY_RATIO:.2f}",
        f"min_median_paired_throughput_ratio={MIN_THROUGHPUT_RATIO:.2f}",
        f"max_router_cpu_pct_one_core={MAX_ROUTER_CPU_PERCENT_ONE_CORE:.1f}",
        f"max_router_peak_rss_kib={MAX_ROUTER_RSS_KIB}",
        "scope=synthetic routing probe only; PASS does not close representative terminal-workload release gate",
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
