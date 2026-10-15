#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/bin"
for tool in dirname uname tr; do
  path=$(command -v "$tool")
  ln -s "$path" "$TMP/bin/$tool"
done

# A generic runner without systemd-run must visibly SKIP/OPEN, and strict mode
# for a provisioned runner must convert the same capability skip into failure.
if PATH="$TMP/bin" WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 \
    "$ROOT/tests/controller-integration.sh" >"$TMP/strict.out" 2>"$TMP/strict.err"; then
  echo 'strict controller integration accepted missing systemd-run capability' >&2
  exit 1
else
  rc=$?
  if [ "$rc" -ne 1 ]; then
    cat "$TMP/strict.out" "$TMP/strict.err" >&2
    echo "strict controller integration returned unexpected status $rc" >&2
    exit 1
  fi
fi
grep -q 'controller integration: SKIP reason=systemd-run command unavailable' "$TMP/strict.out"
grep -q 'strict live-capability mode converts SKIP to failure' "$TMP/strict.err"

PATH="$TMP/bin" GITHUB_STEP_SUMMARY="$TMP/summary.md" \
  "$ROOT/tests/controller-integration.sh" >"$TMP/compat.out"
grep -q 'controller integration: SKIP reason=systemd-run command unavailable' "$TMP/compat.out"
grep -q 'Delegated CPU runtime gate: \*\*SKIP / OPEN\*\*' "$TMP/summary.md"
grep -q 'does not close this runtime gate' "$TMP/summary.md"
printf '%s\n' 'controller integration skip safety: ok (explicit SKIP/OPEN and strict-mode failure)'
