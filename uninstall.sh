#!/bin/sh
set -eu
USER_SYSTEMD="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
systemctl --user disable --now openclaw-heavy-task-router.service 2>/dev/null || true
systemctl --user disable --now chatgpt-chrome-budget.timer 2>/dev/null || true
rm -f "$USER_SYSTEMD/openclaw-heavy-task-router.service" "$USER_SYSTEMD/terminal-heavy.slice"
rm -f "$USER_SYSTEMD/chatgpt-chrome.slice" "$USER_SYSTEMD/chatgpt-chrome-budget.service" "$USER_SYSTEMD/chatgpt-chrome-budget.timer"
rm -f "$USER_SYSTEMD/openclaw-gateway.service.d/20-cpu-budget.conf"
rmdir "$USER_SYSTEMD/openclaw-gateway.service.d" 2>/dev/null || true
rm -f "$BIN/openclaw-heavy-task-router.py" "$BIN/run-task" "$BIN/limit-chatgpt-chrome-cgroup"
systemctl --user daemon-reload
printf '%s\n' 'OpenClaw CPU Guard removed.'
