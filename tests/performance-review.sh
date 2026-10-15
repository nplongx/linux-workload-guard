#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REVIEW="$ROOT/benchmarks/performance-review.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
HEADER='mode	rep	trial_valid	workers_in_heavy	worker_count	steady_n	steady_p95_ms	steady_p99_ms	steady_work_p95_ms	worker_iterations_per_sec	router_cpu_pct_one_core	router_peak_rss_kib'

make_fixture() {
  name=$1
  status=$2
  p95=$3
  throughput=$4
  result="$TMP/$name.tsv"
  printf '%s\n' "$HEADER" > "$result"
  for rep in 1 2 3 4 5; do
    printf 'baseline\t%s\t1\t0\t1\t600\t1.00\t2.00\t3.00\t1000\t\t\n' "$rep" >> "$result"
    printf 'router\t%s\t1\t1\t1\t600\t%s\t1.80\t3.06\t%s\t1.00\t18000\n' "$rep" "$p95" "$throughput" >> "$result"
  done
  printf 'result_status=%s\nworkers=1\n' "$status" > "$TMP/$name.environment.txt"
  printf '%s\n' "$result"
}

pass=$(make_fixture pass COMPLETE 1.05 1050)
python3 "$REVIEW" "$pass" > "$TMP/pass.out"
grep -q '^acceptance_status=PASS$' "$TMP/pass.out"
grep -q '^scope=synthetic routing probe only' "$TMP/pass.out"
if python3 "$REVIEW" "$pass" > "$TMP/overwrite.out" 2>&1; then
  echo 'performance reviewer overwrote an existing report' >&2; exit 1
else
  [ "$?" -eq 2 ]
fi
grep -q 'refusing to overwrite acceptance report' "$TMP/overwrite.out"

fail=$(make_fixture fail COMPLETE 1.20 1050)
if python3 "$REVIEW" "$fail" > "$TMP/fail.out"; then
  echo 'performance reviewer accepted a latency regression' >&2; exit 1
else
  [ "$?" -eq 1 ]
fi
grep -q '^acceptance_status=FAIL$' "$TMP/fail.out"
grep -q '^FAIL: median paired steady_p95_ms ratio=1.200' "$TMP/fail.out"

incomplete=$(make_fixture incomplete INCOMPLETE 1.00 1000)
if python3 "$REVIEW" "$incomplete" > "$TMP/incomplete.out"; then
  echo 'performance reviewer accepted an incomplete benchmark' >&2; exit 1
else
  [ "$?" -eq 2 ]
fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/incomplete.out"

malformed=$(make_fixture malformed COMPLETE 1.00 1000)
printf 'unexpected\t6\t1\t1\t1\t600\t1.00\t2.00\t3.00\t1000\t1.00\t18000\n' >> "$malformed"
if python3 "$REVIEW" "$malformed" > "$TMP/malformed.out"; then
  echo 'performance reviewer accepted an extra malformed mode/row' >&2; exit 1
else
  [ "$?" -eq 2 ]
fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/malformed.out"

printf '%s\n' 'performance review tests: ok (PASS/FAIL/INCOMPLETE, paired ratios, no fabricated acceptance)'
