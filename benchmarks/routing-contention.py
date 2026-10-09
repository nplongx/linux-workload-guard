#!/usr/bin/env python3
"""Compare interactive-task latency with the router off/on under CPU contention.

Requires a working systemd user manager, cgroup v2, and the installed workload-guard
slice/router units. This test temporarily stops/restarts the user router and changes
runtime slice quotas; it restores the quotas observed at startup on exit.
"""
import csv
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
RESULT = pathlib.Path(os.getenv("RESULT", "/tmp/lwg-routing-contention.tsv"))
CGROOT = pathlib.Path("/sys/fs/cgroup")

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
    return ys[min(len(ys)-1, int((len(ys)-1)*p))]
def write_worker_files(directory):
    hog = directory / "hog.py"
    hog.write_text('''import os,sys,time\npidfile=sys.argv[1]\nopen(pidfile,"w").write(str(os.getpid()))\nx=0x123456789abcdef0\nwhile True:\n x ^= (x << 7) & ((1<<64)-1); x ^= x >> 9; x=(x*0x9e3779b97f4a7c15)&((1<<64)-1)\n''')
    probe = directory / "probe.py"
    probe.write_text('''import os,sys,time\nout=sys.argv[1]; duration=float(sys.argv[2]); end=time.monotonic()+duration; rows=[]; period=.020; deadline=time.monotonic()+period\nwhile deadline<end:\n time.sleep(max(0,deadline-time.monotonic()))\n woke=time.monotonic(); wake_ms=max(0.0,(woke-deadline)*1000); t=time.monotonic_ns(); x=0\n for i in range(5000): x=(x*1664525+i+1013904223)&0xffffffff\n work_ms=(time.monotonic_ns()-t)/1e6\n rows.append((woke,wake_ms,work_ms)); deadline+=period\n if deadline < time.monotonic()-period: deadline=time.monotonic()+period\nwith open(out,"w") as f:\n for t,w,dt in rows: f.write(f"{t:.6f}\\t{w:.6f}\\t{dt:.6f}\\n")\n''')
    return hog, probe

def launch_scope(unit, command):
    return subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
                             "--slice=protected-workload.slice", "--", *command],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
def stop_scope(unit):
    ctl("stop", unit, check=False)

def run_trial(mode, rep, directory, hog, probe):
    tag=f"lwg-{mode}-{rep}-{os.getpid()}"
    router_was = ctl("is-active", "workload-router.service", check=False).returncode == 0
    if mode == "baseline":
        ctl("stop", "workload-router.service", check=False)
    else:
        ctl("start", "workload-router.service")
    # Restore fixed slice budgets before each comparison. Dynamic quota may adapt in router mode.
    ctl("set-property", "protected-workload.slice", "CPUQuota=100%")
    ctl("set-property", "heavy-workload.slice", "CPUQuota=400%")
    time.sleep(2)
    pids=[]; scopes=[]
    try:
        for i in range(WORKERS):
            pidfile=directory / f"{tag}-hog{i}.pid"
            p=launch_scope(f"{tag}-hog{i}.scope", [sys.executable, str(hog), str(pidfile)])
            scopes.append(f"{tag}-hog{i}.scope"); pids.append((pidfile,p))
        probe_file=directory / f"{tag}-probe.tsv"
        pp=launch_scope(f"{tag}-probe.scope", [sys.executable, str(probe), str(probe_file), str(DURATION)])
        scopes.append(f"{tag}-probe.scope")
        for _ in range(100):
            if all(f.exists() for f,p in pids): break
            time.sleep(.05)
        if not all(f.exists() for f,p in pids): raise RuntimeError("worker PID startup timeout")
        worker_pids=[int(f.read_text()) for f,p in pids]
        start=time.monotonic()
        routed_at=None
        peak_workers_in_heavy=0
        while pp.poll() is None and time.monotonic()-start < DURATION+20:
            in_heavy=sum(pid_in("heavy-workload.slice", pid) for pid in worker_pids)
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
        sample_path=RESULT.with_suffix(".samples.tsv")
        with sample_path.open("a") as raw:
            for t,wake,work in rows:
                raw.write(f"{mode}\t{rep}\t{t-trial_start:.6f}\t{wake:.6f}\t{work:.6f}\n")
        return {"mode":mode,"rep":rep,"samples":len(all_ms),"elapsed_s":round(now-start,3),
                "routed_at_s":"" if routed_at is None else round(routed_at,3),
                "workers_in_heavy":peak_workers_in_heavy,"worker_count":WORKERS,
                "all_mean_ms":statistics.mean(all_ms),"all_p50_ms":pct(all_ms,.50),
                "all_p95_ms":pct(all_ms,.95),"all_p99_ms":pct(all_ms,.99),"all_max_ms":max(all_ms),
                "work_p95_ms":pct(work_ms,.95),
                "steady_n":len(steady_ms),"steady_mean_ms":statistics.mean(steady_ms) if steady_ms else float('nan'),
                "steady_p50_ms":pct(steady_ms,.50),"steady_p95_ms":pct(steady_ms,.95),
                "steady_p99_ms":pct(steady_ms,.99),"steady_max_ms":max(steady_ms) if steady_ms else float('nan'),
                "steady_work_p95_ms":pct(steady_work_ms,.95),
                "psi_some_avg10":read_psi(),"router_was_active":int(router_was)}
    finally:
        for unit in scopes: stop_scope(unit)
        if router_was: ctl("start", "workload-router.service", check=False)

