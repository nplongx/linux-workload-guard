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
cp -R "$TMP/unavailable" "$TMP/tampered"
printf 'tampered\n' >> "$TMP/tampered/samples.tsv"
if (cd "$TMP/tampered" && sha256sum -c SHA256SUMS.txt >/dev/null 2>&1); then
  echo 'live PSI evidence manifest did not detect post-seal sample edits' >&2; exit 1
fi

printf '%s\n' 'live PSI collector safety: ok (arguments/no-overwrite/symlink guard, passive-only, provenance and SHA-256 sealing, tamper detection, interruption and missing telemetry fail-closed)'
