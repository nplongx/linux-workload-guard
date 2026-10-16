#!/usr/bin/env python3
"""Create/verify a hash-sealed four-block ABBA preregistration.

This command never runs a benchmark, starts a service, or writes a quota. It
seals the design before an operator runs A/B/B2/A2 on an approved independent
host. Successful verification proves only protocol-file integrity, not that
any experiment was executed or accepted.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
ALLOWED_WORKLOADS = ("terminal-openssl", "terminal-gzip", "terminal-build", "terminal-repo-cli")
BLOCKS = (
    {"block": "A", "cpu_weight": 20, "position": 1},
    {"block": "B", "cpu_weight": 80, "position": 2},
    {"block": "B2", "cpu_weight": 80, "position": 3},
    {"block": "A2", "cpu_weight": 20, "position": 4},
)
MIN_THROUGHPUT_RATIO = 0.90
MAX_LATENCY_RATIO = 1.10
MIN_STEADY_SAMPLES = 500
MIN_PAIRS = 5
PSI_THRESHOLD_PERCENT = 5.0
PSI_MAX_HIGH_SAMPLE_SHARE_PERCENT = 20.0
PSI_MAX_CONDITION_MEDIAN_IMBALANCE_POINTS = 5.0
WITHIN_BLOCK_MODE_ORDER = "odd repetitions baseline->router; even repetitions router->baseline"
CAPACITY_MODEL = ("same temporary cgroup-v2 parent CPUQuota=100% in every block and mode; "
                  "dynamic quota disabled; only candidate temporary workload-scope CPUWeight varies")
RUN_CONTRACT = {
    "before_each_block": ("verify this preregistration checksum; use only the source revision and declared host "
                          "recorded here; capture preflight identity and PSI without changing quota or generating load"),
    "command_template": ("BENCH_REQUIRE_PREREGISTRATION=1 BENCH_PREREGISTRATION_DIR=<sealed-dir> "
                         "BENCH_PREREGISTRATION_BLOCK=<A|B|B2|A2> BENCH_AGGREGATE_ROUTING=true "
                         "BENCH_AGGREGATE_SCOPE_WEIGHT=<block-weight> WORKLOAD_KIND=<declared-workload> "
                         "REPS=5 DURATION=30 WORKERS=2 RESULT=<new-unique-result.tsv> "
                         "benchmarks/routing-contention-isolated.sh"),
    "result_directory_must_be_new": True,
    "production_service_config_and_quota_mutations": "PROHIBITED",
    "execution_requires_explicit_approval": True,
}
EXPECTED_ACCEPTANCE = {
    "minimum_median_paired_throughput_ratio": MIN_THROUGHPUT_RATIO,
    "maximum_median_paired_latency_and_probe_work_ratio": MAX_LATENCY_RATIO,
    "minimum_valid_pairs_per_block": MIN_PAIRS,
    "minimum_steady_samples_per_mode": MIN_STEADY_SAMPLES,
    "maximum_router_cpu_percent_of_one_logical_cpu": 5.0,
    "maximum_router_peak_rss_kib": 32768,
    "minimum_candidate_weight80_block_median_throughput_ratio": MIN_THROUGHPUT_RATIO,
    "minimum_weight_effect_median_delta": 0.05,
    "minimum_individual_weight80_pair_ratio": 0.80,
    "psi_some_avg10_sample_threshold_percent": PSI_THRESHOLD_PERCENT,
    "maximum_samples_above_psi_threshold_per_mode_pair_percent": PSI_MAX_HIGH_SAMPLE_SHARE_PERCENT,
    "maximum_baseline_router_condition_median_imbalance_percentage_points": PSI_MAX_CONDITION_MEDIAN_IMBALANCE_POINTS,
    "psi_failure_classification": "CONFOUNDED_INCONCLUSIVE",
    "missing_or_invalid_psi_classification": "INCONCLUSIVE",
    "invalid_or_missing_raw_evidence_classification": "INCOMPLETE",
    "no_sample_exclusion_or_threshold_change": True,
}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tracked_source_state(root=ROOT):
    """Return pinned Git provenance; fail closed if tracked source is dirty."""
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True, timeout=3).strip()
    status = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                     text=True, timeout=3)
    diff = subprocess.check_output(["git", "-C", str(root), "diff", "HEAD", "--binary"],
                                   timeout=3)
    if status.strip():
        raise ValueError("tracked worktree is dirty; commit the exact protocol/evaluator source before preregistration")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Git HEAD is not a full 40-hex commit")
    return {"revision": revision, "tracked_dirty": False, "tracked_diff_sha256": sha256_bytes(diff)}


def read_host_facts():
    def command(args):
        try:
            return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=3).strip()
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    os_release = pathlib.Path("/etc/os-release")
    try:
        release_text = os_release.read_text()
        pretty = next((line.split("=", 1)[1].strip().strip('"')
                       for line in release_text.splitlines() if line.startswith("PRETTY_NAME=")), "unavailable")
    except OSError:
        pretty = "unavailable"
    cpu_model = "unavailable"
    try:
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith(("model name", "Hardware")) and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        controllers = pathlib.Path("/sys/fs/cgroup/cgroup.controllers").read_text().split()
    except OSError:
        controllers = []
    systemd = command(["systemctl", "--version"])
    return {
        "hostname": platform.node(),
        "os_release": pretty,
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "logical_cpus": os.cpu_count() or 0,
        "cpu_model": cpu_model,
        "python": sys.version.replace("\n", " "),
        "systemd": systemd.splitlines()[0] if systemd != "unavailable" else "unavailable",
        "cgroup_v2": pathlib.Path("/sys/fs/cgroup/cgroup.controllers").is_file(),
        "cgroup_controllers": controllers,
    }


def protocol_payload(host_id, operator_id, workload_kind, source, host_facts, created_at=None):
    if workload_kind not in ALLOWED_WORKLOADS:
        raise ValueError(f"workload must be one of: {', '.join(ALLOWED_WORKLOADS)}")
    if not host_id.strip() or not operator_id.strip():
        raise ValueError("host id and operator id must be non-empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "preregistered-independent-host-abba",
        "artifact_status": "SEALED_PREREGISTRATION_REQUIRED_BEFORE_EXECUTION",
        "created_at_utc": created_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "operator_id": operator_id.strip(),
        "declared_independent_host_id": host_id.strip(),
        "source": source,
        "host": host_facts,
        "experiment": {
            "hypothesis": "The preregistered candidate workload-scope CPUWeight effect is repeatable under a shared 100% parent CPU quota.",
            "workload_kind": workload_kind,
            "workers": 2,
            "repetitions_per_block": MIN_PAIRS,
            "duration_seconds_per_mode": 30,
            "steady_window_start_seconds": 12,
            "minimum_steady_samples_per_mode": MIN_STEADY_SAMPLES,
            "block_order": ["A", "B", "B2", "A2"],
            "blocks": [dict(block) for block in BLOCKS],
            "within_block_mode_order": WITHIN_BLOCK_MODE_ORDER,
            "capacity_model": CAPACITY_MODEL,
            "preserve_all_samples": True,
        },
        "acceptance": dict(EXPECTED_ACCEPTANCE),
        "run_contract": dict(RUN_CONTRACT),
    }


def validate_payload(payload):
    errors = []
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return ["unsupported or malformed preregistration schema"]
    if payload.get("artifact_kind") != "preregistered-independent-host-abba":
        errors.append("artifact_kind mismatch")
    if payload.get("workload_kind") is not None:
        errors.append("unexpected top-level workload_kind")
    experiment = payload.get("experiment", {})
    acceptance = payload.get("acceptance", {})
    run_contract = payload.get("run_contract", {})
    if not isinstance(experiment, dict):
        return ["experiment must be a JSON object"]
    if not isinstance(acceptance, dict):
        return ["acceptance must be a JSON object"]
    if not isinstance(run_contract, dict):
        return ["run_contract must be a JSON object"]
    blocks = experiment.get("blocks", [])
    if experiment.get("block_order") != ["A", "B", "B2", "A2"]:
        errors.append("block order must remain A/B/B2/A2")
    if blocks != list(BLOCKS):
        errors.append("block weights/order must remain A=20, B=80, B2=80, A2=20")
    expected_experiment = {
        "workers": 2,
        "repetitions_per_block": MIN_PAIRS,
        "duration_seconds_per_mode": 30,
        "steady_window_start_seconds": 12,
        "minimum_steady_samples_per_mode": MIN_STEADY_SAMPLES,
        "within_block_mode_order": WITHIN_BLOCK_MODE_ORDER,
        "capacity_model": CAPACITY_MODEL,
        "preserve_all_samples": True,
    }
    for key, expected in expected_experiment.items():
        if experiment.get(key) != expected:
            errors.append(f"predeclared experiment value changed: {key}")
    for key, expected in EXPECTED_ACCEPTANCE.items():
        if acceptance.get(key) != expected:
            errors.append(f"predeclared acceptance value changed: {key}")
    if run_contract != RUN_CONTRACT:
        errors.append("run contract differs from the fixed approved execution contract")
    if payload.get("artifact_status") != "SEALED_PREREGISTRATION_REQUIRED_BEFORE_EXECUTION":
        errors.append("artifact is not a pre-execution sealed preregistration")
    if not str(payload.get("operator_id", "")).strip() or not str(payload.get("declared_independent_host_id", "")).strip():
        errors.append("operator/host identity labels are missing")
    source = payload.get("source", {})
    if not isinstance(source, dict):
        return errors + ["source must be a JSON object"]
    if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision", ""))):
        errors.append("source revision must be a full 40-hex commit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("tracked_diff_sha256", ""))) or source.get("tracked_dirty") is not False:
        errors.append("tracked source must be clean and carry its diff SHA-256")
    host = payload.get("host", {})
    if not isinstance(host, dict):
        return errors + ["host must be a JSON object"]
    if not host.get("hostname") or not host.get("os_release") or not host.get("kernel"):
        errors.append("host identity facts are incomplete")
    if host.get("cgroup_v2") is not True or not isinstance(host.get("cgroup_controllers"), list) or "cpu" not in host.get("cgroup_controllers", []):
        errors.append("host must report cgroup v2 with the CPU controller")
    if not isinstance(host.get("logical_cpus"), int) or host.get("logical_cpus", 0) < 1:
        errors.append("host logical CPU count must be a positive integer")
    if payload.get("experiment", {}).get("workload_kind") not in ALLOWED_WORKLOADS:
        errors.append("unsupported/missing declared workload kind")
    try:
        created_at = datetime.datetime.fromisoformat(str(payload.get("created_at_utc", "")).replace("Z", "+00:00"))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            errors.append("created_at_utc must include an explicit timezone")
    except ValueError:
        errors.append("created_at_utc must be a valid ISO-8601 timestamp")
    return errors


def write_sealed_preregistration(directory, payload):
    """Create a unique directory with create-only protocol and checksum files."""
    directory = pathlib.Path(directory)
    errors = validate_payload(payload)
    if errors:
        raise ValueError("invalid preregistration: " + "; ".join(errors))
    directory.mkdir(parents=False, exist_ok=False)
    protocol_path = directory / "protocol.json"
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    with protocol_path.open("xb") as stream:
        stream.write(encoded)
    digest = sha256_bytes(encoded)
    manifest_path = directory / "SHA256SUMS.txt"
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(f"{digest}  protocol.json\n")
    protocol_path.chmod(0o444)
    manifest_path.chmod(0o444)
    return digest


def verify_sealed_preregistration(directory):
    directory = pathlib.Path(directory)
    protocol_path = directory / "protocol.json"
    manifest_path = directory / "SHA256SUMS.txt"
    if not directory.is_dir() or directory.is_symlink() or protocol_path.is_symlink() or manifest_path.is_symlink():
        return None, None, ["preregistration directory or files are missing/unsafe"]
    try:
        if not protocol_path.is_file() or not manifest_path.is_file():
            return None, None, ["preregistration protocol and manifest must be regular files"]
        unexpected = {item.name for item in directory.iterdir()} - {"protocol.json", "SHA256SUMS.txt"}
        if unexpected:
            return None, None, ["unexpected files in sealed preregistration directory: " + ", ".join(sorted(unexpected))]
        lines = manifest_path.read_text().splitlines()
        if len(lines) != 1:
            return None, None, ["checksum manifest must contain exactly one protocol entry"]
        digest, separator, name = lines[0].partition("  ")
        raw = protocol_path.read_bytes()
        if not separator or name != "protocol.json" or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None, None, ["malformed preregistration checksum manifest"]
        actual = sha256_bytes(raw)
        if actual != digest:
            return None, actual, ["protocol SHA-256 does not match preregistration manifest"]
        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result
        payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, None, [f"could not read preregistration artifact: {exc}"]
    errors = validate_payload(payload)
    return payload, actual, errors


def verify_run_binding(directory, block, host_name, source_revision, workload_kind,
                       repetitions, duration, workers, cpu_weight):
    """Verify a requested benchmark block against the sealed preregistration."""
    payload, digest, errors = verify_sealed_preregistration(directory)
    if errors:
        return None, None, errors
    if block not in {item["block"] for item in BLOCKS}:
        errors.append("unknown preregistration block")
        return payload, digest, errors
    selected = next(item for item in BLOCKS if item["block"] == block)
    experiment = payload["experiment"]
    source = payload["source"]
    host = payload["host"]
    expected_host = host.get("hostname")
    if host_name != expected_host:
        errors.append(f"execution hostname mismatch: expected {expected_host}, observed {host_name}")
    if source_revision != source.get("revision"):
        errors.append("source revision differs from sealed preregistration")
    if workload_kind != experiment.get("workload_kind"):
        errors.append("workload kind differs from sealed preregistration")
    if repetitions != experiment.get("repetitions_per_block"):
        errors.append("repetition count differs from sealed preregistration")
    if not math.isclose(float(duration), float(experiment.get("duration_seconds_per_mode", -1)), rel_tol=0, abs_tol=1e-9):
        errors.append("duration differs from sealed preregistration")
    if workers != experiment.get("workers"):
        errors.append("worker count differs from sealed preregistration")
    if cpu_weight != selected["cpu_weight"]:
        errors.append(f"block {block} requires CPUWeight={selected['cpu_weight']}, observed {cpu_weight}")
    return payload, digest, errors


def cli_create(args):
    root = ROOT.resolve()
    output = pathlib.Path(args.output)
    if not output.is_absolute():
        output = root / output
    parent = output.parent.resolve()
    results_root = (root / "benchmarks" / "results").resolve()
    if parent != results_root and results_root not in parent.parents:
        raise ValueError("preregistration output must resolve inside benchmarks/results")
    if not parent.is_dir():
        raise ValueError("output parent must already exist")
    if not args.confirm_independent_host or not args.confirm_approved_window:
        raise ValueError("explicit --confirm-independent-host and --confirm-approved-window are required")
    source = tracked_source_state(root)
    host = read_host_facts()
    if not host["cgroup_v2"] or "cpu" not in host["cgroup_controllers"]:
        raise ValueError("host does not report cgroup v2 CPU controller; do not preregister an unsupported runtime")
    payload = protocol_payload(args.host_id, args.operator_id, args.workload_kind, source, host)
    digest = write_sealed_preregistration(output, payload)
    print(f"preregistration_status=SEALED")
    print(f"protocol_path={output / 'protocol.json'}")
    print(f"protocol_sha256={digest}")
    print("execution_status=NOT_RUN; sealing is not runtime or acceptance evidence")
    return 0


def cli_verify(args):
    payload, digest, errors = verify_sealed_preregistration(args.directory)
    if errors:
        print("preregistration_status=INVALID", file=sys.stderr)
        for error in errors:
            print(f"error={error}", file=sys.stderr)
        return 2
    print("preregistration_status=SEALED_VERIFIED")
    print(f"protocol_sha256={digest}")
    print(f"source_revision={payload['source']['revision']}")
    print(f"host={payload['host']['hostname']}")
    print(f"workload_kind={payload['experiment']['workload_kind']}")
    print("execution_status=NOT_PROVEN_BY_VERIFICATION")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="seal a preregistration on the approved independent host")
    create.add_argument("--output", required=True, help="new directory below benchmarks/results")
    create.add_argument("--host-id", required=True, help="operator-defined host identity label recorded in the protocol")
    create.add_argument("--operator-id", required=True, help="operator identity or ticket reference")
    create.add_argument("--workload-kind", required=True, choices=ALLOWED_WORKLOADS)
    create.add_argument("--confirm-independent-host", action="store_true", help="confirm this host is independent of earlier runs")
    create.add_argument("--confirm-approved-window", action="store_true", help="confirm an approved test window exists")
    create.set_defaults(func=cli_create)
    verify = subparsers.add_parser("verify", help="verify only the sealed protocol file checksum/schema")
    verify.add_argument("directory", type=pathlib.Path)
    verify.set_defaults(func=cli_verify)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"preregistration_status=INCOMPLETE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