def read_psi():
    try:
        for line in pathlib.Path("/proc/pressure/cpu").read_text().splitlines():
            if line.startswith("some "):
                return next(float(x.split("=",1)[1]) for x in line.split() if x.startswith("avg10="))
    except Exception: pass
    return float("nan")

def main():
    if not pathlib.Path("/sys/fs/cgroup/cgroup.controllers").exists(): raise SystemExit("cgroup v2 required")
    if ctl("is-active", "protected-workload.slice", check=False).returncode != 0: ctl("start", "protected-workload.slice")
    if ctl("is-active", "heavy-workload.slice", check=False).returncode != 0: ctl("start", "heavy-workload.slice")
    was_active=ctl("is-active", "workload-router.service", check=False).returncode == 0
    orig_protected=show("protected-workload.slice","CPUQuotaPerSecUSec")
    orig_heavy=show("heavy-workload.slice","CPUQuotaPerSecUSec")
    orig_weights=(show("protected-workload.slice","CPUWeight"),show("heavy-workload.slice","CPUWeight"))
    RESULT.parent.mkdir(parents=True,exist_ok=True)
    fields=["mode","rep","samples","elapsed_s","routed_at_s","workers_in_heavy","worker_count","all_mean_ms","all_p50_ms","all_p95_ms","all_p99_ms","all_max_ms","work_p95_ms","steady_n","steady_mean_ms","steady_p50_ms","steady_p95_ms","steady_p99_ms","steady_max_ms","steady_work_p95_ms","psi_some_avg10","router_was_active"]
    try:
        with tempfile.TemporaryDirectory(prefix="lwg-routing-bench-") as td:
            directory=pathlib.Path(td); hog,probe=write_worker_files(directory); results=[]
            RESULT.with_suffix(".samples.tsv").write_text("mode\trep\trelative_s\twakeup_lateness_ms\twork_duration_ms\n")
            with RESULT.open("w",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=fields,delimiter="\t"); writer.writeheader(); f.flush()
                for rep in range(1,REPS+1):
                    modes=("baseline","router") if rep%2 else ("router","baseline")
                    for mode in modes:
                        print(f"trial {rep}/{REPS}: {mode} ({DURATION:.0f}s, {WORKERS} CPU workers)",flush=True)
                        row=run_trial(mode,rep,directory,hog,probe)
                        writer.writerow(row); f.flush(); results.append(row)
                        print("  samples={samples}, wakeup p95={all_p95_ms:.2f} ms, p99={all_p99_ms:.2f} ms, work p95={work_p95_ms:.2f} ms, routed={workers_in_heavy}/{worker_count}, route_at={routed_at_s}s".format(**row),flush=True)
                        time.sleep(3)
            print(f"raw results: {RESULT}")
            print("\nSummary by mode (mean of per-trial percentiles; ms):")
            for mode in ("baseline","router"):
                group=[r for r in results if r["mode"]==mode]
                for key in ("all_p50_ms","all_p95_ms","all_p99_ms","work_p95_ms","steady_p95_ms","steady_p99_ms","steady_work_p95_ms"):
                    vals=[r[key] for r in group if isinstance(r[key],(int,float)) and r[key]==r[key]]
                    print(f"  {mode:8s} {key:16s} {statistics.mean(vals):.3f}" if vals else f"  {mode:8s} {key:16s} n/a")
    finally:
        # Always restore quotas as they were observed, and restore the service's initial state.
        ctl("stop","workload-router.service",check=False)
        ctl("set-property","protected-workload.slice",f"CPUQuota={quota_string(orig_protected)}",check=False)
        ctl("set-property","heavy-workload.slice",f"CPUQuota={quota_string(orig_heavy)}",check=False)
        ctl("set-property","protected-workload.slice",f"CPUWeight={orig_weights[0]}",check=False)
        ctl("set-property","heavy-workload.slice",f"CPUWeight={orig_weights[1]}",check=False)
        if was_active: ctl("start","workload-router.service",check=False)
        else: ctl("stop","workload-router.service",check=False)

def quota_string(value):
    # systemd show CPUQuotaPerSecUSec is e.g. 1.5s or 400ms. Convert CPU time per second to percent.
    if value == "infinity": return "infinity"
    if value.endswith("s"): return f"{float(value[:-1])*100:g}%"
    if value.endswith("ms"): return f"{float(value[:-2])*0.1:g}%"
    return "400%"

if __name__ == "__main__": main()
