#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COLLECTOR="$ROOT/benchmarks/live-psi-observe.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

python3 -m py_compile "$COLLECTOR"
if python3 "$COLLECTOR" "$TMP/bad-duration" 0 1 >/dev/null 2>&1; then
  echo 'live PSI collector accepted a zero duration' >&2; exit 1
fi
[ ! -e "$TMP/bad-duration" ]
if python3 "$COLLECTOR" "$TMP/bad-interval" 1 0.01 >/dev/null 2>&1; then
  echo 'live PSI collector accepted an unsafe sampling interval' >&2; exit 1
fi
[ ! -e "$TMP/bad-interval" ]
mkdir "$TMP/existing"
printf 'preserve-evidence\n' > "$TMP/existing/marker"
if python3 "$COLLECTOR" "$TMP/existing" 1 1 >/dev/null 2>&1; then
  echo 'live PSI collector accepted an existing evidence directory' >&2; exit 1
fi
[ "$(cat "$TMP/existing/marker")" = preserve-evidence ]
ln -s "$TMP/missing-target" "$TMP/dangling-link"
if python3 "$COLLECTOR" "$TMP/dangling-link" 1 1 >/dev/null 2>&1; then
  echo 'live PSI collector accepted a dangling symlink as a fresh evidence path' >&2; exit 1
fi
if grep -Eq 'set-property|start workload-router|stop workload-router|restart workload-router|stress-ng|yes >|while true' "$COLLECTOR"; then
  echo 'live PSI collector must remain passive and must not actuate quota or create load' >&2; exit 1
fi

# An interrupted observation must finalize its partial evidence explicitly as
# INCOMPLETE rather than leaving a directory that can be mistaken for a run.
mkdir "$TMP/mockbin"
cat > "$TMP/mockbin/systemctl" <<'SH'
#!/bin/sh
if [ "${MOCK_UNAVAILABLE:-0}" = 1 ]; then echo UNAVAILABLE; exit 0; fi
case "$*" in
  *ActiveState*) echo active;;
  *MainPID*) echo 123;;
  *NRestarts*) echo 0;;
  *CPUQuotaPerSecUSec*) echo 1s;;
  *) echo unavailable;;
esac
SH
cat > "$TMP/mockbin/journalctl" <<'SH'
#!/bin/sh
if [ "${MOCK_SYMLINK_OUTPUT:-0}" = 1 ]; then
  ln -s "$MOCK_SYMLINK_TARGET" "$MOCK_OUTPUT_DIR/unsealed-link"
fi
echo 'mock controller journal'
SH
chmod 0755 "$TMP/mockbin/systemctl" "$TMP/mockbin/journalctl"
PATH="$TMP/mockbin:$PATH" python3 "$COLLECTOR" "$TMP/interrupted" 10 0.2 > "$TMP/interrupted.stdout" 2>&1 &
collector_pid=$!
sleep 0.7
kill -TERM "$collector_pid"
set +e
wait "$collector_pid"
rc=$?
set -e
[ "$rc" -eq 130 ] || { echo "interrupted collector exit code was $rc, expected 130" >&2; exit 1; }
grep -q '^collection_status=INCOMPLETE$' "$TMP/interrupted/manifest.txt"
grep -q '^acceptance_status=NOT_RUN$' "$TMP/interrupted/manifest.txt"
[ -s "$TMP/interrupted/samples.tsv" ]

# Unavailable runtime identity/quota must invalidate a nominally completed
# observation. A zero exit here would let missing telemetry masquerade as a
# valid collection even when no PSI crossing occurred.
set +e
MOCK_UNAVAILABLE=1 PATH="$TMP/mockbin:$PATH" python3 "$COLLECTOR" "$TMP/unavailable" 1 0.2 > "$TMP/unavailable.stdout" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || { echo "collector accepted unavailable runtime telemetry (exit $rc)" >&2; exit 1; }
grep -q '^collection_status=INCOMPLETE$' "$TMP/unavailable/manifest.txt"
grep -q '^acceptance_status=NOT_RUN$' "$TMP/unavailable/manifest.txt"
grep -q '^integrity_error_count=' "$TMP/unavailable/manifest.txt"
grep -q '^integrity_error=missing critical PSI/service/quota telemetry$' "$TMP/unavailable/manifest.txt"
for run in interrupted unavailable; do
  [ -s "$TMP/$run/provenance.txt" ]
  grep -Eq '^source_revision=([0-9a-f]{40}|unavailable)$' "$TMP/$run/provenance.txt"
  grep -q '^verification_status=PASS$' "$TMP/$run/manifest-verification.txt"
  grep -Eq '^sha256=[0-9a-f]{64}  SHA256SUMS.txt$' "$TMP/$run/manifest-digest.txt"
  (cd "$TMP/$run" && sha256sum -c SHA256SUMS.txt >/dev/null)
done

