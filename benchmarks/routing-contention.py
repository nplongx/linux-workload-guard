#!/usr/bin/env python3
"""Compare interactive-task latency with a dedicated router off/on under CPU load.

Only unique temporary user slices and a directly spawned test router are used.
The installed workload-router.service, production configuration, and production
slice quotas are never stopped, rewritten, or changed.
"""
import csv
import math
import os
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time

REPS = int(os.getenv("REPS", "3"))
DURATION = float(os.getenv("DURATION", "30"))
WORKERS = int(os.getenv("WORKERS", "1"))
REQUIRE_ROUTED = os.getenv("REQUIRE_ROUTED", "true").lower() in ("1", "true", "yes", "on")
RESULT = pathlib.Path(os.getenv("RESULT", "/tmp/routing-contention.tsv"))
CGROOT = pathlib.Path("/sys/fs/cgroup")
ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = f"lwgb{os.getuid()}x{os.getpid()}"
PARENT_UNIT = f"{TAG}parent.slice"
HEAVY_UNIT = f"{PARENT_UNIT[:-6]}-heavy.slice"
RECOVERY_UNIT = f"{PARENT_UNIT[:-6]}-recovery.slice"
UNIT_DIR = pathlib.Path(os.getenv("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "systemd/user"
ROUTER_PROCESS = None
ROUTER_LOG_PATH = None
ROUTER_STATE_PATH = None
ROUTER_ENV = None
CREATED_UNIT_FILES = []

def ctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, check=check, timeout=10)
def show(unit, prop):
    return ctl("show", unit, f"--property={prop}", "--value").stdout.strip()
def cgroup_path(unit):
    p = show(unit, "ControlGroup")
    return CGROOT / p.lstrip("/")
def pid_in(unit, pid):
    p = cgroup_path(unit)
    try:
        for root, dirs, files in os.walk(p):
            if "cgroup.procs" in files:
                try:
                    if str(pid) in pathlib.Path(root, "cgroup.procs").read_text().split():
                        return True
                except OSError: pass
    except OSError: pass
    return False

def pct(xs, p):
    if not xs: return float("nan")
    ys = sorted(xs)
    return ys[min(len(ys)-1, max(0, math.ceil(len(ys)*p)-1))]
def write_worker_files(directory):
    hog = directory / "hog.py"
    hog.write_text('''import os,sys,time\npidfile,progress=sys.argv[1:3]\nopen(pidfile,"w").write(str(os.getpid()))\nx=0x123456789abcdef0; count=0; deadline=time.monotonic()+1.0\nwith open(progress,"a",buffering=1) as f:\n while True:\n  x ^= (x << 7) & ((1<<64)-1); x ^= x >> 9; x=(x*0x9e3779b97f4a7c15)&((1<<64)-1); count += 1\n  now=time.monotonic()\n  if now >= deadline:\n   f.write(f"{now:.6f}\\t{count}\\n"); deadline += 1.0\n''')
    probe = directory / "probe.py"
    probe.write_text('''import os,sys,time\nout=sys.argv[1]; duration=float(sys.argv[2]); end=time.monotonic()+duration; rows=[]; period=.020; deadline=time.monotonic()+period\nwhile deadline<end:\n time.sleep(max(0,deadline-time.monotonic()))\n woke=time.monotonic(); wake_ms=max(0.0,(woke-deadline)*1000); t=time.monotonic_ns(); x=0\n for i in range(5000): x=(x*1664525+i+1013904223)&0xffffffff\n work_ms=(time.monotonic_ns()-t)/1e6\n rows.append((woke,wake_ms,work_ms)); deadline+=period\n if deadline < time.monotonic()-period: deadline=time.monotonic()+period\nwith open(out,"w") as f:\n for t,w,dt in rows: f.write(f"{t:.6f}\\t{w:.6f}\\t{dt:.6f}\\n")\n''')
    return hog, probe

def launch_scope(unit, command):
    return subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
                             f"--slice={PARENT_UNIT}", "--", *command],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
def stop_scope(unit):
    ctl("stop", unit, check=False)

def stop_router():
    global ROUTER_PROCESS
    if ROUTER_PROCESS is not None:
        if ROUTER_PROCESS.poll() is None:
            ROUTER_PROCESS.terminate()
            try:
                ROUTER_PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ROUTER_PROCESS.kill()
                ROUTER_PROCESS.wait(timeout=2)
        ROUTER_PROCESS = None

