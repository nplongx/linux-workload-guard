#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STAMP=${STAMP:-$(date +%F)}
OUT="${RESULT:-$ROOT/benchmarks/results/quota-stability-live-${STAMP}.tsv}"
LOG="${LOG_RESULT:-$ROOT/benchmarks/results/quota-stability-live-logs-${STAMP}.tsv}"
UNIT=heavy-workload.slice
SERVICE=workload-router.service
WORKERS=${WORKERS:-2}
LOAD_SECONDS=${LOAD_SECONDS:-28}
RECOVERY_SECONDS=${RECOVERY_SECONDS:-21}
HOST_CPUS=$(nproc)
MAX_WORKERS=$((HOST_CPUS / 2))
(( MAX_WORKERS < 1 )) && MAX_WORKERS=1
(( MAX_WORKERS > 4 )) && MAX_WORKERS=4
if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ ]] || (( WORKERS > MAX_WORKERS )); then
  echo "Refusing test: WORKERS must be 1..${MAX_WORKERS} (at most half of ${HOST_CPUS} logical CPUs, capped at 4)." >&2
  exit 2
fi
if ! [[ "$LOAD_SECONDS" =~ ^[0-9]+$ ]] || (( LOAD_SECONDS < 10 || LOAD_SECONDS > 90 )); then
  echo "Refusing test: LOAD_SECONDS must be 10..90." >&2; exit 2
fi
if ! [[ "$RECOVERY_SECONDS" =~ ^[0-9]+$ ]] || (( RECOVERY_SECONDS < 10 || RECOVERY_SECONDS > 60 )); then
  echo "Refusing test: RECOVERY_SECONDS must be 10..60." >&2; exit 2
fi
# This cgroup may contain real routed workloads; never start CPU stress implicitly.
if [[ "${CONFIRM_SHARED_SLICE_STRESS:-}" != YES ]]; then
  echo "Refusing test: it runs a CPU burner in the shared ${UNIT} cgroup for ${LOAD_SECONDS}s." >&2
  echo "Review the impact, then explicitly opt in with CONFIRM_SHARED_SLICE_STRESS=YES." >&2
  exit 2
fi
if ! systemctl --user is-active --quiet "$SERVICE"; then echo 'Refusing test: router service is not active' >&2; exit 2; fi
INITIAL_STATE=$(systemctl --user is-active "$SERVICE")
INITIAL_USEC=$(systemctl --user show "$UNIT" -p CPUQuotaPerSecUSec --value)
if [[ "$INITIAL_USEC" == infinity || -z "$INITIAL_USEC" ]]; then echo "Refusing test: cannot safely snapshot quota ($INITIAL_USEC)" >&2; exit 2; fi
INITIAL_PCT=$(awk -v x="$INITIAL_USEC" 'BEGIN { if (x ~ /ms$/) {sub(/ms$/, "", x); printf "%.0f", x/10} else if (x ~ /s$/) {sub(/s$/, "", x); printf "%.0f", x*100} else {exit 1} }')
RESTORED=0
cleanup() {
  set +e
  [[ -n "${SCOPE:-}" ]] && systemctl --user stop "$SCOPE" >/dev/null 2>&1 || true
  [[ -n "${BURN_SCRIPT:-}" ]] && rm -f "$BURN_SCRIPT"
  systemctl --user set-property "$UNIT" "CPUQuota=${INITIAL_PCT}%" >/dev/null 2>&1
  if [[ "$INITIAL_STATE" == active ]]; then systemctl --user start "$SERVICE" >/dev/null 2>&1; else systemctl --user stop "$SERVICE" >/dev/null 2>&1; fi
  RESTORED=1
}
trap cleanup EXIT INT TERM HUP
mkdir -p "$(dirname "$OUT")"
printf 'elapsed_s\tphase\tquota_pct\tpsi_some_avg10\tusage_usec\tnr_throttled\tnr_periods\n' > "$OUT"
printf 'timestamp\tmessage\n' > "$LOG"
journalctl --user -u "$SERVICE" --since now --no-pager -o short-iso | tail -1 >> "$LOG" || true
# Bounded CPU workload in the shared heavy slice; at most half the host CPUs (cap 4) for ${LOAD_SECONDS}s.
# Child processes inherit the scope cgroup and are stopped by cleanup on exit/signals.
BURN_SCRIPT=$(mktemp /tmp/workload-guard-quota-burn.XXXXXX.py)
cat > "$BURN_SCRIPT" <<'PY'
import multiprocessing as mp
import os
import time

