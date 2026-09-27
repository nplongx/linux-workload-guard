#!/usr/bin/env python3
import os, time, logging, subprocess

SYSTEMD_PREFIX = "/sys/fs/cgroup"
SAMPLE_SEC = float(os.getenv("WORKLOAD_GUARD_SAMPLE_SEC", "2"))
SUSTAINED_SAMPLES = int(os.getenv("WORKLOAD_GUARD_SUSTAINED_SAMPLES", "4"))
CPU_THRESHOLD = float(os.getenv("WORKLOAD_GUARD_CPU_THRESHOLD", "70"))
PARENT_UNIT = os.getenv("WORKLOAD_GUARD_PARENT_UNIT", "protected-workload.slice")
HEAVY_UNIT = os.getenv("WORKLOAD_GUARD_HEAVY_UNIT", "heavy-workload.slice")
STATE_FILE = os.getenv(
    "WORKLOAD_GUARD_STATE_FILE",
    os.path.join(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "linux-workload-guard", "routes.tsv"),
)

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

def proc_cgroup(pid):
    try:
        with open(f'/proc/{pid}/cgroup') as f:
            for line in f:
                hierarchy, _, path = line.rstrip().partition('::')
                if hierarchy == '0':
                    return path
    except Exception:
        pass
    return None

def in_cgroup_tree(pid, parent_cgroup):
    path = proc_cgroup(pid)
    if not path or not parent_cgroup:
        return False
    parent = parent_cgroup.rstrip('/')
    if parent.startswith(SYSTEMD_PREFIX + '/'):
        parent = parent[len(SYSTEMD_PREFIX):]
    return path == parent or path.startswith(parent + '/')

HEAVY = (
    'npm run build','npm run test','npm run lint','pnpm build','pnpm test','pnpm lint',
    'yarn build','yarn test','yarn lint','tsc ','vite build','webpack','esbuild','rollup',
    'parcel build','cargo build','cargo test','cargo check','rustc ','go build','go test',
    'make ','cmake --build','ninja ','pytest','mvn ','gradle ','gradlew ','docker build',
    'podman build','ffmpeg ','inference','torchrun','accelerate launch'
)
HEAVY_NAMES = {'tsc','rustc','cargo','go','make','ninja','pytest','ffmpeg','torchrun','gradle','mvn'}
DEFAULT_EXCLUDE = (
    'workload-router.py', 'workload-router'
)
EXCLUDE = DEFAULT_EXCLUDE + tuple(
    x.strip().lower() for x in os.getenv("WORKLOAD_GUARD_EXCLUDE_PATTERNS", "").split(",") if x.strip()
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

def write_state(routes):
    directory = os.path.dirname(STATE_FILE)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            for pid, data in sorted(routes.items()):
                cpu, reason, source, target, cmd = data
                fields = (str(pid), f"{cpu:.1f}", reason, source, target, cmd.replace("\t", " ").replace("\n", " "))
                f.write("\t".join(fields) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        logging.debug("write state failed: %s", e)

def main():
    prev={}
    hot={}
    moved=set()
    routes={}
    logging.info('started threshold=%.0f%% sustained=%ss', CPU_THRESHOLD, SAMPLE_SEC*SUSTAINED_SAMPLES)
    while True:
        gateway_cgroup = user_cgroup(PARENT_UNIT)
        heavy_cgroup = user_cgroup(HEAVY_UNIT)
        now={}
        if gateway_cgroup and heavy_cgroup:
            for name in os.listdir('/proc'):
                if not name.isdigit(): continue
                pid=int(name)
                if not in_cgroup_tree(pid, gateway_cgroup): continue
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
                    known_heavy = command_heavy(cmd)
                    sustained = hot.get(pid,0) >= SUSTAINED_SAMPLES
                    if known_heavy or sustained:
                        if pid not in moved and move(pid, heavy_cgroup):
                            moved.add(pid)
                            reason = "known-heavy" if known_heavy else "sustained-cpu"
                            routes[pid] = (cpu, reason, gateway_cgroup, heavy_cgroup, cmd)
                            logging.info('routed pid=%s cpu=%.1f%% reason=%s cmd=%s', pid,cpu,reason,cmd[:180])
        alive=set(now)
        for pid in list(hot):
            if pid not in alive: hot.pop(pid,None)
        for pid in list(moved):
            if not os.path.exists(f"/proc/{pid}") or not in_cgroup_tree(pid, heavy_cgroup):
                moved.discard(pid)
                routes.pop(pid, None)
        for pid in list(routes):
            if pid not in moved:
                routes.pop(pid, None)
        write_state(routes)
        prev=now
        time.sleep(SAMPLE_SEC)

if __name__=='__main__': main()
