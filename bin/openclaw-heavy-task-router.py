#!/usr/bin/env python3
import os, time, logging, subprocess

SYSTEMD_PREFIX = "/sys/fs/cgroup"
SAMPLE_SEC = 2
SUSTAINED_SAMPLES = 4          # 8 seconds
CPU_THRESHOLD = 70.0           # % of one logical CPU
MAX_ANCESTRY = 32

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def user_cgroup(unit):
    try:
        value = subprocess.check_output(
            ["systemctl", "--user", "show", unit, "--property=ControlGroup", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not value or value == "-":
        return None
    return SYSTEMD_PREFIX + value


def read_pids(path):
    try:
        with open(path) as f: return {int(x) for x in f.read().split()}
    except Exception: return set()

def proc_stat(pid):
    try:
        with open(f'/proc/{pid}/stat') as f: s=f.read()
        end=s.rfind(')')
        fields=s[end+2:].split()
        return int(fields[1]), int(fields[11]), int(fields[12])
    except Exception: return None, 0, 0

def cmdline(pid):
    try:
        with open(f'/proc/{pid}/cmdline','rb') as f:
            return f.read().replace(b'\0',b' ').decode(errors='ignore').strip()
    except Exception: return ''

def ancestry(pid, roots):
    seen=set(); cur=pid
    for _ in range(MAX_ANCESTRY):
        if cur in roots: return True
        if cur in seen or cur <= 1: return False
        seen.add(cur)
        p,_,_=proc_stat(cur)
        if not p: return False
        cur=p
    return False

HEAVY = (
    'npm run build','npm run test','npm run lint','pnpm build','pnpm test','pnpm lint',
    'yarn build','yarn test','yarn lint','tsc ','vite build','webpack','esbuild','rollup',
    'parcel build','cargo build','cargo test','cargo check','rustc ','go build','go test',
    'make ','cmake --build','ninja ','pytest','mvn ','gradle ','gradlew ','docker build',
    'podman build','ffmpeg ','inference','torchrun','accelerate launch'
)
HEAVY_NAMES = {'tsc','rustc','cargo','go','make','ninja','pytest','ffmpeg','torchrun','gradle','mvn'}
EXCLUDE = (
    'openclaw-heavy-task-router.py', 'openclaw-gateway',
    'google-chrome-chatgpt', 'chrome_crashpad_handler', 'chromedriver'
)

def is_excluded(cmd):
    low=cmd.lower()
    return any(x in low for x in EXCLUDE)

def command_heavy(cmd):
    low=cmd.lower()
    base=os.path.basename(low.split()[0]) if low else ''
    return any(x in low for x in HEAVY) or base in HEAVY_NAMES

def move(pid, target_cgroup):
    try:
        with open(os.path.join(target_cgroup, "cgroup.procs"), "a") as f: f.write(str(pid)+"\n")
        return True
    except Exception as e:
        logging.debug('move pid=%s failed: %s', pid, e)
        return False

def main():
    prev={}
    hot={}
    moved=set()
    logging.info('started threshold=%.0f%% sustained=%ss', CPU_THRESHOLD, SAMPLE_SEC*SUSTAINED_SAMPLES)
    while True:
        gateway_cgroup = user_cgroup("openclaw-gateway.service")
        heavy_cgroup = user_cgroup("terminal-heavy.slice")
        roots=read_pids(os.path.join(gateway_cgroup, "cgroup.procs")) if gateway_cgroup else set()
        now={}
        if roots and heavy_cgroup:
            for name in os.listdir('/proc'):
                if not name.isdigit(): continue
                pid=int(name)
                if pid in roots or not ancestry(pid, roots): continue
                cmd=cmdline(pid)
                if not cmd or is_excluded(cmd): continue
                _,ut,st=proc_stat(pid)
                now[pid]=(ut+st,cmd)
                if pid in prev:
                    dticks=ut+st-prev[pid][0]
                    # Linux USER_HZ is normally 100. Normalize to % of one CPU.
                    cpu=(dticks / max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK'])) / SAMPLE_SEC)*100
                    if cpu >= CPU_THRESHOLD:
                        hot[pid]=hot.get(pid,0)+1
                    else:
                        hot[pid]=0
                    if command_heavy(cmd) or hot.get(pid,0) >= SUSTAINED_SAMPLES:
                        if pid not in moved and move(pid, heavy_cgroup):
                            moved.add(pid)
                            logging.info('routed pid=%s cpu=%.1f%% hot=%s cmd=%s', pid,cpu,hot.get(pid,0),cmd[:180])
        alive=set(now)
        for pid in list(hot):
            if pid not in alive: hot.pop(pid,None)
        for pid in list(moved):
            if pid not in alive: moved.discard(pid)
        prev=now
        time.sleep(SAMPLE_SEC)

if __name__=='__main__': main()
