#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BENCH="$ROOT/benchmarks/routing-contention-isolated.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

test -x "$BENCH"
bash -n "$BENCH"

# Short windows cannot produce comparable steady-state percentiles and must
# fail before any user unit, workload, or result file is created.
if RESULT="$TMP/short.tsv" DURATION=19 "$BENCH" >/dev/null 2>"$TMP/short.err"; then
  echo 'routing benchmark accepted an under-length trial' >&2; exit 1
fi
grep -q 'DURATION must be >=20s' "$TMP/short.err"
[ ! -e "$TMP/short.tsv" ]

# Historical evidence is immutable: output collisions are refused, not opened
# with truncate/write mode.
printf 'preserve-this-result\n' > "$TMP/existing.tsv"
if RESULT="$TMP/existing.tsv" DURATION=20 "$BENCH" >/dev/null 2>"$TMP/existing.err"; then
  echo 'routing benchmark accepted an existing result file' >&2; exit 1
fi
grep -q 'refusing to overwrite existing benchmark evidence' "$TMP/existing.err"
grep -qx 'preserve-this-result' "$TMP/existing.tsv"

printf '%s\n' 'routing benchmark safety: ok (minimum window, no-overwrite, executable wrapper)'
