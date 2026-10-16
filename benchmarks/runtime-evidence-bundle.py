#!/usr/bin/env python3
"""Create-only finalization and strict file-set verification for runtime evidence."""
import datetime
import hashlib
import os
import pathlib
import platform
import re
import stat
import subprocess
import sys

MANIFEST = "SHA256SUMS.txt"
VERIFICATION = "manifest-verification.txt"
DIGEST = "manifest-digest.txt"
PROVENANCE = "provenance-and-status.txt"
METADATA = {MANIFEST, VERIFICATION, DIGEST}
RESERVED = METADATA | {PROVENANCE, ".manifest-inputs.tmp", ".manifest-sorted.tmp"}
SAFE_NAME = re.compile(r"[A-Za-z0-9._/-]+\Z")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_summary(message):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with pathlib.Path(summary).open("a", encoding="utf-8") as stream:
        stream.write(message.rstrip() + "\n")


def safe_relative_name(name):
    path = pathlib.PurePosixPath(name)
    return bool(SAFE_NAME.fullmatch(name)) and not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def root_path_errors(root):
    """Reject a symlink or non-directory anywhere in the path to the bundle."""
    absolute = pathlib.Path(os.path.abspath(root))
    current = pathlib.Path(absolute.anchor)
    for index, part in enumerate(absolute.parts[1:]):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            return [f"evidence path component is missing or unreadable: {current}: {exc}"]
        if stat.S_ISLNK(mode):
            return [f"evidence path contains a symlink component: {current}"]
        if index < len(absolute.parts[1:]) - 1 and not stat.S_ISDIR(mode):
            return [f"evidence path parent is not a directory: {current}"]
        if index == len(absolute.parts[1:]) - 1 and not stat.S_ISDIR(mode):
            return ["evidence root is not a directory"]
    return []


def walk_entries(root):
    """Return regular files and all unsafe entry names without following links."""
    files = []
    unsafe = []
    for base, dirs, names in os.walk(root, topdown=True, followlinks=False):
        base_path = pathlib.Path(base)
        for name in list(dirs):
            path = base_path / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_dir() or not safe_relative_name(rel):
                unsafe.append(rel)
                dirs.remove(name)
        for name in names:
            path = base_path / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file() or not safe_relative_name(rel):
                unsafe.append(rel)
            else:
                files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix()), sorted(set(unsafe))


def parse_manifest(root):
    path = root / MANIFEST
    if path.is_symlink() or not path.is_file():
        return {}, ["checksum manifest missing or unsafe"]
    recorded = {}
    errors = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return {}, [f"checksum manifest unreadable: {exc}"]
    if not lines:
        errors.append("checksum manifest is empty")
    for number, line in enumerate(lines, 1):
        digest, separator, name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or not safe_relative_name(name):
            errors.append(f"malformed checksum entry at line {number}")
            continue
        if name in METADATA or name in {".manifest-inputs.tmp", ".manifest-sorted.tmp"}:
            errors.append(f"reserved metadata path listed in checksum manifest: {name}")
        if name in recorded:
            errors.append(f"duplicate checksum entry: {name}")
        recorded[name] = digest
    return recorded, errors


def verify_bundle(root, require_digest=True):
    root = pathlib.Path(os.path.abspath(root))
    errors = []
    errors.extend(root_path_errors(root))
    if errors:
        return errors
    files, unsafe = walk_entries(root)
    errors.extend(f"unsafe evidence entry (symlink or non-regular file): {name}" for name in unsafe)
    recorded, manifest_errors = parse_manifest(root)
    errors.extend(manifest_errors)
    actual = {path.relative_to(root).as_posix() for path in files
              if path.relative_to(root).as_posix() not in METADATA}
    expected = set(recorded)
    for name in sorted(expected - actual):
        errors.append(f"manifest entry missing or unsafe: {name}")
    for name in sorted(actual - expected):
        errors.append(f"unmanifested evidence entry: {name}")
    for name in sorted(actual & expected):
        path = root / name
        try:
            if sha256_file(path) != recorded[name]:
                errors.append(f"SHA-256 mismatch: {name}")
        except OSError as exc:
            errors.append(f"could not hash {name}: {exc}")
    if require_digest:
        verification_path = root / VERIFICATION
        if verification_path.is_symlink() or not verification_path.is_file():
            errors.append("manifest verification report missing or unsafe")
        else:
            try:
                verification_lines = verification_path.read_text(encoding="utf-8").splitlines()
                statuses = [line for line in verification_lines if line.startswith("verification_status=")]
                counts = [line for line in verification_lines if line.startswith("manifest_entries=")]
                if statuses != ["verification_status=PASS"]:
                    errors.append("manifest verification report does not assert exactly one PASS")
                if counts != [f"manifest_entries={len(recorded)}"]:
                    errors.append("manifest verification report entry count is missing or inconsistent")
            except (OSError, UnicodeError) as exc:
                errors.append(f"manifest verification report unreadable: {exc}")
        digest_path = root / DIGEST
        if digest_path.is_symlink() or not digest_path.is_file():
            errors.append("manifest digest missing or unsafe")
        elif (root / MANIFEST).is_symlink() or not (root / MANIFEST).is_file():
            errors.append("cannot verify manifest digest because SHA256SUMS.txt is missing or unsafe")
        else:
            try:
                digest_line = digest_path.read_text(encoding="utf-8").strip()
                expected_line = f"{hashlib.sha256((root / MANIFEST).read_bytes()).hexdigest()}  {MANIFEST}"
                if digest_line not in (f"sha256={expected_line}", expected_line):
                    errors.append("manifest digest does not match SHA256SUMS.txt")
            except (OSError, UnicodeError) as exc:
                errors.append(f"manifest digest unreadable: {exc}")
    return sorted(set(errors))


