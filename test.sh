#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m py_compile "$ROOT/bin/openclaw-heavy-task-router.py"
python3 - "$ROOT/bin/openclaw-heavy-task-router.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("router", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for command in ("npm run build", "cargo test", "pytest -q", "ffmpeg -i in out"):
    assert mod.command_heavy(command), command
assert not mod.command_heavy("python app.py")
assert mod.is_excluded("python /home/x/openclaw-heavy-task-router.py")
assert mod.is_excluded("/usr/bin/google-chrome-chatgpt")
print("unit checks: ok")
PY
printf '%s\n' 'tests: ok'
