#!/usr/bin/env python3
import json, math, os, time, logging, subprocess

SYSTEMD_PREFIX = "/sys/fs/cgroup"


def shadow_float_env(name, default):
    """Keep malformed shadow-only settings representable so validation can fail closed."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float("nan")


def shadow_int_env(name, default):
    """Return an invalid sentinel rather than silently clamping a shadow sample count."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return 0


SAMPLE_SEC = float(os.getenv("WORKLOAD_GUARD_SAMPLE_SEC", "2"))
SUSTAINED_SAMPLES = int(os.getenv("WORKLOAD_GUARD_SUSTAINED_SAMPLES", "4"))
CPU_THRESHOLD = float(os.getenv("WORKLOAD_GUARD_CPU_THRESHOLD", "70"))
SHADOW_ENABLED = os.getenv("WORKLOAD_GUARD_SHADOW", "false").lower() in ("1", "true", "yes", "on")
SHADOW_EWMA_ALPHA = shadow_float_env("WORKLOAD_GUARD_SHADOW_EWMA_ALPHA", 0.35)
SHADOW_ROUTE_THRESHOLD = shadow_float_env("WORKLOAD_GUARD_SHADOW_ROUTE_THRESHOLD", CPU_THRESHOLD)
SHADOW_SUSTAINED_SAMPLES = shadow_int_env("WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES", SUSTAINED_SAMPLES)
ADAPTIVE = os.getenv("WORKLOAD_GUARD_ADAPTIVE", "false").lower() in ("1", "true", "yes", "on")
LEARNING_RATE = float(os.getenv("WORKLOAD_GUARD_LEARNING_RATE", "0.15"))
ROUTE_THRESHOLD = float(os.getenv("WORKLOAD_GUARD_ROUTE_THRESHOLD", "0.75"))
COOLDOWN_SEC = float(os.getenv("WORKLOAD_GUARD_COOLDOWN_SEC", "20"))
MIN_SAMPLES = int(os.getenv("WORKLOAD_GUARD_MIN_SAMPLES", "8"))
RECOVERY_CPU_THRESHOLD = float(os.getenv("WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD", "35"))
RECOVERY_SAMPLES = int(os.getenv("WORKLOAD_GUARD_RECOVERY_SAMPLES", "10"))
RECOVERY_DWELL_SEC = float(os.getenv("WORKLOAD_GUARD_RECOVERY_DWELL_SEC", "20"))
SHADOW_CLEAR_THRESHOLD = shadow_float_env("WORKLOAD_GUARD_SHADOW_CLEAR_THRESHOLD", RECOVERY_CPU_THRESHOLD)
SHADOW_RECOVERY_SAMPLES = shadow_int_env("WORKLOAD_GUARD_SHADOW_RECOVERY_SAMPLES", RECOVERY_SAMPLES)
SHADOW_RECOVERY_DWELL_SEC = shadow_float_env("WORKLOAD_GUARD_SHADOW_RECOVERY_DWELL_SEC", RECOVERY_DWELL_SEC)
QUOTA_DYNAMIC = os.getenv("WORKLOAD_GUARD_DYNAMIC_QUOTA", "false").lower() in ("1", "true", "yes", "on")
QUOTA_AIMD_SHADOW = os.getenv("WORKLOAD_GUARD_AIMD_SHADOW", "false").lower() in ("1", "true", "yes", "on")
QUOTA_AIMD_DECREASE = float(os.getenv("WORKLOAD_GUARD_AIMD_DECREASE", "0.80"))
QUOTA_AIMD_ADD = float(os.getenv("WORKLOAD_GUARD_AIMD_ADD", "10"))
QUOTA_INTERVAL_SEC = float(os.getenv("WORKLOAD_GUARD_QUOTA_INTERVAL_SEC", "5"))
QUOTA_MIN = float(os.getenv("WORKLOAD_GUARD_QUOTA_MIN", "100"))
QUOTA_MAX = float(os.getenv("WORKLOAD_GUARD_QUOTA_MAX", "400"))
QUOTA_STEP = float(os.getenv("WORKLOAD_GUARD_QUOTA_STEP", "50"))
QUOTA_PRESSURE_HIGH = float(os.getenv("WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH", "0.40"))
QUOTA_PRESSURE_LOW = float(os.getenv("WORKLOAD_GUARD_QUOTA_PRESSURE_LOW", "0.10"))
QUOTA_THROTTLE_HIGH = float(os.getenv("WORKLOAD_GUARD_QUOTA_THROTTLE_HIGH", "0.10"))
QUOTA_MIN_DWELL_SEC = float(os.getenv("WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC", "10"))
QUOTA_SCHED_DELAY_HIGH_MS = float(os.getenv("WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS", "20"))
QUOTA_SCHED_DELAY_LOW_MS = float(os.getenv("WORKLOAD_GUARD_QUOTA_SCHED_DELAY_LOW_MS", "5"))
STATS_SAVE_INTERVAL_SEC = float(os.getenv("WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC", "10"))
HISTORY_INTERVAL_SEC = float(os.getenv("WORKLOAD_GUARD_HISTORY_INTERVAL_SEC", "300"))
HISTORY_FILE = os.getenv("WORKLOAD_GUARD_HISTORY_FILE", os.path.join(os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "linux-workload-guard", "history.jsonl"))
STATS_FILE = os.getenv("WORKLOAD_GUARD_STATS_FILE", os.path.join(
    os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "linux-workload-guard", "stats.json"))
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

