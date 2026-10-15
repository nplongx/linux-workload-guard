#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/bin" "$TMP/home/.config/linux-workload-guard"
cat > "$TMP/bin/systemd-run" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "$LOGFILE"
EOF
chmod 0755 "$TMP/bin/systemd-run"

printf 'WORKLOAD_GUARD_AGGREGATE_ROUTING=false\n' > "$TMP/home/.config/linux-workload-guard/workload-guard.env"
HOME="$TMP/home" PATH="$TMP/bin:$PATH" LOGFILE="$TMP/disabled.args" "$ROOT/bin/run-workload" make build
grep -qx -- '--slice=heavy-workload.slice' "$TMP/disabled.args"
if grep -q -- '--slice=protected-workload.slice' "$TMP/disabled.args"; then
  echo 'aggregate-disabled workload unexpectedly used a candidate scope' >&2; exit 1
fi
HOME="$TMP/home" PATH="$TMP/bin:$PATH" LOGFILE="$TMP/disabled-shell.args" "$ROOT/bin/run-workload"
grep -qx -- '--slice=heavy-workload.slice' "$TMP/disabled-shell.args"

printf 'WORKLOAD_GUARD_AGGREGATE_ROUTING=true\n' > "$TMP/home/.config/linux-workload-guard/workload-guard.env"
HOME="$TMP/home" PATH="$TMP/bin:$PATH" LOGFILE="$TMP/enabled.args" "$ROOT/bin/run-workload" make build
grep -qx -- '--slice=protected-workload.slice' "$TMP/enabled.args"
grep -Eq '^--unit=lwg-workload-[0-9]+-[0-9]+\.scope$' "$TMP/enabled.args"
grep -qx -- '--description=Linux Workload Guard aggregate candidate' "$TMP/enabled.args"
HOME="$TMP/home" PATH="$TMP/bin:$PATH" LOGFILE="$TMP/enabled-shell.args" "$ROOT/bin/run-workload"
grep -qx -- '--slice=protected-workload.slice' "$TMP/enabled-shell.args"
grep -Eq '^--unit=lwg-workload-[0-9]+-[0-9]+\.scope$' "$TMP/enabled-shell.args"
grep -qx -- '--description=Linux Workload Guard aggregate candidate' "$TMP/enabled-shell.args"

printf '%s\n' 'run-workload scope routing: ok (disabled default and opt-in protected scope)'
