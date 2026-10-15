#!/bin/sh
# Monitor-only soak collector. It does not start workloads or change service/config.
set -eu
umask 077
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 OUTPUT_DIR [DURATION_SECONDS=86400] [INTERVAL_SECONDS=60]" >&2
  exit 2
fi
OUT=$1
DURATION=${2:-86400}
INTERVAL=${3:-60}
case "$DURATION:$INTERVAL" in *[!0-9:]*|:*|*:) echo 'duration and interval must be positive integers' >&2; exit 2;; esac
[ "$DURATION" -ge 1 ] && [ "$INTERVAL" -ge 1 ] || { echo 'duration and interval must be positive integers' >&2; exit 2; }
OUT_PARENT=$(dirname -- "$OUT")
mkdir -p "$OUT_PARENT" 2>/dev/null || { echo "cannot create output parent: $OUT_PARENT" >&2; exit 1; }
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
quota_transition_observation=sampled systemd property; sub-interval transitions require journal review
EOF
printf 'timestamp\tactive_state\tsub_state\tmain_pid\trestarts\texit_status\tcpu_psi_some_avg10_pct\tcpu_psi_some_avg60_pct\tcpu_psi_full_avg10_pct\theavy_cpu_quota_property\trouter_cpu_seconds\trouter_rss_kib\trouter_runqueue_delay_delta_ms\tverified_routed_processes\n' > "$OUT/samples.tsv"
printf 'timestamp\tpriority\tmessage\n' > "$OUT/errors.tsv"
PREV_PID=''; PREV_STARTTIME=''; PREV_SCHED_NS=''; PREV_SAMPLE_EPOCH=''
PREV_SERVICE_PID=$ROUTER_PID_START; PREV_RESTARTS=$ROUTER_RESTARTS_START; PREV_QUOTA_PROPERTY=''; PREV_QUOTA_PCT=''; PREV_QUOTA_DIRECTION=0
SAMPLE_COUNT=0; MISSED_INTERVALS=0; SAMPLING_FAILURES=0; QUOTA_TRANSITIONS=0; QUOTA_REVERSALS=0; RUN_STATUS=INCOMPLETE
sample() {
  ts=$(date --iso-8601=seconds)
  sample_epoch=$(date +%s)
  if [ -n "$PREV_SAMPLE_EPOCH" ]; then
    gap=$((sample_epoch-PREV_SAMPLE_EPOCH))
    if [ "$gap" -gt "$((INTERVAL+1))" ]; then
      missed=$((gap/INTERVAL-1))
      printf '%s\tWARN\tsampling gap %ss; estimated missed intervals=%s\n' "$ts" "$gap" "$missed" >> "$OUT/errors.tsv"
    fi
  fi
  PREV_SAMPLE_EPOCH=$sample_epoch
  props=$(systemctl --user show workload-router.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStatus --value 2>/dev/null || true)
  active=$(printf '%s\n' "$props" | sed -n '1p'); sub=$(printf '%s\n' "$props" | sed -n '2p')
  pid=$(printf '%s\n' "$props" | sed -n '3p'); restarts=$(printf '%s\n' "$props" | sed -n '4p'); status=$(printf '%s\n' "$props" | sed -n '5p')
  psi=$(awk '
    $1=="some" {for(i=2;i<=NF;i++){split($i,a,"="); if(a[1]=="avg10")s10=a[2]; if(a[1]=="avg60")s60=a[2]}}
    $1=="full" {for(i=2;i<=NF;i++){split($i,a,"="); if(a[1]=="avg10")f10=a[2]}}
    END {
      numeric="^[0-9]+([.][0-9]+)?$"
      if(s10 !~ numeric || s60 !~ numeric || f10 !~ numeric || s10+0>100 || s60+0>100 || f10+0>100) print "n/a n/a n/a"
      else printf "%.2f %.2f %.2f",s10+0,s60+0,f10+0
    }' /proc/pressure/cpu 2>/dev/null || printf 'n/a n/a n/a')
  set -- $psi
  psi10=$1; psi60=$2; psifull=$3
  quota=$(systemctl --user show heavy-workload.slice -p CPUQuotaPerSecUSec --value 2>/dev/null || echo n/a)
  [ -n "$quota" ] || quota=n/a
  quota_pct=n/a
  case "$quota" in
    infinity) quota_pct=infinity;;
    *ms) quota_pct=$(awk -v q="${quota%ms}" 'BEGIN {if(q+0>0) printf "%.3f",q*0.1; else print "n/a"}') ;;
    *s) quota_pct=$(awk -v q="${quota%s}" 'BEGIN {if(q+0>0) printf "%.3f",q*100; else print "n/a"}') ;;
  esac
  if [ -n "$PREV_QUOTA_PROPERTY" ] && [ "$quota" != "$PREV_QUOTA_PROPERTY" ]; then
    QUOTA_TRANSITIONS=$((QUOTA_TRANSITIONS+1))
    case "$PREV_QUOTA_PCT:$quota_pct" in *n/a*|*infinity*) direction=0;;
      *) direction=$(awk -v n="$quota_pct" -v p="$PREV_QUOTA_PCT" 'BEGIN {if(n>p) print 1; else if(n<p) print -1; else print 0}');;
    esac
    if [ "$direction" -ne 0 ] && [ "$PREV_QUOTA_DIRECTION" -ne 0 ] && [ "$direction" -ne "$PREV_QUOTA_DIRECTION" ]; then
      QUOTA_REVERSALS=$((QUOTA_REVERSALS+1))
    fi
    [ "$direction" -eq 0 ] || PREV_QUOTA_DIRECTION=$direction
    printf '%s\tINFO\theavy-slice quota property changed %s -> %s\n' "$ts" "$PREV_QUOTA_PROPERTY" "$quota" >> "$OUT/errors.tsv"
  fi
  PREV_QUOTA_PROPERTY=$quota
  case "$quota_pct" in n/a|infinity) PREV_QUOTA_PCT=''; PREV_QUOTA_DIRECTION=0;; *) PREV_QUOTA_PCT=$quota_pct;; esac
  heavy_cgroup=$(systemctl --user show heavy-workload.slice -p ControlGroup --value 2>/dev/null || true)
  cpu=n/a; rss=n/a; sched_delta=n/a; sched_now=''; starttime=''
  case "$pid" in ''|0|*[!0-9]*) ;; *)
    if [ -r "/proc/$pid/stat" ]; then
      metrics=$(python3 - "$pid" <<'PY'
import os,sys
try:
 pid=sys.argv[1]
 s=open('/proc/'+pid+'/stat').read(); f=s[s.rfind(')')+2:].split()
 ticks=int(f[11])+int(f[12]); start=int(f[19])
 status={}
 for row in open('/proc/'+pid+'/status'):
  if ':' in row:
   k,v=row.split(':',1); status[k]=v.strip()
 sched=open('/proc/'+pid+'/schedstat').read().split()
 print('%.2f %s %s %s' % (ticks/os.sysconf('SC_CLK_TCK'),status.get('VmRSS','n/a kB').split()[0],sched[1],start))
except Exception: print('n/a n/a n/a n/a')
PY
)
      set -- $metrics
      cpu=${1:-n/a}; rss=${2:-n/a}; sched_now=${3:-}; starttime=${4:-}
      case "$sched_now" in ''|*[!0-9]*) sched_now='';; esac
      case "$starttime" in ''|*[!0-9]*) starttime='';; esac
      case "$PREV_SCHED_NS" in ''|*[!0-9]*) ;; *)
        if [ "$pid" = "$PREV_PID" ] && [ "$starttime" = "$PREV_STARTTIME" ] && [ -n "$sched_now" ] && [ "$sched_now" -ge "$PREV_SCHED_NS" ]; then
          sched_delta=$(awk -v n="$sched_now" -v p="$PREV_SCHED_NS" 'BEGIN {printf "%.3f", (n-p)/1000000}')
        fi;;
      esac
    fi
  esac
  PREV_PID=$pid; PREV_STARTTIME=$starttime; PREV_SCHED_NS=$sched_now
  state_file=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/linux-workload-guard/routes.tsv
  routes=$(python3 - "$state_file" "$heavy_cgroup" <<'PY'
import os,sys
state,heavy=sys.argv[1:]
if not heavy or heavy=='-':
 print('n/a'); raise SystemExit
count=0
try:
 with open(state) as f:
  for line in f:
   fields=line.rstrip('\n').split('\t')
   if len(fields)<7 or not fields[6]: continue
   try: pid=int(fields[0]); expected=int(fields[6])
   except ValueError: continue
   try:
    stat=open(f'/proc/{pid}/stat').read(); sf=stat[stat.rfind(')')+2:].split()
    if int(sf[19])!=expected: continue
    cg=None
    for row in open(f'/proc/{pid}/cgroup'):
     if row.startswith('0::'): cg=row.rstrip('\n').split('::',1)[1]; break
    if cg and (cg==heavy or cg.startswith(heavy.rstrip('/')+'/')): count+=1
   except (OSError,ValueError,IndexError): continue
 print(count)
except OSError:
 print('n/a')
PY
)
  SAMPLE_COUNT=$((SAMPLE_COUNT+1))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "${active:-n/a}" "${sub:-n/a}" "${pid:-n/a}" "${restarts:-n/a}" "${status:-n/a}" "$psi10" "$psi60" "$psifull" "${quota:-n/a}" "$cpu" "$rss" "$sched_delta" "$routes" >> "$OUT/samples.tsv"
  if [ "${active:-unknown}" != active ]; then
    printf '%s\tERROR\tservice state %s/%s pid=%s status=%s\n' "$ts" "${active:-n/a}" "${sub:-n/a}" "${pid:-n/a}" "${status:-n/a}" >> "$OUT/errors.tsv"
    SAMPLING_FAILURES=$((SAMPLING_FAILURES+1))
  fi
  case "$restarts:$PREV_RESTARTS" in *[!0-9:]*|:*|*:) ;; *)
    if [ "$restarts" -gt "$PREV_RESTARTS" ]; then
      printf '%s\tERROR\tservice restart count increased %s -> %s\n' "$ts" "$PREV_RESTARTS" "$restarts" >> "$OUT/errors.tsv"
    elif [ "$restarts" -lt "$PREV_RESTARTS" ]; then
      printf '%s\tWARN\tservice restart counter reset %s -> %s\n' "$ts" "$PREV_RESTARTS" "$restarts" >> "$OUT/errors.tsv"
    fi;;
  esac
  if [ -n "$PREV_SERVICE_PID" ] && [ "$pid" != "$PREV_SERVICE_PID" ] && [ "${active:-unknown}" = active ] && [ -n "$pid" ] && [ "$pid" != 0 ]; then
    printf '%s\tINFO\tservice MainPID changed %s -> %s\n' "$ts" "$PREV_SERVICE_PID" "$pid" >> "$OUT/errors.tsv"
  fi
  PREV_RESTARTS=$restarts; PREV_SERVICE_PID=$pid
  if [ "$psi10" = n/a ] || [ "$quota" = n/a ] || [ "$cpu" = n/a ] || [ "$rss" = n/a ] || [ "$routes" = n/a ] || { [ "$SAMPLE_COUNT" -gt 1 ] && [ "$sched_delta" = n/a ]; }; then
    printf '%s\tWARN\tmissing metric(s): psi_avg10=%s quota=%s router_cpu=%s router_rss_kib=%s scheduler_delta_ms=%s verified_routes=%s\n' "$ts" "$psi10" "$quota" "$cpu" "$rss" "$sched_delta" "$routes" >> "$OUT/errors.tsv"
    SAMPLING_FAILURES=$((SAMPLING_FAILURES+1))
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
    expected_samples=$(((DURATION+INTERVAL-1)/INTERVAL+1))
    coverage=$(awk -v got="$SAMPLE_COUNT" -v want="$expected_samples" 'BEGIN {if(want>0) printf "%.4f",got/want; else print "n/a"}')
    if [ "$RUN_STATUS" = COMPLETE ] && [ "$SAMPLE_COUNT" -eq "$expected_samples" ] && [ "$MISSED_INTERVALS" -eq 0 ] && [ "$SAMPLING_FAILURES" -eq 0 ] && [ "$((END_EPOCH-START_EPOCH))" -ge "$DURATION" ]; then
      final_status=COMPLETE
    else
      final_status=INCOMPLETE
    fi
    printf 'run_status=%s\n' "$final_status"
    printf 'sample_count=%s\nexpected_samples=%s\ncoverage_ratio=%s\n' "$SAMPLE_COUNT" "$expected_samples" "$coverage"
    printf 'missed_intervals_estimate=%s\nsampling_failures=%s\n' "$MISSED_INTERVALS" "$SAMPLING_FAILURES"
    printf 'quota_transitions_observed=%s\nquota_direction_reversals_observed=%s\n' "$QUOTA_TRANSITIONS" "$QUOTA_REVERSALS"
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
NEXT_SAMPLE_EPOCH=$((START_EPOCH+INTERVAL))
while :; do
  now=$(date +%s)
  [ "$((now-START_EPOCH))" -lt "$DURATION" ] || break
  sample
  MISSED_BEFORE=$MISSED_INTERVALS
  now=$(date +%s)
  if [ "$now" -lt "$NEXT_SAMPLE_EPOCH" ]; then
    sleep "$((NEXT_SAMPLE_EPOCH-now))"
  else
    while [ "$NEXT_SAMPLE_EPOCH" -lt "$now" ]; do
      NEXT_SAMPLE_EPOCH=$((NEXT_SAMPLE_EPOCH+INTERVAL))
      MISSED_INTERVALS=$((MISSED_INTERVALS+1))
    done
    if [ "$MISSED_INTERVALS" -gt "$MISSED_BEFORE" ]; then
      printf '%s\tWARN\tmonitor collection overran one or more sample deadlines\n' "$(date --iso-8601=seconds)" >> "$OUT/errors.tsv"
    fi
  fi
  NEXT_SAMPLE_EPOCH=$((NEXT_SAMPLE_EPOCH+INTERVAL))
done
sample
RUN_STATUS=COMPLETE
printf 'soak monitor completed: %s\n' "$(date --iso-8601=seconds)"
