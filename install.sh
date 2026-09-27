#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
USER_SYSTEMD="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
DATA="$HOME/.local/share/linux-workload-guard"
mkdir -p "$USER_SYSTEMD" "$BIN" "$DATA"
install -m 0755 "$ROOT/bin/workload-router.py" "$BIN/workload-router.py"
install -m 0755 "$ROOT/bin/run-workload" "$BIN/run-workload"
install -m 0755 "$ROOT/bin/workload-guard" "$BIN/workload-guard"
install -m 0644 "$ROOT/VERSION" "$DATA/VERSION"
install -m 0755 "$ROOT/bin/limit-browser-automation-cgroup" "$BIN/limit-browser-automation-cgroup"
install -m 0644 "$ROOT/systemd/protected-workload.slice" "$USER_SYSTEMD/protected-workload.slice"
install -m 0644 "$ROOT/systemd/heavy-workload.slice" "$USER_SYSTEMD/heavy-workload.slice"
install -m 0644 "$ROOT/systemd/browser-automation.slice" "$USER_SYSTEMD/browser-automation.slice"
install -m 0644 "$ROOT/systemd/browser-automation-budget.service" "$USER_SYSTEMD/browser-automation-budget.service"
install -m 0644 "$ROOT/systemd/browser-automation-budget.timer" "$USER_SYSTEMD/browser-automation-budget.timer"
install -m 0644 "$ROOT/systemd/workload-router.service" "$USER_SYSTEMD/workload-router.service"
systemctl --user daemon-reload
systemctl --user enable --now protected-workload.slice heavy-workload.slice
systemctl --user enable --now browser-automation-budget.timer
systemctl --user enable --now workload-router.service
printf '%s\n' 'Linux Workload Guard installed.'