def write_exclusive(path, content):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def finalize(root):
    # Do not even stat or enumerate a colliding path if preparation failed.
    if os.environ.get("PREPARE_OUTCOME") != "success":
        message = "Finalizer refused to touch/upload a run path because unique-directory preparation did not succeed."
        append_summary(message)
        print(message, file=sys.stderr)
        return 1
    root = pathlib.Path(os.path.abspath(root))
    path_errors = root_path_errors(root)
    if path_errors:
        append_summary("Runtime evidence finalizer refused an unsafe evidence path: " + "; ".join(path_errors))
        return 1
    for name in RESERVED:
        path = root / name
        if path.exists() or path.is_symlink():
            message = f"Runtime evidence finalizer refused to overwrite reserved path: {name}"
            append_summary(message)
            print(message, file=sys.stderr)
            return 1

    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=3, check=False)
        source_revision = revision.stdout.strip() if revision.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        source_revision = "unavailable"
    try:
        os_release = pathlib.Path("/etc/os-release").read_text(encoding="utf-8").replace("\n", ";")
    except OSError:
        os_release = "unavailable"
    try:
        cgroup_mode = subprocess.run(["stat", "-fc", "%T", "/sys/fs/cgroup"], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     timeout=3, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        cgroup_mode = "unavailable"
    provenance = {
        "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "workflow_run_id": os.environ.get("WORKFLOW_RUN_ID", "unavailable"),
        "workflow_run_attempt": os.environ.get("WORKFLOW_RUN_ATTEMPT", "unavailable"),
        "source_revision": source_revision,
        "ref": os.environ.get("WORKFLOW_REF", "unavailable"),
        "preflight_step_outcome": os.environ.get("PREFLIGHT_OUTCOME", "unknown"),
        "controller_step_outcome": os.environ.get("CONTROLLER_OUTCOME", "unknown"),
        "routing_step_outcome": os.environ.get("ROUTING_OUTCOME", "unknown"),
        "host": platform.node(),
        "kernel": platform.release(),
        "os_release": os_release,
        "cgroup_mode": cgroup_mode,
        "release_gate_decision": "OPEN_PENDING_INDEPENDENT_REVIEW_AND_EXTERNAL_DIGEST_RETENTION",
    }
    try:
        write_exclusive(root / PROVENANCE, "".join(f"{key}={value}\n" for key, value in provenance.items()))
    except (OSError, FileExistsError) as exc:
        append_summary(f"Runtime evidence provenance creation failed: {exc}")
        return 1

    _, unsafe = walk_entries(root)
    if unsafe:
        detail = "\n".join(f"error=unsafe evidence entry (symlink or non-regular file): {name}" for name in unsafe)
        write_exclusive(root / VERIFICATION, f"verification_status=FAIL\n{detail}\n")
        append_summary("Runtime evidence manifest verification failed: unsafe evidence entries found.")
        return 1

    files, _ = walk_entries(root)
    entries = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in METADATA or relative in {".manifest-inputs.tmp", ".manifest-sorted.tmp"}:
            continue
        entries.append(f"{sha256_file(path)}  {relative}")
    try:
        write_exclusive(root / MANIFEST, "".join(line + "\n" for line in sorted(entries, key=lambda item: item.split("  ", 1)[1])))
    except (OSError, FileExistsError) as exc:
        append_summary(f"Runtime evidence manifest creation failed: {exc}")
        return 1

    errors = verify_bundle(root, require_digest=False)
    verification = [f"manifest_entries={len(entries)}", f"verification_status={'PASS' if not errors else 'FAIL'}"]
    verification.extend(f"error={error}" for error in errors)
    try:
        write_exclusive(root / VERIFICATION, "".join(line + "\n" for line in verification))
        digest = hashlib.sha256((root / MANIFEST).read_bytes()).hexdigest()
        write_exclusive(root / DIGEST, f"sha256={digest}  {MANIFEST}\n")
    except (OSError, FileExistsError) as exc:
        append_summary(f"Runtime evidence verification metadata creation failed: {exc}")
        return 1
    if errors:
        append_summary("Runtime evidence manifest verification failed; bundle remains OPEN.")
        return 1
    append_summary("Runtime evidence manifest digest (retain separately for independent review):")
    append_summary(f"sha256={digest}  {MANIFEST}")
    return 0


def main(argv):
    if len(argv) != 3 or argv[1] not in ("finalize", "verify"):
        print("usage: runtime-evidence-bundle.py {finalize|verify} EVIDENCE_DIR", file=sys.stderr)
        return 2
    root = pathlib.Path(argv[2])
    if argv[1] == "finalize":
        return finalize(root)
    errors = verify_bundle(root, require_digest=True)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("runtime evidence bundle: PASS (exact file set, hashes and manifest digest verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