def ensure_workload_units():
    """Start the configured slices before resolving their cgroup paths."""
    for unit in (PARENT_UNIT, HEAVY_UNIT):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", unit],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=5,
            )
            if result.returncode:
                logging.warning("could not start %s: %s", unit, result.stderr.strip() or "systemctl failed")
        except (OSError, subprocess.SubprocessError) as e:
            logging.warning("could not start %s: %s", unit, e)


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

def cgroup_tree_pids(parent_cgroup):
    """Return PIDs under a cgroup tree without scanning unrelated /proc entries."""
    if not parent_cgroup:
        return ()
    root = parent_cgroup
    if root.startswith(SYSTEMD_PREFIX + '/'):
        root = root.rstrip('/')
    if not os.path.isdir(root):
        return ()
    pids = set()
    try:
        for directory, dirs, files in os.walk(root):
            if 'cgroup.procs' not in files:
                continue
            try:
                with open(os.path.join(directory, 'cgroup.procs')) as f:
                    for line in f:
                        if line.strip().isdigit():
                            pids.add(int(line))
            except OSError:
                continue
    except OSError:
        return ()
    return pids

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

def command_class(cmd):
    parts = cmd.lower().split()
    if not parts: return "unknown"
    base = os.path.basename(parts[0])
    sub = parts[1] if len(parts) > 1 and not parts[1].startswith("-") else ""
    return f"{base}:{sub}" if sub else base

def load_stats():
    try:
        with open(STATS_FILE) as f: data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError): return {}

def save_stats(stats):
    directory = os.path.dirname(STATS_FILE)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        tmp = STATS_FILE + ".tmp"
        with open(tmp, "w") as f: json.dump(stats, f, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATS_FILE)
    except OSError as e: logging.debug("write stats failed: %s", e)

def append_history(snapshot):
    directory = os.path.dirname(HISTORY_FILE)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(snapshot, sort_keys=True) + "\n")
        os.chmod(HISTORY_FILE, 0o600)
    except OSError as e: logging.debug("write history failed: %s", e)

def proc_cpu_sample(pid):
    try:
        with open(f'/proc/{pid}/stat') as f: s=f.read()
        end=s.rfind(')')
        fields=s[end+2:].split()
        return int(fields[19]), int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError):
        return None

