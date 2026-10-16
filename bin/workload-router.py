#!/usr/bin/env python3
import json, math, os, time, logging, subprocess, signal

SYSTEMD_PREFIX = "/sys/fs/cgroup"


def core_float_env(name, default):
    """Parse core routing floats without crashing import on malformed service env."""
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def core_int_env(name, default):
    """Represent malformed core integer settings as invalid, not implicit defaults."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return None


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


def numeric_env(name, default):
    """Represent malformed numeric controller settings as invalid sentinels."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float("nan")


SAMPLE_SEC = core_float_env("WORKLOAD_GUARD_SAMPLE_SEC", 2)
SUSTAINED_SAMPLES = core_int_env("WORKLOAD_GUARD_SUSTAINED_SAMPLES", 4)
CPU_THRESHOLD = core_float_env("WORKLOAD_GUARD_CPU_THRESHOLD", 70)
SHADOW_ENABLED = os.getenv("WORKLOAD_GUARD_SHADOW", "false").lower() in ("1", "true", "yes", "on")
SHADOW_EWMA_ALPHA = shadow_float_env("WORKLOAD_GUARD_SHADOW_EWMA_ALPHA", 0.35)
SHADOW_ROUTE_THRESHOLD = shadow_float_env("WORKLOAD_GUARD_SHADOW_ROUTE_THRESHOLD", CPU_THRESHOLD)
SHADOW_SUSTAINED_SAMPLES = shadow_int_env("WORKLOAD_GUARD_SHADOW_SUSTAINED_SAMPLES", SUSTAINED_SAMPLES)
ADAPTIVE = os.getenv("WORKLOAD_GUARD_ADAPTIVE", "false").lower() in ("1", "true", "yes", "on")
LEARNING_RATE = core_float_env("WORKLOAD_GUARD_LEARNING_RATE", 0.15)
ROUTE_THRESHOLD = core_float_env("WORKLOAD_GUARD_ROUTE_THRESHOLD", 0.75)
COOLDOWN_SEC = core_float_env("WORKLOAD_GUARD_COOLDOWN_SEC", 20)
MIN_SAMPLES = core_int_env("WORKLOAD_GUARD_MIN_SAMPLES", 8)
RECOVERY_CPU_THRESHOLD = core_float_env("WORKLOAD_GUARD_RECOVERY_CPU_THRESHOLD", 35)
RECOVERY_SAMPLES = core_int_env("WORKLOAD_GUARD_RECOVERY_SAMPLES", 10)
RECOVERY_DWELL_SEC = core_float_env("WORKLOAD_GUARD_RECOVERY_DWELL_SEC", 20)
SHADOW_CLEAR_THRESHOLD = shadow_float_env("WORKLOAD_GUARD_SHADOW_CLEAR_THRESHOLD", RECOVERY_CPU_THRESHOLD)
SHADOW_RECOVERY_SAMPLES = shadow_int_env("WORKLOAD_GUARD_SHADOW_RECOVERY_SAMPLES", RECOVERY_SAMPLES)
SHADOW_RECOVERY_DWELL_SEC = shadow_float_env("WORKLOAD_GUARD_SHADOW_RECOVERY_DWELL_SEC", RECOVERY_DWELL_SEC)
QUOTA_DYNAMIC = os.getenv("WORKLOAD_GUARD_DYNAMIC_QUOTA", "false").lower() in ("1", "true", "yes", "on")
QUOTA_AIMD_SHADOW = os.getenv("WORKLOAD_GUARD_AIMD_SHADOW", "false").lower() in ("1", "true", "yes", "on")
AGGREGATE_ROUTING = os.getenv("WORKLOAD_GUARD_AGGREGATE_ROUTING", "false").lower() in ("1", "true", "yes", "on")
AGGREGATE_ROUTE_RATIO = core_float_env("WORKLOAD_GUARD_AGGREGATE_ROUTE_RATIO", 0.85)
AGGREGATE_CLEAR_RATIO = core_float_env("WORKLOAD_GUARD_AGGREGATE_CLEAR_RATIO", 0.65)
AGGREGATE_MIN_WORKER_CPU = core_float_env("WORKLOAD_GUARD_AGGREGATE_MIN_WORKER_CPU", 20.0)
AGGREGATE_SUSTAINED_SAMPLES = core_int_env("WORKLOAD_GUARD_AGGREGATE_SUSTAINED_SAMPLES", 3)
QUOTA_AIMD_DECREASE = numeric_env("WORKLOAD_GUARD_AIMD_DECREASE", "0.80")
QUOTA_AIMD_ADD = numeric_env("WORKLOAD_GUARD_AIMD_ADD", "10")
QUOTA_INTERVAL_SEC = numeric_env("WORKLOAD_GUARD_QUOTA_INTERVAL_SEC", "5")
QUOTA_MIN = numeric_env("WORKLOAD_GUARD_QUOTA_MIN", "100")
QUOTA_MAX = numeric_env("WORKLOAD_GUARD_QUOTA_MAX", "400")
QUOTA_STEP = numeric_env("WORKLOAD_GUARD_QUOTA_STEP", "50")
QUOTA_PRESSURE_HIGH = numeric_env("WORKLOAD_GUARD_QUOTA_PRESSURE_HIGH", "0.40")
QUOTA_PRESSURE_LOW = numeric_env("WORKLOAD_GUARD_QUOTA_PRESSURE_LOW", "0.10")
QUOTA_THROTTLE_HIGH = numeric_env("WORKLOAD_GUARD_QUOTA_THROTTLE_HIGH", "0.10")
QUOTA_MIN_DWELL_SEC = numeric_env("WORKLOAD_GUARD_QUOTA_MIN_DWELL_SEC", "10")
QUOTA_SCHED_DELAY_HIGH_MS = numeric_env("WORKLOAD_GUARD_QUOTA_SCHED_DELAY_HIGH_MS", "20")
QUOTA_SCHED_DELAY_LOW_MS = numeric_env("WORKLOAD_GUARD_QUOTA_SCHED_DELAY_LOW_MS", "5")
STATS_SAVE_INTERVAL_SEC = core_float_env("WORKLOAD_GUARD_STATS_SAVE_INTERVAL_SEC", 10)
HISTORY_INTERVAL_SEC = core_float_env("WORKLOAD_GUARD_HISTORY_INTERVAL_SEC", 300)
HISTORY_FILE = os.getenv("WORKLOAD_GUARD_HISTORY_FILE", os.path.join(os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "linux-workload-guard", "history.jsonl"))
STATS_FILE = os.getenv("WORKLOAD_GUARD_STATS_FILE", os.path.join(
    os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "linux-workload-guard", "stats.json"))
PARENT_UNIT = os.getenv("WORKLOAD_GUARD_PARENT_UNIT", "protected-workload.slice")
HEAVY_UNIT = os.getenv("WORKLOAD_GUARD_HEAVY_UNIT", "heavy-workload.slice")
DEFAULT_RECOVERY_UNIT = (PARENT_UNIT[:-6] if PARENT_UNIT.endswith(".slice") else PARENT_UNIT) + "-recovery.slice"
RECOVERY_UNIT = os.getenv("WORKLOAD_GUARD_RECOVERY_UNIT", DEFAULT_RECOVERY_UNIT)
STATE_FILE = os.getenv(
    "WORKLOAD_GUARD_STATE_FILE",
    os.path.join(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "linux-workload-guard", "routes.tsv"),
)
SCOPE_STATE_FILE = os.getenv("WORKLOAD_GUARD_SCOPE_STATE_FILE", STATE_FILE + ".scopes.tsv")
AGGREGATE_SCOPE_WEIGHT = core_int_env("WORKLOAD_GUARD_AGGREGATE_SCOPE_WEIGHT", 20)
AGGREGATE_SCOPE_DESCRIPTION = "Linux Workload Guard aggregate candidate"
AGGREGATE_MAX_SCOPES = 64

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
STOP_REQUESTED = False
SCOPE_IDENTITY_CACHE = {}
SCOPE_DISCOVERY_OVERFLOW = False
SCOPE_DISCOVERY_ERROR = False

