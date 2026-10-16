#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORKFLOW="$ROOT/.github/workflows/ci.yml"
RUNTIME_WORKFLOW="$ROOT/.github/workflows/approved-controller-runtime.yml"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM

# The generic hosted matrix must never directly run the quota-actuating live
# integration. It should always report OPEN rather than letting a green job
# imply that the runtime gate passed.
sed -n '/^  test:/,$p' "$WORKFLOW" > "$TMP"
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

# The generic push/pull_request workflow must not contain any self-hosted job or
# quota/workload actuation command. A PR can edit its own workflow YAML, so the
# runtime job lives in a separate workflow with workflow_dispatch as its only
# event; that workflow only runs code selected from main.
if grep -Eq '^[[:space:]]*runs-on:.*self-hosted|lwg-approved-controller-test|^[[:space:]]*WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1|^[[:space:]]*\./tests/(integration-routing|recovery-routing|shadow-routing|aggregate-scope-integration)\.sh' "$WORKFLOW"; then
  echo 'generic CI workflow must not contain self-hosted runner or runtime actuation steps' >&2
  exit 1
fi
grep -q '^  workflow_dispatch:' "$RUNTIME_WORKFLOW"
grep -q "github.event_name == 'workflow_dispatch'" "$RUNTIME_WORKFLOW"
grep -q "github.ref == 'refs/heads/main'" "$RUNTIME_WORKFLOW"
grep -q "RUN_APPROVED_CONTROLLER_INTEGRATION == 'true'" "$RUNTIME_WORKFLOW"
grep -q "APPROVED_CONTROLLER_RUNTIME_APPROVED == 'true'" "$RUNTIME_WORKFLOW"
grep -q 'lwg-approved-controller-test' "$RUNTIME_WORKFLOW"
grep -q '^permissions:' "$RUNTIME_WORKFLOW"
grep -q 'contents: read' "$RUNTIME_WORKFLOW"
grep -q 'group: lwg-approved-controller-runtime' "$RUNTIME_WORKFLOW"
grep -q 'cancel-in-progress: false' "$RUNTIME_WORKFLOW"
grep -q 'timeout-minutes: 30' "$RUNTIME_WORKFLOW"
cp "$RUNTIME_WORKFLOW" "$TMP"
grep -q "RUN_APPROVED_CONTROLLER_INTEGRATION == 'true'" "$TMP"
grep -q "APPROVED_CONTROLLER_RUNTIME_APPROVED == 'true'" "$TMP"
grep -q 'lwg-approved-controller-test' "$TMP"
grep -q 'WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh' "$TMP"
grep -q './tests/integration-routing.sh' "$TMP"
grep -q './tests/recovery-routing.sh' "$TMP"
grep -q './tests/shadow-routing.sh' "$TMP"
grep -q './tests/aggregate-scope-integration.sh' "$TMP"
grep -q 'does not close ABBA, throughput, rollback, PSI-crossing, or soak gates' "$TMP"
printf '%s\n' 'CI runtime gate safety: ok (generic CI has no self-hosted/runtime steps; separate dispatch-only workflow requires main, two approval variables, and the dedicated runner label)'