# A symlink injected inside an otherwise valid output directory must invalidate
# the seal rather than silently disappearing from the regular-file manifest.
set +e
MOCK_SYMLINK_OUTPUT=1 MOCK_OUTPUT_DIR="$TMP/symlink-entry" MOCK_SYMLINK_TARGET="$TMP/no-target" \
  PATH="$TMP/mockbin:$PATH" python3 "$COLLECTOR" "$TMP/symlink-entry" 1 0.2 > "$TMP/symlink-entry.stdout" 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || { echo 'PSI collector accepted an unsealed symlink inside its evidence directory' >&2; exit 1; }
grep -q '^verification_status=FAIL$' "$TMP/symlink-entry/manifest-verification.txt"
grep -q 'unsafe evidence entry.*unsealed-link' "$TMP/symlink-entry/manifest-verification.txt"
grep -Eq '^sha256=[0-9a-f]{64}  SHA256SUMS.txt$' "$TMP/symlink-entry/manifest-digest.txt"
grep -q '^collection_status=INCOMPLETE$' "$TMP/symlink-entry/manifest.txt"
grep -q '^acceptance_status=NOT_RUN$' "$TMP/symlink-entry/manifest.txt"

# Pure fixtures exercise G6 continuity and correlation: a PSI crossing only
# becomes a review candidate when the same heavy unit logs a nearby decision
# whose old/new percentages match the effective quota readbacks around it.
python3 - "$COLLECTOR" <<'PY'
import importlib.util, pathlib, sys, datetime
spec=importlib.util.spec_from_file_location('live_psi',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
def row(stamp, psi, quota='1s', pid='123', restarts='0'):
    mono = str(datetime.datetime.fromisoformat(stamp).timestamp())
    return [stamp, mono, str(psi), '1', '1', '1', '1', '1', 'active', pid, restarts, quota]
rows=[row('2026-01-01T00:00:00.000+00:00',10),
      row('2026-01-01T00:00:01.000+00:00',41),
      row('2026-01-01T00:00:03.000+00:00',10,'1.5s')]
decision=('2026-01-01T00:00:02.000+00:00 host workload-router[1]: '
          'quota changed unit=heavy-workload.slice old=100% new=150% pressure=0.450')
events=module.correlate_crossings(rows,decision,1)
assert len(events)==1 and events[0]['effective_quota_before_readback']=='1s' and events[0]['effective_quota_after_readback']=='1.5s', events
assert not module.correlate_crossings(rows,decision.replace('heavy-workload.slice','other.slice'),1)
assert not module.correlate_crossings(rows,decision.replace('00:00:02','00:05:02'),1)
assert not module.correlate_crossings([rows[0],rows[1],row('2026-01-01T00:00:03.000+00:00',10,'2s')],decision,1)
assert module.service_identity_errors(rows,'123','0','123','0','1s','1.5s') == []
changed=[rows[0],row('2026-01-01T00:00:01.000+00:00',41,pid='124'),rows[2]]
assert any('MainPID discontinuity' in e for e in module.service_identity_errors(changed,'123','0','123','0','1s','1.5s'))
restarted=[rows[0],row('2026-01-01T00:00:01.000+00:00',41,restarts='1'),rows[2]]
assert any('restart-counter discontinuity' in e for e in module.service_identity_errors(restarted,'123','0','123','0','1s','1.5s'))
assert any('quota baseline' in e for e in module.service_identity_errors(rows,'123','0','123','0','infinity','1.5s'))
assert module.sample_trace_errors(rows) == []
assert module.sample_trace_errors([rows[1], rows[0], rows[2]])
assert module.sample_trace_errors([rows[0], rows[0], rows[2]])
bad_psi = [list(row) for row in rows]
bad_psi[1][2] = 'nan'
assert module.sample_trace_errors(bad_psi)
bad_width = [list(row) for row in rows]
bad_width[1].append('unexpected')
assert module.sample_trace_errors(bad_width)
no_timezone = [list(row) for row in rows]
no_timezone[1][0] = '2026-01-01T00:00:01.000'
assert module.sample_trace_errors(no_timezone)
bad_monotonic = [list(row) for row in rows]
bad_monotonic[1][1] = 'inf'
assert module.sample_trace_errors(bad_monotonic)
assert not module.correlate_crossings([rows[1], rows[0], rows[2]], decision, 1), 'out-of-order trace must not create a G6 review candidate'
print('G6 PSI correlation regression: ok (service identity continuity, exact unit/time/pressure and quota readback binding)')
PY

cp -R "$TMP/unavailable" "$TMP/tampered"
printf 'tampered\n' >> "$TMP/tampered/samples.tsv"
if (cd "$TMP/tampered" && sha256sum -c SHA256SUMS.txt >/dev/null 2>&1); then
  echo 'live PSI evidence manifest did not detect post-seal sample edits' >&2; exit 1
fi

printf '%s\n' 'live PSI collector safety: ok (arguments/no-overwrite/symlink guard, passive-only, provenance and SHA-256 sealing, tamper detection, interruption and missing telemetry fail-closed)'
