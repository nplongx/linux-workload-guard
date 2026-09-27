#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
USER_SYSTEMD="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
mkdir -p "$USER_SYSTEMD/openclaw-gateway.service.d" "$BIN"
install -m 0755 "$ROOT/bin/openclaw-heavy-task-router.py" "$BIN/openclaw-heavy-task-router.py"
install -m 0755 "$ROOT/bin/run-task" "$BIN/run-task"
install -m 0755 "$ROOT/bin/limit-chatgpt-chrome-cgroup" "$BIN/limit-chatgpt-chrome-cgroup"
install -m 0644 "$ROOT/systemd/terminal-heavy.slice" "$USER_SYSTEMD/terminal-heavy.slice"
install -m 0644 "$ROOT/systemd/chatgpt-chrome.slice" "$USER_SYSTEMD/chatgpt-chrome.slice"
install -m 0644 "$ROOT/systemd/chatgpt-chrome-budget.service" "$USER_SYSTEMD/chatgpt-chrome-budget.service"
install -m 0644 "$ROOT/systemd/chatgpt-chrome-budget.timer" "$USER_SYSTEMD/chatgpt-chrome-budget.timer"
install -m 0644 "$ROOT/systemd/openclaw-heavy-task-router.service" "$USER_SYSTEMD/openclaw-heavy-task-router.service"
install -m 0644 "$ROOT/systemd/openclaw-gateway-cpu-budget.conf" "$USER_SYSTEMD/openclaw-gateway.service.d/20-cpu-budget.conf"
systemctl --user daemon-reload
systemctl --user enable --now terminal-heavy.slice
systemctl --user enable --now chatgpt-chrome-budget.timer
systemctl --user enable --now openclaw-heavy-task-router.service
if systemctl --user is-active --quiet openclaw-gateway.service; then
  systemctl --user restart openclaw-gateway.service
fi
printf '%s\n' 'OpenClaw CPU Guard installed.'
