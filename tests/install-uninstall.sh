#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$TMP/bin" "$TMP/home"
cat > "$TMP/bin/systemctl" <<'MOCK'
#!/bin/sh
printf '%s\n' "$*" >> "$SYSTEMCTL_CALL_LOG"
exit 0
MOCK
chmod 0755 "$TMP/bin/systemctl"
export HOME="$TMP/home"
export SYSTEMCTL_CALL_LOG="$TMP/systemctl.log"
export PATH="$TMP/bin:$PATH"

"$ROOT/install.sh"
for helper in workload-router.py run-workload workload-guard workload-profile limit-browser-automation-cgroup; do
    test -x "$HOME/.local/bin/$helper" || { echo "install failed to install $helper" >&2; exit 1; }
done
test -f "$HOME/.local/share/linux-workload-guard/VERSION"
test -f "$HOME/.config/linux-workload-guard/workload-guard.env"

"$ROOT/uninstall.sh"
for helper in workload-router.py run-workload workload-guard workload-profile limit-browser-automation-cgroup; do
    test ! -e "$HOME/.local/bin/$helper" || { echo "uninstall left $helper behind" >&2; exit 1; }
done
test ! -e "$HOME/.local/share/linux-workload-guard/VERSION"
test -f "$HOME/.config/linux-workload-guard/workload-guard.env" || {
    echo 'uninstall unexpectedly removed the user configuration' >&2; exit 1;
}
grep -q -- '--user enable --now workload-router.service' "$SYSTEMCTL_CALL_LOG"
grep -q -- '--user disable --now workload-router.service' "$SYSTEMCTL_CALL_LOG"
echo 'install/uninstall smoke: ok (systemctl mocked; user config preserved)'
