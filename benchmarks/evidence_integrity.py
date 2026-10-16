#!/usr/bin/env python3
"""Create and verify a create-only SHA-256 manifest for one benchmark run.

The manifest detects edits or missing/extra artifacts after collection. Its own
digest must be retained separately by the reviewer/approved evidence system if
tamper resistance against coordinated manifest+artifact rewrites is required.
"""
import hashlib
import os
import pathlib
import re


ARTIFACT_SUFFIXES = (
    ".tsv", ".samples.tsv", ".environment.txt", ".router.log",
    ".telemetry.tsv", ".throughput.tsv",
)
MANIFEST_SUFFIX = ".evidence.sha256"


def manifest_path(result):
    return pathlib.Path(result).with_suffix(MANIFEST_SUFFIX)


def artifact_paths(result):
    result = pathlib.Path(result)
    return tuple(result.with_suffix(suffix) for suffix in ARTIFACT_SUFFIXES)


def digest_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(result):
    """Write a manifest once all run files have been finalized; never replace."""
    target = manifest_path(result)
    paths = artifact_paths(result)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required benchmark artifact missing or unsafe: {path}")
    lines = [f"{digest_file(path)}  {path.name}" for path in paths]
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return target


def verify_manifest(result):
    """Return a list of integrity errors; an empty list means hashes matched."""
    result = pathlib.Path(result)
    target = manifest_path(result)
    if target.is_symlink() or not target.is_file():
        return [f"run evidence checksum manifest missing or unsafe: {target}"]
    expected = {path.name: path for path in artifact_paths(result)}
    recorded = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        if len(lines) != len(expected):
            return [f"checksum manifest must contain exactly {len(expected)} artifact entries"]
        for number, line in enumerate(lines, 1):
            digest, separator, name = line.partition("  ")
            if (not separator or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not name or "/" in name or "\\" in name):
                return [f"malformed checksum entry at line {number}"]
            if name not in expected:
                return [f"unexpected artifact in checksum manifest: {name}"]
            if name in recorded:
                return [f"duplicate artifact in checksum manifest: {name}"]
            recorded[name] = digest
        missing = sorted(set(expected) - set(recorded))
        if missing:
            return ["checksum manifest missing artifacts: " + ", ".join(missing)]
        errors = []
        for name, path in expected.items():
            if path.is_symlink() or not path.is_file():
                errors.append(f"artifact missing or unsafe: {path}")
                continue
            if digest_file(path) != recorded[name]:
                errors.append(f"SHA-256 mismatch: {path}")
        return errors
    except (OSError, UnicodeError) as exc:
        return [f"could not verify checksum manifest: {exc}"]
