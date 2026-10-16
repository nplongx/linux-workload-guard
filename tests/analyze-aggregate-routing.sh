#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/lwg-aggregate-analysis.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
cd "$ROOT"
python3 benchmarks/analyze-aggregate-routing.py \
  --out "$TMP/pairs.tsv" --summary "$TMP/summary.txt" \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight20-blockA3-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight80-blockB3-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight80-blockB4-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight20-blockA4-pass15-5rep30-2026-10-15.tsv \
  > "$TMP/analyzer.stdout"
grep -q '^paired_rows=20$' "$TMP/summary.txt"
grep -q '^all_pairs_retained=true$' "$TMP/summary.txt"
grep -q '^block=B4 weight=80 n=5 median=0.824 range=\[0.671,1.035\]' "$TMP/summary.txt"
grep -q '^pass15_predeclared_weight_effect_interpretation=CONFOUNDED_INCONCLUSIVE$' "$TMP/summary.txt"
grep -q '^pass15_contrast_B3_minus_A3=-0.090' "$TMP/summary.txt"
grep -q '^pass15_contrast_B4_minus_A4=-0.165' "$TMP/summary.txt"
python3 - "$TMP/pairs.tsv" <<'PY'
import csv, sys
with open(sys.argv[1], newline="") as source:
    rows = list(csv.DictReader(source, delimiter="\t"))
assert len(rows) == 20, len(rows)
for block in ("A3", "B3", "B4", "A4"):
    selected = [row for row in rows if row["block"] == block]
    assert len(selected) == 5, (block, len(selected))
assert {row["acceptance_status"] for row in rows if row["block"] == "B4"} == {"FAIL"}
assert all(row["throughput_ratio"] not in ("", None) for row in rows if row["block"] in ("A3", "B3", "B4", "A4"))
print("aggregate analysis regression: ok")
PY

# A valid-looking report must be bound to the exact result path it describes;
# copying an unrelated PASS report beside a different TSV is not acceptance.
source=benchmarks/results/routing-contention-scope-aggregate-synthetic-weight20-blockA3-pass15-5rep30-2026-10-15.tsv
cp "$source" "$TMP/copied-report.tsv"
cp "${source%.tsv}.environment.txt" "$TMP/copied-report.environment.txt"
cp "${source%.tsv}.acceptance.txt" "$TMP/copied-report.acceptance.txt"
if python3 benchmarks/analyze-aggregate-routing.py \
  --out "$TMP/copied-report-pairs.tsv" --summary "$TMP/copied-report-summary.txt" \
  "$TMP/copied-report.tsv" > "$TMP/copied-report.out" 2>&1; then
  echo 'aggregate analyzer accepted an acceptance report copied from another result' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q 'acceptance report result_tsv does not bind to supplied result' "$TMP/copied-report.out"

# Conflicting duplicate status keys are ambiguous evidence, even when the
# first occurrence is valid; they must not receive last-key-wins semantics.
cp "${source%.tsv}.acceptance.txt" "$TMP/duplicate-status.acceptance.txt"
cp "${source%.tsv}.environment.txt" "$TMP/duplicate-status.environment.txt"
cp "$source" "$TMP/duplicate-status.tsv"
printf 'acceptance_status=PASS\n' >> "$TMP/duplicate-status.acceptance.txt"
if python3 benchmarks/analyze-aggregate-routing.py \
  --out "$TMP/duplicate-status-pairs.tsv" --summary "$TMP/duplicate-status-summary.txt" \
  "$TMP/duplicate-status.tsv" > "$TMP/duplicate-status.out" 2>&1; then
  echo 'aggregate analyzer accepted duplicate evaluator status keys' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q 'duplicate key.*acceptance_status' "$TMP/duplicate-status.out"

