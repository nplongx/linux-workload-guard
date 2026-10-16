#!/usr/bin/env python3
"""Fixtures for the runtime Environment policy validator; no GitHub calls."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_environment_policy", ROOT / "benchmarks/verify-runtime-environment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

good_env = {
    "name": "approved-controller-runtime",
    "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "Team", "id": 7}], "prevent_self_review": True}],
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}
good_branches = {"branch_policies": [{"name": "main", "type": "branch"}]}
assert module.validate_environment(good_env, good_branches) == []

cases = [
    ({}, good_branches, "missing environment"),
    ({**good_env, "protection_rules": []}, good_branches, "missing reviewers"),
    ({**good_env, "protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 7}], "prevent_self_review": False}]}, good_branches, "self-review allowed"),
    ({**good_env, "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}}, good_branches, "broad branch policy"),
    (good_env, {"branch_policies": [{"name": "main"}, {"name": "release/*"}]}, "extra branch policy"),
    (good_env, {"branch_policies": []}, "no branch policy"),
]
for environment, branches, label in cases:
    assert module.validate_environment(environment, branches), f"unsafe policy accepted: {label}"

print("runtime Environment policy regressions: ok (missing/unprotected environment, reviewer/self-review and exact main branch policy fail closed)")
