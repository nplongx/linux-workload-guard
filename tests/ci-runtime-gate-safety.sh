#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORKFLOW="$ROOT/.github/workflows/ci.yml"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM

# The generic hosted matrix must never directly run the quota-actuating live
# integration. It should always report OPEN rather than letting a green job
# imply that the runtime gate passed.
sed -n '/^  test:/,/^  approved-controller-runtime:/p' "$WORKFLOW" |
  sed '$d' > "$TMP"
grep -q 'Delegated CPU runtime gate (SKIP is not PASS)' "$TMP"
grep -q 'Status: \*\*SKIP / OPEN\*\*' "$TMP"
grep -q 'Routing lifecycle runtime gate (SKIP is not PASS)' "$TMP"
grep -q 'Aggregate scope runtime gate (SKIP is not PASS)' "$TMP"
for active_step in \
  'WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh' \
  './tests/integration-routing.sh' './tests/recovery-routing.sh' \
  './tests/shadow-routing.sh' './tests/aggregate-scope-integration.sh'; do
  if grep -Fq "$active_step" "$TMP"; then
    echo "generic hosted CI must not run workload/actuation integration: $active_step" >&2
    exit 1
  fi
done

# Live actuation is isolated to an explicitly approved, dedicated runner job.
sed -n '/^  approved-controller-runtime:/,$p' "$WORKFLOW" > "$TMP"
grep -q "RUN_APPROVED_CONTROLLER_INTEGRATION == 'true'" "$TMP"
grep -q "APPROVED_CONTROLLER_RUNTIME_APPROVED == 'true'" "$TMP"
grep -q 'lwg-approved-controller-test' "$TMP"
grep -q 'WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh' "$TMP"
grep -q './tests/integration-routing.sh' "$TMP"
grep -q './tests/recovery-routing.sh' "$TMP"
grep -q './tests/shadow-routing.sh' "$TMP"
grep -q './tests/aggregate-scope-integration.sh' "$TMP"
grep -q 'does not close ABBA, throughput, rollback, PSI-crossing, or soak gates' "$TMP"
printf '%s\n' 'CI runtime gate safety: ok (generic hosted matrix is test/static-only and marks all live workload/actuation gates SKIP/OPEN; runtime integration requires two approvals and dedicated runner label)'
