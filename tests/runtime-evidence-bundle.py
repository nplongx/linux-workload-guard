#!/usr/bin/env python3
"""Runtime evidence finalizer regressions; only temporary fixture directories are used."""
import importlib.util
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_evidence_bundle", ROOT / "benchmarks/runtime-evidence-bundle.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="lwg-runtime-bundle-") as directory:
    base = pathlib.Path(directory)
    summary = base / "step-summary.md"
    os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
    os.environ["PREPARE_OUTCOME"] = "failure"
    collided = base / "collided"
    collided.mkdir()
    marker = collided / "historical-marker.txt"
    marker.write_text("must not be changed\n")
    check(module.finalize(collided) == 1, "failed preparation must be nonzero")
    check(marker.read_text() == "must not be changed\n", "failed preparation touched a colliding evidence path")
    check(not (collided / module.PROVENANCE).exists(), "failed preparation created provenance in an unowned path")
    check("did not succeed" in summary.read_text(), "failed preparation reason was not surfaced")

    os.environ["PREPARE_OUTCOME"] = "success"
    for key, value in {
        "WORKFLOW_RUN_ID": "fixture-run", "WORKFLOW_RUN_ATTEMPT": "1", "WORKFLOW_REF": "refs/heads/main",
        "PREFLIGHT_OUTCOME": "success", "CONTROLLER_OUTCOME": "success", "ROUTING_OUTCOME": "success",
    }.items():
        os.environ[key] = value
    valid = base / "valid"
    (valid / "controller").mkdir(parents=True)
    (valid / "controller" / "step.log").write_text("fixture runtime output\n")
    check(module.finalize(valid) == 0, "valid fixture evidence should seal")
    check(module.verify_bundle(valid) == [], "fresh bundle should verify strictly")
    manifest_names = {line.split("  ", 1)[1] for line in (valid / module.MANIFEST).read_text().splitlines()}
    check(module.PROVENANCE in manifest_names, "provenance must be included in the sealed file set")
    check("verification_status=PASS" in (valid / module.VERIFICATION).read_text(), "verification status should be PASS")

    verification_text = (valid / module.VERIFICATION).read_text()
    (valid / module.VERIFICATION).write_text(verification_text.replace("verification_status=PASS", "verification_status=FAIL"))
    check(module.verify_bundle(valid), "tampered verification status must invalidate the bundle")
    (valid / module.VERIFICATION).write_text(verification_text)

    (valid / "controller" / "step.log").write_text("post-seal edit\n")
    check(module.verify_bundle(valid), "post-seal edit must invalidate the bundle")
    (valid / "controller" / "step.log").write_text("fixture runtime output\n")
    (valid / "late-unmanifested.txt").write_text("extra entry after seal\n")
    check(any("unmanifested evidence entry" in error for error in module.verify_bundle(valid)),
          "post-seal extra file must invalidate the exact file set")

    reserved = base / "reserved-collision"
    reserved.mkdir()
    reserved_marker = reserved / module.PROVENANCE
    reserved_marker.write_text("preserve reserved file\n")
    check(module.finalize(reserved) == 1, "reserved metadata collision must fail")
    check(reserved_marker.read_text() == "preserve reserved file\n", "finalizer overwrote reserved metadata")

    unsafe = base / "unsafe"
    unsafe.mkdir()
    (unsafe / "observed.log").write_text("partial failure evidence\n")
    (unsafe / "unexpected-link").symlink_to(base / "missing-target")
    check(module.finalize(unsafe) == 1, "symlink in evidence bundle must fail sealing")
    check("verification_status=FAIL" in (unsafe / module.VERIFICATION).read_text(),
          "unsafe bundle must retain explicit verification failure")
    check(not (unsafe / module.MANIFEST).exists(), "unsafe bundle must not receive a misleading complete manifest")

    real_parent = base / "real-parent"
    real_parent.mkdir()
    parent_bundle = real_parent / "bundle"
    parent_bundle.mkdir()
    (parent_bundle / "keep.txt").write_text("parent symlink target\n")
    linked_parent = base / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    check(module.finalize(linked_parent / "bundle") == 1, "symlinked parent path must fail finalization")
    check(not (parent_bundle / module.PROVENANCE).exists(), "finalizer followed a symlinked parent path")
    check(module.verify_bundle(linked_parent / "bundle"), "verification must reject symlinked parent path")

print("runtime evidence bundle regressions: ok (collision no-touch, reserved-path no-overwrite, exact file set, tamper, symlink fail-closed)")