def history_cpu_attribution(prev, elapsed, protected_cgroup, heavy_cgroup):
    hz = max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK']))
    current = {}
    guarded_cpu = 0.0
    unmanaged_cpu = 0.0
    candidates = []
    try:
        pids = (int(name) for name in os.listdir('/proc') if name.isdigit())
    except OSError:
        return current, guarded_cpu, unmanaged_cpu, []
    for pid in pids:
        sample = proc_cpu_sample(pid)
        if not sample:
            continue
        starttime, ticks = sample
        current[pid] = (starttime, ticks)
        old = prev.get(pid)
        if not old or old[0] != starttime:
            continue
        cpu = max(0.0, (ticks - old[1]) / hz / max(0.001, elapsed) * 100.0)
        if cpu <= 0.0:
            continue
        cgroup = proc_cgroup(pid)
        protected = in_cgroup_tree(pid, protected_cgroup)
        heavy = in_cgroup_tree(pid, heavy_cgroup)
        if protected or heavy:
            guarded_cpu += cpu
        else:
            unmanaged_cpu += cpu
        candidates.append((cpu, pid, cgroup, protected, heavy))
    top = []
    for cpu, pid, cgroup, protected, heavy in sorted(candidates, reverse=True)[:5]:
        cmd = cmdline(pid) or '[unknown]'
        top.append({
            'pid': pid,
            'command': cmd[:240],
            'cpu_pct': round(cpu, 1),
            'class': 'protected' if protected else ('heavy' if heavy else 'unmanaged'),
            'cgroup': cgroup or '',
        })
    return current, guarded_cpu, unmanaged_cpu, top

def update_stat(stats, cls, cpu):
    item = stats.setdefault(cls, {"samples": 0, "mean": 0.0, "variance": 0.0})
    old_mean = float(item.get("mean", 0.0))
    old_var = max(0.0, float(item.get("variance", 0.0)))
    rate = min(1.0, max(0.01, LEARNING_RATE))
    delta = cpu - old_mean
    item["samples"] = int(item.get("samples", 0)) + 1
    item["mean"] = old_mean + rate * delta
    item["variance"] = (1.0 - rate) * (old_var + rate * delta * delta)
    return item

def adaptive_score(cpu, stat):
    if not stat or int(stat.get("samples", 0)) < MIN_SAMPLES: return None
    mean = max(1.0, float(stat.get("mean", 0.0)))
    std = math.sqrt(max(0.0, float(stat.get("variance", 0.0))))
    anomaly = min(1.0, max(0.0, (cpu - mean) / max(20.0, 2.0 * std)))
    normalized = min(1.0, max(0.0, cpu / max(100.0, CPU_THRESHOLD)))
    return 0.65 * normalized + 0.35 * anomaly

def update_shadow_policy(state, cpu, now, alpha=SHADOW_EWMA_ALPHA,
                         route_threshold=SHADOW_ROUTE_THRESHOLD,
                         clear_threshold=SHADOW_CLEAR_THRESHOLD,
                         sustained_samples=SHADOW_SUSTAINED_SAMPLES,
                         recovery_samples=SHADOW_RECOVERY_SAMPLES,
                         recovery_dwell_sec=SHADOW_RECOVERY_DWELL_SEC):
    """Update shadow-only EWMA/hysteresis state; return route/recover/None."""
    smoothed = cpu if state.get("ewma") is None else alpha * cpu + (1.0 - alpha) * state["ewma"]
    state["ewma"] = smoothed
    if not state.get("active", False):
        state["high_samples"] = state.get("high_samples", 0) + 1 if smoothed >= route_threshold else 0
        if state["high_samples"] >= sustained_samples:
            state["active"] = True
            state["low_samples"] = 0
            state["low_since"] = None
            return "route"
        return None

    if smoothed <= clear_threshold:
        state["low_samples"] = state.get("low_samples", 0) + 1
        if state.get("low_since") is None:
            state["low_since"] = now
        if state["low_samples"] >= recovery_samples and now - state["low_since"] >= recovery_dwell_sec:
            state["active"] = False
            state["high_samples"] = 0
            state["low_samples"] = 0
            state["low_since"] = None
            return "recover"
    else:
        state["low_samples"] = 0
        state["low_since"] = None
    return None

