#!/usr/bin/env python3
"""Passive live PSI/controller evidence collector; never creates workload or writes quota.

Usage: live-psi-observe.py OUTPUT_DIR [DURATION_SECONDS=3600] [INTERVAL_SECONDS=5]
The resulting trace is observations only. Acceptance still requires a genuine
>=40% PSI sample, matching production-controller decision logs, and reviewed
effective quota before/after evidence; a collection without those is NOT RUN.
"""
import datetime
import os
import pathlib
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
    if out.exists():
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
    for row in rows:
        columns = row.split("\t")
        try:
            if float(columns[2]) >= 40.0:
                crossings.append((columns[0], columns[2], columns[11]))
        except (ValueError, IndexError):
            continue
    journal_text = (out / "controller-journal.log").read_text()
    matching_decisions = [line for line in journal_text.splitlines()
                          if "quota changed unit=" in line and "pressure=" in line]
    status = "NOT_RUN"
    if not stop_requested["value"] and crossings and matching_decisions and journal_ok:
        status = "REVIEW_REQUIRED"
    with (out / "manifest.txt").open("x") as manifest:
        manifest.write(f"started_at={start_wall}\nended_at={end_wall}\nrequested_duration_seconds={duration}\n")
        manifest.write(f"observed_duration_seconds={time.monotonic()-start_mono:.3f}\nsample_count={len(rows)}\n")
        manifest.write(f"service_mainpid_start={start_pid}\nservice_mainpid_end={end_pid}\n")
        manifest.write(f"service_nrestarts_start={start_restarts}\nservice_nrestarts_end={end_restarts}\n")
        manifest.write(f"quota_effective_start={start_quota}\nquota_effective_end={end_quota}\n")
        manifest.write(f"psi_some_avg10_ge_40_percent_samples={len(crossings)}\n")
        manifest.write(f"controller_quota_change_lines={len(matching_decisions)}\njournal_capture={'PASS' if journal_ok else 'FAIL'}\n")
        manifest.write(f"collection_status={'INCOMPLETE' if stop_requested['value'] else 'COMPLETE'}\nacceptance_status={status}\n")
        manifest.write("note=Passive observation only; correlate crossing timestamp, controller decision, and effective quota transition manually. No workload/config/quota changes made.\n")
    print(f"evidence_dir={out}\nsamples={len(rows)}\npsi_high_samples={len(crossings)}\ncontroller_quota_change_lines={len(matching_decisions)}\nacceptance_status={status}")
    if not journal_ok:
        return 1
    return 130 if stop_requested["value"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
