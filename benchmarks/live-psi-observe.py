#!/usr/bin/env python3
"""Passive live PSI/controller evidence collector; never creates workload or writes quota.

Usage: live-psi-observe.py OUTPUT_DIR [DURATION_SECONDS=3600] [INTERVAL_SECONDS=5]
The resulting trace is observations only. Acceptance still requires a genuine
>=40% PSI sample, matching production-controller decision logs, and reviewed
effective quota before/after evidence; a collection without those is NOT RUN.
"""
import datetime
import hashlib
import math
import os
import pathlib
import platform
import re
import signal
import subprocess
import sys
import time


def command(args):
    try:
        return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None


def property_value(unit, prop):
    result = command(["systemctl", "--user", "show", unit, f"--property={prop}", "--value"])
    if result is None or result.returncode:
        return "UNAVAILABLE"
    value = result.stdout.strip()
    return value if value else "UNAVAILABLE"


def valid_quota_readback(value):
    """Require a finite positive systemd CPUQuotaPerSecUSec readback."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)", value.strip())
    if not match:
        return False
    amount = float(match.group(1))
    scale = {"us": 1e-6, "ms": 1e-3, "s": 1.0}[match.group(2)]
    return math.isfinite(amount) and amount > 0 and math.isfinite(amount * scale)


def quota_readback_percent(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)", value.strip())
    if not match:
        return None
    scale = {"us": 1e-6, "ms": 1e-3, "s": 1.0}[match.group(2)]
    result = float(match.group(1)) * scale * 100.0
    return result if math.isfinite(result) and result > 0 else None


def service_identity_errors(rows, start_pid, start_restarts, end_pid, end_restarts, start_quota, end_quota):
    """Reject a purported continuous trace if the service identity/counter resets."""
    errors = []
    if not start_pid.isdigit() or int(start_pid) <= 0:
        errors.append("service MainPID baseline unavailable or invalid")
    if not start_restarts.isdigit():
        errors.append("service restart-counter baseline unavailable or invalid")
    if not end_pid.isdigit() or end_pid != start_pid:
        errors.append("service MainPID changed or became unavailable during observation")
    if not end_restarts.isdigit() or end_restarts != start_restarts:
        errors.append("service restart counter changed or became unavailable during observation")
    if not valid_quota_readback(start_quota):
        errors.append("effective quota baseline unavailable or invalid")
    if not valid_quota_readback(end_quota):
        errors.append("effective quota final readback unavailable or invalid")
    for number, columns in enumerate(rows, 1):
        if len(columns) < 12:
            errors.append(f"malformed service identity fields in sample {number}")
            continue
        if not columns[9].isdigit() or columns[9] != start_pid:
            errors.append(f"service MainPID discontinuity in sample {number}")
        if not columns[10].isdigit() or columns[10] != start_restarts:
            errors.append(f"service restart-counter discontinuity in sample {number}")
        if not valid_quota_readback(columns[11]):
            errors.append(f"invalid effective quota readback in sample {number}")
    return sorted(set(errors))


def sample_trace_errors(rows):
    """Validate the full sample sequence before it can support G6 correlation."""
    errors = []
    previous_wall = None
    previous_mono = None
    for number, columns in enumerate(rows, 1):
        if len(columns) != 12:
            errors.append(f"sample {number} has {len(columns)} columns; expected exactly 12")
            continue
        try:
            wall = datetime.datetime.fromisoformat(columns[0].replace("Z", "+00:00"))
            mono = float(columns[1])
            wall_valid = wall.tzinfo is not None and wall.utcoffset() is not None
            if not wall_valid:
                errors.append(f"sample {number} wall timestamp has no timezone")
            if not math.isfinite(mono) or mono < 0:
                errors.append(f"sample {number} monotonic timestamp is invalid")
            if wall_valid and previous_wall is not None and wall <= previous_wall:
                errors.append("wall timestamps are duplicate or out of order")
            if math.isfinite(mono) and mono >= 0 and previous_mono is not None and mono <= previous_mono:
                errors.append("monotonic timestamps are duplicate or out of order")
            if wall_valid:
                previous_wall = wall
            if math.isfinite(mono) and mono >= 0:
                previous_mono = mono
        except (ValueError, OverflowError):
            errors.append(f"sample {number} timestamp is malformed")
        for column in columns[2:8]:
            try:
                value = float(column)
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError
            except ValueError:
                errors.append(f"sample {number} contains missing or invalid PSI value")
                break
        if columns[8] != "active":
            errors.append(f"sample {number} service is not active")
        if not columns[9].isdigit() or int(columns[9]) <= 0:
            errors.append(f"sample {number} MainPID is invalid")
        if not columns[10].isdigit():
            errors.append(f"sample {number} restart counter is invalid")
        if not valid_quota_readback(columns[11]):
            errors.append(f"sample {number} effective quota is invalid")
    return sorted(set(errors))


def correlate_crossings(rows, journal_text, interval, expected_unit="heavy-workload.slice"):
    """Find PSI crossings with a nearby decision and changed effective quota readback.

    This emits review candidates, never acceptance. Timestamp, exact unit, logged
    pressure, and before/after effective quota samples must all agree.
    """
    if sample_trace_errors(rows):
        return []
    parsed_samples = []
    for columns in rows:
        try:
            stamp = datetime.datetime.fromisoformat(columns[0].replace("Z", "+00:00"))
            psi = float(columns[2])
            quota = columns[11]
            if not math.isfinite(psi) or not valid_quota_readback(quota):
                continue
            parsed_samples.append((stamp, psi, quota))
        except (ValueError, IndexError):
            continue
    decisions = []
    pattern = re.compile(
        r"quota changed unit=([^ ]+) old=([0-9]+(?:\.[0-9]+)?)% "
        r"new=([0-9]+(?:\.[0-9]+)?)% pressure=([0-9]+(?:\.[0-9]+)?)"
    )
    for line in journal_text.splitlines():
        match = pattern.search(line)
        if not match or match.group(1) != expected_unit or match.group(2) == match.group(3):
            continue
        try:
            stamp = datetime.datetime.fromisoformat(line.split(None, 1)[0].replace("Z", "+00:00"))
            pressure_value = float(match.group(4))
            if math.isfinite(pressure_value) and pressure_value >= 0.40:
                decisions.append((stamp, match.group(1), match.group(2), match.group(3), pressure_value, line))
        except (ValueError, IndexError):
            continue
    tolerance = max(10.0, interval * 2.0)
    correlated = []
    for crossing_stamp, psi, _ in parsed_samples:
        if psi < 40.0:
            continue
        for decision_stamp, unit, old_quota, new_quota, pressure_value, line in decisions:
            delta = abs((decision_stamp - crossing_stamp).total_seconds())
            if delta > tolerance:
                continue
            before = [item for item in parsed_samples if item[0] <= decision_stamp]
            after = [item for item in parsed_samples if item[0] > decision_stamp]
            if not before or not after:
                continue
            quota_before_pct = quota_readback_percent(before[-1][2])
            quota_after_pct = quota_readback_percent(after[0][2])
            if (quota_before_pct is None or quota_after_pct is None
                    or not math.isclose(quota_before_pct, float(old_quota), rel_tol=0.0, abs_tol=0.05)
                    or not math.isclose(quota_after_pct, float(new_quota), rel_tol=0.0, abs_tol=0.05)):
                continue
            correlated.append({
                "crossing_at": crossing_stamp.isoformat(),
                "crossing_psi_some_avg10_percent": f"{psi:.4f}",
                "decision_at": decision_stamp.isoformat(),
                "decision_delta_seconds": f"{delta:.3f}",
                "decision_unit": unit,
                "decision_old_quota_percent": old_quota,
                "decision_new_quota_percent": new_quota,
                "decision_pressure": f"{pressure_value:.4f}",
                "effective_quota_before_readback": before[-1][2],
                "effective_quota_after_readback": after[0][2],
                "decision_log_line": line,
            })
    unique = {}
    for event in correlated:
        unique[(event["crossing_at"], event["decision_at"], event["decision_unit"])] = event
    return [unique[key] for key in sorted(unique)]


def pressure():
    values = {"some_avg10": "", "some_avg60": "", "some_avg300": "",
              "full_avg10": "", "full_avg60": "", "full_avg300": ""}
    try:
        rows = pathlib.Path("/proc/pressure/cpu").read_text().splitlines()
    except OSError:
        return values
    for row in rows:
        parts = row.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        for field in parts[1:]:
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            name = f"{parts[0]}_{key}"
            if name in values:
                try:
                    number = float(value)
                    if 0 <= number <= 100 and number == number and abs(number) != float("inf"):
                        values[name] = f"{number:.4f}"
                except ValueError:
                    pass
    return values


def write_provenance(out, start_wall, duration, interval):
    """Capture source and host identity without upgrading observations to acceptance."""
    def command(args):
        try:
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=3, check=False)
            return result.stdout.strip() if result.returncode == 0 else "unavailable"
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    try:
        os_release = pathlib.Path("/etc/os-release").read_text().replace("\n", ";")
    except OSError:
        os_release = "unavailable"
    try:
        controllers = " ".join(pathlib.Path("/sys/fs/cgroup/cgroup.controllers").read_text().split())
    except OSError:
        controllers = "unavailable"
    status = command(["git", "status", "--porcelain", "--untracked-files=no"])
    try:
        diff_result = subprocess.run(["git", "diff", "HEAD", "--binary"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=3, check=False)
        diff_sha = hashlib.sha256(diff_result.stdout).hexdigest() if diff_result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        diff_sha = "unavailable"
    systemd_version = command(["systemctl", "--version"])
    try:
        process_cgroup = pathlib.Path("/proc/self/cgroup").read_text().replace("\n", ";")
    except OSError:
        process_cgroup = "unavailable"
    values = {
        "recorded_at": start_wall,
        "source_revision": command(["git", "rev-parse", "HEAD"]),
        "source_branch": command(["git", "branch", "--show-current"]),
        "tracked_worktree": "clean" if status == "" else ("dirty" if status != "unavailable" else "unavailable"),
        "tracked_diff_sha256": diff_sha,
        "hostname": platform.node(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "os_release": os_release,
        "systemd_version": systemd_version.splitlines()[0] if systemd_version != "unavailable" else "unavailable",
        "cgroup_v2": str(pathlib.Path("/sys/fs/cgroup/cgroup.controllers").is_file()).lower(),
        "cgroup_controllers": controllers,
        "process_cgroup": process_cgroup,
        "requested_duration_seconds": duration,
        "sampling_interval_seconds": interval,
        "collection_mode": "passive-only; no workload generation, service lifecycle, config or quota writes",
    }
    with (out / "provenance.txt").open("x") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def seal_evidence(out):
    """Create and verify a create-only SHA-256 manifest for finalized run files."""
    excluded = {"SHA256SUMS.txt", "manifest-verification.txt", "manifest-digest.txt"}
    entries = list(out.iterdir())
    unsafe_entries = sorted(path.name for path in entries
                            if path.name not in excluded and (path.is_symlink() or not path.is_file()))
    files = sorted(path for path in entries
                   if path.is_file() and not path.is_symlink() and path.name not in excluded)
    manifest = out / "SHA256SUMS.txt"
    with manifest.open("x") as stream:
        for path in files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stream.write(f"{digest}  {path.name}\n")
    errors = [f"unsafe evidence entry (symlink or non-regular file): {name}"
              for name in unsafe_entries]
    for line in manifest.read_text().splitlines():
        try:
            expected, name = line.split("  ", 1)
            path = out / name
            if path.parent != out or path.is_symlink() or not path.is_file():
                errors.append(f"unsafe or missing manifest entry: {name}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                errors.append(f"SHA-256 mismatch: {name}")
        except ValueError:
            errors.append("malformed SHA-256 manifest line")
    with (out / "manifest-verification.txt").open("x") as stream:
        stream.write(f"manifest_entries={len(files)}\n")
        stream.write(f"verification_status={'PASS' if not errors else 'FAIL'}\n")
        for error in errors:
            stream.write(f"error={error}\n")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with (out / "manifest-digest.txt").open("x") as stream:
        stream.write(f"sha256={digest}  SHA256SUMS.txt\n")
    if errors:
        raise RuntimeError("evidence manifest verification failed: " + "; ".join(errors))
    return digest, len(files)


def main():
    if len(sys.argv) not in (2, 3, 4):
        raise SystemExit("usage: live-psi-observe.py OUTPUT_DIR [DURATION_SECONDS=3600] [INTERVAL_SECONDS=5]")
    out = pathlib.Path(sys.argv[1]).expanduser()
    try:
        duration = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
        interval = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    except ValueError:
        raise SystemExit("duration must be a positive integer and interval a positive number")
    if duration < 1 or interval < 0.2:
        raise SystemExit("duration must be >=1s and interval must be >=0.2s")
    if out.exists() or out.is_symlink():
        raise SystemExit(f"refusing to overwrite existing evidence directory: {out}")
    out.mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)
    stop_requested = {"value": False}

    def request_stop(_signum, _frame):
        stop_requested["value"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    start_wall = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    start_mono = time.monotonic()
    write_provenance(out, start_wall, duration, interval)
    start_pid = property_value("workload-router.service", "MainPID")
    start_restarts = property_value("workload-router.service", "NRestarts")
    start_quota = property_value("heavy-workload.slice", "CPUQuotaPerSecUSec")
    fields = ["timestamp", "monotonic_seconds", "some_avg10_percent", "some_avg60_percent",
              "some_avg300_percent", "full_avg10_percent", "full_avg60_percent",
              "full_avg300_percent", "service_active", "service_mainpid", "service_nrestarts",
              "quota_effective_property"]
    sample_path = out / "samples.tsv"
    with sample_path.open("x") as samples:
        samples.write("\t".join(fields) + "\n")
        deadline = start_mono
        while not stop_requested["value"]:
            now_mono = time.monotonic()
            if now_mono - start_mono > duration:
                break
            timestamp = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
            psi = pressure()
            active = property_value("workload-router.service", "ActiveState")
            pid = property_value("workload-router.service", "MainPID")
            restarts = property_value("workload-router.service", "NRestarts")
            quota = property_value("heavy-workload.slice", "CPUQuotaPerSecUSec")
            row = [timestamp, f"{now_mono-start_mono:.6f}", psi["some_avg10"], psi["some_avg60"],
                   psi["some_avg300"], psi["full_avg10"], psi["full_avg60"], psi["full_avg300"],
                   active, pid, restarts, quota]
            samples.write("\t".join(row) + "\n")
            samples.flush()
            deadline += interval
            time.sleep(max(0.0, deadline - time.monotonic()))
    end_wall = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    end_pid = property_value("workload-router.service", "MainPID")
    end_restarts = property_value("workload-router.service", "NRestarts")
    end_quota = property_value("heavy-workload.slice", "CPUQuotaPerSecUSec")
    journal = command(["journalctl", "--user", "-u", "workload-router.service",
                       "--since", start_wall, "--until", end_wall, "--no-pager", "--output=short-iso"])
    journal_ok = journal is not None and journal.returncode == 0
    (out / "controller-journal.log").write_text(journal.stdout if journal_ok else "journal capture unavailable\n")
    rows = sample_path.read_text().splitlines()[1:]
    crossings = []
    sample_times = []
    integrity_errors = []
    for path in out.iterdir():
        if path.is_symlink() or not path.is_file():
            integrity_errors.append(f"unsafe evidence entry (symlink or non-regular file): {path.name}")
    for row in rows:
        columns = row.split("\t")
        try:
            sample_times.append(float(columns[1]))
            # Missing PSI or runtime identity is a telemetry gap, not a zero.
            if (any(not value for value in columns[2:8]) or
                    any(value == "UNAVAILABLE" for value in columns[8:12]) or
                    columns[8] != "active"):
                integrity_errors.append("missing critical PSI/service/quota telemetry")
            if float(columns[2]) >= 40.0:
                crossings.append((columns[0], columns[2], columns[11]))
        except (ValueError, IndexError):
            integrity_errors.append("malformed sample row or PSI value")
    integrity_errors.extend(sample_trace_errors([row.split("\t") for row in rows]))
    integrity_errors.extend(service_identity_errors(
        [row.split("\t") for row in rows], start_pid, start_restarts, end_pid, end_restarts,
        start_quota, end_quota
    ))
    # The collector is passive, but it must not certify a trace with missed
    # sampling deadlines or an observation window shorter than requested.
    max_gap = max(interval * 2.0, interval + 1.0)
    for previous, current in zip(sample_times, sample_times[1:]):
        if current <= previous or current - previous > max_gap:
            integrity_errors.append("sample cadence gap or monotonic ordering violation")
            break
    observed_duration = time.monotonic() - start_mono
    if not stop_requested["value"] and observed_duration < duration:
        integrity_errors.append("observation ended before requested duration")
    minimum_samples = max(1, int(duration / interval))
    if not stop_requested["value"] and len(rows) < minimum_samples:
        integrity_errors.append("sample count below duration/interval minimum")
    journal_text = (out / "controller-journal.log").read_text()
    correlated_events = correlate_crossings([row.split("\t") for row in rows], journal_text, interval)
    status = "NOT_RUN"
    collection_complete = not stop_requested["value"] and not integrity_errors and journal_ok
    if collection_complete and correlated_events:
        status = "REVIEW_REQUIRED"
    with (out / "manifest.txt").open("x") as manifest:
        manifest.write(f"started_at={start_wall}\nended_at={end_wall}\nrequested_duration_seconds={duration}\n")
        manifest.write(f"observed_duration_seconds={observed_duration:.3f}\nsample_count={len(rows)}\n")
        manifest.write(f"service_mainpid_start={start_pid}\nservice_mainpid_end={end_pid}\n")
        manifest.write(f"service_nrestarts_start={start_restarts}\nservice_nrestarts_end={end_restarts}\n")
        manifest.write(f"quota_effective_start={start_quota}\nquota_effective_end={end_quota}\n")
        manifest.write(f"psi_some_avg10_ge_40_percent_samples={len(crossings)}\n")
        manifest.write(f"correlated_psi_controller_quota_events={len(correlated_events)}\njournal_capture={'PASS' if journal_ok else 'FAIL'}\n")
        manifest.write(f"collection_status={'COMPLETE' if collection_complete else 'INCOMPLETE'}\nacceptance_status={status}\n")
        manifest.write(f"integrity_error_count={len(integrity_errors)}\n")
        for error in sorted(set(integrity_errors)):
            manifest.write(f"integrity_error={error}\n")
        for index, event in enumerate(correlated_events, 1):
            for key, value in event.items():
                manifest.write(f"correlated_event_{index}_{key}={value}\n")
        manifest.write("note=Passive observation only; correlate crossing timestamp, controller decision, and effective quota transition manually. No workload/config/quota changes made.\n")
    digest, manifest_entries = seal_evidence(out)
    print(f"evidence_dir={out}\nsamples={len(rows)}\npsi_high_samples={len(crossings)}\ncorrelated_psi_controller_quota_events={len(correlated_events)}\nacceptance_status={status}")
    print(f"manifest_entries={manifest_entries}\nmanifest_sha256={digest}\nmanifest_verification=PASS")
    if stop_requested["value"]:
        return 130
    if not journal_ok or not collection_complete:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
