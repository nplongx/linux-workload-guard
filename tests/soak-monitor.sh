#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MONITOR="$ROOT/benchmarks/soak-monitor.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
MOCKBIN="$TMP/bin"
mkdir -p "$MOCKBIN" "$TMP/runtime/linux-workload-guard"
: > "$TMP/runtime/linux-workload-guard/routes.tsv"

cat > "$MOCKBIN/systemctl" <<'SH'
#!/bin/sh
case "$*" in
  *"-p ActiveState"*)
    n=0; [ ! -r "${MOCK_SERVICE_COUNT:-/nonexistent}" ] || n=$(cat "$MOCK_SERVICE_COUNT")
    n=$((n+1)); [ -z "${MOCK_SERVICE_COUNT:-}" ] || printf '%s\n' "$n" > "$MOCK_SERVICE_COUNT"
    [ "${MOCK_SLOW_SAMPLE:-0}" != 1 ] || sleep 2
    [ "${MOCK_SLOW_FINAL_SAMPLE:-0}" != 1 ] || [ "$n" -lt 2 ] || sleep 2
    if [ "${MOCK_ACTIVE:-active}" = active ]; then
      restarts=0; pid=${MOCK_MAINPID:-0}
      if [ "${MOCK_SERVICE_RESTART:-0}" = 1 ] && [ "$n" -ge 2 ]; then restarts=1; pid=$((pid+1)); fi
      if [ "${MOCK_SERVICE_RESET:-0}" = 1 ] && [ "$n" -lt 2 ]; then restarts=1; fi
      if [ "${MOCK_PID_CHANGE:-0}" = 1 ] && [ "$n" -ge 2 ]; then pid=$((pid+1)); fi
      printf 'active\nrunning\n%s\n%s\n0\n' "$pid" "$restarts"
    else
      printf 'inactive\ndead\n0\n0\n1\n'
    fi;;
  *"-p MainPID"*)
    pid=${MOCK_MAINPID:-0}; n=0; [ ! -r "${MOCK_SERVICE_COUNT:-/nonexistent}" ] || n=$(cat "$MOCK_SERVICE_COUNT")
    if { [ "${MOCK_SERVICE_RESTART:-0}" = 1 ] || [ "${MOCK_PID_CHANGE:-0}" = 1 ]; } && [ "$n" -ge 2 ]; then pid=$((pid+1)); fi
    printf '%s\n' "$pid";;
  *"-p NRestarts"*)
    n=0; [ ! -r "${MOCK_SERVICE_COUNT:-/nonexistent}" ] || n=$(cat "$MOCK_SERVICE_COUNT")
    if [ "${MOCK_SERVICE_RESTART:-0}" = 1 ] && [ "$n" -ge 2 ]; then printf '1\n';
    elif [ "${MOCK_SERVICE_RESET:-0}" = 1 ] && [ "$n" -lt 1 ]; then printf '1\n';
    elif [ "${MOCK_RESTARTS_UNKNOWN_START:-0}" = 1 ] && [ "$n" -lt 1 ]; then printf 'unknown\n';
    else printf '0\n'; fi;;
  *"-p CPUQuotaPerSecUSec"*)
    if [ "${MOCK_QUOTA_SEQUENCE:-0}" = 1 ]; then
      n=0; [ ! -r "$MOCK_QUOTA_COUNT" ] || n=$(cat "$MOCK_QUOTA_COUNT")
      n=$((n+1)); printf '%s\n' "$n" > "$MOCK_QUOTA_COUNT"
      case "$n" in 1) printf '1s\n';; 2) printf '1.5s\n';; 3) printf '1s\n';; *) printf '1s\n';; esac
    else printf '1s\n'; fi;;
  *"-p ControlGroup"*) printf '/user.slice/mock-heavy.slice\n';;
  *) echo "unexpected systemctl invocation: $*" >&2; exit 2;;
esac
SH
cat > "$MOCKBIN/journalctl" <<'SH'
#!/bin/sh
[ "${MOCK_JOURNAL_FAIL:-0}" = 1 ] && exit 1
if [ "${MOCK_SYMLINK_OUTPUT:-0}" = 1 ]; then
  ln -s "$MOCK_SYMLINK_TARGET" "$MOCK_OUTPUT_DIR/unsealed-link"
