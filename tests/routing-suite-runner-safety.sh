#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNNER="$ROOT/tests/run-approved-routing-suites.sh"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/lwg-routing-suite-runner.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir "$TMP/out" "$TMP/fake-suites" "$TMP/markers"

for suite in integration-routing recovery-routing shadow-routing aggregate-scope-integration; do
  if [ "$suite" = integration-routing ]; then code=17; else code=0; fi
  cat > "$TMP/fake-suites/$suite.sh" <<SH
#!/bin/sh
printf '%s\\n' '$suite' > '$TMP/markers/$suite.ran'
echo 'fixture suite $suite'
exit $code
SH
  chmod 0755 "$TMP/fake-suites/$suite.sh"
done

if "$RUNNER" "$TMP/out" "$TMP/fake-suites" > "$TMP/stdout" 2>&1; then
  echo 'routing suite runner returned success after a suite failed' >&2; exit 1
else
  rc=$?
  [ "$rc" -eq 1 ] || { echo "routing suite runner returned $rc, expected 1" >&2; exit 1; }
fi
for suite in integration-routing recovery-routing shadow-routing aggregate-scope-integration; do
  [ -s "$TMP/markers/$suite.ran" ] || { echo "later suite was skipped: $suite" >&2; exit 1; }
  grep -q "fixture suite $suite" "$TMP/out/$suite.log"
done
grep -q '^integration-routing[[:space:]]17[[:space:]]' "$TMP/out/routing-suite-status.tsv"
for suite in recovery-routing shadow-routing aggregate-scope-integration; do
  grep -q "^$suite[[:space:]]0[[:space:]]" "$TMP/out/routing-suite-status.tsv"
done
grep -q 'suite=aggregate-scope-integration exit_status=0' "$TMP/out/routing-step.log"

# A second invocation must refuse to truncate any first-attempt evidence.
cp "$TMP/out/routing-suite-status.tsv" "$TMP/status.before"
if "$RUNNER" "$TMP/out" "$TMP/fake-suites" > "$TMP/collision.out" 2>&1; then
  echo 'routing suite runner overwrote an existing evidence bundle' >&2; exit 1
else [ "$?" -eq 2 ]; fi
cmp "$TMP/status.before" "$TMP/out/routing-suite-status.tsv"
printf '%s\n' 'routing suite runner safety: ok (all suites execute after failure, per-suite logs/status, create-only outputs)'
