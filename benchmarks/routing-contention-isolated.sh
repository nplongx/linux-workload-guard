#!/usr/bin/env bash
# Run routing contention with the existing dynamic quota controller disabled temporarily.
# The EnvironmentFile contents and initial service state are restored on exit.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/linux-workload-guard/workload-guard.env"
if [[ ! -f "$CONFIG" ]]; then
  echo "config not found: $CONFIG" >&2
  exit 2
fi
BACKUP=$(mktemp)
cp -p "$CONFIG" "$BACKUP"
RESTORED=0
SERVICE_WAS_ACTIVE=0
if systemctl --user is-active --quiet workload-router.service; then
  SERVICE_WAS_ACTIVE=1
fi
restore() {
  if [[ "$RESTORED" -eq 0 ]]; then
    cat "$BACKUP" > "$CONFIG"
    chmod --reference="$BACKUP" "$CONFIG"
    touch "$CONFIG"
    RESTORED=1
    if [[ "$SERVICE_WAS_ACTIVE" -eq 1 ]]; then
      systemctl --user restart workload-router.service >/dev/null 2>&1 || true
    else
      systemctl --user stop workload-router.service >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$BACKUP"
}
trap restore EXIT INT TERM HUP
python3 - "$CONFIG" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
pattern = r'(?m)^\s*WORKLOAD_GUARD_DYNAMIC_QUOTA\s*=.*$'
if re.search(pattern, s):
    s = re.sub(pattern, 'WORKLOAD_GUARD_DYNAMIC_QUOTA=false', s)
else:
    s += '\nWORKLOAD_GUARD_DYNAMIC_QUOTA=false\n'
p.write_text(s)
PY
if [[ "$SERVICE_WAS_ACTIVE" -eq 1 ]]; then
  systemctl --user restart workload-router.service
else
  systemctl --user stop workload-router.service >/dev/null 2>&1 || true
fi
printf 'Temporary benchmark setting: WORKLOAD_GUARD_DYNAMIC_QUOTA=false (will restore original config and service state)\n'
cd "$ROOT"
python3 benchmarks/routing-contention.py
