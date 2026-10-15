#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SCRIPT="$ROOT/benchmarks/rc-gate-preflight.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
sh -n "$SCRIPT"
if "$SCRIPT" >/dev/null 2>&1; then echo 'preflight accepted missing output path' >&2; exit 1; fi
if "$SCRIPT" "$TMP/outside" >/dev/null 2>&1; then echo 'preflight accepted output outside repository' >&2; exit 1; fi
if "$SCRIPT" "$ROOT/benchmarks/results/../../tmp/preflight-traversal-$$" >/dev/null 2>&1; then echo 'preflight accepted parent traversal' >&2; exit 1; fi
OUT="$ROOT/benchmarks/results/.rc-gate-preflight-safety-$$"
trap 'rm -rf "$TMP" "$OUT"' EXIT HUP INT TERM
if [ -e "$OUT" ]; then echo 'unexpected output collision' >&2; exit 1; fi
"$SCRIPT" "$OUT" > "$TMP/stdout"
grep -q '^delegated_cpu_stat_counters=NOT PROVEN' "$OUT/gates.txt"
grep -q '^quota_actuation_and_readback=NOT RUN' "$OUT/gates.txt"
grep -q '^router_user_service=' "$OUT/environment.txt"
grep -q '^heavy_user_slice_quota_observation=' "$OUT/environment.txt"
grep -q '^router_system_service_separate_scope=' "$OUT/environment.txt"
grep -q '^heavy_system_slice_quota_separate_scope=' "$OUT/environment.txt"
grep -q '^throughput_acceptance=OPEN / CONFOUNDED_INCONCLUSIVE; threshold=0.90$' "$OUT/gates.txt"
grep -q '^stable_release=NO-GO' "$OUT/gates.txt"
(cd "$OUT" && sha256sum -c SHA256SUMS.txt >/dev/null)
printf 'preserve-me\n' > "$OUT/marker"
if "$SCRIPT" "$OUT" >/dev/null 2>&1; then echo 'preflight overwrote existing evidence directory' >&2; exit 1; fi
[ "$(cat "$OUT/marker")" = preserve-me ]
if grep -Eqi 'systemctl .* (set-property|start|stop|restart)|stress-ng|yes >|while true' "$SCRIPT"; then
  echo 'preflight contains unsafe mutation/load generation' >&2; exit 1
fi
rm -rf "$OUT"
echo 'RC gate preflight safety: ok (arguments, repository boundary, explicit OPEN/NOT RUN classification, SHA-256, no-overwrite, read-only guard)'