fi
printf 'mock journal capture\n'
SH
chmod 0755 "$MOCKBIN/systemctl" "$MOCKBIN/journalctl"
REAL_PYTHON=$(command -v python3)
REAL_DATE=$(command -v date)
cat > "$MOCKBIN/python3" <<'SH'
#!/bin/sh
case "$*" in
  *time.monotonic_ns*)
    if [ "${MOCK_MONOTONIC_FAIL:-0}" = 1 ]; then
      n=0; [ ! -r "${MOCK_MONOTONIC_COUNT:-/nonexistent}" ] || n=$(cat "$MOCK_MONOTONIC_COUNT")
      n=$((n+1)); [ -z "${MOCK_MONOTONIC_COUNT:-}" ] || printf '%s\n' "$n" > "$MOCK_MONOTONIC_COUNT"
      [ "$n" -lt 2 ] || exit 1
    fi;;
esac
exec "$REAL_PYTHON" "$@"
SH
chmod 0755 "$MOCKBIN/python3"
cat > "$MOCKBIN/date" <<'SH'
#!/bin/sh
if [ "$*" = '+%s' ] && [ "${MOCK_WALL_CLOCK_JUMP:-0}" = 1 ]; then
  n=0; [ ! -r "${MOCK_DATE_COUNT:-/nonexistent}" ] || n=$(cat "$MOCK_DATE_COUNT")
  n=$((n+1)); [ -z "${MOCK_DATE_COUNT:-}" ] || printf '%s\n' "$n" > "$MOCK_DATE_COUNT"
  if [ "$n" -eq 2 ]; then
    now=$("$REAL_DATE" +%s); printf '%s\n' "$((now+3600))"; exit 0
  fi
fi
exec "$REAL_DATE" "$@"
SH
chmod 0755 "$MOCKBIN/date"
export REAL_PYTHON REAL_DATE
export PATH="$MOCKBIN:$PATH" XDG_RUNTIME_DIR="$TMP/runtime" MOCK_MAINPID="$$"

# A full-duration run with any failed acceptance invariant must exit 1, not
# merely write run_status=INCOMPLETE while returning a misleading zero status.
run_incomplete() {
  set +e
  "$MONITOR" "$@" >/dev/null 2>&1
  rc=$?
  set -e
  [ "$rc" -eq 1 ] || { echo "expected INCOMPLETE monitor exit 1, got $rc: $*" >&2; exit 1; }
}

# Argument errors must not create an output directory.
if "$MONITOR" "$TMP/bad-args" 0 1 >/dev/null 2>&1; then
  echo 'soak monitor accepted zero duration' >&2; exit 1
else
  rc=$?
  [ "$rc" -eq 2 ] || { echo "bad-argument exit code was $rc, expected 2" >&2; exit 1; }
fi
[ ! -e "$TMP/bad-args" ]

# Existing evidence must remain untouched.
mkdir "$TMP/existing"
printf 'preserve-me\n' > "$TMP/existing/marker"
if "$MONITOR" "$TMP/existing" 1 1 >/dev/null 2>&1; then
  echo 'soak monitor accepted an existing output directory' >&2; exit 1
fi
[ "$(cat "$TMP/existing/marker")" = preserve-me ]

