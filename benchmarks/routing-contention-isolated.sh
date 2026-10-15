#!/usr/bin/env bash
# Run a safe router-off/router-on benchmark using unique temporary user slices.
# This wrapper never edits workload-guard.env, restarts the installed router,
# or writes quotas to the production protected/heavy slices.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
exec python3 benchmarks/routing-contention.py "$@"