def burn(until):
    x = 1
    while time.monotonic() < until:
        x = (x * 1664525 + 1013904223) & 0xffffffff

if __name__ == "__main__":
    until = time.monotonic() + int(os.environ.get("LOAD_SECONDS", "28"))
    children = [mp.Process(target=burn, args=(until,)) for _ in range(int(os.environ.get("WORKERS", "2")))]
    for child in children: child.start()
    for child in children: child.join()
PY
START=$(date +%s)
# The process name is intentionally unique to allow cleanup if interrupted.
SCOPE="workload-guard-quota-stress-${STAMP}.scope"
(systemd-run --user --scope --slice="$UNIT" --unit="$SCOPE" env WORKERS="$WORKERS" LOAD_SECONDS="$LOAD_SECONDS" python3 "$BURN_SCRIPT" >"/tmp/${SCOPE}.log" 2>&1) &
LOAD_PID=$!
sleep 1
if ! systemctl --user is-active --quiet "$SCOPE"; then echo "Synthetic workload did not start; inspect /tmp/${SCOPE}.log" >&2; exit 3; fi
CG=$(systemctl --user show "$UNIT" -p ControlGroup --value)
CGFILE="/sys/fs/cgroup${CG}/cpu.stat"
TOTAL_SECONDS=$((LOAD_SECONDS + RECOVERY_SECONDS))
for t in $(seq 0 1 "$TOTAL_SECONDS"); do
  if (( t < LOAD_SECONDS )); then phase=load; else phase=recovery; fi
  q_usec=$(systemctl --user show "$UNIT" -p CPUQuotaPerSecUSec --value)
  q=$(awk -v x="$q_usec" 'BEGIN {if(x ~ /ms$/){sub(/ms$/, "",x);printf "%.0f",x/10}else if(x ~ /s$/){sub(/s$/, "",x);printf "%.0f",x*100}else print "NA"}')
  read -r psi _ < <(awk '/^some /{for(i=1;i<=NF;i++)if($i~/^avg10=/){split($i,a,"=");print a[2]}}' /proc/pressure/cpu)
  read -r usage throttled periods < <(awk '/^usage_usec /{u=$2}/^nr_throttled /{t=$2}/^nr_periods /{p=$2}END{print u,t,p}' "$CGFILE")
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$t" "$phase" "$q" "${psi:-NA}" "${usage:-0}" "${throttled:-0}" "${periods:-0}" >> "$OUT"
  sleep 1
done
wait "$LOAD_PID" || true
journalctl --user -u "$SERVICE" --since "@$START" --no-pager -o short-iso | grep -E 'quota changed|quota shadow|WARNING|ERROR' | while IFS= read -r line; do printf '%s\t%s\n' "$(date -Is)" "$line" >> "$LOG"; done || true
printf 'initial_quota_pct=%s workers=%s load_s=%s recovery_s=%s\n' "$INITIAL_PCT" "$WORKERS" "$LOAD_SECONDS" "$RECOVERY_SECONDS"
awk -F '\t' 'NR>1{if(NR==2){prev=$3} else if($3!=prev){changes++; printf "quota_change elapsed=%ss phase=%s quota=%s%%\n",$1,$2,$3;prev=$3} if($2=="load"&&$3>maxload)maxload=$3; if($2=="recovery")last=$3} END{printf "quota_changes=%d peak_load_quota=%s%% recovery_end_quota=%s%%\n",changes,maxload,last}' "$OUT"
printf 'wrote %s and %s\n' "$OUT" "$LOG"