# A complete short run must have a rectangular schema and explicit coverage.
"$MONITOR" "$TMP/complete" 2 1 > "$TMP/complete.stdout"
grep -q 'soak monitor collection loop exited:.*acceptance verdict is in' "$TMP/complete.stdout"
awk -F '\t' 'NR==1 {n=NF; if(n!=14) exit 1} NR>1 && NF!=n {exit 1} END {if(NR<3) exit 1}' "$TMP/complete/samples.tsv"
grep -q '^run_status=COMPLETE$' "$TMP/complete/environment.txt"
grep -q '^soak_gate_status=OPEN_DURATION_BELOW_24H$' "$TMP/complete/environment.txt"
grep -q '^soak_minimum_seconds=86400$' "$TMP/complete/environment.txt"
grep -q '^coverage_ratio=1.0000$' "$TMP/complete/environment.txt"
grep -q 'mock journal capture' "$TMP/complete/service-journal.log"
grep -Eq '^source_revision=([0-9a-f]{40}|unavailable)$' "$TMP/complete/provenance.txt"
grep -q '^verification_status=PASS$' "$TMP/complete/manifest-verification.txt"
grep -Eq '^[0-9a-f]{64}  SHA256SUMS.txt$' "$TMP/complete/manifest-digest.txt"
(cd "$TMP/complete" && sha256sum -c SHA256SUMS.txt >/dev/null)
[ "$(stat -c '%a' "$TMP/complete")" = 700 ]
[ "$(stat -c '%a' "$TMP/complete/samples.tsv")" = 600 ]
cp -R "$TMP/complete" "$TMP/complete-tampered"
printf 'tampered\n' >> "$TMP/complete-tampered/samples.tsv"
if (cd "$TMP/complete-tampered" && sha256sum -c SHA256SUMS.txt >/dev/null 2>&1); then
  echo 'soak evidence manifest did not detect post-seal sample edits' >&2; exit 1
fi

# Quota property changes and a direction reversal must be summarized explicitly.
export MOCK_QUOTA_SEQUENCE=1 MOCK_QUOTA_COUNT="$TMP/quota-count"
"$MONITOR" "$TMP/quota-changes" 2 1 >/dev/null
grep -q '^quota_transitions_observed=2$' "$TMP/quota-changes/environment.txt"
grep -q '^quota_direction_reversals_observed=1$' "$TMP/quota-changes/environment.txt"
unset MOCK_QUOTA_SEQUENCE MOCK_QUOTA_COUNT

# A router restart during the observation window must invalidate continuous
# soak acceptance, even if the service is active again at the next sample.
export MOCK_SERVICE_RESTART=1 MOCK_SERVICE_COUNT="$TMP/service-count"
run_incomplete "$TMP/restarted" 2 1
grep -q '^run_status=INCOMPLETE$' "$TMP/restarted/environment.txt"
grep -q '^soak_gate_status=OPEN_RUN_INCOMPLETE$' "$TMP/restarted/environment.txt"
grep -q 'ERROR.*service restart count increased' "$TMP/restarted/errors.tsv"
unset MOCK_SERVICE_RESTART MOCK_SERVICE_COUNT

# A manual service restart can change MainPID without increasing NRestarts;
# process identity continuity must still fail the soak acceptance check.
export MOCK_PID_CHANGE=1 MOCK_SERVICE_COUNT="$TMP/pid-change-count"
run_incomplete "$TMP/pid-changed" 2 1
grep -q '^run_status=INCOMPLETE$' "$TMP/pid-changed/environment.txt"
grep -q 'ERROR.*service MainPID changed' "$TMP/pid-changed/errors.tsv"
unset MOCK_PID_CHANGE MOCK_SERVICE_COUNT

# A restart counter reset is discontinuous telemetry and must not be treated
# as evidence that the service remained stable.
export MOCK_SERVICE_RESET=1 MOCK_SERVICE_COUNT="$TMP/service-reset-count"
run_incomplete "$TMP/restart-counter-reset" 2 1
grep -q '^run_status=INCOMPLETE$' "$TMP/restart-counter-reset/environment.txt"
grep -q 'ERROR.*service restart counter reset' "$TMP/restart-counter-reset/errors.tsv"
unset MOCK_SERVICE_RESET MOCK_SERVICE_COUNT

# Missing initial restart-counter baseline must not be silently replaced by a
# later numeric value and accepted as a continuous soak.
export MOCK_RESTARTS_UNKNOWN_START=1
run_incomplete "$TMP/unknown-restart-baseline" 1 1
grep -q '^run_status=INCOMPLETE$' "$TMP/unknown-restart-baseline/environment.txt"
grep -q 'ERROR.*service restart counter baseline unavailable/invalid' "$TMP/unknown-restart-baseline/errors.tsv"
unset MOCK_RESTARTS_UNKNOWN_START

# Simulate a delayed sampling command crossing a deadline. The monitor must
# count the missed deadline and reject continuous-soak acceptance.
MOCK_SLOW_SAMPLE=1 run_incomplete "$TMP/missed-sample" 3 1
grep -q '^run_status=INCOMPLETE$' "$TMP/missed-sample/environment.txt"
awk -F= '$1=="missed_intervals_estimate" {if ($2+0<1) exit 1; found=1} END {if(!found) exit 1}' "$TMP/missed-sample/environment.txt"