def request_shutdown(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


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
    """Start the parent, stable recovery destination, and heavy slices."""
    for unit in (PARENT_UNIT, RECOVERY_UNIT, HEAVY_UNIT):
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


def parse_proc_stat(text):
    try:
        end = text.rfind(')')
        if end < 0:
            return None
        fields = text[end+2:].split()
        # Fields begin at proc stat field 3 (state); starttime is field 22.
        if len(fields) <= 19:
            return None
        return int(fields[1]), int(fields[11]), int(fields[12]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def proc_stat(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return parse_proc_stat(f.read())
    except OSError:
        return None


def route_process_matches(route, command, starttime):
    """Persisted route identity is the kernel starttime, not the executable name.

    exec(2) can legitimately change cmdline without changing PID/starttime. A
    command change resets classification samples, but must not orphan an
    already-routed process in the heavy cgroup and make it unrecoverable.
    """
    if not route or len(route) < 6 or route[5] is None:
        return False
    return route[5] == starttime

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

def cgroup_path_in_tree(path, parent_cgroup):
    """Check a known cgroup-v2 path against a cgroup tree boundary."""
    if not path or not parent_cgroup:
        return False
    parent = parent_cgroup.rstrip('/')
    if parent.startswith(SYSTEMD_PREFIX + '/'):
        parent = parent[len(SYSTEMD_PREFIX):]
    return path == parent or path.startswith(parent + '/')


def routed_process_membership(pid, heavy_cgroup):
    """Return gone/unknown/inside/outside without conflating unreadable proc data.

    A transient /proc/<pid>/cgroup read failure is not proof that the process
    left the heavy cgroup. Route state must be retained until membership is
    known or the process is confirmed gone.
    """
    try:
        os.stat(f"/proc/{pid}")
    except FileNotFoundError:
        return "gone"
    except OSError:
        return "unknown"
    path = proc_cgroup(pid)
    if path is None:
        return "unknown"
    return "inside" if cgroup_path_in_tree(path, heavy_cgroup) else "outside"


def in_cgroup_tree(pid, parent_cgroup):
    return cgroup_path_in_tree(proc_cgroup(pid), parent_cgroup)

def cgroup_tree_pids(parent_cgroup):
    """Return PIDs under a cgroup tree without scanning unrelated /proc entries."""
    if not parent_cgroup:
        return set()
    root = parent_cgroup
    if root.startswith(SYSTEMD_PREFIX + '/'):
        root = root.rstrip('/')
    if not os.path.isdir(root):
        return set()
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
        return set()
    return pids

def read_cgroup_usage_usec(cgroup):
    """Read only real usage_usec; scope routing does not require quota counters."""
    try:
        found = None
        with open(os.path.join(cgroup, "cpu.stat")) as stream:
            for line in stream:
                fields = line.split()
                if len(fields) != 2:
                    return None
                if fields[0] == "usage_usec":
                    if found is not None or not fields[1].isdigit():
                        return None
                    found = int(fields[1])
        return found
    except (OSError, ValueError, TypeError):
        return None

def workload_scope_candidates(parent_cgroup, tracked_units=()):
    """Return direct, explicitly opted-in workload scopes under the protected slice."""
    global SCOPE_DISCOVERY_OVERFLOW, SCOPE_DISCOVERY_ERROR
    candidates = {}
    seen_units = set()
    SCOPE_DISCOVERY_OVERFLOW = False
    SCOPE_DISCOVERY_ERROR = False
    if not parent_cgroup:
        return candidates
    if not os.path.isdir(parent_cgroup):
        return candidates
    try:
        with os.scandir(parent_cgroup) as stream:
            entries = list(stream)
        eligible_entries = []
        for entry in entries:
            if not (entry.name.startswith("lwg-workload-") and entry.name.endswith(".scope")):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    eligible_entries.append(entry)
            except OSError:
                continue
        entries = sorted(eligible_entries, key=lambda entry: entry.name)
        if len(entries) > AGGREGATE_MAX_SCOPES:
            # Overflow disables new routing. Continue inspecting only already
            # tracked scopes so controller-owned weights can still be restored.
            SCOPE_DISCOVERY_OVERFLOW = True
            tracked = set(tracked_units)
            entries = [entry for entry in entries if entry.name in tracked]
        pending = []
        for entry in entries:
            seen_units.add(entry.name)
            path = entry.path
            try:
                info = entry.stat(follow_symlinks=False)
                if not os.path.isfile(os.path.join(path, "cgroup.procs")):
                    continue
            except OSError:
                continue
            identity = (info.st_ino, path)
            cached_identity = SCOPE_IDENTITY_CACHE.get(entry.name)
            if cached_identity == (info.st_ino, path, "rejected"):
                continue
            if cached_identity == identity:
                candidates[entry.name] = {"path": path, "inode": info.st_ino,
                                          "usage_usec": read_cgroup_usage_usec(path)}
                continue
            pending.append((entry, path, info, identity))
        if pending:
            # Query all uncached units in one bounded systemd round trip. A
            # per-property/per-scope loop can block the controller for minutes
            # at the configured 64-scope ceiling when D-Bus is degraded.
            properties = unit_properties([entry.name for entry, _, _, _ in pending],
                                         ("Description", "ActiveState", "ControlGroup"))
            if properties is None:
                SCOPE_DISCOVERY_ERROR = True
                return {}
            for entry, path, info, identity in pending:
                observed = properties.get(entry.name)
                if observed is None:
                    # A scope may have exited while systemd was answering. It
                    # is not a candidate for this sample; retry discovery next
                    # cycle rather than trusting stale unit identity.
                    continue
                expected_control_group = path[len(SYSTEMD_PREFIX):] if path.startswith(SYSTEMD_PREFIX) else path
                if (observed.get("Description") != AGGREGATE_SCOPE_DESCRIPTION or
                        observed.get("ActiveState") != "active" or
                        (observed.get("ControlGroup") or "").rstrip("/") != expected_control_group.rstrip("/")):
                    SCOPE_IDENTITY_CACHE[entry.name] = (info.st_ino, path, "rejected")
                    continue
                SCOPE_IDENTITY_CACHE[entry.name] = identity
                candidates[entry.name] = {"path": path, "inode": info.st_ino,
                                          "usage_usec": read_cgroup_usage_usec(path)}
    except OSError:
        SCOPE_DISCOVERY_ERROR = True
        return {}
    for unit in set(SCOPE_IDENTITY_CACHE) - seen_units:
        SCOPE_IDENTITY_CACHE.pop(unit, None)
    return candidates

def unit_property(unit, prop):
    try:
        value = subprocess.check_output(
            ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
        return value if value and value != "-" else None
    except (OSError, subprocess.SubprocessError):
        return None

def unit_properties(units, properties):
    """Read selected properties for many units in one bounded systemctl call."""
    if not units:
        return {}
    command = ["systemctl", "--user", "show", *units, "--property=Id"]
    command.extend(f"--property={prop}" for prop in properties)
    command.append("--no-pager")
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    parsed = {}
    record = {}
    for line in result.stdout.splitlines() + [""]:
        if not line.strip():
            unit = record.get("Id")
            if unit:
                parsed[unit] = record
            record = {}
            continue
        key, separator, value = line.partition("=")
        if separator:
            record[key] = value
    return parsed

def unit_cpu_weight(unit):
    value = unit_property(unit, "CPUWeight")
    try:
        weight = int(value)
        return weight if 1 <= weight <= 10000 else None
    except (TypeError, ValueError):
        control_group = unit_property(unit, "ControlGroup")
        if not control_group:
            return None
        try:
            with open(os.path.join(SYSTEMD_PREFIX, control_group.lstrip("/"), "cpu.weight")) as stream:
                weight = int(stream.read().strip())
            return weight if 1 <= weight <= 10000 else None
        except (OSError, ValueError):
            return None

def set_unit_cpu_weight(unit, weight, expected_cgroup=None, expected_inode=None):
    """Set/read back a scope weight only while its systemd identity still matches."""
    if not isinstance(weight, int) or not 1 <= weight <= 10000:
        return False
    control_group = unit_property(unit, "ControlGroup")
    if not control_group or not unit.endswith(".scope") or not unit.startswith("lwg-workload-"):
        return False
    if unit_property(unit, "Description") != AGGREGATE_SCOPE_DESCRIPTION:
        return False
    if expected_cgroup is not None:
        expected = expected_cgroup
        if expected.startswith(SYSTEMD_PREFIX):
            expected = expected[len(SYSTEMD_PREFIX):]
        if control_group.rstrip("/") != expected.rstrip("/"):
            return False
        try:
            if expected_inode is not None and os.stat(expected_cgroup).st_ino != expected_inode:
                return False
        except OSError:
            return False
    if unit_property(unit, "ActiveState") != "active":
        return False
    try:
        subprocess.run(["systemctl", "--user", "set-property", unit, f"CPUWeight={weight}"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    observed = unit_cpu_weight(unit)
    if observed != weight or unit_property(unit, "ControlGroup") != control_group:
        return False
    try:
        return expected_inode is None or os.stat(expected_cgroup).st_ino == expected_inode
    except OSError:
        return False

def load_scope_state(path=SCOPE_STATE_FILE):
    state = {}
    try:
        with open(path) as stream:
            for line in stream:
                fields = line.rstrip("\n").split("\t")
                if len(fields) not in (4, 5, 6):
                    continue
                unit, cgroup, inode_text, weight_text = fields[:4]
                try:
                    inode, weight = int(inode_text), int(weight_text)
                    route_weight = int(fields[4]) if len(fields) >= 5 else AGGREGATE_SCOPE_WEIGHT
                except ValueError:
                    continue
                if (unit.startswith("lwg-workload-") and unit.endswith(".scope") and inode > 0
                        and 1 <= weight <= 10000 and 1 <= route_weight <= 10000 and cgroup.startswith("/")):
                    state[unit] = {"cgroup": cgroup, "inode": inode, "original_weight": weight}
                    state[unit]["route_weight"] = route_weight
                    state[unit]["suppressed"] = len(fields) == 6 and fields[5] == "1"
    except OSError:
        pass
    return state

def write_scope_state(state, path=SCOPE_STATE_FILE):
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as stream:
            for unit, data in sorted(state.items()):
                stream.write("\t".join((unit, data["cgroup"], str(data["inode"]),
                                         str(data["original_weight"]), str(data.get("route_weight", AGGREGATE_SCOPE_WEIGHT)),
                                         "1" if data.get("suppressed", False) else "0")) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        logging.warning("scope route state persistence failed: %s", exc)

def scope_cpu_samples(candidates, previous, sampled_at, max_interval):
    """Convert per-scope usage counters to CPU%, rejecting stale/reset snapshots."""
    cpu_by_scope = {}
    next_previous = {}
    valid = True
    for unit, candidate in candidates.items():
        usage = candidate.get("usage_usec")
        old = previous.get(unit)
        next_previous[unit] = (candidate["inode"], usage, sampled_at)
        if usage is None or old is None or old[0] != candidate["inode"] or old[1] is None:
            valid = False
            continue
        elapsed = sampled_at - old[2]
        delta = usage - old[1]
        if delta < 0 or elapsed <= 0 or elapsed > max_interval:
            valid = False
            continue
        cpu_by_scope[unit] = (delta / elapsed) / 10000.0
    return cpu_by_scope, next_previous, valid

def apply_scope_route(state, unit, candidate, route_weight=AGGREGATE_SCOPE_WEIGHT):
    """Lower one verified workload scope's weight without moving its processes."""
    created = False
    if unit not in state:
        original = unit_cpu_weight(unit)
        if original is None:
            return False, "scope-weight-unavailable"
        if original == route_weight:
            return False, "scope-already-at-route-weight"
        state[unit] = {"cgroup": candidate["path"], "inode": candidate["inode"],
                       "original_weight": original, "route_weight": route_weight, "suppressed": False}
        created = True
    record = state[unit]
    if record.get("suppressed"):
        return False, "scope-weight-external-override"
    if record["inode"] != candidate["inode"] or record["cgroup"] != candidate["path"]:
        state.pop(unit, None)
        return False, "scope-recreated"
    if not created and unit_cpu_weight(unit) == route_weight:
        return False, "scope-already-routed"
    if not set_unit_cpu_weight(unit, route_weight, candidate["path"], candidate["inode"]):
        return False, "scope-weight-actuation-failed"
    return True, "aggregate-scope-weight"

def reconcile_scope_routes(state, candidates, should_remain_routed,
                           route_weight=AGGREGATE_SCOPE_WEIGHT):
    """Restore only our own unchanged scope weights; never overwrite external edits."""
    changed = False
    candidate_names = set(candidates)
    for unit, record in list(state.items()):
        candidate = candidates.get(unit)
        if candidate is None:
            # Scope exited or its cgroup was recreated. Never apply a stale
            # restoration to a different unit instance with the same name.
            state.pop(unit, None)
            changed = True
            continue
        if candidate["inode"] != record["inode"] or candidate["path"] != record["cgroup"]:
            state.pop(unit, None)
            changed = True
            continue
        current = unit_cpu_weight(unit)
        if current is None:
            continue
        if record.get("suppressed"):
            continue
        if should_remain_routed and unit in candidate_names:
            if current == record.get("route_weight", route_weight):
                continue
            record["suppressed"] = True
            logging.info("aggregate scope restore skipped unit=%s reason=external-weight-change current=%s", unit, current)
            changed = True
            continue
        if current == record.get("route_weight", route_weight):
            if set_unit_cpu_weight(unit, record["original_weight"], candidate["path"], candidate["inode"]):
                logging.info("aggregate scope restored unit=%s weight=%s", unit, record["original_weight"])
                state.pop(unit, None)
                changed = True
            else:
                # Keep the record so a later cycle can retry a failed restore.
                continue
        else:
            logging.info("aggregate scope restore skipped unit=%s reason=external-weight-change current=%s", unit, current)
            record["suppressed"] = True
            changed = True
    return changed

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


def update_aggregate_route_policy(state, cpu_by_pid, routable_pids, parent_quota,
                                  route_ratio=AGGREGATE_ROUTE_RATIO,
                                  clear_ratio=AGGREGATE_CLEAR_RATIO,
                                  min_worker_cpu=AGGREGATE_MIN_WORKER_CPU,
                                  sustained_samples=AGGREGATE_SUSTAINED_SAMPLES):
    """Propose one fair scope action only after sustained aggregate saturation.

    Keys may be stable scope names rather than PIDs. Scope accounting keeps
    descendant churn inside one cgroup identity. Invalid telemetry fails closed;
    the caller verifies the systemd unit and cgroup inode before actuation.
    """
    try:
        quota = float(parent_quota)
        route_ratio, clear_ratio, min_worker_cpu = map(float, (route_ratio, clear_ratio, min_worker_cpu))
        if (not math.isfinite(quota) or quota <= 0 or
                not all(math.isfinite(x) for x in (route_ratio, clear_ratio, min_worker_cpu)) or
                not 0 < route_ratio <= 1 or not 0 <= clear_ratio < route_ratio or
                min_worker_cpu <= 0 or not isinstance(sustained_samples, int) or sustained_samples < 1):
            raise ValueError
        valid = {}
        for pid, value in cpu_by_pid.items():
            number = float(value)
            if math.isfinite(number) and number >= min_worker_cpu:
                valid[pid] = number
    except (TypeError, ValueError, OverflowError):
        state.update(high_samples=0, low_samples=0, active=False)
        return None, "aggregate-telemetry-unavailable"

    ratio = sum(valid.values()) / quota
    if not state.get("active", False):
        state["high_samples"] = state.get("high_samples", 0) + 1 if len(valid) >= 2 and ratio >= route_ratio else 0
        state["low_samples"] = 0
        if state["high_samples"] < sustained_samples:
            return None, "aggregate-saturation-pending"
        state.update(active=True, low_samples=0, cursor=-1)
    elif ratio <= clear_ratio or len(valid) < 2:
        state["low_samples"] = state.get("low_samples", 0) + 1
        if state["low_samples"] >= sustained_samples:
            state.update(active=False, high_samples=0, low_samples=0)
            return None, "aggregate-saturation-cleared"
    else:
        state["low_samples"] = 0

    eligible = sorted(pid for pid in routable_pids if pid in valid)
    if not state.get("active", False) or not eligible:
        return None, "aggregate-no-routable-worker"
    # One route per controller sample, round-robin by stable candidate key.
    cursor = state.get("cursor")
    if cursor in eligible:
        candidate = eligible[(eligible.index(cursor) + 1) % len(eligible)]
    else:
        candidate = eligible[0]
    state["cursor"] = candidate
    return candidate, "aggregate-saturation"

def reconcile_aggregate_quota(state, previous_quota, observed_quota):
    """Reset saturation hysteresis on parent quota drift; never retain stale normalization."""
    try:
        observed = float(observed_quota)
        if not math.isfinite(observed) or observed <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        state.update(active=False, high_samples=0, low_samples=0, cursor=None)
        return None, previous_quota is not None
    if previous_quota is None:
        return observed, False
    try:
        previous = float(previous_quota)
    except (TypeError, ValueError, OverflowError):
        previous = float("nan")
    if not math.isfinite(previous) or not math.isclose(previous, observed, rel_tol=0.0, abs_tol=0.01):
        state.update(active=False, high_samples=0, low_samples=0, cursor=None)
        return observed, True
    return observed, False

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


def routing_config_error():
    """Reject malformed or unsafe core routing settings before entering the loop."""
    floats = (SAMPLE_SEC, CPU_THRESHOLD, LEARNING_RATE, ROUTE_THRESHOLD,
              COOLDOWN_SEC, RECOVERY_CPU_THRESHOLD, RECOVERY_DWELL_SEC,
              STATS_SAVE_INTERVAL_SEC, HISTORY_INTERVAL_SEC)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in floats):
        return "core routing settings must be finite numbers"
    if not 0.05 <= SAMPLE_SEC <= 60:
        return "sample interval must be in [0.05, 60] seconds"
    if not 0 < CPU_THRESHOLD <= 100 or not 0 <= RECOVERY_CPU_THRESHOLD <= 100:
        return "CPU thresholds must be in (0, 100] and [0, 100]"
    if not 0 < LEARNING_RATE <= 1:
        return "adaptive learning rate must be in (0, 1]"
    if not 0 <= ROUTE_THRESHOLD <= 1:
        return "adaptive route threshold must be in [0, 1]"
    if COOLDOWN_SEC < 0 or RECOVERY_DWELL_SEC < 0:
        return "cooldown and recovery dwell must be non-negative"
    if STATS_SAVE_INTERVAL_SEC < 0.1 or HISTORY_INTERVAL_SEC < 1:
        return "statistics interval must be at least 0.1s and history interval at least 1s"
    if not isinstance(SUSTAINED_SAMPLES, int) or SUSTAINED_SAMPLES < 1:
        return "sustained sample count must be a positive integer"
    if not isinstance(MIN_SAMPLES, int) or MIN_SAMPLES < 1:
        return "adaptive minimum sample count must be a positive integer"
    if not isinstance(RECOVERY_SAMPLES, int) or RECOVERY_SAMPLES < 1:
        return "recovery sample count must be a positive integer"
    if AGGREGATE_ROUTING and (not all(math.isfinite(value) for value in (AGGREGATE_ROUTE_RATIO, AGGREGATE_CLEAR_RATIO, AGGREGATE_MIN_WORKER_CPU)) or
            not 0 < AGGREGATE_ROUTE_RATIO <= 1 or not 0 <= AGGREGATE_CLEAR_RATIO < AGGREGATE_ROUTE_RATIO or
            AGGREGATE_MIN_WORKER_CPU <= 0 or not isinstance(AGGREGATE_SUSTAINED_SAMPLES, int) or AGGREGATE_SUSTAINED_SAMPLES < 1 or
            not isinstance(AGGREGATE_SCOPE_WEIGHT, int) or not 1 <= AGGREGATE_SCOPE_WEIGHT < 100):
        return "aggregate routing thresholds and sustained sample count are invalid"
    return None

def parse_cpu_pressure(text):
    """Parse a complete PSI `some` row; incomplete or inconsistent rows are unavailable."""
    found = None
    for line in text.splitlines():
        if not line.startswith("some "):
            continue
        if found is not None:
            return None
        parts = {}
        for field in line.split()[1:]:
            if "=" not in field:
                return None
            key, value = field.split("=", 1)
            if not key or key in parts:
                return None
            parts[key] = value
        if not {"avg10", "avg60", "avg300", "total"}.issubset(parts):
            return None
        try:
            averages = [float(parts[key]) for key in ("avg10", "avg60", "avg300")]
            total = int(parts["total"])
        except (TypeError, ValueError):
            return None
        if total < 0 or not all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in averages):
            return None
        found = averages[0] / 100.0
    return found


def cpu_pressure():
    try:
        with open("/proc/pressure/cpu") as f:
            return parse_cpu_pressure(f.read())
    except OSError:
        return None

def cgroup_cpu_stat(cgroup):
    if not cgroup:
        return None
    try:
        data = {}
        with open(os.path.join(cgroup, "cpu.stat")) as f:
            for line in f:
                fields = line.split()
                if len(fields) != 2:
                    return None
                key, value = fields
                parsed = int(value)
                if parsed < 0 or key in data:
                    return None
                data[key] = parsed
        # Treat incomplete snapshots as unavailable telemetry. In particular,
        # never infer zero usage/throttling from a partially readable cpu.stat.
        if not {"usage_usec", "nr_periods", "nr_throttled"}.issubset(data):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def cgroup_cpu_delta(current, previous):
    """Return monotonic cgroup counter deltas or None for reset/anomaly/missing data."""
    keys = ("usage_usec", "nr_periods", "nr_throttled")
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return None
    if any(key not in current or key not in previous for key in keys):
        return None
    deltas = {}
    for key in keys:
        now, before = current[key], previous[key]
        if not isinstance(now, int) or not isinstance(before, int) or now < 0 or before < 0:
            return None
        delta = now - before
        if delta < 0:
            return None
        deltas[key] = delta
    # A throttled-period count greater than elapsed quota periods indicates
    # a reset, mixed cgroup snapshots, or otherwise unusable telemetry.
    if deltas["nr_throttled"] > deltas["nr_periods"]:
        return None
    return deltas


def monotonic_counter_delta(current, previous):
    """Return a non-negative delta; never turn a counter reset into zero pressure."""
    if current is None or previous is None:
        return None
    try:
        current, previous = float(current), float(previous)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current) or not math.isfinite(previous) or current < previous:
        return None
    return current - previous

def unit_quota(unit):
    """Read systemd CPUQuotaPerSecUSec in its documented time units.

    systemd commonly renders quotas below 100% as e.g. ``500ms``. Treating
    every value ending in ``s`` as seconds also matches ``ms`` and then fails
    to parse the numeric prefix, which made otherwise valid effective quota
    readback look unavailable. Convert the property duration to a percentage
    of one CPU; infinity and unknown units are intentionally unsupported.
    """
    try:
        value = subprocess.check_output(
            ["systemctl", "--user", "show", unit, "--property=CPUQuotaPerSecUSec", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
        if value.endswith("ms"):
            seconds = float(value[:-2]) / 1000.0
        elif value.endswith("us"):
            seconds = float(value[:-2]) / 1_000_000.0
        elif value.endswith("s"):
            seconds = float(value[:-1])
        else:
            return None
        quota = seconds * 100.0
        return quota if math.isfinite(quota) and quota > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def reconcile_quota_observation(current_quota, observed_quota):
    """Reconcile cached quota with systemd's live property before deciding.

    A missing property invalidates the cached value. First observations and
    out-of-band changes establish a new baseline; neither is an actuation
    opportunity because the preceding counter interval may span a quota change.
    """
    if observed_quota is None:
        return None, "quota-unavailable"
    try:
        observed = float(observed_quota)
    except (TypeError, ValueError):
        return None, "quota-unavailable"
    if not math.isfinite(observed) or observed <= 0:
        return None, "quota-unavailable"
    if current_quota is None:
        return observed, "quota-baseline-reset"
    try:
        current = float(current_quota)
    except (TypeError, ValueError):
        return observed, "quota-baseline-reset"
    if not math.isfinite(current) or current != observed:
        return observed, "quota-changed-externally"
    return observed, "ok"

def schedstat_delay_ms():
    try:
        with open(f"/proc/{os.getpid()}/schedstat") as f:
            fields = f.read().split()
        return int(fields[1]) / 1_000_000.0
    except (OSError, ValueError, IndexError):
        return None

def set_unit_quota(unit, quota):
    try:
        quota = float(quota)
    except (TypeError, ValueError):
        return False
    if (not math.isfinite(quota) or not quota.is_integer() or
            not QUOTA_MIN <= quota <= QUOTA_MAX):
        return False
    try:
        subprocess.run(
            ["systemctl", "--user", "set-property", unit, f"CPUQuota={quota:.0f}%"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        # A successful systemctl exit confirms the request was accepted, not
        # that the effective unit property is the exact value the controller
        # expects. Read back the effective property before updating its cache.
        # If readback is missing or differs, the caller keeps the last confirmed
        # value and retries with bounded backoff; the next observation window
        # will reconcile any quota that did in fact change.
        observed = unit_quota(unit)
        if observed is None or not math.isclose(observed, quota, rel_tol=0.0, abs_tol=0.01):
            logging.warning(
                "quota actuation readback mismatch unit=%s requested=%.0f%% observed=%s",
                unit, quota, "unavailable" if observed is None else f"{observed:.2f}%",
            )
            return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def dynamic_quota_target(current, pressure, demand, throttled_ratio=0.0, sched_delay_ms=None):
    try:
        current = float(current)
    except (TypeError, ValueError):
        return current
    if not math.isfinite(current) or not current.is_integer() or not QUOTA_MIN <= current <= QUOTA_MAX:
        return current
    try:
        demand = float(demand)
        throttled_ratio = float(throttled_ratio)
    except (TypeError, ValueError):
        return current
    if (not math.isfinite(demand) or not 0.0 <= demand <= 1.0 or
            not math.isfinite(throttled_ratio) or not 0.0 <= throttled_ratio <= 1.0):
        return current
    if pressure is not None:
        try:
            pressure = float(pressure)
        except (TypeError, ValueError):
            pressure = None
        if pressure is not None and (not math.isfinite(pressure) or not 0.0 <= pressure <= 1.0):
            pressure = None
    if sched_delay_ms is not None:
        try:
            sched_delay_ms = float(sched_delay_ms)
        except (TypeError, ValueError):
            sched_delay_ms = None
        if sched_delay_ms is not None and (not math.isfinite(sched_delay_ms) or sched_delay_ms < 0.0):
            sched_delay_ms = None
    high_pressure = (
        (pressure is not None and pressure >= QUOTA_PRESSURE_HIGH) or
        (sched_delay_ms is not None and sched_delay_ms >= QUOTA_SCHED_DELAY_HIGH_MS)
    )
    low_pressure = (
        pressure is not None and pressure <= QUOTA_PRESSURE_LOW and
        sched_delay_ms is not None and sched_delay_ms <= QUOTA_SCHED_DELAY_LOW_MS
    )
    if high_pressure:
        return max(QUOTA_MIN, current - QUOTA_STEP)
    # Throttling can corroborate meaningful demand, but stale/recent throttle
    # counters alone must not raise quota after a workload becomes idle.
    if throttled_ratio >= QUOTA_THROTTLE_HIGH and demand >= 0.30 and low_pressure:
        return min(QUOTA_MAX, current + QUOTA_STEP)
    if low_pressure and demand >= 0.60:
        return min(QUOTA_MAX, current + QUOTA_STEP)
    if demand < 0.15 and low_pressure:
        return max(QUOTA_MIN, current - QUOTA_STEP)
    return current


def quota_config_error():
    """Validate settings used by production quota actuation."""
    values = (
        QUOTA_INTERVAL_SEC, QUOTA_MIN, QUOTA_MAX, QUOTA_STEP,
        QUOTA_PRESSURE_HIGH, QUOTA_PRESSURE_LOW,
        QUOTA_THROTTLE_HIGH, QUOTA_MIN_DWELL_SEC,
        QUOTA_SCHED_DELAY_HIGH_MS, QUOTA_SCHED_DELAY_LOW_MS,
    )
    if not all(math.isfinite(value) for value in values):
        return "quota controller settings must be finite numbers"
    if QUOTA_MIN <= 0 or QUOTA_MAX < QUOTA_MIN or QUOTA_STEP <= 0:
        return "quota bounds must be positive and max must be at least min"
    if not all(float(value).is_integer() for value in (QUOTA_MIN, QUOTA_MAX, QUOTA_STEP)):
        return "quota bounds and step must be whole percentage points"
    if QUOTA_INTERVAL_SEC < 0.25 or QUOTA_MIN_DWELL_SEC < 0:
        return "quota interval must be at least 0.25s and dwell must be non-negative"
    if not 0 <= QUOTA_PRESSURE_LOW < QUOTA_PRESSURE_HIGH <= 1:
        return "quota PSI thresholds must satisfy 0 <= low < high <= 1"
    if not 0 <= QUOTA_THROTTLE_HIGH <= 1:
        return "quota throttling threshold must be in [0, 1]"
    if QUOTA_SCHED_DELAY_LOW_MS < 0 or QUOTA_SCHED_DELAY_HIGH_MS <= QUOTA_SCHED_DELAY_LOW_MS:
        return "scheduler delay thresholds must satisfy 0 <= low < high"
    return None


def aimd_config_error():
    """Validate shadow-only AIMD knobs without disabling the production step controller."""
    if not math.isfinite(QUOTA_AIMD_DECREASE) or not math.isfinite(QUOTA_AIMD_ADD):
        return "AIMD settings must be finite numbers"
    if not 0 < QUOTA_AIMD_DECREASE < 1 or QUOTA_AIMD_ADD <= 0:
        return "AIMD decrease must be in (0, 1) and additive increase must be positive"
    return None


def quota_decision_from_sample(current_quota, previous_stat, current_stat, elapsed,
                               pressure, sched_delay_ms, route_demand=0.0):
    """Build one guarded controller decision from a contiguous telemetry window.

    Returns (decision, reason). Missing baselines, reset counters, invalid
    pressure intervals, and stale windows produce no actuation proposal.
    """
    try:
        quota_value = float(current_quota)
    except (TypeError, ValueError):
        return None, "quota-unavailable"
    if not math.isfinite(quota_value):
        return None, "quota-unavailable"
    if not QUOTA_MIN <= quota_value <= QUOTA_MAX:
        return None, "quota-out-of-bounds"
    if not quota_value.is_integer():
        return None, "quota-not-integral"
    if current_stat is None:
        return None, "cgroup-stat-unavailable"
    if previous_stat is None:
        return None, "counter-baseline-unavailable"
    deltas = cgroup_cpu_delta(current_stat, previous_stat)
    if deltas is None:
        return None, "counter-reset-or-anomaly"
    try:
        elapsed = float(elapsed)
        route_demand = float(route_demand)
    except (TypeError, ValueError):
        return None, "invalid-sample-metadata"
    max_sample_gap = max(QUOTA_INTERVAL_SEC * 3.0, SAMPLE_SEC * 3.0)
    if not math.isfinite(elapsed) or elapsed <= 0 or elapsed > max_sample_gap:
        return None, "stale-or-invalid-sample-window"
    if not math.isfinite(route_demand) or not 0.0 <= route_demand <= 1.0:
        return None, "invalid-route-demand"
    usage_pct = deltas["usage_usec"] / (elapsed * 10000.0)
    cgroup_demand = min(1.0, usage_pct / max(100.0, quota_value))
    demand = max(min(1.0, max(0.0, route_demand)), cgroup_demand)
    periods = deltas["nr_periods"]
    # A quota interval with no observed period progress cannot support a
    # throttling ratio. In particular, do not substitute 0.0 and let stale or
    # unsupported counters authorize an increase. Require a fresh interval.
    if periods <= 0:
        return None, "quota-period-counter-not-advancing"
    throttled_ratio = deltas["nr_throttled"] / periods
    if pressure is None and sched_delay_ms is None:
        return None, "pressure-signals-unavailable"
    target = dynamic_quota_target(quota_value, pressure, demand, throttled_ratio, sched_delay_ms)
    aimd_target = aimd_quota_target(quota_value, pressure, demand, throttled_ratio, sched_delay_ms)
    return {
        "target": target,
        "aimd_target": aimd_target,
        "demand": demand,
        "throttled_ratio": throttled_ratio,
        "usage_pct": usage_pct,
        "deltas": deltas,
    }, "ok"

def aimd_quota_target(current, pressure, demand, throttled_ratio=0.0, sched_delay_ms=None,
                      decrease=QUOTA_AIMD_DECREASE, additive_increase=QUOTA_AIMD_ADD):
    """Experimental AIMD quota proposal. Safe to call in shadow-only mode."""
    try:
        current = float(current)
        decrease = float(decrease)
        additive_increase = float(additive_increase)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current) or not math.isfinite(decrease) or not math.isfinite(additive_increase):
        return None
    if not current.is_integer() or not QUOTA_MIN <= current <= QUOTA_MAX:
        return None
    if not 0 < decrease < 1 or additive_increase <= 0:
        return None
    try:
        demand = float(demand)
        throttled_ratio = float(throttled_ratio)
    except (TypeError, ValueError):
        return current
    if (not math.isfinite(demand) or not 0.0 <= demand <= 1.0 or
            not math.isfinite(throttled_ratio) or not 0.0 <= throttled_ratio <= 1.0):
        return current
    if pressure is not None:
        try:
            pressure = float(pressure)
        except (TypeError, ValueError):
            pressure = None
        if pressure is not None and (not math.isfinite(pressure) or not 0.0 <= pressure <= 1.0):
            pressure = None
    if sched_delay_ms is not None:
        try:
            sched_delay_ms = float(sched_delay_ms)
        except (TypeError, ValueError):
            sched_delay_ms = None
        if sched_delay_ms is not None and (not math.isfinite(sched_delay_ms) or sched_delay_ms < 0):
            sched_delay_ms = None
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
        sched_delay_ms is not None and sched_delay_ms <= QUOTA_SCHED_DELAY_LOW_MS
    )
    if high_pressure:
        return max(QUOTA_MIN, current * decrease)
    if low_pressure and (demand >= 0.60 or
                         (throttled_ratio >= QUOTA_THROTTLE_HIGH and demand >= 0.30)):
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
                cpu, reason, source, target, cmd = data[:5]
                starttime = data[5] if len(data) > 5 else None
                fields = (
                    str(pid), f"{cpu:.1f}", reason, source, target,
                    cmd.replace("\t", " ").replace("\n", " "),
                    "" if starttime is None else str(starttime),
                )
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
                fields = line.rstrip("\n").split("\t", 6)
                if len(fields) not in (6, 7):
                    continue
                try:
                    pid, cpu = int(fields[0]), float(fields[1])
                except ValueError:
                    continue
                if pid <= 0 or not math.isfinite(cpu):
                    continue
                starttime = None
                if len(fields) == 7 and fields[6]:
                    try:
                        starttime = int(fields[6])
                    except ValueError:
                        continue
                    if starttime < 0:
                        continue
                routes[pid] = (cpu, fields[2], fields[3], fields[4], fields[5], starttime)
    except OSError:
        pass
    return routes

def main():
    global SHADOW_ENABLED, QUOTA_DYNAMIC, QUOTA_AIMD_SHADOW
    scope_route_state = load_scope_state()
    routing_error = routing_config_error()
    if routing_error:
        parent = user_cgroup(PARENT_UNIT)
        candidates = workload_scope_candidates(parent, scope_route_state.keys()) if parent else {}
        if not SCOPE_DISCOVERY_ERROR and reconcile_scope_routes(scope_route_state, candidates, False):
            write_scope_state(scope_route_state)
        logging.error("routing disabled due to invalid configuration: %s", routing_error)
        return
    prev={}
    hot={}
    shadow_policy={}
    aggregate_policy = {"active": False, "high_samples": 0, "low_samples": 0, "cursor": None}
    scope_usage_previous = {}
    aggregate_parent_quota = None
    aggregate_quota_checked_at = 0.0
    moved=set()
    routes=load_state()
    recovery_hot={}
    recovery_since={}
    route_prev={}
    stats=load_stats() if ADAPTIVE else {}
    last_move={}
    last_quota_change=0.0
    quota_dwell_until=0.0
    quota_retry_after=0.0
    quota_retry_backoff=2.0
    quota_route_cpu_sum=0.0
    quota_route_cpu_samples=0
    last_stats_save=0.0
    last_history_save=time.monotonic()
    route_events=0
    recovery_events=0
    quota_changes=0
    quota_cgroup_path=None
    quota_telemetry_status=None
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
    quota_sched_sample_time = time.monotonic()
    quota_sched_observation = None
    logging.info('started threshold=%.0f%% sustained=%ss recovery<%.0f%% adaptive=%s shadow=%s', CPU_THRESHOLD, SAMPLE_SEC*SUSTAINED_SAMPLES, RECOVERY_CPU_THRESHOLD, ADAPTIVE, SHADOW_ENABLED)
    if AGGREGATE_ROUTING:
        logging.warning('aggregate scope routing OPT-IN enabled: aggregate>=%.0f%% for %s samples, per-scope>=%.0f%%, clear<=%.0f%%; CPUWeight=%s; usage/quota telemetry required',
                        AGGREGATE_ROUTE_RATIO * 100, AGGREGATE_SUSTAINED_SAMPLES,
                        AGGREGATE_MIN_WORKER_CPU, AGGREGATE_CLEAR_RATIO * 100, AGGREGATE_SCOPE_WEIGHT)
    if SHADOW_ENABLED:
        shadow_error = shadow_config_error()
        if shadow_error:
            logging.error("shadow mode disabled due to invalid configuration: %s", shadow_error)
            SHADOW_ENABLED = False
    if QUOTA_DYNAMIC or QUOTA_AIMD_SHADOW:
        quota_error = quota_config_error()
        if quota_error:
            logging.error("quota control disabled due to invalid configuration: %s", quota_error)
            QUOTA_DYNAMIC = False
            QUOTA_AIMD_SHADOW = False
            quota_observe = False
        elif QUOTA_AIMD_SHADOW:
            aimd_error = aimd_config_error()
            if aimd_error:
                logging.error("AIMD shadow disabled due to invalid configuration: %s", aimd_error)
                QUOTA_AIMD_SHADOW = False
                quota_observe = QUOTA_DYNAMIC
    if SHADOW_ENABLED:
        logging.info('shadow policy=ewma-hysteresis alpha=%.2f route>=%.1f%% clear<=%.1f%% sustained_samples=%s recovery_samples=%s recovery_dwell=%.1fs; actions are log-only',
                     SHADOW_EWMA_ALPHA, SHADOW_ROUTE_THRESHOLD, SHADOW_CLEAR_THRESHOLD,
                     SHADOW_SUSTAINED_SAMPLES, SHADOW_RECOVERY_SAMPLES, SHADOW_RECOVERY_DWELL_SEC)
    if QUOTA_AIMD_SHADOW:
        logging.info('quota policy=aimd-shadow decrease=%.3f additive_increase=%.1f; proposals are log-only',
                     QUOTA_AIMD_DECREASE, QUOTA_AIMD_ADD)
    ensure_workload_units()
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    last_unit_start_attempt=time.monotonic()
    missing_cgroups = None
    while not STOP_REQUESTED:
        gateway_cgroup = user_cgroup(PARENT_UNIT)
        heavy_cgroup = user_cgroup(HEAVY_UNIT)
        if gateway_cgroup and not os.path.isdir(gateway_cgroup):
            gateway_cgroup = None
        if heavy_cgroup and not os.path.isdir(heavy_cgroup):
            heavy_cgroup = None
        recovery_cgroup = user_cgroup(RECOVERY_UNIT)
        if recovery_cgroup and not os.path.isdir(recovery_cgroup):
            recovery_cgroup = None
        if not gateway_cgroup or not heavy_cgroup or not recovery_cgroup:
            missing = tuple(unit for unit, path in ((PARENT_UNIT, gateway_cgroup), (RECOVERY_UNIT, recovery_cgroup), (HEAVY_UNIT, heavy_cgroup)) if not path)
            if missing != missing_cgroups:
                logging.warning("routing paused: missing ControlGroup for %s; check user systemd manager and slice units", ", ".join(missing))
                missing_cgroups = missing
                ensure_workload_units()
                last_unit_start_attempt = time.monotonic()
            if quota_observe:
                # Do not compare a future cgroup sample with counters from a
                # disappeared cgroup, even if systemd later reuses its path.
                quota_cgroup_path = None
                quota_stat_prev = None
                quota_sched_prev = None
                quota_sched_sample_time = time.monotonic()
                quota_sched_observation = None
                quota_stat_time = time.monotonic()
                quota_route_cpu_sum = 0.0
                quota_route_cpu_samples = 0
            if time.monotonic() - last_unit_start_attempt >= 30.0:
                ensure_workload_units()
                last_unit_start_attempt = time.monotonic()
            time.sleep(SAMPLE_SEC)
            continue
        if missing_cgroups is not None:
            logging.info("routing resumed; workload cgroups are available")
            missing_cgroups = None
        quota_path_changed = False
        if quota_observe and heavy_cgroup != quota_cgroup_path:
            quota_cgroup_path = heavy_cgroup
            quota_stat_prev = cgroup_cpu_stat(heavy_cgroup)
            quota_stat_time = time.monotonic()
            quota_sched_prev = schedstat_delay_ms()
            quota_sched_sample_time = time.monotonic()
            quota_sched_observation = None
            observed_quota = unit_quota(HEAVY_UNIT)
            current_quota = observed_quota
            quota_path_changed = True
            quota_route_cpu_sum = 0.0
            quota_route_cpu_samples = 0
            history_stat_prev = quota_stat_prev
            history_stat_time = quota_stat_time
            history_sched_prev = quota_sched_prev
            logging.info("quota telemetry baseline reset for cgroup=%s", heavy_cgroup)
        if not history_proc_prev:
            history_proc_prev, _, _, _ = history_cpu_attribution({}, 1.0, gateway_cgroup, heavy_cgroup)
            history_proc_time = time.monotonic()
        now={}
        aggregate_cpu = {}
        if AGGREGATE_ROUTING and time.monotonic() - aggregate_quota_checked_at >= 5.0:
            observed_parent_quota = unit_quota(PARENT_UNIT)
            aggregate_parent_quota, quota_drift = reconcile_aggregate_quota(
                aggregate_policy, aggregate_parent_quota, observed_parent_quota)
            if quota_drift:
                logging.warning("aggregate parent quota changed or became unavailable; resetting scope saturation state observed=%s",
                                observed_parent_quota)
            aggregate_quota_checked_at = time.monotonic()
        if gateway_cgroup and heavy_cgroup:
            scope_candidates = workload_scope_candidates(gateway_cgroup, scope_route_state.keys()) if (AGGREGATE_ROUTING or scope_route_state) else {}
            scope_sampled_at = time.monotonic()
            scope_cpu, scope_usage_previous, scope_telemetry_valid = scope_cpu_samples(
                scope_candidates, scope_usage_previous, scope_sampled_at, max(SAMPLE_SEC * 3.0, 10.0))
            scope_telemetry_valid = scope_telemetry_valid and not SCOPE_DISCOVERY_OVERFLOW and not SCOPE_DISCOVERY_ERROR
            if SCOPE_DISCOVERY_OVERFLOW:
                logging.error("aggregate scope discovery exceeded maximum=%s; no new scope routing until count falls", AGGREGATE_MAX_SCOPES)
            if SCOPE_DISCOVERY_ERROR:
                logging.warning("aggregate scope discovery failed transiently; preserving persisted scope-route identities")
            if scope_route_state and not SCOPE_DISCOVERY_ERROR and reconcile_scope_routes(
                    scope_route_state, scope_candidates,
                    AGGREGATE_ROUTING and aggregate_policy.get("active", False) and scope_telemetry_valid and aggregate_parent_quota is not None):
                write_scope_state(scope_route_state)
            protected_pids = cgroup_tree_pids(gateway_cgroup)
            heavy_pids = cgroup_tree_pids(heavy_cgroup) if (SHADOW_ENABLED or AGGREGATE_ROUTING) else set()
            if AGGREGATE_ROUTING:
                # The benchmark/production hierarchy may nest heavy under the
                # parent; don't classify already-routed descendants as routable.
                protected_pids -= heavy_pids
            for pid in protected_pids | heavy_pids:
                cmd=cmdline(pid)
                if not cmd or is_excluded(cmd): continue
                process_stat = proc_stat(pid)
                if process_stat is None:
                    continue
                _,ut,st,starttime=process_stat
                sampled_at = time.monotonic()
                now[pid]=(ut+st,cmd,starttime,sampled_at)
                if pid in prev:
                    if len(prev[pid]) < 3 or prev[pid][2] != starttime or prev[pid][1] != cmd:
                        # PID reuse and exec-style command changes both reset
                        # classification state. Persisted route identity still
                        # uses starttime, so exec cannot orphan a routed process.
                        hot.pop(pid, None)
                        shadow_policy.pop(pid, None)
                        last_move.pop(pid, None)
                        continue
                    dticks=ut+st-prev[pid][0]
                    elapsed_cpu = sampled_at - prev[pid][3] if len(prev[pid]) > 3 else SAMPLE_SEC
                    if dticks < 0 or elapsed_cpu <= 0 or elapsed_cpu > max(SAMPLE_SEC * 3.0, 10.0):
                        hot[pid] = 0
                        shadow_policy.pop(pid, None)
                        continue
                    # Linux USER_HZ is normally 100. Normalize to % of one CPU.
                    cpu=(dticks / max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK'])) / elapsed_cpu)*100
                    if pid in protected_pids or pid in routes:
                        aggregate_cpu[pid] = cpu
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
                            # Recheck identity and location immediately before
                            # the PID-based cgroup write to narrow the reuse race.
                            before_move = proc_stat(pid)
                            if (pid not in moved and before_move is not None and
                                    before_move[3] == starttime and in_cgroup_tree(pid, gateway_cgroup) and
                                    move(pid, heavy_cgroup, reason)):
                                after_move = proc_stat(pid)
                                if after_move is None or after_move[3] != starttime:
                                    logging.warning("process identity changed during route pid=%s; refusing to track stale PID", pid)
                                    continue
                                moved.add(pid)
                                routes[pid] = (cpu, reason, source_cgroup, heavy_cgroup, cmd, starttime)
                                last_move[pid] = time.monotonic()
                                route_events += 1
                                state_dirty = True
                                logging.info('route decision=accepted pid=%s cpu=%.1f%% reason=%s threshold=%.1f%% sustained_samples=%s cmd=%s',
                                             pid, cpu, reason, CPU_THRESHOLD, hot.get(pid, 0), cmd[:180])
            current_route_cpu = {}
            for pid in list(routes):
                membership = routed_process_membership(pid, heavy_cgroup)
                if membership in ("gone", "outside"):
                    moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                    route_prev.pop(pid, None)
                    continue
                if membership == "unknown":
                    # Keep the persisted route identity while /proc cgroup
                    # membership is unreadable. Missing membership cannot be
                    # treated as proof of exit or recovery; reset CPU baselines
                    # so the first valid post-gap sample only re-establishes a
                    # baseline and cannot spuriously trigger an idle recovery.
                    route_prev.pop(pid, None)
                    recovery_hot[pid] = 0
                    recovery_since.pop(pid, None)
                    continue
                process_stat = proc_stat(pid)
                if process_stat is None:
                    # Incomplete /proc reads are not evidence that a process
                    # is idle; defer recovery until a valid sample returns.
                    route_prev.pop(pid, None)
                    continue
                _, ut, st, starttime = process_stat
                sampled_at = time.monotonic()
                cmd = cmdline(pid)
                expected_starttime = routes[pid][5] if len(routes[pid]) > 5 else None
                if expected_starttime is None:
                    logging.warning("dropping legacy route state without process starttime pid=%s; refusing unsafe recovery", pid)
                    moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                    route_prev.pop(pid, None)
                    continue
                if not route_process_matches(routes[pid], cmd, starttime):
                    logging.info('dropping stale route pid=%s after process identity changed', pid)
                    moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                    route_prev.pop(pid, None)
                    continue
                moved.add(pid)
                prev_route = route_prev.get(pid)
                current_route_cpu[pid] = 0.0
                if prev_route and prev_route[0] == starttime:
                    dticks = ut + st - prev_route[1]
                    elapsed_cpu = sampled_at - prev_route[2]
                    if dticks < 0 or elapsed_cpu <= 0 or elapsed_cpu > max(SAMPLE_SEC * 3.0, 10.0):
                        # A counter discontinuity is not an idle sample.
                        recovery_hot[pid] = 0
                        recovery_since.pop(pid, None)
                        current_route_cpu.pop(pid, None)
                        continue
                    cpu = (dticks / max(1, os.sysconf(os.sysconf_names['SC_CLK_TCK'])) / elapsed_cpu) * 100
                    aggregate_cpu[pid] = cpu
                    current_route_cpu[pid] = max(0.0, cpu)
                    if cpu < RECOVERY_CPU_THRESHOLD:
                        recovery_hot[pid] = recovery_hot.get(pid, 0) + 1
                        recovery_since.setdefault(pid, time.monotonic())
                    else:
                        recovery_hot[pid] = 0; recovery_since.pop(pid, None)
                    if (recovery_hot.get(pid, 0) >= RECOVERY_SAMPLES and
                            time.monotonic() - recovery_since.get(pid, time.monotonic()) >= RECOVERY_DWELL_SEC):
                        source = routes[pid][2] if os.path.isdir(routes[pid][2]) else recovery_cgroup
                        before_recovery = proc_stat(pid)
                        if before_recovery is not None and before_recovery[3] == starttime and move(pid, source, "recovery"):
                            after_recovery = proc_stat(pid)
                            if after_recovery is None or after_recovery[3] != starttime:
                                logging.warning("process identity changed during recovery pid=%s; route state will be revalidated", pid)
                                continue
                            logging.info('unrouted pid=%s cpu=%.1f%% recovery', pid, cpu)
                            recovery_events += 1
                            moved.discard(pid); routes.pop(pid, None); recovery_hot.pop(pid, None); recovery_since.pop(pid, None); state_dirty = True
                            route_prev.pop(pid, None)
            if AGGREGATE_ROUTING:
                aggregate_quota_sample = aggregate_parent_quota if scope_telemetry_valid else None
                routable_scopes = set(scope_candidates) - {
                    unit for unit, record in scope_route_state.items() if record.get("suppressed")
                }
                aggregate_scope, aggregate_reason = update_aggregate_route_policy(
                    aggregate_policy, scope_cpu, routable_scopes, aggregate_quota_sample)
                if aggregate_policy.get("high_samples", 0) in (1, AGGREGATE_SUSTAINED_SAMPLES) or aggregate_reason in ("aggregate-telemetry-unavailable", "aggregate-saturation-cleared"):
                    logging.info('aggregate scope observation status=%s active=%s eligible_scopes=%s observed_scopes=%s demand_cpu=%.1f%% parent_quota=%s high_samples=%s low_samples=%s telemetry_valid=%s',
                                 aggregate_reason, aggregate_policy.get("active"),
                                 sum(1 for name in scope_candidates if scope_cpu.get(name, 0.0) >= AGGREGATE_MIN_WORKER_CPU),
                                 sum(1 for value in scope_cpu.values() if value >= AGGREGATE_MIN_WORKER_CPU),
                                 sum(value for value in scope_cpu.values() if value >= AGGREGATE_MIN_WORKER_CPU),
                                 aggregate_parent_quota, aggregate_policy.get("high_samples"), aggregate_policy.get("low_samples"), scope_telemetry_valid)
                scope_state_changed = False
                if not SCOPE_DISCOVERY_ERROR:
                    scope_state_changed = reconcile_scope_routes(
                        scope_route_state, scope_candidates,
                        aggregate_policy.get("active", False) and scope_telemetry_valid and aggregate_parent_quota is not None)
                if aggregate_scope is not None and aggregate_scope in scope_candidates:
                    ok, actuation_status = apply_scope_route(scope_route_state, aggregate_scope, scope_candidates[aggregate_scope])
                    logging.info('aggregate scope decision=%s unit=%s cpu=%.1f%% weight=%s status=%s inode=%s',
                                 aggregate_reason, aggregate_scope, scope_cpu.get(aggregate_scope, 0.0),
                                 AGGREGATE_SCOPE_WEIGHT, actuation_status, scope_candidates[aggregate_scope]["inode"])
                    scope_state_changed = scope_state_changed or ok
                elif aggregate_reason not in ("aggregate-saturation-pending", "aggregate-no-routable-worker"):
                    logging.info('aggregate scope state=%s active=%s high_samples=%s low_samples=%s quota=%s',
                                 aggregate_reason, aggregate_policy.get("active"), aggregate_policy.get("high_samples"),
                                 aggregate_policy.get("low_samples"), aggregate_quota_sample)
                if scope_state_changed:
                    write_scope_state(scope_route_state)
                elif aggregate_reason not in ("aggregate-saturation-pending", "aggregate-no-routable-worker"):
                    logging.info("aggregate scope route not applied reason=%s", aggregate_reason)
        # Average routed CPU across the entire interval between quota samples.
        # One stale hot process sample must not dominate the controller after
        # the workload has already gone idle.
        quota_route_cpu_sum += sum(current_route_cpu.values())
        quota_route_cpu_samples += 1
        # Sample cumulative runqueue delay every router iteration, including
        # quota dwell windows, so a 10-second dwell cannot look like one sudden
        # high-pressure event. Normalize to the configured quota interval.
        if quota_observe:
            scheduler_sample_now = schedstat_delay_ms()
            scheduler_sample_time = time.monotonic()
            scheduler_delta = monotonic_counter_delta(scheduler_sample_now, quota_sched_prev)
            scheduler_elapsed = scheduler_sample_time - quota_sched_sample_time
            if (scheduler_delta is not None and
                    max(0.1, SAMPLE_SEC * 0.5) <= scheduler_elapsed <= max(10.0, SAMPLE_SEC * 3.0)):
                quota_sched_observation = scheduler_delta * QUOTA_INTERVAL_SEC / scheduler_elapsed
            else:
                quota_sched_observation = None
            quota_sched_prev = scheduler_sample_now
            quota_sched_sample_time = scheduler_sample_time

        if quota_observe and heavy_cgroup:
            now_mono = time.monotonic()
            if now_mono >= quota_dwell_until and now_mono - last_quota_change >= QUOTA_INTERVAL_SEC:
                pressure = cpu_pressure()
                after_stat = cgroup_cpu_stat(heavy_cgroup)
                sched_delay_ms = quota_sched_observation
                now_stat_time = time.monotonic()
                observed_quota = unit_quota(HEAVY_UNIT)
                reconciled_quota, quota_observation_status = reconcile_quota_observation(
                    current_quota, observed_quota,
                )
                current_quota = reconciled_quota
                route_cpu_average = quota_route_cpu_sum / quota_route_cpu_samples if quota_route_cpu_samples else 0.0
                route_demand = min(1.0, max(0.0, route_cpu_average) / max(100.0, current_quota or QUOTA_MIN))
                quota_stat_elapsed = now_stat_time - quota_stat_time
                if quota_observation_status != "ok":
                    decision, decision_status = None, quota_observation_status
                    # Do not compare a window spanning an unavailable or changed
                    # quota property. The next complete interval is the first
                    # eligible controller observation.
                    quota_stat_prev = after_stat
                    quota_stat_time = now_stat_time
                    if quota_observation_status == "quota-changed-externally":
                        logging.warning("quota property changed outside controller; adopting observed quota=%.1f%% and resetting dwell/baseline", current_quota)
                        last_quota_change = now_mono
                        quota_dwell_until = now_mono + QUOTA_MIN_DWELL_SEC
                elif quota_path_changed:
                    decision, decision_status = None, "cgroup-changed"
                else:
                    decision, decision_status = quota_decision_from_sample(
                        current_quota, quota_stat_prev, after_stat, quota_stat_elapsed,
                        pressure, sched_delay_ms, route_demand,
                    )
                # Always re-seed from the latest read. A missing or reset sample
                # therefore costs one complete good interval before actuation resumes.
                quota_stat_prev = after_stat
                quota_stat_time = now_stat_time
                quota_route_cpu_sum = 0.0
                quota_route_cpu_samples = 0
                if decision_status != quota_telemetry_status:
                    if decision_status == "ok":
                        logging.info("quota telemetry recovered; controller decisions resumed")
                    else:
                        logging.warning("quota decision skipped reason=%s", decision_status)
                    quota_telemetry_status = decision_status
                if decision is not None:
                    target = decision["target"]
                    demand = decision["demand"]
                    throttled_ratio = decision["throttled_ratio"]
                    aimd_target = decision["aimd_target"]
                    if QUOTA_AIMD_SHADOW and aimd_target is not None and aimd_target != current_quota:
                        logging.info('quota shadow=aimd current=%.0f%% proposed=%.0f%% pressure=%s sched_delay=%sms demand=%.2f throttled=%.3f action=log-only',
                                     current_quota, aimd_target,
                                     'n/a' if pressure is None else f'{pressure:.3f}',
                                     'n/a' if sched_delay_ms is None else f'{sched_delay_ms:.2f}',
                                     demand, throttled_ratio)
                    if QUOTA_DYNAMIC and target != current_quota and now_mono >= quota_retry_after:
                        if set_unit_quota(HEAVY_UNIT, target):
                            logging.info('quota changed unit=%s old=%.0f%% new=%.0f%% pressure=%s sched_delay=%sms demand=%.2f',
                                         HEAVY_UNIT, current_quota, target,
                                         'n/a' if pressure is None else f'{pressure:.3f}',
                                         'n/a' if sched_delay_ms is None else f'{sched_delay_ms:.2f}', demand)
                            current_quota = target
                            quota_changes += 1
                            last_quota_change = now_mono
                            quota_dwell_until = now_mono + QUOTA_MIN_DWELL_SEC
                            quota_retry_after = 0.0
                            quota_retry_backoff = 2.0
                        else:
                            logging.warning("quota change failed unit=%s requested=%.0f%%; retaining last confirmed quota=%.0f%%",
                                            HEAVY_UNIT, target, current_quota)
                            quota_retry_after = now_mono + quota_retry_backoff
                            quota_retry_backoff = min(30.0, quota_retry_backoff * 2.0)
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
                process_stat = proc_stat(pid)
                if process_stat is not None and process_stat[3] == routes[pid][5]:
                    route_prev[pid] = (process_stat[3], process_stat[1] + process_stat[2], time.monotonic())
                else:
                    route_prev.pop(pid, None)
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
            stat_delta = cgroup_cpu_delta(hstat, history_stat_prev)
            if stat_delta is not None and elapsed <= max(HISTORY_INTERVAL_SEC * 3.0, QUOTA_INTERVAL_SEC * 3.0, SAMPLE_SEC * 3.0):
                usage_pct = stat_delta["usage_usec"] / (elapsed * 10000.0)
                periods = stat_delta["nr_periods"]
                throttle_ratio = stat_delta["nr_throttled"] / periods if periods else 0.0
            history_sched_now = schedstat_delay_ms() if quota_observe else None
            history_sched_delay = monotonic_counter_delta(history_sched_now, history_sched_prev)
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
        sleep_until = time.monotonic() + SAMPLE_SEC
        while not STOP_REQUESTED and time.monotonic() < sleep_until:
            time.sleep(min(0.25, max(0.0, sleep_until - time.monotonic())))

    # A normal systemd stop restores only weights this controller previously
    # changed and only for the same live cgroup inode. On SIGKILL, the persisted
    # journal is reconciled by the next service start.
    parent = user_cgroup(PARENT_UNIT)
    candidates = workload_scope_candidates(parent, scope_route_state.keys()) if parent else {}
    if not SCOPE_DISCOVERY_ERROR and reconcile_scope_routes(scope_route_state, candidates, False):
        write_scope_state(scope_route_state)
    logging.info("shutdown complete; aggregate scope routes reconciled")

if __name__=='__main__': main()
