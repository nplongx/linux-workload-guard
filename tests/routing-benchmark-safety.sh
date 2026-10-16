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

# Dangling symlinks also occupy an artifact path. The collector must not follow
# them to create a target file or overwrite untrusted destinations.
ln -s "$TMP/should-not-be-created.tsv" "$TMP/dangling.samples.tsv"
if WORKLOAD_KIND=synthetic RESULT="$TMP/dangling.tsv" DURATION=20 "$BENCH" >"$TMP/dangling.out" 2>"$TMP/dangling.err"; then
  echo 'routing benchmark accepted a dangling sidecar symlink' >&2; exit 1
fi
grep -q 'refusing to overwrite existing benchmark evidence' "$TMP/dangling.err"
[ ! -e "$TMP/should-not-be-created.tsv" ]

# The routed trial must remain inside the same aggregate 100% parent budget;
# a sibling heavy slice would accidentally grant a second CPU's worth of quota.
grep -q 'HEAVY_UNIT = f"{PARENT_UNIT\[:-6\]}-heavy.slice"' "$ROOT/benchmarks/routing-contention.py"
grep -q 'both modes share the same 100% parent aggregate ceiling' "$ROOT/benchmarks/routing-contention.py"
grep -q 'capacity_model=equal aggregate CPU ceiling' "$ROOT/benchmarks/routing-contention.py"
grep -q 'worker_iterations_per_sec' "$ROOT/benchmarks/routing-contention.py"
grep -q 'router_cpu_pct_one_core' "$ROOT/benchmarks/routing-contention.py"
grep -q 'router_peak_rss_kib' "$ROOT/benchmarks/routing-contention.py"
grep -q 'candidate_acceptance=5 valid pairs' "$ROOT/benchmarks/routing-contention.py"
grep -q 'identical >=12s elapsed steady window' "$ROOT/benchmarks/routing-contention.py"
grep -q 'git_revision=' "$ROOT/benchmarks/routing-contention.py"
grep -q 'git_diff_sha256=' "$ROOT/benchmarks/routing-contention.py"
grep -q 'git_dirty=' "$ROOT/benchmarks/routing-contention.py"
grep -q 'status.*--untracked-files=no' "$ROOT/benchmarks/routing-contention.py"
grep -q 'ls-files.*--others.*--exclude-standard' "$ROOT/benchmarks/routing-contention.py"
grep -q 'loadavg_1m_start' "$ROOT/benchmarks/routing-contention.py"
grep -q 'WORKLOAD_KIND must be synthetic, terminal-openssl, terminal-gzip, terminal-build, or terminal-repo-cli' "$ROOT/benchmarks/routing-contention.py"
grep -q 'WORKLOAD_GUARD_AGGREGATE_ROUTING' "$ROOT/benchmarks/routing-contention.py"
grep -q 'lwg-workload-' "$ROOT/benchmarks/routing-contention.py"
grep -q 'workers_routed' "$ROOT/benchmarks/routing-contention.py"
grep -q 'scope cpu.stat usage_usec' "$ROOT/benchmarks/routing-contention.py"
grep -q 'CPUWeight' "$ROOT/bin/workload-router.py"
grep -q 'terminal-build' "$ROOT/benchmarks/performance-review.py"
grep -q 'terminal-gzip' "$ROOT/benchmarks/performance-review.py"
grep -q 'openssl", "speed"' "$ROOT/benchmarks/routing-contention.py"
grep -q 'Popen(\["gzip","-1","-c","/dev/zero"\]' "$ROOT/benchmarks/routing-contention.py"
grep -q 'worker_throughput_per_sec' "$ROOT/benchmarks/routing-contention.py"
grep -q 'RESULT must end in .tsv' "$ROOT/benchmarks/routing-contention.py"
grep -q 'write_manifest(RESULT)' "$ROOT/benchmarks/routing-contention.py"
grep -q 'not cleanup_ok or not evidence_sealed' "$ROOT/benchmarks/routing-contention.py"

# Unknown workload modes must fail before creating evidence or temporary units.
if WORKLOAD_KIND=unknown RESULT="$TMP/unknown.tsv" DURATION=20 "$BENCH" >/dev/null 2>"$TMP/unknown.err"; then
  echo 'routing benchmark accepted an unknown workload kind' >&2; exit 1
fi
grep -q 'WORKLOAD_KIND must be synthetic, terminal-openssl, terminal-gzip, terminal-build, or terminal-repo-cli' "$TMP/unknown.err"
[ ! -e "$TMP/unknown.tsv" ]

# Provenance must count untracked artifacts independently from tracked source
# dirtiness; the old implementation queried status with --untracked-files=no
# and therefore always recorded an untracked count of zero.
mkdir -p "$TMP/provenance/bin" "$TMP/provenance/benchmarks"
git -C "$TMP/provenance" init -q
git -C "$TMP/provenance" config user.email test@example.invalid
git -C "$TMP/provenance" config user.name "Evidence Test"
printf 'router\n' > "$TMP/provenance/bin/workload-router.py"
printf 'bench\n' > "$TMP/provenance/benchmarks/routing-contention.py"
printf 'review\n' > "$TMP/provenance/benchmarks/performance-review.py"
git -C "$TMP/provenance" add .
git -C "$TMP/provenance" commit -qm baseline
printf 'historical artifact\n' > "$TMP/provenance/benchmarks/preserved-result.tsv"
printf 'changed source\n' >> "$TMP/provenance/bin/workload-router.py"
python3 - "$ROOT/benchmarks/routing-contention.py" "$TMP/provenance" <<'PY'
import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('routing_contention',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
dirty,diff_hash,untracked=module.git_source_state(pathlib.Path(sys.argv[2]))
assert dirty=='true', dirty
assert len(diff_hash)==64 and all(ch in '0123456789abcdef' for ch in diff_hash), diff_hash
assert untracked==1, untracked
print('benchmark provenance dirty/untracked accounting: ok')
PY
git -C "$TMP/provenance" add .
git -C "$TMP/provenance" commit -qm final
python3 - "$ROOT/benchmarks/routing-contention.py" "$TMP/provenance" <<'PY'
import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('routing_contention',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
dirty,diff_hash,untracked=module.git_source_state(pathlib.Path(sys.argv[2]))
assert dirty=='false', dirty
assert len(diff_hash)==64 and untracked==0, (diff_hash,untracked)
print('benchmark provenance clean revision accounting: ok')
PY

printf '%s\n' 'routing benchmark safety: ok (minimum window, no-overwrite, executable wrapper)'