# Aggregate analysis must reject symlinked evidence companions before parsing
# metadata, even when the symlink target is a valid old acceptance report.
cp "$source" "$TMP/linked-acceptance.tsv"
cp "${source%.tsv}.environment.txt" "$TMP/linked-acceptance.environment.txt"
ln -s "$ROOT/${source%.tsv}.acceptance.txt" "$TMP/linked-acceptance.acceptance.txt"
if python3 benchmarks/analyze-aggregate-routing.py \
  --out "$TMP/linked-acceptance-pairs.tsv" --summary "$TMP/linked-acceptance-summary.txt" \
  "$TMP/linked-acceptance.tsv" > "$TMP/linked-acceptance.out" 2>&1; then
  echo 'aggregate analyzer accepted a symlinked acceptance report' >&2; exit 1
else [ "$?" -eq 2 ]; fi
grep -q 'missing or unsafe manifest/evaluator output' "$TMP/linked-acceptance.out"

# Historical summaries may still be used for descriptive plots, but an ABBA
# run explicitly bound to preregistration cannot close a gate without the new
# per-run evidence digest/path fields.
python3 - "$ROOT" "$source" <<'PY'
import importlib.util, pathlib, sys
root=pathlib.Path(sys.argv[1]); result=pathlib.Path(sys.argv[2])
path=root/'benchmarks/analyze-aggregate-routing.py'
spec=importlib.util.spec_from_file_location('aggregate_binding_test',path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
manifest=result.with_suffix('.environment.txt'); acceptance_path=result.with_suffix('.acceptance.txt')
env=module.manifest_values(manifest); report=module.manifest_values(acceptance_path)
env['preregistration_required']='true'
errors=module.verify_acceptance_binding(result,manifest,acceptance_path,env,report)
assert any('missing the per-run evidence checksum digest' in item for item in errors), errors
assert any('missing the per-run evidence checksum path' in item for item in errors), errors
print('preregistered evidence binding regression: ok (missing manifest digest/path cannot close gate)')
PY

# PSI acceptance must be evaluated per mode/pair, not by averaging the
# percentages across all repetitions. A single noisy pair is sufficient to
# confound attribution; missing PSI is INCONCLUSIVE rather than quiet.
python3 - "$ROOT/benchmarks/analyze-aggregate-routing.py" <<'PY'
import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('aggregate_analyzer',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
blocks={'A':20,'B':80,'B2':80,'A2':20}
def dataset():
    result={}
    for block in blocks:
        result[block]=[]
        for rep in range(1,6):
            result[block].append({
                'rep':str(rep),
                'baseline_host_psi_status':'VALID','router_host_psi_status':'VALID',
                'baseline_host_psi_avg10_median':2.0,'router_host_psi_avg10_median':3.0,
                'baseline_host_psi_gt5_pct':0.0,'host_psi_router_gt5_pct':0.0,
            })
    return result
rows=dataset()
status,detail,imbalance=module.psi_gate_status(rows,blocks)
assert status=='WITHIN_LIMIT' and imbalance==1.0, (status,detail,imbalance)
rows['A'][4]['baseline_host_psi_gt5_pct']=20.1
status,detail,imbalance=module.psi_gate_status(rows,blocks)
assert status=='CONFOUNDED_INCONCLUSIVE' and 'A/rep5/baseline=20.1%' in detail, (status,detail)
rows=dataset()
for block in blocks:
    for row in rows[block]:
        row['router_host_psi_avg10_median']=8.1
status,detail,imbalance=module.psi_gate_status(rows,blocks)
assert status=='CONFOUNDED_INCONCLUSIVE' and imbalance>5, (status,detail,imbalance)
rows=dataset(); rows['B'].pop()
status,detail,imbalance=module.psi_gate_status(rows,blocks)
assert status=='INCONCLUSIVE' and 'expected 5 pairs, observed 4' in detail, (status,detail)
rows=dataset(); rows['B2'][0]['router_host_psi_status']='INVALID'
status,detail,imbalance=module.psi_gate_status(rows,blocks)
assert status=='INCONCLUSIVE' and 'unavailable or incomplete' in detail, (status,detail)
print('aggregate PSI gate regression: ok (per-pair threshold, condition medians, missing-data inconclusive)')
PY
