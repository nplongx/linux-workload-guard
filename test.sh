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
assert mod.PARENT_UNIT == "protected-workload.slice"
assert mod.HEAVY_UNIT == "heavy-workload.slice"
print("unit checks: ok")
PY
test "$("$ROOT/bin/workload-guard" version)" = "0.1.1"
grep -q '^EnvironmentFile=-%h/.config/linux-workload-guard/workload-guard.env$' "$ROOT/systemd/workload-router.service"
grep -q 'CONFIG_DIR="$HOME/.config/linux-workload-guard"' "$ROOT/install.sh"
printf '%s\n' 'tests: ok'