def router_process_metrics():
    """Return CPU ticks and peak RSS KiB for the isolated router process."""
    if ROUTER_PROCESS is None:
        return None, None
    try:
        raw = pathlib.Path(f"/proc/{ROUTER_PROCESS.pid}/stat").read_text()
        tail = raw[raw.rfind(")") + 2:].split()
        cpu_ticks = int(tail[11]) + int(tail[12])  # proc fields 14 and 15
        peak_rss = None
        for line in pathlib.Path(f"/proc/{ROUTER_PROCESS.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                peak_rss = int(line.split()[1])
                break
        return cpu_ticks, peak_rss
    except (OSError, ValueError, IndexError):
        return None, None

def start_router():
    global ROUTER_PROCESS
    stop_router()
    with open(ROUTER_LOG_PATH, "a") as log:
        ROUTER_PROCESS = subprocess.Popen(
            [sys.executable, str(ROOT / "bin" / "workload-router.py")],
            stdout=log, stderr=subprocess.STDOUT, env=ROUTER_ENV,
        )

def run_trial(mode, rep, directory, hog, probe):
    tag=f"lwg-{mode}-{rep}-{os.getpid()}"
    stop_router()
    # Both modes share the same 100% parent budget. In router mode the worker
    # moves into a 100% child slice; the parent's 100% ceiling still caps the
    # aggregate, avoiding the historical 100% baseline vs 200% routed budget.
    if mode == "router":
        start_router()
    time.sleep(2)
    pids=[]; scopes=[]; progress_files=[]
    try:
        for i in range(WORKERS):
            pidfile=directory / f"{tag}-hog{i}.pid"
            progressfile=directory / f"{tag}-hog{i}.progress.tsv"
            p=launch_scope(f"{tag}-hog{i}.scope", [sys.executable, str(hog), str(pidfile), str(progressfile)])
            scopes.append(f"{tag}-hog{i}.scope"); pids.append((pidfile,p)); progress_files.append(progressfile)
        probe_file=directory / f"{tag}-probe.tsv"
        pp=launch_scope(f"{tag}-probe.scope", [sys.executable, str(probe), str(probe_file), str(DURATION)])
        scopes.append(f"{tag}-probe.scope")
        for _ in range(100):
            if all(f.exists() for f,p in pids): break
            time.sleep(.05)
        if not all(f.exists() for f,p in pids): raise RuntimeError("worker PID startup timeout")
        worker_pids=[int(f.read_text()) for f,p in pids]
        router_ticks_start, _ = router_process_metrics()
        start=time.monotonic()
        routed_at=None
        peak_workers_in_heavy=0
        while pp.poll() is None and time.monotonic()-start < DURATION+20:
            in_heavy=sum(pid_in(HEAVY_UNIT, pid) for pid in worker_pids)
            peak_workers_in_heavy=max(peak_workers_in_heavy,in_heavy)
            if mode == "router" and routed_at is None and in_heavy == WORKERS:
                routed_at=time.monotonic()-start
            time.sleep(.25)
        if pp.poll() is None:
            stop_scope(f"{tag}-probe.scope")
            raise RuntimeError("probe scope timed out")
        for p in scopes:
            if "probe" not in p: stop_scope(p)
        for _,p in pids:
            if p.poll() is None:
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired: p.kill()
        rows=[]
        if probe_file.exists():
            for line in probe_file.read_text().splitlines():
                t,wake_ms,work_ms=line.split("\t"); rows.append((float(t),float(wake_ms),float(work_ms)))
        now=time.monotonic()
        if not rows: raise RuntimeError("probe produced no samples")
        # Separate initial routing convergence from steady state. Baseline uses the same elapsed window.
        trial_start=rows[0][0]
        all_ms=[wake_ms for _,wake_ms,_ in rows]
        work_ms=[work for _,_,work in rows]
        steady_rows=[r for r in rows if (r[0]-trial_start >= 12.0 if mode == "baseline" else
                                       (routed_at is not None and r[0]-trial_start >= routed_at+2))]
        steady_ms=[wake for _,wake,_ in steady_rows]
        steady_work_ms=[work for _,_,work in steady_rows]
        steady_start = trial_start + (12.0 if mode == "baseline" else
                                      ((routed_at + 2.0) if routed_at is not None else DURATION + 1.0))
        worker_rates=[]
        for progressfile in progress_files:
            samples=[]
            if progressfile.exists():
                for line in progressfile.read_text().splitlines():
                    try:
                        ts, count = line.split("\t", 1)
                        ts, count = float(ts), int(count)
                        if ts >= steady_start:
                            samples.append((ts, count))
                    except (ValueError, TypeError):
                        continue
            if len(samples) >= 2 and samples[-1][0] > samples[0][0]:
                worker_rates.append((samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]))
        worker_iterations_per_sec = sum(worker_rates) if len(worker_rates) == WORKERS else float("nan")
        router_ticks_end, router_peak_rss_kib = router_process_metrics()
        router_cpu_pct = None
        if (mode == "router" and router_ticks_start is not None and router_ticks_end is not None
                and router_ticks_end >= router_ticks_start and now > start):
            router_cpu_pct = ((router_ticks_end - router_ticks_start) / os.sysconf("SC_CLK_TCK") /
                              (now - start) * 100.0)
        sample_path=RESULT.with_suffix(".samples.tsv")
        with sample_path.open("a") as raw:
            for t,wake,work in rows:
                raw.write(f"{mode}\t{rep}\t{t-trial_start:.6f}\t{wake:.6f}\t{work:.6f}\n")
        route_complete = mode != "router" or peak_workers_in_heavy == WORKERS
        validity_reason = "" if route_complete else f"routing-incomplete:{peak_workers_in_heavy}/{WORKERS}"
        return {"mode":mode,"rep":rep,"samples":len(all_ms),"elapsed_s":round(now-start,3),
                "routed_at_s":"" if routed_at is None else round(routed_at,3),
                "workers_in_heavy":peak_workers_in_heavy,"worker_count":WORKERS,
                "trial_valid":int(route_complete),"validity_reason":validity_reason,
                "all_mean_ms":statistics.mean(all_ms),"all_p50_ms":pct(all_ms,.50),
                "all_p95_ms":pct(all_ms,.95),"all_p99_ms":pct(all_ms,.99),"all_max_ms":max(all_ms),
                "work_p95_ms":pct(work_ms,.95),
                "steady_n":len(steady_ms),"steady_mean_ms":statistics.mean(steady_ms) if steady_ms else float('nan'),
                "steady_p50_ms":pct(steady_ms,.50),"steady_p95_ms":pct(steady_ms,.95),
                "steady_p99_ms":pct(steady_ms,.99),"steady_max_ms":max(steady_ms) if steady_ms else float('nan'),
                "steady_work_p95_ms":pct(steady_work_ms,.95),
                "worker_iterations_per_sec":worker_iterations_per_sec,
                "router_cpu_pct_one_core":router_cpu_pct,
                "router_peak_rss_kib":router_peak_rss_kib if mode == "router" else None,
                "psi_some_avg10":read_psi(),"router_enabled_during_trial":int(mode == "router")}
    finally:
        for unit in scopes: stop_scope(unit)
        stop_router()

