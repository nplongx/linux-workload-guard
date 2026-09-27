#!/bin/sh
set -eu
USER_SYSTEMD="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
systemctl --user disable --now workload-router.service 2>/dev/null || true
systemctl --user disable --now browser-automation-budget.timer 2>/dev/null || true
rm -f "$USER_SYSTEMD/workload-router.service" "$USER_SYSTEMD/heavy-workload.slice"
rm -f "$USER_SYSTEMD/browser-automation.slice" "$USER_SYSTEMD/browser-automation-budget.service" "$USER_SYSTEMD/browser-automation-budget.timer"
rm -f "$USER_SYSTEMD/protected-workload.service.d/20-cpu-budget.conf"
rmdir "$USER_SYSTEMD/protected-workload.service.d" 2>/dev/null || true
rm -f "$BIN/workload-router.py" "$BIN/run-workload" "$BIN/limit-browser-automation-cgroup"
systemctl --user daemon-reload
printf '%s\n' 'Linux Workload Guard removed.'
