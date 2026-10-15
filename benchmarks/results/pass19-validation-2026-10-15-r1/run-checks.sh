#!/bin/sh
set -u
B=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$B/../../.." && pwd)
FAILED=0
run_check() {
  name=$1; shift
  printf 'recorded_at=%s\ncommand=' "$(date --iso-8601=seconds)" > "$B/$name.command.txt"
  for arg do printf ' <%s>' "$arg" >> "$B/$name.command.txt"; done; printf '\n' >> "$B/$name.command.txt"
  if "$@" > "$B/$name.log" 2>&1; then rc=0; else rc=$?; FAILED=1; fi
  printf 'recorded_at=%s\nexit_status=%s\n' "$(date --iso-8601=seconds)" "$rc" > "$B/$name.status.txt"
  printf '%s: exit=%s\n' "$name" "$rc"
}
cd "$ROOT"
run_check full-test-suite ./test.sh
run_check python-compile sh -c 'python3 -m py_compile bin/workload-router.py bin/workload-profile benchmarks/*.py tests/controller-hardening.py tests/routing-telemetry.py'
run_check shell-syntax sh -c 'sh -n install.sh uninstall.sh test.sh bin/run-workload bin/workload-guard bin/limit-browser-automation-cgroup tests/integration-routing.sh tests/recovery-routing.sh tests/shadow-routing.sh tests/install-uninstall.sh tests/controller-integration.sh tests/aggregate-scope-integration.sh tests/recovery-unit.sh tests/soak-monitor.sh tests/routing-benchmark-safety.sh tests/quota-benchmark-safety.sh tests/live-psi-observe-safety.sh tests/rc-gate-preflight-safety.sh tests/performance-review.sh tests/analyze-aggregate-routing.sh tests/run-workload-scope.sh benchmarks/soak-monitor.sh benchmarks/rc-gate-preflight.sh && bash -n benchmarks/quota-benchmark.sh benchmarks/routing-contention-isolated.sh'
run_check systemd-verify systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice
run_check strict-telemetry python3 tests/routing-telemetry.py
run_check aggregate-evidence ./tests/analyze-aggregate-routing.sh
run_check soak-fail-closed ./tests/soak-monitor.sh
run_check gate-preflight-safety ./tests/rc-gate-preflight-safety.sh
run_check version-smoke ./bin/workload-guard version
run_check whitespace git diff --check
exit "$FAILED"
