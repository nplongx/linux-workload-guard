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
if grep -q 'WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh' "$TMP"; then
  echo 'generic hosted CI must not actuate quota or run live controller workload' >&2
  exit 1
fi

# Live actuation is isolated to an explicitly approved, dedicated runner job.
sed -n '/^  approved-controller-runtime:/,$p' "$WORKFLOW" > "$TMP"
grep -q "RUN_APPROVED_CONTROLLER_INTEGRATION == 'true'" "$TMP"
grep -q "APPROVED_CONTROLLER_RUNTIME_APPROVED == 'true'" "$TMP"
grep -q 'lwg-approved-controller-test' "$TMP"
grep -q 'WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh' "$TMP"
grep -q 'does not close ABBA, throughput, rollback, PSI-crossing, or soak gates' "$TMP"
printf '%s\n' 'CI runtime gate safety: ok (generic hosted matrix SKIP/OPEN; actuation requires two approvals and dedicated runner label)'
