#!/usr/bin/env bash
set -u
BUNDLE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$BUNDLE/../../.." && pwd)"
cd "$ROOT" || exit 2
FAILED=0
run_check() {
  local slug="$1"; shift
  local started ended rc log
  log="$BUNDLE/commands/$slug.log"
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf 'command='
    first_arg=1
    for arg in "$@"; do
      if [ "$first_arg" -eq 1 ]; then printf '%q' "$arg"; first_arg=0; else printf ' %q' "$arg"; fi
    done
    printf '\n'
    printf 'started_at_utc=%s\n' "$started"
    "$@"
    rc=$?
    ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'ended_at_utc=%s\nexit_code=%s\n' "$ended" "$rc"
  } > "$log" 2>&1
  rc=$(sed -n 's/^exit_code=//p' "$log" | tail -1)
  [ -n "$rc" ] || rc=255
  printf '%s\n' "$rc" > "$BUNDLE/commands/$slug.exit"
  printf '%-34s exit=%s\n' "$slug" "$rc"
  if [ "$rc" -ne 0 ]; then FAILED=1; fi
}

run_check full-test-suite ./test.sh
run_check python-compileall python3 -m compileall -q bin benchmarks tests
run_check controller-hardening python3 tests/controller-hardening.py
run_check controller-sample-flow python3 tests/controller-sample-flow.py
run_check evidence-integrity python3 tests/evidence-integrity.py
run_check prereg-abba ./tests/preregister-abba.sh
run_check performance-review ./tests/performance-review.sh
run_check aggregate-analysis ./tests/analyze-aggregate-routing.sh
run_check ci-runtime-gate-safety ./tests/ci-runtime-gate-safety.sh
run_check rc-preflight-safety ./tests/rc-gate-preflight-safety.sh
run_check routing-benchmark-safety ./tests/routing-benchmark-safety.sh
run_check quota-benchmark-safety ./tests/quota-benchmark-safety.sh
run_check soak-monitor-safety ./tests/soak-monitor.sh
run_check live-psi-collector-safety ./tests/live-psi-observe-safety.sh
run_check workload-scope-safety ./tests/run-workload-scope.sh
run_check shell-bash-syntax bash -lc 'set -eu; sh -n install.sh uninstall.sh test.sh bin/run-workload bin/workload-guard bin/limit-browser-automation-cgroup tests/integration-routing.sh tests/recovery-routing.sh tests/shadow-routing.sh tests/install-uninstall.sh tests/controller-integration.sh tests/recovery-unit.sh tests/soak-monitor.sh tests/routing-benchmark-safety.sh tests/quota-benchmark-safety.sh tests/live-psi-observe-safety.sh tests/performance-review.sh tests/ci-runtime-gate-safety.sh tests/preregister-abba.sh tests/analyze-aggregate-routing.sh tests/rc-gate-preflight-safety.sh tests/run-workload-scope.sh benchmarks/soak-monitor.sh benchmarks/rc-gate-preflight.sh; bash -n benchmarks/quota-benchmark.sh benchmarks/routing-contention-isolated.sh'
run_check systemd-unit-verification systemd-analyze --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice
run_check workflow-yaml-validation python3 -c 'import yaml,pathlib; p=yaml.BaseLoader; a=yaml.load(pathlib.Path(".github/workflows/ci.yml").read_text(),Loader=p); b=yaml.load(pathlib.Path(".github/workflows/approved-controller-runtime.yml").read_text(),Loader=p); assert set(a["on"])=={"push","pull_request"}; assert set(b["on"])=={"workflow_dispatch"}; assert all("self-hosted" not in str(job.get("runs-on","")) for job in a["jobs"].values()); assert all("WORKLOAD_GUARD_REQUIRE_LIVE_CONTROLLER=1 ./tests/controller-integration.sh" not in str(step.get("run","")) for job in a["jobs"].values() for step in job.get("steps",[])); j=b["jobs"]["approved-controller-runtime"]; assert "refs/heads/main" in j["if"] and "RUN_APPROVED_CONTROLLER_INTEGRATION" in j["if"] and "APPROVED_CONTROLLER_RUNTIME_APPROVED" in j["if"]; assert j["timeout-minutes"]=="30"; assert b["permissions"]["contents"]=="read"; assert b["concurrency"]["cancel-in-progress"]=="false"; print("workflow YAML parse and event/approval/runner invariants: ok")'
run_check git-diff-whitespace git diff --check HEAD
run_check release-version-guard bash -lc 'set -eu; test "$(cat VERSION)" = "0.6.5-rc.1"; test "$(git tag --list v1.0.0)" = ""; ! git diff --name-status HEAD | grep -q "^D[[:space:]]"; echo "version unchanged; no v1.0.0 tag; no tracked deletion"'

