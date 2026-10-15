#!/bin/bash
# Static quota sweep in a unique temporary user slice. Never touches the
# installed router service or heavy-workload.slice.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: benchmarks/quota-benchmark.sh

Environment:
  WORKERS=2          CPU workers per trial (default 2)
  WORK=100000000     integer-mixing iterations per worker (default 100M)
  REPS=3             repetitions per quota (default 3)
  QUOTAS=100,200,400 comma-separated integer quota percentages (default 100,150,200,250,300,350,400)
  RESULT=PATH        new TSV path (default: unique file in /tmp)

The benchmark changes CPUQuota only on its uniquely named temporary user
slice. It does not stop/restart workload-router.service or change the
production heavy-workload.slice. Existing result or manifest paths are refused.
EOF
}
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then usage; exit 0; fi
if [ "$#" -ne 0 ]; then usage >&2; exit 2; fi

WORKERS=${WORKERS:-2}
WORK=${WORK:-100000000}
REPS=${REPS:-3}
QUOTAS=${QUOTAS:-100,150,200,250,300,350,400}
RESULT=${RESULT:-"/tmp/linux-workload-guard-quota-$(date +%Y%m%d-%H%M%S)-$$.tsv"}
CGROUP_ROOT=${CGROUP_ROOT:-/sys/fs/cgroup}

is_positive_int() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
is_positive_int "$WORKERS" && is_positive_int "$WORK" && is_positive_int "$REPS" || {
    echo 'WORKERS, WORK, and REPS must be positive integers' >&2; exit 2;
}
IFS=',' read -r -a LEVELS <<< "$QUOTAS"
[ "${#LEVELS[@]}" -gt 0 ] || { echo 'QUOTAS must contain at least one percentage' >&2; exit 2; }
declare -A seen=()
for q in "${LEVELS[@]}"; do
    is_positive_int "$q" || { echo "invalid quota percentage: $q" >&2; exit 2; }
    [ "$q" -ge 1 ] && [ "$q" -le 1000 ] || { echo "quota must be in [1,1000]: $q" >&2; exit 2; }
    [ -z "${seen[$q]+x}" ] || { echo "duplicate quota percentage: $q" >&2; exit 2; }
    seen[$q]=1
done