# If journal collection fails, the report must not claim a complete soak.
MOCK_JOURNAL_FAIL=1 run_incomplete "$TMP/no-journal" 1 1
grep -q '^run_status=INCOMPLETE$' "$TMP/no-journal/environment.txt"
grep -q 'ERROR.*service journal capture failed' "$TMP/no-journal/errors.tsv"

# Missing service state is a recorded monitoring failure, not a successful soak.
MOCK_ACTIVE=inactive run_incomplete "$TMP/inactive" 1 1
grep -q '^run_status=INCOMPLETE$' "$TMP/inactive/environment.txt"
grep -q 'ERROR.*service state inactive/dead' "$TMP/inactive/errors.tsv"

# Graceful interruption must finalize as incomplete and preserve the signal exit.
"$MONITOR" "$TMP/interrupted" 10 1 > "$TMP/interrupted.stdout" 2>&1 &
monitor_pid=$!
sleep 1.2
kill -TERM "$monitor_pid"
set +e
wait "$monitor_pid"
rc=$?
set -e
[ "$rc" -eq 143 ] || { echo "interrupted monitor exit code was $rc, expected 143" >&2; exit 1; }
grep -q '^run_status=INCOMPLETE$' "$TMP/interrupted/environment.txt"
grep -q '^exit_code=143$' "$TMP/interrupted/environment.txt"

# A slow final sample occurs after the scheduling loop. It must still invalidate
# continuity rather than escape the loop's missed-deadline accounting.
MOCK_SLOW_FINAL_SAMPLE=1 MOCK_SERVICE_COUNT="$TMP/final-sample-count" \
  run_incomplete "$TMP/slow-final-sample" 1 1
grep -q '^run_status=INCOMPLETE$' "$TMP/slow-final-sample/environment.txt"
grep -q 'WARN.*sample collection took' "$TMP/slow-final-sample/errors.tsv"
awk -F= '$1=="sampling_failures" {if ($2+0<1) exit 1; found=1} END {if(!found) exit 1}' "$TMP/slow-final-sample/environment.txt"

# Symlinks and non-regular entries must not be silently omitted from a soak
# manifest. The run remains incomplete and the verification status is FAIL.
MOCK_SYMLINK_OUTPUT=1 MOCK_OUTPUT_DIR="$TMP/symlink-entry" MOCK_SYMLINK_TARGET="$TMP/no-target" \
  run_incomplete "$TMP/symlink-entry" 1 1
grep -q '^verification_status=FAIL$' "$TMP/symlink-entry/manifest-verification.txt"

# A missing monotonic clock must be an explicit monitoring failure, not a shell
# arithmetic error that skips the final summary or falsely passes the run.
MOCK_MONOTONIC_FAIL=1 MOCK_MONOTONIC_COUNT="$TMP/monotonic-count" \
  run_incomplete "$TMP/no-monotonic-clock" 1 1
grep -q '^run_status=INCOMPLETE$' "$TMP/no-monotonic-clock/environment.txt"
grep -q 'ERROR.*monotonic sample-collection timer unavailable' "$TMP/no-monotonic-clock/errors.tsv"

# Wall-clock steps must not control duration/scheduling or silently corrupt
# sample timestamps while the monotonic interval remains steady.
MOCK_WALL_CLOCK_JUMP=1 MOCK_DATE_COUNT="$TMP/date-count" \
  run_incomplete "$TMP/wall-clock-jump" 2 1
grep -q '^run_status=INCOMPLETE$' "$TMP/wall-clock-jump/environment.txt"
grep -q 'ERROR.*wall-clock sample timestamps discontinuous' "$TMP/wall-clock-jump/errors.tsv"

printf '%s\n' 'soak monitor tests: ok (arguments/no-overwrite, schema/permissions, quota reversals, restart/PID/counter baseline/reset, sampling overruns including final sample, monotonic-clock loss, wall-clock discontinuity, journal failure, missing service, interruption/completeness, symlink/non-regular-entry rejection)'
