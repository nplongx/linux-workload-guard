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
if python3 "$REVIEW" "$pass" > "$TMP/pass.out"; then
  echo 'performance reviewer accepted legacy evidence without a verifiable PSI noise series' >&2; exit 1
else [ "$?" -eq 4 ]; fi
grep -q '^acceptance_status=INCONCLUSIVE$' "$TMP/pass.out"
grep -q '^INCONCLUSIVE: legacy evidence has no verified steady-window host PSI series' "$TMP/pass.out"
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
  echo 'performance reviewer accepted legacy evidence without PSI controls' >&2; exit 1
else [ "$?" -eq 4 ]; fi
grep -q '^acceptance_status=INCONCLUSIVE$' "$TMP/fail.out"

throughput_fail=$(make_fixture throughput-fail COMPLETE 1.00 800)
if python3 "$REVIEW" "$throughput_fail" > "$TMP/throughput-fail.out"; then
  echo 'performance reviewer accepted legacy evidence without PSI controls' >&2; exit 1
else [ "$?" -eq 4 ]; fi
grep -q '^acceptance_status=INCONCLUSIVE$' "$TMP/throughput-fail.out"

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
import csv, importlib.util, math, pathlib, sys, tempfile
spec=importlib.util.spec_from_file_location('performance_review',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert module.acceptance_limits_error(max_latency=float('nan'))
assert module.acceptance_limits_error(min_pairs=1)
assert module.acceptance_limits_error(max_rss=0)
assert module.acceptance_limits_error() is None
with tempfile.TemporaryDirectory() as td:
    root=pathlib.Path(td); telemetry=root/'telemetry.tsv'
    fields=['mode','rep','elapsed_s','cgroup_kind','host_psi_some_avg10']
    with telemetry.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,delimiter='\t',lineterminator='\n'); writer.writeheader()
        for mode,psi in [('baseline','2.0'),('router','4.9')]:
            writer.writerow({'mode':mode,'rep':1,'elapsed_s':'12.5','cgroup_kind':'parent','host_psi_some_avg10':psi})
    status,detail=module.steady_host_psi_noise({'interval_telemetry':str(telemetry)},root/'result.environment.txt',1)
    assert status=='WITHIN_LIMIT', (status,detail)
    with telemetry.open('a') as stream:
        stream.write('router\t1\t13.5\tparent\t27.0\n')
    status,detail=module.steady_host_psi_noise({'interval_telemetry':str(telemetry)},root/'result.environment.txt',1)
    assert status=='CONFOUNDED_INCONCLUSIVE' and 'all samples retained' in detail, (status,detail)
    telemetry.write_text('mode\trep\telapsed_s\tcgroup_kind\thost_psi_some_avg10\nbaseline\t1\t12.5\tparent\tnan\nrouter\t1\t12.5\tparent\t1\n')
    status,detail=module.steady_host_psi_noise({'interval_telemetry':str(telemetry)},root/'result.environment.txt',1)
    assert status=='INVALID', (status,detail)
    raw=root/'unordered.samples.tsv'
    raw.write_text('mode\trep\trelative_s\twakeup_lateness_ms\twork_duration_ms\n'
                   'baseline\t1\t12.0\t1.0\t1.0\n'
                   'baseline\t1\t13.0\t1.0\t1.0\n'
                   'baseline\t1\t12.5\t1.0\t1.0\n')
    try:
        module.read_raw_samples(raw,30.0)
    except ValueError as exc:
        assert 'strictly increasing' in str(exc), str(exc)
    else:
        raise AssertionError('unordered paired samples were accepted')
print('performance reviewer rejects invalid limits and detects PSI confounding / unordered evidence')
PY

# End-to-end status classification with a fully shaped interval telemetry
# fixture: a quiet run may PASS; the same metrics with PSI above the predeclared
# 5% noise limit must remain CONFOUNDED_INCONCLUSIVE, not PASS or causal FAIL.
python3 - "$TMP" <<'PY'
import csv, pathlib, sys
root=pathlib.Path(sys.argv[1])
summary_fields=['mode','rep','samples','elapsed_s','routed_at_s','workers_in_heavy','worker_count','trial_valid','validity_reason',
 'all_mean_ms','all_p50_ms','all_p95_ms','all_p99_ms','all_max_ms','work_p95_ms','steady_n','steady_mean_ms',
 'steady_p50_ms','steady_p95_ms','steady_p99_ms','steady_max_ms','steady_work_p95_ms','worker_iterations_per_sec','worker_throughput_per_sec',
 'router_cpu_pct_one_core','router_peak_rss_kib','psi_some_avg10','router_enabled_during_trial']