def shadow_config_error(alpha=SHADOW_EWMA_ALPHA, route_threshold=SHADOW_ROUTE_THRESHOLD,
                        clear_threshold=SHADOW_CLEAR_THRESHOLD,
                        sustained_samples=SHADOW_SUSTAINED_SAMPLES,
                        recovery_samples=SHADOW_RECOVERY_SAMPLES,
                        recovery_dwell_sec=SHADOW_RECOVERY_DWELL_SEC):
    """Return a readable validation error, or None for a safe shadow configuration."""
    values = (alpha, route_threshold, clear_threshold, recovery_dwell_sec)
    if not all(math.isfinite(float(value)) for value in values):
        return "shadow thresholds, alpha, and dwell must be finite numbers"
    if not 0 < alpha <= 1:
        return "shadow EWMA alpha must be in (0, 1]"
    if route_threshold <= clear_threshold:
        return "shadow route threshold must be greater than clear threshold"
    if route_threshold < 0 or clear_threshold < 0:
        return "shadow CPU thresholds must be non-negative"
    if sustained_samples < 1 or recovery_samples < 1:
        return "shadow sample counts must be at least 1"
    if recovery_dwell_sec < 0:
        return "shadow recovery dwell must be non-negative"
    return None

def cpu_pressure():
    try:
        with open("/proc/pressure/cpu") as f:
            for line in f:
                if line.startswith("some "):
                    parts = dict(x.split("=") for x in line.split()[1:] if "=" in x)
                    return float(parts.get("avg10", 0.0)) / 100.0
    except (OSError, ValueError, TypeError):
        pass
    return None

def cgroup_cpu_stat(cgroup):
    if not cgroup:
        return None
    try:
        data = {}
        with open(os.path.join(cgroup, "cpu.stat")) as f:
            for line in f:
                key, value = line.split()[:2]
                data[key] = int(value)
        return data
    except (OSError, ValueError):
        return None

