#!/usr/bin/env python3
"""Compare interactive-task latency with a dedicated router off/on under CPU load.

Only unique temporary user slices and a directly spawned test router are used.
The installed workload-router.service, production configuration, and production
slice quotas are never stopped, rewritten, or changed.
"""
import csv
import hashlib
import math
import os
import pathlib
import statistics
import subprocess
import shutil
import sys
import tempfile
import time

REPS = int(os.getenv("REPS", "3"))
DURATION = float(os.getenv("DURATION", "30"))
WORKERS = int(os.getenv("WORKERS", "1"))
WORKLOAD_KIND = os.getenv("WORKLOAD_KIND", "synthetic").strip().lower()
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
SCOPE_CGROUP_CACHE = {}

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

def scope_weight(unit):
    try:
        path = SCOPE_CGROUP_CACHE.get(unit)
        if path is None:
            path = cgroup_path(PARENT_UNIT) / unit
            SCOPE_CGROUP_CACHE[unit] = path
        return int((path / "cpu.weight").read_text().strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

def pct(xs, p):
    if not xs: return float("nan")
    ys = sorted(xs)
    return ys[min(len(ys)-1, max(0, math.ceil(len(ys)*p)-1))]
def write_worker_files(directory):
    hog = directory / "hog.py"
    if WORKLOAD_KIND == "terminal-openssl":
        hog.write_text('''import os, re, subprocess, sys, time\npidfile, progress, output, duration = sys.argv[1:5]\nseconds=max(10, int(float(duration)-3))\nwith open(output, "w") as log:\n p=subprocess.Popen(["openssl", "speed", "-seconds", str(seconds), "-bytes", "16384", "sha256"], stdout=log, stderr=subprocess.STDOUT)\n open(pidfile,"w").write(str(p.pid))\n rc=p.wait()\nrate=float("nan")\nfor line in open(output, errors="replace"):\n if line.lstrip().startswith("sha256"):\n  values=re.findall(r"([0-9]+(?:\\.[0-9]+)?)(?:k)?", line)\n  if values:\n   try: rate=float(values[-1])\n   except ValueError: pass\nwith open(progress,"a",buffering=1) as f: f.write(f"{time.monotonic():.6f}\\t{int(rate) if rate==rate and rate>0 else 0}\\n")\nraise SystemExit(rc)\n''')
    elif WORKLOAD_KIND == "terminal-build":
        srcdir = directory / "build-src"
        srcdir.mkdir(exist_ok=True)
        for index in range(12):
            body = "\n".join(f"long f{index}_{n}(long x) {{ return (x * {n + 3}L + {n * 17 + 1}L) ^ (x >> {(n % 7) + 1}); }}" for n in range(240))
            (srcdir / f"unit{index}.c").write_text(body + "\n")
        (srcdir / "main.c").write_text("int main(void) { return 0; }\n")
        hog.write_text('''import os,pathlib,subprocess,sys,time\npidfile,progress=sys.argv[1:3]\nopen(pidfile,"w").write(str(os.getpid()))\nsrc=pathlib.Path(__file__).parent/"build-src"; out=src/"build-out"; out.mkdir(exist_ok=True)\nsources=sorted(str(p) for p in src.glob("unit*.c"))+[str(src/"main.c")]\ncompiler=os.environ.get("CC","cc"); count=0; deadline=time.monotonic()+1\nwith open(progress,"a",buffering=1) as f:\n while True:\n  objects=[]\n  for source in sources:\n   obj=out/(pathlib.Path(source).stem+".o")\n   subprocess.run([compiler,"-O2","-c",source,"-o",str(obj)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)\n   objects.append(str(obj))\n  subprocess.run([compiler,*objects,"-o",str(out/"build-worker")],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)\n  count+=1; now=time.monotonic()\n  if now>=deadline: f.write(f"{now:.6f}\\t{count}\\n"); deadline=now+1\n''')
    else:
        hog.write_text('''import os,sys,time\npidfile,progress=sys.argv[1:3]\nopen(pidfile,"w").write(str(os.getpid()))\nx=0x123456789abcdef0; count=0; deadline=time.monotonic()+1.0\nwith open(progress,"a",buffering=1) as f:\n while True:\n  x ^= (x << 7) & ((1<<64)-1); x ^= x >> 9; x=(x*0x9e3779b97f4a7c15)&((1<<64)-1); count += 1\n  now=time.monotonic()\n  if now >= deadline:\n   f.write(f"{now:.6f}\\t{count}\\n"); deadline += 1.0\n''')
    probe = directory / "probe.py"
    probe.write_text('''import os,sys,time\nout=sys.argv[1]; duration=float(sys.argv[2]); end=time.monotonic()+duration; rows=[]; period=.020; deadline=time.monotonic()+period\nwhile deadline<end:\n time.sleep(max(0,deadline-time.monotonic()))\n woke=time.monotonic(); wake_ms=max(0.0,(woke-deadline)*1000); t=time.monotonic_ns(); x=0\n for i in range(5000): x=(x*1664525+i+1013904223)&0xffffffff\n work_ms=(time.monotonic_ns()-t)/1e6\n rows.append((woke,wake_ms,work_ms)); deadline+=period\n if deadline < time.monotonic()-period: deadline=time.monotonic()+period\nwith open(out,"w") as f:\n for t,w,dt in rows: f.write(f"{t:.6f}\\t{w:.6f}\\t{dt:.6f}\\n")\n''')
    return hog, probe

def launch_scope(unit, command):
    args = ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}", f"--slice={PARENT_UNIT}"]
    if unit.startswith("lwg-workload-"):
        args.append("--description=Linux Workload Guard aggregate candidate")
    return subprocess.Popen([*args, "--", *command],
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
    # either moves the one-worker target PID into the 100% child slice or, for
    # multi-worker aggregate mode, lowers the verified workload scope's CPUWeight.
    # The parent's 100% ceiling caps aggregate CPU in both cases.
    if mode == "router":
        start_router()
    time.sleep(2)
    pids=[]; scopes=[]; progress_files=[]
    try:
        for i in range(WORKERS):
            pidfile=directory / f"{tag}-hog{i}.pid"
            progressfile=directory / f"{tag}-hog{i}.progress.tsv"
            if WORKLOAD_KIND == "terminal-openssl":
                outputfile=directory / f"{tag}-hog{i}.openssl.log"
                worker_command=[sys.executable, str(hog), str(pidfile), str(progressfile), str(outputfile), str(DURATION)]
            else:
                worker_command=[sys.executable, str(hog), str(pidfile), str(progressfile)]
            worker_scope=f"lwg-workload-{tag}-hog{i}.scope"
            p=launch_scope(worker_scope, worker_command)
            scopes.append(worker_scope); pids.append((pidfile,p)); progress_files.append(progressfile)
        probe_file=directory / f"{tag}-probe.tsv"
        pp=launch_scope(f"{tag}-probe.scope", [sys.executable, str(probe), str(probe_file), str(DURATION)])
        scopes.append(f"{tag}-probe.scope")
        for _ in range(100):
            if all(f.exists() for f,p in pids): break
            time.sleep(.05)
        if not all(f.exists() for f,p in pids): raise RuntimeError("worker PID startup timeout")
        worker_pids=[int(f.read_text()) for f,p in pids]
        router_ticks_start, _ = router_process_metrics()
        trial_psi_start = read_psi()
        trial_load_start = read_loadavg()
        start=time.monotonic()
        routed_at=None
        peak_workers_in_heavy=0
        peak_workers_routed=0
        aggregate_scope_mode = WORKERS > 1 and os.getenv("BENCH_AGGREGATE_ROUTING", "true").lower() in ("1", "true", "yes", "on")
        while pp.poll() is None and time.monotonic()-start < DURATION+20:
            in_heavy=sum(pid_in(HEAVY_UNIT, pid) for pid in worker_pids)
            peak_workers_in_heavy=max(peak_workers_in_heavy,in_heavy)
            routed = (sum(scope_weight(f"lwg-workload-{tag}-hog{i}.scope") == int(os.getenv("BENCH_AGGREGATE_SCOPE_WEIGHT", "20")) for i in range(WORKERS))
                      if aggregate_scope_mode else in_heavy)
            peak_workers_routed=max(peak_workers_routed,routed)
            if mode == "router" and routed_at is None and routed == WORKERS:
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
        # Compare identical elapsed windows. The previous routed window started
        # at routed_at+2s (~10.5s) while baseline started at 12s, so paired
        # percentiles used different sample counts and were not fully comparable.
        trial_start=rows[0][0]
        all_ms=[wake_ms for _,wake_ms,_ in rows]
        work_ms=[work for _,_,work in rows]
        steady_rows=[r for r in rows if r[0]-trial_start >= 12.0]
        steady_ms=[wake for _,wake,_ in steady_rows]
        steady_work_ms=[work for _,_,work in steady_rows]
        steady_start = trial_start + 12.0
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
            if WORKLOAD_KIND == "terminal-openssl" and samples:
                worker_rates.append(float(samples[-1][1]))
            elif len(samples) >= 2 and samples[-1][0] > samples[0][0]:
                worker_rates.append((samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]))
        worker_throughput_per_sec = sum(worker_rates) if len(worker_rates) == WORKERS else float("nan")
        worker_iterations_per_sec = worker_throughput_per_sec if WORKLOAD_KIND == "synthetic" else float("nan")
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
        route_complete = mode != "router" or (
            peak_workers_routed == WORKERS and routed_at is not None and routed_at + 2.0 <= 12.0
        )
        if route_complete:
            validity_reason = ""
        elif peak_workers_routed != WORKERS:
            validity_reason = f"routing-incomplete:{peak_workers_routed}/{WORKERS}"
        else:
            validity_reason = "routing-converged-too-late-for-common-steady-window"
        return {"mode":mode,"rep":rep,"samples":len(all_ms),"elapsed_s":round(now-start,3),
                "routed_at_s":"" if routed_at is None else round(routed_at,3),
                "workers_in_heavy":peak_workers_in_heavy,"workers_routed":peak_workers_routed,"worker_count":WORKERS,
                "trial_valid":int(route_complete),"validity_reason":validity_reason,
                "all_mean_ms":statistics.mean(all_ms),"all_p50_ms":pct(all_ms,.50),
                "all_p95_ms":pct(all_ms,.95),"all_p99_ms":pct(all_ms,.99),"all_max_ms":max(all_ms),
                "work_p95_ms":pct(work_ms,.95),
                "steady_n":len(steady_ms),"steady_mean_ms":statistics.mean(steady_ms) if steady_ms else float('nan'),
                "steady_p50_ms":pct(steady_ms,.50),"steady_p95_ms":pct(steady_ms,.95),
                "steady_p99_ms":pct(steady_ms,.99),"steady_max_ms":max(steady_ms) if steady_ms else float('nan'),
                "steady_work_p95_ms":pct(steady_work_ms,.95),
                "worker_iterations_per_sec":worker_iterations_per_sec,
                "worker_throughput_per_sec":worker_throughput_per_sec,
                "router_cpu_pct_one_core":router_cpu_pct,
                "router_peak_rss_kib":router_peak_rss_kib if mode == "router" else None,
                "psi_some_avg10_start":trial_psi_start,"psi_some_avg10":read_psi(),
                "loadavg_1m_start":trial_load_start,"loadavg_1m_end":read_loadavg(),
                "router_enabled_during_trial":int(mode == "router")}
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

