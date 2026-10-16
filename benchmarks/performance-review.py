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
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from evidence_integrity import digest_file, manifest_path, verify_manifest

MIN_PAIRS = 5
MIN_STEADY_SAMPLES = 500
MAX_LATENCY_RATIO = 1.10
MIN_THROUGHPUT_RATIO = 0.90
MAX_ROUTER_CPU_PERCENT_ONE_CORE = 5.0
MAX_ROUTER_RSS_KIB = 32768
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261015
MAX_HOST_PSI_AVG10 = 5.0


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


def compare_number(actual, expected, tolerance=0.002):
    try:
        actual = float(actual)
        return math.isfinite(actual) and abs(actual - expected) <= max(tolerance, abs(expected) * 1e-6)
    except (TypeError, ValueError):
        return False


def validate_summary_from_raw(summary_row, raw_rows):
    """Recompute every published probe percentile/mean from immutable raw samples."""
    if not raw_rows:
        return ["raw sample set is empty"]
    errors = []
    wake = [sample[1] for sample in raw_rows]
    work = [sample[2] for sample in raw_rows]
    steady = [sample for sample in raw_rows if sample[0] >= 12.0]
    steady_wake = [sample[1] for sample in steady]
    steady_work = [sample[2] for sample in steady]
    expected = {
        "all_mean_ms": statistics.mean(wake), "all_p50_ms": nearest_rank(wake, .50),
        "all_p95_ms": nearest_rank(wake, .95), "all_p99_ms": nearest_rank(wake, .99),
        "all_max_ms": max(wake), "work_p95_ms": nearest_rank(work, .95),
        "steady_n": float(len(steady)),
    }
    if steady_wake:
        expected.update({
            "steady_mean_ms": statistics.mean(steady_wake),
            "steady_p50_ms": nearest_rank(steady_wake, .50),
            "steady_p95_ms": nearest_rank(steady_wake, .95),
            "steady_p99_ms": nearest_rank(steady_wake, .99),
            "steady_max_ms": max(steady_wake),
            "steady_work_p95_ms": nearest_rank(steady_work, .95),
        })
    for field, value in expected.items():
        tolerance = 0.0 if field == "steady_n" else 0.002
        if not compare_number(summary_row.get(field), value, tolerance):
            errors.append(f"{field} summary={summary_row.get(field)!r} raw_recomputed={value:.6f}")
    return errors


