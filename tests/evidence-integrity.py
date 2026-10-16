#!/usr/bin/env python3
"""Deterministic tests for run artifact manifest completeness and tamper detection."""
import importlib.util
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence_integrity", ROOT / "benchmarks/evidence_integrity.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="lwg-evidence-integrity-") as directory:
    root = pathlib.Path(directory)
    result = root / "run.tsv"
    for path in module.artifact_paths(result):
        path.write_text(f"fixture artifact: {path.name}\n")
    manifest = module.write_manifest(result)
    check(module.verify_manifest(result) == [], "a freshly sealed run should verify")
    check((manifest.stat().st_mode & 0o777) == 0o600, "manifest permissions should be owner-only")
    try:
        module.write_manifest(result)
    except FileExistsError:
        pass
    else:
        raise AssertionError("manifest writer must never overwrite existing evidence")

    telemetry = result.with_suffix(".telemetry.tsv")
    telemetry.write_text(telemetry.read_text() + "post-seal mutation\n")
    errors = module.verify_manifest(result)
    check(any("SHA-256 mismatch" in error and telemetry.name in error for error in errors),
          f"post-seal artifact edit was not detected: {errors}")

    manifest.write_text(manifest.read_text() + manifest.read_text().splitlines()[0] + "\n")
    errors = module.verify_manifest(result)
    check(any("exactly" in error for error in errors), "extra manifest entries must be rejected")
    manifest.write_bytes(b"\xff\xfe\xfa\n")
    errors = module.verify_manifest(result)
    check(any("could not verify" in error for error in errors),
          f"non-UTF-8 manifest must be reported as invalid evidence, not traceback: {errors}")

    missing = root / "missing.tsv"
    for path in module.artifact_paths(missing):
        if path.suffix == ".log":
            continue
        path.write_text("fixture\n")
    try:
        module.write_manifest(missing)
    except ValueError as exc:
        check("missing or unsafe" in str(exc), str(exc))
    else:
        raise AssertionError("missing required artifact must prevent evidence sealing")

print("evidence integrity: ok (complete artifact set, create-only manifest, 0600 permissions, tamper and manifest-shape rejection)")
