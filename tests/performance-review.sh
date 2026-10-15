#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REVIEW="$ROOT/benchmarks/performance-review.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
make_fixture() {
  name=$1
  status=$2
  p95=$3
  throughput=$4
  result="$TMP/$name.tsv"
  python3 - "$result" "$p95" "$throughput" <<'PY'
import csv, pathlib, sys
result=pathlib.Path(sys.argv[1]); p95=float(sys.argv[2]); throughput=float(sys.argv[3])
fields=['mode','rep','samples','elapsed_s','routed_at_s','workers_in_heavy','worker_count','trial_valid','validity_reason',
 'all_mean_ms','all_p50_ms','all_p95_ms','all_p99_ms','all_max_ms','work_p95_ms','steady_n','steady_mean_ms',
 'steady_p50_ms','steady_p95_ms','steady_p99_ms','steady_max_ms','steady_work_p95_ms','worker_iterations_per_sec','worker_throughput_per_sec',
 'router_cpu_pct_one_core','router_peak_rss_kib','psi_some_avg10','router_enabled_during_trial']
with result.open('w',newline='') as out, result.with_suffix('.samples.tsv').open('w',newline='') as raw:
 writer=csv.DictWriter(out,fieldnames=fields,delimiter='\t',lineterminator='\n'); writer.writeheader()
 samples=csv.DictWriter(raw,fieldnames=['mode','rep','relative_s','wakeup_lateness_ms','work_duration_ms'],delimiter='\t',lineterminator='\n'); samples.writeheader()
 for rep in range(1,6):
  for mode in (('baseline','router') if rep%2 else ('router','baseline')):
   router=mode=='router'; cutoff=12.0
   steady_n=sum(1 for i in range(1500) if i*0.02 >= cutoff)
   row={'mode':mode,'rep':rep,'samples':1500,'elapsed_s':30.0,'routed_at_s':1.0 if router else '',
    'workers_in_heavy':1 if router else 0,'worker_count':1,'trial_valid':1,'validity_reason':'',
    'all_mean_ms':0.1,'all_p50_ms':0.1,'all_p95_ms':1.0,'all_p99_ms':2.0,'all_max_ms':3.0,'work_p95_ms':3.0,
    'steady_n':steady_n,'steady_mean_ms':0.1,'steady_p50_ms':0.1,'steady_p95_ms':p95 if router else 1.0,
    'steady_p99_ms':1.8 if router else 2.0,'steady_max_ms':3.0,'steady_work_p95_ms':3.06 if router else 3.0,
    'worker_iterations_per_sec':throughput if router else 1000,'worker_throughput_per_sec':throughput if router else 1000,
    'router_cpu_pct_one_core':1.0 if router else '',
    'router_peak_rss_kib':18000 if router else '', 'psi_some_avg10':5.0,'router_enabled_during_trial':int(router)}
   writer.writerow(row)
   for i in range(1500):
    samples.writerow({'mode':mode,'rep':rep,'relative_s':f'{i*0.02:.6f}',
      'wakeup_lateness_ms':'0.1','work_duration_ms':'0.2'})
PY
  printf 'result_status=%s\nworkers=1\nworkload_kind=synthetic\ngit_revision=fixture\nhost=test-host\npython=python-test\ncpu_model=test-cpu\nlogical_cpus=8\nsystemd=systemd-test\ncgroup_mode=v2\ncgroup_controllers=cpu\nrepetitions=5\nduration_seconds=30\nparent_cpu_quota=100-percent\n' "$status" > "$TMP/$name.environment.txt"
  printf '%s\n' "$result"
}

pass=$(make_fixture pass COMPLETE 1.05 1050)
python3 "$REVIEW" "$pass" > "$TMP/pass.out"
grep -q '^acceptance_status=PASS$' "$TMP/pass.out"
grep -q '^scope=synthetic Python integer-mixing workload' "$TMP/pass.out"
grep -q '^VARIATION: steady_p95_ms ratio min=' "$TMP/pass.out"
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

throughput_fail=$(make_fixture throughput-fail COMPLETE 1.00 800)
if python3 "$REVIEW" "$throughput_fail" > "$TMP/throughput-fail.out"; then
  echo 'performance reviewer accepted a throughput regression' >&2; exit 1
else [ "$?" -eq 1 ]; fi
grep -q '^FAIL: median paired worker_throughput_per_sec ratio=0.800 limit=≥0.90' "$TMP/throughput-fail.out"

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