def validate_new_evidence(env, manifest, declared_duration, declared_repetitions, expected_workers, findings):
    """Require interval telemetry and raw progress counters for newly instrumented runs.

    Historical artifacts without these manifest keys remain readable, but are
    explicitly reported as legacy/uninstrumented rather than silently upgraded.
    """
    telemetry_value = env.get("interval_telemetry", "").strip()
    if telemetry_value:
        telemetry_name = telemetry_value.split(";", 1)[0].strip()
        telemetry_path = pathlib.Path(telemetry_name)
        if not telemetry_path.is_file() and not telemetry_path.is_absolute():
            telemetry_path = manifest.parent / telemetry_path.name
        required = {"mode", "rep", "monotonic_s", "elapsed_s", "cgroup_kind", "unit", "cgroup_path",
                    "cpu_weight", "usage_usec", "nr_periods", "nr_throttled", "throttled_usec",
                    "host_psi_some_avg10", "loadavg_1m", "router_cpu_ticks", "router_rss_kib",
                    "router_log_new_lines"}
        groups = {}
        errors = []
        last_elapsed_by_group = {}
        if not re.fullmatch(r"[0-9a-fA-F]{40}", env.get("git_revision", "")):
            errors.append("source revision is not a full 40-hex Git commit")
        if env.get("git_dirty", "").strip().lower() not in ("false", "0", "no"):
            errors.append("source tree is dirty or dirty-state provenance is missing")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", env.get("git_diff_sha256", "")):
            errors.append("tracked-source diff SHA-256 is missing or malformed")
        if not telemetry_path.is_file():
            errors.append(f"interval telemetry file missing: {telemetry_path}")
        else:
            try:
                with telemetry_path.open(newline="") as source:
                    reader = csv.DictReader(source, delimiter="\t")
                    if not reader.fieldnames or not required.issubset(reader.fieldnames):
                        errors.append("interval telemetry schema missing required columns")
                    else:
                        seen = set()
                        for line_no, row in enumerate(reader, 2):
                            mode, rep = row.get("mode", ""), row.get("rep", "")
                            if mode not in ("baseline", "router") or not rep.isdigit() or not 1 <= int(rep) <= declared_repetitions:
                                errors.append(f"telemetry row {line_no} has invalid mode/repetition")
                                continue
                            try:
                                elapsed = float(row["elapsed_s"])
                                monotonic = float(row["monotonic_s"])
                                if not math.isfinite(elapsed) or not math.isfinite(monotonic) or elapsed < 0 or elapsed > declared_duration + 1:
                                    raise ValueError
                            except (ValueError, TypeError):
                                errors.append(f"telemetry row {line_no} has invalid time")
                                continue
                            kind, unit = row.get("cgroup_kind", ""), row.get("unit", "")
                            if kind not in ("parent", "heavy", "recovery", "workload", "probe") or not unit:
                                errors.append(f"telemetry row {line_no} has invalid cgroup identity")
                                continue
                            key = (mode, rep, kind, unit, round(elapsed, 3))
                            if key in seen:
                                errors.append(f"duplicate telemetry timestamp for {mode}/{rep}/{unit} at {elapsed:.3f}s")
                                continue
                            seen.add(key)
                            ordered_key = (mode, rep, kind, unit)
                            previous_elapsed = last_elapsed_by_group.get(ordered_key)
                            if previous_elapsed is not None and elapsed <= previous_elapsed:
                                errors.append(f"telemetry timestamps are not strictly increasing for {mode}/{rep}/{unit}: {elapsed:.3f}s after {previous_elapsed:.3f}s")
                            last_elapsed_by_group[ordered_key] = elapsed
                            for field in ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec"):
                                if not row.get(field, "").isdigit():
                                    errors.append(f"telemetry row {line_no} missing real cpu.stat {field}")
                                    break
                            if not row.get("cpu_weight", "").isdigit():
                                errors.append(f"telemetry row {line_no} missing cpu.weight readback")
                            psi = finite_nonnegative(row.get("host_psi_some_avg10"))
                            if psi is None:
                                errors.append(f"telemetry row {line_no} missing host CPU PSI some avg10")
                            if mode == "router" and (not row.get("router_cpu_ticks", "").isdigit()
                                                      or not row.get("router_rss_kib", "").isdigit()):
                                errors.append(f"telemetry row {line_no} missing router overhead sample")
                            groups.setdefault((mode, rep, kind, unit), []).append((elapsed, row))
            except (OSError, csv.Error) as exc:
                errors.append(f"could not read interval telemetry: {exc}")
        if not errors:
            for rep in range(1, declared_repetitions + 1):
                for mode in ("baseline", "router"):
                    base = (mode, str(rep))
                    for kind in ("parent", "heavy", "probe"):
                        candidates = [values for (m, r, k, _), values in groups.items() if (m, r, k) == (*base, kind)]
                        if not candidates or len(candidates[0]) < 20:
                            errors.append(f"{mode}/{rep} {kind} telemetry has fewer than 20 intervals")
                            continue
                        times = sorted(item[0] for item in candidates[0])
                        if any(b - a > 2.5 for a, b in zip(times, times[1:])):
                            errors.append(f"{mode}/{rep} {kind} telemetry has a sampling gap >2.5s")
                    workloads = [(unit, values) for (m, r, k, unit), values in groups.items()
                                 if (m, r, k) == (*base, "workload")]
                    if len(workloads) != expected_workers:
                        errors.append(f"{mode}/{rep} telemetry workload scope count={len(workloads)}, expected={expected_workers}")
                    for unit, values in workloads:
                        if len(values) < 20:
                            errors.append(f"{mode}/{rep}/{unit} workload telemetry has fewer than 20 intervals")
                        times = sorted(item[0] for item in values)
                        if any(b - a > 2.5 for a, b in zip(times, times[1:])):
                            errors.append(f"{mode}/{rep}/{unit} telemetry has a sampling gap >2.5s")
                        weights = {int(row["cpu_weight"]) for _, row in values if row.get("cpu_weight", "").isdigit()}
                        if mode == "router":
                            match = re.search(r"CPUWeight=(\d+) per sample", env.get("aggregate_routing", ""))
                            target_weight = int(match.group(1)) if match else 20
                            if target_weight not in weights:
                                errors.append(f"{mode}/{rep}/{unit} never read back candidate cpu.weight={target_weight}")
                        elif weights != {100}:
                            errors.append(f"{mode}/{rep}/{unit} baseline cpu.weight readback was not stable at 100: {sorted(weights)}")
                    if mode == "router":
                        decision_lines = [row.get("router_log_new_lines", "") for (m, r, k, _), values in groups.items()
                                          if (m, r, k) == (*base, "parent") for _, row in values]
                        if not any("aggregate scope decision=" in line for line in decision_lines):
                            errors.append(f"router/{rep} telemetry captured no aggregate routing decision log")
        if errors:
            findings.extend("INCOMPLETE: interval telemetry: " + error for error in errors)
        else:
            findings.append(f"PASS: interval telemetry validated ({telemetry_path}, per-scope cpu.stat/weight/PSI/router coverage)")
    else:
        findings.append("INFO: legacy evidence has no interval telemetry; no per-interval CPU/throttling causal analysis is possible")

    throughput_value = env.get("throughput_raw", "").strip()
    if throughput_value:
        throughput_name = throughput_value.split(";", 1)[0].strip()
        throughput_path = pathlib.Path(throughput_name)
        if not throughput_path.is_file() and not throughput_path.is_absolute():
            throughput_path = manifest.parent / throughput_path.name
        errors = []
        progress = {}
        last_progress_elapsed = {}
        required = {"mode", "rep", "worker", "elapsed_s", "counter", "metric_kind"}
        if not throughput_path.is_file():
            errors.append(f"throughput raw file missing: {throughput_path}")
        else:
            try:
                with throughput_path.open(newline="") as source:
                    reader = csv.DictReader(source, delimiter="\t")
                    if not reader.fieldnames or not required.issubset(reader.fieldnames):
                        errors.append("throughput raw schema missing required columns")
                    else:
                        for line_no, row in enumerate(reader, 2):
                            key = (row.get("mode", ""), row.get("rep", ""), row.get("worker", ""))
                            try:
                                elapsed, counter = float(row["elapsed_s"]), float(row["counter"])
                                if (key[0] not in ("baseline", "router") or not key[1].isdigit()
                                        or not 1 <= int(key[1]) <= declared_repetitions
                                        or not key[2].isdigit() or not 1 <= int(key[2]) <= expected_workers
                                        or not math.isfinite(elapsed)
                                        or not math.isfinite(counter) or elapsed < 0 or elapsed > declared_duration + 1
                                        or counter < 0):
                                    raise ValueError
                            except (TypeError, ValueError):
                                errors.append(f"throughput raw row {line_no} has invalid data")
                                continue
                            previous_elapsed = last_progress_elapsed.get(key)
                            if previous_elapsed is not None and elapsed <= previous_elapsed:
                                errors.append(f"throughput raw timestamps are not strictly increasing for {key}: {elapsed:.3f}s after {previous_elapsed:.3f}s")
                            last_progress_elapsed[key] = elapsed
                            progress.setdefault(key, []).append((elapsed, counter, row.get("metric_kind", "")))
            except (OSError, csv.Error) as exc:
                errors.append(f"could not read throughput raw: {exc}")
        if not errors:
            summary_rows = {}
            with pathlib.Path(str(manifest).removesuffix(".environment.txt") + ".tsv").open(newline="") as source:
                for row in csv.DictReader(source, delimiter="\t"):
                    summary_rows[(row["mode"], row["rep"])] = row
            for rep in range(1, declared_repetitions + 1):
                for mode in ("baseline", "router"):
                    worker_rates = []
                    for worker in range(1, expected_workers + 1):
                        values = sorted(progress.get((mode, str(rep), str(worker)), []))
                        if not values:
                            errors.append(f"{mode}/{rep}/worker{worker} has no raw throughput samples")
                            continue
                        if values[-1][2] == "final_reported_rate":
                            worker_rates.append(values[-1][1])
                        else:
                            values = [item for item in values if item[0] >= 12.0]
                            if len(values) < 2 or values[-1][0] <= values[0][0] or values[-1][1] < values[0][1]:
                                errors.append(f"{mode}/{rep}/worker{worker} lacks valid steady-window cumulative counters")
                                continue
                            worker_rates.append((values[-1][1] - values[0][1]) / (values[-1][0] - values[0][0]))
                    summary = summary_rows.get((mode, str(rep)))
                    if summary and len(worker_rates) == expected_workers:
                        expected_rate = sum(worker_rates)
                        if not compare_number(summary.get("worker_throughput_per_sec"), expected_rate,
                                              max(0.5, abs(expected_rate) * 0.005)):
                            errors.append(f"{mode}/{rep} summary throughput={summary.get('worker_throughput_per_sec')} raw recomputed={expected_rate:.3f}")
        if errors:
            findings.extend("INCOMPLETE: throughput raw: " + error for error in errors)
        else:
            findings.append(f"PASS: throughput summary recomputed from raw per-worker counters ({throughput_path})")
    else:
        findings.append("INFO: legacy evidence has no raw per-worker throughput counters; throughput summary cannot be independently recomputed")


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