def unit_quota(unit):
    try:
        value = subprocess.check_output(
            ["systemctl", "--user", "show", unit, "--property=CPUQuotaPerSecUSec", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
        if value.endswith("s"):
            return float(value[:-1]) * 100.0
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None

def schedstat_delay_ms():
    try:
        with open(f"/proc/{os.getpid()}/schedstat") as f:
            fields = f.read().split()
        return int(fields[1]) / 1_000_000.0
    except (OSError, ValueError, IndexError):
        return None

def set_unit_quota(unit, quota):
    try:
        subprocess.run(
            ["systemctl", "--user", "set-property", unit, f"CPUQuota={quota:.0f}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def dynamic_quota_target(current, pressure, demand, throttled_ratio=0.0, sched_delay_ms=None):
    if current is None:
        current = QUOTA_MIN
    high_pressure = (
        (pressure is not None and pressure >= QUOTA_PRESSURE_HIGH) or
        (sched_delay_ms is not None and sched_delay_ms >= QUOTA_SCHED_DELAY_HIGH_MS)
    )
    low_pressure = (
        (pressure is None or pressure <= QUOTA_PRESSURE_LOW) and
        (sched_delay_ms is None or sched_delay_ms <= QUOTA_SCHED_DELAY_LOW_MS)
    )
    if high_pressure:
        return max(QUOTA_MIN, current - QUOTA_STEP)
    # Throttling alone is not a reason to raise quota while scheduler pressure
    # is in the hysteresis band; require corroborated low pressure for increases.
    if throttled_ratio >= QUOTA_THROTTLE_HIGH and low_pressure:
        return min(QUOTA_MAX, current + QUOTA_STEP)
    if low_pressure and demand >= 0.60:
        return min(QUOTA_MAX, current + QUOTA_STEP)
    if demand < 0.15 and low_pressure:
        return max(QUOTA_MIN, current - QUOTA_STEP)
    return current

def aimd_quota_target(current, pressure, demand, throttled_ratio=0.0, sched_delay_ms=None,
                      decrease=QUOTA_AIMD_DECREASE, additive_increase=QUOTA_AIMD_ADD):
    """Experimental AIMD quota proposal. Safe to call in shadow-only mode."""
    if current is None or not math.isfinite(current):
        return None
    if not 0 < decrease < 1 or additive_increase <= 0:
        return None
    # AIMD is deliberately more conservative than the existing step controller:
    # router-process runqueue delay alone is not proof of host/workload pressure.
    # Require PSI to be available; a high router delay may corroborate elevated
    # PSI, but must not trigger multiplicative decrease while PSI is low/missing.
    high_pressure = pressure is not None and (
        pressure >= QUOTA_PRESSURE_HIGH or
        (pressure > QUOTA_PRESSURE_LOW and
         sched_delay_ms is not None and
         sched_delay_ms >= QUOTA_SCHED_DELAY_HIGH_MS)
    )
    low_pressure = (
        pressure is not None and pressure <= QUOTA_PRESSURE_LOW and
        (sched_delay_ms is None or sched_delay_ms <= QUOTA_SCHED_DELAY_LOW_MS)
    )
    if high_pressure:
        return max(QUOTA_MIN, current * decrease)
    if low_pressure and (demand >= 0.60 or throttled_ratio >= QUOTA_THROTTLE_HIGH):
        return min(QUOTA_MAX, current + additive_increase)
    return current

def move(pid, target_cgroup, reason="unspecified"):
    try:
        with open(os.path.join(target_cgroup, "cgroup.procs"), "a") as f: f.write(str(pid)+"\n")
        return True
    except Exception as e:
        logging.warning('cgroup move failed pid=%s reason=%s target=%s error=%s', pid, reason, target_cgroup, e)
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

def load_state():
    routes = {}
    try:
        with open(STATE_FILE) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t", 5)
                if len(fields) != 6:
                    continue
                try:
                    pid, cpu = int(fields[0]), float(fields[1])
                except ValueError:
                    continue
                routes[pid] = (cpu, fields[2], fields[3], fields[4], fields[5])
    except OSError:
        pass
    return routes

def main():
    global SHADOW_ENABLED
    prev={}
    hot={}
    shadow_policy={}
    moved=set()
    routes=load_state()
    recovery_hot={}
    recovery_since={}
    route_prev={}
    stats=load_stats() if ADAPTIVE else {}
    last_move={}
    last_quota_change=0.0
    quota_dwell_until=0.0
    last_stats_save=0.0
    last_history_save=time.monotonic()
    route_events=0
    recovery_events=0
    quota_changes=0
    quota_observe = QUOTA_DYNAMIC or QUOTA_AIMD_SHADOW
    history_stat_prev = cgroup_cpu_stat(user_cgroup(HEAVY_UNIT)) if quota_observe else None
    history_stat_time = time.monotonic()
    history_sched_prev = schedstat_delay_ms() if quota_observe else None
    history_proc_prev = {}
    history_proc_time = time.monotonic()
    state_dirty=True
    current_quota=unit_quota(HEAVY_UNIT) if quota_observe else None
    quota_stat_prev = cgroup_cpu_stat(user_cgroup(HEAVY_UNIT)) if quota_observe else None
    quota_stat_time = time.monotonic()
    quota_sched_prev = schedstat_delay_ms() if quota_observe else None
    logging.info('started threshold=%.0f%% sustained=%ss recovery<%.0f%% adaptive=%s shadow=%s', CPU_THRESHOLD, SAMPLE_SEC*SUSTAINED_SAMPLES, RECOVERY_CPU_THRESHOLD, ADAPTIVE, SHADOW_ENABLED)
    if SHADOW_ENABLED:
        shadow_error = shadow_config_error()
        if shadow_error:
            logging.error("shadow mode disabled due to invalid configuration: %s", shadow_error)
            SHADOW_ENABLED = False
    if SHADOW_ENABLED:
        logging.info('shadow policy=ewma-hysteresis alpha=%.2f route>=%.1f%% clear<=%.1f%% sustained_samples=%s recovery_samples=%s recovery_dwell=%.1fs; actions are log-only',
                     SHADOW_EWMA_ALPHA, SHADOW_ROUTE_THRESHOLD, SHADOW_CLEAR_THRESHOLD,
                     SHADOW_SUSTAINED_SAMPLES, SHADOW_RECOVERY_SAMPLES, SHADOW_RECOVERY_DWELL_SEC)
    if QUOTA_AIMD_SHADOW:
        logging.info('quota policy=aimd-shadow decrease=%.3f additive_increase=%.1f; proposals are log-only',
                     QUOTA_AIMD_DECREASE, QUOTA_AIMD_ADD)
    ensure_workload_units()
    missing_cgroups = None
    while True:
        gateway_cgroup = user_cgroup(PARENT_UNIT)
        heavy_cgroup = user_cgroup(HEAVY_UNIT)
        if not gateway_cgroup or not heavy_cgroup:
            missing = tuple(unit for unit, path in ((PARENT_UNIT, gateway_cgroup), (HEAVY_UNIT, heavy_cgroup)) if not path)
            if missing != missing_cgroups:
                logging.warning("routing paused: missing ControlGroup for %s; check user systemd manager and slice units", ", ".join(missing))
                missing_cgroups = missing
            time.sleep(SAMPLE_SEC)
            continue
        if missing_cgroups is not None:
            logging.info("routing resumed; workload cgroups are available")
            missing_cgroups = None
        if not history_proc_prev:
            history_proc_prev, _, _, _ = history_cpu_attribution({}, 1.0, gateway_cgroup, heavy_cgroup)
            history_proc_time = time.monotonic()
        now={}
        if gateway_cgroup and heavy_cgroup:
            protected_pids = cgroup_tree_pids(gateway_cgroup)
            heavy_pids = cgroup_tree_pids(heavy_cgroup) if SHADOW_ENABLED else set()
            for pid in protected_pids | heavy_pids:
                cmd=cmdline(pid)
                if not cmd or is_excluded(cmd): continue
                _,ut,st=proc_stat(pid)
                now[pid]=(ut+st,cmd)
                if pid in prev:
                    dticks=ut+st-prev[pid][0]
                    # Linux USER_HZ is normally 100. Normalize to % of one CPU.
                    cpu=(dticks / max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK'])) / SAMPLE_SEC)*100
                    cls = command_class(cmd)
                    stat = update_stat(stats, cls, cpu) if ADAPTIVE else None
                    score = adaptive_score(cpu, stat) if ADAPTIVE else None
                    if SHADOW_ENABLED:
                        shadow = shadow_policy.setdefault(pid, {"ewma": None, "active": False, "high_samples": 0, "low_samples": 0, "low_since": None})
                        shadow_action = update_shadow_policy(shadow, cpu, time.monotonic())
                        if shadow_action:
                            logging.info('shadow decision=%s pid=%s cpu=%.1f%% ewma=%.1f%% route_threshold=%.1f%% clear_threshold=%.1f%% high_samples=%s low_samples=%s actual_group=%s cmd=%s',
                                         shadow_action, pid, cpu, shadow["ewma"], SHADOW_ROUTE_THRESHOLD,
                                         SHADOW_CLEAR_THRESHOLD, shadow["high_samples"], shadow["low_samples"],
                                         "protected" if pid in protected_pids else "heavy", cmd[:180])
                    if pid in protected_pids:
                        if cpu >= CPU_THRESHOLD:
                            hot[pid]=hot.get(pid,0)+1
                        else:
                            hot[pid]=0
                        known_heavy = command_heavy(cmd)
                        sustained = hot.get(pid,0) >= SUSTAINED_SAMPLES
                        adaptive_hot = score is not None and score >= ROUTE_THRESHOLD
                        if known_heavy or sustained or adaptive_hot:
                            if pid in last_move and time.monotonic() - last_move[pid] < COOLDOWN_SEC:
                                continue
                            source_path = proc_cgroup(pid)
                            source_cgroup = SYSTEMD_PREFIX + source_path if source_path else gateway_cgroup
                            reason = "known-heavy" if known_heavy else ("sustained-cpu" if sustained else "adaptive")
                            if pid not in moved and move(pid, heavy_cgroup, reason):
                                moved.add(pid)
                                routes[pid] = (cpu, reason, source_cgroup, heavy_cgroup, cmd)
                                last_move[pid] = time.monotonic()
                                route_events += 1
                                state_dirty = True
                                logging.info('route decision=accepted pid=%s cpu=%.1f%% reason=%s threshold=%.1f%% sustained_samples=%s cmd=%s',
                                             pid, cpu, reason, CPU_THRESHOLD, hot.get(pid, 0), cmd[:180])
            for pid in list(routes):
                if not os.path.exists(f"/proc/{pid}") or not in_cgroup_tree(pid, heavy_cgroup):
                    moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                    continue
                moved.add(pid)
                cmd = cmdline(pid)
                if not cmd or (routes[pid][4] and cmd != routes[pid][4]):
                    logging.info('dropping stale route pid=%s after process identity changed', pid)
                    moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                    continue
                _, ut, st = proc_stat(pid)
                prev_route = route_prev.get(pid)
                if prev_route:
                    dticks = ut + st - prev_route
                    cpu = (dticks / max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK'])) / SAMPLE_SEC) * 100
                    if cpu < RECOVERY_CPU_THRESHOLD:
                        recovery_hot[pid] = recovery_hot.get(pid, 0) + 1
                        recovery_since.setdefault(pid, time.monotonic())
                    else:
                        recovery_hot[pid] = 0; recovery_since.pop(pid, None)
                    if (recovery_hot.get(pid, 0) >= RECOVERY_SAMPLES and
                            time.monotonic() - recovery_since.get(pid, time.monotonic()) >= RECOVERY_DWELL_SEC):
                        source = routes[pid][2] if os.path.isdir(routes[pid][2]) else gateway_cgroup
                        if move(pid, source):
                            logging.info('unrouted pid=%s cpu=%.1f%% recovery', pid, cpu)
                            recovery_events += 1
                            moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
        if quota_observe and heavy_cgroup:
            now_mono = time.monotonic()
            if now_mono >= quota_dwell_until and now_mono - last_quota_change >= QUOTA_INTERVAL_SEC:
                pressure = cpu_pressure()
                after_stat = cgroup_cpu_stat(heavy_cgroup)
                sched_now = schedstat_delay_ms()
                sched_delay_ms = None
                throttled_ratio = 0.0
                cgroup_demand = 0.0
                now_stat_time = time.monotonic()
                if quota_sched_prev is not None and sched_now is not None:
                    sched_delay_ms = max(0.0, sched_now - quota_sched_prev)
                quota_sched_prev = sched_now
                telemetry_ok = after_stat is not None
                if quota_stat_prev and after_stat:
                    elapsed = max(0.001, now_stat_time - quota_stat_time)
                    usage = max(0, after_stat.get("usage_usec", 0) - quota_stat_prev.get("usage_usec", 0))
                    periods = max(0, after_stat.get("nr_periods", 0) - quota_stat_prev.get("nr_periods", 0))
                    throttled = max(0, after_stat.get("nr_throttled", 0) - quota_stat_prev.get("nr_throttled", 0))
                    throttled_ratio = throttled / periods if periods else 0.0
                    usage_pct = usage / (elapsed * 10000.0)
                    cgroup_demand = min(1.0, usage_pct / max(100.0, current_quota or QUOTA_MIN))
                quota_stat_prev = after_stat
                quota_stat_time = now_stat_time
                demand = 0.0
                if routes:
                    demand = min(1.0, max(
                        adaptive_score(data[0], stats.get(command_class(data[4]))) or
                        min(1.0, data[0] / max(100.0, CPU_THRESHOLD))
                        for data in routes.values()
                    ))
                demand = max(demand, cgroup_demand)
                target = current_quota
                if telemetry_ok and current_quota is not None and (pressure is not None or sched_delay_ms is not None):
                    target = dynamic_quota_target(current_quota, pressure, demand, throttled_ratio, sched_delay_ms)
                if QUOTA_AIMD_SHADOW and telemetry_ok and current_quota is not None:
                    aimd_target = aimd_quota_target(current_quota, pressure, demand, throttled_ratio, sched_delay_ms)
                    if aimd_target is not None and aimd_target != current_quota:
                        logging.info('quota shadow=aimd current=%.0f%% proposed=%.0f%% pressure=%s sched_delay=%sms demand=%.2f throttled=%.3f action=log-only',
                                     current_quota, aimd_target,
                                     'n/a' if pressure is None else f'{pressure:.3f}',
                                     'n/a' if sched_delay_ms is None else f'{sched_delay_ms:.2f}',
                                     demand, throttled_ratio)
                if QUOTA_DYNAMIC and target != current_quota and set_unit_quota(HEAVY_UNIT, target):
                    logging.info('quota changed unit=%s old=%.0f%% new=%.0f%% pressure=%s sched_delay=%sms demand=%.2f',
                                 HEAVY_UNIT, current_quota or 0, target,
                                 'n/a' if pressure is None else f'{pressure:.3f}',
                                 'n/a' if sched_delay_ms is None else f'{sched_delay_ms:.2f}', demand)
                    current_quota = target
                    quota_changes += 1
                    last_quota_change = now_mono
                    quota_dwell_until = now_mono + QUOTA_MIN_DWELL_SEC
        alive=set(now)
        for pid in list(hot):
            if pid not in protected_pids: hot.pop(pid,None)
        for pid in list(shadow_policy):
            if pid not in alive: shadow_policy.pop(pid, None)
        for pid in list(moved):
            if not os.path.exists(f"/proc/{pid}") or not in_cgroup_tree(pid, heavy_cgroup):
                moved.discard(pid)
                routes.pop(pid, None)
                state_dirty = True
        for pid in list(routes):
            if pid not in moved:
                routes.pop(pid, None)
                route_prev.pop(pid, None)
                recovery_hot.pop(pid, None)
                recovery_since.pop(pid, None)
                state_dirty = True
            else:
                _, ut, st = proc_stat(pid)
                route_prev[pid] = ut + st
        now_mono = time.monotonic()
        if state_dirty:
            write_state(routes)
            state_dirty = False
        if ADAPTIVE and now_mono - last_stats_save >= STATS_SAVE_INTERVAL_SEC:
            save_stats(stats)
            last_stats_save = now_mono
        if now_mono - last_history_save >= HISTORY_INTERVAL_SEC:
            hstat = cgroup_cpu_stat(heavy_cgroup) if heavy_cgroup else None
            elapsed = max(0.001, now_mono - history_stat_time)
            usage_pct = None; throttle_ratio = None
            if history_stat_prev and hstat:
                usage = max(0, hstat.get("usage_usec", 0) - history_stat_prev.get("usage_usec", 0))
                periods = max(0, hstat.get("nr_periods", 0) - history_stat_prev.get("nr_periods", 0))
                throttled = max(0, hstat.get("nr_throttled", 0) - history_stat_prev.get("nr_throttled", 0))
                usage_pct = usage / (elapsed * 10000.0)
                throttle_ratio = throttled / periods if periods else 0.0
            history_sched_now = schedstat_delay_ms() if quota_observe else None
            history_sched_delay = None
            if history_sched_prev is not None and history_sched_now is not None:
                history_sched_delay = max(0.0, history_sched_now - history_sched_prev)
            history_sched_prev = history_sched_now
            proc_elapsed = max(0.001, now_mono - history_proc_time)
            history_proc_prev, guarded_cpu, unmanaged_cpu, top_cpu = history_cpu_attribution(
                history_proc_prev, proc_elapsed, gateway_cgroup, heavy_cgroup)
            history_proc_time = now_mono
            append_history({"schema_version": 3, "ts": int(time.time()), "routed": len(routes), "route_events": route_events,
                            "recovery_events": recovery_events, "quota_changes": quota_changes,
                            "quota": current_quota, "cpu_psi_some_avg10": cpu_pressure(),
                            "router_runqueue_delay_ms": history_sched_delay, "heavy_cpu_pct": usage_pct,
                            "heavy_throttle_ratio": throttle_ratio,
                            "guarded_cpu_pct": round(guarded_cpu, 1),
                            "unmanaged_cpu_pct": round(unmanaged_cpu, 1),
                            "top_cpu": top_cpu})
            history_stat_prev = hstat; history_stat_time = now_mono; last_history_save = now_mono
        prev=now
        time.sleep(SAMPLE_SEC)

if __name__=='__main__': main()