# New manifests opt in to independent recomputation; summary values that do
# not match the raw probe samples must never be accepted as complete evidence.
cp "$pass" "$TMP/raw-summary-mismatch.tsv"
cp "$TMP/pass.samples.tsv" "$TMP/raw-summary-mismatch.samples.tsv"
cp "$TMP/pass.environment.txt" "$TMP/raw-summary-mismatch.environment.txt"
printf 'raw_summary_validation=true\n' >> "$TMP/raw-summary-mismatch.environment.txt"
if python3 "$REVIEW" "$TMP/raw-summary-mismatch.tsv" > "$TMP/raw-summary-mismatch.out" 2>&1; then
  echo 'performance reviewer accepted summary values inconsistent with raw samples' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/raw-summary-mismatch.out"
grep -q 'summary is inconsistent with raw samples' "$TMP/raw-summary-mismatch.out"

# New interval telemetry is fail-closed: a manifest reference to missing
# cpu.stat/weight/PSI evidence cannot silently retain a PASS verdict.
cp "$pass" "$TMP/missing-telemetry.tsv"
cp "$TMP/pass.samples.tsv" "$TMP/missing-telemetry.samples.tsv"
cp "$TMP/pass.environment.txt" "$TMP/missing-telemetry.environment.txt"
printf 'interval_telemetry=%s; test fixture\n' "$TMP/missing-telemetry.telemetry.tsv" >> "$TMP/missing-telemetry.environment.txt"
if python3 "$REVIEW" "$TMP/missing-telemetry.tsv" > "$TMP/missing-telemetry.out" 2>&1; then
  echo 'performance reviewer accepted missing interval telemetry' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/missing-telemetry.out"
grep -q 'interval telemetry file missing' "$TMP/missing-telemetry.out"

# Summary data cannot pass when raw samples are missing, malformed, non-finite,
# duplicated/out of order, or inconsistent with summary counts/steady windows.
cp "$pass" "$TMP/no-raw.tsv"
cp "$TMP/pass.samples.tsv" "$TMP/no-raw.samples.tsv"
rm "$TMP/no-raw.samples.tsv"
cp "$TMP/pass.environment.txt" "$TMP/no-raw.environment.txt"
if python3 "$REVIEW" "$TMP/no-raw.tsv" > "$TMP/no-raw.out" 2>&1; then
  echo 'performance reviewer accepted missing raw samples' >&2; exit 1
else [ "$?" -eq 2 ]; fi
cp "$pass" "$TMP/count-mismatch.tsv"
cp "$TMP/pass.samples.tsv" "$TMP/count-mismatch.samples.tsv"
cp "$TMP/pass.environment.txt" "$TMP/count-mismatch.environment.txt"
sed -i '2s/1500/1499/' "$TMP/count-mismatch.tsv"
if python3 "$REVIEW" "$TMP/count-mismatch.tsv" > "$TMP/count-mismatch.out" 2>&1; then
  echo 'performance reviewer accepted a summary/raw sample-count mismatch' >&2; exit 1
else [ "$?" -eq 2 ]; fi

cp "$pass" "$TMP/raw-nonfinite.tsv"
cp "$TMP/pass.samples.tsv" "$TMP/raw-nonfinite.samples.tsv"
cp "$TMP/pass.environment.txt" "$TMP/raw-nonfinite.environment.txt"
sed -i '2s/0.1/nan/' "$TMP/raw-nonfinite.samples.tsv"
if python3 "$REVIEW" "$TMP/raw-nonfinite.tsv" > "$TMP/raw-nonfinite.out" 2>&1; then
  echo 'performance reviewer accepted non-finite raw samples' >&2; exit 1
else [ "$?" -eq 2 ]; fi

python3 - "$REVIEW" <<'PY'
import importlib.util, math, sys
spec=importlib.util.spec_from_file_location('performance_review',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert module.acceptance_limits_error(max_latency=float('nan'))
assert module.acceptance_limits_error(min_pairs=1)
assert module.acceptance_limits_error(max_rss=0)
assert module.acceptance_limits_error() is None
print('performance reviewer rejects invalid acceptance limits')
PY

printf '%s\n' 'performance review tests: ok (PASS/FAIL/INCOMPLETE, paired ratios, no fabricated acceptance)'