sample_fields=['mode','rep','relative_s','wakeup_lateness_ms','work_duration_ms']
telemetry_fields=['mode','rep','monotonic_s','elapsed_s','cgroup_kind','unit','cgroup_path','cpu_weight','usage_usec','nr_periods','nr_throttled','throttled_usec',
 'host_psi_some_avg10','loadavg_1m','router_cpu_ticks','router_rss_kib','router_log_new_lines']
for name, psi in [('quiet-telemetry',1.0),('noisy-telemetry',27.0)]:
    result=root/f'{name}.tsv'; raw=result.with_suffix('.samples.tsv'); manifest=result.with_suffix('.environment.txt'); telemetry=result.with_suffix('.telemetry.tsv'); throughput=result.with_suffix('.throughput.tsv')
    with result.open('w',newline='') as out, raw.open('w',newline='') as rawout, telemetry.open('w',newline='') as tout, throughput.open('w',newline='') as thout:
        sw=csv.DictWriter(out,fieldnames=summary_fields,delimiter='\t',lineterminator='\n'); sw.writeheader()
        rw=csv.DictWriter(rawout,fieldnames=sample_fields,delimiter='\t',lineterminator='\n'); rw.writeheader()
        tw=csv.DictWriter(tout,fieldnames=telemetry_fields,delimiter='\t',lineterminator='\n'); tw.writeheader()
        thw=csv.DictWriter(thout,fieldnames=['mode','rep','worker','elapsed_s','counter','metric_kind'],delimiter='\t',lineterminator='\n'); thw.writeheader()
        for rep in range(1,6):
            for mode in (('baseline','router') if rep%2 else ('router','baseline')):
                router=mode=='router'
                steady=sum(1 for i in range(1500) if i*0.02 >= 12.0)
                sw.writerow({'mode':mode,'rep':rep,'samples':1500,'elapsed_s':30.0,'routed_at_s':1.0 if router else '',
                    'workers_in_heavy':2 if router else 0,'worker_count':2,'trial_valid':1,'validity_reason':'',
                    'all_mean_ms':0.1,'all_p50_ms':0.1,'all_p95_ms':1.0,'all_p99_ms':2.0,'all_max_ms':3.0,'work_p95_ms':3.0,
                    'steady_n':steady,'steady_mean_ms':0.1,'steady_p50_ms':0.1,'steady_p95_ms':1.0,'steady_p99_ms':1.8 if router else 2.0,
                    'steady_max_ms':3.0,'steady_work_p95_ms':3.06 if router else 3.0,'worker_iterations_per_sec':2000,
                    'worker_throughput_per_sec':2000,'router_cpu_pct_one_core':1.0 if router else '',
                    'router_peak_rss_kib':18000 if router else '', 'psi_some_avg10':psi,'router_enabled_during_trial':int(router)})
                for i in range(1500):
                    rw.writerow({'mode':mode,'rep':rep,'relative_s':f'{i*0.02:.6f}','wakeup_lateness_ms':'0.1','work_duration_ms':'0.2'})
                for worker in (1,2):
                    for elapsed,counter in ((12,0),(13,1000),(14,2000)):
                        thw.writerow({'mode':mode,'rep':rep,'worker':worker,'elapsed_s':elapsed,'counter':counter,'metric_kind':'cumulative_counter'})
                for kind in ('parent','heavy','probe'):
                    unit=f'lwg-{kind}-{rep}'
                    for i in range(20):
                        tw.writerow({'mode':mode,'rep':rep,'monotonic_s':f'{10000+rep*100+i:.3f}','elapsed_s':f'{12+i:.3f}',
                            'cgroup_kind':kind,'unit':unit,'cgroup_path':f'/test/{mode}/{rep}/{kind}',
                            'cpu_weight':20 if router and kind=='workload' else 100,
                            'usage_usec':1000000+i*1000,'nr_periods':100+i,'nr_throttled':5+i,'throttled_usec':1000+i,
                            'host_psi_some_avg10':psi,'loadavg_1m':'0.5','router_cpu_ticks':100+i if router else '',
                            'router_rss_kib':18000 if router else '',
                            'router_log_new_lines':'aggregate scope decision=fixture' if router and kind=='parent' and i==0 else ''})
                for worker in (1,2):
                    kind='workload'; unit=f'lwg-{kind}-{rep}-worker{worker}'
                    for i in range(20):
                        tw.writerow({'mode':mode,'rep':rep,'monotonic_s':f'{10000+rep*100+i:.3f}','elapsed_s':f'{12+i:.3f}',
                            'cgroup_kind':kind,'unit':unit,'cgroup_path':f'/test/{mode}/{rep}/{kind}/{worker}',
                            'cpu_weight':20 if router else 100,'usage_usec':1000000+i*1000,
                            'nr_periods':100+i,'nr_throttled':5+i,'throttled_usec':1000+i,
                            'host_psi_some_avg10':psi,'loadavg_1m':'0.5','router_cpu_ticks':100+i if router else '',
                            'router_rss_kib':18000 if router else '', 'router_log_new_lines':''})
    manifest.write_text('\n'.join([
        'result_status=COMPLETE','workers=2','workload_kind=synthetic','git_revision='+'a'*40,'git_dirty=false',
        'git_diff_sha256='+'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        'host=test-host','python=python-test',
        'cpu_model=test-cpu','logical_cpus=8','systemd=systemd-test','cgroup_mode=v2','cgroup_controllers=cpu',
        'repetitions=5','duration_seconds=30','parent_cpu_quota=100-percent',f'interval_telemetry={telemetry}; test fixture',
        f'throughput_raw={throughput}; test fixture',
        'aggregate_routing=CPUWeight=20 per sample','']))