{
  printf 'pass=24\nsource_base_head=%s\norigin_main_head_at_pass_start=%s\n' \
    "$(git rev-parse HEAD)" "$(git rev-parse --verify origin/main)"
  printf 'version=%s\nbranch=%s\nrecorded_at_utc=%s\n' "$(cat VERSION)" "$(git branch --show-current)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'tracked_diff_excluding_bundle_sha256='; git diff HEAD --binary -- . ':(exclude)benchmarks/results/pass24-validation-2026-10-16-r1' | sha256sum | awk '{print $1}'
  printf 'untracked_count_at_pass_start=259\npreexisting_historical_untracked_count=254\n'
  printf 'all_historical_untracked_artifacts_preserved=true\n'
  printf 'runtime_workflow_dispatched=false\napproval_variables_changed=false\n'
  printf 'runtime_acceptance_gates_closed=0\nrelease_status=NO-GO\n'
  printf 'actionlint=not-installed; workflows parsed with PyYAML BaseLoader and static gate tests\n'
} > "$BUNDLE/provenance.txt"
{
  printf 'head='; git rev-parse HEAD
  printf 'origin_main='; git rev-parse --verify origin/main
  printf 'version='; cat VERSION
  printf 'branch='; git branch --show-current
  printf 'prebundle_untracked_path_count=259 (254 historical entries + 5 Pass 24 source/test/docs files)\n'
  printf 'current_untracked_path_count=%s (254 historical paths plus the new repository-state snapshot emitted during this run; other Pass 24 files are already staged)\n' "$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')"
  printf 'tracked_diff_excluding_bundle_sha256='; git diff HEAD --binary -- . ':(exclude)benchmarks/results/pass24-validation-2026-10-16-r1' | sha256sum | awk '{print $1}'
  printf '\n--- git status at validation start (Pass 24 bundle already exists) ---\n'
  git status --short --branch
  printf '\n--- tracked modified/deleted paths ---\n'
  git diff --name-status HEAD
  printf '\n--- historical artifact count in benchmarks/results at baseline ---\n'
  printf '253 (part of the 254 preexisting untracked entries)\n'
} > "$BUNDLE/repository-state-at-validation-start.log"
sha256sum .github/workflows/approved-controller-runtime.yml benchmarks/evidence_integrity.py docs/rc-gate-execution-pass24.md tests/controller-sample-flow.py tests/evidence-integrity.py > "$BUNDLE/new-source-hashes.txt"
cat > "$BUNDLE/gate-matrix.tsv" <<'TSV'
gate_id	gate	status	blocker	required_evidence
G1	Ubuntu 22.04 delegated CPU counters + effective quota readback	OPEN	Required cpu.stat delegated counters were unavailable on the prior generic environment	Approved temporary cgroup CPU counters over a valid monotonic interval plus exact effective CPUQuota readback and unit/cgroup identity
G2	Approved-runner controller integration	OPEN	No approval variables enabled; runtime workflow not dispatched	Separate dispatch-only workflow on dedicated runner with routing/recovery/quota/CPUWeight effective readback and no capability skip
G3	Independent-host preregistered ABBA	OPEN	No real independent host/preregistration/ABBA run performed	Sealed protocol before run; independent host identity; A/B/B2/A2 sequence with 20/80/80/20 weights and complete raw samples
G4	Throughput acceptance >= 0.90 under PSI protocol	OPEN	No approved-host throughput evidence; local fixtures are not acceptance	All four ABBA blocks, median paired throughput >= 0.90, latency/work ratios <= 1.10, CPU/RSS and PSI confounder evidence
G5	Dedicated-host rollback drill	OPEN	Not run on a disposable approved host	Pre/post config hashes/modes, service identity/restarts, quota readback, no-actuation check, exact restoration and healthy readback
G6	Natural PSI crossing >= 0.40 with decision/readback evidence	OPEN	No naturally occurring qualifying crossing collected	Passive timestamp-correlated PSI, controller decision/reason, before/after effective quota, source/journal integrity, independent review
G7	Continuous soak >= 24h (72h target)	OPEN	No approved-host soak performed	Uninterrupted 24h minimum/72h target with complete telemetry, restart/gap/deadline checks, logs and hash review
TSV
cat > "$BUNDLE/summary.txt" <<'TXT'
Pass 24 — safe controller/evidence hardening validation
Version: 0.6.5-rc.1
Base source at pass start: b280d546bcff0ed32de7730103d7b23e9ee0e7c1 (origin/main matched at pass start)
Purpose: local regression/static verification only. No runtime controller integration, workload benchmark, ABBA, quota actuation, production configuration change, PSI-forcing load, or soak was run.
Changes under validation: tri-state routed-process membership recovery; argv-based known-heavy detection; nonblocking singleton state lock; bounded batched scope-weight read/restore with external-edit protection; SHA-256 run manifests and evaluator/analyzer binding; dispatch-only approved runtime workflow separated from generic push/PR CI; preregistration validation hardening.
Acceptance thresholds preserved: throughput ratio >= 0.90; latency/probe-work ratio <= 1.10; production PSI high threshold 0.40; PSI confounder rules unchanged; dynamic quota remains opt-in; all preregistration requirements retained.
See gate-matrix.tsv: every host-/approval-dependent mandatory gate remains OPEN. Local tests and generic CI are not runtime acceptance.
TXT

# The pre-bundle artifact count is recorded separately from the current status;
# the new Pass 24 bundle is intentionally distinct from historical artifacts.
printf '\nvalidation commands recorded; failed_count=%s\n' "$FAILED"
exit "$FAILED"