def steady_host_psi_noise(manifest_values, manifest_path, declared_repetitions):
    """Assess the preregistered host-PSI noise limit without excluding samples.

    Returns (classification, detail). Legacy evidence without interval telemetry
    is explicitly unassessed; malformed/missing telemetry is handled separately
    as INCOMPLETE by validate_new_evidence().
    """
    telemetry_value = manifest_values.get("interval_telemetry", "").split(";", 1)[0].strip()
    if not telemetry_value:
        return "NOT_ASSESSED", "legacy evidence has no interval host PSI series"
    telemetry_path = pathlib.Path(telemetry_value)
    if not telemetry_path.is_file() and not telemetry_path.is_absolute():
        telemetry_path = manifest_path.parent / telemetry_path.name
    if not telemetry_path.is_file():
        return "INVALID", f"interval telemetry missing: {telemetry_path}"
    samples = {("baseline", rep): [] for rep in range(1, declared_repetitions + 1)}
    samples.update({("router", rep): [] for rep in range(1, declared_repetitions + 1)})
    try:
        with telemetry_path.open(newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            required = {"mode", "rep", "elapsed_s", "cgroup_kind", "host_psi_some_avg10"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                return "INVALID", "interval telemetry lacks columns required for PSI noise assessment"
            for line_no, row in enumerate(reader, 2):
                if row.get("cgroup_kind") != "parent" or row.get("mode") not in ("baseline", "router"):
                    continue
                try:
                    rep = int(row["rep"])
                    elapsed = float(row["elapsed_s"])
                    psi = float(row["host_psi_some_avg10"])
                except (KeyError, TypeError, ValueError):
                    return "INVALID", f"invalid host PSI/time in telemetry row {line_no}"
                if rep < 1 or rep > declared_repetitions or not math.isfinite(elapsed) or not math.isfinite(psi):
                    return "INVALID", f"out-of-range host PSI/time in telemetry row {line_no}"
                if elapsed < 12.0:
                    continue
                if psi < 0 or psi > 100:
                    return "INVALID", f"host PSI outside [0,100] in telemetry row {line_no}"
                key = (row["mode"], rep)
                samples[key].append(psi)
    except (OSError, csv.Error) as exc:
        return "INVALID", f"could not read PSI telemetry: {exc}"
    missing = [f"{mode}/{rep}" for (mode, rep), values in samples.items() if not values]
    if missing:
        return "INVALID", "no steady-window host PSI samples for " + ", ".join(missing)
    high_pct = {key: 100.0 * sum(value > MAX_HOST_PSI_AVG10 for value in values) / len(values)
                for key, values in samples.items()}
    baseline_values = [value for (mode, _), values in samples.items() if mode == "baseline" for value in values]
    router_values = [value for (mode, _), values in samples.items() if mode == "router" for value in values]
    baseline_median = statistics.median(baseline_values)
    router_median = statistics.median(router_values)
    imbalance = abs(router_median - baseline_median)
    details = ", ".join(
        f"{mode}/{rep}:median={statistics.median(values):.2f}%,samples_gt5={high_pct[(mode,rep)]:.1f}%"
        for (mode, rep), values in sorted(samples.items())
    )
    if any(value > 20.0 for value in high_pct.values()) or imbalance > 5.0:
        return "CONFOUNDED_INCONCLUSIVE", (
            f"predeclared PSI control failed: samples >{MAX_HOST_PSI_AVG10:.1f}% exceed 20% in a mode/pair "
            f"or baseline/router condition median imbalance exceeds 5 percentage points; "
            f"baseline_median={baseline_median:.2f}%, router_median={router_median:.2f}%, "
            f"imbalance={imbalance:.2f} points; per-pair diagnostics: {details}; all samples retained"
        )
    return "WITHIN_LIMIT", (
        f"predeclared PSI controls satisfied: no mode/pair has >20% samples above {MAX_HOST_PSI_AVG10:.1f}% "
        f"and baseline/router median imbalance <=5 points; baseline_median={baseline_median:.2f}%, "
        f"router_median={router_median:.2f}%; per-pair diagnostics: {details}"
    )


def validate_preregistration_manifest(env):
    """Verify that a preregistered block is still bound to the sealed protocol."""
    requested = env.get("preregistration_required", "false").strip().lower() in ("1", "true", "yes", "on")
    if not requested:
        return []
    required = ("preregistration_status", "preregistration_directory", "preregistration_sha256",
                "preregistration_block", "preregistration_cpu_weight", "preregistration_source_revision",
                "preregistration_host")
    missing = [key for key in required if not env.get(key, "").strip()]
    if missing:
        return ["preregistered run is missing binding metadata: " + ", ".join(missing)]
    if env["preregistration_status"] != "SEALED_VERIFIED":
        return ["preregistered run manifest does not record successful pre-run seal verification"]
    try:
        import preregister_abba
        payload, digest, errors = preregister_abba.verify_sealed_preregistration(env["preregistration_directory"])
        if errors:
            return ["sealed preregistration invalid: " + "; ".join(errors)]
        if digest != env["preregistration_sha256"]:
            return ["preregistration hash differs from the pre-run verified hash"]
        block = env["preregistration_block"]
        selected = next((item for item in payload["experiment"]["blocks"] if item["block"] == block), None)
        if selected is None:
            return ["preregistration block is not declared in the sealed protocol"]
        comparisons = {
            "preregistration_host": payload["host"].get("hostname"),
            "preregistration_source_revision": payload["source"].get("revision"),
            "git_revision": payload["source"].get("revision"),
            "workload_kind": payload["experiment"].get("workload_kind"),
            "repetitions": str(payload["experiment"].get("repetitions_per_block")),
            "workers": str(payload["experiment"].get("workers")),
            "preregistration_cpu_weight": str(selected["cpu_weight"]),
        }
        mismatches = [f"{key} differs from sealed preregistration" for key, expected in comparisons.items()
                      if env.get(key) != expected]
        if mismatches:
            return mismatches
        try:
            observed_duration = float(env.get("duration_seconds", "nan"))
            expected_duration = float(payload["experiment"]["duration_seconds_per_mode"])
            if not math.isfinite(observed_duration) or not math.isclose(observed_duration, expected_duration, rel_tol=0.0, abs_tol=1e-9):
                return ["duration_seconds differs from sealed preregistration"]
        except (TypeError, ValueError, KeyError):
            return ["duration_seconds is invalid for sealed preregistration"]
        if env.get("git_dirty", "").strip().lower() not in ("false", "0", "no"):
            return ["preregistered result source tree is not clean"]
        aggregate = env.get("aggregate_routing", "")
        match = re.search(r"CPUWeight=(\d+) per sample", aggregate)
        if not match or int(match.group(1)) != selected["cpu_weight"]:
            return ["effective candidate CPUWeight in manifest does not match preregistered block"]
        return []
    except (OSError, ValueError, KeyError, ImportError) as exc:
        return [f"could not verify preregistration binding: {exc}"]


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
    if (any(path.is_symlink() for path in (result, manifest, raw_path)) or
            not result.is_file() or not manifest.is_file() or not raw_path.is_file()):
        print("INCOMPLETE: result TSV, raw samples, or environment manifest missing/unsafe", file=sys.stderr)
        return 2
    if report.exists() or report.is_symlink():
        print(f"refusing to overwrite acceptance report: {report}", file=sys.stderr)
        return 2
    evidence_manifest = manifest_path(result)
    evidence_manifest_errors = (verify_manifest(result)
                                if evidence_manifest.exists() or evidence_manifest.is_symlink() else [])
    try:
        evidence_manifest_digest = (digest_file(evidence_manifest)
                                    if evidence_manifest.is_file() and not evidence_manifest.is_symlink()
                                    else "unavailable")
    except (OSError, UnicodeError) as exc:
        evidence_manifest_digest = "unavailable"
        evidence_manifest_errors.append(f"could not digest evidence checksum manifest: {exc}")
    env = {}
    try:
        environment_lines = manifest.read_text().splitlines()
    except (OSError, UnicodeError) as exc:
        print(f"INCOMPLETE: environment manifest could not be read: {exc}", file=sys.stderr)
        return 2
    for line in environment_lines:
        if "=" in line:
            key, value = line.split("=", 1)
            if key in env:
                print(f"INCOMPLETE: environment manifest contains duplicate key {key!r}", file=sys.stderr)
                return 2
            env[key] = value
    findings = []
    status = "INCOMPLETE"
    psi_noise_status = "NOT_ASSESSED"
    manifest_required = ("git_revision", "host", "python", "cpu_model", "logical_cpus", "systemd",
                         "cgroup_mode", "cgroup_controllers", "workload_kind", "repetitions", "workers",
                         "duration_seconds", "parent_cpu_quota", "result_status")
    missing_manifest = [key for key in manifest_required if not env.get(key, "").strip()]
    if missing_manifest:
        print("INCOMPLETE: manifest missing required metadata: " + ", ".join(missing_manifest), file=sys.stderr)
        return 2
    if env.get("workload_kind") not in ("synthetic", "terminal-openssl", "terminal-gzip", "terminal-build", "terminal-repo-cli"):
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
        preregistration_errors = validate_preregistration_manifest(env)
        if preregistration_errors:
            malformed = True
            findings.extend("INCOMPLETE: preregistration: " + error for error in preregistration_errors)
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
        if expected_workers is not None:
            validate_new_evidence(env, manifest, declared_duration, declared_repetitions,
                                  expected_workers, findings)
        psi_noise_status, psi_noise_detail = steady_host_psi_noise(env, manifest, declared_repetitions)
        if psi_noise_status == "CONFOUNDED_INCONCLUSIVE":
            findings.append("CONFOUNDED_INCONCLUSIVE: " + psi_noise_detail)
        elif psi_noise_status == "WITHIN_LIMIT":
            findings.append("NOISE_CONTROL_PASS: " + psi_noise_detail)
        elif psi_noise_status == "INVALID":
            findings.append("INCOMPLETE: PSI noise assessment: " + psi_noise_detail)
            malformed = True
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
                    if raw_rows and env.get("raw_summary_validation", "false").lower() in ("1", "true", "yes", "on"):
                        raw_errors = validate_summary_from_raw(summary_row, raw_rows)
                        if raw_errors:
                            malformed = True
                            findings.append(
                                f"INCOMPLETE: repetition {rep} {summary_row['mode']} summary is inconsistent with raw samples: "
                                + "; ".join(raw_errors)
                            )
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
                if psi_noise_status == "CONFOUNDED_INCONCLUSIVE":
                    findings.append(f"OBSERVED_METRIC: median paired {metric} ratio={median_ratio:.3f} limit={'≥' if metric == 'worker_throughput_per_sec' else '≤'}{limit:.2f}; causal acceptance blocked by PSI noise")
                else:
                    findings.append(f"{'PASS' if passed else 'FAIL'}: median paired {metric} ratio={median_ratio:.3f} limit={'≥' if metric == 'worker_throughput_per_sec' else '≤'}{limit:.2f}")
                spread = ratio_variation([pair[1][metric] for pair in pairs])
                findings.append(
                    f"VARIATION: {metric} ratio min={spread['minimum']:.3f} q1={spread['q1']:.3f} "
                    f"median={median_ratio:.3f} q3={spread['q3']:.3f} max={spread['maximum']:.3f}; "
                    f"bootstrap95CI=[{spread['ci_low']:.3f},{spread['ci_high']:.3f}] "
                    f"(resamples={BOOTSTRAP_RESAMPLES}, seed={BOOTSTRAP_SEED}; descriptive, not a gate override)"
                )
    has_fail = any(line.startswith("FAIL:") for line in findings)
    has_nonperformance_fail = any(line.startswith("FAIL:") and not line.startswith("FAIL: median paired ") for line in findings)
    has_incomplete = any(line.startswith("INCOMPLETE:") for line in findings)
    if psi_noise_status == "NOT_ASSESSED":
        findings.append("INCONCLUSIVE: legacy evidence has no verified steady-window host PSI series; it cannot close throughput acceptance")
    status = ("INCOMPLETE" if has_incomplete else
              "FAIL" if has_nonperformance_fail else
              "CONFOUNDED_INCONCLUSIVE" if psi_noise_status == "CONFOUNDED_INCONCLUSIVE" else
              "INCONCLUSIVE" if psi_noise_status == "NOT_ASSESSED" else
              "FAIL" if has_fail else "PASS")
    if evidence_manifest_errors:
        findings.extend("INCOMPLETE: evidence integrity: " + error for error in evidence_manifest_errors)
        status = "INCOMPLETE"
    elif status == "PASS" and evidence_manifest_digest == "unavailable":
        findings.append("INCOMPLETE: verified throughput acceptance requires a run evidence checksum manifest")
        status = "INCOMPLETE"
    lines = [
        f"acceptance_status={status}",
        f"result_tsv={result}",
        f"raw_samples_tsv={raw_path}",
        f"manifest={manifest}",
        f"evidence_checksum_manifest={evidence_manifest if evidence_manifest_digest != 'unavailable' else 'unavailable'}",
        f"evidence_checksum_manifest_sha256={evidence_manifest_digest}",
        f"git_revision={env.get('git_revision', 'unavailable')}",
        f"workload_kind={env.get('workload_kind', 'unavailable')}",
        f"host={env.get('host', 'unavailable')}",
        f"duration_seconds={env.get('duration_seconds', 'unavailable')}",
        f"workers={env.get('workers', 'unavailable')}",
        f"minimum_pairs={MIN_PAIRS}",
        f"minimum_steady_samples_per_mode={MIN_STEADY_SAMPLES}",
        f"max_median_paired_latency_ratio={MAX_LATENCY_RATIO:.2f}",
        f"min_median_paired_throughput_ratio={MIN_THROUGHPUT_RATIO:.2f}",
        f"host_psi_some_avg10_sample_threshold_percent={MAX_HOST_PSI_AVG10:.1f}",
        "max_samples_above_psi_threshold_per_mode_pair_percent=20.0",
        "max_baseline_router_psi_median_imbalance_percentage_points=5.0",
        f"psi_noise_status={psi_noise_status}",
        f"max_router_cpu_pct_one_core={MAX_ROUTER_CPU_PERCENT_ONE_CORE:.1f}",
        f"max_router_peak_rss_kib={MAX_ROUTER_RSS_KIB}",
        ("scope=synthetic Python integer-mixing workload and periodic probe only; PASS does not close terminal-workload release gate"
         if env.get("workload_kind") == "synthetic" else
         "scope=OpenSSL SHA-256 CLI throughput workload and periodic probe only; PASS does not generalize to compiler builds or all terminal workloads"
         if env.get("workload_kind") == "terminal-openssl" else
         "scope=GNU gzip -1 compression of /dev/zero and periodic probe only; PASS does not generalize to other CLI workloads"
         if env.get("workload_kind") == "terminal-gzip" else
         "scope=actual repository Python compileall passes interleaved with read-only workload-guard status CLI calls and periodic probe; single-host result does not generalize to all repository builds or terminal workloads"
         if env.get("workload_kind") == "terminal-repo-cli" else
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
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2, "CONFOUNDED_INCONCLUSIVE": 3, "INCONCLUSIVE": 4}[status]


if __name__ == "__main__":
    raise SystemExit(main())
