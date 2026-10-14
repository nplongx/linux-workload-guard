#!/bin/sh
# Source from systemd/cgroup integration tests. Creates a PID-unique recovery
# leaf under protected-workload.slice without replacing the user's real unit.

setup_test_recovery_unit() {
  TEST_RECOVERY_UNIT="protected-workload-recovery-test-$$.slice"
  TEST_RECOVERY_UNIT_FILE="$HOME/.config/systemd/user/$TEST_RECOVERY_UNIT"
  TEST_RECOVERY_UNIT_CREATED=0
  mkdir -p "$(dirname -- "$TEST_RECOVERY_UNIT_FILE")"
  if [ -e "$TEST_RECOVERY_UNIT_FILE" ]; then
    printf 'test recovery unit path already exists: %s\n' "$TEST_RECOVERY_UNIT_FILE" >&2
    return 1
  fi
  cp "$ROOT/systemd/protected-workload-recovery.slice" "$TEST_RECOVERY_UNIT_FILE"
  TEST_RECOVERY_UNIT_CREATED=1
  systemctl --user daemon-reload
  export WORKLOAD_GUARD_RECOVERY_UNIT="$TEST_RECOVERY_UNIT"
}

cleanup_test_recovery_unit() {
  [ "${TEST_RECOVERY_UNIT_CREATED:-0}" -eq 1 ] || return 0
  systemctl --user stop "$TEST_RECOVERY_UNIT" >/dev/null 2>&1 || true
  rm -f "$TEST_RECOVERY_UNIT_FILE"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}