def read_psi():
    try:
        for line in pathlib.Path("/proc/pressure/cpu").read_text().splitlines():
            if line.startswith("some "):
                return next(float(x.split("=",1)[1]) for x in line.split() if x.startswith("avg10="))
    except Exception: pass
    return float("nan")

def main():
    global ROUTER_LOG_PATH, ROUTER_STATE_PATH, ROUTER_ENV
    global CREATED_UNIT_FILES
    if REPS < 1 or WORKERS < 1 or DURATION < 20:
        raise SystemExit("REPS and WORKERS must be positive; DURATION must be >=20s for comparable steady windows")
    if not pathlib.Path("/sys/fs/cgroup/cgroup.controllers").exists():
        raise SystemExit("cgroup v2 required")
    sample_path = RESULT.with_suffix(".samples.tsv")
    environment_path = RESULT.with_suffix(".environment.txt")
    ROUTER_LOG_PATH = RESULT.with_suffix(".router.log")
    output_paths = (RESULT, sample_path, environment_path, ROUTER_LOG_PATH)
    for path in output_paths:
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing benchmark evidence: {path}")
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    benchmark_status = "INCOMPLETE"
    unit_specs = (
        (PARENT_UNIT, "[Unit]\nDescription=Temporary isolated routing benchmark protected parent\n\n[Slice]\nCPUQuota=100%\nCPUWeight=1000\n"),
        (HEAVY_UNIT, f"[Unit]\nDescription=Temporary isolated routing benchmark heavy child\n\n[Slice]\nCPUQuota=100%\nCPUWeight=100\n"),
        (RECOVERY_UNIT, "[Unit]\nDescription=Temporary isolated routing benchmark recovery child\n\n[Slice]\nCPUWeight=1000\n"),
    )
    for unit, contents in unit_specs:
        path = UNIT_DIR / unit
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing user unit: {path}")
    try:
        for unit, contents in unit_specs:
            path = UNIT_DIR / unit
            with path.open("x") as f:
                f.write(contents)
            CREATED_UNIT_FILES.append(path)
        with RESULT.open("x", newline="") as f:
            fields=["mode","rep","samples","elapsed_s","routed_at_s","workers_in_heavy","worker_count","trial_valid","validity_reason","all_mean_ms","all_p50_ms","all_p95_ms","all_p99_ms","all_max_ms","work_p95_ms","steady_n","steady_mean_ms","steady_p50_ms","steady_p95_ms","steady_p99_ms","steady_max_ms","steady_work_p95_ms","worker_iterations_per_sec","router_cpu_pct_one_core","router_peak_rss_kib","psi_some_avg10","router_enabled_during_trial"]
            writer=csv.DictWriter(f,fieldnames=fields,delimiter="\t",lineterminator="\n"); writer.writeheader(); f.flush()
        with sample_path.open("x") as f:
            f.write("mode\trep\trelative_s\twakeup_lateness_ms\twork_duration_ms\n")
        with environment_path.open("x") as f:
            f.write(f"started_at={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
            f.write(f"host={os.uname()}\npython={sys.version.replace(chr(10), ' ')}\nlogical_cpus={os.cpu_count()}\n")
            f.write(f"systemd={ctl('--version').stdout.splitlines()[0]}\ncgroup_mode=v2\n")
            f.write(f"repetitions={REPS}\nworkers={WORKERS}\nduration_seconds={DURATION}\n")
            f.write(f"parent_unit={PARENT_UNIT}\nheavy_unit={HEAVY_UNIT}\nrecovery_unit={RECOVERY_UNIT}\n")
            f.write("parent_cpu_quota=100%; heavy_child_cpu_quota=100%; both modes share the same 100% parent aggregate ceiling\n")
            f.write("capacity_model=equal aggregate CPU ceiling; heavy slice is nested beneath parent\n")
            f.write("dynamic_quota=false; production user service/configuration/quotas untouched\n")
            f.write(f"router_sample_seconds={os.getenv('BENCH_ROUTER_SAMPLE_SEC', '2')}\n")
            f.write(f"router_sustained_samples={os.getenv('BENCH_ROUTER_SUSTAINED_SAMPLES', '4')}\n")
            f.write(f"router_cpu_threshold_percent={os.getenv('BENCH_ROUTER_CPU_THRESHOLD', '70')}\n")
            f.write("percentile_method=nearest-rank ceil(p*n)-1\n")
            f.write("worker_throughput=synthetic Python integer-mixing iterations per second over steady-state samples\n")
            f.write("router_overhead=isolated router process CPU percentage of one logical CPU and VmHWM peak RSS\n")
            f.write("candidate_acceptance=5 valid pairs; >=500 steady samples per mode; median paired wakeup p95/p99 and work p95 ratios <=1.10; throughput ratio >=0.90; router CPU <=5% of one logical CPU; peak RSS <=32768 KiB\n")
            f.write(f"command=REPS={REPS} DURATION={DURATION} WORKERS={WORKERS} RESULT={RESULT} python3 benchmarks/routing-contention.py\n")
        with ROUTER_LOG_PATH.open("x"):
            pass
        ctl("daemon-reload")
        ctl("start", PARENT_UNIT, HEAVY_UNIT, RECOVERY_UNIT)
        with tempfile.TemporaryDirectory(prefix="lwg-routing-bench-") as td:
            directory=pathlib.Path(td); hog,probe=write_worker_files(directory); results=[]
            ROUTER_STATE_PATH = directory / "routes.tsv"
            # Do not inherit ambient policy overrides from a developer shell;
            # the isolated run must be reproducible and independent of the
            # installed service's EnvironmentFile.
            ROUTER_ENV = {k: v for k, v in os.environ.items()
                          if not k.startswith("WORKLOAD_GUARD_")}
            ROUTER_ENV.update({
                "WORKLOAD_GUARD_PARENT_UNIT": PARENT_UNIT,
                "WORKLOAD_GUARD_HEAVY_UNIT": HEAVY_UNIT,
                "WORKLOAD_GUARD_RECOVERY_UNIT": RECOVERY_UNIT,
                "WORKLOAD_GUARD_STATE_FILE": str(ROUTER_STATE_PATH),
                "WORKLOAD_GUARD_DYNAMIC_QUOTA": "false",
                "WORKLOAD_GUARD_AIMD_SHADOW": "false",
                "WORKLOAD_GUARD_SHADOW": "false",
                "WORKLOAD_GUARD_SAMPLE_SEC": os.getenv("BENCH_ROUTER_SAMPLE_SEC", "2"),
                "WORKLOAD_GUARD_SUSTAINED_SAMPLES": os.getenv("BENCH_ROUTER_SUSTAINED_SAMPLES", "4"),
                "WORKLOAD_GUARD_CPU_THRESHOLD": os.getenv("BENCH_ROUTER_CPU_THRESHOLD", "70"),
                "WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD": "35",
                "WORKLOAD_GUARD_RECOVERY_SAMPLES": "10",
                "WORKLOAD_GUARD_RECOVERY_DWELL_SEC": "20",
            })
            with RESULT.open("a", newline="") as f:
                writer=csv.DictWriter(f,fieldnames=fields,delimiter="\t",lineterminator="\n");
                for rep in range(1,REPS+1):
                    modes=("baseline","router") if rep%2 else ("router","baseline")
                    for mode in modes:
                        print(f"trial {rep}/{REPS}: {mode} ({DURATION:.0f}s, {WORKERS} CPU workers)",flush=True)
                        row=run_trial(mode,rep,directory,hog,probe)
                        writer.writerow(row); f.flush(); results.append(row)
                        validity = "valid" if row["trial_valid"] else row["validity_reason"]
                        print("  samples={samples}, wakeup p95={all_p95_ms:.2f} ms, p99={all_p99_ms:.2f} ms, work p95={work_p95_ms:.2f} ms, routed={workers_in_heavy}/{worker_count}, route_at={routed_at_s}s, {validity}".format(**row, validity=validity),flush=True)
                        time.sleep(3)
            print(f"raw results: {RESULT}")
            print("\nSummary by mode (mean of per-trial percentiles; ms):")
            for mode in ("baseline","router"):
                group=[r for r in results if r["mode"]==mode]
                for key in ("all_p50_ms","all_p95_ms","all_p99_ms","work_p95_ms","steady_p95_ms","steady_p99_ms","steady_work_p95_ms","worker_iterations_per_sec","router_cpu_pct_one_core","router_peak_rss_kib"):
                    eligible=[r for r in group if r["trial_valid"]]
                    vals=[r[key] for r in eligible if isinstance(r[key],(int,float)) and r[key]==r[key]]
                    print(f"  {mode:8s} {key:16s} {statistics.mean(vals):.3f} (n={len(vals)})" if vals else f"  {mode:8s} {key:16s} n/a (no valid trials)")
            invalid=[r for r in results if not r["trial_valid"]]
            if REQUIRE_ROUTED and invalid:
                raise SystemExit(f"benchmark invalid: {len(invalid)} router trial(s) failed to route all workers; see {RESULT}")
        benchmark_status = "COMPLETE"
    finally:
        cleanup_ok = True
        try:
            stop_router()
        except Exception as exc:
            cleanup_ok = False
            print(f"warning: isolated router cleanup failed: {exc}", file=sys.stderr)
        for unit in (PARENT_UNIT, HEAVY_UNIT, RECOVERY_UNIT):
            try:
                ctl("stop", unit, check=False)
            except Exception as exc:
                cleanup_ok = False
                print(f"warning: could not stop temporary unit {unit}: {exc}", file=sys.stderr)
        for path in CREATED_UNIT_FILES:
            try: path.unlink()
            except FileNotFoundError: pass
            except OSError as exc:
                cleanup_ok = False
                print(f"warning: could not remove temporary unit file {path}: {exc}", file=sys.stderr)
        if CREATED_UNIT_FILES:
            try:
                ctl("daemon-reload", check=False)
            except Exception as exc:
                cleanup_ok = False
                print(f"warning: systemd daemon-reload failed during cleanup: {exc}", file=sys.stderr)
        if environment_path.exists():
            with environment_path.open("a") as f:
                f.write(f"result_status={'COMPLETE' if benchmark_status == 'COMPLETE' and cleanup_ok else 'INCOMPLETE'}\n")
                f.write(f"ended_at={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")

if __name__ == "__main__": main()
