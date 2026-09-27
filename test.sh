#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m py_compile "$ROOT/bin/workload-router.py"
python3 - "$ROOT/bin/workload-router.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("router", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for command in ("npm run build", "cargo test", "pytest -q", "ffmpeg -i in out"):
    assert mod.command_heavy(command), command
assert not mod.command_heavy("python app.py")
assert mod.is_excluded("python /home/x/workload-router.py")
assert not mod.is_excluded("/usr/bin/google-chrome")
assert mod.in_cgroup_tree
assert mod.command_class("npm run build") == "npm:run"
assert mod.adaptive_score(95, {"samples": 10, "mean": 30, "variance": 25}) is not None
assert mod.adaptive_score(80, {"samples": 2, "mean": 30, "variance": 25}) is None
assert mod.dynamic_quota_target(200, 0.30, 0.9) == 150
assert mod.dynamic_quota_target(200, 0.01, 0.9) == 250
assert mod.dynamic_quota_target(200, 0.01, 0.2) == 150
assert mod.PARENT_UNIT == "protected-workload.slice"
assert mod.HEAVY_UNIT == "heavy-workload.slice"
print("unit checks: ok")
PY
test "$("$ROOT/bin/workload-guard" version)" = "0.5.0"
grep -q '^EnvironmentFile=-%h/.config/linux-workload-guard/workload-guard.env$' "$ROOT/systemd/workload-router.service"
grep -q 'CONFIG_DIR="$HOME/.config/linux-workload-guard"' "$ROOT/install.sh"
printf '%s\n' 'tests: ok'
