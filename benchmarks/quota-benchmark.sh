#!/bin/bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORKERS=${WORKERS:-8}
WORK=${WORK:-250000000}
REPS=${REPS:-3}
RESULT=${RESULT:-"/tmp/linux-workload-guard-quota-results.tsv"}
CG=${CGROUP:-"$(systemctl --user show heavy-workload.slice -p ControlGroup --value)"}
CG="/sys/fs/cgroup$CG"

TMP=$(mktemp -d)
ROUTER_WAS_ACTIVE=0
if systemctl --user is-active --quiet workload-router.service; then
    ROUTER_WAS_ACTIVE=1
fi
cleanup() {
    set +e
    rm -rf "$TMP"
    systemctl --user set-property heavy-workload.slice CPUQuota=400% >/dev/null 2>&1
    if [ "$ROUTER_WAS_ACTIVE" -eq 1 ]; then
        systemctl --user start workload-router.service >/dev/null 2>&1
    fi
}
trap cleanup EXIT INT TERM

cat >"$TMP/cpubench.c" <<'EOF'
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
int main(int argc, char **argv) {
    uint64_t n = argc > 1 ? strtoull(argv[1], 0, 10) : 250000000ULL;
    volatile uint64_t x = 0x123456789abcdef0ULL;
    for (uint64_t i = 0; i < n; i++) {
        x ^= x << 7; x ^= x >> 9; x *= 0x9e3779b97f4a7c15ULL;
    }
    printf("%llu\n", (unsigned long long)x);
    return 0;
}
EOF
gcc -O2 -march=native -o "$TMP/cpubench" "$TMP/cpubench.c"

read_psi() {
    awk '/^some /{
        for (i=1;i<=NF;i++) {
            if ($i ~ /^avg10=/) { split($i,a,"="); a10=a[2] }
            if ($i ~ /^avg60=/) { split($i,b,"="); a60=b[2] }
        }
    } END { print a10 "\t" a60 }' /proc/pressure/cpu
}

read_stat() {
    awk '/^usage_usec /{u=$2} /^nr_throttled /{t=$2} /^nr_periods /{p=$2}
         END { print u,t,p }' "$CG/cpu.stat"
}

mkdir -p "$(dirname -- "$RESULT")"
printf 'quota\trep\telapsed_s\tcpu_s\tthrottle_periods\tperiods\tpsi_some_avg10\tpsi_some_avg60\n' >"$RESULT"

systemctl --user stop workload-router.service

for round in $(seq 1 "$REPS"); do
    case "$round" in
        1) levels=(100 250 400 150 300 200 350) ;;
        2) levels=(300 150 350 200 400 100 250) ;;
        3) levels=(200 350 100 300 150 400 250) ;;
        *) levels=(100 150 200 250 300 350 400) ;;
    esac
    for q in "${levels[@]}"; do
        systemctl --user set-property heavy-workload.slice CPUQuota="${q}%"
        sleep 2
        read -r bu bt bp < <(read_stat)
        start=$(date +%s%N)
        systemd-run --user --scope --quiet --slice=heavy-workload.slice env \
            WORKERS="$WORKERS" BIN="$TMP/cpubench" WORK="$WORK" \
            bash -c 'for i in $(seq 1 "$WORKERS"); do "$BIN" "$WORK" >/dev/null & done; wait'
        end=$(date +%s%N)
        read -r au at ap < <(read_stat)
        elapsed=$(awk -v s="$start" -v e="$end" 'BEGIN{printf "%.3f",(e-s)/1e9}')
        cpu=$(awk -v a="$au" -v b="$bu" 'BEGIN{printf "%.3f",(a-b)/1e6}')
        read -r psi10 psi60 < <(read_psi)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$q" "$round" "$elapsed" "$cpu" "$((at-bt))" "$((ap-bp))" "$psi10" "$psi60" >>"$RESULT"
        sleep 2
    done
done

cat "$RESULT"
