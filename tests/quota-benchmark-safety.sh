#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BENCH="$ROOT/benchmarks/quota-benchmark.sh"
grep -q 'time.monotonic_ns' "$BENCH"
if grep -q 'date +%s%N' "$BENCH"; then
  echo 'quota benchmark elapsed timing must use a monotonic clock' >&2; exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
MOCKBIN="$TMP/bin"
CGROOT="$TMP/cgroup"
mkdir -p "$MOCKBIN" "$CGROOT"
: > "$CGROOT/cgroup.controllers"
export MOCK_CGROUP_ROOT="$CGROOT" MOCK_CALL_LOG="$TMP/systemctl.calls"
export PATH="$MOCKBIN:$PATH" CGROUP_ROOT="$CGROOT"

cat > "$MOCKBIN/systemctl" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$MOCK_CALL_LOG"
case "$*" in
  --version) echo 'systemd 259.5';;
  --user\ start\ lwgquotabench*.slice)
    unit=$3; dir="$MOCK_CGROUP_ROOT/user.slice/mock/$unit"; mkdir -p "$dir"
    printf 'usage_usec 100000\nnr_throttled 0\nnr_periods 0\n' > "$dir/cpu.stat";;
  --user\ set-property\ lwgquotabench*.slice\ CPUQuota=*)
    unit=$3; dir="$MOCK_CGROUP_ROOT/user.slice/mock/$unit"; mkdir -p "$dir"
    [ -f "$dir/cpu.stat" ] || printf 'usage_usec 100000\nnr_throttled 0\nnr_periods 0\n' > "$dir/cpu.stat";;
  --user\ show\ lwgquotabench*.slice\ -p\ ControlGroup\ --value)
    unit=$3; printf '/user.slice/mock/%s\n' "$unit";;
  --user\ stop\ lwgquotabench*.slice|--user\ reset-failed\ lwgquotabench*.slice) :;;
  *) echo "unexpected systemctl invocation: $*" >&2; exit 2;;
esac
SH
cat > "$MOCKBIN/systemd-run" <<'SH'
#!/bin/sh
slice=
for arg do case "$arg" in --slice=*) slice=${arg#--slice=};; esac; done
case "$slice" in lwgquotabench*.slice) :;; *) echo "non-temporary slice requested: $slice" >&2; exit 2;; esac
dir="$MOCK_CGROUP_ROOT/user.slice/mock/$slice"
[ -f "$dir/cpu.stat" ] || { echo "missing temporary cgroup stats: $dir" >&2; exit 2; }
if [ "${MOCK_COUNTER_RESET:-0}" = 1 ]; then
  printf 'usage_usec 1\nnr_throttled 0\nnr_periods 0\n' > "$dir/cpu.stat"
  exit 0
fi
awk '{if($1=="usage_usec")u=$2; if($1=="nr_throttled")t=$2; if($1=="nr_periods")p=$2}
     END {printf "usage_usec %d\nnr_throttled %d\nnr_periods %d\n",u+100000,t+1,p+2}' "$dir/cpu.stat" > "$dir/cpu.stat.next"
mv "$dir/cpu.stat.next" "$dir/cpu.stat"
SH
chmod 0755 "$MOCKBIN/systemctl" "$MOCKBIN/systemd-run"

if "$BENCH" --bad-option >/dev/null 2>&1; then
  echo 'quota benchmark accepted an unknown option' >&2; exit 1
else
  [ "$?" -eq 2 ]
fi
if WORKERS=0 "$BENCH" >/dev/null 2>&1; then
  echo 'quota benchmark accepted zero workers' >&2; exit 1
else
  [ "$?" -eq 2 ]
fi

# A result collision must be refused before touching systemd or any slice.
printf 'preserve historical result\n' > "$TMP/existing.tsv"
: > "$MOCK_CALL_LOG"
if RESULT="$TMP/existing.tsv" "$BENCH" >/dev/null 2>&1; then
  echo 'quota benchmark overwrote an existing result' >&2; exit 1
else
  [ "$?" -eq 1 ]
fi
[ "$(cat "$TMP/existing.tsv")" = 'preserve historical result' ]
[ ! -s "$MOCK_CALL_LOG" ]
printf 'preserve historical manifest\n' > "$TMP/manifest-collision.environment.txt"
if RESULT="$TMP/manifest-collision.tsv" "$BENCH" >/dev/null 2>&1; then
  echo 'quota benchmark accepted an existing manifest path' >&2; exit 1
else
  [ "$?" -eq 1 ]
fi
[ "$(cat "$TMP/manifest-collision.environment.txt")" = 'preserve historical manifest' ]
[ ! -s "$MOCK_CALL_LOG" ]

# A fully mocked short run exercises temporary-slice setup, quota changes,
# counter continuity, output schema and manifest without generating load.
if ! RESULT="$TMP/isolation.tsv" WORKERS=1 WORK=10 REPS=1 QUOTAS=100,200 \
  bash -x "$BENCH" > "$TMP/run.stdout" 2> "$TMP/run.stderr"; then
  cat "$TMP/run.stdout" "$TMP/run.stderr" >&2
  exit 1
fi
awk -F '\t' 'NR==1 {if(NF!=8) exit 1} NR>1 && NF!=8 {exit 1} END {if(NR!=3) exit 1}' "$TMP/isolation.tsv"
grep -q '^run_status=COMPLETE$' "$TMP/isolation.environment.txt"
grep -q '^production_service_touched=false$' "$TMP/isolation.environment.txt"
grep -q '^production_slice_touched=false$' "$TMP/isolation.environment.txt"
grep -q '^gcc=' "$TMP/isolation.environment.txt"
grep -q '^temporary_cpu_max=' "$TMP/isolation.environment.txt"
grep -q '^parent_cpu_max=' "$TMP/isolation.environment.txt"
grep -q 'lwgquotabench' "$MOCK_CALL_LOG"
if grep -Eq 'workload-router\.service|heavy-workload\.slice' "$MOCK_CALL_LOG"; then
  echo 'quota benchmark touched the installed service or production slice' >&2; exit 1
fi

# A reset in the temporary cgroup counters must fail the trial and leave an
# explicit incomplete manifest rather than silently publishing a valid run.
if MOCK_COUNTER_RESET=1 RESULT="$TMP/reset.tsv" WORKERS=1 WORK=10 REPS=1 QUOTAS=100 \
  "$BENCH" >/dev/null 2>&1; then
  echo 'quota benchmark accepted reset cgroup counters' >&2; exit 1
else
  [ "$?" -eq 1 ]
fi
grep -q '^run_status=INCOMPLETE$' "$TMP/reset.environment.txt"
[ -f "$TMP/reset.tsv" ]

printf '%s\n' 'quota benchmark safety: ok (argument/collision guards, isolated temporary slice, counters/schema, no production unit calls)'