PY
quiet="$TMP/quiet-telemetry.tsv"
python3 "$REVIEW" "$quiet" > "$TMP/quiet-telemetry.out"
grep -q '^acceptance_status=PASS$' "$TMP/quiet-telemetry.out"

# Preregistered rows require a real seal/hash and an exact binding to the
# measured host, source, workload, pair count, duration and candidate weight.
# A modified protocol cannot pass even when the samples are otherwise clean.
python3 - "$TMP" "$ROOT/benchmarks/preregister_abba.py" <<'PY'
import csv, importlib.util, pathlib, shutil, sys
root=pathlib.Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('preregister_abba', pathlib.Path(sys.argv[2]))
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
source={'revision':'a'*40,'tracked_dirty':False,'tracked_diff_sha256':'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}
host={'hostname':'test-host','os_release':'test-os','kernel':'test-kernel','architecture':'x86_64','logical_cpus':8,
      'cpu_model':'test-cpu','python':'Python test','systemd':'systemd test','cgroup_v2':True,'cgroup_controllers':['cpu']}
payload=module.protocol_payload('host-fixture','operator-fixture','terminal-build',source,host,created_at='2026-10-16T00:00:00+00:00')
sealed=root/'prereg-sealed'; digest=module.write_sealed_preregistration(sealed,payload)
(root/'preregistration.sha256').write_text(digest+'\n')
def clone_result(prefix):
    for suffix in ('.tsv','.samples.tsv','.telemetry.tsv','.throughput.tsv','.environment.txt'):
        source_path=root/('quiet-telemetry'+suffix)
        target_path=root/(prefix+suffix)
        shutil.copyfile(source_path,target_path)
    env_path=root/(prefix+'.environment.txt')
    text=env_path.read_text().replace(str(root/'quiet-telemetry.telemetry.tsv'),str(root/(prefix+'.telemetry.tsv')))
    text=text.replace(str(root/'quiet-telemetry.throughput.tsv'),str(root/(prefix+'.throughput.tsv')))
    text=text.replace('workload_kind=synthetic','workload_kind=terminal-build')
    env_path.write_text(text)
    return root/(prefix+'.tsv')
accepted=clone_result('prereg-accepted')
with (root/'prereg-accepted.environment.txt').open('a') as stream:
    stream.write('\n'.join([
        'preregistration_required=true','preregistration_status=SEALED_VERIFIED',
        f'preregistration_directory={sealed}',f'preregistration_sha256={digest}',
        'preregistration_block=A','preregistration_cpu_weight=20',
        'preregistration_source_revision='+'a'*40,'preregistration_host=test-host',''
    ]))
tampered=root/'prereg-tampered'; shutil.copytree(sealed,tampered)
protocol=tampered/'protocol.json'; protocol.chmod(0o644)
protocol.write_text(protocol.read_text().replace('"minimum_median_paired_throughput_ratio": 0.9',
                                                  '"minimum_median_paired_throughput_ratio": 0.89',1))
bad=clone_result('prereg-tampered-run')
env_path=root/'prereg-tampered-run.environment.txt'
text=env_path.read_text().replace(str(sealed),str(tampered))
text += '\n'.join([
    'preregistration_required=true','preregistration_status=SEALED_VERIFIED',
    f'preregistration_directory={tampered}',f'preregistration_sha256={digest}',
    'preregistration_block=A','preregistration_cpu_weight=20',
    'preregistration_source_revision='+'a'*40,'preregistration_host=test-host',''
])
env_path.write_text(text)
print(accepted)
print(bad)
PY
if ! python3 "$REVIEW" "$TMP/prereg-accepted.tsv" > "$TMP/prereg-accepted.out"; then
  cat "$TMP/prereg-accepted.out" >&2
  echo 'sealed preregistration fixture should produce a valid scoped acceptance result' >&2
  exit 1
fi
grep -q '^acceptance_status=PASS$' "$TMP/prereg-accepted.out"
if python3 "$REVIEW" "$TMP/prereg-tampered-run.tsv" > "$TMP/prereg-tampered-run.out"; then
  echo 'performance reviewer accepted a modified sealed preregistration' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/prereg-tampered-run.out"
grep -q 'preregistration.*SHA-256 does not match' "$TMP/prereg-tampered-run.out"

# A measurable performance failure is still FAIL when the preregistered PSI
# controls and raw evidence are complete. This distinguishes causal FAIL from
# a legacy result that cannot assess confounding.
cp "$TMP/quiet-telemetry.tsv" "$TMP/quiet-latency-fail.tsv"
cp "$TMP/quiet-telemetry.samples.tsv" "$TMP/quiet-latency-fail.samples.tsv"
cp "$TMP/quiet-telemetry.telemetry.tsv" "$TMP/quiet-latency-fail.telemetry.tsv"
cp "$TMP/quiet-telemetry.throughput.tsv" "$TMP/quiet-latency-fail.throughput.tsv"
cp "$TMP/quiet-telemetry.environment.txt" "$TMP/quiet-latency-fail.environment.txt"
sed -i "s|$TMP/quiet-telemetry.telemetry.tsv|$TMP/quiet-latency-fail.telemetry.tsv|; s|$TMP/quiet-telemetry.throughput.tsv|$TMP/quiet-latency-fail.throughput.tsv|" "$TMP/quiet-latency-fail.environment.txt"
python3 - "$TMP/quiet-latency-fail.tsv" <<'PY'
import csv, pathlib, sys
p=pathlib.Path(sys.argv[1])
with p.open(newline='') as source:
    rows=list(csv.DictReader(source,delimiter='\t')); fields=list(rows[0])
for row in rows:
    if row['mode']=='router': row['steady_p95_ms']='1.20'
with p.open('w',newline='') as output:
    writer=csv.DictWriter(output,fieldnames=fields,delimiter='\t',lineterminator='\n'); writer.writeheader(); writer.writerows(rows)
PY
if python3 "$REVIEW" "$TMP/quiet-latency-fail.tsv" > "$TMP/quiet-latency-fail.out"; then
  echo 'performance reviewer accepted a complete-evidence latency regression' >&2; exit 1
else [ "$?" -eq 1 ]; fi
grep -q '^acceptance_status=FAIL$' "$TMP/quiet-latency-fail.out"
grep -q '^FAIL: median paired steady_p95_ms ratio=1.200 limit=≤1.10' "$TMP/quiet-latency-fail.out"
cp "$TMP/quiet-telemetry.tsv" "$TMP/dirty-telemetry.tsv"
cp "$TMP/quiet-telemetry.samples.tsv" "$TMP/dirty-telemetry.samples.tsv"
cp "$TMP/quiet-telemetry.telemetry.tsv" "$TMP/dirty-telemetry.telemetry.tsv"
cp "$TMP/quiet-telemetry.throughput.tsv" "$TMP/dirty-telemetry.throughput.tsv"
cp "$TMP/quiet-telemetry.environment.txt" "$TMP/dirty-telemetry.environment.txt"
sed -i "s|$TMP/quiet-telemetry.telemetry.tsv|$TMP/dirty-telemetry.telemetry.tsv|; s|$TMP/quiet-telemetry.throughput.tsv|$TMP/dirty-telemetry.throughput.tsv|; s/^git_dirty=false$/git_dirty=true/" "$TMP/dirty-telemetry.environment.txt"
if python3 "$REVIEW" "$TMP/dirty-telemetry.tsv" > "$TMP/dirty-telemetry.out" 2>&1; then
  echo 'performance reviewer accepted dirty source provenance' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/dirty-telemetry.out"
grep -q 'source tree is dirty' "$TMP/dirty-telemetry.out"
noisy="$TMP/noisy-telemetry.tsv"
if python3 "$REVIEW" "$noisy" > "$TMP/noisy-telemetry.out"; then
  echo 'performance reviewer accepted PSI-confounded telemetry as PASS' >&2; exit 1
else [ "$?" -eq 3 ]; fi
grep -q '^acceptance_status=CONFOUNDED_INCONCLUSIVE$' "$TMP/noisy-telemetry.out"
grep -q '^CONFOUNDED_INCONCLUSIVE: predeclared PSI control failed' "$TMP/noisy-telemetry.out"
cp "$TMP/quiet-telemetry.tsv" "$TMP/unordered-telemetry.tsv"
cp "$TMP/quiet-telemetry.samples.tsv" "$TMP/unordered-telemetry.samples.tsv"
cp "$TMP/quiet-telemetry.environment.txt" "$TMP/unordered-telemetry.environment.txt"
cp "$TMP/quiet-telemetry.telemetry.tsv" "$TMP/unordered-telemetry.telemetry.tsv"
cp "$TMP/quiet-telemetry.throughput.tsv" "$TMP/unordered-telemetry.throughput.tsv"
sed -i "s|$TMP/quiet-telemetry.telemetry.tsv|$TMP/unordered-telemetry.telemetry.tsv|; s|$TMP/quiet-telemetry.throughput.tsv|$TMP/unordered-telemetry.throughput.tsv|" "$TMP/unordered-telemetry.environment.txt"
python3 - "$TMP/unordered-telemetry.telemetry.tsv" <<'PY'
import csv, pathlib, sys
p=pathlib.Path(sys.argv[1])
with p.open(newline='') as source:
    rows=list(csv.reader(source,delimiter='\t'))
rows[1],rows[2]=rows[2],rows[1]
with p.open('w',newline='') as output:
    csv.writer(output,delimiter='\t',lineterminator='\n').writerows(rows)
PY
if python3 "$REVIEW" "$TMP/unordered-telemetry.tsv" > "$TMP/unordered-telemetry.out" 2>&1; then
  echo 'performance reviewer accepted out-of-order interval telemetry' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/unordered-telemetry.out"
grep -q 'telemetry timestamps are not strictly increasing' "$TMP/unordered-telemetry.out"
cp "$TMP/quiet-telemetry.tsv" "$TMP/unordered-throughput.tsv"
cp "$TMP/quiet-telemetry.samples.tsv" "$TMP/unordered-throughput.samples.tsv"
cp "$TMP/quiet-telemetry.telemetry.tsv" "$TMP/unordered-throughput.telemetry.tsv"
cp "$TMP/quiet-telemetry.throughput.tsv" "$TMP/unordered-throughput.throughput.tsv"
cp "$TMP/quiet-telemetry.environment.txt" "$TMP/unordered-throughput.environment.txt"
sed -i "s|$TMP/quiet-telemetry.telemetry.tsv|$TMP/unordered-throughput.telemetry.tsv|; s|$TMP/quiet-telemetry.throughput.tsv|$TMP/unordered-throughput.throughput.tsv|" "$TMP/unordered-throughput.environment.txt"
python3 - "$TMP/unordered-throughput.throughput.tsv" <<'PY'
import csv, pathlib, sys
p=pathlib.Path(sys.argv[1])
with p.open(newline='') as source:
    rows=list(csv.reader(source,delimiter='\t'))
rows[1],rows[2]=rows[2],rows[1]
with p.open('w',newline='') as output:
    csv.writer(output,delimiter='\t',lineterminator='\n').writerows(rows)
PY
if python3 "$REVIEW" "$TMP/unordered-throughput.tsv" > "$TMP/unordered-throughput.out" 2>&1; then
  echo 'performance reviewer accepted out-of-order throughput counters' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q '^acceptance_status=INCOMPLETE$' "$TMP/unordered-throughput.out"
grep -q 'throughput raw: throughput raw timestamps are not strictly increasing' "$TMP/unordered-throughput.out"

printf '%s\n' 'performance review tests: ok (PASS/FAIL/INCOMPLETE/CONFOUNDED_INCONCLUSIVE/INCONCLUSIVE, legacy PSI gate, paired ratios and evidence-order guards)'