OUT_DIR=$(dirname -- "$RESULT")
mkdir -p "$OUT_DIR"
ENV_RESULT="${RESULT%.tsv}.environment.txt"
if [ -e "$RESULT" ] || [ -L "$RESULT" ] || [ -e "$ENV_RESULT" ] || [ -L "$ENV_RESULT" ]; then
    echo "refusing to overwrite benchmark evidence: $RESULT or $ENV_RESULT already exists" >&2
    exit 1
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/lwg-quota-bench.XXXXXX")
BENCH_ID="$(date +%s)$$"
# Avoid hyphens in the slice name: systemd interprets them as a nested slice
# hierarchy and would leave timestamp-specific ancestor units behind.
SLICE="lwgquotabench${BENCH_ID}.slice"
SCOPE_PREFIX="lwgquotabench${BENCH_ID}"
CGROUP=""
SLICE_STARTED=0
MANIFEST_CREATED=0
finish() {
    rc=$?
    trap - EXIT INT TERM HUP
    if [ "$SLICE_STARTED" -eq 1 ]; then
        systemctl --user stop "$SLICE" >/dev/null 2>&1 || true
        systemctl --user reset-failed "$SLICE" >/dev/null 2>&1 || true
    fi
    if [ "$rc" -ne 0 ] && [ "$MANIFEST_CREATED" -eq 1 ] && [ -f "$ENV_RESULT" ]; then
        rows=0
        [ ! -f "$RESULT" ] || rows=$(awk 'END {print (NR>0 ? NR-1 : 0)}' "$RESULT")
        printf 'ended_at=%s\nrun_status=INCOMPLETE\nexit_code=%s\ncompleted_trials=%s\n' \
            "$(date --iso-8601=seconds)" "$rc" "$rows" >> "$ENV_RESULT"
    fi
    rm -rf -- "$TMP"
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

cat > "$TMP/cpubench.c" <<'EOF'
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
int main(int argc, char **argv) {
    uint64_t n = argc > 1 ? strtoull(argv[1], 0, 10) : 100000000ULL;
    volatile uint64_t x = 0x123456789abcdef0ULL;
    for (uint64_t i = 0; i < n; i++) {
        x ^= x << 7; x ^= x >> 9; x *= 0x9e3779b97f4a7c15ULL;
    }
    printf("%llu\n", (unsigned long long)x);
    return 0;
}
EOF
gcc -O2 -o "$TMP/cpubench" "$TMP/cpubench.c"

systemctl --user start "$SLICE"
SLICE_STARTED=1
systemctl --user set-property "$SLICE" CPUQuota="${LEVELS[0]}%" >/dev/null
CGROUP_PATH=$(systemctl --user show "$SLICE" -p ControlGroup --value)
case "$CGROUP_PATH" in
    /*) CGROUP="${CGROUP_ROOT%/}$CGROUP_PATH";;
    *) echo "temporary slice has invalid ControlGroup: $CGROUP_PATH" >&2; exit 1;;
esac
[ -r "$CGROUP/cpu.stat" ] || { echo "temporary slice cpu.stat unavailable: $CGROUP/cpu.stat" >&2; exit 1; }

read_stat() {
    awk '
      $1=="usage_usec" {u=$2; uc++}
      $1=="nr_throttled" {t=$2; tc++}
      $1=="nr_periods" {p=$2; pc++}
      END {
        if(uc!=1 || tc!=1 || pc!=1 || u!~/^[0-9]+$/ || t!~/^[0-9]+$/ || p!~/^[0-9]+$/) exit 1
        print u, t, p
      }' "$CGROUP/cpu.stat"
}
read_psi() {
    awk '/^some /{
        for(i=1;i<=NF;i++) {
            if($i~/^avg10=/) {split($i,a,"="); a10=a[2]}
            if($i~/^avg60=/) {split($i,b,"="); a60=b[2]}
        }
    } END {
        numeric="^[0-9]+([.][0-9]+)?$"
        if(a10!~numeric || a60!~numeric || a10+0>100 || a60+0>100) print "n/a n/a"
        else printf "%.2f %.2f\n",a10+0,a60+0
    }' /proc/pressure/cpu
}
monotonic_ns() { python3 -c 'import time; print(time.monotonic_ns())'; }

# Create new evidence atomically enough to refuse both existing files and
# symlinks. The manifest explicitly distinguishes the isolated slice from the
# installed service and records the workload/cgroup setup.
(set -o noclobber; : > "$RESULT") || { echo "cannot create new result file: $RESULT" >&2; exit 1; }
(set -o noclobber; : > "$ENV_RESULT") || { echo "cannot create new manifest: $ENV_RESULT" >&2; exit 1; }
MANIFEST_CREATED=1
printf 'quota\trep\telapsed_s\tcpu_s\tthrottle_periods\tperiods\tpsi_some_avg10\tpsi_some_avg60\n' > "$RESULT"
{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host=%s\n' "$(uname -a)"
    grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release || true
    printf 'logical_cpus=%s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
    printf 'cgroup_mode=%s\n' "$(if [ -f "$CGROUP_ROOT/cgroup.controllers" ]; then echo v2; else echo unknown; fi)"
    printf 'systemd=%s\n' "$(systemctl --version | head -1)"
    printf 'python=%s\n' "$(python3 --version 2>&1)"
    printf 'gcc=%s\n' "$(gcc --version | head -1)"
    printf 'workers=%s\nwork_iterations_per_worker=%s\nrepetitions=%s\nquotas=%s\n' "$WORKERS" "$WORK" "$REPS" "$QUOTAS"
    printf 'temporary_slice=%s\ntemporary_cgroup=%s\n' "$SLICE" "$CGROUP_PATH"
    printf 'temporary_cpu_max=%s\nparent_cpu_max=%s\n' "$(cat "$CGROUP/cpu.max" 2>/dev/null || echo unavailable)" "$(cat "$(dirname -- "$CGROUP")/cpu.max" 2>/dev/null || echo unavailable)"
    printf 'production_service_touched=false\nproduction_slice_touched=false\n'
    printf 'result_path=%s\ncommand=WORKERS=%s WORK=%s REPS=%s QUOTAS=%s RESULT=%s %s\n' "$RESULT" "$WORKERS" "$WORK" "$REPS" "$QUOTAS" "$RESULT" "$0"
} >> "$ENV_RESULT"

for round in $(seq 1 "$REPS"); do
    # Rotate order between repetitions to reduce simple time/order bias.
    if (( round % 2 == 1 )); then order=("${LEVELS[@]}"); else order=(); for ((i=${#LEVELS[@]}-1;i>=0;i--)); do order+=("${LEVELS[$i]}"); done; fi
    for q in "${order[@]}"; do
        systemctl --user set-property "$SLICE" CPUQuota="${q}%" >/dev/null
        sleep 1
        read -r bu bt bp < <(read_stat) || { echo 'invalid initial cgroup counters' >&2; exit 1; }
        start=$(monotonic_ns)
        systemd-run --user --scope --quiet --slice="$SLICE" --unit="${SCOPE_PREFIX}-r${round}-q${q}.scope" env \
            WORKERS="$WORKERS" BIN="$TMP/cpubench" WORK="$WORK" \
            bash -c 'for i in $(seq 1 "$WORKERS"); do "$BIN" "$WORK" >/dev/null & done; wait'
        end=$(monotonic_ns)
        read -r au at ap < <(read_stat) || { echo 'invalid final cgroup counters' >&2; exit 1; }
        [[ "$au" =~ ^[0-9]+$ && "$at" =~ ^[0-9]+$ && "$ap" =~ ^[0-9]+$ ]] || { echo 'malformed cgroup counters' >&2; exit 1; }
        (( au >= bu && at >= bt && ap >= bp )) || { echo 'cgroup counters reset during trial' >&2; exit 1; }
        elapsed=$(awk -v s="$start" -v e="$end" 'BEGIN{if(e<s) exit 1; printf "%.3f",(e-s)/1e9}')
        cpu=$(awk -v a="$au" -v b="$bu" 'BEGIN{printf "%.3f",(a-b)/1e6}')
        psi_values=$(read_psi)
        read -r psi10 psi60 <<< "$psi_values"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$q" "$round" "$elapsed" "$cpu" "$((at-bt))" "$((ap-bp))" "$psi10" "$psi60" >> "$RESULT"
        sleep 1
    done
done

printf 'ended_at=%s\nrun_status=COMPLETE\ntrial_count=%s\n' "$(date --iso-8601=seconds)" "$((REPS * ${#LEVELS[@]}))" >> "$ENV_RESULT"
cat "$RESULT"
