#!/bin/sh
# Run every approved routing lifecycle suite, preserving each result even when
# an earlier suite fails. This helper does not grant approval; its caller must
# already be inside the separately approved runtime workflow.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: run-approved-routing-suites.sh EVIDENCE_DIR [SUITE_DIR_FOR_TESTS]" >&2
  exit 2
fi
OUT=$1
SUITE_DIR=${2:-$ROOT/tests}
if [ ! -d "$OUT" ] || [ -L "$OUT" ]; then
  echo "evidence directory must already exist and must not be a symlink: $OUT" >&2
  exit 2
fi
for artifact in routing-step.log routing-suite-status.tsv integration-routing.log recovery-routing.log shadow-routing.log aggregate-scope-integration.log; do
  if [ -e "$OUT/$artifact" ] || [ -L "$OUT/$artifact" ]; then
    echo "refusing to overwrite existing routing evidence: $OUT/$artifact" >&2
    exit 2
  fi
done
printf 'suite\texit_status\tlog\n' > "$OUT/routing-suite-status.tsv"
: > "$OUT/routing-step.log"
overall=0
for suite in integration-routing recovery-routing shadow-routing aggregate-scope-integration; do
  log="$OUT/$suite.log"
  printf 'suite=%s started_at=%s\n' "$suite" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$OUT/routing-step.log"
  if [ ! -x "$SUITE_DIR/$suite.sh" ]; then
    rc=127
    printf 'suite executable missing: %s\n' "$SUITE_DIR/$suite.sh" > "$log"
  else
    set +e
    "$SUITE_DIR/$suite.sh" > "$log" 2>&1
    rc=$?
    set -e
  fi
  printf '%s\t%s\t%s\n' "$suite" "$rc" "$log" >> "$OUT/routing-suite-status.tsv"
  {
    printf '\n--- suite=%s exit_status=%s ---\n' "$suite" "$rc"
    cat "$log"
  } >> "$OUT/routing-step.log" || overall=1
  if [ "$rc" -ne 0 ]; then overall=1; fi
done
exit "$overall"
