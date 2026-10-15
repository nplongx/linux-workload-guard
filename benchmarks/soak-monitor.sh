#!/bin/sh
# Monitor-only soak collector. It does not start workloads or change service/config.
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 OUTPUT_DIR [DURATION_SECONDS=86400] [INTERVAL_SECONDS=60]" >&2
  exit 2
fi
OUT=$1
DURATION=${2:-86400}
INTERVAL=${3:-60}
case "$DURATION:$INTERVAL" in *[!0-9:]*|:*|*:) echo 'duration and interval must be positive integers' >&2; exit 2;; esac
[ "$DURATION" -ge 1 ] && [ "$INTERVAL" -ge 1 ] || { echo 'duration and interval must be positive integers' >&2; exit 2; }
mkdir "$OUT" 2>/dev/null || { echo "refusing existing output directory: $OUT" >&2; exit 1; }
START_EPOCH=$(date +%s)
START_ISO=$(date --iso-8601=seconds)
ROUTER_PID_START=$(systemctl --user show workload-router.service -p MainPID --value 2>/dev/null || echo 0)
ROUTER_RESTARTS_START=$(systemctl --user show workload-router.service -p NRestarts --value 2>/dev/null || echo unknown)
cat > "$OUT/environment.txt" <<EOF
started_at=$START_ISO
host=$(uname -a)
python=$(python3 --version 2>&1)
logical_cpus=$(nproc)
cgroup_mode=$(if [ -f /sys/fs/cgroup/cgroup.controllers ]; then echo v2; else echo unknown; fi)
service=workload-router.service
service_initial_main_pid=$ROUTER_PID_START
service_initial_restarts=$ROUTER_RESTARTS_START
duration_seconds=$DURATION
interval_seconds=$INTERVAL
mode=monitor-only; no workload generation; no configuration or quota writes
EOF
printf 'timestamp\tactive_state\tsub_state\tmain_pid\trestarts\texit_status\tcpu_psi_some_avg10_pct\tcpu_psi_some_avg60_pct\tcpu_psi_full_avg10_pct\theavy_cpu_quota_property\trouter_cpu_seconds\trouter_rss_kib\trouter_runqueue_delay_delta_ms\trouted_processes\n' > "$OUT/samples.tsv"
printf 'timestamp\tpriority\tmessage\n' > "$OUT/errors.tsv"
PREV_PID=''; PREV_SCHED_NS=''
sample() {
  ts=$(date --iso-8601=seconds)
  props=$(systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus --value 2>/dev/null || true)
  active=$(printf '%s\n' "$props" | sed -n '1p'); sub=$(printf '%s\n' "$props" | sed -n '2p')
  pid=$(printf '%s\n' "$props" | sed -n '3p'); restarts=$(printf '%s\n' "$props" | sed -n '4p'); status=$(printf '%s\n' "$props" | sed -n '5p')
  psi=$(awk '$1=="some" {for(i=2;i<=NF;i++){split($i,a,"="); if(a[1]=="avg10")s10=a[2]; if(a[1]=="avg60")s60=a[2]}} $1=="full" {for(i=2;i<=NF;i++){split($i,a,"="); if(a[1]=="avg10")f10=a[2]}} END {if(s10=="")s10="n/a"; if(s60=="")s60="n/a"; if(f10=="")f10="n/a"; printf "%s %s %s",s10,s60,f10}' /proc/pressure/cpu 2>/dev/null || printf 'n/a n/a n/a')
  set -- $psi
  psi10=$1; psi60=$2; psifull=$3
  quota=$(systemctl --user show heavy-workload.slice -p CPUQuotaPerSecUSec --value 2>/dev/null || echo n/a)
  cpu=n/a; rss=n/a; sched_delta=n/a; sched_now=''
  case "$pid" in ''|0|*[!0-9]*) ;; *)
    if [ -r "/proc/$pid/stat" ]; then
      cpu=$(python3 - "$pid" <<'PY'
import os,sys
try:
 s=open('/proc/'+sys.argv[1]+'/stat').read(); f=s[s.rfind(')')+2:].split()
 print('%.2f' % ((int(f[11])+int(f[12]))/os.sysconf('SC_CLK_TCK')))
except Exception: print('n/a')
PY
)
      rss=$(awk '/^VmRSS:/ {print $2; found=1} END {if(!found) print "n/a"}' "/proc/$pid/status" 2>/dev/null || echo n/a)
      sched_now=$(awk '{print $2; exit}' "/proc/$pid/schedstat" 2>/dev/null || true)
      case "$sched_now" in ''|*[!0-9]*) sched_now='';; esac
      case "$PREV_SCHED_NS" in ''|*[!0-9]*) ;; *)
        if [ "$pid" = "$PREV_PID" ] && [ -n "$sched_now" ] && [ "$sched_now" -ge "$PREV_SCHED_NS" ]; then
          sched_delta=$(awk -v n="$sched_now" -v p="$PREV_SCHED_NS" 'BEGIN {printf "%.3f", (n-p)/1000000}')
        fi;;
      esac
    fi
  esac
  PREV_PID=$pid; PREV_SCHED_NS=$sched_now
  state_file=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/linux-workload-guard/routes.tsv
  routes=0
  if [ -r "$state_file" ]; then routes=$(awk 'NF>=6 {n++} END {print n+0}' "$state_file"); fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "${active:-n/a}" "${sub:-n/a}" "${pid:-n/a}" "${restarts:-n/a}" "${status:-n/a}" "$psi10" "$psi60" "$psifull" "${quota:-n/a}" "$cpu" "$rss" "$sched_delta" "$routes" >> "$OUT/samples.tsv"
  if [ "${active:-unknown}" != active ]; then
    printf '%s\tERROR\tservice state %s/%s pid=%s status=%s\n' "$ts" "${active:-n/a}" "${sub:-n/a}" "${pid:-n/a}" "${status:-n/a}" >> "$OUT/errors.tsv"
  fi
}
finish() {
  rc=$?
  END_ISO=$(date --iso-8601=seconds)
  END_EPOCH=$(date +%s)
  restarts_end=$(systemctl --user show workload-router.service -p NRestarts --value 2>/dev/null || echo unknown)
  {
    printf 'ended_at=%s\n' "$END_ISO"
    printf 'elapsed_seconds=%s\n' "$((END_EPOCH-START_EPOCH))"
    printf 'exit_code=%s\n' "$rc"
    printf 'service_final_restarts=%s\n' "$restarts_end"
  } >> "$OUT/environment.txt"
  if command -v journalctl >/dev/null 2>&1; then
    journalctl --user -u workload-router.service --since "$START_ISO" --no-pager > "$OUT/service-journal.log" 2>&1 || true
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'soak monitor started: %s; output=%s; duration=%ss; interval=%ss\n' "$START_ISO" "$OUT" "$DURATION" "$INTERVAL"
while :; do
  now=$(date +%s)
  [ "$((now-START_EPOCH))" -lt "$DURATION" ] || break
  sample
  sleep "$INTERVAL"
done
sample
printf 'soak monitor completed: %s\n' "$(date --iso-8601=seconds)"