def read_loadavg():
    try:
        return float(pathlib.Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return float("nan")

def cpu_model():
    try:
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    return "unavailable"

def git_revision():
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"

def git_source_state():
    """Record tracked harness/controller source changes, not unrelated result artifacts."""
    try:
        status = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                                         text=True, stderr=subprocess.DEVNULL, timeout=2)
        source_paths = ["bin/workload-router.py", "benchmarks/routing-contention.py",
                        "benchmarks/performance-review.py"]
        diff = subprocess.check_output(["git", "-C", str(ROOT), "diff", "HEAD", "--binary", "--", *source_paths],
                                       stderr=subprocess.DEVNULL, timeout=2)
        return ("true" if status.strip() else "false", hashlib.sha256(diff).hexdigest(),
                sum(1 for line in status.splitlines() if line.startswith("??")))
    except (OSError, subprocess.SubprocessError):
        return "unavailable", "unavailable", -1

def main():
    global ROUTER_LOG_PATH, ROUTER_STATE_PATH, ROUTER_ENV
    global CREATED_UNIT_FILES
    if REPS < 1 or WORKERS < 1 or DURATION < 20:
        raise SystemExit("REPS and WORKERS must be positive; DURATION must be >=20s for comparable steady windows")
    if WORKLOAD_KIND not in ("synthetic", "terminal-openssl", "terminal-build"):
        raise SystemExit("WORKLOAD_KIND must be synthetic, terminal-openssl, or terminal-build")
    if WORKLOAD_KIND == "terminal-openssl" and not shutil.which("openssl"):
        raise SystemExit("WORKLOAD_KIND=terminal-openssl requires the openssl CLI")
    if WORKLOAD_KIND == "terminal-build" and not shutil.which(os.getenv("CC", "cc")):
        raise SystemExit("WORKLOAD_KIND=terminal-build requires a C compiler (CC or cc)")
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
            fields=["mode","rep","samples","elapsed_s","routed_at_s","workers_in_heavy","workers_routed","worker_count","trial_valid","validity_reason","all_mean_ms","all_p50_ms","all_p95_ms","all_p99_ms","all_max_ms","work_p95_ms","steady_n","steady_mean_ms","steady_p50_ms","steady_p95_ms","steady_p99_ms","steady_max_ms","steady_work_p95_ms","worker_iterations_per_sec","worker_throughput_per_sec","router_cpu_pct_one_core","router_peak_rss_kib","psi_some_avg10_start","psi_some_avg10","loadavg_1m_start","loadavg_1m_end","router_enabled_during_trial"]
            writer=csv.DictWriter(f,fieldnames=fields,delimiter="\t",lineterminator="\n"); writer.writeheader(); f.flush()
        with sample_path.open("x") as f:
            f.write("mode\trep\trelative_s\twakeup_lateness_ms\twork_duration_ms\n")
        with environment_path.open("x") as f:
            f.write(f"started_at={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
            f.write(f"host={os.uname()}\npython={sys.version.replace(chr(10), ' ')}\nlogical_cpus={os.cpu_count()}\n")
            dirty, diff_hash, untracked_count = git_source_state()
            f.write(f"git_revision={git_revision()}\ngit_dirty={dirty}\ngit_diff_sha256={diff_hash}\ngit_untracked_path_count={untracked_count}\ncpu_model={cpu_model()}\n")
            f.write(f"cpu_pressure_initial_some_avg10={read_psi()}\nloadavg_initial_1m={read_loadavg()}\n")
            try:
                f.write(f"cgroup_controllers={pathlib.Path('/sys/fs/cgroup/cgroup.controllers').read_text().strip()}\n")
            except OSError:
                f.write("cgroup_controllers=unavailable\n")
            f.write(f"systemd={ctl('--version').stdout.splitlines()[0]}\ncgroup_mode=v2\n")
            f.write(f"repetitions={REPS}\nworkers={WORKERS}\nduration_seconds={DURATION}\n")
            f.write(f"workload_kind={WORKLOAD_KIND}\n")
            f.write(f"parent_unit={PARENT_UNIT}\nheavy_unit={HEAVY_UNIT}\nrecovery_unit={RECOVERY_UNIT}\n")
            f.write("parent_cpu_quota=100%; heavy_child_cpu_quota=100%; both modes share the same 100% parent aggregate ceiling\n")
            aggregate_setting = os.getenv("BENCH_AGGREGATE_ROUTING", "true" if WORKERS > 1 else "false").lower()
            f.write(f"aggregate_routing={'enabled' if aggregate_setting in ('1','true','yes','on') else 'disabled'}; policy observes opt-in scope cpu.stat usage_usec; requires >=2 scopes at >=20% CPU each and >=85% aggregate quota for 3 samples; clears <=65% for 3 samples; one scope CPUWeight={os.getenv('BENCH_AGGREGATE_SCOPE_WEIGHT','20')} per sample\n")
            f.write("capacity_model=equal aggregate CPU ceiling; heavy slice is nested beneath parent\n")
            f.write("dynamic_quota=false; production user service/configuration/quotas untouched\n")
            f.write(f"router_sample_seconds={os.getenv('BENCH_ROUTER_SAMPLE_SEC', '2')}\n")
            f.write(f"router_sustained_samples={os.getenv('BENCH_ROUTER_SUSTAINED_SAMPLES', '4')}\n")
            f.write(f"router_cpu_threshold_percent={os.getenv('BENCH_ROUTER_CPU_THRESHOLD', '70')}\n")
            f.write("percentile_method=nearest-rank ceil(p*n)-1\n")
            f.write("worker_throughput=worker_throughput_per_sec; synthetic mode is Python integer-mixing iterations/s; terminal-openssl mode is OpenSSL SHA-256 throughput (kB/s at largest reported block); terminal-build mode is completed generated C compile-and-link cycles/s\n")
            f.write("steady_state_window=common elapsed time >=12s after probe start in both modes; routing must have converged before window for a valid router trial\n")
            f.write("noise_diagnostics=host CPU PSI some avg10 and /proc/loadavg 1m sampled at each trial start/end; diagnostic only, no post-hoc exclusion\n")
            f.write("router_overhead=isolated router process CPU percentage of one logical CPU and VmHWM peak RSS\n")
            f.write("candidate_acceptance=5 valid pairs; >=500 steady samples per mode; identical >=12s elapsed steady window; route convergence at least 2s before window; median paired wakeup p95/p99 and wall-work p95 ratios <=1.10; throughput ratio >=0.90; router CPU <=5% of one logical CPU; peak RSS <=32768 KiB\n")
            f.write("workload_seed=0x123456789abcdef0; probe_period_seconds=0.020; probe_integer_operations=5000\n")
            governor = pathlib.Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
            f.write(f"cpu_governor={governor.read_text().strip() if governor.is_file() else 'unavailable'}\n")
            f.write(f"command=REPS={REPS} DURATION={DURATION} WORKERS={WORKERS} RESULT={RESULT} python3 benchmarks/routing-contention.py\n")
        with ROUTER_LOG_PATH.open("x"):
            pass
        ctl("daemon-reload")
        ctl("start", PARENT_UNIT, HEAVY_UNIT, RECOVERY_UNIT)
        with environment_path.open("a") as manifest:
            for unit in (PARENT_UNIT, HEAVY_UNIT, RECOVERY_UNIT):
                cg = cgroup_path(unit)
                manifest.write(f"{unit}_control_group={show(unit, 'ControlGroup')}\n")
                for name in ("cpu.max", "cpu.weight", "cpuset.cpus.effective"):
                    try:
                        value = pathlib.Path(cg, name).read_text().strip()
                    except OSError:
                        value = "unavailable"
                    manifest.write(f"{unit}_{name.replace('.', '_')}={value}\n")
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
                "WORKLOAD_GUARD_AGGREGATE_ROUTING": os.getenv("BENCH_AGGREGATE_ROUTING", "true" if WORKERS > 1 else "false"),
                "WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT": os.getenv("BENCH_AGGREGATE_SCOPE_WEIGHT", "20"),
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
                        print(f"trial {rep}/{REPS}: {mode} ({DURATION:.0f}s, {WORKERS} workers, workload={WORKLOAD_KIND})",flush=True)
                        row=run_trial(mode,rep,directory,hog,probe)
                        writer.writerow(row); f.flush(); results.append(row)
                        validity = "valid" if row["trial_valid"] else row["validity_reason"]
                        print("  samples={samples}, wakeup p95={all_p95_ms:.2f} ms, p99={all_p99_ms:.2f} ms, work p95={work_p95_ms:.2f} ms, routed={workers_routed}/{worker_count}, route_at={routed_at_s}s, {validity}".format(**row, validity=validity),flush=True)
                        time.sleep(3)
            print(f"raw results: {RESULT}")
            print("\nSummary by mode (mean of per-trial percentiles; ms):")
            for mode in ("baseline","router"):
                group=[r for r in results if r["mode"]==mode]
                for key in ("all_p50_ms","all_p95_ms","all_p99_ms","work_p95_ms","steady_p95_ms","steady_p99_ms","steady_work_p95_ms","worker_iterations_per_sec","worker_throughput_per_sec","router_cpu_pct_one_core","router_peak_rss_kib"):
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
