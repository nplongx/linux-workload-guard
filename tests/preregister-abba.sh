#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/lwg-preregistration.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

python3 - "$ROOT/benchmarks/preregister_abba.py" "$TMP" <<'PY'
import importlib.util, pathlib, shutil, sys
spec=importlib.util.spec_from_file_location('preregister_abba',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
tmp=pathlib.Path(sys.argv[2])
source={'revision':'a'*40,'tracked_dirty':False,'tracked_diff_sha256':'b'*64}
host={'hostname':'approved-host-fixture','os_release':'Ubuntu fixture','kernel':'test-kernel',
      'architecture':'x86_64','logical_cpus':8,'cpu_model':'fixture-cpu','python':'Python test',
      'systemd':'systemd fixture','cgroup_v2':True,'cgroup_controllers':['cpu','memory']}
payload=module.protocol_payload('host-ticket-42','operator-ticket-7','terminal-build',source,host,
                                created_at='2026-10-16T00:00:00+00:00')
sealed=tmp/'sealed'
digest=module.write_sealed_preregistration(sealed,payload)
assert len(digest)==64
assert (sealed/'protocol.json').stat().st_mode & 0o222 == 0
assert (sealed/'SHA256SUMS.txt').stat().st_mode & 0o222 == 0
verified,actual,errors=module.verify_sealed_preregistration(sealed)
assert not errors, errors
assert actual==digest and verified['experiment']['block_order']==['A','B','B2','A2']

verified,digest2,errors=module.verify_run_binding(sealed,'A','approved-host-fixture','a'*40,
                                                  'terminal-build',5,30.0,2,20)
assert not errors and digest2==digest, errors
_,_,errors=module.verify_run_binding(sealed,'A','different-host','a'*40,'terminal-build',5,30.0,2,20)
assert any('hostname mismatch' in item for item in errors), errors
_,_,errors=module.verify_run_binding(sealed,'B','approved-host-fixture','a'*40,'terminal-build',5,30.0,2,20)
assert any('requires CPUWeight=80' in item for item in errors), errors
_,_,errors=module.verify_run_binding(sealed,'A','approved-host-fixture','c'*40,'terminal-build',5,30.0,2,20)
assert any('source revision differs' in item for item in errors), errors
_,_,errors=module.verify_run_binding(sealed,'A','approved-host-fixture','a'*40,'terminal-gzip',5,30.0,2,20)
assert any('workload kind differs' in item for item in errors), errors

tampered=tmp/'tampered'
shutil.copytree(sealed,tampered)
protocol=tampered/'protocol.json'
protocol.chmod(0o644)
protocol.write_text(protocol.read_text().replace('0.9', '0.8', 1))
_,_,errors=module.verify_sealed_preregistration(tampered)
assert any('SHA-256 does not match' in item for item in errors), errors

tamper_design=dict(payload)
tamper_design['acceptance']=dict(payload['acceptance'],minimum_median_paired_throughput_ratio=0.89)
assert any('minimum_median_paired_throughput_ratio' in item for item in module.validate_payload(tamper_design))
try:
    module.write_sealed_preregistration(sealed,payload)
except FileExistsError:
    pass
else:
    raise AssertionError('preregistration writer overwrote an existing directory')
assert module.verify_sealed_preregistration(sealed)[2] == []
print('ABBA preregistration regression: ok (seal/hash, fixed thresholds/order, run binding, tamper/no-overwrite guards)')
PY

if python3 "$ROOT/benchmarks/preregister_abba.py" create \
    --output "benchmarks/results/SHOULD-NOT-BE-CREATED" --host-id dummy --operator-id dummy \
    --workload-kind terminal-build >/dev/null 2>&1; then
  echo 'preregistration CLI accepted missing explicit independent-host/approved-window confirmation' >&2
  exit 1
fi
[ ! -e "$ROOT/benchmarks/results/SHOULD-NOT-BE-CREATED" ]
echo 'ABBA preregistration CLI safety: ok (no output created without explicit confirmations)'
